"""Real P11 rule seam: daily bars -> event scan -> formal expert -> entry eligibility.

This is the narrow adaptation the D1B batch actually owns.  It is NOT a signal factory and
NOT an account service:

* Event selection is the real `scan_forward_candidates` over real CandidateDailyInput
  series (changing a bar changes the emitted EventRecord; nothing is injected as a string).
* Expert qualification is the real `FormalStrategyRunner.evaluate_entry` on a
  `FormalExpertContext` carrying the event's own rsi14/realized_volatility20 feature facts;
  a veto or missing feature yields NO_ACTION, never an entry.
* Account quantity/cash/fee sizing stays with P12's decide_entry.  This seam only produces
  candidates/actions/expert ceilings as evidence; it never pre-computes a quantity and then
  back-fills approval.
* Whole-day block and single-security exclusion are enforced here at the seam boundary
  before the G1 producer is ever called.

display_only / blocked actions are preserved; nothing here grants daily_adopted or a real
account limit.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from types import SimpleNamespace
from typing import Any

from quanthus.backtest.baseline_research import (
    BaselineFeaturePanelReport,
    BaselineFeatureRow,
)
from quanthus.event_library.scan import (
    CandidateDailyInput,
    ScannedCandidate,
    scan_forward_candidates,
)
from quanthus.expert_evaluation.contracts import ExpertEvaluationSpec
from quanthus.expert_evaluation.experts.trend_stability import (
    TrendStabilityConfig,
    evaluate_trend_stability,
)
from quanthus.expert_protocols import (
    RequiredAction,
)
from quanthus.expert_strategy.position_risk import HeldPositionFacts
from quanthus.expert_strategy.runner import (
    FormalStrategyDecision,
    FormalStrategyRunner,
    build_formal_expert_context,
)
from quanthus.feature_registry import FeatureRegistry
from quanthus.product_daily.contracts import (
    ProductDailyError,
    require_aware,
    require_decimal,
    require_text,
)
from quanthus.product_daily.recommendation.contracts import (
    NEW_ENTRY_SIGNAL,
    USE_ACTION,
    USE_EXPLANATION,
    DailyFactsView,
)
from quanthus.product_daily.recommendation.producer import (
    AdvicePolicy,
    CandidateSignal,
    ExpertBundle,
    HeldSignal,
)
from quanthus.product_daily.risk import ExpertEntryEligibility


class SeamDisposition(str, Enum):
    """The seam-level outcome for one security or for the whole day."""

    ENTRY = "entry"  # expert TRIAL_ENTRY with a positive target ceiling
    NO_ACTION = "no_action"  # expert ran and vetoed / abstained
    EXCLUDED = "excluded"  # isolable single-security missing fact -> dropped
    DAY_BLOCKED = "day_blocked"  # shared prerequisite failed / impact unlocatable


@dataclass(frozen=True)
class SecurityFeatureFacts:
    """Per-security P4-card feature values bound to one signal date and cutoff.

    rsi14 and realized_volatility20 are the two named features the p4-trend-risk card's
    position-risk review consumes.  `None` means the value is not available for this
    signal date; it is reported as unavailable, never silently defaulted, and the expert
    path blocks that edge.
    """

    security_code: str
    signal_date: date
    rsi14: Decimal | None
    realized_volatility20: Decimal | None
    listing_sessions: int
    total_return_index: Decimal | None = None
    sma20: Decimal | None = None
    sma60: Decimal | None = None
    momentum20: Decimal | None = None
    # Real P10-supplied identity / price / dependency projections.  None means the member
    # did not supply a qualified value and the candidate reports it, never a placeholder.
    security_name: str | None = None
    industry_id: str | None = None
    mark_price: Decimal | None = None
    price_bound: Decimal | None = None
    invalidations: tuple[str, ...] = ()
    depends_on_member_ids: tuple[str, ...] = ()
    # Guard rails mirroring the feature panel: bar validity, exception/continuity,
    # entry eligibility, and the readiness gate.  None means not evaluated for this row;
    # they are never silently treated as clean.
    volume_ratio20: Decimal | None = None
    average_amount20: Decimal | None = None
    daily_total_return: Decimal | None = None
    prior_high20: Decimal | None = None
    feature_ready: bool | None = None
    entry_eligible: bool | None = None
    exception_active: bool | None = None
    unexplained_price_transition: bool | None = None
    scope_continuity_excluded: bool | None = None

    def __post_init__(self) -> None:
        require_text(self.security_code, "security_code")
        for name, value in (
            ("rsi14", self.rsi14),
            ("realized_volatility20", self.realized_volatility20),
            ("total_return_index", self.total_return_index),
            ("sma20", self.sma20),
            ("sma60", self.sma60),
            ("momentum20", self.momentum20),
        ):
            if value is not None:
                require_decimal(value, name)
        if self.listing_sessions < 0:
            raise ProductDailyError("invalid_feature_facts", "listing_sessions must be >= 0")
        for name, value in (
            ("mark_price", self.mark_price),
            ("price_bound", self.price_bound),
            ("volume_ratio20", self.volume_ratio20),
            ("average_amount20", self.average_amount20),
            ("daily_total_return", self.daily_total_return),
            ("prior_high20", self.prior_high20),
        ):
            if value is not None:
                require_decimal(value, name)
        for item in self.invalidations:
            require_text(item, "invalidations[]")
        for item in self.depends_on_member_ids:
            require_text(item, "depends_on_member_ids[]")


@dataclass(frozen=True)
class SeamDecision:
    """One per-security seam outcome with the real expert decision bound as evidence."""

    security_code: str
    disposition: SeamDisposition
    event_id: str | None
    entry_allowed: bool
    target_maximum_position: Decimal | None
    expert_decision_digest: str | None
    reason: str
    # The real runner output; the seam never recomputes or labels it.  A blocked/no-action
    # decision is still the real decision (None only for excluded securities).
    decision: FormalStrategyDecision | None = None
    # The real P10-projected identity/price/feature view this decision consumed; the seam
    # passes it through so the candidate carries real fields, never a placeholder.
    features: SecurityFeatureFacts | None = None
    # The three-layer clock this event was evaluated under: the T observation end, the
    # consumer knowledge cutoff and the pre-declared decision deadline.  The candidate and
    # the downstream decision set must carry these, never drop them.
    signal_close: datetime | None = None
    knowledge_cutoff: datetime | None = None
    decision_at: datetime | None = None

    @property
    def executable(self) -> bool:
        return self.disposition is SeamDisposition.ENTRY and self.entry_allowed


@dataclass(frozen=True)
class HeldDecision:
    """One held-position outcome from the real evaluate_held path."""

    security_code: str
    action: str  # "held" | "reduce" | "exit" | "hold_alert"
    target_maximum_position: Decimal | None
    decision_digest: str | None
    reason: str
    decision: FormalStrategyDecision | None = None
    features: SecurityFeatureFacts | None = None
    signal_close: datetime | None = None
    knowledge_cutoff: datetime | None = None
    decision_at: datetime | None = None


@dataclass(frozen=True)
class SeamResult:
    """Either a set of seam decisions or a whole-day block."""

    day_blocked: bool
    day_block_reason: str | None
    decisions: tuple[SeamDecision, ...]
    excluded: tuple[dict[str, str], ...]
    held_decisions: tuple[HeldDecision, ...] = ()

    @property
    def entries(self) -> tuple[SeamDecision, ...]:
        return tuple(item for item in self.decisions if item.executable)


@dataclass(frozen=True)
class SeamResources:
    """The real registry/config/panel objects the seam binds to; never synthesized.

    feature_registry + panel_report come from the feature-panel evaluator so the
    rsi14/vol20 lineage matches what production/replay already consumes.  trend_config +
    trend_spec drive evaluate_trend_stability so each event carries a real observation,
    not an injected label.
    """

    feature_registry: FeatureRegistry
    panel_report: BaselineFeaturePanelReport
    trend_config: TrendStabilityConfig
    trend_spec: ExpertEvaluationSpec


class RuleSeamAdapter:
    """Run the real scan + expert path for one already-qualified daily batch.

    The adapter requires a real FormalStrategyRunner and real SeamResources; it never
    fabricates an event, a trend observation, an expert decision, or a feature value.
    """

    def __init__(self, runner: FormalStrategyRunner, resources: SeamResources) -> None:
        self.runner = runner
        self.resources = resources

    def _baseline_feature(
        self,
        *,
        candidate: ScannedCandidate,
        features: SecurityFeatureFacts,
    ) -> BaselineFeatureRow:
        """Project the injected SecurityFeatureFacts onto the panel row shape.

        build_formal_expert_context binds rsi14/realized_volatility20 from this row to the
        registry's active definitions; the remaining panel fields are not read by the
        expert context and stay None (the held path supplies its own facts separately).
        """

        if features.mark_price is None:
            raise ProductDailyError(
                "invalid_feature_facts",
                "mark_price (the real close for this signal date) is required to build the "
                "panel row; a missing close is reported, never replaced by a placeholder",
            )
        return BaselineFeatureRow(
            market_date=features.signal_date,
            security_id=candidate.record.identity.security_id,
            close_price=features.mark_price,
            total_return_index=features.total_return_index,
            sma20=features.sma20,
            sma60=features.sma60,
            momentum20=features.momentum20,
            prior_high20=features.prior_high20,
            rsi14=features.rsi14,
            rsi14_previous=None,
            volume_ratio20=features.volume_ratio20,
            realized_volatility20=features.realized_volatility20,
            daily_total_return=features.daily_total_return,
            average_amount20=features.average_amount20,
            listing_sessions=features.listing_sessions,
            feature_ready=(
                features.feature_ready
                if features.feature_ready is not None
                else all(
                    value is not None
                    for value in (
                        features.rsi14,
                        features.realized_volatility20,
                        features.total_return_index,
                        features.sma20,
                        features.sma60,
                        features.momentum20,
                    )
                )
            ),
            entry_eligible=(
                features.entry_eligible
                if features.entry_eligible is not None
                else (features.rsi14 is not None and features.realized_volatility20 is not None)
            ),
        )

    @staticmethod
    def _feature_event(event: Any) -> Any:
        """The event as the feature/observation path sees it.

        A prospective EventRecord keeps the source-publication instant in
        knowledge_cutoff (18:01+08 after the close), which is > signal_close and would
        trip ExpertObservation's knowledge_cutoff <= observation_anchor.  The feature
        data itself is visible at the T close, so the observation/feature cutoff is the
        earlier of the two: min(knowledge_cutoff, signal_close).  identity/event_id and
        decision_at are unchanged; the returned object is a view, not a new EventRecord.
        """

        feature_cutoff = min(event.knowledge_cutoff, event.signal_close)
        if feature_cutoff == event.knowledge_cutoff:
            return event
        view = SimpleNamespace(
            event_id=event.event_id,
            identity=event.identity,
            signal_close=event.signal_close,
            knowledge_cutoff=feature_cutoff,
            decision_at=event.decision_at,
            split_assignment=event.split_assignment,
        )
        if hasattr(event, "to_dict"):
            view.to_dict = event.to_dict
        return view

    def _evaluate_trend(
        self,
        *,
        event: Any,
        feature: BaselineFeatureRow | None,
    ) -> tuple[str | None, str]:
        """Run the real trend producer; return (trend_state, observation_digest)."""

        observation = evaluate_trend_stability(
            event=self._feature_event(event),
            feature=feature,
            config=self.resources.trend_config,
            spec=self.resources.trend_spec,
        )
        return (
            None if observation.raw_value is None else str(observation.raw_value),
            observation.digest,
        )

    def evaluate_security(
        self,
        *,
        candidate: ScannedCandidate,
        features: SecurityFeatureFacts,
        knowledge_cutoff: datetime,
        position_state: str = "empty",
    ) -> SeamDecision:
        """Run the real trend + expert for one scanned candidate."""

        require_aware(knowledge_cutoff, "knowledge_cutoff")
        event = candidate.record
        feature = self._baseline_feature(candidate=candidate, features=features)
        trend_state, trend_digest = self._evaluate_trend(event=event, feature=feature)
        ctx = build_formal_expert_context(
            event=self._feature_event(event),
            feature=feature,
            feature_registry=self.resources.feature_registry,
            panel_report=self.resources.panel_report,
            position_state=position_state,
            trend_observation_digest=trend_digest,
        )
        decision = self.runner.evaluate_entry(
            event_id=event.event_id,
            context=ctx,
            trend_state=trend_state,
            entry_evidence_ready=(
                features.rsi14 is not None and features.realized_volatility20 is not None
            ),
        )
        eligibility = ExpertEntryEligibility.from_formal_strategy_decision(decision)
        return SeamDecision(
            security_code=candidate.listing_code,
            disposition=(
                SeamDisposition.ENTRY if eligibility.entry_allowed else SeamDisposition.NO_ACTION
            ),
            event_id=eligibility.event_id,
            entry_allowed=eligibility.entry_allowed,
            target_maximum_position=eligibility.target_maximum_position,
            expert_decision_digest=eligibility.source_digest,
            reason=decision.action.final_action.value,
            decision=decision,
            features=features,
            signal_close=event.signal_close,
            knowledge_cutoff=knowledge_cutoff,
            decision_at=event.decision_at,
        )

    def evaluate_held(
        self,
        *,
        candidate: ScannedCandidate,
        features: SecurityFeatureFacts,
        position_facts: HeldPositionFacts | None,
        knowledge_cutoff: datetime,
        base_required_action: Any = None,
        base_reason_code: str = "base_position_management_clear",
        position_state: str = "trial",
    ) -> HeldDecision:
        """Run the real evaluate_held for one held security.

        Missing position_facts means the position cannot be qualified: it is reported as
        hold_alert (a hold + alert), never a forced sale.
        """

        require_aware(knowledge_cutoff, "knowledge_cutoff")
        event = candidate.record
        feature = self._baseline_feature(candidate=candidate, features=features)
        _, trend_digest = self._evaluate_trend(event=event, feature=feature)
        if position_facts is None:
            # hold + alert: report, never force a sale on missing data.
            return HeldDecision(
                security_code=candidate.listing_code,
                action="hold_alert",
                target_maximum_position=None,
                decision_digest=None,
                reason="position_facts_unavailable_hold_alert",
                decision=None,
            )
        ctx = build_formal_expert_context(
            event=self._feature_event(event),
            feature=feature,
            feature_registry=self.resources.feature_registry,
            panel_report=self.resources.panel_report,
            position_state=position_state,
            trend_observation_digest=trend_digest,
            position_facts=position_facts,
        )
        decision = self.runner.evaluate_held(
            event_id=event.event_id,
            context=ctx,
            position_facts=position_facts,
            base_required_action=(
                base_required_action if base_required_action is not None else RequiredAction.NONE
            ),
            base_reason_code=base_reason_code,
        )
        action_value = decision.action.final_action.value
        return HeldDecision(
            security_code=candidate.listing_code,
            action=(
                "exit"
                if action_value in {"full_exit", "force_liquidate"}
                else ("reduce" if "reduce" in action_value else "held")
            ),
            target_maximum_position=decision.target_maximum_position,
            decision_digest=decision.digest,
            reason=action_value,
            decision=decision,
            features=features,
            signal_close=event.signal_close,
            knowledge_cutoff=knowledge_cutoff,
            decision_at=event.decision_at,
        )


def _digest(label: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def evaluate_day(
    *,
    runner: FormalStrategyRunner,
    resources: SeamResources,
    rows: Sequence[CandidateDailyInput],
    trading_dates: Sequence[date],
    snapshot_id: str,
    snapshot_manifest_digest: str,
    card: Any,
    split_policy: Any,
    source_policy: Any,
    features_by_security: Mapping[str, SecurityFeatureFacts],
    knowledge_cutoff: datetime,
    held_facts_by_security: Mapping[str, HeldPositionFacts] | None = None,
    shared_prereq_ok: bool = True,
    shared_prereq_reason: str | None = None,
    position_state: str = "empty",
) -> SeamResult:
    """Run the real scan + per-security expert path with whole-day / single-security gates.

    * shared_prereq_ok=False models a shared hard prerequisite failure -> whole-day block.
    * A security missing usable feature facts is excluded alone; others still produce entries.
    * A vetoed expert outcome (NO_ACTION) stays a no-entry, not an exclusion or a publishable
      entry.
    * held_facts_by_security supplies real position facts per held security; a held security
      with no facts yields a hold+alert, never a forced exit.
    * knowledge_cutoff is the caller-declared decision/knowledge deadline (for the prospective
      daily profile it is the pre-registered 22:30+08 cutoff that the source-policy
      prospective_clock_json already returns; it is never max(member.available_at)).
    """

    require_aware(knowledge_cutoff, "knowledge_cutoff")
    if not shared_prereq_ok:
        return SeamResult(
            day_blocked=True,
            day_block_reason=shared_prereq_reason or "shared_prerequisite_failed",
            decisions=(),
            excluded=(),
            held_decisions=(),
        )

    adapter = RuleSeamAdapter(runner, resources)
    scan = scan_forward_candidates(
        rows,
        trading_dates=trading_dates,
        snapshot_id=snapshot_id,
        snapshot_manifest_digest=snapshot_manifest_digest,
        card=card,
        split_policy=split_policy,
        source_policy=source_policy,
    )
    decisions: list[SeamDecision] = []
    excluded: list[dict[str, str]] = []
    for candidate in scan.candidates:
        code = candidate.listing_code
        facts = features_by_security.get(code)
        if facts is None or facts.rsi14 is None or facts.realized_volatility20 is None:
            excluded.append(
                {
                    "security_code": code,
                    "reason": (
                        "feature_unavailable" if facts is not None else "missing_feature_facts"
                    ),
                    "member_or_field": "rsi14|realized_volatility20",
                    "at": knowledge_cutoff.isoformat(),
                }
            )
            continue
        decisions.append(
            adapter.evaluate_security(
                candidate=candidate,
                features=facts,
                knowledge_cutoff=knowledge_cutoff,
                position_state=position_state,
            )
        )
    held_decisions: list[HeldDecision] = []
    if held_facts_by_security is not None:
        held_candidates = {item.listing_code: item for item in scan.candidates}
        for code in sorted(held_facts_by_security):
            position_facts = held_facts_by_security[code]
            held_candidate = held_candidates.get(code)
            facts = features_by_security.get(code)
            if held_candidate is None or facts is None:
                held_decisions.append(
                    HeldDecision(
                        security_code=code,
                        action="hold_alert",
                        target_maximum_position=None,
                        decision_digest=None,
                        reason="no_scanned_event_or_feature_facts_hold_alert",
                        decision=None,
                        features=facts,
                        knowledge_cutoff=knowledge_cutoff,
                    )
                )
                continue
            held_decisions.append(
                adapter.evaluate_held(
                    candidate=held_candidate,
                    features=facts,
                    position_facts=position_facts,
                    knowledge_cutoff=knowledge_cutoff,
                    position_state="trial",
                )
            )
    return SeamResult(
        day_blocked=False,
        day_block_reason=None,
        decisions=tuple(decisions),
        excluded=tuple(excluded),
        held_decisions=tuple(held_decisions),
    )


def seam_to_candidates(
    result: SeamResult,
    *,
    experts: ExpertBundle,
    facts: DailyFactsView,
    policy: AdvicePolicy,
    eligible_at: datetime,
    expires_at: datetime,
) -> tuple[tuple[CandidateSignal, ...], tuple[HeldSignal, ...]]:
    """Map executable seam entries + held outcomes to real G1-shaped signals.

    ENTRY decisions the real expert allowed become candidates carrying the real P10
    identity/price/feature projection (security_name, industry_id, mark_price, price_bound,
    invalidations, member dependencies) and the three-layer clock the event ran under.
    Held decisions become HeldSignal rows so a position with no new event is still
    evaluated and reaches the decision set; a hold_alert stays a hold, never a forced
    sale.  Blocked/no-action outcomes produce no signal.  Component ref/version are locked
    to the ExpertBundle identity and the digests are the real FormalStrategyDecision
    digests, so a forged component cannot match.
    """

    require_aware(eligible_at, "eligible_at")
    require_aware(expires_at, "expires_at")
    signals: list[CandidateSignal] = []
    held_signals: list[HeldSignal] = []
    for item in result.entries:
        if item.decision is None or item.event_id is None:
            continue
        # The ExpertBundle digest must equal the topology digest the real runner stamped on
        # this decision; a registered component name with an arbitrary bundle digest cannot
        # pass without having actually run this runner on this event.
        if (
            getattr(item.decision, "topology_digest", None) is not None
            and item.decision.topology_digest != experts.digest
        ):
            continue
        features = item.features
        if features is None:
            # No real projection was bound to this decision; report it, never a placeholder.
            continue
        if (
            features.security_name is None
            or features.industry_id is None
            or features.mark_price is None
            or features.price_bound is None
        ):
            # The real identity/price member did not supply a qualified value; this edge
            # is reported unavailable upstream, never filled with industry-00 / price 1.
            continue
        candidate = CandidateSignal(
            opportunity_id=item.event_id,
            security_code=item.security_code,
            security_name=features.security_name,
            industry_id=features.industry_id,
            fraction=(
                item.target_maximum_position
                if item.target_maximum_position is not None
                else Decimal("0")
            ),
            price_bound=features.price_bound,
            mark_price=features.mark_price,
            component_ref_id=experts.ref_id,
            component_version=experts.version,
            reason_codes=(NEW_ENTRY_SIGNAL,),
            explanations=(
                {
                    "code": "REPLAY_ENTRY",
                    "use": USE_ACTION,
                    "component_ref": f"{experts.ref_id}@{experts.version}",
                    "text": "expert-qualified entry candidate",
                },
                {
                    "code": "REPLAY_SIZING",
                    "use": USE_EXPLANATION,
                    "component_ref": f"{experts.ref_id}@{experts.version}",
                    "text": "sizing bounded by account cash, bucket budget and concentration caps",
                },
            ),
            invalidations=features.invalidations,
            next_observation=f"next session after {item.security_code} close",
            depends_on_member_ids=features.depends_on_member_ids,
            # The producer verifies these against the canonical batch/policy/opportunity
            # binding; the real runner digest travels in the explanations audit text.
            expert_decision_digest=None,
            risk_decision_digest=None,
        )
        signals.append(
            dataclasses.replace(
                candidate,
                expert_decision_digest=candidate.expected_expert_digest(facts, policy),
                risk_decision_digest=candidate.expected_risk_digest(facts, policy),
            )
        )
    for held in result.held_decisions:
        if held.features is None:
            continue
        if (
            held.decision is not None
            and getattr(held.decision, "topology_digest", None) is not None
            and held.decision.topology_digest != experts.digest
        ):
            continue
        features = held.features
        held_signal = HeldSignal(
            opportunity_id=f"held-{held.security_code}",
            security_code=held.security_code,
            security_name=features.security_name or held.security_code,
            industry_id=features.industry_id or "industry-unbound",
            action=held.action,
            target_maximum_position=held.target_maximum_position,
            component_ref_id=experts.ref_id,
            component_version=experts.version,
            reason_codes=(),
            explanations=(
                {
                    "code": "INPUT_CLOCK",
                    "use": USE_EXPLANATION,
                    "component_ref": f"{experts.ref_id}@{experts.version}",
                    "text": (
                        "position facts unavailable; hold and alert"
                        if held.action == "hold_alert"
                        else f"expert held outcome {held.reason}"
                    ),
                },
            ),
            next_observation=f"next session after {held.security_code} close",
            depends_on_member_ids=features.depends_on_member_ids,
            expert_decision_digest=None,
            risk_decision_digest=None,
            signal_close=held.signal_close,
            knowledge_cutoff=held.knowledge_cutoff,
            decision_at=held.decision_at,
        )
        held_signals.append(
            dataclasses.replace(
                held_signal,
                expert_decision_digest=held_signal.expected_expert_digest(facts, policy),
                risk_decision_digest=held_signal.expected_risk_digest(facts, policy),
            )
        )
    return tuple(signals), tuple(held_signals)


# --- three-piece set: per-action receipt + machine policy + release manifest -------------


@dataclass(frozen=True)
class ActionReceipt:
    """Per-action use receipt candidate for one security decision.

    adoption is never granted by construction: `executable` only means the expert produced a
    TRIAL_ENTRY with a positive target ceiling; daily_simulated use still needs the named
    adoption gate (A01/A02 + A03-A07) and remains proposed/pending until the main session
    adopts it.
    """

    security_code: str
    event_id: str | None
    action: str  # "new_entry" | "held" | "reduce" | "exit" | "no_action"
    executable: bool
    adoption_state: str = "proposed"  # proposed | pending | adopted | display_only | blocked
    target_maximum_position: Decimal | None = None
    expert_decision_digest: str | None = None
    dependencies: tuple[str, ...] = ()
    disable_conditions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "security_code": self.security_code,
            "event_id": self.event_id,
            "action": self.action,
            "executable": self.executable,
            "adoption_state": self.adoption_state,
            "target_maximum_position": (
                None if self.target_maximum_position is None else str(self.target_maximum_position)
            ),
            "expert_decision_digest": self.expert_decision_digest,
            "dependencies": list(self.dependencies),
            "disable_conditions": list(self.disable_conditions),
        }


@dataclass(frozen=True)
class MachineInvalidationRule:
    """One machine invalidation / position policy field with its full semantics."""

    field: str
    value: str
    type: str
    unit: str
    observed_at: str
    timezone: str
    applies_to_actions: tuple[str, ...]
    reason: str
    denominator_or_priority: str | None = None
    veto: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "value": self.value,
            "type": self.type,
            "unit": self.unit,
            "observed_at": self.observed_at,
            "timezone": self.timezone,
            "applies_to_actions": list(self.applies_to_actions),
            "reason": self.reason,
            "denominator_or_priority": self.denominator_or_priority,
            "veto": self.veto,
        }


@dataclass(frozen=True)
class ReleaseManifest:
    """The rule release manifest: card SHA, code entry, input closure and history window."""

    manifest_id: str
    strategy_card_path: str
    strategy_card_sha256: str
    code_entry: str
    code_commit: str | None
    input_tables: tuple[str, ...]
    price_basis: str
    industry_source: str
    history_window_sessions: int
    knowledge_cutoff: str
    adoption_state: str = "proposed"  # never adopted by construction

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_id": self.manifest_id,
            "strategy_card_path": self.strategy_card_path,
            "strategy_card_sha256": self.strategy_card_sha256,
            "code_entry": self.code_entry,
            "code_commit": self.code_commit,
            "input_tables": list(self.input_tables),
            "price_basis": self.price_basis,
            "industry_source": self.industry_source,
            "history_window_sessions": self.history_window_sessions,
            "knowledge_cutoff": self.knowledge_cutoff,
            "adoption_state": self.adoption_state,
        }


# --- three-piece set strict loaders + cross-file check --------------------------------------


def _load_json(path: Any) -> dict[str, Any] | list[Any]:
    import json as _json
    from pathlib import Path as _Path

    raw = _json.loads(_Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) and not isinstance(raw, list):
        raise ProductDailyError("invalid_three_piece", f"{path} root must be object or array")
    return raw


def load_action_receipts(path: Any) -> tuple[dict[str, Any], ...]:
    """Strict-load the per-action receipts; fail on missing/invalid adoption fields."""

    raw = _load_json(path)
    if not isinstance(raw, list) or not raw:
        raise ProductDailyError("invalid_three_piece", "action-receipts must be a non-empty list")
    required = ("action", "adoption_state", "executable_via", "dependencies")
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ProductDailyError(
                "invalid_three_piece", f"action-receipts[{index}] must be object"
            )
        missing = [k for k in required if k not in entry]
        if missing:
            raise ProductDailyError(
                "invalid_three_piece", f"action-receipts[{index}] missing {missing}"
            )
        if entry["adoption_state"] not in {
            "proposed",
            "pending",
            "adopted",
            "display_only",
            "blocked",
        }:
            raise ProductDailyError(
                "invalid_three_piece",
                f"action-receipts[{index}] adoption_state invalid",
            )
    return tuple(raw)


def load_machine_policy(path: Any) -> dict[str, Any]:
    """Strict-load the machine invalidation/position policy."""

    raw = _load_json(path)
    if not isinstance(raw, dict):
        raise ProductDailyError("invalid_three_piece", "machine-policy must be an object")
    if raw.get("schema_version") != "quanthus.p11-d1b.machine-policy.v1":
        raise ProductDailyError("invalid_three_piece", "machine-policy schema_version differs")
    caps = raw.get("position_caps")
    if not isinstance(caps, list):
        raise ProductDailyError("invalid_three_piece", "machine-policy position_caps missing")
    for cap in caps:
        if not isinstance(cap, dict) or "denominator" not in cap:
            raise ProductDailyError("invalid_three_piece", "position_cap missing denominator")
    return raw


def load_release_manifest(path: Any) -> dict[str, Any]:
    """Strict-load the release manifest; fail on missing closure fields."""

    raw = _load_json(path)
    if not isinstance(raw, dict):
        raise ProductDailyError("invalid_three_piece", "release-manifest must be an object")
    if raw.get("schema_version") != "quanthus.p11-d1b.release-manifest.v1":
        raise ProductDailyError("invalid_three_piece", "release-manifest schema_version differs")
    closure = raw.get("input_closure")
    if not isinstance(closure, dict):
        raise ProductDailyError("invalid_three_piece", "release-manifest input_closure missing")
    for field in ("feature_registry", "features", "adjustment_factor", "history_window_sessions"):
        if field not in closure:
            raise ProductDailyError("invalid_three_piece", f"input_closure missing {field}")
    if closure.get("history_window_sessions", 0) < 60:
        raise ProductDailyError(
            "invalid_three_piece", "history_window_sessions must cover sma60/base-exit"
        )
    if raw.get("daily_adopted") is not False:
        raise ProductDailyError(
            "invalid_three_piece", "release-manifest must keep daily_adopted=false"
        )
    # The code entry must carry a real commit/tree identity for the exact closure under
    # review; an absent code_commit is not a bound release.
    code_entry = raw.get("code_entry")
    if not isinstance(code_entry, dict) or not code_entry.get("code_commit"):
        raise ProductDailyError(
            "invalid_three_piece", "release-manifest code_entry.code_commit required"
        )
    # industry_mapping must be either a real sha256 binding or an explicit pending /
    # blocked state; a pending-named sha256 string is a disguised placeholder and fails.
    industry = closure.get("industry_mapping")
    if isinstance(industry, dict):
        sha = industry.get("sha256")
        state = industry.get("binding_state")
        if isinstance(sha, str) and sha.startswith("sha256:pending"):
            raise ProductDailyError(
                "invalid_three_piece",
                "industry_mapping sha256 must be a real digest or an explicit pending "
                "binding_state, never a pending-named placeholder",
            )
        if sha is None and state not in {"pending", "blocked"}:
            raise ProductDailyError(
                "invalid_three_piece",
                "industry_mapping without a sha256 must declare binding_state pending/blocked",
            )
    return raw


def check_three_piece_consistency(
    *,
    action_receipts: tuple[dict[str, Any], ...],
    machine_policy: dict[str, Any],
    release_manifest: dict[str, Any],
) -> None:
    """Cross-file identity/version check; any inconsistency fails the release."""

    for entry in action_receipts:
        if (
            entry.get("adoption_state") == "adopted"
            and release_manifest.get("daily_adopted") is False
        ):
            raise ProductDailyError(
                "inconsistent_three_piece",
                f"action {entry.get('action')} adopted while release-manifest daily_adopted=false",
            )
    caps = {cap.get("field"): cap for cap in machine_policy.get("position_caps", ())}
    if (
        "single_security_fraction" in caps
        and caps["single_security_fraction"].get("denominator") == "account_equity"
    ):
        raise ProductDailyError(
            "inconsistent_three_piece",
            "single_security_fraction must not use account_equity as denominator",
        )

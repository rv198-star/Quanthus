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
import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from time import perf_counter
from typing import Any, cast

from quanthus.backtest.baseline_research import (
    BaselineFeaturePanelReport,
    BaselineFeatureRow,
)
from quanthus.event_library.contracts import (
    EventIdentity,
    EventRecord,
    prospective_clock,
    sha256_digest,
)
from quanthus.event_library.rule_card import CandidateRuleCard
from quanthus.event_library.scan import (
    CandidateDailyInput,
    ScannedCandidate,
    scan_forward_candidates,
)
from quanthus.expert_evaluation.contracts import ExpertEvaluationSpec
from quanthus.expert_evaluation.experts.trend_stability import (
    TrendStabilityConfig,
    classify_trend_stability,
    evaluate_trend_stability,
)
from quanthus.expert_protocols import (
    RequiredAction,
)
from quanthus.expert_strategy.context import ExpertDecisionSubject
from quanthus.expert_strategy.position_risk import HeldPositionFacts
from quanthus.expert_strategy.runner import (
    FormalStrategyDecision,
    FormalStrategyRunner,
    build_formal_expert_context,
)
from quanthus.feature_registry import FeatureRegistry
from quanthus.policy import find_project_root
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
    OctoberReplayAdmissionClock,
    OctoberReplayContext,
    _new_replay_admission_clock,
)
from quanthus.product_daily.recommendation.producer import (
    RULE_DAY_PRODUCER_VERSION,
    AccountFactsView,
    AdvicePolicy,
    CandidateEvaluationReceipt,
    CandidateSignal,
    DailyAdviceProducer,
    ExpertBundle,
    HeldSignal,
    ProducerResult,
)
from quanthus.product_daily.recommendation.runtime_identity import code_identity, consumer_identity
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
    # Guard rails mirroring the feature panel: volume/amount ratios, prior high,
    # readiness and the entry/exception/continuity gates.  None means not evaluated
    # for this row; they are never silently treated as clean.
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
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class SeamResult:
    """Either a set of seam decisions or a whole-day block."""

    day_blocked: bool
    day_block_reason: str | None
    decisions: tuple[SeamDecision, ...]
    excluded: tuple[dict[str, str], ...]
    held_decisions: tuple[HeldDecision, ...] = ()
    experts: ExpertBundle | None = None
    evaluated_universe: tuple[str, ...] = ()
    scan_result: Any = None

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

    def __post_init__(self) -> None:
        if (
            self.feature_registry.digest != self.panel_report.feature_registry_digest
            or self.feature_registry.digest != self.trend_config.feature_registry_digest
        ):
            raise ProductDailyError(
                "feature_registry_binding_mismatch", "registry identity differs"
            )


@dataclass(frozen=True)
class HeldReviewSubject:
    """A daily position review identity, independent of any new-entry signal."""

    identity: EventIdentity
    knowledge_cutoff: datetime
    signal_close: datetime
    decision_at: datetime
    source_evidence_digest: str

    @property
    def event_id(self) -> str:
        return "held-review:" + sha256_digest(self.to_dict()).removeprefix("sha256:")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "quanthus.p11-held-review.v1",
            "identity": self.identity.to_dict(),
            "knowledge_cutoff": self.knowledge_cutoff.isoformat(),
            "signal_close": self.signal_close.isoformat(),
            "decision_at": self.decision_at.isoformat(),
            "source_evidence_digest": self.source_evidence_digest,
        }


def _held_no_trend_evidence(
    *,
    subject_id: str,
    security_id: str,
    snapshot_id: str,
    knowledge_cutoff: datetime,
    topology_digest: str,
) -> dict[str, Any]:
    """Versioned context-only evidence; no trend assessment/prediction is requested."""
    return {
        "schema_version": "quanthus.p11.held-no-trend-context.v1",
        "subject_id": subject_id,
        "security_id": security_id,
        "snapshot_id": snapshot_id,
        "knowledge_cutoff": knowledge_cutoff.astimezone(timezone.utc).isoformat(),
        "topology_digest": topology_digest,
        "assessment_requested": False,
        "score_applicability": "ABSTAINED",
        "scope": "held_position_management",
    }


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
        candidate: ScannedCandidate | None,
        features: SecurityFeatureFacts,
        security_id: str | None = None,
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
            security_id=(
                candidate.record.identity.security_id if candidate is not None else str(security_id)
            ),
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

    def _evaluate_trend(
        self,
        *,
        event: EventRecord,
        feature: BaselineFeatureRow | None,
    ) -> tuple[str | None, str]:
        """Run the real trend producer; return (trend_state, observation_digest)."""

        if event.prospective_clock_json is not None:
            # The original opportunity assessment anchors at T close and cannot describe
            # an evening source. Reuse its pure classification, with a separately versioned
            # daily domain evidence envelope; never rewrite source time to satisfy it.
            if (
                self.resources.trend_spec.assessment_id != self.resources.trend_config.expert_id
                or self.resources.trend_spec.assessment_version
                != self.resources.trend_config.expert_version
            ):
                raise ProductDailyError("producer_binding_mismatch", "trend spec differs")
            state, reason = classify_trend_stability(feature, self.resources.trend_config)
            return state, sha256_digest(
                {
                    "schema_version": "quanthus.p11-prospective-trend-evidence.v1",
                    "event": event.to_dict(),
                    "feature": None
                    if feature is None
                    else {
                        key: str(value) if isinstance(value, (date, Decimal)) else value
                        for key, value in dataclasses.asdict(feature).items()
                    },
                    "config": self.resources.trend_config.semantic_digest,
                    "assessment_binding": self.resources.trend_spec.assessment_binding_digest,
                    "state": state,
                    "reason": reason,
                }
            )
        observation = evaluate_trend_stability(
            event=event,
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
            event=event,
            feature=feature,
            feature_registry=self.resources.feature_registry,
            panel_report=self.resources.panel_report,
            position_state=position_state,
            trend_observation_digest=trend_digest,
        )
        # The real panel row's feature_ready/entry_eligible are the M2 admission: a
        # feature_ready=True but entry_eligible=False row (e.g. a limit/tradable gate)
        # must not produce an entry.  When the row was not evaluated (None) fall back
        # to the rsi14/vol20 presence floor; missing is still not-ready, never a grant.
        evidence_ready = (
            (features.feature_ready and features.entry_eligible)
            if features.feature_ready is not None or features.entry_eligible is not None
            else (features.rsi14 is not None and features.realized_volatility20 is not None)
        )
        decision = self.runner.evaluate_entry(
            event_id=event.event_id,
            context=ctx,
            trend_state=trend_state,
            entry_evidence_ready=bool(evidence_ready),
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
            knowledge_cutoff=event.knowledge_cutoff,
            decision_at=event.decision_at,
        )

    def evaluate_held(
        self,
        *,
        candidate: ScannedCandidate | None = None,
        review: HeldReviewSubject | None = None,
        features: SecurityFeatureFacts,
        position_facts: HeldPositionFacts | None,
        knowledge_cutoff: datetime,
        base_required_action: RequiredAction | None = None,
        base_reason_code: str = "base_position_management_clear",
        position_state: str = "trial",
        prior_deterioration_sessions: int | None = None,
    ) -> HeldDecision:
        """Run the real evaluate_held for one held security.

        Missing position_facts means the position cannot be qualified: it is reported as
        hold_alert (a hold + alert), never a forced sale.
        """

        require_aware(knowledge_cutoff, "knowledge_cutoff")
        event: ExpertDecisionSubject
        if candidate is not None:
            event = candidate.record
            signal_close, decision_at = candidate.record.signal_close, candidate.record.decision_at
        elif review is not None:
            event = review
            signal_close, decision_at = review.signal_close, review.decision_at
        else:
            raise ProductDailyError("invalid_held_review", "held review subject is required")
        feature = (
            None
            if features.mark_price is None
            else self._baseline_feature(
                candidate=candidate,
                features=features,
                security_id=event.identity.security_id,
            )
        )
        if position_facts is None:
            # hold + alert: report, never force a sale on missing data.
            return HeldDecision(
                security_code=features.security_code,
                action="hold_alert",
                target_maximum_position=None,
                decision_digest=None,
                reason="position_facts_unavailable_hold_alert",
                decision=None,
                features=features,
                signal_close=signal_close,
                knowledge_cutoff=event.knowledge_cutoff,
                decision_at=decision_at,
            )
        base = self.runner.card.semantic_sections["position_and_execution"][
            "base_position_management"
        ]
        base_unknown = False
        if base_required_action is None:
            base_required_action = RequiredAction.NONE
            if position_facts.holding_sessions >= int(base["maximum_holding_sessions"]):
                base_required_action = RequiredAction.FORCE_LIQUIDATE
                base_reason_code = "base_maximum_holding_exit"
            elif (
                position_facts.marked_return is not None
                and position_facts.marked_return <= -Decimal(str(base["constraint_stop_loss"]))
            ):
                base_required_action = RequiredAction.FORCE_LIQUIDATE
                base_reason_code = "base_constraint_stop_exit"
            elif (
                features.total_return_index is None
                or features.sma20 is None
                or features.sma60 is None
            ):
                base_unknown = True
            elif features.total_return_index < features.sma60 and features.sma20 <= features.sma60:
                if prior_deterioration_sessions is None:
                    base_unknown = True
                elif position_facts.holding_sessions >= int(
                    base["minimum_holding_sessions"]
                ) and prior_deterioration_sessions + 1 >= int(base["deterioration_sessions"]):
                    base_required_action = RequiredAction.FORCE_LIQUIDATE
                    base_reason_code = "base_structural_deterioration_exit"
        # Preserve the sealed runner's EventRecord annotation. Its builder reads only
        # ExpertDecisionSubject; the actual held object remains a HeldReviewSubject.
        ctx = build_formal_expert_context(
            event=cast(EventRecord, event),
            feature=feature,
            feature_registry=self.resources.feature_registry,
            panel_report=self.resources.panel_report,
            position_state=position_state,
            trend_observation_digest=sha256_digest(
                _held_no_trend_evidence(
                    subject_id=event.event_id,
                    security_id=event.identity.security_id,
                    snapshot_id=event.identity.snapshot_id,
                    knowledge_cutoff=event.knowledge_cutoff,
                    topology_digest=self.runner.topology_digest,
                )
            ),
            position_facts=position_facts,
        )
        decision = self.runner.evaluate_held(
            event_id=event.event_id,
            context=ctx,
            position_facts=position_facts,
            base_required_action=base_required_action,
            base_reason_code=base_reason_code,
        )
        action_value = decision.action.final_action.value
        return HeldDecision(
            security_code=features.security_code,
            action=(
                "exit"
                if action_value in {"full_exit", "force_liquidate"}
                else (
                    "reduce"
                    if "reduce" in action_value
                    else (
                        "hold_alert"
                        if base_unknown
                        or features.rsi14 is None
                        or features.realized_volatility20 is None
                        else "held"
                    )
                )
            ),
            target_maximum_position=decision.target_maximum_position,
            decision_digest=decision.digest,
            reason=action_value,
            decision=decision,
            features=features,
            signal_close=signal_close,
            knowledge_cutoff=event.knowledge_cutoff,
            decision_at=decision_at,
        )


def _digest(label: str) -> str:

    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _projection_issues(
    features: SecurityFeatureFacts,
    row: CandidateDailyInput,
    *,
    entry: bool,
) -> tuple[str, ...]:
    """Bind prepared domain output to the exact source coordinate before expert use."""
    fields = []
    for field, expected in (
        ("security_code", row.listing_code),
        ("signal_date", row.session_date),
        ("mark_price", row.close_price),
        ("industry_id", row.static_level1_industry_id),
        ("listing_sessions", row.listing_sessions),
    ):
        if getattr(features, field) != expected:
            fields.append(field)
    if features.mark_price is None or features.mark_price <= 0:
        fields.append("mark_price")
    if entry:
        if features.mark_price is not None and features.mark_price < Decimal("0.01"):
            fields.append("mark_price")
        for name in ("security_name", "industry_id"):
            value = getattr(features, name)
            if not isinstance(value, str) or not value.strip():
                fields.append(name)
        if features.price_bound is None or features.price_bound < Decimal("0.01"):
            fields.append("price_bound")
    return tuple(dict.fromkeys(fields))


def _held_source_only(row: CandidateDailyInput) -> SecurityFeatureFacts:
    """Reject an unbound feature payload while retaining independent base facts.

    These values come from the already-read source row; no domain feature is inferred.
    Holding duration/marked return can still trigger the fixed base guardian.
    """
    return SecurityFeatureFacts(
        security_code=row.listing_code,
        signal_date=row.session_date,
        rsi14=None,
        realized_volatility20=None,
        listing_sessions=row.listing_sessions,
        mark_price=row.close_price if row.close_price is not None and row.close_price > 0 else None,
        industry_id=row.static_level1_industry_id,
    )


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
    held_facts_by_security: Mapping[str, HeldPositionFacts | None] | None = None,
    held_unavailable_reasons_by_security: Mapping[str, str] | None = None,
    prior_deterioration_sessions_by_security: Mapping[str, int] | None = None,
    pending_security_codes: Sequence[str] | None = None,
    shared_prereq_ok: bool = True,
    shared_prereq_reason: str | None = None,
    position_state: str = "empty",
) -> SeamResult:
    """Run the real scan + per-security expert path with whole-day / single-security gates.

    * shared_prereq_ok=False models a shared hard prerequisite failure -> whole-day block.
    * A security missing usable feature facts is excluded alone; others still produce entries.
    * A vetoed expert outcome (NO_ACTION) stays a no-entry, not an exclusion or a publishable
      entry.
    * held_facts_by_security supplies real position facts per held security; a held member
      whose value is None is a declared position with unavailable facts and yields a
      hold+alert review, never an omission and never a forced exit.
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
    scan_result = (
        scan_forward_candidates(
            rows,
            explain_date=knowledge_cutoff.date(),
            trading_dates=trading_dates,
            snapshot_id=snapshot_id,
            snapshot_manifest_digest=snapshot_manifest_digest,
            card=card,
            split_policy=split_policy,
            source_policy=source_policy,
        )
        if rows
        else None
    )
    candidates = scan_result.candidates if scan_result is not None else ()
    decisions: list[SeamDecision] = []
    excluded: list[dict[str, str]] = []
    source_rows: dict[tuple[str, date], list[CandidateDailyInput]] = {}
    for row in rows:
        if row.knowledge_cutoff is None:
            # A date-context warm-up row carries no instant; it qualifies by
            # business date -- strictly earlier than the declared decision
            # cutoff's session day under the ordered session sequence, never
            # a borrowed clock.
            if row.session_date < knowledge_cutoff.date():
                source_rows.setdefault((row.listing_code, row.session_date), []).append(row)
        elif row.knowledge_cutoff <= knowledge_cutoff:
            source_rows.setdefault((row.listing_code, row.session_date), []).append(row)
    for candidate in candidates:
        code = candidate.listing_code
        if held_facts_by_security is not None and code in held_facts_by_security:
            # An existing position has its own daily review, not a second entry plan.
            continue
        if pending_security_codes is not None and code in pending_security_codes:
            # A pending open buy order / reserved bucket / unconfirmed fill blocks a new
            # entry for the security but gets no held review of its own.
            excluded.append(
                {
                    "security_code": code,
                    "reason": "pending_security_no_new_entry",
                    "member_or_field": "pending_securities",
                    "at": knowledge_cutoff.isoformat(),
                    "scope": "new_entry",
                }
            )
            continue
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
        matching = source_rows.get((code, candidate.record.identity.signal_date), [])
        issues = (
            ("source_row",)
            if len(matching) != 1
            else _projection_issues(facts, matching[0], entry=True)
        )
        if issues:
            excluded.append(
                {
                    "security_code": code,
                    "reason": "feature_projection_invalid",
                    "member_or_field": "|".join(issues),
                    "at": knowledge_cutoff.isoformat(),
                    "scope": "new_entry",
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
        for code in sorted(held_facts_by_security):
            position_facts = held_facts_by_security[code]
            facts = features_by_security.get(code)
            dates = [day for row_code, day in source_rows if row_code == code]
            review_rows = source_rows.get((code, max(dates)), []) if dates else []
            if len(review_rows) != 1:
                held_decisions.append(
                    HeldDecision(
                        security_code=code,
                        action="hold_alert",
                        target_maximum_position=None,
                        decision_digest=None,
                        reason="held_source_identity_or_feature_facts_unavailable_hold_alert",
                        decision=None,
                        features=facts,
                        knowledge_cutoff=knowledge_cutoff,
                        unavailable_reason=(held_unavailable_reasons_by_security or {}).get(
                            code, "held_market_input_unavailable"
                        ),
                    )
                )
                continue
            row = review_rows[0]
            issues = (
                ("feature_facts",) if facts is None else _projection_issues(facts, row, entry=False)
            )
            if issues:
                excluded.append(
                    {
                        "security_code": code,
                        "reason": "held_feature_projection_invalid",
                        "member_or_field": "|".join(issues),
                        "at": knowledge_cutoff.isoformat(),
                        "scope": "held_features",
                        "fallback": "source_only_base_review",
                    }
                )
                facts = _held_source_only(row)
            assert facts is not None
            # Held-position feature-eligibility gate (issue #751): a feature row
            # that is not ready, or has an active exception, is NOT qualified
            # domain evidence.  It must not drive a reduce/exit through the
            # feature-dependent deterioration path.  Degrade to the source-only
            # base review so that only the independent raw position facts
            # (holding_sessions -> max-holding, marked_return -> stop-loss) can
            # still legally trigger the fixed base guardian; the unqualified
            # feature can no longer drive a reduce/exit and the resulting
            # decision collapses to hold+alert when nothing else qualifies.
            # feature_ready is None means the row was not evaluated for the
            # panel; the frozen contract treats "not evaluated" as not-a-defect
            # (only an explicit False unqualifies).  We degrade only on an
            # explicit feature_ready=False or an active exception.
            feature_unqualified = facts.feature_ready is False or facts.exception_active is True
            if feature_unqualified:
                excluded.append(
                    {
                        "security_code": code,
                        "reason": "held_feature_not_ready_or_exception_hold_alert",
                        "member_or_field": (
                            "feature_ready" if facts.feature_ready is False else "exception_active"
                        ),
                        "at": knowledge_cutoff.isoformat(),
                        "scope": "held_features",
                        "fallback": "source_only_base_review",
                    }
                )
                facts = _held_source_only(row)
                assert facts is not None
            review = HeldReviewSubject(
                identity=EventIdentity(
                    row.security_id,
                    row.session_date,
                    "p11.daily-held-review",
                    "1.0.0",
                    snapshot_id,
                ),
                knowledge_cutoff=row.knowledge_cutoff,
                signal_close=row.close_at,
                decision_at=knowledge_cutoff,
                source_evidence_digest=row.source_evidence_digest,
            )
            held_decision = adapter.evaluate_held(
                review=review,
                features=facts,
                position_facts=position_facts,
                knowledge_cutoff=knowledge_cutoff,
                position_state="trial",
                prior_deterioration_sessions=(prior_deterioration_sessions_by_security or {}).get(
                    code
                ),
            )
            if held_decision.action == "hold_alert":
                held_decision = dataclasses.replace(
                    held_decision,
                    unavailable_reason=(held_unavailable_reasons_by_security or {}).get(
                        code, "holding_position_facts_unavailable"
                    ),
                )
            held_decisions.append(held_decision)
    return SeamResult(
        day_blocked=False,
        day_block_reason=None,
        decisions=tuple(decisions),
        excluded=tuple(excluded),
        held_decisions=tuple(held_decisions),
        scan_result=scan_result,
        experts=ExpertBundle.from_runner(runner),
        evaluated_universe=tuple(
            sorted({row.listing_code for row in rows} | set(held_facts_by_security or {}))
        ),
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
    if experts != result.experts:
        return (), ()
    signals: list[CandidateSignal] = []
    held_signals: list[HeldSignal] = []
    for item in result.entries:
        if type(item.decision) is not FormalStrategyDecision or item.event_id is None:
            continue
        # The ExpertBundle digest must equal the topology digest the real runner stamped on
        # this decision; a registered component name with an arbitrary bundle digest cannot
        # pass without having actually run this runner on this event.
        if (
            item.decision.topology_digest != experts.digest
            or item.expert_decision_digest != item.decision.digest
            or item.event_id != item.decision.event_id
            or item.knowledge_cutoff != item.decision.governance.knowledge_cutoff
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
            expert_decision_digest=item.decision.digest,
            risk_decision_digest=item.decision.digest,
            formal_decision=item.decision,
            signal_close=item.signal_close,
            knowledge_cutoff=item.knowledge_cutoff,
            decision_at=item.decision_at,
        )
        signals.append(candidate)
    for held in result.held_decisions:
        if held.decision is not None and (
            type(held.decision) is not FormalStrategyDecision
            or held.decision.topology_digest != experts.digest
            or held.decision_digest != held.decision.digest
        ):
            continue
        features = held.features
        held_signal = HeldSignal(
            opportunity_id=(
                held.decision.event_id
                if held.decision is not None
                else f"held-{held.security_code}"
            ),
            security_code=held.security_code,
            security_name=(features.security_name if features else None) or held.security_code,
            industry_id=(features.industry_id if features else None) or "industry-unbound",
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
                        "position facts unavailable; hold and alert: "
                        + (held.unavailable_reason or held.reason)
                        if held.action == "hold_alert"
                        else f"expert held outcome {held.reason}"
                    ),
                },
            ),
            next_observation=f"next session after {held.security_code} close",
            depends_on_member_ids=features.depends_on_member_ids if features else (),
            expert_decision_digest=held.decision_digest,
            risk_decision_digest=held.decision_digest,
            formal_decision=held.decision,
            signal_close=held.signal_close,
            knowledge_cutoff=held.knowledge_cutoff,
            decision_at=held.decision_at,
        )
        held_signals.append(held_signal)
    return tuple(signals), tuple(held_signals)


RULE_DAY_PRODUCER_ID = "p11.fixed-rule-day"


@dataclass(frozen=True)
class RuleDayOutput:
    seam: SeamResult
    candidates: tuple[CandidateSignal, ...]
    held_signals: tuple[HeldSignal, ...]
    receipt: CandidateEvaluationReceipt | None
    result: ProducerResult
    source_closure: dict[str, Any]
    stage_metrics: tuple[dict[str, Any], ...]


def _enriched_history_receipts(
    history_receipts: Sequence[Mapping[str, Any]] | None,
    *,
    features_by_security: Mapping[str, SecurityFeatureFacts],
    seam: SeamResult,
    signal_date: date,
    scan_rows: Sequence[CandidateDailyInput],
) -> list[dict[str, Any]]:
    """Bind the ORIGINAL algorithm verdicts onto the per-security history receipts.

    The gate floors are not recomputed: each gate's satisfied|failed|unavailable
    comes from the real panel row (feature_ready/entry_eligible/listing_sessions)
    and the real scan verdict (a sufficient 22-session window evaluated to a real
    trigger-or-quiet verdict vs an unusable window), so a short history is a named
    failure rather than a qualified empty - and a fully-scanned no-signal is a
    qualified quiet day, not a blocked one.  Excluded securities keep their
    exclusion record.
    """
    if not history_receipts:
        return []
    decision_codes = {d.security_code for d in seam.decisions}
    excluded_reason = {
        e.get("security_code"): e.get("reason") or e.get("member_or_field") for e in seam.excluded
    }
    # The real scan verdicts the SAME evaluation produced, re-keyed from
    # (security_id, signal_date) to this run's listing_code via the actual scan rows
    # (each row carries both identities).  A receipt names the per-security T verdict
    # from this map - never re-derived from counters and never "no candidate" ==
    # NOT_TRIGGERED.
    id_to_code: dict[str, str] = {row.security_id: row.listing_code for row in scan_rows}
    scan_verdicts: dict[tuple[str, str], str] = {}
    if seam.scan_result is not None:
        for sec_id, sig_date, verdict in getattr(seam.scan_result, "signal_verdicts", ()):
            resolved = id_to_code.get(sec_id, sec_id)
            scan_verdicts[(resolved, sig_date)] = verdict
    out: list[dict[str, Any]] = []
    for raw_receipt in history_receipts:
        receipt = dict(raw_receipt)
        code = receipt.get("security_code")
        if receipt.get("status") == "excluded":
            out.append(receipt)
            continue
        facts = features_by_security.get(code)
        gates = dict(receipt.get("gates") or {})

        def _v(ok: bool | None, reason: str) -> dict[str, Any]:
            return {
                "status": "unavailable" if ok is None else ("satisfied" if ok else "failed"),
                "reason": reason,
            }

        # The events gate is the real 22-session scan window VERDICT, not the
        # trigger outcome.  A security that produced a valid T scan row (its
        # facts exist - the raw closure and signal row both qualified) reached
        # a real scan evaluation: it either fired a candidate transition or
        # evaluated NOT_TRIGGERED / a named eligibility veto / a real blocked
        # verdict.  The verdict comes from the SAME scan's signal_verdicts for
        # this security at T - not from reason_counts and never "no candidate"
        # == NOT_TRIGGERED.  The window-sufficiency floor stays named in
        # required/observed.
        event_scanned = code in decision_codes and any(
            d.event_id is not None for d in seam.decisions if d.security_code == code
        )
        events_observed = gates.get("events", {}).get("observed")
        events_required = gates.get("events", {}).get("required")
        window_sufficient = (
            isinstance(events_observed, int)
            and isinstance(events_required, int)
            and events_observed >= events_required
        )
        # Resolve this security's real scan verdict at T (receipts are keyed on
        # listing_code; scan_verdicts was already re-keyed to listing_code above).
        scan_verdict = scan_verdicts.get((code, signal_date.isoformat()))
        # Only a verdict the scan window actually evaluated to a conclusive
        # end-state counts as a QUALIFIED day: a real transition ("triggered")
        # or a real evaluated no-signal ("not_triggered").  Every named
        # pre-evaluation veto / unusable window - knowledge_after_t_close,
        # t_minus_1_not_tradable, zero_activity_baseline, observation_gap,
        # missing_input and the rest - means the T facts never reached a
        # qualified evaluation; the security is not_qualified at this gate,
        # never an "evaluated qualified no-signal".  A missing-input or
        # unusable-window day is a real named outcome but NOT a publishable
        # qualified verdict.
        scan_evaluated_ok = scan_verdict in ("triggered", "not_triggered")
        scan_reached_verdict = facts is not None and window_sufficient and scan_evaluated_ok
        if event_scanned or scan_verdict == "triggered":
            events_reason = "event scan produced a transition"
        elif scan_verdict is not None:
            # A real verdict from the same scan.  A genuine evaluated
            # no-signal keeps its qualified name; a named eligibility veto /
            # unusable window keeps its own reason and fails the gate - never
            # flattened to a generic "no-signal" and never counted qualified.
            if scan_verdict == "not_triggered":
                events_reason = "qualified no-signal: scan window reached a real verdict"
            else:
                events_reason = f"scan verdict at T: {scan_verdict}"
        elif facts is not None and window_sufficient:
            events_reason = "no qualified event window in the 22-session scan"
        else:
            events_reason = "no qualified event window in the 22-session scan"
        gates["events"] = {
            **gates.get("events", {}),
            **_v(
                scan_reached_verdict if facts is not None else None,
                events_reason,
            ),
        }
        gates["features"] = {
            **gates.get("features", {}),
            **_v(
                None if facts is None else bool(facts.feature_ready),
                "panel feature_ready qualified on the signal row"
                if (facts is not None and facts.feature_ready)
                else (excluded_reason.get(code) or ("panel feature_ready=False / facts missing")),
            ),
        }
        listing_ok = (
            None if facts is None else facts.listing_sessions >= 120  # GATE_LISTING; original floor
        )
        gates["listing"] = {
            **gates.get("listing", {}),
            **_v(
                listing_ok,
                f"listing_sessions={None if facts is None else facts.listing_sessions}"
                " vs required 120",
            ),
        }
        receipt["gates"] = gates
        receipt["status"] = (
            "qualified"
            if all(g.get("status") == "satisfied" for g in gates.values())
            else "not_qualified"
        )
        out.append(receipt)
    return out


def run_daily_advice(
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
    facts: DailyFactsView,
    policy: AdvicePolicy,
    account: AccountFactsView,
    run_id: str,
    held_facts_by_security: Mapping[str, HeldPositionFacts | None] | None = None,
    held_unavailable_reasons_by_security: Mapping[str, str] | None = None,
    account_membership: Sequence[Mapping[str, Any]] | None = None,
    prior_deterioration_sessions_by_security: Mapping[str, int] | None = None,
    input_exclusions: Sequence[dict[str, Any]] | None = None,
    raw_security_inputs_digest: str | None = None,
    pending_security_codes: Sequence[str] | None = None,
    # Pool-external unresolved account records (B-segment): named per-record
    # inside the issued source_closure; never silently dropped.
    # Per-order pending evidence bound into the issued closure: kind/quantity
    # plus order_key/source_receipt/direction verbatim.
    pending_evidence: Sequence[Mapping[str, Any]] | None = None,
    account_unresolved: Sequence[Mapping[str, Any]] | None = None,
    account_disposition: str | None = None,
    shared_prereq_ok: bool = True,
    shared_prereq_reason: str | None = None,
    history_receipts: Sequence[Mapping[str, Any]] | None = None,
    # Versioned October as-observed consumption evidence, bound into the
    # issued source_closure before its digest.  Strict/daily callers omit it.
    consumption_evidence: Mapping[str, Any] | None = None,
    # The verified single-T replay context token, minted upstream by the
    # versioned as-observed replay entry.  Its presence is the ONLY thing that
    # lets the producer admit the replay's actual intake/evaluation instants
    # against the run's real evaluation bound instead of the logical T
    # decision_cutoff; a strict/daily caller passes no token and keeps the
    # logical-cutoff semantics on every instant.  A bare instant can never be
    # passed - the physical bound is taken from the signed
    # consumption_evidence.package_clock, not from the caller.
    replay_context: OctoberReplayContext | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> RuleDayOutput:
    """Run domain evaluation and issue its receipt in one job, before package assembly.

    P12 supplies already-read inputs and a predeclared policy, never candidate decisions
    or a rule receipt. Prepared feature inputs are still the M1 boundary; the M2 raw-row
    adapter plugs in before this function. This entry makes no account binding or writes.
    """
    if policy.evidence_mode != "formal_runner" or type(runner) is not FormalStrategyRunner:
        raise ProductDailyError("invalid_rule_runtime", "fixed FormalStrategyRunner required")
    if (
        type(card) is not CandidateRuleCard
        or card.semantic_digest != card.computed_semantic_digest
        or policy.candidate_rule_digest != card.semantic_digest
        or card.semantic_digest
        != runner.card.semantic_sections["population"]["candidate_generator"]["digest"]
    ):
        raise ProductDailyError("rule_card_binding_mismatch", "candidate rule identity differs")
    clock_json = getattr(source_policy, "prospective_clock_json", None)
    if not isinstance(clock_json, str):
        raise ProductDailyError("prospective_clock_required", "daily entry requires its clock")
    decision_clock = prospective_clock(clock_json)
    if (
        decision_clock.market_date != facts.market_date
        or decision_clock.decision_at != policy.decision_cutoff
        or decision_clock.next_session_open != policy.eligible_at
    ):
        raise ProductDailyError("daily_clock_binding_mismatch", "daily clock identity differs")
    require_text(run_id, "run_id")
    library_root = find_project_root(Path(__file__))
    started_at = clock()
    require_aware(started_at, "evaluation_started_at")
    # The private OctoberReplayContext token is the ONLY thing that can admit a
    # replay admission clock.  It is the same unforgeable token the public raw
    # entry already requires; a strict/daily caller passing nothing (or any
    # non-token value) can never reach the late-clock branch.
    if replay_context is not None and (
        type(replay_context) is not OctoberReplayContext
        or replay_context.context_id != "october_replay_as_observed"
    ):
        raise ProductDailyError("unknown_replay_context", f"replay_context {replay_context!r}")
    # Derive the physical admission bounds from the signed package_clock proof
    # the versioned e1_runtime entry projected into consumption_evidence.  The
    # proof supplies two distinct instants: source_read_at (the members'
    # original source-acquire read) and evaluation_observed_at (THIS run's
    # real evaluation).  A bare caller-chosen wall clock can never be passed;
    # a replay context without a complete, matching package_clock mints NO
    # admission clock, so every actual instant stays judged against the
    # logical decision_cutoff (the strict outcome).
    replay_admission_clock: OctoberReplayAdmissionClock | None = None
    if replay_context is not None:
        package_clock = consumption_evidence.get("package_clock") if consumption_evidence else None
        if isinstance(package_clock, Mapping):
            schema_ok = package_clock.get("schema_version") == "p11.october-replay-package-clock.v1"
            market_ok = package_clock.get("market_date") == facts.market_date.isoformat()
            cutoff_ok = package_clock.get("logical_cutoff") == policy.decision_cutoff.isoformat()

            def _parse_clock(name: str) -> datetime | None:
                raw = package_clock.get(name)
                if not isinstance(raw, str):
                    return None
                try:
                    parsed = datetime.fromisoformat(raw)
                except ValueError:
                    return None
                if parsed.tzinfo is None or parsed.utcoffset() is None:
                    return None
                return parsed

            source_read_at = _parse_clock("source_read_at")
            evaluation_observed_at = _parse_clock("evaluation_observed_at")
            if (
                schema_ok
                and market_ok
                and cutoff_ok
                and source_read_at is not None
                and evaluation_observed_at is not None
            ):
                # Mint the bounds through the verified token; the proof
                # supplies the instants, the token supplies the authority.  A
                # proof that fails any check leaves the bound at None (strict).
                replay_admission_clock = _new_replay_admission_clock(
                    source_read_at, evaluation_observed_at, replay_context
                )
    stage_start = perf_counter()
    current_rows = tuple(row for row in rows if row.session_date <= facts.market_date)
    seam = evaluate_day(
        runner=runner,
        resources=resources,
        rows=current_rows,
        trading_dates=trading_dates,
        snapshot_id=snapshot_id,
        snapshot_manifest_digest=snapshot_manifest_digest,
        card=card,
        split_policy=split_policy,
        source_policy=source_policy,
        features_by_security=features_by_security,
        knowledge_cutoff=policy.decision_cutoff,
        held_facts_by_security=held_facts_by_security,
        held_unavailable_reasons_by_security=held_unavailable_reasons_by_security,
        prior_deterioration_sessions_by_security=prior_deterioration_sessions_by_security,
        pending_security_codes=pending_security_codes,
        shared_prereq_ok=shared_prereq_ok and policy.producer_version == RULE_DAY_PRODUCER_VERSION,
        shared_prereq_reason=(
            "producer_version_mismatch"
            if policy.producer_version != RULE_DAY_PRODUCER_VERSION
            else shared_prereq_reason
        ),
    )
    evaluation_elapsed = perf_counter() - stage_start
    if input_exclusions and not seam.day_blocked:
        seam = dataclasses.replace(
            seam,
            evaluated_universe=tuple(
                sorted(
                    set(seam.evaluated_universe)
                    | {item["security_code"] for item in input_exclusions}
                )
            ),
        )
    assembly_start = perf_counter()
    experts = ExpertBundle.from_runner(runner)
    candidates, held = seam_to_candidates(
        seam,
        experts=experts,
        facts=facts,
        policy=policy,
        eligible_at=policy.eligible_at,
        expires_at=policy.expires_at,
    )
    completed_at = clock()
    require_aware(completed_at, "evaluation_completed_at")
    source_closure = {
        "schema_version": "quanthus.p11-rule-source-closure.v1",
        "code_provenance": code_identity(library_root),
        "consumer": consumer_identity(library_root),
        "source_policy_digest": source_policy.semantic_digest,
        "split_policy_digest": split_policy.semantic_digest,
        "snapshot_id": snapshot_id,
        "snapshot_manifest_digest": snapshot_manifest_digest,
        "raw_inputs_digest": sha256_digest(
            [
                {
                    key: str(value) if isinstance(value, (date, Decimal)) else value
                    for key, value in dataclasses.asdict(row).items()
                }
                for row in sorted(
                    current_rows,
                    key=lambda row: (row.listing_code, row.session_date),
                )
            ]
        ),
        "feature_inputs_digest": sha256_digest(
            [
                {
                    key: str(value) if isinstance(value, (date, Decimal)) else value
                    for key, value in dataclasses.asdict(features_by_security[code]).items()
                }
                for code in sorted(features_by_security)
            ]
        ),
        "batch_id": facts.batch_id,
        "market_date": facts.market_date.isoformat(),
        "next_session": facts.next_session.isoformat(),
        "source_id": facts.source_id,
        "source_version": facts.source_version,
        "calendar_version": facts.calendar_version,
        "price_basis": facts.price_basis,
        "required_member_ids": sorted(facts.required_member_ids),
        "intake_receipt_id": facts.intake_receipt_id,
        "intake_received_at": facts.intake_received_at.isoformat(),
        "intake_first_read_at": facts.intake_first_read_at.isoformat(),
        "intake_verified_at": facts.intake_verified_at.isoformat(),
        "policy_id": policy.policy_id,
        "policy_version": policy.version,
        "policy_digest": policy.digest,
        "policy": policy.binding_payload,
        "day_blocked": seam.day_blocked,
        "day_block_reason": seam.day_block_reason,
        "decision_cutoff": policy.decision_cutoff.isoformat(),
        "candidate_rule_digest": policy.candidate_rule_digest,
        "members": [m.to_dict() for m in sorted(facts.members, key=lambda m: m.member_id)],
        "feature_registry_digest": resources.feature_registry.digest,
        "feature_panel_report_digest": resources.panel_report.digest,
        "topology_digest": runner.topology_digest,
        "evaluated_universe": list(seam.evaluated_universe),
        "exclusions": [*(input_exclusions or ()), *seam.excluded],
        # The complete RawSecurityInput closure digest supplied by the raw
        # adapter (bars/actions/exceptions/limits/membership/identity/held/clocks).  It
        # is distinct from raw_inputs_digest (the projected scan rows) so a byte change
        # inside a raw input alters the job identity even when projected features
        # happen not to move or that security is excluded.
        "raw_security_inputs_digest": raw_security_inputs_digest,
        "pending_security_codes": sorted(pending_security_codes or ()),
        "account_disposition": account_disposition,
        "account_membership": [dict(item) for item in (account_membership or ())],
        # Pool-external unresolved account records, named verbatim inside the
        # issued closure: a held role surfaces a hold review, a pending role
        # stays pending-isolated, neither admits entry/exit for that key.
        "account_unresolved": [dict(item) for item in (account_unresolved or ())],
        # Per-order pending evidence: verbatim order identity bound into the
        # issued closure (kind/quantity/order_key/source_receipt/direction).
        "pending_evidence": [dict(item) for item in (pending_evidence or ())],
        "entry_decisions": [
            {
                "security_code": d.security_code,
                "disposition": d.disposition.value,
                "decision_digest": d.expert_decision_digest,
                "reason": d.reason,
            }
            for d in seam.decisions
        ],
        # H1 history-gate receipts: per-security 22/61/120 observations + the
        # bounded listing-floor proof kind, bound into the job's digested closure.
        "held_decisions": [
            {
                "security_code": d.security_code,
                "action": d.action,
                "decision_digest": d.decision_digest,
                "reason": d.reason,
                "unavailable_reason": d.unavailable_reason,
                "trend_context": None
                if d.decision is None
                else _held_no_trend_evidence(
                    subject_id=d.decision.event_id,
                    security_id=d.decision.governance.security_code,
                    snapshot_id=d.decision.governance.snapshot_id,
                    knowledge_cutoff=d.decision.governance.knowledge_cutoff,
                    topology_digest=d.decision.topology_digest,
                ),
            }
            for d in seam.held_decisions
        ],
    }
    enriched_history = _enriched_history_receipts(
        history_receipts,
        features_by_security=features_by_security,
        seam=seam,
        signal_date=facts.market_date,
        scan_rows=current_rows,
    )
    source_closure["history_receipts"] = enriched_history
    source_closure["history_receipts_digest"] = sha256_digest(enriched_history)

    # Versioned October as-observed consumption evidence is folded into the
    # closure BEFORE its digest: the original envelope identity, selected
    # member/row versions and original clocks, this run's actual open/verify/
    # observe instants, exclusions and the member catalogue are all signed so
    # the job identity recomputes from receipt + final closure together.  A
    # change to any of them drifts the digest rather than passing silently.
    if consumption_evidence is not None:
        source_closure["consumption_evidence"] = dict(consumption_evidence)

    # H1-R2 P1-1: a history gate is a publish gate, not a label.  A receipt
    # marked not_qualified means the original algorithm did NOT qualify that
    # security - the day is only publishable when at least one market input was
    # qualified, or a real held/exit evaluation produced an independent output.
    #
    # account_member_market_unavailable is not a market verdict: the market
    # parse failed but the held review ran on its own facts, so that security
    # contributes no market evidence either way.
    market_receipts = [
        item
        for item in enriched_history
        if item.get("status") != "account_member_market_unavailable"
    ]
    any_market_qualified = any(item.get("status") == "qualified" for item in market_receipts)
    all_market_failed = bool(market_receipts) and not any_market_qualified
    # Any held_decisions entry is an independent held evaluation - even a
    # hold_alert, which is itself a valid held output (missing facts fail closed
    # to hold_alert, never a manufactured entry).  A blocked whole-day or a
    # missing-held case simply has an empty tuple.
    held_evaluated = bool(seam.held_decisions)
    if all_market_failed and not candidates and not held_evaluated and not seam.day_blocked:
        seam = dataclasses.replace(
            seam,
            day_blocked=True,
            day_block_reason=(
                "history_gate_unsatisfied: every market input failed its "
                "history qualification and no independent held evaluation "
                "produced an output; this is a blocked day, never a qualified "
                "no-advice package"
            ),
        )

    closure_digest = sha256_digest(source_closure)
    receipt = None
    if not seam.day_blocked and len(candidates) == len(seam.entries):
        identity: dict[str, object] = {
            "producer_id": RULE_DAY_PRODUCER_ID,
            "producer_version": RULE_DAY_PRODUCER_VERSION,
            "run_id": run_id,
            "evaluated_universe": sorted(seam.evaluated_universe),
            "source_closure_digest": closure_digest,
            "evaluation_started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "evaluated_count": len(seam.evaluated_universe),
            "qualified_count": len(candidates),
        }
        receipt = CandidateEvaluationReceipt(
            producer_id=RULE_DAY_PRODUCER_ID,
            producer_version=RULE_DAY_PRODUCER_VERSION,
            run_id=run_id,
            completed_at=completed_at,
            evaluation_started_at=started_at,
            source_closure_digest=closure_digest,
            evaluated_universe=seam.evaluated_universe,
            evaluated_count=len(seam.evaluated_universe),
            qualified_count=len(candidates),
            coverage_digest="sha256:" + "0" * 64,
            binding_digest=CandidateEvaluationReceipt.expected_binding_digest(
                facts,
                policy,
                candidates,
                receipt_identity=identity,
                held_signals=held,
            ),
        )
        receipt = dataclasses.replace(receipt, coverage_digest=receipt.expected_coverage_digest())
    result = DailyAdviceProducer(repo_root=library_root)._assemble(
        facts=facts,
        account=account,
        policy=policy,
        experts=experts,
        candidates=candidates,
        held_signals=held,
        evaluation_receipt=receipt,
        replay_admission_clock=replay_admission_clock,
    )
    if seam.day_blocked:
        result = dataclasses.replace(
            result,
            diagnostics=(
                {
                    "reason": seam.day_block_reason or "shared_prerequisite_failed",
                    "scope": "whole_day",
                    "stage": "shared_prerequisite",
                },
            ),
        )
    elif len(candidates) != len(seam.entries):
        result = dataclasses.replace(
            result,
            diagnostics=(
                {
                    "reason": "entry_materialization_mismatch",
                    "scope": "whole_day",
                    "expected": len(seam.entries),
                    "actual": len(candidates),
                },
            ),
        )
    elif seam.excluded:
        result = dataclasses.replace(result, diagnostics=(*result.diagnostics, *seam.excluded))
    return RuleDayOutput(
        seam,
        candidates,
        held,
        receipt,
        result,
        source_closure,
        (
            {
                "stage": "prepared_features",
                "disposition": "reuse_supplied_job_local",
                "rows": len(features_by_security),
                "bytes": None,
                "elapsed_seconds": None,
            },
            {
                "stage": "scan_and_expert_evaluation",
                "disposition": "blocked_before_scan" if seam.day_blocked else "built_once_TMP",
                "rows": 0 if seam.day_blocked else len(current_rows),
                "input_rows": len(current_rows),
                "bytes": None,
                "elapsed_seconds": evaluation_elapsed,
            },
            {
                "stage": "receipt_and_package",
                "disposition": "built_once_TMP",
                "rows": len(candidates) + len(held),
                "bytes": None,
                "elapsed_seconds": perf_counter() - assembly_start,
            },
        ),
    )


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
    """Strict-load the machine invalidation/position policy.

    v1 files keep the original schema gate.  v2 additionally declares the seven
    fact-dependency conditions and the confirm_buy action scope from the P11/P12
    machine-fact contract (#723 comment7116501586): missing or unverified T+1
    preopen facts may only block the buy confirmations that depend on them, while
    eligible sell/reduce continues with an alert.  The conditions apply at the
    T+1 preopen/confirmation phase; they are not a requirement to hold future T+1
    quotes while producing the T-evening package.
    """

    raw = _load_json(path)
    if not isinstance(raw, dict):
        raise ProductDailyError("invalid_three_piece", "machine-policy must be an object")
    schema = raw.get("schema_version")
    if schema not in {"quanthus.p11-d1b.machine-policy.v1", "quanthus.p11-d1b.machine-policy.v2"}:
        raise ProductDailyError("invalid_three_piece", "machine-policy schema_version differs")
    caps = raw.get("position_caps")
    if not isinstance(caps, list):
        raise ProductDailyError("invalid_three_piece", "machine-policy position_caps missing")
    for cap in caps:
        if not isinstance(cap, dict) or "denominator" not in cap:
            raise ProductDailyError("invalid_three_piece", "position_cap missing denominator")
    if schema == "quanthus.p11-d1b.machine-policy.v2":
        _validate_machine_policy_v2(raw)
    return raw


# Actions that a T+1 preopen fact may block.  confirm_buy is the only action a missing
# or unverified preopen dependency can suppress; sell and reduce confirmations are
# outside its scope and continue with an alert.
MACHINE_BUY_ACTIONS = frozenset({"new_entry", "confirm_buy"})

# Frozen v2 contract values (#723 comment7116501586): exactly these seven unique
# condition identities, in this order; exactly these four position caps; the exact
# evaluation phase, veto precedence and action-scope semantics below.  The strict
# loader is an identity gate, not a substring check - a policy that drops, duplicates,
# reorders, renames or rewords any of these is a different contract and fails closed.
MACHINE_POLICY_V2_CONDITION_FIELDS: tuple[str, ...] = (
    "price_limit_or_halt_unavailable",
    "auction_snapshot_unavailable",
    "single_security_fraction",
    "per_industry_max",
    "new_buckets_per_day",
    "total_position",
    "expert_veto",
)
MACHINE_POLICY_V2_CAP_FIELDS: tuple[str, ...] = (
    "single_security_fraction",
    "per_industry_max",
    "new_buckets_per_day",
    "total_position",
)
MACHINE_POLICY_V2_CAP_DENOMINATORS: dict[str, str] = {
    "single_security_fraction": "unit_notional_bucket_budget",
    "per_industry_max": "active_or_reserved_count",
    "new_buckets_per_day": "buckets",
    "total_position": "account_equity",
}
MACHINE_POLICY_V2_EVALUATION_PHASE = "T+1 preopen or user confirmation; not T package generation"
MACHINE_POLICY_V2_VETO_PRECEDENCE: tuple[str, ...] = (
    "force_liquidate_veto",
    "reduce_only_veto",
    "block_new",
)
MACHINE_POLICY_V2_ACTION_SCOPE: dict[str, str] = {
    "new_entry": "new buy plan",
    "confirm_buy": "buy confirmation only; sell and reduce confirmation excluded",
}

# The complete frozen semantic surface of the v2 machine policy, expressed as the
# canonical JSON of each top-level semantic key.  Generated from the authority
# candidate MACHINE-POLICY-FACT-DEPENDENCIES.candidate.v2.json
# (sha256:1466daffe451f8025793f710c19191b5b52aa50d416bc8d2e8729d6e406260c2) as
# transcribed into config/research/r11/d1b-20261124/machine-policy.v2.json: every
# condition's dependency/provenance/grade/phase/action/on-missing/veto/
# semantic-limit contents, every cap's value-ref/priority/veto/denominator, the
# base policy sha256, the invalidation scope and the top-level
# paper_quote_feed_acceptance/real_account_fact_grade/scope sentences.  A policy
# file that deviates on any of these keys - or adds semantic keys - is a different
# contract and fails closed; only the four metadata keys
# (schema_version/policy_version/adoption_state/daily_adopted) sit outside this
# surface.
MACHINE_POLICY_V2_META_KEYS = frozenset(
    {"schema_version", "policy_version", "adoption_state", "daily_adopted"}
)
# fmt: off
# ruff: noqa: E501
MACHINE_POLICY_V2_FROZEN_JSON: dict[str, str] = {
    'base_machine_policy': '"config/research/r11/d1b-20261124/machine-policy.v1.json"',
    'base_sha256': '"5dcc84c833828a62555b9588ec5341cdb78d63ec87a5a9930a39b495e4c49896"',
    'invalidations': '[{"applies_to_actions":["new_entry","confirm"],"field":"price_limit_or_halt_unavailable","observed_at":"T+1 pre-open","reason":"T+1 suspension/native price limit not yet a named qualified source","timezone":"Asia/Shanghai","type":"boolean","unit":"flag","value":"block","veto":true},{"applies_to_actions":["confirm"],"field":"auction_snapshot_unavailable","observed_at":"T+1 09:00-09:25","reason":"no named auction semantic snapshot; zero-volume/last_price not a substitute","timezone":"Asia/Shanghai","type":"enum","unit":"state","value":"unavailable","veto":false}]',  # noqa: E501
    'position_caps': '[{"denominator":"unit_notional_bucket_budget","field":"single_security_fraction","note":"base_trial_position_fraction is a fraction of the trial unit_notional/bucket budget (same bucket as the expert allowed_maximum_position ceiling); P12 sizes the actual quantity against real account equity/cash/fees. NOT the historical 4/8/20 research caps","priority":"expert_ceiling","value":"from card position_and_execution base_trial_position_fraction","veto":false},{"denominator":"active_or_reserved_count","field":"per_industry_max","priority":"entry_limit","value":"EntryLimits.max_active_or_reserved_per_industry","veto":false},{"denominator":"buckets","field":"new_buckets_per_day","priority":"entry_limit","value":"EntryLimits.max_new_buckets_per_day","veto":false},{"denominator":"account_equity","field":"total_position","priority":"capacity","value":"quantity_limits.total","veto":false}]',  # noqa: E501
    'veto_precedence': '["force_liquidate_veto","reduce_only_veto","block_new"]',
    'expert_veto': '"system/position guardian + scorer; a veto always suppresses executable entry"',
    'action_scope_definition': '{"confirm_buy":"buy confirmation only; sell and reduce confirmation excluded","new_entry":"new buy plan"}',  # noqa: E501
    'evaluation_phase': '"T+1 preopen or user confirmation; not T package generation"',
    'sell_reduce_on_missing_preopen_facts': '"continue_with_alert"',
    'opening_auction_window': '"09:15-09:25 Asia/Shanghai; 09:00 unavailable"',
    'paper_quote_feed_acceptance': '"accepted_only_for_P10_727_verified_fields"',
    'real_account_fact_grade': '"formal_named_supply_727"',
    'scope': '"T+1 preopen three fact classes for personal simulated accounts only"',
    'conditions': '[{"applies_to_actions":["new_entry","confirm_buy"],"conditional_activation":"only_when_active_predicate_depends_on_this_fact","evaluation_phase":"T+1 preopen or user confirmation; not T package generation","field":"price_limit_or_halt_unavailable","on_missing_or_unverified":"block_only_buy_confirmation_depending_on_this_fact","paper_quote_feed_acceptance":"accepted_only_for_P10_727_verified_fields","paper_required_qualification":"P10 #727 field/scope/source/clock receipt","preopen_fact_dependencies":["suspension_resumption","limit_prices"],"preserved_veto_flag":true,"real_account_fact_grade":"formal_named_supply_727","required_provenance":["source_ref","fact_class","fact_grade","security_code","applicable_session","observed_at","received_at","qualification_receipt"],"sell_reduce_on_missing_preopen_facts":"continue_with_alert","semantic_limits":["zero_volume_is_not_suspension","ordinary_last_is_not_relabelled_as_auction","unknown_remains_unavailable"]},{"applies_to_actions":["confirm_buy"],"conditional_activation":"only_when_active_predicate_depends_on_this_fact","evaluation_phase":"T+1 preopen or user confirmation; not T package generation","field":"auction_snapshot_unavailable","on_missing_or_unverified":"block_only_buy_confirmation_depending_on_this_fact","paper_quote_feed_acceptance":"accepted_only_for_P10_727_verified_fields","paper_required_qualification":"P10 #727 field/scope/source/clock receipt","preopen_fact_dependencies":["opening_auction"],"preserved_veto_flag":false,"real_account_fact_grade":"formal_named_supply_727","required_provenance":["source_ref","fact_class","fact_grade","security_code","applicable_session","observed_at","received_at","qualification_receipt"],"sell_reduce_on_missing_preopen_facts":"continue_with_alert","semantic_limits":["zero_volume_is_not_suspension","ordinary_last_is_not_relabelled_as_auction","unknown_remains_unavailable"]},{"field":"single_security_fraction","non_preopen_dependencies":["fixed_strategy_card","qualified_expert_decision","unit_notional_bucket_budget"],"on_missing_or_unverified":"preserve_existing_account_or_rule_admission","paper_quote_feed_acceptance":"not_applicable_to_this_preopen_contract","preopen_fact_dependencies":[],"preserved_denominator":"unit_notional_bucket_budget","preserved_value_ref":"from card position_and_execution base_trial_position_fraction"},{"field":"per_industry_max","non_preopen_dependencies":["qualified_industry_binding","active_or_reserved_account_state","EntryLimits"],"on_missing_or_unverified":"preserve_existing_account_or_rule_admission","paper_quote_feed_acceptance":"not_applicable_to_this_preopen_contract","preopen_fact_dependencies":[],"preserved_denominator":"active_or_reserved_count","preserved_value_ref":"EntryLimits.max_active_or_reserved_per_industry"},{"field":"new_buckets_per_day","non_preopen_dependencies":["daily_account_intake_state","EntryLimits"],"on_missing_or_unverified":"preserve_existing_account_or_rule_admission","paper_quote_feed_acceptance":"not_applicable_to_this_preopen_contract","preopen_fact_dependencies":[],"preserved_denominator":"buckets","preserved_value_ref":"EntryLimits.max_new_buckets_per_day"},{"field":"total_position","non_preopen_dependencies":["account_equity","existing_and_pending_commitments","quantity_limits.total"],"on_missing_or_unverified":"preserve_existing_account_or_rule_admission","paper_quote_feed_acceptance":"not_applicable_to_this_preopen_contract","preopen_fact_dependencies":[],"preserved_denominator":"account_equity","preserved_value_ref":"quantity_limits.total"},{"field":"expert_veto","non_preopen_dependencies":["actual_qualified_guardian_constraint_scorer_decision"],"on_missing_or_unverified":"no_executable_new_entry_and_alert; preserve_independently_qualified_held_exit","paper_quote_feed_acceptance":"not_applicable_to_this_preopen_contract","precedence":["force_liquidate_veto","reduce_only_veto","block_new"],"preopen_fact_dependencies":[]}]',  # noqa: E501
    'held_reduction_wire': '{"consumer_capability":"supports_held_reduction_v2","exit_kind_field":"exit_kind","missing_consumer_disposition":"display_only","reduce_denominator":"locked_cycle_budget","reduce_denominator_note":"the matching held funding cycle\'s locked_cycle_budget fixed at cycle activation; 45,000 CNY is a paper-account example, not a P11 constant","target_field":"target_position_fraction","wire_version":"p12.decision-set.v2"}',  # noqa: E501
    'not_changed': '["strategy thresholds","daily/historical input qualification","P6","account adoption","sentiment parked739"]',  # noqa: E501
}


def _machine_policy_v2_frozen(key: str) -> Any:
    return json.loads(MACHINE_POLICY_V2_FROZEN_JSON[key])


# Consumer-binding schema the verified release pipeline resolves against.  A pinned
# consumer declares the one wire schema it validates; the binding's pinned source
# identity (commit/tree/loader entry/file hashes) is what makes it more than a caller
# flag.
CONSUMER_BINDING_SCHEMA = "quanthus.p11.p12-consumer-binding.v1"


def _validate_machine_policy_v2(raw: dict[str, Any]) -> None:
    """Fail closed on any deviation from the frozen v2 fact-dependency contract.

    Identity gate first (registered condition/cap identities in frozen order, the
    action-scope wording, buy-only blocking, denominators and the wire contract)
    so a malformed policy reports the specific rule it broke; then the full
    frozen semantic surface MACHINE_POLICY_V2_FROZEN_JSON must deep-equal every
    semantic top-level key - dependency/provenance/grade/phase/veto/
    semantic-limit contents, cap values/priorities/veto flags, base sha256,
    invalidation scope and the top-level quote-feed/real-account/scope sentences.
    Missing, mutated, nested-tampered or extra keys are a different contract and
    fail closed.
    """

    if raw.get("adoption_state") != "proposed" or raw.get("daily_adopted") is not False:
        raise ProductDailyError(
            "invalid_three_piece",
            "machine-policy v2 must stay proposed with daily_adopted=false",
        )
    scopes = raw.get("action_scope_definition")
    if scopes != MACHINE_POLICY_V2_ACTION_SCOPE:
        raise ProductDailyError(
            "invalid_three_piece",
            "machine-policy v2 action_scope_definition must equal the frozen "
            f"{MACHINE_POLICY_V2_ACTION_SCOPE}",
        )
    if raw.get("sell_reduce_on_missing_preopen_facts") != "continue_with_alert":
        raise ProductDailyError(
            "invalid_three_piece",
            "missing preopen facts must leave sell/reduce as continue_with_alert",
        )
    if raw.get("evaluation_phase") != MACHINE_POLICY_V2_EVALUATION_PHASE:
        raise ProductDailyError(
            "invalid_three_piece",
            "machine-policy v2 evaluation_phase must be exactly "
            f"{MACHINE_POLICY_V2_EVALUATION_PHASE!r}",
        )
    if tuple(raw.get("veto_precedence") or ()) != MACHINE_POLICY_V2_VETO_PRECEDENCE:
        raise ProductDailyError(
            "invalid_three_piece",
            "machine-policy v2 veto_precedence must be exactly "
            f"{list(MACHINE_POLICY_V2_VETO_PRECEDENCE)}",
        )
    caps = raw.get("position_caps") or []
    cap_fields = [cap.get("field") for cap in caps if isinstance(cap, dict)]
    if cap_fields != list(MACHINE_POLICY_V2_CAP_FIELDS):
        raise ProductDailyError(
            "invalid_three_piece",
            "machine-policy v2 must carry exactly the four registered caps "
            f"{list(MACHINE_POLICY_V2_CAP_FIELDS)} in order; got {cap_fields}",
        )
    for cap in caps:
        expected_denominator = MACHINE_POLICY_V2_CAP_DENOMINATORS[cap["field"]]
        if cap.get("denominator") != expected_denominator:
            raise ProductDailyError(
                "invalid_three_piece",
                f"machine-policy v2 cap {cap['field']} denominator must be "
                f"{expected_denominator}, never {cap.get('denominator')}",
            )
    conditions = raw.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        raise ProductDailyError("invalid_three_piece", "machine-policy v2 conditions missing")
    condition_fields = [cond.get("field") for cond in conditions if isinstance(cond, dict)]
    if condition_fields != list(MACHINE_POLICY_V2_CONDITION_FIELDS):
        raise ProductDailyError(
            "invalid_three_piece",
            "machine-policy v2 must carry exactly the seven registered conditions "
            f"{list(MACHINE_POLICY_V2_CONDITION_FIELDS)} in order; got "
            f"{condition_fields}",
        )
    for index, cond in enumerate(conditions):
        if not isinstance(cond, dict) or "field" not in cond:
            raise ProductDailyError(
                "invalid_three_piece", f"machine-policy v2 conditions[{index}] malformed"
            )
        preopen_deps = cond.get("preopen_fact_dependencies")
        if not isinstance(preopen_deps, list):
            raise ProductDailyError(
                "invalid_three_piece",
                f"machine-policy v2 {cond.get('field')} missing preopen_fact_dependencies",
            )
        if preopen_deps:
            applies = cond.get("applies_to_actions")
            if not isinstance(applies, list) or "confirm_buy" not in applies:
                raise ProductDailyError(
                    "invalid_three_piece",
                    f"machine-policy v2 {cond.get('field')} preopen scope must include confirm_buy",
                )
            if any(action not in MACHINE_BUY_ACTIONS for action in applies):
                raise ProductDailyError(
                    "invalid_three_piece",
                    f"machine-policy v2 {cond.get('field')} may only block buy actions "
                    f"{sorted(MACHINE_BUY_ACTIONS)}, never sell/reduce",
                )
            if cond.get("on_missing_or_unverified") != (
                "block_only_buy_confirmation_depending_on_this_fact"
            ):
                raise ProductDailyError(
                    "invalid_three_piece",
                    f"machine-policy v2 {cond.get('field')} on_missing_or_unverified "
                    "must be block_only_buy_confirmation_depending_on_this_fact",
                )
            if cond.get("sell_reduce_on_missing_preopen_facts") != "continue_with_alert":
                raise ProductDailyError(
                    "invalid_three_piece",
                    f"machine-policy v2 {cond.get('field')} must keep "
                    "sell_reduce_on_missing_preopen_facts=continue_with_alert",
                )
            for key in ("required_provenance", "semantic_limits"):
                if not isinstance(cond.get(key), list) or not cond.get(key):
                    raise ProductDailyError(
                        "invalid_three_piece",
                        f"machine-policy v2 {cond.get('field')} missing {key}",
                    )
        else:
            if not isinstance(cond.get("non_preopen_dependencies"), list):
                raise ProductDailyError(
                    "invalid_three_piece",
                    f"machine-policy v2 {cond.get('field')} missing non_preopen_dependencies",
                )
    wire = raw.get("held_reduction_wire")
    if not isinstance(wire, dict):
        raise ProductDailyError(
            "invalid_three_piece", "machine-policy v2 held_reduction_wire missing"
        )
    if wire.get("wire_version") != "p12.decision-set.v2":
        raise ProductDailyError(
            "invalid_three_piece",
            "machine-policy v2 held_reduction_wire.wire_version must be "
            "p12.decision-set.v2",
        )
    if wire.get("reduce_denominator") != "locked_cycle_budget":
        raise ProductDailyError(
            "invalid_three_piece",
            "reduce target_position_fraction must divide by the held funding cycle's "
            "locked_cycle_budget, never account_equity or a sell fraction",
        )
    # Full semantic freeze: every semantic top-level key must deep-equal the
    # frozen canonical surface; only the four metadata keys sit outside it.
    unknown = set(raw) - MACHINE_POLICY_V2_META_KEYS - set(MACHINE_POLICY_V2_FROZEN_JSON)
    if unknown:
        raise ProductDailyError(
            "invalid_three_piece",
            f"machine-policy v2 carries unregistered top-level keys: {sorted(unknown)}",
        )
    for key in MACHINE_POLICY_V2_FROZEN_JSON:
        if key not in raw:
            raise ProductDailyError(
                "invalid_three_piece",
                f"machine-policy v2 missing frozen semantic key: {key}",
            )
        if raw[key] != _machine_policy_v2_frozen(key):
            raise ProductDailyError(
                "invalid_three_piece",
                f"machine-policy v2 semantic key {key} differs from the frozen"
                " authority candidate - any change requires an explicit versioned"
                " successor, never a silent edit",
            )


def machine_policy_action_scope(policy: dict[str, Any], field: str) -> frozenset[str]:
    """Actions a machine-policy field may block when its facts are missing/unverified.

    Under v2 a preopen dependency only ever yields {confirm_buy} (plus the package-time
    new_entry invalidation for the halt/limit fact); under v1 the declared
    applies_to_actions are returned verbatim.  An unregistered field is an empty scope:
    it blocks nothing.
    """

    if policy.get("schema_version") == "quanthus.p11-d1b.machine-policy.v2":
        for cond in policy.get("conditions", ()):
            if isinstance(cond, dict) and cond.get("field") == field:
                return frozenset(cond.get("applies_to_actions", ()))
        for rule in policy.get("invalidations", ()):
            if isinstance(rule, dict) and rule.get("field") == field:
                return frozenset(rule.get("applies_to_actions", ()))
        return frozenset()
    for rule in policy.get("invalidations", ()):
        if isinstance(rule, dict) and rule.get("field") == field:
            return frozenset(rule.get("applies_to_actions", ()))
    return frozenset()


def load_release_manifest(path: Any) -> dict[str, Any]:
    """Strict-load the release manifest; fail on missing closure fields."""

    raw = _load_json(path)
    if not isinstance(raw, dict):
        raise ProductDailyError("invalid_three_piece", "release-manifest must be an object")
    if raw.get("schema_version") not in {
        "quanthus.p11-d1b.release-manifest.v1",
        "quanthus.p11-d1b.release-manifest.v2",
    }:
        raise ProductDailyError("invalid_three_piece", "release-manifest schema_version differs")
    if raw.get("schema_version") == "quanthus.p11-d1b.release-manifest.v2":
        wire = raw.get("held_reduction_wire")
        if not isinstance(wire, dict) or not wire.get("wire_version"):
            raise ProductDailyError(
                "invalid_three_piece",
                "release-manifest v2 must declare held_reduction_wire.wire_version",
            )
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
    if raw["schema_version"] == "quanthus.p11-d1b.release-manifest.v2":
        from quanthus.product_daily.recommendation.release_contract import (
            validate_manifest_structure,
        )

        validate_manifest_structure(raw)
    return raw


def verify_release_manifest_closure(
    release_manifest: dict[str, Any],
    *,
    project_root: Any,
    pending_is_failure: bool = True,
) -> None:
    """Verify registered files, mandatory bindings and index/worktree closure."""
    from quanthus.product_daily.recommendation.release_contract import verify_manifest

    verify_manifest(
        release_manifest,
        project_root=Path(project_root),
        pending_is_failure=pending_is_failure,
    )


def check_three_piece_consistency(
    *,
    action_receipts: tuple[dict[str, Any], ...],
    machine_policy: dict[str, Any],
    release_manifest: dict[str, Any],
    project_root: Any = None,
) -> None:
    """Strictly reload and bind every supplied object to the same registered files."""
    from quanthus.policy import find_project_root
    from quanthus.product_daily.recommendation.release_contract import verify_manifest

    verified = verify_manifest(
        release_manifest,
        project_root=Path(project_root) if project_root is not None else find_project_root(),
        pending_is_failure=False,
    )
    if machine_policy != verified.policy or action_receipts != verified.receipts:
        raise ProductDailyError(
            "inconsistent_three_piece",
            "machine_policy/action_receipts objects differ from the registered verified files",
        )

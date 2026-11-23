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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from time import perf_counter
from typing import Any

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
        for name, value in (("mark_price", self.mark_price), ("price_bound", self.price_bound)):
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
    experts: ExpertBundle | None = None
    evaluated_universe: tuple[str, ...] = ()

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
            prior_high20=None,
            rsi14=features.rsi14,
            rsi14_previous=None,
            volume_ratio20=None,
            realized_volatility20=features.realized_volatility20,
            daily_total_return=None,
            average_amount20=None,
            listing_sessions=features.listing_sessions,
            feature_ready=all(
                value is not None
                for value in (
                    features.rsi14,
                    features.realized_volatility20,
                    features.total_return_index,
                    features.sma20,
                    features.sma60,
                    features.momentum20,
                )
            ),
            entry_eligible=features.rsi14 is not None
            and features.realized_volatility20 is not None,
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
        ctx = build_formal_expert_context(
            event=event,
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
    import hashlib

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
    held_facts_by_security: Mapping[str, HeldPositionFacts] | None = None,
    prior_deterioration_sessions_by_security: Mapping[str, int] | None = None,
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
    source_rows: dict[tuple[str, date], list[CandidateDailyInput]] = {}
    for row in rows:
        if row.knowledge_cutoff <= knowledge_cutoff:
            source_rows.setdefault((row.listing_code, row.session_date), []).append(row)
    for candidate in scan.candidates:
        code = candidate.listing_code
        if held_facts_by_security is not None and code in held_facts_by_security:
            # An existing position has its own daily review, not a second entry plan.
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
            held_decisions.append(
                adapter.evaluate_held(
                    review=review,
                    features=facts,
                    position_facts=position_facts,
                    knowledge_cutoff=knowledge_cutoff,
                    position_state="trial",
                    prior_deterioration_sessions=(
                        prior_deterioration_sessions_by_security or {}
                    ).get(code),
                )
            )
    return SeamResult(
        day_blocked=False,
        day_block_reason=None,
        decisions=tuple(decisions),
        excluded=tuple(excluded),
        held_decisions=tuple(held_decisions),
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
                        "position facts unavailable; hold and alert"
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
    held_facts_by_security: Mapping[str, HeldPositionFacts] | None = None,
    prior_deterioration_sessions_by_security: Mapping[str, int] | None = None,
    shared_prereq_ok: bool = True,
    shared_prereq_reason: str | None = None,
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
        prior_deterioration_sessions_by_security=prior_deterioration_sessions_by_security,
        shared_prereq_ok=shared_prereq_ok and policy.producer_version == RULE_DAY_PRODUCER_VERSION,
        shared_prereq_reason=(
            "producer_version_mismatch"
            if policy.producer_version != RULE_DAY_PRODUCER_VERSION
            else shared_prereq_reason
        ),
    )
    evaluation_elapsed = perf_counter() - stage_start
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
        "exclusions": list(seam.excluded),
        "entry_decisions": [
            {
                "security_code": d.security_code,
                "disposition": d.disposition.value,
                "decision_digest": d.expert_decision_digest,
                "reason": d.reason,
            }
            for d in seam.decisions
        ],
        "held_decisions": [
            {
                "security_code": d.security_code,
                "action": d.action,
                "decision_digest": d.decision_digest,
                "reason": d.reason,
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
                "disposition": "built_once_TMP",
                "rows": len(current_rows),
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

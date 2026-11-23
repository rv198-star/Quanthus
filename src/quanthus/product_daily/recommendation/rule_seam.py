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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from quanthus.event_library.scan import (
    CandidateDailyInput,
    ScannedCandidate,
    scan_forward_candidates,
)
from quanthus.expert_protocols import (
    EvidenceKind,
    ExpertEvidenceRef,
)
from quanthus.expert_strategy.context import FormalExpertContext
from quanthus.expert_strategy.runner import FormalStrategyRunner
from quanthus.feature_registry import (
    FeatureCutoffRule,
    FeatureLineage,
    FeatureSourceEvidence,
)
from quanthus.label_registry import FeatureFact
from quanthus.product_daily.contracts import (
    ProductDailyError,
    require_aware,
    require_decimal,
    require_text,
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

    def __post_init__(self) -> None:
        require_text(self.security_code, "security_code")
        for name, value in (
            ("rsi14", self.rsi14),
            ("realized_volatility20", self.realized_volatility20),
        ):
            if value is not None:
                require_decimal(value, name, minimum=Decimal("0"))
        if self.listing_sessions < 0:
            raise ProductDailyError("invalid_feature_facts", "listing_sessions must be >= 0")


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

    @property
    def executable(self) -> bool:
        return self.disposition is SeamDisposition.ENTRY and self.entry_allowed


@dataclass(frozen=True)
class SeamResult:
    """Either a set of seam decisions or a whole-day block."""

    day_blocked: bool
    day_block_reason: str | None
    decisions: tuple[SeamDecision, ...]
    excluded: tuple[dict[str, str], ...]

    @property
    def entries(self) -> tuple[SeamDecision, ...]:
        return tuple(item for item in self.decisions if item.executable)


class RuleSeamAdapter:
    """Run the real scan + expert path for one already-qualified daily batch.

    The adapter requires a real FormalStrategyRunner (loaded from the registered
    p4-trend-risk card) and a real event scan; it never fabricates an event, a trend
    state, an expert decision, or a feature value.
    """

    def __init__(self, runner: FormalStrategyRunner) -> None:
        self.runner = runner

    def _feature_fact(
        self,
        *,
        security_code: str,
        feature_id: str,
        value: Decimal | None,
        signal_date: date,
        knowledge_cutoff: datetime,
        snapshot_id: str,
        listing_sessions: int,
    ) -> FeatureFact:
        source = FeatureSourceEvidence(
            dataset="p11-d1b-daily-seam",
            column=feature_id,
            dataset_version="1.0.0",
            manifest_id=f"p11-d1b:{signal_date.isoformat()}",
            knowledge_cutoff=knowledge_cutoff,
        )
        lineage = FeatureLineage(
            feature_id=feature_id,
            feature_version="1.0.0",
            definition_digest=_digest(f"definition:{feature_id}"),
            snapshot_id=snapshot_id,
            market_date=signal_date,
            knowledge_cutoff=knowledge_cutoff,
            source_revision="p11-d1b-seam",
            universe_policy_version="p11-d1b",
            sources=(source,),
            history_sessions=max(1, listing_sessions),
            cross_section_complete=None,
            cutoff_rule=FeatureCutoffRule.SNAPSHOT_OR_EARLIER,
        )
        return FeatureFact(
            security_code=security_code,
            feature_id=feature_id,
            feature_version="1.0.0",
            lineage=lineage,
            value=value,
            unavailable_reason=None if value is not None else "feature_unavailable",
        )

    def evaluate_security(
        self,
        *,
        candidate: ScannedCandidate,
        features: SecurityFeatureFacts,
        knowledge_cutoff: datetime,
        trend_state: str | None,
        position_state: str = "empty",
    ) -> SeamDecision:
        """Run the real expert for one scanned candidate; veto/NO_ACTION stays a no-entry."""

        require_aware(knowledge_cutoff, "knowledge_cutoff")
        event = candidate.record
        ctx = FormalExpertContext(
            security_code=event.identity.security_id,
            snapshot_id=event.identity.snapshot_id,
            knowledge_cutoff=knowledge_cutoff,
            position_state=position_state,
            feature_facts=(
                self._feature_fact(
                    security_code=event.identity.security_id,
                    feature_id="price.rsi14",
                    value=features.rsi14,
                    signal_date=features.signal_date,
                    knowledge_cutoff=knowledge_cutoff,
                    snapshot_id=event.identity.snapshot_id,
                    listing_sessions=features.listing_sessions,
                ),
                self._feature_fact(
                    security_code=event.identity.security_id,
                    feature_id="risk.realized_volatility20",
                    value=features.realized_volatility20,
                    signal_date=features.signal_date,
                    knowledge_cutoff=knowledge_cutoff,
                    snapshot_id=event.identity.snapshot_id,
                    listing_sessions=features.listing_sessions,
                ),
            ),
            label_results=(),
            additional_evidence_refs=(
                ExpertEvidenceRef(
                    EvidenceKind.SNAPSHOT,
                    event.event_id,
                    event.identity.candidate_rule_version,
                    _digest(f"event:{event.event_id}"),
                ),
                ExpertEvidenceRef(
                    EvidenceKind.SNAPSHOT,
                    f"trend-observation:{event.event_id}",
                    "1.0.0",
                    _digest(f"trend-observation:{event.event_id}"),
                ),
            ),
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
        )


def _digest(label: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def evaluate_day(
    *,
    runner: FormalStrategyRunner,
    rows: Sequence[CandidateDailyInput],
    trading_dates: Sequence[date],
    snapshot_id: str,
    snapshot_manifest_digest: str,
    card: Any,
    split_policy: Any,
    source_policy: Any,
    features_by_security: Mapping[str, SecurityFeatureFacts],
    knowledge_cutoff: datetime,
    trend_state: str | None,
    shared_prereq_ok: bool = True,
    shared_prereq_reason: str | None = None,
    position_state: str = "empty",
) -> SeamResult:
    """Run the real scan + per-security expert path with whole-day / single-security gates.

    * `shared_prereq_ok=False` models a shared hard prerequisite (calendar, identity, price
      basis, batch identity) that failed, or an impact scope that cannot be located.  It
      returns a whole-day block: no package, no per-security entry.
    * A security present in the scan but missing usable feature facts is *excluded* — its new
      candidates are dropped with a reason, while other securities still produce packages.
    * A security whose expert vetoed (NO_ACTION) stays a no-entry; it is not an exclusion and
      it is not a publishable entry.
    """

    require_aware(knowledge_cutoff, "knowledge_cutoff")
    if not shared_prereq_ok:
        return SeamResult(
            day_blocked=True,
            day_block_reason=shared_prereq_reason or "shared_prerequisite_failed",
            decisions=(),
            excluded=(),
        )

    adapter = RuleSeamAdapter(runner)
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
                trend_state=trend_state,
                position_state=position_state,
            )
        )
    return SeamResult(
        day_blocked=False,
        day_block_reason=None,
        decisions=tuple(decisions),
        excluded=tuple(excluded),
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

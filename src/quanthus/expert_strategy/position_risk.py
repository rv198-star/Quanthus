"""Policy-only position-risk expert for the first formal topology."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from quanthus.expert_protocols import (
    ConstraintOutput,
    ConstraintScope,
    EvidenceKind,
    ExpertApplicability,
    ExpertContext,
    ExpertEvidenceRef,
    RequiredAction,
    VetoAuthority,
    VetoOutput,
)
from quanthus.expert_strategy.contracts import ExpertStrategyCard

POSITION_RISK_EXPERT_ID = "expert.position-risk-review"
POSITION_RISK_EXPERT_VERSION = "1.0.0"


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


def _feature(context: ExpertContext, feature_id: str) -> tuple[Decimal | None, ExpertEvidenceRef]:
    matches = [item for item in context.feature_facts if item.feature_id == feature_id]
    if len(matches) != 1:
        raise ValueError(f"formal context requires exactly one {feature_id} feature")
    fact = matches[0]
    value = fact.value
    if value is not None and not isinstance(value, Decimal):
        raise ValueError(f"formal risk feature must be decimal: {feature_id}")
    reference = next(
        item
        for item in context.evidence_refs
        if item.kind is EvidenceKind.FEATURE and item.evidence_id == feature_id
    )
    return value, reference


@dataclass(frozen=True)
class HeldPositionFacts:
    holding_sessions: int
    marked_return: Decimal | None
    prior_strategy_reduction_count: int
    evidence: ExpertEvidenceRef

    def __post_init__(self) -> None:
        if self.holding_sessions <= 0 or self.prior_strategy_reduction_count < 0:
            raise ValueError("held position facts contain invalid counts")
        if self.marked_return is not None and not self.marked_return.is_finite():
            raise ValueError("marked return must be finite")


@dataclass(frozen=True)
class EntryRiskDecision:
    veto: VetoOutput
    constraint: ConstraintOutput


@dataclass(frozen=True)
class HeldRiskDecision:
    veto: VetoOutput
    target_maximum_position: Decimal | None


@dataclass(frozen=True)
class PositionRiskPolicy:
    expert_semantic_digest: str
    mapping_digest: str
    maximum_entry_volatility: Decimal
    maximum_entry_rsi: Decimal
    entry_minimum_position: Decimal
    entry_maximum_position: Decimal
    minimum_reduce_holding_sessions: int
    reduce_return_threshold: Decimal
    reduce_volatility_threshold: Decimal
    reduce_rsi_threshold: Decimal
    reduce_target_maximum_position: Decimal
    maximum_strategy_reductions: int
    exit_volatility_threshold: Decimal
    exit_rsi_threshold: Decimal

    @classmethod
    def from_card(cls, card: ExpertStrategyCard) -> PositionRiskPolicy:
        expert = card.experts[1]
        if (expert["expert_id"], expert["expert_version"], expert["route"]) != (
            POSITION_RISK_EXPERT_ID,
            POSITION_RISK_EXPERT_VERSION,
            "policy_only",
        ):
            raise ValueError("position-risk expert binding differs")
        entry = expert["entry_policy"]
        held = expert["held_policy"]
        mapping = expert["policy_mapping"]
        assert isinstance(entry, Mapping)
        assert isinstance(held, Mapping)
        assert isinstance(mapping, Mapping)
        return cls(
            expert_semantic_digest=str(expert["expert_semantic_digest"]),
            mapping_digest=str(mapping["mapping_digest"]),
            maximum_entry_volatility=_decimal(entry["maximum_realized_volatility20"]),
            maximum_entry_rsi=_decimal(entry["maximum_rsi14"]),
            entry_minimum_position=_decimal(entry["allowed_minimum_position"]),
            entry_maximum_position=_decimal(entry["allowed_maximum_position"]),
            minimum_reduce_holding_sessions=int(held["minimum_holding_sessions_for_reduce"]),
            reduce_return_threshold=_decimal(held["reduce_marked_return_at_or_below"]),
            reduce_volatility_threshold=_decimal(held["reduce_realized_volatility20_above"]),
            reduce_rsi_threshold=_decimal(held["reduce_rsi14_above"]),
            reduce_target_maximum_position=_decimal(held["reduce_target_maximum_position"]),
            maximum_strategy_reductions=int(held["maximum_strategy_reductions"]),
            exit_volatility_threshold=_decimal(held["exit_realized_volatility20_above"]),
            exit_rsi_threshold=_decimal(held["exit_rsi14_above"]),
        )

    @staticmethod
    def _abstained_veto(evidence: tuple[ExpertEvidenceRef, ...], reason: str) -> VetoOutput:
        return VetoOutput(
            expert_id=POSITION_RISK_EXPERT_ID,
            expert_version=POSITION_RISK_EXPERT_VERSION,
            applicability=ExpertApplicability.ABSTAINED,
            applicability_reason=reason,
            reason_code=reason,
            evidence=evidence,
            authority=VetoAuthority.STRATEGY,
            allow=None,
            required_action=None,
        )

    @staticmethod
    def _abstained_constraint(
        evidence: tuple[ExpertEvidenceRef, ...], reason: str
    ) -> ConstraintOutput:
        return ConstraintOutput(
            expert_id=POSITION_RISK_EXPERT_ID,
            expert_version=POSITION_RISK_EXPERT_VERSION,
            applicability=ExpertApplicability.ABSTAINED,
            applicability_reason=reason,
            reason_code=reason,
            evidence=evidence,
            minimum_position=None,
            maximum_position=None,
            stop_rule=None,
            hold_rule=None,
            scope=None,
        )

    def evaluate_entry(
        self, context: ExpertContext, *, fallback: bool = False
    ) -> EntryRiskDecision:
        volatility, volatility_ref = _feature(context, "risk.realized_volatility20")
        rsi, rsi_ref = _feature(context, "price.rsi14")
        evidence = tuple(sorted((volatility_ref, rsi_ref), key=lambda item: item.key))
        if fallback:
            reason = "position_risk_fallback"
            return EntryRiskDecision(
                self._abstained_veto(evidence, reason),
                self._abstained_constraint(evidence, reason),
            )
        if volatility is None or rsi is None:
            reason = "position_risk_entry_evidence_unavailable"
            return EntryRiskDecision(
                self._abstained_veto(evidence, reason),
                self._abstained_constraint(evidence, reason),
            )
        if volatility > self.maximum_entry_volatility or rsi > self.maximum_entry_rsi:
            veto = VetoOutput(
                expert_id=POSITION_RISK_EXPERT_ID,
                expert_version=POSITION_RISK_EXPERT_VERSION,
                applicability=ExpertApplicability.APPLICABLE,
                applicability_reason="entry_review",
                reason_code="position_risk_entry_blocked",
                evidence=evidence,
                authority=VetoAuthority.STRATEGY,
                allow=False,
                required_action=RequiredAction.BLOCK_NEW,
            )
            return EntryRiskDecision(
                veto,
                self._abstained_constraint(evidence, "position_risk_entry_blocked"),
            )
        return EntryRiskDecision(
            VetoOutput(
                expert_id=POSITION_RISK_EXPERT_ID,
                expert_version=POSITION_RISK_EXPERT_VERSION,
                applicability=ExpertApplicability.APPLICABLE,
                applicability_reason="entry_review",
                reason_code="position_risk_entry_allowed",
                evidence=evidence,
                authority=VetoAuthority.STRATEGY,
                allow=True,
                required_action=RequiredAction.NONE,
            ),
            ConstraintOutput(
                expert_id=POSITION_RISK_EXPERT_ID,
                expert_version=POSITION_RISK_EXPERT_VERSION,
                applicability=ExpertApplicability.APPLICABLE,
                applicability_reason="entry_review",
                reason_code="position_risk_entry_capped",
                evidence=evidence,
                minimum_position=self.entry_minimum_position,
                maximum_position=self.entry_maximum_position,
                stop_rule="base_constraint_stop_preserved",
                hold_rule="base_holding_policy_preserved",
                scope=ConstraintScope.UNIT,
            ),
        )

    def evaluate_held(
        self,
        context: ExpertContext,
        facts: HeldPositionFacts,
        *,
        fallback: bool = False,
    ) -> HeldRiskDecision:
        volatility, volatility_ref = _feature(context, "risk.realized_volatility20")
        rsi, rsi_ref = _feature(context, "price.rsi14")
        evidence = tuple(
            sorted((volatility_ref, rsi_ref, facts.evidence), key=lambda item: item.key)
        )
        if fallback:
            return HeldRiskDecision(
                self._abstained_veto(evidence, "position_risk_fallback"), None
            )
        if volatility is None or rsi is None or facts.marked_return is None:
            return HeldRiskDecision(
                self._abstained_veto(evidence, "position_risk_held_evidence_unavailable"), None
            )
        if volatility > self.exit_volatility_threshold and rsi > self.exit_rsi_threshold:
            return HeldRiskDecision(
                VetoOutput(
                    expert_id=POSITION_RISK_EXPERT_ID,
                    expert_version=POSITION_RISK_EXPERT_VERSION,
                    applicability=ExpertApplicability.APPLICABLE,
                    applicability_reason="held_review",
                    reason_code="position_risk_extreme_exit",
                    evidence=evidence,
                    authority=VetoAuthority.STRATEGY,
                    allow=False,
                    required_action=RequiredAction.FORCE_LIQUIDATE,
                ),
                Decimal("0"),
            )
        reduction_triggered = (
            facts.holding_sessions >= self.minimum_reduce_holding_sessions
            and facts.prior_strategy_reduction_count < self.maximum_strategy_reductions
            and (
                facts.marked_return <= self.reduce_return_threshold
                or volatility > self.reduce_volatility_threshold
                or rsi > self.reduce_rsi_threshold
            )
        )
        if reduction_triggered:
            return HeldRiskDecision(
                VetoOutput(
                    expert_id=POSITION_RISK_EXPERT_ID,
                    expert_version=POSITION_RISK_EXPERT_VERSION,
                    applicability=ExpertApplicability.APPLICABLE,
                    applicability_reason="held_review",
                    reason_code="position_risk_reduce_once",
                    evidence=evidence,
                    authority=VetoAuthority.STRATEGY,
                    allow=False,
                    required_action=RequiredAction.REDUCE_ONLY,
                ),
                self.reduce_target_maximum_position,
            )
        return HeldRiskDecision(
            VetoOutput(
                expert_id=POSITION_RISK_EXPERT_ID,
                expert_version=POSITION_RISK_EXPERT_VERSION,
                applicability=ExpertApplicability.APPLICABLE,
                applicability_reason="held_review",
                reason_code="position_risk_hold",
                evidence=evidence,
                authority=VetoAuthority.STRATEGY,
                allow=True,
                required_action=RequiredAction.NONE,
            ),
            None,
        )

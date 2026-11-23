"""Deterministic two-expert topology composed through the accepted aggregator ports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
from decimal import Decimal
from typing import Any

from quanthus.aggregator import (
    GovernanceResult,
    GovernedAction,
    ScoreActionPolicy,
    ScoreActionResult,
    aggregate_governance,
    aggregate_score_and_map_action,
)
from quanthus.backtest.baseline_research import BaselineFeaturePanelReport, BaselineFeatureRow
from quanthus.expert_evaluation.serialization import canonical_json_bytes, digest_value
from quanthus.expert_protocols import (
    ConstraintOutput,
    ConstraintScope,
    EvidenceKind,
    ExpertApplicability,
    ExpertContext,
    ExpertEvidenceRef,
    RequiredAction,
    ScoringOutput,
    VetoAuthority,
    VetoOutput,
    run_constraint_expert,
    run_scoring_expert,
    run_veto_expert,
)
from quanthus.expert_strategy.context import ExpertDecisionSubject, FormalExpertContext
from quanthus.expert_strategy.contracts import ExpertStrategyCard
from quanthus.expert_strategy.position_risk import HeldPositionFacts, PositionRiskPolicy
from quanthus.feature_registry import FeatureLineage, FeatureRegistry, FeatureSourceEvidence
from quanthus.label_registry import FeatureFact

TREND_EXPERT_ID = "expert.trend-stability"
TREND_EXPERT_VERSION = "1.0.0"
P3_STRAT_003_CONTROL_ID = "control.p4-candidate-pass-through"
P3_STRAT_003_CONTROL_VERSION = "1.0.0"
SYSTEM_ENTRY_GUARDIAN_ID = "system.formal-entry-guardian"
SYSTEM_BASE_POSITION_ID = "system.base-position-management"


def _context_ref(context: ExpertContext, evidence_id: str) -> ExpertEvidenceRef:
    matches = [item for item in context.evidence_refs if item.evidence_id == evidence_id]
    if len(matches) != 1:
        raise ValueError(f"formal context evidence is missing: {evidence_id}")
    return matches[0]


def build_formal_expert_context(
    *,
    event: ExpertDecisionSubject,
    feature: BaselineFeatureRow | None,
    feature_registry: FeatureRegistry,
    panel_report: BaselineFeaturePanelReport,
    position_state: str,
    trend_observation_digest: str,
    position_facts: HeldPositionFacts | None = None,
) -> FormalExpertContext:
    """Bind the two risk features and domain observations to one exact PIT context."""

    cutoff = event.knowledge_cutoff.astimezone(timezone.utc)
    if feature is not None and (
        feature.market_date != event.identity.signal_date
        or feature.security_id != event.identity.security_id
    ):
        raise ValueError("formal feature row does not match event identity")
    facts: list[FeatureFact] = []
    values = {
        "price.rsi14": None if feature is None else feature.rsi14,
        "risk.realized_volatility20": (None if feature is None else feature.realized_volatility20),
    }
    for feature_id in sorted(values):
        definition = feature_registry.active(feature_id)
        lineage = FeatureLineage(
            feature_id=feature_id,
            feature_version=definition.version,
            definition_digest=definition.digest,
            snapshot_id=event.identity.snapshot_id,
            market_date=event.identity.signal_date,
            knowledge_cutoff=cutoff,
            source_revision=(panel_report.data_content_digest or panel_report.bundle_digest),
            universe_policy_version=panel_report.feature_registry_version,
            sources=(
                FeatureSourceEvidence(
                    dataset="p3_feature_panel",
                    column=feature_id,
                    dataset_version=panel_report.evaluator_version,
                    manifest_id=panel_report.digest,
                    knowledge_cutoff=cutoff,
                ),
            ),
            history_sessions=(1 if feature is None else max(1, feature.listing_sessions)),
            cross_section_complete=None,
            cutoff_rule=definition.cutoff_rule,
        )
        value = values[feature_id]
        facts.append(
            FeatureFact(
                security_code=event.identity.security_id,
                feature_id=feature_id,
                feature_version=definition.version,
                lineage=lineage,
                value=value,
                unavailable_reason=None if value is not None else "feature_unavailable",
            )
        )
    additional = [
        ExpertEvidenceRef(
            EvidenceKind.SNAPSHOT,
            f"trend-observation:{event.event_id}",
            TREND_EXPERT_VERSION,
            trend_observation_digest,
        ),
        ExpertEvidenceRef(
            EvidenceKind.SNAPSHOT,
            event.event_id,
            event.identity.candidate_rule_version,
            digest_value(event.to_dict()),
        ),
    ]
    if position_facts is not None:
        additional.append(position_facts.evidence)
    return FormalExpertContext(
        security_code=event.identity.security_id,
        snapshot_id=event.identity.snapshot_id,
        knowledge_cutoff=cutoff,
        position_state=position_state,
        feature_facts=tuple(facts),
        label_results=(),
        additional_evidence_refs=tuple(sorted(additional, key=lambda item: item.key)),
    )


class _SystemGuardian:
    expert_id = SYSTEM_ENTRY_GUARDIAN_ID
    expert_version = "1.0.0"

    def __init__(
        self,
        *,
        allow: bool,
        required_action: RequiredAction,
        reason_code: str,
        evidence_id: str,
    ) -> None:
        self.allow = allow
        self.required_action = required_action
        self.reason_code = reason_code
        self.evidence_id = evidence_id

    def evaluate(self, context: ExpertContext) -> VetoOutput:
        return VetoOutput(
            expert_id=self.expert_id,
            expert_version=self.expert_version,
            applicability=ExpertApplicability.APPLICABLE,
            applicability_reason="system_guardian",
            reason_code=self.reason_code,
            evidence=(_context_ref(context, self.evidence_id),),
            authority=VetoAuthority.SYSTEM,
            allow=self.allow,
            required_action=self.required_action,
        )


class _BasePositionConstraint:
    expert_id = SYSTEM_BASE_POSITION_ID
    expert_version = "1.0.0"

    def __init__(self, maximum_position: Decimal) -> None:
        self.maximum_position = maximum_position

    def evaluate(self, context: ExpertContext) -> ConstraintOutput:
        return ConstraintOutput(
            expert_id=self.expert_id,
            expert_version=self.expert_version,
            applicability=ExpertApplicability.APPLICABLE,
            applicability_reason="base_position_management",
            reason_code="base_position_constraint_preserved",
            evidence=(
                next(item for item in context.evidence_refs if item.kind is EvidenceKind.SNAPSHOT),
            ),
            minimum_position=Decimal("0"),
            maximum_position=self.maximum_position,
            stop_rule="base_constraint_stop_preserved",
            hold_rule="base_holding_policy_preserved",
            scope=ConstraintScope.UNIT,
        )


class _CandidatePassThroughScorer:
    expert_id = P3_STRAT_003_CONTROL_ID
    expert_version = P3_STRAT_003_CONTROL_VERSION

    def __init__(self, event_id: str, *, evidence_ready: bool) -> None:
        self.event_id = event_id
        self.evidence_ready = evidence_ready

    def evaluate(self, context: ExpertContext) -> ScoringOutput:
        evidence = (_context_ref(context, self.event_id),)
        if not self.evidence_ready:
            return ScoringOutput(
                self.expert_id,
                self.expert_version,
                ExpertApplicability.ABSTAINED,
                "candidate_control_evidence_missing",
                "candidate_control_abstained",
                evidence,
                None,
                None,
            )
        return ScoringOutput(
            self.expert_id,
            self.expert_version,
            ExpertApplicability.APPLICABLE,
            "candidate_control_eligible",
            "candidate_control_pass_through",
            evidence,
            Decimal("1.00"),
            Decimal("1.00"),
        )


class _TrendPolicyScorer:
    expert_id = TREND_EXPERT_ID
    expert_version = TREND_EXPERT_VERSION

    def __init__(self, state: str | None, mapping: dict[str, Any], fallback: bool) -> None:
        self.state = state
        self.mapping = mapping
        self.fallback = fallback

    def evaluate(self, context: ExpertContext) -> ScoringOutput:
        evidence = (_context_ref(context, f"trend-observation:{self.mapping['event_id']}"),)
        if self.fallback or self.state is None or self.state == "abstained":
            reason = "trend_fallback" if self.fallback else "trend_observation_abstained"
            return ScoringOutput(
                self.expert_id,
                self.expert_version,
                ExpertApplicability.ABSTAINED,
                reason,
                reason,
                evidence,
                None,
                None,
            )
        effects = self.mapping["state_effects"]
        if self.state not in effects or self.state not in {"fragile", "neutral", "stable"}:
            raise ValueError("trend observation state is not admitted")
        effect = effects[self.state]
        return ScoringOutput(
            self.expert_id,
            self.expert_version,
            ExpertApplicability.APPLICABLE,
            "trend_observation_available",
            f"trend_{self.state}",
            evidence,
            Decimal(str(effect["signal"])),
            Decimal(str(effect["confidence"])),
        )


@dataclass(frozen=True)
class FormalStrategyDecision:
    event_id: str
    intervention_id: str | None
    topology_digest: str
    trend_state: str | None
    governance: GovernanceResult
    action: ScoreActionResult
    target_maximum_position: Decimal | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "quanthus.formal-strategy-decision.v1",
            "event_id": self.event_id,
            "intervention_id": self.intervention_id,
            "topology_digest": self.topology_digest,
            "trend_state": self.trend_state,
            "governance": self.governance.to_dict(),
            "action": self.action.to_dict(),
            "target_maximum_position": (
                None if self.target_maximum_position is None else str(self.target_maximum_position)
            ),
        }

    @property
    def digest(self) -> str:
        return digest_value(self.to_dict())

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes({**self.to_dict(), "digest": self.digest})


class FormalStrategyRunner:
    """Execute the exact card with no runtime weight or threshold search."""

    def __init__(self, card: ExpertStrategyCard, topology_digest: str) -> None:
        card.require_implementation_authority()
        self.card = card
        self.topology_digest = topology_digest
        self.position_policy = PositionRiskPolicy.from_card(card)
        trend_mapping = card.experts[0]["policy_mapping"]
        self.trend_mapping = {
            "mapping_digest": trend_mapping["mapping_digest"],
            "state_effects": trend_mapping["state_effects"],
        }
        aggregation = card.semantic_sections["aggregation"]
        position = card.semantic_sections["position_and_execution"]
        self.score_policy = ScoreActionPolicy(
            policy_version=f"{aggregation['policy_id']}@{aggregation['policy_version']}",
            minimum_entry_score=Decimal(str(aggregation["minimum_entry_score"])),
        )
        self.base_maximum_position = Decimal(str(position["base_trial_position_fraction"]))
        self.intervention_ids = {str(item["intervention_id"]) for item in card.interventions}

    def _intervention_flags(self, intervention_id: str | None) -> tuple[bool, bool]:
        if intervention_id is not None and intervention_id not in self.intervention_ids:
            raise ValueError("intervention is not admitted by the formal strategy")
        return (
            intervention_id
            in {
                "intervention.trend-stability-abstain",
                "intervention.both-experts-fallback",
            },
            intervention_id
            in {
                "intervention.position-risk-base-policy",
                "intervention.both-experts-fallback",
            },
        )

    def _map_action(
        self,
        context: ExpertContext,
        governance: GovernanceResult,
        scores: tuple[ScoringOutput, ...],
    ) -> ScoreActionResult:
        if any(item.applicability is ExpertApplicability.APPLICABLE for item in scores):
            return aggregate_score_and_map_action(context, governance, scores, self.score_policy)
        required = governance.required_action
        if required is RequiredAction.FORCE_LIQUIDATE:
            final = GovernedAction.FULL_EXIT
            reasons = ("force_liquidate_veto",)
        elif required is RequiredAction.REDUCE_ONLY:
            final = GovernedAction.REDUCE
            reasons = ("reduce_only_veto",)
        elif required is RequiredAction.BLOCK_NEW and context.position_state == "empty":
            final = GovernedAction.NO_ACTION
            reasons = ("all_scoring_abstained_block_new",)
        elif context.position_state != "empty":
            final = GovernedAction.NO_ACTION
            reasons = ("base_position_management_only",)
        else:
            raise ValueError("all-scoring abstention must fail closed for entry")
        return ScoreActionResult(
            security_code=context.security_code,
            snapshot_id=context.snapshot_id,
            knowledge_cutoff=context.knowledge_cutoff,
            position_state=context.position_state,
            governance_digest=governance.digest,
            action_policy_version=self.score_policy.policy_version,
            action_policy_digest=self.score_policy.digest,
            scoring_outputs=scores,
            aggregate_score=Decimal("0"),
            proposed_action=GovernedAction.NO_ACTION,
            final_action=final,
            reason_codes=reasons,
        )

    def _trend_score(
        self,
        context: ExpertContext,
        event_id: str,
        trend_state: str | None,
        fallback: bool,
    ) -> ScoringOutput:
        mapping = {**self.trend_mapping, "event_id": event_id}
        return run_scoring_expert(_TrendPolicyScorer(trend_state, mapping, fallback), context)

    @staticmethod
    def _candidate_control_score(
        context: ExpertContext,
        event_id: str,
        *,
        evidence_ready: bool,
    ) -> ScoringOutput:
        return run_scoring_expert(
            _CandidatePassThroughScorer(event_id, evidence_ready=evidence_ready),
            context,
        )

    def evaluate_entry_control(
        self,
        *,
        event_id: str,
        context: ExpertContext,
        entry_evidence_ready: bool,
        position_risk_enabled: bool,
    ) -> FormalStrategyDecision:
        """Run the frozen P3-STRAT-003 non-empty research control.

        This is additive research-only behavior. It never alters the accepted Trend mapping and it
        keeps the existing Position Risk implementation, aggregator, system guardian and base
        position constraint unchanged.
        """

        score = self._candidate_control_score(
            context,
            event_id,
            evidence_ready=entry_evidence_ready,
        )
        position = self.position_policy.evaluate_entry(
            context,
            fallback=not position_risk_enabled,
        )
        risk_values_ready = all(item.value is not None for item in context.feature_facts)
        scoring_ready = score.applicability is ExpertApplicability.APPLICABLE
        system_ready = entry_evidence_ready and risk_values_ready and scoring_ready
        system = run_veto_expert(
            _SystemGuardian(
                allow=system_ready,
                required_action=(RequiredAction.NONE if system_ready else RequiredAction.BLOCK_NEW),
                reason_code=("entry_guardians_clear" if system_ready else "entry_guardian_blocked"),
                evidence_id=event_id,
            ),
            context,
        )
        base = run_constraint_expert(
            _BasePositionConstraint(self.base_maximum_position),
            context,
        )
        governance = aggregate_governance(
            context,
            (system, position.veto),
            (base, position.constraint),
        )
        action = self._map_action(context, governance, (score,))
        target = (
            None
            if governance.merged_constraints is None
            else governance.merged_constraints.maximum_position
        )
        return FormalStrategyDecision(
            event_id,
            (
                "research.control.p4-candidate-pass-through+risk"
                if position_risk_enabled
                else "research.control.p4-candidate-pass-through"
            ),
            self.topology_digest,
            None,
            governance,
            action,
            target,
        )

    def evaluate_entry(
        self,
        *,
        event_id: str,
        context: ExpertContext,
        trend_state: str | None,
        entry_evidence_ready: bool,
        intervention_id: str | None = None,
    ) -> FormalStrategyDecision:
        trend_fallback, position_fallback = self._intervention_flags(intervention_id)
        score = self._trend_score(context, event_id, trend_state, trend_fallback)
        position = self.position_policy.evaluate_entry(context, fallback=position_fallback)
        risk_values_ready = all(item.value is not None for item in context.feature_facts)
        scoring_ready = score.applicability is ExpertApplicability.APPLICABLE
        system_ready = entry_evidence_ready and risk_values_ready and scoring_ready
        system = run_veto_expert(
            _SystemGuardian(
                allow=system_ready,
                required_action=(RequiredAction.NONE if system_ready else RequiredAction.BLOCK_NEW),
                reason_code=("entry_guardians_clear" if system_ready else "entry_guardian_blocked"),
                evidence_id=event_id,
            ),
            context,
        )
        base = run_constraint_expert(_BasePositionConstraint(self.base_maximum_position), context)
        governance = aggregate_governance(
            context,
            (system, position.veto),
            (base, position.constraint),
        )
        action = self._map_action(context, governance, (score,))
        target = (
            None
            if governance.merged_constraints is None
            else governance.merged_constraints.maximum_position
        )
        return FormalStrategyDecision(
            event_id,
            intervention_id,
            self.topology_digest,
            trend_state,
            governance,
            action,
            target,
        )

    def evaluate_held(
        self,
        *,
        event_id: str,
        context: ExpertContext,
        position_facts: HeldPositionFacts,
        base_required_action: RequiredAction = RequiredAction.NONE,
        base_reason_code: str = "base_position_management_clear",
        intervention_id: str | None = None,
    ) -> FormalStrategyDecision:
        _trend_fallback, position_fallback = self._intervention_flags(intervention_id)
        score = self._trend_score(context, event_id, None, True)
        position = self.position_policy.evaluate_held(
            context, position_facts, fallback=position_fallback
        )
        system = run_veto_expert(
            _SystemGuardian(
                allow=base_required_action is RequiredAction.NONE,
                required_action=base_required_action,
                reason_code=base_reason_code,
                evidence_id=position_facts.evidence.evidence_id,
            ),
            context,
        )
        base = run_constraint_expert(_BasePositionConstraint(self.base_maximum_position), context)
        governance = aggregate_governance(context, (system, position.veto), (base,))
        action = self._map_action(context, governance, (score,))
        return FormalStrategyDecision(
            event_id,
            intervention_id,
            self.topology_digest,
            None,
            governance,
            action,
            position.target_maximum_position,
        )


def position_facts_evidence(
    *,
    unit_id: str,
    holding_sessions: int,
    marked_return: Decimal | None,
    prior_strategy_reduction_count: int,
) -> ExpertEvidenceRef:
    payload = {
        "unit_id": unit_id,
        "holding_sessions": holding_sessions,
        "marked_return": None if marked_return is None else str(marked_return),
        "prior_strategy_reduction_count": prior_strategy_reduction_count,
    }
    return ExpertEvidenceRef(
        EvidenceKind.SNAPSHOT,
        f"position-facts:{unit_id}",
        "1.0.0",
        digest_value(payload),
    )

import hashlib
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

from quanthus.aggregator import (
    AggregationError,
    GovernedAction,
    ScoreActionPolicy,
    aggregate_governance,
    aggregate_score_and_map_action,
)
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
)
from quanthus.label_registry import (
    FeatureEvidenceRef,
    LabelDimension,
    LabelEvidence,
    LabelResult,
    LabelResultStatus,
)


def digest(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def context() -> ExpertContext:
    cutoff = datetime(2025, 1, 2, 8, 0, tzinfo=timezone.utc)
    label = LabelResult(
        label_id="stage.close_strength",
        label_version="1.0.0",
        card_digest=digest("card"),
        dimension=LabelDimension.PRICE_VOLUME_ACCEPTANCE,
        security_code="600000",
        snapshot_id="snapshot-2025-01-02-v1",
        knowledge_cutoff=cutoff,
        status=LabelResultStatus.APPLIED,
        value="accepted",
        reason_code="matched",
        evidence=(LabelEvidence("comparison", "0.80>=0.70"),),
        feature_evidence=(FeatureEvidenceRef("bar.close_location", "1.0.0", digest("lineage")),),
        evaluator_id="synthetic.close_strength",
        evaluator_version="1.0.0",
    )
    return ExpertContext(
        security_code="600000",
        snapshot_id="snapshot-2025-01-02-v1",
        knowledge_cutoff=cutoff,
        position_state="empty",
        feature_facts=(),
        label_results=(label,),
    )


def veto(
    expert_id: str,
    action: RequiredAction | None,
    *,
    authority: VetoAuthority = VetoAuthority.SYSTEM,
    applicability: ExpertApplicability = ExpertApplicability.APPLICABLE,
) -> VetoOutput:
    allowed = None if action is None else action is RequiredAction.NONE
    return VetoOutput(
        expert_id=expert_id,
        expert_version="1.0.0",
        applicability=applicability,
        applicability_reason=(
            "label_available"
            if applicability is ExpertApplicability.APPLICABLE
            else "missing_context"
        ),
        reason_code=("allowed" if action is RequiredAction.NONE else "governance_action"),
        evidence=(context().evidence_refs[0],),
        authority=authority,
        allow=allowed,
        required_action=action,
    )


def constraint(
    expert_id: str,
    minimum: str | None,
    maximum: str | None,
    *,
    applicability: ExpertApplicability = ExpertApplicability.APPLICABLE,
) -> ConstraintOutput:
    active = applicability is ExpertApplicability.APPLICABLE
    return ConstraintOutput(
        expert_id=expert_id,
        expert_version="1.0.0",
        applicability=applicability,
        applicability_reason="label_available" if active else "missing_context",
        reason_code="bounded" if active else "not_applicable",
        evidence=(context().evidence_refs[0],),
        minimum_position=Decimal(minimum) if minimum is not None else None,
        maximum_position=Decimal(maximum) if maximum is not None else None,
        stop_rule="stop_rule" if active else None,
        hold_rule="hold_rule" if active else None,
        scope=ConstraintScope.UNIT if active else None,
    )


def score(expert_id: str, signal: str, confidence: str) -> ScoringOutput:
    return ScoringOutput(
        expert_id=expert_id,
        expert_version="1.0.0",
        applicability=ExpertApplicability.APPLICABLE,
        applicability_reason="label_available",
        reason_code="scored",
        evidence=(context().evidence_refs[0],),
        signal=Decimal(signal),
        confidence=Decimal(confidence),
    )


class AggregatorTests(unittest.TestCase):
    def test_strictest_veto_wins_before_constraint_processing(self) -> None:
        vetoes = (
            veto("veto.system_block", RequiredAction.BLOCK_NEW),
            veto(
                "veto.strategy_force",
                RequiredAction.FORCE_LIQUIDATE,
                authority=VetoAuthority.STRATEGY,
            ),
            veto("veto.system_reduce", RequiredAction.REDUCE_ONLY),
        )
        conflicting = (
            constraint("constraint.lower", "0.80", "0.90"),
            constraint("constraint.upper", "0.10", "0.20"),
        )

        result = aggregate_governance(context(), vetoes, conflicting)

        self.assertEqual(result.required_action, RequiredAction.FORCE_LIQUIDATE)
        self.assertEqual(result.terminating_veto_ids, ("veto.strategy_force",))
        self.assertIsNone(result.merged_constraints)

    def test_no_veto_intersects_all_applicable_constraints(self) -> None:
        result = aggregate_governance(
            context(),
            (veto("veto.system", RequiredAction.NONE),),
            (
                constraint("constraint.market", "0.00", "0.60"),
                constraint("constraint.extension", "0.20", "0.40"),
            ),
        )

        self.assertEqual(result.required_action, RequiredAction.NONE)
        self.assertIsNotNone(result.merged_constraints)
        assert result.merged_constraints is not None
        self.assertEqual(result.merged_constraints.minimum_position, Decimal("0.20"))
        self.assertEqual(result.merged_constraints.maximum_position, Decimal("0.40"))
        self.assertEqual(len(result.merged_constraints.stop_rules), 2)
        self.assertEqual(len(result.merged_constraints.hold_rules), 2)

    def test_constraint_intersection_conflict_fails_closed(self) -> None:
        with self.assertRaises(AggregationError) as raised:
            aggregate_governance(
                context(),
                (veto("veto.system", RequiredAction.NONE),),
                (
                    constraint("constraint.lower", "0.70", "0.90"),
                    constraint("constraint.upper", "0.10", "0.30"),
                ),
            )

        self.assertEqual(raised.exception.reason_code, "constraint_conflict")

    def test_abstained_constraint_does_not_become_an_implicit_default(self) -> None:
        abstained = constraint(
            "constraint.abstained",
            None,
            None,
            applicability=ExpertApplicability.ABSTAINED,
        )

        with self.assertRaises(AggregationError) as raised:
            aggregate_governance(
                context(),
                (veto("veto.system", RequiredAction.NONE),),
                (abstained,),
            )

        self.assertEqual(raised.exception.reason_code, "no_applicable_constraints")

    def test_system_veto_output_is_mandatory_even_when_strategy_allows(self) -> None:
        strategy = veto(
            "veto.strategy",
            RequiredAction.NONE,
            authority=VetoAuthority.STRATEGY,
        )

        with self.assertRaises(AggregationError) as raised:
            aggregate_governance(
                context(),
                (strategy,),
                (constraint("constraint.market", "0", "1"),),
            )

        self.assertEqual(raised.exception.reason_code, "missing_system_veto")

    def test_abstained_system_veto_cannot_be_treated_as_allow(self) -> None:
        system_abstained = veto(
            "veto.system",
            None,
            applicability=ExpertApplicability.ABSTAINED,
        )
        strategy = veto(
            "veto.strategy",
            RequiredAction.NONE,
            authority=VetoAuthority.STRATEGY,
        )

        with self.assertRaises(AggregationError) as raised:
            aggregate_governance(
                context(),
                (system_abstained, strategy),
                (constraint("constraint.market", "0", "1"),),
            )

        self.assertEqual(raised.exception.reason_code, "system_veto_unavailable")

    def test_duplicate_output_from_one_expert_is_rejected(self) -> None:
        system = veto("veto.system", RequiredAction.NONE)

        with self.assertRaises(AggregationError) as raised:
            aggregate_governance(
                context(),
                (system, system),
                (constraint("constraint.market", "0", "1"),),
            )

        self.assertEqual(raised.exception.reason_code, "duplicate_expert_output")

    def test_evidence_outside_aggregation_context_is_rejected(self) -> None:
        forged = ExpertEvidenceRef(
            EvidenceKind.LABEL,
            "stage.hidden",
            "1.0.0",
            digest("hidden"),
        )
        system = veto("veto.system", RequiredAction.NONE)
        system = replace(system, evidence=(forged,))

        with self.assertRaises(AggregationError) as raised:
            aggregate_governance(
                context(),
                (system,),
                (constraint("constraint.market", "0", "1"),),
            )

        self.assertEqual(raised.exception.reason_code, "context_evidence_mismatch")

    def test_input_permutation_cannot_change_governance_result(self) -> None:
        vetoes = (
            veto("veto.system_b", RequiredAction.NONE),
            veto(
                "veto.strategy_a",
                RequiredAction.NONE,
                authority=VetoAuthority.STRATEGY,
            ),
        )
        constraints = (
            constraint("constraint.b", "0.10", "0.50"),
            constraint("constraint.a", "0.20", "0.40"),
        )

        first = aggregate_governance(context(), vetoes, constraints)
        second = aggregate_governance(
            context(), tuple(reversed(vetoes)), tuple(reversed(constraints))
        )

        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.digest, second.digest)

    def test_score_mapping_is_confidence_adjusted_and_veto_cannot_be_bypassed(self) -> None:
        current = context()
        policy = ScoreActionPolicy("candidate-action-v1", Decimal("0.35"))
        scoring = (score("score.trend", "0.80", "0.75"), score("score.risk", "0.60", "0.50"))
        allowed = aggregate_governance(
            current,
            (veto("veto.system", RequiredAction.NONE),),
            (constraint("constraint.position", "0", "0.30"),),
        )
        accepted = aggregate_score_and_map_action(current, allowed, scoring, policy)

        self.assertEqual(accepted.aggregate_score, Decimal("0.45"))
        self.assertEqual(accepted.final_action, GovernedAction.TRIAL_ENTRY)

        blocked = aggregate_governance(
            current,
            (veto("veto.system", RequiredAction.BLOCK_NEW),),
            (constraint("constraint.position", "0", "0.30"),),
        )
        rejected = aggregate_score_and_map_action(current, blocked, scoring, policy)
        self.assertEqual(rejected.aggregate_score, Decimal("0.45"))
        self.assertEqual(rejected.proposed_action, GovernedAction.TRIAL_ENTRY)
        self.assertEqual(rejected.final_action, GovernedAction.NO_ACTION)

    def test_score_mapping_is_input_order_independent(self) -> None:
        current = context()
        governance = aggregate_governance(
            current,
            (veto("veto.system", RequiredAction.NONE),),
            (constraint("constraint.position", "0", "0.30"),),
        )
        policy = ScoreActionPolicy("candidate-action-v1", Decimal("0.35"))
        outputs = (score("score.trend", "0.90", "0.90"), score("score.risk", "0.30", "0.50"))

        first = aggregate_score_and_map_action(current, governance, outputs, policy)
        second = aggregate_score_and_map_action(
            current, governance, tuple(reversed(outputs)), policy
        )
        self.assertEqual(first.to_dict(), second.to_dict())


if __name__ == "__main__":
    unittest.main()

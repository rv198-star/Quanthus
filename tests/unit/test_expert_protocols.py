import hashlib
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from quanthus.expert_protocols import (
    ConstraintOutput,
    ConstraintScope,
    EvidenceKind,
    ExpertApplicability,
    ExpertContext,
    ExpertEvidenceRef,
    ExpertProtocolError,
    RequiredAction,
    ScoringOutput,
    VetoAuthority,
    VetoOutput,
    run_constraint_expert,
    run_scoring_expert,
    run_veto_expert,
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


def label_result(cutoff: datetime) -> LabelResult:
    return LabelResult(
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


class ScoringFixture:
    def __init__(self, output: ScoringOutput | None = None) -> None:
        self.output = output

    @property
    def expert_id(self) -> str:
        return "expert.synthetic_score"

    @property
    def expert_version(self) -> str:
        return "1.0.0"

    def evaluate(self, context: ExpertContext) -> ScoringOutput:
        if self.output is not None:
            return self.output
        return ScoringOutput(
            self.expert_id,
            self.expert_version,
            ExpertApplicability.APPLICABLE,
            "label_available",
            "positive_evidence",
            (context.evidence_refs[0],),
            Decimal("0.60"),
            Decimal("0.80"),
        )


class ConstraintFixture:
    @property
    def expert_id(self) -> str:
        return "expert.synthetic_constraint"

    @property
    def expert_version(self) -> str:
        return "1.0.0"

    def evaluate(self, context: ExpertContext) -> ConstraintOutput:
        return ConstraintOutput(
            self.expert_id,
            self.expert_version,
            ExpertApplicability.APPLICABLE,
            "label_available",
            "bounded_position",
            (context.evidence_refs[0],),
            Decimal("0.10"),
            Decimal("0.30"),
            "close_below_structural_level",
            "hold_while_structure_valid",
            ConstraintScope.UNIT,
        )


class VetoFixture:
    @property
    def expert_id(self) -> str:
        return "expert.synthetic_veto"

    @property
    def expert_version(self) -> str:
        return "1.0.0"

    def evaluate(self, context: ExpertContext) -> VetoOutput:
        return VetoOutput(
            self.expert_id,
            self.expert_version,
            ExpertApplicability.APPLICABLE,
            "label_available",
            "tradability_block",
            (context.evidence_refs[0],),
            VetoAuthority.SYSTEM,
            False,
            RequiredAction.BLOCK_NEW,
        )


class ExpertProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cutoff = datetime(2025, 1, 2, 8, 0, tzinfo=timezone.utc)
        self.label = label_result(self.cutoff)
        self.context = ExpertContext(
            security_code="600000",
            snapshot_id="snapshot-2025-01-02-v1",
            knowledge_cutoff=self.cutoff,
            position_state="empty",
            feature_facts=(),
            label_results=(self.label,),
        )

    def test_context_exposes_only_deterministic_snapshot_and_label_evidence(self) -> None:
        first = self.context.evidence_refs
        second = self.context.evidence_refs

        self.assertEqual(first, second)
        self.assertEqual({item.kind for item in first}, {EvidenceKind.SNAPSHOT, EvidenceKind.LABEL})
        self.assertTrue(all(item.digest.startswith("sha256:") for item in first))

    def test_scoring_expert_returns_bounded_deterministic_opinion(self) -> None:
        first = run_scoring_expert(ScoringFixture(), self.context)
        second = run_scoring_expert(ScoringFixture(), self.context)

        self.assertEqual(first.signal, Decimal("0.60"))
        self.assertEqual(first.confidence, Decimal("0.80"))
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.digest, second.digest)

    def test_scoring_range_and_abstention_are_structurally_distinct(self) -> None:
        evidence = (self.context.evidence_refs[0],)
        with self.assertRaises(ExpertProtocolError) as raised:
            ScoringOutput(
                "expert.synthetic_score",
                "1.0.0",
                ExpertApplicability.APPLICABLE,
                "label_available",
                "out_of_range",
                evidence,
                Decimal("1.01"),
                Decimal("0.80"),
            )
        self.assertEqual(raised.exception.reason_code, "invalid_scoring_output")

        abstained = ScoringOutput(
            "expert.synthetic_score",
            "1.0.0",
            ExpertApplicability.ABSTAINED,
            "missing_context",
            "not_applicable",
            evidence,
            None,
            None,
        )
        self.assertIsNone(abstained.signal)
        with self.assertRaises(ExpertProtocolError) as raised:
            replace(abstained, signal=Decimal("0"), confidence=Decimal("0"))
        self.assertEqual(raised.exception.reason_code, "invalid_abstention")

    def test_constraint_requires_complete_non_conflicting_position_bounds(self) -> None:
        output = run_constraint_expert(ConstraintFixture(), self.context)

        self.assertEqual(output.minimum_position, Decimal("0.10"))
        self.assertEqual(output.maximum_position, Decimal("0.30"))
        self.assertEqual(output.scope, ConstraintScope.UNIT)
        with self.assertRaises(ExpertProtocolError) as raised:
            replace(
                output,
                minimum_position=Decimal("0.40"),
                maximum_position=Decimal("0.30"),
            )
        self.assertEqual(raised.exception.reason_code, "invalid_constraint_output")

    def test_constraint_abstention_cannot_hide_position_rules(self) -> None:
        abstained = ConstraintOutput(
            "expert.synthetic_constraint",
            "1.0.0",
            ExpertApplicability.ABSTAINED,
            "missing_context",
            "not_applicable",
            (self.context.evidence_refs[0],),
            None,
            None,
            None,
            None,
            None,
        )

        with self.assertRaises(ExpertProtocolError) as raised:
            replace(abstained, minimum_position=Decimal("0"))

        self.assertEqual(raised.exception.reason_code, "invalid_abstention")

    def test_veto_allow_flag_and_required_action_cannot_disagree(self) -> None:
        output = run_veto_expert(VetoFixture(), self.context)

        self.assertFalse(output.allow)
        self.assertEqual(output.required_action, RequiredAction.BLOCK_NEW)
        self.assertEqual(output.authority, VetoAuthority.SYSTEM)
        with self.assertRaises(ExpertProtocolError) as raised:
            replace(output, allow=True)
        self.assertEqual(raised.exception.reason_code, "invalid_veto_output")

    def test_veto_abstention_cannot_hide_an_action(self) -> None:
        abstained = VetoOutput(
            "expert.synthetic_veto",
            "1.0.0",
            ExpertApplicability.ABSTAINED,
            "missing_context",
            "not_applicable",
            (self.context.evidence_refs[0],),
            VetoAuthority.STRATEGY,
            None,
            None,
        )

        with self.assertRaises(ExpertProtocolError) as raised:
            replace(abstained, allow=False, required_action=RequiredAction.BLOCK_NEW)

        self.assertEqual(raised.exception.reason_code, "invalid_abstention")

    def test_context_rejects_label_from_another_snapshot(self) -> None:
        late = replace(
            self.label,
            knowledge_cutoff=self.cutoff + timedelta(seconds=1),
        )

        with self.assertRaises(ExpertProtocolError) as raised:
            replace(self.context, label_results=(late,))

        self.assertEqual(raised.exception.reason_code, "context_snapshot_mismatch")

    def test_runner_rejects_output_identity_different_from_implementation(self) -> None:
        forged = ScoringOutput(
            "expert.other_score",
            "1.0.0",
            ExpertApplicability.APPLICABLE,
            "label_available",
            "positive_evidence",
            (self.context.evidence_refs[0],),
            Decimal("0.60"),
            Decimal("0.80"),
        )

        with self.assertRaises(ExpertProtocolError) as raised:
            run_scoring_expert(ScoringFixture(forged), self.context)

        self.assertEqual(raised.exception.reason_code, "expert_identity_mismatch")

    def test_runner_rejects_evidence_outside_the_context(self) -> None:
        forged_evidence = ExpertEvidenceRef(
            EvidenceKind.LABEL,
            "stage.hidden",
            "1.0.0",
            digest("hidden"),
        )
        forged = ScoringOutput(
            "expert.synthetic_score",
            "1.0.0",
            ExpertApplicability.APPLICABLE,
            "label_available",
            "positive_evidence",
            (forged_evidence,),
            Decimal("0.60"),
            Decimal("0.80"),
        )

        with self.assertRaises(ExpertProtocolError) as raised:
            run_scoring_expert(ScoringFixture(forged), self.context)

        self.assertEqual(raised.exception.reason_code, "unknown_expert_evidence")


if __name__ == "__main__":
    unittest.main()

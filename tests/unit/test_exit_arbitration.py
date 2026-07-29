import copy
import hashlib
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from quanthus.exit_arbitration import (
    ExitArbitrationError,
    ExitDecisionStatus,
    ExitEvidenceKind,
    ExitEvidenceRef,
    ExitTriggerEvaluation,
    ExitTriggerSource,
    ExitTriggerStatus,
    arbitrate_exit,
)
from quanthus.policy import P0Policy, load_policy
from quanthus.position_lifecycle import PositionStage

CUTOFF = datetime(2025, 1, 2, 8, 0, tzinfo=timezone.utc)

EVIDENCE_KIND = {
    ExitTriggerSource.STRUCTURAL_BREAK: ExitEvidenceKind.GOVERNANCE,
    ExitTriggerSource.CONSTRAINT_STOP: ExitEvidenceKind.CONSTRAINT,
    ExitTriggerSource.CONSECUTIVE_DETERIORATION: ExitEvidenceKind.REVIEW,
    ExitTriggerSource.MAXIMUM_HOLDING_PERIOD: ExitEvidenceKind.CALENDAR,
}


def digest(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def evaluation(
    source: ExitTriggerSource,
    *,
    status: ExitTriggerStatus = ExitTriggerStatus.CLEAR,
    reason_code: str | None = None,
    evidence_kind: ExitEvidenceKind | None = None,
) -> ExitTriggerEvaluation:
    kind = evidence_kind or EVIDENCE_KIND[source]
    return ExitTriggerEvaluation(
        source=source,
        status=status,
        unit_id="unit-001",
        opportunity_id="opportunity-001",
        security_code="600000",
        snapshot_id="snapshot-001",
        knowledge_cutoff=CUTOFF,
        lifecycle_stage=PositionStage.DYNAMIC,
        evaluator_version="1.0.0",
        reason_code=reason_code or f"{source.value}_{status.value}",
        evidence=(
            ExitEvidenceRef(kind, f"evidence-{source.value}", "1.0.0", digest(source.value)),
        ),
    )


def complete_evaluations(
    overrides: dict[ExitTriggerSource, ExitTriggerEvaluation] | None = None,
) -> tuple[ExitTriggerEvaluation, ...]:
    values = {source: evaluation(source) for source in ExitTriggerSource}
    values.update(overrides or {})
    return tuple(values.values())


class ExitArbitrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_policy()

    def test_all_clear_sources_hold_without_alert_or_execution(self) -> None:
        result = arbitrate_exit(self.policy, complete_evaluations())

        self.assertEqual(result.status, ExitDecisionStatus.HOLD)
        self.assertFalse(result.exit_requested)
        self.assertFalse(result.alert_required)
        self.assertFalse(result.execution_authorized)
        self.assertEqual(result.reason_codes, ("hold_no_exit_trigger",))

    def test_each_frozen_source_can_request_exit(self) -> None:
        for source in ExitTriggerSource:
            with self.subTest(source=source.value):
                triggered = evaluation(source, status=ExitTriggerStatus.TRIGGERED)
                result = arbitrate_exit(
                    self.policy,
                    complete_evaluations({source: triggered}),
                )
                self.assertTrue(result.exit_requested)
                self.assertEqual(result.triggered_sources, (source,))
                self.assertEqual(result.reason_codes, (triggered.reason_code,))

    def test_multiple_triggers_retain_frozen_policy_order(self) -> None:
        overrides = {
            ExitTriggerSource.MAXIMUM_HOLDING_PERIOD: evaluation(
                ExitTriggerSource.MAXIMUM_HOLDING_PERIOD,
                status=ExitTriggerStatus.TRIGGERED,
            ),
            ExitTriggerSource.STRUCTURAL_BREAK: evaluation(
                ExitTriggerSource.STRUCTURAL_BREAK,
                status=ExitTriggerStatus.TRIGGERED,
            ),
        }

        result = arbitrate_exit(self.policy, complete_evaluations(overrides))

        self.assertEqual(
            result.triggered_sources,
            (
                ExitTriggerSource.STRUCTURAL_BREAK,
                ExitTriggerSource.MAXIMUM_HOLDING_PERIOD,
            ),
        )

    def test_missing_data_is_unavailable_hold_with_alert_not_structural_exit(self) -> None:
        unavailable = evaluation(
            ExitTriggerSource.STRUCTURAL_BREAK,
            status=ExitTriggerStatus.UNAVAILABLE,
            reason_code="missing_structure_data",
            evidence_kind=ExitEvidenceKind.MISSING_DATA,
        )

        result = arbitrate_exit(
            self.policy,
            complete_evaluations({ExitTriggerSource.STRUCTURAL_BREAK: unavailable}),
        )

        self.assertEqual(result.status, ExitDecisionStatus.HOLD_WITH_UNAVAILABLE_EVIDENCE)
        self.assertFalse(result.exit_requested)
        self.assertTrue(result.alert_required)
        self.assertEqual(result.unavailable_sources, (ExitTriggerSource.STRUCTURAL_BREAK,))

    def test_missing_data_cannot_be_a_triggered_structural_break(self) -> None:
        triggered = evaluation(
            ExitTriggerSource.STRUCTURAL_BREAK,
            status=ExitTriggerStatus.TRIGGERED,
        )
        missing = ExitEvidenceRef(
            ExitEvidenceKind.MISSING_DATA,
            "evidence-missing-structure",
            "1.0.0",
            digest("missing-structure"),
        )
        with self.assertRaises(ExitArbitrationError) as raised:
            replace(triggered, evidence=(*triggered.evidence, missing))

        self.assertEqual(raised.exception.reason_code, "missing_data_structural_break")

    def test_wrong_evidence_kind_fails_closed_for_available_source(self) -> None:
        with self.assertRaises(ExitArbitrationError) as raised:
            evaluation(
                ExitTriggerSource.CONSTRAINT_STOP,
                evidence_kind=ExitEvidenceKind.REVIEW,
            )

        self.assertEqual(raised.exception.reason_code, "invalid_exit_evidence")

    def test_missing_duplicate_and_cross_context_sources_fail_closed(self) -> None:
        complete = complete_evaluations()
        with self.assertRaises(ExitArbitrationError) as missing:
            arbitrate_exit(self.policy, complete[:-1])
        self.assertEqual(missing.exception.reason_code, "missing_exit_source")

        with self.assertRaises(ExitArbitrationError) as duplicate:
            arbitrate_exit(self.policy, (*complete, complete[0]))
        self.assertEqual(duplicate.exception.reason_code, "duplicate_exit_source")

        changed = replace(complete[-1], snapshot_id="snapshot-002")
        with self.assertRaises(ExitArbitrationError) as context:
            arbitrate_exit(self.policy, (*complete[:-1], changed))
        self.assertEqual(context.exception.reason_code, "exit_context_mismatch")

    def test_only_trial_or_dynamic_positions_can_be_arbitrated(self) -> None:
        with self.assertRaises(ExitArbitrationError) as raised:
            replace(
                evaluation(ExitTriggerSource.STRUCTURAL_BREAK),
                lifecycle_stage=PositionStage.EXITING,
            )

        self.assertEqual(raised.exception.reason_code, "invalid_exit_lifecycle")

    def test_input_permutation_cannot_change_result_or_digest(self) -> None:
        evaluations = complete_evaluations(
            {
                ExitTriggerSource.CONSTRAINT_STOP: evaluation(
                    ExitTriggerSource.CONSTRAINT_STOP,
                    status=ExitTriggerStatus.TRIGGERED,
                )
            }
        )

        first = arbitrate_exit(self.policy, evaluations)
        second = arbitrate_exit(self.policy, tuple(reversed(evaluations)))

        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.digest, second.digest)

    def test_policy_must_contain_each_frozen_source_once(self) -> None:
        raw = copy.deepcopy(dict(self.policy.raw))
        raw["risk"]["exit_triggers"] = [
            "structural_break",
            "constraint_stop",
            "consecutive_deterioration",
        ]
        invalid = P0Policy(Path("invalid"), raw)

        with self.assertRaises(ExitArbitrationError) as raised:
            arbitrate_exit(invalid, complete_evaluations())

        self.assertEqual(raised.exception.reason_code, "invalid_exit_policy")


if __name__ == "__main__":
    unittest.main()

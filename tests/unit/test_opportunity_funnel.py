import unittest
from dataclasses import replace
from datetime import date

from quanthus.opportunity_funnel import (
    FunnelDisposition,
    FunnelObservation,
    FunnelOutcomeKind,
    FunnelStage,
    OpportunityFunnelError,
    build_opportunity_funnel,
)

REPORT_DATE = date(2026, 7, 28)


def observation(
    stage: FunnelStage,
    opportunity_id: str,
    input_event_id: str,
    disposition: FunnelDisposition,
    *,
    output_event_id: str | None = None,
    outcome_kind: FunnelOutcomeKind = FunnelOutcomeKind.PENDING,
    outcome_ref: str | None = None,
) -> FunnelObservation:
    return FunnelObservation(
        market_date=REPORT_DATE,
        stage=stage,
        opportunity_id=opportunity_id,
        input_event_id=input_event_id,
        disposition=disposition,
        reason_codes=(f"{stage.value}_{disposition.value}",),
        output_event_id=output_event_id,
        decision_id=f"decision-{input_event_id}",
        outcome_kind=outcome_kind,
        outcome_ref=outcome_ref,
    )


def complete_observations() -> tuple[FunnelObservation, ...]:
    return (
        observation(
            FunnelStage.CANDIDATE_TO_SCORE,
            "opportunity-1",
            "candidate-1",
            FunnelDisposition.ADVANCED,
            output_event_id="score-1",
        ),
        observation(
            FunnelStage.CANDIDATE_TO_SCORE,
            "opportunity-2",
            "candidate-2",
            FunnelDisposition.STOPPED,
            outcome_kind=FunnelOutcomeKind.DIAGNOSTIC,
            outcome_ref="hypothetical-2",
        ),
        observation(
            FunnelStage.SCORE_TO_VETO,
            "opportunity-1",
            "score-1",
            FunnelDisposition.ADVANCED,
            output_event_id="entry-1",
        ),
        observation(
            FunnelStage.ENTRY_TO_ADD,
            "opportunity-1",
            "entry-1",
            FunnelDisposition.ADVANCED,
            output_event_id="hold-1",
        ),
        observation(
            FunnelStage.HOLD_TO_EXIT,
            "opportunity-1",
            "hold-1",
            FunnelDisposition.STOPPED,
            outcome_kind=FunnelOutcomeKind.REALIZED,
            outcome_ref="trade-1",
        ),
    )


def expected_inputs() -> dict[FunnelStage, tuple[str, ...]]:
    return {
        FunnelStage.CANDIDATE_TO_SCORE: ("candidate-1", "candidate-2"),
        FunnelStage.SCORE_TO_VETO: ("score-1",),
        FunnelStage.ENTRY_TO_ADD: ("entry-1",),
        FunnelStage.HOLD_TO_EXIT: ("hold-1",),
    }


class OpportunityFunnelTests(unittest.TestCase):
    def build(
        self,
        observations: tuple[FunnelObservation, ...],
        expected: dict[FunnelStage, tuple[str, ...]] | None = None,
    ):
        return build_opportunity_funnel(
            run_id="p3-funnel-001",
            period_start=REPORT_DATE,
            period_end=REPORT_DATE,
            observations=observations,
            expected_input_event_ids=expected or expected_inputs(),
        )

    def test_fixed_stages_reconcile_dispositions_and_outcomes(self) -> None:
        report = self.build(complete_observations())

        self.assertTrue(report.reconciled)
        self.assertFalse(report.execution_authorized)
        self.assertEqual(report.source_event_count, 5)
        self.assertEqual([summary.stage for summary in report.stage_summaries], list(FunnelStage))
        candidate = report.stage_summaries[0]
        self.assertEqual(candidate.input_count, 2)
        self.assertEqual(candidate.advanced_count, 1)
        self.assertEqual(candidate.stopped_count, 1)
        self.assertEqual(candidate.diagnostic_count, 1)
        self.assertEqual(report.stage_summaries[-1].realized_count, 1)

    def test_input_permutation_cannot_change_report_identity(self) -> None:
        records = complete_observations()
        expected = expected_inputs()
        reversed_expected = {
            stage: tuple(reversed(values)) for stage, values in reversed(tuple(expected.items()))
        }

        first = self.build(records, expected)
        second = self.build(tuple(reversed(records)), reversed_expected)

        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.digest, second.digest)
        self.assertEqual(first.canonical_bytes(), second.canonical_bytes())

    def test_missing_or_extra_source_record_fails_reconciliation(self) -> None:
        with self.assertRaises(OpportunityFunnelError) as raised:
            self.build(complete_observations()[:-1])

        self.assertEqual(raised.exception.reason_code, "source_event_mismatch")

    def test_duplicate_stage_input_is_rejected(self) -> None:
        records = complete_observations()
        with self.assertRaises(OpportunityFunnelError) as raised:
            self.build(records + (records[0],))

        self.assertEqual(raised.exception.reason_code, "duplicate_observation")

    def test_transition_and_outcome_shapes_fail_closed(self) -> None:
        with self.assertRaises(OpportunityFunnelError) as raised:
            replace(
                complete_observations()[0],
                disposition=FunnelDisposition.STOPPED,
            )
        self.assertEqual(raised.exception.reason_code, "invalid_transition")

        with self.assertRaises(OpportunityFunnelError) as raised:
            replace(
                complete_observations()[0],
                outcome_kind=FunnelOutcomeKind.REALIZED,
            )
        self.assertEqual(raised.exception.reason_code, "invalid_outcome")

    def test_linked_stage_must_preserve_opportunity_identity(self) -> None:
        records = list(complete_observations())
        records[2] = replace(records[2], opportunity_id="opportunity-forged")

        with self.assertRaises(OpportunityFunnelError) as raised:
            self.build(tuple(records))

        self.assertEqual(raised.exception.reason_code, "opportunity_identity_mismatch")

    def test_duplicate_output_event_is_rejected(self) -> None:
        records = list(complete_observations())
        records[1] = replace(
            records[1],
            disposition=FunnelDisposition.ADVANCED,
            output_event_id="hold-1",
        )

        with self.assertRaises(OpportunityFunnelError) as raised:
            self.build(tuple(records))

        self.assertEqual(raised.exception.reason_code, "duplicate_output_event")

    def test_linked_output_must_enter_the_next_fixed_stage(self) -> None:
        records = list(complete_observations())
        records[1] = replace(
            records[1],
            disposition=FunnelDisposition.ADVANCED,
            output_event_id="hold-2",
        )
        records[4] = replace(
            records[4],
            opportunity_id="opportunity-2",
            input_event_id="hold-2",
        )
        expected = expected_inputs()
        expected[FunnelStage.HOLD_TO_EXIT] = ("hold-2",)

        with self.assertRaises(OpportunityFunnelError) as raised:
            self.build(tuple(records), expected)

        self.assertEqual(raised.exception.reason_code, "invalid_stage_chain")

    def test_all_fixed_stage_expectations_are_required(self) -> None:
        expected = expected_inputs()
        expected.pop(FunnelStage.HOLD_TO_EXIT)

        with self.assertRaises(OpportunityFunnelError) as raised:
            self.build(complete_observations(), expected)

        self.assertEqual(raised.exception.reason_code, "incomplete_stage_set")


if __name__ == "__main__":
    unittest.main()

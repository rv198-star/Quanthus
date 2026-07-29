import unittest
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from quanthus.backtest.contracts import DecisionDisposition, DecisionEvent
from quanthus.contracts import SnapshotRef
from quanthus.shadow_divergence import (
    ShadowDivergenceError,
    build_shadow_divergence_log,
    compare_shadow_decision,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


def decision(
    disposition: DecisionDisposition,
    *,
    decision_id: str,
    aggregate_score: str = "0.60",
    proposed_action: str = "trial_entry",
    final_action: str = "trial_entry",
) -> DecisionEvent:
    accepted = disposition is DecisionDisposition.ACCEPTED
    return DecisionEvent(
        decision_id=decision_id,
        run_id="run-shadow-001",
        signal_id="signal-active-001" if accepted else None,
        snapshot=SnapshotRef(
            market_date=date(2025, 1, 2),
            knowledge_cutoff=datetime(2025, 1, 2, 15, 0, tzinfo=SHANGHAI),
            source_revision="synthetic-shadow-source-v1",
            snapshot_id="snapshot-shadow-001",
        ),
        decided_at=datetime(2025, 1, 2, 15, 5, tzinfo=SHANGHAI),
        profile_id="close-to-next-open-v1",
        window_id=None,
        security_code="600000",
        unit_id="unit-001",
        lifecycle_stage="empty",
        aggregate_score=Decimal(aggregate_score),
        expert_outputs={
            "synthetic.expert": {
                "score": aggregate_score,
                "reason_code": "synthetic_evidence",
            }
        },
        vetoes=(),
        constraints={"maximum_position": "0.20"},
        proposed_action=proposed_action,
        final_action=final_action,
        reason_codes=("synthetic_baseline",),
        policy_versions={"decision": "synthetic-active-v1"},
        linked_order_ids=("order-active-001",) if accepted else (),
        disposition=disposition,
    )


class ShadowDivergenceTests(unittest.TestCase):
    def test_action_and_governance_divergence_is_explicit_and_non_executing(self) -> None:
        baseline = decision(DecisionDisposition.ACCEPTED, decision_id="decision-active")
        shadow = replace(
            decision(
                DecisionDisposition.SHADOW,
                decision_id="decision-shadow",
                aggregate_score="0.20",
                proposed_action="none",
                final_action="none",
            ),
            expert_outputs={"synthetic.expert": {"score": "0.20"}},
            vetoes=("extension_risk",),
            constraints={"maximum_position": "0.00"},
            reason_codes=("shadow_blocked",),
            policy_versions={"decision": "synthetic-shadow-v2"},
        )

        result = compare_shadow_decision(baseline, shadow)

        self.assertTrue(result.output_diverged)
        self.assertTrue(result.proposed_action_changed)
        self.assertTrue(result.final_action_changed)
        self.assertEqual(result.aggregate_score_delta, Decimal("-0.40"))
        self.assertEqual(result.added_vetoes, ("extension_risk",))
        self.assertEqual(result.constraint_changes[0].key, "maximum_position")
        self.assertFalse(result.execution_authorized)
        self.assertFalse(result.to_dict()["execution_authorized"])

    def test_equal_outputs_are_not_divergence_despite_shadow_disposition(self) -> None:
        baseline = decision(DecisionDisposition.REJECTED, decision_id="decision-active")
        shadow = replace(
            decision(DecisionDisposition.SHADOW, decision_id="decision-shadow"),
            expert_outputs=baseline.expert_outputs,
            vetoes=baseline.vetoes,
            constraints=baseline.constraints,
            proposed_action=baseline.proposed_action,
            final_action=baseline.final_action,
            reason_codes=baseline.reason_codes,
            policy_versions=baseline.policy_versions,
        )

        result = compare_shadow_decision(baseline, shadow)

        self.assertFalse(result.output_diverged)
        self.assertEqual(result.aggregate_score_delta, Decimal("0.00"))

    def test_policy_version_change_is_reported_without_becoming_output_divergence(self) -> None:
        baseline = decision(DecisionDisposition.REJECTED, decision_id="decision-active")
        shadow = replace(
            decision(DecisionDisposition.SHADOW, decision_id="decision-shadow"),
            expert_outputs=baseline.expert_outputs,
            constraints=baseline.constraints,
            reason_codes=baseline.reason_codes,
            policy_versions={"decision": "synthetic-shadow-v2"},
        )

        result = compare_shadow_decision(baseline, shadow)

        self.assertFalse(result.output_diverged)
        self.assertEqual(result.policy_version_changes[0].baseline_value, "synthetic-active-v1")
        self.assertEqual(result.policy_version_changes[0].shadow_value, "synthetic-shadow-v2")

    def test_shadow_baseline_and_non_shadow_target_fail_closed(self) -> None:
        shadow = decision(DecisionDisposition.SHADOW, decision_id="decision-shadow")
        active = decision(DecisionDisposition.REJECTED, decision_id="decision-active")

        with self.assertRaises(ShadowDivergenceError) as baseline_error:
            compare_shadow_decision(shadow, shadow)
        self.assertEqual(baseline_error.exception.reason_code, "invalid_baseline")

        with self.assertRaises(ShadowDivergenceError) as target_error:
            compare_shadow_decision(active, active)
        self.assertEqual(target_error.exception.reason_code, "invalid_shadow")

    def test_review_identity_mismatch_fails_closed(self) -> None:
        baseline = decision(DecisionDisposition.REJECTED, decision_id="decision-active")
        shadow = replace(
            decision(DecisionDisposition.SHADOW, decision_id="decision-shadow"),
            unit_id="unit-002",
        )

        with self.assertRaises(ShadowDivergenceError) as raised:
            compare_shadow_decision(baseline, shadow)

        self.assertEqual(raised.exception.reason_code, "review_identity_mismatch")

    def test_decision_identity_cannot_be_reused_across_roles(self) -> None:
        baseline = decision(DecisionDisposition.REJECTED, decision_id="decision-shared")
        shadow = decision(DecisionDisposition.SHADOW, decision_id="decision-shared")

        with self.assertRaises(ShadowDivergenceError) as raised:
            compare_shadow_decision(baseline, shadow)

        self.assertEqual(raised.exception.reason_code, "duplicate_decision_identity")

    def test_log_order_is_independent_of_shadow_input_order(self) -> None:
        baseline = decision(DecisionDisposition.REJECTED, decision_id="decision-active")
        shadows = (
            decision(DecisionDisposition.SHADOW, decision_id="decision-shadow-b"),
            decision(DecisionDisposition.SHADOW, decision_id="decision-shadow-a"),
        )

        first = build_shadow_divergence_log(baseline, shadows)
        second = build_shadow_divergence_log(baseline, tuple(reversed(shadows)))

        self.assertEqual(
            [record.shadow_decision_id for record in first],
            ["decision-shadow-a", "decision-shadow-b"],
        )
        self.assertEqual(
            [record.to_dict() for record in first],
            [record.to_dict() for record in second],
        )

    def test_empty_or_duplicate_shadow_batch_fails_closed(self) -> None:
        baseline = decision(DecisionDisposition.REJECTED, decision_id="decision-active")
        shadow = decision(DecisionDisposition.SHADOW, decision_id="decision-shadow")

        with self.assertRaises(ShadowDivergenceError) as missing:
            build_shadow_divergence_log(baseline, ())
        self.assertEqual(missing.exception.reason_code, "missing_shadow_decision")

        with self.assertRaises(ShadowDivergenceError) as duplicate:
            build_shadow_divergence_log(baseline, (shadow, shadow))
        self.assertEqual(duplicate.exception.reason_code, "duplicate_shadow_decision")

    def test_nested_mapping_order_cannot_change_comparison_identity(self) -> None:
        baseline = decision(DecisionDisposition.REJECTED, decision_id="decision-active")
        first_shadow = replace(
            decision(DecisionDisposition.SHADOW, decision_id="decision-shadow"),
            expert_outputs={"b": {"y": "2", "x": "1"}, "a": {"z": "3"}},
        )
        second_shadow = replace(
            first_shadow,
            expert_outputs={"a": {"z": "3"}, "b": {"x": "1", "y": "2"}},
        )

        first = compare_shadow_decision(baseline, first_shadow)
        second = compare_shadow_decision(baseline, second_shadow)

        self.assertEqual(first.comparison_id, second.comparison_id)
        self.assertEqual(first.digest, second.digest)


if __name__ == "__main__":
    unittest.main()

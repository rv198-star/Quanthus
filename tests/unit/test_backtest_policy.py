import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from quanthus.backtest.policy import (
    ExecutionPolicyError,
    ProfileKind,
    load_execution_policy,
)
from quanthus.policy import find_project_root

FIXTURE = find_project_root() / "tests" / "fixtures" / "p2" / "execution-policy.synthetic.v1.json"
RESEARCH_POLICY = find_project_root() / "config" / "p2-execution-policy.research.v1.json"


class ExecutionPolicyTests(unittest.TestCase):
    def test_frozen_daily_research_policy_is_explicit_and_official(self) -> None:
        policy = load_execution_policy(RESEARCH_POLICY)

        self.assertTrue(policy.official)
        self.assertEqual(policy.policy_version, "p2-daily-research-v1")
        self.assertEqual(policy.scope, "historical-and-paper-research-only")
        self.assertEqual(policy.active_profile.profile_id, "close-to-next-open-v1")
        self.assertEqual(policy.active_profile.schedule_version, "close-to-next-open-research-v1")
        self.assertEqual(policy.buy_cost.slippage_bps, Decimal("5"))
        self.assertEqual(policy.buy_cost.commission_rate, Decimal("0.0003"))
        self.assertEqual(policy.sell_cost.stamp_duty_rate, Decimal("0.0005"))
        self.assertEqual(policy.sell_cost.transfer_fee_rate, Decimal("0.00001"))

    def test_synthetic_policy_declares_active_daily_and_valid_two_window_profiles(self) -> None:
        policy = load_execution_policy(FIXTURE)

        self.assertFalse(policy.official)
        self.assertEqual(policy.sell_cost.stamp_duty_rate, Decimal("0.0005"))
        self.assertEqual(policy.sell_cost.transfer_fee_rate, Decimal("0.00001"))
        self.assertEqual(policy.active_profile.profile_id, "close-to-next-open-v1")
        target = policy.profile("synthetic-two-window-fixture-v1")
        self.assertEqual(target.kind, ProfileKind.TWO_WINDOW)
        self.assertEqual([window.window_id for window in target.windows], ["window_1", "window_2"])
        self.assertEqual(
            [(window.start.isoformat(), window.end.isoformat()) for window in target.windows],
            [("09:45:00", "10:15:00"), ("14:15:00", "15:00:00")],
        )
        self.assertEqual(
            [
                (
                    window.decision_cutoff.isoformat(),
                    window.first_eligible_execution.isoformat(),
                    window.expiry.isoformat(),
                )
                for window in target.windows
            ],
            [
                ("09:55:00", "10:00:00", "10:15:00"),
                ("14:30:00", "14:35:00", "15:00:00"),
            ],
        )

    def test_two_window_profile_requires_exactly_two_non_overlapping_windows(self) -> None:
        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        target = raw["profiles"][1]
        target["windows"][1]["start"] = "09:59:00"

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "overlap.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ExecutionPolicyError, "must not overlap"):
                load_execution_policy(path)

        target["windows"] = target["windows"][:1]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "one-window.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ExecutionPolicyError, "exactly two"):
                load_execution_policy(path)

    def test_policy_requires_exactly_one_active_profile(self) -> None:
        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        raw["profiles"][1]["active"] = True

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "two-active.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ExecutionPolicyError, "exactly one active"):
                load_execution_policy(path)

    def test_two_window_cutoff_execution_and_expiry_must_be_explicitly_ordered(self) -> None:
        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        raw["profiles"][1]["windows"][0]["decision_cutoff"] = "10:01:00"

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "late-cutoff.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ExecutionPolicyError, "must order"):
                load_execution_policy(path)


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

from quanthus.event_library.contracts import SplitAssignment
from quanthus.event_library.splits import (
    PartitionAccessError,
    SplitPolicyError,
    assign_event_split,
    calculate_split_policy_digest,
    load_split_policy,
)
from quanthus.policy import find_project_root

ROOT = find_project_root()
POLICY_PATH = ROOT / "config" / "p4-event-split-policy.research.v1.json"


def _weekdays(start: date, end: date) -> tuple[date, ...]:
    result: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            result.append(current)
        current += timedelta(days=1)
    return tuple(result)


class EventSplitPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.trading_dates = _weekdays(date(2012, 1, 1), date(2025, 12, 31))

    def _write(self, raw: object, directory: str) -> Path:
        path = Path(directory) / "split.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        return path

    def test_repository_policy_is_digest_bound_and_development_only(self) -> None:
        policy = load_split_policy(POLICY_PATH)

        self.assertEqual(policy.semantic_digest, policy.computed_semantic_digest)
        self.assertEqual(policy.allowed_read_partitions, (SplitAssignment.DEVELOPMENT,))
        self.assertEqual(policy.purge_sessions, 22)
        self.assertEqual(policy.embargo_sessions, 22)
        self.assertEqual(policy.lifecycle, "approved")
        self.assertEqual(policy.governance.approval_state, "approved")
        self.assertEqual(
            policy.governance.decision_reference,
            "https://github.com/rv198-star/Quanthus/issues/73#issuecomment-5196483760",
        )
        policy.require_stage_b_authority()
        with self.assertRaisesRegex(SplitPolicyError, "purge"):
            replace(policy, purge_sessions=21)

    def test_whole_event_assigns_deterministically_inside_development(self) -> None:
        policy = load_split_policy(POLICY_PATH)

        assignment = assign_event_split(
            policy,
            signal_date=date(2012, 6, 29),
            feature_start=date(2012, 6, 1),
            outcome_end=date(2012, 7, 27),
            trading_dates=self.trading_dates,
        )

        self.assertEqual(assignment, SplitAssignment.DEVELOPMENT)

    def test_boundary_leakage_and_forbidden_partitions_fail_closed(self) -> None:
        policy = load_split_policy(POLICY_PATH)

        with self.assertRaisesRegex(SplitPolicyError, "embargo"):
            assign_event_split(
                policy,
                signal_date=date(2012, 2, 1),
                feature_start=date(2012, 1, 2),
                outcome_end=date(2012, 3, 1),
                trading_dates=self.trading_dates,
            )
        with self.assertRaisesRegex(SplitPolicyError, "purge"):
            assign_event_split(
                policy,
                signal_date=date(2021, 11, 30),
                feature_start=date(2021, 11, 1),
                outcome_end=date(2021, 12, 31),
                trading_dates=self.trading_dates,
            )
        with self.assertRaisesRegex(SplitPolicyError, "one split"):
            assign_event_split(
                policy,
                signal_date=date(2021, 12, 15),
                feature_start=date(2021, 12, 1),
                outcome_end=date(2022, 1, 14),
                trading_dates=self.trading_dates,
            )
        with self.assertRaisesRegex(PartitionAccessError, "not authorized"):
            assign_event_split(
                policy,
                signal_date=date(2022, 6, 30),
                feature_start=date(2022, 6, 1),
                outcome_end=date(2022, 7, 29),
                trading_dates=self.trading_dates,
            )

    def test_split_semantic_mutation_requires_a_new_digest(self) -> None:
        raw = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        raw["embargo_sessions"] = 23
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(raw, directory)
            self.assertNotEqual(calculate_split_policy_digest(path), raw["semantic_digest"])
            with self.assertRaisesRegex(SplitPolicyError, "does not match"):
                load_split_policy(path)


if __name__ == "__main__":
    unittest.main()

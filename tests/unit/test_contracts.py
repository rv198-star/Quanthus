import unittest
from datetime import date, datetime, timezone

from quanthus.contracts import (
    CandidateSignal,
    EventOutcome,
    RunManifest,
    SnapshotRef,
    VetoAction,
    rank_candidates,
    strictest_veto,
)


class ContractTests(unittest.TestCase):
    def test_snapshot_requires_timezone_aware_cutoff(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            SnapshotRef(
                market_date=date(2025, 1, 2),
                knowledge_cutoff=datetime(2025, 1, 2, 15, 30),
                source_revision="source-v1",
                snapshot_id="snapshot-1",
            )

    def test_snapshot_accepts_a_valid_reference(self) -> None:
        snapshot = SnapshotRef(
            market_date=date(2025, 1, 2),
            knowledge_cutoff=datetime(2025, 1, 2, 8, 0, tzinfo=timezone.utc),
            source_revision="source-v1",
            snapshot_id="snapshot-1",
        )

        self.assertEqual(snapshot.snapshot_id, "snapshot-1")

    def test_candidates_rank_by_score_then_code(self) -> None:
        candidates = [
            CandidateSignal("s1", "600002", 0.8),
            CandidateSignal("s2", "000001", 0.8),
            CandidateSignal("s3", "300001", 0.9),
        ]

        ranked = rank_candidates(candidates)

        self.assertEqual([item.signal_id for item in ranked], ["s3", "s2", "s1"])

    def test_veto_uses_strictest_action(self) -> None:
        action = strictest_veto([VetoAction.BLOCK_NEW, VetoAction.FORCE_LIQUIDATE])

        self.assertEqual(action, VetoAction.FORCE_LIQUIDATE)

    def test_event_outcome_vocabulary_matches_p4_contract(self) -> None:
        self.assertEqual(EventOutcome.POSITIVE.value, "positive")
        self.assertEqual(EventOutcome.NEUTRAL.value, "neutral")
        self.assertEqual(EventOutcome.FAILURE.value, "failure")
        self.assertEqual(EventOutcome.NOT_EXECUTABLE.value, "not_executable")

    def test_run_manifest_rejects_blank_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "code_version"):
            RunManifest(
                code_version="",
                parameter_hash="params",
                data_snapshot_id="snapshot",
                feature_version="feature-v1",
                rule_version="rule-v1",
                run_id="run-1",
                result_summary={},
            )


if __name__ == "__main__":
    unittest.main()

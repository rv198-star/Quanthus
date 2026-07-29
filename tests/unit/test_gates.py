import copy
import json
import tempfile
import unittest
from pathlib import Path

from quanthus.gates import load_gate_report, load_p2_gate_report, load_p3_gate_report
from quanthus.policy import ConfigurationError, find_project_root


class GateTests(unittest.TestCase):
    def test_current_release_state(self) -> None:
        report = load_gate_report()

        self.assertTrue(report.p1_can_start)
        self.assertTrue(report.p1_acceptance_passed)
        self.assertTrue(report.p2_can_start)
        self.assertEqual(report.open_blockers, ())
        self.assertEqual(report.chapter_01_status, "passed")

    def test_p2_cannot_open_before_chapter_01_passes(self) -> None:
        gate_path = find_project_root() / "config" / "p1-gates.v1.json"
        raw = copy.deepcopy(json.loads(gate_path.read_text(encoding="utf-8")))
        raw["chapter_01_acceptance"]["status"] = "pending"
        raw["p2_implementation"] = "allowed"

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gates.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigurationError, "Chapter 01"):
                load_gate_report(path)

    def test_p2_gate_accepts_daily_research_and_opens_p3(self) -> None:
        report = load_p2_gate_report()

        self.assertEqual(report.acceptance_status, "passed-conditional")
        self.assertTrue(report.p2_acceptance_passed)
        self.assertTrue(report.p3_can_start)
        self.assertEqual(report.accepted_profile, "close-to-next-open-v1")
        self.assertEqual(
            report.open_activation_blockers,
            ("P2-INTRADAY-INPUT", "P2-INTRADAY-MATCH", "P6-NOTIFICATION-TRANSPORT"),
        )

    def test_p3_cannot_open_before_p2_acceptance(self) -> None:
        gate_path = find_project_root() / "config" / "p2-gates.v1.json"
        raw = copy.deepcopy(json.loads(gate_path.read_text(encoding="utf-8")))
        raw["p2_acceptance"] = "pending"
        raw["p3_implementation"] = "allowed"

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "p2-gates.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigurationError, "P3 cannot be allowed"):
                load_p2_gate_report(path)

    def test_p3_gate_accepts_capability_slice_and_opens_p4(self) -> None:
        report = load_p3_gate_report()

        self.assertEqual(report.acceptance_status, "passed")
        self.assertTrue(report.p3_acceptance_passed)
        self.assertTrue(report.p4_can_start)
        self.assertEqual(report.accepted_profile, "close-to-next-open-v1")
        self.assertEqual(
            report.capability_fixture_id,
            "candidate.market-breadth-trend-breakout@1.0.0",
        )
        self.assertEqual(
            report.deferred_work_items,
            ("P3-ATTR-001", "P3-DASH-001", "P3-LABEL-004"),
        )
        self.assertEqual(
            report.unopened_partitions,
            ("2022-2024-arbitration", "2025-permanent-blind"),
        )

    def test_p4_cannot_open_before_p3_acceptance(self) -> None:
        gate_path = find_project_root() / "config" / "p3-gates.v1.json"
        raw = copy.deepcopy(json.loads(gate_path.read_text(encoding="utf-8")))
        raw["p3_acceptance"] = "pending"
        raw["p4_implementation"] = "allowed"

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "p3-gates.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigurationError, "P4 cannot be allowed"):
                load_p3_gate_report(path)

    def test_p3_capability_fixture_cannot_authorize_tuning_or_promotion(self) -> None:
        gate_path = find_project_root() / "config" / "p3-gates.v1.json"
        raw = copy.deepcopy(json.loads(gate_path.read_text(encoding="utf-8")))
        raw["capability_fixture"]["tuning_authorized"] = True

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "p3-gates.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigurationError, "tuning_authorized must be false"):
                load_p3_gate_report(path)

    def test_p3_release_blocker_cannot_be_reclassified_as_deferred(self) -> None:
        gate_path = find_project_root() / "config" / "p3-gates.v1.json"
        raw = copy.deepcopy(json.loads(gate_path.read_text(encoding="utf-8")))
        raw["work_items"]["P3-REPLAY-001"] = "deferred"
        raw["deferred_capabilities"].append(
            {
                "id": "P3-REPLAY-001",
                "resume_condition": "invalid attempt to defer an accepted release blocker",
            }
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "p3-gates.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigurationError, "invalid deferred work-item set"):
                load_p3_gate_report(path)


if __name__ == "__main__":
    unittest.main()

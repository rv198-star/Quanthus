import copy
import json
import tempfile
import unittest
from pathlib import Path

from quanthus.gates import load_gate_report
from quanthus.policy import ConfigurationError, find_project_root


class GateTests(unittest.TestCase):
    def test_current_release_state(self) -> None:
        report = load_gate_report()

        self.assertTrue(report.p1_can_start)
        self.assertFalse(report.p1_acceptance_passed)
        self.assertFalse(report.p2_can_start)
        self.assertEqual(report.open_blockers, ("C1",))
        self.assertEqual(report.chapter_01_status, "pending")

    def test_p2_cannot_open_before_chapter_01_passes(self) -> None:
        gate_path = find_project_root() / "config" / "p1-gates.v1.json"
        raw = copy.deepcopy(json.loads(gate_path.read_text(encoding="utf-8")))
        raw["p2_implementation"] = "allowed"

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gates.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigurationError, "Chapter 01"):
                load_gate_report(path)


if __name__ == "__main__":
    unittest.main()

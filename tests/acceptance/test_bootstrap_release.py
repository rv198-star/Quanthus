import io
import unittest
from contextlib import redirect_stdout

from quanthus.cli import main
from quanthus.gates import load_gate_report


class BootstrapAcceptanceTests(unittest.TestCase):
    def test_doctor_passes_while_reporting_expected_blockers(self) -> None:
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = main(["doctor"])

        self.assertEqual(exit_code, 0)
        rendered = output.getvalue()
        self.assertIn("Quanthus environment: OK", rendered)
        self.assertIn("P1 implementation: ALLOWED", rendered)
        self.assertIn("open blockers: C1, C2, S1", rendered)
        self.assertIn("P2 implementation: BLOCKED", rendered)

    def test_release_gate_matches_approved_p0_decision(self) -> None:
        report = load_gate_report()

        self.assertTrue(report.p1_can_start)
        self.assertFalse(report.p1_acceptance_passed)
        self.assertFalse(report.p2_can_start)


if __name__ == "__main__":
    unittest.main()

import copy
import unittest
from datetime import date, timedelta

from quanthus.p1_acceptance import validate_p1_acceptance_evidence


def valid_evidence() -> dict[str, object]:
    dates = [date(2015, 1, 5) + timedelta(days=offset) for offset in range(10)]
    securities = [f"600{index:03d}" for index in range(10)]
    return {
        "schema_version": "1.0.0",
        "snapshot_id": "p1-2015-01-05-v1",
        "blockers": {
            blocker: {"status": "pass", "evidence_id": f"{blocker}-evidence"}
            for blocker in ("C1", "C2", "S1")
        },
        "D1": {
            "independent_reference": "exchange-reference-v1",
            "cases": [
                {
                    "market_date": market_date.isoformat(),
                    "security_id": security_id,
                    "status": "pass",
                    "reference_id": f"d1-{market_date.isoformat()}-{security_id}",
                }
                for market_date in dates
                for security_id in securities
            ],
        },
        "D2": {
            "independent_reference": "exchange-status-reference-v1",
            "cases": [
                {
                    "market_date": (date(2016, 1, 1) + timedelta(days=index)).isoformat(),
                    "security_id": f"000{index:03d}",
                    "case_type": "limit" if index % 2 == 0 else "suspension",
                    "status": "pass",
                    "reference_id": f"d2-{index}",
                }
                for index in range(20)
            ],
        },
        "D3": {
            "independent_reference": "corporate-action-reference-v1",
            "cases": [
                {
                    "ex_date": "2017-06-01",
                    "security_id": "600000",
                    "status": "pass",
                    "reference_id": "d3-1",
                    "artificial_jump_detected": False,
                }
            ],
        },
    }


class P1AcceptanceEvidenceTests(unittest.TestCase):
    def test_complete_evidence_passes_structure_validation(self) -> None:
        report = validate_p1_acceptance_evidence(valid_evidence())

        self.assertFalse(report.has_failures)
        self.assertEqual(
            [check.check_id for check in report.checks], ["C1", "C2", "S1", "D1", "D2", "D3"]
        )

    def test_d1_requires_the_full_ten_by_ten_matrix(self) -> None:
        evidence = valid_evidence()
        d1 = evidence["D1"]
        assert isinstance(d1, dict)
        cases = d1["cases"]
        assert isinstance(cases, list)
        cases.pop()

        report = validate_p1_acceptance_evidence(evidence)

        by_id = {check.check_id: check for check in report.checks}
        self.assertFalse(by_id["D1"].passed)

    def test_d3_rejects_an_artificial_jump_or_open_blocker(self) -> None:
        evidence = copy.deepcopy(valid_evidence())
        blockers = evidence["blockers"]
        assert isinstance(blockers, dict)
        c1 = blockers["C1"]
        assert isinstance(c1, dict)
        c1["status"] = "open"
        d3 = evidence["D3"]
        assert isinstance(d3, dict)
        cases = d3["cases"]
        assert isinstance(cases, list)
        case = cases[0]
        assert isinstance(case, dict)
        case["artificial_jump_detected"] = True

        report = validate_p1_acceptance_evidence(evidence)

        by_id = {check.check_id: check for check in report.checks}
        self.assertFalse(by_id["C1"].passed)
        self.assertFalse(by_id["D3"].passed)


if __name__ == "__main__":
    unittest.main()

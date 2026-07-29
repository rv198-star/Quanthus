import json
import unittest
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from quanthus.backtest import (
    CoverageStatus,
    P1DailyExecutionEvidence,
    market_session_from_p1,
)
from quanthus.contracts import SnapshotRef
from quanthus.policy import find_project_root

ROOT = find_project_root()
GOLDEN_PATH = ROOT / "tests" / "fixtures" / "golden" / "p2_p1_execution_adapter.v1.json"
SHANGHAI = ZoneInfo("Asia/Shanghai")


class BacktestInputAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
        self.base = self.fixture["base"]

    def evidence(self, case: dict[str, object]) -> P1DailyExecutionEvidence:
        def price(field: str) -> Decimal:
            return Decimal(str(case.get(field, self.base[field])))

        market_date = date.fromisoformat(self.base["market_date"])
        return P1DailyExecutionEvidence(
            snapshot=SnapshotRef(
                market_date=market_date,
                knowledge_cutoff=datetime(2026, 7, 28, 18, 0, tzinfo=SHANGHAI),
                source_revision="p1-synthetic-integrated-execution-v1",
                snapshot_id=f"p1-execution-{case['id']}-v1",
            ),
            security_code=self.base["security_code"],
            open_at=datetime.fromisoformat(self.base["open_at"]),
            close_at=datetime.fromisoformat(self.base["close_at"]),
            coverage_status=CoverageStatus(str(case["coverage_status"])),
            data_valid=bool(case["data_valid"]),
            is_executable=bool(case["is_executable"]),
            entry_policy_allows=bool(case["entry_policy_allows"]),
            reason_code=str(case["reason_code"]),
            open_price=price("open_price"),
            high_price=price("high_price"),
            low_price=price("low_price"),
            close_price=price("close_price"),
            upper_limit_price=price("upper_limit_price"),
            lower_limit_price=price("lower_limit_price"),
        )

    def test_p1_rows_map_to_explicit_conservative_execution_states(self) -> None:
        for case in self.fixture["cases"]:
            with self.subTest(case_id=case["id"]):
                session = market_session_from_p1(self.evidence(case))

                self.assertEqual(session.open_execution_state.value, case["expected_state"])
                if case["keeps_prices"]:
                    self.assertIsNotNone(session.open_price)
                    self.assertIsNotNone(session.close_price)
                else:
                    self.assertIsNone(session.open_price)
                    self.assertIsNone(session.close_price)

    def test_valid_trading_row_requires_complete_consistent_ohlc(self) -> None:
        normal = self.fixture["cases"][0]
        evidence = self.evidence(normal)
        with self.assertRaisesRegex(ValueError, "complete OHLC"):
            P1DailyExecutionEvidence(
                **{
                    **evidence.__dict__,
                    "open_price": None,
                }
            )
        with self.assertRaisesRegex(ValueError, "OHLC ordering"):
            P1DailyExecutionEvidence(
                **{
                    **evidence.__dict__,
                    "open_price": Decimal("12.00"),
                }
            )


if __name__ == "__main__":
    unittest.main()

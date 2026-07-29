import json
import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal

from quanthus.backtest import (
    EquityPoint,
    FixedUnitPerformanceInput,
    build_fixed_unit_report,
)
from quanthus.policy import find_project_root

FIXTURE = find_project_root() / "tests" / "fixtures" / "golden" / "p2_fixed_unit_report.v1.json"


class FixedUnitPerformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def performance_input(self) -> FixedUnitPerformanceInput:
        raw = self.fixture["inputs"]
        return FixedUnitPerformanceInput(
            target_notional=Decimal(raw["target_notional"]),
            entry_gross=Decimal(raw["entry_gross"]),
            entry_fees=Decimal(raw["entry_fees"]),
            exit_gross=Decimal(raw["exit_gross"]),
            exit_fees=Decimal(raw["exit_fees"]),
            dividends=Decimal(raw["dividends"]),
            capital_days=raw["capital_days"],
            equity_points=tuple(
                EquityPoint(
                    market_date=date.fromisoformat(point["market_date"]),
                    system_equity=Decimal(point["system_equity"]),
                    external_cash_flow=Decimal(point["external_cash_flow"]),
                )
                for point in raw["equity_points"]
            ),
        )

    def test_hand_calculated_fixed_unit_report_reconciles(self) -> None:
        report = build_fixed_unit_report(self.performance_input())

        self.assertEqual(report.to_dict(), self.fixture["expected"])
        self.assertFalse(report.compound_nav)

    def test_report_rejects_wealth_that_does_not_reconcile_to_trade_cash_flows(self) -> None:
        performance = self.performance_input()
        changed_final = replace(performance.equity_points[-1], system_equity=Decimal("10108.48"))

        with self.assertRaisesRegex(ValueError, "does not reconcile"):
            build_fixed_unit_report(
                replace(
                    performance,
                    equity_points=performance.equity_points[:-1] + (changed_final,),
                )
            )


if __name__ == "__main__":
    unittest.main()

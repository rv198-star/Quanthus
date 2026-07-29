"""Fixed-unit performance reporting without compound-NAV claims."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from quanthus.backtest.contracts import decimal_text

MONEY_QUANTUM = Decimal("0.01")
RATE_QUANTUM = Decimal("0.00000001")


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _rate(value: Decimal) -> Decimal:
    return value.quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)


def _non_negative(value: Decimal, field_name: str, *, positive: bool = False) -> None:
    if not value.is_finite() or value < 0 or (positive and value <= 0):
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"{field_name} must be finite and {qualifier}")
    if value != _money(value):
        raise ValueError(f"{field_name} must use the cash quantum")


@dataclass(frozen=True)
class EquityPoint:
    market_date: date
    system_equity: Decimal
    external_cash_flow: Decimal = Decimal("0.00")

    def __post_init__(self) -> None:
        _non_negative(self.system_equity, "system_equity", positive=True)
        if not self.external_cash_flow.is_finite():
            raise ValueError("external_cash_flow must be finite")
        if self.external_cash_flow != _money(self.external_cash_flow):
            raise ValueError("external_cash_flow must use the cash quantum")


@dataclass(frozen=True)
class FixedUnitPerformanceInput:
    target_notional: Decimal
    entry_gross: Decimal
    entry_fees: Decimal
    exit_gross: Decimal
    exit_fees: Decimal
    dividends: Decimal
    capital_days: int
    equity_points: tuple[EquityPoint, ...]

    def __post_init__(self) -> None:
        _non_negative(self.target_notional, "target_notional", positive=True)
        _non_negative(self.entry_gross, "entry_gross", positive=True)
        for value, field_name in (
            (self.entry_fees, "entry_fees"),
            (self.exit_gross, "exit_gross"),
            (self.exit_fees, "exit_fees"),
            (self.dividends, "dividends"),
        ):
            _non_negative(value, field_name)
        if not isinstance(self.capital_days, int) or isinstance(self.capital_days, bool):
            raise ValueError("capital_days must be an integer")
        if self.capital_days <= 0:
            raise ValueError("capital_days must be positive")
        if len(self.equity_points) < 2:
            raise ValueError("performance input requires at least two equity points")
        if any(
            current.market_date >= following.market_date
            for current, following in zip(self.equity_points, self.equity_points[1:], strict=False)
        ):
            raise ValueError("equity point dates must be strictly increasing")


@dataclass(frozen=True)
class FixedUnitPerformanceReport:
    trade_return: Decimal
    unit_capital_day_return: Decimal
    total_pnl: Decimal
    external_cash_flow: Decimal
    cash_flow_adjusted_drawdown: Decimal
    compound_nav: bool = False

    def to_dict(self) -> dict[str, str | bool]:
        return {
            "trade_return": decimal_text(self.trade_return),
            "unit_capital_day_return": decimal_text(self.unit_capital_day_return),
            "total_pnl": decimal_text(self.total_pnl),
            "external_cash_flow": decimal_text(self.external_cash_flow),
            "cash_flow_adjusted_drawdown": decimal_text(self.cash_flow_adjusted_drawdown),
            "compound_nav": self.compound_nav,
        }


def build_fixed_unit_report(
    performance: FixedUnitPerformanceInput,
) -> FixedUnitPerformanceReport:
    external_cash_flow = _money(
        sum(
            (point.external_cash_flow for point in performance.equity_points),
            Decimal("0.00"),
        )
    )
    total_pnl = _money(
        performance.equity_points[-1].system_equity
        - performance.equity_points[0].system_equity
        - external_cash_flow
    )
    trade_pnl = _money(
        performance.exit_gross
        - performance.exit_fees
        + performance.dividends
        - performance.entry_gross
        - performance.entry_fees
    )
    if total_pnl != trade_pnl:
        raise ValueError(
            "trade cash-flow PnL does not reconcile to cash-flow-adjusted system wealth"
        )

    deployed_capital = performance.entry_gross + performance.entry_fees
    trade_return = _rate(trade_pnl / deployed_capital)
    unit_capital_day_return = _rate(
        total_pnl / (performance.target_notional * performance.capital_days)
    )

    cumulative_external_flow = Decimal("0.00")
    peak_adjusted_equity: Decimal | None = None
    maximum_drawdown = Decimal("0")
    for point in performance.equity_points:
        cumulative_external_flow += point.external_cash_flow
        adjusted_equity = point.system_equity - cumulative_external_flow
        if adjusted_equity <= 0:
            raise ValueError("cash-flow-adjusted equity must remain positive")
        if peak_adjusted_equity is None or adjusted_equity > peak_adjusted_equity:
            peak_adjusted_equity = adjusted_equity
        drawdown = (peak_adjusted_equity - adjusted_equity) / peak_adjusted_equity
        maximum_drawdown = max(maximum_drawdown, drawdown)

    return FixedUnitPerformanceReport(
        trade_return=trade_return,
        unit_capital_day_return=unit_capital_day_return,
        total_pnl=total_pnl,
        external_cash_flow=external_cash_flow,
        cash_flow_adjusted_drawdown=_rate(maximum_drawdown),
    )

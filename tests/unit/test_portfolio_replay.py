from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from quanthus.backtest.baseline_research import BaselineFeatureRow
from quanthus.backtest.benchmark import BenchmarkCurve, BenchmarkObservation
from quanthus.backtest.contracts import EngineEvent, EventType
from quanthus.backtest.portfolio_replay import (
    PortfolioEquityPoint,
    PortfolioTrade,
    _build_performance,
    _entry_quantity,
)
from quanthus.strategy_policy import load_governed_strategy_policy

ROOT = Path(__file__).resolve().parents[2]


def _feature(close_price: str) -> BaselineFeatureRow:
    return BaselineFeatureRow(
        market_date=date(2020, 1, 2),
        security_id="sec-1",
        close_price=Decimal(close_price),
        total_return_index=Decimal("100"),
        sma20=Decimal("99"),
        sma60=Decimal("98"),
        momentum20=Decimal("0.05"),
        prior_high20=Decimal("99.5"),
        rsi14=Decimal("60"),
        rsi14_previous=Decimal("59"),
        volume_ratio20=Decimal("1.2"),
        realized_volatility20=Decimal("0.2"),
        daily_total_return=Decimal("0.01"),
        average_amount20=Decimal("200000000"),
        listing_sessions=200,
        feature_ready=True,
        entry_eligible=True,
    )


def _trade(pnl: str, entry_date: date, exit_date: date) -> PortfolioTrade:
    value = Decimal(pnl)
    return PortfolioTrade(
        unit_id=f"unit-{pnl}",
        opportunity_id=f"opportunity-{pnl}",
        security_id=f"security-{pnl}",
        entry_date=entry_date,
        exit_date=exit_date,
        entry_quantity=100,
        exit_quantity=100,
        entry_gross=Decimal("10000.00"),
        entry_fees=Decimal("5.00"),
        exit_gross=Decimal("10000.00") + value + Decimal("10.00"),
        exit_fees=Decimal("5.00"),
        dividends=Decimal("0.00"),
        pnl=value,
        return_rate=value / Decimal("10005.00"),
        holding_sessions=5,
        entry_breadth_regime="supportive",
        exit_sources=("maximum_holding_period",),
    )


def test_entry_quantity_uses_trial_budget_buffer_and_whole_lots() -> None:
    policy = load_governed_strategy_policy(
        ROOT / "config" / "p3-governed-strategy.research.v1.json"
    )

    assert _entry_quantity(_feature("20.00"), policy) == 1200
    assert _entry_quantity(_feature("300.00"), policy) == 0


def test_portfolio_performance_reconciles_capital_path_and_top_three() -> None:
    dates = (date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 6))
    equity = (
        PortfolioEquityPoint(dates[0], Decimal("500000.00"), Decimal("0.00"), Decimal("0"), 0),
        PortfolioEquityPoint(
            dates[1], Decimal("501000.00"), Decimal("250500.00"), Decimal("0.5"), 3
        ),
        PortfolioEquityPoint(
            dates[2], Decimal("500600.00"), Decimal("125150.00"), Decimal("0.25"), 2
        ),
    )
    trades = (
        _trade("500.00", dates[0], dates[1]),
        _trade("300.00", dates[0], dates[1]),
        _trade("-100.00", dates[1], dates[2]),
        _trade("-100.00", dates[1], dates[2]),
    )
    fill = EngineEvent(
        event_id="fill-event",
        sequence=1,
        event_type=EventType.FILL,
        event_time=datetime(2020, 1, 3, tzinfo=timezone.utc),
        reason_code="normal_buy_fill",
        payload={"side": "buy", "gross_amount": "10000.00", "total_fees": "5.00"},
    )
    benchmark = BenchmarkCurve(
        benchmark_id="benchmark-csi300",
        instrument_id="sh000300",
        display_name="CSI 300",
        currency="CNY",
        price_field="close",
        source="test",
        source_revision="test-v1",
        acquired_at=datetime(2020, 1, 7, tzinfo=timezone.utc),
        observations=(
            BenchmarkObservation(dates[0], Decimal("100")),
            BenchmarkObservation(dates[1], Decimal("102")),
            BenchmarkObservation(dates[2], Decimal("101")),
        ),
    )

    report = _build_performance(
        equity_curve=equity,
        trades=trades,
        engine_events=(fill,),
        benchmark=benchmark,
        unit_count=5,
    )

    assert report.total_return == Decimal("0.00120000")
    assert report.average_invested_capital_ratio == Decimal("0.37500000")
    assert report.exposure_rate == Decimal("0.50000000")
    assert report.top_3_closed_trade_pnl == Decimal("700.00")
    assert report.return_excluding_top_3_closed_trades == Decimal("-0.00020000")
    assert report.operation_count == 1

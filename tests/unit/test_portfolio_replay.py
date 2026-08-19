from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from quanthus.backtest.baseline_research import BaselineFeatureRow
from quanthus.backtest.benchmark import BenchmarkCurve, BenchmarkObservation
from quanthus.backtest.contracts import (
    BacktestRunSpec,
    EngineEvent,
    EventType,
    OpenExecutionState,
    UnitState,
)
from quanthus.backtest.engine import BacktestEngine
from quanthus.backtest.policy import load_execution_policy
from quanthus.backtest.portfolio_replay import (
    PortfolioEquityPoint,
    PortfolioTrade,
    _build_exit_schedule,
    _build_performance,
    _entry_quantity,
    _OpenPortfolioTrade,
    _UnitRuntime,
)
from quanthus.backtest.strategy_replay import StrategyReplayFrame
from quanthus.contracts import SnapshotRef
from quanthus.position_lifecycle import (
    POSITION_STATE_MACHINE_VERSION,
    PositionLifecycleState,
    PositionStage,
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


def test_governed_exit_schedule_carries_observed_non_trading_session() -> None:
    from quanthus.backtest.contracts import MarketSession
    from quanthus.exit_arbitration import ExitTriggerSource

    policy = load_governed_strategy_policy(
        ROOT / "config" / "p3-governed-strategy.research.v1.json"
    )
    engine = BacktestEngine(
        load_execution_policy(ROOT / "config" / "p2-execution-policy.research.v1.json")
    )
    decision_dates = (date(2020, 1, 2), date(2020, 1, 3))
    execution_dates = (date(2020, 1, 3), date(2020, 1, 6))
    sessions = (
        MarketSession(
            session_date=execution_dates[0],
            open_at=datetime(2020, 1, 3, 9, 30, tzinfo=timezone.utc),
            close_at=datetime(2020, 1, 3, 15, 0, tzinfo=timezone.utc),
            security_code="sec-1",
            open_price=Decimal("10.00"),
            close_price=Decimal("10.10"),
            open_execution_state=OpenExecutionState.NON_TRADING_OBSERVED,
            source_revision="observed-zero-activity-v1",
            snapshot_id="session-20200103",
        ),
        MarketSession(
            session_date=execution_dates[1],
            open_at=datetime(2020, 1, 6, 9, 30, tzinfo=timezone.utc),
            close_at=datetime(2020, 1, 6, 15, 0, tzinfo=timezone.utc),
            security_code="sec-1",
            open_price=Decimal("10.20"),
            close_price=Decimal("10.30"),
            open_execution_state=OpenExecutionState.EXECUTABLE,
            source_revision="tradable-observed-v1",
            snapshot_id="session-20200106",
        ),
    )
    frames = tuple(
        StrategyReplayFrame(
            snapshot=SnapshotRef(
                market_date=decision_date,
                knowledge_cutoff=datetime.combine(
                    decision_date, datetime.min.time(), timezone.utc
                ).replace(hour=15),
                source_revision="decision-snapshot-v1",
                snapshot_id=f"decision-{decision_date.isoformat()}",
            ),
            decision_at=datetime.combine(
                decision_date, datetime.min.time(), timezone.utc
            ).replace(hour=15),
            execution_date=execution_date,
            execution_sessions=(session,),
        )
        for decision_date, execution_date, session in zip(
            decision_dates, execution_dates, sessions, strict=True
        )
    )
    ledger = UnitState(
        unit_id="capital-unit-01",
        target_notional=policy.portfolio.unit_notional,
        cash=Decimal("90000.00"),
        security_code="sec-1",
        settled_quantity=1000,
        position_cost=Decimal("10000.00"),
        commission_expense=Decimal("5.00"),
        stamp_duty_expense=Decimal("0.00"),
        transfer_fee_expense=Decimal("0.00"),
        realized_pnl=Decimal("0.00"),
        dividend_income=Decimal("0.00"),
        external_pool=Decimal("0.00"),
        last_mark_price=Decimal("10.00"),
        last_mark_snapshot_id="prior-mark",
    )
    runtime = _UnitRuntime(
        ledger=ledger,
        lifecycle=PositionLifecycleState(
            unit_id="capital-unit-01",
            stage=PositionStage.TRIAL,
            security_code="sec-1",
            opportunity_id="opportunity-1",
            state_machine_version=POSITION_STATE_MACHINE_VERSION,
            as_of=datetime(2020, 1, 2, 14, 59, tzinfo=timezone.utc),
            transition_ids=("entry-transition",),
            consumed_evidence=(),
        ),
        open_trade=_OpenPortfolioTrade(
            unit_id="capital-unit-01",
            opportunity_id="opportunity-1",
            security_id="sec-1",
            entry_date=date(2019, 12, 2),
            entry_quantity=1000,
            entry_gross=Decimal("10000.00"),
            entry_fees=Decimal("5.00"),
            entry_breadth_regime="supportive",
        ),
    )
    run = BacktestRunSpec(
        code_version="test-code-v1",
        parameter_hash=policy.digest,
        data_snapshot_id="decision-2020-01-02",
        feature_version="test-features-v1",
        rule_version=policy.strategy_version,
        run_id="governed-non-trading-exit",
        execution_policy_version=engine.policy.policy_version,
        execution_profile_id=engine.policy.active_profile.profile_id,
        schedule_version=engine.policy.active_profile.schedule_version,
        official=False,
    )
    review = cast(
        Any,
        SimpleNamespace(
            arbitration=SimpleNamespace(
                triggered_sources=(ExitTriggerSource.MAXIMUM_HOLDING_PERIOD,),
                reason_codes=("maximum_holding_period",),
            )
        ),
    )

    schedule = _build_exit_schedule(
        reader_frames=frames,
        frame_index=0,
        runtime=runtime,
        frame=frames[0],
        run=run,
        engine=engine,
        policy=policy,
        review=review,
    )

    assert tuple(schedule.states_by_date) == execution_dates
    assert schedule.carry_events_by_date[execution_dates[0]].reason_code == (
        "security_not_trading_observed"
    )
    assert schedule.fill_events_by_date[execution_dates[1]].payload["side"] == "sell"

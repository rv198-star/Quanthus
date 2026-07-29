"""Five-unit governed P3 portfolio replay over the accepted P2 engine."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Any

from quanthus.aggregator import GovernedAction
from quanthus.backtest.baseline_research import (
    BaselineFeaturePanel,
    BaselineFeatureRow,
)
from quanthus.backtest.benchmark import (
    BenchmarkCapitalWeight,
    BenchmarkCurve,
)
from quanthus.backtest.contracts import (
    MARKET_TIMEZONE,
    BacktestRunSpec,
    BacktestSignal,
    CorporateActionStep,
    DecisionDisposition,
    DecisionEvent,
    EngineEvent,
    EventType,
    OpenExecutionState,
    OrderSide,
    UnitState,
    decimal_text,
)
from quanthus.backtest.engine import BacktestEngine
from quanthus.backtest.historical_frames import HistoricalReplayFrameReader
from quanthus.backtest.strategy_replay import StrategyReplayFrame
from quanthus.capital_allocation import (
    AllocationDisposition,
    AvailableCapitalUnit,
    EntryAllocationCandidate,
    EntryAllocationDecision,
    allocate_entry_reservations,
)
from quanthus.concentration import ActiveUnitExposure
from quanthus.feature_registry import FeatureRegistry
from quanthus.governed_strategy import (
    GovernedCandidateDecision,
    GovernedPositionReview,
    evaluate_governed_candidate,
    evaluate_governed_position_review,
    governed_candidate_decision_event,
    governed_position_decision_event,
)
from quanthus.industry_mapping import StaticIndustryMapping
from quanthus.label_registry import LabelRegistry
from quanthus.opportunity_funnel import (
    FunnelDisposition,
    FunnelObservation,
    FunnelOutcomeKind,
    FunnelStage,
    OpportunityFunnelReport,
    build_opportunity_funnel,
)
from quanthus.policy import P0Policy
from quanthus.position_lifecycle import (
    POSITION_STATE_MACHINE_VERSION,
    PositionEvidenceKind,
    PositionEvidenceRef,
    PositionLifecycleState,
    PositionStage,
    PositionTransition,
    PositionTransitionKind,
    apply_position_transition,
    initialize_position_lifecycle,
)
from quanthus.strategy_policy import GovernedStrategyPolicy

PORTFOLIO_REPLAY_VERSION = "1.0.0"
MONEY_QUANTUM = Decimal("0.01")
RATE_QUANTUM = Decimal("0.00000001")
TRADING_PERIODS_PER_YEAR = Decimal("252")


class PortfolioReplayError(ValueError):
    """Raised when portfolio evidence cannot be replayed or reconciled."""


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _rate(value: Decimal) -> Decimal:
    return value.quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        default=lambda item: (
            item.isoformat()
            if isinstance(item, (date, datetime))
            else decimal_text(item)
            if isinstance(item, Decimal)
            else item.value
            if isinstance(item, Enum)
            else item.to_dict()
            if hasattr(item, "to_dict")
            else str(item)
        ),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


def _stable_id(prefix: str, *parts: object) -> str:
    return f"{prefix}_{hashlib.sha256(_canonical_bytes(parts)).hexdigest()[:24]}"


def _wealth(state: UnitState) -> Decimal:
    market_value = (
        Decimal("0.00")
        if state.last_mark_price is None
        else _money(state.last_mark_price * state.settled_quantity)
    )
    return _money(state.cash + market_value + state.external_pool)


def _market_value(state: UnitState) -> Decimal:
    if state.last_mark_price is None:
        return Decimal("0.00")
    return _money(state.last_mark_price * state.settled_quantity)


def _state_from_snapshot(event: EngineEvent, *, target_notional: Decimal) -> UnitState:
    if event.event_type is not EventType.DAILY_SNAPSHOT:
        raise PortfolioReplayError("unit state requires a P2 daily snapshot")
    payload = event.payload
    quantity = int(payload["position_quantity"])
    mark_price = payload.get("mark_price")
    mark_snapshot_id = payload.get("mark_snapshot_id")
    return UnitState(
        unit_id=str(payload["unit_id"]),
        target_notional=target_notional,
        cash=Decimal(str(payload["cash"])),
        security_code=None if quantity == 0 else str(payload["security_code"]),
        settled_quantity=quantity,
        position_cost=Decimal(str(payload["position_cost"])),
        commission_expense=Decimal(str(payload["commission_expense"])),
        stamp_duty_expense=Decimal(str(payload["stamp_duty_expense"])),
        transfer_fee_expense=Decimal(str(payload["transfer_fee_expense"])),
        realized_pnl=Decimal(str(payload["realized_pnl"])),
        dividend_income=Decimal(str(payload["dividend_income"])),
        external_pool=Decimal(str(payload["external_pool"])),
        last_mark_price=None if mark_price is None else Decimal(str(mark_price)),
        last_mark_snapshot_id=None if mark_snapshot_id is None else str(mark_snapshot_id),
    )


def _child_run(run: BacktestRunSpec, *, suffix: str, data_snapshot_id: str) -> BacktestRunSpec:
    return replace(run, run_id=f"{run.run_id}.{suffix}", data_snapshot_id=data_snapshot_id)


def _entry_quantity(
    row: BaselineFeatureRow,
    policy: GovernedStrategyPolicy,
) -> int:
    budget = policy.portfolio.unit_notional * policy.portfolio.trial_position_fraction
    protected_price = row.close_price * (Decimal("1") + policy.portfolio.sizing_cash_buffer)
    lots = int(budget / protected_price) // policy.portfolio.entry_lot_size
    return lots * policy.portfolio.entry_lot_size


def _build_signal(
    *,
    run: BacktestRunSpec,
    frame: StrategyReplayFrame,
    policy: GovernedStrategyPolicy,
    unit_id: str,
    security_id: str,
    side: OrderSide,
    quantity: int,
    score: Decimal,
    reason_code: str,
) -> BacktestSignal:
    signal_id = _stable_id(
        "signal",
        run.run_id,
        frame.snapshot.snapshot_id,
        unit_id,
        security_id,
        side.value,
        quantity,
        reason_code,
    )
    return BacktestSignal(
        signal_id=signal_id,
        snapshot=frame.snapshot,
        decision_at=frame.decision_at,
        profile_id=run.execution_profile_id,
        window_id=None,
        security_code=security_id,
        side=side,
        unit_id=unit_id,
        quantity=quantity,
        aggregate_score=score,
        reason_code=reason_code,
        decision_version=policy.strategy_version,
    )


def _evidence(
    kind: PositionEvidenceKind,
    evidence_id: str,
    source_version: str,
) -> PositionEvidenceRef:
    return PositionEvidenceRef(kind=kind, evidence_id=evidence_id, source_version=source_version)


def _transition(
    *,
    kind: PositionTransitionKind,
    lifecycle: PositionLifecycleState,
    opportunity_id: str,
    security_id: str,
    occurred_at: datetime,
    reason_codes: tuple[str, ...],
    evidence: tuple[PositionEvidenceRef, ...],
) -> PositionTransition:
    return PositionTransition(
        transition_id=_stable_id(
            "transition",
            lifecycle.unit_id,
            kind.value,
            opportunity_id,
            security_id,
            occurred_at,
            evidence,
        ),
        kind=kind,
        unit_id=lifecycle.unit_id,
        opportunity_id=opportunity_id,
        security_code=security_id,
        occurred_at=occurred_at,
        reason_codes=reason_codes,
        evidence=evidence,
    )


@dataclass(frozen=True)
class PortfolioEquityPoint:
    market_date: date
    system_equity: Decimal
    market_value: Decimal
    invested_capital_ratio: Decimal
    active_unit_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_date": self.market_date.isoformat(),
            "system_equity": decimal_text(self.system_equity),
            "market_value": decimal_text(self.market_value),
            "invested_capital_ratio": decimal_text(self.invested_capital_ratio),
            "active_unit_count": self.active_unit_count,
        }


@dataclass(frozen=True)
class PortfolioTrade:
    unit_id: str
    opportunity_id: str
    security_id: str
    entry_date: date
    exit_date: date
    entry_quantity: int
    exit_quantity: int
    entry_gross: Decimal
    entry_fees: Decimal
    exit_gross: Decimal
    exit_fees: Decimal
    dividends: Decimal
    pnl: Decimal
    return_rate: Decimal
    holding_sessions: int
    entry_breadth_regime: str
    exit_sources: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "opportunity_id": self.opportunity_id,
            "security_id": self.security_id,
            "entry_date": self.entry_date.isoformat(),
            "exit_date": self.exit_date.isoformat(),
            "entry_quantity": self.entry_quantity,
            "exit_quantity": self.exit_quantity,
            "entry_gross": decimal_text(self.entry_gross),
            "entry_fees": decimal_text(self.entry_fees),
            "exit_gross": decimal_text(self.exit_gross),
            "exit_fees": decimal_text(self.exit_fees),
            "dividends": decimal_text(self.dividends),
            "pnl": decimal_text(self.pnl),
            "return_rate": decimal_text(self.return_rate),
            "holding_sessions": self.holding_sessions,
            "entry_breadth_regime": self.entry_breadth_regime,
            "exit_sources": list(self.exit_sources),
        }


@dataclass(frozen=True)
class PortfolioPerformanceReport:
    initial_equity: Decimal
    ending_equity: Decimal
    total_pnl: Decimal
    total_return: Decimal
    maximum_drawdown: Decimal
    annualized_volatility: Decimal
    sharpe_ratio: Decimal | None
    closed_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: Decimal | None
    payoff_ratio: Decimal | None
    profit_factor: Decimal | None
    operation_count: int
    buy_operation_count: int
    sell_operation_count: int
    turnover: Decimal
    total_fees: Decimal
    exposure_rate: Decimal
    average_invested_capital_ratio: Decimal
    average_holding_sessions: Decimal | None
    benchmark_total_return: Decimal
    excess_total_return: Decimal
    capital_matched_benchmark_total_return: Decimal
    capital_matched_excess_total_return: Decimal
    capital_matched_benchmark_path_digest: str
    top_3_closed_trade_count: int
    top_3_closed_trade_pnl: Decimal
    top_3_contribution_ratio: Decimal | None
    return_excluding_top_3_closed_trades: Decimal

    def to_dict(self) -> dict[str, Any]:
        def optional(value: Decimal | None) -> str | None:
            return None if value is None else decimal_text(value)

        return {
            "initial_equity": decimal_text(self.initial_equity),
            "ending_equity": decimal_text(self.ending_equity),
            "total_pnl": decimal_text(self.total_pnl),
            "total_return": decimal_text(self.total_return),
            "maximum_drawdown": decimal_text(self.maximum_drawdown),
            "annualized_volatility": decimal_text(self.annualized_volatility),
            "sharpe_ratio": optional(self.sharpe_ratio),
            "closed_trades": self.closed_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": optional(self.win_rate),
            "payoff_ratio": optional(self.payoff_ratio),
            "profit_factor": optional(self.profit_factor),
            "operation_count": self.operation_count,
            "buy_operation_count": self.buy_operation_count,
            "sell_operation_count": self.sell_operation_count,
            "turnover": decimal_text(self.turnover),
            "total_fees": decimal_text(self.total_fees),
            "exposure_rate": decimal_text(self.exposure_rate),
            "average_invested_capital_ratio": decimal_text(self.average_invested_capital_ratio),
            "average_holding_sessions": optional(self.average_holding_sessions),
            "benchmark_total_return": decimal_text(self.benchmark_total_return),
            "excess_total_return": decimal_text(self.excess_total_return),
            "capital_matched_benchmark_total_return": decimal_text(
                self.capital_matched_benchmark_total_return
            ),
            "capital_matched_excess_total_return": decimal_text(
                self.capital_matched_excess_total_return
            ),
            "capital_matched_benchmark_path_digest": (self.capital_matched_benchmark_path_digest),
            "top_3_closed_trade_count": self.top_3_closed_trade_count,
            "top_3_closed_trade_pnl": decimal_text(self.top_3_closed_trade_pnl),
            "top_3_contribution_ratio": optional(self.top_3_contribution_ratio),
            "return_excluding_top_3_closed_trades": decimal_text(
                self.return_excluding_top_3_closed_trades
            ),
            "compound_nav": False,
        }


@dataclass(frozen=True)
class PortfolioValidationResult:
    gate_name: str
    passed: bool
    actual: str
    required: str
    severity: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_name": self.gate_name,
            "passed": self.passed,
            "actual": self.actual,
            "required": self.required,
            "severity": self.severity,
        }


@dataclass(frozen=True)
class PortfolioReplayResult:
    run: BacktestRunSpec
    strategy_id: str
    strategy_version: str
    strategy_digest: str
    scope_start: date
    scope_end: date
    decision_events: tuple[DecisionEvent, ...]
    lifecycle_transitions: tuple[PositionTransition, ...]
    engine_events: tuple[EngineEvent, ...]
    equity_curve: tuple[PortfolioEquityPoint, ...]
    trades: tuple[PortfolioTrade, ...]
    performance: PortfolioPerformanceReport
    breakdowns: Mapping[str, tuple[Mapping[str, Any], ...]]
    opportunity_funnel: OpportunityFunnelReport
    final_ledgers: tuple[UnitState, ...]
    final_lifecycles: tuple[PositionLifecycleState, ...]
    validation: tuple[PortfolioValidationResult, ...]

    @property
    def accepted(self) -> bool:
        return all(item.passed for item in self.validation if item.severity == "gate")

    @property
    def digest(self) -> str:
        return _digest(self.payload_dict())

    def payload_dict(self) -> dict[str, Any]:
        return {
            "runner_version": PORTFOLIO_REPLAY_VERSION,
            "run_manifest": self.run.manifest(
                {
                    "accepted": self.accepted,
                    "ending_equity": decimal_text(self.performance.ending_equity),
                    "total_return": decimal_text(self.performance.total_return),
                    "closed_trades": self.performance.closed_trades,
                }
            ),
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "strategy_digest": self.strategy_digest,
            "scope": {"start": self.scope_start.isoformat(), "end": self.scope_end.isoformat()},
            "decision_events": [item.to_dict() for item in self.decision_events],
            "lifecycle_transitions": [item.to_dict() for item in self.lifecycle_transitions],
            "engine_events": [item.to_dict() for item in self.engine_events],
            "equity_curve": [item.to_dict() for item in self.equity_curve],
            "trades": [item.to_dict() for item in self.trades],
            "performance": self.performance.to_dict(),
            "breakdowns": {
                key: [dict(item) for item in values]
                for key, values in sorted(self.breakdowns.items())
            },
            "opportunity_funnel": self.opportunity_funnel.to_dict(),
            "final_ledgers": [_ledger_dict(item) for item in self.final_ledgers],
            "final_lifecycles": [item.to_dict() for item in self.final_lifecycles],
            "validation": [item.to_dict() for item in self.validation],
            "accepted": self.accepted,
            "research_only": True,
            "execution_authorized": False,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.payload_dict(), "digest": self.digest}

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())


def _ledger_dict(state: UnitState) -> dict[str, Any]:
    return {
        "unit_id": state.unit_id,
        "target_notional": decimal_text(state.target_notional),
        "cash": decimal_text(state.cash),
        "security_code": state.security_code,
        "settled_quantity": state.settled_quantity,
        "position_cost": decimal_text(state.position_cost),
        "commission_expense": decimal_text(state.commission_expense),
        "stamp_duty_expense": decimal_text(state.stamp_duty_expense),
        "transfer_fee_expense": decimal_text(state.transfer_fee_expense),
        "realized_pnl": decimal_text(state.realized_pnl),
        "dividend_income": decimal_text(state.dividend_income),
        "external_pool": decimal_text(state.external_pool),
        "last_mark_price": (
            None if state.last_mark_price is None else decimal_text(state.last_mark_price)
        ),
        "last_mark_snapshot_id": state.last_mark_snapshot_id,
    }


@dataclass
class _OpenPortfolioTrade:
    unit_id: str
    opportunity_id: str
    security_id: str
    entry_date: date
    entry_quantity: int
    entry_gross: Decimal
    entry_fees: Decimal
    entry_breadth_regime: str
    dividends: Decimal = Decimal("0.00")
    holding_sessions: int = 1
    exit_sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class _ExitSchedule:
    states_by_date: Mapping[date, UnitState]
    carry_events_by_date: Mapping[date, EngineEvent]
    fill_events_by_date: Mapping[date, EngineEvent]
    dividend_by_date: Mapping[date, Decimal]
    events: tuple[EngineEvent, ...]
    signal_id: str
    order_ids: tuple[str, ...]


@dataclass
class _UnitRuntime:
    ledger: UnitState
    lifecycle: PositionLifecycleState
    open_trade: _OpenPortfolioTrade | None = None
    deterioration_streak: int = 0
    cooldown_sessions: int = 0
    exit_schedule: _ExitSchedule | None = None


@dataclass
class _CandidateRecord:
    decision: GovernedCandidateDecision
    opportunity_id: str
    candidate_event_id: str
    score_event_id: str
    allocation_event_id: str
    decision_event_id: str | None = None
    entry_requested: bool = False
    allocation: EntryAllocationDecision | None = None
    entry_filled: bool = False


@dataclass
class _ReviewRecord:
    review: GovernedPositionReview
    input_event_id: str
    decision_event_id: str
    exit_requested: bool


def _trade_from_sell(
    open_trade: _OpenPortfolioTrade,
    fill_event: EngineEvent,
    exit_date: date,
) -> PortfolioTrade:
    fill = fill_event.payload
    exit_gross = Decimal(str(fill["gross_amount"]))
    exit_fees = Decimal(str(fill["total_fees"]))
    pnl = _money(
        exit_gross
        - exit_fees
        + open_trade.dividends
        - open_trade.entry_gross
        - open_trade.entry_fees
    )
    deployed = open_trade.entry_gross + open_trade.entry_fees
    return PortfolioTrade(
        unit_id=open_trade.unit_id,
        opportunity_id=open_trade.opportunity_id,
        security_id=open_trade.security_id,
        entry_date=open_trade.entry_date,
        exit_date=exit_date,
        entry_quantity=open_trade.entry_quantity,
        exit_quantity=int(fill["quantity"]),
        entry_gross=open_trade.entry_gross,
        entry_fees=open_trade.entry_fees,
        exit_gross=exit_gross,
        exit_fees=exit_fees,
        dividends=open_trade.dividends,
        pnl=pnl,
        return_rate=_rate(pnl / deployed),
        holding_sessions=open_trade.holding_sessions,
        entry_breadth_regime=open_trade.entry_breadth_regime,
        exit_sources=open_trade.exit_sources,
    )


def _build_performance(
    *,
    equity_curve: tuple[PortfolioEquityPoint, ...],
    trades: tuple[PortfolioTrade, ...],
    engine_events: tuple[EngineEvent, ...],
    benchmark: BenchmarkCurve,
    unit_count: int,
) -> PortfolioPerformanceReport:
    if len(equity_curve) < 2:
        raise PortfolioReplayError("portfolio performance requires replay observations")
    initial = equity_curve[0].system_equity
    ending = equity_curve[-1].system_equity
    total_pnl = _money(ending - initial)
    total_return = _rate(total_pnl / initial)
    peak = initial
    maximum_drawdown = Decimal("0")
    returns: list[Decimal] = []
    for previous, current in zip(equity_curve, equity_curve[1:], strict=False):
        returns.append(current.system_equity / previous.system_equity - Decimal("1"))
        peak = max(peak, current.system_equity)
        maximum_drawdown = max(maximum_drawdown, (peak - current.system_equity) / peak)
    if len(returns) < 2:
        annualized_volatility = Decimal("0")
        sharpe_ratio = None
    else:
        mean_return = sum(returns, Decimal("0")) / Decimal(len(returns))
        variance = sum(((value - mean_return) ** 2 for value in returns), Decimal("0")) / Decimal(
            len(returns) - 1
        )
        standard_deviation = variance.sqrt()
        annualized_volatility = standard_deviation * TRADING_PERIODS_PER_YEAR.sqrt()
        sharpe_ratio = (
            None
            if standard_deviation == 0
            else _rate(mean_return / standard_deviation * TRADING_PERIODS_PER_YEAR.sqrt())
        )

    wins = tuple(item.pnl for item in trades if item.pnl > 0)
    losses = tuple(item.pnl for item in trades if item.pnl < 0)
    closed_trades = len(trades)
    win_rate = None if not trades else _rate(Decimal(len(wins)) / Decimal(closed_trades))
    payoff_ratio = None
    if wins and losses:
        payoff_ratio = _rate(
            (sum(wins, Decimal("0")) / Decimal(len(wins)))
            / abs(sum(losses, Decimal("0")) / Decimal(len(losses)))
        )
    profit_factor = (
        None if not losses else _rate(sum(wins, Decimal("0")) / abs(sum(losses, Decimal("0"))))
    )
    average_holding = (
        None
        if not trades
        else _rate(Decimal(sum(item.holding_sessions for item in trades)) / Decimal(len(trades)))
    )

    fills = tuple(item for item in engine_events if item.event_type is EventType.FILL)
    gross_turnover = sum(
        (Decimal(str(item.payload["gross_amount"])) for item in fills), Decimal("0")
    )
    total_fees = _money(
        sum((Decimal(str(item.payload["total_fees"])) for item in fills), Decimal("0"))
    )
    replay_points = equity_curve[1:]
    exposure_rate = _rate(
        sum((Decimal(item.active_unit_count) for item in replay_points), Decimal("0"))
        / Decimal(len(replay_points) * unit_count)
    )
    average_invested = _rate(
        sum((item.invested_capital_ratio for item in replay_points), Decimal("0"))
        / Decimal(len(replay_points))
    )

    benchmark_dates = tuple(item.market_date for item in equity_curve)
    benchmark_comparison = benchmark.compare(benchmark_dates)
    capital_comparison = benchmark.compare_capital_path(
        tuple(
            BenchmarkCapitalWeight(
                market_date=item.market_date,
                invested_ratio=item.invested_capital_ratio,
            )
            for item in equity_curve
        )
    )
    if capital_comparison.average_invested_ratio != average_invested:
        raise PortfolioReplayError("capital-matched benchmark does not reconcile to portfolio")

    top_trades = tuple(sorted(trades, key=lambda item: item.pnl, reverse=True)[:3])
    top_pnl = _money(sum((item.pnl for item in top_trades), Decimal("0")))
    top_contribution = None if total_pnl == 0 else _rate(top_pnl / total_pnl)
    return PortfolioPerformanceReport(
        initial_equity=initial,
        ending_equity=ending,
        total_pnl=total_pnl,
        total_return=total_return,
        maximum_drawdown=_rate(maximum_drawdown),
        annualized_volatility=_rate(annualized_volatility),
        sharpe_ratio=sharpe_ratio,
        closed_trades=closed_trades,
        winning_trades=len(wins),
        losing_trades=len(losses),
        win_rate=win_rate,
        payoff_ratio=payoff_ratio,
        profit_factor=profit_factor,
        operation_count=len(fills),
        buy_operation_count=sum(item.payload.get("side") == "buy" for item in fills),
        sell_operation_count=sum(item.payload.get("side") == "sell" for item in fills),
        turnover=_rate(gross_turnover / initial),
        total_fees=total_fees,
        exposure_rate=exposure_rate,
        average_invested_capital_ratio=average_invested,
        average_holding_sessions=average_holding,
        benchmark_total_return=benchmark_comparison.total_return,
        excess_total_return=_rate(total_return - benchmark_comparison.total_return),
        capital_matched_benchmark_total_return=capital_comparison.total_return,
        capital_matched_excess_total_return=_rate(total_return - capital_comparison.total_return),
        capital_matched_benchmark_path_digest=capital_comparison.capital_path_digest,
        top_3_closed_trade_count=len(top_trades),
        top_3_closed_trade_pnl=top_pnl,
        top_3_contribution_ratio=top_contribution,
        return_excluding_top_3_closed_trades=_rate((total_pnl - top_pnl) / initial),
    )


def _trade_breakdown(
    trades: Sequence[PortfolioTrade],
    key: str,
) -> tuple[Mapping[str, Any], ...]:
    groups: dict[str, list[PortfolioTrade]] = defaultdict(list)
    for trade in trades:
        if key == "market_breadth_regime":
            name = trade.entry_breadth_regime
        elif key == "holding_duration":
            if trade.holding_sessions <= 5:
                name = "1-5"
            elif trade.holding_sessions <= 10:
                name = "6-10"
            elif trade.holding_sessions <= 20:
                name = "11-20"
            else:
                name = "21+"
        elif key == "exit_source":
            name = "+".join(trade.exit_sources) if trade.exit_sources else "unclassified"
        else:
            raise PortfolioReplayError(f"unsupported breakdown: {key}")
        groups[name].append(trade)
    rows: list[Mapping[str, Any]] = []
    for name, group in sorted(groups.items()):
        pnl = _money(sum((item.pnl for item in group), Decimal("0")))
        wins = sum(item.pnl > 0 for item in group)
        rows.append(
            {
                "bucket": name,
                "closed_trades": len(group),
                "wins": wins,
                "win_rate": decimal_text(_rate(Decimal(wins) / Decimal(len(group)))),
                "pnl": decimal_text(pnl),
            }
        )
    return tuple(rows)


def _build_breakdowns(
    equity_curve: tuple[PortfolioEquityPoint, ...],
    trades: tuple[PortfolioTrade, ...],
) -> Mapping[str, tuple[Mapping[str, Any], ...]]:
    annual_rows: list[Mapping[str, Any]] = []
    points_by_year: dict[int, list[PortfolioEquityPoint]] = defaultdict(list)
    for point in equity_curve:
        points_by_year[point.market_date.year].append(point)
    prior_close = equity_curve[0].system_equity
    for year, points in sorted(points_by_year.items()):
        ending = points[-1].system_equity
        year_trades = tuple(item for item in trades if item.exit_date.year == year)
        annual_rows.append(
            {
                "bucket": str(year),
                "starting_equity": decimal_text(prior_close),
                "ending_equity": decimal_text(ending),
                "total_return": decimal_text(_rate(ending / prior_close - Decimal("1"))),
                "closed_trades": len(year_trades),
                "trade_pnl": decimal_text(
                    _money(sum((item.pnl for item in year_trades), Decimal("0")))
                ),
            }
        )
        prior_close = ending
    breakdowns = {
        key: _trade_breakdown(trades, key)
        for key in (
            "market_breadth_regime",
            "holding_duration",
            "exit_source",
        )
    }
    return {"calendar_year": tuple(annual_rows), **breakdowns}


def _build_validation(
    performance: PortfolioPerformanceReport,
    policy: GovernedStrategyPolicy,
) -> tuple[PortfolioValidationResult, ...]:
    evaluation = policy.evaluation
    return (
        PortfolioValidationResult(
            gate_name="minimum_closed_trades",
            passed=performance.closed_trades >= evaluation.minimum_closed_trades,
            actual=str(performance.closed_trades),
            required=f">={evaluation.minimum_closed_trades}",
            severity="gate",
        ),
        PortfolioValidationResult(
            gate_name="capital_matched_excess",
            passed=(
                performance.capital_matched_excess_total_return
                >= evaluation.minimum_capital_matched_excess
            ),
            actual=decimal_text(performance.capital_matched_excess_total_return),
            required=f">={evaluation.minimum_capital_matched_excess}",
            severity="gate",
        ),
        PortfolioValidationResult(
            gate_name="return_excluding_top3",
            passed=(
                performance.return_excluding_top_3_closed_trades
                >= evaluation.minimum_return_excluding_top3
            ),
            actual=decimal_text(performance.return_excluding_top_3_closed_trades),
            required=f">={evaluation.minimum_return_excluding_top3}",
            severity="gate",
        ),
        PortfolioValidationResult(
            gate_name="maximum_drawdown_warning",
            passed=performance.maximum_drawdown <= evaluation.maximum_drawdown_warning,
            actual=decimal_text(performance.maximum_drawdown),
            required=f"<={evaluation.maximum_drawdown_warning}",
            severity="warning",
        ),
    )


def _build_exit_schedule(
    *,
    reader_frames: Sequence[StrategyReplayFrame],
    frame_index: int,
    runtime: _UnitRuntime,
    frame: StrategyReplayFrame,
    run: BacktestRunSpec,
    engine: BacktestEngine,
    policy: GovernedStrategyPolicy,
    review: GovernedPositionReview,
) -> _ExitSchedule:
    state = runtime.ledger
    if state.security_code is None or state.last_mark_price is None:
        raise PortfolioReplayError("exit schedule requires a marked position")
    security_id = state.security_code
    signal = _build_signal(
        run=run,
        frame=frame,
        policy=policy,
        unit_id=runtime.lifecycle.unit_id,
        security_id=security_id,
        side=OrderSide.SELL,
        quantity=state.settled_quantity,
        score=Decimal("-1"),
        reason_code=review.arbitration.reason_codes[0],
    )
    sessions = []
    actions: list[CorporateActionStep] = []
    mark_cursor = state.last_mark_price
    for candidate_index in range(frame_index, len(reader_frames)):
        candidate = reader_frames[candidate_index]
        block_reason = candidate.block_reason(security_id)
        if block_reason is not None:
            raise PortfolioReplayError(
                f"held security {security_id} is blocked by replay evidence: {block_reason}"
            )
        candidate_actions = candidate.actions(security_id, mark_cursor)
        if any(step.action.bonus_share_ratio != 0 for step in candidate_actions):
            raise PortfolioReplayError(
                "bonus-share corporate action during a carried sell requires explicit "
                "lifecycle quantity reconciliation"
            )
        actions.extend(candidate_actions)
        if candidate_actions:
            mark_cursor = candidate_actions[-1].post_action_mark_price
        session = candidate.session(security_id)
        sessions.append(session)
        if session.close_price is not None:
            mark_cursor = session.close_price
        if session.open_execution_state not in {
            OpenExecutionState.SUSPENDED,
            OpenExecutionState.SELL_BLOCKED_LIMIT_DOWN,
            OpenExecutionState.MISSING_EVIDENCE,
        }:
            break
    result = engine.run_sell_sequence(
        run=_child_run(
            run,
            suffix=f"{runtime.lifecycle.unit_id}.exit.{frame.snapshot.market_date.isoformat()}",
            data_snapshot_id=frame.snapshot.snapshot_id,
        ),
        signal=signal,
        sessions=tuple(sessions),
        opening_state=state,
        corporate_actions=tuple(actions),
    )
    states_by_date: dict[date, UnitState] = {}
    carry_events: dict[date, EngineEvent] = {}
    fill_events: dict[date, EngineEvent] = {}
    dividend_by_date: dict[date, Decimal] = defaultdict(lambda: Decimal("0.00"))
    for event in result.events:
        if event.event_type is EventType.DAILY_SNAPSHOT:
            market_date = date.fromisoformat(str(event.payload["session_date"]))
            states_by_date[market_date] = _state_from_snapshot(
                event,
                target_notional=policy.portfolio.unit_notional,
            )
        elif event.event_type is EventType.ORDER_STATUS and str(
            event.payload.get("transition", "")
        ).endswith("to_carried"):
            carry_events[event.event_time.date()] = event
        elif event.event_type is EventType.FILL and event.payload.get("side") == "sell":
            fill_events[event.event_time.date()] = event
        elif event.event_type is EventType.CORPORATE_ACTION:
            market_date = date.fromisoformat(str(event.payload["effective_date"]))
            dividend_by_date[market_date] = _money(
                dividend_by_date[market_date] + Decimal(str(event.payload["cash_dividend"]))
            )
    if tuple(states_by_date) != tuple(session.session_date for session in sessions):
        raise PortfolioReplayError("P2 sell sequence omitted a scheduled daily snapshot")
    return _ExitSchedule(
        states_by_date=states_by_date,
        carry_events_by_date=carry_events,
        fill_events_by_date=fill_events,
        dividend_by_date=dict(dividend_by_date),
        events=result.events,
        signal_id=signal.signal_id,
        order_ids=tuple(
            str(item.payload["order_id"])
            for item in result.events
            if item.event_type is EventType.ORDER_CREATED
        ),
    )


def _build_funnel(
    *,
    run_id: str,
    scope_start: date,
    scope_end: date,
    candidates: Sequence[_CandidateRecord],
    reviews: Sequence[_ReviewRecord],
    closed_opportunities: set[str],
) -> OpportunityFunnelReport:
    observations: list[FunnelObservation] = []
    expected: dict[FunnelStage, list[str]] = {stage: [] for stage in FunnelStage}
    first_review_by_opportunity: dict[str, _ReviewRecord] = {}
    for review_record in reviews:
        first_review_by_opportunity.setdefault(review_record.review.opportunity_id, review_record)

    for candidate_record in candidates:
        market_date = candidate_record.decision.market_date
        outcome_ref = candidate_record.decision_event_id or candidate_record.decision.digest
        expected[FunnelStage.CANDIDATE_TO_SCORE].append(candidate_record.candidate_event_id)
        observations.append(
            FunnelObservation(
                market_date=market_date,
                stage=FunnelStage.CANDIDATE_TO_SCORE,
                opportunity_id=candidate_record.opportunity_id,
                input_event_id=candidate_record.candidate_event_id,
                disposition=FunnelDisposition.ADVANCED,
                reason_codes=("candidate_evaluated",),
                output_event_id=candidate_record.score_event_id,
                decision_id=candidate_record.decision_event_id,
                outcome_kind=FunnelOutcomeKind.DIAGNOSTIC,
                outcome_ref=outcome_ref,
            )
        )
        expected[FunnelStage.SCORE_TO_VETO].append(candidate_record.score_event_id)
        observations.append(
            FunnelObservation(
                market_date=market_date,
                stage=FunnelStage.SCORE_TO_VETO,
                opportunity_id=candidate_record.opportunity_id,
                input_event_id=candidate_record.score_event_id,
                disposition=(
                    FunnelDisposition.ADVANCED
                    if candidate_record.entry_requested
                    else FunnelDisposition.STOPPED
                ),
                reason_codes=candidate_record.decision.action.reason_codes,
                output_event_id=(
                    candidate_record.allocation_event_id
                    if candidate_record.entry_requested
                    else None
                ),
                decision_id=candidate_record.decision_event_id,
                outcome_kind=FunnelOutcomeKind.DIAGNOSTIC,
                outcome_ref=outcome_ref,
            )
        )
        if not candidate_record.entry_requested:
            continue
        expected[FunnelStage.ENTRY_TO_ADD].append(candidate_record.allocation_event_id)
        first_review = first_review_by_opportunity.get(candidate_record.opportunity_id)
        observations.append(
            FunnelObservation(
                market_date=market_date,
                stage=FunnelStage.ENTRY_TO_ADD,
                opportunity_id=candidate_record.opportunity_id,
                input_event_id=candidate_record.allocation_event_id,
                disposition=(
                    FunnelDisposition.ADVANCED
                    if candidate_record.entry_filled and first_review is not None
                    else FunnelDisposition.STOPPED
                    if not candidate_record.entry_filled
                    else FunnelDisposition.UNAVAILABLE
                ),
                reason_codes=(
                    ("entry_filled",)
                    if candidate_record.entry_filled and first_review is not None
                    else ("entry_not_filled",)
                    if not candidate_record.entry_filled
                    else ("held_review_outside_scope",)
                ),
                output_event_id=(
                    first_review.input_event_id
                    if candidate_record.entry_filled and first_review is not None
                    else None
                ),
                decision_id=candidate_record.decision_event_id,
                outcome_kind=(
                    FunnelOutcomeKind.REALIZED
                    if candidate_record.entry_filled
                    else FunnelOutcomeKind.DIAGNOSTIC
                ),
                outcome_ref=outcome_ref,
            )
        )

    for review_record in reviews:
        expected[FunnelStage.HOLD_TO_EXIT].append(review_record.input_event_id)
        closed = review_record.review.opportunity_id in closed_opportunities
        observations.append(
            FunnelObservation(
                market_date=review_record.review.market_date,
                stage=FunnelStage.HOLD_TO_EXIT,
                opportunity_id=review_record.review.opportunity_id,
                input_event_id=review_record.input_event_id,
                disposition=(
                    FunnelDisposition.ADVANCED
                    if review_record.exit_requested
                    else FunnelDisposition.STOPPED
                ),
                reason_codes=review_record.review.arbitration.reason_codes,
                output_event_id=(
                    review_record.decision_event_id if review_record.exit_requested else None
                ),
                decision_id=review_record.decision_event_id,
                outcome_kind=(FunnelOutcomeKind.REALIZED if closed else FunnelOutcomeKind.PENDING),
                outcome_ref=(review_record.decision_event_id if closed else None),
            )
        )
    return build_opportunity_funnel(
        run_id=run_id,
        period_start=scope_start,
        period_end=scope_end,
        observations=tuple(observations),
        expected_input_event_ids={stage: tuple(values) for stage, values in expected.items()},
    )


def _make_run(
    *,
    reader: HistoricalReplayFrameReader,
    engine: BacktestEngine,
    panel: BaselineFeaturePanel,
    feature_registry: FeatureRegistry,
    policy: GovernedStrategyPolicy,
    scope_start: date,
    scope_end: date,
) -> BacktestRunSpec:
    return BacktestRunSpec(
        code_version=f"governed-portfolio-runner-{PORTFOLIO_REPLAY_VERSION}",
        parameter_hash=policy.digest,
        data_snapshot_id=reader.bundle.snapshot.snapshot_id,
        feature_version=(
            f"{feature_registry.registry_version}|{feature_registry.digest}|"
            f"panel={panel.report.digest}"
        ),
        rule_version=policy.strategy_version,
        run_id=(
            f"{policy.strategy_id}.{policy.strategy_version}.portfolio."
            f"{scope_start.strftime('%Y%m%d')}-{scope_end.strftime('%Y%m%d')}"
        ),
        execution_policy_version=engine.policy.policy_version,
        execution_profile_id=engine.policy.active_profile.profile_id,
        schedule_version=engine.policy.active_profile.schedule_version,
        official=False,
    )


def run_governed_portfolio_replay(
    reader: HistoricalReplayFrameReader,
    engine: BacktestEngine,
    panel: BaselineFeaturePanel,
    feature_registry: FeatureRegistry,
    label_registry: LabelRegistry,
    policy: GovernedStrategyPolicy,
    p0_policy: P0Policy,
    industry_mapping: StaticIndustryMapping,
    benchmark: BenchmarkCurve,
    *,
    scope_start: date,
    scope_end: date,
) -> PortfolioReplayResult:
    """Replay the frozen five-unit candidate only inside its development partition."""

    policy.validation.require_development_scope(scope_start, scope_end)
    if policy.bindings.execution_policy_version != engine.policy.policy_version:
        raise PortfolioReplayError("strategy and P2 execution policy versions do not match")
    if policy.bindings.p0_policy_version != p0_policy.version:
        raise PortfolioReplayError("strategy and P0 policy versions do not match")
    if policy.bindings.feature_registry_version != feature_registry.registry_version:
        raise PortfolioReplayError("strategy and governed feature registry versions do not match")
    if policy.bindings.label_registry_version != label_registry.registry_version:
        raise PortfolioReplayError("strategy and label registry versions do not match")
    if policy.bindings.industry_mapping_version != industry_mapping.mapping_version:
        raise PortfolioReplayError("strategy and industry mapping versions do not match")
    if policy.bindings.position_state_machine_version != POSITION_STATE_MACHINE_VERSION:
        raise PortfolioReplayError("strategy and position lifecycle versions do not match")
    if panel.report.bundle_digest != reader.bundle.digest:
        raise PortfolioReplayError("feature panel does not bind the historical replay bundle")
    if benchmark.instrument_id != "sh000300":
        raise PortfolioReplayError("governed replay benchmark must be CSI 300 sh000300")

    frames = reader.frames(scope_start, scope_end)
    if frames[-1].execution_date > policy.validation.development_end:
        raise PortfolioReplayError("the final decision would execute outside development data")
    if panel.report.scope_end < frames[-1].execution_date:
        raise PortfolioReplayError("feature panel must include the final execution session")
    run = _make_run(
        reader=reader,
        engine=engine,
        panel=panel,
        feature_registry=feature_registry,
        policy=policy,
        scope_start=scope_start,
        scope_end=scope_end,
    )
    initial_equity = policy.portfolio.unit_notional * policy.portfolio.unit_count
    initial_as_of = frames[0].decision_at - timedelta(microseconds=1)
    runtimes: dict[str, _UnitRuntime] = {}
    for number in range(1, policy.portfolio.unit_count + 1):
        unit_id = f"capital-unit-{number:02d}"
        ledger = UnitState.empty(unit_id=unit_id, cash=policy.portfolio.unit_notional)
        runtimes[unit_id] = _UnitRuntime(
            ledger=ledger,
            lifecycle=initialize_position_lifecycle(
                ledger_state=ledger,
                as_of=initial_as_of,
            ),
        )

    equity_curve = [
        PortfolioEquityPoint(
            market_date=frames[0].snapshot.market_date,
            system_equity=initial_equity,
            market_value=Decimal("0.00"),
            invested_capital_ratio=Decimal("0"),
            active_unit_count=0,
        )
    ]
    decisions: list[DecisionEvent] = []
    transitions: list[PositionTransition] = []
    engine_events: list[EngineEvent] = []
    trades: list[PortfolioTrade] = []
    candidate_records: list[_CandidateRecord] = []
    review_records: list[_ReviewRecord] = []

    for frame_index, frame in enumerate(frames):
        market_date = frame.snapshot.market_date
        breadth = panel.market_breadth_above_sma60(market_date)

        # T-close reviews create one immutable exit order at most once per position.
        for unit_id in sorted(runtimes):
            runtime = runtimes[unit_id]
            if runtime.lifecycle.stage not in {PositionStage.TRIAL, PositionStage.DYNAMIC}:
                continue
            if runtime.ledger.security_code is None or runtime.open_trade is None:
                raise PortfolioReplayError("active lifecycle is missing its trade identity")
            row = panel.feature(market_date, runtime.ledger.security_code)
            if row is None:
                raise PortfolioReplayError(
                    f"held security {runtime.ledger.security_code} has no feature row "
                    f"on {market_date}"
                )
            deployed = runtime.open_trade.entry_gross + runtime.open_trade.entry_fees
            marked_return = _rate(
                (_market_value(runtime.ledger) + runtime.open_trade.dividends - deployed) / deployed
            )
            review = evaluate_governed_position_review(
                frame=frame,
                row=row,
                policy=policy,
                p0_policy=p0_policy,
                unit_id=unit_id,
                opportunity_id=runtime.open_trade.opportunity_id,
                lifecycle_stage=runtime.lifecycle.stage,
                holding_sessions=runtime.open_trade.holding_sessions,
                prior_deterioration_streak=runtime.deterioration_streak,
                marked_return=marked_return,
            )
            runtime.deterioration_streak = review.deterioration_streak
            exit_requested = review.arbitration.exit_requested
            exit_schedule: _ExitSchedule | None = None
            if exit_requested:
                runtime.open_trade.exit_sources = tuple(
                    item.value for item in review.arbitration.triggered_sources
                )
                exit_schedule = _build_exit_schedule(
                    reader_frames=frames,
                    frame_index=frame_index,
                    runtime=runtime,
                    frame=frame,
                    run=run,
                    engine=engine,
                    policy=policy,
                    review=review,
                )
            decision_event = governed_position_decision_event(
                review=review,
                frame=frame,
                policy=policy,
                run_id=run.run_id,
                profile_id=run.execution_profile_id,
                disposition=(
                    DecisionDisposition.ACCEPTED if exit_requested else DecisionDisposition.REJECTED
                ),
                signal_id=None if exit_schedule is None else exit_schedule.signal_id,
                linked_order_ids=() if exit_schedule is None else exit_schedule.order_ids,
            )
            decisions.append(decision_event)
            review_input_id = _stable_id(
                "hold", runtime.open_trade.opportunity_id, runtime.open_trade.holding_sessions
            )
            review_records.append(
                _ReviewRecord(
                    review=review,
                    input_event_id=review_input_id,
                    decision_event_id=decision_event.decision_id,
                    exit_requested=exit_requested,
                )
            )
            if not exit_requested:
                continue
            if exit_schedule is None:
                raise PortfolioReplayError("exit request did not produce a P2 sell schedule")
            runtime.exit_schedule = exit_schedule
            engine_events.extend(exit_schedule.events)
            transition = _transition(
                kind=PositionTransitionKind.EXIT_REQUESTED,
                lifecycle=runtime.lifecycle,
                opportunity_id=runtime.open_trade.opportunity_id,
                security_id=runtime.open_trade.security_id,
                occurred_at=frame.decision_at,
                reason_codes=review.arbitration.reason_codes,
                evidence=(
                    _evidence(
                        PositionEvidenceKind.DECISION,
                        decision_event.decision_id,
                        policy.strategy_version,
                    ),
                ),
            )
            runtime.lifecycle = apply_position_transition(
                runtime.lifecycle,
                transition,
                ledger_before=runtime.ledger,
                ledger_after=runtime.ledger,
            )
            transitions.append(transition)

        # Candidate facts, labels, experts and policy are evaluated before unit competition.
        scanned_rows = panel.governed_candidate_scan(
            market_date,
            minimum_listing_sessions=policy.entry.minimum_listing_sessions,
            minimum_average_amount20=policy.entry.minimum_average_amount20,
            limit=policy.entry.candidate_scan_limit,
        )
        industry_mapping.require_coverage(tuple(row.security_id for row in scanned_rows))
        daily_records: list[_CandidateRecord] = []
        allocation_candidates: list[EntryAllocationCandidate] = []
        for row in scanned_rows:
            governed = evaluate_governed_candidate(
                reader.bundle,
                frame,
                row,
                breadth,
                policy,
                feature_registry,
                label_registry,
            )
            opportunity_id = _stable_id(
                "opportunity", run.run_id, market_date, row.security_id, governed.digest
            )
            record = _CandidateRecord(
                decision=governed,
                opportunity_id=opportunity_id,
                candidate_event_id=_stable_id("candidate", opportunity_id),
                score_event_id=_stable_id("score", opportunity_id),
                allocation_event_id=_stable_id("allocation", opportunity_id),
                entry_requested=(governed.action.final_action is GovernedAction.TRIAL_ENTRY),
            )
            daily_records.append(record)
            candidate_records.append(record)
            if record.entry_requested:
                membership = industry_mapping.by_security_id[row.security_id]
                allocation_candidates.append(
                    EntryAllocationCandidate(
                        decision_id=_stable_id("allocation-decision", opportunity_id),
                        opportunity_id=opportunity_id,
                        aggregate_score=governed.action.aggregate_score,
                        membership=membership,
                    )
                )

        active_exposures = tuple(
            ActiveUnitExposure(
                unit_id=runtime.lifecycle.unit_id,
                opportunity_id=str(runtime.lifecycle.opportunity_id),
                lifecycle_stage=runtime.lifecycle.stage,
                lifecycle_digest=runtime.lifecycle.digest,
                membership=industry_mapping.by_security_id[str(runtime.lifecycle.security_code)],
            )
            for runtime in runtimes.values()
            if runtime.lifecycle.stage
            in {PositionStage.TRIAL, PositionStage.DYNAMIC, PositionStage.EXITING}
        )
        remaining_active_slots = max(
            0,
            policy.portfolio.maximum_active_units - len(active_exposures),
        )
        available_units = tuple(
            AvailableCapitalUnit(runtime.lifecycle, runtime.ledger)
            for runtime in sorted(
                (item for item in runtimes.values() if item.lifecycle.stage is PositionStage.EMPTY),
                key=lambda item: item.lifecycle.unit_id,
            )
        )[:remaining_active_slots]
        allocation_batch = allocate_entry_reservations(
            p0_policy,
            market_date=market_date,
            candidates=tuple(allocation_candidates),
            available_units=available_units,
            active_exposures=active_exposures,
            accepted_entries=(),
        )
        allocation_by_opportunity = {
            item.opportunity_id: item for item in allocation_batch.decisions
        }
        for record in daily_records:
            record.allocation = allocation_by_opportunity.get(record.opportunity_id)

        # T+1-open execution: carried exits, ordinary marks, accepted buys, then cooldown.
        buy_record_by_unit: dict[str, _CandidateRecord] = {}
        for record in daily_records:
            allocation = record.allocation
            if (
                allocation is not None
                and allocation.disposition is AllocationDisposition.RESERVED
                and allocation.considered_unit_id is not None
            ):
                buy_record_by_unit[allocation.considered_unit_id] = record

        for unit_id in sorted(runtimes):
            runtime = runtimes[unit_id]
            execution_date = frame.execution_date
            if runtime.lifecycle.stage is PositionStage.EXITING:
                cached_schedule = runtime.exit_schedule
                if cached_schedule is None or execution_date not in cached_schedule.states_by_date:
                    raise PortfolioReplayError("exiting unit has no cached P2 order outcome")
                before = runtime.ledger
                after = cached_schedule.states_by_date[execution_date]
                if runtime.open_trade is None:
                    raise PortfolioReplayError("exiting unit is missing its open trade")
                runtime.open_trade.dividends = _money(
                    runtime.open_trade.dividends
                    + cached_schedule.dividend_by_date.get(execution_date, Decimal("0.00"))
                )
                runtime.open_trade.holding_sessions += 1
                fill_event = cached_schedule.fill_events_by_date.get(execution_date)
                if fill_event is None:
                    carry_event = cached_schedule.carry_events_by_date.get(execution_date)
                    if carry_event is None:
                        raise PortfolioReplayError("carried exit lacks an order-status event")
                    transition = _transition(
                        kind=PositionTransitionKind.EXIT_CARRIED,
                        lifecycle=runtime.lifecycle,
                        opportunity_id=runtime.open_trade.opportunity_id,
                        security_id=runtime.open_trade.security_id,
                        occurred_at=datetime.fromisoformat(
                            str(
                                next(
                                    item.payload["as_of"]
                                    for item in cached_schedule.events
                                    if item.event_type is EventType.DAILY_SNAPSHOT
                                    and item.payload["session_date"] == execution_date.isoformat()
                                )
                            )
                        ),
                        reason_codes=(carry_event.reason_code,),
                        evidence=(
                            _evidence(
                                PositionEvidenceKind.ORDER_STATUS,
                                carry_event.event_id,
                                engine.policy.policy_version,
                            ),
                        ),
                    )
                else:
                    transition = _transition(
                        kind=PositionTransitionKind.EXIT_FILLED,
                        lifecycle=runtime.lifecycle,
                        opportunity_id=runtime.open_trade.opportunity_id,
                        security_id=runtime.open_trade.security_id,
                        occurred_at=fill_event.event_time,
                        reason_codes=("full_exit_filled",),
                        evidence=(
                            _evidence(
                                PositionEvidenceKind.FILL,
                                str(fill_event.payload["fill_id"]),
                                engine.policy.policy_version,
                            ),
                        ),
                    )
                runtime.lifecycle = apply_position_transition(
                    runtime.lifecycle,
                    transition,
                    ledger_before=before,
                    ledger_after=after,
                )
                transitions.append(transition)
                runtime.ledger = after
                if fill_event is not None:
                    trades.append(_trade_from_sell(runtime.open_trade, fill_event, execution_date))
                    runtime.open_trade = None
                    runtime.exit_schedule = None
                    runtime.deterioration_streak = 0
                    runtime.cooldown_sessions = 0
                continue

            if runtime.lifecycle.stage in {PositionStage.TRIAL, PositionStage.DYNAMIC}:
                if (
                    runtime.ledger.security_code is None
                    or runtime.ledger.last_mark_price is None
                    or runtime.open_trade is None
                ):
                    raise PortfolioReplayError("positioned unit is missing mark identity")
                before = runtime.ledger
                security_id = runtime.ledger.security_code
                prior_mark = runtime.ledger.last_mark_price
                block_reason = frame.block_reason(security_id)
                if block_reason is not None:
                    raise PortfolioReplayError(
                        f"held security {before.security_code} is blocked: {block_reason}"
                    )
                for action_index, step in enumerate(
                    frame.actions(security_id, prior_mark), start=1
                ):
                    application = engine.apply_corporate_action(
                        run=_child_run(
                            run,
                            suffix=f"{unit_id}.action.{frame_index}.{action_index}",
                            data_snapshot_id=step.action.snapshot_id,
                        ),
                        opening_state=runtime.ledger,
                        action=step.action,
                        applied_at=step.applied_at,
                        post_action_mark_price=step.post_action_mark_price,
                        mark_snapshot_id=step.mark_snapshot_id,
                    )
                    runtime.ledger = application.state
                    engine_events.extend(application.events)
                    runtime.open_trade.dividends = _money(
                        runtime.open_trade.dividends
                        + Decimal(str(application.events[0].payload["cash_dividend"]))
                    )
                session = frame.session(str(runtime.ledger.security_code))
                mark = engine.mark_position_session(
                    run=_child_run(
                        run,
                        suffix=f"{unit_id}.mark.{frame_index}",
                        data_snapshot_id=session.snapshot_id,
                    ),
                    session=session,
                    opening_state=runtime.ledger,
                )
                runtime.ledger = mark.state
                engine_events.extend(mark.events)
                runtime.open_trade.holding_sessions += 1
                continue

            buy_record = buy_record_by_unit.get(unit_id)
            if runtime.lifecycle.stage is PositionStage.EMPTY and buy_record is not None:
                row = next(
                    item
                    for item in scanned_rows
                    if item.security_id == buy_record.decision.security_id
                )
                quantity = _entry_quantity(row, policy)
                if quantity <= 0:
                    event = governed_candidate_decision_event(
                        decision=buy_record.decision,
                        frame=frame,
                        policy=policy,
                        run_id=run.run_id,
                        unit_id=unit_id,
                        profile_id=run.execution_profile_id,
                        disposition=DecisionDisposition.REJECTED,
                        final_action=GovernedAction.NO_ACTION.value,
                        additional_reason_codes=("funding_below_one_lot",),
                    )
                    decisions.append(event)
                    buy_record.decision_event_id = event.decision_id
                    continue
                signal = _build_signal(
                    run=run,
                    frame=frame,
                    policy=policy,
                    unit_id=unit_id,
                    security_id=row.security_id,
                    side=OrderSide.BUY,
                    quantity=quantity,
                    score=buy_record.decision.action.aggregate_score,
                    reason_code="governed_trial_entry",
                )
                session = frame.session(row.security_id)
                ledger_before_entry = runtime.ledger
                result = engine.run_buy_session(
                    run=_child_run(
                        run,
                        suffix=f"{unit_id}.entry.{frame_index}",
                        data_snapshot_id=frame.snapshot.snapshot_id,
                    ),
                    signal=signal,
                    session=session,
                    opening_state=runtime.ledger,
                )
                engine_events.extend(result.events)
                snapshot = next(
                    item
                    for item in reversed(result.events)
                    if item.event_type is EventType.DAILY_SNAPSHOT
                )
                after = _state_from_snapshot(
                    snapshot,
                    target_notional=policy.portfolio.unit_notional,
                )
                fill = next(
                    (item for item in result.events if item.event_type is EventType.FILL), None
                )
                order_ids = tuple(
                    str(item.payload["order_id"])
                    for item in result.events
                    if item.event_type is EventType.ORDER_CREATED
                )
                event = governed_candidate_decision_event(
                    decision=buy_record.decision,
                    frame=frame,
                    policy=policy,
                    run_id=run.run_id,
                    unit_id=unit_id,
                    profile_id=run.execution_profile_id,
                    disposition=DecisionDisposition.ACCEPTED,
                    signal_id=signal.signal_id,
                    linked_order_ids=order_ids,
                    additional_reason_codes=tuple(
                        () if fill is not None else (str(result.result_summary["status"]),)
                    ),
                )
                decisions.append(event)
                buy_record.decision_event_id = event.decision_id
                runtime.ledger = after
                if fill is not None:
                    transition = _transition(
                        kind=PositionTransitionKind.ENTRY_FILLED,
                        lifecycle=runtime.lifecycle,
                        opportunity_id=buy_record.opportunity_id,
                        security_id=row.security_id,
                        occurred_at=fill.event_time,
                        reason_codes=("trial_entry_filled",),
                        evidence=(
                            _evidence(
                                PositionEvidenceKind.FILL,
                                str(fill.payload["fill_id"]),
                                engine.policy.policy_version,
                            ),
                        ),
                    )
                    runtime.lifecycle = apply_position_transition(
                        runtime.lifecycle,
                        transition,
                        ledger_before=ledger_before_entry,
                        ledger_after=after,
                    )
                    transitions.append(transition)
                    runtime.open_trade = _OpenPortfolioTrade(
                        unit_id=unit_id,
                        opportunity_id=buy_record.opportunity_id,
                        security_id=row.security_id,
                        entry_date=frame.execution_date,
                        entry_quantity=int(fill.payload["quantity"]),
                        entry_gross=Decimal(str(fill.payload["gross_amount"])),
                        entry_fees=Decimal(str(fill.payload["total_fees"])),
                        entry_breadth_regime=(
                            "supportive"
                            if breadth is not None
                            and breadth >= policy.entry.minimum_market_breadth_above_sma60
                            else "weak_or_unavailable"
                        ),
                    )
                    buy_record.entry_filled = True
                continue

            if runtime.lifecycle.stage is PositionStage.CLOSED:
                runtime.cooldown_sessions += 1
                if runtime.cooldown_sessions >= policy.portfolio.cooldown_sessions:
                    settlement_event = next(
                        (
                            item
                            for item in reversed(engine_events)
                            if item.event_type is EventType.UNIT_SETTLEMENT
                            and item.payload.get("unit_id") == unit_id
                        ),
                        None,
                    )
                    if settlement_event is None:
                        raise PortfolioReplayError("closed unit has no P2 settlement evidence")
                    opportunity_id = str(runtime.lifecycle.opportunity_id)
                    security_id = str(runtime.lifecycle.security_code)
                    transition = _transition(
                        kind=PositionTransitionKind.COOLDOWN_COMPLETED,
                        lifecycle=runtime.lifecycle,
                        opportunity_id=opportunity_id,
                        security_id=security_id,
                        occurred_at=datetime.combine(
                            frame.execution_date,
                            time(15, 0),
                            tzinfo=MARKET_TIMEZONE,
                        ),
                        reason_codes=("cooldown_completed",),
                        evidence=(
                            _evidence(
                                PositionEvidenceKind.UNIT_SETTLEMENT,
                                settlement_event.event_id,
                                engine.policy.policy_version,
                            ),
                            _evidence(
                                PositionEvidenceKind.CALENDAR,
                                _stable_id("calendar", unit_id, execution_date),
                                run.schedule_version,
                            ),
                        ),
                    )
                    runtime.lifecycle = apply_position_transition(
                        runtime.lifecycle,
                        transition,
                        ledger_before=runtime.ledger,
                        ledger_after=runtime.ledger,
                    )
                    transitions.append(transition)
                    runtime.cooldown_sessions = 0

        # Every scanned candidate receives a P2 decision, including non-entry outcomes.
        for record in daily_records:
            if record.decision_event_id is not None:
                continue
            allocation = record.allocation
            additional = (
                tuple(allocation.reason_codes)
                if allocation is not None
                else ("governed_entry_not_requested",)
            )
            event = governed_candidate_decision_event(
                decision=record.decision,
                frame=frame,
                policy=policy,
                run_id=run.run_id,
                unit_id=(
                    str(allocation.considered_unit_id)
                    if allocation is not None and allocation.considered_unit_id is not None
                    else "capital-pool"
                ),
                profile_id=run.execution_profile_id,
                disposition=DecisionDisposition.REJECTED,
                final_action=GovernedAction.NO_ACTION.value,
                additional_reason_codes=additional,
            )
            decisions.append(event)
            record.decision_event_id = event.decision_id

        total_market_value = _money(
            sum((_market_value(item.ledger) for item in runtimes.values()), Decimal("0"))
        )
        total_equity = _money(
            sum((_wealth(item.ledger) for item in runtimes.values()), Decimal("0"))
        )
        if total_equity <= 0:
            raise PortfolioReplayError("portfolio equity must remain positive")
        active_units = sum(item.ledger.settled_quantity > 0 for item in runtimes.values())
        equity_curve.append(
            PortfolioEquityPoint(
                market_date=frame.execution_date,
                system_equity=total_equity,
                market_value=total_market_value,
                invested_capital_ratio=_rate(total_market_value / total_equity),
                active_unit_count=active_units,
            )
        )

    ordered_events = tuple(sorted(engine_events, key=lambda item: (item.event_time, item.event_id)))
    ordered_trades = tuple(sorted(trades, key=lambda item: (item.exit_date, item.unit_id)))
    performance = _build_performance(
        equity_curve=tuple(equity_curve),
        trades=ordered_trades,
        engine_events=ordered_events,
        benchmark=benchmark,
        unit_count=policy.portfolio.unit_count,
    )
    closed_opportunities = {item.opportunity_id for item in ordered_trades}
    funnel = _build_funnel(
        run_id=run.run_id,
        scope_start=equity_curve[0].market_date,
        scope_end=equity_curve[-1].market_date,
        candidates=candidate_records,
        reviews=review_records,
        closed_opportunities=closed_opportunities,
    )
    if not funnel.reconciled:
        raise PortfolioReplayError("opportunity funnel did not reconcile")
    final_runtimes = tuple(runtimes[key] for key in sorted(runtimes))
    return PortfolioReplayResult(
        run=run,
        strategy_id=policy.strategy_id,
        strategy_version=policy.strategy_version,
        strategy_digest=policy.digest,
        scope_start=scope_start,
        scope_end=scope_end,
        decision_events=tuple(
            sorted(decisions, key=lambda item: (item.decided_at, item.decision_id))
        ),
        lifecycle_transitions=tuple(
            sorted(transitions, key=lambda item: (item.occurred_at, item.transition_id))
        ),
        engine_events=ordered_events,
        equity_curve=tuple(equity_curve),
        trades=ordered_trades,
        performance=performance,
        breakdowns=_build_breakdowns(tuple(equity_curve), ordered_trades),
        opportunity_funnel=funnel,
        final_ledgers=tuple(item.ledger for item in final_runtimes),
        final_lifecycles=tuple(item.lifecycle for item in final_runtimes),
        validation=_build_validation(performance, policy),
    )


def render_governed_portfolio_scoreboard(result: PortfolioReplayResult) -> str:
    """Render the governed development gates and headline diagnostics."""

    performance = result.performance

    def percent(value: Decimal) -> str:
        return f"{value * Decimal('100'):.2f}%"

    def optional(value: Decimal | None) -> str:
        return "n/a" if value is None else f"{value:.3f}"

    lines = [
        "| Strategy | Operations (B/S) | Closed trades | Win rate | Total return | "
        "CSI 300 | Full excess | Matched benchmark | Matched excess | Return ex top 3 | "
        "Max drawdown | Avg invested | Time in market | Fees | Sharpe | Accepted |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | "
        "---: | ---: | ---: | ---: | ---: | --- |",
        "| "
        + " | ".join(
            (
                f"{result.strategy_id} {result.strategy_version}",
                f"{performance.operation_count} "
                f"({performance.buy_operation_count}/{performance.sell_operation_count})",
                str(performance.closed_trades),
                "n/a" if performance.win_rate is None else percent(performance.win_rate),
                percent(performance.total_return),
                percent(performance.benchmark_total_return),
                percent(performance.excess_total_return),
                percent(performance.capital_matched_benchmark_total_return),
                percent(performance.capital_matched_excess_total_return),
                percent(performance.return_excluding_top_3_closed_trades),
                percent(performance.maximum_drawdown),
                percent(performance.average_invested_capital_ratio),
                percent(performance.exposure_rate),
                f"CNY {performance.total_fees:.2f}",
                optional(performance.sharpe_ratio),
                "yes" if result.accepted else "no",
            )
        )
        + " |",
        "",
        "| Gate | Status | Actual | Required | Severity |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    lines.extend(
        f"| {item.gate_name} | {'pass' if item.passed else 'fail'} | "
        f"{item.actual} | {item.required} | {item.severity} |"
        for item in result.validation
    )
    return "\n".join(lines)

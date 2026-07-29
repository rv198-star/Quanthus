"""Strategy-neutral daily historical replay over the accepted P2 referee."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Any, Protocol

from quanthus.backtest.contracts import (
    MARKET_TIMEZONE,
    BacktestResult,
    BacktestRunSpec,
    BacktestSignal,
    CorporateActionStep,
    EngineEvent,
    EventType,
    MarketSession,
    OpenExecutionState,
    OrderSide,
    UnitState,
    decimal_text,
)
from quanthus.backtest.engine import BacktestEngine, BacktestInputError, UnsupportedModeError
from quanthus.backtest.inputs import (
    CoverageStatus,
    P1DailyExecutionEvidence,
    market_session_from_p1,
)
from quanthus.backtest.replay import merge_replay_events
from quanthus.contracts import SnapshotRef

MONEY_QUANTUM = Decimal("0.01")
RATE_QUANTUM = Decimal("0.00000001")
TRADING_PERIODS_PER_YEAR = Decimal("252")


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _rate(value: Decimal) -> Decimal:
    return value.quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)


def _require_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _stable_id(prefix: str, *parts: object) -> str:
    payload = json.dumps(
        parts,
        default=lambda value: (
            value.isoformat()
            if isinstance(value, (date, datetime))
            else decimal_text(value)
            if isinstance(value, Decimal)
            else value.value
            if isinstance(value, Enum)
            else value
        ),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(payload).hexdigest()[:24]}"


class ReplayAction(str, Enum):
    NO_ACTION = "no_action"
    ENTER = "enter"
    EXIT = "exit"


@dataclass(frozen=True)
class ReplayDecision:
    action: ReplayAction
    reason_code: str
    aggregate_score: Decimal = Decimal("0")
    security_code: str | None = None
    quantity: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.action, ReplayAction):
            raise ValueError("action must be a ReplayAction")
        _require_text(self.reason_code, "reason_code")
        if not self.aggregate_score.is_finite():
            raise ValueError("aggregate_score must be finite")
        if self.action is ReplayAction.ENTER:
            _require_text(self.security_code, "security_code")
            if (
                not isinstance(self.quantity, int)
                or isinstance(self.quantity, bool)
                or self.quantity <= 0
            ):
                raise ValueError("enter quantity must be a positive integer")
        elif self.security_code is not None or self.quantity is not None:
            raise ValueError("only an enter decision may declare security_code and quantity")


@dataclass(frozen=True)
class PITDailyBar:
    """One decision-visible daily observation from an immutable historical bundle."""

    security_code: str
    exchange: str
    market_date: date
    coverage_status: CoverageStatus
    data_valid: bool
    is_executable: bool
    entry_policy_allows: bool
    reason_code: str
    open_price: Decimal | None
    high_price: Decimal | None
    low_price: Decimal | None
    close_price: Decimal | None
    volume: Decimal | None
    amount: Decimal | None
    upper_limit_price: Decimal | None
    lower_limit_price: Decimal | None
    corporate_action_block_reason: str | None = None

    def __post_init__(self) -> None:
        for text_value, field_name in (
            (self.security_code, "security_code"),
            (self.exchange, "exchange"),
            (self.reason_code, "reason_code"),
        ):
            _require_text(text_value, field_name)
        if not isinstance(self.coverage_status, CoverageStatus):
            raise ValueError("coverage_status must be a CoverageStatus")
        for bool_value, field_name in (
            (self.data_valid, "data_valid"),
            (self.is_executable, "is_executable"),
            (self.entry_policy_allows, "entry_policy_allows"),
        ):
            if not isinstance(bool_value, bool):
                raise ValueError(f"{field_name} must be boolean")
        if self.corporate_action_block_reason is not None:
            _require_text(
                self.corporate_action_block_reason,
                "corporate_action_block_reason",
            )
            if self.entry_policy_allows or self.is_executable:
                raise ValueError("a corporate-action block cannot permit execution")
        for decimal_value, field_name in (
            (self.open_price, "open_price"),
            (self.high_price, "high_price"),
            (self.low_price, "low_price"),
            (self.close_price, "close_price"),
            (self.volume, "volume"),
            (self.amount, "amount"),
            (self.upper_limit_price, "upper_limit_price"),
            (self.lower_limit_price, "lower_limit_price"),
        ):
            if decimal_value is not None and not decimal_value.is_finite():
                raise ValueError(f"{field_name} must be finite when present")


class StrategyDecisionView(Protocol):
    """Read-only T-close view; implementations must not expose future sessions."""

    @property
    def snapshot(self) -> SnapshotRef: ...

    def securities(self) -> tuple[str, ...]: ...

    def current_bars(self) -> tuple[PITDailyBar, ...]: ...

    def bar(self, security_code: str) -> PITDailyBar: ...

    def history(self, security_code: str, sessions: int) -> tuple[PITDailyBar, ...]: ...


class ReplayFrameSource(Protocol):
    """Execution-side evidence source that is intentionally absent from strategy context."""

    def session(self, security_code: str) -> MarketSession: ...

    def corporate_actions(
        self,
        security_code: str,
        prior_mark_price: Decimal,
    ) -> tuple[CorporateActionStep, ...]: ...

    def block_reason(self, security_code: str) -> str | None: ...


@dataclass(frozen=True)
class StrategyReplayContext:
    snapshot: SnapshotRef
    decision_at: datetime
    unit_state: UnitState
    market_view: StrategyDecisionView | None = None


class HistoricalStrategy(Protocol):
    @property
    def strategy_id(self) -> str: ...

    @property
    def strategy_version(self) -> str: ...

    def decide(self, context: StrategyReplayContext) -> ReplayDecision: ...


@dataclass(frozen=True)
class StrategyReplayFrame:
    snapshot: SnapshotRef
    decision_at: datetime
    execution_date: date
    execution_sessions: tuple[MarketSession, ...]
    corporate_actions: tuple[CorporateActionStep, ...] = ()
    decision_view: StrategyDecisionView | None = field(
        default=None,
        compare=False,
        repr=False,
    )
    source: ReplayFrameSource | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if self.decision_at.tzinfo is None or self.decision_at.utcoffset() is None:
            raise ValueError("decision_at must be timezone-aware")
        if self.snapshot.knowledge_cutoff > self.decision_at:
            raise ValueError("frame decision cannot precede snapshot knowledge_cutoff")
        if self.decision_at.astimezone(MARKET_TIMEZONE).date() != self.snapshot.market_date:
            raise ValueError("frame decision date must equal snapshot market_date")
        if self.execution_date <= self.snapshot.market_date:
            raise ValueError("execution_date must follow the decision market date")
        security_codes: set[str] = set()
        for session in self.execution_sessions:
            if session.session_date != self.execution_date:
                raise ValueError("all execution sessions must match execution_date")
            if session.security_code in security_codes:
                raise ValueError("execution sessions must have unique security codes")
            security_codes.add(session.security_code)
        action_ids: set[str] = set()
        for step in self.corporate_actions:
            if step.action.effective_date != self.execution_date:
                raise ValueError("corporate action effective date must match execution_date")
            if step.applied_at.astimezone(MARKET_TIMEZONE).date() != self.execution_date:
                raise ValueError("corporate action applied_at must match execution_date")
            if step.action.action_id in action_ids:
                raise ValueError("corporate action ids must be unique within a frame")
            action_ids.add(step.action.action_id)
        if self.decision_view is not None and self.decision_view.snapshot != self.snapshot:
            raise ValueError("decision view snapshot must match its replay frame")

    def session(self, security_code: str) -> MarketSession:
        matches = tuple(
            session for session in self.execution_sessions if session.security_code == security_code
        )
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise BacktestInputError(
                f"frame requires exactly one execution session for {security_code}"
            )
        if self.source is not None:
            return self.source.session(security_code)
        raise BacktestInputError(
            f"frame requires exactly one execution session for {security_code}"
        )

    def block_reason(self, security_code: str) -> str | None:
        return None if self.source is None else self.source.block_reason(security_code)

    def actions(
        self,
        security_code: str,
        prior_mark_price: Decimal,
    ) -> tuple[CorporateActionStep, ...]:
        if self.corporate_actions:
            if any(
                step.action.security_code != security_code
                for step in self.corporate_actions
            ):
                raise BacktestInputError(
                    "frame corporate action does not match the held security"
                )
            return self.corporate_actions
        if self.source is None:
            return ()
        return self.source.corporate_actions(security_code, prior_mark_price)


@dataclass(frozen=True)
class P1StrategyReplayFrameEvidence:
    """Typed P1 evidence for one decision-to-execution replay frame."""

    decision_snapshot: SnapshotRef
    decision_at: datetime
    execution_date: date
    execution_evidence: tuple[P1DailyExecutionEvidence, ...]
    corporate_actions: tuple[CorporateActionStep, ...] = ()

    def __post_init__(self) -> None:
        if self.execution_date <= self.decision_snapshot.market_date:
            raise ValueError("execution_date must follow the decision market date")
        if self.decision_at.tzinfo is None or self.decision_at.utcoffset() is None:
            raise ValueError("decision_at must be timezone-aware")
        if self.decision_snapshot.knowledge_cutoff > self.decision_at:
            raise ValueError("decision cannot precede the P1 snapshot knowledge_cutoff")
        if (
            self.decision_at.astimezone(MARKET_TIMEZONE).date()
            != self.decision_snapshot.market_date
        ):
            raise ValueError("decision_at must match the P1 decision market date")

        security_codes: set[str] = set()
        for evidence in self.execution_evidence:
            if evidence.snapshot.market_date != self.execution_date:
                raise ValueError("P1 execution evidence must match execution_date")
            if evidence.security_code in security_codes:
                raise ValueError("P1 execution evidence must have unique security codes")
            security_codes.add(evidence.security_code)


def strategy_replay_frame_from_p1(
    evidence: P1StrategyReplayFrameEvidence,
) -> StrategyReplayFrame:
    """Build a replay frame without exposing execution evidence to strategy code."""

    sessions = tuple(
        market_session_from_p1(item)
        for item in sorted(evidence.execution_evidence, key=lambda item: item.security_code)
    )
    return StrategyReplayFrame(
        snapshot=evidence.decision_snapshot,
        decision_at=evidence.decision_at,
        execution_date=evidence.execution_date,
        execution_sessions=sessions,
        corporate_actions=evidence.corporate_actions,
    )


@dataclass(frozen=True)
class StrategyReplaySpec:
    run: BacktestRunSpec
    unit_id: str
    initial_cash: Decimal
    frames: Sequence[StrategyReplayFrame]

    def __post_init__(self) -> None:
        _require_text(self.unit_id, "unit_id")
        if not self.initial_cash.is_finite() or self.initial_cash <= 0:
            raise ValueError("initial_cash must be finite and positive")
        if self.initial_cash != _money(self.initial_cash):
            raise ValueError("initial_cash must use the cash quantum")
        if not self.frames:
            raise ValueError("strategy replay requires at least one frame")
        if self.run.official:
            raise ValueError("the first P3 strategy replay slice is research-only")
        for index in range(len(self.frames) - 1):
            current = self.frames[index]
            following = self.frames[index + 1]
            if current.snapshot.market_date >= following.snapshot.market_date:
                raise ValueError("frame decision dates must be strictly increasing")
            if current.execution_date >= following.execution_date:
                raise ValueError("frame execution dates must be strictly increasing")
            if current.execution_date != following.snapshot.market_date:
                raise ValueError(
                    "each execution date must equal the following frame's decision date"
                )
        snapshot_ids = [frame.snapshot.snapshot_id for frame in self.frames]
        if len(snapshot_ids) != len(set(snapshot_ids)):
            raise ValueError("frame snapshot ids must be unique")


@dataclass(frozen=True)
class StrategyDecisionTrace:
    decision_id: str
    market_date: date
    snapshot_id: str
    state_before: str
    action: ReplayAction
    reason_code: str
    aggregate_score: Decimal
    security_code: str | None
    quantity: int | None
    signal_id: str | None
    execution_status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "market_date": self.market_date.isoformat(),
            "snapshot_id": self.snapshot_id,
            "state_before": self.state_before,
            "action": self.action.value,
            "reason_code": self.reason_code,
            "aggregate_score": decimal_text(self.aggregate_score),
            "security_code": self.security_code,
            "quantity": self.quantity,
            "signal_id": self.signal_id,
            "execution_status": self.execution_status,
        }


@dataclass(frozen=True)
class StrategyEquityPoint:
    market_date: date
    system_equity: Decimal

    def __post_init__(self) -> None:
        if not self.system_equity.is_finite() or self.system_equity <= 0:
            raise ValueError("system_equity must be finite and positive")
        if self.system_equity != _money(self.system_equity):
            raise ValueError("system_equity must use the cash quantum")

    def to_dict(self) -> dict[str, str]:
        return {
            "market_date": self.market_date.isoformat(),
            "system_equity": decimal_text(self.system_equity),
        }


@dataclass(frozen=True)
class StrategyTrade:
    security_code: str
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "security_code": self.security_code,
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
        }


@dataclass(frozen=True)
class StrategyPerformanceReport:
    initial_equity: Decimal
    ending_equity: Decimal
    total_pnl: Decimal
    total_return: Decimal
    unit_capital_day_return: Decimal
    maximum_drawdown: Decimal
    annualized_volatility: Decimal
    sharpe_ratio: Decimal | None
    closed_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: Decimal | None
    payoff_ratio: Decimal | None
    profit_factor: Decimal | None
    turnover: Decimal
    total_fees: Decimal
    exposure_rate: Decimal
    average_holding_sessions: Decimal | None
    periods_per_year: int = 252
    risk_free_rate: Decimal = Decimal("0")
    compound_nav: bool = False

    def to_dict(self) -> dict[str, Any]:
        def optional(value: Decimal | None) -> str | None:
            return None if value is None else decimal_text(value)

        return {
            "initial_equity": decimal_text(self.initial_equity),
            "ending_equity": decimal_text(self.ending_equity),
            "total_pnl": decimal_text(self.total_pnl),
            "total_return": decimal_text(self.total_return),
            "unit_capital_day_return": decimal_text(self.unit_capital_day_return),
            "maximum_drawdown": decimal_text(self.maximum_drawdown),
            "annualized_volatility": decimal_text(self.annualized_volatility),
            "sharpe_ratio": optional(self.sharpe_ratio),
            "closed_trades": self.closed_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": optional(self.win_rate),
            "payoff_ratio": optional(self.payoff_ratio),
            "profit_factor": optional(self.profit_factor),
            "turnover": decimal_text(self.turnover),
            "total_fees": decimal_text(self.total_fees),
            "exposure_rate": decimal_text(self.exposure_rate),
            "average_holding_sessions": optional(self.average_holding_sessions),
            "periods_per_year": self.periods_per_year,
            "risk_free_rate": decimal_text(self.risk_free_rate),
            "compound_nav": self.compound_nav,
        }


@dataclass(frozen=True)
class StrategyReplayResult:
    strategy_id: str
    strategy_version: str
    backtest: BacktestResult
    decisions: tuple[StrategyDecisionTrace, ...]
    equity_curve: tuple[StrategyEquityPoint, ...]
    trades: tuple[StrategyTrade, ...]
    performance: StrategyPerformanceReport
    final_state: UnitState
    pending_exit: bool

    def audit_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.backtest.run.run_id,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "decisions": [decision.to_dict() for decision in self.decisions],
            "equity_curve": [point.to_dict() for point in self.equity_curve],
            "trades": [trade.to_dict() for trade in self.trades],
            "performance": self.performance.to_dict(),
            "final_state": _state_dict(self.final_state),
            "pending_exit": self.pending_exit,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.audit_dict(),
            "backtest": self.backtest.to_dict(),
        }

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")


@dataclass
class _OpenTrade:
    security_code: str
    entry_date: date
    entry_quantity: int
    entry_gross: Decimal
    entry_fees: Decimal
    dividends: Decimal
    holding_sessions: int


def _state_dict(state: UnitState) -> dict[str, Any]:
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


def _state_from_events(events: Sequence[EngineEvent], *, target_notional: Decimal) -> UnitState:
    snapshots = [event.payload for event in events if event.event_type is EventType.DAILY_SNAPSHOT]
    if not snapshots:
        raise BacktestInputError("replay operation did not emit a daily snapshot")
    payload = snapshots[-1]
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
        last_mark_snapshot_id=(None if mark_snapshot_id is None else str(mark_snapshot_id)),
    )


def _wealth(state: UnitState) -> Decimal:
    market_value = (
        Decimal("0.00")
        if state.last_mark_price is None
        else _money(state.last_mark_price * state.settled_quantity)
    )
    return _money(state.cash + market_value + state.external_pool)


def _equity_from_events(events: Sequence[EngineEvent]) -> Mapping[date, Decimal]:
    points: dict[date, Decimal] = {}
    for event in events:
        if event.event_type is not EventType.DAILY_SNAPSHOT:
            continue
        payload = event.payload
        points[date.fromisoformat(str(payload["session_date"]))] = _money(
            Decimal(str(payload["total_equity"])) + Decimal(str(payload["external_pool"]))
        )
    return points


def _build_performance(
    *,
    equity_curve: tuple[StrategyEquityPoint, ...],
    trades: tuple[StrategyTrade, ...],
    events: tuple[EngineEvent, ...],
    target_notional: Decimal,
    replay_sessions: int,
    exposure_sessions: int,
) -> StrategyPerformanceReport:
    initial = equity_curve[0].system_equity
    ending = equity_curve[-1].system_equity
    total_pnl = _money(ending - initial)
    total_return = _rate(total_pnl / initial)
    unit_capital_day_return = _rate(total_pnl / (target_notional * Decimal(replay_sessions)))

    peak = equity_curve[0].system_equity
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

    wins = tuple(trade.pnl for trade in trades if trade.pnl > 0)
    losses = tuple(trade.pnl for trade in trades if trade.pnl < 0)
    closed_trades = len(trades)
    win_rate = None if closed_trades == 0 else _rate(Decimal(len(wins)) / closed_trades)
    payoff_ratio = None
    if wins and losses:
        average_win = sum(wins, Decimal("0")) / len(wins)
        average_loss = abs(sum(losses, Decimal("0")) / len(losses))
        payoff_ratio = _rate(average_win / average_loss)
    profit_factor = (
        None if not losses else _rate(sum(wins, Decimal("0")) / abs(sum(losses, Decimal("0"))))
    )
    average_holding = (
        None
        if not trades
        else _rate(Decimal(sum(trade.holding_sessions for trade in trades)) / len(trades))
    )

    fill_payloads = [event.payload for event in events if event.event_type is EventType.FILL]
    gross_turnover = sum(
        (Decimal(str(payload["gross_amount"])) for payload in fill_payloads),
        Decimal("0"),
    )
    total_fees = _money(
        sum(
            (Decimal(str(payload["total_fees"])) for payload in fill_payloads),
            Decimal("0"),
        )
    )
    return StrategyPerformanceReport(
        initial_equity=initial,
        ending_equity=ending,
        total_pnl=total_pnl,
        total_return=total_return,
        unit_capital_day_return=unit_capital_day_return,
        maximum_drawdown=_rate(maximum_drawdown),
        annualized_volatility=_rate(annualized_volatility),
        sharpe_ratio=sharpe_ratio,
        closed_trades=closed_trades,
        winning_trades=len(wins),
        losing_trades=len(losses),
        win_rate=win_rate,
        payoff_ratio=payoff_ratio,
        profit_factor=profit_factor,
        turnover=_rate(gross_turnover / initial),
        total_fees=total_fees,
        exposure_rate=_rate(Decimal(exposure_sessions) / replay_sessions),
        average_holding_sessions=average_holding,
    )


def _build_signal(
    *,
    spec: StrategyReplaySpec,
    strategy_version: str,
    frame: StrategyReplayFrame,
    decision: ReplayDecision,
    side: OrderSide,
    security_code: str,
    quantity: int,
) -> BacktestSignal:
    signal_id = _stable_id(
        "signal",
        spec.run.run_id,
        strategy_version,
        frame.snapshot.snapshot_id,
        side.value,
        security_code,
        quantity,
        decision.reason_code,
    )
    return BacktestSignal(
        signal_id=signal_id,
        snapshot=frame.snapshot,
        decision_at=frame.decision_at,
        profile_id=spec.run.execution_profile_id,
        window_id=None,
        security_code=security_code,
        side=side,
        unit_id=spec.unit_id,
        quantity=quantity,
        aggregate_score=decision.aggregate_score,
        reason_code=decision.reason_code,
        decision_version=strategy_version,
    )


def _child_run(spec: StrategyReplaySpec, *, suffix: str, data_snapshot_id: str) -> BacktestRunSpec:
    return replace(
        spec.run,
        run_id=f"{spec.run.run_id}.{suffix}",
        data_snapshot_id=data_snapshot_id,
    )


def _apply_actions(
    *,
    engine: BacktestEngine,
    spec: StrategyReplaySpec,
    frame: StrategyReplayFrame,
    frame_index: int,
    state: UnitState,
    groups: list[tuple[EngineEvent, ...]],
) -> tuple[UnitState, Decimal]:
    if state.security_code is None:
        raise BacktestInputError("an empty unit cannot consume portfolio corporate actions")
    block_reason = frame.block_reason(state.security_code)
    if block_reason is not None:
        raise BacktestInputError(
            "held security is blocked by replay evidence: " + block_reason
        )
    if state.last_mark_price is None:
        raise BacktestInputError("positioned state requires a prior mark before corporate actions")
    corporate_actions = frame.actions(state.security_code, state.last_mark_price)
    if not corporate_actions:
        return state, Decimal("0.00")
    session = frame.session(state.security_code)
    dividends = Decimal("0.00")
    for action_index, step in enumerate(corporate_actions, start=1):
        if step.applied_at > session.open_at:
            raise BacktestInputError("corporate action must apply no later than the session open")
        application = engine.apply_corporate_action(
            run=_child_run(
                spec,
                suffix=f"frame-{frame_index}.action-{action_index}",
                data_snapshot_id=step.action.snapshot_id,
            ),
            opening_state=state,
            action=step.action,
            applied_at=step.applied_at,
            post_action_mark_price=step.post_action_mark_price,
            mark_snapshot_id=step.mark_snapshot_id,
        )
        state = application.state
        groups.append(application.events)
        dividends += Decimal(str(application.events[0].payload["cash_dividend"]))
    return state, _money(dividends)


def _trade_from_exit(
    open_trade: _OpenTrade, events: Sequence[EngineEvent], exit_date: date
) -> StrategyTrade:
    sell_fills = [
        event.payload
        for event in events
        if event.event_type is EventType.FILL and event.payload.get("side") == "sell"
    ]
    if len(sell_fills) != 1:
        raise BacktestInputError("completed strategy exit requires exactly one sell fill")
    fill = sell_fills[0]
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
    return StrategyTrade(
        security_code=open_trade.security_code,
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
    )


def run_strategy_replay(
    engine: BacktestEngine,
    strategy: HistoricalStrategy,
    spec: StrategyReplaySpec,
) -> StrategyReplayResult:
    """Replay one fixed capital unit through PIT decisions and P2 execution."""

    _require_text(strategy.strategy_id, "strategy_id")
    _require_text(strategy.strategy_version, "strategy_version")
    if strategy.strategy_version != spec.run.rule_version:
        raise BacktestInputError("strategy version must match run rule_version")
    if spec.run.execution_policy_version != engine.policy.policy_version:
        raise BacktestInputError("run execution_policy_version does not match loaded policy")
    profile = engine.policy.profile(spec.run.execution_profile_id)
    if profile.schedule_version != spec.run.schedule_version:
        raise BacktestInputError("run schedule_version does not match execution profile")
    if profile.profile_id != "close-to-next-open-v1":
        raise UnsupportedModeError("strategy replay currently supports close-to-next-open-v1 only")

    state = UnitState.empty(unit_id=spec.unit_id, cash=spec.initial_cash)
    groups: list[tuple[EngineEvent, ...]] = []
    decisions: list[StrategyDecisionTrace] = []
    trades: list[StrategyTrade] = []
    equity_curve = [
        StrategyEquityPoint(
            market_date=spec.frames[0].snapshot.market_date,
            system_equity=spec.initial_cash,
        )
    ]
    open_trade: _OpenTrade | None = None
    exposure_sessions = 0
    pending_exit = False
    frame_index = 0

    while frame_index < len(spec.frames):
        frame = spec.frames[frame_index]
        context = StrategyReplayContext(
            snapshot=frame.snapshot,
            decision_at=frame.decision_at,
            unit_state=state,
            market_view=frame.decision_view,
        )
        decision = strategy.decide(context)
        if not isinstance(decision, ReplayDecision):
            raise BacktestInputError("strategy must return ReplayDecision")
        state_before = "empty" if state.settled_quantity == 0 else "positioned"
        decision_id = _stable_id(
            "decision",
            spec.run.run_id,
            strategy.strategy_version,
            frame.snapshot.snapshot_id,
            decision.action.value,
            decision.reason_code,
        )
        signal: BacktestSignal | None = None
        execution_status = "no_action"
        trace_security = state.security_code
        trace_quantity: int | None = state.settled_quantity or None

        if decision.action is ReplayAction.ENTER:
            if state.settled_quantity != 0:
                raise UnsupportedModeError("adds require the separately frozen P3 position policy")
            if frame.corporate_actions:
                raise BacktestInputError("entry frame cannot carry portfolio corporate actions")
            assert decision.security_code is not None
            assert decision.quantity is not None
            session = frame.session(decision.security_code)
            signal = _build_signal(
                spec=spec,
                strategy_version=strategy.strategy_version,
                frame=frame,
                decision=decision,
                side=OrderSide.BUY,
                security_code=decision.security_code,
                quantity=decision.quantity,
            )
            result = engine.run_buy_session(
                run=_child_run(
                    spec,
                    suffix=f"frame-{frame_index}.enter",
                    data_snapshot_id=frame.snapshot.snapshot_id,
                ),
                signal=signal,
                session=session,
                opening_state=state,
            )
            groups.append(result.events)
            state = _state_from_events(result.events, target_notional=spec.initial_cash)
            execution_status = str(result.result_summary["status"])
            trace_security = decision.security_code
            trace_quantity = decision.quantity
            fills = [event.payload for event in result.events if event.event_type is EventType.FILL]
            if fills:
                fill = fills[0]
                open_trade = _OpenTrade(
                    security_code=decision.security_code,
                    entry_date=frame.execution_date,
                    entry_quantity=int(fill["quantity"]),
                    entry_gross=Decimal(str(fill["gross_amount"])),
                    entry_fees=Decimal(str(fill["total_fees"])),
                    dividends=Decimal("0.00"),
                    holding_sessions=1,
                )
                exposure_sessions += 1
            equity_curve.append(
                StrategyEquityPoint(
                    market_date=frame.execution_date,
                    system_equity=_wealth(state),
                )
            )

        elif decision.action is ReplayAction.NO_ACTION:
            if state.settled_quantity == 0:
                if frame.corporate_actions:
                    raise BacktestInputError(
                        "an empty no-action frame cannot carry portfolio corporate actions"
                    )
            else:
                state, dividends = _apply_actions(
                    engine=engine,
                    spec=spec,
                    frame=frame,
                    frame_index=frame_index,
                    state=state,
                    groups=groups,
                )
                if open_trade is None:
                    raise BacktestInputError("positioned replay state requires an open trade")
                open_trade.dividends = _money(open_trade.dividends + dividends)
                session = frame.session(str(state.security_code))
                mark = engine.mark_position_session(
                    run=_child_run(
                        spec,
                        suffix=f"frame-{frame_index}.mark",
                        data_snapshot_id=session.snapshot_id,
                    ),
                    session=session,
                    opening_state=state,
                )
                state = mark.state
                groups.append(mark.events)
                open_trade.holding_sessions += 1
                exposure_sessions += 1
                execution_status = "position_marked"
            equity_curve.append(
                StrategyEquityPoint(
                    market_date=frame.execution_date,
                    system_equity=_wealth(state),
                )
            )

        else:
            if state.settled_quantity == 0 or state.security_code is None:
                raise BacktestInputError("exit decision requires a positioned unit")
            if open_trade is None:
                raise BacktestInputError("exit decision requires an open trade")
            state, dividends = _apply_actions(
                engine=engine,
                spec=spec,
                frame=frame,
                frame_index=frame_index,
                state=state,
                groups=groups,
            )
            open_trade.dividends = _money(open_trade.dividends + dividends)
            held_security = state.security_code
            if held_security is None:
                raise BacktestInputError("corporate action unexpectedly emptied the unit")
            trace_security = held_security
            trace_quantity = state.settled_quantity
            signal = _build_signal(
                spec=spec,
                strategy_version=strategy.strategy_version,
                frame=frame,
                decision=decision,
                side=OrderSide.SELL,
                security_code=held_security,
                quantity=state.settled_quantity,
            )
            sell_sessions: list[MarketSession] = []
            sell_actions: list[CorporateActionStep] = []
            last_consumed = frame_index
            if state.last_mark_price is None:
                raise BacktestInputError("exit sequence requires a prior position mark")
            mark_cursor = state.last_mark_price
            for candidate_index in range(frame_index, len(spec.frames)):
                candidate = spec.frames[candidate_index]
                if candidate_index > frame_index:
                    block_reason = candidate.block_reason(held_security)
                    if block_reason is not None:
                        raise BacktestInputError(
                            "held security is blocked by replay evidence: " + block_reason
                        )
                    candidate_actions = candidate.actions(held_security, mark_cursor)
                    sell_actions.extend(candidate_actions)
                    if candidate_actions:
                        mark_cursor = candidate_actions[-1].post_action_mark_price
                session = candidate.session(held_security)
                sell_sessions.append(session)
                if session.close_price is not None:
                    mark_cursor = session.close_price
                last_consumed = candidate_index
                if session.open_execution_state not in {
                    OpenExecutionState.SUSPENDED,
                    OpenExecutionState.SELL_BLOCKED_LIMIT_DOWN,
                    OpenExecutionState.MISSING_EVIDENCE,
                }:
                    break
            result = engine.run_sell_sequence(
                run=_child_run(
                    spec,
                    suffix=f"frame-{frame_index}.exit",
                    data_snapshot_id=frame.snapshot.snapshot_id,
                ),
                signal=signal,
                sessions=tuple(sell_sessions),
                opening_state=state,
                corporate_actions=tuple(sell_actions),
            )
            groups.append(result.events)
            carried_dividends = sum(
                (
                    Decimal(str(event.payload["cash_dividend"]))
                    for event in result.events
                    if event.event_type is EventType.CORPORATE_ACTION
                ),
                Decimal("0.00"),
            )
            open_trade.dividends = _money(open_trade.dividends + carried_dividends)
            state = _state_from_events(result.events, target_notional=spec.initial_cash)
            execution_status = str(result.result_summary["status"])
            consumed_equity = _equity_from_events(result.events)
            for consumed_index in range(frame_index, last_consumed + 1):
                consumed_date = spec.frames[consumed_index].execution_date
                if consumed_date not in consumed_equity:
                    raise BacktestInputError("sell sequence omitted a consumed session mark")
                equity_curve.append(
                    StrategyEquityPoint(
                        market_date=consumed_date,
                        system_equity=consumed_equity[consumed_date],
                    )
                )
            consumed_sessions = last_consumed - frame_index + 1
            open_trade.holding_sessions += consumed_sessions
            exposure_sessions += consumed_sessions
            if execution_status == "completed":
                trades.append(
                    _trade_from_exit(open_trade, result.events, sell_sessions[-1].session_date)
                )
                open_trade = None
            else:
                pending_exit = True
            frame_index = last_consumed

        decisions.append(
            StrategyDecisionTrace(
                decision_id=decision_id,
                market_date=frame.snapshot.market_date,
                snapshot_id=frame.snapshot.snapshot_id,
                state_before=state_before,
                action=decision.action,
                reason_code=decision.reason_code,
                aggregate_score=decision.aggregate_score,
                security_code=trace_security,
                quantity=trace_quantity,
                signal_id=None if signal is None else signal.signal_id,
                execution_status=execution_status,
            )
        )
        frame_index += 1

    events = merge_replay_events(spec.run.run_id, tuple(groups))
    performance = _build_performance(
        equity_curve=tuple(equity_curve),
        trades=tuple(trades),
        events=events,
        target_notional=spec.initial_cash,
        replay_sessions=len(spec.frames),
        exposure_sessions=exposure_sessions,
    )
    event_types = [event.event_type for event in events]
    summary: dict[str, Any] = {
        "status": "completed_with_pending_exit" if pending_exit else "completed",
        "strategy_id": strategy.strategy_id,
        "strategy_version": strategy.strategy_version,
        "research_only": True,
        "replay_sessions": len(spec.frames),
        "strategy_decisions": len(decisions),
        "signals": event_types.count(EventType.SIGNAL_ACCEPTED),
        "orders": event_types.count(EventType.ORDER_CREATED),
        "fills": event_types.count(EventType.FILL),
        "closed_trades": len(trades),
        "open_position": state.settled_quantity > 0,
        "pending_exit": pending_exit,
        "ending_equity": decimal_text(performance.ending_equity),
        "total_pnl": decimal_text(performance.total_pnl),
        "total_return": decimal_text(performance.total_return),
        "compound_nav": False,
    }
    return StrategyReplayResult(
        strategy_id=strategy.strategy_id,
        strategy_version=strategy.strategy_version,
        backtest=BacktestResult(run=spec.run, events=events, result_summary=summary),
        decisions=tuple(decisions),
        equity_curve=tuple(equity_curve),
        trades=tuple(trades),
        performance=performance,
        final_state=state,
        pending_exit=pending_exit,
    )

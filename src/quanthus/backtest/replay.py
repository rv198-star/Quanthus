"""One-unit historical replay orchestration over already PIT-resolved inputs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any

from quanthus.backtest.contracts import (
    BacktestResult,
    BacktestRunSpec,
    BacktestSignal,
    CorporateActionStep,
    EngineEvent,
    EventType,
    MarketSession,
    OrderSide,
    UnitState,
    decimal_text,
)
from quanthus.backtest.engine import BacktestEngine, BacktestInputError
from quanthus.backtest.metrics import (
    EquityPoint,
    FixedUnitPerformanceInput,
    FixedUnitPerformanceReport,
    build_fixed_unit_report,
)


@dataclass(frozen=True)
class RoundTripReplaySpec:
    run: BacktestRunSpec
    entry_signal: BacktestSignal
    entry_session: MarketSession
    exit_signal: BacktestSignal
    exit_sessions: tuple[MarketSession, ...]
    initial_cash: Decimal
    capital_days: int
    corporate_actions: tuple[CorporateActionStep, ...] = ()


@dataclass(frozen=True)
class RoundTripReplayResult:
    backtest: BacktestResult
    performance: FixedUnitPerformanceReport

    def to_dict(self) -> dict[str, Any]:
        return {
            "backtest": self.backtest.to_dict(),
            "performance": self.performance.to_dict(),
        }


class DiagnosticDisposition(str, Enum):
    REJECTED = "rejected"
    NEAR_THRESHOLD = "near_threshold"


@dataclass(frozen=True)
class DiagnosticReplaySpec:
    source_decision_id: str
    source_disposition: DiagnosticDisposition
    baseline_policy_version: str
    scenario: RoundTripReplaySpec

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.source_decision_id, "source_decision_id"),
            (self.baseline_policy_version, "baseline_policy_version"),
        ):
            if not value.strip():
                raise ValueError(f"{field_name} must not be empty")
        if not isinstance(self.source_disposition, DiagnosticDisposition):
            raise ValueError("source_disposition must be a DiagnosticDisposition")
        if self.scenario.run.official:
            raise ValueError("a diagnostic hypothetical replay cannot be official")
        if self.scenario.capital_days > 20:
            raise ValueError("a diagnostic hypothetical replay is limited to 20 capital days")


def _stable_event_id(run_id: str, sequence: int, event: EngineEvent) -> str:
    payload = json.dumps(
        [
            run_id,
            sequence,
            event.event_type.value,
            event.event_time.isoformat(),
            event.payload,
        ],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"evt_{hashlib.sha256(payload).hexdigest()[:24]}"


def merge_replay_events(
    run_id: str, groups: tuple[tuple[EngineEvent, ...], ...]
) -> tuple[EngineEvent, ...]:
    indexed = [
        (event.event_time, group_index, event.sequence, event)
        for group_index, group in enumerate(groups)
        for event in group
    ]
    ordered = sorted(indexed, key=lambda item: (item[0], item[1], item[2]))
    merged: list[EngineEvent] = []
    for sequence, (_, _, _, event) in enumerate(ordered, start=1):
        merged.append(
            EngineEvent(
                event_id=_stable_event_id(run_id, sequence, event),
                sequence=sequence,
                event_type=event.event_type,
                event_time=event.event_time,
                reason_code=event.reason_code,
                payload=event.payload,
            )
        )
    return tuple(merged)


def _state_from_entry(result: BacktestResult, target_notional: Decimal) -> UnitState:
    if result.result_summary.get("fills") != 1:
        raise BacktestInputError("round-trip replay requires a filled entry")
    snapshots = [
        event.payload for event in result.events if event.event_type is EventType.DAILY_SNAPSHOT
    ]
    if len(snapshots) != 1:
        raise BacktestInputError("round-trip entry must produce exactly one daily snapshot")
    snapshot = snapshots[0]
    mark_price = snapshot.get("mark_price")
    mark_snapshot_id = snapshot.get("mark_snapshot_id")
    security_code = snapshot.get("security_code")
    if not isinstance(mark_price, str) or not isinstance(mark_snapshot_id, str):
        raise BacktestInputError("filled entry snapshot requires a sourced mark")
    if not isinstance(security_code, str):
        raise BacktestInputError("filled entry snapshot requires a security code")
    return UnitState(
        unit_id=str(snapshot["unit_id"]),
        target_notional=target_notional,
        cash=Decimal(str(snapshot["cash"])),
        security_code=security_code,
        settled_quantity=int(snapshot["position_quantity"]),
        position_cost=Decimal(str(snapshot["position_cost"])),
        commission_expense=Decimal(str(snapshot["commission_expense"])),
        stamp_duty_expense=Decimal(str(snapshot["stamp_duty_expense"])),
        transfer_fee_expense=Decimal(str(snapshot["transfer_fee_expense"])),
        realized_pnl=Decimal(str(snapshot["realized_pnl"])),
        dividend_income=Decimal(str(snapshot["dividend_income"])),
        external_pool=Decimal(str(snapshot["external_pool"])),
        last_mark_price=Decimal(mark_price),
        last_mark_snapshot_id=mark_snapshot_id,
    )


def _performance_from_events(
    *,
    events: tuple[EngineEvent, ...],
    entry_signal: BacktestSignal,
    initial_cash: Decimal,
    capital_days: int,
) -> FixedUnitPerformanceReport:
    fills = [event.payload for event in events if event.event_type is EventType.FILL]
    if len(fills) != 2 or [fill.get("side") for fill in fills] != ["buy", "sell"]:
        raise BacktestInputError(
            "round-trip report requires one buy fill followed by one sell fill"
        )

    equity_by_date: dict[date, Decimal] = {
        entry_signal.snapshot.market_date: initial_cash,
    }
    external_pool = Decimal("0.00")
    dividends = Decimal("0.00")
    for event in events:
        market_date = event.event_time.astimezone(entry_signal.decision_at.tzinfo).date()
        if event.event_type is EventType.CORPORATE_ACTION:
            dividends += Decimal(str(event.payload["cash_dividend"]))
            equity_by_date[market_date] = (
                Decimal(str(event.payload["equity_after"])) + external_pool
            )
        elif event.event_type is EventType.DAILY_SNAPSHOT:
            external_pool = Decimal(str(event.payload["external_pool"]))
            equity_by_date[market_date] = (
                Decimal(str(event.payload["total_equity"])) + external_pool
            )

    points = tuple(
        EquityPoint(market_date=market_date, system_equity=equity_by_date[market_date])
        for market_date in sorted(equity_by_date)
    )
    buy, sell = fills
    return build_fixed_unit_report(
        FixedUnitPerformanceInput(
            target_notional=initial_cash,
            entry_gross=Decimal(str(buy["gross_amount"])),
            entry_fees=Decimal(str(buy["total_fees"])),
            exit_gross=Decimal(str(sell["gross_amount"])),
            exit_fees=Decimal(str(sell["total_fees"])),
            dividends=dividends,
            capital_days=capital_days,
            equity_points=points,
        )
    )


def run_round_trip(engine: BacktestEngine, scenario: RoundTripReplaySpec) -> RoundTripReplayResult:
    """Replay explicit entry/action/exit facts without deriving strategy decisions."""

    if scenario.entry_signal.side is not OrderSide.BUY:
        raise BacktestInputError("round-trip entry signal must be buy")
    if scenario.exit_signal.side is not OrderSide.SELL:
        raise BacktestInputError("round-trip exit signal must be sell")
    if scenario.entry_signal.unit_id != scenario.exit_signal.unit_id:
        raise BacktestInputError("round-trip signals must use one capital unit")
    if scenario.entry_signal.security_code != scenario.exit_signal.security_code:
        raise BacktestInputError("round-trip signals must use one security")
    if scenario.entry_session.close_at >= scenario.exit_signal.decision_at:
        raise BacktestInputError("round-trip exit decision must follow entry-session close")
    if scenario.capital_days <= 0:
        raise BacktestInputError("round-trip capital_days must be positive")
    if any(
        current.applied_at >= following.applied_at
        for current, following in zip(
            scenario.corporate_actions, scenario.corporate_actions[1:], strict=False
        )
    ):
        raise BacktestInputError("corporate-action steps must be strictly increasing")
    if any(
        step.applied_at <= scenario.entry_session.close_at for step in scenario.corporate_actions
    ):
        raise BacktestInputError("corporate actions must follow the filled entry session")

    entry_run = replace(
        scenario.run,
        run_id=f"{scenario.run.run_id}.entry",
        data_snapshot_id=scenario.entry_signal.snapshot.snapshot_id,
    )
    entry = engine.run_session(
        run=entry_run,
        signal=scenario.entry_signal,
        session=scenario.entry_session,
        initial_cash=scenario.initial_cash,
    )
    state = _state_from_entry(entry, scenario.initial_cash)
    groups: list[tuple[EngineEvent, ...]] = [entry.events]

    for index, step in enumerate(scenario.corporate_actions, start=1):
        action_run = replace(
            scenario.run,
            run_id=f"{scenario.run.run_id}.action-{index}",
            data_snapshot_id=step.action.snapshot_id,
        )
        application = engine.apply_corporate_action(
            run=action_run,
            opening_state=state,
            action=step.action,
            applied_at=step.applied_at,
            post_action_mark_price=step.post_action_mark_price,
            mark_snapshot_id=step.mark_snapshot_id,
        )
        state = application.state
        groups.append(application.events)

    exit_run = replace(
        scenario.run,
        run_id=f"{scenario.run.run_id}.exit",
        data_snapshot_id=scenario.exit_signal.snapshot.snapshot_id,
    )
    exit_result = engine.run_sell_sequence(
        run=exit_run,
        signal=scenario.exit_signal,
        sessions=scenario.exit_sessions,
        opening_state=state,
    )
    if exit_result.result_summary.get("status") != "completed":
        raise BacktestInputError("round-trip exit did not complete within supplied sessions")
    groups.append(exit_result.events)
    events = merge_replay_events(scenario.run.run_id, tuple(groups))
    performance = _performance_from_events(
        events=events,
        entry_signal=scenario.entry_signal,
        initial_cash=scenario.initial_cash,
        capital_days=scenario.capital_days,
    )
    event_types = [event.event_type for event in events]
    result_summary: dict[str, Any] = {
        "status": "completed",
        "signals": event_types.count(EventType.SIGNAL_ACCEPTED),
        "order_intents": event_types.count(EventType.ORDER_INTENT_CREATED),
        "orders": event_types.count(EventType.ORDER_CREATED),
        "fills": event_types.count(EventType.FILL),
        "ledger_entries": event_types.count(EventType.LEDGER_ENTRY),
        "corporate_actions": event_types.count(EventType.CORPORATE_ACTION),
        "ending_cash": exit_result.result_summary["ending_cash"],
        "external_pool": exit_result.result_summary["external_pool"],
        "realized_pnl": exit_result.result_summary["realized_pnl"],
        "total_pnl": decimal_text(performance.total_pnl),
        "compound_nav": False,
    }
    return RoundTripReplayResult(
        backtest=BacktestResult(
            run=scenario.run,
            events=events,
            result_summary=result_summary,
        ),
        performance=performance,
    )


def run_diagnostic_path(
    engine: BacktestEngine, diagnostic: DiagnosticReplaySpec
) -> RoundTripReplayResult:
    """Run an isolated rejected/near-threshold path that cannot be official or aggregatable."""

    replay = run_round_trip(engine, diagnostic.scenario)
    summary = dict(replay.backtest.result_summary)
    summary.update(
        {
            "result_class": "diagnostic_hypothetical",
            "source_decision_id": diagnostic.source_decision_id,
            "source_disposition": diagnostic.source_disposition.value,
            "baseline_policy_version": diagnostic.baseline_policy_version,
            "capital_constrained": False,
            "aggregatable_as_official": False,
            "maximum_capital_days": 20,
        }
    )
    return replace(replay, backtest=replace(replay.backtest, result_summary=summary))

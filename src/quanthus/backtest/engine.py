"""First deterministic P2 vertical slice: one close signal through the next session."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Any

from quanthus.backtest.contracts import (
    MARKET_TIMEZONE,
    BacktestResult,
    BacktestRunSpec,
    BacktestSignal,
    CorporateAction,
    CorporateActionApplication,
    CorporateActionStep,
    EngineEvent,
    EventType,
    FillRecord,
    LedgerEntry,
    LedgerPosting,
    LedgerSnapshot,
    MarketSession,
    MarkStatus,
    OpenExecutionState,
    OrderIntentRecord,
    OrderRecord,
    OrderSide,
    PositionMarkResult,
    UnitState,
    decimal_text,
)
from quanthus.backtest.inputs import buy_open_rejection_reason
from quanthus.backtest.policy import ExecutionPolicy, ExecutionProfile, ProfileKind


class BacktestInputError(ValueError):
    """Raised when replay inputs violate time, identity, or policy contracts."""


class UnsupportedModeError(BacktestInputError):
    """Raised for an explicit P2 mode that has not passed its implementation gate."""


def _canonical_json_value(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return decimal_text(value)
    if isinstance(value, Enum):
        return str(value.value)
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        default=_canonical_json_value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _stable_id(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256(_canonical_bytes(list(parts))).hexdigest()[:24]
    return f"{prefix}_{digest}"


class BacktestEngine:
    """Conservative event referee for the first normal-buy acceptance slice."""

    def __init__(self, policy: ExecutionPolicy) -> None:
        self.policy = policy

    def run_session(
        self,
        *,
        run: BacktestRunSpec,
        signal: BacktestSignal,
        session: MarketSession,
        initial_cash: Decimal,
    ) -> BacktestResult:
        """Execute a buy for a fresh unit; retained for the accepted P2 interface."""

        return self.run_buy_session(
            run=run,
            signal=signal,
            session=session,
            opening_state=UnitState.empty(unit_id=signal.unit_id, cash=initial_cash),
        )

    def run_buy_session(
        self,
        *,
        run: BacktestRunSpec,
        signal: BacktestSignal,
        session: MarketSession,
        opening_state: UnitState,
    ) -> BacktestResult:
        """Execute one buy while preserving an existing empty unit's accounting state."""

        self._validate_empty_unit_state(opening_state)
        if opening_state.unit_id != signal.unit_id:
            raise BacktestInputError("opening unit does not match signal unit")
        profile = self._validate_inputs(run, signal, session, opening_state.cash)
        if profile.kind is not ProfileKind.CLOSE_TO_NEXT_OPEN:
            raise UnsupportedModeError(
                "two-window execution is contract-validated but not implemented in the first slice"
            )
        if signal.side is not OrderSide.BUY:
            raise UnsupportedModeError("the first P2 slice implements normal buy execution only")

        events: list[EngineEvent] = []

        def append_event(
            event_type: EventType,
            event_time: datetime,
            reason_code: str,
            payload: dict[str, Any],
        ) -> None:
            sequence = len(events) + 1
            event_id = _stable_id(
                "evt",
                run.run_id,
                sequence,
                event_type.value,
                event_time.isoformat(),
                payload,
            )
            events.append(
                EngineEvent(
                    event_id=event_id,
                    sequence=sequence,
                    event_type=event_type,
                    event_time=event_time,
                    reason_code=reason_code,
                    payload=payload,
                )
            )

        append_event(
            EventType.SIGNAL_ACCEPTED,
            signal.decision_at,
            "signal_contract_valid",
            {
                "signal_id": signal.signal_id,
                "snapshot_id": signal.snapshot.snapshot_id,
                "decision_at": signal.decision_at.isoformat(),
                "profile_id": signal.profile_id,
                "window_id": signal.window_id,
                "security_code": signal.security_code,
                "side": signal.side.value,
                "unit_id": signal.unit_id,
                "quantity": signal.quantity,
                "aggregate_score": decimal_text(signal.aggregate_score),
                "decision_reason_code": signal.reason_code,
                "decision_version": signal.decision_version,
            },
        )

        intent = OrderIntentRecord(
            intent_id=_stable_id(
                "intent", run.run_id, signal.signal_id, profile.profile_id, session.session_date
            ),
            signal_id=signal.signal_id,
            profile_id=profile.profile_id,
            window_id=signal.window_id,
            security_code=signal.security_code,
            side=signal.side,
            unit_id=signal.unit_id,
            quantity=signal.quantity,
            created_at=signal.decision_at,
            eligible_at=session.open_at,
            expires_at=session.close_at,
            unfilled_policy="cancel_after_open_attempt",
            decision_reason_code=signal.reason_code,
            policy_version=self.policy.policy_version,
        )
        append_event(
            EventType.ORDER_INTENT_CREATED,
            signal.decision_at,
            "next_session_intent_created",
            intent.to_dict(),
        )
        order = OrderRecord(
            order_id=_stable_id(
                "ord", run.run_id, signal.signal_id, profile.profile_id, session.session_date
            ),
            intent_id=intent.intent_id,
            signal_id=signal.signal_id,
            profile_id=profile.profile_id,
            window_id=signal.window_id,
            security_code=signal.security_code,
            side=signal.side,
            unit_id=signal.unit_id,
            quantity=signal.quantity,
            eligible_session=session.session_date,
            policy_version=self.policy.policy_version,
        )
        append_event(
            EventType.ORDER_CREATED,
            signal.decision_at,
            "next_session_order_created",
            order.to_dict(),
        )

        if signal.quantity % self.policy.entry_lot_size != 0:
            return self._rejected_result(
                run=run,
                events=events,
                order=order,
                session=session,
                opening_state=opening_state,
                reason_code="entry_quantity_not_whole_lot",
                append_event=append_event,
            )
        cancellation_reason = buy_open_rejection_reason(session.open_execution_state)
        if cancellation_reason is not None:
            return self._no_fill_result(
                run=run,
                events=events,
                order=order,
                session=session,
                opening_state=opening_state,
                reason_code=cancellation_reason,
                transition="created_to_cancelled",
                append_event=append_event,
            )

        if session.open_price is None or session.close_price is None:
            raise BacktestInputError("executable buy session requires open and close prices")

        fill_price = self._quantize_price(
            session.open_price
            * (Decimal("1") + self.policy.buy_cost.slippage_bps / Decimal("10000"))
        )
        gross_amount = self._quantize_cash(fill_price * signal.quantity)
        commission = self._quantize_cash(
            max(
                gross_amount * self.policy.buy_cost.commission_rate,
                self.policy.buy_cost.minimum_commission,
            )
        )
        total_cash = gross_amount + commission
        if total_cash > opening_state.cash:
            return self._rejected_result(
                run=run,
                events=events,
                order=order,
                session=session,
                opening_state=opening_state,
                reason_code="insufficient_unit_cash",
                append_event=append_event,
            )

        append_event(
            EventType.ORDER_STATUS,
            session.open_at,
            "order_filled_at_eligible_open",
            {
                "order_id": order.order_id,
                "transition": "created_to_filled",
                "open_execution_state": session.open_execution_state.value,
                "source_snapshot_id": session.snapshot_id,
            },
        )
        fill = FillRecord(
            fill_id=_stable_id(
                "fill", order.order_id, session.open_at.isoformat(), signal.quantity, fill_price
            ),
            order_id=order.order_id,
            filled_at=session.open_at,
            side=signal.side,
            price=fill_price,
            quantity=signal.quantity,
            gross_amount=gross_amount,
            commission=commission,
            stamp_duty=Decimal("0.00"),
            transfer_fee=Decimal("0.00"),
            total_fees=commission,
            open_execution_state=session.open_execution_state,
            source_revision=session.source_revision,
            snapshot_id=session.snapshot_id,
        )
        append_event(EventType.FILL, session.open_at, "normal_buy_fill", fill.to_dict())

        ledger = LedgerEntry(
            entry_id=_stable_id("led", run.run_id, fill.fill_id),
            reference_id=fill.fill_id,
            reference_type="fill",
            unit_id=signal.unit_id,
            security_code=signal.security_code,
            quantity_delta=signal.quantity,
            postings=(
                LedgerPosting(f"unit/{signal.unit_id}/cash", -total_cash),
                LedgerPosting(
                    f"unit/{signal.unit_id}/position_cost/{signal.security_code}", gross_amount
                ),
                LedgerPosting(f"unit/{signal.unit_id}/commission_expense", commission),
            ),
        )
        append_event(
            EventType.LEDGER_ENTRY,
            session.open_at,
            "balanced_buy_entry",
            ledger.to_dict(),
        )

        ending_cash = self._quantize_cash(opening_state.cash - total_cash)
        market_value = self._quantize_cash(session.close_price * signal.quantity)
        total_equity = self._quantize_cash(ending_cash + market_value)
        snapshot = LedgerSnapshot(
            snapshot_id=_stable_id("lsnap", run.run_id, session.session_date, signal.unit_id),
            session_date=session.session_date,
            as_of=session.close_at,
            unit_id=signal.unit_id,
            cash=ending_cash,
            security_code=signal.security_code,
            position_quantity=signal.quantity,
            position_cost=gross_amount,
            market_value=market_value,
            commission_expense=opening_state.commission_expense + commission,
            stamp_duty_expense=opening_state.stamp_duty_expense,
            transfer_fee_expense=opening_state.transfer_fee_expense,
            realized_pnl=opening_state.realized_pnl,
            dividend_income=opening_state.dividend_income,
            external_pool=opening_state.external_pool,
            total_equity=total_equity,
            mark_status=MarkStatus.OBSERVED_CLOSE,
            mark_price=session.close_price,
            mark_snapshot_id=session.snapshot_id,
        )
        append_event(
            EventType.DAILY_SNAPSHOT,
            session.close_at,
            "session_mark_complete",
            snapshot.to_dict(),
        )
        summary = {
            "status": "completed",
            "signals": 1,
            "orders": 1,
            "fills": 1,
            "rejections": 0,
            "cancellations": 0,
            "ending_cash": decimal_text(ending_cash),
            "position_quantity": signal.quantity,
            "market_value": decimal_text(market_value),
            "total_equity": decimal_text(total_equity),
        }
        return BacktestResult(run=run, events=tuple(events), result_summary=summary)

    def mark_position_session(
        self,
        *,
        run: BacktestRunSpec,
        session: MarketSession,
        opening_state: UnitState,
    ) -> PositionMarkResult:
        """Mark a held position at one session close without execution artifacts."""

        self._validate_run(run)
        self._validate_unit_state(opening_state)
        if opening_state.security_code != session.security_code:
            raise BacktestInputError("mark session security does not match unit position")
        for value, field_name in (
            (session.open_price, "open_price"),
            (session.close_price, "close_price"),
        ):
            if value is not None and value != self._quantize_price(value):
                raise BacktestInputError(f"{field_name} must match the configured price tick")

        if session.close_price is None:
            mark_price = opening_state.last_mark_price
            mark_snapshot_id = opening_state.last_mark_snapshot_id
            mark_status = MarkStatus.CARRIED_FORWARD
        else:
            mark_price = session.close_price
            mark_snapshot_id = session.snapshot_id
            mark_status = MarkStatus.OBSERVED_CLOSE
        if mark_price is None or mark_snapshot_id is None:
            raise BacktestInputError("a held position requires a prior observable mark")

        market_value = self._quantize_cash(mark_price * opening_state.settled_quantity)
        snapshot = self._position_snapshot(
            run=run,
            session=session,
            state=opening_state,
            market_value=market_value,
            mark_status=mark_status,
            mark_price=mark_price,
            mark_snapshot_id=mark_snapshot_id,
        )
        payload = snapshot.to_dict()
        event = EngineEvent(
            event_id=_stable_id(
                "evt",
                run.run_id,
                1,
                EventType.DAILY_SNAPSHOT.value,
                session.close_at.isoformat(),
                payload,
            ),
            sequence=1,
            event_type=EventType.DAILY_SNAPSHOT,
            event_time=session.close_at,
            reason_code="held_position_mark_complete",
            payload=payload,
        )
        return PositionMarkResult(
            state=replace(
                opening_state,
                last_mark_price=mark_price,
                last_mark_snapshot_id=mark_snapshot_id,
            ),
            events=(event,),
        )

    def apply_corporate_action(
        self,
        *,
        run: BacktestRunSpec,
        opening_state: UnitState,
        action: CorporateAction,
        applied_at: datetime,
        post_action_mark_price: Decimal,
        mark_snapshot_id: str,
    ) -> CorporateActionApplication:
        """Apply an already PIT-resolved corporate action to one positioned unit."""

        if run.official and not self.policy.official:
            raise BacktestInputError("an official run requires a frozen execution policy")
        if run.execution_policy_version != self.policy.policy_version:
            raise BacktestInputError("run execution_policy_version does not match loaded policy")
        self._validate_unit_state(opening_state)
        if applied_at.tzinfo is None or applied_at.utcoffset() is None:
            raise BacktestInputError("corporate action applied_at must be timezone-aware")
        if applied_at.astimezone(MARKET_TIMEZONE).date() != action.effective_date:
            raise BacktestInputError("corporate action must apply on its effective market date")
        if action.known_at > applied_at:
            raise BacktestInputError("corporate action was not known at application time")
        if opening_state.security_code != action.security_code:
            raise BacktestInputError("corporate action security does not match unit position")
        if post_action_mark_price != self._quantize_price(post_action_mark_price):
            raise BacktestInputError("post-action mark must match the configured price tick")
        if post_action_mark_price <= 0:
            raise BacktestInputError("post-action mark must be positive")
        if not mark_snapshot_id.strip():
            raise BacktestInputError("corporate action mark_snapshot_id must not be empty")

        old_quantity = opening_state.settled_quantity
        new_quantity_decimal = Decimal(old_quantity) * (Decimal("1") + action.bonus_share_ratio)
        if new_quantity_decimal != new_quantity_decimal.to_integral_value():
            raise UnsupportedModeError(
                "corporate action creates a fractional share outside the first P2 rule"
            )
        new_quantity = int(new_quantity_decimal)
        quantity_delta = new_quantity - old_quantity
        cash_dividend = self._quantize_cash(action.cash_dividend_per_share * old_quantity)
        ending_cash = self._quantize_cash(opening_state.cash + cash_dividend)
        ending_dividend_income = self._quantize_cash(opening_state.dividend_income + cash_dividend)
        ending_state = replace(
            opening_state,
            cash=ending_cash,
            settled_quantity=new_quantity,
            dividend_income=ending_dividend_income,
            last_mark_price=post_action_mark_price,
            last_mark_snapshot_id=mark_snapshot_id,
        )
        if opening_state.last_mark_price is None:
            raise BacktestInputError("corporate action requires a prior position mark")
        equity_before = self._quantize_cash(
            opening_state.cash + opening_state.last_mark_price * opening_state.settled_quantity
        )
        equity_after = self._quantize_cash(ending_cash + post_action_mark_price * new_quantity)
        payload = {
            "action_id": action.action_id,
            "security_code": action.security_code,
            "effective_date": action.effective_date.isoformat(),
            "known_at": action.known_at.isoformat(),
            "cash_dividend_per_share": decimal_text(action.cash_dividend_per_share),
            "cash_dividend": decimal_text(cash_dividend),
            "bonus_share_ratio": decimal_text(action.bonus_share_ratio),
            "quantity_before": old_quantity,
            "quantity_after": new_quantity,
            "quantity_delta": quantity_delta,
            "position_cost": decimal_text(opening_state.position_cost),
            "cash_before": decimal_text(opening_state.cash),
            "cash_after": decimal_text(ending_cash),
            "equity_before": decimal_text(equity_before),
            "equity_after": decimal_text(equity_after),
            "source_revision": action.source_revision,
            "action_snapshot_id": action.snapshot_id,
            "mark_price": decimal_text(post_action_mark_price),
            "mark_snapshot_id": mark_snapshot_id,
        }
        action_event = EngineEvent(
            event_id=_stable_id(
                "evt",
                run.run_id,
                1,
                EventType.CORPORATE_ACTION.value,
                applied_at.isoformat(),
                payload,
            ),
            sequence=1,
            event_type=EventType.CORPORATE_ACTION,
            event_time=applied_at,
            reason_code="corporate_action_applied",
            payload=payload,
        )
        events = [action_event]
        if cash_dividend > 0:
            ledger = LedgerEntry(
                entry_id=_stable_id("led", run.run_id, action.action_id),
                reference_id=action.action_id,
                reference_type="corporate_action_cash_dividend",
                unit_id=opening_state.unit_id,
                security_code=action.security_code,
                quantity_delta=quantity_delta,
                postings=(
                    LedgerPosting(f"unit/{opening_state.unit_id}/cash", cash_dividend),
                    LedgerPosting(f"unit/{opening_state.unit_id}/dividend_income", -cash_dividend),
                ),
            )
            ledger_payload = ledger.to_dict()
            events.append(
                EngineEvent(
                    event_id=_stable_id(
                        "evt",
                        run.run_id,
                        2,
                        EventType.LEDGER_ENTRY.value,
                        applied_at.isoformat(),
                        ledger_payload,
                    ),
                    sequence=2,
                    event_type=EventType.LEDGER_ENTRY,
                    event_time=applied_at,
                    reason_code="balanced_cash_dividend_entry",
                    payload=ledger_payload,
                )
            )
        return CorporateActionApplication(state=ending_state, events=tuple(events))

    def run_sell_sequence(
        self,
        *,
        run: BacktestRunSpec,
        signal: BacktestSignal,
        sessions: Sequence[MarketSession],
        opening_state: UnitState,
        corporate_actions: Sequence[CorporateActionStep] = (),
    ) -> BacktestResult:
        """Carry one full-position sell order until the first executable open."""

        if signal.side is not OrderSide.SELL:
            raise BacktestInputError("run_sell_sequence requires a sell signal")
        if not sessions:
            raise BacktestInputError("sell sequence requires at least one market session")
        if any(
            current.session_date >= following.session_date
            for current, following in zip(sessions, sessions[1:], strict=False)
        ):
            raise BacktestInputError("sell sessions must be strictly increasing")
        if opening_state.unit_id != signal.unit_id:
            raise BacktestInputError("opening unit does not match signal unit")
        if opening_state.security_code != signal.security_code:
            raise BacktestInputError("opening position security does not match signal security")
        if opening_state.settled_quantity != signal.quantity:
            raise UnsupportedModeError("the first sell slice requires a full-position quantity")
        self._validate_unit_state(opening_state)
        for session in sessions:
            profile = self._validate_inputs(run, signal, session, opening_state.cash)
            if profile.kind is not ProfileKind.CLOSE_TO_NEXT_OPEN:
                raise UnsupportedModeError(
                    "two-window execution is contract-validated but not implemented"
                )

        session_dates = {session.session_date for session in sessions}
        action_ids: set[str] = set()
        actions_by_date: dict[date, list[CorporateActionStep]] = {}
        for step in corporate_actions:
            if step.action.action_id in action_ids:
                raise BacktestInputError("sell sequence corporate action ids must be unique")
            action_ids.add(step.action.action_id)
            if step.action.effective_date not in session_dates:
                raise BacktestInputError(
                    "sell sequence corporate action requires a matching market session"
                )
            actions_by_date.setdefault(step.action.effective_date, []).append(step)
        for steps in actions_by_date.values():
            steps.sort(key=lambda step: (step.applied_at, step.action.action_id))

        events: list[EngineEvent] = []

        def append_event(
            event_type: EventType,
            event_time: datetime,
            reason_code: str,
            payload: dict[str, Any],
        ) -> None:
            sequence = len(events) + 1
            event_id = _stable_id(
                "evt",
                run.run_id,
                sequence,
                event_type.value,
                event_time.isoformat(),
                payload,
            )
            events.append(
                EngineEvent(
                    event_id=event_id,
                    sequence=sequence,
                    event_type=event_type,
                    event_time=event_time,
                    reason_code=reason_code,
                    payload=payload,
                )
            )

        append_event(
            EventType.SIGNAL_ACCEPTED,
            signal.decision_at,
            "signal_contract_valid",
            {
                "signal_id": signal.signal_id,
                "snapshot_id": signal.snapshot.snapshot_id,
                "decision_at": signal.decision_at.isoformat(),
                "profile_id": signal.profile_id,
                "window_id": signal.window_id,
                "security_code": signal.security_code,
                "side": signal.side.value,
                "unit_id": signal.unit_id,
                "quantity": signal.quantity,
                "aggregate_score": decimal_text(signal.aggregate_score),
                "decision_reason_code": signal.reason_code,
                "decision_version": signal.decision_version,
            },
        )
        first_session = sessions[0]
        intent = OrderIntentRecord(
            intent_id=_stable_id(
                "intent",
                run.run_id,
                signal.signal_id,
                signal.profile_id,
                first_session.session_date,
            ),
            signal_id=signal.signal_id,
            profile_id=signal.profile_id,
            window_id=signal.window_id,
            security_code=signal.security_code,
            side=signal.side,
            unit_id=signal.unit_id,
            quantity=signal.quantity,
            created_at=signal.decision_at,
            eligible_at=first_session.open_at,
            expires_at=None,
            unfilled_policy="carry_until_tradable",
            decision_reason_code=signal.reason_code,
            policy_version=self.policy.policy_version,
        )
        append_event(
            EventType.ORDER_INTENT_CREATED,
            signal.decision_at,
            "next_session_intent_created",
            intent.to_dict(),
        )
        order = OrderRecord(
            order_id=_stable_id(
                "ord",
                run.run_id,
                signal.signal_id,
                signal.profile_id,
                first_session.session_date,
            ),
            intent_id=intent.intent_id,
            signal_id=signal.signal_id,
            profile_id=signal.profile_id,
            window_id=signal.window_id,
            security_code=signal.security_code,
            side=signal.side,
            unit_id=signal.unit_id,
            quantity=signal.quantity,
            eligible_session=first_session.session_date,
            policy_version=self.policy.policy_version,
        )
        append_event(
            EventType.ORDER_CREATED,
            signal.decision_at,
            "next_session_order_created",
            order.to_dict(),
        )

        carried_sessions = 0
        current_state = opening_state
        current_mark_price = opening_state.last_mark_price
        current_mark_snapshot_id = opening_state.last_mark_snapshot_id
        sell_carry_reasons = {
            OpenExecutionState.SUSPENDED: "security_suspended",
            OpenExecutionState.NON_TRADING_OBSERVED: "security_not_trading_observed",
            OpenExecutionState.SELL_BLOCKED_LIMIT_DOWN: "sell_blocked_limit_down",
            OpenExecutionState.MISSING_EVIDENCE: "missing_execution_evidence",
        }

        for session in sessions:
            for step in actions_by_date.get(session.session_date, []):
                if step.applied_at > session.open_at:
                    raise BacktestInputError(
                        "sell sequence corporate action must apply no later than session open"
                    )
                quantity_before = current_state.settled_quantity
                application = self.apply_corporate_action(
                    run=run,
                    opening_state=current_state,
                    action=step.action,
                    applied_at=step.applied_at,
                    post_action_mark_price=step.post_action_mark_price,
                    mark_snapshot_id=step.mark_snapshot_id,
                )
                current_state = application.state
                current_mark_price = current_state.last_mark_price
                current_mark_snapshot_id = current_state.last_mark_snapshot_id
                for action_event in application.events:
                    append_event(
                        action_event.event_type,
                        action_event.event_time,
                        action_event.reason_code,
                        dict(action_event.payload),
                    )
                if current_state.settled_quantity != quantity_before:
                    transition = (
                        "created_to_created_adjusted"
                        if carried_sessions == 0
                        else "carried_to_carried_adjusted"
                    )
                    append_event(
                        EventType.ORDER_STATUS,
                        step.applied_at,
                        "pending_sell_quantity_adjusted_for_corporate_action",
                        {
                            "order_id": order.order_id,
                            "transition": transition,
                            "action_id": step.action.action_id,
                            "quantity_before": quantity_before,
                            "quantity_after": current_state.settled_quantity,
                            "source_snapshot_id": step.action.snapshot_id,
                        },
                    )

            carry_reason = sell_carry_reasons.get(session.open_execution_state)
            if carry_reason is not None:
                transition = "created_to_carried" if carried_sessions == 0 else "carried_to_carried"
                append_event(
                    EventType.ORDER_STATUS,
                    session.open_at,
                    carry_reason,
                    {
                        "order_id": order.order_id,
                        "transition": transition,
                        "open_execution_state": session.open_execution_state.value,
                        "source_snapshot_id": session.snapshot_id,
                    },
                )
                carried_sessions += 1
                if session.close_price is None:
                    mark_status = MarkStatus.CARRIED_FORWARD
                else:
                    current_mark_price = session.close_price
                    current_mark_snapshot_id = session.snapshot_id
                    mark_status = MarkStatus.OBSERVED_CLOSE
                if current_mark_price is None or current_mark_snapshot_id is None:
                    raise BacktestInputError("a carried position requires a prior observable mark")
                market_value = self._quantize_cash(
                    current_mark_price * current_state.settled_quantity
                )
                snapshot = self._position_snapshot(
                    run=run,
                    session=session,
                    state=current_state,
                    market_value=market_value,
                    mark_status=mark_status,
                    mark_price=current_mark_price,
                    mark_snapshot_id=current_mark_snapshot_id,
                )
                append_event(
                    EventType.DAILY_SNAPSHOT,
                    session.close_at,
                    "carried_sell_session_mark_complete",
                    snapshot.to_dict(),
                )
                continue

            if session.open_price is None or session.close_price is None:
                raise BacktestInputError("executable sell session requires open and close prices")
            transition = "created_to_filled" if carried_sessions == 0 else "carried_to_filled"
            append_event(
                EventType.ORDER_STATUS,
                session.open_at,
                "order_filled_at_eligible_open",
                {
                    "order_id": order.order_id,
                    "transition": transition,
                    "open_execution_state": session.open_execution_state.value,
                    "source_snapshot_id": session.snapshot_id,
                },
            )
            return self._complete_sell(
                run=run,
                signal=signal,
                session=session,
                fill_state=current_state,
                fill_quantity=current_state.settled_quantity,
                order=order,
                events=events,
                carried_sessions=carried_sessions,
                append_event=append_event,
            )

        market_value = self._quantize_cash(
            current_mark_price * current_state.settled_quantity
            if current_mark_price is not None
            else Decimal("0")
        )
        return BacktestResult(
            run=run,
            events=tuple(events),
            result_summary={
                "status": "pending_sell",
                "signals": 1,
                "orders": 1,
                "fills": 0,
                "rejections": 0,
                "cancellations": 0,
                "carries": carried_sessions,
                "ending_cash": decimal_text(current_state.cash),
                "position_quantity": current_state.settled_quantity,
                "market_value": decimal_text(market_value),
                "external_pool": decimal_text(current_state.external_pool),
            },
        )

    def _complete_sell(
        self,
        *,
        run: BacktestRunSpec,
        signal: BacktestSignal,
        session: MarketSession,
        fill_state: UnitState,
        fill_quantity: int,
        order: OrderRecord,
        events: list[EngineEvent],
        carried_sessions: int,
        append_event: Callable[[EventType, datetime, str, dict[str, Any]], None],
    ) -> BacktestResult:
        if session.open_price is None:
            raise BacktestInputError("sell fill requires an open price")
        fill_price = self._quantize_price(
            session.open_price
            * (Decimal("1") - self.policy.sell_cost.slippage_bps / Decimal("10000"))
        )
        gross_amount = self._quantize_cash(fill_price * fill_quantity)
        commission = self._quantize_cash(
            max(
                gross_amount * self.policy.sell_cost.commission_rate,
                self.policy.sell_cost.minimum_commission,
            )
        )
        stamp_duty = self._quantize_cash(gross_amount * self.policy.sell_cost.stamp_duty_rate)
        transfer_fee = self._quantize_cash(gross_amount * self.policy.sell_cost.transfer_fee_rate)
        total_fees = commission + stamp_duty + transfer_fee
        net_proceeds = gross_amount - total_fees
        gross_gain = self._quantize_cash(gross_amount - fill_state.position_cost)
        realized_pnl = self._quantize_cash(gross_gain - total_fees)

        fill = FillRecord(
            fill_id=_stable_id(
                "fill", order.order_id, session.open_at.isoformat(), fill_quantity, fill_price
            ),
            order_id=order.order_id,
            filled_at=session.open_at,
            side=signal.side,
            price=fill_price,
            quantity=fill_quantity,
            gross_amount=gross_amount,
            commission=commission,
            stamp_duty=stamp_duty,
            transfer_fee=transfer_fee,
            total_fees=total_fees,
            open_execution_state=session.open_execution_state,
            source_revision=session.source_revision,
            snapshot_id=session.snapshot_id,
        )
        append_event(EventType.FILL, session.open_at, "full_position_sell_fill", fill.to_dict())

        trade_ledger = LedgerEntry(
            entry_id=_stable_id("led", run.run_id, fill.fill_id, "trade"),
            reference_id=fill.fill_id,
            reference_type="fill",
            unit_id=signal.unit_id,
            security_code=signal.security_code,
            quantity_delta=-fill_quantity,
            postings=(
                LedgerPosting(f"unit/{signal.unit_id}/cash", net_proceeds),
                LedgerPosting(
                    f"unit/{signal.unit_id}/position_cost/{signal.security_code}",
                    -fill_state.position_cost,
                ),
                LedgerPosting(f"unit/{signal.unit_id}/commission_expense", commission),
                LedgerPosting(f"unit/{signal.unit_id}/stamp_duty_expense", stamp_duty),
                LedgerPosting(f"unit/{signal.unit_id}/transfer_fee_expense", transfer_fee),
                LedgerPosting(f"unit/{signal.unit_id}/realized_gain", -gross_gain),
            ),
        )
        append_event(
            EventType.LEDGER_ENTRY,
            session.open_at,
            "balanced_full_sell_entry",
            trade_ledger.to_dict(),
        )

        cash_before_settlement = self._quantize_cash(fill_state.cash + net_proceeds)
        pool_before = fill_state.external_pool
        gap = self._quantize_cash(cash_before_settlement - fill_state.target_notional)
        profit_transfer = max(gap, Decimal("0.00"))
        requested_replenishment = max(-gap, Decimal("0.00"))
        replenishment = min(requested_replenishment, pool_before)
        ending_cash = self._quantize_cash(cash_before_settlement - profit_transfer + replenishment)
        ending_pool = self._quantize_cash(pool_before + profit_transfer - replenishment)
        underfunded = self._quantize_cash(
            max(fill_state.target_notional - ending_cash, Decimal("0.00"))
        )
        settlement_id = _stable_id("settle", run.run_id, fill.fill_id, session.session_date)
        if profit_transfer > 0:
            settlement_reason = "unit_profit_transferred"
            transfer_amount = profit_transfer
            unit_cash_posting = -transfer_amount
            pool_cash_posting = transfer_amount
        elif replenishment > 0:
            settlement_reason = (
                "unit_loss_replenished" if underfunded == 0 else "unit_loss_partially_replenished"
            )
            transfer_amount = replenishment
            unit_cash_posting = transfer_amount
            pool_cash_posting = -transfer_amount
        else:
            settlement_reason = (
                "unit_closed_at_target" if underfunded == 0 else "unit_underfunded_pool_empty"
            )
            transfer_amount = Decimal("0.00")
            unit_cash_posting = Decimal("0.00")
            pool_cash_posting = Decimal("0.00")

        append_event(
            EventType.UNIT_SETTLEMENT,
            session.close_at,
            settlement_reason,
            {
                "settlement_id": settlement_id,
                "unit_id": signal.unit_id,
                "target_notional": decimal_text(fill_state.target_notional),
                "cash_before_settlement": decimal_text(cash_before_settlement),
                "profit_transfer": decimal_text(profit_transfer),
                "requested_replenishment": decimal_text(requested_replenishment),
                "actual_replenishment": decimal_text(replenishment),
                "underfunded_amount": decimal_text(underfunded),
                "external_pool_before": decimal_text(pool_before),
                "external_pool_after": decimal_text(ending_pool),
            },
        )
        if transfer_amount > 0:
            settlement_ledger = LedgerEntry(
                entry_id=_stable_id("led", run.run_id, settlement_id),
                reference_id=settlement_id,
                reference_type="unit_close_settlement",
                unit_id=signal.unit_id,
                security_code=None,
                quantity_delta=0,
                postings=(
                    LedgerPosting(f"unit/{signal.unit_id}/cash", unit_cash_posting),
                    LedgerPosting("external_pool/cash", pool_cash_posting),
                ),
            )
            append_event(
                EventType.LEDGER_ENTRY,
                session.close_at,
                "balanced_unit_close_transfer",
                settlement_ledger.to_dict(),
            )

        total_realized_pnl = self._quantize_cash(fill_state.realized_pnl + realized_pnl)
        snapshot = LedgerSnapshot(
            snapshot_id=_stable_id("lsnap", run.run_id, session.session_date, signal.unit_id),
            session_date=session.session_date,
            as_of=session.close_at,
            unit_id=signal.unit_id,
            cash=ending_cash,
            security_code=None,
            position_quantity=0,
            position_cost=Decimal("0.00"),
            market_value=Decimal("0.00"),
            commission_expense=fill_state.commission_expense + commission,
            stamp_duty_expense=fill_state.stamp_duty_expense + stamp_duty,
            transfer_fee_expense=fill_state.transfer_fee_expense + transfer_fee,
            realized_pnl=total_realized_pnl,
            dividend_income=fill_state.dividend_income,
            external_pool=ending_pool,
            total_equity=ending_cash,
            mark_status=MarkStatus.NO_POSITION,
            mark_price=None,
            mark_snapshot_id=None,
        )
        append_event(
            EventType.DAILY_SNAPSHOT,
            session.close_at,
            "unit_close_settlement_complete",
            snapshot.to_dict(),
        )
        return BacktestResult(
            run=run,
            events=tuple(events),
            result_summary={
                "status": "completed",
                "signals": 1,
                "orders": 1,
                "fills": 1,
                "rejections": 0,
                "cancellations": 0,
                "carries": carried_sessions,
                "ending_cash": decimal_text(ending_cash),
                "position_quantity": 0,
                "market_value": "0.00",
                "realized_pnl": decimal_text(total_realized_pnl),
                "external_pool": decimal_text(ending_pool),
                "underfunded_amount": decimal_text(underfunded),
            },
        )

    def _position_snapshot(
        self,
        *,
        run: BacktestRunSpec,
        session: MarketSession,
        state: UnitState,
        market_value: Decimal,
        mark_status: MarkStatus,
        mark_price: Decimal,
        mark_snapshot_id: str,
    ) -> LedgerSnapshot:
        return LedgerSnapshot(
            snapshot_id=_stable_id("lsnap", run.run_id, session.session_date, state.unit_id),
            session_date=session.session_date,
            as_of=session.close_at,
            unit_id=state.unit_id,
            cash=state.cash,
            security_code=state.security_code,
            position_quantity=state.settled_quantity,
            position_cost=state.position_cost,
            market_value=market_value,
            commission_expense=state.commission_expense,
            stamp_duty_expense=state.stamp_duty_expense,
            transfer_fee_expense=state.transfer_fee_expense,
            realized_pnl=state.realized_pnl,
            dividend_income=state.dividend_income,
            external_pool=state.external_pool,
            total_equity=self._quantize_cash(state.cash + market_value),
            mark_status=mark_status,
            mark_price=mark_price,
            mark_snapshot_id=mark_snapshot_id,
        )

    def _validate_unit_state(self, state: UnitState) -> None:
        for value, field_name in (
            (state.target_notional, "target_notional"),
            (state.cash, "cash"),
            (state.position_cost, "position_cost"),
            (state.commission_expense, "commission_expense"),
            (state.stamp_duty_expense, "stamp_duty_expense"),
            (state.transfer_fee_expense, "transfer_fee_expense"),
            (state.realized_pnl, "realized_pnl"),
            (state.dividend_income, "dividend_income"),
            (state.external_pool, "external_pool"),
        ):
            if value != self._quantize_cash(value):
                raise BacktestInputError(f"{field_name} must match the configured cash quantum")
        if state.last_mark_price is None:
            raise BacktestInputError("positioned unit requires a last_mark_price")
        if state.last_mark_price != self._quantize_price(state.last_mark_price):
            raise BacktestInputError("last_mark_price must match the configured price tick")

    def _validate_empty_unit_state(self, state: UnitState) -> None:
        if state.settled_quantity != 0 or state.security_code is not None:
            raise BacktestInputError("buy execution requires an empty unit")
        for value, field_name in (
            (state.target_notional, "target_notional"),
            (state.cash, "cash"),
            (state.position_cost, "position_cost"),
            (state.commission_expense, "commission_expense"),
            (state.stamp_duty_expense, "stamp_duty_expense"),
            (state.transfer_fee_expense, "transfer_fee_expense"),
            (state.realized_pnl, "realized_pnl"),
            (state.dividend_income, "dividend_income"),
            (state.external_pool, "external_pool"),
        ):
            if value != self._quantize_cash(value):
                raise BacktestInputError(f"{field_name} must match the configured cash quantum")

    def _validate_run(self, run: BacktestRunSpec) -> ExecutionProfile:
        if run.official and not self.policy.official:
            raise BacktestInputError("an official run requires a frozen execution policy")
        if run.execution_policy_version != self.policy.policy_version:
            raise BacktestInputError("run execution_policy_version does not match loaded policy")
        profile = self.policy.profile(run.execution_profile_id)
        if profile.schedule_version != run.schedule_version:
            raise BacktestInputError("run schedule_version does not match execution profile")
        return profile

    def _validate_inputs(
        self,
        run: BacktestRunSpec,
        signal: BacktestSignal,
        session: MarketSession,
        initial_cash: Decimal,
    ) -> ExecutionProfile:
        profile = self._validate_run(run)
        if signal.profile_id != profile.profile_id:
            raise BacktestInputError("signal profile_id does not match run profile")
        if run.data_snapshot_id != signal.snapshot.snapshot_id:
            raise BacktestInputError("run data_snapshot_id does not match signal snapshot")
        if session.security_code != signal.security_code:
            raise BacktestInputError("session security does not match signal")
        if session.session_date <= signal.snapshot.market_date:
            raise BacktestInputError("T-close signal may execute only on a later market session")
        if session.open_at <= signal.decision_at:
            raise BacktestInputError("session open must follow signal decision time")
        if profile.kind is ProfileKind.CLOSE_TO_NEXT_OPEN and signal.window_id is not None:
            raise BacktestInputError("close_to_next_open signal must not declare window_id")
        if not initial_cash.is_finite() or initial_cash < 0:
            raise BacktestInputError("initial_cash must be finite and non-negative")
        if initial_cash != self._quantize_cash(initial_cash):
            raise BacktestInputError("initial_cash must match the configured cash quantum")
        for value, field_name in (
            (session.open_price, "open_price"),
            (session.close_price, "close_price"),
        ):
            if value is None:
                continue
            if value != self._quantize_price(value):
                raise BacktestInputError(f"{field_name} must match the configured price tick")
        return profile

    def _rejected_result(
        self,
        *,
        run: BacktestRunSpec,
        events: list[EngineEvent],
        order: OrderRecord,
        session: MarketSession,
        opening_state: UnitState,
        reason_code: str,
        append_event: Callable[[EventType, datetime, str, dict[str, Any]], None],
    ) -> BacktestResult:
        return self._no_fill_result(
            run=run,
            events=events,
            order=order,
            session=session,
            opening_state=opening_state,
            reason_code=reason_code,
            transition="created_to_rejected",
            append_event=append_event,
        )

    def _no_fill_result(
        self,
        *,
        run: BacktestRunSpec,
        events: list[EngineEvent],
        order: OrderRecord,
        session: MarketSession,
        opening_state: UnitState,
        reason_code: str,
        transition: str,
        append_event: Callable[[EventType, datetime, str, dict[str, Any]], None],
    ) -> BacktestResult:
        if transition not in {"created_to_cancelled", "created_to_rejected"}:
            raise ValueError(f"unsupported no-fill transition: {transition}")
        append_event(
            EventType.ORDER_STATUS,
            session.open_at,
            reason_code,
            {
                "order_id": order.order_id,
                "transition": transition,
                "open_execution_state": session.open_execution_state.value,
                "source_snapshot_id": session.snapshot_id,
            },
        )
        snapshot = LedgerSnapshot(
            snapshot_id=_stable_id("lsnap", run.run_id, session.session_date, order.unit_id),
            session_date=session.session_date,
            as_of=session.close_at,
            unit_id=order.unit_id,
            cash=opening_state.cash,
            security_code=None,
            position_quantity=0,
            position_cost=Decimal("0.00"),
            market_value=Decimal("0.00"),
            commission_expense=opening_state.commission_expense,
            stamp_duty_expense=opening_state.stamp_duty_expense,
            transfer_fee_expense=opening_state.transfer_fee_expense,
            realized_pnl=opening_state.realized_pnl,
            dividend_income=opening_state.dividend_income,
            external_pool=opening_state.external_pool,
            total_equity=opening_state.cash,
            mark_status=MarkStatus.NO_POSITION,
            mark_price=None,
            mark_snapshot_id=None,
        )
        append_event(
            EventType.DAILY_SNAPSHOT,
            session.close_at,
            "session_mark_complete",
            snapshot.to_dict(),
        )
        cancelled = transition == "created_to_cancelled"
        summary = {
            "status": "completed_with_cancellation" if cancelled else "completed_with_rejection",
            "signals": 1,
            "orders": 1,
            "fills": 0,
            "rejections": 0 if cancelled else 1,
            "cancellations": 1 if cancelled else 0,
            "ending_cash": decimal_text(opening_state.cash),
            "position_quantity": 0,
            "market_value": "0.00",
            "total_equity": decimal_text(opening_state.cash),
        }
        return BacktestResult(run=run, events=tuple(events), result_summary=summary)

    def _quantize_price(self, value: Decimal) -> Decimal:
        return value.quantize(self.policy.money.price_tick, rounding=ROUND_HALF_UP)

    def _quantize_cash(self, value: Decimal) -> Decimal:
        return value.quantize(self.policy.money.cash_quantum, rounding=ROUND_HALF_UP)

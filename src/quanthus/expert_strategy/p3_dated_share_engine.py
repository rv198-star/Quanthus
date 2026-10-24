"""Opt-in retrospective listed-share accounting; legacy P2 is unchanged.

Economic entitlement is part of the position from ex-date. Availability is a
separate research assumption at the publisher's listing session, never an
assertion of account credit or historical knowledge. Daily execution slices
share one original intent; each actual fill retains the unchanged P2 costs.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from typing import Any

from quanthus.backtest.contracts import BacktestResult, EventType, decimal_text
from quanthus.backtest.engine import BacktestInputError, _stable_id
from quanthus.backtest.portfolio_replay import _state_from_snapshot
from quanthus.expert_strategy.p3_historical_action_successor import SupplementedEngine

VERSION = "p3-retrospective-listed-share-availability-v1"
AVAILABILITY = "SIMULATED_LISTED_SESSION_OPEN_ACTUAL_CREDIT_UNKNOWN"


class DatedShareEngine(SupplementedEngine):
    """A separately admitted research matcher, with explicit locked quantities."""

    def __init__(self, policy: Any, cutoff: Any, snapshot: str, actions: Any, facts: Any):
        super().__init__(policy, cutoff, snapshot, actions)
        self.dated = {
            "official-action:" + f.evidence_sha256: f
            for f in facts
            if f.bonus_share_ratio and f.share_listing_date != f.ex_date
        }
        if any(
            f.share_listing_date is None
            or f.share_listing_date <= f.ex_date
            or f.share_credit_date is not None
            or f.cash_payment_date != f.ex_date
            for f in self.dated.values()
        ):
            raise BacktestInputError("DATED_SHARE_POLICY_SCOPE")
        self.lots: dict[tuple[str, str], list[tuple[str, int, date]]] = {}
        self.applied: set[tuple[str, str]] = set()

    def reset_arm(self) -> None:
        self.lots.clear()
        self.applied.clear()

    def _annotate_snapshot(self, event: Any, target: Any) -> Any:
        if event.event_type is not EventType.DAILY_SNAPSHOT:
            return event
        state = _state_from_snapshot(event, target_notional=target)
        locked = self.locked_quantity(state, date.fromisoformat(event.payload["session_date"]))
        payload = {
            **event.payload,
            "economic_quantity": state.settled_quantity,
            "locked_quantity": locked,
            "tradable_quantity": state.settled_quantity - locked,
            "share_availability_model": AVAILABILITY,
            "actual_share_credit_date": None,
        }
        return replace(
            event, payload=payload, event_id=_stable_id("evt", event.event_id, VERSION, payload)
        )

    def mark_position_session(self, **kwargs: Any) -> Any:
        result = super().mark_position_session(**kwargs)
        state = kwargs["opening_state"]
        if (state.unit_id, state.security_code) not in self.lots:
            return result
        return replace(
            result,
            events=tuple(self._annotate_snapshot(e, state.target_notional) for e in result.events),
        )

    def locked_quantity(self, state: Any, day: date) -> int:
        locked = sum(
            quantity
            for _, quantity, listing in self.lots.get((state.unit_id, state.security_code), [])
            if day < listing
        )
        if not 0 <= locked <= state.settled_quantity:
            raise BacktestInputError("DATED_SHARE_LOT_RECONCILIATION")
        return locked

    def apply_corporate_action(self, **kwargs: Any) -> Any:
        action, before = kwargs["action"], kwargs["opening_state"]
        fact = self.dated.get(action.action_id)
        if fact is None:
            return super().apply_corporate_action(**kwargs)
        key = (before.unit_id, action.action_id)
        if key in self.applied:
            raise BacktestInputError("DATED_SHARE_DUPLICATE_ENTITLEMENT")
        if self.locked_quantity(before, action.effective_date):
            raise BacktestInputError("DATED_SHARE_OVERLAPPING_UNQUALIFIED_ENTITLEMENT")
        result = super().apply_corporate_action(**kwargs)
        added = result.state.settled_quantity - before.settled_quantity
        assert fact.share_listing_date is not None
        self.lots.setdefault((before.unit_id, before.security_code), []).append(
            (action.action_id, added, fact.share_listing_date)
        )
        self.applied.add(key)
        events = []
        for event in result.events:
            if event.event_type is EventType.CORPORATE_ACTION:
                payload = {
                    **event.payload,
                    "share_availability_model": AVAILABILITY,
                    "actual_share_credit_date": None,
                    "simulated_available_from": fact.share_listing_date.isoformat(),
                    "economic_quantity": result.state.settled_quantity,
                    "locked_quantity": added,
                    "tradable_quantity": before.settled_quantity,
                }
                event = replace(
                    event,
                    event_id=_stable_id("evt", event.event_id, VERSION, payload),
                    payload=payload,
                )
            events.append(event)
        return replace(result, events=tuple(events))

    def _complete_sell(self, **kwargs: Any) -> Any:
        state, session = kwargs["fill_state"], kwargs["session"]
        available = state.settled_quantity - self.locked_quantity(state, session.session_date)
        quantity = min(kwargs["fill_quantity"], available)
        if quantity:
            return super()._complete_sell(**{**kwargs, "fill_quantity": quantity})
        marked = self.mark_position_session(run=kwargs["run"], session=session, opening_state=state)
        for event in marked.events:
            kwargs["append_event"](
                event.event_type, event.event_time, event.reason_code, dict(event.payload)
            )
        return BacktestResult(
            run=kwargs["run"],
            events=tuple(kwargs["events"]),
            result_summary={"status": "pending_sell", "fills": 0},
        )

    def run_sell_sequence(self, **kwargs: Any) -> Any:
        steps = kwargs.get("corporate_actions", ())
        opening = kwargs["opening_state"]
        if not any(s.action.action_id in self.dated for s in steps) and not any(
            listing > kwargs["sessions"][0].session_date
            for _, _, listing in self.lots.get((opening.unit_id, opening.security_code), [])
        ):
            return super().run_sell_sequence(**kwargs)
        sessions, signal, run = kwargs["sessions"], kwargs["signal"], kwargs["run"]
        if not sessions or any(
            a.session_date >= b.session_date for a, b in zip(sessions, sessions[1:], strict=False)
        ):
            raise BacktestInputError("DATED_SHARE_SESSION_ORDER")
        ids = [s.action.action_id for s in steps]
        if len(ids) != len(set(ids)) or any(
            s.action.effective_date not in {d.session_date for d in sessions} for s in steps
        ):
            raise BacktestInputError("DATED_SHARE_ACTION_CLOSURE")
        current, pending, merged = opening, signal.quantity, []
        root_order = None
        carries = 0
        for session in sessions:
            first_slice = root_order is None
            result = super().run_sell_sequence(
                run=run,
                signal=replace(signal, quantity=pending),
                sessions=(session,),
                opening_state=current,
                corporate_actions=tuple(
                    s for s in steps if s.action.effective_date == session.session_date
                ),
            )
            if root_order is None:
                root_order = next(
                    e.payload["order_id"]
                    for e in result.events
                    if e.event_type is EventType.ORDER_CREATED
                )
            adjusted = pending
            fills = []
            for event in result.events:
                if event.event_type is EventType.CORPORATE_ACTION:
                    numerator = adjusted * event.payload["quantity_after"]
                    if numerator % event.payload["quantity_before"]:
                        raise BacktestInputError("DATED_SHARE_PENDING_FRACTION")
                    adjusted = numerator // event.payload["quantity_before"]
                if event.event_type is EventType.FILL:
                    fills.append(event)
            remaining = adjusted - sum(e.payload["quantity"] for e in fills)
            if remaining < 0:
                raise BacktestInputError("DATED_SHARE_ORDER_OVERFILL")
            for event in result.events:
                if not first_slice and event.event_type in {
                    EventType.SIGNAL_ACCEPTED,
                    EventType.ORDER_INTENT_CREATED,
                    EventType.ORDER_CREATED,
                }:
                    continue
                if event.reason_code == "order_filled_at_eligible_open":
                    continue
                payload = dict(event.payload)
                if "order_id" in payload:
                    payload["execution_slice_order_id"] = payload["order_id"]
                    payload["order_id"] = root_order
                merged.append(
                    self._annotate_snapshot(
                        replace(event, payload=payload), opening.target_notional
                    )
                )
            snap = next(
                e for e in reversed(result.events) if e.event_type is EventType.DAILY_SNAPSHOT
            )
            current = _state_from_snapshot(snap, target_notional=opening.target_notional)
            if remaining:
                carries += 1
                # Carry occurs after an actual partial fill. Its ledger boundary
                # is the post-fill state; the fill is separately fully audited.
                payload = {
                    "order_id": root_order,
                    "transition": "created_to_carried" if carries == 1 else "carried_to_carried",
                    "remaining_quantity": remaining,
                    "economic_quantity": current.settled_quantity,
                    "locked_quantity": self.locked_quantity(current, session.session_date),
                    "source_snapshot_id": session.snapshot_id,
                    "partial_fill_ids": [e.payload["fill_id"] for e in fills],
                    "quantity_before_session": pending,
                    "quantity_after_action": adjusted,
                    "quantity_filled": adjusted - remaining,
                    "share_availability_model": AVAILABILITY,
                }
                merged.append(
                    replace(
                        result.events[0],
                        event_type=EventType.ORDER_STATUS,
                        event_time=session.open_at + timedelta(microseconds=1),
                        reason_code="dated_share_residual_order_carried",
                        payload=payload,
                    )
                )
            pending = remaining
            if not pending:
                break
        events = tuple(
            replace(
                e,
                sequence=i,
                event_id=_stable_id("evt", run.run_id, VERSION, i, e.event_type, e.payload),
            )
            for i, e in enumerate(sorted(merged, key=lambda e: e.event_time), 1)
        )
        return BacktestResult(
            run=run,
            events=events,
            result_summary={
                "status": "pending_sell" if pending else "completed_sell",
                "signals": 1,
                "orders": 1,
                "fills": sum(e.event_type is EventType.FILL for e in events),
                "carries": carries,
                "ending_cash": decimal_text(current.cash),
                "position_quantity": current.settled_quantity,
                "pending_quantity": pending,
                "external_pool": decimal_text(current.external_pool),
            },
        )

"""Reconcile source orders with an account's reservations. Live and replay share these rules."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from quanthus.capabilities.execution.order_evidence import OrderWindow as OrderWindow
from quanthus.capabilities.execution.order_evidence import (
    LaterOrderResult,
    LaterOrderStatus,
    SourceOrder,
    WindowResult,
    WindowStatus,
    evidence_body,
)
from quanthus.capabilities.execution.order_evidence import (
    SyntheticOrderSource as SyntheticOrderSource,
)
from quanthus.capabilities.execution.order_evidence import require_admission as require_admission
from quanthus.capabilities.execution.order_evidence import restore_window as restore_window
from quanthus.capabilities.execution.order_evidence import window_body as window_body
from quanthus.product_daily.application.funding_bridge import (
    BridgeState,
    CycleFillProfile,
    ExecutionReport,
    PendingObligation,
    SyntheticContext,
)
from quanthus.product_daily.contracts import CommandEnvelope
from quanthus.product_daily.funding import (
    BucketPhase,
    CycleSettlementEvidence,
    SettlementRequest,
    settle_batch,
    settle_invocation_payload,
)
from quanthus.shared.fact_contracts import FactError


@dataclass(frozen=True)
class OrderLineage:
    provider: str
    source_digest: str
    order_id: str
    order_revision: int
    observation_id: str
    parent_id: str
    attempt_id: str
    command_id: str
    reservation_id: str | None
    submitted_at: str
    order_effective_at: str
    order_available_at: str
    fill_occurred_at: str
    fill_available_at: str
    received_at: str
    source_sequence: str


def identify_buy(
    source: SyntheticOrderSource,
    report: ExecutionReport,
    at: datetime,
    state: BridgeState,
    *,
    received_at: datetime,
) -> tuple[SourceOrder, OrderLineage]:
    order = source.execution(report, at)
    entry = next(
        (
            r
            for r in state.funding.entry_reservations
            if r.command_id == order.command_id
            and r.security_code == order.security_id
            and r.opportunity_id == order.attempt_id
        ),
        None,
    )
    event = next(f for f in source.fills if f.sequence == report.source_sequence)
    return order, OrderLineage(
        source.source_id,
        source.identity,
        order.order_id,
        order.revision,
        order.observation_id,
        order.parent_id,
        order.attempt_id,
        order.command_id,
        entry.reservation_id if entry else None,
        order.submitted_at.isoformat(),
        order.effective_at.isoformat(),
        order.available_at.isoformat(),
        report.occurred_at.isoformat(),
        event.available_at.isoformat(),
        received_at.isoformat(),
        report.source_sequence,
    )


def check_original_reservation(
    result: WindowResult,
    source: SyntheticOrderSource,
    window: OrderWindow,
    state: BridgeState,
    obligations: tuple[PendingObligation, ...],
) -> None:
    originals = [
        r for r in state.funding.entry_reservations if r.opportunity_id == window.parent_id
    ]
    order = result.order
    if not originals:
        if order is not None and order.fee != 0:
            raise FactError("C13_ORIGINAL_ORDER_FACT_REQUIRED", "unallocated terminal fee")
        return
    if (
        result.status != WindowStatus.ZERO
        or order is None
        or len(originals) != 1
        or originals[0].command_id != order.command_id
        or not any(
            o.reservation_id == originals[0].reservation_id and o.status == "CANCELLED"
            for o in obligations
        )
        or not any(
            s.settlement_id == order.zero_identity(source.source_id)
            for s in state.funding.completed_settlements
        )
    ):
        raise FactError(
            "C13_ORIGINAL_ORDER_FACT_REQUIRED", "original reservation or corrected fee unresolved"
        )


def check_buy_route(
    order: SourceOrder,
    lineage: OrderLineage,
    report: ExecutionReport,
    profile: CycleFillProfile,
    obligation: PendingObligation | None,
    later: dict[str, Any] | None,
    owner: dict[str, Any] | None,
    reserved_at: datetime | None,
) -> None:
    if owner is not None and (
        owner["parent_opportunity_id"] != order.parent_id
        or owner["attempt_identity"] != order.attempt_id
    ):
        raise FactError(
            "C13_ATTEMPT_BINDING_MISMATCH",
            "reservation belongs to a different source-order attempt",
        )
    if reserved_at is not None and order.submitted_at < reserved_at:
        raise FactError(
            "ORDER_RESERVATION_TIME_MISMATCH", "order submission precedes the reservation"
        )
    if later and order.attempt_id == order.parent_id:
        raise FactError(
            "C13_ORIGINAL_FILL_AFTER_LATER_RESERVATION", "retain original fill and later hold"
        )
    if (
        not obligation
        or obligation.status != "OPEN"
        or obligation.reservation_id != lineage.reservation_id
    ):
        raise FactError(
            "ORDER_OBLIGATION_UNRECONCILED", "source order has no matching open reservation"
        )
    if order.parent_id != order.attempt_id and (
        not later
        or later["attempt_identity"] != order.attempt_id
        or later["obligation_id"] != obligation.obligation_id
        or order.submitted_at < datetime.fromisoformat(later["created_at"])
        or report.occurred_at < datetime.fromisoformat(later["execution_at"])
    ):
        raise FactError(
            "C13_ATTEMPT_BINDING_MISMATCH", "source order differs from persisted later attempt"
        )
    if (
        order.status != "filled"
        or not order.corrections_clear
        or (order.total, order.filled, order.cancelled, order.fee)
        != (
            profile.order_total_quantity,
            profile.final_cumulative_fill_quantity,
            profile.cancelled_quantity,
            profile.confirmed_fee,
        )
        or order.filled != report.quantity
        or order.cancelled != 0
        or obligation.quantity != report.quantity
        or order.effective_at < report.occurred_at
    ):
        raise FactError("ORDER_TERMINAL_UNRECONCILED", "confirmed fill differs from terminal order")


@dataclass(frozen=True)
class WindowTransition:
    state: BridgeState
    closed_obligation: PendingObligation | None
    receipt: dict[str, Any]


def reconcile_window(
    source: SyntheticOrderSource,
    context: SyntheticContext,
    window: OrderWindow,
    state: BridgeState,
    obligations: tuple[PendingObligation, ...],
    reservation_times: Mapping[str, datetime],
    *,
    unresolved: bool = False,
) -> WindowTransition:
    result: WindowResult = source.window_result(window, context.as_of)
    receipt = evidence_body(source, window, context.as_of)
    receipt.update(
        {
            "reservation_id": None,
            "released": False,
            "fee_posted": "0.00",
            "settlement_id": None,
            "account_revision": state.account_revision,
        }
    )
    if unresolved and result.status in {WindowStatus.CLEAR, WindowStatus.ZERO}:
        receipt.update(status=WindowStatus.UNKNOWN.value, reason="UNRESOLVED_ACCOUNT_REPORT")
        return WindowTransition(state, None, receipt)
    reservations = [
        r for r in state.funding.entry_reservations if r.opportunity_id == window.parent_id
    ]
    if result.status == WindowStatus.CLEAR and reservations:
        receipt.update(
            status=WindowStatus.UNKNOWN.value, reason="ORIGINAL_RESERVATION_UNRECONCILED"
        )
    if not result.order or not result.order.terminal_zero or result.status != WindowStatus.ZERO:
        return WindowTransition(state, None, receipt)
    order = result.order
    matched = next(
        (
            r
            for r in reservations
            if r.command_id == order.command_id
            and r.security_code == order.security_id
            and r.quantity == order.total
        ),
        None,
    )
    obligation = next(
        (ob for ob in obligations if matched and ob.reservation_id == matched.reservation_id), None
    )
    if (
        reservations
        and (len(reservations) != 1 or not obligation)
        or (not obligation and order.fee != 0)
    ):
        receipt.update(
            status=WindowStatus.UNKNOWN.value, reason="ORIGINAL_RESERVATION_UNRECONCILED"
        )
        return WindowTransition(state, None, receipt)
    if not obligation:
        return WindowTransition(state, None, receipt)
    receipt["reservation_id"] = obligation.reservation_id
    reserved_at = reservation_times.get(obligation.reservation_id)
    if reserved_at is None or order.submitted_at < reserved_at:
        receipt.update(status=WindowStatus.UNKNOWN.value, reason="ORDER_RESERVATION_TIME_MISMATCH")
        return WindowTransition(state, None, receipt)
    return _release_terminal_zero(
        source,
        order,
        obligation,
        state,
        reserved_at,
        receipt,
        release_reason="ORIGINAL_HOLD_UNRECONCILED",
    )


def _release_terminal_zero(
    source: SyntheticOrderSource,
    order: SourceOrder,
    obligation: PendingObligation,
    state: BridgeState,
    reserved_at: datetime | None,
    receipt: dict[str, Any],
    *,
    release_reason: str,
) -> WindowTransition:
    if reserved_at is None or order.submitted_at < reserved_at:
        receipt.update(status=WindowStatus.UNKNOWN.value, reason="ORDER_RESERVATION_TIME_MISMATCH")
        return WindowTransition(state, None, receipt)
    settlement_id = order.zero_identity(source.source_id)
    if obligation.status == "CANCELLED" and any(
        s.settlement_id == settlement_id for s in state.funding.completed_settlements
    ):
        receipt["settlement_id"] = settlement_id
        return WindowTransition(state, None, receipt)
    fee = order.fee
    bucket = state.funding.bucket(obligation.bucket_id)
    if (
        obligation.status != "OPEN"
        or bucket.phase != BucketPhase.RESERVED
        or bucket.cycle_id != obligation.cycle_id
        or fee is None
        or fee > bucket.cash
        or state.account.quantity(order.security_id)
    ):
        receipt.update(status=WindowStatus.UNKNOWN.value, reason=release_reason)
        return WindowTransition(state, None, receipt)
    closed = replace(bucket, phase=BucketPhase.CLOSED, cash=bucket.cash - fee)
    funded = replace(
        state.funding,
        buckets=tuple(
            closed if b.bucket_id == closed.bucket_id else b for b in state.funding.buckets
        ),
    )
    request = SettlementRequest(
        settlement_id,
        closed.bucket_id,
        obligation.cycle_id,
        CycleSettlementEvidence(True, True, True, True, True),
    )
    command = CommandEnvelope.create(
        settlement_id, settle_invocation_payload((request,)), funded.revision
    )
    settlement = settle_batch(funded, command, (request,))
    if not settlement.accepted:
        raise FactError("SETTLEMENT_NOT_READY", "terminal-zero settlement was refused")
    account = replace(
        state.account,
        cash=state.account.cash - fee,
        revision=state.account_revision + 1,
        event_sequence=state.account.event_sequence + 1,
        fact_count=state.account.fact_count + 1,
    )
    if account.cash != settlement.state.cash_total:
        raise FactError("BASELINE_IDENTITY_MISMATCH", "terminal fee account/funding differs")
    receipt.update(
        released=True,
        fee_posted=format(fee, ".2f"),
        settlement_id=settlement_id,
        account_revision=account.revision,
        fee_postings=[
            ["ASSET_CASH", format(-fee, ".2f")],
            ["EXPENSE_CONFIRMED_FEE", format(fee, ".2f")],
        ],
        funding_entries=[entry.to_dict() for entry in settlement.ledger_entries],
    )
    return WindowTransition(
        BridgeState(account, settlement.state, account.revision),
        replace(obligation, status="CANCELLED"),
        receipt,
    )


def reconcile_later(
    source: SyntheticOrderSource,
    context: SyntheticContext,
    attempt_identity: str,
    later: Mapping[str, Any] | None,
    state: BridgeState,
    obligations: tuple[PendingObligation, ...],
    *,
    unresolved: bool = False,
) -> WindowTransition:
    """Release only the bound later attempt's hold on a proven terminal zero fill."""

    result: LaterOrderResult = source.terminal_order(attempt_identity, context.as_of)
    order = result.order
    receipt: dict[str, Any] = {
        "schema": "p12-later-order-result-v1",
        "source_digest": source.identity,
        "source_id": source.source_id,
        "account_id": source.account_id,
        "mode": "synthetic",
        "attempt_id": attempt_identity,
        "parent_id": later["parent_opportunity_id"] if later else None,
        "as_of": context.as_of.isoformat(),
        "status": result.status.value,
        "reason": result.reason,
        "order_id": order.order_id if order else None,
        "order_revision": order.revision if order else None,
        "order_observation": order.observation_id if order else None,
        "reservation_id": None,
        "released": False,
        "fee_posted": "0.00",
        "settlement_id": None,
        "account_revision": state.account_revision,
    }
    if unresolved and result.status is LaterOrderStatus.ZERO:
        receipt.update(status=LaterOrderStatus.UNKNOWN.value, reason="UNRESOLVED_ACCOUNT_REPORT")
        return WindowTransition(state, None, receipt)
    if order is None or result.status is not LaterOrderStatus.ZERO or not order.terminal_zero:
        return WindowTransition(state, None, receipt)
    if (
        not later
        or later["attempt_identity"] != attempt_identity
        or order.parent_id != later["parent_opportunity_id"]
        or order.attempt_id != attempt_identity
        or order.command_id != later["command_id"]
        or order.submitted_at < datetime.fromisoformat(later["created_at"])
    ):
        receipt.update(
            status=LaterOrderStatus.UNKNOWN.value, reason="C13_ATTEMPT_BINDING_MISMATCH"
        )
        return WindowTransition(state, None, receipt)
    matched = next(
        (
            r
            for r in state.funding.entry_reservations
            if r.reservation_id == later["obligation_id"]
            and r.command_id == order.command_id
            and r.security_code == order.security_id
            and r.quantity == order.total
        ),
        None,
    )
    obligation = next(
        (ob for ob in obligations if matched and ob.reservation_id == matched.reservation_id),
        None,
    )
    if matched is None or obligation is None or obligation.obligation_id != later["obligation_id"]:
        receipt.update(
            status=LaterOrderStatus.UNKNOWN.value, reason="LATER_RESERVATION_UNRECONCILED"
        )
        return WindowTransition(state, None, receipt)
    receipt["reservation_id"] = obligation.reservation_id
    return _release_terminal_zero(
        source,
        order,
        obligation,
        state,
        datetime.fromisoformat(later["created_at"]),
        receipt,
        release_reason="LATER_HOLD_UNRECONCILED",
    )

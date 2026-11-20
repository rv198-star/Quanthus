"""Immutable, composition-owned order feed for the P12 synthetic account lane."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from quanthus.capabilities.execution.facts import ExecutionReport
from quanthus.shared.fact_contracts import FactError, digest

SOURCE_SCHEMA = "p12-synthetic-order-facts-v1"


def _identity(*values: str) -> None:
    if not all(isinstance(v, str) and v.strip() for v in values):
        raise FactError("INVALID_ORDER_SOURCE", "source identities must be non-empty strings")


def _time(*values: datetime) -> None:
    if not all(isinstance(v, datetime) and v.utcoffset() is not None for v in values):
        raise FactError("INVALID_ORDER_SOURCE", "source timestamps must include a timezone")


def _integer(value: int, minimum: int) -> bool:
    return type(value) is int and value >= minimum


def _amount(value: Decimal) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value >= 0


@dataclass(frozen=True)
class OrderWindow:
    parent_id: str
    security_id: str
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        _identity(self.parent_id, self.security_id)
        _time(self.start, self.end)
        if self.start > self.end:
            raise FactError("INVALID_ORDER_SOURCE", "inverted order submission window")


@dataclass(frozen=True)
class WindowStatement:
    """The provider attests the submitted order set through this watermark."""

    receipt_id: str
    window: OrderWindow
    available_at: datetime
    watermark: datetime
    complete: bool
    order_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _identity(self.receipt_id, *self.order_ids)
        _time(self.available_at, self.watermark)
        if (
            type(self.complete) is not bool
            or self.watermark > self.available_at
            or type(self.order_ids) is not tuple
            or len(set(self.order_ids)) != len(self.order_ids)
        ):
            raise FactError("INVALID_ORDER_SOURCE", "invalid coverage statement")


@dataclass(frozen=True)
class SourceOrder:
    """Provider observation. A successor observation names the one it replaces."""

    observation_id: str
    order_id: str
    revision: int
    parent_id: str
    attempt_id: str
    command_id: str
    security_id: str
    submitted_at: datetime
    effective_at: datetime
    available_at: datetime
    status: str
    total: int
    filled: int | None
    cancelled: int | None
    fee: Decimal | None
    corrections_clear: bool
    supersedes: str | None = None

    def __post_init__(self) -> None:
        _identity(
            self.observation_id,
            self.order_id,
            self.parent_id,
            self.attempt_id,
            self.command_id,
            self.security_id,
        )
        _time(self.submitted_at, self.effective_at, self.available_at)
        if (
            not _integer(self.revision, 1)
            or not _integer(self.total, 1)
            or any(v is not None and not _integer(v, 0) for v in (self.filled, self.cancelled))
            or self.status not in {"open", "cancelled", "rejected", "filled"}
            or self.submitted_at > self.effective_at
            or self.effective_at > self.available_at
            or (
                self.fee is not None
                and (not _amount(self.fee) or self.fee != self.fee.quantize(Decimal("0.01")))
            )
            or type(self.corrections_clear) is not bool
        ):
            raise FactError("INVALID_ORDER_SOURCE", "invalid order observation")

    def zero_identity(self, provider: str) -> str:
        return "terminal-zero:" + digest(
            [provider, self.order_id, self.revision, self.observation_id, str(self.fee)]
        )

    @property
    def terminal_zero(self) -> bool:
        return (
            self.status in {"cancelled", "rejected"}
            and self.filled == 0
            and self.cancelled == self.total
            and self.fee is not None
            and self.corrections_clear
        )


@dataclass(frozen=True)
class SourceExecution:
    """A source event identifies its order, independently of report IDs and economics."""

    sequence: str
    order_id: str
    revision: int
    security_id: str
    occurred_at: datetime
    available_at: datetime
    quantity: int
    price: Decimal
    fee: Decimal

    def __post_init__(self) -> None:
        _identity(self.sequence, self.order_id, self.security_id)
        _time(self.occurred_at, self.available_at)
        if (
            not _integer(self.revision, 1)
            or not _integer(self.quantity, 1)
            or not _amount(self.price)
            or self.price == 0
            or not _amount(self.fee)
            or self.occurred_at > self.available_at
        ):
            raise FactError("INVALID_ORDER_SOURCE", "invalid execution observation")


class LaterOrderStatus(str, Enum):
    UNKNOWN = "NEEDS_RECONCILIATION"
    ZERO = "TERMINAL_ZERO_FILL_RECONCILED"
    FILLED = "ANY_FILL"


@dataclass(frozen=True)
class LaterOrderResult:
    status: LaterOrderStatus
    reason: str
    order: SourceOrder | None = None


class WindowStatus(str, Enum):
    UNKNOWN = "NEEDS_RECONCILIATION"
    CLEAR = "NEVER_SUBMITTED"
    ZERO = "TERMINAL_ZERO_FILL_RECONCILED"
    FILLED = "ANY_FILL"


@dataclass(frozen=True)
class WindowResult:
    status: WindowStatus
    reason: str
    coverage_id: str | None
    order: SourceOrder | None = None


@dataclass(frozen=True)
class SyntheticOrderSource:
    """Inject once during trusted bootstrap. Business commands cannot append source claims."""

    account_id: str
    source_id: str
    version: str
    windows: tuple[OrderWindow, ...]
    statements: tuple[WindowStatement, ...]
    orders: tuple[SourceOrder, ...]
    fills: tuple[SourceExecution, ...]

    def __post_init__(self) -> None:
        _identity(self.account_id, self.source_id, self.version)
        if not self.account_id.startswith("SYN-"):
            raise FactError("UNAUTHORIZED_CONTEXT", "order source requires synthetic account")
        if type(self.windows) is not tuple or len(set(self.windows)) != len(self.windows):
            raise FactError("INVALID_ORDER_SOURCE", "invalid registered windows")
        for rows, ids in [
            (self.statements, [x.receipt_id for x in self.statements]),
            (self.orders, [x.observation_id for x in self.orders]),
            (self.fills, [x.sequence for x in self.fills]),
        ]:
            if type(rows) is not tuple or len(ids) != len(set(ids)):
                raise FactError("INVALID_ORDER_SOURCE", "mutable or duplicate provider evidence")
        chains: dict[str, SourceOrder] = {}
        for order in self.orders:
            old = chains.get(order.order_id)
            if old is None:
                if order.supersedes is not None:
                    raise FactError("INVALID_ORDER_SOURCE", "missing predecessor")
            elif (
                order.supersedes != old.observation_id
                or order.revision < old.revision
                or order.available_at < old.available_at
                or (
                    order.parent_id,
                    order.attempt_id,
                    order.command_id,
                    order.security_id,
                    order.submitted_at,
                )
                != (
                    old.parent_id,
                    old.attempt_id,
                    old.command_id,
                    old.security_id,
                    old.submitted_at,
                )
            ):
                raise FactError(
                    "INVALID_ORDER_SOURCE", "broken observation chain or changed lineage"
                )
            chains[order.order_id] = order
        if len({o.command_id for o in chains.values()}) != len(chains):
            raise FactError("INVALID_ORDER_SOURCE", "one submission has multiple source orders")

    def encoded(self) -> str:
        def convert(v: object) -> str:
            if isinstance(v, datetime):
                return v.isoformat()
            if isinstance(v, Decimal):
                return format(v, "f")
            raise TypeError(type(v).__name__)

        return json.dumps(
            {"schema": SOURCE_SCHEMA, **asdict(self)},
            default=convert,
            sort_keys=True,
            separators=(",", ":"),
        )

    @property
    def identity(self) -> str:
        return digest(json.loads(self.encoded()))

    def window_result(self, window: OrderWindow, at: datetime) -> WindowResult:
        _time(at)
        if window not in self.windows:
            return WindowResult(WindowStatus.UNKNOWN, "UNREGISTERED_ORIGINAL_WINDOW", None)
        observations = {
            o.order_id: o
            for o in self.orders
            if o.available_at <= at
            and o.parent_id == window.parent_id
            and o.attempt_id == window.parent_id
        }
        # A visible execution whose order has not arrived cannot establish a silent window.
        known_orders = {o.order_id for o in self.orders if o.available_at <= at}
        if any(
            f.security_id == window.security_id
            and f.available_at <= at
            and f.order_id not in known_orders
            for f in self.fills
        ):
            return WindowResult(WindowStatus.UNKNOWN, "UNATTRIBUTED_SOURCE_EXECUTION", None)
        original = tuple(observations.values())
        if any(
            o.security_id != window.security_id or not window.start <= o.submitted_at <= window.end
            for o in original
        ):
            return WindowResult(WindowStatus.UNKNOWN, "ORIGINAL_WINDOW_IDENTITY_CONFLICT", None)
        receipts = [s for s in self.statements if s.window == window and s.available_at <= at]
        receipt = max(receipts, key=lambda s: s.available_at, default=None)
        ref = receipt.receipt_id if receipt else None
        for order in original:
            if (order.filled or 0) > 0 or any(
                f.order_id == order.order_id and f.available_at <= at for f in self.fills
            ):
                return WindowResult(WindowStatus.FILLED, "ORIGINAL_FILL_RECORDED", ref, order)
        if (
            not receipt
            or not receipt.complete
            or receipt.watermark < window.end
            or set(receipt.order_ids) != set(observations)
            or sum(s.available_at == receipt.available_at for s in receipts) != 1
        ):
            return WindowResult(WindowStatus.UNKNOWN, "ORIGINAL_COVERAGE_INCOMPLETE", ref)
        if not original:
            return WindowResult(WindowStatus.CLEAR, "COVERED_NO_SUBMISSION", ref)
        if len(original) == 1 and original[0].terminal_zero:
            return WindowResult(WindowStatus.ZERO, "CONFIRMED_TERMINAL_ZERO", ref, original[0])
        return WindowResult(
            WindowStatus.UNKNOWN,
            "ORIGINAL_ORDER_UNRECONCILED",
            ref,
            original[0] if len(original) == 1 else None,
        )

    def execution(self, report: ExecutionReport, at: datetime) -> SourceOrder:
        event = next(
            (
                f
                for f in self.fills
                if f.sequence == report.source_sequence and f.available_at <= at
            ),
            None,
        )
        if event is None or self.source_id != report.source_id:
            raise FactError("C13_ORDER_ATTRIBUTION_REQUIRED", "provider has no visible execution")
        if any(
            other.sequence != event.sequence
            and other.order_id == event.order_id
            and other.available_at <= at
            for other in self.fills
        ):
            raise FactError(
                "ORDER_SOURCE_CONFLICT", "multiple executions require per-order reconciliation"
            )
        observations = [
            o for o in self.orders if o.order_id == event.order_id and o.available_at <= at
        ]
        order = observations[-1] if observations else None
        if (
            order is None
            or order.revision != event.revision
            or report.occurred_at != event.occurred_at
            or event.occurred_at < order.submitted_at
            or (report.security_code, report.quantity, report.price, report.confirmed_fee)
            != (event.security_id, event.quantity, event.price, event.fee)
            or order.security_id != event.security_id
        ):
            raise FactError("ORDER_SOURCE_CONFLICT", "reported execution differs from provider")
        return order

    def terminal_order(self, attempt: str, at: datetime) -> LaterOrderResult:
        """Resolve the admitted later-attempt terminal fact bound to one persisted attempt."""
        _identity(attempt)
        _time(at)
        candidates = [
            o
            for o in self.orders
            if o.attempt_id == attempt and o.attempt_id != o.parent_id and o.available_at <= at
        ]
        if len({o.order_id for o in candidates}) != 1 or len(candidates) != 1:
            return LaterOrderResult(LaterOrderStatus.UNKNOWN, "C13_LATER_ORDER_AMBIGUOUS", None)
        order = candidates[0]
        if (
            order.status == "filled"
            or (order.filled or 0) > 0
            or any(f.order_id == order.order_id and f.available_at <= at for f in self.fills)
        ):
            return LaterOrderResult(LaterOrderStatus.FILLED, "LATER_FILL_RECORDED", order)
        if order.terminal_zero:
            return LaterOrderResult(LaterOrderStatus.ZERO, "CONFIRMED_TERMINAL_ZERO", order)
        return LaterOrderResult(LaterOrderStatus.UNKNOWN, "LATER_ORDER_UNRECONCILED", order)

    def scope_window(self, parent_id: str, security_id: str, end: datetime) -> OrderWindow:
        windows = {
            w
            for w in self.windows
            if w.parent_id == parent_id and w.security_id == security_id and w.end == end
        }
        if len(windows) != 1:
            raise FactError("C13_ORIGINAL_ORDER_FACT_REQUIRED", "missing or ambiguous exact window")
        return windows.pop()


def window_body(window: OrderWindow) -> dict[str, str]:
    return {
        "parent_id": window.parent_id,
        "security_id": window.security_id,
        "start": window.start.isoformat(),
        "end": window.end.isoformat(),
    }


def restore_window(body: Mapping[str, Any]) -> OrderWindow:
    return OrderWindow(
        body["parent_id"],
        body["security_id"],
        datetime.fromisoformat(body["start"]),
        datetime.fromisoformat(body["end"]),
    )


def evidence_body(
    source: SyntheticOrderSource, window: OrderWindow, at: datetime
) -> dict[str, Any]:
    result = source.window_result(window, at)
    order = result.order
    statement = next((s for s in source.statements if s.receipt_id == result.coverage_id), None)
    return {
        "schema": "p12-order-window-result-v1",
        "source_digest": source.identity,
        "source_id": source.source_id,
        "account_id": source.account_id,
        "mode": "synthetic",
        "window": window_body(window),
        "as_of": at.isoformat(),
        "status": result.status.value,
        "reason": result.reason,
        "coverage_receipt": result.coverage_id,
        "source_watermark": statement.watermark.isoformat() if statement else None,
        "available_at": statement.available_at.isoformat() if statement else None,
        "order_id": order.order_id if order else None,
        "order_revision": order.revision if order else None,
        "order_observation": order.observation_id if order else None,
        "order_version_status": (
            {
                "total": order.total,
                "filled": order.filled,
                "cancelled": order.cancelled,
                "status": order.status,
                "fee": format(order.fee, "f") if order.fee is not None else None,
                "effective_at": order.effective_at.isoformat(),
                "available_at": order.available_at.isoformat(),
                "supersedes": order.supersedes,
                "corrections_clear": order.corrections_clear,
            }
            if order
            else None
        ),
    }


def require_admission(
    source: SyntheticOrderSource,
    window: OrderWindow,
    decision_at: datetime,
    received_at: datetime,
    claimed_state: str,
) -> dict[str, Any]:
    decision = source.window_result(window, decision_at)
    current = source.window_result(window, received_at)
    if decision_at > received_at or any(
        r.status not in {WindowStatus.ZERO, WindowStatus.CLEAR} or r.status.value != claimed_state
        for r in (decision, current)
    ):
        reason = (
            "C13_ORIGINAL_FILL_PRESENT"
            if WindowStatus.FILLED in (decision.status, current.status)
            else "C13_ORIGINAL_ORDER_FACT_REQUIRED"
        )
        raise FactError(reason, f"{decision.reason} / {current.reason}")
    return {
        "window": window_body(window),
        "decision_at": decision_at.isoformat(),
        "received_at": received_at.isoformat(),
        "claimed_state": claimed_state,
        "decision": evidence_body(source, window, decision_at),
        "current": evidence_body(source, window, received_at),
    }

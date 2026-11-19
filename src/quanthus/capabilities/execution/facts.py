"""Trusted synthetic context and strict execution facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from quanthus.shared.fact_contracts import FactError, digest


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FactError("INVALID_REPORT_SHAPE", f"{name} is required")
    return value


def _aware(value: object, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise FactError("INVALID_REPORT_SHAPE", f"{name} must be aware")
    return value


def _money(value: object, name: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise FactError("INVALID_REPORT_SHAPE", f"{name} must be finite Decimal")
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -2:
        raise FactError("INVALID_REPORT_SHAPE", f"{name} supports at most 2 decimals")
    if value < 0 or (positive and value == 0):
        raise FactError("INVALID_REPORT_SHAPE", f"{name} is out of range")
    return value


def _quantity(value: object, name: str = "quantity") -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise FactError("INVALID_REPORT_SHAPE", f"{name} must be a positive integer")
    return value


@dataclass(frozen=True)
class SyntheticContext:
    account_id: str
    mode: str
    source_id: str
    as_of: datetime
    coverage_id: str

    def __post_init__(self) -> None:
        for name in ("account_id", "mode", "source_id", "coverage_id"):
            _text(getattr(self, name), name)
        _aware(self.as_of, "as_of")


def validate_context(context: SyntheticContext) -> SyntheticContext:
    if not isinstance(context, SyntheticContext):
        raise FactError("UNAUTHORIZED_CONTEXT", "typed trusted context required")
    context.__post_init__()
    if context.mode != "synthetic":
        raise FactError("UNAUTHORIZED_CONTEXT", "only trusted synthetic mode is admitted")
    if not context.account_id.startswith("SYN-"):
        raise FactError("UNAUTHORIZED_CONTEXT", "synthetic account must use SYN-")
    return context


@dataclass(frozen=True)
class ExecutionReport:
    report_id: str
    security_code: str
    side: str
    quantity: int
    price: Decimal
    confirmed_fee: Decimal
    occurred_at: datetime
    source_id: str
    source_sequence: str
    intended_quantity: int
    intended_price_cap: Decimal
    suggestion_id: str | None = None
    order_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("report_id", "security_code", "source_id", "source_sequence"):
            _text(getattr(self, name), name)
        if self.suggestion_id is not None:
            _text(self.suggestion_id, "suggestion_id")
        if self.order_id is not None:
            _text(self.order_id, "order_id")
        if self.side not in ("buy", "sell"):
            raise FactError("UNSUPPORTED_FACT", "side must be buy or sell")
        _quantity(self.quantity)
        _quantity(self.intended_quantity, "intended_quantity")
        _money(self.price, "price", positive=True)
        _money(self.confirmed_fee, "confirmed_fee")
        _money(self.intended_price_cap, "intended_price_cap", positive=True)
        _aware(self.occurred_at, "occurred_at")

    @property
    def payload_digest(self) -> str:
        return digest(self.to_dict())

    @property
    def source_fact_id(self) -> str:
        return digest([self.source_id, self.source_sequence])

    @property
    def deviations(self) -> tuple[str, ...]:
        result: list[str] = []
        if self.quantity != self.intended_quantity:
            result.append("QUANTITY_DEVIATION")
        if self.price > self.intended_price_cap:
            result.append("PRICE_DEVIATION")
        return tuple(result)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "report_id": self.report_id,
            "security_code": self.security_code,
            "side": self.side,
            "quantity": self.quantity,
            "price": format(self.price.quantize(Decimal("0.01")), "f"),
            "confirmed_fee": format(self.confirmed_fee.quantize(Decimal("0.01")), "f"),
            "occurred_at": self.occurred_at.isoformat(),
            "source_id": self.source_id,
            "source_sequence": self.source_sequence,
            "intended_quantity": self.intended_quantity,
            "intended_price_cap": format(self.intended_price_cap.quantize(Decimal("0.01")), "f"),
        }
        # Optional plan link (contract `suggestion_id?`); omitted when absent so earlier
        # synthetic receipts keep their original payload digest.
        if self.suggestion_id is not None:
            payload["suggestion_id"] = self.suggestion_id
        if self.order_id is not None:
            payload["order_id"] = self.order_id
        return payload


FINAL_FILL_GAP = "FINAL_FILL_GAP"


@dataclass(frozen=True)
class OrderObservation:
    """One observed state of an actual order (in progress, cancel requested or terminal).

    Terminal observations satisfy ``final_cumulative_fill_qty + cancelled_qty ==
    accepted_total_qty``; a missing final cumulative keeps the last provable hold.
    """

    observation_id: str
    order_id: str
    status: str  # "in_progress" | "cancel_requested" | "terminal"
    observed_at: datetime
    source_id: str
    source_sequence: str
    final_cumulative_fill_qty: int | None = None
    cancelled_qty: int | None = None
    order_revision: int = 1
    expected_order_state_revision: int | None = None

    def __post_init__(self) -> None:
        for name in ("observation_id", "order_id", "source_id", "source_sequence"):
            _text(getattr(self, name), name)
        if self.status not in ("in_progress", "cancel_requested", "terminal"):
            raise FactError("INVALID_REPORT_SHAPE", f"unknown order status {self.status}")
        _aware(self.observed_at, "observed_at")
        if not isinstance(self.order_revision, int) or self.order_revision <= 0:
            raise FactError("INVALID_REPORT_SHAPE", "order_revision must be positive")
        if (
            self.expected_order_state_revision is not None
            and (
                not isinstance(self.expected_order_state_revision, int)
                or self.expected_order_state_revision <= 0
            )
        ):
            raise FactError("INVALID_REPORT_SHAPE", "expected_order_state_revision is invalid")
        for name in ("final_cumulative_fill_qty", "cancelled_qty"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0
            ):
                raise FactError("INVALID_REPORT_SHAPE", f"{name} is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "observation_id": self.observation_id,
            "order_id": self.order_id,
            "status": self.status,
            "observed_at": self.observed_at.isoformat(),
            "source_id": self.source_id,
            "source_sequence": self.source_sequence,
            "final_cumulative_fill_qty": self.final_cumulative_fill_qty,
            "cancelled_qty": self.cancelled_qty,
            "order_revision": self.order_revision,
            "expected_order_state_revision": self.expected_order_state_revision,
        }


@dataclass(frozen=True)
class FeeAssessment:
    """A confirmed fee covering one whole order (scope ``order`` in this slice)."""

    fee_id: str
    order_id: str
    amount: Decimal
    source_id: str
    source_sequence: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        for name in ("fee_id", "order_id", "source_id", "source_sequence"):
            _text(getattr(self, name), name)
        _money(self.amount, "amount")
        _aware(self.occurred_at, "occurred_at")

    def to_dict(self) -> dict[str, object]:
        return {
            "fee_id": self.fee_id,
            "order_id": self.order_id,
            "amount": format(self.amount.quantize(Decimal("0.01")), "f"),
            "source_id": self.source_id,
            "source_sequence": self.source_sequence,
            "occurred_at": self.occurred_at.isoformat(),
        }


def validate_observation(observation: OrderObservation) -> OrderObservation:
    observation.__post_init__()
    return observation


def validate_fee_assessment(fee: FeeAssessment) -> FeeAssessment:
    fee.__post_init__()
    return fee


def validate_report(report: ExecutionReport) -> ExecutionReport:
    report.__post_init__()
    return report


BASELINE_COVERAGE_UNKNOWN = "BASELINE_COVERAGE_UNKNOWN"


def baseline_coverage_gap(report: ExecutionReport, baseline_as_of: datetime) -> str | None:
    """A fact that happened before the takeover snapshot may already be inside the baseline.

    Without per-fact coverage evidence it must not be posted; it stays received and pending
    reconciliation (engineering contract v1.1 section 6).  Facts at or after the snapshot
    instant post normally, including over-cap or out-of-window fills.
    """

    return coverage_gap_at(report.occurred_at, baseline_as_of)


def coverage_gap_at(occurred_at: datetime, baseline_as_of: datetime) -> str | None:
    """Same rule for a stored fact: earlier than the takeover snapshot means coverage unknown."""

    _aware(occurred_at, "occurred_at")
    _aware(baseline_as_of, "baseline_as_of")
    return BASELINE_COVERAGE_UNKNOWN if occurred_at < baseline_as_of else None


@dataclass(frozen=True)
class Correction:
    correction_id: str
    fact_id: str
    expected_fact_revision: int
    quantity: int
    price: Decimal
    reason: str

    def __post_init__(self) -> None:
        for name in ("correction_id", "fact_id", "reason"):
            _text(getattr(self, name), name)
        if (
            not isinstance(self.expected_fact_revision, int)
            or isinstance(self.expected_fact_revision, bool)
            or self.expected_fact_revision <= 0
        ):
            raise FactError("INVALID_REPORT_SHAPE", "bad expected_fact_revision")
        _quantity(self.quantity)
        _money(self.price, "price", positive=True)

    @property
    def payload_digest(self) -> str:
        return digest(
            {
                "correction_id": self.correction_id,
                "fact_id": self.fact_id,
                "expected_fact_revision": self.expected_fact_revision,
                "quantity": self.quantity,
                "price": format(self.price.quantize(Decimal("0.01")), "f"),
                "reason": self.reason,
            }
        )


def validate_correction(correction: Correction) -> Correction:
    correction.__post_init__()
    return correction


# ---- order aggregation -----------------------------------------------------------


@dataclass(frozen=True)
class OrderState:
    """Effective state of one actual order derived from its observations and fills."""

    order_id: str
    status: str  # "open" | "cancel_pending" | "terminal" | "needs_reconciliation"
    order_state_revision: int
    accepted_total_qty: int
    known_fill_qty: int
    unreported_fill_qty: int
    hold_qty: int
    hold_amount: Decimal
    needs_reconciliation: bool
    reason_codes: tuple[str, ...]


def effective_order_state(
    *,
    order_id: str,
    side: str,
    accepted_total_qty: int,
    price_bound: Decimal,
    known_fill_qty: int,
    latest_observation: OrderObservation | None,
    confirmed_fees: tuple[FeeAssessment, ...],
    estimated_fee_upper: Decimal,
) -> OrderState:
    """Fold one order's fills and observations into its current effective state.

    ``known_fill_qty`` is the baseline-covered plus posted fill quantity.  A terminal
    observation releases only the confirmed ``cancelled_qty``; the gap between the
    final cumulative and the known quantity stays occupied as unreported fills.  A
    missing final cumulative keeps the last provable hold.
    """

    _quantity(accepted_total_qty, "accepted_total_qty")
    _money(price_bound, "price_bound", positive=True)
    _money(estimated_fee_upper, "estimated_fee_upper")
    if known_fill_qty < 0 or known_fill_qty > accepted_total_qty:
        raise FactError("INVALID_REPORT_SHAPE", "known_fill_qty out of range")

    reasons: list[str] = []
    needs_reconciliation = False
    status = "open"
    revision = 0
    final_qty: int | None = None
    cancelled: int | None = None
    if latest_observation is not None:
        revision = latest_observation.order_revision
        if latest_observation.status == "terminal":
            final_qty = latest_observation.final_cumulative_fill_qty
            cancelled = latest_observation.cancelled_qty
            if (
                final_qty is None
                or cancelled is None
                or final_qty + cancelled != accepted_total_qty
                or final_qty < known_fill_qty
            ):
                status = "needs_reconciliation"
                needs_reconciliation = True
                reasons.append(FINAL_FILL_GAP)
            else:
                status = "terminal"
        elif latest_observation.status == "cancel_requested":
            status = "cancel_pending"
        else:
            status = "open"
        if (
            latest_observation.expected_order_state_revision is not None
            and latest_observation.expected_order_state_revision != revision - 1
        ):
            needs_reconciliation = True
            reasons.append("ORDER_STATE_GAP")

    unreported = 0
    hold_qty = accepted_total_qty - known_fill_qty
    if status == "terminal":
        unreported = final_qty - known_fill_qty  # type: ignore[operator]
        hold_qty = unreported
    elif status == "needs_reconciliation":
        unreported = accepted_total_qty - known_fill_qty
        hold_qty = accepted_total_qty - known_fill_qty
    if side == "sell":
        hold_amount = Decimal("0.00")
    else:
        principal = Decimal(hold_qty) * price_bound
        fees = sum((f.amount for f in confirmed_fees), Decimal("0"))
        outstanding_fee = max(Decimal("0"), estimated_fee_upper - fees)
        hold_amount = principal + outstanding_fee
    if unreported > 0 or status == "needs_reconciliation":
        reasons.append(FINAL_FILL_GAP)
    if unreported > 0:
        needs_reconciliation = True
    return OrderState(
        order_id=order_id,
        status=status,
        order_state_revision=revision,
        accepted_total_qty=accepted_total_qty,
        known_fill_qty=known_fill_qty,
        unreported_fill_qty=unreported,
        hold_qty=hold_qty,
        hold_amount=hold_amount.quantize(Decimal("0.01")),
        needs_reconciliation=needs_reconciliation or status == "needs_reconciliation",
        reason_codes=tuple(dict.fromkeys(reasons)),
    )

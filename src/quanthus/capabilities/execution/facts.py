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

    def __post_init__(self) -> None:
        for name in ("report_id", "security_code", "source_id", "source_sequence"):
            _text(getattr(self, name), name)
        if self.side != "buy":
            raise FactError("UNSUPPORTED_FACT", "slice supports confirmed buys")
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
        return {
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


def validate_report(report: ExecutionReport) -> ExecutionReport:
    report.__post_init__()
    return report


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

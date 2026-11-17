"""Pure account deltas and balanced confirmed-buy postings."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from quanthus.shared.fact_contracts import FactError, digest


@dataclass(frozen=True)
class Position:
    security_code: str
    quantity: int

    def __post_init__(self) -> None:
        if (
            not self.security_code
            or not isinstance(self.quantity, int)
            or isinstance(self.quantity, bool)
        ):
            raise FactError("INVALID_REPORT_SHAPE", "invalid position")
        if self.quantity < 0:
            raise FactError("NEGATIVE_POSITION", "position cannot be negative")

    def to_dict(self) -> dict[str, object]:
        return {"security_code": self.security_code, "quantity": self.quantity}


@dataclass(frozen=True)
class Posting:
    account: str
    amount: Decimal

    def __post_init__(self) -> None:
        if not self.account or not self.amount.is_finite():
            raise FactError("INVALID_REPORT_SHAPE", "invalid posting")

    def to_dict(self) -> dict[str, str]:
        return {"account": self.account, "amount": format(self.amount, "f")}


@dataclass(frozen=True)
class AccountProjection:
    revision: int
    event_sequence: int
    cash: Decimal
    positions: tuple[Position, ...]
    fact_count: int

    @property
    def digest(self) -> str:
        return digest(self.to_dict())

    def quantity(self, security_code: str) -> int:
        return next(
            (item.quantity for item in self.positions if item.security_code == security_code), 0
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "revision": self.revision,
            "event_sequence": self.event_sequence,
            "cash": format(self.cash, "f"),
            "positions": [item.to_dict() for item in self.positions],
            "fact_count": self.fact_count,
        }


def apply_buy_postings(principal: Decimal, fee: Decimal) -> tuple[Posting, ...]:
    postings = (
        Posting("ASSET_CASH", -(principal + fee)),
        Posting("ASSET_POSITION", principal),
        Posting("EXPENSE_CONFIRMED_FEE", fee),
    )
    if sum((item.amount for item in postings), Decimal("0")) != 0:
        raise FactError("UNBALANCED_POSTING", "buy postings must sum to zero")
    return postings


def apply_buy_delta(
    projection: AccountProjection,
    *,
    security_code: str,
    quantity: int,
    price: Decimal,
    fee: Decimal,
) -> tuple[AccountProjection, tuple[Posting, ...]]:
    principal = (price * quantity).quantize(Decimal("0.01"))
    cash = (projection.cash - principal - fee).quantize(Decimal("0.01"))
    if cash < 0:
        raise FactError("INSUFFICIENT_CASH", "confirmed buy exceeds cash")
    quantities = {item.security_code: item.quantity for item in projection.positions}
    quantities[security_code] = quantities.get(security_code, 0) + quantity
    positions = tuple(Position(code, qty) for code, qty in sorted(quantities.items()) if qty)
    return (
        AccountProjection(
            projection.revision + 1,
            projection.event_sequence + 1,
            cash,
            positions,
            projection.fact_count + 1,
        ),
        apply_buy_postings(principal, fee),
    )


def apply_correction_delta(
    projection: AccountProjection,
    *,
    security_code: str,
    quantity_delta: int,
    cash_delta: Decimal,
    principal_delta: Decimal,
) -> tuple[AccountProjection, tuple[Posting, ...]]:
    quantities = {item.security_code: item.quantity for item in projection.positions}
    quantities[security_code] = quantities.get(security_code, 0) + quantity_delta
    if any(value < 0 for value in quantities.values()):
        raise FactError("NEGATIVE_POSITION", "correction cannot create negative position")
    cash = (projection.cash + cash_delta).quantize(Decimal("0.01"))
    if cash < 0:
        raise FactError("INSUFFICIENT_CASH", "correction requires cash reconciliation")
    positions = tuple(Position(code, qty) for code, qty in sorted(quantities.items()) if qty)
    postings = (
        Posting("ASSET_CASH", cash_delta),
        Posting("ASSET_POSITION", principal_delta),
    )
    if sum((item.amount for item in postings), Decimal("0")) != 0:
        raise FactError("UNBALANCED_POSTING", "correction postings must sum to zero")
    return (
        AccountProjection(
            projection.revision + 1,
            projection.event_sequence + 1,
            cash,
            positions,
            projection.fact_count,
        ),
        postings,
    )

"""Risk capability public entry.

Quantity solving and account exposure checks are owned by the authoritative
``quanthus.product_daily.risk.evaluate_quantity`` implementation.  This module binds the
versioned P12 account-risk policy to it and returns every triggered constraint plus a
primary reason in the frozen priority order.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from quanthus.product_daily.contracts import ReasonCode
from quanthus.product_daily.risk import (
    BuyFeeRule,
    MarkedExposure,
    QuantityLimits,
    QuantityRequest,
    RiskSnapshot,
    evaluate_quantity,
)
from quanthus.shared.fact_contracts import FactError
from quanthus.shared.values import MaybeDecimal, VersionRef

INSUFFICIENT_CAPACITY = "INSUFFICIENT_CAPACITY"


def _ratio(value: object, name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or not 0 <= value <= 1:
        raise FactError("INVALID_POLICY", f"{name} must be a Decimal in [0, 1]")
    return value


@dataclass(frozen=True)
class AccountRiskPolicy:
    policy_ref: VersionRef
    stock_limit: Decimal
    industry_limit: Decimal
    total_limit: Decimal
    lot_size: int
    commission_rate: Decimal
    minimum_commission: Decimal

    def __post_init__(self) -> None:
        for name in ("stock_limit", "industry_limit", "total_limit"):
            _ratio(getattr(self, name), name)
        if not isinstance(self.lot_size, int) or isinstance(self.lot_size, bool):
            raise FactError("INVALID_POLICY", "lot_size must be an integer")
        if self.lot_size <= 0:
            raise FactError("INVALID_POLICY", "lot_size must be positive")
        _ratio(self.commission_rate, "commission_rate")
        if not isinstance(self.minimum_commission, Decimal) or self.minimum_commission < 0:
            raise FactError("INVALID_POLICY", "minimum_commission must be non-negative")


@dataclass(frozen=True)
class HeldExposure:
    security_code: str
    industry_id: str | None
    quantity: int
    mark_price: Decimal | None


@dataclass(frozen=True)
class EntrySizingInput:
    decision_snapshot_id: str
    as_of: datetime
    opportunity_id: str
    security_code: str
    industry_id: str | None
    mark_price: Decimal | None
    price_bound: Decimal | None
    bucket_budget: Decimal
    fraction: Decimal | None
    equity: MaybeDecimal
    cash_available: MaybeDecimal
    held: tuple[HeldExposure, ...] = ()


@dataclass(frozen=True)
class EntrySizing:
    quantity: int
    gross: Decimal | None
    fees: Decimal | None
    total_cost: Decimal | None
    target_cap: Decimal | None
    reason_codes: tuple[str, ...]
    primary_reason: str | None
    decision_digest: str

    @property
    def feasible(self) -> bool:
        return self.quantity > 0


def size_entry(policy: AccountRiskPolicy, request: EntrySizingInput) -> EntrySizing:
    """Largest legal whole-lot buy under budget, cash and stock/industry/account caps."""

    snapshot = RiskSnapshot(
        snapshot_id=request.decision_snapshot_id,
        as_of=request.as_of,
        equity=request.equity.value,
        cash_available=request.cash_available.value,
        positions=tuple(
            MarkedExposure(
                unit_id=f"held:{item.security_code}",
                opportunity_id=f"held:{item.security_code}",
                security_code=item.security_code,
                level1_industry_id=item.industry_id,
                quantity=item.quantity,
                mark_price=item.mark_price,
                price_bound=item.mark_price,
            )
            for item in request.held
            if item.quantity > 0
        ),
    )
    decision = evaluate_quantity(
        QuantityRequest(
            decision_snapshot_id=request.decision_snapshot_id,
            bucket_budget=request.bucket_budget,
            fraction=request.fraction,
            security_code=request.security_code,
            level1_industry_id=request.industry_id,
            mark_price=request.mark_price,
            price_bound=request.price_bound,
            lot_size=policy.lot_size,
            limits=QuantityLimits(policy.stock_limit, policy.industry_limit, policy.total_limit),
            fee_rule=BuyFeeRule(policy.commission_rate, policy.minimum_commission),
        ),
        snapshot,
    )
    reasons = tuple(item.value for item in decision.reason_codes)
    binding = tuple(item for item in reasons if item != ReasonCode.CAPACITY_AVAILABLE.value)
    if decision.quantity <= 0:
        primary: str | None = binding[0] if binding else INSUFFICIENT_CAPACITY
        if (
            INSUFFICIENT_CAPACITY not in reasons
            and ReasonCode.INPUT_UNAVAILABLE.value not in reasons
        ):
            reasons = (INSUFFICIENT_CAPACITY, *reasons)
    else:
        primary = binding[0] if binding else None
    return EntrySizing(
        quantity=decision.quantity,
        gross=decision.gross,
        fees=decision.fees,
        total_cost=decision.total_cost,
        target_cap=decision.target_cap,
        reason_codes=reasons,
        primary_reason=primary,
        decision_digest=decision.digest,
    )


__all__ = [
    "INSUFFICIENT_CAPACITY",
    "AccountRiskPolicy",
    "EntrySizing",
    "EntrySizingInput",
    "HeldExposure",
    "size_entry",
]

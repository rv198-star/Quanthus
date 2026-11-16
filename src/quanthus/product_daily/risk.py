"""Conservative whole-lot sizing and the shared C13/C15 entry decision."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from quanthus.product_daily.candidate import CandidateProjection
from quanthus.product_daily.contracts import (
    CommandEnvelope,
    ProductDailyError,
    ReasonCode,
    decimal_text,
    digest,
    ordered_reasons,
    quantize,
    require_aware,
    require_decimal,
    require_text,
)
from quanthus.product_daily.funding import BucketPhase, FundingState, prepare_cycle


@dataclass(frozen=True)
class BuyFeeRule:
    commission_rate: Decimal | None
    minimum_commission: Decimal | None
    cash_quantum: Decimal = Decimal("0.01")

    def __post_init__(self) -> None:
        if self.commission_rate is not None:
            require_decimal(
                self.commission_rate,
                "fee.commission_rate",
                minimum=Decimal("0"),
                maximum=Decimal("0.9999999999"),
            )
        if self.minimum_commission is not None:
            require_decimal(
                self.minimum_commission,
                "fee.minimum_commission",
                minimum=Decimal("0"),
            )
        require_decimal(self.cash_quantum, "fee.cash_quantum", minimum=Decimal("0.0000000001"))

    @classmethod
    def from_buy_cost(
        cls,
        *,
        commission_rate: Decimal,
        minimum_commission: Decimal,
        cash_quantum: Decimal,
    ) -> BuyFeeRule:
        return cls(
            commission_rate=commission_rate,
            minimum_commission=minimum_commission,
            cash_quantum=cash_quantum,
        )


@dataclass(frozen=True)
class MarkedExposure:
    unit_id: str
    opportunity_id: str
    security_code: str
    level1_industry_id: str | None
    quantity: int
    mark_price: Decimal | None
    price_bound: Decimal | None

    def __post_init__(self) -> None:
        for field_name in ("unit_id", "opportunity_id", "security_code"):
            require_text(getattr(self, field_name), field_name)
        if self.level1_industry_id is not None:
            require_text(self.level1_industry_id, "level1_industry_id")
        if (
            not isinstance(self.quantity, int)
            or isinstance(self.quantity, bool)
            or self.quantity <= 0
        ):
            raise ProductDailyError("invalid_exposure", "quantity must be a positive integer")
        for value, field_name in (
            (self.mark_price, "mark_price"),
            (self.price_bound, "price_bound"),
        ):
            if value is not None:
                require_decimal(value, field_name, minimum=Decimal("0.0000000001"))

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.unit_id, self.opportunity_id, self.security_code)

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "opportunity_id": self.opportunity_id,
            "security_code": self.security_code,
            "level1_industry_id": self.level1_industry_id,
            "quantity": self.quantity,
            "mark_price": decimal_text(self.mark_price),
            "price_bound": decimal_text(self.price_bound),
        }


@dataclass(frozen=True)
class PendingBuy:
    reservation_id: str
    security_code: str
    level1_industry_id: str | None
    remaining_quantity: int
    mark_price: Decimal | None
    price_bound: Decimal | None
    unrecognized_fee_upper_bound: Decimal | None
    fee_already_recognized: bool = False

    def __post_init__(self) -> None:
        require_text(self.reservation_id, "reservation_id")
        require_text(self.security_code, "security_code")
        if self.level1_industry_id is not None:
            require_text(self.level1_industry_id, "level1_industry_id")
        if (
            not isinstance(self.remaining_quantity, int)
            or isinstance(self.remaining_quantity, bool)
            or self.remaining_quantity <= 0
        ):
            raise ProductDailyError(
                "invalid_pending_buy", "remaining_quantity must be a positive integer"
            )
        for value, field_name in (
            (self.mark_price, "mark_price"),
            (self.price_bound, "price_bound"),
            (self.unrecognized_fee_upper_bound, "unrecognized_fee_upper_bound"),
        ):
            if value is not None:
                require_decimal(value, field_name, minimum=Decimal("0"))
        if self.fee_already_recognized and self.unrecognized_fee_upper_bound not in {
            None,
            Decimal("0"),
            Decimal("0.00"),
        }:
            raise ProductDailyError(
                "invalid_pending_buy",
                "recognized fees cannot also carry an unrecognized upper bound",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "reservation_id": self.reservation_id,
            "security_code": self.security_code,
            "level1_industry_id": self.level1_industry_id,
            "remaining_quantity": self.remaining_quantity,
            "mark_price": decimal_text(self.mark_price),
            "price_bound": decimal_text(self.price_bound),
            "unrecognized_fee_upper_bound": decimal_text(self.unrecognized_fee_upper_bound),
            "fee_already_recognized": self.fee_already_recognized,
        }


@dataclass(frozen=True)
class PendingSell:
    sell_id: str
    security_code: str
    level1_industry_id: str | None
    quantity: int
    mark_price: Decimal | None

    def __post_init__(self) -> None:
        require_text(self.sell_id, "sell_id")
        require_text(self.security_code, "security_code")
        if self.level1_industry_id is not None:
            require_text(self.level1_industry_id, "level1_industry_id")
        if (
            not isinstance(self.quantity, int)
            or isinstance(self.quantity, bool)
            or self.quantity <= 0
        ):
            raise ProductDailyError("invalid_pending_sell", "quantity must be positive")
        if self.mark_price is not None:
            require_decimal(self.mark_price, "mark_price", minimum=Decimal("0.0000000001"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "sell_id": self.sell_id,
            "security_code": self.security_code,
            "level1_industry_id": self.level1_industry_id,
            "quantity": self.quantity,
            "mark_price": decimal_text(self.mark_price),
        }


@dataclass(frozen=True)
class RiskSnapshot:
    snapshot_id: str
    as_of: datetime
    equity: Decimal | None
    cash_available: Decimal | None
    positions: tuple[MarkedExposure, ...] = ()
    pending_buys: tuple[PendingBuy, ...] = ()
    pending_sells: tuple[PendingSell, ...] = ()
    recognized_payables: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        require_text(self.snapshot_id, "snapshot_id")
        require_aware(self.as_of, "as_of")
        if self.equity is not None:
            require_decimal(self.equity, "equity")
        if self.cash_available is not None:
            require_decimal(self.cash_available, "cash_available", minimum=Decimal("0"))
        require_decimal(self.recognized_payables, "recognized_payables", minimum=Decimal("0"))
        position_ids = [item.identity for item in self.positions]
        if len(position_ids) != len(set(position_ids)):
            raise ProductDailyError("duplicate_risk_evidence", "position identities must be unique")
        pending_ids = [item.reservation_id for item in self.pending_buys]
        if len(pending_ids) != len(set(pending_ids)):
            raise ProductDailyError("duplicate_risk_evidence", "pending buy ids must be unique")
        sell_ids = [item.sell_id for item in self.pending_sells]
        if len(sell_ids) != len(set(sell_ids)):
            raise ProductDailyError("duplicate_risk_evidence", "pending sell ids must be unique")

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "as_of": self.as_of.isoformat(),
            "equity": decimal_text(self.equity),
            "cash_available": decimal_text(self.cash_available),
            "positions": [item.to_dict() for item in self.positions],
            "pending_buys": [item.to_dict() for item in self.pending_buys],
            "pending_sells": [item.to_dict() for item in self.pending_sells],
            "recognized_payables": decimal_text(self.recognized_payables),
        }


@dataclass(frozen=True)
class QuantityLimits:
    stock: Decimal | None
    industry: Decimal | None
    total: Decimal | None

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.stock, "stock_limit"),
            (self.industry, "industry_limit"),
            (self.total, "total_limit"),
        ):
            if value is not None:
                require_decimal(value, field_name, minimum=Decimal("0"), maximum=Decimal("1"))


@dataclass(frozen=True)
class QuantityRequest:
    decision_snapshot_id: str
    bucket_budget: Decimal
    fraction: Decimal | None
    security_code: str
    level1_industry_id: str | None
    mark_price: Decimal | None
    price_bound: Decimal | None
    lot_size: int | None
    limits: QuantityLimits
    fee_rule: BuyFeeRule

    def __post_init__(self) -> None:
        require_text(self.decision_snapshot_id, "decision_snapshot_id")
        require_decimal(self.bucket_budget, "bucket_budget", minimum=Decimal("0"))
        if self.fraction is not None:
            require_decimal(
                self.fraction,
                "fraction",
                minimum=Decimal("0"),
                maximum=Decimal("1"),
            )
        require_text(self.security_code, "security_code")
        if self.level1_industry_id is not None:
            require_text(self.level1_industry_id, "level1_industry_id")
        for value, field_name in (
            (self.mark_price, "mark_price"),
            (self.price_bound, "price_bound"),
        ):
            if value is not None:
                require_decimal(value, field_name, minimum=Decimal("0.0000000001"))
        if self.lot_size is not None and (
            not isinstance(self.lot_size, int)
            or isinstance(self.lot_size, bool)
            or self.lot_size <= 0
        ):
            raise ProductDailyError("invalid_lot", "lot_size must be a positive integer")


@dataclass(frozen=True)
class QuantityDecision:
    quantity: int
    gross: Decimal | None
    fees: Decimal | None
    total_cost: Decimal | None
    target_cap: Decimal | None
    conservative_post_fill_equity: Decimal | None
    existing_security_exposure: Decimal | None
    existing_industry_exposure: Decimal | None
    existing_total_exposure: Decimal | None
    post_security_exposure: Decimal | None
    post_industry_exposure: Decimal | None
    post_total_exposure: Decimal | None
    next_lot_cost: Decimal | None
    next_lot_post_equity: Decimal | None
    next_lot_cap: Decimal | None
    reason_codes: tuple[ReasonCode, ...]
    pending_sell_releases: Decimal = Decimal("0")

    @property
    def allowed(self) -> bool:
        return self.quantity > 0

    @property
    def execution_authorized(self) -> bool:
        return False

    @property
    def forced_sale(self) -> bool:
        return False

    @property
    def digest(self) -> str:
        return digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "q": self.quantity,
            "gross": decimal_text(self.gross),
            "fees": decimal_text(self.fees),
            "total_cost": decimal_text(self.total_cost),
            "target_cap": decimal_text(self.target_cap),
            "conservative_post_fill_equity": decimal_text(self.conservative_post_fill_equity),
            "existing_security_exposure": decimal_text(self.existing_security_exposure),
            "existing_industry_exposure": decimal_text(self.existing_industry_exposure),
            "existing_total_exposure": decimal_text(self.existing_total_exposure),
            "post_security_exposure": decimal_text(self.post_security_exposure),
            "post_industry_exposure": decimal_text(self.post_industry_exposure),
            "post_total_exposure": decimal_text(self.post_total_exposure),
            "next_lot_cost": decimal_text(self.next_lot_cost),
            "next_lot_post_equity": decimal_text(self.next_lot_post_equity),
            "next_lot_cap": decimal_text(self.next_lot_cap),
            "reason_codes": [item.value for item in self.reason_codes],
            "pending_sell_releases": decimal_text(self.pending_sell_releases),
            "execution_authorized": self.execution_authorized,
            "forced_sale": self.forced_sale,
        }


def _fee(gross: Decimal, rule: BuyFeeRule) -> Decimal:
    if rule.commission_rate is None or rule.minimum_commission is None:
        raise ProductDailyError("input_unavailable", "buy fee rule is incomplete")
    return quantize(
        max(gross * rule.commission_rate, rule.minimum_commission),
        rule.cash_quantum,
    )


def _marked_unit_value(mark: Decimal | None, bound: Decimal | None) -> Decimal:
    if mark is None:
        raise ProductDailyError("input_unavailable", "mark price is unavailable")
    if bound is None:
        return mark
    return max(mark, bound)


def _other_pending_evidence(
    snapshot: RiskSnapshot,
) -> tuple[Decimal, Decimal]:
    adverse = Decimal("0")
    fees = Decimal("0")
    for item in snapshot.pending_buys:
        if item.mark_price is None or item.price_bound is None:
            raise ProductDailyError("input_unavailable", "pending buy lacks a finite price bound")
        adverse += Decimal(item.remaining_quantity) * max(
            item.price_bound - item.mark_price,
            Decimal("0"),
        )
        if not item.fee_already_recognized:
            if item.unrecognized_fee_upper_bound is None:
                raise ProductDailyError("input_unavailable", "pending buy fee is unavailable")
            fees += item.unrecognized_fee_upper_bound
    return adverse, fees


def evaluate_quantity(
    request: QuantityRequest,
    snapshot: RiskSnapshot,
) -> QuantityDecision:
    """Find the largest whole-lot quantity under conservative post-fill equity."""

    reasons: list[ReasonCode] = []
    if snapshot.snapshot_id != request.decision_snapshot_id:
        reasons.append(ReasonCode.INPUT_UNAVAILABLE)
    if (
        snapshot.equity is None
        or snapshot.cash_available is None
        or request.fraction is None
        or request.mark_price is None
        or request.price_bound is None
        or request.lot_size is None
        or request.level1_industry_id is None
        or request.fee_rule.commission_rate is None
        or request.fee_rule.minimum_commission is None
        or request.limits.stock is None
        or request.limits.industry is None
        or request.limits.total is None
    ):
        reasons.append(ReasonCode.INPUT_UNAVAILABLE)
    try:
        other_adverse, other_fees = _other_pending_evidence(snapshot)
        if any(item.level1_industry_id is None for item in snapshot.positions) or any(
            item.level1_industry_id is None for item in snapshot.pending_buys
        ):
            raise ProductDailyError(
                "input_unavailable",
                "required industry membership is unavailable for an existing exposure",
            )
        existing_security = sum(
            (
                Decimal(item.quantity) * _marked_unit_value(item.mark_price, item.price_bound)
                for item in snapshot.positions
                if item.security_code == request.security_code
            ),
            Decimal("0"),
        ) + sum(
            (
                Decimal(item.remaining_quantity)
                * _marked_unit_value(item.mark_price, item.price_bound)
                for item in snapshot.pending_buys
                if item.security_code == request.security_code
            ),
            Decimal("0"),
        )
        if request.level1_industry_id is None:
            raise ProductDailyError("input_unavailable", "candidate industry is unavailable")
        existing_industry = sum(
            (
                Decimal(item.quantity) * _marked_unit_value(item.mark_price, item.price_bound)
                for item in snapshot.positions
                if item.level1_industry_id == request.level1_industry_id
            ),
            Decimal("0"),
        ) + sum(
            (
                Decimal(item.remaining_quantity)
                * _marked_unit_value(item.mark_price, item.price_bound)
                for item in snapshot.pending_buys
                if item.level1_industry_id == request.level1_industry_id
            ),
            Decimal("0"),
        )
        existing_total = sum(
            (
                Decimal(item.quantity) * _marked_unit_value(item.mark_price, item.price_bound)
                for item in snapshot.positions
            ),
            Decimal("0"),
        ) + sum(
            (
                Decimal(item.remaining_quantity)
                * _marked_unit_value(item.mark_price, item.price_bound)
                for item in snapshot.pending_buys
            ),
            Decimal("0"),
        )
    except ProductDailyError:
        reasons.append(ReasonCode.INPUT_UNAVAILABLE)
        ordered = ordered_reasons(reasons)
        return QuantityDecision(
            quantity=0,
            gross=None,
            fees=None,
            total_cost=None,
            target_cap=None,
            conservative_post_fill_equity=None,
            existing_security_exposure=None,
            existing_industry_exposure=None,
            existing_total_exposure=None,
            post_security_exposure=None,
            post_industry_exposure=None,
            post_total_exposure=None,
            next_lot_cost=None,
            next_lot_post_equity=None,
            next_lot_cap=None,
            reason_codes=ordered,
        )

    if reasons:
        ordered = ordered_reasons(reasons)
        return QuantityDecision(
            quantity=0,
            gross=None,
            fees=None,
            total_cost=None,
            target_cap=None,
            conservative_post_fill_equity=None,
            existing_security_exposure=None,
            existing_industry_exposure=None,
            existing_total_exposure=None,
            post_security_exposure=None,
            post_industry_exposure=None,
            post_total_exposure=None,
            next_lot_cost=None,
            next_lot_post_equity=None,
            next_lot_cap=None,
            reason_codes=ordered,
        )

    assert snapshot.equity is not None
    assert snapshot.cash_available is not None
    assert request.fraction is not None
    assert request.mark_price is not None
    assert request.price_bound is not None
    assert request.lot_size is not None
    assert request.limits.stock is not None
    assert request.limits.industry is not None
    assert request.limits.total is not None

    equity = snapshot.equity
    cash_available = snapshot.cash_available
    mark_price = request.mark_price
    price_bound = request.price_bound
    lot_size = request.lot_size
    stock_limit = request.limits.stock
    industry_limit = request.limits.industry
    total_limit = request.limits.total
    target_cap = request.fraction * request.bucket_budget
    exposure_price = max(mark_price, price_bound)
    adverse_per_share = max(price_bound - mark_price, Decimal("0"))

    def metrics(
        quantity: int,
    ) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal, Decimal, Decimal]:
        gross = Decimal(quantity) * price_bound
        fees = _fee(gross, request.fee_rule)
        total_cost = gross + fees
        post_equity = equity - other_adverse - other_fees
        post_equity -= Decimal(quantity) * adverse_per_share
        post_equity -= fees
        security_exposure = existing_security + Decimal(quantity) * exposure_price
        industry_exposure = existing_industry + Decimal(quantity) * exposure_price
        total_exposure = existing_total + Decimal(quantity) * exposure_price
        return (
            gross,
            fees,
            total_cost,
            post_equity,
            security_exposure,
            industry_exposure,
            total_exposure,
        )

    def failures_for(quantity: int) -> tuple[ReasonCode, ...]:
        (
            gross,
            fees,
            total_cost,
            post_equity,
            security_exposure,
            industry_exposure,
            total_exposure,
        ) = metrics(quantity)
        del gross, fees
        failed: list[ReasonCode] = []
        if total_cost > target_cap:
            failed.append(ReasonCode.BUDGET_CAPACITY)
        if total_cost > cash_available:
            failed.append(ReasonCode.CASH_CAPACITY)
        if security_exposure > stock_limit * post_equity:
            failed.append(ReasonCode.STOCK_CAPACITY)
        if industry_exposure > industry_limit * post_equity:
            failed.append(ReasonCode.INDUSTRY_CAPACITY)
        if total_exposure > total_limit * post_equity:
            failed.append(ReasonCode.TOTAL_CAPACITY)
        return ordered_reasons(failed)

    first_lot = lot_size
    first_failures = failures_for(first_lot)
    upper_cash = min(cash_available, max(target_cap, Decimal("0")))
    upper_lots = int(upper_cash // (price_bound * Decimal(lot_size))) + 1
    lo = 0
    hi_lots = upper_lots
    while lo < hi_lots:
        mid = (lo + hi_lots + 1) // 2
        if not failures_for(mid * lot_size):
            lo = mid
        else:
            hi_lots = mid - 1
    quantity = lo * lot_size
    gross, fees, total_cost, post_equity, security_exposure, industry_exposure, total_exposure = (
        metrics(quantity)
    )
    next_quantity = quantity + lot_size
    next_gross, _, next_cost, next_equity, _, _, _ = metrics(next_quantity)
    del next_gross
    next_failures = failures_for(next_quantity)
    if quantity <= 0:
        return QuantityDecision(
            quantity=0,
            gross=Decimal("0"),
            fees=Decimal("0"),
            total_cost=Decimal("0"),
            target_cap=target_cap,
            conservative_post_fill_equity=equity - other_adverse - other_fees,
            existing_security_exposure=existing_security,
            existing_industry_exposure=existing_industry,
            existing_total_exposure=existing_total,
            post_security_exposure=existing_security,
            post_industry_exposure=existing_industry,
            post_total_exposure=existing_total,
            next_lot_cost=metrics(first_lot)[2],
            next_lot_post_equity=metrics(first_lot)[3],
            next_lot_cap=stock_limit * metrics(first_lot)[3],
            reason_codes=first_failures or (ReasonCode.BUDGET_CAPACITY,),
        )

    reasons_out = [ReasonCode.CAPACITY_AVAILABLE, *next_failures]
    if not next_failures:
        reasons_out = [ReasonCode.CAPACITY_AVAILABLE]
    return QuantityDecision(
        quantity=quantity,
        gross=gross,
        fees=fees,
        total_cost=total_cost,
        target_cap=target_cap,
        conservative_post_fill_equity=post_equity,
        existing_security_exposure=existing_security,
        existing_industry_exposure=existing_industry,
        existing_total_exposure=existing_total,
        post_security_exposure=security_exposure,
        post_industry_exposure=industry_exposure,
        post_total_exposure=total_exposure,
        next_lot_cost=next_cost,
        next_lot_post_equity=next_equity,
        next_lot_cap=stock_limit * next_equity,
        reason_codes=ordered_reasons(reasons_out),
    )


@dataclass(frozen=True)
class EntryLimits:
    max_active_or_reserved_per_industry: int
    max_new_buckets_per_day: int

    def __post_init__(self) -> None:
        if self.max_active_or_reserved_per_industry <= 0:
            raise ProductDailyError("invalid_entry_policy", "industry limit must be positive")
        if self.max_new_buckets_per_day <= 0:
            raise ProductDailyError("invalid_entry_policy", "daily limit must be positive")


@dataclass(frozen=True)
class CandidateEntry:
    opportunity_id: str
    security_code: str
    level1_industry_id: str
    projection: CandidateProjection
    input_valid: bool
    knowledge_cutoff_valid: bool
    policy_adopted: bool

    def __post_init__(self) -> None:
        for field_name in ("opportunity_id", "security_code", "level1_industry_id"):
            require_text(getattr(self, field_name), field_name)
        if not isinstance(self.projection, CandidateProjection):
            raise ProductDailyError("invalid_candidate_state", "projection must be typed")


@dataclass(frozen=True)
class EntryDecision:
    accepted: bool
    reason_codes: tuple[ReasonCode, ...]
    selected_bucket_id: str | None
    first_considered_bucket_id: str | None
    second_bucket_probe: bool
    quantity: QuantityDecision | None
    funding_state: FundingState
    funding_state_changed: bool
    reservation_id: str | None

    @property
    def execution_authorized(self) -> bool:
        return False

    @property
    def forced_sale(self) -> bool:
        return False

    @property
    def digest(self) -> str:
        return digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "reason_codes": [item.value for item in self.reason_codes],
            "selected_bucket_id": self.selected_bucket_id,
            "first_considered_bucket_id": self.first_considered_bucket_id,
            "second_bucket_probe": self.second_bucket_probe,
            "quantity": None if self.quantity is None else self.quantity.to_dict(),
            "funding_state_digest": self.funding_state.digest,
            "funding_state_revision": self.funding_state.revision,
            "funding_state_changed": self.funding_state_changed,
            "reservation_id": self.reservation_id,
            "execution_authorized": self.execution_authorized,
            "forced_sale": self.forced_sale,
        }


def decide_entry(
    *,
    candidate: CandidateEntry,
    funding: FundingState,
    snapshot: RiskSnapshot,
    empty_bucket_ids: Sequence[str],
    active_or_reserved_security_codes: Sequence[str],
    active_industry_counts: Sequence[tuple[str, int]],
    new_buckets_today: int,
    limits: EntryLimits,
    fraction: Decimal | None,
    mark_price: Decimal | None,
    price_bound: Decimal | None,
    lot_size: int,
    quantity_limits: QuantityLimits,
    fee_rule: BuyFeeRule,
    command: CommandEnvelope,
) -> EntryDecision:
    """Evaluate one candidate against the first eligible empty bucket only."""

    reasons: list[ReasonCode] = []
    if not candidate.input_valid:
        reasons.append(ReasonCode.INPUT_UNAVAILABLE)
    if not candidate.knowledge_cutoff_valid:
        reasons.append(ReasonCode.KNOWLEDGE_CUTOFF_BREACH)
    if not candidate.policy_adopted:
        reasons.append(ReasonCode.POLICY_NOT_ADOPTED)
    if candidate.projection is CandidateProjection.EXPIRED:
        reasons.append(ReasonCode.ENTRY_WINDOW_EXPIRED)
    elif candidate.projection in {
        CandidateProjection.HELD,
        CandidateProjection.EXITING,
        CandidateProjection.CLOSED,
        CandidateProjection.ENTERED,
        CandidateProjection.RESERVED,
    }:
        reasons.append(ReasonCode.EXISTING_SECURITY_CYCLE)
    if candidate.security_code in set(active_or_reserved_security_codes):
        reasons.append(ReasonCode.EXISTING_SECURITY_CYCLE)
    counts = dict(active_industry_counts)
    if counts.get(candidate.level1_industry_id, 0) >= limits.max_active_or_reserved_per_industry:
        reasons.append(ReasonCode.CONCENTRATION_CAPACITY)
    if new_buckets_today >= limits.max_new_buckets_per_day:
        reasons.append(ReasonCode.DAY_LIMIT)
    ordered_empty = tuple(sorted(empty_bucket_ids))
    first_bucket = ordered_empty[0] if ordered_empty else None
    if first_bucket is None:
        reasons.append(ReasonCode.NO_EMPTY_BUCKET)
    if reasons:
        return EntryDecision(
            accepted=False,
            reason_codes=ordered_reasons(reasons),
            selected_bucket_id=None,
            first_considered_bucket_id=first_bucket,
            second_bucket_probe=False,
            quantity=None,
            funding_state=funding,
            funding_state_changed=False,
            reservation_id=None,
        )

    assert first_bucket is not None
    bucket = funding.bucket(first_bucket)
    if bucket.phase not in {BucketPhase.EMPTY, BucketPhase.CLOSED}:
        return EntryDecision(
            accepted=False,
            reason_codes=(ReasonCode.NO_EMPTY_BUCKET,),
            selected_bucket_id=None,
            first_considered_bucket_id=first_bucket,
            second_bucket_probe=False,
            quantity=None,
            funding_state=funding,
            funding_state_changed=False,
            reservation_id=None,
        )

    prepared = prepare_cycle(
        funding,
        command,
        bucket_id=first_bucket,
        cycle_id=f"candidate-cycle:{candidate.opportunity_id}",
    )
    if not prepared.accepted or prepared.state.bucket(first_bucket).locked_cycle_budget <= 0:
        return EntryDecision(
            accepted=False,
            reason_codes=prepared.reason_codes or (ReasonCode.BUDGET_CAPACITY,),
            selected_bucket_id=None,
            first_considered_bucket_id=first_bucket,
            second_bucket_probe=False,
            quantity=None,
            funding_state=funding,
            funding_state_changed=False,
            reservation_id=None,
        )

    budget = prepared.state.bucket(first_bucket).locked_cycle_budget
    quantity = evaluate_quantity(
        QuantityRequest(
            decision_snapshot_id=snapshot.snapshot_id,
            bucket_budget=budget,
            fraction=fraction,
            security_code=candidate.security_code,
            level1_industry_id=candidate.level1_industry_id,
            mark_price=mark_price,
            price_bound=price_bound,
            lot_size=lot_size,
            limits=quantity_limits,
            fee_rule=fee_rule,
        ),
        snapshot,
    )
    if not quantity.allowed:
        return EntryDecision(
            accepted=False,
            reason_codes=quantity.reason_codes,
            selected_bucket_id=None,
            first_considered_bucket_id=first_bucket,
            second_bucket_probe=False,
            quantity=quantity,
            funding_state=funding,
            funding_state_changed=False,
            reservation_id=None,
        )

    reservation_id = (
        "reservation_"
        + digest(
            {
                "command_id": command.command_id,
                "bucket_id": first_bucket,
                "opportunity_id": candidate.opportunity_id,
            }
        ).removeprefix("sha256:")[:24]
    )
    return EntryDecision(
        accepted=True,
        reason_codes=(ReasonCode.ENTRY_RESERVED,),
        selected_bucket_id=first_bucket,
        first_considered_bucket_id=first_bucket,
        second_bucket_probe=False,
        quantity=quantity,
        funding_state=prepared.state,
        funding_state_changed=True,
        reservation_id=reservation_id,
    )

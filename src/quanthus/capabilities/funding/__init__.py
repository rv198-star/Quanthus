"""Funding capability public entry.

The single authoritative funding implementation is ``quanthus.product_daily.funding``
(fixed buckets, finite reserve, settlement and late deltas).  This module exposes the
P12-facing policy type, the initial-partition constructor and an immutable read view.
It never writes funding state itself; writes go through the authoritative functions in one
application-owned UnitOfWork.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from quanthus.product_daily.funding import (
    BucketPhase,
    BucketState,
    FundingState,
    apply_late_cash_delta,
    prepare_cycle,
    settle_batch,
    transfer_to_reserve,
)
from quanthus.shared.fact_contracts import FactError
from quanthus.shared.values import CNY_QUANTUM, MaybeDecimal, VersionRef

FIXED_BUCKET = "fixed_bucket"
FUNDING_CYCLE_NOT_BOUND = "FUNDING_CYCLE_NOT_BOUND"
BUCKET_ASSIGNMENT_CONFLICT = "BUCKET_ASSIGNMENT_CONFLICT"
FUNDING_LEDGER_MISMATCH = "FUNDING_LEDGER_MISMATCH"


@dataclass(frozen=True)
class FixedBucketPolicy:
    policy_ref: VersionRef
    unit_count: int
    bucket_budget: Decimal
    initial_reserve: Decimal
    reserve_cap: Decimal | None
    trial_fraction: Decimal = Decimal("0.30")

    def __post_init__(self) -> None:
        if not isinstance(self.unit_count, int) or isinstance(self.unit_count, bool):
            raise FactError("INVALID_POLICY", "unit_count must be an integer")
        if self.unit_count <= 0:
            raise FactError("INVALID_POLICY", "unit_count must be positive")
        for name in ("bucket_budget", "initial_reserve"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise FactError("INVALID_POLICY", f"{name} must be a non-negative Decimal")
            if value != value.quantize(CNY_QUANTUM):
                raise FactError("INVALID_POLICY", f"{name} must be quantized to 0.01")
        if self.bucket_budget <= 0:
            raise FactError("INVALID_POLICY", "bucket_budget must be positive")

    @property
    def mode(self) -> str:
        return FIXED_BUCKET

    @property
    def partitioned_cash(self) -> Decimal:
        return self.bucket_budget * self.unit_count + self.initial_reserve

    def bucket_ids(self) -> tuple[str, ...]:
        return tuple(f"bucket-{index:02d}" for index in range(1, self.unit_count + 1))


def initial_funding_state(policy: FixedBucketPolicy, account_cash: Decimal) -> FundingState:
    """Partition baseline cash with the authoritative funding types.

    Cash beyond the configured buckets and reserve is kept in the reserve only when the
    policy allows it; otherwise the mismatch is a named reconciliation outcome.
    """

    if account_cash != policy.partitioned_cash:
        raise FactError(
            BUCKET_ASSIGNMENT_CONFLICT,
            "baseline cash differs from the configured buckets plus reserve",
        )
    buckets = tuple(
        BucketState(
            bucket_id=identity,
            cash=policy.bucket_budget,
            target_notional=policy.bucket_budget,
            locked_cycle_budget=Decimal("0.00"),
            cycle_id=None,
            phase=BucketPhase.EMPTY,
        )
        for identity in policy.bucket_ids()
    )
    return FundingState(
        buckets=buckets,
        reserve=policy.initial_reserve,
        unallocated_adjustment=Decimal("0.00"),
        revision=0,
    )


@dataclass(frozen=True)
class BucketView:
    bucket_id: str
    phase: str
    target_notional: Decimal
    cash: MaybeDecimal
    cycle_id: str | None
    locked_cycle_budget: MaybeDecimal | None = None
    underfunded: MaybeDecimal | None = None
    trial_notional_cap: MaybeDecimal | None = None
    next_available: MaybeDecimal | None = None


@dataclass(frozen=True)
class FundingView:
    policy_ref: VersionRef
    mode: str
    funding_revision: int | None
    buckets: tuple[BucketView, ...]
    reserve: MaybeDecimal
    unallocated_adjustment: MaybeDecimal
    bound_account_revision: int | None
    status_reason: str | None


def describe_funding(
    policy: FixedBucketPolicy,
    state: FundingState,
    *,
    state_account_revision: int,
    current_account_revision: int,
) -> FundingView:
    """Read view; a partition that predates later account facts is shown as not bound."""

    stale = state_account_revision != current_account_revision
    reason = FUNDING_CYCLE_NOT_BOUND if stale else None

    def amount(value: Decimal) -> MaybeDecimal:
        return MaybeDecimal.unknown(FUNDING_CYCLE_NOT_BOUND) if stale else MaybeDecimal.known(value)

    def next_budget(item) -> Decimal:
        # What the bucket can actually deploy on its next activation: cash it
        # already holds, topped up by whatever the shared reserve could add.
        return min(item.target_notional, item.cash + state.reserve)

    return FundingView(
        policy_ref=policy.policy_ref,
        mode=policy.mode,
        funding_revision=state.revision,
        buckets=tuple(
            BucketView(
                item.bucket_id,
                item.phase.value,
                item.target_notional,
                amount(item.cash),
                item.cycle_id,
                amount(item.locked_cycle_budget),
                amount(max(Decimal("0.00"), item.target_notional - item.cash)),
                amount(
                    (policy.trial_fraction * item.locked_cycle_budget).quantize(CNY_QUANTUM)
                    if item.locked_cycle_budget
                    else Decimal("0.00")
                ),
                amount(next_budget(item)),
            )
            for item in sorted(state.buckets, key=lambda x: x.bucket_id)
        ),
        reserve=amount(state.reserve),
        unallocated_adjustment=amount(state.unallocated_adjustment),
        bound_account_revision=state_account_revision,
        status_reason=reason,
    )


__all__ = [
    "BUCKET_ASSIGNMENT_CONFLICT",
    "FIXED_BUCKET",
    "FUNDING_CYCLE_NOT_BOUND",
    "FUNDING_LEDGER_MISMATCH",
    "BucketView",
    "FixedBucketPolicy",
    "FundingState",
    "FundingView",
    "apply_late_cash_delta",
    "describe_funding",
    "describe_unbound_funding",
    "initial_funding_state",
    "prepare_cycle",
    "settle_batch",
    "transfer_to_reserve",
]


def describe_unbound_funding(policy: FixedBucketPolicy, reason_code: str) -> FundingView:
    """Policy-only view when no authoritative partition exists for the account."""

    unknown = MaybeDecimal.unknown(reason_code)
    return FundingView(
        policy_ref=policy.policy_ref,
        mode=policy.mode,
        funding_revision=None,
        buckets=tuple(
            BucketView(
                identity,
                BucketPhase.EMPTY.value,
                policy.bucket_budget,
                unknown,
                None,
                unknown,
                unknown,
                unknown,
            )
            for identity in policy.bucket_ids()
        ),
        reserve=unknown,
        unallocated_adjustment=unknown,
        bound_account_revision=None,
        status_reason=reason_code,
    )

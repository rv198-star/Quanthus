# E8 sell/reduction sizing for exit_plan decisions (issue #723).
# Reduce math and guard rails are frozen in #723 comment-7117737408 and
# HELD-REDUCTION-CONTRACT.candidate.v2.  The planner sizes the sell;
# sellability, order reservation and the confirm gate stay elsewhere.

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

ALREADY_BELOW_TARGET = "ALREADY_BELOW_TARGET"
REDUCE_RESIDUE_LOT_ZERO = "REDUCE_RESIDUE_LOT_ZERO"
REDUCE_BUDGET_UNKNOWN = "REDUCE_BUDGET_UNKNOWN"
MARK_PRICE_UNAVAILABLE = "MARK_PRICE_UNAVAILABLE"
NOTHING_SELLABLE_T1 = "NOTHING_SELLABLE_T1"


@dataclass(frozen=True)
class ExitSizing:
    # Resolved sell quantity for one exit_plan against live account facts.
    security_code: str
    exit_kind: str
    held_qty: int
    sellable_qty: int
    keep_qty: int
    sell_qty: int
    mark_price: Decimal | None
    locked_cycle_budget: Decimal | None
    bucket_id: str | None
    reason_code: str | None

    @property
    def executable(self) -> bool:
        return self.sell_qty > 0


def _lot_floor(quantity: Decimal, lot_size: int) -> int:
    # Round ``quantity`` down to a whole lot; a sub-lot residue floors to 0.
    if lot_size <= 0:
        return int(quantity.to_integral_value(rounding=ROUND_FLOOR))
    lots = (quantity / Decimal(lot_size)).to_integral_value(rounding=ROUND_FLOOR)
    return int(lots) * lot_size


def size_exit(
    *,
    security_code: str,
    exit_kind: str,
    target_position_fraction: Decimal | None,
    held_qty: int,
    sellable_qty: int,
    mark_price: Decimal | None,
    locked_cycle_budget: Decimal | None,
    bucket_id: str | None,
    lot_size: int,
    covered_qty: int = 0,
) -> ExitSizing:
    # Compute the sell quantity for one exit_plan decision.  sellable_qty
    # already nets out today buys and other resting sell orders, so a
    # confirmed plan can never oversell or double-sell the same shares.
    # covered_qty is the in-flight sell quantity already resting toward the
    # same exit target: the new plan only adds the uncovered difference, so
    # an existing 100/200-share reservation only ever adds 100/0 more.
    if held_qty <= 0:
        return ExitSizing(
            security_code,
            exit_kind,
            held_qty,
            sellable_qty,
            0,
            0,
            mark_price,
            locked_cycle_budget,
            bucket_id,
            ALREADY_BELOW_TARGET,
        )
    if sellable_qty <= 0:
        return ExitSizing(
            security_code,
            exit_kind,
            held_qty,
            sellable_qty,
            held_qty,
            0,
            mark_price,
            locked_cycle_budget,
            bucket_id,
            NOTHING_SELLABLE_T1,
        )

    if exit_kind == "full":
        return ExitSizing(
            security_code,
            exit_kind,
            held_qty,
            sellable_qty,
            0,
            max(0, min(held_qty - covered_qty, sellable_qty)),
            mark_price,
            locked_cycle_budget,
            bucket_id,
            None,
        )

    if mark_price is None or mark_price <= 0:
        return ExitSizing(
            security_code,
            exit_kind,
            held_qty,
            sellable_qty,
            held_qty,
            0,
            mark_price,
            locked_cycle_budget,
            bucket_id,
            MARK_PRICE_UNAVAILABLE,
        )
    if bucket_id is None or locked_cycle_budget is None or locked_cycle_budget <= 0:
        return ExitSizing(
            security_code,
            exit_kind,
            held_qty,
            sellable_qty,
            held_qty,
            0,
            mark_price,
            locked_cycle_budget,
            bucket_id,
            REDUCE_BUDGET_UNKNOWN,
        )

    # E8 r2-3: the keep target uses the raw precision product - never a
    # cent-rounded value - so a sub-lot residue is not rounded up into a
    # spurious whole lot before the lot floor is applied.
    max_remaining_value = target_position_fraction * locked_cycle_budget
    keep_qty = _lot_floor(max_remaining_value / mark_price, lot_size)
    if keep_qty <= 0 and target_position_fraction > 0:
        return ExitSizing(
            security_code,
            exit_kind,
            held_qty,
            sellable_qty,
            0,
            0,
            mark_price,
            locked_cycle_budget,
            bucket_id,
            REDUCE_RESIDUE_LOT_ZERO,
        )
    sell_qty = min(held_qty - keep_qty - covered_qty, sellable_qty)
    if sell_qty <= 0:
        return ExitSizing(
            security_code,
            exit_kind,
            held_qty,
            sellable_qty,
            keep_qty,
            0,
            mark_price,
            locked_cycle_budget,
            bucket_id,
            ALREADY_BELOW_TARGET,
        )
    return ExitSizing(
        security_code,
        exit_kind,
        held_qty,
        sellable_qty,
        keep_qty,
        sell_qty,
        mark_price,
        locked_cycle_budget,
        bucket_id,
        None,
    )


__all__ = [
    "ALREADY_BELOW_TARGET",
    "ExitSizing",
    "MARK_PRICE_UNAVAILABLE",
    "NOTHING_SELLABLE_T1",
    "REDUCE_BUDGET_UNKNOWN",
    "REDUCE_RESIDUE_LOT_ZERO",
    "size_exit",
]

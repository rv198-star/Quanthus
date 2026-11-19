"""Account valuation: economic equity from one account projection and one price snapshot."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from quanthus.capabilities.accounting.facts import AccountProjection
from quanthus.shared.values import CNY_QUANTUM, MaybeDecimal

VALUATION_UNAVAILABLE = "VALUATION_UNAVAILABLE"
COST_BASIS_UNKNOWN = "BASELINE_COVERAGE_UNKNOWN"


@dataclass(frozen=True)
class PositionValuation:
    security_code: str
    quantity: int
    mark_price: MaybeDecimal
    market_value: MaybeDecimal
    average_cost: MaybeDecimal


@dataclass(frozen=True)
class AccountValuation:
    """Economic view; holds stay inside cash_total and are reported separately by callers."""

    valuation_ref: str | None
    cash_total: Decimal
    positions_value: MaybeDecimal
    economic_equity: MaybeDecimal
    positions: tuple[PositionValuation, ...]


def value_account(
    projection: AccountProjection,
    *,
    marks: Mapping[str, Decimal],
    valuation_ref: str | None,
    cost_principal: Mapping[str, MaybeDecimal],
) -> AccountValuation:
    """Value every held position with the same snapshot; unknown marks stay unknown.

    ``cost_principal`` is the effective principal per security derived from the current
    fact chain. A missing entry means the cost is not known (for example a baseline holding
    without cost evidence) and is reported as unknown instead of zero.
    """

    rows: list[PositionValuation] = []
    total = Decimal("0")
    unknown_reason: str | None = None
    for position in projection.positions:
        mark = marks.get(position.security_code) if valuation_ref is not None else None
        if mark is None:
            mark_value = MaybeDecimal.unknown(VALUATION_UNAVAILABLE)
            market_value = MaybeDecimal.unknown(VALUATION_UNAVAILABLE)
            unknown_reason = VALUATION_UNAVAILABLE
        else:
            value = (mark * position.quantity).quantize(CNY_QUANTUM)
            mark_value = MaybeDecimal.known(mark)
            market_value = MaybeDecimal.known(value)
            total += value
        principal = cost_principal.get(position.security_code)
        if principal is None or principal.value is None:
            average = MaybeDecimal.unknown(
                principal.reason_code
                if principal is not None and principal.reason_code
                else COST_BASIS_UNKNOWN
            )
        else:
            average = MaybeDecimal.known(
                (principal.value / position.quantity).quantize(Decimal("0.0001"))
            )
        rows.append(
            PositionValuation(
                position.security_code, position.quantity, mark_value, market_value, average
            )
        )
    if unknown_reason is not None:
        positions_value = MaybeDecimal.unknown(unknown_reason)
        equity = MaybeDecimal.unknown(unknown_reason)
    else:
        positions_value = MaybeDecimal.known(total)
        equity = MaybeDecimal.known((projection.cash + total).quantize(CNY_QUANTUM))
    return AccountValuation(valuation_ref, projection.cash, positions_value, equity, tuple(rows))

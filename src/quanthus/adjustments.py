"""Point-in-time selection of versioned corporate-action adjustment factors."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date


class AdjustmentFactorError(ValueError):
    """Raised when factor metadata cannot support a deterministic PIT lookup."""


@dataclass(frozen=True)
class AdjustmentFactor:
    """One factor revision, visible no earlier than ``knowledge_cutoff``."""

    stock_code: str
    effective_date: date
    factor: float
    version: str
    knowledge_cutoff: date

    def __post_init__(self) -> None:
        if not self.stock_code.strip():
            raise AdjustmentFactorError("stock_code must not be empty")
        if not self.version.strip():
            raise AdjustmentFactorError("version must not be empty")
        if not math.isfinite(self.factor) or self.factor <= 0:
            raise AdjustmentFactorError("factor must be finite and positive")


class AdjustmentFactorIndex:
    """Immutable, fail-closed index for replay-time factor selection.

    A factor may affect prices from its effective date, but it may only be read by a
    replay whose knowledge cutoff is on or after the factor's declared visibility
    date.  The latest eligible effective-date record is selected.
    """

    def __init__(self, factors: Iterable[AdjustmentFactor]) -> None:
        by_code: dict[str, list[AdjustmentFactor]] = {}
        versioned_keys: set[tuple[str, date, str, date]] = set()
        for factor in factors:
            key = (
                factor.stock_code,
                factor.effective_date,
                factor.version,
                factor.knowledge_cutoff,
            )
            if key in versioned_keys:
                raise AdjustmentFactorError(
                    "duplicate factor revision for "
                    f"{factor.stock_code} on {factor.effective_date.isoformat()}"
                )
            versioned_keys.add(key)
            by_code.setdefault(factor.stock_code, []).append(factor)
        self._by_code = {
            code: tuple(
                sorted(
                    entries,
                    key=lambda item: (
                        item.effective_date,
                        item.knowledge_cutoff,
                        item.version,
                    ),
                )
            )
            for code, entries in by_code.items()
        }

    def at(
        self,
        stock_code: str,
        market_date: date,
        knowledge_cutoff: date,
    ) -> AdjustmentFactor | None:
        """Return the latest factor record available at the replay cutoff.

        ``None`` is deliberate: callers must explicitly choose the unadjusted
        treatment when no eligible factor exists instead of borrowing a future one.
        """

        candidates = [
            factor
            for factor in self._by_code.get(stock_code, ())
            if factor.effective_date <= market_date and factor.knowledge_cutoff <= knowledge_cutoff
        ]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda item: (item.effective_date, item.knowledge_cutoff, item.version),
        )

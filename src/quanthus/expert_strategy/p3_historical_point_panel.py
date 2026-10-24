"""Borrow one already materialized immutable panel for bounded security/date reads.

The original panel owns its connection and TMP lifecycle. This facade never
builds another feature table, changes a value or persists a cross-Job cache.
Unavailable prices remain unavailable for the affected key, not a global-day
Decimal conversion error from an unrelated security.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from quanthus.backtest.baseline_research import BaselineFeaturePanel, BaselineFeatureRow
from quanthus.expert_strategy import p3_base24_native as native
from quanthus.expert_strategy import p3_historical_robustness as h


class PointPanel(BaselineFeaturePanel):
    def __init__(self, owner: h.DayPanel) -> None:
        self.path, self.report = owner.path, owner.report
        self._connection = owner._connection
        if self._connection is None:
            raise native.InputError("HISTORY_POINT_OWNER_CLOSED")
        # The composite unique index still enforces the complete key census.
        # A single-column access index also supports the native point filter.
        self._connection.execute(
            "CREATE INDEX feature_panel_security_access ON feature_panel(security_id)"
        )
        self._owner = owner
        self.unavailable_observations = 0

    def feature(self, market_date: date, security_id: str) -> BaselineFeatureRow | None:
        if getattr(self, "_point_day", None) != market_date:
            self._point_day = market_date
            self._point_cache: dict[str, BaselineFeatureRow | None] = {}
        if security_id in self._point_cache:
            return self._point_cache[security_id]
        rows = self._rows(
            "SELECT * EXCLUDE (exception_active, unexplained_price_transition, "
            "scope_continuity_excluded) FROM feature_panel WHERE market_date=? AND security_id=?",
            (market_date, security_id),
        )
        if len(rows) > 1:
            raise native.InputError("HISTORY_FEATURE_DUPLICATE")
        row = None
        if rows and rows[0]["close_price"] is None:
            if rows[0]["entry_eligible"]:
                raise native.InputError("HISTORY_POINT_NULL_CLOSE_ELIGIBLE")
            self.unavailable_observations = getattr(self, "unavailable_observations", 0) + 1
        elif rows:
            row = BaselineFeatureRow.from_mapping(rows[0])
            if not row.close_price.is_finite() or row.close_price <= 0:
                raise native.InputError("HISTORY_POINT_CLOSE_INVALID")
            if any(
                getattr(row, key) is None
                for key in (
                    "total_return_index",
                    "sma20",
                    "sma60",
                    "momentum20",
                    "rsi14",
                    "realized_volatility20",
                )
            ):
                row = replace(row, entry_eligible=False)
        self._point_cache[security_id] = row
        return row

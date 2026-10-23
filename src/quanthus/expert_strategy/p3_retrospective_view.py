"""Result-blind P3 reading with separate information and business boundaries.

This reader deliberately leaves NativeView.prepared_manifest unset: the strict
P4/P2 bridge must reject it. It reuses the native identity/calendar joins, not
their historical knowledge selector. Source clocks and Decimal columns survive
unchanged. No candidate, execution eligibility or historical availability is
inferred by reading a row.
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any

from quanthus.expert_strategy import p3_base24_native as native
from quanthus.expert_strategy import p3_retrospective_input as inputs


class RetrospectiveView(native.NativeView):
    """A distinct, verified input purpose; never an admitted strict NativeView."""

    def __init__(self, root: Path, *, manifest_sha256: str, deadline: float | None = None):
        root = native.physical(root)
        deadline = min(
            deadline if deadline is not None else time.monotonic() + 600, time.monotonic() + 600
        )
        watched = inputs._physical_files() | {"manifest.json", "READY"}
        before = {n: native._fingerprint(native._member(root, n)) for n in watched}
        self.input_verification = inputs.verify(
            root, manifest_sha256=manifest_sha256, deadline=deadline
        )
        self.input_manifest_sha256 = manifest_sha256
        self.input_fingerprints = before
        self.information_cutoff = datetime.fromisoformat(
            native._json(root / "plan.json")["information_cutoff"]
        )
        super().__init__(root, deadline=deadline)
        try:
            self._unchanged()
        except BaseException:
            super().__exit__()
            raise

    def __enter__(self) -> RetrospectiveView:
        return self

    def _unchanged(self) -> None:
        super()._unchanged()
        if self.input_fingerprints != {
            n: native._fingerprint(native._member(self.root, n)) for n in self.input_fingerprints
        }:
            raise native.InputError("RETRO_INPUT_CHANGED_DURING_READ")

    def _at(self, cutoff: datetime, *, market_only: bool, execution_only: bool = False) -> None:
        if not market_only:
            raise native.InputError("RETRO_EXPLICIT_BUSINESS_READ_REQUIRED")
        self._unchanged()
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise native.InputError("AWARE_CUTOFF_REQUIRED")
        if not date(2025, 1, 1) <= cutoff.astimezone(native.TZ).date() <= date(2026, 9, 18):
            raise native.InputError("FROZEN_DECISION_WINDOW")
        if cutoff > self.information_cutoff:
            raise native.InputError("RETRO_BUSINESS_AFTER_SNAPSHOT")
        # Offline verification already established that every raw row belongs to
        # the exact original half-open snapshot. Re-selecting at historical T
        # would silently revert this purpose to the strict/PIT reader.
        self.connection.execute("BEGIN TRANSACTION")
        try:
            for table in native.KEYS:
                self.connection.execute(
                    f"CREATE OR REPLACE TEMP VIEW {table} AS SELECT * FROM raw_{table}"
                )
            self._market_scope(cutoff)
            self._unchanged()
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def history_batches(
        self, *, business_read_boundary: datetime, batch_size: int = 8192
    ) -> Iterator[Any]:
        """Stream original joined rows once; execution-tail rows are labelled.

        Consumers must still cap each T feature at its own business boundary.
        The Sep18 observations are execution-only, never additional decisions.
        """
        import pyarrow as pa  # type: ignore[import-untyped]

        for block in super().market_batches(business_read_boundary, batch_size=batch_size):
            day = block.column(block.schema.get_field_index("trade_date")).to_pylist()
            roles = [
                "WARMUP"
                if d < date(2025, 1, 1)
                else "EXECUTION_ONLY"
                if d > date(2026, 9, 17)
                else "DECISION_HISTORY"
                for d in day
            ]
            fields = {
                "r2_information_cutoff": pa.array(
                    [self.information_cutoff] * len(block), type=pa.timestamp("us", tz="UTC")
                ),
                "r2_business_read_boundary": pa.array(
                    [business_read_boundary] * len(block), type=pa.timestamp("us", tz="UTC")
                ),
                "r2_observation_role": pa.array(roles, type=pa.string()),
            }
            for name, array in fields.items():
                if name in block.schema.names:
                    raise native.InputError("RETRO_RESERVED_COLUMN")
                block = block.append_column(name, array)
            yield block
        self._unchanged()


def input_census(view: RetrospectiveView, boundary: datetime) -> dict[str, Any]:
    """Safe consumer census, not candidate/B qualification or D activation."""
    from collections import Counter

    roles: Counter[str] = Counter()
    dates: Counter[str] = Counter()
    total = late = state_unknown = buy_unknown = sell_unknown = 0
    for block in view.history_batches(business_read_boundary=boundary):
        # These columns contain identity/time/state only. Price columns stay in
        # Arrow and are never converted to Python or included in the receipt.
        fields = [
            "trade_date",
            "r2_observation_role",
            "bar_knowledge_from",
            "close_at",
            "is_tradable",
            "can_buy",
            "can_sell",
        ]
        rows = block.select(fields).to_pylist()
        for row in rows:
            total += 1
            dates[row["trade_date"].isoformat()] += 1
            roles[row["r2_observation_role"]] += 1
            late += row["bar_knowledge_from"] > row["close_at"]
            state_unknown += row["is_tradable"] is None
            buy_unknown += row["can_buy"] is None
            sell_unknown += row["can_sell"] is None
    return {
        "status": "P3_RETROSPECTIVE_CONSUMER_READ_COMPLETED",
        "input_manifest_sha256": view.input_manifest_sha256,
        "information_cutoff": view.information_cutoff.isoformat(),
        "business_read_boundary": boundary.isoformat(),
        "joined_rows": total,
        "observation_roles": dict(sorted(roles.items())),
        "date_counts": dict(sorted(dates.items())),
        "original_bar_clocks_after_business_close": late,
        "state_unknown": state_unknown,
        "can_buy_unknown": buy_unknown,
        "can_sell_unknown": sell_unknown,
        "candidate_feature_execution_qualification": "NOT_GRANTED_BY_READING",
        "original_B_accepted": False,
        "D_activated": False,
        "strategy_or_economic_results_read": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--business-read-boundary", required=True)
    args = parser.parse_args(argv)
    with RetrospectiveView(args.root, manifest_sha256=args.manifest_sha256) as view:
        result = input_census(view, datetime.fromisoformat(args.business_read_boundary))
    print(json.dumps(result, sort_keys=True, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

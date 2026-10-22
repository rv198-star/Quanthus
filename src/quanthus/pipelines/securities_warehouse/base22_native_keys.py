"""Native typed key projection: no Python 64-writer fan-out or decoded row index.

The old bounded primitive and all qualified domain receipts stay unchanged.
Whole-image scans use DuckDB's bounded partitioned COPY for the same exact keys
and interval checks. Equal business keys share a bucket; hash collisions do not
establish equality. A failed TMP index and its evidence are retained.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import THROUGH
from .base22_bounded import GlobalScan, identifier, storage_census


class NativeGlobalScan(GlobalScan):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Every source file is immutable within this scan. Do not retain another
        # external-file cache on top of the registered 2 GiB DuckDB working set.
        self.connection.execute("SET enable_external_file_cache=false")
        self.connection.execute("SET partitioned_write_max_open_files=8")

    def _cutoff_predicate(self, cutoff: str, field: Any) -> str:
        import pyarrow as pa  # type: ignore[import-untyped]

        if pa.types.is_timestamp(field.type) and field.type.tz is not None:
            # Shanghai business-date <= THROUGH is precisely an instant before
            # the next Shanghai midnight. Retain original clocks and the legacy
            # verdict, while enabling Parquet range pruning without row-wise ICU.
            upper = datetime.combine(
                THROUGH + timedelta(days=1), time(), tzinfo=ZoneInfo("Asia/Shanghai")
            ).astimezone(timezone.utc)
            return f"{cutoff}>=TIMESTAMPTZ '{upper.isoformat()}'"
        return super()._cutoff_predicate(cutoff, field)

    def keys(self, name: str, business_key: list[str], version_key: list[str]) -> None:
        table = identifier(name)
        keys = ",".join(identifier(k) for k in version_key)
        bk = ",".join(identifier(k) for k in business_key)
        nulls = " OR ".join(identifier(k) + " IS NULL" for k in version_key)
        self.reject(f"SELECT 1 FROM {table} WHERE {nulls} LIMIT 1", "GLOBAL_KEY_NULL")
        index_root = self.budget.destination(self.temp / ("native-keys-" + name))
        projection = ",".join(identifier(k) for k in dict.fromkeys([*version_key, "knowledge_to"]))
        self.budget.check()
        self.connection.execute(
            f"COPY (SELECT {projection},hash({bk})%64 AS __bucket FROM {table}) "
            "TO '" + str(index_root).replace("'", "''") + "' "
            "(FORMAT PARQUET, COMPRESSION ZSTD, PARTITION_BY (__bucket), "
            "ROW_GROUP_SIZE 16384, OVERWRITE_OR_IGNORE false)"
        )
        self.budget.check()
        buckets = sorted(index_root.glob("__bucket=*"))
        counts = []
        query = f"""SELECT 1 FROM (SELECT knowledge_from,
            lag(knowledge_to) OVER w AS previous_end,
            row_number() OVER w AS n FROM native_bounded_keys
            WINDOW w AS (PARTITION BY {bk} ORDER BY knowledge_from))
            WHERE n>1 AND (previous_end IS NULL OR previous_end>knowledge_from) LIMIT 1"""
        for bucket in buckets:
            files = sorted(bucket.glob("*.parquet"))
            self.connection.read_parquet(
                [str(p) for p in files], hive_partitioning=False
            ).create_view("native_bounded_keys", replace=True)
            self.reject(
                f"SELECT 1 FROM native_bounded_keys GROUP BY {keys} HAVING count(*)>1 LIMIT 1",
                "GLOBAL_DUPLICATE_VERSION_KEY",
            )
            self.reject(query, "GLOBAL_OVERLAPPING_KNOWLEDGE_VERSIONS")
            count = self.connection.execute("SELECT count(*) FROM native_bounded_keys").fetchone()
            assert count is not None
            counts.append(count[0])
        self.key_metrics[name] = {
            "rows": sum(counts),
            "buckets": len(buckets),
            "largest_bucket_rows": max(counts, default=0),
            "index_storage": storage_census(index_root),
            "materializations": 1,
            "lifecycle": "TMP",
            "representation": "typed_native_partitioned_COPY",
            "Python_full_row_or_64_writer_index": False,
        }
        if buckets:
            self.connection.execute("DROP VIEW native_bounded_keys")
        # Retire only the successfully validated exact index owned by this scan.
        # Any failed COPY/check exits earlier and preserves all partial evidence.
        for bucket in buckets:
            for path in bucket.iterdir():
                path.unlink()
            bucket.rmdir()
        index_root.rmdir()

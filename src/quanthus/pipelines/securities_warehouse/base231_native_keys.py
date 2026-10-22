"""Equivalent bounded key checks: unique business keys cannot overlap versions.

Only complete exact GROUP BY equality establishes this shortcut. Repeated business
keys keep both legacy version-uniqueness and interval checks. Source-row rules,
null checks, full native keys and all cross-table validation stay unchanged.
Legacy producer scans are not modified; this class is a whole-successor verifier.
"""

from __future__ import annotations

from .base22_bounded import identifier, storage_census
from .base23_native_keys import NativeGlobalScan


class UniqueBusinessKeyScan(NativeGlobalScan):
    def keys(self, name: str, business_key: list[str], version_key: list[str]) -> None:
        if (
            not business_key
            or "knowledge_from" not in version_key
            or not set(business_key) <= set(version_key)
        ):
            return super().keys(name, business_key, version_key)
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
        unique = repeated = 0
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
            self.budget.check()
            duplicate_business = self.connection.execute(
                f"SELECT 1 FROM native_bounded_keys GROUP BY {bk} HAVING count(*)>1 LIMIT 1"
            ).fetchone()
            self.budget.check()
            if duplicate_business is None:
                # Version keys contain the full business key; uniqueness of the
                # latter proves version uniqueness and absence of interval pairs.
                unique += 1
            else:
                repeated += 1
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
            "exact_unique_business_buckets": unique,
            "legacy_version_checks_buckets": repeated,
        }
        if buckets:
            self.connection.execute("DROP VIEW native_bounded_keys")
        # Retire only this successful owned TMP; preserve any failed check index.
        for bucket in buckets:
            for path in bucket.iterdir():
                path.unlink()
            bucket.rmdir()
        index_root.rmdir()

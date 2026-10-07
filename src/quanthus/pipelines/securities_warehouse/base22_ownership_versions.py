"""Bounded knowledge versions for open, individually qualified ownership atoms.

The caller supplies independent, job-owned typed files and archives every source
observation. This successor covers open native observations only: closed native
intervals need their own reviewed transition contract and cannot be reopened.
"""

from __future__ import annotations

from collections.abc import Iterator
from itertools import groupby
from pathlib import Path
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError, Budget, GlobalScan, identifier
from .base22_f10_ownership import TABLES
from .base22_outputs import knowledge_versions

CONTRACT_ID = "p10-base22-open-ownership-observation-versions-v1"
MAX_VERSIONS_PER_KEY = 64


def ownership_versions(rows: list[dict[str, Any]], rule: dict[str, Any]) -> list[dict[str, Any]]:
    """No priority at equal clocks; only observed successor clocks close intervals."""
    if not rows or len(rows) > MAX_VERSIONS_PER_KEY:
        raise BoundedError("OWNERSHIP_VERSION_GROUP_BOUND_INVALID")
    keys = {digest([r[k] for k in rule["business_key"]]) for r in rows}
    if len(keys) != 1 or any(r["knowledge_to"] is not None for r in rows):
        raise BoundedError("OWNERSHIP_OPEN_OBSERVATION_VERSION_SCOPE_INVALID")
    result = knowledge_versions(rows, rule)
    # The helper copies atoms; incoming source rows and raw source evidence remain
    # immutable. Duplicate unchanged observations are retained in that evidence.
    for row in result:
        atoms = digest({k: v for k, v in row.items() if k != "knowledge_to"})
        if not any(
            atoms == digest({k: v for k, v in original.items() if k != "knowledge_to"})
            for original in rows
        ):
            raise BoundedError("OWNERSHIP_VERSION_ATOM_OR_ORIGINAL_CLOCK_CHANGED")
    return result


def version_batches(
    table: str,
    paths: list[Path],
    rule: dict[str, Any],
    schema: Any,
    budget: Budget,
    *,
    read_only_roots: list[Path] | None = None,
) -> Iterator[list[dict[str, Any]]]:
    """Global typed sort spills within the frozen budget; each key is bounded."""
    import pyarrow as pa  # type: ignore[import-untyped]
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    if (
        table not in TABLES
        or not paths
        or rule["version_key"] != (rule["business_key"] + ["knowledge_from"])
    ):
        raise BoundedError("OWNERSHIP_VERSION_TABLE_OR_KEY_CONTRACT_INVALID")
    with GlobalScan(budget, read_only_roots=read_only_roots) as scan:
        scan.add(table, paths)
        scan.connection.execute("SET TimeZone='UTC'")
        if any(
            not pq.ParquetFile(p).schema_arrow.equals(schema, check_metadata=False) for p in paths
        ):
            raise BoundedError("OWNERSHIP_VERSION_INPUT_SCHEMA_CHANGED")
        order = ",".join(identifier(k) for k in rule["business_key"] + ["knowledge_from"])
        reader = scan.connection.execute(
            f"SELECT * FROM {identifier(table)} ORDER BY {order}"
        ).fetch_record_batch(4096)

        def source() -> Iterator[dict[str, Any]]:
            for batch in reader:
                if batch.schema.names != schema.names or any(
                    actual.type != expected.type
                    and not (
                        pa.types.is_timestamp(actual.type)
                        and pa.types.is_timestamp(expected.type)
                        and actual.type.unit == expected.type.unit
                        and actual.type.tz in {"UTC", "Etc/UTC"}
                        and expected.type.tz == "UTC"
                    )
                    for actual, expected in zip(batch.schema, schema, strict=True)
                ):
                    raise BoundedError("OWNERSHIP_VERSION_INPUT_SCHEMA_CHANGED")
                budget.check()
                # DuckDB marks fields nullable and names UTC as Etc/UTC. Pinned
                # original Parquet types above permit only this lossless recast.
                yield from batch.cast(schema, safe=True).to_pylist()

        key_fields = rule["business_key"]
        for _, group in groupby(source(), key=lambda r: tuple(r[k] for k in key_fields)):
            rows: list[dict[str, Any]] = []
            for row in group:
                if len(rows) >= MAX_VERSIONS_PER_KEY:
                    raise BoundedError("OWNERSHIP_VERSION_GROUP_BOUND_INVALID")
                rows.append(row)
            budget.check()
            yield ownership_versions(rows, rule)

"""Move native event rows to Shanghai months without changing any value.

The legacy files are small. A frozen per-member row ceiling keeps reconstruction
bounded; larger files fail rather than falling back to a whole-table materialization.
This scope proves physical relocation only, never raw admission or historical PIT.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any

from .base22 import digest, file_sha
from .base22_bounded import BoundedError, Budget, Member, independent_copy
from .base22_sources import _fingerprint

CONTRACT_ID = "p10-base22-native-event-month-relocation-v1"
TABLES = {"dwd_disclosure", "dwd_disclosure_subject", "dwd_shareholding_change"}
MAX_MEMBER_ROWS = 8192


def _groups(data: Any) -> dict[str, list[int]]:
    pa = importlib.import_module("pyarrow")
    pc = importlib.import_module("pyarrow.compute")

    values = data.column("effective_at")
    if not pa.types.is_timestamp(values.type) or values.type.tz is None or values.null_count:
        raise BoundedError("RELOCATION_AWARE_NON_NULL_EFFECTIVE_TIME_REQUIRED")
    local = pc.cast(values, pa.timestamp(values.type.unit, tz="Asia/Shanghai"))
    years, months = pc.year(local).to_pylist(), pc.month(local).to_pylist()
    groups: dict[str, list[int]] = {}
    for index, (year, month) in enumerate(zip(years, months, strict=True)):
        key = f"effective_at_year={year}/effective_at_month={month}"
        groups.setdefault(key, []).append(index)
    return groups


def _input(member: Member, schema: Any) -> Any:
    pq = importlib.import_module("pyarrow.parquet")

    if not 0 < member.rows <= MAX_MEMBER_ROWS:
        raise BoundedError("RELOCATION_REGISTERED_MEMBER_ROW_CEILING")
    member.verify()
    file = pq.ParquetFile(member.path)
    if file.metadata.num_rows != member.rows or not file.schema_arrow.equals(schema):
        raise BoundedError("RELOCATION_INPUT_SCHEMA_OR_CENSUS_MISMATCH")
    return file


def plan_relocation(
    member: Member, *, table: str, schema: Any, rule: dict[str, Any], original_partition: str
) -> dict[str, Any]:
    """Freeze exact original bytes and row selection before the output is created."""
    if table not in TABLES or rule["partition_by"] != ["effective_at_year", "effective_at_month"]:
        raise BoundedError("RELOCATION_SCOPE_NOT_APPROVED_EVENT_MONTHS")
    before = _fingerprint(member.path)
    data = _input(member, schema).read(columns=["effective_at"])
    groups = _groups(data)
    if _fingerprint(member.path) != before or file_sha(member.path) != member.sha256:
        raise BoundedError("RELOCATION_SOURCE_CHANGED")
    body = {
        "contract_id": CONTRACT_ID,
        "table": table,
        "input": {
            "path": str(member.path),
            "sha256": member.sha256,
            "size_bytes": member.size_bytes,
            "rows": member.rows,
        },
        "schema_and_rule_sha256": digest({"schema": str(schema), "rule": rule}),
        "evaluator_sha256": file_sha(Path(__file__)),
        "original_partition": original_partition,
        "row_ceiling": MAX_MEMBER_ROWS,
        "routes": [
            {"partition": part, "rows": len(indices), "indices_sha256": digest(indices)}
            for part, indices in sorted(groups.items())
        ],
        "wrong_partition_rows": sum(
            len(indices) for part, indices in groups.items() if part != original_partition
        ),
        "semantic_changes_allowed": [],
        "consumer_admission": "NOT_GRANTED",
    }
    return {**body, "identity": digest(body)}


def relocate_member(
    member: Member,
    *,
    proof: dict[str, Any],
    schema: Any,
    rule: dict[str, Any],
    output: Path,
    budget: Budget,
) -> dict[str, Any]:
    """Write distinct files and reconstruct the original typed rows exactly.

    No sorting, clock repair, deduplication, alias repair or scalar conversion occurs.
    Only the physical month changes. Exact all-column equality includes nulls,
    Decimal precision and the original timestamp instants.
    """
    pa = importlib.import_module("pyarrow")
    pq = importlib.import_module("pyarrow.parquet")

    expected = plan_relocation(
        member,
        table=proof.get("table", ""),
        schema=schema,
        rule=rule,
        original_partition=proof.get("original_partition", ""),
    )
    if expected != proof:
        raise BoundedError("RELOCATION_FROZEN_PLAN_MISMATCH")
    # The guard precedes all mkdir/write operations, including healthy copies.
    output = budget.destination(output)
    before = member.verify()
    data = _input(member, schema).read()
    budget.check(reserve=data.nbytes * 4)
    groups = _groups(data)
    stem = digest(proof["input"])[7:] + ".parquet"
    results, reconstructed, positions = [], [], []
    for part, indices in sorted(groups.items()):
        dest = output / proof["table"] / part / stem
        selected = data.take(pa.array(indices, type=pa.int64()))
        if proof["wrong_partition_rows"] == 0:
            copied = independent_copy(member, dest, budget)
            disposition = copied["mode"]
        else:
            dest = budget.destination(dest)
            if dest.exists():
                raise BoundedError("RELOCATION_OUTPUT_EXISTS")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("xb") as stream:
                pq.write_table(selected, stream, compression="zstd")
                stream.flush()
                os.fsync(stream.fileno())
            disposition = "exact_typed_row_month_relocation"
        actual = pq.ParquetFile(dest).read()
        if not actual.equals(selected, check_metadata=True) or dest.stat().st_nlink != 1:
            raise BoundedError("RELOCATION_SERIALIZED_TYPED_VALUES_CHANGED")
        reconstructed.append(actual)
        positions.extend(indices)
        results.append(
            {
                "partition": part,
                "path": str(dest.relative_to(budget.root)),
                "rows": actual.num_rows,
                "sha256": file_sha(dest),
                "allocated_bytes": dest.stat().st_blocks * 512,
                "indices_sha256": digest(indices),
                "disposition": disposition,
            }
        )
        budget.check()
    reverse = sorted(range(len(positions)), key=positions.__getitem__)
    restored = pa.concat_tables(reconstructed).take(pa.array(reverse, type=pa.int64()))
    if sorted(positions) != list(range(member.rows)) or not restored.equals(
        data, check_metadata=True
    ):
        raise BoundedError("RELOCATION_COMPLETE_ORIGINAL_ROWS_NOT_PRESERVED")
    if _fingerprint(member.path) != before or file_sha(member.path) != member.sha256:
        raise BoundedError("RELOCATION_SOURCE_CHANGED")
    body = {
        "plan_identity": proof["identity"],
        "files": results,
        "original_rows": member.rows,
        "wrong_partition_rows": proof["wrong_partition_rows"],
        "all_typed_values_exact": True,
        "source_unchanged": True,
        "complete_Base22": False,
        "READY": False,
    }
    return {**body, "identity": digest(body)}

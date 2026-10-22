"""Financial-only bounded native/qualified-observation union; no image admission.

One key-only interval map serves a whole affected year. Full typed rows remain
streamed in original member order. Local row-position patches and input column
digests allow cold verification without dereferencing archived source paths.
"""

from __future__ import annotations

import hashlib
import os
from decimal import Decimal
from pathlib import Path
from typing import Any

from .base22 import digest, file_sha
from .base22_bounded import BoundedError, Budget, GlobalScan, Member, identifier, semantic_scan
from .base22_outputs import financial_concept_versions

CONTRACT_ID = "p10-base22-qualified-financial-year-union-v1"
YEAR_TABLES = {"dwd_financial_report", "dwd_financial_fact"}


def merge_concepts(
    native: list[dict[str, Any]], incoming: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The approved prefix rule, with every old native atom explicitly preserved."""
    if not native or not incoming or len(native) + len(incoming) > 32768:
        raise BoundedError("FINANCIAL_UNION_CONCEPT_BOUND_INVALID")
    result = financial_concept_versions([*native, *incoming])

    def key(r: dict[str, Any]) -> tuple[Any, Any]:
        return r["financial_concept_id"], r["knowledge_from"]

    selected = {key(r): r for r in result}
    if len(selected) != len(result) or len({key(r) for r in native}) != len(native):
        raise BoundedError("FINANCIAL_UNION_CONCEPT_VERSION_NOT_UNIQUE")
    for row in native:
        out = selected.get(key(row))
        if (
            out is None
            or any(row[f] != out[f] for f in row if f != "knowledge_to")
            or (row["knowledge_to"] is not None and row["knowledge_to"] != out["knowledge_to"])
        ):
            raise BoundedError("FINANCIAL_UNION_NATIVE_CONCEPT_ATOM_CHANGED")
    proof = {
        "native_rows": len(native),
        "incoming_observations": len(incoming),
        "output_versions": len(result),
        "old_versions_retained": len(native),
        "new_versions": len(result) - len(native),
        "nonextending_captures_archived": len(incoming) - len(result) + len(native),
        "before_census_sha256": digest(sorted(digest(r) for r in native)),
        "incoming_census_sha256": digest(sorted(digest(r) for r in incoming)),
        "output_census_sha256": digest(sorted(digest(r) for r in result)),
    }
    return result, proof


def _validate_rule(table: str, year: int, rule: dict[str, Any]) -> None:
    if (
        table not in YEAR_TABLES
        or type(year) is not int
        or rule["partition_by"] != ["period_end_year"]
        or "period_end" not in rule["business_key"]
        or rule["version_key"] != rule["business_key"] + ["knowledge_from"]
    ):
        raise BoundedError("FINANCIAL_UNION_KEY_DERIVED_YEAR_NOT_PROVEN")


def _map(
    scan: GlobalScan,
    members: list[Member],
    native_count: int,
    rule: dict[str, Any],
    year: int,
) -> None:
    """Only typed keys, clocks and ordinals are sorted; values are never copied."""
    import pyarrow as pa  # type: ignore[import-untyped]

    paths = [str(m.path) for m in members]
    scan.connection.read_parquet(
        paths, hive_partitioning=False, filename=True, file_row_number=True
    ).create_view("financial_input", replace=True)
    scan.connection.register(
        "financial_members",
        pa.table(
            {
                "filename": paths,
                "member": range(len(paths)),
                "incoming": [i >= native_count for i in range(len(paths))],
            }
        ),
    )
    keys = ",".join(identifier(k) for k in rule["business_key"])
    versions = ",".join(identifier(k) for k in rule["version_key"])
    nulls = " OR ".join(identifier(k) + " IS NULL" for k in rule["version_key"])
    scan.reject(
        f"SELECT 1 FROM financial_input WHERE {nulls} LIMIT 1", "FINANCIAL_UNION_VERSION_KEY_NULL"
    )
    scan.reject(
        f"SELECT 1 FROM financial_input GROUP BY {versions} HAVING count(*)>1 LIMIT 1",
        "FINANCIAL_UNION_EQUAL_CLOCK_VERSION_CONFLICT",
    )
    scan.reject(
        f"SELECT 1 FROM financial_input WHERE year(period_end)<>{year} OR "
        "period_end>DATE '2026-08-31' OR knowledge_to<=knowledge_from LIMIT 1",
        "FINANCIAL_UNION_DATE_OR_INTERVAL_INVALID",
    )
    # Observing another provider at a later time does not establish priority.
    scan.reject(
        f"SELECT 1 FROM financial_input r JOIN financial_members m USING(filename) "
        f"GROUP BY {keys} HAVING bool_or(m.incoming) AND NOT bool_and(m.incoming) "
        "AND count(DISTINCT record_source)>1 LIMIT 1",
        "FINANCIAL_UNION_CROSS_PROVIDER_PRIORITY_UNQUALIFIED",
    )
    scan.connection.execute(
        "CREATE TEMP TABLE financial_interval_map AS WITH clocks AS ("
        f"SELECT m.member,r.file_row_number AS row,r.knowledge_to AS before_to,"
        f"lead(r.knowledge_from) OVER(PARTITION BY {keys} ORDER BY knowledge_from) AS next_clock "
        "FROM financial_input r JOIN financial_members m USING(filename)) "
        "SELECT member,row,before_to,next_clock AS after_to FROM clocks "
        "WHERE next_clock IS NOT NULL AND (before_to IS NULL OR next_clock<before_to)"
    )
    scan.budget.check()


def _patch_schema() -> Any:
    import pyarrow as pa

    return pa.schema(
        [
            ("member", pa.int64()),
            ("row", pa.int64()),
            ("before_to", pa.timestamp("us", tz="UTC")),
            ("after_to", pa.timestamp("us", tz="UTC")),
        ]
    )


def _patch_batch(batch: Any, patches: list[tuple[int, Any, Any]], offset: int) -> Any:
    import pyarrow as pa

    index = batch.schema.get_field_index("knowledge_to")
    if index < 0:
        raise BoundedError("FINANCIAL_UNION_KNOWLEDGE_FIELD_MISSING")
    ends, starts = batch.column(index).to_pylist(), batch.column("knowledge_from").to_pylist()
    seen = set()
    for row, before, after in patches:
        p = row - offset
        if (
            p in seen
            or not 0 <= p < len(ends)
            or ends[p] != before
            or after is None
            or after <= starts[p]
            or before is not None
            and after >= before
        ):
            raise BoundedError("FINANCIAL_UNION_EXACT_BEFORE_OR_CLOSURE_INVALID")
        seen.add(p)
        ends[p] = after
    return batch.set_column(
        index, batch.schema.field(index), pa.array(ends, type=batch.schema.field(index).type)
    )


def materialize_year(
    native: list[Member],
    incoming: list[Member],
    *,
    table: str,
    year: int,
    schema: Any,
    rule: dict[str, Any],
    output: Path,
    patches: Path,
    budget: Budget,
) -> dict[str, Any]:
    """One affected financial year; caller separately pins true source admission.

    Equal clocks and mixed providers fail before creating an output. Source paths
    below become archived strings in the receipt; a cold reader must use output,
    local patches and the column digests rather than opening those paths.
    """
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    _validate_rule(table, year, rule)
    members = [*native, *incoming]
    if not incoming or len({m.path for m in members}) != len(members):
        raise BoundedError("FINANCIAL_UNION_PARTITION_INPUT_INVALID")
    output, patches = budget.destination(output), budget.destination(patches)
    partial = output.with_name(output.name + ".partial")
    if any(p.exists() for p in [output, partial, patches]):
        raise BoundedError("FINANCIAL_UNION_OUTPUT_EXISTS")
    before = [m.verify() for m in members]
    for member in members:
        f = pq.ParquetFile(member.path)
        if f.metadata.num_rows != member.rows or not f.schema_arrow.equals(schema):
            raise BoundedError("FINANCIAL_UNION_INPUT_SCHEMA_OR_CENSUS_CHANGED")
    segments: list[dict[str, Any]] = []
    total = 0
    with GlobalScan(budget, read_only_roots=sorted({m.path.parent for m in members})) as scan:
        _map(scan, members, len(native), rule, year)
        patches.parent.mkdir(parents=True, exist_ok=True)
        with (
            patches.open("xb") as stream,
            pq.ParquetWriter(stream, _patch_schema(), compression="zstd") as writer,
        ):
            for batch in scan.connection.execute(
                "SELECT * FROM financial_interval_map ORDER BY member,row"
            ).fetch_record_batch(budget.limits.batch_rows):
                writer.write_batch(batch.cast(_patch_schema(), safe=True))
                budget.check()
        output.parent.mkdir(parents=True, exist_ok=True)
        with (
            partial.open("xb") as stream,
            pq.ParquetWriter(stream, schema, compression="zstd") as writer,
        ):
            for n, member in enumerate(members):
                original = semantic_scan(
                    [member.path], schema, budget.limits.batch_rows, check=budget.check
                )
                offset = changed = 0
                for batch in pq.ParquetFile(member.path).iter_batches(
                    batch_size=budget.limits.batch_rows
                ):
                    changes = scan.connection.execute(
                        "SELECT row,before_to,after_to FROM financial_interval_map "
                        "WHERE member=? AND row>=? AND row<? ORDER BY row",
                        [n, offset, offset + batch.num_rows],
                    ).fetchall()
                    if len(changes) > batch.num_rows:
                        raise BoundedError("FINANCIAL_UNION_PATCH_BATCH_BOUND_EXCEEDED")
                    writer.write_batch(_patch_batch(batch, changes, offset))
                    offset += batch.num_rows
                    changed += len(changes)
                    budget.check(reserve=batch.nbytes * 2)
                if offset != member.rows or member.verify() != before[n]:
                    raise BoundedError("FINANCIAL_UNION_SOURCE_OR_ROW_CENSUS_CHANGED")
                segments.append(
                    {
                        "member": n,
                        "kind": "native" if n < len(native) else "incoming",
                        "archived_source_path": str(member.path),
                        "source_sha256": member.sha256,
                        "rows": member.rows,
                        "original_column_summary": original,
                        "closed_versions": changed,
                    }
                )
                total += offset
    if pq.ParquetFile(partial).metadata.num_rows != total:
        raise BoundedError("FINANCIAL_UNION_OUTPUT_CENSUS_CHANGED")
    os.link(partial, output)  # atomic exclusive publication within this job only
    partial.unlink()
    result = {
        "contract_id": CONTRACT_ID,
        "table": table,
        "year": year,
        "configuration_sha256": digest({"schema": str(schema), "rule": rule}),
        "evaluator_sha256": file_sha(Path(__file__)),
        "segments": segments,
        "patches_sha256": file_sha(patches),
        "output_sha256": file_sha(output),
        "old_rows": sum(m.rows for m in native),
        "new_rows": sum(m.rows for m in incoming),
        "output_rows": total,
        "closed_versions": sum(s["closed_versions"] for s in segments),
        "allowed_mutated_fields": ["knowledge_to"],
        "READY": False,
        "consumer_admission": "NOT_GRANTED",
        "complete_Base22": False,
    }
    return {**result, "identity": digest(result)}


class _ColumnSummary:
    """Financial segment's streaming equivalent of the existing semantic_scan."""

    def __init__(self, schema: Any):
        self.schema, self.rows = schema, 0
        self.hashes = [hashlib.sha256() for _ in schema]

    def add(self, batch: Any) -> None:
        import numpy as np
        import pyarrow as pa
        import pyarrow.compute as pc  # type: ignore[import-untyped]

        if not batch.schema.equals(self.schema, check_metadata=False):
            raise BoundedError("FINANCIAL_UNION_RESTORED_SCHEMA_CHANGED")
        for i, column in enumerate(batch.columns):
            if pa.types.is_string(column.type):
                self.hashes[i].update(
                    b"".join(
                        (-1).to_bytes(8, "little", signed=True)
                        if value is None
                        else len(raw := value.encode("utf-8")).to_bytes(8, "little", signed=True)
                        + raw
                        for value in column.to_pylist()
                    )
                )
            else:
                valid = np.array(column.is_valid(), dtype=np.uint8)
                if pa.types.is_boolean(column.type):
                    normalized = pc.fill_null(column, False).cast(pa.uint8())
                else:
                    zero = pa.scalar(
                        Decimal(0) if pa.types.is_decimal(column.type) else 0, type=column.type
                    )
                    normalized = pc.fill_null(column, zero)
                width = normalized.type.bit_width // 8
                data = np.frombuffer(
                    normalized.buffers()[1],
                    dtype=np.uint8,
                    count=len(column) * width,
                    offset=normalized.offset * width,
                ).reshape(-1, width)
                framed = np.empty((len(column), width + 1), dtype=np.uint8)
                framed[:, 0], framed[:, 1:] = valid, data
                self.hashes[i].update(framed.tobytes())
        self.rows += batch.num_rows

    def finish(self) -> dict[str, Any]:
        columns = {
            f.name: "sha256:" + h.hexdigest() for f, h in zip(self.schema, self.hashes, strict=True)
        }
        return {
            "rows": self.rows,
            "schema_sha256": digest(str(self.schema.remove_metadata())),
            "columns_sha256": columns,
            "semantic_sha256": digest(columns),
        }


def audit_year(
    output: Path,
    patches: Path,
    receipt: dict[str, Any],
    *,
    schema: Any,
    rule: dict[str, Any],
    budget: Budget,
) -> dict[str, Any]:
    """Bounded cold restoration of every original column; no source roots opened."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    _validate_rule(receipt["table"], receipt["year"], rule)
    if (
        receipt.get("contract_id") != CONTRACT_ID
        or receipt.get("identity") != digest({k: v for k, v in receipt.items() if k != "identity"})
        or receipt.get("evaluator_sha256") != file_sha(Path(__file__))
        or receipt.get("configuration_sha256") != digest({"schema": str(schema), "rule": rule})
        or receipt.get("allowed_mutated_fields") != ["knowledge_to"]
        or receipt.get("READY") is not False
        or receipt.get("complete_Base22") is not False
        or receipt.get("consumer_admission") != "NOT_GRANTED"
    ):
        raise BoundedError("FINANCIAL_UNION_LOCAL_RECEIPT_INVALID")
    if (
        output.is_symlink()
        or patches.is_symlink()
        or output.stat().st_nlink != 1
        or patches.stat().st_nlink != 1
        or file_sha(output) != receipt["output_sha256"]
        or file_sha(patches) != receipt["patches_sha256"]
    ):
        raise BoundedError("FINANCIAL_UNION_LOCAL_MEMBER_CHANGED")
    f, p = pq.ParquetFile(output), pq.ParquetFile(patches)
    if (
        not f.schema_arrow.equals(schema)
        or not p.schema_arrow.equals(_patch_schema())
        or f.metadata.num_rows != receipt["output_rows"]
    ):
        raise BoundedError("FINANCIAL_UNION_LOCAL_SCHEMA_OR_CENSUS_CHANGED")
    segments = receipt["segments"]
    if (
        not segments
        or [s["member"] for s in segments] != list(range(len(segments)))
        or sum(s["rows"] for s in segments) != receipt["output_rows"]
        or any(
            type(s["rows"]) is not int
            or s["rows"] < 0
            or s["kind"] not in {"native", "incoming"}
            or type(s["closed_versions"]) is not int
            or not 0 <= s["closed_versions"] <= s["rows"]
            for s in segments
        )
        or sum(s["rows"] for s in segments if s["kind"] == "native") != receipt["old_rows"]
        or sum(s["rows"] for s in segments if s["kind"] == "incoming") != receipt["new_rows"]
        or sum(s["closed_versions"] for s in segments) != receipt["closed_versions"]
        or p.metadata.num_rows != receipt["closed_versions"]
    ):
        raise BoundedError("FINANCIAL_UNION_LOCAL_SEGMENT_SET_INVALID")
    with GlobalScan(budget, read_only_roots=sorted({output.parent, patches.parent})) as scan:
        scan.connection.read_parquet(
            str(output), hive_partitioning=False, file_row_number=True
        ).create_view("financial_output", replace=True)
        scan.connection.read_parquet(str(patches), hive_partitioning=False).create_view(
            "financial_patches", replace=True
        )
        bases, total = [], 0
        for s in segments:
            bases.append(total)
            total += s["rows"]
        scan.connection.register(
            "financial_segments",
            pa.table(
                {
                    "member": range(len(segments)),
                    "base": bases,
                    "rows": [s["rows"] for s in segments],
                }
            ),
        )
        scan.reject(
            "SELECT 1 FROM financial_patches p LEFT JOIN financial_segments s "
            "USING(member) WHERE s.member IS NULL OR p.row<0 OR p.row>=s.rows "
            "OR p.after_to IS NULL LIMIT 1",
            "FINANCIAL_UNION_PATCH_POSITION_INVALID",
        )
        scan.reject(
            "SELECT 1 FROM financial_patches GROUP BY member,row HAVING count(*)>1 LIMIT 1",
            "FINANCIAL_UNION_DUPLICATE_PATCH_POSITION",
        )
        keys = ",".join(identifier(k) for k in rule["business_key"])
        versions = ",".join(identifier(k) for k in rule["version_key"])
        nulls = " OR ".join(identifier(f.name) + " IS NULL" for f in schema if not f.nullable)
        scan.reject(
            f"SELECT 1 FROM financial_output WHERE {nulls} LIMIT 1",
            "FINANCIAL_UNION_COLD_REQUIRED_FIELD_NULL",
        )
        scan.reject(
            f"SELECT 1 FROM financial_output GROUP BY {versions} HAVING count(*)>1 LIMIT 1",
            "FINANCIAL_UNION_GLOBAL_DUPLICATE_VERSION",
        )
        scan.reject(
            f"SELECT 1 FROM financial_output WHERE year(period_end)<>{receipt['year']} "
            "OR period_end>DATE '2026-08-31' OR knowledge_to<=knowledge_from LIMIT 1",
            "FINANCIAL_UNION_COLD_DATE_OR_INTERVAL_INVALID",
        )
        scan.connection.execute(
            "CREATE TEMP TABLE financial_next AS "
            "SELECT file_row_number,knowledge_from,knowledge_to,"
            f"lead(knowledge_from) OVER(PARTITION BY {keys} ORDER BY knowledge_from) AS next_clock "
            "FROM financial_output"
        )
        scan.reject(
            "SELECT 1 FROM financial_next WHERE next_clock IS NOT NULL AND "
            "(knowledge_to IS NULL OR knowledge_to>next_clock) LIMIT 1",
            "FINANCIAL_UNION_COLD_OVERLAPPING_INTERVAL",
        )
        scan.reject(
            "SELECT 1 FROM financial_patches p JOIN financial_segments s USING(member) "
            "LEFT JOIN financial_next n ON n.file_row_number=s.base+p.row WHERE "
            "n.file_row_number IS NULL OR n.knowledge_to IS DISTINCT FROM p.after_to "
            "OR n.next_clock IS DISTINCT FROM p.after_to OR p.after_to<=n.knowledge_from "
            "OR (p.before_to IS NOT NULL AND p.before_to<=p.after_to) LIMIT 1",
            "FINANCIAL_UNION_COLD_PATCH_NOT_EXACT_NEXT_CLOCK",
        )
        batches = iter(f.iter_batches(batch_size=budget.limits.batch_rows))
        batch, pos = next(batches, None), 0
        for segment in segments:
            summary, offset, changed = _ColumnSummary(schema), 0, 0
            while offset < segment["rows"]:
                if batch is None:
                    raise BoundedError("FINANCIAL_UNION_COLD_ROW_MISSING")
                n = min(batch.num_rows - pos, segment["rows"] - offset)
                piece = batch.slice(pos, n)
                edits = scan.connection.execute(
                    "SELECT row,before_to,after_to FROM financial_patches "
                    "WHERE member=? AND row>=? AND row<? ORDER BY row",
                    [segment["member"], offset, offset + n],
                ).fetchall()
                ends = piece.column("knowledge_to").to_pylist()
                for row, before_to, after_to in edits:
                    if ends[row - offset] != after_to:
                        raise BoundedError("FINANCIAL_UNION_COLD_AFTER_CHANGED")
                    ends[row - offset] = before_to
                index = schema.get_field_index("knowledge_to")
                summary.add(
                    piece.set_column(
                        index, schema.field(index), pa.array(ends, type=schema.field(index).type)
                    )
                )
                offset, changed, pos = offset + n, changed + len(edits), pos + n
                if pos == batch.num_rows:
                    batch, pos = next(batches, None), 0
                budget.check()
            if (
                summary.finish() != segment["original_column_summary"]
                or changed != segment["closed_versions"]
            ):
                raise BoundedError("FINANCIAL_UNION_UNAPPROVED_ORIGINAL_ATOM_CHANGE")
        if batch is not None:
            raise BoundedError("FINANCIAL_UNION_COLD_EXTRA_ROWS")
    return {
        "status": "FINANCIAL_YEAR_ALLOWED_ENDS_AND_ALL_ORIGINAL_COLUMNS_PASS",
        "receipt_identity": receipt["identity"],
        "rows": total,
        "original_source_runtime_reads": 0,
        "complete_Base22": False,
    }

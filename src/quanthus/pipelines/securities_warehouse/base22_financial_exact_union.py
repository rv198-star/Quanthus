"""Narrow retained-raw financial equivalence and date-precision repair.

Legacy union v1 still rejects equal clocks. This separate contract requires local
original and incoming typed captures plus original/current raw byte equivalence.
It streams typed rows, sorts keys/ordinals only, and never publishes an image.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .base22 import digest, file_sha
from .base22_bounded import BoundedError, Budget, GlobalScan, Member, identifier
from .base22_financial_union import _ColumnSummary, _validate_rule
from .base22_outputs import instant
from .financial_reporting import (
    FinancialCompanyResolver,
    conservative_provider_timestamp,
    decode_financial_envelope,
)

CONTRACT_ID = "p10-base22-raw-equivalent-financial-precision-union-v2"
MAX_ENVELOPE = 64 * 1024**2
YEARS = {2024, 2025, 2026}


def _report_key(
    row: dict[str, Any], period: str, body: dict[str, Any], resolver: FinancialCompanyResolver
) -> tuple[Any, ...] | None:
    day = datetime.strptime(period, "%Y%m%d").date()
    if day.year not in YEARS or day > date(2026, 8, 31):
        return None
    company, _reason = resolver.resolve_for_report(row["stock_code"], period_end=day)
    if company is None:
        return None
    # Preserve the existing model's explicit parent availability floor. This is
    # not provider acquisition: both original raw clocks remain separately bound.
    available = max(instant(row["acquired_at"]), company.knowledge_from)
    return tuple(row[k] for k in ("stock_code", "statement_type", "source", "source_revision")) + (
        available,
        period,
        digest(body),
    )


def bridge_schema() -> Any:
    import pyarrow as pa  # type: ignore[import-untyped]

    return pa.schema(
        [
            ("source_artifact_sha256", pa.string()),
            ("report_record_id", pa.string()),
            ("record_source", pa.string()),
            ("source_revision", pa.string()),
            ("knowledge_from", pa.timestamp("us", tz="UTC")),
            ("period_end", pa.date32()),
            ("publication_raw_sha256", pa.string()),
            ("raw_payload_sha256", pa.string()),
            ("legacy_bound", pa.timestamp("us", tz="UTC")),
            ("date_only", pa.bool_()),
            ("current_artifact_sha256s", pa.list_(pa.string())),
            ("original_acquired_at", pa.timestamp("us", tz="UTC")),
            ("complete_report_body_sha256", pa.string()),
        ]
    )


def bridge_rows(
    originals: list[tuple[Member, str]],
    currents: list[Member],
    budget: Budget,
    resolver: FinancialCompanyResolver,
) -> Iterator[dict[str, Any]]:
    """Reconstruct precision proof from complete hash-verified local raw files.

    Date-only provider tokens never become genuine publication instants. Original
    provider, revision, complete report body and qualified parent availability
    floor must match a current capture before deduplication against a native
    version. Original acquisition clocks stay distinct and are never backfilled.
    """
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    current: dict[tuple[Any, ...], set[str]] = defaultdict(set)
    before = [m.verify() for m in currents] + [m.verify() for m, _ in originals]
    for m in currents:
        for batch in pq.ParquetFile(m.path).iter_batches(batch_size=2):
            for row in batch.to_pylist():
                envelope = decode_financial_envelope(row, max_raw_bytes=MAX_ENVELOPE)
                for period, body in sorted(envelope.report_list.items()):
                    if not isinstance(period, str) or not isinstance(body, dict):
                        raise BoundedError("FINANCIAL_EQUIVALENCE_RAW_PERIOD_INVALID")
                    key = _report_key(row, period, body, resolver)
                    if key is not None:
                        current[key].add(m.sha256)
                budget.check()
                if len(current) > 500000:
                    raise BoundedError("FINANCIAL_EQUIVALENCE_RAW_IDENTITY_BOUND")
    for m, relative in originals:
        if (
            not relative
            or "#" in relative
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
        ):
            raise BoundedError("FINANCIAL_EQUIVALENCE_RAW_ID_INVALID")
        for batch in pq.ParquetFile(m.path).iter_batches(batch_size=2):
            for row in batch.to_pylist():
                envelope = decode_financial_envelope(row, max_raw_bytes=MAX_ENVELOPE)
                for period, body in sorted(envelope.report_list.items()):
                    if not isinstance(period, str) or not isinstance(body, dict):
                        raise BoundedError("FINANCIAL_EQUIVALENCE_RAW_PERIOD_INVALID")
                    day = datetime.strptime(period, "%Y%m%d").date()
                    if day.year not in YEARS or day > date(2026, 8, 31):
                        continue
                    key = _report_key(row, period, body, resolver)
                    if key is None:
                        continue
                    value = body.get("publish_date")
                    only = isinstance(value, str) and bool(
                        re.fullmatch(r"\d{8}|\d{4}-\d{2}-\d{2}", value.strip())
                    )
                    yield {
                        "source_artifact_sha256": m.sha256,
                        "report_record_id": relative
                        + "#stock_code="
                        + row["stock_code"]
                        + "&statement_type="
                        + row["statement_type"]
                        + "&period_end="
                        + period,
                        "record_source": row["source"],
                        "source_revision": row["source_revision"],
                        "knowledge_from": key[4],
                        "period_end": day,
                        "publication_raw_sha256": digest(value),
                        "raw_payload_sha256": row["raw_payload_sha256"],
                        "legacy_bound": conservative_provider_timestamp(value),
                        "date_only": only,
                        "current_artifact_sha256s": sorted(current.get(key, set())),
                        "original_acquired_at": instant(row["acquired_at"]),
                        "complete_report_body_sha256": key[-1],
                    }
                budget.check()
    after = [m.verify() for m in currents] + [m.verify() for m, _ in originals]
    if after != before:
        raise BoundedError("FINANCIAL_EQUIVALENCE_RAW_CHANGED")


def verify_bridge(
    bridge: Member,
    originals: list[tuple[Member, str]],
    currents: list[Member],
    budget: Budget,
    resolver: FinancialCompanyResolver,
) -> None:
    """All proof rows rederived offline, without financial scalar exports."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    bridge.verify()
    pf = pq.ParquetFile(bridge.path)
    if not pf.schema_arrow.equals(bridge_schema()) or pf.metadata.num_rows != bridge.rows:
        raise BoundedError("FINANCIAL_EQUIVALENCE_BRIDGE_SCHEMA_CHANGED")
    actual = (r for b in pf.iter_batches(batch_size=1024) for r in b.to_pylist())
    count = 0
    for expected in bridge_rows(originals, currents, budget, resolver):
        # Cast to the persisted microsecond/date32 representation before comparison.
        expected = pa.Table.from_pylist([expected], schema=bridge_schema()).to_pylist()[0]
        if next(actual, None) != expected:
            raise BoundedError("FINANCIAL_EQUIVALENCE_BRIDGE_RAW_REPRODUCTION_FAILED")
        count += 1
    if count != bridge.rows or next(actual, None) is not None:
        raise BoundedError("FINANCIAL_EQUIVALENCE_BRIDGE_CENSUS_CHANGED")


def _qualified_map(
    scan: GlobalScan,
    members: list[Member],
    native_count: int,
    bridge: Member,
    schema: Any,
    rule: dict[str, Any],
    year: int,
) -> dict[str, int]:
    import pyarrow as pa

    con = scan.connection
    paths = [str(m.path) for m in members]
    con.read_parquet(
        paths, hive_partitioning=False, filename=True, file_row_number=True
    ).create_view("fin_input")
    con.register(
        "fin_members",
        pa.table(
            {
                "filename": paths,
                "member": range(len(paths)),
                "incoming": [i >= native_count for i in range(len(paths))],
            }
        ),
    )
    con.read_parquet(str(bridge.path), hive_partitioning=False).create_view("fin_bridge")
    con.execute(
        "CREATE VIEW fin_rows AS SELECT r.*,m.member,m.incoming FROM fin_input "
        "r JOIN fin_members m USING(filename)"
    )
    keys = ",".join(identifier(k) for k in rule["business_key"])
    versions = ",".join(identifier(k) for k in rule["version_key"])
    nulls = " OR ".join(identifier(f.name) + " IS NULL" for f in schema if not f.nullable)
    scan.reject(
        f"SELECT 1 FROM fin_rows WHERE {nulls} OR year(period_end)<>{year} OR "
        f"period_end>DATE '2026-08-31' OR knowledge_to<=knowledge_from LIMIT 1",
        "FINANCIAL_EQUIVALENCE_INPUT_INVALID",
    )
    scan.reject(
        f"SELECT 1 FROM fin_rows GROUP BY incoming,{versions} HAVING count(*)>1 LIMIT 1",
        "FINANCIAL_EQUIVALENCE_DUPLICATE_WITHIN_SOURCE",
    )
    equality = " AND ".join(
        "o." + identifier(k) + "=n." + identifier(k) for k in rule["version_key"]
    )
    con.execute(
        "CREATE TEMP TABLE fin_matches AS SELECT o.member AS "
        "old_member,n.member AS new_member,o.file_row_number AS "
        "old_row,n.file_row_number AS new_row FROM fin_rows o JOIN fin_rows n "
        "ON " + equality + " WHERE NOT o.incoming AND n.incoming"
    )
    con.execute(
        "CREATE VIEW fin_kept AS SELECT r.* FROM fin_rows r WHERE NOT "
        "EXISTS(SELECT 1 FROM fin_matches p WHERE p.new_member=r.member AND "
        "p.new_row=r.file_row_number)"
    )
    scan.reject(
        f"SELECT 1 FROM fin_kept GROUP BY {keys} HAVING count(DISTINCT record_source)>1 LIMIT 1",
        "FINANCIAL_EQUIVALENCE_CROSS_PROVIDER_PRIORITY_UNQUALIFIED",
    )
    suffix = "regexp_replace(o.source_record_id,'&item_index=[0-9]+$','')"
    bj = (
        "o.source_artifact_sha256=b.source_artifact_sha256 AND "
        + suffix
        + "=b.report_record_id AND o.knowledge_from=b.knowledge_from AND "
        "o.period_end=b.period_end AND o.record_source=b.record_source AND "
        "o.source_revision=b.source_revision"
    )
    excluded = {"source_record_id", "source_artifact_sha256", "knowledge_to", "published_at"}
    strict = " OR ".join(
        "o." + identifier(f.name) + " IS DISTINCT FROM n." + identifier(f.name)
        for f in schema
        if f.name not in excluded
    )
    pairs = con.execute(
        "SELECT DISTINCT old_member,new_member FROM fin_matches ORDER BY old_member,new_member"
    ).fetchall()
    for oi, ni in pairs:
        con.read_parquet(
            str(members[oi].path), hive_partitioning=False, file_row_number=True
        ).create_view("fin_old_pair", replace=True)
        con.read_parquet(
            str(members[ni].path), hive_partitioning=False, file_row_number=True
        ).create_view("fin_new_pair", replace=True)
        scan.reject(
            "SELECT 1 FROM fin_old_pair o JOIN fin_new_pair n ON "
            + equality
            + " LEFT JOIN fin_bridge b ON "
            + bj
            + " WHERE ("
            + strict
            + ") OR b.report_record_id IS NULL OR NOT "
            "list_contains(b.current_artifact_sha256s,n.source_artifact_sha256) OR "
            "split_part(o.source_record_id,'#',2)<>split_part(n.source_record_id,'#',2) "
            "OR (o.published_at IS DISTINCT FROM n.published_at AND "
            "NOT(coalesce(b.date_only,false) AND o.published_at=b.legacy_bound "
            "AND n.published_at IS NULL)) LIMIT 1",
            "FINANCIAL_EQUIVALENCE_NOT_EXACT_RETAINED_RAW_OR_ATOMS",
        )
        scan.budget.check()
    # Only original date-only clocks with exact original raw proof may be cleared.
    con.execute(
        "CREATE TEMP TABLE fin_precision AS SELECT o.member,o.file_row_number "
        "AS row FROM fin_kept o JOIN fin_bridge b ON "
        + bj
        + " WHERE NOT o.incoming AND b.date_only AND o.published_at=b.legacy_bound"
    )
    scan.reject(
        "SELECT 1 FROM fin_precision GROUP BY member,row HAVING count(*)>1 LIMIT 1",
        "FINANCIAL_EQUIVALENCE_AMBIGUOUS_PRECISION_PROOF",
    )
    con.execute(
        "CREATE TEMP TABLE fin_closures AS WITH clocks AS (SELECT "
        "member,file_row_number AS row,knowledge_to AS "
        "before_to,lead(knowledge_from) OVER(PARTITION BY "
        + keys
        + " ORDER BY knowledge_from) AS after_to FROM fin_kept) SELECT * FROM "
        "clocks WHERE after_to IS NOT NULL AND (before_to IS NULL OR "
        "after_to<before_to)"
    )
    scan.budget.check()

    def count(sql: str) -> int:
        result = con.execute(sql).fetchone()
        if result is None:
            raise BoundedError("FINANCIAL_EQUIVALENCE_CENSUS_UNAVAILABLE")
        return int(result[0])

    return {
        "duplicate_captures_archived": count("SELECT count(*) FROM fin_matches"),
        "publication_date_bounds_made_unavailable": count("SELECT count(*) FROM fin_precision"),
        "closed_versions": count("SELECT count(*) FROM fin_closures"),
    }


def _pieces(scan: GlobalScan, members: list[Member], schema: Any, budget: Budget) -> Iterator[Any]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    for i, member in enumerate(members):
        offset = 0
        for batch in pq.ParquetFile(member.path).iter_batches(batch_size=budget.limits.batch_rows):
            stop = offset + batch.num_rows
            dropped = scan.connection.execute(
                "SELECT new_row FROM fin_matches WHERE new_member=? AND new_row>=? AND new_row<?",
                [i, offset, stop],
            ).fetchall()
            precision = scan.connection.execute(
                "SELECT row FROM fin_precision WHERE member=? AND row>=? AND row<?",
                [i, offset, stop],
            ).fetchall()
            closure = scan.connection.execute(
                "SELECT row,before_to,after_to FROM fin_closures WHERE member=? AND "
                "row>=? AND row<?",
                [i, offset, stop],
            ).fetchall()
            ends, starts = (
                batch.column("knowledge_to").to_pylist(),
                batch.column("knowledge_from").to_pylist(),
            )
            for row, before, after in closure:
                p = row - offset
                if (
                    ends[p] != before
                    or after <= starts[p]
                    or before is not None
                    and after >= before
                ):
                    raise BoundedError("FINANCIAL_EQUIVALENCE_CLOSURE_INVALID")
                ends[p] = after
            pub = batch.column("published_at").to_pylist()
            for (row,) in precision:
                if pub[row - offset] is None:
                    raise BoundedError("FINANCIAL_EQUIVALENCE_PUBLICATION_BEFORE_CHANGED")
                pub[row - offset] = None
            for field, values in [("knowledge_to", ends), ("published_at", pub)]:
                index = schema.get_field_index(field)
                batch = batch.set_column(
                    index, schema.field(index), pa.array(values, type=schema.field(index).type)
                )
            if dropped:
                keep = [True] * batch.num_rows
                for (row,) in dropped:
                    keep[row - offset] = False
                batch = batch.filter(pa.array(keep))
            if batch.num_rows:
                yield batch
            offset = stop
            budget.check(reserve=batch.nbytes * 2)
        if offset != member.rows:
            raise BoundedError("FINANCIAL_EQUIVALENCE_INPUT_CENSUS_CHANGED")


def _inputs(native: list[Member], incoming: list[Member], schema: Any) -> list[dict[str, Any]]:
    import pyarrow.parquet as pq

    result = []
    for kind, selected in [("native", native), ("incoming", incoming)]:
        for m in selected:
            m.verify()
            pf = pq.ParquetFile(m.path)
            if pf.metadata.num_rows != m.rows or not pf.schema_arrow.equals(schema):
                raise BoundedError("FINANCIAL_EQUIVALENCE_INPUT_SCHEMA_CHANGED")
            result.append({"kind": kind, "sha256": m.sha256, "bytes": m.size_bytes, "rows": m.rows})
    if not native or not incoming or len({m.path for m in native + incoming}) != len(result):
        raise BoundedError("FINANCIAL_EQUIVALENCE_INPUT_SELECTION_INVALID")
    return result


def materialize_year(
    native: list[Member],
    incoming: list[Member],
    bridge: Member,
    *,
    table: str,
    year: int,
    schema: Any,
    rule: dict[str, Any],
    output: Path,
    budget: Budget,
    qualification_identity: str,
) -> dict[str, Any]:
    """Caller must first qualify/reproduce raw bridge; raw proof lives in image."""
    import pyarrow.parquet as pq

    _validate_rule(table, year, rule)
    output = budget.destination(output)
    partial = output.with_name(output.name + ".partial")
    if output.exists() or partial.exists():
        raise BoundedError("FINANCIAL_EQUIVALENCE_OUTPUT_EXISTS")
    inputs = _inputs(native, incoming, schema)
    bridge.verify()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", qualification_identity):
        raise BoundedError("FINANCIAL_EQUIVALENCE_QUALIFICATION_MISSING")
    summary = _ColumnSummary(schema)
    with GlobalScan(
        budget, read_only_roots=sorted({m.path.parent for m in native + incoming + [bridge]})
    ) as scan:
        counts = _qualified_map(scan, native + incoming, len(native), bridge, schema, rule, year)
        output.parent.mkdir(parents=True, exist_ok=True)
        with (
            partial.open("xb") as stream,
            pq.ParquetWriter(stream, schema, compression="zstd") as writer,
        ):
            for batch in _pieces(scan, native + incoming, schema, budget):
                writer.write_batch(batch)
                summary.add(batch)
    if inputs != _inputs(native, incoming, schema):
        raise BoundedError("FINANCIAL_EQUIVALENCE_SOURCE_CHANGED")
    # Exclusive same-job publication, no original-source links.
    import os

    os.link(partial, output)
    partial.unlink()
    result = {
        "contract_id": CONTRACT_ID,
        "table": table,
        "year": year,
        "inputs": inputs,
        "bridge_sha256": bridge.sha256,
        "raw_qualification_identity": qualification_identity,
        "evaluator_sha256": file_sha(Path(__file__)),
        "configuration_sha256": digest({"schema": str(schema), "rule": rule}),
        "output_sha256": file_sha(output),
        "output_summary": summary.finish(),
        **counts,
        "actual_governance_precision_repair": (
            "date-only remains unknown instant; original raw token/derived bound "
            "and clocks retained in local evidence"
        ),
        "allowed_mutated_fields": ["knowledge_to", "published_at"],
        "READY": False,
        "historical_PIT_admission": False,
        "consumer_admission": "NOT_GRANTED",
        "complete_Base22": False,
    }
    return {**result, "identity": digest(result)}


def audit_year(
    native: list[Member],
    incoming: list[Member],
    bridge: Member,
    output: Path,
    receipt: dict[str, Any],
    *,
    schema: Any,
    rule: dict[str, Any],
    budget: Budget,
) -> dict[str, Any]:
    """Whole bounded input/output atom comparison. All arguments image-local."""
    import pyarrow.parquet as pq

    _validate_rule(receipt["table"], receipt["year"], rule)
    if (
        receipt.get("identity") != digest({k: v for k, v in receipt.items() if k != "identity"})
        or receipt.get("contract_id") != CONTRACT_ID
        or receipt.get("evaluator_sha256") != file_sha(Path(__file__))
        or receipt.get("configuration_sha256") != digest({"schema": str(schema), "rule": rule})
        or receipt.get("allowed_mutated_fields") != ["knowledge_to", "published_at"]
        or receipt.get("READY") is not False
        or receipt.get("complete_Base22") is not False
        or receipt.get("historical_PIT_admission") is not False
        or receipt.get("consumer_admission") != "NOT_GRANTED"
    ):
        raise BoundedError("FINANCIAL_EQUIVALENCE_RECEIPT_INVALID")
    if (
        _inputs(native, incoming, schema) != receipt["inputs"]
        or bridge.verify() is None
        or bridge.sha256 != receipt["bridge_sha256"]
    ):
        raise BoundedError("FINANCIAL_EQUIVALENCE_LOCAL_INPUT_CHANGED")
    if (
        output.is_symlink()
        or output.stat().st_nlink != 1
        or file_sha(output) != receipt["output_sha256"]
    ):
        raise BoundedError("FINANCIAL_EQUIVALENCE_LOCAL_OUTPUT_CHANGED")
    pf = pq.ParquetFile(output)
    if not pf.schema_arrow.equals(schema):
        raise BoundedError("FINANCIAL_EQUIVALENCE_LOCAL_SCHEMA_CHANGED")
    expected, actual = _ColumnSummary(schema), _ColumnSummary(schema)
    with GlobalScan(
        budget, read_only_roots=sorted({m.path.parent for m in native + incoming + [bridge]})
    ) as scan:
        counts = _qualified_map(
            scan, native + incoming, len(native), bridge, schema, rule, receipt["year"]
        )
        for batch in _pieces(scan, native + incoming, schema, budget):
            expected.add(batch)
    for batch in pf.iter_batches(batch_size=budget.limits.batch_rows):
        actual.add(batch)
        budget.check()
    if (
        any(counts[k] != receipt[k] for k in counts)
        or expected.finish() != actual.finish()
        or actual.finish() != receipt["output_summary"]
    ):
        raise BoundedError("FINANCIAL_EQUIVALENCE_UNAPPROVED_ATOM_CHANGE")
    return {
        "status": "FINANCIAL_TYPED_ATOMS_PASS_REQUIRES_REDERIVED_RAW_BRIDGE",
        "receipt_identity": receipt["identity"],
        "rows": actual.rows,
        "original_source_runtime_reads": 0,
        "complete_Base22": False,
    }

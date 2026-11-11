"""Integrate qualified financial observations while retaining sealed history.

Reports acquire real successor knowledge versions. Concepts reuse a covering
native version or add a source-grounded earlier scope; nonextending captures stay
in evidence. Never rerun the cumulative prefix reducer over sealed concepts:
legacy nonextending versions are still part of the immutable historical record.
This module owns a financial TMP, not complete-image or consumer acceptance.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError, GlobalScan, identifier
from .base22_outputs import financial_concept_versions
from .base231_domain_pack import schemas

SEMANTICS = (
    "taxonomy_namespace",
    "taxonomy_version",
    "concept_code",
    "concept_name",
    "statement_type",
    "data_type",
    "period_type",
    "balance_type",
)


def integrate_concepts(
    native: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Retain every old atom, including finite ends and same-clock scope variants."""
    if not native or not incoming or len(native) + len(incoming) > 32768:
        raise BoundedError("FINANCIAL_INTEGRATION_CONCEPT_BOUND")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in [*native, *incoming]:
        grouped.setdefault(row["financial_concept_id"], []).append(row)
    if any(
        len({digest([r[k] for k in SEMANTICS]) for r in rows}) != 1 for rows in grouped.values()
    ):
        raise BoundedError("FINANCIAL_INTEGRATION_CONCEPT_SEMANTICS_CONFLICT")
    result = [dict(r) for r in native]
    selected = financial_concept_versions(incoming)
    added = reused = 0
    for row in selected:
        clock, end = row["knowledge_from"], row["knowledge_to"]
        parents = [r for r in result if r["financial_concept_id"] == row["financial_concept_id"]]
        covered = any(
            p["knowledge_from"] <= clock
            and (p["knowledge_to"] is None or end is not None and p["knowledge_to"] >= end)
            and (p["knowledge_to"] is None or p["knowledge_to"] > clock)
            and p["effective_from"] <= row["effective_from"]
            and (
                p["effective_to"] is None
                or row["effective_to"] is not None
                and p["effective_to"] >= row["effective_to"]
            )
            for p in parents
        )
        if covered:
            reused += 1
        else:
            result.append(dict(row))
            added += 1
    keys = ["financial_concept_id", "taxonomy_version", "effective_from", "knowledge_from"]
    if len({digest([r[k] for k in keys]) for r in result}) != len(result):
        raise BoundedError("FINANCIAL_INTEGRATION_CONCEPT_VERSION_CONFLICT")
    result.sort(key=lambda r: tuple(r[k] for k in keys))
    old_hashes = sorted(digest(r) for r in native)
    if not set(old_hashes) <= {digest(r) for r in result}:
        raise BoundedError("FINANCIAL_INTEGRATION_OLD_CONCEPT_CHANGED")
    return result, dict(
        native_rows=len(native),
        incoming_observations=len(incoming),
        reduced_incoming_versions=len(selected),
        covering_native_versions_reused=reused,
        added_versions=added,
        output_rows=len(result),
        old_atoms_unchanged=True,
        native_census_sha256=digest(old_hashes),
        incoming_census_sha256=digest(sorted(digest(r) for r in incoming)),
        output_census_sha256=digest(sorted(digest(r) for r in result)),
    )


def report_revision_views(scan: GlobalScan) -> dict[str, int]:
    """Close only open report intervals at a real later same-provider observation.

    Caller supplies hash-qualified ``old_reports`` and ``incoming_reports``.
    Source qualification precedes this helper; a matching provider name alone is
    insufficient authority. Finite historical interval ends remain immutable.
    """
    c = scan.connection
    key = "financial_report_id,period_end"
    for table in ("old_reports", "incoming_reports"):
        scan.reject(
            f"SELECT 1 FROM {table} GROUP BY {key},knowledge_from HAVING count(*)>1 LIMIT 1",
            "FINANCIAL_INTEGRATION_REPORT_DUPLICATE",
        )
    join = "o.financial_report_id=n.financial_report_id AND o.period_end=n.period_end"
    scan.reject(
        "SELECT 1 FROM old_reports o JOIN incoming_reports n ON "
        + join
        + " WHERE o.record_source<>n.record_source LIMIT 1",
        "FINANCIAL_INTEGRATION_CROSS_PROVIDER_PRIORITY_HELD",
    )
    scan.reject(
        "SELECT 1 FROM incoming_reports n WHERE EXISTS(SELECT 1 FROM old_reports o "
        "WHERE " + join + " AND o.knowledge_from>=n.knowledge_from) LIMIT 1",
        "FINANCIAL_INTEGRATION_REPORT_NOT_LATER_KNOWLEDGE",
    )
    c.execute(
        "CREATE TEMP VIEW report_clocks AS SELECT *,lead(knowledge_from) OVER "
        "(PARTITION BY " + key + " ORDER BY knowledge_from) AS next_clock FROM "
        "(SELECT *,TRUE AS is_old FROM old_reports UNION ALL "
        "SELECT *,FALSE AS is_old FROM incoming_reports)"
    )
    scan.reject(
        "SELECT 1 FROM report_clocks WHERE next_clock IS NOT NULL "
        "AND knowledge_to>next_clock LIMIT 1",
        "FINANCIAL_INTEGRATION_FINITE_REPORT_END_CONFLICT",
    )
    fields = schemas()["dwd_financial_report"].names
    columns = ",".join(
        "CASE WHEN knowledge_to IS NULL AND next_clock IS NOT NULL "
        "THEN next_clock ELSE knowledge_to END AS knowledge_to"
        if f == "knowledge_to"
        else identifier(f)
        for f in fields
    )
    c.execute("CREATE TEMP VIEW integrated_reports AS SELECT " + columns + " FROM report_clocks")
    c.execute(
        "CREATE TEMP VIEW changed_old_report_intervals AS SELECT financial_report_id,"
        "period_end,knowledge_from,knowledge_to AS before_to,next_clock AS after_to "
        "FROM report_clocks WHERE is_old AND knowledge_to IS NULL "
        "AND next_clock IS NOT NULL"
    )
    result = {}
    for name in (
        "old_reports",
        "incoming_reports",
        "integrated_reports",
        "changed_old_report_intervals",
    ):
        count = c.execute("SELECT count(*) FROM " + name).fetchone()
        assert count is not None
        result[name] = int(count[0])
    return result


def native_batches(
    scan: GlobalScan,
    view: str,
    schema: Any,
    order_by: list[str],
) -> Iterator[Any]:
    """Bounded original typed columns; no full-row JSON/SQLite materialization."""
    import pyarrow as pa  # type: ignore[import-untyped]

    if not order_by or not set(order_by) <= set(schema.names):
        raise BoundedError("FINANCIAL_INTEGRATION_ORDER_REQUIRED")
    query = (
        "SELECT "
        + ",".join(identifier(f) for f in schema.names)
        + " FROM "
        + identifier(view)
        + " ORDER BY "
        + ",".join(identifier(f) for f in order_by)
    )
    reader = scan.connection.execute(query).to_arrow_reader(scan.budget.limits.batch_rows)
    for batch in reader:
        scan.budget.check(reserve=batch.nbytes * 2)
        typed = pa.Table.from_batches([batch]).cast(schema, safe=True)
        if any(not f.nullable and typed[f.name].null_count for f in schema):
            raise BoundedError("FINANCIAL_INTEGRATION_REQUIRED_FIELD_UNKNOWN")
        yield from typed.to_batches()


def financial_references(scan: GlobalScan) -> None:
    """Full new-fact reference closure, with Shanghai business date and real clocks.

    Financial origins belong only to the explicit financial issuer scope. This
    does not extend security/listing or admit an ownership or trading consumer.
    """
    c = scan.connection
    for parent, key in [
        ("reports", "financial_report_id"),
        ("concepts", "financial_concept_id"),
        ("companies", "company_id"),
    ]:
        fields = [
            f
            for f in c.execute("DESCRIBE " + parent).fetchall()
            if f[0]
            in {"knowledge_from", "knowledge_to", "effective_from", "effective_to", "period_end"}
        ]
        record = ",".join(identifier(f[0]) + ":=" + identifier(f[0]) for f in fields)
        c.execute(
            "CREATE TEMP TABLE reference_"
            + parent
            + " AS SELECT "
            + key
            + " AS parent_key,list(struct_pack("
            + record
            + ")) AS versions FROM "
            + parent
            + " GROUP BY "
            + key
        )
        scan.budget.check()
    for child, key, parent, effective in [
        ("reports", "company_id", "companies", True),
        ("facts", "financial_report_id", "reports", False),
        ("facts", "financial_concept_id", "concepts", True),
    ]:
        temporal = (
            "p.knowledge_from<=r.knowledge_from AND "
            "(p.knowledge_to IS NULL OR r.knowledge_from<p.knowledge_to)"
        )
        if effective:
            day = "timezone('Asia/Shanghai',CAST(r.period_end AS TIMESTAMP))"
            temporal += (
                " AND p.effective_from<="
                + day
                + " AND (p.effective_to IS NULL OR "
                + day
                + "<p.effective_to)"
            )
        if parent == "reports":
            temporal += " AND p.period_end=r.period_end"
        scan.reject(
            "SELECT 1 FROM "
            + child
            + " r LEFT JOIN reference_"
            + parent
            + " t ON t.parent_key=r."
            + key
            + " WHERE t.parent_key IS NULL OR "
            "length(list_filter(t.versions,p -> " + temporal + "))=0 LIMIT 1",
            "FINANCIAL_INTEGRATION_TEMPORAL_REFERENCE_FAILED:" + child + ":" + parent,
        )

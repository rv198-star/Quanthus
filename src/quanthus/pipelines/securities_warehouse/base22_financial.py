"""P10 exact statement-envelope projection, bounded by one verified envelope.

Reports, concepts and facts share a single decoded raw dependency. This prepares
real typed observations; it cannot grant provider/PIT or complete-image admission.
Legacy financial construction retains its original public entry and semantics.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .base22 import THROUGH, digest, partition
from .base22_bounded import BoundedError, GlobalScan
from .base22_outputs import financial_concept_versions, instant, write_partition
from .base22_precision import decimal_profile
from .financial_reporting import (
    FinancialCompanyResolver,
    FinancialStatementSource,
    _expand_decoded_financial_envelope,
    decode_financial_envelope,
    financial_reporting_schemas,
    iter_statement_reports,
)

CONTRACT_ID = "p10-base22-statement-envelope-exact-v1"
MAX_RAW_BYTES = 1048576
MAX_FACTS = 4096
TABLES = ("dwd_financial_report", "dwd_financial_concept", "dwd_financial_fact")


def _clock(value: Any) -> datetime:
    # timestamp[us] must not silently truncate a real source instant.
    if isinstance(value, str):
        fraction = re.search(r"T\d\d:\d\d:\d\d[.,](\d+)", value)
        if fraction and any(c != "0" for c in fraction[1][6:]):
            raise BoundedError("FINANCIAL_CLOCK_PRECISION_UNSUPPORTED")
    return instant(value)


def statement_observation(
    raw: dict[str, Any],
    *,
    source: FinancialStatementSource,
    resolver: FinancialCompanyResolver,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Keep raw numerical tokens and actual source clocks, with safe evidence.

    Unsupported finite/non-numeric scalars use the native value_text field, never
    quantization or fabricated nulls. Only genuine null/blank tokens are nil. A
    date-only publication stays unavailable. The source acquisition is a retained
    observation, not an assertion of historical provider availability.
    """
    import pyarrow as pa  # type: ignore[import-untyped]

    clock = _clock(raw.get("acquired_at"))
    size = raw.get("raw_payload_bytes")
    if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_RAW_BYTES:
        raise BoundedError("FINANCIAL_ENVELOPE_BYTE_BOUND_FAILED")
    envelope = decode_financial_envelope(raw, max_raw_bytes=MAX_RAW_BYTES)
    periods = list(iter_statement_reports(envelope))
    if type(raw.get("period_count")) is not int or raw["period_count"] != len(periods):
        raise BoundedError("FINANCIAL_PERIOD_CENSUS_FAILED")
    if sum(len(body["data"]) for _, body, _ in periods) > MAX_FACTS:
        raise BoundedError("FINANCIAL_ENVELOPE_ITEM_BOUND_FAILED")
    for _, body, _ in periods:
        for item in body["data"]:
            if item.get("item_display_type") != 1 and not (
                item.get("item_value") is None or isinstance(item["item_value"], str)
            ):
                raise BoundedError("FINANCIAL_ORIGINAL_SCALAR_TOKEN_UNAVAILABLE")
    expanded = _expand_decoded_financial_envelope(
        raw, envelope=envelope, source=source, company_resolver=resolver, through_date=THROUGH
    )
    rows = dict(
        zip(
            TABLES,
            [list(expanded.reports), list(expanded.concepts), list(expanded.facts)],
            strict=True,
        )
    )
    classifications: Counter[str] = Counter()
    for table in (TABLES[0], TABLES[2]):
        for row in rows[table]:
            body = envelope.report_list[row["period_end"].strftime("%Y%m%d")]
            publication = body.get("publish_date")
            try:
                published = _clock(publication)
            except BoundedError:
                raise
            except RuntimeError:
                published = None
            if published is not None and published > clock:
                raise BoundedError("FINANCIAL_PUBLICATION_AFTER_ACQUISITION")
            row["published_at"] = published
            if table != TABLES[2]:
                continue
            item_index = int(row["source_record_id"].rsplit("&item_index=", 1)[1])
            token = body["data"][item_index].get("item_value")
            profile = decimal_profile(token, precision=38, scale=10)
            classifications[profile["classification"]] += 1
            row["value_decimal"] = Decimal(token) if profile["fits"] else None
            row["value_text"] = (
                token if not profile["fits"] and profile["classification"] != "NULL" else None
            )
            row["is_nil"] = profile["classification"] == "NULL"
    physical = financial_reporting_schemas()
    for table in TABLES:
        restored = pa.Table.from_pylist(rows[table], schema=physical[table]).to_pylist()
        if restored != rows[table]:
            raise BoundedError("FINANCIAL_TYPED_VALUE_CHANGED")
        rows[table] = restored
    evidence = {
        "contract_id": CONTRACT_ID,
        "raw_payload_sha256": raw["raw_payload_sha256"],
        "raw_payload_bytes": size,
        "raw_acquired_at": raw["acquired_at"],
        "actual_acquisition_instant": clock,
        "source_member_sha256": source.source_sha256,
        "row_counts": {t: len(rows[t]) for t in TABLES},
        "typed_rows_sha256": {t: digest(rows[t]) for t in TABLES},
        "scalar_classifications": dict(sorted(classifications.items())),
        "quality_exceptions": list(expanded.quality_exceptions),
        "provider_acquisition_qualified": False,
        "historical_acceptance_asserted": False,
        "decoded_dependencies_built": 1,
    }
    return rows, evidence


def verify_statement_observation(
    raw: dict[str, Any],
    *,
    source: FinancialStatementSource,
    resolver: FinancialCompanyResolver,
    rows: dict[str, list[dict[str, Any]]],
    evidence: dict[str, Any],
) -> None:
    """Reproduce a bounded scope from its local raw/identity archive, offline.

    The caller checks the physical member/hash census. No original source path is
    opened: source.path is archival provenance, and the raw argument is local.
    Native union, global versions and provider admission remain separate gates.
    """
    expected_rows, expected_evidence = statement_observation(raw, source=source, resolver=resolver)
    if rows != expected_rows or evidence != expected_evidence:
        raise BoundedError("FINANCIAL_RAW_TO_TYPED_OUTPUT_MISMATCH")


def statement_batch(
    envelopes: Iterable[tuple[dict[str, Any], FinancialStatementSource]],
    *,
    resolver: FinancialCompanyResolver,
    rules: dict[str, dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Merge one bounded observation group; preserve all input revision evidence.

    Identical bodies do not gain new knowledge versions merely because they were
    retrieved again. Changed bodies retain actual new clocks; equal-clock body
    conflicts fail. Cross-batch/native union remains the complete image's owner.
    """
    combined: dict[str, list[dict[str, Any]]] = {table: [] for table in TABLES}
    evidence: list[dict[str, Any]] = []
    for raw, source in envelopes:
        if len(evidence) >= 64:
            raise BoundedError("FINANCIAL_BATCH_ENVELOPE_BOUND_FAILED")
        rows, item = statement_observation(raw, source=source, resolver=resolver)
        for table in TABLES:
            if len(combined[table]) + len(rows[table]) > 32768:
                raise BoundedError("FINANCIAL_BATCH_ROW_BOUND_FAILED")
            combined[table].extend(rows[table])
        evidence.append(item)
    for table in (TABLES[0], TABLES[2]):
        combined[table] = _statement_versions(combined[table], rules[table])
    combined[TABLES[1]] = financial_concept_versions(combined[TABLES[1]])
    return combined, {
        "contract_id": CONTRACT_ID,
        "envelope_evidence": evidence,
        "row_counts": {t: len(combined[t]) for t in TABLES},
        "typed_rows_sha256": {t: digest(combined[t]) for t in TABLES},
        "source_observations_retained": len(evidence),
        "complete_native_union": "NOT_QUALIFIED",
    }


def _statement_versions(rows: list[dict[str, Any]], rule: dict[str, Any]) -> list[dict[str, Any]]:
    """Same governed revisions over normalized Arrow financial scalars.

    Canonical hashes still own deterministic key order and equal-clock provenance
    selection. Typed body equality avoids serializing/hashing an identical wide
    row repeatedly for each weekly observation. Every caller first normalizes
    values to the same pinned Arrow schema; this is not a general untyped merge.
    """
    if not rows:
        return []
    excluded = {
        "record_source",
        "source_record_id",
        "source_revision",
        "source_artifact_sha256",
        "knowledge_from",
        "knowledge_to",
    }
    fields = sorted(set(rows[0]) - excluded)
    groups: dict[tuple[Any, ...], dict[datetime, tuple[tuple[Any, ...], dict[str, Any]]]] = {}
    for row in rows:
        key = tuple(row[k] for k in rule["business_key"])
        clock = row["knowledge_from"]
        body = tuple(row[f] for f in fields)
        group = groups.setdefault(key, {})
        if clock in group:
            before_body, before = group[clock]
            if before_body != body:
                raise BoundedError("OBSERVATION_EQUAL_CLOCK_BODY_CONFLICT")
            row = min((before, row), key=digest)
        group[clock] = body, row
    result = []
    for key in sorted(groups, key=lambda k: digest(list(k))):
        versions = []
        previous: tuple[Any, ...] | None = None
        for clock in sorted(groups[key]):
            body, row = groups[key][clock]
            if previous == body:
                continue
            versions.append(dict(row))
            previous = body
        for index, row in enumerate(versions):
            row["knowledge_to"] = (
                versions[index + 1]["knowledge_from"] if index + 1 < len(versions) else None
            )
            result.append(row)
    return result


def merge_concept_files(
    scan: GlobalScan,
    files: list[Path],
    *,
    schema: Any,
    rule: dict[str, Any],
    admitted: set[tuple[str, str, str]],
    output: Path,
) -> dict[str, Any]:
    """The governed cumulative concept rule across every bounded input file.

    Typed concept evidence is a single job-local prerequisite. DuckDB performs
    the global ordering under the existing memory/spill budget; Python keeps one
    Arrow batch rather than all envelopes/concepts. No native history is changed.
    """
    scan.add("statement_concept_observations", files)
    semantic = (
        "taxonomy_namespace,taxonomy_version,concept_code,concept_name,"
        "statement_type,data_type,period_type,balance_type"
    )
    scan.reject(
        "SELECT 1 FROM statement_concept_observations GROUP BY financial_concept_id "
        f"HAVING count(DISTINCT ROW({semantic}))<>1 LIMIT 1",
        "FINANCIAL_CONCEPT_SOURCE_SEMANTICS_CONFLICT",
    )
    # Materialize this compact global prerequisite once. Counting and the writer
    # consume it; they must not repeat the cross-file sort/merge.
    scan.connection.execute(
        """CREATE TEMP TABLE statement_concepts_merged AS
        WITH at_clock AS (
          SELECT *, row_number() OVER (
            PARTITION BY financial_concept_id,knowledge_from
            ORDER BY effective_from,source_record_id,source_artifact_sha256,
                     record_source,source_revision) AS rn
          FROM statement_concept_observations
        ), cumulative AS (
          SELECT * EXCLUDE(rn), min(effective_from) OVER (
            PARTITION BY financial_concept_id ORDER BY knowledge_from
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prior_effective
          FROM at_clock WHERE rn=1
        ), revisions AS (
          SELECT * EXCLUDE(prior_effective) FROM cumulative
          WHERE prior_effective IS NULL OR effective_from<prior_effective
        ) SELECT * EXCLUDE(knowledge_to), lead(knowledge_from) OVER (
            PARTITION BY financial_concept_id ORDER BY knowledge_from) AS knowledge_to
          FROM revisions"""
    )
    scan.budget.check()
    census = scan.connection.execute("SELECT count(*) FROM statement_concepts_merged").fetchone()
    if census is None:
        raise BoundedError("FINANCIAL_GLOBAL_CONCEPT_CENSUS_UNAVAILABLE")
    count = int(census[0])
    if count == 0 or count > 32768:
        raise BoundedError("FINANCIAL_GLOBAL_CONCEPT_SCOPE_BOUND_FAILED")
    first = scan.connection.sql("SELECT * FROM statement_concepts_merged LIMIT 1").to_arrow_table()
    part = partition(first.to_pylist()[0], rule)
    reader = scan.connection.sql(
        "SELECT * FROM statement_concepts_merged ORDER BY financial_concept_id,knowledge_from"
    ).to_arrow_reader(scan.budget.limits.batch_rows)

    def rows() -> Iterable[dict[str, Any]]:
        for batch in reader:
            yield from batch.to_pylist()
            scan.budget.check()

    return write_partition(
        rows(),
        table=TABLES[1],
        expected_rows=count,
        expected_partition=part,
        schema=schema,
        rule=rule,
        admitted=admitted,
        output=output,
        budget=scan.budget,
    )

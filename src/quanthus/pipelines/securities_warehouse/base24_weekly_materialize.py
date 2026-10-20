"""Bounded three-table weekly financial materialization; no published product pointer.

One exact envelope supplies all three consumers. Healthy Base identities are
provided once by weekly_parent. Output is a job-local TMP until separate local
closure, independent cold reproduction and consumer-specific acceptance exist.
"""

from __future__ import annotations

import importlib
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .base22 import file_sha
from .base22_bounded import BoundedError, Budget, GlobalScan
from .base22_financial import TABLES
from .base22_outputs import financial_concept_versions
from .base22_sources import _fingerprint, physical_path
from .base23_native_image import typed_summary
from .base24_weekly_financial import statement_observation
from .financial_reporting import (
    FinancialCompanyResolver,
    FinancialStatementSource,
    financial_reporting_schemas,
)


def materialize_statements(
    *,
    source: Path,
    receipts: Path,
    source_sha256: str,
    receipt_sha256: str,
    expected_envelopes: int,
    resolver: FinancialCompanyResolver,
    observed_at: datetime,
    through: date,
    output: Path,
    budget: Budget,
) -> dict[str, Any]:
    """Emit physical typed atoms and check global keys/references under one budget.

    The caller freezes exact source/code/config/Base identities before entering
    Budget. This function never writes READY or invents missing source domains.
    Failed materialization remains in the caller's failed job, never overwritten.
    """
    pa = importlib.import_module("pyarrow")
    pq = importlib.import_module("pyarrow.parquet")
    source, receipts = physical_path(source), physical_path(receipts)
    if type(expected_envelopes) is not int or not 0 < expected_envelopes <= 100000:
        raise BoundedError("WEEKLY_ENVELOPE_BOUND_INVALID")
    snapshots = {p: _fingerprint(p) for p in (source, receipts)}
    if file_sha(source) != source_sha256 or file_sha(receipts) != receipt_sha256:
        raise BoundedError("WEEKLY_FROZEN_INPUT_DIFFERS")
    pf = pq.ParquetFile(source)
    if pf.metadata.num_rows != expected_envelopes:
        raise BoundedError("WEEKLY_DECLARED_ENVELOPE_CENSUS_DIFFERS")
    receipt_file = pq.ParquetFile(receipts)
    if receipt_file.metadata.num_rows != expected_envelopes:
        raise BoundedError("WEEKLY_RECEIPT_CENSUS_DIFFERS")
    wanted = {}
    for receipt in receipt_file.read().to_pylist():
        request = json.loads(receipt["request_json"])
        key = (receipt["security_id"], request["params"]["source"])
        if key in wanted or receipt["dataset"] != "financial_statements":
            raise BoundedError("WEEKLY_RECEIPT_MATRIX_INVALID")
        wanted[key] = receipt
    if len(wanted) != expected_envelopes:
        raise BoundedError("WEEKLY_RECEIPT_CENSUS_DIFFERS")
    by_code: dict[str, set[str]] = {}
    for code, provider in wanted:
        by_code.setdefault(code, set()).add(provider)
    if any(providers != {"fzb", "lrb", "llb"} for providers in by_code.values()):
        raise BoundedError("WEEKLY_THREE_STATEMENT_MATRIX_MISSING")
    output = budget.destination(output)
    if output.exists():
        raise BoundedError("WEEKLY_OUTPUT_EXISTS_PRESERVED")
    output.mkdir()
    schemas = financial_reporting_schemas()
    writers: dict[str, Any] = {}
    pending: dict[str, list[dict[str, Any]]] = {t: [] for t in TABLES}
    written: Counter[str] = Counter()
    seen = set()
    witnesses: dict[str, dict[str, Any]] = {}
    classifications: Counter[str] = Counter()
    exceptions: Counter[str] = Counter()
    semantic_fields = (
        "taxonomy_namespace",
        "taxonomy_version",
        "concept_code",
        "concept_name",
        "statement_type",
        "data_type",
        "period_type",
        "balance_type",
    )

    def flush(table: str) -> None:
        if pending[table]:
            budget.check()
            typed = pa.Table.from_pylist(pending[table], schema=schemas[table])
            writers[table].write_table(typed, row_group_size=8192)
            written[table] += len(pending[table])
            pending[table].clear()

    def add(table: str, values: list[dict[str, Any]]) -> None:
        for row in values:
            pending[table].append(row)
            if len(pending[table]) == 8192:
                flush(table)

    def witness_order(row: dict[str, Any]) -> tuple[Any, ...]:
        return (
            row["effective_from"],
            row["source_record_id"],
            row["source_artifact_sha256"],
            row["record_source"],
            row["source_revision"],
        )

    try:
        for t in TABLES:
            writers[t] = pq.ParquetWriter(output / (t + ".parquet"), schemas[t], compression="zstd")
        for batch in pf.iter_batches(batch_size=16, use_threads=False):
            budget.check()
            for raw in batch.to_pylist():
                key = (raw["security_id"], raw["provider_source"])
                if key in seen or key not in wanted:
                    raise BoundedError("WEEKLY_RAW_RECEIPT_MATRIX_DIFFERS")
                seen.add(key)
                values, proof = statement_observation(
                    raw,
                    wanted[key],
                    source=FinancialStatementSource(
                        label="original_weekly",
                        path=source,
                        relative_path="sources/financial_statements.parquet",
                        source_sha256=source_sha256,
                        source_revision=raw["source_revision"],
                        precedence="hotfix",
                    ),
                    resolver=resolver,
                    observed_at=observed_at,
                    through=through,
                )
                classifications.update(proof["scalar_classifications"])
                exceptions.update(item["code"] for item in proof["quality_exceptions"])
                for t in [TABLES[0], TABLES[2]]:
                    add(t, values[t])
                for row in values[TABLES[1]]:
                    concept_id = row["financial_concept_id"]
                    old = witnesses.get(concept_id)
                    if old and any(row[field] != old[field] for field in semantic_fields):
                        raise BoundedError("WEEKLY_CONCEPT_SEMANTICS_CONFLICT")
                    if old is None or witness_order(row) < witness_order(old):
                        witnesses[concept_id] = row
                    if len(witnesses) > 100000:
                        raise BoundedError("WEEKLY_CONCEPT_WITNESS_BOUND")
        if seen != set(wanted):
            raise BoundedError("WEEKLY_RAW_COMPLETE_CENSUS_DIFFERS")
        add(TABLES[1], financial_concept_versions(witnesses.values()))
        for t in TABLES:
            flush(t)
    finally:
        for writer in writers.values():
            writer.close()
    if any(_fingerprint(p) != fp for p, fp in snapshots.items()):
        raise BoundedError("WEEKLY_INPUT_CHANGED_DURING_PROJECTION")
    summaries = {
        t: typed_summary([output / (t + ".parquet")], schemas[t], check=budget.check)
        for t in TABLES
    }
    if any(summaries[t]["rows"] != written[t] for t in TABLES):
        raise BoundedError("WEEKLY_PHYSICAL_ROW_CENSUS_DIFFERS")
    with GlobalScan(budget) as scan:
        for t in TABLES:
            scan.add(t, [output / (t + ".parquet")])
            primary = {
                "dwd_financial_report": "financial_report_id",
                "dwd_financial_concept": "financial_concept_id",
                "dwd_financial_fact": "financial_fact_id",
            }[t]
            scan.keys(t, [primary], [primary, "knowledge_from"])
        scan.structural_reference(
            TABLES[2], "financial_report_id", TABLES[0], "financial_report_id"
        )
        scan.structural_reference(
            TABLES[2], "financial_concept_id", TABLES[1], "financial_concept_id"
        )
    return {
        "status": "MATERIALIZED_FINANCIAL_TMP_NOT_COMPLETE_WEEKLY_OR_CONSUMER_ADMISSION",
        "source_envelopes": len(seen),
        "source_securities": len(by_code),
        "row_counts": {t: written[t] for t in TABLES},
        "summaries": summaries,
        "source_sha256": source_sha256,
        "receipt_sha256": receipt_sha256,
        "governance_observed_at": observed_at.isoformat(),
        "business_through": through.isoformat(),
        "scalar_classifications": dict(sorted(classifications.items())),
        "quality_exception_counts": dict(sorted(exceptions.items())),
        "global_keys_and_financial_references": "PASS",
        "source_fingerprints_unchanged": True,
        "provider_acquisition_inferred": False,
        "historical_PIT_admission": False,
        "consumer_admission": "NOT_GRANTED",
    }

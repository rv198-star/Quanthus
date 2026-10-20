"""One bounded, source-grounded weekly financial observation for Base 2.4.

This adapter consumes one verified original envelope once for all three tables.
It grants neither complete weekly coverage nor historical PIT/consumer admission.
Old Base22/23 adapters and the legacy decoder's default numeric mode stay intact.
"""

from __future__ import annotations

import importlib
import json
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_financial import MAX_FACTS, MAX_RAW_BYTES, TABLES, _clock
from .base22_precision import decimal_profile
from .financial_reporting import (
    FinancialCompanyResolver,
    FinancialStatementSource,
    _expand_decoded_financial_envelope,
    decode_financial_envelope,
    financial_company_resolver_from_listed_company_rows,
    financial_reporting_schemas,
    iter_statement_reports,
)
from .native_base24_reader import read_native_tables

CONTRACT = "p10-base24-weekly-exact-statement-observation-v1"
REFERENCE_TABLES = ("dwd_security", "dwd_security_identifier")


def weekly_parent(base: Path, config: dict[str, Any]) -> tuple[Any, FinancialCompanyResolver]:
    """Qualify only the explicit weekly identity role against whole-Base cold receipts."""
    reference = config.get("weekly_identity_reference", {})
    receipts = reference.get("acceptance_receipts", [])
    if (
        config.get("schema_version") != "quanthus.base24-weekly-governance.v1"
        or config.get("purpose") != "isolated_weekly_governance"
        or config.get("consumer_admission") != "NOT_GRANTED"
        or config.get("execution_authorized") is not False
        or reference.get("tables") != list(REFERENCE_TABLES)
        or reference.get("status") != "accepted_reference_only"
        or reference.get("trading_or_research_admission") is not False
        or not isinstance(receipts, list)
        or len({s for s in receipts if isinstance(s, str)}) < 2
        or any(
            not isinstance(s, str)
            or len(s) != 71
            or not s.startswith("sha256:")
            or any(c not in "0123456789abcdef" for c in s[7:])
            for s in receipts
        )
    ):
        raise BoundedError("BASE24_WEEKLY_REFERENCE_ROLE_INVALID")
    native = read_native_tables(base, config, REFERENCE_TABLES)
    pq = importlib.import_module("pyarrow.parquet")
    rows = {
        t: [row for p in native.files(t) for row in pq.ParquetFile(p).read().to_pylist()]
        for t in REFERENCE_TABLES
    }
    return native, financial_company_resolver_from_listed_company_rows(
        rows["dwd_security"], rows["dwd_security_identifier"]
    )


def statement_observation(
    raw: dict[str, Any],
    receipt: dict[str, Any],
    *,
    source: FinancialStatementSource,
    resolver: FinancialCompanyResolver,
    observed_at: datetime,
    through: date,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Project a new knowledge version, retaining exact source scalar/clock evidence."""
    import pyarrow as pa  # type: ignore[import-untyped]

    capture, observed = _clock(raw["acquired_at"]), _clock(observed_at)
    if capture > observed or raw.get("market_date") != through.isoformat():
        raise BoundedError("BASE24_WEEKLY_CAPTURE_SCOPE_INVALID")
    size = raw.get("raw_payload_bytes")
    if type(size) is not int or not 0 < size <= MAX_RAW_BYTES:
        raise BoundedError("BASE24_WEEKLY_RAW_BOUND_INVALID")
    request = json.loads(receipt["request_json"])
    if (
        receipt.get("dataset") != "financial_statements"
        or receipt.get("security_id") != raw.get("security_id")
        or request.get("params", {}).get("source") != raw.get("provider_source")
        or receipt.get("response_sha256") != raw.get("raw_payload_sha256")
        or receipt.get("response_bytes") != size
        or receipt.get("status") not in {"success_nonempty", "justified_empty"}
        or source.source_revision != raw.get("source_revision")
    ):
        raise BoundedError("BASE24_WEEKLY_RECEIPT_IDENTITY_DIFFERS")
    envelope = decode_financial_envelope(
        raw, max_raw_bytes=MAX_RAW_BYTES, preserve_numeric_tokens=True
    )
    periods = list(iter_statement_reports(envelope))
    if type(raw.get("period_count")) is not int or len(periods) != raw["period_count"]:
        raise BoundedError("BASE24_WEEKLY_PERIOD_CENSUS_DIFFERS")
    if (receipt["status"] == "justified_empty") != (not periods):
        raise BoundedError("BASE24_WEEKLY_RECEIPT_EMPTY_STATE_DIFFERS")
    if sum(len(body["data"]) for _, body, _ in periods) > MAX_FACTS:
        raise BoundedError("BASE24_WEEKLY_FACT_BOUND_INVALID")
    for _, body, _ in periods:
        for item in body["data"]:
            value = item.get("item_value")
            if (
                item.get("item_display_type") != 1
                and value is not None
                and type(value) not in (str, int)
            ):
                raise BoundedError("BASE24_WEEKLY_UNSUPPORTED_ORIGINAL_SCALAR")
    expanded = _expand_decoded_financial_envelope(
        raw, envelope=envelope, source=source, company_resolver=resolver, through_date=through
    )
    rows = dict(
        zip(
            TABLES,
            [list(expanded.reports), list(expanded.concepts), list(expanded.facts)],
            strict=True,
        )
    )
    classifications: Counter[str] = Counter()
    for table, values in rows.items():
        for row in values:
            if row["knowledge_from"] > observed:
                raise BoundedError("BASE24_WEEKLY_REFERENCE_KNOWLEDGE_AFTER_OBSERVATION")
            row["knowledge_from"] = observed
            if table == TABLES[1]:
                continue
            body = envelope.report_list[row["period_end"].strftime("%Y%m%d")]
            try:
                published = _clock(body.get("publish_date"))
            except BoundedError:
                raise
            except RuntimeError:
                published = None
            if published is not None and published > capture:
                raise BoundedError("BASE24_WEEKLY_PUBLICATION_AFTER_CAPTURE")
            row["published_at"] = published
            if table == TABLES[2]:
                ordinal = int(row["source_record_id"].rsplit("&item_index=", 1)[1])
                token = body["data"][ordinal].get("item_value")
                if type(token) is int:
                    token = str(token)
                profile = decimal_profile(token, precision=38, scale=10)
                classifications[profile["classification"]] += 1
                row["value_decimal"] = Decimal(token) if profile["fits"] else None
                row["value_text"] = (
                    token if not profile["fits"] and profile["classification"] != "NULL" else None
                )
                row["is_nil"] = profile["classification"] == "NULL"
    schemas = financial_reporting_schemas()
    for table, values in rows.items():
        typed = pa.Table.from_pylist(values, schema=schemas[table])
        if typed.to_pylist() != values or any(
            not f.nullable and typed.column(f.name).null_count for f in schemas[table]
        ):
            raise BoundedError("BASE24_WEEKLY_TYPED_ATOM_CHANGED")
    return rows, {
        "contract_id": CONTRACT,
        "raw_payload_sha256": raw["raw_payload_sha256"],
        "source_member_sha256": source.source_sha256,
        "original_acquired_at": raw["acquired_at"],
        "governance_observed_at": observed,
        "business_through": through.isoformat(),
        "row_counts": {t: len(rows[t]) for t in TABLES},
        "typed_rows_sha256": {t: digest(rows[t]) for t in TABLES},
        "scalar_classifications": dict(sorted(classifications.items())),
        "quality_exceptions": list(expanded.quality_exceptions),
        "decoded_dependencies_built": 1,
        "provider_acquisition_inferred": False,
        "historical_PIT_admission": False,
        "consumer_admission": "NOT_GRANTED",
    }

"""P10 exact statement-envelope projection, bounded by one verified envelope.

Reports, concepts and facts share a single decoded raw dependency. This prepares
real typed observations; it cannot grant provider/PIT or complete-image admission.
Legacy financial construction retains its original public entry and semantics.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from decimal import Decimal
from typing import Any

from .base22 import THROUGH, digest
from .base22_bounded import BoundedError
from .base22_outputs import instant
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

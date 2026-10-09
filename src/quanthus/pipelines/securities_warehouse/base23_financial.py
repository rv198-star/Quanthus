"""September statement observations from complete retained raw envelopes.

One verified envelope supplies all three typed consumers. Literal values and
publication/capture clocks are kept; newly qualified knowledge is observed now.
Native merge, quarterly/abstract closure and whole-image acceptance are separate.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from decimal import Decimal
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_financial import MAX_FACTS, MAX_RAW_BYTES, TABLES, _clock
from .base22_precision import decimal_profile
from .base23_financial_reconfirmation import declared_statement_identity
from .base23_sources import START, THROUGH
from .financial_reporting import (
    FinancialCompanyResolver,
    FinancialStatementSource,
    _expand_decoded_financial_envelope,
    decode_financial_envelope,
    financial_reporting_schemas,
    iter_statement_reports,
)

CONTRACT_ID = "p10-base23-retained-exact-statement-observations-v1"
QUALIFICATION = "COMPLETE_RETAINED_STATEMENT_ENVELOPE_NOT_PROVIDER_PIT"


def statement_observation(
    raw: dict[str, Any], *, source: FinancialStatementSource,
    resolver: FinancialCompanyResolver, observed_at: datetime,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    import pyarrow as pa  # type: ignore[import-untyped]

    declared = declared_statement_identity(raw)
    if not str(START) <= raw["market_date"] <= str(THROUGH):
        raise BoundedError("SEPTEMBER_STATEMENT_CAPTURE_WINDOW_INVALID")
    acquired, observed = _clock(raw["acquired_at"]), _clock(observed_at)
    if acquired > observed:
        raise BoundedError("SEPTEMBER_STATEMENT_CAPTURE_AFTER_GOVERNANCE")
    envelope = decode_financial_envelope(raw, max_raw_bytes=MAX_RAW_BYTES)
    periods = list(iter_statement_reports(envelope))
    if len(periods) != raw["period_count"]:
        raise BoundedError("SEPTEMBER_STATEMENT_PERIOD_CENSUS_INVALID")
    if sum(len(body["data"]) for _, body, _ in periods) > MAX_FACTS:
        raise BoundedError("SEPTEMBER_STATEMENT_ENVELOPE_ITEM_BOUND_FAILED")
    for _, body, _ in periods:
        for item in body["data"]:
            if item.get("item_display_type") != 1 and not (
                item.get("item_value") is None or isinstance(item["item_value"], str)
            ):
                raise BoundedError("SEPTEMBER_STATEMENT_ORIGINAL_SCALAR_TOKEN_UNAVAILABLE")
    expanded = _expand_decoded_financial_envelope(
        raw, envelope=envelope, source=source, company_resolver=resolver, through_date=THROUGH
    )
    rows = dict(zip(TABLES, [list(expanded.reports), list(expanded.concepts),
                             list(expanded.facts)], strict=True))
    counts: Counter[str] = Counter()
    for table, values in rows.items():
        for row in values:
            if row["knowledge_from"] > observed:
                raise BoundedError("SEPTEMBER_STATEMENT_DATED_PARENT_NOT_AVAILABLE")
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
            if published is not None and published > acquired:
                raise BoundedError("SEPTEMBER_STATEMENT_PUBLICATION_AFTER_CAPTURE")
            row["published_at"] = published
            if table == TABLES[2]:
                index = int(row["source_record_id"].rsplit("&item_index=", 1)[1])
                token = body["data"][index].get("item_value")
                profile = decimal_profile(token, precision=38, scale=10)
                counts[profile["classification"]] += 1
                row["value_decimal"] = Decimal(token) if profile["fits"] else None
                row["value_text"] = (token if not profile["fits"]
                                     and profile["classification"] != "NULL" else None)
                row["is_nil"] = profile["classification"] == "NULL"
    ss = financial_reporting_schemas()
    for table in TABLES:
        typed = pa.Table.from_pylist(rows[table], schema=ss[table])
        if typed.to_pylist() != rows[table] or any(
            not f.nullable and typed.column(f.name).null_count for f in ss[table]
        ):
            raise BoundedError("SEPTEMBER_STATEMENT_SERIALIZED_ATOM_CHANGED")
    proof = {
        "contract_id": CONTRACT_ID, "complete_envelope_fingerprint":
        declared["complete_envelope_fingerprint"],
        "source_member_sha256": source.source_sha256, "source_revision": source.source_revision,
        "raw_payload_sha256": raw["raw_payload_sha256"],
        "original_raw_acquired_at": raw["acquired_at"], "actual_acquisition_instant": acquired,
        "governance_observed_at": observed, "decoded_dependencies_built": 1,
        "row_counts": {t: len(rows[t]) for t in TABLES},
        "typed_rows_sha256": {t: digest(rows[t]) for t in TABLES},
        "scalar_classifications": dict(counts),
        "quality_exceptions": list(expanded.quality_exceptions),
        "provider_PIT_qualified": False, "native_history_changed": False,
    }
    return rows, proof

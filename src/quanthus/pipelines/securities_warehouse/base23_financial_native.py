"""Exact September quarterly observations and explicit abstract dispositions.

Quarterly inputs have an explicit currency and source identity. Daily summaries
do not establish those units or historical availability; their complete rows are
accounted for without inventing either. This is a governance scope, not admission
to a PIT consumer or proof of complete September financial coverage.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_financial import TABLES, _clock
from .base22_precision import decimal_profile
from .base23_sources import THROUGH
from .financial_reporting import (
    FinancialCompanyResolver,
    FinancialReportingSource,
    expand_financial_native_row,
    financial_reporting_schemas,
)

CONTRACT_ID = "p10-base23-retained-exact-quarterly-observations-v1"
QUALIFICATION = "COMPLETE_RETAINED_QUARTERLY_ROWS_NOT_PROVIDER_PIT"
NAMESPACE = "sina.full_income_statement_projection"
FIELDS = ("revenue", "net_profit", "parent_net_profit")
RAW_FIELDS = frozenset({
    "security_id", "stock_code", "report_date", "publish_date", "update_time",
    *FIELDS, "currency", "is_audit", "audit_opinion", "source", "source_revision", "acquired_at",
})
ABSTRACT_FIELDS = frozenset({
    "net_profit", "profit_growth_yoy", "provider_acquired_at", "provider_dataset",
    "provider_source_path", "provider_window_end", "provider_window_start", "publish_deadline",
    "report_date", "revenue", "revenue_growth_yoy", "roe", "stock_code", "acquired_at",
})


def quarterly_identity(raw: dict[str, Any]) -> tuple[str, str, str]:
    if (
        set(raw) != RAW_FIELDS or raw.get("source") != NAMESPACE
        or not re.fullmatch(r"\d{6}", str(raw.get("stock_code")))
        or raw.get("security_id") != raw.get("stock_code")
        or not re.fullmatch(r"\d{8}", str(raw.get("report_date")))
        or not raw.get("source_revision") or raw.get("currency") != "CNY"
        or any(not isinstance(raw[f], str) and raw[f] is not None for f in RAW_FIELDS)
    ):
        raise BoundedError("SEPTEMBER_QUARTERLY_SOURCE_OR_SCHEMA_UNQUALIFIED")
    # Date validity is checked separately from its literal representation.
    datetime.strptime(raw["report_date"], "%Y%m%d")
    return raw["source"], raw["stock_code"], raw["report_date"]


def latest_quarterly_captures(
    observations: Iterable[tuple[dict[str, Any], dict[str, Any], int]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Bounded September scope, with unchanged provider identity and real clocks.

    Complete original captures remain in the scope archive. This selects the
    newest observed snapshot, not the provider's authority over another source.
    """
    selected: dict[tuple[str, str, str], tuple[dict[str, Any], dict[str, Any]]] = {}
    seen = set()
    for number, (raw, source, ordinal) in enumerate(observations, 1):
        if number > 131072 or len(selected) > 65536:
            raise BoundedError("SEPTEMBER_QUARTERLY_SELECTION_BOUND")
        location = source["sha256"], ordinal
        if location in seen or type(ordinal) is not int or not 0 <= ordinal < source["rows"]:
            raise BoundedError("SEPTEMBER_QUARTERLY_CAPTURE_CENSUS_INVALID")
        seen.add(location)
        key, clock = quarterly_identity(raw), _clock(raw["acquired_at"])
        item = {"identity_key": key, "member_sha256": source["sha256"], "ordinal": ordinal,
                "release": source["release"], "original_raw_acquired_at": raw["acquired_at"],
                "complete_raw_row_sha256": digest(raw)}
        if key in selected:
            previous, previous_item = selected[key]
            prior_clock = _clock(previous["acquired_at"])
            if clock == prior_clock and digest(raw) != digest(previous):
                raise BoundedError("SEPTEMBER_QUARTERLY_EQUAL_CLOCK_BODY_CONFLICT")
            if clock < prior_clock or (
                clock == prior_clock and digest(item) >= digest(previous_item)
            ):
                continue
        selected[key] = raw, item
    return [selected[key] for key in sorted(selected)]


def quarterly_observation(
    raw: dict[str, Any], *, source: FinancialReportingSource,
    resolver: FinancialCompanyResolver, observed_at: datetime,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    import pyarrow as pa  # type: ignore[import-untyped]

    key = quarterly_identity(raw)
    if source.source_id != "weekly_full_financial_quarterly":
        raise BoundedError("SEPTEMBER_QUARTERLY_ADAPTER_IDENTITY_UNQUALIFIED")
    acquired, observed = _clock(raw["acquired_at"]), _clock(observed_at)
    if acquired > observed:
        raise BoundedError("SEPTEMBER_QUARTERLY_CAPTURE_AFTER_GOVERNANCE")
    expanded = expand_financial_native_row(
        raw, source=source, company_resolver=resolver,
        knowledge_cutoff=observed, through_date=THROUGH,
    )
    rows = dict(zip(TABLES, [list(expanded.reports), list(expanded.concepts),
                             list(expanded.facts)], strict=True))
    try:
        published = _clock(raw["publish_date"])
    except BoundedError:
        raise
    except RuntimeError:
        published = None  # Date-only disclosure is not an actual availability instant.
    if published is not None and published > acquired:
        raise BoundedError("SEPTEMBER_QUARTERLY_PUBLICATION_AFTER_CAPTURE")
    classes: Counter[str] = Counter()
    for table, values in rows.items():
        for row in values:
            if row["knowledge_from"] > observed:
                raise BoundedError("SEPTEMBER_QUARTERLY_PARENT_NOT_AVAILABLE")
            row["knowledge_from"] = observed
            if table == TABLES[1]:
                continue
            row["published_at"] = published
            if table != TABLES[2]:
                continue
            field = row["source_record_id"].rsplit("&concept=", 1)[1]
            token = raw[field]
            profile = decimal_profile(token, precision=38, scale=10)
            classes[profile["classification"]] += 1
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
            raise BoundedError("SEPTEMBER_QUARTERLY_SERIALIZED_ATOM_CHANGED")
    proof = {
        "contract_id": CONTRACT_ID, "original_identity_key": key,
        "complete_raw_row_sha256": digest(raw), "source_member_sha256": source.source_sha256,
        "source_revision": source.source_revision, "original_raw_acquired_at": raw["acquired_at"],
        "actual_acquisition_instant": acquired, "governance_observed_at": observed,
        "original_publication_literal_sha256": digest(raw["publish_date"]),
        "published_at": published, "currency_asserted_by_raw": raw["currency"],
        "row_counts": {t: len(rows[t]) for t in TABLES},
        "typed_rows_sha256": {t: digest(rows[t]) for t in TABLES},
        "scalar_classifications": dict(classes),
        "quality_exceptions": list(expanded.quality_exceptions),
        "provider_PIT_qualified": False, "native_history_changed": False,
    }
    return rows, proof


def abstract_disposition(raw: dict[str, Any], *, observed_at: datetime) -> dict[str, Any]:
    """Account for every literal summary; do not infer units or fix clock ordering."""
    if (
        set(raw) != ABSTRACT_FIELDS
        or not re.fullmatch(r"\d{6}", str(raw.get("stock_code")))
        or raw.get("provider_dataset") != "financial-disclosures"
        or any(not isinstance(raw[f], str) and raw[f] is not None for f in ABSTRACT_FIELDS)
    ):
        raise BoundedError("SEPTEMBER_FINANCIAL_ABSTRACT_SCHEMA_UNQUALIFIED")
    acquired, observed = _clock(raw["acquired_at"]), _clock(observed_at)
    provider = _clock(raw["provider_acquired_at"]) if raw["provider_acquired_at"] else None
    if acquired > observed or provider is not None and provider > observed:
        raise BoundedError("SEPTEMBER_FINANCIAL_ABSTRACT_CAPTURE_AFTER_GOVERNANCE")
    reasons = ["CURRENCY_AND_PROVIDER_SCALAR_UNIT_CLOSURE_UNAVAILABLE"]
    if provider is None:
        reasons.append("PROVIDER_ACQUISITION_UNAVAILABLE")
    elif provider > acquired:
        reasons.append("PROVIDER_AND_COLLECTOR_CLOCK_ORDER_UNPROVEN")
    return {"complete_raw_row_sha256": digest(raw), "stock_code": raw["stock_code"],
            "report_date_literal": raw["report_date"], "raw_acquired_at": raw["acquired_at"],
            "raw_provider_acquired_at": raw["provider_acquired_at"],
            "actual_acquisition_instant": acquired, "governance_observed_at": observed,
            "reasons": reasons, "typed_outputs": 0, "published_at": None,
            "publish_deadline_is_publication": False, "provider_PIT_qualified": False}

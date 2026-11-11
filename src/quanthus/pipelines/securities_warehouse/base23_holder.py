"""September retained shareholder-count observations, exact and purpose-scoped."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_financial import _clock
from .base22_holder_exact import TABLE as TABLE
from .base22_precision import decimal_profile
from .base23_sources import THROUGH
from .disclosure_event import _report_date
from .financial_reporting import FinancialCompanyResolver
from .ownership_capital import (
    materialize_shareholder_count_row,
    ownership_capital_schemas,
)

CONTRACT_ID = "p10-base23-retained-holder-count-exact13-v1"
QUALIFICATION = "RETAINED_LITERAL_PROVIDER_ROWS_NOT_HISTORICAL_PROVIDER_PIT"
RAW_FIELDS = frozenset({
    "avg_hold_num", "avg_market_cap", "change_num", "change_ratio", "code", "end_date",
    "fetched_at", "hold_notice_date", "knowledge_cutoff", "name", "pre_shareholder_count",
    "provider_acquired_at", "provider_dataset", "provider_source_path", "provider_window_end",
    "provider_window_start", "report_label", "shareholder_count", "source", "total_a_shares",
    "total_market_cap", "revision_retained", "lookback_days", "watermark_date", "acquired_at",
})


def holder_identity(raw: dict[str, Any]) -> tuple[str, str, str]:
    if (
        set(raw) != RAW_FIELDS or raw.get("source") != "eastmoney_rpt_holdernum_det"
        or raw.get("provider_dataset") != "shareholder-count"
        or not re.fullmatch(r"\d{6}", str(raw.get("code")))
        or type(raw.get("revision_retained")) is not bool
        or type(raw.get("lookback_days")) is not int
        or any(not isinstance(raw[f], str) and raw[f] is not None
               for f in RAW_FIELDS - {"revision_retained", "lookback_days"})
    ):
        raise BoundedError("SEPTEMBER_HOLDER_SOURCE_OR_SCHEMA_UNQUALIFIED")
    day = _report_date(raw["end_date"], "retained shareholder end_date")
    return raw["source"], raw["code"], day.isoformat()


def holder_schema() -> Any:
    import pyarrow as pa  # type: ignore[import-untyped]

    return ownership_capital_schemas()[TABLE].append(
        pa.field("average_holding_exact", pa.decimal256(41, 13)),
    )


def holder_observation(
    raw: dict[str, Any], *, source_sha256: str, revision: str,
    resolver: FinancialCompanyResolver, observed_at: datetime,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    import pyarrow as pa

    key = holder_identity(raw)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", source_sha256) or not revision:
        raise BoundedError("SEPTEMBER_HOLDER_SOURCE_BINDING_INVALID")
    acquired, observed = _clock(raw["acquired_at"]), _clock(observed_at)
    provider = _clock(raw["provider_acquired_at"]) if raw["provider_acquired_at"] else None
    if acquired > observed or provider is not None and provider > observed:
        raise BoundedError("SEPTEMBER_HOLDER_CAPTURE_AFTER_GOVERNANCE")
    day = _report_date(raw["end_date"], "retained shareholder end_date")
    parent, reason = resolver.resolve_for_report(raw["code"], period_end=day)
    held = "AFTER_IMAGE_CUTOFF" if day > THROUGH else reason if parent is None else None
    rows: list[dict[str, Any]] = []
    exact = decimal_profile(raw["avg_hold_num"], precision=41, scale=13)
    old = decimal_profile(raw["avg_hold_num"], precision=38, scale=10)
    count = decimal_profile(raw["shareholder_count"], precision=19, scale=0)
    if held is None:
        if (not count["fits"] or not exact["fits"] and exact["classification"] != "NULL"):
            raise BoundedError("SEPTEMBER_HOLDER_EXACT_SCALAR_UNREPRESENTABLE")
        value = Decimal(raw["shareholder_count"])
        if not 0 <= value < 2**63:
            raise BoundedError("SEPTEMBER_HOLDER_COUNT_RANGE_INVALID")
        assert parent is not None
        if parent.knowledge_from > observed:
            raise BoundedError("SEPTEMBER_HOLDER_PARENT_NOT_AVAILABLE")
        # The legacy mapper receives no average, so its quantizer is never used.
        row = materialize_shareholder_count_row(
            dict(raw, avg_hold_num=None, knowledge_cutoff=observed),
            company_id=parent.company_id, security_id=parent.security_id,
            source_sha256=source_sha256, parent_knowledge_from=parent.knowledge_from,
        )
        token = raw["avg_hold_num"]
        row.update(
            knowledge_from=observed, published_at=None, source_revision=revision,
            raw_holder_count=raw["shareholder_count"], raw_average_holding=token,
            average_holding=(pa.scalar(Decimal(token), type=pa.decimal128(38, 10)).as_py()
                             if old["fits"] else None),
            average_holding_exact=(pa.scalar(Decimal(token), type=pa.decimal256(41, 13)).as_py()
                                   if exact["fits"] else None),
            parser_version=CONTRACT_ID,
        )
        typed = pa.Table.from_pylist([row], schema=holder_schema())
        if typed.to_pylist() != [row] or any(
            not f.nullable and typed.column(f.name).null_count for f in holder_schema()
        ):
            raise BoundedError("SEPTEMBER_HOLDER_SERIALIZED_ATOM_CHANGED")
        rows = typed.to_pylist()
    proof = {"contract_id": CONTRACT_ID, "complete_raw_row_sha256": digest(raw),
             "identity_key": key, "source_member_sha256": source_sha256, "revision": revision,
             "raw_acquired_at": raw["acquired_at"], "actual_acquisition_instant": acquired,
             "raw_provider_acquired_at": raw["provider_acquired_at"],
             "provider_clock_order": "UNAVAILABLE" if provider is None else
             "UNPROVEN" if provider > acquired else "RETAINED_ORDERED_NOT_PROVIDER_PIT",
             "governance_observed_at": observed, "held_reason": held,
             "row_count": len(rows), "typed_rows_sha256": digest(rows),
             "old_column_classification": old["classification"],
             "exact13_column_classification": exact["classification"],
             "published_at_inferred": False, "provider_PIT_qualified": False}
    return {TABLE: rows}, proof


def latest_holder_captures(
    observations: Iterable[tuple[dict[str, Any], dict[str, Any], int]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    selected: dict[tuple[str, str, str], tuple[dict[str, Any], dict[str, Any]]] = {}
    seen = set()
    for n, (raw, source, ordinal) in enumerate(observations, 1):
        if n > 32768:
            raise BoundedError("SEPTEMBER_HOLDER_CAPTURE_SCOPE_BOUND")
        location = source["sha256"], ordinal
        if location in seen or type(ordinal) is not int or not 0 <= ordinal < source["rows"]:
            raise BoundedError("SEPTEMBER_HOLDER_CAPTURE_CENSUS_INVALID")
        seen.add(location)
        key, clock = holder_identity(raw), _clock(raw["acquired_at"])
        item = {"identity_key": key, "member_sha256": source["sha256"], "ordinal": ordinal,
                "release": source["release"], "original_raw_acquired_at": raw["acquired_at"],
                "complete_raw_row_sha256": digest(raw)}
        if key in selected:
            old, previous = selected[key]
            previous_clock = _clock(old["acquired_at"])
            if clock == previous_clock and digest(raw) != digest(old):
                raise BoundedError("SEPTEMBER_HOLDER_EQUAL_CLOCK_BODY_CONFLICT")
            if clock < previous_clock or (
                clock == previous_clock and digest(item) >= digest(previous)
            ):
                continue
        selected[key] = raw, item
    return [selected[key] for key in sorted(selected)]

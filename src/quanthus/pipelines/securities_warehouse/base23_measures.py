"""Explicit September successor of the frozen August daily measure mapping."""

from __future__ import annotations

import math
import re
import struct
from collections import Counter
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_outputs import decimal_observation, instant
from .base23_core import revision_union
from .base23_sources import START, THROUGH

CONTRACT_ID = "p10-base23-retained-daily-measures-v1"
QUALIFICATION = "RETAINED_MANIFEST_AND_PAYLOAD_VERIFIED_NOT_PROVIDER_PIT"
TABLES = ("dwd_security_valuation", "dwd_index_bar")
MEASURES = ("pe_ttm", "pb", "ps_ttm", "pcf_ttm")


def _common(
    raw: dict[str, Any], day: date, observed_at: datetime, source_sha: str, revision: str
) -> tuple[datetime, datetime]:
    if type(day) is not date or not START <= day <= THROUGH or raw.get("date") != str(day):
        raise BoundedError("DAILY_MEASURE_BUSINESS_DATE_INVALID")
    if (
        not revision
        or not source_sha.startswith("sha256:")
        or len(source_sha) != 71
        or any(c not in "0123456789abcdef" for c in source_sha[7:])
    ):
        raise BoundedError("DAILY_MEASURE_SOURCE_IDENTITY_MISSING")
    acquired_raw = raw.get("acquired_at")
    if isinstance(acquired_raw, str):
        fractional = re.search(r"[ T]\d\d:\d\d:\d\d[.,](\d+)", acquired_raw)
        if fractional and any(c != "0" for c in fractional[1][6:]):
            raise BoundedError("DAILY_MEASURE_CLOCK_PRECISION_UNSUPPORTED")
    acquired, observed = instant(acquired_raw), instant(observed_at)
    if acquired > observed:
        raise BoundedError("DAILY_MEASURE_CAPTURE_AFTER_GOVERNANCE_OBSERVATION")
    return acquired, observed


def _typed_proof(
    rows: list[dict[str, Any]],
    schema: Any,
    numbers: list[tuple[Any, Any]],
    acquired: datetime,
    observed: datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import pyarrow as pa  # type: ignore[import-untyped]

    table = pa.Table.from_pylist(rows, schema=schema)
    if any(not f.nullable and table.column(f.name).null_count for f in schema):
        raise BoundedError("DAILY_MEASURE_REQUIRED_FIELD_NULL")
    typed = table.to_pylist()
    if typed != rows:
        raise BoundedError("DAILY_MEASURE_TYPED_ATOM_CHANGED")
    counts: Counter[str] = Counter()
    for original, value in numbers:
        if original is None:
            if value is not None:
                raise BoundedError("DAILY_MEASURE_NULL_CHANGED")
            counts["NULL_UNAVAILABLE"] += 1
        elif isinstance(original, float):
            if not math.isfinite(original) or struct.pack("!d", original) != struct.pack(
                "!d", float(value)
            ):
                raise BoundedError("DAILY_MEASURE_RETAINED_FLOAT_CHANGED")
            counts["RETAINED_FLOAT_SHORTEST_DECIMAL_EXACT"] += 1
        else:
            counts["EXACT_RETAINED_NUMERIC"] += 1
        if value == 0:
            counts["ZERO"] += 1
    return typed, {
        "raw_acquisition_instant": acquired,
        "actual_governance_observation": observed,
        "provider_acquisition": None,
        "original_vendor_decimal": "UNKNOWN_FOR_RETAINED_FLOAT",
        "numeric_classifications": dict(counts),
        "row_census_sha256": digest(typed),
    }


def valuation_observation(
    raw: dict[str, Any],
    *,
    day: date,
    listing: dict[str, Any],
    calendar: dict[str, Any],
    observed_at: datetime,
    source_sha: str,
    revision: str,
    schema: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    required = {
        "security_id",
        "stock_code",
        "market_date",
        "date",
        "source",
        "acquired_at",
        "total_share",
        "total_mv",
        *MEASURES,
    }
    if set(raw) != required or raw["security_id"] != raw["stock_code"]:
        raise BoundedError("DAILY_VALUATION_RAW_SCHEMA_OR_VENDOR_CODE_CHANGED")
    acquired, observed = _common(raw, day, observed_at, source_sha, revision)
    if (
        raw.get("market_date") != str(day)
        or raw.get("source") != "baostock.query_history_k_data_plus.valuation"
    ):
        raise BoundedError("DAILY_VALUATION_SOURCE_OR_DATE_UNQUALIFIED")
    if raw.get("total_share") is not None or raw.get("total_mv") is not None:
        raise BoundedError("DAILY_VALUATION_NONEMPTY_CAPITAL_REQUIRES_DOMAIN_ADAPTER")
    if (
        listing["listing_code"] != raw.get("stock_code")
        or calendar["venue_id"] != listing["venue_id"]
        or calendar["trade_date"] != day
        or calendar["session_type"] != "day"
    ):
        raise BoundedError("DAILY_VALUATION_DATE_VENUE_LISTING_MISMATCH")
    for p in [listing, calendar]:
        if (
            p["knowledge_from"] > observed
            or p["knowledge_to"] is not None
            and observed >= p["knowledge_to"]
        ):
            raise BoundedError("DAILY_MEASURE_PARENT_KNOWLEDGE_UNAVAILABLE")
    start = datetime.combine(day, time(), ZoneInfo("Asia/Shanghai"))
    if (
        listing["effective_from"] > start
        or listing["effective_to"] is not None
        and start >= listing["effective_to"]
    ):
        raise BoundedError("DAILY_MEASURE_LISTING_DATE_UNAVAILABLE")
    rows = [
        {
            "record_source": "base23_september/valuation_daily_delta",
            "source_record_id": f"stock_code={raw['stock_code']}&date={day}",
            "source_revision": revision,
            "source_artifact_sha256": source_sha,
            "knowledge_from": observed,
            "knowledge_to": None,
            "observation_start": start,
            "observation_end": start + timedelta(days=1),
            "security_id": listing["security_id"],
            "listing_id": listing["listing_id"],
            "measure_type": measure,
            "measure_value": decimal_observation(raw.get(measure), nullable=True),
            "currency": None,
            "methodology": "provider_reported",
            "fiscal_basis": "ttm_or_reported",
        }
        for measure in MEASURES
    ]
    return _typed_proof(
        rows,
        schema,
        [(raw.get(f), r["measure_value"]) for f, r in zip(MEASURES, rows, strict=True)],
        acquired,
        observed,
    )


def index_observation(
    raw: dict[str, Any],
    *,
    day: date,
    index: dict[str, Any],
    observed_at: datetime,
    source_sha: str,
    revision: str,
    schema: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    required = {
        "index_code",
        "date",
        "source",
        "acquired_at",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "pct_chg",
    }
    if set(raw) != required:
        raise BoundedError("DAILY_INDEX_RAW_SCHEMA_CHANGED")
    acquired, observed = _common(raw, day, observed_at, source_sha, revision)
    if raw.get("source") != "baostock.query_history_k_data_plus.adjustflag=3" or index[
        "index_code"
    ] != raw.get("index_code"):
        raise BoundedError("DAILY_INDEX_SOURCE_OR_IDENTITY_UNQUALIFIED")
    start = datetime.combine(day, time(), ZoneInfo("Asia/Shanghai")).astimezone(observed.tzinfo)
    if (
        index["effective_from"] > start
        or index["effective_to"] is not None
        and start >= index["effective_to"]
        or index["knowledge_from"] > observed
        or index["knowledge_to"] is not None
        and observed >= index["knowledge_to"]
    ):
        raise BoundedError("DAILY_INDEX_DATED_PARENT_UNAVAILABLE")
    row = {
        "record_source": "base23_september/index_daily_delta",
        "source_record_id": f"index_code={raw['index_code']}&date={day}",
        "source_revision": revision,
        "source_artifact_sha256": source_sha,
        "knowledge_from": observed,
        "knowledge_to": None,
        "observation_start": start,
        "observation_end": start + timedelta(days=1),
        "index_id": index["index_id"],
        "calendar_session_id": None,
        "interval_size": "1d",
        "currency": index["base_currency"],
    }
    pairs = []
    for source, dest in [(f, f) for f in ["open", "high", "low", "close", "volume"]] + [
        ("amount", "turnover")
    ]:
        row[dest] = decimal_observation(raw.get(source), nullable=source in ["volume", "amount"])
        pairs.append((raw.get(source), row[dest]))
    if (
        not 0 < row["low"] <= min(row["open"], row["close"])
        or max(row["open"], row["close"]) > row["high"]
        or any(row[f] is not None and row[f] < 0 for f in ["volume", "turnover"])
    ):
        raise BoundedError("DAILY_INDEX_PRICE_OR_QUANTITY_SEMANTICS_INVALID")
    return _typed_proof([row], schema, pairs, acquired, observed)


def project_day(
    raw: dict[str, list[dict[str, Any]]],
    *,
    day: date,
    listings: list[dict[str, Any]],
    calendars: list[dict[str, Any]],
    indexes: list[dict[str, Any]],
    observed_at: datetime,
    sources: dict[str, dict[str, Any]],
    ss: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    if set(raw) != set(TABLES) or any(len(v) > 8192 for v in raw.values()):
        raise BoundedError("DAILY_MEASURE_INPUT_FAMILIES_OR_BOUND_INVALID")
    start = datetime.combine(day, time(), ZoneInfo("Asia/Shanghai")).astimezone(observed_at.tzinfo)
    output: dict[str, list[dict[str, Any]]] = {t: [] for t in TABLES}
    listings_by_code: dict[str, list[dict[str, Any]]] = {}
    calendars_by_market: dict[str, list[dict[str, Any]]] = {}
    indexes_by_code: dict[str, list[dict[str, Any]]] = {}
    for p in listings:
        listings_by_code.setdefault(p["listing_code"], []).append(p)
    for p in calendars:
        if p["trade_date"] == day and p["session_type"] == "day":
            calendars_by_market.setdefault(p["venue_id"], []).append(p)
    for p in indexes:
        indexes_by_code.setdefault(p["index_code"], []).append(p)
    proofs = []
    seen = set()
    # JSON object sorting and provider member insertion order must not reorder
    # the semantic proof array in a fresh independent process.
    for t in TABLES:
        rows = raw[t]
        for r in rows:
            code = r.get("stock_code") if t == TABLES[0] else r.get("index_code")
            if not isinstance(code, str) or not code:
                raise BoundedError("DAILY_MEASURE_RAW_CODE_UNKNOWN")
            key = (t, code)
            if key in seen:
                raise BoundedError("DAILY_MEASURE_DUPLICATE_RAW_KEY")
            seen.add(key)
            if t == TABLES[0]:
                parents = [
                    p
                    for p in listings_by_code.get(code, [])
                    if p["effective_from"] <= start
                    and (p["effective_to"] is None or start < p["effective_to"])
                ]
                if len(parents) != 1:
                    raise BoundedError("DAILY_MEASURE_LISTING_CLOSURE_MISSING")
                cal = calendars_by_market.get(parents[0]["venue_id"], [])
                if len(cal) != 1:
                    raise BoundedError("DAILY_MEASURE_CALENDAR_CLOSURE_MISSING")
                generated, proof = valuation_observation(
                    r,
                    day=day,
                    listing=parents[0],
                    calendar=cal[0],
                    observed_at=observed_at,
                    source_sha=sources[t]["sha256"],
                    revision=sources[t]["revision"],
                    schema=ss[t],
                )
            else:
                parents = indexes_by_code.get(code, [])
                if len(parents) != 1:
                    raise BoundedError("DAILY_MEASURE_INDEX_CLOSURE_MISSING")
                generated, proof = index_observation(
                    r,
                    day=day,
                    index=parents[0],
                    observed_at=observed_at,
                    source_sha=sources[t]["sha256"],
                    revision=sources[t]["revision"],
                    schema=ss[t],
                )
            output[t] += generated
            proofs.append(proof)
    return output, proofs


def project_revisions(
    raw: dict[str, list[tuple[str, str, list[dict[str, Any]]]]],
    *,
    day: date,
    listings: list[dict[str, Any]],
    calendars: list[dict[str, Any]],
    indexes: list[dict[str, Any]],
    observed_at: datetime,
    ss: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Identical captures share output; contradictory atoms receive no priority."""
    if set(raw) != set(TABLES):
        raise BoundedError("SEPTEMBER_MEASURES_EXACT_FAMILIES_REQUIRED")
    outputs: dict[str, list[dict[str, Any]]] = {t: [] for t in TABLES}
    details = {}
    for table in TABLES:
        aliased = [
            (
                sha,
                rev,
                [dict(r, stock_code=r["index_code"]) for r in rows] if table == TABLES[1] else rows,
            )
            for sha, rev, rows in raw[table]
        ]
        selected, held, union = revision_union(aliased)
        proofs = []
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for entry in selected:
            groups.setdefault((entry["sha256"], entry["revision"]), []).append(entry)
        for (sha, revision), entries in sorted(groups.items()):
            rows = [dict(entry["raw"]) for entry in entries]
            if table == TABLES[1]:
                for row in rows:
                    row.pop("stock_code")
            generated, proof = project_day(
                {t: rows if t == table else [] for t in TABLES},
                day=day,
                listings=listings,
                calendars=calendars,
                indexes=indexes,
                observed_at=observed_at,
                sources={table: {"sha256": sha, "revision": revision}},
                ss=ss,
            )
            outputs[table].extend(generated[table])
            proofs.append(
                {"output": proof, "retained_captures": [entry["observations"] for entry in entries]}
            )
        details[table] = {"union": union, "held": held, "proofs": proofs}
    return outputs, details

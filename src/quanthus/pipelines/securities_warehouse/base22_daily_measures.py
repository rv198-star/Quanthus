"""Retained daily valuation and index observations, never all-market admission."""

from __future__ import annotations

import json
import math
import re
import struct
from collections import Counter
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import THROUGH, _physical, digest, file_sha, load_contract, partition, schemas
from .base22_bounded import BoundedError, Budget, GlobalScan, semantic_scan
from .base22_outputs import decimal_observation, instant, market_observation
from .base22_sources import _tree

CONTRACT_ID = "p10-base22-retained-daily-measures-v1"
QUALIFICATION = "RETAINED_MANIFEST_AND_PAYLOAD_VERIFIED_NOT_PROVIDER_PIT"
TABLES = ("dwd_security_valuation", "dwd_index_bar")
MEASURES = ("pe_ttm", "pb", "ps_ttm", "pcf_ttm")


def _common(
    raw: dict[str, Any], day: date, observed_at: datetime, source_sha: str, revision: str
) -> tuple[datetime, datetime]:
    if (
        type(day) is not date
        or not date(2026, 8, 1) <= day <= THROUGH
        or raw.get("date") != str(day)
    ):
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
    rows = market_observation(
        raw,
        listing=listing,
        calendar=calendar,
        source_sha256=source_sha,
        revision=revision,
        family="valuation_daily_delta",
    )[TABLES[0]]
    for r in rows:
        r["knowledge_from"] = observed
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
        "record_source": "base22_august/index_daily_delta",
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
    for t, rows in raw.items():
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


def audit_scope(root: Path, body: dict[str, Any], budget: Budget) -> dict[str, Any]:
    import pyarrow as pa
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("projection_module_sha256") != file_sha(Path(__file__))
        or body.get("READY") is not False
        or body.get("complete_Base22") is not False
        or body.get("historical_PIT_admission") is not False
        or body.get("consumer_admission") != "NOT_GRANTED"
    ):
        raise BoundedError("DAILY_MEASURE_LOCAL_CONTRACT_INVALID")
    plan = body.get("input_closure", {})
    ids = plan.get("source_ids_by_table", {})
    if (
        plan.get("purpose") != CONTRACT_ID
        or plan.get("qualification") != QUALIFICATION
        or set(ids) != set(TABLES)
        or any(not v or len(v) != len(set(v)) for v in ids.values())
    ):
        raise BoundedError("DAILY_MEASURE_OFFLINE_SOURCE_PLAN_UNQUALIFIED")
    physical = body["physical_members"]
    if not body.get("days"):
        raise BoundedError("DAILY_MEASURE_EMPTY_DAY_CLOSURE")
    if _tree(root) != set(physical) | {"QUALIFICATION.json"}:
        raise BoundedError("DAILY_MEASURE_PHYSICAL_MEMBER_SET_CHANGED")
    expected_payload = {i["outputs"][t]["path"] for i in body["days"] for t in TABLES}
    if (
        len(expected_payload) != len(body["days"]) * len(TABLES)
        or {p for p in physical if p.startswith("payload/")} != expected_payload
    ):
        raise BoundedError("DAILY_MEASURE_PHYSICAL_PAYLOAD_SET_CHANGED")
    for rel, i in physical.items():
        p = _physical(root, rel, independent=True)
        if p.stat().st_size != i["size_bytes"] or file_sha(p) != i["sha256"]:
            raise BoundedError("DAILY_MEASURE_LOCAL_HASH_CHANGED")
        budget.check()
    c, model, ext = load_contract(root / "contract")
    ss = schemas(model, ext)
    required_parents = {
        "dwd_listed_company",
        "dwd_listing",
        "dwd_security",
        "dwd_trading_venue",
        "dwd_trading_calendar",
        "dwd_market_index",
    }
    if set(body["parents"]) != required_parents:
        raise BoundedError("DAILY_MEASURE_COMPLETE_PARENT_SET_MISSING")
    for table, items in body["parents"].items():
        if not items or any(
            not pq.ParquetFile(_physical(root, r, independent=True)).schema_arrow.equals(
                ss[table], check_metadata=False
            )
            for r in items
        ):
            raise BoundedError("DAILY_MEASURE_PARENT_SCHEMA_OR_FILES_CHANGED")
        if (
            sum(
                pq.ParquetFile(_physical(root, r, independent=True)).metadata.num_rows
                for r in items
            )
            > 32768
        ):
            raise BoundedError("DAILY_MEASURE_PARENT_ROW_BOUND_CHANGED")
    parents = {
        t: [
            r
            for rel in items
            for r in pq.ParquetFile(_physical(root, rel, independent=True)).read().to_pylist()
        ]
        for t, items in body["parents"].items()
    }
    observed = instant(body["observed_at"])
    counts: Counter[str] = Counter()
    outputs: dict[str, list[Path]] = {t: [] for t in TABLES}
    registry: dict[str, list[tuple[str, str, str]]] = {t: [] for t in TABLES}
    days = set()
    for i in body["days"]:
        day = date.fromisoformat(i["market_date"])
        if day in days:
            raise BoundedError("DAILY_MEASURE_DUPLICATE_DAY")
        days.add(day)
        raw = {}
        sources = {}
        manifest = json.loads(_physical(root, i["manifest"], independent=True).read_bytes())
        if _physical(root, i["READY"], independent=True).read_text().strip() != file_sha(
            _physical(root, i["manifest"], independent=True)
        ) or manifest["market_date"] != str(day):
            raise BoundedError("DAILY_MEASURE_LOCAL_RELEASE_BINDING_CHANGED")
        if set(i["sources"]) != set(TABLES) or set(i["outputs"]) != set(TABLES):
            raise BoundedError("DAILY_MEASURE_DAY_TABLE_SET_CHANGED")
        for t, source in i["sources"].items():
            if source["sha256"] not in ids[t]:
                raise BoundedError("DAILY_MEASURE_TABLE_LOCAL_SOURCE_UNKNOWN")
            entry = [a for a in manifest["artifacts"] if a["name"] == source["artifact_name"]]
            if (
                len(entry) != 1
                or entry[0]["sha256"] != source["sha256"]
                or entry[0]["source_revision"] != source["revision"]
            ):
                raise BoundedError("DAILY_MEASURE_LOCAL_SOURCE_UNQUALIFIED")
            p = _physical(root, source["raw"], independent=True)
            if file_sha(p) != source["sha256"]:
                raise BoundedError("DAILY_MEASURE_RAW_SHA_CHANGED")
            pf = pq.ParquetFile(p)
            if pf.metadata.num_rows == 0 or pf.metadata.num_rows > 8192:
                raise BoundedError("DAILY_MEASURE_RAW_ROW_BOUND_CHANGED")
            raw[t] = pf.read().to_pylist()
            sources[t] = source
        generated, proof = project_day(
            raw,
            day=day,
            listings=parents["dwd_listing"],
            calendars=parents["dwd_trading_calendar"],
            indexes=parents["dwd_market_index"],
            observed_at=observed,
            sources=sources,
            ss=ss,
        )
        if digest(proof) != i["proof_sha256"]:
            raise BoundedError("DAILY_MEASURE_RAW_PROOF_CHANGED")
        for t, rows in generated.items():
            out = i["outputs"][t]
            p = _physical(root, out["path"], independent=True)
            pf = pq.ParquetFile(p)
            if pf.metadata.num_rows > 32768 or not pf.schema_arrow.equals(ss[t]):
                raise BoundedError("DAILY_MEASURE_OUTPUT_BOUND_OR_SCHEMA_CHANGED")
            actual = pf.read()
            if (
                not actual.equals(pa.Table.from_pylist(rows, schema=ss[t]))
                or any(partition(r, c["tables"][t]) != out["partition"] for r in rows)
                or semantic_scan([p], ss[t], check=budget.check) != out["semantic_scan"]
            ):
                raise BoundedError("DAILY_MEASURE_COMPLETE_TYPED_PROJECTION_CHANGED")
            outputs[t].append(p)
            counts[t] += len(rows)
            registry[t].append(
                (
                    "base22_august/" + i["sources"][t]["artifact_name"],
                    i["sources"][t]["revision"],
                    i["sources"][t]["sha256"],
                )
            )
        budget.check()
    with GlobalScan(budget, read_only_roots=[root]) as scan:
        for t, items in body["parents"].items():
            scan.add(t, [root / p for p in items])
            scan.keys(t, c["tables"][t]["business_key"], c["tables"][t]["version_key"])
            scan.row_rules(t, ss[t], c["tables"][t])
        for t in TABLES:
            scan.add(t, outputs[t])
            scan.keys(t, c["tables"][t]["business_key"], c["tables"][t]["version_key"])
            scan.lineage(t, registry[t])
            scan.row_rules(t, ss[t], c["tables"][t])
        scan.references(
            {"tables": [t for t in model["tables"] if t["name"] in scan.tables]},
            {"tables": {t: c["tables"][t] for t in scan.tables}},
        )
    return {
        "status": "FULL_RETAINED_DAILY_MEASURES_LOCAL_TYPED_PASS_NOT_ALL_MARKET_OR_BASE22",
        "rows": dict(counts),
        "days": len(days),
        "source_runtime_reads": 0,
        "complete_Base22": False,
    }

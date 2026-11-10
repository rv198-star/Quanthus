"""Retained August unadjusted day bars, without market/PIT/consumer admission.

This independent-image route uses the existing opt-in P10 day-basis mapping.
Missing original provider Decimal tokens remain unknown. Representable retained
binary values round-trip exactly; unsupported precision is held, never rounded.
"""

from __future__ import annotations

import json
import struct
from collections import Counter
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import THROUGH, _physical, digest, file_sha, load_contract, partition, schemas
from .base22_bounded import BoundedError, Budget, GlobalScan, semantic_scan
from .base22_calendar import audit_calendar_scope
from .base22_outputs import instant, market_observation
from .base22_sources import SourceAssessmentError, _tree
from .base22_status import MAX_DAY_ROWS, resolve_status_listing
from .daily_price_basis import POLICY

CONTRACT_ID = "p10-base22-retained-august-unadjusted-day-bar-v1"
TABLE = "dwd_security_bar"
FAMILY = "daily_ohlcv_none_delta"


def bar_observation(
    raw: dict[str, Any],
    *,
    market_date: date,
    listing: dict[str, Any],
    calendar: dict[str, Any],
    observed_at: datetime,
    source_sha256: str,
    revision: str,
    schema: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Keep exact retained quantities, identities and clocks in a new observation."""
    import pyarrow as pa  # type: ignore[import-untyped]

    if type(market_date) is not date or not date(2026, 8, 1) <= market_date <= THROUGH:
        raise BoundedError("DAY_BAR_BUSINESS_SCOPE_INVALID")
    if raw.get("date") != str(market_date) or raw.get("market_date") != str(market_date):
        raise BoundedError("DAY_BAR_RAW_DATE_DISAGREES")
    if (
        raw.get("adj_mode") != "none"
        or raw.get("adj_version") != "baostock.adjustflag=3.v1"
        or raw.get("source") != "baostock.query_history_k_data_plus.adjustflag=3"
    ):
        raise BoundedError("DAY_BAR_EXACT_UNADJUSTED_SOURCE_UNKNOWN")
    if (
        listing["listing_code"] != raw.get("stock_code")
        or listing["venue_id"] != calendar["venue_id"]
        or calendar["trade_date"] != market_date
        or calendar["session_type"] != "day"
    ):
        raise BoundedError("DAY_BAR_DATE_CODE_VENUE_SESSION_MISMATCH")
    if (
        not revision
        or len(source_sha256) != 71
        or not source_sha256.startswith("sha256:")
        or any(c not in "0123456789abcdef" for c in source_sha256[7:])
    ):
        raise BoundedError("DAY_BAR_EXACT_SOURCE_IDENTITY_REQUIRED")
    clock, acquired = instant(observed_at), instant(raw.get("acquired_at"))
    start = datetime.combine(market_date, time(), ZoneInfo("Asia/Shanghai"))
    if acquired > clock:
        raise BoundedError("DAY_BAR_ACQUISITION_AFTER_NEW_OBSERVATION")
    for parent in (listing, calendar):
        if not (
            parent["knowledge_from"] <= clock
            and (parent["knowledge_to"] is None or clock < parent["knowledge_to"])
        ):
            raise BoundedError("DAY_BAR_PARENT_KNOWLEDGE_UNAVAILABLE")
    output = market_observation(
        raw,
        listing=listing,
        calendar=calendar,
        source_sha256=source_sha256,
        revision=revision,
        family=FAMILY,
    )[TABLE][0]
    output["knowledge_from"] = clock
    # Arrow exact rescale must preserve every finite scalar, including zero/null.
    typed = pa.Table.from_pylist([output], schema=schema).to_pylist()[0]
    if typed != output or output["observation_start"] != instant(start):
        raise BoundedError("DAY_BAR_TYPED_VALUE_OR_INSTANT_CHANGED")
    classes: Counter[str] = Counter()
    for raw_name, field in [(f, f) for f in ["open", "high", "low", "close", "volume"]] + [
        ("amount", "turnover")
    ]:
        original, value = raw.get(raw_name), typed[field]
        if isinstance(original, float):
            if struct.pack("!d", original) != struct.pack("!d", float(value)):
                raise BoundedError("DAY_BAR_RETAINED_FLOAT_INFORMATION_CHANGED")
            classes["RETAINED_FLOAT_PROVIDER_ORIGINAL_DECIMAL_UNAVAILABLE"] += 1
        else:
            classes["NULL" if original is None else "EXACT_" + type(original).__name__] += 1
        if value is not None and value == 0:
            classes["ZERO"] += 1
    return typed, {
        "contract_id": CONTRACT_ID,
        "policy": POLICY,
        "raw_row_sha256": digest(raw),
        "typed_row_sha256": digest(typed),
        "listing_row_sha256": digest(listing),
        "calendar_row_sha256": digest(calendar),
        "acquired_at": raw["acquired_at"],
        "provider_acquisition": None,
        "warehouse_observed_at": clock.isoformat(),
        "numeric_classifications": dict(classes),
        "source_precision_recovered": False,
        "historical_PIT_admission": False,
        "consumer_admission": "NOT_GRANTED",
        "sealed_rows_mutated": 0,
    }


def project_bar_day(
    rows: list[dict[str, Any]],
    *,
    day: date,
    listings: list[dict[str, Any]],
    calendars: list[dict[str, Any]],
    observed_at: datetime,
    raw_sha256: str,
    revision: str,
    schema: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """One bounded day; exceptions are explicit and never count as covered rows."""
    if not 0 < len(rows) <= MAX_DAY_ROWS or len(listings) > MAX_DAY_ROWS:
        raise BoundedError("DAY_BAR_SOURCE_OR_PARENT_BOUND_EXCEEDED")
    by_code: dict[str, list[dict[str, Any]]] = {}
    for listing in listings:
        by_code.setdefault(listing["listing_code"], []).append(listing)
    outputs, proofs, held, seen = [], [], [], set()
    for raw in rows:
        code = raw.get("stock_code")
        if not isinstance(code, str) or code in seen:
            raise BoundedError("DAY_BAR_RAW_DUPLICATE_OR_UNKNOWN_CODE_DAY")
        seen.add(code)
        try:
            listing = resolve_status_listing(
                raw, by_code.get(code, []), day=day, observed_at=observed_at
            )
            matches = [
                c
                for c in calendars
                if c["trade_date"] == day
                and c["venue_id"] == listing["venue_id"]
                and c["session_type"] == "day"
            ]
            if len(matches) != 1:
                raise BoundedError("DAY_BAR_EXACT_CALENDAR_KEY_NOT_UNIQUE")
            output, proof = bar_observation(
                raw,
                market_date=day,
                listing=listing,
                calendar=matches[0],
                observed_at=observed_at,
                source_sha256=raw_sha256,
                revision=revision,
                schema=schema,
            )
        except (BoundedError, SourceAssessmentError) as exc:
            held.append(
                {
                    "stock_code": code,
                    "market_date": str(day),
                    "raw_row_sha256": digest(raw),
                    "reason": str(exc),
                    "disposition": "UNQUALIFIED_NO_BAR_OUTPUT_NO_ROUNDING",
                }
            )
        else:
            outputs.append(output)
            proofs.append(proof)
    return outputs, proofs, held


def audit_bar_scope(root: Path, body: dict[str, Any], budget: Budget) -> dict[str, Any]:
    """Image-local all-row replay plus exact physical/schema/global closures."""
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("policy") != POLICY
        or body.get("projection_module_sha256") != file_sha(Path(__file__))
        or body.get("input_revision_digest") != digest(body.get("input_closure"))
        or body.get("purpose") != "NEW_RETAINED_DAY_BARS_NOT_MARKET_OR_PROVIDER_PRECISION"
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("READY") is not False
        or body.get("complete_Base22") is not False
        or body.get("historical_PIT_admission") is not False
        or body.get("consumer_admission") != "NOT_GRANTED"
    ):
        raise BoundedError("DAY_BAR_SCOPE_INVALID")
    if set(_tree(root)) != {*body["physical_members"], "QUALIFICATION.json"}:
        raise BoundedError("DAY_BAR_EXACT_PHYSICAL_SET_CHANGED")
    declared_outputs = [item["output"] for item in body["days"]]
    if len(declared_outputs) != len(set(declared_outputs)) or set(declared_outputs) != {
        p for p in body["physical_members"] if p.startswith("payload/")
    }:
        raise BoundedError("DAY_BAR_EXACT_OUTPUT_FILE_SET_CHANGED")
    for rel, item in body["physical_members"].items():
        p = _physical(root, rel, independent=True)
        if file_sha(p) != item["sha256"] or p.stat().st_size != item["size_bytes"]:
            raise BoundedError("DAY_BAR_PHYSICAL_MEMBER_CHANGED")
        budget.check()
    contract, model, extension = load_contract(root / "contract")
    ss = schemas(model, extension)
    parents = body["parents"]
    if set(parents) != {
        "dwd_listing",
        "dwd_trading_calendar",
        "dwd_security",
        "dwd_trading_venue",
        "dwd_listed_company",
    }:
        raise BoundedError("DAY_BAR_PARENT_SET_INVALID")
    for t, paths in parents.items():
        for p in paths:
            if not pq.ParquetFile(root / p).schema_arrow.equals(ss[t], check_metadata=False):
                raise BoundedError("DAY_BAR_PARENT_SCHEMA_CHANGED")
    cal_root = root / body["qualified_calendar_root"]
    cal_body = json.loads((cal_root / "QUALIFICATION.json").read_bytes())
    if cal_body["identity"] != body["calendar_scope_identity"]:
        raise BoundedError("DAY_BAR_CALENDAR_IDENTITY_CHANGED")
    audit_calendar_scope(cal_root, cal_body, budget)
    listings = [
        r for p in parents["dwd_listing"] for r in pq.ParquetFile(root / p).read().to_pylist()
    ]
    calendars = [
        r
        for p in parents["dwd_trading_calendar"]
        for r in pq.ParquetFile(root / p).read().to_pylist()
    ]
    if len(listings) > MAX_DAY_ROWS or len(calendars) != 93:
        raise BoundedError("DAY_BAR_PARENT_CENSUS_OR_BOUND_FAILED")
    outputs, admitted, seen = [], set(), set()
    classes: Counter[str] = Counter()
    held_counts: Counter[str] = Counter()
    rows = 0
    for item in body["days"]:
        day = date.fromisoformat(item["market_date"])
        if day in seen:
            raise BoundedError("DAY_BAR_DUPLICATE_SOURCE_DAY")
        seen.add(day)
        manifest = json.loads((root / item["manifest"]).read_bytes())
        if (root / item["READY"]).read_text().strip() != file_sha(root / item["manifest"]):
            raise BoundedError("DAY_BAR_ORIGINAL_READY_CHANGED")
        artifacts = [a for a in manifest["artifacts"] if a["name"] == FAMILY]
        if (
            len(artifacts) != 1
            or artifacts[0]["sha256"] != item["raw_sha256"]
            or artifacts[0]["source_revision"] != item["source_revision"]
            or manifest["market_date"] != str(day)
            or manifest.get("status") != "ready"
            or body["physical_members"][item["raw"]]["sha256"] != item["raw_sha256"]
        ):
            raise BoundedError("DAY_BAR_ORIGINAL_SOURCE_CLOSURE_CHANGED")
        raw = pq.ParquetFile(root / item["raw"]).read().to_pylist()
        if len(raw) != item["raw_rows"]:
            raise BoundedError("DAY_BAR_RAW_DAY_CENSUS_CHANGED")
        generated, proofs, held = project_bar_day(
            raw,
            day=day,
            listings=listings,
            calendars=calendars,
            observed_at=instant(body["observed_at"]),
            raw_sha256=item["raw_sha256"],
            revision=item["source_revision"],
            schema=ss[TABLE],
        )
        if digest(held) != item["unqualified_dispositions_sha256"]:
            raise BoundedError("DAY_BAR_EXACT_UNQUALIFIED_DISPOSITIONS_CHANGED")
        actual_file = pq.ParquetFile(root / item["output"])
        if not actual_file.schema_arrow.equals(ss[TABLE], check_metadata=False):
            raise BoundedError("DAY_BAR_OUTPUT_SCHEMA_CHANGED")
        actual = actual_file.read().to_pylist()
        if digest(generated) != digest(actual) or digest(proofs) != item["proof_sha256"]:
            raise BoundedError("DAY_BAR_RAW_TO_TYPED_REPLAY_CHANGED")
        if any(partition(r, contract["tables"][TABLE]) != item["partition"] for r in actual):
            raise BoundedError("DAY_BAR_PHYSICAL_PARTITION_CHANGED")
        summary = semantic_scan([root / item["output"]], ss[TABLE], check=budget.check)
        if summary != item["semantic_scan"]:
            raise BoundedError("DAY_BAR_RECOMPUTED_SEMANTICS_CHANGED")
        for proof in proofs:
            classes.update(proof["numeric_classifications"])
        held_counts.update(v["reason"] for v in held)
        rows += len(actual)
        admitted.add(("base22_august/" + FAMILY, item["source_revision"], item["raw_sha256"]))
        outputs.append(root / item["output"])
        budget.check()
    if seen != {c["trade_date"] for c in calendars if c["is_open"]} or len(seen) != 21:
        raise BoundedError("DAY_BAR_EXACT_OPEN_DAY_SET_CHANGED")
    with GlobalScan(budget, read_only_roots=[root]) as scan:
        for t, paths in parents.items():
            scan.add(t, [root / p for p in paths])
        scan.add(TABLE, outputs)
        selected = {*parents, TABLE}
        for t in sorted(selected):
            rule = contract["tables"][t]
            scan.keys(t, rule["business_key"], rule["version_key"])
            scan.row_rules(t, ss[t], rule)
        scan.lineage(TABLE, sorted(admitted))
        scan.references(
            {"tables": [t for t in model["tables"] if t["name"] in selected]},
            {"tables": {t: contract["tables"][t] for t in selected}},
        )
    result = {
        "status": "FULL_LOCAL_RETAINED_DAY_BAR_PASS_NOT_MARKET_OR_PROVIDER_PRECISION",
        "scope_identity": body["identity"],
        "observed_days": len(seen),
        "output_rows": rows,
        "numeric_classifications": dict(classes),
        "unqualified_source_rows": dict(held_counts),
        "complete_selected_raw_projection": not held_counts,
        "raw_and_output_hashes_unchanged": True,
        "provider_original_Decimal_recovered": False,
        "provider_acquisition_inferred": False,
        "historical_PIT_admission": False,
        "consumer_admission": "NOT_GRANTED",
        "complete_Base22": False,
    }
    return {**result, "identity": digest(result)}

"""Retained August provider status observations with explicit new knowledge.

No source flag admits an entry, proves market-wide coverage, or fills prices.
The offline verifier reproduces each physical row from its local raw and dated
parents. Original acquisition strings and unknown provider acquisition remain
in the raw archive and safe evidence rather than becoming invented PIT clocks.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import THROUGH, _physical, digest, file_sha, load_contract, partition, schemas
from .base22_bounded import BoundedError, Budget, GlobalScan, semantic_scan
from .base22_calendar import audit_calendar_scope
from .base22_outputs import instant
from .base22_sources import SourceAssessmentError, _tree

CONTRACT_ID = "p10-base22-retained-august-status-observation-v1"
TABLE = "dwd_tradability_state"
MAX_DAY_ROWS = 8192


def status_observation(
    raw: dict[str, Any],
    *,
    market_date: date,
    listing: dict[str, Any],
    calendar: dict[str, Any],
    observed_at: datetime,
    source_sha256: str,
    revision: str,
    input_revision_digest: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Preserve the provider's two flags under the existing status mapping.

    The new warehouse observation is explicit. It must be later than genuine
    acquisition and within the parent's knowledge interval. It never changes a
    sealed state, invents a limit price, or claims historical acceptance.
    """
    if type(market_date) is not date or not date(2026, 8, 1) <= market_date <= THROUGH:
        raise BoundedError("STATUS_BUSINESS_SCOPE_INVALID")
    if raw.get("date") != str(market_date) or raw.get("market_date") != str(market_date):
        raise BoundedError("STATUS_RAW_DATE_DISAGREES")
    if raw.get("tradestatus") not in ("0", "1") or raw.get("isST") not in ("0", "1"):
        raise BoundedError("STATUS_SOURCE_FLAGS_UNAVAILABLE")
    code = raw.get("stock_code")
    if not isinstance(code, str) or re.fullmatch(r"[0-9]{6}", code) is None:
        raise BoundedError("STATUS_EXACT_SOURCE_CODE_REQUIRED")
    if not isinstance(raw.get("source"), str) or not raw["source"]:
        raise BoundedError("STATUS_RAW_SOURCE_REQUIRED")
    if not revision or re.fullmatch(r"sha256:[0-9a-f]{64}", source_sha256) is None:
        raise BoundedError("STATUS_EXACT_SOURCE_IDENTITY_REQUIRED")
    if re.fullmatch(r"sha256:[0-9a-f]{64}", input_revision_digest) is None:
        raise BoundedError("STATUS_INPUT_CLOSURE_REQUIRED")
    clock, acquired = instant(observed_at), instant(raw.get("acquired_at"))
    start = datetime.combine(market_date, time(), ZoneInfo("Asia/Shanghai"))
    if (
        listing["listing_code"] != code
        or listing["venue_id"] != calendar["venue_id"]
        or calendar["trade_date"] != market_date
        or calendar["session_type"] != "day"
    ):
        raise BoundedError("STATUS_DATE_CODE_VENUE_SESSION_MISMATCH")
    if not (
        listing["effective_from"] <= start
        and (listing["effective_to"] is None or start < listing["effective_to"])
    ):
        raise BoundedError("STATUS_LISTING_BUSINESS_DATE_INVALID")
    if acquired > clock:
        raise BoundedError("STATUS_ACQUISITION_AFTER_NEW_OBSERVATION")
    for parent in (listing, calendar):
        if not (
            parent["knowledge_from"] <= clock
            and (parent["knowledge_to"] is None or clock < parent["knowledge_to"])
        ):
            raise BoundedError("STATUS_PARENT_KNOWLEDGE_UNAVAILABLE")
    if type(calendar["is_open"]) is not bool:
        raise BoundedError("STATUS_CALENDAR_OPEN_FLAG_UNKNOWN")
    tradable = raw["tradestatus"] == "1"
    if tradable and not calendar["is_open"]:
        raise BoundedError("STATUS_SOURCE_CALENDAR_FLAG_CONFLICT")
    output = {
        "record_source": "p10_retained_august_status",
        "source_record_id": f"stock_code={code}&date={market_date}",
        "source_revision": revision,
        "source_artifact_sha256": source_sha256,
        "knowledge_from": clock,
        "knowledge_to": None,
        "observation_start": instant(start),
        "observation_end": instant(start + timedelta(days=1)),
        "listing_id": listing["listing_id"],
        "calendar_session_id": calendar["calendar_session_id"],
        "is_tradable": tradable,
        "can_buy": tradable,
        "can_sell": tradable,
        "reason_code": "SOURCE_NOT_TRADABLE"
        if not tradable
        else "ST_TRADABLE"
        if raw["isST"] == "1"
        else "NORMAL",
        "upper_price": None,
        "lower_price": None,
        "input_revision_digest": input_revision_digest,
    }
    proof = {
        "raw_row_sha256": digest(raw),
        "typed_output_sha256": digest(output),
        "listing_row_sha256": digest(listing),
        "calendar_row_sha256": digest(calendar),
        "market_date": str(market_date),
        "acquired_at": raw["acquired_at"],
        "provider_acquisition": None,
        "warehouse_observed_at": clock.isoformat(),
        "unknown_price_limits_preserved": True,
        "sealed_rows_mutated": 0,
        "historical_PIT_admission": False,
        "consumer_admission": "NOT_GRANTED",
    }
    return output, proof


def resolve_status_listing(
    raw: dict[str, Any],
    listings: list[dict[str, Any]],
    *,
    day: date,
    observed_at: datetime,
) -> dict[str, Any]:
    start = datetime.combine(day, time(), ZoneInfo("Asia/Shanghai"))
    clock = instant(observed_at)
    eligible = [
        r
        for r in listings
        if r["listing_code"] == raw.get("stock_code")
        and r["effective_from"] <= start
        and (r["effective_to"] is None or start < r["effective_to"])
        and r["knowledge_from"] <= clock
        and (r["knowledge_to"] is None or clock < r["knowledge_to"])
    ]
    if len(eligible) != 1:
        raise BoundedError("STATUS_EXACT_DATED_LISTING_NOT_UNIQUE")
    return eligible[0]


def project_status_day(
    raw_rows: list[dict[str, Any]],
    *,
    day: date,
    listings: list[dict[str, Any]],
    calendars: list[dict[str, Any]],
    observed_at: datetime,
    raw_sha256: str,
    revision: str,
    input_revision_digest: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Every source row gets an output or an explicit unqualified disposition.

    Invalid rows never become a default tradable state or disappear as a claim
    of market-wide coverage. A repeated source code/day fails the entire input.
    """
    if not raw_rows or len(raw_rows) > MAX_DAY_ROWS:
        raise BoundedError("STATUS_RAW_DAY_CENSUS_OR_BOUND_CHANGED")
    by_code: dict[str, list[dict[str, Any]]] = {}
    for listing in listings:
        by_code.setdefault(listing["listing_code"], []).append(listing)
    outputs, proofs, exceptions, seen = [], [], [], set()
    for raw in raw_rows:
        key = raw.get("stock_code")
        if not isinstance(key, str) or key in seen:
            raise BoundedError("STATUS_RAW_DUPLICATE_OR_UNKNOWN_CODE_DAY")
        seen.add(key)
        try:
            listing = resolve_status_listing(
                raw, by_code.get(key, []), day=day, observed_at=observed_at
            )
            matches = [
                c
                for c in calendars
                if c["venue_id"] == listing["venue_id"]
                and c["trade_date"] == day
                and c["session_type"] == "day"
            ]
            if len(matches) != 1:
                raise BoundedError("STATUS_EXACT_CALENDAR_KEY_NOT_UNIQUE")
            value, proof = status_observation(
                raw,
                market_date=day,
                listing=listing,
                calendar=matches[0],
                observed_at=observed_at,
                source_sha256=raw_sha256,
                revision=revision,
                input_revision_digest=input_revision_digest,
            )
        except (BoundedError, SourceAssessmentError) as exc:
            exceptions.append(
                {
                    "stock_code": key,
                    "market_date": str(day),
                    "raw_row_sha256": digest(raw),
                    "reason": str(exc),
                    "disposition": "UNQUALIFIED_NO_STATE_OUTPUT_NO_INFERRED_STATUS",
                    "consumer_admission": "NOT_GRANTED",
                }
            )
        else:
            outputs.append(value)
            proofs.append(proof)
    return outputs, proofs, exceptions


def audit_status_scope(root: Path, body: dict[str, Any], budget: Budget) -> dict[str, Any]:
    """Full offline raw/typed replay and global structural/temporal closure."""
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("projection_module_sha256") != file_sha(Path(__file__))
        or body.get("input_revision_digest") != digest(body.get("input_closure"))
        or body.get("purpose") != "NEW_PROVIDER_STATUS_OBSERVATIONS_NOT_MARKET_COVERAGE"
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("READY") is not False
        or body.get("complete_Base22") is not False
        or body.get("historical_PIT_admission") is not False
        or body.get("consumer_admission") != "NOT_GRANTED"
    ):
        raise BoundedError("STATUS_SCOPE_INVALID")
    if set(_tree(root)) != {*body["physical_members"], "QUALIFICATION.json"}:
        raise BoundedError("STATUS_EXACT_PHYSICAL_SET_CHANGED")
    for rel, item in body["physical_members"].items():
        p = _physical(root, rel, independent=True)
        if file_sha(p) != item["sha256"] or p.stat().st_size != item["size_bytes"]:
            raise BoundedError("STATUS_PHYSICAL_MEMBER_CHANGED")
        budget.check()
    contract, model, extension = load_contract(root / "contract")
    ss = schemas(model, extension)
    parents = body["parents"]
    required = {
        "dwd_listing",
        "dwd_trading_calendar",
        "dwd_security",
        "dwd_trading_venue",
        "dwd_listed_company",
    }
    if set(parents) != required:
        raise BoundedError("STATUS_PARENT_SET_INVALID")
    for t, paths in parents.items():
        for p in paths:
            if not pq.ParquetFile(root / p).schema_arrow.equals(ss[t], check_metadata=False):
                raise BoundedError("STATUS_PARENT_SCHEMA_CHANGED")
    cal_root = root / body["qualified_calendar_root"]
    cal_body = json.loads((cal_root / "QUALIFICATION.json").read_bytes())
    if cal_body["identity"] != body["calendar_scope_identity"]:
        raise BoundedError("STATUS_QUALIFIED_CALENDAR_IDENTITY_CHANGED")
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
        raise BoundedError("STATUS_PARENT_BOUND_OR_CALENDAR_CENSUS_FAILED")
    outputs, admitted, seen = [], set(), set()
    counts: Counter[str] = Counter()
    held: Counter[str] = Counter()
    for item in body["days"]:
        day = date.fromisoformat(item["market_date"])
        if day in seen:
            raise BoundedError("STATUS_DUPLICATE_SOURCE_DAY")
        seen.add(day)
        manifest = json.loads((root / item["manifest"]).read_bytes())
        if (root / item["READY"]).read_text().strip() != file_sha(root / item["manifest"]):
            raise BoundedError("STATUS_ORIGINAL_READY_CHANGED")
        artifacts = [
            a for a in manifest["artifacts"] if a["name"] == "daily_status_observation_delta"
        ]
        if (
            len(artifacts) != 1
            or artifacts[0]["sha256"] != item["raw_sha256"]
            or artifacts[0]["source_revision"] != item["source_revision"]
            or manifest["market_date"] != str(day)
        ):
            raise BoundedError("STATUS_ORIGINAL_SOURCE_CLOSURE_CHANGED")
        raw = pq.ParquetFile(root / item["raw"]).read().to_pylist()
        if not raw or len(raw) > MAX_DAY_ROWS or len(raw) != item["raw_rows"]:
            raise BoundedError("STATUS_RAW_DAY_CENSUS_OR_BOUND_CHANGED")
        generated, proofs, exceptions = project_status_day(
            raw,
            day=day,
            listings=listings,
            calendars=calendars,
            observed_at=instant(body["observed_at"]),
            raw_sha256=item["raw_sha256"],
            revision=item["source_revision"],
            input_revision_digest=body["input_revision_digest"],
        )
        counts.update(v["reason_code"] for v in generated)
        held.update(v["reason"] for v in exceptions)
        if digest(exceptions) != item["unqualified_dispositions_sha256"]:
            raise BoundedError("STATUS_EXACT_UNQUALIFIED_DISPOSITIONS_CHANGED")
        actual = pq.ParquetFile(root / item["output"]).read().to_pylist()
        if digest(generated) != digest(actual) or digest(proofs) != item["proof_sha256"]:
            raise BoundedError("STATUS_RAW_TO_TYPED_REPLAY_CHANGED")
        if any(partition(r, contract["tables"][TABLE]) != item["partition"] for r in actual):
            raise BoundedError("STATUS_PHYSICAL_PARTITION_CHANGED")
        summary = semantic_scan([root / item["output"]], ss[TABLE], check=budget.check)
        if summary != item["semantic_scan"]:
            raise BoundedError("STATUS_RECOMPUTED_SEMANTICS_CHANGED")
        admitted.add(("p10_retained_august_status", item["source_revision"], item["raw_sha256"]))
        outputs.append(root / item["output"])
        budget.check()
    expected_days = {c["trade_date"] for c in calendars if c["is_open"]}
    if seen != expected_days or len(seen) != 21:
        raise BoundedError("STATUS_EXACT_OPEN_DAY_SET_CHANGED")
    with GlobalScan(budget, read_only_roots=[root]) as scan:
        for t, paths in parents.items():
            scan.add(t, [root / p for p in paths])
        scan.add(TABLE, outputs)
        for t in [*sorted(parents), TABLE]:
            rule = contract["tables"][t]
            scan.keys(t, rule["business_key"], rule["version_key"])
            scan.row_rules(t, ss[t], rule)
        scan.lineage(TABLE, sorted(admitted))
        selected = {t for t in [*parents, TABLE]}
        scan.references(
            {"tables": [t for t in model["tables"] if t["name"] in selected]},
            {"tables": {t: contract["tables"][t] for t in selected}},
        )
    result = {
        "status": "FULL_LOCAL_STATUS_OBSERVATIONS_PASS_NOT_MARKET_COVERAGE_OR_BASE22",
        "scope_identity": body["identity"],
        "observed_days": len(seen),
        "output_rows": sum(counts.values()),
        "safe_status_counts": dict(counts),
        "unqualified_source_rows": dict(held),
        "raw_and_output_hashes_unchanged": True,
        "provider_acquisition_inferred": False,
        "missing_listing_or_price_coverage_filled": False,
        "historical_PIT_admission": False,
        "consumer_admission": "NOT_GRANTED",
        "complete_Base22": False,
    }
    result["identity"] = digest(result)
    return result

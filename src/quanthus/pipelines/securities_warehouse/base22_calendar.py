"""Bounded August calendar observations from frozen existing ODS members.

The provider contract mirrors one shared A-share calendar across three venues.
This projection preserves that evidence grade: it does not invent separate
exchange responses, source acquisition, PIT availability or session hours.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import THROUGH, _physical, digest, file_sha, load_contract, partition, schemas
from .base22_bounded import BoundedError, Budget, GlobalScan, semantic_scan
from .base22_financial import _clock
from .base22_sources import _tree
from .disclosure_event import _stable_id

CONTRACT_ID = "p10-base22-shared-calendar-observation-v1"
EXCHANGE_MIC = {"SSE": "XSHG", "SZSE": "XSHE", "BSE": "BJSE"}
AUGUST_START = date(2026, 8, 1)


def calendar_union(
    sources: Iterable[tuple[str, list[dict[str, Any]]]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Compose exact retained fragments; differing flags cannot be deduplicated.

    An earlier archived built_at orders otherwise identical observations only.
    Its timestamp is retained as warehouse evidence, never provider acquisition.
    """
    selected: dict[tuple[str, date, str], tuple[str, dict[str, Any]]] = {}
    seen_members = set()
    overlap = 0
    for sha, rows in sources:
        if re.fullmatch(r"sha256:[0-9a-f]{64}", sha) is None or sha in seen_members:
            raise BoundedError("CALENDAR_UNION_EXACT_MEMBER_IDENTITY_REQUIRED")
        seen_members.add(sha)
        local = set()
        for raw in rows:
            day = raw["trade_date"]
            if type(day) is not date:
                raise BoundedError("CALENDAR_EXACT_BUSINESS_DATE_REQUIRED")
            if not AUGUST_START <= day <= THROUGH:
                continue
            key = raw["exchange"], day, raw["session"]
            if (
                key in local
                or raw["exchange"] not in EXCHANGE_MIC
                or raw["session"] != "day"
                or type(raw["is_open"]) is not int
                or raw["is_open"] not in (0, 1)
                or raw["source"] != "baostock.query_trade_dates"
            ):
                raise BoundedError("CALENDAR_UNION_RAW_KEY_FLAG_OR_SOURCE_INVALID")
            local.add(key)
            clock = _clock(raw["built_at"])
            if key in selected:
                prev_sha, prev = selected[key]
                if (prev["is_open"], prev["source"]) != (raw["is_open"], raw["source"]):
                    raise BoundedError("CALENDAR_UNION_CONFLICTING_OBSERVATIONS")
                overlap += 1
                if (clock, sha) >= (_clock(prev["built_at"]), prev_sha):
                    continue
            selected[key] = sha, raw
    required = {
        (ex, AUGUST_START + timedelta(days=n), "day") for ex in EXCHANGE_MIC for n in range(31)
    }
    if set(selected) != required:
        raise BoundedError("CALENDAR_UNION_COMPLETE_93_KEYS_REQUIRED")
    keys = sorted(selected)
    out = [selected[k][1] for k in keys]
    proof = {
        "complete_members": sorted(seen_members),
        "same_flag_overlap_observations": overlap,
        "exact_union_rows": len(out),
        "exact_union_row_sha256": digest(out),
        "origins": [
            {
                "exchange": k[0],
                "trade_date": k[1],
                "session": k[2],
                "original_member_sha256": selected[k][0],
                "raw_row_sha256": digest(selected[k][1]),
            }
            for k in keys
        ],
        "source_conflict_priority_granted": False,
        "provider_acquisition_inferred": False,
    }
    proof["identity"] = digest(proof)
    return out, proof


def audit_calendar_scope(root: Path, body: dict[str, Any], budget: Budget) -> dict[str, Any]:
    """Replay all selected local fragments and the one typed physical calendar."""
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("purpose") != "SHARED_CALENDAR_NEW_OBSERVATION_NOT_INDEPENDENT_EXCHANGE_PIT"
        or body.get("complete_Base22") is not False
        or body.get("READY") is not False
        or body.get("historical_PIT_admission") is not False
        or body.get("consumer_admission") != "NOT_GRANTED"
    ):
        raise BoundedError("CALENDAR_SCOPE_INVALID")
    if set(_tree(root)) != {*body["physical_members"], "QUALIFICATION.json"}:
        raise BoundedError("CALENDAR_EXACT_PHYSICAL_SET_CHANGED")
    for rel, item in body["physical_members"].items():
        p = _physical(root, rel, independent=True)
        if file_sha(p) != item["sha256"] or p.stat().st_size != item["size_bytes"]:
            raise BoundedError("CALENDAR_PHYSICAL_MEMBER_CHANGED")
        budget.check()

    def fragments() -> Iterable[tuple[str, list[dict[str, Any]]]]:
        for source in body["sources"]:
            manifest = _physical(root, source["local_manifest"], independent=True)
            ready = _physical(root, source["local_READY"], independent=True)
            if ready.read_text().strip() != file_sha(manifest):
                raise BoundedError("CALENDAR_ORIGINAL_READY_IDENTITY_CHANGED")
            meta = json.loads(manifest.read_bytes())
            matches = [a for a in meta["artifacts"] if a["name"] == "trading_calendar"]
            if len(matches) != 1 or matches[0]["sha256"] != source["raw_sha256"]:
                raise BoundedError("CALENDAR_ORIGINAL_ARTIFACT_CLOSURE_CHANGED")
            p = _physical(root, source["local_raw"], independent=True)
            if file_sha(p) != source["raw_sha256"]:
                raise BoundedError("CALENDAR_ORIGINAL_RAW_CHANGED")
            yield source["raw_sha256"], pq.ParquetFile(p).read().to_pylist()
            budget.check()

    union, union_proof = calendar_union(fragments())
    union_path = _physical(root, body["union_path"], independent=True)
    stored_union = pq.ParquetFile(union_path).read().to_pylist()
    if digest(union) != digest(stored_union) or digest(union_proof) != digest(body["union_proof"]):
        raise BoundedError("CALENDAR_EXACT_RAW_UNION_OR_ORIGIN_CHANGED")
    venue_path = _physical(root, body["venue_path"], independent=True)
    venues = pq.ParquetFile(venue_path).read().to_pylist()
    expected, proof = calendar_rows(
        union,
        venues,
        observed_at=body["observed_at"],
        source_sha256=file_sha(union_path),
        revision=body["revision"],
    )
    if digest(proof) != digest(body["projection_proof"]):
        raise BoundedError("CALENDAR_EXACT_PROJECTION_PROOF_CHANGED")
    contract, model, extension = load_contract(root / "contract")
    ss = schemas(model, extension)
    if not pq.ParquetFile(venue_path).schema_arrow.equals(
        ss["dwd_trading_venue"], check_metadata=False
    ):
        raise BoundedError("CALENDAR_NATIVE_VENUE_SCHEMA_CHANGED")
    table = "dwd_trading_calendar"
    item = body["output"]
    path = _physical(root, item["path"], independent=True)
    actual = pq.ParquetFile(path).read().to_pylist()
    if digest(actual) != digest(expected) or len(actual) != 93:
        raise BoundedError("CALENDAR_TYPED_RAW_REPLAY_CHANGED")
    if any(partition(r, contract["tables"][table]) != item["partition"] for r in actual):
        raise BoundedError("CALENDAR_EXACT_PHYSICAL_PARTITION_CHANGED")
    summary = semantic_scan([path], ss[table], check=budget.check)
    if summary != item["semantic_scan"]:
        raise BoundedError("CALENDAR_TYPED_SUMMARY_CHANGED")
    with GlobalScan(budget, read_only_roots=[root]) as scan:
        scan.add(table, [path])
        scan.keys(
            table,
            contract["tables"][table]["business_key"],
            contract["tables"][table]["version_key"],
        )
        scan.row_rules(table, ss[table], contract["tables"][table])
        scan.lineage(
            table,
            sorted(
                {
                    (r["record_source"], r["source_revision"], r["source_artifact_sha256"])
                    for r in actual
                }
            ),
        )
        scan.add("dwd_trading_venue", [root / body["venue_path"]])
        scan.reject(
            """SELECT 1 FROM dwd_trading_calendar c WHERE NOT EXISTS(
                SELECT 1 FROM dwd_trading_venue v WHERE v.venue_id=c.venue_id
                AND v.knowledge_from<=c.knowledge_from
                AND (v.knowledge_to IS NULL OR c.knowledge_from<v.knowledge_to)) LIMIT 1""",
            "CALENDAR_VENUE_KNOWLEDGE_REFERENCE_CHANGED",
        )
    result = {
        "status": "FULL_LOCAL_SHARED_CALENDAR_OBSERVATION_PASS_NOT_EXCHANGE_PIT_OR_BASE22",
        "scope_identity": body["identity"],
        "source_fragments": len(body["sources"]),
        "exact_venue_day_rows": 93,
        "semantic_scan": summary,
        "union_identity": union_proof["identity"],
        "unknown_provider_acquisition_preserved": True,
        "unknown_session_hours_preserved": True,
        "complete_Base22": False,
    }
    result["identity"] = digest(result)
    return result


def calendar_rows(
    raw_rows: list[dict[str, Any]],
    venues: list[dict[str, Any]],
    *,
    observed_at: datetime,
    source_sha256: str,
    revision: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Materialize all 93 venue/day keys at the real new governance clock.

    The caller freezes the exact union with physical original member pointers
    and verifies all selected calendar members' overlap. built_at remains an archived warehouse
    clock; no provider acquisition or original exchange publication is inferred.
    """
    clock = _clock(observed_at)
    by_mic = {}
    for venue in venues:
        mic = venue["mic_code"]
        if mic in by_mic or mic not in EXCHANGE_MIC.values():
            raise BoundedError("CALENDAR_EXACT_VENUE_IDENTITY_REQUIRED")
        if venue["knowledge_from"] > clock or (
            venue["knowledge_to"] is not None and clock >= venue["knowledge_to"]
        ):
            raise BoundedError("CALENDAR_VENUE_KNOWLEDGE_UNAVAILABLE")
        by_mic[mic] = venue
    if set(by_mic) != set(EXCHANGE_MIC.values()):
        raise BoundedError("CALENDAR_ALL_THREE_VENUES_REQUIRED")
    if re.fullmatch(r"sha256:[0-9a-f]{64}", source_sha256) is None:
        raise BoundedError("CALENDAR_SOURCE_IDENTITY_REQUIRED")
    if not revision:
        raise BoundedError("CALENDAR_SOURCE_REVISION_REQUIRED")
    keys = set()
    out = []
    built = set()
    for raw in raw_rows:
        day = raw["trade_date"]
        if type(day) is not date:
            raise BoundedError("CALENDAR_EXACT_BUSINESS_DATE_REQUIRED")
        if not AUGUST_START <= day <= THROUGH:
            continue
        exchange = raw["exchange"]
        key = exchange, day, raw["session"]
        if (
            exchange not in EXCHANGE_MIC
            or raw["session"] != "day"
            or type(raw["is_open"]) is not int
            or raw["is_open"] not in (0, 1)
            or raw["source"] != "baostock.query_trade_dates"
            or key in keys
        ):
            raise BoundedError("CALENDAR_SOURCE_FLAG_SESSION_OR_DUPLICATE_INVALID")
        raw_clock = _clock(raw["built_at"])
        if raw_clock >= clock:
            raise BoundedError("CALENDAR_NEW_OBSERVATION_NOT_AFTER_ARCHIVED_SOURCE")
        keys.add(key)
        built.add(raw_clock)
        venue = by_mic[EXCHANGE_MIC[exchange]]
        start = datetime.combine(day, time(), ZoneInfo("Asia/Shanghai"))
        out.append(
            {
                "record_source": "p10_shared_a_share_calendar",
                "source_record_id": f"exchange={exchange}&trade_date={day}&session=day",
                "source_revision": revision,
                "source_artifact_sha256": source_sha256,
                "knowledge_from": clock,
                "knowledge_to": None,
                "observation_start": start,
                "observation_end": start + timedelta(days=1),
                "calendar_session_id": _stable_id(
                    "calendar_session", venue["venue_id"], str(day), "day"
                ),
                "venue_id": venue["venue_id"],
                "trade_date": day,
                "session_type": "day",
                "is_open": bool(raw["is_open"]),
                "open_at": None,
                "close_at": None,
                "calendar_reason": None if raw["is_open"] else "SCHEDULED_CLOSED",
            }
        )
    required = {
        (exchange, AUGUST_START + timedelta(days=n), "day")
        for exchange in EXCHANGE_MIC
        for n in range(31)
    }
    if keys != required:
        raise BoundedError("CALENDAR_COMPLETE_AUGUST_93_KEYS_REQUIRED")
    out.sort(key=lambda r: (r["venue_id"], r["trade_date"]))
    proof = {
        "contract_id": CONTRACT_ID,
        "source_member_sha256": source_sha256,
        "source_revision": revision,
        "selected_raw_rows_sha256": digest(
            [r for r in raw_rows if AUGUST_START <= r["trade_date"] <= THROUGH]
        ),
        "rows": len(out),
        "output_rows_sha256": digest(out),
        "open_counts_by_MIC": {
            mic: sum(r["is_open"] for r in out if r["venue_id"] == v["venue_id"])
            for mic, v in sorted(by_mic.items())
        },
        "original_warehouse_built_at_instants": sorted(built),
        "observed_at": clock,
        "provider_acquisition": None,
        "source_evidence_model": "shared_a_share_calendar_mirrored_to_exchanges",
        "independent_exchange_original_responses": "NOT_ASSERTED",
        "session_hours_inferred": False,
        "historical_PIT_admission": False,
        "consumer_admission": "NOT_GRANTED",
    }
    proof["identity"] = digest(proof)
    return out, proof

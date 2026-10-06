"""Bounded August calendar observations from frozen existing ODS members.

The provider contract mirrors one shared A-share calendar across three venues.
This projection preserves that evidence grade: it does not invent separate
exchange responses, source acquisition, PIT availability or session hours.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import THROUGH, digest
from .base22_bounded import BoundedError
from .base22_financial import _clock
from .disclosure_event import _stable_id

CONTRACT_ID = "p10-base22-shared-calendar-observation-v1"
EXCHANGE_MIC = {"SSE": "XSHG", "SZSE": "XSHE", "BSE": "BJSE"}
AUGUST_START = date(2026, 8, 1)


def calendar_rows(
    raw_rows: list[dict[str, Any]],
    venues: list[dict[str, Any]],
    *,
    observed_at: datetime,
    source_sha256: str,
    revision: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Materialize all 93 venue/day keys at the real new governance clock.

    The caller freezes one actual complete August member and verifies the other
    selected calendar members' overlap. built_at remains an archived warehouse
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
    if not isinstance(source_sha256, str) or not source_sha256.startswith("sha256:"):
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

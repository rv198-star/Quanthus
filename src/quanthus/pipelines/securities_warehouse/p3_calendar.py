"""P10's bounded official-schedule reconciliation for P3's exact 2024 warmup.

This establishes declared session membership, not the availability of historical
market values or an as-of view. Acquisition time stays separate from the date
printed on the official notice. It never fetches sources or changes DWD clocks.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from html import unescape
from typing import Any

from quanthus.expert_evaluation.serialization import digest_value

URLS = {
    "XSHG": "https://www.sse.com.cn/disclosure/announcement/general/c/c_20231226_5733939.shtml",
    "XSHE": "https://www.szse.cn/disclosure/notice/t20231226_605108.html",
}
ISSUERS = {"XSHG": "上海证券交易所", "XSHE": "深圳证券交易所"}
HOLIDAYS = ("元旦", "春节", "清明节", "劳动节", "端午节", "中秋节", "国庆节")
_SHA = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _closed_dates(body: bytes, issuer: str) -> set[date]:
    if not body or len(body) > 2 * 1024**2:
        raise ValueError("P3_CALENDAR_NOTICE_SIZE")
    text = re.sub(r"\s+", "", unescape(re.sub(r"<[^>]+>", " ", body.decode("utf-8"))))
    if any(s not in text for s in (issuer, "2024年", "2023年12月26日", "周末休市")):
        raise ValueError("P3_CALENDAR_NOTICE_IDENTITY")
    closed: set[date] = set()
    for holiday in HOLIDAYS:
        found = re.findall(holiday + r"[：:](.*?)休市", text)
        if len(found) != 1:
            raise ValueError("P3_CALENDAR_HOLIDAY_CLAUSE")
        dates = re.findall(r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日", found[0])
        if not 1 <= len(dates) <= 2:
            raise ValueError("P3_CALENDAR_HOLIDAY_DATES")
        parsed = [date(int(y or 2024), int(m), int(d)) for y, m, d in dates]
        first, last = parsed[0], parsed[-1]
        if first > last or (last - first).days > 15:
            raise ValueError("P3_CALENDAR_HOLIDAY_RANGE")
        day = first
        while day <= last:
            if day.year == 2024:
                closed.add(day)
            day += timedelta(days=1)
    return closed


def bind_warmup_calendar(
    documents: Mapping[str, Mapping[str, Any]],
    records: Iterable[Mapping[str, Any]],
    *,
    source_manifest_sha256: str,
    calendar_file_sha256: str,
    venue_file_sha256: str,
) -> dict[str, Any]:
    """Compare the full native year, then freeze its exact last 60 common opens.

    Callers verify these exact native file hashes before projecting metadata.
    Duplicate native versions must be reconciled explicitly by P10 beforehand;
    this function neither chooses the latest version nor deduplicates silently.
    """
    if set(documents) != set(URLS) or not all(
        isinstance(s, str) and _SHA.fullmatch(s)
        for s in (source_manifest_sha256, calendar_file_sha256, venue_file_sha256)
    ):
        raise ValueError("P3_CALENDAR_SOURCE_BINDING")
    declarations = {}
    closures = []
    for mic, url in URLS.items():
        item = documents[mic]
        if item.get("url") != url or not isinstance(item.get("body"), bytes):
            raise ValueError("P3_CALENDAR_OFFICIAL_SOURCE_REQUIRED")
        closures.append(_closed_dates(item["body"], ISSUERS[mic]))
        declarations[mic] = {
            "url": url,
            "sha256": "sha256:" + hashlib.sha256(item["body"]).hexdigest(),
            "publication_date_declared": "2023-12-26",
            "publication_instant": None,
            "evidence_grade": "official_declared_schedule_retrieved_later",
        }
    if closures[0] != closures[1]:
        raise ValueError("P3_CALENDAR_EXCHANGE_SCHEDULE_CONFLICT")
    days = tuple(date(2024, 1, 1) + timedelta(days=i) for i in range(366))
    opened = tuple(d for d in days if d.weekday() < 5 and d not in closures[0])
    by_key: dict[tuple[str, date], str] = {}
    ids: set[str] = set()
    warehouse_clocks = set()
    for row in records:
        native_mic, day = row.get("mic_code"), row.get("trade_date")
        if (
            not isinstance(native_mic, str)
            or native_mic not in URLS
            or type(day) is not date
            or day.year != 2024
        ):
            raise ValueError("P3_CALENDAR_NATIVE_SCOPE")
        mic = native_mic
        session = row.get("calendar_session_id")
        if (
            (mic, day) in by_key
            or not isinstance(session, str)
            or not session.strip()
            or session in ids
            or type(row.get("is_open")) is not bool
        ):
            raise ValueError("P3_CALENDAR_NATIVE_IDENTITY")
        if row["is_open"] != (day in opened):
            raise ValueError("P3_CALENDAR_NATIVE_SCHEDULE_DIFFERS")
        by_key[mic, day] = session
        ids.add(session)
        warehouse_clocks.add(str(row.get("knowledge_from")))
    if set(by_key) != {(mic, d) for mic in URLS for d in days}:
        raise ValueError("P3_CALENDAR_NATIVE_YEAR_INCOMPLETE")
    sessions = [
        {"trade_date": d.isoformat(), **{m: by_key[m, d] for m in URLS}} for d in opened[-60:]
    ]
    evidence = {
        "schema_version": "quanthus.p10-p3-calendar-membership-evidence.v1",
        "source_manifest_sha256": source_manifest_sha256,
        "calendar_file_sha256": calendar_file_sha256,
        "venue_file_sha256": venue_file_sha256,
        "declarations": declarations,
        "native_year_rows": len(by_key),
        "common_open_dates": len(opened),
        "original_warehouse_knowledge_values": sorted(warehouse_clocks),
        "sessions": sessions,
    }
    return {
        "evidence": evidence,
        "binding": {
            "schema_version": "quanthus.p3-warmup-calendar.v1",
            "authority_reference": "P10 #474 / official SSE+SZSE 2024 declared schedule",
            "authority_evidence_sha256": digest_value(evidence),
            "sessions": sessions,
        },
        "membership_status": "MATCHES_OFFICIAL_DECLARED_SCHEDULE",
        "warehouse_knowledge_unchanged": True,
        "native_asof_warmup_accepted": False,
        "historical_market_value_availability_qualified": False,
        "B_accepted": False,
        "D_activated": False,
    }

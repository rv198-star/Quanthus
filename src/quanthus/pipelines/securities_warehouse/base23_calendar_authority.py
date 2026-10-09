"""Offline, date-only authority supplement for the retained August/September calendar.

Annual exchange notices qualify business dates. They do not recover old internal
acquisition, qualify listing coverage, assert session hours or admit a consumer.
The sealed Base 2.3 and its earlier receipts remain unchanged.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_financial import _clock

CONTRACT_ID = "p10-base23-calendar-date-authority-supplement-v1"
START = date(2026, 8, 1)
THROUGH = date(2026, 9, 30)
_ISSUERS = {
    "SSE": ("www.sse.com.cn", "上海证券交易所", "上证公告〔2025〕45号"),
    "SZSE": ("www.szse.cn", "深圳证券交易所", "深证会〔2025〕481号"),
    "BSE": ("www.bse.cn", "北京证券交易所", "北证公告〔2025〕58号"),
}
_HOLIDAYS = {"元旦", "春节", "清明节", "劳动节", "端午节", "中秋节", "国庆节"}
_MIC_VENUES = {"XSHG": "SSE", "XSHE": "SZSE", "BJSE": "BSE"}


class _NoticeText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def derive_date_authority(
    config: Mapping[str, Any], documents: Mapping[str, bytes],
) -> dict[str, Any]:
    """Parse complete pinned first-party notices without opening a source path."""
    if (
        config.get("contract_id") != CONTRACT_ID
        or config.get("business_start") != str(START)
        or config.get("business_through") != str(THROUGH)
        or config.get("timezone") != "Asia/Shanghai"
        or config.get("claim") != "EXCHANGE_BUSINESS_DATES_ONLY"
        or set(config.get("documents", {})) != set(_ISSUERS)
        or set(documents) != set(_ISSUERS)
    ):
        raise BoundedError("CALENDAR_AUTHORITY_SCOPE_OR_DOCUMENT_SET_INVALID")
    observed = _clock(config["observed_at"])
    rows = []
    sources = []
    for venue, (host, issuer, notice_id) in sorted(_ISSUERS.items()):
        item = config["documents"][venue]
        uri = urlparse(item["url"])
        body = documents[venue]
        retrieved = _clock(item["retrieved_at"])
        if (
            uri.scheme != "https" or uri.netloc != host
            or uri.username is not None or uri.password is not None
            or len(body) > 2 * 1024 * 1024 or not body
            or "sha256:" + hashlib.sha256(body).hexdigest() != item["sha256"]
            or item.get("publication_date_literal") != "2025-12-22"
            or retrieved > observed or retrieved.date() < date(2025, 12, 22)
        ):
            raise BoundedError("CALENDAR_AUTHORITY_ORIGINAL_DOCUMENT_INVALID")
        parser = _NoticeText()
        try:
            parser.feed(body.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise BoundedError("CALENDAR_AUTHORITY_TEXT_UNREADABLE") from exc
        text = re.sub(r"\s+", "", "".join(parser.parts)).replace("：", ":")
        if not all(token in text for token in [
            issuer, notice_id, "2026年部分节假日休市安排", "2025年12月22日",
        ]):
            raise BoundedError("CALENDAR_AUTHORITY_PUBLISHER_OR_REVISION_INVALID")
        if text.count("一、休市安排") != 1 or "二、" not in text:
            raise BoundedError("CALENDAR_AUTHORITY_COMPLETE_ANNUAL_NOTICE_REQUIRED")
        section = text.split("一、休市安排", 1)[1].split("二、", 1)[0]
        blocks = re.findall(r"（[一二三四五六七]）([^:]+):(.*?)(?=（[一二三四五六七]）|$)", section)
        if len(blocks) != 7 or {name for name, _ in blocks} != _HOLIDAYS:
            raise BoundedError("CALENDAR_AUTHORITY_COMPLETE_ANNUAL_NOTICE_REQUIRED")
        closed: set[date] = set()
        periods = []
        for name, block in blocks:
            match = re.search(
                r"(\d{1,2})月(\d{1,2})日（[^）]+）至(\d{1,2})月(\d{1,2})日（[^）]+）休市",
                block,
            )
            if match is None:
                raise BoundedError("CALENDAR_AUTHORITY_HOLIDAY_PERIOD_UNPARSEABLE")
            try:
                month1, day1, month2, day2 = map(int, match.groups())
                first, last = date(2026, month1, day1), date(2026, month2, day2)
            except ValueError as exc:
                raise BoundedError("CALENDAR_AUTHORITY_HOLIDAY_DATE_INVALID") from exc
            if not 0 <= (last - first).days <= 14:
                raise BoundedError("CALENDAR_AUTHORITY_HOLIDAY_DATE_INVALID")
            periods.append({"holiday": name, "first": str(first), "last": str(last)})
            closed.update(first + timedelta(days=i) for i in range((last - first).days + 1))
        for i in range((THROUGH - START).days + 1):
            day = START + timedelta(days=i)
            rows.append({"exchange_code": venue, "trade_date": str(day),
                         "is_open": day.weekday() < 5 and day not in closed,
                         "source_document_sha256": item["sha256"]})
        sources.append({"exchange_code": venue, "url": item["url"],
                        "document_sha256": item["sha256"], "notice_id": notice_id,
                        "publication_date_literal": "2025-12-22",
                        "retrieved_at": item["retrieved_at"], "holiday_periods": periods})
    result = {"contract_id": CONTRACT_ID, "business_start": str(START),
              "business_through": str(THROUGH), "timezone": "Asia/Shanghai",
              "observed_at": config["observed_at"], "sources": sources, "rows": rows,
              "config_identity": digest(config), "historical_internal_acquisition_recovered": False,
              "listing_coverage_qualified": False, "session_hours_qualified": False,
              "consumer_admission": "NOT_GRANTED", "sealed_image_changed": False}
    result["identity"] = digest(result)
    return result


def compare_retained_calendar(
    authority: Mapping[str, Any], calendar: Iterable[Mapping[str, Any]],
    venues: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compare every retained knowledge version; validate exact business-date closure."""
    by_id = {}
    for row in venues:
        if (row["venue_id"] in by_id or row.get("mic_code") not in _MIC_VENUES
                or row.get("timezone") != "Asia/Shanghai"):
            raise BoundedError("CALENDAR_AUTHORITY_VENUE_IDENTITY_INVALID")
        by_id[row["venue_id"]] = _MIC_VENUES[row["mic_code"]]
    if set(by_id.values()) != set(_ISSUERS) or len(by_id) != 3:
        raise BoundedError("CALENDAR_AUTHORITY_VENUE_IDENTITY_INVALID")
    expected = {(r["exchange_code"], r["trade_date"]): r["is_open"] for r in authority["rows"]}
    if len(expected) != len(authority["rows"]) or len(expected) != 183:
        raise BoundedError("CALENDAR_AUTHORITY_DATE_SET_INVALID")
    seen: set[tuple[str, str]] = set()
    count = 0
    for row in calendar:
        day = date.fromisoformat(str(row["trade_date"]))
        if not START <= day <= THROUGH:
            continue
        if (row.get("session_type") not in {"REGULAR_DAY", "day"}
                or row.get("venue_id") not in by_id):
            raise BoundedError("CALENDAR_AUTHORITY_REFERENCED_VENUE_OR_SESSION_INVALID")
        key = by_id[row["venue_id"]], str(day)
        if type(row.get("is_open")) is not bool or row["is_open"] != expected[key]:
            raise BoundedError("CALENDAR_AUTHORITY_DATE_FLAG_CONFLICT")
        seen.add(key)
        count += 1
        if count > 4096:
            raise BoundedError("CALENDAR_AUTHORITY_VERSION_CENSUS_BOUND")
    if seen != set(expected):
        raise BoundedError("CALENDAR_AUTHORITY_REQUIRED_MIRROR_DATE_MISSING")
    result = {"status": "AUTHORITATIVE_DATE_SCOPE_PASS",
              "authority_identity": authority["identity"],
              "calendar_versions_verified": count, "distinct_venue_dates": len(seen),
              "conflicting_flags": 0, "original_times_changed": False,
              "historical_internal_acquisition_recovered": False,
              "consumer_admission": "NOT_GRANTED"}
    result["identity"] = digest(result)
    return result

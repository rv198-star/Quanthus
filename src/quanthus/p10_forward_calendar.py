"""Pinned 2026 exchange dates for the independent P12 calendar product (#743).

This offline builder consumes captured official documents and their real receipt
clocks. It never fetches quotes, extends a historical calendar, or guesses 2027.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

PRODUCT = "p10-forward-exchange-calendar-v1"
START, END = date(2026, 11, 24), date(2026, 12, 31)
# Weekly clauses were read from these exact official originals. The annual
# notices are parsed independently; source equality is never a venue proxy.
SOURCES = {
    "SSE-annual.html": {
        "venue": "SSE",
        "kind": "ANNUAL_CLOSURES",
        "publication_date": "2025-12-22",
        "url": "https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml",
        "sha256": "6414ab8f4316c318d816b68aefc4843a5ea87ec75d543f614e3e53aa7688f33c",
        "notice_id": "上证公告〔2025〕45号",
    },
    "SZSE-annual.html": {
        "venue": "SZSE",
        "kind": "ANNUAL_CLOSURES",
        "publication_date": "2025-12-22",
        "url": "https://www.szse.cn/disclosure/notice/general/t20251222_618087.html",
        "sha256": "1656d8708d5faed33c7588538639d96128f39fdface02cfa6eb66d796c40f14e",
        "notice_id": "深证会〔2025〕481号",
    },
    "SSE-weekly.docx": {
        "venue": "SSE",
        "kind": "WEEKLY_RULE",
        "publication_date": "2026-04-24",
        "url": "https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/10816482/files/e3a3b98e36894edca77841837936e006.docx",
        "sha256": "fc922c433438b2636cb631eab25cca405209712acbb6aaded768c45456ff8888",
        "rule_clause": "2.4.1",
        "effective_date": "2026-07-06",
    },
    "SZSE-weekly.pdf": {
        "venue": "SZSE",
        "kind": "WEEKLY_RULE",
        "publication_date": "2026-04-24",
        "url": "https://docs.static.szse.cn/www/lawrules/rule/trade/current/W020260424690713155663.pdf",
        "sha256": "9b66f8b0db70f84a25ef1ccb4ee2351001724e408117552d75f6d8993483c586",
        "rule_clause": "2.3.1",
        "effective_date": "2026-07-06",
    },
}


class CalendarError(ValueError):
    """Source or product identity failed."""


def encoded(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _clock(value: object) -> datetime:
    if not isinstance(value, str):
        raise CalendarError("INVALID_CLOCK")
    try:
        clock = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as exc:
        raise CalendarError("INVALID_CLOCK") from exc
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise CalendarError("CLOCK_TIMEZONE_REQUIRED")
    return clock


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def annual_closures(body: bytes, notice_id: str) -> set[date]:
    parser = _Text()
    parser.feed(body.decode("utf-8"))
    text = re.sub(r"\s+", "", "".join(parser.parts))
    if notice_id not in text or "2026年部分节假日休市安排" not in text:
        raise CalendarError("ANNUAL_NOTICE_IDENTITY")
    if text.count("一、休市安排") != 1:
        raise CalendarError("INCOMPLETE_ANNUAL_NOTICE")
    section = text.split("一、休市安排")[1].split("二、")[0]
    periods = re.findall(
        r"(\d{1,2})月(\d{1,2})日（[^）]+）至(\d{1,2})月(\d{1,2})日（[^）]+）休市",
        section,
    )
    if len(periods) != 7:
        raise CalendarError("INCOMPLETE_ANNUAL_NOTICE")
    closed: set[date] = set()
    for m1, d1, m2, d2 in periods:
        first, last = date(2026, int(m1), int(d1)), date(2026, int(m2), int(d2))
        if not 0 <= (last - first).days <= 14:
            raise CalendarError("INVALID_CLOSURE_INTERVAL")
        closed.update(first + timedelta(days=i) for i in range((last - first).days + 1))
    return closed


def date_payload(first: date, last: date, closed: set[date]) -> dict[str, Any]:
    """Apply the pinned Mon-Fri rule only inside this rule's 2026 scope."""
    if not date(2026, 7, 6) <= first <= last <= date(2026, 12, 31):
        raise CalendarError("RULE_OR_YEAR_OUTSIDE_COVERAGE")
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    sessions = [day for day in days if day.weekday() < 5 and day not in closed]
    next_dates = {str(day): next((str(s) for s in sessions if s > day), None) for day in days}
    return {
        "status": "AVAILABLE",
        "sessions": [str(day) for day in sessions],
        "next_session": next_dates,
        "next_session_unavailable": {
            day: "NEXT_SESSION_OUTSIDE_COVERAGE"
            for day, value in next_dates.items()
            if value is None
        },
    }


def build_calendar(
    documents: dict[str, bytes],
    receipts: list[dict[str, Any]],
    *,
    built_at: str,
    code_revision: str,
) -> dict[str, Any]:
    """Retain unknown publisher clocks; receipt time qualifies current observation."""
    built = _clock(built_at)
    if re.fullmatch(r"[0-9a-f]{40}", code_revision) is None:
        raise CalendarError("FULL_CODE_REVISION_REQUIRED")
    indexed = {row["path"]: row for row in receipts}
    if len(indexed) != len(receipts) or set(documents) != set(SOURCES):
        raise CalendarError("SOURCE_INVENTORY_MISMATCH")
    sources = []
    for name, spec in SOURCES.items():
        row = indexed.get(name, {})
        body = documents[name]
        if (
            not body
            or len(body) > 2 * 1024 * 1024
            or sha256(body) != spec["sha256"]
            or row.get("sha256") != spec["sha256"]
            or row.get("http_status") != 200
            or row.get("url") != spec["url"]
            or row.get("final_url") != spec["url"]
            or row.get("size_bytes") != len(body)
        ):
            raise CalendarError("SOURCE_IDENTITY_MISMATCH")
        if not (_clock(row.get("requested_at")) <= _clock(row.get("received_at")) <= built):
            raise CalendarError("SOURCE_RECEIPT_CLOCK_ORDER")
        if _clock(row["received_at"]).date() < date.fromisoformat(spec["publication_date"]):
            raise CalendarError("SOURCE_RECEIPT_PRECEDES_PUBLICATION_DATE")
        sources.append(
            {
                **spec,
                "path": "sources/" + name,
                "size_bytes": len(body),
                "source_published_at": None,
                "source_available_at": None,
                "unknown_clock_reason": "OFFICIAL_PAGE_HAS_DATE_ONLY",
                "source_retrieved_at": row["received_at"],
                "source_receipt_sha256": sha256(encoded(row)),
            }
        )
    venues = {}
    for venue in ("SSE", "SZSE"):
        name = venue + "-annual.html"
        closed = annual_closures(documents[name], SOURCES[name]["notice_id"])
        venues[venue] = date_payload(START, END, closed)
        venues[venue]["source_sha256"] = [s["sha256"] for s in sources if s["venue"] == venue]
    venues["BSE"] = {
        "status": "unavailable",
        "reason": "OFFICIAL_DOCUMENT_REDIRECT_LOOP",
        "sessions": None,
        "next_session": None,
    }
    result = {
        "schema_version": "quanthus.forward-calendar.v1",
        "product_id": PRODUCT,
        "status": "PARTIAL",
        "timezone": "Asia/Shanghai",
        "built_at": built_at,
        "code_revision": code_revision,
        "coverage": {"start": str(START), "end": str(END)},
        "representation": "DERIVED_DATES_FROM_PINNED_EXCHANGE_RULES_AND_ANNUAL_NOTICES",
        "weekly_rule": "MONDAY_THROUGH_FRIDAY_EXCEPT_ANNOUNCED_CLOSURES",
        "venues": venues,
        "sources": sources,
        "source_receipts_sha256": sha256(encoded(receipts)),
        "publication_clock_location": "discovery_pointer_and_publication_receipt",
        "consumer_receipt_clock_owner": "P12",
    }
    result["content_sha256"] = sha256(encoded(result))
    return result


def select_next(
    descriptor: dict[str, Any],
    venue: str,
    day: date,
    *,
    available_at: str,
    knowledge_cutoff: str,
) -> dict[str, Any]:
    """Use a verified descriptor and visibility, never a weekday fallback."""
    identity = {key: value for key, value in descriptor.items() if key != "content_sha256"}
    if descriptor.get("content_sha256") != sha256(encoded(identity)):
        raise CalendarError("DESCRIPTOR_CONTENT_IDENTITY")
    if _clock(knowledge_cutoff) < _clock(available_at):
        return {"status": "unavailable", "next_session": None, "reason": "NOT_YET_VISIBLE"}
    coverage = descriptor["coverage"]
    if not date.fromisoformat(coverage["start"]) <= day <= date.fromisoformat(coverage["end"]):
        return {"status": "unavailable", "next_session": None, "reason": "DATE_OUTSIDE_COVERAGE"}
    item = descriptor["venues"].get(venue)
    if not item or item["status"] != "AVAILABLE":
        return {"status": "unavailable", "next_session": None, "reason": "VENUE_UNAVAILABLE"}
    following = item["next_session"][str(day)]
    return {
        "status": "AVAILABLE" if following else "unavailable",
        "next_session": following,
        "reason": None if following else "NEXT_SESSION_OUTSIDE_COVERAGE",
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def publish(
    root: Path,
    version: str,
    descriptor: dict[str, Any],
    documents: dict[str, bytes],
    receipts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create one immutable version, then expose its verified pointer atomically."""
    expected = build_calendar(
        documents,
        receipts,
        built_at=descriptor["built_at"],
        code_revision=descriptor["code_revision"],
    )
    if descriptor != expected:
        raise CalendarError("PUBLICATION_INPUT_IDENTITY")
    if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,100}", version) is None:
        raise CalendarError("INVALID_VERSION")
    root.mkdir(parents=True, exist_ok=True)
    destination = root / version
    if destination.exists():
        raise CalendarError("IMMUTABLE_VERSION_EXISTS")
    staging = root / (".pending-" + version)
    staging.mkdir(mode=0o755)
    (staging / "sources").mkdir()
    for name, body in documents.items():
        (staging / "sources" / name).write_bytes(body)
    (staging / "SOURCE-RECEIPTS.json").write_bytes(encoded(receipts))
    payload = encoded(descriptor)
    (staging / "descriptor.json").write_bytes(payload)
    for path in staging.rglob("*"):
        if path.is_file():
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
    os.rename(staging, destination)
    publication = {
        "product_id": PRODUCT,
        "version": version,
        "publisher": "P10-CN02",
        "publication_commit_at": _now(),
        "channel": str(destination),
        "descriptor_sha256": sha256(payload),
    }
    for name, body in documents.items():
        if (destination / "sources" / name).read_bytes() != body:
            raise CalendarError("PUBLISHED_SOURCE_READBACK_MISMATCH")
    if (destination / "descriptor.json").read_bytes() != payload:
        raise CalendarError("PUBLISHED_DESCRIPTOR_READBACK_MISMATCH")
    publication["channel_visible_at"] = _now()
    with (destination / "PUBLICATION.json").open("xb") as stream:
        stream.write(encoded(publication))
        stream.flush()
        os.fsync(stream.fileno())
    pointer = {
        "schema_version": "quanthus.forward-calendar-pointer.v1",
        "product_id": PRODUCT,
        "version": version,
        "descriptor": version + "/descriptor.json",
        "descriptor_sha256": sha256(payload),
        "published_at": publication["publication_commit_at"],
        "available_at": publication["channel_visible_at"],
        "publication": version + "/PUBLICATION.json",
        "publication_sha256": sha256(encoded(publication)),
        "status": descriptor["status"],
    }
    tmp = root / (".pointer-" + version)
    with tmp.open("xb") as stream:
        stream.write(encoded(pointer))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, root / "CURRENT_FORWARD_CALENDAR.json")
    for directory in (destination / "sources", destination, root):
        handle = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(handle)
        finally:
            os.close(handle)
    return pointer


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--receipts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--code-revision", required=True)
    args = parser.parse_args()
    documents = {name: (args.sources / name).read_bytes() for name in SOURCES}
    receipts = json.loads(args.receipts.read_text())
    descriptor = build_calendar(
        documents,
        receipts,
        built_at=_now(),
        code_revision=args.code_revision,
    )
    print(json.dumps(publish(args.output, args.version, descriptor, documents, receipts)))


if __name__ == "__main__":
    main()

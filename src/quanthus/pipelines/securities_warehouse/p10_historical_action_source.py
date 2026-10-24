"""Offline P10 qualification of exact official cash/bonus implementation facts.

An observed publisher timestamp is a claim, never an inferred historical known_at.
This contract admits source terms only; exchange-reference and consumer admission
are independent gates. Original warehouse and research envelopes stay immutable.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

VERSION = "p10-official-historical-cash-bonus-source-v1"
FIELDS = {
    "record_date",
    "ex_date",
    "cash_payment_date",
    "share_credit_date",
    "share_listing_date",
    "cash_per_ten_gross",
    "bonus_per_ten",
    "transfer_per_ten",
}


def sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _instant(value: Any) -> datetime:
    try:
        result = datetime.fromisoformat(value)
        if result.utcoffset() is None:
            raise ValueError
        return result
    except (TypeError, ValueError) as exc:
        raise ValueError("ACTION_SOURCE_CLOCK") from exc


@dataclass(frozen=True)
class ActionFacts:
    security_code: str
    record_date: date
    ex_date: date
    cash_payment_date: date | None
    share_credit_date: date | None
    share_listing_date: date | None
    cash_per_share: Decimal
    bonus_share_ratio: Decimal
    acquired_at: datetime
    source_revision: str
    evidence_sha256: str
    manifest_sha256: str


def load_source(root: Path, expected: str) -> tuple[ActionFacts, ...]:
    """Verify self-contained bytes, publisher links, quotation offsets and clocks."""
    if not re.fullmatch("[a-f0-9]{64}", expected):
        raise ValueError("ACTION_SOURCE_DIGEST")
    if root.resolve() != root.absolute() or not root.is_dir() or root.is_symlink():
        raise ValueError("ACTION_SOURCE_ROOT")
    paths = list(root.iterdir())
    if len(paths) > 256 or any(p.is_symlink() or not p.is_file() for p in paths):
        raise ValueError("ACTION_SOURCE_PHYSICAL_SET")
    if sum(p.stat().st_size for p in paths) > 128 * 1024**2:
        raise ValueError("ACTION_SOURCE_SIZE")
    if sha(root / "manifest.json") != expected:
        raise ValueError("ACTION_SOURCE_MANIFEST_CHANGED")
    manifest = json.loads((root / "manifest.json").read_bytes())
    if (
        manifest.get("kind") != VERSION
        or manifest.get("consumer_admission") != "NOT_GRANTED"
        or manifest.get("historical_PIT_claim") is not False
        or manifest.get("exchange_reference_qualified") is not False
        or manifest.get("production_authorized") is not False
    ):
        raise ValueError("ACTION_SOURCE_SCOPE")
    members: dict[str, bytes] = {}
    for row in manifest["files"]:
        name = row["path"]
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or name in members
            or name in {"manifest.json", "SHA256SUMS"}
        ):
            raise ValueError("ACTION_SOURCE_MEMBER")
        raw = (root / name).read_bytes()
        if len(raw) != row["bytes"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
            raise ValueError("ACTION_SOURCE_MEMBER_CHANGED")
        members[name] = raw
    if {p.name for p in paths} != set(members) | {"manifest.json", "SHA256SUMS"}:
        raise ValueError("ACTION_SOURCE_PHYSICAL_SET")
    sums = (
        "".join(
            f"{hashlib.sha256(raw).hexdigest()}  {name}\n" for name, raw in sorted(members.items())
        )
        + f"{expected}  manifest.json\n"
    )
    if (root / "SHA256SUMS").read_text() != sums:
        raise ValueError("ACTION_SOURCE_CHECKSUMS")
    facts = json.loads(members["facts.json"])
    if facts.get("kind") != VERSION or not 1 <= len(facts["events"]) <= 32:
        raise ValueError("ACTION_SOURCE_FACTS")
    results = []
    keys = set()
    for event in facts["events"]:
        code = event["security_code"]
        if not isinstance(code, str) or not re.fullmatch(r"\d{6}", code):
            raise ValueError("ACTION_SOURCE_SECURITY")
        terms = event["facts"]
        if set(terms) != FIELDS:
            raise ValueError("ACTION_SOURCE_FIELDS")
        documents = event["documents"]
        if not documents or len({d["notice_id"] for d in documents}) != len(documents):
            raise ValueError("ACTION_SOURCE_DOCUMENT_SET")
        by_id = {d["notice_id"]: d for d in documents}
        for doc in documents:
            aid = doc["notice_id"]
            metadata = json.loads(members[doc["metadata_member"]])
            matches = [
                a
                for a in metadata.get("announcements") or []
                if str(a.get("announcementId")) == aid and a.get("secCode") == code
            ]
            if len(matches) != 1:
                raise ValueError("ACTION_SOURCE_OFFICIAL_IDENTITY")
            entry = matches[0]
            link = json.loads(members[f"publisher-link-{aid}.json"])
            parsed = urlparse(link["url"])
            detail = members[doc["detail_member"]].decode("utf-8")
            bases = re.findall(r"var v3_cninfo = [\"']([^\"']+)", detail)
            from urllib.parse import urljoin

            if (
                len(set(bases)) != 1
                or link["url"]
                != urljoin(
                    bases[0].replace("http://", "https://", 1).rstrip("/") + "/",
                    entry["adjunctUrl"],
                )
                or parsed.scheme != "https"
                or not parsed.hostname
                or not (
                    parsed.hostname == "cninfo.com.cn" or parsed.hostname.endswith(".cninfo.com.cn")
                )
                or parsed.username
                or parsed.password
                or parsed.port not in (None, 443)
                or link["metadata_sha256"]
                != hashlib.sha256(members[doc["metadata_member"]]).hexdigest()
                or link["detail_sha256"]
                != hashlib.sha256(members[doc["detail_member"]]).hexdigest()
                or not members[doc["pdf_member"]].startswith(b"%PDF-")
                or hashlib.sha256(members[doc["pdf_member"]]).hexdigest() != doc["pdf_sha256"]
                or code not in members[doc["text_member"]].decode("utf-8")
            ):
                raise ValueError("ACTION_SOURCE_PUBLISHER_CLOSURE")
            _instant(doc["acquired_at"])
        for term in terms.values():
            if term["value"] is None:
                if term.get("unknown") is not True:
                    raise ValueError("ACTION_SOURCE_UNKNOWN")
                continue
            proof = term["evidence"]
            doc = by_id[proof["notice_id"]]
            raw = members[doc["text_member"]]
            text = raw.decode("utf-8")
            start, end = proof["start"], proof["end"]
            if (
                type(start) is not int
                or type(end) is not int
                or not 0 <= start < end <= len(text)
                or text[start:end] != proof["quote"]
                or not proof["quote"]
                or len(proof["quote"]) > 2048
                or hashlib.sha256(raw).hexdigest() != proof["text_sha256"]
                or len(text[:start].encode()) != proof["utf8_byte_start"]
                or len(text[:end].encode()) != proof["utf8_byte_end"]
            ):
                raise ValueError("ACTION_SOURCE_QUOTE_CLOSURE")
        # Independently parse declared dates and the literal uniform per-ten
        # plan. Rehashing a changed numeric value cannot qualify it. In this
        # bounded contract an absent component is zero only inside that exact
        # complete, reviewed plan; it is never inferred from a nullable column.
        for name in FIELDS:
            term = terms[name]
            if term["value"] is None:
                continue
            compact = re.sub(r"\s+", "", term["evidence"]["quote"])
            if name.endswith("date"):
                matches = re.findall(r"(\d{4})年(\d{1,2})月(\d{1,2})日", compact)
                if len(matches) != 1 or date(*map(int, matches[0])).isoformat() != term["value"]:
                    raise ValueError("ACTION_SOURCE_DECLARED_VALUE")
            else:
                if not all(s in compact for s in ("总股本", "向全体股东", "含税")):
                    raise ValueError("ACTION_SOURCE_COMPLETE_PLAN_REQUIRED")
                expressions = {
                    "cash_per_ten_gross": (
                        r"每10股派(?:发)?(?:现金(?:股利|红利)?)?([0-9]+(?:\.[0-9]+)?)元"
                    ),
                    "bonus_per_ten": r"每10股送(?:红股)?([0-9]+(?:\.[0-9]+)?)股",
                    "transfer_per_ten": r"每10股转增([0-9]+(?:\.[0-9]+)?)股",
                }
                plan = compact.split("含税", 1)[0] if name == "cash_per_ten_gross" else compact
                numbers = re.findall(expressions[name], plan)
                if len(numbers) > 1 or Decimal(numbers[0] if numbers else "0") != Decimal(
                    term["value"]
                ):
                    raise ValueError("ACTION_SOURCE_DECLARED_VALUE")
        try:
            decimals = []
            for name in ("cash_per_ten_gross", "bonus_per_ten", "transfer_per_ten"):
                value = terms[name]["value"]
                if type(value) is not str:
                    raise ValueError
                number = Decimal(value)
                if not number.is_finite() or number < 0:
                    raise ValueError
                decimals.append(number)
            cash, bonus, transfer = decimals
            record, ex = (date.fromisoformat(terms[n]["value"]) for n in ("record_date", "ex_date"))
            dates = [
                None if terms[n]["value"] is None else date.fromisoformat(terms[n]["value"])
                for n in ("cash_payment_date", "share_credit_date", "share_listing_date")
            ]
        except (ValueError, TypeError, InvalidOperation) as exc:
            raise ValueError("ACTION_SOURCE_TERMS") from exc
        if (
            not record < ex
            or cash + bonus + transfer <= 0
            or any(d is not None and d < ex for d in dates)
        ):
            raise ValueError("ACTION_SOURCE_TERMS")
        key = (code, ex)
        if key in keys:
            raise ValueError("ACTION_SOURCE_DUPLICATE_KEY")
        keys.add(key)
        acquired = max(_instant(d["acquired_at"]) for d in documents)
        if _instant(event["known_at"]) != acquired or event.get("historical_known_at") is not None:
            raise ValueError("ACTION_SOURCE_CLOCK")
        results.append(
            ActionFacts(
                code,
                record,
                ex,
                dates[0],
                dates[1],
                dates[2],
                cash / 10,
                (bonus + transfer) / 10,
                acquired,
                manifest["source_revision"],
                hashlib.sha256(
                    json.dumps(event, sort_keys=True, ensure_ascii=False).encode()
                ).hexdigest(),
                expected,
            )
        )
    if _instant(manifest["source_information_cutoff"]) != max(f.acquired_at for f in results):
        raise ValueError("ACTION_SOURCE_CLOCK")
    return tuple(results)

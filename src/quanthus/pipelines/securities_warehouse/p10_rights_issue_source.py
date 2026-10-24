"""Verify one bounded P10 official-rights source bundle without admitting a consumer.

The expected physical manifest digest is frozen by the caller before row access.
Quotes prove the retained evidence chain, not the semantic truth of an arbitrary
new extractor. Source terms still need P10 semantic review. No price-reference
formula, historical publication instant or subscription fill is inferred here.
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

VERSION = "p10-official-rights-source-reader-v1"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _digest(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("RIGHTS_SOURCE_DIGEST")
    return value


def _instant(value: Any) -> datetime:
    try:
        result = datetime.fromisoformat(value)
        if result.utcoffset() is None:
            raise ValueError
        return result
    except (ValueError, TypeError) as exc:
        raise ValueError("RIGHTS_SOURCE_CLOCK") from exc


@dataclass(frozen=True)
class RightsIssueFacts:
    source_revision: str
    manifest_sha256: str
    event_sha256: str
    security_code: str
    record_date: date
    subscription_start: date
    subscription_end: date
    ex_right_date: date
    listing_date: date
    source_information_cutoff: datetime
    maximum_subscription_ratio: Decimal
    subscription_price: Decimal
    actual_issued_shares: int
    shares_before: int


def load_rights_source(root: Path, expected_manifest_sha256: str) -> RightsIssueFacts:
    """Read only local ordinary files; unknown times and consumer gates stay closed."""
    expected = _digest(expected_manifest_sha256)
    if root.resolve() != root.absolute() or not root.is_dir():
        raise ValueError("RIGHTS_SOURCE_ROOT")
    children = list(root.iterdir())
    if len(children) > 32 or any(p.is_symlink() or not p.is_file() for p in children):
        raise ValueError("RIGHTS_SOURCE_PHYSICAL_SET")
    if sum(p.stat().st_size for p in children) > 2 * 1024**2:
        raise ValueError("RIGHTS_SOURCE_SIZE")
    manifest_bytes = (root / "source-facts-manifest.json").read_bytes()
    if _sha(manifest_bytes) != expected:
        raise ValueError("RIGHTS_SOURCE_MANIFEST_CHANGED")
    manifest = json.loads(manifest_bytes)
    content_digest = _sha(
        json.dumps(manifest["files"], sort_keys=True, separators=(",", ":")).encode()
    )
    if (
        manifest.get("content_sha256") != content_digest
        or manifest.get("source_revision") != "p10-557-000901-rights-" + content_digest[:16]
    ):
        raise ValueError("RIGHTS_SOURCE_CONTENT_IDENTITY")
    members: dict[str, bytes] = {}
    for entry in manifest["files"]:
        name = entry["path"]
        if not isinstance(name, str) or Path(name).name != name or name in members:
            raise ValueError("RIGHTS_SOURCE_MEMBER_IDENTITY")
        raw = (root / name).read_bytes()
        if len(raw) != entry["bytes"] or _sha(raw) != _digest(entry["sha256"]):
            raise ValueError("RIGHTS_SOURCE_MEMBER_CHANGED")
        members[name] = raw
    if {p.name for p in children} != set(members) | {"source-facts-manifest.json", "SHA256SUMS"}:
        raise ValueError("RIGHTS_SOURCE_PHYSICAL_SET")
    expected_sums = (
        "".join(f"{_sha(raw)}  {name}\n" for name, raw in sorted(members.items()))
        + f"{expected}  source-facts-manifest.json\n"
    )
    if (root / "SHA256SUMS").read_text() != expected_sums:
        raise ValueError("RIGHTS_SOURCE_CHECKSUM_LIST")
    event = json.loads(members["rights-event-source-facts.json"])
    if (
        manifest.get("kind") != "p10-exact-rights-official-source-bundle-v1"
        or manifest.get("consumer_admission") != "NOT_GRANTED"
        or event.get("kind") != "p10-official-rights-issue-source-facts-v1"
        or event.get("event_type") != "optional-paid-rights-issue"
        or event.get("source_type") != "official-CNINFO-issuer-publication"
        or event.get("consumer_admission") != "NOT_GRANTED"
        or event.get("source_prices_modified") is not False
        or event.get("original_frozen_cutoff_rewritten") is not False
    ):
        raise ValueError("RIGHTS_SOURCE_SCOPE")
    acquisition = json.loads(members["acquisition-state.json"])
    rows = acquisition["requests"]
    if (
        len(rows) > 12
        or acquisition["response_bytes"] > 16 * 1024**2
        or any(r.get("status") != 200 or "error" in r for r in rows)
    ):
        raise ValueError("RIGHTS_SOURCE_ACQUISITION")
    pdfs = {r["label"][4:]: r for r in rows if r["label"].startswith("pdf-")}
    if len(pdfs) != 3 or len(pdfs) != sum(r["label"].startswith("pdf-") for r in rows):
        raise ValueError("RIGHTS_SOURCE_DOCUMENT_SET")
    for aid, row in pdfs.items():
        pdf_bytes = members[Path(row["body_path"]).name]
        if not pdf_bytes.startswith(b"%PDF-") or _sha(pdf_bytes) != row["sha256"]:
            raise ValueError("RIGHTS_SOURCE_PDF")
        if row["acquired_at"] != event["acquired_at_by_notice"][aid]:
            raise ValueError("RIGHTS_SOURCE_CLOCK")
    cutoff = max(_instant(r["acquired_at"]) for r in pdfs.values())
    if (
        _instant(event["earliest_source_information_cutoff_for_this_new_bundle"]) != cutoff
        or _instant(manifest["source_information_cutoff"]) != cutoff
    ):
        raise ValueError("RIGHTS_SOURCE_CLOCK")
    facts = event["facts"]
    for fact in facts.values():
        if fact["value"] is None:
            continue
        proof = fact["evidence"]
        aid = proof["notice_id"]
        raw = members[f"pdf-{aid}.txt"]
        text = raw.decode("utf-8")
        start, end = proof["start"], proof["end"]
        if (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= len(text)
            or text[start:end] != proof["quote"]
            or len(proof["quote"]) > 1024
            or _sha(raw) != proof["text_sha256"]
            or proof["pdf_sha256"] != pdfs[aid]["sha256"]
            or len(text[:start].encode()) != proof["utf8_byte_start"]
            or len(text[:end].encode()) != proof["utf8_byte_end"]
        ):
            raise ValueError("RIGHTS_SOURCE_QUOTE_CLOSURE")

    def value(name: str) -> Any:
        return facts[name]["value"]

    if (
        value("unpaid_after_deadline") != "subscription-rights-waived"
        or value("new_share_account_credit_instant") is not None
        or value("exact_historical_publication_instants") is not None
        or value("exchange_ex_right_formula_and_tick_rule") is not None
    ):
        raise ValueError("RIGHTS_SOURCE_UNSUPPORTED_INFERENCE")
    dates = [
        date.fromisoformat(value("record_date")),
        *map(date.fromisoformat, value("subscription_window")),
        date.fromisoformat(value("ex_right_date")),
        date.fromisoformat(value("new_share_listing_date")),
    ]
    if not dates[0] < dates[1] <= dates[2] < dates[3] <= dates[4]:
        raise ValueError("RIGHTS_SOURCE_BUSINESS_DATES")
    if value("resumption_date") != dates[3].isoformat():
        raise ValueError("RIGHTS_SOURCE_RESUMPTION")
    before, issued, after = (
        value(k) for k in ("shares_before", "actual_issued_shares", "shares_after")
    )
    if (
        any(type(n) is not int or n <= 0 for n in (before, issued, after))
        or before + issued != after
    ):
        raise ValueError("RIGHTS_SOURCE_SHARE_CLOSURE")
    try:
        if any(
            type(value(k)) is not str for k in ("maximum_subscription_ratio", "subscription_price")
        ):
            raise ValueError
        ratio, price = (
            Decimal(value(k)) for k in ("maximum_subscription_ratio", "subscription_price")
        )
        if not ratio.is_finite() or not price.is_finite() or ratio <= 0 or price <= 0:
            raise ValueError
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("RIGHTS_SOURCE_DECIMAL") from exc
    code = event["security_code"]
    if not isinstance(code, str) or not re.fullmatch(r"\d{6}", code):
        raise ValueError("RIGHTS_SOURCE_SECURITY")
    return RightsIssueFacts(
        manifest["source_revision"],
        expected,
        _sha(members["rights-event-source-facts.json"]),
        code,
        dates[0],
        dates[1],
        dates[2],
        dates[3],
        dates[4],
        cutoff,
        ratio,
        price,
        issued,
        before,
    )

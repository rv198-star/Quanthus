"""Exact optional-rights limit reference, with an offline official-rule closure.

Only the separately reviewed 2014-10-24 SZSE event is supported. The reference
stays rational: the rule rounds the final limit price to a tick and does not
justify first rounding an intermediate reference. No observed price is repaired.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .p10_rights_issue_source import RightsIssueFacts, _digest, _instant

VERSION = "p10-szse-optional-rights-reference-v1"
EVENT_DATE = date(2014, 10, 24)
CLAUSES = {
    "revision_effective",
    "rule_title",
    "A_share_tick_3_3_13",
    "limit_rates_3_3_15",
    "limit_rounding_3_3_16",
    "exright_formula_4_4_2",
    "exright_limit_basis_4_4_3",
    "displayed_previous_close_5_2_3",
    "successor_revision_chain",
}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class OfficialRuleFacts:
    manifest_sha256: str
    source_information_cutoff: datetime
    event_date: date


def load_official_rule(root: Path, expected_manifest_sha256: str) -> OfficialRuleFacts:
    """Validate the physically frozen, locally retained reviewed rule evidence."""
    expected = _digest(expected_manifest_sha256)
    if root.resolve() != root.absolute() or not root.is_dir():
        raise ValueError("RIGHTS_RULE_ROOT")
    paths = list(root.iterdir())
    if (
        len(paths) > 12
        or any(p.is_symlink() or not p.is_file() for p in paths)
        or sum(p.stat().st_size for p in paths) > 16 * 1024**2
    ):
        raise ValueError("RIGHTS_RULE_PHYSICAL_SET")
    raw = (root / "manifest.json").read_bytes()
    if _sha(raw) != expected:
        raise ValueError("RIGHTS_RULE_MANIFEST_CHANGED")
    m = json.loads(raw)
    identity = _sha(json.dumps(m["files"], sort_keys=True, separators=(",", ":")).encode())
    if (
        m.get("kind") != "p10-szse-exright-official-rule-bundle-v1"
        or m.get("consumer_admission") != "NOT_GRANTED"
        or m.get("content_sha256") != identity
        or m.get("source_revision") != "p10-557-szse-rule-" + identity[:16]
    ):
        raise ValueError("RIGHTS_RULE_IDENTITY")
    members = {}
    for entry in m["files"]:
        name = entry["path"]
        if not isinstance(name, str) or Path(name).name != name or name in members:
            raise ValueError("RIGHTS_RULE_MEMBER")
        body = (root / name).read_bytes()
        if len(body) != entry["bytes"] or _sha(body) != _digest(entry["sha256"]):
            raise ValueError("RIGHTS_RULE_MEMBER_CHANGED")
        members[name] = body
    if {p.name for p in paths} != set(members) | {"manifest.json", "SHA256SUMS"}:
        raise ValueError("RIGHTS_RULE_PHYSICAL_SET")
    sums = "".join(f"{_sha(v)}  {k}\n" for k, v in sorted(members.items()))
    if (root / "SHA256SUMS").read_text() != sums + f"{expected}  manifest.json\n":
        raise ValueError("RIGHTS_RULE_CHECKSUM_LIST")
    facts = json.loads(members["rule-facts.json"])
    state = json.loads(members["budget-state.json"])
    docs = m["documents"]
    if len(docs) != 2 or {d["label"] for d in docs} != {
        "rule-201311",
        "rule-201512-successor",
    }:
        raise ValueError("RIGHTS_RULE_DOCUMENT_SET")
    for d in docs:
        parsed = urlparse(d["url"])
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or not (parsed.hostname == "szse.cn" or parsed.hostname.endswith(".szse.cn"))
            or parsed.username is not None
            or parsed.port not in (None, 443)
            or _sha(members[d["label"] + ".body"]) != d["sha256"]
            or not members[d["label"] + ".body"].startswith(b"%PDF-")
        ):
            raise ValueError("RIGHTS_RULE_PUBLISHER_OR_PDF")
    cutoff = max(_instant(d["acquired_at"]) for d in docs)
    if (
        state.get("status") != "CLOSED_TWO_APPROVED_DOCUMENTS_COMPLETE"
        or state["request_count"] > 10
        or state["response_bytes"] > 16 * 1024**2
        or _instant(m["source_information_cutoff"]) != cutoff
        or _instant(facts["source_information_cutoff"]) != cutoff
    ):
        raise ValueError("RIGHTS_RULE_CLOCK_OR_BUDGET")
    if (
        facts.get("kind") != "p10-szse-exright-rule-facts-v1"
        or facts.get("rule_revision") != "SZSE-TRADING-2013-11"
        or facts.get("qualified_event_date") != EVENT_DATE.isoformat()
        or facts.get("revision_effective_date_claim") != "2013-11-30"
        or facts.get("later_revision_dates_claimed") != ["2015-01-09", "2015-12-04"]
        or facts.get("A_share_tick") != "0.01"
        or facts.get("limit_rounding") != "ROUND_HALF_UP_TO_TICK_AFTER_MULTIPLICATION"
        or facts.get("reference_intermediate_rounding")
        != "NO_UNPROVED_PRE_ROUNDING; retain exact rational reference"
        or facts.get("original_historical_acquisition_instant") is not None
        or facts.get("market_prices_modified") is not False
        or facts.get("per_decision_historical_PIT_claim") is not False
        or set(facts["clauses"]) != CLAUSES
    ):
        raise ValueError("RIGHTS_RULE_SCOPE")
    by_label = {d["label"]: d for d in docs}
    for proof in facts["clauses"].values():
        label = proof["document"]
        text_raw = members[label + ".txt"]
        text = text_raw.decode("utf-8")
        start, end = proof["start"], proof["end"]
        if (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= len(text)
            or text[start:end] != proof["quote"]
            or len(proof["quote"]) > 1024
            or len(text[:start].encode()) != proof["utf8_byte_start"]
            or len(text[:end].encode()) != proof["utf8_byte_end"]
            or _sha(text_raw) != proof["text_sha256"]
            or proof["pdf_sha256"] != by_label[label]["sha256"]
            or proof["url"] != by_label[label]["url"]
        ):
            raise ValueError("RIGHTS_RULE_QUOTE_CLOSURE")
    return OfficialRuleFacts(expected, cutoff, EVENT_DATE)


def _tick_round(value: Fraction) -> Decimal:
    """Positive-price HALF_UP in integer arithmetic, independent of Decimal context."""
    if value <= 0:
        raise ValueError("RIGHTS_REFERENCE_NONPOSITIVE")
    cents = value * 100
    whole = cents.numerator // cents.denominator
    if 2 * (cents.numerator % cents.denominator) >= cents.denominator:
        whole += 1
    return Decimal(f"{whole // 100}.{whole % 100:02d}")


def derive_reference(
    facts: RightsIssueFacts,
    rule: OfficialRuleFacts,
    *,
    previous_close: Decimal,
    limit_pct: Decimal,
    source_information_cutoff: datetime,
    before_bar_sha256: str,
    before_limit_sha256: str,
) -> dict[str, Any]:
    """Return one exact derived row; applying it needs its own before-image check.

    The caller must prove the selected last-traded close and absence of a
    simultaneous cash/bonus action. This function does not infer those facts.
    The original market data, optional-election accounting and every other
    normalization exception stay outside this narrow derived-reference scope.
    """
    if (
        type(facts) is not RightsIssueFacts
        or type(rule) is not OfficialRuleFacts
        or facts.security_code != "000901"
        or facts.ex_right_date != rule.event_date
        or rule.event_date != EVENT_DATE
    ):
        raise ValueError("RIGHTS_REFERENCE_SCOPE")
    if (
        type(previous_close) is not Decimal
        or not previous_close.is_finite()
        or previous_close <= 0
        or type(limit_pct) is not Decimal
        or limit_pct not in (Decimal("5"), Decimal("10"))
    ):
        raise ValueError("RIGHTS_REFERENCE_DECIMAL_OR_RATE")
    if source_information_cutoff.utcoffset() is None or source_information_cutoff < max(
        facts.source_information_cutoff, rule.source_information_cutoff
    ):
        raise ValueError("RIGHTS_REFERENCE_SOURCE_NOT_AVAILABLE")
    reference = (
        Fraction(previous_close) * facts.shares_before
        + Fraction(facts.subscription_price) * facts.actual_issued_shares
    ) / (facts.shares_before + facts.actual_issued_shares)
    pct = Fraction(limit_pct) / 100
    lower, upper = (_tick_round(reference * (1 + sign * pct)) for sign in (-1, 1))
    # 3.3.16: limits closer than one tick must differ by at least that tick.
    if Fraction(upper) - reference < Fraction(1, 100):
        upper = _tick_round(reference + Fraction(1, 100))
    if reference - Fraction(lower) < Fraction(1, 100):
        lower = _tick_round(reference - Fraction(1, 100))
    return {
        "kind": VERSION,
        "security_code": facts.security_code,
        "execution_date": facts.ex_right_date.isoformat(),
        "issuer_source_manifest_sha256": facts.manifest_sha256,
        "issuer_event_sha256": facts.event_sha256,
        "rule_source_manifest_sha256": rule.manifest_sha256,
        "before_bar_sha256": _digest(before_bar_sha256),
        "before_limit_sha256": _digest(before_limit_sha256),
        "reference_numerator": str(reference.numerator),
        "reference_denominator": str(reference.denominator),
        "lower_limit_price": str(lower),
        "upper_limit_price": str(upper),
        "limit_pct": str(limit_pct),
        "reference_basis": "EXACT_ACTUAL_ISSUED_RATIO_NO_INTERMEDIATE_ROUNDING",
        "source_information_cutoff": source_information_cutoff.isoformat(),
        "historical_publication_instant": None,
        "historical_PIT_claim": False,
        "market_prices_modified": False,
        "qualified_scope": "EXACT_DERIVED_REFERENCE_NOT_WHOLE_REPLAY_ADMISSION",
    }

"""Explicit current implementation sections, separate from approved old plans.

The legacy whole-document identity/date/currency guards run first and remain
unchanged. This successor only resolves its cash-uniqueness failure when the
publisher explicitly labels a current scheme and identifies its company scheme.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal, localcontext
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_notice_action import _CASH, parse_implemented_notice_cash
from .base22_precision import decimal_profile

CONTRACT_ID = "p10-base22-issuer-current-implementation-section-v2"
_HEADING = re.compile(r"^\s*[一二三四五六七八九十]+[、.．]\s*(?P<title>.+?)\s*$")
_TITLES = {"权益分派方案", "本次实施的利润分配方案", "本次实施的权益分派方案"}
_DECLARATION = re.compile(
    r"^(?:[1-9][、.]?(?:分派方案)?)?(?:本公司|公司)\d{4}(?:年)?"
    r"(?:年度|半年度)?(?:权益分派|利润分配)方案(?:为|如下)?[:：]"
)


def parse_current_scheme_cash(
    text: str,
    *,
    stock_code: str,
    ex_date: date,
    publisher_title: str,
) -> dict[str, Any]:
    """Never choose the last/largest cash atom or an arbitrary nearby paragraph."""
    try:
        return parse_implemented_notice_cash(
            text,
            stock_code=stock_code,
            ex_date=ex_date,
            publisher_title=publisher_title,
        )
    except BoundedError as exc:
        if str(exc) != "NOTICE_UNIQUE_GROSS_CASH_ATOM_NOT_ESTABLISHED":
            raise
    # The one permitted predecessor failure occurs after every existing global
    # identity, implementation, currency, ex-date and exact-precision guard.
    lines = unicodedata.normalize("NFKC", text).splitlines()
    headings = [
        (n, m["title"].strip().rstrip(":："))
        for n, line in enumerate(lines)
        if (m := _HEADING.fullmatch(line))
    ]
    sections = []
    for index, (start, title) in enumerate(headings):
        if title not in _TITLES:
            continue
        stop = headings[index + 1][0] if index + 1 < len(headings) else len(lines)
        original = "\n".join(lines[start + 1 : stop])
        normalized = re.sub(r"\s+", "", original)
        if _DECLARATION.match(normalized) is None:
            continue
        sections.append((title, original, normalized))
    if not sections:
        raise BoundedError("NOTICE_EXPLICIT_CURRENT_IMPLEMENTATION_SECTION_UNAVAILABLE")
    values = set()
    tokens = []
    with localcontext() as context:
        context.prec = 64
        for _, _, section in sections:
            atoms = []
            for matched in _CASH.finditer(section):
                denominator = int(matched["denominator"] or "1")
                token = matched["amount"] or matched["post_amount"]
                amount = Decimal(token) / Decimal(denominator)
                if amount <= 0 or not decimal_profile(amount, precision=38, scale=10)["fits"]:
                    raise BoundedError("NOTICE_CURRENT_SCHEME_GROSS_CASH_NOT_EXACT")
                atoms.append(amount)
                tokens.append(
                    {
                        "original_numeric_token_sha256": digest(token),
                        "source_denominator": denominator,
                    }
                )
            if len(set(atoms)) != 1:
                raise BoundedError("NOTICE_CURRENT_IMPLEMENTATION_SECTION_CASH_CONFLICT")
            values.update(atoms)
    if len(values) != 1:
        raise BoundedError("NOTICE_CURRENT_IMPLEMENTATION_SECTION_CASH_CONFLICT")
    amount = next(iter(values))
    return {
        "contract_id": CONTRACT_ID,
        "stock_code": stock_code,
        "ex_date": ex_date,
        "currency": "CNY",
        "cash_per_share": amount,
        "source_unit": "explicit_current_company_scheme_per_one_or_ten_shares_gross_CNY",
        "original_text_sha256": digest(text),
        "publisher_title_sha256": digest(publisher_title),
        "cash_atom_sha256": digest(["CNY", amount]),
        "matched_tokens": tokens,
        "explicit_current_sections": [
            {"heading_sha256": digest(title), "section_sha256": digest(original)}
            for title, original, _ in sections
        ],
        "legacy_global_guards": "PASS_BEFORE_EXPLICIT_CASH_SCOPE_RESOLUTION",
        "source_priority_granted": False,
        "historical_publication_or_acquisition_inferred": False,
        "other_action_terms_qualified": False,
    }

"""Explicit implemented scheme adjustment relations; predecessor guards preserved."""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal, localcontext
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_notice_action import _CASH
from .base22_notice_scheme import _DECLARATION, _HEADING, _TITLES, parse_current_scheme_cash
from .base22_precision import decimal_profile

CONTRACT_ID = "p10-base22-issuer-explicit-current-scheme-adjustment-v3"


def parse_adjusted_current_scheme_cash(
    text: str, *, stock_code: str, ex_date: date, publisher_title: str
) -> dict[str, Any]:
    """A literal completed 'scheme from old to new' relation, never last/largest."""
    try:
        return parse_current_scheme_cash(
            text, stock_code=stock_code, ex_date=ex_date, publisher_title=publisher_title
        )
    except BoundedError as exc:
        if str(exc) != "NOTICE_CURRENT_IMPLEMENTATION_SECTION_CASH_CONFLICT":
            raise
    lines = unicodedata.normalize("NFKC", text).splitlines()
    headings = [
        (n, m["title"].strip().rstrip(":："))
        for n, line in enumerate(lines)
        if (m := _HEADING.fullmatch(line))
    ]
    values = set()
    adjustments = []
    sections = []
    tokens = []
    with localcontext() as context:
        context.prec = 64
        for index, (start, title) in enumerate(headings):
            if title not in _TITLES:
                continue
            stop = headings[index + 1][0] if index + 1 < len(headings) else len(lines)
            original = "\n".join(lines[start + 1 : stop])
            section = re.sub(r"\s+", "", original)
            if _DECLARATION.match(section) is None:
                continue
            ignored = set()
            for marker in re.finditer("分派方案由", section):
                old = _CASH.match(section, marker.end())
                if (
                    old is None
                    or not old[0].endswith(")")
                    or not section.startswith("变更为", old.end())
                ):
                    raise BoundedError("NOTICE_EXPLICIT_SCHEME_ADJUSTMENT_UNQUALIFIED")
                new = _CASH.match(section, old.end() + len("变更为"))
                if (
                    new is None
                    or not new[0].endswith(")")
                    or int(old["denominator"] or "1") != int(new["denominator"] or "1")
                ):
                    raise BoundedError("NOTICE_EXPLICIT_SCHEME_ADJUSTMENT_UNIT_UNQUALIFIED")
                ignored.add(old.span())
                adjustments.append(
                    {
                        "old_clause_sha256": digest(old[0]),
                        "new_clause_sha256": digest(new[0]),
                        "complete_relation_sha256": digest(section[marker.start() : new.end()]),
                    }
                )
            amounts = set()
            for match in _CASH.finditer(section):
                if match.span() in ignored:
                    continue
                token = match["amount"] or match["post_amount"]
                denominator = int(match["denominator"] or "1")
                amount = Decimal(token) / Decimal(denominator)
                if amount <= 0 or not decimal_profile(amount, precision=38, scale=10)["fits"]:
                    raise BoundedError("NOTICE_EXPLICIT_SCHEME_ADJUSTMENT_EXACT_CASH_UNQUALIFIED")
                amounts.add(amount)
                tokens.append(
                    {
                        "original_numeric_token_sha256": digest(token),
                        "source_denominator": denominator,
                    }
                )
            if len(amounts) != 1:
                raise BoundedError("NOTICE_EXPLICIT_SCHEME_ADJUSTMENT_CURRENT_CASH_CONFLICT")
            values.update(amounts)
            sections.append({"heading_sha256": digest(title), "section_sha256": digest(original)})
    if not adjustments or len(values) != 1:
        raise BoundedError("NOTICE_EXPLICIT_SCHEME_ADJUSTMENT_CURRENT_CASH_CONFLICT")
    amount = next(iter(values))
    return {
        "contract_id": CONTRACT_ID,
        "stock_code": stock_code,
        "ex_date": ex_date,
        "currency": "CNY",
        "cash_per_share": amount,
        "source_unit": "explicit_implemented_current_scheme_old_to_new_gross_CNY_relation",
        "original_text_sha256": digest(text),
        "publisher_title_sha256": digest(publisher_title),
        "cash_atom_sha256": digest(["CNY", amount]),
        "matched_tokens": tokens,
        "explicit_current_sections": sections,
        "explicit_adjustments": adjustments,
        "legacy_global_guards": "PASS_BEFORE_EXPLICIT_ADJUSTMENT_RESOLUTION",
        "source_priority_granted": False,
        "historical_publication_or_acquisition_inferred": False,
        "other_action_terms_qualified": False,
    }

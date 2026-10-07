"""Exact mainland-class gross cash in an explicitly scoped issuer scheme.

Foreign-class words elsewhere do not supply an exchange rate, date or currency.
The retained body must positively declare the domestic recipients and CNY gross
cash in its implemented scheme. Other classes and nonuniform terms stay held.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal, localcontext
from typing import Any

from .base22 import THROUGH, digest
from .base22_bounded import BoundedError
from .base22_notice_action import (
    _CASH,
    _DATE_TABLE,
    _EX_DATE,
    _IMPLEMENTED_TITLE,
    MAX_TEXT_BYTES,
)
from .base22_notice_current_scope import _NON_UNIFORM, _scope_view
from .base22_notice_date_layout import parse_exdate_layout_cash
from .base22_notice_scheme import _HEADING
from .base22_precision import decimal_profile

CONTRACT_ID = "p10-base22-explicit-domestic-class-current-cash-v9"
_TITLES = {"权益分派方案", "利润分配方案", "本次利润分配方案", "分配方案", "利润分配及分红派息方案"}
_DOMESTIC = re.compile(r"(?:本公司)?全体A股股东")
_DECLARATION = re.compile(r"(?:本次利润分配|本次分红派息|本次分配|本次权益分派)(?:以|方案)")


def parse_domestic_scope_cash(
    text: str,
    *,
    stock_code: str,
    ex_date: date,
    publisher_title: str,
) -> dict[str, Any]:
    try:
        atom = parse_exdate_layout_cash(
            text, stock_code=stock_code, ex_date=ex_date, publisher_title=publisher_title
        )
    except BoundedError as error:
        if str(error) != "NOTICE_MIXED_SECURITY_OR_CURRENCY_SCOPE_UNQUALIFIED":
            raise
    else:
        return {**atom, "contract_id": CONTRACT_ID, "predecessor_contract": atom["contract_id"]}
    if (
        not isinstance(text, str)
        or not 0 < len(text.encode()) <= MAX_TEXT_BYTES
        or not re.fullmatch(r"\d{6}", stock_code)
        or type(ex_date) is not date
        or ex_date > THROUGH
        or not isinstance(publisher_title, str)
    ):
        raise BoundedError("NOTICE_DOMESTIC_EXACT_INPUT_INVALID")
    view, layout = _scope_view(text)
    normalized = re.sub(r"\s+", "", unicodedata.normalize("NFKC", view))
    title = re.sub(r"\s+", "", unicodedata.normalize("NFKC", publisher_title))
    if not _IMPLEMENTED_TITLE.search(title) or not re.search(
        r"(?:证券代码|股票代码|A股代码)[:：]?" + stock_code + r"(?!\d)", normalized[:4096]
    ):
        raise BoundedError("NOTICE_DOMESTIC_EXACT_IMPLEMENTATION_IDENTITY_REQUIRED")
    dates = set()
    for match in _EX_DATE.finditer(normalized):
        try:
            dates.add(date(*(int(match[n]) for n in ("year", "month", "day"))))
        except ValueError:
            raise BoundedError("NOTICE_DOMESTIC_INVALID_DATE_TOKEN") from None
    for match in _DATE_TABLE.finditer(normalized):
        dates.add(date(*(int(n) for n in match["ex"].split("/"))))
    if dates != {ex_date}:
        raise BoundedError("NOTICE_DOMESTIC_EXACT_EX_DATE_REQUIRED")
    lines = view.splitlines()
    headings = [
        (i, m["title"].strip().rstrip(":："))
        for i, line in enumerate(lines)
        if (m := _HEADING.fullmatch(line))
    ]
    scopes: list[dict[str, Any]] = []
    for index, (start, heading) in enumerate(headings):
        if heading not in _TITLES:
            continue
        end = headings[index + 1][0] if index + 1 < len(headings) else len(lines)
        block = re.sub(r"\s+", "", "\n".join(lines[start + 1 : end]))
        if not _DOMESTIC.search(block) or not _DECLARATION.search(block):
            continue
        if _NON_UNIFORM.search(block):
            raise BoundedError("NOTICE_DOMESTIC_NONUNIFORM_REQUIRES_DISTINCT_TYPED_TERMS")
        # A positive A-share recipient clause is not authority to reinterpret a
        # B/H gross amount or currency in the same scheme. Such blocks remain held.
        if any(marker in block for marker in ("B股", "港币", "港元", "美元")):
            raise BoundedError("NOTICE_DOMESTIC_MIXED_SCHEME_CURRENCY_OR_CLASS")
        atoms = list(_CASH.finditer(block))
        if not atoms or any("人民币" not in atom[0] for atom in atoms):
            raise BoundedError("NOTICE_DOMESTIC_LITERAL_CNY_GROSS_UNIT_REQUIRED")
        amounts = set()
        tokens = []
        with localcontext() as context:
            context.prec = 64
            for cash_match in atoms:
                token = cash_match["amount"] or cash_match["post_amount"]
                denominator = int(cash_match["denominator"] or "1")
                amount = Decimal(token) / Decimal(denominator)
                if amount <= 0 or not decimal_profile(amount, precision=38, scale=10)["fits"]:
                    raise BoundedError("NOTICE_DOMESTIC_GROSS_EXACT_PRECISION_REQUIRED")
                amounts.add(amount)
                tokens.append(
                    {
                        "original_numeric_token_sha256": digest(token),
                        "source_denominator": denominator,
                    }
                )
        if len(amounts) != 1:
            raise BoundedError("NOTICE_DOMESTIC_CURRENT_SCHEME_GROSS_CONFLICT")
        scopes.append(
            {
                "amount": next(iter(amounts)),
                "heading_sha256": digest(heading),
                "complete_scheme_sha256": digest(block),
                "tokens": tokens,
            }
        )
    if not scopes or len({s["amount"] for s in scopes}) != 1:
        raise BoundedError("NOTICE_DOMESTIC_POSITIVE_CURRENT_CLASS_SCOPE_UNAVAILABLE")
    amount = scopes[0]["amount"]
    proof = {
        "layout_identity": layout["identity"],
        "scheme_scopes": [{k: v for k, v in s.items() if k != "amount"} for s in scopes],
        "domestic_class": "EXPLICIT_A_SHARE_RECIPIENTS",
        "other_classes": "NOT_GRANTED",
        "currency_or_conversion_inferred": False,
        "nonuniform_terms_granted": False,
    }
    proof["identity"] = digest(proof)
    return {
        "contract_id": CONTRACT_ID,
        "stock_code": stock_code,
        "ex_date": ex_date,
        "currency": "CNY",
        "cash_per_share": amount,
        "cash_atom_sha256": digest(["CNY", amount]),
        "source_unit": "explicit_implemented_A_share_gross_CNY",
        "original_text_sha256": digest(text),
        "publisher_title_sha256": digest(publisher_title),
        "matched_tokens": [t for s in scopes for t in s["tokens"]],
        "domestic_scope_evidence": proof,
        "source_priority_granted": False,
        "historical_publication_or_acquisition_inferred": False,
        "other_action_terms_qualified": False,
    }

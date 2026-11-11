"""Versioned literal field-label equivalence; every numeric token stays intact."""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_notice_action import _EX_DATE, MAX_TEXT_BYTES
from .base22_notice_adjustment import parse_adjusted_current_scheme_cash

CONTRACT_ID = "p10-base22-issuer-literal-field-labels-v4"


def literal_label_view(text: str) -> tuple[str, dict[str, Any]]:
    """Only a spelled-out ex-date label and identical repeated code labels.

    The source body remains immutable. This explicit parser view never removes a
    numeric token or picks a monetary amount, class, security or nearby date.
    """
    if not isinstance(text, str) or not 0 < len(text.encode("utf-8")) <= MAX_TEXT_BYTES:
        raise BoundedError("NOTICE_LITERAL_LABEL_INPUT_INVALID")
    original = unicodedata.normalize("NFKC", text)
    viewed = original
    replacements = []
    spelled = "除权(除息)日"
    if spelled in viewed:
        replacements.append({"kind": "literal_ex_date_label", "occurrences": viewed.count(spelled)})
        viewed = viewed.replace(spelled, "除权(息)日")
    for label in ("证券代码", "股票代码", "A股代码"):
        pattern = re.compile(re.escape(label) + r"\s*[:：]?\s*" + re.escape(label))
        viewed, count = pattern.subn(label, viewed)
        if count:
            replacements.append({"kind": "identical_repeated_" + label, "occurrences": count})
    if re.findall(r"\d+", original) != re.findall(r"\d+", viewed):
        raise BoundedError("NOTICE_LITERAL_LABEL_NUMERIC_TOKEN_CHANGED")
    proof = {
        "original_text_sha256": digest(text),
        "parser_view_sha256": digest(viewed),
        "literal_replacements": replacements,
        "numeric_tokens_changed": False,
        "financial_values_or_dates_inferred": False,
    }
    proof["identity"] = digest(proof)
    return viewed, proof


def parse_literal_label_cash(
    text: str, *, stock_code: str, ex_date: date, publisher_title: str
) -> dict[str, Any]:
    """Keep the entire predecessor identity/date/currency/gross-cash contract."""
    viewed, proof = literal_label_view(text)
    normalized = re.sub(r"\s+", "", viewed)
    # The predecessor allows an omitted day suffix. It must not interpret the
    # first two digits of a malformed three-digit day as a matching date here.
    for match in _EX_DATE.finditer(normalized):
        if (
            not match[0].endswith("日")
            and match.end() < len(normalized)
            and normalized[match.end()].isdigit()
        ):
            raise BoundedError("NOTICE_LITERAL_LABEL_DATE_TOKEN_BOUNDARY_INVALID")
    atom = parse_adjusted_current_scheme_cash(
        viewed, stock_code=stock_code, ex_date=ex_date, publisher_title=publisher_title
    )
    return {
        **atom,
        "contract_id": CONTRACT_ID,
        "predecessor_parser_contract": atom["contract_id"],
        "original_text_sha256": digest(text),
        "literal_label_evidence": proof,
    }

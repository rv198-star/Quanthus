"""Explicit label/layout equivalents over the frozen current-scope parser.

Period words, issuer values, numeric tokens and source times are retained. The
successor never adds an absent issuer/date or combines separately stated cash.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_notice_current_scope import parse_current_scope_cash
from .base22_notice_scheme import _HEADING

CONTRACT_ID = "p10-base22-explicit-equivalent-company-scope-v7"
_EQUIVALENTS = (
    (r"方案的实施调整为", "方案调整为"),
    (r"年度、(?=\d{4}年第一次中期)", "年度及"),
    (r"第一季度(?=权益分派方案)", "一季度"),
    (r"权益分配方案为", "权益分派方案为"),
    (
        r"现金红利股份类别股权登记日最后交易日除权\(息\)日发放日",
        "股份类别股权登记日最后交易日除权(息)日现金红利发放日",
    ),
    (r"(元(?:人民币)?现金)。(?=\(含税)", r"\1"),
)


def _spaced_literal(match: re.Match[str]) -> str:
    return match.group(0) + r"\s*"


def parse_equivalent_scope_cash(
    text: str, *, stock_code: str, ex_date: date, publisher_title: str
) -> dict[str, Any]:
    try:
        atom = parse_current_scope_cash(
            text, stock_code=stock_code, ex_date=ex_date, publisher_title=publisher_title
        )
    except BoundedError as exc:
        if str(exc) not in {
            "NOTICE_COMPLETE_CURRENT_COMPANY_SCOPE_NOT_UNIQUE_OR_UNAVAILABLE",
            "NOTICE_EXPLICIT_CURRENT_BLOCK_CASH_CONFLICT_OR_UNAVAILABLE",
            "NOTICE_EXACT_SECURITY_CODE_NOT_ESTABLISHED",
            "NOTICE_EXACT_EX_DATE_NOT_ESTABLISHED",
        }:
            raise
    else:
        return {**atom, "contract_id": CONTRACT_ID, "predecessor_contract": atom["contract_id"]}
    view = unicodedata.normalize("NFKC", text)
    # Two identical printed issuer values are not two issuers. Require their
    # original whitespace boundary and the explicit original code label.
    repeat = re.compile(
        r"((?:证券代码|股票代码|A股代码)\s*[:：]?\s*)"
        + re.escape(stock_code)
        + r"\s+(?P<repeated>"
        + re.escape(stock_code)
        + r")(?!\d)"
    )
    view, repetitions = repeat.subn(
        lambda m: m[1] + stock_code + " 证券代码:" + m["repeated"], view
    )
    paragraphs: list[str] = []
    pending: list[str] = []
    for line in view.splitlines():
        if _HEADING.fullmatch(line):
            if pending:
                paragraphs.append(" ".join(pending))
                pending = []
            paragraphs.append(line)
        else:
            pending.append(line)
    if pending:
        paragraphs.append(" ".join(pending))
    view = "\n".join(paragraphs)
    counts = []
    for pattern, replacement in _EQUIVALENTS:
        spaced_pattern = re.sub(r"[\u4e00-\u9fff]", _spaced_literal, pattern)
        view, count = re.subn(spaced_pattern, replacement, view)
        counts.append(count)
    if re.findall(r"\d+", unicodedata.normalize("NFKC", text)) != re.findall(r"\d+", view):
        raise BoundedError("NOTICE_EQUIVALENT_NUMERIC_TOKEN_SEQUENCE_CHANGED")
    atom = parse_current_scope_cash(
        view, stock_code=stock_code, ex_date=ex_date, publisher_title=publisher_title
    )
    evidence = {
        "original_text_sha256": digest(text),
        "label_view_sha256": digest(view),
        "literal_equivalent_counts": counts,
        "identical_issuer_repetitions": repetitions,
        "source_numbers_dates_periods_added": False,
        "predecessor_parser_atom_sha256": digest(atom),
    }
    evidence["identity"] = digest(evidence)
    return {
        **atom,
        "contract_id": CONTRACT_ID,
        "original_text_sha256": digest(text),
        "equivalent_scope_evidence": evidence,
    }

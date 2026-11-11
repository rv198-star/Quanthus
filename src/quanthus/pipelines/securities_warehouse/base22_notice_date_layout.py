"""Exact retained date/cash-label reflow; all explicit claims must still agree."""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_notice_equivalent_scope import parse_equivalent_scope_cash

CONTRACT_ID = "p10-base22-explicit-date-cash-label-layout-v8"
_LABEL = re.compile(r"除\s*权\s*\(\s*除\s*息\s*\)\s*日")
# Two printed header lines, with the complete four ordered column labels.
_TABLE = re.compile(
    r"除\s*权\s*\(\s*息\s*\)\s*现\s*金\s*红\s*利\s*发\s*"
    r"股\s*份\s*类\s*别\s*股\s*权\s*登\s*记\s*日\s*"
    r"最\s*后\s*交\s*易\s*日\s*日\s*放\s*日"
)
_CASH_LABELS = (
    (re.compile(r"现\s*金\s*股\s*息\s*(?=人民币|\d)"), "现金股利"),
    (re.compile(r"现\s*金\s*分\s*红\s*(?=人民币|\d)"), "现金红利"),
)


def parse_exdate_layout_cash(
    text: str, *, stock_code: str, ex_date: date, publisher_title: str
) -> dict[str, Any]:
    view = unicodedata.normalize("NFKC", text)
    view, labels = _LABEL.subn("除权(息)日", view)
    view, tables = _TABLE.subn("股份类别 股权登记日 最后交易日 除权(息)日 现金红利发放日", view)
    cash_labels = []
    for pattern, replacement in _CASH_LABELS:
        view, count = pattern.subn(replacement, view)
        cash_labels.append(count)
    if not labels + tables + sum(cash_labels):
        atom = parse_equivalent_scope_cash(
            text, stock_code=stock_code, ex_date=ex_date, publisher_title=publisher_title
        )
        return {**atom, "contract_id": CONTRACT_ID, "predecessor_contract": atom["contract_id"]}
    if re.findall(r"\d+", unicodedata.normalize("NFKC", text)) != re.findall(r"\d+", view):
        raise BoundedError("NOTICE_DATE_LAYOUT_COMPLETE_LABEL_OR_NUMERIC_PRESERVATION_FAILED")
    # Normalize every explicit literal before delegation. A previously unread
    # cash label must not make a conflicting current claim disappear while an
    # older proposal remains the only recognized amount.
    atom = parse_equivalent_scope_cash(
        view, stock_code=stock_code, ex_date=ex_date, publisher_title=publisher_title
    )
    evidence = {
        "original_text_sha256": digest(text),
        "parser_view_sha256": digest(view),
        "exact_reflowed_exdate_labels": labels,
        "complete_four_column_headers": tables,
        "literal_cash_label_occurrences": cash_labels,
        "numbers_dates_columns_inferred": False,
        "predecessor_atom_sha256": digest(atom),
    }
    evidence["identity"] = digest(evidence)
    return {
        **atom,
        "contract_id": CONTRACT_ID,
        "original_text_sha256": digest(text),
        "date_layout_evidence": evidence,
    }

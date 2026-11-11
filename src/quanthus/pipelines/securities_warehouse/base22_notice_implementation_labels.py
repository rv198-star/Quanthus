"""Opt-in implementation-scheme wording; no numeric or event-scope rewriting."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_notice_labels import literal_label_view, parse_literal_label_cash

CONTRACT_ID = "p10-base22-issuer-company-implementation-label-v5"
_LABEL = re.compile(
    r"(?P<company>(?:本公司|公司)\d{4}(?:年)?(?:年度|半年度)?权益分派)"
    r"实施方案(?=为|如下|[:：])"
)


def parse_implementation_label_cash(
    text: str,
    *,
    stock_code: str,
    ex_date: date,
    publisher_title: str,
) -> dict[str, Any]:
    """Use the whole predecessor guard chain on a numeric-identical parser view.

    Only the literal company/year 'implementation scheme' label is equivalent
    to the already implemented company scheme label. Compound year/dividend
    plans, proposals, approved-only chapters, classes and currencies stay held.
    """
    viewed, prior = literal_label_view(text)
    original = viewed
    viewed, count = _LABEL.subn(r"\g<company>方案", viewed)
    if re.findall(r"\d+", original) != re.findall(r"\d+", viewed):
        raise BoundedError("NOTICE_IMPLEMENTATION_LABEL_NUMERIC_TOKEN_CHANGED")
    atom = parse_literal_label_cash(
        viewed, stock_code=stock_code, ex_date=ex_date, publisher_title=publisher_title
    )
    evidence = {
        "original_text_sha256": digest(text),
        "parser_view_sha256": digest(viewed),
        "company_implementation_label_occurrences": count,
        "predecessor_literal_view_identity": prior["identity"],
        "numeric_tokens_changed": False,
        "financial_values_or_event_scope_inferred": False,
    }
    evidence["identity"] = digest(evidence)
    return {
        **atom,
        "contract_id": CONTRACT_ID,
        "predecessor_parser_contract": atom["contract_id"],
        "original_text_sha256": digest(text),
        "implementation_label_evidence": evidence,
    }

"""Literal A-share current scheme; other-market tax chapters grant no entitlement."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_notice_current_scope import _scope_view
from .base22_notice_literal_statement import select_literal_cash_candidates
from .base22_notice_scheme import _HEADING

CONTRACT_ID = "p10-base22-explicit-A-current-scheme-cash-v11"


def current_a_view(text: str) -> tuple[str, dict[str, Any]]:
    view, previous = _scope_view(text)
    lines = view.splitlines()
    headings = [
        (n, m["title"].strip().rstrip(":："))
        for n, line in enumerate(lines)
        if (m := _HEADING.fullmatch(line))
    ]
    positive = []
    unsafe = False
    for index, (start, name) in enumerate(headings):
        if name != "分配方案":
            continue
        end = headings[index + 1][0] if index + 1 < len(headings) else len(lines)
        block = re.sub(r"\s+", "", "\n".join(lines[start + 1 : end]))
        if any(s in block for s in ("H股", "B股", "港币", "港元", "美元")):
            unsafe = True
        if (
            "全体A股股东" in block
            and "人民币" in block
            and re.search(r"本次(?:利润分配|分红派息)以", block)
        ):
            positive.append(digest(block))
    reflows = 0
    normalized = re.sub(r"\s+", "", view)
    if "H股" in normalized and not unsafe and len(positive) == 1:
        view, reflows = re.subn(r"H\s*股", "原文其他章节的境外股份", view)
    if re.findall(r"\d+", unicodedata.normalize("NFKC", text)) != re.findall(r"\d+", view):
        raise BoundedError("NOTICE_A_SCOPE_NUMERIC_SEQUENCE_CHANGED")
    proof = {
        "contract_id": CONTRACT_ID,
        "original_text_sha256": digest(text),
        "view_sha256": digest(view),
        "predecessor_layout_identity": previous["identity"],
        "positive_current_A_scheme_sha256": positive,
        "other_scheme_unqualified": unsafe,
        "literal_H_label_reflows": reflows,
        "other_market_entitlements_or_currency_qualified": False,
        "numeric_tokens_changed": False,
    }
    proof["identity"] = digest(proof)
    return view, proof


def select_a_cash_candidates(
    targets: list[dict[str, Any]], documents: Iterable[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    originals = {}
    views = []
    proofs = {}
    for d in documents:
        if d["document_id"] in originals:
            raise BoundedError("NOTICE_A_SCOPE_DUPLICATE_DOCUMENT")
        originals[d["document_id"]] = d
        view, p = current_a_view(d["text"])
        proofs[d["document_id"]] = p
        views.append({**d, "text": view})
    selected, evidence = select_literal_cash_candidates(targets, views)
    for candidate in selected.values():
        document = candidate["document_id"]
        candidate["atom"] = {
            **candidate["atom"],
            "contract_id": CONTRACT_ID,
            "original_text_sha256": digest(originals[document]["text"]),
            "A_current_scope_evidence": proofs[document],
        }
    evidence = {
        **{k: v for k, v in evidence.items() if k != "identity"},
        "contract_id": CONTRACT_ID,
        "document_scope_evidence": proofs,
        "other_market_entitlements_or_currency_qualified": False,
    }
    evidence["identity"] = digest(evidence)
    return selected, evidence

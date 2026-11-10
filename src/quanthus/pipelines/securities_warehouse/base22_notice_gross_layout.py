"""Literal gross punctuation and publisher-declared A-share combined amount.

The combined amount must be printed in both the per-share summary and current
scheme. It is never calculated from the two components. Explicit exclusion of
H-share operations is required for that grammar; omitted currency is not added.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_notice_a_scope import current_a_view, select_a_cash_candidates
from .base22_notice_action import _IMPLEMENTED_TITLE, parse_implemented_notice_cash
from .base22_notice_current_scope import _NON_UNIFORM, _scope_view
from .base22_notice_literal_statement import _AGGREGATE, parse_literal_current_cash
from .base22_notice_scheme import _HEADING
from .base22_precision import decimal_profile

CONTRACT_ID = "p10-base22-literal-gross-label-and-declared-A-combined-cash-v12"
_TOTAL = re.compile(
    r"两项合计派发现金红利(?P<amount>(?:0|[1-9]\d*)(?:\.\d+)?)元\(含税(?:\)|[,，;；])"
)


def parse_gross_layout_cash(
    text: str, *, stock_code: str, ex_date: date, publisher_title: str
) -> dict[str, Any]:
    view, scope = current_a_view(text)
    _, predecessor = _scope_view(text)
    excluded = predecessor["H_scope_explicitly_excluded_by_publisher_body"]
    if excluded:
        view = re.sub(r"H\s*股", "正文明确不适用的境外股份", view)
    view, punctuation = re.subn(r"(现\s*金)\s*[,，]\s*(?=\(含税)", r"\1", view)
    if re.findall(r"\d+", unicodedata.normalize("NFKC", text)) != re.findall(r"\d+", view):
        raise BoundedError("NOTICE_GROSS_LAYOUT_NUMERIC_SEQUENCE_CHANGED")
    evidence = {
        "source_text_sha256": digest(text),
        "view_sha256": digest(view),
        "A_scope_identity": scope["identity"],
        "explicit_H_exclusion": excluded,
        "literal_gross_punctuation_reflows": punctuation,
        "aggregate_computed": False,
    }
    try:
        prior = parse_literal_current_cash(
            view, stock_code=stock_code, ex_date=ex_date, publisher_title=publisher_title
        )
    except BoundedError:
        # This unchanged guard checks the full identity, ex-date and foreign
        # scope before an amount-uniqueness failure can reach the new grammar.
        try:
            parse_implemented_notice_cash(
                view, stock_code=stock_code, ex_date=ex_date, publisher_title=publisher_title
            )
        except BoundedError as error:
            if str(error) != "NOTICE_UNIQUE_GROSS_CASH_ATOM_NOT_ESTABLISHED":
                raise
        if not excluded or len(scope["positive_current_A_scheme_sha256"]) != 1:
            raise BoundedError(
                "NOTICE_GROSS_LAYOUT_EXPLICIT_A_SCHEME_AND_EXCLUSION_REQUIRED"
            ) from None
        lines = view.splitlines()
        headings = [
            (n, m["title"].strip().rstrip(":："))
            for n, line in enumerate(lines)
            if (m := _HEADING.fullmatch(line))
        ]
        intro = re.sub(r"\s+", "", "\n".join(lines[: headings[0][0]])) if headings else ""
        claims = list(_AGGREGATE.finditer(intro))
        blocks = []
        for index, (start, title) in enumerate(headings):
            if title == "分配方案":
                stop = headings[index + 1][0] if index + 1 < len(headings) else len(lines)
                blocks.append(re.sub(r"\s+", "", "\n".join(lines[start + 1 : stop])))
        if (
            "重要内容提示" not in intro or "每股分配比例" not in intro
            or len(claims) != 1 or len(blocks) != 1 or _NON_UNIFORM.search(blocks[0])
            or any(s in blocks[0] for s in ("调整后", "实际摊薄", "实施调整为", "虚拟分红"))
        ):
            raise BoundedError("NOTICE_GROSS_LAYOUT_EXACT_SUMMARY_AND_SCHEME_REQUIRED") from None
        totals = list(_TOTAL.finditer(blocks[0]))
        amount = Decimal(claims[0]["amount"])
        if (
            len(totals) != 1 or Decimal(totals[0]["amount"]) != amount
            or amount <= 0 or not decimal_profile(amount, precision=38, scale=10)["fits"]
        ):
            raise BoundedError(
                "NOTICE_GROSS_LAYOUT_PUBLISHER_TOTAL_CONFLICT_OR_PRECISION"
            ) from None
        evidence.update(summary_claim_sha256=digest(claims[0][0]),
                        current_scheme_total_sha256=digest(totals[0][0]))
        prior = {"stock_code": stock_code, "ex_date": ex_date, "currency": "CNY",
                 "cash_per_share": amount, "cash_atom_sha256": digest(["CNY", amount])}
    return {**prior, "contract_id": CONTRACT_ID, "original_text_sha256": digest(text),
            "gross_layout_evidence": evidence, "historical_PIT_admission": False}


def select_gross_layout_candidates(
    targets: list[dict[str, Any]], documents: Iterable[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    docs = list(documents)
    selected, previous = select_a_cash_candidates(targets, docs)
    indexed = {t["action_id"]: t for t in targets}
    diagnostics = []
    for key, target in indexed.items():
        if key in selected:
            continue
        primary = [d for d in docs if key in d["target_action_ids"] and _IMPLEMENTED_TITLE.search(
            re.sub(r"\s+", "", unicodedata.normalize("NFKC", d["publisher_title"]))
        )]
        atoms = []
        for document in primary:
            try:
                if document["publisher_security_code"] != target["stock_code"]:
                    raise BoundedError("NOTICE_GROSS_LAYOUT_PUBLISHER_IDENTITY_REQUIRED")
                atom = parse_gross_layout_cash(
                    document["text"], stock_code=target["stock_code"],
                    ex_date=date.fromisoformat(target["ex_date"]),
                    publisher_title=document["publisher_title"],
                )
            except BoundedError as error:
                diagnostics.append({"action_id": key, "document_id": document["document_id"],
                                    "status": "HELD", "reason": str(error)})
            else:
                atoms.append({"document_id": document["document_id"], "atom": atom})
                diagnostics.append({"action_id": key, "document_id": document["document_id"],
                                    "status": "EXACT_CASH_ATOM", "atom_sha256": digest(atom)})
        if primary and len(atoms) == len(primary) and len({
            a["atom"]["cash_atom_sha256"] for a in atoms
        }) == 1:
            selected[key] = min(atoms, key=lambda a: a["document_id"])
    for candidate in selected.values():
        candidate["atom"] = {**candidate["atom"], "contract_id": CONTRACT_ID}
    evidence = {**{k: v for k, v in previous.items() if k != "identity"},
                "contract_id": CONTRACT_ID, "predecessor_evidence_identity": previous["identity"],
                "gross_layout_dispositions": diagnostics,
                "key_dispositions": {key: "EXACT_PRIMARY_ISSUER_CASH_ONLY" if key in selected
                                     else previous["key_dispositions"][key] for key in indexed}}
    evidence["identity"] = digest(evidence)
    return selected, evidence

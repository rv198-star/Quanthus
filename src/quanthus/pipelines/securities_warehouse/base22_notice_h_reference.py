"""Explicit A entitlements with fully accounted H identity/exclusion references.

The original publisher/body remain immutable. Only literal H references that
grant no entitlement are reflowed, and only with a positive A object and an
explicit H-operation exclusion. Foreign amounts never become CNY amounts.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from datetime import date
from decimal import Decimal, localcontext
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_notice_action import (
    _CASH,
    _DATE_TABLE,
    _IMPLEMENTED_TITLE,
    parse_implemented_notice_cash,
)
from .base22_notice_identity_scope import exact_identity_view, select_exact_identity_candidates
from .base22_precision import decimal_profile

CONTRACT_ID = "p10-base22-literal-A-entitlement-H-reference-closure-v15"
_EXCLUSION = re.compile(
    r"H股(?:股东的)?现金(?:股息|红利|分红)(?:发放|派发)?"
    r"[^。]{0,60}不适用本公告"
)
_REFERENCES = (
    _EXCLUSION,
    re.compile(r"H股(?:简称|代码)[:：]"),
    re.compile(r"H股现金股息派发(?=H股现金股息发放不适用本公告)"),
    re.compile(
        r"H股(?:股息及其派发事项|股息发放的安排|股东的现金(?:分红|红利)有关情况)"
        r"[,，]?(?:请参见|详见)[^。]{0,600}(?:hkexnews\.hk|hkex\.com\.hk)"
        r"[^。]{0,200}(?:公告|通函)"
    ),
)
_YEAR_CASH = re.compile(
    r"每(?P<denominator>10|1)?股(?:派发|派送|派)?\d{4}年度现金红利"
    r"人民币(?P<amount>(?:0|[1-9]\d*)(?:\.\d+)?)元\(含税(?:\)|[,，;；])"
)


def declared_h_reference_view(document: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    title = re.sub(r"\s+", "", unicodedata.normalize("NFKC", document["publisher_title"]))
    text = unicodedata.normalize("NFKC", document["text"])
    compact = re.sub(r"\s+", "", text)
    exclusions = list(_EXCLUSION.finditer(compact))
    positive = (
        "A股" in title
        or "全体A股股东" in compact
        or ("A股每股现金红利" in compact and bool(_DATE_TABLE.search(compact)))
    )
    eligible = (
        positive
        and bool(exclusions)
        and not any(s in compact for s in ("B股", "港币", "港元", "美元"))
    )
    covered = [m for pattern in _REFERENCES for m in pattern.finditer(compact)] if eligible else []
    # Numbers may be original publication dates/codes in a reference, but an
    # amount, per-share grant or tax claim cannot be hidden as an external link.
    covered = [
        m for m in covered if not re.search(r"每\d*股|\d(?:元|美元|港元|港币)|税率|含税", m[0])
    ]
    tokens = list(re.finditer(r"H股", compact))
    accepted = [
        m for m in tokens if any(span.start() <= m.start() < span.end() for span in covered)
    ]
    if len(accepted) != len(tokens):
        raise BoundedError("NOTICE_A_H_REFERENCE_NONEMPTY_UNACCOUNTED_SCOPE")
    if not eligible:
        raise BoundedError("NOTICE_A_H_REFERENCE_EXPLICIT_A_OBJECT_AND_EXCLUSION_REQUIRED")
    offsets = [m.start() for m in re.finditer(r"\S", text)]
    for token in reversed(accepted):
        start, end = offsets[token.start()], offsets[token.start() + 1] + 1
        text = text[:start] + "原文明确排除的境外身份或公告引用" + text[end:]
    text = text.replace("除权(除息)日", "除权(息)日")
    if re.findall(r"\d+", unicodedata.normalize("NFKC", document["text"])) != re.findall(
        r"\d+", text
    ):
        raise BoundedError("NOTICE_A_H_REFERENCE_NUMERIC_SEQUENCE_CHANGED")
    proof = {
        "contract_id": CONTRACT_ID,
        "document_id": document["document_id"],
        "original_text_sha256": digest(document["text"]),
        "view_sha256": digest(text),
        "positive_A_object": positive,
        "explicit_H_exclusions_sha256": [digest(m[0]) for m in exclusions],
        "all_H_references_accounted": True,
        "H_reference_sha256": sorted({digest(m[0]) for m in covered}),
        "H_occurrences": len(tokens),
        "foreign_entitlements_qualified": False,
        "numeric_tokens_changed": False,
        "metadata_or_PDF_rewritten": False,
    }
    proof["identity"] = digest(proof)
    return text, proof


def parse_declared_h_excluded_cash(
    document: dict[str, Any], target: dict[str, Any]
) -> dict[str, Any]:
    if document["publisher_security_code"] != target["stock_code"]:
        raise BoundedError("NOTICE_A_H_REFERENCE_PUBLISHER_IDENTITY_MISMATCH")
    text, scope = declared_h_reference_view(document)
    # The narrow header view is shared, but applied only after the original H
    # reference closure; it cannot supply a missing positive A source object.
    view, header = exact_identity_view({**document, "text": text})
    view_text = view["text"]
    ex = date.fromisoformat(target["ex_date"])
    try:
        atom = parse_implemented_notice_cash(
            view_text,
            stock_code=target["stock_code"],
            ex_date=ex,
            publisher_title=view["publisher_title"],
        )
    except BoundedError as error:
        # All identity/date/market gates are checked by the common parser first.
        # Only its amount-uniqueness result may reach the new literal-year claim.
        if str(error) != "NOTICE_UNIQUE_GROSS_CASH_ATOM_NOT_ESTABLISHED":
            raise
        atom = None
    normalized = re.sub(r"\s+", "", view_text)
    year_claims = list(_YEAR_CASH.finditer(normalized))
    if len(
        list(re.finditer(r"每(?:10|1)?股(?:派发|派送|派)?\d{4}年度现金红利", normalized))
    ) != len(year_claims):
        raise BoundedError("NOTICE_A_H_REFERENCE_NONEMPTY_YEAR_CLAIM_UNQUALIFIED")
    if atom is None or year_claims:
        # Check both claim families even if the old family already parsed. A
        # successful ordinary claim cannot hide a conflicting literal-year one.
        claims = list(_CASH.finditer(normalized)) + year_claims
        values = set()
        with localcontext() as context:
            context.prec = 64
            for claim in claims:
                token = claim["amount"]
                if token is None:
                    token = claim["post_amount"]
                values.add(Decimal(token) / Decimal(claim["denominator"] or "1"))
        if len(values) != 1:
            raise BoundedError("NOTICE_A_H_REFERENCE_LITERAL_GROSS_CASH_NOT_UNIQUE") from None
        amount = next(iter(values))
        if amount <= 0 or not decimal_profile(amount, precision=38, scale=10)["fits"]:
            raise BoundedError("NOTICE_A_H_REFERENCE_EXACT_GROSS_DECIMAL_REQUIRED") from None
        atom = {
            "stock_code": target["stock_code"],
            "ex_date": ex,
            "currency": "CNY",
            "cash_per_share": amount,
            "cash_atom_sha256": digest(["CNY", amount]),
            "literal_year_claim_sha256": [digest(m[0]) for m in claims],
        }
    return {
        **atom,
        "contract_id": CONTRACT_ID,
        "original_text_sha256": digest(document["text"]),
        "original_publisher_title_sha256": digest(document["publisher_title"]),
        "H_reference_scope_evidence": scope,
        "exact_header_evidence": header,
        "historical_PIT_admission": False,
        "aggregate_computed": False,
    }


def select_h_excluded_cash_candidates(
    targets: list[dict[str, Any]], documents: Iterable[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    docs = list(documents)
    selected, previous = select_exact_identity_candidates(targets, docs)
    diagnostics = []
    for target in targets:
        key = target["action_id"]
        if key in selected:
            continue
        primary = [
            d
            for d in docs
            if key in d["target_action_ids"]
            and _IMPLEMENTED_TITLE.search(
                re.sub(r"\s+", "", unicodedata.normalize("NFKC", d["publisher_title"]))
            )
        ]
        candidates = []
        for doc in primary:
            try:
                atom = parse_declared_h_excluded_cash(doc, target)
            except BoundedError as error:
                diagnostics.append(
                    {
                        "action_id": key,
                        "document_id": doc["document_id"],
                        "status": "HELD",
                        "reason": str(error),
                    }
                )
            else:
                candidates.append({"document_id": doc["document_id"], "atom": atom})
                diagnostics.append(
                    {
                        "action_id": key,
                        "document_id": doc["document_id"],
                        "status": "EXACT_CASH_ATOM",
                        "atom_sha256": digest(atom),
                    }
                )
        if (
            primary
            and len(candidates) == len(primary)
            and len({a["atom"]["cash_atom_sha256"] for a in candidates}) == 1
        ):
            selected[key] = min(candidates, key=lambda c: c["document_id"])
    for candidate in selected.values():
        candidate["atom"] = {**candidate["atom"], "contract_id": CONTRACT_ID}
    proof = {
        **{k: v for k, v in previous.items() if k != "identity"},
        "contract_id": CONTRACT_ID,
        "H_reference_closure_dispositions": diagnostics,
        "predecessor_evidence_identity": previous["identity"],
        "key_dispositions": {
            t["action_id"]: "EXACT_PRIMARY_ISSUER_CASH_ONLY"
            if t["action_id"] in selected
            else previous["key_dispositions"][t["action_id"]]
            for t in targets
        },
    }
    proof["identity"] = digest(proof)
    return selected, proof

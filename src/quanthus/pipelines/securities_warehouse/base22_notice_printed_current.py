"""Literal current A cash, including source-printed combined distributions.

No sum, diluted price, foreign entitlement or shareholder membership is inferred.
Full original clauses remain in local evidence; scope witnesses bind every H label.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from datetime import date
from decimal import Decimal, localcontext
from typing import Any

from .base22 import THROUGH, digest
from .base22_bounded import BoundedError
from .base22_notice_ab_scope import _market_dates, select_a_b_cash_candidates
from .base22_notice_action import _CASH, _IMPLEMENTED_TITLE, MAX_TEXT_BYTES
from .base22_notice_h_reference import declared_h_reference_view
from .base22_notice_scheme import _HEADING
from .base22_precision import decimal_profile

CONTRACT_ID = "p10-base22-literal-current-A-and-printed-combined-gross-v17"
_NUMBER = r"(?:0|[1-9]\d*)(?:\.\d+)?"
_SUMMARY = re.compile(rf"A股每股现金红利(?:人民币)?(?P<amount>{_NUMBER})元(?P<gross>\(含税)?")
_TOTAL = re.compile(rf"合计向全体股东每股派发现金红利(?P<amount>{_NUMBER})元\(含税\)")
_COMBINED = re.compile(
    rf"A股每股现金红利(?P<amount>{_NUMBER})元[,，]为\d{{4}}年度和\d{{4}}年一季度"
    r"合并后的分配比例[,，]其中"
)
_CAPITAL = re.compile(r"H股总股本为[\d,，]+股")


def parse_printed_current_cash(document: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    text = document["text"]
    if not isinstance(text, str) or not 0 < len(text.encode()) <= MAX_TEXT_BYTES:
        raise BoundedError("NOTICE_PRINTED_CURRENT_TEXT_INVALID")
    title = re.sub(r"\s+", "", unicodedata.normalize("NFKC", document["publisher_title"]))
    code = target["stock_code"]
    if document["publisher_security_code"] != code or not _IMPLEMENTED_TITLE.search(title):
        raise BoundedError("NOTICE_PRINTED_CURRENT_EXACT_PRIMARY_PUBLISHER_REQUIRED")
    original = unicodedata.normalize("NFKC", text)
    first = next((line for line in original.splitlines() if line.strip()), "")
    header = re.search(r"(?:证券代码|股票代码|A股代码)[:：]?\s*(\d{6})(?!\d)", first)
    if not header or header[1] != code:
        raise BoundedError("NOTICE_PRINTED_CURRENT_LITERAL_SECURITY_HEADER_REQUIRED")
    ex = date.fromisoformat(target["ex_date"])
    if ex > THROUGH:
        raise BoundedError("NOTICE_PRINTED_CURRENT_BUSINESS_CUTOFF")
    lines = original.splitlines()
    headings = [
        (n, m["title"].strip().rstrip(":："))
        for n, line in enumerate(lines)
        if (m := _HEADING.fullmatch(line))
    ]
    sections = [
        (
            name,
            re.sub(
                r"\s+",
                "",
                "\n".join(
                    lines[
                        start + 1 : headings[index + 1][0]
                        if index + 1 < len(headings)
                        else len(lines)
                    ]
                ),
            ),
        )
        for index, (start, name) in enumerate(headings)
    ]
    current = [block for name, block in sections if name in {"分配方案", "本次实施的权益分派方案"}]
    if len(current) != 1 or any(s in current[0] for s in ("社会公众股", "上市前", "原三家", "B股")):
        raise BoundedError("NOTICE_PRINTED_CURRENT_COMPLETE_UNIFORM_A_SCOPE_REQUIRED")
    block = current[0]
    normalized = re.sub(r"\s+", "", original)
    if any(s in normalized for s in ("B股", "美元", "港币", "港元")):
        raise BoundedError("NOTICE_PRINTED_CURRENT_FOREIGN_CURRENCY_UNQUALIFIED")
    intro = re.sub(r"\s+", "", "\n".join(lines[: headings[0][0]]))
    summary = _SUMMARY.search(intro)
    explicit_declaration = bool(re.search(r"公司实施的\d{4}年度A股权益分派方案为:", block))
    if not summary and not ("A股" in title and explicit_declaration):
        raise BoundedError("NOTICE_PRINTED_CURRENT_EXPLICIT_A_OBJECT_REQUIRED")
    # Different eligible populations never collapse to a scalar. Only the
    # expressly excluded repurchase account is a permitted denominator scope.
    if ("差异化" in block or "差别化" in block) and not re.search(
        r"(?:扣除回购专户中的股份|扣减回购专用证券账户中股份|剔除回购专用账户中已回购股份)", block
    ):
        raise BoundedError("NOTICE_PRINTED_CURRENT_ELIGIBLE_POPULATION_UNPROVEN")
    roles = []
    if "H股" in normalized:
        try:
            _, evidence = declared_h_reference_view(document)
        except BoundedError:
            evidence = None
        if evidence is not None:
            roles.append({"role": "EXPLICIT_EXCLUDED_H_REFERENCE", "evidence": evidence})
        else:
            prior = [b for name, b in sections if name == "股东会审议通过的权益分派方案情况"]
            capital = list(_CAPITAL.finditer(prior[0])) if len(prior) == 1 else []
            if explicit_declaration and capital and normalized.count("H股") == len(capital):
                roles.append(
                    {
                        "role": "PRIOR_APPROVED_TOTAL_CAPITAL_ONLY",
                        "witness_sha256": [digest(m[0]) for m in capital],
                    }
                )
            else:
                method = [b for name, b in sections if name == "分配实施办法"]
                tax = (
                    re.search(
                        r"\(\d+\)对于上海证券交易所投资者[^。]{0,250}投资香港联交所本公司H股股票"
                        r"(?P<rest>.*)",
                        method[0],
                    )
                    if len(method) == 1
                    else None
                )
                if (
                    not summary
                    or not summary["gross"]
                    or not tax
                    or normalized.count("H股") != tax[0].count("H股")
                    or "港股通H股股票现金红利派发协议" not in tax[0]
                    or "现金红利以人民币派发" not in tax[0]
                    or "请参见" not in tax[0]
                    or _CASH.search(tax[0])
                    or re.search(r"每(?:\d+)?股", tax[0])
                ):
                    raise BoundedError("NOTICE_PRINTED_CURRENT_H_ROLE_UNACCOUNTED")
                roles.append(
                    {
                        "role": "LITERAL_FOREIGN_TAX_AND_REFERENCE_PARAGRAPH_ONLY",
                        "witness_sha256": digest(tax[0]),
                    }
                )
    dates, date_proof = _market_dates(normalized)
    if dates != {ex}:
        raise BoundedError("NOTICE_PRINTED_CURRENT_EXACT_A_DATE_REQUIRED")
    totals = list(_TOTAL.finditer(block))
    combined = _COMBINED.search(intro)
    view = re.sub(r"现金分红(?=人民币)", "现金红利", block)
    claims = list(_CASH.finditer(view))
    with localcontext() as context:
        context.prec = 64
        values = {
            Decimal(m["amount"] or m["post_amount"]) / Decimal(m["denominator"] or "1")
            for m in claims
        }
    if totals:
        totals_values = {Decimal(m["amount"]) for m in totals}
        if values != totals_values:
            raise BoundedError("NOTICE_PRINTED_CURRENT_ADDITIONAL_GROSS_CLAIM_CONFLICT")
        values = totals_values
        mode = "ORIGINAL_PRINTED_CURRENT_COMBINED_GROSS"
        if not summary or not summary["gross"]:
            raise BoundedError("NOTICE_PRINTED_CURRENT_TOTAL_NEEDS_GROSS_A_SUMMARY")
    elif combined:
        # The source states the combined field; never obtain it by summing
        # component values. The original exempt-institution clause confirms its
        # tax basis independently of the diluted calculation subsection.
        actual = Decimal(combined["amount"])
        methods = [b for name, b in sections if name == "分配实施办法"]
        exempt = (
            re.search(
                rf"对于其他机构投资者及法人股东[,，]其所得税自行缴纳[,，]实际派发每股现金红利为"
                rf"人民币(?P<amount>{_NUMBER})元",
                methods[0],
            )
            if len(methods) == 1
            else None
        )
        eligible = block.split("本次差异化分红除权除息的计算依据", 1)[0]
        components = list(_CASH.finditer(eligible))
        if (
            not exempt
            or Decimal(exempt["amount"]) != actual
            or len(components) != 2
            or not re.search(r"\d{4}年度利润分配方案", eligible)
            or not re.search(r"\d{4}年一季度利润分配方案", eligible)
        ):
            raise BoundedError("NOTICE_PRINTED_CURRENT_COMBINED_GROSS_BASIS_INCOMPLETE")
        values = {actual}
        roles.append(
            {
                "role": "PRINTED_COMBINED_AND_EXEMPT_INSTITUTION_GROSS",
                "witness_sha256": [
                    digest(combined[0]),
                    digest(exempt[0]),
                    *[digest(m[0]) for m in components],
                ],
            }
        )
        mode = "ORIGINAL_COMBINED_FIELD_NOT_COMPONENT_ARITHMETIC"
    else:
        mode = "ORIGINAL_CURRENT_UNIFORM_A_GROSS"
    if len(values) != 1 or (summary and Decimal(summary["amount"]) not in values):
        raise BoundedError("NOTICE_PRINTED_CURRENT_AMOUNT_CONFLICT")
    amount = next(iter(values))
    if amount <= 0 or not decimal_profile(amount, precision=38, scale=10)["fits"]:
        raise BoundedError("NOTICE_PRINTED_CURRENT_EXACT_DECIMAL_REQUIRED")
    proof = {
        "contract_id": CONTRACT_ID,
        "mode": mode,
        "literal_header_sha256": digest(header[0]),
        "complete_current_scheme_sha256": digest(block),
        "original_roles": roles,
        "original_claims_sha256": [digest(m[0]) for m in claims],
        "aggregate_computed": False,
        "diluted_amount_used": False,
        "foreign_entitlements_qualified": False,
        "all_H_references_accounted": True,
        "eligible_population_inferred": False,
        **date_proof,
    }
    proof["identity"] = digest(proof)
    return {
        "contract_id": CONTRACT_ID,
        "stock_code": code,
        "ex_date": ex,
        "currency": "CNY",
        "cash_per_share": amount,
        "cash_atom_sha256": digest(["CNY", amount]),
        "original_text_sha256": digest(text),
        "original_publisher_title_sha256": digest(document["publisher_title"]),
        "printed_current_scope_evidence": proof,
        "historical_PIT_admission": False,
        "aggregate_computed": False,
    }


def select_printed_current_candidates(
    targets: list[dict[str, Any]], documents: Iterable[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    docs = list(documents)
    selected, previous = select_a_b_cash_candidates(targets, docs)
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
                atom = parse_printed_current_cash(doc, target)
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
            and len({c["atom"]["cash_atom_sha256"] for c in candidates}) == 1
        ):
            selected[key] = min(candidates, key=lambda c: c["document_id"])
    for candidate in selected.values():
        candidate["atom"] = {**candidate["atom"], "contract_id": CONTRACT_ID}
    proof = {
        **{k: v for k, v in previous.items() if k != "identity"},
        "contract_id": CONTRACT_ID,
        "printed_current_dispositions": diagnostics,
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

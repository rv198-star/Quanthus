"""Explicit implemented company scope, separate from approved and class-specific cash.

This opt-in successor interprets literal section/declaration grammar. It never
selects the largest/last amount, computes dilution, or supplies an issuer/date.
The original publisher/security/date/unit/precision gates still run first.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal, localcontext
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_notice_action import _CASH, parse_implemented_notice_cash
from .base22_notice_implementation_labels import parse_implementation_label_cash
from .base22_notice_labels import literal_label_view
from .base22_notice_scheme import _HEADING
from .base22_precision import decimal_profile

CONTRACT_ID = "p10-base22-explicit-current-company-cash-scope-v6"
_TITLES = {
    "权益分派方案",
    "本次实施的利润分配方案",
    "本次实施的权益分派方案",
    "分配方案",
    "利润分配方案",
    "本次利润分配方案",
    "权益分派方案及调整说明",
}
_COMPANY = (
    r"(?:本公司|公司)\d{4}(?:年)?(?:年度|半年度)?"
    r"(?:及\d{4}年(?:第一次中期|首次中期|一季度))?"
    r"(?:(?:及回报股东特别分红|中期分红及回报股东特别分红))?"
    r"(?:A股)?(?:权益分派|利润分配|分派)(?:实施)?方案"
)
_DECLARATION = re.compile(_COMPANY + r"(?P<adjusted>调整)?(?:合并)?(?:为|如下)[:：]")
_CURRENT_DILUTED = re.compile(r"摊薄后实际向全体股东(?=每)")
_CURRENT_PLAN = re.compile(r"本次利润分配以方案实施前的公司总股本")
_NON_UNIFORM = re.compile(r"(?:差异化|差别化)(?:分红|送转|利润分配|权益分派)")
_DATE_LABEL = "除权(除息)日"
_LITERAL_CASH_LABELS = (
    (r"现金分红(?=人民币|\d)", "现金红利"),
    (r"现金股息(?=人民币|\d)", "现金股利"),
    (r"分派(?=现金股利|现金红利)", "派发"),
    (r"(现金红利|现金股利)为(?=人民币|\d)", r"\1"),
    (r"(元(?:人民币)?(?:现金)?)[,，](?=\(含税)", r"\1"),
)
_HISTORICAL_APPROVAL = re.compile(r"(?:根据|按照)公司[^。:：]{0,60}股东大会[^。:：]{0,30}审议通过")


def _scope_view(text: str) -> tuple[str, dict[str, Any]]:
    view, literal = literal_label_view(text)
    # The literal date label is equivalent; dates and all numeric tokens stay fixed.
    view = unicodedata.normalize("NFKC", view)
    date_labels = view.count(_DATE_LABEL)
    view = view.replace(_DATE_LABEL, "除权(息)日")
    label_counts = []
    for pattern, replacement in _LITERAL_CASH_LABELS:
        view, count = re.subn(pattern, replacement, view)
        label_counts.append(count)
    normalized = re.sub(r"\s+", "", view)
    excluded_h = False
    if "H股" in normalized:
        # No inference from a publisher code or an A-share amount alone. Both
        # positive domestic scope and explicit exclusion must exist in the body.
        exclusion = re.search(r"本公司H股股东的现金红利发放[^。]{0,60}不适用本公告", normalized)
        domestic = re.search(r"本公司全体A股股东", normalized)
        if (
            exclusion
            and domestic
            and not any(s in normalized for s in ("B股", "港币", "港元", "美元"))
        ):
            excluded_h = True
            view = view.replace("H股", "明确不适用本公告的境外股份")
    if re.findall(r"\d+", unicodedata.normalize("NFKC", text)) != re.findall(r"\d+", view):
        raise BoundedError("NOTICE_CURRENT_SCOPE_NUMERIC_TOKEN_SEQUENCE_CHANGED")
    proof = {
        "literal_predecessor_identity": literal["identity"],
        "original_text_sha256": digest(text),
        "scope_view_sha256": digest(view),
        "explicit_equivalent_date_labels": date_labels,
        "literal_cash_label_occurrences": label_counts,
        "H_scope_explicitly_excluded_by_publisher_body": excluded_h,
        "numeric_tokens_changed": False,
    }
    proof["identity"] = digest(proof)
    return view, proof


def parse_current_scope_cash(
    text: str, *, stock_code: str, ex_date: date, publisher_title: str
) -> dict[str, Any]:
    """Use literal current declarations only; nonuniform entitlements remain held."""
    try:
        prior = parse_implementation_label_cash(
            text, stock_code=stock_code, ex_date=ex_date, publisher_title=publisher_title
        )
    except BoundedError as exc:
        if str(exc) not in {
            "NOTICE_EXPLICIT_CURRENT_IMPLEMENTATION_SECTION_UNAVAILABLE",
            "NOTICE_EXPLICIT_SCHEME_ADJUSTMENT_CURRENT_CASH_CONFLICT",
            "NOTICE_CURRENT_IMPLEMENTATION_SECTION_CASH_CONFLICT",
            "NOTICE_EXACT_EX_DATE_NOT_ESTABLISHED",
            "NOTICE_MIXED_SECURITY_OR_CURRENCY_SCOPE_UNQUALIFIED",
        }:
            raise
        prior = None
    if prior is not None:
        return {**prior, "contract_id": CONTRACT_ID, "predecessor_contract": prior["contract_id"]}
    view, evidence = _scope_view(text)
    try:
        prior = parse_implemented_notice_cash(
            view, stock_code=stock_code, ex_date=ex_date, publisher_title=publisher_title
        )
    except BoundedError as exc:
        if str(exc) != "NOTICE_UNIQUE_GROSS_CASH_ATOM_NOT_ESTABLISHED":
            raise
    else:
        return {
            **prior,
            "contract_id": CONTRACT_ID,
            "original_text_sha256": digest(text),
            "current_scope_evidence": evidence,
        }
    # A uniqueness failure occurs after every unchanged predecessor global guard.
    lines = view.splitlines()
    headings = [
        (n, m["title"].strip().rstrip(":："))
        for n, line in enumerate(lines)
        if (m := _HEADING.fullmatch(line))
    ]
    all_amounts: set[Decimal] = set()
    blocks = []
    tokens = []
    with localcontext() as context:
        context.prec = 64
        for index, (start, title) in enumerate(headings):
            if title not in _TITLES:
                continue
            stop = headings[index + 1][0] if index + 1 < len(headings) else len(lines)
            original = "\n".join(lines[start + 1 : stop])
            section = re.sub(r"\s+", "", original)
            if _NON_UNIFORM.search(section):
                raise BoundedError("NOTICE_NONUNIFORM_ENTITLEMENT_NEEDS_DISTINCT_QUALIFIED_TERMS")
            matches = list(_DECLARATION.finditer(section))
            adjusted = [m for m in matches if m["adjusted"]]
            diluted = list(_CURRENT_DILUTED.finditer(section))
            if adjusted and diluted:
                raise BoundedError("NOTICE_MULTIPLE_CURRENT_ADJUSTMENT_GRAMMARS_UNQUALIFIED")
            if adjusted:
                selected = [
                    (m.start(), m.end(), "EXPLICIT_COMPANY_SCHEME_ADJUSTED_TO") for m in adjusted
                ]
            elif diluted:
                selected = [
                    (m.start(), m.end(), "LITERAL_DILUTED_ACTUAL_ALL_SHAREHOLDER_AMOUNT")
                    for m in diluted
                ]
            else:
                selected = []
                for m in matches:
                    # A declaration inside a proposal/approved historical clause
                    # cannot be promoted simply because this chapter is current.
                    prefix = section[max(section.rfind("。", 0, m.start()) + 1, 0) : m.start()]
                    if re.search(r"拟|预案|审议通过|原为", prefix[-160:]):
                        continue
                    selected.append((m.start(), m.end(), "EXPLICIT_COMPANY_CURRENT_DECLARATION"))
                selected += [
                    (m.start(), m.end(), "EXPLICIT_CURRENT_IMPLEMENTED_TOTAL_BASE")
                    for m in _CURRENT_PLAN.finditer(section)
                ]
            for begin, after_label, grammar in selected:
                next_declarations = [m.start() for m in matches if m.start() > begin]
                end = min(next_declarations) if next_declarations else len(section)
                historical = _HISTORICAL_APPROVAL.search(section, after_label, end)
                if historical:
                    end = historical.start()
                scope = section[after_label:end]
                values = set()
                for atom in _CASH.finditer(scope):
                    number = atom["amount"] or atom["post_amount"]
                    denominator = int(atom["denominator"] or "1")
                    amount = Decimal(number) / Decimal(denominator)
                    if amount <= 0 or not decimal_profile(amount, precision=38, scale=10)["fits"]:
                        raise BoundedError("NOTICE_CURRENT_SCOPE_GROSS_CASH_NOT_EXACT")
                    values.add(amount)
                    tokens.append(
                        {
                            "original_numeric_token_sha256": digest(number),
                            "source_denominator": denominator,
                        }
                    )
                if len(values) != 1:
                    raise BoundedError("NOTICE_EXPLICIT_CURRENT_BLOCK_CASH_CONFLICT_OR_UNAVAILABLE")
                all_amounts.update(values)
                blocks.append(
                    {
                        "heading_sha256": digest(title),
                        "section_sha256": digest(original),
                        "literal_current_block_sha256": digest(section[begin:end]),
                        "grammar": grammar,
                    }
                )
    if not blocks or len(all_amounts) != 1:
        raise BoundedError("NOTICE_COMPLETE_CURRENT_COMPANY_SCOPE_NOT_UNIQUE_OR_UNAVAILABLE")
    amount = next(iter(all_amounts))
    return {
        "contract_id": CONTRACT_ID,
        "stock_code": stock_code,
        "ex_date": ex_date,
        "currency": "CNY",
        "cash_per_share": amount,
        "source_unit": "explicit_current_company_scope_gross_CNY_per_one_or_ten_shares",
        "original_text_sha256": digest(text),
        "publisher_title_sha256": digest(publisher_title),
        "cash_atom_sha256": digest(["CNY", amount]),
        "matched_tokens": tokens,
        "explicit_current_blocks": blocks,
        "current_scope_evidence": evidence,
        "legacy_global_guards": "PASS_BEFORE_CURRENT_SCOPE_UNIQUENESS_RESOLUTION",
        "source_priority_granted": False,
        "historical_publication_or_acquisition_inferred": False,
        "other_action_terms_qualified": False,
    }

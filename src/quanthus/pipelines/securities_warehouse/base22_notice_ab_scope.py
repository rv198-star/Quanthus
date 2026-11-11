"""Selected literal A-share gross cash in retained multi-market issuer notices.

This opt-in source contract binds the publisher and literal security header,
the A date and gross CNY claim. It never converts B payments, adopts B dates,
infers aliases, computes an aggregate, or grants foreign-market qualification.
Original foreign observations remain visible in the local evidence closure.
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
from .base22_notice_action import _CASH, _DATE_TABLE, _EX_DATE, _IMPLEMENTED_TITLE, MAX_TEXT_BYTES
from .base22_notice_action import (
    CONTRACT_ID as MAINLAND_UNIT_CONTRACT,
)
from .base22_notice_current_scope import _NON_UNIFORM
from .base22_notice_h_reference import select_h_excluded_cash_candidates
from .base22_notice_scheme import _HEADING
from .base22_precision import decimal_profile

CONTRACT_ID = "p10-base22-selected-literal-A-gross-currency-and-market-date-v16"
_AMOUNT = r"(?:0|[1-9]\d*)(?:\.\d+)?"
_A_GROSS = re.compile(
    rf"A股每股现金红利(?:\((?:扣税前|税前)\))?(?:人民币)?(?P<amount>{_AMOUNT})"
    r"(?P<unit>元)?(?:\(含税(?:\)|[,，;；]))?(?![\d.]|美元|港元|港币)"
)
_CHINESE_DATE = r"\d{4}年\d{1,2}月\d{1,2}日"
_A_DATES = re.compile(
    rf"A股股权登记日为(?P<record>{_CHINESE_DATE})\(T日\)[,，]"
    rf"除息日为(?P<ex>{_CHINESE_DATE})\(T\+\d+日\)"
)
_B_DATES = re.compile(
    rf"B股最后交易日为(?P<last>{_CHINESE_DATE})\(T日\)[,，]"
    rf"除息日为(?P<ex>{_CHINESE_DATE})\(T\+\d+日\)"
)
_GENERIC_GROSS = re.compile(
    rf"每股现金红利\(扣税前\)(?P<amount>{_AMOUNT})(?:元)?"
    rf"每股现金红利\(扣税后\)(?:{_AMOUNT})(?:元)?"
)
_SCHEMES = {"分配方案", "利润分配方案", "分配、转增股本方案", "分红派息方案"}


def _day(token: str) -> date:
    try:
        return date(*(int(v) for v in re.findall(r"\d+", token)))
    except ValueError:
        raise BoundedError("NOTICE_SELECTED_A_LITERAL_DATE_INVALID") from None


def _primary_role(document: dict[str, Any]) -> bool:
    title = re.sub(r"\s+", "", unicodedata.normalize("NFKC", document["publisher_title"]))
    if _IMPLEMENTED_TITLE.search(title):
        return True
    if not re.fullmatch(r"\d{4}年度分红派息公告", title):
        return False
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", document["text"]))
    return bool(
        re.search(r"方案(?:已|已经)(?:经|获)?[^。]{0,200}股东大会[^。]{0,80}审议通过", text)
        and "现将有关实施事宜公告如下" in text
        and not re.search(r"实施公告将|预计时间安排", text)
    )


def _market_dates(normalized: str) -> tuple[set[date], dict[str, Any]]:
    view = normalized.replace("除权(除息)日", "除权(息)日")
    specific_a = list(_A_DATES.finditer(view))
    specific_b = list(_B_DATES.finditer(view))
    if specific_a:
        if not specific_b:
            raise BoundedError("NOTICE_SELECTED_A_OTHER_MARKET_DATE_BOUNDARY_INCOMPLETE")
        if any(_day(m["record"]) > _day(m["ex"]) for m in specific_a):
            raise BoundedError("NOTICE_SELECTED_A_RECORD_AFTER_EX_DATE")
        # Retain and classify every inline ex-date. A floating shared label or
        # another unbound market date cannot be dropped to fit the target.
        for claim in _EX_DATE.finditer(view):
            if not any(m.start() <= claim.start() < m.end() for m in specific_a + specific_b):
                raise BoundedError("NOTICE_SELECTED_A_NONEMPTY_EX_DATE_UNACCOUNTED")
        dates = {_day(m["ex"]) for m in specific_a}
        mode = "EXPLICIT_SEPARATE_A_AND_B_IMPLEMENTATION_FIELDS"
        witnesses = [digest(m[0]) for m in specific_a]
    else:
        dates = {
            date(*(int(m[n]) for n in ("year", "month", "day"))) for m in _EX_DATE.finditer(view)
        }
        tables = list(_DATE_TABLE.finditer(view))
        if any(not (_day(m["record"]) <= _day(m["ex"]) <= _day(m["pay"])) for m in tables):
            raise BoundedError("NOTICE_SELECTED_A_TABLE_RECORD_EX_PAY_ORDER_INVALID")
        dates.update(_day(m["ex"]) for m in tables)
        mode = "EXPLICIT_A_FULL_TABLE_OR_SHARED_ISSUER_EX_DATE"
        witnesses = [digest(m[0]) for m in tables] + [digest(m[0]) for m in _EX_DATE.finditer(view)]
    return dates, {
        "mode": mode,
        "selected_A_date_witnesses_sha256": witnesses,
        "B_date_witnesses_sha256": [digest(m[0]) for m in specific_b],
        "other_market_date_reused": False,
    }


def parse_selected_a_cash(document: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    code = target["stock_code"]
    text = document["text"]
    if (
        not isinstance(text, str)
        or not 0 < len(text.encode()) <= MAX_TEXT_BYTES
        or not re.fullmatch(r"\d{6}", code)
        or document["publisher_security_code"] != code
        or not _primary_role(document)
    ):
        raise BoundedError("NOTICE_SELECTED_A_EXACT_PUBLISHER_AND_PRIMARY_ROLE_REQUIRED")
    ex_date = date.fromisoformat(target["ex_date"])
    if ex_date > THROUGH:
        raise BoundedError("NOTICE_SELECTED_A_BUSINESS_DATE_AFTER_IMAGE_CUTOFF")
    original = unicodedata.normalize("NFKC", text)
    first = next((line for line in original.splitlines() if line.strip()), "")
    header = re.search(r"(?:证券代码|股票代码|A股代码)[:：]?\s*(?P<code>\d{6})(?!\d)", first)
    if not header or header["code"] != code:
        raise BoundedError("NOTICE_SELECTED_A_FIRST_LITERAL_CODE_AND_PUBLISHER_MUST_AGREE")
    normalized = re.sub(r"\s+", "", original)
    if "A股" not in normalized or "B股" not in normalized or "H股" in normalized:
        raise BoundedError("NOTICE_SELECTED_A_EXPLICIT_AB_SOURCE_SCOPE_REQUIRED")
    dates, date_proof = _market_dates(normalized)
    if dates != {ex_date}:
        raise BoundedError("NOTICE_SELECTED_A_EXACT_MARKET_EX_DATE_REQUIRED")
    lines = original.splitlines()
    headings = [
        (n, m["title"].strip().rstrip(":："))
        for n, line in enumerate(lines)
        if (m := _HEADING.fullmatch(line))
    ]
    if not headings:
        raise BoundedError("NOTICE_SELECTED_A_COMPLETE_CURRENT_SECTION_REQUIRED")
    intro = re.sub(r"\s+", "", "\n".join(lines[: headings[0][0]]))
    gross = list(_A_GROSS.finditer(intro))
    if any(m["unit"] is None for m in gross) and "单位:元" not in intro:
        raise BoundedError("NOTICE_SELECTED_A_LITERAL_GROSS_UNIT_REQUIRED")
    # This exact original table grammar puts the ordinary gross/net fields
    # under a literal yuan unit, then explicitly labels the B currency fields.
    # Never search past the B boundary or use an unlabelled arbitrary number.
    generic = []
    if not gross and "单位:元" in intro and "B股每股现金红利" in intro:
        ordinary = intro.split("单位:元", 1)[1].split("B股每股现金红利", 1)[0]
        generic = list(_GENERIC_GROSS.finditer(ordinary))
        if len(generic) != 1:
            raise BoundedError("NOTICE_SELECTED_A_EXACT_ORDINARY_GROSS_COLUMN_REQUIRED")
        gross = generic
    amounts = {Decimal(m["amount"]) for m in gross}
    blocks = []
    for index, (start, name) in enumerate(headings):
        if name in _SCHEMES:
            end = headings[index + 1][0] if index + 1 < len(headings) else len(lines)
            blocks.append(re.sub(r"\s+", "", "\n".join(lines[start + 1 : end])))
    if len(blocks) != 1 or _NON_UNIFORM.search(blocks[0]):
        raise BoundedError("NOTICE_SELECTED_A_CURRENT_ELIGIBLE_SCOPE_UNQUALIFIED")
    claims = list(_CASH.finditer(blocks[0]))
    with localcontext() as context:
        context.prec = 64
        actual = {
            Decimal(m["amount"] or m["post_amount"]) / Decimal(m["denominator"] or "1")
            for m in claims
        }
    # An explicit A summary is required when available; an exact common gross
    # CNY scheme may also qualify when the original text separately defines the
    # A and B dated objects and states only B payment is converted abroad.
    separate_dates = date_proof["mode"] == "EXPLICIT_SEPARATE_A_AND_B_IMPLEMENTATION_FIELDS"
    if not gross and not separate_dates:
        raise BoundedError("NOTICE_SELECTED_A_EXPLICIT_GROSS_SUMMARY_REQUIRED")
    if len(actual) != 1 or (gross and amounts != actual):
        raise BoundedError("NOTICE_SELECTED_A_CURRENT_GROSS_AND_SUMMARY_CONFLICT")
    if any("人民币" not in m[0] for m in claims) and not gross:
        raise BoundedError("NOTICE_SELECTED_A_EXPLICIT_COMMON_CNY_REQUIRED")
    amount = next(iter(actual))
    if amount <= 0 or not decimal_profile(amount, precision=38, scale=10)["fits"]:
        raise BoundedError("NOTICE_SELECTED_A_EXACT_GROSS_DECIMAL_REQUIRED")
    proof = {
        "contract_id": CONTRACT_ID,
        "literal_header_sha256": digest(header[0]),
        "publisher_and_literal_header_agree": True,
        "code_alias_inferred": False,
        "A_summary_witness_sha256": [digest(m[0]) for m in gross],
        "literal_generic_yuan_gross_column": bool(generic),
        "complete_current_scheme_sha256": digest(blocks[0]),
        "gross_current_claim_sha256": [digest(m[0]) for m in claims],
        "original_mainland_gross_unit_contract": MAINLAND_UNIT_CONTRACT,
        "B_payment_currency_conversion_performed": False,
        "foreign_B_terms_qualified": False,
        "selected_scope": "SOURCE_DECLARED_A_SHARE_CASH_ONLY_NOT_ALL_MARKETS",
        "original_foreign_observations_retained_in_local_PDF": True,
        **date_proof,
    }
    proof["identity"] = digest(proof)
    return {
        "contract_id": CONTRACT_ID,
        "stock_code": code,
        "ex_date": ex_date,
        "currency": "CNY",
        "cash_per_share": amount,
        "cash_atom_sha256": digest(["CNY", amount]),
        "original_text_sha256": digest(text),
        "original_publisher_title_sha256": digest(document["publisher_title"]),
        "selected_A_scope_evidence": proof,
        "historical_PIT_admission": False,
        "aggregate_computed": False,
    }


def select_a_b_cash_candidates(
    targets: list[dict[str, Any]], documents: Iterable[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    docs = list(documents)
    selected, previous = select_h_excluded_cash_candidates(targets, docs)
    diagnostics = []
    for target in targets:
        key = target["action_id"]
        if key in selected:
            continue
        primary = [d for d in docs if key in d["target_action_ids"] and _primary_role(d)]
        candidates = []
        for doc in primary:
            try:
                atom = parse_selected_a_cash(doc, target)
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
        "selected_A_B_scope_dispositions": diagnostics,
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

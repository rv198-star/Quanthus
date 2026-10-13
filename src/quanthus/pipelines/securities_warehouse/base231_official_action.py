"""Bounded official implementation terms, separated from adjustment bases.

This successor qualifies the stated eligible-holder distribution. A treasury-
share adjustment denominator is a distinct business concept and is never a
conflicting value to overwrite silently. Original observations remain intact.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, localcontext
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_outputs import instant
from .base22_precision import decimal_profile
from .disclosure_event import materialize_corporate_action_rows

CONTRACT_ID = "p10-base231-official-implemented-distribution-basis-v1"


def _one(pattern: str, text: str) -> str:
    found = set(re.findall(pattern, text))
    if len(found) != 1:
        raise BoundedError("OFFICIAL_ACTION_EXPLICIT_TERM_MISSING_OR_CONFLICTING")
    return str(next(iter(found)))


def _per_ten(token: str) -> Decimal:
    # Moving the decimal exponent preserves all digits regardless of context.
    parts = Decimal(token).as_tuple()
    if not isinstance(parts.exponent, int):
        raise BoundedError("OFFICIAL_ACTION_EXACT_NATIVE_TERM_UNREPRESENTABLE")
    return Decimal((parts.sign, parts.digits, parts.exponent - 1))


def parse_distribution(
    body: str, *, code: str, venue: str, expected_ex_date: date
) -> tuple[dict[str, Any], dict[str, Any]]:
    text = re.sub(r"\s+", "", body)
    if (
        not re.fullmatch(r"\d{6}", code)
        or code not in text
        or "实施公告" not in text
        or expected_ex_date.year != 2026
        or expected_ex_date > date(2026, 9, 30)
    ):
        raise BoundedError("OFFICIAL_ACTION_DOCUMENT_SCOPE_UNQUALIFIED")
    if venue == "SZSE":
        match = re.search(
            r"二、(?:本次实施的)?(?:权益分派方案|利润分配及资本公积金转增股本方案)"
            r"(.+?)三、股权登记日与除权除息日",
            text,
        )
        if match is None:
            raise BoundedError("OFFICIAL_ACTION_IMPLEMENTATION_SECTION_MISSING")
        section = match.group(1)
        ex_tokens = set(re.findall(r"除权除息日[为：:]{1,2}(\d{4})年(\d{1,2})月(\d{1,2})日", text))
        record_tokens = set(
            re.findall(r"股权登记日[为：:]{1,2}(\d{4})年(\d{1,2})月(\d{1,2})日", text)
        )
        if len(ex_tokens) != 1 or len(record_tokens) != 1:
            raise BoundedError("OFFICIAL_ACTION_DATE_LABEL_NOT_UNIQUE")
        ex_date = date(*map(int, next(iter(ex_tokens))))
        record_date = date(*map(int, next(iter(record_tokens))))
        cash = (
            Decimal(0)
            if "不派发现金红利" in section
            else _per_ten(_one(r"每10股派([0-9]+(?:\.[0-9]+)?)元人民币现金（含税", section))
        )
        transfer = _per_ten(_one(r"每10股转增([0-9]+(?:\.[0-9]+)?)股", section))
        bonus = Decimal(0) if "不送红股" in section else None
    elif venue == "SSE":
        if "上海证券交易所" not in text:
            raise BoundedError("OFFICIAL_ACTION_VENUE_NOT_PROVEN")
        match = re.search(r"每股分配比例(.+?)(?:相关日期)", text)
        dates = re.search(r"股权登记日(.{1,180})一、", text)
        if match is None or dates is None or "除权（息）日" not in dates.group(1):
            raise BoundedError("OFFICIAL_ACTION_IMPLEMENTATION_SECTION_MISSING")
        section = match.group(1)
        ordered_dates = re.findall(r"(\d{4})/(\d{1,2})/(\d{1,2})", dates.group(1))
        if len(ordered_dates) not in {3, 4}:
            raise BoundedError("OFFICIAL_ACTION_DATE_LABEL_NOT_UNIQUE")
        record_date, ex_date = [date(*map(int, d)) for d in ordered_dates[:2]]
        cash = Decimal(_one(r"每股现金红利([0-9]+(?:\.[0-9]+)?)元", section))
        bonus = (
            Decimal(_one(r"每股派送红股([0-9]+(?:\.[0-9]+)?)股", section))
            if "每股派送红股" in section
            else None
        )
        transfer = None
    else:
        raise BoundedError("OFFICIAL_ACTION_VENUE_UNSUPPORTED")
    if ex_date != expected_ex_date or record_date >= ex_date:
        raise BoundedError("OFFICIAL_ACTION_EX_DATE_SCOPE_CONFLICT")
    values = {"cash_per_share": cash, "bonus_shares": bonus, "transfer_shares": transfer}
    if not any(v is not None and v > 0 for v in values.values()) or any(
        v is not None
        and (not v.is_finite() or v < 0 or not decimal_profile(v, precision=38, scale=10)["fits"])
        for v in values.values()
    ):
        raise BoundedError("OFFICIAL_ACTION_EXACT_NATIVE_TERM_UNREPRESENTABLE")
    positive = [k for k, v in values.items() if v is not None and v > 0]
    record = dict(
        stock_code=code,
        ex_date=ex_date,
        record_date=record_date,
        announce_date=None,
        payment_date=None,
        progress="实施",
        action_type="mixed"
        if len(positive) > 1
        else {
            "cash_per_share": "cash_div",
            "bonus_shares": "bonus",
            "transfer_shares": "transfer",
        }[positive[0]],
        **values,
    )
    proof = dict(
        contract_id=CONTRACT_ID,
        code=code,
        venue=venue,
        ex_date=ex_date,
        source_body_sha256=digest(body),
        implementation_section_sha256=digest(section),
        parsed_terms_sha256=digest(values),
        term_basis="ELIGIBLE_HOLDER_PRETAX_DISTRIBUTION",
        all_shares_adjustment_basis_separately_required=True,
        derived_price_factor_admission="NOT_GRANTED",
        classes={
            k: "NULL" if v is None else "ZERO" if v == 0 else "EXACT" for k, v in values.items()
        },
        publication_instant=None,
    )
    return record, proof


def materialize_distribution(
    record: dict[str, Any],
    *,
    announcement_id: str,
    observed_at: datetime,
    pdf_received_at: datetime,
    pdf_sha256: str,
    company_id: str,
    security_id: str,
    vocabulary: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    clock = instant(observed_at)
    if (
        instant(pdf_received_at) > clock
        or not announcement_id
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", pdf_sha256)
    ):
        raise BoundedError("OFFICIAL_ACTION_REAL_SOURCE_CLOCK_OR_HASH_INVALID")
    with localcontext() as ctx:
        ctx.prec = 64
        return materialize_corporate_action_rows(
            {**record, "source_row_key": announcement_id, "built_at": clock},
            source_id="cninfo.official_implemented_distribution",
            source_sha256=pdf_sha256,
            source_revision=CONTRACT_ID,
            company_id=company_id,
            security_id=security_id,
            vocabulary={**vocabulary, "share_quantity_unit": "per_share"},
        )

"""Bounded official implementation terms, separated from adjustment bases.

This successor qualifies the stated eligible-holder distribution. A treasury-
share adjustment denominator is a distinct business concept and is never a
conflicting value to overwrite silently. Original observations remain intact.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, localcontext
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_outputs import instant
from .base22_precision import decimal_profile
from .disclosure_event import materialize_corporate_action_rows

CONTRACT_ID = "p10-base231-official-implemented-distribution-basis-v1"

# Whitespace removal joins adjacent PDF table dates. A single-digit day must
# stop before the next YYYY/ token rather than consume its leading digit.
_SLASH_DATE = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})(?=\d{4}/|[^0-9]|$)")


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


def _szse_summary_cash(section: str, text: str) -> tuple[Decimal, str]:
    """Bind currency only through the same document's current event summary.

    The implementation supplies the value and precision. A matching, explicit
    pretax RMB per-ten statement in 特别提示 supplies currency, never a proposal,
    total amount, tax deduction, adjustment formula or unrelated RMB mention.
    """
    implemented = _one(
        r"每10股派发现金(?:股利|红利)([0-9]+(?:\.[0-9]+)?)元[（(【]含税", section
    )
    prefix = text.split("一、", 1)[0]
    if "特别提示" not in prefix:
        raise BoundedError("OFFICIAL_ACTION_EXPLICIT_TERM_MISSING_OR_CONFLICTING")
    summary = prefix.split("特别提示", 1)[1]
    if "本次权益分派" not in summary or re.search(r"拟|预案|上次|上年度", summary):
        raise BoundedError("OFFICIAL_ACTION_EXPLICIT_TERM_MISSING_OR_CONFLICTING")
    explicit = re.findall(
        r"每10股派(?:发现金股利|发现金红利)?(?:人民币([0-9]+(?:\.[0-9]+)?)元"
        r"|([0-9]+(?:\.[0-9]+)?)元人民币)[（(【]含税",
        summary,
    )
    if not explicit or {Decimal(a or b) for a, b in explicit} != {Decimal(implemented)}:
        raise BoundedError("OFFICIAL_ACTION_EXPLICIT_TERM_MISSING_OR_CONFLICTING")
    return _per_ten(implemented), digest(summary)


def szse_distribution_dates(body: str) -> tuple[date, date]:
    """Read explicit labels; multiple A/B or revised dates remain ambiguous."""
    text = re.sub(r"\s+", "", body)
    dates = []
    for label in [r"股权登记日", r"(?:除权除息日|除权日[（(]除息日[）)])"]:
        tokens = {
            date(*map(int, parts))
            for parts in re.findall(
                label + r"[为：:]{1,2}(\d{4})年(\d{1,2})月(\d{1,2})日", text
            )
        }
        if len(tokens) != 1:
            raise BoundedError("OFFICIAL_ACTION_DATE_LABEL_NOT_UNIQUE")
        dates.append(next(iter(tokens)))
    return dates[0], dates[1]


def sse_distribution_dates(body: str) -> tuple[date, date]:
    """Only the explicitly ordered, single A-share current implementation table."""
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", body))
    match = re.search(r"三、相关日期(.+?)四、", text)
    if match is None:
        raise BoundedError("OFFICIAL_ACTION_IMPLEMENTATION_SECTION_MISSING")
    section = match[1]
    # A two-line PDF header may place the right-hand labels before the left
    # labels in the text stream. Admit only this exact wrapped header family;
    # the sole A-share row still supplies all three ordered dates.
    header = (
        r"(?:股份类别股权登记日.*?除权\(息\)日现金红利发放日"
        r"|除权\(息\)现金红利发股份类别股权登记日最后交易日日放日)A股"
    )
    if (
        not (
            re.search(header, section)
            or re.match(r"股权登记日除权\(息\)日现金红利发放日(?=\d{4}/)", section)
        )
        or "B股" in section
        or "H股" in section
    ):
        raise BoundedError("OFFICIAL_ACTION_DATE_LABEL_NOT_UNIQUE")
    tokens = _SLASH_DATE.findall(section)
    if len(tokens) != 3:
        raise BoundedError("OFFICIAL_ACTION_DATE_LABEL_NOT_UNIQUE")
    return date(*map(int, tokens[0])), date(*map(int, tokens[1]))


def _sse_transfer_terms(section: str) -> tuple[Decimal, date, date]:
    """Explicit transfer-only implementation and its three-column date table."""
    normalized = unicodedata.normalize("NFKC", section)
    parts = normalized.split("相关日期")
    if len(parts) != 2:
        raise BoundedError("OFFICIAL_ACTION_DATE_LABEL_NOT_UNIQUE")
    terms, related = parts
    if re.search(r"现金|送红股", terms):
        raise BoundedError("OFFICIAL_ACTION_EXPLICIT_TERM_MISSING_OR_CONFLICTING")
    ratio = Decimal(
        _one(r"以资本公积金向全体股东每股转增([0-9]+(?:\.[0-9]+)?)股", terms)
    )
    dates = list(_SLASH_DATE.finditer(related))
    headers = {
        "股权登记日除权(息)日新增无限售条件流通股份上市日",
        "新增无限售条件流通股份股权登记日除权(息)日上市日",
    }
    if len(dates) != 3 or related[: dates[0].start()] not in headers:
        raise BoundedError("OFFICIAL_ACTION_DATE_LABEL_NOT_UNIQUE")
    return ratio, date(*map(int, dates[0].groups())), date(*map(int, dates[1].groups()))


def parse_distribution(
    body: str, *, code: str, venue: str, expected_ex_date: date
) -> tuple[dict[str, Any], dict[str, Any]]:
    # A numeric footer may split a word across PDF pages. Only remove a
    # standalone, indented page number inside the explicit RMB cash phrase.
    # Ordinary numbers, table values and unmarked line breaks remain untouched.
    layout_body = re.sub(
        r"(?<=元人民币)[ \t]*\n(?:[ \t]*\n)*[ \t]{8,}[0-9]{1,4}[ \t]*\n\f"
        r"[ \t]*(?=现金[（(【]含税)",
        "\n",
        body,
    )
    text = re.sub(r"\s+", "", layout_body)
    if (
        not re.fullmatch(r"\d{6}", code)
        or code not in text
        or "实施公告" not in text
        or expected_ex_date.year != 2026
        or expected_ex_date > date(2026, 9, 30)
    ):
        raise BoundedError("OFFICIAL_ACTION_DOCUMENT_SCOPE_UNQUALIFIED")
    cash: Decimal | None
    currency_summary_sha256 = None
    if venue == "SZSE":
        match = re.search(
            r"二、(?:本次实施的|本次)?(?:A股)?(?:权益分派(?:实施)?方案|权益分配方案|"
            r"利润分配及资本公积金转增股本方案|"
            r"(?:2026年(?:中期|半年度|年度))?利润分配方案)"
            r"(.+?)三、(?:A股)?(?:股权登记日[与及]除权除息日|分红派息日期?|权益分派日期)",
            text,
        )
        if match is None:
            raise BoundedError("OFFICIAL_ACTION_IMPLEMENTATION_SECTION_MISSING")
        section = match.group(1)
        record_date, ex_date = szse_distribution_dates(body)
        explicit_cash = re.findall(
            r"每10股派(?:发现金红利|现金红利|发现金股利|现金股利|发)?"
            r"(?=人民币|[0-9]+(?:\.[0-9]+)?元人民币现金)"
            r"(?:人民币)?([0-9]+(?:\.[0-9]+)?)元(?:人民币现金|现金)?[（(【]含税",
            section,
        )
        cash = (
            Decimal(0)
            if "不派发现金红利" in section
            else _per_ten(
                _one(
                    r"每10股派(?:发现金红利|现金红利|发现金股利|现金股利|发)?"
                    r"(?=人民币|[0-9]+(?:\.[0-9]+)?元人民币现金)"
                    r"(?:人民币)?([0-9]+(?:\.[0-9]+)?)元(?:人民币现金|现金)?[（(【]含税",
                    section,
                )
            )
            if explicit_cash
            else None
        )
        if cash is None:
            cash, currency_summary_sha256 = _szse_summary_cash(section, text)
        transfers = re.findall(r"每10股转增([0-9]+(?:\.[0-9]+)?)股", section)
        no_transfer = bool(re.search(r"不以(?:资本)?公积金转增股本", section))
        if transfers and no_transfer:
            raise BoundedError("OFFICIAL_ACTION_EXPLICIT_TERM_MISSING_OR_CONFLICTING")
        transfer = (
            _per_ten(_one(r"每10股转增([0-9]+(?:\.[0-9]+)?)股", section))
            if transfers
            else Decimal(0)
            if no_transfer
            else None
        )
        bonus = Decimal(0) if "不送红股" in section else None
    elif venue == "SSE":
        if "上海证券交易所" not in text:
            raise BoundedError("OFFICIAL_ACTION_VENUE_NOT_PROVEN")
        match = re.search(r"每股分配比例(.+?)(?:相关日期)", text)
        dates = re.search(r"股权登记日(.{1,180})一、", text)
        transfer_match = re.search(r"二、转增股本方案(.+?)三、转增股本实施办法", text)
        if transfer_match is not None:
            section = transfer_match[1]
            transfer, record_date, ex_date = _sse_transfer_terms(section)
            cash = None
        elif match is not None and dates is not None and "除权（息）日" in dates[1]:
            section = match.group(1)
            ordered_dates = _SLASH_DATE.findall(dates.group(1))
            if len(ordered_dates) not in {3, 4}:
                raise BoundedError("OFFICIAL_ACTION_DATE_LABEL_NOT_UNIQUE")
            record_date, ex_date = [date(*map(int, d)) for d in ordered_dates[:2]]
            cash = Decimal(_one(r"每股现金红利(?:人民币)?([0-9]+(?:\.[0-9]+)?)元", section))
        else:
            match = re.search(r"二、分配方案(.+?)三、相关日期", text)
            if match is None:
                raise BoundedError("OFFICIAL_ACTION_IMPLEMENTATION_SECTION_MISSING")
            section = match[1]
            record_date, ex_date = sse_distribution_dates(body)
            cash = Decimal(
                _one(r"每股派发现金红利(?:人民币)?([0-9]+(?:\.[0-9]+)?)元[（(]含税[）)]", section)
            )
        bonus = (
            Decimal(_one(r"每股派送红股([0-9]+(?:\.[0-9]+)?)股", section))
            if "每股派送红股" in section
            else None
        )
        if transfer_match is None:
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
    if layout_body != body:
        proof["layout_normalization_contract"] = "pdf-explicit-rmb-cash-page-footer-v1"
        proof["layout_body_sha256"] = digest(layout_body)
    if currency_summary_sha256 is not None:
        proof["currency_binding_contract"] = "same-current-implementation-summary-v1"
        proof["current_currency_summary_sha256"] = currency_summary_sha256
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

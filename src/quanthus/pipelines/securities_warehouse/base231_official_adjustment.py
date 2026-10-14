"""Explicit official all-issued-share adjustment coefficients, not payouts.

This adds a separately qualified source observation. It never silently turns an
eligible-holder distribution into an adjustment coefficient or admits a factor.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_outputs import instant
from .base22_precision import decimal_profile
from .base231_official_action import parse_distribution
from .disclosure_event import materialize_corporate_action_rows

CONTRACT_ID = "p10-base231-official-all-issued-share-adjustment-v1"
NUMBER = r"([0-9]+(?:\.[0-9]+)?)"


def parse_adjustment(
    body: str, *, code: str, venue: str, expected_ex_date: date
) -> tuple[dict[str, Any], dict[str, Any]]:
    payout, payout_proof = parse_distribution(
        body,
        code=code,
        venue=venue,
        expected_ex_date=expected_ex_date,
    )
    text = re.sub(r"\s+", "", body).replace("－", "-").replace("−", "-")
    if len(text) > 100000:
        raise BoundedError("OFFICIAL_ADJUSTMENT_BODY_BOUND")
    basis = "ALL_ISSUED_PRE_EVENT_SHARES"
    if venue == "SZSE":
        if not re.search(r"(?:公司|按)总股本|除权前总股本|公司现有总股本", text):
            raise BoundedError("OFFICIAL_ADJUSTMENT_DENOMINATOR_UNPROVEN")
        # Only the final literal reference-price expression qualifies. It must
        # follow its explicit label, not a proposal or a convertible-bond price.
        starts = list(
            re.finditer(r"(?:本次权益分派实施后的)?(?:每股)?除权除息参考(?:价格|价)", text)
        )
        pairs: set[tuple[str, str]] = set()
        atoms: set[str] = set()
        for m in starts:
            section = text[m.start() : m.start() + 550]
            if re.match(r"除权除息参考(?:价格|价)的计算见", section):
                continue
            literal = re.findall(
                r"[（(](?:除权除息日前一交易日|除权除息日前|权益分派股权登记日)"
                r"收盘价-" + NUMBER + r"元(?:/股)?[）)](?:/|÷)[（(]1\+" + NUMBER + r"(?:股)?[）)]",
                section,
            )
            zero_cash = re.findall(
                r"股权登记日股票收盘价÷[（(]1\+" + NUMBER + r"[）)]",
                section,
            )
            if literal:
                pairs.update(literal)
                atoms.add(digest(section))
            if zero_cash:
                if payout["cash_per_share"] != 0:
                    raise BoundedError("OFFICIAL_ADJUSTMENT_ZERO_CASH_CONFLICT")
                pairs.update(("0", v) for v in zero_cash)
                atoms.add(digest(section))
        if len(pairs) != 1:
            raise BoundedError("OFFICIAL_ADJUSTMENT_UNIQUE_LITERAL_FORMULA_REQUIRED")
        cash_token, shares_token = next(iter(pairs))
        cash, transfer = Decimal(cash_token), Decimal(shares_token)
        bonus = payout["bonus_shares"]
        if bonus is not None and bonus != 0:
            raise BoundedError("OFFICIAL_ADJUSTMENT_TRANSFER_VS_BONUS_UNPROVEN")
        method = "OFFICIAL_FINAL_REFERENCE_FORMULA"
    else:
        if not re.search(r"公司总股本[0-9,]+股为基数", text):
            raise BoundedError("OFFICIAL_ADJUSTMENT_TOTAL_ISSUED_BASE_UNPROVEN")
        if re.search(r"剔除|扣除|不参与本次|差异化分红送转[：:]是", text):
            raise BoundedError("OFFICIAL_ADJUSTMENT_DIFFERENTIATED_BASE_UNQUALIFIED")
        if payout["action_type"] != "cash_div" and "是否涉及差异化分红送转：否" not in text:
            raise BoundedError("OFFICIAL_ADJUSTMENT_NONDIF_SCOPE_UNPROVEN")
        cash, bonus, transfer = (
            payout[k] for k in ["cash_per_share", "bonus_shares", "transfer_shares"]
        )
        atoms = {payout_proof["implementation_section_sha256"]}
        method = "EXPLICIT_ALL_ISSUED_IMPLEMENTATION_BASE"
    values = dict(cash_per_share=cash, bonus_shares=bonus, transfer_shares=transfer)
    if any(
        v is not None
        and (v < 0 or not v.is_finite() or not decimal_profile(v, precision=38, scale=10)["fits"])
        for v in values.values()
    ):
        raise BoundedError("OFFICIAL_ADJUSTMENT_EXACT_NATIVE_RANGE")
    record = {**payout, **values}
    proof = dict(
        contract_id=CONTRACT_ID,
        code=code,
        venue=venue,
        ex_date=expected_ex_date,
        source_body_sha256=digest(body),
        basis=basis,
        method=method,
        formula_section_sha256=sorted(atoms),
        coefficients_sha256=digest(values),
        eligible_distribution_sha256=payout_proof["parsed_terms_sha256"],
        coefficients_equal_eligible_distribution=all(values[k] == payout[k] for k in values),
        unknown_components=[k for k, v in values.items() if v is None],
        derived_price_factor_admission="NOT_GRANTED",
        provider_PIT_qualified=False,
        prior_values_overwritten=False,
    )
    return record, proof


def materialize_adjustment(
    record: dict[str, Any],
    *,
    announcement_id: str,
    observed_at: datetime,
    received_at: datetime,
    pdf_sha256: str,
    company_id: str,
    security_id: str,
    vocabulary: dict[str, Any],
    declared_publication_ms: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    clock = instant(observed_at)
    if (
        instant(received_at) > clock
        or not re.fullmatch(r"[0-9]+", announcement_id)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", pdf_sha256)
    ):
        raise BoundedError("OFFICIAL_ADJUSTMENT_SOURCE_CLOCK_OR_ID_INVALID")
    output = materialize_corporate_action_rows(
        {**record, "source_row_key": announcement_id + ":ALL_ISSUED_SHARES", "built_at": clock},
        source_id="cninfo.official_all_issued_share_adjustment",
        source_sha256=pdf_sha256,
        source_revision=CONTRACT_ID,
        company_id=company_id,
        security_id=security_id,
        vocabulary={**vocabulary, "share_quantity_unit": "per_share"},
    )

    if declared_publication_ms is not None:
        if type(declared_publication_ms) is not int:
            raise BoundedError("OFFICIAL_ADJUSTMENT_DECLARED_PUBLICATION_TYPE")
        published = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(
            milliseconds=declared_publication_ms
        )
        if published > instant(received_at):
            raise BoundedError("OFFICIAL_ADJUSTMENT_DECLARED_PUBLICATION_AFTER_RECEIPT")
        for rows in output.values():
            for row in rows:
                row["announced_at"] = published
    return output

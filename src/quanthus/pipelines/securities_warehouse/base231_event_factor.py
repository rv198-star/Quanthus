"""Current-knowledge, single-event reference ratios from explicit official inputs.

These observations do not replace cumulative provider QFQ/HFQ, grant historical
PIT or change a consumer's method selection. Original observations stay intact.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_outputs import instant
from .listed_company import _stable_id

CONTRACT_ID = "p10-base231-official-event-reference-price-ratio-v1"
METHOD = "OFFICIAL_EVENT_REFERENCE_PRICE_RATIO_V1"
QUANTUM = Decimal("0.000000000000000001")


def reference_inputs(
    record: dict[str, Any], proof: dict[str, Any], *, official_body: str | None = None,
) -> dict[str, Any]:
    fields = ("cash_per_share", "bonus_shares", "transfer_shares")
    values = {k: record[k] for k in fields}
    if (proof.get("basis") != "ALL_ISSUED_PRE_EVENT_SHARES"
            or proof.get("coefficients_sha256") != digest(values)
            or proof.get("code") != record.get("stock_code")
            or proof.get("ex_date") != record.get("ex_date")
            or not proof.get("formula_section_sha256")):
        raise BoundedError("EVENT_REFERENCE_OFFICIAL_INPUT_CHANGED")
    if proof["method"] == "OFFICIAL_FINAL_REFERENCE_FORMULA":
        # The literal final denominator is a complete combined coefficient.
        # This does not assign zero to a missing bonus/transfer component.
        combined = record["transfer_shares"]
    elif proof["method"] == "EXPLICIT_ALL_ISSUED_IMPLEMENTATION_BASE":
        if any(v is None for v in values.values()):
            if official_body is None or digest(official_body) != proof["source_body_sha256"]:
                raise BoundedError("EVENT_REFERENCE_COMPONENTS_INCOMPLETE")
            text = re.sub(r"\s+", "", official_body)
            totals = set(re.findall(
                r"方案实施前的公司总股本([0-9,]+)股.{0,800}?"
                r"本次分配后总股本为([0-9,]+)股", text))
            if len(totals) != 1:
                raise BoundedError("EVENT_REFERENCE_COMPONENTS_INCOMPLETE")
            before, after = (Decimal(v.replace(",", "")) for v in next(iter(totals)))
            if before <= 0 or after < before:
                raise BoundedError("EVENT_REFERENCE_ISSUED_TOTALS_INVALID")
            with localcontext() as context:
                context.prec = 96
                combined = (after - before) / before
                if combined * before != after - before:
                    raise BoundedError("EVENT_REFERENCE_ISSUED_TOTAL_RATIO_INEXACT")
                known = sum((v for k, v in values.items()
                             if k != "cash_per_share" and v is not None), Decimal(0))
                if combined < known:
                    raise BoundedError("EVENT_REFERENCE_ISSUED_TOTALS_CONFLICT")
        else:
            with localcontext() as context:
                context.prec = 96
                combined = record["bonus_shares"] + record["transfer_shares"]
    else:
        raise BoundedError("EVENT_REFERENCE_METHOD_UNQUALIFIED")
    cash = record["cash_per_share"]
    if any(not isinstance(v, Decimal) or not v.is_finite() or v < 0
           for v in (cash, combined)):
        raise BoundedError("EVENT_REFERENCE_COEFFICIENT_INVALID")
    return dict(contract_id=CONTRACT_ID, code=record["stock_code"],
                ex_date=record["ex_date"], cash_per_share=cash,
                combined_share_increment=combined, official_proof_sha256=digest(proof),
                original_record_sha256=digest(record),
                unknown_components=[k for k, v in values.items() if v is None],
                component_nulls_preserved=True, basis=proof["basis"])


def factor_observation(
    inputs: dict[str, Any], *, prior_close: Decimal, prior_business_date: date,
    expected_prior_session: date, price_basis: str, currency: str,
    security_id: str, action_id: str, observed_at: datetime,
    source_known_at: datetime, price_known_at: datetime, source_sha256: str,
    price_row_sha256: str, price_member_sha256: str,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    clock = instant(observed_at)
    ex_date = inputs.get("ex_date")
    if (inputs.get("contract_id") != CONTRACT_ID or type(ex_date) is not date
            or ex_date > date(2026, 9, 30) or type(prior_business_date) is not date
            or type(expected_prior_session) is not date
            or prior_business_date != expected_prior_session or prior_business_date >= ex_date
            or price_basis != "UNADJUSTED" or currency != "CNY"
            or not security_id or not action_id
            or instant(source_known_at) > clock or instant(price_known_at) > clock
            or not isinstance(prior_close, Decimal) or not prior_close.is_finite()
            or prior_close <= 0 or any(not re.fullmatch(r"sha256:[0-9a-f]{64}", s)
                                      for s in (source_sha256, price_row_sha256,
                                                price_member_sha256))):
        raise BoundedError("EVENT_REFERENCE_PRICE_DATE_CLOCK_OR_ID_INVALID")
    cash, shares = inputs["cash_per_share"], inputs["combined_share_increment"]
    if any(not isinstance(v, Decimal) or not v.is_finite() or v < 0 for v in (cash, shares)):
        raise BoundedError("EVENT_REFERENCE_COEFFICIENT_INVALID")
    with localcontext() as context:
        context.prec = 96
        if prior_close <= cash:
            raise BoundedError("EVENT_REFERENCE_NONPOSITIVE_PRICE")
        value = ((prior_close - cash) / ((1 + shares) * prior_close)).quantize(
            QUANTUM, rounding=ROUND_HALF_EVEN)
        if not 0 < value <= 1:
            raise BoundedError("EVENT_REFERENCE_RATIO_RANGE")
    closure = dict(contract_id=CONTRACT_ID, inputs_sha256=digest(inputs),
                   prior_close_sha256=digest(prior_close), prior_business_date=prior_business_date,
                   price_row_sha256=price_row_sha256, price_member_sha256=price_member_sha256,
                   official_member_sha256=source_sha256, security_id=security_id,
                   corporate_action_id=action_id, source_known_at=instant(source_known_at),
                   price_known_at=instant(price_known_at), observed_at=clock,
                   rounding="ROUND_HALF_EVEN", quantum=str(QUANTUM))
    evidence = digest(closure)
    factor_id = _stable_id("adjustment_factor", security_id, action_id, CONTRACT_ID)
    effective = datetime.combine(ex_date, datetime.min.time(), ZoneInfo("Asia/Shanghai"))
    common = dict(record_source="p10.official_event_reference", source_record_id=action_id,
                  source_revision=CONTRACT_ID, source_artifact_sha256=source_sha256,
                  knowledge_from=clock, knowledge_to=None, effective_from=instant(effective),
                  effective_to=None, adjustment_factor_id=factor_id)
    factor = dict(**common, security_id=security_id, adjustment_method=METHOD,
                  adjustment_direction="EVENT_PRE_TO_POST_REFERENCE_MULTIPLIER",
                  factor_value=value, anchor_date=ex_date, evidence_set_digest=evidence)
    rows = [dict(**common, input_type=kind, corporate_action_id=parent,
                 input_evidence_digest=sha, input_revision=CONTRACT_ID)
            for kind, parent, sha in [("OFFICIAL_ALL_ISSUED_EVENT", action_id, digest(inputs)),
                                      ("UNADJUSTED_PRIOR_SESSION_CLOSE", None, price_row_sha256)]]
    proof = dict(closure, output_sha256=digest(factor), old_values_overwritten=False,
                 historical_PIT_admission="NOT_GRANTED", consumer_admission="NOT_GRANTED",
                 cumulative_qfq_hfq_admission="NOT_GRANTED")
    return {"dwd_adjustment_factor": [factor], "dwd_adjustment_factor_input": rows}, proof

"""Retrospective observations from retained raw rows, never historical PIT proof."""

from __future__ import annotations

import math
from datetime import date
from decimal import Decimal, localcontext
from typing import Any

from quanthus.corporate_action_normalization import (
    CorporateActionNormalizationError,
    _expected_factor_ratio,
    _value,
)

from .base22 import THROUGH, digest
from .base22_bounded import BoundedError
from .base22_financial import _clock
from .disclosure_event import materialize_corporate_action_rows

CONTRACT_ID = "p10-base22-retained-factor-retrospective-v1"
SOURCE_FAMILY = "base22_retained_factor/retrospective"


def _number(value: Any) -> Decimal:
    # Original Arrow doubles remain in the local raw evidence. The legacy numeric
    # contract uses their shortest decimal serialization and its frozen 8dp quantum.
    if isinstance(value, bool) or value is None or not isinstance(value, (float, int, Decimal)):
        raise BoundedError("FACTOR_RAW_NUMBER_MISSING_OR_INVALID")
    if isinstance(value, float) and not math.isfinite(value):
        raise BoundedError("FACTOR_RAW_NUMBER_MISSING_OR_INVALID")
    number = Decimal(str(value))
    if not number.is_finite():
        raise BoundedError("FACTOR_RAW_NUMBER_MISSING_OR_INVALID")
    return number


def normalize_retained_factor_record(
    record: dict[str, Any],
    *,
    prior_close: Any,
    factor_before: Any,
    factor_after: Any,
    baseline: str,
    policy: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Use frozen numeric rules while leaving every historical clock unqualified.

    This does not instantiate the legacy evidence object, supply known_at, round
    source prices, replace legacy rows, or reinterpret null quantities as zero.
    """
    if (
        policy.get("policy_version") != "p1-corporate-action-normalization-20260730-v3"
        or policy.get("status") != "frozen"
        or policy.get("share_quantity_unit") != "per_ten_shares"
        or policy.get("cash_unit_candidates") != ["per_share", "per_ten_shares"]
        or policy.get("maximum_factor_ratio_error") != "0.005"
        or policy.get("minimum_candidate_error_separation") != "0.00001"
        or policy.get("cross_check_baselines") != ["prior_factor", "prior_qfq_ratio"]
    ):
        raise BoundedError("FACTOR_FROZEN_NUMERIC_POLICY_MISMATCH")
    if baseline not in policy["cross_check_baselines"]:
        raise BoundedError("FACTOR_BASELINE_UNSUPPORTED")
    effective = record.get("ex_date")
    notice = record.get("announce_date")
    if (
        type(effective) is not date
        or effective > THROUGH
        or (notice is not None and type(notice) is not date)
        or record.get("progress") != "实施"
        or not record.get("stock_code")
        or not record.get("source_row_key")
    ):
        raise BoundedError("FACTOR_RAW_ACTION_IDENTITY_OR_DATE_INVALID")
    cash, bonus, transfer = (
        _number(record.get(field))
        for field in ("cash_per_share", "bonus_shares", "transfer_shares")
    )
    prior, before, after = _number(prior_close), _number(factor_before), _number(factor_after)
    if min(cash, bonus, transfer) < 0 or min(prior, before, after) <= 0:
        raise BoundedError("FACTOR_RAW_NUMBER_NONPOSITIVE")
    with localcontext() as context:
        context.prec = 64
        shares = _value((bonus + transfer) / Decimal(10))
        observed = after / before
        candidates = []
        for unit, value in (
            [("zero", Decimal(0))]
            if cash == 0
            else [("per_share", _value(cash)), ("per_ten_shares", _value(cash / Decimal(10)))]
        ):
            try:
                expected = _expected_factor_ratio(
                    prior_close=prior, cash_per_share=value, share_ratio=shares
                )
            except CorporateActionNormalizationError:
                continue
            candidates.append((abs(observed - expected), unit, value))
        candidates.sort(key=lambda item: (item[0], item[1]))
        if not candidates or candidates[0][0] > Decimal(policy["maximum_factor_ratio_error"]):
            raise BoundedError("FACTOR_FROZEN_RATIO_MISMATCH")
        if len(candidates) > 1 and candidates[1][0] - candidates[0][0] < Decimal(
            policy["minimum_candidate_error_separation"]
        ):
            raise BoundedError("FACTOR_FROZEN_CASH_UNIT_AMBIGUOUS")
        normalized = {
            **record,
            "cash_per_share": candidates[0][2],
            "bonus_shares": _value(bonus / Decimal(10)),
            "transfer_shares": _value(transfer / Decimal(10)),
        }
    present = [
        f for f in ("cash_per_share", "bonus_shares", "transfer_shares") if normalized[f] > 0
    ]
    if not present:
        raise BoundedError("FACTOR_EMPTY_ACTION")
    normalized["action_type"] = (
        "mixed"
        if len(present) > 1
        else {"cash_per_share": "cash_div", "bonus_shares": "bonus", "transfer_shares": "transfer"}[
            present[0]
        ]
    )
    return normalized, {
        "contract_id": CONTRACT_ID,
        "frozen_policy_sha256": digest(policy),
        "original_record_sha256": digest(record),
        "normalized_record_sha256": digest(normalized),
        "baseline": baseline,
        "cash_unit": candidates[0][1],
        "candidate_evaluation_sha256": digest(candidates),
        "quantity_quantum": "0.00000001",
        "original_raw_atoms_or_prices_changed": False,
        "provider_acquisition": None,
        "historical_publication_instant": None,
        "original_normalization_manifest_restored": False,
        "legacy_known_at_or_accepted_child_inferred": False,
    }


def materialize_retained_factor_record(
    record: dict[str, Any],
    *,
    observed_at: Any,
    source_sha256: str,
    revision: str,
    parent: Any,
    vocabulary: dict[str, Any],
    schemas: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    import pyarrow as pa  # type: ignore[import-untyped]

    clock = max(_clock(observed_at), parent.knowledge_from)
    with localcontext() as context:
        context.prec = 64
        out = materialize_corporate_action_rows(
            {**record, "built_at": clock},
            source_id=SOURCE_FAMILY,
            source_sha256=source_sha256,
            source_revision=revision,
            company_id=parent.company_id,
            security_id=parent.security_id,
            vocabulary={**vocabulary, "share_quantity_unit": "per_share"},
            parent_knowledge_from=parent.knowledge_from,
        )
        expected = {
            vocabulary["term_types"][field]: record[field]
            for field in ("cash_per_share", "bonus_shares", "transfer_shares")
            if record[field] != 0
        }
        actual = {
            r["term_type"]: r["amount_per_share"]
            if r["amount_per_share"] is not None
            else r["ratio_numerator"] / r["ratio_denominator"]
            for r in out["dwd_corporate_action_term"]
        }
    if actual != expected:
        raise BoundedError("FACTOR_TYPED_TERM_SEMANTICS_CHANGED")
    for table, rows in out.items():
        for row in rows:
            row["announced_at"] = None
            if pa.Table.from_pylist([row], schema=schemas[table]).to_pylist()[0] != row:
                raise BoundedError("FACTOR_TYPED_VALUE_OR_TIME_CHANGED")
    return out

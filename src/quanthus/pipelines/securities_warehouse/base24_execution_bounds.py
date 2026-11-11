"""Explicit new revisions for exact stale 5% native bounds, preserving original rows."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from .base22 import digest
from .base24_execution_evidence import EFFECTIVE, SSE_RULE_SHA, SZSE_RULE_SHA, _require
from .base24_p6_purpose import _state_row

CONTRACT = "p10-exact-stale-five-percent-bound-successor.v1"


def repair_stale_bounds(
    row: dict[str, Any],
    *,
    prior: Decimal,
    ordinal: int,
    complete: bool,
    policy: dict[str, Any],
    raw_sha: str,
    window_proof: dict[str, Any] | None,
    authority_sha: str,
    authority_known: datetime,
    repair_at: datetime,
) -> dict[str, Any]:
    """Only this diagnosed old formula, with all other qualification gates retained."""
    _require(
        row["board"] in {"SSE_main", "SZ_main"} and row["market_date"] >= EFFECTIVE,
        "BOUND_REPAIR_SCOPE",
    )
    _require(
        authority_sha == (SSE_RULE_SHA if row["board"] == "SSE_main" else SZSE_RULE_SHA)
        and authority_known <= repair_at,
        "BOUND_AUTHORITY",
    )
    args: dict[str, Any] = dict(
        prior=prior,
        ordinal=ordinal,
        complete=complete,
        policy=policy,
        raw_sha=raw_sha,
        window_proof=window_proof,
    )
    before = _state_row(row, **args)
    _require(before["reason_code"] == "NATIVE_PRICE_BOUND_DISAGREEMENT", "NOT_EXACT_BOUND_DEFECT")
    _require(prior is not None and prior.is_finite() and prior > 0, "REFERENCE_UNAVAILABLE")
    old = tuple(
        (prior * (1 + ratio)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        for ratio in (Decimal("0.05"), Decimal("-0.05"))
    )
    _require((row["upper_price"], row["lower_price"]) == old, "NOT_STALE_FIVE_PERCENT")
    # Probe the frozen calculation, then independently check the complete corrected row.
    probe = deepcopy(row)
    probe.update(upper_price=None, lower_price=None)
    expected = _state_row(probe, **args)
    _require(expected["execution_source_qualified"], "OTHER_QUALIFICATION_GATE")
    corrected = deepcopy(row)
    corrected.update(
        upper_price=expected["qualified_upper_price"],
        lower_price=expected["qualified_lower_price"],
        state_known=repair_at,
    )
    _require(
        corrected["upper_price"] is not None
        and corrected["lower_price"] is not None
        and (corrected["upper_price"], corrected["lower_price"]) != old,
        "NO_QUALIFIED_NEW_BOUND",
    )
    after = _state_row(corrected, **args)
    _require(after["execution_source_qualified"], "CORRECTED_ROW_REJECTED")
    for field in (
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "is_tradable",
        "can_buy",
        "can_sell",
        "listing_id",
        "market_date",
        "bar_known",
    ):
        _require(corrected[field] == row[field], "UNALLOWED_BOUND_CHANGE")
    result = dict(
        contract_id=CONTRACT,
        before_row=row,
        replacement_row=corrected,
        before_result=before,
        replacement_result=after,
        reference_close=prior,
        authority_sha256=authority_sha,
        authority_known_at=authority_known,
        repair_known_at=repair_at,
        policy_digest=digest(policy),
        allowed_changes=["upper_price", "lower_price", "state_known"],
        historical_pit_granted=False,
        consumer_admission_granted=False,
    )
    return {**result, "identity": digest(result)}

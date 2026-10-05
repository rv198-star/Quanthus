"""Exact negative tail observations; retained controls are never rewritten."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from .base22 import business_date, digest
from .base22_bounded import BoundedError
from .base22_outputs import instant, tail_observation
from .base22_quality import _exception

CONTRACT_ID = "p10-base22-tail-state-reconciliation-v1"
STATE_COLUMNS = (
    "listing_id",
    "calendar_session_id",
    "observation_start",
    "observation_end",
    "knowledge_from",
    "knowledge_to",
    "is_tradable",
    "can_buy",
    "can_sell",
    "reason_code",
)


def reconcile_tail_observation(
    raw: Mapping[str, Any],
    *,
    bar: Mapping[str, Any],
    listing: Mapping[str, Any],
    calendar: Mapping[str, Any],
    states: Sequence[Mapping[str, Any]],
    gaps: Sequence[Mapping[str, Any]],
    detected_at: datetime,
    source_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind genuine raw, typed observation and exact retained negative controls.

    The caller supplies all selected state/gap versions for the exact date, from
    frozen members. This function does not decide a supplier priority, repair
    tradability, resolve an old gap, or grant consumer admission. Numeric values
    participate only in sealed equality/hashes; the returned proof is safe.
    """
    if raw["stock_code"] != listing["listing_code"]:
        raise BoundedError("TAIL_RECONCILIATION_SOURCE_IDENTITY_MISMATCH")
    expected, raw_proof = tail_observation(
        raw,
        listing=listing,
        calendar=calendar,
        observed_at=bar["knowledge_from"],
        source_sha256=source_sha256,
        revision=bar["source_revision"],
    )
    if dict(bar) != expected:
        raise BoundedError("TAIL_RECONCILIATION_TYPED_RAW_MISMATCH")
    if not raw_proof["classification"].startswith("QUARANTINED"):
        raise BoundedError("TAIL_RECONCILIATION_NOT_QUARANTINED")
    detected = instant(detected_at)
    if detected < bar["knowledge_from"]:
        raise BoundedError("TAIL_RECONCILIATION_DETECTION_PRECEDES_OBSERVATION")
    if not states or not gaps:
        raise BoundedError("TAIL_RECONCILIATION_CONTROL_CLOSURE_MISSING")
    day = raw["trade_date"]
    state_proofs = []
    for state in states:
        if (
            state["listing_id"] != listing["listing_id"]
            or state["calendar_session_id"] != calendar["calendar_session_id"]
            or business_date(state["observation_start"]) != day
            or state["observation_end"] <= state["observation_start"]
            or instant(state["knowledge_from"]) > detected
        ):
            raise BoundedError("TAIL_RECONCILIATION_STATE_CLOSURE_MISMATCH")
        if any(type(state[field]) is not bool for field in ("is_tradable", "can_buy", "can_sell")):
            raise BoundedError("TAIL_RECONCILIATION_CONTROL_FLAG_INVALID")
        projection = {field: state[field] for field in STATE_COLUMNS}
        state_proofs.append({"control_projection": projection, "sha256": digest(state)})
    gap_proofs = []
    for gap in gaps:
        if (
            gap["listing_id"] != listing["listing_id"]
            or business_date(gap["affected_effective_at"]) != day
            or gap["interval_size"] != "1d"
            or instant(gap["knowledge_from"]) > detected
        ):
            raise BoundedError("TAIL_RECONCILIATION_GAP_CLOSURE_MISMATCH")
        gap_proofs.append({"gap_reason": gap["gap_reason"], "sha256": digest(gap)})
    disagreement = any(s["is_tradable"] or s["can_buy"] or s["can_sell"] for s in states)
    proof = {
        "contract_id": CONTRACT_ID,
        "stock_code": raw["stock_code"],
        "trade_date": day,
        "raw_row_sha256": digest(raw),
        "typed_bar_sha256": digest(bar),
        "listing_row_sha256": digest(listing),
        "calendar_row_sha256": digest(calendar),
        "calendar_is_open": calendar["is_open"],
        "raw_nontrading": raw["tradestatus"] != "1",
        "zero_volume": raw["volume"] == 0,
        "zero_turnover": raw["amount"] == 0,
        "retained_states": state_proofs,
        "retained_gaps": gap_proofs,
        "state_disagreement": disagreement,
        "provider_acquisition": None,
        "warehouse_observed_at": bar["knowledge_from"],
        "detected_at": detected,
        "state_mutations": 0,
        "gap_resolutions": 0,
        "historical_accepted_child": "NOT_ASSERTED",
        "consumer_admission": "NOT_GRANTED",
        "disposition": "PRESERVE_RAW_AND_OLD_CONTROLS; BLOCK_NEW_ENTRIES; NO_FORCED_SALE",
    }
    row = _exception(
        key={
            "listing_id": listing["listing_id"],
            "calendar_session_id": calendar["calendar_session_id"],
            "trade_date": day,
            "observed_bar_version": bar["knowledge_from"],
        },
        effective=day,
        detected_at=detected,
        rule="P10-472-RAW-STATE-DISAGREEMENT" if disagreement else "P10-472-NONTRADING-RAW",
        domain="tradability_state",
        affected_table="dwd_security_bar",
        source_sha256=source_sha256,
        evidence=proof,
    )
    return row, proof

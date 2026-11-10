from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pytest

from quanthus.pipelines.securities_warehouse.base22 import load_contract, schemas
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_outputs import tail_observation
from quanthus.pipelines.securities_warehouse.base22_sources import SourceAssessmentError
from quanthus.pipelines.securities_warehouse.base22_tail_governance import (
    reconcile_tail_observation,
)

CLOCK = datetime(2026, 10, 5, tzinfo=timezone.utc)
SHA = "sha256:" + "a" * 64


def case():
    parent = {"knowledge_from": CLOCK - timedelta(days=1), "knowledge_to": None}
    listing = {
        **parent,
        "listing_id": "l",
        "listing_code": "600810",
        "venue_id": "v",
        "listing_currency": "CNY",
        "effective_from": datetime(2000, 1, 1, tzinfo=timezone.utc),
        "effective_to": None,
    }
    calendar = {
        **parent,
        "calendar_session_id": "c",
        "trade_date": date(2019, 9, 23),
        "venue_id": "v",
        "is_open": True,
    }
    raw = {
        "stock_code": "600810",
        "trade_date": date(2019, 9, 23),
        "tradestatus": "0",
        **{k: Decimal("0") for k in ("open", "high", "low", "close", "volume", "amount")},
    }
    bar, _ = tail_observation(
        raw,
        listing=listing,
        calendar=calendar,
        observed_at=CLOCK,
        source_sha256=SHA,
        revision="new-raw",
    )
    state = {
        **parent,
        "listing_id": "l",
        "calendar_session_id": "c",
        "observation_start": bar["observation_start"],
        "observation_end": bar["observation_end"],
        "is_tradable": True,
        "can_buy": True,
        "can_sell": True,
        "reason_code": "TRADABLE_OBSERVATION_MISSING",
    }
    gap = {
        **parent,
        "listing_id": "l",
        "affected_effective_at": bar["observation_start"],
        "interval_size": "1d",
        "gap_reason": "SOURCE_OBSERVATION_MISSING",
    }
    return dict(
        raw=raw,
        bar=bar,
        listing=listing,
        calendar=calendar,
        states=[state],
        gaps=[gap],
        detected_at=CLOCK,
        source_sha256=SHA,
    )


def test_exact_negative_governance_preserves_raw_old_controls_and_serialization():
    inputs = case()
    before = deepcopy(inputs)
    row, proof = reconcile_tail_observation(**inputs)
    assert inputs == before
    assert proof["state_disagreement"] is True
    assert proof["state_mutations"] == proof["gap_resolutions"] == 0
    assert proof["provider_acquisition"] is None
    assert proof["consumer_admission"] == "NOT_GRANTED"
    assert row["knowledge_from"] == CLOCK and row["resolution_state"] == "UNRESOLVED"
    assert "NO_FORCED_SALE" in proof["disposition"]
    _, model, extension = load_contract(Path("config/pipelines"))
    assert pa.Table.from_pylist(
        [row], schema=schemas(model, extension)["dwd_quality_exception"]
    ).to_pylist() == [row]
    assert not any(k in proof for k in ("open", "high", "low", "close", "volume", "amount"))


@pytest.mark.parametrize(
    "change",
    [
        "raw_identity",
        "bar",
        "calendar_date",
        "venue",
        "state_date",
        "state_session",
        "gap_date",
        "gap_listing",
        "missing_state",
        "missing_gap",
        "before_observation",
        "control_flag",
    ],
)
def test_wrong_identity_date_venue_controls_or_evidence_fail_closed(change):
    args = case()
    if change == "raw_identity":
        args["raw"]["stock_code"] = "other"
    elif change == "bar":
        args["bar"]["close"] = Decimal("1")
    elif change == "calendar_date":
        args["calendar"]["trade_date"] = date(2026, 8, 3)
    elif change == "venue":
        args["calendar"]["venue_id"] = "other"
    elif change == "state_date":
        args["states"][0]["observation_start"] -= timedelta(days=1)
    elif change == "state_session":
        args["states"][0]["calendar_session_id"] = "other"
    elif change == "gap_date":
        args["gaps"][0]["affected_effective_at"] -= timedelta(days=1)
    elif change == "gap_listing":
        args["gaps"][0]["listing_id"] = "other"
    elif change == "missing_state":
        args["states"] = []
    elif change == "missing_gap":
        args["gaps"] = []
    elif change == "before_observation":
        args["detected_at"] -= timedelta(seconds=1)
    else:
        args["states"][0]["can_buy"] = 1
    with pytest.raises((BoundedError, SourceAssessmentError)):
        reconcile_tail_observation(**args)


def test_consistent_nontrading_control_still_does_not_resolve_old_gap():
    args = case()
    for field in ("is_tradable", "can_buy", "can_sell"):
        args["states"][0][field] = False
    row, proof = reconcile_tail_observation(**args)
    assert not proof["state_disagreement"]
    assert row["rule_id"] == "P10-472-NONTRADING-RAW"
    assert row["resolution_state"] == "UNRESOLVED"
    assert proof["gap_resolutions"] == 0

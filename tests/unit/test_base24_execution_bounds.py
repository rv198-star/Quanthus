import json
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse.base24_execution_bounds import repair_stale_bounds
from quanthus.pipelines.securities_warehouse.base24_execution_evidence import SZSE_RULE_SHA
from tests.unit.test_base24_p6_purpose import market_row


def setup():
    row = market_row()
    row.update(upper_price=Decimal("10.50"), lower_price=Decimal("9.50"))
    kw = dict(
        prior=Decimal("10"),
        ordinal=6,
        complete=False,
        policy=json.loads(Path("config/price-limit.v2.json").read_bytes()),
        raw_sha="sha256:" + "a" * 64,
        window_proof=None,
        authority_sha=SZSE_RULE_SHA,
        authority_known=datetime(2026, 10, 14, tzinfo=timezone.utc),
        repair_at=datetime(2026, 10, 23, tzinfo=timezone.utc),
    )
    return row, kw


def test_stale_five_percent_is_new_version_not_overwrite():
    row, kw = setup()
    before = deepcopy(row)
    proof = repair_stale_bounds(row, **kw)
    assert row == before
    assert proof["before_result"]["execution_source_qualified"] is False
    assert proof["replacement_result"]["execution_source_qualified"] is True
    assert proof["replacement_row"]["upper_price"] == Decimal("11")
    assert proof["replacement_row"]["state_known"] == kw["repair_at"]
    assert proof["consumer_admission_granted"] is False
    assert proof["replacement_row"]["close"] == before["close"]


@pytest.mark.parametrize(
    "change", ["other_bound", "status", "venue", "authority", "missing_price", "null_prior"]
)
def test_bound_repair_cannot_bypass_other_gates(change):
    row, kw = setup()
    if change == "other_bound":
        row["upper_price"] = Decimal("10.60")
    if change == "status":
        row["dated_status"] = []
    if change == "venue":
        row["board"] = "ChiNext"
    if change == "authority":
        kw["authority_sha"] = "sha256:" + "f" * 64
    if change == "missing_price":
        row["close"] = None
    if change == "null_prior":
        kw["prior"] = None
    with pytest.raises(ValueError):
        repair_stale_bounds(row, **kw)

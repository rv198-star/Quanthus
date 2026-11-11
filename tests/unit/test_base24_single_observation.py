from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base24_single_observation import (
    FIELDS,
    project_observation,
)


def fixture():
    t = datetime(2026, 8, 3, tzinfo=timezone.utc)
    known = datetime(2026, 10, 23, tzinfo=timezone.utc)
    query = dict(
        code="sz.002569",
        start_date="2026-08-03",
        end_date="2026-08-03",
        fields=FIELDS,
        frequency="d",
        adjustflag="3",
    )
    raw = dict(
        query=query,
        error_code="0",
        rows=[
            dict(
                code="sz.002569",
                date="2026-08-03",
                open="2.0000000000",
                close="2.1",
                high="2.2",
                low="1.9",
                volume="10",
                amount="20",
                isST="1",
                tradestatus="1",
            )
        ],
    )
    receipt = dict(
        query=query,
        error=None,
        query_error_code="0",
        body_sha256="sha256:" + "a" * 64,
        rows=1,
        started_at=known.isoformat(),
        completed_at=known.isoformat(),
    )
    listing = dict(
        listing_id="l",
        listing_code="002569",
        venue_id="v",
        effective_from=t,
        effective_to=None,
        knowledge_from=t,
        listing_currency="CNY",
    )
    calendar = dict(
        calendar_session_id="c",
        venue_id="v",
        trade_date=date(2026, 8, 3),
        session_type="day",
        is_open=True,
        observation_start=t,
        observation_end=datetime(2026, 8, 4, tzinfo=timezone.utc),
        knowledge_from=t,
    )
    kwargs = dict(
        source_sha256=receipt["body_sha256"],
        expected_code="002569",
        expected_day=date(2026, 8, 3),
        repair_at=known,
    )
    return raw, receipt, listing, calendar, kwargs


def test_exact_raw_value_and_new_clock_preserved_without_execution_authority():
    raw, receipt, listing, calendar, kwargs = fixture()
    original = deepcopy(raw)
    got = project_observation(raw, receipt, listing, calendar, **kwargs)
    assert got["bar"]["open"] == Decimal("2.0000000000")
    assert got["bar"]["open"].as_tuple().exponent == -10
    assert got["bar"]["knowledge_from"] == kwargs["repair_at"]
    assert got["state"]["upper_price"] is None
    assert got["provider_acquired_at"] is None
    assert got["consumer_admission_granted"] is False
    assert raw == original


@pytest.mark.parametrize(
    "change", ["date", "venue", "code", "precision", "future", "float", "empty", "status"]
)
def test_unknown_or_out_of_scope_observation_fails(change):
    raw, receipt, listing, calendar, kwargs = fixture()
    if change == "date":
        calendar["trade_date"] = date(2026, 8, 4)
    if change == "venue":
        calendar["venue_id"] = "other"
    if change == "code":
        raw["rows"][0]["code"] = "sz.000001"
    if change == "precision":
        raw["rows"][0]["open"] = "2.00000000001"
    if change == "future":
        receipt["completed_at"] = "2026-10-24T00:00:00+00:00"
    if change == "float":
        raw["rows"][0]["open"] = 2.0
    if change == "empty":
        raw["rows"] = []
    if change == "status":
        raw["rows"][0]["isST"] = ""
    with pytest.raises(ValueError):
        project_observation(raw, receipt, listing, calendar, **kwargs)

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse.base22 import load_contract, schemas
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_index import TABLE, index_observation


def fixture():
    _, model, extension = load_contract(Path("config/pipelines"))
    raw = {
        "index_code": "000300.SH",
        "date": "2026-08-03",
        "open": 1.25,
        "high": 2.5,
        "low": 1.125,
        "close": 2.0,
        "volume": 123.0,
        "amount": 321.25,
        "acquired_at": "2026-08-03T16:00:00.123456+08:00",
    }
    index = {
        "index_id": "index-1",
        "index_code": "000300.SH",
        "base_currency": "CNY",
        "effective_from": datetime(1990, 1, 1, tzinfo=timezone.utc),
        "effective_to": None,
        "knowledge_from": datetime(2026, 8, 4, tzinfo=timezone.utc),
        "knowledge_to": None,
    }
    arguments = dict(
        index=index,
        market_date=date(2026, 8, 3),
        source_sha256="sha256:" + "a" * 64,
        revision="fixture-v1",
        schema=schemas(model, extension)[TABLE],
    )
    return raw, arguments


def test_native_identity_numeric_and_source_clock_preservation():
    raw, arguments = fixture()
    row, proof = index_observation(raw, **arguments)
    assert row["open"] == Decimal("1.25")
    assert row["knowledge_from"] == arguments["index"]["knowledge_from"]
    assert proof["source_acquisition_instant"].microsecond == 123456
    assert proof["numeric_classifications"] == {"FLOAT_ORIGINAL_PROVIDER_DECIMAL_UNAVAILABLE": 6}
    assert row["calendar_session_id"] is None
    assert not proof["provider_acquisition_qualified"]


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("index_code", "399001.SZ", "DATE_OR_IDENTITY"),
        ("date", "2026-07-31", "DATE_OR_IDENTITY"),
        ("acquired_at", "2026-08-03T16:00:00.1234567+08:00", "CLOCK_PRECISION"),
    ],
)
def test_source_date_identity_and_time_precision_fail_closed(field, value, reason):
    raw, arguments = fixture()
    raw[field] = value
    with pytest.raises(BoundedError, match=reason):
        index_observation(raw, **arguments)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, 0.123456789012345])
def test_invalid_or_nonrepresentable_numeric_observation_blocks(value):
    raw, arguments = fixture()
    raw["open"] = value
    with pytest.raises(RuntimeError):
        index_observation(raw, **arguments)


def test_negative_float_zero_is_not_silently_changed_by_arrow():
    raw, arguments = fixture()
    raw["open"] = -0.0
    with pytest.raises(BoundedError, match="FLOAT_INFORMATION_CHANGED"):
        index_observation(raw, **arguments)


def test_zero_and_nullable_amounts_remain_distinct():
    raw, arguments = fixture()
    raw.update(open=0.0, volume=None, amount=0.0)
    row, proof = index_observation(raw, **arguments)
    assert row["open"] == 0 and row["turnover"] == 0 and row["volume"] is None
    assert proof["numeric_classifications"]["NULL"] == 1
    assert proof["numeric_classifications"]["ZERO"] == 2


def test_august31_endpoint_is_not_clipped():
    raw, arguments = fixture()
    raw["date"] = "2026-08-31"
    raw["acquired_at"] = "2026-08-31T16:00:00+08:00"
    arguments["market_date"] = date(2026, 8, 31)
    row, _ = index_observation(raw, **arguments)
    assert row["observation_end"].astimezone(row["observation_start"].tzinfo).date() == date(
        2026, 8, 31
    )
    # Serialized UTC endpoint is Sep1 midnight Shanghai, Aug31 16:00 UTC.
    assert row["observation_end"] == datetime(2026, 8, 31, 16, tzinfo=timezone.utc)


def test_september_business_observation_blocks():
    raw, arguments = fixture()
    raw["date"] = "2026-09-01"
    arguments["market_date"] = date(2026, 9, 1)
    with pytest.raises(BoundedError, match="DATE_OR_IDENTITY"):
        index_observation(raw, **arguments)


def test_future_bar_cannot_gain_an_earlier_acquisition_from_parent_floor():
    raw, arguments = fixture()
    raw["acquired_at"] = "2026-08-01T16:00:00+08:00"
    with pytest.raises(BoundedError, match="ACQUISITION_BEFORE_OBSERVATION"):
        index_observation(raw, **arguments)


@pytest.mark.parametrize(
    "field,value",
    [
        ("effective_from", datetime(2026, 8, 4, tzinfo=timezone.utc)),
        ("effective_to", datetime(2026, 8, 1, tzinfo=timezone.utc)),
        ("knowledge_to", datetime(2026, 8, 4, tzinfo=timezone.utc)),
    ],
)
def test_dated_parent_window_blocks(field, value):
    raw, arguments = fixture()
    arguments["index"][field] = value
    with pytest.raises(BoundedError, match="PARENT_CLOSURE"):
        index_observation(raw, **arguments)

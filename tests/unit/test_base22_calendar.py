from datetime import date, datetime, timedelta, timezone

import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_calendar import calendar_rows


def case():
    built = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)
    now = datetime(2026, 10, 6, 9, tzinfo=timezone.utc)
    rows = [
        {
            "exchange": exchange,
            "trade_date": date(2026, 8, 1) + timedelta(days=n),
            "is_open": int(n % 7 < 5),
            "session": "day",
            "source": "baostock.query_trade_dates",
            "built_at": built,
        }
        for exchange in ("SSE", "SZSE", "BSE")
        for n in range(31)
    ]
    venues = [
        {"mic_code": mic, "venue_id": "venue-" + mic, "knowledge_from": built, "knowledge_to": None}
        for mic in ("XSHG", "XSHE", "BJSE")
    ]
    args = {
        "observed_at": now,
        "source_sha256": "sha256:" + "a" * 64,
        "revision": "frozen-calendar-source",
    }
    return rows, venues, args


def test_all_days_three_venues_clocks_and_unknown_hours():
    rows, venues, args = case()
    out, proof = calendar_rows(rows, venues, **args)
    assert len(out) == len({r["calendar_session_id"] for r in out}) == 93
    assert {r["knowledge_from"] for r in out} == {args["observed_at"]}
    assert all(r["open_at"] is r["close_at"] is None for r in out)
    assert out[0]["observation_start"].astimezone(timezone.utc).date() == date(2026, 7, 31)
    assert proof["provider_acquisition"] is None
    assert proof["session_hours_inferred"] is proof["historical_PIT_admission"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("is_open", 2),
        ("is_open", True),
        ("exchange", "OTHER"),
        ("session", "night"),
        ("source", "unknown"),
    ],
)
def test_invalid_source_flag_session_or_exchange_rejected(field, value):
    rows, venues, args = case()
    rows[0][field] = value
    with pytest.raises(BoundedError, match="SOURCE_FLAG_SESSION"):
        calendar_rows(rows, venues, **args)


def test_duplicate_and_missing_days_rejected():
    rows, venues, args = case()
    with pytest.raises(BoundedError, match="DUPLICATE"):
        calendar_rows(rows + [rows[0]], venues, **args)
    with pytest.raises(BoundedError, match="93_KEYS"):
        calendar_rows(rows[1:], venues, **args)


def test_september_observation_excluded_without_truncating_interval():
    rows, venues, args = case()
    extra = {**rows[0], "trade_date": date(2026, 9, 1)}
    out, _ = calendar_rows(rows + [extra], venues, **args)
    assert len(out) == 93
    assert max(r["observation_end"].date() for r in out) == date(2026, 9, 1)


def test_source_and_parent_clock_rejected():
    rows, venues, args = case()
    with pytest.raises(BoundedError, match="NOT_AFTER"):
        calendar_rows(rows, venues, **{**args, "observed_at": rows[0]["built_at"]})
    venues[0]["knowledge_to"] = args["observed_at"]
    with pytest.raises(BoundedError, match="VENUE_KNOWLEDGE"):
        calendar_rows(rows, venues, **args)


def test_unknown_missing_and_duplicate_venues_rejected():
    rows, venues, args = case()
    with pytest.raises(BoundedError, match="ALL_THREE"):
        calendar_rows(rows, venues[:-1], **args)
    with pytest.raises(BoundedError, match="EXACT_VENUE"):
        calendar_rows(rows, venues + [venues[0]], **args)

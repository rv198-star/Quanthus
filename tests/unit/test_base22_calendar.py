from datetime import date, datetime, timedelta, timezone

import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_calendar import calendar_rows, calendar_union


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


def test_partial_windows_form_one_exact_union_with_preserved_observation_clocks():
    rows, _, _ = case()
    first = rows[:50]
    second = [{**r, "built_at": r["built_at"] + timedelta(days=1)} for r in rows[40:]]
    out, proof = calendar_union([("sha256:" + "a" * 64, first), ("sha256:" + "b" * 64, second)])
    assert len(out) == 93
    assert proof["same_flag_overlap_observations"] == 10
    assert {r["built_at"] for r in out if r["exchange"] == "SSE"} == {rows[0]["built_at"]}
    reversed_out, reversed_proof = calendar_union(
        [("sha256:" + "b" * 64, second), ("sha256:" + "a" * 64, first)]
    )
    assert out == reversed_out and proof == reversed_proof


@pytest.mark.parametrize("defect", ["flag", "duplicate", "missing", "sha"])
def test_union_does_not_hide_conflict_missing_keys_or_unknown_member(defect):
    rows, _, _ = case()
    sha = "sha256:" + "a" * 64
    incoming = [{**rows[0], "is_open": 1 - rows[0]["is_open"]}]
    sources = [(sha, rows), ("sha256:" + "b" * 64, incoming)]
    if defect == "duplicate":
        sources = [(sha, rows + [rows[0]])]
    elif defect == "missing":
        sources = [(sha, rows[1:])]
    elif defect == "sha":
        sources = [("sha256:short", rows)]
    with pytest.raises(BoundedError, match="CALENDAR_UNION"):
        calendar_union(sources)

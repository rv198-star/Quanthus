"""Historical denominator cannot discard retired, unknown or conflicting scope."""

from datetime import date

import pytest

from quanthus.pipelines.securities_warehouse.base24_coverage import (
    CoverageError,
    bounded_missing_windows,
    source_denominator,
)


def listing(code="000001", start="2019-01-01", end=None, venue="SZSE"):
    return (
        "listing-source",
        dict(stock_code=code, exchange=venue, list_date=start, delist_date=end),
    )


def calendar():
    return [
        ("calendar-source", dict(exchange=venue, trade_date=day, is_open=opened))
        for venue in ["SSE", "SZSE"]
        for day, opened in [("2020-01-02", 1), ("2020-01-03", 1), ("2020-01-04", 0)]
    ]


def census(rows, cells=None):
    return source_denominator(
        rows,
        calendar() if cells is None else cells,
        source_ids=frozenset({"listing-source", "calendar-source"}),
        first=date(2020, 1, 2),
        through=date(2020, 1, 4),
    )


def test_retired_boundary_and_unknown_suspension_stay_in_denominator():
    result = census([listing(), listing("000005", end="2020-01-03")])
    assert result["known_interval_session_units"] == 4
    assert result["required_uncertain_delist_boundary"] == [
        dict(venue="SZSE", code="000005", date="2020-01-03")
    ]
    assert result["source_structure_complete"]
    assert not result["universe_completeness_granted"]
    assert result["expected_bucket_count"] is None  # Never assume universal48.
    assert result["source_admitted_DWD_bucket_census"] == "NOT_RUN"


def test_missing_closed_day_is_unknown_not_an_inferred_weekend():
    result = census([listing()], calendar()[:-1])
    assert not result["source_structure_complete"]
    assert result["missing_calendar_cells"] == [dict(venue="SZSE", date="2020-01-04")]


def test_same_day_other_venue_cannot_close_calendar_cell():
    cells = [(s, {**r, "exchange": "SSE"}) for s, r in calendar()]
    result = census([listing()], cells)
    assert len(result["missing_calendar_cells"]) == 3
    assert not result["source_structure_complete"]


def test_conflicting_calendar_rejected():
    cells = calendar() + [
        ("calendar-source", dict(exchange="SZSE", trade_date="2020-01-02", is_open=False))
    ]
    with pytest.raises(CoverageError, match="CONFLICT"):
        census([listing()], cells)


def test_unknown_and_conflicting_listing_dates_are_not_silently_prioritized():
    result = census([listing(start=None), listing("000005"), listing("000005", start="2020-01-03")])
    assert result["known_listing_keys"] == 2
    assert len(result["unknown_intervals"]) == len(result["conflicting_intervals"]) == 1
    assert not result["source_structure_complete"]


@pytest.mark.parametrize("family", ["calendar", "listing"])
def test_unregistered_sources_rejected(family):
    rows = [listing()]
    cells = calendar()
    if family == "listing":
        rows[0] = ("unknown", rows[0][1])
    else:
        cells[0] = ("unknown", cells[0][1])
    with pytest.raises(CoverageError, match="UNKNOWN"):
        census(rows, cells)


def test_date_scope_not_refreshed_by_renaming_snapshot_and_bse_stays_outside():
    result = census(
        [listing(), listing("430001", venue="BSE")],
        [(s, {**r, "trade_date": "2019-01-01"}) for s, r in calendar()],
    )
    assert not result["source_structure_complete"]
    assert len(result["missing_calendar_cells"]) == 6
    assert result["excluded_market_input_rows"] == {"BSE": 1}


def test_new_listing_cannot_gain_earlier_sessions_or_cross_venue():
    result = census(
        [listing("001248", start="2020-01-03"), listing("600000", start="2020-01-04", venue="SSE")]
    )
    assert result["known_interval_session_units"] == 1


def test_missing_windows_merge_contiguous_years_and_bounded_nearby_gaps():
    rows = [
        ("SSE", "600000", date(2020, 1, day), good)
        for day, good in [(2, False), (3, False), (6, True), (7, True), (8, True), (9, False)]
    ]
    windows = bounded_missing_windows(rows, max_session_days=6, max_observed_gap=3)
    assert windows == [
        {
            "venue": "SSE",
            "stock_code": "600000",
            "start_date": "2020-01-02",
            "end_date": "2020-01-09",
            "required_dates": ["2020-01-02", "2020-01-03", "2020-01-09"],
            "expected_session_dates": [
                "2020-01-02",
                "2020-01-03",
                "2020-01-06",
                "2020-01-07",
                "2020-01-08",
                "2020-01-09",
            ],
            "required_security_days": 3,
            "expected_source_days": 6,
        }
    ]


def test_missing_windows_split_on_four_good_sessions_or_limits():
    rows = [("SZSE", "000001", date(2020, 1, day), 3 <= day <= 6) for day in range(2, 9)]
    windows = bounded_missing_windows(rows, max_session_days=4, max_observed_gap=3)
    assert [window["required_dates"] for window in windows] == [
        ["2020-01-02"],
        ["2020-01-07", "2020-01-08"],
    ]

from datetime import date

import pytest

from quanthus.pipelines.daily_core_cycle import pending_market_dates


def test_recovery_uses_actual_holiday_calendar() -> None:
    rows = [
        {"trade_date": "2026-09-25", "is_open": "0"},
        {"trade_date": "2026-09-26", "is_open": "0"},
        {"trade_date": "2026-09-27", "is_open": "0"},
        {"trade_date": "2026-09-28", "is_open": "1"},
        {"trade_date": "2026-09-29", "is_open": "1"},
    ]
    assert pending_market_dates(date(2026, 9, 24), date(2026, 9, 29), rows) == [
        date(2026, 9, 28),
        date(2026, 9, 29),
    ]


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [{"trade_date": "2026-09-24", "is_open": "unknown"}],
        [{"trade_date": "2026-09-24", "is_open": "1"}] * 2,
    ],
)
def test_missing_or_ambiguous_calendar_stops_recovery(rows: list) -> None:
    with pytest.raises(ValueError):
        pending_market_dates(date(2026, 9, 23), date(2026, 9, 24), rows)


def test_completed_date_has_no_duplicate_attempt() -> None:
    assert pending_market_dates(date(2026, 9, 24), date(2026, 9, 24), []) == []

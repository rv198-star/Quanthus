from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from quanthus.pipelines.trading_maturity import (
    NextSessionEvidence,
    minute_maturity_eligible,
    overdue_on_next_session,
    resolve_next_session,
)

CST = ZoneInfo("Asia/Shanghai")


def _rows(start: date, next_day: date):
    day = start
    while day <= next_day:
        yield [day.isoformat(), "1" if day in (start, next_day) else "0"]
        day += timedelta(days=1)


def test_mid_autumn_tplus1_is_monday_not_calendar_friday() -> None:
    evidence = resolve_next_session(
        date(2026, 9, 24), list(_rows(date(2026, 9, 24), date(2026, 9, 28)))
    )
    assert evidence.next_session == date(2026, 9, 28)
    assert len(evidence.calendar_sha256) == 64
    assert not minute_maturity_eligible(
        date(2026, 9, 24), datetime(2026, 9, 25, 17, 40, tzinfo=CST), evidence
    )
    assert not minute_maturity_eligible(
        date(2026, 9, 24), datetime(2026, 9, 28, 10, 9, tzinfo=CST), evidence
    )
    assert minute_maturity_eligible(
        date(2026, 9, 24), datetime(2026, 9, 28, 10, 10, tzinfo=CST), evidence
    )
    assert not overdue_on_next_session(
        date(2026, 9, 24), datetime(2026, 9, 28, 17, 39, tzinfo=CST), time(17, 40), evidence
    )
    assert overdue_on_next_session(
        date(2026, 9, 24), datetime(2026, 9, 28, 17, 40, tzinfo=CST), time(17, 40), evidence
    )


def test_national_holiday_tplus1_is_october_eighth() -> None:
    evidence = resolve_next_session(
        date(2026, 9, 30), list(_rows(date(2026, 9, 30), date(2026, 10, 8)))
    )
    assert evidence.next_session == date(2026, 10, 8)
    assert not minute_maturity_eligible(
        date(2026, 9, 30), datetime(2026, 10, 7, 12, tzinfo=CST), evidence
    )


def test_full_calendar_continuity_and_open_start_are_required() -> None:
    start = date(2026, 9, 24)
    with pytest.raises(ValueError, match="missing"):
        resolve_next_session(start, [["2026-09-24", "1"], ["2026-09-26", "0"], ["2026-09-28", "1"]])
    with pytest.raises(ValueError, match="not an exchange"):
        resolve_next_session(start, [["2026-09-24", "0"], ["2026-09-25", "1"]])
    with pytest.raises(ValueError, match="unavailable"):
        resolve_next_session(start, [["2026-09-24", "1"], ["2026-09-25", "0"]])
    with pytest.raises(ValueError, match="invalid"):
        resolve_next_session(start, [["2026-09-24", "2"], ["2026-09-25", "1"]])


def test_timezone_and_bad_evidence_are_rejected() -> None:
    start = date(2026, 9, 24)
    ev = NextSessionEvidence(date(2026, 9, 24), "frozen", "a" * 64)
    with pytest.raises(ValueError, match="did not advance"):
        minute_maturity_eligible(start, datetime(2026, 9, 24, tzinfo=CST), ev)
    ev2 = NextSessionEvidence(date(2026, 9, 28), "frozen", "b" * 64)
    with pytest.raises(ValueError, match="timezone"):
        overdue_on_next_session(start, datetime(2026, 9, 28), time(17, 40), ev2)


def test_identical_calendar_evidence_is_reproducible() -> None:
    rows = list(_rows(date(2026, 9, 24), date(2026, 9, 28)))
    a = resolve_next_session(date(2026, 9, 24), rows)
    b = resolve_next_session(date(2026, 9, 24), rows)
    assert a == b
    assert (
        a.calendar_sha256
        != resolve_next_session(
            date(2026, 9, 23),
            list(_rows(date(2026, 9, 23), date(2026, 9, 24))),
        ).calendar_sha256
    )

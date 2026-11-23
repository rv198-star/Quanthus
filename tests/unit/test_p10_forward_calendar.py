"""Date/identity/clock boundaries for P12's independent calendar facts."""

from datetime import date
from pathlib import Path

import pytest

from quanthus.p10_forward_calendar import (
    SOURCES,
    CalendarError,
    annual_closures,
    build_calendar,
    date_payload,
    encoded,
    publish,
    select_next,
    sha256,
)


def _descriptor():
    result = {
        "coverage": {"start": "2026-11-24", "end": "2026-12-31"},
        "status": "PARTIAL",
        "venues": {
            "SSE": date_payload(date(2026, 11, 24), date(2026, 12, 31), set()),
            "BSE": {"status": "unavailable"},
        },
    }
    result["content_sha256"] = sha256(encoded(result))
    return result


@pytest.mark.parametrize(
    ("day", "next_day"),
    [
        ("2026-11-24", "2026-11-25"),
        ("2026-11-27", "2026-11-30"),
        ("2026-11-28", "2026-11-30"),
        ("2026-12-30", "2026-12-31"),
    ],
)
def test_next_is_strictly_later_inside_coverage(day, next_day):
    actual = select_next(
        _descriptor(),
        "SSE",
        date.fromisoformat(day),
        available_at="2026-11-24T08:00:00+08:00",
        knowledge_cutoff="2026-11-24T22:30:00+08:00",
    )
    assert actual == {"status": "AVAILABLE", "next_session": next_day, "reason": None}


@pytest.mark.parametrize(
    ("venue", "day", "reason"),
    [
        ("SSE", "2026-12-31", "NEXT_SESSION_OUTSIDE_COVERAGE"),
        ("SSE", "2027-01-01", "DATE_OUTSIDE_COVERAGE"),
        ("SSE", "2026-11-23", "DATE_OUTSIDE_COVERAGE"),
        ("BSE", "2026-11-24", "VENUE_UNAVAILABLE"),
    ],
)
def test_missing_coverage_does_not_fall_back_to_weekday(venue, day, reason):
    actual = select_next(
        _descriptor(),
        venue,
        date.fromisoformat(day),
        available_at="2026-11-24T08:00:00+08:00",
        knowledge_cutoff="2026-11-24T22:30:00+08:00",
    )
    assert actual == {"status": "unavailable", "next_session": None, "reason": reason}


def test_prior_cutoff_cannot_use_freshly_received_document():
    actual = select_next(
        _descriptor(),
        "SSE",
        date(2026, 11, 24),
        available_at="2026-11-24T08:00:00+08:00",
        knowledge_cutoff="2026-11-24T07:59:59+08:00",
    )
    assert actual["reason"] == "NOT_YET_VISIBLE"
    assert actual["next_session"] is None


def test_civil_makeup_weekend_remains_closed_and_announced_holiday_is_skipped():
    closed = {date(2026, 10, day) for day in range(1, 8)}
    payload = date_payload(date(2026, 9, 30), date(2026, 10, 12), closed)
    assert payload["next_session"]["2026-09-30"] == "2026-10-08"
    assert payload["next_session"]["2026-10-09"] == "2026-10-12"
    assert "2026-10-10" not in payload["sessions"]


@pytest.mark.parametrize(
    ("first", "last"),
    [
        (date(2026, 1, 1), date(2026, 1, 10)),
        (date(2026, 12, 31), date(2027, 1, 4)),
    ],
)
def test_rule_cannot_expand_to_prior_or_future_effective_scope(first, last):
    with pytest.raises(CalendarError, match="RULE_OR_YEAR_OUTSIDE_COVERAGE"):
        date_payload(first, last, set())


def test_source_fixture_cannot_impersonate_pinned_official_original():
    documents = dict.fromkeys(SOURCES, b"fixture")
    with pytest.raises(CalendarError, match="SOURCE_IDENTITY_MISMATCH"):
        build_calendar(documents, [], built_at="2026-11-24T08:00:00+08:00", code_revision="a" * 40)


def test_incomplete_notice_does_not_become_full_year_authority():
    with pytest.raises(CalendarError, match="INCOMPLETE_ANNUAL_NOTICE"):
        annual_closures(
            "上证公告〔2025〕45号2026年部分节假日休市安排一、休市安排二、".encode(),
            "上证公告〔2025〕45号",
        )


def test_tampered_next_session_invalidates_semantic_digest():
    descriptor = _descriptor()
    descriptor["venues"]["SSE"]["next_session"]["2026-11-24"] = "2026-11-28"
    with pytest.raises(CalendarError, match="DESCRIPTOR_CONTENT_IDENTITY"):
        select_next(
            descriptor,
            "SSE",
            date(2026, 11, 24),
            available_at="2026-11-24T08:00:00+08:00",
            knowledge_cutoff="2026-11-24T22:30:00+08:00",
        )


def test_publication_keeps_old_version_and_binds_pointer(tmp_path: Path, monkeypatch):
    descriptor = _descriptor()
    descriptor.update(built_at="2026-11-24T08:00:00+08:00", code_revision="a" * 40)
    monkeypatch.setattr("quanthus.p10_forward_calendar.build_calendar", lambda *a, **k: descriptor)
    pointer = publish(tmp_path, "test-v1", descriptor, {}, [])
    body = (tmp_path / pointer["descriptor"]).read_bytes()
    assert sha256(body) == pointer["descriptor_sha256"]
    assert pointer["published_at"] <= pointer["available_at"]
    assert (tmp_path / "CURRENT_FORWARD_CALENDAR.json").read_bytes() == encoded(pointer)
    with pytest.raises(CalendarError, match="IMMUTABLE_VERSION_EXISTS"):
        publish(tmp_path, "test-v1", descriptor, {}, [])
    assert (tmp_path / pointer["descriptor"]).read_bytes() == body

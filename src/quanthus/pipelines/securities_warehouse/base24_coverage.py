"""Source-derived historical listing/session prerequisite for the Base 2.4 job.

Only compact source metadata is held; security-day/bucket products are not expanded
into JSON/SQLite. This is not a grant of complete-universe or minute qualification.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from typing import Any

VENUES = frozenset({"SSE", "SZSE"})
CONTRACT = "p10-base24-historical-denominator-v1"


class CoverageError(ValueError):
    """Fail-closed source/identity/calendar reason without market values."""


def _day(value: Any) -> date:
    if isinstance(value, date) and not hasattr(value, "hour"):
        return value
    if isinstance(value, str):
        parsed = date.fromisoformat(value)
        if parsed.isoformat() == value:
            return parsed
    raise CoverageError("EXPLICIT_BUSINESS_DATE_REQUIRED")


def source_denominator(
    listings: Iterable[tuple[str, Mapping[str, Any]]],
    calendar: Iterable[tuple[str, Mapping[str, Any]]],
    *,
    source_ids: frozenset[str],
    first: date,
    through: date,
) -> dict[str, Any]:
    """Count observed source intervals without guessing precedence or exemptions.

    Delisting-day observations stay required/uncertain until separate key-specific
    evidence resolves their applicability. Missing dates, contradictory intervals
    and closed-day conflicts are surfaced, never used to shrink the denominator.
    Actual source/admitted/DWD bucket census remains an explicit downstream stage.
    """
    if first > through or not source_ids or any(not source for source in source_ids):
        raise CoverageError("DENOMINATOR_SCOPE_OR_SOURCES_INVALID")
    cells: dict[tuple[str, date], bool] = {}
    for source, row in calendar:
        if source not in source_ids:
            raise CoverageError("UNKNOWN_CALENDAR_SOURCE")
        venue = row["exchange"]
        if venue not in VENUES:
            continue  # Explicit product boundary; never silently enlarge the market.
        day = _day(row["trade_date"])
        if not first <= day <= through:
            continue
        opened = row["is_open"]
        if type(opened) not in (int, bool) or opened not in (0, 1):
            raise CoverageError("CALENDAR_OPEN_VALUE_INVALID")
        calendar_key = (venue, day)
        if calendar_key in cells and cells[calendar_key] != bool(opened):
            raise CoverageError("CALENDAR_DATE_VENUE_CONFLICT")
        cells[calendar_key] = bool(opened)
    missing_calendar = []
    day = first
    while day <= through:
        for venue in sorted(VENUES):
            if (venue, day) not in cells:
                missing_calendar.append({"venue": venue, "date": day.isoformat()})
        day += timedelta(days=1)
    sessions = {
        venue: sorted(d for (v, d), opened in cells.items() if v == venue and opened)
        for venue in VENUES
    }
    intervals: dict[tuple[str, str], set[tuple[date, date | None]]] = {}
    unknown: set[tuple[str, str]] = set()
    excluded: Counter[str] = Counter()
    for source, row in listings:
        if source not in source_ids:
            raise CoverageError("UNKNOWN_LISTING_SOURCE")
        venue = row["exchange"]
        if venue not in VENUES:
            excluded[venue] += 1
            continue
        code = row["stock_code"]
        if not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit():
            raise CoverageError("LISTING_IDENTITY_INVALID")
        listing_key = (venue, code)
        if row.get("list_date") is None:
            unknown.add(listing_key)
            continue
        start = _day(row["list_date"])
        end = _day(row["delist_date"]) if row.get("delist_date") is not None else None
        if end is not None and end < start:
            raise CoverageError("LISTING_INTERVAL_REVERSED")
        intervals.setdefault(listing_key, set()).add((start, end))
    conflicts = sorted(key for key, variants in intervals.items() if len(variants) > 1)
    counts: Counter[tuple[str, str]] = Counter()
    boundary = []
    listing_counts = []
    for (venue, code), variants in sorted(intervals.items()):
        if len(variants) != 1 or (venue, code) in unknown:
            continue
        start, end = next(iter(variants))
        days = sessions[venue]
        begin = bisect_left(days, max(first, start))
        finish = bisect_right(days, min(through, end or through))
        selected = days[begin:finish]
        for day in selected:
            counts[(day.strftime("%Y-%m"), venue)] += 1
        listing_counts.append({"venue": venue, "code": code, "session_units": len(selected)})
        if end is not None and first <= end <= through and cells.get((venue, end)):
            boundary.append({"venue": venue, "code": code, "date": end.isoformat()})
    return {
        "contract": CONTRACT,
        "first": first.isoformat(),
        "through": through.isoformat(),
        "known_listing_keys": len(intervals.keys() | unknown),
        "unknown_intervals": sorted(unknown),
        "conflicting_intervals": conflicts,
        "missing_calendar_cells": missing_calendar,
        "excluded_market_input_rows": dict(sorted(excluded.items())),
        "known_interval_session_units": sum(counts.values()),
        "month_venue_counts": [
            dict(month=m, venue=v, units=n) for (m, v), n in sorted(counts.items())
        ],
        "listing_counts": listing_counts,
        "required_uncertain_delist_boundary": boundary,
        "source_structure_complete": not (unknown or conflicts or missing_calendar),
        "universe_completeness_granted": False,
        "expected_bucket_count": None,
        "source_admitted_DWD_bucket_census": "NOT_RUN",
        "provider_acquisition_inferred": False,
    }

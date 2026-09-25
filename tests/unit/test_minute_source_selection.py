"""A bad primary never contaminates a qualified whole-security fallback."""

from __future__ import annotations

from dataclasses import replace

import pytest

from quanthus.providers.minute_source_selection import (
    MinuteCandidate,
    choose_whole_security,
    qualify_candidate,
)
from quanthus.providers.tencent_m1_normalize import expected_times

DATE = "2026-09-24"
SHA = "a" * 64


def candidate(provider: str = "tencent") -> MinuteCandidate:
    rows = tuple(
        {
            "stock_code": "000001",
            "ts": f"{DATE} {stamp[-4:-2]}:{stamp[-2:]}:00",
            "open": 11.3,
            "high": 11.3,
            "low": 11.3,
            "close": 11.3,
            "volume": 1,
            "amount": None,
        }
        for stamp in expected_times(DATE)
    )
    return MinuteCandidate(
        provider,
        SHA,
        "quarantine/raw/000001.json.gz",
        rows,
        "09:30 merged using source bars",
        "null_source_absent",
    )


def decide(*items: MinuteCandidate):
    return choose_whole_security(
        items,
        symbol="000001",
        market_date=DATE,
        daily_volume=240,
        daily_close=11.3,
        traded=True,
        priority=("relay", "tencent"),
    )


def test_bad_primary_allows_independently_qualified_fallback() -> None:
    bad = candidate("relay")
    bad.rows[0]["volume"] = 29
    result = decide(bad, candidate())
    assert result.status == "qualified_whole_security_candidate"
    assert result.selected_provider == "tencent"
    assert result.rejected[0][0] == "relay"
    assert "volume mismatch" in result.rejected[0][1]
    assert result.amount_status == "null_source_absent"


def test_no_qualified_candidate_remains_pending() -> None:
    bad = candidate()
    bad.rows[0]["volume"] = 29
    assert decide(bad).status == "pending_no_qualified_source"


def test_two_qualified_but_different_minute_allocations_block() -> None:
    other = candidate("relay")
    other.rows[0]["volume"] = 2
    other.rows[1]["volume"] = 0
    assert decide(other, candidate()).status == "blocked_cross_source_conflict"


def test_same_semantics_respects_versioned_priority() -> None:
    result = decide(candidate(), candidate("relay"))
    assert result.selected_provider == "relay"
    assert result.qualified == ("relay", "tencent")


def test_wrong_date_or_missing_grid_rejected() -> None:
    wrong = candidate()
    wrong.rows[0]["ts"] = "2026-09-23 09:31:00"
    with pytest.raises(ValueError, match="minute grid"):
        qualify_candidate(
            wrong,
            symbol="000001",
            market_date=DATE,
            daily_volume=240,
            daily_close=11.3,
            traded=True,
        )
    with pytest.raises(ValueError, match="240"):
        qualify_candidate(
            replace(candidate(), rows=candidate().rows[:-1]),
            symbol="000001",
            market_date=DATE,
            daily_volume=240,
            daily_close=11.3,
            traded=True,
        )


def test_close_and_raw_provenance_are_hard_gates() -> None:
    with pytest.raises(ValueError, match="close mismatch"):
        qualify_candidate(
            candidate(),
            symbol="000001",
            market_date=DATE,
            daily_volume=240,
            daily_close=11.31,
            traded=True,
        )
    with pytest.raises(ValueError, match="raw source SHA"):
        qualify_candidate(
            replace(candidate(), raw_sha256=""),
            symbol="000001",
            market_date=DATE,
            daily_volume=240,
            daily_close=11.3,
            traded=True,
        )


def test_suspended_security_is_zero_rows_only() -> None:
    empty = replace(candidate(), rows=())
    assert qualify_candidate(
        empty, symbol="000001", market_date=DATE, daily_volume=0, daily_close=11.3, traded=False
    )
    with pytest.raises(ValueError, match="suspended"):
        qualify_candidate(
            candidate(),
            symbol="000001",
            market_date=DATE,
            daily_volume=0,
            daily_close=11.3,
            traded=False,
        )

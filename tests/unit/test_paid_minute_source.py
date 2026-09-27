"""Strict qualification of the paid proxy's historical 1m response."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from quanthus.providers.minute_source_selection import qualify_candidate
from quanthus.providers.paid_minute_source import (
    _audit_symbol,
    normalized_candidate,
    qualify_payload,
    select_canary_symbols,
)
from quanthus.providers.tencent_m1_normalize import expected_times

DATE = "2026-09-23"
FIELDS = ["ts_code", "trade_time", "freq", "open", "high", "low", "close", "vol", "amount"]


def payload(*, auction_volume: int = 100, preopen: bool = False) -> dict:
    items = []
    for stamp in expected_times(DATE):
        ts = f"{DATE} {stamp[-4:-2]}:{stamp[-2:]}:00"
        items.append(["000001.SZ", ts, "1MIN", 11.0, 11.0, 11.0, 11.0, 10, 110])
    items.append(
        ["000001.SZ", f"{DATE} 09:30:00", "1MIN", 11.0, 11.0, 11.0, 11.0, auction_volume, 1100]
    )
    if preopen:
        for minute in range(25, 30):
            items.append(
                ["000001.SZ", f"{DATE} 09:{minute}:00", "1MIN", 11.0, 11.0, 11.0, 11.0, 200, 2200]
            )
    return {
        "code": 0,
        "api_name": "stk_mins",
        "count": len(items),
        "meta": {"partial": False, "coverage": "complete"},
        "data": {"fields": FIELDS, "items": items},
    }


def start_labeled_payload() -> dict:
    items = []
    for stamp in expected_times(DATE):
        canonical = datetime.strptime(stamp, "%Y%m%d%H%M")
        source = canonical - timedelta(minutes=1)
        items.append(
            [
                "000001.SZ",
                source.strftime("%Y-%m-%d %H:%M:00"),
                "1MIN",
                11.0,
                11.0,
                11.0,
                11.0,
                10,
                110,
            ]
        )
    # These observations are preserved in raw evidence, but fixed-session
    # selection must not admit them to the 240-row candidate.
    for hhmm, volume in (
        ("09:25", 70),
        ("09:26", 70),
        ("09:27", 70),
        ("09:28", 70),
        ("09:29", 70),
        ("11:30", 19),
        ("12:59", 20),
        ("15:00", 30),
        ("15:33", 30),
    ):
        items.append(
            ["000001.SZ", f"{DATE} {hhmm}:00", "1MIN", 11.0, 11.0, 11.0, 11.0, volume, volume * 11]
        )
    return {
        "code": 0,
        "api_name": "stk_mins",
        "count": len(items),
        "meta": {"partial": False, "coverage": "complete"},
        "data": {"fields": FIELDS, "items": items},
    }


def check(value: dict, *, daily: int = 2500, first5: int = 150) -> dict:
    return qualify_payload(
        value,
        symbol="000001.SZ",
        market_date=DATE,
        daily_volume=daily,
        daily_close=11.0,
        first_5m_volume=first5,
    )


def test_exact_auction_is_merged_and_preopen_snapshots_are_excluded() -> None:
    result = check(payload(preopen=True))
    assert result["auction_mode"] == "merged_into_0931"
    assert result["preopen_duplicate_snapshots_excluded"] == 5
    assert result["source_rows"] == 246
    assert result["session_rows"] == 240
    assert result["status"] == "qualified_candidate_not_published"


def test_duplicated_auction_must_match_independent_first_5m_without_it() -> None:
    result = check(payload(), daily=2400, first5=50)
    assert result["auction_mode"] == "duplicate_0930_excluded"


def test_replay_without_5m_only_accepts_exact_included_auction() -> None:
    result = qualify_payload(
        payload(),
        symbol="000001.SZ",
        market_date=DATE,
        daily_volume=2500,
        daily_close=11,
        first_5m_volume=None,
    )
    assert result["auction_mode"] == "merged_into_0931"
    assert result["first_5m_volume_delta"] is None
    with pytest.raises(ValueError, match="daily/native-5m volume mismatch"):
        qualify_payload(
            payload(),
            symbol="000001.SZ",
            market_date=DATE,
            daily_volume=2400,
            daily_close=11,
            first_5m_volume=None,
        )


def test_daily_volume_mismatch_is_not_reallocated() -> None:
    with pytest.raises(ValueError, match="daily/native-5m volume mismatch"):
        check(payload(), daily=2496)


def test_first_5m_disagreement_is_recorded_when_daily_is_exact() -> None:
    result = check(payload(), first5=149)
    assert result["first_5m_volume_delta"] == "1"


def test_first_5m_mismatch_blocks_auction_exclusion() -> None:
    with pytest.raises(ValueError, match="daily/native-5m volume mismatch"):
        check(payload(), daily=2400, first5=49)


def test_wrong_date_and_duplicate_timestamp_block() -> None:
    wrong = payload()
    wrong["data"]["items"][0][1] = "2026-09-22 09:31:00"
    with pytest.raises(ValueError, match="symbol/frequency/date mismatch"):
        check(wrong)
    duplicate = payload()
    duplicate["data"]["items"][1][1] = duplicate["data"]["items"][0][1]
    with pytest.raises(ValueError, match="duplicate proxy minute timestamp"):
        check(duplicate)


def test_partial_response_and_missing_grid_block() -> None:
    partial = payload()
    partial["meta"]["partial"] = True
    with pytest.raises(ValueError, match="not complete"):
        check(partial)
    missing = payload()
    missing["data"]["items"].pop(1)
    missing["count"] -= 1
    with pytest.raises(ValueError, match="session grid missing"):
        check(missing)


def test_rate_limit_is_recorded_without_persisting_key(tmp_path, monkeypatch) -> None:
    import requests

    class Response:
        status_code = 429
        headers = {"x-request-id": "request-1", "retry-after": "60"}

    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: Response())
    receipt = _audit_symbol(
        "000001",
        day=DATE,
        key="do-not-persist-this-key",
        daily={"volume": 2500, "close": 11.0},
        first_5m_volume=150,
        output_dir=tmp_path,
    )
    assert receipt["status"] == "rate_limited"
    assert receipt["retry_after"] == "60"
    assert not (tmp_path / "raw").exists()
    assert "do-not-persist-this-key" not in (tmp_path / "receipts/000001.json").read_text()


def test_explicit_canary_is_bounded_to_traded_sealed_universe() -> None:
    traded = ["000001", "600000", "688001", "000002"]
    assert select_canary_symbols("000001,600000,688001", traded) == traded[:3]
    for invalid in ("", "000001,000001", "000001,600000,688001,000002", "300001"):
        with pytest.raises(ValueError, match="canary requires"):
            select_canary_symbols(invalid, traded)


def test_qualified_payload_becomes_one_complete_candidate_without_amount_inference() -> None:
    raw = json.dumps(payload(preopen=True)).encode()
    result = normalized_candidate(
        raw,
        symbol="000001.SZ",
        market_date=DATE,
        daily_volume=2500,
        daily_close=11,
        first_5m_volume=150,
        raw_ref="quarantine/raw/000001.json.gz",
    )
    assert len(result.rows) == 240
    assert result.rows[0]["volume"] == 110
    assert all(row["amount"] is None for row in result.rows)
    assert qualify_candidate(
        result,
        symbol="000001",
        market_date=DATE,
        daily_volume=2500,
        daily_close=11,
        traded=True,
    )


def test_duplicate_auction_is_not_counted_twice_in_candidate() -> None:
    result = normalized_candidate(
        json.dumps(payload()).encode(),
        symbol="000001.SZ",
        market_date=DATE,
        daily_volume=2400,
        daily_close=11,
        first_5m_volume=50,
        raw_ref="quarantine/raw/000001.json.gz",
    )
    assert result.auction_provenance == "duplicate_0930_excluded"
    assert result.rows[0]["volume"] == 10
    assert sum(row["volume"] for row in result.rows) == 2400


def test_interval_start_labels_are_shifted_and_non_session_observations_excluded() -> None:
    value = start_labeled_payload()
    result = check(value, daily=2400, first5=50)
    assert result["time_label_mode"] == "interval_start_shifted_plus_1m"
    assert result["auction_mode"] == "source_interval_start_shifted_plus_1m"
    assert result["session_rows"] == 240
    assert result["excluded_observation_rows"] == 9
    candidate = normalized_candidate(
        json.dumps(value).encode(),
        symbol="000001.SZ",
        market_date=DATE,
        daily_volume=2400,
        daily_close=11,
        first_5m_volume=50,
        raw_ref="quarantine/raw/000001.json.gz",
    )
    assert candidate.rows[0]["ts"] == f"{DATE} 09:31:00"
    assert candidate.rows[-1]["ts"] == f"{DATE} 15:00:00"
    assert sum(row["volume"] for row in candidate.rows) == 2400


def test_interval_start_mode_still_blocks_daily_mismatch() -> None:
    with pytest.raises(ValueError, match="interval-start daily volume mismatch"):
        check(start_labeled_payload(), daily=2399, first5=50)


def test_unknown_observation_time_remains_fail_closed() -> None:
    value = start_labeled_payload()
    value["data"]["items"].append(["000001.SZ", f"{DATE} 16:00:00", "1MIN", 11, 11, 11, 11, 1, 11])
    value["count"] += 1
    with pytest.raises(ValueError, match="out-of-session"):
        check(value, daily=2400, first5=50)


def test_two_exact_but_different_time_label_interpretations_block() -> None:
    value = start_labeled_payload()
    fields = value["data"]["fields"]
    by_time = {row[1][11:16]: row for row in value["data"]["items"]}
    # Both interpretations total 2,410 shares: the end-labelled path uses the
    # two canonical boundary observations plus 09:30, while the start-labelled
    # path uses 09:30 and 13:00. Their selected minute rows are not identical.
    by_time["13:00"][fields.index("vol")] = 20
    by_time["13:00"][fields.index("amount")] = 220
    by_time["11:30"][fields.index("vol")] = 10
    by_time["11:30"][fields.index("amount")] = 110
    by_time["15:00"][fields.index("vol")] = 10
    by_time["15:00"][fields.index("amount")] = 110
    with pytest.raises(ValueError, match="ambiguous proxy minute time-label"):
        check(value, daily=2410, first5=50)

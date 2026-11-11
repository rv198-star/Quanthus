from __future__ import annotations

import hashlib
import json

import pytest

from quanthus.providers.tencent_m1_normalize import expected_times
from quanthus.providers.tencent_native_m5_witness import (
    UPSTREAM_REVISION,
    source_native_m5_support,
)

DAY = "2026-09-24"


def _encode(code: str, period: str, rows: list[list]) -> bytes:
    symbol = ("sh" if code.startswith("6") else "sz") + code
    return json.dumps(
        {"code": 0, "msg": "", "data": {symbol: {period: rows}}},
        separators=(",", ":"),
    ).encode()


def _fixture(
    code: str = "000726",
    *,
    missing: str | None = "202609241013",
    auction: bool = True,
) -> dict:
    minute = []
    grid = expected_times(DAY)
    bucket_counts = {grid[i]: 0 for i in range(4, 240, 5)}
    for i, stamp in enumerate(grid):
        raw_vol = "0.00" if stamp == missing else "1.00"
        if stamp != missing:
            minute.append([stamp, "10", "10", "10", "10", raw_vol, {}, "0"])
        bucket_counts[grid[(i // 5) * 5 + 4]] += int(float(raw_vol))
    if auction:
        minute.append(["202609240930", "10", "10", "10", "10", "2.00", {}, "0"])
        bucket_counts[grid[4]] += 2
    bars = [
        [stamp, "10", "10", "10", "10", str(vol), {}, "0"] for stamp, vol in bucket_counts.items()
    ]
    minute = sorted(minute)
    shares_per_unit = 1 if code.startswith(("688", "689")) else 100
    return {
        "stock_code": code,
        "market_date": DAY,
        "m1_raw": _encode(code, "m1", minute),
        "m5_raw": _encode(code, "m5", bars),
        "sealed_daily_shares": sum(bucket_counts.values()) * shares_per_unit,
        "sealed_daily_close": "10",
    }


def _audit(fixture: dict) -> dict:
    kwargs = dict(fixture)
    kwargs["m1_sha256"] = hashlib.sha256(kwargs["m1_raw"]).hexdigest()
    kwargs["m5_sha256"] = hashlib.sha256(kwargs["m5_raw"]).hexdigest()
    return source_native_m5_support(**kwargs)


def test_sparse_zero_is_witnessed_but_formal_1m_stays_unqualified() -> None:
    result = _audit(_fixture())
    assert result["upstream_revision"] == UPSTREAM_REVISION
    assert result["m1_observed_regular_minutes"] == 239
    assert result["m1_missing_actual_source_minutes"] == ["202609241013"]
    assert result["m1_auction_observed"] is True
    assert result["volume_multiplier_to_shares"] == 100
    assert result["m5_observed_buckets"] == 48
    assert result["daily_share_volume_matches_both"] is True
    assert result["same_source_zero_volume_hypothesis_supported"] is True
    assert result["cross_frequency_volume_mismatches"] == []
    assert result["inferred_or_materialized_m1_rows"] == 0
    assert result["formal_m1_qualified"] is False
    assert result["ods_ready_authorized"] is False
    assert result["dwd_publication_authorized"] is False


def test_star_volume_unit_is_shares_and_real_grid_not_inferred() -> None:
    result = _audit(_fixture("688001", missing=None, auction=False))
    assert result["volume_multiplier_to_shares"] == 1
    assert result["sealed_daily_shares"] == "240"
    assert result["m1_observed_regular_minutes"] == 240
    assert result["m1_missing_actual_source_minutes"] == []
    assert result["same_source_zero_volume_hypothesis_supported"] is False
    assert result["formal_m1_qualified"] is False


def test_m1_actual_positive_volume_cannot_be_replaced_with_zero() -> None:
    data = _fixture()
    obj = json.loads(data["m1_raw"])
    source = obj["data"]["sz000726"]["m1"]
    # A source bar with real volume is absent from the observed 1m raw.
    source.remove(next(v for v in source if v[0] == "202609241012"))
    data["m1_raw"] = json.dumps(obj, separators=(",", ":")).encode()
    result = _audit(data)
    assert len(result["m1_missing_actual_source_minutes"]) == 2
    assert len(result["cross_frequency_volume_mismatches"]) == 1
    assert result["same_source_zero_volume_hypothesis_supported"] is False


def test_native_m5_delta_can_cancel_at_daily_level_but_fails_bucket_witness() -> None:
    data = _fixture()
    payload = json.loads(data["m5_raw"])
    rows = payload["data"]["sz000726"]["m5"]
    rows[1][5] = str(float(rows[1][5]) + 1)
    rows[2][5] = str(float(rows[2][5]) - 1)
    data["m5_raw"] = json.dumps(payload, separators=(",", ":")).encode()
    result = _audit(data)
    assert result["daily_share_volume_matches_both"] is True
    assert len(result["cross_frequency_volume_mismatches"]) == 2
    assert result["same_source_zero_volume_hypothesis_supported"] is False


def test_invalid_sha_or_missing_native_five_minute_bars_fail_closed() -> None:
    data = _fixture()
    with pytest.raises(ValueError, match="SHA-256"):
        source_native_m5_support(
            **data,
            m1_sha256="0" * 64,
            m5_sha256=hashlib.sha256(data["m5_raw"]).hexdigest(),
        )
    payload = json.loads(data["m5_raw"])
    payload["data"]["sz000726"]["m5"].pop()
    data["m5_raw"] = json.dumps(payload).encode()
    with pytest.raises(ValueError, match="48"):
        _audit(data)


def test_duplicate_source_timestamp_and_nonfinite_price_fail_closed() -> None:
    data = _fixture()
    payload = json.loads(data["m1_raw"])
    bars = payload["data"]["sz000726"]["m1"]
    bars.append(bars[0])
    data["m1_raw"] = json.dumps(payload).encode()
    with pytest.raises(ValueError, match="duplicate"):
        _audit(data)
    data = _fixture()
    payload = json.loads(data["m5_raw"])
    payload["data"]["sz000726"]["m5"][0][1] = "NaN"
    data["m5_raw"] = json.dumps(payload).encode()
    with pytest.raises(ValueError, match="nonfinite"):
        _audit(data)


def test_wrong_market_code_date_and_source_provider_fail_closed() -> None:
    data = _fixture()
    with pytest.raises(ValueError, match="six-digit"):
        _audit({**data, "stock_code": "510300"})
    with pytest.raises(ValueError, match="date"):
        _audit({**data, "market_date": "2026-09-99"})
    payload = json.loads(data["m5_raw"])
    payload["data"]["sz000726"].pop("m5")
    data["m5_raw"] = json.dumps(payload).encode()
    with pytest.raises(ValueError, match="frequency"):
        _audit(data)


def test_mismatched_daily_share_or_close_never_supports_hypothesis() -> None:
    data = _fixture()
    result = _audit({**data, "sealed_daily_shares": data["sealed_daily_shares"] + 1})
    assert result["same_source_zero_volume_hypothesis_supported"] is False
    assert result["daily_share_volume_matches_both"] is False
    result = _audit({**data, "sealed_daily_close": "10.01"})
    assert result["native_5m_close_matches_sealed"] is False
    assert result["same_source_zero_volume_hypothesis_supported"] is False


def test_tolerates_other_trading_date_rows_but_not_missing_target_date() -> None:
    data = _fixture()
    payload = json.loads(data["m5_raw"])
    payload["data"]["sz000726"]["m5"].insert(
        0, ["202609230935", "10", "10", "10", "10", "1", {}, "0"]
    )
    data["m5_raw"] = json.dumps(payload).encode()
    assert _audit(data)["m5_observed_buckets"] == 48
    payload["data"]["sz000726"]["m5"] = [
        item for item in payload["data"]["sz000726"]["m5"] if not item[0].startswith("20260924")
    ]
    data["m5_raw"] = json.dumps(payload).encode()
    with pytest.raises(ValueError, match="no source observations"):
        _audit(data)

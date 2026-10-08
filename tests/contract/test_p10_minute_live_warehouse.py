"""P10 ongoing-date 1m source truth, admission and quarantine invariants."""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.pipelines import p10_minute_live_warehouse as live
from quanthus.providers.tencent_m1_normalize import expected_times

DAY = "2026-10-09"
SHA = "sha256:" + "a" * 64


def native(
    code: str = "000001", *,
    missing: str | None = None,
    more_other_date: bool = False,
) -> bytes:
    alias = ("sh" if code.startswith("6") else "sz") + code
    stamps = [DAY.replace("-", "") + "0930", *expected_times(DAY)]
    if missing:
        stamps.remove(missing)
    bars = [[s, "10", "10", "10", "10", "1", {}] for s in stamps]
    if more_other_date:
        bars.insert(0, ["202610081500", "8", "9", "9", "8", "1", {}])
    return json.dumps({"code": 0, "data": {alias: {"m1": bars}}}).encode()


def test_r0_full_observed_exact_native_source() -> None:
    raw = native()
    status, rows = live.assess_native(
        code="000001", day=DAY, raw=raw,
        traded=True, daily_shares=24100, daily_close=10,
    )
    assert status["quality_grade"] == "R0"
    assert status["default_research_admitted"] is True
    assert status["missing_actual_minutes"] == []
    assert len(rows) == 241


def test_r2_incomplete_actual_rows_admitted_but_not_research_ready() -> None:
    gap = expected_times(DAY)[45]
    raw = native(missing=gap)
    status, rows = live.assess_native(
        code="000001", day=DAY, raw=raw,
        traded=True, daily_shares=24000, daily_close=10,
    )
    assert status["quality_grade"] == "R2"
    assert status["formally_ingested"] is True
    assert status["default_research_admitted"] is False
    assert status["missing_actual_minutes"] == [gap]
    assert gap not in rows
    assert len(rows) == 240


def test_r3_minute_to_daily_volume_mismatch_forbidden_formal_storage() -> None:
    status, rows = live.assess_native(
        code="000001", day=DAY, raw=native(),
        traded=True, daily_shares=24101, daily_close=10,
    )
    assert status["quality_grade"] == "R3"
    assert status["formally_ingested"] is False
    assert status["quality_reason"] == "minute_daily_volume_mismatch"
    assert rows == {}


def test_incorrect_original_close_fails_closed() -> None:
    status, rows = live.assess_native(
        code="000001", day=DAY, raw=native(),
        traded=True, daily_shares=24100, daily_close=10.09,
    )
    assert status["quality_grade"] == "R3" and rows == {}


def test_old_day_rolling_original_cannot_be_falsely_counted_as_today() -> None:
    rows = live.native_minutes(native(more_other_date=True), "000001", DAY)
    assert len(rows) == 241
    assert all(stamp.startswith("20261009") for stamp in rows)


def test_no_trade_has_metadata_only() -> None:
    status, rows = live.assess_native(
        code="000001", day=DAY, raw=None,
        traded=False, daily_shares=0, daily_close=10,
    )
    assert status["quality_grade"] == "SUSPENDED"
    assert not status["formally_ingested"] and rows == {}


def test_wrong_source_symbol_rejected() -> None:
    status, rows = live.assess_native(
        code="600000", day=DAY, raw=native("000001"),
        traded=True, daily_shares=24100, daily_close=10,
    )
    assert status["quality_grade"] == "R3"
    assert rows == {}


def test_duplicate_original_timestamp_is_invalid() -> None:
    obj = json.loads(native())
    obj["data"]["sz000001"]["m1"].append(obj["data"]["sz000001"]["m1"][-1])
    status, rows = live.assess_native(
        code="000001", day=DAY, raw=json.dumps(obj).encode(),
        traded=True, daily_shares=24200, daily_close=10,
    )
    assert status["quality_grade"] == "R3"
    assert rows == {}


def test_frozen_dates_not_allowed_in_new_dynamic_product() -> None:
    with pytest.raises(ValueError, match="outside successor"):
        live.native_minutes(native(), "000001", "2026-10-08")


def _save_source(root: Path, code: str, raw: bytes) -> None:
    shard = root / "shard-000"
    (shard / "raw").mkdir(parents=True, exist_ok=True)
    (shard / "receipts").mkdir(parents=True, exist_ok=True)
    (shard / "identity.json").write_text(json.dumps({
        "kind": live.SOURCE_KIND,
        "market_date": DAY,
        "market_ods_manifest_sha256": SHA,
        "selected_symbols": [code],
    }))
    (shard / "raw" / f"{code}.json.gz").write_bytes(gzip.compress(raw))
    (shard / "receipts" / f"{code}.json").write_text(json.dumps({
        "stock_code": code, "market_date": DAY,
        "status": "raw_saved",
        "source_market_ods_sha256": SHA,
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "captured_at": "2026-10-09T14:00:00+00:00",
    }))


def test_real_parquet_only_contains_source_valid_r2_never_r3(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    codes = [f"{i:06}" for i in range(4992)]
    daily = {code: {"volume": Decimal(1), "close": Decimal(10)} for code in codes}
    traded = {code: "1" for code in codes}
    daily["000001"]["volume"] = Decimal(24000)
    raw = native(missing=expected_times(DAY)[45])
    _save_source(tmp_path / "capture", "000001", raw)
    monkeypatch.setattr(
        live, "_reference",
        lambda path, day: (daily, SHA, traded),
    )
    result = live.build(
        day=DAY, daily_release=tmp_path / "fake-ods",
        capture_root=tmp_path / "capture",
        product_root=tmp_path / "product",
    )
    assert result["quality_grades"]["R2"] == 1
    assert result["quality_grades"]["R3"] == 4991
    assert result["grade_rows"]["R2"] == 240
    pkg = next((tmp_path / "product" / "packages" / DAY).iterdir())
    assert (pkg / "ods" / "raw" / "000001.json.gz").exists()
    assert not (pkg / "ods" / "raw" / "000002.json.gz").exists()
    assert not result["research_ready"]
    assert live.verify(pkg)["R3_rejected"] == 4991


def test_tampering_formal_data_fails_manifest_sha(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    codes = [f"{i:06}" for i in range(4992)]
    daily = {code: {"volume": Decimal(1), "close": Decimal(10)} for code in codes}
    daily["000001"]["volume"] = Decimal(24000)
    _save_source(tmp_path / "capture", "000001", native(missing=expected_times(DAY)[20]))
    monkeypatch.setattr(
        live, "_reference",
        lambda path, day: (daily, SHA, {c:"1" for c in codes}),
    )
    live.build(
        day=DAY,daily_release=tmp_path,
        capture_root=tmp_path/"capture",product_root=tmp_path/"product",
    )
    pkg=next((tmp_path/"product"/"packages"/DAY).iterdir())
    (pkg/"ods"/"raw"/"000001.json.gz").write_bytes(b"tampered")
    with pytest.raises(ValueError,match="checksum"):
        live.verify(pkg)

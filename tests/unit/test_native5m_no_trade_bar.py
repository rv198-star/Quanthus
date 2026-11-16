"""Contract tests for source-observed no-trade 5m bars (#692).

A genuine no-trade window (unmatched opening auction, no first-window trade)
is reported by the supplier as an all-zero row. The exact all-zero pattern is
admitted as source-observed; every other zero/negative pattern stays fail-closed.
"""

from __future__ import annotations

import gzip
import json
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse import daily_base24_native_rebind as binding
from quanthus.pipelines.securities_warehouse import daily_base24_precision as precision
from quanthus.pipelines.securities_warehouse import native5m_daily_source as native
from tests.unit.test_daily_base24_native_rebind import inputs, reseal, verify
from tests.unit.test_daily_base24_precision import DAY, REV

CODE = "300462"
VENDOR = "sz." + CODE


def rows_with_zero_bar(zero_index: int = 0, *, close: str = "10.19") -> list[list[str]]:
    rows = []
    for i, label in enumerate(native.m5_grid(DAY)):
        if i == zero_index:
            values = ["0.0000"] * 4 + ["0", "0.0000"]
        elif i == 47:
            values = ["10.10", "10.20", "10.03", close, "300", "3057.0000"]
        else:
            values = ["10.20", "10.20", "10.20", "10.20", "200", "2040.0000"]
        rows.append([DAY, label + "00000", VENDOR, *values])
    return rows


def encode_raw(rows: list[list[str]]) -> bytes:
    return (
        native.canonical(
            dict(
                provider=native.PROVIDER,
                frequency="5",
                adjustflag="3",
                code=VENDOR,
                market_date=DAY,
                fields=native.FIELDS.split(","),
                rows=rows,
            )
        )
        + b"\n"
    )


def daily_row(volume: str = "9500", close: str = "10.19") -> dict:
    return dict(tradestatus="1", volume=volume, close=close)


def test_genuine_no_trade_opening_bar_is_kept_exact_and_counted():
    raw = encode_raw(rows_with_zero_bar(0))
    bars = native.native_m5(raw, CODE, DAY)
    assert len(bars) == 48
    first = bars[native.m5_grid(DAY)[0]]
    assert native.is_no_trade_bar(first)
    assert all(value == 0 for value in first.values())
    assert not any(native.is_no_trade_bar(bar) for bar in list(bars.values())[1:])


def test_no_trade_bar_is_admitted_in_any_session_window():
    raw = encode_raw(rows_with_zero_bar(20))
    bars = native.native_m5(raw, CODE, DAY)
    assert len(bars) == 48
    assert native.is_no_trade_bar(bars[native.m5_grid(DAY)[20]])


@pytest.mark.parametrize(
    "values",
    [
        ["0.0000", "10.20", "10.20", "10.20", "200", "2040.0000"],
        ["10.20", "10.20", "10.20", "0.0000", "200", "2040.0000"],
        ["0.0000", "0.0000", "0.0000", "0.0000", "200", "0.0000"],
        ["0.0000", "0.0000", "0.0000", "0.0000", "0", "2040.0000"],
        ["10.20", "10.20", "10.20", "10.20", "-200", "2040.0000"],
        ["-10.20", "10.20", "10.20", "10.20", "200", "2040.0000"],
        ["0.0000", "10.20", "10.20", "10.20", "0", "0.0000"],
    ],
)
def test_any_other_zero_or_negative_pattern_stays_fail_closed(values):
    rows = rows_with_zero_bar(0)
    rows[1] = [DAY, native.m5_grid(DAY)[1] + "00000", VENDOR, *values]
    with pytest.raises(ValueError):
        native.native_m5(encode_raw(rows), CODE, DAY)


def test_no_trade_opening_qualifies_r0_with_explicit_audit():
    raw = encode_raw(rows_with_zero_bar(0))
    quality, bars = binding.qualify(raw, CODE, DAY, daily_row())
    assert quality["quality_grade"] == "R0"
    assert quality["quality_reason"] == "native_48_exact_daily"
    assert quality["missing_actual_minutes"] == []
    assert quality["no_trade_minutes"] == [native.m5_grid(DAY)[0]]
    assert quality["formally_ingested"] is True
    assert len(bars) == 48
    assert sum(bar["volume_shares"] for bar in bars.values()) == Decimal("9500")


def test_no_trade_minutes_empty_for_fully_traded_day():
    rows = rows_with_zero_bar(0)
    rows[0] = [
        DAY,
        native.m5_grid(DAY)[0] + "00000",
        VENDOR,
        "10.20",
        "10.20",
        "10.20",
        "10.20",
        "200",
        "2040.0000",
    ]
    quality, _ = binding.qualify(encode_raw(rows), CODE, DAY, daily_row(volume="9700"))
    assert quality["quality_grade"] == "R0"
    assert quality["no_trade_minutes"] == []


def test_no_trade_terminal_bar_stays_r3_terminal_close():
    raw = encode_raw(rows_with_zero_bar(47))
    quality, bars = binding.qualify(raw, CODE, DAY, daily_row())
    assert quality["quality_grade"] == "R3"
    assert quality["quality_reason"] == "missing_or_wrong_terminal_close"
    assert not bars


def test_no_trade_bar_does_not_relax_exact_share_gate():
    raw = encode_raw(rows_with_zero_bar(0))
    quality, bars = binding.qualify(raw, CODE, DAY, daily_row(volume="9600"))
    assert quality["quality_grade"] == "R3"
    assert quality["quality_reason"] == "minute_daily_volume_mismatch"
    assert not bars


def test_fabricated_zero_price_with_volume_stays_invalid_source():
    rows = rows_with_zero_bar(0)
    rows[5] = [
        DAY,
        native.m5_grid(DAY)[5] + "00000",
        VENDOR,
        "10.20",
        "10.20",
        "10.20",
        "0.0000",
        "200",
        "2040.0000",
    ]
    quality, bars = binding.qualify(encode_raw(rows), CODE, DAY, daily_row())
    assert quality["quality_grade"] == "R3"
    assert quality["quality_reason"] == "invalid_native_5m_source"
    assert quality["no_trade_minutes"] == []
    assert not bars


def test_suspended_all_zero_raw_stays_suspended():
    rows = [
        [DAY, label + "00000", VENDOR, "0.0000", "0.0000", "0.0000", "0.0000", "0", "0.0000"]
        for label in native.m5_grid(DAY)
    ]
    quality, bars = binding.qualify(
        encode_raw(rows), CODE, DAY, dict(tradestatus="0", volume="0", close=None)
    )
    assert quality["quality_grade"] == "SUSPENDED"
    assert quality["quality_reason"] == "sealed_daily_no_trade"
    assert not bars


def test_rebind_build_and_full_replay_with_no_trade_bar(tmp_path):
    market, reference, original, daily, ref = inputs(tmp_path)
    # Replace 600001's capture with a genuine no-trade opening bar; keep the
    # exact daily share total by lowering the terminal bar volume.
    grid = native.m5_grid(DAY)
    rows = []
    for i, label in enumerate(grid):
        if i == 0:
            values = ["0.0000"] * 4 + ["0", "0.0000"]
        elif i == 47:
            values = ["12.50", "12.50", "12.50", "12.50", "800", "10000.0000"]
        else:
            values = ["12.50", "12.50", "12.50", "12.50", "200", "2500.0000"]
        rows.append([DAY, label + "00000", "sh.600001", *values])
    raw = (
        native.canonical(
            dict(
                provider=native.PROVIDER,
                frequency="5",
                adjustflag="3",
                code="sh.600001",
                market_date=DAY,
                fields=native.FIELDS.split(","),
                rows=rows,
            )
        )
        + b"\n"
    )
    p = Path(original["600001"]["path"])
    p.write_bytes(gzip.compress(raw, mtime=0))
    original["600001"].update(
        compressed_sha256=precision.file_sha(p),
        raw_sha256=precision._sha(raw),
    )
    original["600001"]["evidence"]["raw_sha256"] = precision._sha(raw)
    output = tmp_path / "native-no-trade"
    result = binding.build(
        market=market, reference=reference, originals=original, output=output, revision=REV
    )
    assert result["grades"] == {"R0": 2} and result["rows_per_grade"] == {"R0": 96}
    manifest = json.loads((output / "manifest.json").read_bytes())
    assert manifest["processing_revision"] == binding.CONTRACT
    statuses = {
        json.loads(line)["stock_code"]: json.loads(line)
        for line in (output / "quality-status.jsonl").read_text().splitlines()
    }
    assert statuses["600001"]["no_trade_minutes"] == [grid[0]]
    assert statuses["600000"]["no_trade_minutes"] == []
    assert verify(output, daily, ref)["rows"] == 96


def test_v1_rebind_contract_products_still_verify(tmp_path):
    market, reference, original, daily, ref = inputs(tmp_path)
    output = tmp_path / "native-v1"
    binding.build(
        market=market, reference=reference, originals=original, output=output, revision=REV
    )
    manifest = json.loads((output / "manifest.json").read_bytes())
    manifest["processing_revision"] = binding.CONTRACT_V1
    (output / "manifest.json").write_bytes(native.canonical(manifest) + b"\n")
    origins = json.loads((output / "evidence/original-bindings.json").read_bytes())
    origins["kind"] = binding.CONTRACT_V1
    (output / "evidence/original-bindings.json").write_bytes(native.canonical(origins) + b"\n")
    reseal(output)
    assert verify(output, daily, ref)["rows"] == 96

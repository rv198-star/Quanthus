"""Collector contract tests for source-observed no-trade 5m bars (#692).

Mirrors the pipeline-side rule in securities_warehouse/native5m_daily_source.py:
only the exact all-zero row is a source-observed no-trade bar; every other
zero/negative pattern stays fail-closed. The 1m lane is unchanged.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from quanthus.pipelines import p10_native_minute_delivery as delivery

DAY = "2026-10-08"
CODE = "300462"
VENDOR = "sz." + CODE


def rows_with_zero_bar(zero_index: int = 0) -> list[list[str]]:
    rows = []
    for i, label in enumerate(delivery.m5_grid(DAY)):
        if i == zero_index:
            values = ["0.0000"] * 4 + ["0", "0.0000"]
        elif i == 47:
            values = ["10.10", "10.20", "10.03", "10.19", "300", "3057.0000"]
        else:
            values = ["10.20", "10.20", "10.20", "10.20", "200", "2040.0000"]
        rows.append([DAY, label + "00000", VENDOR, *values])
    return rows


def encode_raw(rows: list[list[str]]) -> bytes:
    return (
        json.dumps(
            dict(
                provider=delivery.PROVIDER,
                frequency="5",
                adjustflag="3",
                code=VENDOR,
                market_date=DAY,
                fields=delivery.FIELDS.split(","),
                rows=rows,
            ),
            sort_keys=True,
        ).encode()
        + b"\n"
    )


def daily_row(volume: str = "9500", close: str = "10.19") -> dict:
    return dict(tradestatus="1", volume=volume, close=close)


def test_genuine_no_trade_opening_bar_is_kept_exact_and_counted():
    bars = delivery.native_m5(encode_raw(rows_with_zero_bar(0)), CODE, DAY)
    assert len(bars) == 48
    first = bars[delivery.m5_grid(DAY)[0]]
    assert delivery.is_no_trade_bar(first)
    assert all(value == 0 for value in first.values())


def test_no_trade_bar_is_admitted_in_any_session_window():
    bars = delivery.native_m5(encode_raw(rows_with_zero_bar(20)), CODE, DAY)
    assert len(bars) == 48
    assert delivery.is_no_trade_bar(bars[delivery.m5_grid(DAY)[20]])


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
    rows[1] = [DAY, delivery.m5_grid(DAY)[1] + "00000", VENDOR, *values]
    with pytest.raises(ValueError):
        delivery.native_m5(encode_raw(rows), CODE, DAY)


def test_no_trade_opening_qualifies_r0_with_explicit_audit():
    quality, bars = delivery.assess(
        interval="5m", code=CODE, day=DAY, raw=encode_raw(rows_with_zero_bar(0)), daily=daily_row()
    )
    assert quality["quality_grade"] == "R0"
    assert quality["quality_reason"] == "native_48_exact_daily"
    assert quality["missing_actual_minutes"] == []
    assert quality["no_trade_minutes"] == [delivery.m5_grid(DAY)[0]]
    assert len(bars) == 48
    assert sum(bar["volume_shares"] for bar in bars.values()) == Decimal("9500")


def test_no_trade_minutes_empty_for_fully_traded_day():
    rows = rows_with_zero_bar(0)
    rows[0] = [
        DAY,
        delivery.m5_grid(DAY)[0] + "00000",
        VENDOR,
        "10.20",
        "10.20",
        "10.20",
        "10.20",
        "200",
        "2040.0000",
    ]
    quality, _ = delivery.assess(
        interval="5m", code=CODE, day=DAY, raw=encode_raw(rows), daily=daily_row(volume="9700")
    )
    assert quality["quality_grade"] == "R0"
    assert quality["no_trade_minutes"] == []


def test_no_trade_terminal_bar_stays_r3_terminal_close():
    quality, bars = delivery.assess(
        interval="5m", code=CODE, day=DAY, raw=encode_raw(rows_with_zero_bar(47)), daily=daily_row()
    )
    assert quality["quality_grade"] == "R3"
    assert quality["quality_reason"] == "missing_or_wrong_terminal_close"
    assert not bars


def test_no_trade_bar_does_not_relax_exact_share_gate():
    quality, bars = delivery.assess(
        interval="5m",
        code=CODE,
        day=DAY,
        raw=encode_raw(rows_with_zero_bar(0)),
        daily=daily_row(volume="9600"),
    )
    assert quality["quality_grade"] == "R3"
    assert quality["quality_reason"] == "minute_daily_volume_mismatch"
    assert not bars


def test_fabricated_zero_price_with_volume_stays_invalid_source():
    rows = rows_with_zero_bar(0)
    rows[5] = [
        DAY,
        delivery.m5_grid(DAY)[5] + "00000",
        VENDOR,
        "10.20",
        "10.20",
        "10.20",
        "0.0000",
        "200",
        "2040.0000",
    ]
    quality, bars = delivery.assess(
        interval="5m", code=CODE, day=DAY, raw=encode_raw(rows), daily=daily_row()
    )
    assert quality["quality_grade"] == "R3"
    assert quality["quality_reason"] == "invalid_native_5m_source"
    assert quality["no_trade_minutes"] == []
    assert not bars


def test_suspended_all_zero_raw_stays_suspended():
    rows = [
        [DAY, label + "00000", VENDOR, "0.0000", "0.0000", "0.0000", "0.0000", "0", "0.0000"]
        for label in delivery.m5_grid(DAY)
    ]
    quality, bars = delivery.assess(
        interval="5m",
        code=CODE,
        day=DAY,
        raw=encode_raw(rows),
        daily=dict(tradestatus="0", volume="0", close=None),
    )
    assert quality["quality_grade"] == "SUSPENDED"
    assert quality["quality_reason"] == "sealed_daily_no_trade"
    assert not bars

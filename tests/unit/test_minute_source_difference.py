"""Numeric diagnostic partitions do not change exact source arbitration."""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from quanthus.providers.minute_source_difference import describe_pair
from quanthus.providers.minute_source_selection import (
    MinuteCandidate,
    choose_whole_security,
    qualify_candidate,
)
from quanthus.providers.tencent_m1_normalize import expected_times

DATE = "2026-09-24"


def pair():
    rows = tuple(
        {
            "stock_code": "000001",
            "ts": f"{DATE} {stamp[-4:-2]}:{stamp[-2:]}:00",
            "open": "11",
            "high": "11",
            "low": "11",
            "close": "11",
            "volume": 110 if i == 0 else 10,
            "amount": None,
        }
        for i, stamp in enumerate(expected_times(DATE))
    )
    left = MinuteCandidate("left", "0" * 64, "left-raw", rows, "explicit", "null")
    right = replace(left, provider="right", raw_ref="right-raw", rows=copy.deepcopy(rows))
    return left, right


def gate(left, right):
    return choose_whole_security(
        (left, right),
        symbol="000001",
        market_date=DATE,
        daily_volume=2500,
        daily_close=11,
        traded=True,
        priority=("left", "right"),
    )


def test_equal_numeric_representations_and_null_amount_are_not_new_differences():
    left, right = pair()
    right.rows[0]["high"] = 11.0
    original = copy.deepcopy((left, right))
    detail = describe_pair(left, right)
    assert detail["category"] == "exact_numeric_equal"
    assert not detail["field_difference_counts"]
    assert detail["compared_rows"] == 240
    assert not detail["gate_changed"] and not detail["publication_authorized"]
    assert (left, right) == original
    assert gate(left, right).status == "qualified_whole_security_candidate"


@pytest.mark.parametrize(
    "high,category",
    [
        (11.000000000000002, "only_price_deltas_below_diagnostic_floor"),
        ("11.0000000001", "other_price_or_volume_differences"),
        ("11.01", "other_price_or_volume_differences"),
        ("11.28", "other_price_or_volume_differences"),
    ],
)
def test_small_floor_boundary_and_true_price_differences_still_block(high, category):
    left, right = pair()
    right.rows[60]["high"] = high
    detail = describe_pair(left, right)
    assert detail["category"] == category
    assert detail["differing_minutes"] == 1
    assert detail["field_difference_counts"] == {"high": 1}
    assert detail["cause"] == "not_determined_by_numeric_description"
    assert right.rows[60]["high"] == high
    assert gate(left, right).status == "blocked_cross_source_conflict"
    for candidate in (left, right):
        assert qualify_candidate(
            candidate,
            symbol="000001",
            market_date=DATE,
            daily_volume=2500,
            daily_close=11,
            traded=True,
        )


def test_equal_daily_shares_do_not_erase_minute_volume_distribution_differences():
    left, right = pair()
    right.rows[60]["volume"] -= 1
    right.rows[61]["volume"] += 1
    detail = describe_pair(left, right)
    assert detail["volume_difference_minutes"] == 2
    assert detail["max_abs_share_difference"] == "1"
    assert detail["field_difference_counts"] == {"volume": 2}
    assert detail["category"] == "other_price_or_volume_differences"
    assert gate(left, right).status == "blocked_cross_source_conflict"


def test_mixed_small_and_price_volume_differences_samples_are_bounded():
    left, right = pair()
    for i in range(10):
        right.rows[i]["high"] = 11.000000000000002
    right.rows[10]["high"] = 11.01
    detail = describe_pair(left, right)
    assert detail["small_price_difference_counts"] == {"high": 10}
    assert detail["larger_price_difference_counts"] == {"high": 1}
    assert len(detail["first_three_differing_minutes"]) == 3
    assert detail["category"] == "other_price_or_volume_differences"


@pytest.mark.parametrize("fault", ["length", "date", "code", "nan", "bool", "missing"])
def test_unknown_or_misaligned_input_cannot_be_repaired_by_description(fault):
    left, right = pair()
    if fault == "length":
        right = replace(right, rows=right.rows[:-1])
    elif fault == "date":
        right.rows[0]["ts"] = "2026-09-23 09:31:00"
    elif fault == "code":
        right.rows[0]["stock_code"] = "000002"
    else:
        right.rows[0]["high"] = {"nan": float("nan"), "bool": True, "missing": None}[fault]
    with pytest.raises(ValueError):
        describe_pair(left, right)

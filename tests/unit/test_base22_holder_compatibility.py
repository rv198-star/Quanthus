from datetime import date

import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_holder_compatibility import (
    qualify_legacy_capture,
)


def fixture():
    old = {
        "code": "000001",
        "end_date": "2026-06-30",
        "source": "provider",
        "shareholder_count": 100.0,
        "avg_hold_num": 1.23456789012,
    }
    before = {
        "source_record_id": "stock_code=000001&period_end=2026-06-30",
        "period_end": date(2026, 6, 30),
        "record_source": "provider",
        "holder_count": 100,
        "raw_holder_count": format(100.0, ".17g"),
        "raw_average_holding": format(1.23456789012, ".17g"),
    }
    incoming = dict(old, shareholder_count="100", avg_hold_num="1.23456789012000001")
    return old, before, incoming


def test_real_decimal_difference_can_share_legacy_bits_without_old_precision_or_time_claim():
    old, before, incoming = fixture()
    frozen = (old.copy(), before.copy(), incoming.copy())
    result = qualify_legacy_capture(old, before, incoming)
    assert result["same_average_binary_bits"]
    assert result["old_provider_original_precision"] == "UNKNOWN"
    assert not result["overwrite_old_body"] and not result["knowledge_time_assigned"]
    assert (old, before, incoming) == frozen


@pytest.mark.parametrize(
    "field,value",
    [
        ("code", "000002"),
        ("end_date", "2026-03-31"),
        ("source", "other"),
        ("shareholder_count", "101"),
        ("shareholder_count", "100.1"),
        ("avg_hold_num", "1.23456789013"),
        ("avg_hold_num", "NaN"),
        ("avg_hold_num", "Infinity"),
        ("avg_hold_num", "1e99999"),
        ("avg_hold_num", 1.23456789012),
        ("avg_hold_num", "bad"),
    ],
)
def test_identity_counts_nonfinite_and_true_binary_changes_fail(field, value):
    old, before, incoming = fixture()
    incoming[field] = value
    with pytest.raises(BoundedError):
        qualify_legacy_capture(old, before, incoming)


@pytest.mark.parametrize(
    "field,value",
    [
        ("raw_holder_count", "100.0"),
        ("raw_average_holding", "1.2"),
        ("holder_count", 101),
        ("source_record_id", "other"),
        ("record_source", "other"),
        ("period_end", date(2025, 6, 30)),
    ],
)
def test_before_must_link_exactly_to_original_raw(field, value):
    old, before, incoming = fixture()
    before[field] = value
    with pytest.raises(BoundedError):
        qualify_legacy_capture(old, before, incoming)


def test_null_and_zero_remain_distinct_and_zero_sign_is_preserved():
    old, before, incoming = fixture()
    old["avg_hold_num"] = None
    before["raw_average_holding"] = None
    incoming["avg_hold_num"] = None
    assert qualify_legacy_capture(old, before, incoming)["same_average_binary_bits"]
    incoming["avg_hold_num"] = "0"
    with pytest.raises(BoundedError):
        qualify_legacy_capture(old, before, incoming)
    old["avg_hold_num"] = -0.0
    before["raw_average_holding"] = "-0"
    incoming["avg_hold_num"] = "-0"
    assert qualify_legacy_capture(old, before, incoming)["same_average_binary_bits"]
    incoming["avg_hold_num"] = "0"
    with pytest.raises(BoundedError):
        qualify_legacy_capture(old, before, incoming)

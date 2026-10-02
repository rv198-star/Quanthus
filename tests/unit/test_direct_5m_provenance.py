"""Formal 5m publication must have exact native-source receipts."""

import json

import pytest

from quanthus.pipelines import lane_runner


def _check(tmp_path, receipts):
    (tmp_path / "checkpoint.json").write_text(json.dumps({"symbols": receipts}), encoding="utf-8")
    return lane_runner._direct_5m_provenance_error(tmp_path, ["000001", "000002"])


def _native(provider="sina-minute-jsonp"):
    return {
        "status": "done",
        "provider": provider,
        "native_frequency": "5m",
        "derived_from": None,
    }


def test_native_exact_universe_passes(tmp_path):
    assert _check(tmp_path, {"000001": _native(), "000002": _native("baostock-native-5m")}) is None


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"provider": "tencent-minute-query"}, "source_nonconforming"),
        ({"provider": "eastmoney-push2delay-trends2"}, "source_nonconforming"),
        ({"native_frequency": "1m", "derived_from": "1m"}, "source_nonconforming"),
        ({"native_frequency": None}, "source_nonconforming"),
        ({"status": "error"}, "receipt_incomplete"),
    ],
)
def test_nonconforming_receipt_blocks(tmp_path, override, reason):
    assert reason in _check(tmp_path, {"000001": _native(), "000002": {**_native(), **override}})


def test_missing_or_extra_symbol_blocks(tmp_path):
    assert "universe_mismatch" in _check(tmp_path, {"000001": _native()})
    assert "universe_mismatch" in _check(
        tmp_path, {"000001": _native(), "000002": _native(), "000003": _native()}
    )


def test_unreadable_checkpoint_blocks(tmp_path):
    assert "checkpoint_unreadable" in lane_runner._direct_5m_provenance_error(tmp_path, ["000001"])

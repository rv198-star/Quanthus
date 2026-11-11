"""Historical Tencent rolling source must be validated by TARGET-DAY only."""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from quanthus.providers.p10_native_m1_shard_capture import (
    capture_shard,
    target_date_records,
)

DAY = date(2026, 10, 8)
SOURCE_MANIFEST_SHA = "sha256:" + "a" * 64


def _response(
    stock_code: str = "000001",
    *,
    old_day_price: str = "11.00",
    target_close: str = "12.00",
) -> bytes:
    symbol = ("sh" if stock_code.startswith("6") else "sz") + stock_code
    return json.dumps(
        {
            "code": 0,
            "data": {
                symbol: {
                    "m1": [
                        ["202609301500", "11", old_day_price, "11", "11", "1", {}],
                        ["202610080930", "12", "12", "12", "12", "2", {}],
                        ["202610080931", "12", target_close, "12", "12", "3", {}],
                    ]
                }
            },
        },
        separators=(",", ":"),
    ).encode()


def test_rolling_response_changed_but_prior_target_day_unchanged() -> None:
    old = _response(old_day_price="10.00")
    later = _response(old_day_price="10.50")
    assert hashlib.sha256(old).hexdigest() != hashlib.sha256(later).hexdigest()
    first, first_sha = target_date_records(old, "000001", DAY)
    second, second_sha = target_date_records(later, "000001", DAY)
    assert first == second
    assert first_sha == second_sha


def test_real_prior_target_day_revision_changes_slice_digest() -> None:
    first = _response()
    second = _response(target_close="12.01")
    _, first_sha = target_date_records(first, "000001", DAY)
    _, second_sha = target_date_records(second, "000001", DAY)
    assert first_sha != second_sha


def test_missing_source_symbol_and_duplicated_stamp_rejected() -> None:
    with pytest.raises(ValueError, match="missing exact"):
        target_date_records(_response("000002"), "000001", DAY)
    obj = json.loads(_response())
    obj["data"]["sz000001"]["m1"].append(
        obj["data"]["sz000001"]["m1"][-1]
    )
    with pytest.raises(ValueError, match="duplicated"):
        target_date_records(json.dumps(obj).encode(), "000001", DAY)


def test_one_authentic_shard_is_saved_and_never_published(tmp_path: Path) -> None:
    codes = ["000001", "300750", "600000"]
    observed: list[tuple[str, str]] = []

    def provider(code: str, anchor: str) -> bytes:
        observed.append((code, anchor))
        return _response(code)

    path = tmp_path / "source-attempt"
    result = capture_shard(
        codes=codes, market_date=DAY,
        market_manifest_sha=SOURCE_MANIFEST_SHA,
        output=path, shard_index=0, shard_size=2,
        fetcher=provider,
    )
    assert result["saved"] == 2 and result["errors"] == 0
    assert result["published"] is False
    assert observed == [("000001", "202610082359"), ("300750", "202610082359")]
    for code in codes[:2]:
        original = gzip.decompress((path / "raw" / f"{code}.json.gz").read_bytes())
        receipt = json.loads((path / "receipts" / f"{code}.json").read_text())
        assert hashlib.sha256(original).hexdigest() == receipt["raw_sha256"]
        assert receipt["native_target_rows"] == 2
    assert not (path / "READY").exists()


def test_source_replay_can_verify_but_not_mutate_original_bytes(tmp_path: Path) -> None:
    path = tmp_path / "source"
    kw = {
        "codes": ["000001"], "market_date": DAY,
        "market_manifest_sha": SOURCE_MANIFEST_SHA,
        "output": path, "shard_index": 0,
        "fetcher": lambda code, anchor: _response(code),
    }
    capture_shard(**kw)
    first = (path / "raw" / "000001.json.gz").read_bytes()
    # Replay must reuse prior original, not fetch a revised source into same path.
    kw["fetcher"] = lambda code, anchor: _response(code, target_close="99")
    again = capture_shard(**kw)
    assert again["already_captured"] == 1
    assert (path / "raw" / "000001.json.gz").read_bytes() == first


def test_tampered_source_receipt_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "source"
    kw = {
        "codes": ["000001"], "market_date": DAY,
        "market_manifest_sha": SOURCE_MANIFEST_SHA,
        "output": path, "shard_index": 0,
        "fetcher": lambda code, anchor: _response(code),
    }
    capture_shard(**kw)
    (path / "raw" / "000001.json.gz").write_bytes(gzip.compress(b"tampered"))
    with pytest.raises(ValueError, match="SHA differs"):
        capture_shard(**kw)


def test_capture_shard_bound_prevents_multi_hour_whole_market_operation(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="unsafe shard"):
        capture_shard(
            codes=["000001"], market_date=DAY,
            market_manifest_sha=SOURCE_MANIFEST_SHA,
            output=tmp_path / "oversized", shard_index=0,
            shard_size=4992, fetcher=lambda code, anchor: _response(code),
        )

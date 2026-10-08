"""Adversarial source-native loss-aware 1m grade tests; no market network."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from quanthus.providers.loss_aware_m1 import (
    _bucket_ends, _grid, assess, verify_native_m5,
)
from quanthus.providers.loss_aware_m1_capture import parse_source

DAY = "2026-09-24"
SYMBOL = "000001"


def _raw(*, period: str = "m1", missing: set[str] | None = None,
         missing_auction: bool = False, bad_source_code: str | None = None,
         bucket_5m_delta: dict[str, str] | None = None) -> bytes:
    missing = missing or set()
    mult = Decimal("100")
    date = DAY.replace("-", "")
    if period == "m1":
        stamps = ([date+"0930"] if not missing_auction else []) + list(_grid(DAY))
    else:
        stamps = list(_bucket_ends(DAY))
    rows = []
    for stamp in stamps:
        if stamp in missing:
            continue
        volume = Decimal(1) if period == "m1" else Decimal(5)
        if period == "m5" and stamp == date+"0935":
            volume = Decimal(6)
        if period == "m1" and stamp in missing:
            volume = Decimal(0)
        if bucket_5m_delta:
            volume += Decimal(bucket_5m_delta.get(stamp, "0"))
        rows.append([stamp, "10.00", "10.00", "10.00", "10.00", str(volume), {}])
    key = "sz"+(bad_source_code or SYMBOL)
    return json.dumps({"code":0, "data":{key:{period:rows}}}).encode()


def _sparse_raw(*, absent: set[str]) -> tuple[bytes, int]:
    rows=[]
    for index,stamp in enumerate([DAY.replace("-","")+"0930"]+list(_grid(DAY))):
        if stamp in absent:
            continue
        volume = "0" if stamp in {DAY.replace("-","")+"1023", DAY.replace("-","")+"1024"} else "1"
        rows.append([stamp, "10", "10", "10", "10", volume, {}])
    raw=json.dumps({"code":0,"data":{"sz"+SYMBOL:{"m1":rows}}}).encode()
    shares=sum(Decimal(row[5]) for row in rows)*100
    return raw, int(shares)


def _matching_m5_with_sparse(m1_raw: bytes) -> bytes:
    obj=json.loads(m1_raw)
    minutes={x[0]:Decimal(x[5]) for x in obj["data"]["sz"+SYMBOL]["m1"]}
    grid=list(_grid(DAY))
    ends=_bucket_ends(DAY)
    rows=[]
    for k, end in enumerate(ends):
        total=sum((minutes.get(x,Decimal(0)) for x in grid[k*5:(k+1)*5]),Decimal(0))
        if k==0:
            total+=minutes.get(DAY.replace("-","")+"0930",Decimal(0))
        rows.append([end,"10","10","10","10",str(total),{}])
    return json.dumps({"code":0,"data":{"sz"+SYMBOL:{"m5":rows}}}).encode()


def test_r0_native_complete_and_auction_separate() -> None:
    raw, total=_sparse_raw(absent=set())
    item=assess(symbol=SYMBOL,day=DAY,raw=raw,shares=total,close=10,traded=True,
                cross_source_status="known_semantic_conflict")
    assert item.grade=="R0"
    assert len(item.rows)==241
    assert not item.missing_stamps
    assert item.disposition()["strict_240_source_qualified"] is False
    assert item.disposition()["source_semantic_disagreement"]=="known_semantic_conflict"


def test_r1_requires_full_48_bucket_same_source_witness() -> None:
    raw, total=_sparse_raw(absent={DAY.replace("-","")+"1023"})
    no_witness=assess(symbol=SYMBOL,day=DAY,raw=raw,shares=total,close=10,traded=True)
    assert no_witness.grade=="R2"
    assert no_witness.reason=="same_vendor_native_5m_witness_pending"
    m5=_matching_m5_with_sparse(raw)
    item=assess(symbol=SYMBOL,day=DAY,raw=raw,shares=total,close=10,traded=True,m5_raw=m5)
    assert item.grade=="R1"
    assert item.missing_stamps==(DAY.replace("-","")+"1023",)
    assert len(item.rows)==240
    assert item.witness_sha256 is not None


def test_wrong_five_minute_bucket_does_not_pass_even_if_total_matches() -> None:
    raw,total=_sparse_raw(absent={DAY.replace("-","")+"1023"})
    doc=json.loads(_matching_m5_with_sparse(raw))
    bars=doc["data"]["sz"+SYMBOL]["m5"]
    bars[0][5]=str(Decimal(bars[0][5])+1)
    bars[1][5]=str(Decimal(bars[1][5])-1)
    wrong=json.dumps(doc).encode()
    with pytest.raises(ValueError,match="bucket-volume"):
        # Direct per-bucket proof is required; no daily-total majority vote.
        from quanthus.providers.loss_aware_m1 import _source_rows
        native,_=_source_rows(raw,SYMBOL,DAY,period="m1")
        verify_native_m5(m1=native,m5_raw=wrong,symbol=SYMBOL,day=DAY,
                         daily_shares=Decimal(total),daily_close=Decimal(10))


def test_r2_for_more_than_five_sparse_minutes() -> None:
    grid=_grid(DAY)
    missing=set(grid[20:26])
    raw,total=_sparse_raw(absent=missing)
    item=assess(symbol=SYMBOL,day=DAY,raw=raw,shares=total,close=10,traded=True)
    assert item.grade=="R2"
    assert item.reason=="more_than_five_missing_native_minutes"


def test_r2_for_critical_session_missing() -> None:
    missing={_grid(DAY)[0]}
    raw,total=_sparse_raw(absent=missing)
    item=assess(symbol=SYMBOL,day=DAY,raw=raw,shares=total,close=10,traded=True,
                m5_raw=_matching_m5_with_sparse(raw))
    assert item.grade=="R2"
    assert item.reason=="missing_in_critical_session_window"


def test_r2_for_missing_auction_even_full_grid() -> None:
    raw,total=_sparse_raw(absent={DAY.replace("-","")+"0930"})
    item=assess(symbol=SYMBOL,day=DAY,raw=raw,shares=total,close=10,traded=True)
    assert item.grade=="R2"
    assert item.reason=="no_original_auction_bar"


def test_r3_for_matured_volume_or_close_mismatch() -> None:
    raw,total=_sparse_raw(absent=set())
    item=assess(symbol=SYMBOL,day=DAY,raw=raw,shares=total+6,close=10,traded=True)
    assert item.grade=="R3"
    assert item.reason=="unmatured_or_incorrect_minute_daily_share_volume"
    other=assess(symbol=SYMBOL,day=DAY,raw=raw,shares=total,close=11,traded=True)
    assert other.grade=="R3"


def test_r3_for_source_identity_or_duplicate() -> None:
    raw,total=_sparse_raw(absent=set())
    doc=json.loads(raw)
    doc["data"]["sz"+SYMBOL]["m1"].append(doc["data"]["sz"+SYMBOL]["m1"][-1])
    item=assess(symbol=SYMBOL,day=DAY,raw=json.dumps(doc).encode(),shares=total,close=10,traded=True)
    assert item.grade=="R3"
    assert "duplicate source timestamp" in item.reason
    bad=assess(symbol=SYMBOL,day=DAY,raw=_raw(bad_source_code="000002"),shares=total,close=10,traded=True)
    assert bad.grade=="R3"


def test_suspended_stays_explicit_and_outside_traded_denominator() -> None:
    state=assess(symbol=SYMBOL,day=DAY,raw=None,shares=0,close=10,traded=False)
    assert state.grade=="SUSPENDED"
    assert not state.missing_stamps
    invalid=assess(symbol=SYMBOL,day=DAY,raw=None,shares=100,close=10,traded=False)
    assert invalid.grade=="R3"


def test_capture_day_count_preserves_original_native_rows() -> None:
    raw,_=_sparse_raw(absent={DAY.replace("-","")+"1023"})
    counts=parse_source(raw,SYMBOL,(DAY,))
    assert counts[DAY]==240

"""Research-only source-native 1m loss grading. No fabricated bars or strict READY.

R0 = complete observed 240-slot native session with auction truth; R1 = 1..5
unobserved slots outside critical session boundaries, witnessed by same-vendor
native 5m volume buckets; R2 = incomplete but non-falsified archive; R3 =
unusable/invalid evidence. Research acceptance is consumer- and date-scoped.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from quanthus.providers.tencent_m1_normalize import expected_times

GRADES = ("R0", "R1", "R2", "R3", "SUSPENDED")
CODE = re.compile(r"[0-9]{6}\Z")
SHABYTES = re.compile(r"[0-9a-f]{64}\Z")
TARGET = {"2026-09-24", "2026-09-28", "2026-09-29", "2026-09-30", "2026-10-08"}


def _decimal(value: object, label: str) -> Decimal:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"invalid {label}") from exc
    if not d.is_finite():
        raise ValueError(f"nonfinite {label}")
    return d


def _grid(day: str) -> tuple[str, ...]:
    return expected_times(day)


def _critical(grid: tuple[str, ...]) -> frozenset[str]:
    # First and last five native minute endpoints for BOTH sessions.
    return frozenset((*grid[:5], *grid[115:120], *grid[120:125], *grid[-5:]))


def _source_rows(
    raw: bytes, symbol: str, day: str, *, period: str
) -> tuple[dict[str, dict[str, Decimal]], str]:
    if not CODE.fullmatch(symbol) or day not in TARGET:
        raise ValueError("source stock/date identity invalid")
    doc = json.loads(raw)
    if doc.get("code") != 0:
        raise ValueError("provider error")
    key = ("sh" if symbol.startswith("6") else "sz") + symbol
    raw_rows = doc.get("data", {}).get(key, {}).get(period)
    if not isinstance(raw_rows, list):
        raise ValueError("original vendor frequency list unavailable")
    multiplier = Decimal(1 if symbol.startswith("68") else 100)
    result: dict[str, dict[str, Decimal]] = {}
    prefix = day.replace("-", "")
    for item in raw_rows:
        if not isinstance(item, list) or len(item) < 7:
            raise ValueError("malformed source bar")
        stamp = str(item[0])
        if not stamp.startswith(prefix):
            continue
        if stamp in result:
            raise ValueError("duplicate source timestamp")
        if item[6] not in ({}, None):
            raise ValueError("unexpected provider extra-field semantics")
        row = {
            "open": _decimal(item[1], "open"),
            "close": _decimal(item[2], "close"),
            "high": _decimal(item[3], "high"),
            "low": _decimal(item[4], "low"),
            "volume": _decimal(item[5], "volume") * multiplier,
        }
        if (
            row["volume"] < 0
            or row["high"] < max(row["open"], row["close"], row["low"])
            or row["low"] > min(row["open"], row["close"], row["high"])
        ):
            raise ValueError("invalid native OHLCV")
        result[stamp] = row
    return result, hashlib.sha256(raw).hexdigest()


def _bucket_ends(day: str) -> tuple[str, ...]:
    grid = _grid(day)
    return tuple((*grid[4:120:5], *grid[124:240:5]))


def verify_native_m5(
    *,
    m1: Mapping[str, Mapping[str, Decimal]],
    m5_raw: bytes,
    symbol: str,
    day: str,
    daily_shares: Decimal,
    daily_close: Decimal,
) -> str:
    """Verify exact 48 native Tencent 5m volumes vs originally observed 1m rows."""
    m5, sha = _source_rows(m5_raw, symbol, day, period="m5")
    grid = _grid(day)
    ends = _bucket_ends(day)
    if set(m5) != set(ends) or len(ends) != 48:
        raise ValueError("native 5m has wrong session buckets")
    source_m1_groups: dict[str, Decimal] = {end: Decimal(0) for end in ends}
    for i, minute in enumerate(grid):
        if minute in m1:
            source_m1_groups[ends[i // 5]] += m1[minute]["volume"]
    auction = day.replace("-", "") + "0930"
    if auction in m1:
        source_m1_groups[ends[0]] += m1[auction]["volume"]
    for end in ends:
        if source_m1_groups[end] != m5[end]["volume"]:
            raise ValueError(f"native 5m bucket-volume mismatch at {end}")
    if sum((v["volume"] for v in m5.values()), Decimal(0)) != daily_shares:
        raise ValueError("native 5m total differs from sealed daily")
    if m5[ends[-1]]["close"].quantize(Decimal("0.01")) != daily_close.quantize(Decimal("0.01")):
        raise ValueError("native 5m closing price differs from sealed daily")
    return sha


@dataclass(frozen=True)
class LossAssessment:
    symbol: str
    day: str
    grade: str
    reason: str
    raw_sha256: str | None
    native_count: int
    observed_regular: int
    missing_stamps: tuple[str, ...]
    critical_gap: bool
    daily_share_delta: str | None
    daily_close_delta: str | None
    witness_sha256: str | None
    auction_source_observed: bool
    source_semantic_disagreement: str
    rows: tuple[tuple[str, Mapping[str, Decimal]], ...]

    def disposition(self) -> dict[str, Any]:
        return {
            "stock_code": self.symbol,
            "market_date": self.day,
            "quality_grade": self.grade,
            "reason": self.reason,
            "source_provider": "tencent_mkline_m1" if self.raw_sha256 else None,
            "source_raw_sha256": self.raw_sha256,
            "observed_native_rows": self.native_count,
            "observed_regular_minutes": self.observed_regular,
            "missing_actual_minutes": list(self.missing_stamps),
            "critical_gap": self.critical_gap,
            "daily_share_delta": self.daily_share_delta,
            "daily_close_delta": self.daily_close_delta,
            "native_5m_witness_sha256": self.witness_sha256,
            "auction_source_observed": self.auction_source_observed,
            "source_semantic_disagreement": self.source_semantic_disagreement,
            "amount_quality": "source_absent_no_amount_based_consumers",
            "permitted_use": "non_execution_gap_aware_research_only"
            if self.grade in {"R0", "R1"}
            else "archive_only",
            "strict_240_source_qualified": False,
        }


def assess(
    *,
    symbol: str,
    day: str,
    raw: bytes | None,
    shares: object,
    close: object,
    traded: bool,
    m5_raw: bytes | None = None,
    cross_source_status: str = "not_independently_compared",
) -> LossAssessment:
    """Never accept missing OHLC as an observed minute, even when volume is zero."""
    if day not in TARGET or not CODE.fullmatch(symbol):
        raise ValueError("outside frozen research product universe")
    if cross_source_status not in {
        "known_semantic_conflict",
        "single_qualified_source",
        "not_independently_compared",
    }:
        raise ValueError("unknown source-comparison state")
    daily_shares, daily_close = _decimal(shares, "daily shares"), _decimal(close, "daily close")
    if daily_shares < 0:
        raise ValueError("negative sealed daily volume")
    kwargs: dict[str, Any] = {
        "symbol": symbol,
        "day": day,
        "grade": "R3",
        "reason": "no_original_source",
        "raw_sha256": None,
        "native_count": 0,
        "observed_regular": 0,
        "missing_stamps": _grid(day),
        "critical_gap": True,
        "daily_share_delta": None,
        "daily_close_delta": None,
        "witness_sha256": None,
        "auction_source_observed": False,
        "source_semantic_disagreement": cross_source_status,
        "rows": (),
    }
    if not traded:
        if daily_shares == 0:
            kwargs.update(
                grade="SUSPENDED",
                reason="sealed_daily_no_trade",
                missing_stamps=(),
                critical_gap=False,
            )
        else:
            kwargs["reason"] = "suspended_with_positive_daily_volume"
        return LossAssessment(**kwargs)
    if raw is None:
        return LossAssessment(**kwargs)
    try:
        observed, sha = _source_rows(raw, symbol, day, period="m1")
        kwargs["raw_sha256"] = sha
        grid = _grid(day)
        allowed = set(grid) | {day.replace("-", "") + "0930"}
        if not set(observed) <= allowed:
            raise ValueError("timestamp outside this exchange-session grid")
        missing = tuple(t for t in grid if t not in observed)
        kwargs.update(
            native_count=len(observed),
            observed_regular=len(grid) - len(missing),
            missing_stamps=missing,
            critical_gap=any(t in _critical(grid) for t in missing),
            auction_source_observed=(day.replace("-", "") + "0930") in observed,
            rows=tuple(sorted(observed.items())),
        )
        vtotal = sum((v["volume"] for v in observed.values()), Decimal(0))
        kwargs["daily_share_delta"] = str(vtotal - daily_shares)
        terminal = observed.get(grid[-1])
        kwargs["daily_close_delta"] = str(terminal["close"] - daily_close) if terminal else None
        if terminal is None or terminal["close"].quantize(Decimal("0.01")) != daily_close.quantize(
            Decimal("0.01")
        ):
            kwargs["reason"] = "source_missing_or_wrong_terminal_close"
        elif vtotal != daily_shares:
            kwargs["reason"] = "unmatured_or_incorrect_minute_daily_share_volume"
        elif not kwargs["auction_source_observed"]:
            kwargs.update(grade="R2", reason="no_original_auction_bar")
        elif not missing:
            kwargs.update(grade="R0", reason="native_240_observed_source_minutes")
        elif len(missing) > 5:
            kwargs.update(grade="R2", reason="more_than_five_missing_native_minutes")
        elif kwargs["critical_gap"]:
            kwargs.update(grade="R2", reason="missing_in_critical_session_window")
        elif m5_raw is None:
            kwargs.update(grade="R2", reason="same_vendor_native_5m_witness_pending")
        else:
            try:
                witness_sha = verify_native_m5(
                    m1=observed,
                    m5_raw=m5_raw,
                    symbol=symbol,
                    day=day,
                    daily_shares=daily_shares,
                    daily_close=daily_close,
                )
            except ValueError:
                kwargs.update(grade="R2", reason="native_5m_volume_bucket_mismatch")
            else:
                kwargs.update(
                    grade="R1",
                    reason="at_most_five_sparse_minutes_with_48_native_m5_bucket_conservation",
                    witness_sha256=witness_sha,
                )
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        kwargs.update(grade="R3", reason=f"invalid_source: {str(exc)[:160]}", rows=())
    return LossAssessment(**kwargs)

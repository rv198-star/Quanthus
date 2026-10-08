"""Read-only Tencent native-m5 evidence for *observed* historical sparse m1.

The source-verified cross-frequency volume witness does not turn omitted m1
timestamps into OHLC bars, does not qualify 240-real-minute formal 1m, and
does not participate in any ODS/DWD publication gate.

Pinned semantics: a-stock-data v3.10.1, 8f6a6a53a59813bf5f010875f16f794fdc4f44f4.
Its tencent_kline m5 and Quanthus Tencent m1 share the mkline backend; this
is source-internal consistency, not an independent transaction-level witness.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from quanthus.providers.tencent_m1_normalize import expected_times

SCHEMA = "quanthus.tencent-native-m5-sparse-m1-witness.v1"
UPSTREAM_REVISION = "8f6a6a53a59813bf5f010875f16f794fdc4f44f4"
_MAX_RAW_BYTES = 2_000_000
_HEX_SHA = re.compile(r"[0-9a-f]{64}\Z")
_CODE = re.compile(r"[036][0-9]{5}\Z")


def _number(value: Any, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"invalid numeric {label}") from exc
    if not result.is_finite():
        raise ValueError(f"nonfinite numeric {label}")
    return result


def _verify_bytes(raw: bytes, expected_sha256: str, label: str) -> dict[str, Any]:
    if not isinstance(raw, bytes) or not raw or len(raw) > _MAX_RAW_BYTES:
        raise ValueError(f"{label} raw bytes unavailable or too large")
    if not _HEX_SHA.fullmatch(expected_sha256):
        raise ValueError(f"{label} expected SHA-256 missing")
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected_sha256:
        raise ValueError(f"{label} source raw SHA-256 mismatch")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} source JSON malformed") from exc
    if not isinstance(payload, dict) or payload.get("code") != 0:
        raise ValueError(f"{label} source status was not successful")
    if not isinstance(payload.get("data"), dict):
        raise ValueError(f"{label} source data mapping missing")
    return payload


def _bar(item: Any, label: str) -> dict[str, Decimal]:
    if not isinstance(item, list) or len(item) < 6:
        raise ValueError(f"{label} native source bar malformed")
    result = {
        "open": _number(item[1], "open"),
        "close": _number(item[2], "close"),
        "high": _number(item[3], "high"),
        "low": _number(item[4], "low"),
        "volume": _number(item[5], "volume"),
    }
    if (
        result["volume"] < 0
        or result["high"] < max(result["open"], result["close"], result["low"])
        or result["low"] > min(result["open"], result["close"], result["high"])
    ):
        raise ValueError(f"{label} invalid native OHLCV")
    return result


def _dated_observations(
    payload: dict[str, Any],
    symbol: str,
    frequency: str,
    day: str,
    allowed: set[str],
) -> dict[str, dict[str, Decimal]]:
    node = payload["data"].get(symbol)
    if not isinstance(node, dict) or not isinstance(node.get(frequency), list):
        raise ValueError(f"{frequency} provider symbol/frequency missing")
    observations: dict[str, dict[str, Decimal]] = {}
    for item in node[frequency]:
        if not isinstance(item, list) or not item:
            raise ValueError(f"{frequency} malformed timestamp row")
        stamp = str(item[0])
        if not stamp.startswith(day):
            continue
        if stamp not in allowed or stamp in observations:
            raise ValueError(f"{frequency} wrong-session or duplicate timestamp")
        observations[stamp] = _bar(item, frequency)
    if not observations:
        raise ValueError(f"{frequency} no source observations for requested date")
    return observations


def source_native_m5_support(
    *,
    stock_code: str,
    market_date: str,
    m1_raw: bytes,
    m1_sha256: str,
    m5_raw: bytes,
    m5_sha256: str,
    sealed_daily_shares: Any,
    sealed_daily_close: Any,
) -> dict[str, Any]:
    """Return source-internal 5m volume evidence, never a materialized 1m bar.

    The native m5 and observed m1 K-line sources are both Tencent. Equal volume
    in each of the 48 vendor-aligned buckets supports a zero-volume *hypothesis*
    for omitted m1 timestamps; it cannot prove source 1m OHLC for those rows or
    independently adjudicate the competing paid vendor's time-boundary semantics.
    """
    if not isinstance(stock_code, str) or _CODE.fullmatch(stock_code) is None:
        raise ValueError("expected in-scope six-digit A-share stock_code")
    try:
        target = date.fromisoformat(market_date)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid target market date") from exc
    if target.isoformat() != market_date:
        raise ValueError("target market date is not canonical")

    m1_payload = _verify_bytes(m1_raw, m1_sha256, "m1")
    m5_payload = _verify_bytes(m5_raw, m5_sha256, "m5")
    expected_1m = expected_times(market_date)
    if len(expected_1m) != 240:
        raise ValueError("unexpected governing real-minute session grid")
    expected_5m = tuple(expected_1m[i] for i in range(4, 240, 5))
    if len(expected_5m) != 48 or len(set(expected_5m)) != 48:
        raise ValueError("unexpected governing five-minute session grid")

    symbol = ("sh" if stock_code.startswith("6") else "sz") + stock_code
    prefix = target.strftime("%Y%m%d")
    actual_1m = _dated_observations(
        m1_payload, symbol, "m1", prefix, set(expected_1m) | {prefix + "0930"}
    )
    actual_5m = _dated_observations(m5_payload, symbol, "m5", prefix, set(expected_5m))
    if set(actual_5m) != set(expected_5m):
        raise ValueError("native m5 does not have exactly 48 actually observed session bars")
    expected_shares = _number(sealed_daily_shares, "sealed daily shares")
    expected_close = _number(sealed_daily_close, "sealed daily close")
    if expected_shares < 0 or expected_close <= 0:
        raise ValueError("invalid sealed daily evidence")
    multiplier = Decimal(1 if stock_code.startswith(("688", "689")) else 100)

    # Source m1 is sparse. Count only *actual provider rows* and the real 09:30
    # auction observation; never manufacture an empty minute or a previous-close
    # surrogate as an OHLC bar.
    map_1m_to_5m = {stamp: expected_5m[i // 5] for i, stamp in enumerate(expected_1m)}
    buckets = defaultdict(lambda: Decimal(0))
    for stamp, bar in actual_1m.items():
        bucket = expected_5m[0] if stamp == prefix + "0930" else map_1m_to_5m[stamp]
        buckets[bucket] += bar["volume"] * multiplier
    differences = []
    for stamp in expected_5m:
        source_five_shares = actual_5m[stamp]["volume"] * multiplier
        diff = source_five_shares - buckets[stamp]
        if diff:
            differences.append(
                {"bucket_end": stamp, "native_m5_minus_observed_m1_shares": str(diff)}
            )
    observed_regular = set(actual_1m) & set(expected_1m)
    missing = [stamp for stamp in expected_1m if stamp not in observed_regular]
    m5_total = sum((bar["volume"] * multiplier for bar in actual_5m.values()), Decimal(0))
    m1_observed_total = sum((bar["volume"] * multiplier for bar in actual_1m.values()), Decimal(0))
    close_matches = actual_5m[expected_5m[-1]]["close"] == expected_close
    volume_matches = m5_total == expected_shares == m1_observed_total
    supports_hypothesis = bool(missing) and not differences and volume_matches and close_matches
    return {
        "schema": SCHEMA,
        "upstream_revision": UPSTREAM_REVISION,
        "date": market_date,
        "stock_code": stock_code,
        "provider": "tencent-mkline-native-m5-internal-witness",
        "m1_raw_sha256": m1_sha256,
        "m5_raw_sha256": m5_sha256,
        "volume_multiplier_to_shares": int(multiplier),
        "m1_source_rows": len(actual_1m),
        "m1_observed_regular_minutes": len(observed_regular),
        "m1_auction_observed": prefix + "0930" in actual_1m,
        "m1_missing_actual_source_minutes": missing,
        "m5_observed_buckets": len(actual_5m),
        "m1_observed_shares": str(m1_observed_total),
        "m5_native_shares": str(m5_total),
        "sealed_daily_shares": str(expected_shares),
        "native_5m_close_matches_sealed": close_matches,
        "daily_share_volume_matches_both": volume_matches,
        "cross_frequency_volume_mismatches": differences,
        "same_source_zero_volume_hypothesis_supported": supports_hypothesis,
        "inferred_or_materialized_m1_rows": 0,
        "formal_m1_qualified": False,
        "source_semantic_adjudication_authorized": False,
        "ods_ready_authorized": False,
        "dwd_publication_authorized": False,
        "evidence_grade": "same_provider_cross_frequency_not_independent_trade_tape",
    }

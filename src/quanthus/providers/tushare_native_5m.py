"""Quarantine-only direct Tushare-style native five-minute acquisition.

The API credential remains caller-owned and is never written to disk.  This
module preserves the exact response bytes and lexical decimal values, and only
returns an exact, unadjusted Shanghai business-day grid.  It does not publish
ODS/DWD or grant source admission by itself.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from quanthus.providers.native_5m_exact import SHANGHAI, ExactBar, NativeWindow

ENDPOINT = "https://pcd.mobcvb.cn/tushare/pro/stk_mins"
PROVIDER = "tushare-stk_mins-native-5m"
EXPECTED_FIELDS = {
    "ts_code",
    "trade_time",
    "freq",
    "open",
    "high",
    "low",
    "close",
    "vol",
    "amount",
}
BUCKETS = tuple(
    [f"09:{minute:02d}:00" for minute in range(35, 60, 5)]
    + [f"10:{minute:02d}:00" for minute in range(0, 60, 5)]
    + [f"11:{minute:02d}:00" for minute in range(0, 31, 5)]
    + [f"13:{minute:02d}:00" for minute in range(5, 60, 5)]
    + [f"14:{minute:02d}:00" for minute in range(0, 60, 5)]
    + ["15:00:00"]
)


class TushareNative5mError(ValueError):
    """Stable fail-closed error without source values or credentials."""


def _symbol(code: str) -> str:
    prefix, digits = code.split(".", 1)
    return digits + {"sh": ".SH", "sz": ".SZ"}[prefix]


def _load_lexical(raw: bytes) -> Mapping[str, Any]:
    try:
        value = json.loads(raw, parse_float=str, parse_int=str)
    except (UnicodeDecodeError, ValueError, TypeError) as exc:
        raise TushareNative5mError("SOURCE_RESPONSE_UNREADABLE") from exc
    if not isinstance(value, dict):
        raise TushareNative5mError("SOURCE_RESPONSE_UNREADABLE")
    return value


def parse_response(raw: bytes, request: NativeWindow) -> tuple[ExactBar, ...]:
    """Parse one exact-day direct 5m response while retaining numeric lexemes."""

    if request.start_date != request.end_date:
        raise TushareNative5mError("EXACT_DAY_REQUIRED")
    payload = _load_lexical(raw)
    code = payload.get("code")
    if str(code) != "0" or payload.get("api_name") not in (None, "stk_mins"):
        raise TushareNative5mError("SOURCE_RESPONSE_NOT_SUCCESSFUL")
    meta = payload.get("meta") or {}
    if (
        not isinstance(meta, dict)
        or meta.get("partial") is True
        or meta.get("coverage")
        not in (
            None,
            "complete",
        )
    ):
        raise TushareNative5mError("SOURCE_RESPONSE_PARTIAL")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise TushareNative5mError("SOURCE_DATA_MISSING")
    fields, items = data.get("fields"), data.get("items")
    if (
        not isinstance(fields, list)
        or any(not isinstance(field, str) for field in fields)
        or not set(fields) >= EXPECTED_FIELDS
        or not isinstance(items, list)
        or str(payload.get("count")) != str(len(items))
    ):
        raise TushareNative5mError("SOURCE_SCHEMA_OR_COUNT_INVALID")

    expected_symbol = _symbol(request.code)
    seen: set[datetime] = set()
    bars: list[ExactBar] = []
    for item in items:
        if not isinstance(item, list) or len(item) != len(fields):
            raise TushareNative5mError("SOURCE_ROW_SCHEMA_INVALID")
        row = dict(zip(fields, item, strict=True))
        if row["ts_code"] != expected_symbol or str(row["freq"]).lower() != "5min":
            raise TushareNative5mError("SOURCE_ROW_SCOPE_INVALID")
        try:
            stamp = datetime.strptime(str(row["trade_time"]), "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=SHANGHAI
            )
        except ValueError as exc:
            raise TushareNative5mError("SOURCE_TIMESTAMP_INVALID") from exc
        if stamp.date().isoformat() != request.start_date or stamp in seen:
            raise TushareNative5mError("SOURCE_ROW_DATE_OR_DUPLICATE_INVALID")
        lexical = tuple(
            str(row[name]) for name in ("open", "high", "low", "close", "vol", "amount")
        )
        try:
            values = tuple(Decimal(value) for value in lexical)
        except InvalidOperation as exc:
            raise TushareNative5mError("SOURCE_NUMERIC_INVALID") from exc
        if any(not value.is_finite() for value in values):
            raise TushareNative5mError("SOURCE_NUMERIC_INVALID")
        open_, high, low, close, volume, amount = values
        if (
            low > min(open_, close)
            or high < max(open_, close)
            or low > high
            or volume < 0
            or amount < 0
        ):
            raise TushareNative5mError("SOURCE_NUMERIC_RELATION_INVALID")
        seen.add(stamp)
        bars.append(ExactBar(request.code, stamp, lexical))

    ordered = tuple(sorted(bars, key=lambda row: row.timestamp))
    labels = tuple(row.timestamp.time().replace(tzinfo=None).isoformat() for row in ordered)
    if labels != BUCKETS:
        raise TushareNative5mError("SOURCE_BUCKET_GRID_INVALID")
    return ordered


HttpGet = Callable[..., Any]


def fetch_captured_day(
    staging: Path,
    request: NativeWindow,
    *,
    api_key: str,
    get: HttpGet,
    endpoint: str = ENDPOINT,
    timeout_seconds: int = 25,
) -> tuple[tuple[ExactBar, ...], dict[str, Any]]:
    """Fetch one exact day and retain deterministic compressed source bytes."""

    if not api_key:
        raise TushareNative5mError("API_KEY_REQUIRED")
    if request.start_date != request.end_date:
        raise TushareNative5mError("EXACT_DAY_REQUIRED")
    response = get(
        endpoint,
        params={
            "ts_code": _symbol(request.code),
            "freq": "5min",
            "start_date": request.start_date + " 09:00:00",
            "end_date": request.end_date + " 16:00:00",
        },
        headers={"X-API-Key": api_key},
        timeout=timeout_seconds,
    )
    status = int(response.status_code)
    if status != 200:
        raise TushareNative5mError(f"SOURCE_HTTP_{status}")
    raw = bytes(response.content)
    raw_path = staging / "source-response.json.gz"
    with raw_path.open("xb") as sink, gzip.GzipFile(fileobj=sink, mode="wb", mtime=0) as zipped:
        zipped.write(raw)
    bars = parse_response(raw, request)
    return bars, {
        "provider": PROVIDER,
        "source_endpoint": endpoint,
        "native_frequency": "5m",
        "derived_from": None,
        "price_basis": "provider_unadjusted",
        "volume_unit": "share",
        "amount_unit": "CNY",
        "actual_requests": 1,
        "actual_bytes": len(raw),
        "source_raw_sha256": hashlib.sha256(raw).hexdigest(),
        "source_capture": raw_path.name,
        "source_request_id": response.headers.get("x-request-id"),
        "credential_persisted": False,
    }

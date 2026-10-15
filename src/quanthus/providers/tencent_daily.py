"""Native Tencent daily candidates; never synthesize status or historical valuation."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from quanthus.providers.acquisition_evidence import capture, clock, decimal_token
from quanthus.providers.daily_source_failover import (
    BudgetExhausted,
    SourceUnavailable,
    policy,
    recoverable,
)

URL = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
SOURCE = "tencent.native-unadjusted-daily-v1"


def decode(raw: bytes, symbol: str, day: date, *, acquired_at: str) -> dict[str, Any]:
    obj = json.loads(raw, parse_float=Decimal)
    if obj.get("code") != 0 or symbol not in obj.get("data", {}):
        raise ValueError("TENCENT_DAILY_SECURITY_OR_RESPONSE_INVALID")
    data = obj["data"][symbol]
    # Never read qfqday/hfqday as unadjusted, even when the native key is missing.
    rows = [row for row in data.get("day", []) if row[0] == str(day)]
    if len(rows) != 1:
        raise ValueError("TENCENT_DAILY_TARGET_MISSING_OR_AMBIGUOUS")
    row = rows[0]
    if len(row) < 9:
        raise ValueError("TENCENT_DAILY_NATIVE_AMOUNT_MISSING")
    numbers = {
        name: Decimal(cast(str, decimal_token(row[pos], required=True)))
        for name, pos in [
            ("open", 1),
            ("close", 2),
            ("high", 3),
            ("low", 4),
            ("volume", 5),
            ("turnover_rate", 7),
            ("amount", 8),
        ]
    }
    if (
        min(numbers[n] for n in ["open", "high", "low", "close"]) <= 0
        or numbers["high"] < max(numbers["open"], numbers["close"], numbers["low"])
        or numbers["low"] > min(numbers["open"], numbers["close"], numbers["high"])
        or numbers["volume"] <= 0
        or numbers["amount"] <= 0
    ):
        raise ValueError("TENCENT_DAILY_OHLC_OR_ACTIVITY_INVALID")
    # This adapter admits equities only. Index units require separate evidence.
    if symbol.startswith(("sh000", "sz399")):
        raise ValueError("TENCENT_INDEX_VOLUME_UNIT_NOT_QUALIFIED")
    lot = Decimal(1) if symbol.startswith("sh688") else Decimal(100)
    numbers["volume"] *= lot
    numbers["amount"] *= Decimal(10000)
    # Tencent's turnover token is percent, as in the existing ODS turn field.
    # Positional quote PE/PB semantics are not admitted as these governed measures.
    valuation: dict[str, str | None] = {"pe_ttm": None, "pb": None, "ps_ttm": None, "pcf_ttm": None}
    return {
        "stock_code": symbol[2:],
        "security_id": symbol[2:],
        "market_date": str(day),
        "date": str(day),
        **{k: format(v, "f") for k, v in numbers.items()},
        "pct_change": None,
        "adj_mode": "none",
        "adj_version": SOURCE,
        "source": SOURCE,
        "acquired_at": acquired_at,
        "built_at": acquired_at,
        **{"_valuation_" + k: v for k, v in valuation.items()},
        "_valuation_source": "tencent.source-absent",
        "_source_limitations": [
            "isST_source_absent",
            "pe_ttm_source_absent",
            "pb_source_absent",
            "ps_ttm_source_absent",
            "pcf_ttm_source_absent",
            "tencent_native_volume_amount_precision",
        ],
    }


def fetch(
    code: str,
    day: date,
    *,
    root: Path,
    deadline: datetime,
    opener: Callable[..., Any] = urlopen,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Two HTTP attempts maximum, sharing the existing Job wall-clock budget."""
    cfg = policy()
    symbol = ("sh" if code.startswith(("60", "68")) else "sz") + code
    if len(code) != 6 or not code.isdigit():
        raise ValueError("TENCENT_DAILY_CODE_INVALID")
    url = URL + "?" + urlencode({"param": f"{symbol},day,{day},{day},2,"})
    for attempt in range(1, cfg["http_attempts"] + 1):
        remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            raise BudgetExhausted("DAILY_SHARED_WALL_BUDGET_EXHAUSTED")
        started = clock()
        try:
            request = Request(
                url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}
            )
            try:
                response = opener(request, timeout=min(cfg["http_timeout_seconds"], remaining))
            except HTTPError as exc:
                capture(
                    root=root,
                    request={
                        "provider": "tencent",
                        "url": url,
                        "code": symbol,
                        "market_date": str(day),
                        "attempt": attempt,
                        "response_received": True,
                    },
                    raw=exc.read(2 * 1024 * 1024 + 1),
                    started_at=started,
                    completed_at=clock(),
                    status=str(exc.code),
                )
                raise
            except Exception as exc:
                if not recoverable(exc):
                    raise
                capture(
                    root=root,
                    request={
                        "provider": "tencent",
                        "url": url,
                        "code": symbol,
                        "market_date": str(day),
                        "attempt": attempt,
                        "response_received": False,
                        "error_class": type(exc).__name__,
                    },
                    raw=b"",
                    started_at=started,
                    completed_at=clock(),
                    status="transport_failure",
                )
                raise
            with response:
                raw = response.read(2 * 1024 * 1024 + 1)
                status = str(response.status)
            completed = clock()
            receipt = capture(
                root=root,
                request={
                    "provider": "tencent",
                    "url": url,
                    "code": symbol,
                    "market_date": str(day),
                    "basis": "none",
                    "attempt": attempt,
                },
                raw=raw,
                started_at=started,
                completed_at=completed,
                status=status,
            )
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError("TENCENT_RESPONSE_BYTE_BUDGET_EXCEEDED")
            if status != "200":
                raise SourceUnavailable("TENCENT_HTTP_UNAVAILABLE:" + status)
            bar = decode(raw, symbol, day, acquired_at=completed)
            limits = bar.pop("_source_limitations")
            return bar, {
                "security_id": code,
                "provider": "tencent",
                "acquired_at": completed,
                "response_error_code": "0",
                "response_rows": 1,
                "attempts": attempt,
                "raw_receipt": receipt,
                "limitations": limits,
            }
        except Exception as exc:
            if not recoverable(exc) or attempt == cfg["http_attempts"]:
                raise
            time.sleep(min(cfg["retry_delay_seconds"], max(0, remaining)))
    raise AssertionError("bounded Tencent request did not terminate")


def reuse(
    shadow: Path,
    landing: Path,
    day: date,
    codes: set[str],
) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    """Requalify exact preserved responses, keeping their original response clocks."""
    import base64
    import gzip
    import shutil

    from quanthus.pipelines.p10_delivery_common import atomic, digest
    from quanthus.providers.acquisition_evidence import POLICY, verify_capture

    attempts = shadow / "attempts/market-daily-core" / str(day)
    selected: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    roots = sorted(attempts.iterdir())[-12:] if attempts.exists() else []
    read_bytes = 0
    rejections = []
    for attempt in roots:
        for source in sorted((attempt / "landing/original-responses").glob("*.json.gz")):
            read_bytes += source.stat().st_size
            if read_bytes > 512 * 1024**2 or source.stat().st_size > 16 * 1024**2:
                raise ValueError("TENCENT_REUSE_IO_BUDGET_EXCEEDED")
            if source.is_symlink():
                raise ValueError("TENCENT_REUSE_CAPTURE_SYMLINK")
            with gzip.open(source, "rb") as handle:
                payload = handle.read(16 * 1024**2 + 1)
            if len(payload) > 16 * 1024**2 or digest(payload)[7:] + ".json.gz" != source.name:
                raise ValueError("TENCENT_REUSE_ENVELOPE_IDENTITY_DIFFERS")
            envelope = verify_capture(source)
            if envelope.get("policy_id") != POLICY:
                raise ValueError("TENCENT_REUSE_CAPTURE_POLICY_DIFFERS")
            request = envelope["request"]
            code = str(request.get("code", ""))[2:]
            if (
                request.get("provider") != "tencent"
                or request.get("market_date") != str(day)
                or request.get("basis") != "none"
                or code not in codes
                or envelope.get("status") != "200"
            ):
                continue
            expected_symbol = ("sh" if code.startswith(("60", "68")) else "sz") + code
            if request["code"] != expected_symbol:
                raise ValueError("TENCENT_REUSE_EXCHANGE_IDENTITY_DIFFERS")
            stamp = datetime.fromisoformat(envelope["response_completed_at"])
            if stamp.tzinfo is None or stamp > datetime.now(timezone.utc):
                raise ValueError("TENCENT_REUSE_OBSERVATION_CLOCK_INVALID")
            raw = base64.b64decode(envelope["raw_base64"], validate=True)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError("TENCENT_REUSE_RESPONSE_BYTE_BUDGET_EXCEEDED")
            dest = landing / "original-responses" / source.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                if digest(dest.read_bytes()) != digest(source.read_bytes()):
                    raise ValueError("TENCENT_REUSE_CAPTURE_IDENTITY_DIFFERS")
            else:
                shutil.copyfile(source, dest)
            try:
                bar = decode(raw, request["code"], day, acquired_at=stamp.isoformat())
            except (ValueError, KeyError, TypeError, IndexError) as exc:
                rejections.append(
                    {
                        "code": code,
                        "capture_file": dest.name,
                        "capture_sha256": digest(dest.read_bytes()),
                        "reason": "candidate_not_qualified",
                        "error_class": type(exc).__name__,
                    }
                )
                continue
            limits = bar.pop("_source_limitations")
            prior = selected.get(code)
            if prior and datetime.fromisoformat(prior[0]["acquired_at"]) > stamp:
                continue
            if prior and prior[0]["acquired_at"] == stamp.isoformat() and prior[0] != bar:
                raise ValueError("TENCENT_REUSE_SAME_CLOCK_CONFLICT")
            selected[code] = (
                bar,
                {
                    "security_id": code,
                    "provider": "tencent",
                    "acquired_at": stamp.isoformat(),
                    "response_error_code": "0",
                    "response_rows": 1,
                    "attempts": 0,
                    "new_source_requests": 0,
                    "source_disposition": "verified_original_response_reuse",
                    "limitations": limits,
                    "raw_receipt": {
                        "capture_file": dest.name,
                        "capture_sha256": digest(dest.read_bytes()),
                        "raw_sha256": envelope["raw_sha256"],
                    },
                },
            )
    if rejections:
        atomic(
            landing / "tencent-reuse-rejections.json",
            {
                "market_date": str(day),
                "rejections": rejections,
                "disposition": "preserve rejected candidates; retry missing codes only",
            },
        )
    return selected

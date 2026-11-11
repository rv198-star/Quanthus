"""Collect attempt-local A-share minute bars from TDX via mootdx.

The lane runner always supplies an isolated ``--home``. This module has no PG
integration and does not write shared canonical minute partitions.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import socket
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from quanthus.providers.baostock_transport import set_socket_timeout
from quanthus.providers.minute_recovery import (
    MinuteBudget,
    MinuteBudgetExceeded,
    MinuteRecoveryPolicy,
    load_minute_recovery_policy,
)
from quanthus.providers.minute_source_selection import (
    MinuteCandidate,
    choose_whole_security,
    qualify_candidate,
)
from quanthus.providers.paid_minute_source import normalized_candidate
from quanthus.providers.tencent_m1_normalize import normalize_symbol

PAGE_SIZE = 800
FREQUENCIES = {"1m": 8, "5m": 0}
LAYER_FREQUENCIES = {"prod": "5m", "research": "1m"}
TDX_SERVERS = (
    ("60.12.136.250", 7709),
    ("180.153.18.170", 7709),
    ("218.75.126.9", 7709),
    ("60.191.117.167", 7709),
    ("119.97.185.59", 7709),
    ("124.70.133.119", 7709),
    ("116.205.183.150", 7709),
    ("123.60.73.44", 7709),
    ("121.36.225.169", 7709),
)
EASTMONEY_TRENDS_URL = "https://push2delay.eastmoney.com/api/qt/stock/trends2/get"
EASTMONEY_HISTORY_TRENDS_URL = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
EASTMONEY_TOKEN = "bd1d9ddb04089700cf9c27f6f7426281"
EASTMONEY_MAX_DAYS = 5
EASTMONEY_MIN_INTERVAL_SECONDS = 0.2
EASTMONEY_CONSECUTIVE_ERROR_LIMIT = 20
TENCENT_M1_URL = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/kline/mkline"
TENCENT_MIN_INTERVAL_SECONDS = 0.18
MINUTE_SOURCE_POLICY = "tencent-then-paid-audited-raw-v1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _probe(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def tdx_client(*, budget: MinuteBudget | None = None) -> tuple[Any, str]:
    from mootdx.quotes import Quotes

    for host, port in TDX_SERVERS:
        timeout = min(2.0, budget.remaining()) if budget else 2.0
        if not _probe(host, port, timeout=timeout):
            continue
        try:
            if budget:
                budget.remaining()
            client = Quotes.factory(market="std", server=(host, port))
            if budget:
                budget.remaining()
            probe = client.bars(symbol="000001", frequency=9, offset=1)
            if budget:
                budget.remaining()
            if probe is not None and not probe.empty:
                return client, f"{host}:{port}"
        except MinuteBudgetExceeded:
            raise
        except Exception:  # noqa: BLE001 - provider failover must try the next endpoint
            continue
    for options in ({"bestip": True}, {}):
        try:
            if budget:
                budget.remaining()
            client = Quotes.factory(market="std", **options)
            if budget:
                budget.remaining()
            probe = client.bars(symbol="000001", frequency=9, offset=1)
            if budget:
                budget.remaining()
            if probe is not None and not probe.empty:
                return client, f"factory:{options}"
        except MinuteBudgetExceeded:
            raise
        except Exception:  # noqa: BLE001 - provider failover must try the next endpoint
            continue
    raise RuntimeError("no TDX endpoint returned a valid bars probe")


def _eastmoney_secid(symbol: str) -> str:
    market = "1" if symbol.startswith(("5", "6", "9")) else "0"
    return f"{market}.{symbol}"


def _eastmoney_payload(
    symbol: str,
    *,
    ndays: int = 1,
    timeout: float = 10.0,
    opener: Callable[..., Any] = urlopen,
    session: Any | None = None,
    budget: MinuteBudget | None = None,
) -> dict[str, Any]:
    query = urlencode(
        {
            "secid": _eastmoney_secid(symbol),
            "fields1": "f1,f2,f3,f4,f5,f6,f7,f8",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
            "ndays": str(ndays),
            "iscr": "0",
            "iscca": "0",
            "ut": EASTMONEY_TOKEN,
        }
    )
    endpoint = EASTMONEY_HISTORY_TRENDS_URL if ndays > 1 else EASTMONEY_TRENDS_URL
    request = Request(
        f"{endpoint}?{query}",
        headers={"User-Agent": "Mozilla/5.0 Quanthus/1.0"},
    )
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            request_timeout = min(timeout, budget.remaining()) if budget else timeout
            if session is None:
                with opener(request, timeout=request_timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            else:
                response = session.get(
                    request.full_url,
                    headers=dict(request.header_items()),
                    timeout=request_timeout,
                )
                response.raise_for_status()
                payload = response.json()
            if budget:
                budget.remaining()
            data = payload.get("data")
            if data is None:
                return {"code": symbol, "trends": [], "no_data": True}
            if str(data.get("code") or "").zfill(6) != symbol:
                raise RuntimeError("Eastmoney returned a mismatched security")
            return data
        except MinuteBudgetExceeded:
            raise
        except Exception as exc:  # noqa: BLE001 - bounded provider retry
            last_error = exc
            if attempt < 4:
                delay = 0.5 * (2**attempt)
                budget.sleep(delay) if budget else time.sleep(delay)
    raise RuntimeError(f"Eastmoney trends request failed: {last_error}")


def _eastmoney_ndays(market_date: str) -> int:
    target = datetime.strptime(market_date, "%Y-%m-%d").date()
    shanghai_today = datetime.now(timezone(timedelta(hours=8))).date()
    days = (shanghai_today - target).days + 1
    if days < 1:
        raise ValueError("market_date must not be in the future")
    if days > EASTMONEY_MAX_DAYS:
        raise ValueError(
            f"market_date is outside Eastmoney's {EASTMONEY_MAX_DAYS}-day recovery window"
        )
    return days


def _tencent_daily_evidence(
    *, daily_path: Path, status_path: Path, market_date: str, symbols: Sequence[str]
) -> dict[str, dict[str, Any]]:
    """Require one sealed same-date market universe before Tencent 1m recovery."""
    import pyarrow.parquet as pq

    daily_rows = pq.read_table(
        daily_path, columns=["stock_code", "market_date", "volume", "close"]
    ).to_pylist()
    status_rows = pq.read_table(
        status_path, columns=["stock_code", "market_date", "tradestatus"]
    ).to_pylist()
    daily = {str(row["stock_code"]): row for row in daily_rows}
    status = {str(row["stock_code"]): row for row in status_rows}
    if (
        len(daily) != len(daily_rows)
        or len(status) != len(status_rows)
        or not daily
        or set(daily) != set(status)
        or not set(symbols).issubset(daily)
        or any(row["market_date"] != market_date for row in daily_rows + status_rows)
        or any(str(row["tradestatus"]) not in {"0", "1"} for row in status_rows)
    ):
        raise ValueError("Tencent 1m sealed daily/status evidence is incomplete or wrong-date")
    return {
        code: {
            "volume": daily[code]["volume"],
            "close": daily[code]["close"],
            "tradestatus": str(status[code]["tradestatus"]),
        }
        for code in symbols
    }


def _tencent_qualification_symbols(
    symbols: Sequence[str], daily: dict[str, dict[str, Any]]
) -> tuple[str, ...]:
    """Probe both lot-based markets and STAR before a full Tencent scan.

    Prefer a daily volume that cannot be represented by integer lots. A passing
    sample is only an early source check; every symbol still has its own exact
    daily-volume and session-grid gate.
    """
    from decimal import Decimal

    selected: dict[str, tuple[int, str]] = {}
    for symbol in symbols:
        evidence = daily[symbol]
        if evidence["tradestatus"] != "1" or Decimal(str(evidence["volume"])) <= 0:
            continue
        market = "star" if symbol.startswith("68") else "sh" if symbol.startswith("6") else "sz"
        score = 0 if market == "star" or Decimal(str(evidence["volume"])) % 100 else 1
        if market not in selected or score < selected[market][0]:
            selected[market] = (score, symbol)
    return tuple(selected[market][1] for market in ("sz", "sh", "star") if market in selected)


def _tencent_one_minute_bars(
    symbol: str,
    market_date: str,
    daily: dict[str, Any],
    raw_home: Path,
    *,
    opener: Callable[..., Any] = urlopen,
) -> tuple[Any, dict[str, Any]]:
    """Fetch native OHLCV, preserve raw bytes, and never infer minute amount."""
    import pandas as pd

    provider_symbol = ("sh" if symbol.startswith("6") else "sz") + symbol
    request = Request(
        f"{TENCENT_M1_URL}?param={provider_symbol},m1,,800",
        headers={
            "Referer": "https://gu.qq.com/",
            "User-Agent": "Mozilla/5.0 Quanthus-TencentM1/1.0",
        },
    )
    raw_home.mkdir(parents=True, exist_ok=True)
    raw_path = raw_home / f"{symbol}.json.gz"
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            with opener(request, timeout=12) as response:
                raw = response.read()
            # Retain source bytes even when strict validation fails. A failed
            # qualification must remain auditable without publishing minute bars.
            temporary = raw_path.with_name(f"{raw_path.name}.tmp.{os.getpid()}")
            with (
                temporary.open("wb") as sink,
                gzip.GzipFile(fileobj=sink, mode="wb", mtime=0) as zipped,
            ):
                zipped.write(raw)
            os.replace(temporary, raw_path)
            payload = json.loads(raw)
            if payload.get("code") != 0:
                # Provider faults such as code 11 (Redis unavailable) are
                # transient, unlike a deterministic daily-volume mismatch.
                # Keep the raw response and use the bounded transport retry.
                raise RuntimeError(f"Tencent response code {payload.get('code')}")
            source = payload["data"][provider_symbol]["m1"]
            selected = [
                row for row in source if str(row[0]).startswith(market_date.replace("-", ""))
            ]
            if daily["tradestatus"] == "0":
                if selected or float(daily["volume"]) != 0:
                    raise ValueError(f"suspended stock has Tencent 1m data: {symbol}")
                frame = pd.DataFrame(
                    columns=("stock_code", "ts", "open", "high", "low", "close", "volume", "amount")
                )
                detail: dict[str, Any] = {"source_rows": 0, "normalized_rows": 0}
            else:
                rows, detail = normalize_symbol(
                    symbol,
                    raw,
                    daily_volume=daily["volume"],
                    daily_close=daily["close"],
                    market_date=market_date,
                )
                frame = pd.DataFrame(rows)
            # Pandas otherwise serializes an all-None column as Parquet null
            # type, not the governed nullable float64 amount field.
            frame["amount"] = pd.Series([pd.NA] * len(frame), dtype="Float64")
            return frame, {
                "ok": True,
                "provider": "tencent-mkline-validated",
                "native_frequency": "1m",
                "derived_from": None,
                "market_date": market_date,
                "source_raw_sha256": hashlib.sha256(raw).hexdigest(),
                "source_raw_bytes": len(raw),
                "source_raw_path": str(raw_path),
                "minute_amount_status": "source_absent_null_not_estimated",
                "attempts": attempt,
                **detail,
            }
        except ValueError as exc:
            # A source-quality mismatch is not a transport fault. Retrying the
            # same preliminary snapshot three times only delays the scan.
            raise RuntimeError(f"Tencent m1 request/validation failed for {symbol}: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - retry bounded provider read
            last_error = exc
            if attempt < 3:
                time.sleep(0.4 * attempt)
    raise RuntimeError(f"Tencent m1 request/validation failed for {symbol}: {last_error}")


def _tencent_candidate(frame: Any, meta: dict[str, Any]) -> MinuteCandidate:
    return MinuteCandidate(
        provider="tencent-mkline-validated",
        raw_sha256=meta["source_raw_sha256"],
        raw_ref=meta["source_raw_path"],
        rows=tuple(
            {
                "stock_code": row.stock_code,
                "ts": str(row.ts),
                "open": row.open,
                "high": row.high,
                "low": row.low,
                "close": row.close,
                "volume": row.volume,
                "amount": None,
            }
            for row in frame.itertuples(index=False)
        ),
        auction_provenance="tencent-m1-normalize-v1",
        amount_status="source_absent_null_not_estimated",
    )


def _paid_audit_candidate(
    audit_dir: Path,
    symbol: str,
    market_date: str,
    daily: dict[str, Any],
) -> MinuteCandidate:
    """Replay an immutable, date-bound audit receipt; never fetch or read a key."""
    suffix = ".SH" if symbol.startswith("6") else ".SZ"
    receipt_path = audit_dir / "receipts" / f"{symbol}.json"
    raw_path = audit_dir / "raw" / f"{symbol}.json.gz"
    if receipt_path.is_symlink() or raw_path.is_symlink():
        raise ValueError("paid audit evidence must not use symlinks")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if (
        receipt.get("stock_code") != symbol
        or receipt.get("market_date") != market_date
        or receipt.get("http_status") != 200
        or receipt.get("status") != "qualified_candidate_not_published"
    ):
        raise ValueError("paid audit receipt is not a qualified same-date HTTP 200")
    if raw_path.stat().st_size > 2_000_000:
        raise ValueError("paid audit raw capture is unexpectedly large")
    with gzip.open(raw_path, "rb") as source:
        raw = source.read(16_000_001)
    if len(raw) > 16_000_000 or hashlib.sha256(raw).hexdigest() != receipt.get(
        "source_raw_sha256"
    ):
        raise ValueError("paid audit raw SHA-256 or size mismatch")
    return normalized_candidate(
        raw,
        symbol=symbol + suffix,
        market_date=market_date,
        daily_volume=daily["volume"],
        daily_close=daily["close"],
        first_5m_volume=None,
        raw_ref=str(raw_path),
    )


def _eastmoney_one_minute_frame(data: dict[str, Any], symbol: str) -> Any:
    import pandas as pd

    rows: list[dict[str, Any]] = []
    for raw in data.get("trends") or []:
        fields = str(raw).split(",")
        if len(fields) < 7:
            raise RuntimeError("Eastmoney trends row has fewer than 7 fields")
        ts = pd.Timestamp(fields[0])
        rows.append(
            {
                "stock_code": symbol,
                "ts": ts,
                "open": float(fields[1]),
                "close": float(fields[2]),
                "high": float(fields[3]),
                "low": float(fields[4]),
                "volume": float(fields[5]) * 100.0,
                "amount": float(fields[6]),
            }
        )
    if not rows:
        return pd.DataFrame(
            columns=("stock_code", "ts", "open", "high", "low", "close", "volume", "amount")
        )
    return pd.DataFrame(rows).sort_values("ts").reset_index(drop=True)


def _five_minute_label(ts: Any) -> Any:
    minute = int(ts.minute)
    if ts.hour == 9 and minute == 30:
        return ts.replace(minute=35)
    base = ts.replace(second=0, microsecond=0)
    rounded = ((minute + 4) // 5) * 5
    if rounded == 60:
        return base.replace(minute=0) + timedelta(hours=1)
    return base.replace(minute=rounded)


def _eastmoney_bars(
    symbol: str,
    frequency: str,
    *,
    market_date: str | None = None,
    session: Any | None = None,
    budget: MinuteBudget | None = None,
) -> tuple[Any, dict[str, Any]]:
    started = time.monotonic()
    if market_date is None and session is None and budget is None:
        payload = _eastmoney_payload(symbol)
    else:
        payload = _eastmoney_payload(
            symbol,
            ndays=_eastmoney_ndays(market_date) if market_date else 1,
            session=session,
            **({"budget": budget} if budget else {}),
        )
    minute = _eastmoney_one_minute_frame(payload, symbol)
    if market_date is not None and not minute.empty:
        minute = minute.loc[minute["ts"].dt.strftime("%Y-%m-%d") == market_date].copy()
    if minute.empty:
        return minute, {
            "ok": True,
            "provider": "eastmoney-push2delay-trends2",
            "native_frequency": "1m",
            "derived_from": "1m" if frequency == "5m" else None,
            "pages": 1,
            "rows": 0,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "earliest": None,
            "latest": None,
        }
    if frequency == "1m":
        frame = minute.loc[minute["ts"].dt.strftime("%H:%M") != "09:30"].copy()
    elif frequency == "5m":
        grouped = minute.assign(_bucket=minute["ts"].map(_five_minute_label))
        frame = (
            grouped.groupby("_bucket", sort=True, as_index=False)
            .agg(
                stock_code=("stock_code", "first"),
                open=("open", "first"),
                high=("high", "max"),
                low=("low", "min"),
                close=("close", "last"),
                volume=("volume", "sum"),
                amount=("amount", "sum"),
            )
            .rename(columns={"_bucket": "ts"})
        )
        frame = frame[["stock_code", "ts", "open", "high", "low", "close", "volume", "amount"]]
    else:
        raise RuntimeError(f"unsupported Eastmoney fallback frequency: {frequency}")
    frame = (
        frame.drop_duplicates(subset=["stock_code", "ts"]).sort_values("ts").reset_index(drop=True)
    )
    return frame, {
        "ok": True,
        "provider": "eastmoney-push2delay-trends2",
        "native_frequency": "1m",
        "derived_from": "1m" if frequency == "5m" else None,
        "pages": 1,
        "rows": len(frame),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "earliest": str(frame["ts"].iloc[0]) if len(frame) else None,
        "latest": str(frame["ts"].iloc[-1]) if len(frame) else None,
    }


def _baostock_native_5m_bars(
    symbol: str, market_date: str, *, client: Any
) -> tuple[Any, dict[str, Any]]:
    """Read unadjusted native five-minute OHLCV for one exact security/date."""
    import pandas as pd

    started = time.monotonic()
    code = ("sh." if symbol.startswith("6") else "sz.") + symbol
    response = client.query_history_k_data_plus(
        code,
        "date,time,code,open,high,low,close,volume,amount",
        start_date=market_date,
        end_date=market_date,
        frequency="5",
        adjustflag="3",
    )
    if str(response.error_code) != "0":
        raise RuntimeError(f"BaoStock native 5m error={response.error_code}: {response.error_msg}")
    rows: list[dict[str, Any]] = []
    while response.next():
        raw = response.get_row_data()
        if len(raw) != 9 or raw[0] != market_date or raw[2] != code:
            raise RuntimeError("BaoStock native 5m returned wrong date/code/schema")
        timestamp = pd.to_datetime(raw[1], format="%Y%m%d%H%M%S%f", errors="raise")
        if timestamp.strftime("%Y-%m-%d") != market_date:
            raise RuntimeError("BaoStock native 5m timestamp differs from market date")
        rows.append(
            {
                "stock_code": symbol,
                "ts": timestamp,
                "open": float(raw[3]),
                "high": float(raw[4]),
                "low": float(raw[5]),
                "close": float(raw[6]),
                "volume": float(raw[7]),
                "amount": float(raw[8]),
            }
        )
    frame = pd.DataFrame(
        rows, columns=("stock_code", "ts", "open", "high", "low", "close", "volume", "amount")
    )
    if not frame.empty:
        if frame.duplicated(subset=["stock_code", "ts"]).any():
            raise RuntimeError("BaoStock native 5m returned duplicate bar timestamps")
        frame = frame.sort_values("ts").reset_index(drop=True)
    return frame, {
        "ok": True,
        "provider": "baostock-native-5m",
        "native_frequency": "5m",
        "derived_from": None,
        "source_market_date": market_date if len(frame) else None,
        "adjustflag": "3",
        "volume_unit": "share",
        "amount_unit": "CNY",
        "pages": 1,
        "rows": len(frame),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "earliest": str(frame["ts"].iloc[0]) if len(frame) else None,
        "latest": str(frame["ts"].iloc[-1]) if len(frame) else None,
    }


def _row_timestamp(row: Any) -> Any:
    import pandas as pd

    return pd.Timestamp(
        year=int(row["year"]),
        month=int(row["month"]),
        day=int(row["day"]),
        hour=int(row.get("hour", 0) or 0),
        minute=int(row.get("minute", 0) or 0),
    )


def page_bars(
    client: Any,
    symbol: str,
    frequency: int,
    max_pages: int,
    mode: str,
    *,
    budget: MinuteBudget | None = None,
) -> tuple[Any, dict[str, Any]]:
    import pandas as pd

    frames = []
    empty_streak = 0
    started = time.monotonic()
    pages = 1 if mode == "incr" else max_pages
    for index in range(pages):
        try:
            if budget:
                budget.remaining()
            frame = client.bars(
                symbol=symbol,
                frequency=frequency,
                offset=PAGE_SIZE,
                start=index * PAGE_SIZE,
            )
            if budget:
                budget.remaining()
        except MinuteBudgetExceeded:
            raise
        except Exception as exc:  # noqa: BLE001 - preserve provider failure in evidence
            return pd.DataFrame(), {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "pages": index,
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
        if frame is None or frame.empty:
            empty_streak += 1
            if empty_streak >= 2 or mode == "incr":
                break
            continue
        empty_streak = 0
        frames.append(frame)
        if len(frame) < PAGE_SIZE:
            break

    if not frames:
        return pd.DataFrame(), {
            "ok": True,
            "pages": 0,
            "rows": 0,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "earliest": None,
            "latest": None,
        }
    raw = pd.concat(frames, ignore_index=True)
    output = pd.DataFrame(
        {
            "stock_code": symbol,
            "ts": raw.apply(_row_timestamp, axis=1),
            "open": raw["open"].astype(float),
            "high": raw["high"].astype(float),
            "low": raw["low"].astype(float),
            "close": raw["close"].astype(float),
            "volume": raw["vol"].astype(float),
            "amount": raw["amount"].astype(float),
        }
    )
    output = (
        output.drop_duplicates(subset=["stock_code", "ts"]).sort_values("ts").reset_index(drop=True)
    )
    return output, {
        "ok": True,
        "pages": len(frames),
        "rows": len(output),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "earliest": str(output["ts"].iloc[0]) if len(output) else None,
        "latest": str(output["ts"].iloc[-1]) if len(output) else None,
    }


def _symbols(raw: str) -> list[str]:
    return [item.strip().zfill(6) for item in raw.replace(",", " ").split() if item.strip()]


def _merge_write(home: Path, symbol: str, frequency: str, frame: Any) -> tuple[int, Path]:
    import pandas as pd

    path = home / f"{symbol}_{frequency}.parquet"
    if path.exists() and len(frame):
        frame = (
            pd.concat([pd.read_parquet(path), frame], ignore_index=True)
            .drop_duplicates(subset=["stock_code", "ts"])
            .sort_values("ts")
            .reset_index(drop=True)
        )
    if len(frame):
        temporary = path.with_suffix(".parquet.tmp")
        frame.to_parquet(temporary, index=False)
        temporary.replace(path)
    return len(frame), path


def _require_fresh_qualified_home(home: Path) -> None:
    """Reject reuse before changing an earlier attempt's files or receipts."""
    if home.is_symlink() or (home.exists() and (not home.is_dir() or any(home.iterdir()))):
        raise ValueError("qualified 1m requires a fresh, empty attempt home; retain prior evidence")


def _write_qualified_one_minute(
    home: Path,
    symbol: str,
    frame: Any,
    *,
    candidate: MinuteCandidate,
    market_date: str,
    daily: dict[str, Any],
    expected_semantic_sha256: str,
) -> tuple[int, Path, str]:
    """Publish one whole security only if its staged bytes reproduce qualification.

    Never merge with an earlier file, even on a different date. Cross-attempt
    recovery must instead enter a new attempt through its separately governed
    verifier. A no-replace link also protects against a late target-file collision.
    """
    import pandas as pd

    path = home / f"{symbol}_1m.parquet"
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"qualified 1m target already exists: {path.name}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{symbol}_1m-", suffix=".parquet", dir=home,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        frame.to_parquet(temporary, index=False)
        materialized = pd.read_parquet(temporary)
        materialized_candidate = replace(
            candidate,
            rows=tuple(
                {
                    "stock_code": row.stock_code,
                    "ts": str(row.ts),
                    "open": row.open,
                    "high": row.high,
                    "low": row.low,
                    "close": row.close,
                    "volume": row.volume,
                    "amount": None if pd.isna(row.amount) else row.amount,
                }
                for row in materialized.itertuples(index=False)
            ),
        )
        digest = qualify_candidate(
            materialized_candidate,
            symbol=symbol,
            market_date=market_date,
            daily_volume=daily["volume"],
            daily_close=daily["close"],
            traded=daily["tradestatus"] == "1",
        )
        if digest != expected_semantic_sha256:
            raise ValueError("qualified 1m materialization differs from selected source semantics")
        if len(materialized):
            # Unlike replace(), link() cannot overwrite a competing writer's
            # target. The existing node/attempt lock still owns process exclusion.
            os.link(temporary, path)
        elif path.exists() or path.is_symlink():
            raise FileExistsError(f"qualified 1m target already exists: {path.name}")
        return len(materialized), path, digest
    finally:
        unwinding = sys.exc_info()[0] is not None
        try:
            temporary.unlink(missing_ok=True)
        except OSError as exc:
            if not unwinding:
                raise
            print(f"minute staging cleanup failed: {exc}", file=sys.stderr)


def collect(
    *,
    home: Path,
    layer: str,
    frequency: str,
    mode: str,
    symbols: list[str],
    max_pages: int,
    sleep_seconds: float,
    market_date: str | None = None,
    recovery_policy: MinuteRecoveryPolicy | None = None,
    tencent_daily_path: Path | None = None,
    tencent_status_path: Path | None = None,
    paid_audit_dir: Path | None = None,
) -> int:
    if not symbols:
        raise ValueError("symbols must not be empty")
    if recovery_policy and (frequency != "1m" or layer != "research" or mode != "incr"):
        raise ValueError("minute recovery v1 is limited to research 1m incr")
    if recovery_policy and len(set(symbols)) != len(symbols):
        raise ValueError("minute recovery requires unique symbols")
    tencent_enabled = tencent_daily_path is not None or tencent_status_path is not None
    if tencent_enabled and (
        tencent_daily_path is None
        or tencent_status_path is None
        or frequency != "1m"
        or layer != "research"
        or mode != "incr"
        or market_date is None
        or recovery_policy is not None
    ):
        raise ValueError("Tencent 1m requires paired sealed evidence and research incr mode")
    if tencent_enabled:
        if len(set(symbols)) != len(symbols):
            raise ValueError("qualified 1m requires unique symbols")
        _require_fresh_qualified_home(home)
    if paid_audit_dir is not None and (
        not tencent_enabled or paid_audit_dir.is_symlink() or not paid_audit_dir.is_dir()
    ):
        raise ValueError("paid audit fallback requires a real directory and sealed 1m evidence")
    tencent_daily = (
        _tencent_daily_evidence(
            daily_path=tencent_daily_path,
            status_path=tencent_status_path,
            market_date=market_date,
            symbols=symbols,
        )
        if tencent_enabled
        else {}
    )
    if tencent_enabled:
        sleep_seconds = max(sleep_seconds, TENCENT_MIN_INTERVAL_SECONDS)
    started = time.monotonic()
    budget = MinuteBudget.start(recovery_policy.budget_seconds) if recovery_policy else None
    budget_kwargs = {"budget": budget} if budget else {}
    home.mkdir(parents=True, exist_ok=True)
    status_path = home / "status.json"
    checkpoint_path = home / "checkpoint.json"
    client: Any | None = None
    server = "unselected"
    fallback: str | None = None
    # Both scheduled and explicitly requested historical repairs target one exact
    # session; provider pagination is unnecessary for this date-bounded API.
    native_5m = frequency == "5m" and market_date is not None
    results: dict[str, dict[str, Any]] = {}
    errors = 0
    done = 0
    consecutive_errors = 0
    session: Any | None = None
    scan_round = 0
    attempts_total = 0
    terminal_reason: str | None = None

    def checkpoint() -> None:
        _atomic_json(
            checkpoint_path,
            {"symbols": results, "updated_at": _now_iso()},
        )

    def record(state: str, current: str | None = None) -> None:
        # A terminal receipt must never outrun its per-symbol evidence.
        if state != "running":
            checkpoint()
        _atomic_json(
            status_path,
            {
                "state": state,
                "phase": "finished" if state != "running" else "backfill",
                "layer": layer,
                "freq": frequency,
                "mode": mode,
                "server": server,
                "total": len(symbols),
                "done": done,
                "error": errors,
                "current": current,
                "elapsed_s": round(time.monotonic() - started, 1),
                "updated_at": _now_iso(),
                **(
                    {
                        "recovery_policy_id": recovery_policy.policy_id,
                        "recovery_policy_sha256": recovery_policy.sha256,
                        "budget_seconds": recovery_policy.budget_seconds,
                        "scan_round": scan_round,
                        "symbol_attempts": attempts_total,
                        "terminal_reason": terminal_reason,
                    }
                    if recovery_policy
                    else {}
                ),
            },
        )

    def collect_symbol(symbol: str) -> None:
        nonlocal current, client, server, done, errors, consecutive_errors, attempts_total
        current = symbol
        if budget:
            budget.remaining()
        previous = results.get(symbol, {})
        history = list(previous.get("attempts", []))
        attempts_total += 1
        writing = False
        selected: MinuteCandidate | None = None
        try:
            if native_5m:
                assert market_date is not None
                frame, meta = _baostock_native_5m_bars(symbol, market_date, client=client)
            elif fallback == "tencent":
                assert market_date is not None
                candidates: list[MinuteCandidate] = []
                tencent_error: Exception | None = None
                try:
                    frame, meta = _tencent_one_minute_bars(
                        symbol,
                        market_date,
                        tencent_daily[symbol],
                        home / "provider_raw" / "tencent-m1",
                    )
                    candidates.append(_tencent_candidate(frame, meta))
                except (OSError, RuntimeError, ValueError) as exc:
                    tencent_error = exc
                audit_receipt = (
                    paid_audit_dir / "receipts" / f"{symbol}.json"
                    if paid_audit_dir is not None and tencent_daily[symbol]["tradestatus"] == "1"
                    else None
                )
                if audit_receipt is not None and audit_receipt.exists():
                    try:
                        audit_record = json.loads(audit_receipt.read_text(encoding="utf-8"))
                        audit_status = audit_record["status"]
                        if (
                            audit_record.get("stock_code") != symbol
                            or audit_record.get("market_date") != market_date
                            or audit_status not in {
                                "qualified_candidate_not_published",
                                "source_pending",
                                "unqualified",
                                "rate_limited",
                                "http_error",
                                "transport_error",
                            }
                        ):
                            raise ValueError("paid audit receipt code/date/status mismatch")
                        if audit_status == "qualified_candidate_not_published":
                            candidates.append(
                                _paid_audit_candidate(
                                    paid_audit_dir, symbol, market_date, tencent_daily[symbol]
                                )
                            )
                    except (OSError, ValueError, KeyError, TypeError) as exc:
                        raise RuntimeError(
                            f"paid audit evidence invalid for {symbol}: {exc}"
                        ) from exc
                decision = choose_whole_security(
                    candidates,
                    symbol=symbol,
                    market_date=market_date,
                    daily_volume=tencent_daily[symbol]["volume"],
                    daily_close=tencent_daily[symbol]["close"],
                    traded=tencent_daily[symbol]["tradestatus"] == "1",
                    priority=("tencent-mkline-validated", "paid-tushare-style-proxy-stk_mins"),
                )
                if decision.status != "qualified_whole_security_candidate":
                    if decision.status == "blocked_cross_source_conflict":
                        raise RuntimeError(f"cross-source minute conflict for {symbol}")
                    raise RuntimeError(
                        f"{tencent_error or 'no qualified 1m source'}; "
                        f"candidate rejections={decision.rejected}"
                    )
                selected = next(
                    candidate for candidate in candidates
                    if candidate.provider == decision.selected_provider
                )
                if decision.selected_provider == "paid-tushare-style-proxy-stk_mins":
                    import pandas as pd
                    frame = pd.DataFrame(
                        {
                            **row,
                            **{
                                name: float(row[name])
                                for name in ("open", "high", "low", "close", "volume")
                            },
                        }
                        for row in selected.rows
                    )
                    frame["ts"] = pd.to_datetime(frame["ts"])
                    frame["amount"] = pd.Series([pd.NA] * len(frame), dtype="Float64")
                    meta = {
                        "ok": True,
                        "provider": selected.provider,
                        "native_frequency": "1m",
                        "derived_from": None,
                        "market_date": market_date,
                        "source_raw_sha256": selected.raw_sha256,
                        "source_raw_path": selected.raw_ref,
                        "minute_amount_status": selected.amount_status,
                        "auction_mode": selected.auction_provenance,
                        "normalized_rows": len(frame),
                    }
                meta.update(
                    source_selection_policy=MINUTE_SOURCE_POLICY,
                    source_selection_status=decision.status,
                    source_semantic_sha256=decision.normalized_sha256,
                    source_qualified=list(decision.qualified),
                    source_rejected=list(decision.rejected),
                )
            elif fallback == "eastmoney":
                frame, meta = _eastmoney_bars(
                    symbol,
                    frequency,
                    market_date=market_date,
                    session=session,
                    **budget_kwargs,
                )
            else:
                frame, meta = page_bars(
                    client,
                    symbol,
                    FREQUENCIES[frequency],
                    max_pages,
                    mode,
                    **budget_kwargs,
                )
                meta.update(
                    provider="mootdx-native-kline",
                    native_frequency=frequency,
                    derived_from=None,
                )
            if budget:
                budget.remaining()
            if not meta.get("ok"):
                raise RuntimeError(str(meta.get("error") or "minute bars request failed"))
            writing = True
            if selected is not None:
                assert market_date is not None
                rows, path, materialized_digest = _write_qualified_one_minute(
                    home, symbol, frame,
                    candidate=selected,
                    market_date=market_date,
                    daily=tencent_daily[symbol],
                    expected_semantic_sha256=meta["source_semantic_sha256"],
                )
                meta["materialized_semantic_sha256"] = materialized_digest
            else:
                rows, path = _merge_write(home, symbol, frequency, frame)
            results[symbol] = {
                **meta,
                "status": "done",
                "path": str(path),
                "rows_file": rows,
            }
            if previous.get("status") == "error":
                errors -= 1
            done += 1
            consecutive_errors = 0
            if recovery_policy:
                history.append({"attempt": len(history) + 1, "round": scan_round, "status": "done"})
                results[symbol]["attempts"] = history
        except MinuteBudgetExceeded:
            raise
        except Exception as exc:  # noqa: BLE001 - durable per-symbol failure evidence
            if previous.get("status") != "error":
                errors += 1
            consecutive_errors += 1
            detail = f"{type(exc).__name__}: {exc}"
            results[symbol] = {"status": "error", "error": detail}
            if recovery_policy:
                history.append(
                    {
                        "attempt": len(history) + 1,
                        "round": scan_round,
                        "status": "error",
                        "error": detail,
                    }
                )
                results[symbol]["attempts"] = history
            if writing and (recovery_policy or tencent_enabled):
                raise  # Storage/consistency failures must not induce more network traffic.
            if native_5m and consecutive_errors >= 3:
                raise RuntimeError(
                    "BaoStock native 5m circuit opened after 3 consecutive failures"
                ) from exc
            if fallback is None and not native_5m:
                try:
                    client, server = tdx_client(**budget_kwargs)
                except MinuteBudgetExceeded:
                    raise
                except Exception:
                    pass
            elif (
                consecutive_errors >= EASTMONEY_CONSECUTIVE_ERROR_LIMIT
                and not (
                    fallback == "tencent"
                    and any(
                        marker in detail.lower()
                        for marker in (
                            "tencent/daily volume mismatch",
                            "tencent/daily close mismatch",
                        )
                    )
                )
            ):
                raise RuntimeError(
                    f"{fallback or 'provider'} circuit opened after "
                    f"{consecutive_errors} consecutive symbol failures"
                ) from exc

    current: str | None = None
    try:
        record("running")
        if native_5m:
            import baostock as bs

            login = bs.login()
            if str(login.error_code) != "0":
                raise RuntimeError(f"BaoStock native 5m login failed: {login.error_msg}")
            client = bs
            set_socket_timeout(30.0)
            server = "baostock-native-5m"
        else:
            if tencent_enabled:
                server = "tencent-mkline-validated"
                fallback = "tencent"
            else:
                try:
                    client, server = tdx_client(**budget_kwargs)
                except MinuteBudgetExceeded:
                    raise
                except RuntimeError:
                    if mode != "incr":
                        raise
                    server = "eastmoney-push2delay-trends2"
                    fallback = "eastmoney"
        if budget:
            budget.remaining()
        if fallback == "eastmoney":
            import requests

            session = requests.Session()
            sleep_seconds = max(sleep_seconds, EASTMONEY_MIN_INTERVAL_SECONDS)
        if fallback == "tencent":
            _atomic_json(
                home / "source_qualification.json",
                {
                    "provider": "whole-security-source-selection",
                    "market_date": market_date,
                    "status": "per_symbol_strict_scan_not_yet_published",
                    "policy": MINUTE_SOURCE_POLICY,
                    "paid_audit_dir": str(paid_audit_dir) if paid_audit_dir else None,
                },
            )
        pending = list(symbols)
        max_rounds = 1 + (recovery_policy.retry_rounds if recovery_policy else 0)
        for scan_round in range(1, max_rounds + 1):
            if not pending:
                break
            if scan_round > 1:
                budget.sleep(recovery_policy.retry_delay_seconds)
            for index, symbol in enumerate(pending, 1):
                # The helper below owns the per-symbol attempt; round membership
                # is derived only from this invocation's failed results.
                collect_symbol(symbol)
                if index % 5 == 0 or index == len(pending):
                    checkpoint()
                    record("running", symbol)
                if sleep_seconds > 0 and (not recovery_policy or index < len(pending)):
                    budget.sleep(sleep_seconds) if budget else time.sleep(sleep_seconds)
            pending = [code for code in symbols if results[code]["status"] == "error"]
        if budget:
            budget.remaining()
        record("done" if errors == 0 else "done_with_errors")
        return 0 if errors == 0 else 1
    except BaseException as exc:
        terminal_reason = (
            "budget_exhausted" if isinstance(exc, MinuteBudgetExceeded) else type(exc).__name__
        )
        # Includes catchable cancellation, not uncatchable SIGKILL/default SIGTERM.
        # Persistence failure must not replace the original cause or claim success.
        try:
            record("failed", current)
        except Exception as write_exc:  # noqa: BLE001 - preserve the original exception
            print(f"minute terminal evidence write failed: {write_exc}", file=sys.stderr)
        raise
    finally:
        if session is not None:
            unwinding = sys.exc_info()[0] is not None
            try:
                session.close()
            except Exception as exc:  # noqa: BLE001 - preserve an active failure
                if not unwinding:
                    raise
                print(f"minute session cleanup failed: {exc}", file=sys.stderr)
        if native_5m and client is not None:
            unwinding = sys.exc_info()[0] is not None
            try:
                client.logout()
            except Exception as exc:  # noqa: BLE001 - preserve an active failure
                if not unwinding:
                    raise
                print(f"BaoStock cleanup failed: {exc}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="quanthus-minute-tdx")
    parser.add_argument("--layer", choices=sorted(LAYER_FREQUENCIES), required=True)
    parser.add_argument("--freq", choices=sorted(FREQUENCIES), required=True)
    parser.add_argument("--mode", choices=("backfill", "incr"), required=True)
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--max-pages", type=int, default=500)
    parser.add_argument("--sleep", type=float, default=0.05)
    parser.add_argument("--market-date")
    parser.add_argument("--recovery-policy", type=Path)
    parser.add_argument("--tencent-daily-path", type=Path)
    parser.add_argument("--tencent-status-path", type=Path)
    parser.add_argument("--paid-audit-dir", type=Path)
    parser.add_argument("--home", required=True)
    parser.add_argument("--no-lock", action="store_true")
    args = parser.parse_args(argv)

    expected_frequency = LAYER_FREQUENCIES[args.layer]
    if args.freq != expected_frequency:
        parser.error(f"{args.layer} layer requires --freq {expected_frequency}")
    if args.max_pages < 1:
        parser.error("--max-pages must be at least 1")
    del args.no_lock  # lane attempts are isolated; this flag preserves legacy CLI compatibility
    try:
        if args.tencent_daily_path is not None or args.tencent_status_path is not None:
            # Check the requested path before resolve() erases a final symlink.
            _require_fresh_qualified_home(Path(args.home))
        return collect(
            home=Path(args.home).resolve(),
            layer=args.layer,
            frequency=args.freq,
            mode=args.mode,
            symbols=_symbols(args.symbols),
            max_pages=args.max_pages,
            sleep_seconds=args.sleep,
            market_date=args.market_date,
            recovery_policy=(
                load_minute_recovery_policy(args.recovery_policy) if args.recovery_policy else None
            ),
            tencent_daily_path=args.tencent_daily_path,
            tencent_status_path=args.tencent_status_path,
            paid_audit_dir=args.paid_audit_dir,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"minute collector failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

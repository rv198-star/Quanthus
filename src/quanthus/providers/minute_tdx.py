"""Collect attempt-local A-share minute bars from TDX via mootdx.

The lane runner always supplies an isolated ``--home``. This module has no PG
integration and does not write shared canonical minute partitions.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import socket
import sys
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from quanthus.providers.baostock_transport import set_socket_timeout

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


def tdx_client() -> tuple[Any, str]:
    from mootdx.quotes import Quotes

    for host, port in TDX_SERVERS:
        if not _probe(host, port):
            continue
        try:
            client = Quotes.factory(market="std", server=(host, port))
            probe = client.bars(symbol="000001", frequency=9, offset=1)
            if probe is not None and not probe.empty:
                return client, f"{host}:{port}"
        except Exception:  # noqa: BLE001 - provider failover must try the next endpoint
            continue
    for options in ({"bestip": True}, {}):
        try:
            client = Quotes.factory(market="std", **options)
            probe = client.bars(symbol="000001", frequency=9, offset=1)
            if probe is not None and not probe.empty:
                return client, f"factory:{options}"
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
            if session is None:
                with opener(request, timeout=timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            else:
                response = session.get(
                    request.full_url,
                    headers=dict(request.header_items()),
                    timeout=timeout,
                )
                response.raise_for_status()
                payload = response.json()
            data = payload.get("data")
            if data is None:
                return {"code": symbol, "trends": [], "no_data": True}
            if str(data.get("code") or "").zfill(6) != symbol:
                raise RuntimeError("Eastmoney returned a mismatched security")
            return data
        except Exception as exc:  # noqa: BLE001 - bounded provider retry
            last_error = exc
            if attempt < 4:
                time.sleep(0.5 * (2**attempt))
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
) -> tuple[Any, dict[str, Any]]:
    started = time.monotonic()
    if market_date is None and session is None:
        payload = _eastmoney_payload(symbol)
    else:
        payload = _eastmoney_payload(
            symbol,
            ndays=_eastmoney_ndays(market_date) if market_date else 1,
            session=session,
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
) -> tuple[Any, dict[str, Any]]:
    import pandas as pd

    frames = []
    empty_streak = 0
    started = time.monotonic()
    pages = 1 if mode == "incr" else max_pages
    for index in range(pages):
        try:
            frame = client.bars(
                symbol=symbol,
                frequency=frequency,
                offset=PAGE_SIZE,
                start=index * PAGE_SIZE,
            )
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
) -> int:
    if not symbols:
        raise ValueError("symbols must not be empty")
    home.mkdir(parents=True, exist_ok=True)
    status_path = home / "status.json"
    checkpoint_path = home / "checkpoint.json"
    client: Any | None
    fallback = False
    # Both scheduled and explicitly requested historical repairs target one exact
    # session; provider pagination is unnecessary for this date-bounded API.
    native_5m = frequency == "5m" and market_date is not None
    if native_5m:
        import baostock as bs

        assert market_date is not None
        login = bs.login()
        if str(login.error_code) != "0":
            raise RuntimeError(f"BaoStock native 5m login failed: {login.error_msg}")
        try:
            set_socket_timeout(30.0)
        except Exception:
            bs.logout()
            raise
        client = bs
        server = "baostock-native-5m"
    else:
        try:
            client, server = tdx_client()
        except RuntimeError:
            if mode != "incr":
                raise
            client = None
            server = "eastmoney-push2delay-trends2"
            fallback = True
    started = time.monotonic()
    results: dict[str, dict[str, Any]] = {}
    errors = 0
    done = 0
    consecutive_errors = 0
    session: Any | None = None
    if fallback:
        import requests

        session = requests.Session()
        sleep_seconds = max(sleep_seconds, EASTMONEY_MIN_INTERVAL_SECONDS)

    def record(state: str, current: str | None = None) -> None:
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
            },
        )

    record("running")
    for index, symbol in enumerate(symbols, 1):
        try:
            if native_5m:
                frame, meta = _baostock_native_5m_bars(symbol, market_date, client=client)
            elif fallback:
                frame, meta = _eastmoney_bars(
                    symbol,
                    frequency,
                    market_date=market_date,
                    session=session,
                )
            else:
                frame, meta = page_bars(
                    client,
                    symbol,
                    FREQUENCIES[frequency],
                    max_pages,
                    mode,
                )
                meta.update(
                    provider="mootdx-native-kline",
                    native_frequency=frequency,
                    derived_from=None,
                )
            if not meta.get("ok"):
                raise RuntimeError(str(meta.get("error") or "minute bars request failed"))
            rows, path = _merge_write(home, symbol, frequency, frame)
            results[symbol] = {
                "status": "done",
                "path": str(path),
                "rows_file": rows,
                **meta,
            }
            done += 1
            consecutive_errors = 0
        except Exception as exc:  # noqa: BLE001 - record per-symbol provider failures
            errors += 1
            consecutive_errors += 1
            results[symbol] = {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
            }
            if native_5m and consecutive_errors >= 3:
                _atomic_json(
                    checkpoint_path,
                    {"symbols": results, "updated_at": _now_iso()},
                )
                record("failed", symbol)
                with contextlib.suppress(Exception):
                    client.logout()
                raise RuntimeError(
                    "BaoStock native 5m circuit opened after 3 consecutive failures"
                ) from exc
            if not fallback and not native_5m:
                with contextlib.suppress(Exception):
                    client, server = tdx_client()
            elif consecutive_errors >= EASTMONEY_CONSECUTIVE_ERROR_LIMIT:
                record("failed", symbol)
                if session is not None:
                    session.close()
                raise RuntimeError(
                    "Eastmoney circuit opened after "
                    f"{consecutive_errors} consecutive symbol failures"
                ) from exc
        if index % 5 == 0 or index == len(symbols):
            _atomic_json(
                checkpoint_path,
                {"symbols": results, "updated_at": _now_iso()},
            )
            record("running", symbol)
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    record("done" if errors == 0 else "done_with_errors")
    if session is not None:
        session.close()
    if native_5m:
        client.logout()
    return 0 if errors == 0 else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="quanthus-minute-tdx")
    parser.add_argument("--layer", choices=sorted(LAYER_FREQUENCIES), required=True)
    parser.add_argument("--freq", choices=sorted(FREQUENCIES), required=True)
    parser.add_argument("--mode", choices=("backfill", "incr"), required=True)
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--max-pages", type=int, default=500)
    parser.add_argument("--sleep", type=float, default=0.05)
    parser.add_argument("--market-date")
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
        return collect(
            home=Path(args.home).resolve(),
            layer=args.layer,
            frequency=args.freq,
            mode=args.mode,
            symbols=_symbols(args.symbols),
            max_pages=args.max_pages,
            sleep_seconds=args.sleep,
            market_date=args.market_date,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"minute collector failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

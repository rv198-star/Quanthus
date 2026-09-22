"""Collect attempt-local A-share minute bars from TDX via mootdx.

The lane runner always supplies an isolated ``--home``. This module has no PG
integration and does not write shared canonical minute partitions.
"""

from __future__ import annotations

import argparse
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

from quanthus.providers.minute_recovery import (
    MinuteBudget,
    MinuteBudgetExceeded,
    MinuteRecoveryPolicy,
    load_minute_recovery_policy,
)

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
) -> int:
    if not symbols:
        raise ValueError("symbols must not be empty")
    if recovery_policy and (frequency != "1m" or layer != "research" or mode != "incr"):
        raise ValueError("minute recovery v1 is limited to research 1m incr")
    if recovery_policy and len(set(symbols)) != len(symbols):
        raise ValueError("minute recovery requires unique symbols")
    started = time.monotonic()
    budget = MinuteBudget.start(recovery_policy.budget_seconds) if recovery_policy else None
    budget_kwargs = {"budget": budget} if budget else {}
    home.mkdir(parents=True, exist_ok=True)
    status_path = home / "status.json"
    checkpoint_path = home / "checkpoint.json"
    client: Any | None = None
    server = "unselected"
    fallback = False
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
        try:
            if fallback:
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
            if budget:
                budget.remaining()
            if not meta.get("ok"):
                raise RuntimeError(str(meta.get("error") or "minute bars request failed"))
            writing = True
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
                if writing:
                    raise  # Storage failures must not induce more network traffic.
            if not fallback:
                try:
                    client, server = tdx_client(**budget_kwargs)
                except MinuteBudgetExceeded:
                    raise
                except Exception:
                    pass
            elif consecutive_errors >= EASTMONEY_CONSECUTIVE_ERROR_LIMIT:
                raise RuntimeError(
                    "Eastmoney circuit opened after "
                    f"{consecutive_errors} consecutive symbol failures"
                ) from exc

    current: str | None = None
    try:
        record("running")
        try:
            client, server = tdx_client(**budget_kwargs)
        except MinuteBudgetExceeded:
            raise
        except RuntimeError:
            if mode != "incr":
                raise
            server = "eastmoney-push2delay-trends2"
            fallback = True
        if budget:
            budget.remaining()
        if fallback:
            import requests

            session = requests.Session()
            sleep_seconds = max(sleep_seconds, EASTMONEY_MIN_INTERVAL_SECONDS)
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
            recovery_policy=(
                load_minute_recovery_policy(args.recovery_policy) if args.recovery_policy else None
            ),
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"minute collector failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

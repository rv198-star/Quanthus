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
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
        output.drop_duplicates(subset=["stock_code", "ts"])
        .sort_values("ts")
        .reset_index(drop=True)
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
) -> int:
    if not symbols:
        raise ValueError("symbols must not be empty")
    home.mkdir(parents=True, exist_ok=True)
    status_path = home / "status.json"
    checkpoint_path = home / "checkpoint.json"
    client, server = tdx_client()
    started = time.monotonic()
    results: dict[str, dict[str, Any]] = {}
    errors = 0
    done = 0

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
            frame, meta = page_bars(
                client,
                symbol,
                FREQUENCIES[frequency],
                max_pages,
                mode,
            )
            if not meta.get("ok"):
                raise RuntimeError(str(meta.get("error") or "TDX bars request failed"))
            rows, path = _merge_write(home, symbol, frequency, frame)
            results[symbol] = {
                "status": "done",
                "path": str(path),
                "rows_file": rows,
                **meta,
            }
            done += 1
        except Exception as exc:  # noqa: BLE001 - record per-symbol provider failures
            errors += 1
            results[symbol] = {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
            }
            with contextlib.suppress(Exception):
                client, server = tdx_client()
        if index % 5 == 0 or index == len(symbols):
            _atomic_json(
                checkpoint_path,
                {"symbols": results, "updated_at": _now_iso()},
            )
            record("running", symbol)
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    record("done" if errors == 0 else "done_with_errors")
    return 0 if errors == 0 else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="quanthus-minute-tdx")
    parser.add_argument("--layer", choices=sorted(LAYER_FREQUENCIES), required=True)
    parser.add_argument("--freq", choices=sorted(FREQUENCIES), required=True)
    parser.add_argument("--mode", choices=("backfill", "incr"), required=True)
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--max-pages", type=int, default=500)
    parser.add_argument("--sleep", type=float, default=0.05)
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
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"minute collector failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

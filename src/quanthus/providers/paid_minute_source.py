"""Quarantined qualification for the owner's paid A-share minute proxy.

This module never writes ODS READY or DWD. A successful HTTP response is only
source evidence; every symbol must match the sealed daily and native 5m inputs.
The API key is read from stdin by the audit command and is never persisted.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from quanthus.providers.minute_source_selection import MinuteCandidate
from quanthus.providers.tencent_m1_normalize import expected_times

ENDPOINT = "https://tl.kaixin8.top/tushare/pro/stk_mins"
FIELDS = {"ts_code", "trade_time", "freq", "open", "high", "low", "close", "vol", "amount"}
MATERIAL_FIELDS = ("open", "high", "low", "close", "vol")


def _decimal(value: Any) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("invalid minute number") from exc
    if not number.is_finite():
        raise ValueError("non-finite minute number")
    return number


def _material_complete(row: dict[str, Any]) -> bool:
    return all(row[name] is not None for name in MATERIAL_FIELDS)


def _source_start_times(market_date: str) -> dict[str, str]:
    """Map the governed minute-end grid to an interval-start source grid."""

    result: dict[str, str] = {}
    for stamp in expected_times(market_date):
        canonical = datetime.strptime(stamp, "%Y%m%d%H%M")
        result[canonical.strftime("%H:%M")] = (canonical - timedelta(minutes=1)).strftime("%H:%M")
    return result


def _parse_rows(
    payload: dict[str, Any], *, symbol: str, market_date: str
) -> tuple[list[str], list[list[Any]], dict[str, dict[str, Any]]]:
    if payload.get("code") != 0 or payload.get("api_name") not in (None, "stk_mins"):
        raise ValueError("proxy response is not successful stk_mins")
    meta = payload.get("meta") or {}
    if meta.get("partial") is True or meta.get("coverage") not in (None, "complete"):
        raise ValueError("proxy minute coverage is not complete")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("proxy response has no data mapping")
    fields, items = data.get("fields"), data.get("items")
    if not isinstance(fields, list) or not FIELDS.issubset(fields) or not isinstance(items, list):
        raise ValueError("proxy minute fields/items contract changed")
    if payload.get("count") != len(items):
        raise ValueError("proxy minute count disagrees with items")

    canonical = {stamp[-4:-2] + ":" + stamp[-2:] for stamp in expected_times(market_date)}
    source_starts = set(_source_start_times(market_date).values())
    date_prefix = market_date + " "
    seen: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, list) or len(item) != len(fields):
            raise ValueError("malformed proxy minute item")
        row = dict(zip(fields, item, strict=True))
        stamp = str(row["trade_time"])
        if (
            row["ts_code"] != symbol
            or row["freq"] != "1MIN"
            or not stamp.startswith(date_prefix)
            or len(stamp) != 19
            or not stamp.endswith(":00")
        ):
            raise ValueError("proxy minute symbol/frequency/date mismatch")
        hhmm = stamp[11:16]
        if hhmm in seen:
            raise ValueError("duplicate proxy minute timestamp")
        # The relay may expose call-auction observations and repeated quote
        # snapshots during the lunch break or after the close. They are retained
        # in the immutable raw response but are never admitted to the candidate.
        admitted_observation = (
            hhmm in canonical
            or hhmm in source_starts
            or "09:25" <= hhmm <= "09:30"
            or "11:30" <= hhmm <= "12:59"
            or "15:00" <= hhmm <= "15:59"
        )
        if not admitted_observation:
            raise ValueError("out-of-session proxy minute timestamp")
        for name in (*MATERIAL_FIELDS, "amount"):
            row[name] = None if row[name] is None else _decimal(row[name])
        if _material_complete(row) and (
            row["vol"] < 0
            or (row["amount"] is not None and row["amount"] < 0)
            or row["high"] < max(row["open"], row["close"], row["low"])
            or row["low"] > min(row["open"], row["close"], row["high"])
        ):
            raise ValueError("invalid proxy minute OHLCVA")
        seen[hhmm] = row
    return fields, items, seen


def _qualify_start_labeled(
    seen: dict[str, dict[str, Any]],
    *,
    market_date: str,
    daily_volume: Decimal,
    daily_close: Decimal,
    first_5m_volume: Any | None,
) -> dict[str, Any]:
    mapping = _source_start_times(market_date)
    source_times = set(mapping.values())
    missing = source_times - set(seen)
    if missing:
        raise ValueError(f"proxy interval-start grid missing {len(missing)} minutes")
    incomplete = [t for t in source_times if not _material_complete(seen[t])]
    if incomplete:
        raise ValueError(
            f"proxy interval-start grid has {len(incomplete)} incomplete material minutes"
        )
    source_volume = sum((seen[t]["vol"] for t in source_times), Decimal(0))
    if source_volume != daily_volume:
        raise ValueError(
            f"proxy interval-start daily volume mismatch: delta={source_volume - daily_volume}"
        )
    close = seen[mapping["15:00"]]["close"]
    if abs(close - daily_close) > Decimal("0.011"):
        raise ValueError("proxy interval-start/daily close mismatch")
    first = sum(
        (seen[mapping[t]]["vol"] for t in mapping if "09:31" <= t <= "09:35"),
        Decimal(0),
    )
    reference_first = _decimal(first_5m_volume) if first_5m_volume is not None else None
    return {
        "time_label_mode": "interval_start_shifted_plus_1m",
        "auction_mode": "source_interval_start_shifted_plus_1m",
        "first_5m_volume_delta": (
            str(first - reference_first) if reference_first is not None else None
        ),
        "source_close": str(close),
        "excluded_observation_rows": len(seen) - len(source_times),
    }


def qualify_payload(
    payload: dict[str, Any],
    *,
    symbol: str,
    market_date: str,
    daily_volume: Any,
    daily_close: Any,
    first_5m_volume: Any | None,
) -> dict[str, Any]:
    """Check source-faithful rows without adjusting any reported shares.

    The proxy supports both the historical minute-end convention and an
    interval-start convention observed on the replacement relay. Only a complete
    fixed 240-row session can be selected. Other admitted observations remain in
    immutable raw evidence but are excluded before normalization; exact sealed
    daily volume and close still license the selected interpretation. No residual
    is spread over minute bars.
    """

    _, items, seen = _parse_rows(payload, symbol=symbol, market_date=market_date)
    code = symbol.split(".")[0]
    grid = {
        date_stamp[-4:-2] + ":" + date_stamp[-2:]: date_stamp
        for date_stamp in expected_times(market_date)
    }
    preopen = [seen[t] for t in ("09:25", "09:26", "09:27", "09:28", "09:29") if t in seen]
    if preopen and (
        len(preopen) != 5
        or any(
            any(
                row[name] != preopen[0][name]
                for name in ("open", "high", "low", "close", "vol", "amount")
            )
            for row in preopen[1:]
        )
    ):
        raise ValueError("unexpected preopen observations are not identical snapshots")
    daily = _decimal(daily_volume)
    close = _decimal(daily_close)
    missing = set(grid) - set(seen)
    end_error: ValueError | None = None
    end_result: dict[str, Any] | None = None
    if not missing:
        auction = seen.get("09:30")
        incomplete = [t for t in grid if not _material_complete(seen[t])]
        if incomplete:
            end_error = ValueError(
                f"proxy session grid has {len(incomplete)} incomplete material minutes"
            )
        else:
            first = sum((seen[t]["vol"] for t in grid if "09:31" <= t <= "09:35"), Decimal(0))
            normal = sum((seen[t]["vol"] for t in grid), Decimal(0))
            reference_first = _decimal(first_5m_volume) if first_5m_volume is not None else None
            usable_auction = auction if auction and _material_complete(auction) else None
            auction_volume = usable_auction["vol"] if usable_auction else Decimal(0)
            include = normal + auction_volume == daily
            exclude = normal == daily and reference_first is not None and first == reference_first
        if not incomplete and (include or exclude):
            auction_mode = (
                "merged_into_0931"
                if include and usable_auction and auction_volume
                else "zero_or_absent_0930"
                if include
                else "duplicate_0930_excluded"
            )
            first_5m_delta = (
                first + auction_volume - reference_first
                if include and reference_first is not None
                else Decimal(0)
                if exclude
                else None
            )
            if abs(seen["15:00"]["close"] - close) <= Decimal("0.011"):
                end_result = {
                    "time_label_mode": "interval_end_native",
                    "auction_mode": auction_mode,
                    "first_5m_volume_delta": (
                        str(first_5m_delta) if first_5m_delta is not None else None
                    ),
                    "source_close": str(seen["15:00"]["close"]),
                    "excluded_observation_rows": len(seen)
                    - len(grid)
                    - (1 if usable_auction and auction_mode == "merged_into_0931" else 0),
                }
            else:
                end_error = ValueError("proxy/daily close mismatch")
        elif not incomplete:
            first_with_delta = (
                first + auction_volume - reference_first
                if reference_first is not None
                else "unavailable"
            )
            first_without_delta = (
                first - reference_first if reference_first is not None else "unavailable"
            )
            end_error = ValueError(
                "proxy daily/native-5m volume mismatch: "
                f"with_auction_delta={normal + auction_volume - daily}, "
                f"without_auction_delta={normal - daily}, "
                f"first_5m_with_delta={first_with_delta}, "
                f"first_5m_without_delta={first_without_delta}"
            )
    else:
        end_error = ValueError(f"proxy session grid missing {len(missing)} minutes")

    start_result: dict[str, Any] | None = None
    start_error: ValueError | None = None
    try:
        start_result = _qualify_start_labeled(
            seen,
            market_date=market_date,
            daily_volume=daily,
            daily_close=close,
            first_5m_volume=first_5m_volume,
        )
    except ValueError as exc:
        start_error = exc

    if end_result is not None and start_result is not None:
        start_mapping = _source_start_times(market_date)
        if any(
            any(
                seen[canonical][name] != seen[start_mapping[canonical]][name]
                for name in MATERIAL_FIELDS
            )
            for canonical in grid
        ):
            raise ValueError("ambiguous proxy minute time-label interpretation")
        result = end_result
    elif end_result is not None:
        result = end_result
    elif start_result is not None:
        result = start_result
    else:
        assert end_error is not None and start_error is not None
        if set(_source_start_times(market_date).values()).issubset(seen):
            raise start_error from None
        raise end_error from None
    return {
        "status": "qualified_candidate_not_published",
        "provider": "paid-tushare-style-proxy-stk_mins",
        "stock_code": code,
        "market_date": market_date,
        "source_rows": len(items),
        "session_rows": len(grid),
        "preopen_duplicate_snapshots_excluded": len(preopen),
        **result,
        "source_volume": str(daily),
        "amount_status": "source_present_unpublished_pending_separate_quality_review",
    }


def normalized_candidate(
    raw: bytes,
    *,
    symbol: str,
    market_date: str,
    daily_volume: Any,
    daily_close: Any,
    first_5m_volume: Any | None,
    raw_ref: str,
) -> MinuteCandidate:
    """Build one quarantined, whole-security candidate from exact source bytes.

    The 09:30 auction is merged only when the daily/5m qualification above
    licenses it. Minute amount remains null until its own quality contract is
    accepted, despite the relay exposing an amount field in its raw response.
    """
    payload = json.loads(raw)
    result = qualify_payload(
        payload,
        symbol=symbol,
        market_date=market_date,
        daily_volume=daily_volume,
        daily_close=daily_close,
        first_5m_volume=first_5m_volume,
    )
    fields = payload["data"]["fields"]
    by_time = {
        str(row[fields.index("trade_time")])[11:16]: dict(zip(fields, row, strict=True))
        for row in payload["data"]["items"]
    }
    auction = by_time.get("09:30")
    start_mapping = _source_start_times(market_date)
    rows: list[dict[str, Any]] = []
    for stamp in expected_times(market_date):
        hhmm = stamp[-4:-2] + ":" + stamp[-2:]
        source = by_time[
            start_mapping[hhmm]
            if result["time_label_mode"] == "interval_start_shifted_plus_1m"
            else hhmm
        ]
        values = {name: _decimal(source[name]) for name in ("open", "high", "low", "close", "vol")}
        if hhmm == "09:31" and auction and result["auction_mode"] == "merged_into_0931":
            values["open"] = _decimal(auction["open"])
            values["high"] = max(values["high"], _decimal(auction["high"]))
            values["low"] = min(values["low"], _decimal(auction["low"]))
            values["vol"] += _decimal(auction["vol"])
        rows.append(
            {
                "stock_code": symbol.split(".")[0],
                "ts": f"{market_date} {hhmm}:00",
                "open": values["open"],
                "high": values["high"],
                "low": values["low"],
                "close": values["close"],
                "volume": values["vol"],
                "amount": None,
            }
        )
    return MinuteCandidate(
        provider="paid-tushare-style-proxy-stk_mins",
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        raw_ref=raw_ref,
        rows=tuple(rows),
        auction_provenance=result["auction_mode"],
        amount_status="source_present_unpublished_pending_separate_quality_review",
    )


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".tmp.{os.getpid()}")
    temp.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def select_canary_symbols(raw: str, traded_codes: list[str]) -> list[str]:
    requested = [item.strip() for item in raw.split(",")]
    if (
        len(requested) not in {1, 2, 3}
        or len(requested) != len(set(requested))
        or any(code not in traded_codes for code in requested)
    ):
        raise ValueError("canary requires one to three unique, traded sealed-universe codes")
    return requested


def _audit_symbol(
    code: str,
    *,
    day: str,
    key: str,
    daily: dict[str, Any],
    first_5m_volume: Any,
    output_dir: Path,
) -> dict[str, Any]:
    import requests

    symbol = code + (".SH" if code.startswith("6") else ".SZ")
    receipt: dict[str, Any] = {"stock_code": code, "market_date": day}
    try:
        response = requests.get(
            ENDPOINT,
            params={
                "ts_code": symbol,
                "freq": "1min",
                "start_date": day + " 09:00:00",
                "end_date": day + " 16:00:00",
            },
            headers={"X-API-Key": key},
            timeout=25,
        )
        receipt["http_status"] = response.status_code
        receipt["request_id"] = response.headers.get("x-request-id")
        if response.status_code == 202:
            receipt.update(status="source_pending", retry_after=response.headers.get("retry-after"))
        elif response.status_code == 429:
            receipt.update(status="rate_limited", retry_after=response.headers.get("retry-after"))
        elif response.status_code != 200:
            receipt.update(status="http_error")
        else:
            raw = response.content
            receipt["source_raw_sha256"] = hashlib.sha256(raw).hexdigest()
            raw_path = output_dir / "raw" / f"{code}.json.gz"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            with (
                raw_path.open("wb") as sink,
                gzip.GzipFile(fileobj=sink, mode="wb", mtime=0) as zipped,
            ):
                zipped.write(raw)
            try:
                receipt.update(
                    qualify_payload(
                        response.json(),
                        symbol=symbol,
                        market_date=day,
                        daily_volume=daily["volume"],
                        daily_close=daily["close"],
                        first_5m_volume=first_5m_volume,
                    )
                )
            except (ValueError, KeyError) as exc:
                receipt.update(status="unqualified", reason=str(exc)[:350])
    except requests.RequestException as exc:
        receipt.update(status="transport_error", reason=type(exc).__name__)
    _write_json(output_dir / "receipts" / f"{code}.json", receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description="Quarantine-only paid 1m source coverage audit")
    parser.add_argument("--market-date", required=True, type=date.fromisoformat)
    parser.add_argument("--daily-path", required=True, type=Path)
    parser.add_argument("--status-path", required=True, type=Path)
    parser.add_argument("--five-path", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--rate-per-second", type=float, default=1)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--wall-budget-seconds", type=int, default=240)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--full-market",
        action="store_true",
        help="Explicitly authorize a bounded full-market audit",
    )
    parser.add_argument(
        "--symbols",
        help="Comma-separated, at most three traded codes for a bounded canary audit",
    )
    args = parser.parse_args()
    if (
        args.rate_per_second <= 0
        or args.rate_per_second > 5
        or args.workers < 1
        or args.workers > 8
        or args.wall_budget_seconds < 60
        or (args.limit is not None and args.limit < 1)
        or (args.symbols is not None and args.limit is not None)
        or (args.full_market and (args.symbols is not None or args.limit is not None))
        or (not args.full_market and args.symbols is None and args.limit is None)
    ):
        parser.error("choose exactly one of --symbols, --limit, or --full-market")
    import pyarrow.parquet as pq

    daily_rows = pq.read_table(
        args.daily_path, columns=["stock_code", "market_date", "volume", "close"]
    ).to_pylist()
    status_rows = pq.read_table(
        args.status_path, columns=["stock_code", "market_date", "tradestatus"]
    ).to_pylist()
    first_rows = pq.read_table(args.five_path, columns=["stock_code", "ts", "volume"]).to_pylist()
    day = args.market_date.isoformat()
    daily = {row["stock_code"]: row for row in daily_rows}
    status = {row["stock_code"]: row for row in status_rows}
    first = {
        row["stock_code"]: row["volume"] for row in first_rows if str(row["ts"])[11:16] == "09:35"
    }
    if (
        len(daily) != len(daily_rows)
        or len(status) != len(status_rows)
        or set(daily) != set(status)
        or any(row["market_date"] != day for row in daily_rows + status_rows)
    ):
        raise ValueError("sealed daily/status universe or date mismatch")
    codes = [code for code in sorted(daily) if str(status[code]["tradestatus"]) == "1"]
    if args.symbols is not None:
        codes = select_canary_symbols(args.symbols, codes)
    elif args.limit is not None:
        codes = codes[: args.limit]
    if any(code not in first for code in codes):
        raise ValueError("native 5m opening bucket missing for selected symbols")
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise ValueError("audit output directory must be empty")
    args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    print("READY_FOR_KEY", flush=True)
    key = sys.stdin.readline().strip()
    if not key:
        raise ValueError("empty API key")
    counts: Counter[str] = Counter()
    started = time.monotonic()
    next_call = time.monotonic()
    processed = 0
    stop_reason = "completed"

    def checkpoint() -> None:
        _write_json(
            args.output_dir / "summary.json",
            {
                "market_date": day,
                "selected_traded_symbols": len(codes),
                "processed": processed,
                "counts": counts,
                "stop_reason": stop_reason,
                "elapsed_seconds": round(time.monotonic() - started, 1),
                "quarantine_only": True,
                "production_publication": False,
            },
        )

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for start in range(0, len(codes), 10):
                if time.monotonic() - started >= args.wall_budget_seconds:
                    stop_reason = "stopped_on_wall_budget"
                    break
                batch = codes[start : start + 10]
                futures = []
                for code in batch:
                    time.sleep(max(0.0, next_call - time.monotonic()))
                    next_call = time.monotonic() + 1 / args.rate_per_second
                    futures.append(
                        pool.submit(
                            _audit_symbol,
                            code,
                            day=day,
                            key=key,
                            daily=daily[code],
                            first_5m_volume=first[code],
                            output_dir=args.output_dir,
                        )
                    )
                batch_counts: Counter[str] = Counter()
                for future in as_completed(futures):
                    receipt = future.result()
                    counts[receipt["status"]] += 1
                    batch_counts[receipt["status"]] += 1
                processed = start + len(batch)
                checkpoint()
                print(
                    json.dumps(
                        {"processed": processed, "total": len(codes), "counts": counts},
                        sort_keys=True,
                    ),
                    flush=True,
                )
                if counts["rate_limited"]:
                    stop_reason = "stopped_on_provider_rate_limit"
                    break
                if batch_counts["source_pending"] >= max(3, len(batch) // 2):
                    stop_reason = "stopped_on_provider_pending_batch"
                    break
                if batch_counts["http_error"]:
                    stop_reason = "stopped_on_provider_http_error_batch"
                    break
                if time.monotonic() - started >= args.wall_budget_seconds:
                    stop_reason = "stopped_on_wall_budget"
                    break
    except BaseException:
        stop_reason = "interrupted_before_terminal_batch"
        raise
    finally:
        key = ""
        checkpoint()
    return 0 if stop_reason == "completed" else 3


if __name__ == "__main__":
    raise SystemExit(main())

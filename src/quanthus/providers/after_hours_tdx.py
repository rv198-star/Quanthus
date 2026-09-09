"""Acquire sparse 15:05-15:30 after-hours fixed-price matches from TDX."""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from quanthus.providers.common import (
    atomic_json,
    code6,
    now_shanghai_iso,
    read_equity_universe,
    receipt_schema,
    sha256_bytes,
    write_parquet,
)
from quanthus.providers.minute_tdx import tdx_client

PROVIDER_REVISION = "after-hours-fixed-price-trades-v1"
PAGE_SIZE = 800
MATCHING_START = "15:05:00"
MATCHING_END = "15:30:00"
EASTMONEY_DETAILS_URL = "https://push2delay.eastmoney.com/api/qt/stock/details/get"
EASTMONEY_TRENDS_URL = "https://push2delay.eastmoney.com/api/qt/stock/trends2/get"
EASTMONEY_TOKEN = "fa5fd1943c7b386f172d6893dbbd1d0c"
EASTMONEY_WINDOWS = (100, 200, 400, 800, 1600)
TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q={symbol}"


def trades_schema() -> Any:
    import pyarrow as pa  # type: ignore[import-untyped]

    return pa.schema(
        [
            ("event_id", pa.string()),
            ("security_id", pa.string()),
            ("stock_code", pa.string()),
            ("security_type", pa.string()),
            ("market_date", pa.string()),
            ("match_time", pa.string()),
            ("price", pa.float64()),
            ("raw_volume_lots", pa.float64()),
            ("quantity_units", pa.float64()),
            ("amount", pa.float64()),
            ("buy_or_sell_code", pa.int64()),
            ("provider_offset", pa.int64()),
            ("provider_index", pa.int64()),
            ("source", pa.string()),
            ("source_revision", pa.string()),
            ("acquired_at", pa.string()),
        ]
    )


def _disconnect(client: Any) -> None:
    with contextlib.suppress(Exception):
        client.client.disconnect()


def _clock(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) == 5 and text[2] == ":":
        return f"{text}:00"
    if len(text) >= 8 and text[2] == ":" and text[5] == ":":
        return text[:8]
    if len(text) == 6 and text.isdigit():
        return f"{text[:2]}:{text[2:4]}:{text[4:]}"
    raise ValueError(f"unsupported transaction time {text!r}")


def _frame_records(frame: Any) -> list[dict[str, Any]]:
    if frame is None:
        return []
    empty = getattr(frame, "empty", None)
    if empty is True:
        return []
    to_dict = getattr(frame, "to_dict", None)
    if callable(to_dict):
        return [dict(row) for row in to_dict("records")]
    if isinstance(frame, Sequence):
        return [dict(row) for row in frame]
    raise TypeError(f"unsupported transaction response {type(frame).__name__}")


def _eastmoney_secid(code: str) -> str:
    market = "1" if code.startswith(("5", "6", "9")) else "0"
    return f"{market}.{code}"


def _eastmoney_json(
    url: str,
    params: dict[str, str],
    *,
    timeout: float = 12.0,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    request = Request(
        f"{url}?{urlencode(params)}",
        headers={
            "Referer": "https://quote.eastmoney.com/",
            "User-Agent": "Mozilla/5.0 Quanthus/1.0",
        },
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with opener(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if int(payload.get("rc", -1)) != 0:
                raise RuntimeError(f"Eastmoney rc={payload.get('rc')}")
            return payload
        except Exception as exc:  # noqa: BLE001 - bounded provider retry
            last_error = exc
            if attempt < 2:
                time.sleep(0.25 * (attempt + 1))
    raise RuntimeError(f"Eastmoney request failed: {last_error}")


def _eastmoney_latest_market_date(
    *, opener: Callable[..., Any] = urlopen
) -> str:
    payload = _eastmoney_json(
        EASTMONEY_TRENDS_URL,
        {
            "secid": "0.000001",
            "fields1": "f1,f2,f3,f4,f5,f6,f7,f8",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
            "ndays": "1",
            "iscr": "0",
            "iscca": "0",
            "ut": EASTMONEY_TOKEN,
        },
        opener=opener,
    )
    data = payload.get("data") or {}
    trends = data.get("trends") or []
    if not trends:
        raise RuntimeError("Eastmoney market-date anchor returned no trends")
    latest = str(trends[-1]).split(",", 1)[0][:10]
    if len(latest) != 10:
        raise RuntimeError("Eastmoney market-date anchor is malformed")
    return latest


def _eastmoney_detail_rows(
    code: str,
    limit: int,
    *,
    opener: Callable[..., Any] = urlopen,
) -> list[str]:
    payload = _eastmoney_json(
        EASTMONEY_DETAILS_URL,
        {
            "secid": _eastmoney_secid(code),
            "fields1": "f1",
            "fields2": "f51,f52,f53,f54,f55",
            "pos": f"-{limit}",
            "lmt": str(limit),
            "ut": EASTMONEY_TOKEN,
        },
        opener=opener,
    )
    data = payload.get("data") or {}
    returned = str(data.get("code") or "").zfill(6)
    if returned != code:
        raise RuntimeError(
            f"Eastmoney returned mismatched security {returned!r} for {code}"
        )
    return [str(row) for row in data.get("details") or []]


def _tencent_symbol(code: str) -> str:
    market = "sh" if code.startswith(("5", "6", "9")) else "sz"
    return f"{market}{code}"


def _tencent_zero_activity_evidence(
    code: str,
    market_date: date,
    *,
    opener: Callable[..., Any] = urlopen,
    timeout: float = 12.0,
) -> dict[str, Any]:
    symbol = _tencent_symbol(code)
    request = Request(
        TENCENT_QUOTE_URL.format(symbol=symbol),
        headers={"User-Agent": "Mozilla/5.0 Quanthus/1.0"},
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with opener(request, timeout=timeout) as response:
                text = response.read().decode("gb18030", errors="replace")
            marker = f'v_{symbol}="'
            start = text.find(marker)
            if start < 0:
                raise RuntimeError("Tencent quote response marker is missing")
            payload = text[start + len(marker) :].split('";', 1)[0]
            fields = payload.split("~")
            if len(fields) <= 30:
                raise RuntimeError("Tencent quote response has too few fields")
            returned = str(fields[2]).strip().zfill(6)
            if returned != code:
                raise RuntimeError(
                    f"Tencent returned mismatched security {returned!r} for {code}"
                )
            quote_timestamp = str(fields[30]).strip()
            expected_date = market_date.strftime("%Y%m%d")
            if quote_timestamp[:8] != expected_date:
                raise RuntimeError(
                    "Tencent quote market date "
                    f"{quote_timestamp[:8]!r} does not match requested {expected_date!r}"
                )
            total_volume = float(fields[6] or 0.0)
            if total_volume != 0.0:
                raise RuntimeError(
                    f"Tencent quote reports nonzero total volume {total_volume} for {code}"
                )
            return {
                "provider": "tencent-quote-zero-activity",
                "quote_market_date": market_date.isoformat(),
                "quote_timestamp": quote_timestamp,
                "daily_volume": total_volume,
            }
        except Exception as exc:  # noqa: BLE001 - bounded quote retry
            last_error = exc
            if attempt < 2:
                time.sleep(0.25 * (attempt + 1))
    raise RuntimeError(f"Tencent zero-activity quote failed: {last_error}")


def collect_symbol_eastmoney(
    *,
    code: str,
    security_type: str,
    market_date: date,
    opener: Callable[..., Any] = urlopen,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    details: list[str] = []
    requested_limit = 0
    requests = 0
    boundary_proved = False
    for requested_limit in EASTMONEY_WINDOWS:
        details = _eastmoney_detail_rows(code, requested_limit, opener=opener)
        requests += 1
        if not details:
            boundary_proved = True
            break
        clocks = [_clock(row.split(",", 1)[0]) for row in details]
        if min(clocks) < MATCHING_START:
            boundary_proved = True
            break
    if not boundary_proved:
        raise RuntimeError(
            f"Eastmoney detail window did not cross {MATCHING_START}; "
            f"limit={requested_limit} rows={len(details)}"
        )

    acquired_at = now_shanghai_iso()
    output: list[dict[str, Any]] = []
    for index, raw in enumerate(details):
        fields = raw.split(",")
        if len(fields) < 5:
            raise RuntimeError("Eastmoney detail row has fewer than 5 fields")
        clock = _clock(fields[0])
        if not (MATCHING_START <= clock <= MATCHING_END):
            continue
        price = float(fields[1])
        lots = float(fields[2])
        quantity = lots * 100.0
        side = int(fields[4] or 0)
        raw_identity = json.dumps(
            {
                "source": "eastmoney.push2delay.details",
                "code": code,
                "date": market_date.isoformat(),
                "limit": requested_limit,
                "index": index,
                "time": clock,
                "price": price,
                "vol": lots,
                "buyorsell": side,
            },
            ensure_ascii=True,
            sort_keys=True,
        ).encode("utf-8")
        output.append(
            {
                "event_id": sha256_bytes(raw_identity),
                "security_id": code,
                "stock_code": code,
                "security_type": security_type,
                "market_date": market_date.isoformat(),
                "match_time": f"{market_date.isoformat()}T{clock}+08:00",
                "price": price,
                "raw_volume_lots": lots,
                "quantity_units": quantity,
                "amount": price * quantity,
                "buy_or_sell_code": side,
                "provider_offset": -requested_limit,
                "provider_index": index,
                "source": "eastmoney.push2delay.details",
                "source_revision": PROVIDER_REVISION,
                "acquired_at": acquired_at,
            }
        )
    output.sort(
        key=lambda row: (
            str(row["match_time"]),
            int(row["provider_offset"]),
            int(row["provider_index"]),
        )
    )
    return output, {
        "pages": requests,
        "provider_rows": len(details),
        "after_hours_rows": len(output),
        "requested_limit": requested_limit,
        "acquired_at": acquired_at,
    }


def collect_symbol(
    client: Any,
    *,
    code: str,
    security_type: str,
    market_date: date,
    max_pages: int = 10,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if max_pages < 1:
        raise ValueError("max_pages must be positive")
    raw_rows: list[tuple[int, int, dict[str, Any]]] = []
    pages = 0
    crossed_boundary = False
    for page in range(max_pages):
        offset = page * PAGE_SIZE
        frame = client.transactions(
            symbol=code,
            start=offset,
            offset=PAGE_SIZE,
            date=market_date.strftime("%Y%m%d"),
        )
        records = _frame_records(frame)
        pages += 1
        for index, row in enumerate(records):
            raw_rows.append((offset, index, row))
        if not records:
            crossed_boundary = True
            break
        clocks = [_clock(row.get("time")) for row in records]
        if min(clocks) < MATCHING_START or len(records) < PAGE_SIZE:
            crossed_boundary = True
            break
    if raw_rows and not crossed_boundary:
        earliest = min(_clock(row.get("time")) for _, _, row in raw_rows)
        if earliest >= MATCHING_START:
            raise RuntimeError(
                f"transaction paging truncated before {MATCHING_START}; pages={max_pages}"
            )

    acquired_at = now_shanghai_iso()
    output: list[dict[str, Any]] = []
    for offset, index, row in raw_rows:
        clock = _clock(row.get("time"))
        if not (MATCHING_START <= clock <= MATCHING_END):
            continue
        price = float(row.get("price") or 0.0)
        lots = float(row.get("vol") or row.get("volume") or 0.0)
        quantity = lots * 100.0
        raw_identity = json.dumps(
            {
                "code": code,
                "date": market_date.isoformat(),
                "offset": offset,
                "index": index,
                "time": clock,
                "price": price,
                "vol": lots,
                "buyorsell": row.get("buyorsell"),
            },
            ensure_ascii=True,
            sort_keys=True,
        ).encode("utf-8")
        output.append(
            {
                "event_id": sha256_bytes(raw_identity),
                "security_id": code,
                "stock_code": code,
                "security_type": security_type,
                "market_date": market_date.isoformat(),
                "match_time": f"{market_date.isoformat()}T{clock}+08:00",
                "price": price,
                "raw_volume_lots": lots,
                "quantity_units": quantity,
                "amount": price * quantity,
                "buy_or_sell_code": int(row.get("buyorsell") or 0),
                "provider_offset": offset,
                "provider_index": index,
                "source": "tdx.get_history_transaction_data",
                "source_revision": PROVIDER_REVISION,
                "acquired_at": acquired_at,
            }
        )
    output.sort(
        key=lambda row: (
            str(row["match_time"]),
            int(row["provider_offset"]),
            int(row["provider_index"]),
        )
    )
    return output, {
        "pages": pages,
        "provider_rows": len(raw_rows),
        "after_hours_rows": len(output),
        "acquired_at": acquired_at,
    }


def _etf_universe(
    bs_module: Any | None = None,
    *,
    max_attempts: int = 3,
    retry_delay_seconds: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> list[str]:
    if bs_module is None:
        import baostock as loaded_bs  # type: ignore[import-not-found]

        bs_module = loaded_bs
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")

    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        rows: list[list[str]] = []
        try:
            login = bs_module.login()
            if str(login.error_code) != "0":
                raise RuntimeError(f"baostock login failed: {login.error_msg}")
            result = bs_module.query_stock_basic()
            if str(result.error_code) != "0":
                raise RuntimeError(
                    f"baostock query_stock_basic failed: {result.error_msg}"
                )
            while result.next():
                rows.append(result.get_row_data())
            return sorted(
                {
                    code6(row[0])
                    for row in rows
                    if len(row) >= 6
                    and row[4] == "5"
                    and row[5] == "1"
                    and str(row[0]).startswith(("sh.", "sz."))
                }
            )
        except Exception as exc:  # noqa: BLE001 - bounded provider retry
            last_error = exc
        finally:
            with contextlib.suppress(Exception):
                bs_module.logout()
        if attempt < max_attempts and retry_delay_seconds > 0:
            sleep(retry_delay_seconds)

    raise RuntimeError(
        f"baostock ETF universe failed after {max_attempts} attempts: {last_error}"
    ) from last_error


def collect_after_hours(
    equities: Sequence[str],
    etfs: Sequence[str],
    market_date: date,
    *,
    max_pages: int = 10,
    sleep_seconds: float = 0.0,
    client_factory: Callable[[], tuple[Any, str]] = tdx_client,
    eastmoney_opener: Callable[..., Any] = urlopen,
    quote_opener: Callable[..., Any] = urlopen,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    typed_universe = [
        *((code, "equity") for code in sorted(set(equities))),
        *((code, "etf") for code in sorted(set(etfs) - set(equities))),
    ]
    rows: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    client: Any | None = None
    fallback = False
    try:
        client, server = client_factory()
    except RuntimeError:
        latest_market_date = _eastmoney_latest_market_date(opener=eastmoney_opener)
        if latest_market_date != market_date.isoformat():
            raise RuntimeError(
                "Eastmoney after-hours fallback latest market date "
                f"{latest_market_date} does not match requested {market_date.isoformat()}"
            ) from None
        fallback = True
        server = "push2delay.eastmoney.com"

    try:
        for code, security_type in typed_universe:
            started = time.monotonic()
            attempts = 0
            error: Exception | None = None
            selected: list[dict[str, Any]] = []
            meta: dict[str, Any] = {}
            if fallback:
                attempts = 1
                try:
                    selected, meta = collect_symbol_eastmoney(
                        code=code,
                        security_type=security_type,
                        market_date=market_date,
                        opener=eastmoney_opener,
                    )
                except Exception as exc:  # noqa: BLE001 - request retry is bounded in helper
                    error = exc
                    if security_type == "etf":
                        try:
                            meta = _tencent_zero_activity_evidence(
                                code,
                                market_date,
                                opener=quote_opener,
                            )
                            selected = []
                            error = None
                            attempts = 2
                        except Exception as quote_exc:  # noqa: BLE001 - fail closed on both sources
                            error = RuntimeError(
                                f"{exc}; zero-activity fallback failed: {quote_exc}"
                            )
            else:
                assert client is not None
                for attempts in (1, 2):
                    try:
                        selected, meta = collect_symbol(
                            client,
                            code=code,
                            security_type=security_type,
                            market_date=market_date,
                            max_pages=max_pages,
                        )
                        error = None
                        break
                    except Exception as exc:  # noqa: BLE001 - one bounded provider retry
                        error = exc
                        _disconnect(client)
                        if attempts == 1:
                            client, server = client_factory()
            acquired_at = now_shanghai_iso()
            if error is not None:
                status = "failed"
                reason = "provider_request_failed"
                detail = f"{type(error).__name__}: {error}"
            elif selected:
                status = "success_nonempty"
                reason = "ok"
                detail = None
                rows.extend(selected)
            else:
                status = "justified_empty"
                if meta.get("provider") == "tencent-quote-zero-activity":
                    reason = "same_day_zero_volume_proves_no_after_hours_matches"
                    detail = (
                        "Tencent same-day quote reported zero total trading volume; "
                        "after-hours fixed-price matches are therefore impossible"
                    )
                else:
                    reason = "successful_query_no_after_hours_matches"
                    provider_name = "Eastmoney" if fallback else "TDX"
                    detail = (
                        f"{provider_name} transaction request completed and returned no matches "
                        "inside 15:05-15:30"
                    )
            receipt_uses_tencent = meta.get("provider") == "tencent-quote-zero-activity"
            receipts.append(
                {
                    "dataset": "after_hours_fixed_price_trades",
                    "security_id": code,
                    "security_type": security_type,
                    "provider": (
                        "tencent:qt.gtimg.cn"
                        if receipt_uses_tencent
                        else (f"eastmoney:{server}" if fallback else f"tdx:{server}")
                    ),
                    "request_json": json.dumps(
                        {
                            "operation": (
                                "quote_zero_activity"
                                if receipt_uses_tencent
                                else ("details" if fallback else "transactions")
                            ),
                            "symbol": code,
                            "date": market_date.strftime("%Y%m%d"),
                            "matching_start": MATCHING_START,
                            "matching_end": MATCHING_END,
                            "boundary_policy": (
                                "same_day_zero_volume_proves_no_after_hours"
                                if receipt_uses_tencent
                                else (
                                    "expand_latest_window_until_before_1505"
                                    if fallback
                                    else "page_until_before_1505"
                                )
                            ),
                        },
                        sort_keys=True,
                    ),
                    "status": status,
                    "reason_code": reason,
                    "response_rows": len(selected),
                    "response_bytes": 0,
                    "response_sha256": None,
                    "attempts": attempts,
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "acquired_at": acquired_at,
                    "detail": detail,
                    **{f"provider_{key}": value for key, value in meta.items()},
                }
            )
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
    finally:
        if client is not None:
            _disconnect(client)
    rows.sort(
        key=lambda row: (
            str(row["security_id"]),
            str(row["match_time"]),
            int(row["provider_offset"]),
            int(row["provider_index"]),
        )
    )
    return rows, receipts, {
        "provider": "eastmoney" if fallback else "tdx",
        "server": server,
        "equity_count": len(set(equities)),
        "etf_count": len(set(etfs) - set(equities)),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="quanthus-after-hours-tdx")
    parser.add_argument("--market-date", required=True)
    parser.add_argument("--stock-list", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--receipts", required=True)
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--sleep", type=float, default=0.0)
    args = parser.parse_args(argv)

    market_date = date.fromisoformat(args.market_date)
    output = Path(args.output).resolve()
    receipts_path = Path(args.receipts).resolve()
    try:
        equities = read_equity_universe(Path(args.stock_list).resolve())
        etfs = _etf_universe()
        if not equities or not etfs:
            raise ValueError("after-hours universe requires nonempty equities and ETFs")
        rows, receipts, metadata = collect_after_hours(
            equities,
            etfs,
            market_date,
            max_pages=args.max_pages,
            sleep_seconds=args.sleep,
        )
        row_count = write_parquet(rows, output, trades_schema())
        receipt_count = write_parquet(receipts, receipts_path, receipt_schema())
        failed = sum(1 for receipt in receipts if receipt.get("status") == "failed")
        completed_at = now_shanghai_iso()
        summary: dict[str, Any] = {
            "schema_version": 1,
            "provider_revision": PROVIDER_REVISION,
            "dataset": "after_hours_fixed_price_trades",
            "market_date": market_date.isoformat(),
            "output": str(output),
            "receipts": str(receipts_path),
            "row_count": row_count,
            "receipt_count": receipt_count,
            "expected_receipt_count": metadata["equity_count"] + metadata["etf_count"],
            "failed_receipts": failed,
            "justified_empty_receipts": sum(
                1 for receipt in receipts if receipt.get("status") == "justified_empty"
            ),
            "completed_at": completed_at,
            "matching_start": MATCHING_START,
            "matching_end": MATCHING_END,
            "all_empty_is_legal_with_complete_receipts": True,
            **metadata,
        }
        atomic_json(output.with_suffix(output.suffix + ".meta.json"), summary)
        print(json.dumps(summary, ensure_ascii=True, sort_keys=True))
        expected_receipts = int(metadata["equity_count"]) + int(metadata["etf_count"])
        return 0 if failed == 0 and receipt_count == expected_receipts else 1
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"after-hours collector failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

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


def _etf_universe(bs_module: Any | None = None) -> list[str]:
    if bs_module is None:
        import baostock as loaded_bs  # type: ignore[import-not-found]

        bs_module = loaded_bs

    login = bs_module.login()
    if str(login.error_code) != "0":
        raise RuntimeError(f"baostock login failed: {login.error_msg}")
    rows: list[list[str]] = []
    try:
        result = bs_module.query_stock_basic()
        if str(result.error_code) != "0":
            raise RuntimeError(f"baostock query_stock_basic failed: {result.error_msg}")
        while result.next():
            rows.append(result.get_row_data())
    finally:
        bs_module.logout()
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


def collect_after_hours(
    equities: Sequence[str],
    etfs: Sequence[str],
    market_date: date,
    *,
    max_pages: int = 10,
    sleep_seconds: float = 0.0,
    client_factory: Callable[[], tuple[Any, str]] = tdx_client,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    typed_universe = [
        *((code, "equity") for code in sorted(set(equities))),
        *((code, "etf") for code in sorted(set(etfs) - set(equities))),
    ]
    rows: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    client, server = client_factory()
    try:
        for code, security_type in typed_universe:
            started = time.monotonic()
            attempts = 0
            error: Exception | None = None
            selected: list[dict[str, Any]] = []
            meta: dict[str, Any] = {}
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
                reason = "successful_query_no_after_hours_matches"
                detail = (
                    "TDX transaction request completed and returned no matches "
                    "inside 15:05-15:30"
                )
            receipts.append(
                {
                    "dataset": "after_hours_fixed_price_trades",
                    "security_id": code,
                    "security_type": security_type,
                    "provider": f"tdx:{server}",
                    "request_json": json.dumps(
                        {
                            "operation": "transactions",
                            "symbol": code,
                            "date": market_date.strftime("%Y%m%d"),
                            "matching_start": MATCHING_START,
                            "matching_end": MATCHING_END,
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
        "provider": "tdx",
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

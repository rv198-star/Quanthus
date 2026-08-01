"""Acquire a bounded unadjusted daily-history repair from BaoStock.

This collector is intentionally universe-driven.  It is used for immutable repair
packages and never mutates the normal market-daily lane or its stock list.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any, cast

from quanthus.providers.baostock_transport import set_socket_timeout
from quanthus.providers.common import (
    atomic_json,
    callable_identity,
    checkpoint_part_identities,
    checkpoint_parts_reusable,
    code6,
    merge_parquet_parts,
    now_shanghai_iso,
    parquet_row_count,
    read_equity_universe,
    receipt_schema,
    sha256_bytes,
    verify_receipt_identities,
    write_parquet,
)

PROVIDER_REVISION = "baostock-daily-history-hotfix-v1"
FIELDS = "date,code,open,high,low,close,volume,amount,adjustflag,turn,pctChg,tradestatus,isST"


class DailyHistoryQueryError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(detail)
        self.reason_code = reason_code


def daily_schema() -> Any:
    import pyarrow as pa  # type: ignore[import-untyped]

    return pa.schema(
        [
            ("security_id", pa.string()),
            ("stock_code", pa.string()),
            ("market_date", pa.string()),
            ("date", pa.string()),
            ("open", pa.float64()),
            ("high", pa.float64()),
            ("low", pa.float64()),
            ("close", pa.float64()),
            ("volume", pa.float64()),
            ("amount", pa.float64()),
            ("turnover_rate", pa.float64()),
            ("pct_change", pa.float64()),
            ("adj_mode", pa.string()),
            ("adj_version", pa.string()),
            ("source", pa.string()),
            ("acquired_at", pa.string()),
            ("built_at", pa.string()),
        ]
    )


def status_schema() -> Any:
    import pyarrow as pa

    return pa.schema(
        [
            ("security_id", pa.string()),
            ("stock_code", pa.string()),
            ("market_date", pa.string()),
            ("date", pa.string()),
            ("tradestatus", pa.string()),
            ("isST", pa.string()),
            ("source", pa.string()),
            ("acquired_at", pa.string()),
            ("built_at", pa.string()),
        ]
    )


def _fnum(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    text = str(value).strip()
    if text.lower() in {"", "-", "nan", "none", "null"}:
        return default
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def _bs_code(code: str) -> str:
    return f"sh.{code}" if code.startswith("6") else f"sz.{code}"


def _login(
    bs_module: Any,
    *,
    max_attempts: int,
    socket_timeout_seconds: float,
    sleep: Callable[[float], None],
    configure_timeout: Callable[[float], bool],
) -> int:
    last_error = "unknown provider error"
    for attempt in range(1, max_attempts + 1):
        try:
            login = bs_module.login()
            if str(login.error_code) == "0":
                configure_timeout(socket_timeout_seconds)
                return attempt
            last_error = str(login.error_msg)
        except Exception as exc:  # noqa: BLE001 - bounded provider retry
            last_error = f"{type(exc).__name__}: {exc}"
        with contextlib.suppress(Exception):
            bs_module.logout()
        if attempt < max_attempts:
            sleep(float(attempt))
    raise RuntimeError(f"baostock login failed after {max_attempts} attempts: {last_error}")


def _query_once(
    bs_module: Any,
    code: str,
    start: date,
    end: date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, str]:
    response = bs_module.query_history_k_data_plus(
        _bs_code(code),
        FIELDS,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        frequency="d",
        adjustflag="3",
    )
    if str(response.error_code) != "0":
        raise DailyHistoryQueryError(
            "provider_request_failed",
            f"baostock error={response.error_code}: {response.error_msg}",
        )
    raw_rows: list[list[str]] = []
    while response.next():
        raw_rows.append(response.get_row_data())
    if not raw_rows:
        raise DailyHistoryQueryError(
            "provider_empty_response",
            "BaoStock returned no rows for an in-scope equity and date range",
        )

    acquired_at = now_shanghai_iso()
    bars: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    seen_dates: set[str] = set()
    for raw in raw_rows:
        if len(raw) != 13:
            raise DailyHistoryQueryError(
                "provider_schema_mismatch",
                f"BaoStock returned {len(raw)} fields; expected 13",
            )
        observed_date = str(raw[0])
        observed_code = code6(raw[1])
        if not (start.isoformat() <= observed_date <= end.isoformat()):
            raise DailyHistoryQueryError(
                "provider_date_out_of_range",
                f"provider date {observed_date} is outside {start}..{end}",
            )
        if observed_code != code:
            raise DailyHistoryQueryError(
                "provider_identity_mismatch",
                f"requested {code}, provider returned {observed_code}",
            )
        if observed_date in seen_dates:
            raise DailyHistoryQueryError(
                "provider_duplicate_key",
                f"provider returned duplicate {code}/{observed_date}",
            )
        seen_dates.add(observed_date)
        if str(raw[8]) != "3" or str(raw[11]) not in {"0", "1"}:
            raise DailyHistoryQueryError(
                "provider_contract_mismatch",
                f"unexpected adjustflag/tradestatus for {code}/{observed_date}",
            )
        open_value = _fnum(raw[2])
        high_value = _fnum(raw[3])
        low_value = _fnum(raw[4])
        close_value = _fnum(raw[5])
        volume = _fnum(raw[6], 0.0 if str(raw[11]) == "0" else None)
        amount = _fnum(raw[7], 0.0 if str(raw[11]) == "0" else None)
        numeric = (open_value, high_value, low_value, close_value, volume, amount)
        if any(value is None or value < 0 for value in numeric):
            raise DailyHistoryQueryError(
                "invalid_ohlcv",
                f"invalid numeric value for {code}/{observed_date}",
            )
        assert open_value is not None
        assert high_value is not None
        assert low_value is not None
        assert close_value is not None
        assert volume is not None
        assert amount is not None
        if (
            low_value > high_value
            or not low_value <= open_value <= high_value
            or not low_value <= close_value <= high_value
        ):
            raise DailyHistoryQueryError(
                "invalid_ohlc_order",
                f"invalid OHLC ordering for {code}/{observed_date}",
            )
        bars.append(
            {
                "security_id": code,
                "stock_code": code,
                "market_date": observed_date,
                "date": observed_date,
                "open": open_value,
                "high": high_value,
                "low": low_value,
                "close": close_value,
                "volume": volume,
                "amount": amount,
                "turnover_rate": _fnum(raw[9]),
                "pct_change": _fnum(raw[10]),
                "adj_mode": "none",
                "adj_version": "baostock.adjustflag=3.v1",
                "source": "baostock.query_history_k_data_plus.adjustflag=3",
                "acquired_at": acquired_at,
                "built_at": acquired_at,
            }
        )
        statuses.append(
            {
                "security_id": code,
                "stock_code": code,
                "market_date": observed_date,
                "date": observed_date,
                "tradestatus": str(raw[11]),
                "isST": str(raw[12]),
                "source": "baostock.query_history_k_data_plus",
                "acquired_at": acquired_at,
                "built_at": acquired_at,
            }
        )
    raw_bytes = json.dumps(raw_rows, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return bars, statuses, len(raw_bytes), sha256_bytes(raw_bytes)


def collect_daily_history(
    codes: Sequence[str],
    start: date,
    end: date,
    *,
    bs_module: Any | None = None,
    max_attempts: int = 2,
    socket_timeout_seconds: float = 30,
    sleep: Callable[[float], None] = time.sleep,
    configure_timeout: Callable[[float], bool] = set_socket_timeout,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if end < start:
        raise ValueError("daily-history end date precedes start date")
    if max_attempts < 1 or max_attempts > 3:
        raise ValueError("daily-history attempts must be between 1 and 3")
    if bs_module is None:
        import baostock as loaded_bs  # type: ignore[import-not-found]

        bs_module = loaded_bs

    initial_login_attempts = _login(
        bs_module,
        max_attempts=3,
        socket_timeout_seconds=socket_timeout_seconds,
        sleep=sleep,
        configure_timeout=configure_timeout,
    )
    bars: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    try:
        for code in codes:
            started = time.monotonic()
            selected_bars: list[dict[str, Any]] = []
            selected_statuses: list[dict[str, Any]] = []
            response_bytes = 0
            response_sha256: str | None = None
            error: Exception | None = None
            attempts = 0
            for attempts in range(1, max_attempts + 1):
                if attempts > 1:
                    with contextlib.suppress(Exception):
                        bs_module.logout()
                    sleep(float(attempts - 1))
                    _login(
                        bs_module,
                        max_attempts=3,
                        socket_timeout_seconds=socket_timeout_seconds,
                        sleep=sleep,
                        configure_timeout=configure_timeout,
                    )
                try:
                    (
                        selected_bars,
                        selected_statuses,
                        response_bytes,
                        response_sha256,
                    ) = _query_once(bs_module, code, start, end)
                    error = None
                    break
                except Exception as exc:  # noqa: BLE001 - persisted fail-closed
                    error = exc
            acquired_at = now_shanghai_iso()
            if error is None:
                bars.extend(selected_bars)
                statuses.extend(selected_statuses)
                status = "success_nonempty"
                reason_code = "ok"
                detail = None
            else:
                status = "failed"
                reason_code = (
                    error.reason_code
                    if isinstance(error, DailyHistoryQueryError)
                    else "provider_request_failed"
                )
                detail = f"{type(error).__name__}: {error}"
            receipts.append(
                {
                    "dataset": "daily_ohlcv_none_history",
                    "security_id": code,
                    "security_type": "equity",
                    "provider": "baostock",
                    "request_json": json.dumps(
                        {
                            "operation": "query_history_k_data_plus",
                            "code": _bs_code(code),
                            "start": start.isoformat(),
                            "end": end.isoformat(),
                            "fields": FIELDS,
                            "frequency": "d",
                            "adjustflag": "3",
                        },
                        sort_keys=True,
                    ),
                    "status": status,
                    "reason_code": reason_code,
                    "response_rows": len(selected_bars),
                    "response_bytes": response_bytes,
                    "response_sha256": response_sha256,
                    "attempts": attempts,
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "acquired_at": acquired_at,
                    "detail": detail,
                }
            )
    finally:
        with contextlib.suppress(Exception):
            bs_module.logout()
    bars.sort(key=lambda row: (str(row["security_id"]), str(row["date"])))
    statuses.sort(key=lambda row: (str(row["security_id"]), str(row["date"])))
    receipts.sort(key=lambda row: str(row["security_id"]))
    return (
        bars,
        statuses,
        receipts,
        {
            "provider": "baostock.query_history_k_data_plus",
            "initial_login_attempts": initial_login_attempts,
        },
    )


def _failed_receipt_count(path: Path) -> int:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    return sum(
        row["status"] == "failed" for row in pq.read_table(path, columns=["status"]).to_pylist()
    )


def collect_daily_history_checkpointed(
    codes: Sequence[str],
    start: date,
    end: date,
    *,
    output: Path,
    status_output: Path,
    receipts_path: Path,
    checkpoint_dir: Path,
    batch_size: int = 100,
    collector: Callable[
        ..., tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]
    ] = collect_daily_history,
    collector_kwargs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if batch_size < 1 or batch_size > 500:
        raise ValueError("daily-history batch size must be between 1 and 500")
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    bar_parts: list[Path] = []
    status_parts: list[Path] = []
    receipt_parts: list[Path] = []
    failed_receipts = 0
    reused_batches = 0
    kwargs = dict(collector_kwargs or {})
    for offset in range(0, len(codes), batch_size):
        batch = list(codes[offset : offset + batch_size])
        part_id = f"part-{offset // batch_size:05d}"
        bar_part = checkpoint_dir / f"{part_id}.daily.parquet"
        status_part = checkpoint_dir / f"{part_id}.status.parquet"
        receipt_part = checkpoint_dir / f"{part_id}.receipts.parquet"
        metadata_part = checkpoint_dir / f"{part_id}.json"
        parts = {
            "daily": (bar_part, daily_schema()),
            "status": (status_part, status_schema()),
            "receipts": (receipt_part, receipt_schema()),
        }
        expected = {
            "schema_version": 2,
            "provider_revision": PROVIDER_REVISION,
            "collector": callable_identity(collector),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "codes": batch,
            "receipt_identities": sorted(
                [
                    "daily_ohlcv_none_history",
                    code,
                    json.dumps(
                        {
                            "operation": "query_history_k_data_plus",
                            "code": _bs_code(code),
                            "start": start.isoformat(),
                            "end": end.isoformat(),
                            "fields": FIELDS,
                            "frequency": "d",
                            "adjustflag": "3",
                        },
                        sort_keys=True,
                    ),
                ]
                for code in batch
            ),
        }
        reusable = False
        if all(path.is_file() for path in (*[item[0] for item in parts.values()], metadata_part)):
            try:
                saved = json.loads(metadata_part.read_text(encoding="utf-8"))
                reusable = isinstance(saved, dict) and checkpoint_parts_reusable(
                    saved, expected, parts
                )
            except (OSError, ValueError, json.JSONDecodeError):
                reusable = False
        if reusable:
            reused_batches += 1
        else:
            bars, statuses, receipts, metadata = collector(batch, start, end, **kwargs)
            write_parquet(bars, bar_part, daily_schema())
            write_parquet(statuses, status_part, status_schema())
            write_parquet(receipts, receipt_part, receipt_schema())
            verify_receipt_identities(
                receipt_part,
                cast(Sequence[Sequence[str]], expected["receipt_identities"]),
            )
            atomic_json(
                metadata_part,
                {
                    **expected,
                    "bar_rows": len(bars),
                    "status_rows": len(statuses),
                    "receipt_rows": len(receipts),
                    "failed_receipts": sum(row.get("status") == "failed" for row in receipts),
                    "parts": checkpoint_part_identities(parts),
                    "provider": metadata.get("provider"),
                    "completed_at": now_shanghai_iso(),
                },
            )
        failed_receipts += _failed_receipt_count(receipt_part)
        bar_parts.append(bar_part)
        status_parts.append(status_part)
        receipt_parts.append(receipt_part)
    bar_rows = merge_parquet_parts(bar_parts, output, daily_schema())
    status_rows = merge_parquet_parts(status_parts, status_output, status_schema())
    receipt_rows = merge_parquet_parts(receipt_parts, receipts_path, receipt_schema())
    return {
        "provider": "baostock.query_history_k_data_plus",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "batch_size": batch_size,
        "batch_count": len(bar_parts),
        "reused_batches": reused_batches,
        "bar_rows": bar_rows,
        "status_rows": status_rows,
        "receipt_rows": receipt_rows,
        "failed_receipts": failed_receipts,
        "checkpoint_dir": str(checkpoint_dir),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="quanthus-daily-history")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--stock-list", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--status-output", required=True)
    parser.add_argument("--receipts", required=True)
    parser.add_argument("--checkpoint-dir", required=True)
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args(argv)

    output = Path(args.output).resolve()
    status_output = Path(args.status_output).resolve()
    receipts = Path(args.receipts).resolve()
    try:
        codes = read_equity_universe(Path(args.stock_list).resolve())
        if not codes:
            raise ValueError("empty in-scope equity universe")
        metadata = collect_daily_history_checkpointed(
            codes,
            args.start,
            args.end,
            output=output,
            status_output=status_output,
            receipts_path=receipts,
            checkpoint_dir=Path(args.checkpoint_dir).resolve(),
            batch_size=args.batch_size,
        )
        summary = {
            "schema_version": 1,
            "provider_revision": PROVIDER_REVISION,
            "dataset": "daily_ohlcv_none_history",
            "output": str(output),
            "status_output": str(status_output),
            "receipts": str(receipts),
            "expected_receipt_count": len(codes),
            "completed_at": now_shanghai_iso(),
            **metadata,
        }
        atomic_json(output.with_suffix(output.suffix + ".meta.json"), summary)
        print(json.dumps(summary, ensure_ascii=True, sort_keys=True))
        return (
            0
            if metadata["failed_receipts"] == 0
            and parquet_row_count(output) > 0
            and parquet_row_count(status_output) == parquet_row_count(output)
            and parquet_row_count(receipts) == len(codes)
            else 1
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"daily-history collector failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

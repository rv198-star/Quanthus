"""Acquire a bounded historical valuation gap directly from BaoStock."""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from quanthus.providers.baostock_transport import set_socket_timeout
from quanthus.providers.common import (
    atomic_json,
    code6,
    merge_parquet_parts,
    now_shanghai_iso,
    parquet_row_count,
    read_equity_universe,
    receipt_schema,
    sha256_bytes,
    write_parquet,
)

PROVIDER_REVISION = "baostock-valuation-history-v1"
FIELDS = "date,code,close,tradestatus,peTTM,pbMRQ,psTTM,pcfNcfTTM"
VALUATION_FIELDS = ("pe_ttm", "pb", "ps_ttm", "pcf_ttm")


class ValuationQueryError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(detail)
        self.reason_code = reason_code


def valuation_schema() -> Any:
    import pyarrow as pa

    return pa.schema(
        [
            ("security_id", pa.string()),
            ("stock_code", pa.string()),
            ("market_date", pa.string()),
            ("date", pa.string()),
            ("close", pa.float64()),
            ("tradestatus", pa.string()),
            ("pe_ttm", pa.float64()),
            ("pb", pa.float64()),
            ("ps_ttm", pa.float64()),
            ("pcf_ttm", pa.float64()),
            ("total_share", pa.float64()),
            ("total_mv", pa.float64()),
            ("source", pa.string()),
            ("source_revision", pa.string()),
            ("acquired_at", pa.string()),
        ]
    )


def _fnum(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in {"", "-", "nan", "none", "null"}:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


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
    raise RuntimeError(
        f"baostock login failed after {max_attempts} attempts: {last_error}"
    )


def _query_once(
    bs_module: Any,
    code: str,
    start: date,
    end: date,
) -> tuple[list[dict[str, Any]], int, str]:
    response = bs_module.query_history_k_data_plus(
        _bs_code(code),
        FIELDS,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        frequency="d",
        adjustflag="3",
    )
    if str(response.error_code) != "0":
        raise ValuationQueryError(
            "provider_request_failed",
            f"baostock error={response.error_code}: {response.error_msg}",
        )
    raw_rows: list[list[str]] = []
    while response.next():
        raw_rows.append(response.get_row_data())
    if not raw_rows:
        raise ValuationQueryError(
            "provider_empty_response",
            "BaoStock returned no rows for an in-scope equity and date range",
        )

    acquired_at = now_shanghai_iso()
    rows: list[dict[str, Any]] = []
    for raw in raw_rows:
        if len(raw) < 8:
            raise ValuationQueryError(
                "provider_schema_mismatch",
                f"BaoStock returned {len(raw)} fields; expected 8",
            )
        observed_date = str(raw[0])
        observed_code = code6(raw[1])
        if not (start.isoformat() <= observed_date <= end.isoformat()):
            raise ValuationQueryError(
                "provider_date_out_of_range",
                f"provider date {observed_date} is outside {start}..{end}",
            )
        if observed_code != code:
            raise ValuationQueryError(
                "provider_identity_mismatch",
                f"requested {code}, provider returned {observed_code}",
            )
        rows.append(
            {
                "security_id": code,
                "stock_code": code,
                "market_date": observed_date,
                "date": observed_date,
                "close": _fnum(raw[2]),
                "tradestatus": str(raw[3]),
                "pe_ttm": _fnum(raw[4]),
                "pb": _fnum(raw[5]),
                "ps_ttm": _fnum(raw[6]),
                "pcf_ttm": _fnum(raw[7]),
                "total_share": None,
                "total_mv": None,
                "source": "baostock.query_history_k_data_plus.valuation",
                "source_revision": PROVIDER_REVISION,
                "acquired_at": acquired_at,
            }
        )
    if not any(
        row.get(field) is not None for row in rows for field in VALUATION_FIELDS
    ):
        raise ValuationQueryError(
            "all_valuation_fields_null",
            "BaoStock rows exist but all four valuation fields are null",
        )
    raw_bytes = json.dumps(
        raw_rows, ensure_ascii=True, separators=(",", ":")
    ).encode("utf-8")
    return rows, len(raw_bytes), sha256_bytes(raw_bytes)


def collect_valuation_history(
    codes: Sequence[str],
    start: date,
    end: date,
    *,
    bs_module: Any | None = None,
    max_attempts: int = 2,
    socket_timeout_seconds: float = 30,
    sleep: Callable[[float], None] = time.sleep,
    configure_timeout: Callable[[float], bool] = set_socket_timeout,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if end < start:
        raise ValueError("valuation end date precedes start date")
    if max_attempts < 1 or max_attempts > 3:
        raise ValueError("valuation attempts must be between 1 and 3")
    if bs_module is None:
        import baostock as loaded_bs

        bs_module = loaded_bs

    initial_login_attempts = _login(
        bs_module,
        max_attempts=3,
        socket_timeout_seconds=socket_timeout_seconds,
        sleep=sleep,
        configure_timeout=configure_timeout,
    )
    rows: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    try:
        for code in codes:
            started = time.monotonic()
            selected: list[dict[str, Any]] = []
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
                    selected, response_bytes, response_sha256 = _query_once(
                        bs_module, code, start, end
                    )
                    error = None
                    break
                except Exception as exc:  # noqa: BLE001 - persisted fail-closed
                    error = exc
            acquired_at = now_shanghai_iso()
            if error is None:
                rows.extend(selected)
                status = "success_nonempty"
                reason_code = "ok"
                detail = None
            else:
                status = "failed"
                reason_code = (
                    error.reason_code
                    if isinstance(error, ValuationQueryError)
                    else "provider_request_failed"
                )
                detail = f"{type(error).__name__}: {error}"
            receipts.append(
                {
                    "dataset": "valuation_history",
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
                    "response_rows": len(selected),
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
    rows.sort(key=lambda row: (str(row["date"]), str(row["security_id"])))
    receipts.sort(key=lambda row: str(row["security_id"]))
    return rows, receipts, {
        "provider": "baostock.query_history_k_data_plus",
        "initial_login_attempts": initial_login_attempts,
    }


def _failed_receipt_count(path: Path) -> int:
    import pyarrow.parquet as pq

    return sum(
        row["status"] == "failed"
        for row in pq.read_table(path, columns=["status"]).to_pylist()
    )


def collect_valuation_history_checkpointed(
    codes: Sequence[str],
    start: date,
    end: date,
    *,
    output: Path,
    receipts_path: Path,
    checkpoint_dir: Path,
    batch_size: int = 100,
    collector: Callable[
        ..., tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]
    ] = collect_valuation_history,
    collector_kwargs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if batch_size < 1 or batch_size > 500:
        raise ValueError("valuation batch size must be between 1 and 500")
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    data_parts: list[Path] = []
    receipt_parts: list[Path] = []
    failed_receipts = 0
    reused_batches = 0
    kwargs = dict(collector_kwargs or {})
    for offset in range(0, len(codes), batch_size):
        batch = list(codes[offset : offset + batch_size])
        part_id = f"part-{offset // batch_size:05d}"
        data_part = checkpoint_dir / f"{part_id}.valuation.parquet"
        receipt_part = checkpoint_dir / f"{part_id}.receipts.parquet"
        metadata_part = checkpoint_dir / f"{part_id}.json"
        expected = {
            "schema_version": 1,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "codes": batch,
        }
        reusable = False
        if data_part.is_file() and receipt_part.is_file() and metadata_part.is_file():
            try:
                saved = json.loads(metadata_part.read_text(encoding="utf-8"))
                reusable = all(
                    saved.get(key) == value for key, value in expected.items()
                ) and int(saved.get("failed_receipts", -1)) == 0
            except (OSError, ValueError, json.JSONDecodeError):
                reusable = False
        if reusable:
            reused_batches += 1
        else:
            rows, receipts, metadata = collector(batch, start, end, **kwargs)
            write_parquet(rows, data_part, valuation_schema())
            write_parquet(receipts, receipt_part, receipt_schema())
            atomic_json(
                metadata_part,
                {
                    **expected,
                    "row_count": len(rows),
                    "receipt_count": len(receipts),
                    "failed_receipts": sum(
                        row.get("status") == "failed" for row in receipts
                    ),
                    "provider": metadata.get("provider"),
                    "completed_at": now_shanghai_iso(),
                },
            )
        failed_receipts += _failed_receipt_count(receipt_part)
        data_parts.append(data_part)
        receipt_parts.append(receipt_part)
    row_count = merge_parquet_parts(data_parts, output, valuation_schema())
    receipt_count = merge_parquet_parts(
        receipt_parts, receipts_path, receipt_schema()
    )
    return {
        "provider": "baostock.query_history_k_data_plus",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "batch_size": batch_size,
        "batch_count": len(data_parts),
        "reused_batches": reused_batches,
        "row_count": row_count,
        "receipt_count": receipt_count,
        "failed_receipts": failed_receipts,
        "checkpoint_dir": str(checkpoint_dir),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="quanthus-valuation-history")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--stock-list", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--receipts", required=True)
    parser.add_argument("--checkpoint-dir", required=True)
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args(argv)

    output = Path(args.output).resolve()
    receipts = Path(args.receipts).resolve()
    try:
        codes = read_equity_universe(Path(args.stock_list).resolve())
        if not codes:
            raise ValueError("empty in-scope equity universe")
        metadata = collect_valuation_history_checkpointed(
            codes,
            args.start,
            args.end,
            output=output,
            receipts_path=receipts,
            checkpoint_dir=Path(args.checkpoint_dir).resolve(),
            batch_size=args.batch_size,
        )
        summary = {
            "schema_version": 1,
            "provider_revision": PROVIDER_REVISION,
            "dataset": "valuation_history",
            "output": str(output),
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
            and parquet_row_count(receipts) == len(codes)
            else 1
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"valuation history collector failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

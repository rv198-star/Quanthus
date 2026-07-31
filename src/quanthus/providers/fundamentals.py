"""Acquire raw F10, full financial statements, and industry membership.

These collectors call upstream providers directly. They never snapshot a node-local
canonical parquet. Every requested security receives a receipt so a successful
zero-row response cannot be confused with a failed or skipped request.
"""

from __future__ import annotations

import argparse
import contextlib
import gzip
import json
import re
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from typing import Any

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
from quanthus.providers.minute_tdx import tdx_client

PROVIDER_REVISION = "quanthus-fundamentals-provider-v1"
SINA_STATEMENT_URL = (
    "https://quotes.sina.cn/cn/api/openapi.php/"
    "CompanyFinanceService.getFinanceReport2022"
)
STATEMENT_TYPES = {
    "fzb": "balance_sheet",
    "lrb": "income_statement",
    "llb": "cash_flow",
}
_F10_UPDATED = re.compile(r"更新日期[：:]\s*(\d{4}-\d{2}-\d{2})")


def f10_schema() -> Any:
    import pyarrow as pa  # type: ignore[import-untyped]

    return pa.schema(
        [
            ("security_id", pa.string()),
            ("stock_code", pa.string()),
            ("category", pa.string()),
            ("source_filename", pa.string()),
            ("source_start", pa.int64()),
            ("source_length", pa.int64()),
            ("content", pa.string()),
            ("content_sha256", pa.string()),
            ("provider_updated_date", pa.string()),
            ("market_date", pa.string()),
            ("source", pa.string()),
            ("source_revision", pa.string()),
            ("acquired_at", pa.string()),
        ]
    )


def statements_schema() -> Any:
    import pyarrow as pa

    return pa.schema(
        [
            ("security_id", pa.string()),
            ("stock_code", pa.string()),
            ("statement_type", pa.string()),
            ("provider_source", pa.string()),
            ("requested_periods", pa.int32()),
            ("period_count", pa.int32()),
            ("earliest_report_date", pa.string()),
            ("latest_report_date", pa.string()),
            ("latest_publish_date", pa.string()),
            ("latest_update_time", pa.string()),
            ("raw_payload_gzip", pa.binary()),
            ("raw_payload_sha256", pa.string()),
            ("raw_payload_bytes", pa.int64()),
            ("market_date", pa.string()),
            ("source", pa.string()),
            ("source_revision", pa.string()),
            ("acquired_at", pa.string()),
        ]
    )


def financial_quarterly_schema() -> Any:
    import pyarrow as pa

    return pa.schema(
        [
            ("security_id", pa.string()),
            ("stock_code", pa.string()),
            ("report_date", pa.string()),
            ("publish_date", pa.string()),
            ("update_time", pa.string()),
            ("revenue", pa.string()),
            ("net_profit", pa.string()),
            ("parent_net_profit", pa.string()),
            ("currency", pa.string()),
            ("is_audit", pa.string()),
            ("audit_opinion", pa.string()),
            ("source", pa.string()),
            ("source_revision", pa.string()),
            ("acquired_at", pa.string()),
        ]
    )


def industry_schema() -> Any:
    import pyarrow as pa

    return pa.schema(
        [
            ("security_id", pa.string()),
            ("stock_code", pa.string()),
            ("stock_name", pa.string()),
            ("industry_code", pa.string()),
            ("industry_name", pa.string()),
            ("classification", pa.string()),
            ("provider_update_date", pa.string()),
            ("market_date", pa.string()),
            ("source", pa.string()),
            ("source_revision", pa.string()),
            ("acquired_at", pa.string()),
        ]
    )


def _disconnect(client: Any) -> None:
    with contextlib.suppress(Exception):
        client.client.disconnect()


def _f10_once(client: Any, code: str, market_date: date) -> list[dict[str, Any]]:
    categories = client.F10C(symbol=code) or []
    acquired_at = now_shanghai_iso()
    rows: list[dict[str, Any]] = []
    for category in categories:
        name = str(category.get("name") or "").strip()
        if not name:
            continue
        content = client.F10(symbol=code, name=name)
        if content is None:
            continue
        if not isinstance(content, str):
            raise RuntimeError(f"F10 category {name!r} returned {type(content).__name__}")
        if not content.strip():
            continue
        raw = content.encode("utf-8")
        updated = _F10_UPDATED.search(content)
        rows.append(
            {
                "security_id": code,
                "stock_code": code,
                "category": name,
                "source_filename": str(category.get("filename") or ""),
                "source_start": int(category.get("start") or 0),
                "source_length": int(category.get("length") or 0),
                "content": content,
                "content_sha256": sha256_bytes(raw),
                "provider_updated_date": updated.group(1) if updated else None,
                "market_date": market_date.isoformat(),
                "source": "tdx.get_company_info_content",
                "source_revision": PROVIDER_REVISION,
                "acquired_at": acquired_at,
            }
        )
    return rows


def collect_f10(
    codes: Sequence[str],
    market_date: date,
    *,
    client_factory: Callable[[], tuple[Any, str]] = tdx_client,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    client, server = client_factory()
    try:
        for code in codes:
            started = time.monotonic()
            attempts = 0
            error: Exception | None = None
            selected: list[dict[str, Any]] = []
            for attempts in (1, 2):
                try:
                    selected = _f10_once(client, code, market_date)
                    error = None
                    break
                except Exception as exc:  # noqa: BLE001 - one bounded provider retry
                    error = exc
                    _disconnect(client)
                    if attempts == 1:
                        client, server = client_factory()
            acquired_at = now_shanghai_iso()
            raw_bytes = sum(len(str(row["content"]).encode("utf-8")) for row in selected)
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
                reason = "provider_has_no_f10"
                detail = "TDX returned a successful empty F10 category set"
            receipts.append(
                {
                    "dataset": "raw_f10",
                    "security_id": code,
                    "security_type": "equity",
                    "provider": f"tdx:{server}",
                    "request_json": json.dumps(
                        {"operation": "F10C+F10", "symbol": code}, sort_keys=True
                    ),
                    "status": status,
                    "reason_code": reason,
                    "response_rows": len(selected),
                    "response_bytes": raw_bytes,
                    "response_sha256": (
                        sha256_bytes(
                            "".join(str(row["content"]) for row in selected).encode("utf-8")
                        )
                        if selected
                        else None
                    ),
                    "attempts": attempts,
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "acquired_at": acquired_at,
                    "detail": detail,
                }
            )
    finally:
        _disconnect(client)
    return rows, receipts, {"provider": "tdx", "server": server}


def collect_f10_checkpointed(
    codes: Sequence[str],
    market_date: date,
    *,
    output: Path,
    receipts_path: Path,
    checkpoint_dir: Path,
    batch_size: int = 200,
    client_factory: Callable[[], tuple[Any, str]] = tdx_client,
) -> dict[str, Any]:
    if batch_size < 1 or batch_size > 500:
        raise ValueError("F10 batch size must be between 1 and 500")
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    data_parts: list[Path] = []
    receipt_parts: list[Path] = []
    failed_receipts = 0
    reused_batches = 0
    servers: set[str] = set()
    for offset in range(0, len(codes), batch_size):
        batch = list(codes[offset : offset + batch_size])
        part_id = f"part-{offset // batch_size:05d}"
        data_part = checkpoint_dir / f"{part_id}.f10.parquet"
        receipt_part = checkpoint_dir / f"{part_id}.receipts.parquet"
        metadata_part = checkpoint_dir / f"{part_id}.json"
        expected = {
            "schema_version": 1,
            "market_date": market_date.isoformat(),
            "codes": batch,
        }
        reusable = False
        if data_part.is_file() and receipt_part.is_file() and metadata_part.is_file():
            try:
                saved = json.loads(metadata_part.read_text(encoding="utf-8"))
                reusable = all(saved.get(key) == value for key, value in expected.items()) and int(
                    saved.get("failed_receipts", -1)
                ) == 0
                if saved.get("server"):
                    servers.add(str(saved["server"]))
            except (OSError, ValueError, json.JSONDecodeError):
                reusable = False
        if reusable:
            reused_batches += 1
        else:
            rows, receipts, metadata = collect_f10(
                batch,
                market_date,
                client_factory=client_factory,
            )
            servers.add(str(metadata["server"]))
            write_parquet(rows, data_part, f10_schema())
            write_parquet(receipts, receipt_part, receipt_schema())
            atomic_json(
                metadata_part,
                {
                    **expected,
                    "row_count": len(rows),
                    "receipt_count": len(receipts),
                    "failed_receipts": sum(
                        1 for receipt in receipts if receipt.get("status") == "failed"
                    ),
                    "server": metadata["server"],
                    "completed_at": now_shanghai_iso(),
                },
            )
        failed_receipts += sum(
            1
            for receipt in _read_receipts(receipt_part)
            if receipt.get("status") == "failed"
        )
        data_parts.append(data_part)
        receipt_parts.append(receipt_part)
    row_count = merge_parquet_parts(data_parts, output, f10_schema())
    receipt_count = merge_parquet_parts(receipt_parts, receipts_path, receipt_schema())
    return {
        "provider": "tdx",
        "servers": sorted(servers),
        "batch_size": batch_size,
        "batch_count": len(data_parts),
        "reused_batches": reused_batches,
        "row_count": row_count,
        "receipt_count": receipt_count,
        "failed_receipts": failed_receipts,
        "checkpoint_dir": str(checkpoint_dir),
    }


def _statement_request(
    code: str,
    source: str,
    periods: int,
    *,
    get: Callable[..., Any],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    prefix = "sh" if code.startswith("6") else "sz"
    params = {
        "paperCode": f"{prefix}{code}",
        "source": source,
        "type": "0",
        "page": "1",
        "num": str(periods),
    }
    started = time.monotonic()
    acquired_at = now_shanghai_iso()
    request_json = json.dumps(
        {"url": SINA_STATEMENT_URL, "params": params}, sort_keys=True
    )
    try:
        response = get(
            SINA_STATEMENT_URL,
            params=params,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"},
            timeout=30,
        )
        if int(response.status_code) != 200:
            raise RuntimeError(f"HTTP {response.status_code}")
        raw = bytes(response.content)
        body = response.json()
        result = body.get("result") or {}
        status_code = str((result.get("status") or {}).get("code", ""))
        if status_code != "0":
            raise RuntimeError(f"provider status={status_code or 'missing'}")
        report_list = (result.get("data") or {}).get("report_list") or {}
        if not isinstance(report_list, dict):
            raise RuntimeError("report_list is not an object")
        periods_found = sorted(str(value) for value in report_list)
        latest = report_list.get(periods_found[-1], {}) if periods_found else {}
        row = {
            "security_id": code,
            "stock_code": code,
            "statement_type": STATEMENT_TYPES[source],
            "provider_source": source,
            "requested_periods": periods,
            "period_count": len(periods_found),
            "earliest_report_date": periods_found[0] if periods_found else None,
            "latest_report_date": periods_found[-1] if periods_found else None,
            "latest_publish_date": str(latest.get("publish_date") or "") or None,
            "latest_update_time": str(latest.get("update_time") or "") or None,
            "raw_payload_gzip": gzip.compress(raw, compresslevel=6, mtime=0),
            "raw_payload_sha256": sha256_bytes(raw),
            "raw_payload_bytes": len(raw),
            "market_date": None,
            "source": "sina.CompanyFinanceService.getFinanceReport2022",
            "source_revision": PROVIDER_REVISION,
            "acquired_at": acquired_at,
        }
        receipt = {
            "dataset": "financial_statements",
            "security_id": code,
            "security_type": "equity",
            "provider": "sina",
            "request_json": request_json,
            "status": "success_nonempty" if periods_found else "justified_empty",
            "reason_code": "ok" if periods_found else "provider_has_no_statement",
            "response_rows": len(periods_found),
            "response_bytes": len(raw),
            "response_sha256": sha256_bytes(raw),
            "attempts": 1,
            "duration_seconds": round(time.monotonic() - started, 3),
            "acquired_at": acquired_at,
            "detail": None,
        }
        return row, receipt
    except Exception as exc:  # noqa: BLE001 - persisted as a fail-closed receipt
        return None, {
            "dataset": "financial_statements",
            "security_id": code,
            "security_type": "equity",
            "provider": "sina",
            "request_json": request_json,
            "status": "failed",
            "reason_code": "provider_request_failed",
            "response_rows": 0,
            "response_bytes": 0,
            "response_sha256": None,
            "attempts": 1,
            "duration_seconds": round(time.monotonic() - started, 3),
            "acquired_at": acquired_at,
            "detail": f"{type(exc).__name__}: {exc}",
        }


def collect_statements(
    codes: Sequence[str],
    market_date: date,
    *,
    periods: int = 8,
    workers: int = 4,
    get: Callable[..., Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if periods < 1 or periods > 100:
        raise ValueError("statement periods must be between 1 and 100")
    if workers < 1 or workers > 8:
        raise ValueError("statement workers must be between 1 and 8")
    if get is None:
        import requests  # type: ignore[import-untyped]

        get = requests.get

    rows: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    def request_with_retry(code: str, source: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        row, receipt = _statement_request(code, source, periods, get=get)
        if receipt["status"] != "failed":
            return row, receipt
        retry_row, retry_receipt = _statement_request(code, source, periods, get=get)
        retry_receipt["attempts"] = 2
        retry_receipt["duration_seconds"] = round(
            float(receipt["duration_seconds"]) + float(retry_receipt["duration_seconds"]), 3
        )
        return retry_row, retry_receipt

    tasks = [(code, source) for code in codes for source in STATEMENT_TYPES]
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(request_with_retry, code, source): (code, source)
            for code, source in tasks
        }
        for future in as_completed(futures):
            row, receipt = future.result()
            receipts.append(receipt)
            if row is not None:
                row["market_date"] = market_date.isoformat()
                rows.append(row)
    rows.sort(key=lambda row: (str(row["security_id"]), str(row["statement_type"])))
    receipts.sort(key=lambda row: (str(row["security_id"]), str(row["request_json"])))
    return rows, receipts, {
        "provider": "sina",
        "requested_periods": periods,
        "workers": workers,
    }


def collect_statements_checkpointed(
    codes: Sequence[str],
    market_date: date,
    *,
    output: Path,
    receipts_path: Path,
    quarterly_output: Path,
    checkpoint_dir: Path,
    periods: int = 8,
    workers: int = 4,
    batch_size: int = 100,
    get: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Collect statements in restartable batches and stream them into final artifacts."""

    if batch_size < 1 or batch_size > 500:
        raise ValueError("statement batch size must be between 1 and 500")
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    statement_parts: list[Path] = []
    receipt_parts: list[Path] = []
    quarterly_parts: list[Path] = []
    failed_receipts = 0
    reused_batches = 0

    for offset in range(0, len(codes), batch_size):
        batch = list(codes[offset : offset + batch_size])
        part_id = f"part-{offset // batch_size:05d}"
        statements_part = checkpoint_dir / f"{part_id}.statements.parquet"
        receipts_part = checkpoint_dir / f"{part_id}.receipts.parquet"
        quarterly_part = checkpoint_dir / f"{part_id}.quarterly.parquet"
        metadata_part = checkpoint_dir / f"{part_id}.json"
        expected = {
            "schema_version": 1,
            "market_date": market_date.isoformat(),
            "periods": periods,
            "codes": batch,
        }
        reusable = False
        part_paths = (statements_part, receipts_part, quarterly_part, metadata_part)
        if all(path.is_file() for path in part_paths):
            try:
                saved = json.loads(metadata_part.read_text(encoding="utf-8"))
                reusable = all(saved.get(key) == value for key, value in expected.items()) and int(
                    saved.get("failed_receipts", -1)
                ) == 0
            except (OSError, ValueError, json.JSONDecodeError):
                reusable = False
        if reusable:
            reused_batches += 1
        else:
            rows, receipts, _metadata = collect_statements(
                batch,
                market_date,
                periods=periods,
                workers=workers,
                get=get,
            )
            quarterly = derive_financial_quarterly(rows)
            write_parquet(rows, statements_part, statements_schema())
            write_parquet(receipts, receipts_part, receipt_schema())
            write_parquet(quarterly, quarterly_part, financial_quarterly_schema())
            atomic_json(
                metadata_part,
                {
                    **expected,
                    "statement_rows": len(rows),
                    "receipt_rows": len(receipts),
                    "quarterly_rows": len(quarterly),
                    "failed_receipts": sum(
                        1 for receipt in receipts if receipt.get("status") == "failed"
                    ),
                    "completed_at": now_shanghai_iso(),
                },
            )
        failed_receipts += sum(
            1
            for receipt in _read_receipts(receipts_part)
            if receipt.get("status") == "failed"
        )
        statement_parts.append(statements_part)
        receipt_parts.append(receipts_part)
        quarterly_parts.append(quarterly_part)

    statement_rows = merge_parquet_parts(statement_parts, output, statements_schema())
    receipt_rows = merge_parquet_parts(receipt_parts, receipts_path, receipt_schema())
    quarterly_rows = merge_parquet_parts(
        quarterly_parts, quarterly_output, financial_quarterly_schema()
    )
    return {
        "provider": "sina",
        "requested_periods": periods,
        "workers": workers,
        "batch_size": batch_size,
        "batch_count": len(statement_parts),
        "reused_batches": reused_batches,
        "statement_rows": statement_rows,
        "receipt_rows": receipt_rows,
        "quarterly_rows": quarterly_rows,
        "failed_receipts": failed_receipts,
        "checkpoint_dir": str(checkpoint_dir),
    }


def _read_receipts(path: Path) -> list[dict[str, Any]]:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    rows: list[dict[str, Any]] = pq.read_table(path, columns=["status"]).to_pylist()
    return rows


def _payload_json(row: Mapping[str, Any]) -> dict[str, Any]:
    payload = row.get("raw_payload_gzip")
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise ValueError("financial statement row has no compressed raw payload")
    decoded = json.loads(gzip.decompress(bytes(payload)))
    if not isinstance(decoded, dict):
        raise ValueError("financial statement payload root is not an object")
    return decoded


def derive_financial_quarterly(
    statement_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in statement_rows:
        if row.get("statement_type") != "income_statement":
            continue
        body = _payload_json(row)
        report_list = (
            ((body.get("result") or {}).get("data") or {}).get("report_list") or {}
        )
        for period, report in report_list.items():
            fields = {
                str(item.get("item_field") or ""): item.get("item_value")
                for item in report.get("data", []) or []
                if item.get("item_field")
            }
            output.append(
                {
                    "security_id": str(row["security_id"]),
                    "stock_code": str(row["stock_code"]),
                    "report_date": str(period),
                    "publish_date": str(report.get("publish_date") or "") or None,
                    "update_time": str(report.get("update_time") or "") or None,
                    "revenue": fields.get("BIZINCO"),
                    "net_profit": fields.get("NETPROFIT"),
                    "parent_net_profit": (
                        fields.get("PARENETP") or fields.get("NETPARECOMPPROF")
                    ),
                    "currency": str(report.get("rCurrency") or "") or None,
                    "is_audit": str(report.get("is_audit") or "") or None,
                    "audit_opinion": str(report.get("audit_opinion") or "") or None,
                    "source": "sina.full_income_statement_projection",
                    "source_revision": PROVIDER_REVISION,
                    "acquired_at": str(row["acquired_at"]),
                }
            )
    output.sort(key=lambda row: (str(row["security_id"]), str(row["report_date"])))
    return output


def collect_industry(
    codes: Sequence[str],
    market_date: date,
    *,
    bs_module: Any | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if bs_module is None:
        import baostock as loaded_bs  # type: ignore[import-not-found]

        bs_module = loaded_bs

    started = time.monotonic()
    acquired_at = now_shanghai_iso()
    login = bs_module.login()
    if str(login.error_code) != "0":
        raise RuntimeError(f"baostock login failed: {login.error_msg}")
    raw_rows: list[list[str]] = []
    try:
        result = bs_module.query_stock_industry()
        if str(result.error_code) != "0":
            raise RuntimeError(
                f"baostock query_stock_industry failed: {result.error_msg}"
            )
        while result.next():
            raw_rows.append(result.get_row_data())
    finally:
        bs_module.logout()

    by_code = {code6(row[1]): row for row in raw_rows if len(row) >= 5}
    rows: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    for code in codes:
        provider_row = by_code.get(code)
        industry = str(provider_row[3]).strip() if provider_row else ""
        if provider_row is None or not industry:
            status = "failed"
            reason = "missing_industry_membership"
            detail = "provider response has no nonempty industry for an in-scope equity"
        else:
            status = "success_nonempty"
            reason = "ok"
            detail = None
            match = re.match(r"^([A-Z]\d+)(.*)$", industry)
            rows.append(
                {
                    "security_id": code,
                    "stock_code": code,
                    "stock_name": str(provider_row[2]),
                    "industry_code": match.group(1) if match else None,
                    "industry_name": match.group(2) if match else industry,
                    "classification": str(provider_row[4]),
                    "provider_update_date": str(provider_row[0]),
                    "market_date": market_date.isoformat(),
                    "source": "baostock.query_stock_industry",
                    "source_revision": PROVIDER_REVISION,
                    "acquired_at": acquired_at,
                }
            )
        receipts.append(
            {
                "dataset": "industry_membership",
                "security_id": code,
                "security_type": "equity",
                "provider": "baostock",
                "request_json": json.dumps(
                    {"operation": "query_stock_industry", "scope": "all"},
                    sort_keys=True,
                ),
                "status": status,
                "reason_code": reason,
                "response_rows": 1 if status == "success_nonempty" else 0,
                "response_bytes": 0,
                "response_sha256": None,
                "attempts": 1,
                "duration_seconds": round(time.monotonic() - started, 3),
                "acquired_at": acquired_at,
                "detail": detail,
            }
        )
    return rows, receipts, {
        "provider": "baostock.query_stock_industry",
        "provider_row_count": len(raw_rows),
    }


def _write_result(
    *,
    dataset: str,
    rows: Sequence[Mapping[str, Any]],
    receipts: Sequence[Mapping[str, Any]],
    output: Path,
    receipts_path: Path,
    schema: Any,
    market_date: date,
    metadata: Mapping[str, Any],
    expected_receipts: int,
) -> int:
    row_count = write_parquet(rows, output, schema)
    receipt_count = write_parquet(receipts, receipts_path, receipt_schema())
    failed = sum(1 for row in receipts if row.get("status") == "failed")
    justified_empty = sum(
        1 for row in receipts if row.get("status") == "justified_empty"
    )
    completed_at = now_shanghai_iso()
    summary = {
        "schema_version": 1,
        "provider_revision": PROVIDER_REVISION,
        "dataset": dataset,
        "market_date": market_date.isoformat(),
        "output": str(output),
        "receipts": str(receipts_path),
        "row_count": row_count,
        "receipt_count": receipt_count,
        "expected_receipt_count": expected_receipts,
        "failed_receipts": failed,
        "justified_empty_receipts": justified_empty,
        "completed_at": completed_at,
        **dict(metadata),
    }
    atomic_json(output.with_suffix(output.suffix + ".meta.json"), summary)
    print(json.dumps(summary, ensure_ascii=True, sort_keys=True))
    return 0 if failed == 0 and row_count > 0 and receipt_count == expected_receipts else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="quanthus-fundamentals")
    parser.add_argument(
        "dataset", choices=("raw-f10", "financial-statements", "industry-membership")
    )
    parser.add_argument("--market-date", required=True)
    parser.add_argument("--stock-list", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--receipts", required=True)
    parser.add_argument("--statement-periods", type=int, default=8)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--checkpoint-dir")
    parser.add_argument("--quarterly-output")
    args = parser.parse_args(argv)

    market_date = date.fromisoformat(args.market_date)
    output = Path(args.output).resolve()
    receipts_path = Path(args.receipts).resolve()
    try:
        codes = read_equity_universe(Path(args.stock_list).resolve())
        if not codes:
            raise ValueError("empty in-scope equity universe")
        if args.dataset == "raw-f10":
            if args.checkpoint_dir:
                metadata = collect_f10_checkpointed(
                    codes,
                    market_date,
                    output=output,
                    receipts_path=receipts_path,
                    checkpoint_dir=Path(args.checkpoint_dir).resolve(),
                    batch_size=args.batch_size,
                )
                completed_at = now_shanghai_iso()
                summary = {
                    "schema_version": 1,
                    "provider_revision": PROVIDER_REVISION,
                    "dataset": args.dataset,
                    "market_date": market_date.isoformat(),
                    "output": str(output),
                    "receipts": str(receipts_path),
                    "expected_receipt_count": len(codes),
                    "completed_at": completed_at,
                    **metadata,
                }
                atomic_json(output.with_suffix(output.suffix + ".meta.json"), summary)
                print(json.dumps(summary, ensure_ascii=True, sort_keys=True))
                return (
                    0
                    if metadata["failed_receipts"] == 0
                    and metadata["row_count"] > 0
                    and metadata["receipt_count"] == len(codes)
                    else 1
                )
            rows, receipts, metadata = collect_f10(codes, market_date)
            schema = f10_schema()
        elif args.dataset == "financial-statements":
            if not args.checkpoint_dir or not args.quarterly_output:
                raise ValueError(
                    "financial-statements requires --checkpoint-dir and --quarterly-output"
                )
            metadata = collect_statements_checkpointed(
                codes,
                market_date,
                output=output,
                receipts_path=receipts_path,
                quarterly_output=Path(args.quarterly_output).resolve(),
                checkpoint_dir=Path(args.checkpoint_dir).resolve(),
                periods=args.statement_periods,
                workers=args.workers,
                batch_size=args.batch_size,
            )
            completed_at = now_shanghai_iso()
            summary = {
                "schema_version": 1,
                "provider_revision": PROVIDER_REVISION,
                "dataset": args.dataset,
                "market_date": market_date.isoformat(),
                "output": str(output),
                "receipts": str(receipts_path),
                "quarterly_output": str(Path(args.quarterly_output).resolve()),
                "row_count": parquet_row_count(output),
                "receipt_count": parquet_row_count(receipts_path),
                "failed_receipts": metadata["failed_receipts"],
                "expected_receipt_count": len(codes) * len(STATEMENT_TYPES),
                "completed_at": completed_at,
                **metadata,
            }
            atomic_json(output.with_suffix(output.suffix + ".meta.json"), summary)
            print(json.dumps(summary, ensure_ascii=True, sort_keys=True))
            return (
                0
                if metadata["failed_receipts"] == 0
                and metadata["statement_rows"] > 0
                and metadata["receipt_rows"] == len(codes) * len(STATEMENT_TYPES)
                else 1
            )
        else:
            rows, receipts, metadata = collect_industry(codes, market_date)
            schema = industry_schema()
        return _write_result(
            dataset=args.dataset,
            rows=rows,
            receipts=receipts,
            output=output,
            receipts_path=receipts_path,
            schema=schema,
            market_date=market_date,
            metadata=metadata,
            expected_receipts=len(codes),
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"fundamentals collector failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

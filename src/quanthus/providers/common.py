"""Shared helpers for repository-owned ODS provider collectors."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


def now_shanghai_iso() -> str:
    return datetime.now(SHANGHAI).isoformat(timespec="seconds")


def sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def callable_identity(value: Callable[..., Any]) -> str:
    module = getattr(value, "__module__", type(value).__module__)
    qualname = getattr(value, "__qualname__", type(value).__qualname__)
    return f"{module}.{qualname}"


def jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return jsonable(item())
        except Exception:
            pass
    return str(value)


def atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(jsonable(payload), ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def write_parquet(rows: Sequence[Mapping[str, Any]], path: Path, schema: Any) -> int:
    try:
        import pyarrow as pa  # type: ignore[import-untyped]
        import pyarrow.parquet as pq  # type: ignore[import-untyped]
    except ModuleNotFoundError as exc:  # pragma: no cover - deployment dependency
        raise RuntimeError("pyarrow is required by ODS provider collectors") from exc

    normalized = [{str(key): jsonable(value) for key, value in row.items()} for row in rows]
    table = pa.Table.from_pylist(normalized, schema=schema)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    pq.write_table(table, temporary, compression="zstd")
    os.replace(temporary, path)
    return int(table.num_rows)


def merge_parquet_parts(parts: Sequence[Path], path: Path, schema: Any) -> int:
    """Merge bounded parquet parts without loading the complete dataset into memory."""

    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:  # pragma: no cover - deployment dependency
        raise RuntimeError("pyarrow is required by ODS provider collectors") from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    writer = pq.ParquetWriter(temporary, schema, compression="zstd")
    row_count = 0
    try:
        for part in parts:
            table = pq.read_table(part)
            if table.schema != schema:
                table = table.cast(schema)
            writer.write_table(table)
            row_count += table.num_rows
        if not parts:
            writer.write_table(pa.Table.from_pylist([], schema=schema))
    finally:
        writer.close()
    os.replace(temporary, path)
    return row_count


def parquet_row_count(path: Path) -> int:
    try:
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:  # pragma: no cover - deployment dependency
        raise RuntimeError("pyarrow is required by ODS provider collectors") from exc
    return int(pq.ParquetFile(path).metadata.num_rows)


def parquet_schema_sha256(schema: Any) -> str:
    return sha256_bytes(schema.serialize().to_pybytes())


def checkpoint_part_identities(
    parts: Mapping[str, tuple[Path, Any]],
) -> dict[str, dict[str, object]]:
    try:
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:  # pragma: no cover - deployment dependency
        raise RuntimeError("pyarrow is required by ODS provider collectors") from exc

    identities: dict[str, dict[str, object]] = {}
    for name, (path, expected_schema) in sorted(parts.items()):
        parquet = pq.ParquetFile(path)
        actual_schema = parquet.schema_arrow
        identities[name] = {
            "sha256": sha256_file(path),
            "row_count": int(parquet.metadata.num_rows),
            "schema_sha256": parquet_schema_sha256(actual_schema),
            "expected_schema_sha256": parquet_schema_sha256(expected_schema),
        }
    return identities


def checkpoint_parts_reusable(
    saved: Mapping[str, Any],
    expected: Mapping[str, Any],
    parts: Mapping[str, tuple[Path, Any]],
) -> bool:
    if any(saved.get(key) != value for key, value in expected.items()):
        return False
    failed_receipts = saved.get("failed_receipts")
    if type(failed_receipts) is not int or failed_receipts != 0:
        return False
    try:
        actual = checkpoint_part_identities(parts)
    except Exception:  # noqa: BLE001 - any unreadable checkpoint must be recollected
        return False
    return saved.get("parts") == actual and all(
        identity["schema_sha256"] == identity["expected_schema_sha256"]
        for identity in actual.values()
    )


def receipt_identities(path: Path) -> list[list[str]]:
    try:
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:  # pragma: no cover - deployment dependency
        raise RuntimeError("pyarrow is required by ODS provider collectors") from exc
    rows = pq.read_table(
        path, columns=["dataset", "security_id", "request_json"]
    ).to_pylist()
    identities = [
        [str(row["dataset"]), str(row["security_id"]), str(row["request_json"])]
        for row in rows
    ]
    return sorted(identities)


def verify_receipt_identities(
    path: Path, expected: Sequence[Sequence[str]]
) -> list[list[str]]:
    actual = receipt_identities(path)
    normalized_expected = sorted([list(identity) for identity in expected])
    if actual != normalized_expected:
        raise ValueError("checkpoint receipt identities differ from the requested batch")
    return actual


def receipt_schema() -> Any:
    import pyarrow as pa

    return pa.schema(
        [
            ("dataset", pa.string()),
            ("security_id", pa.string()),
            ("security_type", pa.string()),
            ("provider", pa.string()),
            ("request_json", pa.string()),
            ("status", pa.string()),
            ("reason_code", pa.string()),
            ("response_rows", pa.int64()),
            ("response_bytes", pa.int64()),
            ("response_sha256", pa.string()),
            ("attempts", pa.int32()),
            ("duration_seconds", pa.float64()),
            ("acquired_at", pa.string()),
            ("detail", pa.string()),
        ]
    )


def code6(value: Any) -> str:
    text = str(value or "").strip()
    if "." in text:
        text = text.split(".")[-1]
    return text.zfill(6) if text.isdigit() else text


def is_supported_equity(code: str) -> bool:
    code = code6(code)
    if len(code) != 6 or not code.isdigit():
        return False
    return code.startswith(("0", "3", "6"))


def read_equity_universe(stock_list: Path) -> list[str]:
    try:
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:  # pragma: no cover - deployment dependency
        raise RuntimeError("pyarrow is required to read the stock universe") from exc
    if not stock_list.is_file():
        raise FileNotFoundError(f"stock list is missing: {stock_list}")
    table = pq.read_table(stock_list)
    column = "stock_code" if "stock_code" in table.schema.names else "security_id"
    if column not in table.schema.names:
        raise ValueError("stock list has neither stock_code nor security_id")
    codes = {code6(value) for value in table.column(column).to_pylist()}
    return sorted(code for code in codes if is_supported_equity(code))

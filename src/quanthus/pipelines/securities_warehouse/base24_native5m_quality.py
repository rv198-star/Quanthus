"""Bounded source admission for Base 2.4 supplier-native five-minute releases."""

from __future__ import annotations

import hashlib
import importlib
import json
import math
from collections import Counter
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path
from typing import Any

CONTRACT = "p10-base24-native5m-r2-v1"
ARTIFACTS = {
    "intraday_bars": "intraday_5m_bars.parquet",
    "intraday_coverage": "intraday_5m_coverage.parquet",
    "intraday_meta": "intraday_5m_meta.json",
}


class Native5mQualityError(ValueError):
    """Stable fail-closed reason without market values."""


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _load_policy(raw: bytes) -> dict[str, Any]:
    try:
        loaded = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise Native5mQualityError("QUALITY_POLICY_UNREADABLE") from exc
    if not isinstance(loaded, dict):
        raise Native5mQualityError("QUALITY_POLICY_INVALID")
    policy: dict[str, Any] = loaded
    if (
        policy.get("contract_id") != CONTRACT
        or policy.get("bar_size") != "5m"
        or policy.get("minimum_rating") != "R2"
        or policy.get("execution_authorized") is not False
        or len(policy.get("bucket_labels", [])) != policy.get("ready_rows")
        or len(set(policy["bucket_labels"])) != len(policy["bucket_labels"])
    ):
        raise Native5mQualityError("QUALITY_POLICY_INVALID")
    return policy


def _artifact_paths(root: Path) -> dict[str, Path]:
    if root.is_symlink() or not root.is_dir():
        raise Native5mQualityError("ARTIFACT_ROOT_INVALID")
    paths = {name: root / filename for name, filename in ARTIFACTS.items()}
    lane_status = root / "lane_status.json"
    release = root.parent / "release.json"
    if lane_status.is_file() and not lane_status.is_symlink():
        paths["receipt"] = lane_status
    elif release.is_file() and not release.is_symlink():
        paths["receipt"] = release
    else:
        raise Native5mQualityError("ARTIFACT_RECEIPT_MISSING")
    for path in (paths[name] for name in ARTIFACTS):
        if path.is_symlink() or not path.is_file():
            raise Native5mQualityError("REQUIRED_ARTIFACT_MISSING_OR_SYMLINK")
        if path.parent.resolve() != root.resolve():
            raise Native5mQualityError("ARTIFACT_OUTSIDE_ROOT")
    return paths


def _verify_receipt(paths: dict[str, Path], market_date: str) -> dict[str, str]:
    try:
        status = json.loads(paths["receipt"].read_text(encoding="utf-8"))
        entries = status.get("datasets", status.get("artifacts"))
        datasets = {entry["name"]: entry for entry in entries}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise Native5mQualityError("LANE_RECEIPT_UNREADABLE") from exc
    if status.get("market_date") != market_date or status.get("stream", "5m") != "5m":
        raise Native5mQualityError("LANE_RECEIPT_SCOPE_MISMATCH")
    hashes: dict[str, str] = {}
    for name in ARTIFACTS:
        receipt = datasets.get(name)
        if not isinstance(receipt, dict) or receipt.get("status", "ready") != "ready":
            raise Native5mQualityError("ARTIFACT_RECEIPT_MISSING")
        actual = _sha(paths[name])
        if receipt.get("sha256", "").removeprefix("sha256:") != actual:
            raise Native5mQualityError("ARTIFACT_HASH_MISMATCH")
        if "watermark" in receipt and receipt["watermark"] != market_date:
            raise Native5mQualityError("ARTIFACT_WATERMARK_MISMATCH")
        hashes[name] = actual
    return hashes


def _evidenced_no_data(row: dict[str, Any], market_date: str, policy: dict[str, Any]) -> bool:
    reason = row.get("reason")
    allowed = policy["justified_no_data"].get(reason)
    if not allowed or row.get("source") not in allowed or row.get("row_count") != 0:
        return False
    if reason == "daily_status_not_tradable":
        return (
            row.get("tradestatus") == "0"
            and row.get("evidence_date") == market_date
            and isinstance(row.get("knowledge_cutoff"), str)
            and str(row.get("artifact_sha256", "")).startswith("sha256:")
        )
    if reason == "zero_turnover_daily_bar":
        return (
            row.get("evidence_date") == market_date
            and row.get("volume") == 0
            and row.get("amount") == 0
            and str(row.get("source_sha256", "")).startswith("sha256:")
        )
    if reason == "suspended":
        start, end = row.get("suspend_start"), row.get("suspend_end")
        return (
            isinstance(row.get("knowledge_cutoff"), str)
            and isinstance(start, str)
            and start <= market_date
            and (end is None or market_date <= end)
        )
    return False


def _coverage(path: Path, market_date: str, policy: dict[str, Any]) -> tuple[set[str], int]:
    pq = importlib.import_module("pyarrow.parquet")
    required = {"security_id", "status", "row_count", "reason"}
    schema = pq.ParquetFile(path).schema_arrow
    if not required <= set(schema.names):
        raise Native5mQualityError("COVERAGE_SCHEMA_INVALID")
    rows = pq.read_table(path).to_pylist()
    ids = [row["security_id"] for row in rows]
    if any(not isinstance(code, str) or not code.isdigit() or len(code) != 6 for code in ids):
        raise Native5mQualityError("COVERAGE_SECURITY_INVALID")
    if len(set(ids)) != len(ids):
        raise Native5mQualityError("COVERAGE_DUPLICATE_SECURITY")
    ready: set[str] = set()
    justified = 0
    for row in rows:
        if row["status"] == "ready":
            if (
                row["row_count"] != policy["ready_rows"]
                or row["reason"] != "ok"
                or row.get("observed_maximum") != market_date + " 15:00:00"
            ):
                raise Native5mQualityError("READY_COVERAGE_INVALID")
            ready.add(row["security_id"])
        elif row["status"] == "justified_no_data":
            if not _evidenced_no_data(row, market_date, policy):
                raise Native5mQualityError("NO_DATA_EVIDENCE_INVALID")
            justified += 1
        else:
            raise Native5mQualityError("UNKNOWN_COVERAGE_STATUS")
    return ready, justified


def _bars(path: Path, market_date: str, ready: set[str], policy: dict[str, Any]) -> int:
    pq = importlib.import_module("pyarrow.parquet")
    required = {
        "stock_code",
        "ts",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "bar_size",
        "market_date",
        "acquired_at",
    }
    parquet = pq.ParquetFile(path)
    if not required <= set(parquet.schema_arrow.names):
        raise Native5mQualityError("BAR_SCHEMA_INVALID")
    expected = set(policy["bucket_labels"])
    buckets: dict[str, set[str]] = {}
    counts: Counter[str] = Counter()
    rows = 0
    columns = sorted(required)
    for batch in parquet.iter_batches(batch_size=65536, columns=columns):
        for row in batch.to_pylist():
            rows += 1
            code = row["stock_code"]
            stamp = row["ts"]
            stamp = datetime.fromisoformat(stamp) if isinstance(stamp, str) else stamp
            if (
                code not in ready
                or not isinstance(stamp, datetime)
                or stamp.date().isoformat() != market_date
                or row["market_date"] != market_date
                or row["bar_size"] != "5m"
                or not row["acquired_at"]
            ):
                raise Native5mQualityError("BAR_SCOPE_INVALID")
            label = stamp.time().isoformat()
            if label in buckets.setdefault(code, set()):
                raise Native5mQualityError("DUPLICATE_BAR_KEY")
            buckets[code].add(label)
            counts[code] += 1
            values = [row[name] for name in ("open", "high", "low", "close", "volume", "amount")]
            if any(value is None or not math.isfinite(float(value)) for value in values):
                raise Native5mQualityError("BAR_NUMERIC_INVALID")
            if (
                row["low"] > min(row["open"], row["close"])
                or row["high"] < max(row["open"], row["close"])
                or row["low"] > row["high"]
                or row["volume"] < 0
                or row["amount"] < 0
            ):
                raise Native5mQualityError("BAR_NUMERIC_RELATION_INVALID")
    if set(buckets) != ready or any(counts[code] != len(expected) for code in ready):
        raise Native5mQualityError("BAR_READY_UNIVERSE_OR_COUNT_MISMATCH")
    if any(buckets[code] != expected for code in ready):
        raise Native5mQualityError("BUCKET_GRID_MISMATCH")
    return rows


def assess_native_5m_release(root: Path, policy_bytes: bytes) -> dict[str, Any]:
    """Recompute one sealed release's bounded physical and semantic admission."""
    policy = _load_policy(policy_bytes)
    paths = _artifact_paths(root)
    try:
        meta = json.loads(paths["intraday_meta"].read_text(encoding="utf-8"))
        market_date = date.fromisoformat(meta["market_date"]).isoformat()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise Native5mQualityError("META_UNREADABLE") from exc
    if (
        meta.get("bar_size") != policy["bar_size"]
        or meta.get("coverage_ok") is not True
        or meta.get("session_complete") is not True
        or meta.get("session_policy_version") != policy["session_policy_version"]
        or meta.get("expected_rows_per_ready_symbol") != policy["ready_rows"]
        or meta.get("unexplained_missing") != 0
        or meta.get("may_derive_from") is not None
    ):
        reason = (
            "DERIVED_5M_FORBIDDEN"
            if meta.get("may_derive_from") is not None
            else "META_GATE_FAILED"
        )
        raise Native5mQualityError(reason)
    hashes = _verify_receipt(paths, market_date)
    ready, justified = _coverage(paths["intraday_coverage"], market_date, policy)
    rows = _bars(paths["intraday_bars"], market_date, ready, policy)
    if (
        meta.get("ready_symbols") != len(ready)
        or meta.get("justified_no_data") != justified
        or meta.get("universe_size") != len(ready) + justified
        or meta.get("row_count") not in (None, rows)
    ):
        raise Native5mQualityError("META_CENSUS_MISMATCH")
    return {
        "contract": CONTRACT,
        "policy_sha256": hashlib.sha256(policy_bytes).hexdigest(),
        "market_date": market_date,
        "rating": "R2",
        "ready_security_days": len(ready),
        "justified_no_data_security_days": justified,
        "bucket_rows": rows,
        "unexplained_security_days": 0,
        "artifact_identity_chain_sha256": hashlib.sha256(
            "\n".join(f"{name}\t{hashes[name]}" for name in sorted(hashes)).encode()
        ).hexdigest(),
        "consumer_admission": policy["allowed_use"],
        "execution_authorized": False,
    }


def assess_native_5m_series(
    roots: Iterable[Path], required_dates: Iterable[str], policy_bytes: bytes
) -> dict[str, Any]:
    """Admit a selected date series; omissions cannot disappear from the denominator."""
    results = [assess_native_5m_release(root, policy_bytes) for root in roots]
    dates = [result["market_date"] for result in results]
    if len(set(dates)) != len(dates):
        raise Native5mQualityError("DUPLICATE_MARKET_DATE")
    required = {date.fromisoformat(day).isoformat() for day in required_dates}
    missing, extra = sorted(required - set(dates)), sorted(set(dates) - required)
    if missing:
        raise Native5mQualityError("REQUIRED_DATE_MISSING:" + ",".join(missing))
    if extra:
        raise Native5mQualityError("OUT_OF_SCOPE_DATE:" + ",".join(extra))
    return {
        "contract": CONTRACT,
        "rating": "R2",
        "required_dates": sorted(required),
        "release_count": len(results),
        "ready_security_days": sum(r["ready_security_days"] for r in results),
        "justified_no_data_security_days": sum(
            r["justified_no_data_security_days"] for r in results
        ),
        "bucket_rows": sum(r["bucket_rows"] for r in results),
        "unexplained_security_days": 0,
        "consumer_admission": results[0]["consumer_admission"] if results else None,
        "execution_authorized": False,
        "releases": sorted(results, key=lambda row: row["market_date"]),
    }

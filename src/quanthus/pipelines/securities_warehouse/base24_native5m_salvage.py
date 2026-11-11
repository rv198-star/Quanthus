"""Recover complete security-days from sealed Base 2.4 failure captures.

The source acquisition rejects a whole bounded window when any observed day has
an incomplete five-minute grid.  This module preserves that fail-closed rule
while materialising only the independently complete days from the immutable
captured response.  Partial and absent days remain unresolved.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq  # type: ignore[import-untyped]

from quanthus.pipelines.securities_warehouse.base24_native5m_acquire import (
    BUCKETS,
    _plan,
    _validate_output,
    _verify_held,
)
from quanthus.providers.native_5m_exact import (
    ExactBar,
    NativeWindow,
    _window_page,
    write_parquet,
)

CONTRACT = "p10-base24-native5m-salvage-v1"
PACKAGE_CONTRACT = "p10-base24-native5m-salvage-package-v1"
PAGE_SIZE = 20_000


class Native5mSalvageError(ValueError):
    """Stable fail-closed reason without source price values."""


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _write(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _complete_days(
    bars: Sequence[ExactBar], window: dict[str, Any]
) -> tuple[tuple[ExactBar, ...], list[str], dict[str, int], list[str]]:
    expected_buckets = set(BUCKETS)
    expected_dates = set(window["expected_session_dates"])
    grouped: dict[str, dict[str, ExactBar]] = {}
    previous = None
    for bar in bars:
        day = bar.timestamp.date().isoformat()
        bucket = bar.timestamp.time().replace(tzinfo=None).isoformat()
        if (
            bar.code != window["code"]
            or day not in expected_dates
            or bucket not in expected_buckets
            or bucket in grouped.setdefault(day, {})
            or previous is not None
            and bar.timestamp <= previous
        ):
            raise Native5mSalvageError("SOURCE_ROW_SCOPE_OR_KEY_INVALID")
        grouped[day][bucket] = bar
        previous = bar.timestamp
    complete_dates = sorted(
        day for day, values in grouped.items() if set(values) == expected_buckets
    )
    partial = {
        day: len(values)
        for day, values in sorted(grouped.items())
        if set(values) != expected_buckets
    }
    complete = tuple(
        bar for day in complete_dates for _, bar in sorted(grouped[day].items())
    )
    absent = sorted(expected_dates - set(grouped))
    if len(complete) != len(complete_dates) * len(BUCKETS):
        raise Native5mSalvageError("RECOVERED_BUCKET_CENSUS_INVALID")
    return complete, complete_dates, partial, absent


def _failure_capture(
    acquisition: Path,
    hold: dict[str, Any],
    window: dict[str, Any],
    parser: Callable[[bytes, NativeWindow, int, int], tuple[tuple[ExactBar, ...], bool]],
) -> tuple[tuple[ExactBar, ...], dict[str, Any]]:
    selected = None
    for member in hold["failure_receipts"]:
        relative = member.get("path")
        if not isinstance(relative, str):
            raise Native5mSalvageError("FAILURE_MEMBER_INVALID")
        failure = acquisition / relative
        try:
            resolved = failure.resolve(strict=True)
            resolved.relative_to(acquisition.resolve(strict=True))
        except (OSError, ValueError) as exc:
            raise Native5mSalvageError("FAILURE_MEMBER_INVALID") from exc
        if failure.is_symlink() or _sha(failure) != member.get("sha256"):
            raise Native5mSalvageError("FAILURE_MEMBER_CHANGED")
        value = json.loads(failure.read_text(encoding="utf-8"))
        if value.get("reason_code") == "BUCKET_CENSUS_INCOMPLETE":
            selected = (failure, member["sha256"])
    if selected is None:
        raise Native5mSalvageError("NO_BUCKET_CENSUS_CAPTURE")
    failure, failure_sha = selected
    wire_root = failure.parent / "wire"
    receipt_path = wire_root / "wire-receipt.json"
    try:
        receipt: dict[str, Any] = json.loads(receipt_path.read_text(encoding="utf-8"))
        records = receipt["records"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise Native5mSalvageError("WIRE_RECEIPT_INVALID") from exc
    if (
        wire_root.is_symlink()
        or receipt.get("contract") != "p10-native-5m-exact-v1"
        or receipt.get("failure") is not None
        or not isinstance(records, list)
        or len(records) != 1
    ):
        raise Native5mSalvageError("WIRE_RECEIPT_INVALID")
    record = records[0]
    wire = wire_root / str(record.get("path", ""))
    actual = {path.name for path in wire_root.iterdir() if path.is_file()}
    if (
        actual != {"wire-receipt.json", wire.name}
        or wire.parent != wire_root
        or wire.is_symlink()
        or not record.get("complete")
        or wire.stat().st_size != record.get("received_bytes")
        or _sha(wire) != record.get("response_sha256")
    ):
        raise Native5mSalvageError("WIRE_CAPTURE_CHANGED")
    bars, terminal = parser(
        wire.read_bytes(),
        NativeWindow(window["code"], window["start_date"], window["end_date"]),
        1,
        PAGE_SIZE,
    )
    if not terminal:
        raise Native5mSalvageError("WIRE_TERMINAL_PAGE_UNPROVEN")
    source = {
        "failure_receipt_sha256": failure_sha,
        "wire_receipt_sha256": _sha(receipt_path),
        "wire_response_sha256": record["response_sha256"],
        "wire_response_size_bytes": record["received_bytes"],
        "actually_observed_at": record.get("last_received_at") or record.get("ended_at"),
        "wire": wire,
    }
    try:
        clock = datetime.fromisoformat(str(source["actually_observed_at"]))
    except ValueError as exc:
        raise Native5mSalvageError("SOURCE_CLOCK_INVALID") from exc
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise Native5mSalvageError("SOURCE_CLOCK_INVALID")
    return bars, source


def _read_exact(path: Path) -> tuple[ExactBar, ...]:
    try:
        table = pq.read_table(path)
    except (OSError, ValueError) as exc:
        raise Native5mSalvageError("RECOVERED_PARQUET_INVALID") from exc
    expected = (
        "code",
        "timestamp",
        "source_values",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
    )
    if (
        tuple(table.column_names) != expected
        or (table.schema.metadata or {}).get(b"contract") != b"p10-native-5m-exact-v1"
    ):
        raise Native5mSalvageError("RECOVERED_PARQUET_INVALID")
    try:
        return tuple(
            ExactBar(row["code"], row["timestamp"], tuple(row["source_values"]))
            for row in table.to_pylist()
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise Native5mSalvageError("RECOVERED_PARQUET_INVALID") from exc


def _audit_partition(
    root: Path,
    window: dict[str, Any],
    parser: Callable[
        [bytes, NativeWindow, int, int], tuple[tuple[ExactBar, ...], bool]
    ] = _window_page,
) -> dict[str, Any]:
    try:
        receipt_path = root / "RECEIPT.json"
        receipt: dict[str, Any] = json.loads(receipt_path.read_text(encoding="utf-8"))
        ready = (root / "READY").read_text(encoding="ascii").strip()
    except (OSError, ValueError, TypeError) as exc:
        raise Native5mSalvageError("SALVAGE_PARTITION_INVALID") from exc
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name not in {"RECEIPT.json", "READY"}
    }
    if (
        root.is_symlink()
        or any(path.is_symlink() for path in root.rglob("*"))
        or receipt.get("contract") != CONTRACT
        or receipt.get("partition_id") != window["partition_id"]
        or ready != _sha(receipt_path)
        or set(receipt.get("files", {})) != actual
        or any(_sha(root / name) != digest for name, digest in receipt["files"].items())
    ):
        raise Native5mSalvageError("SALVAGE_PARTITION_INVALID")
    bars, terminal = parser(
        (root / "source.wire").read_bytes(),
        NativeWindow(window["code"], window["start_date"], window["end_date"]),
        1,
        PAGE_SIZE,
    )
    if not terminal:
        raise Native5mSalvageError("SALVAGE_PARTITION_INVALID")
    complete, dates, partial, absent = _complete_days(bars, window)
    if (
        _read_exact(root / "exact.parquet") != complete
        or receipt.get("rows") != len(complete)
        or receipt.get("complete_dates") != dates
        or receipt.get("partial_date_bucket_counts") != partial
        or receipt.get("absent_dates") != absent
        or receipt.get("partial_required_date_bucket_counts")
        != {
            day: count
            for day, count in partial.items()
            if day in set(window["required_dates"])
        }
        or receipt.get("absent_required_dates")
        != sorted(set(absent) & set(window["required_dates"]))
    ):
        raise Native5mSalvageError("SALVAGE_PARTITION_INVALID")
    return receipt


def audit_salvage(
    root: Path,
    plan_bytes: bytes,
    *,
    parser: Callable[
        [bytes, NativeWindow, int, int], tuple[tuple[ExactBar, ...], bool]
    ] = _window_page,
) -> dict[str, Any]:
    """Cold-verify a package using only its contained wire evidence and code."""
    try:
        manifest_path = root / "manifest.json"
        manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
        ready = (root / "READY").read_text(encoding="ascii").strip()
    except (OSError, ValueError, TypeError) as exc:
        raise Native5mSalvageError("SALVAGE_PACKAGE_INVALID") from exc
    _, windows = _plan(plan_bytes)
    by_id = {window["partition_id"]: window for window in windows}
    members = manifest.get("partitions")
    if (
        root.is_symlink()
        or manifest.get("contract") != PACKAGE_CONTRACT
        or manifest.get("plan_sha256") != hashlib.sha256(plan_bytes).hexdigest()
        or not _digest(manifest.get("selection_evidence_sha256"))
        or not _digest(manifest.get("module_sha256"))
        or not isinstance(manifest.get("code_revision"), str)
        or len(manifest["code_revision"]) != 40
        or ready != _sha(manifest_path)
        or not isinstance(members, list)
    ):
        raise Native5mSalvageError("SALVAGE_PACKAGE_INVALID")
    expected = set()
    rows = source_days = recovered_days = partial_days = absent_days = 0
    for member in members:
        identity = member.get("partition_id")
        if identity not in by_id or identity in expected:
            raise Native5mSalvageError("SALVAGE_PACKAGE_INVALID")
        expected.add(identity)
        receipt = _audit_partition(
            root / "partitions" / identity, by_id[identity], parser
        )
        if member.get("receipt_sha256") != _sha(root / "partitions" / identity / "RECEIPT.json"):
            raise Native5mSalvageError("SALVAGE_PACKAGE_INVALID")
        rows += receipt["rows"]
        source_days += len(receipt["complete_dates"])
        recovered_days += len(receipt["recovered_required_dates"])
        partial_days += len(receipt["partial_required_date_bucket_counts"])
        absent_days += len(receipt["absent_required_dates"])
    physical = {path.name for path in (root / "partitions").iterdir() if path.is_dir()}
    root_files = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }
    allowed = {"manifest.json", "READY"}
    for identity in expected:
        allowed.update(
            {
                f"partitions/{identity}/source.wire",
                f"partitions/{identity}/exact.parquet",
                f"partitions/{identity}/RECEIPT.json",
                f"partitions/{identity}/READY",
            }
        )
    if (
        physical != expected
        or root_files != allowed
        or manifest.get("rows") != rows
        or manifest.get("complete_source_days") != source_days
        or manifest.get("recovered_required_days") != recovered_days
        or manifest.get("partial_required_days_not_recovered") != partial_days
        or manifest.get("absent_required_days_not_recovered") != absent_days
    ):
        raise Native5mSalvageError("SALVAGE_PACKAGE_INVALID")
    return manifest


def build_salvage(
    plan_bytes: bytes,
    acquisition: Path,
    output: Path,
    partition_ids: Iterable[str],
    *,
    selection_evidence_sha256: str,
    code_revision: str,
    module_sha256: str,
    parser: Callable[
        [bytes, NativeWindow, int, int], tuple[tuple[ExactBar, ...], bool]
    ] = _window_page,
) -> dict[str, Any]:
    """Build an immutable package for the selected held bucket-census windows."""
    if (
        not _digest(selection_evidence_sha256)
        or not _digest(module_sha256)
        or len(code_revision) != 40
        or any(character not in "0123456789abcdef" for character in code_revision)
    ):
        raise Native5mSalvageError("SALVAGE_CODE_OR_SELECTION_IDENTITY_INVALID")
    _validate_output(output, (acquisition,))
    staging = output.with_name(f".{output.name}.job")
    _validate_output(staging, (acquisition,))
    if output.exists() or staging.exists():
        raise Native5mSalvageError("SALVAGE_OUTPUT_EXISTS")
    _, windows = _plan(plan_bytes)
    plan_sha = hashlib.sha256(plan_bytes).hexdigest()
    selected = list(partition_ids)
    if not selected or selected != sorted(set(selected)):
        raise Native5mSalvageError("SALVAGE_SELECTION_INVALID")
    by_id = {window["partition_id"]: window for window in windows}
    if any(identity not in by_id for identity in selected):
        raise Native5mSalvageError("SALVAGE_SELECTION_INVALID")
    checkpoint_path = acquisition / "CHECKPOINT.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    if (
        (acquisition / "READY").read_text(encoding="ascii").strip() != _sha(checkpoint_path)
        or checkpoint.get("plan_sha256") != plan_sha
        or checkpoint.get("status") != "COMPLETE_WITH_HELD"
    ):
        raise Native5mSalvageError("ACQUISITION_IDENTITY_INVALID")
    staging.mkdir()
    (staging / "partitions").mkdir()
    members = []
    total_rows = total_source_days = total_recovered = total_partial = total_absent = 0
    try:
        for identity in selected:
            window = by_id[identity]
            held_root = acquisition / "held" / identity
            hold = _verify_held(
                held_root, acquisition / "failures", window, plan_sha
            )
            bars, source = _failure_capture(acquisition, hold, window, parser)
            complete, dates, partial, absent = _complete_days(bars, window)
            required = set(window["required_dates"])
            recovered_required = sorted(required & set(dates))
            partial_required = {
                day: count for day, count in partial.items() if day in required
            }
            absent_required = sorted(required & set(absent))
            if not recovered_required:
                raise Native5mSalvageError("NO_REQUIRED_COMPLETE_DAY")
            partition = staging / "partitions" / identity
            partition.mkdir()
            shutil.copyfile(source["wire"], partition / "source.wire")
            write_parquet(partition / "exact.parquet", complete)
            files = {
                path.name: _sha(path)
                for path in sorted(partition.iterdir())
                if path.is_file()
            }
            receipt = {
                "contract": CONTRACT,
                "partition_id": identity,
                "plan_sha256": plan_sha,
                "parent_acquisition_READY_sha256": _sha(acquisition / "READY"),
                "hold_sha256": _sha(held_root / "HOLD.json"),
                "failure_receipt_sha256": source["failure_receipt_sha256"],
                "wire_receipt_sha256": source["wire_receipt_sha256"],
                "wire_response_sha256": source["wire_response_sha256"],
                "actually_observed_at": source["actually_observed_at"],
                "code": window["code"],
                "rows": len(complete),
                "complete_dates": dates,
                "recovered_required_dates": recovered_required,
                "partial_date_bucket_counts": partial,
                "absent_dates": absent,
                "partial_required_date_bucket_counts": partial_required,
                "absent_required_dates": absent_required,
                "files": files,
            }
            _write(partition / "RECEIPT.json", receipt)
            (partition / "READY").write_text(_sha(partition / "RECEIPT.json") + "\n")
            _audit_partition(partition, window, parser)
            members.append(
                {"partition_id": identity, "receipt_sha256": _sha(partition / "RECEIPT.json")}
            )
            total_rows += len(complete)
            total_source_days += len(dates)
            total_recovered += len(recovered_required)
            total_partial += len(partial_required)
            total_absent += len(absent_required)
        manifest = {
            "contract": PACKAGE_CONTRACT,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "plan_sha256": plan_sha,
            "parent_acquisition_READY_sha256": _sha(acquisition / "READY"),
            "selection_evidence_sha256": selection_evidence_sha256,
            "code_revision": code_revision,
            "module_sha256": module_sha256,
            "selection_partition_ids": selected,
            "partitions": members,
            "rows": total_rows,
            "complete_source_days": total_source_days,
            "recovered_required_days": total_recovered,
            "partial_required_days_not_recovered": total_partial,
            "absent_required_days_not_recovered": total_absent,
            "source_prices_exported_to_evidence": False,
        }
        _write(staging / "manifest.json", manifest)
        (staging / "READY").write_text(_sha(staging / "manifest.json") + "\n")
        audit_salvage(staging, plan_bytes, parser=parser)
        os.rename(staging, output)
        return manifest
    except BaseException:
        raise

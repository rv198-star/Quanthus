"""Checkpointed exact native-5m acquisition for the frozen Base 2.4 gap plan."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from decimal import InvalidOperation
from pathlib import Path
from typing import Any

from quanthus.providers.native_5m_exact import ExactBar, NativeWindow, write_parquet

CONTRACT = "p10-base24-native5m-acquisition-v1"
PLAN_CONTRACT = "p10-base24-native5m-missing-window-plan-v1"
BUCKETS = tuple(
    [f"09:{minute:02d}:00" for minute in range(35, 60, 5)]
    + [f"10:{minute:02d}:00" for minute in range(0, 60, 5)]
    + [f"11:{minute:02d}:00" for minute in range(0, 31, 5)]
    + [f"13:{minute:02d}:00" for minute in range(5, 60, 5)]
    + [f"14:{minute:02d}:00" for minute in range(0, 60, 5)]
    + ["15:00:00"]
)


class Native5mAcquireError(ValueError):
    """Stable acquisition/checkpoint failure reason."""


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


def _validate_output(output: Path, source_roots: Sequence[Path]) -> None:
    if output.is_symlink():
        raise Native5mAcquireError("OUTPUT_SYMLINK_FORBIDDEN")
    if output.exists() and not output.is_dir():
        raise Native5mAcquireError("OUTPUT_NOT_DIRECTORY")
    resolved_output = output.resolve(strict=False)
    for source in source_roots:
        resolved_source = source.resolve(strict=False)
        if (
            resolved_output == resolved_source
            or resolved_source in resolved_output.parents
            or resolved_output in resolved_source.parents
        ):
            raise Native5mAcquireError("OUTPUT_SOURCE_OVERLAP")
    if output.exists():
        for parent in (output, *output.parents):
            if parent.is_symlink():
                raise Native5mAcquireError("OUTPUT_PARENT_SYMLINK_FORBIDDEN")


def _plan(raw: bytes) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        value = json.loads(raw)
        windows = value["windows"]
    except (ValueError, KeyError, TypeError) as exc:
        raise Native5mAcquireError("PLAN_UNREADABLE") from exc
    if (
        value.get("contract") != PLAN_CONTRACT
        or value.get("frequency") != "5"
        or value.get("adjustflag") != "3"
        or value.get("business_through") != "2026-09-30"
        or not isinstance(windows, list)
    ):
        raise Native5mAcquireError("PLAN_CONTRACT_INVALID")
    identities = set()
    total = 0
    for row in windows:
        request = NativeWindow(row["code"], row["start_date"], row["end_date"])
        venue = {"sh": "SSE", "sz": "SZSE"}[request.code[:2]]
        identity = hashlib.sha256(
            f"{venue}\t{request.code[3:]}\t{request.start_date}\t{request.end_date}".encode()
        ).hexdigest()
        required_dates = row.get("required_dates")
        expected_dates = row.get("expected_session_dates")
        if (
            row.get("partition_id") != identity
            or row.get("venue") != venue
            or type(row.get("required_security_days")) is not int
            or row["required_security_days"] < 1
            or type(row.get("expected_source_days")) is not int
            or not isinstance(required_dates, list)
            or not isinstance(expected_dates, list)
            or required_dates != sorted(set(required_dates))
            or expected_dates != sorted(set(expected_dates))
            or not set(required_dates) <= set(expected_dates)
            or row["required_security_days"] != len(required_dates)
            or row["expected_source_days"] != len(expected_dates)
            or not expected_dates
            or expected_dates[0] != request.start_date
            or expected_dates[-1] != request.end_date
            or row.get("maximum_rows") != row["expected_source_days"] * 48
            or identity in identities
        ):
            raise Native5mAcquireError("PARTITION_IDENTITY_OR_SCOPE_INVALID")
        identities.add(identity)
        total += row["required_security_days"]
    if total != value.get("missing_security_days"):
        raise Native5mAcquireError("PLAN_SECURITY_DAY_CENSUS_INVALID")
    return value, windows


def _validate_bars(bars: tuple[ExactBar, ...], window: dict[str, Any]) -> dict[str, Any]:
    expected = set(BUCKETS)
    grouped: dict[str, set[str]] = {}
    for bar in bars:
        day = bar.timestamp.date().isoformat()
        label = bar.timestamp.time().replace(tzinfo=None).isoformat()
        if (
            bar.code != window["code"]
            or not window["start_date"] <= day <= window["end_date"]
            or label not in expected
            or label in grouped.setdefault(day, set())
        ):
            raise Native5mAcquireError("BUCKET_SCOPE_OR_DUPLICATE_INVALID")
        grouped[day].add(label)
    expected_dates = set(window["expected_session_dates"])
    if not set(grouped) <= expected_dates or any(labels != expected for labels in grouped.values()):
        raise Native5mAcquireError("BUCKET_CENSUS_INCOMPLETE")
    return {
        "observed_source_days": len(grouped),
        "source_empty_days": sorted(expected_dates - set(grouped)),
        "observed_rows": len(bars),
    }


def _verify_sealed(root: Path, window: dict[str, Any], plan_sha: str) -> dict[str, Any]:
    pq = __import__("pyarrow.parquet", fromlist=["read_table"])
    try:
        loaded = json.loads((root / "RECEIPT.json").read_text(encoding="utf-8"))
        ready = (root / "READY").read_text(encoding="ascii").strip()
    except (OSError, ValueError, TypeError) as exc:
        raise Native5mAcquireError("SEALED_PARTITION_CHANGED") from exc
    if not isinstance(loaded, dict):
        raise Native5mAcquireError("SEALED_PARTITION_CHANGED")
    receipt: dict[str, Any] = loaded
    files = receipt.get("files", {})
    try:
        actual_files = {
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() and path.name not in {"RECEIPT.json", "READY"}
        }
        if (
            not isinstance(files, dict)
            or set(files) != actual_files
            or receipt.get("contract") != CONTRACT
            or receipt.get("partition_id") != window["partition_id"]
            or receipt.get("plan_sha256") != plan_sha
            or ready != hashlib.sha256((root / "RECEIPT.json").read_bytes()).hexdigest()
            or any(_sha(root / path) != digest for path, digest in files.items())
        ):
            raise Native5mAcquireError("SEALED_PARTITION_CHANGED")
        table = pq.read_table(root / "exact.parquet")
        expected_columns = (
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
            tuple(table.column_names) != expected_columns
            or (table.schema.metadata or {}).get(b"contract") != b"p10-native-5m-exact-v1"
        ):
            raise Native5mAcquireError("SEALED_PARTITION_CHANGED")
        bars = []
        previous = None
        for row in table.to_pylist():
            lexical = row["source_values"]
            if (
                row["code"] != window["code"]
                or not isinstance(lexical, list)
                or len(lexical) != 6
                or any(not isinstance(value, str) for value in lexical)
                or previous is not None
                and row["timestamp"] <= previous
            ):
                raise Native5mAcquireError("SEALED_PARTITION_CHANGED")
            bar = ExactBar(row["code"], row["timestamp"], tuple(lexical))
            values = bar.values
            if any(not value.is_finite() for value in values) or any(
                row[name] != values[index] for index, name in enumerate(expected_columns[3:])
            ):
                raise Native5mAcquireError("SEALED_PARTITION_CHANGED")
            bars.append(bar)
            previous = row["timestamp"]
        census = _validate_bars(tuple(bars), window)
        if (
            receipt.get("rows") != len(bars)
            or receipt.get("source_census") != census
            or len(bars) > window["maximum_rows"]
        ):
            raise Native5mAcquireError("SEALED_PARTITION_CHANGED")
    except (OSError, KeyError, TypeError, ValueError, InvalidOperation) as exc:
        if isinstance(exc, Native5mAcquireError):
            raise
        raise Native5mAcquireError("SEALED_PARTITION_CHANGED") from exc
    return receipt


def _failure_receipts(failures: Path, identity: str) -> list[Path]:
    receipts: list[Path] = []
    if not failures.exists():
        return receipts
    for root in sorted(failures.glob(f"{identity}-*")):
        path = root / "FAILURE.json"
        if root.is_symlink() or path.is_symlink() or not path.is_file():
            raise Native5mAcquireError("FAILURE_HISTORY_INVALID")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise Native5mAcquireError("FAILURE_HISTORY_INVALID") from exc
        if value.get("contract") != CONTRACT or value.get("partition_id") != identity:
            raise Native5mAcquireError("FAILURE_HISTORY_INVALID")
        receipts.append(path)
    return receipts


def _seal_hold(
    root: Path,
    *,
    failures: Path,
    window: dict[str, Any],
    plan_sha: str,
    attempts: list[Path],
) -> None:
    if root.exists() or not attempts:
        raise Native5mAcquireError("HELD_PARTITION_INVALID")
    reasons: Counter[str] = Counter()
    members = []
    for path in attempts:
        value = json.loads(path.read_text(encoding="utf-8"))
        reasons[str(value["reason_code"])] += 1
        members.append(
            {
                "path": path.relative_to(failures.parent).as_posix(),
                "sha256": _sha(path),
            }
        )
    root.mkdir()
    hold = {
        "contract": CONTRACT,
        "disposition": "HELD_AFTER_BOUNDED_FAILURES",
        "partition_id": window["partition_id"],
        "plan_sha256": plan_sha,
        "code": window["code"],
        "start_date": window["start_date"],
        "end_date": window["end_date"],
        "required_dates": window["required_dates"],
        "attempts": len(attempts),
        "reason_counts": dict(sorted(reasons.items())),
        "failure_receipts": members,
        "held_at": datetime.now(timezone.utc).isoformat(),
    }
    _write(root / "HOLD.json", hold)
    (root / "READY").write_text(_sha(root / "HOLD.json") + "\n", encoding="ascii")


def _verify_held(
    root: Path, failures: Path, window: dict[str, Any], plan_sha: str
) -> dict[str, Any]:
    try:
        loaded = json.loads((root / "HOLD.json").read_text(encoding="utf-8"))
        ready = (root / "READY").read_text(encoding="ascii").strip()
        actual = {path.name for path in root.iterdir() if path.is_file()}
    except (OSError, ValueError, TypeError) as exc:
        raise Native5mAcquireError("HELD_PARTITION_CHANGED") from exc
    if not isinstance(loaded, dict):
        raise Native5mAcquireError("HELD_PARTITION_CHANGED")
    hold: dict[str, Any] = loaded
    if (
        root.is_symlink()
        or actual != {"HOLD.json", "READY"}
        or hold.get("contract") != CONTRACT
        or hold.get("disposition") != "HELD_AFTER_BOUNDED_FAILURES"
        or hold.get("partition_id") != window["partition_id"]
        or hold.get("plan_sha256") != plan_sha
        or hold.get("required_dates") != window["required_dates"]
        or type(hold.get("attempts")) is not int
        or hold["attempts"] < 1
        or ready != _sha(root / "HOLD.json")
    ):
        raise Native5mAcquireError("HELD_PARTITION_CHANGED")
    members = hold.get("failure_receipts")
    if not isinstance(members, list) or len(members) != hold["attempts"]:
        raise Native5mAcquireError("HELD_PARTITION_CHANGED")
    for member in members:
        try:
            path = failures.parent / member["path"]
            resolved = path.resolve(strict=True)
            if (
                failures.resolve(strict=True) not in resolved.parents
                or path.is_symlink()
                or _sha(path) != member["sha256"]
            ):
                raise Native5mAcquireError("HELD_PARTITION_CHANGED")
        except (OSError, KeyError, TypeError) as exc:
            if isinstance(exc, Native5mAcquireError):
                raise
            raise Native5mAcquireError("HELD_PARTITION_CHANGED") from exc
    return hold


Fetch = Callable[[Path, NativeWindow], tuple[tuple[ExactBar, ...], dict[str, Any]]]


def run_acquisition(
    plan_bytes: bytes,
    output: Path,
    fetch: Fetch,
    *,
    source_roots: Sequence[Path],
    max_new_partitions: int | None = None,
    max_failures: int | None = None,
    max_attempts_per_partition: int | None = None,
) -> dict[str, Any]:
    """Execute/reuse independent partitions; failed staging never gains READY."""
    _validate_output(output, source_roots)
    if max_new_partitions is not None and max_new_partitions < 1:
        raise Native5mAcquireError("NEW_PARTITION_LIMIT_INVALID")
    if max_failures is not None and max_failures < 1:
        raise Native5mAcquireError("FAILURE_LIMIT_INVALID")
    if max_attempts_per_partition is not None and max_attempts_per_partition < 1:
        raise Native5mAcquireError("ATTEMPT_LIMIT_INVALID")
    plan, windows = _plan(plan_bytes)
    plan_sha = hashlib.sha256(plan_bytes).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    partitions = output / "partitions"
    failures = output / "failures"
    held_root = output / "held"
    staging_root = output / "staging"
    for path in (partitions, failures, held_root, staging_root):
        path.mkdir(exist_ok=True)
    state: dict[str, Any] = {
        "contract": CONTRACT,
        "plan_sha256": plan_sha,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "total": len(windows),
        "completed": 0,
        "reused": 0,
        "failed": 0,
        "held": 0,
        "actual_requests": 0,
        "actual_bytes": 0,
        "failure_circuit": "consecutive",
        "source_roots_modified": False,
    }
    consecutive_failures = 0
    for window in windows:
        identity = window["partition_id"]
        sealed = partitions / identity
        held = held_root / identity
        if held.exists():
            _verify_held(held, failures, window, plan_sha)
            state["held"] += 1
            continue
        if sealed.exists():
            receipt = _verify_sealed(sealed, window, plan_sha)
            state["completed"] += 1
            state["reused"] += 1
            state["actual_requests"] += int(receipt["metrics"].get("actual_requests", 0))
            state["actual_bytes"] += int(receipt["metrics"].get("actual_bytes", 0))
            continue
        attempts = _failure_receipts(failures, identity)
        if max_attempts_per_partition is not None and len(attempts) >= max_attempts_per_partition:
            _seal_hold(
                held,
                failures=failures,
                window=window,
                plan_sha=plan_sha,
                attempts=attempts,
            )
            state["held"] += 1
            continue
        if (
            max_new_partitions is not None
            and state["completed"] - state["reused"] >= max_new_partitions
        ):
            state["stop_reason"] = "NEW_PARTITION_LIMIT"
            break
        staging = staging_root / identity
        if staging.exists():
            suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            staging.rename(failures / f"{identity}-incomplete-{suffix}")
        staging.mkdir()
        try:
            bars, metrics = fetch(
                staging,
                NativeWindow(window["code"], window["start_date"], window["end_date"]),
            )
            source_census = _validate_bars(bars, window)
            write_parquet(staging / "exact.parquet", bars)
            files = {
                path.relative_to(staging).as_posix(): _sha(path)
                for path in sorted(staging.rglob("*"))
                if path.is_file()
            }
            receipt = {
                "contract": CONTRACT,
                "partition_id": identity,
                "plan_sha256": plan_sha,
                "code": window["code"],
                "start_date": window["start_date"],
                "end_date": window["end_date"],
                "required_security_days": window["required_security_days"],
                "expected_source_days": window["expected_source_days"],
                "rows": len(bars),
                "source_census": source_census,
                "files": files,
                "metrics": metrics,
                "sealed_at": datetime.now(timezone.utc).isoformat(),
            }
            _write(staging / "RECEIPT.json", receipt)
            (staging / "READY").write_text(
                hashlib.sha256((staging / "RECEIPT.json").read_bytes()).hexdigest() + "\n",
                encoding="ascii",
            )
            os.rename(staging, sealed)
            consecutive_failures = 0
            state["completed"] += 1
            state["actual_requests"] += int(metrics.get("actual_requests", 0))
            state["actual_bytes"] += int(metrics.get("actual_bytes", 0))
        except BaseException as exc:
            state["failed"] += 1
            consecutive_failures += 1
            failure = {
                "contract": CONTRACT,
                "partition_id": identity,
                "plan_sha256": plan_sha,
                "reason_code": str(exc) if str(exc).isascii() else type(exc).__name__,
                "failed_at": datetime.now(timezone.utc).isoformat(),
            }
            _write(staging / "FAILURE.json", failure)
            suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            failed_root = failures / f"{identity}-{suffix}"
            os.rename(staging, failed_root)
            attempts = [*attempts, failed_root / "FAILURE.json"]
            became_held = False
            if (
                max_attempts_per_partition is not None
                and len(attempts) >= max_attempts_per_partition
            ):
                _seal_hold(
                    held,
                    failures=failures,
                    window=window,
                    plan_sha=plan_sha,
                    attempts=attempts,
                )
                state["held"] += 1
                became_held = True
                consecutive_failures = 0
            if (
                not became_held
                and max_failures is not None
                and consecutive_failures >= max_failures
            ):
                state["stop_reason"] = "FAILURE_LIMIT"
                state["consecutive_failures_at_stop"] = consecutive_failures
                break
    state["finished_at"] = datetime.now(timezone.utc).isoformat()
    terminal = state["completed"] + state["held"] == state["total"]
    state["status"] = (
        "COMPLETE"
        if terminal and not state["held"]
        else "COMPLETE_WITH_HELD"
        if terminal
        else "PARTIAL"
    )
    checkpoint = output / "CHECKPOINT.json"
    temporary = output / ".CHECKPOINT.json.tmp"
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, checkpoint)
    if terminal:
        (output / "READY").write_text(_sha(checkpoint) + "\n", encoding="ascii")
    else:
        (output / "READY").unlink(missing_ok=True)
    for root in source_roots:
        if root.exists() and root.is_dir():
            # The caller binds full source hashes separately; this flag records that
            # the acquisition implementation has no mutation path for them.
            pass
    if not any(staging_root.iterdir()):
        shutil.rmtree(staging_root)
    return state

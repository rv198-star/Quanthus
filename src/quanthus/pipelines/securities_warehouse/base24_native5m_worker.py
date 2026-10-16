"""Bounded node-local worker for a frozen Base 2.4 native-5m shard."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import inspect
import json
import os
import resource
import socket
import subprocess
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.pipelines.securities_warehouse.base24_native5m_acquire import (
    _plan,
    _selection,
    run_acquisition,
)
from quanthus.providers.native_5m_exact import (
    ExactBar,
    NativeWindow,
    captured_socket,
    fetch_captured_window,
)


class Native5mWorkerError(ValueError):
    """Stable node-worker failure reason."""


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _allocated(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(item.stat().st_blocks * 512 for item in path.rglob("*") if item.is_file())


def _free(path: Path) -> int:
    stat = os.statvfs(path)
    return stat.f_bavail * stat.f_frsize


def _data_sends(output: Path) -> int:
    sends = 0
    for receipt_path in sorted(output.rglob("wire-receipt.json")):
        try:
            records = json.loads(receipt_path.read_text(encoding="utf-8"))["records"]
            if not isinstance(records, list):
                raise TypeError
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise Native5mWorkerError("WIRE_RECEIPT_UNREADABLE") from exc
        sends += len(records)
    return sends


def _control_calls(output: Path) -> int:
    ledger = output / "CONTROL.jsonl"
    if not ledger.exists():
        return 0
    calls = 0
    try:
        for line in ledger.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)["event"]
            if event in {"LOGIN_ATTEMPT", "LOGOUT_ATTEMPT"}:
                calls += 1
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise Native5mWorkerError("CONTROL_LEDGER_UNREADABLE") from exc
    return calls


def _append_control(output: Path, value: dict[str, object]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    with (output / "CONTROL.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _write_new(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _git_head(worktree: Path) -> str:
    process = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    )
    return process.stdout.strip()


def _frozen_inputs(
    plan_bytes: bytes,
    selection_bytes: bytes,
) -> tuple[dict[str, Any], str, str]:
    _, windows = _plan(plan_bytes)
    plan_sha = hashlib.sha256(plan_bytes).hexdigest()
    _selection(selection_bytes, plan_sha=plan_sha, windows=windows)
    try:
        selection = json.loads(selection_bytes)
    except (ValueError, TypeError) as exc:
        raise Native5mWorkerError("SELECTION_UNREADABLE") from exc
    return selection, plan_sha, hashlib.sha256(selection_bytes).hexdigest()


def run_worker(args: argparse.Namespace) -> int:
    plan_bytes = args.plan.read_bytes()
    selection_bytes = args.selection.read_bytes()
    selection, plan_sha, selection_sha = _frozen_inputs(plan_bytes, selection_bytes)
    if (
        plan_sha != args.expected_plan_sha256
        or selection_sha != args.expected_selection_sha256
        or _git_head(args.worktree) != args.expected_code_sha
    ):
        raise Native5mWorkerError("WORKER_SEMANTIC_CLOSURE_MISMATCH")
    if args.evidence.is_symlink() or args.output.is_symlink():
        raise Native5mWorkerError("WORKER_PATH_SYMLINK_FORBIDDEN")
    args.evidence.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if (
        _free(args.output.parent) < args.free_floor_bytes
        or _allocated(args.output) > args.max_allocated_bytes
    ):
        raise Native5mWorkerError("RESOURCE_GATE_FAILED_BEFORE_LOGIN")

    prior_data_sends = _data_sends(args.output)
    prior_control_calls = _control_calls(args.output)
    maximum_data_sends = selection["maximum_data_sends"]
    maximum_control_calls = selection["maximum_control_calls"]
    if prior_data_sends >= maximum_data_sends:
        raise Native5mWorkerError("DATA_SEND_BUDGET_EXHAUSTED_BEFORE_LOGIN")
    if prior_control_calls + 2 > maximum_control_calls:
        raise Native5mWorkerError("CONTROL_CALL_BUDGET_EXHAUSTED_BEFORE_LOGIN")

    import baostock as bs  # type: ignore[import-untyped]
    import baostock.common.context as context  # type: ignore[import-untyped]
    import baostock.security.history as history  # type: ignore[import-untyped]

    suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    registration = {
        "UTC": datetime.now(timezone.utc).isoformat(),
        "mode": "shard-resume",
        "node": socket.gethostname(),
        "plan": str(args.plan),
        "plan_SHA256": plan_sha,
        "selection": str(args.selection),
        "selection_SHA256": selection_sha,
        "shard_id": selection["shard_id"],
        "code_SHA": args.expected_code_sha,
        "worker_module_SHA256": _sha(Path(__file__)),
        "sdk_transport_SHA256": hashlib.sha256(
            inspect.getsource(history.__query_history_k_data_plus_page).encode()
        ).hexdigest(),
        "output": str(args.output),
        "source_roots": [str(path) for path in args.source_root],
        "prior_data_sends_in_output": prior_data_sends,
        "prior_control_calls_in_output": prior_control_calls,
        "maximum_data_sends": maximum_data_sends,
        "maximum_control_calls": maximum_control_calls,
        "page_size": 20000,
        "max_pages_per_partition": 1,
        "max_rows_per_partition": 20000,
        "request_spacing_seconds": args.request_spacing_seconds,
        "socket_timeout_seconds": args.socket_timeout_seconds,
        "concurrency": 1,
        "failure_circuit": 3,
        "max_attempts_per_partition": 3,
        "free_floor_bytes": args.free_floor_bytes,
        "max_allocated_bytes": args.max_allocated_bytes,
        "max_RSS_bytes": args.max_rss_bytes,
        "data_free_before_bytes": _free(args.output.parent),
        "output_allocated_before_bytes": _allocated(args.output),
    }
    registration_path = args.evidence / f"registration-{suffix}.json"
    _write_new(registration_path, registration)

    lock_path = args.output.with_name(f".{args.output.name}.lock")
    lock = lock_path.open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    start = time.monotonic()
    logged = False
    process_control_calls = 0
    process_counters = {"data_sends": 0}
    result: dict[str, object] = {"status": "PROCESS_FAILED"}
    old_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(args.socket_timeout_seconds)
    _append_control(
        args.output,
        {
            "UTC": registration["UTC"],
            "event": "PROCESS_START",
            "registration_SHA256": _sha(registration_path),
        },
    )
    try:
        _append_control(
            args.output,
            {"UTC": datetime.now(timezone.utc).isoformat(), "event": "LOGIN_ATTEMPT"},
        )
        process_control_calls += 1
        login = bs.login()
        if str(login.error_code) != "0":
            raise RuntimeError("LOGIN_REJECTED")
        logged = True
        context.default_socket.settimeout(args.socket_timeout_seconds)
        _append_control(
            args.output,
            {"UTC": datetime.now(timezone.utc).isoformat(), "event": "LOGIN_SUCCESS"},
        )

        def fetch(
            staging: Path, request: NativeWindow
        ) -> tuple[tuple[ExactBar, ...], dict[str, Any]]:
            if (
                _free(args.output) < args.free_floor_bytes
                or _allocated(args.output) > args.max_allocated_bytes
                or resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
                > args.max_rss_bytes
                or prior_data_sends + process_counters["data_sends"]
                >= maximum_data_sends
            ):
                raise RuntimeError("RESOURCE_OR_DATA_SEND_STOP_THRESHOLD")
            wire_root = staging / "wire"
            wire_root.mkdir()
            before = time.monotonic()
            capture = None
            try:
                with captured_socket(
                    context,
                    wire_root,
                    max_requests=1,
                    max_bytes=8 * 1024**2,
                    seconds=180,
                ) as capture:

                    def query(*query_args: object) -> Any:
                        time.sleep(args.request_spacing_seconds)
                        return history.__query_history_k_data_plus_page(*query_args)

                    bars = fetch_captured_window(
                        query,
                        request,
                        capture,
                        page_size=20000,
                        max_pages=1,
                        max_rows=20000,
                    )
            finally:
                if capture is not None:
                    process_counters["data_sends"] += len(capture.records)
            return bars, {
                "actual_requests": len(capture.records),
                "actual_bytes": capture.total_bytes,
                "elapsed_seconds": time.monotonic() - before,
                "actually_observed_at": capture.records[-1].get("last_received_at"),
                "provider_original_acquisition": None,
                "frequency": "5",
                "adjustflag": "3",
            }

        result = run_acquisition(
            plan_bytes,
            args.output,
            fetch,
            source_roots=args.source_root,
            max_failures=3,
            max_attempts_per_partition=3,
            selection_bytes=selection_bytes,
        )
    except BaseException as exc:
        result["process_reason_code"] = f"{type(exc).__name__}:{exc}"
    finally:
        if logged:
            _append_control(
                args.output,
                {"UTC": datetime.now(timezone.utc).isoformat(), "event": "LOGOUT_ATTEMPT"},
            )
            process_control_calls += 1
            bs.logout()
            _append_control(
                args.output,
                {"UTC": datetime.now(timezone.utc).isoformat(), "event": "LOGOUT_COMPLETE"},
            )
        socket.setdefaulttimeout(old_timeout)
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()

    summary = {
        **result,
        "registration_SHA256": _sha(registration_path),
        "elapsed_process_seconds": time.monotonic() - start,
        "peak_RSS_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
        "output_allocated_bytes": _allocated(args.output),
        "data_free_after_bytes": _free(args.output),
        "actual_data_sends_this_process": process_counters["data_sends"],
        "prior_data_sends_in_output": prior_data_sends,
        "supplier_control_calls_this_process": process_control_calls,
        "prior_control_calls_in_output": prior_control_calls,
    }
    result_path = args.evidence / f"result-{suffix}.safe.json"
    _write_new(result_path, summary)
    _append_control(
        args.output,
        {
            "UTC": datetime.now(timezone.utc).isoformat(),
            "event": "PROCESS_RESULT",
            "result_SHA256": _sha(result_path),
            "status": result.get("status"),
        },
    )
    return 0 if result.get("status") in {"COMPLETE", "COMPLETE_WITH_HELD"} else 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, action="append", default=[])
    parser.add_argument("--expected-plan-sha256", required=True)
    parser.add_argument("--expected-selection-sha256", required=True)
    parser.add_argument("--expected-code-sha", required=True)
    parser.add_argument("--free-floor-bytes", type=int, required=True)
    parser.add_argument("--max-allocated-bytes", type=int, required=True)
    parser.add_argument("--max-rss-bytes", type=int, default=4 * 1024**3)
    parser.add_argument("--request-spacing-seconds", type=float, default=1.0)
    parser.add_argument("--socket-timeout-seconds", type=float, default=30.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    return run_worker(_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())

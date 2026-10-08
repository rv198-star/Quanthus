"""Opt-in one-atomic-operation runner for P10 1m T / T+1.

This is a durable *mechanism*, not a background collector or enabled timer.
No operation may start after 60m aggregate active work in its market-date/phase.
An already admitted bounded operation is allowed to finish/commit, even when
the cumulative processing budget crosses 60m. All work runs in a distinct
process group with its OWN individual 600s maximum timeout.

Actual deployment to CN01/Hermes requires explicit service integration and
independent input/consumer quality acceptance. Never run the old 4-lane writer.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.pipelines.p10_operating_schedule import start_at
from quanthus.providers.minute_operation_cutoff import new_operation

CST = ZoneInfo("Asia/Shanghai")
PHASE_TO_SCHEDULE = {"T": "minute_T", "T_plus_1": "minute_Tplus1"}
POLICY_ID = "p10-1m-daily-and-tplus1-bounded-ops-v2"


def _atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("xb") as stream:
        stream.write(
            (json.dumps(data, sort_keys=True, ensure_ascii=True) + "\n").encode()
        )
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def _calendar(path: Path) -> list[dict[str, Any]]:
    obj = json.loads(path.read_text())
    if not isinstance(obj, list) or not obj:
        raise ValueError("authoritative exchange calendar rows are required")
    return obj


def _load_state(
    path: Path, target: date, phase: str
) -> dict[str, Any]:
    if path.exists():
        data = json.loads(path.read_text())
        if (
            data.get("schema_version") != 1
            or data.get("market_date") != target.isoformat()
            or data.get("phase") != phase
            or data.get("policy_id") != POLICY_ID
            or not isinstance(data.get("cumulative_processing_seconds"), (int, float))
            or not isinstance(data.get("operations"), list)
        ):
            raise ValueError("existing persisted P10 operation ledger identity mismatch")
        return data
    return {
        "schema_version": 1,
        "policy_id": POLICY_ID,
        "market_date": target.isoformat(),
        "phase": phase,
        "cumulative_processing_seconds": 0.0,
        "request_count": 0,
        "operations": [],
        "inflight_operation": None,
    }


def _operation_digest(argv: Sequence[str]) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(list(argv), separators=(",", ":")).encode()
    ).hexdigest()


def run_one(
    *,
    state_root: Path,
    calendar_file: Path,
    market_date: date,
    phase: str,
    operation_id: str,
    argv: Sequence[str],
    timeout_seconds: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    if (
        phase not in PHASE_TO_SCHEDULE
        or not operation_id
        or "/" in operation_id
        or len(operation_id) > 96
        or not operation_id.replace("-", "").replace("_", "").isalnum()
        or not argv
    ):
        raise ValueError("invalid P10 operation identity, phase or command")
    if timeout_seconds < 1 or timeout_seconds > 600:
        raise ValueError("P10 atomic operation must have 1..600s own timeout")
    rows = _calendar(calendar_file)
    planned = start_at(market_date, PHASE_TO_SCHEDULE[phase], rows)
    actual = (now or datetime.now(CST)).astimezone(CST)
    if actual < planned or actual.date() != planned.date():
        raise ValueError("operation outside authorized calendar trading-day phase")
    state_root.mkdir(parents=True, exist_ok=True)
    base = f"{market_date.isoformat()}-{phase}"
    state_file = state_root / f"{base}.json"
    with (state_root / f"{base}.lock").open("a+") as locked:
        fcntl.flock(locked.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        ledger = _load_state(state_file, market_date, phase)
        prior = next(
            (r for r in ledger["operations"] if r["operation_id"] == operation_id),
            None,
        )
        if prior is not None:
            if prior["command_digest"] != _operation_digest(argv):
                raise ValueError("operation_id reuse with different command")
            return {"status": "already_recorded", "receipt": prior}
        if ledger.get("inflight_operation") is not None:
            raise ValueError("unreconciled in-flight operation; never reset deadline")
        admission = new_operation(
            phase=phase,
            cumulative_phase_seconds=float(ledger["cumulative_processing_seconds"]),
            operation_timeout_seconds=timeout_seconds,
        )
        if admission is None:
            ledger["status"] = "no_new_operations_after_budget_cutoff"
            _atomic(state_file, ledger)
            return {"status": "cutoff_reached", "market_date": market_date.isoformat()}
        started_at = datetime.now(timezone.utc).isoformat()
        inflight = {
            "operation_id": operation_id,
            "started_at": started_at,
            "command_digest": _operation_digest(argv),
            "timeout_seconds": timeout_seconds,
            "admitted_at_cumulative_seconds": admission.elapsed_before_start,
        }
        ledger["inflight_operation"] = inflight
        _atomic(state_file, ledger)
        log_path = state_root / f"{base}-{operation_id}.log"
        started = time.monotonic()
        rc: int | None = None
        timed_out = False
        launch_error: str | None = None
        proc: subprocess.Popen[bytes] | None = None
        try:
            with log_path.open("xb") as output:
                os.chmod(log_path, 0o640)
                proc = subprocess.Popen(
                    list(argv), stdout=output, stderr=subprocess.STDOUT,
                    start_new_session=True, stdin=subprocess.DEVNULL,
                )
                try:
                    rc = proc.wait(timeout=timeout_seconds)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()
                    rc = 124
        except OSError as exc:
            launch_error = f"{type(exc).__name__}: {str(exc)[:200]}"
            rc = 127
        elapsed = time.monotonic() - started
        cumulative = float(ledger["cumulative_processing_seconds"]) + elapsed
        tail = max(0.0, cumulative - max(admission.cutoff_seconds, admission.elapsed_before_start))
        result = {
            **inflight,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "exit_code": rc,
            "timed_out_by_own_operation_limit": timed_out,
            "launch_error": launch_error,
            "duration_seconds": round(elapsed, 3),
            "post_cutoff_drain_seconds": round(tail, 3),
            "cumulative_after_seconds": round(cumulative, 3),
            "completed_after_cutoff": cumulative >= admission.cutoff_seconds,
            "log_path": str(log_path),
            "outcome": "success" if rc == 0 else "failed",
        }
        ledger["cumulative_processing_seconds"] = cumulative
        ledger["operations"].append(result)
        ledger["inflight_operation"] = None
        ledger["status"] = (
            "no_new_operations_after_budget_cutoff"
            if cumulative >= admission.cutoff_seconds
            else "admission_remains_available"
        )
        ledger["updated_at"] = datetime.now(timezone.utc).isoformat()
        _atomic(state_file, ledger)
        return {
            "status": result["outcome"],
            "receipt": result,
            "further_new_operations_admitted": cumulative < admission.cutoff_seconds,
        }


def main(argv: Sequence[str] | None = None) -> int:
    argsv = list(argv) if argv is not None else sys.argv[1:]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-date", type=date.fromisoformat, required=True)
    parser.add_argument("--phase", choices=sorted(PHASE_TO_SCHEDULE), required=True)
    parser.add_argument("--calendar-file", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    opt = parser.parse_args(argsv)
    operation = opt.command[1:] if opt.command and opt.command[0] == "--" else opt.command
    result = run_one(
        state_root=opt.state_root,
        calendar_file=opt.calendar_file,
        market_date=opt.market_date,
        phase=opt.phase,
        operation_id=opt.operation_id,
        argv=operation,
        timeout_seconds=opt.timeout_seconds,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] in {"success", "already_recorded", "cutoff_reached"} else 2


if __name__ == "__main__":
    raise SystemExit(main())

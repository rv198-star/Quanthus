"""Bounded, chronological daily-core recovery; optional minute lanes never run here."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import io
import json
import os
import signal
import subprocess
import sys
import time
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.pipelines.securities_warehouse.daily_core_domain import verify_daily_domain_increment
from quanthus.providers.trading_calendar import fetch_baostock_calendar

ROOT = Path(__file__).resolve().parents[3]
CST = ZoneInfo("Asia/Shanghai")
BASELINE = date(2026, 9, 24)


def pending_market_dates(after: date, through: date, rows: list[dict[str, Any]]) -> list[date]:
    """Require consecutive explicit calendar evidence, never weekday guessing."""
    flags = {}
    for row in rows:
        day = date.fromisoformat(str(row["trade_date"]))
        if not after < day <= through:
            continue
        flag = str(row["is_open"])
        if day in flags or flag not in {"0", "1"}:
            raise ValueError("ambiguous or duplicate daily-core calendar evidence")
        flags[day] = flag
    result = []
    day = after + timedelta(days=1)
    while day <= through:
        if day not in flags:
            raise ValueError(f"missing daily-core trading date evidence: {day}")
        if flags[day] == "1":
            result.append(day)
        day += timedelta(days=1)
    return result


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    with tmp.open("x", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _verified_pointer(pointer: Path, *, legacy: bool = False) -> date:
    obj = json.loads(pointer.read_text())
    kind = "cn01_daily_dwd_current" if legacy else "cn01_daily_core_dwd_current"
    if obj.get("kind") != kind:
        raise ValueError("wrong daily-core prerequisite product kind")
    manifest = (pointer.parent / obj["manifest_path"]).resolve()
    if not manifest.is_relative_to(pointer.parent.resolve()):
        raise ValueError("daily-core prerequisite escapes product root")
    checked = verify_daily_domain_increment(manifest.parent)
    if checked.manifest_sha256 != obj.get(
        "manifest_sha256"
    ) or checked.market_date.isoformat() != obj.get("market_date"):
        raise ValueError("daily-core prerequisite bytes/date do not match pointer")
    return checked.market_date


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--through-date", type=date.fromisoformat)
    parser.add_argument("--max-sessions", type=int, default=2)
    parser.add_argument("--max-wall-seconds", type=int, default=3600)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.max_sessions <= 5 or not 60 <= args.max_wall_seconds <= 14400:
        parser.error("requires 1..5 sessions and 60..14400 seconds")
    now = datetime.now(CST)
    eligible = now.date() if now.hour >= 17 else now.date() - timedelta(days=1)
    through = args.through_date or eligible
    if through > eligible or through < BASELINE:
        parser.error("target must be a completed session on/after recovery baseline")
    core = Path(
        os.environ.get(
            "QUANTHUS_DWD_DAILY_CORE_ROOT", "/srv/quanthus/dwd-dws-v2/daily/products/daily-core"
        )
    )
    if (core / "CURRENT.json").exists():
        parser.error("daily-core root contains legacy CURRENT")
    core.mkdir(parents=True, exist_ok=True)
    pointer = core / "CURRENT_DAILY_CORE.json"
    prior = Path(
        os.environ.get(
            "QUANTHUS_DWD_BOOTSTRAP_PRIOR_POINTER", "/srv/quanthus/dwd-dws-v2/daily/CURRENT.json"
        )
    )
    with (core / ".cycle.lock").open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"status": "already_running", "product_scope": "daily-core-v1"}))
            return 0
        started = time.monotonic()
        run = core / "operations" / now.strftime("%Y%m%dT%H%M%S%f%z")
        state: dict[str, Any] = {
            "schema_version": 1,
            "product_scope": "daily-core-v1",
            "started_at": now.isoformat(),
            "invocation_id": os.environ.get("INVOCATION_ID"),
            "through_date": through.isoformat(),
            "attempts": [],
            "minute_products": "separate_not_qualified_by_daily_core",
        }
        _write(core / "operations/LATEST.json", {**state, "status": "initializing"})

        def checkpoint(phase: str) -> None:
            state["phase"] = phase
            _write(run / "run.json", {**state, "status": "running"})
            _write(core / "operations/LATEST.json", {**state, "status": "running"})

        try:
            checkpoint("verify_prior_daily")
            after = (
                _verified_pointer(pointer)
                if pointer.exists()
                else _verified_pointer(prior, legacy=True)
            )
            if not pointer.exists() and after != BASELINE - timedelta(days=1):
                raise ValueError("first daily-core recovery must bind 2026-09-23 legacy baseline")
            if after > through:
                raise ValueError("daily-core requested date is older than current product")
            if after == through:
                dates = []
                calendar = []
            else:
                if (through - after).days > 45:
                    raise ValueError("calendar catch-up exceeds bounded 45-day recovery window")
                checkpoint("calendar")
                with redirect_stdout(io.StringIO()):
                    calendar = fetch_baostock_calendar(after + timedelta(days=1), through)
                dates = pending_market_dates(after, through, calendar)
            state["calendar_sha256"] = hashlib.sha256(
                json.dumps(calendar, sort_keys=True).encode()
            ).hexdigest()
            state["calendar_source"] = "baostock.query_trade_dates"
            state["calendar_rows"] = calendar
            state["pending_dates"] = [x.isoformat() for x in dates]
            state["selected_dates"] = [x.isoformat() for x in dates[: args.max_sessions]]
            if args.dry_run:
                state["status"] = "plan_only"
            elif not dates:
                state["status"] = "caught_up_to_latest_completed_session"
            else:
                for target in dates[: args.max_sessions]:
                    state["phase"] = "ods_and_dwd"
                    remaining = args.max_wall_seconds - (time.monotonic() - started)
                    if remaining < 60:
                        state["status"] = "bounded_budget_exhausted"
                        break
                    command = [
                        sys.executable,
                        str(ROOT / "ops/bin/run-cn01-ods-daily"),
                        "--product-scope",
                        "daily-core-v1",
                        "--market-date",
                        target.isoformat(),
                        "--recovery-window-seconds",
                        str(max(1, int(remaining - 10))),
                        "--dwd-sla-seconds",
                        str(max(1, int(remaining - 10))),
                    ]
                    if not pointer.exists():
                        command += ["--bootstrap-prior-pointer", str(prior)]
                    run.mkdir(parents=True, exist_ok=True)
                    log = run / f"{target.isoformat()}.log"
                    _write(
                        run / "run.json",
                        {**state, "status": "running", "active_market_date": target.isoformat()},
                    )
                    _write(
                        core / "operations/LATEST.json",
                        {**state, "status": "running", "active_market_date": target.isoformat()},
                    )
                    with log.open("x") as output:
                        proc = subprocess.Popen(
                            command, stdout=output, stderr=subprocess.STDOUT, start_new_session=True
                        )
                        try:
                            rc = proc.wait(timeout=remaining)
                        except subprocess.TimeoutExpired:
                            os.killpg(proc.pid, signal.SIGTERM)
                            try:
                                proc.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                os.killpg(proc.pid, signal.SIGKILL)
                                proc.wait()
                            rc = 124
                    attempt = {"market_date": target.isoformat(), "returncode": rc, "log": str(log)}
                    state["attempts"].append(attempt)
                    if rc != 0:
                        state["status"] = "required_daily_input_or_build_failed"
                        break
                    if _verified_pointer(pointer) != target:
                        raise ValueError(
                            "daily-core process success did not advance verified product"
                        )
                    state["status"] = "selected_sessions_completed"
                state["remaining_dates"] = [
                    x.isoformat()
                    for x in dates
                    if x.isoformat()
                    not in {a["market_date"] for a in state["attempts"] if a["returncode"] == 0}
                ]
            result = 0 if state["status"] != "required_daily_input_or_build_failed" else 2
        except (OSError, ValueError, RuntimeError) as exc:
            state["status"] = "blocked_required_core_evidence"
            state["error"] = str(exc)[:1500]
            state["error_class"] = type(exc).__name__
            result = 2
        state["elapsed_seconds"] = round(time.monotonic() - started, 3)
        _write(run / "run.json", state)
        _write(core / "operations/LATEST.json", state)
        print(json.dumps(state, ensure_ascii=False, sort_keys=True))
        return result

"""Frozen-input CN02 Daily pipeline; no acquisition, CURRENT or timer changes.

One catalog and one immutable core feed the native-5m composite. The final job
receipt is written only after independent product verification. Historical replay
is an explicit mode and can never establish natural T-day delivery.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import signal
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import canonical, file_sha
from .base22_sources import _read_json, disjoint_output, physical_path
from .daily_base24 import build, verify
from .daily_core_domain import build_daily_domain_increment
from .daily_core_ods_catalog import (
    DAILY_CORE_PRODUCT_SCOPE,
    build_daily_ods_catalog,
    verify_daily_ods_catalog,
)

CST = ZoneInfo("Asia/Shanghai")
KIND = "cn02_base24_daily_frozen_job_v1"
LANES = {"market_daily_core", "calendar_daily_events"}
WINDOW = {
    "schema_version": "quanthus.base24-daily-window.v1",
    "timezone": "Asia/Shanghai",
    "start_time": "17:30:00",
    "sealed_input_deadline": "20:45:00",
    "delivery_deadline": "21:00:00",
    "default_intervals": ["1d", "5m"],
    "one_minute": "NOT_IN_PRODUCT",
    "weekly_on_daily_critical_path": False,
    "late_disposition": "ALERT_AFFECTED_SCOPE_AND_BOUNDED_RECOVERY",
    "earliest_execution": "T+1_OPEN_UNCHANGED",
    "production_activation": "SEPARATE_CONFIRMATION_REQUIRED",
}


def _object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("DAILY_JOB_OBJECT_REQUIRED")
    return value


def _write(path: Path, value: Any) -> None:
    with path.open("xb") as stream:
        stream.write(canonical(value) + b"\n")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def wait_for_plan(plan_root: Path) -> Path:
    """Wait for one checksum-sealed intake plan, reserving 15m before delivery.

    Only the plan and its seal are polled. This does not run a collector, rescan
    payloads, choose a previous date or treat a partial plan as ready.
    """
    plan_root = physical_path(plan_root)
    if not plan_root.is_dir():
        raise ValueError("DAILY_JOB_PLAN_ROOT_MISSING")
    day = _now().astimezone(CST).date()
    delivery_window(day, _now(), "t-day")
    stop = datetime.fromisoformat(f"{day}T{WINDOW['sealed_input_deadline']}").replace(tzinfo=CST)
    plan_path = plan_root / f"{day}.json"
    seal = plan_root / f"{day}.json.READY"
    while _now() < stop:
        physical_path(plan_path)
        physical_path(seal)
        if plan_path.is_file() and seal.is_file():
            if seal.read_text().strip() != file_sha(plan_path):
                raise ValueError("DAILY_JOB_INTAKE_PLAN_SEAL_DIFFERS")
            return plan_path
        time.sleep(min(30, max(0, (stop - _now()).total_seconds())))
    raise TimeoutError("DAILY_JOB_SEALED_INPUT_DEADLINE")


def delivery_window(day: date, now: datetime, mode: str) -> datetime | None:
    """No weekday heuristic: the qualified source/calendar decide open sessions."""
    if now.tzinfo is None or mode not in {"t-day", "replay"}:
        raise ValueError("DAILY_JOB_CLOCK_OR_MODE_INVALID")
    if mode == "replay":
        return None
    local = now.astimezone(CST)
    start = datetime.fromisoformat(f"{day}T{WINDOW['start_time']}").replace(tzinfo=CST)
    end = datetime.fromisoformat(f"{day}T{WINDOW['delivery_deadline']}").replace(tzinfo=CST)
    if local < start or local >= end:
        raise ValueError("DAILY_JOB_OUTSIDE_T_DAY_WINDOW")
    return end


@contextmanager
def _wall_stop(seconds: float) -> Iterator[None]:
    # This CLI runs one Job in its own process. Preserve a caller's signal state;
    # do not silently cancel another owner's active timer.
    if signal.getitimer(signal.ITIMER_REAL)[0]:
        raise ValueError("DAILY_JOB_PROCESS_TIMER_ALREADY_ACTIVE")

    def expired(signum: int, frame: Any) -> None:
        raise TimeoutError("DAILY_JOB_WALL_STOP")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def validate_plan_inputs(plan: dict[str, Any]) -> dict[str, Path]:
    """Inspect frozen controls only; domain/payload qualification belongs to the DAG.

    Shared by the intake publisher and the consumer so neither can silently
    select another release or accept a different business date.
    """
    if (
        plan.get("kind") != KIND
        or not re.fullmatch(r"[0-9a-f]{40}", str(plan.get("code_revision", "")))
        or set(plan.get("lanes", {})) != LANES
        or plan.get("consumer_admission") != "NOT_GRANTED"
        or plan.get("execution_authorized") is not False
    ):
        raise ValueError("DAILY_JOB_PLAN_CONTRACT_INVALID")
    if date.fromisoformat(plan["market_date"]).isoformat() != plan["market_date"]:
        raise ValueError("DAILY_JOB_SOURCE_DATE_INVALID")
    paths = {}
    for key in ("source_root", "native5m", "base", "release_config", "prior_pointer"):
        raw = plan[key]
        if not isinstance(raw, str) or not Path(raw).is_absolute():
            raise ValueError("DAILY_JOB_ABSOLUTE_INPUT_REQUIRED")
        paths[key] = physical_path(Path(raw))
    controls = {
        "native5m": paths["native5m"] / "manifest.json",
        "base": paths["base"] / "manifest.json",
        "release_config": paths["release_config"],
        "prior_pointer": paths["prior_pointer"],
    }
    for lane, entry in plan["lanes"].items():
        relative = Path(entry["release_path"])
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError("DAILY_JOB_LANE_PATH_INVALID")
        controls[lane] = physical_path(paths["source_root"] / relative / "release.json")
    if set(plan.get("control_sha256", {})) != set(controls) - LANES:
        raise ValueError("DAILY_JOB_FROZEN_CONTROL_SET_DIFFERS")
    expected = {k: plan["lanes"][k]["sha256"] for k in LANES}
    expected.update(plan["control_sha256"])
    if set(expected) != set(controls) or any(
        not re.fullmatch(r"sha256:[0-9a-f]{64}", str(expected[k])) or file_sha(p) != expected[k]
        for k, p in controls.items()
    ):
        raise ValueError("DAILY_JOB_FROZEN_INPUT_CHANGED")
    for key in (*sorted(LANES), "native5m"):
        if _object(controls[key]).get("market_date") != plan["market_date"]:
            raise ValueError("DAILY_JOB_SOURCE_DATE_DIFFERS")
    return paths


def _prepare(
    plan_path: Path, window_path: Path, output: Path
) -> tuple[dict[str, Any], dict[str, Path], Path, Path]:
    plan_path, window_path = physical_path(plan_path), physical_path(window_path)
    plan, _, _ = _read_json(plan_path)
    if _object(window_path) != WINDOW:
        raise ValueError("DAILY_JOB_WINDOW_CONTRACT_DIFFERS")
    paths = validate_plan_inputs(plan)
    roots = [*paths.values(), plan_path, window_path]
    # All preflight checks precede creation of the output or sibling lock.
    output = disjoint_output(output, roots)
    lock = disjoint_output(output.with_name("." + output.name + ".lock"), roots)
    return plan, paths, output, lock


def run(
    *, plan_path: Path, window_path: Path, output: Path, mode: str, max_seconds: int = 900
) -> dict[str, Any]:
    if type(max_seconds) is not int or not 1 <= max_seconds <= 12600:
        raise ValueError("DAILY_JOB_WALL_BUDGET_INVALID")
    plan, paths, output, lock = _prepare(plan_path, window_path, output)
    day = date.fromisoformat(plan["market_date"])
    end = delivery_window(day, _now(), mode)
    budget = min(max_seconds, (end - _now()).total_seconds()) if end else max_seconds
    if budget <= 0:
        raise TimeoutError("DAILY_JOB_DELIVERY_DEADLINE")
    if not output.parent.is_dir():
        raise ValueError("DAILY_JOB_PARENT_MUST_EXIST")
    identity = {
        "plan_sha256": file_sha(plan_path),
        "window_sha256": file_sha(window_path),
        "code_revision": plan["code_revision"],
        "mode": mode,
    }
    if _object(plan_path) != plan or _object(window_path) != WINDOW:
        raise ValueError("DAILY_JOB_FROZEN_INPUT_CHANGED")
    # O_NOFOLLOW also closes a final-component symlink replacement race.
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a"), _wall_stop(budget):
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if output.exists():
            receipt = _object(output / "receipt.json")
            if (
                receipt.get("identity") != identity
                or receipt.get("market_date") != str(day)
                or receipt.get("consumer_admission") != "NOT_GRANTED"
                or receipt.get("execution_authorized") is not False
                or (output / "READY").read_text().strip() != file_sha(output / "receipt.json")
                or receipt.get("product_manifest_sha256")
                != verify(output / "product")["manifest_sha256"]
            ):
                raise ValueError("DAILY_JOB_EXISTING_IDENTITY_OR_PRODUCT_DIFFERS")
            return receipt
        output.mkdir(mode=0o700)
        _write(output / "frozen-plan.json", plan)
        stages = []
        started = _now()

        def record(name: str, began: float, manifest: Path) -> None:
            files = [p for p in manifest.parent.rglob("*") if p.is_file()]
            stages.append(
                {
                    "stage": name,
                    "elapsed_seconds": time.monotonic() - began,
                    "manifest_sha256": file_sha(manifest),
                    "row_counts": _object(manifest).get("row_counts"),
                    "file_count": len(files),
                    "size_bytes": sum(p.stat().st_size for p in files),
                    "disposition": "BUILT_ONCE" if name != "product_replay_verify" else "REPLAYED",
                }
            )
            if end and _now() >= end:
                raise TimeoutError("DAILY_JOB_DELIVERY_DEADLINE")

        try:
            began = time.monotonic()
            catalog = build_daily_ods_catalog(
                release_root=paths["source_root"],
                market_date=day,
                output=output / "catalog",
                product_scope=DAILY_CORE_PRODUCT_SCOPE,
            )
            selected = _object(catalog.root / "catalog.json")["lanes"]
            if {
                item["lane"]: {
                    "release_path": item["release_path"],
                    "sha256": item["release_manifest_sha256"],
                }
                for item in selected
            } != plan["lanes"]:
                raise ValueError("DAILY_JOB_SELECTED_SOURCE_DIFFERS")
            record("catalog", began, catalog.root / "catalog.json")
            began = time.monotonic()
            core = build_daily_domain_increment(
                catalog_root=catalog.root,
                release_root=paths["source_root"],
                base_root=paths["base"],
                release_config=paths["release_config"],
                output=output / "core",
                code_revision=plan["code_revision"],
                prior_pointer=paths["prior_pointer"],
            )
            record("core", began, core.root / "manifest.json")
            began = time.monotonic()
            build(
                core=core.root,
                native5m=paths["native5m"],
                config=paths["release_config"],
                output=output / "product",
                code_revision=plan["code_revision"],
            )
            record("composite", began, output / "product/manifest.json")
            began = time.monotonic()
            result = verify(output / "product")
            record("product_replay_verify", began, output / "product/manifest.json")
            # A later release/control replacement is not silently a new input revision.
            if (
                file_sha(plan_path) != identity["plan_sha256"]
                or file_sha(window_path) != identity["window_sha256"]
            ):
                raise ValueError("DAILY_JOB_FROZEN_INPUT_CHANGED")
            _prepare(plan_path, window_path, output)
            verify_daily_ods_catalog(output / "catalog")
            finished = _now()
            if end and finished >= end:
                raise TimeoutError("DAILY_JOB_DELIVERY_DEADLINE")
            receipt = {
                "kind": KIND,
                "identity": identity,
                "market_date": str(day),
                "status": "VERIFIED_ISOLATED_DELIVERY",
                "T_day_build_completed_before_deadline": mode == "t-day",
                "natural_T_day_acquisition_claim": False,
                "started_at": started.isoformat(),
                "finished_at": finished.isoformat(),
                "deadline": end.isoformat() if end else None,
                "row_counts": result["row_counts"],
                "source_grades": result["source_grades"],
                "product_manifest_sha256": result["manifest_sha256"],
                "stages": stages,
                "consumer_admission": "NOT_GRANTED",
                "execution_authorized": False,
                "weekly_completion_claim": False,
                "production_switch": False,
            }
            _write(output / "receipt.json", receipt)
            (output / "READY").write_text(file_sha(output / "receipt.json") + "\n")
            return receipt
        except BaseException as exc:
            (output / "READY").unlink(missing_ok=True)
            _write(
                output / "FAILURE.safe.json",
                {
                    "status": "FAILED_AFFECTED_SCOPE_NOT_DELIVERED",
                    "exception_type": type(exc).__name__,
                    "completed_stages": stages,
                    "failed_evidence_retained": True,
                    "consumer_admission": "NOT_GRANTED",
                },
            )
            raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--plan", type=Path)
    selection.add_argument("--watch-plan-root", type=Path)
    parser.add_argument("--window", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--job-root", type=Path)
    parser.add_argument("--mode", choices=["t-day", "replay"])
    parser.add_argument("--max-seconds", type=int, default=900)
    args = parser.parse_args()
    try:
        if args.watch_plan_root:
            if args.output or args.mode or args.job_root is None:
                raise ValueError("DAILY_JOB_SCHEDULE_ARGUMENTS_INVALID")
            if _object(physical_path(args.window)) != WINDOW:
                raise ValueError("DAILY_JOB_WINDOW_CONTRACT_DIFFERS")
            if not physical_path(args.job_root).is_dir():
                raise ValueError("DAILY_JOB_PARENT_MUST_EXIST")
            disjoint_output(args.job_root, [args.watch_plan_root])
            args.plan = wait_for_plan(args.watch_plan_root)
            args.output = args.job_root / _now().astimezone(CST).date().isoformat()
            args.mode = "t-day"
        elif args.output is None or args.mode is None or args.job_root is not None:
            raise ValueError("DAILY_JOB_MANUAL_ARGUMENTS_INVALID")
        result = run(
            plan_path=args.plan,
            window_path=args.window,
            output=args.output,
            mode=args.mode,
            max_seconds=args.max_seconds,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        # Raw provider errors/tracebacks can contain response values or credentials.
        print(
            json.dumps(
                {
                    "status": "FAILED_AFFECTED_SCOPE",
                    "exception_type": type(exc).__name__,
                    "alert_required": True,
                    "delivery_claim": False,
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

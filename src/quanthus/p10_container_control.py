"""Host scheduler wrapper for P10 jobs and lightweight API notifications."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import signal
import subprocess
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.p10_operational_mail import atomic, deliver, validate
from quanthus.pipelines.securities_warehouse.container_code import digest
from quanthus.pipelines.securities_warehouse.container_jobs import event


def settings(path: Path) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(path.read_bytes())
    if (
        value.get("kind") != "p10-container-deployment-v1"
        or not re.fullmatch(r"[0-9a-f]{40}", str(value.get("revision")))
        or not re.fullmatch(
            r"(?:[a-z0-9][a-z0-9_.:/-]*@)?sha256:[0-9a-f]{64}", str(value.get("image"))
        )
        or not isinstance(value.get("compose_env"), dict)
        or not 30 <= value.get("timeout_seconds", 0) <= 12600
    ):
        raise ValueError("P10_CONTAINER_DEPLOYMENT_INVALID")
    compose = Path(value["compose"])
    if digest(compose) != value["compose_sha256"]:
        raise ValueError("P10_CONTAINER_COMPOSE_CHANGED")
    allowed = {
        "QUANTHUS_DATA_UID",
        "QUANTHUS_DATA_GID",
        "QUANTHUS_DAILY_CONFIG",
        "QUANTHUS_WEEKLY_CONFIG",
        "QUANTHUS_BASE_ROOT",
        "QUANTHUS_RUNTIME_ROOT",
        "QUANTHUS_HISTORY_ROOT",
        "QUANTHUS_WEEKLY_INPUT_ROOT",
        "QUANTHUS_WEEKLY_JOB_ROOT",
    }
    if set(value["compose_env"]) != allowed or any(
        not isinstance(v, str) or not v or "\n" in v for v in value["compose_env"].values()
    ):
        raise ValueError("P10_CONTAINER_ENVIRONMENT_INVALID")
    state = Path(value["state"])
    if not state.is_absolute() or state.resolve() != state or not state.is_dir():
        raise ValueError("P10_CONTAINER_PHYSICAL_STATE_REQUIRED")
    # State/outbox must not mutate any mounted source, including ancestors.
    for key in ("QUANTHUS_BASE_ROOT", "QUANTHUS_HISTORY_ROOT", "QUANTHUS_WEEKLY_INPUT_ROOT"):
        source = Path(value["compose_env"][key]).resolve(strict=True)
        if state.is_relative_to(source) or source.is_relative_to(state):
            raise ValueError("P10_CONTAINER_STATE_SOURCE_OVERLAP")
    return value


def run(config: dict[str, Any], role: str, day: date) -> dict[str, Any]:
    if role not in {"daily", "recovery", "weekly"}:
        raise ValueError("P10_CONTAINER_ROLE_INVALID")
    state = Path(config["state"])
    for name in ("events", "logs", "mail"):
        path = state / name
        if path.resolve() != path:
            raise ValueError("P10_CONTAINER_STATE_LINK_FORBIDDEN")
    for name in ("events", "logs", "mail"):
        (state / name).mkdir(exist_ok=True)
    run_id = f"{role}-{day}-{uuid.uuid4().hex}"
    target = state / "events" / (run_id + ".json")
    prefix = ["docker", "compose", "--project-name", "quanthus-data", "--file", config["compose"]]
    env = {
        "PATH": os.defpath,
        "HOME": os.environ.get("HOME", "/srv/agentdock"),
        **config["compose_env"],
        "QUANTHUS_PIPELINE_IMAGE": config["image"],
        "QUANTHUS_EVENT_ROOT": str(state / "events"),
    }
    # Do not accidentally select a different daemon, Compose file, profile or .env.
    env["COMPOSE_DISABLE_ENV_FILE"] = "1"
    command = [
        *prefix,
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "--name",
        run_id,
        role,
        role,
        "--config",
        "/run/config/weekly.json" if role == "weekly" else "/run/config/daily.json",
        "--day",
        str(day),
        "--run-id",
        run_id,
        "--event",
        "/run/events/" + target.name,
    ]
    lock_name = "weekly" if role == "weekly" else "daily"
    with (state / (lock_name + ".lock")).open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "busy", "role": role}
        process = None
        interrupted = False

        def stop(signum: int, frame: Any) -> None:
            nonlocal interrupted
            interrupted = True
            raise InterruptedError("P10_CONTAINER_STOP_REQUESTED")

        handlers = {s: signal.signal(s, stop) for s in (signal.SIGTERM, signal.SIGINT)}
        failed = False
        try:
            with (state / "logs" / (run_id + ".log")).open("xb") as log:
                process = subprocess.Popen(command, env=env, stdout=log, stderr=log)
                failed = process.wait(timeout=config["timeout_seconds"]) != 0
        except (OSError, subprocess.TimeoutExpired, InterruptedError):
            failed = True
            # Names are unique to this invocation. Never stop another job/container.
            with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                subprocess.run(
                    ["docker", "stop", "--time", "30", run_id],
                    env=env,
                    timeout=40,
                    capture_output=True,
                    check=False,
                )
        finally:
            for s, handler in handlers.items():
                signal.signal(s, handler)
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        if failed or not target.exists():
            value = event(role, day, run_id, config["revision"], {"status": "CONTAINER_RUN_FAILED"})
            atomic(target, value)
        else:
            value = json.loads(target.read_bytes())
            validate(value)
            if any(
                value[k] != v
                for k, v in {
                    "run_id": run_id,
                    "role": role,
                    "market_date": str(day),
                    "pipeline_revision": config["revision"],
                }.items()
            ):
                raise ValueError("P10_CONTAINER_EVENT_IDENTITY_DIFFERS")
        mail = "not_configured"
        if config.get("mail_secret"):
            try:
                mail = deliver(target, state / "mail", Path(config["mail_secret"]))
            except Exception:
                mail = "failed_event_retained"
        result = {"status": value["status"], "event": str(target), "mail": mail}
        if failed or interrupted or value["status"] == "failed":
            raise RuntimeError("P10_CONTAINER_JOB_FAILED_SEE_RETAINED_EVENT")
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=["daily", "recovery", "weekly", "mail"])
    parser.add_argument("--deployment", type=Path, required=True)
    parser.add_argument("--day", type=date.fromisoformat)
    parser.add_argument("--event", type=Path)
    args = parser.parse_args()
    config = settings(args.deployment)
    if args.role == "mail":
        if args.event is None or args.event.parent.resolve() != Path(config["state"]) / "events":
            raise ValueError("P10_MAIL_EVENT_DESTINATION_INVALID")
        result: Any = deliver(
            args.event, Path(config["state"]) / "mail", Path(config["mail_secret"])
        )
    else:
        day = args.day or datetime.now(ZoneInfo("Asia/Shanghai")).date()
        if day > datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Shanghai")).date():
            raise ValueError("P10_CONTAINER_FUTURE_DATE")
        result = run(config, args.role, day)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()

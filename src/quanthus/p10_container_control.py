"""Host triggers only: all P10 data, mail, backup and cleanup work is in one image."""

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

from quanthus import p10_tmp
from quanthus.p10_operational_mail import atomic, validate
from quanthus.pipelines.securities_warehouse.container_code import digest
from quanthus.pipelines.securities_warehouse.container_jobs import event


def no_new_privileges() -> bool:
    """Inspect the actual launcher, including inherited systemd restrictions."""
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("NoNewPrivs:"):
            return line.split()[1] == "1"
    raise ValueError("P10_CONTAINER_NO_NEW_PRIVILEGES_UNDETERMINED")


def settings(path: Path) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(path.read_bytes())
    docker_command(value)  # Validate the fixed access mode before any state writes.
    if value.get("docker_access") == "sudo-noninteractive" and no_new_privileges():
        raise ValueError("P10_CONTAINER_SUDO_BLOCKED_BY_NO_NEW_PRIVILEGES")
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
        "QUANTHUS_PRIOR_ANCHOR",
        "QUANTHUS_DAILY_CONFIG",
        "QUANTHUS_WEEKLY_CONFIG",
        "QUANTHUS_BASE_ROOT",
        "QUANTHUS_RUNTIME_ROOT",
        "QUANTHUS_HISTORY_ROOT",
        "QUANTHUS_WEEKLY_INPUT_ROOT",
        "QUANTHUS_WEEKLY_JOB_ROOT",
        "QUANTHUS_TMP_ROOT",
        "QUANTHUS_MAIL_SECRET",
        "QUANTHUS_BACKUP_SELECTION",
        "QUANTHUS_BACKUP_ROOT",
        "QUANTHUS_GDRIVE_CONFIG_ROOT",
    }
    if set(value["compose_env"]) != allowed or any(
        not isinstance(v, str) or not v or "\n" in v for v in value["compose_env"].values()
    ):
        raise ValueError("P10_CONTAINER_ENVIRONMENT_INVALID")
    state = Path(value["state"])
    if not state.is_absolute() or state.resolve() != state or not state.is_dir():
        raise ValueError("P10_CONTAINER_PHYSICAL_STATE_REQUIRED")
    roots: dict[str, Path] = {}
    for key, name in value["compose_env"].items():
        if key == "QUANTHUS_TMP_ROOT":
            continue  # The existing TMP validator owns its stricter contract/reason codes.
        if not key.endswith(("_ROOT", "_CONFIG", "_SECRET", "_SELECTION")):
            continue
        path = Path(name)
        if (
            not path.is_absolute()
            or path.resolve(strict=True) != path
            or not (path.is_dir() if key.endswith("_ROOT") else path.is_file())
        ):
            raise ValueError("P10_CONTAINER_PHYSICAL_MOUNT_REQUIRED")
        roots[key] = path
    anchor = Path(value["compose_env"]["QUANTHUS_PRIOR_ANCHOR"])
    if (
        not anchor.is_absolute()
        or anchor.resolve(strict=True) != anchor
        or anchor == Path("/")
        or anchor
        != Path(
            os.path.commonpath(
                [
                    roots[k]
                    for k in (
                        "QUANTHUS_BASE_ROOT",
                        "QUANTHUS_HISTORY_ROOT",
                        "QUANTHUS_RUNTIME_ROOT",
                    )
                ]
            )
        )
    ):
        raise ValueError("P10_CONTAINER_EXACT_PRIOR_ANCHOR_REQUIRED")
    # State/outbox must not mutate any mounted source, including ancestors.
    for key in ("QUANTHUS_BASE_ROOT", "QUANTHUS_HISTORY_ROOT", "QUANTHUS_WEEKLY_INPUT_ROOT"):
        source = roots[key]
        if state.is_relative_to(source) or source.is_relative_to(state):
            raise ValueError("P10_CONTAINER_STATE_SOURCE_OVERLAP")
        for writable in (
            "QUANTHUS_RUNTIME_ROOT",
            "QUANTHUS_WEEKLY_JOB_ROOT",
            "QUANTHUS_BACKUP_ROOT",
            "QUANTHUS_GDRIVE_CONFIG_ROOT",
        ):
            output = roots[writable]
            if output.is_relative_to(source) or source.is_relative_to(output):
                raise ValueError("P10_CONTAINER_WRITABLE_SOURCE_OVERLAP")
    p10_tmp.check_root(Path(value["compose_env"]["QUANTHUS_TMP_ROOT"]), protected_paths(value))
    return value


def docker_command(config: dict[str, Any], *arguments: str) -> list[str]:
    """Elevate only local Docker control; keep the host wrapper and TMP unprivileged."""
    access = config.get("docker_access", "direct")
    if access not in ("direct", "sudo-noninteractive"):
        raise ValueError("P10_CONTAINER_DOCKER_ACCESS_INVALID")
    if access == "direct":
        return ["docker", *arguments]
    # subprocess receives an already minimal environment. Preserve only Compose
    # interpolation, never ambient daemon/context, proxy or Python settings.
    preserved = sorted(
        set(config.get("compose_env", {}))
        | {
            "COMPOSE_DISABLE_ENV_FILE",
            "QUANTHUS_PIPELINE_IMAGE",
            "QUANTHUS_EVENT_ROOT",
            "QUANTHUS_MAIL_ROOT",
            "QUANTHUS_JOB_TMP",
        }
    )
    return [
        "sudo",
        "-n",
        "--preserve-env=" + ",".join(preserved),
        "--",
        "/usr/bin/docker",
        "--host=unix:///var/run/docker.sock",
        *arguments,
    ]


def protected_paths(config: dict[str, Any]) -> list[Path]:
    return [Path(config["state"])] + [
        Path(value)
        for key, value in config["compose_env"].items()
        if key.endswith(("_ROOT", "_CONFIG", "_SECRET", "_SELECTION"))
        and key != "QUANTHUS_TMP_ROOT"
    ]


def cleanup_container(config: dict[str, Any], mode: str, *, apply: bool = False) -> dict[str, str]:
    """Host preflight/launch only. All housekeeping runs behind the TMP-only mount."""
    p10_tmp.check_root(Path(config["compose_env"]["QUANTHUS_TMP_ROOT"]), protected_paths(config))
    prefix = docker_command(
        config, "compose", "--project-name", "quanthus-data", "--file", config["compose"]
    )
    env = {
        "PATH": os.defpath,
        **config["compose_env"],
        "QUANTHUS_PIPELINE_IMAGE": config["image"],
        # Unused by cleanup; satisfy interpolation without mounting any live outputs.
        "QUANTHUS_EVENT_ROOT": str(Path(config["state"]) / "events"),
        "QUANTHUS_MAIL_ROOT": str(Path(config["state"]) / "mail"),
        "QUANTHUS_JOB_TMP": config["compose_env"]["QUANTHUS_TMP_ROOT"],
        "COMPOSE_DISABLE_ENV_FILE": "1",
    }
    if mode == "start":
        command = [*prefix, "up", "--detach", "--no-deps", "cleanup"]
    elif mode == "stop":
        command = [*prefix, "stop", "--timeout", "30", "cleanup"]
    elif mode == "once":
        command = [
            *prefix,
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "cleanup",
            "once",
            "--root",
            "/work/tmp",
        ]
        if apply:
            command.append("--apply")
    else:
        raise ValueError("P10_CLEANER_MODE_INVALID")
    result = subprocess.run(command, env=env, check=False, timeout=600)
    if result.returncode:
        raise RuntimeError("P10_CLEANER_CONTAINER_COMMAND_FAILED")
    return {"status": "container_command_completed", "mode": mode}


def delivery_container(config: dict[str, Any], target: Path | None = None) -> dict[str, str]:
    """Retry backup/mail only, without entering a collector or writable data mount."""
    state = Path(config["state"])
    if target is not None and (
        target.parent.resolve() != state / "events" or not target.is_file() or target.is_symlink()
    ):
        raise ValueError("P10_MAIL_EVENT_DESTINATION_INVALID")
    run_id = "delivery-" + uuid.uuid4().hex
    env = {
        "PATH": os.defpath,
        **config["compose_env"],
        "QUANTHUS_PIPELINE_IMAGE": config["image"],
        "QUANTHUS_EVENT_ROOT": str(state / "events"),
        "QUANTHUS_MAIL_ROOT": str(state / "mail"),
        "QUANTHUS_JOB_TMP": config["compose_env"]["QUANTHUS_TMP_ROOT"],
        "COMPOSE_DISABLE_ENV_FILE": "1",
    }
    command = [
        *docker_command(config),
        "compose",
        "--project-name",
        "quanthus-data",
        "--file",
        config["compose"],
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "--name",
        run_id,
    ]
    if target is None:
        command += [
            "--entrypoint",
            "python",
            "backup",
            "-m",
            "quanthus.p10_delivery",
            "--routing",
            "/run/config/backup.json",
            "--runtime",
            config["compose_env"]["QUANTHUS_RUNTIME_ROOT"],
        ]
    else:
        command += [
            "--entrypoint",
            "python",
            "backup",
            "-m",
            "quanthus.p10_operational_mail",
            "--event",
            "/run/events/" + target.name,
        ]

    def stop(signum: int, frame: Any) -> None:
        raise InterruptedError("P10_DELIVERY_STOP_REQUESTED")

    handlers = {s: signal.signal(s, stop) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        result = subprocess.run(command, env=env, check=False, timeout=config["timeout_seconds"])
    except (OSError, subprocess.TimeoutExpired, InterruptedError):
        # A terminated Compose CLI does not terminate its daemon-owned container.
        # Keep its source mounts read-only and stop only this invocation's name.
        for s in handlers:
            signal.signal(s, signal.SIG_IGN)
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            subprocess.run(
                docker_command(config, "stop", "--time", "30", run_id),
                env=env,
                check=False,
                capture_output=True,
                timeout=40,
            )
        raise RuntimeError("P10_DELIVERY_CONTAINER_FAILED") from None
    finally:
        for s, handler in handlers.items():
            signal.signal(s, handler)
    if result.returncode:
        raise RuntimeError("P10_DELIVERY_CONTAINER_FAILED")
    return {"status": "container_command_completed", "role": "backup" if target is None else "mail"}


def run(config: dict[str, Any], role: str, day: date) -> dict[str, Any]:
    prefix = docker_command(
        config, "compose", "--project-name", "quanthus-data", "--file", config["compose"]
    )
    if role not in {"daily", "recovery", "weekly"}:
        raise ValueError("P10_CONTAINER_ROLE_INVALID")
    tmp_root = Path(config["compose_env"]["QUANTHUS_TMP_ROOT"])
    protected = protected_paths(config)
    p10_tmp.check_root(tmp_root, protected)
    state = Path(config["state"])
    for name in ("events", "logs", "mail"):
        path = state / name
        if path.resolve() != path:
            raise ValueError("P10_CONTAINER_STATE_LINK_FORBIDDEN")
    for name in ("events", "logs", "mail"):
        (state / name).mkdir(exist_ok=True)
    run_id = f"{role}-{day}-{uuid.uuid4().hex}"
    target = state / "events" / (run_id + ".json")
    env = {
        "PATH": os.defpath,
        "HOME": os.environ.get("HOME", "/srv/agentdock"),
        **config["compose_env"],
        "QUANTHUS_PIPELINE_IMAGE": config["image"],
        "QUANTHUS_EVENT_ROOT": str(state / "events"),
        "QUANTHUS_MAIL_ROOT": str(state / "mail"),
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
    with contextlib.ExitStack() as stack:
        lock = stack.enter_context((state / (lock_name + ".lock")).open("a"))
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "busy", "role": role}
        scratch = stack.enter_context(p10_tmp.job(tmp_root, run_id, protected))
        env["QUANTHUS_JOB_TMP"] = str(scratch.path)
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
                    docker_command(config, "stop", "--time", "30", run_id),
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
        if not target.exists():
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
        # The job sends its own API notice. A daemon/host failure retains an unsent
        # safe event for same-image mail retry; the host never reads the secret.
        result = {
            "status": value["status"],
            "event": str(target),
            "mail": "container_owned_see_receipt" if not failed else "event_retained_unsent",
        }
        if failed or interrupted or value["status"] == "failed":
            raise RuntimeError("P10_CONTAINER_JOB_FAILED_SEE_RETAINED_EVENT")
        scratch.status = "succeeded" if value["status"] in {"complete", "closed"} else "held"
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "role",
        choices=[
            "daily",
            "recovery",
            "weekly",
            "mail",
            "backup",
            "clean-tmp",
            "cleanup-start",
            "cleanup-stop",
        ],
    )
    parser.add_argument("--deployment", type=Path, required=True)
    parser.add_argument("--day", type=date.fromisoformat)
    parser.add_argument("--event", type=Path)
    parser.add_argument(
        "--apply", action="store_true", help="Delete only eligible owned TMP; default is dry-run"
    )
    args = parser.parse_args()
    config = settings(args.deployment)
    if args.apply and args.role != "clean-tmp":
        parser.error("--apply is only valid with clean-tmp")
    if args.role == "clean-tmp":
        result: Any = cleanup_container(config, "once", apply=args.apply)
    elif args.role in {"cleanup-start", "cleanup-stop"}:
        result = cleanup_container(config, args.role.removeprefix("cleanup-"))
    elif args.role == "mail":
        if args.event is None:
            raise ValueError("P10_MAIL_EVENT_DESTINATION_INVALID")
        result = delivery_container(config, args.event)
    elif args.role == "backup":
        result = delivery_container(config)
    else:
        day = args.day or datetime.now(ZoneInfo("Asia/Shanghai")).date()
        if day > datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Shanghai")).date():
            raise ValueError("P10_CONTAINER_FUTURE_DATE")
        result = run(config, args.role, day)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()

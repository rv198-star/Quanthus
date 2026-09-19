#!/usr/bin/env python3
"""Restricted Quanthus production operations for OCI DevSpace MCP.

This daemon is intentionally not a shell wrapper.  It exposes five fixed JSON
operations over a group-restricted Unix socket and invokes subprocesses only
with constant executable paths and validated argv lists.
"""

from __future__ import annotations

import dataclasses
import grp
import hashlib
import json
import os
import re
import shlex
import socketserver
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPOSITORY = Path("/home/ubuntu/services/quanthus-platform")
ENV_FILE = Path("/home/ubuntu/.config/quanthus/platform.env")
COMPOSE_FILE = REPOSITORY / "ops/platform/compose.yaml"
COMPOSE_OVERRIDE = Path("/etc/quanthus-mcp-production/compose.override.yaml")
SOCKET_PATH = Path("/run/quanthus-mcp-production/control.sock")
AUDIT_PATH = Path("/var/log/quanthus-mcp-production/audit.jsonl")
OFFICIAL_ORIGIN = "https://github.com/rv198-star/Quanthus.git"
ALLOWED_COMPONENTS = {"api", "web", "replay-worker", "event-worker"}
SERVICE_CONTAINERS = {
    "postgres": "quanthus-platform-postgres-1",
    "api": "quanthus-platform-api-1",
    "web": "quanthus-platform-web-1",
    "replay-worker": "quanthus-platform-replay-worker-1",
    "event-worker": "quanthus-platform-event-worker-1",
}
ALLOWED_JOB_KINDS = {
    "quanthus.candidate-replay.v1": "replay",
    "quanthus.p4-event-scan.v1": "event",
    "quanthus.structure-mvp.v1": "event",
}
MCP_ACTOR_USER_ID = "usr_18c14af81a06cf878f177579efec3727"
DWD_READ_ONLY_ROOTS = {
    "/srv/quanthus/dwd-dws-v2.1/base/dwd-base-202607-v2.1",
    "/srv/quanthus/dwd-dws-v2.1/corrections/dwd-base-202607-v2.1-correction-20260813-001",
    "/srv/quanthus/research-bundles/development/strategy-replay-development-1f1ea4d-v1",
    "/home/ubuntu/data/quanthus/dwd-daily",
}
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
JOB_RE = re.compile(r"^job_[0-9a-f]{32}$")
IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")
CALLER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")
MAX_REQUEST_BYTES = 128 * 1024

STAGE_WORKER_REPOSITORY = Path("/home/qiuyu/quanthus-code/Quanthus")
STAGE_WORKER_TRAINING_ROOT = Path("/home/qiuyu/quanthus-data/training").resolve()
STAGE_WORKER_JOB_PARENT = (STAGE_WORKER_TRAINING_ROOT / "runs/stage-worker").resolve()
STAGE_WORKER_SSH = [
    "/usr/bin/ssh",
    "-F", "/dev/null",
    "-o", "HostName=100.89.43.38",
    "-o", "User=qiuyu",
    "-o", "Port=22",
    "-o", "IdentityFile=/home/ubuntu/.ssh/quanthus-oci-rx6800-training",
    "-o", "IdentitiesOnly=yes",
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=yes",
    "-o", "UserKnownHostsFile=/home/ubuntu/.ssh/known_hosts",
    "-o", "ConnectTimeout=15",
    "-o", "ServerAliveInterval=10",
    "-o", "ServerAliveCountMax=2",
    "-o", "RequestTTY=no",
    "-o", "LogLevel=ERROR",
    "quanthus-stage-rx6800",
]
STAGE_JOB_RE = re.compile(r"^stage_[0-9a-f]{32}$")
STAGE_ARTIFACT_TYPES = {
    "workflow_summary",
    "learning_summary",
    "r0",
    "r1_trials",
    "synthetic_review_summary",
    "reference_summary",
}
MAX_STAGE_STATUS_BYTES = 256 * 1024
MAX_STAGE_ARTIFACT_BYTES = 512 * 1024
MAX_STAGE_RESPONSE_BYTES = 768 * 1024

STAGE_WORKER_OFFICIAL_ORIGIN = "git@github.com:rv198-star/Quanthus.git"
STAGE_WORKER_GITHUB_KEY = Path("/home/qiuyu/.ssh/quanthus-github-ro")
STAGE_WORKER_GITHUB_KNOWN_HOSTS = Path("/home/qiuyu/.ssh/known_hosts")
STAGE_WORKER_UNIT = "quanthus-stage-worker.service"
STAGE_WORKER_RUNTIME_PATH = (
    STAGE_WORKER_TRAINING_ROOT / "control/stage-worker/worker-runtime.json"
)
STAGE_QUALIFICATION_KIND = "quanthus.stage-qualification.v1"


class ProductionError(RuntimeError):
    pass


def run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 120,
    check: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {
        "HOME": "/home/ubuntu",
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "GIT_TERMINAL_PROMPT": "0",
    }
    try:
        result = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            input=input_text,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProductionError(f"command_timed_out:{Path(argv[0]).name}") from exc
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ProductionError(f"command_failed:{Path(argv[0]).name}:{detail[:500]}")
    return result


def git(*args: str, timeout: int = 120) -> str:
    return run(["/usr/bin/git", "-C", str(REPOSITORY), *args], timeout=timeout).stdout.strip()


def compose(*args: str, timeout: int = 300) -> str:
    return run(
        [
            "/usr/bin/docker",
            "compose",
            "--env-file",
            str(ENV_FILE),
            "--file",
            str(COMPOSE_FILE),
            "--file",
            str(COMPOSE_OVERRIDE),
            *args,
        ],
        cwd=REPOSITORY,
        timeout=timeout,
    ).stdout.strip()


def docker(*args: str, timeout: int = 120, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["/usr/bin/docker", *args], timeout=timeout, check=check)


def checked_caller(payload: dict[str, Any]) -> str:
    caller = str(payload.get("caller_identity") or "devspace-oauth-single-user")
    if not CALLER_RE.fullmatch(caller):
        raise ProductionError("invalid_caller_identity")
    return caller


def audit(operation: str, caller: str, request: dict[str, Any], result: dict[str, Any]) -> None:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mcp_caller_identity": caller,
        "operation": operation,
        "requested_git_revision": request.get("git_ref") or request.get("code_revision"),
        "previous_production_revision": result.get("previous_revision"),
        "resulting_production_revision": result.get("deployed_revision"),
        "affected_services": result.get("affected_services", []),
        "image_digests": result.get("image_digests", {}),
        "job_id": result.get("job_id"),
        "artifact_type": request.get("artifact_type"),
        "run_id": result.get("run_id"),
        "success": bool(result.get("success")),
        "error_reason": result.get("error"),
    }
    AUDIT_PATH.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    with AUDIT_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    os.chmod(AUDIT_PATH, 0o640)


def http_json(url: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            body = response.read(1024 * 1024)
            return {
                "http_status": response.status,
                "body": json.loads(body.decode("utf-8")),
            }
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"http_status": None, "error": f"{type(exc).__name__}:{exc}"[:500]}


def service_names() -> set[str]:
    return set(compose("config", "--services").splitlines())


def inspect_service(service: str) -> dict[str, Any]:
    container = SERVICE_CONTAINERS[service]
    fmt = (
        "{{.Config.Image}}\t{{.Image}}\t{{.State.Status}}\t"
        "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}"
    )
    observed = docker("inspect", "--format", fmt, container, check=False)
    if observed.returncode != 0:
        return {"service": service, "container": container, "present": False}
    fields = observed.stdout.strip().split("\t")
    if len(fields) != 4:
        raise ProductionError(f"unexpected_docker_inspect:{service}")
    return {
        "service": service,
        "container": container,
        "present": True,
        "image": fields[0],
        "image_id": fields[1],
        "state": fields[2],
        "health": fields[3],
    }


def worker_mounts(service: str) -> list[dict[str, Any]]:
    container = SERVICE_CONTAINERS[service]
    observed = docker("inspect", "--format", "{{json .Mounts}}", container, check=False)
    if observed.returncode != 0:
        return []
    mounts = json.loads(observed.stdout)
    selected = []
    for mount in mounts:
        source = str(mount.get("Source", ""))
        destination = str(mount.get("Destination", ""))
        if (
            source.startswith("/srv/quanthus")
            or destination.startswith("/srv/quanthus")
            or source == "/home/ubuntu/data/quanthus/dwd-daily"
            or destination == "/data/dwd-daily"
        ):
            selected.append(
                {
                    "source": source,
                    "destination": destination,
                    "read_only": not bool(mount.get("RW")),
                    "mode": mount.get("Mode"),
                }
            )
    return selected


def worker_environment(service: str) -> dict[str, str]:
    container = SERVICE_CONTAINERS[service]
    observed = docker("inspect", "--format", "{{json .Config.Env}}", container, check=False)
    if observed.returncode != 0:
        return {}
    raw = json.loads(observed.stdout)
    values: dict[str, str] = {}
    for item in raw:
        if isinstance(item, str) and "=" in item:
            key, value = item.split("=", 1)
            values[key] = value
    return values


def replay_runtime_contract_compliant() -> bool:
    mounts = worker_mounts("replay-worker")
    observed_roots = {item["source"] for item in mounts if item["read_only"]}
    env = worker_environment("replay-worker")
    return (
        DWD_READ_ONLY_ROOTS.issubset(observed_roots)
        and all(item["read_only"] for item in mounts)
        and env.get("QUANTHUS_P6_CONTROLLER_ENABLED") == "1"
        and env.get("QUANTHUS_P6_FORWARD_RUN_ROOT") == "/data/candidate-runs/p6-forward"
        and env.get("QUANTHUS_P6_DWD_DAILY_ROOT") == "/data/dwd-daily"
        and env.get("QUANTHUS_P6_RUNTIME_CONFIG")
        == "/app/config/forward-validation/activation/p6-prospective-runtime.v1.json"
        and env.get("QUANTHUS_P6_ACTIVATION_MANDATE")
        == "/app/config/forward-validation/activation/p6-owner-activation-mandate.v1.json"
    )


BRIDGE_CODE = r'''
import dataclasses, json, os, sys
from pathlib import Path
from quanthus.platform import jobs as jobs_module
from quanthus.platform.jobs import DurableJobQueue, public_job
from quanthus.platform.persistence import PostgresPlatformStore

request = json.loads(sys.argv[1])
store = PostgresPlatformStore(os.environ["QUANTHUS_PLATFORM_DATABASE_URL"])
candidate = "quanthus.candidate-replay.v1"
event = "quanthus.p4-event-scan.v1"
structure = "quanthus.structure-mvp.v1"
p6_forward = "quanthus.p6-forward-cycle.v1"
admitted = {candidate: "replay"}
runtime_event = getattr(jobs_module, "P4_EVENT_SCAN_JOB", None) or getattr(jobs_module, "EVENT_SCAN_JOB", None)
runtime_structure = getattr(jobs_module, "STRUCTURE_MVP_JOB", None)
runtime_p6_forward = getattr(jobs_module, "P6_FORWARD_CYCLE_JOB", None)
if runtime_event == event:
    admitted[event] = "event"
if runtime_structure == structure:
    admitted[structure] = "event"
if runtime_p6_forward == p6_forward:
    admitted[p6_forward] = "replay"
queue = DurableJobQueue(store, admitted)
action = request["action"]

if action == "submit":
    kind = request["kind"]
    if kind not in admitted:
        raise ValueError("job kind is not admitted by deployed runtime")
    job, created = queue.submit(
        kind=kind,
        actor_user_id="usr_18c14af81a06cf878f177579efec3727",
        idempotency_key=request["idempotency_key"],
        input_ref=request["input_ref"],
        concurrency_key=request.get("concurrency_key"),
    )
    response = {"job": public_job(job), "created": created, "admitted_kinds": sorted(admitted)}
elif action == "status":
    job = queue.get(request["job_id"])
    if job is None or job.kind not in admitted:
        raise KeyError("job_not_found")
    response = {"job": public_job(job)}
    if request.get("include_run_manifest") and job.result_ref:
        artifact = job.result_ref.get("artifact_ref")
        if isinstance(artifact, str):
            path = Path(artifact).resolve()
            roots = [Path("/data/candidate-runs").resolve(), Path("/data/event-runs").resolve()]
            if any(path == root or root in path.parents for root in roots):
                candidates = [path / "manifest.json", path / "run-manifest.json", path / "result.json"]
                for candidate_path in candidates:
                    if candidate_path.is_file() and candidate_path.stat().st_size <= 1048576:
                        response["run_manifest"] = json.loads(candidate_path.read_text(encoding="utf-8"))
                        response["run_manifest_path"] = str(candidate_path)
                        break
elif action == "cancel":
    job = queue.get(request["job_id"])
    if job is None or job.kind not in admitted:
        raise KeyError("job_not_found")
    response = {"job": public_job(queue.cancel(job.job_id))}
elif action == "summary":
    jobs = [public_job(item) for item in store.list_jobs(None, int(request.get("job_limit", 20))) if item.kind in admitted]
    active = [dataclasses.asdict(item) for item in store.candidate_active_runs()]
    response = {"jobs": jobs, "candidate_active_runs": active, "admitted_kinds": sorted(admitted)}
else:
    raise ValueError("unsupported bridge action")

print(json.dumps(response, ensure_ascii=True, sort_keys=True, default=str))
'''


def bridge(request: dict[str, Any], *, timeout: int = 120) -> dict[str, Any]:
    api = SERVICE_CONTAINERS["api"]
    result = docker(
        "exec",
        api,
        "python",
        "-c",
        BRIDGE_CODE,
        json.dumps(request, sort_keys=True, separators=(",", ":")),
        timeout=timeout,
    )
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise ProductionError("invalid_worker_bridge_response") from exc



STAGE_RESULT_READER = r'''
import json
import re
import sys
from pathlib import Path

JOB_RE = re.compile(r"^stage_[0-9a-f]{32}$")
TRAINING_ROOT = Path("/home/qiuyu/quanthus-data/training").resolve()
JOB_PARENT = (TRAINING_ROOT / "runs/stage-worker").resolve()
MAX_FILE_BYTES = 512 * 1024
MAX_RESPONSE_BYTES = 768 * 1024
PATTERNS = {
    "workflow_summary": "synthetic-workflow/stage-synthetic-workflow-*/summary.json",
    "learning_summary": "synthetic-workflow/development-learning/stage-learning-*/summary.json",
    "r0": "synthetic-workflow/development-learning/stage-learning-*/r0.json",
    "r1_trials": "synthetic-workflow/development-learning/stage-learning-*/r1-trials.jsonl",
    "synthetic_review_summary": "synthetic-workflow/synthetic-review/stage-synthetic-review-*/summary.json",
    "reference_summary": "synthetic-workflow/synthetic-review/stage-synthetic-review-*/resolution/stage-reference-resolution-*/summary.json",
}

def fail(message):
    raise RuntimeError(message)

def require_under(path, root, label):
    try:
        path.relative_to(root)
    except ValueError:
        fail(label + "_outside_job_root")

def select_artifact(job_root, artifact_type):
    matches = []
    for candidate in job_root.glob(PATTERNS[artifact_type]):
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            fail("artifact_resolution_failed:" + type(exc).__name__)
        require_under(resolved, job_root, "artifact")
        if not resolved.is_file():
            fail("artifact_is_not_regular_file")
        matches.append(resolved)
    if len(matches) != 1:
        fail("artifact_selection_not_unique")
    return matches[0]

def read_json(path):
    if path.stat().st_size > MAX_FILE_BYTES:
        fail("artifact_file_too_large")
    try:
        parsed = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        fail("invalid_json_artifact:" + type(exc).__name__)
    if not isinstance(parsed, dict):
        fail("json_artifact_must_be_object")
    return parsed

def read_jsonl(path, eligible_only):
    if path.stat().st_size > MAX_FILE_BYTES:
        fail("artifact_file_too_large")
    try:
        lines = path.read_bytes().decode("utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        fail("invalid_jsonl_artifact:" + type(exc).__name__)
    records = []
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            fail("invalid_jsonl_artifact_line_" + str(index) + ":" + type(exc).__name__)
        if not isinstance(record, dict):
            fail("jsonl_record_must_be_object")
        records.append(record)
    if not eligible_only:
        return {"record_count": len(records), "records": records}
    trials = []
    for record in records:
        gate = record.get("development_gate")
        if not isinstance(gate, dict):
            fail("r1_trial_missing_development_gate")
        if gate.get("eligible") is True:
            required = ("trial_id", "parameters", "development_rank", "diagnostic_rank", "score")
            if any(field not in record for field in required):
                fail("eligible_r1_trial_missing_required_field")
            if not isinstance(record["trial_id"], str) or not isinstance(record["parameters"], dict):
                fail("eligible_r1_trial_invalid_required_field")
            trials.append({
                "trial_id": record["trial_id"],
                "parameters": record["parameters"],
                "development_gate": {"eligible": True},
                "development_rank": record["development_rank"],
                "diagnostic_rank": record["diagnostic_rank"],
                "score": record["score"],
            })
    return {
        "record_count": len(records),
        "eligible_count": len(trials),
        "trials": trials,
    }

def main():
    if len(sys.argv) != 4:
        fail("invalid_reader_arguments")
    job_id, artifact_type, eligible_value = sys.argv[1:]
    if not JOB_RE.fullmatch(job_id) or artifact_type not in PATTERNS or eligible_value not in {"0", "1"}:
        fail("invalid_reader_request")
    try:
        job_root = (JOB_PARENT / job_id).resolve(strict=True)
    except OSError as exc:
        fail("job_artifact_root_unavailable:" + type(exc).__name__)
    if not job_root.is_dir() or job_root.parent != JOB_PARENT:
        fail("invalid_job_artifact_root")
    artifact = select_artifact(job_root, artifact_type)
    relative_path = str(artifact.relative_to(job_root))
    if artifact_type == "r1_trials":
        payload = read_jsonl(artifact, eligible_value == "1")
    else:
        payload = read_json(artifact)
    response = {
        "job_id": job_id,
        "artifact_type": artifact_type,
        "eligible_only": eligible_value == "1" if artifact_type == "r1_trials" else None,
        "relative_path": relative_path,
        "result": payload,
    }
    try:
        encoded = json.dumps(response, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        fail("non_json_result:" + type(exc).__name__)
    if len(encoded) > MAX_RESPONSE_BYTES:
        fail("response_too_large")
    sys.stdout.buffer.write(encoded)

main()
'''


def checked_stage_job_id(payload: dict[str, Any]) -> str:
    job_id = str(payload.get("job_id") or "")
    if not STAGE_JOB_RE.fullmatch(job_id):
        raise ProductionError("invalid_stage_job_id")
    return job_id


def stage_worker_ssh(
    command: str,
    *,
    timeout: int,
    input_text: str | None = None,
    retry_on_timeout: bool = True,
) -> str:
    last_timeout: ProductionError | None = None
    attempts = 2 if retry_on_timeout else 1
    for _attempt in range(attempts):
        try:
            result = run([*STAGE_WORKER_SSH, command], timeout=timeout, input_text=input_text)
        except ProductionError as exc:
            if str(exc) != "command_timed_out:ssh":
                raise
            last_timeout = exc
            continue
        output = result.stdout
        if len(output.encode("utf-8")) > MAX_STAGE_RESPONSE_BYTES:
            raise ProductionError("stage_worker_response_too_large")
        return output
    raise ProductionError("stage_worker_ssh_timeout") from last_timeout


def stage_worker_status(job_id: str) -> dict[str, Any]:
    command = (
        "cd " + shlex.quote(str(STAGE_WORKER_REPOSITORY))
        + " && exec /usr/bin/bash ops/bin/quanthus-stage-worker status --job-id "
        + shlex.quote(job_id)
    )
    raw = stage_worker_ssh(command, timeout=45)
    if len(raw.encode("utf-8")) > MAX_STAGE_STATUS_BYTES:
        raise ProductionError("stage_worker_status_too_large")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProductionError("invalid_stage_worker_status_json") from exc
    if not isinstance(parsed, dict) or parsed.get("job_id") != job_id:
        raise ProductionError("stage_worker_status_identity_mismatch")
    return parsed


def operation_stage_worker_status(payload: dict[str, Any]) -> dict[str, Any]:
    job_id = checked_stage_job_id(payload)
    return {"success": True, **stage_worker_status(job_id)}


def operation_stage_worker_result(payload: dict[str, Any]) -> dict[str, Any]:
    job_id = checked_stage_job_id(payload)
    artifact_type = str(payload.get("artifact_type") or "")
    if artifact_type not in STAGE_ARTIFACT_TYPES:
        raise ProductionError("invalid_stage_artifact_type")
    eligible_only = payload.get("eligible_only", False)
    if not isinstance(eligible_only, bool):
        raise ProductionError("eligible_only_must_be_boolean")
    command = " ".join(
        shlex.quote(part)
        for part in (
            "/usr/bin/python3",
            "-",
            job_id,
            artifact_type,
            "1" if eligible_only else "0",
        )
    )
    raw = stage_worker_ssh(command, timeout=45, input_text=STAGE_RESULT_READER)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProductionError("invalid_stage_worker_result_json") from exc
    if (
        not isinstance(parsed, dict)
        or parsed.get("job_id") != job_id
        or parsed.get("artifact_type") != artifact_type
    ):
        raise ProductionError("stage_worker_result_identity_mismatch")
    return {"success": True, **parsed}



STAGE_DEPLOY_EXECUTOR = r'''
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY = Path("/home/qiuyu/quanthus-code/Quanthus")
TRAINING_ROOT = Path("/home/qiuyu/quanthus-data/training").resolve()
RUNTIME_PATH = TRAINING_ROOT / "control/stage-worker/worker-runtime.json"
ORIGIN = "git@github.com:rv198-star/Quanthus.git"
GIT_SSH_COMMAND = (
    "ssh -i /home/qiuyu/.ssh/quanthus-github-ro "
    "-o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes "
    "-o UserKnownHostsFile=/home/qiuyu/.ssh/known_hosts"
)
UNIT = "quanthus-stage-worker.service"
KIND = "quanthus.stage-qualification.v1"

def emit(value):
    print(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")))

def stop(code, **details):
    emit({"success": False, "error": code, **details})
    raise SystemExit(0)

def run(argv, *, cwd=None, git=False, timeout=180):
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    if git:
        env["GIT_SSH_COMMAND"] = GIT_SSH_COMMAND
    try:
        result = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        stop("remote_command_timeout")
    if result.returncode != 0:
        stop("remote_command_failed:" + Path(argv[0]).name, exit_code=result.returncode)
    return result.stdout.strip()

def git(*args, timeout=180):
    return run(["/usr/bin/git", "-C", str(REPOSITORY), *args], git=True, timeout=timeout)

def parse_json_file(path, label):
    try:
        if not path.is_file() or path.stat().st_size > 256 * 1024:
            stop(label + "_unavailable")
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        stop("invalid_" + label)
    if not isinstance(parsed, dict):
        stop("invalid_" + label)
    return parsed

def current_service_pid():
    try:
        result = subprocess.run(
            ["/usr/bin/systemctl", "--user", "show", UNIT, "-p", "MainPID", "--value"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0
    if result.returncode != 0:
        return 0
    try:
        pid = int(result.stdout.strip())
    except ValueError:
        return 0
    return pid if pid > 0 else 0

def runtime_candidate():
    try:
        if not RUNTIME_PATH.is_file() or RUNTIME_PATH.is_symlink() or RUNTIME_PATH.stat().st_size > 256 * 1024:
            return None
        value = json.loads(RUNTIME_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None

def wait_runtime_identity(target, *, require_kind, report_failure):
    last_runtime = None
    last_pid = 0
    for _ in range(20):
        service_pid = current_service_pid()
        runtime = runtime_candidate()
        last_pid = service_pid
        if runtime is not None:
            last_runtime = runtime
            runtime_pid = runtime.get("pid")
            kinds = runtime.get("job_kinds")
            if (
                service_pid > 0
                and type(runtime_pid) is int
                and runtime_pid == service_pid
                and runtime.get("code_revision") == target
                and (not require_kind or (isinstance(kinds, list) and KIND in kinds))
            ):
                return runtime
        time.sleep(1)
    if report_failure:
        stop(
            "worker_runtime_revision_mismatch",
            expected_revision=target,
            observed_revision=None if last_runtime is None else last_runtime.get("code_revision"),
            service_pid=last_pid,
            runtime_pid=None if last_runtime is None else last_runtime.get("pid"),
        )
    return None

def verify_runtime_and_doctor(target):
    runtime = wait_runtime_identity(target, require_kind=True, report_failure=True)
    doctor_raw = run(
        ["/usr/bin/bash", "ops/bin/quanthus-stage-worker", "doctor"],
        cwd=REPOSITORY,
        timeout=90,
    )
    try:
        doctor = json.loads(doctor_raw)
    except json.JSONDecodeError:
        stop("invalid_worker_doctor_json")
    if not isinstance(doctor, dict) or doctor.get("ready") is not True:
        stop("worker_doctor_not_ready")
    admitted = doctor.get("job_kinds")
    if not isinstance(admitted, list) or KIND not in admitted:
        stop("worker_doctor_qualification_kind_missing")
    return runtime, doctor

def main():
    if len(sys.argv) != 4:
        stop("invalid_deploy_arguments")
    target, expected, dry_value = sys.argv[1:]
    if not SHA_RE.fullmatch(target) or not SHA_RE.fullmatch(expected) or dry_value not in {"0", "1"}:
        stop("invalid_deploy_request")
    if not REPOSITORY.is_dir():
        stop("control_repository_missing")
    if git("remote", "get-url", "origin") != ORIGIN:
        stop("control_origin_not_official")
    if git("status", "--porcelain"):
        stop("control_checkout_is_dirty")
    previous = git("rev-parse", "HEAD")
    if previous != expected:
        stop("expected_previous_revision_mismatch", current_revision=previous)
    remote_line = git("ls-remote", "--exit-code", "origin", "refs/heads/main", timeout=90)
    remote_main = remote_line.split()[0] if remote_line.split() else ""
    if remote_main != target:
        stop("remote_origin_main_mismatch", remote_main=remote_main)
    if dry_value == "1":
        emit({
            "success": True,
            "dry_run": True,
            "previous_revision": previous,
            "deployed_revision": previous,
            "target_revision": target,
            "remote_main": remote_main,
            "unit": UNIT,
            "admitted_kind_required": KIND,
        })
        return
    git("fetch", "--no-tags", "origin", "refs/heads/main:refs/remotes/origin/main", timeout=300)
    if git("rev-parse", "origin/main") != target:
        stop("fetched_origin_main_mismatch")
    git("cat-file", "-e", target + "^{commit}")
    ancestor = subprocess.run(
        ["/usr/bin/git", "-C", str(REPOSITORY), "merge-base", "--is-ancestor", previous, target],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
    )
    if ancestor.returncode != 0:
        stop("target_is_not_fast_forward_from_current")
    changed = False
    try:
        git("checkout", "--detach", target, timeout=120)
        changed = True
        if git("rev-parse", "HEAD") != target or git("status", "--porcelain"):
            stop("post_checkout_verification_failed")
        run(["/usr/bin/systemctl", "--user", "restart", UNIT], timeout=120)
        active = run(["/usr/bin/systemctl", "--user", "is-active", UNIT], timeout=30)
        if active != "active":
            stop("stage_worker_service_not_active")
        runtime, doctor = verify_runtime_and_doctor(target)
        emit({
            "success": True,
            "dry_run": False,
            "previous_revision": previous,
            "deployed_revision": target,
            "unit": UNIT,
            "service_active": True,
            "runtime_code_revision": runtime["code_revision"],
            "doctor_ready": doctor["ready"],
            "admitted_job_kinds": doctor["job_kinds"],
        })
    except SystemExit:
        if changed:
            try:
                rollback_checkout = subprocess.run(
                    ["/usr/bin/git", "-C", str(REPOSITORY), "checkout", "--detach", previous],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=120,
                    check=False,
                )
                if rollback_checkout.returncode == 0:
                    rollback_restart = subprocess.run(
                        ["/usr/bin/systemctl", "--user", "restart", UNIT],
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=120,
                        check=False,
                    )
                    if rollback_restart.returncode == 0:
                        wait_runtime_identity(previous, require_kind=False, report_failure=False)
            except (OSError, subprocess.TimeoutExpired):
                pass
        raise

main()
'''


STAGE_SUBMIT_EXECUTOR = r'''
import json
import os
import re
import subprocess
import sys
from pathlib import Path

JOB_RE = re.compile(r"^stage_[0-9a-f]{32}$")
REPOSITORY = Path("/home/qiuyu/quanthus-code/Quanthus")
TRAINING_ROOT = Path("/home/qiuyu/quanthus-data/training").resolve()
RUNTIME_PATH = TRAINING_ROOT / "control/stage-worker/worker-runtime.json"
KIND = "quanthus.stage-qualification.v1"

def emit(value):
    print(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")))

def stop(code):
    emit({"success": False, "error": code})
    raise SystemExit(0)

def run(argv, *, cwd=None, timeout=180):
    try:
        result = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            env=os.environ.copy(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        stop("remote_command_timeout")
    if result.returncode != 0:
        stop("remote_command_failed:" + Path(argv[0]).name)
    return result.stdout.strip()

def parse_json(raw, label):
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        stop("invalid_" + label + "_json")
    if not isinstance(value, dict):
        stop("invalid_" + label + "_json")
    return value

def main():
    if len(sys.argv) != 1:
        stop("invalid_submit_arguments")
    try:
        if not RUNTIME_PATH.is_file() or RUNTIME_PATH.stat().st_size > 256 * 1024:
            stop("worker_runtime_unavailable")
        runtime = parse_json(RUNTIME_PATH.read_text(encoding="utf-8"), "worker_runtime")
    except (OSError, UnicodeDecodeError):
        stop("worker_runtime_unavailable")
    revision = runtime.get("code_revision")
    kinds = runtime.get("job_kinds")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        stop("worker_runtime_invalid_revision")
    if not isinstance(kinds, list) or KIND not in kinds:
        stop("worker_runtime_qualification_kind_missing")
    doctor = parse_json(
        run(["/usr/bin/bash", "ops/bin/quanthus-stage-worker", "doctor"], cwd=REPOSITORY, timeout=90),
        "worker_doctor",
    )
    if doctor.get("ready") is not True or KIND not in doctor.get("job_kinds", []):
        stop("worker_not_ready_for_qualification")
    submitted = parse_json(
        run(
            [
                "/usr/bin/bash",
                "ops/bin/quanthus-stage-worker",
                "submit",
                "--kind",
                KIND,
                "--code-revision",
                revision,
            ],
            cwd=REPOSITORY,
            timeout=120,
        ),
        "worker_submit",
    )
    job_id = submitted.get("job_id")
    if not isinstance(job_id, str) or not JOB_RE.fullmatch(job_id):
        stop("invalid_submitted_job_id")
    emit({
        "success": True,
        "job_id": job_id,
        "kind": KIND,
        "code_revision": revision,
        "job": submitted,
    })

main()
'''


def stage_worker_json_executor(
    script: str,
    args: tuple[str, ...],
    *,
    timeout: int,
    retry_on_timeout: bool,
) -> dict[str, Any]:
    command = " ".join(
        shlex.quote(part)
        for part in ("/usr/bin/python3", "-", *args)
    )
    raw = stage_worker_ssh(
        command,
        timeout=timeout,
        input_text=script,
        retry_on_timeout=retry_on_timeout,
    )
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProductionError("invalid_stage_worker_executor_json") from exc
    if not isinstance(parsed, dict):
        raise ProductionError("invalid_stage_worker_executor_response")
    if parsed.get("success") is not True:
        error = str(parsed.get("error") or "stage_worker_executor_failed")
        raise ProductionError(error[:240])
    return parsed


def operation_stage_worker_deploy(payload: dict[str, Any]) -> dict[str, Any]:
    revision = validate_revision(payload.get("git_ref"), "git_ref")
    expected = validate_revision(payload.get("expected_previous_revision"), "expected_previous_revision")
    dry_run = payload.get("dry_run", False)
    if not isinstance(dry_run, bool):
        raise ProductionError("dry_run_must_be_boolean")
    return stage_worker_json_executor(
        STAGE_DEPLOY_EXECUTOR,
        (revision, expected, "1" if dry_run else "0"),
        timeout=360,
        retry_on_timeout=False,
    )


def operation_stage_worker_submit(payload: dict[str, Any]) -> dict[str, Any]:
    return stage_worker_json_executor(
        STAGE_SUBMIT_EXECUTOR,
        (),
        timeout=180,
        retry_on_timeout=False,
    )


def operation_status(payload: dict[str, Any]) -> dict[str, Any]:
    limit = int(payload.get("job_limit", 20))
    if not 1 <= limit <= 100:
        raise ProductionError("job_limit_out_of_range")
    names = service_names()
    services = {
        name: inspect_service(name)
        for name in SERVICE_CONTAINERS
        if name == "postgres" or name in names
    }
    workers = {}
    for name in ("replay-worker", "event-worker"):
        if name not in names:
            continue
        mounts = worker_mounts(name)
        observed_roots = {item["source"] for item in mounts if item["read_only"]}
        mount_policy_compliant = DWD_READ_ONLY_ROOTS.issubset(observed_roots) and all(
            item["read_only"] for item in mounts
        )
        workers[name] = {
            "status": services.get(name),
            "dwd_mounts": mounts,
            "dwd_mount_policy_compliant": mount_policy_compliant,
        }
        if name == "replay-worker":
            env = worker_environment(name)
            workers[name]["p6_controller_enabled"] = env.get("QUANTHUS_P6_CONTROLLER_ENABLED")
            workers[name]["p6_daily_root"] = env.get("QUANTHUS_P6_DWD_DAILY_ROOT")
            workers[name]["p6_runtime_contract_compliant"] = replay_runtime_contract_compliant()
    result = {
        "success": True,
        "production_revision": git("rev-parse", "HEAD"),
        "production_branch": git("branch", "--show-current"),
        "production_dirty": bool(git("status", "--porcelain")),
        "origin": git("remote", "get-url", "origin"),
        "services": services,
        "health": http_json("http://127.0.0.1:8080/health"),
        "ready": http_json("http://127.0.0.1:8080/ready"),
        "web_ready": http_json("http://127.0.0.1:3100/ready"),
        "workers": workers,
        "worker_runtime": bridge({"action": "summary", "job_limit": limit}),
    }
    return result


def validate_revision(value: Any, field: str) -> str:
    revision = str(value or "")
    if not SHA_RE.fullmatch(revision):
        raise ProductionError(f"{field}_must_be_exact_40_char_sha")
    return revision


def read_env() -> tuple[list[str], dict[str, str]]:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    values: dict[str, str] = {}
    for line in lines:
        if line and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return lines, values


def write_env_images(changes: dict[str, str]) -> None:
    lines, values = read_env()
    missing = set(changes).difference(values)
    if missing:
        raise ProductionError(f"missing_image_env_keys:{','.join(sorted(missing))}")
    output = []
    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            key = line.split("=", 1)[0]
            if key in changes:
                line = f"{key}={changes[key]}"
        output.append(line)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=ENV_FILE.parent, prefix="platform.env.", delete=False
    ) as handle:
        handle.write("\n".join(output) + "\n")
        temporary = Path(handle.name)
    os.chmod(temporary, 0o600)
    os.replace(temporary, ENV_FILE)


def image_digest(image: str) -> str:
    result = docker("image", "inspect", "--format", "{{.Id}}", image)
    return result.stdout.strip()


def deployment_plan(revision: str, components: list[str]) -> dict[str, tuple[str, str, str]]:
    short = revision[:7]
    plan: dict[str, tuple[str, str, str]] = {}
    if "api" in components:
        plan["api"] = (
            "ops/bin/build-platform-image",
            f"quanthus-platform-kernel:git-{short}",
            "QUANTHUS_PLATFORM_IMAGE",
        )
    if "web" in components:
        plan["web"] = (
            "ops/bin/build-platform-web-image",
            f"quanthus-platform-web:git-{short}",
            "QUANTHUS_PLATFORM_WEB_IMAGE",
        )
    if "replay-worker" in components:
        plan["replay-worker"] = (
            "ops/bin/build-replay-image",
            f"quanthus-replay:git-{short}",
            "QUANTHUS_REPLAY_IMAGE",
        )
    if "event-worker" in components:
        plan["event-worker"] = (
            "ops/bin/build-event-image",
            f"quanthus-event:git-{short}",
            "QUANTHUS_EVENT_IMAGE",
        )
    return plan


def operation_deploy(payload: dict[str, Any]) -> dict[str, Any]:
    revision = validate_revision(payload.get("git_ref"), "git_ref")
    raw_components = payload.get("components")
    if not isinstance(raw_components, list) or not raw_components:
        raise ProductionError("components_must_be_non_empty_array")
    components = list(dict.fromkeys(str(item) for item in raw_components))
    if set(components).difference(ALLOWED_COMPONENTS):
        raise ProductionError("component_not_allowed")
    previous = git("rev-parse", "HEAD")
    expected = payload.get("expected_previous_revision")
    if expected is not None and validate_revision(expected, "expected_previous_revision") != previous:
        raise ProductionError("expected_previous_revision_mismatch")
    if git("remote", "get-url", "origin") != OFFICIAL_ORIGIN:
        raise ProductionError("production_origin_not_official")
    if git("status", "--porcelain"):
        raise ProductionError("production_checkout_is_dirty")
    git("fetch", "--prune", "origin", timeout=300)
    run(["/usr/bin/git", "-C", str(REPOSITORY), "cat-file", "-e", f"{revision}^{{commit}}"])
    ancestor = run(
        ["/usr/bin/git", "-C", str(REPOSITORY), "merge-base", "--is-ancestor", revision, "origin/main"],
        check=False,
    )
    if ancestor.returncode != 0:
        raise ProductionError("revision_not_reachable_from_origin_main")
    names = service_names()
    if set(components).difference(names):
        raise ProductionError("requested_component_absent_from_compose")
    plan = deployment_plan(revision, components)
    for component, (script, _image, env_key) in plan.items():
        if not (REPOSITORY / script).is_file():
            raise ProductionError(f"build_script_missing:{component}")
        _lines, env_values = read_env()
        if env_key not in env_values:
            raise ProductionError(f"image_env_key_missing:{component}")
    planned_images_active = all(
        inspect_service(component).get("image") == image
        for component, (_script, image, _env_key) in plan.items()
    )
    runtime_contract_active = (
        replay_runtime_contract_compliant() if "replay-worker" in components else True
    )
    true_no_op = revision == previous and planned_images_active and runtime_contract_active
    if bool(payload.get("dry_run")) or true_no_op:
        status = operation_status({"job_limit": 5})
        return {
            "success": True,
            "dry_run": bool(payload.get("dry_run")),
            "no_op": true_no_op,
            "previous_revision": previous,
            "deployed_revision": previous,
            "affected_services": components,
            "planned_images": {c: item[1] for c, item in plan.items()},
            "health": status["health"],
            "ready": status["ready"],
            "services": {c: status["services"].get(c) for c in components},
        }
    old_lines, old_values = read_env()
    old_images = {item[2]: old_values[item[2]] for item in plan.values()}
    built: dict[str, str] = {}
    try:
        git("checkout", "--detach", revision)
        if git("status", "--porcelain"):
            raise ProductionError("checkout_became_dirty")
        for component, (script, image, _env_key) in plan.items():
            if revision == previous and inspect_service(component).get("image") == image:
                built[component] = image_digest(image)
                continue
            run([str(REPOSITORY / script), image], cwd=REPOSITORY, timeout=3600)
            built[component] = image_digest(image)
        write_env_images({item[2]: item[1] for item in plan.values()})
        compose("up", "-d", "--no-deps", *components, timeout=600)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            states = [inspect_service(component) for component in components]
            if all(item.get("state") == "running" and item.get("health") in {"healthy", "none"} for item in states):
                break
            time.sleep(3)
        else:
            raise ProductionError("deployed_services_not_healthy")
        health = http_json("http://127.0.0.1:8080/health")
        ready = http_json("http://127.0.0.1:8080/ready")
        if health.get("http_status") != 200 or ready.get("http_status") != 200:
            raise ProductionError("post_deploy_health_check_failed")
        return {
            "success": True,
            "previous_revision": previous,
            "deployed_revision": git("rev-parse", "HEAD"),
            "affected_services": components,
            "image_digests": built,
            "services": {c: inspect_service(c) for c in components},
            "health": health,
            "ready": ready,
        }
    except Exception:
        write_env_images(old_images)
        git("checkout", "--detach", previous)
        compose("up", "-d", "--no-deps", *components, timeout=600)
        raise


def operation_worker_submit(payload: dict[str, Any]) -> dict[str, Any]:
    kind = str(payload.get("kind") or "")
    if kind not in ALLOWED_JOB_KINDS:
        raise ProductionError("job_kind_not_allowed")
    if kind in {"quanthus.p4-event-scan.v1", "quanthus.structure-mvp.v1"} and "event-worker" not in service_names():
        raise ProductionError("event_worker_not_deployed")
    idempotency_key = str(payload.get("idempotency_key") or "")
    if not IDEMPOTENCY_RE.fullmatch(idempotency_key):
        raise ProductionError("invalid_idempotency_key")
    code_revision = validate_revision(payload.get("code_revision"), "code_revision")
    runtime_service = "event-worker" if kind in {"quanthus.p4-event-scan.v1", "quanthus.structure-mvp.v1"} else "replay-worker"
    runtime_image = str(inspect_service(runtime_service).get("image") or "")
    image_revision = re.search(r":git-([0-9a-f]{7,40})$", runtime_image)
    if image_revision is None:
        raise ProductionError("worker_image_has_no_git_revision_identity")
    try:
        runtime_revision = git("rev-parse", f"{image_revision.group(1)}^{{commit}}")
    except ProductionError as exc:
        raise ProductionError("worker_image_revision_not_resolvable") from exc
    if code_revision != runtime_revision:
        raise ProductionError("code_revision_not_deployed_worker_revision")
    input_ref = payload.get("input_ref")
    if not isinstance(input_ref, dict) or not input_ref or len(json.dumps(input_ref)) > 65536:
        raise ProductionError("invalid_input_ref")
    if input_ref.get("code_revision") not in {None, code_revision}:
        raise ProductionError("input_ref_code_revision_mismatch")
    governed_input = dict(input_ref)
    governed_input["code_revision"] = code_revision
    response = bridge(
        {
            "action": "submit",
            "kind": kind,
            "idempotency_key": idempotency_key,
            "input_ref": governed_input,
            "concurrency_key": payload.get("concurrency_key"),
        }
    )
    job = response["job"]
    return {
        "success": True,
        "created": response["created"],
        "job_id": job["job_id"],
        "run_id": (job.get("result_ref") or {}).get("run_id"),
        "job": job,
        "admitted_kinds": response["admitted_kinds"],
        "worker_image": runtime_image,
        "code_revision": runtime_revision,
    }


def operation_worker_status(payload: dict[str, Any]) -> dict[str, Any]:
    job_id = str(payload.get("job_id") or "")
    if not JOB_RE.fullmatch(job_id):
        raise ProductionError("invalid_job_id")
    response = bridge(
        {
            "action": "status",
            "job_id": job_id,
            "include_run_manifest": bool(payload.get("include_run_manifest")),
        }
    )
    job = response["job"]
    return {
        "success": True,
        "job_id": job_id,
        "run_id": (job.get("result_ref") or {}).get("run_id"),
        **response,
    }


def operation_worker_cancel(payload: dict[str, Any]) -> dict[str, Any]:
    job_id = str(payload.get("job_id") or "")
    if not JOB_RE.fullmatch(job_id):
        raise ProductionError("invalid_job_id")
    response = bridge({"action": "cancel", "job_id": job_id})
    job = response["job"]
    return {
        "success": True,
        "job_id": job_id,
        "run_id": (job.get("result_ref") or {}).get("run_id"),
        "job": job,
    }


OPERATIONS = {
    "production_status": operation_status,
    "production_deploy": operation_deploy,
    "production_worker_submit": operation_worker_submit,
    "production_worker_status": operation_worker_status,
    "production_worker_cancel": operation_worker_cancel,
    "stage_worker_status": operation_stage_worker_status,
    "stage_worker_result": operation_stage_worker_result,
    "stage_worker_deploy": operation_stage_worker_deploy,
    "stage_worker_submit": operation_stage_worker_submit,
}


def execute(request: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    operation = request.get("operation")
    payload = request.get("payload")
    if operation not in OPERATIONS or not isinstance(payload, dict):
        return 400, {"success": False, "error": "invalid_operation_or_payload"}
    caller = "invalid"
    try:
        caller = checked_caller(payload)
        result = OPERATIONS[operation](payload)
        audit(operation, caller, payload, result)
        return 200, result
    except Exception as exc:
        error = f"{type(exc).__name__}:{exc}"[:1000]
        result = {"success": False, "error": error}
        try:
            audit(operation, caller, payload, result)
        except Exception:
            pass
        return 400, result


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        line = self.rfile.readline(MAX_REQUEST_BYTES + 1)
        if not line or len(line) > MAX_REQUEST_BYTES:
            response = {"success": False, "error": "request_too_large_or_empty"}
        else:
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("request must be an object")
                _status, response = execute(request)
            except Exception as exc:
                response = {"success": False, "error": f"invalid_json:{exc}"[:500]}
        self.wfile.write(json.dumps(response, sort_keys=True, default=str).encode("utf-8") + b"\n")


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def main() -> None:
    SOCKET_PATH.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    if SOCKET_PATH.exists() or SOCKET_PATH.is_socket():
        SOCKET_PATH.unlink()
    with Server(str(SOCKET_PATH), Handler) as server:
        os.chown(SOCKET_PATH, -1, grp.getgrnam("devspace").gr_gid)
        os.chmod(SOCKET_PATH, 0o660)
        server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()

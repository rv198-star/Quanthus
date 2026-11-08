"""Pinned offline controller for the existing paired Base graph and publication.

Only task-generated files are protected or published. Worker transformations and
source audits run as the configured unprivileged user in systemd namespaces.
The root controller itself must run inside an offline network namespace.
"""

from __future__ import annotations

import fcntl
import json
import os
import pwd
import re
import socket
import stat
import subprocess
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base22_bounded import BoundedError, Limits
from .base22_sources import disjoint_output
from .base_build_inputs import _physical, file_sha, identity, member
from .base_build_period import load
from .base_build_publication import (
    begin_transfer,
    pair_documents,
    portable_audit_identity,
    prepare_markers,
    recover_owned_markers,
    resume_transfer,
    verify_pair_registration,
    verify_pair_seal,
    write_markers,
)
from .base_build_resources import job_capacity, remaining_stage_limits
from .base_build_stages import _reason, _save_new

EXECUTION = "p10-standard-base-execution-v1"
CONTROLLER = "p10-standard-base-controller-v1"
PAIR_READY = "p10-standard-base-pair-completion-v1"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_path(raw: Any, *, executable: bool = False) -> Path:
    # systemd path-list syntax and specifiers are deliberately not interpolated.
    if (
        not isinstance(raw, str)
        or not re.fullmatch(r"/[A-Za-z0-9_./+\-]+", raw)
        or os.path.abspath(raw) != raw
        or raw == "/"
    ):
        raise BoundedError("BUILD_EXECUTION_UNAMBIGUOUS_ABSOLUTE_PATH_REQUIRED")
    path = Path(raw)
    if executable:
        _physical(path.parent)
        if not path.is_file() or not os.access(path, os.X_OK):
            raise BoundedError("BUILD_EXECUTION_RUNTIME_PYTHON_NOT_EXECUTABLE")
        return path
    return _physical(path)


def validate_execution(value: dict[str, Any]) -> None:
    if (
        set(value)
        != {
            "contract",
            "runtime_user",
            "runtime_python",
            "audit_parent",
            "protected_paths",
            "audit_limits",
            "service_limits",
        }
        or value.get("contract") != EXECUTION
        or not isinstance(value.get("runtime_user"), str)
        or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", value["runtime_user"])
    ):
        raise BoundedError("BUILD_EXECUTION_SCHEMA_INVALID")
    safe_path(value["runtime_python"], executable=True)
    safe_path(value["audit_parent"])
    paths = value["protected_paths"]
    if (
        not isinstance(paths, list)
        or not paths
        or any(not isinstance(p, str) for p in paths)
        or len(set(paths)) != len(paths)
    ):
        raise BoundedError("BUILD_EXECUTION_PRODUCTION_CACHE_DENIAL_REQUIRED")
    for raw in paths:
        safe_path(raw)
    Limits(**value["audit_limits"])
    service = value["service_limits"]
    if (
        not isinstance(service, dict)
        or set(service) != {"memory_max_bytes", "cpu_quota_percent", "wall_seconds"}
        or any(type(v) is not int or v <= 0 for v in service.values())
    ):
        raise BoundedError("BUILD_EXECUTION_SERVICE_LIMITS_INVALID")


def _sync(path: Path) -> None:
    fd = os.open(path, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def control_record(path: Path, value: dict[str, Any]) -> None:
    """Recover only this reserved atomic control write; never replace final bytes."""
    path = _physical(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = member(path.parent, "." + path.name + ".partial")
    if partial.exists():
        if path.exists() and os.path.samefile(path, partial):
            if load(path) != value:
                raise BoundedError("BUILD_CONTROLLER_CONTROL_CONFLICT_PRESERVED")
            partial.unlink()
        elif path.exists():
            raise BoundedError("BUILD_CONTROLLER_CONTROL_CONFLICT_PRESERVED")
        else:
            # A partial write is not a receipt. Preserve it atomically and build
            # the authoritative value again from current validated state.
            archive = member(path.parent, "interrupted-controls")
            archive.mkdir(exist_ok=True)
            os.rename(partial, archive / (uuid.uuid4().hex + "-" + path.name))
            _sync(archive)
        _sync(path.parent)
    if path.exists():
        if load(path) != value:
            raise BoundedError("BUILD_CONTROLLER_CONTROL_CONFLICT_PRESERVED")
        return
    _save_new(path, value)


@contextmanager
def controller_lock(root: Path) -> Iterator[None]:
    root = _physical(root)
    root.mkdir(parents=True, exist_ok=True)
    fd = os.open(
        root / "CONTROLLER.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
    )
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise BoundedError("BUILD_CONTROLLER_LOCK_NOT_REGULAR")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BoundedError("BUILD_CONTROLLER_ALREADY_RUNNING") from exc
        yield
    finally:
        os.close(fd)


def _no_active_workers(controls: Path) -> None:
    for path in sorted(controls.glob("run-*/*/INVOCATION.json")):
        unit = load(member(path.parent, path.name))["unit"]
        if not re.fullmatch(r"quanthus-base-build-[0-9a-f]{32}-[a-z0-9-]+", unit):
            raise BoundedError("BUILD_CONTROLLER_UNKNOWN_UNIT_PRESERVED")
        result = subprocess.run(
            [
                "systemctl",
                "show",
                "--property=ActiveState",
                "--property=LoadState",
                unit,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        state = dict(
            line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
        )
        if state.get("ActiveState") in {
            "active",
            "activating",
            "reloading",
            "deactivating",
        }:
            raise BoundedError("BUILD_CONTROLLER_PREVIOUS_WORKER_STILL_RUNNING")
        if set(state) != {"ActiveState", "LoadState"} or state["ActiveState"] not in {
            "inactive",
            "failed",
        }:
            raise BoundedError("BUILD_CONTROLLER_PREVIOUS_WORKER_STATE_UNREADABLE")


def controller_capacity(
    root: Path, sources: list[Path], limits: Limits, days: int = 0
) -> dict[str, int]:
    """Host ownership preflight; kernel readonly admission belongs to each worker."""
    usage = job_capacity(root, sources, limits)
    ancestor = root
    while not ancestor.exists():
        ancestor = ancestor.parent
    fs = os.statvfs(ancestor)
    if (
        fs.f_bavail * fs.f_frsize
        < usage["remaining_allocated_bytes"] + limits.free_floor_bytes
    ):
        raise BoundedError("BUILD_WORK_SPACE_RESERVE_INSUFFICIENT")
    if fs.f_favail < 4000 + days * 500:
        raise BoundedError("BUILD_WORK_INODE_RESERVE_INSUFFICIENT")
    return usage


def protect_tree(root: Path, *, strict: bool) -> None:
    """Root-own generated data, without following even retained failure symlinks."""
    _physical(root)
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(directory) / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                if strict:
                    raise BoundedError("BUILD_CONTROLLER_PRODUCT_SYMLINK")
                if (info.st_uid, info.st_gid) != (0, 0):
                    os.chown(path, 0, 0, follow_symlinks=False)
                continue
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise BoundedError("BUILD_CONTROLLER_SPECIAL_FILE_PRESERVED")
            if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                if strict:
                    raise BoundedError("BUILD_CONTROLLER_PRODUCT_NOT_INDEPENDENT")
                # A rejected old attempt may contain a linked input. Protect its
                # directories but never chmod/chown the shared original inode.
                continue
            if (info.st_uid, info.st_gid) != (0, 0):
                os.chown(path, 0, 0, follow_symlinks=False)
            wanted = 0o555 if stat.S_ISDIR(info.st_mode) else 0o444
            if stat.S_IMODE(info.st_mode) != wanted:
                os.chmod(path, wanted)
        info = Path(directory).stat()
        if (info.st_uid, info.st_gid) != (0, 0):
            os.chown(directory, 0, 0)
        if stat.S_IMODE(info.st_mode) != 0o555:
            os.chmod(directory, 0o555)


def require_protected(root: Path) -> None:
    for directory, dirs, files in os.walk(_physical(root), followlinks=False):
        for path in [Path(directory), *(Path(directory) / n for n in [*dirs, *files])]:
            info = path.lstat()
            if (
                info.st_uid != 0
                or info.st_mode & 0o222
                or stat.S_ISLNK(info.st_mode)
                or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
                or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)
            ):
                raise BoundedError("BUILD_CONTROLLER_ROOT_READONLY_OWNERSHIP_REQUIRED")


def derive_denied(job: dict[str, Any], execution: dict[str, Any]) -> list[Path]:
    roots = load(safe_path(job["roots"]))
    paths = [
        *(safe_path(p) for p in roots.values()),
        safe_path(job["work_root"]) / "stages",
        *(safe_path(p) for p in execution["protected_paths"]),
    ]
    # In particular, the audit cannot read the old period/windows or the old
    # stage payloads after ownership transfer, including failed attempts.
    return sorted(set(paths), key=str)


def service_arguments(
    *,
    action: str,
    job: dict[str, Any],
    execution: dict[str, Any],
    repository: Path,
    job_path: Path,
    contract_path: Path,
    readonly: list[Path],
    denied: list[Path],
    writable: Path,
    unit: str,
) -> list[str]:
    runtime = safe_path(execution["runtime_python"], executable=True)
    service = execution["service_limits"]
    properties = [
        "User=" + execution["runtime_user"],
        "PrivateNetwork=yes",
        "PrivateTmp=yes",
        "PrivateDevices=yes",
        "ProtectSystem=strict",
        "ProtectHome=read-only",
        "NoNewPrivileges=yes",
        "CapabilityBoundingSet=",
        "RestrictAddressFamilies=AF_UNIX AF_NETLINK",
        "KillMode=control-group",
        "UMask=0022",
        "WorkingDirectory=" + str(repository),
        "MemoryMax=" + str(service["memory_max_bytes"]),
        "CPUQuota=" + str(service["cpu_quota_percent"]) + "%",
        "RuntimeMaxSec=" + str(service["wall_seconds"]),
        "ReadWritePaths=" + str(writable),
        # Explicitly recover the persistent host path beneath PrivateTmp.
        "BindPaths=" + str(writable) + ":" + str(writable),
        "BindReadOnlyPaths="
        + " ".join(
            str(p)
            for p in sorted(
                set(
                    [
                        repository,
                        runtime.parent.parent,
                        safe_path(job["environment_lock"]),
                        safe_path(job["wheelhouse"]),
                        job_path,
                        contract_path,
                        *readonly,
                    ]
                ),
                key=str,
            )
        ),
    ]
    # A frozen Base may live inside a production jobs parent. An inaccessible
    # parent also hides its explicitly bound child. Mask that parent with an
    # empty readonly filesystem instead; the existing readonly bind list then
    # exposes only the selected inputs. New production siblings stay hidden too.
    masks, inaccessible = [], []
    for path in denied:
        if any(path == source or source in path.parents for source in readonly):
            raise BoundedError("BUILD_EXECUTION_INPUT_DENIAL_CONFLICT")
        if any(path in source.parents for source in readonly):
            masks.append(path)
        else:
            inaccessible.append(path)
    masks = [p for p in masks if not any(q in p.parents for q in masks)]
    if masks:
        properties.append(
            "TemporaryFileSystem=" + " ".join(str(p) + ":ro" for p in masks)
        )
    inaccessible = [p for p in inaccessible if not any(q in p.parents for q in masks)]
    if inaccessible:
        properties.append(
            "InaccessiblePaths=" + " ".join("-" + str(p) for p in inaccessible)
        )
    arguments = [
        "systemd-run",
        "--quiet",
        "--wait",
        "--pipe",
        "--collect",
        "--unit=" + unit,
    ]
    for value in properties:
        arguments.extend(["-p", value])
    arguments.extend(
        [
            "-E",
            "PYTHONPATH=" + str(repository / "src") + ":" + str(repository),
            "-E",
            "PYTHONTZPATH=",
            "-E",
            "PYTHONDONTWRITEBYTECODE=1",
            str(runtime),
            "-m",
            "quanthus.pipelines.securities_warehouse.base_build",
            action,
            "--job",
            str(job_path),
            "--contract",
            str(contract_path),
        ]
    )
    return arguments


def run_service(
    *,
    action: str,
    job: dict[str, Any],
    contract: dict[str, Any],
    execution: dict[str, Any],
    repository: Path,
    parent: Path,
    readonly: list[Path],
    denied: list[Path],
    writable: Path,
    token: str,
) -> tuple[dict[str, Any], str]:
    parent.mkdir(parents=True, exist_ok=False)
    job_path, contract_path = parent / "job.json", parent / "contract.json"
    control_record(job_path, job)
    control_record(contract_path, contract)
    unit = "quanthus-base-build-" + token + "-" + action
    started = now()
    control_record(
        parent / "INVOCATION.json",
        {
            "unit": unit,
            "action": action,
            "job_identity": identity(job),
            "started_at": started,
            "work_root": job["work_root"],
        },
    )
    arguments = service_arguments(
        action=action,
        job=job,
        execution=execution,
        repository=repository,
        job_path=job_path,
        contract_path=contract_path,
        readonly=readonly,
        denied=denied,
        writable=writable,
        unit=unit,
    )
    clock = time.monotonic()
    with (parent / "worker.log").open("xb") as stream:
        result = subprocess.run(
            arguments, stdout=stream, stderr=subprocess.STDOUT, check=False
        )
    control_record(
        parent / "EXIT.json",
        {
            "unit": unit,
            "exit_code": result.returncode,
            "finished_at": now(),
            "elapsed_seconds": time.monotonic() - clock,
        },
    )
    if result.returncode:
        raise BoundedError("BUILD_CONTROLLER_WORKER_FAILED_PRESERVED")
    for line in reversed((parent / "worker.log").read_text().splitlines()):
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("stage") == action:
            return value, started
    raise BoundedError("BUILD_CONTROLLER_WORKER_COMPLETION_MISSING")


def audit_completion(
    job: dict[str, Any],
    contract: dict[str, Any],
    environment_identity: str,
    started_at: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    root = safe_path(job["work_root"])
    completion = load(member(root, "COMPLETION.json"))
    audit_path = member(root, "AUDIT.json")
    receipt = load(audit_path)
    try:
        start = datetime.fromisoformat(completion["started_at"])
        end = datetime.fromisoformat(completion["finished_at"])
        clocks = (
            datetime.fromisoformat(started_at)
            <= start
            <= end
            <= datetime.now(timezone.utc)
        )
    except (KeyError, TypeError, ValueError):
        clocks = False
    if (
        completion.get("contract") != "p10-standard-base-audit-execution-v1"
        or completion.get("identity")
        != identity({k: v for k, v in completion.items() if k != "identity"})
        or completion.get("job_identity") != identity(job)
        or completion.get("work_root") != job["work_root"]
        or completion.get("code_revision") != job["builder_revision"]
        or completion.get("materialization_revision", job["builder_revision"])
        != job.get("materialization_revision", job["builder_revision"])
        or completion.get("policy_identity") != identity(contract)
        or completion.get("environment_identity") != environment_identity
        or completion.get("audit_sha256") != file_sha(audit_path)
        or completion.get("audit_identity") != receipt.get("identity")
        or receipt.get("identity")
        != identity({k: v for k, v in receipt.items() if k != "identity"})
        or receipt.get("input_identity") != job["frozen_input_identity"]
        or completion.get("publication_eligible")
        is not receipt.get("publication_eligible")
        or not clocks
        or (root / "FAILURE.json").exists()
    ):
        raise BoundedError("BUILD_CONTROLLER_CURRENT_AUDIT_COMPLETION_REQUIRED")
    return receipt, completion


def _audit(
    *,
    job: dict[str, Any],
    contract: dict[str, Any],
    execution: dict[str, Any],
    repository: Path,
    products: dict[str, Path],
    attempt: Path,
    token: str,
    denied: list[Path],
    environment_identity: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    from .base_build import AUDIT_JOB

    parent = safe_path(execution["audit_parent"])
    limits = Limits(**execution["audit_limits"])
    sources = [*products.values(), safe_path(job["work_root"])]
    controller_capacity(parent, sources, limits)
    user = pwd.getpwnam(execution["runtime_user"])
    parent.mkdir(parents=True, exist_ok=True)
    # Prior audit replay/spill is retained failure evidence, never a readable
    # shortcut for this fresh source audit.
    denied = sorted(
        set([*denied, *(member(parent, p.name) for p in parent.iterdir())]), key=str
    )
    scratch = member(parent, "audit-" + token)
    scratch.mkdir()
    os.chown(scratch, user.pw_uid, user.pw_gid)
    audit_job = {
        "contract": AUDIT_JOB,
        "builder_revision": job["builder_revision"],
        **(
            {"materialization_revision": job["materialization_revision"]}
            if "materialization_revision" in job
            else {}
        ),
        "ods": str(products["ods"]),
        "dwd": str(products["dwd"]),
        "work_root": str(scratch / "output"),
        "frozen_input_identity": load(safe_path(job["frozen"]))["identity"],
        "environment_lock": job["environment_lock"],
        "wheelhouse": job["wheelhouse"],
        "denied_input_roots": [str(p) for p in denied],
        "limits": asdict(remaining_stage_limits(parent, sources, limits)),
    }
    result, started = run_service(
        action="audit",
        job=audit_job,
        contract=contract,
        execution=execution,
        repository=repository,
        parent=attempt,
        readonly=list(products.values()),
        denied=denied,
        writable=scratch,
        token=token,
    )
    receipt, completion = audit_completion(
        audit_job, contract, environment_identity, started
    )
    if result.get("execution_identity") != completion["identity"]:
        raise BoundedError("BUILD_CONTROLLER_CURRENT_AUDIT_COMPLETION_REQUIRED")
    protect_tree(scratch, strict=True)
    job_capacity(parent, sources, limits)
    return receipt, completion


def _pair_completion(
    work: Path,
    owner: str,
    products: dict[str, Path],
    receipt: dict[str, Any],
    completion: dict[str, Any],
) -> dict[str, Any]:
    gate = verify_pair_registration(products["ods"], products["dwd"])
    content = {
        "owner_identity": owner,
        "pair_registration_identity": gate["identity"],
        "audit_content_identity": portable_audit_identity(receipt),
        "manifest_hashes": {
            r: file_sha(p / "manifest.json") for r, p in products.items()
        },
        "materializations": gate["materializations"],
        "consumer_admission": "NOT_GRANTED",
    }
    path = work / "PAIR-READY.json"
    if path.exists():
        value = load(member(work, path.name))
        if (
            value.get("contract") != PAIR_READY
            or value.get("content") != content
            or value.get("identity")
            != identity({k: v for k, v in value.items() if k != "identity"})
        ):
            raise BoundedError("BUILD_CONTROLLER_PAIR_COMPLETION_CHANGED")
        control_record(path, value)
        os.chmod(path, 0o444)
        return value
    value = {
        "contract": PAIR_READY,
        "content": content,
        "sealed_audit_execution_identity": completion["identity"],
        "completed_at": now(),
        "full_A_B_C": False,
    }
    value["identity"] = identity(value)
    control_record(path, value)
    os.chown(path, 0, 0)
    os.chmod(path, 0o444)
    return value


def execute(
    *, job: dict[str, Any], contract: dict[str, Any], execution: dict[str, Any]
) -> dict[str, Any]:
    from .base_build import REPOSITORY, _require_pinned_code, validate_job
    from .base_build_environment import check_environment
    from .base_build_profile import validate_contract

    validate_job(job)
    validate_contract(contract)
    validate_execution(execution)
    if os.geteuid() != 0 or {n for _, n in socket.if_nameindex()} != {"lo"}:
        raise BoundedError("BUILD_CONTROLLER_ROOT_OFFLINE_NAMESPACE_REQUIRED")
    _require_pinned_code(job["builder_revision"])
    environment = check_environment(
        lock=safe_path(job["environment_lock"]), wheelhouse=safe_path(job["wheelhouse"])
    )
    user = pwd.getpwnam(execution["runtime_user"])
    if user.pw_uid == 0:
        raise BoundedError("BUILD_CONTROLLER_UNPRIVILEGED_WORKER_REQUIRED")
    root = safe_path(job["work_root"])
    materialization_revision = job.get(
        "materialization_revision", job["builder_revision"]
    )
    if not re.fullmatch(r"[0-9a-f]{40}", str(materialization_revision)):
        raise BoundedError("BUILD_CONTROLLER_MATERIALIZATION_REVISION_INVALID")
    resume = "materialization_revision" in job
    if resume:
        # A re-audit names the revision that produced these bytes. The product
        # must already be materialized and must not carry any seal marker.
        if not (root / "PUBLICATION.json").exists():
            raise BoundedError("BUILD_CONTROLLER_MATERIALIZATION_NOT_FOUND")
        for role in ("ODS", "DWD"):
            for marker in ("manifest.json", "READY"):
                if (root / "published" / role / marker).exists():
                    raise BoundedError("BUILD_CONTROLLER_MARKER_REQUIRES_RECOVERY")
        if (root / "PAIR-READY.json").exists():
            raise BoundedError("BUILD_CONTROLLER_PAIR_ALREADY_COMPLETE")
    roots = load(safe_path(job["roots"]))
    sources = [safe_path(p) for p in roots.values()]
    readonly = [
        REPOSITORY,
        safe_path(execution["runtime_python"], executable=True).parent.parent,
        safe_path(job["wheelhouse"]),
        safe_path(job["environment_lock"]),
    ]
    # A re-audit of an already materialized pair names the revision that owns
    # those bytes; it is an input identity, never a write target.
    if "materialization_revision" in job:
        readonly.append(safe_path(job["work_root"]))
    protected = [safe_path(p) for p in execution["protected_paths"]]
    disjoint_output(root, [*sources, *readonly, *protected])
    disjoint_output(
        safe_path(execution["audit_parent"]), [root, *sources, *readonly, *protected]
    )
    for denied in derive_denied(job, execution):
        disjoint_output(denied, readonly)
    limits = Limits(**job["limits"])
    owner_record = {
        "contract": CONTROLLER,
        "job_identity": identity(job),
        "policy_identity": identity(contract),
        "execution_identity": identity(execution),
        "environment_identity": environment["lock_sha256"],
    }
    owner = identity(owner_record)
    with controller_lock(root):
        if not (root / "CONTROLLER.json").exists():
            reserved = {
                "CONTROLLER.lock",
                ".CONTROLLER.json.partial",
                "controller",
                "interrupted-controls",
            }
            if any(p.name not in reserved for p in root.iterdir()):
                raise BoundedError("BUILD_CONTROLLER_UNKNOWN_INITIAL_JOB_PRESERVED")
        controls = member(root, "controller")
        controls.mkdir(exist_ok=True)
        _no_active_workers(controls)
        control_record(root / "CONTROLLER.json", owner_record)
        token = uuid.uuid4().hex
        attempt = controls / ("run-" + token)
        attempt.mkdir()
        control_record(
            attempt / "START.json",
            {**owner_record, "owner_identity": owner, "started_at": now()},
        )
        try:
            if resume:
                # Re-audit path: the materialization and its transfer intent are
                # already owned; never invoke the materialize worker again.
                intent = begin_transfer(root, {}, owner_identity=owner)
            elif not (root / "PUBLICATION.json").exists():
                controller_capacity(
                    root,
                    sources,
                    limits,
                    len(load(safe_path(job["selection"]))["expected_days"]),
                )
                os.chown(root, user.pw_uid, user.pw_gid)
                os.chmod(root, 0o750)
                result, _ = run_service(
                    action="materialize",
                    job=job,
                    contract=contract,
                    execution=execution,
                    repository=REPOSITORY,
                    parent=attempt / "materialize",
                    readonly=sources,
                    denied=protected,
                    writable=root,
                    token=token,
                )
                if set(result.get("stages", {})) != {"period", "windows", "ods", "dwd"}:
                    raise BoundedError("BUILD_CONTROLLER_COMPLETE_GRAPH_REQUIRED")
                outputs = {
                    role: safe_path(result["stages"][role]["output"])
                    for role in ("ods", "dwd")
                }
                # Recover a killed intent write before starting a new transfer.
                partial = root / ".PUBLICATION.json.partial"
                if partial.exists():
                    archive = controls / ("interrupted-intent-" + uuid.uuid4().hex)
                    os.rename(member(root, partial.name), archive)
                    _sync(root)
                intent = begin_transfer(root, outputs, owner_identity=owner)
            else:
                intent = begin_transfer(root, {}, owner_identity=owner)
                partial = root / ".PUBLICATION.json.partial"
                if partial.exists():
                    if not os.path.samefile(
                        member(root, partial.name), root / "PUBLICATION.json"
                    ):
                        raise BoundedError(
                            "BUILD_CONTROLLER_TRANSFER_INTENT_CONFLICT_PRESERVED"
                        )
                    partial.unlink()
                    _sync(root)
            # Resolve a killed no-overwrite marker link before the strict file
            # census in transfer recovery (which rejects multiple inode links).
            recover_owned_markers(
                root,
                {role: root / "published" / role.upper() for role in ("ods", "dwd")},
            )
            products = resume_transfer(root, intent)
            protect_tree(root / "stages", strict=False)
            protect_tree(root / "published", strict=True)
            os.chown(root, 0, 0)
            os.chmod(root, 0o555)
            os.chown(controls, 0, 0)
            os.chmod(controls, 0o555)
            complete_markers = recover_owned_markers(root, products)
            for path in products.values():
                require_protected(path)
            denied = derive_denied(job, execution)
            receipt, audited = _audit(
                job=job,
                contract=contract,
                execution=execution,
                repository=REPOSITORY,
                products=products,
                attempt=attempt / "audit-before",
                token=uuid.uuid4().hex,
                denied=denied,
                environment_identity=environment["lock_sha256"],
            )
            if not receipt["publication_eligible"]:
                if complete_markers or (root / "PAIR-READY.json").exists():
                    raise BoundedError("BUILD_CONTROLLER_INELIGIBLE_PAIR_HAS_MARKERS")
                status, formal, pair = (
                    "PAIRED_BUILT_AUDITED_HISTORICAL_GATE_PENDING",
                    False,
                    None,
                )
            else:
                if not complete_markers:
                    documents = pair_documents(
                        ods=products["ods"],
                        dwd=products["dwd"],
                        receipt=receipt,
                        contract=contract,
                        revision=job["builder_revision"],
                        materialization_revision=job.get(
                            "materialization_revision", job["builder_revision"]
                        ),
                    )
                    writes = root / "publication-writes"
                    numbers = (
                        [int(p.name[8:]) for p in writes.iterdir()]
                        if writes.exists()
                        else []
                    )
                    number = 1 + max(numbers, default=0)
                    name = f"attempt-{number:06d}"
                    prepared = prepare_markers(root, documents, attempt=name)
                    write_markers(root, products, prepared, attempt=name)
                    for path in products.values():
                        for marker in ("manifest.json", "READY"):
                            os.chmod(path / marker, 0o444)
                    receipt, audited = _audit(
                        job=job,
                        contract=contract,
                        execution=execution,
                        repository=REPOSITORY,
                        products=products,
                        attempt=attempt / "audit-sealed",
                        token=uuid.uuid4().hex,
                        denied=denied,
                        environment_identity=environment["lock_sha256"],
                    )
                verify_pair_seal(products["ods"], products["dwd"], receipt)
                pair = _pair_completion(root, owner, products, receipt, audited)
                status, formal = (
                    "PAIRED_SEALED_SOURCE_AUDIT_PASS_INDEPENDENT_ACCEPTANCE_REQUIRED",
                    True,
                )
            resources = job_capacity(root, sources, limits)
            result = {
                "stage": "build",
                "status": status,
                "products": {r: str(p) for r, p in products.items()},
                "owner_identity": owner,
                "audit_execution_identity": audited["identity"],
                "pair_completion_identity": pair["identity"] if pair else None,
                "retained_job_resources": resources,
                "formal_READY": formal,
                "full_A_B_C": False,
                "production_changed": False,
                "consumer_admission": "NOT_GRANTED",
                "finished_at": now(),
            }
            control_record(attempt / "SUCCESS.json", result)
            return result
        except BaseException as exc:
            control_record(
                attempt / "FAILURE.json",
                {
                    "contract": CONTROLLER,
                    "owner_identity": owner,
                    "failed_at": now(),
                    "exception_type": type(exc).__name__,
                    "reason_code": _reason(exc),
                    "status": "FAILED_PRESERVED",
                    "formal_READY": False,
                },
            )
            raise

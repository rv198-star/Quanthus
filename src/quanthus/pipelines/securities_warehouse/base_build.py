"""Standard paired Base input, build, source audit and guarded publication.

Unsealed paired materialization shares the admitted period and window closure.
Readonly paired audit replays archived sources. The offline root build controller
owns protected publication and recovery. Registered historical gates remain
mandatory. Independent A/B/C acceptance and consumer activation stay separate.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base22_bounded import BoundedError, Budget, Limits
from .base23_native_image import image_schemas
from .base_build_audit import audit_pair
from .base_build_environment import check_environment
from .base_build_freeze import freeze
from .base_build_inputs import canonical, file_sha, identity, member
from .base_build_ods import save
from .base_build_pair import materialization_stages
from .base_build_period import VerifiedInputs, load, prepare_period
from .base_build_profile import validate_contract
from .base_build_resources import job_capacity, remaining_stage_limits
from .base_build_stages import Stage, StageRunner, _save_new

REPOSITORY = Path(__file__).resolve().parents[4]
JOB = "p10-standard-base-build-job-v1"
JOB_KEYS = {
    "contract",
    "builder_revision",
    "selection",
    "frozen",
    "roots",
    "work_root",
    "environment_lock",
    "wheelhouse",
    "source_evidence_id",
    "calendar_reference",
    "closed_days",
    "limits",
}
AUDIT_JOB = "p10-standard-base-pair-audit-job-v1"
AUDIT_KEYS = {
    "contract",
    "builder_revision",
    "ods",
    "dwd",
    "work_root",
    "frozen_input_identity",
    "environment_lock",
    "wheelhouse",
    "limits",
    "denied_input_roots",
}


def validate_audit_job(job: dict[str, Any]) -> None:
    if (
        set(job) != AUDIT_KEYS
        or job.get("contract") != AUDIT_JOB
        or not re.fullmatch(r"[0-9a-f]{40}", str(job.get("builder_revision")))
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(job.get("frozen_input_identity")))
    ):
        raise BoundedError("BUILD_AUDIT_JOB_SCHEMA_OR_IDENTITY_INVALID")
    for key in ("ods", "dwd", "work_root", "environment_lock", "wheelhouse"):
        if not isinstance(job[key], str) or not Path(job[key]).is_absolute():
            raise BoundedError("BUILD_AUDIT_JOB_ABSOLUTE_PATH_REQUIRED")
    denied = job["denied_input_roots"]
    if (
        not isinstance(denied, list)
        or not denied
        or any(not isinstance(p, str) or not Path(p).is_absolute() for p in denied)
        or len(set(denied)) != len(denied)
    ):
        raise BoundedError("BUILD_AUDIT_SOURCE_DENIAL_DECLARATION_REQUIRED")
    Limits(**job["limits"])


def run_audit(*, job: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    validate_audit_job(job)
    validate_contract(contract)
    _require_pinned_code(job["builder_revision"])
    environment = check_environment(
        lock=Path(job["environment_lock"]), wheelhouse=Path(job["wheelhouse"])
    )
    products = {k: job[k] for k in ("ods", "dwd")}
    limits = Limits(**job["limits"])
    _preflight_paths(products, Path(job["work_root"]), limits, 0)
    for raw in job["denied_input_roots"]:
        path = Path(raw)
        try:
            readable = path.exists() and os.access(path, os.R_OK)
        except PermissionError:
            # InaccessiblePaths may deny traversal of an original source's parent.
            readable = False
        if readable:
            raise BoundedError("BUILD_AUDIT_ORIGINAL_SOURCE_STILL_READABLE")
    sources = [Path(p) for p in products.values()]
    owned = False
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        with Budget(Path(job["work_root"]), sources, limits) as budget:
            owned = True
            receipt = audit_pair(
                ods=Path(job["ods"]),
                dwd=Path(job["dwd"]),
                contract=contract,
                expected_input_identity=job["frozen_input_identity"],
                budget=budget,
            )
            if receipt["binding"]["code_revision"] != job["builder_revision"]:
                raise BoundedError("BUILD_AUDIT_CODE_BINDING_CHANGED")
            _save_new(budget.root / "AUDIT.json", receipt)
            completion = {
                "contract": "p10-standard-base-audit-execution-v1",
                "job_identity": identity(job),
                "work_root": job["work_root"],
                "code_revision": job["builder_revision"],
                "policy_identity": identity(contract),
                "environment_identity": environment["lock_sha256"],
                "audit_identity": receipt["identity"],
                "audit_sha256": file_sha(budget.root / "AUDIT.json"),
                "started_at": started_at,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "publication_eligible": receipt["publication_eligible"],
                "formal_READY": False,
            }
            completion["identity"] = identity(completion)
            _save_new(budget.root / "COMPLETION.json", completion)
    except BaseException as exc:
        if owned:
            reason = str(exc)
            reason = (
                reason if re.fullmatch(r"[A-Z][A-Z0-9_]{0,159}", reason) else type(exc).__name__
            )
            save(
                Path(job["work_root"]) / "FAILURE.json",
                {
                    "contract": AUDIT_JOB,
                    "job_identity": identity(job),
                    "frozen_input_identity": job["frozen_input_identity"],
                    "code_revision": job["builder_revision"],
                    "policy_identity": identity(contract),
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                    "exception_type": type(exc).__name__,
                    "reason_code": reason,
                    "status": "FAILED_PRESERVED",
                    "formal_READY": False,
                },
            )
        raise
    return {
        "stage": "audit",
        "status": (
            "PAIRED_SOURCE_STRUCTURE_AND_REGISTERED_QUALITY_GATES_PASS"
            if receipt["publication_eligible"]
            else "PAIRED_SOURCE_AND_STRUCTURE_PASS_HISTORICAL_GATE_PENDING"
        ),
        "audit_identity": receipt["identity"],
        "output": str(budget.root / "AUDIT.json"),
        "completion": str(budget.root / "COMPLETION.json"),
        "execution_identity": completion["identity"],
        "environment_identity": environment["lock_sha256"],
        "resources": dict(budget.peak),
        "formal_READY": False,
        "publication_eligible": receipt["publication_eligible"],
        "full_A_B_C": False,
    }


def validate_job(job: dict[str, Any]) -> None:
    if (
        set(job) != JOB_KEYS
        or job["contract"] != JOB
        or not re.fullmatch(r"[0-9a-f]{40}", str(job["builder_revision"]))
    ):
        raise BoundedError("BUILD_JOB_SCHEMA_OR_REVISION_INVALID")
    for key in (
        "selection",
        "frozen",
        "roots",
        "work_root",
        "environment_lock",
        "wheelhouse",
    ):
        if not isinstance(job[key], str) or not Path(job[key]).is_absolute():
            raise BoundedError("BUILD_JOB_ABSOLUTE_PATH_REQUIRED")
    reference = job["calendar_reference"]
    if not isinstance(reference, dict) or set(reference) != {
        "evidence_id",
        "path",
        "parent_release",
        "parent_core",
        "parent_daily",
        "artifact_name",
    }:
        raise BoundedError("BUILD_JOB_CALENDAR_REFERENCE_SCHEMA")
    if not isinstance(job["closed_days"], list):
        raise BoundedError("BUILD_JOB_CLOSED_DAYS_SCHEMA")
    for entry in job["closed_days"]:
        if not isinstance(entry, dict) or set(entry) != {
            "evidence_id",
            "day",
            "release_path",
            "release_id",
            "manifest_sha256",
        }:
            raise BoundedError("BUILD_JOB_CLOSED_DAYS_SCHEMA")
    Limits(**job["limits"])


def _require_pinned_code(revision: str) -> None:
    git = [
        "git",
        "--no-optional-locks",
        "-c",
        "safe.directory=" + str(REPOSITORY),
        "-C",
        str(REPOSITORY),
    ]
    head = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output([*git, "status", "--porcelain"], text=True)
    if head != revision or dirty:
        raise BoundedError("BUILD_CLEAN_PINNED_GITHUB_WORKTREE_REQUIRED")


def _preflight_paths(roots: dict[str, str], work_root: Path, limits: Limits, days: int) -> None:
    usage = job_capacity(work_root, [Path(p) for p in roots.values()], limits)
    if {name for _, name in socket.if_nameindex()} != {"lo"}:
        raise BoundedError("BUILD_ISOLATED_OFFLINE_NETWORK_REQUIRED")
    for source in roots.values():
        if not os.statvfs(source).f_flag & os.ST_RDONLY:
            raise BoundedError("BUILD_KERNEL_READONLY_INPUT_REQUIRED")
    ancestor = work_root
    while not ancestor.exists():
        ancestor = ancestor.parent
    fs = os.statvfs(ancestor)
    if not os.access(ancestor, os.W_OK | os.X_OK):
        raise BoundedError("BUILD_RUNTIME_USER_CANNOT_WRITE_WORK_ROOT")
    if fs.f_bavail * fs.f_frsize < usage["remaining_allocated_bytes"] + limits.free_floor_bytes:
        raise BoundedError("BUILD_WORK_SPACE_RESERVE_INSUFFICIENT")
    if fs.f_favail < 4000 + days * 500:
        raise BoundedError("BUILD_WORK_INODE_RESERVE_INSUFFICIENT")


def run(
    *,
    action: str,
    job: dict[str, Any],
    contract: dict[str, Any],
    execution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if action == "build":
        from .base_build_controller import execute

        if execution is None:
            raise BoundedError("BUILD_EXECUTION_CONTRACT_REQUIRED")
        return execute(job=job, contract=contract, execution=execution)
    if execution is not None:
        raise BoundedError("BUILD_EXECUTION_ONLY_FOR_BUILD_ACTION")
    if action == "audit":
        return run_audit(job=job, contract=contract)
    validate_job(job)
    validate_contract(contract)
    _require_pinned_code(job["builder_revision"])
    environment = check_environment(
        lock=Path(job["environment_lock"]), wheelhouse=Path(job["wheelhouse"])
    )
    selection, roots = load(Path(job["selection"])), load(Path(job["roots"]))
    if (
        selection.get("target_version") != contract["base_version"]
        or selection.get("predecessor_version") != contract["predecessor_version"]
        or selection.get("through_date") != contract["business_through"]
    ):
        raise BoundedError("BUILD_JOB_SELECTION_CONTRACT_SCOPE_DIFFERS")
    limits = Limits(**job["limits"])
    sources = [Path(p) for p in roots.values()]
    _preflight_paths(roots, Path(job["work_root"]), limits, len(selection["expected_days"]))
    frozen_path = Path(job["frozen"])
    if action == "freeze":
        actual = freeze(selection, roots)
        if frozen_path.exists():
            if load(frozen_path) != actual:
                raise BoundedError("BUILD_EXISTING_FROZEN_INPUT_CONFLICT")
        else:
            for root in sources:
                if root == frozen_path or root in frozen_path.parents:
                    raise BoundedError("BUILD_FROZEN_RECEIPT_INSIDE_SOURCE")
            with frozen_path.open("xb") as stream:
                stream.write(canonical(actual) + b"\n")
        return {
            "stage": "freeze",
            "identity": actual["identity"],
            "formal_READY": False,
        }
    if action not in {"prepare-period", "materialize"}:
        raise BoundedError("BUILD_ACTION_NOT_IMPLEMENTED")
    # Full input verification runs exactly once in this invocation, including a
    # verified stage reuse. The resulting object is shared with the period stage.
    inputs = VerifiedInputs.verify(load(frozen_path), roots)
    if inputs.frozen["content"]["selection"] != selection:
        raise BoundedError("BUILD_JOB_SELECTION_CHANGED_AFTER_FREEZE")
    base = next(r for r in selection["packages"] if r["role"] == "dwd")
    binding = {
        "predecessor_manifest_sha256": base["manifest_sha256"],
        "frozen_input_identity": inputs.verified_identity,
        "code_revision": job["builder_revision"],
        "policy_identity": identity(contract),
        "predecessor_through": selection["predecessor_through"],
        "through_date": selection["through_date"],
        "target_version": selection["target_version"],
    }
    config = REPOSITORY / "config/pipelines"
    rules = load(config / "base22-independent-image.v1.json")
    schemas = image_schemas(
        load(config / "dwd-domain-model.v1.json"),
        load(config / "dwd-domain-model.v1.1-extension.json"),
    )
    stage_configuration = {
        k: job[k] for k in ("source_evidence_id", "closed_days", "calendar_reference")
    }

    def build(output: Path, dependencies: dict[str, Path]) -> dict[str, Any]:
        with Budget(
            output, sources, remaining_stage_limits(Path(job["work_root"]), sources, limits)
        ) as budget:
            result = prepare_period(
                inputs=inputs,
                binding=binding,
                rules=rules,
                schemas=schemas,
                output=output / "period",
                budget=budget,
                **stage_configuration,
            )
        return {
            "admission_identity": result["identity"],
            "delta_identity": result["delta_identity"],
            "security_days": result["new_windows"],
            "resources": dict(budget.peak),
        }

    def verify(output: Path, result: dict[str, Any]) -> None:
        receipt = load(member(output, "period/ADMISSION.json"))
        delta = load(member(output, "period/delta/DELTA.json"))
        if (
            receipt.get("binding") != binding
            or receipt.get("identity")
            != identity({k: v for k, v in receipt.items() if k != "identity"})
            or receipt["identity"] != result["admission_identity"]
            or receipt["delta_identity"] != result["delta_identity"]
            or delta.get("identity")
            != identity({k: v for k, v in delta.items() if k != "identity"})
            or delta["identity"] != receipt["delta_identity"]
            or receipt["new_quality_held"] != 0
        ):
            raise BoundedError("BUILD_PERIOD_STAGE_RECEIPT_CHANGED")

    stages = [Stage("period", (), build, verify, configuration=stage_configuration)]
    if action == "materialize":
        stages.extend(
            materialization_stages(
                inputs=inputs,
                binding=binding,
                contract=contract,
                config_root=config,
                limits=limits,
                job_root=Path(job["work_root"]),
            )
        )
    runner = StageRunner(
        root=Path(job["work_root"]),
        sources=sources,
        limits=limits,
        binding={
            "input_identity": inputs.verified_identity,
            "code_revision": job["builder_revision"],
            "environment_identity": environment["lock_sha256"],
            "policy_identity": identity(contract),
            "scope": {
                k: binding[k] for k in ("predecessor_through", "through_date", "target_version")
            },
        },
        stages=stages,
    )
    all_completed = runner.execute()
    completed = all_completed["period"]
    return {
        "stage": action,
        "status": (
            "PAIRED_UNSEALED_MATERIALIZATIONS_COMPLETE_AUDIT_SEAL_PENDING"
            if action == "materialize"
            else "SOURCE_ADMISSION_AND_PERIOD_TMP_VERIFIED"
        ),
        "stages": {
            name: {
                "output": str(r["path"]),
                "reused": r["reused"],
                "output_identity": r["receipt"]["output_identity"],
            }
            for name, r in all_completed.items()
        },
        "output": str(completed["path"]),
        "reused": completed["reused"],
        "stage_identity": completed["receipt"]["stage_identity"],
        "output_identity": completed["receipt"]["output_identity"],
        "result": completed["receipt"]["content"]["result"],
        "retained_job_resources": job_capacity(Path(job["work_root"]), sources, limits),
        "formal_READY": False,
        "kernel_readonly_inputs": True,
        "network_interfaces": sorted(name for _, name in socket.if_nameindex()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("freeze", "prepare-period", "materialize", "audit", "build")
    )
    parser.add_argument("--job", required=True, type=Path)
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--execution", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            run(
                action=args.action,
                job=load(args.job),
                contract=load(args.contract),
                execution=load(args.execution) if args.execution else None,
            )
        )
    )


if __name__ == "__main__":
    main()

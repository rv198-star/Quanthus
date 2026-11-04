"""Standard Base build entry point for implemented input/period stages.

Unsealed paired materialization shares the admitted period and window closure.
Audit/seal stages remain under implementation. No action here writes a formal
Base READY or activates consumers.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
from pathlib import Path
from typing import Any

from .base22_bounded import BoundedError, Budget, Limits
from .base23_native_image import image_schemas
from .base_build_environment import check_environment
from .base_build_freeze import freeze
from .base_build_inputs import canonical, identity, member
from .base_build_pair import materialization_stages
from .base_build_period import VerifiedInputs, load, prepare_period
from .base_build_profile import validate_contract
from .base_build_stages import Stage, StageRunner

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
    head = subprocess.check_output(
        ["git", "-C", str(REPOSITORY), "rev-parse", "HEAD"], text=True
    ).strip()
    dirty = subprocess.check_output(
        ["git", "-C", str(REPOSITORY), "status", "--porcelain"], text=True
    )
    if head != revision or dirty:
        raise BoundedError("BUILD_CLEAN_PINNED_GITHUB_WORKTREE_REQUIRED")


def _preflight_paths(roots: dict[str, str], work_root: Path, limits: Limits, days: int) -> None:
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
    if fs.f_bavail * fs.f_frsize < limits.allocated_bytes + limits.free_floor_bytes:
        raise BoundedError("BUILD_WORK_SPACE_RESERVE_INSUFFICIENT")
    if fs.f_favail < 4000 + days * 500:
        raise BoundedError("BUILD_WORK_INODE_RESERVE_INSUFFICIENT")


def run(*, action: str, job: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
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
        with Budget(output, sources, limits) as budget:
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
                inputs=inputs, binding=binding, contract=contract, config_root=config, limits=limits
            )
        )
    runner = StageRunner(
        root=Path(job["work_root"]),
        sources=sources,
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
        "formal_READY": False,
        "kernel_readonly_inputs": True,
        "network_interfaces": sorted(name for _, name in socket.if_nameindex()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "prepare-period", "materialize"))
    parser.add_argument("--job", required=True, type=Path)
    parser.add_argument("--contract", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run(action=args.action, job=load(args.job), contract=load(args.contract))))


if __name__ == "__main__":
    main()

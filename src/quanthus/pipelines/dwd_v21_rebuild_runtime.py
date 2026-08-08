"""Checkpointed preparation runtime for the governed DWD Base V2.1 rebuild."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from quanthus.pipelines.dwd_v21_rebuild_monitor import verify_authority
from quanthus.pipelines.dwd_v21_rebuild_preflight import (
    DwdV21PreflightError,
    load_environment,
    run_preflight,
)
from quanthus.pipelines.dwd_v21_rebuild_preflight import (
    write_receipt as write_preflight_receipt,
)
from quanthus.pipelines.securities_warehouse import (
    WarehousePlanningError,
    build_input_plan,
)


class DwdV21RuntimeError(RuntimeError):
    """Raised when the rebuild runtime cannot advance safely."""


def _canonical_digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return "sha256:" + hashlib.sha256(payload.encode("ascii")).hexdigest()


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value: Any = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DwdV21RuntimeError(f"cannot read {label}: {path}: {error}") from error
    if not isinstance(value, dict):
        raise DwdV21RuntimeError(f"{label} must be a JSON object: {path}")
    return cast(dict[str, Any], value)


def _sealed(value: Mapping[str, Any]) -> dict[str, Any]:
    unsigned = dict(value)
    unsigned.pop("canonical_digest", None)
    return {**unsigned, "canonical_digest": _canonical_digest(unsigned)}


def _verify_sealed(path: Path, label: str) -> dict[str, Any]:
    value = _object(path, label)
    supplied = value.get("canonical_digest")
    unsigned = dict(value)
    unsigned.pop("canonical_digest", None)
    actual = _canonical_digest(unsigned)
    if supplied != actual:
        raise DwdV21RuntimeError(
            f"{label} canonical digest mismatch: supplied={supplied!r} actual={actual}"
        )
    return value


def _atomic_json(path: Path, value: Mapping[str, Any], *, mode: int = 0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            os.fchmod(stream.fileno(), mode)
            json.dump(dict(value), stream, ensure_ascii=True, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _phase_receipt(
    run_root: Path,
    *,
    phase: str,
    run_id: str,
    contract_digest: str,
    code_revision: str,
    status: str,
    evidence: Mapping[str, Any] | None = None,
) -> Path:
    path = run_root / "state" / f"{phase}.json"
    if path.exists():
        current = _verify_sealed(path, f"{phase} phase receipt")
        expected = {
            "run_id": run_id,
            "contract_digest": contract_digest,
            "code_revision": code_revision,
            "phase": phase,
            "status": status,
        }
        if any(current.get(key) != value for key, value in expected.items()):
            raise DwdV21RuntimeError(f"existing phase receipt belongs to another run: {path}")
        if evidence is not None and current.get("evidence") != dict(evidence):
            raise DwdV21RuntimeError(f"existing phase receipt evidence differs: {path}")
        return path
    receipt = _sealed(
        {
            "schema_version": 1,
            "kind": "dwd_base_v2_1_rebuild_phase_receipt",
            "run_id": run_id,
            "contract_digest": contract_digest,
            "code_revision": code_revision,
            "phase": phase,
            "status": status,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "evidence": dict(evidence or {}),
        }
    )
    _atomic_json(path, receipt, mode=0o440)
    return path


def _active_pointer(
    workspace: Path,
    *,
    run_id: str,
    phase: str,
    contract_digest: str,
    executor_pid: int,
) -> None:
    _atomic_json(
        workspace / "control" / "active-run.json",
        {
            "schema_version": 1,
            "kind": "dwd_base_v2_1_active_run",
            "run_id": run_id,
            "phase": phase,
            "contract_digest": contract_digest,
            "executor_pid": executor_pid,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def _advance(
    workspace: Path,
    run_root: Path,
    *,
    phase: str,
    run_id: str,
    contract_digest: str,
    code_revision: str,
    status: str,
    evidence: Mapping[str, Any] | None = None,
) -> Path:
    receipt = _phase_receipt(
        run_root,
        phase=phase,
        run_id=run_id,
        contract_digest=contract_digest,
        code_revision=code_revision,
        status=status,
        evidence=evidence,
    )
    _active_pointer(
        workspace,
        run_id=run_id,
        phase=phase,
        contract_digest=contract_digest,
        executor_pid=os.getpid(),
    )
    return receipt


def _require_pilot_only(environment_path: Path, expected_revision: str) -> dict[str, str]:
    environment: dict[str, str] = load_environment(environment_path)
    expected = {
        "QUANTHUS_DWD_BUILDER_AUTHORIZED": "0",
        "QUANTHUS_DWD_CODE_REVISION": expected_revision,
        "QUANTHUS_DWD_EXECUTION_MODE": "pilot_only",
        "QUANTHUS_DWD_PILOT_AUTHORIZED": "1",
    }
    if any(environment.get(key) != value for key, value in expected.items()):
        raise DwdV21RuntimeError("deployment environment is not the approved pilot-only state")
    return environment


def run_prepare(
    *,
    repository: Path,
    workspace: Path,
    ods_base: Path,
    dwd_base: Path,
    environment_path: Path,
    run_id: str,
    expected_revision: str,
    expected_contract_digest: str,
    expected_findings_digest: str,
    expected_remediation_digest: str,
    minimum_free_bytes: int,
    knowledge_cutoff: str,
    through_date: str,
    runtime_id: str,
) -> dict[str, Any]:
    """Run the authorized preflight and planning phases, then pause at the pilot gate."""

    repository = repository.resolve()
    workspace = workspace.resolve()
    ods_base = ods_base.resolve()
    dwd_base = dwd_base.resolve()
    environment_path = environment_path.resolve()
    run_root = workspace / "runs" / run_id
    contract_path = repository / "config/audits/dwd-base-v2-1-gpu-p100-execution.v1.json"
    approval_path = (
        repository
        / "docs/acceptance/issue-78-dwd-base-v2-1-gpu-p100-execution-approval-20260807.json"
    )
    closure_root = (
        repository / "docs/acceptance/issue-78-dwd-base-v2-pre-rebuild-audit-closure-20260807"
    )
    findings_path = closure_root / "findings-ledger.json"
    remediation_path = closure_root / "v2.1-remediation-scope.json"
    model_path = repository / "config/pipelines/dwd-domain-model.v1.json"
    input_override = repository / "config/pipelines/dwd-v2-1-input-identity.v1.json"

    verify_authority(contract_path, approval_path, expected_contract_digest)
    _require_pilot_only(environment_path, expected_revision)
    run_root.mkdir(parents=True, exist_ok=True)
    _advance(
        workspace,
        run_root,
        phase="bootstrap",
        run_id=run_id,
        contract_digest=expected_contract_digest,
        code_revision=expected_revision,
        status="running",
    )

    try:
        _advance(
            workspace,
            run_root,
            phase="preflight-running",
            run_id=run_id,
            contract_digest=expected_contract_digest,
            code_revision=expected_revision,
            status="running",
        )
        preflight = run_preflight(
            repository=repository,
            workspace=workspace,
            ods_base=ods_base,
            dwd_base=dwd_base,
            contract_path=contract_path,
            approval_path=approval_path,
            findings_path=findings_path,
            remediation_path=remediation_path,
            environment_path=environment_path,
            expected_revision=expected_revision,
            expected_contract_digest=expected_contract_digest,
            expected_findings_digest=expected_findings_digest,
            expected_remediation_digest=expected_remediation_digest,
            minimum_free_bytes=minimum_free_bytes,
        )
        preflight_path = write_preflight_receipt(run_root / "evidence" / "preflight", preflight)
        sealed_preflight = _object(preflight_path, "preflight receipt")
        _advance(
            workspace,
            run_root,
            phase="preflight-passed",
            run_id=run_id,
            contract_digest=expected_contract_digest,
            code_revision=expected_revision,
            status="passed",
            evidence={
                "receipt": str(preflight_path),
                "receipt_digest": sealed_preflight.get("canonical_digest"),
            },
        )

        lock_path = workspace / "control" / "locks" / "dwd-base-v2-1-rebuild.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o640)
        try:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise DwdV21RuntimeError("GPU rebuild lock is already held") from error
            _advance(
                workspace,
                run_root,
                phase="planning-running",
                run_id=run_id,
                contract_digest=expected_contract_digest,
                code_revision=expected_revision,
                status="running",
            )
            plan = build_input_plan(
                ods_package=ods_base,
                model_path=model_path,
                output=run_root / "plan",
                mode="base",
                run_id=run_id,
                knowledge_cutoff=knowledge_cutoff,
                code_revision=expected_revision,
                runtime_id=runtime_id,
                through_date=through_date,
                capacity_root=workspace,
                input_identity_override=input_override,
            )
        finally:
            os.close(lock_fd)

        plan_evidence = {
            "root": str(plan.root),
            "plan_digest": plan.plan_digest,
            "input_manifest_sha256": plan.input_manifest_sha256,
            "required_free_bytes": plan.required_free_bytes,
            "available_free_bytes": plan.available_free_bytes,
            "capacity_sufficient": plan.capacity_sufficient,
        }
        _advance(
            workspace,
            run_root,
            phase="planning-passed",
            run_id=run_id,
            contract_digest=expected_contract_digest,
            code_revision=expected_revision,
            status="passed",
            evidence=plan_evidence,
        )
        _advance(
            workspace,
            run_root,
            phase="awaiting-pilot-receipts",
            run_id=run_id,
            contract_digest=expected_contract_digest,
            code_revision=expected_revision,
            status="paused",
            evidence={
                **plan_evidence,
                "required_receipts": ["replay-pilot", "partition-pilot"],
                "builder_started": False,
            },
        )
        return {
            "run_id": run_id,
            "run_root": str(run_root),
            "phase": "awaiting-pilot-receipts",
            "status": "paused",
            "builder_started": False,
            **plan_evidence,
        }
    except (DwdV21PreflightError, WarehousePlanningError, OSError) as error:
        stamp = datetime.now(timezone.utc).strftime("failed-%Y%m%dT%H%M%SZ")
        _advance(
            workspace,
            run_root,
            phase=stamp,
            run_id=run_id,
            contract_digest=expected_contract_digest,
            code_revision=expected_revision,
            status="failed",
            evidence={"error": str(error), "builder_started": False},
        )
        raise DwdV21RuntimeError(str(error)) from error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--ods-base", type=Path, required=True)
    parser.add_argument("--dwd-base", type=Path, required=True)
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--expected-contract-digest", required=True)
    parser.add_argument("--expected-findings-digest", required=True)
    parser.add_argument("--expected-remediation-digest", required=True)
    parser.add_argument("--minimum-free-bytes", type=int, required=True)
    parser.add_argument("--knowledge-cutoff", required=True)
    parser.add_argument("--through-date", required=True)
    parser.add_argument("--runtime-id", required=True)
    parser.add_argument("--resume", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    run_root = args.workspace.resolve() / "runs" / args.run_id
    if run_root.exists() and not args.resume:
        print(f"DWD V2.1 rebuild runtime failed: run already exists: {run_root}", file=sys.stderr)
        return 2
    try:
        result = run_prepare(
            repository=args.repository,
            workspace=args.workspace,
            ods_base=args.ods_base,
            dwd_base=args.dwd_base,
            environment_path=args.environment,
            run_id=args.run_id,
            expected_revision=args.expected_revision,
            expected_contract_digest=args.expected_contract_digest,
            expected_findings_digest=args.expected_findings_digest,
            expected_remediation_digest=args.expected_remediation_digest,
            minimum_free_bytes=args.minimum_free_bytes,
            knowledge_cutoff=args.knowledge_cutoff,
            through_date=args.through_date,
            runtime_id=args.runtime_id,
        )
    except DwdV21RuntimeError as error:
        print(f"DWD V2.1 rebuild runtime failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

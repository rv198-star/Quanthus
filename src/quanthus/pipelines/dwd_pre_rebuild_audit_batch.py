"""Sequential, resumable orchestration for CN01 DWD pre-rebuild audits."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class DwdPreRebuildAuditBatchError(RuntimeError):
    """Raised when an audit batch cannot produce trustworthy evidence."""


@dataclass(frozen=True)
class AuditStep:
    name: str
    kind: str
    report_name: str


AUDIT_STEPS = (
    AuditStep(
        name="schema_partitions",
        kind="dwd_base_v2_physical_schema_partition_audit",
        report_name="table-and-partition-profile.json",
    ),
    AuditStep(
        name="semantic_contracts",
        kind="dwd_base_v2_semantic_contract_audit",
        report_name="semantic-contract-audit.json",
    ),
    AuditStep(
        name="lineage_authority",
        kind="dwd_base_v2_lineage_authority_audit",
        report_name="lineage-authority-audit.json",
    ),
    AuditStep(
        name="domain_invariants",
        kind="dwd_base_v2_domain_invariant_audit",
        report_name="numeric-domain-invariant-audit.json",
    ),
)

_STEP_BY_NAME = {step.name: step for step in AUDIT_STEPS}
_TERMINAL_STEP_STATUSES = {"verified", "completed_with_findings"}
_BATCH_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _canonical_digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
        "ascii"
    )
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(value)
    payload.pop("canonical_digest", None)
    payload["canonical_digest"] = _canonical_digest(payload)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.partial")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )
    temporary.chmod(0o640)
    os.replace(temporary, path)


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DwdPreRebuildAuditBatchError(f"cannot read audit report: {path}") from exc
    if not isinstance(value, dict):
        raise DwdPreRebuildAuditBatchError(f"audit report must be a JSON object: {path}")
    return value


def classify_report(path: Path, *, step: AuditStep, package_id: str) -> dict[str, str]:
    """Validate one immutable report and classify findings separately from failure."""

    report = _object(path)
    supplied_digest = report.pop("canonical_digest", None)
    expected_digest = _canonical_digest(report)
    if supplied_digest != expected_digest:
        raise DwdPreRebuildAuditBatchError(f"audit report digest mismatch: {path}")
    if report.get("kind") != step.kind:
        raise DwdPreRebuildAuditBatchError(f"unexpected audit report kind for {step.name}: {path}")

    if step.name == "lineage_authority":
        actual_package_id = report.get("dwd", {}).get("package_id")
        status = report.get("status")
        mutation_performed = report.get("mutation_performed")
    else:
        actual_package_id = report.get("base_release", {}).get("package_id")
        summary = report.get("summary", {})
        status = summary.get("status")
        mutation_performed = summary.get("mutation_performed")
    if actual_package_id != package_id:
        raise DwdPreRebuildAuditBatchError(
            f"audit report package differs for {step.name}: {actual_package_id}"
        )
    if mutation_performed is not False:
        raise DwdPreRebuildAuditBatchError(
            f"audit report does not prove read-only execution: {path}"
        )
    if status not in {"verified", "finding"}:
        raise DwdPreRebuildAuditBatchError(
            f"audit report has invalid status for {step.name}: {status}"
        )
    return {
        "status": "verified" if status == "verified" else "completed_with_findings",
        "report_digest": str(supplied_digest),
    }


def _command(
    step: AuditStep,
    *,
    repository: Path,
    dwd_root: Path,
    ods_root: Path,
    plan_root: Path,
    report: Path,
    scratch: Path,
    checkpoint: Path,
    memory_limit: str,
) -> list[str]:
    executable = repository / "ops" / "bin"
    if step.name == "schema_partitions":
        return [
            sys.executable,
            str(executable / "audit-dwd-schema-partitions"),
            "--root",
            str(dwd_root),
            "--output",
            str(report),
        ]
    if step.name == "semantic_contracts":
        return [
            sys.executable,
            str(executable / "audit-dwd-semantic-contracts"),
            "--root",
            str(dwd_root),
            "--scratch",
            str(scratch),
            "--checkpoint",
            str(checkpoint),
            "--memory-limit",
            memory_limit,
            "--output",
            str(report),
        ]
    if step.name == "lineage_authority":
        return [
            sys.executable,
            str(executable / "audit-dwd-lineage-authority"),
            "--dwd-root",
            str(dwd_root),
            "--ods-root",
            str(ods_root),
            "--plan-root",
            str(plan_root),
            "--repository",
            str(repository),
            "--output",
            str(report),
        ]
    if step.name == "domain_invariants":
        return [
            sys.executable,
            str(executable / "audit-dwd-domain-invariants"),
            "--root",
            str(dwd_root),
            "--output",
            str(report),
        ]
    raise DwdPreRebuildAuditBatchError(f"unregistered audit step: {step.name}")


def _initial_state(
    *, batch_id: str, package_id: str, code_revision: str, selected_steps: Sequence[str]
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "dwd_base_v2_pre_rebuild_audit_batch",
        "batch_id": batch_id,
        "package_id": package_id,
        "code_revision": code_revision,
        "status": "running",
        "started_at": _utc_now(),
        "completed_at": None,
        "mutation_performed": False,
        "selected_steps": list(selected_steps),
        "steps": {
            step.name: {"status": "pending" if step.name in selected_steps else "not_selected"}
            for step in AUDIT_STEPS
        },
        "remaining_audit_scope": [
            "coverage_and_exclusion_matrix",
            "representative_replay_determinism",
            "downstream_and_increment_compatibility",
            "rebuild_publication_and_rollback_readiness",
            "findings_and_remediation_freeze",
        ],
    }


def run_audit_batch(
    *,
    repository: Path,
    dwd_root: Path,
    ods_root: Path,
    plan_root: Path,
    output_root: Path,
    batch_id: str,
    package_id: str,
    code_revision: str,
    selected_steps: Sequence[str],
    evidence: Mapping[str, Path],
    memory_limit: str = "2GB",
    required_output_ancestor: Path | None = None,
) -> dict[str, Any]:
    """Run registered audits in order, resuming only from digest-valid evidence."""

    unknown = set(selected_steps).difference(_STEP_BY_NAME)
    unknown_evidence = set(evidence).difference(_STEP_BY_NAME)
    if unknown or unknown_evidence:
        names = sorted(unknown | unknown_evidence)
        raise DwdPreRebuildAuditBatchError(f"unknown audit steps: {', '.join(names)}")
    if not selected_steps:
        raise DwdPreRebuildAuditBatchError("at least one audit step is required")
    if len(selected_steps) != len(set(selected_steps)):
        raise DwdPreRebuildAuditBatchError("audit steps must not be repeated")
    if not code_revision or any(
        character not in "0123456789abcdef" for character in code_revision
    ):
        raise DwdPreRebuildAuditBatchError("code revision must be a lowercase Git object id")
    if not set(evidence).issubset(selected_steps):
        raise DwdPreRebuildAuditBatchError("evidence was supplied for an unselected audit step")
    if _BATCH_ID.fullmatch(batch_id) is None:
        raise DwdPreRebuildAuditBatchError("batch id must be a safe 1-128 character name")

    repository = repository.resolve()
    dwd_root = dwd_root.resolve()
    ods_root = ods_root.resolve()
    plan_root = plan_root.resolve()
    output_root = output_root.resolve()
    if required_output_ancestor is not None:
        ancestor = required_output_ancestor.resolve()
        if output_root != ancestor and not output_root.is_relative_to(ancestor):
            raise DwdPreRebuildAuditBatchError(
                f"batch output must remain under mandatory audit storage: {ancestor}"
            )
    if any(
        output_root == root or output_root.is_relative_to(root)
        for root in (dwd_root, ods_root, plan_root)
    ):
        raise DwdPreRebuildAuditBatchError("batch output must remain outside sealed packages")

    batch_root = output_root / batch_id
    batch_root.mkdir(parents=True, exist_ok=True)
    state_path = batch_root / "batch-state.json"
    lock_path = batch_root / "batch.lock"
    with lock_path.open("a+", encoding="ascii") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DwdPreRebuildAuditBatchError(
                f"audit batch is already running: {batch_id}"
            ) from exc

        state = _initial_state(
            batch_id=batch_id,
            package_id=package_id,
            code_revision=code_revision,
            selected_steps=selected_steps,
        )
        _atomic_json(state_path, state)
        try:
            for step in AUDIT_STEPS:
                if step.name not in selected_steps:
                    continue
                step_root = batch_root / step.name
                step_root.mkdir(parents=True, exist_ok=True)
                report = evidence.get(step.name, step_root / step.report_name).resolve()
                report_existed = report.exists()
                if step.name in evidence:
                    evidence_mode = "adopted_external"
                elif report_existed:
                    evidence_mode = "resumed_existing"
                else:
                    evidence_mode = "executed"
                state["steps"][step.name] = {
                    "status": "running",
                    "started_at": _utc_now(),
                    "report": str(report),
                    "evidence_mode": evidence_mode,
                }
                _atomic_json(state_path, state)

                if step.name not in evidence and not report_existed:
                    command = _command(
                        step,
                        repository=repository,
                        dwd_root=dwd_root,
                        ods_root=ods_root,
                        plan_root=plan_root,
                        report=report,
                        scratch=step_root / "duckdb-scratch",
                        checkpoint=step_root / "checkpoint.json",
                        memory_limit=memory_limit,
                    )
                    log_path = step_root / "audit.log"
                    with log_path.open("a", encoding="utf-8") as log:
                        completed = subprocess.run(
                            command,
                            cwd=repository,
                            stdout=log,
                            stderr=subprocess.STDOUT,
                            check=False,
                        )
                    state["steps"][step.name]["exit_code"] = completed.returncode
                    state["steps"][step.name]["log"] = str(log_path)
                    state["steps"][step.name]["code_revision"] = code_revision
                    if completed.returncode not in {0, 2}:
                        raise DwdPreRebuildAuditBatchError(
                            f"audit tool exited unexpectedly for {step.name}: "
                            f"{completed.returncode}"
                        )

                classification = classify_report(report, step=step, package_id=package_id)
                state["steps"][step.name].update(classification)
                state["steps"][step.name]["completed_at"] = _utc_now()
                _atomic_json(state_path, state)
        except Exception as exc:
            state["status"] = "failed"
            state["completed_at"] = _utc_now()
            state["failure"] = {"type": type(exc).__name__, "message": str(exc)}
            _atomic_json(state_path, state)
            if isinstance(exc, DwdPreRebuildAuditBatchError):
                raise
            raise DwdPreRebuildAuditBatchError(f"audit batch failed: {exc}") from exc

        statuses = [state["steps"][name]["status"] for name in selected_steps]
        if not all(status in _TERMINAL_STEP_STATUSES for status in statuses):
            raise DwdPreRebuildAuditBatchError("audit batch ended without terminal step evidence")
        state["status"] = (
            "completed_with_findings"
            if "completed_with_findings" in statuses
            else "verified"
        )
        state["completed_at"] = _utc_now()
        _atomic_json(state_path, state)
        return state

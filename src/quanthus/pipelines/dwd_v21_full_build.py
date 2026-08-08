"""Checkpointed full DWD Base V2.1 build for the approved GPU-P100 execution node."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from quanthus.pipelines.dwd_coverage_exclusion_audit import (
    audit_dwd_coverage_exclusions,
    write_coverage_exclusion_report,
)
from quanthus.pipelines.dwd_domain_invariant_audit import (
    audit_dwd_domain_invariants,
    write_domain_invariant_report,
)
from quanthus.pipelines.dwd_lineage_authority_audit import (
    audit_dwd_lineage_authority,
    write_lineage_authority_report,
)
from quanthus.pipelines.dwd_pre_rebuild_audit import (
    audit_dwd_schema_and_partitions,
    write_phase_b_report,
)
from quanthus.pipelines.dwd_semantic_contract_audit import (
    audit_dwd_semantic_contracts,
    write_semantic_contract_report,
)
from quanthus.pipelines.dwd_v21_rebuild_monitor import verify_authority
from quanthus.pipelines.dwd_v21_rebuild_preflight import (
    load_environment,
    run_preflight,
)
from quanthus.pipelines.dwd_v21_rebuild_preflight import (
    write_receipt as write_preflight_receipt,
)
from quanthus.pipelines.dwd_v21_rebuild_runtime import (
    DwdV21RuntimeError,
    _advance,
    _object,
    _verify_sealed,
)
from quanthus.pipelines.securities_warehouse.disclosure_event import (
    audit_disclosure_event_source_package,
    build_disclosure_event,
)
from quanthus.pipelines.securities_warehouse.domain_base import (
    DomainBaseResult,
    seal_domain_base_candidate,
    verify_domain_base,
    verify_domain_candidate,
)
from quanthus.pipelines.securities_warehouse.financial_reporting import (
    audit_financial_native_source_package,
    audit_financial_statement_sources,
    build_financial_reporting,
    combine_financial_reporting_source_audits,
    select_financial_statement_sources,
    write_financial_reporting_source_audit,
)
from quanthus.pipelines.securities_warehouse.industry_classification import (
    build_industry_classification,
)
from quanthus.pipelines.securities_warehouse.listed_company import build_listed_company
from quanthus.pipelines.securities_warehouse.market_observation import build_market_observation
from quanthus.pipelines.securities_warehouse.ownership_capital import (
    audit_ownership_capital_source_package,
    build_ownership_capital,
)
from quanthus.pipelines.securities_warehouse.planner import build_input_plan
from quanthus.pipelines.securities_warehouse.quality_governance import build_quality_governance
from quanthus.pipelines.securities_warehouse.tradability_state import build_tradability_state
from quanthus.pipelines.securities_warehouse.trading_calendar import (
    _mapping as plan_mapping,
)
from quanthus.pipelines.securities_warehouse.trading_calendar import (
    _object as plan_object,
)
from quanthus.pipelines.securities_warehouse.trading_calendar import (
    _sequence as plan_sequence,
)
from quanthus.pipelines.securities_warehouse.trading_calendar import (
    _verify_ods_binding,
    _verify_plan_package,
    build_trading_calendar,
)


class DwdV21FullBuildError(RuntimeError):
    """Raised when the full builder cannot advance without violating its authority."""


_PILOT_APPROVAL = "docs/acceptance/issue-78-dwd-base-v2-1-gpu-p100-pilot-approval-20260808.json"
_CONTRACT = "config/audits/dwd-base-v2-1-gpu-p100-execution.v1.json"
_CONTRACT_APPROVAL = (
    "docs/acceptance/issue-78-dwd-base-v2-1-gpu-p100-execution-approval-20260807.json"
)
_CLOSURE = "docs/acceptance/issue-78-dwd-base-v2-pre-rebuild-audit-closure-20260807"
_PACKAGE_ID = "dwd-base-202607-v2.1"
_CANDIDATE_NAMES = {
    "listed_company": "issue57",
    "trading_calendar": "issue72",
    "security_market_observation": "issue58",
    "tradability_state": "issue59",
    "financial_reporting": "issue61",
    "disclosure_company_event": "issue60-v2",
    "industry_classification": "issue62",
    "ownership_capital": "issue63-v2",
    "governance_sidecar": "issue64",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise DwdV21FullBuildError(f"cannot hash build artifact: {path}: {error}") from error
    return "sha256:" + digest.hexdigest()


def _require_clean_revision(repository: Path, expected_revision: str) -> None:
    revision = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    status = subprocess.run(
        ["git", "-C", str(repository), "status", "--porcelain=v1", "--untracked-files=all"],
        check=False,
        capture_output=True,
        text=True,
    )
    if revision.returncode != 0 or status.returncode != 0:
        raise DwdV21FullBuildError("cannot inspect full-builder repository state")
    if revision.stdout.strip() != expected_revision:
        raise DwdV21FullBuildError("full-builder code revision differs from approval")
    if status.stdout.strip():
        raise DwdV21FullBuildError("full-builder repository checkout is not clean")


def _require_full_build_environment(
    environment_path: Path,
    *,
    expected_revision: str,
    expected_contract_digest: str,
    run_id: str,
) -> dict[str, str]:
    environment = load_environment(environment_path)
    expected = {
        "QUANTHUS_DWD_BUILDER_AUTHORIZED": "1",
        "QUANTHUS_DWD_CODE_REVISION": expected_revision,
        "QUANTHUS_DWD_EXECUTION_CONTRACT_DIGEST": expected_contract_digest,
        "QUANTHUS_DWD_EXECUTION_MODE": "full_build",
        "QUANTHUS_DWD_FULL_BUILD_APPROVED_REVISION": expected_revision,
        "QUANTHUS_DWD_PILOT_AUTHORIZED": "0",
        "QUANTHUS_DWD_RUN_ID": run_id,
    }
    if any(environment.get(key) != value for key, value in expected.items()):
        raise DwdV21FullBuildError("deployment environment differs from full-build authority")
    if environment.get("QUANTHUS_DWD_RESUME_AUTHORIZED", "0") not in {"0", "1"}:
        raise DwdV21FullBuildError("resume authority must be exactly 0 or 1")
    return environment


def _pilot_approval(
    *,
    repository: Path,
    workspace: Path,
    expected_contract_digest: str,
) -> dict[str, Any]:
    approval = _verify_sealed(repository / _PILOT_APPROVAL, "pilot acceptance receipt")
    expected = {
        "schema_version": 1,
        "kind": "dwd_base_v2_1_gpu_p100_pilot_receipt_approval",
        "approved_contract_digest": expected_contract_digest,
    }
    if any(approval.get(key) != value for key, value in expected.items()):
        raise DwdV21FullBuildError("pilot acceptance receipt differs from the execution contract")
    pilot_run_id = approval.get("pilot_run_id")
    pilot_revision = approval.get("approved_pilot_revision")
    replay_digest = approval.get("replay_receipt_digest")
    partition_digest = approval.get("partition_receipt_digest")
    if not all(
        isinstance(value, str) and value
        for value in (pilot_run_id, pilot_revision, replay_digest, partition_digest)
    ):
        raise DwdV21FullBuildError("pilot acceptance receipt lacks exact evidence bindings")
    pilot_root = (workspace / "runs" / str(pilot_run_id)).resolve()
    if not pilot_root.is_relative_to(workspace) or not pilot_root.is_dir():
        raise DwdV21FullBuildError("approved pilot run is outside the build workspace")

    replay_path = pilot_root / "evidence/pilot/replay-determinism.json"
    partition_path = pilot_root / "evidence/pilot/partition-pilot.json"
    replay = _verify_sealed(replay_path, "accepted replay receipt")
    partition = _verify_sealed(partition_path, "accepted partition receipt")
    awaiting = _verify_sealed(
        pilot_root / "state/awaiting-pilot-acceptance.json",
        "accepted pilot phase",
    )
    replay_summary = replay.get("summary")
    replay_input = replay.get("input_plan")
    partition_summary = partition.get("summary")
    awaiting_evidence = awaiting.get("evidence")
    if not all(
        isinstance(value, Mapping)
        for value in (replay_summary, replay_input, partition_summary, awaiting_evidence)
    ):
        raise DwdV21FullBuildError("accepted pilot evidence is malformed")
    replay_summary = cast(Mapping[str, Any], replay_summary)
    replay_input = cast(Mapping[str, Any], replay_input)
    partition_summary = cast(Mapping[str, Any], partition_summary)
    awaiting_evidence = cast(Mapping[str, Any], awaiting_evidence)
    if (
        replay.get("canonical_digest") != replay_digest
        or replay_summary.get("status") != "verified"
        or replay_summary.get("mutation_performed") is not False
        or replay_summary.get("independent_execution_count") != 2
        or replay_input.get("run_id") != pilot_run_id
        or partition.get("canonical_digest") != partition_digest
        or partition.get("status") != "verified"
        or partition.get("run_id") != pilot_run_id
        or partition.get("contract_digest") != expected_contract_digest
        or partition.get("code_revision") != pilot_revision
        or partition_summary.get("limits_verified") is not True
        or partition_summary.get("sequential_execution_verified") is not True
        or partition_summary.get("builder_started") is not False
        or awaiting.get("run_id") != pilot_run_id
        or awaiting.get("contract_digest") != expected_contract_digest
        or awaiting.get("code_revision") != pilot_revision
        or awaiting.get("status") != "paused"
        or awaiting_evidence.get("replay_report_digest") != replay_digest
        or awaiting_evidence.get("partition_report_digest") != partition_digest
    ):
        raise DwdV21FullBuildError("approved pilot evidence failed exact receipt verification")
    return {
        "approval_receipt": str((repository / _PILOT_APPROVAL).resolve()),
        "approval_receipt_digest": approval["canonical_digest"],
        "pilot_run_id": pilot_run_id,
        "pilot_revision": pilot_revision,
        "replay_receipt": str(replay_path),
        "replay_receipt_digest": replay_digest,
        "partition_receipt": str(partition_path),
        "partition_receipt_digest": partition_digest,
    }


def _file_evidence(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise DwdV21FullBuildError(f"checkpoint artifact is not a physical file: {path}")
    return {
        "path": str(path.resolve()),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }


def _candidate_evidence(
    path: Path,
    *,
    domain: str,
    run_id: str,
    plan_digest: str,
) -> dict[str, Any]:
    verified = verify_domain_candidate(
        path,
        domain=domain,
        run_id=run_id,
        input_plan_digest=plan_digest,
    )
    return {
        "root": str(verified.root),
        "manifest_sha256": verified.manifest_sha256,
        "semantic_digest": verified.semantic_digest,
        "row_counts": dict(sorted(verified.row_counts.items())),
        "payload_file_count": verified.payload_file_count,
    }


def _run_file_step(
    *,
    workspace: Path,
    run_root: Path,
    phase: str,
    output: Path,
    action: Callable[[], None],
    run_id: str,
    contract_digest: str,
    code_revision: str,
) -> dict[str, Any]:
    passed_phase = f"{phase}-passed"
    if (run_root / "state" / f"{passed_phase}.json").exists():
        evidence = _file_evidence(output)
        _advance(
            workspace,
            run_root,
            phase=passed_phase,
            run_id=run_id,
            contract_digest=contract_digest,
            code_revision=code_revision,
            status="passed",
            evidence=evidence,
        )
        return evidence
    _advance(
        workspace,
        run_root,
        phase=f"{phase}-running",
        run_id=run_id,
        contract_digest=contract_digest,
        code_revision=code_revision,
        status="running",
        evidence={"output": str(output)},
    )
    action()
    evidence = _file_evidence(output)
    _advance(
        workspace,
        run_root,
        phase=passed_phase,
        run_id=run_id,
        contract_digest=contract_digest,
        code_revision=code_revision,
        status="passed",
        evidence=evidence,
    )
    return evidence


def _run_domain_step(
    *,
    workspace: Path,
    run_root: Path,
    phase: str,
    domain: str,
    output: Path,
    action: Callable[[], None],
    run_id: str,
    plan_digest: str,
    contract_digest: str,
    code_revision: str,
) -> dict[str, Any]:
    passed_phase = f"{phase}-passed"
    if (run_root / "state" / f"{passed_phase}.json").exists():
        evidence = _candidate_evidence(
            output,
            domain=domain,
            run_id=run_id,
            plan_digest=plan_digest,
        )
        _advance(
            workspace,
            run_root,
            phase=passed_phase,
            run_id=run_id,
            contract_digest=contract_digest,
            code_revision=code_revision,
            status="passed",
            evidence=evidence,
        )
        return evidence
    _advance(
        workspace,
        run_root,
        phase=f"{phase}-running",
        run_id=run_id,
        contract_digest=contract_digest,
        code_revision=code_revision,
        status="running",
        evidence={"candidate": str(output), "domain": domain},
    )
    action()
    evidence = _candidate_evidence(
        output,
        domain=domain,
        run_id=run_id,
        plan_digest=plan_digest,
    )
    _advance(
        workspace,
        run_root,
        phase=passed_phase,
        run_id=run_id,
        contract_digest=contract_digest,
        code_revision=code_revision,
        status="passed",
        evidence=evidence,
    )
    return evidence


def _report_evidence(
    path: Path,
    *,
    label: str,
    status_path: tuple[str, ...],
) -> dict[str, Any]:
    report = _verify_sealed(path, label)
    status: object = report
    for key in status_path:
        if not isinstance(status, Mapping):
            raise DwdV21FullBuildError(f"{label} has no status path: {status_path}")
        status = status.get(key)
    if status != "verified":
        raise DwdV21FullBuildError(f"{label} did not verify: status={status!r}")
    return {
        **_file_evidence(path),
        "canonical_digest": report["canonical_digest"],
        "status": status,
    }


def _run_report_step(
    *,
    workspace: Path,
    run_root: Path,
    phase: str,
    output: Path,
    action: Callable[[], None],
    label: str,
    status_path: tuple[str, ...],
    run_id: str,
    contract_digest: str,
    code_revision: str,
) -> dict[str, Any]:
    passed_phase = f"{phase}-passed"
    if (run_root / "state" / f"{passed_phase}.json").exists() or output.exists():
        evidence = _report_evidence(output, label=label, status_path=status_path)
        _advance(
            workspace,
            run_root,
            phase=passed_phase,
            run_id=run_id,
            contract_digest=contract_digest,
            code_revision=code_revision,
            status="passed",
            evidence=evidence,
        )
        return evidence
    _advance(
        workspace,
        run_root,
        phase=f"{phase}-running",
        run_id=run_id,
        contract_digest=contract_digest,
        code_revision=code_revision,
        status="running",
        evidence={"output": str(output)},
    )
    action()
    evidence = _report_evidence(output, label=label, status_path=status_path)
    _advance(
        workspace,
        run_root,
        phase=passed_phase,
        run_id=run_id,
        contract_digest=contract_digest,
        code_revision=code_revision,
        status="passed",
        evidence=evidence,
    )
    return evidence


def _statement_audit(
    *,
    ods_base: Path,
    plan_root: Path,
    model_path: Path,
    output: Path,
    run_id: str,
    batch_size: int,
) -> None:
    plan, ledger = _verify_plan_package(plan_root)
    semantic_plan = plan_mapping(plan.get("plan"), "input plan")
    model = plan_object(model_path)
    _verify_ods_binding(ods_base, semantic_plan, model)
    if plan.get("run_id") != run_id or semantic_plan.get("mode") != "base":
        raise DwdV21FullBuildError("financial statement audit differs from the full-build plan")
    records = [
        plan_mapping(raw, "source ledger record")
        for raw in plan_sequence(ledger.get("records"), "source ledger records")
    ]
    sources = select_financial_statement_sources(records, ods_base)
    audit = audit_financial_statement_sources(sources, batch_size=batch_size)
    write_financial_reporting_source_audit(output, audit)


def _configure_scratch(run_root: Path, thread_limit: int) -> Path:
    scratch = run_root / "scratch/full-build"
    scratch.mkdir(parents=True, exist_ok=True)
    for key in ("TMPDIR", "TMP", "TEMP", "ARROW_TMPDIR", "DUCKDB_TEMP_DIRECTORY"):
        os.environ[key] = str(scratch)
    for key in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "POLARS_MAX_THREADS",
        "ARROW_NUM_THREADS",
    ):
        os.environ[key] = str(thread_limit)
    return scratch


def _seal_evidence(result: DomainBaseResult) -> dict[str, Any]:
    return {
        "root": str(result.root),
        "package_id": result.package_id,
        "manifest_sha256": result.manifest_sha256,
        "content_digest": result.content_digest,
        "table_count": result.table_count,
        "row_count": result.row_count,
        "payload_bytes": result.payload_bytes,
        "payload_file_count": result.payload_file_count,
        "current_pointer_written": False,
    }


def _run_candidate_audits(
    *,
    repository: Path,
    workspace: Path,
    run_root: Path,
    sealed_root: Path,
    ods_base: Path,
    plan_root: Path,
    model_path: Path,
    model_extension_path: Path,
    scratch: Path,
    run_id: str,
    contract_digest: str,
    code_revision: str,
) -> dict[str, dict[str, Any]]:
    audit_root = run_root / "evidence/candidate-audits"
    checkpoint_root = run_root / "checkpoints/candidate-audits"
    audit_scratch = scratch / "candidate-audits"
    audit_root.mkdir(parents=True, exist_ok=True)
    checkpoint_root.mkdir(parents=True, exist_ok=True)
    audit_scratch.mkdir(parents=True, exist_ok=True)

    schema_path = audit_root / "schema-partitions.json"

    def schema_action() -> None:
        report = audit_dwd_schema_and_partitions(
            root=sealed_root,
            model_path=model_path,
            extension_path=model_extension_path,
        )
        write_phase_b_report(schema_path, report, sealed_roots=(sealed_root,))

    schema = _run_report_step(
        workspace=workspace,
        run_root=run_root,
        phase="11a-schema-partition-audit",
        output=schema_path,
        action=schema_action,
        label="candidate schema and partition audit",
        status_path=("summary", "status"),
        run_id=run_id,
        contract_digest=contract_digest,
        code_revision=code_revision,
    )

    semantic_path = audit_root / "semantic-contracts.json"

    def semantic_action() -> None:
        report = audit_dwd_semantic_contracts(
            root=sealed_root,
            model_path=model_path,
            scratch=audit_scratch / "semantic-contracts",
            checkpoint_path=checkpoint_root / "semantic-contracts.json",
            memory_limit="2GB",
        )
        write_semantic_contract_report(
            semantic_path,
            report,
            sealed_roots=(sealed_root,),
        )

    semantic = _run_report_step(
        workspace=workspace,
        run_root=run_root,
        phase="11b-semantic-contract-audit",
        output=semantic_path,
        action=semantic_action,
        label="candidate semantic contract audit",
        status_path=("summary", "status"),
        run_id=run_id,
        contract_digest=contract_digest,
        code_revision=code_revision,
    )

    invariant_path = audit_root / "domain-invariants.json"

    def invariant_action() -> None:
        report = audit_dwd_domain_invariants(
            root=sealed_root,
            scratch=audit_scratch / "domain-invariants",
            checkpoint_path=checkpoint_root / "domain-invariants.json",
            memory_limit="2GB",
        )
        write_domain_invariant_report(
            invariant_path,
            report,
            sealed_roots=(sealed_root,),
        )

    invariant = _run_report_step(
        workspace=workspace,
        run_root=run_root,
        phase="11c-domain-invariant-audit",
        output=invariant_path,
        action=invariant_action,
        label="candidate domain invariant audit",
        status_path=("summary", "status"),
        run_id=run_id,
        contract_digest=contract_digest,
        code_revision=code_revision,
    )

    coverage_path = audit_root / "coverage-exclusions.json"

    def coverage_action() -> None:
        report = audit_dwd_coverage_exclusions(
            root=sealed_root,
            plan_root=plan_root,
            model_path=model_path,
            schema_report=schema_path,
        )
        write_coverage_exclusion_report(
            coverage_path,
            report,
            sealed_roots=(sealed_root, plan_root),
        )

    coverage = _run_report_step(
        workspace=workspace,
        run_root=run_root,
        phase="11d-coverage-exclusion-audit",
        output=coverage_path,
        action=coverage_action,
        label="candidate coverage and exclusion audit",
        status_path=("summary", "status"),
        run_id=run_id,
        contract_digest=contract_digest,
        code_revision=code_revision,
    )

    lineage_path = audit_root / "lineage-authority.json"

    def lineage_action() -> None:
        report = audit_dwd_lineage_authority(
            dwd_root=sealed_root,
            ods_root=ods_base,
            plan_root=plan_root,
            repository=repository,
            model_path=model_path,
        )
        write_lineage_authority_report(
            lineage_path,
            report,
            sealed_roots=(sealed_root, ods_base),
        )

    lineage = _run_report_step(
        workspace=workspace,
        run_root=run_root,
        phase="11e-lineage-authority-audit",
        output=lineage_path,
        action=lineage_action,
        label="candidate lineage authority audit",
        status_path=("status",),
        run_id=run_id,
        contract_digest=contract_digest,
        code_revision=code_revision,
    )
    return {
        "schema_partitions": schema,
        "semantic_contracts": semantic,
        "domain_invariants": invariant,
        "coverage_exclusions": coverage,
        "lineage_authority": lineage,
    }


def run_full_build(
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
    resume: bool,
    thread_limit: int = 4,
    statement_audit_batch_size: int = 4,
    financial_source_batch_size: int = 32,
    financial_native_batch_size: int = 4096,
    disclosure_batch_size: int = 50000,
    ownership_batch_size: int = 8192,
) -> dict[str, Any]:
    """Build, assemble, and deep-verify one pointerless DWD Base V2.1 candidate."""

    repository = repository.resolve()
    workspace = workspace.resolve()
    ods_base = ods_base.resolve()
    dwd_base = dwd_base.resolve()
    environment_path = environment_path.resolve()
    run_root = workspace / "runs" / run_id
    if not run_root.resolve().is_relative_to(workspace):
        raise DwdV21FullBuildError("full-build run path escapes the approved workspace")
    if (
        thread_limit < 1
        or min(
            statement_audit_batch_size,
            financial_source_batch_size,
            financial_native_batch_size,
            disclosure_batch_size,
            ownership_batch_size,
        )
        < 1
    ):
        raise DwdV21FullBuildError("full-build resource or batch limits are invalid")
    if expected_revision[:8] not in run_id:
        raise DwdV21FullBuildError("full-build run id is not bound to the approved revision")

    environment = _require_full_build_environment(
        environment_path,
        expected_revision=expected_revision,
        expected_contract_digest=expected_contract_digest,
        run_id=run_id,
    )
    existed = run_root.exists()
    if existed and (not resume or environment.get("QUANTHUS_DWD_RESUME_AUTHORIZED", "0") != "1"):
        raise DwdV21FullBuildError("existing full-build run requires explicit resume authority")
    _require_clean_revision(repository, expected_revision)
    contract_path = repository / _CONTRACT
    contract_approval_path = repository / _CONTRACT_APPROVAL
    verify_authority(contract_path, contract_approval_path, expected_contract_digest)
    accepted_pilot = _pilot_approval(
        repository=repository,
        workspace=workspace,
        expected_contract_digest=expected_contract_digest,
    )
    if accepted_pilot["pilot_run_id"] == run_id:
        raise DwdV21FullBuildError("full-build run must be distinct from the accepted pilot run")

    run_root.mkdir(parents=True, exist_ok=True)
    _advance(
        workspace,
        run_root,
        phase="full-build-bootstrap",
        run_id=run_id,
        contract_digest=expected_contract_digest,
        code_revision=expected_revision,
        status="running",
        evidence={"runtime": "dwd_v21_full_build"},
    )
    closure = repository / _CLOSURE
    try:
        preflight = run_preflight(
            repository=repository,
            workspace=workspace,
            ods_base=ods_base,
            dwd_base=dwd_base,
            contract_path=contract_path,
            approval_path=contract_approval_path,
            findings_path=closure / "findings-ledger.json",
            remediation_path=closure / "v2.1-remediation-scope.json",
            environment_path=environment_path,
            expected_revision=expected_revision,
            expected_contract_digest=expected_contract_digest,
            expected_findings_digest=expected_findings_digest,
            expected_remediation_digest=expected_remediation_digest,
            minimum_free_bytes=minimum_free_bytes,
            expected_execution_mode="full_build",
        )
        preflight_path = write_preflight_receipt(
            run_root / "evidence/preflight-full-build", preflight
        )
        if not (run_root / "state/full-build-preflight-passed.json").exists():
            _advance(
                workspace,
                run_root,
                phase="full-build-preflight-passed",
                run_id=run_id,
                contract_digest=expected_contract_digest,
                code_revision=expected_revision,
                status="passed",
                evidence={
                    "receipt": str(preflight_path),
                    "receipt_digest": _object(preflight_path, "full-build preflight").get(
                        "canonical_digest"
                    ),
                },
            )

        lock_path = workspace / "control/locks/dwd-base-v2-1-rebuild.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o640)
        try:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise DwdV21FullBuildError("GPU rebuild lock is already held") from error

            _advance(
                workspace,
                run_root,
                phase="pilot-acceptance-verified",
                run_id=run_id,
                contract_digest=expected_contract_digest,
                code_revision=expected_revision,
                status="passed",
                evidence=accepted_pilot,
            )
            scratch = _configure_scratch(run_root, thread_limit)
            market_scratch = scratch / "market-observation"
            tradability_scratch = scratch / "tradability-state"
            market_scratch.mkdir(parents=True, exist_ok=True)
            tradability_scratch.mkdir(parents=True, exist_ok=True)
            plan_root = run_root / "plan"
            _advance(
                workspace,
                run_root,
                phase="full-build-planning-running",
                run_id=run_id,
                contract_digest=expected_contract_digest,
                code_revision=expected_revision,
                status="running",
            )
            model_path = repository / "config/pipelines/dwd-domain-model.v1.json"
            model_extension_path = (
                repository / "config/pipelines/dwd-domain-model.v1.1-extension.json"
            )
            plan = build_input_plan(
                ods_package=ods_base,
                model_path=model_path,
                output=plan_root,
                mode="base",
                run_id=run_id,
                knowledge_cutoff=knowledge_cutoff,
                code_revision=expected_revision,
                runtime_id=runtime_id,
                through_date=through_date,
                capacity_root=workspace,
                input_identity_override=(
                    repository / "config/pipelines/dwd-v2-1-input-identity.v1.json"
                ),
            )
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
                phase="full-build-planning-passed",
                run_id=run_id,
                contract_digest=expected_contract_digest,
                code_revision=expected_revision,
                status="passed",
                evidence=plan_evidence,
            )
            if not plan.capacity_sufficient:
                raise DwdV21FullBuildError("sealed input plan reports insufficient capacity")

            candidate_root = run_root / "candidates"
            candidates = {
                domain: candidate_root / f"{run_id}-{suffix}"
                for domain, suffix in _CANDIDATE_NAMES.items()
            }
            audit_root = run_root / "evidence/source-audits"
            listed = candidates["listed_company"]
            calendar = candidates["trading_calendar"]
            market = candidates["security_market_observation"]
            tradability = candidates["tradability_state"]
            financial = candidates["financial_reporting"]
            disclosure = candidates["disclosure_company_event"]
            industry = candidates["industry_classification"]
            ownership = candidates["ownership_capital"]
            quality = candidates["governance_sidecar"]

            _run_domain_step(
                workspace=workspace,
                run_root=run_root,
                phase="01-listed-company",
                domain="listed_company",
                output=listed,
                action=lambda: cast(
                    None,
                    build_listed_company(
                        ods_package=ods_base,
                        input_plan=plan_root,
                        model_path=model_path,
                        reference_seed_path=(
                            repository
                            / "config/pipelines/securities-warehouse-reference-seeds.v1.json"
                        ),
                        code_validity_path=repository / "config/security-code-validity.v1.json",
                        output=listed,
                        run_id=run_id,
                    ),
                ),
                run_id=run_id,
                plan_digest=plan.plan_digest,
                contract_digest=expected_contract_digest,
                code_revision=expected_revision,
            )
            _run_domain_step(
                workspace=workspace,
                run_root=run_root,
                phase="02-trading-calendar",
                domain="trading_calendar",
                output=calendar,
                action=lambda: cast(
                    None,
                    build_trading_calendar(
                        ods_package=ods_base,
                        input_plan=plan_root,
                        model_path=model_path,
                        upstream_candidate=listed,
                        output=calendar,
                        run_id=run_id,
                    ),
                ),
                run_id=run_id,
                plan_digest=plan.plan_digest,
                contract_digest=expected_contract_digest,
                code_revision=expected_revision,
            )
            _run_domain_step(
                workspace=workspace,
                run_root=run_root,
                phase="03-market-observation",
                domain="security_market_observation",
                output=market,
                action=lambda: cast(
                    None,
                    build_market_observation(
                        ods_package=ods_base,
                        input_plan=plan_root,
                        model_path=model_path,
                        listed_company_candidate=listed,
                        trading_calendar_candidate=calendar,
                        output=market,
                        run_id=run_id,
                        scratch_root=market_scratch,
                    ),
                ),
                run_id=run_id,
                plan_digest=plan.plan_digest,
                contract_digest=expected_contract_digest,
                code_revision=expected_revision,
            )
            _run_domain_step(
                workspace=workspace,
                run_root=run_root,
                phase="04-tradability-state",
                domain="tradability_state",
                output=tradability,
                action=lambda: cast(
                    None,
                    build_tradability_state(
                        ods_package=ods_base,
                        input_plan=plan_root,
                        model_path=model_path,
                        listed_company_candidate=listed,
                        trading_calendar_candidate=calendar,
                        market_observation_candidate=market,
                        output=tradability,
                        run_id=run_id,
                        scratch_root=tradability_scratch,
                    ),
                ),
                run_id=run_id,
                plan_digest=plan.plan_digest,
                contract_digest=expected_contract_digest,
                code_revision=expected_revision,
            )

            statement_audit = audit_root / "financial-statement.json"
            native_audit = audit_root / "financial-native.json"
            combined_audit = audit_root / "financial-combined.json"
            _run_file_step(
                workspace=workspace,
                run_root=run_root,
                phase="05a-financial-statement-audit",
                output=statement_audit,
                action=lambda: _statement_audit(
                    ods_base=ods_base,
                    plan_root=plan_root,
                    model_path=model_path,
                    output=statement_audit,
                    run_id=run_id,
                    batch_size=statement_audit_batch_size,
                ),
                run_id=run_id,
                contract_digest=expected_contract_digest,
                code_revision=expected_revision,
            )
            _run_file_step(
                workspace=workspace,
                run_root=run_root,
                phase="05b-financial-native-audit",
                output=native_audit,
                action=lambda: cast(
                    None,
                    audit_financial_native_source_package(
                        ods_package=ods_base,
                        input_plan=plan_root,
                        model_path=model_path,
                        listed_company_candidate=listed,
                        output=native_audit,
                        run_id=run_id,
                        batch_size=financial_native_batch_size,
                    ),
                ),
                run_id=run_id,
                contract_digest=expected_contract_digest,
                code_revision=expected_revision,
            )
            _run_file_step(
                workspace=workspace,
                run_root=run_root,
                phase="05c-financial-combined-audit",
                output=combined_audit,
                action=lambda: cast(
                    None,
                    combine_financial_reporting_source_audits(
                        statement_audit_path=statement_audit,
                        native_audit_path=native_audit,
                        output=combined_audit,
                    ),
                ),
                run_id=run_id,
                contract_digest=expected_contract_digest,
                code_revision=expected_revision,
            )
            _run_domain_step(
                workspace=workspace,
                run_root=run_root,
                phase="05-financial-reporting",
                domain="financial_reporting",
                output=financial,
                action=lambda: cast(
                    None,
                    build_financial_reporting(
                        ods_package=ods_base,
                        input_plan=plan_root,
                        model_path=model_path,
                        listed_company_candidate=listed,
                        source_audit_path=combined_audit,
                        output=financial,
                        run_id=run_id,
                        source_batch_size=financial_source_batch_size,
                        native_batch_size=financial_native_batch_size,
                    ),
                ),
                run_id=run_id,
                plan_digest=plan.plan_digest,
                contract_digest=expected_contract_digest,
                code_revision=expected_revision,
            )

            disclosure_audit = audit_root / "disclosure.json"
            _run_file_step(
                workspace=workspace,
                run_root=run_root,
                phase="06a-disclosure-audit",
                output=disclosure_audit,
                action=lambda: cast(
                    None,
                    audit_disclosure_event_source_package(
                        ods_package=ods_base,
                        input_plan=plan_root,
                        model_path=model_path,
                        listed_company_candidate=listed,
                        financial_reporting_candidate=financial,
                        vocabulary_path=(
                            repository / "config/pipelines/disclosure-event-vocabulary.v1.json"
                        ),
                        output=disclosure_audit,
                        run_id=run_id,
                    ),
                ),
                run_id=run_id,
                contract_digest=expected_contract_digest,
                code_revision=expected_revision,
            )
            _run_domain_step(
                workspace=workspace,
                run_root=run_root,
                phase="06-disclosure-event",
                domain="disclosure_company_event",
                output=disclosure,
                action=lambda: cast(
                    None,
                    build_disclosure_event(
                        ods_package=ods_base,
                        input_plan=plan_root,
                        model_path=model_path,
                        listed_company_candidate=listed,
                        financial_reporting_candidate=financial,
                        vocabulary_path=(
                            repository / "config/pipelines/disclosure-event-vocabulary.v1.json"
                        ),
                        source_audit_path=disclosure_audit,
                        output=disclosure,
                        run_id=run_id,
                        announcement_batch_size=disclosure_batch_size,
                    ),
                ),
                run_id=run_id,
                plan_digest=plan.plan_digest,
                contract_digest=expected_contract_digest,
                code_revision=expected_revision,
            )
            _run_domain_step(
                workspace=workspace,
                run_root=run_root,
                phase="07-industry-classification",
                domain="industry_classification",
                output=industry,
                action=lambda: cast(
                    None,
                    build_industry_classification(
                        ods_package=ods_base,
                        input_plan=plan_root,
                        model_path=model_path,
                        listed_company_candidate=listed,
                        hierarchy_reference=(
                            repository / "config/pipelines/csrc-industry-hierarchy.v1.json"
                        ),
                        output=industry,
                        run_id=run_id,
                    ),
                ),
                run_id=run_id,
                plan_digest=plan.plan_digest,
                contract_digest=expected_contract_digest,
                code_revision=expected_revision,
            )

            ownership_audit = audit_root / "ownership-capital.json"
            _run_file_step(
                workspace=workspace,
                run_root=run_root,
                phase="08a-ownership-audit",
                output=ownership_audit,
                action=lambda: cast(
                    None,
                    audit_ownership_capital_source_package(
                        ods_package=ods_base,
                        input_plan=plan_root,
                        model_path=model_path,
                        model_extension_path=model_extension_path,
                        listed_company_candidate=listed,
                        output=ownership_audit,
                        run_id=run_id,
                    ),
                ),
                run_id=run_id,
                contract_digest=expected_contract_digest,
                code_revision=expected_revision,
            )
            _run_domain_step(
                workspace=workspace,
                run_root=run_root,
                phase="08-ownership-capital",
                domain="ownership_capital",
                output=ownership,
                action=lambda: cast(
                    None,
                    build_ownership_capital(
                        ods_package=ods_base,
                        input_plan=plan_root,
                        model_path=model_path,
                        model_extension_path=model_extension_path,
                        listed_company_candidate=listed,
                        disclosure_event_candidate=disclosure,
                        source_audit_path=ownership_audit,
                        output=ownership,
                        run_id=run_id,
                        batch_size=ownership_batch_size,
                    ),
                ),
                run_id=run_id,
                plan_digest=plan.plan_digest,
                contract_digest=expected_contract_digest,
                code_revision=expected_revision,
            )

            quality_inputs = {
                key: value for key, value in candidates.items() if key != "governance_sidecar"
            }
            _run_domain_step(
                workspace=workspace,
                run_root=run_root,
                phase="09-quality-governance",
                domain="governance_sidecar",
                output=quality,
                action=lambda: cast(
                    None,
                    build_quality_governance(
                        ods_package=ods_base,
                        input_plan=plan_root,
                        model_path=model_path,
                        model_extension_path=model_extension_path,
                        accounting_contract_path=(
                            repository / "config/pipelines/dwd-domain-accounting-receipt.v1.json"
                        ),
                        candidates=quality_inputs,
                        output=quality,
                        run_id=run_id,
                    ),
                ),
                run_id=run_id,
                plan_digest=plan.plan_digest,
                contract_digest=expected_contract_digest,
                code_revision=expected_revision,
            )

            sealed_root = run_root / "export" / _PACKAGE_ID
            pointer_path = sealed_root.parent / "BASE_CURRENT.json"
            if pointer_path.exists() or pointer_path.is_symlink():
                raise DwdV21FullBuildError("GPU export contains a forbidden CURRENT pointer")
            _advance(
                workspace,
                run_root,
                phase="10-candidate-sealing-running",
                run_id=run_id,
                contract_digest=expected_contract_digest,
                code_revision=expected_revision,
                status="running",
                evidence={"output": str(sealed_root), "current_pointer_written": False},
            )
            ods_identity = cast(Mapping[str, Any], preflight["ods_base"])
            sealed = seal_domain_base_candidate(
                candidates=candidates,
                output=sealed_root,
                package_id=_PACKAGE_ID,
                run_id=run_id,
                input_plan_digest=plan.plan_digest,
                ods_base={
                    "package_id": str(ods_identity["bundle_id"]),
                    "manifest_sha256": str(ods_identity["manifest_sha256"]),
                    "content_digest": str(ods_identity["content_digest"]),
                },
                model_sha256=_sha256(model_path),
                model_extension_sha256=_sha256(model_extension_path),
                code_revision=expected_revision,
            )
            sealed = verify_domain_base(sealed_root)
            if pointer_path.exists() or pointer_path.is_symlink():
                raise DwdV21FullBuildError("candidate sealing created a forbidden CURRENT pointer")
            seal_evidence = _seal_evidence(sealed)
            _advance(
                workspace,
                run_root,
                phase="10-candidate-sealed",
                run_id=run_id,
                contract_digest=expected_contract_digest,
                code_revision=expected_revision,
                status="passed",
                evidence=seal_evidence,
            )
            candidate_audits = _run_candidate_audits(
                repository=repository,
                workspace=workspace,
                run_root=run_root,
                sealed_root=sealed_root,
                ods_base=ods_base,
                plan_root=plan_root,
                model_path=model_path,
                model_extension_path=model_extension_path,
                scratch=scratch,
                run_id=run_id,
                contract_digest=expected_contract_digest,
                code_revision=expected_revision,
            )
            candidate_audit_digests = {
                name: evidence["canonical_digest"]
                for name, evidence in sorted(candidate_audits.items())
            }
            _advance(
                workspace,
                run_root,
                phase="awaiting-candidate-approval",
                run_id=run_id,
                contract_digest=expected_contract_digest,
                code_revision=expected_revision,
                status="paused",
                evidence={
                    **seal_evidence,
                    "candidate_audit_digests": candidate_audit_digests,
                    "install_started": False,
                    "pointer_cutover_started": False,
                    "required_next_authority": "exact_sealed_candidate_approval",
                },
            )
            return {
                "run_id": run_id,
                "phase": "awaiting-candidate-approval",
                "status": "paused",
                **seal_evidence,
                "candidate_audit_digests": candidate_audit_digests,
            }
        finally:
            os.close(lock_fd)
    except (OSError, RuntimeError) as error:
        stamp = datetime.now(timezone.utc).strftime("full-build-failed-%Y%m%dT%H%M%SZ")
        with contextlib.suppress(DwdV21RuntimeError, OSError):
            _advance(
                workspace,
                run_root,
                phase=stamp,
                run_id=run_id,
                contract_digest=expected_contract_digest,
                code_revision=expected_revision,
                status="failed",
                evidence={
                    "error": str(error),
                    "install_started": False,
                    "pointer_cutover_started": False,
                },
            )
        if isinstance(error, DwdV21FullBuildError):
            raise
        raise DwdV21FullBuildError(str(error)) from error


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
    parser.add_argument("--thread-limit", type=int, default=4)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    run_root = args.workspace.resolve() / "runs" / args.run_id
    if run_root.exists() and not args.resume:
        print(f"DWD V2.1 full build failed: run already exists: {run_root}", file=sys.stderr)
        return 2
    try:
        result = run_full_build(
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
            resume=args.resume,
            thread_limit=args.thread_limit,
        )
    except (DwdV21FullBuildError, KeyError, TypeError, ValueError) as error:
        print(f"DWD V2.1 full build failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

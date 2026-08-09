from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from quanthus.pipelines.dwd_v21_full_build import (
    DwdV21FullBuildError,
    DwdV21FullBuildInterrupted,
    _audit_full_build_contract,
    _pilot_approval,
    _require_full_build_environment,
    _run_file_step,
    _run_report_step,
    run_full_build,
)
from quanthus.pipelines.dwd_v21_rebuild_runtime import (
    DwdV21RuntimeError,
    _atomic_json,
    _sealed,
)
from quanthus.pipelines.securities_warehouse.domain_base import _EXPECTED_KINDS

CONTRACT = "sha256:" + "1" * 64
REVISION = "2" * 40
PILOT_REVISION = "3" * 40
PILOT_RUN = "pilot-run-r33333333"
ROOT = Path(__file__).resolve().parents[2]


def test_gpu_full_build_cutoff_covers_all_frozen_input_authority() -> None:
    environment = {
        key: value
        for line in (ROOT / "ops/systemd/gpu-p100-dwd-full-build.env.example")
        .read_text(encoding="ascii")
        .splitlines()
        if line and not line.startswith("#")
        for key, value in [line.split("=", 1)]
    }
    seed = json.loads(
        (ROOT / "config/pipelines/securities-warehouse-reference-seeds.v1.json").read_text(
            encoding="ascii"
        )
    )
    input_identity = json.loads(
        (ROOT / "config/pipelines/dwd-v2-1-input-identity.v1.json").read_text(encoding="ascii")
    )
    cutoff = datetime.fromisoformat(environment["QUANTHUS_DWD_KNOWLEDGE_CUTOFF"])

    assert cutoff >= datetime.fromisoformat(seed["source_snapshot"]["retrieved_at"])
    assert cutoff >= datetime.fromisoformat(input_identity["minimum_knowledge_cutoff"])


def test_static_full_build_contract_is_audited_before_domains() -> None:
    input_identity = json.loads(
        (ROOT / "config/pipelines/dwd-v2-1-input-identity.v1.json").read_text(encoding="ascii")
    )["build_input_contract"]
    evidence = _audit_full_build_contract(
        repository=ROOT,
        preflight={"ods_base": input_identity},
        knowledge_cutoff="2026-08-03T23:59:00+00:00",
        through_date="2026-07-31",
    )
    assert evidence["status"] == "passed"
    assert len(evidence["static_inputs"]) == 7

    with pytest.raises(DwdV21FullBuildError, match="frozen ODS identity authority"):
        _audit_full_build_contract(
            repository=ROOT,
            preflight={"ods_base": input_identity},
            knowledge_cutoff="2026-08-03T23:58:59+00:00",
            through_date="2026-07-31",
        )


def test_full_build_environment_requires_distinct_revision_approval(tmp_path: Path) -> None:
    environment = tmp_path / "full.env"
    environment.write_text(
        "\n".join(
            [
                "QUANTHUS_DWD_BUILDER_AUTHORIZED=1",
                f"QUANTHUS_DWD_CODE_REVISION={REVISION}",
                f"QUANTHUS_DWD_EXECUTION_CONTRACT_DIGEST={CONTRACT}",
                "QUANTHUS_DWD_EXECUTION_MODE=full_build",
                f"QUANTHUS_DWD_FULL_BUILD_APPROVED_REVISION={REVISION}",
                "QUANTHUS_DWD_PILOT_AUTHORIZED=0",
                "QUANTHUS_DWD_RUN_ID=build-r22222222",
                "QUANTHUS_DWD_RESUME_AUTHORIZED=0",
                "QUANTHUS_DWD_KNOWLEDGE_CUTOFF=2026-08-03T23:59:00+00:00",
                "QUANTHUS_DWD_THROUGH_DATE=2026-07-31",
                "QUANTHUS_DWD_RUNTIME_ID=runtime-1",
                "QUANTHUS_DWD_MINIMUM_FREE_BYTES=1",
            ]
        )
        + "\n",
        encoding="ascii",
    )
    parsed = _require_full_build_environment(
        environment,
        expected_revision=REVISION,
        expected_contract_digest=CONTRACT,
        run_id="build-r22222222",
        knowledge_cutoff="2026-08-03T23:59:00+00:00",
        through_date="2026-07-31",
        runtime_id="runtime-1",
        minimum_free_bytes=1,
    )
    assert parsed["QUANTHUS_DWD_BUILDER_AUTHORIZED"] == "1"

    environment.write_text(
        environment.read_text(encoding="ascii").replace(
            f"QUANTHUS_DWD_FULL_BUILD_APPROVED_REVISION={REVISION}",
            f"QUANTHUS_DWD_FULL_BUILD_APPROVED_REVISION={PILOT_REVISION}",
        ),
        encoding="ascii",
    )
    with pytest.raises(DwdV21FullBuildError, match="full-build authority"):
        _require_full_build_environment(
            environment,
            expected_revision=REVISION,
            expected_contract_digest=CONTRACT,
            run_id="build-r22222222",
            knowledge_cutoff="2026-08-03T23:59:00+00:00",
            through_date="2026-07-31",
            runtime_id="runtime-1",
            minimum_free_bytes=1,
        )


def _pilot_fixture(repository: Path, workspace: Path) -> tuple[str, str]:
    replay = _sealed(
        {
            "kind": "dwd_base_v2_replay_determinism_audit",
            "input_plan": {"run_id": PILOT_RUN},
            "summary": {
                "status": "verified",
                "mutation_performed": False,
                "independent_execution_count": 2,
            },
        }
    )
    partition = _sealed(
        {
            "status": "verified",
            "run_id": PILOT_RUN,
            "contract_digest": CONTRACT,
            "code_revision": PILOT_REVISION,
            "summary": {
                "limits_verified": True,
                "sequential_execution_verified": True,
                "builder_started": False,
            },
        }
    )
    replay_digest = str(replay["canonical_digest"])
    partition_digest = str(partition["canonical_digest"])
    approval_path = (
        repository / "docs/acceptance/issue-78-dwd-base-v2-1-gpu-p100-pilot-approval-20260808.json"
    )
    _atomic_json(
        approval_path,
        _sealed(
            {
                "schema_version": 1,
                "kind": "dwd_base_v2_1_gpu_p100_pilot_receipt_approval",
                "approved_contract_digest": CONTRACT,
                "approved_pilot_revision": PILOT_REVISION,
                "pilot_run_id": PILOT_RUN,
                "replay_receipt_digest": replay_digest,
                "partition_receipt_digest": partition_digest,
            }
        ),
    )
    pilot_root = workspace / "runs" / PILOT_RUN
    _atomic_json(
        pilot_root / "evidence/pilot/replay-determinism.json",
        replay,
    )
    _atomic_json(
        pilot_root / "evidence/pilot/partition-pilot.json",
        partition,
    )
    _atomic_json(
        pilot_root / "state/awaiting-pilot-acceptance.json",
        _sealed(
            {
                "run_id": PILOT_RUN,
                "contract_digest": CONTRACT,
                "code_revision": PILOT_REVISION,
                "status": "paused",
                "evidence": {
                    "replay_report_digest": replay_digest,
                    "partition_report_digest": partition_digest,
                },
            }
        ),
    )
    return replay_digest, partition_digest


def test_pilot_approval_verifies_exact_run_and_receipts(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    workspace = tmp_path / "workspace"
    replay_digest, partition_digest = _pilot_fixture(repository, workspace)

    evidence = _pilot_approval(
        repository=repository,
        workspace=workspace,
        expected_contract_digest=CONTRACT,
    )

    assert evidence["pilot_run_id"] == PILOT_RUN
    assert evidence["replay_receipt_digest"] == replay_digest
    assert evidence["partition_receipt_digest"] == partition_digest


def test_file_checkpoint_detects_artifact_drift(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    run_root = workspace / "runs/build-r22222222"
    output = run_root / "evidence/audit.json"

    def write_output() -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("first\n", encoding="ascii")

    _run_file_step(
        workspace=workspace,
        run_root=run_root,
        phase="source-audit",
        output=output,
        action=write_output,
        run_id="build-r22222222",
        contract_digest=CONTRACT,
        code_revision=REVISION,
    )
    output.write_text("changed\n", encoding="ascii")

    with pytest.raises(DwdV21RuntimeError, match="evidence differs"):
        _run_file_step(
            workspace=workspace,
            run_root=run_root,
            phase="source-audit",
            output=output,
            action=write_output,
            run_id="build-r22222222",
            contract_digest=CONTRACT,
            code_revision=REVISION,
        )


def test_report_step_blocks_findings_and_verified_report_drift(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    run_root = workspace / "runs/build-r22222222"
    finding_output = run_root / "evidence/finding.json"

    def write_finding() -> None:
        _atomic_json(finding_output, _sealed({"summary": {"status": "finding"}}))

    with pytest.raises(DwdV21FullBuildError, match="did not verify"):
        _run_report_step(
            workspace=workspace,
            run_root=run_root,
            phase="candidate-finding-audit",
            output=finding_output,
            action=write_finding,
            label="candidate finding audit",
            status_path=("summary", "status"),
            run_id="build-r22222222",
            contract_digest=CONTRACT,
            code_revision=REVISION,
        )

    verified_output = run_root / "evidence/verified.json"

    def write_verified() -> None:
        _atomic_json(
            verified_output,
            _sealed({"summary": {"status": "verified"}, "result": "original"}),
        )

    _run_report_step(
        workspace=workspace,
        run_root=run_root,
        phase="candidate-verified-audit",
        output=verified_output,
        action=write_verified,
        label="candidate verified audit",
        status_path=("summary", "status"),
        run_id="build-r22222222",
        contract_digest=CONTRACT,
        code_revision=REVISION,
    )
    _atomic_json(
        verified_output,
        _sealed({"summary": {"status": "verified"}, "result": "drifted"}),
    )

    with pytest.raises(DwdV21RuntimeError, match="evidence differs"):
        _run_report_step(
            workspace=workspace,
            run_root=run_root,
            phase="candidate-verified-audit",
            output=verified_output,
            action=write_verified,
            label="candidate verified audit",
            status_path=("summary", "status"),
            run_id="build-r22222222",
            contract_digest=CONTRACT,
            code_revision=REVISION,
        )


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def test_full_build_records_controlled_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import quanthus.pipelines.dwd_v21_full_build as subject

    repository = tmp_path / "repository"
    workspace = tmp_path / "workspace"
    ods = tmp_path / "ods"
    dwd = tmp_path / "dwd"
    environment = tmp_path / "full.env"
    for path in (repository, workspace, ods, dwd):
        path.mkdir(parents=True)
    revision = "a" * 40
    run_id = "dwd-v2-1-full-aaaaaaaa-interrupted"
    environment.write_text(
        "\n".join(
            [
                "QUANTHUS_DWD_BUILDER_AUTHORIZED=1",
                f"QUANTHUS_DWD_CODE_REVISION={revision}",
                f"QUANTHUS_DWD_EXECUTION_CONTRACT_DIGEST={CONTRACT}",
                "QUANTHUS_DWD_EXECUTION_MODE=full_build",
                f"QUANTHUS_DWD_FULL_BUILD_APPROVED_REVISION={revision}",
                "QUANTHUS_DWD_PILOT_AUTHORIZED=0",
                f"QUANTHUS_DWD_RUN_ID={run_id}",
                "QUANTHUS_DWD_RESUME_AUTHORIZED=0",
                "QUANTHUS_DWD_KNOWLEDGE_CUTOFF=2026-08-03T23:59:00+00:00",
                "QUANTHUS_DWD_THROUGH_DATE=2026-07-31",
                "QUANTHUS_DWD_RUNTIME_ID=runtime-1",
                "QUANTHUS_DWD_MINIMUM_FREE_BYTES=1",
            ]
        )
        + "\n",
        encoding="ascii",
    )
    monkeypatch.setattr(subject, "_require_clean_revision", lambda *_args: None)
    monkeypatch.setattr(subject, "verify_authority", lambda *_args: ({}, {}))
    monkeypatch.setattr(
        subject,
        "_pilot_approval",
        lambda **_kwargs: {"pilot_run_id": "distinct-pilot"},
    )
    monkeypatch.setattr(
        subject,
        "run_preflight",
        lambda **_kwargs: (_ for _ in ()).throw(DwdV21FullBuildInterrupted(15)),
    )

    with pytest.raises(DwdV21FullBuildInterrupted):
        run_full_build(
            repository=repository,
            workspace=workspace,
            ods_base=ods,
            dwd_base=dwd,
            environment_path=environment,
            run_id=run_id,
            expected_revision=revision,
            expected_contract_digest=CONTRACT,
            expected_findings_digest="sha256:findings",
            expected_remediation_digest="sha256:remediation",
            minimum_free_bytes=1,
            knowledge_cutoff="2026-08-03T23:59:00+00:00",
            through_date="2026-07-31",
            runtime_id="runtime-1",
            resume=False,
        )

    interrupted = list((workspace / "runs" / run_id / "state").glob("*interrupted*.json"))
    assert len(interrupted) == 1
    receipt = json.loads(interrupted[0].read_text(encoding="ascii"))
    pointer = json.loads((workspace / "control/active-run.json").read_text(encoding="ascii"))
    assert receipt["status"] == "interrupted"
    assert receipt["evidence"]["signal"] == 15
    assert pointer["status"] == "interrupted"


def test_full_build_runs_sequentially_and_resumes_from_verified_checkpoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import quanthus.pipelines.dwd_v21_full_build as subject

    repository = tmp_path / "repository"
    workspace = tmp_path / "workspace"
    ods = tmp_path / "ods"
    dwd = tmp_path / "dwd"
    environment = tmp_path / "full.env"
    for path in (repository, workspace, ods, dwd):
        path.mkdir(parents=True)
    revision = "a" * 40
    run_id = "dwd-v2-1-full-aaaaaaaa-r1"
    environment.write_text(
        "\n".join(
            [
                "QUANTHUS_DWD_BUILDER_AUTHORIZED=1",
                f"QUANTHUS_DWD_CODE_REVISION={revision}",
                f"QUANTHUS_DWD_EXECUTION_CONTRACT_DIGEST={CONTRACT}",
                "QUANTHUS_DWD_EXECUTION_MODE=full_build",
                f"QUANTHUS_DWD_FULL_BUILD_APPROVED_REVISION={revision}",
                "QUANTHUS_DWD_PILOT_AUTHORIZED=0",
                f"QUANTHUS_DWD_RUN_ID={run_id}",
                "QUANTHUS_DWD_RESUME_AUTHORIZED=0",
                "QUANTHUS_DWD_KNOWLEDGE_CUTOFF=2026-08-01T00:00:00+00:00",
                "QUANTHUS_DWD_THROUGH_DATE=2026-07-31",
                "QUANTHUS_DWD_RUNTIME_ID=runtime-1",
                "QUANTHUS_DWD_MINIMUM_FREE_BYTES=1",
            ]
        )
        + "\n",
        encoding="ascii",
    )
    for relative in (
        subject._CONTRACT,
        subject._CONTRACT_APPROVAL,
        f"{subject._CLOSURE}/findings-ledger.json",
        f"{subject._CLOSURE}/v2.1-remediation-scope.json",
        "config/pipelines/dwd-domain-model.v1.json",
        "config/pipelines/dwd-domain-model.v1.1-extension.json",
        "config/pipelines/dwd-v2-1-input-identity.v1.json",
        "config/pipelines/securities-warehouse-reference-seeds.v1.json",
        "config/security-code-validity.v1.json",
        "config/pipelines/disclosure-event-vocabulary.v1.json",
        "config/pipelines/csrc-industry-hierarchy.v1.json",
        "config/pipelines/dwd-domain-accounting-receipt.v1.json",
    ):
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="ascii")

    monkeypatch.setattr(subject, "_require_clean_revision", lambda *_args: None)
    monkeypatch.setattr(subject, "verify_authority", lambda *_args: ({}, {}))
    accepted_pilot = {
        "approval_receipt": "/approval.json",
        "approval_receipt_digest": "sha256:approval",
        "pilot_run_id": "pilot-run",
        "pilot_revision": PILOT_REVISION,
        "replay_receipt": "/replay.json",
        "replay_receipt_digest": "sha256:replay",
        "partition_receipt": "/partition.json",
        "partition_receipt_digest": "sha256:partition",
    }
    monkeypatch.setattr(subject, "_pilot_approval", lambda **_kwargs: accepted_pilot)
    preflight_count = 0

    def fake_preflight(**_kwargs: Any) -> dict[str, Any]:
        nonlocal preflight_count
        preflight_count += 1
        return {
            "schema_version": 1,
            "kind": "dwd_base_v2_1_gpu_p100_preflight_receipt",
            "status": "passed",
            "completed_at": f"2026-08-08T17:00:0{preflight_count}+00:00",
            "ods_base": {
                "bundle_id": "ods-base-202607-v2",
                "manifest_sha256": "sha256:ods",
                "content_digest": "sha256:ods-content",
            },
        }

    monkeypatch.setattr(subject, "run_preflight", fake_preflight)
    monkeypatch.setattr(
        subject,
        "_audit_full_build_contract",
        lambda **_kwargs: {"status": "passed"},
    )

    def fake_plan(**kwargs: Any) -> SimpleNamespace:
        output = Path(kwargs["output"])
        output.mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(
            root=output,
            plan_digest="sha256:plan",
            input_manifest_sha256="sha256:ods",
            required_free_bytes=1,
            available_free_bytes=2,
            capacity_sufficient=True,
        )

    monkeypatch.setattr(subject, "build_input_plan", fake_plan)
    build_count = 0

    def fake_candidate(**kwargs: Any) -> SimpleNamespace:
        nonlocal build_count
        build_count += 1
        output = Path(kwargs["output"])
        domain = next(
            name
            for name, suffix in subject._CANDIDATE_NAMES.items()
            if output.name.endswith(suffix)
        )
        payload = output / "payload" / domain / "part.parquet"
        payload.parent.mkdir(parents=True, exist_ok=True)
        payload.write_bytes(domain.encode("ascii"))
        table_count = 1 if domain == "governance_sidecar" else 4
        row_counts = {f"dwd_{domain}_{index}": 1 for index in range(table_count)}
        manifest = {
            "kind": _EXPECTED_KINDS[domain],
            "run_id": run_id,
            "input_plan_digest": "sha256:plan",
            "semantic_digest": f"sha256:{domain}",
            "row_counts": row_counts,
            "files": [
                {
                    "path": payload.relative_to(output).as_posix(),
                    "sha256": _sha(payload),
                    "size_bytes": payload.stat().st_size,
                }
            ],
        }
        (output / "manifest.json").write_text(json.dumps(manifest), encoding="ascii")
        (output / "READY").write_text(_sha(output / "manifest.json") + "\n", encoding="ascii")
        return SimpleNamespace(root=output)

    for name in (
        "build_listed_company",
        "build_trading_calendar",
        "build_market_observation",
        "build_tradability_state",
        "build_financial_reporting",
        "build_disclosure_event",
        "build_industry_classification",
        "build_ownership_capital",
        "build_quality_governance",
    ):
        monkeypatch.setattr(subject, name, fake_candidate)

    def fake_audit(**kwargs: Any) -> SimpleNamespace:
        output = Path(kwargs["output"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("{}\n", encoding="ascii")
        return SimpleNamespace(report={})

    monkeypatch.setattr(
        subject,
        "_statement_audit",
        lambda **kwargs: fake_audit(output=kwargs["output"]),
    )
    for name in (
        "audit_financial_native_source_package",
        "combine_financial_reporting_source_audits",
        "audit_disclosure_event_source_package",
        "audit_ownership_capital_source_package",
    ):
        monkeypatch.setattr(subject, name, fake_audit)

    candidate_audit_count = 0
    candidate_audits = {
        name: {
            "path": f"/evidence/{name}.json",
            "sha256": f"sha256:file-{name}",
            "size_bytes": 1,
            "canonical_digest": f"sha256:canonical-{name}",
            "status": "verified",
        }
        for name in (
            "schema_partitions",
            "semantic_contracts",
            "domain_invariants",
            "coverage_exclusions",
            "lineage_authority",
        )
    }

    def fake_candidate_audits(**_kwargs: Any) -> dict[str, dict[str, Any]]:
        nonlocal candidate_audit_count
        candidate_audit_count += 1
        return candidate_audits

    monkeypatch.setattr(subject, "_run_candidate_audits", fake_candidate_audits)

    arguments = {
        "repository": repository,
        "workspace": workspace,
        "ods_base": ods,
        "dwd_base": dwd,
        "environment_path": environment,
        "run_id": run_id,
        "expected_revision": revision,
        "expected_contract_digest": CONTRACT,
        "expected_findings_digest": "sha256:findings",
        "expected_remediation_digest": "sha256:remediation",
        "minimum_free_bytes": 1,
        "knowledge_cutoff": "2026-08-01T00:00:00+00:00",
        "through_date": "2026-07-31",
        "runtime_id": "runtime-1",
        "resume": True,
    }
    first = run_full_build(**arguments)
    assert first["phase"] == "awaiting-candidate-approval"
    assert first["table_count"] == 33
    assert first["candidate_audit_digests"] == {
        name: evidence["canonical_digest"] for name, evidence in candidate_audits.items()
    }
    assert build_count == 9
    assert candidate_audit_count == 1
    assert (workspace / "runs" / run_id / "scratch/full-build/market-observation").is_dir()
    assert (workspace / "runs" / run_id / "scratch/full-build/tradability-state").is_dir()
    assert not (workspace / "runs" / run_id / "export" / "BASE_CURRENT.json").exists()

    environment.write_text(
        environment.read_text(encoding="ascii").replace(
            "QUANTHUS_DWD_RESUME_AUTHORIZED=0",
            "QUANTHUS_DWD_RESUME_AUTHORIZED=1",
        ),
        encoding="ascii",
    )
    second = run_full_build(**arguments)
    assert second["manifest_sha256"] == first["manifest_sha256"]
    assert build_count == 9
    assert candidate_audit_count == 2
    assert preflight_count == 2

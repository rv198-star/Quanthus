from __future__ import annotations

import base64
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from quanthus.policy import find_project_root
from quanthus.stage_learning.worker import (
    _JOB_SPECS,
    JOB_KIND,
    JOB_SCHEMA,
    STAGE_ADMISSION_EXPOSURE_CAPACITY_JOB,
    STAGE_ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_JOB,
    STAGE_ATOMIC_PROPOSITION_G1_JOB,
    STAGE_ATOMIC_PROPOSITION_SUCCESSOR_D1_JOB,
    STAGE_CAPACITY_CENSUS_JOB,
    STAGE_CLUSTERED_IDENTITY_ADMISSION_JOB,
    STAGE_COMBINED_ATOMIC_SCORING_JOB,
    STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_PREPARE_JOB,
    STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_SET_JOB,
    STAGE_COMPLETE_RESERVE_REVIEW_READINESS_JOB,
    STAGE_DEVELOPMENT_RESOURCE_ADMISSION_JOB,
    STAGE_DEVELOPMENT_RESOURCE_CENSUS_JOB,
    STAGE_DEVELOPMENT_RESOURCE_PLAN_JOB,
    STAGE_DEVELOPMENT_RESOURCE_QUALIFIED_PLAN_JOB,
    STAGE_DIRECTIONAL_BOUNDARY_D1_JOB,
    STAGE_DIRECTIONAL_BOUNDARY_D3_JOB,
    STAGE_DOWNSIDE_LIFECYCLE_RESOURCE_PLAN_JOB,
    STAGE_DOWNSIDE_LIFECYCLE_REFERENCE_PREPARE_JOB,
    STAGE_INSTABILITY_CHANNEL_DECOMPOSITION_JOB,
    STAGE_ORDER_PROPOSITION_DEVELOPMENT_JOB,
    STAGE_ORDER_RULE_PREPARE_JOB,
    STAGE_ORDER_RULE_RESOLVE_SCORE_JOB,
    STAGE_ORDERLY_CONSOLIDATION_JOINT_D1_JOB,
    STAGE_POSITIVE_INSTABILITY_D1_JOB,
    STAGE_POSITIVE_INSTABILITY_QUANTITATIVE_D1_JOB,
    STAGE_PRIMARY_UNIQUE_CONTINGENT_FALLBACK_JOB,
    STAGE_QUALIFICATION_JOB,
    STAGE_REALIZED_IDENTITY_ADMISSION_JOB,
    STAGE_REFERENCE_PILOT_JOB,
    STAGE_SECOND_GROUP_DEVELOPMENT_JOB,
    STAGE_SECOND_GROUP_DEVELOPMENT_SUPPLEMENT_JOB,
    STAGE_SECOND_GROUP_PROFILE_JOB,
    STAGE_SECOND_GROUP_REPAIR_PROFILE_JOB,
    STAGE_STRATUM_NORMALIZED_INSTABILITY_JOB,
    STAGE_THIRD_GROUP_PROFILE_JOB,
    StageJob,
    StageWorker,
    StageWorkerError,
    StageWorkerPaths,
    _complete_reserve_additional_ready_transport,
    _consume_qualification_budget,
    _digest,
    cancel_job,
    doctor,
    read_status,
    seal_p014_review,
    seal_p015_review,
    submit_job,
)

ROOT = find_project_root()
REVISION = "a" * 40


def paths(tmp_path: Path) -> StageWorkerPaths:
    training = tmp_path / "training"
    source = training / "sources" / "dwd-v2.1-full-20260908"
    source.mkdir(parents=True)
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    python = repo / ".venv-stage-training" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\n")
    python.chmod(0o755)
    key = tmp_path / "github-ro"
    key.write_text("test-only")
    return StageWorkerPaths(training, repo, key)


def seed_development_resource_plan_recovery_auth(
    worker_paths: StageWorkerPaths, failed_job: StageJob,
) -> None:
    relpaths = (
        "config/stage-learning/stage-development-resource-plan.v1.json",
        "config/stage-learning/stage-development-resource-quota-freeze.v1.json",
        "config/stage-learning/stage-development-resource-quota-revision-r1.v1.json",
    )
    for relpath in relpaths:
        target = worker_paths.control_repo / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relpath).read_bytes())
    recovery_path = "config/stage-learning/stage-development-resource-plan-recovery.v1.json"
    recovery = json.loads((ROOT / recovery_path).read_text(encoding="utf-8"))
    recovery["failed_job"] = {
        "job_id": failed_job.job_id,
        "code_revision": failed_job.code_revision,
        "input_digest": failed_job.input_digest,
        "terminal_state": "failed",
        "error": "stage_development_resource_plan_process_failed",
        "artifact_published": False,
        "diagnosis": "synthetic_metadata_plan_failure",
    }
    target = worker_paths.control_repo / recovery_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(recovery), encoding="utf-8")


def seed_stratum_normalized_instability_recovery_auth(
    worker_paths: StageWorkerPaths, failed_job: StageJob,
) -> None:
    relpaths = (
        "config/stage-learning/stage-stratum-normalized-instability.v1.json",
        "config/stage-learning/stage-complete-reserve-reference-sufficiency-result.v1.json",
        "config/stage-learning/stage-combined-atomic-scoring-result.v1.json",
    )
    for relpath in relpaths:
        target = worker_paths.control_repo / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relpath).read_bytes())
    recovery_path = "config/stage-learning/stage-stratum-normalized-instability-recovery.v1.json"
    recovery = json.loads((ROOT / recovery_path).read_text(encoding="utf-8"))
    recovery["failed_job"].update(
        {
            "job_id": failed_job.job_id,
            "code_revision": failed_job.code_revision,
            "input_digest": failed_job.input_digest,
        }
    )
    target = worker_paths.control_repo / recovery_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(recovery), encoding="utf-8")


def seed_downside_lifecycle_resource_plan_recovery_auth(
    worker_paths: StageWorkerPaths, failed_job: StageJob,
) -> None:
    relpaths = (
        "config/stage-learning/stage-downside-lifecycle-resource-plan.v1.json",
        "config/stage-learning/stage-downside-lifecycle-concept.v1.json",
        "config/stage-learning/stage-development-resource-model.v1.json",
    )
    for relpath in relpaths:
        target = worker_paths.control_repo / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relpath).read_bytes())
    recovery_path = (
        "config/stage-learning/stage-downside-lifecycle-resource-plan-recovery.v1.json"
    )
    recovery = json.loads((ROOT / recovery_path).read_text(encoding="utf-8"))
    recovery["failed_job"].update(
        {
            "job_id": failed_job.job_id,
            "code_revision": failed_job.code_revision,
            "input_digest": failed_job.input_digest,
        }
    )
    target = worker_paths.control_repo / recovery_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(recovery), encoding="utf-8")


def seed_order_rule_resolve_score_parent(worker_paths: StageWorkerPaths) -> StageJob:
    config = worker_paths.control_repo / "config/stage-learning/order-rule-resolve-score.v1.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    prepare_job = StageJob(
        job_id="stage_" + "1" * 32,
        kind=STAGE_ORDER_RULE_PREPARE_JOB,
        code_revision="b" * 40,
        config="config/stage-learning/order-rule-prepare.v1.json",
        submitted_at="2026-09-11T07:59:43+00:00",
    )
    manifest = "sha256:" + "1" * 64
    selection = "sha256:" + "2" * 64
    evidence = "sha256:" + "3" * 64
    config.write_text(json.dumps({
        "prepare": {
            "job_id": prepare_job.job_id,
            "manifest_sha256": manifest,
            "selection_digest": selection,
            "evidence_digest": evidence,
            "case_count": 160,
        }
    }))
    StageWorker(worker_paths)._write_status(
        prepare_job,
        state="succeeded",
        stage="terminal",
        started_at="2026-09-11T08:00:00+00:00",
        completed_at="2026-09-11T08:05:00+00:00",
        exit_code=0,
        result_ref={
            "manifest_sha256": manifest,
            "order_rule_prepare": {
                "selection_digest": selection,
                "evidence_digest": evidence,
                "selected_case_count": 160,
                "source_isolation_verified": True,
                "review_ready": True,
            },
        },
    )
    return prepare_job


def test_submit_status_and_preclaim_cancel_are_durable(tmp_path: Path) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(worker_paths, code_revision=REVISION)
    assert submitted["state"] == "queued"
    assert submitted["kind"] == JOB_KIND
    job_id = submitted["job_id"]

    queued = read_status(worker_paths, job_id)
    assert queued["state"] == "queued"
    assert queued["code_revision"] == REVISION
    assert queued["input_digest"] == submitted["input_digest"]

    canceled = cancel_job(worker_paths, job_id)
    assert canceled["state"] == "canceled"
    assert canceled["error"] == "cancel_requested_before_claim"
    assert not (worker_paths.queue / f"{job_id}.json").exists()
    assert (worker_paths.status / f"{job_id}.json").is_file()

    with pytest.raises(StageWorkerError, match="terminal"):
        cancel_job(worker_paths, job_id)


def test_worker_reuses_installer_pinned_main_before_network_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = StageWorker(paths(tmp_path))
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(worker, "_revision_admitted_locally", lambda revision: revision == REVISION)

    def unexpected_git(*arguments: str, **_kwargs: object) -> object:
        calls.append(arguments)
        raise AssertionError("network fetch should not run for installer-pinned revision")

    monkeypatch.setattr(worker, "_git", unexpected_git)
    worker._authorize_revision(REVISION)
    assert calls == []


def test_stage_job_contract_rejects_branch_kind_and_config_drift() -> None:
    valid = {
        "schema_version": JOB_SCHEMA,
        "job_id": "stage_" + "b" * 32,
        "kind": JOB_KIND,
        "code_revision": REVISION,
        "config": "config/stage-learning/harness.v1.json",
        "submitted_at": "2026-09-08T06:00:00+00:00",
    }
    assert StageJob.from_dict(valid).code_revision == REVISION

    for key, value in (
        ("code_revision", "main"),
        ("kind", "quanthus.candidate-replay.v1"),
        ("config", "config/p0-policy.v1.json"),
    ):
        changed = {**valid, key: value}
        with pytest.raises(StageWorkerError):
            StageJob.from_dict(changed)

    with pytest.raises(StageWorkerError):
        StageJob.from_dict({**valid, "unexpected": True})


def test_doctor_is_node_local_and_has_no_database_requirement(tmp_path: Path) -> None:
    worker_paths = paths(tmp_path)
    result = doctor(worker_paths)
    assert result["ready"] is True
    assert result["concurrency"] == 1
    assert result["recent_p011_d1_failure"] is None
    assert result["recent_p012_g1_failure"] is None
    assert result["recent_p013_c_failure"] is None
    assert result["recent_p013_d1_failure"] is None
    assert result["recent_p014_a_failure"] is None
    assert result["recent_p014_b_failure"] is None
    assert result["recent_p014_c_failure"] is None
    assert result["recent_p015_a_failure"] is None
    assert result["recent_p015_b_failure"] is None
    assert result["recent_p015_c_failure"] is None
    assert result["recent_p016_b_failure"] is None
    assert result["recent_p017_b_failure"] is None
    assert result["recent_p018_b_failure"] is None
    assert result["recent_p020_b_failure"] is None
    assert result["recent_p021_b_failure"] is None
    assert result["recent_p022_b_failure"] is None
    assert result["recent_p023_failure"] is None
    assert result["recent_p024_failure"] is None
    assert result["recent_p020c_packet_failure"] is None
    assert set(result["job_kinds"]) == {
        JOB_KIND,
        STAGE_DOWNSIDE_LIFECYCLE_REFERENCE_PREPARE_JOB,
        STAGE_REFERENCE_PILOT_JOB,
        STAGE_QUALIFICATION_JOB,
        STAGE_ATOMIC_PROPOSITION_G1_JOB,
        STAGE_ATOMIC_PROPOSITION_SUCCESSOR_D1_JOB,
        STAGE_ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_JOB,
        STAGE_COMPLETE_RESERVE_REVIEW_READINESS_JOB,
        STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_SET_JOB,
        STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_PREPARE_JOB,
        STAGE_COMBINED_ATOMIC_SCORING_JOB,
        STAGE_STRATUM_NORMALIZED_INSTABILITY_JOB,
        STAGE_INSTABILITY_CHANNEL_DECOMPOSITION_JOB,
        STAGE_DOWNSIDE_LIFECYCLE_RESOURCE_PLAN_JOB,
        STAGE_ADMISSION_EXPOSURE_CAPACITY_JOB,
        STAGE_PRIMARY_UNIQUE_CONTINGENT_FALLBACK_JOB,
        STAGE_REALIZED_IDENTITY_ADMISSION_JOB,
        STAGE_CLUSTERED_IDENTITY_ADMISSION_JOB,
        STAGE_CAPACITY_CENSUS_JOB,
        STAGE_DEVELOPMENT_RESOURCE_ADMISSION_JOB,
        STAGE_DEVELOPMENT_RESOURCE_CENSUS_JOB,
        STAGE_DEVELOPMENT_RESOURCE_PLAN_JOB,
        STAGE_DEVELOPMENT_RESOURCE_QUALIFIED_PLAN_JOB,
        STAGE_DIRECTIONAL_BOUNDARY_D1_JOB,
        STAGE_DIRECTIONAL_BOUNDARY_D3_JOB,
        STAGE_POSITIVE_INSTABILITY_D1_JOB,
        STAGE_POSITIVE_INSTABILITY_QUANTITATIVE_D1_JOB,
        STAGE_ORDERLY_CONSOLIDATION_JOINT_D1_JOB,
        STAGE_SECOND_GROUP_PROFILE_JOB,
        STAGE_SECOND_GROUP_REPAIR_PROFILE_JOB,
        STAGE_SECOND_GROUP_DEVELOPMENT_JOB,
        STAGE_SECOND_GROUP_DEVELOPMENT_SUPPLEMENT_JOB,
        STAGE_THIRD_GROUP_PROFILE_JOB,
        STAGE_ORDER_PROPOSITION_DEVELOPMENT_JOB,
        STAGE_ORDER_RULE_PREPARE_JOB,
        STAGE_ORDER_RULE_RESOLVE_SCORE_JOB,
    }
    assert not any("postgres" in key.lower() or "dsn" in key.lower() for key in result)


@pytest.mark.parametrize(
    ("kind", "diagnostic_key"),
    (
        (STAGE_ORDERLY_CONSOLIDATION_JOINT_D1_JOB, "recent_p011_d1_failure"),
        (STAGE_ATOMIC_PROPOSITION_G1_JOB, "recent_p012_g1_failure"),
        (STAGE_DEVELOPMENT_RESOURCE_PLAN_JOB, "recent_p013_c_failure"),
        (STAGE_ATOMIC_PROPOSITION_SUCCESSOR_D1_JOB, "recent_p013_d1_failure"),
        (STAGE_DEVELOPMENT_RESOURCE_ADMISSION_JOB, "recent_p014_a_failure"),
        (STAGE_DEVELOPMENT_RESOURCE_QUALIFIED_PLAN_JOB, "recent_p014_b_failure"),
        (STAGE_ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_JOB, "recent_p014_c_failure"),
        (STAGE_COMPLETE_RESERVE_REVIEW_READINESS_JOB, "recent_p015_a_failure"),
        (STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_SET_JOB, "recent_p015_b_failure"),
        (STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_PREPARE_JOB, "recent_p015_c_failure"),
        (STAGE_COMBINED_ATOMIC_SCORING_JOB, "recent_p016_b_failure"),
        (STAGE_STRATUM_NORMALIZED_INSTABILITY_JOB, "recent_p017_b_failure"),
        (STAGE_INSTABILITY_CHANNEL_DECOMPOSITION_JOB, "recent_p018_b_failure"),
        (STAGE_DOWNSIDE_LIFECYCLE_RESOURCE_PLAN_JOB, "recent_p020_b_failure"),
        (STAGE_DOWNSIDE_LIFECYCLE_REFERENCE_PREPARE_JOB, "recent_p020c_packet_failure"),
        (STAGE_ADMISSION_EXPOSURE_CAPACITY_JOB, "recent_p021_b_failure"),
        (STAGE_PRIMARY_UNIQUE_CONTINGENT_FALLBACK_JOB, "recent_p022_b_failure"),
        (STAGE_REALIZED_IDENTITY_ADMISSION_JOB, "recent_p023_failure"),
        (STAGE_CLUSTERED_IDENTITY_ADMISSION_JOB, "recent_p024_failure"),
    ),
)
def test_doctor_exposes_only_bounded_recent_failure_tail(
    tmp_path: Path, kind: str, diagnostic_key: str,
) -> None:
    worker_paths = paths(tmp_path)
    worker_paths.ensure()
    job = StageJob(
        job_id="stage_" + "9" * 32,
        kind=kind,
        code_revision=REVISION,
        config=_JOB_SPECS[kind]["config"],
        submitted_at="2026-09-13T06:00:00+00:00",
    )
    StageWorker(worker_paths)._write_status(
        job,
        state="failed",
        stage="terminal",
        started_at="2026-09-13T06:00:01+00:00",
        completed_at="2026-09-13T06:02:00+00:00",
        error=_JOB_SPECS[kind]["process_error"],
        exit_code=1,
    )
    lines = [f"line-{index}" for index in range(60)]
    log_path = worker_paths.logs / f"{job.job_id}.log"
    log_path.write_text("x" * 32768 + "\n" + "\n".join(lines), encoding="utf-8")
    status_path = worker_paths.status / f"{job.job_id}.json"
    status_before = status_path.read_bytes()
    worker_paths.atomic_proposition_g1_budget.write_text("immutable-budget", encoding="utf-8")

    result = doctor(worker_paths)
    diagnostic = result[diagnostic_key]
    assert diagnostic["job_id"] == job.job_id
    assert diagnostic["code_revision"] == REVISION
    assert diagnostic["input_digest"] == job.input_digest
    assert diagnostic["error"] == _JOB_SPECS[kind]["process_error"]
    assert diagnostic["exit_code"] == 1
    assert len(diagnostic["log_tail"]) == 40
    assert diagnostic["log_tail"][0] == "line-20"
    assert diagnostic["log_tail"][-1] == "line-59"
    assert status_path.read_bytes() == status_before
    assert worker_paths.atomic_proposition_g1_budget.read_text() == "immutable-budget"
    assert not list(worker_paths.queue.glob("stage_*.json"))
    assert "selection_transport" not in result
    assert "review_transport_index" not in result


@pytest.mark.parametrize("invalid_binding", (False, True))
def test_doctor_failure_log_access_stays_within_exact_job(
    tmp_path: Path, invalid_binding: bool,
) -> None:
    worker_paths = paths(tmp_path)
    worker = StageWorker(worker_paths)
    job = StageJob(
        job_id="stage_" + "8" * 32,
        kind=STAGE_ATOMIC_PROPOSITION_G1_JOB,
        code_revision=REVISION,
        config=_JOB_SPECS[STAGE_ATOMIC_PROPOSITION_G1_JOB]["config"],
        submitted_at="2026-09-13T20:28:15+00:00",
    )
    worker._write_status(
        job, state="failed", stage="terminal", exit_code=1,
        error="stage_atomic_proposition_g1_process_failed",
    )
    outside = tmp_path / "not-a-job-log"
    outside.write_text("must-not-be-exposed", encoding="utf-8")
    log_path = worker_paths.logs / f"{job.job_id}.log"
    log_path.symlink_to(outside)
    if invalid_binding:
        status_path = worker_paths.status / f"{job.job_id}.json"
        value = json.loads(status_path.read_text())
        value["job_id"] = "../../not-a-job-log"
        status_path.write_text(json.dumps(value), encoding="utf-8")
    diagnostic = doctor(worker_paths)["recent_p012_g1_failure"]
    if invalid_binding:
        assert diagnostic is None
    else:
        assert diagnostic["job_id"] == job.job_id
        assert diagnostic["log_tail"] == []


def test_installer_and_unit_freeze_rx6800_resource_and_authority_boundaries() -> None:
    installer = (ROOT / "ops/bin/install-quanthus-stage-worker").read_text(encoding="utf-8")
    wrapper = (ROOT / "ops/bin/quanthus-stage-worker").read_text(encoding="utf-8")
    unit = (ROOT / "ops/systemd/quanthus-stage-worker.service").read_text(encoding="utf-8")

    for term in (
        '[[ "$(hostname)" == "RX6800" ]]',
        "sudo loginctl enable-linger",
        "StrictHostKeyChecking=yes",
        "-F none",
        "UserKnownHostsFile=",
        "GlobalKnownHostsFile=/dev/null",
        "quanthus-github-ro",
        "bootstrap-stage-training-node",
        "systemctl --user enable quanthus-stage-worker.service",
        "systemctl --user restart quanthus-stage-worker.service",
        "worker-runtime.json",
        "stage worker runtime identity differs",
        "--kind \"${bootstrap_kind}\"",
        "--code-revision \"${revision}\"",
        "bootstrap_state=${bootstrap_state}",
        "git@github.com:rv198-star/Quanthus.git",
    ):
        assert term in installer

    assert "quanthus.stage_learning.worker" in wrapper
    assert "QUANTHUS_STAGE_WORKER_CODE_REVISION" in wrapper
    worker_source = (ROOT / "src/quanthus/stage_learning/worker.py").read_text(encoding="utf-8")
    assert "ssh -F none" in worker_source
    assert "UserKnownHostsFile=" in worker_source
    assert "GlobalKnownHostsFile=/dev/null" in worker_source
    assert "/proc/" not in installer
    assert "MemoryMax=34G" in unit
    assert "CPUQuota=800%" in unit
    assert "Restart=always" in unit
    assert "NoNewPrivileges=true" in unit
    assert "quanthus-stage-worker serve" in unit
    assert "POSTGRES" not in unit.upper()
    assert "DATABASE" not in unit.upper()



def test_worker_publishes_runtime_identity_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker_paths = paths(tmp_path)
    worker = StageWorker(worker_paths)
    monkeypatch.setenv("QUANTHUS_STAGE_WORKER_CODE_REVISION", REVISION)
    monkeypatch.setattr(worker, "run_once", lambda: setattr(worker, "stopping", True))
    worker.serve()
    receipt = json.loads(worker_paths.runtime_identity.read_text(encoding="utf-8"))
    assert receipt["schema_version"] == "quanthus.stage-worker-runtime.v1"
    assert receipt["code_revision"] == REVISION
    assert receipt["pid"] > 0
    assert set(receipt["job_kinds"]) == {
        JOB_KIND,
        STAGE_DOWNSIDE_LIFECYCLE_REFERENCE_PREPARE_JOB,
        STAGE_REFERENCE_PILOT_JOB,
        STAGE_QUALIFICATION_JOB,
        STAGE_ATOMIC_PROPOSITION_G1_JOB,
        STAGE_ATOMIC_PROPOSITION_SUCCESSOR_D1_JOB,
        STAGE_ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_JOB,
        STAGE_COMPLETE_RESERVE_REVIEW_READINESS_JOB,
        STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_SET_JOB,
        STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_PREPARE_JOB,
        STAGE_COMBINED_ATOMIC_SCORING_JOB,
        STAGE_STRATUM_NORMALIZED_INSTABILITY_JOB,
        STAGE_INSTABILITY_CHANNEL_DECOMPOSITION_JOB,
        STAGE_DOWNSIDE_LIFECYCLE_RESOURCE_PLAN_JOB,
        STAGE_ADMISSION_EXPOSURE_CAPACITY_JOB,
        STAGE_PRIMARY_UNIQUE_CONTINGENT_FALLBACK_JOB,
        STAGE_REALIZED_IDENTITY_ADMISSION_JOB,
        STAGE_CLUSTERED_IDENTITY_ADMISSION_JOB,
        STAGE_CAPACITY_CENSUS_JOB,
        STAGE_DEVELOPMENT_RESOURCE_ADMISSION_JOB,
        STAGE_DEVELOPMENT_RESOURCE_CENSUS_JOB,
        STAGE_DEVELOPMENT_RESOURCE_PLAN_JOB,
        STAGE_DEVELOPMENT_RESOURCE_QUALIFIED_PLAN_JOB,
        STAGE_DIRECTIONAL_BOUNDARY_D1_JOB,
        STAGE_DIRECTIONAL_BOUNDARY_D3_JOB,
        STAGE_POSITIVE_INSTABILITY_D1_JOB,
        STAGE_POSITIVE_INSTABILITY_QUANTITATIVE_D1_JOB,
        STAGE_ORDERLY_CONSOLIDATION_JOINT_D1_JOB,
        STAGE_SECOND_GROUP_PROFILE_JOB,
        STAGE_SECOND_GROUP_REPAIR_PROFILE_JOB,
        STAGE_SECOND_GROUP_DEVELOPMENT_JOB,
        STAGE_SECOND_GROUP_DEVELOPMENT_SUPPLEMENT_JOB,
        STAGE_THIRD_GROUP_PROFILE_JOB,
        STAGE_ORDER_PROPOSITION_DEVELOPMENT_JOB,
        STAGE_ORDER_RULE_PREPARE_JOB,
        STAGE_ORDER_RULE_RESOLVE_SCORE_JOB,
    }
    assert receipt["concurrency"] == 1

def test_reference_pilot_kind_gets_its_frozen_config(tmp_path: Path) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_REFERENCE_PILOT_JOB,
    )
    assert submitted["kind"] == STAGE_REFERENCE_PILOT_JOB
    assert submitted["config"] == "config/stage-learning/reference-pilot.v1.json"


def test_capacity_census_kind_gets_its_frozen_config(tmp_path: Path) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_CAPACITY_CENSUS_JOB,
    )
    assert submitted["kind"] == STAGE_CAPACITY_CENSUS_JOB
    assert submitted["config"] == "config/stage-learning/sample-capacity-census.v1.json"
    assert _JOB_SPECS[STAGE_CAPACITY_CENSUS_JOB]["wall_seconds"] == 1800
    assert not worker_paths.qualification_budget.exists()


def test_development_resource_census_is_one_shot_and_idempotent(tmp_path: Path) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_DEVELOPMENT_RESOURCE_CENSUS_JOB,
    )
    assert submitted["kind"] == STAGE_DEVELOPMENT_RESOURCE_CENSUS_JOB
    assert submitted["config"] == (
        "config/stage-learning/stage-development-resource-capacity-census.v1.json"
    )
    assert _JOB_SPECS[STAGE_DEVELOPMENT_RESOURCE_CENSUS_JOB]["wall_seconds"] == 900
    assert worker_paths.development_resource_census_budget.is_file()
    repeated = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_DEVELOPMENT_RESOURCE_CENSUS_JOB,
    )
    assert repeated["job_id"] == submitted["job_id"]
    with pytest.raises(StageWorkerError, match="no retry or replacement authority"):
        submit_job(
            worker_paths,
            code_revision="b" * 40,
            kind=STAGE_DEVELOPMENT_RESOURCE_CENSUS_JOB,
        )


def test_development_resource_plan_is_one_shot_and_idempotent(tmp_path: Path) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_DEVELOPMENT_RESOURCE_PLAN_JOB,
    )
    assert submitted["kind"] == STAGE_DEVELOPMENT_RESOURCE_PLAN_JOB
    assert submitted["config"] == "config/stage-learning/stage-development-resource-plan.v1.json"
    assert _JOB_SPECS[STAGE_DEVELOPMENT_RESOURCE_PLAN_JOB]["wall_seconds"] == 600
    assert worker_paths.development_resource_plan_budget.is_file()
    repeated = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_DEVELOPMENT_RESOURCE_PLAN_JOB,
    )
    assert repeated["job_id"] == submitted["job_id"]
    with pytest.raises(StageWorkerError, match="requires exact failed parent"):
        submit_job(
            worker_paths,
            code_revision="b" * 40,
            kind=STAGE_DEVELOPMENT_RESOURCE_PLAN_JOB,
        )


def test_development_resource_plan_allows_one_bound_metadata_recovery(tmp_path: Path) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_DEVELOPMENT_RESOURCE_PLAN_JOB,
    )
    failed_job = StageJob(
        job_id=submitted["job_id"],
        kind=STAGE_DEVELOPMENT_RESOURCE_PLAN_JOB,
        code_revision=REVISION,
        config="config/stage-learning/stage-development-resource-plan.v1.json",
        submitted_at=submitted["submitted_at"],
    )
    StageWorker(worker_paths)._write_status(
        failed_job,
        state="failed",
        stage="terminal",
        started_at="2026-09-14T09:34:17+00:00",
        completed_at="2026-09-14T09:34:18+00:00",
        error="stage_development_resource_plan_process_failed",
        exit_code=1,
    )
    seed_development_resource_plan_recovery_auth(worker_paths, failed_job)
    recovery_revision = "b" * 40
    recovered = submit_job(
        worker_paths,
        code_revision=recovery_revision,
        kind=STAGE_DEVELOPMENT_RESOURCE_PLAN_JOB,
    )
    assert recovered["metadata_recovery_of"] == failed_job.job_id
    assert recovered["job_id"] != failed_job.job_id
    assert worker_paths.development_resource_plan_recovery_budget.is_file()
    repeated = submit_job(
        worker_paths,
        code_revision=recovery_revision,
        kind=STAGE_DEVELOPMENT_RESOURCE_PLAN_JOB,
    )
    assert repeated["job_id"] == recovered["job_id"]
    with pytest.raises(StageWorkerError, match="no further authority"):
        submit_job(
            worker_paths,
            code_revision="c" * 40,
            kind=STAGE_DEVELOPMENT_RESOURCE_PLAN_JOB,
        )


def test_atomic_proposition_successor_d1_is_one_shot_and_idempotent(tmp_path: Path) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_ATOMIC_PROPOSITION_SUCCESSOR_D1_JOB,
    )
    assert submitted["kind"] == STAGE_ATOMIC_PROPOSITION_SUCCESSOR_D1_JOB
    assert submitted["config"] == (
        "config/stage-learning/stage-atomic-proposition-successor-development-screen.v1.json"
    )
    assert _JOB_SPECS[STAGE_ATOMIC_PROPOSITION_SUCCESSOR_D1_JOB]["wall_seconds"] == 3600
    assert worker_paths.atomic_proposition_successor_d1_budget.is_file()
    repeated = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_ATOMIC_PROPOSITION_SUCCESSOR_D1_JOB,
    )
    assert repeated["job_id"] == submitted["job_id"]
    with pytest.raises(StageWorkerError, match="no retry or replacement authority"):
        submit_job(
            worker_paths,
            code_revision="b" * 40,
            kind=STAGE_ATOMIC_PROPOSITION_SUCCESSOR_D1_JOB,
        )


def test_atomic_proposition_admission_qualified_d1_is_one_shot_and_idempotent(
    tmp_path: Path,
) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_JOB,
    )
    assert submitted["kind"] == STAGE_ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_JOB
    assert submitted["config"] == (
        "config/stage-learning/"
        "stage-atomic-proposition-admission-qualified-development-screen.v1.json"
    )
    assert (
        _JOB_SPECS[STAGE_ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_JOB]["wall_seconds"]
        == 3600
    )
    assert worker_paths.atomic_proposition_admission_qualified_d1_budget.is_file()
    repeated = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_JOB,
    )
    assert repeated["job_id"] == submitted["job_id"]
    with pytest.raises(StageWorkerError, match="no retry or replacement authority"):
        submit_job(
            worker_paths,
            code_revision="b" * 40,
            kind=STAGE_ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_JOB,
        )


def test_development_resource_admission_is_one_shot_and_idempotent(tmp_path: Path) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_DEVELOPMENT_RESOURCE_ADMISSION_JOB,
    )
    assert submitted["kind"] == STAGE_DEVELOPMENT_RESOURCE_ADMISSION_JOB
    assert submitted["config"] == (
        "config/stage-learning/stage-development-resource-admission-screen.v1.json"
    )
    assert _JOB_SPECS[STAGE_DEVELOPMENT_RESOURCE_ADMISSION_JOB]["wall_seconds"] == 2700
    assert worker_paths.development_resource_admission_budget.is_file()
    repeated = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_DEVELOPMENT_RESOURCE_ADMISSION_JOB,
    )
    assert repeated["job_id"] == submitted["job_id"]
    with pytest.raises(StageWorkerError, match="no retry or replacement authority"):
        submit_job(
            worker_paths,
            code_revision="b" * 40,
            kind=STAGE_DEVELOPMENT_RESOURCE_ADMISSION_JOB,
        )


def test_development_resource_qualified_plan_is_one_shot_and_idempotent(
    tmp_path: Path,
) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_DEVELOPMENT_RESOURCE_QUALIFIED_PLAN_JOB,
    )
    assert submitted["kind"] == STAGE_DEVELOPMENT_RESOURCE_QUALIFIED_PLAN_JOB
    assert submitted["config"] == (
        "config/stage-learning/stage-development-resource-qualified-plan.v1.json"
    )
    assert _JOB_SPECS[STAGE_DEVELOPMENT_RESOURCE_QUALIFIED_PLAN_JOB]["wall_seconds"] == 600
    assert worker_paths.development_resource_qualified_plan_budget.is_file()
    repeated = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_DEVELOPMENT_RESOURCE_QUALIFIED_PLAN_JOB,
    )
    assert repeated["job_id"] == submitted["job_id"]
    with pytest.raises(StageWorkerError, match="no retry or replacement authority"):
        submit_job(
            worker_paths,
            code_revision="b" * 40,
            kind=STAGE_DEVELOPMENT_RESOURCE_QUALIFIED_PLAN_JOB,
        )


def test_complete_reserve_review_readiness_is_one_shot_and_idempotent(
    tmp_path: Path,
) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_COMPLETE_RESERVE_REVIEW_READINESS_JOB,
    )
    assert submitted["kind"] == STAGE_COMPLETE_RESERVE_REVIEW_READINESS_JOB
    assert submitted["config"] == (
        "config/stage-learning/stage-complete-reserve-review-readiness.v1.json"
    )
    assert _JOB_SPECS[STAGE_COMPLETE_RESERVE_REVIEW_READINESS_JOB]["wall_seconds"] == 3600
    assert worker_paths.complete_reserve_review_readiness_budget.is_file()
    repeated = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_COMPLETE_RESERVE_REVIEW_READINESS_JOB,
    )
    assert repeated["job_id"] == submitted["job_id"]
    with pytest.raises(StageWorkerError, match="no retry or replacement authority"):
        submit_job(
            worker_paths,
            code_revision="b" * 40,
            kind=STAGE_COMPLETE_RESERVE_REVIEW_READINESS_JOB,
        )


def test_complete_reserve_additional_review_set_is_one_shot_and_idempotent(
    tmp_path: Path,
) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_SET_JOB,
    )
    assert submitted["kind"] == STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_SET_JOB
    assert submitted["config"] == (
        "config/stage-learning/stage-complete-reserve-additional-review-set.v1.json"
    )
    assert _JOB_SPECS[STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_SET_JOB]["wall_seconds"] == 600
    assert worker_paths.complete_reserve_additional_review_set_budget.is_file()
    repeated = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_SET_JOB,
    )
    assert repeated["job_id"] == submitted["job_id"]
    with pytest.raises(StageWorkerError, match="recovery requires exact failed parent"):
        submit_job(
            worker_paths,
            code_revision="b" * 40,
            kind=STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_SET_JOB,
        )


def test_complete_reserve_additional_review_set_allows_one_exact_recovery(
    tmp_path: Path,
) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_SET_JOB,
    )
    failed_job = StageJob(
        job_id=submitted["job_id"],
        kind=STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_SET_JOB,
        code_revision=REVISION,
        config=submitted["config"],
        submitted_at=submitted["submitted_at"],
    )
    StageWorker(worker_paths)._write_status(
        failed_job,
        state="failed",
        stage="terminal",
        started_at="2026-09-14T16:01:37+00:00",
        completed_at="2026-09-14T16:01:38+00:00",
        error="stage_complete_reserve_additional_review_set_process_failed",
        exit_code=1,
    )
    config_root = worker_paths.control_repo / "config/stage-learning"
    config_root.mkdir(parents=True, exist_ok=True)
    bindings = {}
    for key, name in (
        ("original_p015_b_config", "stage-complete-reserve-additional-review-set.v1.json"),
        ("p014_closeout", "stage-atomic-proposition-admission-qualified-d1-closeout.v1.json"),
        ("p015_a_result", "stage-complete-reserve-review-readiness-result.v1.json"),
    ):
        path = config_root / name
        path.write_text(json.dumps({"binding": key}), encoding="utf-8")
        bindings[key] = {
            "path": f"config/stage-learning/{name}",
            "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    authority = {
        "maximum_technical_recovery_jobs": 1,
        "same_p015_b_config_required": True,
        "same_readiness_artifact_required": True,
        "same_qualified_plan_required": True,
        "same_p014_review_ledger_required": True,
        "same_expected_additional_review_count_60_required": True,
        "same_all_matching_rows_included_required": True,
        "field_path_repair_only": True,
        "resource_change": False,
        "selection_predicate_change": False,
        "positive_count_stop": False,
        "semantic_sorting": False,
        "new_identity_discovery": False,
        "market_data_read": False,
        "semantic_values_read": False,
        "reference_opinions_read": False,
        "candidate_output_read": False,
        "threshold_basis_read": False,
        "selection_transport_read": False,
        "candidate_scoring": False,
        "preserved_2021_market_read": False,
        "2025_read": False,
        "daily_or_strategy": False,
        "trading": False,
    }
    auth = {
        "schema_version": "quanthus.stage-complete-reserve-additional-review-set-recovery.v1",
        "work_item": "P3-STAGE-015-B-R1",
        "disposition": "AUTHORIZE_ONE_TECHNICAL_BINDING_RECOVERY",
        "failed_job": {
            "job_id": failed_job.job_id,
            "code_revision": failed_job.code_revision,
            "input_digest": failed_job.input_digest,
            "terminal_state": "failed",
            "error": "stage_complete_reserve_additional_review_set_process_failed",
            "artifact_published": False,
            "runtime_artifact_read_reached": False,
            "semantic_read_reached": False,
        },
        "bindings": bindings,
        "authority": authority,
    }
    (config_root / "stage-complete-reserve-additional-review-set-recovery.v1.json").write_text(
        json.dumps(auth), encoding="utf-8"
    )

    recovery_revision = "b" * 40
    recovered = submit_job(
        worker_paths,
        code_revision=recovery_revision,
        kind=STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_SET_JOB,
    )
    assert recovered["technical_recovery_of"] == failed_job.job_id
    assert recovered["job_id"] != failed_job.job_id
    assert worker_paths.complete_reserve_additional_review_set_recovery_budget.is_file()
    repeated = submit_job(
        worker_paths,
        code_revision=recovery_revision,
        kind=STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_SET_JOB,
    )
    assert repeated["job_id"] == recovered["job_id"]
    with pytest.raises(StageWorkerError, match="has no further authority"):
        submit_job(
            worker_paths,
            code_revision="c" * 40,
            kind=STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_SET_JOB,
        )


def test_complete_reserve_additional_review_prepare_is_one_shot_and_idempotent(
    tmp_path: Path,
) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_PREPARE_JOB,
    )
    assert submitted["kind"] == STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_PREPARE_JOB
    assert submitted["config"] == (
        "config/stage-learning/stage-complete-reserve-additional-review-prepare.v1.json"
    )
    assert _JOB_SPECS[STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_PREPARE_JOB][
        "wall_seconds"
    ] == 3600
    assert worker_paths.complete_reserve_additional_review_prepare_budget.is_file()
    repeated = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_PREPARE_JOB,
    )
    assert repeated["job_id"] == submitted["job_id"]
    with pytest.raises(StageWorkerError, match="no retry or replacement authority"):
        submit_job(
            worker_paths,
            code_revision="b" * 40,
            kind=STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_PREPARE_JOB,
        )


def test_combined_atomic_scoring_is_one_shot_and_idempotent(tmp_path: Path) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_COMBINED_ATOMIC_SCORING_JOB,
    )
    assert submitted["kind"] == STAGE_COMBINED_ATOMIC_SCORING_JOB
    assert submitted["config"] == "config/stage-learning/stage-combined-atomic-scoring.v1.json"
    assert _JOB_SPECS[STAGE_COMBINED_ATOMIC_SCORING_JOB]["wall_seconds"] == 900
    assert worker_paths.combined_atomic_scoring_budget.is_file()
    repeated = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_COMBINED_ATOMIC_SCORING_JOB,
    )
    assert repeated["job_id"] == submitted["job_id"]
    with pytest.raises(StageWorkerError, match="no retry or replacement authority"):
        submit_job(
            worker_paths,
            code_revision="b" * 40,
            kind=STAGE_COMBINED_ATOMIC_SCORING_JOB,
        )


def test_stratum_normalized_instability_is_one_shot_and_idempotent(tmp_path: Path) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_STRATUM_NORMALIZED_INSTABILITY_JOB,
    )
    assert submitted["kind"] == STAGE_STRATUM_NORMALIZED_INSTABILITY_JOB
    assert submitted["config"] == (
        "config/stage-learning/stage-stratum-normalized-instability.v1.json"
    )
    assert _JOB_SPECS[STAGE_STRATUM_NORMALIZED_INSTABILITY_JOB]["wall_seconds"] == 900
    assert worker_paths.stratum_normalized_instability_budget.is_file()
    repeated = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_STRATUM_NORMALIZED_INSTABILITY_JOB,
    )
    assert repeated["job_id"] == submitted["job_id"]
    with pytest.raises(StageWorkerError, match="requires exact failed parent"):
        submit_job(
            worker_paths,
            code_revision="b" * 40,
            kind=STAGE_STRATUM_NORMALIZED_INSTABILITY_JOB,
        )


def test_stratum_normalized_instability_allows_one_exact_binding_recovery(
    tmp_path: Path,
) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_STRATUM_NORMALIZED_INSTABILITY_JOB,
    )
    marker = json.loads(
        worker_paths.stratum_normalized_instability_budget.read_text(encoding="utf-8")
    )
    failed_job = StageJob.from_dict(marker["job"])
    StageWorker(worker_paths)._write_status(
        failed_job,
        state="failed",
        stage="terminal",
        started_at="2026-09-15T03:45:51+00:00",
        completed_at="2026-09-15T03:45:52+00:00",
        error="stage_stratum_normalized_instability_process_failed",
        exit_code=1,
        result_ref=None,
    )
    seed_stratum_normalized_instability_recovery_auth(worker_paths, failed_job)
    recovery_revision = "b" * 40
    recovered = submit_job(
        worker_paths,
        code_revision=recovery_revision,
        kind=STAGE_STRATUM_NORMALIZED_INSTABILITY_JOB,
    )
    assert recovered["job_id"] != submitted["job_id"]
    assert recovered["technical_recovery_of"] == submitted["job_id"]
    assert worker_paths.stratum_normalized_instability_recovery_budget.is_file()
    repeated = submit_job(
        worker_paths,
        code_revision=recovery_revision,
        kind=STAGE_STRATUM_NORMALIZED_INSTABILITY_JOB,
    )
    assert repeated["job_id"] == recovered["job_id"]
    with pytest.raises(StageWorkerError, match="has no further authority"):
        submit_job(
            worker_paths,
            code_revision="c" * 40,
            kind=STAGE_STRATUM_NORMALIZED_INSTABILITY_JOB,
        )


def test_stratum_normalized_instability_status_exposes_bounded_diagnostics_only(
    tmp_path: Path,
) -> None:
    worker_paths = paths(tmp_path)
    worker_paths.ensure()
    job_id = "stage_" + "7" * 32
    artifact = worker_paths.runs / job_id / "stage-stratum-normalized-instability-test"
    artifact.mkdir(parents=True)
    pooled = {
        "predictions": 20,
        "true_positive": 12,
        "false_positive": 8,
        "precision": "0.6",
        "positive_reference_coverage": "0.3",
        "definite_negative_false_positive_rate": "0.05",
    }
    macro = {
        "precision": "0.55",
        "positive_reference_coverage": "0.28",
        "definite_negative_false_positive_rate": "0.04",
    }
    per_cell = {
        f"ERA{index % 2}/BOARD{index}": {
            "definite_positive_references": 5,
            "definite_negative_references": 20,
            "predictions": 3,
            "true_positive": 2,
            "false_positive": 1,
            "precision": "0.6666666666666666666666666667",
            "positive_reference_coverage": "0.4",
            "definite_negative_false_positive_rate": "0.05",
            "secret_threshold": "must-not-leak",
        }
        for index in range(8)
    }
    trials = [
        {
            "profile_id": f"profile-{index}",
            "expansion_profile_id": f"exp-{index // 3}",
            "broad_profile_id": f"broad-{index % 3}",
            "metrics": {"pooled": pooled, "macro": macro, "per_cell": per_cell},
            "eligible": False,
        }
        for index in range(9)
    ]
    score = {
        "schema_version": "quanthus.stage-stratum-normalized-instability-score.v1",
        "disposition": "REVISE_OR_PARK_STRATUM_NORMALIZED_INSTABILITY_FAIL",
        "reference_case_count": 252,
        "eligible_candidate_count": 0,
        "trials": trials,
    }
    score_raw = json.dumps(score, sort_keys=True, separators=(",", ":")).encode()
    (artifact / "instability-score.json").write_bytes(score_raw)
    manifest = {
        "schema_version": "quanthus.stage-stratum-normalized-instability-artifact.v1",
        "files": {
            "instability-score.json": "sha256:" + hashlib.sha256(score_raw).hexdigest()
        },
    }
    manifest_raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    manifest_sha = "sha256:" + hashlib.sha256(manifest_raw).hexdigest()
    (artifact / "manifest.json").write_bytes(manifest_raw)
    (artifact / "READY").write_text(manifest_sha + "\n", encoding="ascii")
    status = {
        "schema_version": "quanthus.stage-worker-status.v1",
        "job_id": job_id,
        "kind": STAGE_STRATUM_NORMALIZED_INSTABILITY_JOB,
        "code_revision": REVISION,
        "input_digest": _digest({"job": job_id}),
        "state": "succeeded",
        "stage": "terminal",
        "submitted_at": "2026-09-15T00:00:00+00:00",
        "started_at": "2026-09-15T00:00:01+00:00",
        "completed_at": "2026-09-15T00:00:02+00:00",
        "result_ref": {
            "artifact_root": str(artifact),
            "manifest_sha256": manifest_sha,
            "stratum_normalized_instability": {"disposition": score["disposition"]},
        },
        "error": None,
        "exit_code": 0,
        "updated_at": "2026-09-15T00:00:02+00:00",
    }
    (worker_paths.status / f"{job_id}.json").write_text(json.dumps(status), encoding="utf-8")

    observed = read_status(worker_paths, job_id)
    diagnostics = observed["stratum_normalized_instability_diagnostics"]
    assert diagnostics["disposition"] == score["disposition"]
    assert diagnostics["reference_case_count"] == 252
    assert diagnostics["eligible_candidate_count"] == 0
    assert len(diagnostics["trials"]) == 9
    assert diagnostics["cell_threshold_values_included"] is False
    assert diagnostics["case_predictions_included"] is False
    assert diagnostics["reference_rows_included"] is False
    first_metrics = diagnostics["trials"][0]["metrics"]
    assert len(first_metrics["per_cell"]) == 8
    assert first_metrics["per_cell"]["ERA0/BOARD0"]["true_positive"] == 2
    assert "secret_threshold" not in json.dumps(diagnostics)


def test_instability_channel_decomposition_status_exposes_bounded_diagnostics_only(
    tmp_path: Path,
) -> None:
    worker_paths = paths(tmp_path)
    worker_paths.ensure()
    job_id = "stage_" + "6" * 32
    artifact = worker_paths.runs / job_id / "stage-instability-channel-decomposition-test"
    artifact.mkdir(parents=True)
    metrics = {
        "pooled": {
            "predictions": 10,
            "true_positive": 6,
            "false_positive": 4,
            "precision": "0.6",
            "positive_reference_coverage": "0.5",
            "definite_negative_false_positive_rate": "0.1",
        },
        "macro": {
            "precision": "0.55",
            "positive_reference_coverage": "0.48",
            "definite_negative_false_positive_rate": "0.11",
        },
        "per_cell": {
            "ERA/BOARD": {
                "predictions": 2,
                "true_positive": 1,
                "false_positive": 1,
                "precision": "0.5",
                "positive_reference_coverage": "0.5",
                "definite_negative_false_positive_rate": "0.1",
                "secret_threshold": "must-not-leak",
            }
        },
    }
    profiles = [
        {
            "profile_id": f"profile-{index}",
            "expansion_profile_id": f"exp-{index // 3}",
            "broad_profile_id": f"broad-{index % 3}",
            "expanding_disorder_20": metrics,
            "broad_reversal_disorder_20": metrics,
            "parent_or": metrics,
            "attribution": {
                "expansion_true_count": 10,
                "broad_true_count": 8,
                "overlap_true_count": 3,
                "expansion_only_count": 7,
                "broad_only_count": 5,
                "neither_count": 240,
                "expansion_only_true_positive": 4,
                "expansion_only_false_positive": 3,
                "broad_only_true_positive": 2,
                "broad_only_false_positive": 3,
                "overlap_true_positive": 2,
                "overlap_false_positive": 1,
            },
        }
        for index in range(9)
    ]
    payload = {
        "schema_version": "quanthus.stage-instability-channel-decomposition-diagnostic.v1",
        "disposition": "PASS_CHANNEL_DECOMPOSITION_DIAGNOSTIC_COMPLETE",
        "reference_case_count": 252,
        "profile_count": 9,
        "profiles": profiles,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    (artifact / "channel-decomposition.json").write_bytes(raw)
    manifest = {
        "schema_version": "quanthus.stage-instability-channel-decomposition-artifact.v1",
        "files": {"channel-decomposition.json": "sha256:" + hashlib.sha256(raw).hexdigest()},
    }
    manifest_raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    manifest_sha = "sha256:" + hashlib.sha256(manifest_raw).hexdigest()
    (artifact / "manifest.json").write_bytes(manifest_raw)
    (artifact / "READY").write_text(manifest_sha + "\n", encoding="ascii")
    status = {
        "schema_version": "quanthus.stage-worker-status.v1",
        "job_id": job_id,
        "kind": STAGE_INSTABILITY_CHANNEL_DECOMPOSITION_JOB,
        "code_revision": REVISION,
        "input_digest": _digest({"job": job_id}),
        "state": "succeeded",
        "stage": "terminal",
        "submitted_at": "2026-09-15T00:00:00+00:00",
        "started_at": "2026-09-15T00:00:01+00:00",
        "completed_at": "2026-09-15T00:00:02+00:00",
        "result_ref": {
            "artifact_root": str(artifact),
            "manifest_sha256": manifest_sha,
            "instability_channel_decomposition": {"disposition": payload["disposition"]},
        },
        "error": None,
        "exit_code": 0,
        "updated_at": "2026-09-15T00:00:02+00:00",
    }
    (worker_paths.status / f"{job_id}.json").write_text(json.dumps(status), encoding="utf-8")

    observed = read_status(worker_paths, job_id)
    diagnostics = observed["instability_channel_decomposition_diagnostics"]
    assert diagnostics["profile_count"] == 9
    assert len(diagnostics["profiles"]) == 9
    assert diagnostics["threshold_values_included"] is False
    assert diagnostics["case_predictions_included"] is False
    assert diagnostics["reference_rows_included"] is False
    assert diagnostics["candidate_selection_performed"] is False
    assert diagnostics["profiles"][0]["attribution"]["expansion_only_false_positive"] == 3
    assert "secret_threshold" not in json.dumps(diagnostics)


def test_primary_unique_fallback_status_exposes_bounded_cell_diagnostics_only(
    tmp_path: Path,
) -> None:
    worker_paths = paths(tmp_path)
    worker_paths.ensure()
    job_id = "stage_" + "4" * 32
    artifact = worker_paths.runs / job_id / "stage-primary-unique-contingent-fallback-test"
    artifact.mkdir(parents=True)
    primary = {
        "rows": [{"security_id": "PRIMARY-SECRET"}],
        "cell_counts": {"ERA/A": 24},
    }
    fallback = {
        "rows": [{"security_id": "FALLBACK-SECRET"}],
        "cell_counts": {"ERA/A": 7},
        "cell_receipts": [
            {
                "cell": "ERA/A",
                "available_non_primary_identity_count": 7,
                "frozen_fallback_candidate_count": 7,
                "fallback_ceiling": 8,
                "sufficient": False,
                "selected_identity_digest": "sha256:" + "1" * 64,
            }
        ],
        "worst_case_simultaneous_activation": {
            "cell_activation_counts": {"ERA/A": 6}
        },
    }
    summary = {
        "disposition": "CLOSED_DORMANT_FALLBACK_POOL_SHORTFALL",
        "worst_case_simultaneous_fallback_activation_count": 6,
    }
    payloads = {
        "primary-plan.json": json.dumps(
            primary, sort_keys=True, separators=(",", ":")
        ).encode(),
        "fallback-pools.json": json.dumps(
            fallback, sort_keys=True, separators=(",", ":")
        ).encode(),
        "summary.json": json.dumps(summary, sort_keys=True, separators=(",", ":")).encode(),
    }
    for name, raw in payloads.items():
        (artifact / name).write_bytes(raw)
    manifest = {
        "files": {
            name: "sha256:" + hashlib.sha256(raw).hexdigest()
            for name, raw in payloads.items()
        }
    }
    manifest_raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    manifest_sha = "sha256:" + hashlib.sha256(manifest_raw).hexdigest()
    (artifact / "manifest.json").write_bytes(manifest_raw)
    (artifact / "READY").write_text(manifest_sha + "\n", encoding="ascii")
    status = {
        "schema_version": "quanthus.stage-worker-status.v1",
        "job_id": job_id,
        "kind": STAGE_PRIMARY_UNIQUE_CONTINGENT_FALLBACK_JOB,
        "code_revision": REVISION,
        "input_digest": _digest({"job": job_id}),
        "state": "succeeded",
        "stage": "terminal",
        "submitted_at": "2026-09-15T00:00:00+00:00",
        "started_at": "2026-09-15T00:00:01+00:00",
        "completed_at": "2026-09-15T00:00:02+00:00",
        "result_ref": {
            "artifact_root": str(artifact),
            "manifest_sha256": manifest_sha,
            "primary_unique_contingent_fallback": {"disposition": summary["disposition"]},
        },
        "error": None,
        "exit_code": 0,
        "updated_at": "2026-09-15T00:00:02+00:00",
    }
    (worker_paths.status / f"{job_id}.json").write_text(json.dumps(status), encoding="utf-8")

    observed = read_status(worker_paths, job_id)
    diagnostics = observed["primary_unique_contingent_fallback_diagnostics"]
    assert diagnostics["primary_cell_counts"] == {"ERA/A": 24}
    assert diagnostics["fallback_cell_counts"] == {"ERA/A": 7}
    assert diagnostics["fallback_cell_capacity"][0]["fallback_ceiling"] == 8
    assert diagnostics["fallback_cell_capacity"][0]["sufficient"] is False
    assert diagnostics["worst_case_fallback_cell_activation_counts"] == {"ERA/A": 6}
    assert diagnostics["security_ids_included"] is False
    assert diagnostics["windows_included"] is False
    rendered = json.dumps(diagnostics)
    assert "PRIMARY-SECRET" not in rendered
    assert "FALLBACK-SECRET" not in rendered


def test_instability_channel_decomposition_is_one_shot_and_idempotent(
    tmp_path: Path,
) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_INSTABILITY_CHANNEL_DECOMPOSITION_JOB,
    )
    assert submitted["kind"] == STAGE_INSTABILITY_CHANNEL_DECOMPOSITION_JOB
    assert submitted["config"] == (
        "config/stage-learning/stage-instability-channel-decomposition.v1.json"
    )
    assert _JOB_SPECS[STAGE_INSTABILITY_CHANNEL_DECOMPOSITION_JOB]["wall_seconds"] == 900
    assert worker_paths.instability_channel_decomposition_budget.is_file()
    repeated = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_INSTABILITY_CHANNEL_DECOMPOSITION_JOB,
    )
    assert repeated["job_id"] == submitted["job_id"]
    with pytest.raises(StageWorkerError, match="no retry or replacement authority"):
        submit_job(
            worker_paths,
            code_revision="b" * 40,
            kind=STAGE_INSTABILITY_CHANNEL_DECOMPOSITION_JOB,
        )


def test_downside_lifecycle_resource_plan_is_one_shot_and_idempotent(
    tmp_path: Path,
) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_DOWNSIDE_LIFECYCLE_RESOURCE_PLAN_JOB,
    )
    assert submitted["kind"] == STAGE_DOWNSIDE_LIFECYCLE_RESOURCE_PLAN_JOB
    assert submitted["config"] == (
        "config/stage-learning/stage-downside-lifecycle-resource-plan.v1.json"
    )
    assert _JOB_SPECS[STAGE_DOWNSIDE_LIFECYCLE_RESOURCE_PLAN_JOB]["wall_seconds"] == 3600
    assert worker_paths.downside_lifecycle_resource_plan_budget.is_file()
    repeated = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_DOWNSIDE_LIFECYCLE_RESOURCE_PLAN_JOB,
    )
    assert repeated["job_id"] == submitted["job_id"]
    with pytest.raises(StageWorkerError, match="recovery requires exact failed parent"):
        submit_job(
            worker_paths,
            code_revision="b" * 40,
            kind=STAGE_DOWNSIDE_LIFECYCLE_RESOURCE_PLAN_JOB,
        )


def test_downside_lifecycle_resource_plan_allows_one_exact_pre_admission_recovery(
    tmp_path: Path,
) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_DOWNSIDE_LIFECYCLE_RESOURCE_PLAN_JOB,
    )
    marker = json.loads(
        worker_paths.downside_lifecycle_resource_plan_budget.read_text(encoding="utf-8")
    )
    failed_job = StageJob.from_dict(marker["job"])
    StageWorker(worker_paths)._write_status(
        failed_job,
        state="failed",
        stage="terminal",
        started_at="2026-09-15T10:49:55+00:00",
        completed_at="2026-09-15T10:49:56+00:00",
        error="stage_downside_lifecycle_resource_plan_process_failed",
        exit_code=1,
        result_ref=None,
    )
    seed_downside_lifecycle_resource_plan_recovery_auth(worker_paths, failed_job)
    recovery_revision = "b" * 40
    recovered = submit_job(
        worker_paths,
        code_revision=recovery_revision,
        kind=STAGE_DOWNSIDE_LIFECYCLE_RESOURCE_PLAN_JOB,
    )
    assert recovered["job_id"] != submitted["job_id"]
    assert recovered["technical_recovery_of"] == submitted["job_id"]
    assert worker_paths.downside_lifecycle_resource_plan_recovery_budget.is_file()
    repeated = submit_job(
        worker_paths,
        code_revision=recovery_revision,
        kind=STAGE_DOWNSIDE_LIFECYCLE_RESOURCE_PLAN_JOB,
    )
    assert repeated["job_id"] == recovered["job_id"]
    with pytest.raises(StageWorkerError, match="has no further authority"):
        submit_job(
            worker_paths,
            code_revision="c" * 40,
            kind=STAGE_DOWNSIDE_LIFECYCLE_RESOURCE_PLAN_JOB,
        )


def test_admission_exposure_capacity_is_one_shot_and_idempotent(
    tmp_path: Path,
) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_ADMISSION_EXPOSURE_CAPACITY_JOB,
    )
    assert submitted["kind"] == STAGE_ADMISSION_EXPOSURE_CAPACITY_JOB
    assert submitted["config"] == (
        "config/stage-learning/stage-admission-exposure-capacity.v1.json"
    )
    assert _JOB_SPECS[STAGE_ADMISSION_EXPOSURE_CAPACITY_JOB]["wall_seconds"] == 600
    assert worker_paths.admission_exposure_capacity_budget.is_file()
    repeated = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_ADMISSION_EXPOSURE_CAPACITY_JOB,
    )
    assert repeated["job_id"] == submitted["job_id"]
    with pytest.raises(StageWorkerError, match="no retry or replacement authority"):
        submit_job(
            worker_paths,
            code_revision="b" * 40,
            kind=STAGE_ADMISSION_EXPOSURE_CAPACITY_JOB,
        )


def test_primary_unique_contingent_fallback_is_one_shot_and_idempotent(
    tmp_path: Path,
) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_PRIMARY_UNIQUE_CONTINGENT_FALLBACK_JOB,
    )
    assert submitted["kind"] == STAGE_PRIMARY_UNIQUE_CONTINGENT_FALLBACK_JOB
    assert submitted["config"] == (
        "config/stage-learning/stage-primary-unique-contingent-fallback-feasibility.v1.json"
    )
    assert _JOB_SPECS[STAGE_PRIMARY_UNIQUE_CONTINGENT_FALLBACK_JOB]["wall_seconds"] == 900
    assert worker_paths.primary_unique_contingent_fallback_budget.is_file()
    repeated = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_PRIMARY_UNIQUE_CONTINGENT_FALLBACK_JOB,
    )
    assert repeated["job_id"] == submitted["job_id"]
    with pytest.raises(StageWorkerError, match="no retry or replacement authority"):
        submit_job(
            worker_paths,
            code_revision="b" * 40,
            kind=STAGE_PRIMARY_UNIQUE_CONTINGENT_FALLBACK_JOB,
        )


def test_realized_identity_admission_is_one_shot_and_idempotent(tmp_path: Path) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_REALIZED_IDENTITY_ADMISSION_JOB,
    )
    assert submitted["kind"] == STAGE_REALIZED_IDENTITY_ADMISSION_JOB
    assert submitted["config"] == (
        "config/stage-learning/stage-realized-identity-admission.v1.json"
    )
    assert _JOB_SPECS[STAGE_REALIZED_IDENTITY_ADMISSION_JOB]["wall_seconds"] == 3600
    assert worker_paths.realized_identity_admission_budget.is_file()
    repeated = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_REALIZED_IDENTITY_ADMISSION_JOB,
    )
    assert repeated["job_id"] == submitted["job_id"]
    with pytest.raises(StageWorkerError, match="no retry or replacement authority"):
        submit_job(
            worker_paths,
            code_revision="b" * 40,
            kind=STAGE_REALIZED_IDENTITY_ADMISSION_JOB,
        )


def test_clustered_identity_admission_is_one_shot_and_idempotent(tmp_path: Path) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_CLUSTERED_IDENTITY_ADMISSION_JOB,
    )
    assert submitted["kind"] == STAGE_CLUSTERED_IDENTITY_ADMISSION_JOB
    assert submitted["config"] == (
        "config/stage-learning/stage-clustered-identity-admission.v1.json"
    )
    assert _JOB_SPECS[STAGE_CLUSTERED_IDENTITY_ADMISSION_JOB]["wall_seconds"] == 1800
    assert worker_paths.clustered_identity_admission_budget.is_file()
    repeated = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_CLUSTERED_IDENTITY_ADMISSION_JOB,
    )
    assert repeated["job_id"] == submitted["job_id"]
    with pytest.raises(StageWorkerError, match="no retry or replacement authority"):
        submit_job(
            worker_paths,
            code_revision="b" * 40,
            kind=STAGE_CLUSTERED_IDENTITY_ADMISSION_JOB,
        )


def test_downside_lifecycle_reference_prepare_is_one_shot_and_idempotent(
    tmp_path: Path,
) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_DOWNSIDE_LIFECYCLE_REFERENCE_PREPARE_JOB,
    )
    assert submitted["kind"] == STAGE_DOWNSIDE_LIFECYCLE_REFERENCE_PREPARE_JOB
    assert submitted["config"] == (
        "config/stage-learning/stage-downside-lifecycle-blind-reference.v1.json"
    )
    assert _JOB_SPECS[STAGE_DOWNSIDE_LIFECYCLE_REFERENCE_PREPARE_JOB]["wall_seconds"] == 3600
    assert worker_paths.downside_lifecycle_reference_prepare_budget.is_file()
    repeated = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_DOWNSIDE_LIFECYCLE_REFERENCE_PREPARE_JOB,
    )
    assert repeated["job_id"] == submitted["job_id"]
    with pytest.raises(StageWorkerError, match="no retry or replacement authority"):
        submit_job(
            worker_paths,
            code_revision="b" * 40,
            kind=STAGE_DOWNSIDE_LIFECYCLE_REFERENCE_PREPARE_JOB,
        )


def test_combined_atomic_scoring_status_exposes_bounded_diagnostics_only(
    tmp_path: Path,
) -> None:
    worker_paths = paths(tmp_path)
    worker_paths.ensure()
    job_id = "stage_" + "8" * 32
    artifact = worker_paths.runs / job_id / "stage-combined-atomic-scoring-test"
    artifact.mkdir(parents=True)
    pooled = {
        "predictions": 20,
        "true_positive": 12,
        "false_positive": 8,
        "precision": "0.6",
        "positive_reference_coverage": "0.3",
        "definite_negative_false_positive_rate": "0.05",
    }
    macro = {
        "precision": "0.55",
        "positive_reference_coverage": "0.28",
        "definite_negative_false_positive_rate": "0.04",
    }
    per_cell = {
        f"ERA{index % 2}/BOARD{index}": {
            "definite_positive_references": 5,
            "definite_negative_references": 20,
            "predictions": 3,
            "true_positive": 2,
            "false_positive": 1,
            "precision": "0.6666666666666666666666666667",
            "positive_reference_coverage": "0.4",
            "definite_negative_false_positive_rate": "0.05",
            "secret_case_level_value": "must-not-leak",
        }
        for index in range(8)
    }
    trials = []
    for index in range(9):
        trials.append(
            {
                "candidate": {
                    "candidate_id": "sha256:" + f"{index:064x}"[-64:],
                    "expansion_profile_id": f"exp-{index // 3}",
                    "broad_profile_id": f"broad-{index % 3}",
                    "secret_threshold": "must-not-leak",
                },
                "metrics": {"pooled": pooled, "macro": macro, "per_cell": per_cell},
                "base_metrics": {"pooled": pooled, "macro": macro, "per_cell": per_cell},
                "incremental_true_positive": 1,
                "incremental_false_positive": 0,
                "eligible": False,
            }
        )
    score = {
        "schema_version": "quanthus.stage-combined-atomic-scoring-result.v1",
        "disposition": "REVISE_OR_PARK_INSTABILITY_DEVELOPMENT_FAIL",
        "reference_case_count": 252,
        "instability_trials": trials,
    }
    score_raw = json.dumps(score, sort_keys=True, separators=(",", ":")).encode()
    (artifact / "atomic-score.json").write_bytes(score_raw)
    manifest = {
        "schema_version": "quanthus.stage-combined-atomic-scoring-artifact.v1",
        "files": {"atomic-score.json": "sha256:" + hashlib.sha256(score_raw).hexdigest()},
    }
    manifest_raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    manifest_sha = "sha256:" + hashlib.sha256(manifest_raw).hexdigest()
    (artifact / "manifest.json").write_bytes(manifest_raw)
    (artifact / "READY").write_text(manifest_sha + "\n", encoding="ascii")
    status = {
        "schema_version": "quanthus.stage-worker-status.v1",
        "job_id": job_id,
        "kind": STAGE_COMBINED_ATOMIC_SCORING_JOB,
        "code_revision": REVISION,
        "input_digest": _digest({"job": job_id}),
        "state": "succeeded",
        "stage": "terminal",
        "submitted_at": "2026-09-15T00:00:00+00:00",
        "started_at": "2026-09-15T00:00:01+00:00",
        "completed_at": "2026-09-15T00:00:02+00:00",
        "result_ref": {
            "artifact_root": str(artifact),
            "manifest_sha256": manifest_sha,
            "combined_atomic_scoring": {"disposition": score["disposition"]},
        },
        "error": None,
        "exit_code": 0,
        "updated_at": "2026-09-15T00:00:02+00:00",
    }
    (worker_paths.status / f"{job_id}.json").write_text(json.dumps(status), encoding="utf-8")

    observed = read_status(worker_paths, job_id)
    diagnostics = observed["scoring_diagnostics"]
    assert diagnostics["disposition"] == score["disposition"]
    assert diagnostics["reference_case_count"] == 252
    assert len(diagnostics["instability_trials"]) == 9
    assert diagnostics["candidate_threshold_values_included"] is False
    assert diagnostics["case_predictions_included"] is False
    assert diagnostics["reference_rows_included"] is False
    first_metrics = diagnostics["instability_trials"][0]["metrics"]
    assert len(first_metrics["per_cell"]) == 8
    assert first_metrics["per_cell"]["ERA0/BOARD0"]["true_positive"] == 2
    assert "secret_threshold" not in json.dumps(diagnostics)
    assert "secret_case_level_value" not in json.dumps(diagnostics)


def test_complete_reserve_additional_ready_transport_exposes_metadata_only(
    tmp_path: Path,
) -> None:
    worker_paths = paths(tmp_path)
    worker_paths.ensure()
    job_id = "stage_" + "e" * 32
    rows = []
    for index in range(256):
        old = index < 192
        new = 192 <= index < 252
        ready = index < 252
        rows.append(
            {
                "security_id": f"security_{index:04d}",
                "window_id": f"WINDOW|{index:04d}",
                "era": "PRE_STAR" if index % 2 == 0 else "STAR_PRE_CHINEXT_20PCT",
                "board_family": "MAIN" if index % 3 == 0 else "CHINEXT",
                "cell": f"CELL-{index % 8}",
                "review_packet_ready": ready,
                "readiness_reason": "READY" if ready else "PRIMARY_FACTS_UNAVAILABLE",
                "source_member_closure_digest": "sha256:" + f"{index:064x}"[-64:],
                "already_reviewed_in_p014_ledger": old,
                "new_review_required": new,
            }
        )
    ledger = {
        "schema_version": "quanthus.stage-complete-reserve-review-readiness-ledger.v1",
        "rows": rows,
        "row_count": 256,
        "ready_count": 252,
        "unready_count": 4,
        "ready_by_cell": {f"CELL-{index}": 31 if index < 4 else 32 for index in range(8)},
        "reason_counts": {"PRIMARY_FACTS_UNAVAILABLE": 4, "READY": 252},
        "already_reviewed_ready_count": 192,
        "new_review_required_count": 60,
        "ledger_digest": _digest(rows),
        "semantic_values_retained": False,
    }
    artifact = worker_paths.runs / job_id / "stage-complete-reserve-review-readiness-test"
    artifact.mkdir(parents=True)
    readiness_raw = json.dumps(ledger, sort_keys=True, separators=(",", ":")).encode()
    (artifact / "review-readiness.json").write_bytes(readiness_raw)
    source_raw = b"{}"
    summary_raw = b"{}"
    (artifact / "source-binding.json").write_bytes(source_raw)
    (artifact / "summary.json").write_bytes(summary_raw)
    files = {
        "review-readiness.json": "sha256:" + hashlib.sha256(readiness_raw).hexdigest(),
        "source-binding.json": "sha256:" + hashlib.sha256(source_raw).hexdigest(),
        "summary.json": "sha256:" + hashlib.sha256(summary_raw).hexdigest(),
    }
    manifest = {"files": files}
    manifest_raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    manifest_sha = "sha256:" + hashlib.sha256(manifest_raw).hexdigest()
    (artifact / "manifest.json").write_bytes(manifest_raw)
    (artifact / "READY").write_text(manifest_sha + "\n", encoding="ascii")
    observed = {
        "job_id": job_id,
        "kind": STAGE_COMPLETE_RESERVE_REVIEW_READINESS_JOB,
        "state": "succeeded",
        "result_ref": {
            "artifact_root": str(artifact),
            "manifest_sha256": manifest_sha,
            "complete_reserve_review_readiness": {
                "continuation_pass": True,
                "ledger_digest": _digest(rows),
                "ready_count": 252,
                "unready_count": 4,
                "already_reviewed_ready_count": 192,
                "new_review_required_count": 60,
            },
        },
    }
    transport = _complete_reserve_additional_ready_transport(worker_paths, observed)
    assert transport is not None
    assert transport["new_review_required_count"] == 60
    assert len(transport["rows"]) == 60
    assert transport["rows"][0]["security_id"] == "security_0192"
    assert set(transport["rows"][0]) == {
        "security_id",
        "window_id",
        "era",
        "board_family",
        "cell",
        "source_member_closure_digest",
    }
    assert transport["semantic_values_included"] is False
    assert transport["reference_opinions_included"] is False
    assert transport["candidate_or_threshold_output_included"] is False


def test_second_group_profile_kind_gets_its_frozen_config(tmp_path: Path) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_SECOND_GROUP_PROFILE_JOB,
    )
    assert submitted["kind"] == STAGE_SECOND_GROUP_PROFILE_JOB
    assert submitted["config"] == "config/stage-learning/second-group-profile.v1.json"
    assert _JOB_SPECS[STAGE_SECOND_GROUP_PROFILE_JOB]["wall_seconds"] == 1800
    assert not worker_paths.qualification_budget.exists()


def test_second_group_repair_profile_kind_gets_its_frozen_config(tmp_path: Path) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_SECOND_GROUP_REPAIR_PROFILE_JOB,
    )
    assert submitted["kind"] == STAGE_SECOND_GROUP_REPAIR_PROFILE_JOB
    assert submitted["config"] == "config/stage-learning/second-group-repair-profile.v1.json"
    assert _JOB_SPECS[STAGE_SECOND_GROUP_REPAIR_PROFILE_JOB]["wall_seconds"] == 1800
    assert not worker_paths.qualification_budget.exists()


def test_second_group_development_kind_gets_its_frozen_config(tmp_path: Path) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_SECOND_GROUP_DEVELOPMENT_JOB,
    )
    assert submitted["kind"] == STAGE_SECOND_GROUP_DEVELOPMENT_JOB
    assert submitted["config"] == "config/stage-learning/second-group-development.v1.json"
    assert _JOB_SPECS[STAGE_SECOND_GROUP_DEVELOPMENT_JOB]["wall_seconds"] == 2700
    assert not worker_paths.qualification_budget.exists()


def test_second_group_development_supplement_kind_gets_its_frozen_config(
    tmp_path: Path,
) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_SECOND_GROUP_DEVELOPMENT_SUPPLEMENT_JOB,
    )
    assert submitted["kind"] == STAGE_SECOND_GROUP_DEVELOPMENT_SUPPLEMENT_JOB
    assert submitted["config"] == (
        "config/stage-learning/second-group-development-supplement.v1.json"
    )
    assert _JOB_SPECS[STAGE_SECOND_GROUP_DEVELOPMENT_SUPPLEMENT_JOB]["wall_seconds"] == 2700
    assert not worker_paths.qualification_budget.exists()


def test_third_group_profile_kind_gets_its_frozen_config(tmp_path: Path) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_THIRD_GROUP_PROFILE_JOB,
    )
    assert submitted["kind"] == STAGE_THIRD_GROUP_PROFILE_JOB
    assert submitted["config"] == "config/stage-learning/third-group-profile.v1.json"
    assert _JOB_SPECS[STAGE_THIRD_GROUP_PROFILE_JOB]["wall_seconds"] == 1800
    assert not worker_paths.qualification_budget.exists()


def test_order_proposition_development_kind_gets_its_frozen_config(tmp_path: Path) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_ORDER_PROPOSITION_DEVELOPMENT_JOB,
    )
    assert submitted["kind"] == STAGE_ORDER_PROPOSITION_DEVELOPMENT_JOB
    assert submitted["config"] == "config/stage-learning/order-proposition-development.v1.json"
    assert _JOB_SPECS[STAGE_ORDER_PROPOSITION_DEVELOPMENT_JOB]["wall_seconds"] == 2700
    assert not worker_paths.qualification_budget.exists()


def test_order_rule_prepare_kind_gets_its_frozen_config(tmp_path: Path) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_ORDER_RULE_PREPARE_JOB,
    )
    assert submitted["kind"] == STAGE_ORDER_RULE_PREPARE_JOB
    assert submitted["config"] == "config/stage-learning/order-rule-prepare.v1.json"
    assert _JOB_SPECS[STAGE_ORDER_RULE_PREPARE_JOB]["wall_seconds"] == 2700
    assert not worker_paths.qualification_budget.exists()


def test_order_rule_prepare_submit_is_single_job_idempotent(tmp_path: Path) -> None:
    worker_paths = paths(tmp_path)
    first = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_ORDER_RULE_PREPARE_JOB,
    )
    second = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_ORDER_RULE_PREPARE_JOB,
    )
    assert second["job_id"] == first["job_id"]
    assert second["state"] == "queued"
    assert second["idempotent_reuse"] is True
    assert len(list(worker_paths.queue.glob("stage_*.json"))) == 1
    marker = json.loads(worker_paths.order_rule_prepare_budget.read_text(encoding="utf-8"))
    assert marker["job"]["job_id"] == first["job_id"]
    assert marker["input_digest"] == first["input_digest"]

    with pytest.raises(StageWorkerError, match="another immutable input"):
        submit_job(
            worker_paths,
            code_revision="c" * 40,
            kind=STAGE_ORDER_RULE_PREPARE_JOB,
        )


def _write_prepare_retry_authorization(worker_paths: StageWorkerPaths, first: dict) -> None:
    prepare = {
        "bindings": {
            "rule_digest": "sha256:" + "1" * 64,
            "validation_digest": "sha256:" + "2" * 64,
        }
    }
    prepare_path = worker_paths.control_repo / "config/stage-learning/order-rule-prepare.v1.json"
    prepare_path.parent.mkdir(parents=True, exist_ok=True)
    prepare_path.write_text(json.dumps(prepare), encoding="utf-8")
    fix_path = worker_paths.control_repo / "src/quanthus/stage_learning/order_rule_validation.py"
    fix_path.parent.mkdir(parents=True, exist_ok=True)
    fix_path.write_text("technical-fix\n", encoding="utf-8")
    fix_sha = "sha256:" + hashlib.sha256(fix_path.read_bytes()).hexdigest()
    authorization = {
        "schema_version": "quanthus.stage-order-rule-prepare-retry.v1",
        "work_item": "P3-STAGE-007-B",
        "failure": {
            "job_id": first["job_id"],
            "code_revision": first["code_revision"],
            "input_digest": first["input_digest"],
            "terminal_state": "failed",
            "worker_error": "stage_order_rule_prepare_process_failed",
            "root_cause": "post_selection_sampling_coordinate_canonicalization",
            "failed_check": "selected_observation_differs_from_retained_evidence",
        },
        "authorization": {
            "maximum_technical_retries": 1,
            "counts_against_result_bearing_job_budget": False,
            "same_prepare_config_required": True,
            "same_rule_digest_required": True,
            "same_validation_digest_required": True,
            "same_source_scope_required": True,
            "same_sampling_algorithm_required": True,
            "same_sampling_seed_required": True,
            "opinion_generation": False,
            "candidate_scoring": False,
            "parameter_search": False,
            "future_outcome_access": False,
            "arbitration_access": False,
            "permanent_blind_access": False,
            "official_labels": False,
            "daily_or_strategy": False,
            "trading": False,
        },
        "bindings": {
            "prepare_config_digest": _digest(prepare),
            "rule_digest": prepare["bindings"]["rule_digest"],
            "validation_digest": prepare["bindings"]["validation_digest"],
            "fix_path": "src/quanthus/stage_learning/order_rule_validation.py",
            "fix_file_sha256": fix_sha,
        },
    }
    retry_path = (
        worker_paths.control_repo / "config/stage-learning/order-rule-prepare-retry.v1.json"
    )
    retry_path.write_text(json.dumps(authorization), encoding="utf-8")


def test_order_rule_prepare_allows_exactly_one_bound_technical_retry(tmp_path: Path) -> None:
    worker_paths = paths(tmp_path)
    first = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_ORDER_RULE_PREPARE_JOB,
    )
    job = StageJob.from_dict(json.loads(
        (worker_paths.queue / f"{first['job_id']}.json").read_text(encoding="utf-8")
    ))
    (worker_paths.queue / f"{first['job_id']}.json").unlink()
    StageWorker(worker_paths)._write_status(
        job,
        state="failed",
        stage="terminal",
        started_at="2026-09-11T05:22:47+00:00",
        completed_at="2026-09-11T05:28:35+00:00",
        error="stage_order_rule_prepare_process_failed",
        exit_code=1,
    )
    _write_prepare_retry_authorization(worker_paths, first)

    retry_revision = "c" * 40
    retry = submit_job(
        worker_paths,
        code_revision=retry_revision,
        kind=STAGE_ORDER_RULE_PREPARE_JOB,
    )
    assert retry["job_id"] != first["job_id"]
    assert retry["technical_retry_of"] == first["job_id"]
    assert retry["state"] == "queued"
    repeated = submit_job(
        worker_paths,
        code_revision=retry_revision,
        kind=STAGE_ORDER_RULE_PREPARE_JOB,
    )
    assert repeated["job_id"] == retry["job_id"]
    assert repeated["idempotent_reuse"] is True
    assert repeated["technical_retry_of"] == first["job_id"]
    with pytest.raises(StageWorkerError, match="retry budget is already consumed"):
        submit_job(
            worker_paths,
            code_revision="d" * 40,
            kind=STAGE_ORDER_RULE_PREPARE_JOB,
        )


def test_order_rule_prepare_cancel_does_not_open_a_second_job(tmp_path: Path) -> None:
    worker_paths = paths(tmp_path)
    first = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_ORDER_RULE_PREPARE_JOB,
    )
    canceled = cancel_job(worker_paths, first["job_id"])
    assert canceled["state"] == "canceled"
    repeated = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_ORDER_RULE_PREPARE_JOB,
    )
    assert repeated["job_id"] == first["job_id"]
    assert repeated["state"] == "canceled"
    assert repeated["idempotent_reuse"] is True


def test_successful_prepare_status_exposes_bounded_t_only_review_transport(tmp_path: Path) -> None:
    worker_paths = paths(tmp_path)
    worker_paths.ensure()
    job_id = "stage_" + "9" * 32
    row = {
        "market_date": "2021-03-31",
        "high": "101",
        "low": "99",
        "close": "100",
        "coordinate_epoch": "original-price-quantity-epoch",
        "reset_reason": None,
    }
    row_ref = _digest(row)
    case_body = {
        "security_code": "000001",
        "security_id": "security-A",
        "market_date": "2021-03-31",
        "sampling_stratum": "representative",
        "coordinate_ref": _digest({"coordinate": "A"}),
        "primary_available": True,
        "recent_available": True,
        "input_reason": "admitted",
        "availability_reasons": {"trend": "available", "order": "available"},
        "primary": {"directional_efficiency": {"value": "-0.2"}},
        "recent": {"width_change": {"value": "-0.1"}},
        "history_refs": [row_ref],
    }
    case = {"case_id": _digest(case_body), **case_body}
    evidence = {
        "protocol": {"profile": {"primary_window_sessions": 20, "recent_window_sessions": 10}},
        "source_rows": {row_ref: row},
        "cases": [case],
    }
    evidence_raw = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    evidence_sha = "sha256:" + hashlib.sha256(evidence_raw).hexdigest()
    artifact = worker_paths.runs / job_id / "stage-order-rule-prepare-test"
    artifact.mkdir(parents=True)
    (artifact / "evidence.json").write_bytes(evidence_raw)
    manifest = {"files": {"evidence.json": evidence_sha}}
    manifest_raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    manifest_sha = "sha256:" + hashlib.sha256(manifest_raw).hexdigest()
    (artifact / "manifest.json").write_bytes(manifest_raw)
    (artifact / "READY").write_text(manifest_sha + "\n", encoding="ascii")
    status = {
        "schema_version": "quanthus.stage-worker-status.v1",
        "job_id": job_id,
        "kind": STAGE_ORDER_RULE_PREPARE_JOB,
        "code_revision": REVISION,
        "input_digest": _digest({"job": job_id}),
        "state": "succeeded",
        "stage": "terminal",
        "submitted_at": "2026-09-11T00:00:00+00:00",
        "started_at": "2026-09-11T00:00:01+00:00",
        "completed_at": "2026-09-11T00:00:02+00:00",
        "result_ref": {
            "artifact_root": str(artifact),
            "manifest_sha256": manifest_sha,
            "order_rule_prepare": {"selected_case_count": 1},
        },
        "error": None,
        "exit_code": 0,
        "updated_at": "2026-09-11T00:00:02+00:00",
    }
    (worker_paths.status / f"{job_id}.json").write_text(json.dumps(status), encoding="utf-8")
    observed = read_status(worker_paths, job_id)
    index = observed["review_transport_index"]
    assert index["case_count"] == 1
    assert index["page_count"] == 1
    assert index["candidate_output_included"] is False
    assert index["future_rows_included"] is False
    alias = index["pages"][0]["job_id"]
    page_status = read_status(worker_paths, alias)
    assert page_status["parent_job_id"] == job_id
    assert page_status["stage"] == "virtual_read_only"
    envelope = page_status["review_transport"]
    raw = gzip.decompress(base64.b64decode(envelope["payload"]))
    decoded = json.loads(raw)
    assert decoded["case_count"] == 1
    assert decoded["candidate_output_included"] is False
    assert decoded["future_rows_included"] is False
    assert decoded["cases"][0]["history"] == [[
        row_ref, "2021-03-31", "101", "99", "100",
        "original-price-quantity-epoch", None,
    ]]
    assert "history_refs" not in decoded["cases"][0]


def test_directional_boundary_d1_is_single_job_with_no_retry(tmp_path: Path) -> None:
    worker_paths = paths(tmp_path)
    first = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_DIRECTIONAL_BOUNDARY_D1_JOB,
    )
    second = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_DIRECTIONAL_BOUNDARY_D1_JOB,
    )
    assert first["kind"] == STAGE_DIRECTIONAL_BOUNDARY_D1_JOB
    assert first["config"] == (
        "config/stage-learning/directional-boundary-development-screen.v1.json"
    )
    assert _JOB_SPECS[STAGE_DIRECTIONAL_BOUNDARY_D1_JOB]["wall_seconds"] == 2700
    assert second["job_id"] == first["job_id"]
    assert second["idempotent_reuse"] is True
    marker = json.loads(worker_paths.directional_boundary_d1_budget.read_text(encoding="utf-8"))
    assert marker["job"]["job_id"] == first["job_id"]
    assert marker["input_digest"] == first["input_digest"]
    with pytest.raises(StageWorkerError, match="no retry"):
        submit_job(
            worker_paths,
            code_revision="c" * 40,
            kind=STAGE_DIRECTIONAL_BOUNDARY_D1_JOB,
        )


def test_directional_boundary_d1_status_exposes_primary_only_review_transport(
    tmp_path: Path,
) -> None:
    worker_paths = paths(tmp_path)
    worker_paths.ensure()
    job_id = "stage_" + "8" * 32
    source_rows = {}
    history_refs = []
    for index in range(21):
        row = {
            "market_date": f"2020-03-{index + 1:02d}",
            "high": str(101 + index),
            "low": str(99 + index),
            "close": str(100 + index),
            "coordinate_epoch": "original-price-quantity-epoch",
            "reset_reason": None,
        }
        ref = _digest(row)
        source_rows[ref] = row
        history_refs.append(ref)
    primary = {
        "directional_efficiency": {"value": "0.25", "reason": "available"},
        "range_width": {"value": "0.30", "reason": "available"},
        "width_change": {"value": "0.25", "reason": "available"},
        "center_shift": {"value": "0.10", "reason": "available"},
    }
    case_body = {
        "security_code": "000001",
        "security_id": "security-fresh-A",
        "market_date": "2020-03-21",
        "sampling_stratum": "enriched",
        "coordinate_ref": _digest({"coordinate": "fresh-A"}),
        "primary_available": True,
        "recent_available": True,
        "input_reason": "admitted",
        "availability_reasons": {"trend": "available", "order": "available"},
        "primary": primary,
        "recent": {"width_change": {"value": "0.1"}, "center_shift": {"value": "0.1"}},
        "history_refs": history_refs,
    }
    case = {"case_id": _digest(case_body), **case_body}
    evidence = {"source_rows": source_rows, "cases": [case]}
    evidence_raw = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    evidence_sha = "sha256:" + hashlib.sha256(evidence_raw).hexdigest()
    path_facts = {
        "cases": {
            case["case_id"]: {
                "structure.geometry.range-traversal": {
                    "value": "1", "reason": "available", "revision_id": _digest({"p": 1}),
                },
                "structure.geometry.counter-direction-excursion-share": {
                    "value": "0", "reason": "available", "revision_id": _digest({"p": 2}),
                },
            }
        }
    }
    path_raw = json.dumps(path_facts, sort_keys=True, separators=(",", ":")).encode()
    path_sha = "sha256:" + hashlib.sha256(path_raw).hexdigest()
    artifact = worker_paths.runs / job_id / "stage-directional-boundary-d1-test"
    artifact.mkdir(parents=True)
    (artifact / "evidence.json").write_bytes(evidence_raw)
    (artifact / "path-facts.json").write_bytes(path_raw)
    manifest = {"files": {"evidence.json": evidence_sha, "path-facts.json": path_sha}}
    manifest_raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    manifest_sha = "sha256:" + hashlib.sha256(manifest_raw).hexdigest()
    (artifact / "manifest.json").write_bytes(manifest_raw)
    (artifact / "READY").write_text(manifest_sha + "\n", encoding="ascii")
    status = {
        "schema_version": "quanthus.stage-worker-status.v1",
        "job_id": job_id,
        "kind": STAGE_DIRECTIONAL_BOUNDARY_D1_JOB,
        "code_revision": REVISION,
        "input_digest": _digest({"job": job_id}),
        "state": "succeeded",
        "stage": "terminal",
        "submitted_at": "2026-09-12T00:00:00+00:00",
        "started_at": "2026-09-12T00:00:01+00:00",
        "completed_at": "2026-09-12T00:00:02+00:00",
        "result_ref": {
            "artifact_root": str(artifact),
            "manifest_sha256": manifest_sha,
            "directional_boundary_d1": {"calibration_case_count": 1, "review_ready": True},
        },
        "error": None,
        "exit_code": 0,
        "updated_at": "2026-09-12T00:00:02+00:00",
    }
    (worker_paths.status / f"{job_id}.json").write_text(json.dumps(status), encoding="utf-8")
    observed = read_status(worker_paths, job_id)
    index = observed["review_transport_index"]
    assert index["case_count"] == 1
    assert index["explicit_recent_derived_facts_included"] is False
    assert index["candidate_output_included"] is False
    assert index["future_rows_included"] is False
    page_status = read_status(worker_paths, index["pages"][0]["job_id"])
    envelope = page_status["review_transport"]
    decoded = json.loads(gzip.decompress(base64.b64decode(envelope["payload"])))
    review_case = decoded["cases"][0]
    assert len(review_case["history"]) == 21
    assert review_case["primary"] == primary
    assert len(review_case["directional_path_facts"]) == 2
    assert "recent" not in review_case
    assert "recent_available" not in review_case
    assert decoded["explicit_recent_derived_facts_included"] is False


def test_directional_boundary_d3_is_single_job_with_no_retry(tmp_path: Path) -> None:
    worker_paths = paths(tmp_path)
    first = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_DIRECTIONAL_BOUNDARY_D3_JOB,
    )
    second = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_DIRECTIONAL_BOUNDARY_D3_JOB,
    )
    assert first["kind"] == STAGE_DIRECTIONAL_BOUNDARY_D3_JOB
    assert first["config"] == (
        "config/stage-learning/directional-boundary-evaluation-prepare.v1.json"
    )
    assert _JOB_SPECS[STAGE_DIRECTIONAL_BOUNDARY_D3_JOB]["wall_seconds"] == 2700
    assert second["job_id"] == first["job_id"]
    assert second["idempotent_reuse"] is True
    marker = json.loads(worker_paths.directional_boundary_d3_budget.read_text(encoding="utf-8"))
    assert marker["job"]["job_id"] == first["job_id"]
    assert marker["input_digest"] == first["input_digest"]
    with pytest.raises(StageWorkerError, match="no retry"):
        submit_job(
            worker_paths,
            code_revision="c" * 40,
            kind=STAGE_DIRECTIONAL_BOUNDARY_D3_JOB,
        )


def test_directional_boundary_d3_status_exposes_primary_only_review_transport(
    tmp_path: Path,
) -> None:
    worker_paths = paths(tmp_path)
    worker_paths.ensure()
    job_id = "stage_" + "7" * 32
    source_rows = {}
    history_refs = []
    for index in range(21):
        row = {
            "market_date": f"2021-03-{index + 1:02d}",
            "high": str(201 + index),
            "low": str(199 + index),
            "close": str(200 + index),
            "coordinate_epoch": "original-price-quantity-epoch",
            "reset_reason": None,
        }
        ref = _digest(row)
        source_rows[ref] = row
        history_refs.append(ref)
    primary = {
        "directional_efficiency": {"value": "0.20", "reason": "available"},
        "range_width": {"value": "0.25", "reason": "available"},
        "width_change": {"value": "0.30", "reason": "available"},
        "center_shift": {"value": "0.08", "reason": "available"},
    }
    case_body = {
        "security_code": "600001",
        "security_id": "security-eval-A",
        "market_date": "2021-03-21",
        "sampling_stratum": "representative",
        "coordinate_ref": _digest({"coordinate": "eval-A"}),
        "primary_available": True,
        "recent_available": True,
        "input_reason": "admitted",
        "availability_reasons": {"trend": "available", "order": "available"},
        "primary": primary,
        "recent": {"width_change": {"value": "0.2"}, "center_shift": {"value": "0.1"}},
        "history_refs": history_refs,
    }
    case = {"case_id": _digest(case_body), **case_body}
    evidence = {"source_rows": source_rows, "cases": [case]}
    evidence_raw = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    evidence_sha = "sha256:" + hashlib.sha256(evidence_raw).hexdigest()
    path_facts = {
        "cases": {
            case["case_id"]: {
                "structure.geometry.range-traversal": {
                    "value": "0.8", "reason": "available", "revision_id": _digest({"e": 1}),
                },
                "structure.geometry.counter-direction-excursion-share": {
                    "value": "0.2", "reason": "available", "revision_id": _digest({"e": 2}),
                },
            }
        }
    }
    path_raw = json.dumps(path_facts, sort_keys=True, separators=(",", ":")).encode()
    path_sha = "sha256:" + hashlib.sha256(path_raw).hexdigest()
    artifact = worker_paths.runs / job_id / "stage-directional-boundary-d3-test"
    artifact.mkdir(parents=True)
    (artifact / "evidence.json").write_bytes(evidence_raw)
    (artifact / "path-facts.json").write_bytes(path_raw)
    manifest = {"files": {"evidence.json": evidence_sha, "path-facts.json": path_sha}}
    manifest_raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    manifest_sha = "sha256:" + hashlib.sha256(manifest_raw).hexdigest()
    (artifact / "manifest.json").write_bytes(manifest_raw)
    (artifact / "READY").write_text(manifest_sha + "\n", encoding="ascii")
    status = {
        "schema_version": "quanthus.stage-worker-status.v1",
        "job_id": job_id,
        "kind": STAGE_DIRECTIONAL_BOUNDARY_D3_JOB,
        "code_revision": REVISION,
        "input_digest": _digest({"job": job_id}),
        "state": "succeeded",
        "stage": "terminal",
        "submitted_at": "2026-09-12T01:00:00+00:00",
        "started_at": "2026-09-12T01:00:01+00:00",
        "completed_at": "2026-09-12T01:00:02+00:00",
        "result_ref": {
            "artifact_root": str(artifact),
            "manifest_sha256": manifest_sha,
            "directional_boundary_d3": {
                "evaluation_selected_case_count": 1,
                "review_ready": True,
            },
        },
        "error": None,
        "exit_code": 0,
        "updated_at": "2026-09-12T01:00:02+00:00",
    }
    (worker_paths.status / f"{job_id}.json").write_text(json.dumps(status), encoding="utf-8")
    observed = read_status(worker_paths, job_id)
    index = observed["review_transport_index"]
    assert index["case_count"] == 1
    assert index["explicit_recent_derived_facts_included"] is False
    assert index["candidate_output_included"] is False
    assert index["future_rows_included"] is False
    page_status = read_status(worker_paths, index["pages"][0]["job_id"])
    envelope = page_status["review_transport"]
    decoded = json.loads(gzip.decompress(base64.b64decode(envelope["payload"])))
    review_case = decoded["cases"][0]
    assert len(review_case["history"]) == 21
    assert review_case["primary"] == primary
    assert len(review_case["directional_path_facts"]) == 2
    assert "recent" not in review_case
    assert "recent_available" not in review_case
    assert decoded["explicit_recent_derived_facts_included"] is False


def test_positive_instability_d1_is_single_job_with_no_retry(tmp_path: Path) -> None:
    worker_paths = paths(tmp_path)
    first = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_POSITIVE_INSTABILITY_D1_JOB,
    )
    second = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_POSITIVE_INSTABILITY_D1_JOB,
    )
    assert first["kind"] == STAGE_POSITIVE_INSTABILITY_D1_JOB
    assert first["config"] == (
        "config/stage-learning/positive-instability-development-screen.v1.json"
    )
    assert _JOB_SPECS[STAGE_POSITIVE_INSTABILITY_D1_JOB]["wall_seconds"] == 2700
    assert second["job_id"] == first["job_id"]
    assert second["idempotent_reuse"] is True
    marker = json.loads(worker_paths.positive_instability_d1_budget.read_text(encoding="utf-8"))
    assert marker["job"]["job_id"] == first["job_id"]
    assert marker["input_digest"] == first["input_digest"]
    with pytest.raises(StageWorkerError, match="no retry"):
        submit_job(
            worker_paths,
            code_revision="c" * 40,
            kind=STAGE_POSITIVE_INSTABILITY_D1_JOB,
        )


def test_positive_instability_d1_status_exposes_candidate_blind_primary_transport(
    tmp_path: Path,
) -> None:
    worker_paths = paths(tmp_path)
    worker_paths.ensure()
    job_id = "stage_" + "6" * 32
    source_rows = {}
    history_refs = []
    for index in range(21):
        row = {
            "market_date": f"2020-04-{index + 1:02d}",
            "high": str(301 + index),
            "low": str(299 + index),
            "close": str(300 + index),
            "coordinate_epoch": "original-price-quantity-epoch",
            "reset_reason": None,
        }
        ref = _digest(row)
        source_rows[ref] = row
        history_refs.append(ref)
    primary = {
        "directional_efficiency": {"value": "0.05", "reason": "available"},
        "range_width": {"value": "0.30", "reason": "available"},
        "width_change": {"value": "-0.20", "reason": "available"},
        "center_shift": {"value": "0.12", "reason": "available"},
    }
    case_body = {
        "security_code": "600009",
        "security_id": "security-third-pool-A",
        "market_date": "2020-04-21",
        "sampling_stratum": "representative",
        "coordinate_ref": _digest({"coordinate": "third-A"}),
        "primary_available": True,
        "recent_available": True,
        "input_reason": "admitted",
        "availability_reasons": {"trend": "available", "order": "available"},
        "primary": primary,
        "recent": {"width_change": {"value": "0.9"}, "center_shift": {"value": "0.9"}},
        "history_refs": history_refs,
    }
    case = {"case_id": _digest(case_body), **case_body}
    evidence = {"source_rows": source_rows, "cases": [case]}
    evidence_raw = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    evidence_sha = "sha256:" + hashlib.sha256(evidence_raw).hexdigest()
    path_facts = {
        "cases": {
            case["case_id"]: {
                "structure.geometry.range-traversal": {
                    "value": "0.1", "reason": "available", "revision_id": _digest({"p": 3}),
                },
                "structure.geometry.counter-direction-excursion-share": {
                    "value": "0.9", "reason": "available", "revision_id": _digest({"p": 4}),
                },
            }
        }
    }
    path_raw = json.dumps(path_facts, sort_keys=True, separators=(",", ":")).encode()
    path_sha = "sha256:" + hashlib.sha256(path_raw).hexdigest()
    threshold_basis = {
        "schema_version": "test-threshold-basis",
        "labels_used": False,
        "range_width20": {"q50": "0.2"},
    }
    candidate_grid = {
        "schema_version": "test-candidate-grid",
        "candidate_count": 27,
        "parameter_search_performed": False,
        "candidates": [{"candidate_id": f"candidate-{index}"} for index in range(27)],
    }
    evaluation_plan = {
        "schema_version": "test-evaluation-plan",
        "identity_count": 160,
        "identity_digest": _digest({"evaluation": 160}),
        "data_read": False,
    }
    threshold_raw = json.dumps(threshold_basis, sort_keys=True, separators=(",", ":")).encode()
    candidate_raw = json.dumps(candidate_grid, sort_keys=True, separators=(",", ":")).encode()
    evaluation_raw = json.dumps(evaluation_plan, sort_keys=True, separators=(",", ":")).encode()
    artifact = worker_paths.runs / job_id / "stage-positive-instability-d1-test"
    artifact.mkdir(parents=True)
    (artifact / "evidence.json").write_bytes(evidence_raw)
    (artifact / "path-facts.json").write_bytes(path_raw)
    (artifact / "threshold-basis.json").write_bytes(threshold_raw)
    (artifact / "candidate-grid.json").write_bytes(candidate_raw)
    (artifact / "evaluation-plan.json").write_bytes(evaluation_raw)
    manifest = {
        "files": {
            "evidence.json": evidence_sha,
            "path-facts.json": path_sha,
            "threshold-basis.json": "sha256:" + hashlib.sha256(threshold_raw).hexdigest(),
            "candidate-grid.json": "sha256:" + hashlib.sha256(candidate_raw).hexdigest(),
            "evaluation-plan.json": "sha256:" + hashlib.sha256(evaluation_raw).hexdigest(),
        }
    }
    manifest_raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    manifest_sha = "sha256:" + hashlib.sha256(manifest_raw).hexdigest()
    (artifact / "manifest.json").write_bytes(manifest_raw)
    (artifact / "READY").write_text(manifest_sha + "\n", encoding="ascii")
    status = {
        "schema_version": "quanthus.stage-worker-status.v1",
        "job_id": job_id,
        "kind": STAGE_POSITIVE_INSTABILITY_D1_JOB,
        "code_revision": REVISION,
        "input_digest": _digest({"job": job_id}),
        "state": "succeeded",
        "stage": "terminal",
        "submitted_at": "2026-09-12T02:00:00+00:00",
        "started_at": "2026-09-12T02:00:01+00:00",
        "completed_at": "2026-09-12T02:00:02+00:00",
        "result_ref": {
            "artifact_root": str(artifact),
            "manifest_sha256": manifest_sha,
            "positive_instability_d1": {
                "development_selected_case_count": 1,
                "threshold_basis_digest": _digest(threshold_basis),
                "candidate_grid_digest": _digest(candidate_grid),
                "evaluation_plan_digest": _digest(evaluation_plan),
                "review_ready": True,
            },
        },
        "error": None,
        "exit_code": 0,
        "updated_at": "2026-09-12T02:00:02+00:00",
    }
    (worker_paths.status / f"{job_id}.json").write_text(json.dumps(status), encoding="utf-8")
    observed = read_status(worker_paths, job_id)
    index = observed["review_transport_index"]
    assert index["case_count"] == 1
    assert index["threshold_basis_included"] is False
    assert index["candidate_output_included"] is False
    assert index["explicit_recent_derived_facts_included"] is False
    assert index["future_rows_included"] is False
    page_status = read_status(worker_paths, index["pages"][0]["job_id"])
    decoded = json.loads(
        gzip.decompress(base64.b64decode(page_status["review_transport"]["payload"]))
    )
    review_case = decoded["cases"][0]
    assert len(review_case["history"]) == 21
    assert review_case["primary"] == primary
    assert len(review_case["directional_path_facts"]) == 2
    assert "recent" not in review_case
    assert "recent_available" not in review_case
    assert "threshold_basis" not in decoded
    assert "candidate" not in decoded
    selection = json.loads(
        gzip.decompress(base64.b64decode(observed["selection_transport"]["payload"]))
    )
    assert selection["threshold_basis"] == threshold_basis
    assert selection["candidate_grid"] == candidate_grid
    assert selection["evaluation_plan_binding"]["identity_count"] == 160
    assert selection["review_ledger_must_be_sealed_before_use"] is True
    assert selection["evaluation_data_read"] is False


def _write_positive_instability_quantitative_d1_retry_authorization(
    worker_paths: StageWorkerPaths, first: dict,
) -> None:
    config_dir = worker_paths.control_repo / "config/stage-learning"
    config_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "positive-instability-quantitative-development-screen.v1.json",
        "positive-instability-quantitative-development-execution.v1.json",
    ):
        (config_dir / name).write_text(
            (ROOT / "config/stage-learning" / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    screen = json.loads(
        (config_dir / "positive-instability-quantitative-development-screen.v1.json").read_text(
            encoding="utf-8"
        )
    )
    execution = json.loads(
        (config_dir / "positive-instability-quantitative-development-execution.v1.json").read_text(
            encoding="utf-8"
        )
    )
    authorization = {
        "schema_version": "quanthus.stage-positive-instability-quantitative-d1-retry.v1",
        "work_item": "P3-STAGE-010-D",
        "retry_kind": "technical_only",
        "parent_job": {
            "job_id": first["job_id"],
            "code_revision": first["code_revision"],
            "input_digest": first["input_digest"],
            "terminal_state": "failed",
            "error": "stage_positive_instability_quantitative_d1_process_failed",
            "artifact_published": False,
        },
        "root_cause": {
            "exception": "quanthus.stage_learning.source.StageDevelopmentSourceError",
            "message": "identity security_codes must be unique six-digit codes",
            "diagnosis": "test-only year-end calendar endpoint correction",
            "project_evidence_for_corrected_endpoint": "2018-12-28",
        },
        "correction": {
            "field": "source_isolation.candidate_cohort_date",
            "old_value": "2018-12-31",
            "new_value": "2018-12-28",
            "old_preregistration_digest": "sha256:" + "0" * 64,
            "new_preregistration_digest": _digest(screen),
            "new_execution_digest": _digest(execution),
        },
        "unchanged_semantic_contract": {
            "fresh_pool_size": 512,
            "development_identity_capacity": 256,
            "development_case_target": 160,
            "candidate_count": 9,
            "development_gates_changed": False,
            "evaluation_gates_changed": False,
            "projection_topology_changed": False,
            "relocation_guard_retuned": False,
            "stage_2_changed": False,
            "preserved_2021_evaluation_plan_changed": False,
            "preserved_2021_evaluation_read": False,
            "2022_2024_access": False,
            "2025_access": False,
        },
        "budget": {
            "maximum_retry_jobs": 1,
            "semantic_retries": 0,
            "automatic_supplement_rounds": 0,
            "retry_requires_exact_parent_failure": True,
            "retry_requires_exact_corrected_preregistration_digest": True,
        },
    }
    (config_dir / "positive-instability-quantitative-d1-retry.v1.json").write_text(
        json.dumps(authorization), encoding="utf-8"
    )


def _write_positive_instability_quantitative_d1_transport_recovery_authorization(
    worker_paths: StageWorkerPaths, first: dict, retry: dict,
) -> None:
    config_dir = worker_paths.control_repo / "config/stage-learning"
    scope_name = "order-reference-evidence-scope-2019.v1.json"
    (config_dir / scope_name).write_text(
        (ROOT / "config/stage-learning" / scope_name).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    screen = json.loads(
        (config_dir / "positive-instability-quantitative-development-screen.v1.json").read_text(
            encoding="utf-8"
        )
    )
    execution = json.loads(
        (config_dir / "positive-instability-quantitative-development-execution.v1.json").read_text(
            encoding="utf-8"
        )
    )
    scope = json.loads((config_dir / scope_name).read_text(encoding="utf-8"))
    authorization = {
        "schema_version": (
            "quanthus.stage-positive-instability-quantitative-d1-transport-recovery.v1"
        ),
        "work_item": "P3-STAGE-010-D1-TRANSPORT-RECOVERY",
        "recovery_kind": "evidence_transport_only",
        "failed_chain": {
            "initial_job": {
                "job_id": first["job_id"],
                "code_revision": first["code_revision"],
                "input_digest": first["input_digest"],
                "terminal_state": "failed",
                "error": "stage_positive_instability_quantitative_d1_process_failed",
                "artifact_published": False,
            },
            "technical_retry_job": {
                "job_id": retry["job_id"],
                "code_revision": retry["code_revision"],
                "input_digest": retry["input_digest"],
                "terminal_state": "failed",
                "error": "stage_positive_instability_quantitative_d1_process_failed",
                "artifact_published": False,
            },
        },
        "implementation_conformance_defect": {
            "old_builder_ignored_declared_time_fields": True,
            "new_scope_path": f"config/stage-learning/{scope_name}",
            "new_scope_digest": _digest(scope),
            "new_scope_history_start": "2018-09-01",
            "new_scope_case_start": "2019-01-01",
            "new_scope_development_end": "2019-12-31",
        },
        "frozen_parent_contract": {
            "preregistration_digest": _digest(screen),
            "execution_digest": _digest(execution),
            "fresh_pool_size": 512,
            "development_identity_capacity": 256,
            "development_case_target": 160,
            "candidate_count": 9,
            "preserved_evaluation_plan_digest": (
                "sha256:2800777b5941892a73cf022ddd9375780f2a4f63985f1ce0c1eae9b3668169a7"
            ),
            "preserved_evaluation_identity_count": 160,
        },
        "observed_result_exposure_before_recovery": {
            "initial_job_artifact": False,
            "technical_retry_artifact": False,
            "development_reference_opinions": 0,
            "candidate_scores": 0,
            "candidate_selected": False,
            "threshold_values_exposed_to_reviewer": False,
            "preserved_2021_evaluation_read": False,
            "2022_2024_read": False,
            "2025_read": False,
        },
        "authority": {
            "maximum_transport_recovery_jobs": 1,
            "counts_against_result_bearing_market_data_job_budget": False,
            "same_preregistration_digest_required": True,
            "same_execution_digest_required": True,
            "same_identity_exclusion_required": True,
            "same_sampling_seed_required": True,
            "same_candidate_grid_required": True,
            "same_gates_required": True,
            "same_preserved_evaluation_plan_required": True,
            "parameter_search": False,
            "semantic_retry": False,
            "candidate_replacement": False,
            "automatic_supplement": False,
            "preserved_2021_evaluation_read": False,
            "2022_2024_access": False,
            "2025_access": False,
            "official_labels": False,
            "daily_or_strategy": False,
            "trading": False,
        },
    }
    recovery_path = (
        config_dir / "positive-instability-quantitative-d1-transport-recovery.v1.json"
    )
    recovery_path.write_text(json.dumps(authorization), encoding="utf-8")


def test_positive_instability_quantitative_d1_allows_one_bound_technical_retry(
    tmp_path: Path,
) -> None:
    worker_paths = paths(tmp_path)
    first = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_POSITIVE_INSTABILITY_QUANTITATIVE_D1_JOB,
    )
    second = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_POSITIVE_INSTABILITY_QUANTITATIVE_D1_JOB,
    )
    assert second["job_id"] == first["job_id"]
    assert second["idempotent_reuse"] is True
    job = StageJob.from_dict(
        json.loads(
            (worker_paths.queue / f"{first['job_id']}.json").read_text(encoding="utf-8")
        )
    )
    (worker_paths.queue / f"{first['job_id']}.json").unlink()
    StageWorker(worker_paths)._write_status(
        job,
        state="failed",
        stage="terminal",
        started_at="2026-09-12T13:36:49+00:00",
        completed_at="2026-09-12T13:36:55+00:00",
        error="stage_positive_instability_quantitative_d1_process_failed",
        exit_code=1,
    )
    _write_positive_instability_quantitative_d1_retry_authorization(worker_paths, first)

    retry_revision = "c" * 40
    retry = submit_job(
        worker_paths,
        code_revision=retry_revision,
        kind=STAGE_POSITIVE_INSTABILITY_QUANTITATIVE_D1_JOB,
    )
    assert retry["job_id"] != first["job_id"]
    assert retry["technical_retry_of"] == first["job_id"]
    assert retry["state"] == "queued"
    repeated = submit_job(
        worker_paths,
        code_revision=retry_revision,
        kind=STAGE_POSITIVE_INSTABILITY_QUANTITATIVE_D1_JOB,
    )
    assert repeated["job_id"] == retry["job_id"]
    assert repeated["idempotent_reuse"] is True
    assert repeated["technical_retry_of"] == first["job_id"]

    retry_job = StageJob.from_dict(
        json.loads(
            (worker_paths.queue / f"{retry['job_id']}.json").read_text(encoding="utf-8")
        )
    )
    (worker_paths.queue / f"{retry['job_id']}.json").unlink()
    StageWorker(worker_paths)._write_status(
        retry_job,
        state="failed",
        stage="terminal",
        started_at="2026-09-12T14:26:18+00:00",
        completed_at="2026-09-12T14:28:23+00:00",
        error="stage_positive_instability_quantitative_d1_process_failed",
        exit_code=1,
    )
    _write_positive_instability_quantitative_d1_transport_recovery_authorization(
        worker_paths, first, retry
    )

    recovery_revision = "d" * 40
    recovery = submit_job(
        worker_paths,
        code_revision=recovery_revision,
        kind=STAGE_POSITIVE_INSTABILITY_QUANTITATIVE_D1_JOB,
    )
    assert recovery["job_id"] not in {first["job_id"], retry["job_id"]}
    assert recovery["transport_recovery_of"] == retry["job_id"]
    repeated_recovery = submit_job(
        worker_paths,
        code_revision=recovery_revision,
        kind=STAGE_POSITIVE_INSTABILITY_QUANTITATIVE_D1_JOB,
    )
    assert repeated_recovery["job_id"] == recovery["job_id"]
    assert repeated_recovery["idempotent_reuse"] is True
    assert repeated_recovery["transport_recovery_of"] == retry["job_id"]
    with pytest.raises(StageWorkerError, match="transport recovery budget is already consumed"):
        submit_job(
            worker_paths,
            code_revision="e" * 40,
            kind=STAGE_POSITIVE_INSTABILITY_QUANTITATIVE_D1_JOB,
        )


def test_positive_instability_quantitative_d1_status_is_candidate_blind(
    tmp_path: Path,
) -> None:
    worker_paths = paths(tmp_path)
    worker_paths.ensure()
    job_id = "stage_" + "5" * 32
    source_rows = {}
    history_refs = []
    for index in range(21):
        row = {
            "market_date": f"2019-04-{index + 1:02d}",
            "high": str(401 + index),
            "low": str(399 + index),
            "close": str(400 + index),
            "coordinate_epoch": "original-price-quantity-epoch",
            "reset_reason": None,
        }
        ref = _digest(row)
        source_rows[ref] = row
        history_refs.append(ref)
    primary = {
        "directional_efficiency": {"value": "0.15", "reason": "available"},
        "range_width": {"value": "0.18", "reason": "available"},
        "width_change": {"value": "0.40", "reason": "available"},
        "center_shift": {"value": "0.03", "reason": "available"},
    }
    case_body = {
        "security_code": "000001",
        "security_id": "security-fourth-pool-A",
        "market_date": "2019-04-21",
        "sampling_stratum": "representative",
        "coordinate_ref": _digest({"coordinate": "fourth-A"}),
        "primary_available": True,
        "recent_available": True,
        "input_reason": "admitted",
        "availability_reasons": {"trend": "available", "order": "available"},
        "primary": primary,
        "recent": {"width_change": {"value": "0.8"}, "center_shift": {"value": "0.9"}},
        "history_refs": history_refs,
    }
    case = {"case_id": _digest(case_body), **case_body}
    evidence = {"source_rows": source_rows, "cases": [case]}
    evidence_raw = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    evidence_sha = "sha256:" + hashlib.sha256(evidence_raw).hexdigest()
    path_facts = {
        "cases": {
            case["case_id"]: {
                "structure.geometry.range-traversal": {
                    "value": "0.2",
                    "reason": "available",
                    "revision_id": _digest({"q": 1}),
                },
                "structure.geometry.counter-direction-excursion-share": {
                    "value": "0.8",
                    "reason": "available",
                    "revision_id": _digest({"q": 2}),
                },
            }
        }
    }
    path_raw = json.dumps(path_facts, sort_keys=True, separators=(",", ":")).encode()
    path_sha = "sha256:" + hashlib.sha256(path_raw).hexdigest()
    artifact = worker_paths.runs / job_id / "stage-positive-instability-quantitative-d1-test"
    artifact.mkdir(parents=True)
    (artifact / "evidence.json").write_bytes(evidence_raw)
    (artifact / "path-facts.json").write_bytes(path_raw)
    manifest = {"files": {"evidence.json": evidence_sha, "path-facts.json": path_sha}}
    manifest_raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    manifest_sha = "sha256:" + hashlib.sha256(manifest_raw).hexdigest()
    (artifact / "manifest.json").write_bytes(manifest_raw)
    (artifact / "READY").write_text(manifest_sha + "\n", encoding="ascii")
    status = {
        "schema_version": "quanthus.stage-worker-status.v1",
        "job_id": job_id,
        "kind": STAGE_POSITIVE_INSTABILITY_QUANTITATIVE_D1_JOB,
        "code_revision": REVISION,
        "input_digest": _digest({"job": job_id}),
        "state": "succeeded",
        "stage": "terminal",
        "submitted_at": "2026-09-12T10:00:00+00:00",
        "started_at": "2026-09-12T10:00:01+00:00",
        "completed_at": "2026-09-12T10:00:02+00:00",
        "result_ref": {
            "artifact_root": str(artifact),
            "manifest_sha256": manifest_sha,
            "positive_instability_quantitative_d1": {
                "development_selected_case_count": 1,
                "review_ready": True,
            },
        },
        "error": None,
        "exit_code": 0,
        "updated_at": "2026-09-12T10:00:02+00:00",
    }
    (worker_paths.status / f"{job_id}.json").write_text(json.dumps(status), encoding="utf-8")
    observed = read_status(worker_paths, job_id)
    index = observed["review_transport_index"]
    assert index["case_count"] == 1
    assert index["threshold_basis_included"] is False
    assert index["candidate_output_included"] is False
    assert index["preserved_2021_evaluation_included"] is False
    page_status = read_status(worker_paths, index["pages"][0]["job_id"])
    decoded = json.loads(
        gzip.decompress(base64.b64decode(page_status["review_transport"]["payload"]))
    )
    review_case = decoded["cases"][0]
    assert review_case["primary"] == primary
    assert len(review_case["history"]) == 21
    assert "recent" not in review_case
    assert "threshold_basis" not in decoded
    assert "candidate" not in decoded


def _write_orderly_consolidation_joint_d1_transport_recovery_authorization(
    worker_paths: StageWorkerPaths, first: dict,
) -> None:
    config_dir = worker_paths.control_repo / "config/stage-learning"
    config_dir.mkdir(parents=True, exist_ok=True)
    names = (
        "orderly-consolidation-joint-development-screen.v1.json",
        "orderly-consolidation-joint-development-execution.v1.json",
        "order-reference-evidence-scope-2018.v1.json",
    )
    for name in names:
        (config_dir / name).write_text(
            (ROOT / "config/stage-learning" / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    screen = json.loads(
        (config_dir / names[0]).read_text(encoding="utf-8")
    )
    execution = json.loads(
        (config_dir / names[1]).read_text(encoding="utf-8")
    )
    scope = json.loads(
        (config_dir / names[2]).read_text(encoding="utf-8")
    )
    authorization = {
        "schema_version": "quanthus.stage-orderly-consolidation-joint-d1-transport-recovery.v1",
        "work_item": "P3-STAGE-011-D1-TRANSPORT-RECOVERY",
        "recovery_kind": "protocol_budget_conformance_only",
        "failed_job": {
            "job_id": first["job_id"],
            "code_revision": first["code_revision"],
            "input_digest": first["input_digest"],
            "terminal_state": "failed",
            "error": "stage_orderly_consolidation_joint_d1_process_failed",
            "artifact_published": False,
        },
        "implementation_conformance_defect": {
            "protocol_path": "config/stage-learning/order-reference-evidence-scope-2018.v1.json",
            "protocol_digest": _digest(scope),
            "declared_max_cases": 192,
            "declared_max_source_rows": 20000,
            "declared_max_artifact_bytes": 33554432,
            "builder_consumed_signed_bounds": True,
            "validator_hardcoded_case_ceiling": 160,
            "validator_hardcoded_source_row_ceiling": 20000,
        },
        "frozen_parent_contract": {
            "preregistration_digest": _digest(screen),
            "execution_digest": _digest(execution),
            "evidence_scope_digest": _digest(scope),
            "development_year": 2018,
            "fresh_pool_size": 512,
            "development_identity_capacity": 256,
            "development_case_target": 192,
            "stage0_candidate_count": 9,
            "stage2_candidate_count": 3,
            "candidate_pair_count": 27,
            "preserved_evaluation_plan_digest": (
                "sha256:2800777b5941892a73cf022ddd9375780f2a4f63985f1ce0c1eae9b3668169a7"
            ),
            "preserved_evaluation_identity_count": 160,
        },
        "observed_result_exposure_before_recovery": {
            "failed_job_market_data_read": True,
            "governed_artifact_published": False,
            "development_reference_opinions": 0,
            "candidate_pair_scores": 0,
            "candidate_pair_selected": False,
            "threshold_values_exposed_to_reviewer": False,
            "preserved_2021_evaluation_read": False,
            "2019_p010_development_read": False,
            "2020_p009_development_read": False,
            "2022_2024_read": False,
            "2025_read": False,
        },
        "authority": {
            "maximum_transport_recovery_jobs": 1,
            "counts_against_result_bearing_market_data_job_budget": False,
            "same_preregistration_digest_required": True,
            "same_execution_digest_required": True,
            "same_evidence_scope_required": True,
            "same_identity_exclusion_required": True,
            "same_sampling_seed_required": True,
            "same_candidate_pair_surface_required": True,
            "same_gates_required": True,
            "same_preserved_evaluation_plan_required": True,
            "parameter_search": False,
            "semantic_retry": False,
            "candidate_replacement": False,
            "automatic_supplement": False,
            "preserved_2021_evaluation_read": False,
            "2019_p010_development_access": False,
            "2020_p009_development_access": False,
            "2022_2024_access": False,
            "2025_access": False,
            "official_labels": False,
            "daily_or_strategy": False,
            "trading": False,
        },
    }
    (config_dir / "orderly-consolidation-joint-d1-transport-recovery.v1.json").write_text(
        json.dumps(authorization), encoding="utf-8"
    )


def test_orderly_consolidation_joint_d1_is_one_shot_and_idempotent(tmp_path: Path) -> None:
    worker_paths = paths(tmp_path)
    first = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_ORDERLY_CONSOLIDATION_JOINT_D1_JOB,
    )
    second = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_ORDERLY_CONSOLIDATION_JOINT_D1_JOB,
    )
    assert first["kind"] == STAGE_ORDERLY_CONSOLIDATION_JOINT_D1_JOB
    assert first["config"] == (
        "config/stage-learning/orderly-consolidation-joint-development-screen.v1.json"
    )
    assert _JOB_SPECS[STAGE_ORDERLY_CONSOLIDATION_JOINT_D1_JOB]["wall_seconds"] == 2700
    assert second["job_id"] == first["job_id"]
    assert second["idempotent_reuse"] is True
    marker = json.loads(
        worker_paths.orderly_consolidation_joint_d1_budget.read_text(encoding="utf-8")
    )
    assert marker["schema_version"] == "quanthus.stage-orderly-consolidation-joint-d1-budget.v1"
    assert marker["work_item"] == "P3-STAGE-011-D"
    assert marker["job"]["job_id"] == first["job_id"]
    assert marker["input_digest"] == first["input_digest"]
    with pytest.raises(StageWorkerError, match="already bound"):
        submit_job(
            worker_paths,
            code_revision="b" * 40,
            kind=STAGE_ORDERLY_CONSOLIDATION_JOINT_D1_JOB,
        )

    job = StageJob.from_dict(
        json.loads(
            (worker_paths.queue / f"{first['job_id']}.json").read_text(encoding="utf-8")
        )
    )
    (worker_paths.queue / f"{first['job_id']}.json").unlink()
    StageWorker(worker_paths)._write_status(
        job,
        state="failed",
        stage="terminal",
        started_at="2026-09-13T06:09:27+00:00",
        completed_at="2026-09-13T06:11:32+00:00",
        error="stage_orderly_consolidation_joint_d1_process_failed",
        exit_code=1,
    )
    with pytest.raises(StageWorkerError, match="cannot read"):
        submit_job(
            worker_paths,
            code_revision="b" * 40,
            kind=STAGE_ORDERLY_CONSOLIDATION_JOINT_D1_JOB,
        )

    _write_orderly_consolidation_joint_d1_transport_recovery_authorization(
        worker_paths, first
    )
    recovery_revision = "b" * 40
    recovery = submit_job(
        worker_paths,
        code_revision=recovery_revision,
        kind=STAGE_ORDERLY_CONSOLIDATION_JOINT_D1_JOB,
    )
    assert recovery["job_id"] != first["job_id"]
    assert recovery["transport_recovery_of"] == first["job_id"]
    repeated = submit_job(
        worker_paths,
        code_revision=recovery_revision,
        kind=STAGE_ORDERLY_CONSOLIDATION_JOINT_D1_JOB,
    )
    assert repeated["job_id"] == recovery["job_id"]
    assert repeated["idempotent_reuse"] is True
    assert repeated["transport_recovery_of"] == first["job_id"]
    marker = json.loads(
        worker_paths.orderly_consolidation_joint_d1_recovery_budget.read_text(
            encoding="utf-8"
        )
    )
    assert marker["transport_recovery_of"] == first["job_id"]
    with pytest.raises(StageWorkerError, match="transport recovery budget is already consumed"):
        submit_job(
            worker_paths,
            code_revision="c" * 40,
            kind=STAGE_ORDERLY_CONSOLIDATION_JOINT_D1_JOB,
        )


def test_atomic_proposition_g1_is_one_shot_and_idempotent(tmp_path: Path) -> None:
    worker_paths = paths(tmp_path)
    first = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_ATOMIC_PROPOSITION_G1_JOB,
    )
    second = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_ATOMIC_PROPOSITION_G1_JOB,
    )
    assert first["kind"] == STAGE_ATOMIC_PROPOSITION_G1_JOB
    assert first["config"] == (
        "config/stage-learning/stage-atomic-proposition-development-screen.v1.json"
    )
    assert _JOB_SPECS[STAGE_ATOMIC_PROPOSITION_G1_JOB]["wall_seconds"] == 2700
    assert second["job_id"] == first["job_id"]
    assert second["idempotent_reuse"] is True
    marker = json.loads(worker_paths.atomic_proposition_g1_budget.read_text(encoding="utf-8"))
    assert marker["schema_version"] == "quanthus.stage-atomic-proposition-g1-budget.v1"
    assert marker["work_item"] == "P3-STAGE-012-G1"
    assert marker["job"]["job_id"] == first["job_id"]
    with pytest.raises(StageWorkerError, match="no retry or replacement authority"):
        submit_job(
            worker_paths,
            code_revision="b" * 40,
            kind=STAGE_ATOMIC_PROPOSITION_G1_JOB,
        )


def test_orderly_consolidation_joint_d1_status_is_candidate_blind(tmp_path: Path) -> None:
    worker_paths = paths(tmp_path)
    worker_paths.ensure()
    job_id = "stage_" + "6" * 32
    source_rows = {}
    history_refs = []
    for index in range(21):
        row = {
            "market_date": f"2018-04-{index + 1:02d}",
            "high": str(201 + index),
            "low": str(199 + index),
            "close": str(200 + index),
            "coordinate_epoch": "original-price-quantity-epoch",
            "reset_reason": None,
        }
        ref = _digest(row)
        source_rows[ref] = row
        history_refs.append(ref)
    primary = {
        "directional_efficiency": {"value": "0.12", "reason": "available"},
        "range_width": {"value": "0.08", "reason": "available"},
        "width_change": {"value": "-0.40", "reason": "available"},
        "center_shift": {"value": "0.08", "reason": "available"},
    }
    case_body = {
        "security_code": "000001",
        "security_id": "security-fifth-pool-A",
        "market_date": "2018-04-21",
        "sampling_stratum": "representative",
        "coordinate_ref": _digest({"coordinate": "fifth-A"}),
        "primary_available": True,
        "recent_available": True,
        "input_reason": "admitted",
        "availability_reasons": {"trend": "available", "order": "available"},
        "primary": primary,
        "recent": {"width_change": {"value": "9.9"}, "center_shift": {"value": "9.9"}},
        "history_refs": history_refs,
    }
    case = {"case_id": _digest(case_body), **case_body}
    evidence = {"source_rows": source_rows, "cases": [case]}
    evidence_raw = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    evidence_sha = "sha256:" + hashlib.sha256(evidence_raw).hexdigest()
    path_facts = {
        "cases": {
            case["case_id"]: {
                "structure.geometry.range-traversal": {
                    "value": "0.2",
                    "reason": "available",
                    "revision_id": _digest({"joint": 1}),
                },
                "structure.geometry.counter-direction-excursion-share": {
                    "value": "0.9",
                    "reason": "available",
                    "revision_id": _digest({"joint": 2}),
                },
            }
        }
    }
    path_raw = json.dumps(path_facts, sort_keys=True, separators=(",", ":")).encode()
    path_sha = "sha256:" + hashlib.sha256(path_raw).hexdigest()
    basis = {"labels_used": False, "reference_opinions_used": False}
    grid = {
        "stage0_candidate_count": 9,
        "stage2_candidate_count": 3,
        "pair_count": 27,
        "parameter_search_performed": False,
        "grid_expanded_after_result": False,
    }
    plan = {
        "identity_count": 160,
        "identity_digest": "sha256:" + "7" * 64,
        "data_read": False,
    }
    basis_raw = json.dumps(basis, sort_keys=True, separators=(",", ":")).encode()
    grid_raw = json.dumps(grid, sort_keys=True, separators=(",", ":")).encode()
    plan_raw = json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()
    artifact = worker_paths.runs / job_id / "stage-orderly-consolidation-joint-d1-test"
    artifact.mkdir(parents=True)
    (artifact / "evidence.json").write_bytes(evidence_raw)
    (artifact / "path-facts.json").write_bytes(path_raw)
    (artifact / "threshold-basis.json").write_bytes(basis_raw)
    (artifact / "candidate-grid.json").write_bytes(grid_raw)
    (artifact / "preserved-evaluation-plan.json").write_bytes(plan_raw)
    manifest = {
        "files": {
            "evidence.json": evidence_sha,
            "path-facts.json": path_sha,
            "threshold-basis.json": _digest(basis),
            "candidate-grid.json": _digest(grid),
            "preserved-evaluation-plan.json": _digest(plan),
        }
    }
    manifest_raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    manifest_sha = "sha256:" + hashlib.sha256(manifest_raw).hexdigest()
    (artifact / "manifest.json").write_bytes(manifest_raw)
    (artifact / "READY").write_text(manifest_sha + "\n", encoding="ascii")
    status = {
        "schema_version": "quanthus.stage-worker-status.v1",
        "job_id": job_id,
        "kind": STAGE_ORDERLY_CONSOLIDATION_JOINT_D1_JOB,
        "code_revision": REVISION,
        "input_digest": _digest({"job": job_id}),
        "state": "succeeded",
        "stage": "terminal",
        "submitted_at": "2026-09-13T00:00:00+00:00",
        "started_at": "2026-09-13T00:00:01+00:00",
        "completed_at": "2026-09-13T00:00:02+00:00",
        "result_ref": {
            "artifact_root": str(artifact),
            "manifest_sha256": manifest_sha,
            "orderly_consolidation_joint_d1": {
                "development_selected_case_count": 1,
                "threshold_basis_digest": _digest(basis),
                "candidate_grid_digest": _digest(grid),
                "preserved_evaluation_plan_digest": _digest(plan),
                "review_ready": True,
            },
        },
        "error": None,
        "exit_code": 0,
        "updated_at": "2026-09-13T00:00:02+00:00",
    }
    (worker_paths.status / f"{job_id}.json").write_text(json.dumps(status), encoding="utf-8")
    observed = read_status(worker_paths, job_id)
    index = observed["review_transport_index"]
    assert index["case_count"] == 1
    assert index["threshold_basis_included"] is False
    assert index["candidate_output_included"] is False
    assert index["preserved_2021_evaluation_included"] is False
    page_status = read_status(worker_paths, index["pages"][0]["job_id"])
    decoded = json.loads(
        gzip.decompress(base64.b64decode(page_status["review_transport"]["payload"]))
    )
    review_case = decoded["cases"][0]
    assert review_case["primary"] == primary
    assert len(review_case["history"]) == 21
    assert "recent" not in review_case
    assert "threshold_basis" not in decoded
    assert "candidate" not in decoded
    selection = json.loads(
        gzip.decompress(base64.b64decode(observed["selection_transport"]["payload"]))
    )
    assert selection["threshold_basis"] == basis
    assert selection["candidate_grid"] == grid
    assert selection["evaluation_plan_binding"] == {
        "identity_count": 160,
        "identity_digest": "sha256:" + "7" * 64,
        "plan_digest": _digest(plan),
        "data_read": False,
    }
    assert selection["review_ledger_must_be_sealed_before_use"] is True
    assert selection["evaluation_data_read"] is False


@pytest.mark.parametrize(
    ("job_kind", "detail_key", "selection_schema"),
    (
        (
            STAGE_ATOMIC_PROPOSITION_SUCCESSOR_D1_JOB,
            "atomic_proposition_successor_d1",
            "quanthus.stage-atomic-proposition-successor-d1-selection-transport.v1",
        ),
    ),
)
def test_successor_atomic_status_keeps_blind_review_separate_from_selection(
    tmp_path: Path,
    job_kind: str,
    detail_key: str,
    selection_schema: str,
) -> None:
    worker_paths = paths(tmp_path)
    worker_paths.ensure()
    job_id = "stage_" + "d" * 32
    source_rows = {}
    history_refs = []
    for index in range(21):
        row = {
            "security_code": "688001",
            "security_id": "security-star-successor",
            "market_date": f"2020-08-{index + 3:02d}",
            "high": "10",
            "low": "9",
            "close": "9.5",
            "coordinate_epoch": "epoch",
            "reset_reason": None,
        }
        ref = _digest(row)
        source_rows[ref] = row
        history_refs.append(ref)
    primary = {"stage_0": "UNKNOWN", "stage_2": "FALSE"}
    case_body = {
        "security_code": "688001",
        "security_id": "security-star-successor",
        "market_date": "2020-08-23",
        "sampling_stratum": "representative",
        "coordinate_ref": _digest({"coordinate": "successor"}),
        "primary_available": True,
        "recent_available": True,
        "input_reason": "admitted",
        "availability_reasons": {"trend": "available", "order": "available"},
        "primary": primary,
        "recent": {"width_change": {"value": "0.1"}},
        "history_refs": history_refs,
    }
    case = {"case_id": _digest(case_body), **case_body}
    evidence = {"source_rows": source_rows, "cases": [case]}
    path_facts = {
        "cases": {
            case["case_id"]: {
                "structure.geometry.range-traversal": {
                    "value": "0.2",
                    "reason": "available",
                    "revision_id": _digest({"successor": 1}),
                },
                "structure.geometry.counter-direction-excursion-share": {
                    "value": "0.4",
                    "reason": "available",
                    "revision_id": _digest({"successor": 2}),
                },
            }
        }
    }
    strata = {
        case["case_id"]: {
            "era": "STAR_PRE_CHINEXT_20PCT",
            "board_family": "STAR",
            "cell": "STAR_PRE_CHINEXT_20PCT/STAR",
            "plan_role": "primary",
            "window_id": "STAR_PRE_CHINEXT_20PCT|STAR|01",
        }
    }
    basis = {"labels_used": False, "reference_opinions_used": False}
    grid = {
        "instability_candidate_count": 9,
        "order_profile_count": 3,
        "relocation_candidate_count": 1,
        "joint_pair_search_count": 0,
        "parameter_search_performed": False,
        "grid_expanded_after_result": False,
    }
    envelopes = {"review_transport_visible": False, "cases": {}}
    plan = {
        "identity_count": 160,
        "identity_digest": "sha256:" + "7" * 64,
        "data_read": False,
    }
    payloads = {
        "evidence.json": evidence,
        "path-facts.json": path_facts,
        "case-strata.json": strata,
        "threshold-basis.json": basis,
        "candidate-grid.json": grid,
        "envelope-measurements.json": envelopes,
        "preserved-evaluation-plan.json": plan,
    }
    artifact = worker_paths.runs / job_id / "stage-atomic-proposition-successor-d1-test"
    artifact.mkdir(parents=True)
    files = {}
    for name, value in payloads.items():
        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        (artifact / name).write_bytes(raw)
        files[name] = "sha256:" + hashlib.sha256(raw).hexdigest()
    manifest = {"files": files}
    manifest_raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    manifest_sha = "sha256:" + hashlib.sha256(manifest_raw).hexdigest()
    (artifact / "manifest.json").write_bytes(manifest_raw)
    (artifact / "READY").write_text(manifest_sha + "\n", encoding="ascii")
    status = {
        "schema_version": "quanthus.stage-worker-status.v1",
        "job_id": job_id,
        "kind": job_kind,
        "code_revision": REVISION,
        "input_digest": _digest({"job": job_id}),
        "state": "succeeded",
        "stage": "terminal",
        "submitted_at": "2026-09-14T10:00:00+00:00",
        "started_at": "2026-09-14T10:00:01+00:00",
        "completed_at": "2026-09-14T10:00:02+00:00",
        "result_ref": {
            "artifact_root": str(artifact),
            "manifest_sha256": manifest_sha,
            detail_key: {
                "development_selected_case_count": 1,
                "threshold_basis_digest": _digest(basis),
                "candidate_grid_digest": _digest(grid),
                "envelope_measurements_digest": _digest(envelopes),
                "preserved_evaluation_plan_digest": _digest(plan),
                "case_strata_digest": _digest(strata),
                "review_ready": True,
            },
        },
        "error": None,
        "exit_code": 0,
        "updated_at": "2026-09-14T10:00:02+00:00",
    }
    (worker_paths.status / f"{job_id}.json").write_text(json.dumps(status), encoding="utf-8")

    observed = read_status(worker_paths, job_id)
    index = observed["review_transport_index"]
    assert index["case_count"] == 1
    assert index["era_board_stratum_included"] is True
    assert index["threshold_basis_included"] is False
    assert index["candidate_output_included"] is False
    assert index["envelope_measurement_included"] is False
    page_status = read_status(worker_paths, index["pages"][0]["job_id"])
    decoded = json.loads(
        gzip.decompress(base64.b64decode(page_status["review_transport"]["payload"]))
    )
    review_case = decoded["cases"][0]
    assert review_case["primary"] == primary
    assert review_case["resource_stratum"] == strata[case["case_id"]]
    assert len(review_case["history"]) == 21
    assert "recent" not in review_case
    assert "threshold_basis" not in decoded
    assert "candidate_grid" not in decoded
    assert "envelope_measurements" not in decoded
    selection = json.loads(
        gzip.decompress(base64.b64decode(observed["selection_transport"]["payload"]))
    )
    assert selection["schema_version"] == selection_schema
    assert selection["threshold_basis"] == basis
    assert selection["candidate_grid"] == grid
    assert selection["review_ledger_must_be_sealed_before_use"] is True
    assert selection["evaluation_data_read"] is False


def test_p014_selection_transport_requires_immutable_review_ledger(tmp_path: Path) -> None:
    worker_paths = paths(tmp_path)
    worker_paths.ensure()
    job_id = "stage_" + "e" * 32
    cases = []
    strata = {}
    for index in range(192):
        case_id = _digest({"p014_case": index})
        cases.append({"case_id": case_id})
        strata[case_id] = {
            "era": "PRE_STAR",
            "board_family": "MAIN",
            "cell": "PRE_STAR/MAIN",
            "plan_role": "primary",
            "window_id": f"window-{index:03d}",
        }
    evidence = {"source_rows": {}, "cases": cases}
    path_facts = {"cases": {}}
    basis = {"labels_used": False, "reference_opinions_used": False}
    grid = {
        "instability_candidate_count": 9,
        "order_profile_count": 3,
        "relocation_candidate_count": 1,
        "joint_pair_search_count": 0,
        "parameter_search_performed": False,
        "grid_expanded_after_result": False,
    }
    envelopes = {"review_transport_visible": False, "cases": {}}
    plan = {
        "identity_count": 160,
        "identity_digest": "sha256:" + "7" * 64,
        "data_read": False,
    }
    payloads = {
        "evidence.json": evidence,
        "path-facts.json": path_facts,
        "case-strata.json": strata,
        "threshold-basis.json": basis,
        "candidate-grid.json": grid,
        "envelope-measurements.json": envelopes,
        "preserved-evaluation-plan.json": plan,
    }
    artifact = worker_paths.runs / job_id / "stage-atomic-proposition-p014-test"
    artifact.mkdir(parents=True)
    files = {}
    for name, value in payloads.items():
        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        (artifact / name).write_bytes(raw)
        files[name] = "sha256:" + hashlib.sha256(raw).hexdigest()
    manifest = {"files": files}
    manifest_raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    manifest_sha = "sha256:" + hashlib.sha256(manifest_raw).hexdigest()
    (artifact / "manifest.json").write_bytes(manifest_raw)
    (artifact / "READY").write_text(manifest_sha + "\n", encoding="ascii")
    status = {
        "schema_version": "quanthus.stage-worker-status.v1",
        "job_id": job_id,
        "kind": STAGE_ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_JOB,
        "code_revision": REVISION,
        "input_digest": _digest({"job": job_id}),
        "state": "succeeded",
        "stage": "terminal",
        "submitted_at": "2026-09-14T10:00:00+00:00",
        "started_at": "2026-09-14T10:00:01+00:00",
        "completed_at": "2026-09-14T10:00:02+00:00",
        "result_ref": {
            "artifact_root": str(artifact),
            "manifest_sha256": manifest_sha,
            "atomic_proposition_admission_qualified_d1": {
                "development_selected_case_count": 192,
                "threshold_basis_digest": _digest(basis),
                "candidate_grid_digest": _digest(grid),
                "envelope_measurements_digest": _digest(envelopes),
                "preserved_evaluation_plan_digest": _digest(plan),
                "case_strata_digest": _digest(strata),
                "review_ready": True,
            },
        },
        "error": None,
        "exit_code": 0,
        "updated_at": "2026-09-14T10:00:02+00:00",
    }
    (worker_paths.status / f"{job_id}.json").write_text(json.dumps(status), encoding="utf-8")

    before = read_status(worker_paths, job_id)
    assert before["review_ledger_sealed"] is False
    assert before["review_ledger_sha256"] is None
    assert "selection_transport" not in before

    opinions = [
        {
            "index": index,
            "case_id": case["case_id"],
            "short_term": {
                "structural_instability_20": "FALSE",
                "coherent_directional_relocation_20": "FALSE",
                "orderly_consolidation_20": "FALSE",
            },
            "fund": {
                "structural_instability_20": "FALSE",
                "coherent_directional_relocation_20": "FALSE",
                "orderly_consolidation_20": "FALSE",
            },
            "short_reason": "No positive primary proposition in the blind packet.",
            "fund_reason": "No positive primary proposition in the blind packet.",
        }
        for index, case in enumerate(cases)
    ]
    submission = {
        "schema_version": (
            "quanthus.stage-atomic-proposition-admission-qualified-review-submission.v1"
        ),
        "work_item": "P3-STAGE-014-D",
        "parent_job_id": job_id,
        "source_manifest_sha256": manifest_sha,
        "development_evidence_digest": _digest(evidence),
        "provenance": {
            "kind": "self_simulated",
            "origin_id": "chatgpt-p014-d-test",
            "session_id": "test-session",
        },
        "candidate_output_seen_during_review": False,
        "threshold_basis_seen_during_review": False,
        "envelope_measurement_seen_during_review": False,
        "selection_transport_seen_during_review": False,
        "future_rows_seen_during_review": False,
        "preserved_2021_seen_during_review": False,
        "opinions": opinions,
    }
    receipt = seal_p014_review(worker_paths, job_id, submission)
    assert receipt["sealed"] is True
    assert receipt["case_count"] == 192
    assert receipt["reference_summary"]["atomic_reference_counts"] == {
        "structural_instability_20": {"FALSE": 192},
        "coherent_directional_relocation_20": {"FALSE": 192},
        "orderly_consolidation_20": {"FALSE": 192},
    }
    repeated = seal_p014_review(worker_paths, job_id, submission)
    assert repeated["ledger_sha256"] == receipt["ledger_sha256"]

    after = read_status(worker_paths, job_id)
    assert after["review_ledger_sealed"] is True
    assert after["review_ledger_sha256"] == receipt["ledger_sha256"]
    selection = json.loads(
        gzip.decompress(base64.b64decode(after["selection_transport"]["payload"]))
    )
    assert selection["schema_version"] == (
        "quanthus.stage-atomic-proposition-admission-qualified-d1-selection-transport.v1"
    )
    assert selection["review_ledger_must_be_sealed_before_use"] is True

    changed = json.loads(json.dumps(submission))
    changed["opinions"][0]["short_reason"] = "changed"
    with pytest.raises(StageWorkerError, match="already sealed"):
        seal_p014_review(worker_paths, job_id, changed)


def test_p015_review_prepare_status_exposes_blind_pages_without_selection(
    tmp_path: Path,
) -> None:
    worker_paths = paths(tmp_path)
    worker_paths.ensure()
    job_id = "stage_" + "f" * 32
    source_rows = {}
    history_refs = []
    for index in range(21):
        row = {
            "security_code": "688001",
            "security_id": "security-p015-new",
            "market_date": f"2020-01-{index + 1:02d}",
            "high": "10",
            "low": "9",
            "close": "9.5",
            "coordinate_epoch": "epoch",
            "reset_reason": None,
        }
        ref = _digest(row)
        source_rows[ref] = row
        history_refs.append(ref)
    case_body = {
        "security_code": "688001",
        "security_id": "security-p015-new",
        "market_date": "2020-01-21",
        "sampling_stratum": "representative",
        "coordinate_ref": _digest({"coordinate": "p015"}),
        "primary_available": True,
        "input_reason": "admitted_retrospective_only",
        "availability_reasons": {"trend": "available", "order": "available"},
        "primary": {
            "range_width": {
                "value": "0.1",
                "reason": "available",
                "revision_id": _digest({"p": 1}),
            },
            "width_change": {
                "value": "-0.2",
                "reason": "available",
                "revision_id": _digest({"p": 2}),
            },
            "center_shift": {
                "value": "0.01",
                "reason": "available",
                "revision_id": _digest({"p": 3}),
            },
            "directional_efficiency": {
                "value": "0.05",
                "reason": "available",
                "revision_id": _digest({"p": 4}),
            },
        },
        "history_refs": history_refs,
    }
    case = {"case_id": _digest(case_body), **case_body}
    evidence = {"source_rows": source_rows, "cases": [case]}
    path_facts = {
        "cases": {
            case["case_id"]: {
                "structure.geometry.range-traversal": {
                    "value": "0.2",
                    "reason": "available",
                    "revision_id": _digest({"path": 1}),
                },
                "structure.geometry.counter-direction-excursion-share": {
                    "value": "0.8",
                    "reason": "available",
                    "revision_id": _digest({"path": 2}),
                },
            }
        }
    }
    strata = {
        case["case_id"]: {
            "era": "STAR_AND_CHINEXT_20PCT",
            "board_family": "STAR",
            "cell": "STAR_AND_CHINEXT_20PCT/STAR",
            "plan_role": "fallback",
            "plan_slot": 25,
            "window_id": "STAR_AND_CHINEXT_20PCT|STAR|25",
            "readiness_source_member_closure_digest": "sha256:" + "1" * 64,
        }
    }
    artifact = worker_paths.runs / job_id / "stage-p015-review-prepare-test"
    artifact.mkdir(parents=True)
    payloads = {
        "evidence.json": evidence,
        "path-facts.json": path_facts,
        "case-strata.json": strata,
    }
    files = {}
    for name, value in payloads.items():
        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        (artifact / name).write_bytes(raw)
        files[name] = "sha256:" + hashlib.sha256(raw).hexdigest()
    manifest = {"files": files}
    manifest_raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    manifest_sha = "sha256:" + hashlib.sha256(manifest_raw).hexdigest()
    (artifact / "manifest.json").write_bytes(manifest_raw)
    (artifact / "READY").write_text(manifest_sha + "\n", encoding="ascii")
    status = {
        "schema_version": "quanthus.stage-worker-status.v1",
        "job_id": job_id,
        "kind": STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_PREPARE_JOB,
        "code_revision": REVISION,
        "input_digest": _digest({"job": job_id}),
        "state": "succeeded",
        "stage": "terminal",
        "submitted_at": "2026-09-14T16:30:00+00:00",
        "started_at": "2026-09-14T16:30:01+00:00",
        "completed_at": "2026-09-14T16:30:02+00:00",
        "result_ref": {
            "artifact_root": str(artifact),
            "manifest_sha256": manifest_sha,
            "complete_reserve_additional_review_prepare": {
                "development_selected_case_count": 1,
                "case_strata_digest": _digest(strata),
                "review_ready": True,
            },
        },
        "error": None,
        "exit_code": 0,
        "updated_at": "2026-09-14T16:30:02+00:00",
    }
    (worker_paths.status / f"{job_id}.json").write_text(json.dumps(status), encoding="utf-8")

    observed = read_status(worker_paths, job_id)
    assert observed["review_ledger_sealed"] is False
    assert "selection_transport" not in observed
    index = observed["review_transport_index"]
    assert index["schema_version"] == (
        "quanthus.stage-complete-reserve-additional-primary-review-index.v1"
    )
    assert index["case_count"] == 1
    assert index["threshold_basis_included"] is False
    assert index["candidate_output_included"] is False
    assert index["envelope_measurement_included"] is False
    page = read_status(worker_paths, index["pages"][0]["job_id"])
    decoded = json.loads(gzip.decompress(base64.b64decode(page["review_transport"]["payload"])))
    assert decoded["schema_version"] == (
        "quanthus.stage-complete-reserve-additional-primary-review-transport.v1"
    )
    assert decoded["cases"][0]["resource_stratum"]["window_id"] == (
        "STAR_AND_CHINEXT_20PCT|STAR|25"
    )
    assert "threshold_basis" not in decoded
    assert "candidate_grid" not in decoded
    assert "envelope_measurements" not in decoded


def test_p015_seal_reuses_frozen_p014_ledger_without_rewriting_it(tmp_path: Path) -> None:
    worker_paths = paths(tmp_path)
    worker_paths.ensure()
    config_root = worker_paths.control_repo / "config/stage-learning"
    config_root.mkdir(parents=True, exist_ok=True)

    p014_job_id = "stage_" + "a" * 32
    old_opinions = [
        {
            "index": index,
            "case_id": _digest({"old": index}),
            "short_term": {
                "structural_instability_20": "FALSE",
                "coherent_directional_relocation_20": "FALSE",
                "orderly_consolidation_20": "FALSE",
            },
            "fund": {
                "structural_instability_20": "FALSE",
                "coherent_directional_relocation_20": "FALSE",
                "orderly_consolidation_20": "FALSE",
            },
            "short_reason": "Frozen P014 primary opinion.",
            "fund_reason": "Frozen P014 primary opinion.",
        }
        for index in range(192)
    ]
    old_summary = {
        "atomic_reference_counts": {
            "structural_instability_20": {"FALSE": 192},
            "coherent_directional_relocation_20": {"FALSE": 192},
            "orderly_consolidation_20": {"FALSE": 192},
        },
        "atomic_disagreement_count": {
            "structural_instability_20": 0,
            "coherent_directional_relocation_20": 0,
            "orderly_consolidation_20": 0,
        },
        "projected_reference_counts": {"NEITHER": 192},
    }
    old_ledger = {
        "schema_version": "quanthus.stage-atomic-proposition-admission-qualified-review-ledger.v1",
        "work_item": "P3-STAGE-014-D",
        "case_count": 192,
        "opinions": old_opinions,
        "reference_summary": old_summary,
    }
    old_path = worker_paths.atomic_proposition_admission_qualified_review_ledger(p014_job_id)
    old_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.write_text(json.dumps(old_ledger, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    old_sha = "sha256:" + hashlib.sha256(old_path.read_bytes()).hexdigest()

    closeout = {
        "job": {"job_id": p014_job_id},
        "review_ledger": {"ledger_sha256": old_sha, "case_count": 192},
    }
    closeout_path = config_root / "stage-atomic-proposition-admission-qualified-d1-closeout.v1.json"
    closeout_path.write_text(json.dumps(closeout), encoding="utf-8")
    closeout_sha = "sha256:" + hashlib.sha256(closeout_path.read_bytes()).hexdigest()
    c_config = {
        "schema_version": "quanthus.stage-complete-reserve-additional-review-prepare.v1",
        "work_item": "P3-STAGE-015-C1",
        "state": "PREREGISTERED_EXACT_60_PRIMARY_BLIND_REVIEW_PREPARE",
        "bindings": {
            "p014_closeout": {
                "path": (
                    "config/stage-learning/"
                    "stage-atomic-proposition-admission-qualified-d1-closeout.v1.json"
                ),
                "file_sha256": closeout_sha,
                "review_ledger_sha256": old_sha,
                "review_case_count": 192,
            }
        },
    }
    (config_root / "stage-complete-reserve-additional-review-prepare.v1.json").write_text(
        json.dumps(c_config), encoding="utf-8"
    )

    job_id = "stage_" + "b" * 32
    cases = [{"case_id": _digest({"new": index})} for index in range(60)]
    evidence = {"source_rows": {}, "cases": cases}
    path_facts = {"cases": {}}
    artifact = worker_paths.runs / job_id / "stage-complete-reserve-additional-review-test"
    artifact.mkdir(parents=True)
    evidence_raw = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    paths_raw = json.dumps(path_facts, sort_keys=True, separators=(",", ":")).encode()
    (artifact / "evidence.json").write_bytes(evidence_raw)
    (artifact / "path-facts.json").write_bytes(paths_raw)
    files = {
        "evidence.json": "sha256:" + hashlib.sha256(evidence_raw).hexdigest(),
        "path-facts.json": "sha256:" + hashlib.sha256(paths_raw).hexdigest(),
    }
    manifest = {"files": files}
    manifest_raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    manifest_sha = "sha256:" + hashlib.sha256(manifest_raw).hexdigest()
    (artifact / "manifest.json").write_bytes(manifest_raw)
    (artifact / "READY").write_text(manifest_sha + "\n", encoding="ascii")
    status = {
        "schema_version": "quanthus.stage-worker-status.v1",
        "job_id": job_id,
        "kind": STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_PREPARE_JOB,
        "code_revision": REVISION,
        "input_digest": _digest({"job": job_id}),
        "state": "succeeded",
        "stage": "terminal",
        "submitted_at": "2026-09-14T16:30:00+00:00",
        "started_at": "2026-09-14T16:30:01+00:00",
        "completed_at": "2026-09-14T16:30:02+00:00",
        "result_ref": {
            "artifact_root": str(artifact),
            "manifest_sha256": manifest_sha,
            "complete_reserve_additional_review_prepare": {
                "development_selected_case_count": 60,
                "development_evidence_digest": _digest(evidence),
                "review_ready": True,
            },
        },
        "error": None,
        "exit_code": 0,
        "updated_at": "2026-09-14T16:30:02+00:00",
    }
    (worker_paths.status / f"{job_id}.json").write_text(json.dumps(status), encoding="utf-8")

    new_opinions = [
        {
            "index": index,
            "case_id": case["case_id"],
            "short_term": {
                "structural_instability_20": "FALSE",
                "coherent_directional_relocation_20": "FALSE",
                "orderly_consolidation_20": "TRUE",
            },
            "fund": {
                "structural_instability_20": "FALSE",
                "coherent_directional_relocation_20": "FALSE",
                "orderly_consolidation_20": "TRUE",
            },
            "short_reason": "New blind packet supports orderly consolidation.",
            "fund_reason": "New blind packet supports orderly consolidation.",
        }
        for index, case in enumerate(cases)
    ]
    submission = {
        "schema_version": "quanthus.stage-complete-reserve-additional-review-submission.v1",
        "work_item": "P3-STAGE-015-C",
        "parent_job_id": job_id,
        "source_manifest_sha256": manifest_sha,
        "development_evidence_digest": _digest(evidence),
        "provenance": {
            "kind": "self_simulated",
            "origin_id": "chatgpt-p015-c-test",
            "session_id": "test-session",
        },
        "candidate_output_seen_during_review": False,
        "threshold_basis_seen_during_review": False,
        "envelope_measurement_seen_during_review": False,
        "selection_transport_seen_during_review": False,
        "existing_p014_opinions_seen_during_review": False,
        "future_rows_seen_during_review": False,
        "preserved_2021_seen_during_review": False,
        "opinions": new_opinions,
    }
    receipt = seal_p015_review(worker_paths, job_id, submission)
    assert receipt["sealed"] is True
    assert receipt["new_case_count"] == 60
    assert receipt["combined_case_count"] == 252
    assert receipt["frozen_p014_review_ledger_sha256"] == old_sha
    assert receipt["candidate_scoring_authorized"] is False
    assert receipt["preserved_2021_read_authorized"] is False
    combined = receipt["combined_reference_summary"]
    assert combined["atomic_reference_counts"]["orderly_consolidation_20"] == {
        "FALSE": 192,
        "TRUE": 60,
    }
    assert combined["projected_reference_counts"] == {"NEITHER": 192, "STAGE_2": 60}
    assert old_path.read_text(encoding="utf-8") == json.dumps(
        old_ledger, sort_keys=True, indent=2
    ) + "\n"

    repeated = seal_p015_review(worker_paths, job_id, submission)
    assert repeated["new_review_ledger_sha256"] == receipt["new_review_ledger_sha256"]
    changed = json.loads(json.dumps(submission))
    changed["opinions"][0]["short_reason"] = "changed"
    with pytest.raises(StageWorkerError, match="already sealed"):
        seal_p015_review(worker_paths, job_id, changed)


def test_order_rule_resolve_score_requires_successful_parent_and_is_single_job(
    tmp_path: Path,
) -> None:
    worker_paths = paths(tmp_path)
    with pytest.raises(StageWorkerError, match="config"):
        submit_job(
            worker_paths,
            code_revision=REVISION,
            kind=STAGE_ORDER_RULE_RESOLVE_SCORE_JOB,
        )

    seed_order_rule_resolve_score_parent(worker_paths)
    first = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_ORDER_RULE_RESOLVE_SCORE_JOB,
    )
    second = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_ORDER_RULE_RESOLVE_SCORE_JOB,
    )
    assert first["kind"] == STAGE_ORDER_RULE_RESOLVE_SCORE_JOB
    assert first["config"] == "config/stage-learning/order-rule-resolve-score.v1.json"
    assert _JOB_SPECS[STAGE_ORDER_RULE_RESOLVE_SCORE_JOB]["wall_seconds"] == 600
    assert second["job_id"] == first["job_id"]
    assert second["idempotent_reuse"] is True
    marker = json.loads(worker_paths.order_rule_resolve_score_budget.read_text(encoding="utf-8"))
    assert marker["job"]["job_id"] == first["job_id"]
    assert marker["input_digest"] == first["input_digest"]
    with pytest.raises(StageWorkerError, match="no retry"):
        submit_job(
            worker_paths,
            code_revision="c" * 40,
            kind=STAGE_ORDER_RULE_RESOLVE_SCORE_JOB,
        )


def test_order_rule_resolve_score_terminal_failure_cannot_open_retry(tmp_path: Path) -> None:
    worker_paths = paths(tmp_path)
    seed_order_rule_resolve_score_parent(worker_paths)
    submitted = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_ORDER_RULE_RESOLVE_SCORE_JOB,
    )
    job = StageJob.from_dict(json.loads(
        (worker_paths.queue / f"{submitted['job_id']}.json").read_text(encoding="utf-8")
    ))
    StageWorker(worker_paths)._write_status(
        job,
        state="failed",
        stage="terminal",
        started_at="2026-09-11T09:00:00+00:00",
        completed_at="2026-09-11T09:01:00+00:00",
        error="stage_order_rule_resolve_score_process_failed",
        exit_code=1,
    )
    repeated = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_ORDER_RULE_RESOLVE_SCORE_JOB,
    )
    assert repeated["job_id"] == submitted["job_id"]
    assert repeated["state"] == "failed"
    with pytest.raises(StageWorkerError, match="no retry"):
        submit_job(
            worker_paths,
            code_revision="c" * 40,
            kind=STAGE_ORDER_RULE_RESOLVE_SCORE_JOB,
        )


def test_qualification_kind_gets_its_frozen_config(tmp_path: Path) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_QUALIFICATION_JOB,
    )
    assert submitted["kind"] == STAGE_QUALIFICATION_JOB
    assert submitted["config"] == "config/stage-learning/qualification.v1.json"


def test_qualification_submit_atomically_reserves_single_access_budget(tmp_path: Path) -> None:
    worker_paths = paths(tmp_path)
    first = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_QUALIFICATION_JOB,
    )
    marker = json.loads(worker_paths.qualification_budget.read_text(encoding="utf-8"))
    assert marker["state"] == "reserved"
    assert marker["job_id"] == first["job_id"]
    assert marker["consumed_at"] is None

    with pytest.raises(StageWorkerError, match="already reserved or consumed"):
        submit_job(worker_paths, code_revision=REVISION, kind=STAGE_QUALIFICATION_JOB)

    canceled = cancel_job(worker_paths, first["job_id"])
    assert canceled["state"] == "canceled"
    assert not worker_paths.qualification_budget.exists()

    second = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_QUALIFICATION_JOB,
    )
    assert second["job_id"] != first["job_id"]
    assert worker_paths.qualification_budget.is_file()


def test_qualification_budget_is_irreversible_after_execution_access_starts(
    tmp_path: Path,
) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_QUALIFICATION_JOB,
    )
    job = StageJob.from_dict(
        json.loads(
            (worker_paths.queue / f"{submitted['job_id']}.json").read_text(encoding="utf-8")
        )
    )
    _consume_qualification_budget(worker_paths, job)
    marker = json.loads(worker_paths.qualification_budget.read_text(encoding="utf-8"))
    assert marker["state"] == "consumed"
    assert marker["consumed_at"] is not None

    with pytest.raises(StageWorkerError, match="already reserved or consumed"):
        submit_job(worker_paths, code_revision=REVISION, kind=STAGE_QUALIFICATION_JOB)


def test_qualification_pre_access_worker_failure_releases_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(
        worker_paths,
        code_revision=REVISION,
        kind=STAGE_QUALIFICATION_JOB,
    )
    worker = StageWorker(worker_paths)

    def fail_before_access(_job: StageJob) -> Path:
        raise StageWorkerError("synthetic pre-access failure")

    monkeypatch.setattr(worker, "_prepare_worktree", fail_before_access)
    result = worker.run_once()
    assert result is not None
    assert result["job_id"] == submitted["job_id"]
    assert result["state"] == "failed"
    assert "pre-access failure" in result["error"]
    assert not worker_paths.qualification_budget.exists()


def test_queue_job_file_has_exact_schema(tmp_path: Path) -> None:
    worker_paths = paths(tmp_path)
    submitted = submit_job(worker_paths, code_revision=REVISION)
    payload = json.loads(
        (worker_paths.queue / f"{submitted['job_id']}.json").read_text(encoding="utf-8")
    )
    assert set(payload) == {
        "schema_version",
        "job_id",
        "kind",
        "code_revision",
        "config",
        "submitted_at",
    }
    assert payload["schema_version"] == JOB_SCHEMA

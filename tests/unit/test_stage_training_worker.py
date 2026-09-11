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
    STAGE_CAPACITY_CENSUS_JOB,
    STAGE_DIRECTIONAL_BOUNDARY_D1_JOB,
    STAGE_DIRECTIONAL_BOUNDARY_D3_JOB,
    STAGE_ORDER_PROPOSITION_DEVELOPMENT_JOB,
    STAGE_ORDER_RULE_PREPARE_JOB,
    STAGE_ORDER_RULE_RESOLVE_SCORE_JOB,
    STAGE_QUALIFICATION_JOB,
    STAGE_REFERENCE_PILOT_JOB,
    STAGE_SECOND_GROUP_DEVELOPMENT_JOB,
    STAGE_SECOND_GROUP_DEVELOPMENT_SUPPLEMENT_JOB,
    STAGE_SECOND_GROUP_PROFILE_JOB,
    STAGE_SECOND_GROUP_REPAIR_PROFILE_JOB,
    STAGE_THIRD_GROUP_PROFILE_JOB,
    StageJob,
    StageWorker,
    StageWorkerError,
    StageWorkerPaths,
    _consume_qualification_budget,
    _digest,
    cancel_job,
    doctor,
    read_status,
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
    assert set(result["job_kinds"]) == {
        JOB_KIND,
        STAGE_REFERENCE_PILOT_JOB,
        STAGE_QUALIFICATION_JOB,
        STAGE_CAPACITY_CENSUS_JOB,
        STAGE_DIRECTIONAL_BOUNDARY_D1_JOB,
        STAGE_DIRECTIONAL_BOUNDARY_D3_JOB,
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
        STAGE_REFERENCE_PILOT_JOB,
        STAGE_QUALIFICATION_JOB,
        STAGE_CAPACITY_CENSUS_JOB,
        STAGE_DIRECTIONAL_BOUNDARY_D1_JOB,
        STAGE_DIRECTIONAL_BOUNDARY_D3_JOB,
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

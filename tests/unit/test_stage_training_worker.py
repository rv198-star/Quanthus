from __future__ import annotations

import json
from pathlib import Path

import pytest

from quanthus.policy import find_project_root
from quanthus.stage_learning.worker import (
    JOB_KIND,
    JOB_SCHEMA,
    StageJob,
    StageWorker,
    StageWorkerError,
    StageWorkerPaths,
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
    assert result["job_kind"] == JOB_KIND
    assert not any("postgres" in key.lower() or "dsn" in key.lower() for key in result)


def test_installer_and_unit_freeze_rx6800_resource_and_authority_boundaries() -> None:
    installer = (ROOT / "ops/bin/install-quanthus-stage-worker").read_text(encoding="utf-8")
    wrapper = (ROOT / "ops/bin/quanthus-stage-worker").read_text(encoding="utf-8")
    unit = (ROOT / "ops/systemd/quanthus-stage-worker.service").read_text(encoding="utf-8")

    for term in (
        '[[ "$(hostname)" == "RX6800" ]]',
        "sudo loginctl enable-linger",
        "StrictHostKeyChecking=yes",
        "quanthus-github-ro",
        "bootstrap-stage-training-node",
        "systemctl --user enable --now quanthus-stage-worker.service",
        "submit --code-revision",
        "bootstrap_state=${bootstrap_state}",
        "git@github.com:rv198-star/Quanthus.git",
    ):
        assert term in installer

    assert "quanthus.stage_learning.worker" in wrapper
    assert "MemoryMax=34G" in unit
    assert "CPUQuota=800%" in unit
    assert "Restart=always" in unit
    assert "NoNewPrivileges=true" in unit
    assert "quanthus-stage-worker serve" in unit
    assert "POSTGRES" not in unit.upper()
    assert "DATABASE" not in unit.upper()


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

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from quanthus.platform.jobs import input_digest
from quanthus.platform.models import JobRecord
from quanthus.platform.worker import ReplayWorker, _validate_run_directory


def _write_json(path: Path, value: object) -> str:
    encoded = (json.dumps(value, ensure_ascii=True, sort_keys=True) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded)
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _sealed_run(root: Path) -> tuple[str, str, str]:
    run_id = "run_recovery"
    job_id = "job_recovery"
    input_digest = "sha256:input"
    evidence_index = []
    candidates = []
    for index in range(5):
        candidate_id = f"candidate-{index}"
        version = "1.0.0"
        artifact = f"evidence/{candidate_id}-{version}.json"
        artifact_sha256 = _write_json(root / artifact, {"candidate_id": candidate_id})
        evidence_index.append(
            {
                "candidate_id": candidate_id,
                "candidate_version": version,
                "artifact": artifact,
                "artifact_sha256": artifact_sha256,
            }
        )
        candidates.append(
            {
                "candidate_id": candidate_id,
                "candidate_version": version,
                "status": "succeeded",
            }
        )
    result_sha256 = _write_json(
        root / "result.json",
        {
            "status": "succeeded",
            "evidence_index": evidence_index,
            "candidates": candidates,
        },
    )
    _write_json(
        root / "run.json",
        {
            "schema_version": "quanthus.candidate-run.v1",
            "run_id": run_id,
            "job_id": job_id,
            "input_digest": input_digest,
            "result_sha256": result_sha256,
            "candidate_count": 5,
        },
    )
    return run_id, job_id, input_digest


def test_sealed_run_can_be_validated_for_publication_recovery(tmp_path: Path) -> None:
    run_id, job_id, input_digest = _sealed_run(tmp_path)

    payload, result_sha256, candidates = _validate_run_directory(
        tmp_path,
        run_id=run_id,
        job_id=job_id,
        input_digest=input_digest,
    )

    assert payload["status"] == "succeeded"
    assert result_sha256.startswith("sha256:")
    assert len(candidates) == 5


def test_sealed_run_recovery_rejects_tampered_evidence(tmp_path: Path) -> None:
    run_id, job_id, input_digest = _sealed_run(tmp_path)
    evidence = next((tmp_path / "evidence").iterdir())
    evidence.write_text("tampered\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="sealed_run_evidence_checksum_mismatch"):
        _validate_run_directory(
            tmp_path,
            run_id=run_id,
            job_id=job_id,
            input_digest=input_digest,
        )


def test_worker_records_deterministic_attempt_error_without_stopping_supervisor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(timezone.utc)
    input_ref = {"candidate_set": "all-five"}
    job = JobRecord(
        job_id="job_bad_artifact",
        kind="quanthus.candidate-replay.v1",
        actor_user_id="usr_test",
        idempotency_key="bad-artifact",
        input_ref=input_ref,
        state="running",
        progress=1,
        cancel_requested=False,
        attempt=1,
        result_ref=None,
        error_reason=None,
        created_at=now,
        updated_at=now,
        queue="replay",
        input_digest=input_digest(input_ref),
        concurrency_key="candidate-set:all-five",
        available_at=now,
        stage="starting",
        claimed_by="worker-test",
        claim_generation=1,
    )

    class Store:
        terminal: tuple[object, ...] | None = None

        def claim_job(self, queue: str, worker_id: str, lease_seconds: int) -> JobRecord:
            assert (queue, worker_id, lease_seconds) == ("replay", "worker-test", 300)
            return job

        def finish_claimed_job(self, *args: object, **kwargs: object) -> JobRecord:
            self.terminal = (*args, kwargs)
            return job

    store = Store()
    worker = ReplayWorker(
        store,  # type: ignore[arg-type]
        worker_id="worker-test",
        run_root=tmp_path,
        lease_seconds=300,
        renew_seconds=30,
        terminate_seconds=1,
    )
    monkeypatch.setattr(
        worker,
        "_execute",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("sealed_run_manifest_mismatch")),
    )

    assert worker.run_once() is True
    assert store.terminal is not None
    assert store.terminal[:2] == (job.job_id, 1)
    terminal = store.terminal[2]
    assert isinstance(terminal, dict)
    assert terminal["state"] == "failed"
    assert terminal["error_reason"] == (
        "worker_error:RuntimeError:sealed_run_manifest_mismatch"
    )


def test_worker_persists_bounded_candidate_failure_detail(tmp_path: Path) -> None:
    class Store:
        updates: list[dict[str, object]]

        def __init__(self) -> None:
            self.updates = []

        def update_claimed_job(
            self,
            job_id: str,
            claim_generation: int,
            *,
            stage: str,
            progress: int,
            progress_detail: dict[str, object],
        ) -> None:
            self.updates.append(
                {
                    "job_id": job_id,
                    "claim_generation": claim_generation,
                    "stage": stage,
                    "progress": progress,
                    "progress_detail": progress_detail,
                }
            )

    progress_path = tmp_path / "progress.log"
    progress_path.write_text(
        json.dumps(
            {
                "event": "candidate-batch-progress",
                "stage": "governed-candidate-failed",
                "error_type": "PortfolioReplayError",
                "error_message": "held security evidence is unavailable",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    store = Store()
    worker = ReplayWorker(
        store,  # type: ignore[arg-type]
        worker_id="worker-test",
        run_root=tmp_path,
        lease_seconds=300,
        renew_seconds=30,
        terminate_seconds=1,
    )

    offset = worker._publish_progress(progress_path, 0, "job-test", 4)

    assert offset == progress_path.stat().st_size
    assert store.updates == [
        {
            "job_id": "job-test",
            "claim_generation": 4,
            "stage": "governed-candidate-failed",
            "progress": 80,
            "progress_detail": {
                "error_type": "PortfolioReplayError",
                "error_message": "held security evidence is unavailable",
            },
        }
    ]


def test_worker_stops_child_before_hard_memory_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(timezone.utc)
    input_ref = {
        "provider_config": "provider.json",
        "scope_start": "2020-01-01",
        "scope_end": "2020-01-31",
    }
    job = JobRecord(
        job_id="job_memory_guard",
        kind="quanthus.candidate-replay.v1",
        actor_user_id="usr_test",
        idempotency_key="memory-guard",
        input_ref=input_ref,
        state="running",
        progress=1,
        cancel_requested=False,
        attempt=1,
        result_ref=None,
        error_reason=None,
        created_at=now,
        updated_at=now,
        queue="replay",
        input_digest=input_digest(input_ref),
        concurrency_key="memory-guard",
        available_at=now,
        stage="starting",
        claimed_by="worker-test",
        claim_generation=1,
    )

    class Store:
        terminal: dict[str, object] | None = None
        update: dict[str, object] | None = None

        def claim_job(self, *_args: object) -> JobRecord:
            return job

        def get_job(self, _job_id: str) -> JobRecord:
            return job

        def update_claimed_job(
            self, _job_id: str, _generation: int, **values: object
        ) -> JobRecord:
            self.update = values
            return job

        def finish_claimed_job(
            self, _job_id: str, _generation: int, **values: object
        ) -> JobRecord:
            self.terminal = values
            return job

    class Process:
        returncode = -15
        terminated = False

        def poll(self) -> int | None:
            return self.returncode if self.terminated else None

        def terminate(self) -> None:
            self.terminated = True

        def wait(self, timeout: float) -> int:
            del timeout
            return self.returncode

        def kill(self) -> None:
            self.terminated = True

    process = Process()
    monkeypatch.setattr("quanthus.platform.worker.subprocess.Popen", lambda *_a, **_k: process)
    memory_current = tmp_path / "memory.current"
    memory_current.write_text(str(15 * 1024**3), encoding="ascii")
    store = Store()
    worker = ReplayWorker(
        store,  # type: ignore[arg-type]
        worker_id="worker-test",
        run_root=tmp_path / "runs",
        lease_seconds=300,
        renew_seconds=30,
        terminate_seconds=1,
        memory_current_path=memory_current,
        memory_stop_bytes=14 * 1024**3,
    )

    assert worker.run_once() is True
    assert process.terminated is True
    assert store.update == {
        "stage": "resource-limit-exceeded",
        "progress": 5,
        "progress_detail": {
            "reason": "replay_memory_guard_exceeded",
            "memory_current_bytes": 15 * 1024**3,
            "memory_stop_bytes": 14 * 1024**3,
        },
    }
    assert store.terminal is not None
    assert store.terminal["state"] == "failed"
    assert store.terminal["result_ref"] is None
    assert str(store.terminal["error_reason"]).startswith(
        "replay_memory_guard_exceeded:"
    )
    assert not (tmp_path / "runs" / "runs").exists()

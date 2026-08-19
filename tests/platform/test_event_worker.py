from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from quanthus.platform.event_worker import (
    EventLibraryEnvelope,
    EventWorker,
    _event_input,
    _validate_run_directory,
)
from quanthus.platform.jobs import P4_EVENT_SCAN_JOB, input_digest
from quanthus.platform.models import JobRecord

GOVERNED_INPUT = {
    "code_revision": "a" * 40,
    "source_policy": "config/p4-event-scan-source.research.v1.json",
    "provider_config": "config/replay-data-provider.development.v2.json",
    "rule_card": "config/p4-candidate-price-volume-transition.research.v1.json",
    "split_policy": "config/p4-event-split-policy.research.v1.json",
}


def _job(*, kind: str = P4_EVENT_SCAN_JOB) -> JobRecord:
    now = datetime.now(timezone.utc)
    return JobRecord(
        job_id="job_0123456789abcdef0123456789abcdef",
        kind=kind,
        actor_user_id="usr_test",
        idempotency_key="p4-event-test",
        input_ref=dict(GOVERNED_INPUT),
        input_digest=input_digest(GOVERNED_INPUT),
        queue="event",
        concurrency_key="p4-event-scan:development-v1",
        state="running",
        stage="starting",
        progress=1,
        cancel_requested=False,
        attempt=1,
        result_ref=None,
        error_reason=None,
        created_at=now,
        updated_at=now,
        claimed_by="event-test",
        claim_generation=2,
    )


def test_event_input_is_exact_and_rejects_path_substitution() -> None:
    assert _event_input(GOVERNED_INPUT) == GOVERNED_INPUT

    changed = dict(GOVERNED_INPUT)
    changed["provider_config"] = "/tmp/provider.json"
    with pytest.raises(RuntimeError, match="provider_config_not_governed"):
        _event_input(changed)

    changed = dict(GOVERNED_INPUT)
    changed["arbitrary_command"] = "bash"
    with pytest.raises(RuntimeError, match="input_contract_mismatch"):
        _event_input(changed)


def test_event_worker_claims_only_event_queue_and_kind(tmp_path: Path) -> None:
    job = _job(kind="quanthus.candidate-replay.v1")

    class Store:
        finished: dict[str, Any] | None = None

        def claim_job(self, queue: str, worker_id: str, lease_seconds: int) -> JobRecord:
            assert (queue, worker_id, lease_seconds) == ("event", "event-test", 300)
            return job

        def finish_claimed_job(
            self,
            job_id: str,
            generation: int,
            **kwargs: Any,
        ) -> JobRecord:
            assert (job_id, generation) == (job.job_id, 2)
            self.finished = kwargs
            return job

    store = Store()
    worker = EventWorker(
        store,  # type: ignore[arg-type]
        worker_id="event-test",
        run_root=tmp_path,
        lease_seconds=300,
        renew_seconds=30,
        terminate_seconds=1,
    )

    assert worker.run_once() is True
    assert store.finished == {
        "state": "failed",
        "result_ref": None,
        "error_reason": "job_kind_not_admitted",
    }


def test_event_worker_recovers_sealed_run_without_rerunning_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _job()
    run_id = f"run_{job.job_id.removeprefix('job_')}"
    final = tmp_path / "runs" / run_id
    final.mkdir(parents=True)
    expected_result = EventLibraryEnvelope(
        root=final / "event-library",
        manifest_sha256="sha256:" + "1" * 64,
        content_digest="sha256:" + "2" * 64,
        candidate_events=12,
        executable_events=10,
        not_executable_events=2,
    )
    expected_ref = {
        "run_id": run_id,
        "artifact_ref": str(final),
        "result_sha256": expected_result.manifest_sha256,
    }

    class Store:
        updates: list[dict[str, Any]]
        finished: dict[str, Any] | None

        def __init__(self) -> None:
            self.updates = []
            self.finished = None

        def get_job(self, job_id: str) -> JobRecord:
            assert job_id == job.job_id
            return job

        def update_claimed_job(self, _job_id: str, _generation: int, **kwargs: Any) -> JobRecord:
            self.updates.append(kwargs)
            return job

        def finish_claimed_job(
            self, _job_id: str, _generation: int, **kwargs: Any
        ) -> JobRecord:
            self.finished = kwargs
            return job

    store = Store()
    monkeypatch.setattr(
        "quanthus.platform.event_worker._validate_run_directory",
        lambda *_args, **_kwargs: (expected_result, expected_ref),
    )
    worker = EventWorker(
        store,  # type: ignore[arg-type]
        worker_id="event-test",
        run_root=tmp_path,
        lease_seconds=300,
        renew_seconds=30,
        terminate_seconds=1,
    )

    worker._execute(job.job_id, 2, job.input_ref, job.input_digest)

    assert store.updates == [
        {
            "stage": "recovering-sealed-event-run",
            "progress": 98,
            "progress_detail": {"run_id": run_id, "candidate_events": 12},
        }
    ]
    assert store.finished == {
        "state": "succeeded",
        "result_ref": expected_ref,
        "error_reason": None,
    }


def test_validate_event_run_rejects_manifest_identity_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = EventLibraryEnvelope(
        root=tmp_path / "event-library",
        manifest_sha256="sha256:" + "1" * 64,
        content_digest="sha256:" + "2" * 64,
        candidate_events=4,
        executable_events=3,
        not_executable_events=1,
    )
    event_root = tmp_path / "event-library"
    event_root.mkdir()
    (event_root / "manifest.json").write_text(
        '{"code_revision":"' + "a" * 40 + '"}\n', encoding="utf-8"
    )
    (tmp_path / "run-manifest.json").write_text(
        "{}\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        "quanthus.platform.event_worker._verify_event_library",
        lambda _path: result,
    )

    with pytest.raises(RuntimeError, match="event_run_manifest_mismatch"):
        _validate_run_directory(
            tmp_path,
            run_id="run_test",
            job_id="job_test",
            input_digest="sha256:test",
            code_revision="a" * 40,
        )

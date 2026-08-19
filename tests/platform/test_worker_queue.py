from __future__ import annotations

from datetime import datetime, timezone

import pytest

from quanthus.platform.jobs import input_digest
from quanthus.platform.models import JobRecord
from quanthus.platform.persistence import (
    IdempotencyConflictError,
    InMemoryPlatformStore,
)


def _job(key: str, input_ref: dict[str, str]) -> JobRecord:
    now = datetime.now(timezone.utc)
    return JobRecord(
        job_id=f"job_{key}",
        kind="quanthus.candidate-replay.v1",
        actor_user_id="usr_admin",
        idempotency_key=key,
        input_ref=input_ref,
        input_digest=input_digest(input_ref),
        queue="replay",
        concurrency_key=f"candidate:{input_ref['candidate_id']}",
        state="queued",
        stage="queued",
        progress=0,
        cancel_requested=False,
        attempt=0,
        result_ref=None,
        error_reason=None,
        created_at=now,
        updated_at=now,
        available_at=now,
    )


def test_idempotency_key_rejects_different_input() -> None:
    store = InMemoryPlatformStore()
    first = _job("request-1", {"candidate_id": "candidate-a"})
    store.create_job(first)

    changed = _job("request-1", {"candidate_id": "candidate-b"})
    with pytest.raises(IdempotencyConflictError, match="different_input"):
        store.create_job(changed)


def test_terminal_job_allows_explicit_same_input_rerun() -> None:
    store = InMemoryPlatformStore()
    first = _job("request-1", {"candidate_id": "candidate-a"})
    store.create_job(first)
    running = store.start_job(first.job_id, first.created_at)
    store.finish_job(
        JobRecord(
            **{
                **running.__dict__,
                "state": "succeeded",
                "progress": 100,
                "completed_at": first.created_at,
            }
        )
    )

    rerun = _job("request-2", {"candidate_id": "candidate-a"})
    observed, created = store.create_job(rerun)
    assert created is True
    assert observed.job_id == rerun.job_id


def test_same_candidate_cannot_have_two_active_jobs() -> None:
    store = InMemoryPlatformStore()
    store.create_job(_job("request-1", {"candidate_id": "candidate-a"}))

    with pytest.raises(RuntimeError, match="active_concurrency_key_exists"):
        store.create_job(_job("request-2", {"candidate_id": "candidate-a"}))


def test_local_runner_never_dispatches_replay_queue() -> None:
    from quanthus.platform.jobs import LocalJobRunner

    store = InMemoryPlatformStore()
    replay = _job("request-1", {"candidate_id": "candidate-a"})
    store.create_job(replay)
    calls = 0

    def handler(*_args):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return {}

    runner = LocalJobRunner(store, {replay.kind: handler})
    assert runner.resume_pending() == ()
    assert store.get_job(replay.job_id).state == "queued"  # type: ignore[union-attr]
    assert calls == 0

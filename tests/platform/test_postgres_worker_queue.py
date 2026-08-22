from __future__ import annotations

import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from quanthus.platform.jobs import input_digest
from quanthus.platform.manifest import load_feature_manifest
from quanthus.platform.models import JobRecord, UserRecord
from quanthus.platform.persistence import PostgresPlatformStore


def _dsn() -> str:
    value = os.environ.get("QUANTHUS_TEST_POSTGRES_DSN", "").strip()
    if not value:
        pytest.skip("QUANTHUS_TEST_POSTGRES_DSN is not configured")
    return value


def test_two_workers_reclaim_expired_lease_and_fence_old_generation() -> None:
    dsn = _dsn()
    try:
        import psycopg  # type: ignore[import-not-found]
        from psycopg import sql
        from psycopg.rows import dict_row  # type: ignore[import-not-found]
    except ImportError as exc:
        pytest.fail(f"platform dependencies are required for PostgreSQL tests: {exc}")

    schema = "quanthus_worker_queue_" + uuid.uuid4().hex
    assert re.fullmatch(r"[a-z0-9_]+", schema)
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

    def connect_scoped(observed_dsn: str):  # type: ignore[no-untyped-def]
        connection = psycopg.connect(observed_dsn, row_factory=dict_row)
        connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        return connection

    store = PostgresPlatformStore(dsn, connect=connect_scoped)
    try:
        store.migrate(load_feature_manifest().migrations)
        now = datetime.now(timezone.utc)
        user = UserRecord(
            user_id="usr_worker_queue",
            username="worker-queue",
            password_hash="not-used",
            role="admin",
            active=True,
            mfa_secret_ciphertext=None,
            mfa_enabled=False,
            created_at=now,
        )
        store.create_user(user)
        input_ref = {"candidate_set": "all-five", "request": "queue-fencing"}
        job = JobRecord(
            job_id="job_worker_queue",
            kind="quanthus.candidate-replay.v1",
            actor_user_id=user.user_id,
            idempotency_key="worker-queue",
            input_ref=input_ref,
            state="queued",
            progress=0,
            cancel_requested=False,
            attempt=0,
            result_ref=None,
            error_reason=None,
            created_at=now,
            updated_at=now,
            queue="replay",
            input_digest=input_digest(input_ref),
            concurrency_key="candidate-set:all-five",
            available_at=now,
        )
        store.create_job(job)

        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = tuple(
                executor.map(
                    lambda worker: store.claim_job("replay", worker, 300),
                    ("worker-a", "worker-b"),
                )
            )
        won = [claim for claim in claims if claim is not None]
        assert len(won) == 1
        first = won[0]
        assert first.claim_generation == 1

        with connect_scoped(dsn) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE platform_jobs
                SET lease_expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second'
                WHERE job_id = %s
                """,
                (first.job_id,),
            )

        reclaimed = store.claim_job("replay", "worker-recovery", 300)
        assert reclaimed is not None
        assert reclaimed.job_id == first.job_id
        assert reclaimed.claim_generation == 2
        assert reclaimed.attempt == 2

        assert store.renew_job_lease(first.job_id, 1, first.claimed_by or "", 300) is False
        with pytest.raises(RuntimeError, match="stale_claim_generation"):
            store.update_claimed_job(
                first.job_id,
                1,
                stage="stale-worker",
                progress=90,
                progress_detail={},
            )
        with pytest.raises(RuntimeError, match="stale_claim_generation"):
            store.finish_claimed_job(
                first.job_id,
                1,
                state="succeeded",
                result_ref={"invalid": True},
                error_reason=None,
            )

        current = store.get_job(first.job_id)
        assert current is not None
        assert current.state == "running"
        assert current.claim_generation == 2
        assert current.claimed_by == "worker-recovery"

        store.finish_claimed_job(
            current.job_id,
            current.claim_generation,
            state="succeeded",
            result_ref={"status": "queue-test-complete"},
            error_reason=None,
        )
        replay_job = JobRecord(
            **{
                **job.__dict__,
                "job_id": "job_heavy_replay",
                "idempotency_key": "heavy-replay",
                "input_digest": input_digest({"request": "heavy-replay"}),
                "input_ref": {"request": "heavy-replay"},
                "concurrency_key": "heavy-replay",
                "state": "queued",
                "progress": 0,
                "attempt": 0,
                "claimed_by": None,
                "claim_generation": 0,
                "lease_expires_at": None,
            }
        )
        event_job = JobRecord(
            **{
                **replay_job.__dict__,
                "job_id": "job_heavy_event",
                "kind": "quanthus.p4-event-scan.v1",
                "idempotency_key": "heavy-event",
                "input_digest": input_digest({"request": "heavy-event"}),
                "input_ref": {"request": "heavy-event"},
                "queue": "event",
                "concurrency_key": "heavy-event",
            }
        )
        store.create_job(replay_job)
        store.create_job(event_job)

        with ThreadPoolExecutor(max_workers=2) as executor:
            heavy_claims = tuple(
                executor.map(
                    lambda request: store.claim_job(request[0], request[1], 300),
                    (("replay", "replay-worker"), ("event", "event-worker")),
                )
            )
        heavy_winners = [claim for claim in heavy_claims if claim is not None]
        assert len(heavy_winners) == 1
        winner = heavy_winners[0]
        blocked_queue = "event" if winner.queue == "replay" else "replay"
        assert store.claim_job(blocked_queue, "blocked-heavy-worker", 300) is None

        store.finish_claimed_job(
            winner.job_id,
            winner.claim_generation,
            state="succeeded",
            result_ref={"status": "heavy-queue-test-complete"},
            error_reason=None,
        )
        unblocked = store.claim_job(blocked_queue, "unblocked-heavy-worker", 300)
        assert unblocked is not None
        assert unblocked.queue == blocked_queue
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )

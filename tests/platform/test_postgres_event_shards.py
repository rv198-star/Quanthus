from __future__ import annotations

import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from quanthus.platform.jobs import input_digest
from quanthus.platform.manifest import load_feature_manifest
from quanthus.platform.models import JobRecord, JobShardRecord, UserRecord
from quanthus.platform.persistence import IdempotencyConflictError, PostgresPlatformStore


def _dsn() -> str:
    value = os.environ.get("QUANTHUS_TEST_POSTGRES_DSN", "").strip()
    if not value:
        pytest.skip("QUANTHUS_TEST_POSTGRES_DSN is not configured")
    return value


def test_event_shard_claim_reclaim_fencing_idempotency_and_parent_cancel() -> None:
    dsn = _dsn()
    try:
        import psycopg  # type: ignore[import-not-found]
        from psycopg import sql
        from psycopg.rows import dict_row  # type: ignore[import-not-found]
    except ImportError as exc:
        pytest.fail(f"platform dependencies are required for PostgreSQL tests: {exc}")

    schema = "quanthus_event_shards_" + uuid.uuid4().hex
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
        store.create_user(
            UserRecord(
                user_id="usr_event_shards",
                username="event-shards",
                password_hash="not-used",
                role="admin",
                active=True,
                mfa_secret_ciphertext=None,
                mfa_enabled=False,
                created_at=now,
            )
        )
        parent_input = {"request": "event-shard-contract"}
        parent = JobRecord(
            job_id="job_event_shards",
            kind="quanthus.p4-event-scan.v1",
            actor_user_id="usr_event_shards",
            idempotency_key="event-shards",
            input_ref=parent_input,
            state="queued",
            progress=0,
            cancel_requested=False,
            attempt=0,
            result_ref=None,
            error_reason=None,
            created_at=now,
            updated_at=now,
            queue="event",
            input_digest=input_digest(parent_input),
            concurrency_key="p4-event-scan:development-v1",
            available_at=now,
        )
        store.create_job(parent)
        running = store.claim_job("event", "parent-worker", 300)
        assert running is not None
        payload = {"schema_version": "task-v1", "kind": "test"}
        shard = JobShardRecord(
            parent_job_id=running.job_id,
            parent_claim_generation=running.claim_generation,
            shard_id="stage-000",
            stage="stage",
            ordinal=0,
            payload=payload,
            input_digest=input_digest(payload),
            state="queued",
            attempt=0,
            result_ref=None,
            error_reason=None,
            created_at=now,
            updated_at=now,
            available_at=now,
        )
        assert store.create_job_shards(
            running.job_id, running.claim_generation, (shard,)
        ) == (shard,)
        assert store.create_job_shards(
            running.job_id, running.claim_generation, (shard,)
        ) == (shard,)
        changed = replace(shard, payload={**payload, "changed": True})
        with pytest.raises(IdempotencyConflictError):
            store.create_job_shards(
                running.job_id, running.claim_generation, (changed,)
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = tuple(
                executor.map(
                    lambda worker: store.claim_job_shard(worker, 300),
                    ("shard-a", "shard-b"),
                )
            )
        won = [item for item in claims if item is not None]
        assert len(won) == 1
        first = won[0]
        assert first.claim_generation == 1

        with connect_scoped(dsn) as connection:
            connection.execute(
                """
                UPDATE platform_job_shards
                SET lease_expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second'
                WHERE parent_job_id = %s AND shard_id = %s
                """,
                (first.parent_job_id, first.shard_id),
            )
        reclaimed = store.claim_job_shard("shard-recovery", 300)
        assert reclaimed is not None
        assert reclaimed.claim_generation == 2
        assert reclaimed.attempt == 2
        assert (
            store.renew_job_shard_lease(
                first.parent_job_id,
                first.shard_id,
                first.claim_generation,
                first.claimed_by or "",
                300,
            )
            is False
        )
        with pytest.raises(RuntimeError, match="stale_shard_claim_generation"):
            store.finish_job_shard(
                first.parent_job_id,
                first.shard_id,
                first.claim_generation,
                state="succeeded",
                result_ref={"stale": True},
                error_reason=None,
            )

        store.request_job_cancel(running.job_id, datetime.now(timezone.utc))
        canceled = store.finish_job_shard(
            reclaimed.parent_job_id,
            reclaimed.shard_id,
            reclaimed.claim_generation,
            state="succeeded",
            result_ref={"must_not_publish": True},
            error_reason=None,
        )
        assert canceled.state == "canceled"
        assert canceled.result_ref is None
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone

import pytest

from quanthus.platform.jobs import EXPERT_EVALUATION_JOB, input_digest
from quanthus.platform.manifest import load_feature_manifest
from quanthus.platform.models import JobRecord, UserRecord
from quanthus.platform.persistence import PostgresPlatformStore


def _dsn() -> str:
    value = os.environ.get("QUANTHUS_TEST_POSTGRES_DSN", "").strip()
    if not value:
        pytest.skip("QUANTHUS_TEST_POSTGRES_DSN is not configured")
    return value


def _job(job_id: str, actor_user_id: str) -> JobRecord:
    now = datetime.now(timezone.utc)
    input_ref = {"schema_version": "fixture", "semantic_input": "same-content"}
    return JobRecord(
        job_id=job_id,
        kind=EXPERT_EVALUATION_JOB,
        actor_user_id=actor_user_id,
        idempotency_key=job_id,
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
        concurrency_key=None,
        available_at=now,
    )


def test_expert_run_registration_is_fenced_deduplicated_and_summary_only() -> None:
    dsn = _dsn()
    try:
        import psycopg  # type: ignore[import-not-found]
        from psycopg import sql
        from psycopg.rows import dict_row  # type: ignore[import-not-found]
    except ImportError as exc:
        pytest.fail(f"platform dependencies are required for PostgreSQL tests: {exc}")

    schema = "quanthus_expert_runs_" + uuid.uuid4().hex
    assert re.fullmatch(r"[a-z0-9_]+", schema)
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

    def connect_scoped(observed_dsn: str):  # type: ignore[no-untyped-def]
        connection = psycopg.connect(observed_dsn, row_factory=dict_row)
        connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        return connection

    store = PostgresPlatformStore(dsn, connect=connect_scoped)
    identities = (
        {
            "assessment_id": "expert.fixture",
            "assessment_version": "1.0.0",
            "digest": "sha256:" + "1" * 64,
        },
    )
    summary = {
        "operation": "development_evaluation",
        "spec_count": 1,
        "available_artifact_count": 5,
        "skipped_artifact_count": 0,
        "intervention_count": 0,
    }
    try:
        store.migrate(load_feature_manifest().migrations)
        user = UserRecord(
            user_id="usr_expert_runs",
            username="expert-runs",
            password_hash="not-used",
            role="admin",
            active=True,
            mfa_secret_ciphertext=None,
            mfa_enabled=False,
            created_at=datetime.now(timezone.utc),
        )
        store.create_user(user)

        first, _ = store.create_job(_job("job_expert_first", user.user_id))
        first_claim = store.claim_job("replay", "worker-a", 300)
        assert first_claim is not None and first_claim.job_id == first.job_id
        run, terminal = store.publish_expert_evaluation_run(
            first.job_id,
            first_claim.claim_generation,
            run_id="expert_run_same_content",
            artifact_ref="/runs/expert_run_same_content",
            manifest_digest="sha256:" + "2" * 64,
            evaluation_identities=identities,
            strategy_identity=None,
            summary=summary,
        )
        assert terminal.state == "succeeded"
        assert run.job_id == first.job_id
        assert store.expert_evaluation_run(run.run_id) == run
        assert store.expert_evaluation_run_for_input(first.input_digest) == run

        second, _ = store.create_job(_job("job_expert_adopt", user.user_id))
        second_claim = store.claim_job("replay", "worker-b", 300)
        assert second_claim is not None and second_claim.job_id == second.job_id
        adopted, adopted_terminal = store.publish_expert_evaluation_run(
            second.job_id,
            second_claim.claim_generation,
            run_id=run.run_id,
            artifact_ref=run.artifact_ref,
            manifest_digest=run.manifest_digest,
            evaluation_identities=identities,
            strategy_identity=None,
            summary=summary,
        )
        assert adopted == run
        assert adopted_terminal.state == "succeeded"
        assert adopted_terminal.result_ref is not None
        assert adopted_terminal.result_ref["run_id"] == run.run_id

        divergent, _ = store.create_job(_job("job_expert_divergent", user.user_id))
        divergent_claim = store.claim_job("replay", "worker-c", 300)
        assert divergent_claim is not None and divergent_claim.job_id == divergent.job_id
        with pytest.raises(RuntimeError, match="expert_run_registration_conflict"):
            store.publish_expert_evaluation_run(
                divergent.job_id,
                divergent_claim.claim_generation,
                run_id="expert_run_different_bytes",
                artifact_ref="/runs/expert_run_different_bytes",
                manifest_digest="sha256:" + "4" * 64,
                evaluation_identities=identities,
                strategy_identity=None,
                summary=summary,
            )

        stale, _ = store.create_job(_job("job_expert_stale", user.user_id))
        stale_claim = store.claim_job("replay", "worker-a", 300)
        assert stale_claim is not None and stale_claim.job_id == stale.job_id
        with pytest.raises(RuntimeError, match="stale_claim_generation"):
            store.publish_expert_evaluation_run(
                stale.job_id,
                stale_claim.claim_generation - 1,
                run_id="expert_run_stale",
                artifact_ref="/runs/expert_run_stale",
                manifest_digest="sha256:" + "3" * 64,
                evaluation_identities=identities,
                strategy_identity=None,
                summary=summary,
            )

        with connect_scoped(dsn) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = current_schema()
                  AND table_name LIKE 'platform_expert%'
                ORDER BY table_name
                """
            )
            assert [row["table_name"] for row in cursor.fetchall()] == [
                "platform_expert_evaluation_runs"
            ]
            cursor.execute("SELECT COUNT(*) AS count FROM platform_expert_evaluation_runs")
            assert cursor.fetchone()["count"] == 1
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )

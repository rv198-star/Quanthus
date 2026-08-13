from __future__ import annotations

import os
import re
import uuid
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


def _candidate(result_digest: str) -> tuple[dict[str, object], ...]:
    return (
        {
            "candidate_id": "candidate.test",
            "candidate_version": "1.0.0",
            "status": "succeeded",
            "result_digest": result_digest,
            "artifact": "evidence/candidate.test-1.0.0.json",
            "artifact_sha256": result_digest.replace("evidence", "artifact"),
        },
    )


def _job(job_id: str, actor_user_id: str) -> JobRecord:
    now = datetime.now(timezone.utc)
    input_ref = {"candidate_set": "test", "request": job_id}
    return JobRecord(
        job_id=job_id,
        kind="quanthus.candidate-replay.v1",
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
        concurrency_key="candidate-set:test",
        available_at=now,
    )


def test_candidate_run_publication_is_fenced_and_active_switch_is_cas() -> None:
    dsn = _dsn()
    try:
        import psycopg  # type: ignore[import-not-found]
        from psycopg import sql
        from psycopg.rows import dict_row  # type: ignore[import-not-found]
    except ImportError as exc:
        pytest.fail(f"platform dependencies are required for PostgreSQL tests: {exc}")

    schema = "quanthus_candidate_runs_" + uuid.uuid4().hex
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
        user = UserRecord(
            user_id="usr_candidate_runs",
            username="candidate-runs",
            password_hash="not-used",
            role="admin",
            active=True,
            mfa_secret_ciphertext=None,
            mfa_enabled=False,
            created_at=datetime.now(timezone.utc),
        )
        store.create_user(user)

        first, _ = store.create_job(_job("job_first", user.user_id))
        first_claim = store.claim_job("replay", "worker-a", 300)
        assert first_claim is not None and first_claim.job_id == first.job_id
        first_run, first_terminal = store.publish_candidate_run(
            first.job_id,
            first_claim.claim_generation,
            run_id="run_first",
            artifact_ref="/runs/run_first",
            result_sha256="sha256:first-artifact",
            candidates=_candidate("sha256:first-evidence"),
            expected_active_runs={"candidate.test@1.0.0": None},
        )
        assert first_run.active_switch_applied is True
        assert first_terminal.state == "succeeded"
        assert store.candidate_active_runs()[0].run_id == "run_first"
        assert store.candidate_runs("candidate.test", "1.0.0") == (first_run,)

        second, _ = store.create_job(_job("job_second", user.user_id))
        second_claim = store.claim_job("replay", "worker-a", 300)
        assert second_claim is not None and second_claim.job_id == second.job_id
        second_run, _ = store.publish_candidate_run(
            second.job_id,
            second_claim.claim_generation,
            run_id="run_second",
            artifact_ref="/runs/run_second",
            result_sha256="sha256:second-artifact",
            candidates=_candidate("sha256:second-evidence"),
            expected_active_runs={"candidate.test@1.0.0": "run_first"},
        )
        assert second_run.active_switch_applied is True
        assert store.candidate_active_runs()[0].run_id == "run_second"
        assert tuple(
            item.run_id for item in store.candidate_runs("candidate.test", "1.0.0")
        ) == ("run_second", "run_first")

        stale, _ = store.create_job(_job("job_stale", user.user_id))
        stale_claim = store.claim_job("replay", "worker-a", 300)
        assert stale_claim is not None and stale_claim.job_id == stale.job_id
        stale_run, stale_terminal = store.publish_candidate_run(
            stale.job_id,
            stale_claim.claim_generation,
            run_id="run_stale_expectation",
            artifact_ref="/runs/run_stale_expectation",
            result_sha256="sha256:stale-artifact",
            candidates=_candidate("sha256:stale-evidence"),
            expected_active_runs={"candidate.test@1.0.0": "run_first"},
        )
        assert stale_run.active_switch_applied is False
        assert stale_terminal.result_ref is not None
        assert stale_terminal.result_ref["active_switch_applied"] is False
        assert store.candidate_active_runs()[0].run_id == "run_second"

        fenced, _ = store.create_job(_job("job_fenced", user.user_id))
        fenced_claim = store.claim_job("replay", "worker-a", 300)
        assert fenced_claim is not None and fenced_claim.job_id == fenced.job_id
        with pytest.raises(RuntimeError, match="stale_claim_generation"):
            store.publish_candidate_run(
                fenced.job_id,
                fenced_claim.claim_generation - 1,
                run_id="run_fenced",
                artifact_ref="/runs/run_fenced",
                result_sha256="sha256:fenced-artifact",
                candidates=_candidate("sha256:fenced-evidence"),
                expected_active_runs={"candidate.test@1.0.0": "run_second"},
            )
        assert store.get_job(fenced.job_id).state == "running"  # type: ignore[union-attr]
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )

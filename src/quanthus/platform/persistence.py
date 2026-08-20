"""Platform persistence port with in-memory and PostgreSQL adapters."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from typing import Any, Protocol

from quanthus.platform.migrations import MIGRATION_LEDGER_SQL, migrations_for
from quanthus.platform.models import (
    CandidateActiveRunRecord,
    CandidateApprovalEventRecord,
    CandidateReviewRecord,
    CandidateRunRecord,
    ExpertEvaluationRunRecord,
    JobRecord,
    SecurityAuditRecord,
    SessionRecord,
    UserRecord,
)


class DuplicateRecordError(RuntimeError):
    pass


class IdempotencyConflictError(RuntimeError):
    pass


class PlatformStore(Protocol):
    def ready(self) -> bool: ...

    def migrate(self, modules: tuple[str, ...]) -> tuple[str, ...]: ...

    def create_user(self, user: UserRecord) -> None: ...

    def get_user_by_username(self, username_key: str) -> UserRecord | None: ...

    def get_user(self, user_id: str) -> UserRecord | None: ...

    def list_users(self) -> tuple[UserRecord, ...]: ...

    def set_user_mfa(self, user_id: str, ciphertext: str | None, enabled: bool) -> None: ...

    def create_session(self, session: SessionRecord) -> None: ...

    def get_session(self, token_hash: str) -> SessionRecord | None: ...

    def revoke_session(self, token_hash: str, revoked_at: datetime) -> bool: ...

    def append_security_audit(self, audit: SecurityAuditRecord) -> None: ...

    def failed_login_count(self, username_key: str, since: datetime) -> int: ...

    def create_job(self, job: JobRecord) -> tuple[JobRecord, bool]: ...

    def get_job(self, job_id: str) -> JobRecord | None: ...

    def list_jobs(self, kind: str | None = None, limit: int = 50) -> tuple[JobRecord, ...]: ...

    def start_job(self, job_id: str, started_at: datetime) -> JobRecord: ...

    def update_job(self, job: JobRecord) -> None: ...

    def finish_job(self, job: JobRecord) -> JobRecord: ...

    def request_job_cancel(self, job_id: str, requested_at: datetime) -> tuple[JobRecord, bool]: ...

    def finish_job_canceled(self, job_id: str, completed_at: datetime) -> JobRecord: ...

    def resumable_jobs(self) -> tuple[JobRecord, ...]: ...

    def claim_job(self, queue: str, worker_id: str, lease_seconds: int) -> JobRecord | None: ...

    def renew_job_lease(
        self, job_id: str, claim_generation: int, worker_id: str, lease_seconds: int
    ) -> bool: ...

    def update_claimed_job(
        self,
        job_id: str,
        claim_generation: int,
        *,
        stage: str,
        progress: int,
        progress_detail: dict[str, Any],
    ) -> JobRecord: ...

    def finish_claimed_job(
        self,
        job_id: str,
        claim_generation: int,
        *,
        state: str,
        result_ref: dict[str, Any] | None,
        error_reason: str | None,
    ) -> JobRecord: ...

    def candidate_active_runs(self) -> tuple[CandidateActiveRunRecord, ...]: ...

    def candidate_run(self, run_id: str) -> CandidateRunRecord | None: ...

    def candidate_runs(
        self, strategy_id: str, strategy_version: str, limit: int = 20
    ) -> tuple[CandidateRunRecord, ...]: ...

    def publish_candidate_run(
        self,
        job_id: str,
        claim_generation: int,
        *,
        run_id: str,
        artifact_ref: str,
        result_sha256: str,
        candidates: tuple[dict[str, Any], ...],
        expected_active_runs: dict[str, str | None],
    ) -> tuple[CandidateRunRecord, JobRecord]: ...

    def expert_evaluation_run(self, run_id: str) -> ExpertEvaluationRunRecord | None: ...

    def expert_evaluation_run_for_input(
        self, input_digest: str
    ) -> ExpertEvaluationRunRecord | None: ...

    def publish_expert_evaluation_run(
        self,
        job_id: str,
        claim_generation: int,
        *,
        run_id: str,
        artifact_ref: str,
        manifest_digest: str,
        evaluation_identities: tuple[dict[str, Any], ...],
        strategy_identity: dict[str, Any] | None,
        summary: dict[str, Any],
    ) -> tuple[ExpertEvaluationRunRecord, JobRecord]: ...

    def append_candidate_review(self, review: CandidateReviewRecord) -> None: ...

    def candidate_reviews(
        self, strategy_id: str, strategy_version: str
    ) -> tuple[CandidateReviewRecord, ...]: ...

    def append_candidate_approval_event(self, event: CandidateApprovalEventRecord) -> None: ...

    def candidate_approval_events(
        self, strategy_id: str, strategy_version: str
    ) -> tuple[CandidateApprovalEventRecord, ...]: ...


class InMemoryPlatformStore:
    """Deterministic adapter for unit tests and explicitly ephemeral boot."""

    def __init__(self) -> None:
        self._users: dict[str, UserRecord] = {}
        self._username_index: dict[str, str] = {}
        self._sessions: dict[str, SessionRecord] = {}
        self._audits: list[SecurityAuditRecord] = []
        self._jobs: dict[str, JobRecord] = {}
        self._job_keys: dict[tuple[str, str, str], str] = {}
        self._candidate_reviews: list[CandidateReviewRecord] = []
        self._candidate_approval_events: list[CandidateApprovalEventRecord] = []
        self._candidate_runs: dict[str, CandidateRunRecord] = {}
        self._candidate_active_runs: dict[tuple[str, str], CandidateActiveRunRecord] = {}
        self._expert_evaluation_runs: dict[str, ExpertEvaluationRunRecord] = {}
        self._migrations: set[str] = set()
        self._lock = threading.RLock()

    def ready(self) -> bool:
        return True

    def migrate(self, modules: tuple[str, ...]) -> tuple[str, ...]:
        applied = []
        with self._lock:
            for migration in migrations_for(modules):
                if migration.migration_id not in self._migrations:
                    self._migrations.add(migration.migration_id)
                    applied.append(migration.migration_id)
        return tuple(applied)

    def create_user(self, user: UserRecord) -> None:
        key = user.username.casefold()
        with self._lock:
            if user.user_id in self._users or key in self._username_index:
                raise DuplicateRecordError("username or user id already exists")
            self._users[user.user_id] = user
            self._username_index[key] = user.user_id

    def get_user_by_username(self, username_key: str) -> UserRecord | None:
        with self._lock:
            user_id = self._username_index.get(username_key)
            return self._users.get(user_id) if user_id else None

    def get_user(self, user_id: str) -> UserRecord | None:
        with self._lock:
            return self._users.get(user_id)

    def list_users(self) -> tuple[UserRecord, ...]:
        with self._lock:
            return tuple(sorted(self._users.values(), key=lambda item: item.username.casefold()))

    def set_user_mfa(self, user_id: str, ciphertext: str | None, enabled: bool) -> None:
        with self._lock:
            user = self._users.get(user_id)
            if user is None:
                raise KeyError(user_id)
            self._users[user_id] = replace(
                user,
                mfa_secret_ciphertext=ciphertext,
                mfa_enabled=enabled,
            )

    def create_session(self, session: SessionRecord) -> None:
        with self._lock:
            if session.token_hash in self._sessions:
                raise DuplicateRecordError("session token already exists")
            self._sessions[session.token_hash] = session

    def get_session(self, token_hash: str) -> SessionRecord | None:
        with self._lock:
            return self._sessions.get(token_hash)

    def revoke_session(self, token_hash: str, revoked_at: datetime) -> bool:
        with self._lock:
            session = self._sessions.get(token_hash)
            if session is None or session.revoked_at is not None:
                return False
            self._sessions[token_hash] = replace(session, revoked_at=revoked_at)
            return True

    def append_security_audit(self, audit: SecurityAuditRecord) -> None:
        with self._lock:
            self._audits.append(audit)

    def failed_login_count(self, username_key: str, since: datetime) -> int:
        with self._lock:
            return sum(
                1
                for audit in self._audits
                if audit.event_type == "login"
                and audit.outcome == "denied"
                and audit.created_at >= since
                and audit.details.get("username_key") == username_key
            )

    def create_job(self, job: JobRecord) -> tuple[JobRecord, bool]:
        key = (job.actor_user_id, job.kind, job.idempotency_key)
        with self._lock:
            existing_id = self._job_keys.get(key)
            if existing_id is not None:
                existing = self._jobs[existing_id]
                if existing.input_digest != job.input_digest:
                    raise IdempotencyConflictError("idempotency_key_reused_with_different_input")
                return existing, False
            if job.concurrency_key is not None:
                for existing in self._jobs.values():
                    if (
                        existing.queue == job.queue
                        and existing.concurrency_key == job.concurrency_key
                        and existing.state in {"queued", "running"}
                    ):
                        raise DuplicateRecordError("active_concurrency_key_exists")
            self._jobs[job.job_id] = job
            self._job_keys[key] = job.job_id
            return job, True

    def get_job(self, job_id: str) -> JobRecord | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self, kind: str | None = None, limit: int = 50) -> tuple[JobRecord, ...]:
        with self._lock:
            jobs = (job for job in self._jobs.values() if kind is None or job.kind == kind)
            return tuple(
                sorted(jobs, key=lambda job: (job.created_at, job.job_id), reverse=True)[:limit]
            )

    def start_job(self, job_id: str, started_at: datetime) -> JobRecord:
        with self._lock:
            current = self._jobs.get(job_id)
            if current is None:
                raise KeyError(job_id)
            if current.state != "queued":
                return current
            if current.cancel_requested:
                observed = replace(
                    current,
                    state="canceled",
                    completed_at=started_at,
                    updated_at=started_at,
                )
            else:
                observed = replace(
                    current,
                    state="running",
                    progress=1,
                    attempt=current.attempt + 1,
                    started_at=started_at,
                    updated_at=started_at,
                    error_reason=None,
                )
            self._jobs[job_id] = observed
            return observed

    def update_job(self, job: JobRecord) -> None:
        with self._lock:
            current = self._jobs.get(job.job_id)
            if current is None:
                raise KeyError(job.job_id)
            if current.state in {"succeeded", "failed", "canceled"}:
                return
            self._jobs[job.job_id] = replace(
                job,
                cancel_requested=current.cancel_requested or job.cancel_requested,
            )

    def finish_job(self, job: JobRecord) -> JobRecord:
        if job.state not in {"succeeded", "failed"}:
            raise ValueError("finish_job requires a succeeded or failed record")
        with self._lock:
            current = self._jobs.get(job.job_id)
            if current is None:
                raise KeyError(job.job_id)
            terminal = (
                replace(
                    job,
                    state="canceled",
                    progress=current.progress,
                    cancel_requested=True,
                    result_ref=None,
                    error_reason=None,
                )
                if current.cancel_requested
                else job
            )
            self._jobs[job.job_id] = terminal
            return terminal

    def request_job_cancel(self, job_id: str, requested_at: datetime) -> tuple[JobRecord, bool]:
        with self._lock:
            current = self._jobs.get(job_id)
            if current is None:
                raise KeyError(job_id)
            if current.state in {"succeeded", "failed", "canceled"}:
                return current, False
            observed = replace(
                current,
                state="canceled" if current.state == "queued" else current.state,
                cancel_requested=True,
                updated_at=requested_at,
                completed_at=requested_at if current.state == "queued" else None,
                cancel_requested_at=requested_at,
            )
            self._jobs[job_id] = observed
            return observed, True

    def finish_job_canceled(self, job_id: str, completed_at: datetime) -> JobRecord:
        with self._lock:
            current = self._jobs.get(job_id)
            if current is None:
                raise KeyError(job_id)
            if current.state in {"succeeded", "failed", "canceled"}:
                return current
            if not current.cancel_requested:
                raise RuntimeError("job cancellation was not requested")
            terminal = replace(
                current,
                state="canceled",
                result_ref=None,
                error_reason=None,
                updated_at=completed_at,
                completed_at=completed_at,
            )
            self._jobs[job_id] = terminal
            return terminal

    def resumable_jobs(self) -> tuple[JobRecord, ...]:
        with self._lock:
            for job_id, job in tuple(self._jobs.items()):
                if job.state == "running":
                    self._jobs[job_id] = replace(
                        job,
                        state="queued",
                        progress=0,
                        updated_at=datetime.now(job.updated_at.tzinfo),
                    )
            return tuple(
                sorted(
                    (job for job in self._jobs.values() if job.state == "queued"),
                    key=lambda item: (item.created_at, item.job_id),
                )
            )

    def claim_job(self, queue: str, worker_id: str, lease_seconds: int) -> JobRecord | None:
        del queue, worker_id, lease_seconds
        raise RuntimeError("worker claims require PostgreSQL")

    def renew_job_lease(
        self, job_id: str, claim_generation: int, worker_id: str, lease_seconds: int
    ) -> bool:
        del job_id, claim_generation, worker_id, lease_seconds
        raise RuntimeError("worker leases require PostgreSQL")

    def update_claimed_job(
        self,
        job_id: str,
        claim_generation: int,
        *,
        stage: str,
        progress: int,
        progress_detail: dict[str, Any],
    ) -> JobRecord:
        del job_id, claim_generation, stage, progress, progress_detail
        raise RuntimeError("worker progress requires PostgreSQL")

    def finish_claimed_job(
        self,
        job_id: str,
        claim_generation: int,
        *,
        state: str,
        result_ref: dict[str, Any] | None,
        error_reason: str | None,
    ) -> JobRecord:
        del job_id, claim_generation, state, result_ref, error_reason
        raise RuntimeError("worker completion requires PostgreSQL")

    def candidate_active_runs(self) -> tuple[CandidateActiveRunRecord, ...]:
        with self._lock:
            return tuple(
                sorted(
                    self._candidate_active_runs.values(),
                    key=lambda item: (item.strategy_id, item.strategy_version),
                )
            )

    def candidate_run(self, run_id: str) -> CandidateRunRecord | None:
        with self._lock:
            return self._candidate_runs.get(run_id)

    def candidate_runs(
        self, strategy_id: str, strategy_version: str, limit: int = 20
    ) -> tuple[CandidateRunRecord, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with self._lock:
            runs = (
                run
                for run in self._candidate_runs.values()
                if any(
                    item.get("candidate_id") == strategy_id
                    and item.get("candidate_version") == strategy_version
                    for item in run.candidates
                )
            )
            return tuple(
                sorted(runs, key=lambda item: (item.created_at, item.run_id), reverse=True)[
                    :limit
                ]
            )

    def publish_candidate_run(
        self,
        job_id: str,
        claim_generation: int,
        *,
        run_id: str,
        artifact_ref: str,
        result_sha256: str,
        candidates: tuple[dict[str, Any], ...],
        expected_active_runs: dict[str, str | None],
    ) -> tuple[CandidateRunRecord, JobRecord]:
        del (
            job_id,
            claim_generation,
            run_id,
            artifact_ref,
            result_sha256,
            candidates,
            expected_active_runs,
        )
        raise RuntimeError("candidate Run publication requires PostgreSQL")

    def expert_evaluation_run(self, run_id: str) -> ExpertEvaluationRunRecord | None:
        with self._lock:
            return self._expert_evaluation_runs.get(run_id)

    def expert_evaluation_run_for_input(
        self, input_digest: str
    ) -> ExpertEvaluationRunRecord | None:
        with self._lock:
            runs = (
                item
                for item in self._expert_evaluation_runs.values()
                if item.input_digest == input_digest
            )
            return next(
                iter(sorted(runs, key=lambda item: (item.created_at, item.run_id), reverse=True)),
                None,
            )

    def publish_expert_evaluation_run(
        self,
        job_id: str,
        claim_generation: int,
        *,
        run_id: str,
        artifact_ref: str,
        manifest_digest: str,
        evaluation_identities: tuple[dict[str, Any], ...],
        strategy_identity: dict[str, Any] | None,
        summary: dict[str, Any],
    ) -> tuple[ExpertEvaluationRunRecord, JobRecord]:
        del (
            job_id,
            claim_generation,
            run_id,
            artifact_ref,
            manifest_digest,
            evaluation_identities,
            strategy_identity,
            summary,
        )
        raise RuntimeError("expert-evaluation Run publication requires PostgreSQL")

    def append_candidate_review(self, review: CandidateReviewRecord) -> None:
        with self._lock:
            if any(item.review_id == review.review_id for item in self._candidate_reviews):
                raise DuplicateRecordError("candidate review id already exists")
            self._candidate_reviews.append(review)

    def candidate_reviews(
        self, strategy_id: str, strategy_version: str
    ) -> tuple[CandidateReviewRecord, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (
                        item
                        for item in self._candidate_reviews
                        if item.strategy_id == strategy_id
                        and item.strategy_version == strategy_version
                    ),
                    key=lambda item: (item.created_at, item.review_id),
                )
            )

    def append_candidate_approval_event(self, event: CandidateApprovalEventRecord) -> None:
        with self._lock:
            if any(item.event_id == event.event_id for item in self._candidate_approval_events):
                raise DuplicateRecordError("candidate approval event id already exists")
            self._candidate_approval_events.append(event)

    def candidate_approval_events(
        self, strategy_id: str, strategy_version: str
    ) -> tuple[CandidateApprovalEventRecord, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (
                        item
                        for item in self._candidate_approval_events
                        if item.strategy_id == strategy_id
                        and item.strategy_version == strategy_version
                    ),
                    key=lambda item: (item.created_at, item.event_id),
                )
            )

    @property
    def security_audits(self) -> tuple[SecurityAuditRecord, ...]:
        with self._lock:
            return tuple(self._audits)


class PostgresPlatformStore:
    """Small DB-API adapter; psycopg is imported only for the platform extra."""

    def __init__(self, dsn: str, connect: Callable[..., Any] | None = None) -> None:
        self._dsn = dsn
        self._connect_override = connect

    def _connect(self) -> Any:
        if self._connect_override is not None:
            return self._connect_override(self._dsn)
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("install Quanthus with the platform dependency group") from exc
        return psycopg.connect(self._dsn, row_factory=dict_row)

    def ready(self) -> bool:
        try:
            with self._connect() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT 1 AS ready")
                row = cursor.fetchone()
                return row is not None
        except Exception:
            return False

    def migrate(self, modules: tuple[str, ...]) -> tuple[str, ...]:
        applied: list[str] = []
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(MIGRATION_LEDGER_SQL)
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext('quanthus-platform-migrations'))")
            for migration in migrations_for(modules):
                cursor.execute(
                    """
                    SELECT 1 FROM platform_schema_migrations
                    WHERE module_id = %s AND migration_id = %s
                    """,
                    (migration.module_id, migration.migration_id),
                )
                if cursor.fetchone() is not None:
                    continue
                for statement in migration.statements:
                    cursor.execute(statement)
                cursor.execute(
                    """
                    INSERT INTO platform_schema_migrations
                        (module_id, migration_id, applied_at)
                    VALUES (%s, %s, CURRENT_TIMESTAMP)
                    """,
                    (migration.module_id, migration.migration_id),
                )
                applied.append(migration.migration_id)
        return tuple(applied)

    @staticmethod
    def _user(row: dict[str, Any] | None) -> UserRecord | None:
        if row is None:
            return None
        return UserRecord(
            user_id=row["user_id"],
            username=row["username"],
            password_hash=row["password_hash"],
            role=row["role"],
            active=row["active"],
            mfa_secret_ciphertext=row["mfa_secret_ciphertext"],
            mfa_enabled=row["mfa_enabled"],
            created_at=row["created_at"],
        )

    def create_user(self, user: UserRecord) -> None:
        try:
            with self._connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO platform_users
                        (user_id, username, username_key, password_hash, role, active,
                         mfa_secret_ciphertext, mfa_enabled, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        user.user_id,
                        user.username,
                        user.username.casefold(),
                        user.password_hash,
                        user.role,
                        user.active,
                        user.mfa_secret_ciphertext,
                        user.mfa_enabled,
                        user.created_at,
                    ),
                )
        except Exception as exc:
            if getattr(exc, "sqlstate", None) == "23505":
                raise DuplicateRecordError("username or user id already exists") from exc
            raise

    def get_user_by_username(self, username_key: str) -> UserRecord | None:
        return self._get_user("username_key", username_key)

    def get_user(self, user_id: str) -> UserRecord | None:
        return self._get_user("user_id", user_id)

    def _get_user(self, column: str, value: str) -> UserRecord | None:
        if column not in {"username_key", "user_id"}:
            raise ValueError("invalid user lookup column")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(f"SELECT * FROM platform_users WHERE {column} = %s", (value,))
            return self._user(cursor.fetchone())

    def list_users(self) -> tuple[UserRecord, ...]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM platform_users ORDER BY username_key")
            return tuple(self._user(row) for row in cursor.fetchall())  # type: ignore[misc]

    def set_user_mfa(self, user_id: str, ciphertext: str | None, enabled: bool) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE platform_users
                SET mfa_secret_ciphertext = %s, mfa_enabled = %s
                WHERE user_id = %s
                """,
                (ciphertext, enabled, user_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(user_id)

    def create_session(self, session: SessionRecord) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO platform_sessions
                    (session_id, user_id, token_hash, expires_at, created_at, revoked_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    session.session_id,
                    session.user_id,
                    session.token_hash,
                    session.expires_at,
                    session.created_at,
                    session.revoked_at,
                ),
            )

    def get_session(self, token_hash: str) -> SessionRecord | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM platform_sessions WHERE token_hash = %s", (token_hash,))
            row = cursor.fetchone()
        return SessionRecord(**row) if row else None

    def revoke_session(self, token_hash: str, revoked_at: datetime) -> bool:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE platform_sessions SET revoked_at = %s
                WHERE token_hash = %s AND revoked_at IS NULL
                """,
                (revoked_at, token_hash),
            )
            return bool(cursor.rowcount == 1)

    def append_security_audit(self, audit: SecurityAuditRecord) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO platform_security_audit
                    (audit_id, event_type, outcome, actor_user_id, subject_user_id,
                     request_id, details, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                """,
                (
                    audit.audit_id,
                    audit.event_type,
                    audit.outcome,
                    audit.actor_user_id,
                    audit.subject_user_id,
                    audit.request_id,
                    json.dumps(audit.details, ensure_ascii=True, sort_keys=True),
                    audit.created_at,
                ),
            )

    def failed_login_count(self, username_key: str, since: datetime) -> int:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*) AS failures FROM platform_security_audit
                WHERE event_type = 'login' AND outcome = 'denied'
                  AND created_at >= %s AND details->>'username_key' = %s
                """,
                (since, username_key),
            )
            row = cursor.fetchone()
            return int(row["failures"])

    @staticmethod
    def _job(row: dict[str, Any] | None) -> JobRecord | None:
        return JobRecord(**row) if row else None

    def create_job(self, job: JobRecord) -> tuple[JobRecord, bool]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO platform_jobs
                    (job_id, kind, actor_user_id, idempotency_key, input_ref, state,
                     progress, cancel_requested, attempt, result_ref, error_reason,
                     created_at, updated_at, started_at, completed_at, queue,
                     input_digest, concurrency_key, available_at, stage, progress_detail,
                     claimed_by, claim_generation, lease_expires_at, cancel_requested_at)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s::jsonb,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb,
                        %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                RETURNING *
                """,
                (
                    job.job_id,
                    job.kind,
                    job.actor_user_id,
                    job.idempotency_key,
                    json.dumps(job.input_ref, ensure_ascii=True, sort_keys=True),
                    job.state,
                    job.progress,
                    job.cancel_requested,
                    job.attempt,
                    json.dumps(job.result_ref, ensure_ascii=True, sort_keys=True)
                    if job.result_ref is not None
                    else None,
                    job.error_reason,
                    job.created_at,
                    job.updated_at,
                    job.started_at,
                    job.completed_at,
                    job.queue,
                    job.input_digest,
                    job.concurrency_key,
                    job.available_at or job.created_at,
                    job.stage,
                    json.dumps(job.progress_detail or {}, ensure_ascii=True, sort_keys=True),
                    job.claimed_by,
                    job.claim_generation,
                    job.lease_expires_at,
                    job.cancel_requested_at,
                ),
            )
            created = cursor.fetchone()
            if created is not None:
                return self._job(created), True  # type: ignore[return-value]
            cursor.execute(
                """
                SELECT * FROM platform_jobs
                WHERE actor_user_id = %s AND kind = %s AND idempotency_key = %s
                """,
                (job.actor_user_id, job.kind, job.idempotency_key),
            )
            existing = self._job(cursor.fetchone())
            if existing is None:
                if job.concurrency_key is not None:
                    cursor.execute(
                        """
                        SELECT * FROM platform_jobs
                        WHERE queue = %s AND concurrency_key = %s
                          AND state IN ('queued', 'running')
                        """,
                        (job.queue, job.concurrency_key),
                    )
                    if cursor.fetchone() is not None:
                        raise DuplicateRecordError("active_concurrency_key_exists")
                raise RuntimeError("idempotent Job disappeared")
            if existing.input_digest != job.input_digest:
                raise IdempotencyConflictError("idempotency_key_reused_with_different_input")
            return existing, False

    def get_job(self, job_id: str) -> JobRecord | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM platform_jobs WHERE job_id = %s", (job_id,))
            return self._job(cursor.fetchone())

    def list_jobs(self, kind: str | None = None, limit: int = 50) -> tuple[JobRecord, ...]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        with self._connect() as connection, connection.cursor() as cursor:
            if kind is None:
                cursor.execute(
                    """
                    SELECT * FROM platform_jobs
                    ORDER BY created_at DESC, job_id DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
            else:
                cursor.execute(
                    """
                    SELECT * FROM platform_jobs WHERE kind = %s
                    ORDER BY created_at DESC, job_id DESC
                    LIMIT %s
                    """,
                    (kind, limit),
                )
            return tuple(self._job(row) for row in cursor.fetchall())  # type: ignore[misc]

    def start_job(self, job_id: str, started_at: datetime) -> JobRecord:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE platform_jobs SET
                    state = CASE WHEN cancel_requested THEN 'canceled' ELSE 'running' END,
                    progress = CASE WHEN cancel_requested THEN progress ELSE 1 END,
                    attempt = CASE WHEN cancel_requested THEN attempt ELSE attempt + 1 END,
                    error_reason = NULL,
                    started_at = CASE WHEN cancel_requested THEN started_at ELSE %s END,
                    completed_at = CASE WHEN cancel_requested THEN %s ELSE NULL END,
                    updated_at = %s
                WHERE job_id = %s AND state = 'queued'
                RETURNING *
                """,
                (started_at, started_at, started_at, job_id),
            )
            observed = self._job(cursor.fetchone())
            if observed is not None:
                return observed
            cursor.execute("SELECT * FROM platform_jobs WHERE job_id = %s", (job_id,))
            current = self._job(cursor.fetchone())
            if current is None:
                raise KeyError(job_id)
            return current

    def update_job(self, job: JobRecord) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE platform_jobs SET
                    state = CASE
                        WHEN platform_jobs.state IN ('succeeded', 'failed', 'canceled')
                        THEN platform_jobs.state ELSE %s END,
                    progress = CASE
                        WHEN platform_jobs.state IN ('succeeded', 'failed', 'canceled')
                        THEN platform_jobs.progress ELSE %s END,
                    cancel_requested = platform_jobs.cancel_requested OR %s,
                    attempt = %s,
                    result_ref = %s::jsonb, error_reason = %s, updated_at = %s,
                    started_at = %s, completed_at = %s
                WHERE job_id = %s
                """,
                (
                    job.state,
                    job.progress,
                    job.cancel_requested,
                    job.attempt,
                    json.dumps(job.result_ref, ensure_ascii=True, sort_keys=True)
                    if job.result_ref is not None
                    else None,
                    job.error_reason,
                    job.updated_at,
                    job.started_at,
                    job.completed_at,
                    job.job_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(job.job_id)

    def finish_job(self, job: JobRecord) -> JobRecord:
        if job.state not in {"succeeded", "failed"}:
            raise ValueError("finish_job requires a succeeded or failed record")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE platform_jobs SET
                    state = CASE WHEN cancel_requested THEN 'canceled' ELSE %s END,
                    progress = CASE WHEN cancel_requested THEN progress ELSE %s END,
                    result_ref = CASE WHEN cancel_requested THEN NULL ELSE %s::jsonb END,
                    error_reason = CASE WHEN cancel_requested THEN NULL ELSE %s END,
                    updated_at = %s, completed_at = %s
                WHERE job_id = %s AND state = 'running'
                RETURNING *
                """,
                (
                    job.state,
                    job.progress,
                    json.dumps(job.result_ref, ensure_ascii=True, sort_keys=True)
                    if job.result_ref is not None
                    else None,
                    job.error_reason,
                    job.updated_at,
                    job.completed_at,
                    job.job_id,
                ),
            )
            terminal = self._job(cursor.fetchone())
            if terminal is not None:
                return terminal
            cursor.execute("SELECT * FROM platform_jobs WHERE job_id = %s", (job.job_id,))
            current = self._job(cursor.fetchone())
            if current is None:
                raise KeyError(job.job_id)
            if current.state not in {"succeeded", "failed", "canceled"}:
                raise RuntimeError("job terminal transition was rejected")
            return current

    def request_job_cancel(self, job_id: str, requested_at: datetime) -> tuple[JobRecord, bool]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE platform_jobs SET
                    state = CASE WHEN state = 'queued' THEN 'canceled' ELSE state END,
                    cancel_requested = TRUE,
                    cancel_requested_at = COALESCE(cancel_requested_at, %s),
                    updated_at = %s,
                    completed_at = CASE
                        WHEN state = 'queued' THEN %s ELSE completed_at END
                WHERE job_id = %s AND state IN ('queued', 'running')
                RETURNING *
                """,
                (requested_at, requested_at, requested_at, job_id),
            )
            row = cursor.fetchone()
            if row is not None:
                return self._job(row), True  # type: ignore[return-value]
            cursor.execute("SELECT * FROM platform_jobs WHERE job_id = %s", (job_id,))
            current = self._job(cursor.fetchone())
            if current is None:
                raise KeyError(job_id)
            return current, False

    def finish_job_canceled(self, job_id: str, completed_at: datetime) -> JobRecord:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE platform_jobs SET
                    state = 'canceled', result_ref = NULL, error_reason = NULL,
                    updated_at = %s, completed_at = %s
                WHERE job_id = %s AND state = 'running' AND cancel_requested
                RETURNING *
                """,
                (completed_at, completed_at, job_id),
            )
            terminal = self._job(cursor.fetchone())
            if terminal is not None:
                return terminal
            cursor.execute("SELECT * FROM platform_jobs WHERE job_id = %s", (job_id,))
            current = self._job(cursor.fetchone())
            if current is None:
                raise KeyError(job_id)
            if current.state not in {"succeeded", "failed", "canceled"}:
                raise RuntimeError("job cancellation was not requested")
            return current

    def resumable_jobs(self) -> tuple[JobRecord, ...]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE platform_jobs
                SET state = 'queued', progress = 0, claimed_by = NULL,
                    lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE state = 'running' AND lease_expires_at < CURRENT_TIMESTAMP
                """
            )
            cursor.execute(
                """
                SELECT * FROM platform_jobs WHERE state = 'queued'
                ORDER BY created_at, job_id
                """
            )
            return tuple(self._job(row) for row in cursor.fetchall())  # type: ignore[misc]

    def claim_job(self, queue: str, worker_id: str, lease_seconds: int) -> JobRecord | None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                WITH candidate AS (
                    SELECT job_id FROM platform_jobs
                    WHERE queue = %s
                      AND cancel_requested = FALSE
                      AND (
                        (state = 'queued' AND available_at <= CURRENT_TIMESTAMP)
                        OR (state = 'running' AND lease_expires_at < CURRENT_TIMESTAMP)
                      )
                    ORDER BY available_at, created_at, job_id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE platform_jobs AS job SET
                    state = 'running',
                    stage = 'starting',
                    progress = GREATEST(job.progress, 1),
                    claimed_by = %s,
                    claim_generation = job.claim_generation + 1,
                    lease_expires_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                    attempt = job.attempt + 1,
                    started_at = COALESCE(job.started_at, CURRENT_TIMESTAMP),
                    completed_at = NULL,
                    error_reason = NULL,
                    updated_at = CURRENT_TIMESTAMP
                FROM candidate
                WHERE job.job_id = candidate.job_id
                RETURNING job.*
                """,
                (queue, worker_id, lease_seconds),
            )
            return self._job(cursor.fetchone())

    def renew_job_lease(
        self, job_id: str, claim_generation: int, worker_id: str, lease_seconds: int
    ) -> bool:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE platform_jobs SET
                    lease_expires_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                    updated_at = CURRENT_TIMESTAMP
                WHERE job_id = %s AND claim_generation = %s
                  AND claimed_by = %s AND state = 'running'
                """,
                (lease_seconds, job_id, claim_generation, worker_id),
            )
            return bool(cursor.rowcount == 1)

    def update_claimed_job(
        self,
        job_id: str,
        claim_generation: int,
        *,
        stage: str,
        progress: int,
        progress_detail: dict[str, Any],
    ) -> JobRecord:
        if not 1 <= progress <= 99:
            raise ValueError("running progress must be between 1 and 99")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE platform_jobs SET
                    stage = %s,
                    progress = GREATEST(progress, %s),
                    progress_detail = %s::jsonb,
                    updated_at = CURRENT_TIMESTAMP
                WHERE job_id = %s AND claim_generation = %s AND state = 'running'
                RETURNING *
                """,
                (
                    stage,
                    progress,
                    json.dumps(progress_detail, ensure_ascii=True, sort_keys=True),
                    job_id,
                    claim_generation,
                ),
            )
            observed = self._job(cursor.fetchone())
            if observed is None:
                raise RuntimeError("stale_claim_generation")
            return observed

    def finish_claimed_job(
        self,
        job_id: str,
        claim_generation: int,
        *,
        state: str,
        result_ref: dict[str, Any] | None,
        error_reason: str | None,
    ) -> JobRecord:
        if state not in {"succeeded", "failed", "canceled"}:
            raise ValueError("invalid terminal state")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE platform_jobs SET
                    state = CASE WHEN cancel_requested THEN 'canceled' ELSE %s END,
                    stage = CASE WHEN cancel_requested THEN 'canceled' ELSE %s END,
                    progress = CASE WHEN cancel_requested OR %s <> 'succeeded'
                                    THEN progress ELSE 100 END,
                    result_ref = CASE WHEN cancel_requested OR %s <> 'succeeded'
                                      THEN NULL ELSE %s::jsonb END,
                    error_reason = CASE WHEN cancel_requested THEN NULL ELSE %s END,
                    lease_expires_at = NULL,
                    updated_at = CURRENT_TIMESTAMP,
                    completed_at = CURRENT_TIMESTAMP
                WHERE job_id = %s AND claim_generation = %s AND state = 'running'
                RETURNING *
                """,
                (
                    state,
                    state,
                    state,
                    state,
                    json.dumps(result_ref, ensure_ascii=True, sort_keys=True)
                    if result_ref is not None
                    else None,
                    error_reason,
                    job_id,
                    claim_generation,
                ),
            )
            observed = self._job(cursor.fetchone())
            if observed is None:
                raise RuntimeError("stale_claim_generation")
            return observed

    @staticmethod
    def _candidate_run(row: dict[str, Any]) -> CandidateRunRecord:
        return CandidateRunRecord(
            run_id=row["run_id"],
            job_id=row["job_id"],
            claim_generation=row["claim_generation"],
            input_digest=row["input_digest"],
            artifact_ref=row["artifact_ref"],
            result_sha256=row["result_sha256"],
            candidates=tuple(row["candidates"]),
            active_switch_applied=row["active_switch_applied"],
            created_at=row["created_at"],
        )

    def candidate_active_runs(self) -> tuple[CandidateActiveRunRecord, ...]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM platform_candidate_active_runs
                ORDER BY strategy_id, strategy_version
                """
            )
            return tuple(CandidateActiveRunRecord(**row) for row in cursor.fetchall())

    def candidate_run(self, run_id: str) -> CandidateRunRecord | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM platform_candidate_runs WHERE run_id = %s",
                (run_id,),
            )
            row = cursor.fetchone()
            return self._candidate_run(row) if row else None

    def candidate_runs(
        self, strategy_id: str, strategy_version: str, limit: int = 20
    ) -> tuple[CandidateRunRecord, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT run.* FROM platform_candidate_runs run
                WHERE EXISTS (
                    SELECT 1 FROM jsonb_array_elements(run.candidates) candidate
                    WHERE candidate->>'candidate_id' = %s
                      AND candidate->>'candidate_version' = %s
                )
                ORDER BY run.created_at DESC, run.run_id DESC
                LIMIT %s
                """,
                (strategy_id, strategy_version, limit),
            )
            return tuple(self._candidate_run(row) for row in cursor.fetchall())

    def publish_candidate_run(
        self,
        job_id: str,
        claim_generation: int,
        *,
        run_id: str,
        artifact_ref: str,
        result_sha256: str,
        candidates: tuple[dict[str, Any], ...],
        expected_active_runs: dict[str, str | None],
    ) -> tuple[CandidateRunRecord, JobRecord]:
        identities = {
            f"{item['candidate_id']}@{item['candidate_version']}": item
            for item in candidates
        }
        if not candidates or len(identities) != len(candidates):
            raise ValueError("candidate Run identities must be non-empty and unique")
        if set(expected_active_runs) != set(identities):
            raise ValueError("expected active Run identities do not match the result")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM platform_jobs
                WHERE job_id = %s AND claim_generation = %s AND state = 'running'
                FOR UPDATE
                """,
                (job_id, claim_generation),
            )
            job = self._job(cursor.fetchone())
            if job is None or job.cancel_requested:
                raise RuntimeError("stale_claim_generation")
            cursor.execute(
                """
                SELECT * FROM platform_candidate_active_runs
                WHERE (strategy_id, strategy_version) IN (
                    SELECT value->>'candidate_id', value->>'candidate_version'
                    FROM jsonb_array_elements(%s::jsonb) value
                )
                FOR UPDATE
                """,
                (json.dumps(candidates, ensure_ascii=True, sort_keys=True),),
            )
            current = {
                f"{row['strategy_id']}@{row['strategy_version']}": row["run_id"]
                for row in cursor.fetchall()
            }
            switch = all(
                current.get(identity) == expected
                for identity, expected in expected_active_runs.items()
            )
            cursor.execute(
                """
                INSERT INTO platform_candidate_runs
                    (run_id, job_id, claim_generation, input_digest, artifact_ref,
                     result_sha256, candidates, active_switch_applied)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                RETURNING *
                """,
                (
                    run_id,
                    job_id,
                    claim_generation,
                    job.input_digest,
                    artifact_ref,
                    result_sha256,
                    json.dumps(candidates, ensure_ascii=True, sort_keys=True),
                    switch,
                ),
            )
            run = self._candidate_run(cursor.fetchone())
            if switch:
                for item in candidates:
                    cursor.execute(
                        """
                        INSERT INTO platform_candidate_active_runs
                            (strategy_id, strategy_version, run_id, evidence_digest)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (strategy_id, strategy_version) DO UPDATE SET
                            run_id = EXCLUDED.run_id,
                            evidence_digest = EXCLUDED.evidence_digest,
                            updated_at = CURRENT_TIMESTAMP
                        """,
                        (
                            item["candidate_id"],
                            item["candidate_version"],
                            run_id,
                            item["artifact_sha256"],
                        ),
                    )
            result_ref = {
                "run_id": run_id,
                "artifact_ref": artifact_ref,
                "result_sha256": result_sha256,
                "candidate_count": len(candidates),
                "active_switch_applied": switch,
            }
            cursor.execute(
                """
                UPDATE platform_jobs SET
                    state = 'succeeded', stage = 'succeeded', progress = 100,
                    result_ref = %s::jsonb, error_reason = NULL,
                    lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP,
                    completed_at = CURRENT_TIMESTAMP
                WHERE job_id = %s AND claim_generation = %s AND state = 'running'
                  AND NOT cancel_requested
                RETURNING *
                """,
                (
                    json.dumps(result_ref, ensure_ascii=True, sort_keys=True),
                    job_id,
                    claim_generation,
                ),
            )
            terminal = self._job(cursor.fetchone())
            if terminal is None:
                raise RuntimeError("stale_claim_generation")
            return run, terminal

    @staticmethod
    def _expert_evaluation_run(row: dict[str, Any]) -> ExpertEvaluationRunRecord:
        return ExpertEvaluationRunRecord(
            run_id=row["run_id"],
            job_id=row["job_id"],
            claim_generation=row["claim_generation"],
            input_digest=row["input_digest"],
            artifact_ref=row["artifact_ref"],
            manifest_digest=row["manifest_digest"],
            evaluation_identities=tuple(row["evaluation_identities"]),
            strategy_identity=row["strategy_identity"],
            summary=row["summary"],
            created_at=row["created_at"],
        )

    def expert_evaluation_run(self, run_id: str) -> ExpertEvaluationRunRecord | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM platform_expert_evaluation_runs WHERE run_id = %s",
                (run_id,),
            )
            row = cursor.fetchone()
            return self._expert_evaluation_run(row) if row else None

    def expert_evaluation_run_for_input(
        self, input_digest: str
    ) -> ExpertEvaluationRunRecord | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM platform_expert_evaluation_runs
                WHERE input_digest = %s
                ORDER BY created_at DESC, run_id DESC
                LIMIT 1
                """,
                (input_digest,),
            )
            row = cursor.fetchone()
            return self._expert_evaluation_run(row) if row else None

    def publish_expert_evaluation_run(
        self,
        job_id: str,
        claim_generation: int,
        *,
        run_id: str,
        artifact_ref: str,
        manifest_digest: str,
        evaluation_identities: tuple[dict[str, Any], ...],
        strategy_identity: dict[str, Any] | None,
        summary: dict[str, Any],
    ) -> tuple[ExpertEvaluationRunRecord, JobRecord]:
        identities = {
            (item.get("assessment_id"), item.get("assessment_version"), item.get("digest"))
            for item in evaluation_identities
        }
        if not evaluation_identities or len(identities) != len(evaluation_identities):
            raise ValueError("expert-evaluation identities must be non-empty and unique")
        encoded_identities = json.dumps(
            evaluation_identities, ensure_ascii=True, sort_keys=True
        )
        encoded_strategy = (
            json.dumps(strategy_identity, ensure_ascii=True, sort_keys=True)
            if strategy_identity is not None
            else None
        )
        encoded_summary = json.dumps(summary, ensure_ascii=True, sort_keys=True)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM platform_jobs
                WHERE job_id = %s AND claim_generation = %s AND state = 'running'
                FOR UPDATE
                """,
                (job_id, claim_generation),
            )
            job = self._job(cursor.fetchone())
            if job is None or job.cancel_requested:
                raise RuntimeError("stale_claim_generation")
            cursor.execute(
                """
                INSERT INTO platform_expert_evaluation_runs
                    (run_id, job_id, claim_generation, input_digest, artifact_ref,
                     manifest_digest, evaluation_identities, strategy_identity, summary)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb)
                ON CONFLICT DO NOTHING
                RETURNING *
                """,
                (
                    run_id,
                    job_id,
                    claim_generation,
                    job.input_digest,
                    artifact_ref,
                    manifest_digest,
                    encoded_identities,
                    encoded_strategy,
                    encoded_summary,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                cursor.execute(
                    "SELECT * FROM platform_expert_evaluation_runs WHERE run_id = %s",
                    (run_id,),
                )
                row = cursor.fetchone()
            if row is None:
                raise RuntimeError("expert_run_registration_conflict")
            run = self._expert_evaluation_run(row)
            if (
                run.input_digest != job.input_digest
                or run.artifact_ref != artifact_ref
                or run.manifest_digest != manifest_digest
                or run.evaluation_identities != evaluation_identities
                or run.strategy_identity != strategy_identity
                or run.summary != summary
            ):
                raise RuntimeError("expert_run_registration_conflict")
            result_ref = {
                "run_id": run_id,
                "artifact_ref": artifact_ref,
                "manifest_digest": manifest_digest,
                "evaluation_count": len(evaluation_identities),
            }
            cursor.execute(
                """
                UPDATE platform_jobs SET
                    state = 'succeeded', stage = 'succeeded', progress = 100,
                    result_ref = %s::jsonb, error_reason = NULL,
                    lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP,
                    completed_at = CURRENT_TIMESTAMP
                WHERE job_id = %s AND claim_generation = %s AND state = 'running'
                  AND NOT cancel_requested
                RETURNING *
                """,
                (
                    json.dumps(result_ref, ensure_ascii=True, sort_keys=True),
                    job_id,
                    claim_generation,
                ),
            )
            terminal = self._job(cursor.fetchone())
            if terminal is None:
                raise RuntimeError("stale_claim_generation")
            return run, terminal

    @staticmethod
    def _candidate_review(row: dict[str, Any]) -> CandidateReviewRecord:
        return CandidateReviewRecord(**row)

    def append_candidate_review(self, review: CandidateReviewRecord) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO platform_candidate_reviews
                    (review_id, strategy_id, strategy_version, action, state, note,
                     actor_user_id, actor_username, request_id, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    review.review_id,
                    review.strategy_id,
                    review.strategy_version,
                    review.action,
                    review.state,
                    review.note,
                    review.actor_user_id,
                    review.actor_username,
                    review.request_id,
                    review.created_at,
                ),
            )

    def candidate_reviews(
        self, strategy_id: str, strategy_version: str
    ) -> tuple[CandidateReviewRecord, ...]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM platform_candidate_reviews
                WHERE strategy_id = %s AND strategy_version = %s
                ORDER BY created_at, review_id
                """,
                (strategy_id, strategy_version),
            )
            return tuple(self._candidate_review(row) for row in cursor.fetchall())

    @staticmethod
    def _candidate_approval_event(row: dict[str, Any]) -> CandidateApprovalEventRecord:
        return CandidateApprovalEventRecord(**row)

    def append_candidate_approval_event(self, event: CandidateApprovalEventRecord) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO platform_candidate_approval_events
                    (event_id, strategy_id, strategy_version, evidence_digest, axis,
                     action, state, note, package_id, actor_user_id, actor_username,
                     request_id, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    event.event_id,
                    event.strategy_id,
                    event.strategy_version,
                    event.evidence_digest,
                    event.axis,
                    event.action,
                    event.state,
                    event.note,
                    event.package_id,
                    event.actor_user_id,
                    event.actor_username,
                    event.request_id,
                    event.created_at,
                ),
            )

    def candidate_approval_events(
        self, strategy_id: str, strategy_version: str
    ) -> tuple[CandidateApprovalEventRecord, ...]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM platform_candidate_approval_events
                WHERE strategy_id = %s AND strategy_version = %s
                ORDER BY created_at, event_id
                """,
                (strategy_id, strategy_version),
            )
            return tuple(self._candidate_approval_event(row) for row in cursor.fetchall())

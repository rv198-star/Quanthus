"""Module-scoped PostgreSQL migration registry."""

from __future__ import annotations

from dataclasses import dataclass

MIGRATION_LEDGER_SQL = """
CREATE TABLE IF NOT EXISTS platform_schema_migrations (
    module_id TEXT NOT NULL,
    migration_id TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (module_id, migration_id)
)
""".strip()


@dataclass(frozen=True)
class Migration:
    module_id: str
    migration_id: str
    statements: tuple[str, ...]


MIGRATIONS = (
    Migration(
        module_id="identity",
        migration_id="identity-0001",
        statements=(
            """
            CREATE TABLE platform_users (
                user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                username_key TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('admin', 'operator', 'viewer')),
                active BOOLEAN NOT NULL DEFAULT TRUE,
                mfa_secret_ciphertext TEXT,
                mfa_enabled BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL
            )
            """.strip(),
            """
            CREATE TABLE platform_sessions (
                session_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES platform_users(user_id),
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                revoked_at TIMESTAMPTZ
            )
            """.strip(),
            """
            CREATE INDEX platform_sessions_user_idx
            ON platform_sessions (user_id, expires_at)
            """.strip(),
            """
            CREATE TABLE platform_security_audit (
                audit_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                outcome TEXT NOT NULL,
                actor_user_id TEXT,
                subject_user_id TEXT,
                request_id TEXT,
                details JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            )
            """.strip(),
            """
            CREATE INDEX platform_security_audit_time_idx
            ON platform_security_audit (created_at, audit_id)
            """.strip(),
        ),
    ),
    Migration(
        module_id="jobs",
        migration_id="jobs-0001",
        statements=(
            """
            CREATE TABLE platform_jobs (
                job_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                actor_user_id TEXT NOT NULL REFERENCES platform_users(user_id),
                idempotency_key TEXT NOT NULL,
                input_ref JSONB NOT NULL,
                state TEXT NOT NULL CHECK (
                    state IN ('pending', 'running', 'succeeded', 'failed', 'canceled')
                ),
                progress INTEGER NOT NULL CHECK (progress BETWEEN 0 AND 100),
                cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
                attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
                result_ref JSONB,
                error_reason TEXT,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                started_at TIMESTAMPTZ,
                completed_at TIMESTAMPTZ,
                UNIQUE (actor_user_id, kind, idempotency_key)
            )
            """.strip(),
            """
            CREATE INDEX platform_jobs_state_idx
            ON platform_jobs (state, updated_at, job_id)
            """.strip(),
        ),
    ),
    Migration(
        module_id="jobs",
        migration_id="jobs-0002-lean-worker",
        statements=(
            "ALTER TABLE platform_jobs DROP CONSTRAINT IF EXISTS platform_jobs_state_check",
            "UPDATE platform_jobs SET state = 'queued' WHERE state = 'pending'",
            """
            ALTER TABLE platform_jobs ADD CONSTRAINT platform_jobs_state_check
            CHECK (state IN ('queued', 'running', 'succeeded', 'failed', 'canceled'))
            """.strip(),
            "ALTER TABLE platform_jobs ADD COLUMN queue TEXT NOT NULL DEFAULT 'local'",
            "ALTER TABLE platform_jobs ADD COLUMN input_digest TEXT",
            """
            UPDATE platform_jobs SET input_digest = 'legacy:' || job_id
            WHERE input_digest IS NULL
            """.strip(),
            "ALTER TABLE platform_jobs ALTER COLUMN input_digest SET NOT NULL",
            "ALTER TABLE platform_jobs ADD COLUMN concurrency_key TEXT",
            """
            ALTER TABLE platform_jobs ADD COLUMN available_at TIMESTAMPTZ
            NOT NULL DEFAULT CURRENT_TIMESTAMP
            """.strip(),
            "ALTER TABLE platform_jobs ADD COLUMN stage TEXT NOT NULL DEFAULT 'queued'",
            """
            ALTER TABLE platform_jobs ADD COLUMN progress_detail JSONB
            NOT NULL DEFAULT '{}'::jsonb
            """.strip(),
            "ALTER TABLE platform_jobs ADD COLUMN claimed_by TEXT",
            "ALTER TABLE platform_jobs ADD COLUMN claim_generation BIGINT NOT NULL DEFAULT 0",
            "ALTER TABLE platform_jobs ADD COLUMN lease_expires_at TIMESTAMPTZ",
            "ALTER TABLE platform_jobs ADD COLUMN cancel_requested_at TIMESTAMPTZ",
            """
            CREATE INDEX platform_jobs_claim_idx
            ON platform_jobs (queue, available_at, created_at, job_id)
            WHERE state = 'queued'
            """.strip(),
            """
            CREATE UNIQUE INDEX platform_jobs_active_concurrency_idx
            ON platform_jobs (queue, concurrency_key)
            WHERE concurrency_key IS NOT NULL AND state IN ('queued', 'running')
            """.strip(),
        ),
    ),
    Migration(
        module_id="jobs",
        migration_id="jobs-0003-candidate-runs",
        statements=(
            """
            CREATE TABLE platform_candidate_runs (
                run_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL UNIQUE REFERENCES platform_jobs(job_id),
                claim_generation BIGINT NOT NULL,
                input_digest TEXT NOT NULL,
                artifact_ref TEXT NOT NULL,
                result_sha256 TEXT NOT NULL,
                candidates JSONB NOT NULL,
                active_switch_applied BOOLEAN NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """.strip(),
            """
            CREATE TABLE platform_candidate_active_runs (
                strategy_id TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                run_id TEXT NOT NULL REFERENCES platform_candidate_runs(run_id),
                evidence_digest TEXT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (strategy_id, strategy_version)
            )
            """.strip(),
            """
            CREATE INDEX platform_candidate_active_run_idx
            ON platform_candidate_active_runs (run_id)
            """.strip(),
        ),
    ),
    Migration(
        module_id="candidate-review",
        migration_id="candidate-review-0001",
        statements=(
            """
            CREATE TABLE platform_candidate_reviews (
                review_id TEXT PRIMARY KEY,
                strategy_id TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                action TEXT NOT NULL CHECK (
                    action IN ('start-review', 'request-changes', 'submit-governance-review')
                ),
                state TEXT NOT NULL CHECK (
                    state IN ('reviewing', 'changes-requested',
                              'governance-review-requested')
                ),
                note TEXT NOT NULL,
                actor_user_id TEXT NOT NULL REFERENCES platform_users(user_id),
                actor_username TEXT NOT NULL,
                request_id TEXT,
                created_at TIMESTAMPTZ NOT NULL
            )
            """.strip(),
            """
            CREATE INDEX platform_candidate_reviews_strategy_idx
            ON platform_candidate_reviews
                (strategy_id, strategy_version, created_at DESC, review_id DESC)
            """.strip(),
        ),
    ),
    Migration(
        module_id="candidate-approval",
        migration_id="candidate-approval-0001",
        statements=(
            """
            CREATE TABLE platform_candidate_approval_events (
                event_id TEXT PRIMARY KEY,
                strategy_id TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                evidence_digest TEXT NOT NULL,
                axis TEXT NOT NULL CHECK (axis IN ('evidence-review', 'governance')),
                action TEXT NOT NULL CHECK (action IN (
                    'claim-review', 'request-changes', 'approve-review', 'reject-review',
                    'submit-governance', 'return-governance', 'resubmit-governance',
                    'withdraw-governance', 'reject-governance', 'admit-governance'
                )),
                state TEXT NOT NULL CHECK (state IN (
                    'reviewing', 'changes-requested', 'review-passed', 'review-failed',
                    'governance-under-review', 'governance-returned',
                    'governance-withdrawn', 'governance-rejected', 'governance-admitted'
                )),
                note TEXT NOT NULL,
                package_id TEXT,
                actor_user_id TEXT NOT NULL REFERENCES platform_users(user_id),
                actor_username TEXT NOT NULL,
                request_id TEXT,
                created_at TIMESTAMPTZ NOT NULL
            )
            """.strip(),
            """
            CREATE INDEX platform_candidate_approval_identity_idx
            ON platform_candidate_approval_events
                (strategy_id, strategy_version, created_at, event_id)
            """.strip(),
        ),
    ),
)


def migrations_for(modules: tuple[str, ...]) -> tuple[Migration, ...]:
    known = {migration.module_id for migration in MIGRATIONS}
    unknown = set(modules).difference(known)
    if unknown:
        raise ValueError(f"unknown platform migration modules: {sorted(unknown)}")
    return tuple(migration for migration in MIGRATIONS if migration.module_id in modules)


def expected_tables(modules: tuple[str, ...]) -> tuple[str, ...]:
    tables = ["platform_schema_migrations"]
    if "identity" in modules:
        tables.extend(("platform_users", "platform_sessions", "platform_security_audit"))
    if "jobs" in modules:
        tables.extend(
            (
                "platform_jobs",
                "platform_candidate_runs",
                "platform_candidate_active_runs",
            )
        )
    if "candidate-review" in modules:
        tables.append("platform_candidate_reviews")
    if "candidate-approval" in modules:
        tables.append("platform_candidate_approval_events")
    return tuple(tables)

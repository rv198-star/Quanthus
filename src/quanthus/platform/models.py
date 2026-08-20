"""Platform-owned records; no financial or strategy truth belongs here."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

PLATFORM_ROLES = ("admin", "operator", "viewer")
JOB_STATES = ("queued", "running", "succeeded", "failed", "canceled")


@dataclass(frozen=True)
class UserRecord:
    user_id: str
    username: str
    password_hash: str
    role: str
    active: bool
    mfa_secret_ciphertext: str | None
    mfa_enabled: bool
    created_at: datetime


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    user_id: str
    token_hash: str
    expires_at: datetime
    created_at: datetime
    revoked_at: datetime | None = None


@dataclass(frozen=True)
class SecurityAuditRecord:
    audit_id: str
    event_type: str
    outcome: str
    actor_user_id: str | None
    subject_user_id: str | None
    request_id: str | None
    details: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class CandidateReviewRecord:
    review_id: str
    strategy_id: str
    strategy_version: str
    action: str
    state: str
    note: str
    actor_user_id: str
    actor_username: str
    request_id: str | None
    created_at: datetime


@dataclass(frozen=True)
class CandidateApprovalEventRecord:
    event_id: str
    strategy_id: str
    strategy_version: str
    evidence_digest: str
    axis: str
    action: str
    state: str
    note: str
    package_id: str | None
    actor_user_id: str
    actor_username: str
    request_id: str | None
    created_at: datetime


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    kind: str
    actor_user_id: str
    idempotency_key: str
    input_ref: dict[str, Any]
    state: str
    progress: int
    cancel_requested: bool
    attempt: int
    result_ref: dict[str, Any] | None
    error_reason: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    queue: str = "local"
    input_digest: str = ""
    concurrency_key: str | None = None
    available_at: datetime | None = None
    stage: str = "queued"
    progress_detail: dict[str, Any] | None = None
    claimed_by: str | None = None
    claim_generation: int = 0
    lease_expires_at: datetime | None = None
    cancel_requested_at: datetime | None = None


@dataclass(frozen=True)
class CandidateRunRecord:
    run_id: str
    job_id: str
    claim_generation: int
    input_digest: str
    artifact_ref: str
    result_sha256: str
    candidates: tuple[dict[str, Any], ...]
    active_switch_applied: bool
    created_at: datetime


@dataclass(frozen=True)
class CandidateActiveRunRecord:
    strategy_id: str
    strategy_version: str
    run_id: str
    evidence_digest: str
    updated_at: datetime


@dataclass(frozen=True)
class ExpertEvaluationRunRecord:
    run_id: str
    job_id: str
    claim_generation: int
    input_digest: str
    artifact_ref: str
    manifest_digest: str
    evaluation_identities: tuple[dict[str, Any], ...]
    strategy_identity: dict[str, Any] | None
    summary: dict[str, Any]
    created_at: datetime

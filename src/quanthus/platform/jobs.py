"""Finite local JobRunner with durable envelopes and artifact-only results."""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from quanthus.platform.models import JobRecord
from quanthus.platform.persistence import PlatformStore
from quanthus.platform.ports import AttributionVerificationPort

ATTRIBUTION_VERIFICATION_JOB = "quanthus.attribution.verify.v1"
CANDIDATE_REPLAY_JOB = "quanthus.candidate-replay.v1"
EXPERT_EVALUATION_JOB = "quanthus.expert-evaluation.v1"
P4_EVENT_SCAN_JOB = "quanthus.p4-event-scan.v1"
EVENT_RUN_ROOT = Path("/data/event-runs")
EVENT_SUMMARY_MAX_BYTES = 1024 * 1024
EVENT_FAILURE_LOG_MAX_BYTES = 64 * 1024
EVENT_FAILURE_AUDIT_MAX_BYTES = 256 * 1024
P4_INDUSTRY_MAPPING_PATH = Path(
    "/srv/quanthus/research-bundles/development/strategy-replay-development-1f1ea4d-v1/"
    "tables/static_industry_mapping.json"
)
P4_INDUSTRY_MAPPING_MAX_BYTES = 8 * 1024 * 1024


class JobNotCancelableError(RuntimeError):
    pass


class JobHandler(Protocol):
    def __call__(
        self,
        input_ref: Mapping[str, Any],
        progress: Callable[[int], None],
        canceled: Callable[[], bool],
    ) -> dict[str, Any]: ...


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def input_digest(input_ref: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        input_ref, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def expert_evaluation_submission(
    input_ref: Mapping[str, Any],
) -> tuple[dict[str, Any], str, str]:
    """Validate the exact domain input and derive both queue keys from its content."""
    from quanthus.expert_evaluation.runner import ExpertEvaluationJobInput

    parsed = ExpertEvaluationJobInput.from_dict(input_ref)
    canonical = parsed.to_dict()
    if input_digest(canonical) != parsed.input_digest:
        raise RuntimeError("expert evaluation digest implementations differ")
    concurrency_key = f"expert-evaluation:{parsed.input_digest[7:]}"
    return canonical, parsed.idempotency_key, concurrency_key


def _industry_mapping_identity_summary() -> dict[str, Any]:
    path = P4_INDUSTRY_MAPPING_PATH
    try:
        if not path.is_file():
            return {"status": "mapping_missing"}
        if path.stat().st_size > P4_INDUSTRY_MAPPING_MAX_BYTES:
            return {"status": "mapping_too_large"}
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return {"status": "mapping_unreadable"}
    except json.JSONDecodeError:
        return {"status": "mapping_invalid_json"}
    if not isinstance(raw, dict) or not isinstance(raw.get("memberships"), list):
        return {"status": "mapping_schema_invalid"}
    memberships = raw["memberships"]
    if len(memberships) > 10000:
        return {"status": "mapping_membership_limit_exceeded"}
    counts = {
        "dwd_governed_security_id": 0,
        "exchange_prefixed_security_id": 0,
        "plain_six_digit_security_id": 0,
        "other_security_id": 0,
    }
    for item in memberships:
        if not isinstance(item, dict) or not isinstance(item.get("security_id"), str):
            return {"status": "mapping_membership_invalid"}
        security_id = item["security_id"]
        if security_id.startswith("security_"):
            counts["dwd_governed_security_id"] += 1
        elif security_id.startswith(("SSE-", "SZSE-", "BSE-")):
            counts["exchange_prefixed_security_id"] += 1
        elif len(security_id) == 6 and security_id.isdigit():
            counts["plain_six_digit_security_id"] += 1
        else:
            counts["other_security_id"] += 1
    return {
        "status": "available",
        "mapping_version": raw.get("mapping_version"),
        "membership_count": len(memberships),
        "security_id_namespace_counts": counts,
    }


def _event_scan_failure(job: JobRecord) -> dict[str, Any] | None:
    if job.kind != P4_EVENT_SCAN_JOB or job.state != "failed":
        return None
    generation = job.claim_generation
    if generation <= 0:
        return {"status": "generation_missing"}
    try:
        root = EVENT_RUN_ROOT.resolve()
        staging = (root / ".staging" / job.job_id / str(generation)).resolve()
    except OSError:
        return {"status": "staging_path_unresolvable"}
    if root not in staging.parents:
        return {"status": "staging_outside_event_root"}
    stderr_path = staging / "stderr.log"
    try:
        if not stderr_path.is_file():
            return {"status": "stderr_missing"}
        size = stderr_path.stat().st_size
        if size > EVENT_FAILURE_LOG_MAX_BYTES:
            return {"status": "stderr_too_large", "size_bytes": size}
        stderr = stderr_path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return {"status": "stderr_unreadable"}
    response: dict[str, Any] = {
        "status": "available",
        "stderr": stderr,
        "size_bytes": size,
    }
    audit_path = staging / "event-library" / "projection" / "static_industry_coverage_audit.json"
    try:
        if audit_path.is_file() and audit_path.stat().st_size <= EVENT_FAILURE_AUDIT_MAX_BYTES:
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            if isinstance(audit, dict) and audit.get("schema_version") == (
                "quanthus.p4-static-industry-coverage-audit.v1"
            ):
                response["static_industry_coverage_audit"] = audit
    except (OSError, json.JSONDecodeError):
        pass
    return response


def _event_scan_summary(job: JobRecord) -> dict[str, Any] | None:
    if job.kind != P4_EVENT_SCAN_JOB or job.state != "succeeded":
        return None
    if not job.result_ref:
        return {"status": "result_ref_missing"}
    artifact = job.result_ref.get("artifact_ref")
    if not isinstance(artifact, str):
        return {"status": "artifact_ref_invalid"}
    try:
        root = EVENT_RUN_ROOT.resolve()
        run = Path(artifact).resolve()
    except OSError:
        return {"status": "artifact_path_unresolvable"}
    if run == root or root not in run.parents:
        return {"status": "artifact_outside_event_root"}
    summary_path = run / "event-library" / "summary.json"
    try:
        if not summary_path.is_file():
            return {"status": "summary_missing"}
        if summary_path.stat().st_size > EVENT_SUMMARY_MAX_BYTES:
            return {"status": "summary_too_large"}
        raw = json.loads(summary_path.read_text(encoding="utf-8"))
    except OSError:
        return {"status": "summary_unreadable"}
    except json.JSONDecodeError:
        return {"status": "summary_invalid_json"}
    if not isinstance(raw, dict):
        return {"status": "summary_not_object"}
    counters = raw.get("scan_counters")
    if not isinstance(counters, dict):
        return {"status": "scan_counters_missing"}
    expected = {
        "evaluated_rows",
        "raw_triggers",
        "isolated_candidates",
        "concentration_retained",
        "candidate_events",
        "reason_counts",
    }
    if set(counters) != expected:
        return {"status": "scan_counter_keys_invalid"}
    reason_counts = counters.get("reason_counts")
    if not isinstance(reason_counts, dict) or any(
        not isinstance(key, str)
        or not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
        for key, value in reason_counts.items()
    ):
        return {"status": "reason_counts_invalid"}
    for field in expected - {"reason_counts"}:
        value = counters.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return {"status": "scan_counter_value_invalid"}
    return {
        "status": "available",
        "industry_mapping_identity": _industry_mapping_identity_summary(),
        "scan_counters": {
            "evaluated_rows": counters["evaluated_rows"],
            "raw_triggers": counters["raw_triggers"],
            "isolated_candidates": counters["isolated_candidates"],
            "concentration_retained": counters["concentration_retained"],
            "candidate_events": counters["candidate_events"],
            "reason_counts": dict(sorted(reason_counts.items())),
        }
    }


def public_job(job: JobRecord) -> dict[str, Any]:
    value = {
        "job_id": job.job_id,
        "kind": job.kind,
        "actor_user_id": job.actor_user_id,
        "idempotency_key": job.idempotency_key,
        "input_ref": job.input_ref,
        "input_digest": job.input_digest,
        "queue": job.queue,
        "concurrency_key": job.concurrency_key,
        "state": job.state,
        "stage": job.stage,
        "progress_detail": job.progress_detail or {},
        "progress": job.progress,
        "cancel_requested": job.cancel_requested,
        "attempt": job.attempt,
        "result_ref": job.result_ref,
        "error_reason": job.error_reason,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "claimed_by": job.claimed_by,
        "claim_generation": job.claim_generation,
        "lease_expires_at": job.lease_expires_at.isoformat() if job.lease_expires_at else None,
        "cancel_requested_at": (
            job.cancel_requested_at.isoformat() if job.cancel_requested_at else None
        ),
    }
    event_summary = _event_scan_summary(job)
    if event_summary is not None:
        value["event_scan_summary"] = event_summary
    event_failure = _event_scan_failure(job)
    if event_failure is not None:
        value["event_scan_failure"] = event_failure
    return value


class LocalJobRunner:
    """Executes admitted finite jobs in-process; no queue or hidden worker exists."""

    def __init__(
        self,
        store: PlatformStore,
        handlers: Mapping[str, JobHandler],
        *,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._store = store
        self._handlers = dict(handlers)
        self._now = now
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}

    @property
    def job_kinds(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))

    def submit(
        self,
        *,
        kind: str,
        actor_user_id: str,
        idempotency_key: str,
        input_ref: Mapping[str, Any],
        execute: bool = True,
    ) -> tuple[JobRecord, bool]:
        if kind not in self._handlers:
            raise ValueError("job kind is not admitted")
        if not 1 <= len(idempotency_key) <= 128:
            raise ValueError("idempotency_key must be 1-128 characters")
        now = self._now()
        proposed = JobRecord(
            job_id=f"job_{uuid.uuid4().hex}",
            kind=kind,
            actor_user_id=actor_user_id,
            idempotency_key=idempotency_key,
            input_ref=dict(input_ref),
            state="queued",
            progress=0,
            cancel_requested=False,
            attempt=0,
            result_ref=None,
            error_reason=None,
            created_at=now,
            updated_at=now,
            input_digest=input_digest(input_ref),
            available_at=now,
        )
        job, created = self._store.create_job(proposed)
        if execute and job.state == "queued":
            job = self.run(job.job_id)
        return job, created

    def dispatch(self, job_id: str) -> JobRecord:
        job = self._store.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.state != "queued":
            return job
        with self._locks_guard:
            thread = self._threads.get(job_id)
            if thread is None or not thread.is_alive():
                thread = threading.Thread(
                    target=self._run_dispatched,
                    args=(job_id,),
                    name=f"quanthus-platform-{job_id}",
                    daemon=True,
                )
                self._threads[job_id] = thread
                thread.start()
        return self._store.get_job(job_id) or job

    def wait(self, job_id: str, timeout: float | None = None) -> JobRecord:
        with self._locks_guard:
            thread = self._threads.get(job_id)
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                raise TimeoutError(job_id)
        job = self._store.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def get(self, job_id: str) -> JobRecord | None:
        return self._store.get_job(job_id)

    def list(self, kind: str | None = None, limit: int = 50) -> tuple[JobRecord, ...]:
        return self._store.list_jobs(kind, limit)

    def run(self, job_id: str) -> JobRecord:
        lock = self._job_lock(job_id)
        with lock:
            job = self._store.get_job(job_id)
            if job is None:
                raise KeyError(job_id)
            if job.state in {"succeeded", "failed", "canceled"}:
                return job
            job = self._store.start_job(job.job_id, self._now())
            if job.state in {"succeeded", "failed", "canceled"}:
                return job
            if job.state != "running":
                raise RuntimeError("job did not enter running state")
            active_job_id = job.job_id
            handler = self._handlers.get(job.kind)
            if handler is None:
                return self._fail(job, "job_kind_not_admitted")

            def update_progress(value: int) -> None:
                nonlocal job
                if not 1 <= value <= 99:
                    raise ValueError("running job progress must be between 1 and 99")
                observed = self._store.get_job(active_job_id)
                if observed is None:
                    raise KeyError(active_job_id)
                job = replace(
                    observed,
                    progress=max(observed.progress, value),
                    updated_at=self._now(),
                )
                self._store.update_job(job)

            def canceled() -> bool:
                observed = self._store.get_job(active_job_id)
                return observed is None or observed.cancel_requested

            try:
                result_ref = handler(job.input_ref, update_progress, canceled)
                observed = self._store.get_job(active_job_id)
                if observed is None:
                    raise KeyError(active_job_id)
                if observed.cancel_requested:
                    return self._cancel_pending(observed)
                completed = self._now()
                job = replace(
                    observed,
                    state="succeeded",
                    progress=100,
                    result_ref=dict(result_ref),
                    completed_at=completed,
                    updated_at=completed,
                )
                return self._store.finish_job(job)
            except Exception as exc:
                observed = self._store.get_job(job.job_id) or job
                if observed.cancel_requested:
                    return self._cancel_pending(observed)
                reason = getattr(exc, "reason_code", None) or type(exc).__name__
                return self._fail(observed, str(reason)[:160])

    def cancel(self, job_id: str) -> JobRecord:
        job, accepted = self._store.request_job_cancel(job_id, self._now())
        if not accepted:
            raise JobNotCancelableError("job_is_terminal")
        return job

    def resume_pending(self) -> tuple[JobRecord, ...]:
        return tuple(
            self.dispatch(job.job_id)
            for job in self._store.resumable_jobs()
            if job.queue == "local" and job.kind in self._handlers
        )

    def _run_dispatched(self, job_id: str) -> None:
        try:
            self.run(job_id)
        finally:
            with self._locks_guard:
                self._threads.pop(job_id, None)

    def _job_lock(self, job_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(job_id, threading.Lock())

    def _cancel_pending(self, job: JobRecord) -> JobRecord:
        return self._store.finish_job_canceled(job.job_id, self._now())

    def _fail(self, job: JobRecord, reason: str) -> JobRecord:
        completed = self._now()
        failed = replace(
            job,
            state="failed",
            error_reason=reason,
            completed_at=completed,
            updated_at=completed,
        )
        return self._store.finish_job(failed)


class DurableJobQueue:
    """API-facing durable queue; it persists work but never executes a handler."""

    def __init__(self, store: PlatformStore, admitted_kinds: Mapping[str, str]) -> None:
        self._store = store
        self._queues = dict(admitted_kinds)

    def submit(
        self,
        *,
        kind: str,
        actor_user_id: str,
        idempotency_key: str,
        input_ref: Mapping[str, Any],
        concurrency_key: str | None = None,
    ) -> tuple[JobRecord, bool]:
        queue = self._queues.get(kind)
        if queue is None:
            raise ValueError("job kind is not admitted")
        if not 1 <= len(idempotency_key) <= 128:
            raise ValueError("idempotency_key must be 1-128 characters")
        now = _utc_now()
        return self._store.create_job(
            JobRecord(
                job_id=f"job_{uuid.uuid4().hex}",
                kind=kind,
                actor_user_id=actor_user_id,
                idempotency_key=idempotency_key,
                input_ref=dict(input_ref),
                input_digest=input_digest(input_ref),
                queue=queue,
                concurrency_key=concurrency_key,
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
        )

    def get(self, job_id: str) -> JobRecord | None:
        return self._store.get_job(job_id)

    def list(self, kind: str | None = None, limit: int = 50) -> tuple[JobRecord, ...]:
        return self._store.list_jobs(kind, limit)

    def cancel(self, job_id: str) -> JobRecord:
        job, accepted = self._store.request_job_cancel(job_id, _utc_now())
        if not accepted:
            raise JobNotCancelableError("job_is_terminal")
        return job


def attribution_verification_handler(
    port: AttributionVerificationPort,
) -> JobHandler:
    def handle(
        input_ref: Mapping[str, Any],
        progress: Callable[[int], None],
        canceled: Callable[[], bool],
    ) -> dict[str, Any]:
        if set(input_ref) != {"release_id"} or not isinstance(input_ref["release_id"], str):
            raise ValueError("invalid attribution verification input reference")
        progress(20)
        if canceled():
            return {}
        verified = port.verify(input_ref["release_id"])
        progress(90)
        if canceled():
            return {}
        return verified.to_dict()

    return handle

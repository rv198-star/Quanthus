from __future__ import annotations

import json
import threading
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from quanthus.platform.jobs import (
    ATTRIBUTION_VERIFICATION_JOB,
    P4_EVENT_SCAN_JOB,
    JobNotCancelableError,
    LocalJobRunner,
    public_job,
)
from quanthus.platform.models import JobRecord
from quanthus.platform.persistence import InMemoryPlatformStore


def test_public_event_job_exposes_bounded_scan_counters(tmp_path, monkeypatch) -> None:
    run = tmp_path / "runs" / "run_test"
    event_library = run / "event-library"
    event_library.mkdir(parents=True)
    (event_library / "summary.json").write_text(
        json.dumps(
            {
                "scan_counters": {
                    "evaluated_rows": 100,
                    "raw_triggers": 7,
                    "isolated_candidates": 6,
                    "concentration_retained": 4,
                    "candidate_events": 3,
                    "reason_counts": {
                        "not_triggered": 93,
                        "same_security_isolation": 1,
                        "static_industry_missing": 2,
                        "split_policy_rejected": 1,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    mapping_path = tmp_path / "static_industry_mapping.json"
    mapping_path.write_text(
        json.dumps(
            {
                "mapping_version": "static-level1-concentration-research-v1",
                "memberships": [
                    {"security_id": "SZSE-000001"},
                    {"security_id": "600001"},
                    {"security_id": "security_abc123"},
                    {"security_id": "legacy-other"},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("quanthus.platform.jobs.EVENT_RUN_ROOT", tmp_path)
    monkeypatch.setattr("quanthus.platform.jobs.P4_INDUSTRY_MAPPING_PATH", mapping_path)
    now = datetime.now(timezone.utc)
    job = JobRecord(
        job_id="job_0123456789abcdef0123456789abcdef",
        kind=P4_EVENT_SCAN_JOB,
        actor_user_id="usr_test",
        idempotency_key="event-summary-test",
        input_ref={},
        input_digest="sha256:test",
        queue="event",
        concurrency_key="p4-event-scan:test",
        state="succeeded",
        stage="succeeded",
        progress=100,
        progress_detail={"candidate_events": 3},
        cancel_requested=False,
        attempt=1,
        result_ref={"artifact_ref": str(run), "run_id": "run_test"},
        error_reason=None,
        created_at=now,
        updated_at=now,
        started_at=now,
        completed_at=now,
    )

    public = public_job(job)

    assert public["event_scan_summary"] == {
        "status": "available",
        "industry_mapping_identity": {
            "status": "available",
            "mapping_version": "static-level1-concentration-research-v1",
            "membership_count": 4,
            "security_id_namespace_counts": {
                "dwd_governed_security_id": 1,
                "exchange_prefixed_security_id": 1,
                "plain_six_digit_security_id": 1,
                "other_security_id": 1,
            },
        },
        "scan_counters": {
            "evaluated_rows": 100,
            "raw_triggers": 7,
            "isolated_candidates": 6,
            "concentration_retained": 4,
            "candidate_events": 3,
            "reason_counts": {
                "not_triggered": 93,
                "same_security_isolation": 1,
                "split_policy_rejected": 1,
                "static_industry_missing": 2,
            },
        }
    }


def test_public_failed_event_job_exposes_bounded_stderr(tmp_path, monkeypatch) -> None:
    job_id = "job_abcdefabcdefabcdefabcdefabcdefab"
    staging = tmp_path / ".staging" / job_id / "2"
    staging.mkdir(parents=True)
    (staging / "stderr.log").write_text(
        "P4 static industry coverage missing 17 isolated trigger securities\n",
        encoding="utf-8",
    )
    audit_path = staging / "event-library" / "projection" / "static_industry_coverage_audit.json"
    audit_path.parent.mkdir(parents=True)
    audit = {
        "schema_version": "quanthus.p4-static-industry-coverage-audit.v1",
        "candidate_security_count": 100,
        "mapped_security_count": 83,
        "missing_security_count": 17,
        "missing_current_listed": 2,
        "missing_historical_only": 15,
        "missing_reason_counts": {"no_company_industry_membership": 17},
        "samples": [],
    }
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    monkeypatch.setattr("quanthus.platform.jobs.EVENT_RUN_ROOT", tmp_path)
    now = datetime.now(timezone.utc)
    job = JobRecord(
        job_id=job_id,
        kind=P4_EVENT_SCAN_JOB,
        actor_user_id="usr_test",
        idempotency_key="event-failure-test",
        input_ref={},
        input_digest="sha256:test",
        queue="event",
        concurrency_key="p4-event-scan:test",
        state="failed",
        stage="failed",
        progress=5,
        cancel_requested=False,
        attempt=1,
        result_ref=None,
        error_reason="event_scan_child_exit_2",
        created_at=now,
        updated_at=now,
        started_at=now,
        completed_at=now,
        claimed_by="event-test",
        claim_generation=2,
    )

    assert public_job(job)["event_scan_failure"] == {
        "status": "available",
        "stderr": "P4 static industry coverage missing 17 isolated trigger securities",
        "size_bytes": 67,
        "static_industry_coverage_audit": audit,
    }


def test_public_event_job_does_not_read_summary_outside_event_root(tmp_path, monkeypatch) -> None:
    outside = tmp_path.parent / "outside-event-run-test"
    event_library = outside / "event-library"
    event_library.mkdir(parents=True, exist_ok=True)
    (event_library / "summary.json").write_text(
        json.dumps(
            {
                "scan_counters": {
                    "evaluated_rows": 1,
                    "raw_triggers": 1,
                    "isolated_candidates": 1,
                    "concentration_retained": 1,
                    "candidate_events": 1,
                    "reason_counts": {},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("quanthus.platform.jobs.EVENT_RUN_ROOT", tmp_path)
    now = datetime.now(timezone.utc)
    job = JobRecord(
        job_id="job_fedcba9876543210fedcba9876543210",
        kind=P4_EVENT_SCAN_JOB,
        actor_user_id="usr_test",
        idempotency_key="event-summary-path-test",
        input_ref={},
        input_digest="sha256:test",
        queue="event",
        concurrency_key="p4-event-scan:test",
        state="succeeded",
        stage="succeeded",
        progress=100,
        cancel_requested=False,
        attempt=1,
        result_ref={"artifact_ref": str(outside), "run_id": "run_test"},
        error_reason=None,
        created_at=now,
        updated_at=now,
    )

    assert public_job(job)["event_scan_summary"] == {
        "status": "artifact_outside_event_root"
    }


def test_gate_query_returns_versioned_repository_facts(kernel) -> None:
    facts = kernel.gate_query.read()

    assert facts.port_version == "quanthus.gate-query.v1"
    assert facts.gate_version.startswith("platform-kernel-")
    assert facts.current_gate == "PK6"
    assert Path(facts.source_ref).name == "platform-kernel-gates.v1.json"


def test_real_attribution_verification_job_is_idempotent(kernel, attribution_release) -> None:
    actor = kernel.store.get_user_by_username("admin")
    assert actor is not None
    first, created = kernel.jobs.submit(
        kind=ATTRIBUTION_VERIFICATION_JOB,
        actor_user_id=actor.user_id,
        idempotency_key="verify-release-once",
        input_ref={"release_id": attribution_release.name},
    )
    second, second_created = kernel.jobs.submit(
        kind=ATTRIBUTION_VERIFICATION_JOB,
        actor_user_id=actor.user_id,
        idempotency_key="verify-release-once",
        input_ref={"release_id": attribution_release.name},
    )

    assert created is True
    assert second_created is False
    assert first.job_id == second.job_id
    assert first.state == "succeeded"
    assert first.result_ref is not None
    assert first.result_ref["port_version"] == "quanthus.attribution-verification.v1"
    assert first.result_ref["release_id"] == attribution_release.name
    assert first.result_ref["artifact_ref"] == attribution_release.as_posix()
    assert "workspace" not in first.result_ref
    try:
        kernel.jobs.cancel(first.job_id)
    except JobNotCancelableError as exc:
        assert str(exc) == "job_is_terminal"
    else:
        raise AssertionError("terminal job was cancelable")


def test_pending_job_can_cancel_and_running_envelope_can_resume(
    kernel, attribution_release
) -> None:
    actor = kernel.store.get_user_by_username("admin")
    assert actor is not None
    pending, _ = kernel.jobs.submit(
        kind=ATTRIBUTION_VERIFICATION_JOB,
        actor_user_id=actor.user_id,
        idempotency_key="cancel-before-start",
        input_ref={"release_id": attribution_release.name},
        execute=False,
    )
    assert kernel.jobs.cancel(pending.job_id).state == "canceled"

    resumable, _ = kernel.jobs.submit(
        kind=ATTRIBUTION_VERIFICATION_JOB,
        actor_user_id=actor.user_id,
        idempotency_key="resume-after-restart",
        input_ref={"release_id": attribution_release.name},
        execute=False,
    )
    resumed = kernel.jobs.resume_pending()
    assert [job.job_id for job in resumed] == [resumable.job_id]
    assert kernel.jobs.wait(resumable.job_id, timeout=10).state == "succeeded"


def test_running_local_job_honors_cooperative_cancel() -> None:
    store = InMemoryPlatformStore()
    entered = threading.Event()
    release = threading.Event()

    def blocking_handler(input_ref, progress, canceled):  # type: ignore[no-untyped-def]
        assert input_ref == {"artifact_ref": "immutable:test"}
        progress(25)
        entered.set()
        assert release.wait(timeout=5)
        if canceled():
            return {}
        return {"artifact_ref": "immutable:result"}

    runner = LocalJobRunner(store, {"test.finite.v1": blocking_handler})
    pending, _ = runner.submit(
        kind="test.finite.v1",
        actor_user_id="usr_test",
        idempotency_key="running-cancel",
        input_ref={"artifact_ref": "immutable:test"},
        execute=False,
    )
    runner.dispatch(pending.job_id)
    assert entered.wait(timeout=5)
    observed = runner.cancel(pending.job_id)
    assert observed.cancel_requested is True
    release.set()

    assert runner.wait(pending.job_id, timeout=5).state == "canceled"


def test_in_memory_restart_converts_running_envelope_to_pending() -> None:
    store = InMemoryPlatformStore()

    def handler(input_ref, progress, canceled):  # type: ignore[no-untyped-def]
        assert input_ref == {"artifact_ref": "immutable:test"}
        assert canceled() is False
        progress(50)
        return {"artifact_ref": "immutable:result"}

    first_runner = LocalJobRunner(store, {"test.restart.v1": handler})
    pending, _ = first_runner.submit(
        kind="test.restart.v1",
        actor_user_id="usr_test",
        idempotency_key="restart-running",
        input_ref={"artifact_ref": "immutable:test"},
        execute=False,
    )
    store.update_job(replace(pending, state="running", progress=75, attempt=1))

    restarted_runner = LocalJobRunner(store, {"test.restart.v1": handler})
    resumed = restarted_runner.resume_pending()
    assert [job.job_id for job in resumed] == [pending.job_id]
    terminal = restarted_runner.wait(pending.job_id, timeout=5)
    assert terminal.state == "succeeded"
    assert terminal.attempt == 2


def test_cancel_request_wins_atomic_terminal_write() -> None:
    store = InMemoryPlatformStore()
    runner = LocalJobRunner(store, {"test.atomic.v1": lambda *_args: {}})
    pending, _ = runner.submit(
        kind="test.atomic.v1",
        actor_user_id="usr_test",
        idempotency_key="atomic-terminal",
        input_ref={"artifact_ref": "immutable:test"},
        execute=False,
    )
    running = replace(pending, state="running", progress=90, attempt=1)
    store.update_job(running)
    requested = replace(running, cancel_requested=True)
    store.update_job(requested)
    stale_success = replace(
        running,
        state="succeeded",
        progress=100,
        result_ref={"artifact_ref": "immutable:result"},
        completed_at=running.updated_at,
    )

    terminal = store.finish_job(stale_success)
    assert terminal.state == "canceled"
    assert terminal.cancel_requested is True
    assert terminal.result_ref is None


def test_cancel_before_dispatch_prevents_handler_start() -> None:
    store = InMemoryPlatformStore()
    calls = 0

    def handler(*_args):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return {"artifact_ref": "immutable:unexpected"}

    runner = LocalJobRunner(store, {"test.cancel-before.v1": handler})
    pending, _ = runner.submit(
        kind="test.cancel-before.v1",
        actor_user_id="usr_test",
        idempotency_key="cancel-before-dispatch",
        input_ref={"artifact_ref": "immutable:test"},
        execute=False,
    )
    assert runner.cancel(pending.job_id).state == "canceled"

    assert runner.run(pending.job_id).state == "canceled"
    assert calls == 0

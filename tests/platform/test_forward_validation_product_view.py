from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from quanthus.forward_validation.product_view import ForwardValidationProductView
from quanthus.forward_validation.runner import (
    load_forward_worker_config,
    make_disabled_rehearsal_input,
)
from quanthus.platform.jobs import P6_FORWARD_CYCLE_JOB, DurableJobQueue, input_digest
from quanthus.platform.models import JobRecord
from quanthus.platform.persistence import InMemoryPlatformStore
from quanthus.policy import find_project_root

ROOT = find_project_root()


def _write_enabled_activation(candidate_run_root: Path) -> dict[str, object]:
    activation_root = candidate_run_root / "p6-forward/activation"
    decision_path = activation_root / "decisions/p6-activation-20260919-test.json"
    decision_path.parent.mkdir(parents=True)
    decision: dict[str, object] = {
        "schema_version": "quanthus.p6-activation-decision.v1",
        "work_item": "P6-ACTIVATE-001",
        "decision_id": "p6-activation-20260919-test",
        "state": "enabled",
        "decision_at": "2026-09-18T16:30:00+00:00",
        "activation_market_date": "2026-09-19",
        "entry_proposal_digest": (
            "sha256:3c9356e49dbdd6356754b42cfd34af11949289121454a05bdbf0c0ff2aebaa18"
        ),
        "runtime_sha256": "sha256:" + "1" * 64,
        "deployment_revision": "2" * 40,
        "previous_production_revision": "3" * 40,
        "disabled_rehearsal_manifest_digest": "sha256:" + "4" * 64,
        "rollback_drill_digest": "sha256:" + "5" * 64,
        "activation_mandate_digest": "sha256:" + "6" * 64,
        "approval_authority": "human",
        "official_prospective_cycle_authorized": True,
        "paper_authority_only": True,
        "notification_authorized": False,
        "broker_authorized": False,
        "live_capital_authorized": False,
        "arbitration_2022_2024_access_authorized": False,
        "permanent_blind_2025_access_authorized": False,
        "decision_digest": "sha256:" + "7" * 64,
    }
    decision_bytes = json.dumps(decision, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    decision_path.write_bytes(decision_bytes)
    pointer = {
        "schema_version": "quanthus.p6-activation-current.v1",
        "decision_path": "decisions/p6-activation-20260919-test.json",
        "decision_sha256": "sha256:" + hashlib.sha256(decision_bytes).hexdigest(),
    }
    (activation_root / "CURRENT.json").write_text(
        json.dumps(pointer, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return decision


def test_target_candidate_projects_frozen_pre_activation_state() -> None:
    store = InMemoryPlatformStore()
    view = ForwardValidationProductView(ROOT, store)

    observed = view.candidate("candidate.p4-trend-risk", "1.0.1")

    assert observed["state"] == "engineering_ready_activation_disabled"
    assert observed["read_only"] is True
    assert observed["admission"] == {
        "state": "disabled",
        "activation_owner": "P6-ACTIVATE-001",
        "first_official_market_date_rule": (
            "first_complete_trading_session_strictly_after_activation_decision_market_date"
        ),
        "required_release_state": "READY_AND_DEEPLY_VERIFIABLE",
        "official_prospective_cycle_authorized": False,
        "activation_decision": None,
    }
    assert observed["evidence"] == {
        "state": "waiting_activation",
        "official_collection_sessions_completed": 0,
        "independent_blocks_completed": 0,
        "settled_target_count": 0,
        "censored_target_count": 0,
        "first_official_market_date": None,
        "last_admitted_dwd_release": None,
    }
    assert observed["budget"]["collection_trading_sessions"] == 66
    assert observed["budget"]["collection_block_count"] == 3
    assert observed["budget"]["target_maturity_tail_trading_sessions"] == 20
    assert observed["budget"]["maximum_calendar_days"] == 150
    assert observed["latest_forward_job"] is None
    assert observed["paper"]["unit_count"] == 5
    assert observed["paper"]["unit_notional_cny"] == "100000.00"
    assert observed["paper"]["configured_total_notional_cny"] == "500000.00"
    assert observed["paper"]["actual_execution_authorized"] is False
    assert observed["paper"]["ledger_head"] is None
    assert view.health_summary() == {
        "state": "engineering_ready_activation_disabled",
        "admission_state": "disabled",
        "official_collection_sessions_completed": 0,
        "independent_blocks_completed": 0,
        "first_official_market_date": None,
        "last_admitted_dwd_release": None,
    }
    assert observed["authority"] == {
        "notification_authorized": False,
        "broker_authorized": False,
        "live_capital_authorized": False,
        "stage_action_authority": False,
        "parameter_edit_authorized": False,
        "activation_control_exposed": False,
    }
    assert set(observed["scorecards"]["roles"]) == {
        "trend_assessment",
        "position_risk",
        "aggregator",
        "complete_strategy",
        "paper_operation",
    }
    assert all(str(value).startswith("sha256:") for value in observed["source_digests"].values())


def test_enabled_activation_is_projected_read_only(tmp_path: Path) -> None:
    candidate_run_root = tmp_path / "candidate-runs"
    decision = _write_enabled_activation(candidate_run_root)
    view = ForwardValidationProductView(
        ROOT,
        InMemoryPlatformStore(),
        candidate_run_root=candidate_run_root,
    )

    observed = view.candidate("candidate.p4-trend-risk", "1.0.1")

    assert observed["state"] == "prospective_activation_enabled"
    assert observed["admission"]["state"] == "enabled"
    assert observed["admission"]["official_prospective_cycle_authorized"] is True
    assert observed["admission"]["activation_decision"] == {
        "decision_id": decision["decision_id"],
        "decision_at": decision["decision_at"],
        "activation_market_date": decision["activation_market_date"],
        "decision_digest": decision["decision_digest"],
        "deployment_revision": decision["deployment_revision"],
    }
    assert observed["evidence"]["state"] == "activated_waiting_first_release"
    assert observed["evidence"]["official_collection_sessions_completed"] == 0
    assert observed["paper"]["state"] == "paper_authority_active"
    assert observed["paper"]["actual_execution_authorized"] is False
    assert observed["authority"]["broker_authorized"] is False
    assert observed["authority"]["live_capital_authorized"] is False
    assert observed["source_digests"]["activation_decision"] == decision["decision_digest"]
    assert view.health_summary() == {
        "state": "prospective_activation_enabled",
        "admission_state": "enabled",
        "official_collection_sessions_completed": 0,
        "independent_blocks_completed": 0,
        "first_official_market_date": None,
        "last_admitted_dwd_release": None,
    }


def test_enabled_activation_projects_official_session_progress(tmp_path: Path) -> None:
    candidate_run_root = tmp_path / "candidate-runs"
    _write_enabled_activation(candidate_run_root)
    store = InMemoryPlatformStore()
    now = datetime(2026, 9, 21, 8, tzinfo=timezone.utc)
    input_ref = {"mode": "official_cycle", "market_date": "2026-09-21"}
    store.create_job(
        JobRecord(
            job_id="job_" + "8" * 32,
            kind=P6_FORWARD_CYCLE_JOB,
            actor_user_id="usr_p6_web",
            idempotency_key="p6-official-2026-09-21-test",
            input_ref=input_ref,
            state="succeeded",
            progress=100,
            cancel_requested=False,
            attempt=1,
            result_ref={
                "run_id": "p6_run_test",
                "summary": {
                    "mode": "official_cycle",
                    "official_evidence": True,
                    "market_date": "2026-09-21",
                    "collection_session_count": 1,
                    "tail_session_count": 0,
                },
            },
            error_reason=None,
            created_at=now,
            updated_at=now,
            completed_at=now,
            queue="replay",
            input_digest=input_digest(input_ref),
            concurrency_key="p6-forward-cycle:official",
            available_at=now,
            stage="succeeded",
            started_at=now,
            claimed_by="worker-test",
            claim_generation=1,
        )
    )
    view = ForwardValidationProductView(ROOT, store, candidate_run_root=candidate_run_root)

    observed = view.candidate("candidate.p4-trend-risk", "1.0.1")

    assert observed["evidence"]["state"] == "collecting"
    assert observed["evidence"]["official_collection_sessions_completed"] == 1
    assert observed["evidence"]["independent_blocks_completed"] == 0
    assert observed["evidence"]["first_official_market_date"] == "2026-09-21"
    assert observed["evidence"]["last_admitted_dwd_release"] == "2026-09-21"


def test_non_target_candidate_is_not_given_p6_authority_or_state() -> None:
    view = ForwardValidationProductView(ROOT, InMemoryPlatformStore())

    assert view.candidate("baseline.ma-trend", "1.0.0") == {
        "state": "not_applicable",
        "read_only": True,
    }


def test_latest_disabled_rehearsal_job_is_projected_without_becoming_evidence() -> None:
    store = InMemoryPlatformStore()
    view = ForwardValidationProductView(ROOT, store)
    config = load_forward_worker_config(
        ROOT / "config/forward-validation/runtime/p6-forward-worker.v1.json",
        root=ROOT,
    )
    job_input = make_disabled_rehearsal_input(rehearsal_id="web-view-rehearsal", config=config)
    queue = DurableJobQueue(store, {P6_FORWARD_CYCLE_JOB: "replay"})
    job, created = queue.submit(
        kind=P6_FORWARD_CYCLE_JOB,
        actor_user_id="usr_p6_web",
        idempotency_key=job_input.idempotency_key,
        input_ref=job_input.to_dict(),
        concurrency_key=job_input.concurrency_key,
    )
    assert created is True

    observed = view.candidate("candidate.p4-trend-risk", "1.0.1")

    assert observed["latest_forward_job"]["job_id"] == job.job_id
    assert observed["latest_forward_job"]["state"] == "queued"
    assert observed["evidence"]["official_collection_sessions_completed"] == 0
    assert observed["evidence"]["state"] == "waiting_activation"

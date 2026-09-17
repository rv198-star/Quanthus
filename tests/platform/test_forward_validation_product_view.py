from __future__ import annotations

from pathlib import Path

from quanthus.forward_validation.product_view import ForwardValidationProductView
from quanthus.forward_validation.runner import (
    load_forward_worker_config,
    make_disabled_rehearsal_input,
)
from quanthus.platform.jobs import DurableJobQueue, P6_FORWARD_CYCLE_JOB
from quanthus.platform.persistence import InMemoryPlatformStore
from quanthus.policy import find_project_root

ROOT = find_project_root()


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

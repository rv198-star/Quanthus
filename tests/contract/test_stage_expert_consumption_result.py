from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.sync_github_planning import work_items

ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / "config/expert-strategies/stage10-11-consumption-discovery-result.v1.json"
REVIEW = ROOT / "docs/reviews/p3-expert-stage-consumption-result-review-20260917.md"
ACCEPTANCE = ROOT / "docs/acceptance/p3-expert-stage-consumption-20260917.md"
WORK_ITEM = ROOT / "docs/work-items/expert-stage-semantic-consumption.md"


def test_p3_expert_006_closes_sufficient_without_reopening_stage_research() -> None:
    value = json.loads(RESULT.read_text(encoding="utf-8"))
    assert value["schema_version"] == "quanthus.stage-expert-consumption-discovery-result.v1"
    assert value["work_item"] == "P3-EXPERT-006"
    assert value["state"] == "SUFFICIENT_FOR_BOUNDED_CONSUMPTION_NO_STAGE_REOPEN"

    for binding in value["bindings"].values():
        path = ROOT / binding["path"]
        observed = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        assert observed == binding["sha256"]

    prep = value["packet_preparation"]
    assert prep["recovery_state"] == "succeeded"
    assert prep["role_packet_count"] == 4
    assert prep["observation_count"] == 10
    assert prep["role_observation_evaluation_count"] == 40
    assert prep["market_data_reads"] == 0
    assert prep["dwd_provider_opens"] == 0
    assert prep["future_outcome_reads"] == 0
    assert prep["protected_partition_reads"] == 0
    assert prep["strict_pit_claim"] is False
    assert prep["execution_authorized"] is False

    delivery = value["evaluator_delivery"]
    assert delivery["production_named_volume_directly_mounted_by_evaluator"] is False
    assert delivery["governed_worker_status_visible_payload_used"] is True
    assert delivery["semantic_key_exposed_to_evaluator"] is False
    assert delivery["byte_identical_to_role_packets_artifact"] is False
    assert delivery["answer_relevant_information_preserved"] is True
    assert delivery["transport_review_disposition"] == (
        "ACCEPT_TRANSPORT_EQUIVALENT_WITH_AUDIT_NOTE"
    )
    assert delivery["transport_review_accept_count"] == 4
    assert delivery["transport_review_rerun_required_count"] == 0

    evaluation = value["evaluation"]
    assert evaluation["model"] == "gpt-6-astra"
    assert evaluation["provider"] == "sub2api"
    assert evaluation["reasoning_effort"] == "low"
    assert evaluation["role_session_count"] == 4
    assert evaluation["same_model_provider_effort_across_roles"] is True
    assert evaluation["result_bearing_retries"] == 0
    assert evaluation["semantic_key_hidden_until_all_outputs_sealed"] is True
    assert set(evaluation["roles"]) == {
        "short_term_event_driven",
        "fund_midterm_risk",
        "quant_research",
        "data_governance",
    }

    metrics = value["mechanical_metrics"]
    assert metrics["evaluation_count"] == 40
    assert metrics["semantic_understanding_error_count"] == 0
    assert metrics["surface_sufficient_yes_count"] == 40
    assert metrics["boundary_unsafe_count"] == 0
    assert metrics["other_gap_count"] == 0
    assert metrics["triggered_gap_families"] == []
    assert all(item["evaluation_count"] == 0 for item in metrics["gap_support"].values())
    assert metrics["frozen_decision"] == "SUFFICIENT_FOR_BOUNDED_CONSUMPTION_NO_STAGE_REOPEN"

    review = value["post_result_review"]
    assert review["reviewer_count"] == 4
    assert review["mechanical_result_accept_count"] == 4
    assert review["no_gap_trigger_accept_count"] == 4
    assert review["phase1_remain_closed_count"] == 4
    assert review["disposition"] == "ACCEPT_SUFFICIENT_CLOSE_NO_STAGE_REOPEN"

    transport = value["transport_review"]
    assert transport["reviewer_count"] == 4
    assert transport["accept_equivalent_count"] == 4
    assert transport["semantic_key_hidden_count"] == 4
    assert transport["rerun_required_count"] == 0

    disposition = value["phase_disposition"]
    assert disposition["stage10_11_sufficient_for_tested_bounded_consumption"] is True
    assert disposition["specific_downstream_stage_gap_identified"] is False
    assert disposition["stock_stage_phase1_remains_closed"] is True
    assert disposition["stage_research_reopened"] is False
    assert disposition["new_stage_code_authorized"] is False
    assert disposition["automatic_successor"] is None
    assert disposition["continuous_facts_remain_authoritative"] is True
    assert disposition["stage10_11_remain_development_advisory"] is True

    assert REVIEW.is_file()
    assert ACCEPTANCE.is_file()
    assert WORK_ITEM.is_file()


def test_p3_expert_006_stays_closed_when_strategy_input_qualification_stops() -> None:
    items = {item.stable_id: item for item in work_items(ROOT)}
    assert items["P3-EXPERT-006"].status == "closed"
    ready_p3 = {
        item.stable_id for item in items.values() if item.phase == "P3" and item.status == "ready"
    }
    assert ready_p3 == {"P3-STRAT-003-B-R1"}
    assert all(not stable_id.startswith("P3-STAGE-") for stable_id in ready_p3)

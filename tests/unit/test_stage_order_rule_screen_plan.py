from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def module():
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location(
        "rule_screen_plan", ROOT / "scripts/sync_stage_order_rule_screen_issues.py",
    )
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_terminal_screen_closes_with_revise_not_candidate_pass():
    script = module()
    raw = script.load_plan()
    assert raw["milestone_completed"]
    assert raw["runtime_receipts"]["successful_prepare"]["state"] == "succeeded"
    assert raw["runtime_receipts"]["resolve_score"]["state"] == "succeeded"
    assert raw["final_disposition"]["decision"] == "REVISE_TARGET_OR_PARK"
    assert raw["final_disposition"]["combined_candidate_broader_validation_authorized"] is False
    assert [(i["id"], i["status"]) for i in raw["items"]] == [
        ("P3-STAGE-007", "closed"), ("P3-STAGE-007-A", "closed"),
        ("P3-STAGE-007-B", "closed"), ("P3-STAGE-007-C", "closed"),
        ("P3-STAGE-007-D", "closed"),
    ]
    from sync_github_planning import work_items

    by_id = {i.stable_id: i for i in work_items(ROOT)}
    assert all(by_id[i["id"]].status == i["status"] for i in raw["items"])


@pytest.mark.parametrize("key", [
    "parameter_search", "arbitration_access", "permanent_blind_access", "official_labels",
    "daily_or_strategy", "trading",
])
def test_prepare_authority_cannot_silently_open_search_or_trading(key):
    script = module()
    raw = script.load_plan()
    raw["authorization"][key] = True
    with pytest.raises(ValueError, match="activation"):
        script.validate_plan(raw)


def test_prepare_and_seal_score_contracts_are_consumed_and_runtime_is_closed():
    script = module()
    raw = script.load_plan()
    assert raw["authorization"]["runtime_job_submission"] is False
    assert raw["authorization"]["new_market_data_read"] is False
    assert raw["completed_prepare_contract"]["phase"] == "prepare_only"
    assert raw["completed_prepare_contract"]["maximum_technical_retries"] == 1
    assert raw["completed_resolve_score_contract"]["phase"] == "seal_then_score"
    assert raw["completed_resolve_score_contract"]["maximum_jobs"] == 1
    assert raw["completed_resolve_score_contract"]["maximum_technical_retries"] == 0
    assert raw["completed_resolve_score_contract"]["case_count"] == 160
    assert raw["completed_resolve_score_contract"]["opinion_count"] == 320
    assert raw["completed_resolve_score_contract"]["new_market_data_read"] is False
    assert raw["completed_resolve_score_contract"]["parameter_search"] is False
    assert raw["runtime_contract"] is None
    assert raw["technical_retry"]["authorized"] is False
    assert raw["technical_retry"]["consumed"] is True
    assert raw["technical_retry"]["actual_retries"] == 1
    assert raw["preparation_checkpoint"]["source_isolation_verified"] is True
    assert raw["reference_checkpoint"]["state"] == "REVIEW_LEDGER_FROZEN"
    assert raw["reference_checkpoint"]["case_count"] == 160
    assert raw["reference_checkpoint"]["opinion_count"] == 320
    raw["runtime_contract"] = dict(raw["completed_resolve_score_contract"])
    with pytest.raises(ValueError, match="terminal_runtime_contract"):
        script.validate_plan(raw)


@pytest.mark.parametrize("key,value", [
    ("excluded_old_cohort_size", 160), ("report_year", 2025), ("candidate_count", 108),
    ("maximum_cases", 200), ("maximum_cases_per_economic_identity", 2),
    ("cross_bucket_backfill", True), ("representativeness_claimed", True),
    ("formal_population_confidence_claimed", True), ("result_dependent_replacement", True),
    ("minimum_definite_positives_per_class", 1), ("minimum_benchmark_precision", "0.50"),
])
def test_no_reuse_expansion_lower_gate_or_population_claim(key, value):
    script = module()
    raw = script.load_plan()
    raw["validation"][key] = value
    with pytest.raises(ValueError):
        script.validate_plan(raw)


@pytest.mark.parametrize("key,value", [
    ("maximum_new_jobs", 3), ("semantic_answer_retries", 1),
    ("automatic_supplement_rounds", 1), ("parameter_trials", 108),
])
def test_resources_are_bounded_before_result_access(key, value):
    script = module()
    raw = script.load_plan()
    raw["budgets"][key] = value
    with pytest.raises(ValueError, match="budget"):
        script.validate_plan(raw)


def test_terminal_completion_cannot_be_reframed_as_candidate_acceptance():
    script = module()
    raw = script.load_plan()
    raw["final_disposition"]["combined_candidate_broader_validation_authorized"] = True
    with pytest.raises(ValueError, match="final_disposition"):
        script.validate_plan(raw)


@pytest.mark.parametrize("key,value", [
    ("source_isolation_verified", False), ("source_membership_verified", False),
    ("old_cohort_size", 511), ("selected_case_count", 159),
    ("evidence_source_row_count", 9759), ("read_attempt_exit_code", 255),
])
def test_accepted_prepare_evidence_cannot_be_rewritten(key, value):
    script = module()
    raw = script.load_plan()
    raw["preparation_checkpoint"][key] = value
    with pytest.raises(ValueError, match="checkpoint_claim"):
        script.validate_plan(raw)


def test_technical_retry_cannot_be_expanded_or_reframed_as_semantic_result():
    script = module()
    raw = script.load_plan()
    raw["technical_retry"]["maximum_retries"] = 2
    with pytest.raises(ValueError, match="technical_retry"):
        script.validate_plan(raw)
    raw = script.load_plan()
    raw["technical_retry"]["semantic_result_exposed_before_failure"] = True
    with pytest.raises(ValueError, match="technical_retry"):
        script.validate_plan(raw)


def test_frozen_review_ledger_claim_cannot_be_rewritten():
    script = module()
    raw = script.load_plan()
    raw["reference_checkpoint"]["resolved_preview"]["STAGE_2"] = 47
    with pytest.raises(ValueError, match="reference_checkpoint"):
        script.validate_plan(raw)


def test_sampling_band_drift_is_rejected_before_activation():
    script = module()
    raw = script.load_plan()
    raw["validation"]["sampling_bands"]["efficiency_abs_max"] = "0.9"
    with pytest.raises(ValueError, match="validation_drift"):
        script.validate_plan(raw)


def test_governing_lld_is_consistent_with_closed_stage006():
    from sync_github_planning import chapter_rows

    owners, _ = chapter_rows(ROOT)
    for stable_id in ("P3-STAGE-006", *[f"P3-STAGE-006-{s}" for s in "ABCD"]):
        _, row = owners[stable_id]
        assert row["Acceptance evidence"].startswith("closed")

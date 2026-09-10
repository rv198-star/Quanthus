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


def test_review_complete_does_not_mean_milestone_complete():
    script = module()
    raw = script.load_plan()
    assert not raw["milestone_completed"]
    assert raw["runtime_receipts"] is None
    assert [(i["id"], i["status"]) for i in raw["items"]] == [
        ("P3-STAGE-007", "blocked"), ("P3-STAGE-007-A", "closed"),
        ("P3-STAGE-007-B", "blocked"), ("P3-STAGE-007-C", "blocked"),
        ("P3-STAGE-007-D", "blocked"),
    ]
    from sync_github_planning import work_items

    by_id = {i.stable_id: i for i in work_items(ROOT)}
    assert all(by_id[i["id"]].status == i["status"] for i in raw["items"])


@pytest.mark.parametrize("key", [
    "runtime_job_submission", "new_market_data_read", "parameter_search", "arbitration_access",
    "permanent_blind_access", "official_labels", "daily_or_strategy", "trading",
])
def test_meeting_cannot_silently_open_runtime_or_trading(key):
    script = module()
    raw = script.load_plan()
    raw["authorization"][key] = True
    with pytest.raises(ValueError, match="activation"):
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


def test_no_parent_pass_or_runtime_receipt_from_synthetic_rule_tests():
    script = module()
    raw = script.load_plan()
    raw["milestone_completed"] = True
    with pytest.raises(ValueError, match="unexecuted"):
        script.validate_plan(raw)


@pytest.mark.parametrize("key,value", [
    ("source_isolation_verified", True), ("source_membership_verified", True),
    ("old_cohort_digest", "sha256:" + "0" * 64), ("new_market_data_reads", 1),
    ("new_jobs_submitted", 1), ("read_attempt_exit_code", 0),
])
def test_component_tests_cannot_fill_missing_real_source_evidence(key, value):
    script = module()
    raw = script.load_plan()
    raw["preparation_checkpoint"][key] = value
    with pytest.raises(ValueError, match="checkpoint_claim"):
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

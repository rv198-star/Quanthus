from __future__ import annotations

import json

from quanthus.policy import find_project_root
from quanthus.structure_v2_inputs import digest

ROOT = find_project_root()
PATH = ROOT / "config/stage-learning/positive-instability-development-screen.v1.json"
CONFIG_DIGEST = "sha256:2ad1afc0c0ac1f9ba9822f1b34dc9f9a995e6c8783b7509e8c5189e897f4110a"


def load_config() -> dict:
    value = json.loads(PATH.read_text(encoding="utf-8"))
    assert digest(value) == CONFIG_DIGEST
    return value


def test_source_scope_stays_inside_development_and_keeps_2022_2025_closed() -> None:
    config = load_config()
    scope = config["source_scope"]
    assert scope["history_start"] == "2019-01-01"
    assert scope["development_report_start"] == "2020-01-01"
    assert scope["development_report_end"] == "2020-12-31"
    assert scope["evaluation_report_start"] == "2021-01-01"
    assert scope["evaluation_report_end"] == "2021-12-31"
    authority = config["authority"]
    assert authority["2022_2024_access"] is False
    assert authority["2025_access"] is False
    assert config["claim_ceiling"]["formal_blind_qualification"] is False


def test_third_identity_pool_excludes_every_prior_exposed_identity_surface() -> None:
    plan = load_config()["fresh_identity_plan"]
    assert plan["exclude_all_stage_007_excluded_identities"] is True
    assert plan["exclude_entire_stage_007_pool"] is True
    assert plan["exclude_entire_stage_008_fresh_pool"] is True
    assert plan["fresh_pool_size"] == 512
    assert plan["development_identity_capacity"] == 256
    assert plan["evaluation_identity_capacity"] == 256
    assert plan["cross_panel_identity_overlap_allowed"] is False
    assert plan["one_case_per_identity"] is True


def test_development_and_evaluation_sampling_are_morphology_blind_and_no_backfill() -> None:
    config = load_config()
    for key, expected, per_quarter in (
        ("development_selection", 128, 32),
        ("evaluation_selection", 160, 40),
    ):
        selection = config[key]
        assert selection["selection_uses_morphology"] is False
        assert selection["planned_identities"] == expected
        assert selection["identities_per_quarter"] == per_quarter
        assert selection["cross_quarter_backfill"] is False
        assert selection["identity_backfill"] is False
        assert selection["result_dependent_replacement"] is False


def test_quantile_threshold_basis_is_label_free_and_candidate_grid_is_exactly_27() -> None:
    config = load_config()
    basis = config["threshold_basis"]
    assert basis["labels_used_to_compute_quantiles"] is False
    assert basis["range_width_quantiles"] == ["0.50", "0.60", "0.70"]
    assert basis["positive_width_change_quantiles"] == ["0.50", "0.65", "0.80"]
    assert basis["absolute_directional_efficiency_quantiles"] == ["0.50", "0.40", "0.30"]
    assert basis["range_traversal_quantiles"] == ["0.50", "0.40", "0.30"]
    assert basis["counter_direction_excursion_quantiles"] == ["0.50", "0.60", "0.70"]
    assert basis["positive_width_change_basis_requires_value_gt_zero"] is True
    assert basis["counter_excursion_basis_excludes_unavailable_zero_net"] is True
    grid = config["candidate_grid"]
    assert grid["candidate_count"] == 27
    assert len(grid["shared_core_profiles"]) == 3
    assert len(grid["expansion_profiles"]) == 3
    assert len(grid["reversal_profiles"]) == 3
    assert grid["grid_expansion_after_result"] is False


def test_candidate_and_reference_boundaries_remain_sealed_until_scoring() -> None:
    config = load_config()
    review = config["reference_collection"]
    assert review["candidate_output_visible"] is False
    assert review["explicit_recent_derived_facts_visible"] is False
    assert review["future_rows_visible"] is False
    assert review["primary_opinion_commit_required_before_scoring"] is True
    assert review["independent_validation_claimed"] is False
    assert config["bindings"]["frozen_008_relocation_candidate"]["retuning_authorized"] is False


def test_result_budget_and_claim_ceiling_are_bounded() -> None:
    config = load_config()
    limits = config["limits"]
    assert limits["maximum_parameter_trials"] == 27
    assert limits["maximum_result_bearing_market_data_jobs"] == 2
    assert limits["automatic_supplement_rounds"] == 0
    assert limits["semantic_answer_retries"] == 0
    assert config["execution_sequence"] == [
        "D1_PREPARE_2020_DEVELOPMENT_AND_FREEZE_2021_EVALUATION_IDENTITY_PLAN",
        "D2_SEAL_DEVELOPMENT_REFERENCES_SCORE_27_AND_FREEZE_ONE_CANDIDATE",
        "D3_PREPARE_2021_EVALUATION_AFTER_CANDIDATE_FREEZE",
        "D4_SEAL_EVALUATION_REFERENCES_AND_SCORE_FROZEN_CANDIDATE",
    ]
    ceiling = config["claim_ceiling"]
    assert ceiling["development_only_task_disjoint_validation"] is True
    assert ceiling["formal_blind_qualification"] is False
    assert ceiling["runtime_fact_qualification"] is False
    assert ceiling["official_labels"] is False
    assert ceiling["daily_or_strategy"] is False
    assert ceiling["trading"] is False

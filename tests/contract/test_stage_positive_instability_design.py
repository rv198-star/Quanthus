from __future__ import annotations

import json

from quanthus.policy import find_project_root
from quanthus.structure_v2_inputs import digest

ROOT = find_project_root()
DESIGN = ROOT / "config/stage-learning/positive-instability-repair-design.v1.json"
DESIGN_DIGEST = "sha256:e5393eb71e7500966ad5e497a3918967c2a12a6aa4113b0db2e6888ef5aaddd6"


def load_design() -> dict:
    value = json.loads(DESIGN.read_text(encoding="utf-8"))
    assert digest(value) == DESIGN_DIGEST
    return value


def test_a_design_is_approved_but_runtime_and_data_stay_closed() -> None:
    design = load_design()
    assert design["design_status"] == "approved"
    assert design["delivery_gate"] == "blocked"
    assert design["delivery_sequence"] == {
        "P3-STAGE-009-A": "closed_design_approved",
        "P3-STAGE-009-B": "closed_engineering_accepted",
        "P3-STAGE-009-C": "closed_pass_diagnostic_conformance_only",
        "P3-STAGE-009-D": "ready_preregistration_only_no_data_access",
    }
    assert design["authority"] == {
        "design_and_review": True,
        "implementation": False,
        "runtime_job_submission": False,
        "market_data_read": False,
        "parameter_search": False,
        "2022_2024_access": False,
        "2025_access": False,
        "official_labels": False,
        "daily_or_strategy": False,
        "trading": False,
    }


def test_positive_instability_is_small_explainable_channel_or() -> None:
    model = load_design()["positive_evidence_model"]
    assert model["composition"] == "small_explainable_or_of_positive_channels"
    assert set(model["channels"]) == {
        "expanding_disorder_20",
        "broad_reversal_disorder_20",
    }
    assert model["resolution"] == {
        "TRUE": "at_least_one_positive_channel_true",
        "FALSE": "all_positive_channels_false",
        "UNKNOWN": "no_positive_channel_true_and_at_least_one_channel_unknown",
    }
    assert "opaque_weighted_composite" in model["forbidden_forms"]
    assert "residual_stage_0" in model["forbidden_forms"]
    assert "center_shift_alone" in model["forbidden_forms"]


def test_center_shift_has_no_positive_truth_authority() -> None:
    design = load_design()
    target = design["target"]
    policy = design["center_shift_policy"]
    assert target["center_shift_independent_positive_authority"] is False
    assert policy["truth_function_role"] == "none"
    assert policy["may_change_structural_instability_truth"] is False
    assert policy["strong_contraction_plus_large_center_shift_default"] == (
        "not_stage_0_without_independent_positive_disorder_evidence"
    )


def test_existing_facts_are_reused_and_no_new_fact_is_authorized() -> None:
    scope = load_design()["fact_scope"]
    assert scope["new_threshold_free_fact_required"] is False
    assert set(scope["reuse"]) == {
        "structure.geometry.range-width@20",
        "structure.geometry.width-change@20_vs_prior20",
        "structure.geometry.range-center-shift@20_vs_prior20",
        "structure.geometry.directional-efficiency@20",
        "structure.geometry.range-traversal@20",
        "structure.geometry.counter-direction-excursion-share@20",
    }


def test_zero_net_and_contracting_broad_reversal_are_explicitly_supported() -> None:
    channel = load_design()["positive_evidence_model"]["channels"]["broad_reversal_disorder_20"]
    assert "low traversal" in channel["zero_net_handling"]
    assert "low directional efficiency" in channel["zero_net_handling"]
    cases = {item["case"]: item["expected"] for item in load_design()["adversarial_cases"]}
    assert cases["strong_contraction_large_center_shift"] == (
        "center_shift_cannot_create_instability"
    )
    assert cases["strong_contraction_broad_two_sided_reversal"] == (
        "broad_reversal_channel_may_create_instability_without_expansion"
    )
    assert cases["symmetric_broad_oscillation_zero_net"] == (
        "broad_reversal_instability_can_be_true_with_counter_excursion_unavailable"
    )


def test_relocation_order_and_stage2_boundaries_remain_exact() -> None:
    interaction = load_design()["interaction_with_relocation_and_order"]
    assert interaction["stage_0_requires"] == {
        "structural_instability_20": "TRUE",
        "coherent_directional_relocation_20": "FALSE",
        "orderly_consolidation_20": "FALSE",
    }
    assert interaction["instability_true_and_relocation_true"] == (
        "outside_stage_0_keep_mixed_directional_instability_reason"
    )
    assert interaction["instability_true_and_order_true"] == "AMBIGUOUS_CONFLICT"
    target = load_design()["target"]
    assert target["stage_2_changes_authorized"] is False
    assert target["relocation_guard_retuning_authorized"] is False


def test_consumed_008_evidence_cannot_select_or_qualify_009() -> None:
    policy = load_design()["threshold_policy"]
    assert policy["thresholds_frozen_by_009_a"] is False
    assert policy["consumed_008_evaluation_may_select_thresholds"] is False
    assert policy["consumed_008_evaluation_may_qualify_009"] is False
    assert policy["post_hoc_counterfactual_may_set_thresholds"] is False
    assert policy["future_threshold_search_requires_separate_preregistration"] is True


def test_b_implementation_checkpoint_opens_only_c_diagnostics() -> None:
    checkpoint = load_design()["implementation_checkpoint"]
    assert checkpoint["work_item"] == "P3-STAGE-009-B"
    assert checkpoint["state"] == "ENGINEERING_IMPLEMENTATION_ACCEPTED"
    assert checkpoint["contract_digest"] == (
        "sha256:2160cdb23ed43acb248bafd7bbdace20ff2dd936ab9c8ff5f3b3cda3d74cda7b"
    )
    assert checkpoint["numeric_thresholds_frozen"] is False
    assert checkpoint["market_data_reads"] == 0
    assert checkpoint["runtime_jobs"] == 0
    assert checkpoint["parameter_trials"] == 0
    assert checkpoint["stage_2_changed"] is False
    assert checkpoint["relocation_guard_retuned"] is False
    assert checkpoint["next_consumed_evidence_diagnostic_authorized"] is True
    for key in ("contract", "implementation", "unit_contract", "acceptance"):
        assert (ROOT / checkpoint[key]).is_file()


def test_c_conformance_checkpoint_opens_only_d_preregistration() -> None:
    checkpoint = load_design()["conformance_checkpoint"]
    assert checkpoint["work_item"] == "P3-STAGE-009-C"
    assert checkpoint["state"] == "PASS_DIAGNOSTIC_CONFORMANCE_ONLY"
    assert checkpoint["case_count"] == 95
    assert checkpoint["center_shift_only_true_stage0_count"] == 2
    assert checkpoint["center_shift_only_false_positive_count"] == 8
    assert checkpoint["definite_stage0_count"] == 15
    assert checkpoint["definite_stage0_positive_width_change_count"] == 13
    assert checkpoint["definite_stage0_negative_width_change_count"] == 2
    assert checkpoint["negative_width_change_cases_match_broad_reversal_diagnostic_group"] is True
    assert checkpoint["numeric_thresholds_selected"] is False
    assert checkpoint["parameter_trials"] == 0
    assert checkpoint["new_market_data_reads"] == 0
    assert checkpoint["runtime_jobs"] == 0
    assert checkpoint["qualification_claim"] is False
    assert checkpoint["next_fresh_validation_preregistration_authorized"] is True
    assert checkpoint["next_fresh_validation_data_access_authorized"] is False
    for key in ("contract", "result", "runner", "acceptance"):
        assert (ROOT / checkpoint[key]).is_file()


def test_review_provenance_and_routes_are_bound() -> None:
    review = load_design()["review"]
    assert review["mode"] == "same_origin_four_responsibility_adversarial_review"
    assert review["independent_expert_validation"] is False
    assert review["directional_blockers_open"] == 0
    for relative in (
        "docs/design/stage-positive-instability-proposition-repair.md",
        review["review_path"],
        "docs/work-items/stage-positive-instability-proposition-repair.md",
    ):
        assert (ROOT / relative).is_file()

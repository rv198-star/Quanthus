from __future__ import annotations

import json

from quanthus.policy import find_project_root
from quanthus.structure_v2_inputs import digest

ROOT = find_project_root()
DIAGNOSTIC = ROOT / "config/stage-learning/orderly-consolidation-boundary-diagnostic.v1.json"
DESIGN = ROOT / "config/stage-learning/orderly-consolidation-boundary-repair-design.v1.json"
DELIVERY = ROOT / "config/stage-learning/orderly-consolidation-boundary-repair-delivery.v1.json"
PROJECTION = ROOT / "config/stage-learning/orderly-consolidation-projection.v1.json"
CONFORMANCE = ROOT / (
    "config/stage-learning/orderly-consolidation-boundary-conformance-result.v1.json"
)
PREREG = ROOT / "config/stage-learning/orderly-consolidation-joint-development-screen.v1.json"
DIAGNOSTIC_DIGEST = "sha256:d34e9f7ad8a32969e28d2a1d5245caa089a52fb8a7891a8b8f03e69ce3fc3d1c"
DESIGN_DIGEST = "sha256:5d1e1ab3298df676829aff34a9c3b6117fa56053fb684f2c630ca04fbd380353"
PROJECTION_DIGEST = "sha256:11569752a0a3ae7c5a6c3f2ecf07b51710442c0f2445a848800203df3cda146c"
CONFORMANCE_DIGEST = "sha256:64741982652b0323a14dcd7c87c1486d7b8998fa085d5c190d3ca9a28a0c91d1"
PREREG_DIGEST = "sha256:c9b5eff892f3d731170afac052e3810543f7444f5b6025bdef24c1ebfa4c213e"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_consumed_p010_diagnostic_is_bounded_and_selects_no_threshold() -> None:
    value = load(DIAGNOSTIC)
    assert digest(value) == DIAGNOSTIC_DIGEST
    assert value["reference_counts"] == {
        "STAGE_0": 54,
        "STAGE_2": 16,
        "NEITHER": 72,
        "AMBIGUOUS": 18,
    }
    assert value["legacy_stage2"]["true_positive"] == 6
    assert value["legacy_stage2"]["false_negative"] == 10
    assert value["legacy_stage2"]["false_positive"] == 2
    assert value["stage2_reference_gate_failure_counts"] == {
        "efficiency20_abs_gt_0_33": 0,
        "range_width20_gt_0_18": 0,
        "width_change20_gt_minus_0_20": 0,
        "center_shift20_abs_gt_0_04": 10,
    }
    assert (
        value["findings"][
            "generic_low_churn_or_low_counter_excursion_guard_is_rejected_by_consumed_evidence"
        ]
        is True
    )
    assert value["authority"]["threshold_selection"] is False
    assert value["authority"]["market_data_read"] is False
    assert value["authority"]["preserved_2021_evaluation_read"] is False


def test_a_design_removes_center_shift_truth_authority_without_creating_a_residual_stage2() -> None:
    value = load(DESIGN)
    assert digest(value) == DESIGN_DIGEST
    assert value["state"] == "APPROVED_IMMUTABLE"
    assert value["root_cause_diagnostic"]["digest"] == DIAGNOSTIC_DIGEST
    assert value["truth_authority"]["center_shift20_abs"] is False
    assert value["truth_authority"]["path_turnover20"] is False
    assert value["truth_authority"]["range_traversal20"] == "relocation_guard_only"
    assert (
        value["truth_authority"]["counter_direction_excursion_share20"]
        == "relocation_guard_only"
    )
    target = value["semantic_target"]["orderly_consolidation_20"]
    assert "low_directional_efficiency" in target
    assert "materially_narrow_current_range" in target
    assert "material_range_contraction" in target
    assert "coherent_directional_relocation_20=FALSE" in target
    assert value["semantic_target"]["conflict"].startswith("positive orderly_consolidation")


def test_future_profile_surface_is_bundled_and_capped_before_fresh_data() -> None:
    value = load(DESIGN)
    assert value["orderly_consolidation_profiles"] == [
        {
            "id": "order-lenient",
            "efficiency_max_quantile": "0.70",
            "range_width_max_quantile": "0.40",
            "positive_contraction_min_quantile": "0.30",
        },
        {
            "id": "order-balanced",
            "efficiency_max_quantile": "0.60",
            "range_width_max_quantile": "0.30",
            "positive_contraction_min_quantile": "0.50",
        },
        {
            "id": "order-strict",
            "efficiency_max_quantile": "0.50",
            "range_width_max_quantile": "0.20",
            "positive_contraction_min_quantile": "0.70",
        },
    ]
    joint = value["future_joint_development_shape"]
    assert joint["p010_stage0_profile_count"] == 9
    assert joint["stage2_profile_count"] == 3
    assert joint["maximum_joint_pairs"] == 27
    assert joint["grid_expansion_after_result"] is False
    assert joint["numeric_thresholds_selected_now"] is False
    assert joint["candidate_pair_selected_now"] is False


def test_b_projection_contract_is_accepted_without_opening_data_authority() -> None:
    value = load(DELIVERY)
    assert value["diagnostic"]["digest"] == DIAGNOSTIC_DIGEST
    assert value["design"]["digest"] == DESIGN_DIGEST
    assert digest(load(PROJECTION)) == PROJECTION_DIGEST
    checkpoint = value["implementation_checkpoint"]
    assert checkpoint["state"] == "ENGINEERING_IMPLEMENTATION_ACCEPTED"
    assert checkpoint["contract_digest"] == PROJECTION_DIGEST
    assert checkpoint["numeric_thresholds_selected"] is False
    assert checkpoint["market_data_reads"] == 0
    assert checkpoint["runtime_jobs"] == 0
    for key in ("contract", "implementation", "unit_contract", "acceptance"):
        assert (ROOT / checkpoint[key]).is_file()
    conformance = value["conformance_checkpoint"]
    assert conformance["state"] == "PASS_TOPOLOGY_CONFORMANCE_ONLY"
    assert conformance["result_digest"] == CONFORMANCE_DIGEST
    assert digest(load(CONFORMANCE)) == CONFORMANCE_DIGEST
    assert load(CONFORMANCE)["failed_checks"] == []
    assert conformance["numeric_thresholds_selected"] is False
    assert conformance["candidate_pairs_scored"] == 0
    assert conformance["new_market_data_reads"] == 0
    assert conformance["runtime_jobs"] == 0
    assert conformance["preserved_2021_evaluation_read"] is False
    prereg = load(PREREG)
    assert digest(prereg) == PREREG_DIGEST
    prereg_checkpoint = value["validation_preregistration"]
    assert prereg_checkpoint["state"] == "PREREGISTERED_IMPLEMENTATION_READY"
    assert prereg_checkpoint["contract_digest"] == PREREG_DIGEST
    assert prereg_checkpoint["development_year"] == 2018
    assert prereg_checkpoint["development_case_target"] == 192
    assert prereg_checkpoint["candidate_pair_count"] == 27
    assert prereg_checkpoint["preserved_evaluation_data_read"] is False
    assert prereg_checkpoint["data_access_authorized_now"] is False
    assert prereg_checkpoint["runtime_job_submission_authorized_now"] is False
    assert value["delivery_sequence"] == {
        "P3-STAGE-011-A": "closed_design_approved",
        "P3-STAGE-011-B": "closed_engineering_accepted",
        "P3-STAGE-011-C": "closed_pass_topology_conformance_only",
        "P3-STAGE-011-D": "preregistered_ready_d1_engineering",
    }
    assert value["authority"] == {
        "implementation": True,
        "synthetic_conformance": False,
        "consumed_evidence_topology_diagnostic": False,
        "preregistration_design": False,
        "market_data_read": False,
        "runtime_job_submission": False,
        "parameter_search": False,
        "candidate_selection": False,
        "preserved_2021_evaluation_read": False,
        "2022_2024_access": False,
        "2025_access": False,
        "official_labels": False,
        "daily_or_strategy": False,
        "trading": False,
    }
    for path in (
        ROOT / "docs/design/stage-orderly-consolidation-boundary-repair.md",
        ROOT / "docs/reviews/stage-orderly-consolidation-boundary-repair-review-20260913.md",
        ROOT / "docs/work-items/stage-orderly-consolidation-boundary-repair.md",
        ROOT / "docs/acceptance/stage-orderly-consolidation-boundary-d-preregistration-20260913.md",
    ):
        assert path.is_file()


def test_d_preregistration_is_fresh_blind_bounded_and_preserves_prior_gates() -> None:
    value = load(PREREG)
    source = value["source_isolation"]
    assert source["candidate_cohort_date"] == "2017-12-29"
    assert source["development_history_start"] == "2017-09-01"
    assert source["development_report_start"] == "2018-01-01"
    assert source["development_report_end"] == "2018-12-31"
    assert source["fresh_pool_size"] == 512
    assert source["development_identity_capacity"] == 256
    for key in (
        "exclude_stage007_exposed_pool",
        "exclude_stage008_entire_fresh_pool",
        "exclude_stage009_entire_512_identity_fresh_pool",
        "exclude_stage010_entire_512_identity_fresh_pool",
    ):
        assert source[key] is True
    assert source["development_overlap_with_preserved_evaluation"] == 0
    selection = value["development_selection"]
    assert selection["planned_identities"] == 192
    assert selection["quarter_caps"] == {"1": 48, "2": 48, "3": 48, "4": 48}
    assert selection["selection_uses_morphology"] is False
    assert selection["cross_quarter_backfill"] is False
    assert selection["identity_backfill"] is False
    surface = value["joint_candidate_surface"]
    assert surface["stage0_profile_count"] == 9
    assert surface["stage2_profile_count"] == 3
    assert surface["pair_count"] == 27
    assert surface["grid_expansion_after_result"] is False
    gates = value["development_gates"]
    assert gates["minimum_definite_stage_0_positive_references"] == 18
    assert gates["minimum_definite_stage_2_positive_references"] == 20
    assert gates["stage_0_minimum_precision"] == "0.78"
    assert gates["stage_0_minimum_positive_reference_coverage"] == "0.70"
    assert gates["stage_0_maximum_definite_negative_false_positive_rate"] == "0.10"
    assert gates["pair_overlap_count_must_equal"] == 0
    assert value["preserved_evaluation"]["data_read_before_pair_freeze"] is False
    assert value["budgets"] == {
        "maximum_result_bearing_market_data_jobs": 2,
        "maximum_development_candidate_pairs": 27,
        "semantic_retries": 0,
        "automatic_supplement_rounds": 0,
        "automatic_technical_retries": 0,
    }

from __future__ import annotations

import json

from quanthus.policy import find_project_root
from quanthus.structure_v2_inputs import digest

ROOT = find_project_root()
DIAGNOSTIC = ROOT / "config/stage-learning/orderly-consolidation-boundary-diagnostic.v1.json"
DESIGN = ROOT / "config/stage-learning/orderly-consolidation-boundary-repair-design.v1.json"
DELIVERY = ROOT / "config/stage-learning/orderly-consolidation-boundary-repair-delivery.v1.json"
DIAGNOSTIC_DIGEST = "sha256:d34e9f7ad8a32969e28d2a1d5245caa089a52fb8a7891a8b8f03e69ce3fc3d1c"
DESIGN_DIGEST = "sha256:5d1e1ab3298df676829aff34a9c3b6117fa56053fb684f2c630ca04fbd380353"


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


def test_delivery_opens_only_b_implementation_and_synthetic_work() -> None:
    value = load(DELIVERY)
    assert value["diagnostic"]["digest"] == DIAGNOSTIC_DIGEST
    assert value["design"]["digest"] == DESIGN_DIGEST
    assert value["delivery_sequence"] == {
        "P3-STAGE-011-A": "closed_design_approved",
        "P3-STAGE-011-B": "ready_implementation_and_synthetic_only",
        "P3-STAGE-011-C": "blocked_pending_b_acceptance",
        "P3-STAGE-011-D": "blocked_pending_c_and_fresh_preregistration",
    }
    assert value["authority"] == {
        "implementation": True,
        "synthetic_conformance": True,
        "consumed_evidence_topology_diagnostic": False,
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
    ):
        assert path.is_file()

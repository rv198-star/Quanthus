from __future__ import annotations

import json
from pathlib import Path

from quanthus.policy import find_project_root
from quanthus.structure_v2_inputs import digest

ROOT = find_project_root()
INVENTORY = ROOT / "config/stage-learning/stage-atomic-proposition-freshness-inventory.v1.json"
SCREEN = ROOT / "config/stage-learning/stage-atomic-proposition-development-screen.v1.json"
REVIEW = ROOT / "config/stage-learning/stage-atomic-proposition-f-preregistration-review.v1.json"
INVENTORY_DIGEST = "sha256:689da68cfc6aadb5cd6468df952bfb0f7aded716a0150333ce8e23f34387fa5a"
SCREEN_DIGEST = "sha256:24aacc70ef7ffa3bbfd0e28e8aa33fd91ba51bbd65da6c13640926215987c54d"
REVIEW_DIGEST = "sha256:df5c906effdc863477f6858089e68eadaad4f839d54e8069874fa3132c0c712b"


def load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_freshness_inventory_is_frozen_before_market_read() -> None:
    value = load(INVENTORY)
    assert digest(value) == INVENTORY_DIGEST
    assert value["state"] == "FROZEN_BEFORE_FRESH_MARKET_READ"
    fresh = value["selected_fresh_surface"]
    assert fresh["development_year"] == 2016
    assert fresh["candidate_cohort_date"] == "2015-12-31"
    assert fresh["development_history_start"] == "2015-09-01"
    assert fresh["development_report_start"] == "2016-01-01"
    assert fresh["development_report_end"] == "2016-12-31"
    assert fresh["time_disjoint_from_p008_p011_result_bearing_surfaces"] is True
    assert fresh["fresh_market_data_read_during_inventory"] is False
    assert value["closed_partitions"]["2022_2024_access"] is False
    assert value["closed_partitions"]["2025_access"] is False
    assert value["closed_partitions"]["preserved_2021_access_before_exact_freeze"] is False


def test_preregistration_keeps_review_candidate_and_envelope_blind() -> None:
    value = load(SCREEN)
    assert digest(value) == SCREEN_DIGEST
    assert value["state"] == "PREREGISTERED_BEFORE_2016_MARKET_READ"
    assert value["bindings"]["freshness_inventory"]["digest"] == INVENTORY_DIGEST
    blind = value["review_blinding"]
    assert blind["candidate_output_visible"] is False
    assert blind["threshold_basis_visible"] is False
    assert blind["registered_envelope_measurement_visible"] is False
    assert blind["raw_21_close_primary_path_visible"] is True
    assert blind["future_rows_visible"] is False
    assert blind["atomic_truths"] == [
        "structural_instability_20",
        "coherent_directional_relocation_20",
        "orderly_consolidation_20",
    ]


def test_atomic_candidate_surfaces_are_finite_and_independent() -> None:
    value = load(SCREEN)
    instability = value["instability_candidate_surface"]
    assert instability["selectable_candidate_count"] == 9
    assert instability["candidate_grid_expansion_after_result"] is False
    fixed = instability["fixed_additive_channel"]
    assert fixed["minimum_resolved_escape_count"] == 3
    assert fixed["resolved_escape_both_sides_required"] is True
    assert fixed["minimum_resolved_escape_side_alternation_count"] == 2

    relocation = value["relocation_surface"]
    assert relocation["candidate_count"] == 1
    assert relocation["threshold_retune"] is False
    assert relocation["parameter_search"] is False
    assert relocation["instability_truth_can_coexist"] is True

    order = value["order_candidate_surface"]
    assert order["selectable_profile_count"] == 3
    assert order["relocation_veto_inside_atomic_order_truth"] is False
    assert order["center_shift_truth_authority"] is False

    selection = value["independent_selection"]
    assert selection["joint_pair_ranking_forbidden"] is True
    assert selection["freeze_at_most_one_instability_candidate"] is True
    assert selection["freeze_at_most_one_order_profile"] is True
    assert selection["relocation_is_fixed"] is True


def test_projection_gate_preserves_mixed_directional_instability() -> None:
    value = load(SCREEN)
    gate = value["projection_compatibility_gate"]
    resolver = gate["resolver"]
    assert resolver["stage0"] == "instability_TRUE AND relocation_FALSE AND order_FALSE"
    assert resolver["stage2"] == "instability_FALSE AND relocation_FALSE AND order_TRUE"
    assert "NEITHER" in resolver["mixed_directional_instability"]
    assert resolver["positive_order_with_instability_or_relocation"] == "AMBIGUOUS_CONFLICT"
    assert gate["maximum_prediction_ambiguous_conflict_rate"] == "0.05"
    assert gate["mixed_directional_instability_rate_is_reported_not_gated"] is True
    assert gate["no_joint_reselection_after_compatibility_result"] is True


def test_preserved_2021_and_result_budgets_are_closed_before_freeze() -> None:
    value = load(SCREEN)
    preserved = value["preserved_evaluation"]
    assert preserved["identity_count"] == 160
    assert preserved["identity_digest"] == (
        "sha256:8bbbd7ca7110d06bfe07db6c2f555cc093b0c1a259aa666e359a637f9157b1e8"
    )
    assert preserved["plan_digest"] == (
        "sha256:2800777b5941892a73cf022ddd9375780f2a4f63985f1ce0c1eae9b3668169a7"
    )
    assert preserved["data_read_before_exact_instability_order_projection_freeze"] is False
    assert value["budgets"] == {
        "maximum_result_bearing_market_data_jobs": 2,
        "maximum_selectable_instability_candidates": 9,
        "maximum_selectable_order_profiles": 3,
        "relocation_candidates": 1,
        "joint_pair_search_count": 0,
        "semantic_retries": 0,
        "automatic_supplement_rounds": 0,
        "automatic_technical_retries": 0,
    }
    authority = value["authority"]
    assert authority["preserved_2021_evaluation_read_before_exact_freeze"] is False
    assert authority["2017_2021_result_bearing_access_in_development"] is False
    assert authority["2022_2024_access"] is False
    assert authority["2025_access"] is False


def test_preregistration_review_passes_without_opening_data() -> None:
    value = load(REVIEW)
    assert digest(value) == REVIEW_DIGEST
    assert value["state"] == "PASS_PREREGISTRATION_REVIEW"
    assert value["reviewed"]["freshness_inventory_digest"] == INVENTORY_DIGEST
    assert value["reviewed"]["preregistration_digest"] == SCREEN_DIGEST
    assert all(item["verdict"] == "PASS" for item in value["perspectives"].values())
    resolved = value["resolved_decisions"]
    assert resolved["joint_pair_search"] == "FORBIDDEN"
    assert resolved["preserved_2021_before_projection_freeze"] == "FORBIDDEN"
    assert value["authority"]["implementation_next"] is True
    assert value["authority"]["fresh_market_data_read"] is False
    assert value["authority"]["runtime_job"] is False

from __future__ import annotations

import json

from quanthus.policy import find_project_root
from quanthus.structure_v2_inputs import digest

ROOT = find_project_root()
DESIGN = ROOT / "config/stage-learning/positive-instability-quantitative-projection-design.v1.json"
DELIVERY = ROOT / (
    "config/stage-learning/positive-instability-quantitative-projection-delivery.v1.json"
)
DESIGN_DIGEST = "sha256:b69ba0f9c22569c521b43c29efa252770d1b1b6e8630f66522cb61f60e306b62"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_a_design_is_immutable_and_separate_from_delivery_state() -> None:
    design = load(DESIGN)
    delivery = load(DELIVERY)
    assert digest(design) == DESIGN_DIGEST
    assert design["design_status"] == "approved"
    assert design["projection_model"]["shared_core_forbidden"] is True
    assert delivery["design"] == {
        "path": "config/stage-learning/positive-instability-quantitative-projection-design.v1.json",
        "digest": DESIGN_DIGEST,
        "state": "APPROVED_IMMUTABLE",
    }


def test_expanding_and_broad_channels_are_quantitatively_separated() -> None:
    design = load(DESIGN)
    model = design["projection_model"]
    expanding = model["expanding_disorder_20"]
    broad = model["broad_reversal_disorder_20"]
    assert "broad_reversal_range_width_floor" in expanding["does_not_require"]
    assert "low_directional_efficiency_as_mandatory_gate" in expanding["does_not_require"]
    assert set(expanding["any_path_disorder"]["inputs"]) == {
        "low_directional_efficiency_expansion",
        "low_range_traversal_expansion",
        "high_counter_direction_excursion_expansion",
    }
    assert broad["truth_form"] == (
        "current_range_width_material_broad AND at_least_two_of_three_path_disorder"
    )
    assert "low_directional_efficiency_as_single_mandatory_gate" in broad["does_not_require"]


def test_absolute_width_gain_is_projection_local_not_new_fact() -> None:
    measurement = load(DESIGN)["derived_measurements"]["absolute_range_width_gain"]
    assert measurement["source_facts"] == [
        "structure.geometry.range-width@20",
        "structure.geometry.width-change@20_vs_prior20",
    ]
    assert measurement["registration"] == (
        "projection-local deterministic derived measurement, not a new catalog Fact"
    )


def test_b_checkpoint_contains_no_threshold_search_or_data_read() -> None:
    checkpoint = load(DELIVERY)["implementation_checkpoint"]
    assert checkpoint["state"] == "ENGINEERING_IMPLEMENTATION_ACCEPTED"
    assert checkpoint["contract_digest"] == (
        "sha256:10dec4e9a63d1d1277dfd7e345d1f7b9c65cc27bee0bf05671a86c1277171cee"
    )
    assert checkpoint["numeric_threshold_values_frozen"] is False
    assert checkpoint["market_data_reads"] == 0
    assert checkpoint["runtime_jobs"] == 0
    assert checkpoint["parameter_trials"] == 0
    for key in ("contract", "implementation", "unit_contract", "acceptance"):
        assert (ROOT / checkpoint[key]).is_file()


def test_c_closes_topology_only_and_keeps_evaluation_unread() -> None:
    checkpoint = load(DELIVERY)["conformance_checkpoint"]
    assert checkpoint["state"] == "PASS_TOPOLOGY_CONFORMANCE_ONLY"
    assert checkpoint["old_nearest_candidate_false_negative_count"] == 15
    assert checkpoint["numeric_thresholds_selected"] is False
    assert checkpoint["parameter_trials"] == 0
    assert checkpoint["new_market_data_reads"] == 0
    assert checkpoint["runtime_jobs"] == 0
    assert checkpoint["preserved_2021_evaluation_read"] is False
    result = load(ROOT / checkpoint["result"])
    assert digest(result) == checkpoint["result_digest"]
    assert not result["failed_checks"]


def test_d_preregistration_freezes_nine_candidates_before_data_read() -> None:
    checkpoint = load(DELIVERY)["validation_preregistration"]
    assert checkpoint["state"] == "PREREGISTERED_IMPLEMENTATION_BEFORE_DATA_READ"
    assert checkpoint["contract_digest"] == (
        "sha256:586dda1fe987842cee2f56084697e994bfb44781c998b8a093c3e3678565a6e2"
    )
    assert checkpoint["development_year"] == 2019
    assert checkpoint["development_case_target"] == 160
    assert checkpoint["candidate_count"] == 9
    assert checkpoint["preserved_evaluation_year"] == 2021
    assert checkpoint["preserved_evaluation_identity_count"] == 160
    assert checkpoint["preserved_evaluation_data_read"] is False
    assert checkpoint["data_access_authorized_now"] is False
    assert checkpoint["runtime_job_submission_authorized_now"] is False
    assert checkpoint["2022_2024_access"] is False
    assert checkpoint["2025_access"] is False
    for key in ("contract", "acceptance"):
        assert (ROOT / checkpoint[key]).is_file()


def test_current_authority_is_implementation_only() -> None:
    authority = load(DELIVERY)["authority"]
    assert authority == {
        "implementation": True,
        "runtime_job_submission": False,
        "market_data_read": False,
        "parameter_search": False,
        "preserved_2021_evaluation_read": False,
        "2022_2024_access": False,
        "2025_access": False,
        "official_labels": False,
        "daily_or_strategy": False,
        "trading": False,
    }

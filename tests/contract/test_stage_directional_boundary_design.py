from __future__ import annotations

import json

from quanthus.policy import find_project_root
from quanthus.structure_v2_inputs import digest

ROOT = find_project_root()
DESIGN = ROOT / "config/stage-learning/directional-boundary-repair-design.v1.json"
DESIGN_DIGEST = "sha256:e347e44104d79e06e354f0eaeb049d9f9fbfa19b2d54aeec48002e6387a3d6d1"


def load_design() -> dict:
    value = json.loads(DESIGN.read_text(encoding="utf-8"))
    assert digest(value) == DESIGN_DIGEST
    return value


def test_design_is_approved_and_only_preregistered_d_runtime_is_open() -> None:
    design = load_design()
    assert design["design_status"] == "approved"
    assert design["delivery_gate"] == "open"
    assert design["authority"] == {
        "design_and_review": True,
        "production_fact_implementation": False,
        "reference_protocol_implementation": False,
        "runtime_job_submission": True,
        "market_data_read": True,
        "parameter_search": True,
        "official_labels": False,
        "daily_or_strategy": False,
        "trading": False,
    }
    activation = design["development_screen_activation"]
    assert activation["state"] == "PREREGISTERED_AND_AUTHORIZED"
    assert activation["maximum_result_bearing_jobs"] == 4
    assert activation["maximum_parameter_trials"] == 27
    assert activation["development_data_years"] == [2019, 2020, 2021]
    assert activation["evaluation_market_data_before_candidate_freeze"] is False
    assert activation["2022_2024_access"] is False
    assert activation["2025_access"] is False
    assert activation["stage_2_threshold_research"] is False
    assert (ROOT / activation["contract"]).is_file()


def test_stage0_is_not_residual_and_directional_relocation_is_a_guard() -> None:
    semantic = load_design()["semantic_decomposition"]
    assert semantic["atomic_primary_propositions"] == [
        "structural_instability_20",
        "coherent_directional_relocation_20",
        "orderly_consolidation_20",
    ]
    assert semantic["stage_0_requires"] == {
        "structural_instability_20": "TRUE",
        "coherent_directional_relocation_20": "FALSE",
        "orderly_consolidation_20": "FALSE",
    }
    assert semantic["directional_relocation_is_a_stage_0_guard"] is True
    assert semantic["directional_relocation_is_not_automatically_stage_12"] is True
    precedence = semantic["role_level_resolution_precedence"]
    assert any(
        "NEITHER when coherent_directional_relocation_20 is TRUE" in item
        for item in precedence
    )
    assert semantic["cross_role_resolution"].startswith(
        "if the two semantic roles do not produce the same primary status"
    )
    assert semantic["residual_stage_0_forbidden"] is True
    assert semantic["top_1_force_forbidden"] is True


def test_fact_design_is_minimal_threshold_free_and_does_not_duplicate_direction_facts() -> None:
    facts = load_design()["fact_design"]
    assert set(facts["proposed_threshold_free_facts"]) == {
        "structure.geometry.range-traversal",
        "structure.geometry.counter-direction-excursion-share",
    }
    assert "structure.geometry.window-return@20" in facts["reuse_without_duplication"]
    assert "structure.geometry.directional-efficiency@20" in facts["reuse_without_duplication"]
    assert facts["stage_thresholds_in_fact_identity"] is False
    assert facts["facts_emit_stage_labels"] is False
    traversal = facts["proposed_threshold_free_facts"]["structure.geometry.range-traversal"]
    counter = facts["proposed_threshold_free_facts"][
        "structure.geometry.counter-direction-excursion-share"
    ]
    assert traversal["bounds"] == ["0", "1"]
    assert traversal["zero_close_range_value"] == "0"
    assert counter["bounds"] == ["0", "1"]
    assert counter["zero_net_displacement"] == "unavailable:zero_net_direction"
    assert facts["not_promoted_in_v1"]["daily_sign_reversal_rate"].startswith(
        "Stage-007 diagnostic showed little separation"
    )


def test_range_traversal_cannot_become_a_single_direction_veto() -> None:
    projection = load_design()["projection_design"]
    assert projection["center_shift_alone_cannot_assert_stage_0"] is True
    assert projection["range_traversal_alone_cannot_assert_relocation"] is True
    assert (
        projection[
            "counter_direction_excursion_must_be_considered_before_a_relocation_guard_can_be_frozen"
        ]
        is True
    )
    assert projection["no_stage_0_thresholds_are_frozen_by_this_design"] is True
    assert projection["stage_2_rule_changes_authorized"] is False


def test_recent_packet_cannot_mutate_primary() -> None:
    protocol = load_design()["reference_protocol_v2"]
    assert protocol["primary_then_recent_two_phase"] is True
    assert protocol["primary_packet"]["explicit_10_session_derived_facts_visible"] is False
    assert protocol["primary_packet"]["candidate_output_visible"] is False
    assert protocol["primary_packet"]["future_rows_visible"] is False
    assert protocol["primary_commit_required_before_recent_packet"] is True
    assert protocol["primary_commit_digest_required"] is True
    assert protocol["recent_packet"]["may_modify_primary_opinion"] is False
    assert protocol["resolver_uses_recent_to_change_primary_status"] is False
    assert protocol["unknown_primary_reason_may_cite_explicit_recent_fact"] is False
    assert protocol["full_cognitive_blinding_claimed"] is False


def test_b_implementation_is_bound_but_opens_no_next_stage_or_runtime() -> None:
    checkpoint = load_design()["implementation_checkpoint"]
    assert checkpoint == {
        "work_item": "P3-STAGE-008-B",
        "state": "ENGINEERING_IMPLEMENTATION_ACCEPTED",
        "fact_contract": "config/structure-directional-path-geometry.v1.json",
        "fact_contract_digest": (
            "sha256:d944a1c503b0d6bb71dfae247c94378361f1a4100174a6da93c944cc2cc1030c"
        ),
        "fact_implementation": "src/quanthus/structure_directional_path_geometry.py",
        "reference_protocol": "config/stage-learning/order-reference-protocol.v2.json",
        "reference_protocol_digest": (
            "sha256:8db37fb3d197668cfe16f9daabaa57c625617460be94ace13a28ccb81e89c88b"
        ),
        "reference_implementation": "src/quanthus/stage_learning/order_reference_protocol_v2.py",
        "acceptance": (
            "docs/acceptance/stage-directional-boundary-repair-b-implementation-20260912.md"
        ),
        "new_market_data_reads": 0,
        "runtime_jobs": 0,
        "parameter_trials": 0,
        "stage_2_changed": False,
        "stage_007_threshold_learning": False,
        "next_work_item_authorized": False,
    }
    for key in (
        "fact_contract",
        "fact_implementation",
        "reference_protocol",
        "reference_implementation",
        "acceptance",
    ):
        assert (ROOT / checkpoint[key]).is_file()


def test_c_conformance_is_bound_without_opening_d_data_or_threshold_search() -> None:
    checkpoint = load_design()["conformance_checkpoint"]
    assert checkpoint["work_item"] == "P3-STAGE-008-C"
    assert checkpoint["state"] == "PASS_DIAGNOSTIC_CONFORMANCE_ONLY"
    assert checkpoint["contract_digest"] == (
        "sha256:defec6a0ec09f209e5608274febdf3a8c76f7907db4bc44c8c2a9061dff022bb"
    )
    assert checkpoint["case_count"] == 160
    assert checkpoint["old_stage_0_true_positive_count"] == 28
    assert checkpoint["old_stage_0_false_positive_neither_count"] == 12
    assert checkpoint["diagnostic_medians"] == {
        "old_stage_0_true_positive": {
            "absolute_window_return_20": "0.0444",
            "range_traversal_20": "0.3261",
            "counter_direction_excursion_share_20": "0.6286",
        },
        "old_stage_0_false_positive_neither": {
            "absolute_window_return_20": "0.2526",
            "range_traversal_20": "0.7695",
            "counter_direction_excursion_share_20": "0.3272",
        },
    }
    assert checkpoint["read_only_stage_status_calls"] == 21
    assert checkpoint["new_market_data_reads"] == 0
    assert checkpoint["runtime_jobs"] == 0
    assert checkpoint["threshold_search_count"] == 0
    assert checkpoint["parameter_trials"] == 0
    assert checkpoint["stage_007_threshold_learning"] is False
    assert checkpoint["stage_007_qualification"] is False
    assert checkpoint["next_work_item_preregistration_authorized"] is True
    assert checkpoint["next_work_item_data_access_authorized"] is False
    for key in ("contract", "runner", "acceptance"):
        assert (ROOT / checkpoint[key]).is_file()


def test_stage007_is_diagnostic_only_and_future_partitions_remain_closed() -> None:
    design = load_design()
    diagnostic = design["stage_007_design_diagnostic"]
    assert diagnostic["cohort_status"] == "permanently_design_exposed"
    assert diagnostic["threshold_search_performed"] is False
    assert diagnostic["daily_sign_reversal_rate_20"]["decision"] == "do_not_promote_as_primary_fact"
    future = design["future_validation_boundary"]
    assert future["stage_007_cases_may_be_used_for_threshold_learning"] is False
    assert future["stage_007_cases_may_be_used_for_qualification"] is False
    assert future["new_market_data_read_authorized"] is True
    assert design["development_screen_activation"]["development_data_years"] == [2019, 2020, 2021]
    assert future["2022_2024_access_authorized"] is False
    assert future["2025_access_authorized"] is False
    assert future["stage_2_threshold_research_authorized"] is False


def test_adversarial_cases_cover_direction_recent_and_unavailable_failures() -> None:
    cases = {
        item["case"]: item["expected"]
        for item in load_design()["synthetic_direction_guard_cases"]
    }
    assert cases["monotonic_up"] == "relocation_positive_stage0_false"
    assert cases["monotonic_down"] == "relocation_positive_stage0_false"
    assert "counter_excursion" in cases["large_up_down_up_path_ending_at_range_edge"]
    assert cases["narrow_contracting_box_with_recent_10d_expansion"] == (
        "primary_stage2_unchanged_recent_modifier_contradicts"
    )
    assert cases["listing_or_coordinate_reset"] == "input_unavailable_not_stage0"
    assert cases["missing_primary_input"] == "input_unavailable_not_stage0"


def _path_geometry(closes: list[float]) -> tuple[float, float | None]:
    window_range = max(closes) - min(closes)
    if window_range == 0:
        return 0.0, None
    net = closes[-1] - closes[0]
    traversal = abs(net) / window_range
    if net == 0:
        return traversal, None
    if net > 0:
        running = closes[0]
        adverse = 0.0
        for value in closes[1:]:
            running = max(running, value)
            adverse = max(adverse, running - value)
    else:
        running = closes[0]
        adverse = 0.0
        for value in closes[1:]:
            running = min(running, value)
            adverse = max(adverse, value - running)
    return traversal, adverse / window_range


def test_proposed_path_geometry_handles_adversarial_direction_cases() -> None:
    assert _path_geometry([0.0, 2.0, 4.0, 6.0, 10.0]) == (1.0, 0.0)
    assert _path_geometry([10.0, 8.0, 6.0, 4.0, 0.0]) == (1.0, 0.0)
    assert _path_geometry([0.0, 10.0, 0.0, 10.0]) == (1.0, 1.0)
    assert _path_geometry([0.0, 10.0, 0.0]) == (0.0, None)
    assert _path_geometry([0.0, 10.0, 10.0, 10.0]) == (1.0, 0.0)
    base = _path_geometry([2.0, 4.0, 3.0, 5.0, 6.0])
    scaled = _path_geometry([20.0, 40.0, 30.0, 50.0, 60.0])
    assert base == scaled


def test_design_and_review_routes_exist() -> None:
    for relative in (
        "docs/design/stage-directional-boundary-repair.md",
        "docs/reviews/stage-directional-boundary-repair-review-20260911.md",
        "docs/work-items/stage-directional-boundary-repair.md",
    ):
        assert (ROOT / relative).is_file()

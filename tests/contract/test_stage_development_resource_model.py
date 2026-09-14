from __future__ import annotations

import json

from quanthus.policy import find_project_root

ROOT = find_project_root()
MODEL = ROOT / "config/stage-learning/stage-development-resource-model.v1.json"
REVIEW = ROOT / "docs/reviews/stage-development-resource-four-party-review-20260914.md"
WORK_ITEM = ROOT / "docs/work-items/stage-development-resource-model.md"
CENSUS = ROOT / "config/stage-learning/stage-development-resource-capacity-census.v1.json"
CENSUS_SOURCE = ROOT / "src/quanthus/stage_learning/development_resource_capacity_census.py"
CENSUS_ACCEPTANCE = (
    ROOT / "docs/acceptance/stage-development-resource-capacity-census-engineering-20260914.md"
)
CENSUS_RESULT = (
    ROOT / "config/stage-learning/stage-development-resource-capacity-census-result.v1.json"
)
QUOTA_FREEZE = ROOT / "config/stage-learning/stage-development-resource-quota-freeze.v1.json"
P013B_REVIEW = ROOT / "docs/reviews/stage-development-resource-p013b-four-party-review-20260914.md"
P013C_PLAN = ROOT / "config/stage-learning/stage-development-resource-plan.v1.json"
P013C_ACCEPTANCE = ROOT / "docs/acceptance/stage-development-resource-plan-engineering-20260914.md"
P013C_RESULT = ROOT / "config/stage-learning/stage-development-resource-plan-result.v1.json"
P013C_SUCCESSOR = (
    ROOT / "config/stage-learning/stage-atomic-proposition-successor-development-screen.v1.json"
)


def load_model() -> dict:
    return json.loads(MODEL.read_text(encoding="utf-8"))


def test_stage013_selects_exposure_aware_resource_unit() -> None:
    value = load_model()
    assert value["work_item"] == "P3-STAGE-013"
    assert value["state"] == "DESIGN_SELECTED_CAPACITY_CENSUS_REQUIRED_BEFORE_MARKET_READ"
    selected = value["selected_model"]
    assert selected["name"] == "ERA_BOARD_EXPOSURE_AWARE_DEVELOPMENT_RESERVOIR"
    assert selected["resource_unit"] == [
        "economic_identity",
        "non_overlapping_time_window",
        "listing_board_at_time",
        "evidence_role",
    ]
    assert selected["whole_identity_is_not_default_consumption_unit"] is True
    assert selected["random_append_to_reach_capacity"] is False
    assert selected["result_aware_expansion"] is False


def test_stage013_exposure_levels_separate_development_from_preserved_evaluation() -> None:
    levels = {item["id"]: item for item in load_model()["exposure_levels"]}
    assert tuple(levels) == (
        "L0_METADATA_DISCOVERED",
        "L1_POOL_RESERVED_NOT_READ",
        "L2_DEVELOPMENT_MARKET_READ",
        "L3_THRESHOLD_OR_MODEL_BASIS_CONSUMED",
        "L4_REVIEW_OR_SCORING_CONSUMED",
        "L5_PRESERVED_EVALUATION",
    )
    assert levels["L0_METADATA_DISCOVERED"]["future_development_reuse"] is True
    assert levels["L1_POOL_RESERVED_NOT_READ"]["future_development_reuse"] is True
    for key in (
        "L2_DEVELOPMENT_MARKET_READ",
        "L3_THRESHOLD_OR_MODEL_BASIS_CONSUMED",
        "L4_REVIEW_OR_SCORING_CONSUMED",
    ):
        assert "disjoint_window_only" in levels[key]["future_development_reuse"]
        assert levels[key]["identity_global_exclusion"] is False
    assert levels["L5_PRESERVED_EVALUATION"]["future_development_reuse"] is False
    assert levels["L5_PRESERVED_EVALUATION"]["identity_global_exclusion"] is True


def test_stage013_board_and_time_axes_are_structural_not_outcome_labels() -> None:
    value = load_model()
    board = value["board_axis"]
    assert board["authority"] == "dwd_listing.board_code at the declared point in time"
    assert board["families"] == {
        "MAIN": ["SSE_main", "SZ_main"],
        "CHINEXT": ["ChiNext"],
        "STAR": ["STAR"],
    }
    assert board["bse_in_scope"] is False
    assert board["board_is_hard_sampling_axis"] is True
    assert board["board_is_not_a_stage_label"] is True

    time = value["time_axis"]
    assert time["development_reservoir_end"] == "2020-12-31"
    assert [item["id"] for item in time["eras"]] == [
        "PRE_STAR",
        "STAR_PRE_CHINEXT_20PCT",
        "STAR_AND_CHINEXT_20PCT",
    ]
    assert time["eras"][1]["start"] == "2019-07-22"
    assert time["eras"][2]["start"] == "2020-08-24"
    assert "not ex-post market performance" in time["regime_boundary_basis"]


def test_stage013_does_not_promote_static_industry_to_historical_truth() -> None:
    sector = load_model()["sector_axis"]
    assert sector["hard_sampling_axis_now"] is False
    assert "not authoritative historical PIT truth" in sector["reason"]
    assert "diagnostic metadata only" in sector["allowed_now"]


def test_stage013_capacity_census_is_metadata_only_and_preserves_blinds() -> None:
    value = load_model()
    census = value["capacity_census_next"]
    for key in ("market_price_read", "morphology_read", "label_read", "candidate_scoring"):
        assert census[key] is False
    assert census["quota_freeze_rule"].startswith("Exact board-era quotas must be frozen")
    assert any(
        "every structurally present board-era cell" in item
        for item in census["allocation_principles"]
    )
    assert any("not randomly" in item for item in census["allocation_principles"])

    protected = value["protected_surfaces"]
    assert protected["preserved_2021"]["state"] == "UNREAD_AND_IDENTITY_GLOBAL_EXCLUSION_RETAINED"
    assert protected["preserved_2021"]["identity_count"] == 160
    assert protected["2022_2024"] == "CLOSED_PENDING_SEPARATE_AUTHORITY"
    assert protected["2025"] == "PERMANENT_BLIND_CLOSED"


def test_stage013_documents_record_four_party_selection_and_next_gate() -> None:
    assert REVIEW.is_file()
    assert WORK_ITEM.is_file()
    review = REVIEW.read_text(encoding="utf-8")
    work = WORK_ITEM.read_text(encoding="utf-8")
    for role in (
        "Short-term trading perspective",
        "Fund-manager perspective",
        "Quantitative research perspective",
        "Data / evidence governance perspective",
    ):
        assert role in review
    assert "ERA_BOARD_EXPOSURE_AWARE_DEVELOPMENT_RESERVOIR" in review
    assert "P3-STAGE-013-A" in work
    assert "metadata-only" in work
    assert "2025 remains permanent blind" in work


def test_stage013_a_engineering_is_metadata_only_and_bounded() -> None:
    census = json.loads(CENSUS.read_text(encoding="utf-8"))
    assert census["work_item"] == "P3-STAGE-013-A"
    assert census["state"] == "PREREGISTERED_METADATA_ONLY_BEFORE_CENSUS"
    assert census["window_contract"]["open_sessions_per_window"] == 61
    assert census["runtime"]["maximum_jobs"] == 1
    assert census["runtime"]["automatic_retries"] == 0
    source = CENSUS_SOURCE.read_text(encoding="utf-8")
    assert "dwd_listing.parquet" in source
    assert "dwd_trading_calendar" in source
    assert "dwd_security_bar" not in source
    assert "candidate-grid.json" not in source
    assert "evidence.json" not in source
    assert CENSUS_ACCEPTANCE.is_file()
    assert "PASS_ENGINEERING_READY_FOR_ONE_METADATA_ONLY_JOB" in CENSUS_ACCEPTANCE.read_text(
        encoding="utf-8"
    )


def test_stage013_a_result_proves_capacity_without_market_result_read() -> None:
    result = json.loads(CENSUS_RESULT.read_text(encoding="utf-8"))
    assert result["state"] == "PASS_METADATA_CAPACITY_CENSUS"
    assert result["job"]["terminal_state"] == "succeeded"
    assert result["artifact"]["verified"] is True
    totals = result["totals"]
    assert totals["economic_identity_count"] == 4194
    assert totals["identity_window_capacity"] == 104833
    assert totals["exposure_aware_available_capacity"] == 90067
    assert totals["legacy_whole_identity_available_capacity"] == 18967
    assert totals["released_capacity_vs_whole_identity"] == 71100
    cells = [row for row in result["capacity_matrix"] if row["structurally_present"]]
    assert len(cells) == 8
    assert min(row["available_unique_identity_count"] for row in cells) == 57
    assert result["interpretation"]["global_market_data_exhaustion_demonstrated"] is False
    assert result["interpretation"]["whole_identity_burn_down_is_binding_waste"] is True
    assert result["interpretation"]["preserved_2021_market_data_read"] is False


def test_stage013_b_freezes_equal_cell_quotas_before_result_bearing_read() -> None:
    value = json.loads(QUOTA_FREEZE.read_text(encoding="utf-8"))
    assert value["state"] == "BOARD_ERA_QUOTAS_FROZEN_BEFORE_RESULT_BEARING_READ"
    derivation = value["derivation"]
    assert derivation["structurally_present_board_era_cell_count"] == 8
    assert derivation["review_cases_per_cell"] == 24
    assert derivation["reserve_identity_windows_per_cell"] == 32
    assert derivation["prefrozen_fallback_identity_windows_per_cell"] == 8
    assert derivation["total_reserve_identity_windows"] == 256
    assert derivation["total_primary_review_identity_windows"] == 192
    assert derivation["inherited_512_pool_requirement"] is False
    assert len(value["cell_quotas"]) == 8
    assert all(row["reserve"] == 32 for row in value["cell_quotas"])
    assert min(row["observed_available_unique_identities"] for row in value["cell_quotas"]) == 57
    selection = value["selection_policy"]
    assert selection["current_batch_global_identity_uniqueness_required"] is True
    assert selection["same_identity_multiple_cells_in_current_batch"] is False
    assert selection["cross_cell_fallback"] is False
    assert selection["post_result_identity_replacement"] is False
    fallback = value["fallback_policy"]
    assert fallback["fallbacks_are_frozen_before_market_read"] is True
    assert fallback["maximum_fallbacks_per_cell"] == 8
    protected = value["protected_surfaces"]
    assert protected["preserved_2021_market_data_read"] is False
    assert protected["2025_market_data_read"] is False
    assert value["next_gate"]["work_item"] == "P3-STAGE-013-C"


def test_stage013_b_review_records_all_four_responsibilities_and_no_market_authority() -> None:
    text = P013B_REVIEW.read_text(encoding="utf-8")
    for heading in (
        "Short-term trading perspective",
        "Fund-manager perspective",
        "Quantitative research perspective",
        "Data and evidence governance perspective",
    ):
        assert heading in text
    assert "PASS_FREEZE_EQUAL_ERA_BOARD_RESERVE_AND_REVIEW_QUOTAS" in text
    assert "No result-bearing market read is authorized" in text
    assert "256" in text and "192" in text


def test_stage013_c_engineering_freezes_exact_metadata_plan_before_market_read() -> None:
    value = json.loads(P013C_PLAN.read_text(encoding="utf-8"))
    assert value["work_item"] == "P3-STAGE-013-C"
    assert value["state"] == "PREREGISTERED_METADATA_PLAN_BEFORE_RESULT_BEARING_READ"
    selection = value["selection"]
    assert selection["total_reserve_rows"] == 256
    assert selection["total_primary_rows"] == 192
    assert selection["total_fallback_rows"] == 64
    assert selection["global_identity_unique"] is True
    assert selection["same_identity_multiple_rows"] is False
    assert value["runtime"]["maximum_jobs"] == 1
    assert value["runtime"]["automatic_retries"] == 0
    for key in (
        "market_price_read",
        "morphology_read",
        "label_or_reference_read",
        "candidate_output_read",
        "candidate_scoring",
        "preserved_2021_market_read",
        "2022_2024_read",
        "2025_read",
        "result_bearing_stage_authorized",
    ):
        assert value["authority"][key] is False
    assert P013C_ACCEPTANCE.is_file()
    text = P013C_ACCEPTANCE.read_text(encoding="utf-8")
    assert "PASS_ENGINEERING_READY_FOR_ONE_EXACT_METADATA_PLAN_JOB" in text
    assert "exactly 256 unique economic identities" in text


def test_stage013_c_exact_plan_result_is_frozen_and_still_non_result_bearing() -> None:
    value = json.loads(P013C_RESULT.read_text(encoding="utf-8"))
    assert value["state"] == "PASS_EXACT_METADATA_PLAN_FROZEN"
    assert value["failed_attempt"]["artifact_published"] is False
    assert value["failed_attempt"]["market_data_read"] is False
    recovery = value["recovery"]
    assert recovery["job_id"] == "stage_060f6edc80774b6b802e56b6c21025bc"
    assert recovery["terminal_state"] == "succeeded"
    artifact = value["artifact"]
    assert artifact["verified"] is True
    assert artifact["manifest_sha256"] == (
        "sha256:853ade679e1107db238ec68daa8a26755e1d48ef334b9b85d525a8fcd0a3adef"
    )
    assert artifact["plan_digest"] == (
        "sha256:2aff2d250cb58baae9775b23cc53d2f0ac073f3408af577e17102f67cb54f371"
    )
    counts = value["counts"]
    assert counts == {
        "row_count": 256,
        "unique_identity_count": 256,
        "primary_count": 192,
        "fallback_count": 64,
        "structurally_present_cell_count": 8,
        "reserve_per_cell": 32,
        "primary_per_cell": 24,
        "fallback_per_cell": 8,
    }
    assert set(value["cell_counts"].values()) == {32}
    assert value["authority"]["market_price_read"] is False
    assert value["authority"]["result_bearing_stage_authorized"] is False


def test_stage013_c_successor_prereg_binds_exact_plan_and_preserves_atomic_surface() -> None:
    value = json.loads(P013C_SUCCESSOR.read_text(encoding="utf-8"))
    assert value["state"] == "PREREGISTERED_EXACT_PLAN_BEFORE_RESULT_BEARING_MARKET_READ"
    plan = value["bindings"]["exact_plan_artifact"]
    assert plan["job_id"] == "stage_060f6edc80774b6b802e56b6c21025bc"
    assert plan["row_count"] == 256
    assert plan["unique_identity_count"] == 256
    assert plan["primary_count"] == 192
    assert plan["fallback_count"] == 64
    resources = value["resource_plan"]
    assert resources["cell_count"] == 8
    assert resources["reserve_per_cell"] == 32
    assert resources["primary_per_cell"] == 24
    assert resources["fallback_per_cell"] == 8
    assert resources["global_identity_unique"] is True
    assert resources["cross_cell_fallback"] is False
    assert resources["post_result_identity_replacement"] is False
    atomic = value["atomic_contract"]
    assert atomic["instability_candidate_count"] == 9
    assert atomic["relocation_candidate_count"] == 1
    assert atomic["order_profile_count"] == 3
    assert atomic["joint_pair_search_count"] == 0
    assert atomic["numeric_atomic_quality_gates"] == "exact_P012"
    assert atomic["projection_compatibility_numeric_gates"] == "exact_P012"
    assert atomic["candidate_surface_change"] is False
    review = value["review"]
    assert review["primary_aggregate"] == "macro_equal_weight_across_eight_era_board_cells"
    assert review["candidate_output_visible"] is False
    assert review["immutable_review_ledger_required_before_scoring"] is True
    assert value["preserved_evaluation_gate"]["otherwise_remains_unread"] is True
    assert value["authority"]["result_bearing_market_read"] is False
    assert value["authority"]["runtime_job_submission"] is False
    assert value["authority"]["numeric_gate_change"] is False
    assert value["authority"]["2025_read"] is False


def test_stage013_does_not_reopen_p012_or_result_bearing_authority() -> None:
    text = json.dumps(load_model(), sort_keys=True)
    for forbidden in (
        '"random_append_to_reach_capacity": true',
        '"market_price_read": true',
        '"morphology_read": true',
        '"label_read": true',
        '"candidate_scoring": true',
    ):
        assert forbidden not in text
    non_goals = "\n".join(load_model()["explicit_non_goals"])
    assert "Do not reopen or rerun P012 G1." in non_goals
    assert "Do not lower P012 gates" in non_goals
    assert "Do not use 2024 technology-bull knowledge" in non_goals

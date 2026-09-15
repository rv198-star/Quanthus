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
P013D_CLOSEOUT = (
    ROOT / "config/stage-learning/stage-atomic-proposition-successor-d1-closeout.v1.json"
)
P014_MODEL = (
    ROOT / "config/stage-learning/stage-development-resource-admission-qualified-model.v1.json"
)
P014_WORK = ROOT / "docs/work-items/stage-development-resource-admission-qualified.md"
P014_ADMISSION_RESULT = (
    ROOT / "config/stage-learning/stage-development-resource-admission-result.v1.json"
)
P014_QUALIFIED_RESULT = (
    ROOT / "config/stage-learning/stage-development-resource-qualified-plan-result.v1.json"
)
P014C_PREREG = (
    ROOT
    / "config/stage-learning/"
    "stage-atomic-proposition-admission-qualified-development-screen.v1.json"
)
P014C_EXECUTION = (
    ROOT / "config/stage-learning/stage-atomic-proposition-admission-qualified-execution.v1.json"
)
P014C_ACCEPTANCE = (
    ROOT
    / "docs/acceptance/stage-atomic-proposition-admission-qualified-c-engineering-20260914.md"
)
P014D_CLOSEOUT = (
    ROOT / "config/stage-learning/stage-atomic-proposition-admission-qualified-d1-closeout.v1.json"
)
P015_MODEL = ROOT / "config/stage-learning/stage-complete-reserve-reference-model.v1.json"
P015_A_RESULT = (
    ROOT / "config/stage-learning/stage-complete-reserve-review-readiness-result.v1.json"
)
P015_B_CONFIG = (
    ROOT / "config/stage-learning/stage-complete-reserve-additional-review-set.v1.json"
)
P015_B_RECOVERY = (
    ROOT / "config/stage-learning/stage-complete-reserve-additional-review-set-recovery.v1.json"
)
P015_B_RESULT = (
    ROOT / "config/stage-learning/stage-complete-reserve-additional-review-set-result.v1.json"
)
P015_C_CONFIG = (
    ROOT / "config/stage-learning/stage-complete-reserve-additional-review-prepare.v1.json"
)
P015_D_RESULT = (
    ROOT / "config/stage-learning/stage-complete-reserve-reference-sufficiency-result.v1.json"
)
P016_SCORING = ROOT / "config/stage-learning/stage-combined-atomic-scoring.v1.json"
P016_RESULT = ROOT / "config/stage-learning/stage-combined-atomic-scoring-result.v1.json"
P016_WORK = ROOT / "docs/work-items/stage-combined-atomic-scoring.md"
P016_REVIEW = ROOT / "docs/reviews/stage-p016-combined-scoring-four-party-review-20260915.md"
P016_NOPASS_REVIEW = (
    ROOT / "docs/reviews/stage-p016-instability-no-pass-four-party-review-20260915.md"
)
P017_CONFIG = ROOT / "config/stage-learning/stage-stratum-normalized-instability.v1.json"
P017_RECOVERY = (
    ROOT / "config/stage-learning/stage-stratum-normalized-instability-recovery.v1.json"
)
P017_RESULT = ROOT / "config/stage-learning/stage-stratum-normalized-instability-result.v1.json"
P017_NOPASS_REVIEW = (
    ROOT / "docs/reviews/stage-p017-stratum-normalized-no-pass-four-party-review-20260915.md"
)
P017_WORK = ROOT / "docs/work-items/stage-stratum-normalized-instability.md"
P018_CONFIG = ROOT / "config/stage-learning/stage-instability-channel-decomposition.v1.json"
P018_RESULT = ROOT / "config/stage-learning/stage-instability-channel-decomposition-result.v1.json"
P018_REVIEW = ROOT / "docs/reviews/stage-p018-channel-decomposition-four-party-review-20260915.md"
P018_WORK = ROOT / "docs/work-items/stage-instability-channel-decomposition.md"
P019_REBASELINE = ROOT / "config/stage-learning/stage-third-group-portfolio-rebaseline.v1.json"
P019_REVIEW = ROOT / "docs/reviews/stage-p019-third-group-portfolio-rebaseline-20260915.md"
P019_WORK = ROOT / "docs/work-items/stage-third-group-portfolio-rebaseline.md"
P020_CONCEPT = ROOT / "config/stage-learning/stage-downside-lifecycle-concept.v1.json"
P020_RESOURCE = ROOT / "config/stage-learning/stage-downside-lifecycle-resource-plan.v1.json"
P020_WORK = ROOT / "docs/work-items/stage-downside-lifecycle-proposition.md"
P015_WORK = ROOT / "docs/work-items/stage-complete-reserve-reference.md"
P014D_REVIEW = ROOT / "docs/reviews/stage-p014d-reference-sufficiency-four-party-review-20260914.md"
P013D_PREREG_R1 = (
    ROOT / "config/stage-learning/stage-atomic-proposition-successor-prereg-revision-r1.v1.json"
)
P013D_EXECUTION = (
    ROOT / "config/stage-learning/stage-atomic-proposition-successor-execution.v1.json"
)
P013D_PROTOCOL = ROOT / "config/stage-learning/order-reference-evidence-scope-p013.v1.json"
P013D_SOURCE = (
    ROOT / "src/quanthus/stage_learning/atomic_proposition_successor_development_prepare.py"
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


def test_stage013_d_runtime_is_exact_plan_bounded_and_one_shot() -> None:
    revision = json.loads(P013D_PREREG_R1.read_text(encoding="utf-8"))
    assert revision["state"] == "FROZEN_PRE_RESULT_INPUT_WINDOW_ALIGNMENT_R1"
    assert revision["revision"]["case_t_selection_after"] == "exact_plan_window_end_only"
    assert revision["unchanged"]["total_primary"] == 192
    assert revision["unchanged"]["total_fallback"] == 64
    assert revision["unchanged"]["instability_candidate_count"] == 9
    assert revision["unchanged"]["relocation_candidate_count"] == 1
    assert revision["unchanged"]["order_profile_count"] == 3
    assert revision["unchanged"]["joint_pair_search_count"] == 0
    assert revision["authority"]["market_data_read_before_revision_freeze"] is False

    execution = json.loads(P013D_EXECUTION.read_text(encoding="utf-8"))
    assert execution["state"] == "FROZEN_RUNTIME_CONTRACT_BEFORE_FIRST_RESULT_BEARING_JOB"
    assert execution["exact_plan_artifact"]["plan_digest"] == (
        "sha256:2aff2d250cb58baae9775b23cc53d2f0ac073f3408af577e17102f67cb54f371"
    )
    assert execution["market_read"]["group_by_exact_window"] is True
    assert execution["market_read"]["read_only_exact_plan_window_dates"] is True
    assert execution["market_read"]["price_rows_outside_window"] is False
    assert execution["selection"]["complete_cases_per_cell"] == 24
    assert execution["selection"]["total_complete_cases"] == 192
    assert execution["selection"]["fallback_same_cell_only"] is True
    assert execution["selection"]["cross_cell_fill"] is False
    assert execution["runtime"]["maximum_jobs"] == 1
    assert execution["runtime"]["automatic_retries"] == 0
    assert execution["p012_atomic_contract"]["instability_candidate_count"] == 9
    assert execution["p012_atomic_contract"]["relocation_candidate_count"] == 1
    assert execution["p012_atomic_contract"]["order_profile_count"] == 3
    assert execution["p012_atomic_contract"]["joint_pair_search_count"] == 0
    assert execution["authority"]["preserved_2021_evaluation_read"] is False
    assert execution["authority"]["2025_access"] is False

    protocol = json.loads(P013D_PROTOCOL.read_text(encoding="utf-8"))
    assert protocol["profile"]["retained_history_sessions"] == 61
    assert protocol["bounds"]["max_cases"] == 192
    assert protocol["bounds"]["max_source_rows"] == 192 * 61
    assert protocol["sampling"]["case_t"] == "exact_plan_window_end_only"
    assert protocol["authority"]["preserved_2021_access"] is False
    source = P013D_SOURCE.read_text(encoding="utf-8")
    assert "read_development_admissions" in source
    assert "discover_development_security_population" not in source
    assert "exact_plan_window_end_only" in source
    assert '"cross_cell_fill": False' in source


def test_stage013_d_closeout_is_pre_review_admission_failure_not_atomic_failure() -> None:
    value = json.loads(P013D_CLOSEOUT.read_text(encoding="utf-8"))
    assert value["state"] == "CLOSED_PRE_REVIEW_INPUT_ADMISSION_SHORTFALL"
    job = value["job"]
    assert job["job_id"] == "stage_71fe94875bbe428fb464ffc6017af313"
    assert job["terminal_state"] == "failed"
    assert job["artifact_published"] is False
    assert job["failure_reason"] == "cell_complete_case_shortfall:PRE_STAR/MAIN"
    position = value["execution_position"]
    assert position["market_read_reached"] is True
    assert position["threshold_basis_computation_reached"] is True
    assert position["blind_review_reached"] is False
    assert position["candidate_scoring_reached"] is False
    assert position["preserved_2021_read"] is False
    cause = value["root_cause_classification"]
    assert cause["frozen_sample_input_admission_shortfall_demonstrated"] is True
    assert cause["semantic_candidate_failure_demonstrated"] is False
    assert cause["numeric_gate_failure_demonstrated"] is False
    exposure = value["exposure_accounting"]
    assert exposure["exact_plan_windows_market_read"] == 256
    assert exposure["whole_identity_global_burn"] is False
    retry = value["retry_authority"]
    assert retry["same_experiment_retry"] is False
    assert retry["increase_fallback_after_result"] is False
    assert value["successor"]["work_item"] == "P3-STAGE-014"


def test_stage014_inserts_admission_only_screen_before_semantic_development() -> None:
    value = json.loads(P014_MODEL.read_text(encoding="utf-8"))
    assert value["work_item"] == "P3-STAGE-014"
    assert value["state"] == "DESIGN_SELECTED_ADMISSION_SCREEN_REQUIRED_BEFORE_SEMANTIC_READ"
    target = value["semantic_target"]
    assert target["era_board_cell_count"] == 8
    assert target["qualified_reserve_per_cell"] == 32
    assert target["primary_per_cell"] == 24
    assert target["prefrozen_fallback_per_cell"] == 8
    assert target["global_identity_unique_in_scored_batch"] is True
    assert target["cross_cell_fill"] is False
    screen = value["candidate_screen"]
    assert screen["maximum_candidate_identities_per_cell"] == 256
    assert screen["maximum_windows_per_identity"] == 4
    assert screen["p013_d_exact_identity_windows_excluded_from_overlap"] is True
    assert screen["same_identity_disjoint_window_reuse_allowed"] is True
    admission = value["admission_screen"]
    for key in (
        "semantic_morphology_read",
        "structure_fact_build",
        "threshold_basis",
        "label_or_reference_read",
        "candidate_output",
        "candidate_scoring",
        "raw_ohlc_published",
        "raw_ohlc_retained_in_artifact",
    ):
        assert admission[key] is False
    assert "coordinate_ref remains stable" in "\n".join(admission["qualified_window_requires"])
    freeze = value["qualified_plan_freeze"]
    assert freeze["must_happen_before_semantic_read"] is True
    assert freeze["first_32_globally_unique_qualified_identities_per_cell"] is True
    assert freeze["no_supplement_after_semantic_results"] is True
    atomic = value["atomic_semantic_contract"]
    assert atomic["instability_candidate_count"] == 9
    assert atomic["relocation_candidate_count"] == 1
    assert atomic["order_profile_count"] == 3
    assert atomic["joint_pair_search_count"] == 0
    assert atomic["candidate_or_gate_change"] is False
    assert P014_WORK.is_file()


def test_stage014_admission_and_qualified_plan_results_close_pre_semantic_resource_work() -> None:
    admission = json.loads(P014_ADMISSION_RESULT.read_text(encoding="utf-8"))
    assert admission["state"] == "PASS_ADMISSION_CAPACITY_READY_FOR_QUALIFIED_PLAN"
    assert admission["job"]["job_id"] == "stage_bcaf8263cb724a2798d250909c42331f"
    assert admission["totals"]["all_cells_have_required_qualified_capacity"] is True
    assert admission["totals"]["candidate_window_count"] == 4608
    assert admission["totals"]["qualified_window_count"] == 3942
    assert min(row["qualified_unique_identity_count"] for row in admission["cell_summary"]) == 36
    assert admission["interpretation"]["raw_ohlc_retained"] is False
    assert admission["interpretation"]["threshold_basis_computed"] is False
    assert admission["interpretation"]["candidate_scoring"] is False

    plan = json.loads(P014_QUALIFIED_RESULT.read_text(encoding="utf-8"))
    assert plan["state"] == "PASS_QUALIFIED_EXACT_PLAN_FROZEN"
    assert plan["job"]["job_id"] == "stage_9dbc57be4839405790be0e021d8271ba"
    assert plan["counts"] == {
        "row_count": 256,
        "unique_identity_count": 256,
        "primary_count": 192,
        "fallback_count": 64,
        "cell_count": 8,
        "rows_per_cell": 32,
    }
    assert set(plan["cell_counts"].values()) == {32}
    assert plan["authority"]["market_data_read"] is False
    assert plan["authority"]["semantic_job_authorized"] is False
    assert plan["artifact"]["plan_digest"] == (
        "sha256:206ed58e8349de59efd52caad958f7bd0f2f9d79f1ee0199eea6a079b6c14829"
    )


def test_stage014_c_freezes_one_admission_qualified_semantic_job_before_execution() -> None:
    prereg = json.loads(P014C_PREREG.read_text(encoding="utf-8"))
    execution = json.loads(P014C_EXECUTION.read_text(encoding="utf-8"))
    assert prereg["work_item"] == "P3-STAGE-014-C"
    assert prereg["state"] == "PREREGISTERED_QUALIFIED_PLAN_BEFORE_SEMANTIC_MARKET_READ"
    plan = prereg["bindings"]["qualified_plan_artifact"]
    assert plan["job_id"] == "stage_9dbc57be4839405790be0e021d8271ba"
    assert plan["row_count"] == 256
    assert plan["unique_identity_count"] == 256
    assert plan["primary_count"] == 192
    assert plan["fallback_count"] == 64
    resource = prereg["resource_plan"]
    assert resource["input_admission_qualified_before_plan_freeze"] is True
    assert resource["cross_cell_fallback"] is False
    assert resource["post_result_identity_replacement"] is False
    atomic = prereg["atomic_contract"]
    assert atomic["instability_candidate_count"] == 9
    assert atomic["relocation_candidate_count"] == 1
    assert atomic["order_profile_count"] == 3
    assert atomic["joint_pair_search_count"] == 0
    assert atomic["numeric_atomic_quality_gates"] == "exact_P012"
    assert atomic["projection_compatibility_numeric_gates"] == "exact_P012"
    review = prereg["review"]
    assert review["candidate_output_visible"] is False
    assert review["threshold_basis_visible"] is False
    assert review["envelope_measurement_visible"] is False
    assert review["immutable_review_ledger_required_before_scoring"] is True
    assert prereg["preserved_evaluation_gate"]["otherwise_remains_unread"] is True
    assert prereg["authority"]["runtime_job_submission"] is False
    assert prereg["authority"]["2025_read"] is False

    assert execution["work_item"] == "P3-STAGE-014-C1"
    assert execution["runtime"] == {
        "maximum_jobs": 1,
        "automatic_retries": 0,
        "worker_wall_seconds": 3600,
    }
    assert execution["market_read"]["read_only_qualified_plan_window_dates"] is True
    assert execution["market_read"]["price_rows_outside_window"] is False
    assert execution["selection"]["fallback_same_cell_only"] is True
    assert execution["selection"]["cross_cell_fill"] is False
    assert execution["selection"]["post_result_identity_replacement"] is False
    assert execution["authority"]["parameter_search"] is False
    assert execution["authority"]["candidate_surface_change"] is False
    assert execution["authority"]["numeric_gate_change"] is False
    assert execution["authority"]["preserved_2021_evaluation_read"] is False
    assert execution["authority"]["2025_access"] is False
    assert P014C_ACCEPTANCE.is_file()
    assert "PASS_ENGINEERING_READY_FOR_ONE_QUALIFIED_SEMANTIC_JOB" in P014C_ACCEPTANCE.read_text(
        encoding="utf-8"
    )


def test_stage014_d_closes_before_candidate_scoring_on_exact_reference_sufficiency() -> None:
    value = json.loads(P014D_CLOSEOUT.read_text(encoding="utf-8"))
    assert value["work_item"] == "P3-STAGE-014-D"
    assert value["state"] == "CLOSED_ATOMIC_REFERENCE_SUFFICIENCY_FAIL"
    assert value["job"]["job_id"] == "stage_7c11b97d47a0473fafa37783056f55f4"
    assert value["job"]["selected_case_count"] == 192
    ledger = value["review_ledger"]
    assert ledger["ledger_sha256"] == (
        "sha256:f17063c28e0f75a8ae5280d6ee66b31f9b9ffc318896a3ae8b7e48efc101ed69"
    )
    assert ledger["case_count"] == 192
    assert ledger["selection_transport_seen_during_review"] is False
    resolved = value["p012_atomic_reference_resolution"]
    assert resolved["structural_instability_20"] == {"TRUE": 31, "FALSE": 114, "AMBIGUOUS": 47}
    assert resolved["coherent_directional_relocation_20"] == {
        "TRUE": 33,
        "FALSE": 129,
        "AMBIGUOUS": 30,
    }
    assert resolved["orderly_consolidation_20"] == {
        "TRUE": 18,
        "FALSE": 136,
        "AMBIGUOUS": 38,
    }
    gate = value["frozen_reference_sufficiency_gate"]
    assert gate["order_minimum_definite_positive"] == 20
    assert gate["order_observed_definite_positive"] == 18
    assert gate["passed"] is False
    ceiling = value["execution_ceiling"]
    assert ceiling["selection_transport_read"] is False
    assert ceiling["candidate_scoring_reached"] is False
    assert ceiling["preserved_2021_read"] is False
    assert value["successor"]["work_item"] == "P3-STAGE-015"


def test_stage015_uses_complete_precommitted_reserve_without_targeted_top_up() -> None:
    value = json.loads(P015_MODEL.read_text(encoding="utf-8"))
    assert value["work_item"] == "P3-STAGE-015"
    assert value["selected_disposition"] == (
        "PROCEED_TO_COMPLETE_RESERVE_REVIEW_READINESS_CENSUS"
    )
    ceiling = value["resource_ceiling"]
    assert ceiling["source"] == "exact_pre_result_P014_B_qualified_plan"
    assert ceiling["total_rows"] == 256
    assert ceiling["rows_per_cell"] == 32
    assert ceiling["new_identity_discovery"] is False
    assert ceiling["new_random_sampling"] is False
    assert ceiling["morphology_enrichment"] is False
    assert ceiling["result_count_targeted_top_up"] is False
    readiness = value["review_readiness_census"]
    assert readiness["inclusion_rule"].startswith("every row with review_packet_ready=true")
    assert readiness["minimum_count_for_continuation"] == 192
    assert readiness["per_cell_minimum_for_continuation"] == 24
    reuse = value["opinion_reuse"]
    assert reuse["p014_192_case_opinions"] == "immutable_reuse_exactly"
    assert reuse["re_review_existing_cases"] is False
    gate = value["combined_reference_gate"]
    assert gate["numeric_sufficiency"] == "exact_P012"
    assert gate["order_minimum_definite_positive"] == 20
    assert gate["gate_change"] is False
    protected = value["protected_surfaces"]
    assert protected["p014_candidate_selection_transport_read"] is False
    assert protected["preserved_2021_market_read"] is False
    assert protected["2025_read"] is False
    assert P015_WORK.is_file()
    assert P014D_REVIEW.is_file()
    assert "PASS_OPEN_COMPLETE_PRECOMMITTED_RESERVE_REFERENCE_SUCCESSOR" in P014D_REVIEW.read_text(
        encoding="utf-8"
    )


def test_stage015_a_result_and_b_freeze_bind_complete_ready_denominator() -> None:
    result = json.loads(P015_A_RESULT.read_text(encoding="utf-8"))
    assert result["state"] == "PASS_COMPLETE_RESERVE_REVIEW_READINESS"
    assert result["job"]["job_id"] == "stage_19326342d3bf497fba2964fbed10c29f"
    assert result["counts"] == {
        "row_count": 256,
        "ready_count": 252,
        "unready_count": 4,
        "already_reviewed_ready_count": 192,
        "new_review_required_count": 60,
    }
    assert result["reason_counts"] == {
        "PRIMARY_FACTS_UNAVAILABLE": 4,
        "READY": 252,
    }
    assert result["authority"]["semantic_values_retained"] is False
    assert result["authority"]["selection_transport_read"] is False
    assert result["authority"]["preserved_2021_market_read"] is False
    assert result["authority"]["2025_read"] is False

    config = json.loads(P015_B_CONFIG.read_text(encoding="utf-8"))
    assert config["work_item"] == "P3-STAGE-015-B"
    assert config["state"] == "PREREGISTERED_FREEZE_ALL_READY_UNREVIEWED_ROWS"
    assert config["bindings"]["readiness_artifact"]["new_review_required_count"] == 60
    assert config["bindings"]["p014_closeout"]["review_case_count"] == 192
    selection = config["selection"]
    assert selection["expected_additional_review_count"] == 60
    assert selection["include_every_matching_row"] is True
    assert selection["positive_count_stop"] is False
    assert selection["semantic_value_sort"] is False
    assert selection["reference_value_sort"] is False
    assert selection["random_sample"] is False
    assert selection["new_identity_discovery"] is False
    assert all(item is False for item in config["authority"].values())


def test_stage015_b_recovery_is_one_field_path_technical_recovery_only() -> None:
    value = json.loads(P015_B_RECOVERY.read_text(encoding="utf-8"))
    assert value["work_item"] == "P3-STAGE-015-B-R1"
    assert value["disposition"] == "AUTHORIZE_ONE_TECHNICAL_BINDING_RECOVERY"
    failed = value["failed_job"]
    assert failed["job_id"] == "stage_66051c46d8d94852888182e67bef3b7a"
    assert failed["code_revision"] == "6c94ab786b2d3f92f8b5cbd9e68ecb1f066211bb"
    assert failed["terminal_state"] == "failed"
    assert failed["artifact_published"] is False
    assert failed["runtime_artifact_read_reached"] is False
    assert failed["semantic_read_reached"] is False
    cause = value["root_cause"]
    assert cause["class"] == "IMPLEMENTATION_BINDING_FIELD_PATH"
    assert cause["observed_code_path"] == "closeout['immutable_review']['ledger_sha256']"
    assert cause["frozen_contract_path"] == "closeout['review_ledger']['ledger_sha256']"
    assert cause["config_change_required"] is False
    assert cause["resource_change_required"] is False
    assert cause["semantic_rule_change_required"] is False
    binding = value["bindings"]["original_p015_b_config"]
    assert binding["path"] == (
        "config/stage-learning/stage-complete-reserve-additional-review-set.v1.json"
    )
    assert binding["sha256"] == (
        "sha256:1bd50e11f8038b2a55a4424e6480ddd29a008463b60fd1fd1b11692e4031f9e9"
    )
    authority = value["authority"]
    assert authority["maximum_technical_recovery_jobs"] == 1
    for key in (
        "same_p015_b_config_required",
        "same_readiness_artifact_required",
        "same_qualified_plan_required",
        "same_p014_review_ledger_required",
        "same_expected_additional_review_count_60_required",
        "same_all_matching_rows_included_required",
        "field_path_repair_only",
    ):
        assert authority[key] is True
    for key in (
        "resource_change",
        "selection_predicate_change",
        "positive_count_stop",
        "semantic_sorting",
        "new_identity_discovery",
        "market_data_read",
        "semantic_values_read",
        "reference_opinions_read",
        "candidate_output_read",
        "threshold_basis_read",
        "selection_transport_read",
        "candidate_scoring",
        "preserved_2021_market_read",
        "2025_read",
        "daily_or_strategy",
        "trading",
    ):
        assert authority[key] is False


def test_stage015_b_result_and_c_prepare_keep_review_blind_and_incremental() -> None:
    result = json.loads(P015_B_RESULT.read_text(encoding="utf-8"))
    assert result["state"] == "PASS_COMPLETE_ADDITIONAL_REVIEW_SET_FROZEN"
    assert result["initial_job"]["terminal_state"] == "failed"
    assert result["initial_job"]["artifact_published"] is False
    assert result["recovery_job"]["job_id"] == "stage_ac0fb812b46e4ae5bfe57d7b2dc82aee"
    assert result["recovery_job"]["terminal_state"] == "succeeded"
    assert result["recovery_job"]["technical_recovery_of"] == (
        "stage_66051c46d8d94852888182e67bef3b7a"
    )
    assert result["counts"] == {"row_count": 60, "cell_count": 8}
    assert sum(result["cell_counts"].values()) == 60
    assert result["selection"]["all_matching_rows_included"] is True
    assert result["selection"]["positive_count_stop"] is False
    assert result["selection"]["semantic_selection"] is False
    assert result["authority"]["reference_opinions_read"] is False
    assert result["authority"]["candidate_scoring"] is False
    assert result["authority"]["preserved_2021_market_read"] is False
    assert result["authority"]["2025_read"] is False

    config = json.loads(P015_C_CONFIG.read_text(encoding="utf-8"))
    assert config["work_item"] == "P3-STAGE-015-C1"
    assert config["state"] == "PREREGISTERED_EXACT_60_PRIMARY_BLIND_REVIEW_PREPARE"
    assert config["bindings"]["additional_review_set_artifact"]["row_count"] == 60
    assert config["bindings"]["p014_closeout"]["review_case_count"] == 192
    assert config["resource_set"]["all_matching_rows_included"] is True
    assert config["resource_set"]["positive_count_stop"] is False
    assert config["review_prepare"]["expected_case_count"] == 60
    assert config["review_prepare"]["publish_evidence"] is True
    assert config["review_prepare"]["publish_directional_path_facts"] is True
    assert config["review_prepare"]["publish_era_board_stratum"] is True
    assert config["review_prepare"]["publish_threshold_basis"] is False
    assert config["review_prepare"]["publish_candidate_grid"] is False
    assert config["review_prepare"]["publish_envelope_measurements"] is False
    assert config["review_prepare"]["read_existing_p014_opinions"] is False
    review = config["review"]
    assert review["roles"] == ["short_term", "fund"]
    assert review["candidate_output_visible"] is False
    assert review["threshold_basis_visible"] is False
    assert review["envelope_measurement_visible"] is False
    assert review["existing_p014_opinions_visible"] is False
    assert review["preserved_2021_visible"] is False
    authority = config["authority"]
    assert authority["result_bearing_market_read"] is True
    assert authority["structure_fact_build"] is True
    assert authority["blind_review_packet_publication"] is True
    for key in (
        "existing_p014_opinion_read",
        "reference_opinion_generation_inside_job",
        "threshold_basis_computation",
        "candidate_output_computation",
        "envelope_measurement_computation",
        "candidate_scoring",
        "selection_transport_read",
        "preserved_2021_read",
        "2022_2024_read",
        "2025_read",
        "daily_or_strategy",
        "trading",
    ):
        assert authority[key] is False


def test_stage015_d_passes_reference_sufficiency_without_opening_scoring() -> None:
    value = json.loads(P015_D_RESULT.read_text(encoding="utf-8"))
    assert value["work_item"] == "P3-STAGE-015-D"
    assert value["state"] == "PASS_COMPLETE_RESERVE_REFERENCE_SUFFICIENCY"
    ceiling = value["resource_ceiling"]
    assert ceiling["total_rows"] == 256
    assert ceiling["review_ready_rows"] == 252
    assert ceiling["unready_rows"] == 4
    assert ceiling["reviewed_existing_rows"] == 192
    assert ceiling["reviewed_additional_rows"] == 60
    assert ceiling["all_additional_ready_rows_reviewed"] is True
    assert ceiling["post_result_case_selection"] is False
    assert ceiling["positive_count_stop"] is False

    ledgers = value["review_ledgers"]
    assert ledgers["combined_case_count"] == 252
    assert ledgers["frozen_p014"]["ledger_sha256"] == (
        "sha256:f17063c28e0f75a8ae5280d6ee66b31f9b9ffc318896a3ae8b7e48efc101ed69"
    )
    assert ledgers["p015_additional"]["ledger_sha256"] == (
        "sha256:db3216d322e00ad40ebd7de435684b43bcaf72cbc66a90dcb49403a8be461e06"
    )
    assert ledgers["old_ledger_rewritten"] is False
    assert ledgers["old_opinions_re_reviewed"] is False

    resolved = value["combined_reference_resolution"]
    assert resolved["structural_instability_20"] == {
        "TRUE": 40,
        "FALSE": 147,
        "AMBIGUOUS": 65,
    }
    assert resolved["coherent_directional_relocation_20"] == {
        "TRUE": 46,
        "FALSE": 170,
        "AMBIGUOUS": 36,
    }
    assert resolved["orderly_consolidation_20"] == {
        "TRUE": 33,
        "FALSE": 172,
        "AMBIGUOUS": 47,
    }

    gate = value["frozen_p012_reference_sufficiency_gate"]
    assert gate["minimum_complete_cases"] == 144
    assert gate["observed_complete_cases"] == 252
    assert gate["instability_observed_definite_positive"] == 40
    assert gate["instability_observed_definite_negative"] == 147
    assert gate["relocation_observed_definite_positive"] == 46
    assert gate["relocation_observed_definite_negative"] == 170
    assert gate["order_observed_definite_positive"] == 33
    assert gate["order_observed_definite_negative"] == 172
    assert gate["passed"] is True
    assert gate["gate_change"] is False

    authority = value["execution_ceiling"]
    assert authority["candidate_scoring_authorized"] is False
    assert authority["preserved_2021_read"] is False
    assert authority["2025_read"] is False
    disposition = value["disposition"]
    assert disposition["reference_sufficiency_passed"] is True
    assert disposition["atomic_candidate_quality_passed"] is False
    assert disposition["next_step_requires_separate_scoring_freeze"] is True


def test_stage016_freezes_macro_rate_pooled_count_two_job_scoring_route() -> None:
    value = json.loads(P016_SCORING.read_text(encoding="utf-8"))
    assert value["work_item"] == "P3-STAGE-016-A"
    assert value["state"] == (
        "PREREGISTERED_ARTIFACT_ONLY_ATOMIC_SCORING_BEFORE_CANDIDATE_ACCESS"
    )
    bindings = value["bindings"]
    assert bindings["p015_reference_sufficiency_result"]["file_sha256"] == (
        "sha256:5258606e1a97c0ba53702f233851391134d6bb1f722b90d82f87a140d2a67413"
    )
    assert bindings["p014_semantic_artifact"]["manifest_sha256"] == (
        "sha256:5926d73a04e37b74f7025c2f38314898ee7c5414aa45fe0686de20357ce3c388"
    )
    assert bindings["p015_additional_review_artifact"]["manifest_sha256"] == (
        "sha256:8219bb18b1c5c3236fa5e3dd1ec0ea5112dfc101e286d4dbdab1c72c3d3c8d59"
    )
    assert bindings["p014_review_ledger"]["sha256"] == (
        "sha256:f17063c28e0f75a8ae5280d6ee66b31f9b9ffc318896a3ae8b7e48efc101ed69"
    )
    assert bindings["p015_review_ledger"]["sha256"] == (
        "sha256:db3216d322e00ad40ebd7de435684b43bcaf72cbc66a90dcb49403a8be461e06"
    )
    assert value["reference_contract"]["combined_case_count"] == 252
    surface = value["candidate_surface"]
    assert surface["instability_candidate_count"] == 9
    assert surface["relocation_candidate_count"] == 1
    assert surface["order_profile_count"] == 3
    assert surface["joint_pair_search_count"] == 0
    assert surface["threshold_recompute"] is False
    assert surface["candidate_expansion"] is False
    aggregate = value["aggregate_contract"]
    assert aggregate["count_gates"] == "pooled_counts_exact_P012"
    assert aggregate["rate_gates"] == (
        "macro_cell_equal_weight_same_numeric_thresholds_as_P012"
    )
    assert aggregate["precision"]["zero_prediction_cell_value"] == "0"
    assert aggregate["positive_reference_coverage"]["mean_over"] == (
        "cells_with_at_least_one_definite_positive_reference"
    )
    assert aggregate["definite_negative_false_positive_rate"]["mean_over"] == (
        "cells_with_at_least_one_definite_negative_reference"
    )
    assert value["runtime"] == {
        "maximum_jobs": 1,
        "automatic_retries": 0,
        "worker_wall_seconds": 900,
    }
    authority = value["authority"]
    assert authority["candidate_scoring"] is True
    assert authority["projection_compatibility"] is False
    assert authority["dwd_market_read"] is False
    assert authority["reference_rewrite"] is False
    assert authority["candidate_surface_change"] is False
    assert authority["numeric_gate_change"] is False
    assert authority["preserved_2021_read"] is False
    assert authority["2025_read"] is False
    assert P016_WORK.is_file()
    assert P016_REVIEW.is_file()
    assert "PASS_SELECT_MACRO_RATE_POOLED_COUNT_TWO_JOB_SCORING_ROUTE" in P016_REVIEW.read_text(
        encoding="utf-8"
    )


def test_stage016_closes_semantic_no_pass_without_projection_or_retune() -> None:
    value = json.loads(P016_RESULT.read_text(encoding="utf-8"))
    assert value["work_item"] == "P3-STAGE-016-B"
    assert value["state"] == "CLOSED_INSTABILITY_DEVELOPMENT_FAIL"
    job = value["job"]
    assert job["job_id"] == "stage_e06cc35f8935424d9e906b9d456bbff2"
    assert job["terminal_state"] == "succeeded"
    assert job["semantic_disposition"] == "REVISE_OR_PARK_INSTABILITY_DEVELOPMENT_FAIL"
    surface = value["bindings"]["candidate_surface"]
    assert surface == {
        "instability_candidate_count": 9,
        "relocation_candidate_count": 1,
        "order_profile_count": 3,
        "joint_pair_search_count": 0,
        "candidate_surface_changed": False,
        "threshold_recomputed": False,
    }
    instability = value["instability_result"]
    assert instability["trial_count"] == 9
    assert instability["eligible_candidate_count"] == 0
    envelope = instability["envelope_channel_observation"]
    assert envelope["all_nine_trials_incremental_true_positive"] == 0
    assert envelope["all_nine_trials_incremental_false_positive"] == 0
    stop = value["stop_rule"]
    assert stop["instability_gate_passed"] is False
    assert stop["fixed_relocation_scored"] is False
    assert stop["order_profiles_scored"] is False
    assert stop["atomic_freeze_available"] is False
    assert stop["projection_compatibility_reached"] is False
    ceiling = value["authority_ceiling"]
    assert ceiling["same_job_retry"] is False
    assert ceiling["candidate_grid_expansion"] is False
    assert ceiling["threshold_retuning"] is False
    assert ceiling["numeric_gate_change"] is False
    assert ceiling["preserved_2021_read"] is False
    assert ceiling["2025_read"] is False
    assert value["successor"]["work_item"] == "P3-STAGE-017"
    assert value["successor"]["disposition"] == (
        "OPEN_ERA_BOARD_STRATUM_NORMALIZED_INSTABILITY_SUCCESSOR"
    )
    assert P016_NOPASS_REVIEW.is_file()
    assert "PASS_OPEN_P017_STRATUM_NORMALIZED_INSTABILITY_SUCCESSOR" in (
        P016_NOPASS_REVIEW.read_text(encoding="utf-8")
    )


def test_stage017_freezes_cell_local_unlabeled_instability_successor() -> None:
    value = json.loads(P017_CONFIG.read_text(encoding="utf-8"))
    assert value["work_item"] == "P3-STAGE-017-A"
    assert value["state"] == (
        "PREREGISTERED_ARTIFACT_ONLY_STRATUM_NORMALIZED_INSTABILITY_BEFORE_SCORING"
    )
    assert value["bindings"]["p016_result"]["state"] == (
        "CLOSED_INSTABILITY_DEVELOPMENT_FAIL"
    )
    surface = value["development_surface"]
    assert surface["case_count"] == 252
    assert surface["expected_cell_count"] == 8
    assert surface["new_identity_discovery"] is False
    assert surface["new_random_sampling"] is False
    assert surface["case_replacement"] is False
    normalization = value["normalization"]
    assert normalization["kind"] == "cell_local_unlabeled_quantile_basis"
    assert normalization["label_or_reference_visibility_during_basis_build"] is False
    assert normalization["candidate_output_visibility_during_basis_build"] is False
    assert normalization["cross_cell_pooling"] is False
    assert normalization["cross_cell_fallback"] is False
    candidates = value["candidate_surface"]
    assert candidates["candidate_count"] == 9
    assert candidates["new_profile_coordinates"] is False
    assert candidates["candidate_expansion"] is False
    envelope = value["envelope_successor_disposition"]
    assert envelope["p012_fixed_additive_channel"] == "RETIRED_FROM_P017_ELIGIBILITY"
    assert envelope["post_result_threshold_tuning"] is False
    assert envelope["new_envelope_candidate_search"] is False
    gate = value["quality_gate"]
    assert gate["minimum_predictions"] == 18
    assert gate["minimum_macro_precision"] == "0.78"
    assert gate["minimum_macro_positive_reference_coverage"] == "0.70"
    assert gate["maximum_macro_definite_negative_false_positive_rate"] == "0.10"
    assert gate["numeric_threshold_relaxation"] is False
    authority = value["authority"]
    assert authority["artifact_only_development_scoring"] is True
    assert authority["cell_local_unlabeled_basis_build"] is True
    assert authority["instability_candidate_scoring"] is True
    assert authority["dwd_market_read"] is False
    assert authority["fixed_relocation_scoring"] is False
    assert authority["order_scoring"] is False
    assert authority["projection_compatibility"] is False
    assert authority["preserved_2021_read"] is False
    assert authority["2025_read"] is False
    assert P017_WORK.is_file()
    assert "P3-STAGE-017" in P017_WORK.read_text(encoding="utf-8")


def test_stage017_recovery_is_exact_field_path_repair_only() -> None:
    value = json.loads(P017_RECOVERY.read_text(encoding="utf-8"))
    assert value["work_item"] == "P3-STAGE-017-B-R1"
    assert value["disposition"] == "AUTHORIZE_ONE_TECHNICAL_BINDING_RECOVERY"
    failed = value["failed_job"]
    assert failed["job_id"] == "stage_f670ede6347e4bd5921b66e61a034a9d"
    assert failed["code_revision"] == "10b998766acd72e27fa46b9ecd3fff4ecaf6a556"
    assert failed["artifact_published"] is False
    assert failed["candidate_scoring_reached"] is False
    assert failed["semantic_result_reached"] is False
    cause = value["root_cause"]
    assert cause["class"] == "IMPLEMENTATION_BINDING_FIELD_PATH"
    assert cause["observed_code_path"] == "p015_result['p012_atomic_reference_resolution']"
    assert cause["frozen_contract_path"] == "p015_result['combined_reference_resolution']"
    assert cause["config_change_required"] is False
    assert cause["resource_change_required"] is False
    authority = value["authority"]
    assert authority["maximum_technical_recovery_jobs"] == 1
    for key in (
        "same_p017_config_required",
        "same_252_case_surface_required",
        "same_review_ledgers_required",
        "same_eight_cell_normalization_required",
        "same_3x3_profile_coordinates_required",
        "same_quality_gates_required",
        "field_path_repair_only",
    ):
        assert authority[key] is True
    for key in (
        "resource_change",
        "reference_change",
        "normalization_change",
        "candidate_surface_change",
        "numeric_gate_change",
        "envelope_reactivation",
        "dwd_market_read",
        "preserved_2021_read",
        "2022_2024_read",
        "2025_read",
        "daily_or_strategy",
        "trading",
    ):
        assert authority[key] is False


def test_stage017_closes_no_pass_and_selects_channel_decomposition_diagnostic() -> None:
    value = json.loads(P017_RESULT.read_text(encoding="utf-8"))
    assert value["work_item"] == "P3-STAGE-017-B"
    assert value["state"] == "CLOSED_STRATUM_NORMALIZED_INSTABILITY_FAIL"
    recovery = value["technical_recovery"]
    assert recovery["job_id"] == "stage_5356bab390684fd9ae154e5d14b1a02b"
    assert recovery["technical_recovery_of"] == "stage_f670ede6347e4bd5921b66e61a034a9d"
    assert recovery["terminal_state"] == "succeeded"
    surface = value["development_surface"]
    assert surface == {
        "reference_case_count": 252,
        "cell_count": 8,
        "candidate_count": 9,
        "eligible_candidate_count": 0,
        "selected_profile_id": None,
        "prediction_artifact_available": False,
    }
    gate = value["frozen_quality_gate"]
    assert gate["minimum_macro_precision"] == "0.78"
    assert gate["minimum_macro_positive_reference_coverage"] == "0.70"
    assert gate["maximum_macro_definite_negative_false_positive_rate"] == "0.10"
    assert gate["gate_change"] is False
    trials = value["trial_summary"]
    assert len(trials) == 9
    assert all(item["eligible"] is False for item in trials)
    assert max(float(item["macro_positive_reference_coverage"]) for item in trials) > 0.70
    assert max(float(item["macro_precision"]) for item in trials) < 0.78
    assert min(float(item["macro_definite_negative_false_positive_rate"]) for item in trials) > 0.10
    ceiling = value["authority_ceiling"]
    assert ceiling["additional_recovery"] is False
    assert ceiling["profile_expansion"] is False
    assert ceiling["quantile_change"] is False
    assert ceiling["quality_gate_relaxation"] is False
    assert ceiling["dwd_market_read"] is False
    assert ceiling["preserved_2021_read"] is False
    assert ceiling["2025_read"] is False
    assert value["successor"]["work_item"] == "P3-STAGE-018"
    assert value["successor"]["disposition"] == (
        "OPEN_INSTABILITY_CHANNEL_DECOMPOSITION_DIAGNOSTIC"
    )
    assert P017_NOPASS_REVIEW.is_file()
    assert "PASS_OPEN_P018_INSTABILITY_CHANNEL_DECOMPOSITION_DIAGNOSTIC" in (
        P017_NOPASS_REVIEW.read_text(encoding="utf-8")
    )


def test_stage018_freezes_diagnostic_only_channel_decomposition() -> None:
    value = json.loads(P018_CONFIG.read_text(encoding="utf-8"))
    assert value["work_item"] == "P3-STAGE-018-A"
    assert value["state"] == "PREREGISTERED_ARTIFACT_ONLY_CHANNEL_DECOMPOSITION_BEFORE_EXECUTION"
    surface = value["diagnostic_surface"]
    assert surface["parent_proposition"] == "structural_instability_20"
    assert surface["channels"] == ["expanding_disorder_20", "broad_reversal_disorder_20"]
    assert surface["reference_case_count"] == 252
    assert surface["cell_count"] == 8
    assert surface["profile_count"] == 9
    assert surface["new_profile_coordinates"] is False
    assert surface["new_thresholds"] is False
    assert surface["threshold_recompute"] is False
    assert surface["new_semantic_labels"] is False
    metrics = value["metrics"]
    assert metrics["candidate_selection"] is False
    assert metrics["eligibility_gate"] is False
    assert metrics["successor_qualification"] is False
    authority = value["authority"]
    assert authority["artifact_only_diagnostic"] is True
    assert authority["channel_decomposition"] is True
    for key in (
        "candidate_scoring_for_selection",
        "candidate_reselection",
        "threshold_change",
        "new_reference_generation",
        "reference_rewrite",
        "dwd_market_read",
        "fixed_relocation_scoring",
        "order_scoring",
        "projection_compatibility",
        "preserved_2021_read",
        "2022_2024_read",
        "2025_read",
        "daily_or_strategy",
        "trading",
    ):
        assert authority[key] is False
    assert P018_WORK.is_file()


def test_stage018_result_attributes_parent_or_without_creating_child_truth() -> None:
    value = json.loads(P018_RESULT.read_text(encoding="utf-8"))
    assert value["work_item"] == "P3-STAGE-018-B"
    assert value["state"] == "PASS_CHANNEL_DECOMPOSITION_DIAGNOSTIC_COMPLETE"
    assert value["job"]["job_id"] == "stage_4e8eedc28b5542c798a8b33c8276755f"
    assert value["surface"] == {
        "reference_case_count": 252,
        "cell_count": 8,
        "profile_count": 9,
        "candidate_selection_performed": False,
        "new_semantic_labels_generated": False,
    }
    balanced = value["channel_summary"]["expansion_balanced"]
    assert float(balanced["macro_precision"]) > 0.78
    assert float(balanced["macro_positive_reference_coverage"]) < 0.70
    assert float(balanced["macro_definite_negative_false_positive_rate"]) < 0.10
    broad = value["channel_summary"]["broad_strict"]
    assert float(broad["macro_precision"]) < 0.78
    closest = value["closest_parent_profile_attribution"]
    assert closest["profile"] == "expansion-balanced/broad-strict"
    assert closest["expansion_only_true_positive"] == 8
    assert closest["expansion_only_false_positive"] == 2
    assert closest["broad_only_true_positive"] == 9
    assert closest["broad_only_false_positive"] == 14
    assert closest["overlap_true_positive"] == 6
    assert closest["overlap_false_positive"] == 0
    conclusion = value["diagnostic_conclusion"]
    assert conclusion["parent_or_composition_is_primary_quality_problem"] is True
    assert conclusion["drop_broad_and_apply_parent_coverage_gate_authorized"] is False
    assert conclusion["threshold_retuning_authorized"] is False
    ceiling = value["authority_ceiling"]
    assert ceiling["candidate_selection"] is False
    assert ceiling["new_semantic_labels"] is False
    assert ceiling["dwd_market_read"] is False
    assert ceiling["preserved_2021_read"] is False
    assert ceiling["2025_read"] is False
    assert value["successor"]["work_item"] == "P3-STAGE-019"
    assert value["successor"]["disposition"] == (
        "RECOMMEND_FRESH_BLIND_MECHANISM_REFERENCE_PILOT"
    )
    assert P018_REVIEW.is_file()
    assert "PASS_RECOMMEND_FRESH_BLIND_MECHANISM_REFERENCE_PILOT" in (
        P018_REVIEW.read_text(encoding="utf-8")
    )


def test_stage019_reanchors_third_group_and_parks_active_stage0_optimization() -> None:
    value = json.loads(P019_REBASELINE.read_text(encoding="utf-8"))
    assert value["work_item"] == "P3-STAGE-019"
    assert value["state"] == "PASS_REBASELINE_THIRD_GROUP_ROUTE"
    goal = value["parent_goal"]
    assert goal["program"] == "P3-STAGE-001"
    assert goal["third_group"] == ["0", "1", "2", "8", "9", "12"]
    assert goal["subgroups"] == {
        "0_2": "order_vs_disorder",
        "1_12": "downside_lifecycle",
        "8_9": "high_side_modifiers",
    }
    decisions = value["portfolio_decisions"]
    assert decisions["stage_0_parent"]["disposition"] == "PARK_PRIMARY_STAGE_QUALIFICATION"
    assert decisions["expansion_disorder_child"]["disposition"] == "RETAIN_RESEARCH_MECHANISM_ONLY"
    assert decisions["broad_reversal_child"]["disposition"] == (
        "REJECT_AS_CURRENT_ELIGIBILITY_CHANNEL"
    )
    assert decisions["stage_2_order"]["disposition"] == (
        "RETAIN_STRONG_EXPLORATORY_PROPOSITION"
    )
    assert decisions["stage_1_12"]["disposition"] == "NEXT_ACTIVE_THIRD_GROUP_ROUTE"
    assert decisions["stage_8_9"]["disposition"] == "PARKED_MODIFIER_ROUTE"
    superseded = value["superseded_active_recommendation"]
    assert superseded["source"] == "P3-STAGE-018-D"
    assert superseded["active_route"] == "DO_NOT_EXECUTE_NOW"
    assert value["successor"]["work_item"] == "P3-STAGE-020"
    ceiling = value["authority_ceiling"]
    assert ceiling["stage_0_reopen"] is False
    assert ceiling["preserved_2021_read"] is False
    assert ceiling["2025_read"] is False
    assert P019_REVIEW.is_file()
    assert P019_WORK.is_file()


def test_stage020_freezes_downside_context_and_nested_stabilization_before_search() -> None:
    value = json.loads(P020_CONCEPT.read_text(encoding="utf-8"))
    assert value["work_item"] == "P3-STAGE-020-A"
    assert value["state"] == (
        "PREREGISTERED_DOWNSIDE_LIFECYCLE_SEMANTICS_BEFORE_REFERENCE_OR_THRESHOLD_SEARCH"
    )
    props = value["propositions"]
    assert props["downside_context"]["stage_vocabulary_relation"] == "Stage_12_context_candidate"
    assert props["deceleration_stabilization"]["stage_vocabulary_relation"] == (
        "Stage_1_nested_condition_candidate"
    )
    assert props["deceleration_stabilization"]["requires_context"] == "downside_context"
    relation = value["relationship_contract"]
    assert relation["multi_proposition_first"] is True
    assert relation["primary_stage_compression"] is False
    assert relation["nested_condition_without_context"] == "NOT_APPLICABLE_OR_UNKNOWN_NOT_STAGE_1"
    assert relation["both_false"] == "NEITHER_IS_LEGAL"
    protocol = value["reference_protocol"]
    assert protocol["candidate_blind"] is True
    assert protocol["threshold_blind"] is True
    assert protocol["future_blind"] is True
    assert protocol["coexistence_preserved"] is True
    next_step = value["next_resource_step"]
    assert next_step["work_item"] == "P3-STAGE-020-B"
    assert next_step["reuse_resource_model"] == "P3-STAGE-013_ERA_BOARD_EXPOSURE_AWARE"
    assert next_step["require_61_contiguous_admitted_sessions"] is True
    authority = value["authority"]
    assert authority["threshold_search"] is False
    assert authority["new_market_read"] is False
    assert authority["review_opinion_collection"] is False
    assert authority["primary_stage_output"] is False
    assert authority["preserved_2021_read"] is False
    assert authority["2025_read"] is False
    assert P020_WORK.is_file()


def test_stage020b_freezes_fresh_exposure_aware_admission_plan_before_review() -> None:
    value = json.loads(P020_RESOURCE.read_text(encoding="utf-8"))
    assert value["work_item"] == "P3-STAGE-020-B"
    assert value["state"] == (
        "PREREGISTERED_RESOURCE_ADMISSION_AND_EXACT_PLAN_BEFORE_SEMANTIC_REVIEW"
    )
    consumed = value["consumption_ceiling"]
    assert consumed["exclude_p013_exact_plan_windows"] is True
    assert consumed["exclude_all_p014_admission_read_windows"] is True
    assert consumed["whole_identity_burn_down"] is False
    assert consumed["same_identity_disjoint_window_reuse_allowed"] is True
    assert consumed["preserved_2021_identity_global_exclusion"] is True
    screen = value["candidate_screen"]
    assert screen["maximum_candidate_identities_per_cell"] == 256
    assert screen["maximum_windows_per_identity"] == 4
    assert screen["candidate_order_uses_market_values"] is False
    assert screen["candidate_order_uses_semantic_results"] is False
    admission = value["admission_contract"]
    assert admission["required_sessions"] == 61
    assert admission["screen_only_input_readiness"] is True
    assert admission["raw_ohlc_published"] is False
    assert admission["semantic_label_or_reference_read"] is False
    selection = value["selection"]
    assert selection["reserve_per_cell"] == 32
    assert selection["primary_per_cell"] == 24
    assert selection["fallback_per_cell"] == 8
    assert selection["total_rows"] == 256
    assert selection["global_identity_unique"] is True
    assert selection["cross_cell_fill"] is False
    assert selection["post_result_replacement"] is False
    authority = value["authority"]
    assert authority["input_readiness_market_read"] is True
    for key in (
        "semantic_market_read",
        "structure_fact_build",
        "reference_opinion",
        "threshold_basis",
        "candidate_output",
        "candidate_scoring",
        "primary_stage_output",
        "preserved_2021_market_read",
        "2022_2024_read",
        "2025_read",
        "daily_or_strategy",
        "trading",
    ):
        assert authority[key] is False


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

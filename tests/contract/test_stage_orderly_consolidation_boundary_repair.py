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
EXECUTION = ROOT / "config/stage-learning/orderly-consolidation-joint-development-execution.v1.json"
EVIDENCE_SCOPE = ROOT / "config/stage-learning/order-reference-evidence-scope-2018.v1.json"
RECOVERY_AUTH = ROOT / (
    "config/stage-learning/orderly-consolidation-joint-d1-transport-recovery.v1.json"
)
RECOVERY_RESULT = ROOT / (
    "config/stage-learning/orderly-consolidation-joint-d1-recovery-result.v1.json"
)
D2_REVIEW_SEAL = ROOT / (
    "config/stage-learning/orderly-consolidation-joint-d2-review-seal.v1.json"
)
SELECTION_RESULT = ROOT / (
    "config/stage-learning/orderly-consolidation-joint-development-selection-result.v1.json"
)
FAILURE_DIAGNOSTIC = ROOT / (
    "config/stage-learning/orderly-consolidation-joint-development-failure-diagnostic.v1.json"
)
DIAGNOSTIC_DIGEST = "sha256:d34e9f7ad8a32969e28d2a1d5245caa089a52fb8a7891a8b8f03e69ce3fc3d1c"
DESIGN_DIGEST = "sha256:5d1e1ab3298df676829aff34a9c3b6117fa56053fb684f2c630ca04fbd380353"
PROJECTION_DIGEST = "sha256:11569752a0a3ae7c5a6c3f2ecf07b51710442c0f2445a848800203df3cda146c"
CONFORMANCE_DIGEST = "sha256:64741982652b0323a14dcd7c87c1486d7b8998fa085d5c190d3ca9a28a0c91d1"
PREREG_DIGEST = "sha256:c9b5eff892f3d731170afac052e3810543f7444f5b6025bdef24c1ebfa4c213e"
EXECUTION_DIGEST = "sha256:3424f673f11af42d37a454429fb9e624978b18799d10fea21c365f81c0c0d680"
EVIDENCE_SCOPE_DIGEST = "sha256:2400ccd1c025098b0a3d050aebf39cc0ebc7241289d9db2e8425819f9daf76c9"
RECOVERY_AUTH_DIGEST = "sha256:a2bbd496830adda80c13bf9a1d61baa0cf3dabcf6439cc53efcc45f6d5f11b2e"
RECOVERY_RESULT_DIGEST = "sha256:6ad3aebcda3c73b89e0ad3a94cdad89f78527cc1c9cd4506455fa17fd102bc8c"
D2_REVIEW_SEAL_DIGEST = "sha256:70a9f60426319331b3937991e560fd8a9c1ee6c5bdbd43a87d5cd1373f6606af"
SELECTION_RESULT_DIGEST = "sha256:9e3c61db83e5b9f6914dbab763c8597686f67d1527355b097020e91b690d0348"
FAILURE_DIAGNOSTIC_DIGEST = (
    "sha256:3518ba46bcd32cae6dc34b524e58dc43bb40a5cb54e329e1626371de586bda5a"
)


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
    d1 = value["d1_implementation_checkpoint"]
    assert d1["state"] == "RECOVERY_ARTIFACT_VERIFIED_D2_REVIEW_SEALED"
    assert d1["execution_contract_digest"] == EXECUTION_DIGEST
    assert digest(load(EXECUTION)) == EXECUTION_DIGEST
    assert d1["evidence_scope_digest"] == EVIDENCE_SCOPE_DIGEST
    assert digest(load(EVIDENCE_SCOPE)) == EVIDENCE_SCOPE_DIGEST
    assert d1["authorized_job_kind"] == (
        "quanthus.stage-orderly-consolidation-joint-d1-prepare.v1"
    )
    assert d1["maximum_result_bearing_jobs_now"] == 0
    assert d1["maximum_transport_recovery_jobs_remaining"] == 0
    assert d1["maximum_technical_retry_jobs_remaining"] == 0
    assert d1["semantic_retries_remaining"] == 0
    assert d1["automatic_supplement_rounds_remaining"] == 0
    assert d1["candidate_blind_review_required_before_scoring"] is True
    assert d1["candidate_pair_scoring_authorized_in_d1"] is False
    assert d1["preserved_2021_evaluation_read_authorized"] is False
    assert d1["failed_job"] == {
        "job_id": "stage_e73f7197d1f34b879d8b31e78db800ce",
        "code_revision": "e91dfbf77e9d62b66a2d11cf064b3fb0538331ec",
        "input_digest": "sha256:4a3dfd5da5da9b08c27ff7564ab725d5e968358568dcc2ce9b25e38049545a95",
        "state": "failed",
        "error": "stage_orderly_consolidation_joint_d1_process_failed",
        "artifact_published": False,
        "failure_class": "post_build_evidence_validator_ignored_signed_protocol_case_budget",
    }
    assert d1["transport_recovery_authorization_digest"] == RECOVERY_AUTH_DIGEST
    assert digest(load(RECOVERY_AUTH)) == RECOVERY_AUTH_DIGEST
    assert d1["recovery_result_digest"] == RECOVERY_RESULT_DIGEST
    assert digest(load(RECOVERY_RESULT)) == RECOVERY_RESULT_DIGEST
    assert d1["recovery_job"]["job_id"] == "stage_8189b79722fc48d1b774aa42f9238852"
    assert d1["recovery_job"]["development_selected_case_count"] == 190
    assert d1["recovery_job"]["development_shortfall_count"] == 2
    assert d1["recovery_job"]["review_ready"] is True
    for key in (
        "execution_contract",
        "evidence_scope_contract",
        "joint_development_implementation",
        "prepare_implementation",
        "worker_implementation",
        "acceptance",
        "transport_recovery_acceptance",
        "transport_recovery_authorization",
        "recovery_result",
    ):
        assert (ROOT / d1[key]).is_file()
    d2 = value["d2_review_checkpoint"]
    assert d2["state"] == "DEVELOPMENT_REVIEW_LEDGER_SEALED"
    assert d2["seal_digest"] == D2_REVIEW_SEAL_DIGEST
    assert digest(load(D2_REVIEW_SEAL)) == D2_REVIEW_SEAL_DIGEST
    assert d2["ledger_sha256"] == (
        "sha256:1a73c66dd1255970c3a8de81a92adfabe7e57fc668b8deab7a12ef3eb82092f1"
    )
    assert d2["case_count"] == 190
    assert d2["opinion_count"] == 380
    assert d2["candidate_pair_scoring_exact_frozen_grid_authorized"] is True
    assert d2["maximum_candidate_pairs"] == 27
    assert d2["candidate_grid_expansion_authorized"] is False
    assert d2["preserved_2021_evaluation_read_authorized"] is False
    selection = value["d2_selection_checkpoint"]
    assert selection["state"] == "REVISE_OR_PARK_DEVELOPMENT_FAIL"
    assert selection["result_digest"] == SELECTION_RESULT_DIGEST
    assert digest(load(SELECTION_RESULT)) == SELECTION_RESULT_DIGEST
    assert selection["failure_diagnostic_digest"] == FAILURE_DIAGNOSTIC_DIGEST
    assert digest(load(FAILURE_DIAGNOSTIC)) == FAILURE_DIAGNOSTIC_DIGEST
    assert selection["trial_count"] == 27
    assert selection["eligible_count"] == 0
    assert selection["pair_frozen"] is False
    assert selection["preserved_2021_evaluation_read"] is False
    assert value["delivery_sequence"] == {
        "P3-STAGE-011-A": "closed_design_approved",
        "P3-STAGE-011-B": "closed_engineering_accepted",
        "P3-STAGE-011-C": "closed_pass_topology_conformance_only",
        "P3-STAGE-011-D": "closed_revise_or_park_development_fail",
    }
    assert value["authority"] == {
        "implementation": False,
        "synthetic_conformance": False,
        "transport_recovery_only": False,
        "consumed_evidence_topology_diagnostic": False,
        "preregistration_design": False,
        "market_data_read": False,
        "authorized_market_data_scope": None,
        "runtime_job_submission": False,
        "authorized_runtime_kind": None,
        "candidate_blind_review": False,
        "candidate_pair_scoring": False,
        "maximum_candidate_pairs": 0,
        "candidate_grid_expansion": False,
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
        ROOT / "docs/acceptance/stage-orderly-consolidation-joint-d1-implementation-20260913.md",
        ROOT
        / "docs/acceptance/stage-orderly-consolidation-joint-d1-transport-recovery-20260913.md",
        ROOT
        / "docs/acceptance/stage-orderly-consolidation-joint-d1-recovery-result-20260913.md",
        ROOT
        / "docs/acceptance/stage-orderly-consolidation-joint-d2-review-seal-20260913.md",
        ROOT
        / "docs/acceptance/stage-orderly-consolidation-joint-d2-development-closeout-20260913.md",
    ):
        assert path.is_file()


def test_d1_transport_recovery_is_exactly_one_nonsemantic_budget_conformance_repair() -> None:
    value = load(RECOVERY_AUTH)
    assert digest(value) == RECOVERY_AUTH_DIGEST
    failed = value["failed_job"]
    assert failed["job_id"] == "stage_e73f7197d1f34b879d8b31e78db800ce"
    assert failed["code_revision"] == "e91dfbf77e9d62b66a2d11cf064b3fb0538331ec"
    assert failed["input_digest"] == (
        "sha256:4a3dfd5da5da9b08c27ff7564ab725d5e968358568dcc2ce9b25e38049545a95"
    )
    assert failed["terminal_state"] == "failed"
    assert failed["artifact_published"] is False
    defect = value["implementation_conformance_defect"]
    assert defect["protocol_digest"] == EVIDENCE_SCOPE_DIGEST
    assert defect["declared_max_cases"] == 192
    assert defect["validator_hardcoded_case_ceiling"] == 160
    frozen = value["frozen_parent_contract"]
    assert frozen["preregistration_digest"] == PREREG_DIGEST
    assert frozen["execution_digest"] == EXECUTION_DIGEST
    assert frozen["evidence_scope_digest"] == EVIDENCE_SCOPE_DIGEST
    assert frozen["candidate_pair_count"] == 27
    exposure = value["observed_result_exposure_before_recovery"]
    assert exposure["failed_job_market_data_read"] is True
    assert exposure["governed_artifact_published"] is False
    assert exposure["development_reference_opinions"] == 0
    assert exposure["candidate_pair_scores"] == 0
    assert exposure["candidate_pair_selected"] is False
    assert exposure["preserved_2021_evaluation_read"] is False
    authority = value["authority"]
    assert authority["maximum_transport_recovery_jobs"] == 1
    assert authority["counts_against_result_bearing_market_data_job_budget"] is False
    assert authority["parameter_search"] is False
    assert authority["semantic_retry"] is False
    assert authority["automatic_supplement"] is False
    assert authority["preserved_2021_evaluation_read"] is False


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

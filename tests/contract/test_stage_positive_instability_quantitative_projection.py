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


def test_d_preregistration_freezes_nine_candidates_before_evaluation_read() -> None:
    checkpoint = load(DELIVERY)["validation_preregistration"]
    assert checkpoint["state"] == "D2_REVIEW_LEDGER_SEALED_SCORE_READY"
    assert checkpoint["contract_digest"] == (
        "sha256:65dbe27838ce7721f5f3ca3e4a76f52b7a99cd7d819488e752e883adb5a05606"
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


def test_d1_checkpoint_opens_only_one_2019_prepare_job() -> None:
    delivery = load(DELIVERY)
    checkpoint = delivery["d1_implementation_checkpoint"]
    assert checkpoint["state"] == "RECOVERY_ARTIFACT_INDEPENDENTLY_VERIFIED"
    assert checkpoint["execution_contract_digest"] == (
        "sha256:7e59a3fcadfab30369f66b214a82da1e2a38bae33e5e3c5dd80f35f7e7cd5e6f"
    )
    assert checkpoint["authorized_job_kind"] == (
        "quanthus.stage-positive-instability-quantitative-d1-prepare.v1"
    )
    assert checkpoint["maximum_result_bearing_jobs_now"] == 0
    assert checkpoint["maximum_technical_retry_jobs_remaining"] == 0
    assert checkpoint["maximum_transport_recovery_jobs_remaining"] == 0
    assert checkpoint["semantic_retries_remaining"] == 0
    assert checkpoint["technical_correction"] == {
        "field": "source_isolation.candidate_cohort_date",
        "old_value": "2018-12-31",
        "new_value": "2018-12-28",
    }
    assert checkpoint["failed_job"]["job_id"] == "stage_b5e3feae8b9f47faae7566c308444b3c"
    assert checkpoint["failed_job"]["artifact_published"] is False
    assert checkpoint["retry_authorization_digest"] == (
        "sha256:ae2028fa9f3e6bae5725cb86494f0abc98a298085c4e39a12f1d34c3c6ebd08a"
    )
    assert checkpoint["failed_retry_job"]["job_id"] == (
        "stage_2612f623a94b44ba86ed71128d16bf48"
    )
    assert checkpoint["failed_retry_job"]["artifact_published"] is False
    assert checkpoint["transport_recovery_authorization_digest"] == (
        "sha256:9962922fb4ba7ada5e4041c86f01f919fa40969ee5e5a5061c5a0c6440d7a1b1"
    )
    assert checkpoint["evidence_scope_digest"] == (
        "sha256:2d65ce22666a7709a0f67e3d708df085bb624505484bddeec62a7de9b6ecaa0d"
    )
    assert digest(load(ROOT / checkpoint["transport_recovery_authorization"])) == (
        checkpoint["transport_recovery_authorization_digest"]
    )
    assert digest(load(ROOT / checkpoint["evidence_scope_contract"])) == (
        checkpoint["evidence_scope_digest"]
    )
    assert checkpoint["evidence_transport_correction"] == {
        "defect": "order_reference_protocol_v1_ignored_declared_time_scope_fields",
        "history_start": "2018-09-01",
        "case_start": "2019-01-01",
        "development_end": "2019-12-31",
    }
    assert checkpoint["authorized_market_data_scope"] == {
        "warmup_start": "2018-09-01",
        "development_start": "2019-01-01",
        "development_end": "2019-12-31",
    }
    assert checkpoint["candidate_blind_review_authorized"] is True
    assert checkpoint["parameter_search_authorized"] is False
    assert checkpoint["preserved_2021_evaluation_read_authorized"] is False
    recovery = load(ROOT / checkpoint["recovery_result"])
    assert digest(recovery) == checkpoint["recovery_result_digest"]
    assert recovery["artifact"]["verified"] is True
    assert recovery["artifact"]["development_selected_case_count"] == 160
    assert recovery["artifact"]["candidate_count"] == 9
    assert recovery["artifact"]["opinion_count"] == 0
    assert recovery["artifact"]["candidate_frozen"] is False
    assert recovery["preserved_evaluation"]["market_data_read"] is False
    for key in (
        "execution_contract",
        "development_implementation",
        "prepare_implementation",
        "worker_implementation",
        "acceptance",
        "retry_acceptance",
        "retry_authorization",
        "transport_recovery_acceptance",
        "transport_recovery_authorization",
        "evidence_scope_contract",
    ):
        assert (ROOT / checkpoint[key]).is_file()


def test_d2_review_is_sealed_before_exact_candidate_scoring() -> None:
    checkpoint = load(DELIVERY)["d2_review_checkpoint"]
    assert checkpoint["state"] == "DEVELOPMENT_REVIEW_LEDGER_SEALED"
    assert checkpoint["ledger_sha256"] == (
        "sha256:e9d93b43903f8753ad8d7f8859493b8a279ec6aad4101856ebeb4d2bcb5e204e"
    )
    assert checkpoint["case_count"] == 160
    assert checkpoint["opinion_count"] == 320
    assert checkpoint["role_disagreement_count"] == 19
    assert checkpoint["candidate_output_seen_during_review"] is False
    assert checkpoint["threshold_basis_seen_during_review"] is False
    assert checkpoint["preserved_2021_evaluation_seen"] is False
    assert checkpoint["candidate_scoring_exact_frozen_grid_authorized"] is True
    assert checkpoint["maximum_candidate_trials"] == 9
    assert checkpoint["candidate_grid_expansion_authorized"] is False
    assert checkpoint["preserved_2021_evaluation_read_authorized"] is False
    seal = load(ROOT / checkpoint["seal"])
    assert digest(seal) == checkpoint["seal_digest"]
    assert seal["review"]["immutable_snapshot_artifact_id"] == (
        "d071b4d27a5f23a2a522c94ce0790dfc"
    )
    assert seal["review"]["ledger_bytes"] == 36197


def test_current_authority_is_exact_nine_scoring_only() -> None:
    authority = load(DELIVERY)["authority"]
    assert authority == {
        "implementation": True,
        "runtime_job_submission": False,
        "authorized_runtime_kind": None,
        "market_data_read": False,
        "authorized_market_data_scope": None,
        "candidate_blind_review": False,
        "candidate_scoring_exact_frozen_grid": True,
        "maximum_candidate_trials": 9,
        "candidate_grid_expansion": False,
        "transport_recovery_only": False,
        "parameter_search": False,
        "preserved_2021_evaluation_read": False,
        "2022_2024_access": False,
        "2025_access": False,
        "official_labels": False,
        "daily_or_strategy": False,
        "trading": False,
    }

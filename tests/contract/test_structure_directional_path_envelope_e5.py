from __future__ import annotations

import json
from pathlib import Path

from quanthus.policy import find_project_root
from quanthus.structure_v2_inputs import digest

ROOT = find_project_root()
AUTH = ROOT / "config/structure-directional-path-envelope-e5-authorization.v1.json"
DESIGN = ROOT / "config/structure-directional-path-envelope-extension.v1.json"
SYNTHETIC = ROOT / "config/structure-directional-path-envelope-synthetic-contract.v1.json"
RESULT = ROOT / "config/structure-directional-path-envelope-synthetic-result.v1.json"
E5_RESULT = ROOT / "config/structure-directional-path-envelope-e5-result.v1.json"
B6_PATH = ROOT / "config/structure-directional-path-geometry.v1.json"
CATALOG = ROOT / "config/structure-semantic-catalog.v1.json"

AUTH_DIGEST = "sha256:634136cc9df451b8590626a0d2a57e7bb76e0452f3dbe9d333ddd0970bc25430"
DESIGN_DIGEST = "sha256:22d1031e547dec1db5a5d620b12949cc4a856537569c03d495d04cc4e559a71a"
SYNTHETIC_DIGEST = "sha256:4dcef798bc12cabf4cc0f0e67c016bc93eb504f1f9cf1bbacb9f8d04d6c6e0ea"
RESULT_DIGEST = "sha256:413dd2256158f7e9eb68ee17711832070581af04f930c47bbd60f7ab2db39f9e"
E5_RESULT_DIGEST = "sha256:5ea1b5bfc64af7c63b80fa767a60e3656b335dc344d4e766fef13a7419171241"
B6_PATH_DIGEST = "sha256:d944a1c503b0d6bb71dfae247c94378361f1a4100174a6da93c944cc2cc1030c"
CONCEPT = "structure.geometry.close-envelope-resolution-trace"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_e5_authority_is_structure_design_and_synthetic_only():
    value = load(AUTH)
    assert digest(value) == AUTH_DIGEST
    assert value["state"] == "OWNER_APPROVED_STRUCTURE_DESIGN_AND_SYNTHETIC_ONLY"
    assert value["existing_owner_binding"]["blueprint_id"] == "B6-PATH"
    assert value["authorized"] == {
        "structure_owned_design": True,
        "structure_owned_threshold_free_prototype": True,
        "synthetic_conformance": True,
        "stage_prototype_compatibility_refactor": True,
    }
    assert all(value["forbidden"].values())


def test_e5_design_freezes_one_neutral_trace_not_descriptor_fact_sprawl():
    value = load(DESIGN)
    assert digest(value) == DESIGN_DIGEST
    assert value["state"] == "DESIGN_FROZEN_SYNTHETIC_ONLY_NOT_REGISTERED"
    assert value["owner"]["blueprint_id"] == "B6-PATH"
    assert value["concept_proposal"] == {
        "concept_id": CONCEPT,
        "registration_status": "PROPOSED_NOT_REGISTERED",
        "result_kind": "structured_path_trace",
        "concept_version": "0.1.0-synthetic",
        "summary_fields_are_separate_fact_identities": False,
    }
    spec = value["evaluation_spec"]
    assert spec["primary_path_sessions"] == 20
    assert spec["required_contiguous_closes"] == 21
    assert spec["prior_close_envelope_sessions"] == 10
    assert spec["future_after_T_used"] is False
    assert spec["numeric_threshold_learning"] is False
    assert value["identity_proposal"]["event_components_are_independent_backdated_facts"] is False
    authority = value["authority"]
    assert authority["structure_owned_prototype_implementation"] is True
    assert authority["synthetic_conformance"] is True
    for key in (
        "existing_b6_path_contract_change",
        "semantic_catalog_registration",
        "fact_registration",
        "market_data_read",
        "runtime_job",
        "threshold_search",
        "candidate_generation",
        "stage_projection",
        "preserved_2021_evaluation_read",
        "daily_or_strategy",
        "trading",
    ):
        assert authority[key] is False


def test_synthetic_contract_and_result_are_bound_and_pass():
    contract = load(SYNTHETIC)
    result = load(RESULT)
    assert digest(contract) == SYNTHETIC_DIGEST
    assert digest(result) == RESULT_DIGEST
    assert contract["design_binding"]["digest"] == DESIGN_DIGEST
    assert result["state"] == "PASS_SYNTHETIC_QUALIFICATION"
    assert result["design_contract_digest"] == DESIGN_DIGEST
    assert result["synthetic_contract_digest"] == SYNTHETIC_DIGEST
    assert result["failed_checks"] == []
    assert all(result["checks"].values())
    assert len(result["cases"]) == 9
    assert result["checks"]["compatibility:stage_e2:failed_resolution"] is True
    assert result["checks"]["invariance:positive_price_scale"] is True
    assert result["checks"]["pit:T_escape_unresolved"] is True
    assert result["checks"]["segment:listing_or_coordinate_rewarm"] is True
    assert result["checks"]["segment:hidden_gap_rewarm"] is True
    assert all(value is False for value in result["authority"].values())


def test_e5_closeout_is_ready_for_review_but_opens_no_registration_or_market_authority():
    value = load(E5_RESULT)
    assert digest(value) == E5_RESULT_DIGEST
    assert value["state"] == "READY_FOR_POST_E5_REVIEW"
    assert value["synthetic"]["state"] == "PASS_SYNTHETIC_QUALIFICATION"
    assert value["synthetic"]["failed_check_count"] == 0
    assert value["ownership"]["blueprint_id"] == "B6-PATH"
    assert value["ownership"]["structure_detector_is_single_algorithm_core"] is True
    assert value["registration"] == {
        "existing_b6_path_contract_changed": False,
        "semantic_catalog_changed": False,
        "proposed_concept_registered": False,
        "blueprint_concept_inventory_changed": False,
    }
    assert value["next_gate"]["work_item"] == "P3-STAGE-012-E6"
    assert value["next_gate"]["ready_for_review"] is True
    assert value["next_gate"]["registration_authorized"] is False
    assert value["next_gate"]["market_data_authorized"] is False
    for key in (
        "fact_registration",
        "semantic_catalog_change",
        "runtime_integration",
        "market_data_read",
        "threshold_search",
        "candidate_generation",
        "fresh_cohort_inventory",
        "preserved_2021_evaluation_read",
        "daily_or_strategy",
        "trading",
    ):
        assert value["authority"][key] is False


def test_existing_b6_path_and_catalog_remain_unmodified_by_e5_registration():
    b6 = load(B6_PATH)
    assert digest(b6) == B6_PATH_DIGEST
    assert set(b6["concepts"]) == {
        "structure.geometry.range-traversal",
        "structure.geometry.counter-direction-excursion-share",
    }
    catalog = CATALOG.read_text(encoding="utf-8")
    assert CONCEPT not in catalog


def test_stage_e2_now_uses_structure_owner_for_core_detection():
    source = (
        ROOT / "src/quanthus/stage_learning/range_envelope_prototype.py"
    ).read_text(encoding="utf-8")
    assert "from quanthus.structure_directional_path_envelope import" in source
    assert "detect_close_envelope_events" in source
    assert "for index in range(PRIOR_ENVELOPE_SESSIONS" not in source


def test_e5_documentation_nodes_exist():
    for path in (
        ROOT / "docs/design/structure-directional-path-envelope-extension.md",
        ROOT
        / "docs/acceptance/structure-directional-path-envelope-e5-synthetic-acceptance-20260914.md",
    ):
        assert path.is_file()

from __future__ import annotations

import json
from pathlib import Path

from quanthus.policy import find_project_root
from quanthus.structure_v2_inputs import digest

ROOT = find_project_root()
AUTH = ROOT / "config/structure-directional-path-envelope-e7-authorization.v1.json"
REP = ROOT / "config/structure-directional-path-envelope-catalog-representation.v1.json"
RESULT = ROOT / "config/structure-directional-path-envelope-e7-representation-result.v1.json"
B6_PATH = ROOT / "config/structure-directional-path-geometry.v1.json"

AUTH_DIGEST = "sha256:2ac2219402151cca13c6ff805ba32fd633bf198c5f55d0629332b687203d8225"
REP_DIGEST = "sha256:439653ff774e56dd05c79957b003b01e14022f8466cb8bd92140026784ad1beb"
RESULT_DIGEST = "sha256:60e8961761c6f70661fb2faafa53fc2447ac6d66d7d13d518520f1529be2855e"
CATALOG_DIGEST = "sha256:86bf671c163c69002330102e15b120cd3883b7064d32d806c6f30680082e3a4d"
B6_PATH_DIGEST = "sha256:d944a1c503b0d6bb71dfae247c94378361f1a4100174a6da93c944cc2cc1030c"
CONCEPT_ID = "structure.geometry.close-envelope-resolution-trace"


def load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_e7_authority_is_representation_only() -> None:
    value = load(AUTH)
    assert digest(value) == AUTH_DIGEST
    assert value["state"] == "OWNER_APPROVED_SYNTHETIC_REPRESENTATION_REVISION_ONLY"
    assert value["authority"]["representation_adapter_implementation"] is True
    assert value["authority"]["synthetic_conformance"] is True
    for key in (
        "catalog_registration",
        "catalog_type_system_change",
        "active_b6_path_contract_change",
        "algorithm_change",
        "profile_change",
        "market_data_read",
        "runtime_job",
        "threshold_search",
        "candidate_generation",
        "preserved_2021_evaluation_read",
        "daily_or_strategy",
        "trading",
    ):
        assert value["authority"][key] is False


def test_e7_selects_existing_measurement_kind_without_registration() -> None:
    value = load(REP)
    assert digest(value) == REP_DIGEST
    assert value["state"] == "CATALOG_FACING_MEASUREMENT_PROPOSAL_NOT_REGISTERED"
    assert value["concept"] == {
        "concept_id": CONCEPT_ID,
        "concept_version": "0.2.0-synthetic",
        "result_kind": "measurement",
        "registration_status": "PROPOSED_NOT_REGISTERED",
        "meaning": value["concept"]["meaning"],
    }
    decision = value["representation_decision"]
    assert decision["selected_kind"] == "measurement"
    assert value["measurement_value_schema"]["events_are_independent_registered_facts"] is False
    assert (
        value["measurement_value_schema"]["descriptors_are_independent_registered_facts"]
        is False
    )
    assert value["compatibility"]["e5_algorithm_unchanged"] is True
    assert value["compatibility"]["e5_profile_unchanged"] is True
    assert value["compatibility"]["semantic_catalog_unchanged"] is True


def test_e7_frozen_result_records_pre_registration_catalog_state() -> None:
    frozen = load(RESULT)
    assert digest(frozen) == RESULT_DIGEST
    assert frozen["state"] == "PASS_CATALOG_FACING_MEASUREMENT_REPRESENTATION"
    assert frozen["failed_checks"] == []
    assert all(frozen["checks"].values())
    assert frozen["active_catalog_digest"] == CATALOG_DIGEST
    assert frozen["active_b6_path_digest"] == B6_PATH_DIGEST
    assert frozen["checks"]["catalog:concept_still_unregistered"] is True
    assert digest(load(B6_PATH)) == B6_PATH_DIGEST


def test_e7_proposal_preserves_owned_geometry_family_before_registration() -> None:
    value = load(REP)
    assert value["owner"]["semantic_family"] == "structure.geometry"
    assert value["concept"]["concept_id"] == CONCEPT_ID
    assert value["concept"]["registration_status"] == "PROPOSED_NOT_REGISTERED"


def test_e7_next_gate_is_review_only() -> None:
    value = load(RESULT)
    assert value["next_gate"] == {
        "work_item": "P3-STAGE-012-E8",
        "state": "READY_FOR_REGISTRATION_DECISION_REVIEW_ONLY",
        "purpose": value["next_gate"]["purpose"],
    }
    for key in (
        "catalog_registration",
        "catalog_type_system_change",
        "active_b6_path_contract_change",
        "algorithm_change",
        "market_data_read",
        "runtime_job",
        "threshold_search",
        "candidate_generation",
        "preserved_2021_evaluation_read",
    ):
        assert value["authority"][key] is False

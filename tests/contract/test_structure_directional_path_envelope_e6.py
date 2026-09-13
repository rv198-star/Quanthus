from __future__ import annotations

import json
from pathlib import Path

from quanthus.structure_semantics import load_semantic_catalog
from quanthus.structure_v2_inputs import digest

ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / "config/structure-directional-path-envelope-e6-review-result.v1.json"
RESULT_DIGEST = "sha256:871c1fabf860a15d630dbaa80ea173160dcf191faab6b78a2d6a57fe13647b42"
CATALOG = ROOT / "config/structure-semantic-catalog.v1.json"


def load() -> dict[str, object]:
    return json.loads(RESULT.read_text(encoding="utf-8"))


def test_e6_review_selects_exactly_revise_neutral_concept() -> None:
    value = load()
    assert digest(value) == RESULT_DIGEST
    assert value["state"] == "REVISE_NEUTRAL_CONCEPT"
    decision = value["decision"]
    assert decision["selected_route"] == "REVISE_NEUTRAL_CONCEPT"
    assert set(decision["rejected_routes"]) == {
        "REGISTER_AS_B6_PATH_EXTENSION",
        "KEEP_RESEARCH_ONLY",
        "PARK",
    }
    assert decision["b6_path_owner"] == "RETAIN"
    assert decision["e5_algorithm_semantics"] == "RETAIN"
    assert decision["active_b6_path_contract"] == "UNCHANGED"
    assert decision["semantic_catalog"] == "UNCHANGED"


def test_e6_identifies_catalog_result_kind_mismatch_without_mutation() -> None:
    value = load()
    facts = value["catalog_facts"]
    assert facts["catalog_authority"] == "naming_and_boundary_contract_only"
    assert facts["allowed_result_kinds"] == [
        "reference",
        "measurement",
        "event",
        "relation",
        "process",
    ]
    assert facts["e5_proposed_result_kind"] == "structured_path_trace"
    assert facts["e5_result_kind_is_catalog_compatible"] is False
    catalog = load_semantic_catalog(CATALOG)
    assert all(
        item.concept_id != "structure.geometry.close-envelope-resolution-trace"
        for item in catalog.concepts
    )


def test_e6_opens_only_owner_gated_synthetic_representation_revision() -> None:
    value = load()
    next_phase = value["next_phase"]
    assert next_phase["work_item"] == "P3-STAGE-012-E7"
    assert next_phase["status"] == "BLOCKED_PENDING_OWNER_APPROVAL"
    assert "do not add a new semantic catalog result kind" in next_phase["constraints"]
    authority = value["authority"]
    assert authority["review_completed"] is True
    assert authority["route_selected"] is True
    for key in (
        "catalog_registration",
        "catalog_type_system_change",
        "active_b6_path_contract_change",
        "algorithm_change",
        "threshold_search",
        "candidate_generation",
        "runtime_job",
        "market_data_read",
        "preserved_2021_evaluation_read",
        "daily_or_strategy",
        "trading",
    ):
        assert authority[key] is False


def test_e6_review_document_is_bound_and_non_mutating() -> None:
    review = (
        ROOT
        / "docs/reviews/structure-directional-path-envelope-e6-registration-review-20260914.md"
    )
    text = review.read_text(encoding="utf-8")
    assert "SELECTED: REVISE_NEUTRAL_CONCEPT" in text
    assert "structured_path_trace" in text
    assert "P3-STAGE-012-E7: BLOCKED PENDING OWNER APPROVAL" in text
    assert "SEMANTIC CATALOG: UNCHANGED" in text

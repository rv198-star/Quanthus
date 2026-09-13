from __future__ import annotations

import json
from pathlib import Path

from quanthus.structure_v2_inputs import digest

ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / "config/structure-directional-path-envelope-e8-review-result.v1.json"
RESULT_DIGEST = "sha256:7dd02799c0fba3e6f34d7ad84023ab49394cc9609b90aa448c63eec5d99b49d9"
CONCEPT_ID = "structure.geometry.close-envelope-resolution-trace"


def load() -> dict[str, object]:
    return json.loads(RESULT.read_text(encoding="utf-8"))


def test_e8_selects_reserved_measurement_registration_only() -> None:
    value = load()
    assert digest(value) == RESULT_DIGEST
    assert value["state"] == "REGISTER_RESERVED_MEASUREMENT"
    decision = value["decision"]
    assert decision["selected_route"] == "REGISTER_RESERVED_MEASUREMENT"
    assert decision["concept_id"] == CONCEPT_ID
    assert decision["result_kind"] == "measurement"
    assert decision["registration_value"] == "reserved"
    assert decision["active_b6_path_contract"] == "KEEP_FROZEN_AT_P008_DIGEST"
    assert decision["e5_algorithm"] == "KEEP_UNCHANGED"
    assert decision["e7_representation"] == "KEEP_UNCHANGED"
    assert decision["p008_relocation"] == "KEEP_FROZEN"
    assert decision["preserved_2021"] == "UNREAD_AND_CLOSED"


def test_e8_uses_reserved_registration_precedent_without_runtime_promotion() -> None:
    value = load()
    precedent = value["registration_precedent"]
    assert precedent["work_item"] == "P3-STAGE-008-B"
    assert precedent["catalog_registration_implies_runtime_qualification"] is False
    assert "zero market-data read" in precedent["precedent"]
    assert "runtime qualification remained separately gated" in precedent["precedent"]


def test_e8_review_records_pre_registration_catalog_and_opens_only_e9() -> None:
    value = load()
    assert value["review_basis"]["semantic_catalog_digest"] == (
        "sha256:86bf671c163c69002330102e15b120cd3883b7064d32d806c6f30680082e3a4d"
    )
    next_phase = value["next_phase"]
    assert next_phase["work_item"] == "P3-STAGE-012-E9"
    assert next_phase["status"] == "BLOCKED_PENDING_OWNER_APPROVAL"
    assert any(
        "semantic-catalog.v1.json" in item
        for item in next_phase["allowed_mutations_if_approved"]
    )
    assert any(
        "do not add a runtime accepted semantic binding" in item
        for item in next_phase["forbidden"]
    )

    authority = value["authority"]
    assert authority["review_completed"] is True
    assert authority["route_selected"] is True
    for key in (
        "catalog_registration_performed",
        "active_b6_path_contract_change",
        "runtime_binding",
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


def test_e8_review_document_records_claim_ceiling_and_next_gate() -> None:
    review = ROOT / (
        "docs/reviews/structure-directional-path-envelope-e8-registration-review-20260914.md"
    )
    text = review.read_text(encoding="utf-8")
    assert "SELECTED: REGISTER_RESERVED_MEASUREMENT" in text
    assert "registration: reserved" in text
    assert "Reserved registration means only" in text
    assert "E9: BLOCKED PENDING OWNER APPROVAL" in text
    assert "must not create a runtime binding" in text.lower()

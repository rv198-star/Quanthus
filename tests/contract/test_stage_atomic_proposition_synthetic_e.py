from __future__ import annotations

import json
from pathlib import Path

from quanthus.policy import find_project_root
from quanthus.structure_v2_inputs import canonical, digest
from scripts.run_stage_atomic_envelope_synthetic_conformance import run

ROOT = find_project_root()
AUTH = ROOT / "config/stage-learning/stage-atomic-proposition-e-synthetic-authorization.v1.json"
ATOMIC = ROOT / "config/stage-learning/stage-atomic-primary-review-contract.v1.json"
ENVELOPE = ROOT / "config/stage-learning/stage-range-envelope-prototype.v1.json"
SYNTHETIC = ROOT / "config/stage-learning/stage-atomic-envelope-synthetic-contract.v1.json"
RESULT = ROOT / "config/stage-learning/stage-atomic-envelope-synthetic-conformance-result.v1.json"
E_RESULT = ROOT / "config/stage-learning/stage-atomic-proposition-e-synthetic-result.v1.json"

AUTH_DIGEST = "sha256:c617f21613ce506287753c9da572c3793ddb0139385bddc96b2cbf309f1bacb6"
ATOMIC_DIGEST = "sha256:74900cfde3d7e86744cab8621a3fd14710fd3fd900fa18f53ef9a98ec750a80c"
ENVELOPE_DIGEST = "sha256:bf34c7790d8341826cc94521d7e5d1c2e6004af172d983d6f4d6ff6d167c4a6f"
SYNTHETIC_DIGEST = "sha256:df82e410efde252c48b3a94d9552f19c20d368a17ee9cdc09b2822486b9e2dcb"
RESULT_DIGEST = "sha256:ee09715c69b2e12485ec841af6a96d74370a96b584d7fa9df637e42798820b0f"
E_RESULT_DIGEST = "sha256:1276254b1274ef4db0421067399b6afe4d43bf24e810dda404fdef2f9fa03036"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_e_authorization_is_synthetic_only_and_preserves_closed_boundaries() -> None:
    value = load(AUTH)
    assert digest(value) == AUTH_DIGEST
    assert value["state"] == "OWNER_APPROVED_SYNTHETIC_ONLY"
    assert value["parent_review"]["digest"] == (
        "sha256:0b00dd0a327b161cd20a4122e5c5a8cad806ad6f9f5713246b36b412a3fb6af7"
    )
    authority = value["authority"]
    assert authority["implementation"] is True
    assert authority["synthetic_conformance"] is True
    for key in (
        "modify_historical_p008_p011_artifacts",
        "p008_relocation_retune",
        "threshold_search",
        "candidate_generation",
        "new_fact_registration",
        "new_fact_qualification",
        "runtime_job",
        "new_market_data_read",
        "preserved_2021_evaluation_read",
        "2022_2025_access",
        "daily_or_strategy",
        "trading",
    ):
        assert authority[key] is False


def test_e1_freezes_atomic_first_output_without_reimplementing_historical_protocol() -> None:
    value = load(ATOMIC)
    assert digest(value) == ATOMIC_DIGEST
    assert value["reuses"]["protocol_digest"] == (
        "sha256:8db37fb3d197668cfe16f9daabaa57c625617460be94ace13a28ccb81e89c88b"
    )
    assert value["primary_propositions"] == [
        "structural_instability_20",
        "coherent_directional_relocation_20",
        "orderly_consolidation_20",
    ]
    required = value["required_output"]
    assert required["preserve_role_atomic_truths"] is True
    assert required["preserve_mixed_directional_instability_roles"] is True
    assert required["project_only_after_atomic_commit"] is True
    assert value["projection"]["TRUE_TRUE_FALSE"] == (
        "NEITHER_WITH_MIXED_DIRECTIONAL_INSTABILITY"
    )


def test_e2_is_a_threshold_free_non_fact_prototype() -> None:
    value = load(ENVELOPE)
    assert digest(value) == ENVELOPE_DIGEST
    assert value["state"] == "SYNTHETIC_ONLY_NOT_A_FACT"
    assert value["profile"]["primary_history_sessions"] == 21
    assert value["profile"]["prior_close_envelope_sessions"] == 10
    assert value["profile"]["future_beyond_T"] is False
    nonclaims = value["explicit_non_claims"]
    assert set(nonclaims.values()) == {False}
    assert value["authority"]["market_data"] is False
    assert value["authority"]["threshold_search"] is False
    assert value["authority"]["new_fact_registration"] is False


def test_e3_synthetic_contract_and_result_are_exactly_reproducible() -> None:
    contract = load(SYNTHETIC)
    result = load(RESULT)
    assert digest(contract) == SYNTHETIC_DIGEST
    assert digest(result) == RESULT_DIGEST
    assert result["contract_digest"] == SYNTHETIC_DIGEST
    assert result["state"] == "PASS_SYNTHETIC_CONFORMANCE"
    assert result["failed_checks"] == []
    assert canonical(result) == canonical(run(ROOT))
    by_id = {item["id"]: item for item in result["envelope_results"]}
    assert by_id["orderly-high-churn-contraction"]["escape_count"] == 0
    assert by_id["stable-narrow-noise"]["escape_count"] == 0
    assert by_id["one-leg-repricing-then-hold"]["unresolved_escape_sides"] == ["UPPER"]
    compact = by_id["compact-failed-resolution-disorder"]
    assert compact["resolved_escape_side_sequence"] == ["UPPER", "LOWER", "UPPER", "LOWER"]
    assert compact["resolved_escape_side_alternation_count"] == 3
    assert compact["resolved_escape_both_sides"] is True
    assert by_id["unavailable-close"]["status"] == "UNAVAILABLE"
    mixed = next(
        item
        for item in result["atomic_results"]
        if item["id"] == "mixed-directional-instability"
    )
    assert mixed["projected_primary_reference"] == "NEITHER"
    assert mixed["mixed_directional_instability_roles"] == ["fund", "short_term"]


def test_e_result_claim_ceiling_opens_no_market_or_fact_authority() -> None:
    result = load(RESULT)
    assert result["authority"] == {
        "market_data_read": False,
        "preserved_2021_evaluation_read": False,
        "threshold_search": False,
        "candidate_generation": False,
        "new_fact_registration": False,
        "p008_relocation_change": False,
    }
    assert "Synthetic-only" in result["claim_ceiling"]


def test_e_closeout_opens_only_post_e_review() -> None:
    value = load(E_RESULT)
    assert digest(value) == E_RESULT_DIGEST
    assert value["state"] == "READY_FOR_POST_E_REVIEW"
    assert value["E1"]["historical_protocol_modified"] is False
    assert value["E2"]["registered_fact"] is False
    assert value["E2"]["numeric_threshold_selected"] is False
    assert value["E3"]["state"] == "PASS_SYNTHETIC_CONFORMANCE"
    assert value["next_gate"] == {
        "work_item": "P3-STAGE-012-E4",
        "purpose": (
            "Post-E expert review to decide whether to formalize a threshold-free Fact/projection "
            "repair, retain the prototype as research-only, or park it."
        ),
        "status": "READY_FOR_REVIEW_ONLY",
    }
    authority = value["authority"]
    assert authority["E4_review_ready"] is True
    for key in (
        "formal_fact_design",
        "new_fact_registration",
        "projection_repair",
        "threshold_search",
        "candidate_generation",
        "runtime_job",
        "new_market_data_read",
        "preserved_2021_evaluation_read",
        "fresh_cohort_inventory",
        "daily_or_strategy",
        "trading",
    ):
        assert authority[key] is False

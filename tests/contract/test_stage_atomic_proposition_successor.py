from __future__ import annotations

import json

from quanthus.policy import find_project_root
from quanthus.structure_v2_inputs import digest

ROOT = find_project_root()
A = ROOT / "config/stage-learning/stage-atomic-proposition-ontology-audit.v1.json"
B = ROOT / "config/stage-learning/stage-relocation-conflict-adjudication.v1.json"
C = ROOT / "config/stage-learning/stage-compact-disorder-observable-audit.v1.json"
ABC = ROOT / "config/stage-learning/stage-atomic-proposition-successor-abc-result.v1.json"
D = ROOT / "config/stage-learning/stage-atomic-proposition-d-expert-review-result.v1.json"

A_DIGEST = "sha256:f9aac37b172a38b6c756830c04b508f749c67df791465fafab758f618a09aa79"
B_DIGEST = "sha256:addaa2e0555a93685b722ff48721b938d8153f236fdc06dfbe2e7bb4a6f73645"
C_DIGEST = "sha256:86ad2e31c474be556c374b87130ad8fa56567734d8ffe077701e889e15dc4259"
ABC_DIGEST = "sha256:9fd7ac60f7b12bd8ddc8c4875d125658f319ef58cfea4d9fefc95867865ba1af"
D_DIGEST = "sha256:0b00dd0a327b161cd20a4122e5c5a8cad806ad6f9f5713246b36b412a3fb6af7"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_a_freezes_atomic_first_projection_semantics_without_new_authority() -> None:
    value = load(A)
    assert digest(value) == A_DIGEST
    assert value["status"] == "PASS_ATOMIC_PROPOSITION_FIRST"
    decisions = value["decisions"]
    assert decisions["stage0_is_direction_neutral_instability_projection"] is True
    assert decisions["instability_and_relocation_may_coexist"] is True
    assert decisions["instability_and_relocation_coexistence_is_not_a_resolver_conflict"] is True
    assert decisions["mixed_directional_instability_is_not_stage0"] is True
    assert (
        decisions["atomic_propositions_are_qualification_targets_before_stage_projection"]
        is True
    )
    assert decisions["residual_stage0_is_forbidden"] is True
    assert decisions["top1_force_is_forbidden"] is True
    mixed = next(
        row
        for row in value["truth_table"]
        if row.get("atomic_interpretation") == "mixed_directional_instability"
    )
    assert mixed["stage0_2_projection"] == "NEITHER"
    assert all(flag is False for flag in value["authority"].values() if flag is not True)
    assert value["authority"]["diagnostic_only"] is True


def test_b_accounts_for_all_relocation_conflicts_without_retuning_p008() -> None:
    value = load(B)
    assert digest(value) == B_DIGEST
    assert value["status"] == "PASS_CONSUMED_SEMANTIC_ADJUDICATION"
    assert value["packet"]["case_count"] == 11
    assert value["classification_counts"] == {
        "R1_GUARD_OVERFIRE": 0,
        "R2_REFERENCE_UNDERCALL": 2,
        "R3_GENUINE_COEXISTENCE": 9,
        "R4_UNRESOLVED": 0,
    }
    assert len(value["cases"]) == 11
    assert len({item["case_id"] for item in value["cases"]}) == 11
    assert value["bounded_conclusions"]["p008_threshold_retuning_is_not_supported"] is True
    assert (
        value["bounded_conclusions"]
        ["projected_stage0_reference_should_not_be_reused_as_atomic_instability_or_relocation_ground_truth"]
        is True
    )
    authority = value["authority"]
    assert authority["diagnostic_only"] is True
    for key in (
        "p011_review_rewrite",
        "p008_guard_change",
        "threshold_change",
        "candidate_generation",
        "new_market_data_read",
        "preserved_2021_evaluation_read",
        "daily_or_strategy",
        "trading",
    ):
        assert authority[key] is False


def test_c_rejects_generic_churn_but_does_not_prematurely_create_a_fact() -> None:
    value = load(C)
    assert digest(value) == C_DIGEST
    assert value["status"] == "INCONCLUSIVE"
    packets = value["consumed_packets"]
    assert packets["compact_stage0_miss_count"] == 14
    assert packets["definite_stage2_reference_count"] == 21
    findings = value["findings"]
    assert findings["all_six_scalar_fact_ranges_overlap_between_compact_stage0_and_stage2"] is True
    assert findings["all_14_compact_cases_have_broad_path_disorder_two_of_three_true"] is True
    assert findings["all_21_stage2_cases_have_broad_path_disorder_two_of_three_true"] is True
    assert findings["generic_path_disorder_or_churn_is_discriminative"] is False
    assert findings["balanced_order_projection_true_on_compact_stage0_count"] == 1
    assert value["exit"]["new_fact_required_is_proven"] is False
    assert value["candidate_hypothesis_only"]["registered_fact"] is False
    assert value["candidate_hypothesis_only"]["implementation_authorized"] is False
    authority = value["authority"]
    assert authority["diagnostic_only"] is True
    for key, observed in authority.items():
        if key != "diagnostic_only":
            assert observed is False, key


def test_abc_result_opens_only_d_expert_review() -> None:
    value = load(ABC)
    assert digest(value) == ABC_DIGEST
    assert value["state"] == "READY_FOR_D_EXPERT_REVIEW"
    assert value["results"]["A"]["status"] == "PASS_ATOMIC_PROPOSITION_FIRST"
    assert value["results"]["B"]["classification_counts"] == {
        "guard_overfire": 0,
        "reference_undercall": 2,
        "genuine_coexistence": 9,
        "unresolved": 0,
    }
    assert value["results"]["C"]["status"] == "INCONCLUSIVE"
    fork = value["decision_fork_evidence"]
    assert fork["D1_projection_or_taxonomy_repair"] == "SUPPORTED_FOR_REVIEW"
    assert fork["D2_relocation_proposition_repair"] == "NOT_SUPPORTED_BY_CURRENT_CONSUMED_EVIDENCE"
    assert (
        fork["D3_compact_disorder_repair"]
        == "REQUIRES_SYNTHETIC_OBSERVABLE_REVIEW_BEFORE_ROUTE_SELECTION"
    )
    authority = value["authority"]
    assert authority["A_completed"] is True
    assert authority["B_completed"] is True
    assert authority["C_completed"] is True
    assert authority["D_expert_review_ready"] is True
    assert authority["D_route_selected"] is False
    for key in (
        "implementation",
        "threshold_search",
        "candidate_generation",
        "new_fact_registration",
        "runtime_job",
        "new_market_data_read",
        "preserved_2021_evaluation_read",
        "daily_or_strategy",
        "trading",
    ):
        assert authority[key] is False


def test_d_review_recommends_only_owner_gated_synthetic_route() -> None:
    value = load(D)
    assert digest(value) == D_DIGEST
    assert value["state"] == "RECOMMEND_SYNTHETIC_ONLY_ATOMIC_AND_ENVELOPE_ROUTE"
    decisions = value["resolved_decisions"]
    assert decisions["atomic_first_qualification"] == "APPROVE_AS_SUCCESSOR_ARCHITECTURE"
    assert decisions["p008_relocation_implementation"] == "KEEP_FROZEN"
    assert decisions["p008_relocation_threshold_retune"] == "NOT_SUPPORTED"
    assert decisions["new_compact_disorder_fact"] == "NOT_AUTHORIZED"
    assert decisions["third_instability_channel"] == "NOT_AUTHORIZED"
    assert decisions["fresh_market_experiment"] == "NOT_AUTHORIZED"
    assert decisions["preserved_2021_evaluation"] == "UNREAD_AND_CLOSED"
    next_phase = value["recommended_next_phase"]
    assert next_phase["work_item"] == "P3-STAGE-012-E"
    assert next_phase["status"] == "RECOMMENDED_PENDING_OWNER_APPROVAL"
    assert len(next_phase["subwork"]) == 3
    assert all(item["market_data"] is False for item in next_phase["subwork"])
    authority = value["authority"]
    assert authority["review_completed"] is True
    assert authority["route_recommended"] is True
    assert authority["owner_approved_next_phase"] is False
    for key in (
        "implementation",
        "synthetic_prototype",
        "threshold_search",
        "candidate_generation",
        "new_fact_registration",
        "runtime_job",
        "new_market_data_read",
        "preserved_2021_evaluation_read",
        "2022_2025_access",
        "daily_or_strategy",
        "trading",
    ):
        assert authority[key] is False


def test_stage012_documents_exist_and_route_to_owner_gated_e5() -> None:
    for path in (
        ROOT / "docs/design/stage-atomic-proposition-successor-concept.md",
        ROOT / "docs/work-items/stage-atomic-proposition-successor.md",
        ROOT / "docs/acceptance/stage-atomic-proposition-a-ontology-audit-20260913.md",
        ROOT / "docs/acceptance/stage-atomic-proposition-b-relocation-adjudication-20260913.md",
        ROOT / "docs/acceptance/stage-atomic-proposition-c-observable-audit-20260913.md",
        ROOT / "docs/reviews/stage-atomic-proposition-d-expert-review-20260913.md",
        ROOT / "docs/acceptance/stage-atomic-proposition-e-synthetic-acceptance-20260913.md",
        ROOT / "docs/reviews/stage-atomic-proposition-e4-post-synthetic-review-20260913.md",
        ROOT / "docs/acceptance/structure-directional-path-envelope-e5-synthetic-acceptance-20260914.md",
    ):
        assert path.is_file()

    concept = (ROOT / "docs/design/stage-atomic-proposition-successor-concept.md").read_text(
        encoding="utf-8"
    )
    work_item = (ROOT / "docs/work-items/stage-atomic-proposition-successor.md").read_text(
        encoding="utf-8"
    )
    assert (
        "P3-STAGE-012-E4  CLOSED  RECOMMEND_B6_PATH_ENVELOPE_FORMALIZATION_SYNTHETIC_ONLY"
        in concept
    )
    assert "P3-STAGE-012-E5" in work_item
    assert "closed `PASS_SYNTHETIC_QUALIFICATION`" in work_item
    assert "P3-STAGE-012-E6" in work_item
    assert "ready for review only" in work_item
    assert "proposal, not a registered Fact" in work_item
    assert "preserved 2021 remain closed" in work_item

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from quanthus.policy import find_project_root
from quanthus.stage_proposition_bundle import (
    StagePropositionBundleError,
    build_bundle,
    consumer_view,
    load_stage_proposition_contract,
    make_assertion,
    validate_bundle,
)

ROOT = find_project_root()
CUTOFF = "2026-09-15T15:00:00+08:00"


def contract():
    return load_stage_proposition_contract(
        ROOT / "config/stage-learning/stage-proposition-bundle.v1.json"
    )


def assertion(code: int, verdict: str = "TRUE"):
    value = contract().by_code[code]
    provenance = [f"projection:{code}:fixture"]
    if code == 4 and verdict == "TRUE":
        provenance.append("stage3_episode:fixture-episode")
    if code in {10, 11} and verdict == "TRUE":
        provenance.append("stage10_confirmation:fixture-confirmation")
    return make_assertion(
        contract(),
        proposition_id=value.proposition_id,
        verdict=verdict,
        reason_codes=[f"fixture_reason_{code}"],
        fact_refs=[f"fact:fixture:{code}"],
        provenance_refs=provenance,
    )


def test_contract_accounts_for_all_codes_and_only_10_11_are_development_shadow() -> None:
    value = contract()
    assert set(value.by_code) == set(range(13))
    assert len(value.propositions) == 13
    assert value.by_code[10].use_authority == "development_shadow"
    assert value.by_code[11].use_authority == "development_shadow"
    assert all(
        value.by_code[code].use_authority == "audit"
        for code in range(13)
        if code not in {10, 11}
    )


def test_stage10_11_coexist_and_expert_development_shadow_can_consume_both() -> None:
    value = contract()
    stage11 = assertion(11)
    stage10 = assertion(10)
    bundle = build_bundle(
        value,
        security_id="security-fixture-001",
        knowledge_cutoff=CUTOFF,
        control_state="ACTIVE",
        epistemic_status="ASSERTED",
        assertions=[stage11, stage10],
    )
    assert [item.historical_stage_code for item in bundle.context_assertions] == [10]
    assert [item.historical_stage_code for item in bundle.condition_assertions] == [11]
    assert bundle.modifier_assertions == ()
    assert bundle.primary_stage is None
    assert bundle.fact_refs == ("fact:fixture:10", "fact:fixture:11")

    view = consumer_view(value, bundle, surface="development_shadow")
    assert [item.historical_stage_code for item in view.context_assertions] == [10]
    assert [item.historical_stage_code for item in view.condition_assertions] == [11]
    assert view.hidden_assertion_count == 0
    assert view.primary_stage is None
    assert view.to_dict()["production_expert_authority"] is False
    assert view.to_dict()["strategy_or_trading_authority"] is False


def test_deterministic_serialization_ignores_caller_assertion_order() -> None:
    value = contract()
    stage10 = assertion(10)
    stage11 = assertion(11)
    first = build_bundle(
        value,
        security_id="security-fixture-002",
        knowledge_cutoff=CUTOFF,
        control_state="ACTIVE",
        epistemic_status="ASSERTED",
        assertions=[stage10, stage11],
    )
    second = build_bundle(
        value,
        security_id="security-fixture-002",
        knowledge_cutoff=CUTOFF,
        control_state="ACTIVE",
        epistemic_status="ASSERTED",
        assertions=[stage11, stage10],
    )
    assert first.canonical_bytes == second.canonical_bytes
    assert first.digest == second.digest


def test_research_modifier_is_visible_to_audit_but_filtered_from_development_shadow() -> None:
    value = contract()
    advancing = assertion(5)
    modifier = assertion(8)
    bundle = build_bundle(
        value,
        security_id="security-fixture-003",
        knowledge_cutoff=CUTOFF,
        control_state="ACTIVE",
        epistemic_status="ASSERTED",
        assertions=[modifier, advancing],
    )
    audit = consumer_view(value, bundle, surface="audit")
    assert [item.historical_stage_code for item in audit.context_assertions] == [5]
    assert [item.historical_stage_code for item in audit.modifier_assertions] == [8]

    development = consumer_view(value, bundle, surface="development_shadow")
    assert development.context_assertions == ()
    assert development.modifier_assertions == ()
    assert development.fact_refs == ()
    assert development.hidden_assertion_count == 2


def test_neutral_abstain_and_x_are_three_distinct_outputs() -> None:
    value = contract()
    neutral = build_bundle(
        value,
        security_id="security-neutral",
        knowledge_cutoff=CUTOFF,
        control_state="ACTIVE",
        epistemic_status="ASSERTED",
        assertions=[assertion(0)],
    )
    abstain = build_bundle(
        value,
        security_id="security-abstain",
        knowledge_cutoff=CUTOFF,
        control_state="ACTIVE",
        epistemic_status="ABSTAIN",
        status_reasons=["usable_evidence_insufficient"],
    )
    frozen = build_bundle(
        value,
        security_id="security-frozen",
        knowledge_cutoff=CUTOFF,
        control_state="X",
        epistemic_status="NOT_EVALUATED",
        status_reasons=["ordinary_stage_evaluation_frozen"],
    )
    assert neutral.context_assertions[0].historical_stage_code == 0
    assert neutral.epistemic_status == "ASSERTED"
    assert abstain.context_assertions == ()
    assert abstain.epistemic_status == "ABSTAIN"
    assert frozen.control_state == "X"
    assert frozen.epistemic_status == "NOT_EVALUATED"
    assert frozen.context_assertions == ()
    assert len({neutral.digest, abstain.digest, frozen.digest}) == 3


def test_abstain_cannot_hide_a_true_neutral_assertion() -> None:
    with pytest.raises(
        StagePropositionBundleError,
        match="epistemic_status_forbids_true_assertion",
    ):
        build_bundle(
            contract(),
            security_id="security-invalid-abstain",
            knowledge_cutoff=CUTOFF,
            control_state="ACTIVE",
            epistemic_status="ABSTAIN",
            status_reasons=["do_not_pool_with_neutral"],
            assertions=[assertion(0)],
        )


def test_true_nested_condition_requires_true_parent_context() -> None:
    with pytest.raises(StagePropositionBundleError, match="true_parent_required"):
        build_bundle(
            contract(),
            security_id="security-orphan-stage11",
            knowledge_cutoff=CUTOFF,
            control_state="ACTIVE",
            epistemic_status="ASSERTED",
            assertions=[assertion(11)],
        )


def test_same_layer_true_contexts_require_ambiguity_and_neutral_never_coexists() -> None:
    value = contract()
    with pytest.raises(
        StagePropositionBundleError,
        match="same_layer_true_requires_ambiguity",
    ):
        build_bundle(
            value,
            security_id="security-context-conflict",
            knowledge_cutoff=CUTOFF,
            control_state="ACTIVE",
            epistemic_status="ASSERTED",
            assertions=[assertion(10), assertion(12)],
        )
    ambiguous = build_bundle(
        value,
        security_id="security-context-ambiguous",
        knowledge_cutoff=CUTOFF,
        control_state="ACTIVE",
        epistemic_status="AMBIGUOUS",
        status_reasons=["multiple_context_propositions_true"],
        assertions=[assertion(10), assertion(12)],
    )
    assert ambiguous.epistemic_status == "AMBIGUOUS"

    with pytest.raises(StagePropositionBundleError, match="neutral_context_conflict"):
        build_bundle(
            value,
            security_id="security-neutral-conflict",
            knowledge_cutoff=CUTOFF,
            control_state="ACTIVE",
            epistemic_status="AMBIGUOUS",
            status_reasons=["neutral_directional_conflict"],
            assertions=[assertion(0), assertion(12)],
        )


def test_stage4_true_requires_prior_stage3_episode_provenance() -> None:
    value = contract()
    definition = value.by_code[4]
    with pytest.raises(StagePropositionBundleError, match="required_provenance_missing"):
        make_assertion(
            value,
            proposition_id=definition.proposition_id,
            verdict="TRUE",
            reason_codes=["accepted_breakout_fixture"],
            fact_refs=["fact:breakout:fixture"],
            provenance_refs=["projection:stage4:fixture"],
        )


def test_raw_bundle_cannot_mutate_authority_fact_closure_or_primary_stage() -> None:
    value = contract()
    bundle = build_bundle(
        value,
        security_id="security-tamper",
        knowledge_cutoff=CUTOFF,
        control_state="ACTIVE",
        epistemic_status="ASSERTED",
        assertions=[assertion(10)],
    )

    mutated_grade = deepcopy(bundle.to_dict())
    mutated_grade["context_assertions"][0]["evidence_grade"] = "official"
    with pytest.raises(StagePropositionBundleError, match="assertion_catalog_metadata_mutated"):
        validate_bundle(value, mutated_grade)

    missing_fact = deepcopy(bundle.to_dict())
    missing_fact["fact_refs"] = []
    with pytest.raises(StagePropositionBundleError, match="fact_reference_closure"):
        validate_bundle(value, missing_fact)

    primary = deepcopy(bundle.to_dict())
    primary["primary_stage"] = 10
    with pytest.raises(StagePropositionBundleError, match="primary_stage_must_be_null"):
        validate_bundle(value, primary)


def test_x_control_state_forbids_ordinary_assertions() -> None:
    with pytest.raises(StagePropositionBundleError, match="control_x_freeze_contract"):
        build_bundle(
            contract(),
            security_id="security-x-invalid",
            knowledge_cutoff=CUTOFF,
            control_state="X",
            epistemic_status="NOT_EVALUATED",
            status_reasons=["frozen"],
            assertions=[assertion(10)],
        )


def test_published_examples_are_deep_valid_and_show_surface_filtering() -> None:
    value = contract()
    examples = json.loads(
        (
            ROOT
            / "config/stage-learning/stage-proposition-bundle-examples.v1.json"
        ).read_text(encoding="utf-8")
    )
    assert examples["example_kind"] == "synthetic_contract_examples_not_market_evidence"
    bundles = {
        name: validate_bundle(value, raw)
        for name, raw in examples["examples"].items()
    }
    assert set(bundles) == {
        "accepted_nested_10_11",
        "neutral_0",
        "abstain",
        "control_x",
        "research_modifier_audit_only",
    }
    accepted = consumer_view(
        value,
        bundles["accepted_nested_10_11"],
        surface="development_shadow",
    )
    assert [item.historical_stage_code for item in accepted.context_assertions] == [10]
    assert [item.historical_stage_code for item in accepted.condition_assertions] == [11]
    research = consumer_view(
        value,
        bundles["research_modifier_audit_only"],
        surface="development_shadow",
    )
    assert research.context_assertions == ()
    assert research.modifier_assertions == ()
    assert research.hidden_assertion_count == 2

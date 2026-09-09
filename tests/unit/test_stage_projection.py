from __future__ import annotations

from decimal import Decimal

from quanthus.policy import find_project_root
from quanthus.stage_projection import (
    load_stage_projection_contract,
    project_support_evolution_stages,
    projection_counts,
)
from quanthus.structure_support_facts import project_stage_10_11

from .test_structure_support_facts import build, sequence
from .test_structure_v2_inputs import row

ROOT = find_project_root()
CONTRACT = ROOT / "config/stage-10-11-projection.v1.json"


def _scale20(result, facts):
    process_id = next(item.process_id for item in facts.bindings if item.scale == 20)
    return [item for item in result.observations if item.process_id == process_id]


def test_contract_freezes_nested_candidate_semantics_without_primary_stage_authority():
    contract = load_stage_projection_contract(CONTRACT)
    assert contract.projection_id == "stock-stage.support-evolution-10-11"
    assert contract.version == "1.0.0"
    assert contract.persistence_sessions == 5
    assert contract.failure_depth_abs_min == Decimal("0.02")
    assert contract.rebound_min == Decimal("0.05")
    assert contract.source_scope == "bounded_retrospective_f9_support_process"


def test_stage_10_confirms_once_and_stage_11_is_nested_candidate_not_primary_stage():
    _admissions, _structure, facts = build(sequence())
    contract = load_stage_projection_contract(CONTRACT)
    result = project_support_evolution_stages(facts, contract)
    projected = _scale20(result, facts)
    day71 = next(item for item in projected if item.market_date == row(71).market_date)
    day72 = next(item for item in projected if item.market_date == row(72).market_date)

    assert day71.stage_10_context_active is True
    assert day71.stage_11_rebound_condition_active is False
    assert day71.candidate_stages == (10,)
    assert day71.primary_stage is None
    assert day71.stage_10_confirmation_ref is not None

    # Current distance is only about -0.56%; Stage 11 must consume the prior confirmation.
    assert day72.stage_10_context_active is True
    assert day72.stage_11_rebound_condition_active is True
    assert day72.candidate_stages == (10, 11)
    assert day72.primary_stage is None
    assert day72.stage_10_confirmation_ref == day71.stage_10_confirmation_ref


def test_reclaim_clears_active_candidates_but_preserves_historical_confirmation_reference():
    _admissions, _structure, facts = build(sequence())
    result = project_support_evolution_stages(facts, load_stage_projection_contract(CONTRACT))
    projected = _scale20(result, facts)
    day71 = next(item for item in projected if item.market_date == row(71).market_date)
    reclaim = next(item for item in projected if item.market_date == row(73).market_date)
    assert reclaim.stage_10_context_active is False
    assert reclaim.stage_11_rebound_condition_active is False
    assert reclaim.candidate_stages == ()
    assert reclaim.primary_stage is None
    assert reclaim.stage_10_confirmation_ref == day71.stage_10_confirmation_ref


def test_formal_projection_preserves_legacy_research_predicates_without_reusing_legacy_identity():
    _admissions, _structure, facts = build(sequence())
    formal = project_support_evolution_stages(facts, load_stage_projection_contract(CONTRACT))
    legacy = project_stage_10_11(facts)
    formal_by_key = {(item.process_id, item.market_date): item for item in formal.observations}
    legacy_by_key = {(item.process_id, item.market_date): item for item in legacy}
    assert set(formal_by_key) == set(legacy_by_key)
    for key in formal_by_key:
        observed = formal_by_key[key]
        previous = legacy_by_key[key]
        assert observed.stage_10_context_active is previous.stage_10_active
        assert observed.stage_11_rebound_condition_active is previous.stage_11_active
        assert observed.primary_stage is None


def test_projection_counts_make_overlap_explicit_and_primary_stage_always_null():
    _admissions, _structure, facts = build(sequence())
    result = project_support_evolution_stages(facts, load_stage_projection_contract(CONTRACT))
    counts = projection_counts(result)
    assert counts["stage_10_confirmation_count"] >= 1
    assert counts["stage_10_context_count"] >= counts["stage_11_rebound_condition_count"]
    assert counts["nested_10_11_candidate_count"] == counts["stage_11_rebound_condition_count"]
    assert counts["primary_stage_non_null_count"] == 0


def test_projection_threshold_change_changes_projection_contract_not_support_fact_identity(
    tmp_path,
):
    _admissions, _structure, facts = build(sequence())
    original_digest = facts.digest
    raw = CONTRACT.read_text(encoding="utf-8").replace(
        '"minimum_post_break_rebound": "0.05"',
        '"minimum_post_break_rebound": "0.06"',
    )
    changed = tmp_path / "projection.json"
    changed.write_text(raw, encoding="utf-8")
    changed_contract = load_stage_projection_contract(changed)
    assert changed_contract.contract_digest != load_stage_projection_contract(
        CONTRACT
    ).contract_digest
    assert facts.digest == original_digest


def test_stage_11_current_condition_can_turn_false_without_claiming_primary_transition():
    values = sequence()
    # Add an unreclaimed pullback after Stage 11 has already appeared; still below support.
    values[-1] = row(73, "89.8", high=Decimal("90"), low=Decimal("89"))
    _admissions, _structure, facts = build(values)
    result = project_support_evolution_stages(facts, load_stage_projection_contract(CONTRACT))
    projected = _scale20(result, facts)
    prior = next(item for item in projected if item.market_date == row(72).market_date)
    later = next(item for item in projected if item.market_date == row(73).market_date)
    assert prior.stage_11_rebound_condition_active is True
    assert later.stage_10_context_active is True
    assert later.primary_stage is None
    # This assertion is only about the proposition, not a Stage-11 -> Stage-10 transition.
    assert later.candidate_stages in {(10,), (10, 11)}

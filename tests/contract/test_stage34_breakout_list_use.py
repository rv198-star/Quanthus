"""The new purpose never amends frozen semantics or creates an execution permit."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(path):
    return json.loads((ROOT / path).read_text())


def test_use_has_one_actual_action_equal_information_and_equal_cost():
    use = load('config/research/learning-mvp/stage34-breakout-list-use.v1.json')
    assert use['consumer']['review_slots_per_day'] == 5
    assert use['consumer']['cost_per_slot_units'] == 1
    assert use['comparison']['arms'] == ['frozen_R1', 'frozen_R0']
    assert use['comparison']['same_review_slots_and_input_cost'] is True
    assert 'UNKNOWN denominator' in use['comparison']['shared_inputs'][-1]
    assert use['comparison']['ranking_before_reference']['stage_order'] == [4, 3]
    assert use['comparison']['economic_or_future_settlement_result'] is False
    assert use['consumer']['no_automatic_order_or_fill'] is True
    assert use['consumer']['missing_or_invalid_never_forces_sale'] is True


def test_original_quality_minimums_and_candidate_are_unchanged():
    use = load('config/research/learning-mvp/stage34-breakout-list-use.v1.json')
    old = load('config/stage-learning/qualification.v1.json')
    from quanthus.structure_v2_inputs import digest
    candidate = load(use['recognition']['candidate_config'])
    assert digest(candidate) == use['recognition']['candidate_canonical_digest']
    assert use['recognition']['minimum_definite_per_stage'] == (
        old['hard_gates']['minimum_definite_references_per_stage'])
    assert use['recognition']['minimum_ambiguous'] == (
        old['hard_gates']['minimum_ambiguous_references_for_overassertion_audit'])
    assert use['recognition']['minimum_total_unique_episodes_necessary_not_sufficient'] == 250
    assert use['recognition']['training_or_parameter_search'] is False
    assert use['sampling_preflight']['legacy_result_and_sampler_immutable'] is True


def test_history_and_exposure_do_not_become_new_truth_or_permission():
    use = load('config/research/learning-mvp/stage34-breakout-list-use.v1.json')
    sampling = use['sampling_preflight']
    assert sampling['history']['scales'] == [20, 60]
    assert sampling['history']['minimum_prior_contiguous_admitted_sessions'] == {'20': 20, '60': 60}
    assert sampling['history']['T_excluded_from_prior_count'] is True
    assert sampling['exclude_all318_previously_reviewed_episode_ids_at_every_date'] is True
    assert sampling['caps_unchanged'] is True
    assert use['reference']['independence_receipt_required'] is True
    assert use['reference']['independent_reference_available_by_this_contract'] is False
    assert use['execution']['consumer_test_authorized'] is False
    assert use['execution']['production_or_P6_admitted'] is False
    assert use['execution']['new_network_source_requests'] == 0
    assert use['execution']['steps_1_2_deadline'] == '2026-10-27T19:43:13+00:00'
    assert use['execution']['maximum_conditional_step3_seconds'] == 7200

from copy import deepcopy
from datetime import date

import pytest

from quanthus.expert_strategy.held_decision_census import census_held_decisions


def population():
    held = [
        dict(
            market_date="2020-01-02",
            unit_id="u1",
            security_code="s1",
            entry_key="e1",
            entry_date="2020-01-01",
            quantity=100,
        )
    ]
    trace = [
        {
            **held[0],
            "baseline_action": "no_action",
            "base_required_action": "none",
            "baseline_decision_digest": "sha256:" + "a" * 64,
            "base_reason_code": "clear",
            "context": {"security_code": "s1", "knowledge_cutoff": "2020-01-02T16:00:00+08:00"},
        }
    ]
    fact = dict(
        security_code="s1",
        market_date="2020-01-02",
        available=True,
        reason="qualified",
        lineage_digest="sha256:" + "b" * 64,
        stage10_active=True,
        stage11_active=False,
    )
    shadow = [
        {
            **held[0],
            "baseline_action": "no_action",
            "candidate_action": "reduce",
            "baseline_decision_digest": trace[0]["baseline_decision_digest"],
            "support_available": True,
            "support_reason": "qualified",
            "support_lineage_digest": fact["lineage_digest"],
            "proposed": True,
            "final_action_changed": True,
            "reason": "existing_proposal",
        }
    ]
    expected = dict(
        reviews=1, episodes=1, issuers=1, unique_security_dates=1, unavailable_structure=0
    )
    return held, trace, shadow, [fact], expected


def run(values):
    held, trace, shadow, support, expected = values
    return census_held_decisions(
        held,
        trace,
        shadow,
        support,
        expected=expected,
        first_date=date(2012, 1, 1),
        last_date=date(2021, 12, 31),
    )


def test_census_preserves_denominator_without_granting_another_trial():
    result = run(population())
    assert result["population"]["reviews"] == 1
    assert result["existing_mapping_concentration"]["changed_episodes"] == 1
    assert result["new_candidate_authorized"] is False
    assert set(result["unobserved_needs"].values()) == {"NOT_OBSERVED_IN_HELD_TRACE"}


@pytest.mark.parametrize("index", [0, 1, 2, 3])
def test_duplicate_or_dropped_rows_do_not_become_a_complete_census(index):
    values = population()
    values[index].append(deepcopy(values[index][0]))
    with pytest.raises(ValueError, match="DUPLICATE|DENOMINATOR"):
        run(values)


@pytest.mark.parametrize(
    "index,field,value,error",
    [
        (1, "security_code", "wrong", "JOIN_IDENTITY"),
        (2, "entry_key", "wrong", "JOIN_IDENTITY"),
        (1, "baseline_decision_digest", "sha256:" + "c" * 64, "BASELINE_BINDING"),
        (2, "support_lineage_digest", "sha256:" + "c" * 64, "FACT_BINDING"),
        (2, "final_action_changed", False, "ACTION_DELTA"),
        (1, "base_required_action", "force_liquidate", "GUARD_NOT_ENFORCED"),
        (2, "proposed", "true", "INVALID_BOOLEAN"),
    ],
)
def test_join_and_existing_guard_integrity(index, field, value, error):
    values = population()
    values[index][0][field] = value
    with pytest.raises(ValueError, match=error):
        run(values)


def test_unknown_is_not_a_negative_label_or_forced_sale():
    values = population()
    values[3][0].update(available=False, reason="beyond_horizon", stage10_active=False)
    values[2][0].update(
        support_available=False,
        support_reason="beyond_horizon",
        proposed=False,
        final_action_changed=False,
        candidate_action="no_action",
    )
    values[4]["unavailable_structure"] = 1
    result = run(values)
    assert result["counts"]["unavailable_structure"] == 1
    assert result["unavailable_reasons"] == {"beyond_horizon": 1}
    assert result["data_unavailable_implies_sale"] is False
    values[2][0].update(proposed=True, final_action_changed=True, candidate_action="reduce")
    with pytest.raises(ValueError, match="UNAVAILABLE_IS_NOT_DAMAGE"):
        run(values)


def test_existing_safety_overlap_is_not_counted_as_a_new_decision():
    values = population()
    values[1][0].update(baseline_action="full_exit", base_required_action="force_liquidate")
    values[2][0].update(
        baseline_action="full_exit", candidate_action="full_exit", final_action_changed=False
    )
    result = run(values)
    assert result["counts"]["existing_safety_overlap_reviews"] == 1
    assert result["counts"]["existing_changed_reviews"] == 0
    values[2][0].update(candidate_action="reduce", final_action_changed=True)
    with pytest.raises(ValueError, match="CANDIDATE_BYPASSES_GUARD"):
        run(values)


def test_untyped_quantity_is_not_an_exact_identity():
    values = population()
    values[0][0]["quantity"] = True
    values[1][0]["quantity"] = True
    with pytest.raises(ValueError, match="HELD_IDENTITY"):
        run(values)


def test_cutoff_uses_shanghai_date_and_requires_a_real_timezone():
    values = population()
    values[1][0]["context"]["knowledge_cutoff"] = "2020-01-01T23:59:00+00:00"
    assert run(values)["counts"]["recorded_cutoff_same_session"] == 1
    values[1][0]["context"]["knowledge_cutoff"] = "2020-01-01T23:59:00"
    with pytest.raises(ValueError, match="CONTEXT_CUTOFF"):
        run(values)


def test_retrospective_cutoff_is_preserved_and_never_called_historical_pit():
    values = population()
    values[1][0]["context"]["knowledge_cutoff"] = "2026-10-25T08:51:06+00:00"
    result = run(values)
    assert result["counts"]["recorded_cutoff_later_snapshot"] == 1
    assert result["historical_PIT_qualified"] is False
    assert result["new_candidate_authorized"] is False

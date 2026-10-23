"""Synthetic counterexamples and exact frozen-policy boundary checks."""

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from quanthus.aggregator import GovernedAction
from quanthus.backtest.contracts import UnitState
from quanthus.expert_protocols import RequiredAction
from quanthus.expert_strategy.formal_p2_accounting_v2 import AccountingCorrectedReplayRuntime
from quanthus.expert_strategy.formal_p2_replay import FormalP2ReplayRuntime, _UnitRuntime
from quanthus.expert_strategy.p3_risk_diagnostic import (
    classify_entry_veto,
    marked_return_diagnostic,
)
from quanthus.expert_strategy.position_risk import PositionRiskPolicy
from quanthus.expert_strategy.registry import ExpertStrategyRegistry
from quanthus.position_lifecycle import PositionStage, initialize_position_lifecycle

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def policy():
    return PositionRiskPolicy.from_card(ExpertStrategyRegistry(ROOT).card)


@pytest.mark.parametrize(
    "vol,rsi,expected",
    [
        ("0.50", "75", "clear"),
        ("0.50000001", "75", "volatility_only"),
        ("0.50", "75.00000001", "rsi_only"),
        ("0.51", "76", "volatility_and_rsi"),
        (None, "50", "unavailable"),
        ("0.2", None, "unavailable"),
    ],
)
def test_frozen_entry_units_and_strict_boundaries(policy, vol, rsi, expected):
    from tests.unit.test_formal_expert_strategy import _context

    v, r = None if vol is None else Decimal(vol), None if rsi is None else Decimal(rsi)
    assert classify_entry_veto(v, r, policy) == expected
    decision = policy.evaluate_entry(_context(volatility=v, rsi=r))
    assert decision.veto.allow == (None if expected == "unavailable" else expected == "clear")


@pytest.mark.parametrize("rsi", ["NaN", "101", "-1"])
def test_invalid_feature_does_not_become_a_policy_clear(policy, rsi):
    with pytest.raises(ValueError, match="FEATURE_UNIT_OR_VALUE"):
        classify_entry_veto(Decimal("0.3"), Decimal(rsi), policy)


def accounting(**kwargs):
    values = {
        "entry_gross": "10000",
        "entry_fees": "0",
        "entry_wealth": "100000",
        "cash": "90000",
        "market_value": "10000",
        "external_pool": "0",
        "stop_loss": "0.08",
        **kwargs,
    }
    return marked_return_diagnostic(**{k: Decimal(v) for k, v in values.items()})


def test_flat_price_partial_sale_does_not_destroy_wealth_but_v1_input_claims_loss():
    result = accounting(cash="95000", market_value="5000")
    assert result["v1_marked_return"] == Decimal("-0.5")
    assert result["wealth_return_counterexample"] == 0
    assert result["v1_only_stop"]


def test_cash_dividend_and_reference_price_keep_closed_wealth():
    result = accounting(cash="91000", market_value="9000")
    assert result["v1_marked_return"] == Decimal("-0.1")
    assert result["wealth_return_counterexample"] == 0
    assert result["v1_only_stop"]


def test_fee_and_prior_cycle_pool_are_neither_lost_nor_double_counted():
    # Prior-cycle pool 1234 is in both baselines; buy and partial sell cost 10 each.
    result = accounting(
        entry_fees="10",
        entry_wealth="101234",
        external_pool="1234",
        cash="94980",
        market_value="5000",
    )
    assert result["wealth_return_counterexample"] == Decimal("-0.00199800")


def test_unreduced_unadjusted_position_matches_and_true_loss_still_stops():
    result = accounting(market_value="9000")
    assert result["v1_marked_return"] == result["wealth_return_counterexample"] == Decimal("-0.1")
    assert result["wealth_stop"] and not result["v1_only_stop"]


def test_missing_cost_is_not_an_implicit_zero_return():
    with pytest.raises(ValueError, match="ACCOUNTING_INPUT"):
        accounting(entry_gross="0")


def held_runtime():
    before = UnitState.empty(unit_id="unit-test", cash=Decimal("100000"))
    ledger = replace(
        before,
        cash=Decimal("95000"),
        security_code="synthetic-security",
        settled_quantity=500,
        position_cost=Decimal("5000"),
        last_mark_price=Decimal("10"),
        last_mark_snapshot_id="synthetic-mark",
    )
    lifecycle = replace(
        initialize_position_lifecycle(
            ledger_state=before, as_of=datetime(2020, 1, 1, tzinfo=timezone.utc)
        ),
        stage=PositionStage.TRIAL,
        security_code="synthetic-security",
        opportunity_id="synthetic-opportunity",
    )
    return _UnitRuntime(
        ledger=ledger,
        lifecycle=lifecycle,
        panel_security_id="synthetic-security",
        formal_security_id="synthetic-security",
        source_event=SimpleNamespace(),
        entry_gross=Decimal("10000"),
        entry_fees=Decimal("0"),
        entry_wealth=Decimal("100000"),
        holding_sessions=4,
    )


@pytest.mark.parametrize(
    "runtime_class,expected_return,expected_action",
    [
        (FormalP2ReplayRuntime, Decimal("-0.5"), RequiredAction.FORCE_LIQUIDATE),
        (AccountingCorrectedReplayRuntime, Decimal("0"), RequiredAction.NONE),
    ],
)
def test_actual_held_review_exposes_partial_sale_bug_and_v2_correction(
    monkeypatch, runtime_class, expected_return, expected_action
):
    import quanthus.expert_strategy.formal_p2_replay as formal

    # Invoke the production review path, not a copied diagnostic formula.
    r = object.__new__(runtime_class)
    r.registry = ExpertStrategyRegistry(ROOT)
    r.feature_registry = None
    r.panel = SimpleNamespace(feature=lambda *_: None, report=None)
    r._held_event = lambda *_: SimpleNamespace(event_id="synthetic-held")
    r._decision_row = lambda *_: {}
    monkeypatch.setattr(formal, "build_formal_expert_context", lambda **_: None)
    observed = {}

    def review(**kwargs):
        observed.update(kwargs)
        return SimpleNamespace(action=SimpleNamespace(final_action=GovernedAction.NO_ACTION))

    r._review_positions(
        0,
        SimpleNamespace(snapshot=SimpleNamespace(market_date=date(2020, 1, 6))),
        {"unit-test": held_runtime()},
        SimpleNamespace(evaluate_held=review),
        None,
        None,
        [],
        [],
        [],
    )
    assert observed["position_facts"].marked_return == expected_return
    assert observed["base_required_action"] is expected_action


def test_new_position_resets_only_v2_cycle_state_and_uses_prebuy_wealth():
    before = UnitState.empty(unit_id="unit-test", cash=Decimal("100000"))
    fill = SimpleNamespace(payload={"gross_amount": "10000", "total_fees": "10"})
    for cls, count in [(FormalP2ReplayRuntime, 1), (AccountingCorrectedReplayRuntime, 0)]:
        state = held_runtime()
        state.strategy_reduction_count = state.deterioration_streak = 1
        state.cooldown_sessions = 5
        object.__new__(cls)._record_entry(state, before, fill)
        assert state.strategy_reduction_count == state.deterioration_streak == count
        assert state.holding_sessions == 1
        assert state.entry_gross == 10000 and state.entry_fees == 10
        if cls is AccountingCorrectedReplayRuntime:
            assert state.entry_wealth == 100000 and state.cooldown_sessions == 0


def test_v2_cannot_read_real_sources_or_inherit_old_admission():
    with pytest.raises(ValueError, match="REAL_RUN_NOT_ADMITTED"):
        AccountingCorrectedReplayRuntime(ROOT)


def test_v2_missing_position_baseline_holds_unavailable():
    state = held_runtime()
    state.entry_wealth = None
    assert object.__new__(AccountingCorrectedReplayRuntime)._marked_return(state) is None

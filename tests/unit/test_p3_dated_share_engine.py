"""Actual P2 matching costs with separately versioned listed-share availability."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from quanthus.backtest.contracts import EventType
from quanthus.backtest.engine import BacktestInputError
from quanthus.expert_strategy import p3_historical_action_successor as old
from quanthus.expert_strategy.p3_dated_share_engine import DatedShareEngine
from quanthus.pipelines.securities_warehouse.p10_historical_action_source import ActionFacts
from tests.unit.test_p3_historical_bonus_successor import setup_case


def case(*, quantity=100, full_exit=True, fraction="0.4"):
    runtime, frames, unit, run = setup_case(
        year=2019, quantity=quantity, full_exit=full_exit, fraction=fraction
    )
    step = frames[1].actions("000001.SZ", Decimal("9.50"))[0]
    action = replace(step.action, action_id="official-action:" + "a" * 64)
    step = replace(step, action=action)
    fact = ActionFacts(
        action.security_code,
        frames[0].execution_date,
        action.effective_date,
        action.effective_date,
        None,
        frames[2].execution_date,
        action.cash_dividend_per_share,
        action.bonus_share_ratio,
        action.known_at,
        action.source_revision,
        "a" * 64,
        "b" * 64,
    )
    original = runtime._signal()
    snapshot = original.snapshot
    signal = old.h.HistoricalSignal(**{**original.__dict__, "snapshot": snapshot})
    engine = DatedShareEngine(
        runtime.engine.policy,
        snapshot.knowledge_cutoff,
        snapshot.snapshot_id,
        {action.action_id: action},
        (fact,),
    )
    run = replace(run, code_version=old.h.VERSION + ":dated-synthetic")
    # Use executable ex/listing sessions, with the original signal before ex-date.
    sessions = tuple(f.session("000001.SZ") for f in frames[1:])
    sessions = (
        replace(sessions[0], open_execution_state=sessions[1].open_execution_state),
        sessions[1],
    )
    return engine, run, signal, unit.ledger, sessions, step


def execute(*, quantity=100, fraction="0.4"):
    engine, run, signal, state, sessions, step = case(quantity=quantity, fraction=fraction)
    result = engine.run_sell_sequence(
        run=run, signal=signal, opening_state=state, sessions=sessions, corporate_actions=(step,)
    )
    return engine, result, state, sessions, step


def test_old_shares_sell_on_ex_date_new_entitlement_only_on_listing_date():
    engine, result, _, sessions, _ = execute()
    fills = [e for e in result.events if e.event_type is EventType.FILL]
    assert [e.payload["quantity"] for e in fills] == [100, 40]
    assert [e.event_time.date() for e in fills] == [s.session_date for s in sessions]
    assert len({e.payload["order_id"] for e in fills}) == 1
    assert sum(e.event_type is EventType.ORDER_CREATED for e in result.events) == 1
    assert sum(e.event_type is EventType.SIGNAL_ACCEPTED for e in result.events) == 1
    assert result.result_summary["position_quantity"] == 0
    assert result.result_summary["pending_quantity"] == 0
    assert sum(e.event_type is EventType.UNIT_SETTLEMENT for e in result.events) == 1
    snapshots = [e.payload for e in result.events if e.event_type is EventType.DAILY_SNAPSHOT]
    assert [s["position_quantity"] for s in snapshots] == [40, 0]
    assert snapshots[0]["dividend_income"] == "5.00"
    for fill in fills:
        p = fill.payload
        assert Decimal(p["commission"]) >= engine.policy.sell_cost.minimum_commission
        assert Decimal(p["total_fees"]) == sum(
            Decimal(p[k]) for k in ("commission", "stamp_duty", "transfer_fee")
        )
    action = next(e.payload for e in result.events if e.event_type is EventType.CORPORATE_ACTION)
    assert action["actual_share_credit_date"] is None
    assert action["locked_quantity"] == 40
    assert action["tradable_quantity"] == 100


def test_reduction_preserves_proportion_without_delaying_tradable_old_shares():
    _, result, _, sessions, _ = execute(quantity=50)
    fills = [e for e in result.events if e.event_type is EventType.FILL]
    assert len(fills) == 1
    assert fills[0].payload["quantity"] == 70
    assert fills[0].event_time.date() == sessions[0].session_date
    assert result.result_summary["position_quantity"] == 70


def test_end_before_listing_keeps_entitlement_and_residual_order_pending():
    engine, run, signal, state, sessions, step = case()
    result = engine.run_sell_sequence(
        run=run,
        signal=signal,
        opening_state=state,
        sessions=sessions[:1],
        corporate_actions=(step,),
    )
    assert result.result_summary["status"] == "pending_sell"
    assert result.result_summary["pending_quantity"] == 40
    assert result.result_summary["position_quantity"] == 40
    assert not any(e.event_type is EventType.UNIT_SETTLEMENT for e in result.events)


def test_duplicate_entitlement_rejected_without_second_application():
    engine, run, _, state, _, step = case()
    args = dict(
        run=run,
        opening_state=state,
        action=step.action,
        applied_at=step.applied_at,
        post_action_mark_price=step.post_action_mark_price,
        mark_snapshot_id=step.mark_snapshot_id,
    )
    first = engine.apply_corporate_action(**args)
    with pytest.raises(BacktestInputError, match="DUPLICATE_ENTITLEMENT"):
        engine.apply_corporate_action(**{**args, "opening_state": first.state})


def test_fractional_entitlement_is_not_rounded_to_an_invented_share():
    with pytest.raises(BacktestInputError, match="fractional"):
        execute(fraction="0.001")


def test_production_or_changed_source_term_rejected():
    engine, run, _, state, _, step = case()
    for action, r in (
        (step.action, replace(run, official=True)),
        (replace(step.action, cash_dividend_per_share=Decimal("0.06")), run),
    ):
        with pytest.raises(BacktestInputError, match="EXACT_ACTION_BINDING|official run"):
            engine.apply_corporate_action(
                run=r,
                opening_state=state,
                action=action,
                applied_at=step.applied_at,
                post_action_mark_price=step.post_action_mark_price,
                mark_snapshot_id=step.mark_snapshot_id,
            )


def test_availability_uses_listing_date_and_never_sets_unknown_actual_credit():
    engine, result, _, sessions, step = execute()
    from quanthus.backtest.portfolio_replay import _state_from_snapshot

    first = next(e for e in result.events if e.event_type is EventType.DAILY_SNAPSHOT)
    state = _state_from_snapshot(first, target_notional=Decimal("1000"))
    assert engine.locked_quantity(state, sessions[0].session_date) == 40
    assert engine.locked_quantity(state, sessions[1].session_date) == 0
    assert engine.dated[step.action.action_id].share_credit_date is None
    assert engine.locked_quantity(state, sessions[1].session_date + timedelta(days=1)) == 0


def test_all_locked_intermediate_session_carries_without_fake_fill_or_extra_settlement():
    engine, run, signal, state, sessions, step = case()
    middle = replace(
        sessions[0],
        session_date=sessions[0].session_date + timedelta(days=1),
        open_at=sessions[0].open_at + timedelta(days=1),
        close_at=sessions[0].close_at + timedelta(days=1),
    )
    result = engine.run_sell_sequence(
        run=run,
        signal=signal,
        opening_state=state,
        sessions=(sessions[0], middle, sessions[1]),
        corporate_actions=(step,),
    )
    fills = [e for e in result.events if e.event_type is EventType.FILL]
    assert [e.payload["quantity"] for e in fills] == [100, 40]
    assert not any(e.event_time.date() == middle.session_date for e in fills)
    snapshots = [e.payload for e in result.events if e.event_type is EventType.DAILY_SNAPSHOT]
    assert [e["position_quantity"] for e in snapshots] == [40, 40, 0]
    assert snapshots[1]["locked_quantity"] == 40
    assert snapshots[1]["tradable_quantity"] == 0
    assert sum(e.event_type is EventType.UNIT_SETTLEMENT for e in result.events) == 1

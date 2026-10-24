"""The existing P2 proportional order rule also reconciles P3 lifecycle inputs."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from quanthus.backtest import (
    BacktestEngine,
    BacktestRunSpec,
    CorporateAction,
    CorporateActionStep,
    OpenExecutionState,
    load_execution_policy,
)
from quanthus.backtest.contracts import BacktestSignal, EventType, OrderSide
from quanthus.expert_strategy import p3_historical_bonus_successor as s
from quanthus.expert_strategy.formal_p2_replay import FormalP2ReplayRuntime, _UnitRuntime
from quanthus.position_lifecycle import PositionStage
from tests.unit.test_position_lifecycle import (
    PositionTransitionKind,
    apply_position_transition,
    initialize_position_lifecycle,
    ledger,
    transition,
)
from tests.unit.test_strategy_replay import POLICY_PATH, at, frame, session


def setup_case(*, fraction="0.1", quantity=100, full_exit=True):
    engine = BacktestEngine(load_execution_policy(POLICY_PATH))
    positioned = replace(ledger(100), security_code="000001.SZ")
    empty = ledger(0)
    lifecycle = initialize_position_lifecycle(
        ledger_state=empty, as_of=at(date(2024, 1, 1), 9), state_machine_version="1.1.0"
    )
    # Reuse the legal lifecycle fixture; only its security identity changes.
    entry = replace(transition(PositionTransitionKind.ENTRY_FILLED, 1), security_code="000001.SZ")
    lifecycle = apply_position_transition(
        lifecycle, entry, ledger_before=empty, ledger_after=positioned
    )
    if full_exit:
        request = replace(
            transition(PositionTransitionKind.EXIT_REQUESTED, 2), security_code="000001.SZ"
        )
        lifecycle = apply_position_transition(
            lifecycle, request, ledger_before=positioned, ledger_after=positioned
        )
    unit = _UnitRuntime(positioned, lifecycle, "000001.SZ", "000001.SZ", "opportunity-01")
    days = [date(2026, 4, 2), date(2026, 4, 3), date(2026, 4, 6)]
    action = CorporateActionStep(
        CorporateAction(
            action_id="bonus-1",
            security_code="000001.SZ",
            effective_date=days[1],
            known_at=at(date(2026, 4, 1), 10),
            cash_dividend_per_share=Decimal("0.05"),
            bonus_share_ratio=Decimal(fraction),
            source_revision="synthetic-v1",
            snapshot_id="action-1",
        ),
        at(days[1], 9),
        Decimal("8.60"),
        "bonus-mark",
    )
    frames = (
        frame(
            date(2026, 4, 1),
            days[0],
            session(
                days[0], "000001.SZ", "9.50", "9.50", OpenExecutionState.SELL_BLOCKED_LIMIT_DOWN
            ),
        ),
        frame(
            days[0],
            days[1],
            session(
                days[1], "000001.SZ", "8.60", "8.60", OpenExecutionState.SELL_BLOCKED_LIMIT_DOWN
            ),
            corporate_actions=(action,),
        ),
        frame(days[1], days[2], session(days[2], "000001.SZ", "8.70", "8.80")),
    )
    run = BacktestRunSpec(
        code_version="synthetic-v1",
        parameter_hash="sha256:synthetic",
        data_snapshot_id=frames[0].snapshot.snapshot_id,
        feature_version="synthetic",
        rule_version="synthetic",
        run_id="synthetic-bonus",
        execution_policy_version=engine.policy.policy_version,
        execution_profile_id="close-to-next-open-v1",
        schedule_version="close-to-next-open-synthetic-v1",
        official=False,
    )
    runtime = object.__new__(s.HistoricalBonusRuntime)
    runtime.engine, runtime.frames = engine, frames
    runtime._signal = lambda *args: BacktestSignal(
        signal_id="sell-1",
        snapshot=frames[0].snapshot,
        decision_at=frames[0].decision_at,
        profile_id="close-to-next-open-v1",
        window_id=None,
        security_code="000001.SZ",
        side=OrderSide.SELL,
        unit_id=positioned.unit_id,
        quantity=quantity,
        aggregate_score=Decimal("-1"),
        reason_code="test_exit",
        decision_version="synthetic",
    )
    return runtime, frames, unit, run


def test_original_runtime_still_rejects_bonus_sell():
    runtime, frames, unit, run = setup_case()
    with pytest.raises(ValueError, match="formal_sell_bonus_share_unsupported"):
        FormalP2ReplayRuntime._sell_schedule(
            runtime, 0, frames[0], unit, run, 100, True, "decision"
        )


def test_existing_proportional_order_and_lifecycle_carry_then_full_exit():
    runtime, frames, unit, run = setup_case()
    schedule = runtime._sell_schedule(0, frames[0], unit, run, 100, True, "decision")
    transitions = []
    for f in frames[:2]:
        runtime._apply_sell_schedule(f, unit, transitions, schedule, [])
        assert unit.lifecycle.stage is PositionStage.EXITING
    assert unit.ledger.settled_quantity == 110
    assert unit.ledger.dividend_income == Decimal("5.00")
    runtime._apply_sell_schedule(frames[2], unit, transitions, schedule, [])
    assert unit.lifecycle.stage is PositionStage.CLOSED
    assert unit.ledger.settled_quantity == 0
    assert [t.kind for t in transitions] == [
        PositionTransitionKind.EXIT_CARRIED,
        PositionTransitionKind.EXIT_CARRIED,
        PositionTransitionKind.EXIT_FILLED,
    ]
    orders = [e for e in schedule.events if e.event_type is EventType.ORDER_CREATED]
    fills = [e for e in schedule.events if e.event_type is EventType.FILL]
    assert len(orders) == len(fills) == 1
    assert fills[0].payload["quantity"] == 110


def test_fractional_pending_proportion_remains_unsupported():
    runtime, frames, unit, run = setup_case(quantity=55, full_exit=False)
    with pytest.raises(ValueError, match="pending sell proportion"):
        runtime._sell_schedule(0, frames[0], unit, run, 55, False, "decision")


def test_missing_action_evidence_cannot_explain_quantity_increase():
    runtime, frames, unit, run = setup_case()
    schedule = runtime._sell_schedule(0, frames[0], unit, run, 100, True, "decision")
    runtime._apply_sell_schedule(frames[0], unit, [], schedule, [])
    schedule.events = tuple(
        e for e in schedule.events if e.event_type is not EventType.CORPORATE_ACTION
    )
    with pytest.raises(ValueError, match="invalid_ledger_delta"):
        runtime._apply_sell_schedule(frames[1], unit, [], schedule, [])


def test_tampered_action_before_image_fails_before_lifecycle():
    runtime, frames, unit, run = setup_case()
    schedule = runtime._sell_schedule(0, frames[0], unit, run, 100, True, "decision")
    runtime._apply_sell_schedule(frames[0], unit, [], schedule, [])
    schedule.events = tuple(
        replace(e, payload={**e.payload, "quantity_before": 99})
        if e.event_type is EventType.CORPORATE_ACTION
        else e
        for e in schedule.events
    )
    with pytest.raises(ValueError, match="BONUS_ACTION_BEFORE_IMAGE"):
        runtime._apply_sell_schedule(frames[1], unit, [], schedule, [])


def test_integral_partial_sell_preserves_original_proportion():
    runtime, frames, unit, run = setup_case(quantity=50, full_exit=False)
    schedule = runtime._sell_schedule(0, frames[0], unit, run, 50, False, "decision")
    for f in frames:
        runtime._apply_sell_schedule(f, unit, [], schedule, [])
    assert unit.ledger.settled_quantity == 55
    assert unit.strategy_reduction_count == 1
    assert unit.lifecycle.stage is PositionStage.TRIAL
    assert schedule.fill_by_date[frames[2].execution_date].payload["quantity"] == 55


def test_tampered_action_delta_is_rejected():
    runtime, frames, unit, run = setup_case()
    schedule = runtime._sell_schedule(0, frames[0], unit, run, 100, True, "decision")
    runtime._apply_sell_schedule(frames[0], unit, [], schedule, [])
    schedule.events = tuple(
        replace(e, payload={**e.payload, "quantity_delta": 11})
        if e.event_type is EventType.CORPORATE_ACTION
        else e
        for e in schedule.events
    )
    with pytest.raises(ValueError, match="BONUS_ACTION_DELTA"):
        runtime._apply_sell_schedule(frames[1], unit, [], schedule, [])


def test_missing_execution_admission_refuses_source_open(tmp_path, monkeypatch):
    monkeypatch.setattr(
        s, "load_execution_request", lambda _: {"preparation_sha256": "x", "feature_sha256": "y"}
    )
    monkeypatch.setattr(s.parent.p10, "load_input", lambda *args: pytest.fail("source opened"))
    with pytest.raises(ValueError, match="BONUS_EXECUTION_ADMISSION"):
        s.HistoricalBonusRuntime(tmp_path, tmp_path, tmp_path, {})


@pytest.mark.parametrize(
    "field", [None, "code_sha256", "scope", "rule", "deadline_reset", "feature_sha256"]
)
def test_execution_supplement_binds_unchanged_preparation_and_new_code(
    tmp_path, monkeypatch, field
):
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    request = s.load_execution_request(root)
    for name in [
        s.REQUEST,
        s.parent.REQUEST,
        "src/quanthus/expert_strategy/p3_historical_bonus_successor.py",
    ]:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((root / name).read_bytes())
    scientific = s.parent.load_request(root)
    monkeypatch.setattr(s.parent, "load_request", lambda _: scientific)
    if field:
        request[field] = "unqualified"
        (tmp_path / s.REQUEST).write_text(json.dumps(request))
        with pytest.raises(ValueError, match="BONUS_EXECUTION_REQUEST"):
            s.load_execution_request(tmp_path)
    else:
        assert s.load_execution_request(tmp_path) == request

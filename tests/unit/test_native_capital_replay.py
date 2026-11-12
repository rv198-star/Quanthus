from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from quanthus.backtest.capital_clock import CapitalReplayClock
from quanthus.backtest.contracts import (
    BacktestRunSpec,
    BacktestSignal,
    CorporateAction,
    EventType,
    MarketSession,
    OpenExecutionState,
    OrderSide,
    UnitState,
)
from quanthus.backtest.engine import BacktestEngine, BacktestInputError
from quanthus.backtest.native_capital_replay import (
    VERSION,
    CapitalAuditEngine,
    FixedCapitalStrategy,
    NativeDecisionView,
    capital_decision,
)
from quanthus.backtest.policy import load_execution_policy
from quanthus.backtest.snapshot_clock import SnapshotReplayClock, parse_replay_clock
from quanthus.backtest.strategy_replay import (
    StrategyReplayContext,
    StrategyReplayFrame,
    StrategyReplaySpec,
    run_strategy_replay,
)
from quanthus.contracts import SnapshotRef

ROOT = Path(__file__).resolve().parents[2]
DIGEST = "sha256:" + "a" * 64
ZONE = ZoneInfo("Asia/Shanghai")
CUTOFF = datetime(2026, 11, 12, tzinfo=timezone.utc)
DAY = date(2020, 3, 2)


def policy():
    return load_execution_policy(ROOT / "config/p2-execution-policy.research.v1.json")


def clock(day=DAY):
    return CapitalReplayClock(day, CUTOFF, DIGEST)


def snapshot(day=DAY):
    return SnapshotRef(day, CUTOFF, "native-fixed", "SN-" + str(day))


def run(day=DAY):
    p = policy()
    return BacktestRunSpec(
        "git",
        "params",
        snapshot(day).snapshot_id,
        "r2",
        VERSION,
        "RUN",
        p.policy_version,
        "close-to-next-open-v1",
        p.profile("close-to-next-open-v1").schedule_version,
        data_content_digest=DIGEST,
    )


def signal(side=OrderSide.BUY, day=DAY):
    return BacktestSignal(
        "SIG-" + str(day),
        snapshot(day),
        clock(day).simulated_close,
        "close-to-next-open-v1",
        None,
        "S",
        side,
        "U",
        100,
        Decimal(0),
        "fixed",
        VERSION,
        clock(day).to_json(),
    )


def session(day, *, buy=True, sell=True, state=OpenExecutionState.EXECUTABLE):
    op = datetime.combine(day, datetime.min.time(), ZONE).replace(hour=9, minute=30)
    return MarketSession(
        day,
        op,
        op.replace(hour=15, minute=0),
        "S",
        Decimal(10),
        Decimal(10),
        state,
        "native-fixed",
        "EX-" + str(day),
        buy,
        sell,
    )


def state_after_buy(result):
    from quanthus.backtest.strategy_replay import _state_from_events

    return _state_from_events(result.events, target_notional=Decimal("100000.00"))


def test_clock_preserves_late_knowledge_and_default_pit_is_strict():
    c = clock()
    assert parse_replay_clock(c.to_json()) == c
    assert c.knowledge_cutoff.year == 2026 and c.simulated_close.year == 2020
    with pytest.raises(ValueError, match="authorized 2026"):
        SnapshotReplayClock(DAY, CUTOFF)
    with pytest.raises(ValueError, match="signal decision cannot precede"):
        replace(signal(), snapshot_replay_clock_json=None)
    with pytest.raises(ValueError, match="frame decision cannot precede"):
        StrategyReplayFrame(snapshot(), c.simulated_close, DAY + timedelta(days=1), ())
    StrategyReplayFrame(
        snapshot(),
        c.simulated_close,
        DAY + timedelta(days=1),
        (),
        snapshot_replay_clock_json=c.to_json(),
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("historical_pit_claim", True),
        ("actual_execution_authorized", True),
        ("market_date", "2022-01-03"),
        ("data_content_digest", "bad"),
        ("request_id", "P6"),
    ],
)
def test_clock_rejects_widened_scope_authority_or_identity(field, value):
    raw = clock().to_dict()
    raw[field] = value
    with pytest.raises(ValueError):
        CapitalReplayClock.from_json(json.dumps(raw))


def test_original_p2_money_and_minimum_fees_and_source_direction():
    engine = BacktestEngine(policy())
    initial = UnitState.empty(unit_id="U", cash=Decimal("100000.00"))
    tomorrow = DAY + timedelta(days=1)
    blocked = engine.run_buy_session(
        run=run(), signal=signal(), session=session(tomorrow, buy=False), opening_state=initial
    )
    assert not any(e.event_type is EventType.FILL for e in blocked.events)
    assert any(e.reason_code == "source_cannot_buy" for e in blocked.events)
    bought = engine.run_buy_session(
        run=run(), signal=signal(), session=session(tomorrow), opening_state=initial
    )
    fill = next(e.payload for e in bought.events if e.event_type is EventType.FILL)
    assert fill["price"] == "10.01" and fill["commission"] == "5.00"
    assert bought.result_summary["ending_cash"] == "98994.00"
    assert any("snapshot_replay_clock_json" in e.payload for e in bought.events)
    with pytest.raises(BacktestInputError, match="source content differs"):
        engine.run_buy_session(
            run=replace(run(), data_content_digest="sha256:" + "b" * 64),
            signal=signal(),
            session=session(tomorrow),
            opening_state=initial,
        )


def test_carried_native_sell_keeps_one_order_and_original_cash_rules():
    engine = CapitalAuditEngine(policy())
    initial = UnitState.empty(unit_id="U", cash=Decimal("100000.00"))
    bought = engine.run_buy_session(
        run=run(), signal=signal(), session=session(DAY + timedelta(days=1)), opening_state=initial
    )
    sold = engine.run_sell_sequence(
        run=run(DAY + timedelta(days=1)),
        signal=signal(OrderSide.SELL, DAY + timedelta(days=1)),
        sessions=(session(DAY + timedelta(days=2), sell=False), session(DAY + timedelta(days=3))),
        opening_state=state_after_buy(bought),
    )
    assert sold.result_summary["ending_cash"] == "99988.49"
    assert sum(e.event_type is EventType.ORDER_CREATED for e in sold.events) == 1
    assert any(e.reason_code == "source_cannot_sell" for e in sold.events)
    assert engine.receipts[-1]["carried"] == 1
    assert engine.receipts[-1]["system_wealth"] == "99988.49"


def test_native_action_keeps_real_known_at_and_requires_explicit_context():
    engine = BacktestEngine(policy())
    initial = UnitState.empty(unit_id="U", cash=Decimal("100000.00"))
    bought = engine.run_buy_session(
        run=run(), signal=signal(), session=session(DAY + timedelta(days=1)), opening_state=initial
    )
    day = DAY + timedelta(days=2)
    action = CorporateAction(
        "A",
        "S",
        day,
        CUTOFF - timedelta(days=1),
        Decimal("0.2"),
        Decimal("0.1"),
        "native-fixed",
        "ACTION-SN",
    )
    kw = dict(
        run=run(),
        opening_state=state_after_buy(bought),
        action=action,
        applied_at=session(day).open_at,
        post_action_mark_price=Decimal("9.10"),
        mark_snapshot_id="EX",
    )
    with pytest.raises(BacktestInputError, match="not known"):
        engine.apply_corporate_action(**kw)
    applied = engine.apply_corporate_action(**kw, snapshot_replay_clock_json=clock(day).to_json())
    assert applied.state.settled_quantity == 110 and applied.state.cash == Decimal("99014.00")
    assert applied.events[0].payload["known_at"] == action.known_at.isoformat()
    with pytest.raises(BacktestInputError, match="identity differs"):
        engine.apply_corporate_action(**kw, snapshot_replay_clock_json=clock().to_json())


def test_T_sizing_and_fixed_exit_never_use_next_open_for_quantity():
    engine = CapitalAuditEngine(policy())
    strategy = FixedCapitalStrategy()
    frames = []
    for offset in range(3):
        day = DAY + timedelta(days=offset)
        snap = snapshot(day)
        choice = (
            {
                "listing_id": "S",
                "decision_close": "10.00",
                "ranking_vol12": "0.1",
                "decision_hold": False,
            }
            if offset == 0
            else None
        )
        frames.append(
            StrategyReplayFrame(
                snap,
                clock(day).simulated_close,
                day + timedelta(days=1),
                (session(day + timedelta(days=1)),),
                decision_view=NativeDecisionView(snap, choice),
                snapshot_replay_clock_json=clock(day).to_json(),
            )
        )
    result = run_strategy_replay(
        engine, strategy, StrategyReplaySpec(run(), "U", Decimal("100000.00"), frames)
    )
    assert strategy.nominal_attempts[0]["quantity"] == 2400
    assert len(result.trades) == 1
    assert result.trades[0].entry_date == DAY + timedelta(days=1)
    assert result.trades[0].exit_date == DAY + timedelta(days=2)
    assert result.final_state.settled_quantity == 0


def test_decision_T_hold_does_not_choose_an_alternative():
    choice = {
        "listing_id": "nominal",
        "decision_close": "10",
        "ranking_vol12": "0.1",
        "decision_hold": True,
    }
    s = FixedCapitalStrategy()
    context = StrategyReplayContext(
        snapshot(),
        clock().simulated_close,
        UnitState.empty(unit_id="U", cash=Decimal("100000.00")),
        NativeDecisionView(snapshot(), choice),
    )
    assert s.decide(context).reason_code == "T_input_unqualified"
    assert s.nominal_attempts[0]["listing_id"] == "nominal"


def test_blocked_arm_never_exposes_an_adoption_metric():
    d = capital_decision({"candidate": {"status": "blocked"}, "control": {"status": "completed"}})
    assert d == {"branch": "blocked", "verdict": "not_evaluated", "primary_increment": None}


def held_store(day):
    from collections import defaultdict

    from quanthus.backtest.native_capital_replay import NativeCapitalStore

    store = NativeCapitalStore.__new__(NativeCapitalStore)
    store.identities = {
        "L1": {"security_id": "S1", "listing_code": "000001"},
        "L2": {"security_id": "S2", "listing_code": "000002"},
    }
    store.native_cache = {(name, day): {} for name in store.identities}
    store.actions = defaultdict(list)
    store.unknown_action_dates = defaultdict(list)
    store.terms = defaultdict(list)
    store.exceptions = defaultdict(list)
    store.accesses = []
    store.arm = "candidate"
    store.last_access = {}
    return store


def test_multi_action_hold_is_local_to_the_held_economic_coordinate():
    from quanthus.backtest.native_capital_replay import CapitalInputHold

    store = held_store(DAY)
    store.actions["S1", DAY] = [{"corporate_action_id": "A"}, {"corporate_action_id": "B"}]
    with pytest.raises(CapitalInputHold, match="ACTION_EVENT_AMBIGUOUS") as captured:
        store.action_hold("L1", DAY)
    assert captured.value.detail["action_ids"] == ["A", "B"]
    assert store.action_hold("L2", DAY) is None


def test_unresolved_source_exception_stays_a_hold_without_zero_fill():
    from quanthus.backtest.native_capital_replay import CapitalInputHold

    store = held_store(DAY)
    store.exceptions["000001", DAY] = ["unresolved-four-case"]
    with pytest.raises(CapitalInputHold, match="UNRESOLVED_SOURCE_ACTION_EXCEPTION"):
        store.action_hold("L1", DAY)
    assert store.action_hold("L2", DAY) is None


def test_default_sell_cannot_receive_a_capital_action_context():
    from quanthus.backtest.contracts import CorporateActionStep

    engine = BacktestEngine(policy())
    initial = UnitState.empty(unit_id="U", cash=Decimal("100000.00"))
    bought = engine.run_buy_session(
        run=run(), signal=signal(), session=session(DAY + timedelta(days=1)), opening_state=initial
    )
    day = DAY + timedelta(days=2)
    t = day - timedelta(days=1)
    ordinary = replace(
        signal(OrderSide.SELL, t),
        snapshot=SnapshotRef(t, clock(t).simulated_close, "native-fixed", snapshot(t).snapshot_id),
        snapshot_replay_clock_json=None,
    )
    action = CorporateAction(
        "A", "S", day, CUTOFF, Decimal(".1"), Decimal(0), "native-fixed", "A-SN"
    )
    step = CorporateActionStep(
        action, session(day).open_at, Decimal(10), "EX", clock(day).to_json()
    )
    with pytest.raises(BacktestInputError, match="matching parent capital context"):
        engine.run_sell_sequence(
            run=run(t),
            signal=ordinary,
            sessions=(session(day),),
            opening_state=state_after_buy(bought),
            corporate_actions=(step,),
        )


def test_mixed_source_cutoffs_fail_before_replay():
    frames = []
    for offset in range(2):
        day = DAY + timedelta(days=offset)
        cut = CUTOFF + timedelta(hours=offset)
        c = CapitalReplayClock(day, cut, DIGEST)
        snap = SnapshotRef(day, cut, "native-fixed", "X" + str(day))
        frames.append(
            StrategyReplayFrame(
                snap,
                c.simulated_close,
                day + timedelta(days=1),
                (),
                snapshot_replay_clock_json=c.to_json(),
            )
        )
    with pytest.raises(ValueError, match="one capital source cutoff"):
        StrategyReplaySpec(run(), "U", Decimal("100000.00"), frames)


def test_unknown_action_date_cannot_be_assigned_to_a_weekend_placeholder():
    from quanthus.backtest.native_capital_replay import CapitalInputHold

    store = held_store(DAY)
    store.unknown_action_dates["S1"] = ["UNKNOWN_EX_DATE"]
    with pytest.raises(CapitalInputHold, match="ACTION_BUSINESS_DATE_UNKNOWN"):
        store.action_hold("L1", DAY)
    assert store.action_hold("L2", DAY) is None

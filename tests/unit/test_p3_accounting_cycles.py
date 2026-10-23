"""Actual P2 cycle regressions retained from the independent accounting review."""

from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from quanthus.aggregator import GovernedAction
from quanthus.backtest.contracts import (
    BacktestRunSpec,
    CorporateAction,
    EventType,
    MarketSession,
    OpenExecutionState,
    UnitState,
)
from quanthus.backtest.engine import BacktestEngine
from quanthus.backtest.policy import load_execution_policy
from quanthus.backtest.portfolio_replay import _wealth
from quanthus.concentration import StaticIndustryMembership
from quanthus.contracts import SnapshotRef
from quanthus.expert_strategy.formal_p2_accounting_v2 import AccountingCorrectedReplayRuntime
from quanthus.expert_strategy.formal_p2_replay import (
    FormalP2ReplayRuntime,
    _transition,
    _UnitRuntime,
)
from quanthus.expert_strategy.registry import ExpertStrategyRegistry
from quanthus.policy import load_policy
from quanthus.position_lifecycle import (
    POSITION_STATE_MACHINE_TRIAL_REDUCTION_VERSION,
    PositionEvidenceKind,
    PositionEvidenceRef,
    PositionStage,
    PositionTransitionKind,
    apply_position_transition,
    initialize_position_lifecycle,
)

REPO = Path(__file__).resolve().parents[2]
TZ = ZoneInfo("Asia/Shanghai")
D = Decimal


def ts(day, hour, minute=0):
    return datetime.combine(day, datetime.min.time(), TZ).replace(hour=hour, minute=minute)


def frame(day, price="10", state=OpenExecutionState.EXECUTABLE):
    nxt = day + timedelta(days=1)
    observed_price = (
        None
        if state in (OpenExecutionState.SUSPENDED, OpenExecutionState.MISSING_EVIDENCE)
        else D(price)
    )
    session = MarketSession(
        nxt,
        ts(nxt, 9, 30),
        ts(nxt, 15),
        "synthetic-security",
        observed_price,
        observed_price,
        state,
        "synthetic-revision",
        "synthetic-session-" + str(nxt),
    )
    return SimpleNamespace(
        snapshot=SnapshotRef(
            day, ts(day, 15), "synthetic-revision", "synthetic-snapshot-" + str(day)
        ),
        decision_at=ts(day, 15, 5),
        execution_date=nxt,
        session=lambda *_: session,
        actions=lambda *_: (),
    )


def setup(cls=AccountingCorrectedReplayRuntime):
    r = object.__new__(cls)
    r.registry = ExpertStrategyRegistry(REPO)
    r.p0_policy = load_policy(REPO / "config/p0-policy.v1.json")
    r.engine = BacktestEngine(
        load_execution_policy(REPO / "tests/fixtures/p2/execution-policy.synthetic.v1.json")
    )
    r.industry = SimpleNamespace(
        by_security_id={
            "synthetic-security": StaticIndustryMembership(
                "synthetic-security",
                "synthetic-industry",
                "synthetic-mapping",
                "synthetic-source",
                "synthetic-lineage",
            )
        }
    )
    empty = replace(
        UnitState.empty(unit_id="unit-review", cash=D("100000.00")), external_pool=D("1234.00")
    )
    s = _UnitRuntime(
        ledger=empty,
        lifecycle=initialize_position_lifecycle(
            ledger_state=empty,
            as_of=ts(date(2020, 1, 1), 15),
            state_machine_version=POSITION_STATE_MACHINE_TRIAL_REDUCTION_VERSION,
        ),
    )
    s.entry_wealth = D("7")
    s.entry_gross = D("123")
    s.entry_fees = D("1")
    s.strategy_reduction_count = 1
    s.deterioration_streak = 9
    s.holding_sessions = 30
    policy = r.engine.policy
    run = BacktestRunSpec(
        code_version="synthetic-review-v2",
        parameter_hash="synthetic",
        data_snapshot_id="synthetic",
        feature_version="synthetic",
        rule_version="synthetic",
        run_id="synthetic-review",
        execution_policy_version=policy.policy_version,
        execution_profile_id=policy.active_profile.profile_id,
        schedule_version=policy.active_profile.schedule_version,
        official=False,
    )
    return r, s, run


def candidate(day, close="10"):
    return SimpleNamespace(
        opportunity_id="synthetic-opportunity-" + str(day),
        feature=SimpleNamespace(close_price=D(close)),
        item=SimpleNamespace(
            panel_security_id="synthetic-security",
            record=SimpleNamespace(
                identity=SimpleNamespace(security_id="synthetic-security"),
                event_id="synthetic-entry-" + str(day),
            ),
        ),
        decision=SimpleNamespace(
            target_maximum_position=D(".20"),
            digest="synthetic-decision-" + str(day),
            action=SimpleNamespace(
                final_action=GovernedAction.TRIAL_ENTRY,
                aggregate_score=D("1"),
                reason_codes=("synthetic-entry",),
            ),
        ),
    )


def execute(r, s, run, f, events, transitions, *, entries=()):
    decisions = []
    r._execute_frame(
        0,
        f,
        {s.ledger.unit_id: s},
        entries,
        D("100000"),
        r.registry.card.semantic_sections["position_and_execution"],
        run,
        decisions,
        transitions,
        events,
    )
    return decisions


def open_position(cls=AccountingCorrectedReplayRuntime):
    r, s, run = setup(cls)
    events = []
    transitions = []
    f = frame(date(2020, 1, 2))
    old = s.ledger
    decisions = execute(
        r, s, run, f, events, transitions, entries=(candidate(f.snapshot.market_date),)
    )
    assert s.lifecycle.stage is PositionStage.TRIAL and decisions[0]["filled"]
    return r, s, run, events, transitions, old


def sell(r, s, run, events, transitions, day, price="10", full=False, first_blocked=False):
    frames = [
        frame(
            day,
            price,
            OpenExecutionState.SELL_BLOCKED_LIMIT_DOWN
            if first_blocked
            else OpenExecutionState.EXECUTABLE,
        )
    ]
    if first_blocked:
        frames.append(frame(day + timedelta(days=1), price))
    r.frames = frames
    f = frames[0]
    if full:
        transition = _transition(
            s,
            PositionTransitionKind.EXIT_REQUESTED,
            f.decision_at,
            ("synthetic-exit",),
            PositionEvidenceRef(
                PositionEvidenceKind.DECISION, "synthetic-exit-" + str(day), "synthetic"
            ),
        )
        s.lifecycle = apply_position_transition(
            s.lifecycle, transition, ledger_before=s.ledger, ledger_after=s.ledger
        )
        transitions.append(transition)
    quantity = s.ledger.settled_quantity if full else s.ledger.settled_quantity // 2
    schedule = r._sell_schedule(0, f, s, run, quantity, full, "synthetic-sell-" + str(day))
    events.extend(schedule.events)
    s.sell_schedule = schedule
    r._apply_sell_schedule(f, s, transitions, schedule, events)
    return frames, schedule


def rate(x):
    return x.quantize(D(".00000001"), rounding=ROUND_HALF_UP)


def independent_cycle_return(s, before):
    pnl = (
        s.ledger.last_mark_price * s.ledger.settled_quantity
        - s.ledger.position_cost
        + s.ledger.realized_pnl
        - before.realized_pnl
        + s.ledger.dividend_income
        - before.dividend_income
        - s.entry_fees
    )
    return rate(pnl / (s.entry_gross + s.entry_fees))


def test_actual_successful_buy_records_prebuy_wealth_and_resets_only_current_cycle():
    r, s, run, ev, tr, before = open_position()
    assert s.entry_wealth == _wealth(before) == D("101234")
    assert (
        s.strategy_reduction_count,
        s.deterioration_streak,
        s.holding_sessions,
        s.cooldown_sessions,
    ) == (0, 0, 1, 0)
    assert r._marked_return(s) == independent_cycle_return(s, before)
    assert r._marked_return(s) < 0  # actual slippage and fee remain, no fabricated zero


@pytest.mark.parametrize(
    "state",
    [
        OpenExecutionState.SUSPENDED,
        OpenExecutionState.BUY_BLOCKED_LIMIT_UP,
        OpenExecutionState.MISSING_EVIDENCE,
    ],
)
def test_actual_failed_buy_does_not_capture_new_baseline_or_reset_cycle(state):
    r, s, run = setup()
    f = frame(date(2020, 1, 2), state=state)
    ev = []
    tr = []
    previous = (
        s.entry_wealth,
        s.entry_gross,
        s.entry_fees,
        s.strategy_reduction_count,
        s.deterioration_streak,
        s.holding_sessions,
    )
    decisions = execute(r, s, run, f, ev, tr, entries=(candidate(f.snapshot.market_date),))
    assert s.lifecycle.stage is PositionStage.EMPTY and not decisions[0]["filled"]
    assert not any(e.event_type is EventType.FILL for e in ev)
    assert previous == (
        s.entry_wealth,
        s.entry_gross,
        s.entry_fees,
        s.strategy_reduction_count,
        s.deterioration_streak,
        s.holding_sessions,
    )


def test_actual_zero_quantity_does_not_reset_cycle():
    r, s, run = setup()
    f = frame(date(2020, 1, 2))
    ev = []
    tr = []
    execute(r, s, run, f, ev, tr, entries=(candidate(f.snapshot.market_date, close="1000000"),))
    assert not ev and s.entry_wealth == 7 and s.strategy_reduction_count == 1


@pytest.mark.parametrize("dividend", ["0", "1"])
def test_actual_partial_sale_and_dividend_match_independent_pnl_with_fees(dividend):
    r, s, run, ev, tr, before = open_position()
    mark = "10"
    if dividend != "0":
        day = date(2020, 1, 4)
        action = CorporateAction(
            "synthetic-dividend",
            "synthetic-security",
            day,
            ts(day, 8),
            D(dividend),
            D(0),
            "synthetic-source",
            "synthetic-action",
        )
        application = r.engine.apply_corporate_action(
            run=run,
            opening_state=s.ledger,
            action=action,
            applied_at=ts(day, 9),
            post_action_mark_price=D("9"),
            mark_snapshot_id="synthetic-ex-mark",
        )
        s.ledger = application.state
        ev.extend(application.events)
        mark = "9"
    sell(r, s, run, ev, tr, date(2020, 1, 5), price=mark)
    assert s.strategy_reduction_count == 1 and s.ledger.settled_quantity > 0
    assert r._marked_return(s) == independent_cycle_return(s, before)
    assert FormalP2ReplayRuntime._marked_return(r, s) < D("-.4")
    assert r._marked_return(s) > D("-.01")


def test_carried_partial_sell_counts_only_when_filled():
    r, s, run, ev, tr, before = open_position()
    frames, schedule = sell(r, s, run, ev, tr, date(2020, 1, 5), first_blocked=True)
    assert s.strategy_reduction_count == 0 and s.sell_schedule is not None
    r._apply_sell_schedule(frames[1], s, tr, schedule, ev)
    assert s.strategy_reduction_count == 1 and s.sell_schedule is None
    assert r._marked_return(s) == independent_cycle_return(s, before)


def test_actual_full_exit_cooldown_reentry_resets_baseline_and_count():
    r, s, run, ev, tr, before = open_position()
    sell(r, s, run, ev, tr, date(2020, 1, 5), price="11")
    assert s.strategy_reduction_count == 1
    sell(r, s, run, ev, tr, date(2020, 1, 7), price="11", full=True)
    assert s.lifecycle.stage is PositionStage.CLOSED and s.ledger.settled_quantity == 0
    assert s.ledger.external_pool > before.external_pool
    frozen_baseline = s.entry_wealth
    for i in range(5):
        f = frame(date(2020, 1, 8) + timedelta(days=i))
        decisions = execute(r, s, run, f, ev, tr, entries=(candidate(f.snapshot.market_date),))
        assert not any(d["filled"] for d in decisions)
        assert s.entry_wealth == frozen_baseline and s.strategy_reduction_count == 1
    assert s.lifecycle.stage is PositionStage.EMPTY
    new_baseline = _wealth(s.ledger)
    f = frame(date(2020, 1, 13))
    execute(r, s, run, f, ev, tr, entries=(candidate(f.snapshot.market_date),))
    assert s.lifecycle.stage is PositionStage.TRIAL and s.entry_wealth == new_baseline
    assert s.entry_wealth != frozen_baseline and s.strategy_reduction_count == 0
    assert s.deterioration_streak == 0 and s.holding_sessions == 1 and s.cooldown_sessions == 0
    assert r._marked_return(s) < 0  # old cycle profit is excluded, current costs remain


def test_v1_successful_buy_keeps_legacy_state_and_new_field_unused():
    r, s, run, ev, tr, before = open_position(FormalP2ReplayRuntime)
    assert s.entry_wealth == 7 and s.strategy_reduction_count == 1 and s.deterioration_streak == 9
    assert s.holding_sessions == 1


def test_v2_nonempty_prebuy_rejected_without_mutating_runtime():
    r, s, run, ev, tr, before = open_position()
    old = (s.entry_wealth, s.entry_gross, s.entry_fees, s.strategy_reduction_count)
    fill = next(e for e in ev if e.event_type is EventType.FILL)
    with pytest.raises(ValueError, match="ENTRY_REQUIRES_EMPTY"):
        r._record_entry(s, s.ledger, fill)
    assert old == (s.entry_wealth, s.entry_gross, s.entry_fees, s.strategy_reduction_count)

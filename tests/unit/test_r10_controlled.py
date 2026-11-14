import json
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from quanthus.backtest.capital_clock import CapitalReplayClock, bind_capital_action_context
from quanthus.backtest.contracts import BacktestRunSpec, OpenExecutionState, UnitState
from quanthus.backtest.engine import BacktestEngine
from quanthus.backtest.policy import load_execution_policy
from quanthus.backtest.snapshot_clock import SnapshotReplayClock
from quanthus.r10_inputs import digest
from quanthus.r10_prepare import source_select
from quanthus.r10_replay import NativePanel, NativeStore, event_predictions, observed_action_mark


def clock(day=date(2021, 6, 2), **changes):
    source = "sha256:" + "1" * 64
    identity = {
        "native_content_digest": source,
        "purpose_sha256": "sha256:" + "2" * 64,
        "consumer_closure_sha256": "sha256:" + "2" * 64,
        "native_knowledge_cutoff": "2026-11-11T20:19:54+00:00",
        "purpose_qualified_at": "2026-11-14T08:07:52+00:00",
    }
    identity.update(changes)
    return CapitalReplayClock(
        day,
        datetime(2026, 11, 11, 20, 19, 54, tzinfo=timezone.utc),
        source,
        "RESEARCH-R10-001",
        "RESEARCH-R10-001-T1",
        "A1",
        json.dumps(identity, sort_keys=True, separators=(",", ":")),
    )


def test_controlled_clock_binds_actual_source_and_simulated_run():
    c = clock()
    assert c.to_dict()["schema_version"] == "quanthus.r10-controlled-clock.v1"
    assert c.simulated_close.year == 2021 and c.knowledge_cutoff.year == 2026
    assert CapitalReplayClock.from_json(c.to_json()) == c
    c.validate_run("RESEARCH-R10-001-T1-A1-C0")
    with pytest.raises(ValueError):
        c.validate_run("RESEARCH-R10-001-T2-A1-C0")
    bind_capital_action_context(clock(date(2021, 6, 3)).to_json(), c.to_json())
    with pytest.raises(ValueError):
        bind_capital_action_context(c.to_json(), None)
    with pytest.raises(ValueError):
        clock(native_content_digest="sha256:" + "3" * 64)
    with pytest.raises(ValueError):
        clock(native_knowledge_cutoff="2026-11-10T20:19:54+00:00")
    with pytest.raises(ValueError):
        clock(consumer_closure_sha256="sha256:" + "3" * 64)
    with pytest.raises(ValueError):
        SnapshotReplayClock.from_json(c.to_json())


def sample(company, listing, width):
    return {
        "available": True,
        "identity": {"company_id": company, "listing_id": listing, "anchor": "2021-05-31"},
        "primary_facts": {
            k: {"value": v}
            for k, v in {
                "directional_efficiency": ".1",
                "width_change": "-.3",
                "range_width": width,
            }.items()
        },
    }


def test_selection_company_unique_and_source_order_deterministic():
    pool = [
        sample("C1", "L1", ".1"),
        sample("C1", "L2", ".11"),
        sample("C2", "L3", ".2"),
        sample("C3", "L4", ".3"),
    ]
    a = source_select(pool, {"narrow": 2}, set())
    assert [r["source_row"]["identity"]["company_id"] for r in a] == ["C1", "C2"]
    assert source_select(list(reversed(pool)), {"narrow": 2}, set()) == a
    with pytest.raises(ValueError, match="SOURCE_QUOTA"):
        source_select(pool, {"narrow": 4}, set())


@pytest.mark.parametrize("close", [None, 0, float("nan"), float("inf")])
def test_missing_or_invalid_close_is_unavailable(monkeypatch, close):
    monkeypatch.setattr("quanthus.r10_replay.rows", lambda *args: [{"close_price": close}])
    panel = object.__new__(NativePanel)
    panel.native, panel.cache = SimpleNamespace(db=None), {}
    assert panel.feature(date(2021, 6, 2), "L1") is None


def action_store():
    day = date(2021, 6, 15)
    op = datetime(2021, 6, 15, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    cal = {"open_at": op, "close_at": op.replace(hour=15, minute=0)}
    known = datetime(2026, 11, 1, tzinfo=timezone.utc)
    row = {
        "listing_currency": "CNY",
        "lot_size": 100,
        "currency": "CNY",
        "price_basis": "UNADJUSTED",
        "bar_start": cal["open_at"],
        "bar_end": cal["close_at"],
        "open": Decimal("9.00"),
        "high": Decimal("9.50"),
        "low": Decimal("8.90"),
        "close": Decimal("9.20"),
        "lower_price": Decimal("11.06"),
        "upper_price": Decimal("13.52"),
        "is_tradable": True,
        "can_buy": True,
        "can_sell": True,
        "reason_code": "TRADABLE_OBSERVED",
        "source_record_id": "native-observed",
        "source_revision": "native-rev",
        "source_artifact_sha256": "a" * 64,
        **{k: known for k in ("bar_known", "state_known", "listing_known", "security_known")},
    }
    header = {
        "corporate_action_id": "A",
        "security_id": "S",
        "action_status": "IMPLEMENTED",
        "action_type": "MIXED_DISTRIBUTION",
        "effective_at": op,
        "ex_date": day,
        "knowledge_from": known,
        "announced_at": None,
    }
    terms = [
        {
            "corporate_action_term_id": "cash",
            "corporate_action_id": "A",
            "effective_at": op,
            "term_type": "CASH_AMOUNT_PER_SHARE",
            "knowledge_from": known,
            "currency": "CNY",
            "amount_per_share": Decimal("0.158"),
        },
        {
            "corporate_action_term_id": "shares",
            "corporate_action_id": "A",
            "effective_at": op,
            "term_type": "TRANSFER_SHARES_PER_SHARE",
            "knowledge_from": known,
            "ratio_numerator": Decimal(3),
            "ratio_denominator": Decimal(10),
        },
    ]
    store = object.__new__(NativeStore)
    store.cutoff, store.revision, store.native_revision = clock(day).knowledge_cutoff, "R", "R"
    store.run_prefix = "RESEARCH-R10-001-T1-A1"
    store.native_inputs = SimpleNamespace(clock=clock)
    store.identities = {"L": {"security_id": "S", "venue_id": "V", "listing_code": "603918"}}
    store.calendars = {("V", day): cal}
    store.native = lambda *_args: row
    store.actions, store.terms = {("S", day): [header]}, {"A": terms}
    store.exceptions, store.unknown_action_dates = {}, {}
    store.accesses, store.arm, store.last_access = [], "", {}
    return store, row, cal, day


def test_observed_action_valuation_keeps_limit_conflict_execution_blocked():
    store, row, _, day = action_store()
    original_prices = dict(row)
    session = store.session("L", day)
    assert session.open_execution_state is OpenExecutionState.MISSING_EVIDENCE
    assert session.open_price is None and session.close_price is None
    (step,) = store.action_steps("L", day)
    assert step.post_action_mark_price == Decimal("9.00")
    assert row == original_prices
    policy = load_execution_policy(
        Path(__file__).resolve().parents[2] / "config/p2-execution-policy.research.v1.json"
    )
    run = BacktestRunSpec(
        "test",
        "params",
        "SN",
        "F",
        "R",
        "RESEARCH-R10-001-T1-A1-C0",
        policy.policy_version,
        "close-to-next-open-v1",
        policy.active_profile.schedule_version,
        data_content_digest=clock(day).data_content_digest,
    )
    before = replace(
        UnitState.empty(unit_id="U", cash=Decimal("100000.00")),
        security_code="L",
        settled_quantity=100,
        position_cost=Decimal("1000.00"),
        cash=Decimal("99000.00"),
        last_mark_price=Decimal("12.00"),
        last_mark_snapshot_id="previous-observed",
    )
    engine = BacktestEngine(policy)
    applied = engine.apply_corporate_action(
        run=run,
        opening_state=before,
        action=step.action,
        applied_at=step.applied_at,
        post_action_mark_price=step.post_action_mark_price,
        mark_snapshot_id=step.mark_snapshot_id,
        snapshot_replay_clock_json=step.snapshot_replay_clock_json,
    )
    assert applied.state.settled_quantity == 130
    assert applied.state.cash == Decimal("99015.80")
    assert applied.state.last_mark_price == Decimal("9.00")
    assert not any(e.event_type.value == "fill" for e in applied.events)
    marked = engine.mark_position_session(run=run, session=session, opening_state=applied.state)
    assert marked.state.last_mark_price == Decimal("9.00")
    assert store.session("L", day).open_price is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("open", None),
        ("open", Decimal(0)),
        ("close", Decimal("NaN")),
        ("currency", "USD"),
        ("price_basis", "ADJUSTED"),
        ("source_record_id", ""),
        ("bar_known", datetime(2027, 1, 1, tzinfo=timezone.utc)),
        ("bar_end", None),
    ],
)
def test_action_mark_rejects_missing_invalid_or_wrong_clock(field, value):
    store, row, cal, _ = action_store()
    row[field] = value
    assert not observed_action_mark(row, cal, store.cutoff)


def test_capital_predictions_exclude_non_event_roles_and_reject_conflicts():
    entry = {"event_id": "E1", "case_id": "S1", "role": "opportunity", "v0": 0.2, "v1": 0.6}
    rows = [
        entry,
        dict(entry, role="evaluation"),
        {"event_id": None, "case_id": "guard", "v0": 0.1, "v1": 0.3},
    ]
    result = event_predictions({"rows": rows})
    assert set(result) == {"E1"}
    assert digest(result).startswith("sha256:")
    with pytest.raises(ValueError, match="CONFLICTING_EVENT"):
        event_predictions({"rows": [entry, dict(entry, v1=0.8)]})


def test_pending_sell_integer_action_updates_order_and_lifecycle_once():
    from datetime import timedelta

    from quanthus.backtest.contracts import BacktestSignal, EventType, OrderSide
    from quanthus.backtest.portfolio_replay import _state_from_snapshot
    from quanthus.contracts import SnapshotRef
    from quanthus.expert_strategy.formal_p2_replay import FormalP2ReplayRuntime, _SellSchedule
    from quanthus.position_lifecycle import (
        POSITION_STATE_MACHINE_TRIAL_REDUCTION_VERSION,
        PositionLifecycleState,
        PositionStage,
    )
    from quanthus.r10_replay import ControlledReplay

    store, _, cal, day = action_store()
    (step,) = store.action_steps("L", day)
    with pytest.raises(ValueError, match="formal_sell_bonus_share_unsupported"):
        FormalP2ReplayRuntime._validate_carried_sell_actions(None, [step])
    candidate = object.__new__(ControlledReplay)
    candidate._validate_carried_sell_actions([step])
    policy = load_execution_policy(
        Path(__file__).resolve().parents[2] / "config/p2-execution-policy.research.v1.json"
    )
    candidate.engine = BacktestEngine(policy)
    before = replace(
        UnitState.empty(unit_id="U", cash=Decimal("100000.00")),
        security_code="L",
        settled_quantity=100,
        position_cost=Decimal("1000.00"),
        cash=Decimal("99000.00"),
        last_mark_price=Decimal("12.00"),
        last_mark_snapshot_id="old-mark",
    )
    decision = date(2021, 6, 11)
    snap = SnapshotRef(decision, store.cutoff, "R", "SN")
    run = BacktestRunSpec(
        "test",
        "params",
        "SN",
        "F",
        "R",
        "RESEARCH-R10-001-T1-A1-C2",
        policy.policy_version,
        "close-to-next-open-v1",
        policy.active_profile.schedule_version,
        data_content_digest=clock(day).data_content_digest,
    )
    signal = BacktestSignal(
        "SELL",
        snap,
        clock(decision).simulated_close,
        "close-to-next-open-v1",
        None,
        "L",
        OrderSide.SELL,
        "U",
        100,
        Decimal("-1"),
        "exit",
        "V",
        clock(decision).to_json(),
    )
    held = store.session("L", day)
    next_day = day + timedelta(days=1)
    executable = replace(
        held,
        session_date=next_day,
        open_at=cal["open_at"] + timedelta(days=1),
        close_at=cal["close_at"] + timedelta(days=1),
        open_price=Decimal("9.20"),
        close_price=Decimal("9.30"),
        open_execution_state=OpenExecutionState.EXECUTABLE,
    )
    result = candidate.engine.run_sell_sequence(
        run=run,
        signal=signal,
        sessions=[held, executable],
        opening_state=before,
        corporate_actions=[step],
    )
    states, fills, carries = {}, {}, {}
    for e in result.events:
        if e.event_type is EventType.DAILY_SNAPSHOT:
            states[date.fromisoformat(e.payload["session_date"])] = _state_from_snapshot(
                e, target_notional=before.target_notional
            )
        elif e.event_type is EventType.FILL:
            fills[e.event_time.date()] = e
        elif e.event_type is EventType.ORDER_STATUS and e.payload["transition"].endswith(
            "to_carried"
        ):
            carries[e.event_time.date()] = e
    schedule = _SellSchedule(True, states, fills, carries, result.events)
    runtime = SimpleNamespace(
        ledger=before,
        opportunity_id="OP",
        formal_security_id="S",
        panel_security_id="L",
        holding_sessions=3,
        cooldown_sessions=0,
        sell_schedule=schedule,
        strategy_reduction_count=0,
        lifecycle=PositionLifecycleState(
            "U",
            PositionStage.EXITING,
            "L",
            "OP",
            POSITION_STATE_MACHINE_TRIAL_REDUCTION_VERSION,
            clock(decision).simulated_close,
            (),
            (),
        ),
    )
    transitions = []
    candidate._apply_sell_schedule(
        SimpleNamespace(execution_date=day), runtime, transitions, schedule, []
    )
    assert runtime.ledger.settled_quantity == 130
    assert runtime.ledger.dividend_income == Decimal("15.80")
    assert runtime.lifecycle.stage is PositionStage.EXITING
    candidate._apply_sell_schedule(
        SimpleNamespace(execution_date=next_day), runtime, transitions, schedule, []
    )
    assert runtime.ledger.settled_quantity == 0
    assert runtime.ledger.dividend_income == Decimal("15.80")
    assert runtime.lifecycle.stage is PositionStage.CLOSED
    assert fills[next_day].payload["quantity"] == 130
    assert len(transitions) == 2

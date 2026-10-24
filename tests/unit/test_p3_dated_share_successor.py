"""Real matcher/lifecycle and calendar closure for the approved research policy."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from quanthus.backtest.contracts import EventType
from quanthus.expert_strategy import p3_dated_share_successor as s
from quanthus.position_lifecycle import PositionStage, PositionTransitionKind
from tests.unit.test_p3_dated_share_engine import case
from tests.unit.test_p3_historical_bonus_successor import setup_case
from tests.unit.test_p10_historical_distribution_source import _reader_connection


def test_new_request_preserves_frozen_science_and_old_consumer_contract():
    repository = Path(__file__).resolve().parents[2]
    old = s.parent.load_request(repository)
    request = s.load_request(repository)
    for field in ("arms", "scope", "analysis", "optional_rights_policy"):
        assert request[field] == old[field]
    assert request["share_availability_model"] == s.AVAILABILITY
    assert request["maximum_additional_wall_seconds"] == 7200
    assert request["new_collection_authorized"] is False
    assert request["historical_PIT_claim"] is False


def test_missing_exact_admission_refuses_real_input_open(tmp_path, monkeypatch):
    request = s.load_request(Path(__file__).resolve().parents[2])
    monkeypatch.setattr(s, "load_request", lambda _: request)
    monkeypatch.setattr(s.p10, "load_input", lambda *args: pytest.fail("source opened"))
    with pytest.raises(ValueError, match="EXACT_ADMISSION"):
        s.DatedShareRuntime(tmp_path, tmp_path, tmp_path, {})


def runtime_case():
    _, frames, unit, _ = setup_case(year=2019, fraction="0.4")
    engine, run, signal, _, sessions, step = case()
    frames = (
        frames[0],
        replace(frames[1], execution_sessions=(sessions[0],), corporate_actions=(step,)),
        frames[2],
    )
    runtime = object.__new__(s.DatedShareRuntime)
    runtime.engine, runtime.frames = engine, frames
    runtime._signal = lambda *args: signal
    schedule = runtime._sell_schedule(0, frames[0], unit, run, 100, True, "decision")
    unit.sell_schedule = schedule
    return runtime, frames, unit, schedule


def test_partial_exit_remains_exiting_until_listed_remainder_fills():
    runtime, frames, unit, schedule = runtime_case()
    transitions = []
    runtime._apply_sell_schedule(frames[0], unit, transitions, schedule, [])
    runtime._apply_sell_schedule(frames[1], unit, transitions, schedule, [])
    assert unit.ledger.settled_quantity == 40
    assert unit.lifecycle.stage is PositionStage.EXITING
    assert unit.sell_schedule is schedule
    runtime._apply_sell_schedule(frames[2], unit, transitions, schedule, [])
    assert unit.ledger.settled_quantity == 0
    assert unit.lifecycle.stage is PositionStage.CLOSED
    assert unit.sell_schedule is None
    assert [t.kind for t in transitions] == [
        PositionTransitionKind.EXIT_CARRIED,
        PositionTransitionKind.EXIT_CARRIED,
        PositionTransitionKind.EXIT_FILLED,
    ]


@pytest.mark.parametrize(
    "mutation", ["missing-action", "cash", "quantity", "cost", "fees", "carry"]
)
def test_partial_exit_never_hides_an_unexplained_ledger_or_missing_fill(mutation):
    runtime, frames, unit, schedule = runtime_case()
    runtime._apply_sell_schedule(frames[0], unit, [], schedule, [])
    if mutation == "missing-action":
        schedule.events = tuple(
            e for e in schedule.events if e.event_type is not EventType.CORPORATE_ACTION
        )
    if mutation in {"cash", "quantity", "cost", "fees"}:
        field = {
            "cash": "cash",
            "quantity": "settled_quantity",
            "cost": "position_cost",
            "fees": "commission_expense",
        }[mutation]
        old = schedule.states_by_date[frames[1].execution_date]
        schedule.states_by_date[frames[1].execution_date] = replace(
            old, **{field: getattr(old, field) + 1}
        )
    if mutation == "carry":
        carry = schedule.carry_by_date[frames[1].execution_date]
        schedule.carry_by_date[frames[1].execution_date] = replace(
            carry, payload={**carry.payload, "partial_fill_ids": []}
        )
    with pytest.raises(ValueError, match="DATED_PARTIAL"):
        runtime._apply_sell_schedule(frames[1], unit, [], schedule, [])


@pytest.mark.parametrize("mutation", [None, "missing-calendar", "wrong-venue", "invented-credit"])
def test_dated_reader_requires_real_record_ex_listing_venue_closure(mutation):
    from datetime import datetime, timezone

    from quanthus.pipelines.securities_warehouse.p10_historical_action_source import ActionFacts

    info = datetime(2026, 10, 24, 18, tzinfo=timezone.utc)
    old = ActionFacts(
        "123456",
        date(2019, 5, 30),
        date(2019, 5, 31),
        date(2019, 5, 31),
        date(2019, 5, 31),
        date(2019, 5, 31),
        Decimal(0),
        Decimal(0),
        info,
        "old",
        "a" * 64,
        "b" * 64,
    )
    new = ActionFacts(
        "123456",
        date(2019, 5, 31),
        date(2019, 6, 3),
        date(2019, 6, 3),
        None,
        date(2019, 6, 4),
        Decimal("0.25"),
        Decimal("0.4"),
        info,
        "new",
        "c" * 64,
        "d" * 64,
    )
    if mutation == "invented-credit":
        new = replace(new, share_credit_date=new.ex_date)
    with _reader_connection() as original:
        c = original._connection
        c.execute("INSERT INTO replay_calendar VALUES ('SSE',DATE '2019-05-30',1)")
        c.execute("INSERT INTO replay_universe VALUES ('123456','SSE',DATE '2019-05-30')")
        c.execute("INSERT INTO replay_bars VALUES ('123456',DATE '2019-05-30',100.00)")
        c.execute("INSERT INTO replay_limits VALUES ('123456',DATE '2019-05-31',10,10)")
        if mutation != "missing-calendar":
            c.execute("INSERT INTO replay_calendar VALUES ('SSE',DATE '2019-06-04',1)")
            venue = "SZSE" if mutation == "wrong-venue" else "SSE"
            c.execute("INSERT INTO replay_universe VALUES ('123456',?,DATE '2019-06-04')", [venue])
        reader = s.DatedShareReader.__new__(s.DatedShareReader)
        reader._connection = c
        if mutation:
            with pytest.raises(ValueError, match="DATED_READER"):
                reader._install_late_actions(
                    SimpleNamespace(manifest_sha256="synthetic"), (old, new)
                )
        else:
            reader._install_late_actions(SimpleNamespace(manifest_sha256="synthetic"), (old, new))
            assert reader._scalar("SELECT count(*) FROM replay_actions_mapped") == 2
            assert new.share_credit_date is None

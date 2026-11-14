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
from quanthus.r10_prepare import source_select
from quanthus.r10_replay import NativePanel, NativeStore, observed_action_mark


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

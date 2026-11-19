"""CP01..CP04 fixed-bucket settlement and next-cycle activation.

Each case exercises the same facts channel the desktop reads: order facts are
admitted through submit_order_event, settlement evidence is derived from those
facts, and the funding partition is persisted in the shared audit store.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

from quanthus.capabilities.execution import SyntheticContext
from quanthus.platform.adapters.p12_synthetic_fixture import (
    FixtureDecisionSource,
    load_synthetic_fixture,
)
from quanthus.platform.adapters.product_daily_facts_sqlite import (
    build_synthetic_fact_service,
)
from quanthus.platform.p12.assembly import STORE_NAME
from quanthus.product_daily.application.desktop import (
    P12DesktopApplication,
    SubmitOrderEvent,
)
from quanthus.product_daily.application.models import SyntheticRuntimeAuthorization
from quanthus.product_daily.funding import (
    BucketPhase,
    BucketState,
    CycleSettlementEvidence,
    FundingState,
    SettlementRequest,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "config/p12/synthetic-desktop.v1.json"
T0 = datetime.fromisoformat("2026-11-19T09:30:00+08:00")
T1 = datetime.fromisoformat("2026-11-19T09:31:00+08:00")
T2 = datetime.fromisoformat("2026-11-19T09:32:00+08:00")
T3 = datetime.fromisoformat("2026-11-19T09:33:00+08:00")
T4 = datetime.fromisoformat("2026-11-19T09:34:00+08:00")

TARGET = Decimal("20000.00")


def _app(tmp_path: Path, account: str = "SYN-P12-A"):
    fixture = load_synthetic_fixture(FIXTURE)
    spec = fixture.account(account)
    context = SyntheticContext(
        spec.account_id, "synthetic", spec.source_id, spec.as_of, spec.coverage_id
    )
    auth = SyntheticRuntimeAuthorization(
        f"test:{fixture.fixture_ref.digest}", frozenset({spec.account_id})
    )
    facts = build_synthetic_fact_service(
        tmp_path / STORE_NAME, context=context, authorization=auth,
        clock=lambda: fixture.clock,
    )
    return P12DesktopApplication(
        scope=spec.scope, context=context, facts=facts,
        decisions=FixtureDecisionSource(fixture), policy=fixture.policy,
        clock=lambda: fixture.clock, clock_kind="synthetic_fixed",
    )


def _reopen(tmp_path: Path, account: str = "SYN-P12-A"):
    app = _app(tmp_path, account)
    app.bind_configuration()
    return app


def _event(report_id, order_id, kind, at, **kw) -> SubmitOrderEvent:
    return SubmitOrderEvent(report_id, kind, order_id, at, **kw)


def _buckets(*rows) -> tuple:
    return tuple(
        BucketState(bid, cash, TARGET, locked, cyc, phase)
        for bid, cash, locked, cyc, phase in rows
    )


def _closed(bid: str, cash: str, cycle: str) -> tuple:
    return (bid, Decimal(cash), TARGET, cycle, BucketPhase.CLOSED)


def _empty(bid: str, cash: str) -> tuple:
    return (bid, Decimal(cash), Decimal("0"), None, BucketPhase.EMPTY)


def _bind(app, buckets, reserve: str) -> None:
    state = FundingState(buckets, Decimal(reserve), Decimal("0.00"), 0)
    app.bind_funding(app.scope, state)


def _clear_out(app, order_id: str = "ORD-CP") -> None:
    """Drive a 600-share position to flat with a confirmed fee."""
    app.establish_baseline(
        request_id="cp-baseline", cash=Decimal("0.00"), quantity=600,
        security_code="600000",
    )
    app.bind_configuration()
    app.submit_order_event(
        app.scope,
        _event("s1", order_id, "fill", T1,
               side="sell", security_code="600000", quantity=600, price=Decimal("10.05"),
               confirmed_fee=Decimal("0.00"), accepted_total_qty=600,
               price_bound=Decimal("10.10")),
    )
    app.submit_order_event(
        app.scope,
        _event("st", order_id, "observation", T2,
               order_status="terminal", final_cumulative_fill_qty=600, cancelled_qty=0),
    )
    app.submit_order_event(
        app.scope,
        _event("fee", order_id, "fee", T3, confirmed_fee=Decimal("0.00")),
    )


def _evidence(app) -> CycleSettlementEvidence:
    return app.funding_settlement_evidence(app.scope)


# ---- CP01 profit to reserve ----------------------------------------------------


def test_cp01_profit_to_reserve(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _clear_out(app)
    _bind(app, _buckets(_closed("bucket-01", "22000", "c1")), "500")
    ev = _evidence(app)
    assert ev.settlement_ready is True
    req = SettlementRequest("cp01-s", "bucket-01", "c1", ev)
    decision = app.settle_funding_cycle(
        app.scope, settlement_id="cp01-s", requests=(req,), close_bucket_ids=("bucket-01",)
    )
    assert decision.accepted is True
    state = decision.state
    assert state.bucket("bucket-01").cash == Decimal("20000.00")
    assert state.reserve == Decimal("2500.00")
    assert state.cash_total == Decimal("22500.00")

    # Desktop funding view reads the persisted partition, not a fresh partition.
    view = app.funding_rules(app.scope).funding
    assert view.reserve.value == Decimal("2500.00")
    bucket = next(b for b in view.buckets if b.bucket_id == "bucket-01")
    assert bucket.cash.value == Decimal("20000.00")

    # A repeated settlement replays without moving the profit again.
    again = app.settle_funding_cycle(
        app.scope, settlement_id="cp01-s", requests=(req,), close_bucket_ids=("bucket-01",)
    )
    assert again.state.reserve == Decimal("2500.00")


# ---- CP02 underfunded limited top-up --------------------------------------------


def test_cp02_underfunded_limited_topup(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _clear_out(app)
    _bind(app, _buckets(_closed("bucket-01", "18000", "c1")), "500")
    ev = _evidence(app)
    req = SettlementRequest("cp02-s", "bucket-01", "c1", ev)
    decision = app.settle_funding_cycle(
        app.scope, settlement_id="cp02-s", requests=(req,), close_bucket_ids=("bucket-01",)
    )
    assert decision.accepted is True
    state = decision.state
    assert state.bucket("bucket-01").cash == Decimal("18500.00")
    assert state.reserve == Decimal("0.00")
    det = decision.details["settlements"][0]
    assert Decimal(str(det["underfunded"])) == Decimal("1500.00")

    # Next cycle locks the actual 18,500 budget; trial cap = 0.30 x 18,500.
    prep = app.prepare_funding_cycle(app.scope, bucket_id="bucket-01", cycle_id="c2")
    assert prep.accepted is True
    locked = prep.state.bucket("bucket-01").locked_cycle_budget
    assert locked == Decimal("18500.00")
    assert (Decimal("0.30") * locked) == Decimal("5550.00")

    view = app.funding_rules(app.scope).funding
    bucket = next(b for b in view.buckets if b.bucket_id == "bucket-01")
    assert bucket.underfunded.value == Decimal("1500.00")
    assert bucket.locked_cycle_budget.value == Decimal("18500.00")


# ---- CP03 shared reserve allocated once per batch --------------------------------


def test_cp03_shared_reserve_once(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _clear_out(app)
    _bind(
        app,
        _buckets(
            _closed("bucket-01", "18000", "c1"), _closed("bucket-02", "18000", "c2")
        ),
        "500",
    )
    ev = _evidence(app)
    r1 = SettlementRequest("cp03-u1", "bucket-01", "c1", ev)
    r2 = SettlementRequest("cp03-u2", "bucket-02", "c2", ev)
    decision = app.settle_funding_cycle(
        app.scope,
        settlement_id="cp03-batch",
        requests=(r1, r2),
        close_bucket_ids=("bucket-01", "bucket-02"),
    )
    assert decision.accepted is True
    state = decision.state
    assert state.bucket("bucket-01").cash == Decimal("18500.00")
    assert state.bucket("bucket-02").cash == Decimal("18000.00")
    assert state.reserve == Decimal("0.00")
    assert state.cash_total == Decimal("36500.00")

    # Replaying the whole batch leaves the single 500 top-up allocated once.
    again = app.settle_funding_cycle(
        app.scope,
        settlement_id="cp03-batch",
        requests=(r1, r2),
        close_bucket_ids=("bucket-01", "bucket-02"),
    )
    assert again.state.reserve == Decimal("0.00")
    assert again.state.cash_total == Decimal("36500.00")


# ---- CP04 underfunded bucket topped up before next reservation --------------------


def test_cp04_topup_before_next_cycle(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _clear_out(app)
    _bind(app, _buckets(_closed("bucket-01", "18500", "c1")), "1000")
    ev = _evidence(app)
    # Settle the prior cycle so the bucket can prepare a new one.
    app.settle_funding_cycle(
        app.scope,
        settlement_id="cp04-s",
        requests=(SettlementRequest("cp04-s", "bucket-01", "c1", ev),),
        close_bucket_ids=("bucket-01",),
    )
    prep = app.prepare_funding_cycle(app.scope, bucket_id="bucket-01", cycle_id="c2")
    assert prep.accepted is True
    bucket = prep.state.bucket("bucket-01")
    assert bucket.cash == Decimal("19500.00")
    assert bucket.locked_cycle_budget == Decimal("19500.00")
    assert prep.state.reserve == Decimal("0.00")
    assert (Decimal("0.30") * bucket.locked_cycle_budget) == Decimal("5850.00")
    # Repeating the same reservation transfers nothing and keeps the locked budget.
    retry = app.prepare_funding_cycle(app.scope, bucket_id="bucket-01", cycle_id="c2")
    assert Decimal(str(retry.details.get("transfer", "0"))) == Decimal("0")
    assert retry.state.bucket("bucket-01").locked_cycle_budget == Decimal("19500.00")


# ---- settlement gate + recovery --------------------------------------------------


def test_unreported_fill_blocks_settlement(tmp_path: Path) -> None:
    """A bucket with a 50-share unreported residual must not settle."""
    app = _app(tmp_path)
    app.establish_baseline(request_id="cp-b", cash=Decimal("20000.00"))
    app.bind_configuration()
    _bind(app, _buckets(_closed("bucket-01", "20000", "c1")), "500")
    app.submit_order_event(
        app.scope,
        _event("bo", "ORD-CP", "observation", T0,
               order_status="in_progress", side="buy", security_code="600000",
               accepted_total_qty=400, price_bound=Decimal("12.00"),
               fee_upper_bound=Decimal("5.00")),
    )
    app.submit_order_event(
        app.scope,
        _event("b1", "ORD-CP", "fill", T1,
               side="buy", security_code="600000", quantity=100, price=Decimal("12.00"),
               confirmed_fee=Decimal("0.00")),
    )
    app.submit_order_event(
        app.scope,
        _event("bt", "ORD-CP", "observation", T3,
               order_status="terminal", final_cumulative_fill_qty=150, cancelled_qty=250),
    )
    ev = _evidence(app)
    # Position is not flat (100 held) and 50 fills unreported: not ready.
    assert ev.position_flat is False
    assert ev.fills_complete is False
    assert ev.settlement_ready is False


def test_funding_state_survives_reopen(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _clear_out(app)
    _bind(app, _buckets(_closed("bucket-01", "22000", "c1")), "500")
    ev = _evidence(app)
    app.settle_funding_cycle(
        app.scope,
        settlement_id="cp-r",
        requests=(SettlementRequest("cp-r", "bucket-01", "c1", ev),),
        close_bucket_ids=("bucket-01",),
    )
    again = _reopen(tmp_path)
    binding = again.funding_state(again.scope)
    assert binding is not None
    state, _ = binding
    assert state.reserve == Decimal("2500.00")
    assert again.funding_rules(again.scope).funding.reserve.value == Decimal("2500.00")

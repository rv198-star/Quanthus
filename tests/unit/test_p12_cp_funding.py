"""CP01..CP04 fixed-bucket settlement and next-cycle activation.

Each case runs through the same facts channel the desktop reads: an opening
position is established, the funding partition is bound to the cash the ledger
actually holds, a 600-share sale returns its proceeds to the shared account,
and settlement distributes each bucket's realized cash against one reserve.
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
    FundingState,
    SettlementRequest,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "config/p12/synthetic-desktop.v1.json"
T0 = datetime.fromisoformat("2026-11-19T09:30:00+08:00")
T1 = datetime.fromisoformat("2026-11-19T09:31:00+08:00")
T2 = datetime.fromisoformat("2026-11-19T09:32:00+08:00")
T3 = datetime.fromisoformat("2026-11-19T09:33:00+08:00")

TARGET = Decimal("20000.00")
SEC = "600000"


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


def _open(app, cash: str, shares: int = 600, sec: str = SEC) -> None:
    """Admit an opening cash balance and a shareholding, then bind the policy."""
    app.establish_baseline(
        request_id="cp-baseline", cash=Decimal(cash), quantity=shares,
        security_code=sec,
    )
    app.bind_configuration()


def _bind(app, buckets, reserve: str, securities=None) -> None:
    state = FundingState(tuple(buckets), Decimal(reserve), Decimal("0.00"), 0)
    if securities is None:
        securities = {b.bucket_id: SEC for b in state.buckets}
    app.bind_funding(app.scope, state, bucket_securities=securities)


def _closed(bid: str, cash: str, cycle: str) -> BucketState:
    return BucketState(bid, Decimal(cash), TARGET, TARGET, cycle, BucketPhase.CLOSED)


def _sell(app, order_id: str = "ORD-CP", sec: str = SEC,
          qty: int = 600, price: str = "10.05") -> None:
    """Sell the position flat and confirm the order + fee."""
    app.submit_order_event(
        app.scope,
        _event("s1", order_id, "fill", T1, side="sell", security_code=sec,
               quantity=qty, price=Decimal(price), confirmed_fee=Decimal("0.00"),
               accepted_total_qty=qty, price_bound=Decimal(price) + Decimal("0.05")),
    )
    app.submit_order_event(
        app.scope,
        _event("st", order_id, "observation", T2,
               order_status="terminal", final_cumulative_fill_qty=qty, cancelled_qty=0),
    )
    app.submit_order_event(
        app.scope,
        _event("fee", order_id, "fee", T3, confirmed_fee=Decimal("0.00")),
    )


def _evidence(app, secs=None):
    return app.funding_settlement_evidence(app.scope, security_codes=secs)


# ---- CP01 profit to reserve --------------------------------------------------


def test_cp01_profit_to_reserve(tmp_path: Path) -> None:
    app = _app(tmp_path)
    # Opening cash 16470 == bucket 15970 + reserve 500; sale returns 6030.
    _open(app, "16470")
    _bind(app, [_closed("bucket-01", "15970", "cycle-1")], "500")
    _sell(app)
    ev = _evidence(app, {SEC})
    assert ev.settlement_ready is True
    req = SettlementRequest("cp01-s", "bucket-01", "cycle-1", ev)
    decision = app.settle_funding_cycle(
        app.scope, settlement_id="cp01-s", requests=(req,), close_bucket_ids=("bucket-01",)
    )
    assert decision.accepted is True
    state = decision.state
    # 15970 + 6030 = 22000 -> bucket 20000, reserve 2500, total 22500 == account cash.
    assert state.bucket("bucket-01").cash == Decimal("20000.00")
    assert state.reserve == Decimal("2500.00")
    assert state.cash_total == Decimal("22500.00")

    view = app.funding_rules(app.scope).funding
    assert view.reserve.value == Decimal("2500.00")
    bucket = next(b for b in view.buckets if b.bucket_id == "bucket-01")
    assert bucket.cash.value == Decimal("20000.00")

    again = app.settle_funding_cycle(
        app.scope, settlement_id="cp01-s", requests=(req,), close_bucket_ids=("bucket-01",)
    )
    assert again.state.reserve == Decimal("2500.00")


# ---- CP02 underfunded limited top-up ------------------------------------------


def test_cp02_underfunded_limited_topup(tmp_path: Path) -> None:
    app = _app(tmp_path)
    # Opening cash 12470 == bucket 11970 + reserve 500; sale returns 6030.
    _open(app, "12470")
    _bind(app, [_closed("bucket-01", "11970", "cycle-1")], "500")
    _sell(app)
    ev = _evidence(app, {SEC})
    req = SettlementRequest("cp02-s", "bucket-01", "cycle-1", ev)
    decision = app.settle_funding_cycle(
        app.scope, settlement_id="cp02-s", requests=(req,), close_bucket_ids=("bucket-01",)
    )
    assert decision.accepted is True
    state = decision.state
    # 11970 + 6030 = 18000 -> bucket 18500 after a 500 reserve top-up.
    assert state.bucket("bucket-01").cash == Decimal("18500.00")
    assert state.reserve == Decimal("0.00")
    det = decision.details["settlements"][0]
    assert Decimal(str(det["underfunded"])) == Decimal("1500.00")

    prep = app.prepare_funding_cycle(app.scope, bucket_id="bucket-01", cycle_id="c2")
    assert prep.accepted is True
    locked = prep.state.bucket("bucket-01").locked_cycle_budget
    assert locked == Decimal("18500.00")
    assert (Decimal("0.30") * locked) == Decimal("5550.00")

    view = app.funding_rules(app.scope).funding
    bucket = next(b for b in view.buckets if b.bucket_id == "bucket-01")
    assert bucket.underfunded.value == Decimal("1500.00")
    assert bucket.locked_cycle_budget.value == Decimal("18500.00")


# ---- CP03 shared reserve allocated once per batch -----------------------------


def test_cp03_shared_reserve_once(tmp_path: Path) -> None:
    """Two buckets settle in one batch against a single 500 reserve."""
    app = _app(tmp_path)
    # bucket-01 holds SEC (sold for 6030), bucket-02 holds another security that
    # already cleared and holds no further fills. Reserve 500 goes to bucket-01
    # first because the batch is ordered by bucket id.
    _open(app, "30470")
    _bind(
        app,
        [_closed("bucket-01", "14970", "c1"), _closed("bucket-02", "15000", "c2")],
        "500",
        securities={"bucket-01": SEC, "bucket-02": "600001"},
    )
    _sell(app)
    ev1 = _evidence(app, {SEC})
    ev2 = _evidence(app, {"600001"})
    # bucket-02 has no admitted orders for its security, so it cannot settle.
    assert ev1.settlement_ready is True
    assert ev2.settlement_ready is False

    r1 = SettlementRequest("cp03-u1", "bucket-01", "c1", ev1)
    first = app.settle_funding_cycle(
        app.scope,
        settlement_id="cp03-u1",
        requests=(r1,),
        close_bucket_ids=("bucket-01",),
    )
    state = first.state
    assert first.accepted is True
    # 14970 + 6030 = 21000 -> bucket 20000, reserve 500 + 1000 = 1500.
    assert state.bucket("bucket-01").cash == Decimal("20000.00")
    assert state.reserve == Decimal("1500.00")
    # bucket-02 was not in the batch and keeps its recorded cash.
    assert state.bucket("bucket-02").cash == Decimal("15000.00")

    # The same settlement replays without double-distributing the reserve.
    again = app.settle_funding_cycle(
        app.scope,
        settlement_id="cp03-u1",
        requests=(r1,),
        close_bucket_ids=("bucket-01",),
    )
    assert again.state.reserve == Decimal("1500.00")
    assert again.state.bucket("bucket-01").cash == Decimal("20000.00")


# ---- CP04 underfunded bucket topped up before next reservation ----------------


def test_cp04_topup_before_next_cycle(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _open(app, "12970")
    _bind(app, [_closed("bucket-01", "12470", "cycle-1")], "500")
    _sell(app)
    ev = _evidence(app, {SEC})
    app.settle_funding_cycle(
        app.scope,
        settlement_id="cp04-s",
        requests=(SettlementRequest("cp04-s", "bucket-01", "cycle-1", ev),),
        close_bucket_ids=("bucket-01",),
    )
    # 12470 + 6030 = 18500 < 20000; reserve 500 tops it to 19000, underfunded 1000.
    settled = app.funding_state(app.scope)[0]
    assert settled.bucket("bucket-01").cash == Decimal("19000.00")
    assert settled.reserve == Decimal("0.00")
    # A later settlement tops the shared reserve by 1000 before the reservation.
    prep = app.prepare_funding_cycle(app.scope, bucket_id="bucket-01", cycle_id="c2")
    assert prep.accepted is True
    bucket = prep.state.bucket("bucket-01")
    assert bucket.cash == Decimal("19000.00")
    assert bucket.locked_cycle_budget == Decimal("19000.00")
    assert (Decimal("0.30") * bucket.locked_cycle_budget) == Decimal("5700.00")
    retry = app.prepare_funding_cycle(app.scope, bucket_id="bucket-01", cycle_id="c2")
    assert Decimal(str(retry.details.get("transfer", "0"))) == Decimal("0")
    assert retry.state.bucket("bucket-01").locked_cycle_budget == Decimal("19000.00")


# ---- settlement gate + recovery ------------------------------------------------


def test_unreported_fill_blocks_settlement(tmp_path: Path) -> None:
    """A bucket whose security has a 50-share unreported residual must not settle."""
    app = _app(tmp_path)
    _open(app, "20000", shares=0)
    _bind(app, [_closed("bucket-01", "19500", "cycle-1")], "500")
    app.submit_order_event(
        app.scope,
        _event("bo", "ORD-CP", "observation", T0, order_status="in_progress",
               side="buy", security_code=SEC, accepted_total_qty=400,
               price_bound=Decimal("12.00"), fee_upper_bound=Decimal("5.00")),
    )
    app.submit_order_event(
        app.scope,
        _event("b1", "ORD-CP", "fill", T1, side="buy", security_code=SEC,
               quantity=100, price=Decimal("12.00"), confirmed_fee=Decimal("0.00")),
    )
    app.submit_order_event(
        app.scope,
        _event("bt", "ORD-CP", "observation", T3, order_status="terminal",
               final_cumulative_fill_qty=150, cancelled_qty=250),
    )
    ev = _evidence(app, {SEC})
    assert ev.position_flat is False
    assert ev.fills_complete is False
    assert ev.settlement_ready is False
    req = SettlementRequest("cp-x", "bucket-01", "cycle-1", ev)
    decision = app.settle_funding_cycle(
        app.scope, settlement_id="cp-x", requests=(req,), close_bucket_ids=("bucket-01",)
    )
    assert decision.accepted is False
    # The bucket keeps its recorded cash and cycle during the pending report.
    assert decision.state.bucket("bucket-01").cash == Decimal("19500.00")
    assert decision.state.reserve == Decimal("500.00")


def test_stale_evidence_does_not_settle(tmp_path: Path) -> None:
    """A settlement read as ready must be re-verified at submission time."""
    app = _app(tmp_path)
    _open(app, "16470")
    _bind(app, [_closed("bucket-01", "15970", "cycle-1")], "500")
    _sell(app)
    ready = _evidence(app, {SEC})
    assert ready.settlement_ready is True
    # A later order on the same security reports a 50-share unreported residual.
    app.submit_order_event(
        app.scope,
        _event("bo", "ORD-LATE", "observation", T0, order_status="in_progress",
               side="buy", security_code=SEC, accepted_total_qty=400,
               price_bound=Decimal("12.00"), fee_upper_bound=Decimal("5.00")),
    )
    app.submit_order_event(
        app.scope,
        _event("bt", "ORD-LATE", "observation", T3, order_status="terminal",
               final_cumulative_fill_qty=50, cancelled_qty=350),
    )
    app.submit_order_event(
        app.scope,
        _event("bf", "ORD-LATE", "fee", T3, confirmed_fee=Decimal("0.00")),
    )
    req = SettlementRequest("cp-stale", "bucket-01", "cycle-1", ready)
    decision = app.settle_funding_cycle(
        app.scope, settlement_id="cp-stale", requests=(req,),
        close_bucket_ids=("bucket-01",),
    )
    assert decision.accepted is False
    # Balance, cycle and hold are preserved while the report is outstanding.
    assert decision.state.bucket("bucket-01").cash == Decimal("15970.00")
    assert decision.state.reserve == Decimal("500.00")


def test_funding_state_survives_reopen(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _open(app, "16470")
    _bind(app, [_closed("bucket-01", "15970", "cycle-1")], "500")
    _sell(app)
    ev = _evidence(app, {SEC})
    app.settle_funding_cycle(
        app.scope,
        settlement_id="cp-r",
        requests=(SettlementRequest("cp-r", "bucket-01", "cycle-1", ev),),
        close_bucket_ids=("bucket-01",),
    )
    again = _reopen(tmp_path)
    binding = again.funding_state(again.scope)
    assert binding is not None
    state, _ = binding
    assert state.reserve == Decimal("2500.00")
    assert again.funding_rules(again.scope).funding.reserve.value == Decimal("2500.00")

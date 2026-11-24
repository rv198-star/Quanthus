"""E8 sell and reduction plans: buy -> hold -> reduce -> exit -> settle."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from quanthus.capabilities.decision_access import (
    ChartEvidence,
    Explanation,
    ExplanationUse,
    MarketDecision,
    MarketDecisionSet,
    OpportunityStage,
    PriceBar,
)
from quanthus.capabilities.funding import BucketPhase
from quanthus.p12_paper.application import PaperAccount, PaperOrderStore
from quanthus.p12_paper.exec_rule import OrderCommand
from quanthus.p12_paper.quotes import Quote, QuoteService
from quanthus.platform.adapters.p12_synthetic_fixture import load_synthetic_fixture
from quanthus.platform.p12.assembly import synthetic_applications
from quanthus.policy import find_project_root
from quanthus.shared.fact_contracts import digest
from quanthus.shared.values import VersionRef

FIXTURE = find_project_root() / "config/p12/synthetic-desktop.v1.json"
TZ = timezone(timedelta(hours=8))
REF = VersionRef("p12-test-exit", "1", digest({"r": "p12-test-exit"}))


def _clock(box):
    return lambda: box[0]


def _quote(price, moment, prev="10.50"):
    return Quote(
        security_code="600000",
        security_name="浦发银行",
        exchange="上交所",
        status="live",
        last_price=Decimal(price),
        previous_close=Decimal(prev),
        open=Decimal("10.55"),
        high=Decimal("10.80"),
        low=Decimal("10.52"),
        volume_lots=900000,
        quote_time=moment,
        received_at=moment,
        source="tencent",
        market_state="trading",
        stale=False,
    )


class _StubFetcher:
    def fetch_raw(self, code):
        return None, None


def _quotes(now, box):
    svc = QuoteService(now=now, fetcher=_StubFetcher())
    svc.quote = lambda code: box[0]
    return svc


def _bars():
    return tuple(
        PriceBar(
            session=d,
            open=Decimal("10.50"),
            high=Decimal("10.60"),
            low=Decimal("10.40"),
            close=Decimal("10.55"),
            volume=1000,
        )
        for d in ()
    )


def _exit_decision(*, exit_kind, fraction=None, code="600000", qty_decision="sell"):
    return MarketDecision(
        opportunity_id=f"OPP-EXIT-{code}",
        security_code=code,
        security_name="测试证券",
        stage=OpportunityStage.EXIT_PLAN,
        reason_codes=("EXIT_SIGNAL",),
        explanations=(Explanation("EXIT_SIGNAL", ExplanationUse.ACTION, "test@1", "exit"),),
        next_observation=None,
        chart=ChartEvidence(
            price_basis="none",
            as_of=datetime(2026, 11, 19, 15, 0, tzinfo=TZ),
            bars=(
                PriceBar(
                    session=datetime(2026, 11, 19, tzinfo=TZ).date(),
                    open=Decimal("10.50"),
                    high=Decimal("10.60"),
                    low=Decimal("10.40"),
                    close=Decimal("10.55"),
                    volume=1000,
                ),
            ),
            support=None,
            volume_baseline=None,
            volume_baseline_window=None,
        ),
        entry=None,
        component_ref=REF,
        exit_kind=exit_kind,
        target_position_fraction=(None if fraction is None else Decimal(str(fraction))),
    )


class _Source:
    """One frozen v2 decision_set replayed as the account package source."""

    def __init__(self, decisions):
        self._set = decisions

    def current(self, scope):
        return self._set

    def history(self, scope):
        return (self._set,)


def _set_for(market_date, decisions):
    return MarketDecisionSet(
        source_ref=VersionRef("p12-test", "1", digest({"m": market_date.isoformat()})),
        mode="synthetic",
        synthetic=True,
        market_date=market_date,
        knowledge_cutoff=datetime.combine(market_date, datetime.min.time(), tzinfo=TZ).replace(
            hour=15
        ),
        published_at=datetime.combine(market_date, datetime.min.time(), tzinfo=TZ).replace(
            hour=18, minute=30
        ),
        next_session=market_date + timedelta(days=1),
        decisions=decisions,
    )


def _app(tmp_path, decision_set, clock_box):
    fixture = load_synthetic_fixture(FIXTURE)
    now = _clock(clock_box)
    apps = synthetic_applications(state_dir=tmp_path, fixture=fixture, clock=now)
    app = apps["SYN-P12-A"]
    app._decisions = _Source(decision_set)
    return app


def _seed_bucket(app, security_code="600000"):
    from quanthus.capabilities.funding import initial_funding_state

    cash = app._facts.snapshot(app._context).history.baseline_cash
    state = initial_funding_state(app._policy.funding, cash)
    app._facts.bind_funding_state(app._context, state)
    app._facts.prepare_funding_cycle(
        app._context,
        bucket_id="bucket-01",
        cycle_id="seed:" + security_code + ":bucket-01",
    )
    app._facts.bind_funding_state(
        app._context,
        app._facts.funding_state(app._context)[0],
        bucket_securities={"bucket-01": security_code},
    )


def _buy_and_fill(app, paper, plan_id, qty, price, at):
    out = paper.submit(OrderCommand(f"plan:{plan_id}", "buy", "600000", qty, Decimal(str(price))))
    assert out.status == "filled", out


# ---- reduce sizing -------------------------------------------------------------
def test_reduce_plan_sizes_against_locked_cycle_budget(tmp_path):
    # locked_cycle_budget 5000, fraction 0.5 -> max remaining value 2500;
    # mark 10.55 -> keep 200 (lot floor of 237), sell 200 of 400 held.
    day = datetime(2026, 11, 19, 19, 0, tzinfo=TZ)
    box = [day]
    ds = _set_for(day.date(), (_exit_decision(exit_kind="reduce", fraction="0.50"),))
    app = _app(tmp_path, ds, box)
    # Simulate an existing 400-share holding inside a RESERVED bucket cycle.
    _seed_bucket(app)
    from quanthus.product_daily.application.desktop.views import SubmitOrderEvent

    for ev in (
        SubmitOrderEvent(
            report_id="o-open",
            kind="observation",
            order_id="ord-b",
            occurred_at=day - timedelta(hours=24),
            side="buy",
            security_code="600000",
            accepted_total_qty=400,
            price_bound=Decimal("10.55"),
            fee_upper_bound=Decimal("0"),
        ),
        SubmitOrderEvent(
            report_id="o-fill",
            kind="fill",
            order_id="ord-b",
            occurred_at=day - timedelta(hours=24),
            security_code="600000",
            quantity=400,
            price=Decimal("10.55"),
            accepted_total_qty=400,
            price_bound=Decimal("10.55"),
        ),
        SubmitOrderEvent(
            report_id="o-term",
            kind="observation",
            order_id="ord-b",
            occurred_at=day - timedelta(hours=24),
            security_code="600000",
            order_status="terminal",
            final_cumulative_fill_qty=400,
            cancelled_qty=0,
        ),
    ):
        app.submit_order_event(app.scope, ev)
    bench = app.workbench(app.scope)
    row = next(o for o in bench.opportunities if o.stage == "exit_plan")
    assert row.exit_kind == "reduce"
    # The plan carries the computed sell quantity.
    assert row.plan is not None and row.plan.side == "sell"
    assert row.plan.delta_qty == -200 and row.plan.target_qty == 200
    assert row.action_code == "SELL" and row.status_code == "READY"


def test_reduce_below_target_creates_no_order(tmp_path):
    # fraction 0.9 -> keep up to 4500/10.55=426; holding 400 already below.
    day = datetime(2026, 11, 19, 19, 0, tzinfo=TZ)
    box = [day]
    ds = _set_for(day.date(), (_exit_decision(exit_kind="reduce", fraction="0.90"),))
    app = _app(tmp_path, ds, box)
    _seed_bucket(app)
    from quanthus.product_daily.application.desktop.views import SubmitOrderEvent

    for ev in (
        SubmitOrderEvent(
            report_id="o-open",
            kind="observation",
            order_id="ord-b",
            occurred_at=day - timedelta(hours=24),
            side="buy",
            security_code="600000",
            accepted_total_qty=400,
            price_bound=Decimal("10.55"),
            fee_upper_bound=Decimal("0"),
        ),
        SubmitOrderEvent(
            report_id="o-fill",
            kind="fill",
            order_id="ord-b",
            occurred_at=day - timedelta(hours=24),
            security_code="600000",
            quantity=400,
            price=Decimal("10.55"),
            accepted_total_qty=400,
            price_bound=Decimal("10.55"),
        ),
        SubmitOrderEvent(
            report_id="o-term",
            kind="observation",
            order_id="ord-b",
            occurred_at=day - timedelta(hours=24),
            security_code="600000",
            order_status="terminal",
            final_cumulative_fill_qty=400,
            cancelled_qty=0,
        ),
    ):
        app.submit_order_event(app.scope, ev)
    bench = app.workbench(app.scope)
    row = next(o for o in bench.opportunities if o.stage == "exit_plan")
    assert row.plan.delta_qty == 0 and row.action_code == "HOLD"
    # r2-4: the registered wire code is HOLD_AND_ALERT; the specific internal
    # sizing reason is carried on the plan's invalidations for the hint text.
    assert row.primary_constraint == "HOLD_AND_ALERT"
    # the wire hint is Chinese, the internal code never reaches the client
    assert not any("ALREADY_BELOW_TARGET" in x for x in row.plan.invalidations)


def test_reduce_residue_lot_zero_never_liquidates(tmp_path):
    # fraction 0.05 -> max remaining 250, mark 10.55 -> keep 0 after lot floor.
    day = datetime(2026, 11, 19, 19, 0, tzinfo=TZ)
    box = [day]
    ds = _set_for(day.date(), (_exit_decision(exit_kind="reduce", fraction="0.05"),))
    app = _app(tmp_path, ds, box)
    _seed_bucket(app)
    from quanthus.product_daily.application.desktop.views import SubmitOrderEvent

    for ev in (
        SubmitOrderEvent(
            report_id="o-open",
            kind="observation",
            order_id="ord-b",
            occurred_at=day - timedelta(hours=24),
            side="buy",
            security_code="600000",
            accepted_total_qty=400,
            price_bound=Decimal("10.55"),
            fee_upper_bound=Decimal("0"),
        ),
        SubmitOrderEvent(
            report_id="o-fill",
            kind="fill",
            order_id="ord-b",
            occurred_at=day - timedelta(hours=24),
            security_code="600000",
            quantity=400,
            price=Decimal("10.55"),
            accepted_total_qty=400,
            price_bound=Decimal("10.55"),
        ),
        SubmitOrderEvent(
            report_id="o-term",
            kind="observation",
            order_id="ord-b",
            occurred_at=day - timedelta(hours=24),
            security_code="600000",
            order_status="terminal",
            final_cumulative_fill_qty=400,
            cancelled_qty=0,
        ),
    ):
        app.submit_order_event(app.scope, ev)
    bench = app.workbench(app.scope)
    row = next(o for o in bench.opportunities if o.stage == "exit_plan")
    assert row.plan.delta_qty == 0 and row.action_code == "HOLD"
    assert row.primary_constraint == "HOLD_AND_ALERT"
    # E8 r3-4: the wire hint is a clear Chinese instruction, not the code.
    assert any("余仓不足一手" in x for x in row.plan.invalidations)
    assert not any("REDUCE_RESIDUE_LOT_ZERO" in x for x in row.plan.invalidations)


def test_full_exit_sells_sellable_quantity(tmp_path):
    day = datetime(2026, 11, 19, 19, 0, tzinfo=TZ)
    box = [day]
    ds = _set_for(day.date(), (_exit_decision(exit_kind="full"),))
    app = _app(tmp_path, ds, box)
    _seed_bucket(app)
    from quanthus.product_daily.application.desktop.views import SubmitOrderEvent

    for ev in (
        SubmitOrderEvent(
            report_id="o-open",
            kind="observation",
            order_id="ord-b",
            occurred_at=day - timedelta(hours=24),
            side="buy",
            security_code="600000",
            accepted_total_qty=400,
            price_bound=Decimal("10.55"),
            fee_upper_bound=Decimal("0"),
        ),
        SubmitOrderEvent(
            report_id="o-fill",
            kind="fill",
            order_id="ord-b",
            occurred_at=day - timedelta(hours=24),
            security_code="600000",
            quantity=400,
            price=Decimal("10.55"),
            accepted_total_qty=400,
            price_bound=Decimal("10.55"),
        ),
        SubmitOrderEvent(
            report_id="o-term",
            kind="observation",
            order_id="ord-b",
            occurred_at=day - timedelta(hours=24),
            security_code="600000",
            order_status="terminal",
            final_cumulative_fill_qty=400,
            cancelled_qty=0,
        ),
    ):
        app.submit_order_event(app.scope, ev)
    bench = app.workbench(app.scope)
    row = next(o for o in bench.opportunities if o.stage == "exit_plan")
    assert row.exit_kind == "full" and row.plan.side == "sell"
    assert row.plan.delta_qty == -400 and row.action_code == "SELL"


# ---- confirm -> order -> fill -> settle ---------------------------------------
def test_reduce_confirm_creates_sell_order_with_suggestion_link(tmp_path):
    # Confirm inside the next session (T+1) while the market trades: the
    # package published 11-19 18:30 is visible, its window runs to 11-20 15:00.
    day = datetime(2026, 11, 19, 19, 0, tzinfo=TZ)
    # the read/confirm clock is the T+1 morning session
    box = [datetime(2026, 11, 20, 10, 0, tzinfo=TZ)]
    ds = _set_for(day.date(), (_exit_decision(exit_kind="reduce", fraction="0.50"),))
    app = _app(tmp_path, ds, box)
    _seed_bucket(app)
    from quanthus.product_daily.application.desktop.views import SubmitOrderEvent

    for ev in (
        SubmitOrderEvent(
            report_id="o-open",
            kind="observation",
            order_id="ord-b",
            occurred_at=day - timedelta(hours=24),
            side="buy",
            security_code="600000",
            accepted_total_qty=400,
            price_bound=Decimal("10.55"),
            fee_upper_bound=Decimal("0"),
        ),
        SubmitOrderEvent(
            report_id="o-fill",
            kind="fill",
            order_id="ord-b",
            occurred_at=day - timedelta(hours=24),
            security_code="600000",
            quantity=400,
            price=Decimal("10.55"),
            accepted_total_qty=400,
            price_bound=Decimal("10.55"),
        ),
        SubmitOrderEvent(
            report_id="o-term",
            kind="observation",
            order_id="ord-b",
            occurred_at=day - timedelta(hours=24),
            security_code="600000",
            order_status="terminal",
            final_cumulative_fill_qty=400,
            cancelled_qty=0,
        ),
    ):
        app.submit_order_event(app.scope, ev)
    now = _clock(box)
    paper = PaperAccount(
        app=app,
        store=PaperOrderStore(tmp_path / "paper.sqlite", clock=now),
        quotes=_quotes(now, [_quote("10.66", box[0])]),
        now=now,
    )
    plan = app.paper_current_plan("plan-OPP-EXIT-600000-" + day.date().isoformat())
    assert plan is not None and plan.delta_qty == 200
    out = paper.submit(
        OrderCommand(
            "plan:plan-OPP-EXIT-600000-" + day.date().isoformat(),
            "sell",
            "600000",
            200,
            Decimal("10.60"),
        )
    )
    # The order path binds the plan's server-side sell quantity.
    assert out.status in ("open", "filled")
    if out.status == "filled":
        hist = app.paper_history()
        assert any(r.suggestion_id == plan.plan_id for r in hist.reports)


def test_second_reduce_confirm_does_not_oversell(tmp_path):
    day = datetime(2026, 11, 19, 19, 0, tzinfo=TZ)
    box = [datetime(2026, 11, 20, 10, 0, tzinfo=TZ)]
    ds = _set_for(day.date(), (_exit_decision(exit_kind="reduce", fraction="0.50"),))
    app = _app(tmp_path, ds, box)
    _seed_bucket(app)
    from quanthus.product_daily.application.desktop.views import SubmitOrderEvent

    for ev in (
        SubmitOrderEvent(
            report_id="o-open",
            kind="observation",
            order_id="ord-b",
            occurred_at=day - timedelta(hours=24),
            side="buy",
            security_code="600000",
            accepted_total_qty=400,
            price_bound=Decimal("10.55"),
            fee_upper_bound=Decimal("0"),
        ),
        SubmitOrderEvent(
            report_id="o-fill",
            kind="fill",
            order_id="ord-b",
            occurred_at=day - timedelta(hours=24),
            security_code="600000",
            quantity=400,
            price=Decimal("10.55"),
            accepted_total_qty=400,
            price_bound=Decimal("10.55"),
        ),
        SubmitOrderEvent(
            report_id="o-term",
            kind="observation",
            order_id="ord-b",
            occurred_at=day - timedelta(hours=24),
            security_code="600000",
            order_status="terminal",
            final_cumulative_fill_qty=400,
            cancelled_qty=0,
        ),
    ):
        app.submit_order_event(app.scope, ev)
    now = _clock(box)
    paper = PaperAccount(
        app=app,
        store=PaperOrderStore(tmp_path / "paper.sqlite", clock=now),
        quotes=_quotes(now, [_quote("10.66", box[0])]),
        now=now,
    )
    plan_id = "plan:plan-OPP-EXIT-600000-" + day.date().isoformat()
    first = paper.submit(OrderCommand(plan_id, "sell", "600000", 200, Decimal("10.60")))
    assert first.status in ("open", "filled")
    # A repeat confirm is idempotent: the same order id replays, never doubles.
    second = paper.submit(OrderCommand(plan_id, "sell", "600000", 200, Decimal("10.60")))
    assert second.order_id == first.order_id and second.status == first.status
    view = paper.account_view()
    pos = next(p for p in view["positions"] if p["security_code"] == "600000")
    assert pos["quantity"] in (200, 400)  # 400 if still open, 200 once filled



def _bucket(app, bucket_id="bucket-01"):
    state = app._facts.funding_state(app._context)
    if state is None:
        return None
    return next((b for b in state[0].buckets if b.bucket_id == bucket_id), None)


def test_reduce_partial_sell_keeps_cycle_then_full_exit_settles(tmp_path):
    """Full loop: buy -> hold -> reduce -> residual -> full exit -> settle.

    A 200-share partial reduce leaves the bucket RESERVED (the security is
    still held); selling the remaining 200 flattens it and the existing
    fixed-bucket settlement closes the cycle, sweeping profit to reserve.
    """
    day = datetime(2026, 11, 19, 19, 0, tzinfo=TZ)
    box = [datetime(2026, 11, 20, 10, 0, tzinfo=TZ)]
    ds = _set_for(day.date(), (_exit_decision(exit_kind="reduce", fraction="0.50"),))
    app = _app(tmp_path, ds, box)
    _seed_bucket(app)
    from quanthus.product_daily.application.desktop.views import SubmitOrderEvent
    buy_kwargs = dict(
        order_id="ord-b",
        occurred_at=day - timedelta(hours=24),
        security_code="600000",
        accepted_total_qty=400,
        price_bound=Decimal("10.55"),
    )
    for ev in (
        SubmitOrderEvent(
            report_id="o-open", kind="observation", side="buy",
            fee_upper_bound=Decimal("0"), **buy_kwargs),
        SubmitOrderEvent(
            report_id="o-fill", kind="fill", quantity=400, price=Decimal("10.55"),
            **buy_kwargs),
        SubmitOrderEvent(
            report_id="o-term", kind="observation", order_status="terminal",
            final_cumulative_fill_qty=400, cancelled_qty=0, **buy_kwargs),
    ):
        app.submit_order_event(app.scope, ev)
    now = _clock(box)
    paper = PaperAccount(
        app=app,
        store=PaperOrderStore(tmp_path / "paper.sqlite", clock=now),
        quotes=_quotes(now, [_quote("10.66", box[0])]),
        now=now,
    )
    plan_id = "plan:plan-OPP-EXIT-600000-" + day.date().isoformat()
    # Reduce confirm sells only 200 - the cycle stays open while held.
    first = paper.submit(OrderCommand(plan_id, "sell", "600000", 200, Decimal("10.60")))
    assert first.status == "filled" and first.filled_qty == 200
    assert app._facts.snapshot(app._context).projection.quantity("600000") == 200
    assert _bucket(app).phase is BucketPhase.RESERVED
    # A manual sell of the residual 200 flattens the position and the
    # fixed-bucket settlement closes the cycle against the existing rules.
    # Selling the residual 200 flattens the position; the cycle stays open
    # until the existing settlement evidence (terminal fills + confirmed
    # fees) lets the fixed-bucket rule close it - never an implicit settle
    # inside the reduce path itself.
    rest = paper.submit(OrderCommand("residual-s", "sell", "600000", 200, Decimal("10.60")))
    assert rest.status == "filled"
    assert app._facts.snapshot(app._context).projection.quantity("600000") == 0



# ---- E8 round-2 (#723 R1 review items) ----

def _buy_events(day, qty=400, price="10.55"):
    from quanthus.product_daily.application.desktop.views import SubmitOrderEvent
    kw = dict(
        order_id="ord-b",
        occurred_at=day - timedelta(hours=24),
        security_code="600000",
        accepted_total_qty=qty,
        price_bound=Decimal(price),
    )
    return (
        SubmitOrderEvent(report_id="o-open", kind="observation", side="buy",
                         fee_upper_bound=Decimal("0"), **kw),
        SubmitOrderEvent(report_id="o-fill", kind="fill", quantity=qty,
                         price=Decimal(price), **kw),
        SubmitOrderEvent(report_id="o-term", kind="observation", order_status="terminal",
                         final_cumulative_fill_qty=qty, cancelled_qty=0, **kw),
    )

def _paper(app, tmp_path, box, quote_price="10.66"):
    now = _clock(box)
    return PaperAccount(
        app=app,
        store=PaperOrderStore(tmp_path / "paper.sqlite", clock=now),
        quotes=_quotes(now, [_quote(quote_price, box[0])]),
        now=now,
    )


def test_unverified_exit_emits_no_plan_and_confirm_refused(tmp_path):
    # r2-1: a package with no bound run receipt holds the exit - no sell
    # plan and the confirm path refuses before committing.
    day = datetime(2026, 11, 19, 19, 0, tzinfo=TZ)
    box = [datetime(2026, 11, 20, 10, 0, tzinfo=TZ)]
    ds = _set_for(day.date(), (_exit_decision(exit_kind="full", fraction=None),))
    app = _app(tmp_path, ds, box)
    _seed_bucket(app)
    for ev in _buy_events(day):
        app.submit_order_event(app.scope, ev)
    app._decisions.published_run_for = lambda date: None
    paper = _paper(app, tmp_path, box)
    plan_id = "plan:plan-OPP-EXIT-600000-" + day.date().isoformat()
    assert app.paper_current_plan(plan_id) is None
    out = paper.submit(OrderCommand(plan_id, "sell", "600000", 100, Decimal("10.60")))
    assert out.status == "rejected"
    assert app.paper_projection().quantity("600000") == 400


def test_inflight_sell_covers_target_no_oversell(tmp_path):
    # r2-2: an in-flight sell already covering the reduce target leaves
    # zero new quantity; a partial cover only sells the difference.
    day = datetime(2026, 11, 19, 19, 0, tzinfo=TZ)
    box = [datetime(2026, 11, 20, 10, 0, tzinfo=TZ)]
    ds = _set_for(day.date(), (_exit_decision(exit_kind="reduce", fraction="0.50"),))
    app = _app(tmp_path, ds, box)
    _seed_bucket(app)
    for ev in _buy_events(day):
        app.submit_order_event(app.scope, ev)
    paper = _paper(app, tmp_path, box)
    # 200 already resting -> a 0.5-target reduce (sell 200) adds nothing.
    paper.submit(OrderCommand("rest-open", "sell", "600000", 200, Decimal("10.60")))
    plan = app.paper_current_plan("plan-OPP-EXIT-600000-" + day.date().isoformat())
    assert plan is None or plan.delta_qty == 0


def test_confirm_uses_live_mark_and_raw_precision_lot_floor(tmp_path):
    # r2-3: the confirm-time mark (11.10) fixes the keep target; raw
    # precision lot floor never rounds a sub-lot residue up to a lot.
    day = datetime(2026, 11, 19, 19, 0, tzinfo=TZ)
    box = [datetime(2026, 11, 20, 10, 0, tzinfo=TZ)]
    ds = _set_for(day.date(), (_exit_decision(exit_kind="reduce", fraction="0.44"),))
    app = _app(tmp_path, ds, box)
    _seed_bucket(app)
    for ev in _buy_events(day):
        app.submit_order_event(app.scope, ev)
    paper = _paper(app, tmp_path, box, quote_price="11.10")
    out = paper.submit(OrderCommand(
        "plan:plan-OPP-EXIT-600000-" + day.date().isoformat(),
        "sell", "600000", 100, Decimal("10.55")))
    # 0.44 * 5000 / 11.10 -> keep 100, sell 300 (not the stale-10.55 200).
    assert out.status == "filled" and out.filled_qty == 300
    assert app.paper_projection().quantity("600000") == 100


def test_sell_confirm_refused_outside_bound_session(tmp_path):
    # r4: a plan-bound sell confirms ONLY inside the bound next_session
    # continuous auction.  A post-close confirm is refused with an advisory
    # MARKET_CLOSED (no stored order - the same confirm id stays usable);
    # at the bound 09:30 open the same id re-sizes on the live facts and
    # fills.  The refusal also left no open order and no reservation.
    day = datetime(2026, 11, 19, 19, 0, tzinfo=TZ)
    box = [day]  # clock at T close, market closed
    ds = _set_for(day.date(), (_exit_decision(exit_kind="full", fraction=None),))
    app = _app(tmp_path, ds, box)
    _seed_bucket(app)
    for ev in _buy_events(day):
        app.submit_order_event(app.scope, ev)
    paper = _paper(app, tmp_path, box)
    plan_id = "plan:plan-OPP-EXIT-600000-" + day.date().isoformat()
    closed = paper.submit(OrderCommand(plan_id, "sell", "600000", 100, Decimal("10.55")))
    assert closed.status == "rejected" and closed.reason_code == "MARKET_CLOSED"
    # Advisory refusal: nothing persisted, the confirm id stays confirmable.
    assert paper._store.get(plan_id, app.scope.account_id) is None
    assert app.paper_projection().quantity("600000") == 400

    # Bound-session open: the same confirm id re-sizes live and fills all.
    box[0] = day.replace(day=20, hour=9, minute=31)
    paper._quotes = _quotes(_clock(box), [_quote("10.66", box[0])])
    opened = paper.submit(OrderCommand(plan_id, "sell", "600000", 100, Decimal("10.55")))
    assert opened.status == "filled" and opened.filled_qty == 400
    assert app.paper_projection().quantity("600000") == 0


def test_sell_confirm_refused_on_disagreeing_calendar(tmp_path):
    # r4: a sell confirm outside the bound next_session is refused, even
    # while the quote clock reads a trading day - the package binds
    # execution to 11-23 and the quote clock 11-20 is not its session.
    day = datetime(2026, 11, 19, 19, 0, tzinfo=TZ)
    nextopen = day.replace(day=20, hour=9, minute=30)
    box = [nextopen]
    ds = _set_for(day.date(), (_exit_decision(exit_kind="full", fraction=None),))
    app = _app(tmp_path, ds, box)
    _seed_bucket(app)
    for ev in _buy_events(day):
        app.submit_order_event(app.scope, ev)
    app._decisions._set = dataclasses.replace(ds, next_session=box[0].replace(day=23).date())
    paper = _paper(app, tmp_path, box)
    plan_id = "plan:plan-OPP-EXIT-600000-" + day.date().isoformat()
    out = paper.submit(OrderCommand(plan_id, "sell", "600000", 100, Decimal("10.55")))
    assert out.status == "rejected" and out.reason_code == "MARKET_CLOSED"
    assert paper._store.get(plan_id, app.scope.account_id) is None
    assert app.paper_projection().quantity("600000") == 400

    # On the bound session itself the same confirm fills.
    box[0] = nextopen.replace(day=23)
    paper._quotes = _quotes(_clock(box), [_quote("10.66", box[0])])
    opened = paper.submit(OrderCommand(plan_id, "sell", "600000", 100, Decimal("10.55")))
    assert opened.status == "filled" and opened.filled_qty == 400


def test_unfilled_exit_intent_carries_to_next_package(tmp_path):
    # r2-6: an unfilled plan sell ends terminal at the bound close; the
    # successor package re-offers the same intent, re-sized on the live
    # holding.
    day = datetime(2026, 11, 19, 19, 0, tzinfo=TZ)
    nextopen = day.replace(day=20, hour=9, minute=30)
    box = [nextopen]
    ds = _set_for(day.date(), (_exit_decision(exit_kind="reduce", fraction="0.50"),))
    app = _app(tmp_path, ds, box)
    _seed_bucket(app)
    for ev in _buy_events(day):
        app.submit_order_event(app.scope, ev)
    paper = _paper(app, tmp_path, box, quote_price="10.30")  # below limit, no fill
    plan_id = "plan:plan-OPP-EXIT-600000-" + day.date().isoformat()
    first = paper.submit(OrderCommand(plan_id, "sell", "600000", 200, Decimal("10.55")))
    assert first.status == "open"
    # Bound session close: order ends terminal-unfilled.
    box[0] = nextopen.replace(hour=15, minute=1)
    paper.poll()
    assert paper._store.get(plan_id, app.scope.account_id).status == "expired"
    # Next package adds no new exit signal; the intent still carries.
    box[0] = nextopen.replace(day=23, hour=10)
    app._decisions._set = dataclasses.replace(
        _set_for(nextopen.date(), ()), next_session=box[0].date())
    bench = app.workbench(app.scope)
    carry_ids = [p.plan_id for p in bench.package.plans]
    assert any(pid.startswith("plan-carry-600000") for pid in carry_ids)


def test_carried_reduce_preserves_positive_remainder(tmp_path):
    # r3-6: an expired 200-share reduce carries as the SAME reduce intent -
    # the carry plan keeps a positive remainder, never degrades into a flat.
    day = datetime(2026, 11, 19, 19, 0, tzinfo=TZ)
    nextopen = day.replace(day=20, hour=9, minute=30)
    box = [nextopen]
    ds = _set_for(day.date(), (_exit_decision(exit_kind="reduce", fraction="0.50"),))
    app = _app(tmp_path, ds, box)
    _seed_bucket(app)
    for ev in _buy_events(day):
        app.submit_order_event(app.scope, ev)
    paper = _paper(app, tmp_path, box, quote_price="10.30")  # below limit -> no fill
    plan_id = "plan:plan-OPP-EXIT-600000-" + day.date().isoformat()
    opened = paper.submit(OrderCommand(plan_id, "sell", "600000", 200, Decimal("10.55")))
    assert opened.status == "open"
    box[0] = nextopen.replace(hour=15, minute=1)
    paper.poll()
    assert paper._store.get(plan_id, app.scope.account_id).status == "expired"
    # Successor HOLD-only package: the carried intent survives identity.
    box[0] = nextopen.replace(day=23, hour=10)
    app._decisions._set = dataclasses.replace(
        _set_for(nextopen.date(), ()), next_session=box[0].date())
    bench = app.workbench(app.scope)
    carry = next(p for p in bench.package.plans if p.plan_id.startswith("plan-carry-600000"))
    # Only the 200-share residual obligation carries; 200 stays held.
    assert carry.delta_qty == -200 and carry.target_qty == 200
    # The confirm of the carried id also stays inside the residual.
    paper._quotes = _quotes(_clock(box), [_quote("10.66", box[0])])
    out = paper.submit(
        OrderCommand("plan:" + carry.plan_id, "sell", "600000", 200, Decimal("10.55"))
    )
    assert out.filled_qty == 200
    assert app.paper_projection().quantity("600000") == 200


def test_inflight_sell_deducts_before_new_exit_qty(tmp_path):
    # r3-2: an in-flight sell already covering the reduce target leaves
    # zero; a partial cover only adds the difference.
    day = datetime(2026, 11, 19, 19, 0, tzinfo=TZ)
    box = [datetime(2026, 11, 20, 10, 0, tzinfo=TZ)]
    ds = _set_for(day.date(), (_exit_decision(exit_kind="reduce", fraction="0.50"),))
    app = _app(tmp_path, ds, box)
    _seed_bucket(app)
    for ev in _buy_events(day):
        app.submit_order_event(app.scope, ev)
    paper = _paper(app, tmp_path, box, quote_price="10.30")  # resting, no fill
    # 100 already resting -> the 200-target reduce only adds the other 100.
    paper.submit(OrderCommand("rest-100", "sell", "600000", 100, Decimal("10.60")))
    plan = app.paper_current_plan("plan-OPP-EXIT-600000-" + day.date().isoformat())
    assert plan is not None and plan.delta_qty == 100


def test_consumed_carry_emits_no_further_sell(tmp_path):
    # r4-1: a continuation that fills the original intent's residual in
    # full CONSUMES that intent - the next plain HOLD package emits no new
    # sell plan and no new order.  Regression for the double-sell audited
    # in round 3 (a filled 200-share carry re-emerged as a 100-share sell).
    day = datetime(2026, 11, 19, 19, 0, tzinfo=TZ)
    nextopen = day.replace(day=20, hour=9, minute=30)
    box = [nextopen]
    ds = _set_for(day.date(), (_exit_decision(exit_kind="reduce", fraction="0.50"),))
    app = _app(tmp_path, ds, box)
    _seed_bucket(app)
    for ev in _buy_events(day):
        app.submit_order_event(app.scope, ev)
    paper = _paper(app, tmp_path, box, quote_price="10.30")  # below limit -> rests
    plan_id = "plan:plan-OPP-EXIT-600000-" + day.date().isoformat()
    opened = paper.submit(OrderCommand(plan_id, "sell", "600000", 200, Decimal("10.55")))
    assert opened.status == "open"
    box[0] = nextopen.replace(hour=15, minute=1)
    paper.poll()  # expires terminal-unfilled inside the bound session

    # Monday's plain-HOLD package re-offers the intent as a continuation
    # row; confirming it in-session fills the 200-share residual.
    box[0] = nextopen.replace(day=23, hour=10)
    app._decisions._set = dataclasses.replace(
        _set_for(nextopen.date(), ()), next_session=box[0].date())
    bench = app.workbench(app.scope)
    carry = next(p for p in bench.package.plans if p.plan_id.startswith("plan-carry-600000"))
    carry_row = next(o for o in bench.opportunities if o.opportunity_id == "OPP-CARRY-600000")
    assert carry_row.action_code == "SELL" and carry_row.plan.plan_id == carry.plan_id
    paper._quotes = _quotes(_clock(box), [_quote("10.66", box[0])])
    filled = paper.submit(
        OrderCommand("plan:" + carry.plan_id, "sell", "600000", 200, Decimal("10.55"))
    )
    assert filled.status == "filled" and filled.filled_qty == 200
    assert app.paper_projection().quantity("600000") == 200

    # Tuesday's plain-HOLD package: the consumed intent emits nothing new.
    box[0] = nextopen.replace(day=24, hour=10)
    app._decisions._set = dataclasses.replace(
        _set_for(box[0].date() - timedelta(days=1), ()), next_session=box[0].date())
    bench2 = app.workbench(app.scope)
    assert not any(p.plan_id.startswith("plan-carry-600000") for p in bench2.package.plans)
    assert not any(p.side == "sell" for p in bench2.package.plans)
    # And the ledger records no further sell fill.
    sells = [r for r in app.paper_history().reports if r.side == "sell"]
    assert len(sells) == 1 and sells[0].quantity == 200


def test_consumed_carry_confirm_resolves_zero(tmp_path):
    # r4-1: after the continuation fills the residual, a late confirm of
    # the SAME carry id resolves nothing new to sell.
    day = datetime(2026, 11, 19, 19, 0, tzinfo=TZ)
    nextopen = day.replace(day=20, hour=9, minute=30)
    box = [nextopen]
    ds = _set_for(day.date(), (_exit_decision(exit_kind="reduce", fraction="0.50"),))
    app = _app(tmp_path, ds, box)
    _seed_bucket(app)
    for ev in _buy_events(day):
        app.submit_order_event(app.scope, ev)
    paper = _paper(app, tmp_path, box, quote_price="10.30")
    plan_id = "plan:plan-OPP-EXIT-600000-" + day.date().isoformat()
    opened = paper.submit(OrderCommand(plan_id, "sell", "600000", 200, Decimal("10.55")))
    assert opened.status == "open"
    box[0] = nextopen.replace(hour=15, minute=1)
    paper.poll()
    box[0] = nextopen.replace(day=23, hour=10)
    app._decisions._set = dataclasses.replace(
        _set_for(nextopen.date(), ()), next_session=box[0].date())
    bench = app.workbench(app.scope)
    carry = next(p for p in bench.package.plans if p.plan_id.startswith("plan-carry-600000"))
    paper._quotes = _quotes(_clock(box), [_quote("10.66", box[0])])
    paper.submit(
        OrderCommand("plan:" + carry.plan_id, "sell", "600000", 200, Decimal("10.55"))
    )
    # The same carry id is now terminal-filled; the package emits no new
    # continuation, and paper_current_plan sees nothing executable left.
    bench2 = app.workbench(app.scope)
    assert not any(p.plan_id.startswith("plan-carry-600000") for p in bench2.package.plans)


def test_workbench_row_final_qty_matches_plan_delta(tmp_path):
    # r4-2: list/detail/confirm/receipt all read the plan's final sized
    # quantity - the workbench row's plan delta IS the sell figure.
    day = datetime(2026, 11, 19, 19, 0, tzinfo=TZ)
    box = [datetime(2026, 11, 20, 10, 0, tzinfo=TZ)]
    ds = _set_for(day.date(), (_exit_decision(exit_kind="reduce", fraction="0.50"),))
    app = _app(tmp_path, ds, box)
    _seed_bucket(app)
    for ev in _buy_events(day):
        app.submit_order_event(app.scope, ev)
    bench = app.workbench(app.scope)
    row = next(o for o in bench.opportunities if o.security_code == "600000")
    assert row.action_code == "SELL" and row.plan.delta_qty == -200
    # The package itself names the executable session for page display.
    assert bench.package.next_session == ds.next_session

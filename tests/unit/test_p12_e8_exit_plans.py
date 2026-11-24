"""E8 sell and reduction plans: buy -> hold -> reduce -> exit -> settle."""

from __future__ import annotations

import dataclasses
from datetime import date, datetime, timedelta, timezone
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
    # E8 r5: bind this slice to the 8792 phase-1 execution mode - sell
    # confirms and sell registrations are display-only on the instance.
    app._sell_execution_open = False
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
    # E8 r5: the sell plan is still published for display (sized 200), but
    # the production confirm entry refuses it outright in phase 1.
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
    assert out.status == "rejected" and out.reason_code == "PLAN_NOT_EXECUTABLE"
    assert paper._store.get(out.order_id, app.scope.account_id) is None
    assert app.paper_projection().quantity("600000") == 400


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
    # E8 r5: every sell confirm is refused display-only, so nothing opens.
    first = paper.submit(OrderCommand(plan_id, "sell", "600000", 200, Decimal("10.60")))
    assert first.status == "rejected" and first.reason_code == "PLAN_NOT_EXECUTABLE"
    second = paper.submit(OrderCommand(plan_id, "sell", "600000", 200, Decimal("10.60")))
    assert second.status == "rejected" and second.reason_code == "PLAN_NOT_EXECUTABLE"
    view = paper.account_view()
    pos = next(p for p in view["positions"] if p["security_code"] == "600000")
    assert pos["quantity"] == 400



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
    # E8 r5: the reduce plan is display-only - the confirm entry refuses it,
    # the position stays held and the cycle stays RESERVED untouched.
    first = paper.submit(OrderCommand(plan_id, "sell", "600000", 200, Decimal("10.60")))
    assert first.status == "rejected" and first.reason_code == "PLAN_NOT_EXECUTABLE"
    assert app._facts.snapshot(app._context).projection.quantity("600000") == 400
    assert _bucket(app).phase is BucketPhase.RESERVED
    # A self-directed sell is refused by the same instance switch - the
    # phase-1 refusal lives on the bound account, not on the plan id.
    rest = paper.submit(OrderCommand("residual-s", "sell", "600000", 200, Decimal("10.60")))
    assert rest.status == "rejected" and rest.reason_code == "PLAN_NOT_EXECUTABLE"
    assert app._facts.snapshot(app._context).projection.quantity("600000") == 400



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


def _inject_terminal_sell(app, order_id, qty, filled, at, price="10.55", terminal=True):
    """Stage-1 helper: write a prior terminal sell order + its fill into the
    facts ledger directly (the phase-1 order-event entry refuses NEW sell
    registrations, so a carried-intent premise is injected at the port).
    """
    from quanthus.capabilities.execution.facts import (
        ExecutionReport, OrderObservation)
    ctx = app._context
    app._facts.submit_order_observation(
        ctx,
        OrderObservation(
            observation_id=order_id + ":open",
            order_id=order_id,
            status="in_progress",
            observed_at=at,
            source_id="test",
            source_sequence=order_id + ":open",
        ),
        accepted_total_qty=qty,
        side="sell",
        security_code="600000",
        price_bound=Decimal(price),
        estimated_fee_upper=Decimal("0"),
    )
    if filled:
        app._facts.submit_report(
            ctx,
            ExecutionReport(
                report_id=order_id + ":fill",
                security_code="600000",
                side="sell",
                quantity=filled,
                price=Decimal(price),
                confirmed_fee=Decimal("0"),
                occurred_at=at,
                source_id="test",
                source_sequence=order_id + ":fill",
                intended_quantity=qty,
                intended_price_cap=Decimal(price),
                order_id=order_id,
            ),
        )
    if terminal:
        app._facts.submit_order_observation(
            ctx,
            OrderObservation(
                observation_id=order_id + ":term",
                order_id=order_id,
                status="terminal",
                observed_at=at,
                source_id="test",
                source_sequence=order_id + ":term",
                final_cumulative_fill_qty=filled,
                cancelled_qty=qty - filled,
            ),
            accepted_total_qty=qty,
            side="sell",
            security_code="600000",
            price_bound=Decimal(price),
            estimated_fee_upper=Decimal("0"),
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
    # r5: the in-flight netting math is preserved - a resting sell already
    # covering the reduce target leaves zero new quantity - but the resting
    # sell itself is injected at the facts port because the phase-1 order
    # entry refuses new sell registrations on this instance.
    day = datetime(2026, 11, 19, 19, 0, tzinfo=TZ)
    box = [datetime(2026, 11, 20, 10, 0, tzinfo=TZ)]
    ds = _set_for(day.date(), (_exit_decision(exit_kind="reduce", fraction="0.50"),))
    app = _app(tmp_path, ds, box)
    _seed_bucket(app)
    for ev in _buy_events(day):
        app.submit_order_event(app.scope, ev)
    paper = _paper(app, tmp_path, box)
    # 200 already resting -> a 0.5-target reduce (sell 200) adds nothing.
    _inject_terminal_sell(app, "rest-open", 200, 0, box[0], price="10.60", terminal=False)
    # The same resting sell through the production confirm entry is refused.
    refused = paper.submit(OrderCommand("rest-open-2", "sell", "600000", 200, Decimal("10.60")))
    assert refused.status == "rejected" and refused.reason_code == "PLAN_NOT_EXECUTABLE"
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
    # E8 r5: the confirm entry refuses the display-only sell.  The
    # confirm-time live-mark sizing that would re-derive 300 is retained
    # for the later sell-open round (R4 review keeps the 200-vs-300
    # sizing check pending until sell execution opens).
    out = paper.submit(OrderCommand(
        "plan:plan-OPP-EXIT-600000-" + day.date().isoformat(),
        "sell", "600000", 100, Decimal("10.55")))
    assert out.status == "rejected" and out.reason_code == "PLAN_NOT_EXECUTABLE"
    assert app.paper_projection().quantity("600000") == 400


def test_sell_confirm_refused_outside_bound_session(tmp_path):
    # r5: phase-1 sell confirms are display-only - refused outright at any
    # hour (advisory, nothing stored), inside AND outside the bound session.
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
    assert closed.status == "rejected" and closed.reason_code == "PLAN_NOT_EXECUTABLE"
    # Advisory refusal: nothing persisted, no order row and no movement.
    assert paper._store.get(plan_id, app.scope.account_id) is None
    assert app.paper_projection().quantity("600000") == 400

    # Even at the bound open the phase-1 sell confirm stays refused.
    box[0] = day.replace(day=20, hour=9, minute=31)
    paper._quotes = _quotes(_clock(box), [_quote("10.66", box[0])])
    opened = paper.submit(OrderCommand(plan_id, "sell", "600000", 100, Decimal("10.55")))
    assert opened.status == "rejected" and opened.reason_code == "PLAN_NOT_EXECUTABLE"
    assert app.paper_projection().quantity("600000") == 400


def test_sell_confirm_refused_during_lunch_break(tmp_path):
    # r5: the phase-1 sell confirm is display-only - refused at 11:45 and
    # again at the 13:00 resume (advisory, nothing stored).
    day = datetime(2026, 11, 19, 19, 0, tzinfo=TZ)
    open_ = day.replace(day=20, hour=9, minute=30)
    lunch = open_.replace(hour=11, minute=45)
    resume = open_.replace(hour=13, minute=0)
    box = [lunch]
    ds = _set_for(day.date(), (_exit_decision(exit_kind="full", fraction=None),))
    app = _app(tmp_path, ds, box)
    _seed_bucket(app)
    for ev in _buy_events(day):
        app.submit_order_event(app.scope, ev)
    paper = _paper(app, tmp_path, box)
    plan_id = "plan:plan-OPP-EXIT-600000-" + day.date().isoformat()
    mid = paper.submit(OrderCommand(plan_id, "sell", "600000", 100, Decimal("10.55")))
    assert mid.status == "rejected" and mid.reason_code == "PLAN_NOT_EXECUTABLE"
    assert paper._store.get(plan_id, app.scope.account_id) is None
    assert app.paper_projection().quantity("600000") == 400
    box[0] = resume
    paper._quotes = _quotes(_clock(box), [_quote("10.66", box[0])])
    opened = paper.submit(OrderCommand(plan_id, "sell", "600000", 100, Decimal("10.55")))
    assert opened.status == "rejected" and opened.reason_code == "PLAN_NOT_EXECUTABLE"
    assert app.paper_projection().quantity("600000") == 400


def test_sell_confirm_refused_on_disagreeing_calendar(tmp_path):
    # r5: the phase-1 sell confirm is display-only - refused regardless of
    # the session the quote clock reads.
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
    assert out.status == "rejected" and out.reason_code == "PLAN_NOT_EXECUTABLE"
    assert paper._store.get(plan_id, app.scope.account_id) is None
    assert app.paper_projection().quantity("600000") == 400

    # On the bound session itself the same confirm is still refused.
    box[0] = nextopen.replace(day=23)
    paper._quotes = _quotes(_clock(box), [_quote("10.66", box[0])])
    opened = paper.submit(OrderCommand(plan_id, "sell", "600000", 100, Decimal("10.55")))
    assert opened.status == "rejected" and opened.reason_code == "PLAN_NOT_EXECUTABLE"


def test_unfilled_exit_intent_carries_to_next_package(tmp_path):
    # r5: an unfilled plan sell ended terminal at the bound close (injected
    # at the facts port - phase-1 sell entry refuses new sell orders); the
    # successor package still re-offers the same intent for display.
    day = datetime(2026, 11, 19, 19, 0, tzinfo=TZ)
    nextopen = day.replace(day=20, hour=9, minute=30)
    box = [nextopen]
    ds = _set_for(day.date(), (_exit_decision(exit_kind="reduce", fraction="0.50"),))
    app = _app(tmp_path, ds, box)
    _seed_bucket(app)
    for ev in _buy_events(day):
        app.submit_order_event(app.scope, ev)
    plan_id = "plan:plan-OPP-EXIT-600000-" + day.date().isoformat()
    _inject_terminal_sell(app, plan_id, 200, 0, nextopen)
    # Next package adds no new exit signal; the intent still carries.
    box[0] = nextopen.replace(day=23, hour=10)
    app._decisions._set = dataclasses.replace(
        _set_for(nextopen.date(), ()), next_session=box[0].date())
    bench = app.workbench(app.scope)
    carry_ids = [p.plan_id for p in bench.package.plans]
    assert any(pid.startswith("plan-carry-600000") for pid in carry_ids)


def test_carried_reduce_preserves_positive_remainder(tmp_path):
    # r5: an expired 200-share reduce carries as the SAME reduce intent -
    # the carry plan keeps a positive remainder, never degrades into a flat,
    # and stays display-only (confirm refused).
    day = datetime(2026, 11, 19, 19, 0, tzinfo=TZ)
    nextopen = day.replace(day=20, hour=9, minute=30)
    box = [nextopen]
    ds = _set_for(day.date(), (_exit_decision(exit_kind="reduce", fraction="0.50"),))
    app = _app(tmp_path, ds, box)
    _seed_bucket(app)
    for ev in _buy_events(day):
        app.submit_order_event(app.scope, ev)
    plan_id = "plan:plan-OPP-EXIT-600000-" + day.date().isoformat()
    _inject_terminal_sell(app, plan_id, 200, 0, nextopen)
    # Successor HOLD-only package: the carried intent survives identity.
    box[0] = nextopen.replace(day=23, hour=10)
    app._decisions._set = dataclasses.replace(
        _set_for(nextopen.date(), ()), next_session=box[0].date())
    bench = app.workbench(app.scope)
    carry = next(p for p in bench.package.plans if p.plan_id.startswith("plan-carry-600000"))
    # Only the 200-share residual obligation carries; 200 stays held.
    assert carry.delta_qty == -200 and carry.target_qty == 200
    # The confirm of the carried id is display-only in phase 1: refused.
    paper = _paper(app, tmp_path, box)
    paper._quotes = _quotes(_clock(box), [_quote("10.66", box[0])])
    out = paper.submit(
        OrderCommand("plan:plan-carry-600000-2026-11-20", "sell", "600000", 200, Decimal("10.55"))
    )
    assert out.status == "rejected" and out.reason_code == "PLAN_NOT_EXECUTABLE"
    assert app.paper_projection().quantity("600000") == 400


# ---- E8 r5 buy-side items (#723 R5 ruling) ----


def _buy_decision(code="600000", qty=200, market_date=None):
    from quanthus.capabilities.decision_access import EntryIntent
    md = market_date or datetime(2026, 11, 19, tzinfo=TZ).date()
    return MarketDecision(
        opportunity_id=f"OPP-ENTRY-{code}",
        security_code=code,
        security_name="测试证券",
        stage=OpportunityStage.ENTRY_PLAN,
        reason_codes=("CAPACITY_AVAILABLE",),
        explanations=(Explanation("CAPACITY_AVAILABLE", ExplanationUse.ACTION, "test@1", "entry"),),
        next_observation=None,
        chart=ChartEvidence(
            price_basis="none",
            as_of=datetime.combine(md, datetime.min.time(), tzinfo=TZ).replace(hour=15),
            bars=(
                PriceBar(
                    session=md,
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
        entry=EntryIntent(
            fraction=Decimal("0.5"),
            price_bound=Decimal("10.60"),
            mark_price=Decimal("10.55"),
            industry_id="test",
            eligible_at=datetime.combine(md + timedelta(days=1), datetime.min.time(), tzinfo=TZ).replace(hour=9, minute=30),
            expires_at=datetime.combine(md + timedelta(days=1), datetime.min.time(), tzinfo=TZ).replace(hour=15),
            window_ref="test",
            invalidations=(),
        ),
        component_ref=REF,
    )


def test_buy_confirm_runs_at_bound_session_open(tmp_path):
    # R5 buy-1: the plan-bound buy confirm is admitted at the bound
    # next_session 09:30 open - the same bound-window gate the sell side
    # keeps - and fills 200 at the bound open quote.
    day = datetime(2026, 11, 19, 19, 0, tzinfo=TZ)
    nextopen = day.replace(day=20, hour=9, minute=30)
    box = [nextopen]
    ds = _set_for(day.date(), (_buy_decision(qty=200),))
    app = _app(tmp_path, ds, box)
    _seed_bucket(app)
    paper = _paper(app, tmp_path, box)
    plan_id = "plan-OPP-ENTRY-600000-" + day.date().isoformat()
    plan = app.paper_current_plan(plan_id.removeprefix("plan-"))
    out = paper.submit(OrderCommand(plan_id, "buy", "600000", 200, Decimal("10.60")))
    assert out.status in ("open", "filled")


def test_buy_confirm_on_2026_10_08_reopen_session(tmp_path):
    # R5 buy-1 + P10 复市 (issuecomment-7145646570): 2026-10-08 is a real
    # session (the National Day closure is 10-01..10-07), so a package bound
    # to 10-08 confirms at its 09:30 open - never held as a holiday.
    day = datetime(2026, 10, 7, 19, 0, tzinfo=TZ)
    ds = _set_for(day.date(), (_buy_decision(qty=200, market_date=day.date()),))
    ds = dataclasses.replace(ds, next_session=date(2026, 10, 8))
    box = [datetime(2026, 10, 8, 9, 30, tzinfo=TZ)]
    app = _app(tmp_path, ds, box)
    _seed_bucket(app)
    paper = _paper(app, tmp_path, box)
    plan_id = "plan-OPP-ENTRY-600000-" + day.date().isoformat()
    out = paper.submit(OrderCommand(plan_id, "buy", "600000", 200, Decimal("10.60")))
    assert out.status in ("open", "filled")


def test_buy_confirm_outside_session_is_advisory_not_plan_lock(tmp_path):
    # R5 buy-3: an out-of-session buy confirm is refused MARKET_CLOSED as
    # an advisory - no stored row, no PLAN_NOT_FOUND - and the SAME order
    # id still resolves at the legal window.
    day = datetime(2026, 11, 19, 19, 0, tzinfo=TZ)
    closed = day.replace(day=19, hour=20)   # post-close
    box = [closed]
    ds = _set_for(day.date(), (_buy_decision(qty=200),))
    app = _app(tmp_path, ds, box)
    _seed_bucket(app)
    paper = _paper(app, tmp_path, box)
    plan_id = "plan:plan-OPP-ENTRY-600000-" + day.date().isoformat()
    refused = paper.submit(OrderCommand(plan_id, "buy", "600000", 200, Decimal("10.60")))
    assert refused.status == "rejected" and refused.reason_code == "MARKET_CLOSED"
    assert paper._store.get(plan_id, app.scope.account_id) is None
    # The same id confirms inside the bound session - no lock.
    box[0] = day.replace(day=20, hour=9, minute=31)
    paper._quotes = _quotes(_clock(box), [_quote("10.66", box[0])])
    opened = paper.submit(OrderCommand(plan_id, "buy", "600000", 200, Decimal("10.60")))
    assert opened.status in ("open", "filled")


def test_displayed_buy_qty_matches_bound_open_fill(tmp_path):
    # R5 buy-4: the workbench, plan and fill all agree on the quantity the
    # bound open actually executed - 400-held/200-buy everywhere, never a
    # different figure per surface.
    day = datetime(2026, 11, 19, 19, 0, tzinfo=TZ)
    nextopen = day.replace(day=20, hour=9, minute=30)
    box = [nextopen]
    ds = _set_for(day.date(), (_buy_decision(qty=200),))
    app = _app(tmp_path, ds, box)
    _seed_bucket(app)
    bench = app.workbench(app.scope)
    row = next(o for o in bench.opportunities if o.security_code == "600000")
    assert row.plan is not None and row.plan.delta_qty == 200
    paper = _paper(app, tmp_path, box)
    plan_id = "plan:plan-OPP-ENTRY-600000-" + day.date().isoformat()
    out = paper.submit(OrderCommand(plan_id, "buy", "600000", 200, Decimal("10.60")))
    assert out.status in ("open", "filled")
    if out.status == "filled":
        assert out.filled_qty == 200
    stored = paper._store.get(plan_id, app.scope.account_id)
    assert stored is not None and stored.quantity == 200


def test_inflight_sell_deducts_before_new_exit_qty(tmp_path):
    # r5: preserved netting - a partial in-flight cover only adds the
    # difference - with the resting sell injected at the facts port (the
    # phase-1 order entry refuses new sell registrations on this instance).
    day = datetime(2026, 11, 19, 19, 0, tzinfo=TZ)
    box = [datetime(2026, 11, 20, 10, 0, tzinfo=TZ)]
    ds = _set_for(day.date(), (_exit_decision(exit_kind="reduce", fraction="0.50"),))
    app = _app(tmp_path, ds, box)
    _seed_bucket(app)
    for ev in _buy_events(day):
        app.submit_order_event(app.scope, ev)
    paper = _paper(app, tmp_path, box, quote_price="10.30")  # resting, no fill
    # 100 already resting -> the 200-target reduce only adds the other 100.
    _inject_terminal_sell(app, "rest-100", 100, 0, box[0], price="10.60", terminal=False)
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
    plan_id = "plan:plan-OPP-EXIT-600000-" + day.date().isoformat()
    # A prior reduce order expired terminal-unfilled, then its continuation
    # filled the residual - injected at the facts port (phase-1 sell entry
    # refuses new sell orders).
    _inject_terminal_sell(app, plan_id, 200, 0, nextopen)
    monday_open = nextopen.replace(day=23, hour=9, minute=30)
    _inject_terminal_sell(app, "plan:plan-carry-600000-2026-11-20", 200, 200, monday_open, "10.66")

    # Monday's package: the consumed intent emits NO new sell plan - the
    # carry ledger reads original-accepted minus all fills = 0.
    box[0] = nextopen.replace(day=23, hour=10)
    app._decisions._set = dataclasses.replace(
        _set_for(nextopen.date(), ()), next_session=box[0].date())
    bench = app.workbench(app.scope)
    assert not any(p.plan_id.startswith("plan-carry-600000") for p in bench.package.plans)
    assert not any(p.side == "sell" for p in bench.package.plans)
    sells = [r for r in app.paper_history().reports if r.side == "sell"]
    assert len(sells) == 1 and sells[0].quantity == 200


def test_consumed_carry_confirm_resolves_zero(tmp_path):
    # r5: after the continuation filled the residual (injected at the facts
    # port), a late confirm of the SAME carry id is refused display-only
    # and the package emits no new continuation.
    day = datetime(2026, 11, 19, 19, 0, tzinfo=TZ)
    nextopen = day.replace(day=20, hour=9, minute=30)
    box = [nextopen]
    ds = _set_for(day.date(), (_exit_decision(exit_kind="reduce", fraction="0.50"),))
    app = _app(tmp_path, ds, box)
    _seed_bucket(app)
    for ev in _buy_events(day):
        app.submit_order_event(app.scope, ev)
    plan_id = "plan:plan-OPP-EXIT-600000-" + day.date().isoformat()
    _inject_terminal_sell(app, plan_id, 200, 0, nextopen)
    monday_open = nextopen.replace(day=23, hour=9, minute=30)
    _inject_terminal_sell(app, "plan:plan-carry-600000-2026-11-20", 200, 200, monday_open, "10.66")
    box[0] = nextopen.replace(day=23, hour=10)
    app._decisions._set = dataclasses.replace(
        _set_for(nextopen.date(), ()), next_session=box[0].date())
    bench = app.workbench(app.scope)
    # The consumed intent emits no new carry plan.
    assert not any(p.plan_id.startswith("plan-carry-600000") for p in bench.package.plans)
    # A late confirm of the consumed carry id is refused display-only.
    paper = _paper(app, tmp_path, box)
    paper._quotes = _quotes(_clock(box), [_quote("10.66", box[0])])
    out = paper.submit(
        OrderCommand("plan:plan-carry-600000-2026-11-20", "sell", "600000", 200, Decimal("10.55"))
    )
    assert out.status == "rejected" and out.reason_code == "PLAN_NOT_EXECUTABLE"


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

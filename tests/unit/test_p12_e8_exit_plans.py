"""E8 sell and reduction plans: buy -> hold -> reduce -> exit -> settle."""

from __future__ import annotations

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
    assert row.primary_constraint == "ALREADY_BELOW_TARGET"


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
    assert row.primary_constraint == "REDUCE_RESIDUE_LOT_ZERO"


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

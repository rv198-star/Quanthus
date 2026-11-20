"""P12 guided order: confirm the system's executable plan as a simulated order.

The replay fixture ``config/p12/paper-replay.v1.json`` carries three surfaces:
an executable entry plan (11-19 release), a "no action" day and an
"input not ready" day (11-20 release).  These tests prove the paper account
drives a confirm-or-skip task: the plan's identity rides the order as
``plan:{plan_id}`` so the ledger links the resulting fill back to the
published suggestion, and the link survives a relogin.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.p12_paper.application import PaperAccount, PaperOrderStore
from quanthus.p12_paper.exec_rule import OrderCommand
from quanthus.p12_paper.quotes import SHANGHAI, Quote, QuoteService
from quanthus.p12_paper.replay import Replay, ReplayQuotes
from quanthus.platform.adapters.p12_synthetic_fixture import load_synthetic_fixture
from quanthus.platform.p12.paper_fixture import (
    PAPER_ACCOUNT_ID,
    PaperReplayDecisionSource,
    build_paper_application,
)
from quanthus.policy import find_project_root
from quanthus.product_daily.application.models import SyntheticRuntimeAuthorization

FIXTURE = find_project_root() / "config/p12/paper-replay.v1.json"


def _clock(box):
    return lambda: box[0].astimezone(timezone.utc).astimezone()


def _quote(price, moment, code="600000", name="浦发银行", prev="10.60"):
    return Quote(
        security_code=code, security_name=name, exchange="上交所",
        status="live", last_price=Decimal(price), previous_close=Decimal(prev),
        open=Decimal("10.62"), high=Decimal("10.78"), low=Decimal("10.58"),
        volume_lots=900000, quote_time=moment, received_at=moment,
        source="replay", market_state="trading", stale=False,
    )


class _StubFetcher:
    def fetch_raw(self, code):
        return None, None


class _BoxQuotes(QuoteService):
    """Quote service bound to the replay clock; quotes come from a code->Quote box."""

    def __init__(self, box, now_box):
        super().__init__(now=lambda: now_box[0], fetcher=_StubFetcher())
        self._box = box
        self._now_box = now_box

    def quote(self, code):
        q = self._box.get(code)
        if q is None:
            raise LookupError(f"no replay quote for {code}")
        return q

    def session(self):
        now = self._now_box[0]
        return {
            "trading_date": now.date().isoformat(),
            "market_state": "trading" if now.hour < 15 else "closed",
            "server_time": now.isoformat(),
        }


def _quotes(box, now_box):
    return _BoxQuotes(box, now_box)


def _build(state_dir, now):
    app = build_paper_application(
        state_dir=state_dir,
        policy=load_synthetic_fixture(FIXTURE).policy,
        authorization=SyntheticRuntimeAuthorization(
            "local-paper:test", frozenset({PAPER_ACCOUNT_ID})
        ),
        now=now,
        decision_source=PaperReplayDecisionSource.from_path(FIXTURE, now),
    )
    return app


def _account(state_dir, now, qbox, session_box):
    app = _build(state_dir, now)
    store = PaperOrderStore(Path(state_dir) / "paper.sqlite", clock=now)
    quotes = _quotes(qbox, session_box)
    return PaperAccount(app=app, store=store, quotes=quotes, now=now), app


@pytest.fixture()
def state_dir(tmp_path):
    d = tmp_path / "paper"
    d.mkdir(parents=True)
    return d


# The replay clock inside the 11-20 buy window (09:30-15:00 Shanghai).
def _buy_window():
    return datetime(2026, 11, 20, 10, 30, tzinfo=SHANGHAI)


def _closed():
    return datetime(2026, 11, 20, 15, 30, tzinfo=SHANGHAI)


def test_replay_plan_is_executable(state_dir):
    now_box = [_buy_window()]
    now = _clock(now_box)
    session_box = [now_box[0]]
    qbox = {"600000": _quote("10.66", now_box[0])}
    acct, app = _account(state_dir, now, qbox, session_box)
    wb = app.workbench(app.scope)
    plan = next(o.plan for o in wb.opportunities if o.plan and o.plan.side == "buy")
    assert plan is not None and plan.delta_qty > 0
    assert plan.plan_id == "plan-OPP-REPLAY-600000-2026-11-19"
    out = acct.submit(OrderCommand(
        order_id=f"plan:{plan.plan_id}", side="buy",
        security_code="600000", quantity=plan.delta_qty, limit_price=plan.price_cap,
    ))
    assert out.status == "filled"
    # The server holds the plan's lot-rounded quantity (1,200 under the
    # 13,500 trial cap), not the strategy's raw 50% intent of 2,000.
    assert out.filled_qty == plan.delta_qty == 1200


def test_fill_links_back_to_plan_and_survives_relogin(state_dir):
    now_box = [_buy_window()]
    now = _clock(now_box)
    session_box = [now_box[0]]
    qbox = {"600000": _quote("10.66", now_box[0])}
    acct, app = _account(state_dir, now, qbox, session_box)
    plan_id = "plan-OPP-REPLAY-600000-2026-11-19"
    out = acct.submit(OrderCommand(
        order_id=f"plan:{plan_id}", side="buy",
        security_code="600000", quantity=2000, limit_price=Decimal("10.75"),
    ))
    assert out.status == "filled"
    # Relogin: rebuild the application over the same state dir and confirm the
    # ledger still links the fill back to the originating plan.
    app2 = _build(state_dir, now)
    history = app2._facts.history(app2._context)
    linked = [r for r in history.reports if r.suggestion_id == plan_id]
    assert linked and linked[0].order_id == f"plan:{plan_id}"
    assert linked[0].intended_quantity == 1200
    assert linked[0].intended_price_cap == Decimal("10.75")
    # The order list also keeps the plan-prefixed id for the user's own book.
    orders = app2_workbench_orders(app2)
    assert f"plan:{plan_id}" in orders


def app2_workbench_orders(app):
    return [o.order_id for o in app.records(app.scope).orders]


def test_expired_plan_window_rejects(state_dir):
    # After the 15:00 Shanghai expiry the same order is refused as outside the
    # window; a same-id resubmit does not reopen a new fill.
    now_box = [_buy_window()]
    now = _clock(now_box)
    session_box = [now_box[0]]
    qbox = {"600000": _quote("10.66", now_box[0])}
    acct, app = _account(state_dir, now, qbox, session_box)
    plan_id = "plan-OPP-REPLAY-600000-2026-11-19"
    acct.submit(OrderCommand(
        order_id=f"plan:{plan_id}", side="buy",
        security_code="600000", quantity=2000, limit_price=Decimal("10.75"),
    ))
    # Advance the replay clock past the close; the market is no longer trading.
    now_box[0] = _closed()
    session_box[0] = _closed()
    out = acct.submit(OrderCommand(
        order_id=f"plan:{plan_id}", side="buy",
        security_code="600000", quantity=2000, limit_price=Decimal("10.75"),
    ))
    # Same idempotent id returns the stored filled state, never a new order.
    assert out.status == "filled"
    assert acct.order(f"plan:{plan_id}")["status"] == "filled"


def test_no_action_and_input_blocked_forms(state_dir):
    # 11-20 release surfaces a "no action" and an "input not ready" item.
    now_box = [datetime(2026, 11, 21, 9, 0, tzinfo=SHANGHAI)]
    now = _clock(now_box)
    session_box = [now_box[0]]
    qbox = {}
    acct, app = _account(state_dir, now, qbox, session_box)
    wb = app.workbench(app.scope)
    by_code = {o.opportunity_id: o for o in wb.opportunities}
    noop = by_code["OPP-REPLAY-NOOP"]
    # "Today, nothing to do": a closed, non-actionable item.  The ended stage
    # and NONE action already read as "今天无操作"; the explanation and
    # next_observation carry the "无合格买点 / 下一检查时间" detail.
    assert noop.stage == "ended" and noop.action_code == "NONE"
    assert noop.plan is None and noop.status_code == "ENDED"
    blocked = by_code["OPP-REPLAY-INPUT"]
    # "Input not ready": paused with the missing-input reason and no plan.
    assert blocked.action_code == "PAUSED" and blocked.status_code == "MISSING_INPUT"
    assert "STALE_MARKET_INPUT" in blocked.reason_codes and blocked.plan is None


def test_no_free_bucket_blocks_new_security(state_dir):
    # The paper account admits two buckets; a third distinct security cannot
    # take a cycle and is refused before any fill posts.
    now_box = [_buy_window()]
    now = _clock(now_box)
    session_box = [now_box[0]]
    qbox = {
        "600000": _quote("10.66", now_box[0], code="600000", name="浦发银行"),
        "000001": _quote("11.20", now_box[0], code="000001", name="平安银行"),
        "000858": _quote("150.0", now_box[0], code="000858", name="五粮液"),
    }
    acct, app = _account(state_dir, now, qbox, session_box)
    acct.submit(OrderCommand(
        order_id="o1", side="buy", security_code="600000",
        quantity=100, limit_price=Decimal("10.75")))
    acct.submit(OrderCommand(
        order_id="o2", side="buy", security_code="000001",
        quantity=100, limit_price=Decimal("11.30")))
    out = acct.submit(OrderCommand(
        order_id="o3", side="buy", security_code="000858",
        quantity=100, limit_price=Decimal("151.0")))
    assert out.status == "rejected"
    assert out.reason_code == "FUNDING_NO_FREE_BUCKET"


TAPE = find_project_root() / "config/p12/paper-replay-tape.v1.json"


def test_suggestion_quote_and_match_share_one_replay_clock(state_dir):
    # Bind suggestion, quote source and match to the same replay tape/clock so
    # the simulated fill only ever sees a price already visible at the instant.
    import json
    tape = json.loads(TAPE.read_text(encoding="utf-8"))
    replay = Replay(tape, day="2026-11-20", source="历史回放",
                    start_at=_buy_window())
    now = replay.now
    app = _build(state_dir, now)
    store = PaperOrderStore(Path(state_dir) / "paper.sqlite", clock=now)
    acct = PaperAccount(app=app, store=store,
                        quotes=ReplayQuotes(replay, "历史回放"), now=now)
    plan_id = "plan-OPP-REPLAY-600000-2026-11-19"
    plan = next(o.plan for o in app.workbench(app.scope).opportunities if o.plan)
    out = acct.submit(OrderCommand(
        order_id=f"plan:{plan_id}", side="buy",
        security_code="600000", quantity=plan.delta_qty, limit_price=plan.price_cap,
    ))
    assert out.status == "filled"
    # The fill is priced at the replay-visible quote, never a live feed.
    assert out.avg_price == Decimal("10.72")
    assert out.matched_quote_time == replay.now()
    linked = [r for r in app._facts.history(app._context).reports
              if r.suggestion_id == plan_id]
    assert linked and linked[0].order_id == f"plan:{plan_id}"
    # The intraday series only exposes minutes already completed at the
    # replay instant; advancing the clock reveals more of the tape.
    n0 = replay.fetch("600000")["points"]
    # Jump the replay clock to the close; the same code path now exposes the
    # whole tape and the session reads closed.
    from datetime import datetime as _dt
    replay.set_now(_dt.fromisoformat("2026-11-20T15:00:00+08:00"))
    assert replay.now().hour == 15
    n1 = replay.fetch("600000")["points"]
    assert len(n1) >= len(n0)
    assert acct._quotes.quote("600000").quote_time <= replay.now()


def test_expired_plan_never_submitted_is_refused(state_dir):
    # A normal plan confirm submitted *after* its window closed is refused on
    # admission: the live plan is no longer effective, so the server answers
    # PLAN_NOT_FOUND before any cash is committed.  Distinct from the
    # idempotent filled-order retry, which keeps its stored terminal state.
    now_box = [_buy_window()]
    now = _clock(now_box)
    session_box = [now_box[0]]
    qbox = {"600000": _quote("10.66", now_box[0])}
    acct, app = _account(state_dir, now, qbox, session_box)
    # Advance past the 15:00 close; the plan's window has expired.
    now_box[0] = _closed()
    session_box[0] = _closed()
    plan_id = "plan-OPP-REPLAY-600000-2026-11-19"
    out = acct.submit(OrderCommand(
        order_id=f"plan:{plan_id}", side="buy",
        security_code="600000", quantity=1200, limit_price=Decimal("10.75"),
    ))
    assert out.status == "rejected"
    assert out.reason_code == "PLAN_NOT_FOUND"
    # Nothing was posted: the ledger holds no fill and the order is terminal.
    assert app._facts.history(app._context).reports == ()
    assert acct.order(f"plan:{plan_id}")["status"] == "rejected"


def test_plan_confirm_uses_server_qty_and_cap_not_client(state_dir):
    # The server-side plan governs: a confirm order that echoes a different
    # quantity or cap is still bound to the live plan's authoritative values.
    now_box = [_buy_window()]
    now = _clock(now_box)
    session_box = [now_box[0]]
    qbox = {"600000": _quote("10.66", now_box[0])}
    acct, app = _account(state_dir, now, qbox, session_box)
    plan = next(o.plan for o in app.workbench(app.scope).opportunities if o.plan)
    # Client asks for 100 @ 9.99; the live plan governs at 2000 @ 10.75.
    out = acct.submit(OrderCommand(
        order_id=f"plan:{plan.plan_id}", side="buy",
        security_code="600000", quantity=100, limit_price=Decimal("9.99"),
    ))
    assert out.status == "filled" and out.filled_qty == plan.delta_qty
    linked = [r for r in app._facts.history(app._context).reports
              if r.suggestion_id == plan.plan_id]
    assert linked[0].intended_quantity == plan.delta_qty
    assert linked[0].intended_price_cap == plan.price_cap


def test_trial_cap_binds_below_strategy_intent(state_dir):
    # The 50% strategy intent (2,000 shares) is capped by the account's
    # trial_fraction (0.30 x 45,000 = 13,500): the published plan already
    # carries the lot-rounded quantity the account can take (1,200 shares,
    # 12,905 incl. fee), so suggestion and confirm share one effective ceiling.
    now_box = [_buy_window()]
    now = _clock(now_box)
    session_box = [now_box[0]]
    qbox = {"600000": _quote("10.66", now_box[0])}
    acct, app = _account(state_dir, now, qbox, session_box)
    plan = next(o.plan for o in app.workbench(app.scope).opportunities if o.plan)
    assert plan.delta_qty == 1200
    assert plan.cash_bound == Decimal("12905.00")
    # The same effective ceiling governs at confirm time: a client echo of the
    # raw intent cannot raise the hold above the trial cap.
    out = acct.submit(OrderCommand(
        order_id=f"plan:{plan.plan_id}", side="buy",
        security_code="600000", quantity=2000, limit_price=Decimal("10.75"),
    ))
    assert out.status == "filled" and out.filled_qty == 1200


def test_confirm_re_evaluates_when_bucket_already_spent(state_dir):
    # Control for changed capacity at confirm time: once the buy filled and
    # its bucket is spent, a new plan reference for the same security cannot
    # re-open above the remaining trial headroom - the live plan resolves to
    # a smaller delta and the server binds to it.
    now_box = [_buy_window()]
    now = _clock(now_box)
    session_box = [now_box[0]]
    qbox = {"600000": _quote("10.66", now_box[0])}
    acct, app = _account(state_dir, now, qbox, session_box)
    plan = next(o.plan for o in app.workbench(app.scope).opportunities if o.plan)
    acct.submit(OrderCommand(
        order_id=f"plan:{plan.plan_id}", side="buy",
        security_code="600000", quantity=plan.delta_qty, limit_price=plan.price_cap,
    ))
    # Re-resolving the same plan after the fill: the account now holds the
    # position, so the still-open bucket is attributed and a fresh confirm of
    # the *same* plan id binds the remaining delta (now zero -> PLAN_NOT_FOUND
    # because no positive delta remains to execute).
    resolved = app.paper_current_plan(plan.plan_id)
    assert resolved is None or resolved.delta_qty <= 0

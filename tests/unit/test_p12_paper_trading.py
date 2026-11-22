"""R1: simulated paper trading - isolation, matching, day-close and T+1."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.p12_paper.application import PaperAccount, PaperOrderStore
from quanthus.p12_paper.exec_rule import OrderCommand
from quanthus.p12_paper.quotes import Quote, QuoteService, SHANGHAI
from quanthus.platform.adapters.p12_synthetic_fixture import load_synthetic_fixture
from quanthus.platform.p12.assembly import synthetic_applications
from quanthus.policy import find_project_root

FIXTURE = find_project_root() / "config/p12/synthetic-desktop.v1.json"


def _clock(box):
    return lambda: box[0].astimezone(timezone.utc).astimezone()


def _quote(price, moment, prev="10.50"):
    return Quote(
        security_code="600000", security_name="浦发银行", exchange="上交所",
        status="live", last_price=Decimal(price), previous_close=Decimal(prev),
        open=Decimal("10.55"), high=Decimal("10.80"), low=Decimal("10.52"),
        volume_lots=900000, quote_time=moment, received_at=moment,
        source="tencent", market_state="trading", stale=False,
    )


class _StubFetcher:
    def fetch_raw(self, code):
        return None, None


def _quotes(now, box):
    svc = QuoteService(now=now, fetcher=_StubFetcher())
    svc.quote = lambda code: box[0]
    return svc


@pytest.fixture()
def env(tmp_path):
    box = [datetime(2026, 11, 19, 14, 0, 0, tzinfo=SHANGHAI)]
    now = _clock(box)
    fixture = load_synthetic_fixture(FIXTURE)
    apps = synthetic_applications(state_dir=tmp_path, fixture=fixture, clock=now)
    qbox = [_quote("10.66", box[0])]
    store = PaperOrderStore(tmp_path / "paper.sqlite", clock=now)
    return apps, box, qbox, store, now


def test_account_isolation(env):
    apps, box, qbox, store, now = env
    a = PaperAccount(app=apps["SYN-P12-A"], store=store, quotes=_quotes(now, qbox), now=now)
    b = PaperAccount(app=apps["SYN-P12-B"], store=store, quotes=_quotes(now, qbox), now=now)
    a.submit(OrderCommand("a1", "buy", "600000", 100, Decimal("10.80")))
    assert [o["order_id"] for o in a.orders()] == ["a1"]
    assert b.orders() == []
    assert b.account_view()["cash_available"] == "10000.00"


def test_day_close_expires_and_after_close_rejects(env):
    apps, box, qbox, store, now = env
    a = PaperAccount(app=apps["SYN-P12-A"], store=store, quotes=_quotes(now, qbox), now=now)
    a.submit(OrderCommand("x1", "buy", "600000", 100, Decimal("10.60")))
    assert a.order("x1")["status"] == "open"
    box[0] = datetime(2026, 11, 19, 15, 17, 0, tzinfo=SHANGHAI)
    a.poll()
    assert a.order("x1")["status"] == "expired"
    out = a.submit(OrderCommand("x2", "buy", "600000", 100, Decimal("10.80")))
    assert out.status == "rejected" and out.reason_code == "MARKET_CLOSED"
    # next trading day the stale order does not fill
    box[0] = datetime(2026, 11, 20, 10, 0, 0, tzinfo=SHANGHAI)
    qbox[0] = _quote("10.50", box[0])
    a.poll()
    assert a.order("x1")["status"] == "expired"


def test_fill_at_quote_price_and_price_band(env):
    apps, box, qbox, store, now = env
    qbox[0] = _quote("10.80", box[0])
    a = PaperAccount(app=apps["SYN-P12-A"], store=store, quotes=_quotes(now, qbox), now=now)
    out = a.submit(OrderCommand("b1", "buy", "600000", 100, Decimal("10.90")))
    assert out.status == "filled" and out.avg_price == Decimal("10.80")
    over = a.submit(OrderCommand("b2", "buy", "600000", 100, Decimal("15.00")))
    assert over.status == "rejected" and over.reason_code == "PRICE_OUT_OF_BOUNDS"


def test_t1_sellable_and_order_linkage(env):
    apps, box, qbox, store, now = env
    a = PaperAccount(app=apps["SYN-P12-A"], store=store, quotes=_quotes(now, qbox), now=now)
    out = a.submit(OrderCommand("s1", "buy", "600000", 100, Decimal("10.70")))
    assert out.status == "filled" and out.receipt_id is not None
    view = a.account_view()
    pos = next(p for p in view["positions"] if p["security_code"] == "600000")
    assert pos["today_bought_qty"] == 100 and pos["sellable_qty"] == 0
    sell = a.submit(OrderCommand("s2", "sell", "600000", 100, Decimal("10.70")))
    assert sell.status == "rejected" and sell.reason_code == "SELL_NOT_ALLOWED_T1"
    history = apps["SYN-P12-A"].paper_history()
    assert any(getattr(x, "order_id", None) == "s1"
               for x in list(history.reports) + list(history.observations))


def test_idempotent_resubmit_and_persist(env, tmp_path):
    apps, box, qbox, store, now = env
    a = PaperAccount(app=apps["SYN-P12-A"], store=store, quotes=_quotes(now, qbox), now=now)
    out = a.submit(OrderCommand("i1", "buy", "600000", 100, Decimal("10.70")))
    store2 = PaperOrderStore(tmp_path / "paper.sqlite", clock=now)
    a2 = PaperAccount(app=apps["SYN-P12-A"], store=store2, quotes=_quotes(now, qbox), now=now)
    assert a2.order("i1")["status"] == "filled"
    again = a2.submit(OrderCommand("i1", "buy", "600000", 100, Decimal("10.70")))
    assert again.status == "filled" and again.receipt_id == out.receipt_id


def test_concurrent_match_does_not_revert_filled(env):
    """R2/N1: racing matchers on one open order leave it filled, exactly once."""
    apps, box, qbox, store, now = env
    qbox[0] = _quote("10.80", box[0])
    app = apps["SYN-P12-A"]
    a1 = PaperAccount(app=app, store=store, quotes=_quotes(now, qbox), now=now)
    a1.submit(OrderCommand("c1", "buy", "600000", 100, Decimal("10.90")))
    assert a1.order("c1")["status"] in ("open", "filled")

    import threading
    errs = []
    def worker():
        try:
            for _ in range(30):
                PaperAccount(app=app, store=store, quotes=_quotes(now, qbox), now=now).poll()
        except Exception as e:
            errs.append(e)
    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t_ in threads: t_.start()
    for t_ in threads: t_.join()
    assert not errs
    final = PaperAccount(app=app, store=store, quotes=_quotes(now, qbox), now=now).order("c1")
    assert final["status"] == "filled"
    view = PaperAccount(app=app, store=store, quotes=_quotes(now, qbox), now=now).account_view()
    assert view["cash_held_open_buys"] == "0.00"
    out = PaperAccount(app=app, store=store, quotes=_quotes(now, qbox), now=now).cancel("c1")
    assert out["status"] == "filled" and out["cancelled_qty"] == 0


def test_cross_account_same_order_id_isolation(env):
    """R2/N2: another account cannot read/cancel or replay a foreign order id."""
    apps, box, qbox, store, now = env
    a = PaperAccount(app=apps["SYN-P12-A"], store=store, quotes=_quotes(now, qbox), now=now)
    b = PaperAccount(app=apps["SYN-P12-B"], store=store, quotes=_quotes(now, qbox), now=now)
    a.submit(OrderCommand("dup", "buy", "600000", 100, Decimal("10.80")))
    assert a.order("dup")["status"] == "filled"
    assert b.order("dup") is None
    import pytest as _p
    with _p.raises(Exception):
        b.cancel("dup")
    outb = b.submit(OrderCommand("dup", "buy", "600000", 100, Decimal("10.80")))
    assert outb.status == "filled"
    assert [o["order_id"] for o in b.orders()] == ["dup"]
    assert a.order("dup")["receipt_id"] != outb.receipt_id


def test_self_directed_buy_binds_bucket_and_reconciles(env):
    """R2: a self-directed buy lands in a bucket cycle; funding reconciles."""
    apps, box, qbox, store, now = env
    qbox[0] = _quote("10.80", box[0])
    app = apps["SYN-P12-A"]
    a = PaperAccount(app=app, store=store, quotes=_quotes(now, qbox), now=now)
    out = a.submit(OrderCommand("buy-1", "buy", "600000", 100, Decimal("10.90")))
    assert out.status == "filled"
    # The buy must be attributable to a bucket cycle so the funding page can
    # reconcile the partition against account cash (no LEDGER_MISMATCH).
    fv = app.funding_rules(app.scope)
    status_reason = getattr(fv, "status_reason", None)
    assert status_reason is None or "MISMATCH" not in str(status_reason)
    # one bucket is RESERVED into this security's cycle; cash reflects the fill
    fs = app.funding_state(app.scope)
    assert fs is not None and any(
        b.cycle_id is not None and b.cycle_id.startswith("paper:600000")
        for b in fs[0].buckets
    )
    view = a.account_view()
    assert view["cash"] == "8920.00"


def test_second_security_uses_free_bucket(env):
    """R3: a second self-directed buy claims the remaining free bucket.

    The first buy opens bucket-01's cycle; the second must not see that
    bucket as 'already attributed' and stop - it claims the still-empty
    bucket-02 so the partition reconciles across both securities.
    """
    apps, box, qbox, store, now = env
    qbox[0] = _quote("10.80", box[0])
    app = apps["SYN-P12-A"]
    a = PaperAccount(app=app, store=store, quotes=_quotes(now, qbox), now=now)
    a.submit(OrderCommand("b1", "buy", "600000", 100, Decimal("10.90")))
    a.submit(OrderCommand("b2", "buy", "000001", 100, Decimal("10.90")))
    fv = app.funding_rules(app.scope)
    assert getattr(fv, "status_reason", None) is None
    fs = app.funding_state(app.scope)
    binding = {b.bucket_id: b.cycle_id for b in fs[0].buckets if b.cycle_id}
    assert any("600000" in c for c in binding.values())
    assert any("000001" in c for c in binding.values())
    assert len(binding) == 2


def test_pre_binding_fill_upgrades_into_empty_bucket(env):
    """R3: a fill posted before attribution upgrades on read.

    A partition bound flat, then a fill admitted while no bucket owns the
    security, leaves the fill's cash unaccountable.  The maintenance sweep
    must claim an empty bucket for the security and lower the cycle floor to
    the binding anchor so the partition explains the account again.
    """
    apps, box, qbox, store, now = env
    app = apps["SYN-P12-A"]
    scope = app.scope
    from quanthus.capabilities.funding import initial_funding_state
    history = app._facts.snapshot(app._context).history
    state = initial_funding_state(app._policy.funding, history.baseline_cash)
    app.bind_funding(scope, state)
    # Admit a fill + terminal order + fee without any bucket attribution,
    # mirroring a ledger written before the attribution flow existed.
    from quanthus.product_daily.application.desktop import SubmitOrderEvent
    from decimal import Decimal as D
    day = 19
    events = (
        ("fill", {"security_code": "600000", "side": "buy", "quantity": 100,
                  "price": D("10.42"), "confirmed_fee": D("0.00"),
                  "accepted_total_qty": 100, "price_bound": D("10.42")}),
        ("observation", {"order_status": "terminal",
                         "final_cumulative_fill_qty": 100, "cancelled_qty": 0}),
        ("fee", {"confirmed_fee": D("0.00")}),
    )
    for index, (kind, fields) in enumerate(events):
        occurred = now()
        outcome = app.submit_order_event(
            scope,
            SubmitOrderEvent(f"L-{kind}-{index}", kind, "L-ORDER", occurred, **fields),
        )
        assert outcome.status == "POSTED", (kind, outcome.status)
    # The admitted fill is the account's only security, so the upgrade path
    # can uniquely attribute it to a free bucket: a page read reconciles the
    # partition instead of leaving the cash flagged as an unexplained gap.
    app.workbench(scope)
    fv2 = app.funding_rules(scope)
    assert getattr(fv2, "status_reason", None) is None
    fs = app.funding_state(scope)
    assert any(
        b.cycle_id is not None and "600000" in b.cycle_id
        for b in fs[0].buckets
    )


def test_paper_application_reopens_across_calendar_days(tmp_path):
    """Reopening SYN-PAPER-A on a later day stays append-only and keeps baseline."""
    from quanthus.platform.adapters.p12_synthetic_fixture import load_synthetic_fixture
    from quanthus.platform.p12.paper_fixture import (
        PAPER_ACCOUNT_ID,
        build_paper_application,
    )
    from quanthus.policy import find_project_root
    from quanthus.product_daily.application.models import SyntheticRuntimeAuthorization

    fixture = load_synthetic_fixture(find_project_root() / "config/p12/synthetic-desktop.v1.json")
    auth = SyntheticRuntimeAuthorization(
        "local-paper:test", frozenset({PAPER_ACCOUNT_ID})
    )
    day1 = datetime(2026, 11, 20, 19, 47, 30, tzinfo=timezone.utc)
    day2 = datetime(2026, 11, 23, 1, 50, 0, tzinfo=timezone.utc)
    build_paper_application(
        state_dir=tmp_path,
        policy=fixture.policy,
        authorization=auth,
        now=lambda: day1,
    )
    reopened = build_paper_application(
        state_dir=tmp_path,
        policy=fixture.policy,
        authorization=auth,
        now=lambda: day2,
    )
    wb = reopened.workbench(reopened.scope)
    assert wb.package.market_date.isoformat() == "2026-11-23"
    assert wb.account.cash_total == Decimal("100000.00")


def test_paper_application_reopen_still_rejects_tampered_digest(tmp_path):
    """Retaining prior dates never relaxes stored package digest verification."""
    import json
    import sqlite3

    from quanthus.platform.adapters.p12_synthetic_fixture import load_synthetic_fixture
    from quanthus.platform.p12.paper_fixture import (
        PAPER_ACCOUNT_ID,
        PAPER_FACTS_STORE,
        build_paper_application,
    )
    from quanthus.policy import find_project_root
    from quanthus.product_daily.application.models import SyntheticRuntimeAuthorization
    from quanthus.shared.fact_contracts import FactError

    fixture = load_synthetic_fixture(find_project_root() / "config/p12/synthetic-desktop.v1.json")
    auth = SyntheticRuntimeAuthorization(
        "local-paper:test", frozenset({PAPER_ACCOUNT_ID})
    )
    day1 = datetime(2026, 11, 20, 19, 47, 30, tzinfo=timezone.utc)
    day2 = datetime(2026, 11, 23, 1, 50, 0, tzinfo=timezone.utc)
    build_paper_application(
        state_dir=tmp_path,
        policy=fixture.policy,
        authorization=auth,
        now=lambda: day1,
    )
    with sqlite3.connect(tmp_path / PAPER_FACTS_STORE) as db:
        row = db.execute("SELECT binding_json FROM configuration_bindings").fetchone()
        binding = json.loads(row[0])
        binding["decision_source"]["package_digests"]["2026-11-20"] = "sha256:tampered"
        db.execute(
            "UPDATE configuration_bindings SET binding_json = ?",
            (json.dumps(binding),),
        )
        db.commit()

    with pytest.raises(FactError, match="CONFIGURATION_BINDING_MISMATCH"):
        build_paper_application(
            state_dir=tmp_path,
            policy=fixture.policy,
            authorization=auth,
            now=lambda: day2,
        )

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

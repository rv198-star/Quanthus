"""Regression tests for #717: live marks, bucketed holding classification, daily kline."""

from __future__ import annotations

import io
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from quanthus.p12_paper.application import PaperAccount, PaperOrderStore
from quanthus.p12_paper.exec_rule import OrderCommand
from quanthus.p12_paper.quotes import SHANGHAI, DailyKlineSeries, Quote, QuoteService
from quanthus.p12_paper.replay import Replay, ReplayQuotes
from quanthus.platform.adapters.p12_synthetic_fixture import load_synthetic_fixture
from quanthus.platform.composition import create_platform_kernel
from quanthus.platform.p12.http import P12AccountBinding, P12Runtime
from quanthus.platform.p12.local_server import LocalMfaDisabledCipher
from quanthus.platform.p12.paper import PaperService
from quanthus.platform.p12.paper_fixture import (
    PAPER_ACCOUNT_ID,
    build_paper_application,
    build_quote_live_marks,
)
from quanthus.platform.p12.transport import workbench_dto
from quanthus.platform.persistence import InMemoryPlatformStore
from quanthus.policy import find_project_root
from quanthus.product_daily.application.desktop import SubmitOrderEvent
from quanthus.product_daily.application.models import SyntheticRuntimeAuthorization

SYNTHETIC_FIXTURE = find_project_root() / "config/p12/synthetic-desktop.v1.json"


def _make_quote(code: str, price: str | None, at: datetime, *, status: str = "live") -> Quote:
    return Quote(
        security_code=code,
        security_name=code,
        exchange="深交所" if code.startswith(("00", "30")) else "上交所",
        status=status,
        last_price=None if price is None else Decimal(price),
        previous_close=Decimal(price) if price is not None else None,
        open=Decimal(price) if price is not None else None,
        high=Decimal(price) if price is not None else None,
        low=Decimal(price) if price is not None else None,
        volume_lots=1000 if status == "live" else 0,
        quote_time=at if price is not None else None,
        received_at=at,
        source="tencent" if status != "unavailable" else "none",
        market_state="trading",
        stale=False,
    )


class _StaticQuoteService(QuoteService):
    def __init__(self, quotes_by_code: dict[str, Quote], now) -> None:  # noqa: ANN001
        super().__init__(now=now)
        self._map = quotes_by_code
        self.calls: list[str] = []

    def quote(self, code: str) -> Quote:
        self.calls.append(code)
        return self._map[code]


def _build_paper_app(tmp_path: Path, now, live_marks=None, live_ref: str = "tencent-live-quote"):
    fixture = load_synthetic_fixture(SYNTHETIC_FIXTURE)
    auth = SyntheticRuntimeAuthorization("local-paper:test", frozenset({PAPER_ACCOUNT_ID}))
    return build_paper_application(
        state_dir=tmp_path,
        policy=fixture.policy,
        authorization=auth,
        now=now,
        live_marks=live_marks,
        live_valuation_ref=live_ref,
    )


def _buy_via_paper(
    app, tmp_path: Path, now, quotes_svc, order_id: str, code: str, qty: int, price: str
):
    store = PaperOrderStore(tmp_path / "p12-paper.sqlite", clock=now)
    acct = PaperAccount(app=app, store=store, quotes=quotes_svc, now=now)
    out = acct.submit(OrderCommand(order_id, "buy", code, qty, Decimal(price)))
    assert out.status == "filled", out
    return out


def test_live_marks_values_self_directed_paper_holding_and_classifies_as_holding(
    tmp_path: Path,
) -> None:
    """Bucketed paper holding gets live mark valuation and holding/HOLD/READY classification."""
    t0 = datetime(2026, 11, 23, 13, 25, 42, tzinfo=SHANGHAI)
    now = lambda: t0  # noqa: E731
    qmap = {
        "002517": _make_quote("002517", "13.62", t0),
    }
    qsvc = _StaticQuoteService(qmap, now)
    live_marks = build_quote_live_marks(qsvc, ttl_seconds=10.0)
    app = _build_paper_app(tmp_path, now, live_marks=live_marks)
    _buy_via_paper(app, tmp_path, now, qsvc, "buy-002517", "002517", 2000, "13.63")

    # Advance live mark price to 14.00 and rebuild fresh cache lookup
    qmap["002517"] = _make_quote("002517", "14.00", t0)
    live_marks2 = build_quote_live_marks(qsvc, ttl_seconds=10.0)
    app._live_marks = live_marks2
    calls_before = len(qsvc.calls)
    wb = app.workbench(app.scope)
    wb_again = app.workbench(app.scope)
    # TTL cache avoids re-hitting QuoteService on back-to-back reads
    assert len(qsvc.calls) - calls_before == 1

    assert wb.account.cash_total == Decimal("72760.00")
    assert wb.account.positions_value.value == Decimal("28000.00")
    assert wb.account.economic_equity.value == Decimal("100760.00")
    assert wb.account.top_concentration_security == "002517"
    assert wb.account.top_concentration.value == Decimal("0.2779")
    assert wb.account.valuation_ref is not None and "tencent-live-quote" in wb.account.valuation_ref

    # Classification: bound bucket holding is holding/HOLD/READY, not unassigned
    holding = next(o for o in wb.opportunities if o.security_code == "002517")
    assert holding.opportunity_id == "paper-holding:002517"
    assert holding.queue_group == "holding"
    assert holding.action_code == "HOLD"
    assert holding.status_code == "READY"
    assert holding.reason_codes == ()
    assert holding.primary_constraint is None
    assert "1 号资金桶" in (holding.next_observation or "")

    # Must validate cleanly against the outbound desktop DTO contract
    dto = workbench_dto(wb_again)
    assert dto["account"]["economic_equity"]["value"] == "100760.00"
    assert dto["opportunities"][0]["queue_group"] == "holding"


def test_live_marks_fail_closed_when_quote_unavailable(tmp_path: Path) -> None:
    """Unavailable or failing quote keeps VALUATION_UNAVAILABLE instead of fabricating prices."""
    t0 = datetime(2026, 11, 23, 13, 25, 42, tzinfo=SHANGHAI)
    now = lambda: t0  # noqa: E731
    qmap = {"002517": _make_quote("002517", "13.62", t0)}
    qsvc = _StaticQuoteService(qmap, now)
    app = _build_paper_app(tmp_path, now, live_marks=None)
    _buy_via_paper(app, tmp_path, now, qsvc, "buy-002517", "002517", 100, "13.63")

    qmap["002517"] = _make_quote("002517", None, t0, status="unavailable")
    app._live_marks = build_quote_live_marks(qsvc, ttl_seconds=0.0)
    wb = app.workbench(app.scope)
    assert wb.account.positions_value.value is None
    assert wb.account.positions_value.reason_code == "VALUATION_UNAVAILABLE"
    assert wb.account.economic_equity.value is None
    assert wb.account.economic_equity.reason_code == "VALUATION_UNAVAILABLE"

    def exploding_marks(_codes):
        raise RuntimeError("quote timeout")

    app._live_marks = exploding_marks
    wb2 = app.workbench(app.scope)
    assert wb2.account.economic_equity.reason_code == "VALUATION_UNAVAILABLE"


def test_unbucketed_third_security_stays_unassigned_needs_reconciliation(tmp_path: Path) -> None:
    """Unattributed holding keeps unassigned/NEEDS_RECONCILIATION; bucketed ones stay holding."""
    t0 = datetime(2026, 11, 23, 10, 30, 0, tzinfo=SHANGHAI)
    now = lambda: t0  # noqa: E731
    qmap = {
        "600000": _make_quote("600000", "10.42", t0),
        "000001": _make_quote("000001", "10.30", t0),
    }
    qsvc = _StaticQuoteService(qmap, now)
    app = _build_paper_app(tmp_path, now, live_marks=build_quote_live_marks(qsvc, ttl_seconds=0.0))
    _buy_via_paper(app, tmp_path, now, qsvc, "b1", "600000", 100, "10.50")
    _buy_via_paper(app, tmp_path, now, qsvc, "b2", "000001", 100, "10.50")

    # Simulate an unattributed external third-security fill (like unassigned-controlled-account)
    for idx, (kind, kwargs) in enumerate((
        ("fill", {"security_code": "000858", "side": "buy", "quantity": 100,
                  "price": Decimal("10.22"), "confirmed_fee": Decimal("0.00"),
                  "accepted_total_qty": 100, "price_bound": Decimal("10.22")}),
        ("observation", {"order_status": "terminal",
                         "final_cumulative_fill_qty": 100, "cancelled_qty": 0}),
        ("fee", {"confirmed_fee": Decimal("0.00")}),
    )):
        outcome = app.submit_order_event(
            app.scope,
            SubmitOrderEvent(f"ext-{kind}-{idx}", kind, "ext-000858", t0, **kwargs),
        )
        assert outcome.status == "POSTED"

    wb = app.workbench(app.scope)
    by_code = {o.security_code: o for o in wb.opportunities}
    assert by_code["600000"].queue_group == "holding"
    assert by_code["000001"].queue_group == "holding"
    third = by_code["000858"]
    assert third.opportunity_id == "unassigned:000858"
    assert third.queue_group == "unassigned"
    assert third.action_code == "REVIEW"
    assert third.status_code == "NEEDS_RECONCILIATION"
    assert third.reason_codes == ("EXTERNAL_TRADE",)
    assert wb.account.reconciliation_status == "NEEDS_RECONCILIATION"


def test_daily_kline_series_parses_tencent_qfq_and_degrades_on_failure() -> None:
    """DailyKlineSeries parses [date, open, close, high, low, volume] and degrades safely."""
    payload = {
        "code": 0,
        "data": {
            "sh603087": {
                "qfqday": [
                    ["2026-11-20", "60.50", "60.10", "61.20", "59.80", "25000.00", {}],
                    ["2026-11-23", "60.22", "61.87", "62.07", "59.98", "36163", {}],
                    ["bad-date", "1", "2", "3", "0", "10"],
                ]
            }
        },
    }

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self) -> bytes:
            return json.dumps(payload).encode("utf-8")

    series = DailyKlineSeries(opener=lambda _req, timeout=10.0: _Resp())
    res = series.fetch("603087", now=datetime(2026, 11, 23, 14, 0, tzinfo=SHANGHAI))
    assert res["status"] == "live"
    assert res["source"] == "tencent-qfq-day"
    assert res["basis"] == "qfq"
    assert res["contract_version"] == "p12-paper-daily-kline-v1"
    assert "看盘参考" in res["disclaimer"]
    assert len(res["bars"]) == 2
    assert res["bars"][1] == {
        "session": "2026-11-23",
        "open": "60.22",
        "high": "62.07",
        "low": "59.98",
        "close": "61.87",
        "volume": 36163,
    }

    def _boom(_req, timeout=10.0):
        raise OSError("network unreachable")

    failed = DailyKlineSeries(opener=_boom).fetch("603087")
    assert failed["status"] == "unavailable"
    assert failed["bars"] == []
    assert "network unreachable" in failed["reason"]


def test_daily_kline_http_endpoint_and_replay_fallback(tmp_path: Path) -> None:
    """GET /api/p12/v1/securities/{code}/daily-kline enforces auth and Replay degrades offline."""
    tape = {
        "600000": {
            "name": "浦发银行",
            "previous_close": "10.60",
            "points": [{"time": "202611201029", "price": "10.72", "volume_lots": "100"}],
        }
    }
    replay = Replay(tape, day="2026-11-20", source="历史回放")
    rq = ReplayQuotes(replay, "历史回放")
    app = _build_paper_app(
        tmp_path,
        replay.now,
        live_marks=build_quote_live_marks(rq, ttl_seconds=0.0),
        live_ref="replay-quote:历史回放",
    )
    store = PaperOrderStore(tmp_path / "p12-paper.sqlite", clock=replay.now)
    paper_svc = PaperService(
        quotes=rq,
        intraday=replay,
        daily_kline=replay,
        accounts={PAPER_ACCOUNT_ID: PaperAccount(app=app, store=store, quotes=rq, now=replay.now)},
    )
    runtime = P12Runtime(
        runtime_ref="test-717:v1",
        bindings={
            PAPER_ACCOUNT_ID: P12AccountBinding(
                application=app,
                display_name="模拟账户",
                allowed_usernames=frozenset({"p12-tester"}),
                synthetic=False,
            )
        },
    )
    kernel = create_platform_kernel(
        store=InMemoryPlatformStore(),
        cipher=LocalMfaDisabledCipher(),
        p12_runtime=runtime,
        p12_paper=paper_svc,
    )
    kernel.migrate()
    kernel.identity.create_user(username="p12-tester", password="test-pass-123", role="operator")

    def call(method: str, path: str, body: dict | None = None, token: str | None = None):
        raw = json.dumps(body).encode("utf-8") if body is not None else b""
        env = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": "",
            "CONTENT_LENGTH": str(len(raw)),
            "CONTENT_TYPE": "application/json",
            "wsgi.input": io.BytesIO(raw),
        }
        if token:
            env["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        st: dict[str, int] = {}

        def start_response(status: str, _headers):
            st["code"] = int(status.split(" ", 1)[0])

        data = b"".join(kernel.app(env, start_response)).decode("utf-8")
        return st["code"], json.loads(data)

    unauth_code, _ = call("GET", "/api/p12/v1/securities/603087/daily-kline")
    assert unauth_code == 401
    lcode, lbody = call(
        "POST",
        "/api/platform/v1/auth/login",
        {"username": "p12-tester", "password": "test-pass-123"},
    )
    assert lcode == 200
    dcode, dbody = call("GET", "/api/p12/v1/securities/603087/daily-kline", token=lbody["token"])
    assert dcode == 200
    assert dbody["status"] == "unavailable"
    assert dbody["bars"] == []
    assert dbody["contract_version"] == "p12-paper-daily-kline-v1"

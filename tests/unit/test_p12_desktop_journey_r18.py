"""R18 desktop journey: the six brief steps on the sealed synthetic account.

One ordinary user walks T -> T+1 -> T+2 on SYN-P12-A through the public desktop
use cases only.  Reservation, bucket attribution and settlement all happen
inside the same write path as the user's confirmed facts; the funding page
stays read-only.  A controllable clock stands in for the ``--clock`` restart
used by the local server demo.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.capabilities.funding import BucketPhase
from quanthus.platform.adapters.p12_synthetic_fixture import load_synthetic_fixture
from quanthus.platform.p12.assembly import synthetic_applications
from quanthus.product_daily.application.desktop.views import (
    CorrectFill,
    SubmitFill,
    SubmitOrderEvent,
)
from quanthus.shared.fact_contracts import FactError

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "config/p12/synthetic-desktop.v1.json"
BUY_PLAN = "plan-OPP-SYN-600000-2026-11-18"


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, value: str) -> None:
        self.now = datetime.fromisoformat(value)


def _app(tmp_path: Path):
    fixture = load_synthetic_fixture(FIXTURE)
    clock = _Clock(datetime.fromisoformat("2026-11-18T19:00:00+08:00"))
    apps = synthetic_applications(state_dir=tmp_path, fixture=fixture, clock=clock)
    app = apps["SYN-P12-A"]
    app.bind_configuration()
    return app, clock


def _funding(app):
    state = app._facts.funding_state(app._context)
    return None if state is None else state[0]


def _bucket(app, bucket_id: str):
    funding = _funding(app)
    return next((b for b in funding.buckets if b.bucket_id == bucket_id), None)


def _cash(app) -> Decimal:
    return app._facts.snapshot(app._context).projection.cash


def _qty(app, security: str) -> int:
    snapshot = app._facts.snapshot(app._context)
    return next(
        (p.quantity for p in snapshot.projection.positions if p.security_code == security),
        0,
    )


def _opp(app, security: str):
    board = app.workbench(app.scope)
    return next(o for o in board.opportunities if o.security_code == security)


def _sell(order: str, at: datetime):
    """One complete terminal sell order of 400 @ 13.00, fee 6, in two fills."""
    return (
        SubmitOrderEvent(
            report_id=f"{order}-open",
            kind="observation",
            order_id=order,
            occurred_at=at,
            side="sell",
            security_code="600000",
            accepted_total_qty=400,
            price_bound=Decimal("13.00"),
            fee_upper_bound=Decimal("8.00"),
            order_status="in_progress",
        ),
        SubmitOrderEvent(
            report_id=f"{order}-fill1",
            kind="fill",
            order_id=order,
            occurred_at=at,
            security_code="600000",
            quantity=200,
            price=Decimal("13.00"),
        ),
        SubmitOrderEvent(
            report_id=f"{order}-fill2",
            kind="fill",
            order_id=order,
            occurred_at=at,
            security_code="600000",
            quantity=200,
            price=Decimal("13.00"),
        ),
        SubmitOrderEvent(
            report_id=f"{order}-fee",
            kind="fee",
            order_id=order,
            occurred_at=at,
            security_code="600000",
            confirmed_fee=Decimal("6.00"),
        ),
        SubmitOrderEvent(
            report_id=f"{order}-term",
            kind="observation",
            order_id=order,
            occurred_at=at,
            security_code="600000",
            order_status="terminal",
            final_cumulative_fill_qty=400,
            cancelled_qty=0,
        ),
    )


def test_step1_plan_published_waiting_and_missing(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    entry = _opp(app, "600000")
    assert (entry.stage, entry.action_code, entry.status_code) == (
        "entry_plan",
        "BUY",
        "READY",
    )
    assert (entry.plan.delta_qty, entry.plan.price_cap) == (400, Decimal("12.40"))
    assert _opp(app, "000002").status_code == "WAITING"
    assert _opp(app, "300001").status_code == "MISSING_INPUT"


def test_step2_buy_auto_reserves_bucket(tmp_path: Path) -> None:
    app, clock = _app(tmp_path)
    clock.advance("2026-11-19T10:05:00+08:00")
    outcome = app.submit_fill(
        app.scope,
        SubmitFill(
            report_id="buy-1",
            plan_id=BUY_PLAN,
            quantity=400,
            price=Decimal("12.38"),
            confirmed_fee=Decimal("5.00"),
            occurred_at=clock.now,
        ),
    )
    assert outcome.status == "POSTED" and outcome.deviations == ()
    assert _cash(app) == Decimal("5043.00") and _qty(app, "600000") == 400
    bucket = _bucket(app, "bucket-01")
    assert bucket is not None and bucket.phase is BucketPhase.RESERVED
    assert bucket.cycle_id == f"OPP-SYN-600000:{BUY_PLAN}"
    assert _bucket(app, "bucket-02").phase is BucketPhase.EMPTY


def test_step3_t1_package_shows_exit_plan(tmp_path: Path) -> None:
    app, clock = _app(tmp_path)
    clock.advance("2026-11-19T10:05:00+08:00")
    app.submit_fill(
        app.scope,
        SubmitFill(
            report_id="buy-1",
            plan_id=BUY_PLAN,
            quantity=400,
            price=Decimal("12.38"),
            confirmed_fee=Decimal("5.00"),
            occurred_at=clock.now,
        ),
    )
    clock.advance("2026-11-19T19:00:00+08:00")
    assert app._current_decisions().market_date.isoformat() == "2026-11-19"
    exit_opp = _opp(app, "600000")
    assert (exit_opp.stage, exit_opp.action_code, exit_opp.status_code) == (
        "exit_plan",
        "SELL",
        "READY",
    )
    assert app.records(app.scope) is not None


def test_step4_sell_two_fills_settles_bucket(tmp_path: Path) -> None:
    app, clock = _app(tmp_path)
    clock.advance("2026-11-19T10:05:00+08:00")
    app.submit_fill(
        app.scope,
        SubmitFill(
            report_id="buy-1",
            plan_id=BUY_PLAN,
            quantity=400,
            price=Decimal("12.38"),
            confirmed_fee=Decimal("5.00"),
            occurred_at=clock.now,
        ),
    )
    clock.advance("2026-11-20T10:00:00+08:00")
    events = _sell("ord-1", clock.now)
    for event in events[:2]:
        assert app.submit_order_event(app.scope, event).status == "POSTED"
    # Half sold: 200 shares still open, bucket not yet settled.
    assert _qty(app, "600000") == 200
    assert _bucket(app, "bucket-01").phase is BucketPhase.RESERVED
    for event in events[2:]:
        assert app.submit_order_event(app.scope, event).status == "POSTED"
    assert _cash(app) == Decimal("10237.00") and _qty(app, "600000") == 0
    funding = _funding(app)
    assert _bucket(app, "bucket-01").phase is BucketPhase.CLOSED
    assert funding.reserve == Decimal("237.00")
    # Re-running the terminal event does not re-settle or move cash.
    assert app.submit_order_event(app.scope, events[-1]).status == "POSTED"
    assert _cash(app) == Decimal("10237.00")
    assert _funding(app).reserve == Decimal("237.00")


def test_step5_correction_branch(tmp_path: Path) -> None:
    app, clock = _app(tmp_path)
    clock.advance("2026-11-19T10:05:00+08:00")
    app.submit_fill(
        app.scope,
        SubmitFill(
            report_id="buy-1",
            plan_id=BUY_PLAN,
            quantity=400,
            price=Decimal("12.38"),
            confirmed_fee=Decimal("5.00"),
            occurred_at=clock.now,
        ),
    )
    outcome = app.correct_fill(
        app.scope,
        CorrectFill(
            correction_id="corr-1",
            fact_id="buy-1",
            expected_fact_revision=1,
            quantity=300,
            price=Decimal("12.38"),
            reason="broker statement",
        ),
    )
    assert outcome.status == "POSTED"
    # 300 x 12.38 + 5 fee = 3719.00; cash 10000 - 3719 = 6281.00.
    assert _cash(app) == Decimal("6281.00") and _qty(app, "600000") == 300
    records = app.records(app.scope)
    assert records.reports[0].report.quantity == 400
    assert records.facts[0].fact.quantity == 300
    assert records.facts[0].corrections[0].status == "POSTED"


def test_step6_off_plan_price_still_posts(tmp_path: Path) -> None:
    app, clock = _app(tmp_path)
    clock.advance("2026-11-19T10:05:00+08:00")
    outcome = app.submit_fill(
        app.scope,
        SubmitFill(
            report_id="buy-1",
            plan_id=BUY_PLAN,
            quantity=400,
            price=Decimal("12.50"),
            confirmed_fee=Decimal("5.00"),
            occurred_at=clock.now,
        ),
    )
    assert outcome.status == "POSTED" and "PRICE_DEVIATION" in outcome.deviations
    assert _cash(app) == Decimal("4995.00") and _qty(app, "600000") == 400
    board = app.workbench(app.scope)
    assert board.account.cash_total == Decimal("4995.00")
    assert app.funding_rules(app.scope) is not None

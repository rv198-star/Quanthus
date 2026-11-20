"""R21: order-code resolution, the read-path settle sweep, and sell confirmation.

Product final-acceptance R2 returned one defect: after both sell fills, a user
who came back from the records page and confirmed the whole-order fee and the
terminal event by order id got the right cash but a stuck bucket.  The order
record owns the security code, a blank form code must not skip settlement, and
a bucket left stuck by an earlier blank-coded submission must repair itself on
the next page open.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.capabilities.funding import BucketPhase
from quanthus.platform.adapters.p12_synthetic_fixture import load_synthetic_fixture
from quanthus.platform.p12.assembly import synthetic_applications
from quanthus.product_daily.application.desktop.views import SubmitFill, SubmitOrderEvent
from quanthus.shared.fact_contracts import FactError

from test_p12_desktop_journey_r18 import BUY_PLAN, _Clock, _sell

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "config/p12/synthetic-desktop.v1.json"


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


def _reserve(app) -> Decimal:
    return _funding(app).reserve


def _held_position(app) -> int:
    snapshot = app._facts.snapshot(app._context)
    return next(
        (p.quantity for p in snapshot.projection.positions if p.security_code == "600000"),
        0,
    )


def _buy_and_first_sell(app, clock):
    """Reach the 'first sell fill posted' state used by the stuck-store repro."""
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
    assert _held_position(app) == 200
    return events


@pytest.mark.parametrize("code", ["", "600000"])
def test_blank_or_correct_code_settles_bucket(tmp_path: Path, code: str) -> None:
    """A blank form code and the correct code both end at the settled state."""
    app, clock = _app(tmp_path)
    events = _buy_and_first_sell(app, clock)
    # Second fill uses the order's own code so the position reaches zero.
    assert app.submit_order_event(app.scope, events[2]).status == "POSTED"
    assert _held_position(app) == 0
    # Fee and terminal may arrive with a blank or the correct security code.
    for event in events[3:]:
        assert app.submit_order_event(app.scope, dataclasses.replace(event, security_code=code)).status == "POSTED"
    assert _cash(app) == Decimal("10237.00")
    funding = _funding(app)
    assert _bucket(app, "bucket-01").phase is BucketPhase.CLOSED
    assert _bucket(app, "bucket-02").cash == Decimal("5000.00")
    assert funding.reserve == Decimal("237.00")


def test_conflicting_code_is_rejected_without_moving_the_ledger(tmp_path: Path) -> None:
    """A non-blank code that disagrees with the order is a field error."""
    app, clock = _app(tmp_path)
    events = _buy_and_first_sell(app, clock)
    assert app.submit_order_event(app.scope, events[2]).status == "POSTED"
    before_cash = _cash(app)
    with pytest.raises(FactError) as error:
        app.submit_order_event(
            app.scope, dataclasses.replace(events[3], security_code="600519")
        )
    assert error.value.reason_code == "INVALID_REPORT_SHAPE"
    # The fee fact was not posted and no bucket moved.
    assert _cash(app) == before_cash
    assert _bucket(app, "bucket-01").phase is BucketPhase.RESERVED


def test_stuck_bucket_repairs_on_next_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A bucket left stuck by an earlier blank-coded submit settles on open.

    Blocking the post-fact auto-settle reproduces the stored stuck state
    (position flat, order terminal, fee confirmed, bucket still reserved).
    Reopening the workbench runs the ``None`` sweep and settles it once.
    """
    app, clock = _app(tmp_path)
    events = _buy_and_first_sell(app, clock)
    assert app.submit_order_event(app.scope, events[2]).status == "POSTED"
    monkeypatch.setattr(app, "_auto_settle", lambda *a, **k: None)
    for event in events[3:]:
        assert app.submit_order_event(app.scope, event).status == "POSTED"
    monkeypatch.undo()
    # Stored stuck state: all facts complete, bucket still reserved, reserve 0.
    assert _held_position(app) == 0
    assert _bucket(app, "bucket-01").phase is BucketPhase.RESERVED
    assert _reserve(app) == Decimal("0.00")
    # The next read repairs it; a repeat read does not move it again.
    for _ in range(2):
        app.workbench(app.scope)
        assert _bucket(app, "bucket-01").phase is BucketPhase.CLOSED
        assert _reserve(app) == Decimal("237.00")
    assert _cash(app) == Decimal("10237.00")

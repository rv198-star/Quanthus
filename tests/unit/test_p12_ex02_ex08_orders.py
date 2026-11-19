"""v8 EX02 (two fills on one sell order) and EX08 (cancel with unreported fills)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.capabilities.decision_access import validate_decision_set
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
from quanthus.shared.values import AccountScope
from quanthus.product_daily.application.desktop import SubmitOrderEvent

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "config/p12/synthetic-desktop.v1.json"
T0 = datetime.fromisoformat("2026-11-19T09:30:00+08:00")
T1 = datetime.fromisoformat("2026-11-19T09:31:00+08:00")
T2 = datetime.fromisoformat("2026-11-19T09:32:00+08:00")
T3 = datetime.fromisoformat("2026-11-19T09:33:00+08:00")
T4 = datetime.fromisoformat("2026-11-19T09:34:00+08:00")


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
    app = P12DesktopApplication(
        scope=spec.scope,
        context=context,
        facts=facts,
        decisions=FixtureDecisionSource(fixture),
        policy=fixture.policy,
        clock=lambda: fixture.clock,
        clock_kind="synthetic_fixed",
    )
    return app


def _reopen(tmp_path: Path, account: str = "SYN-P12-A"):
    app = _app(tmp_path, account)
    app.bind_configuration()
    return app


def _event(report_id, order_id, kind, at, **kw) -> SubmitOrderEvent:
    return SubmitOrderEvent(report_id, kind, order_id, at, **kw)


# ---- EX02 ----------------------------------------------------------------------


def _ex02_setup(app) -> None:
    # Take over an account already holding 600 shares; baseline cost is unknown.
    app.establish_baseline(
        request_id="ex02-baseline", cash=Decimal("0.00"), quantity=600,
        security_code="600000",
    )
    app.bind_configuration()


def test_ex02_two_fills_one_order_fee_and_single_settlement(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _ex02_setup(app)

    first = app.submit_order_event(
        app.scope,
        _event(
            "ex02-sell-1", "ORD-EX02", "fill", T1,
            side="sell", security_code="600000", quantity=200, price=Decimal("10.00"),
            confirmed_fee=Decimal("0.00"), accepted_total_qty=600,
            price_bound=Decimal("10.10"), fee_upper_bound=Decimal("0.00"),
        ),
    )
    assert first.status == "POSTED"
    assert first.account.positions[0].quantity == 400
    mid = app.workbench(app.scope).account
    assert mid.cash_total == Decimal("2000.00")

    second = app.submit_order_event(
        app.scope,
        _event(
            "ex02-sell-2", "ORD-EX02", "fill", T2,
            side="sell", security_code="600000", quantity=400, price=Decimal("10.10"),
            confirmed_fee=Decimal("0.00"),
        ),
    )
    assert second.status == "POSTED"
    assert second.account.cash_total == Decimal("6040.00")
    assert second.account.positions == ()

    terminal = app.submit_order_event(
        app.scope,
        _event(
            "ex02-terminal", "ORD-EX02", "observation", T3,
            order_status="terminal", final_cumulative_fill_qty=600, cancelled_qty=0,
        ),
    )
    assert terminal.status == "POSTED"

    fee = app.submit_order_event(
        app.scope,
        _event(
            "ex02-fee", "ORD-EX02", "fee", T4, confirmed_fee=Decimal("6.00"),
        ),
    )
    assert fee.status == "POSTED"
    account = app.workbench(app.scope).account
    assert account.cash_total == Decimal("6034.00")
    assert account.cash_occupied == Decimal("0.00")
    assert account.positions == ()

    records = app.records(app.scope)
    order = next(o for o in records.orders if o.order_id == "ORD-EX02")
    assert order.status == "terminal"
    assert order.known_fill_qty == 600
    assert order.fee_confirmed_total == Decimal("6.00")
    assert order.settlement_ready is True
    fee_events = [e for e in records.events if e.kind == "fee"]
    assert len(fee_events) == 1

    # Retrying the same facts returns the original receipts without re-posting.
    retry = app.submit_order_event(
        app.scope,
        _event(
            "ex02-fee", "ORD-EX02", "fee", T4, confirmed_fee=Decimal("6.00"),
        ),
    )
    assert retry.status == "POSTED" and retry.receipt_id == fee.receipt_id
    assert app.workbench(app.scope).account.cash_total == Decimal("6034.00")

    # Reopen and rebuild reproduce the same ledger.
    again = _reopen(tmp_path)
    assert again.rebuild_matches(again.scope)
    assert again.workbench(again.scope).account.cash_total == Decimal("6034.00")


# ---- EX08 ----------------------------------------------------------------------


def _ex08_setup(app) -> None:
    app.establish_baseline(request_id="ex08-baseline", cash=Decimal("20000.00"))
    app.bind_configuration()


def test_ex08_cancel_keeps_unreported_fill_hold(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _ex08_setup(app)

    # Order accepted: buy 400 at a 12.00 bound with a 5.00 fee provision.
    opened = app.submit_order_event(
        app.scope,
        _event(
            "ex08-open", "ORD-EX08", "observation", T0,
            order_status="in_progress", side="buy", security_code="600000",
            accepted_total_qty=400, price_bound=Decimal("12.00"),
            fee_upper_bound=Decimal("5.00"),
        ),
    )
    assert opened.status == "POSTED"

    fill = app.submit_order_event(
        app.scope,
        _event(
            "ex08-fill-100", "ORD-EX08", "fill", T1,
            side="buy", security_code="600000", quantity=100, price=Decimal("12.00"),
            confirmed_fee=Decimal("0.00"),
        ),
    )
    assert fill.status == "POSTED"
    assert fill.account.cash_total == Decimal("18800.00")
    assert fill.account.positions[0].quantity == 100

    cancel = app.submit_order_event(
        app.scope,
        _event(
            "ex08-cancel", "ORD-EX08", "observation", T2,
            order_status="cancel_requested",
        ),
    )
    assert cancel.status == "POSTED"
    mid = app.workbench(app.scope).account
    # During the cancel request the full 300 remaining stay held.
    assert mid.cash_occupied == Decimal("300") * Decimal("12.00") + Decimal("5.00")
    assert mid.cash_available_for_new.value == mid.cash_total - mid.cash_occupied

    terminal = app.submit_order_event(
        app.scope,
        _event(
            "ex08-terminal", "ORD-EX08", "observation", T3,
            order_status="terminal", final_cumulative_fill_qty=150, cancelled_qty=250,
        ),
    )
    assert terminal.status == "POSTED"

    account = app.workbench(app.scope).account
    assert account.cash_total == Decimal("18800.00")
    assert account.positions[0].quantity == 100
    assert account.cash_occupied == Decimal("605.00")
    assert account.cash_available_for_new.value == Decimal("18195.00")
    assert account.reconciliation_status == "NEEDS_RECONCILIATION"

    order = next(o for o in app.records(app.scope).orders if o.order_id == "ORD-EX08")
    assert order.status == "terminal"
    assert order.known_fill_qty == 100
    assert order.unreported_fill_qty == 50
    assert order.hold_amount == Decimal("605.00")

    # Retry the terminal observation; nothing re-posts.
    retry = app.submit_order_event(
        app.scope,
        _event(
            "ex08-terminal", "ORD-EX08", "observation", T3,
            order_status="terminal", final_cumulative_fill_qty=150, cancelled_qty=250,
        ),
    )
    assert retry.status == "POSTED" and retry.receipt_id == terminal.receipt_id
    assert app.workbench(app.scope).account.cash_occupied == Decimal("605.00")

    again = _reopen(tmp_path)
    assert again.rebuild_matches(again.scope)
    account2 = again.workbench(again.scope).account
    assert account2.cash_occupied == Decimal("605.00")
    assert account2.cash_available_for_new.value == Decimal("18195.00")


def test_ex08_missing_final_cumulative_keeps_last_provable_hold(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _ex08_setup(app)
    app.submit_order_event(
        app.scope,
        _event(
            "ex08-open", "ORD-EX08", "observation", T0,
            order_status="in_progress", side="buy", security_code="600000",
            accepted_total_qty=400, price_bound=Decimal("12.00"),
            fee_upper_bound=Decimal("5.00"),
        ),
    )
    app.submit_order_event(
        app.scope,
        _event(
            "ex08-fill-100", "ORD-EX08", "fill", T1,
            side="buy", security_code="600000", quantity=100, price=Decimal("12.00"),
            confirmed_fee=Decimal("0.00"),
        ),
    )
    app.submit_order_event(
        app.scope,
        _event(
            "ex08-terminal", "ORD-EX08", "observation", T3,
            order_status="terminal", final_cumulative_fill_qty=None, cancelled_qty=None,
        ),
    )
    account = app.workbench(app.scope).account
    # The missing final count keeps the whole unconfirmed remainder held.
    assert account.cash_occupied == Decimal("300") * Decimal("12.00") + Decimal("5.00")
    assert account.reconciliation_status == "NEEDS_RECONCILIATION"
    order = next(o for o in app.records(app.scope).orders if o.order_id == "ORD-EX08")
    assert order.needs_reconciliation is True


# ---- repair-01 regressions ------------------------------------------------------


def test_ex02_settlement_waits_for_fee_and_dedupes_same_total(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _ex02_setup(app)
    app.submit_order_event(
        app.scope,
        _event(
            "s1", "ORD-EX02", "fill", T1,
            side="sell", security_code="600000", quantity=600, price=Decimal("10.07"),
            confirmed_fee=Decimal("0.00"), accepted_total_qty=600,
            price_bound=Decimal("10.10"),
        ),
    )
    app.submit_order_event(
        app.scope,
        _event(
            "st", "ORD-EX02", "observation", T2,
            order_status="terminal", final_cumulative_fill_qty=600, cancelled_qty=0,
        ),
    )
    # Terminal alone does not settle: the confirmed whole-order fee is still due.
    order = next(o for o in app.records(app.scope).orders if o.order_id == "ORD-EX02")
    assert order.settlement_ready is False

    fee = app.submit_order_event(
        app.scope,
        _event("sf", "ORD-EX02", "fee", T3, confirmed_fee=Decimal("6.00")),
    )
    assert fee.status == "POSTED"
    order = next(o for o in app.records(app.scope).orders if o.order_id == "ORD-EX02")
    assert order.settlement_ready is True
    cash_after_fee = app.workbench(app.scope).account.cash_total

    # The same whole-order total under a new form number does not charge again.
    dup = app.submit_order_event(
        app.scope,
        _event("sf2", "ORD-EX02", "fee", T3, confirmed_fee=Decimal("6.00")),
    )
    assert dup.status == "POSTED"
    assert app.workbench(app.scope).account.cash_total == cash_after_fee
    assert len([e for e in app.records(app.scope).events if e.kind == "fee"]) == 1


def test_ex08_late_stale_observation_keeps_terminal_hold(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _ex08_setup(app)
    open_event = dict(
        order_status="in_progress", side="buy", security_code="600000",
        accepted_total_qty=400, price_bound=Decimal("12.00"),
        fee_upper_bound=Decimal("5.00"),
    )
    app.submit_order_event(
        app.scope, _event("bo", "ORD-EX08", "observation", T0, **open_event)
    )
    app.submit_order_event(
        app.scope,
        _event(
            "b1", "ORD-EX08", "fill", T1,
            side="buy", security_code="600000", quantity=100, price=Decimal("12.00"),
            confirmed_fee=Decimal("0.00"),
        ),
    )
    app.submit_order_event(
        app.scope,
        _event(
            "bt", "ORD-EX08", "observation", T3,
            order_status="terminal", final_cumulative_fill_qty=150, cancelled_qty=250,
        ),
    )
    account = app.workbench(app.scope).account
    assert account.cash_occupied == Decimal("605.00")

    # A stale in_progress observation arriving after the terminal stays history.
    late = app.submit_order_event(
        app.scope, _event("bl", "ORD-EX08", "observation", T0, **open_event)
    )
    assert late.status == "POSTED"
    account = app.workbench(app.scope).account
    assert account.cash_occupied == Decimal("605.00")
    assert account.cash_available_for_new.value == Decimal("18195.00")
    order = next(o for o in app.records(app.scope).orders if o.order_id == "ORD-EX08")
    assert order.status == "terminal"
    assert order.unreported_fill_qty == 50


def test_ex08_rebuild_restores_order_hold_from_events(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _ex08_setup(app)
    app.submit_order_event(
        app.scope,
        _event(
            "bo", "ORD-EX08", "observation", T0,
            order_status="in_progress", side="buy", security_code="600000",
            accepted_total_qty=400, price_bound=Decimal("12.00"),
            fee_upper_bound=Decimal("5.00"),
        ),
    )
    app.submit_order_event(
        app.scope,
        _event(
            "b1", "ORD-EX08", "fill", T1,
            side="buy", security_code="600000", quantity=100, price=Decimal("12.00"),
            confirmed_fee=Decimal("0.00"),
        ),
    )
    app.submit_order_event(
        app.scope,
        _event(
            "bt", "ORD-EX08", "observation", T3,
            order_status="terminal", final_cumulative_fill_qty=150, cancelled_qty=250,
        ),
    )
    # Drop only the derived order projection; baseline, inbox and ledger are intact.
    store = tmp_path / STORE_NAME
    with __import__("sqlite3").connect(store) as conn:
        conn.execute("DELETE FROM order_states WHERE order_id=?", ("ORD-EX08",))
        conn.commit()

    again = _reopen(tmp_path)
    assert again.rebuild_matches(again.scope)
    account = again.workbench(again.scope).account
    assert account.cash_occupied == Decimal("605.00")
    assert account.cash_available_for_new.value == Decimal("18195.00")
    order = next(o for o in again.records(again.scope).orders if o.order_id == "ORD-EX08")
    assert order.status == "terminal"
    assert order.unreported_fill_qty == 50
    assert order.hold_amount == Decimal("605.00")


# ---- repair-02 regressions ------------------------------------------------------


def test_ex02_revised_fee_total_applies_only_the_delta(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _ex02_setup(app)
    for report_id, at, qty, px in (
        ("s1", T1, 200, "10.00"),
        ("s2", T2, 400, "10.10"),
    ):
        app.submit_order_event(
            app.scope,
            _event(
                report_id, "ORD-EX02", "fill", at,
                side="sell", security_code="600000", quantity=qty, price=Decimal(px),
                confirmed_fee=Decimal("0.00"), accepted_total_qty=600,
                price_bound=Decimal("10.10"),
            ),
        )
    app.submit_order_event(
        app.scope,
        _event("st", "ORD-EX02", "observation", T3,
               order_status="terminal", final_cumulative_fill_qty=600, cancelled_qty=0),
    )
    app.submit_order_event(
        app.scope, _event("f6", "ORD-EX02", "fee", T4, confirmed_fee=Decimal("6.00"))
    )
    assert app.workbench(app.scope).account.cash_total == Decimal("6034.00")

    # Restating the whole-order total to 7 only charges the 1 difference.
    revised = app.submit_order_event(
        app.scope,
        _event("f7", "ORD-EX02", "fee", T4, confirmed_fee=Decimal("7.00")),
    )
    assert revised.status == "POSTED"
    assert app.workbench(app.scope).account.cash_total == Decimal("6033.00")


def test_ex02_confirmed_zero_fee_still_settles(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _ex02_setup(app)
    app.submit_order_event(
        app.scope,
        _event(
            "s1", "ORD-EX02", "fill", T1,
            side="sell", security_code="600000", quantity=600, price=Decimal("10.07"),
            confirmed_fee=Decimal("0.00"), accepted_total_qty=600,
            price_bound=Decimal("10.10"),
        ),
    )
    app.submit_order_event(
        app.scope,
        _event("st", "ORD-EX02", "observation", T2,
               order_status="terminal", final_cumulative_fill_qty=600, cancelled_qty=0),
    )
    order = next(o for o in app.records(app.scope).orders if o.order_id == "ORD-EX02")
    assert order.settlement_ready is False  # fee not yet confirmed

    app.submit_order_event(
        app.scope, _event("fz", "ORD-EX02", "fee", T3, confirmed_fee=Decimal("0.00"))
    )
    order = next(o for o in app.records(app.scope).orders if o.order_id == "ORD-EX02")
    # An explicitly confirmed zero is still a confirmed fee, distinct from unknown.
    assert order.settlement_ready is True
    assert order.fee_confirmed_total == Decimal("0.00")


def test_ex08_earlier_terminal_late_keeps_confirmed_state(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _ex08_setup(app)
    app.submit_order_event(
        app.scope,
        _event("bo", "ORD-EX08", "observation", T0,
               order_status="in_progress", side="buy", security_code="600000",
               accepted_total_qty=400, price_bound=Decimal("12.00"),
               fee_upper_bound=Decimal("5.00")),
    )
    app.submit_order_event(
        app.scope,
        _event("b1", "ORD-EX08", "fill", T1,
               side="buy", security_code="600000", quantity=100, price=Decimal("12.00"),
               confirmed_fee=Decimal("0.00")),
    )
    app.submit_order_event(
        app.scope,
        _event("bt", "ORD-EX08", "observation", T3,
               order_status="terminal", final_cumulative_fill_qty=150, cancelled_qty=250),
    )
    assert app.workbench(app.scope).account.cash_occupied == Decimal("605.00")

    # An earlier-dated terminal arriving late keeps the proven 150/250 state.
    old = app.submit_order_event(
        app.scope,
        _event("old", "ORD-EX08", "observation", T2,
               order_status="terminal", final_cumulative_fill_qty=100, cancelled_qty=300),
    )
    assert old.status == "POSTED"
    account = app.workbench(app.scope).account
    assert account.cash_occupied == Decimal("605.00")
    assert account.cash_available_for_new.value == Decimal("18195.00")
    order = next(o for o in app.records(app.scope).orders if o.order_id == "ORD-EX08")
    assert order.unreported_fill_qty == 50


def test_ex02_missing_fee_projection_restored_on_reopen(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _ex02_setup(app)
    for report_id, at, qty, px in (
        ("s1", T1, 200, "10.00"),
        ("s2", T2, 400, "10.10"),
    ):
        app.submit_order_event(
            app.scope,
            _event(
                report_id, "ORD-EX02", "fill", at,
                side="sell", security_code="600000", quantity=qty, price=Decimal(px),
                confirmed_fee=Decimal("0.00"), accepted_total_qty=600,
                price_bound=Decimal("10.10"),
            ),
        )
    app.submit_order_event(
        app.scope,
        _event("st", "ORD-EX02", "observation", T3,
               order_status="terminal", final_cumulative_fill_qty=600, cancelled_qty=0),
    )
    app.submit_order_event(
        app.scope, _event("f6", "ORD-EX02", "fee", T4, confirmed_fee=Decimal("6.00"))
    )
    # Drop only the derived fee projection; order row, fee event and ledger stay.
    with __import__("sqlite3").connect(tmp_path / STORE_NAME) as conn:
        conn.execute("DELETE FROM order_fees")
        conn.commit()

    again = _reopen(tmp_path)
    assert again.rebuild_matches(again.scope)
    account = again.workbench(again.scope).account
    assert account.cash_total == Decimal("6034.00")
    order = next(o for o in again.records(again.scope).orders if o.order_id == "ORD-EX02")
    assert order.settlement_ready is True
    assert order.fee_confirmed_total == Decimal("6.00")

    # Re-confirming the same fee after recovery stays one effective charge.
    dup = again.submit_order_event(
        again.scope,
        _event("f6b", "ORD-EX02", "fee", T4, confirmed_fee=Decimal("6.00")),
    )
    assert dup.status == "POSTED"
    assert again.workbench(again.scope).account.cash_total == Decimal("6034.00")

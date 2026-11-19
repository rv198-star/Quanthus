"""P12-SWE2-FUNDING-TRUTH-R14: attributable cash and security occupancy.

One security can only serve one live bucket at a time, while an unbound
account's cash is shown for review rather than as a stale snapshot.  A bucket
holding its position keeps concrete cash and locked budget on the page while
underfunded and next-cycle amounts wait for settlement; a closed leftover
binding no longer occupies its security.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.platform.adapters.p12_synthetic_fixture import load_synthetic_fixture
from quanthus.platform.p12.assembly import STORE_NAME, synthetic_applications
from quanthus.product_daily.application.desktop import SubmitOrderEvent
from quanthus.product_daily.funding import SettlementRequest
from quanthus.shared.fact_contracts import FactError

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "config/p12/synthetic-desktop.v1.json"
ACCOUNT = "SYN-P12-A"
B1, B2 = "bucket-01", "bucket-02"
SEC_A, SEC_B = "600000", "000001"
NOW = datetime.fromisoformat("2026-11-24T16:00:00+08:00")


def _app(state: Path):
    return synthetic_applications(
        state_dir=state, fixture=load_synthetic_fixture(FIXTURE), clock=lambda: NOW
    )[ACCOUNT]


def _trade(app, order, security, side, qty, price, day):
    events = (
        ("fill", {
            "security_code": security, "side": side, "quantity": qty,
            "price": Decimal(price), "confirmed_fee": Decimal("0.00"),
            "accepted_total_qty": qty, "price_bound": Decimal(price),
        }),
        ("observation", {
            "order_status": "terminal", "final_cumulative_fill_qty": qty,
            "cancelled_qty": 0,
        }),
        ("fee", {"confirmed_fee": Decimal("0.00")}),
    )
    for index, (kind, fields) in enumerate(events):
        occurred = datetime.fromisoformat(
            f"2026-11-{day:02d}T09:{31 + index:02d}:00+08:00"
        )
        outcome = app.submit_order_event(
            app.scope,
            SubmitOrderEvent(f"{order}-{kind}", kind, order, occurred, **fields),
        )
        assert outcome.status == "POSTED", (order, kind, outcome.status)


def _page(app):
    return app.funding_rules(app.scope).funding


def _by_id(page):
    return {b.bucket_id: b for b in page.buckets}


def _reserve(app, bucket, cycle):
    assert app.prepare_funding_cycle(
        app.scope, bucket_id=bucket, cycle_id=cycle
    ).accepted


def _assign(app, bucket, security):
    state, _ = app.funding_state(app.scope)
    return app.bind_funding(app.scope, state, bucket_securities={bucket: security})


def _close(app, bucket, cycle, sec, sid):
    evidence = app.funding_settlement_evidence(app.scope, {sec})
    assert evidence.settlement_ready
    return app.settle_funding_cycle(
        app.scope,
        settlement_id=sid,
        requests=(SettlementRequest(f"req-{sid}", bucket, cycle, evidence),),
        close_bucket_ids=(bucket,),
    )


# ---- unbound account ---------------------------------------------------------


def test_unbound_account_marks_cash_for_review(tmp_path: Path) -> None:
    """A ledger movement without a bound partition marks every bucket amount
    for review instead of showing the opening snapshot as current."""
    app = _app(tmp_path)
    _trade(app, "unassigned-buy", SEC_A, "buy", 100, "10.00", 19)
    page = _page(app)
    assert page.status_reason == "FUNDING_CYCLE_NOT_BOUND"
    assert page.reserve.value is None
    for bucket in page.buckets:
        assert bucket.cash.value is None
        assert bucket.locked_cycle_budget.value is None
    reopened = _page(_app(tmp_path))
    assert reopened.status_reason == "FUNDING_CYCLE_NOT_BOUND"


# ---- overlapping live buckets ------------------------------------------------


def test_security_cannot_serve_two_live_buckets(tmp_path: Path) -> None:
    """Two live cycles cannot claim the same security."""
    app = _app(tmp_path)
    _reserve(app, B1, "first-id")
    _assign(app, B1, SEC_A)
    _reserve(app, B2, "different-id")
    with pytest.raises(FactError) as error:
        _assign(app, B2, SEC_A)
    assert error.value.reason_code == "FUNDING_ATTRIBUTION_UNKNOWN"


def test_successive_bucket_owners_share_one_security(tmp_path: Path) -> None:
    """After B1's cycle settles, B2 may trade and settle the same security."""
    app = _app(tmp_path)
    _reserve(app, B1, "first-id")
    _assign(app, B1, SEC_A)
    _trade(app, "old-buy", SEC_A, "buy", 100, "10.00", 19)
    _trade(app, "old-sell", SEC_A, "sell", 100, "11.00", 20)
    assert _close(app, B1, "first-id", SEC_A, "s1").accepted

    _reserve(app, B2, "different-id")
    _assign(app, B2, SEC_A)
    _trade(app, "new-buy", SEC_A, "buy", 100, "10.00", 23)
    _trade(app, "new-sell", SEC_A, "sell", 100, "9.00", 24)
    decision = _close(app, B2, "different-id", SEC_A, "s2")
    assert decision.accepted
    reopened = _cash(_app(tmp_path))
    assert reopened["account"] == reopened["partition"] == Decimal("10000.00")


def _cash(app):
    raw, _ = app.funding_state(app.scope)
    account = app.workbench(app.scope).account
    return {"account": account.cash_total, "partition": raw.cash_total}


# ---- legacy upgrade ----------------------------------------------------------


def test_live_holding_blocks_retarget(tmp_path: Path) -> None:
    """A bucket still holding its security cannot be retargeted."""
    app = _app(tmp_path)
    _reserve(app, B1, "already-live")
    _assign(app, B1, SEC_A)
    _trade(app, "prior-buy", SEC_A, "buy", 100, "10.00", 19)
    with pytest.raises(FactError) as error:
        _assign(app, B1, SEC_B)
    assert error.value.reason_code == "FUNDING_ATTRIBUTION_UNKNOWN"
    # The original attribution and position are untouched.
    page = _page(_app(tmp_path))
    by_id = _by_id(page)
    assert by_id[B1].cycle_id == "already-live"
    assert by_id[B1].cash.value == Decimal("4000.00")


# ---- open-position page semantics ---------------------------------------------


def test_open_position_shows_cash_but_waits_on_settlement(tmp_path: Path) -> None:
    """A holding bucket shows its explainable cash and locked budget while
    underfunded and next-cycle amounts wait for settlement evidence."""
    app = _app(tmp_path)
    _reserve(app, B1, "opp")
    _assign(app, B1, SEC_A)
    _trade(app, "hold-buy", SEC_A, "buy", 100, "10.00", 19)
    page = _page(app)
    by_id = _by_id(page)
    assert by_id[B1].cash.value == Decimal("4000.00")
    assert by_id[B1].locked_cycle_budget.value == Decimal("5000.00")
    assert by_id[B1].underfunded.value is None
    assert by_id[B1].next_available.value is None
    assert by_id[B2].cash.value == Decimal("5000.00")
    assert by_id[B2].underfunded.value == Decimal("0.00")
    assert by_id[B2].next_available.value == Decimal("5000.00")


# ---- legacy store without cycle markers (created by an older release) -------


def _strip_cycle_markers(state: Path) -> None:
    """Drop the cycle-tracking fields an older release never wrote."""
    import json
    import sqlite3

    with sqlite3.connect(state / STORE_NAME) as db:
        raw = db.execute(
            "SELECT bindings_json FROM funding_states WHERE account_id=?",
            (ACCOUNT,),
        ).fetchone()[0]
        bindings = json.loads(raw)
        bindings.pop("bucket_security_cycles", None)
        db.execute(
            "UPDATE funding_states SET bindings_json=? WHERE account_id=?",
            (json.dumps(bindings), ACCOUNT),
        )


def test_legacy_holding_retarget_is_refused(tmp_path: Path) -> None:
    """A store written before cycle tracking still occupies its security.

    A live bucket holding its position cannot be retargeted to a different
    security after the upgrade; refusing leaves the original attribution,
    balances and holdings untouched instead of a later ledger mismatch.
    """
    import sqlite3

    app = _app(tmp_path)
    _reserve(app, B1, "already-live")
    _assign(app, B1, SEC_A)
    _trade(app, "prior-buy", SEC_A, "buy", 100, "10.00", 19)
    _strip_cycle_markers(tmp_path)

    # Reopen through the current adapter, then attempt the retarget.
    app = _app(tmp_path)
    with pytest.raises(FactError) as error:
        _assign(app, B1, SEC_B)
    assert error.value.reason_code == "FUNDING_ATTRIBUTION_UNKNOWN"

    reopened = _app(tmp_path)
    page = _page(reopened)
    assert page.status_reason != "FUNDING_LEDGER_MISMATCH"
    by_id = _by_id(page)
    assert by_id[B1].cycle_id == "already-live"
    assert by_id[B1].cash.value == Decimal("4000.00")
    account = reopened.workbench(reopened.scope).account
    assert account.cash_total == Decimal("9000.00")
    with sqlite3.connect(tmp_path / STORE_NAME) as db:
        bindings = db.execute(
            "SELECT bindings_json FROM funding_states WHERE account_id=?",
            (ACCOUNT,),
        ).fetchone()[0]
    assert '"600000"' in bindings and '"000001"' not in bindings


def test_legacy_settled_cycle_frees_retarget(tmp_path: Path) -> None:
    """A legacy binding only occupies the cycle it served.

    After the bucket settles that cycle, a new reservation may bind a
    different security instead of staying locked to the old one.
    """
    app = _app(tmp_path)
    _reserve(app, B1, "already-live")
    _assign(app, B1, SEC_A)
    _trade(app, "prior-buy", SEC_A, "buy", 100, "10.00", 19)
    _trade(app, "prior-sell", SEC_A, "sell", 100, "11.00", 20)
    assert _close(app, B1, "already-live", SEC_A, "close-already-live").accepted
    _strip_cycle_markers(tmp_path)

    app = _app(tmp_path)
    _reserve(app, B1, "next-cycle")
    _assign(app, B1, SEC_B)  # no raise
    _trade(app, "next-buy", SEC_B, "buy", 100, "10.00", 23)
    _trade(app, "next-sell", SEC_B, "sell", 100, "9.00", 24)
    assert _close(app, B1, "next-cycle", SEC_B, "close-next-cycle").accepted
    reopened = _cash(_app(tmp_path))
    assert reopened["account"] == reopened["partition"] == Decimal("10000.00")


def test_legacy_holding_settle_then_retarget(tmp_path: Path) -> None:
    """An upgraded holding finishes its old cycle, then trades a new security.

    The in-cycle retarget is still refused; after the bucket settles, its
    next reservation accepts the new security and settles it cleanly.
    """
    app = _app(tmp_path)
    _reserve(app, B1, "already-live")
    _assign(app, B1, SEC_A)
    _trade(app, "prior-buy", SEC_A, "buy", 100, "10.00", 19)
    _strip_cycle_markers(tmp_path)

    app = _app(tmp_path)
    with pytest.raises(FactError) as error:
        _assign(app, B1, SEC_B)
    assert error.value.reason_code == "FUNDING_ATTRIBUTION_UNKNOWN"

    _trade(app, "legacy-sell", SEC_A, "sell", 100, "11.00", 20)
    assert _close(app, B1, "already-live", SEC_A, "close-already-live").accepted
    _reserve(app, B1, "next-cycle")
    _assign(app, B1, SEC_B)
    _trade(app, "next-buy", SEC_B, "buy", 100, "10.00", 23)
    _trade(app, "next-sell", SEC_B, "sell", 100, "9.00", 24)
    assert _close(app, B1, "next-cycle", SEC_B, "close-next-cycle").accepted
    reopened = _cash(_app(tmp_path))
    assert reopened["account"] == reopened["partition"] == Decimal("10000.00")


def test_legacy_second_cycle_same_security_held(tmp_path: Path) -> None:
    """A legacy binding re-trading its security inside the open cycle still
    occupies it.

    When the bucket settled cycle-1 and re-bound the same security in
    cycle-2, the missing marker is inferred to the open cycle: the
    in-cycle retarget is refused and a later settlement counts those
    proceeds instead of reporting a ledger mismatch.
    """
    app = _app(tmp_path)
    _reserve(app, B1, "cycle-1")
    _assign(app, B1, SEC_A)
    _trade(app, "c1-buy", SEC_A, "buy", 100, "10.00", 19)
    _trade(app, "c1-sell", SEC_A, "sell", 100, "11.00", 20)
    assert _close(app, B1, "cycle-1", SEC_A, "close-cycle-1").accepted
    _reserve(app, B1, "cycle-2")
    _assign(app, B1, SEC_A)
    _trade(app, "c2-buy", SEC_A, "buy", 100, "10.00", 23)
    _strip_cycle_markers(tmp_path)

    app = _app(tmp_path)
    page = _page(app)
    assert page.status_reason != "FUNDING_LEDGER_MISMATCH"
    assert _by_id(page)[B1].cash.value == Decimal("4000.00")
    with pytest.raises(FactError) as error:
        _assign(app, B1, SEC_B)
    assert error.value.reason_code == "FUNDING_ATTRIBUTION_UNKNOWN"

    _trade(app, "c2-sell", SEC_A, "sell", 100, "9.00", 24)
    assert _close(app, B1, "cycle-2", SEC_A, "close-cycle-2").accepted
    reopened = _cash(_app(tmp_path))
    assert reopened["account"] == reopened["partition"] == Decimal("10000.00")


def test_legacy_second_cycle_same_security_unsettled(tmp_path: Path) -> None:
    """The same legacy holder with fills already settled is accepted.

    The bought-and-sold cycle-2 under the markerless binding closes
    cleanly and leaves the partition equal to the ledger.
    """
    app = _app(tmp_path)
    _reserve(app, B1, "cycle-1")
    _assign(app, B1, SEC_A)
    _trade(app, "c1-buy", SEC_A, "buy", 100, "10.00", 19)
    _trade(app, "c1-sell", SEC_A, "sell", 100, "11.00", 20)
    assert _close(app, B1, "cycle-1", SEC_A, "close-cycle-1").accepted
    _reserve(app, B1, "cycle-2")
    _assign(app, B1, SEC_A)
    _trade(app, "c2-buy", SEC_A, "buy", 100, "10.00", 23)
    _trade(app, "c2-sell", SEC_A, "sell", 100, "9.00", 24)
    _strip_cycle_markers(tmp_path)

    app = _app(tmp_path)
    page = _page(app)
    assert page.status_reason != "FUNDING_LEDGER_MISMATCH"
    assert _close(app, B1, "cycle-2", SEC_A, "close-cycle-2").accepted
    reopened = _cash(_app(tmp_path))
    assert reopened["account"] == reopened["partition"] == Decimal("10000.00")

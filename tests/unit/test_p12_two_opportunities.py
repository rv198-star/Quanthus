"""P12-SWE2-TWO-OPPORTUNITIES-R13: one bucket serving two opportunities.

The same 5,000 bucket wins 100 on 600000 in its first cycle, then switches to
000001 and loses 100 in its second.  The shared reserve returns to zero, both
buckets hold 5,000 again and the account is back at 10,000.  Attribution is
cycle-scoped: a binding recorded before the second reservation or after it
both serve the new opportunity, a retarget inside one live cycle is still
refused, and while the second position is open the page shows the amount the
bucket can already explain instead of "unknown".
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.capabilities.funding import BucketPhase, initial_funding_state
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
# A named synthetic clock covering both trading windows (Nov 19-23).
NOW = datetime.fromisoformat("2026-11-24T16:00:00+08:00")


def _app(state: Path):
    return synthetic_applications(
        state_dir=state, fixture=load_synthetic_fixture(FIXTURE), clock=lambda: NOW
    )[ACCOUNT]


def _initial(app):
    opening = app.workbench(app.scope).account.cash_total
    return opening, initial_funding_state(app._policy.funding, opening)


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


def _cash(app):
    raw, _ = app.funding_state(app.scope)
    page = app.funding_rules(app.scope).funding
    account = app.workbench(app.scope).account
    return {
        "account": account.cash_total,
        "partition": raw.cash_total,
        "reserve": raw.reserve,
        "view_reason": page.status_reason,
        "buckets": {b.bucket_id: b for b in raw.buckets},
    }


def _page(app):
    return app.funding_rules(app.scope).funding


def _settle(app, bucket, cycle, sec, settlement):
    evidence = app.funding_settlement_evidence(app.scope, {sec})
    assert evidence.settlement_ready
    request = SettlementRequest(f"req-{settlement}", bucket, cycle, evidence)
    return app.settle_funding_cycle(
        app.scope,
        settlement_id=settlement,
        requests=(request,),
        close_bucket_ids=(bucket,),
    )


def _settled(app, bucket, cycle):
    """A complete order flow settles its bucket on the write path, so no
    manual settle_funding call is needed once the facts are complete."""
    state, _ = app.funding_state(app.scope)
    item = next(b for b in state.buckets if b.bucket_id == bucket)
    assert item.phase is BucketPhase.CLOSED and item.cycle_id == cycle
    return item


def _first_win(app):
    """Reserve U1 on 600000, win 100, close the cycle."""
    _, partition = _initial(app)
    app.bind_funding(app.scope, partition, bucket_securities={B1: SEC_A})
    assert app.prepare_funding_cycle(
        app.scope, bucket_id=B1, cycle_id="first-opportunity"
    ).accepted
    _trade(app, "A-BUY", SEC_A, "buy", 100, "10.00", 19)
    _trade(app, "A-SELL", SEC_A, "sell", 100, "11.00", 20)
    _settled(app, B1, "first-opportunity")


# ---- the audited two-opportunity flow ----------------------------------------


@pytest.mark.parametrize("assign_before", [True, False])
def test_second_opportunity_new_security(tmp_path: Path, assign_before: bool) -> None:
    """U1 wins 100 on SEC_A, then reserves a second cycle on SEC_B and loses
    100.  Attribution may be bound before or after the reservation."""
    app = _app(tmp_path)
    _first_win(app)
    after_first = _cash(_app(tmp_path))
    assert after_first["account"] == after_first["partition"] == Decimal("10100.00")
    assert after_first["reserve"] == Decimal("100.00")

    app = _app(tmp_path)
    if assign_before:
        state, _ = app.funding_state(app.scope)
        app.bind_funding(app.scope, state, bucket_securities={B1: SEC_B})
    assert app.prepare_funding_cycle(
        app.scope, bucket_id=B1, cycle_id="second-opportunity"
    ).accepted
    if not assign_before:
        state, _ = app.funding_state(app.scope)
        app.bind_funding(app.scope, state, bucket_securities={B1: SEC_B})

    _trade(app, "B-BUY", SEC_B, "buy", 100, "10.00", 21)
    _trade(app, "B-SELL", SEC_B, "sell", 100, "9.00", 23)
    _settled(app, B1, "second-opportunity")

    reopened = _cash(_app(tmp_path))
    buckets = reopened["buckets"]
    assert reopened["account"] == reopened["partition"] == Decimal("10000.00")
    assert reopened["reserve"] == Decimal("0.00")
    assert buckets[B1].cash == buckets[B2].cash == Decimal("5000.00")
    assert buckets[B1].phase.name == "CLOSED"
    assert reopened["view_reason"] != "FUNDING_LEDGER_MISMATCH"


def test_mid_cycle_retarget_is_refused(tmp_path: Path) -> None:
    """Inside one live cycle the bucket cannot switch its security."""
    app = _app(tmp_path)
    _, partition = _initial(app)
    app.bind_funding(app.scope, partition, bucket_securities={B1: SEC_A})
    app.prepare_funding_cycle(app.scope, bucket_id=B1, cycle_id="first-opportunity")
    state, _ = app.funding_state(app.scope)
    with pytest.raises(FactError) as error:
        app.bind_funding(app.scope, state, bucket_securities={B1: SEC_B})
    assert error.value.reason_code == "FUNDING_ATTRIBUTION_UNKNOWN"
    # Repeating the same attribution inside the cycle is a no-op.
    app.bind_funding(app.scope, state, bucket_securities={B1: SEC_A})


def test_open_position_shows_explainable_amounts(tmp_path: Path) -> None:
    """After the buy the page shows the bucket's explainable cash, the
    reserve and the locked budget instead of every amount unknown."""
    app = _app(tmp_path)
    _, partition = _initial(app)
    app.bind_funding(app.scope, partition, bucket_securities={B1: SEC_A})
    app.prepare_funding_cycle(app.scope, bucket_id=B1, cycle_id="first-opportunity")
    _trade(app, "A-BUY", SEC_A, "buy", 100, "10.00", 19)

    page = _page(app)
    assert page.status_reason != "FUNDING_LEDGER_MISMATCH"
    by_id = {b.bucket_id: b for b in page.buckets}
    # U1 spent 1,000 of its 5,000 on the position; the rest is explainable.
    assert by_id[B1].cash.value == Decimal("4000.00")
    assert by_id[B1].locked_cycle_budget.value == Decimal("5000.00")
    assert by_id[B2].cash.value == Decimal("5000.00")
    assert page.reserve.value == Decimal("0.00")
    assert page.bound_account_revision is not None


def test_settlement_replays_first_receipt(tmp_path: Path) -> None:
    """A repeated settlement id returns the recorded first-cycle receipt
    instead of settling the newer reserved cycle."""
    app = _app(tmp_path)
    _first_win(app)
    app = _app(tmp_path)
    app.prepare_funding_cycle(app.scope, bucket_id=B1, cycle_id="second-opportunity")
    state, _ = app.funding_state(app.scope)
    app.bind_funding(app.scope, state, bucket_securities={B1: SEC_B})
    # The first cycle already settled on the write path; re-issuing a manual
    # settlement for the same completed cycle is refused and moves no cash.
    replay = _settle(app, B1, "first-opportunity", SEC_A, "settle-first")
    assert not replay.accepted
    assert _cash(app)["account"] == Decimal("10100.00")


def test_second_cycle_attribution_persisted(tmp_path: Path) -> None:
    """The second cycle's security binding survives a reopen."""
    app = _app(tmp_path)
    _first_win(app)
    app = _app(tmp_path)
    app.prepare_funding_cycle(app.scope, bucket_id=B1, cycle_id="second-opportunity")
    state, _ = app.funding_state(app.scope)
    app.bind_funding(app.scope, state, bucket_securities={B1: SEC_B})
    with sqlite3.connect(tmp_path / STORE_NAME) as db:
        row = db.execute(
            "SELECT bindings_json FROM funding_states WHERE account_id=?",
            (ACCOUNT,),
        ).fetchone()
    assert row is not None
    assert SEC_B in row[0] and "second-opportunity" in row[0]

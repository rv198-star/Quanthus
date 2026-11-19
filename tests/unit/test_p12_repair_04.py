"""P12-SWE2-FIRST-CYCLE-20261119-R12: first reservation to first profitable close.

A cash-only synthetic account reserves bucket-01, buys 100 shares at 10.00 and
sells them at 11.00 the next day.  Settlement folds the 100 profit into the
shared reserve: both buckets keep 5,000, the reserve is 100 and the partition
again equals the account ledger at 10,100.  The reservation must work whether
the security attribution is bound up front or supplied afterwards through the
public bind_funding call, and an explainable ledger movement must not be shown
on the page as a mismatch.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.capabilities.funding import initial_funding_state
from quanthus.platform.adapters.p12_synthetic_fixture import load_synthetic_fixture
from quanthus.platform.p12.assembly import STORE_NAME, synthetic_applications
from quanthus.product_daily.application.desktop import SubmitOrderEvent
from quanthus.product_daily.funding import SettlementRequest
from quanthus.shared.fact_contracts import FactError

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "config/p12/synthetic-desktop.v1.json"
ACCOUNT = "SYN-P12-A"
B1, B2 = "bucket-01", "bucket-02"
SEC_A = "600000"
NOW = datetime.fromisoformat("2026-11-20T16:00:00+08:00")


def _app(state: Path, fixture: Path = FIXTURE):
    return synthetic_applications(
        state_dir=state, fixture=load_synthetic_fixture(fixture), clock=lambda: NOW
    )[ACCOUNT]


def _initial(app):
    """Partition the 10,000 baseline into the two configured 5,000 buckets."""
    opening = app.workbench(app.scope).account.cash_total
    return opening, initial_funding_state(app._policy.funding, opening)


def _trade(app, order: str, security: str, side: str, qty: int, price: str, day: int) -> None:
    """One accepted terminal order: fill, terminal observation, confirmed fee."""
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


def _cash(app) -> dict:
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


def _settle(app, bucket: str, cycle: str, settlement: str):
    evidence = app.funding_settlement_evidence(app.scope, {SEC_A})
    assert evidence.settlement_ready
    request = SettlementRequest(f"req-{settlement}", bucket, cycle, evidence)
    return app.settle_funding_cycle(
        app.scope,
        settlement_id=settlement,
        requests=(request,),
        close_bucket_ids=(bucket,),
    )


def _first_cycle(state: Path, assign_first: bool) -> dict:
    """Reserve B1, attribute SEC_A, buy then sell, close the cycle, reopen."""
    app = _app(state)
    opening, partition = _initial(app)
    if assign_first:
        app.bind_funding(app.scope, partition, bucket_securities={B1: SEC_A})
    prepared = app.prepare_funding_cycle(
        app.scope, bucket_id=B1, cycle_id="first-opportunity"
    )
    assert prepared.accepted
    if not assign_first:
        # The reservation auto-created the partition; the later binding only
        # folds the security attribution into the persisted row.
        persisted, _ = app.funding_state(app.scope)
        app.bind_funding(app.scope, persisted, bucket_securities={B1: SEC_A})
    _trade(app, "A-BUY", SEC_A, "buy", 100, "10.00", 19)
    after_buy = _cash(app)
    _trade(app, "A-SELL", SEC_A, "sell", 100, "11.00", 20)
    before_close = _cash(app)
    closed = _settle(app, B1, "first-opportunity", "close-first")
    reopened = _cash(_app(state))
    return {
        "opening": opening,
        "closed": closed,
        "after_buy": after_buy,
        "before_close": before_close,
        "reopened": reopened,
    }


def _assert_first_cycle_settled(result: dict) -> None:
    assert result["closed"].accepted
    reopened = result["reopened"]
    buckets = reopened["buckets"]
    assert reopened["partition"] == result["opening"] + Decimal("100.00")
    assert reopened["account"] == reopened["partition"]
    assert reopened["reserve"] == Decimal("100.00")
    assert buckets[B1].cash == buckets[B2].cash == Decimal("5000.00")
    assert buckets[B1].phase.name == "CLOSED"
    assert buckets[B2].phase.name == "EMPTY"
    assert reopened["view_reason"] != "FUNDING_LEDGER_MISMATCH"


# ---- the audited first cycle ------------------------------------------------


def test_first_cycle_assigned_before_reservation(tmp_path: Path) -> None:
    result = _first_cycle(tmp_path, assign_first=True)
    _assert_first_cycle_settled(result)
    # Explainable buy/sell movements are pending settlement, not mismatches.
    for snapshot in (result["after_buy"], result["before_close"]):
        assert snapshot["view_reason"] != "FUNDING_LEDGER_MISMATCH"


def test_first_cycle_attribution_after_reservation(tmp_path: Path) -> None:
    """The reservation auto-binds the partition; a later bind_funding call
    persists the bucket-security attribution instead of dropping it."""
    result = _first_cycle(tmp_path, assign_first=False)
    _assert_first_cycle_settled(result)
    # The attribution was merged into the persisted bindings, not just held
    # for one process.
    with sqlite3.connect(tmp_path / STORE_NAME) as db:
        row = db.execute(
            "SELECT bindings_json FROM funding_states WHERE account_id=?",
            (ACCOUNT,),
        ).fetchone()
    assert row is not None and SEC_A in row[0]


# ---- neighbouring refusals stay refused -------------------------------------


def test_prepare_refused_while_partition_trails_ledger(tmp_path: Path) -> None:
    """An older store whose buckets are short of the recorded cash still
    blocks a new reservation instead of spending money the ledger lacks."""
    app = _app(tmp_path)
    opening, partition = _initial(app)
    app.bind_funding(app.scope, partition, bucket_securities={B1: SEC_A})
    # A buy leaves the partition unchanged while the ledger drops by 1,000:
    # the persisted row now trails the admitted account cash.
    _trade(app, "A-BUY", SEC_A, "buy", 100, "10.00", 19)
    assert _cash(app)["account"] == opening - Decimal("1000.00")
    with pytest.raises(FactError) as error:
        app.prepare_funding_cycle(
            app.scope, bucket_id=B1, cycle_id="first-opportunity"
        )
    assert error.value.reason_code == "FUNDING_LEDGER_MISMATCH"


def test_conflicting_bucket_security_is_refused(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _, partition = _initial(app)
    app.bind_funding(app.scope, partition, bucket_securities={B1: SEC_A})
    with pytest.raises(FactError) as error:
        app.bind_funding(app.scope, partition, bucket_securities={B1: "000001"})
    assert error.value.reason_code == "FUNDING_ATTRIBUTION_UNKNOWN"


def test_page_reports_real_gap_as_mismatch(tmp_path: Path) -> None:
    """A partition genuinely short of the ledger is still flagged for review."""
    app = _app(tmp_path)
    opening, partition = _initial(app)
    app.bind_funding(app.scope, partition)
    # A buy on an unattributed security is cash the bound buckets cannot
    # explain, so the page reports the gap instead of a silent balance.
    _trade(app, "B-BUY", "000001", "buy", 40, "10.00", 19)
    assert _cash(app)["account"] == opening - Decimal("400.00")
    assert (
        app.funding_rules(app.scope).funding.status_reason
        == "FUNDING_LEDGER_MISMATCH"
    )

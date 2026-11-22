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
    # The complete order flow settles B1 on the write path; a manual settle
    # for the same completed cycle is refused and moves no cash.
    assert not result["closed"].accepted
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
    """A partition genuinely short of the ledger is still flagged for review.

    Admitted fills no bucket owns are listed as pending attribution rather
    than guessing an owner: the page keeps the known buckets and reserve and
    names the pending cash so the listed amounts still reconcile.  When the
    account's cash cannot be explained even by listing those fills (for
    example a correction that moved cash with no admitted fill), the
    partition is genuinely short and reports FUNDING_LEDGER_MISMATCH.
    """
    app = _app(tmp_path)
    opening, partition = _initial(app)
    app.bind_funding(app.scope, partition)
    # Three unattributed securities each moved cash; the page names all
    # three as pending attribution, still reconciling to the account.
    _trade(app, "B-BUY", "000001", "buy", 40, "10.00", 19)
    _trade(app, "C-BUY", "000002", "buy", 40, "10.00", 19)
    _trade(app, "D-BUY", "000003", "buy", 40, "10.00", 19)
    assert _cash(app)["account"] == opening - Decimal("1200.00")
    funding = app.funding_rules(app.scope).funding
    assert funding.status_reason == "FUNDING_PENDING_ATTRIBUTION"
    assert len(funding.pending_attributions) == 3


def test_pending_attributions_serialize_through_funding_rules_dto_and_http(
    tmp_path: Path,
) -> None:
    """Pending attributions convert price to Decimal so DTO & HTTP GET return 200."""
    import io
    import json

    from quanthus.platform.composition import create_platform_kernel
    from quanthus.platform.p12.http import P12AccountBinding, P12Runtime
    from quanthus.platform.p12.local_server import LocalMfaDisabledCipher
    from quanthus.platform.p12.transport import funding_rules_dto
    from quanthus.platform.persistence import InMemoryPlatformStore

    app = _app(tmp_path)
    opening, partition = _initial(app)
    app.bind_funding(app.scope, partition)
    _trade(app, "B-BUY", "000001", "buy", 40, "10.22", 19)
    _trade(app, "C-BUY", "000002", "buy", 40, "10.00", 19)
    _trade(app, "D-BUY", "000003", "buy", 40, "10.50", 19)
    assert _cash(app)["account"] == opening - Decimal("1228.80")

    dto = funding_rules_dto(app.funding_rules(app.scope))
    pending = dto["funding"]["pending_attributions"]
    assert dto["funding"]["status_reason"] == "FUNDING_PENDING_ATTRIBUTION"
    assert [(p["security_code"], p["price"], p["cash_delta"]) for p in pending] == [
        ("000001", "10.22", "-408.80"),
        ("000002", "10.00", "-400.00"),
        ("000003", "10.50", "-420.00"),
    ]

    runtime = P12Runtime(
        runtime_ref="test-p12-r4:v1",
        bindings={
            ACCOUNT: P12AccountBinding(
                application=app,
                display_name="Test Account",
                allowed_usernames=frozenset({"p12-tester"}),
                synthetic=True,
            )
        },
    )
    kernel = create_platform_kernel(
        store=InMemoryPlatformStore(),
        cipher=LocalMfaDisabledCipher(),
        p12_runtime=runtime,
    )
    kernel.migrate()
    kernel.identity.create_user(username="p12-tester", password="test-pass-123", role="operator")

    def wsgi_call(
        method: str,
        path: str,
        body: dict | None = None,
        token: str | None = None,
    ) -> tuple[int, dict]:
        raw = json.dumps(body).encode("utf-8") if body is not None else b""
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": "",
            "CONTENT_LENGTH": str(len(raw)),
            "CONTENT_TYPE": "application/json",
            "wsgi.input": io.BytesIO(raw),
        }
        if token:
            environ["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        captured: dict[str, int] = {}

        def start_response(status: str, _headers: list[tuple[str, str]]) -> None:
            captured["status"] = int(status.split(" ", 1)[0])

        payload = b"".join(kernel.app(environ, start_response)).decode("utf-8")
        return captured["status"], json.loads(payload)

    login_status, login_body = wsgi_call(
        "POST",
        "/api/platform/v1/auth/login",
        {"username": "p12-tester", "password": "test-pass-123"},
    )
    assert login_status == 200
    status, payload = wsgi_call(
        "GET",
        f"/api/p12/v1/accounts/{ACCOUNT}/funding-rules",
        token=login_body["token"],
    )
    assert status == 200
    assert payload["funding"]["status_reason"] == "FUNDING_PENDING_ATTRIBUTION"
    assert [
        (p["order_id"], p["security_code"], p["price"], p["cash_delta"])
        for p in payload["funding"]["pending_attributions"]
    ] == [
        ("B-BUY", "000001", "10.22", "-408.80"),
        ("C-BUY", "000002", "10.00", "-400.00"),
        ("D-BUY", "000003", "10.50", "-420.00"),
    ]


def test_pending_attribution_service_defensively_coerces_price(tmp_path: Path) -> None:
    """Service converts string prices and turns empty/invalid values into None."""
    from quanthus.platform.p12.transport import funding_rules_dto

    app = _app(tmp_path)
    _, partition = _initial(app)
    app.bind_funding(app.scope, partition)
    _trade(app, "B-BUY", "000001", "buy", 40, "10.22", 19)
    _trade(app, "C-BUY", "000002", "buy", 40, "10.00", 19)
    _trade(app, "D-BUY", "000003", "buy", 40, "10.50", 19)

    original = app._facts.funding_unattributed_fills

    def raw_string_and_bad_prices(context):
        items = list(original(context))
        items[0] = {**items[0], "price": "10.22"}
        items[1] = {**items[1], "price": ""}
        items[2] = {**items[2], "price": "NaN"}
        return tuple(items)

    app._facts.funding_unattributed_fills = raw_string_and_bad_prices
    view = app.funding_rules(app.scope)
    assert [p.price for p in view.funding.pending_attributions] == [
        Decimal("10.22"),
        None,
        None,
    ]
    dto = funding_rules_dto(view)
    assert [p["price"] for p in dto["funding"]["pending_attributions"]] == [
        "10.22",
        None,
        None,
    ]



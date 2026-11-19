"""P12 desktop use cases called directly, without HTTP or a frontend (AR01, AR04, AR05, AR06)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.capabilities.decision_access import validate_decision_set
from quanthus.platform.adapters.p12_synthetic_fixture import (
    FixtureDecisionSource,
    load_synthetic_fixture,
)
from quanthus.platform.p12.assembly import STORE_NAME, synthetic_applications
from quanthus.product_daily.application.desktop import CorrectFill, SubmitFill
from quanthus.shared.fact_contracts import FactError
from quanthus.shared.values import AccountScope

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "config/p12/synthetic-desktop.v1.json"
FILL_AT = datetime.fromisoformat("2026-11-19T09:31:00+08:00")


def _apps(tmp_path: Path, fixture_path: Path = FIXTURE, **kwargs):
    fixture = load_synthetic_fixture(fixture_path)
    return fixture, synthetic_applications(state_dir=tmp_path, fixture=fixture, **kwargs)


def _plan_id(app):
    return app.workbench(app.scope).package.plans[0].plan_id


def _fill(app, report_id="fill-1", **kw):
    return app.submit_fill(
        app.scope,
        SubmitFill(
            report_id,
            kw.get("plan_id", _plan_id(app)),
            kw.get("quantity", 400),
            Decimal(kw.get("price", "12.50")),
            Decimal(kw.get("fee", "5.00")),
            kw.get("occurred_at", FILL_AT),
        ),
    )


def _business_rows(path: Path):
    with sqlite3.connect(path) as db:
        return {
            table: db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            for table in ("accounts", "events", "postings", "facts", "outbox", "dependent_plans")
        }


def test_published_plan_is_sized_by_the_risk_capability(tmp_path: Path) -> None:
    _, apps = _apps(tmp_path)
    app = apps["SYN-P12-A"]
    view = app.workbench(app.scope)
    plan = view.package.plans[0]
    assert (plan.target_qty, plan.delta_qty, plan.price_cap) == (400, 400, Decimal("12.40"))
    assert plan.cash_bound == Decimal("4965.00") and plan.estimated_fee == Decimal("5.00")
    assert plan.primary_constraint == "BUDGET_CAPACITY"
    groups = [
        (o.opportunity_id, o.queue_group, o.action_code, o.status_code) for o in view.opportunities
    ]
    assert groups == [
        ("OPP-SYN-600000", "entry", "BUY", "READY"),
        ("OPP-SYN-000002", "watch", "WAIT", "WAITING"),
        ("OPP-SYN-300001", "watch", "PAUSED", "MISSING_INPUT"),
    ]
    assert view.account.cash_total == Decimal("10000.00")
    assert view.package.knowledge_cutoff < plan.eligible_at


def test_brief_journey_over_cap_fill_retry_correction_and_rebuild(tmp_path: Path) -> None:
    _, apps = _apps(tmp_path)
    app = apps["SYN-P12-A"]
    first = _fill(app)
    assert first.status == "POSTED"
    assert first.deviations == ("PRICE_DEVIATION",)
    assert first.account.cash_total == Decimal("4995.00")
    assert [(p.security_code, p.quantity) for p in first.account.positions] == [("600000", 400)]
    assert first.affected_plans == (_plan_id(app),)
    retry = _fill(app)
    assert retry.receipt_id == first.receipt_id and retry.account.cash_total == Decimal("4995.00")
    view = app.workbench(app.scope)
    entry = view.opportunities[0]
    assert (entry.status_code, entry.action_code) == ("NEEDS_REASSESSMENT", "REVIEW")
    assert entry.primary_constraint == "PRICE_DEVIATION"
    corrected = app.correct_fill(
        app.scope, CorrectFill("corr-1", "fill-1", 1, 300, Decimal("12.50"), "broker statement")
    )
    assert corrected.status == "POSTED"
    assert (corrected.cash_delta, corrected.quantity_delta) == (Decimal("1250.00"), -100)
    assert corrected.account.cash_total == Decimal("6245.00")
    assert corrected.current_fact_revision == 2
    records = app.records(app.scope)
    assert records.reports[0].report.quantity == 400  # original report preserved
    assert records.facts[0].fact.quantity == 300
    assert [c.correction_id for c in records.facts[0].corrections] == ["corr-1"]
    assert [(e.kind, e.cash_delta) for e in records.events] == [
        ("fill", Decimal("-5005.00")),
        ("correction", Decimal("1250.00")),
    ]
    assert app.rebuild_matches(app.scope)
    _, reopened = _apps(tmp_path)
    again = reopened["SYN-P12-A"].workbench(reopened["SYN-P12-A"].scope)
    assert again.account.cash_total == Decimal("6245.00")
    assert again.account.ledger_digest == corrected.account.ledger_digest


def test_stale_correction_returns_the_current_chain_without_writing(tmp_path: Path) -> None:
    _, apps = _apps(tmp_path)
    app = apps["SYN-P12-A"]
    _fill(app)
    app.correct_fill(app.scope, CorrectFill("c1", "fill-1", 1, 300, Decimal("12.50"), "r"))
    before = _business_rows(tmp_path / STORE_NAME)
    stale = app.correct_fill(app.scope, CorrectFill("c2", "fill-1", 1, 200, Decimal("12.50"), "r"))
    assert stale.status == "NEEDS_RECONCILIATION" and stale.conflicts == ("STALE_FACT_REVISION",)
    assert _business_rows(tmp_path / STORE_NAME) == before


def test_changed_report_under_same_id_is_a_conflict_and_ledger_is_unchanged(tmp_path: Path) -> None:
    _, apps = _apps(tmp_path)
    app = apps["SYN-P12-A"]
    _fill(app)
    before = _business_rows(tmp_path / STORE_NAME)
    conflict = _fill(app, price="12.60")
    assert conflict.status == "IDEMPOTENCY_CONFLICT"
    assert conflict.conflicts == ("REPORT_ID_PAYLOAD_CONFLICT",)
    assert _business_rows(tmp_path / STORE_NAME) == before


def test_facts_are_received_even_outside_the_plan_window(tmp_path: Path) -> None:
    _, apps = _apps(tmp_path)
    app = apps["SYN-P12-A"]
    early = _fill(app, occurred_at=FILL_AT.replace(hour=9, minute=0), price="12.30")
    assert early.status == "POSTED"
    assert early.deviations == ("OUTSIDE_PLAN_WINDOW",)
    assert app.records(app.scope).reports[0].deviations == ("OUTSIDE_PLAN_WINDOW",)


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"occurred_at": FILL_AT + timedelta(days=1)}, "INVALID_REPORT_SHAPE"),
        ({"plan_id": "plan-unknown"}, "PLAN_NOT_FOUND"),
        ({"price": "12.505"}, "INVALID_REPORT_SHAPE"),
        ({"quantity": 0}, "INVALID_REPORT_SHAPE"),
    ],
)
def test_invalid_fill_inputs_are_rejected_before_any_write(tmp_path: Path, kwargs, code) -> None:
    _, apps = _apps(tmp_path)
    app = apps["SYN-P12-A"]
    before = _business_rows(tmp_path / STORE_NAME)
    with pytest.raises(FactError) as error:
        _fill(app, **kwargs)
    assert error.value.reason_code == code
    assert _business_rows(tmp_path / STORE_NAME) == before


def test_mid_transaction_fault_rolls_back_and_retry_posts_once(tmp_path: Path) -> None:
    armed = {"on": True}

    def fault(point: str) -> None:
        if armed["on"] and point == "after_fact_event_before_projection":
            raise RuntimeError("injected fault")

    _, apps = _apps(tmp_path, fault_hook=fault)
    app = apps["SYN-P12-A"]
    before = _business_rows(tmp_path / STORE_NAME)
    with pytest.raises(RuntimeError, match="injected fault"):
        _fill(app)
    assert _business_rows(tmp_path / STORE_NAME) == before
    with sqlite3.connect(tmp_path / STORE_NAME) as db:
        assert db.execute("SELECT status FROM inbox").fetchall() == [("RECEIVED",)]
    armed["on"] = False
    posted = _fill(app)
    assert posted.status == "POSTED" and posted.account.cash_total == Decimal("4995.00")
    with sqlite3.connect(tmp_path / STORE_NAME) as db:
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 1


def test_accounts_are_isolated_and_scope_is_bound(tmp_path: Path) -> None:
    _, apps = _apps(tmp_path)
    a, b = apps["SYN-P12-A"], apps["SYN-P12-B"]
    _fill(a)
    assert b.workbench(b.scope).account.cash_total == Decimal("10000.00")
    with pytest.raises(FactError, match="UNAUTHORIZED_CONTEXT"):
        a.workbench(b.scope)
    with pytest.raises(FactError, match="UNAUTHORIZED_CONTEXT"):
        a.workbench(AccountScope("SYN-P12-A", "real"))


def test_decision_source_refuses_non_synthetic_scopes(tmp_path: Path) -> None:
    fixture = load_synthetic_fixture(FIXTURE)
    source = FixtureDecisionSource(fixture)
    with pytest.raises(FactError, match="POLICY_NOT_ADOPTED"):
        source.current(AccountScope("SYN-P12-A", "simulation"))
    with pytest.raises(FactError, match="POLICY_NOT_ADOPTED"):
        source.current(AccountScope("REAL-1", "synthetic"))
    assert fixture.policy.missing_for_action("synthetic") == ()
    assert fixture.policy.missing_for_action("simulation") == (
        "A01",
        "A02",
        "A03",
        "A04",
        "A05",
        "A06",
    )


def test_pit_breaches_fail_closed() -> None:
    decisions = load_synthetic_fixture(FIXTURE).decisions
    entry = decisions.decisions[0]
    early = replace(entry, entry=replace(entry.entry, eligible_at=decisions.knowledge_cutoff))
    with pytest.raises(FactError, match="KNOWLEDGE_CUTOFF_BREACH"):
        validate_decision_set(replace(decisions, decisions=(early, *decisions.decisions[1:])))
    late_chart = replace(entry, chart=replace(entry.chart, as_of=decisions.published_at))
    with pytest.raises(FactError, match="KNOWLEDGE_CUTOFF_BREACH"):
        validate_decision_set(replace(decisions, decisions=(late_chart, *decisions.decisions[1:])))
    with pytest.raises(FactError, match="POLICY_NOT_ADOPTED"):
        validate_decision_set(replace(decisions, synthetic=False))


def _variant(tmp_path: Path, mutate) -> Path:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    mutate(raw)
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return path


def test_quantity_follows_the_server_policy_not_the_page(tmp_path: Path) -> None:
    def tighter(raw):
        raw["policy"]["risk"]["stock_limit"] = "0.30"

    path = _variant(tmp_path, tighter)
    _, apps = _apps(tmp_path / "state", fixture_path=path)
    app = apps["SYN-P12-A"]
    plan = app.workbench(app.scope).package.plans[0]
    assert plan.target_qty == 200
    assert plan.primary_constraint == "STOCK_CAPACITY"


def test_insufficient_capacity_publishes_no_executable_quantity(tmp_path: Path) -> None:
    def tiny(raw):
        raw["policy"]["funding"]["bucket_budget"] = "1000.00"
        raw["policy"]["funding"]["initial_reserve"] = "8000.00"

    path = _variant(tmp_path, tiny)
    _, apps = _apps(tmp_path / "state", fixture_path=path)
    app = apps["SYN-P12-A"]
    view = app.workbench(app.scope)
    assert view.package.plans[0].delta_qty == 0
    assert view.package.plans[0].primary_constraint == "BUDGET_CAPACITY"
    assert (view.opportunities[0].action_code, view.opportunities[0].status_code) == (
        "WAIT",
        "WAITING",
    )
    with pytest.raises(FactError, match="PLAN_NOT_EXECUTABLE"):
        _fill(app, quantity=100)


def test_funding_partition_mismatch_is_reported_not_invented(tmp_path: Path) -> None:
    def mismatch(raw):
        raw["policy"]["funding"]["bucket_budget"] = "4000.00"

    path = _variant(tmp_path, mismatch)
    _, apps = _apps(tmp_path / "state", fixture_path=path)
    app = apps["SYN-P12-A"]
    funding = app.funding_rules(app.scope).funding
    assert funding.status_reason == "BUCKET_ASSIGNMENT_CONFLICT"
    assert all(bucket.cash.value is None for bucket in funding.buckets)
    assert app.workbench(app.scope).account.reserve.reason_code == "BUCKET_ASSIGNMENT_CONFLICT"

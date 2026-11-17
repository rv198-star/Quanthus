"""Independent witnesses for the U05 candidate's material boundary failures."""

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.capabilities.execution import Correction, ExecutionReport, SyntheticContext
from quanthus.platform.adapters.product_daily_facts_sqlite import build_synthetic_fact_service
from quanthus.product_daily.application import Baseline
from quanthus.product_daily.application.models import SyntheticRuntimeAuthorization
from quanthus.shared.fact_contracts import FactError

AUTH = SyntheticRuntimeAuthorization("fixture-runtime", frozenset({"SYN-A", "SYN-B", "SYN-U05"}))
NOW = datetime(2026, 11, 18, tzinfo=timezone.utc)


def context(account="SYN-A"):
    return SyntheticContext(account, "synthetic", "fixture", NOW, "complete")


def baseline(c=None, **kw):
    return Baseline(
        c or context(),
        kw.get("request", "base"),
        Decimal(kw.get("cash", "10000.00")),
        0,
        "600000",
        kw.get("unsettled", 0),
        ("plan-dependent",),
    )


def report(identity="fill-1", **kw):
    return ExecutionReport(
        identity,
        kw.get("security", "600000"),
        "buy",
        kw.get("quantity", 400),
        Decimal(kw.get("price", "12.50")),
        Decimal("5.00"),
        NOW,
        kw.get("source", "broker"),
        kw.get("sequence", "one"),
        400,
        Decimal("12.40"),
    )


def business(path):
    with sqlite3.connect(path) as db:
        return {
            table: db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            for table in [
                "accounts",
                "events",
                "postings",
                "facts",
                "source_facts",
                "corrections",
                "outbox",
            ]
        }


def test_baseline_replay_is_original_and_changed_identity_is_audited(tmp_path: Path):
    path = tmp_path / "base.sqlite"
    c = context()
    s = build_synthetic_fact_service(path, context=c, authorization=AUTH, clock=lambda: NOW)
    initial = s.establish_baseline(baseline())
    s.submit_report(c, report())
    before = business(path)
    assert s.establish_baseline(baseline()) == initial
    assert s.establish_baseline(baseline(cash="10000.0")) == initial
    with pytest.raises(FactError, match="IDEMPOTENCY_CONFLICT"):
        s.establish_baseline(baseline(cash="20000.00"))
    assert business(path) == before
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM conflicts").fetchone()[0] == 1
        assert db.execute("SELECT cash FROM baselines").fetchone()[0] == "10000.00"


def test_account_namespace_isolation_and_distinct_source_pairs(tmp_path: Path):
    path = tmp_path / "accounts.sqlite"
    a = context()
    b = context("SYN-B")
    sa = build_synthetic_fact_service(path, context=a, authorization=AUTH)
    sb = build_synthetic_fact_service(path, context=b, authorization=AUTH)
    sa.establish_baseline(baseline(a))
    sb.establish_baseline(baseline(b))
    before = business(path)
    with pytest.raises(FactError, match="UNAUTHORIZED_CONTEXT"):
        sa.submit_report(b, report())
    assert business(path) == before
    first = sa.submit_report(a, report())
    second = sb.submit_report(b, report())
    assert first.receipt_id != second.receipt_id
    assert first.projection.cash == second.projection.cash == Decimal("4995.00")
    assert (
        sa.submit_report(a, report("r2", quantity=1, price="1", source="a:b", sequence="c")).status
        == "POSTED"
    )
    assert (
        sa.submit_report(a, report("r3", quantity=1, price="1", source="a", sequence="b:c")).status
        == "POSTED"
    )
    current = sa.projection(a)
    duplicate = sa.submit_report(a, report("r4", quantity=1, price="1", source="a", sequence="b:c"))
    assert duplicate.status == "NEEDS_RECONCILIATION"
    assert duplicate.conflicts == ("SOURCE_FACT_DUPLICATE",)
    assert sa.projection(a) == current
    assert sa.rebuild(a) == current


def test_receipt_result_replay_and_correction_conflict_survive_reopen(tmp_path: Path):
    path = tmp_path / "replay.sqlite"
    c = context()
    s = build_synthetic_fact_service(path, context=c, authorization=AUTH)
    s.establish_baseline(baseline())
    original = s.submit_report(c, report())
    corr = Correction("correction-1", "fill-1", 1, 300, Decimal("12.50"), "broker correction")
    corrected = s.correct_fact(c, corr)
    s.submit_report(c, report("fill-2", quantity=1, price="1", sequence="two", security="600001"))
    before = business(path)
    reopened = build_synthetic_fact_service(path, context=c, authorization=AUTH)
    assert reopened.submit_report(c, replace(report(), price=Decimal("12.5"))) == original
    assert reopened.correct_fact(c, corr) == corrected
    with pytest.raises(FactError, match="IDEMPOTENCY_CONFLICT"):
        reopened.correct_fact(c, replace(corr, quantity=250))
    assert business(path) == before
    assert reopened.rebuild(c) == reopened.projection(c)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM conflicts").fetchone()[0] == 1
        raw = json.loads(
            db.execute("SELECT payload_json FROM inbox WHERE report_id='fill-1'").fetchone()[0]
        )
        assert raw["quantity"] == 400


def test_correction_failure_and_insufficient_cash_preserve_received_facts(tmp_path: Path):
    path = tmp_path / "fault.sqlite"
    c = context()
    fired = []

    def fail(stage):
        if stage == "after_correction_event_before_projection" and not fired:
            fired.append(stage)
            raise RuntimeError("correction fault")

    s = build_synthetic_fact_service(
        path, context=c, authorization=AUTH, fault_hook=fail, clock=lambda: NOW
    )
    s.establish_baseline(baseline())
    s.submit_report(c, report())
    corr = Correction("c1", "fill-1", 1, 300, Decimal("12.50"), "correction")
    before = business(path)
    with pytest.raises(RuntimeError, match="correction fault"):
        s.correct_fact(c, corr)
    assert business(path) == before
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT status FROM correction_inbox").fetchone()[0] == "RECEIVED"
    s.correct_fact(c, corr)
    before = business(path)
    with pytest.raises(FactError, match="INSUFFICIENT_CASH"):
        s.correct_fact(
            c, replace(corr, correction_id="c2", expected_fact_revision=2, quantity=1000)
        )
    assert business(path) == before
    with sqlite3.connect(path) as db:
        row = db.execute(
            "SELECT status,error_code,payload_json FROM correction_inbox WHERE correction_id='c2'"
        ).fetchone()
        assert row[:2] == ("NEEDS_RECONCILIATION", "INSUFFICIENT_CASH")
        assert json.loads(row[2])["quantity"] == 1000
        assert db.execute("SELECT received_at FROM inbox").fetchone()[0] == NOW.isoformat()
        assert (
            db.execute("SELECT received_at FROM correction_inbox").fetchone()[0] == NOW.isoformat()
        )
    assert s.projection(c) == s.rebuild(c)


def test_baseline_unknown_holds_and_money_shape_fail_before_writes(tmp_path: Path):
    path = tmp_path / "shape.sqlite"
    c = context()
    s = build_synthetic_fact_service(path, context=c, authorization=AUTH)
    with pytest.raises(FactError, match="UNSUPPORTED_BASELINE_HOLDS"):
        s.establish_baseline(baseline(unsettled=10))
    with pytest.raises(FactError, match="INVALID_REPORT_SHAPE"):
        s.establish_baseline(baseline(cash="1.001"))
    with pytest.raises(FactError, match="INVALID_REPORT_SHAPE"):
        Baseline(c, "b", None, 0, "600000", 0, ())
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM baselines").fetchone()[0] == 0
    blocked = tmp_path / "actual.sqlite"
    with pytest.raises(FactError, match="UNAUTHORIZED_CONTEXT"):
        build_synthetic_fact_service(blocked, context=replace(c, mode="actual"), authorization=AUTH)
    assert not blocked.exists()


def test_unversioned_store_is_retained_instead_of_silently_migrated(tmp_path: Path):
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE old_candidate(value TEXT)")
        db.execute("INSERT INTO old_candidate VALUES('kept')")
    with pytest.raises(FactError, match="STORE_SCHEMA_MISMATCH"):
        build_synthetic_fact_service(path, context=context(), authorization=AUTH)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM old_candidate").fetchone()[0] == "kept"
        assert db.execute("PRAGMA user_version").fetchone()[0] == 0


def test_runtime_authority_is_independent_of_business_context(tmp_path: Path):
    c = context()
    path = tmp_path / "no-authority.sqlite"
    with pytest.raises(TypeError):
        build_synthetic_fact_service(path, context=c)
    assert not path.exists()
    with pytest.raises(TypeError):
        SyntheticContext("SYN-A", "synthetic", "caller", NOW, "complete", trusted_synthetic=True)
    policy = SyntheticRuntimeAuthorization("runtime", frozenset({"SYN-B"}))
    with pytest.raises(FactError, match="UNAUTHORIZED_CONTEXT"):
        build_synthetic_fact_service(path, context=c, authorization=policy)
    assert not path.exists()


def test_plan_reassessment_is_atomic_named_and_replayed(tmp_path: Path):
    c = context()
    path = tmp_path / "plans.sqlite"
    fired = []

    def fail(stage):
        if not fired:
            fired.append(stage)
            raise RuntimeError("plan transaction fault")

    s = build_synthetic_fact_service(path, context=c, authorization=AUTH, fault_hook=fail)
    s.establish_baseline(baseline())
    with pytest.raises(RuntimeError, match="plan transaction fault"):
        s.submit_report(c, report())
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT plan_id,status FROM dependent_plans").fetchall() == [
            ("plan-dependent", "READY")
        ]
        assert db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0
    result = s.submit_report(c, report())
    assert result.affected_plans == ("plan-dependent",)
    assert result.current_revision == 1
    assert result.next_action == "REASSESS_AFFECTED_PLANS"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT plan_id,status FROM dependent_plans").fetchall() == [
            ("plan-dependent", "NEEDS_REASSESSMENT")
        ]
        payload = json.loads(db.execute("SELECT payload_json FROM outbox").fetchone()[0])
        assert payload["affected_plans"] == ["plan-dependent"]
        assert payload["action"] == "NEEDS_REASSESSMENT"
    reopened = build_synthetic_fact_service(path, context=c, authorization=AUTH)
    assert reopened.submit_report(c, report()) == result


def test_public_capabilities_have_no_product_application_or_storage_dependency():
    import ast
    import inspect

    from quanthus.capabilities.accounting import facts as accounting_facts
    from quanthus.capabilities.execution import facts as execution_facts

    for module in [accounting_facts, execution_facts]:
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(
                    ("quanthus.product_daily", "quanthus.platform", "sqlite3", "fastapi")
                )

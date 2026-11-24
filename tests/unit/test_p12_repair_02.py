"""P12-OPUS-FOUNDATION-REPAIR-20261119-02: the four audited counterexamples and their neighbours."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.platform.adapters.p12_synthetic_fixture import load_synthetic_fixture
from quanthus.platform.p12.assembly import STORE_NAME, synthetic_applications
from quanthus.product_daily.application.desktop import CorrectFill, SubmitFill
from quanthus.shared.fact_contracts import FactError

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "config/p12/synthetic-desktop.v1.json"
ACCOUNT = "SYN-P12-A"
PLAN = "plan-OPP-SYN-600000-2026-11-18"


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _app(state: Path, fixture: Path = FIXTURE, **kwargs):
    return synthetic_applications(
        state_dir=state, fixture=load_synthetic_fixture(fixture), **kwargs
    )[ACCOUNT]


def _fill(
    app,
    report_id="fill-1",
    *,
    at="2026-11-19T09:31:00+08:00",
    qty=400,
    price="12.50",
    fee="5.00",
    plan=PLAN,
):
    return app.submit_fill(
        app.scope,
        SubmitFill(report_id, plan, qty, Decimal(price), Decimal(fee), _at(at)),
    )


def _variant(tmp_path: Path, name: str, mutate) -> Path:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    mutate(raw)
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return path


def _rows(state: Path) -> dict[str, list]:
    with sqlite3.connect(state / STORE_NAME) as db:
        return {
            table: db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            for table in ("accounts", "events", "postings", "facts", "outbox", "dependent_plans")
        }


# 1. Facts before the takeover snapshot ------------------------------------------------------


def test_pre_takeover_fill_is_received_pending_and_not_posted(tmp_path: Path) -> None:
    app = _app(tmp_path)
    before = _rows(tmp_path)
    first = _fill(app, at="2026-11-18T14:59:00+08:00")
    assert first.status == "NEEDS_RECONCILIATION"
    assert first.conflicts == ("BASELINE_COVERAGE_UNKNOWN",)
    assert first.account.cash_total == Decimal("10000.00") and first.account.positions == ()
    assert first.account.reconciliation_status == "NEEDS_RECONCILIATION"
    assert first.account.cash_available_for_new.reason_code == "RECONCILIATION_PENDING"
    assert _rows(tmp_path) == before
    retry = _fill(app, at="2026-11-18T14:59:00+08:00")
    assert (retry.receipt_id, retry.status) == (first.receipt_id, "NEEDS_RECONCILIATION")
    records = app.records(app.scope)
    report = records.reports[0].report
    assert (report.status, report.conflicts, report.quantity) == (
        "NEEDS_RECONCILIATION",
        ("BASELINE_COVERAGE_UNKNOWN",),
        400,
    )
    assert records.events == () and records.history.facts == ()
    entry = app.workbench(app.scope).opportunities[0]
    assert (entry.queue_group, entry.status_code) == ("reconcile", "NEEDS_RECONCILIATION")


def test_post_takeover_deviating_fills_still_post(tmp_path: Path) -> None:
    app = _app(tmp_path)
    over_cap = _fill(app, "fill-cap")
    assert (over_cap.status, over_cap.deviations) == ("POSTED", ("PRICE_DEVIATION",))
    early = _fill(
        app, "fill-early", at="2026-11-19T09:00:00+08:00", qty=100, price="12.30", fee="5.00"
    )
    assert (early.status, early.deviations) == (
        "POSTED",
        ("QUANTITY_DEVIATION", "OUTSIDE_PLAN_WINDOW"),
    )
    assert early.account.cash_total == Decimal("3760.00")


# 2. Policy drift on the same store -----------------------------------------------------------


def _tighter(raw: dict) -> None:
    raw["policy"]["risk"].update(stock_limit="0.30", version="0.2.0")


def test_reopening_a_store_with_a_new_policy_is_refused(tmp_path: Path) -> None:
    state = tmp_path / "store"
    app = _app(state)
    _fill(app)
    before = _rows(state)
    with pytest.raises(FactError) as error:
        _app(state, _variant(tmp_path, "risk-v2", _tighter))
    assert error.value.reason_code == "CONFIGURATION_BINDING_MISMATCH"
    assert "p12-synthetic-composition@0.1.0" in str(error.value)
    assert _rows(state) == before


def test_original_configuration_keeps_plan_and_receipts_new_store_uses_new_policy(
    tmp_path: Path,
) -> None:
    state = tmp_path / "store"
    app = _app(state)
    first = _fill(app)
    reopened = _app(state)
    records = reopened.records(reopened.scope)
    assert records.package.plans[0].target_qty == 400
    assert records.reports[0].report.intended_quantity == 400
    replay = _fill(reopened)
    assert (replay.status, replay.receipt_id) == ("POSTED", first.receipt_id)
    fresh = _app(tmp_path / "fresh", _variant(tmp_path, "risk-v2", _tighter))
    assert fresh.workbench(fresh.scope).package.plans[0].target_qty == 200


def test_legacy_store_without_binding_is_adopted_only_when_reports_match(tmp_path: Path) -> None:
    state = tmp_path / "legacy"
    _fill(_app(state))

    def forget_binding() -> None:
        with sqlite3.connect(state / STORE_NAME) as db:
            db.execute("DELETE FROM configuration_bindings")

    forget_binding()
    with pytest.raises(FactError, match="CONFIGURATION_BINDING_MISMATCH"):
        _app(state, _variant(tmp_path, "risk-v2", _tighter))
    forget_binding()
    adopted = _app(state)
    assert adopted.records(adopted.scope).package.plans[0].target_qty == 400


# 3. Publication time and the next-session window ---------------------------------------------


def test_package_is_hidden_until_published(tmp_path: Path) -> None:
    app = _app(tmp_path, clock=lambda: _at("2026-11-18T16:00:00+08:00"))
    view = app.workbench(app.scope)
    assert view.package is None and view.opportunities == ()
    assert view.publication.status == "NOT_YET_PUBLISHED"
    # Issue #721 R16 (r2): published_at is the REAL publish moment only;
    # before visibility it stays null (never the request's own clock).
    assert view.publication.published_at is None
    assert view.publication.observed_at == _at("2026-11-18T16:00:00+08:00")
    assert app.records(app.scope).package is None
    with pytest.raises(FactError, match="PLAN_NOT_FOUND"):
        _fill(app, at="2026-11-18T15:30:00+08:00")


@pytest.mark.parametrize(
    ("clock", "status", "action"),
    [
        ("2026-11-18T18:30:00+08:00", "SCHEDULED", "BUY"),
        ("2026-11-18T19:00:00+08:00", "SCHEDULED", "BUY"),
        ("2026-11-19T10:05:00+08:00", "ACTIONABLE", "BUY"),
        ("2026-11-19T15:30:00+08:00", "EXPIRED", "NONE"),
    ],
)
def test_next_session_window_drives_applicability(tmp_path, clock, status, action) -> None:
    app = _app(tmp_path, clock=lambda: _at(clock))
    entry = app.workbench(app.scope).opportunities[0]
    assert (entry.applicability.status, entry.action_code) == (status, action)


@pytest.mark.parametrize(
    "window",
    [
        ("2026-11-18T18:31:00+08:00", "2026-11-18T19:30:00+08:00"),
        ("2026-11-19T08:00:00+08:00", "2026-11-19T09:00:00+08:00"),
        ("2026-11-20T09:30:00+08:00", "2026-11-20T15:00:00+08:00"),
    ],
)
def test_windows_outside_the_next_session_publish_no_plan(tmp_path, window) -> None:
    def move(raw: dict) -> None:
        # The T package appears under both decision_set and decision_sets[0];
        # the loader prefers decision_sets, so move the window in both places.
        for key in ("decision_set",):
            raw[key]["decisions"][0]["entry"].update(
                eligible_at=window[0], expires_at=window[1]
            )
        for item in raw.get("decision_sets", []):
            if item["market_date"] == "2026-11-18":
                item["decisions"][0]["entry"].update(
                    eligible_at=window[0], expires_at=window[1]
                )

    path = _variant(tmp_path, "window", move)
    app = _app(tmp_path / "state", path, clock=lambda: _at("2026-11-18T19:00:00+08:00"))
    view = app.workbench(app.scope)
    entry = view.opportunities[0]
    assert view.package.plans == ()
    assert (entry.action_code, entry.status_code, entry.primary_constraint) == (
        "PAUSED",
        "MISSING_INPUT",
        "EXECUTION_WINDOW_INVALID",
    )
    assert entry.applicability is None
    with pytest.raises(FactError, match="PLAN_NOT_FOUND"):
        _fill(app, at="2026-11-18T18:45:00+08:00")


# 4. One snapshot per read -------------------------------------------------------------------


def test_read_interleaved_with_a_writer_returns_the_complete_old_state(tmp_path: Path) -> None:
    writer = _app(tmp_path)
    threads: list[threading.Thread] = []
    armed = threading.Event()  # assembly itself reads a snapshot; interleave only the query

    def interleave(point: str) -> None:
        if point == "snapshot_between_projection_and_history" and armed.is_set() and not threads:
            thread = threading.Thread(target=lambda: _fill(writer))
            threads.append(thread)
            thread.start()
            time.sleep(0.3)  # the writer is now blocked on the reader's shared lock

    reader = _app(tmp_path, fault_hook=interleave)
    armed.set()
    split = reader.records(reader.scope)
    threads[0].join(timeout=10)
    assert split.account.cash_total == Decimal("10000.00") and split.account.positions == ()
    assert (len(split.reports), len(split.events), len(split.facts)) == (0, 0, 0)
    after = reader.records(reader.scope)
    assert after.account.cash_total == Decimal("4995.00")
    assert (len(after.reports), len(after.events), len(after.facts)) == (1, 1, 1)
    assert after.account.account_revision == len(after.events)


def test_concurrent_reads_are_always_internally_consistent(tmp_path: Path) -> None:
    writer = _app(tmp_path)
    reader = _app(tmp_path)
    done = threading.Event()
    seen: list[tuple] = []

    def write() -> None:
        for index in range(12):
            _fill(writer, f"fill-{index}", qty=10, price="10.00", fee="0.00")
        done.set()

    thread = threading.Thread(target=write)
    thread.start()
    while not done.is_set() or not seen:
        view = reader.records(reader.scope)
        cash = view.history.baseline_cash + sum((e.cash_delta for e in view.events), Decimal(0))
        shares = sum(f.fact.quantity for f in view.facts)
        position = sum(p.quantity for p in view.account.positions)
        assert view.account.cash_total == cash
        assert position == shares
        assert view.account.account_revision == len(view.events) == len(view.facts)
        assert sum(r.report.status == "POSTED" for r in view.reports) == len(view.facts)
        seen.append((view.account.account_revision, len(view.events)))
    thread.join(timeout=30)
    final = reader.records(reader.scope)
    assert final.account.cash_total == Decimal("8800.00") and final.account.account_revision == 12


def test_outcome_fact_revision_comes_from_the_same_snapshot(tmp_path: Path) -> None:
    app = _app(tmp_path)
    assert _fill(app).current_fact_revision == 1
    corrected = app.correct_fill(
        app.scope, CorrectFill("corr-1", "fill-1", 1, 300, Decimal("12.50"), "statement")
    )
    assert corrected.current_fact_revision == 2
    replay = _fill(app)
    assert replay.current_fact_revision == 2
    assert replay.account.cash_total == Decimal("6245.00")

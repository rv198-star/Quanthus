"""P12 repair 03: publication through the real HTTP reads, and legacy store takeover."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.platform.adapters import product_daily_facts_sqlite
from quanthus.platform.adapters.p12_synthetic_fixture import load_synthetic_fixture
from quanthus.platform.composition import create_platform_kernel
from quanthus.platform.p12.assembly import (
    STORE_NAME,
    build_synthetic_runtime,
    synthetic_applications,
)
from quanthus.platform.persistence import InMemoryPlatformStore
from quanthus.product_daily.application.desktop import SubmitFill
from quanthus.shared.fact_contracts import FactError

from .conftest import TestCipher, login, wsgi_request

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "config/p12/synthetic-desktop.v1.json"
ACCOUNT = "SYN-P12-A"
PLAN = "plan-OPP-SYN-600000-2026-11-18"
PASSWORD = "Reader-password-123"
CANDIDATE_MARKERS = ("600000", "000002", "300001", "OPP-SYN", "plan-OPP", "合成证券")


def _reader_kernel(state: Path, attribution_release: Path, clock: str):
    runtime = build_synthetic_runtime(
        state_dir=state,
        account_users={ACCOUNT: frozenset({"reader"})},
        clock=lambda: datetime.fromisoformat(clock),
    )
    kernel = create_platform_kernel(
        store=InMemoryPlatformStore(),
        cipher=TestCipher(),
        attribution_root=attribution_release.parent,
        p12_runtime=runtime,
    )
    kernel.migrate()
    kernel.identity.create_user(username="reader", password=PASSWORD, role="viewer")
    headers = {"Authorization": f"Bearer {login(kernel, username='reader', password=PASSWORD)}"}
    return kernel, headers


def _read(kernel, headers, endpoint: str):
    status, _, body = wsgi_request(
        kernel, "GET", f"/api/p12/v1/accounts/{ACCOUNT}/{endpoint}", headers=headers
    )
    assert status == 200, body
    return body


def test_reads_before_publication_expose_no_candidate_identity(tmp_path, attribution_release):
    kernel, headers = _reader_kernel(tmp_path, attribution_release, "2026-11-18T16:00:00+08:00")
    for endpoint in ("workbench", "records"):
        body = _read(kernel, headers, endpoint)
        assert body["package"] is None
        publication = body["publication"]
        assert publication["status"] == "NOT_YET_PUBLISHED"
        assert publication["market_date"] == "2026-11-18"
        assert publication["published_at"] == "2026-11-18T18:30:00+08:00"
        # next_check_at is an optional additive field (issue #721 R15), never
        # a candidate identity; only its presence/shape is checked here.
        assert set(publication).issubset(
            {"status", "market_date", "published_at", "retracted", "retraction_reason", "next_check_at"}
        )
        serialized = json.dumps(body, ensure_ascii=False)
        leaked = [marker for marker in CANDIDATE_MARKERS if marker in serialized]
        assert leaked == [], (endpoint, leaked)
        assert body["account"]["cash_total"] == "10000.00"
    records = _read(kernel, headers, "records")
    assert records["plans"] == [] and records["reports"] == []
    assert records["baseline"]["security_code"] == "CASH"
    assert (records["baseline"]["cash"], records["baseline"]["as_of"]) == (
        "10000.00",
        "2026-11-18T15:00:00+08:00",
    )
    assert _read(kernel, headers, "workbench")["opportunities"] == []


def test_reads_at_publication_show_the_plan_waiting_for_the_next_session(
    tmp_path, attribution_release
):
    kernel, headers = _reader_kernel(tmp_path, attribution_release, "2026-11-18T18:30:00+08:00")
    records = _read(kernel, headers, "records")
    assert records["publication"]["status"] == "PUBLISHED"
    assert [p["plan_id"] for p in records["package"]["plans"]] == [PLAN]
    assert records["plans"] == [{"plan_id": PLAN, "status": "READY", "based_on_revision": 0}]
    assert records["baseline"]["security_code"] == "CASH"
    board = _read(kernel, headers, "workbench")
    entry = board["opportunities"][0]
    assert (entry["opportunity_id"], entry["applicability"]["status"]) == (
        "OPP-SYN-600000",
        "SCHEDULED",
    )


def _seed_legacy(state: Path, occurred_at: str, monkeypatch) -> None:
    """Emulate a 66ddf73d store: no coverage check when posting, no configuration binding."""
    with monkeypatch.context() as patch:
        patch.setattr(product_daily_facts_sqlite, "baseline_coverage_gap", lambda *_: None)
        app = synthetic_applications(state_dir=state, fixture=load_synthetic_fixture(FIXTURE))[
            ACCOUNT
        ]
        result = app.submit_fill(
            app.scope,
            SubmitFill(
                "legacy",
                PLAN,
                400,
                Decimal("12.50"),
                Decimal("5.00"),
                datetime.fromisoformat(occurred_at),
            ),
        )
        assert result.status == "POSTED"
    with sqlite3.connect(state / STORE_NAME) as db:
        db.execute("DELETE FROM configuration_bindings")


def _rows(state: Path):
    with sqlite3.connect(state / STORE_NAME) as db:
        return {
            t: db.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall()
            for t in ("accounts", "events", "postings", "facts", "inbox", "configuration_bindings")
        }


def test_legacy_store_with_uncovered_pre_takeover_posting_is_refused(tmp_path, monkeypatch):
    state = tmp_path / "early"
    _seed_legacy(state, "2026-11-18T14:59:00+08:00", monkeypatch)
    before = _rows(state)
    with pytest.raises(FactError) as error:
        synthetic_applications(state_dir=state, fixture=load_synthetic_fixture(FIXTURE))
    assert error.value.reason_code == "BASELINE_COVERAGE_UNKNOWN"
    assert "legacy" in str(error.value) and "receipt-" in str(error.value)
    assert _rows(state) == before


def test_legacy_store_with_legal_fills_is_taken_over(tmp_path, monkeypatch):
    state = tmp_path / "valid"
    _seed_legacy(state, "2026-11-19T09:31:00+08:00", monkeypatch)
    app = synthetic_applications(state_dir=state, fixture=load_synthetic_fixture(FIXTURE))[ACCOUNT]
    receipt = app.records(app.scope).reports[0].report.receipt_id
    replay = app.submit_fill(
        app.scope,
        SubmitFill(
            "legacy",
            PLAN,
            400,
            Decimal("12.50"),
            Decimal("5.00"),
            datetime.fromisoformat("2026-11-19T09:31:00+08:00"),
        ),
    )
    assert (replay.status, replay.receipt_id) == ("POSTED", receipt)
    assert replay.account.cash_total == Decimal("4995.00")
    assert replay.account.reconciliation_status == "CLEAN"

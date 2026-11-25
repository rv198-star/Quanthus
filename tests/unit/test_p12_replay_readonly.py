"""Read-only review projection: GET paths never open the ledger/app, and the
account is a bound evidence snapshot - never a hard-coded balance.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

from quanthus.p12_replay.engine import ReplayEngine

_SNAP = {
    "scenario_id": "spdb-20261008-2130",
    "account": {
        "engineering_replay_as_observed": True,
        "rebuild_matches": True,
        "cash": "100000.00",
        "positions": [],
        "orders": 0,
        "reports": 0,
    },
}


def _snap_file(tmp_path, doc=_SNAP):
    raw = json.dumps(doc).encode()
    path = tmp_path / "snap.json"
    path.write_bytes(raw)
    return path, "sha256:" + hashlib.sha256(raw).hexdigest()


def _engine(tmp_path, *, read_only, snapshot=None):
    calls = {"app": 0}

    def _app_builder(**_kw):
        calls["app"] += 1
        raise AssertionError("read-only path must not open the account app")

    engine = ReplayEngine(
        state_dir=tmp_path / "state",
        sessions=[date(2026, 10, 8), date(2026, 10, 9)],
        bars_by_day={date(2026, 10, 8): {"600000": {}}, date(2026, 10, 9): {}},
        runner=None,
        spec=None,
        app_builder=_app_builder,
        base_manifest_path=tmp_path / "m.json",
        base_manifest_sha256="sha256:0",
        read_only=read_only,
        read_only_account_snapshot=snapshot,
    )
    engine._calls = calls
    return engine


def _snapshot(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


class TestReadOnlyProjection:
    def test_day_state_never_opens_app_or_ledger(self, tmp_path):
        path, sha = _snap_file(tmp_path)
        engine = _engine(
            tmp_path,
            read_only=True,
            snapshot={
                "path": path,
                "sha256": sha,
                "scenario_id": "spdb-20261008-2130",
            },
        )
        before = _snapshot(engine.state_dir)
        state = engine.day_state()
        after = _snapshot(engine.state_dir)
        assert engine._calls["app"] == 0
        assert after == before
        account = state["account"]
        assert account["read_only_projection"] is True
        assert account["account_snapshot_bound"] is True
        assert account["account_snapshot_sha256"] == sha
        assert account["rebuild_matches"] is True
        assert account["cash"] == "100000.00"
        assert account["positions"] == []
        assert account["orders"] == 0
        assert state["open_plans"] == []

    def test_snapshot_sha_mismatch_reports_unavailable(self, tmp_path):
        path, _sha = _snap_file(tmp_path)
        engine = _engine(
            tmp_path,
            read_only=True,
            snapshot={
                "path": path,
                "sha256": "sha256:" + "0" * 64,
                "scenario_id": "spdb-20261008-2130",
            },
        )
        account = engine.day_state()["account"]
        assert engine._calls["app"] == 0
        assert "sha256_mismatch" in account["account_snapshot_unavailable"]
        assert account["cash"] is None
        assert account["rebuild_matches"] is None

    def test_missing_snapshot_reports_not_bound(self, tmp_path):
        engine = _engine(tmp_path, read_only=True, snapshot=None)
        account = engine.day_state()["account"]
        assert account["account_snapshot_unavailable"] == "account_snapshot_not_bound"
        assert account["cash"] is None

    def test_day_review_and_records_project_without_app(self, tmp_path):
        engine = _engine(tmp_path, read_only=True, snapshot=None)
        review = engine.day_review()
        assert engine._calls["app"] == 0
        assert review["advice"] == []
        assert review["orders_today"] == []
        assert review["account"]["read_only_projection"] is True
        records = engine.records_view()
        assert records["read_only_projection"] is True
        assert records["orders"] == []

    def test_normal_mode_still_builds_app(self, tmp_path):
        engine = _engine(tmp_path, read_only=False)
        try:
            engine.reconcile()
        except AssertionError:
            pass
        assert engine._calls["app"] == 1

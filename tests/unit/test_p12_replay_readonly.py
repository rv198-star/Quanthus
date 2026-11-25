"""Read-only review projection: GET paths never open the ledger/app.

Regression for the coordinator finding that /api/state under
P12_REPLAY_READONLY still ran the normal day_state path -> _build_app ->
replay ledger (sqlite files in the state dir).  In read_only mode the
engine must project the sealed run/package/day-receipt evidence plus the
known baseline account (snapshot fc92def5, 100000.00 cash, zero holdings)
without ever calling app_builder - and must label the account a display
projection, never a reconciled ledger truth.
"""
from __future__ import annotations

from datetime import date

from quanthus.p12_replay.engine import ReplayEngine


def _engine(tmp_path, *, read_only: bool) -> ReplayEngine:
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
    )
    engine._calls = calls
    return engine


def _snapshot(root):
    return sorted(
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file()
    )


class TestReadOnlyProjection:
    def test_day_state_never_opens_app_or_ledger(self, tmp_path):
        engine = _engine(tmp_path, read_only=True)
        before = _snapshot(tmp_path)
        state = engine.day_state()
        after = _snapshot(tmp_path)

        assert engine._calls["app"] == 0
        # No new file (sqlite/ledger/log) may appear on a read path.
        assert after == before, f"read created files: {set(after) - set(before)}"
        account = state["account"]
        assert account["read_only_projection"] is True
        assert account["cash"] == "100000.00"
        assert account["positions"] == []
        assert account["orders"] == 0
        # A read-only projection is NOT a rebuild-from-events reconciliation.
        assert account["rebuild_matches"] is None
        assert state["replay"]["current_day"] == "2026-10-08"
        assert state["open_plans"] == []

    def test_day_review_projects_empty_chain_without_app(self, tmp_path):
        engine = _engine(tmp_path, read_only=True)
        review = engine.day_review()
        assert engine._calls["app"] == 0
        assert review["advice"] == []
        assert review["orders_today"] == []
        assert review["account"]["read_only_projection"] is True

    def test_workbench_and_records_are_projections(self, tmp_path):
        engine = _engine(tmp_path, read_only=True)
        bench = engine.workbench()
        assert bench["workbench"]["opportunities"] == []
        records = engine.records_view()
        assert records["read_only_projection"] is True
        assert records["orders"] == []
        assert engine._calls["app"] == 0

    def test_normal_mode_still_builds_app(self, tmp_path, monkeypatch):
        engine = _engine(tmp_path, read_only=False)
        # In the normal (non-read-only) mode reconcile() must still reach
        # _build_app; with our raising builder it raises the AssertionError.
        try:
            engine.reconcile()
        except AssertionError:
            pass
        assert engine._calls["app"] == 1

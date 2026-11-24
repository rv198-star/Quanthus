"""8792 phase-1 E1 web-entry wiring smoke tests.

build_e1_entry binds a per-T #749 read_history call to the E1ReplayRunner so
the browser POST /api/run-day and the replay advance path run the REAL P11
public run_e1_replay_day seam - never the engine's bars-only diagnostic
projection and never a synthetic package.  The tests mock read_history and
the P11 seam at the module boundary so the wiring (context supplier, member
evidence, per-T context, engine->run_daily transport) is what is verified.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from quanthus.p12_daily.e1_replay import E1ReplayRunner
from quanthus.p12_replay import bar_slice as _bar_mod
from quanthus.p12_replay import e1_assembly
from quanthus.p12_replay import engine as _engine_mod
from quanthus.product_daily.recommendation import e1_runtime as _e1_rt


@pytest.fixture(autouse=True)
def _stub_day_write(monkeypatch):
    """write_replay_day needs the real CN02 base manifest; the smoke tests
    only verify the reader/runner seam, so the product write is stubbed."""
    product = type(
        "P",
        (),
        {
            "core_manifest_sha256": "s" * 64,
            "index_sha256": "i" * 64,
            "row_counts": {"dwd_security_bar": 0},
        },
    )()
    monkeypatch.setattr(_engine_mod, "write_replay_day", lambda *a, **kw: product)
    monkeypatch.setattr(
        _bar_mod, "write_session_parquet", lambda bars, path: Path(path).write_bytes(b"x")
    )

SH_TZ = timezone(timedelta(hours=8))
T = date(2026, 10, 8)
CUTOFF = datetime(2026, 10, 8, 21, 30, tzinfo=SH_TZ)

ENVELOPE = {
    "schema_version": "p11.raw-history-input.v1",
    "market_date": T.isoformat(),
    "decision_cutoff": CUTOFF.isoformat(),
    "source_members": [
        {
            "member_id": "m-cal",
            "sha256": "a" * 64,
            "visible_at": "2026-10-08T15:00:00Z",
        },
        {"member_id": "m-nov", "sha256": "b" * 64, "visible_at": None},
    ],
    "calendar_rows": [{"venue_id": "XSHG"}],
    "listing_rows": [],
    "securities": [],
}

COVERAGE = {
    "member_consumption": {
        "members": [
            {
                "member_id": "m-cal",
                "package_key": "pkg::m-cal",
                "version": "v1",
                "sha256": "a" * 64,
                "verify_at": "2026-10-08T13:30:00Z",
                "relevant_for_T": True,
            },
            {
                "member_id": "m-nov",
                "package_key": "pkg::m-nov",
                "version": None,
                "sha256": "b" * 64,
                "verify_at": "2026-10-08T13:30:01Z",
                "relevant_for_T": False,
            },
        ]
    }
}

SCENARIO = {
    "scenario_id": "spdb-20261008",
    "account_namespace": "historical-replay:spdb-20261008",
    "package_id": "replay-core-2026-10-08",
    "manifest_sha256": "sha256:" + "c" * 64,
    "account": {"account_id": "SYN-PAPER-A", "cash_available": "100000"},
    "policy": {"policy_id": "p11-d1b@1.0.0"},
    "facts_template": {"facts_id": "f"},
    "snapshot_id": "snap-1",
    "snapshot_manifest_digest": "sha256:" + "d" * 64,
    "p12_evidence": {"envelope": {"uri": "mem://env", "sha256": "e" * 64}},
}

ACTION_COVERAGE = {"signed": [{"coverage_id": "b02:l:2026-10-08"}]}
EXCLUSIONS = ({"exclusion_id": "737-x"},)


def _fake_runner():
    ref = type("Ref", (), {"ref_id": "r", "version": "1", "digest": "d"})()
    return type("R", (), {"runner_ref": ref})()


class _Result:
    def __init__(self):
        self.raw_history = ENVELOPE
        self.coverage = COVERAGE


def _fake_read_history(market_date, decision_cutoff, *, holdings, pending, source_map):
    return _Result()


def _build(monkeypatch, holdings=(), pending=()):
    monkeypatch.setattr(e1_assembly, "read_history", _fake_read_history)
    monkeypatch.setattr(
        _e1_rt, "load_e1_rule_assets", lambda root: {"assets": "fake"}
    )
    return e1_assembly.build_e1_entry(
        repo_root=Path("/tmp/repo"),
        scenario=SCENARIO,
        action_coverage=ACTION_COVERAGE,
        exclusions=EXCLUSIONS,
        holdings=holdings,
        pending=pending,
        open_sessions=("2026-10-09",),
    )


def test_build_e1_entry_returns_runner_and_per_t_context(monkeypatch):
    runner, reader = _build(monkeypatch)
    assert isinstance(runner, E1ReplayRunner)
    # Before the first read the context supplier reports None -> honest block.
    from quanthus.shared.fact_contracts import FactError

    with pytest.raises(FactError):
        runner.run(type("Req", (), {"raw_history": ENVELOPE})())

    env = reader(T, CUTOFF)
    assert env is ENVELOPE
    ctx = runner._context_supplier(T)  # same T's evidence, bound by date
    assert ctx is not None
    assert ctx.scenario_id == "spdb-20261008"
    assert ctx.market_dates == (T.isoformat(),)
    assert ctx.open_sessions == ("2026-10-09",)
    assert ctx.package_id == SCENARIO["package_id"]
    assert ctx.manifest_sha256 == SCENARIO["manifest_sha256"]
    assert ctx.account == SCENARIO["account"]
    assert ctx.snapshot_id == "snap-1"
    assert ctx.exclusions == EXCLUSIONS
    assert ctx.action_coverage == ACTION_COVERAGE
    assert ctx.p12_evidence == SCENARIO["p12_evidence"]
    # member evidence carries ONLY this run's consumed members with clocks
    assert [m["member_id"] for m in ctx.member_evidence] == ["m-cal", "m-nov"]
    req_ids = ctx.required_member_ids
    assert req_ids == ("m-cal",)
    m0 = ctx.member_evidence[0]
    assert m0["package_key"] == "pkg::m-cal"
    assert m0["digest"] == "a" * 64
    # available_at is the member's own declared usable instant (the
    # envelope source_members visible_at), never the read verify_at.
    assert m0["available_at"] == "2026-10-08T15:00:00+00:00"
    assert "verified_at" not in m0
    assert ctx.member_evidence[1]["available_at"] is None
    assert m0["required"] is True


def test_history_reader_passes_holdings_and_pending(monkeypatch):
    seen = {}

    def fake(day, cutoff, *, holdings, pending, source_map):
        seen["holdings"] = holdings
        seen["pending"] = pending
        return _Result()

    monkeypatch.setattr(e1_assembly, "read_history", fake)
    monkeypatch.setattr(
        _e1_rt, "load_e1_rule_assets", lambda root: {"assets": "fake"}
    )
    _runner, reader = e1_assembly.build_e1_entry(
        repo_root=Path("/tmp/repo"),
        scenario=SCENARIO,
        action_coverage=ACTION_COVERAGE,
        holdings=lambda day: ({"security_code": "600000"},),
        pending=lambda day: ({"security_code": "000002", "kind": "open_buy_order"},),
    )
    reader(T, CUTOFF)
    assert seen["holdings"] == ({"security_code": "600000"},)
    assert seen["pending"] == ({"security_code": "000002", "kind": "open_buy_order"},)


def test_context_is_bound_per_market_date_not_last_read(monkeypatch):
    """A second day's read must not replace the context the FIRST T's run
    resolves - the supplier keys contexts by market_date."""
    T2 = date(2026, 10, 9)
    runner, reader = _build(monkeypatch)

    reader(T, CUTOFF)
    reader(T2, CUTOFF.replace(day=9))
    # The run for T still resolves T's own context (market_dates carries T).
    ctx_t = runner._context_supplier(T)
    ctx_t2 = runner._context_supplier(T2)
    assert ctx_t is not None and ctx_t2 is not None
    assert ctx_t is not ctx_t2
    assert ctx_t.market_dates == (T.isoformat(),)
    assert ctx_t2.market_dates == (T2.isoformat(),)
    # A day never read resolves no context -> honest block.
    assert runner._context_supplier(date(2026, 10, 12)) is None


def test_make_engine_accepts_e1_runner_and_reader(tmp_path):
    from quanthus.p12_replay.driver import make_engine

    calls = {}

    def reader(day, cutoff, holdings, pending):
        calls["reader"] = (day, holdings, pending)
        return ENVELOPE

    def projection(day):
        calls["proj"] = day
        return ({"security_code": "600000"},), ()

    engine = make_engine(
        state_dir=tmp_path / "state",
        sessions=[T, date(2026, 10, 9)],
        bars_by_day={},
        candidates_path=None,
        fixture_path=None,
        account_id="SYN-PAPER-A",
        e1_runner=_fake_runner(),
        history_reader=reader,
        account_projection=projection,
    )
    # materialize_day is called inside evening_run; drive it directly.
    engine.materialize_day(T)
    assert calls["reader"][0] == T
    assert calls["reader"][1] == ({"security_code": "600000"},)
    assert calls["reader"][2] == ()
    assert calls["proj"] == T
    assert engine._real_raw_history is ENVELOPE


def test_engine_without_reader_blocks_honestly(tmp_path):
    from quanthus.p12_replay.driver import make_engine

    engine = make_engine(
        state_dir=tmp_path / "state",
        sessions=[T, date(2026, 10, 9)],
        bars_by_day={},
        candidates_path=None,
        fixture_path=None,
        account_id="SYN-PAPER-A",
    )
    engine.materialize_day(T)
    # the bars-only diagnostic projection stays; the real envelope is absent
    assert getattr(engine, "_real_raw_history", None) is None
    assert engine._raw_history is not None  # diagnostic bars-only stays


def test_reader_signature_falls_back_to_two_arg(tmp_path):
    from quanthus.p12_replay.driver import make_engine

    def reader(day, cutoff):  # older 2-arg reader
        return ENVELOPE

    engine = make_engine(
        state_dir=tmp_path / "state",
        sessions=[T, date(2026, 10, 9)],
        bars_by_day={},
        candidates_path=None,
        fixture_path=None,
        account_id="SYN-PAPER-A",
        history_reader=reader,
    )
    engine.materialize_day(T)
    assert engine._real_raw_history is ENVELOPE



def test_evening_run_transports_envelope_to_run_daily(tmp_path, monkeypatch):
    """evening_run hands the SAME T envelope to run_daily; a stubbed
    run_daily records raw_history so the transport is verified end-to-end."""
    from quanthus.p12_replay import engine as eng_mod
    from quanthus.p12_replay.driver import make_engine

    seen = {}

    def fake_run_daily(*, raw_history=None, market_date=None, runner=None, **kw):
        seen["raw_history"] = raw_history
        seen["market_date"] = market_date
        return {
            "schema_version": "p12.replay.replay.v1",
            "run_id": "r1",
            "market_date": market_date.isoformat() if market_date else None,
            "status": "INPUT_BLOCKED",
        }

    monkeypatch.setattr(eng_mod, "run_daily", fake_run_daily)

    def reader(day, cutoff, holdings, pending):
        return ENVELOPE

    engine = make_engine(
        state_dir=tmp_path / "state",
        sessions=[T, date(2026, 10, 9)],
        bars_by_day={},
        candidates_path=None,
        fixture_path=None,
        account_id="SYN-PAPER-A",
        e1_runner=_fake_runner(),
        history_reader=reader,
    )
    engine.materialize_day(T)
    receipt = engine.evening_run(rerun=True)
    assert receipt["status"] == "INPUT_BLOCKED"
    assert seen["raw_history"] is ENVELOPE
    assert seen["market_date"] == T


def _scenario_doc(tmp_path, **over):
    import json as _json

    doc = {
        "schema_version": "p12.e1-scenario.v1",
        "scenario": {
            "scenario_id": "spdb-20261008",
            "account_namespace": "historical-replay:spdb-20261008",
            "package_id": "replay-core-2026-10-08",
            "manifest_sha256": "sha256:" + "c" * 64,
            "account": {"account_id": "SYN-PAPER-A", "cash_available": "100000"},
            "policy": {"policy_id": "p11-d1b@1.0.0"},
            "facts_template": {"facts_id": "f"},
            "snapshot_id": "snap-1",
            "snapshot_manifest_digest": "sha256:" + "d" * 64,
            "p12_evidence": {"envelope": {"uri": "mem://env", "sha256": "e" * 64}},
        },
        "action_coverage": {"signed": [{"coverage_id": "b02:l:2026-10-08"}]},
        "exclusions": [{"exclusion_id": "737-x"}],
        "open_sessions": ["2026-10-09"],
    }
    doc.update(over)
    path = tmp_path / "scenario.json"
    path.write_text(_json.dumps(doc))
    return path


def test_load_e1_scenario_builds_runner(tmp_path, monkeypatch):
    from quanthus.p12_replay import e1_assembly as asm

    monkeypatch.setattr(
        _e1_rt, "load_e1_rule_assets", lambda root: {"assets": "fake"}
    )

    captured = {}

    def fake_read(day, cutoff, *, holdings, pending, source_map):
        captured["args"] = (day, holdings, pending, source_map is not None)
        return _Result()

    monkeypatch.setattr(asm, "read_history", fake_read)
    runner, reader = asm.load_e1_scenario(
        _scenario_doc(tmp_path), repo_root=tmp_path
    )
    assert isinstance(runner, E1ReplayRunner)
    env = reader(T, CUTOFF)
    assert env is ENVELOPE
    ctx = runner._context_supplier(T)
    assert ctx.scenario_id == "spdb-20261008"
    assert ctx.account["account_id"] == "SYN-PAPER-A"
    assert ctx.open_sessions == ("2026-10-09",)


def test_load_e1_scenario_blocks_missing_file(tmp_path):
    import pytest as _pt

    from quanthus.p12_replay import e1_assembly as asm

    with _pt.raises(OSError):
        asm.load_e1_scenario(tmp_path / "absent.json", repo_root=tmp_path)


def test_load_e1_scenario_blocks_wrong_schema(tmp_path):
    import json as _json

    import pytest as _pt

    from quanthus.p12_replay import e1_assembly as asm

    bad = tmp_path / "bad.json"
    bad.write_text(_json.dumps({"schema_version": "other"}))
    with _pt.raises(ValueError, match="p12.e1-scenario.v1"):
        asm.load_e1_scenario(bad, repo_root=tmp_path)


def test_load_e1_scenario_blocks_missing_scenario_field(tmp_path):
    import pytest as _pt

    from quanthus.p12_replay import e1_assembly as asm

    doc = _scenario_doc(tmp_path)
    # rewrite without account
    import json as _json
    d = _json.loads(doc.read_text())
    del d["scenario"]["account"]
    doc.write_text(_json.dumps(d))
    with _pt.raises(ValueError, match="account"):
        asm.load_e1_scenario(doc, repo_root=tmp_path)


def test_load_e1_scenario_blocks_missing_coverage(tmp_path):
    import json as _json

    import pytest as _pt

    from quanthus.p12_replay import e1_assembly as asm

    doc = _scenario_doc(tmp_path)
    d = _json.loads(doc.read_text())
    d["action_coverage"] = None
    doc.write_text(_json.dumps(d))
    with _pt.raises(ValueError, match="action_coverage"):
        asm.load_e1_scenario(doc, repo_root=tmp_path)


def test_load_e1_scenario_source_map_override(tmp_path, monkeypatch):
    import json as _json

    from quanthus.p12_replay import e1_assembly as asm

    monkeypatch.setattr(
        _e1_rt, "load_e1_rule_assets", lambda root: {"assets": "fake"}
    )
    seen = {}

    class _FakeMap:
        def __init__(self, **kw):
            seen["kw"] = kw

    monkeypatch.setattr(asm, "SourceMap", _FakeMap)
    doc = _scenario_doc(tmp_path)
    d = _json.loads(doc.read_text())
    d["source_map"] = {
        "base_members_path": "/tmp/base.json",
        "daily_members_path": "/tmp/daily.json",
        "base_package_root": "/srv/root",
    }
    doc.write_text(_json.dumps(d))
    from pathlib import Path as _P

    asm.load_e1_scenario(doc, repo_root=tmp_path)
    assert seen["kw"]["base_package_root"] == _P("/srv/root")
    assert seen["kw"]["base_members_path"] == _P("/tmp/base.json")

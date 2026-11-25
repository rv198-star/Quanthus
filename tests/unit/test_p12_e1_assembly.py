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
        "run_read_at": "2026-10-08T13:30:00Z",
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


def _fake_read_history(
    market_date, decision_cutoff, *, holdings, pending, source_map,
    replay_exclusion=None,
):
    return _Result()


def _build(monkeypatch, holdings=(), pending=(), evidence_dir=None):
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
        evidence_dir=evidence_dir,
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
    # p12_evidence is rebuilt from THIS run's coverage: the produced
    # envelope digest, run clocks and this run's standalone artefact refs.
    # The sealed source artefact/read/coverage refs are preserved
    # verbatim under sealed_source as provenance.
    from quanthus.event_library.contracts import sha256_digest

    ev = ctx.p12_evidence
    env_digest = sha256_digest(ENVELOPE).removeprefix("sha256:")
    assert ev["envelope"]["sha256"] == env_digest
    assert ev["envelope"]["uri"].startswith("mem://e1-envelope-")
    # the sealed v6 refs moved to sealed_source; live refs are this run's
    assert ev["sealed_source"]["envelope"]["uri"] == "mem://env"
    assert ev["read_receipt"]["uri"].startswith("mem://e1-read-receipt-")
    assert ev["coverage_receipt"]["uri"].startswith("mem://e1-coverage-")
    assert ev["member_consumption"] is COVERAGE["member_consumption"]
    assert ev["read_at"] == "2026-10-08T13:30:00Z"
    assert ev["source_clocks"]["run_read_at"] == "2026-10-08T13:30:00Z"
    assert ev["source_clocks"]["logical_decision_cutoff"] == CUTOFF.isoformat()
    assert ev["coverage"] is COVERAGE
    # member evidence carries ONLY this run's consumed members with clocks
    assert [m["member_id"] for m in ctx.member_evidence] == ["m-cal", "m-nov"]
    req_ids = ctx.required_member_ids
    assert req_ids == ("m-cal",)
    m0 = ctx.member_evidence[0]
    assert m0["package_key"] == "pkg::m-cal"
    assert m0["digest"] == "a" * 64
    # available_at is THIS run's consumption verify_at (the wall-clock
    # instant the member's sha256 finished re-hashing inside this read),
    # projected to a +00:00 offset for the loader.  The envelope's
    # supplier-declared visible_at is a source clock, never the run clock.
    assert m0["available_at"] == "2026-10-08T13:30:00+00:00"
    assert "verified_at" not in m0
    assert ctx.member_evidence[1]["available_at"] == "2026-10-08T13:30:01+00:00"
    assert m0["required"] is True


def test_history_reader_passes_holdings_and_pending(monkeypatch):
    seen = {}

    def fake(day, cutoff, *, holdings, pending, source_map, replay_exclusion=None):
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

    def fake_read(day, cutoff, *, holdings, pending, source_map, replay_exclusion=None):
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


def test_evidence_dir_writes_recomputable_artefacts(tmp_path, monkeypatch):
    """With evidence_dir the run's envelope/coverage/read-receipt land as
    standalone canonical-JSON files whose sha256 equals the declared
    digest - the auditor recomputes the binding from the file bytes."""
    import hashlib
    import json as _json

    from quanthus.event_library.contracts import sha256_digest

    ev_dir = tmp_path / "e1-evidence"
    runner, reader = _build(monkeypatch, evidence_dir=ev_dir)
    reader(T, CUTOFF)
    ctx = runner._context_supplier(T)
    ev = ctx.p12_evidence
    # the produced envelope file exists and its bytes hash to the digest
    env_file = ev_dir / ("e1-envelope-" + T.isoformat() + ".json")
    assert env_file.is_file()
    env_bytes = env_file.read_bytes()
    assert hashlib.sha256(env_bytes).hexdigest() == ev["envelope"]["sha256"]
    assert ev["envelope"]["uri"] == str(env_file)
    assert _json.loads(env_bytes) == ENVELOPE
    assert ev["envelope"]["sha256"] == sha256_digest(ENVELOPE).removeprefix(
        "sha256:"
    )
    # coverage + read-receipt also land as recomputable files
    for key, prefix in (
        ("coverage_receipt", "e1-coverage-"),
        ("read_receipt", "e1-read-receipt-"),
    ):
        uri = ev[key]["uri"]
        assert Path(uri).is_file(), key
        assert Path(uri).name.startswith(prefix)
        assert (
            hashlib.sha256(Path(uri).read_bytes()).hexdigest()
            == ev[key]["sha256"]
        )


def test_history_reader_forwards_replay_exclusion_pin(monkeypatch):
    """The #737 replay-exclusion pin assembled by the driver reaches
    read_history verbatim so a missing/wrong pin blocks before any member
    is opened (audit 749-09)."""
    captured = {}

    def fake(day, cutoff, *, holdings, pending, source_map, replay_exclusion=None):
        captured["replay_exclusion"] = replay_exclusion
        return _Result()

    monkeypatch.setattr(e1_assembly, "read_history", fake)
    monkeypatch.setattr(
        _e1_rt, "load_e1_rule_assets", lambda root: {"assets": "fake"}
    )
    pin = {"path": "/frozen/737.json", "sha256": "sha256:" + "9" * 64}
    _runner, reader = e1_assembly.build_e1_entry(
        repo_root=Path("/tmp/repo"),
        scenario=SCENARIO,
        action_coverage=ACTION_COVERAGE,
        replay_exclusion=pin,
    )
    reader(T, CUTOFF)
    assert captured["replay_exclusion"] == pin


def test_p12_evidence_carries_p1_exceptions_from_this_read(monkeypatch):
    """The specialised-P1 ledger read evidence rides p12_evidence beside
    the ordinary member_consumption so e1_runtime._p1_read_evidence can
    pair it (bypass when the key is absent)."""
    p1_reads = {
        "reads": [
            {"status": "FAILED", "reason_code": "X", "member_id": "pkg::P1-ledger"},
            {"status": "READ", "member_id": "pkg::P1-ledger", "sha256": "f" * 64},
        ]
    }

    class _Res:
        raw_history = ENVELOPE
        coverage = {**COVERAGE, "p1_exceptions": p1_reads}

    monkeypatch.setattr(
        e1_assembly,
        "read_history",
        lambda *a, **k: _Res(),
    )
    monkeypatch.setattr(
        _e1_rt, "load_e1_rule_assets", lambda root: {"assets": "fake"}
    )
    runner, reader = e1_assembly.build_e1_entry(
        repo_root=Path("/tmp/repo"),
        scenario=SCENARIO,
        action_coverage=ACTION_COVERAGE,
    )
    reader(T, CUTOFF)
    ctx = runner._context_supplier(T)
    assert ctx.p12_evidence["p1_exceptions"] is p1_reads
    assert ctx.p12_evidence["member_consumption"] is COVERAGE["member_consumption"]


def test_explicit_replay_exclusion_overrides_scenario_pin(tmp_path, monkeypatch):
    """The formal entry's explicitly-passed verified #737 binding wins
    over a conflicting scenario pin - the scenario can never redirect the
    read to a different self-consistent path/digest pair (audit ruling)."""
    import json as _json

    from quanthus.p12_replay import e1_assembly as asm

    captured = {}

    def fake(day, cutoff, *, holdings, pending, source_map, replay_exclusion=None):
        captured["replay_exclusion"] = replay_exclusion
        return _Result()

    monkeypatch.setattr(asm, "read_history", fake)
    monkeypatch.setattr(
        _e1_rt, "load_e1_rule_assets", lambda root: {"assets": "fake"}
    )
    doc = _scenario_doc(tmp_path)
    d = _json.loads(doc.read_text())
    d["replay_exclusion"] = {
        "path": "/attacker/other.json",
        "sha256": "sha256:" + "0" * 64,
    }
    doc.write_text(_json.dumps(d))
    verified = {"path": "/frozen/737.json", "sha256": "sha256:" + "9" * 64}
    _runner, reader = asm.load_e1_scenario(
        doc, repo_root=tmp_path, replay_exclusion=verified
    )
    reader(T, CUTOFF)
    assert captured["replay_exclusion"] == verified


def test_scenario_replay_exclusion_used_when_no_explicit_pin(tmp_path, monkeypatch):
    """A scenario-pinned #737 binding is honoured when the caller does not
    pass an explicit verified binding (lab path only)."""
    import json as _json

    from quanthus.p12_replay import e1_assembly as asm

    captured = {}

    def fake(day, cutoff, *, holdings, pending, source_map, replay_exclusion=None):
        captured["replay_exclusion"] = replay_exclusion
        return _Result()

    monkeypatch.setattr(asm, "read_history", fake)
    monkeypatch.setattr(
        _e1_rt, "load_e1_rule_assets", lambda root: {"assets": "fake"}
    )
    doc = _scenario_doc(tmp_path)
    d = _json.loads(doc.read_text())
    pin = {"path": "/frozen/737.json", "sha256": "sha256:" + "9" * 64}
    d["replay_exclusion"] = pin
    doc.write_text(_json.dumps(d))
    _runner, reader = asm.load_e1_scenario(doc, repo_root=tmp_path)
    reader(T, CUTOFF)
    assert captured["replay_exclusion"] == pin

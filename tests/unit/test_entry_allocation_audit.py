"""Synthetic-only observation checks through the actual allocator and P2 matcher."""

import hashlib
import json
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as N
from zoneinfo import ZoneInfo

import pytest

from quanthus.aggregator import GovernedAction
from quanthus.backtest.contracts import MarketSession, OpenExecutionState, UnitState
from quanthus.backtest.engine import BacktestEngine
from quanthus.backtest.policy import load_execution_policy
from quanthus.backtest.strategy_replay import StrategyReplayFrame
from quanthus.concentration import StaticIndustryMembership
from quanthus.contracts import SnapshotRef
from quanthus.expert_evaluation.serialization import canonical_json_bytes, digest_value
from quanthus.expert_strategy import formal_p2_replay as replay
from quanthus.expert_strategy.registry import ExpertStrategyRegistry
from quanthus.policy import load_policy
from quanthus.position_lifecycle import PositionStage, initialize_position_lifecycle

ROOT = Path(__file__).resolve().parents[2]
DAY = date(2020, 1, 2)
SH = ZoneInfo("Asia/Shanghai")


def at(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=SH)


def member(code, industry):
    return StaticIndustryMembership(code, industry, "synthetic-v1", "synthetic-source", "synthetic")


def candidate(n, code, *, score="0.90000000000", action=GovernedAction.TRIAL_ENTRY, close=None):
    return N(
        item=N(record=N(event_id=f"e{n}", identity=N(security_id=code)), panel_security_id=code),
        opportunity_id=f"opportunity-{n}",
        feature=None if close is None else N(close_price=Decimal(close)),
        decision=N(
            digest=digest_value({"synthetic": n}),
            target_maximum_position=None,
            action=N(
                final_action=action, aggregate_score=Decimal(score), reason_codes=("synthetic",)
            ),
        ),
    )


@pytest.fixture
def runtime(monkeypatch):
    r = replay.FormalP2ReplayRuntime.__new__(replay.FormalP2ReplayRuntime)
    r.repository_root = ROOT
    r.registry = ExpertStrategyRegistry(ROOT)
    r.p0_policy = load_policy(ROOT / "config/p0-policy.v1.json")
    r.engine = BacktestEngine(
        load_execution_policy(ROOT / "tests/fixtures/p2/execution-policy.synthetic.v1.json")
    )
    r.industry = N(
        artifact_digest="sha256:" + "1" * 64,
        by_security_id={
            code: member(code, sector)
            for code, sector in (("600001", "a"), ("600002", "b"), ("600003", "c"))
        },
    )
    r.parent_event_run_digest = "sha256:" + "2" * 64
    r.decision_start = r.decision_end = DAY
    r.execution_end = date(2020, 1, 3)
    snap = SnapshotRef(DAY, at(DAY, 15), "synthetic-only", "synthetic-snapshot")
    sessions = tuple(
        MarketSession(
            r.execution_end,
            at(r.execution_end, 9, 30),
            at(r.execution_end, 15),
            code,
            Decimal("10.00000000000"),
            Decimal("10.10000000000"),
            OpenExecutionState.BUY_BLOCKED_LIMIT_UP
            if code == "600002"
            else OpenExecutionState.EXECUTABLE,
            "synthetic-only",
            "synthetic-execution",
        )
        for code in r.industry.by_security_id
    )
    r.frames = (StrategyReplayFrame(snap, at(DAY, 15, 1), r.execution_end, sessions),)
    r.session = N(
        reader=N(
            bundle=N(snapshot=snap),
            data_content_digest="sha256:" + "3" * 64,
            data_manifest_sha256="sha256:" + "4" * 64,
        )
    )
    r.panel = N(report=N(digest="sha256:" + "5" * 64))
    candidates = (
        candidate(3, "600003", score="0.8", close="10.00000000000"),
        candidate(2, "600002", close="10.00000000000"),
        candidate(1, "600001", close="10.00000000000"),
        candidate(4, "600001", action=GovernedAction.NO_ACTION),
    )
    monkeypatch.setattr(r, "_entry_candidates", lambda *args: candidates)
    return r


def test_optional_audit_preserves_every_legacy_byte_evidence_and_allocator_call_count(
    runtime,
    monkeypatch,
    tmp_path,
):
    computed = []
    original = replay.allocate_entry_reservations

    def once(*args, **kwargs):
        result = original(*args, **kwargs)
        computed.append(result)
        return result

    monkeypatch.setattr(replay, "allocate_entry_reservations", once)
    started = datetime.now(timezone.utc)
    off_evidence, off = runtime.run_variant(None, tmp_path / "off")
    assert len(computed) == 1
    on_evidence, on = runtime.run_variant(None, tmp_path / "on", allocation_audit=True)
    assert len(computed) == 2  # one call per frame in each run
    assert on_evidence == off_evidence
    assert set(on) == set(off) | {"entry-allocation-audit.json"}
    assert all(on[k] == v for k, v in off.items())
    audit = json.loads(on["entry-allocation-audit.json"])
    assert audit.pop("audit_digest") == digest_value(audit)
    assert audit["legacy_replay_digest"] == off_evidence.replay_digest
    assert audit["legacy_artifacts_sha256"] == {
        k: hashlib.sha256(v).hexdigest() for k, v in off.items()
    }
    assert audit["identity"]["run"]["data_manifest_sha256"].endswith("4" * 64)
    assert audit["identity"]["industry_artifact_digest"].endswith("1" * 64)
    assert "config/p0-policy.v1.json" in audit["identity"]["code_and_config_sha256"]
    assert audit["execution_authorized"] is audit["historical_PIT_claim"] is False
    frame = audit["frames"][0]
    assert frame["allocation"] == computed[-1].to_dict()
    assert frame["allocation_digest"] == computed[-1].digest
    assert frame["market_date"] == "2020-01-02"
    assert frame["decision_at"] == "2020-01-02T15:01:00+08:00"
    assert frame["knowledge_cutoff"] == "2020-01-02T15:00:00+08:00"
    assert started <= datetime.fromisoformat(frame["observed_at"]) <= datetime.now(timezone.utc)
    allocations = frame["allocation"]["decisions"]
    assert [d["security_code"] for d in allocations] == ["600001", "600002", "600003"]
    assert [d["priority_rank"] for d in allocations] == [1, 2, 3]
    assert [d["reason_codes"] for d in allocations] == [
        ["entry_unit_reserved"],
        ["entry_unit_reserved"],
        ["daily_open_cap"],
    ]
    assert [d["disposition"] for d in allocations] == ["reserved", "reserved", "rejected"]
    assert frame["entries"][-1]["allocation_decision_id"] is None
    assert frame["entries"][0]["entry_key"] == "entry:e3"
    decisions = json.loads(off["decisions.json"])
    assert sum(d["filled"] for d in decisions) == 1
    events = json.loads(off["engine-events.json"])
    assert any(e["reason_code"] == "buy_blocked_limit_up" for e in events)
    assert len(json.loads(off["final-ledgers.json"])) > 0


def test_audit_no_empty_unit_keeps_rejected_reason_not_legacy_allocated_flag(runtime):
    audit, decisions = [], []
    c = candidate(1, "600001")
    runtime._execute_frame(
        0,
        runtime.frames[0],
        {},
        (c,),
        Decimal("10000"),
        {"maximum_active_units": 5},
        None,
        decisions,
        [],
        [],
        allocation_audit=audit,
    )
    result = audit[0]["allocation"]["decisions"][0]
    assert result["reason_codes"] == ["no_empty_unit"]
    assert result["considered_unit_id"] is None and result["disposition"] == "rejected"
    assert decisions[0]["allocated"] is True and decisions[0]["filled"] is False
    assert audit[0]["available_units"] == []


def test_audit_industry_rejection_retains_capacity_evidence_and_next_reservation(
    runtime, monkeypatch
):
    def unit(name, code=None):
        ledger = UnitState.empty(unit_id=name, cash=Decimal("10000"))
        lifecycle = initialize_position_lifecycle(ledger_state=ledger, as_of=at(DAY, 15))
        if code:
            lifecycle = replace(
                lifecycle, stage=PositionStage.TRIAL, security_code=code, opportunity_id=name
            )
        return replay._UnitRuntime(ledger, lifecycle, formal_security_id=code, opportunity_id=name)

    runtime.industry.by_security_id["600003"] = member("600003", "a")
    units = {
        "active-1": unit("active-1", "600001"),
        "active-2": unit("active-2", "600003"),
        "empty": unit("empty"),
    }
    monkeypatch.setattr(runtime, "_mark", lambda *args: None)
    audit = []
    runtime._execute_frame(
        0,
        runtime.frames[0],
        units,
        (candidate(1, "600001"), candidate(2, "600002", score="0.8")),
        Decimal("10000"),
        {"maximum_active_units": 5, "base_trial_position_fraction": "0.2"},
        None,
        [],
        [],
        [],
        allocation_audit=audit,
    )
    first, second = audit[0]["allocation"]["decisions"]
    assert first["reason_codes"] == ["industry_cap"] and first["capacity"] is not None
    assert second["disposition"] == "reserved" and second["considered_unit_id"] == "empty"
    assert len(audit[0]["active_lifecycle_digests"]) == 2


def test_observation_is_detached_from_allocator_objects(runtime):
    audit = []
    runtime._execute_frame(
        0,
        runtime.frames[0],
        {},
        (candidate(1, "600001"),),
        Decimal("10000"),
        {"maximum_active_units": 5},
        None,
        [],
        [],
        [],
        allocation_audit=audit,
    )
    assert json.loads(canonical_json_bytes(audit)) == audit
    assert (
        audit[0]["entries"][0]["formal_decision_digest"] == candidate(1, "600001").decision.digest
    )


def test_nonboolean_opt_in_is_rejected_before_runtime_access(tmp_path):
    r = replay.FormalP2ReplayRuntime.__new__(replay.FormalP2ReplayRuntime)
    with pytest.raises(ValueError, match="allocation_audit must be boolean"):
        r.run_variant(None, tmp_path, allocation_audit="false")

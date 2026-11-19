"""Exercise the R11-only capital effects against real expert and P2 decisions."""

import json
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from quanthus.aggregator import GovernedAction
from quanthus.backtest.contracts import MarketSession, OpenExecutionState
from quanthus.backtest.engine import BacktestEngine
from quanthus.backtest.policy import load_execution_policy
from quanthus.backtest.strategy_replay import StrategyReplayFrame
from quanthus.concentration import StaticIndustryMembership
from quanthus.contracts import SnapshotRef
from quanthus.expert_evaluation.serialization import ExpertEvaluationError
from quanthus.expert_strategy import ExpertStrategyRegistry, FormalStrategyRunner
from quanthus.expert_strategy import formal_p2_replay as replay
from quanthus.expert_strategy.r11_historical import HistoricalBuyBound, R11HistoricalReplayRuntime
from quanthus.policy import load_policy
from tests.unit.test_entry_allocation_audit import at
from tests.unit.test_formal_expert_strategy import _context, _evidence

ROOT = Path(__file__).resolve().parents[2]
DAY = date(2020, 1, 6)
SEC = "security-cn-000001"
PANEL_SEC = "000001"
HASH = "sha256:" + "1" * 64


def fixture_runtime(cls, monkeypatch, *, bound=None, open_price=Decimal("10")):
    runtime = cls.__new__(cls)
    runtime.repository_root = ROOT
    runtime.registry = ExpertStrategyRegistry(ROOT)
    runtime.p0_policy = load_policy(ROOT / "config/p0-policy.v1.json")
    runtime.engine = BacktestEngine(
        load_execution_policy(ROOT / "config/p2-execution-policy.research.v1.json")
    )
    runtime.industry = SimpleNamespace(
        artifact_digest=HASH,
        by_security_id={SEC: StaticIndustryMembership(SEC, "I1", "v1", "source", "synthetic")},
    )
    runtime.parent_event_run_digest = HASH
    runtime.decision_start, runtime.decision_end, runtime.execution_end = (
        DAY,
        DAY + timedelta(days=4),
        DAY + timedelta(days=5),
    )
    frames = []
    for i, close in enumerate(("10", "10", "10", "10.13", "12")):
        day = DAY + timedelta(days=i)
        following = day + timedelta(days=1)
        source = SnapshotRef(day, at(day, 15), "fixture", f"snapshot:{day}")
        session = MarketSession(
            following,
            at(following, 9, 30),
            at(following, 15),
            PANEL_SEC,
            open_price if i == 0 else Decimal(close),
            Decimal(close),
            OpenExecutionState.EXECUTABLE,
            "fixture",
            f"execution:{following}",
        )
        frames.append(StrategyReplayFrame(source, at(day, 15, 1), following, (session,)))
    runtime.frames = tuple(frames)
    runtime.session = SimpleNamespace(
        reader=SimpleNamespace(
            bundle=SimpleNamespace(snapshot=frames[0].snapshot),
            data_content_digest=HASH,
            data_manifest_sha256=HASH,
        )
    )
    runtime.panel = SimpleNamespace(
        report=SimpleNamespace(digest=HASH),
        feature=lambda *args: None,
    )
    runtime.feature_registry = None
    if isinstance(runtime, R11HistoricalReplayRuntime):
        runtime._consumer_receipt_id = "synthetic-fixture"
        runtime._buy_bounds = {(DAY, SEC): bound} if bound is not None else {}

    def entries(frame, runner: FormalStrategyRunner, _intervention):
        if frame.snapshot.market_date != DAY:
            return ()
        event_id = "event:formal-strategy-test"
        approved = runner.evaluate_entry(
            event_id=event_id,
            context=_context(),
            trend_state="stable",
            entry_evidence_ready=True,
        )
        veto = runner.evaluate_entry(
            event_id="fixture-veto",
            context=replace(
                _context(volatility=Decimal("0.9"), rsi=Decimal("90")),
                additional_evidence_refs=(
                    _evidence("fixture-veto"),
                    _evidence("trend-observation:fixture-veto"),
                ),
            ),
            trend_state="stable",
            entry_evidence_ready=True,
        )
        assert approved.action.final_action is GovernedAction.TRIAL_ENTRY
        assert veto.action.final_action is GovernedAction.NO_ACTION
        return tuple(
            replay._EntryCandidate(
                SimpleNamespace(
                    record=SimpleNamespace(
                        event_id=decision.event_id, identity=SimpleNamespace(security_id=SEC)
                    ),
                    panel_security_id=PANEL_SEC,
                ),
                SimpleNamespace(close_price=Decimal("10")),
                decision,
                decision.event_id,
            )
            for decision in (approved, veto)
        )

    monkeypatch.setattr(runtime, "_entry_candidates", entries)
    monkeypatch.setattr(
        runtime,
        "_held_event",
        lambda _, frame: SimpleNamespace(
            event_id="event:formal-strategy-test", day=frame.snapshot.market_date
        ),
    )

    def held_context(*, event, position_facts, **_):
        offset = (event.day - DAY).days
        evidence = position_facts.evidence
        if offset < 3:
            return _context(position_state="held", position_evidence=evidence)
        if offset == 3:
            return _context(
                volatility=Decimal("0.55"), position_state="held", position_evidence=evidence
            )
        return _context(
            volatility=Decimal("0.71"),
            rsi=Decimal("86"),
            position_state="held",
            position_evidence=evidence,
        )

    monkeypatch.setattr(replay, "build_formal_expert_context", held_context)
    return runtime


def test_shared_original_and_candidate_paths_use_real_matcher(monkeypatch, tmp_path):
    runtime = fixture_runtime(
        R11HistoricalReplayRuntime,
        monkeypatch,
        bound=HistoricalBuyBound(Decimal("10.10"), at(DAY, 15), "fixture-bound"),
    )
    baseline = fixture_runtime(replay.FormalP2ReplayRuntime, monkeypatch)
    _, control = runtime.run_variant(None, tmp_path / "arm-control")
    _, legacy = baseline.run_variant(None, tmp_path / "legacy")
    assert control == legacy
    _, candidate = runtime.run_candidate(tmp_path / "candidate")
    events = json.loads(candidate["engine-events.json"])
    fills = [row for row in events if row["event_type"] == "fill"]
    assert [row["payload"]["side"] for row in fills] == ["buy", "sell", "sell"]
    assert [row["payload"]["quantity"] for row in fills] == [1900, 900, 1000]
    assert fills[0]["event_time"] == at(DAY + timedelta(days=1), 9, 30).isoformat()
    decisions = json.loads(candidate["decisions.json"])
    assert [row["final_action"] for row in decisions] == [
        "trial_entry",
        "no_action",
        "no_action",
        "no_action",
        "reduce",
        "full_exit",
    ]
    trace = json.loads(candidate["r11-capital-trace.json"])
    transfer = trace["events"][-1]
    assert transfer["kind"] == "shared_reserve_settlement"
    assert transfer["reserve"] == "2052.53"
    assert json.loads(candidate["reconciliation.json"])["ending_equity"] == "502052.53"
    assert all(
        row["external_pool"] == "0.00" for row in json.loads(candidate["final-ledgers.json"])
    )


def test_late_bound_cannot_use_next_open(monkeypatch, tmp_path):
    runtime = fixture_runtime(
        R11HistoricalReplayRuntime,
        monkeypatch,
        bound=HistoricalBuyBound(Decimal("10.10"), at(DAY, 15, 1), "late-bound"),
    )
    _, candidate = runtime.run_candidate(tmp_path)
    trace = json.loads(candidate["r11-capital-trace.json"])
    assert trace["events"][0]["bound_evidence"] is None
    assert trace["events"][0]["decision"]["q"] == 0
    assert json.loads(candidate["reconciliation.json"])["fill_count"] == 0


def test_realized_fill_outside_bound_invalidates_candidate(monkeypatch, tmp_path):
    runtime = fixture_runtime(
        R11HistoricalReplayRuntime,
        monkeypatch,
        bound=HistoricalBuyBound(Decimal("10.10"), at(DAY, 15), "fixture-bound"),
        open_price=Decimal("11"),
    )
    with pytest.raises(ExpertEvaluationError, match="decision-time bound"):
        runtime.run_candidate(tmp_path)


def test_wrong_admitted_manifest_rejected_before_any_read(monkeypatch):
    calls = []
    monkeypatch.setattr(
        replay.FormalP2ReplayRuntime,
        "_load_event_population",
        lambda self, root: calls.append("events")
        or (_ for _ in ()).throw(RuntimeError("EVENT_LOAD")),
    )
    monkeypatch.setattr(
        replay.FormalP2ReplayRuntime,
        "_open_data_session",
        lambda self: calls.append("open")
        or (_ for _ in ()).throw(RuntimeError("PROVIDER_OPEN")),
    )
    with pytest.raises(ValueError, match="admitted data"):
        R11HistoricalReplayRuntime(
            ROOT,
            provider_config="config/replay-data-provider.development.v2.json",
            parent_event_run_digest="sha256:" + "0" * 64,
            buy_bounds={},
            admitted_data_manifest_sha256="sha256:" + "f" * 64,
            consumer_receipt_id="receipt",
            excluded_security_ids=frozenset({SEC}),
        )
    assert calls == []


def test_admitted_manifest_matches_pinned_canonical_lineage():
    from quanthus.backtest.data_provider import (
        ReplayDataProvider,
        load_replay_data_provider_config,
    )

    provider = ReplayDataProvider(
        load_replay_data_provider_config(
            ROOT / "config/replay-data-provider.development.v2.json"
        )
    )
    assert provider.admitted_manifest() == (
        "sha256:2821b5923bd1525642d9b4479ed2bd35281203c1f117acf6ba0819c606e4b300"
    )

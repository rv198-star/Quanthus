from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.backtest.contracts import OpenExecutionState
from quanthus.backtest.snapshot_clock import SnapshotReplayClock
from quanthus.event_library.contracts import SplitAssignment, event_record_from_dict
from quanthus.event_library.rule_card import load_candidate_rule_card
from quanthus.event_library.scan import scan_forward_candidate_series
from quanthus.forward_validation import snapshot_replay as replay
from quanthus.forward_validation.paper_ledger import initial_paper_ledger
from quanthus.forward_validation.paper_projection import (
    _run_spec,
    paper_pending_intent_from_dict,
    project_paper_intent,
    settle_paper_intent,
)
from quanthus.forward_validation.serialization import digest_value
from quanthus.pipelines.securities_warehouse.base22 import file_sha
from quanthus.pipelines.securities_warehouse.base22_sources import _fingerprint, _tree
from tests.unit.test_forward_validation_paper_ledger import _decision, _policy, _session
from tests.unit.test_p6_base24_runtime import runtime_source as runtime_source
from tests.unit.test_p6_base24_runtime import view as view

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config/forward-validation/runtime/p6-fixed-snapshot-replay.v1.json"
CUTOFF = datetime.fromisoformat("2026-10-22T13:00:14+00:00")


def test_two_clocks_keep_actual_knowledge_and_do_not_open_old_authority():
    c = SnapshotReplayClock(date(2026, 7, 7), CUTOFF)
    assert c.knowledge_cutoff == CUTOFF
    assert c.simulated_close.isoformat() == "2026-07-07T15:00:00+08:00"
    assert SnapshotReplayClock.from_json(c.to_json()) == c
    for field in (
        "historical_pit_claim",
        "economic_validation_claim",
        "prospective_observation",
        "actual_execution_authorized",
    ):
        raw = c.to_dict()
        raw[field] = True
        with pytest.raises(ValueError):
            SnapshotReplayClock.from_json(json.dumps(raw))
        raw[field] = 0
        with pytest.raises(ValueError):
            SnapshotReplayClock.from_json(json.dumps(raw))


@pytest.mark.parametrize(
    "day,cutoff",
    [
        (date(2025, 7, 7), CUTOFF),
        (date(2024, 7, 7), CUTOFF),
        (date(2026, 7, 7), datetime(2026, 10, 22)),
        (date(2026, 7, 7), datetime.fromisoformat("2026-07-07T15:00:00+08:00")),
    ],
)
def test_mechanism_clock_rejects_blind_unknown_or_false_two_clock(day, cutoff):
    with pytest.raises(ValueError):
        SnapshotReplayClock(day, cutoff)


def mechanism_intent():
    policy = _policy()
    ledger = initial_paper_ledger(policy)
    decision = _decision(market_date=date(2026, 9, 14), action="trial_entry")
    decision = replace(
        decision, snapshot=replace(decision.snapshot, knowledge_cutoff=CUTOFF), decided_at=CUTOFF
    )
    clock = SnapshotReplayClock(decision.snapshot.market_date, CUTOFF)
    intent = project_paper_intent(
        policy=policy, ledger=ledger, decision=decision, snapshot_replay_clock=clock
    )
    assert intent is not None
    return policy, ledger, intent


def test_actual_p2_fill_uses_simulated_next_open_without_backdating_snapshot():
    policy, ledger, intent = mechanism_intent()
    assert intent.signal.snapshot.knowledge_cutoff == CUTOFF
    assert (
        intent.signal.decision_at == SnapshotReplayClock(date(2026, 9, 14), CUTOFF).simulated_close
    )
    assert paper_pending_intent_from_dict(intent.to_dict()) == intent
    done = settle_paper_intent(
        policy=policy, ledger=ledger, intent=intent, sessions=(_session(date(2026, 9, 15)),)
    )
    assert done.fill_count == 1 and done.reconciliation["balanced"]
    assert done.ledger.unit("paper-unit-01").settled_quantity == 3000
    # Removing explicit simulation mode cannot launder this into old PIT semantics.
    with pytest.raises(ValueError, match="knowledge_cutoff"):
        replace(intent.signal, snapshot_replay_clock_json=None)


def test_mechanism_signal_is_rejected_by_official_engine_and_same_day_execution():
    from quanthus.backtest.engine import BacktestEngine, BacktestInputError

    policy, ledger, intent = mechanism_intent()
    engine = BacktestEngine(policy.execution_policy)
    with pytest.raises(BacktestInputError, match="official run"):
        engine.run_buy_session(
            run=replace(_run_spec(policy, intent), official=True),
            signal=intent.signal,
            session=_session(date(2026, 9, 15)),
            opening_state=ledger.unit("paper-unit-01"),
        )
    with pytest.raises(BacktestInputError, match="later market session"):
        settle_paper_intent(
            policy=policy, ledger=ledger, intent=intent, sessions=(_session(date(2026, 9, 14)),)
        )


@pytest.mark.parametrize(
    "state",
    [
        OpenExecutionState.MISSING_EVIDENCE,
        OpenExecutionState.SUSPENDED,
        OpenExecutionState.BUY_BLOCKED_LIMIT_UP,
    ],
)
def test_real_p2_missing_invalid_and_limit_evidence_still_blocks_entry(state):
    policy, ledger, intent = mechanism_intent()
    done = settle_paper_intent(
        policy=policy,
        ledger=ledger,
        intent=intent,
        sessions=(_session(date(2026, 9, 15), state=state),),
    )
    assert done.fill_count == 0 and done.ledger.unit("paper-unit-01").settled_quantity == 0
    assert done.reconciliation["balanced"]


def test_candidate_scanner_uses_explicit_later_snapshot_and_round_trips_event():
    from datetime import timedelta

    from tests.unit.test_event_scan import _row

    dates = tuple(date(2026, 6, 1) + timedelta(days=i) for i in range(30))
    rows = [
        _row(
            security="000001",
            session_date=d,
            range_fraction=Decimal("0.01"),
            turnover=Decimal("100"),
        )
        for d in dates
    ]
    rows = [replace(r, knowledge_cutoff=CUTOFF) for r in rows]
    rows[24] = replace(rows[24], high_price=Decimal("100.5"), turnover=Decimal("50"))
    rows[25] = replace(rows[25], high_price=Decimal("102"), turnover=Decimal("200"))
    policy = replay.SnapshotScanPolicy(
        dates[0], dates[25], dates[25], dates[-1], CUTOFF, digest_value({"synthetic": True})
    )
    result = scan_forward_candidate_series(
        [rows],
        trading_dates=dates,
        snapshot_id="fixed-test",
        snapshot_manifest_digest="sha256:" + "a" * 64,
        card=load_candidate_rule_card(
            ROOT / "config/p4-candidate-price-volume-transition.research.v1.json"
        ),
        split_policy=policy,
        source_policy=policy,
    )
    assert len(result.candidates) == 1
    event = result.candidates[0].record
    assert (
        event.knowledge_cutoff == CUTOFF
        and event.split_assignment == SplitAssignment.SNAPSHOT_REPLAY
    )
    assert event.decision_at == CUTOFF
    assert event_record_from_dict(event.to_dict()) == event
    with pytest.raises(ValueError):
        replace(event, split_assignment=SplitAssignment.PROSPECTIVE)


@pytest.fixture
def synthetic_history(runtime_source, tmp_path, monkeypatch):
    context, _, _, industry = runtime_source
    source = context.root
    # Reuse the complete composed source schema. All rows below are disposable
    # synthetic fixtures; no real calendar, prices or P10 authority is asserted.
    bars_path = source / "source/tables/daily_ohlcv_none.parquet"
    bars = pq.read_table(bars_path)
    rows = bars.to_pylist()
    dates = [r["date"] for r in rows]
    for i, r in enumerate(rows):
        scale = Decimal("0.005") if i == 119 else Decimal("0.02") if i == 120 else Decimal("0.01")
        for prefix in ("", "dwd_"):
            for field, value in (
                ("open", Decimal("100")),
                ("low", Decimal("100")),
                ("high", Decimal("100") * (1 + scale)),
                ("close", Decimal("100")),
            ):
                name = field if not prefix else prefix + field + "_decimal"
                r[name] = float(value) if not prefix else value
        r["amount"] = 50 if i == 119 else 200 if i == 120 else 100
        r["dwd_turnover_decimal"] = Decimal(str(r["amount"]))
    pq.write_table(pa.Table.from_pylist(rows, schema=bars.schema), bars_path)
    limit = source / "source/tables/price_limit_execution_daily.parquet"
    t = pq.read_table(limit)
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    **r,
                    "price_reference": Decimal("100"),
                    "upper_limit_price": Decimal("110"),
                    "lower_limit_price": Decimal("90"),
                    "observed_tradestatus": "trading",
                    "entry_policy_allows": True,
                    "is_executable": True,
                }
                for r in t.to_pylist()
            ],
            schema=t.schema,
        ),
        limit,
    )
    raw, plan = replay.load_plan(ROOT, CONFIG)
    plan["scope"].update(start=str(dates[0]), end=str(dates[-1]))
    plan["decisions"] = {"start": str(dates[120]), "end": str(dates[124]), "sessions": 5}
    windows = {
        "all_sessions": list(map(str, dates)),
        "warmup": list(map(str, dates[:120])),
        "decisions": list(map(str, dates[120:125])),
        "smoke": list(map(str, dates[120:123])),
        "settlement_tail": list(map(str, dates[125:])),
        "report_slices": {},
    }
    files = {
        r: {"sha256": file_sha(source / r), "bytes": (source / r).stat().st_size}
        for r in _tree(source)
    }
    meta = {"files": files, "built_at": "2026-10-01T00:00:00+00:00"}
    before = {r: _fingerprint(source / r) for r in _tree(source)}
    monkeypatch.setattr(replay, "load_plan", lambda *_: (raw, plan))
    monkeypatch.setattr(replay, "verify_source", lambda *_: (meta, before))
    monkeypatch.setattr(replay, "bind_calendar", lambda *_: windows)
    out = tmp_path / "history"
    replay.prepare_history(
        source=source,
        output=out,
        repository=ROOT,
        config=CONFIG,
        industry=industry,
        industry_sha256=file_sha(industry),
        knowledge_cutoff=CUTOFF,
        code_revision="a" * 40,
    )
    return out, source, before


def test_actual_shared_history_features_scanner_experts_and_continuous_ledger(
    synthetic_history,
    tmp_path,
):
    history, source, before = synthetic_history
    m, _ = replay.open_history(history)
    assert m["physical_history_build_count"] == 1
    preserved = pq.read_table(history / "tables/daily_ohlcv_none.parquet")
    assert (
        preserved["dwd_bar_knowledge_from"].to_pylist()
        == pq.read_table(source / "source/tables/daily_ohlcv_none.parquet")[
            "dwd_bar_knowledge_from"
        ].to_pylist()
    )
    out = tmp_path / "run"
    result = replay.run_replay(history=history, output=out, repository=ROOT, config=CONFIG)
    assert result["processed_sessions"] == 10 and result["decision_sessions"] == 5
    assert result["counters"]["candidates"] >= 1
    assert result["all_ledgers_balanced"]
    assert result["shared_builds"] == {"feature_panel": 1, "candidate_scan": 1, "frame_reader": 1}
    safe = [json.loads(p.read_text()) for p in sorted((out / "cycles").glob("*.safe.json"))]
    for previous, current in zip(safe, safe[1:], strict=False):
        assert previous["ledger_after"] == current["ledger_before"]
    assert all(r["new_intents"] == 0 for r in safe[5:] if r["held_reviews"] == 0)
    assert not result["claims"]["historical_pit"] and not result["claims"]["economic_validation"]
    assert all(_fingerprint(source / r) == f for r, f in before.items())
    with pytest.raises(ValueError, match="output_exists"):
        replay.run_replay(history=history, output=out, repository=ROOT, config=CONFIG)


@pytest.mark.parametrize("mutation", ["missing", "corrupt", "extra"])
def test_history_corruption_fails_before_output_without_old_source_fallback(
    synthetic_history,
    tmp_path,
    mutation,
):
    history, _, _ = synthetic_history
    p = history / "candidate-input.parquet"
    if mutation == "missing":
        p.unlink()
    elif mutation == "corrupt":
        p.write_bytes(b"broken")
    else:
        (history / "unexpected").write_text("")
    with pytest.raises(ValueError):
        replay.run_replay(
            history=history, output=tmp_path / "no-run", repository=ROOT, config=CONFIG
        )
    assert not (tmp_path / "no-run").exists()


def test_smoke_and_full_share_prerequisites_then_independent_rebuild_matches(
    synthetic_history,
    tmp_path,
    monkeypatch,
):
    import shutil
    import socket

    history, source, _ = synthetic_history
    # Only disposable synthetic sources are removed. Production has no such operation.
    shutil.rmtree(source)

    def no_network(*args, **kwargs):
        raise AssertionError("source network is forbidden")

    monkeypatch.setattr(socket, "create_connection", no_network)
    shared = replay.SharedSnapshotReplay()
    try:
        smoke = replay.run_replay(
            history=history,
            output=tmp_path / "smoke",
            repository=ROOT,
            config=CONFIG,
            smoke=True,
            shared=shared,
        )
        full = replay.run_replay(
            history=history, output=tmp_path / "full", repository=ROOT, config=CONFIG, shared=shared
        )
    finally:
        shared.close()
    assert smoke["shared_disposition"] == "build" and full["shared_disposition"] == "reuse"
    assert all(n == 0 for n in full["shared_builds"].values())
    assert full["shared_prerequisite_binding"] == smoke["shared_prerequisite_binding"]
    for path in (tmp_path / "smoke/cycles").glob("*.private.json"):
        assert path.read_bytes() == (tmp_path / "full/cycles" / path.name).read_bytes()
    replay.run_replay(
        history=history, output=tmp_path / "independent", repository=ROOT, config=CONFIG
    )
    assert (tmp_path / "full/final-state.private.json").read_bytes() == (
        tmp_path / "independent/final-state.private.json"
    ).read_bytes()
    for p in (tmp_path / "full/cycles").glob("*.private.json"):
        assert p.read_bytes() == (tmp_path / "independent/cycles" / p.name).read_bytes()

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from quanthus.backtest.contracts import MarketSession, OpenExecutionState
from quanthus.contracts import SnapshotRef
from quanthus.forward_validation.paper_ledger import initial_paper_ledger, load_paper_policy
from quanthus.forward_validation.paper_projection import (
    SealedAggregateDecision,
    project_paper_intent,
    settle_paper_intent,
)

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "config/forward-validation/paper/p6-paper-lane.v1.json"
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _decision(day: date, *, action: str, close: str, digest_char: str) -> SealedAggregateDecision:
    snapshot = SnapshotRef(
        market_date=day,
        knowledge_cutoff=datetime(day.year, day.month, day.day, 15, 0, tzinfo=SHANGHAI),
        source_revision=f"acceptance-source-{day.isoformat()}",
        snapshot_id=f"acceptance-snapshot-{day.isoformat()}",
    )
    return SealedAggregateDecision(
        decision_digest="sha256:" + digest_char * 64,
        snapshot=snapshot,
        decided_at=datetime(day.year, day.month, day.day, 15, 5, tzinfo=SHANGHAI),
        security_code="000001",
        unit_id="paper-unit-01",
        final_action=action,
        aggregate_score=Decimal("0.75"),
        decision_version="candidate.p4-trend-risk@1.0.1",
        decision_close=Decimal(close),
        target_maximum_position=Decimal("0.30") if action == "trial_entry" else Decimal("0"),
        reason_codes=("acceptance_frozen_decision",),
    )


def _session(day: date, open_price: str, close_price: str, suffix: str) -> MarketSession:
    return MarketSession(
        session_date=day,
        open_at=datetime(day.year, day.month, day.day, 9, 30, tzinfo=SHANGHAI),
        close_at=datetime(day.year, day.month, day.day, 15, 0, tzinfo=SHANGHAI),
        security_code="000001",
        open_price=Decimal(open_price),
        close_price=Decimal(close_price),
        open_execution_state=OpenExecutionState.EXECUTABLE,
        source_revision=f"acceptance-execution-{suffix}",
        snapshot_id=f"acceptance-execution-snapshot-{suffix}",
    )


def _round_trip() -> tuple[dict[str, object], dict[str, object]]:
    policy = load_paper_policy(POLICY, root=ROOT)
    initial = initial_paper_ledger(policy)

    entry_intent = project_paper_intent(
        policy=policy,
        ledger=initial,
        decision=_decision(date(2026, 9, 14), action="trial_entry", close="10.00", digest_char="c"),
    )
    assert entry_intent is not None
    entry = settle_paper_intent(
        policy=policy,
        ledger=initial,
        intent=entry_intent,
        sessions=(_session(date(2026, 9, 15), "10.00", "10.50", "entry"),),
    )

    exit_intent = project_paper_intent(
        policy=policy,
        ledger=entry.ledger,
        decision=_decision(date(2026, 9, 15), action="full_exit", close="10.50", digest_char="d"),
    )
    assert exit_intent is not None
    exit_result = settle_paper_intent(
        policy=policy,
        ledger=entry.ledger,
        intent=exit_intent,
        sessions=(_session(date(2026, 9, 16), "11.00", "11.20", "exit"),),
    )
    return entry.to_dict(), exit_result.to_dict()


def test_paper_round_trip_is_deterministic_isolated_and_non_compounding() -> None:
    first_entry, first_exit = _round_trip()
    second_entry, second_exit = _round_trip()

    assert first_entry == second_entry
    assert first_exit == second_exit
    assert first_entry["authority"] == "paper"
    assert first_exit["authority"] == "paper"
    assert first_entry["actual_execution_authorized"] is False
    assert first_exit["actual_execution_authorized"] is False
    assert first_entry["fill_count"] == 1
    assert first_exit["fill_count"] == 1

    policy = load_paper_policy(POLICY, root=ROOT)
    initial = initial_paper_ledger(policy)
    entry_intent = project_paper_intent(
        policy=policy,
        ledger=initial,
        decision=_decision(date(2026, 9, 14), action="trial_entry", close="10.00", digest_char="c"),
    )
    assert entry_intent is not None
    entry = settle_paper_intent(
        policy=policy,
        ledger=initial,
        intent=entry_intent,
        sessions=(_session(date(2026, 9, 15), "10.00", "10.50", "entry"),),
    )
    exit_intent = project_paper_intent(
        policy=policy,
        ledger=entry.ledger,
        decision=_decision(date(2026, 9, 15), action="full_exit", close="10.50", digest_char="d"),
    )
    assert exit_intent is not None
    closed = settle_paper_intent(
        policy=policy,
        ledger=entry.ledger,
        intent=exit_intent,
        sessions=(_session(date(2026, 9, 16), "11.00", "11.20", "exit"),),
    )
    unit = closed.ledger.unit("paper-unit-01")
    assert unit.target_notional == Decimal("100000.00")
    assert unit.cash == Decimal("100000.00")
    assert unit.external_pool > Decimal("0.00")
    assert unit.settled_quantity == 0

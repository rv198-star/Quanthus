from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from quanthus.backtest.contracts import EventType, MarketSession, OpenExecutionState
from quanthus.contracts import SnapshotRef
from quanthus.forward_validation.contracts import ForwardValidationContractError
from quanthus.forward_validation.paper_ledger import (
    initial_paper_ledger,
    load_paper_policy,
    paper_ledger_from_dict,
    reconcile_paper_ledger,
    verify_p2_ledger_entries,
)
from quanthus.forward_validation.paper_projection import (
    SealedAggregateDecision,
    paper_pending_intent_from_dict,
    project_paper_intent,
    settle_paper_intent,
)

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "config/forward-validation/paper/p6-paper-lane.v1.json"
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _policy():
    return load_paper_policy(POLICY_PATH, root=ROOT)


def _snapshot(market_date: date, suffix: str) -> SnapshotRef:
    cutoff = datetime(market_date.year, market_date.month, market_date.day, 15, 0, tzinfo=SHANGHAI)
    return SnapshotRef(
        market_date=market_date,
        knowledge_cutoff=cutoff,
        source_revision=f"paper-source-{suffix}",
        snapshot_id=f"paper-snapshot-{suffix}",
    )


def _decision(
    *,
    market_date: date,
    action: str,
    unit_id: str = "paper-unit-01",
    security_code: str = "000001",
    close: Decimal | None = Decimal("10.00"),
    target: Decimal | None = Decimal("0.30"),
    suffix: str = "entry",
) -> SealedAggregateDecision:
    snapshot = _snapshot(market_date, suffix)
    return SealedAggregateDecision(
        decision_digest="sha256:" + ("a" if suffix == "entry" else "b") * 64,
        snapshot=snapshot,
        decided_at=datetime(
            market_date.year, market_date.month, market_date.day, 15, 5, tzinfo=SHANGHAI
        ),
        security_code=security_code,
        unit_id=unit_id,
        final_action=action,
        aggregate_score=Decimal("0.75"),
        decision_version="candidate.p4-trend-risk@1.0.1",
        decision_close=close,
        target_maximum_position=target,
        reason_codes=("frozen_aggregate_decision",),
    )


def _session(
    day: date,
    *,
    state: OpenExecutionState = OpenExecutionState.EXECUTABLE,
    open_price: Decimal | None = Decimal("10.00"),
    close_price: Decimal | None = Decimal("10.50"),
    suffix: str = "s1",
) -> MarketSession:
    if state in {OpenExecutionState.SUSPENDED, OpenExecutionState.MISSING_EVIDENCE}:
        open_price = None
        close_price = None
    return MarketSession(
        session_date=day,
        open_at=datetime(day.year, day.month, day.day, 9, 30, tzinfo=SHANGHAI),
        close_at=datetime(day.year, day.month, day.day, 15, 0, tzinfo=SHANGHAI),
        security_code="000001",
        open_price=open_price,
        close_price=close_price,
        open_execution_state=state,
        source_revision=f"paper-execution-{suffix}",
        snapshot_id=f"paper-execution-snapshot-{suffix}",
    )


def test_paper_policy_reuses_exact_strategy_and_p2_profile() -> None:
    policy = _policy()
    assert policy.unit_count == 5
    assert policy.unit_notional == Decimal("100000.00")
    assert policy.base_trial_fraction == Decimal("0.30")
    assert policy.entry_lot_size == 100
    assert policy.execution_policy.active_profile.profile_id == "close-to-next-open-v1"


def test_initial_ledger_is_five_isolated_non_compounding_units() -> None:
    policy = _policy()
    ledger = initial_paper_ledger(policy)
    reconciliation = reconcile_paper_ledger(ledger, policy=policy)

    assert len(ledger.units) == 5
    assert all(item.target_notional == Decimal("100000.00") for item in ledger.units)
    assert all(item.cash == Decimal("100000.00") for item in ledger.units)
    assert reconciliation.total_target_notional == Decimal("500000.00")
    assert reconciliation.total_equity == Decimal("500000.00")
    assert ledger.to_dict()["authority"] == "paper"
    assert ledger.to_dict()["actual_execution_authorized"] is False


def test_no_action_creates_no_paper_intent() -> None:
    policy = _policy()
    ledger = initial_paper_ledger(policy)
    assert project_paper_intent(
        policy=policy,
        ledger=ledger,
        decision=_decision(market_date=date(2026, 9, 14), action="no_action"),
    ) is None


def test_trial_entry_projects_fixed_lot_and_settles_only_through_p2() -> None:
    policy = _policy()
    ledger = initial_paper_ledger(policy)
    decision = _decision(market_date=date(2026, 9, 14), action="trial_entry")
    intent = project_paper_intent(policy=policy, ledger=ledger, decision=decision)
    assert intent is not None
    assert intent.signal.quantity == 3000
    assert intent.signal.profile_id == "close-to-next-open-v1"
    assert intent.actual_execution_authorized is False

    settlement = settle_paper_intent(
        policy=policy,
        ledger=ledger,
        intent=intent,
        sessions=(_session(date(2026, 9, 15)),),
    )
    unit = settlement.ledger.unit("paper-unit-01")
    assert settlement.fill_count == 1
    assert unit.security_code == "000001"
    assert unit.settled_quantity == 3000
    assert unit.target_notional == Decimal("100000.00")
    assert settlement.actual_execution_authorized is False
    verify_p2_ledger_entries(settlement.events)
    assert settlement.reconciliation["balanced"] is True


def test_buy_limit_up_or_missing_evidence_never_fabricates_fill() -> None:
    policy = _policy()
    for state in (OpenExecutionState.BUY_BLOCKED_LIMIT_UP, OpenExecutionState.MISSING_EVIDENCE):
        ledger = initial_paper_ledger(policy)
        intent = project_paper_intent(
            policy=policy,
            ledger=ledger,
            decision=_decision(market_date=date(2026, 9, 14), action="trial_entry"),
        )
        assert intent is not None
        settlement = settle_paper_intent(
            policy=policy,
            ledger=ledger,
            intent=intent,
            sessions=(_session(date(2026, 9, 15), state=state, suffix=state.value),),
        )
        unit = settlement.ledger.unit("paper-unit-01")
        assert settlement.fill_count == 0
        assert unit.settled_quantity == 0
        assert unit.cash == Decimal("100000.00")
        statuses = [item for item in settlement.events if item.event_type is EventType.ORDER_STATUS]
        assert len(statuses) == 1


def _bought_ledger():
    policy = _policy()
    ledger = initial_paper_ledger(policy)
    intent = project_paper_intent(
        policy=policy,
        ledger=ledger,
        decision=_decision(market_date=date(2026, 9, 14), action="trial_entry"),
    )
    assert intent is not None
    settlement = settle_paper_intent(
        policy=policy,
        ledger=ledger,
        intent=intent,
        sessions=(_session(date(2026, 9, 15)),),
    )
    return policy, settlement.ledger


def test_full_exit_carries_through_limit_down_then_uses_p2_fill_and_fixed_unit_settlement() -> None:
    policy, ledger = _bought_ledger()
    decision = _decision(
        market_date=date(2026, 9, 15),
        action="full_exit",
        close=Decimal("10.50"),
        target=Decimal("0"),
        suffix="exit",
    )
    intent = project_paper_intent(policy=policy, ledger=ledger, decision=decision)
    assert intent is not None
    assert intent.signal.quantity == ledger.unit("paper-unit-01").settled_quantity

    settlement = settle_paper_intent(
        policy=policy,
        ledger=ledger,
        intent=intent,
        sessions=(
            _session(
                date(2026, 9, 16),
                state=OpenExecutionState.SELL_BLOCKED_LIMIT_DOWN,
                open_price=Decimal("9.50"),
                close_price=Decimal("9.20"),
                suffix="blocked",
            ),
            _session(
                date(2026, 9, 17),
                open_price=Decimal("11.00"),
                close_price=Decimal("11.20"),
                suffix="fill",
            ),
        ),
    )
    unit = settlement.ledger.unit("paper-unit-01")
    assert settlement.fill_count == 1
    assert unit.settled_quantity == 0
    assert unit.security_code is None
    assert unit.target_notional == Decimal("100000.00")
    assert unit.cash <= Decimal("100000.00")
    assert unit.external_pool >= Decimal("0.00")
    assert any(item.reason_code == "sell_blocked_limit_down" for item in settlement.events)
    verify_p2_ledger_entries(settlement.events)


def test_missing_sell_evidence_does_not_force_sale() -> None:
    policy, ledger = _bought_ledger()
    decision = _decision(
        market_date=date(2026, 9, 15),
        action="full_exit",
        close=Decimal("10.50"),
        target=Decimal("0"),
        suffix="exit",
    )
    intent = project_paper_intent(policy=policy, ledger=ledger, decision=decision)
    assert intent is not None
    settlement = settle_paper_intent(
        policy=policy,
        ledger=ledger,
        intent=intent,
        sessions=(
            _session(
                date(2026, 9, 16),
                state=OpenExecutionState.MISSING_EVIDENCE,
                suffix="missing",
            ),
        ),
    )
    unit = settlement.ledger.unit("paper-unit-01")
    assert settlement.fill_count == 0
    assert unit.settled_quantity > 0
    assert unit.security_code == "000001"


def test_paper_target_notional_cannot_be_mutated_or_redeployed() -> None:
    policy = _policy()
    ledger = initial_paper_ledger(policy)
    mutated = replace(ledger.unit("paper-unit-01"), target_notional=Decimal("101000.00"))
    from quanthus.forward_validation.paper_ledger import replace_paper_unit

    with pytest.raises(ForwardValidationContractError, match="paper_funding_drift"):
        replace_paper_unit(ledger, mutated, policy=policy)


def test_paper_state_and_pending_intent_round_trip_for_cross_day_forward_state() -> None:
    policy = _policy()
    ledger = initial_paper_ledger(policy)
    restored = paper_ledger_from_dict(ledger.to_dict())
    assert restored.to_dict() == ledger.to_dict()
    assert restored.digest == ledger.digest

    intent = project_paper_intent(
        policy=policy,
        ledger=ledger,
        decision=_decision(market_date=date(2026, 9, 14), action="trial_entry"),
    )
    assert intent is not None
    restored_intent = paper_pending_intent_from_dict(intent.to_dict())
    assert restored_intent.to_dict() == intent.to_dict()
    assert restored_intent.actual_execution_authorized is False


def test_missing_decision_close_blocks_new_entry_without_inventing_quantity() -> None:
    policy = _policy()
    ledger = initial_paper_ledger(policy)
    with pytest.raises(ForwardValidationContractError, match="paper_decision_close_missing"):
        project_paper_intent(
            policy=policy,
            ledger=ledger,
            decision=_decision(
                market_date=date(2026, 9, 14), action="trial_entry", close=None
            ),
        )

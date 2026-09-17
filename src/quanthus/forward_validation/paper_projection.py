"""Project sealed aggregate decisions into an isolated P2 paper lane."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from typing import Any, Sequence

from quanthus.backtest.contracts import (
    BacktestRunSpec,
    BacktestSignal,
    EngineEvent,
    EventType,
    MarketSession,
    OrderSide,
    SnapshotRef,
)
from quanthus.backtest.engine import BacktestEngine
from quanthus.forward_validation.contracts import fail
from quanthus.forward_validation.paper_ledger import (
    P6PaperPolicy,
    PaperLedgerState,
    reconcile_paper_ledger,
    replace_paper_unit,
    state_from_p2_snapshot,
    verify_p2_ledger_entries,
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()


def _stable_id(prefix: str, *parts: object) -> str:
    return f"{prefix}_{hashlib.sha256(_canonical(parts)).hexdigest()[:24]}"


@dataclass(frozen=True)
class SealedAggregateDecision:
    decision_digest: str
    snapshot: SnapshotRef
    decided_at: datetime
    security_code: str
    unit_id: str
    final_action: str
    aggregate_score: Decimal
    decision_version: str
    decision_close: Decimal | None
    target_maximum_position: Decimal | None
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.decision_digest.startswith("sha256:") or len(self.decision_digest) != 71:
            fail("invalid_digest", "aggregate decision digest must be sha256")
        if self.snapshot.knowledge_cutoff > self.decided_at:
            fail("paper_pit_violation", "decision precedes snapshot cutoff")
        if not self.security_code or not self.unit_id or not self.decision_version:
            fail("paper_decision_invalid", "paper decision identity is incomplete")
        if self.final_action not in {"trial_entry", "no_action", "reduce", "full_exit"}:
            fail("paper_action_not_admitted", self.final_action)
        if not self.aggregate_score.is_finite():
            fail("paper_decision_invalid", "aggregate score must be finite")
        if self.decision_close is not None and (
            not self.decision_close.is_finite() or self.decision_close <= 0
        ):
            fail("paper_decision_invalid", "decision close must be positive")
        if self.target_maximum_position is not None and (
            not self.target_maximum_position.is_finite()
            or not Decimal("0") <= self.target_maximum_position <= Decimal("1")
        ):
            fail("paper_decision_invalid", "target maximum position must be in [0,1]")
        if not self.reason_codes or any(not item for item in self.reason_codes):
            fail("paper_decision_invalid", "reason codes are required")


@dataclass(frozen=True)
class PaperPendingIntent:
    paper_intent_id: str
    decision_digest: str
    signal: BacktestSignal

    @property
    def actual_execution_authorized(self) -> bool:
        return False

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "quanthus.p6-paper-pending-intent.v1",
            "paper_intent_id": self.paper_intent_id,
            "decision_digest": self.decision_digest,
            "authority": "paper",
            "actual_execution_authorized": False,
            "signal": {
                "signal_id": self.signal.signal_id,
                "market_date": self.signal.snapshot.market_date.isoformat(),
                "knowledge_cutoff": self.signal.snapshot.knowledge_cutoff.isoformat(),
                "snapshot_id": self.signal.snapshot.snapshot_id,
                "source_revision": self.signal.snapshot.source_revision,
                "decision_at": self.signal.decision_at.isoformat(),
                "profile_id": self.signal.profile_id,
                "window_id": self.signal.window_id,
                "security_code": self.signal.security_code,
                "side": self.signal.side.value,
                "unit_id": self.signal.unit_id,
                "quantity": self.signal.quantity,
                "aggregate_score": str(self.signal.aggregate_score),
                "reason_code": self.signal.reason_code,
                "decision_version": self.signal.decision_version,
            },
        }


@dataclass(frozen=True)
class PaperSettlement:
    paper_intent_id: str
    decision_digest: str
    events: tuple[EngineEvent, ...]
    ledger: PaperLedgerState
    fill_count: int
    order_status_count: int
    reconciliation: dict[str, object]

    @property
    def actual_execution_authorized(self) -> bool:
        return False

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "quanthus.p6-paper-settlement.v1",
            "paper_intent_id": self.paper_intent_id,
            "decision_digest": self.decision_digest,
            "authority": "paper",
            "actual_execution_authorized": False,
            "fill_count": self.fill_count,
            "order_status_count": self.order_status_count,
            "event_count": len(self.events),
            "ledger_digest": self.ledger.digest,
            "reconciliation": self.reconciliation,
        }


def _quantity(
    *, policy: P6PaperPolicy, notional_fraction: Decimal, decision_close: Decimal
) -> int:
    lots = int(
        (policy.unit_notional * notional_fraction / decision_close / Decimal(policy.entry_lot_size))
        .to_integral_value(rounding=ROUND_DOWN)
    )
    return lots * policy.entry_lot_size


def project_paper_intent(
    *, policy: P6PaperPolicy, ledger: PaperLedgerState, decision: SealedAggregateDecision
) -> PaperPendingIntent | None:
    unit = ledger.unit(decision.unit_id)
    if decision.final_action == "no_action":
        return None
    if decision.final_action == "trial_entry":
        if unit.settled_quantity != 0 or unit.security_code is not None:
            fail("paper_unit_not_available", "trial entry requires empty paper unit")
        if decision.decision_close is None:
            fail("paper_decision_close_missing", "new paper entry requires decision close")
        target = decision.target_maximum_position or policy.base_trial_fraction
        quantity = _quantity(policy=policy, notional_fraction=target, decision_close=decision.decision_close)
        if quantity <= 0:
            fail("paper_entry_quantity_zero", "frozen paper sizing produced zero shares")
        side = OrderSide.BUY
    elif decision.final_action == "full_exit":
        if unit.security_code != decision.security_code or unit.settled_quantity <= 0:
            fail("paper_position_mismatch", "full exit requires matching held paper position")
        quantity = unit.settled_quantity
        side = OrderSide.SELL
    else:
        if unit.security_code != decision.security_code or unit.settled_quantity <= 0:
            fail("paper_position_mismatch", "reduction requires matching held paper position")
        if decision.decision_close is None or decision.target_maximum_position is None:
            fail("paper_decision_close_missing", "reduction requires close and target position")
        desired = _quantity(
            policy=policy,
            notional_fraction=decision.target_maximum_position,
            decision_close=decision.decision_close,
        )
        quantity = max(0, unit.settled_quantity - desired)
        quantity = quantity // policy.entry_lot_size * policy.entry_lot_size
        if quantity <= 0:
            return None
        side = OrderSide.SELL
    signal_id = _stable_id(
        "paper_signal",
        decision.decision_digest,
        decision.unit_id,
        decision.security_code,
        side.value,
        quantity,
    )
    signal = BacktestSignal(
        signal_id=signal_id,
        snapshot=decision.snapshot,
        decision_at=decision.decided_at,
        profile_id="close-to-next-open-v1",
        window_id=None,
        security_code=decision.security_code,
        side=side,
        unit_id=decision.unit_id,
        quantity=quantity,
        aggregate_score=decision.aggregate_score,
        reason_code="p6_paper_projection:" + decision.reason_codes[0],
        decision_version=decision.decision_version,
    )
    return PaperPendingIntent(
        paper_intent_id=_stable_id("paper_intent", decision.decision_digest, signal_id),
        decision_digest=decision.decision_digest,
        signal=signal,
    )


def _run_spec(policy: P6PaperPolicy, intent: PaperPendingIntent) -> BacktestRunSpec:
    signal = intent.signal
    return BacktestRunSpec(
        code_version="p6-paper-lane-1.0.0",
        parameter_hash=policy.policy_sha256,
        data_snapshot_id=signal.snapshot.snapshot_id,
        feature_version="sealed-forward-decision",
        rule_version=signal.decision_version,
        run_id=f"paper.{intent.paper_intent_id}",
        execution_policy_version=policy.execution_policy.policy_version,
        execution_profile_id="close-to-next-open-v1",
        schedule_version=policy.execution_policy.active_profile.schedule_version,
        official=False,
    )


def settle_paper_intent(
    *,
    policy: P6PaperPolicy,
    ledger: PaperLedgerState,
    intent: PaperPendingIntent,
    sessions: Sequence[MarketSession],
) -> PaperSettlement:
    if not sessions:
        fail("paper_execution_session_missing", "paper settlement requires session evidence")
    opening = ledger.unit(intent.signal.unit_id)
    engine = BacktestEngine(policy.execution_policy)
    run = _run_spec(policy, intent)
    if intent.signal.side is OrderSide.BUY:
        if len(sessions) != 1:
            fail("paper_buy_session_count", "daily paper buy has exactly one open attempt")
        result = engine.run_buy_session(
            run=run,
            signal=intent.signal,
            session=sessions[0],
            opening_state=opening,
        )
    else:
        result = engine.run_sell_sequence(
            run=run,
            signal=intent.signal,
            sessions=sessions,
            opening_state=opening,
        )
    events = tuple(result.events)
    verify_p2_ledger_entries(events)
    snapshots = [item for item in events if item.event_type is EventType.DAILY_SNAPSHOT]
    if not snapshots:
        fail("paper_p2_snapshot_missing", "P2 paper settlement produced no daily snapshot")
    final_state = state_from_p2_snapshot(snapshots[-1], target_notional=policy.unit_notional)
    next_ledger = replace_paper_unit(ledger, final_state, policy=policy)
    reconciliation = reconcile_paper_ledger(next_ledger, policy=policy)
    if not reconciliation.balanced:
        fail("paper_ledger_reconciliation_break", "paper ledger did not reconcile")
    return PaperSettlement(
        paper_intent_id=intent.paper_intent_id,
        decision_digest=intent.decision_digest,
        events=events,
        ledger=next_ledger,
        fill_count=sum(item.event_type is EventType.FILL for item in events),
        order_status_count=sum(item.event_type is EventType.ORDER_STATUS for item in events),
        reconciliation=reconciliation.to_dict(),
    )

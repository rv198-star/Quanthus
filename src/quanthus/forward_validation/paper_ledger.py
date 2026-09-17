"""Isolated fixed-unit paper ledger for P6-PAPER-001."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from quanthus.backtest.contracts import EngineEvent, EventType, UnitState
from quanthus.backtest.policy import ExecutionPolicy, load_execution_policy
from quanthus.forward_validation.contracts import ForwardValidationContractError, fail
from quanthus.forward_validation.serialization import digest_value

_SCHEMA = "quanthus.p6-paper-lane.v1"
_STRATEGY_DIGEST = "sha256:805ce33024ae82dc46a685c3ad7425528702e6a089ee4d09f27af2793d55ed0c"
_P2_DIGEST = "sha256:7617a62ceb5baf549c69525222d67503289f5cfdecc2c3e62151b220a90904c3"


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ForwardValidationContractError("invalid_json", str(path)) from exc
    if not isinstance(value, dict):
        fail("field_mismatch", f"{path} must contain one object")
    return value


def _bound_file(root: Path, value: Mapping[str, Any], name: str) -> Path:
    raw = value.get("path")
    if not isinstance(raw, str) or Path(raw).is_absolute() or ".." in Path(raw).parts:
        fail("path_escape", name)
    path = (root / raw).resolve()
    if root.resolve() not in path.parents or not path.is_file():
        fail("binding_missing", name)
    if value.get("sha256") != _sha(path):
        fail("binding_digest_drift", name)
    return path


@dataclass(frozen=True)
class P6PaperPolicy:
    path: Path
    policy_sha256: str
    execution_policy: ExecutionPolicy
    unit_count: int
    unit_notional: Decimal
    base_trial_fraction: Decimal
    entry_lot_size: int


@dataclass(frozen=True)
class PaperLedgerState:
    ledger_id: str
    version: int
    units: tuple[UnitState, ...]

    def __post_init__(self) -> None:
        if not self.ledger_id or self.version < 0:
            fail("paper_ledger_invalid", "paper ledger identity/version invalid")
        ids = tuple(item.unit_id for item in self.units)
        if not ids or len(ids) != len(set(ids)):
            fail("paper_ledger_invalid", "paper unit identities must be unique")

    @property
    def digest(self) -> str:
        return digest_value(self.to_dict())

    def unit(self, unit_id: str) -> UnitState:
        matches = [item for item in self.units if item.unit_id == unit_id]
        if len(matches) != 1:
            fail("paper_unit_not_found", unit_id)
        return matches[0]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "quanthus.p6-paper-ledger.v1",
            "ledger_id": self.ledger_id,
            "version": self.version,
            "authority": "paper",
            "actual_execution_authorized": False,
            "units": [_unit_dict(item) for item in self.units],
        }


@dataclass(frozen=True)
class PaperLedgerReconciliation:
    ledger_digest: str
    unit_count: int
    total_target_notional: Decimal
    total_cash: Decimal
    total_market_value: Decimal
    total_external_pool: Decimal
    total_equity: Decimal
    balanced: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "ledger_digest": self.ledger_digest,
            "unit_count": self.unit_count,
            "total_target_notional": str(self.total_target_notional),
            "total_cash": str(self.total_cash),
            "total_market_value": str(self.total_market_value),
            "total_external_pool": str(self.total_external_pool),
            "total_equity": str(self.total_equity),
            "balanced": self.balanced,
        }


def _unit_dict(state: UnitState) -> dict[str, object]:
    return {
        "unit_id": state.unit_id,
        "target_notional": str(state.target_notional),
        "cash": str(state.cash),
        "security_code": state.security_code,
        "settled_quantity": state.settled_quantity,
        "position_cost": str(state.position_cost),
        "commission_expense": str(state.commission_expense),
        "stamp_duty_expense": str(state.stamp_duty_expense),
        "transfer_fee_expense": str(state.transfer_fee_expense),
        "realized_pnl": str(state.realized_pnl),
        "dividend_income": str(state.dividend_income),
        "external_pool": str(state.external_pool),
        "last_mark_price": None if state.last_mark_price is None else str(state.last_mark_price),
        "last_mark_snapshot_id": state.last_mark_snapshot_id,
    }


def load_paper_policy(path: Path, *, root: Path) -> P6PaperPolicy:
    raw = _load(path)
    if (
        raw.get("schema_version") != _SCHEMA
        or raw.get("work_item") != "P6-PAPER-001"
        or raw.get("state") != "FROZEN_ENGINEERING_ONLY_NO_ACTIVATION"
    ):
        fail("paper_policy_drift", "P6 paper policy identity differs")
    bindings = raw.get("bindings")
    if not isinstance(bindings, dict):
        fail("field_mismatch", "paper bindings missing")
    strategy_path = _bound_file(root, bindings.get("strategy_card", {}), "strategy_card")
    p2_path = _bound_file(root, bindings.get("p2_execution_policy", {}), "p2_execution_policy")
    _bound_file(root, bindings.get("forward_worker_config", {}), "forward_worker_config")
    strategy = _load(strategy_path)
    if strategy.get("semantic_digest") != _STRATEGY_DIGEST:
        fail("paper_policy_drift", "strategy semantic digest differs")
    if bindings["strategy_card"].get("semantic_digest") != _STRATEGY_DIGEST:
        fail("paper_policy_drift", "paper config strategy digest differs")
    if bindings["p2_execution_policy"].get("semantic_digest") != _P2_DIGEST:
        fail("paper_policy_drift", "paper config P2 digest differs")
    execution_policy = load_execution_policy(p2_path)
    if (
        execution_policy.policy_version != "p2-daily-research-v1"
        or execution_policy.status != "frozen"
        or execution_policy.active_profile.profile_id != "close-to-next-open-v1"
    ):
        fail("paper_policy_drift", "accepted P2 execution profile differs")
    position = strategy.get("position_and_execution")
    execution = raw.get("execution")
    if not isinstance(position, dict) or not isinstance(execution, dict):
        fail("field_mismatch", "paper execution contract incomplete")
    expected = {
        "profile_id": position.get("execution_profile"),
        "unit_count": position.get("unit_count"),
        "unit_notional_cny": position.get("unit_notional"),
        "base_trial_position_fraction": position.get("base_trial_position_fraction"),
        "entry_lot_size": position.get("entry_lot_size"),
        "maximum_active_units": position.get("maximum_active_units"),
        "funding_policy": position.get("funding_policy"),
    }
    if any(execution.get(key) != value for key, value in expected.items()):
        fail("paper_policy_drift", "paper execution differs from accepted strategy")
    if (
        execution.get("paper_authority_marker") != "paper"
        or execution.get("actual_execution_authority") is not False
        or execution.get("adds_enabled") is not False
        or execution.get("reductions_enabled") is not True
        or execution.get("compound_capital") is not False
        or execution.get("profit_redeployment") is not False
    ):
        fail("paper_authority_drift", "paper execution authority/funding differs")
    authority = raw.get("authority")
    if not isinstance(authority, dict) or authority.get("paper_runtime_implementation_authorized") is not True:
        fail("authority_drift", "paper implementation authority missing")
    if any(
        value is not False
        for key, value in authority.items()
        if key != "paper_runtime_implementation_authorized"
    ):
        fail("authority_drift", "paper policy grants downstream authority")
    return P6PaperPolicy(
        path=path,
        policy_sha256=_sha(path),
        execution_policy=execution_policy,
        unit_count=int(execution["unit_count"]),
        unit_notional=Decimal(str(execution["unit_notional_cny"])),
        base_trial_fraction=Decimal(str(execution["base_trial_position_fraction"])),
        entry_lot_size=int(execution["entry_lot_size"]),
    )


def initial_paper_ledger(policy: P6PaperPolicy, *, ledger_id: str = "p6-paper-ledger-v1") -> PaperLedgerState:
    return PaperLedgerState(
        ledger_id=ledger_id,
        version=0,
        units=tuple(
            UnitState.empty(unit_id=f"paper-unit-{index:02d}", cash=policy.unit_notional)
            for index in range(1, policy.unit_count + 1)
        ),
    )


def replace_paper_unit(
    ledger: PaperLedgerState, state: UnitState, *, policy: P6PaperPolicy
) -> PaperLedgerState:
    if state.target_notional != policy.unit_notional:
        fail("paper_funding_drift", "paper unit target notional changed")
    if state.unit_id not in {item.unit_id for item in ledger.units}:
        fail("paper_unit_not_found", state.unit_id)
    units = tuple(state if item.unit_id == state.unit_id else item for item in ledger.units)
    return PaperLedgerState(ledger.ledger_id, ledger.version + 1, units)


def state_from_p2_snapshot(event: EngineEvent, *, target_notional: Decimal) -> UnitState:
    if event.event_type is not EventType.DAILY_SNAPSHOT:
        fail("paper_p2_snapshot_missing", "P2 unit state requires daily snapshot")
    payload = event.payload
    quantity = int(payload["position_quantity"])
    mark_price = payload.get("mark_price")
    mark_snapshot_id = payload.get("mark_snapshot_id")
    return UnitState(
        unit_id=str(payload["unit_id"]),
        target_notional=target_notional,
        cash=Decimal(str(payload["cash"])),
        security_code=None if quantity == 0 else str(payload["security_code"]),
        settled_quantity=quantity,
        position_cost=Decimal(str(payload["position_cost"])),
        commission_expense=Decimal(str(payload["commission_expense"])),
        stamp_duty_expense=Decimal(str(payload["stamp_duty_expense"])),
        transfer_fee_expense=Decimal(str(payload["transfer_fee_expense"])),
        realized_pnl=Decimal(str(payload["realized_pnl"])),
        dividend_income=Decimal(str(payload["dividend_income"])),
        external_pool=Decimal(str(payload["external_pool"])),
        last_mark_price=None if mark_price is None else Decimal(str(mark_price)),
        last_mark_snapshot_id=None if mark_snapshot_id is None else str(mark_snapshot_id),
    )


def reconcile_paper_ledger(ledger: PaperLedgerState, *, policy: P6PaperPolicy) -> PaperLedgerReconciliation:
    if len(ledger.units) != policy.unit_count:
        fail("paper_ledger_reconciliation_break", "paper unit count differs")
    total_target = Decimal("0")
    total_cash = Decimal("0")
    total_market = Decimal("0")
    total_pool = Decimal("0")
    for unit in ledger.units:
        if unit.target_notional != policy.unit_notional:
            fail("paper_ledger_reconciliation_break", "paper target notional drift")
        total_target += unit.target_notional
        total_cash += unit.cash
        total_pool += unit.external_pool
        if unit.last_mark_price is not None:
            total_market += unit.last_mark_price * unit.settled_quantity
    total_equity = total_cash + total_market + total_pool
    return PaperLedgerReconciliation(
        ledger_digest=ledger.digest,
        unit_count=len(ledger.units),
        total_target_notional=total_target,
        total_cash=total_cash,
        total_market_value=total_market,
        total_external_pool=total_pool,
        total_equity=total_equity,
        balanced=True,
    )


def verify_p2_ledger_entries(events: tuple[EngineEvent, ...]) -> None:
    for event in events:
        if event.event_type is not EventType.LEDGER_ENTRY:
            continue
        postings = event.payload.get("postings")
        if not isinstance(postings, list) or not postings:
            fail("paper_ledger_reconciliation_break", "P2 ledger entry lacks postings")
        total = sum((Decimal(str(item["amount"])) for item in postings), Decimal("0"))
        if total != 0:
            fail("paper_ledger_reconciliation_break", "P2 ledger postings do not balance")

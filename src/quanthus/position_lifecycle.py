"""Legal P3 position lifecycle transitions reconciled to the P2 unit ledger."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from quanthus.backtest.contracts import UnitState

POSITION_STATE_MACHINE_VERSION = "1.0.0"

_SEMANTIC_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_REASON_CODE = re.compile(r"^[a-z][a-z0-9_.-]{1,127}$")


class PositionLifecycleError(ValueError):
    """Raised when a lifecycle state or transition fails closed."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}")


class PositionStage(str, Enum):
    EMPTY = "empty"
    TRIAL = "trial"
    DYNAMIC = "dynamic"
    EXITING = "exiting"
    CLOSED = "closed"


class PositionTransitionKind(str, Enum):
    ENTRY_FILLED = "entry_filled"
    ADD_FILLED = "add_filled"
    REDUCE_FILLED = "reduce_filled"
    EXIT_REQUESTED = "exit_requested"
    EXIT_CARRIED = "exit_carried"
    EXIT_FILLED = "exit_filled"
    COOLDOWN_COMPLETED = "cooldown_completed"


class PositionEvidenceKind(str, Enum):
    DECISION = "decision"
    FILL = "fill"
    ORDER_STATUS = "order_status"
    UNIT_SETTLEMENT = "unit_settlement"
    CALENDAR = "calendar"


_TRANSITIONS = {
    (PositionStage.EMPTY, PositionTransitionKind.ENTRY_FILLED): PositionStage.TRIAL,
    (PositionStage.TRIAL, PositionTransitionKind.ADD_FILLED): PositionStage.DYNAMIC,
    (PositionStage.TRIAL, PositionTransitionKind.EXIT_REQUESTED): PositionStage.EXITING,
    (PositionStage.DYNAMIC, PositionTransitionKind.ADD_FILLED): PositionStage.DYNAMIC,
    (PositionStage.DYNAMIC, PositionTransitionKind.REDUCE_FILLED): PositionStage.DYNAMIC,
    (PositionStage.DYNAMIC, PositionTransitionKind.EXIT_REQUESTED): PositionStage.EXITING,
    (PositionStage.EXITING, PositionTransitionKind.EXIT_CARRIED): PositionStage.EXITING,
    (PositionStage.EXITING, PositionTransitionKind.EXIT_FILLED): PositionStage.CLOSED,
    (PositionStage.CLOSED, PositionTransitionKind.COOLDOWN_COMPLETED): PositionStage.EMPTY,
}

_REQUIRED_EVIDENCE = {
    PositionTransitionKind.ENTRY_FILLED: frozenset({PositionEvidenceKind.FILL}),
    PositionTransitionKind.ADD_FILLED: frozenset({PositionEvidenceKind.FILL}),
    PositionTransitionKind.REDUCE_FILLED: frozenset({PositionEvidenceKind.FILL}),
    PositionTransitionKind.EXIT_REQUESTED: frozenset({PositionEvidenceKind.DECISION}),
    PositionTransitionKind.EXIT_CARRIED: frozenset({PositionEvidenceKind.ORDER_STATUS}),
    PositionTransitionKind.EXIT_FILLED: frozenset({PositionEvidenceKind.FILL}),
    PositionTransitionKind.COOLDOWN_COMPLETED: frozenset(
        {PositionEvidenceKind.UNIT_SETTLEMENT, PositionEvidenceKind.CALENDAR}
    ),
}

_POSITIONED_STAGES = {
    PositionStage.TRIAL,
    PositionStage.DYNAMIC,
    PositionStage.EXITING,
}


def _require_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise PositionLifecycleError("invalid_identity", f"{field_name} must not be empty")


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PositionLifecycleError("invalid_timestamp", f"{field_name} must be timezone-aware")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


@dataclass(frozen=True)
class PositionEvidenceRef:
    kind: PositionEvidenceKind
    evidence_id: str
    source_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, PositionEvidenceKind):
            raise PositionLifecycleError("invalid_evidence", "kind must be a PositionEvidenceKind")
        _require_text(self.evidence_id, "evidence_id")
        _require_text(self.source_version, "source_version")

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.kind.value, self.evidence_id, self.source_version)

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind.value,
            "evidence_id": self.evidence_id,
            "source_version": self.source_version,
        }


@dataclass(frozen=True)
class PositionTransition:
    transition_id: str
    kind: PositionTransitionKind
    unit_id: str
    opportunity_id: str
    security_code: str
    occurred_at: datetime
    reason_codes: tuple[str, ...]
    evidence: tuple[PositionEvidenceRef, ...]

    def __post_init__(self) -> None:
        for field_name in ("transition_id", "unit_id", "opportunity_id", "security_code"):
            _require_text(getattr(self, field_name), field_name)
        if not isinstance(self.kind, PositionTransitionKind):
            raise PositionLifecycleError(
                "invalid_transition", "kind must be a PositionTransitionKind"
            )
        _require_aware(self.occurred_at, "occurred_at")
        if (
            not self.reason_codes
            or len(self.reason_codes) != len(set(self.reason_codes))
            or any(not _REASON_CODE.fullmatch(reason) for reason in self.reason_codes)
        ):
            raise PositionLifecycleError(
                "invalid_transition", "reason_codes must be non-empty unique stable tokens"
            )
        evidence_keys = [item.key for item in self.evidence]
        if not evidence_keys or len(evidence_keys) != len(set(evidence_keys)):
            raise PositionLifecycleError(
                "invalid_transition_evidence", "evidence must be non-empty and unique"
            )
        supplied_kinds = frozenset(item.kind for item in self.evidence)
        if supplied_kinds != _REQUIRED_EVIDENCE[self.kind]:
            expected = sorted(kind.value for kind in _REQUIRED_EVIDENCE[self.kind])
            raise PositionLifecycleError(
                "invalid_transition_evidence",
                f"{self.kind.value} requires exactly these evidence kinds: {expected}",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "transition_id": self.transition_id,
            "kind": self.kind.value,
            "unit_id": self.unit_id,
            "opportunity_id": self.opportunity_id,
            "security_code": self.security_code,
            "occurred_at": self.occurred_at.isoformat(),
            "reason_codes": sorted(self.reason_codes),
            "evidence": [
                item.to_dict() for item in sorted(self.evidence, key=lambda item: item.key)
            ],
        }


@dataclass(frozen=True)
class PositionLifecycleState:
    unit_id: str
    stage: PositionStage
    security_code: str | None
    opportunity_id: str | None
    state_machine_version: str
    as_of: datetime
    transition_ids: tuple[str, ...]
    consumed_evidence: tuple[PositionEvidenceRef, ...]

    def __post_init__(self) -> None:
        _require_text(self.unit_id, "unit_id")
        if not isinstance(self.stage, PositionStage):
            raise PositionLifecycleError("invalid_lifecycle_state", "stage must be a PositionStage")
        if not _SEMANTIC_VERSION.fullmatch(self.state_machine_version):
            raise PositionLifecycleError(
                "invalid_lifecycle_state", "state_machine_version must use MAJOR.MINOR.PATCH"
            )
        _require_aware(self.as_of, "as_of")
        if self.stage is PositionStage.EMPTY:
            if self.security_code is not None or self.opportunity_id is not None:
                raise PositionLifecycleError(
                    "invalid_lifecycle_state", "empty state cannot retain position identity"
                )
        else:
            _require_text(self.security_code, "security_code")
            _require_text(self.opportunity_id, "opportunity_id")
        if len(self.transition_ids) != len(set(self.transition_ids)) or any(
            not transition_id.strip() for transition_id in self.transition_ids
        ):
            raise PositionLifecycleError(
                "invalid_lifecycle_state", "transition_ids must be non-empty and unique"
            )
        evidence_keys = [item.key for item in self.consumed_evidence]
        if len(evidence_keys) != len(set(evidence_keys)):
            raise PositionLifecycleError(
                "invalid_lifecycle_state", "consumed evidence must be unique"
            )
        if not self.transition_ids and self.consumed_evidence:
            raise PositionLifecycleError(
                "invalid_lifecycle_state", "evidence cannot exist without a transition"
            )

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "stage": self.stage.value,
            "security_code": self.security_code,
            "opportunity_id": self.opportunity_id,
            "state_machine_version": self.state_machine_version,
            "as_of": self.as_of.isoformat(),
            "transition_ids": list(self.transition_ids),
            "consumed_evidence": [
                item.to_dict() for item in sorted(self.consumed_evidence, key=lambda item: item.key)
            ],
        }


def _validate_unit_identity(unit_id: str, *ledger_states: UnitState) -> None:
    if any(state.unit_id != unit_id for state in ledger_states):
        raise PositionLifecycleError(
            "ledger_identity_mismatch", "P2 ledger state belongs to another unit"
        )


def _validate_ledger_binding(
    stage: PositionStage,
    security_code: str | None,
    ledger_state: UnitState,
) -> None:
    if stage in _POSITIONED_STAGES:
        if ledger_state.settled_quantity <= 0 or ledger_state.security_code != security_code:
            raise PositionLifecycleError(
                "ledger_state_mismatch", "active lifecycle state must match a P2 position"
            )
        return
    if ledger_state.settled_quantity != 0 or ledger_state.security_code is not None:
        raise PositionLifecycleError(
            "ledger_state_mismatch", "empty or closed lifecycle state requires an empty P2 unit"
        )


def initialize_position_lifecycle(
    *,
    ledger_state: UnitState,
    as_of: datetime,
    state_machine_version: str = POSITION_STATE_MACHINE_VERSION,
) -> PositionLifecycleState:
    """Create an empty lifecycle bound to an empty P2 unit ledger."""

    _require_aware(as_of, "as_of")
    _validate_ledger_binding(PositionStage.EMPTY, None, ledger_state)
    return PositionLifecycleState(
        unit_id=ledger_state.unit_id,
        stage=PositionStage.EMPTY,
        security_code=None,
        opportunity_id=None,
        state_machine_version=state_machine_version,
        as_of=as_of,
        transition_ids=(),
        consumed_evidence=(),
    )


def _validate_ledger_delta(
    transition: PositionTransition,
    before: UnitState,
    after: UnitState,
) -> None:
    kind = transition.kind
    before_quantity = before.settled_quantity
    after_quantity = after.settled_quantity
    same_security = before.security_code == after.security_code == transition.security_code

    if kind is PositionTransitionKind.ENTRY_FILLED:
        valid = (
            before_quantity == 0
            and before.security_code is None
            and after_quantity > 0
            and after.security_code == transition.security_code
        )
    elif kind is PositionTransitionKind.ADD_FILLED:
        valid = same_security and after_quantity > before_quantity > 0
    elif kind is PositionTransitionKind.REDUCE_FILLED:
        valid = same_security and 0 < after_quantity < before_quantity
    elif kind in {
        PositionTransitionKind.EXIT_REQUESTED,
        PositionTransitionKind.EXIT_CARRIED,
    }:
        valid = same_security and after_quantity == before_quantity and before_quantity > 0
    elif kind is PositionTransitionKind.EXIT_FILLED:
        valid = (
            before_quantity > 0
            and before.security_code == transition.security_code
            and after_quantity == 0
            and after.security_code is None
        )
    else:
        valid = (
            before_quantity == 0
            and after_quantity == 0
            and before.security_code is None
            and after.security_code is None
        )
    if not valid:
        raise PositionLifecycleError(
            "invalid_ledger_delta",
            f"P2 ledger delta does not prove {transition.kind.value}",
        )


def apply_position_transition(
    current: PositionLifecycleState,
    transition: PositionTransition,
    *,
    ledger_before: UnitState,
    ledger_after: UnitState,
) -> PositionLifecycleState:
    """Apply one legal transition after reconciling its P2 ledger before/after states."""

    _validate_unit_identity(current.unit_id, ledger_before, ledger_after)
    if transition.unit_id != current.unit_id:
        raise PositionLifecycleError(
            "transition_identity_mismatch", "transition belongs to another unit"
        )
    _validate_ledger_binding(current.stage, current.security_code, ledger_before)
    if transition.occurred_at <= current.as_of:
        raise PositionLifecycleError(
            "non_monotonic_transition", "transition time must follow current state time"
        )
    if transition.transition_id in current.transition_ids:
        raise PositionLifecycleError(
            "duplicate_transition", "transition_id has already been consumed"
        )
    consumed_keys = {item.key for item in current.consumed_evidence}
    if any(item.key in consumed_keys for item in transition.evidence):
        raise PositionLifecycleError(
            "duplicate_evidence", "transition evidence has already been consumed"
        )

    next_stage = _TRANSITIONS.get((current.stage, transition.kind))
    if next_stage is None:
        raise PositionLifecycleError(
            "illegal_transition",
            f"{current.stage.value} cannot apply {transition.kind.value}",
        )
    if current.stage is not PositionStage.EMPTY and (
        transition.security_code != current.security_code
        or transition.opportunity_id != current.opportunity_id
    ):
        raise PositionLifecycleError(
            "transition_identity_mismatch",
            "transition does not match the active position identity",
        )

    _validate_ledger_delta(transition, ledger_before, ledger_after)
    next_security = None if next_stage is PositionStage.EMPTY else transition.security_code
    next_opportunity = None if next_stage is PositionStage.EMPTY else transition.opportunity_id
    _validate_ledger_binding(next_stage, next_security, ledger_after)
    return PositionLifecycleState(
        unit_id=current.unit_id,
        stage=next_stage,
        security_code=next_security,
        opportunity_id=next_opportunity,
        state_machine_version=current.state_machine_version,
        as_of=transition.occurred_at,
        transition_ids=(*current.transition_ids, transition.transition_id),
        consumed_evidence=tuple(
            sorted(
                (*current.consumed_evidence, *transition.evidence),
                key=lambda item: item.key,
            )
        ),
    )

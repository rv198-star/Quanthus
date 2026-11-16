"""C13 candidate projection with an explicit T+1 entry window."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Any

from quanthus.position_lifecycle import PositionLifecycleState, PositionStage
from quanthus.product_daily.contracts import (
    CommandDisposition,
    CommandEnvelope,
    ProcessedCommand,
    ProductDailyError,
    ReasonCode,
    digest,
    make_receipt,
    ordered_reasons,
    require_aware,
    require_bool,
    require_nonnegative_int,
    require_text,
    resolve_command,
)


class CandidateProjection(str, Enum):
    OBSERVED = "observed"
    BLOCKED = "blocked"
    RESERVED = "reserved"
    ENTERED = "entered"
    HELD = "held"
    EXITING = "exiting"
    CLOSED = "closed"
    EXPIRED = "expired"


class ActualOrderState(str, Enum):
    NONE = "none"
    PENDING = "pending"
    CANCELED = "canceled"
    FILLED = "filled"


class ActualPositionState(str, Enum):
    NONE = "none"
    HELD = "held"
    EXITING = "exiting"
    CLOSED = "closed"


@dataclass(frozen=True)
class CandidateIdentity:
    original_event_id: str
    original_source_snapshot_id: str
    candidate_rule_digest: str
    security_code: str
    level1_industry_id: str

    def __post_init__(self) -> None:
        for field_name in (
            "original_event_id",
            "original_source_snapshot_id",
            "candidate_rule_digest",
            "security_code",
            "level1_industry_id",
        ):
            require_text(getattr(self, field_name), field_name)

    @property
    def key(self) -> tuple[str, str, str]:
        return (
            self.original_event_id,
            self.original_source_snapshot_id,
            self.candidate_rule_digest,
        )

    @property
    def digest(self) -> str:
        return digest(self.to_dict())

    def to_dict(self) -> dict[str, str]:
        return {
            "original_event_id": self.original_event_id,
            "original_source_snapshot_id": self.original_source_snapshot_id,
            "candidate_rule_digest": self.candidate_rule_digest,
            "security_code": self.security_code,
            "level1_industry_id": self.level1_industry_id,
        }


@dataclass(frozen=True)
class CandidatePolicy:
    policy_id: str = "r11-candidate-observation-v1"
    version: str = "1.0.0"
    minimum_event_separation_sessions: int = 22
    position_cooldown_sessions: int = 5

    def __post_init__(self) -> None:
        require_text(self.policy_id, "candidate.policy_id")
        require_text(self.version, "candidate.version")
        require_nonnegative_int(
            self.minimum_event_separation_sessions,
            "minimum_event_separation_sessions",
            positive=True,
        )
        require_nonnegative_int(
            self.position_cooldown_sessions,
            "position_cooldown_sessions",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "version": self.version,
            "minimum_event_separation_sessions": self.minimum_event_separation_sessions,
            "position_cooldown_sessions": self.position_cooldown_sessions,
        }


DEFAULT_CANDIDATE_POLICY = CandidatePolicy()


@dataclass(frozen=True)
class CandidateObservation:
    identity: CandidateIdentity
    decision_id: str
    observed_at: datetime
    knowledge_cutoff: datetime
    event_session_index: int
    current_session_index: int
    first_eligible_session_index: int
    expiry_session_index: int
    input_valid: bool
    policy_adopted: bool
    raw_allocated: bool
    capacity_allowed: bool
    expert_decision_ref: str | None
    position_state_ref: str | None
    actual_order_state: ActualOrderState = ActualOrderState.NONE
    actual_position_state: ActualPositionState = ActualPositionState.NONE
    evidence_at: datetime | None = None
    active_or_reserved_security_codes: tuple[str, ...] = ()
    previous_event_session_index: int | None = None
    last_closed_settled_session_index: int | None = None
    position_lifecycle: PositionLifecycleState | None = None

    def __post_init__(self) -> None:
        require_text(self.decision_id, "decision_id")
        require_aware(self.observed_at, "observed_at")
        require_aware(self.knowledge_cutoff, "knowledge_cutoff")
        if self.evidence_at is not None:
            require_aware(self.evidence_at, "evidence_at")
        for field_name in (
            "event_session_index",
            "current_session_index",
            "first_eligible_session_index",
            "expiry_session_index",
        ):
            require_nonnegative_int(getattr(self, field_name), field_name)
        for field_name in ("input_valid", "policy_adopted", "raw_allocated", "capacity_allowed"):
            require_bool(getattr(self, field_name), field_name)
        if self.first_eligible_session_index > self.expiry_session_index:
            raise ProductDailyError("invalid_candidate_time", "eligibility must not follow expiry")
        if not isinstance(self.actual_order_state, ActualOrderState):
            raise ProductDailyError("invalid_candidate_state", "invalid actual order state")
        if not isinstance(self.actual_position_state, ActualPositionState):
            raise ProductDailyError("invalid_candidate_state", "invalid actual position state")
        if len(set(self.active_or_reserved_security_codes)) != len(
            self.active_or_reserved_security_codes
        ):
            raise ProductDailyError("invalid_candidate_context", "security evidence must be unique")
        if self.previous_event_session_index is not None:
            require_nonnegative_int(
                self.previous_event_session_index, "previous_event_session_index"
            )
        if self.last_closed_settled_session_index is not None:
            require_nonnegative_int(
                self.last_closed_settled_session_index, "last_closed_settled_session_index"
            )
        if self.position_lifecycle is not None:
            lifecycle = self.position_lifecycle
            if (
                lifecycle.security_code is not None
                and lifecycle.security_code != self.identity.security_code
            ):
                raise ProductDailyError(
                    "position_identity_mismatch",
                    "position lifecycle security does not match the candidate event",
                )
            if (
                lifecycle.opportunity_id is not None
                and lifecycle.opportunity_id != self.identity.original_event_id
            ):
                raise ProductDailyError(
                    "position_identity_mismatch",
                    "position lifecycle opportunity does not match the original event",
                )
            expected_state = {
                PositionStage.EMPTY: ActualPositionState.NONE,
                PositionStage.TRIAL: ActualPositionState.HELD,
                PositionStage.DYNAMIC: ActualPositionState.HELD,
                PositionStage.EXITING: ActualPositionState.EXITING,
                PositionStage.CLOSED: ActualPositionState.CLOSED,
            }[lifecycle.stage]
            if expected_state is not self.actual_position_state:
                raise ProductDailyError(
                    "position_state_mismatch",
                    "actual position enum does not match the bound lifecycle state",
                )
            if self.position_state_ref != lifecycle.digest:
                raise ProductDailyError(
                    "position_state_mismatch",
                    "position_state_ref must equal the bound lifecycle digest",
                )

    @property
    def window(self) -> tuple[int, int, int]:
        return (
            self.event_session_index,
            self.first_eligible_session_index,
            self.expiry_session_index,
        )

    @property
    def digest(self) -> str:
        return digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity.to_dict(),
            "decision_id": self.decision_id,
            "observed_at": self.observed_at.isoformat(),
            "knowledge_cutoff": self.knowledge_cutoff.isoformat(),
            "event_session_index": self.event_session_index,
            "current_session_index": self.current_session_index,
            "first_eligible_session_index": self.first_eligible_session_index,
            "expiry_session_index": self.expiry_session_index,
            "input_valid": self.input_valid,
            "policy_adopted": self.policy_adopted,
            "raw_allocated": self.raw_allocated,
            "capacity_allowed": self.capacity_allowed,
            "expert_decision_ref": self.expert_decision_ref,
            "position_state_ref": self.position_state_ref,
            "actual_order_state": self.actual_order_state.value,
            "actual_position_state": self.actual_position_state.value,
            "evidence_at": None if self.evidence_at is None else self.evidence_at.isoformat(),
            "active_or_reserved_security_codes": list(
                sorted(self.active_or_reserved_security_codes)
            ),
            "previous_event_session_index": self.previous_event_session_index,
            "last_closed_settled_session_index": self.last_closed_settled_session_index,
            "position_lifecycle": (
                None
                if self.position_lifecycle is None
                else {
                    "stage": self.position_lifecycle.stage.value,
                    "digest": self.position_lifecycle.digest,
                }
            ),
        }


@dataclass(frozen=True)
class CandidateRecord:
    identity: CandidateIdentity
    decision_id: str
    event_session_index: int
    first_eligible_session_index: int
    expiry_session_index: int
    projection: CandidateProjection
    entry_authorized: bool
    reason_codes: tuple[ReasonCode, ...]
    observation_digest: str
    expert_decision_ref: str | None
    position_state_ref: str | None
    actual_order_state: ActualOrderState
    actual_position_state: ActualPositionState

    def __post_init__(self) -> None:
        require_nonnegative_int(self.event_session_index, "event_session_index")
        require_nonnegative_int(self.first_eligible_session_index, "first_eligible_session_index")
        require_nonnegative_int(self.expiry_session_index, "expiry_session_index")
        require_bool(self.entry_authorized, "entry_authorized")

    @property
    def execution_authorized(self) -> bool:
        return False

    @property
    def digest(self) -> str:
        return digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity.to_dict(),
            "identity_digest": self.identity.digest,
            "decision_id": self.decision_id,
            "event_session_index": self.event_session_index,
            "first_eligible_session_index": self.first_eligible_session_index,
            "expiry_session_index": self.expiry_session_index,
            "projection": self.projection.value,
            "entry_authorized": self.entry_authorized,
            "execution_authorized": self.execution_authorized,
            "reason_codes": [item.value for item in self.reason_codes],
            "observation_digest": self.observation_digest,
            "expert_decision_ref": self.expert_decision_ref,
            "position_state_ref": self.position_state_ref,
            "actual_order_state": self.actual_order_state.value,
            "actual_position_state": self.actual_position_state.value,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CandidateRecord:
        identity_raw = raw.get("identity")
        if not isinstance(identity_raw, dict):
            raise ProductDailyError("invalid_receipt", "candidate identity must be an object")
        identity = CandidateIdentity(
            original_event_id=str(identity_raw["original_event_id"]),
            original_source_snapshot_id=str(identity_raw["original_source_snapshot_id"]),
            candidate_rule_digest=str(identity_raw["candidate_rule_digest"]),
            security_code=str(identity_raw["security_code"]),
            level1_industry_id=str(identity_raw["level1_industry_id"]),
        )
        reasons = raw.get("reason_codes")
        if not isinstance(reasons, list) or not all(isinstance(item, str) for item in reasons):
            raise ProductDailyError("invalid_receipt", "candidate reasons must be strings")
        return cls(
            identity=identity,
            decision_id=str(raw["decision_id"]),
            event_session_index=raw["event_session_index"],
            first_eligible_session_index=raw["first_eligible_session_index"],
            expiry_session_index=raw["expiry_session_index"],
            projection=CandidateProjection(str(raw["projection"])),
            entry_authorized=raw["entry_authorized"],
            reason_codes=tuple(ReasonCode(item) for item in reasons),
            observation_digest=str(raw["observation_digest"]),
            expert_decision_ref=(
                None if raw.get("expert_decision_ref") is None else str(raw["expert_decision_ref"])
            ),
            position_state_ref=(
                None if raw.get("position_state_ref") is None else str(raw["position_state_ref"])
            ),
            actual_order_state=ActualOrderState(str(raw["actual_order_state"])),
            actual_position_state=ActualPositionState(str(raw["actual_position_state"])),
        )


@dataclass(frozen=True)
class CandidateState:
    records: tuple[CandidateRecord, ...] = ()
    revision: int = 0
    processed_commands: tuple[ProcessedCommand, ...] = ()

    def __post_init__(self) -> None:
        identity_keys = [item.identity.key for item in self.records]
        if len(identity_keys) != len(set(identity_keys)):
            raise ProductDailyError("invalid_candidate_state", "event identities must be unique")
        require_nonnegative_int(self.revision, "revision")
        command_ids = [item.command_id for item in self.processed_commands]
        if len(command_ids) != len(set(command_ids)):
            raise ProductDailyError("invalid_receipt", "processed command ids must be unique")

    def record(self, identity: CandidateIdentity) -> CandidateRecord | None:
        matches = [item for item in self.records if item.identity.key == identity.key]
        if len(matches) > 1:
            raise ProductDailyError("invalid_candidate_state", "duplicate event identity")
        return matches[0] if matches else None

    @property
    def digest(self) -> str:
        return digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "records": [
                item.to_dict() for item in sorted(self.records, key=lambda x: x.identity.key)
            ],
            "revision": self.revision,
            "processed_commands": [item.to_dict() for item in self.processed_commands],
        }


@dataclass(frozen=True)
class CandidateTransition:
    accepted: bool
    replayed: bool
    state: CandidateState
    record: CandidateRecord | None
    result_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "replayed": self.replayed,
            "record": None if self.record is None else self.record.to_dict(),
            "state_digest": self.state.digest,
            "state_revision": self.state.revision,
            "result_digest": self.result_digest,
        }


def _record_from_observation(
    observation: CandidateObservation,
    policy: CandidatePolicy,
) -> CandidateRecord:
    reasons: list[ReasonCode] = []
    projection = CandidateProjection.OBSERVED
    entry_authorized = False

    held = observation.actual_position_state in {
        ActualPositionState.HELD,
        ActualPositionState.EXITING,
    }
    if not observation.input_valid:
        reasons.append(ReasonCode.INPUT_UNAVAILABLE)
        projection = CandidateProjection.HELD if held else CandidateProjection.BLOCKED
        if held:
            reasons.append(ReasonCode.HOLD_AND_ALERT)
    elif (
        observation.evidence_at is not None
        and observation.evidence_at > observation.knowledge_cutoff
    ):
        reasons.append(ReasonCode.KNOWLEDGE_CUTOFF_BREACH)
        projection = CandidateProjection.BLOCKED
    elif not observation.policy_adopted:
        reasons.append(ReasonCode.POLICY_NOT_ADOPTED)
        projection = CandidateProjection.BLOCKED
    elif observation.expert_decision_ref is None:
        reasons.append(ReasonCode.INPUT_UNAVAILABLE)
        projection = CandidateProjection.BLOCKED
    elif observation.current_session_index > observation.expiry_session_index:
        reasons.append(ReasonCode.ENTRY_WINDOW_EXPIRED)
        projection = CandidateProjection.EXPIRED
    elif observation.actual_position_state is ActualPositionState.EXITING:
        projection = CandidateProjection.EXITING
        reasons.append(ReasonCode.CAPACITY_AVAILABLE)
    elif observation.actual_position_state is ActualPositionState.HELD:
        projection = CandidateProjection.HELD
        reasons.append(ReasonCode.CAPACITY_AVAILABLE)
    elif observation.actual_position_state is ActualPositionState.CLOSED:
        projection = CandidateProjection.CLOSED
        reasons.append(ReasonCode.CAPACITY_AVAILABLE)
    elif observation.actual_order_state is ActualOrderState.PENDING:
        projection = CandidateProjection.RESERVED
        reasons.append(ReasonCode.ENTRY_RESERVED)
    elif observation.actual_order_state is ActualOrderState.FILLED:
        projection = CandidateProjection.ENTERED
        reasons.append(ReasonCode.CAPACITY_AVAILABLE)
    elif observation.identity.security_code in observation.active_or_reserved_security_codes:
        reasons.append(ReasonCode.EXISTING_SECURITY_CYCLE)
        projection = CandidateProjection.BLOCKED
    elif (
        observation.previous_event_session_index is not None
        and observation.event_session_index - observation.previous_event_session_index
        < policy.minimum_event_separation_sessions
    ):
        reasons.append(ReasonCode.EVENT_SEPARATION)
        projection = CandidateProjection.BLOCKED
    elif (
        observation.last_closed_settled_session_index is not None
        and observation.event_session_index - observation.last_closed_settled_session_index
        < policy.position_cooldown_sessions
    ):
        reasons.append(ReasonCode.POSITION_COOLDOWN)
        projection = CandidateProjection.BLOCKED
    elif not observation.capacity_allowed:
        reasons.append(ReasonCode.BUDGET_CAPACITY)
        projection = CandidateProjection.BLOCKED
    else:
        reasons.append(ReasonCode.ENTRY_OBSERVED)
        entry_authorized = True

    return CandidateRecord(
        identity=observation.identity,
        decision_id=observation.decision_id,
        event_session_index=observation.event_session_index,
        first_eligible_session_index=observation.first_eligible_session_index,
        expiry_session_index=observation.expiry_session_index,
        projection=projection,
        entry_authorized=entry_authorized,
        reason_codes=ordered_reasons(reasons),
        observation_digest=observation.digest,
        expert_decision_ref=observation.expert_decision_ref,
        position_state_ref=observation.position_state_ref,
        actual_order_state=observation.actual_order_state,
        actual_position_state=observation.actual_position_state,
    )


def candidate_invocation_payload(
    observation: CandidateObservation,
    policy: CandidatePolicy = DEFAULT_CANDIDATE_POLICY,
) -> dict[str, Any]:
    return {"observation": observation.to_dict(), "policy": policy.to_dict()}


def project_candidate(
    state: CandidateState,
    command: CommandEnvelope,
    observation: CandidateObservation,
    policy: CandidatePolicy | None = None,
) -> CandidateTransition:
    """Apply a C13 observation without creating an order, fill or new rearm rule."""

    if policy is None:
        policy = DEFAULT_CANDIDATE_POLICY
    invocation_payload = candidate_invocation_payload(observation, policy)
    resolution = resolve_command(
        revision=state.revision,
        processed=state.processed_commands,
        command=command,
        operation="project_candidate",
        invocation_payload=invocation_payload,
    )
    if resolution.disposition is CommandDisposition.DUPLICATE:
        if resolution.receipt is None:
            raise ProductDailyError("invalid_receipt", "duplicate command lacks receipt")
        stored = resolution.receipt.result
        raw_record = stored.get("record")
        if not isinstance(raw_record, dict):
            raise ProductDailyError("invalid_receipt", "candidate receipt lacks original record")
        record = CandidateRecord.from_dict(raw_record)
        return CandidateTransition(
            accepted=True,
            replayed=True,
            state=state,
            record=record,
            result_digest=resolution.receipt.result_digest,
        )
    if resolution.disposition is CommandDisposition.CAS_CONFLICT:
        return CandidateTransition(
            accepted=False,
            replayed=False,
            state=state,
            record=None,
            result_digest=digest({"reason": ReasonCode.CAS_CONFLICT.value}),
        )

    existing = state.record(observation.identity)
    record = _record_from_observation(observation, policy)
    if existing is not None:
        existing_window = (
            existing.event_session_index,
            existing.first_eligible_session_index,
            existing.expiry_session_index,
        )
        if existing_window != observation.window:
            raise ProductDailyError(
                "candidate_window_mismatch",
                "the original event T+1 eligibility/expiry window cannot be rewritten",
            )
    if existing is not None and existing.projection is CandidateProjection.EXPIRED:
        record = replace(
            record,
            projection=CandidateProjection.EXPIRED,
            entry_authorized=False,
            reason_codes=ordered_reasons([*existing.reason_codes, ReasonCode.ENTRY_WINDOW_EXPIRED]),
        )
    records = tuple(
        sorted(
            (
                record if item.identity.key == record.identity.key else item
                for item in state.records
            ),
            key=lambda item: item.identity.key,
        )
    )
    if existing is None:
        records = tuple(sorted((*state.records, record), key=lambda item: item.identity.key))

    payload = {
        "accepted": True,
        "record": record.to_dict(),
    }
    receipt = make_receipt(
        command=command,
        operation="project_candidate",
        invocation_payload=invocation_payload,
        result=payload,
        revision=state.revision + 1,
    )
    next_state = CandidateState(
        records=records,
        revision=state.revision + 1,
        processed_commands=(*state.processed_commands, receipt),
    )
    return CandidateTransition(
        accepted=True,
        replayed=False,
        state=next_state,
        record=record,
        result_digest=receipt.result_digest,
    )

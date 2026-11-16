"""Strict shared primitives for the R11 daily policy core."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Any


class ProductDailyError(ValueError):
    """Raised when typed policy inputs cannot safely produce a decision."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}")


class CommandDisposition(str, Enum):
    APPLY = "apply"
    DUPLICATE = "duplicate"
    CAS_CONFLICT = "cas_conflict"


class ReasonCode(str, Enum):
    INPUT_UNAVAILABLE = "INPUT_UNAVAILABLE"
    POLICY_NOT_ADOPTED = "POLICY_NOT_ADOPTED"
    CANDIDATE_NOT_ELIGIBLE = "CANDIDATE_NOT_ELIGIBLE"
    EXPERT_ENTRY_BLOCKED = "EXPERT_ENTRY_BLOCKED"
    KNOWLEDGE_CUTOFF_BREACH = "KNOWLEDGE_CUTOFF_BREACH"
    ENTRY_WINDOW_EXPIRED = "ENTRY_WINDOW_EXPIRED"
    EXISTING_SECURITY_CYCLE = "EXISTING_SECURITY_CYCLE"
    EVENT_SEPARATION = "EVENT_SEPARATION"
    POSITION_COOLDOWN = "POSITION_COOLDOWN"
    CONCENTRATION_CAPACITY = "CONCENTRATION_CAPACITY"
    DAY_LIMIT = "DAY_LIMIT"
    NO_EMPTY_BUCKET = "NO_EMPTY_BUCKET"
    BUDGET_CAPACITY = "BUDGET_CAPACITY"
    CASH_CAPACITY = "CASH_CAPACITY"
    STOCK_CAPACITY = "STOCK_CAPACITY"
    INDUSTRY_CAPACITY = "INDUSTRY_CAPACITY"
    TOTAL_CAPACITY = "TOTAL_CAPACITY"
    CAPACITY_AVAILABLE = "CAPACITY_AVAILABLE"
    ENTRY_OBSERVED = "ENTRY_OBSERVED"
    ENTRY_RESERVED = "ENTRY_RESERVED"
    HOLD_AND_ALERT = "HOLD_AND_ALERT"
    SETTLEMENT_NOT_READY = "SETTLEMENT_NOT_READY"
    SETTLEMENT_ALREADY_COMPLETED = "SETTLEMENT_ALREADY_COMPLETED"
    CYCLE_ALREADY_ACTIVE = "CYCLE_ALREADY_ACTIVE"
    FIXED_CYCLE_SETTLED = "FIXED_CYCLE_SETTLED"
    RESERVE_TRANSFER_APPLIED = "RESERVE_TRANSFER_APPLIED"
    LATE_DELTA_APPLIED = "LATE_DELTA_APPLIED"
    CYCLE_PREPARED = "CYCLE_PREPARED"
    IDEMPOTENT_REPLAY = "IDEMPOTENT_REPLAY"
    CAS_CONFLICT = "CAS_CONFLICT"


_REASON_ORDER = {reason: index for index, reason in enumerate(ReasonCode)}


def ordered_reasons(values: tuple[ReasonCode, ...] | list[ReasonCode]) -> tuple[ReasonCode, ...]:
    """Return stable, duplicate-free policy reasons in the frozen priority order."""

    return tuple(sorted(set(values), key=lambda reason: _REASON_ORDER[reason]))


def require_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ProductDailyError("invalid_identity", f"{field_name} must not be empty")


def require_aware(value: object, field_name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ProductDailyError("invalid_timestamp", f"{field_name} must be timezone-aware")


def decimal_value(value: object, field_name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ProductDailyError("invalid_decimal", f"{field_name} must be finite")
    return value


def require_decimal(
    value: object,
    field_name: str,
    *,
    minimum: Decimal | None = None,
    maximum: Decimal | None = None,
) -> Decimal:
    parsed = decimal_value(value, field_name)
    if minimum is not None and parsed < minimum:
        raise ProductDailyError("invalid_decimal", f"{field_name} must be >= {minimum}")
    if maximum is not None and parsed > maximum:
        raise ProductDailyError("invalid_decimal", f"{field_name} must be <= {maximum}")
    return parsed


def decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def quantize(value: Decimal, quantum: Decimal) -> Decimal:
    if quantum <= 0 or not quantum.is_finite():
        raise ProductDailyError("invalid_quantum", "cash quantum must be finite and positive")
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def require_quantized(value: Decimal, quantum: Decimal, field_name: str) -> Decimal:
    require_decimal(value, field_name)
    require_decimal(quantum, "quantum", minimum=Decimal("0.0000000001"))
    if value != quantize(value, quantum):
        raise ProductDailyError(
            "invalid_quantum", f"{field_name} must already equal the adopted cash quantum"
        )
    return value


def require_bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ProductDailyError("invalid_boolean", f"{field_name} must be a boolean")
    return value


def require_nonnegative_int(value: object, field_name: str, *, positive: bool = False) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ProductDailyError("invalid_integer", f"{field_name} must be an integer")
    if value < 0 or (positive and value == 0):
        qualifier = "positive" if positive else "non-negative"
        raise ProductDailyError("invalid_integer", f"{field_name} must be {qualifier}")
    return value


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(canonical_bytes(value)).hexdigest()}"


@dataclass(frozen=True)
class ProcessedCommand:
    command_id: str
    body_digest: str
    operation: str
    invocation_digest: str
    result_digest: str
    result_json: str
    revision: int

    def __post_init__(self) -> None:
        for field_name in (
            "command_id",
            "body_digest",
            "operation",
            "invocation_digest",
            "result_digest",
        ):
            require_text(getattr(self, field_name), field_name)
        require_nonnegative_int(self.revision, "revision", positive=True)
        if not self.invocation_digest.startswith("sha256:") or len(self.invocation_digest) != 71:
            raise ProductDailyError("invalid_command", "invocation_digest must be sha256")
        try:
            json.loads(self.result_json)
        except json.JSONDecodeError as exc:
            raise ProductDailyError("invalid_receipt", "result_json must be valid JSON") from exc

    @property
    def result(self) -> dict[str, Any]:
        parsed = json.loads(self.result_json)
        if not isinstance(parsed, dict):
            raise ProductDailyError("invalid_receipt", "stored result must be an object")
        return parsed

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "body_digest": self.body_digest,
            "operation": self.operation,
            "invocation_digest": self.invocation_digest,
            "result_digest": self.result_digest,
            "result": self.result,
            "revision": self.revision,
        }


@dataclass(frozen=True)
class CommandEnvelope:
    command_id: str
    body_digest: str
    expected_revision: int

    def __post_init__(self) -> None:
        require_text(self.command_id, "command_id")
        require_text(self.body_digest, "body_digest")
        if not self.body_digest.startswith("sha256:") or len(self.body_digest) != 71:
            raise ProductDailyError("invalid_command", "body_digest must be sha256")
        require_nonnegative_int(self.expected_revision, "expected_revision")

    @classmethod
    def create(
        cls,
        command_id: str,
        payload: object,
        expected_revision: int,
    ) -> CommandEnvelope:
        return cls(
            command_id=command_id,
            body_digest=digest(payload),
            expected_revision=expected_revision,
        )


@dataclass(frozen=True)
class CommandResolution:
    disposition: CommandDisposition
    receipt: ProcessedCommand | None
    next_revision: int | None

    @property
    def state_may_change(self) -> bool:
        return self.disposition is CommandDisposition.APPLY


def resolve_command(
    *,
    revision: int,
    processed: tuple[ProcessedCommand, ...],
    command: CommandEnvelope,
    operation: str,
    invocation_payload: object,
) -> CommandResolution:
    """Resolve duplicate, idempotency conflict and revision preconditions."""

    require_text(operation, "operation")
    if command.body_digest != digest(invocation_payload):
        raise ProductDailyError(
            "idempotency_conflict",
            "command envelope body does not match the actual business invocation",
        )
    invocation_digest = digest({"operation": operation, "payload": invocation_payload})
    matches = [item for item in processed if item.command_id == command.command_id]
    if len(matches) > 1:
        raise ProductDailyError("invalid_receipt", "processed command ids must be unique")
    if matches:
        receipt = matches[0]
        if (
            receipt.body_digest != command.body_digest
            or receipt.operation != operation
            or receipt.invocation_digest != invocation_digest
        ):
            raise ProductDailyError(
                "idempotency_conflict",
                "one command_id cannot be reused with a different body",
            )
        return CommandResolution(
            disposition=CommandDisposition.DUPLICATE,
            receipt=receipt,
            next_revision=None,
        )
    if command.expected_revision != revision:
        return CommandResolution(
            disposition=CommandDisposition.CAS_CONFLICT,
            receipt=None,
            next_revision=None,
        )
    return CommandResolution(
        disposition=CommandDisposition.APPLY,
        receipt=None,
        next_revision=revision + 1,
    )


def make_receipt(
    *,
    command: CommandEnvelope,
    operation: str,
    invocation_payload: object,
    result: dict[str, Any],
    revision: int,
) -> ProcessedCommand:
    result_digest = digest(result)
    if command.body_digest != digest(invocation_payload):
        raise ProductDailyError(
            "idempotency_conflict",
            "command envelope body does not match the actual business invocation",
        )
    return ProcessedCommand(
        command_id=command.command_id,
        body_digest=command.body_digest,
        operation=operation,
        invocation_digest=digest({"operation": operation, "payload": invocation_payload}),
        result_digest=result_digest,
        result_json=json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        revision=revision,
    )

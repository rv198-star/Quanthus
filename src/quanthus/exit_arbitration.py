"""P3 four-source exit arbitration without threshold or execution authority."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from quanthus.policy import P0Policy
from quanthus.position_lifecycle import PositionStage

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[a-z][a-z0-9_.-]{1,127}$")


class ExitArbitrationError(ValueError):
    """Raised when exit evidence cannot be safely arbitrated."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}")


class ExitTriggerSource(str, Enum):
    STRUCTURAL_BREAK = "structural_break"
    CONSTRAINT_STOP = "constraint_stop"
    CONSECUTIVE_DETERIORATION = "consecutive_deterioration"
    MAXIMUM_HOLDING_PERIOD = "maximum_holding_period"


class ExitTriggerStatus(str, Enum):
    TRIGGERED = "triggered"
    CLEAR = "clear"
    UNAVAILABLE = "unavailable"


class ExitEvidenceKind(str, Enum):
    GOVERNANCE = "governance"
    CONSTRAINT = "constraint"
    REVIEW = "review"
    CALENDAR = "calendar"
    MISSING_DATA = "missing_data"


class ExitDecisionStatus(str, Enum):
    REQUEST_EXIT = "request_exit"
    HOLD = "hold"
    HOLD_WITH_UNAVAILABLE_EVIDENCE = "hold_with_unavailable_evidence"


_REQUIRED_EVIDENCE = {
    ExitTriggerSource.STRUCTURAL_BREAK: ExitEvidenceKind.GOVERNANCE,
    ExitTriggerSource.CONSTRAINT_STOP: ExitEvidenceKind.CONSTRAINT,
    ExitTriggerSource.CONSECUTIVE_DETERIORATION: ExitEvidenceKind.REVIEW,
    ExitTriggerSource.MAXIMUM_HOLDING_PERIOD: ExitEvidenceKind.CALENDAR,
}


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


def _require_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ExitArbitrationError("invalid_identity", f"{field_name} must not be empty")


@dataclass(frozen=True)
class ExitEvidenceRef:
    kind: ExitEvidenceKind
    evidence_id: str
    version: str
    digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ExitEvidenceKind):
            raise ExitArbitrationError("invalid_exit_evidence", "kind must be an ExitEvidenceKind")
        _require_text(self.evidence_id, "evidence_id")
        _require_text(self.version, "version")
        if not _SHA256.fullmatch(self.digest):
            raise ExitArbitrationError("invalid_exit_evidence", "evidence digest must use sha256")

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.kind.value, self.evidence_id, self.version, self.digest)

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind.value,
            "evidence_id": self.evidence_id,
            "version": self.version,
            "digest": self.digest,
        }


@dataclass(frozen=True)
class ExitTriggerEvaluation:
    source: ExitTriggerSource
    status: ExitTriggerStatus
    unit_id: str
    opportunity_id: str
    security_code: str
    snapshot_id: str
    knowledge_cutoff: datetime
    lifecycle_stage: PositionStage
    evaluator_version: str
    reason_code: str
    evidence: tuple[ExitEvidenceRef, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.source, ExitTriggerSource) or not isinstance(
            self.status, ExitTriggerStatus
        ):
            raise ExitArbitrationError(
                "invalid_exit_evaluation", "source and status must use exit enums"
            )
        for field_name in (
            "unit_id",
            "opportunity_id",
            "security_code",
            "snapshot_id",
            "evaluator_version",
        ):
            _require_text(getattr(self, field_name), field_name)
        if self.knowledge_cutoff.tzinfo is None or self.knowledge_cutoff.utcoffset() is None:
            raise ExitArbitrationError(
                "invalid_exit_evaluation", "knowledge_cutoff must be timezone-aware"
            )
        if self.lifecycle_stage not in {PositionStage.TRIAL, PositionStage.DYNAMIC}:
            raise ExitArbitrationError(
                "invalid_exit_lifecycle",
                "exit arbitration requires a Trial or Dynamic lifecycle",
            )
        if not _TOKEN.fullmatch(self.reason_code):
            raise ExitArbitrationError(
                "invalid_exit_evaluation", "reason_code must be a stable token"
            )
        evidence_keys = [item.key for item in self.evidence]
        if not evidence_keys or len(evidence_keys) != len(set(evidence_keys)):
            raise ExitArbitrationError(
                "invalid_exit_evidence", "exit evidence must be non-empty and unique"
            )
        evidence_kinds = {item.kind for item in self.evidence}
        required = _REQUIRED_EVIDENCE[self.source]
        if self.status is not ExitTriggerStatus.UNAVAILABLE and required not in evidence_kinds:
            raise ExitArbitrationError(
                "invalid_exit_evidence",
                f"{self.source.value} requires {required.value} evidence",
            )
        if (
            self.source is ExitTriggerSource.STRUCTURAL_BREAK
            and self.status is ExitTriggerStatus.TRIGGERED
            and ExitEvidenceKind.MISSING_DATA in evidence_kinds
        ):
            raise ExitArbitrationError(
                "missing_data_structural_break",
                "missing data cannot trigger a structural-break exit",
            )

    @property
    def context_key(self) -> tuple[object, ...]:
        return (
            self.unit_id,
            self.opportunity_id,
            self.security_code,
            self.snapshot_id,
            self.knowledge_cutoff,
            self.lifecycle_stage,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source.value,
            "status": self.status.value,
            "unit_id": self.unit_id,
            "opportunity_id": self.opportunity_id,
            "security_code": self.security_code,
            "snapshot_id": self.snapshot_id,
            "knowledge_cutoff": self.knowledge_cutoff.isoformat(),
            "lifecycle_stage": self.lifecycle_stage.value,
            "evaluator_version": self.evaluator_version,
            "reason_code": self.reason_code,
            "evidence": [
                item.to_dict() for item in sorted(self.evidence, key=lambda item: item.key)
            ],
        }


@dataclass(frozen=True)
class ExitArbitrationResult:
    policy_version: str
    policy_digest: str
    unit_id: str
    opportunity_id: str
    security_code: str
    snapshot_id: str
    knowledge_cutoff: datetime
    lifecycle_stage: PositionStage
    status: ExitDecisionStatus
    triggered_sources: tuple[ExitTriggerSource, ...]
    unavailable_sources: tuple[ExitTriggerSource, ...]
    reason_codes: tuple[str, ...]
    evaluations: tuple[ExitTriggerEvaluation, ...]

    @property
    def exit_requested(self) -> bool:
        return self.status is ExitDecisionStatus.REQUEST_EXIT

    @property
    def alert_required(self) -> bool:
        return bool(self.unavailable_sources)

    @property
    def execution_authorized(self) -> bool:
        return False

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "policy_digest": self.policy_digest,
            "unit_id": self.unit_id,
            "opportunity_id": self.opportunity_id,
            "security_code": self.security_code,
            "snapshot_id": self.snapshot_id,
            "knowledge_cutoff": self.knowledge_cutoff.isoformat(),
            "lifecycle_stage": self.lifecycle_stage.value,
            "status": self.status.value,
            "exit_requested": self.exit_requested,
            "triggered_sources": [item.value for item in self.triggered_sources],
            "unavailable_sources": [item.value for item in self.unavailable_sources],
            "reason_codes": list(self.reason_codes),
            "evaluations": [item.to_dict() for item in self.evaluations],
            "alert_required": self.alert_required,
            "execution_authorized": self.execution_authorized,
        }


def _policy_sources(policy: P0Policy) -> tuple[ExitTriggerSource, ...]:
    configured = policy.at("risk", "exit_triggers")
    if not isinstance(configured, list) or any(not isinstance(item, str) for item in configured):
        raise ExitArbitrationError(
            "invalid_exit_policy", "risk.exit_triggers must be a string array"
        )
    try:
        sources = tuple(ExitTriggerSource(item) for item in configured)
    except ValueError as exc:
        raise ExitArbitrationError(
            "invalid_exit_policy", "risk.exit_triggers contains an unknown source"
        ) from exc
    if len(sources) != len(set(sources)) or set(sources) != set(ExitTriggerSource):
        raise ExitArbitrationError(
            "invalid_exit_policy", "risk.exit_triggers must contain each frozen source once"
        )
    return sources


def arbitrate_exit(
    policy: P0Policy,
    evaluations: Sequence[ExitTriggerEvaluation],
) -> ExitArbitrationResult:
    """Combine exact source evaluations into an exit request or explicit hold result."""

    policy_sources = _policy_sources(policy)
    by_source: dict[ExitTriggerSource, ExitTriggerEvaluation] = {}
    for evaluation in evaluations:
        if evaluation.source in by_source:
            raise ExitArbitrationError(
                "duplicate_exit_source", f"duplicate source: {evaluation.source.value}"
            )
        by_source[evaluation.source] = evaluation
    missing = [source.value for source in policy_sources if source not in by_source]
    if missing:
        raise ExitArbitrationError("missing_exit_source", f"missing source evaluations: {missing}")
    ordered = tuple(by_source[source] for source in policy_sources)
    context = ordered[0].context_key
    if any(item.context_key != context for item in ordered[1:]):
        raise ExitArbitrationError(
            "exit_context_mismatch", "all source evaluations must belong to one review"
        )

    triggered = tuple(item.source for item in ordered if item.status is ExitTriggerStatus.TRIGGERED)
    unavailable = tuple(
        item.source for item in ordered if item.status is ExitTriggerStatus.UNAVAILABLE
    )
    if triggered:
        status = ExitDecisionStatus.REQUEST_EXIT
        reasons = tuple(item.reason_code for item in ordered if item.source in triggered)
    elif unavailable:
        status = ExitDecisionStatus.HOLD_WITH_UNAVAILABLE_EVIDENCE
        reasons = tuple(item.reason_code for item in ordered if item.source in unavailable)
    else:
        status = ExitDecisionStatus.HOLD
        reasons = ("hold_no_exit_trigger",)
    first = ordered[0]
    return ExitArbitrationResult(
        policy_version=policy.version,
        policy_digest=f"sha256:{policy.digest}",
        unit_id=first.unit_id,
        opportunity_id=first.opportunity_id,
        security_code=first.security_code,
        snapshot_id=first.snapshot_id,
        knowledge_cutoff=first.knowledge_cutoff,
        lifecycle_stage=first.lifecycle_stage,
        status=status,
        triggered_sources=triggered,
        unavailable_sources=unavailable,
        reason_codes=reasons,
        evaluations=ordered,
    )

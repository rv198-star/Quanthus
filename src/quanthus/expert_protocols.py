"""Typed expert opinions with explicit abstention and evidence boundaries."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Protocol

from quanthus.label_registry import FeatureFact, LabelResult

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.-]{2,127}$")
_SEMANTIC_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_TOKEN = re.compile(r"^[a-z][a-z0-9_.-]{1,127}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class ExpertProtocolError(ValueError):
    """Raised when an expert output or its evidence violates the protocol."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}")


class ExpertApplicability(str, Enum):
    APPLICABLE = "applicable"
    ABSTAINED = "abstained"


class EvidenceKind(str, Enum):
    FEATURE = "feature"
    LABEL = "label"
    SNAPSHOT = "snapshot"


class ConstraintScope(str, Enum):
    SECURITY = "security"
    UNIT = "unit"
    PORTFOLIO = "portfolio"
    MARKET = "market"


class VetoAuthority(str, Enum):
    SYSTEM = "system"
    STRATEGY = "strategy"


class RequiredAction(str, Enum):
    NONE = "none"
    BLOCK_NEW = "block_new"
    REDUCE_ONLY = "reduce_only"
    FORCE_LIQUIDATE = "force_liquidate"


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


def _require_expert_identity(expert_id: str, expert_version: str) -> None:
    if not _IDENTIFIER.fullmatch(expert_id):
        raise ExpertProtocolError("invalid_expert", f"invalid expert_id: {expert_id}")
    if not _SEMANTIC_VERSION.fullmatch(expert_version):
        raise ExpertProtocolError("invalid_expert", "expert_version must use MAJOR.MINOR.PATCH")


def _require_reason(reason_code: str, applicability_reason: str) -> None:
    if not _TOKEN.fullmatch(reason_code) or not _TOKEN.fullmatch(applicability_reason):
        raise ExpertProtocolError(
            "invalid_expert_output", "reason codes must use stable lowercase tokens"
        )


@dataclass(frozen=True)
class ExpertEvidenceRef:
    kind: EvidenceKind
    evidence_id: str
    version: str
    digest: str

    def __post_init__(self) -> None:
        if not self.evidence_id.strip() or not self.version.strip():
            raise ExpertProtocolError("invalid_evidence", "evidence identity must not be empty")
        if not _SHA256.fullmatch(self.digest):
            raise ExpertProtocolError("invalid_evidence", "evidence digest must use sha256")

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
class ExpertContext:
    security_code: str
    snapshot_id: str
    knowledge_cutoff: datetime
    position_state: str
    feature_facts: tuple[FeatureFact, ...]
    label_results: tuple[LabelResult, ...]

    def __post_init__(self) -> None:
        if any(
            not value.strip()
            for value in (self.security_code, self.snapshot_id, self.position_state)
        ):
            raise ExpertProtocolError("invalid_context", "context identity must not be empty")
        if self.knowledge_cutoff.tzinfo is None or self.knowledge_cutoff.utcoffset() is None:
            raise ExpertProtocolError(
                "invalid_context", "context knowledge_cutoff must be timezone-aware"
            )
        if not self.feature_facts and not self.label_results:
            raise ExpertProtocolError(
                "invalid_context", "expert context requires feature or label evidence"
            )
        feature_keys = [fact.key for fact in self.feature_facts]
        label_keys = [(result.label_id, result.label_version) for result in self.label_results]
        if len(feature_keys) != len(set(feature_keys)) or len(label_keys) != len(set(label_keys)):
            raise ExpertProtocolError(
                "invalid_context", "context evidence identities must be unique"
            )
        for fact in self.feature_facts:
            if fact.security_code != self.security_code:
                raise ExpertProtocolError(
                    "context_security_mismatch", "feature security does not match expert context"
                )
            if (
                fact.lineage.snapshot_id != self.snapshot_id
                or fact.lineage.knowledge_cutoff != self.knowledge_cutoff
            ):
                raise ExpertProtocolError(
                    "context_snapshot_mismatch", "feature snapshot does not match expert context"
                )
        for result in self.label_results:
            if result.security_code != self.security_code:
                raise ExpertProtocolError(
                    "context_security_mismatch", "label security does not match expert context"
                )
            if (
                result.snapshot_id != self.snapshot_id
                or result.knowledge_cutoff != self.knowledge_cutoff
            ):
                raise ExpertProtocolError(
                    "context_snapshot_mismatch", "label snapshot does not match expert context"
                )

    @property
    def evidence_refs(self) -> tuple[ExpertEvidenceRef, ...]:
        snapshot_payload = {
            "snapshot_id": self.snapshot_id,
            "knowledge_cutoff": self.knowledge_cutoff.isoformat(),
        }
        refs = [
            ExpertEvidenceRef(
                EvidenceKind.SNAPSHOT,
                self.snapshot_id,
                self.knowledge_cutoff.isoformat(),
                _digest(snapshot_payload),
            )
        ]
        refs.extend(
            ExpertEvidenceRef(
                EvidenceKind.FEATURE,
                fact.feature_id,
                fact.feature_version,
                fact.lineage.digest,
            )
            for fact in self.feature_facts
        )
        refs.extend(
            ExpertEvidenceRef(
                EvidenceKind.LABEL,
                result.label_id,
                result.label_version,
                result.digest,
            )
            for result in self.label_results
        )
        return tuple(sorted(refs, key=lambda item: item.key))


def _validate_evidence(evidence: tuple[ExpertEvidenceRef, ...]) -> None:
    keys = [item.key for item in evidence]
    if not keys or len(keys) != len(set(keys)):
        raise ExpertProtocolError(
            "invalid_expert_output", "expert evidence must be non-empty and unique"
        )


def _output_base_dict(
    expert_id: str,
    expert_version: str,
    applicability: ExpertApplicability,
    applicability_reason: str,
    reason_code: str,
    evidence: tuple[ExpertEvidenceRef, ...],
) -> dict[str, Any]:
    return {
        "expert_id": expert_id,
        "expert_version": expert_version,
        "applicability": applicability.value,
        "applicability_reason": applicability_reason,
        "reason_code": reason_code,
        "evidence": [item.to_dict() for item in sorted(evidence, key=lambda item: item.key)],
    }


@dataclass(frozen=True)
class ScoringOutput:
    expert_id: str
    expert_version: str
    applicability: ExpertApplicability
    applicability_reason: str
    reason_code: str
    evidence: tuple[ExpertEvidenceRef, ...]
    signal: Decimal | None
    confidence: Decimal | None

    def __post_init__(self) -> None:
        _require_expert_identity(self.expert_id, self.expert_version)
        _require_reason(self.reason_code, self.applicability_reason)
        _validate_evidence(self.evidence)
        if self.applicability is ExpertApplicability.ABSTAINED:
            if self.signal is not None or self.confidence is not None:
                raise ExpertProtocolError(
                    "invalid_abstention", "abstained scoring output cannot carry score values"
                )
            return
        if self.signal is None or self.confidence is None:
            raise ExpertProtocolError(
                "invalid_scoring_output", "applicable scoring output requires signal and confidence"
            )
        if (
            not self.signal.is_finite()
            or not Decimal("-1") <= self.signal <= Decimal("1")
            or not self.confidence.is_finite()
            or not Decimal("0") <= self.confidence <= Decimal("1")
        ):
            raise ExpertProtocolError(
                "invalid_scoring_output", "signal must be in [-1, 1] and confidence in [0, 1]"
            )

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        payload = _output_base_dict(
            self.expert_id,
            self.expert_version,
            self.applicability,
            self.applicability_reason,
            self.reason_code,
            self.evidence,
        )
        payload.update(
            {
                "signal": None if self.signal is None else str(self.signal),
                "confidence": None if self.confidence is None else str(self.confidence),
            }
        )
        return payload


@dataclass(frozen=True)
class ConstraintOutput:
    expert_id: str
    expert_version: str
    applicability: ExpertApplicability
    applicability_reason: str
    reason_code: str
    evidence: tuple[ExpertEvidenceRef, ...]
    minimum_position: Decimal | None
    maximum_position: Decimal | None
    stop_rule: str | None
    hold_rule: str | None
    scope: ConstraintScope | None

    def __post_init__(self) -> None:
        _require_expert_identity(self.expert_id, self.expert_version)
        _require_reason(self.reason_code, self.applicability_reason)
        _validate_evidence(self.evidence)
        values = (
            self.minimum_position,
            self.maximum_position,
            self.stop_rule,
            self.hold_rule,
            self.scope,
        )
        if self.applicability is ExpertApplicability.ABSTAINED:
            if any(value is not None for value in values):
                raise ExpertProtocolError(
                    "invalid_abstention", "abstained constraint output cannot carry constraints"
                )
            return
        if any(value is None for value in values):
            raise ExpertProtocolError(
                "invalid_constraint_output", "applicable constraint output is incomplete"
            )
        assert self.minimum_position is not None
        assert self.maximum_position is not None
        assert self.stop_rule is not None
        assert self.hold_rule is not None
        if (
            not self.minimum_position.is_finite()
            or not self.maximum_position.is_finite()
            or not Decimal("0") <= self.minimum_position <= self.maximum_position <= Decimal("1")
        ):
            raise ExpertProtocolError(
                "invalid_constraint_output", "position bounds must satisfy 0 <= min <= max <= 1"
            )
        if not self.stop_rule.strip() or not self.hold_rule.strip():
            raise ExpertProtocolError(
                "invalid_constraint_output", "stop_rule and hold_rule must not be empty"
            )

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        payload = _output_base_dict(
            self.expert_id,
            self.expert_version,
            self.applicability,
            self.applicability_reason,
            self.reason_code,
            self.evidence,
        )
        payload.update(
            {
                "minimum_position": (
                    None if self.minimum_position is None else str(self.minimum_position)
                ),
                "maximum_position": (
                    None if self.maximum_position is None else str(self.maximum_position)
                ),
                "stop_rule": self.stop_rule,
                "hold_rule": self.hold_rule,
                "scope": None if self.scope is None else self.scope.value,
            }
        )
        return payload


@dataclass(frozen=True)
class VetoOutput:
    expert_id: str
    expert_version: str
    applicability: ExpertApplicability
    applicability_reason: str
    reason_code: str
    evidence: tuple[ExpertEvidenceRef, ...]
    authority: VetoAuthority
    allow: bool | None
    required_action: RequiredAction | None

    def __post_init__(self) -> None:
        _require_expert_identity(self.expert_id, self.expert_version)
        _require_reason(self.reason_code, self.applicability_reason)
        _validate_evidence(self.evidence)
        if self.applicability is ExpertApplicability.ABSTAINED:
            if self.allow is not None or self.required_action is not None:
                raise ExpertProtocolError(
                    "invalid_abstention", "abstained veto output cannot carry veto values"
                )
            return
        if self.allow is None or self.required_action is None:
            raise ExpertProtocolError(
                "invalid_veto_output", "applicable veto output requires allow and action"
            )
        if self.allow != (self.required_action is RequiredAction.NONE):
            raise ExpertProtocolError(
                "invalid_veto_output", "allow must be true exactly when required_action is none"
            )

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        payload = _output_base_dict(
            self.expert_id,
            self.expert_version,
            self.applicability,
            self.applicability_reason,
            self.reason_code,
            self.evidence,
        )
        payload.update(
            {
                "authority": self.authority.value,
                "allow": self.allow,
                "required_action": (
                    None if self.required_action is None else self.required_action.value
                ),
            }
        )
        return payload


class ScoringExpert(Protocol):
    @property
    def expert_id(self) -> str: ...

    @property
    def expert_version(self) -> str: ...

    def evaluate(self, context: ExpertContext) -> ScoringOutput: ...


class ConstraintExpert(Protocol):
    @property
    def expert_id(self) -> str: ...

    @property
    def expert_version(self) -> str: ...

    def evaluate(self, context: ExpertContext) -> ConstraintOutput: ...


class VetoExpert(Protocol):
    @property
    def expert_id(self) -> str: ...

    @property
    def expert_version(self) -> str: ...

    def evaluate(self, context: ExpertContext) -> VetoOutput: ...


def _validate_runner_output(
    expert_id: str,
    expert_version: str,
    output_id: str,
    output_version: str,
    evidence: tuple[ExpertEvidenceRef, ...],
    context: ExpertContext,
) -> None:
    _require_expert_identity(expert_id, expert_version)
    if (output_id, output_version) != (expert_id, expert_version):
        raise ExpertProtocolError(
            "expert_identity_mismatch", "expert output identity does not match implementation"
        )
    allowed = {item.key for item in context.evidence_refs}
    supplied = {item.key for item in evidence}
    if not supplied.issubset(allowed):
        raise ExpertProtocolError(
            "unknown_expert_evidence", "expert output references evidence outside its context"
        )


def run_scoring_expert(expert: ScoringExpert, context: ExpertContext) -> ScoringOutput:
    output = expert.evaluate(context)
    _validate_runner_output(
        expert.expert_id,
        expert.expert_version,
        output.expert_id,
        output.expert_version,
        output.evidence,
        context,
    )
    return output


def run_constraint_expert(expert: ConstraintExpert, context: ExpertContext) -> ConstraintOutput:
    output = expert.evaluate(context)
    _validate_runner_output(
        expert.expert_id,
        expert.expert_version,
        output.expert_id,
        output.expert_version,
        output.evidence,
        context,
    )
    return output


def run_veto_expert(expert: VetoExpert, context: ExpertContext) -> VetoOutput:
    output = expert.evaluate(context)
    _validate_runner_output(
        expert.expert_id,
        expert.expert_version,
        output.expert_id,
        output.expert_version,
        output.evidence,
        context,
    )
    return output

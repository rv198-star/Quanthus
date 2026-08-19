"""Frozen domain contracts for expert evaluation protocol v2."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Any, ClassVar, TypeVar

from quanthus.expert_evaluation.serialization import (
    ExpertEvaluationError,
    canonical_json_bytes,
    date_text,
    datetime_text,
    decimal_text,
    decode_artifact,
    digest_value,
    parse_date,
    parse_datetime,
    parse_decimal,
    require_array,
    require_fields,
    require_object,
)

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.-]{2,127}$")
_SEMANTIC_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[a-z][a-z0-9_.-]{1,127}$")


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExpertEvaluationError("invalid_identity", f"{field_name} must not be empty")
    return value


def _require_identifier(value: object, field_name: str) -> str:
    text = _require_text(value, field_name)
    if _IDENTIFIER.fullmatch(text) is None:
        raise ExpertEvaluationError(
            "invalid_identity", f"{field_name} must use a stable lowercase identifier"
        )
    return text


def _require_version(value: object, field_name: str) -> str:
    text = _require_text(value, field_name)
    if _SEMANTIC_VERSION.fullmatch(text) is None:
        raise ExpertEvaluationError(
            "invalid_identity", f"{field_name} must use MAJOR.MINOR.PATCH"
        )
    return text


def _require_digest(value: object, field_name: str) -> str:
    text = _require_text(value, field_name)
    if _DIGEST.fullmatch(text) is None:
        raise ExpertEvaluationError(
            "invalid_identity", f"{field_name} must be a lowercase sha256 digest"
        )
    return text


def _require_token(value: object, field_name: str) -> str:
    text = _require_text(value, field_name)
    if _TOKEN.fullmatch(text) is None:
        raise ExpertEvaluationError(
            "invalid_identity", f"{field_name} must use a stable lowercase token"
        )
    return text


def _require_non_negative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ExpertEvaluationError(
            "invalid_count", f"{field_name} must be a non-negative integer"
        )
    return value


EnumT = TypeVar("EnumT", bound=Enum)


def _enum_value(enum_type: type[EnumT], value: object, field_name: str) -> EnumT:
    if not isinstance(value, str):
        raise ExpertEvaluationError("field_mismatch", f"{field_name} must be a string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ExpertEvaluationError(
            "field_mismatch", f"{field_name} has unsupported value: {value}"
        ) from exc


def _canonical_items(
    values: tuple[Any, ...], *, key: Any, field_name: str, require_non_empty: bool = True
) -> tuple[Any, ...]:
    items = tuple(values)
    keys = [key(item) for item in items]
    if require_non_empty and not items:
        raise ExpertEvaluationError("invalid_identity", f"{field_name} must not be empty")
    if len(keys) != len(set(keys)):
        raise ExpertEvaluationError("invalid_identity", f"{field_name} contains duplicates")
    return tuple(sorted(items, key=key))


def _plain_json(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, tuple):
        return [_plain_json(item) for item in value]
    if isinstance(value, list):
        return [_plain_json(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    raise ExpertEvaluationError(
        "unsupported_canonical_value",
        f"unsupported policy payload value: {type(value).__name__}",
    )


def _freeze_json(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_json(item) for item in value)
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ExpertEvaluationError(
                "unsupported_canonical_value", "policy payload keys must be strings"
            )
        return MappingProxyType(
            {key: _freeze_json(item) for key, item in sorted(value.items())}
        )
    raise ExpertEvaluationError(
        "unsupported_canonical_value",
        f"unsupported policy payload value: {type(value).__name__}",
    )


class EvidenceRoute(str, Enum):
    FUTURE_TARGET = "future_target"
    COUNTERFACTUAL_REPLAY = "counterfactual_replay"
    FORWARD_SHADOW = "forward_shadow"


class PolicyEffectKind(str, Enum):
    SCORING = "scoring"
    CONSTRAINT = "constraint"
    VETO = "veto"
    ABSTENTION = "abstention"


class RawValueType(str, Enum):
    PROBABILITY = "probability"
    CONTINUOUS = "continuous"
    ORDINAL = "ordinal"
    POLICY_ONLY = "policy_only"


class SubjectKind(str, Enum):
    SECURITY = "security"
    OPPORTUNITY = "opportunity"


class OutcomeType(str, Enum):
    BINARY = "binary"
    CONTINUOUS = "continuous"
    ORDINAL = "ordinal"


class ObservationApplicability(str, Enum):
    APPLICABLE = "applicable"
    ABSTAINED = "abstained"


class EvidenceQuality(str, Enum):
    VERIFIED = "verified"
    DEGRADED = "degraded"


class SettlementState(str, Enum):
    PENDING = "pending"
    SETTLED = "settled"
    CENSORED = "censored"
    UNAVAILABLE = "unavailable"


class LifecycleDecisionKind(str, Enum):
    KEEP = "keep"
    NARROW_WITH_NEW_VERSION = "narrow_with_new_version"
    REMAP_POLICY = "remap_policy"
    MERGE = "merge"
    RETIRE = "retire"
    INCONCLUSIVE = "inconclusive"


class ShadowStatus(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"


class LifecycleActorType(str, Enum):
    HUMAN = "human"
    AUTOMATED = "automated"


class ArtifactContract:
    """Shared self-digesting representation for the seven protocol artifacts."""

    SCHEMA_VERSION: ClassVar[str]

    def semantic_dict(self) -> dict[str, Any]:
        raise NotImplementedError

    @property
    def digest(self) -> str:
        semantic = {"schema_version": self.SCHEMA_VERSION, **self.semantic_dict()}
        return digest_value(semantic)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            **self.semantic_dict(),
            "digest": self.digest,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    @classmethod
    def from_bytes(cls, payload: bytes):
        return decode_artifact(payload, cls)

    @classmethod
    def _check_envelope(cls, raw: Mapping[str, Any], semantic_fields: set[str]) -> None:
        require_fields(raw, {"schema_version", "digest", *semantic_fields}, cls.__name__)
        if raw["schema_version"] != cls.SCHEMA_VERSION:
            raise ExpertEvaluationError(
                "unsupported_schema",
                f"{cls.__name__} requires {cls.SCHEMA_VERSION}",
            )

    @classmethod
    def _check_digest(cls, raw: Mapping[str, Any], value: ArtifactContract) -> None:
        if raw["digest"] != value.digest:
            raise ExpertEvaluationError(
                "digest_mismatch", f"{cls.__name__} semantic digest does not match"
            )


@dataclass(frozen=True)
class ArtifactRef:
    artifact_id: str
    version: str
    digest: str

    def __post_init__(self) -> None:
        _require_identifier(self.artifact_id, "artifact_id")
        _require_version(self.version, "version")
        _require_digest(self.digest, "digest")

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.artifact_id, self.version, self.digest)

    def to_dict(self) -> dict[str, str]:
        return {"artifact_id": self.artifact_id, "version": self.version, "digest": self.digest}

    @classmethod
    def from_dict(cls, value: object, field_name: str) -> ArtifactRef:
        raw = require_object(value, field_name)
        require_fields(raw, {"artifact_id", "version", "digest"}, field_name)
        return cls(raw["artifact_id"], raw["version"], raw["digest"])


@dataclass(frozen=True)
class PolicyMappingRef:
    mapping_id: str
    mapping_version: str
    mapping_digest: str
    effect_kinds: tuple[PolicyEffectKind, ...]

    def __post_init__(self) -> None:
        _require_identifier(self.mapping_id, "mapping_id")
        _require_version(self.mapping_version, "mapping_version")
        _require_digest(self.mapping_digest, "mapping_digest")
        if any(not isinstance(item, PolicyEffectKind) for item in self.effect_kinds):
            raise ExpertEvaluationError("field_mismatch", "effect_kinds contain invalid values")
        normalized = _canonical_items(
            self.effect_kinds, key=lambda item: item.value, field_name="effect_kinds"
        )
        object.__setattr__(self, "effect_kinds", normalized)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.mapping_id, self.mapping_version, self.mapping_digest)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mapping_id": self.mapping_id,
            "mapping_version": self.mapping_version,
            "mapping_digest": self.mapping_digest,
            "effect_kinds": [item.value for item in self.effect_kinds],
        }

    @classmethod
    def from_dict(cls, value: object, field_name: str) -> PolicyMappingRef:
        raw = require_object(value, field_name)
        require_fields(
            raw,
            {"mapping_id", "mapping_version", "mapping_digest", "effect_kinds"},
            field_name,
        )
        return cls(
            raw["mapping_id"],
            raw["mapping_version"],
            raw["mapping_digest"],
            tuple(
                _enum_value(PolicyEffectKind, item, f"{field_name}.effect_kinds")
                for item in require_array(raw["effect_kinds"], f"{field_name}.effect_kinds")
            ),
        )


@dataclass(frozen=True)
class EvidenceRef:
    evidence_id: str
    version: str
    digest: str
    visible_at: datetime

    def __post_init__(self) -> None:
        _require_identifier(self.evidence_id, "evidence_id")
        _require_version(self.version, "version")
        _require_digest(self.digest, "digest")
        datetime_text(self.visible_at, "visible_at")

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (
            self.evidence_id,
            self.version,
            self.digest,
            datetime_text(self.visible_at, "visible_at"),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "evidence_id": self.evidence_id,
            "version": self.version,
            "digest": self.digest,
            "visible_at": datetime_text(self.visible_at, "visible_at"),
        }

    @classmethod
    def from_dict(cls, value: object, field_name: str) -> EvidenceRef:
        raw = require_object(value, field_name)
        require_fields(raw, {"evidence_id", "version", "digest", "visible_at"}, field_name)
        return cls(
            raw["evidence_id"],
            raw["version"],
            raw["digest"],
            parse_datetime(raw["visible_at"], f"{field_name}.visible_at"),
        )


@dataclass(frozen=True)
class MetricRef:
    metric_id: str
    metric_version: str
    metric_digest: str
    admitted_value_types: tuple[RawValueType, ...]

    def __post_init__(self) -> None:
        _require_identifier(self.metric_id, "metric_id")
        _require_version(self.metric_version, "metric_version")
        _require_digest(self.metric_digest, "metric_digest")
        if any(not isinstance(item, RawValueType) for item in self.admitted_value_types):
            raise ExpertEvaluationError(
                "field_mismatch", "admitted_value_types contain invalid values"
            )
        normalized = _canonical_items(
            self.admitted_value_types,
            key=lambda item: item.value,
            field_name="admitted_value_types",
        )
        object.__setattr__(self, "admitted_value_types", normalized)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.metric_id, self.metric_version, self.metric_digest)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "metric_version": self.metric_version,
            "metric_digest": self.metric_digest,
            "admitted_value_types": [item.value for item in self.admitted_value_types],
        }

    @classmethod
    def from_dict(cls, value: object, field_name: str) -> MetricRef:
        raw = require_object(value, field_name)
        require_fields(
            raw,
            {"metric_id", "metric_version", "metric_digest", "admitted_value_types"},
            field_name,
        )
        return cls(
            raw["metric_id"],
            raw["metric_version"],
            raw["metric_digest"],
            tuple(
                _enum_value(RawValueType, item, f"{field_name}.admitted_value_types")
                for item in require_array(
                    raw["admitted_value_types"], f"{field_name}.admitted_value_types"
                )
            ),
        )


@dataclass(frozen=True)
class SampleLedger:
    eligible: int
    applicable: int
    abstained: int
    settled: int
    pending: int
    unavailable: int
    not_executable: int
    censored: int

    def __post_init__(self) -> None:
        for field_name in self.__dataclass_fields__:
            _require_non_negative_int(getattr(self, field_name), field_name)
        if self.eligible != self.applicable + self.abstained:
            raise ExpertEvaluationError(
                "ledger_not_closed", "eligible must equal applicable plus abstained"
            )
        if self.applicable != sum(
            (self.settled, self.pending, self.unavailable, self.not_executable, self.censored)
        ):
            raise ExpertEvaluationError(
                "ledger_not_closed", "applicable terminal states do not close the ledger"
            )

    @property
    def digest(self) -> str:
        return digest_value(self.semantic_dict())

    def semantic_dict(self) -> dict[str, int]:
        return {
            "eligible": self.eligible,
            "applicable": self.applicable,
            "abstained": self.abstained,
            "settled": self.settled,
            "pending": self.pending,
            "unavailable": self.unavailable,
            "not_executable": self.not_executable,
            "censored": self.censored,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.semantic_dict(), "digest": self.digest}

    @classmethod
    def from_dict(cls, value: object, field_name: str) -> SampleLedger:
        raw = require_object(value, field_name)
        names = {
            "eligible",
            "applicable",
            "abstained",
            "settled",
            "pending",
            "unavailable",
            "not_executable",
            "censored",
        }
        require_fields(raw, {*names, "digest"}, field_name)
        result = cls(**{name: raw[name] for name in names})
        if raw["digest"] != result.digest:
            raise ExpertEvaluationError("digest_mismatch", f"{field_name} digest does not match")
        return result


@dataclass(frozen=True)
class MetricResult:
    metric_id: str
    metric_version: str
    value: Decimal
    sample_count: int

    def __post_init__(self) -> None:
        _require_identifier(self.metric_id, "metric_id")
        _require_version(self.metric_version, "metric_version")
        decimal_text(self.value, "metric value")
        _require_non_negative_int(self.sample_count, "sample_count")

    @property
    def key(self) -> tuple[str, str]:
        return (self.metric_id, self.metric_version)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "metric_version": self.metric_version,
            "value": decimal_text(self.value, "metric value"),
            "sample_count": self.sample_count,
        }

    @classmethod
    def from_dict(cls, value: object, field_name: str) -> MetricResult:
        raw = require_object(value, field_name)
        require_fields(raw, {"metric_id", "metric_version", "value", "sample_count"}, field_name)
        return cls(
            raw["metric_id"],
            raw["metric_version"],
            parse_decimal(raw["value"], f"{field_name}.value"),
            raw["sample_count"],
        )


@dataclass(frozen=True)
class UncertaintyEstimate:
    metric_id: str
    method: ArtifactRef
    lower: Decimal
    upper: Decimal
    effective_sample_count: Decimal
    dependence_disclosure: str

    def __post_init__(self) -> None:
        _require_identifier(self.metric_id, "metric_id")
        decimal_text(self.lower, "lower")
        decimal_text(self.upper, "upper")
        decimal_text(self.effective_sample_count, "effective_sample_count")
        if self.lower > self.upper or self.effective_sample_count < 0:
            raise ExpertEvaluationError(
                "invalid_metric", "uncertainty bounds or sample count invalid"
            )
        _require_text(self.dependence_disclosure, "dependence_disclosure")

    @property
    def key(self) -> tuple[str, tuple[str, str, str]]:
        return (self.metric_id, self.method.key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "method": self.method.to_dict(),
            "lower": decimal_text(self.lower, "lower"),
            "upper": decimal_text(self.upper, "upper"),
            "effective_sample_count": decimal_text(
                self.effective_sample_count, "effective_sample_count"
            ),
            "dependence_disclosure": self.dependence_disclosure,
        }

    @classmethod
    def from_dict(cls, value: object, field_name: str) -> UncertaintyEstimate:
        raw = require_object(value, field_name)
        require_fields(
            raw,
            {
                "metric_id",
                "method",
                "lower",
                "upper",
                "effective_sample_count",
                "dependence_disclosure",
            },
            field_name,
        )
        return cls(
            raw["metric_id"],
            ArtifactRef.from_dict(raw["method"], f"{field_name}.method"),
            parse_decimal(raw["lower"], f"{field_name}.lower"),
            parse_decimal(raw["upper"], f"{field_name}.upper"),
            parse_decimal(
                raw["effective_sample_count"], f"{field_name}.effective_sample_count"
            ),
            raw["dependence_disclosure"],
        )


@dataclass(frozen=True)
class StabilityEstimate:
    slice_ref: ArtifactRef
    metric_id: str
    value: Decimal
    sample_count: int

    def __post_init__(self) -> None:
        _require_identifier(self.metric_id, "metric_id")
        decimal_text(self.value, "stability value")
        _require_non_negative_int(self.sample_count, "sample_count")

    @property
    def key(self) -> tuple[tuple[str, str, str], str]:
        return (self.slice_ref.key, self.metric_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "slice_ref": self.slice_ref.to_dict(),
            "metric_id": self.metric_id,
            "value": decimal_text(self.value, "stability value"),
            "sample_count": self.sample_count,
        }

    @classmethod
    def from_dict(cls, value: object, field_name: str) -> StabilityEstimate:
        raw = require_object(value, field_name)
        require_fields(raw, {"slice_ref", "metric_id", "value", "sample_count"}, field_name)
        return cls(
            ArtifactRef.from_dict(raw["slice_ref"], f"{field_name}.slice_ref"),
            raw["metric_id"],
            parse_decimal(raw["value"], f"{field_name}.value"),
            raw["sample_count"],
        )


@dataclass(frozen=True)
class BaselineComparison:
    baseline_ref: ArtifactRef
    metric_id: str
    expert_value: Decimal
    baseline_value: Decimal

    def __post_init__(self) -> None:
        _require_identifier(self.metric_id, "metric_id")
        decimal_text(self.expert_value, "expert_value")
        decimal_text(self.baseline_value, "baseline_value")

    @property
    def key(self) -> tuple[tuple[str, str, str], str]:
        return (self.baseline_ref.key, self.metric_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_ref": self.baseline_ref.to_dict(),
            "metric_id": self.metric_id,
            "expert_value": decimal_text(self.expert_value, "expert_value"),
            "baseline_value": decimal_text(self.baseline_value, "baseline_value"),
        }

    @classmethod
    def from_dict(cls, value: object, field_name: str) -> BaselineComparison:
        raw = require_object(value, field_name)
        require_fields(
            raw, {"baseline_ref", "metric_id", "expert_value", "baseline_value"}, field_name
        )
        return cls(
            ArtifactRef.from_dict(raw["baseline_ref"], f"{field_name}.baseline_ref"),
            raw["metric_id"],
            parse_decimal(raw["expert_value"], f"{field_name}.expert_value"),
            parse_decimal(raw["baseline_value"], f"{field_name}.baseline_value"),
        )


@dataclass(frozen=True)
class DecisionChangeCounts:
    evaluated: int
    score_changed: int
    constraint_changed: int
    veto_changed: int
    action_changed: int

    def __post_init__(self) -> None:
        for field_name in self.__dataclass_fields__:
            _require_non_negative_int(getattr(self, field_name), field_name)
        if any(
            value > self.evaluated
            for value in (
                self.score_changed,
                self.constraint_changed,
                self.veto_changed,
                self.action_changed,
            )
        ):
            raise ExpertEvaluationError(
                "invalid_count", "decision-change counts cannot exceed evaluated"
            )

    def to_dict(self) -> dict[str, int]:
        return {
            "evaluated": self.evaluated,
            "score_changed": self.score_changed,
            "constraint_changed": self.constraint_changed,
            "veto_changed": self.veto_changed,
            "action_changed": self.action_changed,
        }

    @classmethod
    def from_dict(cls, value: object, field_name: str) -> DecisionChangeCounts:
        raw = require_object(value, field_name)
        names = {
            "evaluated",
            "score_changed",
            "constraint_changed",
            "veto_changed",
            "action_changed",
        }
        require_fields(raw, names, field_name)
        return cls(**{name: raw[name] for name in names})


@dataclass(frozen=True)
class ShadowDisposition:
    status: ShadowStatus
    reason_code: str
    effective_boundary: datetime
    observation_budget: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.status, ShadowStatus):
            raise ExpertEvaluationError("field_mismatch", "shadow status is invalid")
        _require_token(self.reason_code, "reason_code")
        datetime_text(self.effective_boundary, "effective_boundary")
        if self.observation_budget is not None and _require_non_negative_int(
            self.observation_budget, "observation_budget"
        ) == 0:
            raise ExpertEvaluationError(
                "lifecycle_disposition_conflict", "observation budget must be positive"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "reason_code": self.reason_code,
            "effective_boundary": datetime_text(
                self.effective_boundary, "effective_boundary"
            ),
            "observation_budget": self.observation_budget,
        }

    @classmethod
    def from_dict(cls, value: object, field_name: str) -> ShadowDisposition:
        raw = require_object(value, field_name)
        require_fields(
            raw,
            {"status", "reason_code", "effective_boundary", "observation_budget"},
            field_name,
        )
        return cls(
            _enum_value(ShadowStatus, raw["status"], f"{field_name}.status"),
            raw["reason_code"],
            parse_datetime(raw["effective_boundary"], f"{field_name}.effective_boundary"),
            raw["observation_budget"],
        )


@dataclass(frozen=True)
class ForecastSpec(ArtifactContract):
    SCHEMA_VERSION: ClassVar[str] = "quanthus.forecast-spec.v2"

    producer_id: str
    producer_version: str
    target: ArtifactRef
    subject_kind: SubjectKind
    eligibility_rule: ArtifactRef
    observation_anchor_rule: str
    horizon_trading_sessions: int
    outcome_type: OutcomeType
    resolver: ArtifactRef
    scorer: ArtifactRef
    exclusions: tuple[str, ...]
    missing_rules: tuple[str, ...]
    censoring_rules: tuple[str, ...]

    _FIELDS: ClassVar[set[str]] = {
        "producer_id",
        "producer_version",
        "target",
        "subject_kind",
        "eligibility_rule",
        "observation_anchor_rule",
        "horizon_trading_sessions",
        "outcome_type",
        "resolver",
        "scorer",
        "exclusions",
        "missing_rules",
        "censoring_rules",
    }

    def __post_init__(self) -> None:
        _require_identifier(self.producer_id, "producer_id")
        _require_version(self.producer_version, "producer_version")
        if not isinstance(self.subject_kind, SubjectKind) or not isinstance(
            self.outcome_type, OutcomeType
        ):
            raise ExpertEvaluationError("field_mismatch", "forecast enum field is invalid")
        _require_token(self.observation_anchor_rule, "observation_anchor_rule")
        if (
            isinstance(self.horizon_trading_sessions, bool)
            or not isinstance(self.horizon_trading_sessions, int)
            or self.horizon_trading_sessions <= 0
        ):
            raise ExpertEvaluationError(
                "invalid_horizon", "horizon_trading_sessions must be positive"
            )
        for field_name in ("exclusions", "missing_rules", "censoring_rules"):
            values = tuple(_require_token(item, field_name) for item in getattr(self, field_name))
            object.__setattr__(
                self,
                field_name,
                _canonical_items(values, key=lambda item: item, field_name=field_name),
            )

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "producer_id": self.producer_id,
            "producer_version": self.producer_version,
            "target": self.target.to_dict(),
            "subject_kind": self.subject_kind.value,
            "eligibility_rule": self.eligibility_rule.to_dict(),
            "observation_anchor_rule": self.observation_anchor_rule,
            "horizon_trading_sessions": self.horizon_trading_sessions,
            "outcome_type": self.outcome_type.value,
            "resolver": self.resolver.to_dict(),
            "scorer": self.scorer.to_dict(),
            "exclusions": list(self.exclusions),
            "missing_rules": list(self.missing_rules),
            "censoring_rules": list(self.censoring_rules),
        }

    @classmethod
    def _from_dict(cls, raw: Mapping[str, Any]) -> ForecastSpec:
        cls._check_envelope(raw, cls._FIELDS)
        value = cls(
            producer_id=raw["producer_id"],
            producer_version=raw["producer_version"],
            target=ArtifactRef.from_dict(raw["target"], "target"),
            subject_kind=_enum_value(SubjectKind, raw["subject_kind"], "subject_kind"),
            eligibility_rule=ArtifactRef.from_dict(raw["eligibility_rule"], "eligibility_rule"),
            observation_anchor_rule=raw["observation_anchor_rule"],
            horizon_trading_sessions=raw["horizon_trading_sessions"],
            outcome_type=_enum_value(OutcomeType, raw["outcome_type"], "outcome_type"),
            resolver=ArtifactRef.from_dict(raw["resolver"], "resolver"),
            scorer=ArtifactRef.from_dict(raw["scorer"], "scorer"),
            exclusions=tuple(require_array(raw["exclusions"], "exclusions")),
            missing_rules=tuple(require_array(raw["missing_rules"], "missing_rules")),
            censoring_rules=tuple(require_array(raw["censoring_rules"], "censoring_rules")),
        )
        cls._check_digest(raw, value)
        return value


@dataclass(frozen=True)
class ExpertEvaluationSpec(ArtifactContract):
    SCHEMA_VERSION: ClassVar[str] = "quanthus.expert-evaluation-spec.v2"

    producer_id: str
    producer_version: str
    assessment_id: str
    assessment_version: str
    raw_value_type: RawValueType
    observation_semantics: ArtifactRef
    policy_mappings: tuple[PolicyMappingRef, ...]
    evidence_routes: tuple[EvidenceRoute, ...]
    population: ArtifactRef
    partition: ArtifactRef
    primary_metric: MetricRef
    metrics: tuple[MetricRef, ...]
    baselines: tuple[ArtifactRef, ...]
    slices: tuple[ArtifactRef, ...]
    assessment_stop_conditions: tuple[ArtifactRef, ...]
    forecast_spec_digest: str | None

    _FIELDS: ClassVar[set[str]] = {
        "producer_id",
        "producer_version",
        "assessment_id",
        "assessment_version",
        "raw_value_type",
        "observation_semantics",
        "policy_mappings",
        "evidence_routes",
        "population",
        "partition",
        "primary_metric",
        "metrics",
        "baselines",
        "slices",
        "assessment_stop_conditions",
        "forecast_spec_digest",
        "assessment_binding_digest",
    }

    def __post_init__(self) -> None:
        _require_identifier(self.producer_id, "producer_id")
        _require_version(self.producer_version, "producer_version")
        _require_identifier(self.assessment_id, "assessment_id")
        _require_version(self.assessment_version, "assessment_version")
        if not isinstance(self.raw_value_type, RawValueType):
            raise ExpertEvaluationError("field_mismatch", "raw_value_type is invalid")
        if any(not isinstance(item, PolicyMappingRef) for item in self.policy_mappings):
            raise ExpertEvaluationError("field_mismatch", "policy_mappings contain invalid values")
        if any(not isinstance(item, EvidenceRoute) for item in self.evidence_routes):
            raise ExpertEvaluationError("field_mismatch", "evidence_routes contain invalid values")
        object.__setattr__(
            self,
            "policy_mappings",
            _canonical_items(
                self.policy_mappings,
                key=lambda item: item.key,
                field_name="policy_mappings",
            ),
        )
        object.__setattr__(
            self,
            "evidence_routes",
            _canonical_items(
                self.evidence_routes,
                key=lambda item: item.value,
                field_name="evidence_routes",
            ),
        )
        for field_name in ("metrics", "baselines", "slices", "assessment_stop_conditions"):
            object.__setattr__(
                self,
                field_name,
                _canonical_items(
                    getattr(self, field_name),
                    key=lambda item: item.key,
                    field_name=field_name,
                ),
            )
        if self.primary_metric.key not in {item.key for item in self.metrics}:
            raise ExpertEvaluationError(
                "metric_not_registered", "primary_metric must be present in metrics"
            )
        if self.raw_value_type not in self.primary_metric.admitted_value_types:
            raise ExpertEvaluationError(
                "metric_not_registered", "primary_metric does not admit the assessment value type"
            )
        if self.raw_value_type is RawValueType.POLICY_ONLY:
            if self.forecast_spec_digest is not None:
                raise ExpertEvaluationError(
                    "value_type_mismatch", "policy-only assessment cannot bind a forecast"
                )
        else:
            _require_digest(self.forecast_spec_digest, "forecast_spec_digest")
            if EvidenceRoute.FUTURE_TARGET not in self.evidence_routes:
                raise ExpertEvaluationError(
                    "value_type_mismatch", "forecast assessment requires future_target evidence"
                )

    @property
    def assessment_binding_digest(self) -> str:
        return digest_value(
            {
                "assessment_id": self.assessment_id,
                "assessment_version": self.assessment_version,
                "raw_value_type": self.raw_value_type.value,
                "forecast_spec_digest": self.forecast_spec_digest,
                "observation_semantics": self.observation_semantics.to_dict(),
                "population": self.population.to_dict(),
                "partition": self.partition.to_dict(),
                "assessment_stop_conditions": [
                    item.to_dict() for item in self.assessment_stop_conditions
                ],
            }
        )

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "producer_id": self.producer_id,
            "producer_version": self.producer_version,
            "assessment_id": self.assessment_id,
            "assessment_version": self.assessment_version,
            "raw_value_type": self.raw_value_type.value,
            "observation_semantics": self.observation_semantics.to_dict(),
            "policy_mappings": [item.to_dict() for item in self.policy_mappings],
            "evidence_routes": [item.value for item in self.evidence_routes],
            "population": self.population.to_dict(),
            "partition": self.partition.to_dict(),
            "primary_metric": self.primary_metric.to_dict(),
            "metrics": [item.to_dict() for item in self.metrics],
            "baselines": [item.to_dict() for item in self.baselines],
            "slices": [item.to_dict() for item in self.slices],
            "assessment_stop_conditions": [
                item.to_dict() for item in self.assessment_stop_conditions
            ],
            "forecast_spec_digest": self.forecast_spec_digest,
            "assessment_binding_digest": self.assessment_binding_digest,
        }

    @classmethod
    def _from_dict(cls, raw: Mapping[str, Any]) -> ExpertEvaluationSpec:
        cls._check_envelope(raw, cls._FIELDS)
        value = cls(
            producer_id=raw["producer_id"],
            producer_version=raw["producer_version"],
            assessment_id=raw["assessment_id"],
            assessment_version=raw["assessment_version"],
            raw_value_type=_enum_value(RawValueType, raw["raw_value_type"], "raw_value_type"),
            observation_semantics=ArtifactRef.from_dict(
                raw["observation_semantics"], "observation_semantics"
            ),
            policy_mappings=tuple(
                PolicyMappingRef.from_dict(item, "policy_mappings[]")
                for item in require_array(raw["policy_mappings"], "policy_mappings")
            ),
            evidence_routes=tuple(
                _enum_value(EvidenceRoute, item, "evidence_routes[]")
                for item in require_array(raw["evidence_routes"], "evidence_routes")
            ),
            population=ArtifactRef.from_dict(raw["population"], "population"),
            partition=ArtifactRef.from_dict(raw["partition"], "partition"),
            primary_metric=MetricRef.from_dict(raw["primary_metric"], "primary_metric"),
            metrics=tuple(
                MetricRef.from_dict(item, "metrics[]")
                for item in require_array(raw["metrics"], "metrics")
            ),
            baselines=tuple(
                ArtifactRef.from_dict(item, "baselines[]")
                for item in require_array(raw["baselines"], "baselines")
            ),
            slices=tuple(
                ArtifactRef.from_dict(item, "slices[]")
                for item in require_array(raw["slices"], "slices")
            ),
            assessment_stop_conditions=tuple(
                ArtifactRef.from_dict(item, "assessment_stop_conditions[]")
                for item in require_array(
                    raw["assessment_stop_conditions"], "assessment_stop_conditions"
                )
            ),
            forecast_spec_digest=raw["forecast_spec_digest"],
        )
        if raw["assessment_binding_digest"] != value.assessment_binding_digest:
            raise ExpertEvaluationError(
                "digest_mismatch", "assessment_binding_digest does not match"
            )
        cls._check_digest(raw, value)
        return value


def derive_prediction_id(
    *,
    assessment_binding_digest: str,
    subject_id: str,
    observation_anchor: datetime,
    knowledge_cutoff: datetime,
    evidence_context_digest: str,
) -> str:
    _require_digest(assessment_binding_digest, "assessment_binding_digest")
    _require_text(subject_id, "subject_id")
    _require_digest(evidence_context_digest, "evidence_context_digest")
    identity = digest_value(
        {
            "assessment_binding_digest": assessment_binding_digest,
            "subject_id": subject_id,
            "observation_anchor": datetime_text(observation_anchor, "observation_anchor"),
            "knowledge_cutoff": datetime_text(knowledge_cutoff, "knowledge_cutoff"),
            "evidence_context_digest": evidence_context_digest,
        }
    )
    return f"prediction:{identity.removeprefix('sha256:')}"


@dataclass(frozen=True)
class ExpertObservation(ArtifactContract):
    SCHEMA_VERSION: ClassVar[str] = "quanthus.expert-observation.v2"

    producer_id: str
    producer_version: str
    prediction_id: str
    assessment_id: str
    assessment_version: str
    assessment_binding_digest: str
    forecast_spec_digest: str | None
    subject_id: str
    subject_kind: SubjectKind
    observation_anchor: datetime
    knowledge_cutoff: datetime
    evidence_context_digest: str
    evidence: tuple[EvidenceRef, ...]
    applicability: ObservationApplicability
    applicability_reason: str
    raw_value_type: RawValueType
    raw_value: Decimal | str | None
    value_unit: str | None
    evidence_quality: EvidenceQuality

    _FIELDS: ClassVar[set[str]] = {
        "producer_id",
        "producer_version",
        "prediction_id",
        "assessment_id",
        "assessment_version",
        "assessment_binding_digest",
        "forecast_spec_digest",
        "subject_id",
        "subject_kind",
        "observation_anchor",
        "knowledge_cutoff",
        "evidence_context_digest",
        "evidence",
        "applicability",
        "applicability_reason",
        "raw_value_type",
        "raw_value",
        "value_unit",
        "evidence_quality",
    }

    def __post_init__(self) -> None:
        _require_identifier(self.producer_id, "producer_id")
        _require_version(self.producer_version, "producer_version")
        _require_identifier(self.assessment_id, "assessment_id")
        _require_version(self.assessment_version, "assessment_version")
        _require_digest(self.assessment_binding_digest, "assessment_binding_digest")
        if self.forecast_spec_digest is not None:
            _require_digest(self.forecast_spec_digest, "forecast_spec_digest")
        _require_text(self.subject_id, "subject_id")
        if not isinstance(self.subject_kind, SubjectKind):
            raise ExpertEvaluationError("field_mismatch", "subject_kind is invalid")
        datetime_text(self.observation_anchor, "observation_anchor")
        datetime_text(self.knowledge_cutoff, "knowledge_cutoff")
        if self.knowledge_cutoff > self.observation_anchor:
            raise ExpertEvaluationError(
                "future_evidence_at_observation",
                "knowledge_cutoff cannot be after observation_anchor",
            )
        _require_digest(self.evidence_context_digest, "evidence_context_digest")
        normalized_evidence = _canonical_items(
            self.evidence, key=lambda item: item.key, field_name="evidence"
        )
        if any(item.visible_at > self.knowledge_cutoff for item in normalized_evidence):
            raise ExpertEvaluationError(
                "future_evidence_at_observation",
                "evidence is not visible at the observation cutoff",
            )
        object.__setattr__(self, "evidence", normalized_evidence)
        if not isinstance(self.applicability, ObservationApplicability):
            raise ExpertEvaluationError("field_mismatch", "applicability is invalid")
        _require_token(self.applicability_reason, "applicability_reason")
        if not isinstance(self.raw_value_type, RawValueType) or not isinstance(
            self.evidence_quality, EvidenceQuality
        ):
            raise ExpertEvaluationError("field_mismatch", "observation enum field is invalid")
        self._validate_value_shape()
        expected_prediction_id = derive_prediction_id(
            assessment_binding_digest=self.assessment_binding_digest,
            subject_id=self.subject_id,
            observation_anchor=self.observation_anchor,
            knowledge_cutoff=self.knowledge_cutoff,
            evidence_context_digest=self.evidence_context_digest,
        )
        if self.prediction_id != expected_prediction_id:
            raise ExpertEvaluationError(
                "invalid_identity", "prediction_id does not match prediction-time inputs"
            )

    def _validate_value_shape(self) -> None:
        if self.applicability is ObservationApplicability.ABSTAINED:
            if self.raw_value is not None or self.value_unit is not None:
                raise ExpertEvaluationError(
                    "value_type_mismatch", "abstained observation cannot carry a value"
                )
            return
        if self.raw_value_type is RawValueType.PROBABILITY:
            if (
                not isinstance(self.raw_value, Decimal)
                or not self.raw_value.is_finite()
                or not Decimal("0") <= self.raw_value <= Decimal("1")
                or self.value_unit is not None
            ):
                raise ExpertEvaluationError(
                    "value_type_mismatch", "probability must be a unitless Decimal in [0, 1]"
                )
        elif self.raw_value_type is RawValueType.CONTINUOUS:
            if not isinstance(self.raw_value, Decimal) or not self.raw_value.is_finite():
                raise ExpertEvaluationError(
                    "value_type_mismatch", "continuous observation requires a finite Decimal"
                )
            _require_token(self.value_unit, "value_unit")
        elif self.raw_value_type is RawValueType.ORDINAL:
            if not isinstance(self.raw_value, str) or self.value_unit is not None:
                raise ExpertEvaluationError(
                    "value_type_mismatch", "ordinal observation requires a unitless string"
                )
            _require_token(self.raw_value, "raw_value")
        elif self.raw_value_type is RawValueType.POLICY_ONLY and (
            self.raw_value is not None or self.value_unit is not None
        ):
            raise ExpertEvaluationError(
                "value_type_mismatch", "policy-only observation cannot carry a value"
            )

    def _raw_value_dict(self) -> str | None:
        if isinstance(self.raw_value, Decimal):
            return decimal_text(self.raw_value, "raw_value")
        return self.raw_value

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "producer_id": self.producer_id,
            "producer_version": self.producer_version,
            "prediction_id": self.prediction_id,
            "assessment_id": self.assessment_id,
            "assessment_version": self.assessment_version,
            "assessment_binding_digest": self.assessment_binding_digest,
            "forecast_spec_digest": self.forecast_spec_digest,
            "subject_id": self.subject_id,
            "subject_kind": self.subject_kind.value,
            "observation_anchor": datetime_text(self.observation_anchor, "observation_anchor"),
            "knowledge_cutoff": datetime_text(self.knowledge_cutoff, "knowledge_cutoff"),
            "evidence_context_digest": self.evidence_context_digest,
            "evidence": [item.to_dict() for item in self.evidence],
            "applicability": self.applicability.value,
            "applicability_reason": self.applicability_reason,
            "raw_value_type": self.raw_value_type.value,
            "raw_value": self._raw_value_dict(),
            "value_unit": self.value_unit,
            "evidence_quality": self.evidence_quality.value,
        }

    @classmethod
    def _from_dict(cls, raw: Mapping[str, Any]) -> ExpertObservation:
        cls._check_envelope(raw, cls._FIELDS)
        raw_value_type = _enum_value(RawValueType, raw["raw_value_type"], "raw_value_type")
        raw_value: Decimal | str | None = raw["raw_value"]
        if raw_value is not None and raw_value_type in {
            RawValueType.PROBABILITY,
            RawValueType.CONTINUOUS,
        }:
            raw_value = parse_decimal(raw_value, "raw_value")
        value = cls(
            producer_id=raw["producer_id"],
            producer_version=raw["producer_version"],
            prediction_id=raw["prediction_id"],
            assessment_id=raw["assessment_id"],
            assessment_version=raw["assessment_version"],
            assessment_binding_digest=raw["assessment_binding_digest"],
            forecast_spec_digest=raw["forecast_spec_digest"],
            subject_id=raw["subject_id"],
            subject_kind=_enum_value(SubjectKind, raw["subject_kind"], "subject_kind"),
            observation_anchor=parse_datetime(raw["observation_anchor"], "observation_anchor"),
            knowledge_cutoff=parse_datetime(raw["knowledge_cutoff"], "knowledge_cutoff"),
            evidence_context_digest=raw["evidence_context_digest"],
            evidence=tuple(
                EvidenceRef.from_dict(item, "evidence[]")
                for item in require_array(raw["evidence"], "evidence")
            ),
            applicability=_enum_value(
                ObservationApplicability, raw["applicability"], "applicability"
            ),
            applicability_reason=raw["applicability_reason"],
            raw_value_type=raw_value_type,
            raw_value=raw_value,
            value_unit=raw["value_unit"],
            evidence_quality=_enum_value(
                EvidenceQuality, raw["evidence_quality"], "evidence_quality"
            ),
        )
        cls._check_digest(raw, value)
        return value


@dataclass(frozen=True)
class PolicyEffect(ArtifactContract):
    SCHEMA_VERSION: ClassVar[str] = "quanthus.policy-effect.v2"

    producer_id: str
    producer_version: str
    observation_digest: str
    assessment_id: str
    assessment_version: str
    mapping: PolicyMappingRef
    effect_kind: PolicyEffectKind
    output_payload: Mapping[str, Any] | None
    abstention_reason: str | None
    evidence: tuple[EvidenceRef, ...]

    _FIELDS: ClassVar[set[str]] = {
        "producer_id",
        "producer_version",
        "observation_digest",
        "assessment_id",
        "assessment_version",
        "mapping",
        "effect_kind",
        "output_payload",
        "abstention_reason",
        "evidence",
    }

    def __post_init__(self) -> None:
        _require_identifier(self.producer_id, "producer_id")
        _require_version(self.producer_version, "producer_version")
        _require_digest(self.observation_digest, "observation_digest")
        _require_identifier(self.assessment_id, "assessment_id")
        _require_version(self.assessment_version, "assessment_version")
        if not isinstance(self.effect_kind, PolicyEffectKind):
            raise ExpertEvaluationError("field_mismatch", "effect_kind is invalid")
        if self.effect_kind not in self.mapping.effect_kinds:
            raise ExpertEvaluationError(
                "value_type_mismatch", "effect_kind is not admitted by the mapping"
            )
        object.__setattr__(
            self,
            "evidence",
            _canonical_items(self.evidence, key=lambda item: item.key, field_name="evidence"),
        )
        if self.effect_kind is PolicyEffectKind.ABSTENTION:
            if self.output_payload is not None:
                raise ExpertEvaluationError(
                    "value_type_mismatch", "explicit abstention cannot carry an output payload"
                )
            _require_token(self.abstention_reason, "abstention_reason")
        else:
            if not isinstance(self.output_payload, Mapping) or not self.output_payload:
                raise ExpertEvaluationError(
                    "value_type_mismatch", "non-abstention effect requires a v1 output payload"
                )
            if self.abstention_reason is not None:
                raise ExpertEvaluationError(
                    "value_type_mismatch", "non-abstention effect cannot carry abstention_reason"
                )
            object.__setattr__(self, "output_payload", _freeze_json(self.output_payload))

    @property
    def uniqueness_key(self) -> tuple[str, str, str, str]:
        return (
            self.observation_digest,
            self.mapping.mapping_id,
            self.mapping.mapping_version,
            self.effect_kind.value,
        )

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "producer_id": self.producer_id,
            "producer_version": self.producer_version,
            "observation_digest": self.observation_digest,
            "assessment_id": self.assessment_id,
            "assessment_version": self.assessment_version,
            "mapping": self.mapping.to_dict(),
            "effect_kind": self.effect_kind.value,
            "output_payload": _plain_json(self.output_payload),
            "abstention_reason": self.abstention_reason,
            "evidence": [item.to_dict() for item in self.evidence],
        }

    @classmethod
    def _from_dict(cls, raw: Mapping[str, Any]) -> PolicyEffect:
        cls._check_envelope(raw, cls._FIELDS)
        output_payload = raw["output_payload"]
        if output_payload is not None:
            output_payload = require_object(output_payload, "output_payload")
        value = cls(
            producer_id=raw["producer_id"],
            producer_version=raw["producer_version"],
            observation_digest=raw["observation_digest"],
            assessment_id=raw["assessment_id"],
            assessment_version=raw["assessment_version"],
            mapping=PolicyMappingRef.from_dict(raw["mapping"], "mapping"),
            effect_kind=_enum_value(PolicyEffectKind, raw["effect_kind"], "effect_kind"),
            output_payload=output_payload,
            abstention_reason=raw["abstention_reason"],
            evidence=tuple(
                EvidenceRef.from_dict(item, "evidence[]")
                for item in require_array(raw["evidence"], "evidence")
            ),
        )
        cls._check_digest(raw, value)
        return value


@dataclass(frozen=True)
class OutcomeSettlement(ArtifactContract):
    SCHEMA_VERSION: ClassVar[str] = "quanthus.outcome-settlement.v2"

    producer_id: str
    producer_version: str
    observation_digest: str
    state: SettlementState
    observed_start: date
    observed_end: date
    future_dwd: ArtifactRef | None
    resolver: ArtifactRef
    realized_value_type: RawValueType
    realized_value: Decimal | str | None
    target_score: Decimal | None
    reason_code: str | None

    _FIELDS: ClassVar[set[str]] = {
        "producer_id",
        "producer_version",
        "observation_digest",
        "state",
        "observed_start",
        "observed_end",
        "future_dwd",
        "resolver",
        "realized_value_type",
        "realized_value",
        "target_score",
        "reason_code",
    }

    def __post_init__(self) -> None:
        _require_identifier(self.producer_id, "producer_id")
        _require_version(self.producer_version, "producer_version")
        _require_digest(self.observation_digest, "observation_digest")
        if not isinstance(self.state, SettlementState) or not isinstance(
            self.realized_value_type, RawValueType
        ):
            raise ExpertEvaluationError("field_mismatch", "settlement enum field is invalid")
        date_text(self.observed_start, "observed_start")
        date_text(self.observed_end, "observed_end")
        if self.observed_end < self.observed_start:
            raise ExpertEvaluationError(
                "settlement_state_conflict", "observed window is reversed"
            )
        if self.realized_value_type is RawValueType.POLICY_ONLY:
            raise ExpertEvaluationError(
                "value_type_mismatch", "policy-only observations are not settleable"
            )
        if self.state is SettlementState.SETTLED:
            if self.future_dwd is None or self.realized_value is None or self.target_score is None:
                raise ExpertEvaluationError(
                    "settlement_state_conflict", "settled outcome is incomplete"
                )
            if self.reason_code is not None:
                raise ExpertEvaluationError(
                    "settlement_state_conflict", "settled outcome cannot carry a skip reason"
                )
            decimal_text(self.target_score, "target_score")
            self._validate_realized_value()
        else:
            if self.realized_value is not None or self.target_score is not None:
                raise ExpertEvaluationError(
                    "settlement_state_conflict",
                    "non-settled outcome cannot carry realized value or target score",
                )
            _require_token(self.reason_code, "reason_code")

    def _validate_realized_value(self) -> None:
        if self.realized_value_type in {RawValueType.PROBABILITY, RawValueType.CONTINUOUS}:
            if not isinstance(self.realized_value, Decimal) or not self.realized_value.is_finite():
                raise ExpertEvaluationError(
                    "value_type_mismatch", "numeric settlement requires a finite Decimal"
                )
            if self.realized_value_type is RawValueType.PROBABILITY and not (
                Decimal("0") <= self.realized_value <= Decimal("1")
            ):
                raise ExpertEvaluationError(
                    "value_type_mismatch", "probability settlement must be in [0, 1]"
                )
        elif self.realized_value_type is RawValueType.ORDINAL:
            if not isinstance(self.realized_value, str):
                raise ExpertEvaluationError(
                    "value_type_mismatch", "ordinal settlement requires a string"
                )
            _require_token(self.realized_value, "realized_value")

    def _realized_value_dict(self) -> str | None:
        if isinstance(self.realized_value, Decimal):
            return decimal_text(self.realized_value, "realized_value")
        return self.realized_value

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "producer_id": self.producer_id,
            "producer_version": self.producer_version,
            "observation_digest": self.observation_digest,
            "state": self.state.value,
            "observed_start": date_text(self.observed_start, "observed_start"),
            "observed_end": date_text(self.observed_end, "observed_end"),
            "future_dwd": None if self.future_dwd is None else self.future_dwd.to_dict(),
            "resolver": self.resolver.to_dict(),
            "realized_value_type": self.realized_value_type.value,
            "realized_value": self._realized_value_dict(),
            "target_score": (
                None
                if self.target_score is None
                else decimal_text(self.target_score, "target_score")
            ),
            "reason_code": self.reason_code,
        }

    @classmethod
    def _from_dict(cls, raw: Mapping[str, Any]) -> OutcomeSettlement:
        cls._check_envelope(raw, cls._FIELDS)
        value_type = _enum_value(
            RawValueType, raw["realized_value_type"], "realized_value_type"
        )
        realized_value: Decimal | str | None = raw["realized_value"]
        if realized_value is not None and value_type in {
            RawValueType.PROBABILITY,
            RawValueType.CONTINUOUS,
        }:
            realized_value = parse_decimal(realized_value, "realized_value")
        future_dwd = (
            None
            if raw["future_dwd"] is None
            else ArtifactRef.from_dict(raw["future_dwd"], "future_dwd")
        )
        value = cls(
            producer_id=raw["producer_id"],
            producer_version=raw["producer_version"],
            observation_digest=raw["observation_digest"],
            state=_enum_value(SettlementState, raw["state"], "state"),
            observed_start=parse_date(raw["observed_start"], "observed_start"),
            observed_end=parse_date(raw["observed_end"], "observed_end"),
            future_dwd=future_dwd,
            resolver=ArtifactRef.from_dict(raw["resolver"], "resolver"),
            realized_value_type=value_type,
            realized_value=realized_value,
            target_score=(
                None
                if raw["target_score"] is None
                else parse_decimal(raw["target_score"], "target_score")
            ),
            reason_code=raw["reason_code"],
        )
        cls._check_digest(raw, value)
        return value


@dataclass(frozen=True)
class ExpertScorecard(ArtifactContract):
    SCHEMA_VERSION: ClassVar[str] = "quanthus.expert-scorecard.v2"

    producer_id: str
    producer_version: str
    evaluation_spec_digest: str
    assessment_id: str
    assessment_version: str
    assessment_binding_digest: str
    forecast_spec_digest: str | None
    target_digest: str | None
    resolver_digest: str | None
    population: ArtifactRef
    partition: ArtifactRef
    sample_ledger: SampleLedger
    metric_definitions: tuple[MetricRef, ...]
    metric_results: tuple[MetricResult, ...]
    uncertainty: tuple[UncertaintyEstimate, ...]
    stability: tuple[StabilityEstimate, ...]
    baseline_comparisons: tuple[BaselineComparison, ...]
    decision_change_counts: DecisionChangeCounts
    limitations: tuple[str, ...]

    _FIELDS: ClassVar[set[str]] = {
        "producer_id",
        "producer_version",
        "evaluation_spec_digest",
        "assessment_id",
        "assessment_version",
        "assessment_binding_digest",
        "forecast_spec_digest",
        "target_digest",
        "resolver_digest",
        "population",
        "partition",
        "sample_ledger",
        "metric_definitions",
        "metric_results",
        "uncertainty",
        "stability",
        "baseline_comparisons",
        "decision_change_counts",
        "limitations",
    }

    def __post_init__(self) -> None:
        _require_identifier(self.producer_id, "producer_id")
        _require_version(self.producer_version, "producer_version")
        _require_digest(self.evaluation_spec_digest, "evaluation_spec_digest")
        _require_identifier(self.assessment_id, "assessment_id")
        _require_version(self.assessment_version, "assessment_version")
        _require_digest(self.assessment_binding_digest, "assessment_binding_digest")
        if self.forecast_spec_digest is not None:
            _require_digest(self.forecast_spec_digest, "forecast_spec_digest")
        if self.target_digest is not None:
            _require_digest(self.target_digest, "target_digest")
        if self.resolver_digest is not None:
            _require_digest(self.resolver_digest, "resolver_digest")
        for field_name in (
            "metric_definitions",
            "metric_results",
            "uncertainty",
            "stability",
            "baseline_comparisons",
        ):
            object.__setattr__(
                self,
                field_name,
                _canonical_items(
                    getattr(self, field_name),
                    key=lambda item: item.key,
                    field_name=field_name,
                ),
            )
        limitations = tuple(_require_text(item, "limitations") for item in self.limitations)
        object.__setattr__(
            self,
            "limitations",
            _canonical_items(limitations, key=lambda item: item, field_name="limitations"),
        )
        definition_keys = {
            (item.metric_id, item.metric_version) for item in self.metric_definitions
        }
        if any(item.key not in definition_keys for item in self.metric_results):
            raise ExpertEvaluationError(
                "metric_not_registered", "metric result is absent from metric_definitions"
            )
        metric_ids = {item.metric_id for item in self.metric_definitions}
        if any(item.metric_id not in metric_ids for item in self.uncertainty):
            raise ExpertEvaluationError(
                "metric_not_registered", "uncertainty refers to an unregistered metric"
            )
        if any(item.metric_id not in metric_ids for item in self.stability):
            raise ExpertEvaluationError(
                "metric_not_registered", "stability refers to an unregistered metric"
            )
        if any(item.metric_id not in metric_ids for item in self.baseline_comparisons):
            raise ExpertEvaluationError(
                "metric_not_registered", "baseline refers to an unregistered metric"
            )

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "producer_id": self.producer_id,
            "producer_version": self.producer_version,
            "evaluation_spec_digest": self.evaluation_spec_digest,
            "assessment_id": self.assessment_id,
            "assessment_version": self.assessment_version,
            "assessment_binding_digest": self.assessment_binding_digest,
            "forecast_spec_digest": self.forecast_spec_digest,
            "target_digest": self.target_digest,
            "resolver_digest": self.resolver_digest,
            "population": self.population.to_dict(),
            "partition": self.partition.to_dict(),
            "sample_ledger": self.sample_ledger.to_dict(),
            "metric_definitions": [item.to_dict() for item in self.metric_definitions],
            "metric_results": [item.to_dict() for item in self.metric_results],
            "uncertainty": [item.to_dict() for item in self.uncertainty],
            "stability": [item.to_dict() for item in self.stability],
            "baseline_comparisons": [item.to_dict() for item in self.baseline_comparisons],
            "decision_change_counts": self.decision_change_counts.to_dict(),
            "limitations": list(self.limitations),
        }

    @classmethod
    def _from_dict(cls, raw: Mapping[str, Any]) -> ExpertScorecard:
        cls._check_envelope(raw, cls._FIELDS)
        value = cls(
            producer_id=raw["producer_id"],
            producer_version=raw["producer_version"],
            evaluation_spec_digest=raw["evaluation_spec_digest"],
            assessment_id=raw["assessment_id"],
            assessment_version=raw["assessment_version"],
            assessment_binding_digest=raw["assessment_binding_digest"],
            forecast_spec_digest=raw["forecast_spec_digest"],
            target_digest=raw["target_digest"],
            resolver_digest=raw["resolver_digest"],
            population=ArtifactRef.from_dict(raw["population"], "population"),
            partition=ArtifactRef.from_dict(raw["partition"], "partition"),
            sample_ledger=SampleLedger.from_dict(raw["sample_ledger"], "sample_ledger"),
            metric_definitions=tuple(
                MetricRef.from_dict(item, "metric_definitions[]")
                for item in require_array(raw["metric_definitions"], "metric_definitions")
            ),
            metric_results=tuple(
                MetricResult.from_dict(item, "metric_results[]")
                for item in require_array(raw["metric_results"], "metric_results")
            ),
            uncertainty=tuple(
                UncertaintyEstimate.from_dict(item, "uncertainty[]")
                for item in require_array(raw["uncertainty"], "uncertainty")
            ),
            stability=tuple(
                StabilityEstimate.from_dict(item, "stability[]")
                for item in require_array(raw["stability"], "stability")
            ),
            baseline_comparisons=tuple(
                BaselineComparison.from_dict(item, "baseline_comparisons[]")
                for item in require_array(
                    raw["baseline_comparisons"], "baseline_comparisons"
                )
            ),
            decision_change_counts=DecisionChangeCounts.from_dict(
                raw["decision_change_counts"], "decision_change_counts"
            ),
            limitations=tuple(require_array(raw["limitations"], "limitations")),
        )
        cls._check_digest(raw, value)
        return value


@dataclass(frozen=True)
class ExpertLifecycleDecision(ArtifactContract):
    SCHEMA_VERSION: ClassVar[str] = "quanthus.expert-lifecycle-decision.v2"

    producer_id: str
    producer_version: str
    decision: LifecycleDecisionKind
    actor_type: LifecycleActorType
    actor_id: str
    decided_at: datetime
    rationale: str
    limitations: tuple[str, ...]
    shadow_disposition: ShadowDisposition
    assessment_digest: str
    policy_digests: tuple[str, ...]
    target_digest: str
    resolver_digest: str
    dwd_digest: str
    split_digest: str
    strategy_digest: str
    scorecard_digest: str
    attribution_digest: str
    package_digest: str

    _FIELDS: ClassVar[set[str]] = {
        "producer_id",
        "producer_version",
        "decision",
        "actor_type",
        "actor_id",
        "decided_at",
        "rationale",
        "limitations",
        "shadow_disposition",
        "assessment_digest",
        "policy_digests",
        "target_digest",
        "resolver_digest",
        "dwd_digest",
        "split_digest",
        "strategy_digest",
        "scorecard_digest",
        "attribution_digest",
        "package_digest",
    }

    def __post_init__(self) -> None:
        _require_identifier(self.producer_id, "producer_id")
        _require_version(self.producer_version, "producer_version")
        if not isinstance(self.decision, LifecycleDecisionKind) or not isinstance(
            self.actor_type, LifecycleActorType
        ):
            raise ExpertEvaluationError("field_mismatch", "lifecycle enum field is invalid")
        _require_text(self.actor_id, "actor_id")
        datetime_text(self.decided_at, "decided_at")
        _require_text(self.rationale, "rationale")
        limitations = tuple(_require_text(item, "limitations") for item in self.limitations)
        object.__setattr__(
            self,
            "limitations",
            _canonical_items(limitations, key=lambda item: item, field_name="limitations"),
        )
        digests = tuple(_require_digest(item, "policy_digests") for item in self.policy_digests)
        object.__setattr__(
            self,
            "policy_digests",
            _canonical_items(digests, key=lambda item: item, field_name="policy_digests"),
        )
        for field_name in (
            "assessment_digest",
            "target_digest",
            "resolver_digest",
            "dwd_digest",
            "split_digest",
            "strategy_digest",
            "scorecard_digest",
            "attribution_digest",
            "package_digest",
        ):
            _require_digest(getattr(self, field_name), field_name)
        if self.actor_type is not LifecycleActorType.HUMAN:
            raise ExpertEvaluationError(
                "automated_lifecycle_actor", "lifecycle decision must be made by a human"
            )
        self._validate_shadow_compatibility()

    def _validate_shadow_compatibility(self) -> None:
        status = self.shadow_disposition.status
        budget = self.shadow_disposition.observation_budget
        if self.decision is LifecycleDecisionKind.KEEP:
            if status is not ShadowStatus.ENABLED:
                raise ExpertEvaluationError(
                    "lifecycle_disposition_conflict", "keep requires enabled Shadow"
                )
        elif self.decision is LifecycleDecisionKind.INCONCLUSIVE:
            if status is ShadowStatus.ENABLED and budget is None:
                raise ExpertEvaluationError(
                    "lifecycle_disposition_conflict",
                    "enabled inconclusive Shadow requires a finite budget",
                )
            if status is ShadowStatus.DISABLED and budget is not None:
                raise ExpertEvaluationError(
                    "lifecycle_disposition_conflict",
                    "disabled inconclusive Shadow cannot carry a budget",
                )
        elif status is not ShadowStatus.DISABLED or budget is not None:
            raise ExpertEvaluationError(
                "lifecycle_disposition_conflict",
                "replacement, merge, remap, or retirement must disable current version",
            )

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "producer_id": self.producer_id,
            "producer_version": self.producer_version,
            "decision": self.decision.value,
            "actor_type": self.actor_type.value,
            "actor_id": self.actor_id,
            "decided_at": datetime_text(self.decided_at, "decided_at"),
            "rationale": self.rationale,
            "limitations": list(self.limitations),
            "shadow_disposition": self.shadow_disposition.to_dict(),
            "assessment_digest": self.assessment_digest,
            "policy_digests": list(self.policy_digests),
            "target_digest": self.target_digest,
            "resolver_digest": self.resolver_digest,
            "dwd_digest": self.dwd_digest,
            "split_digest": self.split_digest,
            "strategy_digest": self.strategy_digest,
            "scorecard_digest": self.scorecard_digest,
            "attribution_digest": self.attribution_digest,
            "package_digest": self.package_digest,
        }

    @classmethod
    def _from_dict(cls, raw: Mapping[str, Any]) -> ExpertLifecycleDecision:
        cls._check_envelope(raw, cls._FIELDS)
        value = cls(
            producer_id=raw["producer_id"],
            producer_version=raw["producer_version"],
            decision=_enum_value(LifecycleDecisionKind, raw["decision"], "decision"),
            actor_type=_enum_value(LifecycleActorType, raw["actor_type"], "actor_type"),
            actor_id=raw["actor_id"],
            decided_at=parse_datetime(raw["decided_at"], "decided_at"),
            rationale=raw["rationale"],
            limitations=tuple(require_array(raw["limitations"], "limitations")),
            shadow_disposition=ShadowDisposition.from_dict(
                raw["shadow_disposition"], "shadow_disposition"
            ),
            assessment_digest=raw["assessment_digest"],
            policy_digests=tuple(require_array(raw["policy_digests"], "policy_digests")),
            target_digest=raw["target_digest"],
            resolver_digest=raw["resolver_digest"],
            dwd_digest=raw["dwd_digest"],
            split_digest=raw["split_digest"],
            strategy_digest=raw["strategy_digest"],
            scorecard_digest=raw["scorecard_digest"],
            attribution_digest=raw["attribution_digest"],
            package_digest=raw["package_digest"],
        )
        cls._check_digest(raw, value)
        return value


ARTIFACT_TYPES_BY_SCHEMA: dict[str, type[ArtifactContract]] = {
    item.SCHEMA_VERSION: item
    for item in (
        ExpertEvaluationSpec,
        ForecastSpec,
        ExpertObservation,
        PolicyEffect,
        OutcomeSettlement,
        ExpertScorecard,
        ExpertLifecycleDecision,
    )
}

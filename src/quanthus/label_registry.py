"""Versioned label cards and deterministic evaluator boundaries."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, TypeVar

from quanthus.feature_registry import (
    FeatureDefinition,
    FeatureLifecycle,
    FeatureLineage,
    FeatureOutputType,
    FeatureRegistry,
    FeatureRegistryError,
    FeatureSource,
)

_LABEL_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,127}$")
_SEMANTIC_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_REGISTRY_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TOKEN = re.compile(r"^[a-z][a-z0-9_.-]{1,127}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_EnumType = TypeVar("_EnumType", bound=Enum)
LabelScalar = bool | Decimal | int | str
LabelParameterValue = bool | int | str


class LabelRegistryError(ValueError):
    """Raised when label-card configuration is incomplete or inconsistent."""


class LabelEvaluationError(ValueError):
    """Raised when an evaluator crosses a declared card or evidence boundary."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}")


class LabelLifecycle(str, Enum):
    DRAFT = "draft"
    SHADOW = "shadow"
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class LabelDimension(str, Enum):
    PRIMARY_STAGE = "primary_stage"
    TREND_ENERGY = "trend_energy"
    PRICE_VOLUME_ACCEPTANCE = "price_volume_acceptance"
    KEY_LEVEL_RELATIONSHIP = "key_level_relationship"
    POSITION_RISK = "position_risk"
    MARKET_SECTOR_CONTEXT = "market_sector_context"
    TRADABILITY = "tradability"


class LabelResultStatus(str, Enum):
    APPLIED = "applied"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise LabelRegistryError(f"{path} must be an object")
    return value


def _sequence(value: Any, path: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise LabelRegistryError(f"{path} must be an array")
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LabelRegistryError(f"{path} must be a non-empty string")
    return value


def _positive_int(value: Any, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise LabelRegistryError(f"{path} must be a positive integer")
    return value


def _enum(value: Any, enum_type: type[_EnumType], path: str) -> _EnumType:
    raw = _string(value, path)
    try:
        return enum_type(raw)
    except ValueError as exc:
        raise LabelRegistryError(f"{path} is unsupported: {raw}") from exc


def _exact_keys(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    missing = sorted(expected.difference(value))
    unexpected = sorted(set(value).difference(expected))
    if missing or unexpected:
        raise LabelRegistryError(
            f"{path} keys do not match contract; missing={missing}, unexpected={unexpected}"
        )


def _decimal(value: Any, path: str) -> Decimal:
    if not isinstance(value, str):
        raise LabelRegistryError(f"{path} must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise LabelRegistryError(f"{path} must be a decimal string") from exc
    if not parsed.is_finite():
        raise LabelRegistryError(f"{path} must be finite")
    return parsed


def _tokens(value: Any, path: str) -> tuple[str, ...]:
    tokens = tuple(
        _string(item, f"{path}[{index}]") for index, item in enumerate(_sequence(value, path))
    )
    if len(tokens) != len(set(tokens)):
        raise LabelRegistryError(f"{path} must not contain duplicates")
    if any(not _TOKEN.fullmatch(token) for token in tokens):
        raise LabelRegistryError(f"{path} contains an invalid token")
    return tokens


@dataclass(frozen=True)
class FeatureRequirement:
    feature_id: str
    version: str

    def __post_init__(self) -> None:
        if not _LABEL_ID.fullmatch(self.feature_id):
            raise LabelRegistryError(f"invalid required feature_id: {self.feature_id}")
        if not _SEMANTIC_VERSION.fullmatch(self.version):
            raise LabelRegistryError(
                f"required feature {self.feature_id} version must use MAJOR.MINOR.PATCH"
            )

    @property
    def key(self) -> tuple[str, str]:
        return (self.feature_id, self.version)

    def to_dict(self) -> dict[str, str]:
        return {"feature_id": self.feature_id, "version": self.version}


@dataclass(frozen=True)
class LabelCounterexample:
    case_id: str
    description: str
    expected_value: str
    evidence_ref: str

    def __post_init__(self) -> None:
        if not _TOKEN.fullmatch(self.case_id):
            raise LabelRegistryError(f"invalid counterexample case_id: {self.case_id}")
        if any(
            not value.strip()
            for value in (self.description, self.expected_value, self.evidence_ref)
        ):
            raise LabelRegistryError("counterexample fields must not be empty")

    def to_dict(self) -> dict[str, str]:
        return {
            "case_id": self.case_id,
            "description": self.description,
            "expected_value": self.expected_value,
            "evidence_ref": self.evidence_ref,
        }


@dataclass(frozen=True)
class ExpectedFrequency:
    minimum_rate: Decimal
    maximum_rate: Decimal
    sample_scope: str

    def __post_init__(self) -> None:
        if (
            not self.minimum_rate.is_finite()
            or not self.maximum_rate.is_finite()
            or not Decimal("0") <= self.minimum_rate <= self.maximum_rate <= Decimal("1")
        ):
            raise LabelRegistryError("expected frequency rates must satisfy 0 <= min <= max <= 1")
        if not self.sample_scope.strip():
            raise LabelRegistryError("expected frequency sample_scope must not be empty")

    def to_dict(self) -> dict[str, str]:
        return {
            "minimum_rate": str(self.minimum_rate),
            "maximum_rate": str(self.maximum_rate),
            "sample_scope": self.sample_scope,
        }


@dataclass(frozen=True)
class LabelParameter:
    name: str
    value: LabelParameterValue

    def __post_init__(self) -> None:
        if not _TOKEN.fullmatch(self.name):
            raise LabelRegistryError(f"invalid label parameter name: {self.name}")
        if not isinstance(self.value, (str, int, bool)) or (
            isinstance(self.value, str) and not self.value.strip()
        ):
            raise LabelRegistryError(f"invalid label parameter value: {self.name}")

    def to_pair(self) -> tuple[str, LabelParameterValue]:
        return (self.name, self.value)


@dataclass(frozen=True)
class LabelCard:
    label_id: str
    display_name: str
    dimension: LabelDimension
    version: str
    lifecycle: LabelLifecycle
    source_fields: tuple[FeatureSource, ...]
    feature_requirements: tuple[FeatureRequirement, ...]
    rule: str
    parameters: tuple[LabelParameter, ...]
    lookback_sessions: int
    minimum_history_sessions: int
    mutually_exclusive_with: tuple[str, ...]
    coexists_with: tuple[str, ...]
    output_values: tuple[str, ...]
    evidence_fields: tuple[str, ...]
    reason_codes: tuple[str, ...]
    counterexamples: tuple[LabelCounterexample, ...]
    expected_frequency: ExpectedFrequency
    validation_sample_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not _LABEL_ID.fullmatch(self.label_id):
            raise LabelRegistryError(f"invalid label_id: {self.label_id}")
        if not _SEMANTIC_VERSION.fullmatch(self.version):
            raise LabelRegistryError(f"label {self.label_id} version must use MAJOR.MINOR.PATCH")
        if not self.display_name.strip() or not self.rule.strip():
            raise LabelRegistryError(f"label {self.label_id} name and rule must not be empty")
        for field_name, required_values in (
            ("source_fields", self.source_fields),
            ("feature_requirements", self.feature_requirements),
            ("output_values", self.output_values),
            ("evidence_fields", self.evidence_fields),
            ("reason_codes", self.reason_codes),
            ("validation_sample_refs", self.validation_sample_refs),
        ):
            if not required_values:
                raise LabelRegistryError(f"label {self.label_id} {field_name} must not be empty")
        source_keys = [(source.dataset, source.column) for source in self.source_fields]
        feature_keys = [requirement.key for requirement in self.feature_requirements]
        parameter_names = [parameter.name for parameter in self.parameters]
        for field_name, unique_values in (
            ("source_fields", source_keys),
            ("feature_requirements", feature_keys),
            ("parameters", parameter_names),
            ("output_values", list(self.output_values)),
            ("evidence_fields", list(self.evidence_fields)),
            ("reason_codes", list(self.reason_codes)),
            ("validation_sample_refs", list(self.validation_sample_refs)),
        ):
            if len(unique_values) != len(set(unique_values)):
                raise LabelRegistryError(f"label {self.label_id} {field_name} must be unique")
        if any(
            not _TOKEN.fullmatch(value)
            for value in (*self.output_values, *self.evidence_fields, *self.reason_codes)
        ):
            raise LabelRegistryError(f"label {self.label_id} outputs and evidence must use tokens")
        if self.label_id in self.mutually_exclusive_with or self.label_id in self.coexists_with:
            raise LabelRegistryError(f"label {self.label_id} cannot reference itself")
        overlap = set(self.mutually_exclusive_with).intersection(self.coexists_with)
        if overlap:
            raise LabelRegistryError(
                f"label {self.label_id} cannot both exclude and coexist with {sorted(overlap)}"
            )
        counts = (self.lookback_sessions, self.minimum_history_sessions)
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in counts
        ):
            raise LabelRegistryError(f"label {self.label_id} history counts must be positive")
        if self.minimum_history_sessions > self.lookback_sessions:
            raise LabelRegistryError(
                f"label {self.label_id} minimum history cannot exceed lookback"
            )
        if len(self.counterexamples) < 2:
            raise LabelRegistryError(f"label {self.label_id} requires at least two counterexamples")
        case_ids = [counterexample.case_id for counterexample in self.counterexamples]
        if len(case_ids) != len(set(case_ids)):
            raise LabelRegistryError(f"label {self.label_id} counterexample ids must be unique")
        if any(
            counterexample.expected_value not in self.output_values
            for counterexample in self.counterexamples
        ):
            raise LabelRegistryError(
                f"label {self.label_id} counterexamples must use declared output values"
            )

    @property
    def key(self) -> tuple[str, str]:
        return (self.label_id, self.version)

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "label_id": self.label_id,
            "display_name": self.display_name,
            "dimension": self.dimension.value,
            "version": self.version,
            "lifecycle": self.lifecycle.value,
            "source_fields": [
                source.to_dict()
                for source in sorted(
                    self.source_fields, key=lambda item: (item.dataset, item.column)
                )
            ],
            "feature_requirements": [
                requirement.to_dict()
                for requirement in sorted(self.feature_requirements, key=lambda item: item.key)
            ],
            "rule": self.rule,
            "parameters": {
                name: value
                for name, value in sorted(parameter.to_pair() for parameter in self.parameters)
            },
            "lookback_sessions": self.lookback_sessions,
            "minimum_history_sessions": self.minimum_history_sessions,
            "mutually_exclusive_with": sorted(self.mutually_exclusive_with),
            "coexists_with": sorted(self.coexists_with),
            "output_values": list(self.output_values),
            "evidence_fields": list(self.evidence_fields),
            "reason_codes": list(self.reason_codes),
            "counterexamples": [
                counterexample.to_dict()
                for counterexample in sorted(self.counterexamples, key=lambda item: item.case_id)
            ],
            "expected_frequency": self.expected_frequency.to_dict(),
            "validation_sample_refs": list(self.validation_sample_refs),
        }


@dataclass(frozen=True)
class LabelRegistry:
    path: Path
    schema_version: str
    registry_version: str
    cards: tuple[LabelCard, ...]

    def __post_init__(self) -> None:
        if self.schema_version != "1.0.0":
            raise LabelRegistryError("label registry schema_version must be 1.0.0")
        if not _REGISTRY_VERSION.fullmatch(self.registry_version):
            raise LabelRegistryError("label registry_version is invalid")
        if not self.cards:
            raise LabelRegistryError("label registry cards must not be empty")
        keys = [card.key for card in self.cards]
        if len(keys) != len(set(keys)):
            raise LabelRegistryError("label registry contains duplicate label/version keys")
        active_ids = [
            card.label_id for card in self.cards if card.lifecycle is LabelLifecycle.ACTIVE
        ]
        if len(active_ids) != len(set(active_ids)):
            raise LabelRegistryError("label registry permits at most one active version per id")

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "registry_version": self.registry_version,
            "cards": [card.to_dict() for card in sorted(self.cards, key=lambda item: item.key)],
        }

    def get(self, label_id: str, version: str) -> LabelCard:
        matches = [
            card for card in self.cards if card.label_id == label_id and card.version == version
        ]
        if len(matches) != 1:
            raise LabelRegistryError(f"unknown label/version: {label_id}@{version}")
        return matches[0]

    def active(self, label_id: str) -> LabelCard:
        matches = [
            card
            for card in self.cards
            if card.label_id == label_id and card.lifecycle is LabelLifecycle.ACTIVE
        ]
        if len(matches) != 1:
            raise LabelRegistryError(f"label does not have exactly one active version: {label_id}")
        return matches[0]


@dataclass(frozen=True)
class FeatureFact:
    security_code: str
    feature_id: str
    feature_version: str
    lineage: FeatureLineage
    value: LabelScalar | None
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.security_code.strip():
            raise LabelEvaluationError("invalid_feature_fact", "security_code must not be empty")
        if (self.feature_id, self.feature_version) != (
            self.lineage.feature_id,
            self.lineage.feature_version,
        ):
            raise LabelEvaluationError(
                "feature_lineage_mismatch", "feature fact identity does not match lineage"
            )
        if self.value is None and (
            self.unavailable_reason is None or not self.unavailable_reason.strip()
        ):
            raise LabelEvaluationError(
                "invalid_feature_fact", "unavailable feature facts require a reason"
            )
        if self.value is not None and self.unavailable_reason is not None:
            raise LabelEvaluationError(
                "invalid_feature_fact", "available feature facts cannot have an unavailable reason"
            )
        if isinstance(self.value, Decimal) and not self.value.is_finite():
            raise LabelEvaluationError(
                "invalid_feature_fact", "decimal feature value must be finite"
            )

    @property
    def key(self) -> tuple[str, str]:
        return (self.feature_id, self.feature_version)


@dataclass(frozen=True)
class LabelEvaluationContext:
    security_code: str
    snapshot_id: str
    knowledge_cutoff: datetime
    facts: tuple[FeatureFact, ...]

    def __post_init__(self) -> None:
        if not self.security_code.strip() or not self.snapshot_id.strip():
            raise LabelEvaluationError("invalid_context", "context identity must not be empty")
        if self.knowledge_cutoff.tzinfo is None or self.knowledge_cutoff.utcoffset() is None:
            raise LabelEvaluationError(
                "invalid_context", "context knowledge_cutoff must be timezone-aware"
            )
        if not self.facts:
            raise LabelEvaluationError("invalid_context", "context facts must not be empty")
        keys = [fact.key for fact in self.facts]
        if len(keys) != len(set(keys)):
            raise LabelEvaluationError("invalid_context", "context feature facts must be unique")
        for fact in self.facts:
            if fact.security_code != self.security_code:
                raise LabelEvaluationError(
                    "context_security_mismatch", "feature fact security does not match context"
                )
            if (
                fact.lineage.snapshot_id != self.snapshot_id
                or fact.lineage.knowledge_cutoff != self.knowledge_cutoff
            ):
                raise LabelEvaluationError(
                    "context_snapshot_mismatch", "feature fact snapshot does not match context"
                )

    def fact(self, requirement: FeatureRequirement) -> FeatureFact:
        matches = [fact for fact in self.facts if fact.key == requirement.key]
        if len(matches) != 1:
            raise LabelEvaluationError(
                "missing_required_feature",
                f"missing feature {requirement.feature_id}@{requirement.version}",
            )
        return matches[0]


@dataclass(frozen=True)
class LabelEvidence:
    name: str
    value: str

    def __post_init__(self) -> None:
        if not _TOKEN.fullmatch(self.name) or not self.value.strip():
            raise LabelEvaluationError("invalid_evidence", "evidence name and value are invalid")

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "value": self.value}


@dataclass(frozen=True)
class LabelDecision:
    status: LabelResultStatus
    value: str | None
    reason_code: str
    evidence: tuple[LabelEvidence, ...]

    def __post_init__(self) -> None:
        if not _TOKEN.fullmatch(self.reason_code):
            raise LabelEvaluationError("invalid_decision", "reason_code is invalid")
        if self.status is LabelResultStatus.APPLIED:
            if self.value is None or not self.value.strip():
                raise LabelEvaluationError("invalid_decision", "applied labels require a value")
        elif self.value is not None:
            raise LabelEvaluationError("invalid_decision", "non-applied labels cannot have a value")
        names = [item.name for item in self.evidence]
        if not names or len(names) != len(set(names)):
            raise LabelEvaluationError(
                "invalid_decision", "decision evidence must be non-empty and unique"
            )


class LabelEvaluator(Protocol):
    @property
    def evaluator_id(self) -> str: ...

    @property
    def evaluator_version(self) -> str: ...

    def evaluate(self, card: LabelCard, context: LabelEvaluationContext) -> LabelDecision: ...


@dataclass(frozen=True)
class FeatureEvidenceRef:
    feature_id: str
    feature_version: str
    lineage_digest: str

    def __post_init__(self) -> None:
        if not _LABEL_ID.fullmatch(self.feature_id) or not _SEMANTIC_VERSION.fullmatch(
            self.feature_version
        ):
            raise LabelEvaluationError("invalid_result", "feature evidence identity is invalid")
        if not _SHA256.fullmatch(self.lineage_digest):
            raise LabelEvaluationError("invalid_result", "feature evidence digest must use sha256")

    def to_dict(self) -> dict[str, str]:
        return {
            "feature_id": self.feature_id,
            "feature_version": self.feature_version,
            "lineage_digest": self.lineage_digest,
        }


@dataclass(frozen=True)
class LabelResult:
    label_id: str
    label_version: str
    card_digest: str
    dimension: LabelDimension
    security_code: str
    snapshot_id: str
    knowledge_cutoff: datetime
    status: LabelResultStatus
    value: str | None
    reason_code: str
    evidence: tuple[LabelEvidence, ...]
    feature_evidence: tuple[FeatureEvidenceRef, ...]
    evaluator_id: str
    evaluator_version: str

    def __post_init__(self) -> None:
        if not _LABEL_ID.fullmatch(self.label_id) or not _SEMANTIC_VERSION.fullmatch(
            self.label_version
        ):
            raise LabelEvaluationError("invalid_result", "label result identity is invalid")
        if not _SHA256.fullmatch(self.card_digest):
            raise LabelEvaluationError("invalid_result", "card_digest must use sha256")
        if not self.security_code.strip() or not self.snapshot_id.strip():
            detail = "result context identity must not be empty"
            raise LabelEvaluationError("invalid_result", detail)
        if self.knowledge_cutoff.tzinfo is None or self.knowledge_cutoff.utcoffset() is None:
            raise LabelEvaluationError(
                "invalid_result", "result knowledge_cutoff must be timezone-aware"
            )
        if not _TOKEN.fullmatch(self.reason_code):
            raise LabelEvaluationError("invalid_result", "result reason_code is invalid")
        if self.status is LabelResultStatus.APPLIED:
            if self.value is None or not self.value.strip():
                raise LabelEvaluationError("invalid_result", "applied result requires a value")
        elif self.value is not None:
            raise LabelEvaluationError("invalid_result", "non-applied result cannot have a value")
        evidence_names = [item.name for item in self.evidence]
        feature_keys = [(item.feature_id, item.feature_version) for item in self.feature_evidence]
        if not evidence_names or len(evidence_names) != len(set(evidence_names)):
            detail = "result evidence must be non-empty and unique"
            raise LabelEvaluationError("invalid_result", detail)
        if not feature_keys or len(feature_keys) != len(set(feature_keys)):
            raise LabelEvaluationError(
                "invalid_result", "result feature evidence must be non-empty and unique"
            )
        if not self.evaluator_id.strip() or not _SEMANTIC_VERSION.fullmatch(self.evaluator_version):
            raise LabelEvaluationError("invalid_result", "result evaluator identity is invalid")

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "label_id": self.label_id,
            "label_version": self.label_version,
            "card_digest": self.card_digest,
            "dimension": self.dimension.value,
            "security_code": self.security_code,
            "snapshot_id": self.snapshot_id,
            "knowledge_cutoff": self.knowledge_cutoff.isoformat(),
            "status": self.status.value,
            "value": self.value,
            "reason_code": self.reason_code,
            "evidence": [
                item.to_dict() for item in sorted(self.evidence, key=lambda item: item.name)
            ],
            "feature_evidence": [
                item.to_dict()
                for item in sorted(
                    self.feature_evidence,
                    key=lambda item: (item.feature_id, item.feature_version),
                )
            ],
            "evaluator_id": self.evaluator_id,
            "evaluator_version": self.evaluator_version,
        }


def _feature_requirement(value: Any, path: str) -> FeatureRequirement:
    raw = _mapping(value, path)
    _exact_keys(raw, {"feature_id", "version"}, path)
    return FeatureRequirement(
        feature_id=_string(raw.get("feature_id"), f"{path}.feature_id"),
        version=_string(raw.get("version"), f"{path}.version"),
    )


def _source(value: Any, path: str) -> FeatureSource:
    raw = _mapping(value, path)
    _exact_keys(raw, {"dataset", "column"}, path)
    return FeatureSource(
        dataset=_string(raw.get("dataset"), f"{path}.dataset"),
        column=_string(raw.get("column"), f"{path}.column"),
    )


def _counterexample(value: Any, path: str) -> LabelCounterexample:
    raw = _mapping(value, path)
    _exact_keys(raw, {"case_id", "description", "expected_value", "evidence_ref"}, path)
    return LabelCounterexample(
        case_id=_string(raw.get("case_id"), f"{path}.case_id"),
        description=_string(raw.get("description"), f"{path}.description"),
        expected_value=_string(raw.get("expected_value"), f"{path}.expected_value"),
        evidence_ref=_string(raw.get("evidence_ref"), f"{path}.evidence_ref"),
    )


def _frequency(value: Any, path: str) -> ExpectedFrequency:
    raw = _mapping(value, path)
    _exact_keys(raw, {"minimum_rate", "maximum_rate", "sample_scope"}, path)
    return ExpectedFrequency(
        minimum_rate=_decimal(raw.get("minimum_rate"), f"{path}.minimum_rate"),
        maximum_rate=_decimal(raw.get("maximum_rate"), f"{path}.maximum_rate"),
        sample_scope=_string(raw.get("sample_scope"), f"{path}.sample_scope"),
    )


def _parameters(value: Any, path: str) -> tuple[LabelParameter, ...]:
    raw = _mapping(value, path)
    parameters: list[LabelParameter] = []
    for name, item in sorted(raw.items()):
        if not isinstance(item, (str, int, bool)) or (isinstance(item, str) and not item.strip()):
            raise LabelRegistryError(f"{path}.{name} must be a string, integer, or boolean")
        parameters.append(LabelParameter(name=str(name), value=item))
    return tuple(parameters)


def _card(value: Any, path: str) -> LabelCard:
    raw = _mapping(value, path)
    expected = {
        "label_id",
        "display_name",
        "dimension",
        "version",
        "lifecycle",
        "source_fields",
        "feature_requirements",
        "rule",
        "parameters",
        "lookback_sessions",
        "minimum_history_sessions",
        "mutually_exclusive_with",
        "coexists_with",
        "output_values",
        "evidence_fields",
        "reason_codes",
        "counterexamples",
        "expected_frequency",
        "validation_sample_refs",
    }
    _exact_keys(raw, expected, path)
    return LabelCard(
        label_id=_string(raw.get("label_id"), f"{path}.label_id"),
        display_name=_string(raw.get("display_name"), f"{path}.display_name"),
        dimension=_enum(raw.get("dimension"), LabelDimension, f"{path}.dimension"),
        version=_string(raw.get("version"), f"{path}.version"),
        lifecycle=_enum(raw.get("lifecycle"), LabelLifecycle, f"{path}.lifecycle"),
        source_fields=tuple(
            _source(item, f"{path}.source_fields[{index}]")
            for index, item in enumerate(
                _sequence(raw.get("source_fields"), f"{path}.source_fields")
            )
        ),
        feature_requirements=tuple(
            _feature_requirement(item, f"{path}.feature_requirements[{index}]")
            for index, item in enumerate(
                _sequence(raw.get("feature_requirements"), f"{path}.feature_requirements")
            )
        ),
        rule=_string(raw.get("rule"), f"{path}.rule"),
        parameters=_parameters(raw.get("parameters"), f"{path}.parameters"),
        lookback_sessions=_positive_int(raw.get("lookback_sessions"), f"{path}.lookback_sessions"),
        minimum_history_sessions=_positive_int(
            raw.get("minimum_history_sessions"), f"{path}.minimum_history_sessions"
        ),
        mutually_exclusive_with=_tokens(
            raw.get("mutually_exclusive_with"), f"{path}.mutually_exclusive_with"
        ),
        coexists_with=_tokens(raw.get("coexists_with"), f"{path}.coexists_with"),
        output_values=_tokens(raw.get("output_values"), f"{path}.output_values"),
        evidence_fields=_tokens(raw.get("evidence_fields"), f"{path}.evidence_fields"),
        reason_codes=_tokens(raw.get("reason_codes"), f"{path}.reason_codes"),
        counterexamples=tuple(
            _counterexample(item, f"{path}.counterexamples[{index}]")
            for index, item in enumerate(
                _sequence(raw.get("counterexamples"), f"{path}.counterexamples")
            )
        ),
        expected_frequency=_frequency(raw.get("expected_frequency"), f"{path}.expected_frequency"),
        validation_sample_refs=tuple(
            _string(item, f"{path}.validation_sample_refs[{index}]")
            for index, item in enumerate(
                _sequence(raw.get("validation_sample_refs"), f"{path}.validation_sample_refs")
            )
        ),
    )


def load_label_registry(path: Path) -> LabelRegistry:
    """Load strict label cards without implicit defaults or ignored fields."""

    resolved = path.resolve()
    try:
        raw_value = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LabelRegistryError(f"label registry does not exist: {resolved}") from exc
    except json.JSONDecodeError as exc:
        raise LabelRegistryError(f"invalid label registry JSON: {exc}") from exc
    raw = _mapping(raw_value, "label_registry")
    _exact_keys(raw, {"schema_version", "registry_version", "cards"}, "label_registry")
    cards = tuple(
        _card(item, f"cards[{index}]")
        for index, item in enumerate(_sequence(raw.get("cards"), "cards"))
    )
    return LabelRegistry(
        path=resolved,
        schema_version=_string(raw.get("schema_version"), "schema_version"),
        registry_version=_string(raw.get("registry_version"), "registry_version"),
        cards=cards,
    )


def _validate_fact_type(definition: FeatureDefinition, fact: FeatureFact) -> None:
    value = fact.value
    if value is None:
        return
    expected = definition.output_type
    valid = (
        (expected is FeatureOutputType.BOOLEAN and type(value) is bool)
        or (expected is FeatureOutputType.INTEGER and type(value) is int)
        or (expected is FeatureOutputType.DECIMAL and isinstance(value, Decimal))
        or (
            expected is FeatureOutputType.CATEGORY
            and isinstance(value, str)
            and bool(value.strip())
        )
    )
    if not valid:
        raise LabelEvaluationError(
            "feature_type_mismatch",
            f"feature {fact.feature_id}@{fact.feature_version} does not match {expected.value}",
        )


def _validate_card_dependencies(card: LabelCard, registry: FeatureRegistry) -> None:
    declared_sources = {(source.dataset, source.column) for source in card.source_fields}
    required_sources: set[tuple[str, str]] = set()
    for requirement in card.feature_requirements:
        try:
            definition = registry.get(requirement.feature_id, requirement.version)
        except FeatureRegistryError as exc:
            raise LabelEvaluationError("unknown_feature_requirement", str(exc)) from exc
        required_sources.update((source.dataset, source.column) for source in definition.inputs)
        if (
            card.lifecycle is LabelLifecycle.ACTIVE
            and definition.lifecycle is not FeatureLifecycle.ACTIVE
        ):
            feature_ref = f"{requirement.feature_id}@{requirement.version}"
            raise LabelEvaluationError(
                "inactive_feature_requirement",
                f"active label requires active feature {feature_ref}",
            )
        if card.lifecycle is LabelLifecycle.SHADOW and definition.lifecycle in {
            FeatureLifecycle.DRAFT,
            FeatureLifecycle.DEPRECATED,
        }:
            feature_ref = f"{requirement.feature_id}@{requirement.version}"
            raise LabelEvaluationError(
                "inactive_feature_requirement",
                f"shadow label cannot use {definition.lifecycle.value} feature {feature_ref}",
            )
    if declared_sources != required_sources:
        raise LabelEvaluationError(
            "source_contract_mismatch", "label source_fields do not equal feature source fields"
        )


def evaluate_label(
    card: LabelCard,
    feature_registry: FeatureRegistry,
    context: LabelEvaluationContext,
    evaluator: LabelEvaluator,
) -> LabelResult:
    """Execute one evaluator inside the exact card, feature, snapshot, and evidence boundary."""

    _validate_card_dependencies(card, feature_registry)
    required_keys = {requirement.key for requirement in card.feature_requirements}
    fact_keys = {fact.key for fact in context.facts}
    if fact_keys != required_keys:
        raise LabelEvaluationError(
            "feature_input_contract_mismatch",
            f"required={sorted(required_keys)}, supplied={sorted(fact_keys)}",
        )

    facts: list[FeatureFact] = []
    for requirement in card.feature_requirements:
        fact = context.fact(requirement)
        definition = feature_registry.get(requirement.feature_id, requirement.version)
        if fact.lineage.definition_digest != definition.digest:
            raise LabelEvaluationError(
                "feature_definition_mismatch",
                f"feature fact does not use registered definition {requirement.feature_id}",
            )
        if fact.lineage.history_sessions < card.minimum_history_sessions:
            raise LabelEvaluationError(
                "insufficient_label_history",
                f"feature {requirement.feature_id} does not meet label minimum history",
            )
        _validate_fact_type(definition, fact)
        facts.append(fact)

    if not evaluator.evaluator_id.strip() or not _SEMANTIC_VERSION.fullmatch(
        evaluator.evaluator_version
    ):
        raise LabelEvaluationError("invalid_evaluator", "evaluator identity or version is invalid")
    decision = evaluator.evaluate(card, context)
    if (
        any(fact.value is None for fact in facts)
        and decision.status is not LabelResultStatus.UNKNOWN
    ):
        raise LabelEvaluationError(
            "unavailable_feature_applied", "unavailable feature facts require an unknown result"
        )
    if decision.status is LabelResultStatus.APPLIED and decision.value not in card.output_values:
        raise LabelEvaluationError("undeclared_label_value", f"undeclared output: {decision.value}")
    if decision.reason_code not in card.reason_codes:
        raise LabelEvaluationError(
            "undeclared_reason_code", f"undeclared reason: {decision.reason_code}"
        )
    evidence_names = {item.name for item in decision.evidence}
    if evidence_names != set(card.evidence_fields):
        raise LabelEvaluationError(
            "evidence_contract_mismatch",
            f"required={sorted(card.evidence_fields)}, supplied={sorted(evidence_names)}",
        )

    return LabelResult(
        label_id=card.label_id,
        label_version=card.version,
        card_digest=card.digest,
        dimension=card.dimension,
        security_code=context.security_code,
        snapshot_id=context.snapshot_id,
        knowledge_cutoff=context.knowledge_cutoff,
        status=decision.status,
        value=decision.value,
        reason_code=decision.reason_code,
        evidence=decision.evidence,
        feature_evidence=tuple(
            FeatureEvidenceRef(
                feature_id=fact.feature_id,
                feature_version=fact.feature_version,
                lineage_digest=fact.lineage.digest,
            )
            for fact in facts
        ),
        evaluator_id=evaluator.evaluator_id,
        evaluator_version=evaluator.evaluator_version,
    )

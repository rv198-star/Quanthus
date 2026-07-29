"""Versioned feature definitions and point-in-time lineage contracts."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, TypeVar

from quanthus.data_foundation import PublishedSnapshot

_FEATURE_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,127}$")
_SEMANTIC_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_REGISTRY_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_EnumType = TypeVar("_EnumType", bound=Enum)


class FeatureRegistryError(ValueError):
    """Raised when a feature registry violates its versioned contract."""


class FeatureLineageError(ValueError):
    """Raised when a feature cannot bind to complete point-in-time evidence."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}")


class FeatureLifecycle(str, Enum):
    DRAFT = "draft"
    SHADOW = "shadow"
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class FeatureNullPolicy(str, Enum):
    EXPLICIT_UNKNOWN = "explicit_unknown"
    FAIL_CLOSED = "fail_closed"


class FeatureCutoffRule(str, Enum):
    SNAPSHOT_OR_EARLIER = "snapshot_or_earlier"
    COMPLETE_T_CLOSE_CROSS_SECTION = "complete_t_close_cross_section"


class FeatureOutputType(str, Enum):
    BOOLEAN = "boolean"
    CATEGORY = "category"
    DECIMAL = "decimal"
    INTEGER = "integer"


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
        raise FeatureRegistryError(f"{path} must be an object")
    return value


def _sequence(value: Any, path: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise FeatureRegistryError(f"{path} must be an array")
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FeatureRegistryError(f"{path} must be a non-empty string")
    return value


def _positive_int(value: Any, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise FeatureRegistryError(f"{path} must be a positive integer")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    missing = sorted(expected.difference(value))
    unexpected = sorted(set(value).difference(expected))
    if missing or unexpected:
        raise FeatureRegistryError(
            f"{path} keys do not match contract; missing={missing}, unexpected={unexpected}"
        )


def _enum(value: Any, enum_type: type[_EnumType], path: str) -> _EnumType:
    raw = _string(value, path)
    try:
        return enum_type(raw)
    except ValueError as exc:
        raise FeatureRegistryError(f"{path} is unsupported: {raw}") from exc


@dataclass(frozen=True)
class FeatureSource:
    dataset: str
    column: str

    def __post_init__(self) -> None:
        if not self.dataset.strip() or not self.column.strip():
            raise FeatureRegistryError("feature source dataset and column must not be empty")

    def to_dict(self) -> dict[str, str]:
        return {"dataset": self.dataset, "column": self.column}


@dataclass(frozen=True)
class FeatureDefinition:
    feature_id: str
    version: str
    formula: str
    inputs: tuple[FeatureSource, ...]
    unit: str
    null_policy: FeatureNullPolicy
    lookback_sessions: int
    minimum_history_sessions: int
    cutoff_rule: FeatureCutoffRule
    output_type: FeatureOutputType
    owner: str
    lifecycle: FeatureLifecycle

    def __post_init__(self) -> None:
        if not _FEATURE_ID.fullmatch(self.feature_id):
            raise FeatureRegistryError(f"invalid feature_id: {self.feature_id}")
        if not _SEMANTIC_VERSION.fullmatch(self.version):
            raise FeatureRegistryError(
                f"feature {self.feature_id} version must use MAJOR.MINOR.PATCH"
            )
        for field_name, value in (
            ("formula", self.formula),
            ("unit", self.unit),
            ("owner", self.owner),
        ):
            if not value.strip():
                raise FeatureRegistryError(
                    f"feature {self.feature_id} {field_name} must not be empty"
                )
        if not self.inputs:
            raise FeatureRegistryError(f"feature {self.feature_id} inputs must not be empty")
        source_keys = [(source.dataset, source.column) for source in self.inputs]
        if len(source_keys) != len(set(source_keys)):
            raise FeatureRegistryError(f"feature {self.feature_id} inputs must be unique")
        session_counts = (self.lookback_sessions, self.minimum_history_sessions)
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value <= 0
            for value in session_counts
        ):
            raise FeatureRegistryError(
                f"feature {self.feature_id} lookback and minimum history must be positive"
            )
        if self.minimum_history_sessions > self.lookback_sessions:
            raise FeatureRegistryError(
                f"feature {self.feature_id} minimum history cannot exceed lookback"
            )

    @property
    def key(self) -> tuple[str, str]:
        return (self.feature_id, self.version)

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_id": self.feature_id,
            "version": self.version,
            "formula": self.formula,
            "inputs": [
                source.to_dict()
                for source in sorted(self.inputs, key=lambda item: (item.dataset, item.column))
            ],
            "unit": self.unit,
            "null_policy": self.null_policy.value,
            "lookback_sessions": self.lookback_sessions,
            "minimum_history_sessions": self.minimum_history_sessions,
            "cutoff_rule": self.cutoff_rule.value,
            "output_type": self.output_type.value,
            "owner": self.owner,
            "lifecycle": self.lifecycle.value,
        }


@dataclass(frozen=True)
class FeatureRegistry:
    path: Path
    schema_version: str
    registry_version: str
    definitions: tuple[FeatureDefinition, ...]

    def __post_init__(self) -> None:
        if self.schema_version != "1.0.0":
            raise FeatureRegistryError("feature registry schema_version must be 1.0.0")
        if not _REGISTRY_VERSION.fullmatch(self.registry_version):
            raise FeatureRegistryError("feature registry_version is invalid")
        if not self.definitions:
            raise FeatureRegistryError("feature registry definitions must not be empty")
        keys = [definition.key for definition in self.definitions]
        if len(keys) != len(set(keys)):
            raise FeatureRegistryError("feature registry contains duplicate feature/version keys")
        active_ids = [
            definition.feature_id
            for definition in self.definitions
            if definition.lifecycle is FeatureLifecycle.ACTIVE
        ]
        if len(active_ids) != len(set(active_ids)):
            raise FeatureRegistryError("feature registry permits at most one active version per id")

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "registry_version": self.registry_version,
            "definitions": [
                definition.to_dict()
                for definition in sorted(self.definitions, key=lambda item: item.key)
            ],
        }

    def get(self, feature_id: str, version: str) -> FeatureDefinition:
        matches = [
            definition
            for definition in self.definitions
            if definition.feature_id == feature_id and definition.version == version
        ]
        if len(matches) != 1:
            raise FeatureRegistryError(f"unknown feature/version: {feature_id}@{version}")
        return matches[0]

    def active(self, feature_id: str) -> FeatureDefinition:
        matches = [
            definition
            for definition in self.definitions
            if definition.feature_id == feature_id
            and definition.lifecycle is FeatureLifecycle.ACTIVE
        ]
        if len(matches) != 1:
            raise FeatureRegistryError(
                f"feature does not have exactly one active version: {feature_id}"
            )
        return matches[0]


@dataclass(frozen=True)
class FeatureSourceEvidence:
    dataset: str
    column: str
    dataset_version: str
    manifest_id: str
    knowledge_cutoff: datetime

    def __post_init__(self) -> None:
        if any(
            not value.strip()
            for value in (self.dataset, self.column, self.dataset_version, self.manifest_id)
        ):
            raise FeatureLineageError("invalid_source_evidence", "source fields must not be empty")
        if self.knowledge_cutoff.tzinfo is None or self.knowledge_cutoff.utcoffset() is None:
            raise FeatureLineageError(
                "invalid_source_evidence", "source knowledge_cutoff must be timezone-aware"
            )

    def to_dict(self) -> dict[str, str]:
        return {
            "dataset": self.dataset,
            "column": self.column,
            "dataset_version": self.dataset_version,
            "manifest_id": self.manifest_id,
            "knowledge_cutoff": self.knowledge_cutoff.isoformat(),
        }


@dataclass(frozen=True)
class FeatureLineage:
    feature_id: str
    feature_version: str
    definition_digest: str
    snapshot_id: str
    market_date: date
    knowledge_cutoff: datetime
    source_revision: str
    universe_policy_version: str
    sources: tuple[FeatureSourceEvidence, ...]
    history_sessions: int
    cross_section_complete: bool | None
    cutoff_rule: FeatureCutoffRule

    def __post_init__(self) -> None:
        if any(
            not value.strip()
            for value in (
                self.feature_id,
                self.feature_version,
                self.definition_digest,
                self.snapshot_id,
                self.source_revision,
                self.universe_policy_version,
            )
        ):
            raise FeatureLineageError("invalid_lineage", "lineage identity must not be empty")
        if not _FEATURE_ID.fullmatch(self.feature_id) or not _SEMANTIC_VERSION.fullmatch(
            self.feature_version
        ):
            raise FeatureLineageError("invalid_lineage", "feature identity is invalid")
        if not _SHA256.fullmatch(self.definition_digest):
            raise FeatureLineageError("invalid_lineage", "definition_digest must use sha256")
        if self.knowledge_cutoff.tzinfo is None or self.knowledge_cutoff.utcoffset() is None:
            raise FeatureLineageError(
                "invalid_lineage", "lineage knowledge_cutoff must be timezone-aware"
            )
        if self.knowledge_cutoff.date() < self.market_date:
            raise FeatureLineageError(
                "invalid_lineage", "lineage knowledge_cutoff cannot precede market_date"
            )
        if not self.sources:
            raise FeatureLineageError("invalid_lineage", "lineage sources must not be empty")
        source_keys = [(source.dataset, source.column) for source in self.sources]
        if len(source_keys) != len(set(source_keys)):
            raise FeatureLineageError("invalid_lineage", "lineage sources must be unique")
        if any(source.knowledge_cutoff > self.knowledge_cutoff for source in self.sources):
            raise FeatureLineageError(
                "future_source_evidence", "source evidence exceeds lineage knowledge_cutoff"
            )
        if (
            not isinstance(self.history_sessions, int)
            or isinstance(self.history_sessions, bool)
            or self.history_sessions <= 0
        ):
            raise FeatureLineageError(
                "invalid_lineage", "lineage history_sessions must be positive"
            )
        if self.cross_section_complete is not None and not isinstance(
            self.cross_section_complete, bool
        ):
            raise FeatureLineageError(
                "invalid_lineage", "cross_section_complete must be boolean or null"
            )
        if (
            self.cutoff_rule is FeatureCutoffRule.COMPLETE_T_CLOSE_CROSS_SECTION
            and self.cross_section_complete is not True
        ):
            raise FeatureLineageError(
                "incomplete_cross_section", "complete T-close cross-sectional evidence is required"
            )

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_id": self.feature_id,
            "feature_version": self.feature_version,
            "definition_digest": self.definition_digest,
            "snapshot_id": self.snapshot_id,
            "market_date": self.market_date.isoformat(),
            "knowledge_cutoff": self.knowledge_cutoff.isoformat(),
            "source_revision": self.source_revision,
            "universe_policy_version": self.universe_policy_version,
            "sources": [
                source.to_dict()
                for source in sorted(self.sources, key=lambda item: (item.dataset, item.column))
            ],
            "history_sessions": self.history_sessions,
            "cross_section_complete": self.cross_section_complete,
            "cutoff_rule": self.cutoff_rule.value,
        }


def _source(value: Any, path: str) -> FeatureSource:
    raw = _mapping(value, path)
    _exact_keys(raw, {"dataset", "column"}, path)
    return FeatureSource(
        dataset=_string(raw.get("dataset"), f"{path}.dataset"),
        column=_string(raw.get("column"), f"{path}.column"),
    )


def _definition(value: Any, path: str) -> FeatureDefinition:
    raw = _mapping(value, path)
    expected = {
        "feature_id",
        "version",
        "formula",
        "inputs",
        "unit",
        "null_policy",
        "lookback_sessions",
        "minimum_history_sessions",
        "cutoff_rule",
        "output_type",
        "owner",
        "lifecycle",
    }
    _exact_keys(raw, expected, path)
    inputs = tuple(
        _source(item, f"{path}.inputs[{index}]")
        for index, item in enumerate(_sequence(raw.get("inputs"), f"{path}.inputs"))
    )
    return FeatureDefinition(
        feature_id=_string(raw.get("feature_id"), f"{path}.feature_id"),
        version=_string(raw.get("version"), f"{path}.version"),
        formula=_string(raw.get("formula"), f"{path}.formula"),
        inputs=inputs,
        unit=_string(raw.get("unit"), f"{path}.unit"),
        null_policy=_enum(raw.get("null_policy"), FeatureNullPolicy, f"{path}.null_policy"),
        lookback_sessions=_positive_int(raw.get("lookback_sessions"), f"{path}.lookback_sessions"),
        minimum_history_sessions=_positive_int(
            raw.get("minimum_history_sessions"), f"{path}.minimum_history_sessions"
        ),
        cutoff_rule=_enum(raw.get("cutoff_rule"), FeatureCutoffRule, f"{path}.cutoff_rule"),
        output_type=_enum(raw.get("output_type"), FeatureOutputType, f"{path}.output_type"),
        owner=_string(raw.get("owner"), f"{path}.owner"),
        lifecycle=_enum(raw.get("lifecycle"), FeatureLifecycle, f"{path}.lifecycle"),
    )


def load_feature_registry(path: Path) -> FeatureRegistry:
    """Load a strict registry without supplying defaults or accepting unknown fields."""

    resolved = path.resolve()
    try:
        raw_value = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FeatureRegistryError(f"feature registry does not exist: {resolved}") from exc
    except json.JSONDecodeError as exc:
        raise FeatureRegistryError(f"invalid feature registry JSON: {exc}") from exc
    raw = _mapping(raw_value, "feature_registry")
    _exact_keys(raw, {"schema_version", "registry_version", "definitions"}, "feature_registry")
    definitions = tuple(
        _definition(item, f"definitions[{index}]")
        for index, item in enumerate(_sequence(raw.get("definitions"), "definitions"))
    )
    return FeatureRegistry(
        path=resolved,
        schema_version=_string(raw.get("schema_version"), "schema_version"),
        registry_version=_string(raw.get("registry_version"), "registry_version"),
        definitions=definitions,
    )


def build_feature_lineage(
    definition: FeatureDefinition,
    snapshot: PublishedSnapshot,
    *,
    history_sessions: int,
    cross_section_complete: bool | None = None,
) -> FeatureLineage:
    """Bind a definition to exact snapshot evidence or fail with a stable reason code."""

    if not isinstance(history_sessions, int) or isinstance(history_sessions, bool):
        raise FeatureLineageError("invalid_history", "history_sessions must be an integer")
    if history_sessions < definition.minimum_history_sessions:
        required = definition.minimum_history_sessions
        detail = f"{history_sessions} sessions available; {required} required"
        raise FeatureLineageError(
            "insufficient_history",
            detail,
        )
    if (
        definition.cutoff_rule is FeatureCutoffRule.COMPLETE_T_CLOSE_CROSS_SECTION
        and cross_section_complete is not True
    ):
        raise FeatureLineageError(
            "incomplete_cross_section",
            "complete T-close cross-sectional evidence is required",
        )

    manifests = {manifest.dataset: manifest for manifest in snapshot.datasets}
    missing = sorted({source.dataset for source in definition.inputs}.difference(manifests))
    if missing:
        raise FeatureLineageError(
            "missing_source_dataset", f"snapshot does not contain datasets: {', '.join(missing)}"
        )

    evidence: list[FeatureSourceEvidence] = []
    for source in definition.inputs:
        manifest = manifests[source.dataset]
        if manifest.knowledge_cutoff > snapshot.reference.knowledge_cutoff:
            raise FeatureLineageError(
                "future_source_evidence",
                f"dataset {source.dataset} exceeds snapshot knowledge_cutoff",
            )
        evidence.append(
            FeatureSourceEvidence(
                dataset=source.dataset,
                column=source.column,
                dataset_version=manifest.version,
                manifest_id=manifest.manifest_id,
                knowledge_cutoff=manifest.knowledge_cutoff,
            )
        )

    reference = snapshot.reference
    return FeatureLineage(
        feature_id=definition.feature_id,
        feature_version=definition.version,
        definition_digest=definition.digest,
        snapshot_id=reference.snapshot_id,
        market_date=reference.market_date,
        knowledge_cutoff=reference.knowledge_cutoff,
        source_revision=reference.source_revision,
        universe_policy_version=snapshot.universe_policy_version,
        sources=tuple(evidence),
        history_sessions=history_sessions,
        cross_section_complete=cross_section_complete,
        cutoff_rule=definition.cutoff_rule,
    )

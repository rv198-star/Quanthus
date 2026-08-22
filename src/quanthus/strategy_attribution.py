"""Strategy-owned attribution adapter contracts and deterministic paired evaluation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import Enum
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

ATTRIBUTION_SCHEMA_VERSION = "0.1.0"
WORKSPACE_SCHEMA = "quanthus.strategy-attribution-workspace.v1"
SEMANTIC_ARTIFACT_SCHEMA = "quanthus.strategy-attribution-artifact.v1"
VERIFICATION_RECEIPT_SCHEMA = "quanthus.strategy-attribution-verification-receipt.v1"
METHOD_REGISTRY_VERSION = "quanthus.strategy-attribution-method-registry.v1"
VERIFIER_ID = "quanthus.strategy-attribution-evidence-verifier"
VERIFIER_VERSION = "1.0.0"
SYNTHETIC_ARTIFACT_PRODUCER_ID = "quanthus.synthetic-attribution-executor"
SYNTHETIC_ARTIFACT_PRODUCER_VERSION = "1.0.0"
FORMAL_ARTIFACT_PRODUCER_ID = "quanthus.formal-strategy-attribution-executor"
FORMAL_ARTIFACT_PRODUCER_VERSION = "1.0.0"
MAX_DECIMAL_EXPONENT = 10_000


class AttributionError(ValueError):
    """Raised when an attribution artifact is structurally invalid."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}")


class AttributionCapability(str, Enum):
    BASELINE_REPLAY = "baseline_replay"
    COMPONENT_MAP = "component_map"
    LEGAL_INTERVENTION = "legal_intervention"
    DECISION_TRACE = "decision_trace"
    OPPORTUNITY_TRACE = "opportunity_trace"
    CONDITION_MAP = "condition_map"
    TRAINING_REPLAY = "training_replay"


class EvidenceType(str, Enum):
    ACCOUNTING = "accounting"
    ASSOCIATION = "association"
    SENSITIVITY = "sensitivity"
    COUNTERFACTUAL_REPLAY = "counterfactual_replay"


class ValidationFlag(str, Enum):
    BASELINE_REPRODUCED = "baseline_reproduced"
    PAIRED_COMPARABLE = "paired_comparable"
    INTERACTION_CHECKED = "interaction_checked"
    CONDITION_CHECKED = "condition_checked"
    STABILITY_CHECKED = "stability_checked"
    INDEPENDENT_EVALUATION_COMPLETED = "independent_evaluation_completed"


class MetricPreference(str, Enum):
    HIGHER = "higher"
    LOWER = "lower"
    TARGET_RANGE = "target_range"
    UNORIENTED = "unoriented"


class MetricDisplay(str, Enum):
    PERCENT = "percent"
    CURRENCY = "currency"
    NUMBER = "number"
    COUNT = "count"
    DAYS = "days"


class EffectClassification(str, Enum):
    BENEFICIAL = "beneficial"
    HARMFUL = "harmful"
    NEGLIGIBLE = "negligible"
    UNORIENTED = "unoriented"


class ClaimClassification(str, Enum):
    BENEFICIAL = "beneficial"
    HARMFUL = "harmful"
    NEGLIGIBLE = "negligible"
    TRADEOFF = "tradeoff"
    INCONCLUSIVE = "inconclusive"


class ClaimStatus(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"


_DECISION_ACTION_KEYS = frozenset(
    {
        *(f"{ClaimStatus.VALID.value}:{item.value}" for item in ClaimClassification),
        f"{ClaimStatus.INVALID.value}:{ClaimClassification.INCONCLUSIVE.value}",
        f"{ClaimStatus.UNAVAILABLE.value}:{ClaimClassification.INCONCLUSIVE.value}",
    }
)


class ExperimentRole(str, Enum):
    BASELINE = "baseline"
    INTERVENTION = "intervention"
    JOINT_INTERVENTION = "joint_intervention"


class SampleRole(str, Enum):
    DEVELOPMENT = "development"
    REPLICATION = "replication"
    INDEPENDENT_EVALUATION = "independent_evaluation"


class AnalysisRole(str, Enum):
    CONFIRMATORY = "confirmatory"
    EXPLORATORY = "exploratory"


class AnalysisCheckKind(str, Enum):
    UNCERTAINTY = "uncertainty"
    STABILITY = "stability"


class AnalysisCheckScope(str, Enum):
    OVERALL_METRIC_VECTOR = "overall_metric_vector"


class AttributionMethodKind(str, Enum):
    EFFECT = "effect"
    CONDITION = "condition"
    INTERACTION = "interaction"
    CLASSIFICATION = "classification"
    MULTIPLE_COMPARISON = "multiple_comparison"
    UNCERTAINTY = "uncertainty"
    STABILITY = "stability"


class ArtifactKind(str, Enum):
    EXPERIMENT_RESULT = "experiment_result"
    NATIVE_TRACE = "native_trace"
    CONDITION_ANALYSIS_RESULT = "condition_analysis_result"
    ANALYSIS_CHECK_RESULT = "analysis_check_result"


@dataclass(frozen=True)
class AttributionMethodRegistration:
    method_id: str
    method_version: str
    kinds: tuple[AttributionMethodKind, ...]
    implementation_id: str

    def __post_init__(self) -> None:
        for field_name in ("method_id", "method_version", "implementation_id"):
            _require_text(getattr(self, field_name), field_name)
        kinds = tuple(sorted(set(self.kinds), key=lambda item: item.value))
        if not kinds or any(not isinstance(item, AttributionMethodKind) for item in kinds):
            raise AttributionError("invalid_method_kind", "method registration kinds are invalid")
        object.__setattr__(self, "kinds", kinds)

    def supports(self, kind: AttributionMethodKind) -> bool:
        return kind in self.kinds

    def to_dict(self) -> dict[str, Any]:
        return {
            "method_id": self.method_id,
            "method_version": self.method_version,
            "kinds": [item.value for item in self.kinds],
            "implementation_id": self.implementation_id,
        }


PAIRED_REPLAY_EFFECT_METHOD = "paired_full_replay_effect_v1"
CONDITION_EFFECT_METHOD = "predeclared_condition_difference_v1"
INTERACTION_EFFECT_METHOD = "pairwise_interaction_contrast_v1"
PRIMARY_CLASSIFICATION_METHOD = "primary_metric_dominance_with_materiality_v1"
NO_CROSS_MODULE_RANKING_METHOD = "no_cross_module_ranking:synthetic_fixture_v1"
NOT_REQUIRED_CHECK_METHOD = "not_required:synthetic_contract_fixture_v1"
FORMAL_NO_RANKING_METHOD = "no_cross_expert_ranking:formal_strategy_v1"
PAIRED_BLOCK_BOOTSTRAP_METHOD = "uncertainty.paired-nonoverlap-22-session-block-bootstrap"


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AttributionError("invalid_identity", f"{field_name} must not be empty")
    return value


def _require_bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise AttributionError("invalid_boolean", f"{field_name} must be boolean")
    return value


def _require_digest(value: object, field_name: str) -> str:
    text = _require_text(value, field_name)
    prefix = "sha256:"
    encoded = text[len(prefix) :] if text.startswith(prefix) else ""
    if len(encoded) != 64 or any(item not in "0123456789abcdef" for item in encoded):
        raise AttributionError("invalid_digest", f"{field_name} must be a lowercase sha256 digest")
    return text


def _require_decimal(value: object, field_name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise AttributionError("invalid_decimal", f"{field_name} must be a finite Decimal")
    exponent = value.as_tuple().exponent
    if abs(exponent) > MAX_DECIMAL_EXPONENT or abs(value.adjusted()) > MAX_DECIMAL_EXPONENT:
        raise AttributionError(
            "decimal_out_of_range",
            f"{field_name} exceeds the supported decimal exponent range",
        )
    return value


def _decimal_context(*values: Decimal, extra_places: int = 0):
    max_integer_places = 1
    max_fractional_places = 0
    for value in values:
        sign, digits, exponent = value.as_tuple()
        del sign
        integer_places = max(1, len(digits) + exponent)
        fractional_places = max(0, -exponent)
        max_integer_places = max(max_integer_places, integer_places)
        max_fractional_places = max(max_fractional_places, fractional_places)
    precision = max(
        34,
        max_integer_places + max_fractional_places + extra_places + 8,
    )
    return localcontext(Context(prec=precision, rounding=ROUND_HALF_EVEN))


def _decimal_text(value: Decimal) -> str:
    if value.is_zero():
        return "0"
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _canonical_json_value(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, Enum):
        return str(value.value)
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        default=_canonical_json_value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(canonical_bytes(value)).hexdigest()}"


def analysis_check_target_digest(
    *,
    metric_ids: Sequence[str],
    baseline_experiment_digest: str,
    intervention_experiment_digest: str,
) -> str:
    normalized_metrics = tuple(sorted(_unique_text(metric_ids, "analysis_metric_ids")))
    _require_digest(baseline_experiment_digest, "baseline_experiment_digest")
    _require_digest(intervention_experiment_digest, "intervention_experiment_digest")
    return digest(
        {
            "scope": AnalysisCheckScope.OVERALL_METRIC_VECTOR.value,
            "metric_ids": list(normalized_metrics),
            "baseline_experiment_digest": baseline_experiment_digest,
            "intervention_experiment_digest": intervention_experiment_digest,
        }
    )


def _unique_text(values: Sequence[str], field_name: str) -> tuple[str, ...]:
    normalized = tuple(values)
    if any(not isinstance(value, str) or not value.strip() for value in normalized):
        raise AttributionError("invalid_identity", f"{field_name} must contain non-empty strings")
    if len(normalized) != len(set(normalized)):
        raise AttributionError("duplicate_identity", f"{field_name} must not contain duplicates")
    return normalized


def _text_mapping(values: Mapping[str, str], field_name: str) -> Mapping[str, str]:
    normalized = dict(values)
    for key, value in normalized.items():
        _require_text(key, f"{field_name}.key")
        _require_text(value, f"{field_name}.{key}")
    return MappingProxyType(normalized)


_METHOD_REGISTRATIONS = (
    AttributionMethodRegistration(
        method_id=PAIRED_REPLAY_EFFECT_METHOD,
        method_version="1.0.0",
        kinds=(AttributionMethodKind.EFFECT,),
        implementation_id="quanthus.strategy_attribution._metric_effects",
    ),
    AttributionMethodRegistration(
        method_id=CONDITION_EFFECT_METHOD,
        method_version="1.0.0",
        kinds=(AttributionMethodKind.CONDITION,),
        implementation_id="quanthus.strategy_attribution._validate_condition_evidence",
    ),
    AttributionMethodRegistration(
        method_id=INTERACTION_EFFECT_METHOD,
        method_version="1.0.0",
        kinds=(AttributionMethodKind.INTERACTION,),
        implementation_id="quanthus.strategy_attribution._validate_interaction_evidence",
    ),
    AttributionMethodRegistration(
        method_id=PRIMARY_CLASSIFICATION_METHOD,
        method_version="1.0.0",
        kinds=(AttributionMethodKind.CLASSIFICATION,),
        implementation_id="quanthus.strategy_attribution._claim_classification",
    ),
    AttributionMethodRegistration(
        method_id=NO_CROSS_MODULE_RANKING_METHOD,
        method_version="1.0.0",
        kinds=(AttributionMethodKind.MULTIPLE_COMPARISON,),
        implementation_id="quanthus.strategy_attribution.no_cross_module_ranking",
    ),
    AttributionMethodRegistration(
        method_id=NOT_REQUIRED_CHECK_METHOD,
        method_version="1.0.0",
        kinds=(AttributionMethodKind.UNCERTAINTY, AttributionMethodKind.STABILITY),
        implementation_id="quanthus.strategy_attribution.not_required_check",
    ),
    AttributionMethodRegistration(
        method_id=FORMAL_NO_RANKING_METHOD,
        method_version="1.0.0",
        kinds=(AttributionMethodKind.MULTIPLE_COMPARISON,),
        implementation_id="quanthus.formal_attribution.no_cross_expert_ranking",
    ),
    AttributionMethodRegistration(
        method_id=PAIRED_BLOCK_BOOTSTRAP_METHOD,
        method_version="1.0.0",
        kinds=(AttributionMethodKind.UNCERTAINTY,),
        implementation_id="quanthus.formal_attribution.paired_block_bootstrap",
    ),
)
_METHOD_REGISTRY = MappingProxyType({item.method_id: item for item in _METHOD_REGISTRATIONS})
_REGISTERED_ARTIFACT_PRODUCERS = frozenset(
    {
        (SYNTHETIC_ARTIFACT_PRODUCER_ID, SYNTHETIC_ARTIFACT_PRODUCER_VERSION),
        (FORMAL_ARTIFACT_PRODUCER_ID, FORMAL_ARTIFACT_PRODUCER_VERSION),
    }
)


def registered_attribution_methods() -> tuple[AttributionMethodRegistration, ...]:
    """Return the immutable methods the current kernel can actually dispatch or enforce."""

    return _METHOD_REGISTRATIONS


def _json_object_copy(value: Mapping[str, Any], field_name: str) -> Mapping[str, Any]:
    try:
        normalized = json.loads(canonical_bytes(dict(value)))
    except (TypeError, ValueError) as exc:
        raise AttributionError(
            "invalid_json_object", f"{field_name} is not canonical JSON"
        ) from exc
    if not isinstance(normalized, dict):
        raise AttributionError("invalid_json_object", f"{field_name} must be an object")
    return MappingProxyType(normalized)


@dataclass(frozen=True)
class SemanticArtifactEnvelope:
    artifact_id: str
    artifact_kind: ArtifactKind
    producer_id: str
    producer_version: str
    source_kind: str
    parent_digests: tuple[str, ...]
    payload: Mapping[str, Any]
    schema_version: str = SEMANTIC_ARTIFACT_SCHEMA

    def __post_init__(self) -> None:
        for field_name in (
            "artifact_id",
            "producer_id",
            "producer_version",
            "source_kind",
            "schema_version",
        ):
            _require_text(getattr(self, field_name), field_name)
        if self.schema_version != SEMANTIC_ARTIFACT_SCHEMA:
            raise AttributionError(
                "artifact_schema_mismatch", "semantic artifact schema is unsupported"
            )
        if not isinstance(self.artifact_kind, ArtifactKind):
            raise AttributionError("invalid_artifact_kind", "artifact kind is not registered")
        parents = tuple(sorted(_unique_text(self.parent_digests, "parent_digests")))
        for parent_digest in parents:
            _require_digest(parent_digest, "parent_digest")
        object.__setattr__(self, "parent_digests", parents)
        object.__setattr__(self, "payload", _json_object_copy(self.payload, "artifact_payload"))

    def _semantic_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "artifact_id": self.artifact_id,
            "artifact_kind": self.artifact_kind.value,
            "producer_id": self.producer_id,
            "producer_version": self.producer_version,
            "source_kind": self.source_kind,
            "parent_digests": list(self.parent_digests),
            "payload": dict(self.payload),
        }

    @property
    def digest(self) -> str:
        return digest(self._semantic_dict())

    def to_dict(self) -> dict[str, Any]:
        payload = self._semantic_dict()
        payload["digest"] = self.digest
        return payload

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())

    @classmethod
    def from_bytes(cls, payload: bytes) -> SemanticArtifactEnvelope:
        try:
            raw = json.loads(payload)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AttributionError("invalid_artifact_json", "artifact is not valid JSON") from exc
        if not isinstance(raw, dict):
            raise AttributionError("invalid_artifact_json", "artifact must be a JSON object")
        expected = {
            "schema_version",
            "artifact_id",
            "artifact_kind",
            "producer_id",
            "producer_version",
            "source_kind",
            "parent_digests",
            "payload",
            "digest",
        }
        if set(raw) != expected:
            raise AttributionError("artifact_field_mismatch", "artifact fields are incomplete")
        try:
            kind = ArtifactKind(raw["artifact_kind"])
        except (TypeError, ValueError) as exc:
            raise AttributionError(
                "invalid_artifact_kind", "artifact kind is not registered"
            ) from exc
        parents = raw["parent_digests"]
        body = raw["payload"]
        if not isinstance(parents, list) or not isinstance(body, dict):
            raise AttributionError(
                "invalid_artifact_json", "artifact parents or payload are invalid"
            )
        envelope = cls(
            schema_version=raw["schema_version"],
            artifact_id=raw["artifact_id"],
            artifact_kind=kind,
            producer_id=raw["producer_id"],
            producer_version=raw["producer_version"],
            source_kind=raw["source_kind"],
            parent_digests=tuple(parents),
            payload=body,
        )
        if raw["digest"] != envelope.digest:
            raise AttributionError("artifact_digest_mismatch", "artifact digest is invalid")
        return envelope


@runtime_checkable
class ArtifactResolver(Protocol):
    def resolve(self, artifact_digest: str) -> bytes: ...


class InMemoryArtifactResolver:
    """Content-addressed resolver used by the bounded synthetic runtime and tests."""

    def __init__(self, artifacts: Sequence[SemanticArtifactEnvelope] = ()) -> None:
        self._artifacts: dict[str, bytes] = {}
        for artifact in artifacts:
            self.register(artifact)

    def register(self, artifact: SemanticArtifactEnvelope) -> str:
        if not isinstance(artifact, SemanticArtifactEnvelope):
            raise AttributionError("invalid_artifact", "resolver accepts semantic artifacts only")
        payload = artifact.canonical_bytes()
        existing = self._artifacts.get(artifact.digest)
        if existing is not None and existing != payload:
            raise AttributionError(
                "artifact_identity_collision", "artifact digest has two payloads"
            )
        self._artifacts[artifact.digest] = payload
        return artifact.digest

    def resolve(self, artifact_digest: str) -> bytes:
        _require_digest(artifact_digest, "artifact_digest")
        try:
            return self._artifacts[artifact_digest]
        except KeyError as exc:
            raise KeyError(f"artifact is unresolved: {artifact_digest}") from exc

    def artifacts(self) -> tuple[SemanticArtifactEnvelope, ...]:
        return tuple(
            SemanticArtifactEnvelope.from_bytes(self._artifacts[item])
            for item in sorted(self._artifacts)
        )


@dataclass(frozen=True)
class ArtifactVerificationReceipt:
    receipt_id: str
    artifact_digest: str
    artifact_kind: ArtifactKind
    subject_id: str
    producer_id: str
    producer_version: str
    parent_digests: tuple[str, ...]
    verified_facts: Mapping[str, bool]
    passed: bool
    reason_codes: tuple[str, ...]
    schema_version: str = VERIFICATION_RECEIPT_SCHEMA
    verifier_id: str = VERIFIER_ID
    verifier_version: str = VERIFIER_VERSION

    def __post_init__(self) -> None:
        for field_name in (
            "receipt_id",
            "artifact_digest",
            "subject_id",
            "producer_id",
            "producer_version",
            "schema_version",
            "verifier_id",
            "verifier_version",
        ):
            _require_text(getattr(self, field_name), field_name)
        _require_digest(self.artifact_digest, "artifact_digest")
        if self.schema_version != VERIFICATION_RECEIPT_SCHEMA:
            raise AttributionError("receipt_schema_mismatch", "receipt schema is unsupported")
        if not isinstance(self.artifact_kind, ArtifactKind):
            raise AttributionError("invalid_artifact_kind", "receipt artifact kind is invalid")
        parents = tuple(sorted(_unique_text(self.parent_digests, "parent_digests")))
        for parent_digest in parents:
            _require_digest(parent_digest, "parent_digest")
        object.__setattr__(self, "parent_digests", parents)
        facts = dict(self.verified_facts)
        for key, value in facts.items():
            _require_text(key, "verified_fact")
            _require_bool(value, f"verified_fact:{key}")
        object.__setattr__(self, "verified_facts", MappingProxyType(dict(sorted(facts.items()))))
        _require_bool(self.passed, "passed")
        reasons = _unique_text(self.reason_codes, "reason_codes")
        if self.passed and reasons:
            raise AttributionError("invalid_receipt", "passing receipt cannot contain reasons")
        if not self.passed and not reasons:
            raise AttributionError("invalid_receipt", "failed receipt requires a reason")
        object.__setattr__(self, "reason_codes", tuple(sorted(reasons)))

    def _semantic_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "receipt_id": self.receipt_id,
            "verifier_id": self.verifier_id,
            "verifier_version": self.verifier_version,
            "artifact_digest": self.artifact_digest,
            "artifact_kind": self.artifact_kind.value,
            "subject_id": self.subject_id,
            "producer_id": self.producer_id,
            "producer_version": self.producer_version,
            "parent_digests": list(self.parent_digests),
            "verified_facts": dict(self.verified_facts),
            "passed": self.passed,
            "reason_codes": list(self.reason_codes),
        }

    @property
    def digest(self) -> str:
        return digest(self._semantic_dict())

    def to_dict(self) -> dict[str, Any]:
        payload = self._semantic_dict()
        payload["digest"] = self.digest
        return payload

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())

    @classmethod
    def from_bytes(cls, payload: bytes) -> ArtifactVerificationReceipt:
        try:
            raw = json.loads(payload)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AttributionError("invalid_receipt_json", "receipt is not valid JSON") from exc
        if not isinstance(raw, dict):
            raise AttributionError("invalid_receipt_json", "receipt must be a JSON object")
        expected = {
            "schema_version",
            "receipt_id",
            "verifier_id",
            "verifier_version",
            "artifact_digest",
            "artifact_kind",
            "subject_id",
            "producer_id",
            "producer_version",
            "parent_digests",
            "verified_facts",
            "passed",
            "reason_codes",
            "digest",
        }
        if set(raw) != expected:
            raise AttributionError("receipt_field_mismatch", "receipt fields are incomplete")
        try:
            kind = ArtifactKind(raw["artifact_kind"])
        except (TypeError, ValueError) as exc:
            raise AttributionError(
                "invalid_artifact_kind", "receipt artifact kind is invalid"
            ) from exc
        parents = raw["parent_digests"]
        facts = raw["verified_facts"]
        reasons = raw["reason_codes"]
        if (
            not isinstance(parents, list)
            or not isinstance(facts, dict)
            or not isinstance(reasons, list)
        ):
            raise AttributionError("invalid_receipt_json", "receipt collections are invalid")
        receipt = cls(
            schema_version=raw["schema_version"],
            receipt_id=raw["receipt_id"],
            verifier_id=raw["verifier_id"],
            verifier_version=raw["verifier_version"],
            artifact_digest=raw["artifact_digest"],
            artifact_kind=kind,
            subject_id=raw["subject_id"],
            producer_id=raw["producer_id"],
            producer_version=raw["producer_version"],
            parent_digests=tuple(parents),
            verified_facts=facts,
            passed=raw["passed"],
            reason_codes=tuple(reasons),
        )
        if (receipt.verifier_id, receipt.verifier_version) != (VERIFIER_ID, VERIFIER_VERSION):
            raise AttributionError("receipt_verifier_mismatch", "receipt verifier is unsupported")
        if receipt.receipt_id != _receipt_id(
            receipt.artifact_digest,
            receipt.artifact_kind,
            receipt.subject_id,
        ):
            raise AttributionError("receipt_identity_mismatch", "receipt identity is invalid")
        if raw["digest"] != receipt.digest:
            raise AttributionError("receipt_digest_mismatch", "receipt digest is invalid")
        return receipt


@dataclass(frozen=True)
class InterventionSpec:
    intervention_id: str
    intervention_version: str
    operation: str
    fallback_semantics: str

    def __post_init__(self) -> None:
        for field_name in (
            "intervention_id",
            "intervention_version",
            "operation",
            "fallback_semantics",
        ):
            _require_text(getattr(self, field_name), field_name)

    @property
    def digest(self) -> str:
        return digest(self._semantic_dict())

    def _semantic_dict(self) -> dict[str, Any]:
        return {
            "intervention_id": self.intervention_id,
            "intervention_version": self.intervention_version,
            "operation": self.operation,
            "fallback_semantics": self.fallback_semantics,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._semantic_dict()
        payload["digest"] = self.digest
        return payload


@dataclass(frozen=True)
class JointInterventionSpec:
    joint_spec_id: str
    joint_spec_version: str
    intervention_ids: tuple[str, ...]
    component_ids: tuple[str, ...]
    intervention_spec_digests: tuple[str, ...]
    compatibility_semantics: str
    conflict_rule: str

    def __post_init__(self) -> None:
        for field_name in (
            "joint_spec_id",
            "joint_spec_version",
            "compatibility_semantics",
            "conflict_rule",
        ):
            _require_text(getattr(self, field_name), field_name)
        object.__setattr__(
            self,
            "intervention_ids",
            _unique_text(self.intervention_ids, "joint_intervention_ids"),
        )
        object.__setattr__(
            self,
            "component_ids",
            _unique_text(self.component_ids, "joint_component_ids"),
        )
        intervention_spec_digests = tuple(self.intervention_spec_digests)
        for item in intervention_spec_digests:
            _require_digest(item, "joint_intervention_spec_digest")
        object.__setattr__(
            self,
            "intervention_spec_digests",
            intervention_spec_digests,
        )
        if len(self.intervention_ids) < 2:
            raise AttributionError(
                "invalid_joint_intervention",
                "a joint intervention requires at least two interventions",
            )
        if not (
            len(self.intervention_ids)
            == len(self.component_ids)
            == len(self.intervention_spec_digests)
        ):
            raise AttributionError(
                "invalid_joint_intervention",
                "joint intervention and component identities must align by position",
            )

    @property
    def digest(self) -> str:
        return digest(self._semantic_dict())

    def _semantic_dict(self) -> dict[str, Any]:
        return {
            "joint_spec_id": self.joint_spec_id,
            "joint_spec_version": self.joint_spec_version,
            "intervention_ids": list(self.intervention_ids),
            "component_ids": list(self.component_ids),
            "intervention_spec_digests": list(self.intervention_spec_digests),
            "compatibility_semantics": self.compatibility_semantics,
            "conflict_rule": self.conflict_rule,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._semantic_dict()
        payload["digest"] = self.digest
        return payload


@dataclass(frozen=True)
class ComponentDescriptor:
    component_id: str
    component_version: str
    label: str
    kind: str
    role: str
    description: str
    depends_on: tuple[str, ...] = ()
    legal_interventions: tuple[InterventionSpec, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.component_id, "component_id")
        _require_text(self.component_version, "component_version")
        _require_text(self.label, "label")
        _require_text(self.kind, "kind")
        _require_text(self.role, "role")
        _require_text(self.description, "description")
        object.__setattr__(self, "depends_on", _unique_text(self.depends_on, "depends_on"))
        if any(not isinstance(item, InterventionSpec) for item in self.legal_interventions):
            raise AttributionError(
                "invalid_intervention", "legal_interventions must contain InterventionSpec values"
            )
        intervention_ids = [item.intervention_id for item in self.legal_interventions]
        if len(intervention_ids) != len(set(intervention_ids)):
            raise AttributionError(
                "duplicate_intervention", "component intervention ids must be unique"
            )
        object.__setattr__(
            self,
            "legal_interventions",
            tuple(sorted(self.legal_interventions, key=lambda item: item.intervention_id)),
        )

    @property
    def digest(self) -> str:
        return digest(self._semantic_dict())

    def intervention(self, intervention_id: str) -> InterventionSpec | None:
        return next(
            (item for item in self.legal_interventions if item.intervention_id == intervention_id),
            None,
        )

    def _semantic_dict(self) -> dict[str, Any]:
        return {
            "component_id": self.component_id,
            "component_version": self.component_version,
            "label": self.label,
            "kind": self.kind,
            "role": self.role,
            "description": self.description,
            "depends_on": sorted(self.depends_on),
            "legal_interventions": [item.to_dict() for item in self.legal_interventions],
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._semantic_dict()
        payload["digest"] = self.digest
        return payload


@dataclass(frozen=True)
class AdapterCapabilityManifest:
    adapter_id: str
    adapter_version: str
    capability_manifest_version: str
    strategy_id: str
    strategy_version: str
    evidence_source_kind: str
    native_topology_digest: str
    capabilities: tuple[AttributionCapability, ...]
    components: tuple[ComponentDescriptor, ...]
    legal_joint_interventions: tuple[JointInterventionSpec, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "adapter_id",
            "adapter_version",
            "capability_manifest_version",
            "strategy_id",
            "strategy_version",
            "evidence_source_kind",
            "native_topology_digest",
        ):
            _require_text(getattr(self, field_name), field_name)
        _require_digest(self.native_topology_digest, "native_topology_digest")
        capabilities = tuple(self.capabilities)
        components = tuple(self.components)
        object.__setattr__(self, "capabilities", capabilities)
        object.__setattr__(self, "components", components)
        if any(not isinstance(item, AttributionCapability) for item in self.capabilities):
            raise AttributionError("invalid_capability", "capabilities must use the registry")
        if len(self.capabilities) != len(set(self.capabilities)):
            raise AttributionError("duplicate_capability", "capabilities must not repeat")
        component_ids = [item.component_id for item in self.components]
        if not component_ids:
            raise AttributionError("missing_component", "adapter must expose at least its root")
        if len(component_ids) != len(set(component_ids)):
            raise AttributionError("duplicate_component", "component ids must be unique")
        if AttributionCapability.COMPONENT_MAP not in self.capabilities and len(component_ids) != 1:
            raise AttributionError(
                "capability_component_mismatch",
                "an adapter without component_map may expose only its strategy root",
            )
        intervention_ids = [
            intervention.intervention_id
            for component in self.components
            for intervention in component.legal_interventions
        ]
        if len(intervention_ids) != len(set(intervention_ids)):
            raise AttributionError(
                "duplicate_intervention", "intervention ids must be unique across the adapter"
            )
        if AttributionCapability.LEGAL_INTERVENTION in self.capabilities and not intervention_ids:
            raise AttributionError(
                "capability_intervention_mismatch",
                "legal_intervention requires at least one declared intervention",
            )
        if AttributionCapability.LEGAL_INTERVENTION not in self.capabilities and intervention_ids:
            raise AttributionError(
                "capability_intervention_mismatch",
                "declared interventions require legal_intervention capability",
            )
        known = set(component_ids)
        missing = sorted(
            dependency
            for component in self.components
            for dependency in component.depends_on
            if dependency not in known
        )
        if missing:
            raise AttributionError(
                "unknown_component_dependency", f"component dependencies are unknown: {missing}"
            )
        if any(
            not isinstance(item, JointInterventionSpec) for item in self.legal_joint_interventions
        ):
            raise AttributionError(
                "invalid_joint_intervention",
                "legal_joint_interventions must contain JointInterventionSpec values",
            )
        joint_ids = [item.joint_spec_id for item in self.legal_joint_interventions]
        if len(joint_ids) != len(set(joint_ids)):
            raise AttributionError(
                "duplicate_joint_intervention", "joint intervention ids must be unique"
            )
        for joint in self.legal_joint_interventions:
            for component_id, intervention_id, intervention_spec_digest in zip(
                joint.component_ids,
                joint.intervention_ids,
                joint.intervention_spec_digests,
                strict=True,
            ):
                member = self.intervention(component_id, intervention_id)
                if member is None or member.digest != intervention_spec_digest:
                    raise AttributionError(
                        "undeclared_joint_member",
                        "joint intervention members must be legal adapter interventions",
                    )
        object.__setattr__(
            self,
            "legal_joint_interventions",
            tuple(
                sorted(
                    self.legal_joint_interventions,
                    key=lambda item: (item.joint_spec_id, item.digest),
                )
            ),
        )

    @property
    def component_ids(self) -> tuple[str, ...]:
        return tuple(sorted(item.component_id for item in self.components))

    @property
    def digest(self) -> str:
        return digest(self._semantic_dict())

    def has(self, capability: AttributionCapability) -> bool:
        return capability in self.capabilities

    def component(self, component_id: str) -> ComponentDescriptor | None:
        return next(
            (item for item in self.components if item.component_id == component_id),
            None,
        )

    def intervention(self, component_id: str, intervention_id: str) -> InterventionSpec | None:
        component = self.component(component_id)
        return component.intervention(intervention_id) if component is not None else None

    def joint_intervention(self, joint_spec_id: str) -> JointInterventionSpec | None:
        return next(
            (
                item
                for item in self.legal_joint_interventions
                if item.joint_spec_id == joint_spec_id
            ),
            None,
        )

    def joint_intervention_by_digest(self, joint_spec_digest: str) -> JointInterventionSpec | None:
        return next(
            (item for item in self.legal_joint_interventions if item.digest == joint_spec_digest),
            None,
        )

    def _semantic_dict(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "capability_manifest_version": self.capability_manifest_version,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "evidence_source_kind": self.evidence_source_kind,
            "native_topology_digest": self.native_topology_digest,
            "capabilities": sorted(item.value for item in self.capabilities),
            "components": [
                item.to_dict()
                for item in sorted(self.components, key=lambda item: item.component_id)
            ],
            "legal_joint_interventions": [
                item.to_dict() for item in self.legal_joint_interventions
            ],
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._semantic_dict()
        payload["digest"] = self.digest
        return payload


@dataclass(frozen=True)
class MetricSpec:
    metric_id: str
    label: str
    unit: str
    definition_version: str
    definition_digest: str
    preference: MetricPreference
    materiality: Decimal
    display: MetricDisplay = MetricDisplay.NUMBER
    display_decimals: int = 2
    target_min: Decimal | None = None
    target_max: Decimal | None = None

    def __post_init__(self) -> None:
        _require_text(self.metric_id, "metric_id")
        _require_text(self.label, "label")
        _require_text(self.unit, "unit")
        _require_text(self.definition_version, "definition_version")
        _require_digest(self.definition_digest, "definition_digest")
        if not isinstance(self.preference, MetricPreference):
            raise AttributionError("invalid_metric_preference", "preference is not registered")
        if not isinstance(self.display, MetricDisplay):
            raise AttributionError("invalid_metric_display", "display is not registered")
        _require_decimal(self.materiality, "materiality")
        if self.materiality < 0:
            raise AttributionError("invalid_materiality", "materiality must not be negative")
        if not isinstance(self.display_decimals, int) or isinstance(self.display_decimals, bool):
            raise AttributionError(
                "invalid_display_precision", "display_decimals must be an integer"
            )
        if not 0 <= self.display_decimals <= 8:
            raise AttributionError(
                "invalid_display_precision", "display_decimals must be between 0 and 8"
            )
        if self.preference is MetricPreference.TARGET_RANGE:
            if self.target_min is None or self.target_max is None:
                raise AttributionError(
                    "missing_target_range", "target_range requires both target bounds"
                )
            if any(
                not isinstance(item, Decimal) or not item.is_finite()
                for item in (self.target_min, self.target_max)
            ):
                raise AttributionError(
                    "invalid_target_range", "target bounds must be finite Decimals"
                )
            _require_decimal(self.target_min, "target_min")
            _require_decimal(self.target_max, "target_max")
            if self.target_min > self.target_max:
                raise AttributionError(
                    "invalid_target_range", "target_min must not exceed target_max"
                )
        elif self.target_min is not None or self.target_max is not None:
            raise AttributionError(
                "unexpected_target_range", "target bounds require target_range preference"
            )

    def _distance_to_target(self, value: Decimal) -> Decimal:
        if self.target_min is None or self.target_max is None:
            raise AttributionError("missing_target_range", "metric has no target range")
        with _decimal_context(value, self.target_min, self.target_max):
            if value < self.target_min:
                return self.target_min - value
            if value > self.target_max:
                return value - self.target_max
        return Decimal("0")

    def oriented_effect(self, baseline: Decimal, intervention: Decimal) -> Decimal | None:
        _require_decimal(baseline, "baseline_metric")
        _require_decimal(intervention, "intervention_metric")
        with _decimal_context(baseline, intervention, self.materiality):
            raw_effect = baseline - intervention
            if self.preference is MetricPreference.HIGHER:
                return raw_effect
            if self.preference is MetricPreference.LOWER:
                return -raw_effect
            if self.preference is MetricPreference.TARGET_RANGE:
                intervention_distance = self._distance_to_target(intervention)
                baseline_distance = self._distance_to_target(baseline)
                return intervention_distance - baseline_distance
        return None

    def classify(self, oriented_effect: Decimal | None) -> EffectClassification:
        if oriented_effect is None:
            return EffectClassification.UNORIENTED
        if oriented_effect.copy_abs() <= self.materiality:
            return EffectClassification.NEGLIGIBLE
        if oriented_effect > 0:
            return EffectClassification.BENEFICIAL
        return EffectClassification.HARMFUL

    @property
    def digest(self) -> str:
        return digest(self._semantic_dict())

    def _semantic_dict(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "label": self.label,
            "unit": self.unit,
            "definition_version": self.definition_version,
            "definition_digest": self.definition_digest,
            "preference": self.preference.value,
            "materiality": _decimal_text(self.materiality),
            "display": self.display.value,
            "display_decimals": self.display_decimals,
            "target_min": (_decimal_text(self.target_min) if self.target_min is not None else None),
            "target_max": (_decimal_text(self.target_max) if self.target_max is not None else None),
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._semantic_dict()
        payload["digest"] = self.digest
        return payload


@dataclass(frozen=True)
class ConditionSpec:
    condition_id: str
    label: str
    definition_version: str
    definition_digest: str

    def __post_init__(self) -> None:
        for field_name in (
            "condition_id",
            "label",
            "definition_version",
            "definition_digest",
        ):
            _require_text(getattr(self, field_name), field_name)
        _require_digest(self.definition_digest, "definition_digest")

    @property
    def digest(self) -> str:
        return digest(self._semantic_dict())

    def _semantic_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "label": self.label,
            "definition_version": self.definition_version,
            "definition_digest": self.definition_digest,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._semantic_dict()
        payload["digest"] = self.digest
        return payload


@dataclass(frozen=True)
class InteractionSpec:
    interaction_id: str
    partner_component_id: str
    partner_intervention_id: str
    partner_intervention_spec_digest: str
    joint_intervention_spec_digest: str
    composition_order: tuple[str, ...]
    compatibility_semantics: str
    conflict_rule: str

    def __post_init__(self) -> None:
        for field_name in (
            "interaction_id",
            "partner_component_id",
            "partner_intervention_id",
            "partner_intervention_spec_digest",
            "joint_intervention_spec_digest",
            "compatibility_semantics",
            "conflict_rule",
        ):
            _require_text(getattr(self, field_name), field_name)
        _require_digest(
            self.partner_intervention_spec_digest,
            "partner_intervention_spec_digest",
        )
        _require_digest(self.joint_intervention_spec_digest, "joint_intervention_spec_digest")
        object.__setattr__(
            self,
            "composition_order",
            _unique_text(self.composition_order, "composition_order"),
        )
        if len(self.composition_order) != 2:
            raise AttributionError(
                "invalid_composition_order", "pairwise interaction requires exactly two ids"
            )
        if self.partner_intervention_id not in self.composition_order:
            raise AttributionError(
                "invalid_composition_order", "composition must include the partner intervention"
            )

    @property
    def digest(self) -> str:
        return digest(self._semantic_dict())

    def _semantic_dict(self) -> dict[str, Any]:
        return {
            "interaction_id": self.interaction_id,
            "partner_component_id": self.partner_component_id,
            "partner_intervention_id": self.partner_intervention_id,
            "partner_intervention_spec_digest": self.partner_intervention_spec_digest,
            "joint_intervention_spec_digest": self.joint_intervention_spec_digest,
            "composition_order": list(self.composition_order),
            "compatibility_semantics": self.compatibility_semantics,
            "conflict_rule": self.conflict_rule,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._semantic_dict()
        payload["digest"] = self.digest
        return payload


@dataclass(frozen=True)
class AttributionQuestion:
    question_id: str
    question_version: str
    subject_component_id: str
    intervention_id: str
    intervention_spec_digest: str
    hypothesis_family_id: str
    estimand_kind: str
    effect_method: str
    intervention_semantics: str
    target_population: str
    sample_role: SampleRole
    analysis_role: AnalysisRole
    multiple_comparison_rule: str
    classification_rule: str
    decision_rule: str
    decision_actions: Mapping[str, str]
    uncertainty_required: bool
    uncertainty_method: str
    stability_required: bool
    stability_method: str
    bound_before_outcomes: bool
    primary_metric_ids: tuple[str, ...]
    metric_specs: tuple[MetricSpec, ...]
    required_capabilities: tuple[AttributionCapability, ...] = ()
    condition_specs: tuple[ConditionSpec, ...] = ()
    interaction_specs: tuple[InteractionSpec, ...] = ()
    known_assumptions: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()
    stop_conditions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "question_id",
            "question_version",
            "subject_component_id",
            "intervention_id",
            "intervention_spec_digest",
            "hypothesis_family_id",
            "estimand_kind",
            "effect_method",
            "intervention_semantics",
            "target_population",
            "multiple_comparison_rule",
            "classification_rule",
            "decision_rule",
            "uncertainty_method",
            "stability_method",
        ):
            _require_text(getattr(self, field_name), field_name)
        _require_digest(self.intervention_spec_digest, "intervention_spec_digest")
        decision_actions = dict(self.decision_actions)
        if set(decision_actions) != _DECISION_ACTION_KEYS:
            raise AttributionError(
                "invalid_decision_actions",
                "decision actions must cover every publishable status and classification",
            )
        for key, action in decision_actions.items():
            _require_text(key, "decision_action_key")
            _require_text(action, "decision_action")
        object.__setattr__(
            self,
            "decision_actions",
            MappingProxyType(dict(sorted(decision_actions.items()))),
        )
        if not isinstance(self.sample_role, SampleRole):
            raise AttributionError("invalid_sample_role", "sample_role must use the registry")
        if not isinstance(self.analysis_role, AnalysisRole):
            raise AttributionError("invalid_analysis_role", "analysis_role must use the registry")
        _require_bool(self.uncertainty_required, "uncertainty_required")
        _require_bool(self.stability_required, "stability_required")
        _require_bool(self.bound_before_outcomes, "bound_before_outcomes")
        primary_metric_ids = _unique_text(self.primary_metric_ids, "primary_metric_ids")
        if not primary_metric_ids:
            raise AttributionError("missing_primary_metric", "question requires a primary metric")
        object.__setattr__(self, "primary_metric_ids", primary_metric_ids)
        if not self.metric_specs:
            raise AttributionError("missing_metric", "question requires at least one metric")
        if any(not isinstance(item, MetricSpec) for item in self.metric_specs):
            raise AttributionError("invalid_metric", "metric_specs must contain MetricSpec values")
        metric_ids = [item.metric_id for item in self.metric_specs]
        if len(metric_ids) != len(set(metric_ids)):
            raise AttributionError("duplicate_metric", "metric ids must be unique")
        missing_primary = sorted(set(self.primary_metric_ids).difference(metric_ids))
        if missing_primary:
            raise AttributionError(
                "unknown_primary_metric", f"primary metrics are not declared: {missing_primary}"
            )
        object.__setattr__(
            self,
            "metric_specs",
            tuple(sorted(self.metric_specs, key=lambda item: item.metric_id)),
        )
        if any(
            not isinstance(capability, AttributionCapability)
            for capability in self.required_capabilities
        ):
            raise AttributionError(
                "invalid_capability", "required capabilities must use the registry"
            )
        capabilities = set(self.required_capabilities)
        capabilities.update(
            {
                AttributionCapability.BASELINE_REPLAY,
                AttributionCapability.LEGAL_INTERVENTION,
            }
        )
        if self.condition_specs:
            capabilities.add(AttributionCapability.CONDITION_MAP)
        object.__setattr__(
            self,
            "required_capabilities",
            tuple(sorted(capabilities, key=lambda item: item.value)),
        )
        for field_name, item_type, key in (
            ("condition_specs", ConditionSpec, lambda item: item.condition_id),
            ("interaction_specs", InteractionSpec, lambda item: item.interaction_id),
        ):
            values = tuple(getattr(self, field_name))
            if any(not isinstance(item, item_type) for item in values):
                raise AttributionError("invalid_evidence_spec", f"{field_name} has invalid values")
            identities = [key(item) for item in values]
            if len(identities) != len(set(identities)):
                raise AttributionError("duplicate_identity", f"{field_name} ids must be unique")
            object.__setattr__(self, field_name, tuple(sorted(values, key=key)))
        for interaction in self.interaction_specs:
            expected_interventions = {
                self.intervention_id,
                interaction.partner_intervention_id,
            }
            if len(expected_interventions) != 2:
                raise AttributionError(
                    "invalid_interaction_partner",
                    "subject and partner interventions must be distinct",
                )
            if interaction.partner_component_id == self.subject_component_id:
                raise AttributionError(
                    "invalid_interaction_partner",
                    "subject and partner components must be distinct",
                )
            if set(interaction.composition_order) != expected_interventions:
                raise AttributionError(
                    "invalid_composition_order",
                    "interaction composition must contain exactly subject and partner",
                )
        for field_name in ("known_assumptions", "exclusions", "stop_conditions"):
            object.__setattr__(
                self,
                field_name,
                _unique_text(getattr(self, field_name), field_name),
            )

    @property
    def digest(self) -> str:
        return digest(self._semantic_dict())

    @property
    def specs_by_id(self) -> dict[str, MetricSpec]:
        return {item.metric_id: item for item in self.metric_specs}

    def decision_action(self, status: ClaimStatus, classification: ClaimClassification) -> str:
        return self.decision_actions[f"{status.value}:{classification.value}"]

    def _semantic_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "question_version": self.question_version,
            "subject_component_id": self.subject_component_id,
            "intervention_id": self.intervention_id,
            "intervention_spec_digest": self.intervention_spec_digest,
            "hypothesis_family_id": self.hypothesis_family_id,
            "estimand_kind": self.estimand_kind,
            "effect_method": self.effect_method,
            "intervention_semantics": self.intervention_semantics,
            "target_population": self.target_population,
            "sample_role": self.sample_role.value,
            "analysis_role": self.analysis_role.value,
            "multiple_comparison_rule": self.multiple_comparison_rule,
            "classification_rule": self.classification_rule,
            "decision_rule": self.decision_rule,
            "decision_actions": dict(self.decision_actions),
            "uncertainty_required": self.uncertainty_required,
            "uncertainty_method": self.uncertainty_method,
            "stability_required": self.stability_required,
            "stability_method": self.stability_method,
            "bound_before_outcomes": self.bound_before_outcomes,
            "primary_metric_ids": list(self.primary_metric_ids),
            "metric_specs": [item.to_dict() for item in self.metric_specs],
            "required_capabilities": [item.value for item in self.required_capabilities],
            "condition_specs": [item.to_dict() for item in self.condition_specs],
            "interaction_specs": [item.to_dict() for item in self.interaction_specs],
            "known_assumptions": list(self.known_assumptions),
            "exclusions": list(self.exclusions),
            "stop_conditions": list(self.stop_conditions),
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._semantic_dict()
        payload["digest"] = self.digest
        return payload


@dataclass(frozen=True)
class ExperimentManifest:
    experiment_id: str
    schema_version: str
    question_id: str
    question_digest: str
    role: ExperimentRole
    strategy_id: str
    strategy_version: str
    adapter_id: str
    adapter_version: str
    capability_manifest_version: str
    capability_manifest_digest: str
    native_topology_digest: str
    context_digest: str
    context_bindings: Mapping[str, str]
    artifact_digest: str
    native_trace_digest: str | None
    plan_receipt_digest: str | None
    execution_work_digest: str | None
    raw_execution_digest: str | None
    metric_calculator_id: str
    metric_calculator_version: str
    metric_spec_digests: Mapping[str, str]
    period_start: date
    period_end: date
    intervention_ids: tuple[str, ...]
    changed_component_ids: tuple[str, ...]
    capital_constrained: bool
    reproduced: bool
    reconciled: bool
    pit_validated: bool
    partition_authorized: bool
    question_bound_before_outcomes: bool
    decision_trace_reconciled: bool | None
    opportunity_trace_reconciled: bool | None
    interaction_spec_digest: str | None
    metrics: Mapping[str, Decimal]

    def __post_init__(self) -> None:
        for field_name in (
            "experiment_id",
            "schema_version",
            "question_id",
            "question_digest",
            "strategy_id",
            "strategy_version",
            "adapter_id",
            "adapter_version",
            "capability_manifest_version",
            "capability_manifest_digest",
            "native_topology_digest",
            "context_digest",
            "artifact_digest",
            "metric_calculator_id",
            "metric_calculator_version",
        ):
            _require_text(getattr(self, field_name), field_name)
        for field_name in (
            "question_digest",
            "capability_manifest_digest",
            "native_topology_digest",
            "context_digest",
            "artifact_digest",
        ):
            _require_digest(getattr(self, field_name), field_name)
        if self.native_trace_digest is not None:
            _require_digest(self.native_trace_digest, "native_trace_digest")
        lineage_digests = (
            self.plan_receipt_digest,
            self.execution_work_digest,
            self.raw_execution_digest,
        )
        if any(item is not None for item in lineage_digests) and any(
            item is None for item in lineage_digests
        ):
            raise AttributionError(
                "incomplete_execution_lineage",
                "execution lineage digests must be supplied together",
            )
        for field_name in (
            "plan_receipt_digest",
            "execution_work_digest",
            "raw_execution_digest",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _require_digest(value, field_name)
        metric_spec_digests = _text_mapping(self.metric_spec_digests, "metric_spec_digests")
        for metric_digest in metric_spec_digests.values():
            _require_digest(metric_digest, "metric_spec_digest")
        object.__setattr__(self, "metric_spec_digests", metric_spec_digests)
        if not isinstance(self.role, ExperimentRole):
            raise AttributionError("invalid_experiment_role", "role must use the registry")
        for field_name in (
            "capital_constrained",
            "reproduced",
            "reconciled",
            "pit_validated",
            "partition_authorized",
            "question_bound_before_outcomes",
        ):
            _require_bool(getattr(self, field_name), field_name)
        for field_name in (
            "decision_trace_reconciled",
            "opportunity_trace_reconciled",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _require_bool(value, field_name)
        bindings = _text_mapping(self.context_bindings, "context_bindings")
        required_context = {
            "data_snapshot",
            "eligible_universe",
            "capital",
            "matcher",
            "costs",
            "settlement",
            "policy",
            "execution_assumptions",
            "seeds",
        }
        missing_context = sorted(required_context.difference(bindings))
        if missing_context:
            raise AttributionError(
                "incomplete_context_binding",
                f"comparison context is missing bindings: {missing_context}",
            )
        if self.context_digest != digest(dict(bindings)):
            raise AttributionError(
                "context_digest_mismatch", "context_digest must cover the visible bindings"
            )
        object.__setattr__(self, "context_bindings", bindings)
        if self.period_start > self.period_end:
            raise AttributionError("invalid_period", "period_start must not follow period_end")
        object.__setattr__(
            self,
            "intervention_ids",
            _unique_text(self.intervention_ids, "intervention_ids"),
        )
        object.__setattr__(
            self,
            "changed_component_ids",
            _unique_text(self.changed_component_ids, "changed_component_ids"),
        )
        if self.role is ExperimentRole.BASELINE:
            if self.intervention_ids or self.changed_component_ids:
                raise AttributionError(
                    "invalid_baseline", "baseline cannot declare an intervention or change"
                )
        elif not self.intervention_ids or not self.changed_component_ids:
            raise AttributionError(
                "missing_intervention", "intervention experiments must declare changes"
            )
        if self.role is ExperimentRole.INTERVENTION and len(self.intervention_ids) != 1:
            raise AttributionError(
                "invalid_intervention_count", "single intervention role requires one intervention"
            )
        if self.role is ExperimentRole.JOINT_INTERVENTION and len(self.intervention_ids) < 2:
            raise AttributionError(
                "invalid_intervention_count", "joint intervention requires at least two ids"
            )
        if self.role is ExperimentRole.JOINT_INTERVENTION:
            _require_digest(self.interaction_spec_digest, "interaction_spec_digest")
        elif self.interaction_spec_digest is not None:
            raise AttributionError(
                "unexpected_interaction_spec",
                "only a joint intervention may bind an interaction spec",
            )
        normalized_metrics: dict[str, Decimal] = {}
        for metric_id, value in self.metrics.items():
            _require_text(metric_id, "metric_id")
            _require_decimal(value, f"metric:{metric_id}")
            normalized_metrics[metric_id] = value
        if set(metric_spec_digests) != set(normalized_metrics):
            raise AttributionError(
                "metric_definition_set_mismatch",
                "metric definition digests must bind every metric value",
            )
        object.__setattr__(self, "metrics", MappingProxyType(normalized_metrics))

    @property
    def digest(self) -> str:
        return digest(self._semantic_dict())

    def _semantic_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "schema_version": self.schema_version,
            "question_id": self.question_id,
            "question_digest": self.question_digest,
            "role": self.role.value,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "capability_manifest_version": self.capability_manifest_version,
            "capability_manifest_digest": self.capability_manifest_digest,
            "native_topology_digest": self.native_topology_digest,
            "context_digest": self.context_digest,
            "context_bindings": dict(sorted(self.context_bindings.items())),
            "artifact_digest": self.artifact_digest,
            "native_trace_digest": self.native_trace_digest,
            "plan_receipt_digest": self.plan_receipt_digest,
            "execution_work_digest": self.execution_work_digest,
            "raw_execution_digest": self.raw_execution_digest,
            "metric_calculator_id": self.metric_calculator_id,
            "metric_calculator_version": self.metric_calculator_version,
            "metric_spec_digests": dict(sorted(self.metric_spec_digests.items())),
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "intervention_ids": list(self.intervention_ids),
            "changed_component_ids": list(self.changed_component_ids),
            "capital_constrained": self.capital_constrained,
            "reproduced": self.reproduced,
            "reconciled": self.reconciled,
            "pit_validated": self.pit_validated,
            "partition_authorized": self.partition_authorized,
            "question_bound_before_outcomes": self.question_bound_before_outcomes,
            "decision_trace_reconciled": self.decision_trace_reconciled,
            "opportunity_trace_reconciled": self.opportunity_trace_reconciled,
            "interaction_spec_digest": self.interaction_spec_digest,
            "metrics": {key: _decimal_text(value) for key, value in sorted(self.metrics.items())},
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._semantic_dict()
        payload["digest"] = self.digest
        return payload


def experiment_evidence_payload(experiment: ExperimentManifest) -> dict[str, Any]:
    """Return the exact manifest facts that a result artifact must independently bind."""

    payload = experiment._semantic_dict()
    payload.pop("artifact_digest")
    return payload


def experiment_evidence_envelope(
    experiment: ExperimentManifest,
    *,
    producer_id: str,
    producer_version: str,
    source_kind: str,
) -> SemanticArtifactEnvelope:
    parents = {
        experiment.question_digest,
        experiment.capability_manifest_digest,
        experiment.native_topology_digest,
        experiment.context_digest,
    }
    if experiment.native_trace_digest is not None:
        parents.add(experiment.native_trace_digest)
    parents.update(
        item
        for item in (
            experiment.plan_receipt_digest,
            experiment.execution_work_digest,
            experiment.raw_execution_digest,
        )
        if item is not None
    )
    return SemanticArtifactEnvelope(
        artifact_id=f"artifact.{experiment.experiment_id}",
        artifact_kind=ArtifactKind.EXPERIMENT_RESULT,
        producer_id=producer_id,
        producer_version=producer_version,
        source_kind=source_kind,
        parent_digests=tuple(sorted(parents)),
        payload=experiment_evidence_payload(experiment),
    )


def native_trace_envelope(
    *,
    trace_id: str,
    question_digest: str,
    capability_manifest_digest: str,
    native_topology_digest: str,
    context_digest: str,
    producer_id: str,
    producer_version: str,
    source_kind: str,
    payload: Mapping[str, Any],
) -> SemanticArtifactEnvelope:
    return SemanticArtifactEnvelope(
        artifact_id=trace_id,
        artifact_kind=ArtifactKind.NATIVE_TRACE,
        producer_id=producer_id,
        producer_version=producer_version,
        source_kind=source_kind,
        parent_digests=tuple(
            sorted(
                {
                    question_digest,
                    capability_manifest_digest,
                    native_topology_digest,
                    context_digest,
                }
            )
        ),
        payload=payload,
    )


def _fixed(value: Decimal, decimals: int) -> str:
    with _decimal_context(value, extra_places=decimals):
        quantum = Decimal(1).scaleb(-decimals)
        quantized = value.quantize(quantum)
    if quantized.is_zero():
        quantized = quantized.copy_abs()
    return format(quantized, f".{decimals}f")


def _percentage_points(value: Decimal) -> Decimal:
    with _decimal_context(value, extra_places=3):
        return value * Decimal("100")


def _display_value(spec: MetricSpec, value: Decimal) -> str:
    if spec.display is MetricDisplay.PERCENT:
        return f"{_fixed(_percentage_points(value), spec.display_decimals)}%"
    if spec.display is MetricDisplay.CURRENCY:
        return f"CNY {_fixed(value, spec.display_decimals)}"
    if spec.display is MetricDisplay.COUNT:
        return f"{_fixed(value, 0)}"
    if spec.display is MetricDisplay.DAYS:
        return f"{_fixed(value, spec.display_decimals)} days"
    return _fixed(value, spec.display_decimals)


def _display_delta(spec: MetricSpec, value: Decimal | None) -> str | None:
    if value is None:
        return None
    sign = "+" if value > 0 else ""
    if spec.display is MetricDisplay.PERCENT:
        return f"{sign}{_fixed(_percentage_points(value), spec.display_decimals)} pp"
    if spec.display is MetricDisplay.CURRENCY:
        return f"{sign}CNY {_fixed(value, spec.display_decimals)}"
    suffix = " days" if spec.display is MetricDisplay.DAYS else ""
    return f"{sign}{_fixed(value, spec.display_decimals)}{suffix}"


@dataclass(frozen=True)
class MetricEffect:
    metric_id: str
    label: str
    unit: str
    preference: MetricPreference
    baseline_value: Decimal
    intervention_value: Decimal
    raw_effect: Decimal
    oriented_effect: Decimal | None
    materiality: Decimal
    classification: EffectClassification
    baseline_display: str
    intervention_display: str
    raw_effect_display: str
    oriented_effect_display: str | None
    uncertainty_evidence_digest: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "label": self.label,
            "unit": self.unit,
            "preference": self.preference.value,
            "baseline_value": _decimal_text(self.baseline_value),
            "intervention_value": _decimal_text(self.intervention_value),
            "raw_effect": _decimal_text(self.raw_effect),
            "oriented_effect": (
                _decimal_text(self.oriented_effect) if self.oriented_effect is not None else None
            ),
            "materiality": _decimal_text(self.materiality),
            "classification": self.classification.value,
            "uncertainty_evidence_digest": self.uncertainty_evidence_digest,
            "display": {
                "baseline": self.baseline_display,
                "intervention": self.intervention_display,
                "raw_effect": self.raw_effect_display,
                "oriented_effect": self.oriented_effect_display,
            },
        }


@dataclass(frozen=True)
class ConditionComparison:
    condition_id: str
    condition_spec_digest: str
    label: str
    sample_count: int
    question_digest: str
    adapter_capability_digest: str
    context_digest: str
    period_start: date
    period_end: date
    baseline_experiment_digest: str
    intervention_experiment_digest: str
    result_digest: str
    plan_receipt_digest: str | None
    execution_work_digest: str | None
    raw_execution_digest: str | None
    reconciled: bool
    baseline_metrics: Mapping[str, Decimal]
    intervention_metrics: Mapping[str, Decimal]

    def __post_init__(self) -> None:
        for field_name in (
            "condition_id",
            "condition_spec_digest",
            "label",
            "question_digest",
            "adapter_capability_digest",
            "context_digest",
            "baseline_experiment_digest",
            "intervention_experiment_digest",
            "result_digest",
        ):
            _require_text(getattr(self, field_name), field_name)
        for field_name in (
            "condition_spec_digest",
            "question_digest",
            "adapter_capability_digest",
            "context_digest",
            "baseline_experiment_digest",
            "intervention_experiment_digest",
            "result_digest",
        ):
            _require_digest(getattr(self, field_name), field_name)
        lineage_digests = (
            self.plan_receipt_digest,
            self.execution_work_digest,
            self.raw_execution_digest,
        )
        if any(item is not None for item in lineage_digests) and any(
            item is None for item in lineage_digests
        ):
            raise AttributionError(
                "incomplete_execution_lineage",
                "condition execution lineage digests must be supplied together",
            )
        for field_name in (
            "plan_receipt_digest",
            "execution_work_digest",
            "raw_execution_digest",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _require_digest(value, field_name)
        if not isinstance(self.sample_count, int) or isinstance(self.sample_count, bool):
            raise AttributionError("invalid_sample_count", "sample_count must be an integer")
        if self.sample_count <= 0:
            raise AttributionError("invalid_sample_count", "sample_count must be positive")
        _require_bool(self.reconciled, "reconciled")
        if self.period_start > self.period_end:
            raise AttributionError("invalid_period", "period_start must not follow period_end")
        for field_name in ("baseline_metrics", "intervention_metrics"):
            values = dict(getattr(self, field_name))
            for metric_id, value in values.items():
                _require_text(metric_id, "metric_id")
                _require_decimal(value, f"{field_name}:{metric_id}")
            object.__setattr__(self, field_name, MappingProxyType(values))

    @property
    def digest(self) -> str:
        return digest(self._semantic_dict())

    def _semantic_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "condition_spec_digest": self.condition_spec_digest,
            "label": self.label,
            "sample_count": self.sample_count,
            "question_digest": self.question_digest,
            "adapter_capability_digest": self.adapter_capability_digest,
            "context_digest": self.context_digest,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "baseline_experiment_digest": self.baseline_experiment_digest,
            "intervention_experiment_digest": self.intervention_experiment_digest,
            "result_digest": self.result_digest,
            "plan_receipt_digest": self.plan_receipt_digest,
            "execution_work_digest": self.execution_work_digest,
            "raw_execution_digest": self.raw_execution_digest,
            "reconciled": self.reconciled,
            "baseline_metrics": {
                key: _decimal_text(value) for key, value in sorted(self.baseline_metrics.items())
            },
            "intervention_metrics": {
                key: _decimal_text(value)
                for key, value in sorted(self.intervention_metrics.items())
            },
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._semantic_dict()
        payload["digest"] = self.digest
        return payload


def condition_analysis_result_payload(
    comparison: ConditionComparison,
) -> dict[str, Any]:
    """Return the exact condition facts independently bound by its result artifact."""

    payload = comparison._semantic_dict()
    payload.pop("result_digest")
    return payload


def condition_analysis_result_envelope(
    comparison: ConditionComparison,
    *,
    producer_id: str,
    producer_version: str,
    source_kind: str,
) -> SemanticArtifactEnvelope:
    return SemanticArtifactEnvelope(
        artifact_id=f"artifact.condition.{comparison.condition_id}",
        artifact_kind=ArtifactKind.CONDITION_ANALYSIS_RESULT,
        producer_id=producer_id,
        producer_version=producer_version,
        source_kind=source_kind,
        parent_digests=tuple(
            sorted(
                {
                    comparison.condition_spec_digest,
                    comparison.question_digest,
                    comparison.adapter_capability_digest,
                    comparison.context_digest,
                    comparison.baseline_experiment_digest,
                    comparison.intervention_experiment_digest,
                    *(
                        item
                        for item in (
                            comparison.plan_receipt_digest,
                            comparison.execution_work_digest,
                            comparison.raw_execution_digest,
                        )
                        if item is not None
                    ),
                }
            )
        ),
        payload=condition_analysis_result_payload(comparison),
    )


@dataclass(frozen=True)
class ConditionEffect:
    condition_id: str
    condition_spec_digest: str
    evidence_digest: str
    label: str
    sample_count: int
    metric_effects: tuple[MetricEffect, ...]

    def __post_init__(self) -> None:
        metric_effects = tuple(self.metric_effects)
        if any(not isinstance(item, MetricEffect) for item in metric_effects):
            raise AttributionError("invalid_metric_effect", "condition metric effects are invalid")
        object.__setattr__(self, "metric_effects", metric_effects)

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "condition_spec_digest": self.condition_spec_digest,
            "evidence_digest": self.evidence_digest,
            "label": self.label,
            "sample_count": self.sample_count,
            "metric_effects": [item.to_dict() for item in self.metric_effects],
        }


@dataclass(frozen=True)
class AnalysisCheckEvidence:
    check_id: str
    kind: AnalysisCheckKind
    method: str
    scope: AnalysisCheckScope
    metric_ids: tuple[str, ...]
    target_evidence_digest: str
    question_digest: str
    adapter_capability_digest: str
    baseline_experiment_digest: str
    intervention_experiment_digest: str
    result_digest: str
    completed: bool
    passed: bool

    def __post_init__(self) -> None:
        for field_name in (
            "check_id",
            "method",
            "target_evidence_digest",
            "question_digest",
            "adapter_capability_digest",
            "baseline_experiment_digest",
            "intervention_experiment_digest",
            "result_digest",
        ):
            _require_text(getattr(self, field_name), field_name)
        for field_name in (
            "target_evidence_digest",
            "question_digest",
            "adapter_capability_digest",
            "baseline_experiment_digest",
            "intervention_experiment_digest",
            "result_digest",
        ):
            _require_digest(getattr(self, field_name), field_name)
        if not isinstance(self.kind, AnalysisCheckKind):
            raise AttributionError("invalid_check_kind", "analysis check kind is not registered")
        if not isinstance(self.scope, AnalysisCheckScope):
            raise AttributionError("invalid_check_scope", "analysis check scope is not registered")
        metric_ids = tuple(sorted(_unique_text(self.metric_ids, "analysis_metric_ids")))
        if not metric_ids:
            raise AttributionError(
                "missing_analysis_metric", "analysis check requires scoped metric ids"
            )
        object.__setattr__(self, "metric_ids", metric_ids)
        _require_bool(self.completed, "completed")
        _require_bool(self.passed, "passed")

    @property
    def digest(self) -> str:
        return digest(self._semantic_dict())

    def _semantic_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "kind": self.kind.value,
            "method": self.method,
            "scope": self.scope.value,
            "metric_ids": list(self.metric_ids),
            "target_evidence_digest": self.target_evidence_digest,
            "question_digest": self.question_digest,
            "adapter_capability_digest": self.adapter_capability_digest,
            "baseline_experiment_digest": self.baseline_experiment_digest,
            "intervention_experiment_digest": self.intervention_experiment_digest,
            "result_digest": self.result_digest,
            "completed": self.completed,
            "passed": self.passed,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._semantic_dict()
        payload["digest"] = self.digest
        return payload


def analysis_check_result_payload(check: AnalysisCheckEvidence) -> dict[str, Any]:
    payload = check._semantic_dict()
    payload.pop("result_digest")
    return payload


def analysis_check_result_envelope(
    check: AnalysisCheckEvidence,
    *,
    producer_id: str,
    producer_version: str,
    source_kind: str,
) -> SemanticArtifactEnvelope:
    return SemanticArtifactEnvelope(
        artifact_id=f"artifact.{check.check_id}",
        artifact_kind=ArtifactKind.ANALYSIS_CHECK_RESULT,
        producer_id=producer_id,
        producer_version=producer_version,
        source_kind=source_kind,
        parent_digests=tuple(
            sorted(
                {
                    check.target_evidence_digest,
                    check.question_digest,
                    check.adapter_capability_digest,
                    check.baseline_experiment_digest,
                    check.intervention_experiment_digest,
                }
            )
        ),
        payload=analysis_check_result_payload(check),
    )


@dataclass(frozen=True)
class InteractionComparison:
    interaction_id: str
    interaction_spec_digest: str
    partner_component_id: str
    baseline_experiment_digest: str
    subject_experiment_digest: str
    partner_intervention: ExperimentManifest
    joint_intervention: ExperimentManifest

    def __post_init__(self) -> None:
        for field_name in (
            "interaction_id",
            "interaction_spec_digest",
            "partner_component_id",
            "baseline_experiment_digest",
            "subject_experiment_digest",
        ):
            _require_text(getattr(self, field_name), field_name)
        for field_name in (
            "interaction_spec_digest",
            "baseline_experiment_digest",
            "subject_experiment_digest",
        ):
            _require_digest(getattr(self, field_name), field_name)
        if not isinstance(self.partner_intervention, ExperimentManifest):
            raise AttributionError("invalid_interaction", "partner run is invalid")
        if not isinstance(self.joint_intervention, ExperimentManifest):
            raise AttributionError("invalid_interaction", "joint run is invalid")

    @property
    def digest(self) -> str:
        return digest(self._semantic_dict())

    def _semantic_dict(self) -> dict[str, Any]:
        return {
            "interaction_id": self.interaction_id,
            "interaction_spec_digest": self.interaction_spec_digest,
            "partner_component_id": self.partner_component_id,
            "baseline_experiment_digest": self.baseline_experiment_digest,
            "subject_experiment_digest": self.subject_experiment_digest,
            "partner_experiment_id": self.partner_intervention.experiment_id,
            "partner_experiment_digest": self.partner_intervention.digest,
            "joint_experiment_id": self.joint_intervention.experiment_id,
            "joint_experiment_digest": self.joint_intervention.digest,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._semantic_dict()
        payload["digest"] = self.digest
        return payload


@dataclass(frozen=True)
class InteractionEffect:
    interaction_id: str
    interaction_spec_digest: str
    partner_component_id: str
    partner_experiment_id: str
    partner_experiment_digest: str
    joint_experiment_id: str
    joint_experiment_digest: str
    metric_id: str
    label: str
    raw_interaction: Decimal
    oriented_interaction: Decimal | None
    material: bool
    raw_display: str
    oriented_display: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "interaction_id": self.interaction_id,
            "interaction_spec_digest": self.interaction_spec_digest,
            "partner_component_id": self.partner_component_id,
            "partner_experiment_id": self.partner_experiment_id,
            "partner_experiment_digest": self.partner_experiment_digest,
            "joint_experiment_id": self.joint_experiment_id,
            "joint_experiment_digest": self.joint_experiment_digest,
            "metric_id": self.metric_id,
            "label": self.label,
            "raw_interaction": _decimal_text(self.raw_interaction),
            "oriented_interaction": (
                _decimal_text(self.oriented_interaction)
                if self.oriented_interaction is not None
                else None
            ),
            "material": self.material,
            "display": {
                "raw": self.raw_display,
                "oriented": self.oriented_display,
            },
        }


@dataclass(frozen=True)
class AttributionClaim:
    claim_id: str
    schema_version: str
    question_id: str
    question_digest: str
    subject_component_id: str
    subject_label: str
    intervention_id: str
    primary_metric_ids: tuple[str, ...]
    strategy_id: str
    strategy_version: str
    adapter_id: str
    adapter_version: str
    capability_manifest_version: str
    capability_manifest_digest: str
    native_topology_digest: str
    context_digest: str
    period_start: date
    period_end: date
    status: ClaimStatus
    classification: ClaimClassification
    next_action_code: str
    evidence_type: EvidenceType
    method_registry_version: str
    applied_method_ids: tuple[str, ...]
    validation_flags: tuple[ValidationFlag, ...]
    reason_codes: tuple[str, ...]
    limitations: tuple[str, ...]
    metric_effects: tuple[MetricEffect, ...]
    condition_effects: tuple[ConditionEffect, ...]
    interaction_effects: tuple[InteractionEffect, ...]
    analysis_checks: tuple[AnalysisCheckEvidence, ...]
    verification_receipts: tuple[ArtifactVerificationReceipt, ...]
    supporting_evidence_digests: tuple[str, ...]
    baseline_experiment_id: str
    intervention_experiment_id: str
    baseline_experiment_digest: str
    intervention_experiment_digest: str
    baseline_artifact_digest: str
    intervention_artifact_digest: str

    def __post_init__(self) -> None:
        for field_name in (
            "validation_flags",
            "reason_codes",
            "limitations",
            "metric_effects",
            "condition_effects",
            "interaction_effects",
            "analysis_checks",
            "verification_receipts",
            "supporting_evidence_digests",
        ):
            object.__setattr__(self, field_name, tuple(getattr(self, field_name)))
        for field_name in (
            "claim_id",
            "schema_version",
            "question_id",
            "question_digest",
            "subject_component_id",
            "subject_label",
            "intervention_id",
            "strategy_id",
            "strategy_version",
            "adapter_id",
            "adapter_version",
            "capability_manifest_version",
            "capability_manifest_digest",
            "native_topology_digest",
            "context_digest",
            "baseline_experiment_id",
            "intervention_experiment_id",
            "baseline_experiment_digest",
            "intervention_experiment_digest",
            "baseline_artifact_digest",
            "intervention_artifact_digest",
            "next_action_code",
            "method_registry_version",
        ):
            _require_text(getattr(self, field_name), field_name)
        for field_name in (
            "question_digest",
            "capability_manifest_digest",
            "native_topology_digest",
            "context_digest",
            "baseline_experiment_digest",
            "intervention_experiment_digest",
            "baseline_artifact_digest",
            "intervention_artifact_digest",
        ):
            _require_digest(getattr(self, field_name), field_name)
        if not isinstance(self.status, ClaimStatus):
            raise AttributionError("invalid_claim_status", "claim status is not registered")
        if not isinstance(self.classification, ClaimClassification):
            raise AttributionError(
                "invalid_claim_classification", "claim classification is not registered"
            )
        if not isinstance(self.evidence_type, EvidenceType):
            raise AttributionError("invalid_evidence_type", "evidence type is not registered")
        if any(not isinstance(item, ValidationFlag) for item in self.validation_flags):
            raise AttributionError("invalid_validation_flag", "validation flags are invalid")
        if len(self.validation_flags) != len(set(self.validation_flags)):
            raise AttributionError("duplicate_validation_flag", "validation flags must not repeat")
        for field_name in ("reason_codes", "limitations"):
            object.__setattr__(
                self,
                field_name,
                _unique_text(getattr(self, field_name), field_name),
            )
        for field_name, item_type in (
            ("metric_effects", MetricEffect),
            ("condition_effects", ConditionEffect),
            ("interaction_effects", InteractionEffect),
        ):
            if any(not isinstance(item, item_type) for item in getattr(self, field_name)):
                raise AttributionError(
                    "invalid_claim_effect", f"{field_name} contains invalid values"
                )
        if any(not isinstance(item, AnalysisCheckEvidence) for item in self.analysis_checks):
            raise AttributionError("invalid_analysis_check", "claim analysis checks are invalid")
        if any(
            not isinstance(item, ArtifactVerificationReceipt) for item in self.verification_receipts
        ):
            raise AttributionError(
                "invalid_verification_receipt", "claim verification receipts are invalid"
            )
        receipt_digests = [item.digest for item in self.verification_receipts]
        if len(receipt_digests) != len(set(receipt_digests)):
            raise AttributionError(
                "duplicate_verification_receipt", "claim verification receipts must be unique"
            )
        object.__setattr__(
            self,
            "verification_receipts",
            tuple(sorted(self.verification_receipts, key=lambda item: item.digest)),
        )
        object.__setattr__(
            self,
            "applied_method_ids",
            tuple(sorted(_unique_text(self.applied_method_ids, "applied_method_ids"))),
        )
        object.__setattr__(
            self,
            "primary_metric_ids",
            _unique_text(self.primary_metric_ids, "primary_metric_ids"),
        )
        supporting_digests = _unique_text(
            self.supporting_evidence_digests,
            "supporting_evidence_digests",
        )
        for evidence_digest in supporting_digests:
            _require_digest(evidence_digest, "supporting_evidence_digest")
        object.__setattr__(
            self,
            "supporting_evidence_digests",
            tuple(sorted(supporting_digests)),
        )
        object.__setattr__(
            self,
            "analysis_checks",
            tuple(sorted(self.analysis_checks, key=lambda item: item.kind.value)),
        )

    @property
    def digest(self) -> str:
        return digest(self._semantic_dict())

    def _semantic_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "schema_version": self.schema_version,
            "question_id": self.question_id,
            "question_digest": self.question_digest,
            "subject_component_id": self.subject_component_id,
            "subject_label": self.subject_label,
            "intervention_id": self.intervention_id,
            "primary_metric_ids": list(self.primary_metric_ids),
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "capability_manifest_version": self.capability_manifest_version,
            "capability_manifest_digest": self.capability_manifest_digest,
            "native_topology_digest": self.native_topology_digest,
            "context_digest": self.context_digest,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "status": self.status.value,
            "classification": self.classification.value,
            "next_action_code": self.next_action_code,
            "evidence_type": self.evidence_type.value,
            "method_registry_version": self.method_registry_version,
            "applied_method_ids": list(self.applied_method_ids),
            "validation_flags": sorted(item.value for item in self.validation_flags),
            "reason_codes": sorted(self.reason_codes),
            "limitations": list(self.limitations),
            "metric_effects": [item.to_dict() for item in self.metric_effects],
            "condition_effects": [item.to_dict() for item in self.condition_effects],
            "interaction_effects": [item.to_dict() for item in self.interaction_effects],
            "analysis_checks": [item.to_dict() for item in self.analysis_checks],
            "verification_receipts": [item.to_dict() for item in self.verification_receipts],
            "supporting_evidence_digests": list(self.supporting_evidence_digests),
            "baseline_experiment_id": self.baseline_experiment_id,
            "intervention_experiment_id": self.intervention_experiment_id,
            "baseline_experiment_digest": self.baseline_experiment_digest,
            "intervention_experiment_digest": self.intervention_experiment_digest,
            "baseline_artifact_digest": self.baseline_artifact_digest,
            "intervention_artifact_digest": self.intervention_artifact_digest,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._semantic_dict()
        all_effects = [item.to_dict() for item in self.metric_effects]
        primary = set(self.primary_metric_ids)
        primary_effects = [
            item.to_dict() for item in self.metric_effects if item.metric_id in primary
        ]
        payload.update(
            {
                "decision_summary": {
                    "headline_code": f"claim.{self.classification.value}",
                    "classification": self.classification.value,
                    "status": self.status.value,
                    "next_action_code": self.next_action_code,
                    "primary_effects": primary_effects,
                    "evidence_type": self.evidence_type.value,
                    "validation_flags": sorted(item.value for item in self.validation_flags),
                    "largest_caveat": self.limitations[0] if self.limitations else None,
                },
                "reason_analysis": {
                    "metric_effects": all_effects,
                    "condition_effects": [item.to_dict() for item in self.condition_effects],
                    "interaction_effects": [item.to_dict() for item in self.interaction_effects],
                    "limitations": list(self.limitations),
                },
                "method_evidence": {
                    "question_digest": self.question_digest,
                    "strategy_id": self.strategy_id,
                    "strategy_version": self.strategy_version,
                    "adapter_id": self.adapter_id,
                    "adapter_version": self.adapter_version,
                    "capability_manifest_version": self.capability_manifest_version,
                    "capability_manifest_digest": self.capability_manifest_digest,
                    "native_topology_digest": self.native_topology_digest,
                    "context_digest": self.context_digest,
                    "period_start": self.period_start.isoformat(),
                    "period_end": self.period_end.isoformat(),
                    "baseline_experiment_id": self.baseline_experiment_id,
                    "intervention_experiment_id": self.intervention_experiment_id,
                    "baseline_experiment_digest": self.baseline_experiment_digest,
                    "intervention_experiment_digest": self.intervention_experiment_digest,
                    "baseline_artifact_digest": self.baseline_artifact_digest,
                    "intervention_artifact_digest": self.intervention_artifact_digest,
                    "evidence_type": self.evidence_type.value,
                    "method_registry_version": self.method_registry_version,
                    "applied_method_ids": list(self.applied_method_ids),
                    "status": self.status.value,
                    "validation_flags": sorted(item.value for item in self.validation_flags),
                    "reason_codes": sorted(self.reason_codes),
                    "analysis_checks": [item.to_dict() for item in self.analysis_checks],
                    "verification_receipts": [
                        item.to_dict() for item in self.verification_receipts
                    ],
                    "supporting_evidence_digests": list(self.supporting_evidence_digests),
                },
                "digest": self.digest,
            }
        )
        return payload


def _metric_effects(
    specs: Sequence[MetricSpec],
    baseline_metrics: Mapping[str, Decimal],
    intervention_metrics: Mapping[str, Decimal],
    uncertainty_evidence_digest: str | None = None,
) -> tuple[MetricEffect, ...]:
    effects: list[MetricEffect] = []
    for spec in specs:
        baseline = baseline_metrics[spec.metric_id]
        intervention = intervention_metrics[spec.metric_id]
        with _decimal_context(baseline, intervention, spec.materiality):
            raw = baseline - intervention
        oriented = spec.oriented_effect(baseline, intervention)
        effects.append(
            MetricEffect(
                metric_id=spec.metric_id,
                label=spec.label,
                unit=spec.unit,
                preference=spec.preference,
                baseline_value=baseline,
                intervention_value=intervention,
                raw_effect=raw,
                oriented_effect=oriented,
                materiality=spec.materiality,
                classification=spec.classify(oriented),
                baseline_display=_display_value(spec, baseline),
                intervention_display=_display_value(spec, intervention),
                raw_effect_display=_display_delta(spec, raw) or "0",
                oriented_effect_display=_display_delta(spec, oriented),
                uncertainty_evidence_digest=uncertainty_evidence_digest,
            )
        )
    return tuple(effects)


def _claim_classification(
    effects: Sequence[MetricEffect], primary_metric_ids: Sequence[str]
) -> ClaimClassification:
    primary = [item for item in effects if item.metric_id in set(primary_metric_ids)]
    if len(primary) != len(set(primary_metric_ids)) or any(
        item.classification is EffectClassification.UNORIENTED for item in primary
    ):
        return ClaimClassification.INCONCLUSIVE
    oriented = [
        item.classification
        for item in primary
        if item.classification is not EffectClassification.UNORIENTED
    ]
    if not oriented:
        return ClaimClassification.INCONCLUSIVE
    unique = set(oriented)
    if unique == {EffectClassification.NEGLIGIBLE}:
        return ClaimClassification.NEGLIGIBLE
    if unique.issubset({EffectClassification.BENEFICIAL, EffectClassification.NEGLIGIBLE}):
        return ClaimClassification.BENEFICIAL
    if unique.issubset({EffectClassification.HARMFUL, EffectClassification.NEGLIGIBLE}):
        return ClaimClassification.HARMFUL
    return ClaimClassification.TRADEOFF


def _method_binding_reasons(question: AttributionQuestion) -> tuple[str, ...]:
    declared = (
        ("effect", question.effect_method, AttributionMethodKind.EFFECT),
        ("classification", question.classification_rule, AttributionMethodKind.CLASSIFICATION),
        (
            "multiple_comparison",
            question.multiple_comparison_rule,
            AttributionMethodKind.MULTIPLE_COMPARISON,
        ),
        (
            "uncertainty",
            question.uncertainty_method,
            AttributionMethodKind.UNCERTAINTY,
        ),
        ("stability", question.stability_method, AttributionMethodKind.STABILITY),
    )
    reasons: list[str] = []
    for field_name, method_id, kind in declared:
        registration = _METHOD_REGISTRY.get(method_id)
        if registration is None or not registration.supports(kind):
            reasons.append(f"unregistered_{field_name}_method:{method_id}")
    if (
        not question.uncertainty_required
        and question.uncertainty_method != NOT_REQUIRED_CHECK_METHOD
    ):
        reasons.append("unexpected_uncertainty_method")
    if question.uncertainty_required and question.uncertainty_method == NOT_REQUIRED_CHECK_METHOD:
        reasons.append("required_uncertainty_method_not_implemented")
    if not question.stability_required and question.stability_method != NOT_REQUIRED_CHECK_METHOD:
        reasons.append("unexpected_stability_method")
    if question.stability_required and question.stability_method == NOT_REQUIRED_CHECK_METHOD:
        reasons.append("required_stability_method_not_implemented")
    return tuple(sorted(set(reasons)))


def _receipt_id(artifact_digest: str, artifact_kind: ArtifactKind, subject_id: str) -> str:
    basis = {
        "schema_version": VERIFICATION_RECEIPT_SCHEMA,
        "verifier_id": VERIFIER_ID,
        "verifier_version": VERIFIER_VERSION,
        "artifact_digest": artifact_digest,
        "artifact_kind": artifact_kind.value,
        "subject_id": subject_id,
    }
    return f"receipt_{hashlib.sha256(canonical_bytes(basis)).hexdigest()[:24]}"


def _verify_semantic_artifact(
    *,
    resolver: ArtifactResolver | None,
    artifact_digest: str,
    artifact_kind: ArtifactKind,
    subject_id: str,
    source_kind: str,
    expected_parents: Sequence[str],
    expected_payload: Mapping[str, Any] | None,
    fact_names: Sequence[str] = (),
) -> ArtifactVerificationReceipt:
    reasons: list[str] = []
    envelope: SemanticArtifactEnvelope | None = None
    if resolver is None:
        reasons.append("artifact_resolver_missing")
    else:
        try:
            raw = resolver.resolve(artifact_digest)
        except (AttributionError, KeyError, OSError, TypeError, ValueError):
            reasons.append("artifact_unresolved")
        else:
            try:
                envelope = SemanticArtifactEnvelope.from_bytes(raw)
            except AttributionError as exc:
                reasons.append(exc.reason_code)
    if envelope is not None:
        if envelope.digest != artifact_digest:
            reasons.append("artifact_lookup_digest_mismatch")
        if envelope.artifact_kind is not artifact_kind:
            reasons.append("artifact_kind_mismatch")
        if envelope.source_kind != source_kind:
            reasons.append("artifact_source_mismatch")
        if (envelope.producer_id, envelope.producer_version) not in _REGISTERED_ARTIFACT_PRODUCERS:
            reasons.append("artifact_producer_unregistered")
        if envelope.parent_digests != tuple(sorted(set(expected_parents))):
            reasons.append("artifact_parent_mismatch")
        if expected_payload is not None and dict(envelope.payload) != dict(
            _json_object_copy(expected_payload, "expected_payload")
        ):
            reasons.append("artifact_payload_mismatch")

    facts: dict[str, bool] = {}
    if envelope is not None:
        for name in fact_names:
            value = envelope.payload.get(name)
            if isinstance(value, bool):
                facts[name] = value
            elif value is not None:
                reasons.append(f"artifact_fact_invalid:{name}")
    producer_id = envelope.producer_id if envelope is not None else "unresolved"
    producer_version = envelope.producer_version if envelope is not None else "unresolved"
    parent_digests = envelope.parent_digests if envelope is not None else ()
    unique_reasons = tuple(sorted(set(reasons)))
    return ArtifactVerificationReceipt(
        receipt_id=_receipt_id(artifact_digest, artifact_kind, subject_id),
        artifact_digest=artifact_digest,
        artifact_kind=artifact_kind,
        subject_id=subject_id,
        producer_id=producer_id,
        producer_version=producer_version,
        parent_digests=parent_digests,
        verified_facts=facts,
        passed=not unique_reasons,
        reason_codes=unique_reasons,
    )


_EXPERIMENT_FACT_NAMES = (
    "capital_constrained",
    "reproduced",
    "reconciled",
    "pit_validated",
    "partition_authorized",
    "question_bound_before_outcomes",
    "decision_trace_reconciled",
    "opportunity_trace_reconciled",
)


def _verify_experiment_artifact(
    *,
    adapter: AdapterCapabilityManifest,
    experiment: ExperimentManifest,
    resolver: ArtifactResolver | None,
) -> tuple[ArtifactVerificationReceipt, ...]:
    parents = {
        experiment.question_digest,
        experiment.capability_manifest_digest,
        experiment.native_topology_digest,
        experiment.context_digest,
    }
    receipts: list[ArtifactVerificationReceipt] = []
    if experiment.native_trace_digest is not None:
        trace_parents = (
            experiment.question_digest,
            experiment.capability_manifest_digest,
            experiment.native_topology_digest,
            experiment.context_digest,
        )
        trace_receipt = _verify_semantic_artifact(
            resolver=resolver,
            artifact_digest=experiment.native_trace_digest,
            artifact_kind=ArtifactKind.NATIVE_TRACE,
            subject_id=experiment.experiment_id,
            source_kind=adapter.evidence_source_kind,
            expected_parents=trace_parents,
            expected_payload=None,
        )
        receipts.append(trace_receipt)
        parents.add(experiment.native_trace_digest)
    parents.update(
        item
        for item in (
            experiment.plan_receipt_digest,
            experiment.execution_work_digest,
            experiment.raw_execution_digest,
        )
        if item is not None
    )
    result_receipt = _verify_semantic_artifact(
        resolver=resolver,
        artifact_digest=experiment.artifact_digest,
        artifact_kind=ArtifactKind.EXPERIMENT_RESULT,
        subject_id=experiment.experiment_id,
        source_kind=adapter.evidence_source_kind,
        expected_parents=tuple(parents),
        expected_payload=experiment_evidence_payload(experiment),
        fact_names=_EXPERIMENT_FACT_NAMES,
    )
    if receipts and any(not item.passed for item in receipts):
        trace_reasons = tuple(
            sorted(
                {
                    f"native_trace:{reason}"
                    for item in receipts
                    if not item.passed
                    for reason in item.reason_codes
                }
            )
        )
        result_receipt = ArtifactVerificationReceipt(
            receipt_id=result_receipt.receipt_id,
            artifact_digest=result_receipt.artifact_digest,
            artifact_kind=result_receipt.artifact_kind,
            subject_id=result_receipt.subject_id,
            producer_id=result_receipt.producer_id,
            producer_version=result_receipt.producer_version,
            parent_digests=result_receipt.parent_digests,
            verified_facts=result_receipt.verified_facts,
            passed=False,
            reason_codes=tuple(sorted(set((*result_receipt.reason_codes, *trace_reasons)))),
        )
    receipts.append(result_receipt)
    return tuple(receipts)


def _verify_analysis_check_artifact(
    *,
    adapter: AdapterCapabilityManifest,
    check: AnalysisCheckEvidence,
    resolver: ArtifactResolver | None,
) -> ArtifactVerificationReceipt:
    return _verify_semantic_artifact(
        resolver=resolver,
        artifact_digest=check.result_digest,
        artifact_kind=ArtifactKind.ANALYSIS_CHECK_RESULT,
        subject_id=check.check_id,
        source_kind=adapter.evidence_source_kind,
        expected_parents=(
            check.target_evidence_digest,
            check.question_digest,
            check.adapter_capability_digest,
            check.baseline_experiment_digest,
            check.intervention_experiment_digest,
        ),
        expected_payload=analysis_check_result_payload(check),
        fact_names=("completed", "passed"),
    )


def _verify_condition_artifact(
    *,
    adapter: AdapterCapabilityManifest,
    comparison: ConditionComparison,
    resolver: ArtifactResolver | None,
) -> ArtifactVerificationReceipt:
    return _verify_semantic_artifact(
        resolver=resolver,
        artifact_digest=comparison.result_digest,
        artifact_kind=ArtifactKind.CONDITION_ANALYSIS_RESULT,
        subject_id=comparison.condition_id,
        source_kind=adapter.evidence_source_kind,
        expected_parents=(
            comparison.condition_spec_digest,
            comparison.question_digest,
            comparison.adapter_capability_digest,
            comparison.context_digest,
            comparison.baseline_experiment_digest,
            comparison.intervention_experiment_digest,
            *(
                item
                for item in (
                    comparison.plan_receipt_digest,
                    comparison.execution_work_digest,
                    comparison.raw_execution_digest,
                )
                if item is not None
            ),
        ),
        expected_payload=condition_analysis_result_payload(comparison),
        fact_names=("reconciled",),
    )


def _experiment_binding_reasons(
    adapter: AdapterCapabilityManifest,
    question: AttributionQuestion,
    experiment: ExperimentManifest,
    expected_role: ExperimentRole,
    resolver: ArtifactResolver | None,
) -> tuple[list[str], tuple[ArtifactVerificationReceipt, ...]]:
    reasons: list[str] = []
    receipts = _verify_experiment_artifact(
        adapter=adapter,
        experiment=experiment,
        resolver=resolver,
    )
    result_receipt = receipts[-1]
    if not result_receipt.passed:
        reasons.extend(f"experiment_evidence:{item}" for item in result_receipt.reason_codes)
    facts = result_receipt.verified_facts if result_receipt.passed else {}
    if experiment.schema_version != ATTRIBUTION_SCHEMA_VERSION:
        reasons.append("experiment_schema_mismatch")
    if experiment.role is not expected_role:
        reasons.append(f"{expected_role.value}_role_mismatch")
    if experiment.question_id != question.question_id:
        reasons.append("question_id_mismatch")
    if experiment.question_digest != question.digest:
        reasons.append("question_digest_mismatch")
    if experiment.strategy_id != adapter.strategy_id:
        reasons.append("strategy_id_mismatch")
    if experiment.strategy_version != adapter.strategy_version:
        reasons.append("strategy_version_mismatch")
    if experiment.adapter_id != adapter.adapter_id:
        reasons.append("adapter_id_mismatch")
    if experiment.adapter_version != adapter.adapter_version:
        reasons.append("adapter_version_mismatch")
    if experiment.capability_manifest_version != adapter.capability_manifest_version:
        reasons.append("capability_manifest_version_mismatch")
    if experiment.capability_manifest_digest != adapter.digest:
        reasons.append("capability_manifest_digest_mismatch")
    if experiment.native_topology_digest != adapter.native_topology_digest:
        reasons.append("native_topology_digest_mismatch")
    if facts.get("capital_constrained") is not True:
        reasons.append("not_capital_constrained")
    if facts.get("reproduced") is not True:
        reasons.append("experiment_not_reproduced")
    if facts.get("reconciled") is not True:
        reasons.append("experiment_not_reconciled")
    if facts.get("pit_validated") is not True:
        reasons.append("pit_validation_failed")
    if facts.get("partition_authorized") is not True:
        reasons.append("partition_not_authorized")
    if (
        not question.bound_before_outcomes
        or facts.get("question_bound_before_outcomes") is not True
    ):
        reasons.append("question_not_predeclared")
    expected_metrics = set(question.specs_by_id)
    if set(experiment.metrics) != expected_metrics:
        reasons.append("metric_set_mismatch")
    expected_metric_spec_digests = {item.metric_id: item.digest for item in question.metric_specs}
    if dict(experiment.metric_spec_digests) != expected_metric_spec_digests:
        reasons.append("metric_definition_digest_mismatch")
    if AttributionCapability.DECISION_TRACE in question.required_capabilities:
        if experiment.native_trace_digest is None:
            reasons.append("decision_trace_missing")
        if facts.get("decision_trace_reconciled") is not True:
            reasons.append("decision_trace_not_reconciled")
    if AttributionCapability.OPPORTUNITY_TRACE in question.required_capabilities:
        if experiment.native_trace_digest is None:
            reasons.append("opportunity_trace_missing")
        if facts.get("opportunity_trace_reconciled") is not True:
            reasons.append("opportunity_trace_not_reconciled")
    return reasons, receipts


def _comparison_reasons(
    adapter: AdapterCapabilityManifest,
    question: AttributionQuestion,
    baseline: ExperimentManifest,
    intervention: ExperimentManifest,
    resolver: ArtifactResolver | None,
) -> tuple[
    ClaimStatus,
    tuple[str, ...],
    tuple[ValidationFlag, ...],
    tuple[ArtifactVerificationReceipt, ...],
]:
    flags: list[ValidationFlag] = []
    invalid: list[str] = []
    missing_capabilities = sorted(
        capability.value
        for capability in question.required_capabilities
        if not adapter.has(capability)
    )
    if missing_capabilities:
        return (
            ClaimStatus.UNAVAILABLE,
            tuple(f"missing_capability:{item}" for item in missing_capabilities),
            (),
            (),
        )
    component = adapter.component(question.subject_component_id)
    if component is None:
        invalid.append("unknown_subject_component")
    elif adapter.has(AttributionCapability.LEGAL_INTERVENTION):
        intervention_spec = component.intervention(question.intervention_id)
        if intervention_spec is None:
            invalid.append("undeclared_intervention")
        elif intervention_spec.digest != question.intervention_spec_digest:
            invalid.append("intervention_spec_digest_mismatch")

    baseline_reasons, baseline_receipts = _experiment_binding_reasons(
        adapter, question, baseline, ExperimentRole.BASELINE, resolver
    )
    invalid.extend(baseline_reasons)
    if not baseline_reasons and adapter.has(AttributionCapability.BASELINE_REPLAY):
        flags.append(ValidationFlag.BASELINE_REPRODUCED)

    intervention_reasons, intervention_receipts = _experiment_binding_reasons(
        adapter, question, intervention, ExperimentRole.INTERVENTION, resolver
    )
    invalid.extend(intervention_reasons)
    if baseline.context_digest != intervention.context_digest:
        invalid.append("comparison_context_mismatch")
    if dict(baseline.context_bindings) != dict(intervention.context_bindings):
        invalid.append("comparison_context_binding_mismatch")
    if (baseline.period_start, baseline.period_end) != (
        intervention.period_start,
        intervention.period_end,
    ):
        invalid.append("comparison_period_mismatch")
    if intervention.intervention_ids != (question.intervention_id,):
        invalid.append("intervention_identity_mismatch")
    if intervention.changed_component_ids != (question.subject_component_id,):
        invalid.append("undeclared_component_change")
    core_capabilities_present = all(
        adapter.has(item)
        for item in (
            AttributionCapability.BASELINE_REPLAY,
            AttributionCapability.LEGAL_INTERVENTION,
        )
    )
    if not invalid and core_capabilities_present:
        flags.append(ValidationFlag.PAIRED_COMPARABLE)
    reasons = sorted(set(invalid))
    if invalid:
        return (
            ClaimStatus.INVALID,
            tuple(reasons),
            tuple(flags),
            (*baseline_receipts, *intervention_receipts),
        )
    return ClaimStatus.VALID, (), tuple(flags), (*baseline_receipts, *intervention_receipts)


def _validate_condition_evidence(
    *,
    adapter: AdapterCapabilityManifest,
    question: AttributionQuestion,
    baseline: ExperimentManifest,
    intervention: ExperimentManifest,
    comparisons: Sequence[ConditionComparison],
    resolver: ArtifactResolver | None,
) -> tuple[
    tuple[ConditionEffect, ...],
    tuple[str, ...],
    bool,
    tuple[str, ...],
    tuple[ArtifactVerificationReceipt, ...],
]:
    declared = {item.condition_id: item for item in question.condition_specs}
    supplied: dict[str, ConditionComparison] = {}
    reasons: list[str] = []
    evidence_digests: list[str] = []
    receipts: list[ArtifactVerificationReceipt] = []
    for comparison in comparisons:
        if not isinstance(comparison, ConditionComparison):
            reasons.append("invalid_condition_evidence")
            continue
        if comparison.condition_id in supplied:
            reasons.append(f"duplicate_condition:{comparison.condition_id}")
            continue
        supplied[comparison.condition_id] = comparison
        evidence_digests.append(comparison.digest)
    for condition_id in sorted(set(supplied).difference(declared)):
        reasons.append(f"undeclared_condition:{condition_id}")
    for condition_id in sorted(set(declared).difference(supplied)):
        reasons.append(f"missing_required_condition:{condition_id}")

    expected_metrics = set(question.specs_by_id)
    results: list[ConditionEffect] = []
    for condition_id in sorted(set(declared).intersection(supplied)):
        spec = declared[condition_id]
        comparison = supplied[condition_id]
        item_reasons: list[str] = []
        receipt = _verify_condition_artifact(
            adapter=adapter,
            comparison=comparison,
            resolver=resolver,
        )
        receipts.append(receipt)
        if not receipt.passed:
            item_reasons.extend(f"condition_evidence:{reason}" for reason in receipt.reason_codes)
        facts = receipt.verified_facts if receipt.passed else {}
        if comparison.condition_spec_digest != spec.digest:
            item_reasons.append("condition_spec_digest_mismatch")
        if comparison.label != spec.label:
            item_reasons.append("condition_label_mismatch")
        if comparison.question_digest != question.digest:
            item_reasons.append("condition_question_mismatch")
        if comparison.adapter_capability_digest != adapter.digest:
            item_reasons.append("condition_adapter_mismatch")
        if comparison.context_digest != baseline.context_digest:
            item_reasons.append("condition_context_mismatch")
        if (comparison.period_start, comparison.period_end) != (
            baseline.period_start,
            baseline.period_end,
        ):
            item_reasons.append("condition_period_mismatch")
        if comparison.baseline_experiment_digest != baseline.digest:
            item_reasons.append("condition_baseline_mismatch")
        if comparison.intervention_experiment_digest != intervention.digest:
            item_reasons.append("condition_intervention_mismatch")
        if facts.get("reconciled") is not True:
            item_reasons.append("condition_not_reconciled")
        if set(comparison.baseline_metrics) != expected_metrics:
            item_reasons.append("condition_metric_mismatch")
        if set(comparison.intervention_metrics) != expected_metrics:
            item_reasons.append("condition_metric_mismatch")
        if item_reasons:
            reasons.extend(f"{item}:{condition_id}" for item in item_reasons)
            continue
        results.append(
            ConditionEffect(
                condition_id=condition_id,
                condition_spec_digest=spec.digest,
                evidence_digest=comparison.digest,
                label=comparison.label,
                sample_count=comparison.sample_count,
                metric_effects=_metric_effects(
                    question.metric_specs,
                    comparison.baseline_metrics,
                    comparison.intervention_metrics,
                ),
            )
        )
    passed = bool(declared) and not reasons and set(declared) == set(supplied)
    return (
        tuple(results) if not reasons else (),
        tuple(sorted(set(reasons))),
        passed,
        tuple(
            sorted(
                {
                    *evidence_digests,
                    *(receipt.digest for receipt in receipts),
                }
            )
        ),
        tuple(sorted(receipts, key=lambda item: item.digest)),
    )


def _interaction_effects(
    *,
    question: AttributionQuestion,
    spec: InteractionSpec,
    baseline: ExperimentManifest,
    subject: ExperimentManifest,
    comparison: InteractionComparison,
) -> tuple[InteractionEffect, ...]:
    partner = comparison.partner_intervention
    joint = comparison.joint_intervention
    effects: list[InteractionEffect] = []
    for metric_spec in question.metric_specs:
        values = (
            baseline.metrics[metric_spec.metric_id],
            subject.metrics[metric_spec.metric_id],
            partner.metrics[metric_spec.metric_id],
            joint.metrics[metric_spec.metric_id],
        )
        with _decimal_context(*values, metric_spec.materiality):
            raw = values[0] - values[1] - values[2] + values[3]
            if metric_spec.preference is MetricPreference.HIGHER:
                oriented = raw
            elif metric_spec.preference is MetricPreference.LOWER:
                oriented = -raw
            else:
                oriented = None
        effects.append(
            InteractionEffect(
                interaction_id=spec.interaction_id,
                interaction_spec_digest=spec.digest,
                partner_component_id=spec.partner_component_id,
                partner_experiment_id=partner.experiment_id,
                partner_experiment_digest=partner.digest,
                joint_experiment_id=joint.experiment_id,
                joint_experiment_digest=joint.digest,
                metric_id=metric_spec.metric_id,
                label=metric_spec.label,
                raw_interaction=raw,
                oriented_interaction=oriented,
                material=(oriented is not None and oriented.copy_abs() > metric_spec.materiality),
                raw_display=_display_delta(metric_spec, raw) or "0",
                oriented_display=_display_delta(metric_spec, oriented),
            )
        )
    return tuple(effects)


def _validate_interaction_evidence(
    *,
    adapter: AdapterCapabilityManifest,
    question: AttributionQuestion,
    baseline: ExperimentManifest,
    subject: ExperimentManifest,
    comparisons: Sequence[InteractionComparison],
    resolver: ArtifactResolver | None,
) -> tuple[
    tuple[InteractionEffect, ...],
    tuple[str, ...],
    bool,
    tuple[str, ...],
    tuple[ArtifactVerificationReceipt, ...],
]:
    declared = {item.interaction_id: item for item in question.interaction_specs}
    supplied: dict[str, InteractionComparison] = {}
    reasons: list[str] = []
    evidence_digests: list[str] = []
    receipts: list[ArtifactVerificationReceipt] = []
    for comparison in comparisons:
        if not isinstance(comparison, InteractionComparison):
            reasons.append("invalid_interaction_evidence")
            continue
        if comparison.interaction_id in supplied:
            reasons.append(f"duplicate_interaction:{comparison.interaction_id}")
            continue
        supplied[comparison.interaction_id] = comparison
        evidence_digests.extend(
            (
                comparison.digest,
                comparison.partner_intervention.digest,
                comparison.joint_intervention.digest,
            )
        )
    for interaction_id in sorted(set(supplied).difference(declared)):
        reasons.append(f"undeclared_interaction:{interaction_id}")
    for interaction_id in sorted(set(declared).difference(supplied)):
        reasons.append(f"missing_required_interaction:{interaction_id}")

    expected_metrics = set(question.specs_by_id)
    results: list[InteractionEffect] = []
    for interaction_id in sorted(set(declared).intersection(supplied)):
        spec = declared[interaction_id]
        comparison = supplied[interaction_id]
        partner = comparison.partner_intervention
        joint = comparison.joint_intervention
        item_reasons: list[str] = []
        if comparison.interaction_spec_digest != spec.digest:
            item_reasons.append("interaction_spec_digest_mismatch")
        if comparison.baseline_experiment_digest != baseline.digest:
            item_reasons.append("interaction_baseline_mismatch")
        if comparison.subject_experiment_digest != subject.digest:
            item_reasons.append("interaction_subject_mismatch")
        if comparison.partner_component_id != spec.partner_component_id:
            item_reasons.append("interaction_partner_mismatch")
        component_by_intervention = {
            question.intervention_id: question.subject_component_id,
            spec.partner_intervention_id: spec.partner_component_id,
        }
        expected_joint_components = tuple(
            component_by_intervention[item] for item in spec.composition_order
        )
        joint_contract = adapter.joint_intervention_by_digest(spec.joint_intervention_spec_digest)
        if joint_contract is None:
            item_reasons.append("undeclared_joint_intervention")
        else:
            if joint_contract.intervention_ids != spec.composition_order:
                item_reasons.append("joint_contract_order_mismatch")
            if joint_contract.component_ids != expected_joint_components:
                item_reasons.append("joint_contract_component_mismatch")
            if joint_contract.compatibility_semantics != spec.compatibility_semantics:
                item_reasons.append("joint_contract_compatibility_mismatch")
            if joint_contract.conflict_rule != spec.conflict_rule:
                item_reasons.append("joint_contract_conflict_rule_mismatch")
        partner_component = adapter.component(spec.partner_component_id)
        if partner_component is None:
            item_reasons.append("unknown_interaction_partner")
        else:
            partner_spec = partner_component.intervention(spec.partner_intervention_id)
            if partner_spec is None:
                item_reasons.append("undeclared_partner_intervention")
            elif partner_spec.digest != spec.partner_intervention_spec_digest:
                item_reasons.append("partner_intervention_spec_digest_mismatch")
        partner_reasons, partner_receipts = _experiment_binding_reasons(
            adapter,
            question,
            partner,
            ExperimentRole.INTERVENTION,
            resolver,
        )
        joint_reasons, joint_receipts = _experiment_binding_reasons(
            adapter,
            question,
            joint,
            ExperimentRole.JOINT_INTERVENTION,
            resolver,
        )
        receipts.extend((*partner_receipts, *joint_receipts))
        item_reasons.extend(f"partner_{item}" for item in partner_reasons)
        item_reasons.extend(f"joint_{item}" for item in joint_reasons)
        if partner.context_digest != baseline.context_digest:
            item_reasons.append("interaction_context_mismatch")
        if joint.context_digest != baseline.context_digest:
            item_reasons.append("interaction_context_mismatch")
        if (partner.period_start, partner.period_end) != (
            baseline.period_start,
            baseline.period_end,
        ):
            item_reasons.append("interaction_period_mismatch")
        if (joint.period_start, joint.period_end) != (
            baseline.period_start,
            baseline.period_end,
        ):
            item_reasons.append("interaction_period_mismatch")
        if partner.intervention_ids != (spec.partner_intervention_id,):
            item_reasons.append("partner_intervention_identity_mismatch")
        if partner.changed_component_ids != (spec.partner_component_id,):
            item_reasons.append("partner_component_mismatch")
        if joint.interaction_spec_digest != spec.digest:
            item_reasons.append("joint_interaction_spec_mismatch")
        if joint.intervention_ids != spec.composition_order:
            item_reasons.append("interaction_composition_order_mismatch")
        if joint.changed_component_ids != expected_joint_components:
            item_reasons.append("joint_component_order_mismatch")
        if any(
            set(item.metrics) != expected_metrics for item in (baseline, subject, partner, joint)
        ):
            item_reasons.append("interaction_metric_mismatch")
        if item_reasons:
            reasons.extend(f"{item}:{interaction_id}" for item in item_reasons)
            continue
        results.extend(
            _interaction_effects(
                question=question,
                spec=spec,
                baseline=baseline,
                subject=subject,
                comparison=comparison,
            )
        )
    passed = bool(declared) and not reasons and set(declared) == set(supplied)
    return (
        tuple(results) if not reasons else (),
        tuple(sorted(set(reasons))),
        passed,
        tuple(
            sorted(
                {
                    *evidence_digests,
                    *(receipt.digest for receipt in receipts),
                }
            )
        ),
        tuple(
            sorted(
                {receipt.digest: receipt for receipt in receipts}.values(),
                key=lambda item: item.digest,
            )
        ),
    )


def _validate_analysis_checks(
    *,
    adapter: AdapterCapabilityManifest,
    question: AttributionQuestion,
    baseline: ExperimentManifest,
    intervention: ExperimentManifest,
    checks: Sequence[AnalysisCheckEvidence],
    resolver: ArtifactResolver | None,
) -> tuple[
    tuple[str, ...],
    tuple[ValidationFlag, ...],
    str | None,
    tuple[str, ...],
    tuple[ArtifactVerificationReceipt, ...],
]:
    supplied: dict[AnalysisCheckKind, AnalysisCheckEvidence] = {}
    reasons: list[str] = []
    for check in checks:
        if not isinstance(check, AnalysisCheckEvidence):
            reasons.append("invalid_analysis_check")
            continue
        if check.kind in supplied:
            reasons.append(f"duplicate_analysis_check:{check.kind.value}")
            continue
        supplied[check.kind] = check
    required = {
        AnalysisCheckKind.UNCERTAINTY: question.uncertainty_required,
        AnalysisCheckKind.STABILITY: question.stability_required,
    }
    methods = {
        AnalysisCheckKind.UNCERTAINTY: question.uncertainty_method,
        AnalysisCheckKind.STABILITY: question.stability_method,
    }
    expected_metric_ids = tuple(sorted(question.specs_by_id))
    expected_target_digest = analysis_check_target_digest(
        metric_ids=expected_metric_ids,
        baseline_experiment_digest=baseline.digest,
        intervention_experiment_digest=intervention.digest,
    )
    flags: list[ValidationFlag] = []
    valid_kinds: set[AnalysisCheckKind] = set()
    receipts: list[ArtifactVerificationReceipt] = []
    for kind, is_required in required.items():
        check = supplied.get(kind)
        if is_required and check is None:
            reasons.append(f"missing_required_{kind.value}_result")
            continue
        if check is None:
            continue
        check_reasons: list[str] = []
        receipt = _verify_analysis_check_artifact(
            adapter=adapter,
            check=check,
            resolver=resolver,
        )
        receipts.append(receipt)
        if not receipt.passed:
            check_reasons.extend(
                f"{kind.value}_evidence:{reason}" for reason in receipt.reason_codes
            )
        facts = receipt.verified_facts if receipt.passed else {}
        if check.method != methods[kind]:
            check_reasons.append(f"{kind.value}_method_mismatch")
        if check.scope is not AnalysisCheckScope.OVERALL_METRIC_VECTOR:
            check_reasons.append(f"{kind.value}_scope_mismatch")
        if check.metric_ids != expected_metric_ids:
            check_reasons.append(f"{kind.value}_metric_scope_mismatch")
        if check.target_evidence_digest != expected_target_digest:
            check_reasons.append(f"{kind.value}_target_mismatch")
        if check.question_digest != question.digest:
            check_reasons.append(f"{kind.value}_question_mismatch")
        if check.adapter_capability_digest != adapter.digest:
            check_reasons.append(f"{kind.value}_adapter_mismatch")
        if check.baseline_experiment_digest != baseline.digest:
            check_reasons.append(f"{kind.value}_baseline_mismatch")
        if check.intervention_experiment_digest != intervention.digest:
            check_reasons.append(f"{kind.value}_intervention_mismatch")
        if facts.get("completed") is not True:
            check_reasons.append(f"{kind.value}_not_completed")
        if facts.get("passed") is not True:
            check_reasons.append(f"{kind.value}_not_passed")
        reasons.extend(check_reasons)
        if not check_reasons:
            valid_kinds.add(kind)
        if kind is AnalysisCheckKind.STABILITY and not check_reasons:
            flags.append(ValidationFlag.STABILITY_CHECKED)
    if question.uncertainty_required and question.condition_specs:
        reasons.append("missing_required_condition_uncertainty_scope")
    if question.uncertainty_required and question.interaction_specs:
        reasons.append("missing_required_interaction_uncertainty_scope")
    uncertainty = supplied.get(AnalysisCheckKind.UNCERTAINTY)
    uncertainty_digest = (
        uncertainty.digest
        if uncertainty is not None and AnalysisCheckKind.UNCERTAINTY in valid_kinds
        else None
    )
    return (
        tuple(sorted(set(reasons))),
        tuple(flags),
        uncertainty_digest,
        tuple(
            sorted(
                {
                    *(check.digest for check in supplied.values()),
                    *(receipt.digest for receipt in receipts),
                }
            )
        ),
        tuple(sorted(receipts, key=lambda item: item.digest)),
    )


def evaluate_attribution(
    *,
    adapter: AdapterCapabilityManifest,
    question: AttributionQuestion,
    baseline: ExperimentManifest,
    intervention: ExperimentManifest,
    conditions: Sequence[ConditionComparison] = (),
    interactions: Sequence[InteractionComparison] = (),
    analysis_checks: Sequence[AnalysisCheckEvidence] = (),
    artifact_resolver: ArtifactResolver | None = None,
) -> AttributionClaim:
    """Evaluate one predeclared intervention without inspecting strategy-native internals."""

    status, base_reason_codes, flags, experiment_receipts = _comparison_reasons(
        adapter, question, baseline, intervention, artifact_resolver
    )
    check_reasons: tuple[str, ...] = ()
    check_flags: tuple[ValidationFlag, ...] = ()
    uncertainty_digest: str | None = None
    check_digests: tuple[str, ...] = ()
    check_receipts: tuple[ArtifactVerificationReceipt, ...] = ()
    condition_results: tuple[ConditionEffect, ...] = ()
    condition_reasons: tuple[str, ...] = ()
    condition_passed = False
    condition_digests: tuple[str, ...] = ()
    condition_receipts: tuple[ArtifactVerificationReceipt, ...] = ()
    interaction_results: tuple[InteractionEffect, ...] = ()
    interaction_reasons: tuple[str, ...] = ()
    interaction_passed = False
    interaction_digests: tuple[str, ...] = ()
    interaction_receipts: tuple[ArtifactVerificationReceipt, ...] = ()
    method_reasons: tuple[str, ...] = ()
    if status is not ClaimStatus.UNAVAILABLE:
        (
            check_reasons,
            check_flags,
            uncertainty_digest,
            check_digests,
            check_receipts,
        ) = _validate_analysis_checks(
            adapter=adapter,
            question=question,
            baseline=baseline,
            intervention=intervention,
            checks=analysis_checks,
            resolver=artifact_resolver,
        )
        (
            condition_results,
            condition_reasons,
            condition_passed,
            condition_digests,
            condition_receipts,
        ) = _validate_condition_evidence(
            adapter=adapter,
            question=question,
            baseline=baseline,
            intervention=intervention,
            comparisons=conditions,
            resolver=artifact_resolver,
        )
        (
            interaction_results,
            interaction_reasons,
            interaction_passed,
            interaction_digests,
            interaction_receipts,
        ) = _validate_interaction_evidence(
            adapter=adapter,
            question=question,
            baseline=baseline,
            subject=intervention,
            comparisons=interactions,
            resolver=artifact_resolver,
        )
        method_reasons = _method_binding_reasons(question)
    evidence_reasons = (
        *method_reasons,
        *check_reasons,
        *condition_reasons,
        *interaction_reasons,
    )
    reason_codes = tuple(sorted(set((*base_reason_codes, *evidence_reasons))))
    if status is ClaimStatus.VALID and evidence_reasons:
        status = ClaimStatus.INVALID
    if condition_passed:
        flags = (*flags, ValidationFlag.CONDITION_CHECKED)
    if interaction_passed:
        flags = (*flags, ValidationFlag.INTERACTION_CHECKED)
    flags = (*flags, *check_flags)
    if (
        question.sample_role is SampleRole.INDEPENDENT_EVALUATION
        and ValidationFlag.PAIRED_COMPARABLE in flags
    ):
        flags = (*flags, ValidationFlag.INDEPENDENT_EVALUATION_COMPLETED)

    component = adapter.component(question.subject_component_id)
    subject_label = component.label if component is not None else question.subject_component_id
    effects: tuple[MetricEffect, ...] = ()
    classification = ClaimClassification.INCONCLUSIVE
    limitations: list[str] = []
    if status is ClaimStatus.VALID:
        effects = _metric_effects(
            question.metric_specs,
            baseline.metrics,
            intervention.metrics,
            uncertainty_digest,
        )
        classification = _claim_classification(effects, question.primary_metric_ids)
        if not adapter.has(AttributionCapability.DECISION_TRACE):
            limitations.append("decision trace unavailable; explanation is portfolio-level")
        if not adapter.has(AttributionCapability.OPPORTUNITY_TRACE):
            limitations.append("opportunity trace unavailable; decomposition is not identifiable")
    else:
        condition_results = ()
        interaction_results = ()
        limitations.extend(reason_codes)

    supporting_evidence = tuple(
        sorted(
            {
                *condition_digests,
                *interaction_digests,
                *check_digests,
                *(item.digest for item in experiment_receipts),
            }
        )
    )
    check_artifacts = tuple(
        item for item in analysis_checks if isinstance(item, AnalysisCheckEvidence)
    )
    verification_receipts = tuple(
        sorted(
            {
                item.digest: item
                for item in (
                    *experiment_receipts,
                    *condition_receipts,
                    *interaction_receipts,
                    *check_receipts,
                )
            }.values(),
            key=lambda item: item.digest,
        )
    )
    applied_method_ids: tuple[str, ...] = ()
    if status is ClaimStatus.VALID:
        applied = {
            question.effect_method,
            question.classification_rule,
            question.multiple_comparison_rule,
        }
        if question.condition_specs:
            applied.add(CONDITION_EFFECT_METHOD)
        if question.interaction_specs:
            applied.add(INTERACTION_EFFECT_METHOD)
        if question.uncertainty_required:
            applied.add(question.uncertainty_method)
        if question.stability_required:
            applied.add(question.stability_method)
        applied_method_ids = tuple(sorted(applied))
    claim_basis = {
        "schema_version": ATTRIBUTION_SCHEMA_VERSION,
        "question_digest": question.digest,
        "adapter_capability_digest": adapter.digest,
        "native_topology_digest": adapter.native_topology_digest,
        "context_digest": baseline.context_digest,
        "period_start": baseline.period_start,
        "period_end": baseline.period_end,
        "baseline_experiment_digest": baseline.digest,
        "intervention_experiment_digest": intervention.digest,
        "supporting_evidence_digests": supporting_evidence,
        "verification_receipt_digests": [item.digest for item in verification_receipts],
        "method_registry_version": METHOD_REGISTRY_VERSION,
        "applied_method_ids": list(applied_method_ids),
        "status": status.value,
    }
    claim_id = f"attr_{hashlib.sha256(canonical_bytes(claim_basis)).hexdigest()[:24]}"
    return AttributionClaim(
        claim_id=claim_id,
        schema_version=ATTRIBUTION_SCHEMA_VERSION,
        question_id=question.question_id,
        question_digest=question.digest,
        subject_component_id=question.subject_component_id,
        subject_label=subject_label,
        intervention_id=question.intervention_id,
        primary_metric_ids=question.primary_metric_ids,
        strategy_id=adapter.strategy_id,
        strategy_version=adapter.strategy_version,
        adapter_id=adapter.adapter_id,
        adapter_version=adapter.adapter_version,
        capability_manifest_version=adapter.capability_manifest_version,
        capability_manifest_digest=adapter.digest,
        native_topology_digest=adapter.native_topology_digest,
        context_digest=baseline.context_digest,
        period_start=baseline.period_start,
        period_end=baseline.period_end,
        status=status,
        classification=classification,
        next_action_code=question.decision_action(status, classification),
        evidence_type=EvidenceType.COUNTERFACTUAL_REPLAY,
        method_registry_version=METHOD_REGISTRY_VERSION,
        applied_method_ids=applied_method_ids,
        validation_flags=tuple(sorted(set(flags), key=lambda item: item.value)),
        reason_codes=reason_codes,
        limitations=tuple(limitations),
        metric_effects=effects,
        condition_effects=condition_results,
        interaction_effects=interaction_results,
        analysis_checks=check_artifacts,
        verification_receipts=verification_receipts,
        supporting_evidence_digests=supporting_evidence,
        baseline_experiment_id=baseline.experiment_id,
        intervention_experiment_id=intervention.experiment_id,
        baseline_experiment_digest=baseline.digest,
        intervention_experiment_digest=intervention.digest,
        baseline_artifact_digest=baseline.artifact_digest,
        intervention_artifact_digest=intervention.artifact_digest,
    )


@runtime_checkable
class StrategyAttributionAdapter(Protocol):
    """Declare strategy-owned attribution semantics without returning outcomes."""

    def capability_manifest(self) -> AdapterCapabilityManifest: ...

    def questions(self) -> Sequence[AttributionQuestion]: ...


@runtime_checkable
class PrecomputedAttributionAdapter(StrategyAttributionAdapter, Protocol):
    """Compatibility port for the synthetic workspace fixture, not Pipeline execution."""

    def artifact_resolver(self) -> ArtifactResolver: ...

    def baseline(self, question: AttributionQuestion) -> ExperimentManifest: ...

    def intervention(self, question: AttributionQuestion) -> ExperimentManifest: ...

    def condition_comparisons(
        self, question: AttributionQuestion
    ) -> Sequence[ConditionComparison]: ...

    def interaction_runs(
        self, question: AttributionQuestion
    ) -> Sequence[InteractionComparison]: ...

    def analysis_checks(self, question: AttributionQuestion) -> Sequence[AnalysisCheckEvidence]: ...


@dataclass(frozen=True)
class AttributionWorkspace:
    workspace_id: str
    title: str
    subtitle: str
    source_kind: str
    adapter: AdapterCapabilityManifest
    baseline: ExperimentManifest
    claims: tuple[AttributionClaim, ...]
    questions: tuple[AttributionQuestion, ...] = ()
    experiments: tuple[ExperimentManifest, ...] = ()
    condition_comparisons: tuple[ConditionComparison, ...] = ()
    interaction_comparisons: tuple[InteractionComparison, ...] = ()
    analysis_checks: tuple[AnalysisCheckEvidence, ...] = ()
    artifacts: tuple[SemanticArtifactEnvelope, ...] = ()
    unavailable_component_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "claims",
            "questions",
            "experiments",
            "condition_comparisons",
            "interaction_comparisons",
            "analysis_checks",
            "artifacts",
            "unavailable_component_ids",
        ):
            object.__setattr__(self, field_name, tuple(getattr(self, field_name)))
        _require_text(self.workspace_id, "workspace_id")
        _require_text(self.title, "title")
        _require_text(self.subtitle, "subtitle")
        _require_text(self.source_kind, "source_kind")
        if not isinstance(self.adapter, AdapterCapabilityManifest):
            raise AttributionError("invalid_adapter", "workspace adapter is invalid")
        if not isinstance(self.baseline, ExperimentManifest):
            raise AttributionError("invalid_baseline", "workspace baseline is invalid")
        if any(not isinstance(item, AttributionClaim) for item in self.claims):
            raise AttributionError("invalid_claim", "workspace claims are invalid")
        if self.source_kind != self.adapter.evidence_source_kind:
            raise AttributionError(
                "workspace_source_mismatch",
                "workspace source kind must be bound by the adapter manifest",
            )
        if self.baseline.role is not ExperimentRole.BASELINE:
            raise AttributionError(
                "invalid_workspace_baseline",
                "workspace baseline must have the baseline experiment role",
            )
        if self.baseline.capability_manifest_digest != self.adapter.digest:
            raise AttributionError(
                "workspace_baseline_adapter_mismatch",
                "workspace baseline must bind the workspace adapter",
            )
        if (
            self.baseline.strategy_id,
            self.baseline.strategy_version,
        ) != (
            self.adapter.strategy_id,
            self.adapter.strategy_version,
        ):
            raise AttributionError(
                "workspace_baseline_strategy_mismatch",
                "workspace baseline must bind the adapter strategy",
            )
        object.__setattr__(
            self,
            "unavailable_component_ids",
            _unique_text(self.unavailable_component_ids, "unavailable_component_ids"),
        )
        unknown = sorted(set(self.unavailable_component_ids).difference(self.adapter.component_ids))
        if unknown:
            raise AttributionError(
                "unknown_unavailable_component", f"unknown component ids: {unknown}"
            )
        if any(not isinstance(item, AttributionQuestion) for item in self.questions):
            raise AttributionError("invalid_question", "workspace questions are invalid")
        if any(not isinstance(item, ExperimentManifest) for item in self.experiments):
            raise AttributionError("invalid_experiment", "workspace experiments are invalid")
        if any(not isinstance(item, ConditionComparison) for item in self.condition_comparisons):
            raise AttributionError(
                "invalid_condition_evidence", "workspace condition evidence is invalid"
            )
        if any(
            not isinstance(item, InteractionComparison) for item in self.interaction_comparisons
        ):
            raise AttributionError(
                "invalid_interaction_evidence", "workspace interaction evidence is invalid"
            )
        if any(not isinstance(item, AnalysisCheckEvidence) for item in self.analysis_checks):
            raise AttributionError(
                "invalid_analysis_check", "workspace analysis checks are invalid"
            )
        if any(not isinstance(item, SemanticArtifactEnvelope) for item in self.artifacts):
            raise AttributionError("invalid_artifact", "workspace artifacts are invalid")
        artifact_digests = [item.digest for item in self.artifacts]
        if len(artifact_digests) != len(set(artifact_digests)):
            raise AttributionError("duplicate_artifact", "workspace artifacts must be unique")
        if any(item.source_kind != self.source_kind for item in self.artifacts):
            raise AttributionError(
                "artifact_source_mismatch", "workspace artifacts must share its source kind"
            )
        object.__setattr__(
            self,
            "artifacts",
            tuple(sorted(self.artifacts, key=lambda item: item.digest)),
        )
        artifact_resolver = InMemoryArtifactResolver(self.artifacts)
        artifact_digest_set = set(artifact_digests)
        question_ids = [item.question_id for item in self.questions]
        question_digests = [item.digest for item in self.questions]
        experiment_ids = [item.experiment_id for item in self.experiments]
        experiment_digests = [item.digest for item in self.experiments]
        claim_ids = [item.claim_id for item in self.claims]
        claim_digests = [item.digest for item in self.claims]
        if len(question_ids) != len(set(question_ids)):
            raise AttributionError("duplicate_question_id", "workspace question ids must be unique")
        if len(question_digests) != len(set(question_digests)):
            raise AttributionError(
                "duplicate_question", "workspace question digests must be unique"
            )
        if len(experiment_ids) != len(set(experiment_ids)):
            raise AttributionError(
                "duplicate_experiment_id", "workspace experiment ids must be unique"
            )
        if len(experiment_digests) != len(set(experiment_digests)):
            raise AttributionError(
                "duplicate_experiment", "workspace experiment digests must be unique"
            )
        if len(claim_ids) != len(set(claim_ids)):
            raise AttributionError("duplicate_claim_id", "workspace claim ids must be unique")
        if len(claim_digests) != len(set(claim_digests)):
            raise AttributionError(
                "duplicate_claim_digest", "workspace claim digests must be unique"
            )
        if self.claims and (not self.questions or not self.experiments):
            raise AttributionError(
                "incomplete_workspace_evidence",
                "claims require resolvable question and experiment artifacts",
            )
        questions_by_digest = {item.digest: item for item in self.questions}
        experiments_by_digest = {item.digest: item for item in self.experiments}
        question_digest_set = set(questions_by_digest)
        experiment_digest_set = set(experiments_by_digest)
        supporting_digest_set = {
            *(item.digest for item in self.condition_comparisons),
            *(item.digest for item in self.interaction_comparisons),
            *(item.digest for item in self.analysis_checks),
            *(receipt.digest for claim in self.claims for receipt in claim.verification_receipts),
            *experiment_digest_set,
        }
        referenced_artifacts = {
            *(item.artifact_digest for item in self.experiments),
            *(
                item.native_trace_digest
                for item in self.experiments
                if item.native_trace_digest is not None
            ),
            *(item.result_digest for item in self.condition_comparisons),
            *(item.result_digest for item in self.analysis_checks),
        }
        unresolved_artifacts = sorted(referenced_artifacts.difference(artifact_digest_set))
        unreferenced_artifacts = sorted(artifact_digest_set.difference(referenced_artifacts))
        if unresolved_artifacts:
            raise AttributionError(
                "unresolved_workspace_artifact",
                f"workspace artifacts are unresolved: {unresolved_artifacts}",
            )
        if unreferenced_artifacts:
            raise AttributionError(
                "unreferenced_workspace_artifact",
                f"workspace artifacts are not referenced: {unreferenced_artifacts}",
            )
        if self.experiments and self.baseline.digest not in experiment_digest_set:
            raise AttributionError(
                "unknown_workspace_baseline", "workspace baseline is not in experiments"
            )
        referenced_question_digests: set[str] = set()
        referenced_experiment_digests: set[str] = set()
        referenced_support_digests: set[str] = set()
        for claim in self.claims:
            if claim.question_digest not in question_digest_set:
                raise AttributionError(
                    "unknown_claim_question", f"claim question is unresolved: {claim.claim_id}"
                )
            if claim.baseline_experiment_digest not in experiment_digest_set:
                raise AttributionError(
                    "unknown_claim_baseline", f"claim baseline is unresolved: {claim.claim_id}"
                )
            if claim.intervention_experiment_digest not in experiment_digest_set:
                raise AttributionError(
                    "unknown_claim_intervention",
                    f"claim intervention is unresolved: {claim.claim_id}",
                )
            if claim.capability_manifest_digest != self.adapter.digest:
                raise AttributionError(
                    "claim_adapter_mismatch", f"claim adapter is unresolved: {claim.claim_id}"
                )
            unresolved_support = sorted(
                set(claim.supporting_evidence_digests).difference(supporting_digest_set)
            )
            if unresolved_support:
                raise AttributionError(
                    "unresolved_claim_evidence",
                    f"claim evidence is unresolved: {unresolved_support}",
                )
            support = set(claim.supporting_evidence_digests)
            referenced_question_digests.add(claim.question_digest)
            referenced_experiment_digests.update(
                {
                    claim.baseline_experiment_digest,
                    claim.intervention_experiment_digest,
                    *support.intersection(experiment_digest_set),
                }
            )
            referenced_support_digests.update(support)
            rebuilt = evaluate_attribution(
                adapter=self.adapter,
                question=questions_by_digest[claim.question_digest],
                baseline=experiments_by_digest[claim.baseline_experiment_digest],
                intervention=experiments_by_digest[claim.intervention_experiment_digest],
                conditions=tuple(
                    item for item in self.condition_comparisons if item.digest in support
                ),
                interactions=tuple(
                    item for item in self.interaction_comparisons if item.digest in support
                ),
                analysis_checks=tuple(
                    item for item in self.analysis_checks if item.digest in support
                ),
                artifact_resolver=artifact_resolver,
            )
            if rebuilt.digest != claim.digest:
                raise AttributionError(
                    "claim_derivation_mismatch",
                    f"claim is not the deterministic result of its evidence: {claim.claim_id}",
                )
        if self.claims:
            claim_baseline_digests = {item.baseline_experiment_digest for item in self.claims}
            if self.baseline.digest not in claim_baseline_digests:
                raise AttributionError(
                    "unreferenced_workspace_baseline",
                    "workspace baseline must be a baseline referenced by a claim",
                )
            unreferenced_questions = sorted(
                question_digest_set.difference(referenced_question_digests)
            )
            unreferenced_experiments = sorted(
                experiment_digest_set.difference(referenced_experiment_digests)
            )
            evidence_artifact_digests = {
                *(item.digest for item in self.condition_comparisons),
                *(item.digest for item in self.interaction_comparisons),
                *(item.digest for item in self.analysis_checks),
            }
            unreferenced_evidence = sorted(
                evidence_artifact_digests.difference(referenced_support_digests)
            )
            if unreferenced_questions:
                raise AttributionError(
                    "unreferenced_workspace_question",
                    f"workspace questions are not referenced: {unreferenced_questions}",
                )
            if unreferenced_experiments:
                raise AttributionError(
                    "unreferenced_workspace_experiment",
                    f"workspace experiments are not referenced: {unreferenced_experiments}",
                )
            if unreferenced_evidence:
                raise AttributionError(
                    "unreferenced_workspace_evidence",
                    f"workspace evidence is not referenced: {unreferenced_evidence}",
                )

    @property
    def digest(self) -> str:
        return digest(self._semantic_dict())

    def _semantic_dict(self) -> dict[str, Any]:
        return {
            "schema": WORKSPACE_SCHEMA,
            "workspace_id": self.workspace_id,
            "title": self.title,
            "subtitle": self.subtitle,
            "source_kind": self.source_kind,
            "adapter": self.adapter.to_dict(),
            "baseline": self.baseline.to_dict(),
            "questions": [
                item.to_dict()
                for item in sorted(self.questions, key=lambda item: (item.question_id, item.digest))
            ],
            "experiments": [
                item.to_dict()
                for item in sorted(
                    self.experiments,
                    key=lambda item: (item.experiment_id, item.digest),
                )
            ],
            "condition_comparisons": [
                item.to_dict()
                for item in sorted(self.condition_comparisons, key=lambda item: item.digest)
            ],
            "interaction_comparisons": [
                item.to_dict()
                for item in sorted(self.interaction_comparisons, key=lambda item: item.digest)
            ],
            "analysis_checks": [
                item.to_dict()
                for item in sorted(self.analysis_checks, key=lambda item: item.digest)
            ],
            "artifacts": [item.to_dict() for item in self.artifacts],
            "claims": [
                item.to_dict()
                for item in sorted(self.claims, key=lambda item: (item.claim_id, item.digest))
            ],
            "unavailable_component_ids": sorted(self.unavailable_component_ids),
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._semantic_dict()
        payload["digest"] = self.digest
        return payload

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


def evaluate_adapter(adapter: PrecomputedAttributionAdapter) -> tuple[AttributionClaim, ...]:
    """Evaluate the legacy precomputed fixture; production execution uses the Pipeline."""

    manifest = adapter.capability_manifest()
    resolver = adapter.artifact_resolver()
    claims = [
        evaluate_attribution(
            adapter=manifest,
            question=question,
            baseline=adapter.baseline(question),
            intervention=adapter.intervention(question),
            conditions=adapter.condition_comparisons(question),
            interactions=adapter.interaction_runs(question),
            analysis_checks=adapter.analysis_checks(question),
            artifact_resolver=resolver,
        )
        for question in adapter.questions()
    ]
    return tuple(sorted(claims, key=lambda item: item.claim_id))

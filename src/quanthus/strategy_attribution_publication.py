"""Atomic publication of fully evaluated strategy-attribution runs."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any

from quanthus.strategy_attribution import (
    WORKSPACE_SCHEMA,
    AdapterCapabilityManifest,
    AnalysisRole,
    AttributionCapability,
    AttributionError,
    AttributionQuestion,
    AttributionWorkspace,
    ComponentDescriptor,
    ConditionSpec,
    ExperimentRole,
    InteractionSpec,
    InterventionSpec,
    JointInterventionSpec,
    MetricDisplay,
    MetricPreference,
    MetricSpec,
    SampleRole,
    canonical_bytes,
    digest,
)
from quanthus.strategy_attribution_pipeline import (
    SYNTHETIC_SOURCE_KIND,
    AttributionPipeline,
    AttributionPipelineResult,
    EvaluationBundle,
    PipelineRunState,
    PipelineStageReceipt,
)
from quanthus.strategy_attribution_planning import (
    AttributionRunRequest,
    FrozenAdapterBinding,
    OutcomeAccessPolicy,
)

PUBLICATION_SCHEMA = "quanthus.strategy-attribution-publication.v1"
TRANSPORT_SCHEMA = "quanthus.strategy-attribution-publication-transport.v1"
PUBLICATION_CATALOG_SCHEMA = "quanthus.strategy-attribution-publication-catalog.v1"

_RELEASE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_RESERVED_RELEASE_IDS = frozenset({"catalog.json"})
_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
_HEX_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_PIPELINE_ROOT_FILES = frozenset(
    {"request.json", "plan.json", "executor-binding.json"}
)


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AttributionError("invalid_publication", f"{field_name} must not be empty")
    return value


def _require_digest(value: object, field_name: str) -> str:
    text = _require_text(value, field_name)
    if _DIGEST_PATTERN.fullmatch(text) is None:
        raise AttributionError(
            "invalid_publication_digest",
            f"{field_name} must be a lowercase sha256 digest",
        )
    return text


def _require_release_id(value: object) -> str:
    text = _require_text(value, "release_id")
    if (
        _RELEASE_ID_PATTERN.fullmatch(text) is None
        or text in {".", ".."}
        or text in _RESERVED_RELEASE_IDS
    ):
        raise AttributionError(
            "invalid_release_id",
            "release id must be one conservative path segment",
        )
    return text


def _sha256_bytes(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _parse_object(payload: bytes, reason_code: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AttributionError(reason_code, "payload is not valid JSON") from exc
    if not isinstance(value, dict):
        raise AttributionError(reason_code, "payload must be a JSON object")
    return value


def _require_fields(
    value: Mapping[str, Any], expected: set[str], reason_code: str
) -> None:
    if set(value) != expected:
        raise AttributionError(reason_code, "payload fields are incomplete or unexpected")


def _mapping(value: object, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AttributionError("invalid_publication", f"{field_name} must be an object")
    return value


def _sequence(value: object, field_name: str) -> list[Any]:
    if not isinstance(value, list):
        raise AttributionError("invalid_publication", f"{field_name} must be an array")
    return value


def _checked_digest(raw: Mapping[str, Any], value: object, field_name: str) -> None:
    expected = getattr(value, "digest", None)
    if raw.get("digest") != expected:
        raise AttributionError(
            "publication_semantic_digest_mismatch",
            f"{field_name} digest is invalid",
        )


def _parse_intervention(raw_value: object) -> InterventionSpec:
    raw = _mapping(raw_value, "intervention")
    _require_fields(
        raw,
        {
            "intervention_id",
            "intervention_version",
            "operation",
            "fallback_semantics",
            "digest",
        },
        "intervention_field_mismatch",
    )
    value = InterventionSpec(
        intervention_id=raw["intervention_id"],
        intervention_version=raw["intervention_version"],
        operation=raw["operation"],
        fallback_semantics=raw["fallback_semantics"],
    )
    _checked_digest(raw, value, "intervention")
    return value


def _parse_joint_intervention(raw_value: object) -> JointInterventionSpec:
    raw = _mapping(raw_value, "joint_intervention")
    _require_fields(
        raw,
        {
            "joint_spec_id",
            "joint_spec_version",
            "intervention_ids",
            "component_ids",
            "intervention_spec_digests",
            "compatibility_semantics",
            "conflict_rule",
            "digest",
        },
        "joint_intervention_field_mismatch",
    )
    value = JointInterventionSpec(
        joint_spec_id=raw["joint_spec_id"],
        joint_spec_version=raw["joint_spec_version"],
        intervention_ids=tuple(_sequence(raw["intervention_ids"], "intervention_ids")),
        component_ids=tuple(_sequence(raw["component_ids"], "component_ids")),
        intervention_spec_digests=tuple(
            _sequence(raw["intervention_spec_digests"], "intervention_spec_digests")
        ),
        compatibility_semantics=raw["compatibility_semantics"],
        conflict_rule=raw["conflict_rule"],
    )
    _checked_digest(raw, value, "joint_intervention")
    return value


def _parse_component(raw_value: object) -> ComponentDescriptor:
    raw = _mapping(raw_value, "component")
    _require_fields(
        raw,
        {
            "component_id",
            "component_version",
            "label",
            "kind",
            "role",
            "description",
            "depends_on",
            "legal_interventions",
            "digest",
        },
        "component_field_mismatch",
    )
    value = ComponentDescriptor(
        component_id=raw["component_id"],
        component_version=raw["component_version"],
        label=raw["label"],
        kind=raw["kind"],
        role=raw["role"],
        description=raw["description"],
        depends_on=tuple(_sequence(raw["depends_on"], "depends_on")),
        legal_interventions=tuple(
            _parse_intervention(item)
            for item in _sequence(raw["legal_interventions"], "legal_interventions")
        ),
    )
    _checked_digest(raw, value, "component")
    return value


def _parse_adapter_manifest(raw_value: object) -> AdapterCapabilityManifest:
    raw = _mapping(raw_value, "adapter_manifest")
    _require_fields(
        raw,
        {
            "adapter_id",
            "adapter_version",
            "capability_manifest_version",
            "strategy_id",
            "strategy_version",
            "evidence_source_kind",
            "native_topology_digest",
            "capabilities",
            "components",
            "legal_joint_interventions",
            "digest",
        },
        "adapter_manifest_field_mismatch",
    )
    try:
        value = AdapterCapabilityManifest(
            adapter_id=raw["adapter_id"],
            adapter_version=raw["adapter_version"],
            capability_manifest_version=raw["capability_manifest_version"],
            strategy_id=raw["strategy_id"],
            strategy_version=raw["strategy_version"],
            evidence_source_kind=raw["evidence_source_kind"],
            native_topology_digest=raw["native_topology_digest"],
            capabilities=tuple(
                AttributionCapability(item)
                for item in _sequence(raw["capabilities"], "capabilities")
            ),
            components=tuple(
                _parse_component(item)
                for item in _sequence(raw["components"], "components")
            ),
            legal_joint_interventions=tuple(
                _parse_joint_intervention(item)
                for item in _sequence(
                    raw["legal_joint_interventions"], "legal_joint_interventions"
                )
            ),
        )
    except (TypeError, ValueError) as exc:
        if isinstance(exc, AttributionError):
            raise
        raise AttributionError("invalid_adapter_manifest", "adapter values are invalid") from exc
    _checked_digest(raw, value, "adapter_manifest")
    return value


def _decimal(value: object, field_name: str, *, optional: bool = False) -> Decimal | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise AttributionError("invalid_publication", f"{field_name} must be decimal text")
    try:
        return Decimal(value)
    except ValueError as exc:
        raise AttributionError("invalid_publication", f"{field_name} is invalid") from exc


def _parse_metric(raw_value: object) -> MetricSpec:
    raw = _mapping(raw_value, "metric")
    _require_fields(
        raw,
        {
            "metric_id",
            "label",
            "unit",
            "definition_version",
            "definition_digest",
            "preference",
            "materiality",
            "display",
            "display_decimals",
            "target_min",
            "target_max",
            "digest",
        },
        "metric_field_mismatch",
    )
    try:
        value = MetricSpec(
            metric_id=raw["metric_id"],
            label=raw["label"],
            unit=raw["unit"],
            definition_version=raw["definition_version"],
            definition_digest=raw["definition_digest"],
            preference=MetricPreference(raw["preference"]),
            materiality=_decimal(raw["materiality"], "materiality"),
            display=MetricDisplay(raw["display"]),
            display_decimals=raw["display_decimals"],
            target_min=_decimal(raw["target_min"], "target_min", optional=True),
            target_max=_decimal(raw["target_max"], "target_max", optional=True),
        )
    except (TypeError, ValueError) as exc:
        if isinstance(exc, AttributionError):
            raise
        raise AttributionError("invalid_metric", "metric values are invalid") from exc
    _checked_digest(raw, value, "metric")
    return value


def _parse_condition(raw_value: object) -> ConditionSpec:
    raw = _mapping(raw_value, "condition")
    _require_fields(
        raw,
        {"condition_id", "label", "definition_version", "definition_digest", "digest"},
        "condition_field_mismatch",
    )
    value = ConditionSpec(
        condition_id=raw["condition_id"],
        label=raw["label"],
        definition_version=raw["definition_version"],
        definition_digest=raw["definition_digest"],
    )
    _checked_digest(raw, value, "condition")
    return value


def _parse_interaction(raw_value: object) -> InteractionSpec:
    raw = _mapping(raw_value, "interaction")
    _require_fields(
        raw,
        {
            "interaction_id",
            "partner_component_id",
            "partner_intervention_id",
            "partner_intervention_spec_digest",
            "joint_intervention_spec_digest",
            "composition_order",
            "compatibility_semantics",
            "conflict_rule",
            "digest",
        },
        "interaction_field_mismatch",
    )
    value = InteractionSpec(
        interaction_id=raw["interaction_id"],
        partner_component_id=raw["partner_component_id"],
        partner_intervention_id=raw["partner_intervention_id"],
        partner_intervention_spec_digest=raw["partner_intervention_spec_digest"],
        joint_intervention_spec_digest=raw["joint_intervention_spec_digest"],
        composition_order=tuple(_sequence(raw["composition_order"], "composition_order")),
        compatibility_semantics=raw["compatibility_semantics"],
        conflict_rule=raw["conflict_rule"],
    )
    _checked_digest(raw, value, "interaction")
    return value


def _parse_question(raw_value: object) -> AttributionQuestion:
    raw = _mapping(raw_value, "question")
    _require_fields(
        raw,
        {
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
            "sample_role",
            "analysis_role",
            "multiple_comparison_rule",
            "classification_rule",
            "decision_rule",
            "decision_actions",
            "uncertainty_required",
            "uncertainty_method",
            "stability_required",
            "stability_method",
            "bound_before_outcomes",
            "primary_metric_ids",
            "metric_specs",
            "required_capabilities",
            "condition_specs",
            "interaction_specs",
            "known_assumptions",
            "exclusions",
            "stop_conditions",
            "digest",
        },
        "question_field_mismatch",
    )
    try:
        value = AttributionQuestion(
            question_id=raw["question_id"],
            question_version=raw["question_version"],
            subject_component_id=raw["subject_component_id"],
            intervention_id=raw["intervention_id"],
            intervention_spec_digest=raw["intervention_spec_digest"],
            hypothesis_family_id=raw["hypothesis_family_id"],
            estimand_kind=raw["estimand_kind"],
            effect_method=raw["effect_method"],
            intervention_semantics=raw["intervention_semantics"],
            target_population=raw["target_population"],
            sample_role=SampleRole(raw["sample_role"]),
            analysis_role=AnalysisRole(raw["analysis_role"]),
            multiple_comparison_rule=raw["multiple_comparison_rule"],
            classification_rule=raw["classification_rule"],
            decision_rule=raw["decision_rule"],
            decision_actions=_mapping(raw["decision_actions"], "decision_actions"),
            uncertainty_required=raw["uncertainty_required"],
            uncertainty_method=raw["uncertainty_method"],
            stability_required=raw["stability_required"],
            stability_method=raw["stability_method"],
            bound_before_outcomes=raw["bound_before_outcomes"],
            primary_metric_ids=tuple(
                _sequence(raw["primary_metric_ids"], "primary_metric_ids")
            ),
            metric_specs=tuple(
                _parse_metric(item)
                for item in _sequence(raw["metric_specs"], "metric_specs")
            ),
            required_capabilities=tuple(
                AttributionCapability(item)
                for item in _sequence(
                    raw["required_capabilities"], "required_capabilities"
                )
            ),
            condition_specs=tuple(
                _parse_condition(item)
                for item in _sequence(raw["condition_specs"], "condition_specs")
            ),
            interaction_specs=tuple(
                _parse_interaction(item)
                for item in _sequence(raw["interaction_specs"], "interaction_specs")
            ),
            known_assumptions=tuple(
                _sequence(raw["known_assumptions"], "known_assumptions")
            ),
            exclusions=tuple(_sequence(raw["exclusions"], "exclusions")),
            stop_conditions=tuple(
                _sequence(raw["stop_conditions"], "stop_conditions")
            ),
        )
    except (TypeError, ValueError) as exc:
        if isinstance(exc, AttributionError):
            raise
        raise AttributionError("invalid_question", "question values are invalid") from exc
    _checked_digest(raw, value, "question")
    return value


def load_attribution_run_request(payload: bytes) -> AttributionRunRequest:
    """Parse and revalidate a self-contained canonical run request."""

    raw = _parse_object(payload, "invalid_run_request_json")
    _require_fields(
        raw,
        {
            "schema_version",
            "outcome_access_policy",
            "adapter_binding",
            "adapter_manifest",
            "questions",
            "context_digest",
            "context_bindings",
            "period_start",
            "period_end",
            "metric_calculator_id",
            "metric_calculator_version",
            "capital_constrained",
            "point_in_time_required",
            "method_registry_version",
            "request_id",
            "digest",
        },
        "run_request_field_mismatch",
    )
    manifest = _parse_adapter_manifest(raw["adapter_manifest"])
    binding_raw = _mapping(raw["adapter_binding"], "adapter_binding")
    _require_fields(
        binding_raw,
        {
            "adapter_id",
            "adapter_version",
            "capability_manifest_version",
            "capability_manifest_digest",
            "strategy_id",
            "strategy_version",
            "native_topology_digest",
        },
        "adapter_binding_field_mismatch",
    )
    binding = FrozenAdapterBinding(**binding_raw)
    try:
        request = AttributionRunRequest(
            schema_version=raw["schema_version"],
            outcome_access_policy=OutcomeAccessPolicy(raw["outcome_access_policy"]),
            adapter_binding=binding,
            adapter_manifest=manifest,
            questions=tuple(
                _parse_question(item)
                for item in _sequence(raw["questions"], "questions")
            ),
            context_bindings=_mapping(raw["context_bindings"], "context_bindings"),
            period_start=date.fromisoformat(raw["period_start"]),
            period_end=date.fromisoformat(raw["period_end"]),
            metric_calculator_id=raw["metric_calculator_id"],
            metric_calculator_version=raw["metric_calculator_version"],
            capital_constrained=raw["capital_constrained"],
            point_in_time_required=raw["point_in_time_required"],
            method_registry_version=raw["method_registry_version"],
        )
    except (TypeError, ValueError) as exc:
        if isinstance(exc, AttributionError):
            raise
        raise AttributionError("invalid_run_request_json", "request values are invalid") from exc
    if binding != FrozenAdapterBinding.from_manifest(manifest):
        raise AttributionError(
            "adapter_binding_mismatch",
            "request adapter binding does not match its manifest",
        )
    if (
        raw["context_digest"] != request.context_digest
        or raw["request_id"] != request.request_id
        or raw["digest"] != request.digest
        or request.canonical_bytes() != payload
    ):
        raise AttributionError(
            "run_request_digest_mismatch",
            "request is not its canonical deterministic representation",
        )
    return request


@dataclass(frozen=True)
class PublicationFile:
    path: str
    sha256: str
    size: int

    def __post_init__(self) -> None:
        _safe_relative_path(self.path)
        _require_digest(self.sha256, "file_sha256")
        if not isinstance(self.size, int) or isinstance(self.size, bool) or self.size < 0:
            raise AttributionError("invalid_publication_file", "file size is invalid")

    @classmethod
    def from_bytes(cls, path: str, payload: bytes) -> PublicationFile:
        return cls(path=path, sha256=_sha256_bytes(payload), size=len(payload))

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "sha256": self.sha256, "size": self.size}


@dataclass(frozen=True)
class AttributionReleaseManifest:
    release_id: str
    run_id: str
    source_kind: str
    synthetic_fixture: bool
    formal_strategy_evidence: bool
    request_digest: str
    plan_digest: str
    evaluation_bundle_digest: str
    evaluation_receipt_digest: str
    workspace_digest: str
    publishable_claim_digests: tuple[str, ...]
    display_claim_digests: tuple[str, ...]
    question_dispositions: tuple[Mapping[str, Any], ...]
    semantic_files: tuple[PublicationFile, ...]
    schema_version: str = PUBLICATION_SCHEMA

    def __post_init__(self) -> None:
        _require_release_id(self.release_id)
        for field_name in ("run_id", "source_kind", "schema_version"):
            _require_text(getattr(self, field_name), field_name)
        if self.schema_version != PUBLICATION_SCHEMA:
            raise AttributionError("publication_schema_mismatch", "schema is unsupported")
        for field_name in (
            "request_digest",
            "plan_digest",
            "evaluation_bundle_digest",
            "evaluation_receipt_digest",
            "workspace_digest",
        ):
            _require_digest(getattr(self, field_name), field_name)
        if not isinstance(self.synthetic_fixture, bool) or not isinstance(
            self.formal_strategy_evidence, bool
        ):
            raise AttributionError("invalid_publication", "source flags must be boolean")
        expected_synthetic = self.source_kind == SYNTHETIC_SOURCE_KIND
        if self.synthetic_fixture != expected_synthetic:
            raise AttributionError(
                "invalid_publication_source",
                "source kind uniquely determines the synthetic fixture flag",
            )
        if self.formal_strategy_evidence != (not expected_synthetic):
            raise AttributionError(
                "invalid_publication_source",
                "source kind uniquely determines the formal evidence flag",
            )
        publishable = _digest_sequence(
            self.publishable_claim_digests, "publishable_claim_digests"
        )
        display = _digest_sequence(self.display_claim_digests, "display_claim_digests")
        if not set(publishable).issubset(display):
            raise AttributionError(
                "publication_claim_mismatch",
                "publishable claims must be display claims",
            )
        if self.synthetic_fixture and publishable:
            raise AttributionError(
                "synthetic_claim_publication_forbidden",
                "synthetic claims cannot become formal published conclusions",
            )
        object.__setattr__(self, "publishable_claim_digests", publishable)
        object.__setattr__(self, "display_claim_digests", display)
        dispositions = tuple(
            MappingProxyType(
                json.loads(canonical_bytes(_mapping(item, "question_disposition")))
            )
            for item in self.question_dispositions
        )
        object.__setattr__(
            self,
            "question_dispositions",
            tuple(
                sorted(
                    dispositions,
                    key=lambda item: (
                        str(item.get("question_id")),
                        str(item.get("question_digest")),
                    ),
                )
            ),
        )
        files = tuple(self.semantic_files)
        if not files or any(not isinstance(item, PublicationFile) for item in files):
            raise AttributionError("missing_publication_file", "semantic files are required")
        files = tuple(sorted(files, key=lambda item: item.path))
        paths = [item.path for item in files]
        if len(paths) != len(set(paths)):
            raise AttributionError("duplicate_publication_file", "semantic file paths repeat")
        if "workspace.json" not in paths or "lineage/request.json" not in paths:
            raise AttributionError(
                "incomplete_publication",
                "workspace and request lineage are mandatory",
            )
        object.__setattr__(self, "semantic_files", files)

    @property
    def digest(self) -> str:
        return digest(self._semantic_dict())

    def _semantic_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "release_id": self.release_id,
            "run_id": self.run_id,
            "source_kind": self.source_kind,
            "synthetic_fixture": self.synthetic_fixture,
            "formal_strategy_evidence": self.formal_strategy_evidence,
            "request_digest": self.request_digest,
            "plan_digest": self.plan_digest,
            "evaluation_bundle_digest": self.evaluation_bundle_digest,
            "evaluation_receipt_digest": self.evaluation_receipt_digest,
            "workspace_digest": self.workspace_digest,
            "publishable_claim_digests": list(self.publishable_claim_digests),
            "display_claim_digests": list(self.display_claim_digests),
            "question_dispositions": [dict(item) for item in self.question_dispositions],
            "semantic_files": [item.to_dict() for item in self.semantic_files],
        }

    def to_dict(self) -> dict[str, Any]:
        value = self._semantic_dict()
        value["digest"] = self.digest
        return value

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())

    @classmethod
    def from_bytes(cls, payload: bytes) -> AttributionReleaseManifest:
        raw = _parse_object(payload, "invalid_publication_json")
        _require_fields(
            raw,
            {
                "schema_version",
                "release_id",
                "run_id",
                "source_kind",
                "synthetic_fixture",
                "formal_strategy_evidence",
                "request_digest",
                "plan_digest",
                "evaluation_bundle_digest",
                "evaluation_receipt_digest",
                "workspace_digest",
                "publishable_claim_digests",
                "display_claim_digests",
                "question_dispositions",
                "semantic_files",
                "digest",
            },
            "publication_field_mismatch",
        )
        files = []
        for item in _sequence(raw["semantic_files"], "semantic_files"):
            item_raw = _mapping(item, "semantic_file")
            _require_fields(
                item_raw,
                {"path", "sha256", "size"},
                "publication_file_field_mismatch",
            )
            files.append(PublicationFile(**item_raw))
        value = cls(
            schema_version=raw["schema_version"],
            release_id=raw["release_id"],
            run_id=raw["run_id"],
            source_kind=raw["source_kind"],
            synthetic_fixture=raw["synthetic_fixture"],
            formal_strategy_evidence=raw["formal_strategy_evidence"],
            request_digest=raw["request_digest"],
            plan_digest=raw["plan_digest"],
            evaluation_bundle_digest=raw["evaluation_bundle_digest"],
            evaluation_receipt_digest=raw["evaluation_receipt_digest"],
            workspace_digest=raw["workspace_digest"],
            publishable_claim_digests=tuple(
                _sequence(raw["publishable_claim_digests"], "publishable_claim_digests")
            ),
            display_claim_digests=tuple(
                _sequence(raw["display_claim_digests"], "display_claim_digests")
            ),
            question_dispositions=tuple(
                _sequence(raw["question_dispositions"], "question_dispositions")
            ),
            semantic_files=tuple(files),
        )
        if raw["digest"] != value.digest or value.canonical_bytes() != payload:
            raise AttributionError(
                "publication_digest_mismatch",
                "release manifest is not canonical or its digest is invalid",
            )
        return value


@dataclass(frozen=True)
class PublicationTransport:
    release_id: str
    release_digest: str
    published_at: str
    schema_version: str = TRANSPORT_SCHEMA

    def __post_init__(self) -> None:
        _require_release_id(self.release_id)
        _require_digest(self.release_digest, "release_digest")
        if self.schema_version != TRANSPORT_SCHEMA:
            raise AttributionError("transport_schema_mismatch", "schema is unsupported")
        try:
            parsed = datetime.fromisoformat(self.published_at.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise AttributionError("invalid_transport_time", "published_at is invalid") from exc
        if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            raise AttributionError("invalid_transport_time", "published_at must be UTC")

    @property
    def digest(self) -> str:
        return digest(self._semantic_dict())

    def _semantic_dict(self) -> dict[str, str]:
        return {
            "schema_version": self.schema_version,
            "release_id": self.release_id,
            "release_digest": self.release_digest,
            "published_at": self.published_at,
        }

    def to_dict(self) -> dict[str, str]:
        value = self._semantic_dict()
        value["digest"] = self.digest
        return value

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())

    @classmethod
    def from_bytes(cls, payload: bytes) -> PublicationTransport:
        raw = _parse_object(payload, "invalid_transport_json")
        _require_fields(
            raw,
            {"schema_version", "release_id", "release_digest", "published_at", "digest"},
            "transport_field_mismatch",
        )
        value = cls(
            schema_version=raw["schema_version"],
            release_id=raw["release_id"],
            release_digest=raw["release_digest"],
            published_at=raw["published_at"],
        )
        if raw["digest"] != value.digest or value.canonical_bytes() != payload:
            raise AttributionError(
                "transport_digest_mismatch",
                "transport envelope is not canonical or its digest is invalid",
            )
        return value


@dataclass(frozen=True)
class PublishedAttributionRelease:
    root: Path
    manifest: AttributionReleaseManifest
    transport: PublicationTransport
    workspace: Mapping[str, Any]

    @property
    def release_id(self) -> str:
        return self.manifest.release_id


def _digest_sequence(values: Sequence[str], field_name: str) -> tuple[str, ...]:
    normalized = tuple(values)
    if any(not isinstance(value, str) for value in normalized):
        raise AttributionError("invalid_publication_digest", f"{field_name} is invalid")
    normalized = tuple(sorted(normalized))
    if len(normalized) != len(set(normalized)):
        raise AttributionError("duplicate_publication_digest", f"{field_name} repeats")
    for value in normalized:
        _require_digest(value, field_name)
    return normalized


def _safe_relative_path(value: str) -> Path:
    _require_text(value, "publication_path")
    if "\\" in value or any(ord(character) < 32 for character in value):
        raise AttributionError(
            "invalid_publication_path",
            "publication paths must not contain control or platform separator characters",
        )
    path = Path(value)
    if path.is_absolute() or not path.parts or ".." in path.parts or "." in path.parts:
        raise AttributionError(
            "invalid_publication_path",
            "publication paths must be normalized relative paths",
        )
    if path.as_posix() != value or any(part.startswith(".") for part in path.parts):
        raise AttributionError(
            "invalid_publication_path",
            "publication paths must not be hidden or platform dependent",
        )
    return path


def _read_safe_file(root: Path, relative: str) -> bytes:
    path = _safe_relative_path(relative)
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise AttributionError(
                "invalid_publication_path",
                f"symbolic links are forbidden: {relative}",
            )
    if not current.is_file():
        raise AttributionError("missing_publication_file", relative)
    try:
        return current.read_bytes()
    except OSError as exc:
        raise AttributionError("publication_file_unreadable", relative) from exc


def _all_regular_files(root: Path) -> set[str]:
    files: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise AttributionError(
                "invalid_publication_path",
                f"symbolic links are forbidden: {path.relative_to(root)}",
            )
        if path.is_file():
            files.add(path.relative_to(root).as_posix())
        elif not path.is_dir():
            raise AttributionError(
                "invalid_publication_path",
                f"non-regular entries are forbidden: {path.relative_to(root)}",
            )
    return files


def _canonical_checksum_ledger(files: Mapping[str, bytes]) -> bytes:
    return "".join(
        f"{hashlib.sha256(payload).hexdigest()}  {path}\n"
        for path, payload in sorted(files.items())
    ).encode("ascii")


def _parse_checksum_ledger(payload: bytes) -> dict[str, str]:
    try:
        lines = payload.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise AttributionError("invalid_checksum_ledger", "ledger must be ASCII") from exc
    values: dict[str, str] = {}
    for line in lines:
        parts = line.split("  ", 1)
        if len(parts) != 2 or _HEX_DIGEST_PATTERN.fullmatch(parts[0]) is None:
            raise AttributionError("invalid_checksum_ledger", "ledger entry is invalid")
        path = _safe_relative_path(parts[1]).as_posix()
        if path in {"CHECKSUMS.sha256", "READY"} or path in values:
            raise AttributionError("invalid_checksum_ledger", "ledger path is invalid")
        values[path] = parts[0]
    if not values:
        raise AttributionError("invalid_checksum_ledger", "ledger must not be empty")
    expected = "".join(f"{value}  {path}\n" for path, value in sorted(values.items())).encode(
        "ascii"
    )
    if payload != expected:
        raise AttributionError("invalid_checksum_ledger", "ledger is not canonical")
    return values


def _normalize_published_at(value: datetime | None) -> str:
    observed = value or datetime.now(timezone.utc)
    if not isinstance(observed, datetime) or observed.tzinfo is None:
        raise AttributionError("invalid_transport_time", "published_at must be timezone-aware")
    observed = observed.astimezone(timezone.utc)
    return observed.isoformat().replace("+00:00", "Z")


def _default_unavailable_components(
    manifest: AdapterCapabilityManifest,
) -> tuple[str, ...]:
    # A root may legitimately have no intervention. A non-root native component with
    # dependencies and no legal intervention is explicitly visible but not attributable.
    return tuple(
        sorted(
            item.component_id
            for item in manifest.components
            if item.depends_on and not item.legal_interventions
        )
    )


def _build_workspace(
    request: AttributionRunRequest,
    result: AttributionPipelineResult,
    *,
    title: str,
    subtitle: str,
) -> AttributionWorkspace:
    if not result.claims:
        raise AttributionError(
            "empty_evaluated_workspace",
            "a report-center workspace requires at least one verified claim",
        )
    claim_question_digests = {item.question_digest for item in result.claims}
    questions = tuple(
        item for item in request.questions if item.digest in claim_question_digests
    )
    baseline_digest = result.claims[0].baseline_experiment_digest
    baseline_matches = [item for item in result.experiments if item.digest == baseline_digest]
    if len(baseline_matches) != 1 or baseline_matches[0].role is not ExperimentRole.BASELINE:
        raise AttributionError(
            "publication_baseline_mismatch",
            "the evaluated claims do not resolve one workspace baseline",
        )
    unavailable = _default_unavailable_components(request.adapter_manifest)
    claimed_components = {item.subject_component_id for item in result.claims}
    if claimed_components.intersection(unavailable):
        raise AttributionError(
            "workspace_availability_mismatch",
            "a component cannot be both unavailable and the subject of a valid claim",
        )
    return AttributionWorkspace(
        workspace_id=f"workspace:{result.run_id}",
        title=title,
        subtitle=subtitle,
        source_kind=result.source_kind,
        adapter=request.adapter_manifest,
        baseline=baseline_matches[0],
        claims=result.claims,
        questions=questions,
        experiments=result.experiments,
        condition_comparisons=result.conditions,
        interaction_comparisons=result.interactions,
        analysis_checks=result.analysis_checks,
        artifacts=result.artifacts,
        unavailable_component_ids=unavailable,
    )


def _node_token(experiment: Any) -> str:
    return str(experiment.digest).removeprefix("sha256:")


def _source_lineage_bytes(result: AttributionPipelineResult) -> dict[str, bytes]:
    source = result.run_dir
    if source.is_symlink() or not source.is_dir():
        raise AttributionError("invalid_pipeline_source", "run directory is invalid")
    expected = set(_PIPELINE_ROOT_FILES)
    for experiment in result.plan.experiments:
        token = _node_token(experiment)
        expected.update(
            {
                f"work/{token}.json",
                f"raw/{token}.json",
                f"verification/{token}.json",
                f"receipts/execution/{token}.json",
                f"receipts/verification/{token}.json",
            }
        )
    expected.update(
        {
            "evaluation/bundle.json",
            "receipts/planned.json",
            "receipts/running.json",
            "receipts/evaluated.json",
        }
    )
    expected.update(
        f"artifacts/{item.digest.removeprefix('sha256:')}.json"
        for item in result.artifacts
    )
    expected.update(
        f"evidence/experiments/{item.digest.removeprefix('sha256:')}.json"
        for item in result.experiments
    )
    expected.update(
        f"evidence/conditions/{item.digest.removeprefix('sha256:')}.json"
        for item in result.conditions
    )
    expected.update(
        f"evidence/analysis-checks/{item.digest.removeprefix('sha256:')}.json"
        for item in result.analysis_checks
    )
    actual = _all_regular_files(source)
    actual.discard(".pipeline.lock")
    failure_paths = sorted(
        path for path in actual if path.startswith("receipts/failures/")
    )
    for path in failure_paths:
        receipt = PipelineStageReceipt.from_bytes(_read_safe_file(source, path))
        if receipt.run_id != result.run_id:
            raise AttributionError(
                "pipeline_receipt_lineage_mismatch",
                f"failure receipt belongs to another run: {path}",
            )
    expected.update(failure_paths)
    if actual != expected:
        missing = sorted(expected.difference(actual))
        extra = sorted(actual.difference(expected))
        raise AttributionError(
            "lineage_file_closure_mismatch",
            f"missing={missing}, extra={extra}",
        )
    return {
        f"lineage/{path}": _read_safe_file(source, path) for path in sorted(expected)
    }


def _validate_pipeline_result(
    request: AttributionRunRequest, result: AttributionPipelineResult
) -> None:
    if not isinstance(request, AttributionRunRequest):
        raise AttributionError("invalid_run_request", "request is invalid")
    if not isinstance(result, AttributionPipelineResult):
        raise AttributionError("invalid_pipeline_result", "result is invalid")
    expected_run_id = f"attribution-run-{request.digest.removeprefix('sha256:')[:24]}"
    if result.state is not PipelineRunState.EVALUATED or result.run_id != expected_run_id:
        raise AttributionError(
            "pipeline_not_evaluated",
            "only the matching complete evaluated run may be published",
        )
    if result.run_dir.name != result.run_id:
        raise AttributionError(
            "pipeline_run_path_mismatch",
            "pipeline result directory does not match its run identity",
        )
    status = AttributionPipeline(result.run_dir.parent).status(request)
    if (
        status.state is not PipelineRunState.EVALUATED
        or status.run_id != result.run_id
        or status.plan_digest != result.plan.digest
        or status.claim_count != len(result.claims)
    ):
        raise AttributionError(
            "pipeline_not_evaluated",
            "persisted pipeline status does not match the supplied result",
        )
    if result.source_kind != request.adapter_manifest.evidence_source_kind:
        raise AttributionError("publication_source_mismatch", "result source kind drifted")
    if result.plan.run_request.digest != request.digest:
        raise AttributionError("publication_request_mismatch", "result plan belongs to another run")
    if (
        result.evaluation_bundle.canonical_bytes()
        != _read_safe_file(result.run_dir, "evaluation/bundle.json")
        or result.evaluation_receipt.canonical_bytes()
        != _read_safe_file(result.run_dir, "receipts/evaluated.json")
    ):
        raise AttributionError(
            "publication_evaluation_mismatch",
            "in-memory evaluation differs from persisted deterministic output",
        )
    bundle_claims = tuple(result.evaluation_bundle.claims)
    if canonical_bytes([item.to_dict() for item in result.claims]) != canonical_bytes(
        [dict(item) for item in bundle_claims]
    ):
        raise AttributionError(
            "publication_claim_mismatch",
            "result claims differ from the evaluated bundle",
        )
    if result.source_kind == SYNTHETIC_SOURCE_KIND and result.publishable_claims:
        raise AttributionError(
            "synthetic_claim_publication_forbidden",
            "synthetic claims cannot become formal published conclusions",
        )


def _publication_generated_evaluation_bytes(
    result: AttributionPipelineResult,
) -> dict[str, bytes]:
    values: dict[str, bytes] = {}
    for claim in result.claims:
        values[
            f"lineage/evaluation/claims/{claim.digest.removeprefix('sha256:')}.json"
        ] = canonical_bytes(claim.to_dict())
        for receipt in claim.verification_receipts:
            values[
                "lineage/evaluation/kernel-receipts/"
                f"{receipt.digest.removeprefix('sha256:')}.json"
            ] = receipt.canonical_bytes()
    for interaction in result.interactions:
        values[
            "lineage/evaluation/interactions/"
            f"{interaction.digest.removeprefix('sha256:')}.json"
        ] = canonical_bytes(interaction.to_dict())
    return values


def _write_file(root: Path, relative: str, payload: bytes) -> None:
    path = root / _safe_relative_path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _publication_lock(root: Path):
    lock_path = root / ".publication.lock"
    descriptor = os.open(
        lock_path,
        os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


class AttributionPublisher:
    """Publish a complete Pipeline result as an immutable report-center release."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink():
            raise AttributionError(
                "invalid_publication_path",
                "publication root cannot be a symbolic link",
            )
        self._resolved_root = self.root.resolve(strict=True)

    def publish(
        self,
        request: AttributionRunRequest,
        result: AttributionPipelineResult,
        *,
        release_id: str | None = None,
        title: str = "Quanthus 策略归因",
        subtitle: str | None = None,
        published_at: datetime | None = None,
    ) -> PublishedAttributionRelease:
        _validate_pipeline_result(request, result)
        title = _require_text(title, "title")
        subtitle = _require_text(
            subtitle
            or (
                f"已验证归因证据包：{request.period_start.isoformat()}"
                f" 至 {request.period_end.isoformat()}"
            ),
            "subtitle",
        )
        workspace = _build_workspace(
            request,
            result,
            title=title,
            subtitle=subtitle,
        )
        release_id = _require_release_id(
            release_id
            or "attribution-release-"
            + result.evaluation_bundle.digest.removeprefix("sha256:")[:24]
        )
        semantic_payloads = _source_lineage_bytes(result)
        semantic_payloads.update(_publication_generated_evaluation_bytes(result))
        semantic_payloads["workspace.json"] = workspace.canonical_bytes()
        semantic_file_records = tuple(
            PublicationFile.from_bytes(path, payload)
            for path, payload in sorted(semantic_payloads.items())
        )
        manifest = AttributionReleaseManifest(
            release_id=release_id,
            run_id=result.run_id,
            source_kind=result.source_kind,
            synthetic_fixture=result.source_kind == SYNTHETIC_SOURCE_KIND,
            formal_strategy_evidence=result.source_kind != SYNTHETIC_SOURCE_KIND,
            request_digest=request.digest,
            plan_digest=result.plan.digest,
            evaluation_bundle_digest=result.evaluation_bundle.digest,
            evaluation_receipt_digest=result.evaluation_receipt.digest,
            workspace_digest=workspace.digest,
            publishable_claim_digests=tuple(
                item.digest for item in result.publishable_claims
            ),
            display_claim_digests=tuple(item.digest for item in result.claims),
            question_dispositions=tuple(
                item.to_dict() for item in result.plan.planned_questions
            ),
            semantic_files=semantic_file_records,
        )
        transport = PublicationTransport(
            release_id=release_id,
            release_digest=manifest.digest,
            published_at=_normalize_published_at(published_at),
        )
        final = self.root / release_id
        try:
            final.parent.resolve(strict=True).relative_to(self._resolved_root)
        except ValueError as exc:
            raise AttributionError(
                "invalid_publication_path",
                "release target escapes publication root",
            ) from exc
        with _publication_lock(self.root):
            if final.exists() or final.is_symlink():
                if final.is_symlink() or not (final / "READY").is_file():
                    raise AttributionError(
                        "publication_target_incomplete",
                        "an incomplete or unsafe target already occupies the release id",
                    )
                existing = verify_attribution_release(final, request=request)
                if existing.manifest.digest != manifest.digest:
                    raise AttributionError(
                        "release_id_conflict",
                        "release id already contains different semantic content",
                    )
                _refresh_catalog_unlocked(self.root)
                return existing
            staging = Path(
                tempfile.mkdtemp(
                    prefix=f".{release_id}.",
                    suffix=".staging",
                    dir=self.root,
                )
            )
            try:
                for path, payload in sorted(semantic_payloads.items()):
                    _write_file(staging, path, payload)
                release_bytes = manifest.canonical_bytes()
                transport_bytes = transport.canonical_bytes()
                _write_file(staging, "release.json", release_bytes)
                _write_file(staging, "transport.json", transport_bytes)
                checksummed = {
                    **semantic_payloads,
                    "release.json": release_bytes,
                    "transport.json": transport_bytes,
                }
                checksum_bytes = _canonical_checksum_ledger(checksummed)
                _write_file(staging, "CHECKSUMS.sha256", checksum_bytes)
                _write_file(
                    staging,
                    "READY",
                    f"{hashlib.sha256(checksum_bytes).hexdigest()}\n".encode("ascii"),
                )
                _verify_release(staging, request=request, expected_release_id=release_id)
                for directory in sorted(
                    (item for item in staging.rglob("*") if item.is_dir()),
                    key=lambda item: len(item.parts),
                    reverse=True,
                ):
                    _fsync_directory(directory)
                _fsync_directory(staging)
                os.rename(staging, final)
                _fsync_directory(self.root)
                _refresh_catalog_unlocked(self.root)
            except Exception:
                shutil.rmtree(staging, ignore_errors=True)
                raise
        return verify_attribution_release(final, request=request)


def _load_digest_named_collection(
    release_root: Path,
    files: set[str],
    prefix: str,
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    observed_digests: set[str] = set()
    for path in sorted(files):
        if not path.startswith(f"{prefix}/") or not path.endswith(".json"):
            continue
        payload = _read_safe_file(release_root, path)
        value = _parse_object(payload, "invalid_evidence_json")
        if payload != canonical_bytes(value):
            raise AttributionError(
                "noncanonical_publication_file",
                f"semantic JSON is not canonical: {path}",
            )
        value_digest = _require_digest(value.get("digest"), f"{prefix}.digest")
        expected_path = f"{prefix}/{value_digest.removeprefix('sha256:')}.json"
        if path != expected_path or value_digest in observed_digests:
            raise AttributionError(
                "evidence_file_identity_mismatch",
                f"evidence path does not match its content identity: {path}",
            )
        observed_digests.add(value_digest)
        values.append(value)
    return values


def _verify_workspace(
    release_root: Path,
    manifest: AttributionReleaseManifest,
    request: AttributionRunRequest,
) -> Mapping[str, Any]:
    workspace_bytes = _read_safe_file(release_root, "workspace.json")
    workspace = _parse_object(workspace_bytes, "invalid_workspace_json")
    _require_fields(
        workspace,
        {
            "schema",
            "workspace_id",
            "title",
            "subtitle",
            "source_kind",
            "adapter",
            "baseline",
            "questions",
            "experiments",
            "condition_comparisons",
            "interaction_comparisons",
            "analysis_checks",
            "artifacts",
            "claims",
            "unavailable_component_ids",
            "digest",
        },
        "workspace_field_mismatch",
    )
    semantic_workspace = {key: value for key, value in workspace.items() if key != "digest"}
    if (
        workspace_bytes != canonical_bytes(workspace)
        or workspace["digest"] != digest(semantic_workspace)
        or workspace["digest"] != manifest.workspace_digest
    ):
        raise AttributionError("workspace_digest_mismatch", "workspace digest is invalid")
    if workspace["schema"] != WORKSPACE_SCHEMA:
        raise AttributionError(
            "workspace_schema_mismatch",
            "published workspace schema is unsupported",
        )
    _require_text(workspace["title"], "workspace.title")
    _require_text(workspace["subtitle"], "workspace.subtitle")
    if (
        workspace["workspace_id"] != f"workspace:{manifest.run_id}"
        or workspace["source_kind"] != manifest.source_kind
        or workspace["adapter"] != request.adapter_manifest.to_dict()
    ):
        raise AttributionError(
            "workspace_source_mismatch",
            "workspace does not bind the published request source",
        )
    bundle = EvaluationBundle.from_bytes(
        _read_safe_file(release_root, "lineage/evaluation/bundle.json")
    )
    if (
        bundle.digest != manifest.evaluation_bundle_digest
        or bundle.source_kind != manifest.source_kind
        or bundle.run_id != manifest.run_id
        or bundle.plan_digest != manifest.plan_digest
    ):
        raise AttributionError(
            "publication_evaluation_mismatch",
            "workspace evaluation bundle identity differs from the release",
        )
    if canonical_bytes(workspace["claims"]) != canonical_bytes(
        [dict(item) for item in bundle.claims]
    ) or canonical_bytes(workspace["interaction_comparisons"]) != canonical_bytes(
        [dict(item) for item in bundle.interactions]
    ):
        raise AttributionError(
            "workspace_evaluation_mismatch",
            "workspace conclusions differ from the deterministic evaluation bundle",
        )
    claim_question_digests = {item["question_digest"] for item in bundle.claims}
    expected_questions = [
        item.to_dict() for item in request.questions if item.digest in claim_question_digests
    ]
    if canonical_bytes(workspace["questions"]) != canonical_bytes(expected_questions):
        raise AttributionError(
            "workspace_question_mismatch",
            "workspace questions differ from evaluated request questions",
        )
    expected_collections = {
        "experiments": "lineage/evidence/experiments",
        "condition_comparisons": "lineage/evidence/conditions",
        "analysis_checks": "lineage/evidence/analysis-checks",
        "artifacts": "lineage/artifacts",
    }
    files = _all_regular_files(release_root)
    for field_name, prefix in expected_collections.items():
        persisted = _load_digest_named_collection(release_root, files, prefix)
        workspace_values = _sequence(workspace[field_name], field_name)
        if len(workspace_values) != len(persisted) or {
            canonical_bytes(item) for item in workspace_values
        } != {canonical_bytes(item) for item in persisted}:
            raise AttributionError(
                "workspace_evidence_mismatch",
                f"workspace {field_name} differs from its lineage files",
            )
    generated_collections = {
        "lineage/evaluation/claims": [dict(item) for item in bundle.claims],
        "lineage/evaluation/interactions": [
            dict(item) for item in bundle.interactions
        ],
        "lineage/evaluation/kernel-receipts": [
            dict(item) for item in bundle.kernel_receipts
        ],
    }
    for prefix, expected in generated_collections.items():
        persisted = _load_digest_named_collection(release_root, files, prefix)
        if len(expected) != len(persisted) or {
            canonical_bytes(item) for item in expected
        } != {canonical_bytes(item) for item in persisted}:
            raise AttributionError(
                "evaluation_file_closure_mismatch",
                f"published evaluation files differ from the bundle: {prefix}",
            )
    baseline = _mapping(workspace["baseline"], "baseline")
    expected_baseline_digest = (
        bundle.claims[0].get("baseline_experiment_digest")
        if bundle.claims
        else None
    )
    if (
        baseline.get("role") != ExperimentRole.BASELINE.value
        or baseline.get("digest") != expected_baseline_digest
        or canonical_bytes(baseline)
        not in {
            canonical_bytes(item)
            for item in _sequence(workspace["experiments"], "experiments")
        }
    ):
        raise AttributionError(
            "workspace_baseline_mismatch",
            "workspace baseline is not the deterministic evaluated baseline",
        )
    if workspace["unavailable_component_ids"] != list(
        _default_unavailable_components(request.adapter_manifest)
    ):
        raise AttributionError(
            "workspace_availability_mismatch",
            "workspace availability differs from the adapter contract",
        )
    display = tuple(sorted(item["digest"] for item in bundle.claims))
    if display != manifest.display_claim_digests:
        raise AttributionError(
            "publication_claim_mismatch",
            "display claim identities differ from the evaluation bundle",
        )
    expected_publishable = () if manifest.source_kind == SYNTHETIC_SOURCE_KIND else display
    if manifest.publishable_claim_digests != expected_publishable:
        raise AttributionError(
            "publication_claim_mismatch",
            "publishable claim identities are not derived from the verified source kind",
        )
    return MappingProxyType(workspace)


def _expected_semantic_paths(
    *,
    plan: Mapping[str, Any],
    workspace: Mapping[str, Any],
    bundle: EvaluationBundle,
    actual_semantic: set[str],
) -> set[str]:
    expected = {
        "workspace.json",
        "lineage/request.json",
        "lineage/plan.json",
        "lineage/executor-binding.json",
        "lineage/evaluation/bundle.json",
        "lineage/receipts/planned.json",
        "lineage/receipts/running.json",
        "lineage/receipts/evaluated.json",
    }
    for experiment_value in _sequence(plan.get("experiments"), "plan.experiments"):
        experiment = _mapping(experiment_value, "planned_experiment")
        experiment_digest = _require_digest(
            experiment.get("digest"), "planned_experiment.digest"
        )
        token = experiment_digest.removeprefix("sha256:")
        expected.update(
            {
                f"lineage/work/{token}.json",
                f"lineage/raw/{token}.json",
                f"lineage/verification/{token}.json",
                f"lineage/receipts/execution/{token}.json",
                f"lineage/receipts/verification/{token}.json",
            }
        )
    for field_name, prefix in (
        ("experiments", "lineage/evidence/experiments"),
        ("condition_comparisons", "lineage/evidence/conditions"),
        ("analysis_checks", "lineage/evidence/analysis-checks"),
        ("artifacts", "lineage/artifacts"),
    ):
        for item_value in _sequence(workspace.get(field_name), field_name):
            item = _mapping(item_value, field_name)
            item_digest = _require_digest(item.get("digest"), f"{field_name}.digest")
            expected.add(f"{prefix}/{item_digest.removeprefix('sha256:')}.json")
    for prefix, values in (
        ("lineage/evaluation/claims", bundle.claims),
        ("lineage/evaluation/interactions", bundle.interactions),
        ("lineage/evaluation/kernel-receipts", bundle.kernel_receipts),
    ):
        for item in values:
            item_digest = _require_digest(item.get("digest"), f"{prefix}.digest")
            expected.add(f"{prefix}/{item_digest.removeprefix('sha256:')}.json")
    failure_prefix = "lineage/receipts/failures/"
    for path in actual_semantic:
        if path.startswith(failure_prefix):
            relative = path.removeprefix(failure_prefix)
            if "/" in relative or not relative.endswith(".json"):
                raise AttributionError(
                    "lineage_file_closure_mismatch",
                    f"invalid failure receipt path: {path}",
                )
            expected.add(path)
    return expected


def _verify_release(
    release_dir: Path,
    *,
    request: AttributionRunRequest | None,
    expected_release_id: str,
) -> PublishedAttributionRelease:
    root = Path(release_dir)
    if root.is_symlink() or not root.is_dir():
        raise AttributionError("attribution_release_not_found", str(root))
    actual_files = _all_regular_files(root)
    if "READY" not in actual_files:
        raise AttributionError("attribution_release_not_ready", str(root))
    checksum_bytes = _read_safe_file(root, "CHECKSUMS.sha256")
    ready_bytes = _read_safe_file(root, "READY")
    expected_ready = f"{hashlib.sha256(checksum_bytes).hexdigest()}\n".encode("ascii")
    if ready_bytes != expected_ready:
        raise AttributionError("ready_digest_mismatch", "READY does not bind the checksum ledger")
    checksums = _parse_checksum_ledger(checksum_bytes)
    if set(checksums) != actual_files.difference({"CHECKSUMS.sha256", "READY"}):
        raise AttributionError(
            "checksum_file_closure_mismatch",
            "checksum ledger does not cover exactly the release files",
        )
    for path, expected in checksums.items():
        if hashlib.sha256(_read_safe_file(root, path)).hexdigest() != expected:
            raise AttributionError("publication_checksum_mismatch", path)
    manifest = AttributionReleaseManifest.from_bytes(_read_safe_file(root, "release.json"))
    if manifest.release_id != _require_release_id(expected_release_id):
        raise AttributionError("release_identity_mismatch", "directory and release id differ")
    transport = PublicationTransport.from_bytes(_read_safe_file(root, "transport.json"))
    if (
        transport.release_id != manifest.release_id
        or transport.release_digest != manifest.digest
    ):
        raise AttributionError(
            "transport_lineage_mismatch",
            "transport envelope does not bind the semantic release",
        )
    declared = {item.path: item for item in manifest.semantic_files}
    actual_semantic = actual_files.difference(
        {"release.json", "transport.json", "CHECKSUMS.sha256", "READY"}
    )
    if set(declared) != actual_semantic:
        raise AttributionError(
            "semantic_file_closure_mismatch",
            "release manifest does not cover exactly the semantic files",
        )
    for path, record in declared.items():
        payload = _read_safe_file(root, path)
        if _sha256_bytes(payload) != record.sha256 or len(payload) != record.size:
            raise AttributionError("semantic_file_digest_mismatch", path)
        if not path.endswith(".json"):
            raise AttributionError(
                "unexpected_semantic_file",
                f"semantic publication files must be canonical JSON: {path}",
            )
        parsed = _parse_object(payload, "invalid_semantic_file_json")
        if payload != canonical_bytes(parsed):
            raise AttributionError(
                "noncanonical_publication_file",
                f"semantic JSON is not canonical: {path}",
            )
    embedded_request = load_attribution_run_request(
        _read_safe_file(root, "lineage/request.json")
    )
    if request is not None and embedded_request.canonical_bytes() != request.canonical_bytes():
        raise AttributionError(
            "publication_request_mismatch",
            "caller request differs from the published request",
        )
    request = embedded_request
    if request.digest != manifest.request_digest:
        raise AttributionError(
            "publication_request_mismatch",
            "release manifest request digest is invalid",
        )
    if manifest.source_kind != request.adapter_manifest.evidence_source_kind:
        raise AttributionError(
            "publication_source_mismatch",
            "release source kind differs from the canonical adapter manifest",
        )
    expected_run_id = (
        f"attribution-run-{request.digest.removeprefix('sha256:')[:24]}"
    )
    if manifest.run_id != expected_run_id:
        raise AttributionError(
            "publication_run_identity_mismatch",
            "release run id is not derived from its canonical request",
        )
    with tempfile.TemporaryDirectory(prefix="quanthus-attribution-verify-") as temporary:
        pipeline_root = Path(temporary)
        shutil.copytree(root / "lineage", pipeline_root / manifest.run_id)
        snapshot = AttributionPipeline(pipeline_root).status(request)
    if (
        snapshot.state is not PipelineRunState.EVALUATED
        or snapshot.run_id != manifest.run_id
        or snapshot.plan_digest != manifest.plan_digest
    ):
        raise AttributionError(
            "publication_pipeline_mismatch",
            "published lineage is not one complete evaluated pipeline",
        )
    receipt = PipelineStageReceipt.from_bytes(
        _read_safe_file(root, "lineage/receipts/evaluated.json")
    )
    if receipt.digest != manifest.evaluation_receipt_digest:
        raise AttributionError(
            "publication_evaluation_mismatch",
            "evaluated receipt differs from the release manifest",
        )
    plan = _parse_object(_read_safe_file(root, "lineage/plan.json"), "invalid_plan_json")
    if canonical_bytes(plan.get("planned_questions")) != canonical_bytes(
        [dict(item) for item in manifest.question_dispositions]
    ):
        raise AttributionError(
            "publication_plan_mismatch",
            "question dispositions differ from the canonical plan",
        )
    workspace = _verify_workspace(root, manifest, request)
    bundle = EvaluationBundle.from_bytes(
        _read_safe_file(root, "lineage/evaluation/bundle.json")
    )
    expected_semantic = _expected_semantic_paths(
        plan=plan,
        workspace=workspace,
        bundle=bundle,
        actual_semantic=actual_semantic,
    )
    if actual_semantic != expected_semantic:
        raise AttributionError(
            "lineage_file_closure_mismatch",
            "release contains missing or unreferenced semantic lineage files",
        )
    return PublishedAttributionRelease(
        root=root,
        manifest=manifest,
        transport=transport,
        workspace=workspace,
    )


def verify_attribution_release(
    release_dir: Path,
    *,
    request: AttributionRunRequest | None = None,
) -> PublishedAttributionRelease:
    """Deep-verify one READY release, including its embedded Pipeline lineage."""

    root = Path(release_dir)
    return _verify_release(root, request=request, expected_release_id=root.name)


def discover_attribution_releases(
    publication_root: Path,
) -> tuple[PublishedAttributionRelease, ...]:
    """Return only complete, deeply verified READY releases in stable order."""

    root = Path(publication_root)
    if root.is_symlink() or not root.is_dir():
        raise AttributionError("invalid_publication_path", "publication root is invalid")
    releases = []
    for candidate in sorted(root.iterdir(), key=lambda item: item.name):
        if candidate.name.startswith(".") or not candidate.is_dir() or candidate.is_symlink():
            continue
        if not (candidate / "READY").is_file():
            continue
        releases.append(verify_attribution_release(candidate))
    return tuple(releases)


def attribution_publication_catalog(publication_root: Path) -> dict[str, Any]:
    """Build the deterministic static catalog consumed by the read-only Web viewer."""

    releases = discover_attribution_releases(publication_root)
    return {
        "schema_version": PUBLICATION_CATALOG_SCHEMA,
        "releases": [
            {
                "release_id": item.release_id,
                "release_path": f"./{item.release_id}/release.json",
                "workspace_path": f"./{item.release_id}/workspace.json",
                "checksum_path": f"./{item.release_id}/CHECKSUMS.sha256",
                "ready_path": f"./{item.release_id}/READY",
            }
            for item in releases
        ],
    }


def _refresh_catalog_unlocked(publication_root: Path) -> Path:
    root = Path(publication_root)
    payload = canonical_bytes(attribution_publication_catalog(root))
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".catalog.",
            suffix=".tmp",
            dir=root,
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, root / "catalog.json")
        temporary_path = None
        _fsync_directory(root)
    finally:
        if temporary_path is not None:
            with suppress(FileNotFoundError):
                temporary_path.unlink()
    return root / "catalog.json"


def refresh_attribution_publication_catalog(publication_root: Path) -> Path:
    """Atomically refresh ``catalog.json`` from deeply verified READY releases."""

    root = Path(publication_root)
    if root.is_symlink() or not root.is_dir():
        raise AttributionError("invalid_publication_path", "publication root is invalid")
    with _publication_lock(root):
        return _refresh_catalog_unlocked(root)

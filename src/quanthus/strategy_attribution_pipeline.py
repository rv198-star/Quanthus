"""Immutable local execution pipeline for strategy-neutral attribution plans."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from quanthus.strategy_attribution import (
    ATTRIBUTION_SCHEMA_VERSION,
    FORMAL_ARTIFACT_PRODUCER_ID,
    FORMAL_ARTIFACT_PRODUCER_VERSION,
    SYNTHETIC_ARTIFACT_PRODUCER_ID,
    SYNTHETIC_ARTIFACT_PRODUCER_VERSION,
    AnalysisCheckEvidence,
    AnalysisCheckKind,
    AnalysisCheckScope,
    ArtifactKind,
    AttributionClaim,
    AttributionError,
    ClaimStatus,
    ConditionComparison,
    ExperimentManifest,
    ExperimentRole,
    InMemoryArtifactResolver,
    InteractionComparison,
    SemanticArtifactEnvelope,
    analysis_check_result_envelope,
    analysis_check_target_digest,
    canonical_bytes,
    condition_analysis_result_envelope,
    digest,
    evaluate_attribution,
    experiment_evidence_envelope,
    native_trace_envelope,
)
from quanthus.strategy_attribution_planning import (
    AttributionPlan,
    AttributionRunRequest,
    PlannedExperiment,
    PlannedQuestion,
    PlanNodeKind,
    QuestionBindingKind,
    QuestionPlanStatus,
    compile_attribution_plan,
)

PIPELINE_ID = "quanthus.strategy-attribution-pipeline"
PIPELINE_VERSION = "1.0.0"
PIPELINE_RECEIPT_SCHEMA = "quanthus.strategy-attribution-pipeline-receipt.v1"
EXECUTOR_BINDING_SCHEMA = "quanthus.strategy-attribution-executor-binding.v1"
EXECUTION_WORK_SCHEMA = "quanthus.strategy-attribution-execution-work.v1"
RAW_EXECUTION_SCHEMA = "quanthus.strategy-attribution-raw-execution.v1"
NODE_VERIFICATION_SCHEMA = "quanthus.strategy-attribution-node-verification.v1"
EVALUATION_BUNDLE_SCHEMA = "quanthus.strategy-attribution-evaluation-bundle.v1"
PIPELINE_VERIFIER_ID = "quanthus.strategy-attribution-pipeline-verifier"
PIPELINE_VERIFIER_VERSION = "1.0.0"
PIPELINE_EVALUATOR_ID = "quanthus.strategy-attribution-kernel-evaluator"
PIPELINE_EVALUATOR_VERSION = "1.0.0"
SYNTHETIC_SOURCE_KIND = "synthetic_fixture_not_strategy_evidence"
FORMAL_SOURCE_KIND = "formal_strategy_evidence"

_RAW_TRACE_RESERVED_KEYS = frozenset(
    {
        "analysis_checks",
        "claim",
        "claim_id",
        "claim_status",
        "claims",
        "classification",
        "condition_effects",
        "decision_summary",
        "effect",
        "evidence_type",
        "interaction_effects",
        "limitations",
        "material",
        "method_evidence",
        "metric_effects",
        "next_action_code",
        "oriented_effect",
        "passed",
        "reason_analysis",
        "reason_codes",
        "receipt_id",
        "reconciled",
        "reproduced",
        "status",
        "supporting_evidence_digests",
        "validation_flags",
        "verification_receipt",
        "verification_receipts",
        "verified_facts",
    }
)

_STABLE_RECEIPT_REASON_CODES = frozenset(
    {
        "executor_instance_override",
        "formal_strategy_source_blocked",
        "immutable_stage_conflict",
        "invalid_executor",
        "invalid_executor_binding",
        "invalid_raw_execution",
        "metric_calculator_binding_mismatch",
        "node_verification_failed",
        "persisted_plan_mismatch",
        "persisted_request_mismatch",
        "pipeline_evaluator_mismatch",
        "pipeline_receipt_lineage_mismatch",
        "pipeline_verifier_mismatch",
        "prototype_executor_not_registered",
        "prototype_producer_not_registered",
        "reserved_trace_field",
        "synthetic_component_unsupported",
        "synthetic_condition_unsupported",
        "synthetic_executor_adapter_mismatch",
        "synthetic_executor_injected_failure",
        "synthetic_executor_node_unsupported",
        "synthetic_metric_unsupported",
        "unregistered_executor_implementation",
        "unregistered_pipeline_evaluator",
        "unregistered_pipeline_verifier",
        "unregistered_plan_compiler",
    }
)

_REGISTERED_EXECUTOR_TYPES: dict[tuple[str, str], type[Any]] = {}


def register_synthetic_executor_implementation(executor_type: type[Any]) -> None:
    """Register the one bounded fixture implementation by concrete class identity."""

    if not isinstance(executor_type, type):
        raise AttributionError("invalid_executor_registration", "executor type is invalid")
    if (
        executor_type.__module__,
        executor_type.__qualname__,
    ) != (
        "quanthus.strategy_attribution_demo",
        "SyntheticAttributionExecutor",
    ):
        raise AttributionError(
            "untrusted_executor_registration",
            "the synthetic identity is reserved for its built-in implementation",
        )
    key = (
        SYNTHETIC_ARTIFACT_PRODUCER_ID,
        SYNTHETIC_ARTIFACT_PRODUCER_VERSION,
    )
    existing = _REGISTERED_EXECUTOR_TYPES.get(key)
    if existing is not None and existing is not executor_type:
        raise AttributionError(
            "executor_registration_conflict",
            "the executor identity is already registered",
        )
    _REGISTERED_EXECUTOR_TYPES[key] = executor_type


def register_formal_executor_implementation(executor_type: type[Any]) -> None:
    """Register the exact built-in formal executor by concrete class identity."""

    if not isinstance(executor_type, type):
        raise AttributionError("invalid_executor_registration", "executor type is invalid")
    if (executor_type.__module__, executor_type.__qualname__) != (
        "quanthus.expert_strategy.formal_attribution",
        "FormalAttributionExecutor",
    ):
        raise AttributionError(
            "untrusted_executor_registration",
            "the formal identity is reserved for its built-in implementation",
        )
    key = (FORMAL_ARTIFACT_PRODUCER_ID, FORMAL_ARTIFACT_PRODUCER_VERSION)
    existing = _REGISTERED_EXECUTOR_TYPES.get(key)
    if existing is not None and existing is not executor_type:
        raise AttributionError(
            "executor_registration_conflict",
            "the executor identity is already registered",
        )
    _REGISTERED_EXECUTOR_TYPES[key] = executor_type


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AttributionError("invalid_identity", f"{field_name} must not be empty")
    return value


def _require_digest(value: object, field_name: str) -> str:
    text = _require_text(value, field_name)
    encoded = text.removeprefix("sha256:") if text.startswith("sha256:") else ""
    if len(encoded) != 64 or any(item not in "0123456789abcdef" for item in encoded):
        raise AttributionError("invalid_digest", f"{field_name} must be a sha256 digest")
    return text


def _unique_text(
    values: Sequence[str], field_name: str, *, sort_values: bool = False
) -> tuple[str, ...]:
    normalized = tuple(values)
    if any(not isinstance(item, str) or not item.strip() for item in normalized):
        raise AttributionError("invalid_identity", f"{field_name} contains an empty value")
    if len(normalized) != len(set(normalized)):
        raise AttributionError("duplicate_identity", f"{field_name} contains duplicates")
    return tuple(sorted(normalized)) if sort_values else normalized


def _text_mapping(values: Mapping[str, str], field_name: str) -> Mapping[str, str]:
    normalized = dict(values)
    for key, value in normalized.items():
        _require_text(key, f"{field_name}.key")
        _require_text(value, f"{field_name}.{key}")
    return MappingProxyType(dict(sorted(normalized.items())))


def _decimal_mapping(values: Mapping[str, Decimal], field_name: str) -> Mapping[str, Decimal]:
    normalized = dict(values)
    for key, value in normalized.items():
        _require_text(key, f"{field_name}.key")
        if not isinstance(value, Decimal) or not value.is_finite():
            raise AttributionError("invalid_decimal", f"{field_name}.{key} must be finite")
    return MappingProxyType(dict(sorted(normalized.items())))


def _json_mapping(values: Mapping[str, Any], field_name: str) -> Mapping[str, Any]:
    try:
        normalized = json.loads(canonical_bytes(dict(values)))
    except (TypeError, ValueError) as exc:
        raise AttributionError("invalid_json_object", f"{field_name} is invalid") from exc
    if not isinstance(normalized, dict):
        raise AttributionError("invalid_json_object", f"{field_name} must be an object")
    return MappingProxyType(normalized)


def _reject_reserved_trace_keys(value: object, path: str = "trace_payload") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise AttributionError(
                    "invalid_trace_payload",
                    f"{path} contains a non-text key",
                )
            if key.casefold() in _RAW_TRACE_RESERVED_KEYS:
                raise AttributionError(
                    "reserved_trace_field",
                    f"{path}.{key} is reserved for verified attribution output",
                )
            _reject_reserved_trace_keys(nested, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _reject_reserved_trace_keys(nested, f"{path}[{index}]")


def _parse_json_object(payload: bytes, reason_code: str) -> dict[str, Any]:
    try:
        raw = json.loads(payload)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AttributionError(reason_code, "payload is not valid JSON") from exc
    if not isinstance(raw, dict):
        raise AttributionError(reason_code, "payload must be a JSON object")
    return raw


def _require_exact_fields(raw: Mapping[str, Any], expected: set[str], reason_code: str) -> None:
    if set(raw) != expected:
        raise AttributionError(reason_code, "payload fields are incomplete or unexpected")


class PipelineRunState(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    EXECUTED = "executed"
    VERIFICATION_FAILED = "verification_failed"
    EVALUATED = "evaluated"
    FAILED = "failed"


@dataclass(frozen=True)
class ExecutorBinding:
    executor_id: str
    executor_version: str
    producer_id: str
    producer_version: str
    source_kind: str
    metric_calculator_id: str
    metric_calculator_version: str
    synthetic_fixture: bool
    schema_version: str = EXECUTOR_BINDING_SCHEMA
    raw_execution_schema: str = RAW_EXECUTION_SCHEMA

    def __post_init__(self) -> None:
        for field_name in (
            "executor_id",
            "executor_version",
            "producer_id",
            "producer_version",
            "source_kind",
            "metric_calculator_id",
            "metric_calculator_version",
            "schema_version",
            "raw_execution_schema",
        ):
            _require_text(getattr(self, field_name), field_name)
        if self.schema_version != EXECUTOR_BINDING_SCHEMA:
            raise AttributionError("executor_binding_schema_mismatch", "schema is unsupported")
        if self.raw_execution_schema != RAW_EXECUTION_SCHEMA:
            raise AttributionError("raw_execution_schema_mismatch", "schema is unsupported")
        if not isinstance(self.synthetic_fixture, bool):
            raise AttributionError("invalid_boolean", "synthetic_fixture must be boolean")

    @property
    def digest(self) -> str:
        return digest(self._semantic_dict())

    def _semantic_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "raw_execution_schema": self.raw_execution_schema,
            "executor_id": self.executor_id,
            "executor_version": self.executor_version,
            "producer_id": self.producer_id,
            "producer_version": self.producer_version,
            "source_kind": self.source_kind,
            "metric_calculator_id": self.metric_calculator_id,
            "metric_calculator_version": self.metric_calculator_version,
            "synthetic_fixture": self.synthetic_fixture,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._semantic_dict()
        payload["digest"] = self.digest
        return payload

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())

    @classmethod
    def from_bytes(cls, payload: bytes) -> ExecutorBinding:
        raw = _parse_json_object(payload, "invalid_executor_binding_json")
        _require_exact_fields(
            raw,
            {
                "schema_version",
                "raw_execution_schema",
                "executor_id",
                "executor_version",
                "producer_id",
                "producer_version",
                "source_kind",
                "metric_calculator_id",
                "metric_calculator_version",
                "synthetic_fixture",
                "digest",
            },
            "executor_binding_field_mismatch",
        )
        binding = cls(
            schema_version=raw["schema_version"],
            raw_execution_schema=raw["raw_execution_schema"],
            executor_id=raw["executor_id"],
            executor_version=raw["executor_version"],
            producer_id=raw["producer_id"],
            producer_version=raw["producer_version"],
            source_kind=raw["source_kind"],
            metric_calculator_id=raw["metric_calculator_id"],
            metric_calculator_version=raw["metric_calculator_version"],
            synthetic_fixture=raw["synthetic_fixture"],
        )
        if raw["digest"] != binding.digest:
            raise AttributionError("executor_binding_digest_mismatch", "digest is invalid")
        return binding


@dataclass(frozen=True)
class ExecutionConsumer:
    question_id: str
    question_digest: str
    binding_kind: QuestionBindingKind
    specification_digest: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.question_id, "question_id")
        _require_digest(self.question_digest, "question_digest")
        if not isinstance(self.binding_kind, QuestionBindingKind):
            raise AttributionError("invalid_question_binding", "binding kind is invalid")
        if self.specification_digest is not None:
            _require_digest(self.specification_digest, "specification_digest")

    def to_dict(self) -> dict[str, str | None]:
        return {
            "question_id": self.question_id,
            "question_digest": self.question_digest,
            "binding_kind": self.binding_kind.value,
            "specification_digest": self.specification_digest,
        }


@dataclass(frozen=True)
class PlannedExecutionWork:
    run_id: str
    plan_id: str
    plan_digest: str
    plan_receipt_digest: str
    executor_binding_digest: str
    run_request_digest: str
    strategy_id: str
    strategy_version: str
    adapter_manifest_digest: str
    native_topology_digest: str
    context_digest: str
    context_bindings: Mapping[str, str]
    period_start: date
    period_end: date
    metric_calculator_id: str
    metric_calculator_version: str
    capital_constrained: bool
    point_in_time_required: bool
    experiment: PlannedExperiment
    parent_raw_result_digests: Mapping[str, str]
    consumers: tuple[ExecutionConsumer, ...]
    schema_version: str = EXECUTION_WORK_SCHEMA

    def __post_init__(self) -> None:
        for field_name in (
            "run_id",
            "plan_id",
            "strategy_id",
            "strategy_version",
            "metric_calculator_id",
            "metric_calculator_version",
            "schema_version",
        ):
            _require_text(getattr(self, field_name), field_name)
        for field_name in (
            "plan_digest",
            "plan_receipt_digest",
            "executor_binding_digest",
            "run_request_digest",
            "adapter_manifest_digest",
            "native_topology_digest",
            "context_digest",
        ):
            _require_digest(getattr(self, field_name), field_name)
        if self.schema_version != EXECUTION_WORK_SCHEMA:
            raise AttributionError("execution_work_schema_mismatch", "schema is unsupported")
        bindings = _text_mapping(self.context_bindings, "context_bindings")
        if digest(dict(bindings)) != self.context_digest:
            raise AttributionError("context_digest_mismatch", "work context digest is invalid")
        object.__setattr__(self, "context_bindings", bindings)
        if not isinstance(self.period_start, date) or not isinstance(self.period_end, date):
            raise AttributionError("invalid_period", "work period must contain dates")
        if self.period_start > self.period_end:
            raise AttributionError("invalid_period", "work period is reversed")
        if self.capital_constrained is not True or self.point_in_time_required is not True:
            raise AttributionError(
                "invalid_execution_constraint",
                "work requires capital-constrained point-in-time execution",
            )
        if not isinstance(self.experiment, PlannedExperiment):
            raise AttributionError("invalid_planned_experiment", "work experiment is invalid")
        parents = _text_mapping(self.parent_raw_result_digests, "parent_raw_result_digests")
        for value in parents.values():
            _require_digest(value, "parent_raw_result_digest")
        if set(parents) != set(self.experiment.dependency_experiment_ids):
            raise AttributionError(
                "execution_parent_mismatch",
                "work must bind every planned dependency exactly once",
            )
        object.__setattr__(self, "parent_raw_result_digests", parents)
        consumers = tuple(self.consumers)
        if not consumers or any(not isinstance(item, ExecutionConsumer) for item in consumers):
            raise AttributionError("missing_execution_consumer", "work requires consumers")
        keys = [
            (
                item.question_digest,
                item.binding_kind.value,
                item.specification_digest,
            )
            for item in consumers
        ]
        if len(keys) != len(set(keys)):
            raise AttributionError("duplicate_execution_consumer", "consumers must be unique")
        object.__setattr__(
            self,
            "consumers",
            tuple(
                sorted(
                    consumers,
                    key=lambda item: (
                        item.question_id,
                        item.question_digest,
                        item.binding_kind.value,
                        item.specification_digest or "",
                    ),
                )
            ),
        )

    @property
    def digest(self) -> str:
        return digest(self._semantic_dict())

    @property
    def work_id(self) -> str:
        return f"attribution-work:{self.digest.removeprefix('sha256:')}"

    def _semantic_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "plan_id": self.plan_id,
            "plan_digest": self.plan_digest,
            "plan_receipt_digest": self.plan_receipt_digest,
            "executor_binding_digest": self.executor_binding_digest,
            "run_request_digest": self.run_request_digest,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "adapter_manifest_digest": self.adapter_manifest_digest,
            "native_topology_digest": self.native_topology_digest,
            "context_digest": self.context_digest,
            "context_bindings": dict(self.context_bindings),
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "metric_calculator_id": self.metric_calculator_id,
            "metric_calculator_version": self.metric_calculator_version,
            "capital_constrained": self.capital_constrained,
            "point_in_time_required": self.point_in_time_required,
            "experiment": self.experiment.to_dict(),
            "parent_raw_result_digests": dict(self.parent_raw_result_digests),
            "consumers": [item.to_dict() for item in self.consumers],
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._semantic_dict()
        payload["work_id"] = self.work_id
        payload["digest"] = self.digest
        return payload

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


@dataclass(frozen=True)
class RawConditionSlice:
    condition_id: str
    condition_spec_digest: str
    label: str
    sample_count: int
    baseline_metrics: Mapping[str, Decimal]
    intervention_metrics: Mapping[str, Decimal]

    def __post_init__(self) -> None:
        _require_text(self.condition_id, "condition_id")
        _require_digest(self.condition_spec_digest, "condition_spec_digest")
        _require_text(self.label, "label")
        if not isinstance(self.sample_count, int) or isinstance(self.sample_count, bool):
            raise AttributionError("invalid_sample_count", "sample_count must be an integer")
        if self.sample_count <= 0:
            raise AttributionError("invalid_sample_count", "sample_count must be positive")
        object.__setattr__(
            self,
            "baseline_metrics",
            _decimal_mapping(self.baseline_metrics, "baseline_metrics"),
        )
        object.__setattr__(
            self,
            "intervention_metrics",
            _decimal_mapping(self.intervention_metrics, "intervention_metrics"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "condition_spec_digest": self.condition_spec_digest,
            "label": self.label,
            "sample_count": self.sample_count,
            "baseline_metrics": dict(self.baseline_metrics),
            "intervention_metrics": dict(self.intervention_metrics),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> RawConditionSlice:
        _require_exact_fields(
            raw,
            {
                "condition_id",
                "condition_spec_digest",
                "label",
                "sample_count",
                "baseline_metrics",
                "intervention_metrics",
            },
            "raw_condition_field_mismatch",
        )
        baseline = raw["baseline_metrics"]
        intervention = raw["intervention_metrics"]
        if not isinstance(baseline, dict) or not isinstance(intervention, dict):
            raise AttributionError("invalid_raw_condition", "condition metrics are invalid")
        try:
            return cls(
                condition_id=raw["condition_id"],
                condition_spec_digest=raw["condition_spec_digest"],
                label=raw["label"],
                sample_count=raw["sample_count"],
                baseline_metrics={key: Decimal(value) for key, value in baseline.items()},
                intervention_metrics={key: Decimal(value) for key, value in intervention.items()},
            )
        except (TypeError, ValueError) as exc:
            raise AttributionError(
                "invalid_raw_condition", "condition metrics are invalid"
            ) from exc


@dataclass(frozen=True)
class RawAnalysisCheck:
    kind: AnalysisCheckKind
    method: str
    completed: bool
    passed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.kind, AnalysisCheckKind):
            raise AttributionError("invalid_check_kind", "raw check kind is invalid")
        _require_text(self.method, "analysis_check_method")
        if not isinstance(self.completed, bool) or not isinstance(self.passed, bool):
            raise AttributionError("invalid_boolean", "raw check flags must be boolean")
        if self.passed and not self.completed:
            raise AttributionError("invalid_analysis_check", "an incomplete check cannot pass")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "method": self.method,
            "completed": self.completed,
            "passed": self.passed,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> RawAnalysisCheck:
        _require_exact_fields(
            raw,
            {"kind", "method", "completed", "passed"},
            "raw_analysis_check_field_mismatch",
        )
        try:
            return cls(
                kind=AnalysisCheckKind(raw["kind"]),
                method=raw["method"],
                completed=raw["completed"],
                passed=raw["passed"],
            )
        except (TypeError, ValueError) as exc:
            if isinstance(exc, AttributionError):
                raise
            raise AttributionError("invalid_raw_analysis_check", "check is invalid") from exc


def raw_observation_basis(
    source_kind: str,
    work_digest: str,
    planned_experiment_digest: str,
    metrics: Mapping[str, Decimal],
    condition_slices: Sequence[RawConditionSlice],
    analysis_check: RawAnalysisCheck | None,
) -> dict[str, Any]:
    """Build the verifier-owned observation basis used by trusted executors."""

    if source_kind == SYNTHETIC_SOURCE_KIND:
        return {
            "fixture": "synthetic_raw_execution_not_strategy_evidence",
            "work_digest": work_digest,
            "planned_experiment_digest": planned_experiment_digest,
            "metrics": dict(metrics),
            "condition_slices": [item.to_dict() for item in condition_slices],
        }
    return {
        "source_kind": source_kind,
        "work_digest": work_digest,
        "planned_experiment_digest": planned_experiment_digest,
        "metrics": dict(metrics),
        "condition_slices": [item.to_dict() for item in condition_slices],
        "analysis_check": None if analysis_check is None else analysis_check.to_dict(),
    }


@dataclass(frozen=True)
class RawExecutionResult:
    work_id: str
    work_digest: str
    executor_binding_digest: str
    planned_experiment_id: str
    planned_experiment_digest: str
    node_kind: PlanNodeKind
    parent_raw_result_digests: Mapping[str, str]
    actual_execution_scope_digest: str
    actual_component_ids: tuple[str, ...]
    actual_intervention_ids: tuple[str, ...]
    metric_spec_digests: Mapping[str, str]
    metrics: Mapping[str, Decimal]
    condition_slices: tuple[RawConditionSlice, ...]
    replay_reference_digest: str
    replay_observation_digest: str
    reconciliation_reference_digest: str
    reconciliation_observation_digest: str
    decision_trace_reference_digest: str
    decision_trace_observation_digest: str
    opportunity_trace_reference_digest: str
    opportunity_trace_observation_digest: str
    pit_context_digest: str
    authorization_scope_digest: str
    capital_constrained_observed: bool
    trace_payload: Mapping[str, Any]
    analysis_check: RawAnalysisCheck | None = None
    schema_version: str = RAW_EXECUTION_SCHEMA

    def __post_init__(self) -> None:
        for field_name in (
            "work_id",
            "planned_experiment_id",
            "schema_version",
        ):
            _require_text(getattr(self, field_name), field_name)
        for field_name in (
            "work_digest",
            "executor_binding_digest",
            "planned_experiment_digest",
            "actual_execution_scope_digest",
            "replay_reference_digest",
            "replay_observation_digest",
            "reconciliation_reference_digest",
            "reconciliation_observation_digest",
            "decision_trace_reference_digest",
            "decision_trace_observation_digest",
            "opportunity_trace_reference_digest",
            "opportunity_trace_observation_digest",
            "pit_context_digest",
            "authorization_scope_digest",
        ):
            _require_digest(getattr(self, field_name), field_name)
        if self.schema_version != RAW_EXECUTION_SCHEMA:
            raise AttributionError("raw_execution_schema_mismatch", "schema is unsupported")
        if not isinstance(self.node_kind, PlanNodeKind):
            raise AttributionError("invalid_plan_node_kind", "raw node kind is invalid")
        parents = _text_mapping(self.parent_raw_result_digests, "parent_raw_result_digests")
        for value in parents.values():
            _require_digest(value, "parent_raw_result_digest")
        object.__setattr__(self, "parent_raw_result_digests", parents)
        object.__setattr__(
            self,
            "actual_component_ids",
            _unique_text(self.actual_component_ids, "actual_component_ids"),
        )
        object.__setattr__(
            self,
            "actual_intervention_ids",
            _unique_text(self.actual_intervention_ids, "actual_intervention_ids"),
        )
        specs = _text_mapping(self.metric_spec_digests, "metric_spec_digests")
        for value in specs.values():
            _require_digest(value, "metric_spec_digest")
        object.__setattr__(self, "metric_spec_digests", specs)
        object.__setattr__(self, "metrics", _decimal_mapping(self.metrics, "metrics"))
        slices = tuple(self.condition_slices)
        if any(not isinstance(item, RawConditionSlice) for item in slices):
            raise AttributionError("invalid_raw_condition", "condition slices are invalid")
        if len({item.condition_spec_digest for item in slices}) != len(slices):
            raise AttributionError("duplicate_raw_condition", "condition slices must be unique")
        object.__setattr__(
            self,
            "condition_slices",
            tuple(sorted(slices, key=lambda item: item.condition_spec_digest)),
        )
        if self.analysis_check is not None and not isinstance(
            self.analysis_check, RawAnalysisCheck
        ):
            raise AttributionError("invalid_raw_analysis_check", "analysis check is invalid")
        if not isinstance(self.capital_constrained_observed, bool):
            raise AttributionError(
                "invalid_boolean", "capital_constrained_observed must be boolean"
            )
        _reject_reserved_trace_keys(self.trace_payload)
        object.__setattr__(
            self,
            "trace_payload",
            _json_mapping(self.trace_payload, "trace_payload"),
        )

    @property
    def digest(self) -> str:
        return digest(self._semantic_dict())

    @property
    def result_id(self) -> str:
        return f"raw-execution:{self.digest.removeprefix('sha256:')}"

    def _semantic_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "work_id": self.work_id,
            "work_digest": self.work_digest,
            "executor_binding_digest": self.executor_binding_digest,
            "planned_experiment_id": self.planned_experiment_id,
            "planned_experiment_digest": self.planned_experiment_digest,
            "node_kind": self.node_kind.value,
            "parent_raw_result_digests": dict(self.parent_raw_result_digests),
            "actual_execution_scope_digest": self.actual_execution_scope_digest,
            "actual_component_ids": list(self.actual_component_ids),
            "actual_intervention_ids": list(self.actual_intervention_ids),
            "metric_spec_digests": dict(self.metric_spec_digests),
            "metrics": dict(self.metrics),
            "condition_slices": [item.to_dict() for item in self.condition_slices],
            "analysis_check": (
                None if self.analysis_check is None else self.analysis_check.to_dict()
            ),
            "replay_reference_digest": self.replay_reference_digest,
            "replay_observation_digest": self.replay_observation_digest,
            "reconciliation_reference_digest": self.reconciliation_reference_digest,
            "reconciliation_observation_digest": self.reconciliation_observation_digest,
            "decision_trace_reference_digest": self.decision_trace_reference_digest,
            "decision_trace_observation_digest": self.decision_trace_observation_digest,
            "opportunity_trace_reference_digest": self.opportunity_trace_reference_digest,
            "opportunity_trace_observation_digest": self.opportunity_trace_observation_digest,
            "pit_context_digest": self.pit_context_digest,
            "authorization_scope_digest": self.authorization_scope_digest,
            "capital_constrained_observed": self.capital_constrained_observed,
            "trace_payload": dict(self.trace_payload),
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._semantic_dict()
        payload["result_id"] = self.result_id
        payload["digest"] = self.digest
        return payload

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())

    @classmethod
    def from_bytes(cls, payload: bytes) -> RawExecutionResult:
        raw = _parse_json_object(payload, "invalid_raw_execution_json")
        expected = {
            "schema_version",
            "work_id",
            "work_digest",
            "executor_binding_digest",
            "planned_experiment_id",
            "planned_experiment_digest",
            "node_kind",
            "parent_raw_result_digests",
            "actual_execution_scope_digest",
            "actual_component_ids",
            "actual_intervention_ids",
            "metric_spec_digests",
            "metrics",
            "condition_slices",
            "analysis_check",
            "replay_reference_digest",
            "replay_observation_digest",
            "reconciliation_reference_digest",
            "reconciliation_observation_digest",
            "decision_trace_reference_digest",
            "decision_trace_observation_digest",
            "opportunity_trace_reference_digest",
            "opportunity_trace_observation_digest",
            "pit_context_digest",
            "authorization_scope_digest",
            "capital_constrained_observed",
            "trace_payload",
            "result_id",
            "digest",
        }
        _require_exact_fields(raw, expected, "raw_execution_field_mismatch")
        mapping_fields = (
            "parent_raw_result_digests",
            "metric_spec_digests",
            "metrics",
            "trace_payload",
        )
        sequence_fields = (
            "actual_component_ids",
            "actual_intervention_ids",
            "condition_slices",
        )
        if any(not isinstance(raw[item], dict) for item in mapping_fields) or any(
            not isinstance(raw[item], list) for item in sequence_fields
        ):
            raise AttributionError("invalid_raw_execution_json", "collections are invalid")
        try:
            result = cls(
                schema_version=raw["schema_version"],
                work_id=raw["work_id"],
                work_digest=raw["work_digest"],
                executor_binding_digest=raw["executor_binding_digest"],
                planned_experiment_id=raw["planned_experiment_id"],
                planned_experiment_digest=raw["planned_experiment_digest"],
                node_kind=PlanNodeKind(raw["node_kind"]),
                parent_raw_result_digests=raw["parent_raw_result_digests"],
                actual_execution_scope_digest=raw["actual_execution_scope_digest"],
                actual_component_ids=tuple(raw["actual_component_ids"]),
                actual_intervention_ids=tuple(raw["actual_intervention_ids"]),
                metric_spec_digests=raw["metric_spec_digests"],
                metrics={key: Decimal(value) for key, value in raw["metrics"].items()},
                condition_slices=tuple(
                    RawConditionSlice.from_dict(item) for item in raw["condition_slices"]
                ),
                analysis_check=(
                    None
                    if raw["analysis_check"] is None
                    else RawAnalysisCheck.from_dict(raw["analysis_check"])
                ),
                replay_reference_digest=raw["replay_reference_digest"],
                replay_observation_digest=raw["replay_observation_digest"],
                reconciliation_reference_digest=raw["reconciliation_reference_digest"],
                reconciliation_observation_digest=raw["reconciliation_observation_digest"],
                decision_trace_reference_digest=raw["decision_trace_reference_digest"],
                decision_trace_observation_digest=raw["decision_trace_observation_digest"],
                opportunity_trace_reference_digest=raw["opportunity_trace_reference_digest"],
                opportunity_trace_observation_digest=raw["opportunity_trace_observation_digest"],
                pit_context_digest=raw["pit_context_digest"],
                authorization_scope_digest=raw["authorization_scope_digest"],
                capital_constrained_observed=raw["capital_constrained_observed"],
                trace_payload=raw["trace_payload"],
            )
        except (TypeError, ValueError) as exc:
            if isinstance(exc, AttributionError):
                raise
            raise AttributionError("invalid_raw_execution_json", "values are invalid") from exc
        if raw["result_id"] != result.result_id:
            raise AttributionError("raw_execution_identity_mismatch", "identity is invalid")
        if raw["digest"] != result.digest:
            raise AttributionError("raw_execution_digest_mismatch", "digest is invalid")
        return result


@runtime_checkable
class AttributionExperimentExecutor(Protocol):
    def binding(self) -> ExecutorBinding: ...

    def execute(self, work: PlannedExecutionWork) -> RawExecutionResult: ...


@dataclass(frozen=True)
class PipelineStageReceipt:
    run_id: str
    state: PipelineRunState
    stage: str
    subject_id: str
    parent_digests: tuple[str, ...] = ()
    output_digests: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    schema_version: str = PIPELINE_RECEIPT_SCHEMA
    pipeline_id: str = PIPELINE_ID
    pipeline_version: str = PIPELINE_VERSION

    def __post_init__(self) -> None:
        for field_name in (
            "run_id",
            "stage",
            "subject_id",
            "schema_version",
            "pipeline_id",
            "pipeline_version",
        ):
            _require_text(getattr(self, field_name), field_name)
        if self.schema_version != PIPELINE_RECEIPT_SCHEMA:
            raise AttributionError("pipeline_receipt_schema_mismatch", "schema is unsupported")
        if (self.pipeline_id, self.pipeline_version) != (PIPELINE_ID, PIPELINE_VERSION):
            raise AttributionError("pipeline_version_mismatch", "pipeline version is unsupported")
        if not isinstance(self.state, PipelineRunState):
            raise AttributionError("invalid_pipeline_state", "state is invalid")
        for field_name in ("parent_digests", "output_digests"):
            values = _unique_text(getattr(self, field_name), field_name, sort_values=True)
            for value in values:
                _require_digest(value, field_name)
            object.__setattr__(self, field_name, values)
        reasons = _unique_text(self.reason_codes, "reason_codes", sort_values=True)
        if self.state in {PipelineRunState.FAILED, PipelineRunState.VERIFICATION_FAILED}:
            if not reasons:
                raise AttributionError("missing_failure_reason", "failure receipt needs a reason")
        elif reasons:
            raise AttributionError("unexpected_failure_reason", "success receipt has reasons")
        object.__setattr__(self, "reason_codes", reasons)

    @property
    def digest(self) -> str:
        return digest(self._semantic_dict())

    @property
    def receipt_id(self) -> str:
        return f"pipeline-receipt:{self.digest.removeprefix('sha256:')}"

    def _semantic_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "pipeline_id": self.pipeline_id,
            "pipeline_version": self.pipeline_version,
            "run_id": self.run_id,
            "state": self.state.value,
            "stage": self.stage,
            "subject_id": self.subject_id,
            "parent_digests": list(self.parent_digests),
            "output_digests": list(self.output_digests),
            "reason_codes": list(self.reason_codes),
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._semantic_dict()
        payload["receipt_id"] = self.receipt_id
        payload["digest"] = self.digest
        return payload

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())

    @classmethod
    def from_bytes(cls, payload: bytes) -> PipelineStageReceipt:
        raw = _parse_json_object(payload, "invalid_pipeline_receipt_json")
        _require_exact_fields(
            raw,
            {
                "schema_version",
                "pipeline_id",
                "pipeline_version",
                "run_id",
                "state",
                "stage",
                "subject_id",
                "parent_digests",
                "output_digests",
                "reason_codes",
                "receipt_id",
                "digest",
            },
            "pipeline_receipt_field_mismatch",
        )
        for field_name in ("parent_digests", "output_digests", "reason_codes"):
            if not isinstance(raw[field_name], list):
                raise AttributionError(
                    "invalid_pipeline_receipt_json", f"{field_name} must be a list"
                )
        try:
            receipt = cls(
                schema_version=raw["schema_version"],
                pipeline_id=raw["pipeline_id"],
                pipeline_version=raw["pipeline_version"],
                run_id=raw["run_id"],
                state=PipelineRunState(raw["state"]),
                stage=raw["stage"],
                subject_id=raw["subject_id"],
                parent_digests=tuple(raw["parent_digests"]),
                output_digests=tuple(raw["output_digests"]),
                reason_codes=tuple(raw["reason_codes"]),
            )
        except (TypeError, ValueError) as exc:
            if isinstance(exc, AttributionError):
                raise
            raise AttributionError("invalid_pipeline_receipt_json", "values are invalid") from exc
        if raw["receipt_id"] != receipt.receipt_id:
            raise AttributionError("pipeline_receipt_identity_mismatch", "identity is invalid")
        if raw["digest"] != receipt.digest:
            raise AttributionError("pipeline_receipt_digest_mismatch", "digest is invalid")
        return receipt


@dataclass(frozen=True)
class NodeVerificationRecord:
    run_id: str
    planned_experiment_id: str
    planned_experiment_digest: str
    work_digest: str
    raw_execution_digest: str
    plan_receipt_digest: str
    dependency_verification_digests: Mapping[str, str]
    passed: bool
    reason_codes: tuple[str, ...]
    experiment_manifest_digests: tuple[str, ...] = ()
    condition_comparison_digests: tuple[str, ...] = ()
    analysis_check_digests: tuple[str, ...] = ()
    artifact_digests: tuple[str, ...] = ()
    schema_version: str = NODE_VERIFICATION_SCHEMA
    verifier_id: str = PIPELINE_VERIFIER_ID
    verifier_version: str = PIPELINE_VERIFIER_VERSION

    def __post_init__(self) -> None:
        for field_name in (
            "run_id",
            "planned_experiment_id",
            "schema_version",
            "verifier_id",
            "verifier_version",
        ):
            _require_text(getattr(self, field_name), field_name)
        for field_name in (
            "planned_experiment_digest",
            "work_digest",
            "raw_execution_digest",
            "plan_receipt_digest",
        ):
            _require_digest(getattr(self, field_name), field_name)
        if self.schema_version != NODE_VERIFICATION_SCHEMA:
            raise AttributionError("node_verification_schema_mismatch", "schema is unsupported")
        if (self.verifier_id, self.verifier_version) != (
            PIPELINE_VERIFIER_ID,
            PIPELINE_VERIFIER_VERSION,
        ):
            raise AttributionError("pipeline_verifier_mismatch", "verifier is unsupported")
        dependencies = _text_mapping(
            self.dependency_verification_digests,
            "dependency_verification_digests",
        )
        for value in dependencies.values():
            _require_digest(value, "dependency_verification_digest")
        object.__setattr__(self, "dependency_verification_digests", dependencies)
        if not isinstance(self.passed, bool):
            raise AttributionError("invalid_boolean", "passed must be boolean")
        reasons = _unique_text(self.reason_codes, "reason_codes", sort_values=True)
        if self.passed == bool(reasons):
            raise AttributionError(
                "invalid_node_verification",
                "passing records have no reasons and failures require reasons",
            )
        object.__setattr__(self, "reason_codes", reasons)
        for field_name in (
            "experiment_manifest_digests",
            "condition_comparison_digests",
            "analysis_check_digests",
            "artifact_digests",
        ):
            values = _unique_text(getattr(self, field_name), field_name, sort_values=True)
            for value in values:
                _require_digest(value, field_name)
            object.__setattr__(self, field_name, values)
        outputs = (
            self.experiment_manifest_digests,
            self.condition_comparison_digests,
            self.analysis_check_digests,
            self.artifact_digests,
        )
        if self.passed and not any(outputs):
            raise AttributionError(
                "empty_node_verification", "passing verification must bind outputs"
            )
        if not self.passed and any(outputs):
            raise AttributionError(
                "failed_node_has_outputs", "failed verification cannot expose evidence"
            )

    @property
    def digest(self) -> str:
        return digest(self._semantic_dict())

    def _semantic_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "verifier_id": self.verifier_id,
            "verifier_version": self.verifier_version,
            "run_id": self.run_id,
            "planned_experiment_id": self.planned_experiment_id,
            "planned_experiment_digest": self.planned_experiment_digest,
            "work_digest": self.work_digest,
            "raw_execution_digest": self.raw_execution_digest,
            "plan_receipt_digest": self.plan_receipt_digest,
            "dependency_verification_digests": dict(self.dependency_verification_digests),
            "passed": self.passed,
            "reason_codes": list(self.reason_codes),
            "experiment_manifest_digests": list(self.experiment_manifest_digests),
            "condition_comparison_digests": list(self.condition_comparison_digests),
            "analysis_check_digests": list(self.analysis_check_digests),
            "artifact_digests": list(self.artifact_digests),
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._semantic_dict()
        payload["digest"] = self.digest
        return payload

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())

    @classmethod
    def from_bytes(cls, payload: bytes) -> NodeVerificationRecord:
        raw = _parse_json_object(payload, "invalid_node_verification_json")
        _require_exact_fields(
            raw,
            {
                "schema_version",
                "verifier_id",
                "verifier_version",
                "run_id",
                "planned_experiment_id",
                "planned_experiment_digest",
                "work_digest",
                "raw_execution_digest",
                "plan_receipt_digest",
                "dependency_verification_digests",
                "passed",
                "reason_codes",
                "experiment_manifest_digests",
                "condition_comparison_digests",
                "analysis_check_digests",
                "artifact_digests",
                "digest",
            },
            "node_verification_field_mismatch",
        )
        sequence_fields = (
            "reason_codes",
            "experiment_manifest_digests",
            "condition_comparison_digests",
            "analysis_check_digests",
            "artifact_digests",
        )
        if not isinstance(raw["dependency_verification_digests"], dict) or any(
            not isinstance(raw[field_name], list) for field_name in sequence_fields
        ):
            raise AttributionError(
                "invalid_node_verification_json",
                "node verification collections are invalid",
            )
        try:
            record = cls(
                schema_version=raw["schema_version"],
                verifier_id=raw["verifier_id"],
                verifier_version=raw["verifier_version"],
                run_id=raw["run_id"],
                planned_experiment_id=raw["planned_experiment_id"],
                planned_experiment_digest=raw["planned_experiment_digest"],
                work_digest=raw["work_digest"],
                raw_execution_digest=raw["raw_execution_digest"],
                plan_receipt_digest=raw["plan_receipt_digest"],
                dependency_verification_digests=raw[
                    "dependency_verification_digests"
                ],
                passed=raw["passed"],
                reason_codes=tuple(raw["reason_codes"]),
                experiment_manifest_digests=tuple(
                    raw["experiment_manifest_digests"]
                ),
                condition_comparison_digests=tuple(
                    raw["condition_comparison_digests"]
                ),
                analysis_check_digests=tuple(raw["analysis_check_digests"]),
                artifact_digests=tuple(raw["artifact_digests"]),
            )
        except (TypeError, ValueError) as exc:
            if isinstance(exc, AttributionError):
                raise
            raise AttributionError(
                "invalid_node_verification_json", "values are invalid"
            ) from exc
        if raw["digest"] != record.digest:
            raise AttributionError(
                "node_verification_digest_mismatch", "digest is invalid"
            )
        return record


@dataclass(frozen=True)
class VerifiedNodeProjection:
    record: NodeVerificationRecord
    experiments: tuple[ExperimentManifest, ...] = ()
    conditions: tuple[ConditionComparison, ...] = ()
    analysis_checks: tuple[AnalysisCheckEvidence, ...] = ()
    artifacts: tuple[SemanticArtifactEnvelope, ...] = ()

    def __post_init__(self) -> None:
        for field_name, item_type in (
            ("experiments", ExperimentManifest),
            ("conditions", ConditionComparison),
            ("analysis_checks", AnalysisCheckEvidence),
            ("artifacts", SemanticArtifactEnvelope),
        ):
            values = tuple(getattr(self, field_name))
            if any(not isinstance(item, item_type) for item in values):
                raise AttributionError("invalid_verified_projection", field_name)
            object.__setattr__(self, field_name, values)


@dataclass(frozen=True)
class EvaluationBundle:
    run_id: str
    plan_digest: str
    source_kind: str
    node_verification_digests: tuple[str, ...]
    claims: tuple[Mapping[str, Any], ...]
    interactions: tuple[Mapping[str, Any], ...]
    kernel_receipts: tuple[Mapping[str, Any], ...]
    schema_version: str = EVALUATION_BUNDLE_SCHEMA
    evaluator_id: str = PIPELINE_EVALUATOR_ID
    evaluator_version: str = PIPELINE_EVALUATOR_VERSION

    def __post_init__(self) -> None:
        for field_name in (
            "run_id",
            "source_kind",
            "schema_version",
            "evaluator_id",
            "evaluator_version",
        ):
            _require_text(getattr(self, field_name), field_name)
        _require_digest(self.plan_digest, "plan_digest")
        if self.schema_version != EVALUATION_BUNDLE_SCHEMA:
            raise AttributionError("evaluation_bundle_schema_mismatch", "schema is unsupported")
        if (self.evaluator_id, self.evaluator_version) != (
            PIPELINE_EVALUATOR_ID,
            PIPELINE_EVALUATOR_VERSION,
        ):
            raise AttributionError("pipeline_evaluator_mismatch", "evaluator is unsupported")
        verification_digests = _unique_text(
            self.node_verification_digests,
            "node_verification_digests",
            sort_values=True,
        )
        for verification_digest in verification_digests:
            _require_digest(verification_digest, "node_verification_digest")
        object.__setattr__(self, "node_verification_digests", verification_digests)
        for field_name, identity_field in (
            ("claims", "claim_id"),
            ("interactions", "digest"),
            ("kernel_receipts", "digest"),
        ):
            values = tuple(
                _json_mapping(item, f"{field_name}[{index}]")
                for index, item in enumerate(getattr(self, field_name))
            )
            identities = tuple(item.get(identity_field) for item in values)
            if any(not isinstance(item, str) or not item for item in identities):
                raise AttributionError(
                    "invalid_evaluation_bundle",
                    f"{field_name} contains an invalid identity",
                )
            if len(identities) != len(set(identities)):
                raise AttributionError(
                    "duplicate_evaluation_output",
                    f"{field_name} contains duplicate identities",
                )
            object.__setattr__(
                self,
                field_name,
                tuple(sorted(values, key=lambda item: str(item[identity_field]))),
            )
        if any(item.get("status") != ClaimStatus.VALID.value for item in self.claims):
            raise AttributionError(
                "invalid_evaluation_claim",
                "evaluation bundles only contain kernel-valid claims",
            )

    @property
    def digest(self) -> str:
        return digest(self._semantic_dict())

    def _semantic_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evaluator_id": self.evaluator_id,
            "evaluator_version": self.evaluator_version,
            "run_id": self.run_id,
            "plan_digest": self.plan_digest,
            "source_kind": self.source_kind,
            "node_verification_digests": list(self.node_verification_digests),
            "claims": [dict(item) for item in self.claims],
            "interactions": [dict(item) for item in self.interactions],
            "kernel_receipts": [dict(item) for item in self.kernel_receipts],
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._semantic_dict()
        payload["digest"] = self.digest
        return payload

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())

    @classmethod
    def from_bytes(cls, payload: bytes) -> EvaluationBundle:
        raw = _parse_json_object(payload, "invalid_evaluation_bundle_json")
        _require_exact_fields(
            raw,
            {
                "schema_version",
                "evaluator_id",
                "evaluator_version",
                "run_id",
                "plan_digest",
                "source_kind",
                "node_verification_digests",
                "claims",
                "interactions",
                "kernel_receipts",
                "digest",
            },
            "evaluation_bundle_field_mismatch",
        )
        sequence_fields = (
            "node_verification_digests",
            "claims",
            "interactions",
            "kernel_receipts",
        )
        if any(not isinstance(raw[field_name], list) for field_name in sequence_fields):
            raise AttributionError(
                "invalid_evaluation_bundle_json",
                "evaluation bundle collections are invalid",
            )
        if any(
            not isinstance(item, dict)
            for field_name in ("claims", "interactions", "kernel_receipts")
            for item in raw[field_name]
        ):
            raise AttributionError(
                "invalid_evaluation_bundle_json",
                "evaluation outputs must be objects",
            )
        try:
            bundle = cls(
                schema_version=raw["schema_version"],
                evaluator_id=raw["evaluator_id"],
                evaluator_version=raw["evaluator_version"],
                run_id=raw["run_id"],
                plan_digest=raw["plan_digest"],
                source_kind=raw["source_kind"],
                node_verification_digests=tuple(raw["node_verification_digests"]),
                claims=tuple(raw["claims"]),
                interactions=tuple(raw["interactions"]),
                kernel_receipts=tuple(raw["kernel_receipts"]),
            )
        except (TypeError, ValueError) as exc:
            if isinstance(exc, AttributionError):
                raise
            raise AttributionError(
                "invalid_evaluation_bundle_json", "values are invalid"
            ) from exc
        if raw["digest"] != bundle.digest:
            raise AttributionError("evaluation_bundle_digest_mismatch", "digest is invalid")
        return bundle


class ImmutableRunStore:
    """Exclusive immutable writes with byte-for-byte idempotency."""

    def __init__(self, root: Path, run_id: str) -> None:
        base = Path(root)
        candidate = Path(run_id)
        if (
            candidate.is_absolute()
            or len(candidate.parts) != 1
            or candidate.parts[0] in {".", ".."}
        ):
            raise AttributionError("invalid_store_path", "run identity must be one path segment")
        base.mkdir(parents=True, exist_ok=True)
        if base.is_symlink():
            raise AttributionError("invalid_store_path", "store root cannot be a symbolic link")
        self.root = base / candidate
        if self.root.is_symlink():
            raise AttributionError("invalid_store_path", "run root cannot be a symbolic link")
        self.root.mkdir(parents=True, exist_ok=True)
        self._resolved_root = self.root.resolve(strict=True)

    def path(self, relative: str) -> Path:
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise AttributionError("invalid_store_path", "store path must be relative")
        target = self.root / candidate
        current = self.root
        for part in candidate.parts:
            current = current / part
            if current.is_symlink():
                raise AttributionError(
                    "invalid_store_path", "store paths cannot traverse symbolic links"
                )
        try:
            target.parent.resolve(strict=False).relative_to(self._resolved_root)
        except ValueError as exc:
            raise AttributionError(
                "invalid_store_path", "store path escapes the immutable run root"
            ) from exc
        return target

    def exists(self, relative: str) -> bool:
        return self.path(relative).is_file()

    def read(self, relative: str) -> bytes:
        try:
            return self.path(relative).read_bytes()
        except OSError as exc:
            raise AttributionError("pipeline_artifact_unreadable", relative) from exc

    def write(self, relative: str, payload: bytes) -> Path:
        if not isinstance(payload, bytes):
            raise AttributionError("invalid_store_payload", "payload must be bytes")
        target = self.path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary_path, target)
            except FileExistsError:
                existing = target.read_bytes()
                if existing != payload:
                    raise AttributionError(
                        "immutable_stage_conflict",
                        f"{relative} already contains different bytes",
                    ) from None
            self._fsync_directory(target.parent)
            return target
        finally:
            if temporary_path is not None:
                with suppress(FileNotFoundError):
                    temporary_path.unlink()

    @contextmanager
    def lock(self):
        lock_path = self.path(".pipeline.lock")
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

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


@dataclass(frozen=True)
class PlannedPipelineRun:
    run_id: str
    run_dir: Path
    request: AttributionRunRequest
    plan: AttributionPlan
    planned_receipt: PipelineStageReceipt


@dataclass(frozen=True)
class AttributionPipelineResult:
    run_id: str
    run_dir: Path
    state: PipelineRunState
    plan: AttributionPlan
    claims: tuple[AttributionClaim, ...]
    experiments: tuple[ExperimentManifest, ...]
    conditions: tuple[ConditionComparison, ...]
    interactions: tuple[InteractionComparison, ...]
    analysis_checks: tuple[AnalysisCheckEvidence, ...]
    artifacts: tuple[SemanticArtifactEnvelope, ...]
    evaluation_receipt: PipelineStageReceipt
    evaluation_bundle: EvaluationBundle
    source_kind: str

    @property
    def publishable_claims(self) -> tuple[AttributionClaim, ...]:
        if self.source_kind == SYNTHETIC_SOURCE_KIND:
            return ()
        return tuple(item for item in self.claims if item.status is ClaimStatus.VALID)


@dataclass(frozen=True)
class PipelineStatusSnapshot:
    run_id: str
    run_dir: Path
    state: PipelineRunState
    plan_digest: str | None
    total_nodes: int
    executed_nodes: int
    verified_nodes: int
    claim_count: int
    reason_codes: tuple[str, ...] = ()


def _run_id(request: AttributionRunRequest) -> str:
    return f"attribution-run-{request.digest.removeprefix('sha256:')[:24]}"


def _node_token(experiment: PlannedExperiment) -> str:
    return experiment.digest.removeprefix("sha256:")


def _consumer_bindings(plan: AttributionPlan) -> Mapping[str, tuple[ExecutionConsumer, ...]]:
    consumers: dict[str, list[ExecutionConsumer]] = {
        item.experiment_id: [] for item in plan.experiments
    }
    for question in plan.planned_questions:
        if question.status is not QuestionPlanStatus.READY:
            continue
        for binding in question.experiment_bindings:
            consumers[binding.experiment_id].append(
                ExecutionConsumer(
                    question_id=question.question_id,
                    question_digest=question.question_digest,
                    binding_kind=binding.kind,
                    specification_digest=binding.specification_digest,
                )
            )
    return MappingProxyType({key: tuple(value) for key, value in sorted(consumers.items())})


def _experiment_from_artifact(artifact: SemanticArtifactEnvelope) -> ExperimentManifest:
    if artifact.artifact_kind is not ArtifactKind.EXPERIMENT_RESULT:
        raise AttributionError("projection_artifact_kind_mismatch", "not an experiment result")
    raw = dict(artifact.payload)
    _require_exact_fields(
        raw,
        {
            "experiment_id",
            "schema_version",
            "question_id",
            "question_digest",
            "role",
            "strategy_id",
            "strategy_version",
            "adapter_id",
            "adapter_version",
            "capability_manifest_version",
            "capability_manifest_digest",
            "native_topology_digest",
            "context_digest",
            "context_bindings",
            "native_trace_digest",
            "plan_receipt_digest",
            "execution_work_digest",
            "raw_execution_digest",
            "metric_calculator_id",
            "metric_calculator_version",
            "metric_spec_digests",
            "period_start",
            "period_end",
            "intervention_ids",
            "changed_component_ids",
            "capital_constrained",
            "reproduced",
            "reconciled",
            "pit_validated",
            "partition_authorized",
            "question_bound_before_outcomes",
            "decision_trace_reconciled",
            "opportunity_trace_reconciled",
            "interaction_spec_digest",
            "metrics",
        },
        "experiment_artifact_field_mismatch",
    )
    mapping_fields = ("context_bindings", "metric_spec_digests", "metrics")
    sequence_fields = ("intervention_ids", "changed_component_ids")
    if any(not isinstance(raw[item], dict) for item in mapping_fields) or any(
        not isinstance(raw[item], list) for item in sequence_fields
    ):
        raise AttributionError(
            "invalid_experiment_artifact", "experiment artifact collections are invalid"
        )
    try:
        return ExperimentManifest(
            experiment_id=raw["experiment_id"],
            schema_version=raw["schema_version"],
            question_id=raw["question_id"],
            question_digest=raw["question_digest"],
            role=ExperimentRole(raw["role"]),
            strategy_id=raw["strategy_id"],
            strategy_version=raw["strategy_version"],
            adapter_id=raw["adapter_id"],
            adapter_version=raw["adapter_version"],
            capability_manifest_version=raw["capability_manifest_version"],
            capability_manifest_digest=raw["capability_manifest_digest"],
            native_topology_digest=raw["native_topology_digest"],
            context_digest=raw["context_digest"],
            context_bindings=raw["context_bindings"],
            artifact_digest=artifact.digest,
            native_trace_digest=raw["native_trace_digest"],
            plan_receipt_digest=raw["plan_receipt_digest"],
            execution_work_digest=raw["execution_work_digest"],
            raw_execution_digest=raw["raw_execution_digest"],
            metric_calculator_id=raw["metric_calculator_id"],
            metric_calculator_version=raw["metric_calculator_version"],
            metric_spec_digests=raw["metric_spec_digests"],
            period_start=date.fromisoformat(raw["period_start"]),
            period_end=date.fromisoformat(raw["period_end"]),
            intervention_ids=tuple(raw["intervention_ids"]),
            changed_component_ids=tuple(raw["changed_component_ids"]),
            capital_constrained=raw["capital_constrained"],
            reproduced=raw["reproduced"],
            reconciled=raw["reconciled"],
            pit_validated=raw["pit_validated"],
            partition_authorized=raw["partition_authorized"],
            question_bound_before_outcomes=raw["question_bound_before_outcomes"],
            decision_trace_reconciled=raw["decision_trace_reconciled"],
            opportunity_trace_reconciled=raw["opportunity_trace_reconciled"],
            interaction_spec_digest=raw["interaction_spec_digest"],
            metrics={key: Decimal(value) for key, value in raw["metrics"].items()},
        )
    except (TypeError, ValueError) as exc:
        if isinstance(exc, AttributionError):
            raise
        raise AttributionError(
            "invalid_experiment_artifact", "experiment artifact values are invalid"
        ) from exc


def _condition_from_artifact(artifact: SemanticArtifactEnvelope) -> ConditionComparison:
    if artifact.artifact_kind is not ArtifactKind.CONDITION_ANALYSIS_RESULT:
        raise AttributionError("projection_artifact_kind_mismatch", "not a condition result")
    raw = dict(artifact.payload)
    _require_exact_fields(
        raw,
        {
            "condition_id",
            "condition_spec_digest",
            "label",
            "sample_count",
            "question_digest",
            "adapter_capability_digest",
            "context_digest",
            "period_start",
            "period_end",
            "baseline_experiment_digest",
            "intervention_experiment_digest",
            "plan_receipt_digest",
            "execution_work_digest",
            "raw_execution_digest",
            "reconciled",
            "baseline_metrics",
            "intervention_metrics",
        },
        "condition_artifact_field_mismatch",
    )
    if not isinstance(raw["baseline_metrics"], dict) or not isinstance(
        raw["intervention_metrics"], dict
    ):
        raise AttributionError(
            "invalid_condition_artifact", "condition metrics are invalid"
        )
    try:
        return ConditionComparison(
            condition_id=raw["condition_id"],
            condition_spec_digest=raw["condition_spec_digest"],
            label=raw["label"],
            sample_count=raw["sample_count"],
            question_digest=raw["question_digest"],
            adapter_capability_digest=raw["adapter_capability_digest"],
            context_digest=raw["context_digest"],
            period_start=date.fromisoformat(raw["period_start"]),
            period_end=date.fromisoformat(raw["period_end"]),
            baseline_experiment_digest=raw["baseline_experiment_digest"],
            intervention_experiment_digest=raw["intervention_experiment_digest"],
            result_digest=artifact.digest,
            plan_receipt_digest=raw["plan_receipt_digest"],
            execution_work_digest=raw["execution_work_digest"],
            raw_execution_digest=raw["raw_execution_digest"],
            reconciled=raw["reconciled"],
            baseline_metrics={
                key: Decimal(value) for key, value in raw["baseline_metrics"].items()
            },
            intervention_metrics={
                key: Decimal(value) for key, value in raw["intervention_metrics"].items()
            },
        )
    except (TypeError, ValueError) as exc:
        if isinstance(exc, AttributionError):
            raise
        raise AttributionError(
            "invalid_condition_artifact", "condition artifact values are invalid"
        ) from exc


def _analysis_check_from_artifact(
    artifact: SemanticArtifactEnvelope,
) -> AnalysisCheckEvidence:
    if artifact.artifact_kind is not ArtifactKind.ANALYSIS_CHECK_RESULT:
        raise AttributionError("projection_artifact_kind_mismatch", "not an analysis result")
    raw = dict(artifact.payload)
    _require_exact_fields(
        raw,
        {
            "check_id",
            "kind",
            "method",
            "scope",
            "metric_ids",
            "target_evidence_digest",
            "question_digest",
            "adapter_capability_digest",
            "baseline_experiment_digest",
            "intervention_experiment_digest",
            "completed",
            "passed",
        },
        "analysis_artifact_field_mismatch",
    )
    if not isinstance(raw["metric_ids"], list):
        raise AttributionError("invalid_analysis_artifact", "metric ids are invalid")
    try:
        return AnalysisCheckEvidence(
            check_id=raw["check_id"],
            kind=AnalysisCheckKind(raw["kind"]),
            method=raw["method"],
            scope=AnalysisCheckScope(raw["scope"]),
            metric_ids=tuple(raw["metric_ids"]),
            target_evidence_digest=raw["target_evidence_digest"],
            question_digest=raw["question_digest"],
            adapter_capability_digest=raw["adapter_capability_digest"],
            baseline_experiment_digest=raw["baseline_experiment_digest"],
            intervention_experiment_digest=raw["intervention_experiment_digest"],
            result_digest=artifact.digest,
            completed=raw["completed"],
            passed=raw["passed"],
        )
    except (TypeError, ValueError) as exc:
        if isinstance(exc, AttributionError):
            raise
        raise AttributionError(
            "invalid_analysis_artifact", "analysis artifact values are invalid"
        ) from exc


@runtime_checkable
class AttributionNodeVerifier(Protocol):
    verifier_id: str
    verifier_version: str

    def verify(
        self,
        *,
        request: AttributionRunRequest,
        plan: AttributionPlan,
        planned_receipt: PipelineStageReceipt,
        binding: ExecutorBinding,
        work: PlannedExecutionWork,
        result: RawExecutionResult,
        dependencies: Mapping[str, VerifiedNodeProjection],
    ) -> VerifiedNodeProjection: ...


class DeterministicAttributionNodeVerifier:
    """Derive evidence facts from raw observations and the already persisted plan."""

    verifier_id = PIPELINE_VERIFIER_ID
    verifier_version = PIPELINE_VERIFIER_VERSION
    __slots__ = ("_calls", "_fail_on_calls")

    def __init__(self, *, fail_on_calls: Sequence[int] = ()) -> None:
        self._calls = 0
        self._fail_on_calls = frozenset(fail_on_calls)

    @property
    def calls(self) -> int:
        return self._calls

    def verify(
        self,
        *,
        request: AttributionRunRequest,
        plan: AttributionPlan,
        planned_receipt: PipelineStageReceipt,
        binding: ExecutorBinding,
        work: PlannedExecutionWork,
        result: RawExecutionResult,
        dependencies: Mapping[str, VerifiedNodeProjection],
    ) -> VerifiedNodeProjection:
        self._calls += 1
        if self._calls in self._fail_on_calls:
            raise RuntimeError(f"injected verifier failure {self._calls}")
        reasons = self._binding_reasons(
            request=request,
            plan=plan,
            planned_receipt=planned_receipt,
            binding=binding,
            work=work,
            result=result,
            dependencies=dependencies,
        )
        if reasons:
            return self._failed_projection(work, result, dependencies, reasons)
        if work.experiment.kind in {
            PlanNodeKind.BASELINE_REPLAY,
            PlanNodeKind.SINGLE_INTERVENTION,
            PlanNodeKind.JOINT_INTERVENTION,
        }:
            return self._project_physical(
                request=request,
                binding=binding,
                work=work,
                result=result,
                dependencies=dependencies,
            )
        if work.experiment.kind is PlanNodeKind.CONDITION_ANALYSIS:
            return self._project_condition(
                request=request,
                binding=binding,
                work=work,
                result=result,
                dependencies=dependencies,
            )
        if work.experiment.kind in {
            PlanNodeKind.UNCERTAINTY_CHECK,
            PlanNodeKind.STABILITY_CHECK,
        }:
            return self._project_analysis_check(
                request=request,
                binding=binding,
                work=work,
                result=result,
                dependencies=dependencies,
            )
        return self._failed_projection(
            work,
            result,
            dependencies,
            (f"unsupported_analysis_node:{work.experiment.kind.value}",),
        )

    def _binding_reasons(
        self,
        *,
        request: AttributionRunRequest,
        plan: AttributionPlan,
        planned_receipt: PipelineStageReceipt,
        binding: ExecutorBinding,
        work: PlannedExecutionWork,
        result: RawExecutionResult,
        dependencies: Mapping[str, VerifiedNodeProjection],
    ) -> tuple[str, ...]:
        node = work.experiment
        reasons: list[str] = []
        if planned_receipt.state is not PipelineRunState.PLANNED:
            reasons.append("plan_receipt_state_mismatch")
        if planned_receipt.stage != "planner":
            reasons.append("plan_receipt_stage_mismatch")
        if planned_receipt.run_id != work.run_id:
            reasons.append("plan_receipt_run_mismatch")
        if planned_receipt.digest != work.plan_receipt_digest:
            reasons.append("plan_receipt_digest_mismatch")
        if planned_receipt.parent_digests != (request.digest,):
            reasons.append("plan_receipt_request_mismatch")
        if planned_receipt.output_digests != (plan.digest,):
            reasons.append("plan_receipt_plan_mismatch")
        expected_work_bindings = {
            "run_request_digest": request.digest,
            "plan_id": plan.plan_id,
            "plan_digest": plan.digest,
            "strategy_id": request.adapter_manifest.strategy_id,
            "strategy_version": request.adapter_manifest.strategy_version,
            "adapter_manifest_digest": request.adapter_manifest.digest,
            "native_topology_digest": request.adapter_manifest.native_topology_digest,
            "context_digest": request.context_digest,
            "metric_calculator_id": request.metric_calculator_id,
            "metric_calculator_version": request.metric_calculator_version,
        }
        for field_name, expected in expected_work_bindings.items():
            if getattr(work, field_name) != expected:
                reasons.append(f"work_{field_name}_mismatch")
        if dict(work.context_bindings) != dict(request.context_bindings):
            reasons.append("work_context_binding_mismatch")
        if (work.period_start, work.period_end) != (
            request.period_start,
            request.period_end,
        ):
            reasons.append("work_period_mismatch")
        if work.executor_binding_digest != binding.digest:
            reasons.append("work_executor_binding_mismatch")
        if result.work_id != work.work_id or result.work_digest != work.digest:
            reasons.append("raw_work_mismatch")
        if result.executor_binding_digest != binding.digest:
            reasons.append("raw_executor_binding_mismatch")
        if result.planned_experiment_id != node.experiment_id:
            reasons.append("raw_planned_experiment_id_mismatch")
        if result.planned_experiment_digest != node.digest:
            reasons.append("raw_planned_experiment_digest_mismatch")
        if result.node_kind is not node.kind:
            reasons.append("raw_node_kind_mismatch")
        if dict(result.parent_raw_result_digests) != dict(work.parent_raw_result_digests):
            reasons.append("raw_parent_result_mismatch")
        if result.actual_execution_scope_digest != node.execution_scope_digest:
            reasons.append("raw_execution_scope_mismatch")
        if result.actual_component_ids != node.component_ids:
            reasons.append("raw_component_mismatch")
        if result.actual_intervention_ids != node.intervention_ids:
            reasons.append("raw_intervention_mismatch")
        if dict(result.metric_spec_digests) != dict(node.metric_spec_digests):
            reasons.append("raw_metric_definition_mismatch")
        if result.pit_context_digest != request.context_digest:
            reasons.append("raw_pit_context_mismatch")
        if result.authorization_scope_digest != request.execution_scope_digest:
            reasons.append("raw_authorization_scope_mismatch")
        if result.capital_constrained_observed is not True:
            reasons.append("raw_not_capital_constrained")
        observation_basis = raw_observation_basis(
            binding.source_kind,
            work.digest,
            node.digest,
            result.metrics,
            result.condition_slices,
            result.analysis_check,
        )
        expected_observations = {
            label: digest({**observation_basis, "observation": label})
            for label in (
                "replay",
                "reconciliation",
                "decision_trace",
                "opportunity_trace",
            )
        }
        for label, reference, observation in (
            (
                "replay",
                result.replay_reference_digest,
                result.replay_observation_digest,
            ),
            (
                "reconciliation",
                result.reconciliation_reference_digest,
                result.reconciliation_observation_digest,
            ),
            (
                "decision_trace",
                result.decision_trace_reference_digest,
                result.decision_trace_observation_digest,
            ),
            (
                "opportunity_trace",
                result.opportunity_trace_reference_digest,
                result.opportunity_trace_observation_digest,
            ),
        ):
            if reference != expected_observations[label]:
                reasons.append(f"raw_{label}_reference_mismatch")
            if observation != expected_observations[label]:
                reasons.append(f"raw_{label}_mismatch")
        expected_dependencies = set(node.dependency_experiment_ids)
        if set(dependencies) != expected_dependencies:
            reasons.append("verification_dependency_mismatch")
        for dependency_id, projection in dependencies.items():
            if not projection.record.passed:
                reasons.append(f"dependency_verification_failed:{dependency_id}")
        physical = node.kind in {
            PlanNodeKind.BASELINE_REPLAY,
            PlanNodeKind.SINGLE_INTERVENTION,
            PlanNodeKind.JOINT_INTERVENTION,
        }
        if physical:
            if set(result.metrics) != set(node.metric_spec_digests):
                reasons.append("raw_metric_set_mismatch")
            if result.condition_slices:
                reasons.append("unexpected_condition_slice")
            if result.analysis_check is not None:
                reasons.append("unexpected_analysis_check")
        elif node.kind is PlanNodeKind.CONDITION_ANALYSIS:
            if result.metrics:
                reasons.append("unexpected_condition_node_metrics")
            expected_condition = {node.condition_spec_digest}
            actual_conditions = {item.condition_spec_digest for item in result.condition_slices}
            if actual_conditions != expected_condition:
                reasons.append("raw_condition_set_mismatch")
            for item in result.condition_slices:
                if set(item.baseline_metrics) != set(node.metric_spec_digests):
                    reasons.append("raw_condition_metric_set_mismatch")
                if set(item.intervention_metrics) != set(node.metric_spec_digests):
                    reasons.append("raw_condition_metric_set_mismatch")
            if result.analysis_check is not None:
                reasons.append("unexpected_analysis_check")
        elif node.kind in {
            PlanNodeKind.UNCERTAINTY_CHECK,
            PlanNodeKind.STABILITY_CHECK,
        }:
            expected_kind = (
                AnalysisCheckKind.UNCERTAINTY
                if node.kind is PlanNodeKind.UNCERTAINTY_CHECK
                else AnalysisCheckKind.STABILITY
            )
            if result.metrics or result.condition_slices:
                reasons.append("unexpected_analysis_check_payload")
            if result.analysis_check is None:
                reasons.append("missing_analysis_check")
            elif (
                result.analysis_check.kind is not expected_kind
                or result.analysis_check.method != node.method_id
            ):
                reasons.append("raw_analysis_check_binding_mismatch")
        return tuple(sorted(set(reasons)))

    def _project_physical(
        self,
        *,
        request: AttributionRunRequest,
        binding: ExecutorBinding,
        work: PlannedExecutionWork,
        result: RawExecutionResult,
        dependencies: Mapping[str, VerifiedNodeProjection],
    ) -> VerifiedNodeProjection:
        role_by_kind = {
            PlanNodeKind.BASELINE_REPLAY: ExperimentRole.BASELINE,
            PlanNodeKind.SINGLE_INTERVENTION: ExperimentRole.INTERVENTION,
            PlanNodeKind.JOINT_INTERVENTION: ExperimentRole.JOINT_INTERVENTION,
        }
        role = role_by_kind[work.experiment.kind]
        questions = {item.digest: item for item in request.questions}
        experiments: list[ExperimentManifest] = []
        artifacts: list[SemanticArtifactEnvelope] = []
        for consumer in work.consumers:
            question = questions.get(consumer.question_digest)
            if question is None or question.question_id != consumer.question_id:
                return self._failed_projection(
                    work,
                    result,
                    dependencies,
                    ("unknown_execution_consumer",),
                )
            interaction_spec_digest = (
                consumer.specification_digest if role is ExperimentRole.JOINT_INTERVENTION else None
            )
            metric_ids = tuple(item.metric_id for item in question.metric_specs)
            experiment_token = hashlib.sha256(
                canonical_bytes(
                    {
                        "planned_experiment_digest": work.experiment.digest,
                        "question_digest": question.digest,
                        "binding_kind": consumer.binding_kind.value,
                    }
                )
            ).hexdigest()[:24]
            experiment_id = f"experiment.pipeline.{experiment_token}"
            trace = native_trace_envelope(
                trace_id=f"trace.pipeline.{experiment_token}",
                question_digest=question.digest,
                capability_manifest_digest=request.adapter_manifest.digest,
                native_topology_digest=request.adapter_manifest.native_topology_digest,
                context_digest=request.context_digest,
                producer_id=binding.producer_id,
                producer_version=binding.producer_version,
                source_kind=binding.source_kind,
                payload={
                    **dict(result.trace_payload),
                    "planned_experiment_digest": work.experiment.digest,
                    "execution_work_digest": work.digest,
                    "raw_execution_digest": result.digest,
                    "question_digest": question.digest,
                    "binding_kind": consumer.binding_kind.value,
                },
            )
            draft = ExperimentManifest(
                experiment_id=experiment_id,
                schema_version=ATTRIBUTION_SCHEMA_VERSION,
                question_id=question.question_id,
                question_digest=question.digest,
                role=role,
                strategy_id=request.adapter_manifest.strategy_id,
                strategy_version=request.adapter_manifest.strategy_version,
                adapter_id=request.adapter_manifest.adapter_id,
                adapter_version=request.adapter_manifest.adapter_version,
                capability_manifest_version=(request.adapter_manifest.capability_manifest_version),
                capability_manifest_digest=request.adapter_manifest.digest,
                native_topology_digest=request.adapter_manifest.native_topology_digest,
                context_digest=request.context_digest,
                context_bindings=request.context_bindings,
                artifact_digest=digest({"unbound_pipeline_experiment": experiment_id}),
                native_trace_digest=trace.digest,
                plan_receipt_digest=work.plan_receipt_digest,
                execution_work_digest=work.digest,
                raw_execution_digest=result.digest,
                metric_calculator_id=request.metric_calculator_id,
                metric_calculator_version=request.metric_calculator_version,
                metric_spec_digests={item.metric_id: item.digest for item in question.metric_specs},
                period_start=request.period_start,
                period_end=request.period_end,
                intervention_ids=work.experiment.intervention_ids,
                changed_component_ids=work.experiment.component_ids,
                capital_constrained=True,
                reproduced=True,
                reconciled=True,
                pit_validated=True,
                partition_authorized=True,
                question_bound_before_outcomes=True,
                decision_trace_reconciled=True,
                opportunity_trace_reconciled=True,
                interaction_spec_digest=interaction_spec_digest,
                metrics={item: result.metrics[item] for item in metric_ids},
            )
            evidence = experiment_evidence_envelope(
                draft,
                producer_id=binding.producer_id,
                producer_version=binding.producer_version,
                source_kind=binding.source_kind,
            )
            experiments.append(replace(draft, artifact_digest=evidence.digest))
            artifacts.extend((trace, evidence))
        return self._passing_projection(
            work=work,
            result=result,
            dependencies=dependencies,
            experiments=tuple(experiments),
            artifacts=tuple(artifacts),
        )

    def _project_condition(
        self,
        *,
        request: AttributionRunRequest,
        binding: ExecutorBinding,
        work: PlannedExecutionWork,
        result: RawExecutionResult,
        dependencies: Mapping[str, VerifiedNodeProjection],
    ) -> VerifiedNodeProjection:
        questions = {item.digest: item for item in request.questions}
        slices = {item.condition_spec_digest: item for item in result.condition_slices}
        comparisons: list[ConditionComparison] = []
        artifacts: list[SemanticArtifactEnvelope] = []
        for consumer in work.consumers:
            question = questions.get(consumer.question_digest)
            condition = (
                next(
                    (
                        item
                        for item in question.condition_specs
                        if item.digest == consumer.specification_digest
                    ),
                    None,
                )
                if question is not None
                else None
            )
            raw_slice = slices.get(consumer.specification_digest or "")
            if question is None or condition is None or raw_slice is None:
                return self._failed_projection(
                    work,
                    result,
                    dependencies,
                    ("condition_consumer_mismatch",),
                )
            baseline = self._dependency_experiment(
                dependencies,
                question.digest,
                ExperimentRole.BASELINE,
            )
            intervention = self._dependency_experiment(
                dependencies,
                question.digest,
                ExperimentRole.INTERVENTION,
            )
            if baseline is None or intervention is None:
                return self._failed_projection(
                    work,
                    result,
                    dependencies,
                    ("condition_parent_projection_missing",),
                )
            metric_ids = tuple(item.metric_id for item in question.metric_specs)
            draft = ConditionComparison(
                condition_id=condition.condition_id,
                condition_spec_digest=condition.digest,
                label=condition.label,
                sample_count=raw_slice.sample_count,
                question_digest=question.digest,
                adapter_capability_digest=request.adapter_manifest.digest,
                context_digest=request.context_digest,
                period_start=request.period_start,
                period_end=request.period_end,
                baseline_experiment_digest=baseline.digest,
                intervention_experiment_digest=intervention.digest,
                result_digest=digest(
                    {
                        "unbound_pipeline_condition": condition.digest,
                        "question_digest": question.digest,
                    }
                ),
                plan_receipt_digest=work.plan_receipt_digest,
                execution_work_digest=work.digest,
                raw_execution_digest=result.digest,
                reconciled=True,
                baseline_metrics={item: raw_slice.baseline_metrics[item] for item in metric_ids},
                intervention_metrics={
                    item: raw_slice.intervention_metrics[item] for item in metric_ids
                },
            )
            evidence = condition_analysis_result_envelope(
                draft,
                producer_id=binding.producer_id,
                producer_version=binding.producer_version,
                source_kind=binding.source_kind,
            )
            comparisons.append(replace(draft, result_digest=evidence.digest))
            artifacts.append(evidence)
        return self._passing_projection(
            work=work,
            result=result,
            dependencies=dependencies,
            conditions=tuple(comparisons),
            artifacts=tuple(artifacts),
        )

    def _project_analysis_check(
        self,
        *,
        request: AttributionRunRequest,
        binding: ExecutorBinding,
        work: PlannedExecutionWork,
        result: RawExecutionResult,
        dependencies: Mapping[str, VerifiedNodeProjection],
    ) -> VerifiedNodeProjection:
        raw_check = result.analysis_check
        if raw_check is None:
            return self._failed_projection(
                work, result, dependencies, ("missing_analysis_check",)
            )
        questions = {item.digest: item for item in request.questions}
        checks: list[AnalysisCheckEvidence] = []
        artifacts: list[SemanticArtifactEnvelope] = []
        for consumer in work.consumers:
            question = questions.get(consumer.question_digest)
            if question is None or question.question_id != consumer.question_id:
                return self._failed_projection(
                    work,
                    result,
                    dependencies,
                    ("analysis_check_consumer_mismatch",),
                )
            baseline = self._dependency_experiment(
                dependencies,
                question.digest,
                ExperimentRole.BASELINE,
            )
            intervention = self._dependency_experiment(
                dependencies,
                question.digest,
                ExperimentRole.INTERVENTION,
            )
            if baseline is None or intervention is None:
                return self._failed_projection(
                    work,
                    result,
                    dependencies,
                    ("analysis_check_parent_projection_missing",),
                )
            metric_ids = tuple(item.metric_id for item in question.metric_specs)
            check_token = hashlib.sha256(
                canonical_bytes(
                    {
                        "planned_experiment_digest": work.experiment.digest,
                        "question_digest": question.digest,
                        "kind": raw_check.kind.value,
                    }
                )
            ).hexdigest()[:24]
            draft = AnalysisCheckEvidence(
                check_id=f"check.pipeline.{check_token}",
                kind=raw_check.kind,
                method=raw_check.method,
                scope=AnalysisCheckScope.OVERALL_METRIC_VECTOR,
                metric_ids=metric_ids,
                target_evidence_digest=analysis_check_target_digest(
                    metric_ids=metric_ids,
                    baseline_experiment_digest=baseline.digest,
                    intervention_experiment_digest=intervention.digest,
                ),
                question_digest=question.digest,
                adapter_capability_digest=request.adapter_manifest.digest,
                baseline_experiment_digest=baseline.digest,
                intervention_experiment_digest=intervention.digest,
                result_digest=digest(
                    {
                        "unbound_pipeline_analysis_check": work.experiment.digest,
                        "question_digest": question.digest,
                    }
                ),
                completed=raw_check.completed,
                passed=raw_check.passed,
            )
            evidence = analysis_check_result_envelope(
                draft,
                producer_id=binding.producer_id,
                producer_version=binding.producer_version,
                source_kind=binding.source_kind,
            )
            checks.append(replace(draft, result_digest=evidence.digest))
            artifacts.append(evidence)
        return self._passing_projection(
            work=work,
            result=result,
            dependencies=dependencies,
            analysis_checks=tuple(checks),
            artifacts=tuple(artifacts),
        )

    @staticmethod
    def _dependency_experiment(
        dependencies: Mapping[str, VerifiedNodeProjection],
        question_digest: str,
        role: ExperimentRole,
    ) -> ExperimentManifest | None:
        matches = [
            experiment
            for projection in dependencies.values()
            for experiment in projection.experiments
            if experiment.question_digest == question_digest and experiment.role is role
        ]
        if len(matches) != 1:
            return None
        return matches[0]

    @staticmethod
    def _failed_projection(
        work: PlannedExecutionWork,
        result: RawExecutionResult,
        dependencies: Mapping[str, VerifiedNodeProjection],
        reasons: Sequence[str],
    ) -> VerifiedNodeProjection:
        record = NodeVerificationRecord(
            run_id=work.run_id,
            planned_experiment_id=work.experiment.experiment_id,
            planned_experiment_digest=work.experiment.digest,
            work_digest=work.digest,
            raw_execution_digest=result.digest,
            plan_receipt_digest=work.plan_receipt_digest,
            dependency_verification_digests={
                key: value.record.digest for key, value in dependencies.items()
            },
            passed=False,
            reason_codes=tuple(reasons),
        )
        return VerifiedNodeProjection(record=record)

    @staticmethod
    def _passing_projection(
        *,
        work: PlannedExecutionWork,
        result: RawExecutionResult,
        dependencies: Mapping[str, VerifiedNodeProjection],
        experiments: tuple[ExperimentManifest, ...] = (),
        conditions: tuple[ConditionComparison, ...] = (),
        analysis_checks: tuple[AnalysisCheckEvidence, ...] = (),
        artifacts: tuple[SemanticArtifactEnvelope, ...] = (),
    ) -> VerifiedNodeProjection:
        record = NodeVerificationRecord(
            run_id=work.run_id,
            planned_experiment_id=work.experiment.experiment_id,
            planned_experiment_digest=work.experiment.digest,
            work_digest=work.digest,
            raw_execution_digest=result.digest,
            plan_receipt_digest=work.plan_receipt_digest,
            dependency_verification_digests={
                key: value.record.digest for key, value in dependencies.items()
            },
            passed=True,
            reason_codes=(),
            experiment_manifest_digests=tuple(item.digest for item in experiments),
            condition_comparison_digests=tuple(item.digest for item in conditions),
            analysis_check_digests=tuple(item.digest for item in analysis_checks),
            artifact_digests=tuple(item.digest for item in artifacts),
        )
        return VerifiedNodeProjection(
            record=record,
            experiments=experiments,
            conditions=conditions,
            analysis_checks=analysis_checks,
            artifacts=artifacts,
        )


class DeterministicAttributionPlanCompiler:
    """Registered wrapper around the outcome-blind deterministic plan compiler."""

    __slots__ = ("_calls", "_fail_on_calls")

    def __init__(self, *, fail_on_calls: Sequence[int] = ()) -> None:
        self._calls = 0
        self._fail_on_calls = frozenset(fail_on_calls)

    @property
    def calls(self) -> int:
        return self._calls

    def compile(self, request: AttributionRunRequest) -> AttributionPlan:
        self._calls += 1
        if self._calls in self._fail_on_calls:
            raise RuntimeError(f"injected planner failure {self._calls}")
        return compile_attribution_plan(request)


class DeterministicAttributionEvaluator:
    """Registered wrapper around the deterministic attribution kernel."""

    evaluator_id = PIPELINE_EVALUATOR_ID
    evaluator_version = PIPELINE_EVALUATOR_VERSION
    __slots__ = ("_calls", "_fail_on_calls")

    def __init__(self, *, fail_on_calls: Sequence[int] = ()) -> None:
        self._calls = 0
        self._fail_on_calls = frozenset(fail_on_calls)

    @property
    def calls(self) -> int:
        return self._calls

    def evaluate(self, **kwargs: Any) -> AttributionClaim:
        self._calls += 1
        if self._calls in self._fail_on_calls:
            raise RuntimeError(f"injected evaluator failure {self._calls}")
        return evaluate_attribution(**kwargs)


class AttributionPipeline:
    """Synchronous orchestration only; attribution mathematics stays in the kernel."""

    def __init__(
        self,
        root: Path,
        *,
        plan_compiler: DeterministicAttributionPlanCompiler | None = None,
        verifier: DeterministicAttributionNodeVerifier | None = None,
        evaluator: DeterministicAttributionEvaluator | None = None,
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._plan_compiler = plan_compiler or DeterministicAttributionPlanCompiler()
        self._verifier = verifier or DeterministicAttributionNodeVerifier()
        self._evaluator = evaluator or DeterministicAttributionEvaluator()
        if type(self._plan_compiler) is not DeterministicAttributionPlanCompiler:
            raise AttributionError(
                "unregistered_plan_compiler",
                "the plan compiler implementation is not registered",
            )
        if type(self._verifier) is not DeterministicAttributionNodeVerifier:
            raise AttributionError(
                "unregistered_pipeline_verifier",
                "the verifier implementation is not registered",
            )
        if type(self._evaluator) is not DeterministicAttributionEvaluator:
            raise AttributionError(
                "unregistered_pipeline_evaluator",
                "the evaluator implementation is not registered",
            )

    def plan(self, request: AttributionRunRequest) -> PlannedPipelineRun:
        if not isinstance(request, AttributionRunRequest):
            raise AttributionError("invalid_run_request", "request is invalid")
        run_id = _run_id(request)
        store = ImmutableRunStore(self.root, run_id)
        store.write("request.json", request.canonical_bytes())
        try:
            plan = self._plan_compiler.compile(request)
            if not isinstance(plan, AttributionPlan):
                raise AttributionError("invalid_attribution_plan", "compiler returned no plan")
        except Exception as exc:
            self._record_failure(
                store=store,
                run_id=run_id,
                stage="planner",
                subject_id=request.request_id,
                reason_code=self._stable_reason(exc, "planner_failed"),
                parents=(request.digest,),
            )
            raise
        store.write("plan.json", plan.canonical_bytes())
        receipt = PipelineStageReceipt(
            run_id=run_id,
            state=PipelineRunState.PLANNED,
            stage="planner",
            subject_id=plan.plan_id,
            parent_digests=(request.digest,),
            output_digests=(plan.digest,),
        )
        store.write("receipts/planned.json", receipt.canonical_bytes())
        return PlannedPipelineRun(
            run_id=run_id,
            run_dir=store.root,
            request=request,
            plan=plan,
            planned_receipt=receipt,
        )

    def run(
        self,
        request: AttributionRunRequest,
        executor: AttributionExperimentExecutor,
    ) -> AttributionPipelineResult:
        self.plan(request)
        return self.resume(request, executor)

    def resume(
        self,
        request: AttributionRunRequest,
        executor: AttributionExperimentExecutor,
    ) -> AttributionPipelineResult:
        if not isinstance(request, AttributionRunRequest):
            raise AttributionError("invalid_run_request", "request is invalid")
        store = ImmutableRunStore(self.root, _run_id(request))
        with store.lock():
            return self._resume_locked(request, executor)

    def _resume_locked(
        self,
        request: AttributionRunRequest,
        executor: AttributionExperimentExecutor,
    ) -> AttributionPipelineResult:
        planned = self.plan(request)
        store = ImmutableRunStore(self.root, planned.run_id)
        try:
            if not isinstance(executor, AttributionExperimentExecutor):
                raise AttributionError("invalid_executor", "executor does not implement its port")
            binding = executor.binding()
            self._validate_executor_implementation(executor, binding)
            self._validate_executor_binding(binding, request)
            if (
                self._verifier.verifier_id,
                self._verifier.verifier_version,
            ) != (PIPELINE_VERIFIER_ID, PIPELINE_VERIFIER_VERSION):
                raise AttributionError(
                    "pipeline_verifier_mismatch", "only the registered verifier may run"
                )
        except Exception as exc:
            self._record_failure(
                store=store,
                run_id=planned.run_id,
                stage="executor_binding",
                subject_id=planned.plan.plan_id,
                reason_code=self._stable_reason(exc, "executor_binding_failed"),
                parents=(planned.planned_receipt.digest,),
            )
            raise
        store.write("executor-binding.json", binding.canonical_bytes())
        running_receipt = PipelineStageReceipt(
            run_id=planned.run_id,
            state=PipelineRunState.RUNNING,
            stage="pipeline",
            subject_id=planned.plan.plan_id,
            parent_digests=(planned.planned_receipt.digest,),
            output_digests=(binding.digest,),
        )
        store.write("receipts/running.json", running_receipt.canonical_bytes())

        consumers = _consumer_bindings(planned.plan)
        raw_results: dict[str, RawExecutionResult] = {}
        verified: dict[str, VerifiedNodeProjection] = {}
        for experiment in planned.plan.experiments:
            token = _node_token(experiment)
            parent_results = {
                parent_id: raw_results[parent_id].digest
                for parent_id in experiment.dependency_experiment_ids
            }
            work = self._build_work(
                planned,
                binding,
                experiment,
                parent_results,
                consumers,
            )
            store.write(f"work/{token}.json", work.canonical_bytes())
            raw_path = f"raw/{token}.json"
            try:
                if store.exists(raw_path):
                    result = RawExecutionResult.from_bytes(store.read(raw_path))
                else:
                    result = executor.execute(work)
                    if not isinstance(result, RawExecutionResult):
                        raise AttributionError(
                            "invalid_raw_execution",
                            "executor must return RawExecutionResult only",
                        )
                    store.write(raw_path, result.canonical_bytes())
            except Exception as exc:
                self._record_failure(
                    store=store,
                    run_id=planned.run_id,
                    stage="executor",
                    subject_id=experiment.experiment_id,
                    reason_code=self._stable_reason(exc, "executor_failed"),
                    parents=(planned.planned_receipt.digest, work.digest),
                )
                raise
            raw_results[experiment.experiment_id] = result
            dependencies = {item: verified[item] for item in experiment.dependency_experiment_ids}
            execution_receipt = PipelineStageReceipt(
                run_id=planned.run_id,
                state=PipelineRunState.EXECUTED,
                stage="executor",
                subject_id=experiment.experiment_id,
                parent_digests=(
                    planned.planned_receipt.digest,
                    work.digest,
                    *(
                        verified[item].record.digest
                        for item in experiment.dependency_experiment_ids
                    ),
                ),
                output_digests=(result.digest,),
            )
            store.write(
                f"receipts/execution/{token}.json",
                execution_receipt.canonical_bytes(),
            )
            verification_receipt_path = f"receipts/verification/{token}.json"
            if store.exists(verification_receipt_path):
                try:
                    verified[experiment.experiment_id] = self._load_projection(
                        store=store,
                        planned=planned,
                        binding=binding,
                        work=work,
                        result=result,
                        dependencies=dependencies,
                    )
                except Exception as exc:
                    self._record_failure(
                        store=store,
                        run_id=planned.run_id,
                        stage="verifier",
                        subject_id=experiment.experiment_id,
                        reason_code=self._stable_reason(exc, "persisted_verification_invalid"),
                        parents=(work.digest, result.digest),
                        state=PipelineRunState.VERIFICATION_FAILED,
                    )
                    raise
                continue
            try:
                projection = self._verifier.verify(
                    request=request,
                    plan=planned.plan,
                    planned_receipt=planned.planned_receipt,
                    binding=binding,
                    work=work,
                    result=result,
                    dependencies=dependencies,
                )
                if not isinstance(projection, VerifiedNodeProjection):
                    raise AttributionError("invalid_verifier_output", "verifier output is invalid")
            except Exception as exc:
                self._record_failure(
                    store=store,
                    run_id=planned.run_id,
                    stage="verifier",
                    subject_id=experiment.experiment_id,
                    reason_code=self._stable_reason(exc, "verifier_failed"),
                    parents=(work.digest, result.digest),
                    state=PipelineRunState.VERIFICATION_FAILED,
                )
                raise
            if not projection.record.passed:
                store.write(
                    f"verification/{token}.json",
                    projection.record.canonical_bytes(),
                )
                for reason in projection.record.reason_codes:
                    self._record_failure(
                        store=store,
                        run_id=planned.run_id,
                        stage="verifier",
                        subject_id=experiment.experiment_id,
                        reason_code=reason,
                        parents=(work.digest, result.digest),
                        state=PipelineRunState.VERIFICATION_FAILED,
                    )
                raise AttributionError(
                    "node_verification_failed",
                    ",".join(projection.record.reason_codes),
                )
            try:
                self._validate_projection(
                    projection=projection,
                    planned=planned,
                    binding=binding,
                    work=work,
                    result=result,
                    dependencies=dependencies,
                )
                store.write(
                    f"verification/{token}.json",
                    projection.record.canonical_bytes(),
                )
                self._persist_projection(store, projection)
                verification_receipt = PipelineStageReceipt(
                    run_id=planned.run_id,
                    state=PipelineRunState.EXECUTED,
                    stage="verifier",
                    subject_id=experiment.experiment_id,
                    parent_digests=(
                        planned.planned_receipt.digest,
                        work.digest,
                        result.digest,
                        *(item.record.digest for item in dependencies.values()),
                    ),
                    output_digests=(projection.record.digest,),
                )
                store.write(
                    verification_receipt_path,
                    verification_receipt.canonical_bytes(),
                )
            except Exception as exc:
                self._record_failure(
                    store=store,
                    run_id=planned.run_id,
                    stage="verifier",
                    subject_id=experiment.experiment_id,
                    reason_code=self._stable_reason(exc, "verifier_output_invalid"),
                    parents=(work.digest, result.digest),
                    state=PipelineRunState.VERIFICATION_FAILED,
                )
                raise
            verified[experiment.experiment_id] = projection

        try:
            return self._evaluate(
                store=store,
                planned=planned,
                verified=verified,
            )
        except Exception as exc:
            self._record_failure(
                store=store,
                run_id=planned.run_id,
                stage="evaluator",
                subject_id=planned.plan.plan_id,
                reason_code=self._stable_reason(exc, "evaluator_failed"),
                parents=tuple(item.record.digest for item in verified.values()),
            )
            raise

    @staticmethod
    def _build_work(
        planned: PlannedPipelineRun,
        binding: ExecutorBinding,
        experiment: PlannedExperiment,
        parent_results: Mapping[str, str],
        consumers: Mapping[str, tuple[ExecutionConsumer, ...]],
    ) -> PlannedExecutionWork:
        request = planned.request
        return PlannedExecutionWork(
            run_id=planned.run_id,
            plan_id=planned.plan.plan_id,
            plan_digest=planned.plan.digest,
            plan_receipt_digest=planned.planned_receipt.digest,
            executor_binding_digest=binding.digest,
            run_request_digest=request.digest,
            strategy_id=request.adapter_manifest.strategy_id,
            strategy_version=request.adapter_manifest.strategy_version,
            adapter_manifest_digest=request.adapter_manifest.digest,
            native_topology_digest=request.adapter_manifest.native_topology_digest,
            context_digest=request.context_digest,
            context_bindings=request.context_bindings,
            period_start=request.period_start,
            period_end=request.period_end,
            metric_calculator_id=request.metric_calculator_id,
            metric_calculator_version=request.metric_calculator_version,
            capital_constrained=request.capital_constrained,
            point_in_time_required=request.point_in_time_required,
            experiment=experiment,
            parent_raw_result_digests=parent_results,
            consumers=consumers[experiment.experiment_id],
        )

    def status(self, request: AttributionRunRequest) -> PipelineStatusSnapshot:
        if not isinstance(request, AttributionRunRequest):
            raise AttributionError("invalid_run_request", "request is invalid")
        run_id = _run_id(request)
        run_dir = self.root / run_id
        if not run_dir.is_dir():
            raise AttributionError("attribution_run_not_found", run_id)
        store = ImmutableRunStore(self.root, run_id)
        if not store.exists("request.json"):
            raise AttributionError("attribution_run_not_found", run_id)
        if store.read("request.json") != request.canonical_bytes():
            raise AttributionError("persisted_request_mismatch", "request bytes changed")
        failures = self._read_failure_receipts(store, run_id)
        if not store.exists("plan.json"):
            self._validate_failure_receipts_without_plan(
                failures=failures,
                request=request,
            )
            return PipelineStatusSnapshot(
                run_id=run_id,
                run_dir=store.root,
                state=PipelineRunState.FAILED,
                plan_digest=None,
                total_nodes=0,
                executed_nodes=0,
                verified_nodes=0,
                claim_count=0,
                reason_codes=tuple(
                    sorted({reason for item in failures for reason in item.reason_codes})
                ),
            )
        plan = self._plan_compiler.compile(request)
        if not isinstance(plan, AttributionPlan):
            raise AttributionError("invalid_attribution_plan", "compiler returned no plan")
        if store.read("plan.json") != plan.canonical_bytes():
            raise AttributionError("persisted_plan_mismatch", "plan bytes changed")
        planned_receipt = PipelineStageReceipt(
            run_id=run_id,
            state=PipelineRunState.PLANNED,
            stage="planner",
            subject_id=plan.plan_id,
            parent_digests=(request.digest,),
            output_digests=(plan.digest,),
        )
        self._require_receipt(store, "receipts/planned.json", planned_receipt)
        planned = PlannedPipelineRun(
            run_id=run_id,
            run_dir=store.root,
            request=request,
            plan=plan,
            planned_receipt=planned_receipt,
        )
        binding: ExecutorBinding | None = None
        if store.exists("executor-binding.json"):
            binding = ExecutorBinding.from_bytes(store.read("executor-binding.json"))
            self._validate_executor_binding(binding, request)
        if store.exists("receipts/running.json"):
            if binding is None:
                raise AttributionError(
                    "pipeline_receipt_lineage_mismatch",
                    "running receipt exists without an executor binding",
                )
            self._require_receipt(
                store,
                "receipts/running.json",
                PipelineStageReceipt(
                    run_id=run_id,
                    state=PipelineRunState.RUNNING,
                    stage="pipeline",
                    subject_id=plan.plan_id,
                    parent_digests=(planned_receipt.digest,),
                    output_digests=(binding.digest,),
                ),
            )
        executed = 0
        raw_results: dict[str, RawExecutionResult] = {}
        verified: dict[str, VerifiedNodeProjection] = {}
        work_by_experiment: dict[str, PlannedExecutionWork] = {}
        consumers = _consumer_bindings(plan)
        if binding is not None:
            for experiment in plan.experiments:
                token = _node_token(experiment)
                raw_relative = f"raw/{token}.json"
                execution_relative = f"receipts/execution/{token}.json"
                verification_relative = f"receipts/verification/{token}.json"
                node_paths_exist = any(
                    store.exists(relative)
                    for relative in (
                        f"work/{token}.json",
                        raw_relative,
                        execution_relative,
                        f"verification/{token}.json",
                        verification_relative,
                    )
                )
                missing_parent = any(
                    item not in raw_results for item in experiment.dependency_experiment_ids
                )
                if missing_parent:
                    if node_paths_exist:
                        raise AttributionError(
                            "persisted_node_order_mismatch",
                            "a node exists before all dependency results",
                        )
                    continue
                parent_results = {
                    item: raw_results[item].digest
                    for item in experiment.dependency_experiment_ids
                }
                work = self._build_work(
                    planned,
                    binding,
                    experiment,
                    parent_results,
                    consumers,
                )
                work_by_experiment[experiment.experiment_id] = work
                work_relative = f"work/{token}.json"
                if not store.exists(raw_relative):
                    if store.exists(execution_relative) or store.exists(verification_relative):
                        raise AttributionError(
                            "pipeline_receipt_lineage_mismatch",
                            "node receipt exists without its raw result",
                        )
                    if store.exists(work_relative) and store.read(
                        work_relative
                    ) != work.canonical_bytes():
                        raise AttributionError(
                            "persisted_work_mismatch", "work bytes changed"
                        )
                    continue
                if not store.exists(work_relative) or store.read(
                    work_relative
                ) != work.canonical_bytes():
                    raise AttributionError("persisted_work_mismatch", "work bytes changed")
                result = RawExecutionResult.from_bytes(store.read(raw_relative))
                raw_results[experiment.experiment_id] = result
                executed += 1
                dependencies = {
                    item: verified[item] for item in experiment.dependency_experiment_ids
                }
                expected_execution = PipelineStageReceipt(
                    run_id=run_id,
                    state=PipelineRunState.EXECUTED,
                    stage="executor",
                    subject_id=experiment.experiment_id,
                    parent_digests=(
                        planned_receipt.digest,
                        work.digest,
                        *(item.record.digest for item in dependencies.values()),
                    ),
                    output_digests=(result.digest,),
                )
                if store.exists(execution_relative):
                    self._require_receipt(store, execution_relative, expected_execution)
                elif store.exists(verification_relative):
                    raise AttributionError(
                        "pipeline_receipt_lineage_mismatch",
                        "verification receipt exists without execution receipt",
                    )
                if store.exists(verification_relative):
                    verified[experiment.experiment_id] = self._load_projection(
                        store=store,
                        planned=planned,
                        binding=binding,
                        work=work,
                        result=result,
                        dependencies=dependencies,
                    )
        self._validate_failure_receipts(
            failures=failures,
            request=request,
            planned=planned,
            work_by_experiment=work_by_experiment,
            raw_results=raw_results,
            verified=verified,
        )
        verified_count = len(verified)
        if store.exists("receipts/evaluated.json"):
            if binding is None or verified_count != len(plan.experiments):
                raise AttributionError(
                    "evaluation_lineage_incomplete",
                    "evaluated receipt requires every node verification",
                )
            bundle_bytes = store.read("evaluation/bundle.json")
            bundle = EvaluationBundle.from_bytes(bundle_bytes)
            expected_verifications = tuple(
                sorted(item.record.digest for item in verified.values())
            )
            if (
                bundle.run_id != run_id
                or bundle.plan_digest != plan.digest
                or bundle.source_kind != request.adapter_manifest.evidence_source_kind
                or bundle.node_verification_digests != expected_verifications
            ):
                raise AttributionError(
                    "evaluation_bundle_lineage_mismatch",
                    "evaluation bundle is not bound to the verified plan",
                )
            rebuilt = self._evaluate(
                store=store,
                planned=planned,
                verified=verified,
                persist=False,
            )
            if rebuilt.evaluation_bundle.canonical_bytes() != bundle_bytes:
                raise AttributionError(
                    "evaluation_bundle_rebuild_mismatch",
                    "persisted evaluation differs from the deterministic kernel output",
                )
            evaluated_receipt_bytes = store.read("receipts/evaluated.json")
            if rebuilt.evaluation_receipt.canonical_bytes() != evaluated_receipt_bytes:
                raise AttributionError(
                    "evaluation_receipt_rebuild_mismatch",
                    "persisted evaluation receipt differs from the rebuilt lineage",
                )
            return PipelineStatusSnapshot(
                run_id=run_id,
                run_dir=store.root,
                state=PipelineRunState.EVALUATED,
                plan_digest=plan.digest,
                total_nodes=len(plan.experiments),
                executed_nodes=executed,
                verified_nodes=verified_count,
                claim_count=len(bundle.claims),
            )
        verification_failed = False
        executor_failed = False
        for experiment in plan.experiments:
            related = [item for item in failures if item.subject_id == experiment.experiment_id]
            if any(item.state is PipelineRunState.VERIFICATION_FAILED for item in related):
                verification_failed = (
                    verification_failed or experiment.experiment_id not in verified
                )
            if any(
                item.state is PipelineRunState.FAILED and item.stage == "executor"
                for item in related
            ):
                executor_failed = executor_failed or experiment.experiment_id not in raw_results
        evaluator_failed = any(item.stage == "evaluator" for item in failures)
        binding_failed = any(item.stage == "executor_binding" for item in failures) and not (
            binding is not None and store.exists("receipts/running.json")
        )
        reasons = tuple(
            sorted(
                {
                    reason
                    for item in failures
                    if (
                        (item.stage == "verifier" and verification_failed)
                        or (item.stage == "executor" and executor_failed)
                        or (item.stage == "evaluator" and evaluator_failed)
                        or (item.stage == "executor_binding" and binding_failed)
                    )
                    for reason in item.reason_codes
                }
            )
        )
        if verification_failed:
            state = PipelineRunState.VERIFICATION_FAILED
        elif executor_failed or evaluator_failed or binding_failed:
            state = PipelineRunState.FAILED
        elif verified_count == len(plan.experiments) and plan.experiments:
            state = PipelineRunState.EXECUTED
        elif store.exists("receipts/running.json"):
            state = PipelineRunState.RUNNING
        else:
            state = PipelineRunState.PLANNED
        return PipelineStatusSnapshot(
            run_id=run_id,
            run_dir=store.root,
            state=state,
            plan_digest=plan.digest,
            total_nodes=len(plan.experiments),
            executed_nodes=executed,
            verified_nodes=verified_count,
            claim_count=0,
            reason_codes=reasons,
        )

    @staticmethod
    def _validate_executor_implementation(
        executor: AttributionExperimentExecutor,
        binding: ExecutorBinding,
    ) -> None:
        if not isinstance(binding, ExecutorBinding):
            raise AttributionError("invalid_executor_binding", "binding is invalid")
        registered_type = _REGISTERED_EXECUTOR_TYPES.get(
            (binding.executor_id, binding.executor_version)
        )
        if registered_type is None or type(executor) is not registered_type:
            raise AttributionError(
                "unregistered_executor_implementation",
                "executor identity is not backed by its registered concrete implementation",
            )
        for method_name in ("binding", "execute"):
            if method_name in getattr(executor, "__dict__", {}):
                raise AttributionError(
                    "executor_instance_override",
                    f"executor method {method_name} cannot be replaced per instance",
                )

    @staticmethod
    def _validate_executor_binding(
        binding: ExecutorBinding, request: AttributionRunRequest
    ) -> None:
        if not isinstance(binding, ExecutorBinding):
            raise AttributionError("invalid_executor_binding", "binding is invalid")
        synthetic_identity = (
            SYNTHETIC_ARTIFACT_PRODUCER_ID,
            SYNTHETIC_ARTIFACT_PRODUCER_VERSION,
        )
        formal_identity = (
            FORMAL_ARTIFACT_PRODUCER_ID,
            FORMAL_ARTIFACT_PRODUCER_VERSION,
        )
        executor_identity = (binding.executor_id, binding.executor_version)
        producer_identity = (binding.producer_id, binding.producer_version)
        if executor_identity not in {synthetic_identity, formal_identity}:
            raise AttributionError(
                "prototype_executor_not_registered",
                "the executor identity is not registered",
            )
        if producer_identity != executor_identity:
            raise AttributionError(
                "prototype_producer_not_registered",
                "the producer identity does not match the registered executor",
            )
        expected_source = (
            SYNTHETIC_SOURCE_KIND
            if executor_identity == synthetic_identity
            else FORMAL_SOURCE_KIND
        )
        if binding.source_kind != expected_source:
            raise AttributionError(
                "formal_strategy_source_blocked",
                "executor evidence source differs from its registered identity",
            )
        if binding.synthetic_fixture != (executor_identity == synthetic_identity):
            raise AttributionError(
                "formal_strategy_source_blocked",
                "executor fixture classification differs from its registered identity",
            )
        if request.adapter_manifest.evidence_source_kind != expected_source:
            raise AttributionError(
                "formal_strategy_source_blocked",
                "request evidence source differs from the registered executor",
            )
        if (
            binding.metric_calculator_id,
            binding.metric_calculator_version,
        ) != (
            request.metric_calculator_id,
            request.metric_calculator_version,
        ):
            raise AttributionError(
                "metric_calculator_binding_mismatch",
                "executor metric calculator differs from the frozen request",
            )

    @staticmethod
    def _require_receipt(
        store: ImmutableRunStore,
        relative: str,
        expected: PipelineStageReceipt,
    ) -> PipelineStageReceipt:
        receipt = PipelineStageReceipt.from_bytes(store.read(relative))
        if receipt != expected:
            raise AttributionError(
                "pipeline_receipt_lineage_mismatch",
                f"{relative} does not match its expected stage lineage",
            )
        return receipt

    @classmethod
    def _validate_projection(
        cls,
        *,
        projection: VerifiedNodeProjection,
        planned: PlannedPipelineRun,
        binding: ExecutorBinding,
        work: PlannedExecutionWork,
        result: RawExecutionResult,
        dependencies: Mapping[str, VerifiedNodeProjection],
    ) -> None:
        record = projection.record
        expected_record_bindings = {
            "run_id": planned.run_id,
            "planned_experiment_id": work.experiment.experiment_id,
            "planned_experiment_digest": work.experiment.digest,
            "work_digest": work.digest,
            "raw_execution_digest": result.digest,
            "plan_receipt_digest": planned.planned_receipt.digest,
        }
        for field_name, expected in expected_record_bindings.items():
            if getattr(record, field_name) != expected:
                raise AttributionError(
                    "node_verification_lineage_mismatch",
                    f"verification field {field_name} is not bound to the current node",
                )
        expected_dependencies = {
            key: value.record.digest for key, value in dependencies.items()
        }
        if dict(record.dependency_verification_digests) != expected_dependencies:
            raise AttributionError(
                "node_verification_dependency_mismatch",
                "verification dependencies do not match the current plan",
            )
        if not record.passed:
            raise AttributionError(
                "node_verification_failed",
                ",".join(record.reason_codes),
            )
        actual_outputs = {
            "experiment_manifest_digests": tuple(
                sorted(item.digest for item in projection.experiments)
            ),
            "condition_comparison_digests": tuple(
                sorted(item.digest for item in projection.conditions)
            ),
            "analysis_check_digests": tuple(
                sorted(item.digest for item in projection.analysis_checks)
            ),
            "artifact_digests": tuple(sorted(item.digest for item in projection.artifacts)),
        }
        for field_name, actual in actual_outputs.items():
            if getattr(record, field_name) != actual:
                raise AttributionError(
                    "node_verification_output_mismatch",
                    f"verification output set {field_name} is incomplete",
                )
        artifact_by_digest = {item.digest: item for item in projection.artifacts}
        if len(artifact_by_digest) != len(projection.artifacts):
            raise AttributionError(
                "duplicate_projection_artifact", "projection artifacts must be unique"
            )
        for artifact in projection.artifacts:
            if (
                artifact.producer_id,
                artifact.producer_version,
                artifact.source_kind,
            ) != (binding.producer_id, binding.producer_version, binding.source_kind):
                raise AttributionError(
                    "projection_producer_mismatch",
                    "projection artifact producer is not the registered executor",
                )
        consumer_digests = {item.question_digest for item in work.consumers}
        for experiment in projection.experiments:
            if experiment.question_digest not in consumer_digests:
                raise AttributionError(
                    "projection_consumer_mismatch",
                    "experiment projection is not consumed by this node",
                )
            if (
                experiment.plan_receipt_digest,
                experiment.execution_work_digest,
                experiment.raw_execution_digest,
            ) != (planned.planned_receipt.digest, work.digest, result.digest):
                raise AttributionError(
                    "projection_execution_lineage_mismatch",
                    "experiment projection is not bound to this execution",
                )
            evidence = artifact_by_digest.get(experiment.artifact_digest)
            trace = artifact_by_digest.get(experiment.native_trace_digest or "")
            if evidence is None or evidence.artifact_kind is not ArtifactKind.EXPERIMENT_RESULT:
                raise AttributionError(
                    "projection_evidence_missing", "experiment result artifact is missing"
                )
            if trace is None or trace.artifact_kind is not ArtifactKind.NATIVE_TRACE:
                raise AttributionError(
                    "projection_trace_missing", "experiment native trace is missing"
                )
        for condition in projection.conditions:
            if condition.question_digest not in consumer_digests:
                raise AttributionError(
                    "projection_consumer_mismatch",
                    "condition projection is not consumed by this node",
                )
            if (
                condition.plan_receipt_digest,
                condition.execution_work_digest,
                condition.raw_execution_digest,
            ) != (planned.planned_receipt.digest, work.digest, result.digest):
                raise AttributionError(
                    "projection_execution_lineage_mismatch",
                    "condition projection is not bound to this execution",
                )
            evidence = artifact_by_digest.get(condition.result_digest)
            if (
                evidence is None
                or evidence.artifact_kind is not ArtifactKind.CONDITION_ANALYSIS_RESULT
            ):
                raise AttributionError(
                    "projection_evidence_missing", "condition result artifact is missing"
                )
        for check in projection.analysis_checks:
            if check.question_digest not in consumer_digests:
                raise AttributionError(
                    "projection_consumer_mismatch",
                    "analysis projection is not consumed by this node",
                )
            evidence = artifact_by_digest.get(check.result_digest)
            if (
                evidence is None
                or evidence.artifact_kind is not ArtifactKind.ANALYSIS_CHECK_RESULT
            ):
                raise AttributionError(
                    "projection_evidence_missing", "analysis result artifact is missing"
                )

    @classmethod
    def _load_projection(
        cls,
        *,
        store: ImmutableRunStore,
        planned: PlannedPipelineRun,
        binding: ExecutorBinding,
        work: PlannedExecutionWork,
        result: RawExecutionResult,
        dependencies: Mapping[str, VerifiedNodeProjection],
    ) -> VerifiedNodeProjection:
        token = _node_token(work.experiment)
        record = NodeVerificationRecord.from_bytes(
            store.read(f"verification/{token}.json")
        )
        artifacts = tuple(
            SemanticArtifactEnvelope.from_bytes(
                store.read(f"artifacts/{item.removeprefix('sha256:')}.json")
            )
            for item in record.artifact_digests
        )
        experiments = tuple(
            _experiment_from_artifact(item)
            for item in artifacts
            if item.artifact_kind is ArtifactKind.EXPERIMENT_RESULT
        )
        conditions = tuple(
            _condition_from_artifact(item)
            for item in artifacts
            if item.artifact_kind is ArtifactKind.CONDITION_ANALYSIS_RESULT
        )
        analysis_checks = tuple(
            _analysis_check_from_artifact(item)
            for item in artifacts
            if item.artifact_kind is ArtifactKind.ANALYSIS_CHECK_RESULT
        )
        projection = VerifiedNodeProjection(
            record=record,
            experiments=experiments,
            conditions=conditions,
            analysis_checks=analysis_checks,
            artifacts=artifacts,
        )
        cls._validate_projection(
            projection=projection,
            planned=planned,
            binding=binding,
            work=work,
            result=result,
            dependencies=dependencies,
        )
        for item in experiments:
            expected = canonical_bytes(item.to_dict())
            relative = f"evidence/experiments/{item.digest.removeprefix('sha256:')}.json"
            if store.read(relative) != expected:
                raise AttributionError(
                    "projection_evidence_digest_mismatch", relative
                )
        for item in conditions:
            expected = canonical_bytes(item.to_dict())
            relative = f"evidence/conditions/{item.digest.removeprefix('sha256:')}.json"
            if store.read(relative) != expected:
                raise AttributionError(
                    "projection_evidence_digest_mismatch", relative
                )
        for item in analysis_checks:
            expected = canonical_bytes(item.to_dict())
            relative = f"evidence/analysis-checks/{item.digest.removeprefix('sha256:')}.json"
            if store.read(relative) != expected:
                raise AttributionError(
                    "projection_evidence_digest_mismatch", relative
                )
        expected_receipt = PipelineStageReceipt(
            run_id=planned.run_id,
            state=PipelineRunState.EXECUTED,
            stage="verifier",
            subject_id=work.experiment.experiment_id,
            parent_digests=(
                planned.planned_receipt.digest,
                work.digest,
                result.digest,
                *(item.record.digest for item in dependencies.values()),
            ),
            output_digests=(record.digest,),
        )
        cls._require_receipt(
            store,
            f"receipts/verification/{token}.json",
            expected_receipt,
        )
        return projection

    def _evaluate(
        self,
        *,
        store: ImmutableRunStore,
        planned: PlannedPipelineRun,
        verified: Mapping[str, VerifiedNodeProjection],
        persist: bool = True,
    ) -> AttributionPipelineResult:
        artifacts = tuple(
            sorted(
                {
                    artifact.digest: artifact
                    for projection in verified.values()
                    for artifact in projection.artifacts
                }.values(),
                key=lambda item: item.digest,
            )
        )
        resolver = InMemoryArtifactResolver(artifacts)
        question_by_digest = {item.digest: item for item in planned.request.questions}
        claims: list[AttributionClaim] = []
        interactions: list[InteractionComparison] = []
        for planned_question in planned.plan.planned_questions:
            if planned_question.status is not QuestionPlanStatus.READY:
                continue
            question = question_by_digest[planned_question.question_digest]
            baseline = self._manifest_for_binding(
                planned_question,
                QuestionBindingKind.BASELINE,
                verified,
            )
            subject = self._manifest_for_binding(
                planned_question,
                QuestionBindingKind.SUBJECT_INTERVENTION,
                verified,
            )
            conditions = tuple(
                item
                for binding in planned_question.experiment_bindings
                if binding.kind is QuestionBindingKind.CONDITION_ANALYSIS
                for item in verified[binding.experiment_id].conditions
                if item.question_digest == question.digest
            )
            question_interactions: list[InteractionComparison] = []
            for spec in question.interaction_specs:
                partner = self._manifest_for_spec_binding(
                    planned_question,
                    QuestionBindingKind.INTERACTION_PARTNER,
                    spec.digest,
                    verified,
                )
                joint = self._manifest_for_spec_binding(
                    planned_question,
                    QuestionBindingKind.JOINT_INTERVENTION,
                    spec.digest,
                    verified,
                )
                comparison = InteractionComparison(
                    interaction_id=spec.interaction_id,
                    interaction_spec_digest=spec.digest,
                    partner_component_id=spec.partner_component_id,
                    baseline_experiment_digest=baseline.digest,
                    subject_experiment_digest=subject.digest,
                    partner_intervention=partner,
                    joint_intervention=joint,
                )
                question_interactions.append(comparison)
                interactions.append(comparison)
            checks = tuple(
                item
                for binding in planned_question.experiment_bindings
                if binding.kind
                in {
                    QuestionBindingKind.UNCERTAINTY_CHECK,
                    QuestionBindingKind.STABILITY_CHECK,
                }
                for item in verified[binding.experiment_id].analysis_checks
            )
            claim = self._evaluator.evaluate(
                adapter=planned.request.adapter_manifest,
                question=question,
                baseline=baseline,
                intervention=subject,
                conditions=conditions,
                interactions=tuple(question_interactions),
                analysis_checks=checks,
                artifact_resolver=resolver,
            )
            if not isinstance(claim, AttributionClaim):
                raise AttributionError("invalid_evaluator_output", "evaluator output is invalid")
            if claim.status is not ClaimStatus.VALID:
                raise AttributionError(
                    "verified_evidence_rejected",
                    f"kernel rejected question {question.question_id}",
                )
            expected_claim_bindings = {
                "question_id": question.question_id,
                "question_digest": question.digest,
                "subject_component_id": question.subject_component_id,
                "intervention_id": question.intervention_id,
                "strategy_id": planned.request.adapter_manifest.strategy_id,
                "strategy_version": planned.request.adapter_manifest.strategy_version,
                "adapter_id": planned.request.adapter_manifest.adapter_id,
                "adapter_version": planned.request.adapter_manifest.adapter_version,
                "capability_manifest_digest": planned.request.adapter_manifest.digest,
                "native_topology_digest": (
                    planned.request.adapter_manifest.native_topology_digest
                ),
                "context_digest": planned.request.context_digest,
                "period_start": planned.request.period_start,
                "period_end": planned.request.period_end,
                "baseline_experiment_id": baseline.experiment_id,
                "intervention_experiment_id": subject.experiment_id,
                "baseline_experiment_digest": baseline.digest,
                "intervention_experiment_digest": subject.digest,
                "baseline_artifact_digest": baseline.artifact_digest,
                "intervention_artifact_digest": subject.artifact_digest,
            }
            for field_name, expected in expected_claim_bindings.items():
                if getattr(claim, field_name) != expected:
                    raise AttributionError(
                        "evaluator_claim_binding_mismatch",
                        f"claim field {field_name} is not bound to the current question",
                    )
            claims.append(claim)
        claims_tuple = tuple(sorted(claims, key=lambda item: item.claim_id))
        if len({item.claim_id for item in claims_tuple}) != len(claims_tuple):
            raise AttributionError(
                "duplicate_evaluator_claim", "each question requires one unique claim identity"
            )
        interactions_tuple = tuple(sorted(interactions, key=lambda item: item.digest))
        experiments = tuple(
            sorted(
                {
                    item.digest: item
                    for projection in verified.values()
                    for item in projection.experiments
                }.values(),
                key=lambda item: (item.experiment_id, item.digest),
            )
        )
        conditions = tuple(
            sorted(
                {
                    item.digest: item
                    for projection in verified.values()
                    for item in projection.conditions
                }.values(),
                key=lambda item: item.digest,
            )
        )
        checks = tuple(
            sorted(
                {
                    item.digest: item
                    for projection in verified.values()
                    for item in projection.analysis_checks
                }.values(),
                key=lambda item: item.digest,
            )
        )
        kernel_receipts = tuple(
            sorted(
                {
                    item.digest: item
                    for claim in claims_tuple
                    for item in claim.verification_receipts
                }.values(),
                key=lambda item: item.digest,
            )
        )
        evaluation_bundle = EvaluationBundle(
            run_id=planned.run_id,
            plan_digest=planned.plan.digest,
            source_kind=planned.request.adapter_manifest.evidence_source_kind,
            node_verification_digests=tuple(
                item.record.digest for item in verified.values()
            ),
            claims=tuple(item.to_dict() for item in claims_tuple),
            interactions=tuple(item.to_dict() for item in interactions_tuple),
            kernel_receipts=tuple(item.to_dict() for item in kernel_receipts),
        )
        if persist:
            store.write("evaluation/bundle.json", evaluation_bundle.canonical_bytes())
        evaluation_receipt = PipelineStageReceipt(
            run_id=planned.run_id,
            state=PipelineRunState.EVALUATED,
            stage="evaluator",
            subject_id=planned.plan.plan_id,
            parent_digests=(
                planned.planned_receipt.digest,
                *(item.record.digest for item in verified.values()),
            ),
            output_digests=(evaluation_bundle.digest,),
        )
        if persist:
            store.write("receipts/evaluated.json", evaluation_receipt.canonical_bytes())
        return AttributionPipelineResult(
            run_id=planned.run_id,
            run_dir=store.root,
            state=PipelineRunState.EVALUATED,
            plan=planned.plan,
            claims=claims_tuple,
            experiments=experiments,
            conditions=conditions,
            interactions=interactions_tuple,
            analysis_checks=checks,
            artifacts=artifacts,
            evaluation_receipt=evaluation_receipt,
            evaluation_bundle=evaluation_bundle,
            source_kind=planned.request.adapter_manifest.evidence_source_kind,
        )

    @staticmethod
    def _manifest_for_binding(
        question: PlannedQuestion,
        kind: QuestionBindingKind,
        verified: Mapping[str, VerifiedNodeProjection],
    ) -> ExperimentManifest:
        bindings = [item for item in question.experiment_bindings if item.kind is kind]
        if len(bindings) != 1:
            raise AttributionError("question_binding_missing", kind.value)
        matches = [
            item
            for item in verified[bindings[0].experiment_id].experiments
            if item.question_digest == question.question_digest
        ]
        if len(matches) != 1:
            raise AttributionError("question_projection_missing", kind.value)
        return matches[0]

    @staticmethod
    def _manifest_for_spec_binding(
        question: PlannedQuestion,
        kind: QuestionBindingKind,
        specification_digest: str,
        verified: Mapping[str, VerifiedNodeProjection],
    ) -> ExperimentManifest:
        bindings = [
            item
            for item in question.experiment_bindings
            if item.kind is kind and item.specification_digest == specification_digest
        ]
        if len(bindings) != 1:
            raise AttributionError("question_binding_missing", kind.value)
        matches = [
            item
            for item in verified[bindings[0].experiment_id].experiments
            if item.question_digest == question.question_digest
        ]
        if len(matches) != 1:
            raise AttributionError("question_projection_missing", kind.value)
        return matches[0]

    @staticmethod
    def _persist_projection(store: ImmutableRunStore, projection: VerifiedNodeProjection) -> None:
        for item in projection.experiments:
            store.write(
                f"evidence/experiments/{item.digest.removeprefix('sha256:')}.json",
                canonical_bytes(item.to_dict()),
            )
        for item in projection.conditions:
            store.write(
                f"evidence/conditions/{item.digest.removeprefix('sha256:')}.json",
                canonical_bytes(item.to_dict()),
            )
        for item in projection.analysis_checks:
            store.write(
                f"evidence/analysis-checks/{item.digest.removeprefix('sha256:')}.json",
                canonical_bytes(item.to_dict()),
            )
        for item in projection.artifacts:
            store.write(
                f"artifacts/{item.digest.removeprefix('sha256:')}.json",
                item.canonical_bytes(),
            )

    @staticmethod
    def _stable_reason(exc: Exception, fallback: str) -> str:
        if (
            isinstance(exc, AttributionError)
            and exc.reason_code in _STABLE_RECEIPT_REASON_CODES
        ):
            return exc.reason_code
        return fallback

    @staticmethod
    def _record_failure(
        *,
        store: ImmutableRunStore,
        run_id: str,
        stage: str,
        subject_id: str,
        reason_code: str,
        parents: Sequence[str],
        state: PipelineRunState = PipelineRunState.FAILED,
    ) -> PipelineStageReceipt:
        receipt = PipelineStageReceipt(
            run_id=run_id,
            state=state,
            stage=stage,
            subject_id=subject_id,
            parent_digests=tuple(parents),
            reason_codes=(reason_code,),
        )
        token = AttributionPipeline._failure_token(stage, subject_id, reason_code)
        store.write(
            f"receipts/failures/{stage}-{token}.json",
            receipt.canonical_bytes(),
        )
        return receipt

    @staticmethod
    def _read_failure_receipts(
        store: ImmutableRunStore,
        expected_run_id: str,
    ) -> tuple[PipelineStageReceipt, ...]:
        directory = store.path("receipts/failures")
        if not directory.is_dir():
            return ()
        receipts: list[PipelineStageReceipt] = []
        for path in sorted(directory.glob("*.json")):
            receipt = PipelineStageReceipt.from_bytes(
                store.read(f"receipts/failures/{path.name}")
            )
            if receipt.run_id != expected_run_id:
                raise AttributionError(
                    "failure_receipt_run_mismatch",
                    "failure receipt belongs to another run",
                )
            if len(receipt.reason_codes) != 1 or receipt.output_digests:
                raise AttributionError(
                    "failure_receipt_lineage_mismatch",
                    "failure receipt must contain one reason and no outputs",
                )
            expected_state = (
                PipelineRunState.VERIFICATION_FAILED
                if receipt.stage == "verifier"
                else PipelineRunState.FAILED
            )
            if receipt.stage not in {
                "planner",
                "executor_binding",
                "executor",
                "verifier",
                "evaluator",
            } or receipt.state is not expected_state:
                raise AttributionError(
                    "failure_receipt_lineage_mismatch",
                    "failure receipt stage or state is invalid",
                )
            token = AttributionPipeline._failure_token(
                receipt.stage,
                receipt.subject_id,
                receipt.reason_codes[0],
            )
            if path.name != f"{receipt.stage}-{token}.json":
                raise AttributionError(
                    "failure_receipt_filename_mismatch",
                    "failure receipt filename is not bound to its contents",
                )
            receipts.append(receipt)
        return tuple(receipts)

    @staticmethod
    def _failure_token(stage: str, subject_id: str, reason_code: str) -> str:
        return hashlib.sha256(
            canonical_bytes(
                {
                    "stage": stage,
                    "subject_id": subject_id,
                    "reason_code": reason_code,
                }
            )
        ).hexdigest()[:24]

    @staticmethod
    def _validate_failure_receipts_without_plan(
        *,
        failures: Sequence[PipelineStageReceipt],
        request: AttributionRunRequest,
    ) -> None:
        expected_parents = (request.digest,)
        if any(
            item.stage != "planner"
            or item.subject_id != request.request_id
            or item.parent_digests != expected_parents
            for item in failures
        ):
            raise AttributionError(
                "failure_receipt_lineage_mismatch",
                "a pre-plan failure receipt has invalid lineage",
            )

    @staticmethod
    def _validate_failure_receipts(
        *,
        failures: Sequence[PipelineStageReceipt],
        request: AttributionRunRequest,
        planned: PlannedPipelineRun,
        work_by_experiment: Mapping[str, PlannedExecutionWork],
        raw_results: Mapping[str, RawExecutionResult],
        verified: Mapping[str, VerifiedNodeProjection],
    ) -> None:
        for receipt in failures:
            expected_subject: str
            expected_parents: tuple[str, ...]
            if receipt.stage == "planner":
                expected_subject = request.request_id
                expected_parents = (request.digest,)
            elif receipt.stage == "executor_binding":
                expected_subject = planned.plan.plan_id
                expected_parents = (planned.planned_receipt.digest,)
            elif receipt.stage in {"executor", "verifier"}:
                expected_subject = receipt.subject_id
                work = work_by_experiment.get(expected_subject)
                if work is None:
                    raise AttributionError(
                        "failure_receipt_lineage_mismatch",
                        "node failure does not identify a reachable planned experiment",
                    )
                if receipt.stage == "executor":
                    expected_parents = (planned.planned_receipt.digest, work.digest)
                else:
                    raw = raw_results.get(expected_subject)
                    if raw is None:
                        raise AttributionError(
                            "failure_receipt_lineage_mismatch",
                            "verifier failure has no matching raw execution",
                        )
                    expected_parents = (work.digest, raw.digest)
            else:
                expected_subject = planned.plan.plan_id
                if len(verified) != len(planned.plan.experiments):
                    raise AttributionError(
                        "failure_receipt_lineage_mismatch",
                        "evaluator failure requires every verified node",
                    )
                expected_parents = tuple(item.record.digest for item in verified.values())
            if (
                receipt.subject_id != expected_subject
                or receipt.parent_digests != tuple(sorted(set(expected_parents)))
            ):
                raise AttributionError(
                    "failure_receipt_lineage_mismatch",
                    "failure receipt is not bound to the current stage lineage",
                )

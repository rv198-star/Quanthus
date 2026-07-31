"""Deterministic, outcome-blind compilation of strategy attribution experiment plans."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from types import MappingProxyType
from typing import Any

from quanthus.strategy_attribution import (
    CONDITION_EFFECT_METHOD,
    INTERACTION_EFFECT_METHOD,
    METHOD_REGISTRY_VERSION,
    NOT_REQUIRED_CHECK_METHOD,
    AdapterCapabilityManifest,
    AttributionCapability,
    AttributionError,
    AttributionMethodKind,
    AttributionQuestion,
    MetricSpec,
    StrategyAttributionAdapter,
    canonical_bytes,
    digest,
    registered_attribution_methods,
)

RUN_REQUEST_SCHEMA = "quanthus.strategy-attribution-run-request.v1"
ATTRIBUTION_PLAN_SCHEMA = "quanthus.strategy-attribution-plan.v1"
PLANNED_EXPERIMENT_SCHEMA = "quanthus.strategy-attribution-planned-experiment.v1"
PLANNER_ID = "quanthus.strategy-attribution-plan-compiler"
PLANNER_VERSION = "1.0.0"

REQUIRED_REPLAY_CONTEXT_BINDINGS = frozenset(
    {
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
)


class OutcomeAccessPolicy(str, Enum):
    """The planning phase cannot inspect target-period outcomes."""

    FORBID_UNTIL_PLAN_PERSISTED = "forbid_target_outcomes_until_plan_persisted"


class PlanNodeKind(str, Enum):
    BASELINE_REPLAY = "baseline_replay"
    SINGLE_INTERVENTION = "single_intervention"
    JOINT_INTERVENTION = "joint_intervention"
    CONDITION_ANALYSIS = "condition_analysis"
    UNCERTAINTY_CHECK = "uncertainty_check"
    STABILITY_CHECK = "stability_check"


class QuestionPlanStatus(str, Enum):
    READY = "ready"
    UNAVAILABLE = "unavailable"
    REJECTED = "rejected"


class QuestionBindingKind(str, Enum):
    BASELINE = "baseline"
    SUBJECT_INTERVENTION = "subject_intervention"
    INTERACTION_PARTNER = "interaction_partner"
    JOINT_INTERVENTION = "joint_intervention"
    CONDITION_ANALYSIS = "condition_analysis"
    UNCERTAINTY_CHECK = "uncertainty_check"
    STABILITY_CHECK = "stability_check"


class PlanReasonCode(str, Enum):
    ADAPTER_BINDING_DRIFT = "adapter_binding_drift"
    METHOD_REGISTRY_DRIFT = "method_registry_drift"
    QUESTION_NOT_PREDECLARED = "question_not_predeclared"
    MISSING_REQUIRED_CAPABILITY = "missing_required_capability"
    METHOD_NOT_REGISTERED = "method_not_registered"
    METHOD_KIND_MISMATCH = "method_kind_mismatch"
    REQUIRED_METHOD_UNAVAILABLE = "required_method_unavailable"
    UNEXPECTED_CHECK_METHOD = "unexpected_check_method"
    UNKNOWN_SUBJECT_COMPONENT = "unknown_subject_component"
    UNDECLARED_INTERVENTION = "undeclared_intervention"
    INTERVENTION_SPEC_DRIFT = "intervention_spec_drift"
    UNKNOWN_PARTNER_COMPONENT = "unknown_partner_component"
    UNDECLARED_PARTNER_INTERVENTION = "undeclared_partner_intervention"
    PARTNER_INTERVENTION_SPEC_DRIFT = "partner_intervention_spec_drift"
    UNDECLARED_JOINT_INTERVENTION = "undeclared_joint_intervention"
    JOINT_MEMBER_CONFLICT = "joint_member_conflict"
    COMPOSITION_ORDER_CONFLICT = "composition_order_conflict"
    JOINT_SEMANTICS_CONFLICT = "joint_semantics_conflict"
    METRIC_DEFINITION_CONFLICT = "metric_definition_conflict"


_UNAVAILABLE_REASON_CODES = frozenset(
    {
        PlanReasonCode.MISSING_REQUIRED_CAPABILITY,
        PlanReasonCode.REQUIRED_METHOD_UNAVAILABLE,
    }
)


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AttributionError("invalid_identity", f"{field_name} must not be empty")
    return value


def _require_digest(value: object, field_name: str) -> str:
    text = _require_text(value, field_name)
    prefix = "sha256:"
    encoded = text[len(prefix) :] if text.startswith(prefix) else ""
    if len(encoded) != 64 or any(item not in "0123456789abcdef" for item in encoded):
        raise AttributionError("invalid_digest", f"{field_name} must be a lowercase sha256 digest")
    return text


def _unique_text(
    values: Sequence[str], field_name: str, *, sort_values: bool = False
) -> tuple[str, ...]:
    normalized = tuple(values)
    if any(not isinstance(item, str) or not item.strip() for item in normalized):
        raise AttributionError("invalid_identity", f"{field_name} contains an empty value")
    if len(normalized) != len(set(normalized)):
        raise AttributionError("duplicate_identity", f"{field_name} must not contain duplicates")
    return tuple(sorted(normalized)) if sort_values else normalized


def _text_mapping(values: Mapping[str, str], field_name: str) -> Mapping[str, str]:
    normalized = dict(values)
    for key, value in normalized.items():
        _require_text(key, f"{field_name}.key")
        _require_text(value, f"{field_name}.{key}")
    return MappingProxyType(dict(sorted(normalized.items())))


@dataclass(frozen=True)
class PlanReason:
    code: PlanReasonCode
    details: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.code, PlanReasonCode):
            raise AttributionError("invalid_plan_reason", "reason code must use the registry")
        object.__setattr__(
            self,
            "details",
            _unique_text(self.details, "plan_reason_details", sort_values=True),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code.value, "details": list(self.details)}


@dataclass(frozen=True)
class FrozenAdapterBinding:
    adapter_id: str
    adapter_version: str
    capability_manifest_version: str
    capability_manifest_digest: str
    strategy_id: str
    strategy_version: str
    native_topology_digest: str

    def __post_init__(self) -> None:
        for field_name in (
            "adapter_id",
            "adapter_version",
            "capability_manifest_version",
            "strategy_id",
            "strategy_version",
        ):
            _require_text(getattr(self, field_name), field_name)
        _require_digest(self.capability_manifest_digest, "capability_manifest_digest")
        _require_digest(self.native_topology_digest, "native_topology_digest")

    @classmethod
    def from_manifest(cls, manifest: AdapterCapabilityManifest) -> FrozenAdapterBinding:
        return cls(
            adapter_id=manifest.adapter_id,
            adapter_version=manifest.adapter_version,
            capability_manifest_version=manifest.capability_manifest_version,
            capability_manifest_digest=manifest.digest,
            strategy_id=manifest.strategy_id,
            strategy_version=manifest.strategy_version,
            native_topology_digest=manifest.native_topology_digest,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "capability_manifest_version": self.capability_manifest_version,
            "capability_manifest_digest": self.capability_manifest_digest,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "native_topology_digest": self.native_topology_digest,
        }


StrategyAttributionSemanticAdapter = StrategyAttributionAdapter


@dataclass(frozen=True)
class AttributionRunRequest:
    """A frozen, self-contained request containing definitions but no target results."""

    adapter_binding: FrozenAdapterBinding
    adapter_manifest: AdapterCapabilityManifest
    questions: tuple[AttributionQuestion, ...]
    context_bindings: Mapping[str, str]
    period_start: date
    period_end: date
    metric_calculator_id: str
    metric_calculator_version: str
    capital_constrained: bool = True
    point_in_time_required: bool = True
    method_registry_version: str = METHOD_REGISTRY_VERSION
    schema_version: str = RUN_REQUEST_SCHEMA
    outcome_access_policy: OutcomeAccessPolicy = OutcomeAccessPolicy.FORBID_UNTIL_PLAN_PERSISTED

    def __post_init__(self) -> None:
        if not isinstance(self.adapter_binding, FrozenAdapterBinding):
            raise AttributionError("invalid_adapter_binding", "adapter binding is invalid")
        if not isinstance(self.adapter_manifest, AdapterCapabilityManifest):
            raise AttributionError("invalid_adapter_manifest", "adapter manifest is invalid")
        if self.schema_version != RUN_REQUEST_SCHEMA:
            raise AttributionError("unsupported_schema", "run request schema is unsupported")
        if self.outcome_access_policy is not OutcomeAccessPolicy.FORBID_UNTIL_PLAN_PERSISTED:
            raise AttributionError(
                "outcome_access_not_forbidden",
                "planning must forbid target outcome access until the plan is persisted",
            )
        for field_name in ("metric_calculator_id", "metric_calculator_version"):
            _require_text(getattr(self, field_name), field_name)
        _require_text(self.method_registry_version, "method_registry_version")
        if self.capital_constrained is not True:
            raise AttributionError(
                "capital_constraint_required",
                "attribution planning requires a capital-constrained replay",
            )
        if self.point_in_time_required is not True:
            raise AttributionError(
                "point_in_time_required",
                "attribution planning requires point-in-time inputs",
            )
        if not isinstance(self.period_start, date) or not isinstance(self.period_end, date):
            raise AttributionError("invalid_period", "period bounds must be dates")
        if self.period_start > self.period_end:
            raise AttributionError("invalid_period", "period_start must not follow period_end")
        bindings = _text_mapping(self.context_bindings, "context_bindings")
        missing_context = sorted(REQUIRED_REPLAY_CONTEXT_BINDINGS.difference(bindings))
        if missing_context:
            raise AttributionError(
                "incomplete_context_binding",
                f"run request is missing context bindings: {missing_context}",
            )
        object.__setattr__(self, "context_bindings", bindings)
        questions = tuple(self.questions)
        if not questions:
            raise AttributionError("missing_question", "run request requires at least one question")
        if any(not isinstance(item, AttributionQuestion) for item in questions):
            raise AttributionError("invalid_question", "questions contain an invalid value")
        question_ids = [item.question_id for item in questions]
        question_digests = [item.digest for item in questions]
        if len(question_ids) != len(set(question_ids)):
            raise AttributionError("duplicate_question", "question ids must be unique")
        if len(question_digests) != len(set(question_digests)):
            raise AttributionError("duplicate_question", "question digests must be unique")
        object.__setattr__(
            self,
            "questions",
            tuple(sorted(questions, key=lambda item: (item.question_id, item.digest))),
        )

    @classmethod
    def create(
        cls,
        *,
        adapter_manifest: AdapterCapabilityManifest,
        questions: Sequence[AttributionQuestion],
        context_bindings: Mapping[str, str],
        period_start: date,
        period_end: date,
        metric_calculator_id: str,
        metric_calculator_version: str,
    ) -> AttributionRunRequest:
        return cls(
            adapter_binding=FrozenAdapterBinding.from_manifest(adapter_manifest),
            adapter_manifest=adapter_manifest,
            questions=tuple(questions),
            context_bindings=context_bindings,
            period_start=period_start,
            period_end=period_end,
            metric_calculator_id=metric_calculator_id,
            metric_calculator_version=metric_calculator_version,
        )

    @classmethod
    def create_from_adapter(
        cls,
        *,
        adapter: StrategyAttributionSemanticAdapter,
        context_bindings: Mapping[str, str],
        period_start: date,
        period_end: date,
        metric_calculator_id: str,
        metric_calculator_version: str,
    ) -> AttributionRunRequest:
        if not isinstance(adapter, StrategyAttributionSemanticAdapter):
            raise AttributionError(
                "invalid_semantic_adapter",
                "adapter must expose only frozen capability and question semantics",
            )
        return cls.create(
            adapter_manifest=adapter.capability_manifest(),
            questions=adapter.questions(),
            context_bindings=context_bindings,
            period_start=period_start,
            period_end=period_end,
            metric_calculator_id=metric_calculator_id,
            metric_calculator_version=metric_calculator_version,
        )

    @property
    def context_digest(self) -> str:
        return digest(dict(self.context_bindings))

    @property
    def execution_scope_digest(self) -> str:
        return digest(
            {
                "adapter_manifest_digest": self.adapter_manifest.digest,
                "strategy_id": self.adapter_manifest.strategy_id,
                "strategy_version": self.adapter_manifest.strategy_version,
                "native_topology_digest": self.adapter_manifest.native_topology_digest,
                "context_digest": self.context_digest,
                "context_bindings": dict(self.context_bindings),
                "period_start": self.period_start.isoformat(),
                "period_end": self.period_end.isoformat(),
                "metric_calculator_id": self.metric_calculator_id,
                "metric_calculator_version": self.metric_calculator_version,
                "capital_constrained": self.capital_constrained,
                "point_in_time_required": self.point_in_time_required,
                "method_registry_version": self.method_registry_version,
            }
        )

    @property
    def digest(self) -> str:
        return digest(self._semantic_dict())

    @property
    def request_id(self) -> str:
        return f"attribution-request:{self.digest.removeprefix('sha256:')}"

    def _semantic_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "outcome_access_policy": self.outcome_access_policy.value,
            "adapter_binding": self.adapter_binding.to_dict(),
            "adapter_manifest": self.adapter_manifest.to_dict(),
            "questions": [item.to_dict() for item in self.questions],
            "context_digest": self.context_digest,
            "context_bindings": dict(self.context_bindings),
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "metric_calculator_id": self.metric_calculator_id,
            "metric_calculator_version": self.metric_calculator_version,
            "capital_constrained": self.capital_constrained,
            "point_in_time_required": self.point_in_time_required,
            "method_registry_version": self.method_registry_version,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._semantic_dict()
        payload["request_id"] = self.request_id
        payload["digest"] = self.digest
        return payload

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


def _planned_execution_key(
    *,
    kind: PlanNodeKind,
    execution_scope_digest: str,
    dependency_experiment_ids: Sequence[str],
    metric_spec_digests: Mapping[str, str],
    component_ids: Sequence[str],
    intervention_ids: Sequence[str],
    intervention_spec_digests: Sequence[str],
    required_capabilities: Sequence[AttributionCapability],
    joint_intervention_spec_digest: str | None,
    condition_spec_digest: str | None,
    method_id: str | None,
) -> str:
    dependency_identity = (
        sorted(dependency_experiment_ids)
        if kind
        in {
            PlanNodeKind.CONDITION_ANALYSIS,
            PlanNodeKind.UNCERTAINTY_CHECK,
            PlanNodeKind.STABILITY_CHECK,
        }
        else []
    )
    return digest(
        {
            "kind": kind.value,
            "execution_scope_digest": execution_scope_digest,
            "dependency_experiment_ids": dependency_identity,
            "metric_spec_digests": dict(sorted(metric_spec_digests.items())),
            "component_ids": list(component_ids),
            "intervention_ids": list(intervention_ids),
            "intervention_spec_digests": list(intervention_spec_digests),
            "required_capabilities": sorted(item.value for item in required_capabilities),
            "joint_intervention_spec_digest": joint_intervention_spec_digest,
            "condition_spec_digest": condition_spec_digest,
            "method_id": method_id,
        }
    )


@dataclass(frozen=True)
class PlannedExperiment:
    """One question-neutral physical execution or analysis unit."""

    kind: PlanNodeKind
    execution_scope_digest: str
    dependency_experiment_ids: tuple[str, ...]
    metric_spec_digests: Mapping[str, str]
    component_ids: tuple[str, ...] = ()
    intervention_ids: tuple[str, ...] = ()
    intervention_spec_digests: tuple[str, ...] = ()
    required_capabilities: tuple[AttributionCapability, ...] = ()
    joint_intervention_spec_digest: str | None = None
    condition_spec_digest: str | None = None
    method_id: str | None = None
    schema_version: str = PLANNED_EXPERIMENT_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != PLANNED_EXPERIMENT_SCHEMA:
            raise AttributionError("unsupported_schema", "planned experiment schema is unsupported")
        if not isinstance(self.kind, PlanNodeKind):
            raise AttributionError("invalid_plan_node_kind", "node kind must use the registry")
        _require_digest(self.execution_scope_digest, "execution_scope_digest")
        object.__setattr__(
            self,
            "dependency_experiment_ids",
            _unique_text(
                self.dependency_experiment_ids,
                "dependency_experiment_ids",
                sort_values=True,
            ),
        )
        components = _unique_text(self.component_ids, "component_ids")
        interventions = _unique_text(self.intervention_ids, "intervention_ids")
        intervention_digests = tuple(self.intervention_spec_digests)
        for item in intervention_digests:
            _require_digest(item, "intervention_spec_digest")
        if len(intervention_digests) != len(set(intervention_digests)):
            raise AttributionError(
                "duplicate_identity", "intervention spec digests must not contain duplicates"
            )
        object.__setattr__(self, "component_ids", components)
        object.__setattr__(self, "intervention_ids", interventions)
        object.__setattr__(self, "intervention_spec_digests", intervention_digests)
        capabilities = tuple(self.required_capabilities)
        if any(not isinstance(item, AttributionCapability) for item in capabilities):
            raise AttributionError(
                "invalid_capability", "planned capabilities must use the registry"
            )
        if len(capabilities) != len(set(capabilities)):
            raise AttributionError("duplicate_capability", "planned capabilities must not repeat")
        object.__setattr__(
            self,
            "required_capabilities",
            tuple(sorted(capabilities, key=lambda item: item.value)),
        )
        metric_digests = _text_mapping(self.metric_spec_digests, "metric_spec_digests")
        if not metric_digests:
            raise AttributionError("missing_metric", "planned experiment requires metrics")
        for item in metric_digests.values():
            _require_digest(item, "metric_spec_digest")
        object.__setattr__(self, "metric_spec_digests", metric_digests)
        if self.joint_intervention_spec_digest is not None:
            _require_digest(
                self.joint_intervention_spec_digest,
                "joint_intervention_spec_digest",
            )
        if self.condition_spec_digest is not None:
            _require_digest(self.condition_spec_digest, "condition_spec_digest")
        if self.method_id is not None:
            _require_text(self.method_id, "method_id")
        self._validate_shape()

    def _validate_shape(self) -> None:
        if self.kind is PlanNodeKind.BASELINE_REPLAY:
            if any(
                (
                    self.component_ids,
                    self.intervention_ids,
                    self.intervention_spec_digests,
                    self.dependency_experiment_ids,
                )
            ):
                raise AttributionError(
                    "invalid_baseline_plan", "baseline plan cannot declare changes"
                )
        elif self.kind is PlanNodeKind.SINGLE_INTERVENTION:
            if not (
                len(self.component_ids)
                == len(self.intervention_ids)
                == len(self.intervention_spec_digests)
                == 1
            ):
                raise AttributionError(
                    "invalid_single_intervention_plan",
                    "single intervention plan requires one aligned intervention",
                )
            if len(self.dependency_experiment_ids) != 1:
                raise AttributionError(
                    "invalid_plan_dependency", "single intervention requires one baseline"
                )
        elif self.kind is PlanNodeKind.JOINT_INTERVENTION:
            if len(self.intervention_ids) < 2 or not (
                len(self.component_ids)
                == len(self.intervention_ids)
                == len(self.intervention_spec_digests)
            ):
                raise AttributionError(
                    "invalid_joint_intervention_plan",
                    "joint intervention members must align by position",
                )
            if self.joint_intervention_spec_digest is None:
                raise AttributionError(
                    "missing_joint_intervention_spec",
                    "joint intervention plan requires its adapter spec",
                )
            if len(self.dependency_experiment_ids) != len(self.intervention_ids) + 1:
                raise AttributionError(
                    "invalid_plan_dependency",
                    "joint intervention requires baseline and member intervention parents",
                )
        elif self.kind is PlanNodeKind.CONDITION_ANALYSIS:
            if self.condition_spec_digest is None or self.method_id is None:
                raise AttributionError(
                    "invalid_condition_plan", "condition analysis requires a spec and method"
                )
            if len(self.dependency_experiment_ids) != 2:
                raise AttributionError(
                    "invalid_plan_dependency",
                    "condition analysis requires baseline and intervention parents",
                )
            if not (
                len(self.component_ids)
                == len(self.intervention_ids)
                == len(self.intervention_spec_digests)
                == 1
            ):
                raise AttributionError(
                    "invalid_condition_plan",
                    "condition analysis requires one aligned subject intervention",
                )
        elif self.kind in {
            PlanNodeKind.UNCERTAINTY_CHECK,
            PlanNodeKind.STABILITY_CHECK,
        } and (self.method_id is None or len(self.dependency_experiment_ids) != 2):
            raise AttributionError(
                "invalid_check_plan",
                "analysis check requires a method, baseline, and intervention",
            )
        if self.kind in {
            PlanNodeKind.UNCERTAINTY_CHECK,
            PlanNodeKind.STABILITY_CHECK,
        } and not (
            len(self.component_ids)
            == len(self.intervention_ids)
            == len(self.intervention_spec_digests)
            == 1
        ):
            raise AttributionError(
                "invalid_check_plan",
                "analysis check requires one aligned subject intervention",
            )
        if (
            self.kind is not PlanNodeKind.JOINT_INTERVENTION
            and self.joint_intervention_spec_digest is not None
        ):
            raise AttributionError(
                "unexpected_joint_intervention_spec",
                "only joint intervention nodes may bind a joint spec",
            )
        if (
            self.kind is not PlanNodeKind.CONDITION_ANALYSIS
            and self.condition_spec_digest is not None
        ):
            raise AttributionError(
                "unexpected_condition_spec",
                "only condition analysis nodes may bind a condition spec",
            )
        if (
            self.kind
            in {
                PlanNodeKind.BASELINE_REPLAY,
                PlanNodeKind.SINGLE_INTERVENTION,
                PlanNodeKind.JOINT_INTERVENTION,
            }
            and self.method_id is not None
        ):
            raise AttributionError(
                "unexpected_method", "physical replay nodes do not dispatch analysis methods"
            )

    @property
    def execution_key(self) -> str:
        return _planned_execution_key(
            kind=self.kind,
            execution_scope_digest=self.execution_scope_digest,
            dependency_experiment_ids=self.dependency_experiment_ids,
            metric_spec_digests=self.metric_spec_digests,
            component_ids=self.component_ids,
            intervention_ids=self.intervention_ids,
            intervention_spec_digests=self.intervention_spec_digests,
            required_capabilities=self.required_capabilities,
            joint_intervention_spec_digest=self.joint_intervention_spec_digest,
            condition_spec_digest=self.condition_spec_digest,
            method_id=self.method_id,
        )

    @property
    def experiment_id(self) -> str:
        return f"planned:{self.kind.value}:{self.execution_key.removeprefix('sha256:')}"

    @property
    def digest(self) -> str:
        return digest(self._semantic_dict())

    def _semantic_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "execution_key": self.execution_key,
            "kind": self.kind.value,
            "execution_scope_digest": self.execution_scope_digest,
            "dependency_experiment_ids": list(self.dependency_experiment_ids),
            "metric_spec_digests": dict(self.metric_spec_digests),
            "component_ids": list(self.component_ids),
            "intervention_ids": list(self.intervention_ids),
            "intervention_spec_digests": list(self.intervention_spec_digests),
            "required_capabilities": [item.value for item in self.required_capabilities],
            "joint_intervention_spec_digest": self.joint_intervention_spec_digest,
            "condition_spec_digest": self.condition_spec_digest,
            "method_id": self.method_id,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._semantic_dict()
        payload["digest"] = self.digest
        return payload


@dataclass(frozen=True)
class QuestionExperimentBinding:
    kind: QuestionBindingKind
    experiment_id: str
    specification_digest: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, QuestionBindingKind):
            raise AttributionError("invalid_question_binding", "binding kind is invalid")
        _require_text(self.experiment_id, "experiment_id")
        if self.specification_digest is not None:
            _require_digest(self.specification_digest, "specification_digest")

    def to_dict(self) -> dict[str, str | None]:
        return {
            "kind": self.kind.value,
            "experiment_id": self.experiment_id,
            "specification_digest": self.specification_digest,
        }


@dataclass(frozen=True)
class PlannedQuestion:
    question_id: str
    question_version: str
    question_digest: str
    status: QuestionPlanStatus
    reasons: tuple[PlanReason, ...] = ()
    experiment_bindings: tuple[QuestionExperimentBinding, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.question_id, "question_id")
        _require_text(self.question_version, "question_version")
        _require_digest(self.question_digest, "question_digest")
        if not isinstance(self.status, QuestionPlanStatus):
            raise AttributionError("invalid_question_plan_status", "question status is invalid")
        reasons = tuple(self.reasons)
        if any(not isinstance(item, PlanReason) for item in reasons):
            raise AttributionError("invalid_plan_reason", "question reasons are invalid")
        object.__setattr__(
            self,
            "reasons",
            tuple(sorted(reasons, key=lambda item: (item.code.value, item.details))),
        )
        bindings = tuple(self.experiment_bindings)
        if any(not isinstance(item, QuestionExperimentBinding) for item in bindings):
            raise AttributionError("invalid_question_binding", "question bindings are invalid")
        binding_keys = [
            (item.kind.value, item.experiment_id, item.specification_digest) for item in bindings
        ]
        if len(binding_keys) != len(set(binding_keys)):
            raise AttributionError("duplicate_question_binding", "question bindings must be unique")
        object.__setattr__(
            self,
            "experiment_bindings",
            tuple(
                sorted(
                    bindings,
                    key=lambda item: (
                        item.kind.value,
                        item.specification_digest or "",
                        item.experiment_id,
                    ),
                )
            ),
        )
        if self.status is QuestionPlanStatus.READY:
            if self.reasons or not self.experiment_bindings:
                raise AttributionError(
                    "invalid_ready_question", "ready question requires bindings and no reasons"
                )
        elif self.experiment_bindings or not self.reasons:
            raise AttributionError(
                "invalid_nonready_question",
                "unavailable or rejected questions require reasons and no execution bindings",
            )

    @property
    def experiment_ids(self) -> tuple[str, ...]:
        return tuple(sorted({item.experiment_id for item in self.experiment_bindings}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "question_version": self.question_version,
            "question_digest": self.question_digest,
            "status": self.status.value,
            "reasons": [item.to_dict() for item in self.reasons],
            "experiment_bindings": [item.to_dict() for item in self.experiment_bindings],
        }


def _stable_topological_experiments(
    experiments: Sequence[PlannedExperiment],
) -> tuple[PlannedExperiment, ...]:
    by_id = {item.experiment_id: item for item in experiments}
    remaining = {item.experiment_id: set(item.dependency_experiment_ids) for item in experiments}
    unknown = sorted(
        dependency
        for dependencies in remaining.values()
        for dependency in dependencies
        if dependency not in by_id
    )
    if unknown:
        raise AttributionError(
            "missing_plan_dependency", f"plan dependencies are missing: {unknown}"
        )
    ready = sorted(key for key, dependencies in remaining.items() if not dependencies)
    ordered: list[PlannedExperiment] = []
    emitted: set[str] = set()
    while ready:
        experiment_id = ready.pop(0)
        ordered.append(by_id[experiment_id])
        emitted.add(experiment_id)
        for candidate in sorted(remaining):
            dependencies = remaining[candidate]
            if experiment_id not in dependencies:
                continue
            dependencies.remove(experiment_id)
            if not dependencies and candidate not in emitted and candidate not in ready:
                ready.append(candidate)
                ready.sort()
    if len(ordered) != len(experiments):
        raise AttributionError("cyclic_plan", "experiment dependencies contain a cycle")
    return tuple(ordered)


@dataclass(frozen=True)
class AttributionPlan:
    run_request: AttributionRunRequest
    experiments: tuple[PlannedExperiment, ...]
    planned_questions: tuple[PlannedQuestion, ...]
    compiler_id: str = PLANNER_ID
    compiler_version: str = PLANNER_VERSION
    method_registry_version: str = METHOD_REGISTRY_VERSION
    schema_version: str = ATTRIBUTION_PLAN_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != ATTRIBUTION_PLAN_SCHEMA:
            raise AttributionError("unsupported_schema", "attribution plan schema is unsupported")
        if (self.compiler_id, self.compiler_version) != (PLANNER_ID, PLANNER_VERSION):
            raise AttributionError("compiler_version_drift", "plan compiler is unsupported")
        if self.method_registry_version != METHOD_REGISTRY_VERSION:
            raise AttributionError(
                "method_registry_drift", "method registry version is unsupported"
            )
        if not isinstance(self.run_request, AttributionRunRequest):
            raise AttributionError("invalid_run_request", "plan run request is invalid")
        experiments = tuple(self.experiments)
        if any(not isinstance(item, PlannedExperiment) for item in experiments):
            raise AttributionError("invalid_planned_experiment", "plan experiments are invalid")
        experiment_ids = [item.experiment_id for item in experiments]
        if len(experiment_ids) != len(set(experiment_ids)):
            raise AttributionError("duplicate_planned_experiment", "experiment ids must be unique")
        object.__setattr__(
            self,
            "experiments",
            _stable_topological_experiments(experiments),
        )
        questions = tuple(self.planned_questions)
        if any(not isinstance(item, PlannedQuestion) for item in questions):
            raise AttributionError("invalid_planned_question", "planned questions are invalid")
        object.__setattr__(
            self,
            "planned_questions",
            tuple(sorted(questions, key=lambda item: (item.question_id, item.question_digest))),
        )
        self._validate_closure()

    def _validate_closure(self) -> None:
        experiment_by_id = {item.experiment_id: item for item in self.experiments}
        request_questions = {item.digest: item for item in self.run_request.questions}
        planned_question_digests = [item.question_digest for item in self.planned_questions]
        if set(planned_question_digests) != set(request_questions):
            raise AttributionError(
                "question_plan_closure_mismatch",
                "plan must disposition every request question exactly once",
            )
        if len(planned_question_digests) != len(set(planned_question_digests)):
            raise AttributionError("duplicate_planned_question", "question plans must be unique")
        referenced: set[str] = set()
        for planned in self.planned_questions:
            source = request_questions[planned.question_digest]
            if (planned.question_id, planned.question_version) != (
                source.question_id,
                source.question_version,
            ):
                raise AttributionError(
                    "question_plan_identity_mismatch", "planned question identity drifted"
                )
            if planned.status is QuestionPlanStatus.READY:
                _validate_question_bindings(planned, source, experiment_by_id)
            for experiment_id in planned.experiment_ids:
                if experiment_id not in experiment_by_id:
                    raise AttributionError(
                        "missing_planned_experiment",
                        "question binding references an unknown experiment",
                    )
                referenced.add(experiment_id)
        if referenced != set(experiment_by_id):
            raise AttributionError(
                "orphan_planned_experiment", "every experiment must serve a ready question"
            )
        graph: dict[str, tuple[str, ...]] = {}
        for experiment in self.experiments:
            unknown = sorted(set(experiment.dependency_experiment_ids).difference(experiment_by_id))
            if unknown:
                raise AttributionError(
                    "missing_plan_dependency", f"plan dependencies are missing: {unknown}"
                )
            dependency_kinds = sorted(
                experiment_by_id[item].kind.value for item in experiment.dependency_experiment_ids
            )
            expected_dependency_kinds: list[str]
            if experiment.kind is PlanNodeKind.BASELINE_REPLAY:
                expected_dependency_kinds = []
            elif experiment.kind is PlanNodeKind.SINGLE_INTERVENTION:
                expected_dependency_kinds = [PlanNodeKind.BASELINE_REPLAY.value]
            elif experiment.kind is PlanNodeKind.JOINT_INTERVENTION:
                expected_dependency_kinds = sorted(
                    [PlanNodeKind.BASELINE_REPLAY.value]
                    + [PlanNodeKind.SINGLE_INTERVENTION.value] * len(experiment.intervention_ids)
                )
            else:
                expected_dependency_kinds = sorted(
                    [
                        PlanNodeKind.BASELINE_REPLAY.value,
                        PlanNodeKind.SINGLE_INTERVENTION.value,
                    ]
                )
            if dependency_kinds != expected_dependency_kinds:
                raise AttributionError(
                    "invalid_plan_dependency_kind",
                    "experiment dependencies do not match the node contract",
                )
            graph[experiment.experiment_id] = experiment.dependency_experiment_ids
        _validate_acyclic(graph)
        expected_experiments, expected_questions = _compile_plan_parts(self.run_request)
        actual_compilation = canonical_bytes(
            {
                "experiments": [item.to_dict() for item in self.experiments],
                "planned_questions": [item.to_dict() for item in self.planned_questions],
            }
        )
        expected_compilation = canonical_bytes(
            {
                "experiments": [item.to_dict() for item in expected_experiments],
                "planned_questions": [item.to_dict() for item in expected_questions],
            }
        )
        if actual_compilation != expected_compilation:
            raise AttributionError(
                "plan_compilation_mismatch",
                "plan is not the unique compiler output for its frozen run request",
            )

    @property
    def digest(self) -> str:
        return digest(self._semantic_dict())

    @property
    def plan_id(self) -> str:
        return f"attribution-plan:{self.digest.removeprefix('sha256:')}"

    def _semantic_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "compiler_id": self.compiler_id,
            "compiler_version": self.compiler_version,
            "method_registry_version": self.method_registry_version,
            "run_request": self.run_request.to_dict(),
            "run_request_digest": self.run_request.digest,
            "experiments": [item.to_dict() for item in self.experiments],
            "planned_questions": [item.to_dict() for item in self.planned_questions],
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._semantic_dict()
        payload["plan_id"] = self.plan_id
        payload["digest"] = self.digest
        return payload

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())


def _validate_question_bindings(
    planned: PlannedQuestion,
    source: AttributionQuestion,
    experiment_by_id: Mapping[str, PlannedExperiment],
) -> None:
    expected_kinds = {
        QuestionBindingKind.BASELINE: PlanNodeKind.BASELINE_REPLAY,
        QuestionBindingKind.SUBJECT_INTERVENTION: PlanNodeKind.SINGLE_INTERVENTION,
        QuestionBindingKind.INTERACTION_PARTNER: PlanNodeKind.SINGLE_INTERVENTION,
        QuestionBindingKind.JOINT_INTERVENTION: PlanNodeKind.JOINT_INTERVENTION,
        QuestionBindingKind.CONDITION_ANALYSIS: PlanNodeKind.CONDITION_ANALYSIS,
        QuestionBindingKind.UNCERTAINTY_CHECK: PlanNodeKind.UNCERTAINTY_CHECK,
        QuestionBindingKind.STABILITY_CHECK: PlanNodeKind.STABILITY_CHECK,
    }
    actual_specs: dict[QuestionBindingKind, list[str | None]] = {
        item: [] for item in QuestionBindingKind
    }
    for binding in planned.experiment_bindings:
        experiment = experiment_by_id.get(binding.experiment_id)
        if experiment is None:
            raise AttributionError(
                "missing_planned_experiment", "question binding references an unknown experiment"
            )
        if experiment.kind is not expected_kinds[binding.kind]:
            raise AttributionError(
                "question_binding_kind_mismatch",
                "question binding references the wrong experiment kind",
            )
        actual_specs[binding.kind].append(binding.specification_digest)
    interaction_digests = sorted(item.digest for item in source.interaction_specs)

    def method_spec(method_id: str) -> str:
        return digest(
            {
                "method_registry_version": METHOD_REGISTRY_VERSION,
                "method_id": method_id,
            }
        )

    expected_specs: dict[QuestionBindingKind, list[str | None]] = {
        QuestionBindingKind.BASELINE: [None],
        QuestionBindingKind.SUBJECT_INTERVENTION: [source.intervention_spec_digest],
        QuestionBindingKind.INTERACTION_PARTNER: interaction_digests,
        QuestionBindingKind.JOINT_INTERVENTION: interaction_digests,
        QuestionBindingKind.CONDITION_ANALYSIS: sorted(
            item.digest for item in source.condition_specs
        ),
        QuestionBindingKind.UNCERTAINTY_CHECK: (
            [method_spec(source.uncertainty_method)] if source.uncertainty_required else []
        ),
        QuestionBindingKind.STABILITY_CHECK: (
            [method_spec(source.stability_method)] if source.stability_required else []
        ),
    }
    for kind in QuestionBindingKind:
        if sorted(actual_specs[kind], key=lambda item: item or "") != sorted(
            expected_specs[kind], key=lambda item: item or ""
        ):
            raise AttributionError(
                "question_binding_closure_mismatch",
                f"question bindings are incomplete for {kind.value}",
            )


def _validate_acyclic(graph: Mapping[str, tuple[str, ...]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise AttributionError("cyclic_plan", "experiment dependencies contain a cycle")
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph[node]:
            visit(dependency)
        visiting.remove(node)
        visited.add(node)

    for experiment_id in sorted(graph):
        visit(experiment_id)


@dataclass
class _ExperimentDraft:
    kind: PlanNodeKind
    execution_scope_digest: str
    component_ids: tuple[str, ...] = ()
    intervention_ids: tuple[str, ...] = ()
    intervention_spec_digests: tuple[str, ...] = ()
    joint_intervention_spec_digest: str | None = None
    condition_spec_digest: str | None = None
    method_id: str | None = None
    dependency_keys: set[str] = field(default_factory=set)
    metric_spec_digests: dict[str, str] = field(default_factory=dict)
    required_capabilities: set[AttributionCapability] = field(default_factory=set)

    @property
    def key(self) -> str:
        return digest(
            {
                "kind": self.kind.value,
                "execution_scope_digest": self.execution_scope_digest,
                "dependency_keys": sorted(self.dependency_keys),
                "component_ids": list(self.component_ids),
                "intervention_ids": list(self.intervention_ids),
                "intervention_spec_digests": list(self.intervention_spec_digests),
                "joint_intervention_spec_digest": self.joint_intervention_spec_digest,
                "condition_spec_digest": self.condition_spec_digest,
                "method_id": self.method_id,
            }
        )

    def add_metrics(self, specs: Sequence[MetricSpec]) -> None:
        for spec in specs:
            existing = self.metric_spec_digests.get(spec.metric_id)
            if existing is not None and existing != spec.digest:
                raise AttributionError(
                    "metric_definition_conflict",
                    f"metric {spec.metric_id} has conflicting definitions",
                )
            self.metric_spec_digests[spec.metric_id] = spec.digest


def _adapter_drift_reasons(request: AttributionRunRequest) -> tuple[PlanReason, ...]:
    manifest = request.adapter_manifest
    actual = FrozenAdapterBinding.from_manifest(manifest)
    expected = request.adapter_binding
    drifted = tuple(
        field_name
        for field_name in (
            "adapter_id",
            "adapter_version",
            "capability_manifest_version",
            "capability_manifest_digest",
            "strategy_id",
            "strategy_version",
            "native_topology_digest",
        )
        if getattr(expected, field_name) != getattr(actual, field_name)
    )
    if not drifted:
        reasons: tuple[PlanReason, ...] = ()
    else:
        reasons = (PlanReason(PlanReasonCode.ADAPTER_BINDING_DRIFT, drifted),)
    if request.method_registry_version != METHOD_REGISTRY_VERSION:
        reasons += (
            PlanReason(
                PlanReasonCode.METHOD_REGISTRY_DRIFT,
                (request.method_registry_version, METHOD_REGISTRY_VERSION),
            ),
        )
    return reasons


def _method_reason(
    *,
    field_name: str,
    method_id: str,
    kind: AttributionMethodKind,
    required_check: bool = False,
) -> PlanReason | None:
    registrations = {item.method_id: item for item in registered_attribution_methods()}
    registration = registrations.get(method_id)
    if registration is None:
        code = (
            PlanReasonCode.REQUIRED_METHOD_UNAVAILABLE
            if required_check
            else PlanReasonCode.METHOD_NOT_REGISTERED
        )
        return PlanReason(code, (field_name, method_id))
    if not registration.supports(kind):
        code = (
            PlanReasonCode.REQUIRED_METHOD_UNAVAILABLE
            if required_check
            else PlanReasonCode.METHOD_KIND_MISMATCH
        )
        return PlanReason(code, (field_name, method_id, kind.value))
    return None


def _question_reasons(
    request: AttributionRunRequest,
    question: AttributionQuestion,
    adapter_reasons: tuple[PlanReason, ...],
) -> tuple[PlanReason, ...]:
    manifest = request.adapter_manifest
    reasons: list[PlanReason] = list(adapter_reasons)
    if not question.bound_before_outcomes:
        reasons.append(PlanReason(PlanReasonCode.QUESTION_NOT_PREDECLARED))
    for capability in question.required_capabilities:
        if not manifest.has(capability):
            reasons.append(
                PlanReason(PlanReasonCode.MISSING_REQUIRED_CAPABILITY, (capability.value,))
            )
    for field_name, method_id, kind in (
        ("effect_method", question.effect_method, AttributionMethodKind.EFFECT),
        (
            "classification_rule",
            question.classification_rule,
            AttributionMethodKind.CLASSIFICATION,
        ),
        (
            "multiple_comparison_rule",
            question.multiple_comparison_rule,
            AttributionMethodKind.MULTIPLE_COMPARISON,
        ),
    ):
        reason = _method_reason(field_name=field_name, method_id=method_id, kind=kind)
        if reason is not None:
            reasons.append(reason)
    for field_name, required, method_id, kind in (
        (
            "uncertainty_method",
            question.uncertainty_required,
            question.uncertainty_method,
            AttributionMethodKind.UNCERTAINTY,
        ),
        (
            "stability_method",
            question.stability_required,
            question.stability_method,
            AttributionMethodKind.STABILITY,
        ),
    ):
        if required and method_id == NOT_REQUIRED_CHECK_METHOD:
            reasons.append(
                PlanReason(PlanReasonCode.REQUIRED_METHOD_UNAVAILABLE, (field_name, method_id))
            )
        elif not required and method_id != NOT_REQUIRED_CHECK_METHOD:
            reasons.append(
                PlanReason(PlanReasonCode.UNEXPECTED_CHECK_METHOD, (field_name, method_id))
            )
        else:
            reason = _method_reason(
                field_name=field_name,
                method_id=method_id,
                kind=kind,
                required_check=required,
            )
            if reason is not None:
                reasons.append(reason)
    subject = manifest.component(question.subject_component_id)
    if subject is None:
        reasons.append(
            PlanReason(
                PlanReasonCode.UNKNOWN_SUBJECT_COMPONENT,
                (question.subject_component_id,),
            )
        )
    else:
        intervention = subject.intervention(question.intervention_id)
        if intervention is None:
            reasons.append(
                PlanReason(
                    PlanReasonCode.UNDECLARED_INTERVENTION,
                    (question.subject_component_id, question.intervention_id),
                )
            )
        elif intervention.digest != question.intervention_spec_digest:
            reasons.append(
                PlanReason(
                    PlanReasonCode.INTERVENTION_SPEC_DRIFT,
                    (question.intervention_id,),
                )
            )
    if question.condition_specs:
        condition_method_reason = _method_reason(
            field_name="condition_method",
            method_id=CONDITION_EFFECT_METHOD,
            kind=AttributionMethodKind.CONDITION,
        )
        if condition_method_reason is not None:
            reasons.append(condition_method_reason)
    for interaction in question.interaction_specs:
        interaction_method_reason = _method_reason(
            field_name="interaction_method",
            method_id=INTERACTION_EFFECT_METHOD,
            kind=AttributionMethodKind.INTERACTION,
        )
        if interaction_method_reason is not None:
            reasons.append(interaction_method_reason)
        partner = manifest.component(interaction.partner_component_id)
        if partner is None:
            reasons.append(
                PlanReason(
                    PlanReasonCode.UNKNOWN_PARTNER_COMPONENT,
                    (interaction.partner_component_id,),
                )
            )
        else:
            partner_intervention = partner.intervention(interaction.partner_intervention_id)
            if partner_intervention is None:
                reasons.append(
                    PlanReason(
                        PlanReasonCode.UNDECLARED_PARTNER_INTERVENTION,
                        (
                            interaction.partner_component_id,
                            interaction.partner_intervention_id,
                        ),
                    )
                )
            elif partner_intervention.digest != interaction.partner_intervention_spec_digest:
                reasons.append(
                    PlanReason(
                        PlanReasonCode.PARTNER_INTERVENTION_SPEC_DRIFT,
                        (interaction.partner_intervention_id,),
                    )
                )
        joint = manifest.joint_intervention_by_digest(interaction.joint_intervention_spec_digest)
        if joint is None:
            reasons.append(
                PlanReason(
                    PlanReasonCode.UNDECLARED_JOINT_INTERVENTION,
                    (interaction.joint_intervention_spec_digest,),
                )
            )
            continue
        expected_members = {
            question.intervention_id: question.subject_component_id,
            interaction.partner_intervention_id: interaction.partner_component_id,
        }
        actual_members = dict(zip(joint.intervention_ids, joint.component_ids, strict=True))
        if actual_members != expected_members:
            reasons.append(
                PlanReason(
                    PlanReasonCode.JOINT_MEMBER_CONFLICT,
                    (interaction.interaction_id, joint.joint_spec_id),
                )
            )
        if joint.intervention_ids != interaction.composition_order:
            reasons.append(
                PlanReason(
                    PlanReasonCode.COMPOSITION_ORDER_CONFLICT,
                    (interaction.interaction_id, joint.joint_spec_id),
                )
            )
        if (
            joint.compatibility_semantics != interaction.compatibility_semantics
            or joint.conflict_rule != interaction.conflict_rule
        ):
            reasons.append(
                PlanReason(
                    PlanReasonCode.JOINT_SEMANTICS_CONFLICT,
                    (interaction.interaction_id, joint.joint_spec_id),
                )
            )
    return tuple(sorted(set(reasons), key=lambda item: (item.code.value, item.details)))


def _question_status(reasons: Sequence[PlanReason]) -> QuestionPlanStatus:
    if not reasons:
        return QuestionPlanStatus.READY
    if all(item.code in _UNAVAILABLE_REASON_CODES for item in reasons):
        return QuestionPlanStatus.UNAVAILABLE
    return QuestionPlanStatus.REJECTED


def _metric_conflict_reasons(
    questions: Sequence[AttributionQuestion],
) -> dict[str, tuple[PlanReason, ...]]:
    definitions: dict[str, dict[str, set[str]]] = {}
    for question in questions:
        for spec in question.metric_specs:
            definitions.setdefault(spec.metric_id, {}).setdefault(spec.digest, set()).add(
                question.digest
            )
    conflicts: dict[str, list[PlanReason]] = {}
    for metric_id, versions in sorted(definitions.items()):
        if len(versions) < 2:
            continue
        reason = PlanReason(
            PlanReasonCode.METRIC_DEFINITION_CONFLICT,
            (metric_id, *sorted(versions)),
        )
        for question_digests in versions.values():
            for question_digest in question_digests:
                conflicts.setdefault(question_digest, []).append(reason)
    return {
        key: tuple(sorted(value, key=lambda item: (item.code.value, item.details)))
        for key, value in conflicts.items()
    }


def _draft_key(draft: _ExperimentDraft) -> str:
    return draft.key


def _ensure_draft(drafts: dict[str, _ExperimentDraft], draft: _ExperimentDraft) -> _ExperimentDraft:
    key = _draft_key(draft)
    existing = drafts.get(key)
    if existing is None:
        drafts[key] = draft
        return draft
    return existing


def _finalize_drafts(
    drafts: Mapping[str, _ExperimentDraft],
) -> tuple[dict[str, PlannedExperiment], dict[str, str]]:
    remaining = {key: set(draft.dependency_keys) for key, draft in drafts.items()}
    unknown_dependencies = sorted(
        dependency
        for dependencies in remaining.values()
        for dependency in dependencies
        if dependency not in drafts
    )
    if unknown_dependencies:
        raise AttributionError(
            "missing_plan_dependency",
            f"draft dependencies are missing: {unknown_dependencies}",
        )
    ready = sorted(key for key, dependencies in remaining.items() if not dependencies)
    ordered_keys: list[str] = []
    while ready:
        key = ready.pop(0)
        ordered_keys.append(key)
        for candidate in sorted(remaining):
            dependencies = remaining[candidate]
            if key not in dependencies:
                continue
            dependencies.remove(key)
            if not dependencies and candidate not in ordered_keys and candidate not in ready:
                ready.append(candidate)
                ready.sort()
    if len(ordered_keys) != len(drafts):
        raise AttributionError("cyclic_plan", "draft dependencies contain a cycle")

    experiment_ids: dict[str, str] = {}
    finalized: dict[str, PlannedExperiment] = {}
    for key in ordered_keys:
        draft = drafts[key]
        dependencies = tuple(sorted(experiment_ids[item] for item in draft.dependency_keys))
        finalized[key] = PlannedExperiment(
            kind=draft.kind,
            execution_scope_digest=draft.execution_scope_digest,
            dependency_experiment_ids=dependencies,
            metric_spec_digests=draft.metric_spec_digests,
            component_ids=draft.component_ids,
            intervention_ids=draft.intervention_ids,
            intervention_spec_digests=draft.intervention_spec_digests,
            required_capabilities=tuple(draft.required_capabilities),
            joint_intervention_spec_digest=draft.joint_intervention_spec_digest,
            condition_spec_digest=draft.condition_spec_digest,
            method_id=draft.method_id,
        )
        experiment_ids[key] = finalized[key].experiment_id
    return finalized, experiment_ids


def _compile_plan_parts(
    request: AttributionRunRequest,
) -> tuple[tuple[PlannedExperiment, ...], tuple[PlannedQuestion, ...]]:
    if not isinstance(request, AttributionRunRequest):
        raise AttributionError("invalid_run_request", "request must be AttributionRunRequest")
    adapter_reasons = _adapter_drift_reasons(request)
    reasons_by_question = {
        question.digest: list(_question_reasons(request, question, adapter_reasons))
        for question in request.questions
    }
    candidates = [
        question for question in request.questions if not reasons_by_question[question.digest]
    ]
    for question_digest, reasons in _metric_conflict_reasons(candidates).items():
        reasons_by_question[question_digest].extend(reasons)
    ready_questions = [
        question for question in request.questions if not reasons_by_question[question.digest]
    ]

    drafts: dict[str, _ExperimentDraft] = {}
    binding_drafts: dict[str, list[tuple[QuestionBindingKind, str, str | None]]] = {
        item.digest: [] for item in ready_questions
    }
    if ready_questions:
        baseline = _ensure_draft(
            drafts,
            _ExperimentDraft(
                kind=PlanNodeKind.BASELINE_REPLAY,
                execution_scope_digest=request.execution_scope_digest,
                required_capabilities={AttributionCapability.BASELINE_REPLAY},
            ),
        )
        baseline_key = baseline.key
        for question in ready_questions:
            baseline.add_metrics(question.metric_specs)
            binding_drafts[question.digest].append(
                (QuestionBindingKind.BASELINE, baseline_key, None)
            )
            intervention = request.adapter_manifest.intervention(
                question.subject_component_id, question.intervention_id
            )
            if intervention is None:
                raise AttributionError(
                    "planner_validation_gap", "ready question lost its subject intervention"
                )
            subject = _ensure_draft(
                drafts,
                _ExperimentDraft(
                    kind=PlanNodeKind.SINGLE_INTERVENTION,
                    execution_scope_digest=request.execution_scope_digest,
                    component_ids=(question.subject_component_id,),
                    intervention_ids=(question.intervention_id,),
                    intervention_spec_digests=(intervention.digest,),
                    dependency_keys={baseline_key},
                    required_capabilities={
                        AttributionCapability.BASELINE_REPLAY,
                        AttributionCapability.LEGAL_INTERVENTION,
                    },
                ),
            )
            subject.add_metrics(question.metric_specs)
            subject_key = subject.key
            binding_drafts[question.digest].append(
                (
                    QuestionBindingKind.SUBJECT_INTERVENTION,
                    subject_key,
                    intervention.digest,
                )
            )
            for condition in question.condition_specs:
                condition_draft = _ensure_draft(
                    drafts,
                    _ExperimentDraft(
                        kind=PlanNodeKind.CONDITION_ANALYSIS,
                        execution_scope_digest=request.execution_scope_digest,
                        component_ids=(question.subject_component_id,),
                        intervention_ids=(question.intervention_id,),
                        intervention_spec_digests=(intervention.digest,),
                        condition_spec_digest=condition.digest,
                        method_id=CONDITION_EFFECT_METHOD,
                        dependency_keys={baseline_key, subject_key},
                        required_capabilities={AttributionCapability.CONDITION_MAP},
                    ),
                )
                condition_draft.add_metrics(question.metric_specs)
                binding_drafts[question.digest].append(
                    (
                        QuestionBindingKind.CONDITION_ANALYSIS,
                        condition_draft.key,
                        condition.digest,
                    )
                )
            for interaction in question.interaction_specs:
                partner_intervention = request.adapter_manifest.intervention(
                    interaction.partner_component_id,
                    interaction.partner_intervention_id,
                )
                joint = request.adapter_manifest.joint_intervention_by_digest(
                    interaction.joint_intervention_spec_digest
                )
                if partner_intervention is None or joint is None:
                    raise AttributionError(
                        "planner_validation_gap", "ready interaction lost adapter semantics"
                    )
                partner = _ensure_draft(
                    drafts,
                    _ExperimentDraft(
                        kind=PlanNodeKind.SINGLE_INTERVENTION,
                        execution_scope_digest=request.execution_scope_digest,
                        component_ids=(interaction.partner_component_id,),
                        intervention_ids=(interaction.partner_intervention_id,),
                        intervention_spec_digests=(partner_intervention.digest,),
                        dependency_keys={baseline_key},
                        required_capabilities={
                            AttributionCapability.BASELINE_REPLAY,
                            AttributionCapability.LEGAL_INTERVENTION,
                        },
                    ),
                )
                partner.add_metrics(question.metric_specs)
                partner_key = partner.key
                binding_drafts[question.digest].append(
                    (
                        QuestionBindingKind.INTERACTION_PARTNER,
                        partner_key,
                        interaction.digest,
                    )
                )
                joint_draft = _ensure_draft(
                    drafts,
                    _ExperimentDraft(
                        kind=PlanNodeKind.JOINT_INTERVENTION,
                        execution_scope_digest=request.execution_scope_digest,
                        component_ids=joint.component_ids,
                        intervention_ids=joint.intervention_ids,
                        intervention_spec_digests=joint.intervention_spec_digests,
                        joint_intervention_spec_digest=joint.digest,
                        dependency_keys={baseline_key, subject_key, partner_key},
                        required_capabilities={
                            AttributionCapability.BASELINE_REPLAY,
                            AttributionCapability.LEGAL_INTERVENTION,
                        },
                    ),
                )
                joint_draft.add_metrics(question.metric_specs)
                binding_drafts[question.digest].append(
                    (
                        QuestionBindingKind.JOINT_INTERVENTION,
                        joint_draft.key,
                        interaction.digest,
                    )
                )
            for required, method_id, node_kind, binding_kind in (
                (
                    question.uncertainty_required,
                    question.uncertainty_method,
                    PlanNodeKind.UNCERTAINTY_CHECK,
                    QuestionBindingKind.UNCERTAINTY_CHECK,
                ),
                (
                    question.stability_required,
                    question.stability_method,
                    PlanNodeKind.STABILITY_CHECK,
                    QuestionBindingKind.STABILITY_CHECK,
                ),
            ):
                if not required:
                    continue
                check = _ensure_draft(
                    drafts,
                    _ExperimentDraft(
                        kind=node_kind,
                        execution_scope_digest=request.execution_scope_digest,
                        component_ids=(question.subject_component_id,),
                        intervention_ids=(question.intervention_id,),
                        intervention_spec_digests=(intervention.digest,),
                        method_id=method_id,
                        dependency_keys={baseline_key, subject_key},
                    ),
                )
                check.add_metrics(question.metric_specs)
                method_spec_digest = digest(
                    {
                        "method_registry_version": METHOD_REGISTRY_VERSION,
                        "method_id": method_id,
                    }
                )
                binding_drafts[question.digest].append(
                    (binding_kind, check.key, method_spec_digest)
                )

    finalized, experiment_ids = _finalize_drafts(drafts)
    planned_questions: list[PlannedQuestion] = []
    for question in request.questions:
        reasons = tuple(
            sorted(
                set(reasons_by_question[question.digest]),
                key=lambda item: (item.code.value, item.details),
            )
        )
        status = _question_status(reasons)
        bindings: tuple[QuestionExperimentBinding, ...] = ()
        if status is QuestionPlanStatus.READY:
            bindings = tuple(
                QuestionExperimentBinding(
                    kind=kind,
                    experiment_id=experiment_ids[draft_key],
                    specification_digest=specification_digest,
                )
                for kind, draft_key, specification_digest in binding_drafts[question.digest]
            )
        planned_questions.append(
            PlannedQuestion(
                question_id=question.question_id,
                question_version=question.question_version,
                question_digest=question.digest,
                status=status,
                reasons=reasons,
                experiment_bindings=bindings,
            )
        )
    return (
        _stable_topological_experiments(tuple(finalized.values())),
        tuple(sorted(planned_questions, key=lambda item: (item.question_id, item.question_digest))),
    )


def compile_attribution_plan(request: AttributionRunRequest) -> AttributionPlan:
    """Compile frozen definitions into a deterministic DAG without accessing outcomes."""

    experiments, planned_questions = _compile_plan_parts(request)
    return AttributionPlan(
        run_request=request,
        experiments=experiments,
        planned_questions=planned_questions,
    )

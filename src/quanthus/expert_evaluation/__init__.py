"""Additive expert evaluation protocol v2 with lazy public re-exports."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_CONTRACT_EXPORTS = {
    "ARTIFACT_TYPES_BY_SCHEMA",
    "ArtifactRef",
    "BaselineComparison",
    "DecisionChangeCounts",
    "EvidenceQuality",
    "EvidenceRef",
    "EvidenceRoute",
    "ExpertEvaluationSpec",
    "ExpertLifecycleDecision",
    "ExpertObservation",
    "ExpertScorecard",
    "ForecastSpec",
    "LifecycleActorType",
    "LifecycleDecisionKind",
    "MetricRef",
    "MetricResult",
    "ObservationApplicability",
    "OutcomeSettlement",
    "OutcomeType",
    "PolicyEffect",
    "PolicyEffectKind",
    "PolicyMappingRef",
    "RawValueType",
    "SampleLedger",
    "SettlementState",
    "ShadowDisposition",
    "ShadowStatus",
    "StabilityEstimate",
    "SubjectKind",
    "UncertaintyEstimate",
    "derive_prediction_id",
}
_SERIALIZATION_EXPORTS = {
    "ExpertEvaluationError",
    "artifact_from_bytes",
    "canonical_json_bytes",
    "digest_value",
}
_TARGET_EXPORTS = {
    "TargetDecisionCard",
    "load_target_decision_card",
    "target_decision_semantic_digest",
}
_VALIDATION_EXPORTS = {"validate_artifact_graph"}

__all__ = sorted(
    _CONTRACT_EXPORTS | _SERIALIZATION_EXPORTS | _TARGET_EXPORTS | _VALIDATION_EXPORTS
)


def __getattr__(name: str) -> Any:
    module_name: str
    if name in _CONTRACT_EXPORTS:
        module_name = "quanthus.expert_evaluation.contracts"
    elif name in _SERIALIZATION_EXPORTS:
        module_name = "quanthus.expert_evaluation.serialization"
    elif name in _TARGET_EXPORTS:
        module_name = "quanthus.expert_evaluation.target_cards"
    elif name in _VALIDATION_EXPORTS:
        module_name = "quanthus.expert_evaluation.validation"
    else:
        raise AttributeError(name)
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value

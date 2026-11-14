"""Outcome-blind T-close trend-stability assessment."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import Any

from quanthus.backtest.baseline_research import BaselineFeatureRow
from quanthus.event_library.contracts import EventRecord
from quanthus.expert_evaluation.contracts import (
    EvidenceQuality,
    EvidenceRef,
    ExpertEvaluationSpec,
    ExpertObservation,
    ObservationApplicability,
    RawValueType,
    SubjectKind,
    derive_prediction_id,
)
from quanthus.expert_evaluation.serialization import (
    ExpertEvaluationError,
    digest_value,
)


@dataclass(frozen=True)
class TrendStabilityConfig:
    schema_version: str
    expert_id: str
    expert_version: str
    lifecycle: str
    feature_registry_digest: str
    required_features: tuple[str, ...]
    stable_rule: str
    fragile_rule: str
    fallback_state: str
    missing_reason: str
    semantic_digest: str

    @property
    def semantic_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "expert_id": self.expert_id,
            "expert_version": self.expert_version,
            "feature_registry_digest": self.feature_registry_digest,
            "required_features": list(self.required_features),
            "stable_rule": self.stable_rule,
            "fragile_rule": self.fragile_rule,
            "fallback_state": self.fallback_state,
            "missing_reason": self.missing_reason,
        }

    @property
    def computed_semantic_digest(self) -> str:
        return digest_value(self.semantic_payload)


def load_trend_stability_config(path: Path) -> TrendStabilityConfig:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExpertEvaluationError(
            "invalid_artifact", "trend expert config is unreadable"
        ) from exc
    expected = {
        "schema_version",
        "expert_id",
        "expert_version",
        "lifecycle",
        "feature_registry_digest",
        "required_features",
        "stable_rule",
        "fragile_rule",
        "fallback_state",
        "missing_reason",
        "semantic_digest",
    }
    if not isinstance(raw, dict) or set(raw) != expected:
        raise ExpertEvaluationError("field_mismatch", "trend expert config fields differ")
    required = raw["required_features"]
    if (
        not isinstance(required, list)
        or not required
        or any(not isinstance(item, str) or not item for item in required)
        or len(required) != len(set(required))
    ):
        raise ExpertEvaluationError("field_mismatch", "required trend features are invalid")
    config = TrendStabilityConfig(
        schema_version=str(raw["schema_version"]),
        expert_id=str(raw["expert_id"]),
        expert_version=str(raw["expert_version"]),
        lifecycle=str(raw["lifecycle"]),
        feature_registry_digest=str(raw["feature_registry_digest"]),
        required_features=tuple(required),
        stable_rule=str(raw["stable_rule"]),
        fragile_rule=str(raw["fragile_rule"]),
        fallback_state=str(raw["fallback_state"]),
        missing_reason=str(raw["missing_reason"]),
        semantic_digest=str(raw["semantic_digest"]),
    )
    if (
        config.schema_version != "1.0.0"
        or config.expert_id != "expert.trend-stability"
        or config.expert_version != "1.0.0"
        or config.lifecycle != "active"
        or config.stable_rule != "total_return_index >= sma20 > sma60 and momentum20 > 0"
        or config.fragile_rule != "total_return_index < sma60 and sma20 <= sma60"
        or config.fallback_state != "neutral"
        or config.missing_reason != "missing_required_t_close_trend_feature"
    ):
        raise ExpertEvaluationError("field_mismatch", "trend expert semantics differ")
    if config.semantic_digest != config.computed_semantic_digest:
        raise ExpertEvaluationError("digest_mismatch", "trend expert semantic digest differs")
    return config


def classify_trend_stability(
    feature: BaselineFeatureRow | None,
    config: TrendStabilityConfig,
) -> tuple[str | None, str]:
    """Return the accepted trend-state semantics without constructing an opportunity artifact.

    This helper preserves the exact v1 rule so bounded research adapters can reuse the already
    accepted assessment semantics on a different subject population without pretending that the
    original opportunity-level assessment identity applies unchanged.
    """

    required_values = (
        None
        if feature is None
        else (
            feature.total_return_index,
            feature.sma20,
            feature.sma60,
            feature.momentum20,
        )
    )
    missing = (
        feature is None
        or not feature.feature_ready
        or any(value is None for value in required_values or ())
    )
    if missing:
        return None, config.missing_reason
    assert feature is not None
    assert feature.total_return_index is not None
    assert feature.sma20 is not None
    assert feature.sma60 is not None
    assert feature.momentum20 is not None
    if feature.total_return_index >= feature.sma20 > feature.sma60 and feature.momentum20 > 0:
        return "stable", "eligible"
    if feature.total_return_index < feature.sma60 and feature.sma20 <= feature.sma60:
        return "fragile", "eligible"
    return config.fallback_state, "eligible"


def _feature_payload(row: BaselineFeatureRow) -> dict[str, Any]:
    return {
        "market_date": row.market_date.isoformat(),
        "security_id": row.security_id,
        "total_return_index": (
            None if row.total_return_index is None else str(row.total_return_index)
        ),
        "sma20": None if row.sma20 is None else str(row.sma20),
        "sma60": None if row.sma60 is None else str(row.sma60),
        "momentum20": None if row.momentum20 is None else str(row.momentum20),
        "feature_ready": row.feature_ready,
    }


def evaluate_trend_stability(
    *,
    event: EventRecord,
    feature: BaselineFeatureRow | None,
    config: TrendStabilityConfig,
    spec: ExpertEvaluationSpec,
) -> ExpertObservation:
    """Return one deterministic observation without any future outcome input."""

    if spec.assessment_id != config.expert_id or spec.assessment_version != config.expert_version:
        raise ExpertEvaluationError("producer_binding_mismatch", "trend spec identity differs")
    if spec.raw_value_type is not RawValueType.ORDINAL:
        raise ExpertEvaluationError("value_type_mismatch", "trend expert requires ordinal output")
    if feature is not None and (
        feature.market_date != event.identity.signal_date
        or feature.security_id != event.identity.security_id
    ):
        raise ExpertEvaluationError(
            "observation_feature_mismatch", "feature row does not match the frozen event"
        )
    knowledge_cutoff = event.knowledge_cutoff.astimezone(timezone.utc)
    observation_anchor = event.signal_close.astimezone(timezone.utc)
    if event.snapshot_replay_clock_json is not None:
        import json

        if json.loads(event.snapshot_replay_clock_json)["schema_version"] == (
            "quanthus.r10-controlled-clock.v1"
        ):
            observation_anchor = knowledge_cutoff
    evidence_payload = (
        {
            "event_id": event.event_id,
            "state": "missing",
            "feature_registry_digest": config.feature_registry_digest,
        }
        if feature is None
        else {
            "event_id": event.event_id,
            "feature_registry_digest": config.feature_registry_digest,
            "feature": _feature_payload(feature),
        }
    )
    evidence_digest = digest_value(evidence_payload)
    evidence = EvidenceRef(
        "evidence.p3-feature-panel-row",
        "1.0.0",
        evidence_digest,
        knowledge_cutoff,
    )
    raw_value, applicability_reason = classify_trend_stability(feature, config)
    applicability = (
        ObservationApplicability.ABSTAINED
        if raw_value is None
        else ObservationApplicability.APPLICABLE
    )
    prediction_id = derive_prediction_id(
        assessment_binding_digest=spec.assessment_binding_digest,
        subject_id=event.event_id,
        observation_anchor=observation_anchor,
        knowledge_cutoff=knowledge_cutoff,
        evidence_context_digest=evidence_digest,
    )
    return ExpertObservation(
        producer_id="producer.trend-stability",
        producer_version="1.0.0",
        prediction_id=prediction_id,
        assessment_id=config.expert_id,
        assessment_version=config.expert_version,
        assessment_binding_digest=spec.assessment_binding_digest,
        forecast_spec_digest=spec.forecast_spec_digest,
        subject_id=event.event_id,
        subject_kind=SubjectKind.OPPORTUNITY,
        observation_anchor=observation_anchor,
        knowledge_cutoff=knowledge_cutoff,
        evidence_context_digest=evidence_digest,
        evidence=(evidence,),
        applicability=applicability,
        applicability_reason=applicability_reason,
        raw_value_type=RawValueType.ORDINAL,
        raw_value=raw_value,
        value_unit=None,
        evidence_quality=EvidenceQuality.VERIFIED,
    )

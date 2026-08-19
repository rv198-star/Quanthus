"""Fail-closed graph validation for expert evaluation protocol v2."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from quanthus.expert_evaluation.contracts import (
    ExpertEvaluationSpec,
    ExpertLifecycleDecision,
    ExpertObservation,
    ExpertScorecard,
    ForecastSpec,
    ObservationApplicability,
    OutcomeSettlement,
    PolicyEffect,
    PolicyEffectKind,
    RawValueType,
)
from quanthus.expert_evaluation.serialization import ExpertEvaluationError, require_fields
from quanthus.expert_protocols import (
    ConstraintOutput,
    ConstraintScope,
    EvidenceKind,
    ExpertApplicability,
    ExpertEvidenceRef,
    RequiredAction,
    ScoringOutput,
    VetoAuthority,
    VetoOutput,
)


def _plain_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain_value(item) for item in value]
    return value


def _decimal_or_none(value: object, field_name: str) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ExpertEvaluationError(
            "value_type_mismatch", f"{field_name} must be a v1 decimal string or null"
        )
    try:
        return Decimal(value)
    except Exception as exc:
        raise ExpertEvaluationError(
            "value_type_mismatch", f"{field_name} must be a v1 decimal string"
        ) from exc


def _v1_evidence(value: object) -> tuple[ExpertEvidenceRef, ...]:
    if not isinstance(value, (tuple, list)):
        raise ExpertEvaluationError("value_type_mismatch", "v1 evidence must be an array")
    result: list[ExpertEvidenceRef] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ExpertEvaluationError(
                "value_type_mismatch", "v1 evidence item must be an object"
            )
        require_fields(item, {"kind", "evidence_id", "version", "digest"}, "v1 evidence")
        try:
            kind = EvidenceKind(item["kind"])
        except (TypeError, ValueError) as exc:
            raise ExpertEvaluationError(
                "value_type_mismatch", "v1 evidence kind is unsupported"
            ) from exc
        result.append(ExpertEvidenceRef(kind, item["evidence_id"], item["version"], item["digest"]))
    return tuple(result)


def _v1_base(payload: Mapping[str, Any]) -> tuple[Any, ...]:
    try:
        applicability = ExpertApplicability(payload["applicability"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ExpertEvaluationError(
            "value_type_mismatch", "v1 applicability is unsupported"
        ) from exc
    return (
        payload["expert_id"],
        payload["expert_version"],
        applicability,
        payload["applicability_reason"],
        payload["reason_code"],
        _v1_evidence(payload["evidence"]),
    )


def _validate_v1_output_payload(effect: PolicyEffect) -> None:
    payload = effect.output_payload
    if payload is None:
        return
    try:
        if effect.effect_kind is PolicyEffectKind.SCORING:
            require_fields(
                payload,
                {
                    "expert_id",
                    "expert_version",
                    "applicability",
                    "applicability_reason",
                    "reason_code",
                    "evidence",
                    "signal",
                    "confidence",
                },
                "v1 scoring output",
            )
            output = ScoringOutput(
                *_v1_base(payload),
                _decimal_or_none(payload["signal"], "signal"),
                _decimal_or_none(payload["confidence"], "confidence"),
            )
        elif effect.effect_kind is PolicyEffectKind.CONSTRAINT:
            require_fields(
                payload,
                {
                    "expert_id",
                    "expert_version",
                    "applicability",
                    "applicability_reason",
                    "reason_code",
                    "evidence",
                    "minimum_position",
                    "maximum_position",
                    "stop_rule",
                    "hold_rule",
                    "scope",
                },
                "v1 constraint output",
            )
            output = ConstraintOutput(
                *_v1_base(payload),
                _decimal_or_none(payload["minimum_position"], "minimum_position"),
                _decimal_or_none(payload["maximum_position"], "maximum_position"),
                payload["stop_rule"],
                payload["hold_rule"],
                None if payload["scope"] is None else ConstraintScope(payload["scope"]),
            )
        elif effect.effect_kind is PolicyEffectKind.VETO:
            require_fields(
                payload,
                {
                    "expert_id",
                    "expert_version",
                    "applicability",
                    "applicability_reason",
                    "reason_code",
                    "evidence",
                    "authority",
                    "allow",
                    "required_action",
                },
                "v1 veto output",
            )
            output = VetoOutput(
                *_v1_base(payload),
                VetoAuthority(payload["authority"]),
                payload["allow"],
                (
                    None
                    if payload["required_action"] is None
                    else RequiredAction(payload["required_action"])
                ),
            )
        else:
            raise ExpertEvaluationError(
                "value_type_mismatch", "abstention must not carry a v1 output payload"
            )
    except ExpertEvaluationError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise ExpertEvaluationError(
            "value_type_mismatch", "policy effect is not a complete accepted v1 output"
        ) from exc
    if (output.expert_id, output.expert_version) != (
        effect.assessment_id,
        effect.assessment_version,
    ):
        raise ExpertEvaluationError(
            "invalid_identity", "v1 output expert identity differs from assessment identity"
        )
    if output.to_dict() != _plain_value(payload):
        raise ExpertEvaluationError(
            "value_type_mismatch", "v1 output payload is not canonical"
        )


def _validate_spec_forecast(
    spec: ExpertEvaluationSpec, forecast_spec: ForecastSpec | None
) -> None:
    if spec.raw_value_type is RawValueType.POLICY_ONLY:
        if forecast_spec is not None:
            raise ExpertEvaluationError(
                "value_type_mismatch", "policy-only evaluation cannot carry ForecastSpec"
            )
        return
    if forecast_spec is None or forecast_spec.digest != spec.forecast_spec_digest:
        raise ExpertEvaluationError(
            "invalid_identity", "evaluation spec does not bind the supplied ForecastSpec"
        )
    if (
        spec.raw_value_type is RawValueType.PROBABILITY
        and forecast_spec.outcome_type.value != "binary"
    ):
        raise ExpertEvaluationError(
            "value_type_mismatch", "probability assessment requires a binary outcome"
        )
    if (
        spec.raw_value_type.value != forecast_spec.outcome_type.value
        and spec.raw_value_type is not RawValueType.PROBABILITY
    ):
        raise ExpertEvaluationError(
            "value_type_mismatch", "assessment value type differs from target outcome type"
        )


def _validate_observations(
    spec: ExpertEvaluationSpec,
    forecast_spec: ForecastSpec | None,
    observations: tuple[ExpertObservation, ...],
) -> dict[str, ExpertObservation]:
    if not observations:
        raise ExpertEvaluationError("ledger_not_closed", "evaluation requires observations")
    by_digest: dict[str, ExpertObservation] = {}
    prediction_ids: set[str] = set()
    for observation in observations:
        if observation.digest in by_digest or observation.prediction_id in prediction_ids:
            raise ExpertEvaluationError("invalid_identity", "duplicate observation identity")
        by_digest[observation.digest] = observation
        prediction_ids.add(observation.prediction_id)
        if (
            observation.assessment_id != spec.assessment_id
            or observation.assessment_version != spec.assessment_version
            or observation.assessment_binding_digest != spec.assessment_binding_digest
            or observation.forecast_spec_digest != spec.forecast_spec_digest
            or observation.raw_value_type is not spec.raw_value_type
        ):
            raise ExpertEvaluationError(
                "invalid_identity", "observation does not match evaluation spec"
            )
        if forecast_spec is not None and observation.subject_kind is not forecast_spec.subject_kind:
            raise ExpertEvaluationError(
                "invalid_identity", "observation subject kind does not match ForecastSpec"
            )
    return by_digest


def _validate_policy_effects(
    spec: ExpertEvaluationSpec,
    observations: Mapping[str, ExpertObservation],
    effects: tuple[PolicyEffect, ...],
) -> None:
    declared = {item.key: item for item in spec.policy_mappings}
    uniqueness: set[tuple[str, str, str, str]] = set()
    covered_pairs: set[tuple[str, tuple[str, str, str]]] = set()
    for effect in effects:
        observation = observations.get(effect.observation_digest)
        if observation is None:
            raise ExpertEvaluationError(
                "invalid_identity", "policy effect references no observation"
            )
        if effect.uniqueness_key in uniqueness:
            raise ExpertEvaluationError("invalid_identity", "duplicate observation/mapping effect")
        uniqueness.add(effect.uniqueness_key)
        expected_mapping = declared.get(effect.mapping.key)
        if expected_mapping is None or expected_mapping != effect.mapping:
            raise ExpertEvaluationError(
                "invalid_identity", "policy mapping is not declared by spec"
            )
        covered_pairs.add((effect.observation_digest, effect.mapping.key))
        if (effect.assessment_id, effect.assessment_version) != (
            observation.assessment_id,
            observation.assessment_version,
        ):
            raise ExpertEvaluationError(
                "invalid_identity", "policy effect assessment differs from observation"
            )
        if any(item.visible_at > observation.knowledge_cutoff for item in effect.evidence):
            raise ExpertEvaluationError(
                "future_evidence_at_observation",
                "policy effect includes evidence unavailable at prediction time",
            )
        _validate_v1_output_payload(effect)
    expected_pairs = {
        (observation_digest, mapping_key)
        for observation_digest in observations
        for mapping_key in declared
    }
    if covered_pairs != expected_pairs:
        raise ExpertEvaluationError(
            "invalid_identity", "observation/policy-mapping graph is incomplete"
        )


def _validate_settlements(
    forecast_spec: ForecastSpec | None,
    observations: Mapping[str, ExpertObservation],
    settlements: tuple[OutcomeSettlement, ...],
) -> dict[str, OutcomeSettlement]:
    by_observation: dict[str, OutcomeSettlement] = {}
    for settlement in settlements:
        if settlement.observation_digest in by_observation:
            raise ExpertEvaluationError("invalid_identity", "observation has duplicate settlement")
        observation = observations.get(settlement.observation_digest)
        if observation is None:
            raise ExpertEvaluationError("invalid_identity", "settlement references no observation")
        if observation.applicability is ObservationApplicability.ABSTAINED:
            raise ExpertEvaluationError(
                "settlement_state_conflict", "abstained observation cannot be settled"
            )
        if observation.raw_value_type is RawValueType.POLICY_ONLY:
            raise ExpertEvaluationError(
                "value_type_mismatch", "policy-only observation cannot be settled"
            )
        if forecast_spec is None or settlement.resolver != forecast_spec.resolver:
            raise ExpertEvaluationError(
                "invalid_identity", "settlement resolver differs from ForecastSpec"
            )
        if settlement.realized_value_type is not observation.raw_value_type:
            raise ExpertEvaluationError(
                "value_type_mismatch", "settlement value type differs from observation"
            )
        by_observation[settlement.observation_digest] = settlement
    for observation in observations.values():
        if (
            observation.applicability is ObservationApplicability.APPLICABLE
            and observation.raw_value_type is not RawValueType.POLICY_ONLY
            and observation.digest not in by_observation
        ):
            raise ExpertEvaluationError(
                "ledger_not_closed", "applicable forecast observation lacks settlement state"
            )
    return by_observation


def _expected_ledger_counts(
    observations: Mapping[str, ExpertObservation],
    settlements: Mapping[str, OutcomeSettlement],
) -> dict[str, int]:
    counts = {
        "eligible": len(observations),
        "applicable": 0,
        "abstained": 0,
        "settled": 0,
        "pending": 0,
        "unavailable": 0,
        "not_executable": 0,
        "censored": 0,
    }
    for observation in observations.values():
        if observation.applicability is ObservationApplicability.ABSTAINED:
            counts["abstained"] += 1
            continue
        counts["applicable"] += 1
        if observation.raw_value_type is RawValueType.POLICY_ONLY:
            counts["not_executable"] += 1
            continue
        settlement = settlements[observation.digest]
        counts[settlement.state.value] += 1
    return counts


def _validate_scorecard(
    spec: ExpertEvaluationSpec,
    forecast_spec: ForecastSpec | None,
    observations: Mapping[str, ExpertObservation],
    settlements: Mapping[str, OutcomeSettlement],
    scorecard: ExpertScorecard,
) -> None:
    if (
        scorecard.evaluation_spec_digest != spec.digest
        or scorecard.assessment_id != spec.assessment_id
        or scorecard.assessment_version != spec.assessment_version
        or scorecard.assessment_binding_digest != spec.assessment_binding_digest
        or scorecard.forecast_spec_digest != spec.forecast_spec_digest
        or scorecard.population != spec.population
        or scorecard.partition != spec.partition
    ):
        raise ExpertEvaluationError("invalid_identity", "scorecard does not match evaluation spec")
    if forecast_spec is None:
        if scorecard.target_digest is not None or scorecard.resolver_digest is not None:
            raise ExpertEvaluationError(
                "invalid_identity", "policy-only scorecard cannot bind target or resolver"
            )
    elif (
        scorecard.target_digest != forecast_spec.target.digest
        or scorecard.resolver_digest != forecast_spec.resolver.digest
    ):
        raise ExpertEvaluationError("invalid_identity", "scorecard target/resolver mismatch")
    expected_metrics = {item.key: item for item in spec.metrics}
    actual_metrics = {item.key: item for item in scorecard.metric_definitions}
    if expected_metrics != actual_metrics:
        raise ExpertEvaluationError(
            "metric_not_registered", "scorecard metric definitions differ from evaluation spec"
        )
    if not any(
        item.key == (spec.primary_metric.metric_id, spec.primary_metric.metric_version)
        for item in scorecard.metric_results
    ):
        raise ExpertEvaluationError("metric_not_registered", "primary metric result is missing")
    if not any(item.metric_id == spec.primary_metric.metric_id for item in scorecard.uncertainty):
        raise ExpertEvaluationError(
            "metric_not_registered", "primary metric uncertainty is missing"
        )
    expected_slices = {item.key for item in spec.slices}
    actual_slices = {item.slice_ref.key for item in scorecard.stability}
    if not expected_slices.issubset(actual_slices):
        raise ExpertEvaluationError("metric_not_registered", "declared stability slice is missing")
    expected_baselines = {item.key for item in spec.baselines}
    actual_baselines = {item.baseline_ref.key for item in scorecard.baseline_comparisons}
    if not expected_baselines.issubset(actual_baselines):
        raise ExpertEvaluationError("metric_not_registered", "declared baseline is missing")
    expected_ledger = _expected_ledger_counts(observations, settlements)
    if scorecard.sample_ledger.semantic_dict() != expected_ledger:
        raise ExpertEvaluationError(
            "ledger_not_closed", "scorecard ledger differs from the complete observation graph"
        )
    if scorecard.decision_change_counts.evaluated != expected_ledger["eligible"]:
        raise ExpertEvaluationError(
            "ledger_not_closed", "decision-change denominator differs from eligible observations"
        )


def _validate_lifecycle(
    spec: ExpertEvaluationSpec,
    forecast_spec: ForecastSpec | None,
    effects: tuple[PolicyEffect, ...],
    scorecard: ExpertScorecard,
    decision: ExpertLifecycleDecision,
) -> None:
    if decision.assessment_digest != spec.digest or decision.scorecard_digest != scorecard.digest:
        raise ExpertEvaluationError(
            "invalid_identity", "lifecycle decision assessment/scorecard chain is incomplete"
        )
    if decision.policy_digests != tuple(sorted(effect.digest for effect in effects)):
        raise ExpertEvaluationError(
            "invalid_identity", "lifecycle decision policy-effect chain is incomplete"
        )
    if forecast_spec is None:
        raise ExpertEvaluationError(
            "invalid_identity", "formal lifecycle decision requires a target and resolver"
        )
    if (
        decision.target_digest != forecast_spec.target.digest
        or decision.resolver_digest != forecast_spec.resolver.digest
    ):
        raise ExpertEvaluationError(
            "invalid_identity", "lifecycle target/resolver chain does not match"
        )


def validate_artifact_graph(
    *,
    evaluation_spec: ExpertEvaluationSpec,
    forecast_spec: ForecastSpec | None,
    observations: tuple[ExpertObservation, ...],
    policy_effects: tuple[PolicyEffect, ...],
    settlements: tuple[OutcomeSettlement, ...],
    scorecard: ExpertScorecard,
    lifecycle_decision: ExpertLifecycleDecision | None = None,
) -> None:
    """Validate one complete immutable evaluation graph without mutating any artifact."""

    _validate_spec_forecast(evaluation_spec, forecast_spec)
    observed = _validate_observations(evaluation_spec, forecast_spec, observations)
    _validate_policy_effects(evaluation_spec, observed, policy_effects)
    settled = _validate_settlements(forecast_spec, observed, settlements)
    _validate_scorecard(evaluation_spec, forecast_spec, observed, settled, scorecard)
    if lifecycle_decision is not None:
        _validate_lifecycle(
            evaluation_spec,
            forecast_spec,
            policy_effects,
            scorecard,
            lifecycle_decision,
        )

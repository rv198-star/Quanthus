from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.expert_evaluation import (
    ARTIFACT_TYPES_BY_SCHEMA,
    ArtifactRef,
    BaselineComparison,
    DecisionChangeCounts,
    EvidenceRef,
    EvidenceRoute,
    ExpertEvaluationError,
    ExpertScorecard,
    ForecastSpec,
    LifecycleActorType,
    LifecycleDecisionKind,
    MetricRef,
    MetricResult,
    ObservationApplicability,
    PolicyEffect,
    PolicyEffectKind,
    PolicyMappingRef,
    RawValueType,
    SampleLedger,
    SettlementState,
    ShadowDisposition,
    ShadowStatus,
    StabilityEstimate,
    SubjectKind,
    UncertaintyEstimate,
    artifact_from_bytes,
    canonical_json_bytes,
    derive_prediction_id,
    digest_value,
    validate_artifact_graph,
)
from quanthus.expert_protocols import RequiredAction
from tests.expert_evaluation_fixtures import (
    artifact_ref,
    build_evaluation_graph,
    fixture_digest,
)
from tests.unit.test_aggregator import constraint, veto

GOLDEN_ROOT = (
    Path(__file__).parents[1] / "fixtures" / "golden" / "expert_evaluation_v2"
)


def assert_reason(reason_code: str, action) -> None:
    with pytest.raises(ExpertEvaluationError) as raised:
        action()
    assert raised.value.reason_code == reason_code


def validate_fixture_graph(graph, **overrides) -> None:
    values = {
        "evaluation_spec": graph.spec,
        "forecast_spec": graph.forecast,
        "observations": (graph.observation,),
        "policy_effects": (graph.effect,),
        "settlements": (graph.settlement,),
        "scorecard": graph.scorecard,
        "lifecycle_decision": None,
    }
    values.update(overrides)
    validate_artifact_graph(**values)


def test_all_seven_artifacts_round_trip_through_exact_schema_dispatch() -> None:
    graph = build_evaluation_graph()

    assert len(ARTIFACT_TYPES_BY_SCHEMA) == 7
    for artifact in graph.artifacts:
        payload = artifact.canonical_bytes()
        assert artifact_from_bytes(payload) == artifact
        assert type(artifact).from_bytes(payload) == artifact
        assert artifact.digest.startswith("sha256:")


def test_golden_artifacts_are_exact_canonical_bytes() -> None:
    graph = build_evaluation_graph()
    artifacts = {
        "forecast-spec.json": graph.forecast,
        "evaluation-spec.json": graph.spec,
        "observation.json": graph.observation,
        "policy-effect.json": graph.effect,
        "settlement.json": graph.settlement,
        "scorecard.json": graph.scorecard,
        "lifecycle-decision.json": graph.lifecycle,
    }

    for filename, artifact in artifacts.items():
        golden_bytes = (GOLDEN_ROOT / filename).read_bytes().removesuffix(b"\n")
        assert golden_bytes == artifact.canonical_bytes()


def test_golden_manifest_binds_schema_artifact_and_canonical_byte_digests() -> None:
    graph = build_evaluation_graph()
    artifacts = {
        "forecast-spec.json": graph.forecast,
        "evaluation-spec.json": graph.spec,
        "observation.json": graph.observation,
        "policy-effect.json": graph.effect,
        "settlement.json": graph.settlement,
        "scorecard.json": graph.scorecard,
        "lifecycle-decision.json": graph.lifecycle,
    }
    manifest = json.loads((GOLDEN_ROOT / "manifest.json").read_text(encoding="utf-8"))

    assert set(manifest) == set(artifacts)
    for filename, artifact in artifacts.items():
        assert manifest[filename] == {
            "schema_version": artifact.SCHEMA_VERSION,
            "artifact_digest": artifact.digest,
            "canonical_sha256": hashlib.sha256(artifact.canonical_bytes()).hexdigest(),
        }


def test_set_and_map_reordering_cannot_change_semantic_identity() -> None:
    graph = build_evaluation_graph()
    reversed_forecast = replace(
        graph.forecast,
        exclusions=tuple(reversed(graph.forecast.exclusions)),
    )
    reversed_spec = replace(
        graph.spec,
        evidence_routes=tuple(reversed(graph.spec.evidence_routes)),
        metrics=tuple(reversed(graph.spec.metrics)),
    )
    reversed_scorecard = replace(
        graph.scorecard,
        metric_definitions=tuple(reversed(graph.scorecard.metric_definitions)),
        metric_results=tuple(reversed(graph.scorecard.metric_results)),
    )

    assert reversed_forecast.canonical_bytes() == graph.forecast.canonical_bytes()
    assert reversed_spec.canonical_bytes() == graph.spec.canonical_bytes()
    assert reversed_scorecard.canonical_bytes() == graph.scorecard.canonical_bytes()


def test_policy_remapping_changes_spec_but_not_assessment_binding_or_prediction() -> None:
    graph = build_evaluation_graph()
    added_mapping = PolicyMappingRef(
        "mapping.trend-constraint",
        "1.0.0",
        fixture_digest("mapping.trend-constraint"),
        (PolicyEffectKind.CONSTRAINT,),
    )
    remapped = replace(
        graph.spec,
        policy_mappings=(*graph.spec.policy_mappings, added_mapping),
    )

    assert remapped.digest != graph.spec.digest
    assert remapped.assessment_binding_digest == graph.spec.assessment_binding_digest
    assert derive_prediction_id(
        assessment_binding_digest=remapped.assessment_binding_digest,
        subject_id=graph.observation.subject_id,
        observation_anchor=graph.observation.observation_anchor,
        knowledge_cutoff=graph.observation.knowledge_cutoff,
        evidence_context_digest=graph.observation.evidence_context_digest,
    ) == graph.observation.prediction_id


def test_future_settlement_and_dwd_changes_cannot_rewrite_observation_identity() -> None:
    graph = build_evaluation_graph()
    changed_settlement = replace(
        graph.settlement,
        future_dwd=ArtifactRef(
            "dwd.development-through-20200208",
            "1.0.0",
            fixture_digest("later-dwd"),
        ),
        target_score=Decimal("0.08"),
    )

    assert changed_settlement.digest != graph.settlement.digest
    assert graph.observation == build_evaluation_graph().observation
    assert graph.observation.prediction_id not in changed_settlement.canonical_bytes().decode()


def test_policy_payload_is_deeply_immutable_after_construction() -> None:
    graph = build_evaluation_graph()
    payload = graph.effect.output_payload
    assert payload is not None

    with pytest.raises(TypeError):
        payload["signal"] = "changed"
    with pytest.raises(TypeError):
        payload["evidence"][0]["digest"] = fixture_digest("changed")


def test_decoder_rejects_unknown_missing_duplicate_and_noncanonical_fields() -> None:
    graph = build_evaluation_graph()
    raw = graph.forecast.to_dict()

    assert_reason(
        "field_mismatch",
        lambda: ForecastSpec.from_bytes(canonical_json_bytes({**raw, "unexpected": True})),
    )
    assert_reason(
        "field_mismatch",
        lambda: ForecastSpec.from_bytes(
            canonical_json_bytes({key: value for key, value in raw.items() if key != "target"})
        ),
    )
    assert_reason(
        "field_mismatch",
        lambda: artifact_from_bytes(b'{"schema_version":"a","schema_version":"b"}'),
    )
    pretty = json.dumps(raw, ensure_ascii=True, sort_keys=True).encode("utf-8")
    assert_reason("non_canonical_encoding", lambda: ForecastSpec.from_bytes(pretty))


def test_decoder_rejects_schema_and_semantic_digest_drift() -> None:
    graph = build_evaluation_graph()
    raw = graph.forecast.to_dict()
    unsupported = {**raw, "schema_version": "quanthus.forecast-spec.v3"}
    tampered = {**raw, "digest": "sha256:" + "0" * 64}

    assert_reason(
        "unsupported_schema",
        lambda: ForecastSpec.from_bytes(canonical_json_bytes(unsupported)),
    )
    assert_reason(
        "unsupported_schema",
        lambda: artifact_from_bytes(canonical_json_bytes(unsupported)),
    )
    assert_reason(
        "digest_mismatch", lambda: ForecastSpec.from_bytes(canonical_json_bytes(tampered))
    )


def test_decimal_timestamp_and_digest_inputs_fail_closed() -> None:
    graph = build_evaluation_graph()
    raw = graph.observation.to_dict()
    raw["raw_value"] = "0.70"
    raw["digest"] = digest_value(
        {key: value for key, value in raw.items() if key != "digest"}
    )

    assert_reason(
        "non_canonical_decimal",
        lambda: type(graph.observation).from_bytes(canonical_json_bytes(raw)),
    )
    assert_reason(
        "invalid_timestamp",
        lambda: replace(graph.observation, knowledge_cutoff=datetime(2020, 1, 2, 7, 0)),
    )
    assert_reason(
        "invalid_identity",
        lambda: ArtifactRef("artifact.invalid-digest", "1.0.0", "sha256:not-a-digest"),
    )
    assert_reason(
        "unsupported_canonical_value",
        lambda: replace(
            graph.effect,
            output_payload={**graph.effect.output_payload, "float": 0.5},
        ),
    )


def test_observation_rejects_future_evidence_and_value_shape_mismatch() -> None:
    graph = build_evaluation_graph()
    future_evidence = EvidenceRef(
        "evidence.future",
        "1.0.0",
        fixture_digest("future"),
        graph.observation.knowledge_cutoff + timedelta(seconds=1),
    )
    assert_reason(
        "future_evidence_at_observation",
        lambda: replace(graph.observation, evidence=(future_evidence,)),
    )
    assert_reason(
        "value_type_mismatch",
        lambda: replace(graph.observation, raw_value=Decimal("1.01")),
    )
    assert_reason(
        "value_type_mismatch",
        lambda: replace(
            graph.observation,
            applicability=ObservationApplicability.ABSTAINED,
            applicability_reason="insufficient_history",
        ),
    )


def test_probability_language_is_confined_to_probability_metrics() -> None:
    graph = build_evaluation_graph()
    continuous_metric = MetricRef(
        "metric.rmse",
        "1.0.0",
        fixture_digest("metric.rmse"),
        (RawValueType.CONTINUOUS,),
    )

    assert_reason(
        "metric_not_registered",
        lambda: replace(
            graph.spec,
            primary_metric=continuous_metric,
            metrics=(continuous_metric,),
        ),
    )


def test_settlement_states_do_not_fabricate_neutral_values() -> None:
    graph = build_evaluation_graph()
    for state in (
        SettlementState.PENDING,
        SettlementState.CENSORED,
        SettlementState.UNAVAILABLE,
    ):
        value = replace(
            graph.settlement,
            state=state,
            future_dwd=None,
            realized_value=None,
            target_score=None,
            reason_code=f"{state.value}_fixture",
        )
        assert value.realized_value is None
        assert value.target_score is None
    assert_reason(
        "settlement_state_conflict",
        lambda: replace(
            graph.settlement,
            state=SettlementState.CENSORED,
            reason_code="partition_tail",
        ),
    )


def test_complete_artifact_graph_passes_and_dangling_bindings_fail_closed() -> None:
    graph = build_evaluation_graph()
    validate_artifact_graph(
        evaluation_spec=graph.spec,
        forecast_spec=graph.forecast,
        observations=(graph.observation,),
        policy_effects=(graph.effect,),
        settlements=(graph.settlement,),
        scorecard=graph.scorecard,
        lifecycle_decision=graph.lifecycle,
    )

    dangling = replace(graph.effect, observation_digest=fixture_digest("missing observation"))
    assert_reason(
        "invalid_identity",
        lambda: validate_artifact_graph(
            evaluation_spec=graph.spec,
            forecast_spec=graph.forecast,
            observations=(graph.observation,),
            policy_effects=(dangling,),
            settlements=(graph.settlement,),
            scorecard=graph.scorecard,
        ),
    )
    assert_reason(
        "invalid_identity",
        lambda: validate_artifact_graph(
            evaluation_spec=graph.spec,
            forecast_spec=graph.forecast,
            observations=(graph.observation,),
            policy_effects=(graph.effect, graph.effect),
            settlements=(graph.settlement,),
            scorecard=graph.scorecard,
        ),
    )


def test_graph_rejects_incomplete_or_cross_bound_observation_and_policy_edges() -> None:
    graph = build_evaluation_graph()
    mismatched_observation = replace(
        graph.observation,
        assessment_id="expert.other-assessment",
    )
    wrong_subject = replace(graph.observation, subject_kind=SubjectKind.OPPORTUNITY)
    undeclared_mapping = PolicyMappingRef(
        "mapping.undeclared-score",
        "1.0.0",
        fixture_digest("mapping.undeclared-score"),
        (PolicyEffectKind.SCORING,),
    )
    undeclared_effect = replace(graph.effect, mapping=undeclared_mapping)
    wrong_assessment_effect = replace(
        graph.effect,
        assessment_id="expert.other-assessment",
    )
    future_evidence = EvidenceRef(
        "evidence.future-policy",
        "1.0.0",
        fixture_digest("future policy"),
        graph.observation.knowledge_cutoff + timedelta(seconds=1),
    )
    future_effect = replace(graph.effect, evidence=(future_evidence,))

    cases = (
        {"forecast_spec": None},
        {"observations": ()},
        {"observations": (graph.observation, graph.observation)},
        {"observations": (mismatched_observation,)},
        {"observations": (wrong_subject,)},
        {"policy_effects": ()},
        {"policy_effects": (undeclared_effect,)},
        {"policy_effects": (wrong_assessment_effect,)},
    )
    for overrides in cases:
        assert_reason(
            "ledger_not_closed" if overrides == {"observations": ()} else "invalid_identity",
            lambda overrides=overrides: validate_fixture_graph(graph, **overrides),
        )
    assert_reason(
        "future_evidence_at_observation",
        lambda: validate_fixture_graph(graph, policy_effects=(future_effect,)),
    )


def test_graph_rejects_every_invalid_settlement_edge() -> None:
    graph = build_evaluation_graph()
    dangling = replace(
        graph.settlement,
        observation_digest=fixture_digest("missing observation"),
    )
    wrong_resolver = replace(
        graph.settlement,
        resolver=artifact_ref("resolver.other"),
    )
    wrong_type = replace(
        graph.settlement,
        realized_value_type=RawValueType.CONTINUOUS,
    )
    abstained_observation = replace(
        graph.observation,
        applicability=ObservationApplicability.ABSTAINED,
        applicability_reason="insufficient_history",
        raw_value=None,
    )
    abstained_effect = replace(
        graph.effect,
        observation_digest=abstained_observation.digest,
    )
    abstained_settlement = replace(
        graph.settlement,
        observation_digest=abstained_observation.digest,
    )

    assert_reason(
        "invalid_identity",
        lambda: validate_fixture_graph(graph, settlements=(dangling,)),
    )
    assert_reason(
        "invalid_identity",
        lambda: validate_fixture_graph(
            graph, settlements=(graph.settlement, graph.settlement)
        ),
    )
    assert_reason(
        "invalid_identity",
        lambda: validate_fixture_graph(graph, settlements=(wrong_resolver,)),
    )
    assert_reason(
        "value_type_mismatch",
        lambda: validate_fixture_graph(graph, settlements=(wrong_type,)),
    )
    assert_reason(
        "ledger_not_closed",
        lambda: validate_fixture_graph(graph, settlements=()),
    )
    assert_reason(
        "settlement_state_conflict",
        lambda: validate_fixture_graph(
            graph,
            observations=(abstained_observation,),
            policy_effects=(abstained_effect,),
            settlements=(abstained_settlement,),
        ),
    )


def test_policy_only_assessment_is_observable_but_never_settleable() -> None:
    graph = build_evaluation_graph()
    coverage = next(
        item for item in graph.spec.metrics if item.metric_id == "metric.coverage"
    )
    policy_spec = replace(
        graph.spec,
        raw_value_type=RawValueType.POLICY_ONLY,
        evidence_routes=(EvidenceRoute.COUNTERFACTUAL_REPLAY,),
        primary_metric=coverage,
        metrics=(coverage,),
        forecast_spec_digest=None,
    )
    prediction_id = derive_prediction_id(
        assessment_binding_digest=policy_spec.assessment_binding_digest,
        subject_id=graph.observation.subject_id,
        observation_anchor=graph.observation.observation_anchor,
        knowledge_cutoff=graph.observation.knowledge_cutoff,
        evidence_context_digest=graph.observation.evidence_context_digest,
    )
    policy_observation = replace(
        graph.observation,
        prediction_id=prediction_id,
        assessment_binding_digest=policy_spec.assessment_binding_digest,
        forecast_spec_digest=None,
        raw_value_type=RawValueType.POLICY_ONLY,
        raw_value=None,
    )
    policy_effect = replace(graph.effect, observation_digest=policy_observation.digest)
    policy_scorecard = replace(
        graph.scorecard,
        evaluation_spec_digest=policy_spec.digest,
        assessment_binding_digest=policy_spec.assessment_binding_digest,
        forecast_spec_digest=None,
        target_digest=None,
        resolver_digest=None,
        sample_ledger=SampleLedger(1, 1, 0, 0, 0, 0, 1, 0),
        metric_definitions=(coverage,),
        metric_results=(MetricResult("metric.coverage", "1.0.0", Decimal("1"), 1),),
        uncertainty=(
            UncertaintyEstimate(
                "metric.coverage",
                artifact_ref("uncertainty.time-block-bootstrap"),
                Decimal("1"),
                Decimal("1"),
                Decimal("1"),
                "policy-only observations are not target settlements",
            ),
        ),
        stability=(
            StabilityEstimate(
                graph.scorecard.stability[0].slice_ref,
                "metric.coverage",
                Decimal("1"),
                1,
            ),
        ),
        baseline_comparisons=(
            BaselineComparison(
                graph.scorecard.baseline_comparisons[0].baseline_ref,
                "metric.coverage",
                Decimal("1"),
                Decimal("1"),
            ),
        ),
    )

    validate_artifact_graph(
        evaluation_spec=policy_spec,
        forecast_spec=None,
        observations=(policy_observation,),
        policy_effects=(policy_effect,),
        settlements=(),
        scorecard=policy_scorecard,
    )
    assert_reason(
        "value_type_mismatch",
        lambda: replace(
            graph.settlement,
            observation_digest=policy_observation.digest,
            realized_value_type=RawValueType.POLICY_ONLY,
        ),
    )


def test_all_v1_policy_effect_shapes_are_exactly_validated() -> None:
    graph = build_evaluation_graph()
    constraint_mapping = PolicyMappingRef(
        "mapping.trend-constraint",
        "1.0.0",
        fixture_digest("mapping.trend-constraint"),
        (PolicyEffectKind.CONSTRAINT,),
    )
    veto_mapping = PolicyMappingRef(
        "mapping.trend-veto",
        "1.0.0",
        fixture_digest("mapping.trend-veto"),
        (PolicyEffectKind.VETO,),
    )
    abstention_mapping = PolicyMappingRef(
        "mapping.trend-abstention",
        "1.0.0",
        fixture_digest("mapping.trend-abstention"),
        (PolicyEffectKind.ABSTENTION,),
    )
    spec = replace(
        graph.spec,
        policy_mappings=(
            *graph.spec.policy_mappings,
            constraint_mapping,
            veto_mapping,
            abstention_mapping,
        ),
    )
    effects = (
        graph.effect,
        PolicyEffect(
            graph.effect.producer_id,
            graph.effect.producer_version,
            graph.observation.digest,
            graph.spec.assessment_id,
            graph.spec.assessment_version,
            constraint_mapping,
            PolicyEffectKind.CONSTRAINT,
            constraint(graph.spec.assessment_id, "0", "0.30").to_dict(),
            None,
            graph.effect.evidence,
        ),
        PolicyEffect(
            graph.effect.producer_id,
            graph.effect.producer_version,
            graph.observation.digest,
            graph.spec.assessment_id,
            graph.spec.assessment_version,
            veto_mapping,
            PolicyEffectKind.VETO,
            veto(graph.spec.assessment_id, RequiredAction.NONE).to_dict(),
            None,
            graph.effect.evidence,
        ),
        PolicyEffect(
            graph.effect.producer_id,
            graph.effect.producer_version,
            graph.observation.digest,
            graph.spec.assessment_id,
            graph.spec.assessment_version,
            abstention_mapping,
            PolicyEffectKind.ABSTENTION,
            None,
            "mapping_abstained",
            graph.effect.evidence,
        ),
    )
    scorecard = replace(graph.scorecard, evaluation_spec_digest=spec.digest)

    validate_artifact_graph(
        evaluation_spec=spec,
        forecast_spec=graph.forecast,
        observations=(graph.observation,),
        policy_effects=effects,
        settlements=(graph.settlement,),
        scorecard=scorecard,
    )
    malformed = replace(
        graph.effect,
        output_payload={
            key: value for key, value in graph.effect.output_payload.items() if key != "signal"
        },
    )
    assert_reason(
        "field_mismatch",
        lambda: validate_artifact_graph(
            evaluation_spec=graph.spec,
            forecast_spec=graph.forecast,
            observations=(graph.observation,),
            policy_effects=(malformed,),
            settlements=(graph.settlement,),
            scorecard=graph.scorecard,
        ),
    )


def test_graph_rejects_ledger_and_metric_registry_drift() -> None:
    graph = build_evaluation_graph()
    mismatched_ledger = SampleLedger(2, 2, 0, 2, 0, 0, 0, 0)
    ledger_scorecard = replace(
        graph.scorecard,
        sample_ledger=mismatched_ledger,
        decision_change_counts=DecisionChangeCounts(2, 1, 0, 0, 1),
    )
    assert_reason(
        "ledger_not_closed",
        lambda: validate_artifact_graph(
            evaluation_spec=graph.spec,
            forecast_spec=graph.forecast,
            observations=(graph.observation,),
            policy_effects=(graph.effect,),
            settlements=(graph.settlement,),
            scorecard=ledger_scorecard,
        ),
    )

    extra_metric = MetricRef(
        "metric.unregistered",
        "1.0.0",
        fixture_digest("unregistered"),
        (RawValueType.PROBABILITY,),
    )
    metric_scorecard = replace(
        graph.scorecard,
        metric_definitions=(*graph.scorecard.metric_definitions, extra_metric),
    )
    assert_reason(
        "metric_not_registered",
        lambda: validate_artifact_graph(
            evaluation_spec=graph.spec,
            forecast_spec=graph.forecast,
            observations=(graph.observation,),
            policy_effects=(graph.effect,),
            settlements=(graph.settlement,),
            scorecard=metric_scorecard,
        ),
    )


def test_graph_rejects_missing_scorecard_evidence_and_identity_edges() -> None:
    graph = build_evaluation_graph()
    coverage_result = next(
        item for item in graph.scorecard.metric_results if item.metric_id == "metric.coverage"
    )
    coverage_uncertainty = replace(
        graph.scorecard.uncertainty[0], metric_id="metric.coverage"
    )
    other_slice = replace(
        graph.scorecard.stability[0], slice_ref=artifact_ref("slice.other-regime")
    )
    other_baseline = replace(
        graph.scorecard.baseline_comparisons[0],
        baseline_ref=artifact_ref("baseline.other"),
    )
    zero_changes = DecisionChangeCounts(0, 0, 0, 0, 0)

    cases = (
        (
            "invalid_identity",
            replace(graph.scorecard, evaluation_spec_digest=fixture_digest("other spec")),
        ),
        (
            "invalid_identity",
            replace(graph.scorecard, target_digest=fixture_digest("other target")),
        ),
        (
            "metric_not_registered",
            replace(graph.scorecard, metric_results=(coverage_result,)),
        ),
        (
            "metric_not_registered",
            replace(graph.scorecard, uncertainty=(coverage_uncertainty,)),
        ),
        (
            "metric_not_registered",
            replace(graph.scorecard, stability=(other_slice,)),
        ),
        (
            "metric_not_registered",
            replace(graph.scorecard, baseline_comparisons=(other_baseline,)),
        ),
        (
            "ledger_not_closed",
            replace(graph.scorecard, decision_change_counts=zero_changes),
        ),
    )
    for reason_code, scorecard in cases:
        assert_reason(
            reason_code,
            lambda scorecard=scorecard: validate_fixture_graph(graph, scorecard=scorecard),
        )


def test_graph_rejects_incomplete_lifecycle_evidence_chain() -> None:
    graph = build_evaluation_graph()
    cases = (
        replace(graph.lifecycle, assessment_digest=fixture_digest("other assessment")),
        replace(graph.lifecycle, policy_digests=(fixture_digest("other policy"),)),
        replace(graph.lifecycle, target_digest=fixture_digest("other target")),
        replace(graph.lifecycle, resolver_digest=fixture_digest("other resolver")),
    )
    for lifecycle in cases:
        assert_reason(
            "invalid_identity",
            lambda lifecycle=lifecycle: validate_fixture_graph(
                graph, lifecycle_decision=lifecycle
            ),
        )


def test_scorecard_exact_decoder_rejects_portfolio_and_attribution_fields() -> None:
    graph = build_evaluation_graph()
    raw = graph.scorecard.to_dict()
    for prohibited in ("portfolio_effect", "attribution_release_digest"):
        assert_reason(
            "field_mismatch",
            lambda prohibited=prohibited: ExpertScorecard.from_bytes(
                canonical_json_bytes({**raw, prohibited: fixture_digest(prohibited)})
            ),
        )


def test_lifecycle_decision_requires_human_and_compatible_shadow_disposition() -> None:
    graph = build_evaluation_graph()
    assert_reason(
        "automated_lifecycle_actor",
        lambda: replace(graph.lifecycle, actor_type=LifecycleActorType.AUTOMATED),
    )
    assert_reason(
        "lifecycle_disposition_conflict",
        lambda: replace(
            graph.lifecycle,
            shadow_disposition=replace(
                graph.lifecycle.shadow_disposition, status=ShadowStatus.DISABLED
            ),
        ),
    )
    inconclusive = replace(
        graph.lifecycle,
        decision=LifecycleDecisionKind.INCONCLUSIVE,
        shadow_disposition=ShadowDisposition(
            ShadowStatus.ENABLED,
            "bounded_observation",
            datetime(2020, 2, 11, tzinfo=timezone.utc),
            100,
        ),
    )
    retired = replace(
        graph.lifecycle,
        decision=LifecycleDecisionKind.RETIRE,
        shadow_disposition=ShadowDisposition(
            ShadowStatus.DISABLED,
            "retired_current_version",
            datetime(2020, 2, 11, tzinfo=timezone.utc),
            None,
        ),
    )
    assert inconclusive.shadow_disposition.observation_budget == 100
    assert retired.shadow_disposition.status is ShadowStatus.DISABLED
    bounded_keep = replace(
        graph.lifecycle,
        shadow_disposition=replace(graph.lifecycle.shadow_disposition, observation_budget=100),
    )
    assert bounded_keep.shadow_disposition.status is ShadowStatus.ENABLED


def test_negative_fixture_manifest_names_every_frozen_failure_family() -> None:
    manifest = json.loads((GOLDEN_ROOT / "negative-cases.json").read_text(encoding="utf-8"))
    assert set(manifest) == {
        "unsupported_schema",
        "field_mismatch",
        "digest_mismatch",
        "future_evidence_at_observation",
        "value_type_mismatch",
        "settlement_state_conflict",
        "ledger_not_closed",
        "metric_not_registered",
        "lifecycle_disposition_conflict",
        "automated_lifecycle_actor",
        "v1_compatibility_drift",
    }
    assert all(isinstance(value, str) and value for value in manifest.values())

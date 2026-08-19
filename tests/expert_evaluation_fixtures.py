from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from quanthus.expert_evaluation import (
    ArtifactRef,
    BaselineComparison,
    DecisionChangeCounts,
    EvidenceQuality,
    EvidenceRef,
    EvidenceRoute,
    ExpertEvaluationSpec,
    ExpertLifecycleDecision,
    ExpertObservation,
    ExpertScorecard,
    ForecastSpec,
    LifecycleActorType,
    LifecycleDecisionKind,
    MetricRef,
    MetricResult,
    ObservationApplicability,
    OutcomeSettlement,
    OutcomeType,
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
    derive_prediction_id,
)
from quanthus.expert_protocols import (
    EvidenceKind,
    ExpertApplicability,
    ExpertEvidenceRef,
    ScoringOutput,
)


def fixture_digest(label: str) -> str:
    return f"sha256:{hashlib.sha256(label.encode('utf-8')).hexdigest()}"


def artifact_ref(identity: str) -> ArtifactRef:
    return ArtifactRef(identity, "1.0.0", fixture_digest(identity))


@dataclass(frozen=True)
class EvaluationGraphFixture:
    forecast: ForecastSpec
    spec: ExpertEvaluationSpec
    observation: ExpertObservation
    effect: PolicyEffect
    settlement: OutcomeSettlement
    scorecard: ExpertScorecard
    lifecycle: ExpertLifecycleDecision

    @property
    def artifacts(self) -> tuple[object, ...]:
        return (
            self.forecast,
            self.spec,
            self.observation,
            self.effect,
            self.settlement,
            self.scorecard,
            self.lifecycle,
        )


def build_evaluation_graph() -> EvaluationGraphFixture:
    cutoff = datetime(2020, 1, 2, 7, 0, tzinfo=timezone.utc)
    anchor = datetime(2020, 1, 2, 7, 5, tzinfo=timezone.utc)
    evidence = EvidenceRef(
        "evidence.market-panel",
        "1.0.0",
        fixture_digest("market-panel"),
        cutoff,
    )
    target = artifact_ref("target.trend-stable")
    resolver = artifact_ref("resolver.trend-stable")
    forecast = ForecastSpec(
        producer_id="producer.expert-evaluation",
        producer_version="1.0.0",
        target=target,
        subject_kind=SubjectKind.SECURITY,
        eligibility_rule=artifact_ref("eligibility.trend-stable"),
        observation_anchor_rule="t_close",
        horizon_trading_sessions=20,
        outcome_type=OutcomeType.BINARY,
        resolver=resolver,
        scorer=artifact_ref("scorer.brier"),
        exclusions=("partition_tail", "listing_age"),
        missing_rules=("missing_price_unavailable",),
        censoring_rules=("sealed_boundary_censored",),
    )
    mapping = PolicyMappingRef(
        "mapping.trend-score",
        "1.0.0",
        fixture_digest("mapping.trend-score"),
        (PolicyEffectKind.SCORING,),
    )
    brier = MetricRef(
        "metric.brier",
        "1.0.0",
        fixture_digest("metric.brier"),
        (RawValueType.PROBABILITY,),
    )
    coverage = MetricRef(
        "metric.coverage",
        "1.0.0",
        fixture_digest("metric.coverage"),
        tuple(RawValueType),
    )
    population = artifact_ref("population.development-candidates")
    partition = artifact_ref("partition.development-2012-2021")
    baseline = artifact_ref("baseline.market-base-rate")
    slice_ref = artifact_ref("slice.market-regime")
    spec = ExpertEvaluationSpec(
        producer_id="producer.expert-evaluation",
        producer_version="1.0.0",
        assessment_id="expert.trend-stability",
        assessment_version="1.0.0",
        raw_value_type=RawValueType.PROBABILITY,
        observation_semantics=artifact_ref("semantics.trend-stability"),
        policy_mappings=(mapping,),
        evidence_routes=(EvidenceRoute.FUTURE_TARGET, EvidenceRoute.FORWARD_SHADOW),
        population=population,
        partition=partition,
        primary_metric=brier,
        metrics=(coverage, brier),
        baselines=(baseline,),
        slices=(slice_ref,),
        assessment_stop_conditions=(artifact_ref("stop.minimum-effective-sample"),),
        forecast_spec_digest=forecast.digest,
    )
    prediction_id = derive_prediction_id(
        assessment_binding_digest=spec.assessment_binding_digest,
        subject_id="600000",
        observation_anchor=anchor,
        knowledge_cutoff=cutoff,
        evidence_context_digest=fixture_digest("context-600000-20200102"),
    )
    observation = ExpertObservation(
        producer_id="producer.trend-expert",
        producer_version="1.0.0",
        prediction_id=prediction_id,
        assessment_id=spec.assessment_id,
        assessment_version=spec.assessment_version,
        assessment_binding_digest=spec.assessment_binding_digest,
        forecast_spec_digest=forecast.digest,
        subject_id="600000",
        subject_kind=SubjectKind.SECURITY,
        observation_anchor=anchor,
        knowledge_cutoff=cutoff,
        evidence_context_digest=fixture_digest("context-600000-20200102"),
        evidence=(evidence,),
        applicability=ObservationApplicability.APPLICABLE,
        applicability_reason="eligible",
        raw_value_type=RawValueType.PROBABILITY,
        raw_value=Decimal("0.70"),
        value_unit=None,
        evidence_quality=EvidenceQuality.VERIFIED,
    )
    v1_evidence = ExpertEvidenceRef(
        EvidenceKind.SNAPSHOT,
        "snapshot-2020-01-02",
        "1.0.0",
        fixture_digest("v1 snapshot"),
    )
    v1_output = ScoringOutput(
        expert_id=spec.assessment_id,
        expert_version=spec.assessment_version,
        applicability=ExpertApplicability.APPLICABLE,
        applicability_reason="eligible",
        reason_code="positive_trend",
        evidence=(v1_evidence,),
        signal=Decimal("0.40"),
        confidence=Decimal("0.75"),
    )
    effect = PolicyEffect(
        producer_id="producer.trend-policy",
        producer_version="1.0.0",
        observation_digest=observation.digest,
        assessment_id=spec.assessment_id,
        assessment_version=spec.assessment_version,
        mapping=mapping,
        effect_kind=PolicyEffectKind.SCORING,
        output_payload=v1_output.to_dict(),
        abstention_reason=None,
        evidence=(evidence,),
    )
    settlement = OutcomeSettlement(
        producer_id="producer.target-resolver",
        producer_version="1.0.0",
        observation_digest=observation.digest,
        state=SettlementState.SETTLED,
        observed_start=date(2020, 1, 3),
        observed_end=date(2020, 2, 7),
        future_dwd=artifact_ref("dwd.development-through-20200207"),
        resolver=resolver,
        realized_value_type=RawValueType.PROBABILITY,
        realized_value=Decimal("1"),
        target_score=Decimal("0.09"),
        reason_code=None,
    )
    scorecard = ExpertScorecard(
        producer_id="producer.expert-scorecard",
        producer_version="1.0.0",
        evaluation_spec_digest=spec.digest,
        assessment_id=spec.assessment_id,
        assessment_version=spec.assessment_version,
        assessment_binding_digest=spec.assessment_binding_digest,
        forecast_spec_digest=forecast.digest,
        target_digest=target.digest,
        resolver_digest=resolver.digest,
        population=population,
        partition=partition,
        sample_ledger=SampleLedger(
            eligible=1,
            applicable=1,
            abstained=0,
            settled=1,
            pending=0,
            unavailable=0,
            not_executable=0,
            censored=0,
        ),
        metric_definitions=(coverage, brier),
        metric_results=(
            MetricResult("metric.brier", "1.0.0", Decimal("0.09"), 1),
            MetricResult("metric.coverage", "1.0.0", Decimal("1"), 1),
        ),
        uncertainty=(
            UncertaintyEstimate(
                "metric.brier",
                artifact_ref("uncertainty.time-block-bootstrap"),
                Decimal("0.05"),
                Decimal("0.20"),
                Decimal("1"),
                "single synthetic block; no independence claim",
            ),
        ),
        stability=(StabilityEstimate(slice_ref, "metric.brier", Decimal("0.09"), 1),),
        baseline_comparisons=(
            BaselineComparison(baseline, "metric.brier", Decimal("0.09"), Decimal("0.25")),
        ),
        decision_change_counts=DecisionChangeCounts(1, 1, 0, 0, 1),
        limitations=("synthetic contract fixture",),
    )
    lifecycle = ExpertLifecycleDecision(
        producer_id="producer.expert-lifecycle",
        producer_version="1.0.0",
        decision=LifecycleDecisionKind.KEEP,
        actor_type=LifecycleActorType.HUMAN,
        actor_id="reviewer-001",
        decided_at=datetime(2020, 2, 10, 8, 0, tzinfo=timezone.utc),
        rationale="Synthetic evidence chain is complete for protocol verification.",
        limitations=("No market conclusion is authorized.",),
        shadow_disposition=ShadowDisposition(
            status=ShadowStatus.ENABLED,
            reason_code="keep_for_forward_observation",
            effective_boundary=datetime(2020, 2, 11, 0, 0, tzinfo=timezone.utc),
            observation_budget=None,
        ),
        assessment_digest=spec.digest,
        policy_digests=(effect.digest,),
        target_digest=target.digest,
        resolver_digest=resolver.digest,
        dwd_digest=fixture_digest("effective-dwd"),
        split_digest=partition.digest,
        strategy_digest=fixture_digest("formal-strategy"),
        scorecard_digest=scorecard.digest,
        attribution_digest=fixture_digest("attribution-release"),
        package_digest=fixture_digest("evidence-package"),
    )
    return EvaluationGraphFixture(
        forecast=forecast,
        spec=spec,
        observation=observation,
        effect=effect,
        settlement=settlement,
        scorecard=scorecard,
        lifecycle=lifecycle,
    )

"""Synthetic adapter proving the attribution kernel without defining a strategy shape."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.strategy_attribution import (
    ATTRIBUTION_SCHEMA_VERSION,
    NOT_REQUIRED_CHECK_METHOD,
    PAIRED_REPLAY_EFFECT_METHOD,
    SYNTHETIC_ARTIFACT_PRODUCER_ID,
    SYNTHETIC_ARTIFACT_PRODUCER_VERSION,
    AdapterCapabilityManifest,
    AnalysisCheckEvidence,
    AnalysisRole,
    AttributionCapability,
    AttributionError,
    AttributionQuestion,
    AttributionWorkspace,
    ComponentDescriptor,
    ConditionComparison,
    ConditionSpec,
    ExperimentManifest,
    ExperimentRole,
    InMemoryArtifactResolver,
    InteractionComparison,
    InteractionSpec,
    InterventionSpec,
    JointInterventionSpec,
    MetricDisplay,
    MetricPreference,
    MetricSpec,
    PrecomputedAttributionAdapter,
    SampleRole,
    condition_analysis_result_envelope,
    digest,
    evaluate_attribution,
    experiment_evidence_envelope,
    native_trace_envelope,
)
from quanthus.strategy_attribution_pipeline import (
    ExecutorBinding,
    PlannedExecutionWork,
    RawConditionSlice,
    RawExecutionResult,
    register_synthetic_executor_implementation,
)
from quanthus.strategy_attribution_planning import (
    AttributionRunRequest,
    PlanNodeKind,
)

_PERIOD_START = date(2025, 1, 1)
_PERIOD_END = date(2025, 6, 30)
_CONTEXT_BINDINGS = {
    "data_snapshot": "synthetic-pit-six-month-v1",
    "eligible_universe": "synthetic-a-share-v1",
    "capital": "CNY-100000000",
    "matcher": "synthetic-close-next-open-v1",
    "costs": "synthetic-cost-v1",
    "settlement": "synthetic-a-share-settlement-v1",
    "policy": "synthetic-policy-v1:same-exit-version",
    "execution_assumptions": "synthetic-close-to-next-open-v1",
    "seeds": "synthetic-fixed-seeds-v1",
}
_METRIC_CALCULATOR_ID = "quanthus.synthetic-attribution-metric-calculator"
_METRIC_CALCULATOR_VERSION = "1.0.0"


def _percent_metric(
    metric_id: str,
    label: str,
    preference: MetricPreference,
    materiality: str,
) -> MetricSpec:
    return MetricSpec(
        metric_id=metric_id,
        label=label,
        unit="ratio",
        definition_version="1.0.0",
        definition_digest=digest(f"synthetic-metric-definition:{metric_id}:v1"),
        preference=preference,
        materiality=Decimal(materiality),
        display=MetricDisplay.PERCENT,
        display_decimals=1,
    )


_COMMON_METRICS = (
    _percent_metric("total_return", "Total return", MetricPreference.HIGHER, "0.01"),
    _percent_metric("max_drawdown", "Maximum drawdown", MetricPreference.LOWER, "0.005"),
    _percent_metric("win_rate", "Win rate", MetricPreference.HIGHER, "0.01"),
    _percent_metric(
        "capital_utilization",
        "Capital utilization",
        MetricPreference.UNORIENTED,
        "0",
    ),
)

_BASELINE_VALUES = {
    "total_return": Decimal("0.99"),
    "max_drawdown": Decimal("0.05"),
    "win_rate": Decimal("0.63"),
    "capital_utilization": Decimal("0.42"),
    "loss_exposure": Decimal("2100000"),
    "opportunity_cost": Decimal("1400000"),
}

_INTERVENTION_VALUES = {
    "expert.trend": {
        "total_return": Decimal("0.72"),
        "max_drawdown": Decimal("0.09"),
        "win_rate": Decimal("0.54"),
        "capital_utilization": Decimal("0.38"),
    },
    "feature.breakout": {
        "total_return": Decimal("0.91"),
        "max_drawdown": Decimal("0.058"),
        "win_rate": Decimal("0.59"),
        "capital_utilization": Decimal("0.41"),
    },
    "router.market_state": {
        "total_return": Decimal("0.93"),
        "max_drawdown": Decimal("0.04"),
        "win_rate": Decimal("0.61"),
        "capital_utilization": Decimal("0.39"),
    },
    "risk.extension": {
        "total_return": Decimal("1.04"),
        "max_drawdown": Decimal("0.11"),
        "loss_exposure": Decimal("8900000"),
        "opportunity_cost": Decimal("0"),
    },
    "feature.noise_filter": {
        "total_return": Decimal("1.06"),
        "max_drawdown": Decimal("0.045"),
        "win_rate": Decimal("0.66"),
        "capital_utilization": Decimal("0.44"),
    },
}

_JOINT_VALUES = {
    "total_return": Decimal("0.68"),
    "max_drawdown": Decimal("0.095"),
    "win_rate": Decimal("0.51"),
    "capital_utilization": Decimal("0.35"),
}

_CONDITION_VALUES = {
    "state.trending": {
        "sample_count": 84,
        "baseline": {
            "total_return": Decimal("0.62"),
            "max_drawdown": Decimal("0.032"),
            "win_rate": Decimal("0.68"),
            "capital_utilization": Decimal("0.51"),
        },
        "intervention": {
            "total_return": Decimal("0.41"),
            "max_drawdown": Decimal("0.06"),
            "win_rate": Decimal("0.57"),
            "capital_utilization": Decimal("0.43"),
        },
    },
    "state.range_bound": {
        "sample_count": 52,
        "baseline": {
            "total_return": Decimal("0.17"),
            "max_drawdown": Decimal("0.03"),
            "win_rate": Decimal("0.58"),
            "capital_utilization": Decimal("0.28"),
        },
        "intervention": {
            "total_return": Decimal("0.15"),
            "max_drawdown": Decimal("0.035"),
            "win_rate": Decimal("0.56"),
            "capital_utilization": Decimal("0.25"),
        },
    },
}


def _intervention(
    intervention_id: str, operation: str, fallback_semantics: str
) -> tuple[InterventionSpec, ...]:
    return (
        InterventionSpec(
            intervention_id=intervention_id,
            intervention_version="1.0.0",
            operation=operation,
            fallback_semantics=fallback_semantics,
        ),
    )


class SyntheticStrategyAttributionAdapter(PrecomputedAttributionAdapter):
    """A bounded fixture; its component vocabulary is deliberately not a kernel enum."""

    def __init__(
        self,
        *,
        context_bindings: Mapping[str, str] | None = None,
        period_start: date = _PERIOD_START,
        period_end: date = _PERIOD_END,
    ) -> None:
        self._context_bindings = dict(context_bindings or _CONTEXT_BINDINGS)
        self._context_digest = digest(self._context_bindings)
        self._period_start = period_start
        self._period_end = period_end
        self._resolver = InMemoryArtifactResolver()
        router_intervention = _intervention(
            "neutralize_market_state_route",
            "neutralize_native_route",
            "Use the frozen native neutral route; do not delete state labels.",
        )[0]
        trend_intervention = _intervention(
            "abstain_trend_expert",
            "abstain",
            "The frozen aggregator handles one native expert abstention.",
        )[0]
        self._manifest = AdapterCapabilityManifest(
            adapter_id="adapter.synthetic-multi-module",
            adapter_version="1.0.0",
            capability_manifest_version="1.0.0",
            strategy_id="strategy.synthetic-attribution-demo",
            strategy_version="1.0.0",
            evidence_source_kind="synthetic_fixture_not_strategy_evidence",
            native_topology_digest=digest("synthetic-native-topology-v1"),
            capabilities=(
                AttributionCapability.BASELINE_REPLAY,
                AttributionCapability.COMPONENT_MAP,
                AttributionCapability.LEGAL_INTERVENTION,
                AttributionCapability.DECISION_TRACE,
                AttributionCapability.OPPORTUNITY_TRACE,
                AttributionCapability.CONDITION_MAP,
            ),
            components=(
                ComponentDescriptor(
                    component_id="strategy.root",
                    component_version="1.0.0",
                    label="Synthetic strategy",
                    kind="strategy_root",
                    role="native_root",
                    description=(
                        "Adapter root for a fictional strategy used only to prove plumbing."
                    ),
                ),
                ComponentDescriptor(
                    component_id="feature.breakout",
                    component_version="1.0.0",
                    label="Breakout feature group",
                    kind="feature_group",
                    role="native_input",
                    description="A fictional breakout evidence group.",
                    depends_on=("strategy.root",),
                    legal_interventions=_intervention(
                        "mask_breakout_feature_group",
                        "mask_without_retrain",
                        "Regenerate native decisions with the declared missing-group mask.",
                    ),
                ),
                ComponentDescriptor(
                    component_id="feature.noise_filter",
                    component_version="1.0.0",
                    label="Noise filter",
                    kind="feature_group",
                    role="native_input",
                    description="A fictional filter included to demonstrate a harmful component.",
                    depends_on=("strategy.root",),
                    legal_interventions=_intervention(
                        "mask_noise_filter",
                        "mask_without_retrain",
                        "Regenerate native decisions with the declared missing-group mask.",
                    ),
                ),
                ComponentDescriptor(
                    component_id="router.market_state",
                    component_version="1.0.0",
                    label="Market-state router",
                    kind="native_router",
                    role="decision_route",
                    description="A fictional state router with a declared neutral route.",
                    depends_on=("feature.breakout",),
                    legal_interventions=(router_intervention,),
                ),
                ComponentDescriptor(
                    component_id="expert.trend",
                    component_version="1.0.0",
                    label="Trend expert",
                    kind="native_expert",
                    role="decision_input",
                    description="A fictional expert with declared abstention semantics.",
                    depends_on=("feature.breakout", "router.market_state"),
                    legal_interventions=(trend_intervention,),
                ),
                ComponentDescriptor(
                    component_id="risk.extension",
                    component_version="1.0.0",
                    label="Extension-risk gate",
                    kind="policy_risk_gate",
                    role="veto",
                    description=(
                        "A fictional gate used to show loss avoidance and opportunity cost."
                    ),
                    depends_on=("expert.trend",),
                    legal_interventions=_intervention(
                        "bypass_extension_risk_gate",
                        "bypass_policy_gate",
                        "Only this strategy policy gate is bypassed; invariants remain active.",
                    ),
                ),
                ComponentDescriptor(
                    component_id="allocator.capital",
                    component_version="1.0.0",
                    label="Capital allocator",
                    kind="native_allocator",
                    role="capital_assignment",
                    description="Visible in the topology but intentionally not intervenable.",
                    depends_on=("risk.extension",),
                ),
            ),
            legal_joint_interventions=(
                JointInterventionSpec(
                    joint_spec_id="joint.trend-expert-market-state-router",
                    joint_spec_version="1.0.0",
                    intervention_ids=(
                        "abstain_trend_expert",
                        "neutralize_market_state_route",
                    ),
                    component_ids=("expert.trend", "router.market_state"),
                    intervention_spec_digests=(
                        trend_intervention.digest,
                        router_intervention.digest,
                    ),
                    compatibility_semantics=("Both native interventions are compatible."),
                    conflict_rule=("Fail if either intervention changes the other fallback."),
                ),
            ),
        )
        self._questions = self._build_questions()

    def capability_manifest(self) -> AdapterCapabilityManifest:
        return self._manifest

    def artifact_resolver(self) -> InMemoryArtifactResolver:
        return self._resolver

    def questions(self) -> tuple[AttributionQuestion, ...]:
        return self._questions

    def baseline(self, question: AttributionQuestion) -> ExperimentManifest:
        return self._experiment(
            question=question,
            role=ExperimentRole.BASELINE,
            component_ids=(),
            intervention_ids=(),
            metrics=self._metrics_for(question, _BASELINE_VALUES),
            suffix="baseline",
        )

    def intervention(self, question: AttributionQuestion) -> ExperimentManifest:
        return self._experiment(
            question=question,
            role=ExperimentRole.INTERVENTION,
            component_ids=(question.subject_component_id,),
            intervention_ids=(question.intervention_id,),
            metrics=self._metrics_for(
                question, _INTERVENTION_VALUES[question.subject_component_id]
            ),
            suffix="neutralized",
        )

    def condition_comparisons(
        self, question: AttributionQuestion
    ) -> tuple[ConditionComparison, ...]:
        if question.subject_component_id != "expert.trend":
            return ()
        baseline = self.baseline(question)
        intervention = self.intervention(question)
        specs = {item.condition_id: item for item in question.condition_specs}
        comparisons = (
            ConditionComparison(
                condition_id="state.trending",
                condition_spec_digest=specs["state.trending"].digest,
                label="Trending state",
                sample_count=84,
                question_digest=question.digest,
                adapter_capability_digest=self._manifest.digest,
                context_digest=baseline.context_digest,
                period_start=baseline.period_start,
                period_end=baseline.period_end,
                baseline_experiment_digest=baseline.digest,
                intervention_experiment_digest=intervention.digest,
                result_digest=digest("unbound-synthetic-condition-artifact"),
                plan_receipt_digest=None,
                execution_work_digest=None,
                raw_execution_digest=None,
                reconciled=True,
                baseline_metrics={
                    "total_return": Decimal("0.62"),
                    "max_drawdown": Decimal("0.032"),
                    "win_rate": Decimal("0.68"),
                    "capital_utilization": Decimal("0.51"),
                },
                intervention_metrics={
                    "total_return": Decimal("0.41"),
                    "max_drawdown": Decimal("0.06"),
                    "win_rate": Decimal("0.57"),
                    "capital_utilization": Decimal("0.43"),
                },
            ),
            ConditionComparison(
                condition_id="state.range_bound",
                condition_spec_digest=specs["state.range_bound"].digest,
                label="Range-bound state",
                sample_count=52,
                question_digest=question.digest,
                adapter_capability_digest=self._manifest.digest,
                context_digest=baseline.context_digest,
                period_start=baseline.period_start,
                period_end=baseline.period_end,
                baseline_experiment_digest=baseline.digest,
                intervention_experiment_digest=intervention.digest,
                result_digest=digest("unbound-synthetic-condition-artifact"),
                plan_receipt_digest=None,
                execution_work_digest=None,
                raw_execution_digest=None,
                reconciled=True,
                baseline_metrics={
                    "total_return": Decimal("0.17"),
                    "max_drawdown": Decimal("0.03"),
                    "win_rate": Decimal("0.58"),
                    "capital_utilization": Decimal("0.28"),
                },
                intervention_metrics={
                    "total_return": Decimal("0.15"),
                    "max_drawdown": Decimal("0.035"),
                    "win_rate": Decimal("0.56"),
                    "capital_utilization": Decimal("0.25"),
                },
            ),
        )
        return tuple(
            replace(
                comparison,
                result_digest=self._resolver.register(
                    condition_analysis_result_envelope(
                        comparison,
                        producer_id=SYNTHETIC_ARTIFACT_PRODUCER_ID,
                        producer_version=SYNTHETIC_ARTIFACT_PRODUCER_VERSION,
                        source_kind=self._manifest.evidence_source_kind,
                    )
                ),
            )
            for comparison in comparisons
        )

    def interaction_runs(self, question: AttributionQuestion) -> tuple[InteractionComparison, ...]:
        if question.subject_component_id != "expert.trend":
            return ()
        spec = question.interaction_specs[0]
        baseline = self.baseline(question)
        subject = self.intervention(question)
        partner_id = "router.market_state"
        partner = self._experiment(
            question=question,
            role=ExperimentRole.INTERVENTION,
            component_ids=(partner_id,),
            intervention_ids=("neutralize_market_state_route",),
            metrics={
                "total_return": Decimal("0.93"),
                "max_drawdown": Decimal("0.04"),
                "win_rate": Decimal("0.61"),
                "capital_utilization": Decimal("0.39"),
            },
            suffix="partner-router",
        )
        joint = self._experiment(
            question=question,
            role=ExperimentRole.JOINT_INTERVENTION,
            component_ids=(question.subject_component_id, partner_id),
            intervention_ids=(question.intervention_id, "neutralize_market_state_route"),
            metrics={
                "total_return": Decimal("0.68"),
                "max_drawdown": Decimal("0.095"),
                "win_rate": Decimal("0.51"),
                "capital_utilization": Decimal("0.35"),
            },
            suffix="joint-trend-router",
            interaction_spec_digest=spec.digest,
        )
        return (
            InteractionComparison(
                interaction_id=spec.interaction_id,
                interaction_spec_digest=spec.digest,
                partner_component_id=partner_id,
                baseline_experiment_digest=baseline.digest,
                subject_experiment_digest=subject.digest,
                partner_intervention=partner,
                joint_intervention=joint,
            ),
        )

    def analysis_checks(self, question: AttributionQuestion) -> tuple[AnalysisCheckEvidence, ...]:
        del question
        return ()

    def _build_questions(self) -> tuple[AttributionQuestion, ...]:
        def intervention_digest(component_id: str, intervention_id: str) -> str:
            intervention = self._manifest.intervention(component_id, intervention_id)
            if intervention is None:
                raise RuntimeError(f"synthetic intervention is undeclared: {intervention_id}")
            return intervention.digest

        common: dict[str, Any] = {
            "question_version": "1.0.0",
            "hypothesis_family_id": "synthetic.primary-components.v1",
            "estimand_kind": "declared_pipeline_component_reliance",
            "effect_method": PAIRED_REPLAY_EFFECT_METHOD,
            "intervention_semantics": (
                "Use only the exact strategy-native InterventionSpec bound by its digest."
            ),
            "target_population": "synthetic-pit-a-share-six-month-v1",
            "sample_role": SampleRole.DEVELOPMENT,
            "analysis_role": AnalysisRole.CONFIRMATORY,
            "multiple_comparison_rule": "no_cross_module_ranking:synthetic_fixture_v1",
            "classification_rule": "primary_metric_dominance_with_materiality_v1",
            "decision_rule": "synthetic_fixture_no_strategy_action_v1",
            "decision_actions": {
                "valid:beneficial": "no_strategy_action_synthetic_fixture",
                "valid:harmful": "no_strategy_action_synthetic_fixture",
                "valid:negligible": "no_strategy_action_synthetic_fixture",
                "valid:tradeoff": "no_strategy_action_synthetic_fixture",
                "valid:inconclusive": "no_strategy_action_synthetic_fixture",
                "invalid:inconclusive": "no_strategy_action_synthetic_fixture",
                "unavailable:inconclusive": "no_strategy_action_synthetic_fixture",
            },
            "uncertainty_required": False,
            "uncertainty_method": NOT_REQUIRED_CHECK_METHOD,
            "stability_required": False,
            "stability_method": NOT_REQUIRED_CHECK_METHOD,
            "bound_before_outcomes": True,
            "primary_metric_ids": ("total_return", "max_drawdown"),
            "metric_specs": _COMMON_METRICS,
            "required_capabilities": (AttributionCapability.DECISION_TRACE,),
            "known_assumptions": (
                "All values are fabricated and prove contracts only.",
                "The paired replay uses one frozen synthetic context.",
            ),
            "exclusions": ("No live or historical strategy inference is permitted.",),
            "stop_conditions": (
                "Stop publication when any bound identity or required evidence differs.",
            ),
        }
        trend_intervention = "abstain_trend_expert"
        router_intervention = "neutralize_market_state_route"
        joint_intervention = self._manifest.joint_intervention(
            "joint.trend-expert-market-state-router"
        )
        if joint_intervention is None:
            raise RuntimeError("synthetic joint intervention is undeclared")
        return (
            AttributionQuestion(
                question_id="question.expert-trend",
                subject_component_id="expert.trend",
                intervention_id=trend_intervention,
                intervention_spec_digest=intervention_digest("expert.trend", trend_intervention),
                condition_specs=(
                    ConditionSpec(
                        condition_id="state.trending",
                        label="Trending state",
                        definition_version="1.0.0",
                        definition_digest=digest("synthetic-trending-state-definition-v1"),
                    ),
                    ConditionSpec(
                        condition_id="state.range_bound",
                        label="Range-bound state",
                        definition_version="1.0.0",
                        definition_digest=digest("synthetic-range-bound-state-definition-v1"),
                    ),
                ),
                interaction_specs=(
                    InteractionSpec(
                        interaction_id="interaction.trend-with-market-state-router",
                        partner_component_id="router.market_state",
                        partner_intervention_id=router_intervention,
                        partner_intervention_spec_digest=intervention_digest(
                            "router.market_state", router_intervention
                        ),
                        joint_intervention_spec_digest=joint_intervention.digest,
                        composition_order=(trend_intervention, router_intervention),
                        compatibility_semantics="Both native interventions are compatible.",
                        conflict_rule="Fail if either intervention changes the other fallback.",
                    ),
                ),
                **common,
            ),
            AttributionQuestion(
                question_id="question.feature-breakout",
                subject_component_id="feature.breakout",
                intervention_id="mask_breakout_feature_group",
                intervention_spec_digest=intervention_digest(
                    "feature.breakout", "mask_breakout_feature_group"
                ),
                **common,
            ),
            AttributionQuestion(
                question_id="question.market-state-router",
                subject_component_id="router.market_state",
                intervention_id=router_intervention,
                intervention_spec_digest=intervention_digest(
                    "router.market_state", router_intervention
                ),
                **common,
            ),
            AttributionQuestion(
                question_id="question.extension-risk",
                subject_component_id="risk.extension",
                intervention_id="bypass_extension_risk_gate",
                intervention_spec_digest=intervention_digest(
                    "risk.extension", "bypass_extension_risk_gate"
                ),
                **{
                    **common,
                    "required_capabilities": (
                        AttributionCapability.DECISION_TRACE,
                        AttributionCapability.OPPORTUNITY_TRACE,
                    ),
                    "metric_specs": (
                        _COMMON_METRICS[0],
                        _COMMON_METRICS[1],
                        MetricSpec(
                            metric_id="loss_exposure",
                            label="Loss exposure",
                            unit="CNY",
                            definition_version="1.0.0",
                            definition_digest=digest(
                                "synthetic-metric-definition:loss_exposure:v1"
                            ),
                            preference=MetricPreference.LOWER,
                            materiality=Decimal("100000"),
                            display=MetricDisplay.CURRENCY,
                            display_decimals=0,
                        ),
                        MetricSpec(
                            metric_id="opportunity_cost",
                            label="Opportunity cost",
                            unit="CNY",
                            definition_version="1.0.0",
                            definition_digest=digest(
                                "synthetic-metric-definition:opportunity_cost:v1"
                            ),
                            preference=MetricPreference.LOWER,
                            materiality=Decimal("100000"),
                            display=MetricDisplay.CURRENCY,
                            display_decimals=0,
                        ),
                    ),
                },
            ),
            AttributionQuestion(
                question_id="question.noise-filter",
                subject_component_id="feature.noise_filter",
                intervention_id="mask_noise_filter",
                intervention_spec_digest=intervention_digest(
                    "feature.noise_filter", "mask_noise_filter"
                ),
                **common,
            ),
        )

    def _metrics_for(
        self, question: AttributionQuestion, values: dict[str, Decimal]
    ) -> dict[str, Decimal]:
        return {metric.metric_id: values[metric.metric_id] for metric in question.metric_specs}

    def _experiment(
        self,
        *,
        question: AttributionQuestion,
        role: ExperimentRole,
        component_ids: tuple[str, ...],
        intervention_ids: tuple[str, ...],
        metrics: dict[str, Decimal],
        suffix: str,
        interaction_spec_digest: str | None = None,
    ) -> ExperimentManifest:
        evidence = {
            "question": question.digest,
            "role": role.value,
            "components": list(component_ids),
            "interventions": list(intervention_ids),
            "metrics": {key: str(value) for key, value in sorted(metrics.items())},
        }
        trace = native_trace_envelope(
            trace_id=f"trace.{question.question_id}.{suffix}",
            question_digest=question.digest,
            capability_manifest_digest=self._manifest.digest,
            native_topology_digest=self._manifest.native_topology_digest,
            context_digest=self._context_digest,
            producer_id=SYNTHETIC_ARTIFACT_PRODUCER_ID,
            producer_version=SYNTHETIC_ARTIFACT_PRODUCER_VERSION,
            source_kind=self._manifest.evidence_source_kind,
            payload={**evidence, "trace": suffix},
        )
        trace_digest = self._resolver.register(trace)
        draft = ExperimentManifest(
            experiment_id=f"experiment.{question.question_id}.{suffix}",
            schema_version=ATTRIBUTION_SCHEMA_VERSION,
            question_id=question.question_id,
            question_digest=question.digest,
            role=role,
            strategy_id=self._manifest.strategy_id,
            strategy_version=self._manifest.strategy_version,
            adapter_id=self._manifest.adapter_id,
            adapter_version=self._manifest.adapter_version,
            capability_manifest_version=self._manifest.capability_manifest_version,
            capability_manifest_digest=self._manifest.digest,
            native_topology_digest=self._manifest.native_topology_digest,
            context_digest=self._context_digest,
            context_bindings=self._context_bindings,
            artifact_digest=digest("unbound-synthetic-experiment-artifact"),
            native_trace_digest=trace_digest,
            plan_receipt_digest=None,
            execution_work_digest=None,
            raw_execution_digest=None,
            metric_calculator_id=_METRIC_CALCULATOR_ID,
            metric_calculator_version=_METRIC_CALCULATOR_VERSION,
            metric_spec_digests={item.metric_id: item.digest for item in question.metric_specs},
            period_start=self._period_start,
            period_end=self._period_end,
            intervention_ids=intervention_ids,
            changed_component_ids=component_ids,
            capital_constrained=True,
            reproduced=True,
            reconciled=True,
            pit_validated=True,
            partition_authorized=True,
            question_bound_before_outcomes=True,
            decision_trace_reconciled=True,
            opportunity_trace_reconciled=True,
            interaction_spec_digest=interaction_spec_digest,
            metrics=metrics,
        )
        artifact = experiment_evidence_envelope(
            draft,
            producer_id=SYNTHETIC_ARTIFACT_PRODUCER_ID,
            producer_version=SYNTHETIC_ARTIFACT_PRODUCER_VERSION,
            source_kind=self._manifest.evidence_source_kind,
        )
        return replace(draft, artifact_digest=self._resolver.register(artifact))


class SyntheticAttributionExecutor:
    """Deterministic raw-fact executor permanently limited to the synthetic fixture."""

    __slots__ = (
        "_adapter",
        "_calls",
        "_conditions",
        "_corrupt_on_calls",
        "_fail_on_calls",
    )

    def __init__(
        self,
        adapter: SyntheticStrategyAttributionAdapter | None = None,
        *,
        fail_on_calls: tuple[int, ...] = (),
        corrupt_on_calls: tuple[int, ...] = (),
    ) -> None:
        self._adapter = adapter or SyntheticStrategyAttributionAdapter()
        self._fail_on_calls = frozenset(fail_on_calls)
        self._corrupt_on_calls = frozenset(corrupt_on_calls)
        self._calls: list[str] = []
        self._conditions = {
            condition.digest: condition
            for question in self._adapter.questions()
            for condition in question.condition_specs
        }

    @property
    def calls(self) -> tuple[str, ...]:
        return tuple(self._calls)

    def binding(self) -> ExecutorBinding:
        return ExecutorBinding(
            executor_id=SYNTHETIC_ARTIFACT_PRODUCER_ID,
            executor_version=SYNTHETIC_ARTIFACT_PRODUCER_VERSION,
            producer_id=SYNTHETIC_ARTIFACT_PRODUCER_ID,
            producer_version=SYNTHETIC_ARTIFACT_PRODUCER_VERSION,
            source_kind=self._adapter.capability_manifest().evidence_source_kind,
            metric_calculator_id=_METRIC_CALCULATOR_ID,
            metric_calculator_version=_METRIC_CALCULATOR_VERSION,
            synthetic_fixture=True,
        )

    def execute(self, work: PlannedExecutionWork) -> RawExecutionResult:
        self._calls.append(work.experiment.experiment_id)
        call_number = len(self._calls)
        if call_number in self._fail_on_calls:
            raise AttributionError(
                "synthetic_executor_injected_failure",
                "deterministic failure injection",
            )
        manifest = self._adapter.capability_manifest()
        if work.adapter_manifest_digest != manifest.digest:
            raise AttributionError(
                "synthetic_executor_adapter_mismatch",
                "work does not bind the synthetic adapter",
            )
        metric_ids = tuple(work.experiment.metric_spec_digests)
        metrics: dict[str, Decimal] = {}
        condition_slices: tuple[RawConditionSlice, ...] = ()
        if work.experiment.kind is PlanNodeKind.BASELINE_REPLAY:
            metrics = self._select_metrics(_BASELINE_VALUES, metric_ids)
        elif work.experiment.kind is PlanNodeKind.SINGLE_INTERVENTION:
            component_id = work.experiment.component_ids[0]
            try:
                values = _INTERVENTION_VALUES[component_id]
            except KeyError as exc:
                raise AttributionError(
                    "synthetic_component_unsupported",
                    component_id,
                ) from exc
            metrics = self._select_metrics(values, metric_ids)
        elif work.experiment.kind is PlanNodeKind.JOINT_INTERVENTION:
            metrics = self._select_metrics(_JOINT_VALUES, metric_ids)
        elif work.experiment.kind is PlanNodeKind.CONDITION_ANALYSIS:
            condition = self._conditions.get(work.experiment.condition_spec_digest or "")
            if condition is None:
                raise AttributionError(
                    "synthetic_condition_unsupported",
                    "condition is not part of the frozen fixture",
                )
            values = _CONDITION_VALUES[condition.condition_id]
            condition_slices = (
                RawConditionSlice(
                    condition_id=condition.condition_id,
                    condition_spec_digest=condition.digest,
                    label=condition.label,
                    sample_count=values["sample_count"],
                    baseline_metrics=self._select_metrics(values["baseline"], metric_ids),
                    intervention_metrics=self._select_metrics(values["intervention"], metric_ids),
                ),
            )
        else:
            raise AttributionError(
                "synthetic_executor_node_unsupported",
                work.experiment.kind.value,
            )
        observation_basis = {
            "fixture": "synthetic_raw_execution_not_strategy_evidence",
            "work_digest": work.digest,
            "planned_experiment_digest": work.experiment.digest,
            "metrics": metrics,
            "condition_slices": [item.to_dict() for item in condition_slices],
        }
        replay_digest = digest({**observation_basis, "observation": "replay"})
        reconciliation_digest = digest({**observation_basis, "observation": "reconciliation"})
        decision_trace_digest = digest({**observation_basis, "observation": "decision_trace"})
        opportunity_trace_digest = digest({**observation_basis, "observation": "opportunity_trace"})
        reconciliation_observation = reconciliation_digest
        if call_number in self._corrupt_on_calls:
            reconciliation_observation = digest(
                {
                    "fixture": "synthetic_corrupt_observation",
                    "work_digest": work.digest,
                }
            )
        return RawExecutionResult(
            work_id=work.work_id,
            work_digest=work.digest,
            executor_binding_digest=work.executor_binding_digest,
            planned_experiment_id=work.experiment.experiment_id,
            planned_experiment_digest=work.experiment.digest,
            node_kind=work.experiment.kind,
            parent_raw_result_digests=work.parent_raw_result_digests,
            actual_execution_scope_digest=work.experiment.execution_scope_digest,
            actual_component_ids=work.experiment.component_ids,
            actual_intervention_ids=work.experiment.intervention_ids,
            metric_spec_digests=work.experiment.metric_spec_digests,
            metrics=metrics,
            condition_slices=condition_slices,
            replay_reference_digest=replay_digest,
            replay_observation_digest=replay_digest,
            reconciliation_reference_digest=reconciliation_digest,
            reconciliation_observation_digest=reconciliation_observation,
            decision_trace_reference_digest=decision_trace_digest,
            decision_trace_observation_digest=decision_trace_digest,
            opportunity_trace_reference_digest=opportunity_trace_digest,
            opportunity_trace_observation_digest=opportunity_trace_digest,
            pit_context_digest=work.context_digest,
            authorization_scope_digest=work.experiment.execution_scope_digest,
            capital_constrained_observed=True,
            trace_payload={
                "fixture": "synthetic_raw_trace_not_strategy_evidence",
                "planned_experiment_id": work.experiment.experiment_id,
                "node_kind": work.experiment.kind.value,
            },
        )

    @staticmethod
    def _select_metrics(
        values: Mapping[str, Decimal], metric_ids: tuple[str, ...]
    ) -> dict[str, Decimal]:
        try:
            return {metric_id: values[metric_id] for metric_id in metric_ids}
        except KeyError as exc:
            raise AttributionError(
                "synthetic_metric_unsupported",
                str(exc.args[0]),
            ) from exc


register_synthetic_executor_implementation(SyntheticAttributionExecutor)


def synthetic_run_request(
    adapter: SyntheticStrategyAttributionAdapter | None = None,
) -> AttributionRunRequest:
    semantic_adapter = adapter or SyntheticStrategyAttributionAdapter()
    return AttributionRunRequest.create_from_adapter(
        adapter=semantic_adapter,
        context_bindings=_CONTEXT_BINDINGS,
        period_start=_PERIOD_START,
        period_end=_PERIOD_END,
        metric_calculator_id=_METRIC_CALCULATOR_ID,
        metric_calculator_version=_METRIC_CALCULATOR_VERSION,
    )


def build_demo_workspace() -> AttributionWorkspace:
    adapter = SyntheticStrategyAttributionAdapter()
    manifest = adapter.capability_manifest()
    claims = []
    experiments: list[ExperimentManifest] = []
    condition_evidence: list[ConditionComparison] = []
    interaction_evidence: list[InteractionComparison] = []
    check_evidence: list[AnalysisCheckEvidence] = []
    questions = adapter.questions()
    for question in questions:
        baseline = adapter.baseline(question)
        intervention = adapter.intervention(question)
        conditions = adapter.condition_comparisons(question)
        interactions = adapter.interaction_runs(question)
        checks = adapter.analysis_checks(question)
        experiments.extend((baseline, intervention))
        condition_evidence.extend(conditions)
        interaction_evidence.extend(interactions)
        check_evidence.extend(checks)
        for comparison in interactions:
            experiments.extend((comparison.partner_intervention, comparison.joint_intervention))
        claims.append(
            evaluate_attribution(
                adapter=manifest,
                question=question,
                baseline=baseline,
                intervention=intervention,
                conditions=conditions,
                interactions=interactions,
                analysis_checks=checks,
                artifact_resolver=adapter.artifact_resolver(),
            )
        )
    return AttributionWorkspace(
        workspace_id="workspace.synthetic-attribution-demo.v1",
        title="Quanthus strategy attribution",
        subtitle="Synthetic six-month replay for interface and contract validation",
        source_kind="synthetic_fixture_not_strategy_evidence",
        adapter=manifest,
        baseline=experiments[0],
        claims=tuple(claims),
        questions=questions,
        experiments=tuple(experiments),
        condition_comparisons=tuple(condition_evidence),
        interaction_comparisons=tuple(interaction_evidence),
        analysis_checks=tuple(check_evidence),
        artifacts=adapter.artifact_resolver().artifacts(),
        unavailable_component_ids=("allocator.capital",),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the synthetic attribution workspace")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    workspace = build_demo_workspace()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(workspace.to_dict(), ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

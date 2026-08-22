"""Versioned semantic bridge from the sealed formal adapter to the generic Pipeline."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.expert_evaluation.serialization import ExpertEvaluationError, digest_value
from quanthus.expert_strategy.attribution_adapter import FormalAttributionAdapter
from quanthus.expert_strategy.registry import ExpertStrategyRegistry
from quanthus.strategy_attribution import (
    FORMAL_NO_RANKING_METHOD,
    NOT_REQUIRED_CHECK_METHOD,
    PAIRED_BLOCK_BOOTSTRAP_METHOD,
    PAIRED_REPLAY_EFFECT_METHOD,
    PRIMARY_CLASSIFICATION_METHOD,
    AdapterCapabilityManifest,
    AnalysisRole,
    AttributionCapability,
    AttributionQuestion,
    ComponentDescriptor,
    InterventionSpec,
    MetricDisplay,
    MetricPreference,
    MetricSpec,
    SampleRole,
    StrategyAttributionAdapter,
    digest,
)
from quanthus.strategy_attribution_planning import AttributionRunRequest

BRIDGE_ID = "adapter.p4-trend-risk-formal-attribution-pipeline"
BRIDGE_VERSION = "1.0.0"
CAPABILITY_MANIFEST_VERSION = "1.0.0"
SOURCE_KIND = "formal_strategy_evidence"
METRIC_CALCULATOR_ID = "quanthus.formal-p2-attribution-metrics"
METRIC_CALCULATOR_VERSION = "1.0.0"
PREREGISTRATION_KEY = "config/expert-strategies/p4-trend-risk-attribution.v1.json"

_DISPLAY_BY_UNIT = {
    "ratio": MetricDisplay.PERCENT,
    "CNY": MetricDisplay.CURRENCY,
    "count": MetricDisplay.COUNT,
}
_PREFERENCE = {
    "higher": MetricPreference.HIGHER,
    "lower": MetricPreference.LOWER,
    "unoriented": MetricPreference.UNORIENTED,
}


class FormalStrategyAttributionAdapter(StrategyAttributionAdapter):
    """Expose the approved formal strategy without modifying its sealed adapter."""

    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()
        self.registry = ExpertStrategyRegistry(self.repository_root)
        self.sealed = FormalAttributionAdapter(self.registry)
        self.preregistration = self._load_preregistration()
        self._manifest = self._build_manifest()
        self._questions = self._build_questions()

    def _load_preregistration(self) -> dict[str, Any]:
        path = self.repository_root / PREREGISTRATION_KEY
        try:
            raw = json.loads(path.read_bytes())
        except (OSError, json.JSONDecodeError) as exc:
            raise ExpertEvaluationError(
                "formal_attribution_preregistration_unreadable",
                "formal attribution preregistration is unreadable",
            ) from exc
        if not isinstance(raw, dict):
            raise ExpertEvaluationError(
                "formal_attribution_preregistration_invalid",
                "formal attribution preregistration must be an object",
            )
        expected = str(raw.get("semantic_digest", ""))
        observed = digest_value(
            {key: value for key, value in raw.items() if key != "semantic_digest"}
        )
        if expected != observed:
            raise ExpertEvaluationError(
                "formal_attribution_preregistration_drift",
                "formal attribution preregistration digest differs",
            )
        strategy = raw.get("strategy")
        if not isinstance(strategy, dict) or (
            strategy.get("strategy_card_digest") != self.registry.card.semantic_digest
            or strategy.get("topology_manifest_digest") != self.registry.topology_manifest.digest
            or strategy.get("sealed_adapter_digest") != self.sealed.digest
        ):
            raise ExpertEvaluationError(
                "formal_attribution_binding_drift",
                "preregistration does not bind the approved formal strategy",
            )
        return raw

    @staticmethod
    def _intervention(item: dict[str, Any]) -> InterventionSpec:
        return InterventionSpec(
            intervention_id=str(item["intervention_id"]),
            intervention_version=str(item["intervention_version"]),
            operation="apply_exact_formal_strategy_fallback",
            fallback_semantics=str(item["fallback_id"]),
        )

    def _build_manifest(self) -> AdapterCapabilityManifest:
        interventions = {
            str(item["intervention_id"]): self._intervention(item)
            for item in self.registry.card.interventions
        }
        return AdapterCapabilityManifest(
            adapter_id=BRIDGE_ID,
            adapter_version=BRIDGE_VERSION,
            capability_manifest_version=CAPABILITY_MANIFEST_VERSION,
            strategy_id=self.registry.card.strategy_id,
            strategy_version=self.registry.card.strategy_version,
            evidence_source_kind=SOURCE_KIND,
            native_topology_digest=self.registry.topology_manifest.digest,
            capabilities=(
                AttributionCapability.BASELINE_REPLAY,
                AttributionCapability.COMPONENT_MAP,
                AttributionCapability.LEGAL_INTERVENTION,
                AttributionCapability.DECISION_TRACE,
                AttributionCapability.OPPORTUNITY_TRACE,
            ),
            components=(
                ComponentDescriptor(
                    component_id="strategy.root",
                    component_version=self.registry.card.strategy_version,
                    label="P4 trend-risk strategy",
                    kind="strategy_root",
                    role="native_root",
                    description="Exact approved formal strategy topology.",
                ),
                ComponentDescriptor(
                    component_id="expert.trend-stability",
                    component_version="1.0.0",
                    label="Trend stability",
                    kind="strategy_expert",
                    role="entry_scoring",
                    description="Ordinal trend assessment mapped to entry scoring.",
                    depends_on=("strategy.root",),
                    legal_interventions=(interventions["intervention.trend-stability-abstain"],),
                ),
                ComponentDescriptor(
                    component_id="expert.position-risk-review",
                    component_version="1.0.0",
                    label="Position risk review",
                    kind="strategy_expert",
                    role="entry_and_held_risk",
                    description="Entry cap plus held reduce and exit review.",
                    depends_on=("strategy.root",),
                    legal_interventions=(interventions["intervention.position-risk-base-policy"],),
                ),
                ComponentDescriptor(
                    component_id="component.trend-position-risk-pair",
                    component_version="1.0.0",
                    label="Trend and position-risk pair",
                    kind="declared_intervention_pair",
                    role="joint_fallback_representation",
                    description=(
                        "Planner representation of the approved joint fallback; not a third expert."
                    ),
                    depends_on=(
                        "expert.trend-stability",
                        "expert.position-risk-review",
                    ),
                    legal_interventions=(interventions["intervention.both-experts-fallback"],),
                ),
            ),
        )

    def _metric_specs(self) -> tuple[MetricSpec, ...]:
        specs: list[MetricSpec] = []
        for item in self.preregistration["metrics"]:
            metric_id = str(item["metric_id"])
            unit = str(item["unit"])
            specs.append(
                MetricSpec(
                    metric_id=metric_id,
                    label=metric_id.replace("_", " ").title(),
                    unit=unit,
                    definition_version="1.0.0",
                    definition_digest=digest(
                        {
                            "metric_id": metric_id,
                            "definition": item["definition"],
                            "unit": unit,
                        }
                    ),
                    preference=_PREFERENCE[str(item["preference"])],
                    materiality=Decimal(str(item["materiality"])),
                    display=_DISPLAY_BY_UNIT[unit],
                    display_decimals=2 if unit == "CNY" else 4,
                )
            )
        return tuple(specs)

    def _build_questions(self) -> tuple[AttributionQuestion, ...]:
        interventions = {
            component.component_id: component.legal_interventions[0]
            for component in self._manifest.components
            if component.legal_interventions
        }
        metrics = self._metric_specs()
        common = {
            "question_version": "1.0.0",
            "hypothesis_family_id": "candidate.p4-trend-risk.development-attribution.v1",
            "estimand_kind": "complete_capital_constrained_p2_fallback_effect",
            "effect_method": PAIRED_REPLAY_EFFECT_METHOD,
            "intervention_semantics": "Apply only the exact strategy-card fallback.",
            "target_population": "population.p4-development-candidates@1.0.0",
            "sample_role": SampleRole.DEVELOPMENT,
            "analysis_role": AnalysisRole.CONFIRMATORY,
            "multiple_comparison_rule": FORMAL_NO_RANKING_METHOD,
            "classification_rule": PRIMARY_CLASSIFICATION_METHOD,
            "decision_rule": "human_lifecycle_review_after_complete_evidence_v1",
            "decision_actions": {
                "valid:beneficial": "human_lifecycle_review_required",
                "valid:harmful": "human_lifecycle_review_required",
                "valid:negligible": "human_lifecycle_review_required",
                "valid:tradeoff": "human_lifecycle_review_required",
                "valid:inconclusive": "human_lifecycle_review_required",
                "invalid:inconclusive": "block_lifecycle_decision",
                "unavailable:inconclusive": "block_lifecycle_decision",
            },
            "uncertainty_required": True,
            "uncertainty_method": PAIRED_BLOCK_BOOTSTRAP_METHOD,
            "stability_required": False,
            "stability_method": NOT_REQUIRED_CHECK_METHOD,
            "bound_before_outcomes": True,
            "primary_metric_ids": ("capital_constrained_net_total_return_delta",),
            "metric_specs": metrics,
            "required_capabilities": (
                AttributionCapability.DECISION_TRACE,
                AttributionCapability.OPPORTUNITY_TRACE,
            ),
            "known_assumptions": (
                "Market dates, securities, event windows, and capital units are dependent.",
                "The pair component is a planner representation and not a third expert.",
            ),
            "exclusions": (
                "Arbitration 2022-2024 and permanent-blind 2025 are sealed.",
                "Interaction is never allocated to either expert.",
            ),
            "stop_conditions": tuple(self.preregistration["stop_conditions"]),
        }
        questions = []
        for frozen in self.preregistration["questions"]:
            component_id = str(frozen["subject_component_id"])
            intervention = interventions[component_id]
            questions.append(
                AttributionQuestion(
                    question_id=str(frozen["question_id"]),
                    subject_component_id=component_id,
                    intervention_id=intervention.intervention_id,
                    intervention_spec_digest=intervention.digest,
                    **common,
                )
            )
        return tuple(questions)

    def capability_manifest(self) -> AdapterCapabilityManifest:
        return self._manifest

    def questions(self) -> tuple[AttributionQuestion, ...]:
        return self._questions

    def run_request(self) -> AttributionRunRequest:
        population = self.preregistration["population"]
        data = self.preregistration["data"]
        position = self.registry.card.semantic_sections["position_and_execution"]
        return AttributionRunRequest.create_from_adapter(
            adapter=self,
            context_bindings={
                "data_snapshot": (
                    f"{data['dwd_package_id']}|{data['dwd_content_digest']}|"
                    f"{data['correction_content_digest']}"
                ),
                "eligible_universe": str(population["assessment_population_digest"]),
                "capital": (
                    f"{position['unit_count']}x{position['unit_notional']}:"
                    f"{position['funding_policy']}"
                ),
                "matcher": str(position["matching_policy"]),
                "costs": str(position["cost_policy"]),
                "settlement": str(position["settlement_policy"]),
                "policy": self.registry.card.semantic_digest,
                "execution_assumptions": str(position["execution_profile"]),
                "seeds": str(self.preregistration["uncertainty"]["seed_rule"]),
            },
            period_start=date.fromisoformat(str(population["decision_start"])),
            period_end=date.fromisoformat(str(population["decision_end"])),
            metric_calculator_id=METRIC_CALCULATOR_ID,
            metric_calculator_version=METRIC_CALCULATOR_VERSION,
        )

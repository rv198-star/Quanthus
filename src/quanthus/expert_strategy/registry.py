"""Exact registry closure for the first approved formal expert strategy."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quanthus.expert_evaluation import ExpertEvaluationSpec, artifact_from_bytes
from quanthus.expert_evaluation.runner import FormalStrategyRef
from quanthus.expert_evaluation.serialization import ExpertEvaluationError, digest_value
from quanthus.expert_strategy.contracts import load_expert_strategy_card

STRATEGY_REGISTRY_KEY = "config/expert-strategies/p4-trend-risk.v1.0.1.json"
POSITION_SPEC_REGISTRY_KEY = "specs/position-risk-formal-policy.evaluation.v1.json"
SENTINEL_PLAN_ID = "plan.p4-trend-risk-january-2020-sentinel"
SENTINEL_PLAN_VERSION = "1.0.0"


@dataclass(frozen=True)
class FormalTopologyManifest:
    strategy_id: str
    strategy_version: str
    strategy_card_digest: str
    evaluation_specs: tuple[tuple[str, str, str], ...]
    expert_bindings: tuple[tuple[str, str, str], ...]
    policy_mappings: tuple[tuple[str, str, str], ...]
    hard_guardians: tuple[str, ...]
    legal_interventions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "quanthus.formal-expert-topology-manifest.v1",
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "strategy_card_digest": self.strategy_card_digest,
            "evaluation_specs": [
                {"assessment_id": item[0], "assessment_version": item[1], "digest": item[2]}
                for item in self.evaluation_specs
            ],
            "expert_bindings": [
                {"expert_id": item[0], "expert_version": item[1], "semantic_digest": item[2]}
                for item in self.expert_bindings
            ],
            "policy_mappings": [
                {"mapping_id": item[0], "mapping_version": item[1], "digest": item[2]}
                for item in self.policy_mappings
            ],
            "hard_guardians": list(self.hard_guardians),
            "legal_interventions": list(self.legal_interventions),
        }

    @property
    def digest(self) -> str:
        return digest_value(self.to_dict())


class ExpertStrategyRegistry:
    """Resolve only the exact approved card and its derived policy-only spec."""

    def __init__(self, repository_root: Path, expert_registry_root: Path | None = None) -> None:
        self.repository_root = repository_root.resolve()
        self.expert_registry_root = (
            expert_registry_root or self.repository_root / "config/expert-evaluations"
        ).resolve()
        self.card_path = (self.repository_root / STRATEGY_REGISTRY_KEY).resolve()
        self.position_spec_path = (
            self.expert_registry_root / POSITION_SPEC_REGISTRY_KEY
        ).resolve()
        self.card = load_expert_strategy_card(self.card_path, self.repository_root)
        self.card.require_implementation_authority()
        self.position_spec = self._load_position_spec()
        self._verify_position_spec()

    def _load_position_spec(self) -> ExpertEvaluationSpec:
        try:
            artifact = artifact_from_bytes(self.position_spec_path.read_bytes())
        except OSError as exc:
            raise ExpertEvaluationError(
                "registry_artifact_missing", "position-risk evaluation spec is unavailable"
            ) from exc
        if not isinstance(artifact, ExpertEvaluationSpec):
            raise ExpertEvaluationError(
                "strategy_reference_mismatch", "position-risk artifact is not an evaluation spec"
            )
        return artifact

    @property
    def trend_spec_identity(self) -> tuple[str, str, str]:
        trend = self.card.experts[0]
        return (
            str(trend["expert_id"]),
            str(trend["expert_version"]),
            str(trend["evaluation_spec_digest"]),
        )

    @property
    def position_spec_identity(self) -> tuple[str, str, str]:
        return (
            self.position_spec.assessment_id,
            self.position_spec.assessment_version,
            self.position_spec.digest,
        )

    @property
    def evaluation_spec_identities(self) -> tuple[tuple[str, str, str], ...]:
        return tuple(sorted((self.trend_spec_identity, self.position_spec_identity)))

    @property
    def intervention_ids(self) -> tuple[str, ...]:
        return tuple(str(item["intervention_id"]) for item in self.card.interventions)

    @property
    def sentinel_plan_digest(self) -> str:
        budget = self.card.semantic_sections["resource_budget"]
        return digest_value(
            {
                "schema_version": "quanthus.formal-strategy-sentinel-plan.v1",
                "plan_id": SENTINEL_PLAN_ID,
                "plan_version": SENTINEL_PLAN_VERSION,
                "strategy_card_digest": self.card.semantic_digest,
                "window_start": budget["sentinel_window_start"],
                "window_end": budget["sentinel_window_end"],
                "requested_interventions": [],
                "economic_result_authority": False,
            }
        )

    @property
    def topology_manifest(self) -> FormalTopologyManifest:
        trend, position = self.card.experts
        trend_mapping = trend["policy_mapping"]
        position_mapping = position["policy_mapping"]
        guardians = self.card.semantic_sections["position_and_execution"]["hard_guardians"]
        return FormalTopologyManifest(
            strategy_id=self.card.strategy_id,
            strategy_version=self.card.strategy_version,
            strategy_card_digest=self.card.semantic_digest,
            evaluation_specs=self.evaluation_spec_identities,
            expert_bindings=(
                (
                    str(trend["expert_id"]),
                    str(trend["expert_version"]),
                    str(trend["assessment_digest"]),
                ),
                (
                    str(position["expert_id"]),
                    str(position["expert_version"]),
                    str(position["expert_semantic_digest"]),
                ),
            ),
            policy_mappings=(
                (
                    str(trend_mapping["mapping_id"]),
                    str(trend_mapping["mapping_version"]),
                    str(trend_mapping["mapping_digest"]),
                ),
                (
                    str(position_mapping["mapping_id"]),
                    str(position_mapping["mapping_version"]),
                    str(position_mapping["mapping_digest"]),
                ),
            ),
            hard_guardians=tuple(sorted(str(item) for item in guardians)),
            legal_interventions=self.intervention_ids,
        )

    def formal_strategy_ref(self) -> FormalStrategyRef:
        return FormalStrategyRef(
            self.card.strategy_id,
            self.card.strategy_version,
            self.card.semantic_digest,
            self.sentinel_plan_digest,
            STRATEGY_REGISTRY_KEY,
        )

    def formal_attribution_ref(self, plan_digest: str) -> FormalStrategyRef:
        return FormalStrategyRef(
            self.card.strategy_id,
            self.card.strategy_version,
            self.card.semantic_digest,
            plan_digest,
            STRATEGY_REGISTRY_KEY,
        )

    def expected_bindings(
        self, strategy: FormalStrategyRef, registry_root: Path
    ) -> tuple[tuple[tuple[str, str, str], ...], tuple[str, ...]]:
        if registry_root.resolve() != self.expert_registry_root:
            raise ExpertEvaluationError(
                "strategy_registry_mismatch", "formal strategy registry root differs"
            )
        if (
            strategy.strategy_id,
            strategy.strategy_version,
            strategy.card_digest,
            strategy.registry_key,
        ) != (
            self.card.strategy_id,
            self.card.strategy_version,
            self.card.semantic_digest,
            STRATEGY_REGISTRY_KEY,
        ):
            raise ExpertEvaluationError(
                "formal_strategy_identity_mismatch", "formal strategy reference differs"
            )
        if strategy.attribution_plan_digest != self.sentinel_plan_digest:
            from quanthus.expert_strategy.formal_attribution_semantics import (
                FormalStrategyAttributionAdapter,
            )
            from quanthus.strategy_attribution_planning import compile_attribution_plan

            formal_plan_digest = compile_attribution_plan(
                FormalStrategyAttributionAdapter(self.repository_root).run_request()
            ).digest
            if strategy.attribution_plan_digest != formal_plan_digest:
                raise ExpertEvaluationError(
                    "formal_strategy_plan_mismatch", "formal strategy plan differs"
                )
        return self.evaluation_spec_identities, self.intervention_ids

    def _verify_position_spec(self) -> None:
        position = self.card.experts[1]
        mapping = position["policy_mapping"]
        if (
            self.position_spec.assessment_id,
            self.position_spec.assessment_version,
            self.position_spec.raw_value_type.value,
            self.position_spec.observation_semantics.digest,
            self.position_spec.population.digest,
            self.position_spec.partition.digest,
        ) != (
            position["expert_id"],
            position["expert_version"],
            "policy_only",
            position["expert_semantic_digest"],
            self.card.semantic_sections["population"]["assessment_population"]["digest"],
            "sha256:accba0894d9892df25c3465f2e572f25d859631bac4a14fa0bcaa236db621bbc",
        ):
            raise ExpertEvaluationError(
                "strategy_reference_mismatch", "position-risk policy spec differs from card"
            )
        mappings = self.position_spec.policy_mappings
        if len(mappings) != 1 or (
            mappings[0].mapping_id,
            mappings[0].mapping_version,
            mappings[0].mapping_digest,
            tuple(item.value for item in mappings[0].effect_kinds),
        ) != (
            mapping["mapping_id"],
            mapping["mapping_version"],
            mapping["mapping_digest"],
            tuple(sorted(mapping["effect_kinds"])),
        ):
            raise ExpertEvaluationError(
                "strategy_reference_mismatch", "position-risk mapping spec differs from card"
            )

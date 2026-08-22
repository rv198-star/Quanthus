"""Sealed intervention surface consumed by bounded formal attribution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from quanthus.expert_evaluation.serialization import digest_value
from quanthus.expert_strategy.registry import ExpertStrategyRegistry

ADAPTER_ID = "adapter.p4-trend-risk-formal-attribution"
ADAPTER_VERSION = "1.0.0"


@dataclass(frozen=True)
class FormalAttributionAdapter:
    registry: ExpertStrategyRegistry

    @property
    def capability_manifest(self) -> dict[str, Any]:
        card = self.registry.card
        return {
            "schema_version": "quanthus.formal-attribution-adapter-capability.v1",
            "adapter_id": ADAPTER_ID,
            "adapter_version": ADAPTER_VERSION,
            "strategy_id": card.strategy_id,
            "strategy_version": card.strategy_version,
            "strategy_card_digest": card.semantic_digest,
            "topology_manifest_digest": self.registry.topology_manifest.digest,
            "baseline_operation": "complete_capital_constrained_p2_replay",
            "interventions": [
                {
                    "intervention_id": item["intervention_id"],
                    "intervention_version": item["intervention_version"],
                    "targets": list(item["targets"]),
                    "fallback_id": item["fallback_id"],
                }
                for item in card.interventions
            ],
            "joint_effect_allocation": "forbidden",
            "system_guardians_intervenable": False,
            "base_position_management_intervenable": False,
        }

    @property
    def digest(self) -> str:
        return digest_value(self.capability_manifest)

    def require_intervention(self, intervention_id: str) -> None:
        admitted = {
            str(item["intervention_id"])
            for item in self.capability_manifest["interventions"]
        }
        if intervention_id not in admitted:
            raise ValueError("formal attribution intervention is not admitted")

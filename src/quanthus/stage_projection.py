"""Versioned Stage-10/11 projection propositions over Support Evolution Facts.

This module does not publish a primary stage or a LabelCard. It formalizes two related
propositions: Stage 10 is a persistent structural-failure context and Stage 11 is a nested
current rebound condition within that confirmed context.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from quanthus.structure_support_facts import SupportFactsResult
from quanthus.structure_v2_inputs import digest

SCHEMA = "quanthus.stock-stage-projection-contract.v1"
PROJECTION_ID = "stock-stage.support-evolution-10-11"


class StageProjectionError(ValueError):
    """Raised when the projection contract or source facts violate the frozen boundary."""


def _decimal(value: Any, name: str) -> Decimal:
    if not isinstance(value, str):
        raise StageProjectionError(f"{name} must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise StageProjectionError(f"{name} must be a decimal string") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise StageProjectionError(f"{name} must be finite and positive")
    return parsed


@dataclass(frozen=True)
class StageProjectionContract:
    projection_id: str
    version: str
    persistence_sessions: int
    failure_depth_abs_min: Decimal
    rebound_min: Decimal
    source_scope: str
    contract_digest: str


@dataclass(frozen=True)
class StageProjectionObservation:
    process_id: str
    security_code: str
    market_date: date
    scale: int
    contract_digest: str
    source_process_revision_ref: str
    source_distance_revision_ref: str | None
    source_rebound_revision_ref: str | None
    stage_10_confirmation_ref: str | None
    stage_10_context_active: bool
    stage_11_rebound_condition_active: bool
    candidate_stages: tuple[int, ...]
    primary_stage: None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StageProjectionResult:
    contract: StageProjectionContract
    observations: tuple[StageProjectionObservation, ...]

    @property
    def digest(self) -> str:
        return digest([item.to_dict() for item in self.observations])

    def semantic(self) -> dict[str, Any]:
        return {
            "schema_version": "quanthus.stock-stage-projection-result.v1",
            "projection_id": self.contract.projection_id,
            "version": self.contract.version,
            "contract_digest": self.contract.contract_digest,
            "primary_stage_authorized": False,
            "observations": [item.to_dict() for item in self.observations],
        }


def load_stage_projection_contract(path: Path) -> StageProjectionContract:
    raw = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "schema_version",
        "projection_id",
        "version",
        "work_item",
        "display_name",
        "source_fact_family",
        "authority",
        "stage_10",
        "stage_11",
        "relationship",
        "claim_limits",
    }
    if not isinstance(raw, dict) or set(raw) != required:
        raise StageProjectionError("projection contract fields differ")
    if raw["schema_version"] != SCHEMA or raw["projection_id"] != PROJECTION_ID:
        raise StageProjectionError("projection contract identity differs")
    if raw["version"] != "1.0.0" or raw["work_item"] != "P3-STAGE-002":
        raise StageProjectionError("projection version or work item differs")

    source = raw["source_fact_family"]
    if not isinstance(source, dict) or source != {
        "display_name": "演化支撑事实族 / Support Evolution Fact Family",
        "historical_planning_code": "F9",
        "required_concepts": [
            "structure.episode.support-failure-sequence",
            "structure.geometry.signed-distance",
            "structure.geometry.post-break-rebound",
        ],
        "accepted_scope": "bounded_retrospective_f9_support_process",
    }:
        raise StageProjectionError("source fact-family binding differs")

    authority = raw["authority"]
    if not isinstance(authority, dict) or authority != {
        "development_projection_contract_authorized": True,
        "primary_stage_arbitration_authorized": False,
        "official_label_card_publication_authorized": False,
        "daily_schedule_authorized": False,
        "strategy_consumption_authorized": False,
        "trading_authorized": False,
        "future_outcome_access_authorized": False,
        "sealed_partition_access_authorized": False,
        "permanent_blind_access_authorized": False,
    }:
        raise StageProjectionError("projection authority differs")

    stage10 = raw["stage_10"]
    if (
        not isinstance(stage10, dict)
        or stage10.get("code") != 10
        or stage10.get("semantic_role") != "persistent_structural_failure_context"
        or stage10.get("active_context")
        != "after_confirmation_while_same_support_remains_unreclaimed"
    ):
        raise StageProjectionError("Stage 10 semantic contract differs")
    confirmation = stage10.get("confirmation")
    if not isinstance(confirmation, dict) or set(confirmation) != {
        "same_support_must_be_unreclaimed",
        "minimum_unreclaimed_sessions",
        "minimum_failure_depth_abs",
        "first_qualifying_observation_confirms_once",
        "confirmation_reference_is_immutable",
    }:
        raise StageProjectionError("Stage 10 confirmation contract differs")
    if (
        confirmation["same_support_must_be_unreclaimed"] is not True
        or type(confirmation["minimum_unreclaimed_sessions"]) is not int
        or confirmation["minimum_unreclaimed_sessions"] < 1
        or confirmation["first_qualifying_observation_confirms_once"] is not True
        or confirmation["confirmation_reference_is_immutable"] is not True
    ):
        raise StageProjectionError("Stage 10 confirmation policy differs")

    stage11 = raw["stage_11"]
    if (
        not isinstance(stage11, dict)
        or stage11.get("code") != 11
        or stage11.get("semantic_role")
        != "nested_rebound_condition_within_confirmed_failure_context"
    ):
        raise StageProjectionError("Stage 11 semantic contract differs")
    requirements = stage11.get("requirements")
    if not isinstance(requirements, dict) or set(requirements) != {
        "prior_stage_10_confirmation_required",
        "same_support_must_remain_unreclaimed",
        "minimum_post_break_rebound",
        "current_stage_10_depth_reconfirmation_required",
    }:
        raise StageProjectionError("Stage 11 requirements differ")
    if (
        requirements["prior_stage_10_confirmation_required"] is not True
        or requirements["same_support_must_remain_unreclaimed"] is not True
        or requirements["current_stage_10_depth_reconfirmation_required"] is not False
    ):
        raise StageProjectionError("Stage 11 dependency policy differs")

    relationship = raw["relationship"]
    expected_relationship = {
        "stage_11_is_nested_under_stage_10_context": True,
        "candidate_set_may_contain_10_and_11_together": True,
        "candidate_set_is_not_primary_stage": True,
        "primary_stage_output_must_be_null": True,
        "reclaim_or_censor_clears_active_candidates_without_rewriting_history": True,
        "stage_11_current_condition_may_turn_false_without_implying_a_primary_stage_transition": (
            True
        ),
    }
    if relationship != expected_relationship:
        raise StageProjectionError("Stage 10/11 relationship contract differs")
    if not isinstance(raw["claim_limits"], list) or len(raw["claim_limits"]) < 5:
        raise StageProjectionError("projection claim limits are incomplete")

    return StageProjectionContract(
        raw["projection_id"],
        raw["version"],
        confirmation["minimum_unreclaimed_sessions"],
        _decimal(confirmation["minimum_failure_depth_abs"], "minimum_failure_depth_abs"),
        _decimal(requirements["minimum_post_break_rebound"], "minimum_post_break_rebound"),
        source["accepted_scope"],
        digest(raw),
    )


def project_support_evolution_stages(
    facts: SupportFactsResult,
    contract: StageProjectionContract,
) -> StageProjectionResult:
    """Project Stage-10 context and Stage-11 rebound propositions without primary arbitration."""
    distances = {item.identity.revision_id: item for item in facts.distances}
    rebounds = {item.identity.revision_id: item for item in facts.rebounds}
    by_process: dict[str, list[Any]] = {}
    for item in facts.observations:
        by_process.setdefault(item.process_id, []).append(item)

    output: list[StageProjectionObservation] = []
    for process_id in sorted(by_process):
        confirmation_ref: str | None = None
        for item in sorted(by_process[process_id], key=lambda value: value.market_date):
            stage10_context = False
            stage11_condition = False
            candidates: tuple[int, ...] = ()

            if item.phase == "unreclaimed":
                if item.support_distance_revision_ref is None:
                    raise StageProjectionError("unreclaimed observation lacks support distance")
                distance = distances.get(item.support_distance_revision_ref)
                if distance is None:
                    raise StageProjectionError("support distance reference is unresolved")
                if (
                    confirmation_ref is None
                    and item.unreclaimed_age_sessions is not None
                    and item.unreclaimed_age_sessions >= contract.persistence_sessions
                    and distance.value <= -contract.failure_depth_abs_min
                ):
                    confirmation_ref = digest(
                        {
                            "projection_id": contract.projection_id,
                            "projection_version": contract.version,
                            "contract_digest": contract.contract_digest,
                            "stage_code": 10,
                            "process_id": process_id,
                            "confirmed_at": item.market_date,
                            "support_process_revision_ref": item.revision_id,
                            "support_distance_revision_ref": item.support_distance_revision_ref,
                        }
                    )
                stage10_context = confirmation_ref is not None
                if stage10_context and item.post_break_rebound_revision_ref is not None:
                    rebound = rebounds.get(item.post_break_rebound_revision_ref)
                    if rebound is None:
                        raise StageProjectionError("post-break rebound reference is unresolved")
                    stage11_condition = rebound.value >= contract.rebound_min
                candidates = (10, 11) if stage11_condition else ((10,) if stage10_context else ())
            elif item.phase not in {"intact", "reclaimed", "censored"}:
                raise StageProjectionError(f"unsupported support-process phase: {item.phase}")

            reason = (
                "stage_10_context_with_stage_11_rebound_condition"
                if stage11_condition
                else (
                    "stage_10_context_confirmed"
                    if stage10_context
                    else (
                        "support_process_terminal_or_inactive"
                        if item.phase in {"intact", "reclaimed", "censored"}
                        else "unreclaimed_not_stage_10_confirmed"
                    )
                )
            )
            output.append(
                StageProjectionObservation(
                    item.process_id,
                    item.security_code,
                    item.market_date,
                    item.scale,
                    contract.contract_digest,
                    item.revision_id,
                    item.support_distance_revision_ref,
                    item.post_break_rebound_revision_ref,
                    confirmation_ref,
                    stage10_context,
                    stage11_condition,
                    candidates,
                    None,
                    reason,
                )
            )

    return StageProjectionResult(contract, tuple(output))


def projection_counts(result: StageProjectionResult) -> dict[str, int]:
    confirmations = {
        item.stage_10_confirmation_ref
        for item in result.observations
        if item.stage_10_confirmation_ref is not None
    }
    return {
        "observation_count": len(result.observations),
        "stage_10_confirmation_count": len(confirmations),
        "stage_10_context_count": sum(item.stage_10_context_active for item in result.observations),
        "stage_11_rebound_condition_count": sum(
            item.stage_11_rebound_condition_active for item in result.observations
        ),
        "nested_10_11_candidate_count": sum(
            item.candidate_stages == (10, 11) for item in result.observations
        ),
        "primary_stage_non_null_count": sum(
            item.primary_stage is not None for item in result.observations
        ),
    }

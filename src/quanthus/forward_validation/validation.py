# ruff: noqa: E501
"""Fail-closed validation for the frozen P6-GOV-001 entry proposal."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from quanthus.forward_validation.contracts import (
    ForwardValidationContractError,
    P6EntryProposal,
    fail,
)
from quanthus.forward_validation.serialization import proposal_digest
from quanthus.policy import find_project_root

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_SHA40 = re.compile(r"^[0-9a-f]{40}$")

_SCHEMA = "quanthus.p6-entry-proposal.v1"
_PROPOSAL_ID = "p6-prospective-mechanism-mvp-entry-v1"
_STATE = "FROZEN_ENTRY_ONLY_NO_ACTIVATION"

_BINDING_NAMES = {
    "strategy_card",
    "lifecycle_decision",
    "acceptance_packet",
    "target_decision",
    "forecast_spec",
    "trend_evaluation_spec",
    "position_risk_evaluation_spec",
    "p0_policy",
    "p2_execution_policy",
    "dwd_domain_model",
    "t_close_pipeline",
    "p6_design",
    "entry_review",
    "contracts_code",
    "serialization_code",
    "validation_code",
}

_AUTHORITY_KEYS = {
    "entry_proposal_publication_authorized",
    "p6_data_implementation_authorized",
    "p6_score_implementation_authorized",
    "forward_worker_authorized",
    "paper_runtime_authorized",
    "official_prospective_cycle_authorized",
    "notification_authorized",
    "broker_authorized",
    "live_capital_authorized",
    "p5_authorized",
    "new_expert_authorized",
    "stage_expansion_authorized",
    "stage_action_authority",
    "aggregator_change_authorized",
    "arbitration_2022_2024_access_authorized",
    "permanent_blind_2025_access_authorized",
}

_REQUIRED_INTEGRITY = (
    "pre_outcome_decision_seal_coverage_equal_one",
    "post_cutoff_evidence_violation_count_equal_zero",
    "semantic_drift_count_equal_zero",
    "unauthorized_authority_event_count_equal_zero",
    "same_release_reproduction_mismatch_count_equal_zero",
    "paper_reconciliation_break_count_equal_zero",
    "correction_rewrite_count_equal_zero",
)

_REQUIRED_DESCRIPTIVE = (
    "trend_maoe_vs_always_neutral",
    "trend_coverage_and_abstention",
    "position_risk_activity",
    "aggregator_agreement_disagreement_and_action_distribution",
    "fixed_unit_paper_return_drawdown_turnover_exposure",
    "fill_reject_carry_and_honest_replay_divergence",
    "date_block_and_regime_slices",
)

_REQUIRED_STOPS = {
    "bound_semantic_digest_drift",
    "new_expert_or_stage_action_or_aggregator_or_p5_required",
    "dwd_publication_or_visibility_identity_unprovable",
    "post_cutoff_evidence_violation",
    "correction_rewrites_prior_evidence",
    "same_release_reproduction_semantic_mismatch",
    "shadow_to_order_or_paper_to_actual_execution_authority_leak",
    "paper_ledger_reconciliation_break",
    "sealed_partition_access_attempt",
    "notification_broker_or_live_capital_scope_expansion",
    "calendar_ceiling_expired_before_review_ready",
}


def _object(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        fail("field_mismatch", f"{path} must be an object")
    return value


def _array(value: object, path: str) -> Sequence[object]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        fail("field_mismatch", f"{path} must be an array")
    return value


def _exact(value: Mapping[str, Any], keys: set[str], path: str) -> None:
    missing = sorted(keys.difference(value))
    extra = sorted(set(value).difference(keys))
    if missing or extra:
        fail("field_mismatch", f"{path} keys differ; missing={missing}, extra={extra}")


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        fail("field_mismatch", f"{path} must be non-empty canonical text")
    return value


def _digest(value: object, path: str) -> str:
    result = _text(value, path)
    if _DIGEST.fullmatch(result) is None:
        fail("invalid_digest", f"{path} must be lowercase sha256")
    return result


def _sha40(value: object, path: str) -> str:
    result = _text(value, path)
    if _SHA40.fullmatch(result) is None:
        fail("invalid_revision", f"{path} must be an exact 40-character git revision")
    return result


def _positive_int(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        fail("field_mismatch", f"{path} must be a positive integer")
    return value


def _bool(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        fail("field_mismatch", f"{path} must be boolean")
    return value


def _unique_texts(value: object, path: str) -> tuple[str, ...]:
    result = tuple(_text(item, f"{path}[]") for item in _array(value, path))
    if not result or len(result) != len(set(result)):
        fail("field_mismatch", f"{path} must contain unique non-empty text values")
    return result


def _sha_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _repo_file(root: Path, value: object, path: str) -> Path:
    relative = Path(_text(value, path))
    if relative.is_absolute() or ".." in relative.parts:
        fail("path_escape", f"{path} must be repository-relative")
    resolved_root = root.resolve()
    resolved = (resolved_root / relative).resolve()
    if resolved_root not in resolved.parents or not resolved.is_file():
        fail("binding_missing", f"{path} does not resolve to a repository file")
    return resolved


def _load_json(path: Path) -> Mapping[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ForwardValidationContractError("binding_invalid_json", str(path)) from exc
    return _object(raw, str(path))


def _validate_bindings(raw: Mapping[str, Any], root: Path) -> dict[str, Path]:
    bindings = _object(raw["bindings"], "bindings")
    _exact(bindings, _BINDING_NAMES, "bindings")
    resolved: dict[str, Path] = {}
    for name in sorted(_BINDING_NAMES):
        binding = _object(bindings[name], f"bindings.{name}")
        _exact(binding, {"path", "sha256"}, f"bindings.{name}")
        path = _repo_file(root, binding["path"], f"bindings.{name}.path")
        if _sha_file(path) != _digest(binding["sha256"], f"bindings.{name}.sha256"):
            fail("binding_digest_drift", f"bindings.{name} content changed")
        resolved[name] = path
    return resolved


def _validate_program_intent(raw: Mapping[str, Any]) -> None:
    intent = _object(raw["program_intent"], "program_intent")
    _exact(
        intent,
        {
            "mode",
            "primary_question",
            "complete_expert_universe_claim",
            "complete_stage_taxonomy_claim",
            "profitability_is_primary_gate",
            "stage10_11_role",
        },
        "program_intent",
    )
    if _text(intent["mode"], "program_intent.mode") != "prospective_mechanism_mvp":
        fail("scope_drift", "P6-GOV-001 must remain a prospective mechanism MVP")
    _text(intent["primary_question"], "program_intent.primary_question")
    if any(
        _bool(intent[name], f"program_intent.{name}")
        for name in (
            "complete_expert_universe_claim",
            "complete_stage_taxonomy_claim",
            "profitability_is_primary_gate",
        )
    ):
        fail("claim_ceiling_violation", "completeness/profitability claims are not authorized")
    if intent["stage10_11_role"] != "development_advisory_not_action_path":
        fail("stage_authority_drift", "Stage10/11 may not enter the P6 action path")


def _validate_formal_strategy(raw: Mapping[str, Any], bound: dict[str, Path]) -> None:
    formal = _object(raw["formal_strategy"], "formal_strategy")
    _exact(
        formal,
        {
            "strategy_id",
            "strategy_version",
            "strategy_digest",
            "reference_runtime_code_revision",
            "acceptance_packet_digest",
            "lifecycle_decision_digest",
            "prior_lifecycle_decision",
            "prior_shadow_status",
            "experts",
        },
        "formal_strategy",
    )
    expected = (
        "candidate.p4-trend-risk",
        "1.0.1",
        "sha256:805ce33024ae82dc46a685c3ad7425528702e6a089ee4d09f27af2793d55ed0c",
    )
    observed = (
        _text(formal["strategy_id"], "formal_strategy.strategy_id"),
        _text(formal["strategy_version"], "formal_strategy.strategy_version"),
        _digest(formal["strategy_digest"], "formal_strategy.strategy_digest"),
    )
    if observed != expected:
        fail("strategy_drift", "entry proposal must bind candidate.p4-trend-risk@1.0.1")
    if _sha40(formal["reference_runtime_code_revision"], "formal_strategy.reference_runtime_code_revision") != "259a6495a14ca25eed65057190a0487f8c46afc6":
        fail("strategy_drift", "reference runtime must be the accepted full-attribution revision")
    if formal["prior_lifecycle_decision"] != "inconclusive" or formal["prior_shadow_status"] != "disabled":
        fail("lifecycle_incompatible", "accepted P3 lifecycle must remain inconclusive + disabled")

    experts = tuple(_object(item, "formal_strategy.experts[]") for item in _array(formal["experts"], "formal_strategy.experts"))
    if len(experts) != 2:
        fail("expert_topology_drift", "first P6 proposal must bind exactly two active experts")
    expected_experts = {
        "expert.trend-stability": (
            "1.0.0",
            "sha256:d6f8cdc64042ee6795fda6979e08ca98c380a33e53664f8bd0d321d775669723",
            "sha256:8bc371a80f3925451f8139b0af0f86e6542ae0abef589277f17751fef43a9be5",
        ),
        "expert.position-risk-review": (
            "1.0.0",
            "sha256:634d9bed8ed158d6267bf648248e9de270be26d4fb51d2a88a3dbf0bc4066002",
            "sha256:b8665cea0dda4c6a9d7949a94ff0b2c030ef6f5321aa2564e09ed7b52fbde8cf",
        ),
    }
    seen: set[str] = set()
    for index, expert in enumerate(experts):
        _exact(expert, {"expert_id", "expert_version", "evaluation_spec_digest", "policy_mapping_digest", "role"}, f"formal_strategy.experts[{index}]")
        expert_id = _text(expert["expert_id"], f"formal_strategy.experts[{index}].expert_id")
        if expert_id not in expected_experts or expert_id in seen:
            fail("expert_topology_drift", f"unexpected or duplicate expert {expert_id}")
        seen.add(expert_id)
        expected_version, expected_spec, expected_policy = expected_experts[expert_id]
        if (
            expert["expert_version"] != expected_version
            or _digest(expert["evaluation_spec_digest"], f"formal_strategy.experts[{index}].evaluation_spec_digest") != expected_spec
            or _digest(expert["policy_mapping_digest"], f"formal_strategy.experts[{index}].policy_mapping_digest") != expected_policy
            or expert["role"] != "active"
        ):
            fail("expert_topology_drift", f"expert binding changed for {expert_id}")

    strategy = _load_json(bound["strategy_card"])
    if (
        strategy.get("strategy_id") != expected[0]
        or strategy.get("strategy_version") != expected[1]
        or strategy.get("semantic_digest") != expected[2]
    ):
        fail("binding_semantic_drift", "strategy-card semantic identity differs")
    lifecycle = _load_json(bound["lifecycle_decision"])
    acceptance = _load_json(bound["acceptance_packet"])
    if lifecycle.get("digest") != formal["lifecycle_decision_digest"]:
        fail("binding_semantic_drift", "lifecycle decision digest differs")
    if lifecycle.get("decision") != "inconclusive" or _object(lifecycle.get("shadow_disposition"), "lifecycle.shadow_disposition").get("status") != "disabled":
        fail("lifecycle_incompatible", "bound lifecycle bytes are not inconclusive + disabled")
    if acceptance.get("digest") != formal["acceptance_packet_digest"]:
        fail("binding_semantic_drift", "acceptance packet digest differs")
    accepted_strategy = _object(acceptance.get("strategy"), "acceptance.strategy")
    if accepted_strategy.get("strategy_digest") != expected[2]:
        fail("binding_semantic_drift", "acceptance packet binds a different strategy")


def _validate_target(raw: Mapping[str, Any], bound: dict[str, Path]) -> None:
    target = _object(raw["target_settlement"], "target_settlement")
    _exact(
        target,
        {
            "target_decision_digest",
            "target_id",
            "target_version",
            "target_digest",
            "forecast_spec_digest",
            "resolver_id",
            "resolver_version",
            "resolver_digest",
            "scorer_id",
            "scorer_version",
            "scorer_digest",
            "horizon_trading_sessions",
        },
        "target_settlement",
    )
    expected = {
        "target_decision_digest": "sha256:a67accc8688b2a63ef01afff160666119b72b5c0ec4adc421d266104e76087f5",
        "target_id": "p4-event.outcome-first-passage",
        "target_version": "1.0.0",
        "target_digest": "sha256:b623a647116a69a0d6559bd265b51bdd3ac0654826cf54ba38b87d819ea165ca",
        "forecast_spec_digest": "sha256:a94b0ff6253ea2dfb9c0bf4701e26c973d43037c74340bad8891cc1368a1d56a",
        "resolver_id": "resolver.p4-label-corpus-adapter",
        "resolver_version": "1.0.0",
        "resolver_digest": "sha256:32246f4f0d5429dbd951e49d69c9548f8ea2f1ae55cc6b7c138258ea8b758e70",
        "scorer_id": "scorer.mean-absolute-ordinal-error",
        "scorer_version": "1.0.0",
        "scorer_digest": "sha256:f571de6f68e30376135e7bbe50da19b50f0fcc1c28878a094deebeec3f861313",
        "horizon_trading_sessions": 20,
    }
    for key, expected_value in expected.items():
        if target.get(key) != expected_value:
            fail("target_drift", f"target_settlement.{key} differs")

    decision = _load_json(bound["target_decision"])
    forecast = _load_json(bound["forecast_spec"])
    if decision.get("semantic_digest") != expected["target_decision_digest"]:
        fail("binding_semantic_drift", "target decision digest differs")
    if forecast.get("digest") != expected["forecast_spec_digest"]:
        fail("binding_semantic_drift", "forecast spec digest differs")
    if forecast.get("horizon_trading_sessions") != 20:
        fail("target_drift", "forecast horizon differs")
    if _object(forecast.get("target"), "forecast.target").get("digest") != expected["target_digest"]:
        fail("target_drift", "forecast target differs")
    if _object(forecast.get("resolver"), "forecast.resolver").get("digest") != expected["resolver_digest"]:
        fail("target_drift", "forecast resolver differs")
    if _object(forecast.get("scorer"), "forecast.scorer").get("digest") != expected["scorer_digest"]:
        fail("target_drift", "forecast scorer differs")

    trend_spec = _load_json(bound["trend_evaluation_spec"])
    position_spec = _load_json(bound["position_risk_evaluation_spec"])
    if trend_spec.get("digest") != "sha256:d6f8cdc64042ee6795fda6979e08ca98c380a33e53664f8bd0d321d775669723":
        fail("binding_semantic_drift", "trend evaluation spec differs")
    if position_spec.get("digest") != "sha256:634d9bed8ed158d6267bf648248e9de270be26d4fb51d2a88a3dbf0bc4066002":
        fail("binding_semantic_drift", "position-risk evaluation spec differs")


def _validate_aggregation_and_execution(raw: Mapping[str, Any], bound: dict[str, Path]) -> None:
    aggregation = _object(raw["aggregation"], "aggregation")
    _exact(aggregation, {"policy_id", "policy_version", "sequence", "scoring_expert_ids", "minimum_entry_score", "change_authorized"}, "aggregation")
    expected_sequence = (
        "strategy_and_system_veto",
        "constraint_intersection",
        "equal_weight_confidence_adjusted_score",
        "action_mapping",
        "hard_position_funding_accounting",
    )
    if (
        aggregation["policy_id"] != "aggregation.p4-trend-risk"
        or aggregation["policy_version"] != "1.0.0"
        or tuple(_unique_texts(aggregation["sequence"], "aggregation.sequence")) != expected_sequence
        or tuple(_unique_texts(aggregation["scoring_expert_ids"], "aggregation.scoring_expert_ids")) != ("expert.trend-stability",)
        or aggregation["minimum_entry_score"] != "0.50"
        or _bool(aggregation["change_authorized"], "aggregation.change_authorized")
    ):
        fail("aggregation_drift", "accepted aggregation policy must remain unchanged")

    execution = _object(raw["position_and_execution"], "position_and_execution")
    _exact(
        execution,
        {
            "p0_policy_digest",
            "p2_execution_policy_digest",
            "execution_profile",
            "position_state_machine_version",
            "unit_count",
            "unit_notional_cny",
            "compound_capital",
            "profit_redeployment",
            "paper_only",
        },
        "position_and_execution",
    )
    expected_execution = {
        "p0_policy_digest": "sha256:2a0411f41db50a85de71642a95c5f595f7b7e3f1c2ede09d098b7d413d8d652c",
        "p2_execution_policy_digest": "sha256:7617a62ceb5baf549c69525222d67503289f5cfdecc2c3e62151b220a90904c3",
        "execution_profile": "close-to-next-open-v1",
        "position_state_machine_version": "1.1.0",
        "unit_count": 5,
        "unit_notional_cny": "100000.00",
        "compound_capital": False,
        "profit_redeployment": False,
        "paper_only": True,
    }
    if dict(execution) != expected_execution:
        fail("execution_policy_drift", "fixed-unit P2 execution policy differs")

    p0 = _load_json(bound["p0_policy"])
    p2 = _load_json(bound["p2_execution_policy"])
    if p0.get("policy_version") != "p0-2026-07-23" or p0.get("status") != "frozen":
        fail("binding_semantic_drift", "P0 policy is not the frozen accepted policy")
    if p2.get("policy_version") != "p2-daily-research-v1" or p2.get("status") != "frozen":
        fail("binding_semantic_drift", "P2 execution policy is not frozen")
    profiles = tuple(_object(item, "p2.profiles[]") for item in _array(p2.get("profiles"), "p2.profiles"))
    if not any(item.get("profile_id") == "close-to-next-open-v1" and item.get("active") is True for item in profiles):
        fail("binding_semantic_drift", "active close-to-next-open-v1 profile missing")


def _validate_dwd_and_activation(raw: Mapping[str, Any]) -> None:
    dwd = _object(raw["dwd_authority"], "dwd_authority")
    _exact(
        dwd,
        {
            "historical_reference_base_id",
            "historical_reference_base_digest",
            "historical_reference_correction_id",
            "historical_reference_correction_digest",
            "forward_release_contract",
            "future_release_identity_bound_per_cycle",
            "pre_activation_2026_disposition",
            "late_correction_policy",
        },
        "dwd_authority",
    )
    if (
        dwd["historical_reference_base_id"] != "dwd-base-202607-v2.1"
        or dwd["historical_reference_base_digest"] != "sha256:b689a21b2052ca722af35faeb72cb68850b54d00ddb062b3694e443ba29c32b9"
        or dwd["historical_reference_correction_id"] != "dwd-base-202607-v2.1-correction-20260813-001"
        or dwd["historical_reference_correction_digest"] != "sha256:1c1d1f4ea906797255fbc71cc3915bb905e51e7aea202d27042e33692b72e9da"
        or dwd["forward_release_contract"] != "t-close-plus-5h-v1_ready_daily_dwd_release"
        or _bool(dwd["future_release_identity_bound_per_cycle"], "dwd_authority.future_release_identity_bound_per_cycle") is not True
        or dwd["pre_activation_2026_disposition"] != "engineering_rehearsal_only_not_budget"
        or dwd["late_correction_policy"] != "append_disposition_never_rewrite_observation"
    ):
        fail("dwd_authority_drift", "prospective DWD authority differs")

    activation = _object(raw["activation_boundary"], "activation_boundary")
    _exact(
        activation,
        {
            "proposal_acceptance_activates",
            "activation_owner",
            "first_official_market_date_rule",
            "required_release_state",
            "timezone",
        },
        "activation_boundary",
    )
    if (
        _bool(activation["proposal_acceptance_activates"], "activation_boundary.proposal_acceptance_activates")
        or activation["activation_owner"] != "P6-ACTIVATE-001"
        or activation["first_official_market_date_rule"] != "first_complete_trading_session_strictly_after_activation_decision_market_date"
        or activation["required_release_state"] != "READY_AND_DEEPLY_VERIFIABLE"
        or activation["timezone"] != "Asia/Shanghai"
    ):
        fail("activation_authority_drift", "P6-GOV-001 may not activate prospective evidence")


def _validate_budget_and_metrics(raw: Mapping[str, Any]) -> None:
    budget = _object(raw["observation_budget"], "observation_budget")
    _exact(
        budget,
        {
            "independent_unit",
            "collection_block_count",
            "sessions_per_block",
            "collection_trading_sessions",
            "target_maturity_tail_trading_sessions",
            "maximum_calendar_days",
            "cross_section_increases_independent_progress",
            "post_result_extension_authorized",
            "low_activity_disposition",
        },
        "observation_budget",
    )
    if (
        budget["independent_unit"] != "non_overlapping_22_trading_session_market_block"
        or _positive_int(budget["collection_block_count"], "observation_budget.collection_block_count") != 3
        or _positive_int(budget["sessions_per_block"], "observation_budget.sessions_per_block") != 22
        or _positive_int(budget["collection_trading_sessions"], "observation_budget.collection_trading_sessions") != 66
        or _positive_int(budget["target_maturity_tail_trading_sessions"], "observation_budget.target_maturity_tail_trading_sessions") != 20
        or _positive_int(budget["maximum_calendar_days"], "observation_budget.maximum_calendar_days") != 150
        or _bool(budget["cross_section_increases_independent_progress"], "observation_budget.cross_section_increases_independent_progress")
        or _bool(budget["post_result_extension_authorized"], "observation_budget.post_result_extension_authorized")
        or budget["low_activity_disposition"] != "inconclusive_low_activity_no_budget_extension"
    ):
        fail("budget_drift", "prospective MVP observation budget differs")
    if budget["collection_block_count"] * budget["sessions_per_block"] != budget["collection_trading_sessions"]:
        fail("budget_inconsistent", "collection blocks do not equal collection sessions")

    metrics = _object(raw["metric_precommitment"], "metric_precommitment")
    _exact(
        metrics,
        {
            "primary_gate_id",
            "primary_gate_kind",
            "economic_performance_is_primary_gate",
            "required_integrity_conditions",
            "descriptive_metrics",
        },
        "metric_precommitment",
    )
    if (
        metrics["primary_gate_id"] != "mechanism_integrity_conjunction_v1"
        or metrics["primary_gate_kind"] != "all_required_integrity_conditions"
        or _bool(metrics["economic_performance_is_primary_gate"], "metric_precommitment.economic_performance_is_primary_gate")
        or tuple(_unique_texts(metrics["required_integrity_conditions"], "metric_precommitment.required_integrity_conditions")) != _REQUIRED_INTEGRITY
        or tuple(_unique_texts(metrics["descriptive_metrics"], "metric_precommitment.descriptive_metrics")) != _REQUIRED_DESCRIPTIVE
    ):
        fail("metric_drift", "mechanism-MVP metric precommitment differs")


def _validate_expected_stops_rollback_authority(raw: Mapping[str, Any]) -> None:
    expected_behavior = _unique_texts(raw["expected_behavior"], "expected_behavior")
    required_behavior = {
        "trend_expert_keeps_frozen_four_state_or_abstain_semantics",
        "position_risk_expert_never_gains_positive_entry_authority",
        "system_guardians_remain_non_intervenable",
        "accepted_aggregator_precedence_remains_unchanged",
        "shadow_and_paper_share_decision_content_but_not_authority",
        "weak_or_negative_economics_are_valid_evidence_not_a_tuning_trigger",
    }
    if not required_behavior.issubset(expected_behavior):
        fail("expected_behavior_incomplete", "required mechanism-MVP behavior is missing")

    stops = set(_unique_texts(raw["stop_conditions"], "stop_conditions"))
    if stops != _REQUIRED_STOPS:
        fail("stop_condition_drift", "stop-condition set differs from the frozen proposal")

    rollback = _object(raw["rollback"], "rollback")
    _exact(rollback, {"action", "preserve", "repair_policy"}, "rollback")
    if rollback["action"] != "disable_p6_admission_binding_and_reject_new_submissions":
        fail("rollback_drift", "rollback action differs")
    preserve = set(_unique_texts(rollback["preserve"], "rollback.preserve"))
    if preserve != {"entry_proposal", "activation_decisions", "runs", "observations", "settlements", "incidents", "paper_ledgers", "historical_p3_p4_evidence"}:
        fail("rollback_drift", "rollback preservation set differs")
    if rollback["repair_policy"] != "supersede_with_new_version_never_mutate_published_evidence":
        fail("rollback_drift", "repair policy differs")

    authority = _object(raw["authority"], "authority")
    _exact(authority, _AUTHORITY_KEYS, "authority")
    for key in sorted(_AUTHORITY_KEYS):
        value = _bool(authority[key], f"authority.{key}")
        if key == "entry_proposal_publication_authorized":
            if value is not True:
                fail("authority_drift", "entry-proposal publication must be authorized")
        elif value is not False:
            fail("authority_drift", f"{key} must remain false")

    governance = _object(raw["governance"], "governance")
    _exact(
        governance,
        {
            "approval_state",
            "approval_authority",
            "decision_reference",
            "automatic_successor",
            "required_next_decision",
        },
        "governance",
    )
    if (
        governance["approval_state"] != "owner_approved"
        or governance["approval_authority"] != "human"
        or governance["decision_reference"] != "docs/reviews/p6-gov001-prospective-mvp-entry-review-20260917.md"
        or governance["automatic_successor"] is not None
        or governance["required_next_decision"] != "explicit_owner_value_review_before_p6_data_001"
    ):
        fail("governance_drift", "post-entry owner checkpoint is not preserved")


def load_entry_proposal(path: Path, *, root: Path | None = None) -> P6EntryProposal:
    """Load and deeply validate the one frozen P6-GOV-001 proposal."""

    repository_root = (root or find_project_root()).resolve()
    proposal_path = path.resolve()
    if repository_root not in proposal_path.parents or not proposal_path.is_file():
        fail("path_escape", "proposal path must be an existing repository file")
    raw = _load_json(proposal_path)
    _exact(
        raw,
        {
            "schema_version",
            "proposal_id",
            "work_item",
            "state",
            "program_intent",
            "bindings",
            "formal_strategy",
            "target_settlement",
            "aggregation",
            "position_and_execution",
            "dwd_authority",
            "activation_boundary",
            "observation_budget",
            "metric_precommitment",
            "expected_behavior",
            "stop_conditions",
            "rollback",
            "authority",
            "governance",
            "proposal_digest",
        },
        "entry_proposal",
    )
    if raw["schema_version"] != _SCHEMA or raw["proposal_id"] != _PROPOSAL_ID or raw["work_item"] != "P6-GOV-001" or raw["state"] != _STATE:
        fail("proposal_identity_drift", "P6 entry proposal identity differs")

    bound = _validate_bindings(raw, repository_root)
    _validate_program_intent(raw)
    _validate_formal_strategy(raw, bound)
    _validate_target(raw, bound)
    _validate_aggregation_and_execution(raw, bound)
    _validate_dwd_and_activation(raw)
    _validate_budget_and_metrics(raw)
    _validate_expected_stops_rollback_authority(raw)

    declared = _digest(raw["proposal_digest"], "proposal_digest")
    computed = proposal_digest(raw)
    if declared != computed:
        fail("proposal_digest_mismatch", f"declared {declared} != computed {computed}")
    return P6EntryProposal(path=proposal_path, payload=raw, proposal_digest=declared)

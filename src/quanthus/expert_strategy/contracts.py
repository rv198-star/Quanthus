"""Outcome-blind contract for the first formal two-expert strategy card."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any, NoReturn

from quanthus.event_library.rule_card import calculate_candidate_rule_card_digest
from quanthus.event_library.splits import calculate_split_policy_digest
from quanthus.expert_evaluation import ExpertEvaluationSpec, artifact_from_bytes
from quanthus.expert_evaluation.experts.trend_stability import load_trend_stability_config
from quanthus.expert_evaluation.serialization import ExpertEvaluationError, digest_value
from quanthus.expert_evaluation.target_cards import load_target_decision_card

_SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.-]{2,127}$")
_ZERO_DIGEST = f"sha256:{'0' * 64}"


def _error(reason: str, detail: str) -> NoReturn:
    raise ExpertEvaluationError(reason, detail)


def _object(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        _error("field_mismatch", f"{path} must be an object")
    return value


def _array(value: object, path: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        _error("field_mismatch", f"{path} must be an array")
    return value


def _exact(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    missing = sorted(expected.difference(value))
    unexpected = sorted(set(value).difference(expected))
    if missing or unexpected:
        _error(
            "field_mismatch",
            f"{path} keys do not match contract; missing={missing}, unexpected={unexpected}",
        )


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        _error("field_mismatch", f"{path} must be non-empty canonical text")
    return value


def _identifier(value: object, path: str) -> str:
    result = _text(value, path)
    if _IDENTIFIER.fullmatch(result) is None:
        _error("invalid_identity", f"{path} must be a stable lowercase identifier")
    return result


def _version(value: object, path: str) -> str:
    result = _text(value, path)
    if _SEMVER.fullmatch(result) is None:
        _error("invalid_identity", f"{path} must use MAJOR.MINOR.PATCH")
    return result


def _digest(value: object, path: str, *, allow_zero: bool = False) -> str:
    result = _text(value, path)
    if _DIGEST.fullmatch(result) is None or (not allow_zero and result == _ZERO_DIGEST):
        _error("invalid_identity", f"{path} must be a nonzero lowercase sha256")
    return result


def _date(value: object, path: str) -> date:
    try:
        result = date.fromisoformat(_text(value, path))
    except ValueError as exc:
        raise ExpertEvaluationError("field_mismatch", f"{path} must be an ISO date") from exc
    if result.isoformat() != value:
        _error("field_mismatch", f"{path} must be a canonical ISO date")
    return result


def _positive_int(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        _error("field_mismatch", f"{path} must be a positive integer")
    return value


def _decimal(value: object, path: str, minimum: str, maximum: str) -> Decimal:
    text = _text(value, path)
    try:
        result = Decimal(text)
    except Exception as exc:
        raise ExpertEvaluationError("invalid_decimal", f"{path} must be decimal text") from exc
    if not result.is_finite() or not Decimal(minimum) <= result <= Decimal(maximum):
        _error("invalid_decimal", f"{path} is outside [{minimum}, {maximum}]")
    return result


def _unique_texts(value: object, path: str, *, nonempty: bool = True) -> tuple[str, ...]:
    result = tuple(_text(item, f"{path}[]") for item in _array(value, path))
    if (nonempty and not result) or len(result) != len(set(result)):
        _error("field_mismatch", f"{path} must contain unique values")
    return result


def _plain(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_plain(item) for item in value]
    _error("unsupported_canonical_value", "strategy card contains a non-JSON value")


def _freeze(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in sorted(value.items())})
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(_freeze(item) for item in value)
    _error("unsupported_canonical_value", "strategy card contains a non-JSON value")


def _repository_path(root: Path, key: object, path: str) -> Path:
    value = Path(_text(key, path))
    if value.is_absolute() or ".." in value.parts or str(value).startswith("."):
        _error("path_escape", f"{path} must be repository-relative")
    resolved_root = root.resolve()
    resolved = (resolved_root / value).resolve()
    if resolved_root not in resolved.parents or not resolved.is_file():
        _error("registry_artifact_missing", f"{path} does not resolve to a file")
    return resolved


def _mapping_digest(mapping: Mapping[str, Any]) -> str:
    return digest_value({key: value for key, value in mapping.items() if key != "mapping_digest"})


def _position_expert_digest(expert: Mapping[str, Any]) -> str:
    return digest_value(
        {key: value for key, value in expert.items() if key != "expert_semantic_digest"}
    )


@dataclass(frozen=True)
class ExpertStrategyCard:
    path: Path
    strategy_id: str
    strategy_version: str
    lifecycle: str
    owner: str
    semantic_sections: Mapping[str, Any]
    governance: Mapping[str, Any]
    semantic_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "semantic_sections", _freeze(self.semantic_sections))
        object.__setattr__(self, "governance", _freeze(self.governance))

    @property
    def computed_semantic_digest(self) -> str:
        return digest_value(_plain(self.semantic_sections))

    @property
    def experts(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(_object(item, "experts[]") for item in self.semantic_sections["experts"])

    @property
    def interventions(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(
            _object(item, "interventions[]") for item in self.semantic_sections["interventions"]
        )

    def require_implementation_authority(self) -> None:
        if self.lifecycle != "approved" or self.governance["approval_state"] != "approved":
            _error(
                "strategy_card_not_approved",
                "implementation, Worker submission, and result access require exact human approval",
            )


_TOP_LEVEL = {
    "schema_version",
    "strategy_id",
    "strategy_version",
    "lifecycle",
    "owner",
    "hypothesis",
    "product_candidate",
    "population",
    "data",
    "experts",
    "shadow_experts",
    "aggregation",
    "position_and_execution",
    "interventions",
    "attribution_questions",
    "uncertainty",
    "resource_budget",
    "stop_conditions",
    "rollback",
    "non_claims",
    "governance",
    "semantic_digest",
}
_SEMANTIC_EXCLUDES = {"lifecycle", "owner", "governance", "semantic_digest"}


def _validate_reference(value: object, path: str) -> Mapping[str, Any]:
    ref = _object(value, path)
    _exact(ref, {"artifact_id", "artifact_version", "digest", "repository_key"}, path)
    _identifier(ref["artifact_id"], f"{path}.artifact_id")
    _version(ref["artifact_version"], f"{path}.artifact_version")
    _digest(ref["digest"], f"{path}.digest")
    _text(ref["repository_key"], f"{path}.repository_key")
    return ref


def _validate_population(raw: Mapping[str, Any], root: Path) -> None:
    population = _object(raw["population"], "population")
    _exact(
        population,
        {
            "candidate_generator",
            "eligible_universe",
            "assessment_population",
            "split",
            "required_partition",
            "applicability_start",
            "applicability_end",
            "last_entry_decision",
            "last_execution_date",
            "match_rule",
        },
        "population",
    )
    generator = _validate_reference(population["candidate_generator"], "candidate_generator")
    universe = _object(population["eligible_universe"], "eligible_universe")
    _exact(
        universe,
        {"artifact_id", "artifact_version", "digest", "repository_key", "selector"},
        "eligible_universe",
    )
    _identifier(universe["artifact_id"], "eligible_universe.artifact_id")
    _version(universe["artifact_version"], "eligible_universe.artifact_version")
    _digest(universe["digest"], "eligible_universe.digest")
    if universe["selector"] != "candidate_rule_eligibility":
        _error("field_mismatch", "eligible universe selector differs")
    assessment = _validate_reference(
        population["assessment_population"], "assessment_population"
    )
    split = _validate_reference(population["split"], "split")
    start = _date(population["applicability_start"], "applicability_start")
    end = _date(population["applicability_end"], "applicability_end")
    entry_end = _date(population["last_entry_decision"], "last_entry_decision")
    execution_end = _date(population["last_execution_date"], "last_execution_date")
    if (
        population["required_partition"] != "development"
        or start.isoformat() != "2020-01-01"
        or end.isoformat() != "2021-12-31"
        or not (entry_end < execution_end <= end)
    ):
        _error(
            "sealed_partition_access",
            "formal strategy population exceeds development authority",
        )
    generator_path = _repository_path(root, generator["repository_key"], "candidate_generator")
    observed_generator = calculate_candidate_rule_card_digest(generator_path)
    if generator["digest"] != observed_generator or universe["digest"] != observed_generator:
        _error("strategy_reference_mismatch", "candidate generator or universe drifted")
    split_path = _repository_path(root, split["repository_key"], "split")
    if split["digest"] != calculate_split_policy_digest(split_path):
        _error("strategy_reference_mismatch", "split policy drifted")
    target_path = _repository_path(root, assessment["repository_key"], "assessment_population")
    target = load_target_decision_card(target_path, root)
    target.require_result_access_authority()
    target_population = target.eligible_population
    if (
        assessment["artifact_id"],
        assessment["artifact_version"],
        assessment["digest"],
        start,
        end,
    ) != (
        target_population.predicate_id,
        target_population.predicate_version,
        target_population.predicate_digest,
        target_population.evaluation_start,
        target_population.evaluation_end,
    ):
        _error("assessment_population_mismatch", "strategy applicability differs from assessment")


def _validate_experts(raw: Mapping[str, Any], root: Path) -> None:
    experts = tuple(_object(item, "experts[]") for item in _array(raw["experts"], "experts"))
    if len(experts) != 2:
        _error("topology_mismatch", "formal topology requires exactly two active experts")
    identities = tuple((item.get("expert_id"), item.get("expert_version")) for item in experts)
    if identities != (
        ("expert.trend-stability", "1.0.0"),
        ("expert.position-risk-review", "1.0.0"),
    ) or any(item.get("role") != "active" for item in experts):
        _error("topology_mismatch", "formal expert order or identity differs")
    if tuple(_array(raw["shadow_experts"], "shadow_experts")):
        _error("topology_mismatch", "first formal strategy has no Shadow-only expert")

    trend = experts[0]
    _exact(
        trend,
        {
            "expert_id",
            "expert_version",
            "role",
            "route",
            "decision_surfaces",
            "assessment_digest",
            "assessment_repository_key",
            "evaluation_spec_digest",
            "evaluation_spec_repository_key",
            "accepted_run_manifest_digest",
            "policy_mapping",
            "fallback",
        },
        "trend expert",
    )
    if trend["route"] != "future_target_assessment" or tuple(
        _unique_texts(trend["decision_surfaces"], "trend decision surfaces")
    ) != ("entry_scoring",):
        _error("topology_mismatch", "trend expert action surface differs")
    trend_path = _repository_path(root, trend["assessment_repository_key"], "trend assessment")
    config = load_trend_stability_config(trend_path)
    if trend["assessment_digest"] != config.semantic_digest:
        _error("strategy_reference_mismatch", "trend assessment drifted")
    spec_path = _repository_path(root, trend["evaluation_spec_repository_key"], "trend spec")
    spec = artifact_from_bytes(spec_path.read_bytes())
    if not isinstance(spec, ExpertEvaluationSpec):
        _error("strategy_reference_mismatch", "trend evaluation reference is not a spec")
    if trend["evaluation_spec_digest"] != spec.digest:
        _error("strategy_reference_mismatch", "trend evaluation spec drifted")
    _digest(trend["accepted_run_manifest_digest"], "accepted_run_manifest_digest")
    trend_mapping = _object(trend["policy_mapping"], "trend policy mapping")
    _exact(
        trend_mapping,
        {"mapping_id", "mapping_version", "effect_kinds", "state_effects", "mapping_digest"},
        "trend policy mapping",
    )
    if trend_mapping["mapping_digest"] != _mapping_digest(trend_mapping):
        _error("digest_mismatch", "trend policy mapping digest differs")
    _identifier(trend_mapping["mapping_id"], "trend mapping id")
    _version(trend_mapping["mapping_version"], "trend mapping version")
    if tuple(_unique_texts(trend_mapping["effect_kinds"], "trend effect kinds")) != (
        "scoring",
        "abstention",
    ):
        _error("field_mismatch", "trend mapping effects differ")
    state_effects = _object(trend_mapping["state_effects"], "trend state effects")
    _exact(state_effects, {"fragile", "neutral", "stable", "abstained"}, "trend states")
    for state in ("fragile", "neutral", "stable"):
        effect = _object(state_effects[state], f"trend state {state}")
        _exact(effect, {"signal", "confidence"}, f"trend state {state}")
        _decimal(effect["signal"], f"trend {state} signal", "-1", "1")
        _decimal(effect["confidence"], f"trend {state} confidence", "0", "1")
    abstained = _object(state_effects["abstained"], "trend abstained state")
    _exact(abstained, {"effect"}, "trend abstained state")
    if abstained["effect"] != "abstention":
        _error("field_mismatch", "trend missing evidence must remain abstention")
    trend_fallback = _object(trend["fallback"], "trend fallback")
    _exact(
        trend_fallback,
        {"fallback_id", "fallback_version", "effect", "entry_authority"},
        "trend fallback",
    )
    if trend_fallback["entry_authority"] != "none":
        _error("unsafe_fallback", "trend fallback cannot authorize entry")

    position = experts[1]
    _exact(
        position,
        {
            "expert_id",
            "expert_version",
            "role",
            "route",
            "decision_surfaces",
            "required_features",
            "required_position_facts",
            "entry_policy",
            "held_policy",
            "policy_mapping",
            "fallback",
            "expert_semantic_digest",
        },
        "position-risk expert",
    )
    if position["route"] != "policy_only" or tuple(
        _unique_texts(position["decision_surfaces"], "position decision surfaces")
    ) != ("entry_constraint", "held_reduce_review", "held_exit_review"):
        _error("topology_mismatch", "position-risk action surfaces differ")
    _unique_texts(position["required_features"], "position required features")
    _unique_texts(position["required_position_facts"], "position required facts")
    entry = _object(position["entry_policy"], "position entry policy")
    _exact(
        entry,
        {
            "maximum_realized_volatility20",
            "maximum_rsi14",
            "allowed_minimum_position",
            "allowed_maximum_position",
            "exceeded_effect",
            "missing_effect",
        },
        "position entry policy",
    )
    _decimal(entry["maximum_realized_volatility20"], "entry volatility", "0", "10")
    _decimal(entry["maximum_rsi14"], "entry RSI", "0", "100")
    minimum = _decimal(entry["allowed_minimum_position"], "entry minimum", "0", "1")
    maximum = _decimal(entry["allowed_maximum_position"], "entry maximum", "0", "1")
    if minimum > maximum or entry["exceeded_effect"] != "block_new":
        _error("field_mismatch", "position entry bounds or exceeded effect differ")
    held = _object(position["held_policy"], "position held policy")
    _exact(
        held,
        {
            "minimum_holding_sessions_for_reduce",
            "reduce_marked_return_at_or_below",
            "reduce_realized_volatility20_above",
            "reduce_rsi14_above",
            "reduce_target_maximum_position",
            "maximum_strategy_reductions",
            "exit_realized_volatility20_above",
            "exit_rsi14_above",
            "exit_combination",
            "missing_effect",
        },
        "position held policy",
    )
    _positive_int(held["minimum_holding_sessions_for_reduce"], "minimum reduce hold")
    _positive_int(held["maximum_strategy_reductions"], "maximum strategy reductions")
    _decimal(held["reduce_marked_return_at_or_below"], "reduce return", "-1", "1")
    _decimal(held["reduce_realized_volatility20_above"], "reduce volatility", "0", "10")
    _decimal(held["reduce_rsi14_above"], "reduce RSI", "0", "100")
    _decimal(held["reduce_target_maximum_position"], "reduce target", "0", "1")
    _decimal(held["exit_realized_volatility20_above"], "exit volatility", "0", "10")
    _decimal(held["exit_rsi14_above"], "exit RSI", "0", "100")
    if held["exit_combination"] != "all":
        _error("field_mismatch", "position-risk exit requires all extreme conditions")
    mapping = _object(position["policy_mapping"], "position policy mapping")
    _exact(
        mapping,
        {"mapping_id", "mapping_version", "effect_kinds", "precedence", "mapping_digest"},
        "position policy mapping",
    )
    if mapping["mapping_digest"] != _mapping_digest(mapping):
        _error("digest_mismatch", "position policy mapping digest differs")
    if position["expert_semantic_digest"] != _position_expert_digest(position):
        _error("digest_mismatch", "position-risk expert digest differs")
    fallback = _object(position["fallback"], "position fallback")
    _exact(
        fallback,
        {"fallback_id", "fallback_version", "effect", "base_position_management"},
        "position fallback",
    )
    if fallback["base_position_management"] != "preserved":
        _error("unsafe_fallback", "position fallback must preserve base management")


def _validate_topology(raw: Mapping[str, Any], root: Path) -> None:
    product = _object(raw["product_candidate"], "product_candidate")
    _exact(
        product,
        {"strategy_id", "strategy_version", "catalog_kind", "publication_state"},
        "product_candidate",
    )
    if (product["strategy_id"], product["strategy_version"]) != (
        raw["strategy_id"],
        raw["strategy_version"],
    ) or raw["strategy_id"] == "candidate.market-breadth-trend-breakout":
        _error("fixture_lineage_forbidden", "formal strategy must own a new product identity")
    if product["publication_state"] != "reserved_pending_trusted_evidence":
        _error("field_mismatch", "draft candidate must not be published")
    _validate_population(raw, root)
    _validate_experts(raw, root)

    aggregation = _object(raw["aggregation"], "aggregation")
    _exact(
        aggregation,
        {
            "policy_id",
            "policy_version",
            "sequence",
            "scoring_expert_ids",
            "minimum_entry_score",
            "all_scoring_abstained_action",
            "candidate_order",
            "shadow_order_authority",
        },
        "aggregation",
    )
    if tuple(aggregation.get("scoring_expert_ids", ())) != ("expert.trend-stability",):
        _error("topology_mismatch", "trend expert must be the sole positive scorer")
    if aggregation.get("all_scoring_abstained_action") != "block_new":
        _error("unsafe_fallback", "all-scoring abstention must block new entries")
    if aggregation.get("shadow_order_authority") != "forbidden":
        _error("shadow_authority", "Shadow output cannot link an order")
    _decimal(aggregation.get("minimum_entry_score"), "minimum_entry_score", "-1", "1")

    position = _object(raw["position_and_execution"], "position_and_execution")
    _exact(
        position,
        {
            "p0_policy",
            "execution_policy",
            "execution_profile",
            "position_state_machine_version",
            "unit_count",
            "unit_notional",
            "base_trial_position_fraction",
            "entry_lot_size",
            "maximum_active_units",
            "adds_enabled",
            "reductions_enabled",
            "cooldown_sessions",
            "funding_policy",
            "matching_policy",
            "settlement_policy",
            "cost_policy",
            "base_position_management",
            "hard_guardians",
        },
        "position_and_execution",
    )
    guardians = _unique_texts(position.get("hard_guardians"), "hard_guardians")
    required_guardians = {
        "point_in_time",
        "data_quality",
        "tradability",
        "accounting",
        "funding",
        "hard_position_limits",
        "concentration",
    }
    if set(guardians) != required_guardians:
        _error("guardian_mismatch", "fixed system guardians differ")
    base = _object(position.get("base_position_management"), "base_position_management")
    _exact(
        base,
        {
            "intervenable",
            "structural_break_rule",
            "constraint_stop_loss",
            "deterioration_sessions",
            "minimum_holding_sessions",
            "maximum_holding_sessions",
            "missing_held_evidence",
        },
        "base_position_management",
    )
    if base.get("intervenable") is not False:
        _error("guardian_mismatch", "base position management must be non-intervenable")
    for key in ("p0_policy", "execution_policy"):
        ref = _validate_reference(position[key], key)
        reference_path = _repository_path(root, ref["repository_key"], key)
        observed = digest_value(json.loads(reference_path.read_text(encoding="utf-8")))
        if ref["digest"] != observed:
            _error("strategy_reference_mismatch", f"{key} drifted")
    data = _object(raw["data"], "data")
    _exact(
        data,
        {
            "provider",
            "dwd_package_id",
            "dwd_content_digest",
            "dwd_manifest_sha256",
            "correction_id",
            "correction_content_digest",
            "correction_manifest_sha256",
        },
        "data",
    )
    provider = _validate_reference(data["provider"], "data.provider")
    provider_path = _repository_path(root, provider["repository_key"], "data.provider")
    if provider["digest"] != digest_value(json.loads(provider_path.read_text(encoding="utf-8"))):
        _error("strategy_reference_mismatch", "DWD provider drifted")
    for field in (
        "dwd_content_digest",
        "dwd_manifest_sha256",
        "correction_content_digest",
        "correction_manifest_sha256",
    ):
        _digest(data[field], f"data.{field}")

    interventions = tuple(
        _object(item, "interventions[]")
        for item in _array(raw["interventions"], "interventions")
    )
    intervention_ids = tuple(item.get("intervention_id") for item in interventions)
    for item in interventions:
        _exact(
            item,
            {
                "intervention_id",
                "intervention_version",
                "targets",
                "fallback_id",
                "expected_direction",
            },
            "intervention",
        )
        _identifier(item["intervention_id"], "intervention id")
        _version(item["intervention_version"], "intervention version")
        _unique_texts(item["targets"], "intervention targets")
    if intervention_ids != (
        "intervention.trend-stability-abstain",
        "intervention.position-risk-base-policy",
        "intervention.both-experts-fallback",
    ):
        _error("intervention_mismatch", "exact two single and one joint fallbacks are required")
    if tuple(interventions[2].get("targets", ())) != (
        "expert.trend-stability",
        "expert.position-risk-review",
    ) or interventions[2].get("fallback_id") != "fallback.block-new-base-position-management":
        _error("unsafe_fallback", "joint fallback must block new and preserve base management")
    questions = tuple(
        _object(item, "attribution_questions[]")
        for item in _array(raw["attribution_questions"], "attribution_questions")
    )
    for item in questions:
        _exact(
            item,
            {
                "question_id",
                "intervention_id",
                "primary_metric",
                "required_secondary_metrics",
                "materiality_rule",
                "expected_direction",
            },
            "attribution question",
        )
        _identifier(item["question_id"], "question id")
        _unique_texts(item["required_secondary_metrics"], "required secondary metrics")
    if tuple(item.get("intervention_id") for item in questions) != intervention_ids:
        _error("question_mismatch", "each legal intervention requires one frozen question")
    budget = _object(raw["resource_budget"], "resource_budget")
    _exact(
        budget,
        {
            "implementation_worker_concurrency",
            "sentinel_window_start",
            "sentinel_window_end",
            "maximum_sentinel_jobs",
            "maximum_wall_minutes_per_job",
            "maximum_peak_rss_gib",
            "maximum_published_artifact_gib",
            "full_2020_2021_attribution_authorized_here",
            "budget_exceeded_action",
        },
        "resource_budget",
    )
    for field in (
        "implementation_worker_concurrency",
        "maximum_sentinel_jobs",
        "maximum_wall_minutes_per_job",
        "maximum_peak_rss_gib",
        "maximum_published_artifact_gib",
    ):
        _positive_int(budget.get(field), f"resource_budget.{field}")
    if budget.get("implementation_worker_concurrency") != 1 or budget.get(
        "full_2020_2021_attribution_authorized_here"
    ) is not False:
        _error("resource_budget_mismatch", "#149 must stay single-Worker and sentinel-only")
    _unique_texts(raw["stop_conditions"], "stop_conditions")
    _unique_texts(raw["non_claims"], "non_claims")
    uncertainty = _object(raw["uncertainty"], "uncertainty")
    _exact(
        uncertainty,
        {
            "method_id",
            "method_version",
            "block_sessions",
            "resamples",
            "confidence_level",
            "seed_rule",
            "dependence_disclosure",
        },
        "uncertainty",
    )
    _positive_int(uncertainty["block_sessions"], "uncertainty block sessions")
    _positive_int(uncertainty["resamples"], "uncertainty resamples")
    rollback = _object(raw["rollback"], "rollback")
    _exact(rollback, {"action", "preserve", "repair_policy"}, "rollback")
    _unique_texts(rollback["preserve"], "rollback preserve")


def _parse_card(path: Path, repository_root: Path) -> ExpertStrategyCard:
    try:
        raw_value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExpertEvaluationError(
            "invalid_artifact", "expert strategy card is unreadable"
        ) from exc
    raw = _object(raw_value, "expert strategy card")
    _exact(raw, _TOP_LEVEL, "expert strategy card")
    if raw["schema_version"] != "quanthus.expert-strategy-card.v1":
        _error("field_mismatch", "unsupported expert strategy card schema")
    strategy_id = _identifier(raw["strategy_id"], "strategy_id")
    strategy_version = _version(raw["strategy_version"], "strategy_version")
    lifecycle = _text(raw["lifecycle"], "lifecycle")
    governance = _object(raw["governance"], "governance")
    _exact(
        governance,
        {"approval_state", "approval_authority", "decision_reference"},
        "governance",
    )
    if governance["approval_authority"] != "human" or (
        lifecycle,
        governance["approval_state"],
    ) not in {
        ("draft", "pending_human_approval"),
        ("approved", "approved"),
        ("deprecated", "deprecated"),
    }:
        _error("approval_required", "strategy lifecycle requires matching human authority")
    semantic = {key: value for key, value in raw.items() if key not in _SEMANTIC_EXCLUDES}
    card = ExpertStrategyCard(
        path,
        strategy_id,
        strategy_version,
        lifecycle,
        _text(raw["owner"], "owner"),
        semantic,
        governance,
        _digest(raw["semantic_digest"], "semantic_digest", allow_zero=True),
    )
    _validate_topology(raw, repository_root)
    if card.semantic_digest != card.computed_semantic_digest:
        _error("digest_mismatch", "expert strategy card semantic digest differs")
    return card


def load_expert_strategy_card(path: Path, repository_root: Path) -> ExpertStrategyCard:
    """Load the exact card and close references without opening any strategy result."""

    return _parse_card(path, repository_root)


def expert_strategy_card_semantic_digest(path: Path) -> str:
    """Calculate proposal identity without granting implementation or result authority."""

    raw = _object(json.loads(path.read_text(encoding="utf-8")), "expert strategy card")
    _exact(raw, _TOP_LEVEL, "expert strategy card")
    return digest_value({key: value for key, value in raw.items() if key not in _SEMANTIC_EXCLUDES})

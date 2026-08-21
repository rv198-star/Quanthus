"""Outcome-blind target-decision cards for expert evaluation."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from quanthus.event_library.label_cards import (
    OutcomeLabelCard,
    StructuralBreakCard,
    load_outcome_label_card,
    load_structural_break_card,
)
from quanthus.expert_evaluation.serialization import (
    ExpertEvaluationError,
    digest_value,
)

_SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.-]{2,127}$")


def _object(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ExpertEvaluationError("field_mismatch", f"{path} must be an object")
    return value


def _array(value: object, path: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ExpertEvaluationError("field_mismatch", f"{path} must be an array")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    missing = sorted(expected.difference(value))
    unexpected = sorted(set(value).difference(expected))
    if missing or unexpected:
        raise ExpertEvaluationError(
            "field_mismatch",
            f"{path} keys do not match contract; missing={missing}, unexpected={unexpected}",
        )


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ExpertEvaluationError("field_mismatch", f"{path} must be non-empty text")
    return value


def _identifier(value: object, path: str) -> str:
    text = _text(value, path)
    if _IDENTIFIER.fullmatch(text) is None:
        raise ExpertEvaluationError(
            "invalid_identity", f"{path} must use a stable lowercase identifier"
        )
    return text


def _version(value: object, path: str) -> str:
    text = _text(value, path)
    if _SEMVER.fullmatch(text) is None:
        raise ExpertEvaluationError("invalid_identity", f"{path} must use MAJOR.MINOR.PATCH")
    return text


def _digest(value: object, path: str) -> str:
    text = _text(value, path)
    if _DIGEST.fullmatch(text) is None:
        raise ExpertEvaluationError("invalid_identity", f"{path} must be a lowercase sha256")
    return text


def _boolean(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise ExpertEvaluationError("field_mismatch", f"{path} must be boolean")
    return value


def _positive_int(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ExpertEvaluationError("field_mismatch", f"{path} must be a positive integer")
    return value


def _unique_texts(value: object, path: str) -> tuple[str, ...]:
    items = tuple(_text(item, f"{path}[]") for item in _array(value, path))
    if not items or len(items) != len(set(items)):
        raise ExpertEvaluationError("field_mismatch", f"{path} must contain unique values")
    return items


def _freeze_json(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ExpertEvaluationError("field_mismatch", "target decision keys must be strings")
        return MappingProxyType({key: _freeze_json(item) for key, item in sorted(value.items())})
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(_freeze_json(item) for item in value)
    raise ExpertEvaluationError(
        "unsupported_canonical_value", "target decision contains a non-JSON value"
    )


def _plain_json(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_plain_json(item) for item in value]
    raise ExpertEvaluationError(
        "unsupported_canonical_value", "target decision contains a non-JSON value"
    )


def _repository_path(root: Path, key: str) -> Path:
    resolved_root = root.resolve()
    candidate = (resolved_root / key).resolve()
    if candidate == resolved_root or resolved_root not in candidate.parents:
        raise ExpertEvaluationError("path_escape", "target reference escapes repository root")
    if not candidate.is_file():
        raise ExpertEvaluationError("registry_artifact_missing", f"missing target reference: {key}")
    return candidate


@dataclass(frozen=True)
class RepositoryCardRef:
    card_id: str
    card_version: str
    semantic_digest: str
    repository_key: str

    def __post_init__(self) -> None:
        _identifier(self.card_id, "card_id")
        _version(self.card_version, "card_version")
        _digest(self.semantic_digest, "semantic_digest")
        key = Path(self.repository_key)
        if key.is_absolute() or ".." in key.parts or self.repository_key.startswith("."):
            raise ExpertEvaluationError("path_escape", "repository_key must be repository-relative")

    def to_dict(self) -> dict[str, str]:
        return {
            "card_id": self.card_id,
            "card_version": self.card_version,
            "semantic_digest": self.semantic_digest,
            "repository_key": self.repository_key,
        }

    @classmethod
    def from_dict(cls, value: object, path: str) -> RepositoryCardRef:
        raw = _object(value, path)
        _exact_keys(
            raw,
            {"card_id", "card_version", "semantic_digest", "repository_key"},
            path,
        )
        return cls(
            _identifier(raw["card_id"], f"{path}.card_id"),
            _version(raw["card_version"], f"{path}.card_version"),
            _digest(raw["semantic_digest"], f"{path}.semantic_digest"),
            _text(raw["repository_key"], f"{path}.repository_key"),
        )


@dataclass(frozen=True)
class PopulationPredicate:
    predicate_id: str
    predicate_version: str
    subject_kind: str
    subject_identity_field: str
    candidate_rule_id: str
    candidate_rule_version: str
    candidate_rule_digest: str
    split_policy_id: str
    split_policy_version: str
    split_policy_digest: str
    required_partition: str
    inclusion_rule: str
    outcome_independent: bool
    predicate_digest: str

    @property
    def semantic_payload(self) -> dict[str, object]:
        return {
            "predicate_id": self.predicate_id,
            "predicate_version": self.predicate_version,
            "subject_kind": self.subject_kind,
            "subject_identity_field": self.subject_identity_field,
            "candidate_rule_id": self.candidate_rule_id,
            "candidate_rule_version": self.candidate_rule_version,
            "candidate_rule_digest": self.candidate_rule_digest,
            "split_policy_id": self.split_policy_id,
            "split_policy_version": self.split_policy_version,
            "split_policy_digest": self.split_policy_digest,
            "required_partition": self.required_partition,
            "inclusion_rule": self.inclusion_rule,
            "outcome_independent": self.outcome_independent,
        }

    @property
    def computed_digest(self) -> str:
        return digest_value(self.semantic_payload)

    def to_dict(self) -> dict[str, object]:
        return {**self.semantic_payload, "predicate_digest": self.predicate_digest}

    @classmethod
    def from_dict(cls, value: object) -> PopulationPredicate:
        raw = _object(value, "eligible_population")
        fields = {
            "predicate_id",
            "predicate_version",
            "subject_kind",
            "subject_identity_field",
            "candidate_rule_id",
            "candidate_rule_version",
            "candidate_rule_digest",
            "split_policy_id",
            "split_policy_version",
            "split_policy_digest",
            "required_partition",
            "inclusion_rule",
            "outcome_independent",
            "predicate_digest",
        }
        _exact_keys(raw, fields, "eligible_population")
        result = cls(
            predicate_id=_identifier(raw["predicate_id"], "eligible_population.predicate_id"),
            predicate_version=_version(
                raw["predicate_version"], "eligible_population.predicate_version"
            ),
            subject_kind=_text(raw["subject_kind"], "eligible_population.subject_kind"),
            subject_identity_field=_text(
                raw["subject_identity_field"], "eligible_population.subject_identity_field"
            ),
            candidate_rule_id=_identifier(
                raw["candidate_rule_id"], "eligible_population.candidate_rule_id"
            ),
            candidate_rule_version=_version(
                raw["candidate_rule_version"], "eligible_population.candidate_rule_version"
            ),
            candidate_rule_digest=_digest(
                raw["candidate_rule_digest"], "eligible_population.candidate_rule_digest"
            ),
            split_policy_id=_identifier(
                raw["split_policy_id"], "eligible_population.split_policy_id"
            ),
            split_policy_version=_version(
                raw["split_policy_version"], "eligible_population.split_policy_version"
            ),
            split_policy_digest=_digest(
                raw["split_policy_digest"], "eligible_population.split_policy_digest"
            ),
            required_partition=_text(
                raw["required_partition"], "eligible_population.required_partition"
            ),
            inclusion_rule=_text(raw["inclusion_rule"], "eligible_population.inclusion_rule"),
            outcome_independent=_boolean(
                raw["outcome_independent"], "eligible_population.outcome_independent"
            ),
            predicate_digest=_digest(
                raw["predicate_digest"], "eligible_population.predicate_digest"
            ),
        )
        if result.subject_kind != "opportunity" or result.subject_identity_field != "event_id":
            raise ExpertEvaluationError(
                "target_reference_mismatch", "first target population must use P4 event identity"
            )
        if result.required_partition != "development" or not result.outcome_independent:
            raise ExpertEvaluationError(
                "sealed_partition_access",
                "first target population must be outcome-blind development",
            )
        if result.predicate_digest != result.computed_digest:
            raise ExpertEvaluationError("digest_mismatch", "population predicate digest differs")
        return result


@dataclass(frozen=True)
class TargetDecisionCard:
    path: Path
    schema_version: str
    decision_id: str
    decision_version: str
    lifecycle: str
    owner: str
    purpose: str
    reuse_decision: str
    outcome_card: RepositoryCardRef
    structural_break_card: RepositoryCardRef
    eligible_population: PopulationPredicate
    intended_strategy_population: Mapping[str, Any]
    observation: Mapping[str, Any]
    evaluation: Mapping[str, Any]
    non_claims: tuple[str, ...]
    governance: Mapping[str, Any]
    semantic_digest: str

    def __post_init__(self) -> None:
        for field_name in (
            "intended_strategy_population",
            "observation",
            "evaluation",
            "governance",
        ):
            object.__setattr__(self, field_name, _freeze_json(getattr(self, field_name)))

    @property
    def semantic_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "decision_id": self.decision_id,
            "decision_version": self.decision_version,
            "purpose": self.purpose,
            "reuse_decision": self.reuse_decision,
            "outcome_card": self.outcome_card.to_dict(),
            "structural_break_card": self.structural_break_card.to_dict(),
            "eligible_population": self.eligible_population.to_dict(),
            "intended_strategy_population": _plain_json(self.intended_strategy_population),
            "observation": _plain_json(self.observation),
            "evaluation": _plain_json(self.evaluation),
            "non_claims": list(self.non_claims),
        }

    @property
    def computed_semantic_digest(self) -> str:
        return digest_value(self.semantic_payload)

    def require_result_access_authority(self) -> None:
        if self.lifecycle != "approved" or self.governance["approval_state"] != "approved":
            raise ExpertEvaluationError(
                "target_decision_not_approved",
                "result-bearing resolver, expert, and scorecard access requires human approval",
            )

    def resolve_reused_cards(
        self, repository_root: Path
    ) -> tuple[OutcomeLabelCard, StructuralBreakCard]:
        outcome = load_outcome_label_card(
            _repository_path(repository_root, self.outcome_card.repository_key)
        )
        structural = load_structural_break_card(
            _repository_path(repository_root, self.structural_break_card.repository_key)
        )
        outcome.require_labeling_authority()
        structural.require_labeling_authority()
        if (outcome.card_id, outcome.card_version, outcome.semantic_digest) != (
            self.outcome_card.card_id,
            self.outcome_card.card_version,
            self.outcome_card.semantic_digest,
        ):
            raise ExpertEvaluationError("target_reference_mismatch", "P4 outcome card drifted")
        if (structural.card_id, structural.card_version, structural.semantic_digest) != (
            self.structural_break_card.card_id,
            self.structural_break_card.card_version,
            self.structural_break_card.semantic_digest,
        ):
            raise ExpertEvaluationError(
                "target_reference_mismatch", "P4 structural-break card drifted"
            )
        if outcome.structural_break.semantic_digest != structural.semantic_digest:
            raise ExpertEvaluationError(
                "target_reference_mismatch", "P4 outcome and structural-break references differ"
            )
        bindings = outcome.bindings
        population = self.eligible_population
        if (
            bindings.candidate_rule_id,
            bindings.candidate_rule_version,
            bindings.candidate_rule_digest,
            bindings.split_policy_id,
            bindings.split_policy_version,
            bindings.split_policy_digest,
        ) != (
            population.candidate_rule_id,
            population.candidate_rule_version,
            population.candidate_rule_digest,
            population.split_policy_id,
            population.split_policy_version,
            population.split_policy_digest,
        ):
            raise ExpertEvaluationError(
                "target_reference_mismatch", "population does not close over the reused P4 target"
            )
        return outcome, structural

    def resolved_decision(self, repository_root: Path) -> dict[str, object]:
        outcome, structural = self.resolve_reused_cards(repository_root)
        return {
            "subject_kind": self.eligible_population.subject_kind,
            "subject_identity_field": self.eligible_population.subject_identity_field,
            "population_digest": self.eligible_population.predicate_digest,
            "observation_anchor": self.observation["anchor_rule"],
            "target_entry_anchor": outcome.entry_anchor,
            "horizon_trading_sessions": outcome.observation_sessions,
            "outcome_values": ["failure", "neutral", "positive"],
            "not_executable_treatment": "sample_ledger_only",
            "structural_break_digest": structural.semantic_digest,
            "primary_metric_id": _object(
                self.evaluation["primary_metric"], "evaluation.primary_metric"
            )["metric_id"],
            "baseline_id": _object(self.evaluation["naive_baseline"], "evaluation.naive_baseline")[
                "baseline_id"
            ],
        }


def _parse_card(path: Path) -> TargetDecisionCard:
    try:
        raw_value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExpertEvaluationError(
            "invalid_artifact", f"cannot read target decision: {path}"
        ) from exc
    raw = _object(raw_value, "target decision")
    fields = {
        "schema_version",
        "decision_id",
        "decision_version",
        "lifecycle",
        "owner",
        "purpose",
        "reuse_decision",
        "outcome_card",
        "structural_break_card",
        "eligible_population",
        "intended_strategy_population",
        "observation",
        "evaluation",
        "non_claims",
        "governance",
        "semantic_digest",
    }
    _exact_keys(raw, fields, "target decision")
    governance = _object(raw["governance"], "governance")
    _exact_keys(
        governance,
        {"approval_state", "approval_authority", "decision_reference"},
        "governance",
    )
    lifecycle = _text(raw["lifecycle"], "lifecycle")
    approval_state = _text(governance["approval_state"], "governance.approval_state")
    if lifecycle not in {"draft", "approved", "deprecated"}:
        raise ExpertEvaluationError("field_mismatch", "unsupported target decision lifecycle")
    if _text(governance["approval_authority"], "governance.approval_authority") != "human":
        raise ExpertEvaluationError("approval_required", "target decision authority must be human")
    if (lifecycle, approval_state) not in {
        ("draft", "pending_human_approval"),
        ("approved", "approved"),
        ("deprecated", "deprecated"),
    }:
        raise ExpertEvaluationError("approval_required", "target decision lifecycle conflicts")
    intended = _object(raw["intended_strategy_population"], "intended_strategy_population")
    _exact_keys(
        intended,
        {"role", "predicate_id", "predicate_version", "predicate_digest"},
        "intended_strategy_population",
    )
    observation = _object(raw["observation"], "observation")
    _exact_keys(
        observation,
        {
            "anchor_rule",
            "knowledge_cutoff_rule",
            "assessment_value_type",
            "assessment_ordinal_values",
            "assessment_to_target_order",
        },
        "observation",
    )
    evaluation = _object(raw["evaluation"], "evaluation")
    _exact_keys(
        evaluation,
        {
            "primary_metric",
            "naive_baseline",
            "hypothesis_family",
            "slices",
            "uncertainty",
            "stop_conditions",
        },
        "evaluation",
    )
    result = TargetDecisionCard(
        path=path,
        schema_version=_text(raw["schema_version"], "schema_version"),
        decision_id=_identifier(raw["decision_id"], "decision_id"),
        decision_version=_version(raw["decision_version"], "decision_version"),
        lifecycle=lifecycle,
        owner=_text(raw["owner"], "owner"),
        purpose=_text(raw["purpose"], "purpose"),
        reuse_decision=_text(raw["reuse_decision"], "reuse_decision"),
        outcome_card=RepositoryCardRef.from_dict(raw["outcome_card"], "outcome_card"),
        structural_break_card=RepositoryCardRef.from_dict(
            raw["structural_break_card"], "structural_break_card"
        ),
        eligible_population=PopulationPredicate.from_dict(raw["eligible_population"]),
        intended_strategy_population=intended,
        observation=observation,
        evaluation=evaluation,
        non_claims=_unique_texts(raw["non_claims"], "non_claims"),
        governance=governance,
        semantic_digest=_digest(raw["semantic_digest"], "semantic_digest"),
    )
    if result.schema_version != "1.0.0" or result.reuse_decision != "reuse_p4_label_cards":
        raise ExpertEvaluationError("field_mismatch", "unsupported first-target decision")
    population = result.eligible_population
    if (
        intended["predicate_id"],
        intended["predicate_version"],
        intended["predicate_digest"],
    ) != (population.predicate_id, population.predicate_version, population.predicate_digest):
        raise ExpertEvaluationError(
            "target_reference_mismatch",
            "intended strategy population must reuse the frozen predicate",
        )
    if intended["role"] != "mandatory_predeclared_slice":
        raise ExpertEvaluationError(
            "field_mismatch", "intended strategy population must remain a mandatory slice"
        )
    if observation["anchor_rule"] != "candidate_t_close":
        raise ExpertEvaluationError(
            "field_mismatch", "observation anchor must be candidate T close"
        )
    if observation["assessment_value_type"] != "ordinal":
        raise ExpertEvaluationError("field_mismatch", "first assessment must be ordinal")
    if tuple(_unique_texts(observation["assessment_ordinal_values"], "ordinal values")) != (
        "fragile",
        "neutral",
        "stable",
    ):
        raise ExpertEvaluationError("field_mismatch", "assessment ordinal values differ")
    mapping = _object(observation["assessment_to_target_order"], "assessment_to_target_order")
    _exact_keys(mapping, {"fragile", "neutral", "stable"}, "assessment_to_target_order")
    if dict(mapping) != {"fragile": "failure", "neutral": "neutral", "stable": "positive"}:
        raise ExpertEvaluationError("field_mismatch", "assessment-to-target order differs")
    primary = _object(evaluation["primary_metric"], "evaluation.primary_metric")
    _exact_keys(
        primary,
        {"metric_id", "metric_version", "definition", "unit", "direction"},
        "evaluation.primary_metric",
    )
    _identifier(primary["metric_id"], "evaluation.primary_metric.metric_id")
    _version(primary["metric_version"], "evaluation.primary_metric.metric_version")
    if primary["direction"] != "lower_is_better":
        raise ExpertEvaluationError("field_mismatch", "primary metric direction differs")
    baseline = _object(evaluation["naive_baseline"], "evaluation.naive_baseline")
    _exact_keys(
        baseline,
        {"baseline_id", "baseline_version", "definition"},
        "evaluation.naive_baseline",
    )
    _identifier(baseline["baseline_id"], "evaluation.naive_baseline.baseline_id")
    _version(baseline["baseline_version"], "evaluation.naive_baseline.baseline_version")
    _unique_texts(evaluation["hypothesis_family"], "evaluation.hypothesis_family")
    slices = _array(evaluation["slices"], "evaluation.slices")
    if not slices:
        raise ExpertEvaluationError("field_mismatch", "evaluation slices must not be empty")
    slice_keys: set[tuple[str, str]] = set()
    for index, item in enumerate(slices):
        field_path = f"evaluation.slices[{index}]"
        item_raw = _object(item, field_path)
        _exact_keys(item_raw, {"slice_id", "slice_version", "definition"}, field_path)
        key = (
            _identifier(item_raw["slice_id"], f"{field_path}.slice_id"),
            _version(item_raw["slice_version"], f"{field_path}.slice_version"),
        )
        if key in slice_keys:
            raise ExpertEvaluationError(
                "duplicate_identity", "evaluation slices contain duplicates"
            )
        slice_keys.add(key)
    uncertainty = _object(evaluation["uncertainty"], "evaluation.uncertainty")
    _exact_keys(
        uncertainty,
        {
            "method_id",
            "method_version",
            "block_sessions",
            "resamples",
            "confidence_level",
            "seed_rule",
            "effective_sample_unit",
            "dependence_disclosure",
        },
        "evaluation.uncertainty",
    )
    _identifier(uncertainty["method_id"], "uncertainty.method_id")
    _version(uncertainty["method_version"], "uncertainty.method_version")
    _positive_int(uncertainty["block_sessions"], "uncertainty.block_sessions")
    _positive_int(uncertainty["resamples"], "uncertainty.resamples")
    stops = _array(evaluation["stop_conditions"], "evaluation.stop_conditions")
    if not stops:
        raise ExpertEvaluationError("field_mismatch", "stop conditions must not be empty")
    stop_ids: set[str] = set()
    for index, item in enumerate(stops):
        field_path = f"evaluation.stop_conditions[{index}]"
        item_raw = _object(item, field_path)
        _exact_keys(item_raw, {"condition_id", "rule", "disposition"}, field_path)
        condition_id = _identifier(item_raw["condition_id"], f"{field_path}.condition_id")
        if condition_id in stop_ids:
            raise ExpertEvaluationError(
                "duplicate_identity", "evaluation stop conditions contain duplicates"
            )
        stop_ids.add(condition_id)
        if item_raw["disposition"] not in {"block_publication", "inconclusive"}:
            raise ExpertEvaluationError("field_mismatch", "unsupported stop disposition")
    if result.semantic_digest != result.computed_semantic_digest:
        raise ExpertEvaluationError("digest_mismatch", "target decision semantic digest differs")
    return result


def load_target_decision_card(path: Path, repository_root: Path) -> TargetDecisionCard:
    """Load a card and close all reused P4 references without opening result data."""

    card = _parse_card(path)
    card.resolve_reused_cards(repository_root)
    return card


def target_decision_semantic_digest(path: Path) -> str:
    """Compute a proposal digest without requiring result-access authority."""

    return _parse_card(path).computed_semantic_digest

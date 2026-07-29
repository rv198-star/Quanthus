"""Deterministic veto precedence and constraint intersection for P3 governance."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from quanthus.expert_protocols import (
    ConstraintOutput,
    ExpertApplicability,
    ExpertContext,
    RequiredAction,
    ScoringOutput,
    VetoAuthority,
    VetoOutput,
)

_ACTION_PRIORITY = {
    RequiredAction.NONE: 0,
    RequiredAction.BLOCK_NEW: 1,
    RequiredAction.REDUCE_ONLY: 2,
    RequiredAction.FORCE_LIQUIDATE: 3,
}
_AUTHORITY_PRIORITY = {VetoAuthority.SYSTEM: 0, VetoAuthority.STRATEGY: 1}


class AggregationError(ValueError):
    """Raised when governance inputs cannot produce a safe deterministic result."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


@dataclass(frozen=True)
class ConstraintRuleRef:
    expert_id: str
    expert_version: str
    rule: str

    def to_dict(self) -> dict[str, str]:
        return {
            "expert_id": self.expert_id,
            "expert_version": self.expert_version,
            "rule": self.rule,
        }


@dataclass(frozen=True)
class MergedConstraints:
    minimum_position: Decimal
    maximum_position: Decimal
    stop_rules: tuple[ConstraintRuleRef, ...]
    hold_rules: tuple[ConstraintRuleRef, ...]
    source_output_digests: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not self.minimum_position.is_finite()
            or not self.maximum_position.is_finite()
            or not Decimal("0") <= self.minimum_position <= self.maximum_position <= Decimal("1")
        ):
            raise AggregationError(
                "invalid_merged_constraints", "merged position bounds are invalid"
            )
        if not self.stop_rules or not self.hold_rules or not self.source_output_digests:
            raise AggregationError(
                "invalid_merged_constraints", "merged constraints require rules and sources"
            )

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "minimum_position": str(self.minimum_position),
            "maximum_position": str(self.maximum_position),
            "stop_rules": [item.to_dict() for item in self.stop_rules],
            "hold_rules": [item.to_dict() for item in self.hold_rules],
            "source_output_digests": list(self.source_output_digests),
        }


@dataclass(frozen=True)
class GovernanceResult:
    security_code: str
    snapshot_id: str
    knowledge_cutoff: datetime
    required_action: RequiredAction
    terminating_veto_ids: tuple[str, ...]
    veto_outputs: tuple[VetoOutput, ...]
    constraint_outputs: tuple[ConstraintOutput, ...]
    merged_constraints: MergedConstraints | None

    def __post_init__(self) -> None:
        if not self.security_code.strip() or not self.snapshot_id.strip():
            raise AggregationError("invalid_governance_result", "result identity must not be empty")
        if self.knowledge_cutoff.tzinfo is None or self.knowledge_cutoff.utcoffset() is None:
            raise AggregationError(
                "invalid_governance_result", "result knowledge_cutoff must be timezone-aware"
            )
        if self.required_action is RequiredAction.NONE:
            if self.terminating_veto_ids or self.merged_constraints is None:
                raise AggregationError(
                    "invalid_governance_result",
                    "non-terminating governance requires merged constraints and no veto ids",
                )
        elif not self.terminating_veto_ids or self.merged_constraints is not None:
            raise AggregationError(
                "invalid_governance_result",
                "terminating governance requires veto ids and no merged constraints",
            )

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "security_code": self.security_code,
            "snapshot_id": self.snapshot_id,
            "knowledge_cutoff": self.knowledge_cutoff.isoformat(),
            "required_action": self.required_action.value,
            "terminating_veto_ids": list(self.terminating_veto_ids),
            "veto_outputs": [item.to_dict() for item in self.veto_outputs],
            "constraint_outputs": [item.to_dict() for item in self.constraint_outputs],
            "merged_constraints": (
                None if self.merged_constraints is None else self.merged_constraints.to_dict()
            ),
        }


class GovernedAction(str, Enum):
    NO_ACTION = "no_action"
    TRIAL_ENTRY = "trial_entry"
    REDUCE = "reduce"
    FULL_EXIT = "full_exit"


@dataclass(frozen=True)
class ScoreActionPolicy:
    policy_version: str
    minimum_entry_score: Decimal

    def __post_init__(self) -> None:
        if not self.policy_version.strip():
            raise AggregationError("invalid_action_policy", "policy version must not be empty")
        if not self.minimum_entry_score.is_finite() or not Decimal(
            "-1"
        ) <= self.minimum_entry_score <= Decimal("1"):
            raise AggregationError(
                "invalid_action_policy", "minimum entry score must be in [-1, 1]"
            )

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, str]:
        return {
            "policy_version": self.policy_version,
            "minimum_entry_score": str(self.minimum_entry_score),
            "score_formula": "mean(signal_times_confidence_over_applicable_outputs)",
        }


@dataclass(frozen=True)
class ScoreActionResult:
    security_code: str
    snapshot_id: str
    knowledge_cutoff: datetime
    position_state: str
    governance_digest: str
    action_policy_version: str
    action_policy_digest: str
    scoring_outputs: tuple[ScoringOutput, ...]
    aggregate_score: Decimal
    proposed_action: GovernedAction
    final_action: GovernedAction
    reason_codes: tuple[str, ...]

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "security_code": self.security_code,
            "snapshot_id": self.snapshot_id,
            "knowledge_cutoff": self.knowledge_cutoff.isoformat(),
            "position_state": self.position_state,
            "governance_digest": self.governance_digest,
            "action_policy_version": self.action_policy_version,
            "action_policy_digest": self.action_policy_digest,
            "scoring_outputs": [item.to_dict() for item in self.scoring_outputs],
            "aggregate_score": str(self.aggregate_score),
            "proposed_action": self.proposed_action.value,
            "final_action": self.final_action.value,
            "reason_codes": list(self.reason_codes),
        }


def _veto_key(output: VetoOutput) -> tuple[int, str, str]:
    return (_AUTHORITY_PRIORITY[output.authority], output.expert_id, output.expert_version)


def _constraint_key(output: ConstraintOutput) -> tuple[str, str, str]:
    scope = "" if output.scope is None else output.scope.value
    return (scope, output.expert_id, output.expert_version)


def _validate_unique_outputs(
    outputs: Sequence[VetoOutput | ConstraintOutput | ScoringOutput],
    kind: str,
) -> None:
    keys = [(output.expert_id, output.expert_version) for output in outputs]
    if len(keys) != len(set(keys)):
        raise AggregationError("duplicate_expert_output", f"duplicate {kind} expert outputs")


def _validate_context_evidence(
    context: ExpertContext,
    vetoes: Sequence[VetoOutput],
    constraints: Sequence[ConstraintOutput],
) -> None:
    allowed = {item.key for item in context.evidence_refs}
    outputs: tuple[VetoOutput | ConstraintOutput, ...] = (
        *tuple(vetoes),
        *tuple(constraints),
    )
    for output in outputs:
        supplied = {item.key for item in output.evidence}
        if not supplied.issubset(allowed):
            raise AggregationError(
                "context_evidence_mismatch",
                f"expert {output.expert_id} references evidence outside aggregation context",
            )


def _validate_scoring_context(
    context: ExpertContext,
    outputs: tuple[ScoringOutput, ...],
) -> None:
    _validate_unique_outputs(outputs, "scoring")
    allowed = {item.key for item in context.evidence_refs}
    for output in outputs:
        supplied = {item.key for item in output.evidence}
        if not supplied.issubset(allowed):
            raise AggregationError(
                "context_evidence_mismatch",
                f"expert {output.expert_id} references evidence outside aggregation context",
            )


def _merge_constraints(outputs: tuple[ConstraintOutput, ...]) -> MergedConstraints:
    applicable = tuple(
        output for output in outputs if output.applicability is ExpertApplicability.APPLICABLE
    )
    if not applicable:
        raise AggregationError(
            "no_applicable_constraints", "no applicable constraint output is available"
        )
    minimums = [output.minimum_position for output in applicable]
    maximums = [output.maximum_position for output in applicable]
    if any(value is None for value in (*minimums, *maximums)):
        raise AggregationError(
            "invalid_constraint_input", "applicable constraint output has missing bounds"
        )
    lower = max(value for value in minimums if value is not None)
    upper = min(value for value in maximums if value is not None)
    if lower > upper:
        sources = ", ".join(output.expert_id for output in applicable)
        raise AggregationError(
            "constraint_conflict", f"constraint intersection is empty across: {sources}"
        )
    stop_rules = tuple(
        ConstraintRuleRef(output.expert_id, output.expert_version, output.stop_rule)
        for output in applicable
        if output.stop_rule is not None
    )
    hold_rules = tuple(
        ConstraintRuleRef(output.expert_id, output.expert_version, output.hold_rule)
        for output in applicable
        if output.hold_rule is not None
    )
    return MergedConstraints(
        minimum_position=lower,
        maximum_position=upper,
        stop_rules=stop_rules,
        hold_rules=hold_rules,
        source_output_digests=tuple(output.digest for output in applicable),
    )


def aggregate_governance(
    context: ExpertContext,
    vetoes: Sequence[VetoOutput],
    constraints: Sequence[ConstraintOutput],
) -> GovernanceResult:
    """Apply fixed veto precedence, then intersect constraints only when no veto terminates."""

    ordered_vetoes = tuple(sorted(vetoes, key=_veto_key))
    ordered_constraints = tuple(sorted(constraints, key=_constraint_key))
    _validate_unique_outputs(ordered_vetoes, "veto")
    _validate_unique_outputs(ordered_constraints, "constraint")
    _validate_context_evidence(context, ordered_vetoes, ordered_constraints)
    system_vetoes = tuple(
        output for output in ordered_vetoes if output.authority is VetoAuthority.SYSTEM
    )
    if not system_vetoes:
        raise AggregationError("missing_system_veto", "at least one system veto output is required")
    if not any(output.applicability is ExpertApplicability.APPLICABLE for output in system_vetoes):
        raise AggregationError(
            "system_veto_unavailable", "at least one system veto must be applicable"
        )

    applicable_vetoes = tuple(
        output
        for output in ordered_vetoes
        if output.applicability is ExpertApplicability.APPLICABLE
    )
    required_action = max(
        (output.required_action for output in applicable_vetoes),
        key=lambda action: _ACTION_PRIORITY[action] if action is not None else -1,
        default=RequiredAction.NONE,
    )
    if required_action is None:
        raise AggregationError("invalid_veto_input", "applicable veto is missing required_action")
    terminating = tuple(
        output.expert_id
        for output in applicable_vetoes
        if output.required_action is required_action and required_action is not RequiredAction.NONE
    )
    merged = (
        _merge_constraints(ordered_constraints) if required_action is RequiredAction.NONE else None
    )
    return GovernanceResult(
        security_code=context.security_code,
        snapshot_id=context.snapshot_id,
        knowledge_cutoff=context.knowledge_cutoff,
        required_action=required_action,
        terminating_veto_ids=terminating,
        veto_outputs=ordered_vetoes,
        constraint_outputs=ordered_constraints,
        merged_constraints=merged,
    )


def aggregate_score_and_map_action(
    context: ExpertContext,
    governance: GovernanceResult,
    scoring_outputs: Sequence[ScoringOutput],
    policy: ScoreActionPolicy,
) -> ScoreActionResult:
    """Compute the fixed score, then map governance and state to one proposed/final action."""

    if (
        governance.security_code != context.security_code
        or governance.snapshot_id != context.snapshot_id
        or governance.knowledge_cutoff != context.knowledge_cutoff
    ):
        raise AggregationError(
            "governance_context_mismatch", "governance and score context must be identical"
        )
    ordered = tuple(sorted(scoring_outputs, key=lambda item: (item.expert_id, item.expert_version)))
    _validate_scoring_context(context, ordered)
    applicable = tuple(
        output for output in ordered if output.applicability is ExpertApplicability.APPLICABLE
    )
    if not applicable:
        raise AggregationError("no_applicable_scores", "at least one applicable score is required")
    if any(output.signal is None or output.confidence is None for output in applicable):
        raise AggregationError("invalid_scoring_input", "applicable score output is incomplete")
    aggregate_score = sum(
        (
            output.signal * output.confidence
            for output in applicable
            if output.signal is not None and output.confidence is not None
        ),
        Decimal("0"),
    ) / Decimal(len(applicable))

    if context.position_state == "empty":
        proposed = (
            GovernedAction.TRIAL_ENTRY
            if aggregate_score >= policy.minimum_entry_score
            else GovernedAction.NO_ACTION
        )
    else:
        proposed = GovernedAction.NO_ACTION

    required = governance.required_action
    if required is RequiredAction.FORCE_LIQUIDATE:
        final = GovernedAction.FULL_EXIT
        reasons = ("force_liquidate_veto",)
    elif required is RequiredAction.REDUCE_ONLY:
        final = GovernedAction.REDUCE
        reasons = ("reduce_only_veto",)
    elif required is RequiredAction.BLOCK_NEW and context.position_state == "empty":
        final = GovernedAction.NO_ACTION
        reasons = ("block_new_veto",)
    elif proposed is GovernedAction.TRIAL_ENTRY:
        final = proposed
        reasons = ("entry_score_threshold_met",)
    elif context.position_state == "empty":
        final = GovernedAction.NO_ACTION
        reasons = ("entry_score_below_threshold",)
    else:
        final = GovernedAction.NO_ACTION
        reasons = ("position_review_hold",)

    return ScoreActionResult(
        security_code=context.security_code,
        snapshot_id=context.snapshot_id,
        knowledge_cutoff=context.knowledge_cutoff,
        position_state=context.position_state,
        governance_digest=governance.digest,
        action_policy_version=policy.policy_version,
        action_policy_digest=policy.digest,
        scoring_outputs=ordered,
        aggregate_score=aggregate_score,
        proposed_action=proposed,
        final_action=final,
        reason_codes=reasons,
    )

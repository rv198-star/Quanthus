"""Deterministic P3 comparison of non-executing shadow decision records."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from quanthus.backtest.contracts import DecisionDisposition, DecisionEvent, decimal_text

SHADOW_DIVERGENCE_VERSION = "1.0.0"


class ShadowDivergenceError(ValueError):
    """Raised when decisions cannot form a safe shadow comparison."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}")


def _canonical_json_value(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return decimal_text(value)
    if isinstance(value, Enum):
        return str(value.value)
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        default=_canonical_json_value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


@dataclass(frozen=True)
class MappingChange:
    key: str
    baseline_value: str | None
    shadow_value: str | None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "key": self.key,
            "baseline_value": self.baseline_value,
            "shadow_value": self.shadow_value,
        }


@dataclass(frozen=True)
class ShadowDivergenceRecord:
    comparison_id: str
    comparison_version: str
    run_id: str
    baseline_decision_id: str
    shadow_decision_id: str
    snapshot_id: str
    decided_at: datetime
    security_code: str
    unit_id: str
    lifecycle_stage: str
    baseline_disposition: DecisionDisposition
    baseline_proposed_action: str
    shadow_proposed_action: str
    baseline_final_action: str
    shadow_final_action: str
    aggregate_score_delta: Decimal
    expert_outputs_changed: bool
    added_vetoes: tuple[str, ...]
    removed_vetoes: tuple[str, ...]
    constraint_changes: tuple[MappingChange, ...]
    added_reason_codes: tuple[str, ...]
    removed_reason_codes: tuple[str, ...]
    policy_version_changes: tuple[MappingChange, ...]
    baseline_digest: str
    shadow_digest: str

    @property
    def proposed_action_changed(self) -> bool:
        return self.baseline_proposed_action != self.shadow_proposed_action

    @property
    def final_action_changed(self) -> bool:
        return self.baseline_final_action != self.shadow_final_action

    @property
    def output_diverged(self) -> bool:
        return any(
            (
                self.proposed_action_changed,
                self.final_action_changed,
                self.aggregate_score_delta != 0,
                self.expert_outputs_changed,
                bool(self.added_vetoes),
                bool(self.removed_vetoes),
                bool(self.constraint_changes),
                bool(self.added_reason_codes),
                bool(self.removed_reason_codes),
            )
        )

    @property
    def execution_authorized(self) -> bool:
        return False

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "comparison_id": self.comparison_id,
            "comparison_version": self.comparison_version,
            "run_id": self.run_id,
            "baseline_decision_id": self.baseline_decision_id,
            "shadow_decision_id": self.shadow_decision_id,
            "snapshot_id": self.snapshot_id,
            "decided_at": self.decided_at.isoformat(),
            "security_code": self.security_code,
            "unit_id": self.unit_id,
            "lifecycle_stage": self.lifecycle_stage,
            "baseline_disposition": self.baseline_disposition.value,
            "baseline_proposed_action": self.baseline_proposed_action,
            "shadow_proposed_action": self.shadow_proposed_action,
            "baseline_final_action": self.baseline_final_action,
            "shadow_final_action": self.shadow_final_action,
            "proposed_action_changed": self.proposed_action_changed,
            "final_action_changed": self.final_action_changed,
            "aggregate_score_delta": decimal_text(self.aggregate_score_delta),
            "expert_outputs_changed": self.expert_outputs_changed,
            "added_vetoes": list(self.added_vetoes),
            "removed_vetoes": list(self.removed_vetoes),
            "constraint_changes": [item.to_dict() for item in self.constraint_changes],
            "added_reason_codes": list(self.added_reason_codes),
            "removed_reason_codes": list(self.removed_reason_codes),
            "policy_version_changes": [item.to_dict() for item in self.policy_version_changes],
            "baseline_digest": self.baseline_digest,
            "shadow_digest": self.shadow_digest,
            "output_diverged": self.output_diverged,
            "execution_authorized": self.execution_authorized,
        }


def _mapping_changes(
    baseline: Mapping[str, str], shadow: Mapping[str, str]
) -> tuple[MappingChange, ...]:
    return tuple(
        MappingChange(key, baseline.get(key), shadow.get(key))
        for key in sorted(set(baseline).union(shadow))
        if baseline.get(key) != shadow.get(key)
    )


def _review_identity(event: DecisionEvent) -> tuple[object, ...]:
    return (
        event.run_id,
        event.snapshot,
        event.decided_at,
        event.profile_id,
        event.window_id,
        event.security_code,
        event.unit_id,
        event.lifecycle_stage,
    )


def compare_shadow_decision(
    baseline: DecisionEvent,
    shadow: DecisionEvent,
) -> ShadowDivergenceRecord:
    """Compare one active/rejected decision with a shadow decision from the same review."""

    if baseline.disposition is DecisionDisposition.SHADOW:
        raise ShadowDivergenceError(
            "invalid_baseline", "baseline decision cannot have shadow disposition"
        )
    if shadow.disposition is not DecisionDisposition.SHADOW:
        raise ShadowDivergenceError(
            "invalid_shadow", "comparison target must have shadow disposition"
        )
    if shadow.signal_id is not None or shadow.linked_order_ids:
        raise ShadowDivergenceError(
            "shadow_execution_authority",
            "shadow decision cannot reference a signal or execution order",
        )
    if baseline.decision_id == shadow.decision_id:
        raise ShadowDivergenceError(
            "duplicate_decision_identity", "baseline and shadow decision ids must differ"
        )
    if _review_identity(baseline) != _review_identity(shadow):
        raise ShadowDivergenceError(
            "review_identity_mismatch", "decisions do not belong to the same review"
        )

    baseline_payload = baseline.to_dict()
    shadow_payload = shadow.to_dict()
    baseline_digest = _digest(baseline_payload)
    shadow_digest = _digest(shadow_payload)
    comparison_hash = hashlib.sha256(
        _canonical_bytes((baseline_digest, shadow_digest, SHADOW_DIVERGENCE_VERSION))
    ).hexdigest()[:24]
    return ShadowDivergenceRecord(
        comparison_id=f"shadowcmp_{comparison_hash}",
        comparison_version=SHADOW_DIVERGENCE_VERSION,
        run_id=baseline.run_id,
        baseline_decision_id=baseline.decision_id,
        shadow_decision_id=shadow.decision_id,
        snapshot_id=baseline.snapshot.snapshot_id,
        decided_at=baseline.decided_at,
        security_code=baseline.security_code,
        unit_id=baseline.unit_id,
        lifecycle_stage=baseline.lifecycle_stage,
        baseline_disposition=baseline.disposition,
        baseline_proposed_action=baseline.proposed_action,
        shadow_proposed_action=shadow.proposed_action,
        baseline_final_action=baseline.final_action,
        shadow_final_action=shadow.final_action,
        aggregate_score_delta=shadow.aggregate_score - baseline.aggregate_score,
        expert_outputs_changed=_digest(baseline.expert_outputs) != _digest(shadow.expert_outputs),
        added_vetoes=tuple(sorted(set(shadow.vetoes).difference(baseline.vetoes))),
        removed_vetoes=tuple(sorted(set(baseline.vetoes).difference(shadow.vetoes))),
        constraint_changes=_mapping_changes(baseline.constraints, shadow.constraints),
        added_reason_codes=tuple(
            sorted(set(shadow.reason_codes).difference(baseline.reason_codes))
        ),
        removed_reason_codes=tuple(
            sorted(set(baseline.reason_codes).difference(shadow.reason_codes))
        ),
        policy_version_changes=_mapping_changes(baseline.policy_versions, shadow.policy_versions),
        baseline_digest=baseline_digest,
        shadow_digest=shadow_digest,
    )


def build_shadow_divergence_log(
    baseline: DecisionEvent,
    shadows: Sequence[DecisionEvent],
) -> tuple[ShadowDivergenceRecord, ...]:
    """Build a canonical comparison log without creating or persisting execution artifacts."""

    if not shadows:
        raise ShadowDivergenceError(
            "missing_shadow_decision", "at least one shadow decision is required"
        )
    decision_ids = [shadow.decision_id for shadow in shadows]
    if len(decision_ids) != len(set(decision_ids)):
        raise ShadowDivergenceError(
            "duplicate_shadow_decision", "shadow decision ids must be unique"
        )
    return tuple(
        compare_shadow_decision(baseline, shadow)
        for shadow in sorted(shadows, key=lambda item: item.decision_id)
    )

"""Deterministic frequency and coexistence audit for registered P3 label results."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext
from enum import Enum
from itertools import combinations
from typing import Any

from quanthus.label_registry import (
    LabelCard,
    LabelDimension,
    LabelLifecycle,
    LabelRegistry,
    LabelResult,
    LabelResultStatus,
)

LABEL_AUDIT_VERSION = "1.0.0"
_RATE_QUANTUM = Decimal("0.000000000001")


class LabelAuditError(ValueError):
    """Raised when label audit inputs are structurally invalid."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}")


class LabelConflictReason(str, Enum):
    PRIMARY_STAGE_MULTIPLE = "primary_stage_multiple"
    MUTUALLY_EXCLUSIVE = "mutually_exclusive"


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


def _rate(numerator: int, denominator: int) -> Decimal:
    with localcontext() as context:
        context.prec = 50
        return (Decimal(numerator) / Decimal(denominator)).quantize(
            _RATE_QUANTUM,
            rounding=ROUND_HALF_UP,
        )


@dataclass(frozen=True)
class LabelValueCount:
    value: str
    count: int

    def to_dict(self) -> dict[str, str | int]:
        return {"value": self.value, "count": self.count}


@dataclass(frozen=True)
class LabelFrequencyAudit:
    label_id: str
    label_version: str
    lifecycle: LabelLifecycle
    sample_count: int
    applied_count: int
    unknown_count: int
    not_applicable_count: int
    applied_rate: Decimal
    unknown_rate: Decimal
    not_applicable_rate: Decimal
    expected_minimum_rate: Decimal
    expected_maximum_rate: Decimal
    within_expected_range: bool
    value_counts: tuple[LabelValueCount, ...]

    @property
    def blocks_publication(self) -> bool:
        return self.lifecycle is LabelLifecycle.ACTIVE and not self.within_expected_range

    @property
    def shadow_warning(self) -> bool:
        return self.lifecycle is LabelLifecycle.SHADOW and not self.within_expected_range

    def to_dict(self) -> dict[str, Any]:
        return {
            "label_id": self.label_id,
            "label_version": self.label_version,
            "lifecycle": self.lifecycle.value,
            "sample_count": self.sample_count,
            "applied_count": self.applied_count,
            "unknown_count": self.unknown_count,
            "not_applicable_count": self.not_applicable_count,
            "applied_rate": str(self.applied_rate),
            "unknown_rate": str(self.unknown_rate),
            "not_applicable_rate": str(self.not_applicable_rate),
            "expected_minimum_rate": str(self.expected_minimum_rate),
            "expected_maximum_rate": str(self.expected_maximum_rate),
            "within_expected_range": self.within_expected_range,
            "value_counts": [item.to_dict() for item in self.value_counts],
            "blocks_publication": self.blocks_publication,
            "shadow_warning": self.shadow_warning,
        }


@dataclass(frozen=True)
class LabelConflict:
    security_code: str
    snapshot_id: str
    knowledge_cutoff: str
    left_label_id: str
    left_label_version: str
    right_label_id: str
    right_label_version: str
    reason: LabelConflictReason
    blocks_publication: bool

    @property
    def key(self) -> tuple[str, ...]:
        return (
            self.security_code,
            self.snapshot_id,
            self.knowledge_cutoff,
            self.left_label_id,
            self.left_label_version,
            self.right_label_id,
            self.right_label_version,
            self.reason.value,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "security_code": self.security_code,
            "snapshot_id": self.snapshot_id,
            "knowledge_cutoff": self.knowledge_cutoff,
            "left_label_id": self.left_label_id,
            "left_label_version": self.left_label_version,
            "right_label_id": self.right_label_id,
            "right_label_version": self.right_label_version,
            "reason": self.reason.value,
            "blocks_publication": self.blocks_publication,
        }


@dataclass(frozen=True)
class LabelAuditReport:
    audit_version: str
    registry_version: str
    registry_digest: str
    sample_scope: str
    context_count: int
    result_count: int
    frequencies: tuple[LabelFrequencyAudit, ...]
    conflicts: tuple[LabelConflict, ...]

    @property
    def blocking_issue_count(self) -> int:
        frequency_failures = sum(item.blocks_publication for item in self.frequencies)
        conflict_failures = sum(item.blocks_publication for item in self.conflicts)
        return frequency_failures + conflict_failures

    @property
    def shadow_warning_count(self) -> int:
        frequency_warnings = sum(item.shadow_warning for item in self.frequencies)
        conflict_warnings = sum(not item.blocks_publication for item in self.conflicts)
        return frequency_warnings + conflict_warnings

    @property
    def passed(self) -> bool:
        return self.blocking_issue_count == 0

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_version": self.audit_version,
            "registry_version": self.registry_version,
            "registry_digest": self.registry_digest,
            "sample_scope": self.sample_scope,
            "context_count": self.context_count,
            "result_count": self.result_count,
            "frequencies": [item.to_dict() for item in self.frequencies],
            "conflicts": [item.to_dict() for item in self.conflicts],
            "blocking_issue_count": self.blocking_issue_count,
            "shadow_warning_count": self.shadow_warning_count,
            "passed": self.passed,
        }


def _validate_relationship_targets(registry: LabelRegistry, cards: Sequence[LabelCard]) -> None:
    registered_ids = {card.label_id for card in registry.cards}
    for card in cards:
        referenced = set(card.mutually_exclusive_with).union(card.coexists_with)
        missing = sorted(referenced.difference(registered_ids))
        if missing:
            raise LabelAuditError(
                "unknown_relationship_target",
                f"label {card.label_id}@{card.version} references unknown labels: {missing}",
            )


def _validate_result(card: LabelCard, result: LabelResult, sample_scope: str) -> None:
    if card.expected_frequency.sample_scope != sample_scope:
        raise LabelAuditError(
            "sample_scope_mismatch",
            f"label {card.label_id}@{card.version} expects {card.expected_frequency.sample_scope}",
        )
    if result.card_digest != card.digest:
        raise LabelAuditError(
            "card_digest_mismatch", f"result does not match {card.label_id}@{card.version}"
        )
    if result.dimension is not card.dimension:
        raise LabelAuditError(
            "dimension_mismatch", f"result dimension does not match {card.label_id}@{card.version}"
        )
    if result.reason_code not in card.reason_codes:
        raise LabelAuditError(
            "undeclared_reason_code",
            f"result reason is not declared by {card.label_id}@{card.version}",
        )
    if result.status is LabelResultStatus.APPLIED and result.value not in card.output_values:
        raise LabelAuditError(
            "undeclared_label_value",
            f"result value is not declared by {card.label_id}@{card.version}",
        )


def _frequency_audit(card: LabelCard, results: Sequence[LabelResult]) -> LabelFrequencyAudit:
    sample_count = len(results)
    applied_count = sum(result.status is LabelResultStatus.APPLIED for result in results)
    unknown_count = sum(result.status is LabelResultStatus.UNKNOWN for result in results)
    not_applicable_count = sum(
        result.status is LabelResultStatus.NOT_APPLICABLE for result in results
    )
    value_counts: dict[str, int] = defaultdict(int)
    for result in results:
        if result.status is LabelResultStatus.APPLIED:
            assert result.value is not None
            value_counts[result.value] += 1
    expected = card.expected_frequency
    within_range = (
        Decimal(applied_count) >= expected.minimum_rate * sample_count
        and Decimal(applied_count) <= expected.maximum_rate * sample_count
    )
    return LabelFrequencyAudit(
        label_id=card.label_id,
        label_version=card.version,
        lifecycle=card.lifecycle,
        sample_count=sample_count,
        applied_count=applied_count,
        unknown_count=unknown_count,
        not_applicable_count=not_applicable_count,
        applied_rate=_rate(applied_count, sample_count),
        unknown_rate=_rate(unknown_count, sample_count),
        not_applicable_rate=_rate(not_applicable_count, sample_count),
        expected_minimum_rate=expected.minimum_rate,
        expected_maximum_rate=expected.maximum_rate,
        within_expected_range=within_range,
        value_counts=tuple(
            LabelValueCount(value, count) for value, count in sorted(value_counts.items())
        ),
    )


def _conflict_reason(left: LabelCard, right: LabelCard) -> LabelConflictReason | None:
    if (
        left.dimension is LabelDimension.PRIMARY_STAGE
        and right.dimension is LabelDimension.PRIMARY_STAGE
    ):
        return LabelConflictReason.PRIMARY_STAGE_MULTIPLE
    if (
        right.label_id in left.mutually_exclusive_with
        or left.label_id in right.mutually_exclusive_with
    ):
        return LabelConflictReason.MUTUALLY_EXCLUSIVE
    return None


def audit_label_results(
    registry: LabelRegistry,
    results: Sequence[LabelResult],
    *,
    sample_scope: str,
) -> LabelAuditReport:
    """Audit card-level applied frequency and declared coexistence without defining labels."""

    if not sample_scope.strip():
        raise LabelAuditError("invalid_sample_scope", "sample_scope must not be empty")
    if not results:
        raise LabelAuditError("missing_label_results", "label audit requires at least one result")

    ordered_results = tuple(
        sorted(
            results,
            key=lambda item: (
                item.label_id,
                item.label_version,
                item.security_code,
                item.snapshot_id,
                item.knowledge_cutoff.isoformat(),
            ),
        )
    )
    result_keys = [
        (
            result.label_id,
            result.label_version,
            result.security_code,
            result.snapshot_id,
            result.knowledge_cutoff,
        )
        for result in ordered_results
    ]
    if len(result_keys) != len(set(result_keys)):
        raise LabelAuditError(
            "duplicate_label_result", "one label result may exist per exact PIT context"
        )

    cutoff_by_snapshot: dict[tuple[str, str], object] = {}
    cards_by_key: dict[tuple[str, str], LabelCard] = {}
    results_by_card: dict[tuple[str, str], list[LabelResult]] = defaultdict(list)
    results_by_context: dict[tuple[str, str, str], list[LabelResult]] = defaultdict(list)
    for result in ordered_results:
        snapshot_key = (result.security_code, result.snapshot_id)
        previous_cutoff = cutoff_by_snapshot.setdefault(snapshot_key, result.knowledge_cutoff)
        if previous_cutoff != result.knowledge_cutoff:
            raise LabelAuditError(
                "context_cutoff_mismatch",
                "one security/snapshot identity cannot have multiple knowledge cutoffs",
            )
        try:
            card = registry.get(result.label_id, result.label_version)
        except ValueError as exc:
            raise LabelAuditError("unknown_label_result", str(exc)) from exc
        _validate_result(card, result, sample_scope)
        cards_by_key[card.key] = card
        results_by_card[card.key].append(result)
        context_key = (
            result.security_code,
            result.snapshot_id,
            result.knowledge_cutoff.isoformat(),
        )
        results_by_context[context_key].append(result)

    cards = tuple(cards_by_key[key] for key in sorted(cards_by_key))
    _validate_relationship_targets(registry, cards)
    frequencies = tuple(_frequency_audit(card, results_by_card[card.key]) for card in cards)

    conflicts: list[LabelConflict] = []
    for context_key, context_results in sorted(results_by_context.items()):
        applied = sorted(
            (result for result in context_results if result.status is LabelResultStatus.APPLIED),
            key=lambda item: (item.label_id, item.label_version),
        )
        for left_result, right_result in combinations(applied, 2):
            left_card = cards_by_key[(left_result.label_id, left_result.label_version)]
            right_card = cards_by_key[(right_result.label_id, right_result.label_version)]
            reason = _conflict_reason(left_card, right_card)
            if reason is None:
                continue
            conflicts.append(
                LabelConflict(
                    security_code=context_key[0],
                    snapshot_id=context_key[1],
                    knowledge_cutoff=context_key[2],
                    left_label_id=left_card.label_id,
                    left_label_version=left_card.version,
                    right_label_id=right_card.label_id,
                    right_label_version=right_card.version,
                    reason=reason,
                    blocks_publication=(
                        left_card.lifecycle is LabelLifecycle.ACTIVE
                        and right_card.lifecycle is LabelLifecycle.ACTIVE
                    ),
                )
            )

    return LabelAuditReport(
        audit_version=LABEL_AUDIT_VERSION,
        registry_version=registry.registry_version,
        registry_digest=registry.digest,
        sample_scope=sample_scope,
        context_count=len(results_by_context),
        result_count=len(ordered_results),
        frequencies=frequencies,
        conflicts=tuple(sorted(conflicts, key=lambda item: item.key)),
    )

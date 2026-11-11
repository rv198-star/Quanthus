"""Versioned Head2 feature correction and one guarded replay update.

These are synthetic-capable primitives, not source/reference admission or a
consumer probability contract. The caller must separately admit the exact run.
The frozen v1 learner, rule and probability gate are intentionally unchanged.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from quanthus.stage_learning import local_head as legacy
from quanthus.stage_learning.local_head import (
    DevelopmentDiagnostic,
    HeadBindings,
    HeadCheckpoint,
    HeadPolicy,
    HeadRow,
    diagnose_head,
    dump_checkpoint,
    load_checkpoint,
)
from quanthus.stage_learning.p5_assessment import (
    AssessmentOutput,
    P5AssessmentError,
    resolve_output,
)
from quanthus.stage_learning.p5_contracts import require_digest
from quanthus.structure_directional_path_geometry import CONTRACT_DIGEST as PATH_CONTRACT_DIGEST

FACT_NAMES = (
    "directional_efficiency",
    "range_width",
    "width_change",
    "center_shift",
    "range_traversal",
    "counter_direction_excursion_share",
)
FEATURE_NAMES = (
    "absolute_directional_efficiency",
    "range_width",
    "width_change",
    "absolute_center_shift",
    "range_traversal",
    "counter_direction_excursion_share",
    "counter_direction_undefined",
)
UPDATE_LOSS_TRIGGER = 0.6931471805599453


def _bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_bytes(value)).hexdigest()


def feature_contract_digest() -> str:
    return _digest(
        {
            "version": "head2-features-v2",
            "columns": FEATURE_NAMES,
            "direction_and_center": "Decimal.copy_abs; original revision retained",
            "width_change": "last20_width/preceding_nonoverlapping20_width-1",
            "center_shift": "last20_midpoint/preceding20_midpoint-1",
            "path_contract": PATH_CONTRACT_DIGEST,
            "zero_net_direction": "counter slot 0 with explicit mask 1; not missing data",
            "missing_source": "unavailable, never imputed",
            "scope": "2012-2021 retrospective T-only; no historical-as-known claim",
        }
    )


@dataclass(frozen=True)
class FactValue:
    name: str
    value: Decimal | None
    reason: str
    revision_digest: str
    market_date: str


@dataclass(frozen=True)
class FeatureEncoding:
    values: tuple[float, ...] | None
    digest: str
    reason: str


def _day(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value or not date(2012, 1, 1) <= parsed <= date(2021, 12, 31):
            raise ValueError("date scope")
        return parsed
    except (TypeError, ValueError) as exc:
        raise P5AssessmentError("head2_revision_date_scope") from exc


def encode_features(
    facts: Sequence[FactValue], *, anchor: str, source_digest: str
) -> FeatureEncoding:
    """Encode admitted fact revisions without modifying their exact Decimal values."""
    end = _day(anchor)
    require_digest(source_digest)
    if len(facts) != len(FACT_NAMES) or {f.name for f in facts} != set(FACT_NAMES):
        raise P5AssessmentError("head2_revision_fact_set")
    by_name = {fact.name: fact for fact in facts}
    ordered = [by_name[name] for name in FACT_NAMES]
    for fact in ordered:
        require_digest(fact.revision_digest)
        if _day(fact.market_date) != end:
            raise P5AssessmentError("head2_revision_fact_anchor")
        if not isinstance(fact.reason, str) or not fact.reason:
            raise P5AssessmentError("head2_revision_fact_reason")
        if fact.value is not None:
            if not isinstance(fact.value, Decimal) or not fact.value.is_finite():
                raise P5AssessmentError("head2_revision_fact_numeric")
            if fact.reason != "available":
                raise P5AssessmentError("head2_revision_value_reason")
        elif fact.reason == "available" or (
            fact.reason == "zero_net_direction" and fact.name != FACT_NAMES[-1]
        ):
            raise P5AssessmentError("head2_revision_missing_reason")
    identity = _digest(
        {
            "contract": feature_contract_digest(),
            "source": source_digest,
            "anchor": anchor,
            "facts": [
                {**asdict(f), "value": None if f.value is None else str(f.value)} for f in ordered
            ],
        }
    )
    counter = ordered[-1]
    undefined = counter.value is None and counter.reason == "zero_net_direction"
    if any(f.value is None for f in ordered[:-1]) or (counter.value is None and not undefined):
        return FeatureEncoding(None, identity, "REQUIRED_FACT_UNAVAILABLE")
    if undefined and (ordered[0].value != 0 or ordered[4].value != 0):
        raise P5AssessmentError("head2_revision_zero_direction_conflict")
    values = tuple(
        float(f.value.copy_abs() if i in (0, 3) else f.value) if f.value is not None else 0.0
        for i, f in enumerate(ordered)
    ) + (float(undefined),)
    _features(values)
    return FeatureEncoding(
        values, identity, "AVAILABLE_MATHEMATICAL_MASK" if undefined else "AVAILABLE"
    )


def encode_reference_facts(
    primary: Mapping[str, Mapping[str, Any]],
    path: Mapping[str, Mapping[str, Any]],
    *,
    anchor: str,
    source_digest: str,
) -> FeatureEncoding:
    """Adapt the frozen reference-packet fact schema; no history or label reader.

    The caller pins the complete packet and binds its blind ID to an admitted T
    anchor. Packet visibility, role, source and time qualification stay external.
    """
    path_names = (
        "structure.geometry.range-traversal",
        "structure.geometry.counter-direction-excursion-share",
    )
    if set(primary) != set(FACT_NAMES[:4]) or set(path) != set(path_names):
        raise P5AssessmentError("head2_revision_reference_fact_set")
    facts = []
    records = [primary[name] for name in FACT_NAMES[:4]] + [path[name] for name in path_names]
    for name, record in zip(FACT_NAMES, records, strict=True):
        try:
            value = record["value"]
            if value is not None and not isinstance(value, str):
                raise ValueError("source feature must retain exact decimal text")
            facts.append(
                FactValue(
                    name,
                    Decimal(value) if value is not None else None,
                    record["reason"],
                    record["revision_id"],
                    anchor,
                )
            )
        except (InvalidOperation, KeyError, TypeError, ValueError) as exc:
            raise P5AssessmentError("head2_revision_reference_fact_value") from exc
    return encode_features(facts, anchor=anchor, source_digest=source_digest)


def _features(values: Sequence[float]) -> None:
    if len(values) != 7 or any(type(x) not in (float, int) or not math.isfinite(x) for x in values):
        raise P5AssessmentError("head2_revision_feature_numeric")
    a, width, change, center, traversal, counter, mask = values
    if (
        not 0 <= a <= 1
        or width < 0
        or change < -1
        or center < 0
        or not 0 <= traversal <= 1
        or not 0 <= counter <= 1
        or mask not in (0, 1)
        or (mask == 1 and (counter != 0 or a != 0 or traversal != 0))
    ):
        raise P5AssessmentError("head2_revision_feature_range_or_mask")


def revision_policy(update_loss_trigger: float = UPDATE_LOSS_TRIGGER) -> HeadPolicy:
    result = HeadPolicy(FEATURE_NAMES, 200, 100, 0.05, 0.01, update_loss_trigger)
    result.validate()
    return result


def _ordered(rows: Sequence[HeadRow], maximum: int) -> tuple[HeadRow, ...]:
    ordered = legacy._rows(rows, len(FEATURE_NAMES))
    if len(ordered) > maximum:
        raise P5AssessmentError("head2_revision_row_budget")
    for row in ordered:
        _features(row.features)
    return ordered


def _rows_digest(rows: Sequence[HeadRow]) -> str:
    return _digest([asdict(row) for row in rows])


def validate_roles(
    train: Sequence[HeadRow],
    update: Sequence[HeadRow],
    guard: Sequence[HeadRow],
    evaluation: Sequence[HeadRow] = (),
) -> None:
    """Guard is development-only; it never enters a gradient or final evaluation."""
    groups = [_ordered(train, 24), _ordered(update, 8), _ordered(guard, 8)]
    if evaluation:
        groups.append(_ordered(evaluation, 24))
    if {row.label for row in groups[0]} != {0, 1}:
        raise P5AssessmentError("head2_revision_train_classes")
    if any(sum(row.label == label for row in groups[2]) < 2 for label in (0, 1)):
        raise P5AssessmentError("head2_revision_guard_classes")
    for i, group in enumerate(groups):
        for other in groups[:i]:
            for field in ("sample_id", "security_id", "episode_id"):
                if {getattr(r, field) for r in group} & {getattr(r, field) for r in other}:
                    raise P5AssessmentError("head2_revision_role_overlap")
    if min(r.anchor for r in groups[1]) <= max(r.anchor for r in groups[0]):
        raise P5AssessmentError("head2_revision_update_time_order")
    if min(r.anchor for r in groups[2]) <= max(r.anchor for r in groups[0]) or max(
        r.anchor for r in groups[2]
    ) > max(r.anchor for r in groups[1]):
        raise P5AssessmentError("head2_revision_guard_time_order")
    if evaluation and min(r.anchor for r in groups[3]) <= max(
        r.anchor for group in groups[:3] for r in group
    ):
        raise P5AssessmentError("head2_revision_evaluation_time_order")


def _parent(parent: HeadCheckpoint) -> None:
    if not isinstance(parent, HeadCheckpoint):
        raise P5AssessmentError("head2_revision_requires_original_parent")
    parent.validate()
    if parent.generation != 0 or not parent.complete:
        raise P5AssessmentError("head2_revision_requires_complete_initial_fit")
    if parent.bindings.features != feature_contract_digest():
        raise P5AssessmentError("head2_revision_feature_binding")
    if parent.policy != revision_policy(parent.policy.update_loss_trigger):
        raise P5AssessmentError("head2_revision_policy_binding")


@dataclass(frozen=True)
class GuardedUpdate:
    parent: HeadCheckpoint
    weights: tuple[float, ...]
    bias: float
    cursor: int
    train_digest: str
    update_digest: str
    guard_digest: str

    @property
    def complete(self) -> bool:
        return self.cursor == 100

    def validate(self) -> None:
        _parent(self.parent)
        for value in (self.train_digest, self.update_digest, self.guard_digest):
            require_digest(value)
        if self.train_digest != self.parent.rows_digest:
            raise P5AssessmentError("head2_revision_train_binding")
        if type(self.cursor) is not int or not 0 <= self.cursor <= 100:
            raise P5AssessmentError("head2_revision_cursor")
        if len(self.weights) != 7 or any(
            type(x) not in (float, int) or not math.isfinite(x) for x in (*self.weights, self.bias)
        ):
            raise P5AssessmentError("head2_revision_parameters")


def _inputs(
    parent: HeadCheckpoint,
    train: Sequence[HeadRow],
    update: Sequence[HeadRow],
    guard: Sequence[HeadRow],
) -> tuple[tuple[HeadRow, ...], tuple[HeadRow, ...], tuple[HeadRow, ...]]:
    _parent(parent)
    validate_roles(train, update, guard)
    result = (_ordered(train, 24), _ordered(update, 8), _ordered(guard, 8))
    if _rows_digest(result[0]) != parent.rows_digest or parent.uses != legacy._uses(
        result[0], 0, "train"
    ):
        raise P5AssessmentError("head2_revision_original_train_changed")
    return result


def begin_update(
    parent: HeadCheckpoint,
    train: Sequence[HeadRow],
    update: Sequence[HeadRow],
    guard: Sequence[HeadRow],
    *,
    diagnostic: DevelopmentDiagnostic,
    stop_after: int | None = None,
) -> GuardedUpdate | None:
    ordered = _inputs(parent, train, update, guard)
    if diagnose_head(parent, ordered[1]) != diagnostic:
        raise P5AssessmentError("head2_revision_update_diagnostic_binding")
    if diagnostic.raw_log_loss < parent.policy.update_loss_trigger:
        return None
    checkpoint = GuardedUpdate(
        parent, parent.weights, parent.bias, 0, *(_rows_digest(group) for group in ordered)
    )
    return resume_update(checkpoint, *ordered, stop_after=stop_after)


def _vector(parent: HeadCheckpoint, features: Sequence[float]) -> tuple[float, ...]:
    _features(features)
    result = tuple(
        (v - m) / s for v, m, s in zip(features, parent.means, parent.scales, strict=True)
    )
    if any(not math.isfinite(v) for v in result):
        raise P5AssessmentError("head2_revision_scaled_nonfinite")
    return result


def _logit(weights: Sequence[float], values: Sequence[float], bias: float) -> float:
    try:
        result = math.fsum(w * v for w, v in zip(weights, values, strict=True)) + bias
        if not math.isfinite(result):
            raise ValueError("nonfinite logit")
        return result
    except (OverflowError, ValueError) as exc:
        raise P5AssessmentError("head2_revision_logit_overflow") from exc


def _sigmoid(value: float) -> float:
    exp = math.exp(-abs(value))
    return 1 / (1 + exp) if value >= 0 else exp / (1 + exp)


def resume_update(
    checkpoint: GuardedUpdate,
    train: Sequence[HeadRow],
    update: Sequence[HeadRow],
    guard: Sequence[HeadRow],
    *,
    stop_after: int | None = None,
) -> GuardedUpdate:
    checkpoint.validate()
    ordered = _inputs(checkpoint.parent, train, update, guard)
    if tuple(_rows_digest(group) for group in ordered) != (
        checkpoint.train_digest,
        checkpoint.update_digest,
        checkpoint.guard_digest,
    ):
        raise P5AssessmentError("head2_revision_resume_input_changed")
    end = 100 if stop_after is None else stop_after
    if type(end) is not int or not checkpoint.cursor <= end <= 100:
        raise P5AssessmentError("head2_revision_step_boundary")
    groups = ordered[:2]  # The protection set is not a gradient input.
    matrices = [tuple(_vector(checkpoint.parent, row.features) for row in g) for g in groups]
    weights, bias = list(checkpoint.weights), checkpoint.bias
    try:
        for _ in range(checkpoint.cursor, end):
            errors = [
                tuple(
                    _sigmoid(_logit(weights, v, bias)) - row.label
                    for row, v in zip(g, vs, strict=True)
                )
                for g, vs in zip(groups, matrices, strict=True)
            ]
            gradient = [
                math.fsum(
                    0.5 * math.fsum(e * v[i] for e, v in zip(es, vs, strict=True)) / len(es)
                    for es, vs in zip(errors, matrices, strict=True)
                )
                for i in range(7)
            ]
            weights = [w - 0.05 * (g + 0.01 * w) for w, g in zip(weights, gradient, strict=True)]
            bias -= 0.05 * math.fsum(0.5 * math.fsum(es) / len(es) for es in errors)
        result = replace(checkpoint, weights=tuple(weights), bias=bias, cursor=end)
        result.validate()
        return result
    except (OverflowError, ValueError) as exc:
        raise P5AssessmentError("head2_revision_gradient_nonfinite") from exc


def update_score(checkpoint: GuardedUpdate, features: Sequence[float | None]) -> AssessmentOutput:
    checkpoint.validate()
    if not checkpoint.complete:
        raise P5AssessmentError("head2_revision_incomplete_update")
    if len(features) != 7:
        raise P5AssessmentError("head2_revision_feature_width")
    available = all(value is not None for value in features)
    raw = (
        _sigmoid(
            _logit(
                checkpoint.weights,
                _vector(checkpoint.parent, tuple(value for value in features if value is not None)),
                checkpoint.bias,
            )
        )
        if available
        else None
    )
    return resolve_output(
        historical_stage_code=2,
        applicability=True,
        raw_score=raw,
        calibrated_probability=None,
        required_facts_available=available,
    )


@dataclass(frozen=True)
class RetentionReport:
    accepted: bool
    reasons: tuple[str, ...]
    positive_errors_before: int
    positive_errors_after: int
    negative_errors_before: int
    negative_errors_after: int
    update_loss_before: float
    update_loss_after: float
    consumer_admission: str = "NOT_GRANTED"


def retention_report(
    checkpoint: GuardedUpdate,
    train: Sequence[HeadRow],
    update: Sequence[HeadRow],
    guard: Sequence[HeadRow],
) -> RetentionReport:
    """A pre-evaluation version choice, never a fresh generalization claim."""
    checkpoint.validate()
    if not checkpoint.complete:
        raise P5AssessmentError("head2_revision_incomplete_update")
    resume_update(checkpoint, train, update, guard)  # Exact input binding; zero further steps.
    before, after = [], []
    for row in guard:
        v = _vector(checkpoint.parent, row.features)
        before.append(_sigmoid(_logit(checkpoint.parent.weights, v, checkpoint.parent.bias)))
        after.append(_sigmoid(_logit(checkpoint.weights, v, checkpoint.bias)))
    errors = [
        sum(
            (score >= 0.5) != row.label
            for row, score in zip(guard, scores, strict=True)
            if row.label == label
        )
        for label in (1, 0)
        for scores in (before, after)
    ]

    def loss(weights: Sequence[float], bias: float) -> float:
        terms = []
        for row in update:
            z = _logit(weights, _vector(checkpoint.parent, row.features), bias)
            terms.append(max(z, 0) - row.label * z + math.log1p(math.exp(-abs(z))))
        return math.fsum(terms) / len(terms)

    old_loss = loss(checkpoint.parent.weights, checkpoint.parent.bias)
    new_loss = loss(checkpoint.weights, checkpoint.bias)
    reasons = tuple(
        reason
        for failed, reason in (
            (errors[1] > errors[0], "POSITIVE_RETENTION_REGRESSION"),
            (errors[3] > errors[2], "NEGATIVE_RETENTION_REGRESSION"),
            (new_loss > old_loss, "UPDATE_LOSS_REGRESSION"),
        )
        if failed
    )
    return RetentionReport(
        not reasons, reasons, errors[0], errors[1], errors[2], errors[3], old_loss, new_loss
    )


def dump_update(checkpoint: GuardedUpdate) -> bytes:
    checkpoint.validate()
    return _bytes(
        {
            "schema": "quanthus.head2.guarded-update.v2",
            **asdict(checkpoint),
            "parent": json.loads(dump_checkpoint(checkpoint.parent)),
        }
    )


def load_update(
    raw: bytes, *, expected_sha256: str, bindings: HeadBindings, parent_digest: str
) -> GuardedUpdate:
    require_digest(expected_sha256)
    require_digest(parent_digest)
    if "sha256:" + hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise P5AssessmentError("head2_revision_checkpoint_bytes")
    try:
        value: dict[str, Any] = json.loads(raw)
        if value.pop("schema") != "quanthus.head2.guarded-update.v2":
            raise ValueError("schema")
        parent_raw = _bytes(value.pop("parent"))
        parent = load_checkpoint(
            parent_raw,
            expected_sha256="sha256:" + hashlib.sha256(parent_raw).hexdigest(),
            bindings=bindings,
        )
        if parent.digest != parent_digest:
            raise ValueError("parent binding")
        result = GuardedUpdate(parent=parent, weights=tuple(value.pop("weights")), **value)
        result.validate()
        if dump_update(result) != raw:
            raise ValueError("canonical encoding")
        return result
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        raise P5AssessmentError("head2_revision_checkpoint_invalid") from exc

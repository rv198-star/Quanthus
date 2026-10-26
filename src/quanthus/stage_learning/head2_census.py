"""Separate bounded census protocol; frozen V2 role caps remain unchanged.

This module admits a caller-frozen cohort, never opens sources or grants research
authority. It reuses the seven-feature numeric kernels without post-label sampling.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date
from typing import Any

from quanthus.stage_learning import head2_revision as v2
from quanthus.stage_learning import local_head as learner
from quanthus.stage_learning.local_head import HeadBindings, HeadCheckpoint, HeadRow
from quanthus.stage_learning.p5_assessment import P5AssessmentError
from quanthus.stage_learning.p5_contracts import require_digest

VERSION = "head2-fixed-census-v1"
ROLE_CAPS = {"TRAIN": 20, "GUARD": 32, "UPDATE": 16, "EVALUATE": 80}


def _bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_bytes(value)).hexdigest()


def protocol_digest() -> str:
    return _digest(
        {
            "version": VERSION,
            "caps": ROLE_CAPS,
            "features": v2.feature_contract_digest(),
            "inclusion": "all-source-qualified-definite-consensus",
            "threshold": 0.5,
            "minimum": {"TRAIN": [12, 4], "GUARD": [4, 2], "UPDATE": [1, 0], "EVALUATE": [12, 5]},
            "evaluation_each_block_each_class": 2,
        }
    )


def eligibility_binding(request_digest: str) -> str:
    require_digest(request_digest)
    return _digest({"request": request_digest, "protocol": protocol_digest()})


@dataclass(frozen=True)
class CensusSlot:
    sample_id: str
    company_id: str
    security_id: str
    listing_id: str
    episode_id: str
    anchor: str
    time_block: str
    role: str


@dataclass(frozen=True)
class CensusOpinion:
    slot: CensusSlot
    source_available: bool
    features: tuple[float, ...] | None
    opinion_a: str | None
    opinion_b: str | None


def validate_slots(slots: Sequence[CensusSlot]) -> None:
    """The full frozen148 slots include20 explicitly reused development cases."""
    if Counter(s.role for s in slots) != ROLE_CAPS:
        raise P5AssessmentError("census_exact_role_slots")
    for field in ("sample_id", "company_id", "security_id", "listing_id", "episode_id"):
        values = [getattr(s, field) for s in slots]
        if any(not isinstance(v, str) or not v for v in values) or len(set(values)) != len(values):
            raise P5AssessmentError("census_identity_overlap")
    for s in slots:
        try:
            day = date.fromisoformat(s.anchor)
            if day.isoformat() != s.anchor or not date(2012, 1, 1) <= day <= date(2021, 12, 31):
                raise ValueError("scope")
        except (TypeError, ValueError) as exc:
            raise P5AssessmentError("census_date_scope") from exc
        if not isinstance(s.time_block, str) or not s.time_block:
            raise P5AssessmentError("census_time_block")
    groups = {role: [s for s in slots if s.role == role] for role in ROLE_CAPS}
    if (
        max(s.anchor for s in groups["TRAIN"]) >= min(s.anchor for s in groups["GUARD"])
        or max(s.anchor for s in groups["GUARD"]) >= min(s.anchor for s in groups["UPDATE"])
        or max(s.anchor for s in groups["UPDATE"]) >= min(s.anchor for s in groups["EVALUATE"])
    ):
        raise P5AssessmentError("census_time_order")
    expected = {
        "GUARD": {"2021-05": 16, "2021-06": 16},
        "UPDATE": {"2021-08": 16},
        "EVALUATE": {"2021-11": 40, "2021-12": 40},
    }
    for role, blocks in expected.items():
        if Counter(s.time_block for s in groups[role]) != blocks or any(
            not s.anchor.startswith(s.time_block + "-") for s in groups[role]
        ):
            raise P5AssessmentError("census_fixed_time_blocks")


def admit_role(
    slots: Sequence[CensusSlot],
    opinions: Sequence[CensusOpinion],
    role: str,
) -> tuple[HeadRow, ...]:
    """Every available definite consensus enters its frozen role, or support fails."""
    validate_slots(slots)
    expected = {s.sample_id: s for s in slots if s.role == role}
    if (
        not expected
        or len(opinions) != len(expected)
        or {o.slot.sample_id for o in opinions} != set(expected)
    ):
        raise P5AssessmentError("census_reference_slot_set")
    rows, labels = [], {}
    for o in opinions:
        if o.slot != expected[o.slot.sample_id] or type(o.source_available) is not bool:
            raise P5AssessmentError("census_reference_identity")
        if not o.source_available:
            if o.features is not None or o.opinion_a is not None or o.opinion_b is not None:
                raise P5AssessmentError("census_unavailable_has_evidence")
            continue
        if o.features is None or any(
            label not in ("POSITIVE", "NEGATIVE", "UNKNOWN") for label in (o.opinion_a, o.opinion_b)
        ):
            raise P5AssessmentError("census_reference_incomplete")
        v2._features(o.features)
        if o.opinion_a != o.opinion_b or o.opinion_a == "UNKNOWN":
            continue
        label = int(o.opinion_a == "POSITIVE")
        s = o.slot
        rows.append(HeadRow(s.sample_id, s.company_id, s.episode_id, s.anchor, o.features, label))
        labels[s.sample_id] = label
    counts = Counter(row.label for row in rows)
    total, each = {"TRAIN": (12, 4), "GUARD": (4, 2), "UPDATE": (1, 0), "EVALUATE": (12, 5)}[role]
    if len(rows) < total or any(counts[label] < each for label in (0, 1)):
        raise P5AssessmentError("census_role_support")
    blocks = {s.time_block for s in expected.values()}
    if (
        role in ("TRAIN", "EVALUATE")
        and len({expected[row.sample_id].time_block for row in rows}) < 2
    ):
        raise P5AssessmentError("census_time_support")
    if role == "GUARD" and any(
        not any(expected[row.sample_id].time_block == block for row in rows) for block in blocks
    ):
        raise P5AssessmentError("census_time_support")
    if role == "EVALUATE" and any(
        sum(expected[row.sample_id].time_block == block and row.label == label for row in rows) < 2
        for block in blocks
        for label in (0, 1)
    ):
        raise P5AssessmentError("census_time_support")
    return tuple(sorted(rows, key=lambda row: row.sample_id))


def _ordered(rows: Sequence[HeadRow], role: str) -> tuple[HeadRow, ...]:
    ordered = learner._rows(rows, 7)
    if len(ordered) > ROLE_CAPS[role]:
        raise P5AssessmentError("census_row_budget")
    for row in ordered:
        v2._features(row.features)
    return ordered


def fit_census(train: Sequence[HeadRow], *, bindings: HeadBindings) -> HeadCheckpoint:
    bindings.validate()
    if bindings.features != v2.feature_contract_digest() or bindings.eligibility != (
        eligibility_binding(bindings.request)
    ):
        raise P5AssessmentError("census_protocol_binding")
    ordered = _ordered(train, "TRAIN")
    if len(ordered) < 12 or any(sum(r.label == label for r in ordered) < 4 for label in (0, 1)):
        raise P5AssessmentError("census_train_support")
    return learner.fit_head(ordered, policy=v2.revision_policy(), bindings=bindings)


def development_spend_gate(parent: HeadCheckpoint, train: Sequence[HeadRow]) -> dict[str, Any]:
    _parent(parent)
    ordered = _ordered(train, "TRAIN")
    if learner._row_digest(ordered) != parent.rows_digest:
        raise P5AssessmentError("census_train_binding")
    positive = negative = 0
    for row in ordered:
        score = learner.predict_head(parent, row.features).raw_score
        if score is None:
            raise P5AssessmentError("census_missing_score")
        positive += int(row.label == 1 and score >= 0.5)
        negative += int(row.label == 0 and score < 0.5)
    return {
        "pass": positive > 0 and negative > 0,
        "true_positive": positive,
        "true_negative": negative,
        "rows": len(ordered),
        "threshold": 0.5,
        "reason": "CONTINUE" if positive and negative else "DEVELOPMENT_NONDISCRIMINATION_STOP",
        "independent_evaluation": False,
    }


def _parent(parent: HeadCheckpoint) -> None:
    v2._parent(parent)
    if parent.bindings.eligibility != eligibility_binding(parent.bindings.request):
        raise P5AssessmentError("census_protocol_binding")


def _inputs(
    parent: HeadCheckpoint,
    train: Sequence[HeadRow],
    update: Sequence[HeadRow],
    guard: Sequence[HeadRow],
) -> tuple[tuple[HeadRow, ...], ...]:
    _parent(parent)
    groups = tuple(
        _ordered(rows, role)
        for rows, role in ((train, "TRAIN"), (update, "UPDATE"), (guard, "GUARD"))
    )
    if learner._row_digest(groups[0]) != parent.rows_digest:
        raise P5AssessmentError("census_train_binding")
    if any(sum(r.label == label for r in groups[2]) < 2 for label in (0, 1)):
        raise P5AssessmentError("census_guard_support")
    for i, group in enumerate(groups):
        for other in groups[:i]:
            if any(
                {getattr(r, key) for r in group} & {getattr(r, key) for r in other}
                for key in ("sample_id", "security_id", "episode_id")
            ):
                raise P5AssessmentError("census_role_overlap")
    if not (
        max(r.anchor for r in groups[0])
        < min(r.anchor for r in groups[2])
        <= max(r.anchor for r in groups[2])
        < min(r.anchor for r in groups[1])
    ):
        raise P5AssessmentError("census_time_order")
    return groups


@dataclass(frozen=True)
class CensusUpdate:
    parent: HeadCheckpoint
    weights: tuple[float, ...]
    bias: float
    cursor: int
    train_digest: str
    update_digest: str
    guard_digest: str
    protocol: str

    @property
    def complete(self) -> bool:
        return self.cursor == 100

    def validate(self) -> None:
        _parent(self.parent)
        if self.protocol != protocol_digest():
            raise P5AssessmentError("census_protocol_binding")
        for value in (self.train_digest, self.update_digest, self.guard_digest):
            require_digest(value)
        if (
            self.train_digest != self.parent.rows_digest
            or type(self.cursor) is not int
            or not (0 <= self.cursor <= 100)
            or len(self.weights) != 7
            or any(not math.isfinite(v) for v in (*self.weights, self.bias))
        ):
            raise P5AssessmentError("census_update_checkpoint")


def begin_update(
    parent: HeadCheckpoint,
    train: Sequence[HeadRow],
    update: Sequence[HeadRow],
    guard: Sequence[HeadRow],
    *,
    diagnostic: learner.DevelopmentDiagnostic,
) -> CensusUpdate | None:
    groups = _inputs(parent, train, update, guard)
    if learner.diagnose_head(parent, groups[1]) != diagnostic:
        raise P5AssessmentError("census_diagnostic_binding")
    if diagnostic.raw_log_loss < parent.policy.update_loss_trigger:
        return None
    checkpoint = CensusUpdate(
        parent,
        parent.weights,
        parent.bias,
        0,
        learner._row_digest(groups[0]),
        learner._row_digest(groups[1]),
        learner._row_digest(groups[2]),
        protocol_digest(),
    )
    return resume_update(checkpoint, *groups)


def resume_update(
    checkpoint: CensusUpdate,
    train: Sequence[HeadRow],
    update: Sequence[HeadRow],
    guard: Sequence[HeadRow],
    *,
    stop_after: int = 100,
) -> CensusUpdate:
    checkpoint.validate()
    groups = _inputs(checkpoint.parent, train, update, guard)
    if tuple(learner._row_digest(g) for g in groups) != (
        checkpoint.train_digest,
        checkpoint.update_digest,
        checkpoint.guard_digest,
    ):
        raise P5AssessmentError("census_resume_input_changed")
    if type(stop_after) is not int or not checkpoint.cursor <= stop_after <= 100:
        raise P5AssessmentError("census_step_boundary")
    matrices = [tuple(v2._vector(checkpoint.parent, r.features) for r in g) for g in groups[:2]]
    weights, bias = list(checkpoint.weights), checkpoint.bias
    for _ in range(checkpoint.cursor, stop_after):
        errors = [
            tuple(
                v2._sigmoid(v2._logit(weights, v, bias)) - r.label
                for r, v in zip(g, vs, strict=True)
            )
            for g, vs in zip(groups[:2], matrices, strict=True)
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
    result = replace(checkpoint, weights=tuple(weights), bias=bias, cursor=stop_after)
    result.validate()
    return result


def retention_report(
    checkpoint: CensusUpdate,
    train: Sequence[HeadRow],
    update: Sequence[HeadRow],
    guard: Sequence[HeadRow],
) -> v2.RetentionReport:
    if not checkpoint.complete:
        raise P5AssessmentError("census_incomplete_update")
    resume_update(checkpoint, train, update, guard)  # Binding validation, zero gradient steps.
    errors = []
    for label in (1, 0):
        for weights, bias in (
            (checkpoint.parent.weights, checkpoint.parent.bias),
            (checkpoint.weights, checkpoint.bias),
        ):
            errors.append(
                sum(
                    (
                        v2._sigmoid(
                            v2._logit(weights, v2._vector(checkpoint.parent, row.features), bias)
                        )
                        >= 0.5
                    )
                    != label
                    for row in guard
                    if row.label == label
                )
            )
    losses = []
    for weights, bias in (
        (checkpoint.parent.weights, checkpoint.parent.bias),
        (checkpoint.weights, checkpoint.bias),
    ):
        logits = [
            v2._logit(weights, v2._vector(checkpoint.parent, r.features), bias) for r in update
        ]
        losses.append(
            math.fsum(
                max(z, 0) - r.label * z + math.log1p(math.exp(-abs(z)))
                for z, r in zip(logits, update, strict=True)
            )
            / len(update)
        )
    reasons = tuple(
        reason
        for failed, reason in (
            (errors[1] > errors[0], "POSITIVE_RETENTION_REGRESSION"),
            (errors[3] > errors[2], "NEGATIVE_RETENTION_REGRESSION"),
            (losses[1] > losses[0], "UPDATE_LOSS_REGRESSION"),
        )
        if failed
    )
    return v2.RetentionReport(
        not reasons, reasons, errors[0], errors[1], errors[2], errors[3], losses[0], losses[1]
    )


def dump_update(checkpoint: CensusUpdate) -> bytes:
    checkpoint.validate()
    return _bytes(
        {
            "schema": VERSION + ".update",
            **asdict(checkpoint),
            "parent": json.loads(learner.dump_checkpoint(checkpoint.parent)),
        }
    )


def load_update(raw: bytes, *, expected_sha256: str, bindings: HeadBindings) -> CensusUpdate:
    require_digest(expected_sha256)
    if "sha256:" + hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise P5AssessmentError("census_checkpoint_bytes")
    try:
        value = json.loads(raw)
        if value.pop("schema") != VERSION + ".update":
            raise ValueError("schema")
        parent_raw = _bytes(value.pop("parent"))
        parent = learner.load_checkpoint(
            parent_raw,
            expected_sha256=("sha256:" + hashlib.sha256(parent_raw).hexdigest()),
            bindings=bindings,
        )
        result = CensusUpdate(parent, weights=tuple(value.pop("weights")), **value)
        result.validate()
        if dump_update(result) != raw:
            raise ValueError("canonical")
        return result
    except (TypeError, ValueError, KeyError, AttributeError, OverflowError) as exc:
        raise P5AssessmentError("census_invalid_checkpoint") from exc


def prediction_commitment(
    *,
    request: str,
    code: str,
    cohort: str,
    candidate: str,
    predictions: Mapping[str, float | None],
    slots: Sequence[CensusSlot],
) -> str:
    """Caller publishes this before opening EVALUATE opinions, then verifies unchanged."""
    for digest in (request, code, cohort, candidate):
        require_digest(digest)
    validate_slots(slots)
    if set(predictions) != {s.sample_id for s in slots if s.role == "EVALUATE"} or any(
        v is not None and (type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1)
        for v in predictions.values()
    ):
        raise P5AssessmentError("census_prediction_set")
    return _digest(
        {
            "protocol": protocol_digest(),
            "request": request,
            "code": code,
            "cohort": cohort,
            "candidate": candidate,
            "predictions": predictions,
        }
    )


def verify_prediction_commitment(expected: str, **inputs: Any) -> None:
    require_digest(expected)
    if prediction_commitment(**inputs) != expected:
        raise P5AssessmentError("census_prediction_commitment_changed")

"""Small head-2 learning primitives; no reader, run admission, or calibration.

The caller must separately qualify sources, reserved identities, references and splits.
Synthetic execution of this module does not accept a real learning run or LOCAL-001.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date
from typing import Any

from quanthus.stage_learning.p5_assessment import (
    AssessmentOutput,
    P5AssessmentError,
    resolve_output,
)
from quanthus.stage_learning.p5_contracts import require_digest


def _bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_bytes(value)).hexdigest()


def _finite(values: Sequence[float]) -> None:
    if any(type(v) not in (float, int) or not math.isfinite(v) for v in values):
        raise P5AssessmentError("local_head_nonfinite_or_non_numeric")


@dataclass(frozen=True)
class HeadBindings:
    request: str
    source: str
    reference: str
    eligibility: str
    split: str
    features: str
    target: str
    code: str

    def validate(self) -> None:
        for value in asdict(self).values():
            require_digest(value)


@dataclass(frozen=True)
class HeadPolicy:
    """Explicit parameters, not a frozen scientific model or activation policy."""

    feature_names: tuple[str, ...]
    fit_steps: int
    update_steps: int
    learning_rate: float
    l2: float
    update_loss_trigger: float

    def validate(self) -> None:
        if (
            not 1 <= len(self.feature_names) <= 24
            or len(set(self.feature_names)) != len(self.feature_names)
            or any(not isinstance(n, str) or not n for n in self.feature_names)
        ):
            raise P5AssessmentError("local_head_feature_contract")
        if any(
            type(n) is not int or not 1 <= n <= 1000 for n in (self.fit_steps, self.update_steps)
        ):
            raise P5AssessmentError("local_head_step_budget")
        _finite((self.learning_rate, self.l2, self.update_loss_trigger))
        if not 0 < self.learning_rate <= 1 or self.l2 < 0 or self.update_loss_trigger < 0:
            raise P5AssessmentError("local_head_optimizer_policy")


@dataclass(frozen=True)
class HeadRow:
    sample_id: str
    security_id: str
    episode_id: str
    anchor: str
    features: tuple[float, ...]
    label: int


def _identity(sample_id: str, security_id: str, episode_id: str, anchor: str) -> None:
    if not all(isinstance(x, str) and x for x in (sample_id, security_id, episode_id, anchor)):
        raise P5AssessmentError("local_head_sample_identity")
    try:
        day = date.fromisoformat(anchor)
    except ValueError as exc:
        raise P5AssessmentError("local_head_anchor") from exc
    if anchor != day.isoformat() or not date(2012, 1, 1) <= day <= date(2021, 12, 31):
        raise P5AssessmentError("local_head_protected_date")


def _rows(rows: Sequence[HeadRow], width: int) -> tuple[HeadRow, ...]:
    if not rows or len(rows) > 128 or len({r.sample_id for r in rows}) != len(rows):
        raise P5AssessmentError("local_head_empty_duplicate_or_excess_rows")
    for row in rows:
        _identity(row.sample_id, row.security_id, row.episode_id, row.anchor)
        if type(row.label) is not int or row.label not in (0, 1):
            raise P5AssessmentError("local_head_requires_definite_label")
        if len(row.features) != width:
            raise P5AssessmentError("local_head_feature_width")
        _finite(row.features)
    return tuple(sorted(rows, key=lambda row: row.sample_id))


def _row_digest(rows: Sequence[HeadRow]) -> str:
    return _digest([asdict(row) for row in rows])


@dataclass(frozen=True)
class RowUse:
    sample_id: str
    security_id: str
    episode_id: str
    anchor: str
    generation: int
    role: str


def _uses(rows: Sequence[HeadRow], generation: int, role: str) -> tuple[RowUse, ...]:
    return tuple(
        RowUse(r.sample_id, r.security_id, r.episode_id, r.anchor, generation, role) for r in rows
    )


@dataclass(frozen=True)
class HeadCheckpoint:
    policy: HeadPolicy
    bindings: HeadBindings
    means: tuple[float, ...]
    scales: tuple[float, ...]
    weights: tuple[float, ...]
    bias: float
    cursor: int
    generation: int
    rows_digest: str
    uses: tuple[RowUse, ...]
    parent_digest: str | None = None

    @property
    def target_steps(self) -> int:
        return self.policy.fit_steps if self.generation == 0 else self.policy.update_steps

    @property
    def complete(self) -> bool:
        return self.cursor == self.target_steps

    def validate(self) -> None:
        self.policy.validate()
        self.bindings.validate()
        require_digest(self.rows_digest)
        if type(self.generation) is not int or self.generation not in (0, 1):
            raise P5AssessmentError("local_head_generation")
        if (self.generation == 0) != (self.parent_digest is None):
            raise P5AssessmentError("local_head_parent")
        if self.parent_digest is not None:
            require_digest(self.parent_digest)
        if type(self.cursor) is not int or not 0 <= self.cursor <= self.target_steps:
            raise P5AssessmentError("local_head_cursor")
        width = len(self.policy.feature_names)
        if any(len(v) != width for v in (self.means, self.scales, self.weights)):
            raise P5AssessmentError("local_head_checkpoint_shape")
        _finite((*self.means, *self.scales, *self.weights, self.bias))
        if any(v <= 0 for v in self.scales) or not self.uses:
            raise P5AssessmentError("local_head_checkpoint_scaler_or_consumption")
        _validate_uses(self.uses, self.generation)

    @property
    def digest(self) -> str:
        self.validate()
        return _digest(asdict(self))


def _validate_uses(uses: Sequence[RowUse], generation: int) -> None:
    if len(uses) > 384:
        raise P5AssessmentError("local_head_consumption_budget")
    allowed = (
        {(0, "train")}
        if generation == 0
        else {(0, "train"), (0, "development_validation"), (1, "incremental_train")}
    )
    seen: set[tuple[str, int, str]] = set()
    groups: dict[str, dict[str, RowUse]] = {}
    for use in uses:
        _identity(use.sample_id, use.security_id, use.episode_id, use.anchor)
        if type(use.generation) is not int or (use.generation, use.role) not in allowed:
            raise P5AssessmentError("local_head_consumption_role")
        key = (use.sample_id, use.generation, use.role)
        if key in seen:
            raise P5AssessmentError("local_head_duplicate_consumption")
        seen.add(key)
        groups.setdefault(use.role, {})[use.sample_id] = use
    if not groups.get("train"):
        raise P5AssessmentError("local_head_missing_train_consumption")
    if generation == 1:
        development = groups.get("development_validation", {})
        incremental = groups.get("incremental_train", {})
        if not development or development.keys() != incremental.keys():
            raise P5AssessmentError("local_head_update_consumption_pair")
        for sample_id, use in development.items():
            if replace(use, generation=1, role="incremental_train") != incremental[sample_id]:
                raise P5AssessmentError("local_head_update_consumption_identity")
        _disjoint_after(tuple(development.values()), tuple(groups["train"].values()))


def _disjoint_after(rows: Sequence[HeadRow] | Sequence[RowUse], uses: Sequence[RowUse]) -> None:
    for field in ("sample_id", "security_id", "episode_id"):
        if {getattr(r, field) for r in rows} & {getattr(u, field) for u in uses}:
            raise P5AssessmentError("local_head_consumption_overlap")
    if min(r.anchor for r in rows) <= max(u.anchor for u in uses):
        raise P5AssessmentError("local_head_time_order")


def dump_checkpoint(checkpoint: HeadCheckpoint) -> bytes:
    checkpoint.validate()
    return _bytes({"schema": "quanthus.local-head2.checkpoint.v1", **asdict(checkpoint)})


def load_checkpoint(raw: bytes, *, expected_sha256: str, bindings: HeadBindings) -> HeadCheckpoint:
    """Decode JSON only; the expected byte hash and bindings come from the caller's ledger."""
    require_digest(expected_sha256)
    if "sha256:" + hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise P5AssessmentError("local_head_checkpoint_bytes")
    try:
        value = json.loads(raw)
        if value.pop("schema") != "quanthus.local-head2.checkpoint.v1":
            raise ValueError("schema")
        policy = value.pop("policy")
        policy["feature_names"] = tuple(policy["feature_names"])
        result = HeadCheckpoint(
            policy=HeadPolicy(**policy),
            bindings=HeadBindings(**value.pop("bindings")),
            means=tuple(value.pop("means")),
            scales=tuple(value.pop("scales")),
            weights=tuple(value.pop("weights")),
            uses=tuple(RowUse(**u) for u in value.pop("uses")),
            **value,
        )
        result.validate()
        if result.bindings != bindings:
            raise P5AssessmentError("local_head_checkpoint_binding")
        # Reject duplicate keys, lossy coercions and noncanonical envelopes as well.
        if dump_checkpoint(result) != raw:
            raise P5AssessmentError("local_head_checkpoint_encoding")
        return result
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise P5AssessmentError("local_head_checkpoint_invalid") from exc


def _sigmoid(value: float) -> float:
    exp = math.exp(-abs(value))
    return 1 / (1 + exp) if value >= 0 else exp / (1 + exp)


def _vector(checkpoint: HeadCheckpoint, values: Sequence[float]) -> tuple[float, ...]:
    if len(values) != len(checkpoint.means):
        raise P5AssessmentError("local_head_feature_width")
    _finite(values)
    result = tuple(
        (v - mean) / scale
        for v, mean, scale in zip(values, checkpoint.means, checkpoint.scales, strict=True)
    )
    _finite(result)
    return result


def _raw(checkpoint: HeadCheckpoint, values: Sequence[float]) -> float:
    return _sigmoid(_logit(checkpoint.weights, _vector(checkpoint, values), checkpoint.bias))


def _logit(weights: Sequence[float], values: Sequence[float], bias: float) -> float:
    try:
        value = math.fsum(w * x for w, x in zip(weights, values, strict=True)) + bias
    except (OverflowError, ValueError) as exc:
        raise P5AssessmentError("local_head_logit_overflow") from exc
    _finite((value,))
    return value


def resume_head(
    checkpoint: HeadCheckpoint, rows: Sequence[HeadRow], *, stop_after: int | None = None
) -> HeadCheckpoint:
    """Continue exact rows without refitting the scaler or repeating completed steps."""
    checkpoint.validate()
    ordered = _rows(rows, len(checkpoint.weights))
    if _row_digest(ordered) != checkpoint.rows_digest:
        raise P5AssessmentError("local_head_resume_input_changed")
    role = "train" if checkpoint.generation == 0 else "incremental_train"
    if _uses(ordered, checkpoint.generation, role) != tuple(
        u for u in checkpoint.uses if u.role == role
    ):
        raise P5AssessmentError("local_head_resume_consumption_changed")
    end = checkpoint.target_steps if stop_after is None else stop_after
    if type(end) is not int or not checkpoint.cursor <= end <= checkpoint.target_steps:
        raise P5AssessmentError("local_head_resume_step_boundary")
    matrix = [_vector(checkpoint, r.features) for r in ordered]
    weights, bias = list(checkpoint.weights), checkpoint.bias
    for _ in range(checkpoint.cursor, end):
        errors = [
            _sigmoid(_logit(weights, v, bias)) - row.label
            for row, v in zip(ordered, matrix, strict=True)
        ]
        weights = [
            w
            - checkpoint.policy.learning_rate
            * (
                math.fsum(e * v[j] for e, v in zip(errors, matrix, strict=True)) / len(ordered)
                + checkpoint.policy.l2 * w
            )
            for j, w in enumerate(weights)
        ]
        bias -= checkpoint.policy.learning_rate * math.fsum(errors) / len(ordered)
        _finite((*weights, bias))
    result = replace(checkpoint, weights=tuple(weights), bias=bias, cursor=end)
    result.validate()
    return result


def fit_head(
    rows: Sequence[HeadRow],
    *,
    policy: HeadPolicy,
    bindings: HeadBindings,
    stop_after: int | None = None,
) -> HeadCheckpoint:
    policy.validate()
    bindings.validate()
    ordered = _rows(rows, len(policy.feature_names))
    if {r.label for r in ordered} != {0, 1}:
        raise P5AssessmentError("local_head_initial_fit_requires_both_classes")
    columns = tuple(zip(*(r.features for r in ordered), strict=True))
    try:
        means = tuple(math.fsum(c) / len(c) for c in columns)
        scales = tuple(
            math.sqrt(math.fsum((v - mean) ** 2 for v in c) / len(c)) or 1.0
            for c, mean in zip(columns, means, strict=True)
        )
    except (OverflowError, ValueError) as exc:
        raise P5AssessmentError("local_head_scaler_overflow") from exc
    initial = HeadCheckpoint(
        policy,
        bindings,
        means,
        scales,
        (0.0,) * len(means),
        0.0,
        0,
        0,
        _row_digest(ordered),
        _uses(ordered, 0, "train"),
    )
    return resume_head(initial, ordered, stop_after=stop_after)


@dataclass(frozen=True)
class DevelopmentDiagnostic:
    parent_digest: str
    rows_digest: str
    raw_log_loss: float
    classes: tuple[int, ...]
    uses: tuple[RowUse, ...]

    def validate(self) -> None:
        require_digest(self.parent_digest)
        require_digest(self.rows_digest)
        _finite((self.raw_log_loss,))
        if (
            self.raw_log_loss < 0
            or not self.classes
            or any(type(c) is not int or c not in (0, 1) for c in self.classes)
            or tuple(sorted(set(self.classes))) != self.classes
        ):
            raise P5AssessmentError("local_head_diagnostic_values")
        if not 1 <= len(self.uses) <= 128 or len({u.sample_id for u in self.uses}) != len(
            self.uses
        ):
            raise P5AssessmentError("local_head_diagnostic_consumption")
        for use in self.uses:
            _identity(use.sample_id, use.security_id, use.episode_id, use.anchor)
            if (
                type(use.generation) is not int
                or use.generation != 0
                or use.role != ("development_validation")
            ):
                raise P5AssessmentError("local_head_diagnostic_consumption")


def dump_diagnostic(diagnostic: DevelopmentDiagnostic) -> bytes:
    """Persist development consumption even when its frozen update trigger does not fire."""
    diagnostic.validate()
    return _bytes({"schema": "quanthus.local-head2.development.v1", **asdict(diagnostic)})


def load_diagnostic(raw: bytes, *, expected_sha256: str) -> DevelopmentDiagnostic:
    require_digest(expected_sha256)
    if "sha256:" + hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise P5AssessmentError("local_head_diagnostic_bytes")
    try:
        value = json.loads(raw)
        if value.pop("schema") != "quanthus.local-head2.development.v1":
            raise ValueError("schema")
        result = DevelopmentDiagnostic(
            classes=tuple(value.pop("classes")),
            uses=tuple(RowUse(**u) for u in value.pop("uses")),
            **value,
        )
        result.validate()
        if dump_diagnostic(result) != raw:
            raise P5AssessmentError("local_head_diagnostic_encoding")
        return result
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise P5AssessmentError("local_head_diagnostic_invalid") from exc


def diagnose_head(checkpoint: HeadCheckpoint, rows: Sequence[HeadRow]) -> DevelopmentDiagnostic:
    checkpoint.validate()
    if not checkpoint.complete or checkpoint.generation != 0:
        raise P5AssessmentError("local_head_diagnostic_requires_complete_v0")
    ordered = _rows(rows, len(checkpoint.weights))
    _disjoint_after(ordered, checkpoint.uses)
    losses = []
    for row in ordered:
        z = _logit(checkpoint.weights, _vector(checkpoint, row.features), checkpoint.bias)
        loss = max(z, 0) - row.label * z + math.log1p(math.exp(-abs(z)))
        _finite((loss,))
        losses.append(loss)
    try:
        mean_loss = math.fsum(losses) / len(losses)
    except OverflowError as exc:
        raise P5AssessmentError("local_head_diagnostic_loss_overflow") from exc
    result = DevelopmentDiagnostic(
        checkpoint.digest,
        _row_digest(ordered),
        mean_loss,
        tuple(sorted({r.label for r in ordered})),
        _uses(ordered, 0, "development_validation"),
    )
    result.validate()
    return result


def update_head(
    checkpoint: HeadCheckpoint,
    rows: Sequence[HeadRow],
    *,
    diagnostic: DevelopmentDiagnostic,
    stop_after: int | None = None,
) -> HeadCheckpoint | None:
    """One update, only after the frozen v0 diagnostic trigger; no trigger means no update."""
    actual = diagnose_head(checkpoint, rows)
    if actual != diagnostic:
        raise P5AssessmentError("local_head_update_diagnostic_binding")
    if diagnostic.raw_log_loss < checkpoint.policy.update_loss_trigger:
        return None
    ordered = _rows(rows, len(checkpoint.weights))
    updated = HeadCheckpoint(
        checkpoint.policy,
        checkpoint.bindings,
        checkpoint.means,
        checkpoint.scales,
        checkpoint.weights,
        checkpoint.bias,
        0,
        1,
        _row_digest(ordered),
        checkpoint.uses
        + _uses(ordered, 0, "development_validation")
        + _uses(ordered, 1, "incremental_train"),
        checkpoint.digest,
    )
    return resume_head(updated, ordered, stop_after=stop_after)


def predict_head(checkpoint: HeadCheckpoint, features: Sequence[float | None]) -> AssessmentOutput:
    checkpoint.validate()
    if not checkpoint.complete:
        raise P5AssessmentError("local_head_incomplete_checkpoint")
    if len(features) != len(checkpoint.weights):
        raise P5AssessmentError("local_head_feature_width")
    available = all(v is not None for v in features)
    raw = _raw(checkpoint, tuple(v for v in features if v is not None)) if available else None
    return resolve_output(
        historical_stage_code=2,
        applicability=True,
        raw_score=raw,
        calibrated_probability=None,
        required_facts_available=available,
    )


def evaluation_summary(
    checkpoint: HeadCheckpoint,
    rows: Sequence[HeadRow],
    *,
    diagnostic: DevelopmentDiagnostic,
) -> dict[str, Any]:
    """Uncalibrated research diagnostics only; caller separately admits the reference."""
    checkpoint.validate()
    if not checkpoint.complete:
        raise P5AssessmentError("local_head_incomplete_checkpoint")
    diagnostic.validate()
    expected_parent = checkpoint.digest if checkpoint.generation == 0 else checkpoint.parent_digest
    if diagnostic.parent_digest != expected_parent:
        raise P5AssessmentError("local_head_evaluation_diagnostic_binding")
    if checkpoint.generation == 0:
        _disjoint_after(diagnostic.uses, checkpoint.uses)
    elif diagnostic.uses != tuple(u for u in checkpoint.uses if u.role == "development_validation"):
        raise P5AssessmentError("local_head_evaluation_diagnostic_consumption")
    ordered = _rows(rows, len(checkpoint.weights))
    _disjoint_after(ordered, checkpoint.uses + diagnostic.uses)
    positives = [_raw(checkpoint, r.features) for r in ordered if r.label == 1]
    negatives = [_raw(checkpoint, r.features) for r in ordered if r.label == 0]
    auc = (
        None
        if not positives or not negatives
        else math.fsum(float(p > n) + 0.5 * float(p == n) for p in positives for n in negatives)
        / (len(positives) * len(negatives))
    )
    return {
        "rows": len(ordered),
        "positive": len(positives),
        "negative": len(negatives),
        "raw_score_auc": auc,
        "calibrated_probability_metrics": None,
        "reason": "REQUIRED_CLASS_MISSING_INCONCLUSIVE"
        if auc is None
        else "UNCALIBRATED_RESEARCH_DIAGNOSTIC_ONLY",
        "consumer_admission": "NOT_GRANTED",
    }

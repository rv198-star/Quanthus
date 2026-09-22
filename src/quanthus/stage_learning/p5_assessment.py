"""Fail-closed primitives for the P5-STAGE-001 learned assessment route.

This module deliberately contains no market-data reader and no result-bearing training entrypoint.
It implements the synthetic contracts shared by L/G/T0/T1 before B, C, and D can be accepted.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any

REQUIRED_SEEDS = (17, 29, 43)
INELIGIBLE_HEADS = frozenset({7, 8, 9})


class P5AssessmentError(ValueError):
    """Raised when an assessment input violates the frozen P5 contract."""


class DecisionState(str, Enum):
    ASSERT_POSITIVE = "ASSERT_POSITIVE"
    ASSERT_NEGATIVE = "ASSERT_NEGATIVE"
    ABSTAIN = "ABSTAIN"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNAVAILABLE = "UNAVAILABLE"
    X = "X"


@dataclass(frozen=True)
class TemporalEncoderConfig:
    window_sessions: int = 120
    input_channels: int = 24
    patch_sessions: int = 5
    encoder_layers: int = 4
    d_model: int = 128
    attention_heads: int = 4
    ffn_dim: int = 512
    output_heads: int = 12
    maximum_parameters: int = 2_000_000

    def validate(self) -> None:
        if not 1 <= self.window_sessions <= 120:
            raise P5AssessmentError("window_sessions exceeds the frozen 120-session cap")
        if not 1 <= self.input_channels <= 24:
            raise P5AssessmentError("input_channels exceeds the frozen 24-channel cap")
        if self.patch_sessions != 5:
            raise P5AssessmentError("patch_sessions differs from the frozen value")
        if self.window_sessions % self.patch_sessions:
            raise P5AssessmentError("window_sessions must form complete non-overlapping patches")
        if (self.encoder_layers, self.d_model, self.attention_heads, self.ffn_dim) != (
            4,
            128,
            4,
            512,
        ):
            raise P5AssessmentError("sequence architecture differs from the frozen contract")
        if self.output_heads != 12 or self.d_model % self.attention_heads:
            raise P5AssessmentError("invalid frozen output or attention shape")
        if self.parameter_count() > self.maximum_parameters:
            raise P5AssessmentError("sequence encoder exceeds the two-million-parameter cap")

    def parameter_count(self) -> int:
        """Count the fixed encoder, including learned position/CLS and twelve sigmoid heads."""
        patches = self.window_sessions // self.patch_sessions
        patch_projection = self.patch_sessions * self.input_channels * self.d_model + self.d_model
        # Separate projections preserve real missingness and artificial SSL masking as distinct
        # channels without changing the governed input-channel count.
        missingness_projections = 2 * patch_projection
        position_and_cls = (patches + 2) * self.d_model
        attention = 4 * self.d_model * self.d_model + 4 * self.d_model
        feed_forward = (
            self.d_model * self.ffn_dim
            + self.ffn_dim
            + self.ffn_dim * self.d_model
            + self.d_model
        )
        layer_norms = 4 * self.d_model
        encoder = self.encoder_layers * (attention + feed_forward + layer_norms)
        heads = self.d_model * self.output_heads + self.output_heads
        return patch_projection + missingness_projections + position_and_cls + encoder + heads


@dataclass(frozen=True)
class FeatureCell:
    value: float | None
    actual_missing: bool = False
    artificial_mask: bool = False

    def validate(self) -> None:
        if self.actual_missing and self.artificial_mask:
            raise P5AssessmentError("actual missingness and artificial masking must stay distinct")
        if self.actual_missing != (self.value is None):
            raise P5AssessmentError("actual_missing must exactly describe a missing source value")
        if self.value is not None and not math.isfinite(self.value):
            raise P5AssessmentError("feature values must be finite")


@dataclass(frozen=True)
class FeatureWindow:
    session_dates: tuple[date, ...]
    cells: tuple[tuple[FeatureCell, ...], ...]
    feature_cutoff: date

    def validate(self, config: TemporalEncoderConfig) -> None:
        config.validate()
        if not self.session_dates or len(self.session_dates) > config.window_sessions:
            raise P5AssessmentError("feature window length is outside the frozen boundary")
        if tuple(sorted(self.session_dates)) != self.session_dates:
            raise P5AssessmentError("feature sessions must be ordered")
        if len(set(self.session_dates)) != len(self.session_dates):
            raise P5AssessmentError("feature sessions must be unique")
        if self.session_dates[-1] > self.feature_cutoff:
            raise P5AssessmentError("future session exceeds feature_cutoff")
        if len(self.cells) != len(self.session_dates):
            raise P5AssessmentError("feature rows and session dates differ")
        for row in self.cells:
            if len(row) != config.input_channels:
                raise P5AssessmentError("feature channel count differs from the model input")
            for cell in row:
                cell.validate()


def masked_binary_cross_entropy(
    probabilities: Sequence[Sequence[float]],
    labels: Sequence[Sequence[int | None]],
    applicability: Sequence[Sequence[bool | None]],
) -> float:
    """Return mean BCE over definite, applicable labels only.

    ``None`` labels include ambiguous, unjudgeable, and unreviewed references.  They are not
    negatives.  ``False`` and ``None`` applicability are also excluded rather than recoded.
    """
    if not (len(probabilities) == len(labels) == len(applicability)):
        raise P5AssessmentError("masked BCE batch dimensions differ")
    losses: list[float] = []
    for score_row, label_row, applicable_row in zip(
        probabilities, labels, applicability, strict=True
    ):
        if not (len(score_row) == len(label_row) == len(applicable_row) == 12):
            raise P5AssessmentError("masked BCE requires twelve aligned heads")
        for score, label, applies in zip(score_row, label_row, applicable_row, strict=True):
            if label is None or applies is not True:
                continue
            if label not in (0, 1):
                raise P5AssessmentError("definite labels must be binary")
            if not math.isfinite(score) or not 0.0 < score < 1.0:
                raise P5AssessmentError(
                    "BCE probabilities must be finite and strictly inside (0,1)"
                )
            losses.append(-(label * math.log(score) + (1 - label) * math.log(1.0 - score)))
    if not losses:
        raise P5AssessmentError("masked BCE has no definite applicable label")
    return math.fsum(losses) / len(losses)


def aggregate_seed_scores(scores: Mapping[int, Sequence[float]]) -> tuple[float, ...]:
    """Build the only confirmatory neural raw score from seeds 17/29/43."""
    if set(scores) != set(REQUIRED_SEEDS):
        raise P5AssessmentError("confirmatory primary requires exactly seeds 17, 29, and 43")
    widths = {len(value) for value in scores.values()}
    if len(widths) != 1:
        raise P5AssessmentError("seed score vectors are not aligned")
    width = widths.pop()
    if width != 12:
        raise P5AssessmentError("seed score vectors must contain twelve independent heads")
    output: list[float] = []
    for index in range(width):
        values = tuple(float(scores[seed][index]) for seed in REQUIRED_SEEDS)
        if any(not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in values):
            raise P5AssessmentError("seed score is outside the raw sigmoid range")
        output.append(math.fsum(values) / len(REQUIRED_SEEDS))
    return tuple(output)


@dataclass(frozen=True)
class AssessmentOutput:
    historical_stage_code: int
    applicability: bool | None
    raw_score: float | None
    calibrated_probability: float | None
    decision_state: DecisionState
    reason_codes: tuple[str, ...]


def resolve_output(
    *,
    historical_stage_code: int,
    applicability: bool | None,
    raw_score: float | None,
    calibrated_probability: float | None,
    control_x: bool = False,
    required_seeds_complete: bool = True,
) -> AssessmentOutput:
    """Apply the frozen X/unavailable/applicability/calibration precedence."""
    if historical_stage_code not in range(1, 13):
        raise P5AssessmentError("historical_stage_code must be in 1..12")
    if control_x:
        return AssessmentOutput(
            historical_stage_code,
            applicability,
            None,
            None,
            DecisionState.X,
            ("SYSTEM_CONTROL_INVALID",),
        )
    if historical_stage_code in INELIGIBLE_HEADS:
        return AssessmentOutput(
            historical_stage_code,
            None,
            None,
            None,
            DecisionState.UNAVAILABLE,
            ("HEAD_INELIGIBLE_BY_FROZEN_TARGET_CONTRACT",),
        )
    if not required_seeds_complete:
        return AssessmentOutput(
            historical_stage_code,
            applicability,
            None,
            None,
            DecisionState.UNAVAILABLE,
            ("REQUIRED_SEED_SET_INCOMPLETE",),
        )
    if applicability is False:
        return AssessmentOutput(
            historical_stage_code,
            False,
            None,
            None,
            DecisionState.NOT_APPLICABLE,
            ("FROZEN_PARENT_OR_CONTEXT_FALSE",),
        )
    if applicability is None:
        return AssessmentOutput(
            historical_stage_code,
            None,
            None,
            None,
            DecisionState.ABSTAIN,
            ("FROZEN_PARENT_OR_CONTEXT_UNKNOWN",),
        )
    if raw_score is not None and (
        not math.isfinite(raw_score) or not 0.0 <= raw_score <= 1.0
    ):
        raise P5AssessmentError("raw score is outside the sigmoid range")
    if calibrated_probability is None:
        return AssessmentOutput(
            historical_stage_code,
            True,
            raw_score,
            None,
            DecisionState.ABSTAIN,
            ("CALIBRATED_PROBABILITY_UNAVAILABLE",),
        )
    if not math.isfinite(calibrated_probability) or not 0.0 <= calibrated_probability <= 1.0:
        raise P5AssessmentError("calibrated probability is invalid")
    if calibrated_probability >= 0.70:
        state = DecisionState.ASSERT_POSITIVE
    elif calibrated_probability <= 0.30:
        state = DecisionState.ASSERT_NEGATIVE
    else:
        state = DecisionState.ABSTAIN
    return AssessmentOutput(
        historical_stage_code,
        True,
        raw_score,
        calibrated_probability,
        state,
        ("PLATT_CALIBRATED",),
    )


def validate_checkpoint_payload(payload: Mapping[str, Any]) -> None:
    """Require state needed to resume without repeating an epoch or consuming a seed."""
    required = {
        "model_state",
        "optimizer_state",
        "scheduler_state",
        "rng_state",
        "sampler_state",
        "epoch_cursor",
        "batch_cursor",
        "seed",
        "input_digest",
        "model_digest",
        "version_digest",
    }
    if set(payload) != required:
        raise P5AssessmentError("checkpoint fields differ from the frozen recovery envelope")
    if payload["seed"] not in REQUIRED_SEEDS:
        raise P5AssessmentError("checkpoint seed is outside the fixed seed set")
    for key in ("epoch_cursor", "batch_cursor"):
        value = payload[key]
        if type(value) is not int or value < 0:
            raise P5AssessmentError(f"{key} must be a nonnegative integer")
    for key in ("input_digest", "model_digest", "version_digest"):
        value = payload[key]
        if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71:
            raise P5AssessmentError(f"{key} must be a sha256 identity")


@dataclass(frozen=True)
class BaselineConfig:
    """One fixed L/G configuration; values are not selected from model results."""

    logistic_c: float = 1.0
    logistic_max_iter: int = 500
    gbdt_learning_rate: float = 0.05
    gbdt_max_depth: int = 3
    gbdt_max_iter: int = 64
    gbdt_l2_regularization: float = 1.0
    random_state: int = 17


def summarize_window(window: FeatureWindow, config: TemporalEncoderConfig) -> tuple[float, ...]:
    """Create the fixed causal summary shared by L and G from the same window closure.

    For each channel it returns last value, mean, minimum, maximum, and actual-missing fraction.
    Artificial masks are forbidden here because L/G consume the supervised unmasked closure.
    """
    window.validate(config)
    output: list[float] = []
    for channel in range(config.input_channels):
        cells = tuple(row[channel] for row in window.cells)
        if any(cell.artificial_mask for cell in cells):
            raise P5AssessmentError("L/G summaries cannot consume artificial SSL masks")
        observed = tuple(cell.value for cell in cells if cell.value is not None)
        missing_fraction = (len(cells) - len(observed)) / len(cells)
        if observed:
            values = tuple(float(value) for value in observed)
            output.extend((values[-1], math.fsum(values) / len(values), min(values), max(values)))
        else:
            output.extend((0.0, 0.0, 0.0, 0.0))
        output.append(missing_fraction)
    return tuple(output)


class FixedBaselineSet:
    """Fixed per-head L or G estimators with nullable outputs for ineligible heads."""

    def __init__(self, kind: str, config: BaselineConfig | None = None) -> None:
        if kind not in {"L", "G"}:
            raise P5AssessmentError("baseline kind must be L or G")
        self.kind = kind
        self.config = config or BaselineConfig()
        self._models: dict[int, Any] = {}

    def fit(
        self,
        features: Sequence[Sequence[float]],
        labels: Sequence[Sequence[int | None]],
        applicability: Sequence[Sequence[bool | None]],
    ) -> FixedBaselineSet:
        if not (len(features) == len(labels) == len(applicability)) or not features:
            raise P5AssessmentError("baseline training batches are empty or unaligned")
        try:
            import numpy as np
            from sklearn.ensemble import HistGradientBoostingClassifier
            from sklearn.linear_model import LogisticRegression
        except ImportError as exc:  # pragma: no cover - exercised by environment preflight
            raise P5AssessmentError("stage-training dependencies are unavailable") from exc
        matrix = np.asarray(features, dtype=float)
        if matrix.ndim != 2 or not np.isfinite(matrix).all():
            raise P5AssessmentError("baseline features must be a finite two-dimensional matrix")
        if any(len(row) != 12 for row in labels) or any(
            len(row) != 12 for row in applicability
        ):
            raise P5AssessmentError("baseline labels/applicability require twelve heads")
        for code in range(1, 13):
            if code in INELIGIBLE_HEADS:
                continue
            index = code - 1
            selected = [
                row
                for row in range(len(features))
                if labels[row][index] in (0, 1) and applicability[row][index] is True
            ]
            target = np.asarray([labels[row][index] for row in selected], dtype=int)
            if len(selected) == 0 or len(np.unique(target)) != 2:
                continue
            if self.kind == "L":
                model: Any = LogisticRegression(
                    C=self.config.logistic_c,
                    solver="lbfgs",
                    max_iter=self.config.logistic_max_iter,
                    random_state=self.config.random_state,
                )
            else:
                model = HistGradientBoostingClassifier(
                    learning_rate=self.config.gbdt_learning_rate,
                    max_depth=self.config.gbdt_max_depth,
                    max_iter=self.config.gbdt_max_iter,
                    l2_regularization=self.config.gbdt_l2_regularization,
                    random_state=self.config.random_state,
                )
            model.fit(matrix[selected], target)
            self._models[code] = model
        return self

    def predict_raw(
        self, features: Sequence[Sequence[float]]
    ) -> tuple[tuple[float | None, ...], ...]:
        try:
            import numpy as np
        except ImportError as exc:  # pragma: no cover - exercised by environment preflight
            raise P5AssessmentError("stage-training dependencies are unavailable") from exc
        matrix = np.asarray(features, dtype=float)
        if matrix.ndim != 2 or not np.isfinite(matrix).all():
            raise P5AssessmentError("baseline features must be a finite two-dimensional matrix")
        rows: list[tuple[float | None, ...]] = []
        probabilities = {
            code: model.predict_proba(matrix)[:, 1].tolist()
            for code, model in self._models.items()
        }
        for row in range(len(matrix)):
            rows.append(
                tuple(
                    None if code not in probabilities else float(probabilities[code][row])
                    for code in range(1, 13)
                )
            )
        return tuple(rows)


def build_t0_model(config: TemporalEncoderConfig | None = None) -> Any:
    """Build the fixed direct-supervision encoder lazily when PyTorch is available."""
    frozen = config or TemporalEncoderConfig()
    frozen.validate()
    try:
        import torch
        from torch import nn
    except ImportError as exc:  # pragma: no cover - exercised by environment preflight
        raise P5AssessmentError("PyTorch backend is unavailable") from exc

    class _T0(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            patch_width = frozen.patch_sessions * frozen.input_channels
            self.value_projection = nn.Linear(patch_width, frozen.d_model)
            self.actual_missing_projection = nn.Linear(patch_width, frozen.d_model)
            self.artificial_mask_projection = nn.Linear(patch_width, frozen.d_model)
            self.cls = nn.Parameter(torch.zeros(1, 1, frozen.d_model))
            self.position = nn.Parameter(
                torch.zeros(
                    1,
                    frozen.window_sessions // frozen.patch_sessions + 1,
                    frozen.d_model,
                )
            )
            layer = nn.TransformerEncoderLayer(
                d_model=frozen.d_model,
                nhead=frozen.attention_heads,
                dim_feedforward=frozen.ffn_dim,
                dropout=0.0,
                activation="gelu",
                batch_first=True,
                norm_first=False,
            )
            self.encoder = nn.TransformerEncoder(layer, num_layers=frozen.encoder_layers)
            self.heads = nn.Linear(frozen.d_model, frozen.output_heads)

        def forward(self, values: Any, actual_missing: Any, artificial_mask: Any) -> Any:
            expected = (frozen.window_sessions, frozen.input_channels)
            if tuple(values.shape[1:]) != expected:
                raise P5AssessmentError("T0 input shape differs from the frozen contract")
            if values.shape != actual_missing.shape or values.shape != artificial_mask.shape:
                raise P5AssessmentError("T0 value and mask shapes differ")
            if torch.logical_and(actual_missing.bool(), artificial_mask.bool()).any():
                raise P5AssessmentError("actual and artificial masks overlap")
            batch = values.shape[0]
            patches = frozen.window_sessions // frozen.patch_sessions
            shape = (batch, patches, frozen.patch_sessions * frozen.input_channels)
            clean = values.masked_fill(actual_missing.bool() | artificial_mask.bool(), 0.0)
            tokens = self.value_projection(clean.reshape(shape))
            tokens = tokens + self.actual_missing_projection(actual_missing.float().reshape(shape))
            tokens = tokens + self.artificial_mask_projection(
                artificial_mask.float().reshape(shape)
            )
            cls = self.cls.expand(batch, -1, -1)
            encoded = self.encoder(torch.cat((cls, tokens), dim=1) + self.position)
            return self.heads(encoded[:, 0, :])

    model = _T0()
    actual = sum(parameter.numel() for parameter in model.parameters())
    if actual != frozen.parameter_count():
        raise P5AssessmentError(
            f"T0 parameter count {actual} differs from frozen count {frozen.parameter_count()}"
        )
    return model

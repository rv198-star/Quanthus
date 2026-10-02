"""Small P5 D adapters; callers must supply B-qualified data and immutable identities.

No adapter discovers files, admits a head, calibrates itself, or starts a formal run.
"""

from __future__ import annotations

import hashlib
import json
import pickle
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any

from quanthus.stage_learning.p5_assessment import (
    FeatureWindow,
    FixedBaselineSet,
    P5AssessmentError,
    TemporalEncoderConfig,
    summarize_window,
)
from quanthus.stage_learning.p5_contracts import (
    ModelIdentity,
    ParentEvidence,
    SampleIdentity,
    SeedPrediction,
    admitted_heads,
    head_set_digest,
    require_digest,
    supervision_mask,
    train_base_rates,
)
from quanthus.stage_proposition_bundle import (
    StagePropositionBundle,
    StagePropositionContract,
    validate_bundle,
)


@dataclass(frozen=True)
class TrainingExample:
    sample: SampleIdentity
    window: FeatureWindow
    labels: tuple[int | None, ...]
    applicability: tuple[bool | None, ...]
    parents: Mapping[int, ParentEvidence]


def _semantic_digest(value: object) -> str:
    return (
        "sha256:"
        + hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()
    )


def _baseline_state_digest(estimator: FixedBaselineSet) -> str:
    # Serialize only our own in-memory estimator to hash it; no pickle is ever loaded.
    return "sha256:" + hashlib.sha256(pickle.dumps(estimator, protocol=5)).hexdigest()


def temporal_state_digest(model: Any) -> str:
    import torch

    from quanthus.stage_learning.p5_compute_probe import _state_digest

    return _state_digest(torch, model.state_dict())


def _window(sample: SampleIdentity, window: FeatureWindow, config: TemporalEncoderConfig) -> None:
    sample.validate()
    window.validate(config)
    if (
        window.feature_cutoff != sample.anchor_date
        or window.session_dates[-1] != sample.anchor_date
    ):
        raise P5AssessmentError("window does not end at the identified sample T")
    if window.session_dates[0].year < 2012:
        raise P5AssessmentError("2011 warmup is not authorized")


class BaselineAdapter:
    def __init__(
        self, identity: ModelIdentity, heads: Sequence[int], config: TemporalEncoderConfig
    ):
        identity.validate()
        config.validate()
        self.heads = admitted_heads(heads)
        if identity.kind not in {"L", "G"} or identity.head_set_digest != head_set_digest(
            self.heads
        ):
            raise P5AssessmentError("baseline identity/head set differs")
        self.identity, self.config = identity, config
        self.estimator = FixedBaselineSet(identity.kind)
        self.fitted = False

    def fit(self, rows: Sequence[TrainingExample], *, partition: str) -> None:
        self.fitted = False
        if partition != "train" or not rows:
            raise P5AssessmentError("baseline fit requires a nonempty train partition")
        features, masks = [], []
        if len({row.sample.sample_id for row in rows}) != len(rows):
            raise P5AssessmentError("duplicate train sample")
        for row in rows:
            _window(row.sample, row.window, self.config)
            features.append(summarize_window(row.window, self.config))
            masks.append(
                supervision_mask(
                    sample=row.sample,
                    labels=row.labels,
                    applicability=row.applicability,
                    trainable_heads=self.heads,
                    parents=row.parents,
                )
            )
        self.estimator.fit(
            features, [row.labels for row in rows], masks, trainable_heads=self.heads
        )
        self.identity = replace(
            self.identity,
            checkpoint_digest=_baseline_state_digest(self.estimator),
            trained_heads=self.estimator.trained_heads,
            training_rows_digest=_semantic_digest([asdict(row) for row in rows]),
        )
        self.fitted = True

    def predict(self, sample: SampleIdentity, window: FeatureWindow) -> SeedPrediction:
        if not self.fitted:
            raise P5AssessmentError("baseline is not fitted")
        if _baseline_state_digest(self.estimator) != self.identity.checkpoint_digest:
            raise P5AssessmentError("baseline state differs from its fitted checkpoint")
        _window(sample, window, self.config)
        scores = self.estimator.predict_raw([summarize_window(window, self.config)])[0]
        return prediction(
            sample, self.identity, scores, window_digest=_semantic_digest(asdict(window))
        )


def prediction(
    sample: SampleIdentity,
    identity: ModelIdentity,
    scores: Sequence[float | None],
    *,
    window_digest: str,
    seed: int | None = None,
) -> SeedPrediction:
    sample.validate()
    identity.validate()
    require_digest(window_digest)
    if len(scores) != 12:
        raise P5AssessmentError("prediction requires twelve heads")
    return SeedPrediction(
        sample,
        identity.input_digest,
        identity.split_digest,
        identity.head_set_digest,
        identity.model_digest,
        identity.checkpoint_digest,
        tuple(scores),
        seed,
        identity.trained_heads,
        window_digest,
    )


def temporal_predict(
    model: Any,
    *,
    sample: SampleIdentity,
    window: FeatureWindow,
    identity: ModelIdentity,
    heads: Sequence[int],
    config: TemporalEncoderConfig,
    seed: int,
) -> SeedPrediction:
    import torch

    identity.validate()
    admitted_heads(heads)
    if seed not in (17, 29, 43) or not set(identity.trained_heads) <= set(heads):
        raise P5AssessmentError("T0 seed/trained head set differs")
    if identity.kind != "T0" or identity.head_set_digest != head_set_digest(heads):
        raise P5AssessmentError("T0 identity/head set differs; T1 requires separate admission")
    _window(sample, window, config)
    if temporal_state_digest(model) != identity.checkpoint_digest:
        raise P5AssessmentError("T0 state differs from its checkpoint identity")
    if any(cell.artificial_mask for row in window.cells for cell in row):
        raise P5AssessmentError("inference cannot consume SSL masks")
    device = next(model.parameters()).device
    values = torch.tensor(
        [[[cell.value or 0.0 for cell in row] for row in window.cells]], device=device
    )
    missing = torch.tensor(
        [[[cell.actual_missing for cell in row] for row in window.cells]], device=device
    )
    was_training = model.training
    try:
        model.eval()
        with torch.no_grad():
            raw = torch.sigmoid(model(values, missing, torch.zeros_like(missing))).cpu().tolist()[0]
    finally:
        model.train(was_training)
    return prediction(
        sample,
        identity,
        [raw[code - 1] if code in identity.trained_heads else None for code in range(1, 13)],
        seed=seed,
        window_digest=_semantic_digest(asdict(window)),
    )


def rule_surface(
    *, sample: SampleIdentity, contract: StagePropositionContract, bundle: StagePropositionBundle
) -> dict:
    """Preserve the existing rule verdicts; never translate them into probabilities."""
    sample.validate()
    verified = validate_bundle(contract, bundle.to_dict())
    # Bundle cutoffs are timezone-aware instants. A date alone is insufficient to relabel a
    # different session's rule output, even when its security happens to match.
    from datetime import datetime, time
    from zoneinfo import ZoneInfo

    cutoff = datetime.fromisoformat(verified.knowledge_cutoff.replace("Z", "+00:00"))
    local_cutoff = cutoff.astimezone(ZoneInfo("Asia/Shanghai"))
    if (
        verified.security_id != sample.security_id
        or local_cutoff.date() != sample.anchor_date
        or local_cutoff.time() > time(15, 0)
    ):
        raise P5AssessmentError("rule bundle belongs to another sample")
    return {
        "sample_id": sample.sample_id,
        "source_kind": "R",
        "bundle_digest": verified.digest,
        "bundle": verified.to_dict(),
        "calibrated_probability": None,
        "claim_ceiling": "existing_rule_surface_only_no_probability_or_trading_authority",
    }


def base_rate_receipt(
    *, rows: Sequence[TrainingExample], heads: Sequence[int], train_manifest_digest: str
) -> dict:
    require_digest(train_manifest_digest)
    admitted_heads(heads)
    masks = [
        supervision_mask(
            sample=row.sample,
            labels=row.labels,
            applicability=row.applicability,
            trainable_heads=heads,
            parents=row.parents,
        )
        for row in rows
    ]
    rates = train_base_rates([row.labels for row in rows], masks, partition="train")
    counts = [sum(mask[index] for mask in masks) for index in range(12)]
    positives = [
        sum(row.labels[index] for row, mask in zip(rows, masks, strict=True) if mask[index])
        for index in range(12)
    ]
    return {
        "fit_partition": "train",
        "train_manifest_digest": train_manifest_digest,
        "head_set_digest": head_set_digest(heads),
        "positive_counts": positives,
        "definite_applicable_counts": counts,
        "base_rates": rates,
    }


def t1_admission_status() -> dict:
    return {
        "admitted": False,
        "raw_score": None,
        "calibrated_probability": None,
        "reason_code": "T1_NOT_ADMITTED_PENDING_B_C",
        "compute_limited": None,
    }

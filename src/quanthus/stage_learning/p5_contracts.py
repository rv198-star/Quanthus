"""Identity-bound P5 adapters. This module opens no data or training entrypoint."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date

from quanthus.stage_learning.p5_assessment import (
    INELIGIBLE_HEADS,
    REQUIRED_SEEDS,
    P5AssessmentError,
    resolve_output,
    validate_trainable_heads,
)

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
PARENT_CODES = {1: 12, 4: 3, 6: 5, 11: 10}
REQUEST_DIGEST = "sha256:37ca82557a1c0a538f5d7dd945536c54a9c6ceb022e1f8f400d4a1243bf1397b"
TARGET_DIGEST = "sha256:315fab9f7bd0304b418bc2a35f27e557a0f97b0c23b82b228f2754a386173d2f"
PROPOSITIONS = (
    "stock_stage.condition.downside_stabilization",
    "stock_stage.condition.orderly_consolidation",
    "stock_stage.event.breakout_probe",
    "stock_stage.condition.breakout_acceptance",
    "stock_stage.context.advancing",
    "stock_stage.condition.pullback_test",
    "stock_stage.condition.repair_rebuild",
    "stock_stage.modifier.upside_acceleration_extension",
    "stock_stage.modifier.high_side_price_volume_divergence",
    "stock_stage.context.structural_failure",
    "stock_stage.condition.post_break_rebound",
    "stock_stage.context.downside",
)


def head_set_digest(codes: Sequence[int]) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(list(admitted_heads(codes))).encode()).hexdigest()


def require_digest(value: str) -> None:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise P5AssessmentError("invalid P5 identity digest")


def admitted_heads(codes: Sequence[int]) -> tuple[int, ...]:
    return validate_trainable_heads(codes)


@dataclass(frozen=True)
class SampleIdentity:
    sample_id: str
    security_id: str
    anchor_date: date
    episode_id: str

    def validate(self) -> None:
        if not all((self.sample_id, self.security_id, self.episode_id)):
            raise P5AssessmentError("sample identity is incomplete")
        if not date(2012, 1, 1) <= self.anchor_date <= date(2021, 12, 31):
            raise P5AssessmentError("P5 sample lies outside the authorized development dates")


@dataclass(frozen=True)
class ModelIdentity:
    kind: str
    request_digest: str
    target_digest: str
    input_digest: str
    split_digest: str
    head_set_digest: str
    model_digest: str
    checkpoint_digest: str
    scaler_digest: str
    code_digest: str
    reference_digest: str
    transform_digest: str
    calibrator_digest: str | None = None
    scaler_fit_partition: str = "train"
    calibrator_fit_partition: str | None = None
    trained_heads: tuple[int, ...] = ()
    training_rows_digest: str | None = None

    def validate(self) -> None:
        admitted_heads(self.trained_heads)
        if self.kind not in {"R", "L", "G", "T0", "T1", "BASE_RATE"}:
            raise P5AssessmentError("P5 model kind is invalid")
        if self.request_digest != REQUEST_DIGEST or self.target_digest != TARGET_DIGEST:
            raise P5AssessmentError("frozen P5 request or target identity differs")
        for name, value in asdict(self).items():
            if name.endswith("digest") and value is not None:
                require_digest(value)
        if self.scaler_fit_partition != "train":
            raise P5AssessmentError("scaler may fit only the train partition")
        if (self.calibrator_digest is None) != (self.calibrator_fit_partition is None):
            raise P5AssessmentError("calibrator identity/partition binding is incomplete")
        if self.calibrator_digest is not None and self.calibrator_fit_partition != "calibration":
            raise P5AssessmentError("calibrator may fit only the calibration partition")


@dataclass(frozen=True)
class ParentEvidence:
    """Applicability is from a governed T-visible context, never a model parent score."""

    sample: SampleIdentity
    historical_stage_code: int
    episode_id: str | None
    applicability: bool | None
    evidence_digest: str
    knowledge_cutoff: date

    def validate_for(self, sample: SampleIdentity, child_code: int) -> None:
        require_digest(self.evidence_digest)
        if self.sample != sample or self.historical_stage_code != PARENT_CODES.get(child_code):
            raise P5AssessmentError("parent context has a different sample or proposition")
        if self.knowledge_cutoff > sample.anchor_date:
            raise P5AssessmentError("parent context contains future evidence")
        if self.applicability is not None and type(self.applicability) is not bool:
            raise P5AssessmentError("parent applicability must be true/false/unknown")
        if self.applicability is True and self.episode_id != sample.episode_id:
            raise P5AssessmentError("child and qualified parent episode differ")


def supervision_mask(
    *,
    sample: SampleIdentity,
    labels: Sequence[int | None],
    applicability: Sequence[bool | None],
    trainable_heads: Sequence[int],
    parents: Mapping[int, ParentEvidence],
) -> tuple[bool, ...]:
    sample.validate()
    heads = admitted_heads(trainable_heads)
    if len(labels) != 12 or len(applicability) != 12:
        raise P5AssessmentError("supervision requires twelve aligned heads")
    mask = []
    for code, (label, applies) in enumerate(zip(labels, applicability, strict=True), 1):
        if label is not None and (type(label) is not int or label not in (0, 1)):
            raise P5AssessmentError("unknown labels must stay null; definite labels are 0/1")
        if applies is not None and type(applies) is not bool:
            raise P5AssessmentError("invalid applicability")
        if code in heads and code in PARENT_CODES:
            parent = parents.get(code)
            if parent is None:
                if applies is True:
                    raise P5AssessmentError("applicable conditional head lacks parent evidence")
            else:
                parent.validate_for(sample, code)
                if applies is not parent.applicability:
                    raise P5AssessmentError("child applicability differs from governed parent")
        mask.append(code in heads and label is not None and applies is True)
    return tuple(mask)


@dataclass(frozen=True)
class SeedPrediction:
    sample: SampleIdentity
    input_digest: str
    split_digest: str
    head_set_digest: str
    model_digest: str
    checkpoint_digest: str
    raw_scores: tuple[float | None, ...]
    seed: int | None = None
    trained_heads: tuple[int, ...] = ()
    window_digest: str | None = None


def aggregate_predictions(
    predictions: Mapping[int, SeedPrediction], *, trainable_heads: Sequence[int]
) -> tuple[float | None, ...]:
    heads = admitted_heads(trainable_heads)
    if set(predictions) != set(REQUIRED_SEEDS):
        raise P5AssessmentError("required seed set is incomplete")
    first = predictions[17]
    first.sample.validate()
    if first.head_set_digest != head_set_digest(heads):
        raise P5AssessmentError("seed head set identity differs")
    if len({p.checkpoint_digest for p in predictions.values()}) != len(REQUIRED_SEEDS):
        raise P5AssessmentError("one checkpoint cannot replace distinct required seeds")
    for seed, prediction in predictions.items():
        if prediction.seed != seed:
            raise P5AssessmentError("prediction seed identity differs from ensemble member")
        admitted_heads(prediction.trained_heads)
        require_digest(prediction.window_digest)
        if prediction.sample != first.sample or any(
            getattr(prediction, key) != getattr(first, key)
            for key in (
                "input_digest",
                "split_digest",
                "head_set_digest",
                "model_digest",
                "window_digest",
            )
        ):
            raise P5AssessmentError("seed predictions are not bound to the same sample/input/model")
        for key in (
            "input_digest",
            "split_digest",
            "head_set_digest",
            "model_digest",
            "checkpoint_digest",
        ):
            require_digest(getattr(prediction, key))
        if len(prediction.raw_scores) != 12:
            raise P5AssessmentError("seed prediction must contain twelve heads")
    scores: list[float | None] = []
    for code in range(1, 13):
        if code not in heads or any(code not in p.trained_heads for p in predictions.values()):
            scores.append(None)
            continue
        values = [predictions[seed].raw_scores[code - 1] for seed in REQUIRED_SEEDS]
        if any(value is None for value in values):
            scores.append(None)
            continue
        if any(not math.isfinite(value) or not 0 <= value <= 1 for value in values):
            raise P5AssessmentError("invalid raw sigmoid score")
        scores.append(math.fsum(values) / len(REQUIRED_SEEDS))
    return tuple(scores)


def aggregation_receipt(
    predictions: Mapping[int, SeedPrediction], *, trainable_heads: Sequence[int]
) -> dict:
    scores = aggregate_predictions(predictions, trainable_heads=trainable_heads)
    members = {str(seed): asdict(predictions[seed]) for seed in REQUIRED_SEEDS}
    for member in members.values():
        member["sample"]["anchor_date"] = member["sample"]["anchor_date"].isoformat()
    receipt = {
        "members": members,
        "raw_scores": scores,
        "aggregation": "three_seed_arithmetic_mean",
    }
    return {
        **receipt,
        "digest": "sha256:"
        + hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest(),
    }


@dataclass(frozen=True)
class CalibrationBinding:
    digest: str
    model_digest: str
    checkpoint_digest: str
    input_digest: str
    split_digest: str
    head_set_digest: str
    fit_partition: str
    calibratable_heads: tuple[int, ...]
    score_kind: str

    def validate_for(self, identity: ModelIdentity, code: int) -> None:
        admitted_heads(self.calibratable_heads)
        if self.fit_partition != "calibration" or code not in self.calibratable_heads:
            raise P5AssessmentError("head lacks qualified calibration population")
        if self.digest != identity.calibrator_digest or any(
            getattr(self, key) != getattr(identity, key)
            for key in (
                "model_digest",
                "checkpoint_digest",
                "input_digest",
                "split_digest",
                "head_set_digest",
            )
        ):
            raise P5AssessmentError("calibrator belongs to a different model/input/split")
        expected = "three_seed_mean_raw_sigmoid" if identity.kind in {"T0", "T1"} else "raw_score"
        if self.score_kind != expected:
            raise P5AssessmentError("calibrator score aggregation differs")


def project_output(
    *,
    sample: SampleIdentity,
    historical_stage_code: int,
    proposition_id: str,
    identity: ModelIdentity,
    trainable_heads: Sequence[int],
    applicability: bool | None,
    raw_score: float | None,
    calibrated_probability: float | None,
    parent: ParentEvidence | None = None,
    required_facts_available: bool = True,
    required_seeds_complete: bool = True,
    control_x: bool = False,
    calibration: CalibrationBinding | None = None,
) -> dict:
    """Attach auditable identities to a score without increasing its decision authority."""
    sample.validate()
    identity.validate()
    heads = admitted_heads(trainable_heads)
    if (
        historical_stage_code not in range(1, 13)
        or proposition_id != PROPOSITIONS[historical_stage_code - 1]
    ):
        raise P5AssessmentError("proposition identity differs from frozen targets")
    if identity.head_set_digest != head_set_digest(heads):
        raise P5AssessmentError("output head set identity differs")
    if not set(identity.trained_heads) <= set(heads):
        raise P5AssessmentError("trained heads exceed admitted heads")
    if historical_stage_code in PARENT_CODES and historical_stage_code in heads:
        if parent is None:
            if applicability is True:
                raise P5AssessmentError("conditional output lacks parent evidence")
        else:
            parent.validate_for(sample, historical_stage_code)
            if applicability is not parent.applicability:
                raise P5AssessmentError("output applicability differs from governed parent")
    if calibrated_probability is not None and identity.calibrator_digest is None:
        raise P5AssessmentError("probability lacks calibrated identity")
    if calibrated_probability is not None:
        if calibration is None:
            raise P5AssessmentError("probability lacks calibration binding")
        calibration.validate_for(identity, historical_stage_code)
    if identity.kind == "R" and calibrated_probability is not None:
        raise P5AssessmentError("rule output cannot be upgraded to a probability")
    output = resolve_output(
        historical_stage_code=historical_stage_code,
        applicability=applicability,
        raw_score=raw_score,
        calibrated_probability=calibrated_probability,
        head_trained=historical_stage_code in identity.trained_heads,
        required_facts_available=required_facts_available,
        required_seeds_complete=required_seeds_complete,
        control_x=control_x,
    )
    return {
        **asdict(output),
        "proposition_id": proposition_id,
        "sample": {**asdict(sample), "anchor_date": sample.anchor_date.isoformat()},
        "model_identity": asdict(identity),
        "raw_score_source": identity.kind,
        "constraint_policy": "frozen_applicability_and_output_precedence_no_score_clipping",
        "parent_evidence_digest": None if parent is None else parent.evidence_digest,
        "claim_ceiling": "research_only_no_expert_policy_or_trading_authority",
    }


def train_base_rates(
    labels: Sequence[Sequence[int | None]], masks: Sequence[Sequence[bool]], *, partition: str
) -> tuple[float | None, ...]:
    if partition != "train" or not labels or len(labels) != len(masks):
        raise P5AssessmentError("base rate requires aligned train labels and governed masks")
    totals = [0] * 12
    counts = [0] * 12
    for row, mask in zip(labels, masks, strict=True):
        if len(row) != 12 or len(mask) != 12:
            raise P5AssessmentError("base rate requires twelve aligned heads")
        for index, (label, selected) in enumerate(zip(row, mask, strict=True)):
            if (
                type(selected) is not bool
                or selected
                and (type(label) is not int or label not in (0, 1))
            ):
                raise P5AssessmentError("base rate mask selected a non-definite label")
            if selected and index + 1 in INELIGIBLE_HEADS:
                raise P5AssessmentError("base rate mask selected a frozen ineligible head")
            if selected:
                totals[index] += label
                counts[index] += 1
    return tuple(
        total / count if count else None for total, count in zip(totals, counts, strict=True)
    )


def validate_reference_use(
    *,
    grade: str,
    partition: str,
    exposed_to_tested_output: bool,
    previously_used_for_development: bool,
) -> None:
    if partition not in {"train", "development_validation", "calibration", "evaluation"}:
        raise P5AssessmentError("invalid reference partition")
    if grade not in {
        "independently_adjudicated",
        "historical_expert",
        "rule_silver",
        "model_suggestion",
        "same_origin_simulation",
    }:
        raise P5AssessmentError("unrecognized reference provenance")
    if partition in {"calibration", "evaluation"} and (
        grade != "independently_adjudicated"
        or exposed_to_tested_output
        or previously_used_for_development
    ):
        raise P5AssessmentError("reference is not eligible for confirmatory calibration/evaluation")


def validate_dependency_mask(
    dependencies: Mapping[str, frozenset[str]],
    *,
    artificial_mask: frozenset[str],
    actual_missing: frozenset[str],
) -> None:
    """A visible cell may not transitively reconstruct an artificially masked target."""
    known = set(dependencies)
    if artificial_mask & actual_missing or not (artificial_mask | actual_missing) <= known:
        raise P5AssessmentError("invalid reconstruction mask or unknown dependency")

    def ancestors(cell: str, active: frozenset[str]) -> frozenset[str]:
        if cell not in dependencies or cell in active:
            raise P5AssessmentError("dependency closure is missing or cyclic")
        output = set(dependencies[cell])
        for parent in dependencies[cell]:
            output.update(ancestors(parent, active | {cell}))
        return frozenset(output)

    for cell in sorted(known):
        closure = ancestors(cell, frozenset())
        if cell not in artificial_mask | actual_missing and closure & artificial_mask:
            raise P5AssessmentError("visible derived fact leaks a masked reconstruction target")

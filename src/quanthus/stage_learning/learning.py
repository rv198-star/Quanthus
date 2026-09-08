"""Fail-closed first-group R0/R1 learning primitives for P3-STAGE-001-E.

The module contains no sealed-partition access and cannot manufacture references.  It consumes a
fully resolved D reference package, builds one T-only observation per frozen context, enumerates the
18 preregistered R1 threshold combinations, and scores them without using future settlements.
"""
from __future__ import annotations

import itertools
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from decimal import Context, Decimal, localcontext
from pathlib import Path
from typing import Any

from quanthus.stage_learning.core import ActivityObservation
from quanthus.stage_learning.reference import (
    ReferenceContext,
    ReferenceKind,
    ResolvedReference,
    context_identity,
)
from quanthus.stage_learning.review_ledger import ReviewLedgerError, verify_resolution_artifact
from quanthus.structure_v2 import StructureV2Result
from quanthus.structure_v2_inputs import digest


class StageLearningError(ValueError):
    """Raised when semantic learning would exceed the frozen reference/experiment contract."""


@dataclass(frozen=True)
class R1Parameters:
    probe_volume_ratio20_min: Decimal
    acceptance_hold_sessions: int
    acceptance_signed_distance_min: Decimal
    acceptance_window_volume_ratio20_min: Decimal

    @property
    def trial_id(self) -> str:
        return digest({"family": "R1", **asdict(self)})

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for key, item in tuple(value.items()):
            if isinstance(item, Decimal):
                value[key] = format(item, "f")
        return {"trial_id": self.trial_id, **value}


@dataclass(frozen=True)
class StageLearningObservation:
    context_id: str
    anchor_scale: int
    observation: str
    phase: str
    exchange_session_age: int
    hold_count: int
    previous_loss: bool
    signed_distance: Decimal | None
    probe_volume_ratio20: Decimal | None
    acceptance_activity_mean: Decimal | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_id": self.context_id,
            "anchor_scale": self.anchor_scale,
            "observation": self.observation,
            "phase": self.phase,
            "exchange_session_age": self.exchange_session_age,
            "hold_count": self.hold_count,
            "previous_loss": self.previous_loss,
            "signed_distance": (
                None if self.signed_distance is None else format(self.signed_distance, "f")
            ),
            "probe_volume_ratio20": (
                None
                if self.probe_volume_ratio20 is None
                else format(self.probe_volume_ratio20, "f")
            ),
            "acceptance_activity_mean": (
                None
                if self.acceptance_activity_mean is None
                else format(self.acceptance_activity_mean, "f")
            ),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> StageLearningObservation:
        required = {
            "context_id",
            "anchor_scale",
            "observation",
            "phase",
            "exchange_session_age",
            "hold_count",
            "previous_loss",
            "signed_distance",
            "probe_volume_ratio20",
            "acceptance_activity_mean",
        }
        if set(value) != required:
            raise StageLearningError("learning observation fields differ")
        try:
            return cls(
                context_id=str(value["context_id"]),
                anchor_scale=int(value["anchor_scale"]),
                observation=str(value["observation"]),
                phase=str(value["phase"]),
                exchange_session_age=int(value["exchange_session_age"]),
                hold_count=int(value["hold_count"]),
                previous_loss=bool(value["previous_loss"]),
                signed_distance=(
                    None
                    if value["signed_distance"] is None
                    else Decimal(str(value["signed_distance"]))
                ),
                probe_volume_ratio20=(
                    None
                    if value["probe_volume_ratio20"] is None
                    else Decimal(str(value["probe_volume_ratio20"]))
                ),
                acceptance_activity_mean=(
                    None
                    if value["acceptance_activity_mean"] is None
                    else Decimal(str(value["acceptance_activity_mean"]))
                ),
            )
        except (TypeError, ValueError) as exc:
            raise StageLearningError("learning observation is invalid") from exc


@dataclass(frozen=True)
class TrialScore:
    trial_id: str
    definite_count: int
    confirmed_count: int
    coverage: float
    definite_positive_count: int
    definite_negative_count: int
    positive_reference_covered: int
    positive_reference_coverage: float
    definite_negative_false_positive_count: int
    definite_negative_false_positive_rate: float
    macro_f1: float
    stage_metrics: Mapping[int, Mapping[str, float | int | None]]
    ambiguous_count: int
    ambiguous_unique_assertions: int
    ambiguous_unique_assertion_rate: float | None
    unjudgeable_count: int
    unjudgeable_unique_assertions: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "trial_id": self.trial_id,
            "definite_count": self.definite_count,
            "confirmed_count": self.confirmed_count,
            "coverage": self.coverage,
            "coverage_semantics": "legacy_confirmed_prediction_rate_over_all_definite",
            "definite_positive_count": self.definite_positive_count,
            "definite_negative_count": self.definite_negative_count,
            "positive_reference_covered": self.positive_reference_covered,
            "positive_reference_coverage": self.positive_reference_coverage,
            "definite_negative_false_positive_count": self.definite_negative_false_positive_count,
            "definite_negative_false_positive_rate": self.definite_negative_false_positive_rate,
            "macro_f1": self.macro_f1,
            "stage_metrics": {str(key): dict(value) for key, value in self.stage_metrics.items()},
            "ambiguous_count": self.ambiguous_count,
            "ambiguous_unique_assertions": self.ambiguous_unique_assertions,
            "ambiguous_unique_assertion_rate": self.ambiguous_unique_assertion_rate,
            "unjudgeable_count": self.unjudgeable_count,
            "unjudgeable_unique_assertions": self.unjudgeable_unique_assertions,
        }


def enumerate_r1_trials(experiment: Mapping[str, Any]) -> tuple[R1Parameters, ...]:
    raw = experiment.get("r1_search_space")
    if not isinstance(raw, Mapping):
        raise StageLearningError("R1 search space is missing")
    try:
        probes = tuple(Decimal(str(value)) for value in raw["probe_volume_ratio20_min"])
        holds = tuple(int(value) for value in raw["acceptance_hold_sessions"])
        distances = tuple(Decimal(str(value)) for value in raw["acceptance_signed_distance_min"])
        activities = tuple(
            Decimal(str(value)) for value in raw["acceptance_window_volume_ratio20_min"]
        )
        expected = int(raw["cartesian_trial_count_per_scale"])
        maximum = int(raw["per_anchor_scale_max_trials"])
    except (KeyError, TypeError, ValueError) as exc:
        raise StageLearningError("R1 search space is invalid") from exc
    trials = tuple(
        R1Parameters(probe, hold, distance, activity)
        for probe, hold, distance, activity in itertools.product(
            probes, holds, distances, activities
        )
    )
    if len(trials) != expected or len(trials) > maximum or expected != 18:
        raise StageLearningError("R1 trial budget differs from the frozen 18-trial contract")
    if len({item.trial_id for item in trials}) != len(trials):
        raise StageLearningError("R1 search space contains duplicate trials")
    return trials


def r0_parameters(experiment: Mapping[str, Any]) -> R1Parameters:
    raw = experiment.get("r0_baseline")
    if not isinstance(raw, Mapping):
        raise StageLearningError("R0 baseline is missing")
    try:
        return R1Parameters(
            Decimal(str(raw["probe_volume_ratio20_min"])),
            int(raw["acceptance_hold_sessions"]),
            Decimal(str(raw["acceptance_signed_distance_min"])),
            Decimal(str(raw["acceptance_window_volume_ratio20_min"])),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise StageLearningError("R0 baseline is invalid") from exc


def _mean(values: Sequence[Decimal]) -> Decimal:
    if not values:
        raise StageLearningError("cannot average an empty activity window")
    with localcontext(Context(prec=28)):
        return sum(values, Decimal(0)) / Decimal(len(values))


def build_learning_observations(
    contexts: Sequence[ReferenceContext],
    structure: StructureV2Result,
    activity: Sequence[ActivityObservation],
) -> tuple[StageLearningObservation, ...]:
    """Project exact T-only rule inputs for the frozen sampled contexts once per Run."""
    wanted = {item.context_id for item in contexts}
    if len(wanted) != len(contexts):
        raise StageLearningError("learning contexts contain duplicate identities")
    activity_by_key = {item.key: item for item in activity}
    if len(activity_by_key) != len(activity):
        raise StageLearningError("learning activity contains duplicate identities")
    episode_activity: dict[tuple[str, int, str], list[ActivityObservation]] = {}
    output: dict[str, StageLearningObservation] = {}
    ordered = sorted(
        structure.episodes,
        key=lambda item: (item.security_code, item.market_date, item.scale, item.episode_id),
    )
    for episode in ordered:
        episode_key = (episode.security_code, episode.scale, episode.episode_id)
        current = activity_by_key.get((episode.security_code, episode.market_date))
        history = episode_activity.setdefault(episode_key, [])
        if current is not None:
            history.append(current)
        identity = context_identity(episode)
        if identity in wanted:
            if episode.close is None or episode.frozen_level <= 0:
                signed = None
            else:
                with localcontext(Context(prec=28)):
                    signed = episode.close / episode.frozen_level - Decimal(1)
            current_ratio = (
                current.volume_ratio
                if current is not None and current.available and current.volume_ratio is not None
                else None
            )
            complete_episode_activity = (
                len(history) == episode.observed_bar_count
                and all(item.available and item.volume_ratio is not None for item in history)
            )
            activity_mean = (
                _mean(tuple(item.volume_ratio for item in history if item.volume_ratio is not None))
                if complete_episode_activity
                else None
            )
            output[identity] = StageLearningObservation(
                context_id=identity,
                anchor_scale=episode.scale,
                observation=episode.observation,
                phase=episode.phase,
                exchange_session_age=episode.exchange_session_age,
                hold_count=episode.hold_count,
                previous_loss=episode.previous_loss_ref is not None,
                signed_distance=signed,
                probe_volume_ratio20=current_ratio,
                acceptance_activity_mean=activity_mean,
            )
        if episode.phase in {"below", "reclaimed", "censored"}:
            episode_activity.pop(episode_key, None)
    if set(output) != wanted:
        raise StageLearningError(
            "not every frozen reference context resolved to T-only learning inputs"
        )
    return tuple(output[item.context_id] for item in contexts)


def load_learning_observations(path: Path) -> tuple[StageLearningObservation, ...]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise StageLearningError("learning observation artifact is unreadable") from exc
    output: list[StageLearningObservation] = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise StageLearningError(
                f"learning observation line {number} is invalid JSON"
            ) from exc
        if not isinstance(raw, Mapping):
            raise StageLearningError(f"learning observation line {number} must be an object")
        output.append(StageLearningObservation.from_dict(raw))
    if not output:
        raise StageLearningError("learning observation artifact is empty")
    if len({item.context_id for item in output}) != len(output):
        raise StageLearningError("learning observation artifact contains duplicate contexts")
    return tuple(output)


def predict_stage(observation: StageLearningObservation, params: R1Parameters) -> int | None:
    if observation.observation == "started":
        if (
            observation.exchange_session_age == 0
            and observation.probe_volume_ratio20 is not None
            and observation.probe_volume_ratio20 >= params.probe_volume_ratio20_min
        ):
            return 3
        return None
    if (
        observation.phase == "active"
        and observation.observation == "held"
        and observation.exchange_session_age > 0
        and not observation.previous_loss
        and observation.hold_count >= params.acceptance_hold_sessions
        and observation.signed_distance is not None
        and observation.signed_distance >= params.acceptance_signed_distance_min
        and observation.acceptance_activity_mean is not None
        and observation.acceptance_activity_mean
        >= params.acceptance_window_volume_ratio20_min
    ):
        return 4
    return None


def _wilson_lower(successes: int, total: int) -> float | None:
    if total <= 0:
        return None
    z = 1.6448536269514722
    p = successes / total
    denom = 1.0 + z * z / total
    centre = p + z * z / (2 * total)
    radius = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return max(0.0, (centre - radius) / denom)


def score_predictions(
    references: Sequence[ResolvedReference],
    predictions: Mapping[str, int | None],
    *,
    trial_id: str,
) -> TrialScore:
    if not references:
        raise StageLearningError("resolved reference corpus is empty")
    ids = {item.context_id for item in references}
    if set(predictions) != ids:
        raise StageLearningError("prediction identities differ from resolved reference corpus")
    definite = [item for item in references if item.reference_kind is ReferenceKind.DEFINITE]
    ambiguous = [item for item in references if item.reference_kind is ReferenceKind.AMBIGUOUS_SET]
    unjudgeable = [item for item in references if item.reference_kind is ReferenceKind.UNJUDGEABLE]
    if not definite:
        raise StageLearningError("resolved corpus has no definite references")
    definite_positive = [item for item in definite if item.supported_stage_codes]
    definite_negative = [item for item in definite if not item.supported_stage_codes]
    if not definite_positive:
        raise StageLearningError("resolved corpus has no positive definite references")
    stage_metrics: dict[int, dict[str, float | int | None]] = {}
    f1_values: list[float] = []
    for stage in (3, 4):
        tp = fp = fn = 0
        for item in definite:
            if len(item.supported_stage_codes) > 1:
                raise StageLearningError(
                    "definite reference cannot contain multiple first-group stages"
                )
            truth = stage in item.supported_stage_codes
            predicted = predictions[item.context_id] == stage
            tp += int(truth and predicted)
            fp += int(not truth and predicted)
            fn += int(truth and not predicted)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        f1_values.append(f1)
        stage_metrics[stage] = {
            "truth_count": sum(stage in item.supported_stage_codes for item in definite),
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": precision,
            "precision_wilson_one_sided_95_lower": _wilson_lower(tp, tp + fp),
            "recall": recall,
            "f1": f1,
        }
    confirmed = sum(predictions[item.context_id] in {3, 4} for item in definite)
    positive_covered = sum(
        predictions[item.context_id] in set(item.supported_stage_codes)
        for item in definite_positive
    )
    negative_false_positive = sum(
        predictions[item.context_id] in {3, 4} for item in definite_negative
    )
    ambiguous_unique = sum(predictions[item.context_id] in {3, 4} for item in ambiguous)
    unjudgeable_unique = sum(predictions[item.context_id] in {3, 4} for item in unjudgeable)
    return TrialScore(
        trial_id=trial_id,
        definite_count=len(definite),
        confirmed_count=confirmed,
        coverage=confirmed / len(definite),
        definite_positive_count=len(definite_positive),
        definite_negative_count=len(definite_negative),
        positive_reference_covered=positive_covered,
        positive_reference_coverage=positive_covered / len(definite_positive),
        definite_negative_false_positive_count=negative_false_positive,
        definite_negative_false_positive_rate=(
            negative_false_positive / len(definite_negative) if definite_negative else 0.0
        ),
        macro_f1=sum(f1_values) / len(f1_values),
        stage_metrics=stage_metrics,
        ambiguous_count=len(ambiguous),
        ambiguous_unique_assertions=ambiguous_unique,
        ambiguous_unique_assertion_rate=(
            ambiguous_unique / len(ambiguous) if ambiguous else None
        ),
        unjudgeable_count=len(unjudgeable),
        unjudgeable_unique_assertions=unjudgeable_unique,
    )


def load_resolved_references(root: Path) -> tuple[ResolvedReference, ...]:
    try:
        verified = verify_resolution_artifact(root)
    except ReviewLedgerError as exc:
        raise StageLearningError(str(exc)) from exc
    path = root.expanduser().resolve() / "resolved-references.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise StageLearningError("resolved reference ledger is unreadable") from exc
    output: list[ResolvedReference] = []
    for number, line in enumerate(lines, 1):
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise StageLearningError(f"resolved reference line {number} is invalid JSON") from exc
        if not isinstance(raw, Mapping) or raw.get("schema_version") != (
            "quanthus.stage-reference-resolution.v1"
        ):
            raise StageLearningError("resolved reference schema differs")
        try:
            output.append(
                ResolvedReference(
                    context_id=str(raw["context_id"]),
                    reference_kind=ReferenceKind(str(raw["reference_kind"])),
                    supported_stage_codes=tuple(int(item) for item in raw["supported_stage_codes"]),
                    adjacent_contexts=tuple(str(item) for item in raw["adjacent_contexts"]),
                    resolution=str(raw["resolution"]),
                    primary_reviewer_ids=tuple(str(item) for item in raw["primary_reviewer_ids"]),  # type: ignore[arg-type]
                    adjudicator_id=(
                        None if raw["adjudicator_id"] is None else str(raw["adjudicator_id"])
                    ),
                    evidence_digest=str(raw["evidence_digest"]),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise StageLearningError(f"resolved reference line {number} is invalid") from exc
    if len(output) != verified["resolved_count"]:
        raise StageLearningError("resolved reference ledger count differs from its manifest")
    if len({item.context_id for item in output}) != len(output):
        raise StageLearningError("resolved reference ledger contains duplicate contexts")
    return tuple(output)

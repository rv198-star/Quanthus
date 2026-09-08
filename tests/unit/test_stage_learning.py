from __future__ import annotations

import json
from decimal import Decimal

import pytest

from quanthus.policy import find_project_root
from quanthus.stage_learning.learning import (
    R1Parameters,
    StageLearningError,
    StageLearningObservation,
    enumerate_r1_trials,
    predict_stage,
    r0_parameters,
    score_predictions,
)
from quanthus.stage_learning.reference import ReferenceKind, ResolvedReference

ROOT = find_project_root()


def resolved(
    context_id: str,
    kind: ReferenceKind,
    stages: tuple[int, ...],
) -> ResolvedReference:
    return ResolvedReference(
        context_id=context_id,
        reference_kind=kind,
        supported_stage_codes=stages,
        adjacent_contexts=(),
        resolution="primary_agreement",
        primary_reviewer_ids=("reviewer-a", "reviewer-b"),
        adjudicator_id=None,
        evidence_digest="sha256:" + "a" * 64,
    )


def observation(**overrides: object) -> StageLearningObservation:
    values: dict[str, object] = {
        "context_id": "ctx",
        "anchor_scale": 20,
        "observation": "started",
        "phase": "active",
        "exchange_session_age": 0,
        "hold_count": 1,
        "previous_loss": False,
        "signed_distance": Decimal("0.01"),
        "probe_volume_ratio20": Decimal("1.30"),
        "acceptance_activity_mean": Decimal("1.10"),
    }
    values.update(overrides)
    return StageLearningObservation(**values)  # type: ignore[arg-type]


def test_frozen_r1_search_space_is_exactly_18_trials_and_contains_r0() -> None:
    experiment = json.loads((ROOT / "config/stage-learning/experiment.v1.json").read_text())
    trials = enumerate_r1_trials(experiment)
    assert len(trials) == 18
    assert len({item.trial_id for item in trials}) == 18
    assert r0_parameters(experiment) in trials


def test_stage3_prediction_uses_only_current_probe_evidence() -> None:
    params = R1Parameters(Decimal("1.25"), 2, Decimal("0"), Decimal("1"))
    assert predict_stage(observation(), params) == 3
    assert predict_stage(observation(probe_volume_ratio20=Decimal("1.24")), params) is None
    assert predict_stage(observation(probe_volume_ratio20=None), params) is None
    assert predict_stage(observation(exchange_session_age=1), params) is None


def test_stage4_prediction_requires_hold_distance_activity_and_no_loss() -> None:
    params = R1Parameters(Decimal("1.25"), 2, Decimal("0.0025"), Decimal("1"))
    held = observation(
        observation="held",
        exchange_session_age=2,
        hold_count=2,
        signed_distance=Decimal("0.003"),
        acceptance_activity_mean=Decimal("1.01"),
    )
    assert predict_stage(held, params) == 4
    assert predict_stage(observation(**{**held.__dict__, "hold_count": 1}), params) is None
    assert predict_stage(observation(**{**held.__dict__, "previous_loss": True}), params) is None
    assert predict_stage(observation(**{**held.__dict__, "signed_distance": None}), params) is None
    assert (
        predict_stage(observation(**{**held.__dict__, "acceptance_activity_mean": None}), params)
        is None
    )


def test_scoring_keeps_definite_ambiguous_and_unjudgeable_roles_separate() -> None:
    refs = (
        resolved("a", ReferenceKind.DEFINITE, (3,)),
        resolved("b", ReferenceKind.DEFINITE, (4,)),
        resolved("c", ReferenceKind.DEFINITE, ()),
        resolved("d", ReferenceKind.AMBIGUOUS_SET, (3, 4)),
        resolved("e", ReferenceKind.UNJUDGEABLE, ()),
    )
    score = score_predictions(
        refs,
        {"a": 3, "b": 4, "c": None, "d": 3, "e": None},
        trial_id="trial",
    )
    assert score.definite_count == 3
    assert score.confirmed_count == 2
    assert score.coverage == pytest.approx(2 / 3)
    assert score.macro_f1 == 1.0
    assert score.stage_metrics[3]["precision"] == 1.0
    assert score.stage_metrics[4]["precision"] == 1.0
    assert score.ambiguous_count == 1
    assert score.ambiguous_unique_assertions == 1
    assert score.ambiguous_unique_assertion_rate == 1.0
    assert score.unjudgeable_unique_assertions == 0


def test_scoring_refuses_missing_predictions_or_no_definite_reference() -> None:
    refs = (resolved("a", ReferenceKind.DEFINITE, (3,)),)
    with pytest.raises(StageLearningError, match="identities differ"):
        score_predictions(refs, {}, trial_id="trial")
    with pytest.raises(StageLearningError, match="no definite"):
        score_predictions(
            (resolved("b", ReferenceKind.AMBIGUOUS_SET, (3, 4)),),
            {"b": None},
            trial_id="trial",
        )


def test_definite_reference_cannot_be_multi_positive_in_same_slot() -> None:
    refs = (resolved("a", ReferenceKind.DEFINITE, (3, 4)),)
    with pytest.raises(StageLearningError, match="multiple"):
        score_predictions(refs, {"a": 3}, trial_id="trial")


def test_wilson_lower_bound_is_reported_not_naive_point_precision() -> None:
    refs = tuple(resolved(str(index), ReferenceKind.DEFINITE, (3,)) for index in range(10))
    score = score_predictions(
        refs,
        {str(index): 3 for index in range(10)},
        trial_id="trial",
    )
    metric = score.stage_metrics[3]
    assert metric["precision"] == 1.0
    assert 0.7 < float(metric["precision_wilson_one_sided_95_lower"]) < 1.0

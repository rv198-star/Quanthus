from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from quanthus.expert_evaluation.contracts import (
    ArtifactRef,
    EvidenceQuality,
    EvidenceRef,
    ExpertEvaluationSpec,
    ExpertObservation,
    ObservationApplicability,
    RawValueType,
    SubjectKind,
    derive_prediction_id,
)
from quanthus.expert_evaluation.resolver import P4LabelRow, resolve_observation
from quanthus.expert_evaluation.scoring import (
    EvaluationRow,
    block_bootstrap_interval,
    build_descriptive_scorecard,
    close_sample_ledger,
)
from quanthus.expert_evaluation.serialization import (
    ExpertEvaluationError,
    decode_artifact,
    digest_value,
)

ROOT = Path(__file__).resolve().parents[2]


def _spec() -> ExpertEvaluationSpec:
    path = (
        ROOT
        / "config/expert-evaluations/specs/"
        "trend-stability-p4-first-passage.evaluation.v1.json"
    )
    return decode_artifact(path.read_bytes(), ExpertEvaluationSpec)


def _observation(subject: str, value: str | None = "stable") -> ExpertObservation:
    spec = _spec()
    cutoff = datetime(2020, 1, 2, 7, tzinfo=timezone.utc)
    anchor = datetime(2020, 1, 2, 7, 1, tzinfo=timezone.utc)
    context = digest_value({"subject": subject})
    prediction = derive_prediction_id(
        assessment_binding_digest=spec.assessment_binding_digest,
        subject_id=subject,
        observation_anchor=anchor,
        knowledge_cutoff=cutoff,
        evidence_context_digest=context,
    )
    applicable = value is not None
    return ExpertObservation(
        "producer.trend-stability",
        "1.0.0",
        prediction,
        spec.assessment_id,
        spec.assessment_version,
        spec.assessment_binding_digest,
        spec.forecast_spec_digest,
        subject,
        SubjectKind.OPPORTUNITY,
        anchor,
        cutoff,
        context,
        (EvidenceRef("evidence.test-feature", "1.0.0", context, cutoff),),
        (
            ObservationApplicability.APPLICABLE
            if applicable
            else ObservationApplicability.ABSTAINED
        ),
        "eligible" if applicable else "missing_required_t_close_trend_feature",
        RawValueType.ORDINAL,
        value,
        None,
        EvidenceQuality.VERIFIED,
    )


def _label(subject: str, outcome: str, status: str = "settled") -> P4LabelRow:
    return P4LabelRow(
        subject,
        date(2020, 1, 3),
        status,
        outcome,
        "settled_by_frozen_card",
        date(2020, 1, 31),
        "sha256:" + "c" * 64,
    )


def _refs() -> tuple[ArtifactRef, ArtifactRef]:
    return (
        ArtifactRef("resolver.p4-label-corpus-adapter", "1.0.0", "sha256:" + "a" * 64),
        ArtifactRef("p4-label-corpus.accepted", "1.0.0", "sha256:" + "b" * 64),
    )


def test_resolver_scores_registered_order_and_keeps_not_executable_ledger_only() -> None:
    resolver, corpus = _refs()
    settled = resolve_observation(
        _observation("event:one"),
        _label("event:one", "failure"),
        resolver=resolver,
        label_corpus=corpus,
    )
    not_executable = resolve_observation(
        _observation("event:two"),
        _label("event:two", "not_executable"),
        resolver=resolver,
        label_corpus=corpus,
    )

    assert settled.settlement is not None and str(settled.settlement.target_score) == "2"
    assert not_executable.ledger_state == "not_executable"
    assert not_executable.settlement is None


def test_resolver_has_no_expert_import_and_rejects_subject_mismatch() -> None:
    source = (ROOT / "src/quanthus/expert_evaluation/resolver.py").read_text()
    assert "expert_evaluation.experts" not in source
    resolver, corpus = _refs()
    with pytest.raises(ExpertEvaluationError, match="subject_mismatch"):
        resolve_observation(
            _observation("event:one"),
            _label("event:other", "neutral"),
            resolver=resolver,
            label_corpus=corpus,
        )


def test_sample_ledger_closes_and_rejects_missing_applicable_resolution() -> None:
    resolver, corpus = _refs()
    first = _observation("event:one", "neutral")
    second = _observation("event:two", None)
    resolved = resolve_observation(
        first,
        _label("event:one", "neutral"),
        resolver=resolver,
        label_corpus=corpus,
    )
    ledger = close_sample_ledger((EvaluationRow(first, resolved), EvaluationRow(second, None)))
    assert ledger.semantic_dict() == {
        "eligible": 2,
        "applicable": 1,
        "abstained": 1,
        "settled": 1,
        "pending": 0,
        "unavailable": 0,
        "not_executable": 0,
        "censored": 0,
    }
    with pytest.raises(ExpertEvaluationError, match="ledger_not_closed"):
        close_sample_ledger((EvaluationRow(first, None),))


def test_22_session_block_bootstrap_is_deterministic_and_drops_partial_tail() -> None:
    scores = tuple(
        (date(2020, 1, index + 1), value)
        for index, value in enumerate([0, 1] * 11)
    )
    from decimal import Decimal

    decimal_scores = tuple((day, Decimal(value)) for day, value in scores)
    first = block_bootstrap_interval(decimal_scores, seed_digest="sha256:" + "d" * 64)
    second = block_bootstrap_interval(decimal_scores, seed_digest="sha256:" + "d" * 64)
    assert first == second
    assert first[2] == 1


def test_descriptive_scorecard_reports_baseline_coverage_slices_and_non_claims() -> None:
    resolver, corpus = _refs()
    observation = _observation("event:one", "stable")
    resolution = resolve_observation(
        observation,
        _label("event:one", "failure"),
        resolver=resolver,
        label_corpus=corpus,
    )
    spec = _spec()
    scorecard = build_descriptive_scorecard(
        (EvaluationRow(observation, resolution, "industry.test"),),
        spec=spec,
        target_digest="sha256:" + "e" * 64,
        resolver=resolver,
        uncertainty_method=ArtifactRef(
            "uncertainty.nonoverlap-22-session-block-bootstrap",
            "1.0.0",
            "sha256:" + "f" * 64,
        ),
        calendar_year_slice=next(
            item for item in spec.slices if item.artifact_id == "slice.calendar-year"
        ),
    )

    results = {item.metric_id: item.value for item in scorecard.metric_results}
    assert results == {
        "metric.abstention-rate": 0,
        "metric.coverage": 1,
        "metric.mean-absolute-ordinal-error": 2,
    }
    assert scorecard.baseline_comparisons[0].baseline_value == 1
    assert len(scorecard.stability) == 3
    assert "no effectiveness claim" in " ".join(scorecard.limitations)

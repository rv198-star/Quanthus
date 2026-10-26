from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, localcontext

import pytest

from quanthus.stage_learning.head_comparison import (
    PilotPolicy,
    ReferencePoint,
    fit_pilot_calibration,
    pilot_metrics,
    pilot_probabilities,
    raw_metrics,
    validate_partition,
)
from quanthus.stage_learning.order_rule_screen import (
    PRIMARY_FIELDS,
    OrderRuleError,
    load_rule,
    stage2_predicate,
)
from quanthus.stage_learning.p5_assessment import P5AssessmentError

MODEL = "sha256:" + "a" * 64
REQUEST = "sha256:" + "b" * 64


def points(prefix="cal", day="2021-05-31", labels=(0, 0, 0, 1, 1, 1)):
    return tuple(ReferencePoint(f"{prefix}-{i}", f"s-{prefix}-{i}", f"e-{prefix}-{i}", day, y)
                 for i, y in enumerate(labels))


def calibrate(rows=None, scores=None):
    return fit_pilot_calibration(
        points() if rows is None else rows,
        (0.1, 0.2, 0.3, 0.7, 0.8, 0.9) if scores is None else scores,
        policy=PilotPolicy(), model_digest=MODEL, request_digest=REQUEST,
    )


def test_ties_ap_auc_and_unknown_common_denominator():
    rows = points(labels=(0, 1, None, 0, 1))
    result = raw_metrics(rows, (0.5, 0.5, 1.0, 0.1, 0.9))
    assert (result["definite"], result["unknown"], result["TP"], result["FP"]) == (4, 1, 2, 1)
    assert result["raw_score_AUC"] == 0.875
    assert result["raw_score_average_precision"] == pytest.approx(5 / 6)
    assert result["consumer_admission"] == "NOT_GRANTED"
    tied = raw_metrics(points(labels=(1, 0, 1, 0)), (0.5,) * 4)
    assert tied["raw_score_AUC"] == tied["raw_score_average_precision"] == 0.5


def test_absent_class_is_inconclusive_not_perfect_accuracy():
    result = raw_metrics(points(labels=(0, 0, None)), (0.1, 0.2, 0.3))
    assert result["raw_score_AUC"] is result["raw_score_average_precision"] is None
    assert result["reason"] == "REQUIRED_CLASS_MISSING_INCONCLUSIVE"
    unknown = raw_metrics(points(labels=(None, None)), (0.0, 1.0))
    assert unknown["definite"] == unknown["TP"] == unknown["FP"] == 0


def test_repeated_anchors_are_not_independent_issuer_episode_units():
    rows = points(labels=(0, 1, 0, 1))
    repeated = tuple(replace(r, security_id="same", episode_id="same") for r in rows)
    result = raw_metrics(repeated, (0.1, 0.9, 0.1, 0.9))
    assert result["definite"] == 4
    assert result["definite_issuer_clusters"] == result["definite_issuer_episode_clusters"] == 1
    cal = calibrate(repeated, (0.1, 0.9, 0.1, 0.9))
    assert cal.status == "UNQUALIFIED_NO_FIT" and cal.slope is None


def test_each_class_requires_distinct_issuers_not_just_many_anchors():
    rows = tuple(replace(r, security_id="positive") if r.label == 1 else r for r in points())
    cal = calibrate(rows)
    assert cal.counts["positive"] == 3 and cal.counts["positive_issuers"] == 1
    assert cal.status == "UNQUALIFIED_NO_FIT"


def test_pilot_is_order_invariant_reproducible_and_never_grants_formal_probability():
    scores = (0.1, 0.2, 0.3, 0.7, 0.8, 0.9)
    cal = calibrate()
    assert calibrate(tuple(reversed(points())), tuple(reversed(scores))) == cal
    assert cal.status == "EXPLORATORY_ONLY" and cal.formal_consumer_admission == "NOT_GRANTED"
    later = points("eval", "2021-09-15")
    probs = pilot_probabilities(cal, later, scores, model_digest=MODEL, request_digest=REQUEST)
    assert probs is not None and probs[0] < probs[-1]
    metrics = pilot_metrics(later, probs, clip=1e-12)
    assert metrics is not None and 0 < metrics["exploratory_Brier"] < 1
    assert metrics["exploratory_log_loss"] > 0


def test_unqualified_pilot_keeps_unknown_consumption_and_null_metrics():
    cal = calibrate(points(labels=(0, 0, 0, None, None, None)))
    assert len(cal.uses) == 6 and cal.counts["definite"] == 3 and cal.slope is None
    later = points("eval", "2021-09-15")
    probs = pilot_probabilities(cal, later, (0.5,) * 6, model_digest=MODEL, request_digest=REQUEST)
    assert probs is pilot_metrics(later, probs, clip=1e-12) is None


@pytest.mark.parametrize("field", ["sample_id", "security_id", "episode_id"])
def test_consumption_overlap_rejected_even_with_unknown_reference(field):
    earlier = points()
    later = list(points("eval", "2021-09-15", (None,) * 6))
    later[0] = replace(later[0], **{field: getattr(earlier[0], field)})
    with pytest.raises(P5AssessmentError, match="consumption_overlap"):
        validate_partition(later, earlier)


def test_earlier_and_equal_dates_rejected_without_reordering_or_silent_drop():
    for day in ("2021-05-30", "2021-05-31"):
        with pytest.raises(P5AssessmentError, match="time_order"):
            validate_partition(points("eval", day), points())
    with pytest.raises(P5AssessmentError, match="missing_consumption"):
        validate_partition(points(), ())


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -0.1, 1.1, True])
def test_nonfinite_or_invalid_scores_are_rejected(score):
    with pytest.raises(P5AssessmentError, match="score_alignment_or_range"):
        raw_metrics(points(labels=(0,)), (score,))


@pytest.mark.parametrize("change", [{"label": True}, {"anchor": "2025-01-01"},
                                   {"anchor": "20210531"}, {"security_id": ""}])
def test_invalid_or_protected_identity_rejected(change):
    with pytest.raises(P5AssessmentError):
        raw_metrics((replace(points()[0], **change),), (0.5,))


def test_duplicate_samples_and_misaligned_scores_rejected():
    with pytest.raises(P5AssessmentError, match="duplicate"):
        raw_metrics((points()[0], points()[0]), (0.1, 0.2))
    with pytest.raises(P5AssessmentError, match="alignment"):
        raw_metrics(points(), (0.1,))


def test_calibration_rejects_model_request_and_role_mismatch():
    cal = calibrate()
    for kw in ({"model_digest": REQUEST, "request_digest": REQUEST},
               {"model_digest": MODEL, "request_digest": MODEL}):
        with pytest.raises(P5AssessmentError, match="calibration_binding"):
            pilot_probabilities(cal, points("eval", "2021-09-15"), (0.5,) * 6, **kw)
    with pytest.raises(P5AssessmentError, match="consumption_overlap"):
        pilot_probabilities(cal, points(), (0.5,) * 6, model_digest=MODEL, request_digest=REQUEST)


@pytest.mark.parametrize("change", [{"steps": True}, {"steps": 1001}, {"clip": 0.5},
                                   {"learning_rate": float("inf")}, {"l2": -1}])
def test_unbounded_or_nonfinite_pilot_policy_rejected(change):
    with pytest.raises(P5AssessmentError, match="pilot_"):
        replace(PilotPolicy(), **change).validate()


def test_reusable_exact_r0_preserves_boundaries_and_no_assertion_meaning():
    rule = load_rule()
    values = dict(zip(PRIMARY_FIELDS,
                      map(Decimal, ("-0.33", "0.18", "-0.20", "-0.04")), strict=True))
    assert stage2_predicate(values, rule)
    values["directional_efficiency"] = Decimal("0.330000000000000000000000001")
    with localcontext() as context:
        context.prec = 2
        assert not stage2_predicate(values, rule)
    rule["rules"]["stage_2"]["range_width20_max"] = "0.99"
    with pytest.raises(OrderRuleError, match="rule_drift"):
        stage2_predicate(values, rule)


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), 0.1])
def test_r0_rejects_nonfinite_and_non_decimal_values(value):
    values = dict(zip(PRIMARY_FIELDS, map(Decimal, ("0.2", "0.1", "-0.25", "0.01")), strict=True))
    values["range_width"] = value
    with pytest.raises(OrderRuleError):
        stage2_predicate(values, load_rule())

"""Bounded, descriptive Head2 comparison; no source reader or consumer admission.

The caller freezes and qualifies references first. UNKNOWN is never a negative.
Small-sample calibration is exploratory, cannot change formal assessment states,
and cannot satisfy the separate P5 or P6 acceptance contracts.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from typing import Any, cast

from quanthus.stage_learning.local_head import RowUse
from quanthus.stage_learning.order_rule_screen import PRIMARY_FIELDS, RULE_DIGEST, OrderRuleError
from quanthus.stage_learning.p5_assessment import P5AssessmentError
from quanthus.stage_learning.p5_contracts import require_digest
from quanthus.structure_v2_inputs import digest


def stage2_predicate(values: Mapping[str, Decimal], rule: Mapping[str, Any]) -> bool:
    """Exact R0 stage2 adapter; the hash-frozen legacy implementation stays immutable.

    A false predicate is NO_ASSERTION, not a semantic negative reference. The caller
    owns source/packet/scale admission. Thresholds come from the exact frozen rule;
    contract tests compare this four-condition adapter with legacy evaluate_case.
    """
    if digest(rule) != RULE_DIGEST:
        raise OrderRuleError("rule_drift_requires_new_version")
    if set(values) != set(PRIMARY_FIELDS) or any(
        not isinstance(v, Decimal) or not v.is_finite() for v in values.values()
    ):
        raise OrderRuleError("invalid_stage2_values")
    e = values["directional_efficiency"].copy_abs()
    w, d = values["range_width"], values["width_change"]
    c = values["center_shift"].copy_abs()
    if e > 1 or not 0 <= w < 2 or d < -1 or values["center_shift"] <= -1:
        raise OrderRuleError("formal_measurement_range_invalid")
    two = {key: Decimal(value) for key, value in rule["rules"]["stage_2"].items()}
    return (e <= two["efficiency20_abs_max"] and w <= two["range_width20_max"]
            and d <= two["width_change20_max"] and c <= two["center_shift20_abs_max"])


@dataclass(frozen=True)
class ReferencePoint:
    sample_id: str
    security_id: str
    episode_id: str
    anchor: str
    label: int | None


def _points(points: Sequence[ReferencePoint]) -> None:
    if not 1 <= len(points) <= 128 or len({p.sample_id for p in points}) != len(points):
        raise P5AssessmentError("head_comparison_case_budget_or_duplicate")
    for p in points:
        if not all(isinstance(v, str) and v for v in (p.sample_id, p.security_id, p.episode_id)):
            raise P5AssessmentError("head_comparison_identity")
        try:
            day = date.fromisoformat(p.anchor)
        except (TypeError, ValueError) as exc:
            raise P5AssessmentError("head_comparison_date") from exc
        if not date(2012, 1, 1) <= day <= date(2021, 12, 31) or day.isoformat() != p.anchor:
            raise P5AssessmentError("head_comparison_protected_or_noncanonical_date")
        if p.label is not None and (type(p.label) is not int or p.label not in (0, 1)):
            raise P5AssessmentError("head_comparison_reference_label")


def validate_partition(
    points: Sequence[ReferencePoint], earlier: Sequence[ReferencePoint] | Sequence[RowUse]
) -> None:
    """Every slot, including UNKNOWN, must be later and identity-disjoint.

    Within-role repeated issuer/episode anchors remain clustered, not independent
    units. Across roles, shared security OR shared episode is rejected.
    """
    _points(points)
    if not earlier:
        raise P5AssessmentError("head_comparison_missing_consumption_ledger")
    for field in ("sample_id", "security_id", "episode_id"):
        if {getattr(p, field) for p in points} & {getattr(p, field) for p in earlier}:
            raise P5AssessmentError("head_comparison_consumption_overlap")
    if min(p.anchor for p in points) <= max(p.anchor for p in earlier):
        raise P5AssessmentError("head_comparison_time_order")


def _scores(points: Sequence[ReferencePoint], scores: Sequence[float]) -> None:
    _points(points)
    if len(points) != len(scores) or any(
        type(s) not in (int, float) or not math.isfinite(s) or not 0 <= s <= 1
        for s in scores
    ):
        raise P5AssessmentError("head_comparison_score_alignment_or_range")


@dataclass(frozen=True)
class PilotPolicy:
    minimum_cases: int = 6
    minimum_each_class: int = 2
    minimum_issuers_each_class: int = 2
    minimum_issuers: int = 3
    steps: int = 200
    learning_rate: float = 0.05
    l2: float = 0.01
    clip: float = 1e-12

    def validate(self) -> None:
        if any(type(n) is not int or not 1 <= n <= 128 for n in (
            self.minimum_cases, self.minimum_each_class, self.minimum_issuers_each_class,
            self.minimum_issuers,
        )) or type(self.steps) is not int or not 1 <= self.steps <= 1000:
            raise P5AssessmentError("head_comparison_pilot_budget")
        if not all(type(x) in (int, float) and math.isfinite(x) for x in (
            self.learning_rate, self.l2, self.clip,
        )) or not (0 < self.learning_rate <= 1 and 0 <= self.l2 <= 1 and 0 < self.clip < 0.5):
            raise P5AssessmentError("head_comparison_pilot_policy")


@dataclass(frozen=True)
class PilotCalibration:
    status: str
    slope: float | None
    intercept: float | None
    policy: PilotPolicy
    model_digest: str
    request_digest: str
    reference_digest: str
    uses: tuple[ReferencePoint, ...]
    counts: Mapping[str, int]
    formal_consumer_admission: str = "NOT_GRANTED"


def _sigmoid(z: float) -> float:
    e = math.exp(-abs(z))
    return 1 / (1 + e) if z >= 0 else e / (1 + e)


def _logit(score: float, clip: float) -> float:
    p = min(1 - clip, max(clip, score))
    return math.log(p) - math.log1p(-p)


def fit_pilot_calibration(
    points: Sequence[ReferencePoint], scores: Sequence[float], *, policy: PilotPolicy,
    model_digest: str, request_digest: str,
) -> PilotCalibration:
    """One fixed fit on admitted calibration-role rows; no evaluation access.

    The returned ledger retains UNKNOWN consumption too. A caller must validate
    role/exposure separation; this function does not create reference authority.
    """
    policy.validate()
    require_digest(model_digest)
    require_digest(request_digest)
    _scores(points, scores)
    paired = sorted(zip(points, scores, strict=True), key=lambda pair: pair[0].sample_id)
    definite = [(p, s) for p, s in paired if p.label is not None]
    counts = {
        "slots": len(points), "definite": len(definite),
        "positive": sum(p.label == 1 for p, _ in definite),
        "negative": sum(p.label == 0 for p, _ in definite),
        "issuers": len({p.security_id for p, _ in definite}),
        "positive_issuers": len({p.security_id for p, _ in definite if p.label == 1}),
        "negative_issuers": len({p.security_id for p, _ in definite if p.label == 0}),
    }
    encoded = json.dumps([(asdict(p), s) for p, s in paired], sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode()
    reference_digest = "sha256:" + hashlib.sha256(encoded).hexdigest()
    qualified = (counts["definite"] >= policy.minimum_cases
                 and min(counts["positive"], counts["negative"]) >= policy.minimum_each_class
                 and counts["issuers"] >= policy.minimum_issuers
                 and min(counts["positive_issuers"], counts["negative_issuers"])
                 >= policy.minimum_issuers_each_class)
    slope = intercept = None
    if qualified:
        slope, intercept = 1.0, 0.0
        xs = [_logit(s, policy.clip) for _, s in definite]
        for _ in range(policy.steps):
            errors = [_sigmoid(slope * x + intercept) - cast(int, p.label)
                      for (p, _), x in zip(definite, xs, strict=True)]
            slope -= policy.learning_rate * (
                math.fsum(e * x for e, x in zip(errors, xs, strict=True)) / len(xs)
                + policy.l2 * slope
            )
            intercept -= policy.learning_rate * math.fsum(errors) / len(xs)
            if not math.isfinite(slope) or not math.isfinite(intercept):
                raise P5AssessmentError("head_comparison_calibration_nonfinite")
    return PilotCalibration(
        "EXPLORATORY_ONLY" if qualified else "UNQUALIFIED_NO_FIT", slope, intercept,
        policy, model_digest, request_digest, reference_digest, tuple(p for p, _ in paired), counts,
    )


def pilot_probabilities(
    calibration: PilotCalibration, points: Sequence[ReferencePoint], scores: Sequence[float],
    *, model_digest: str, request_digest: str,
) -> tuple[float, ...] | None:
    """Descriptive pilot output only; never pass it as formal P5 probability."""
    _scores(points, scores)
    if (model_digest, request_digest) != (calibration.model_digest, calibration.request_digest):
        raise P5AssessmentError("head_comparison_calibration_binding")
    validate_partition(points, calibration.uses)
    if calibration.status == "UNQUALIFIED_NO_FIT":
        return None
    if (calibration.status != "EXPLORATORY_ONLY" or calibration.slope is None
            or calibration.intercept is None
            or calibration.formal_consumer_admission != "NOT_GRANTED"):
        raise P5AssessmentError("head_comparison_calibration_status")
    calibration.policy.validate()
    if not math.isfinite(calibration.slope) or not math.isfinite(calibration.intercept):
        raise P5AssessmentError("head_comparison_calibration_nonfinite")
    return tuple(_sigmoid(calibration.slope * _logit(s, calibration.policy.clip)
                          + calibration.intercept) for s in scores)


def raw_metrics(points: Sequence[ReferencePoint], scores: Sequence[float]) -> dict[str, Any]:
    """Common fixed raw0.5 diagnostic; tie-aware AUC/AP, no independence claim."""
    _scores(points, scores)
    pairs = [(p, float(s)) for p, s in zip(points, scores, strict=True) if p.label is not None]
    positive = [s for p, s in pairs if p.label == 1]
    negative = [s for p, s in pairs if p.label == 0]
    both = bool(positive and negative)
    auc = (math.fsum(float(p > n) + 0.5 * float(p == n) for p in positive for n in negative)
           / (len(positive) * len(negative))) if both else None
    ap = None
    if both:
        tp = fp = 0
        ap = 0.0
        for score in sorted({s for _, s in pairs}, reverse=True):
            added = sum(p.label == 1 for p, s in pairs if s == score)
            tp += added
            fp += sum(p.label == 0 for p, s in pairs if s == score)
            ap += (added / len(positive)) * tp / (tp + fp)
    return {
        "slots": len(points), "definite": len(pairs), "unknown": len(points) - len(pairs),
        "positive": len(positive), "negative": len(negative),
        "definite_issuer_clusters": len({p.security_id for p, _ in pairs}),
        "definite_issuer_episode_clusters": len({(p.security_id, p.episode_id) for p, _ in pairs}),
        "TP": sum(p.label == 1 and s >= 0.5 for p, s in pairs),
        "FP": sum(p.label == 0 and s >= 0.5 for p, s in pairs),
        "TN": sum(p.label == 0 and s < 0.5 for p, s in pairs),
        "FN": sum(p.label == 1 and s < 0.5 for p, s in pairs),
        "raw_score_AUC": auc, "raw_score_average_precision": ap,
        "reason": "DESCRIPTIVE_ONLY" if both else "REQUIRED_CLASS_MISSING_INCONCLUSIVE",
        "consumer_admission": "NOT_GRANTED",
    }


def pilot_metrics(
    points: Sequence[ReferencePoint], probabilities: Sequence[float] | None, *, clip: float,
) -> dict[str, float] | None:
    if probabilities is None:
        return None
    _scores(points, probabilities)
    if not math.isfinite(clip) or not 0 < clip < 0.5:
        raise P5AssessmentError("head_comparison_probability_clip")
    pairs = [(p.label, min(1 - clip, max(clip, s)))
             for p, s in zip(points, probabilities, strict=True) if p.label is not None]
    if not pairs:
        return None
    return {
        "exploratory_Brier": math.fsum((s - y) ** 2 for y, s in pairs) / len(pairs),
        "exploratory_log_loss": math.fsum(-y * math.log(s) - (1 - y) * math.log1p(-s)
                                         for y, s in pairs) / len(pairs),
    }

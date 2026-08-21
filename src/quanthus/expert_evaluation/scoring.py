"""Closed-ledger descriptive scoring for the first ordinal expert proof."""

from __future__ import annotations

import hashlib
import random
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from quanthus.expert_evaluation.contracts import (
    ArtifactRef,
    BaselineComparison,
    DecisionChangeCounts,
    ExpertEvaluationSpec,
    ExpertObservation,
    ExpertScorecard,
    MetricResult,
    ObservationApplicability,
    SampleLedger,
    StabilityEstimate,
    UncertaintyEstimate,
)
from quanthus.expert_evaluation.resolver import Resolution
from quanthus.expert_evaluation.serialization import ExpertEvaluationError


@dataclass(frozen=True)
class EvaluationRow:
    observation: ExpertObservation
    resolution: Resolution | None
    static_industry_id: str | None = None


def close_sample_ledger(rows: Sequence[EvaluationRow]) -> SampleLedger:
    eligible = len(rows)
    abstained = sum(
        row.observation.applicability is ObservationApplicability.ABSTAINED for row in rows
    )
    states: defaultdict[str, int] = defaultdict(int)
    for row in rows:
        if row.observation.applicability is ObservationApplicability.ABSTAINED:
            if row.resolution is not None:
                raise ExpertEvaluationError(
                    "ledger_not_closed", "abstained observation cannot have a resolution"
                )
            continue
        if row.resolution is None or row.resolution.observation_digest != row.observation.digest:
            raise ExpertEvaluationError("ledger_not_closed", "applicable resolution is missing")
        states[row.resolution.ledger_state] += 1
    supported = {"settled", "pending", "unavailable", "not_executable", "censored"}
    if set(states).difference(supported):
        raise ExpertEvaluationError("ledger_not_closed", "resolution state is unsupported")
    return SampleLedger(
        eligible=eligible,
        applicable=eligible - abstained,
        abstained=abstained,
        settled=states["settled"],
        pending=states["pending"],
        unavailable=states["unavailable"],
        not_executable=states["not_executable"],
        censored=states["censored"],
    )


def _mean(values: Sequence[Decimal]) -> Decimal:
    if not values:
        raise ExpertEvaluationError("insufficient_sample", "settled corpus is empty")
    return sum(values, Decimal("0")) / Decimal(len(values))


def _settled_scores(rows: Sequence[EvaluationRow]) -> tuple[tuple[date, Decimal, Decimal], ...]:
    target_order = {"failure": 0, "neutral": 1, "positive": 2}
    result: list[tuple[date, Decimal, Decimal]] = []
    for row in rows:
        resolution = row.resolution
        if resolution is None or resolution.ledger_state != "settled":
            continue
        settlement = resolution.settlement
        if settlement is None or not isinstance(settlement.realized_value, str):
            raise ExpertEvaluationError("settlement_state_conflict", "settled row is incomplete")
        if settlement.realized_value not in target_order or settlement.target_score is None:
            raise ExpertEvaluationError("value_type_mismatch", "settled ordinal is unregistered")
        baseline_score = Decimal(abs(1 - target_order[settlement.realized_value]))
        result.append(
            (row.observation.observation_anchor.date(), settlement.target_score, baseline_score)
        )
    return tuple(result)


def _trend_regime(row: EvaluationRow) -> str:
    raw = row.observation.raw_value
    if raw == "neutral":
        return "mixed"
    return str(raw or "abstained")


def block_bootstrap_interval(
    scores: Sequence[tuple[date, Decimal]],
    *,
    seed_digest: str,
    trading_sessions: Sequence[date] | None = None,
    block_sessions: int = 22,
    resamples: int = 2000,
) -> tuple[Decimal, Decimal, int]:
    """Resample complete non-overlapping date blocks with a deterministic seed."""

    if block_sessions <= 0 or resamples <= 0:
        raise ExpertEvaluationError("invalid_metric", "bootstrap bounds must be positive")
    dates = sorted(
        set(trading_sessions) if trading_sessions is not None else {item[0] for item in scores}
    )
    if scores and (
        not dates
        or min(item[0] for item in scores) < dates[0]
        or max(item[0] for item in scores) > dates[-1]
    ):
        raise ExpertEvaluationError(
            "invalid_metric", "score dates fall outside the bound trading calendar"
        )
    blocks = [
        set(dates[index : index + block_sessions])
        for index in range(0, len(dates), block_sessions)
    ]
    complete = [block for block in blocks if len(block) == block_sessions]
    grouped = [tuple(value for day, value in scores if day in block) for block in complete]
    grouped = [block for block in grouped if block]
    if not grouped:
        values = tuple(value for _, value in scores)
        mean = _mean(values)
        return mean, mean, 0
    seed = int(hashlib.sha256(seed_digest.encode("ascii")).hexdigest()[:16], 16)
    generator = random.Random(seed)
    estimates: list[Decimal] = []
    for _ in range(resamples):
        sampled = [generator.choice(grouped) for _ in grouped]
        estimates.append(_mean(tuple(value for block in sampled for value in block)))
    estimates.sort()
    lower = estimates[int(Decimal("0.025") * Decimal(resamples - 1))]
    upper = estimates[int(Decimal("0.975") * Decimal(resamples - 1))]
    return lower, upper, len(grouped)


def build_descriptive_scorecard(
    rows: Sequence[EvaluationRow],
    *,
    spec: ExpertEvaluationSpec,
    target_digest: str,
    resolver: ArtifactRef,
    uncertainty_method: ArtifactRef,
    calendar_year_slice: ArtifactRef,
    trading_sessions: Sequence[date] | None = None,
) -> ExpertScorecard:
    ledger = close_sample_ledger(rows)
    settled = _settled_scores(rows)
    expert_value = _mean(tuple(item[1] for item in settled))
    baseline_value = _mean(tuple(item[2] for item in settled))
    coverage = Decimal(ledger.applicable) / Decimal(ledger.eligible)
    abstention = Decimal(ledger.abstained) / Decimal(ledger.eligible)
    metric_by_id = {item.metric_id: item for item in spec.metrics}
    required = {
        "metric.mean-absolute-ordinal-error",
        "metric.coverage",
        "metric.abstention-rate",
    }
    if not required.issubset(metric_by_id):
        raise ExpertEvaluationError("metric_not_registered", "descriptive metrics are incomplete")
    lower, upper, blocks = block_bootstrap_interval(
        tuple((day, score) for day, score, _ in settled),
        seed_digest=spec.digest,
        trading_sessions=trading_sessions,
    )
    stability: list[StabilityEstimate] = []
    for year in sorted({day.year for day, _, _ in settled}):
        values = tuple(score for day, score, _ in settled if day.year == year)
        stability.append(
            StabilityEstimate(
                ArtifactRef(
                    f"slice.calendar-year-{year}",
                    calendar_year_slice.version,
                    calendar_year_slice.digest,
                ),
                "metric.mean-absolute-ordinal-error",
                _mean(values),
                len(values),
            )
        )
    primary = metric_by_id["metric.mean-absolute-ordinal-error"]
    industry_slice = next(
        item
        for item in spec.slices
        if item.artifact_id == "slice.static-level-one-industry"
    )
    trend_slice = next(
        item for item in spec.slices if item.artifact_id == "slice.t-close-trend-regime"
    )
    for base, categories in (
        (
            industry_slice,
            sorted({row.static_industry_id or "missing" for row in rows}),
        ),
        (
            trend_slice,
            sorted({_trend_regime(row) for row in rows}),
        ),
    ):
        for value in categories:
            matching = tuple(
                row
                for row in rows
                if (
                    (base is industry_slice and (row.static_industry_id or "missing") == value)
                    or (
                        base is trend_slice
                        and _trend_regime(row) == value
                    )
                )
            )
            slice_scores = _settled_scores(matching)
            if not slice_scores:
                continue
            stability.append(
                StabilityEstimate(
                    ArtifactRef(
                        (
                            f"{base.artifact_id}."
                            f"{hashlib.sha256(value.encode('utf-8')).hexdigest()[:16]}"
                        ),
                        base.version,
                        base.digest,
                    ),
                    primary.metric_id,
                    _mean(tuple(item[1] for item in slice_scores)),
                    len(slice_scores),
                )
            )
    baseline = spec.baselines[0]
    return ExpertScorecard(
        producer_id="producer.trend-stability-scorecard",
        producer_version="1.0.0",
        evaluation_spec_digest=spec.digest,
        assessment_id=spec.assessment_id,
        assessment_version=spec.assessment_version,
        assessment_binding_digest=spec.assessment_binding_digest,
        forecast_spec_digest=spec.forecast_spec_digest,
        target_digest=target_digest,
        resolver_digest=resolver.digest,
        population=spec.population,
        partition=spec.partition,
        sample_ledger=ledger,
        metric_definitions=spec.metrics,
        metric_results=(
            MetricResult(primary.metric_id, primary.metric_version, expert_value, len(settled)),
            MetricResult(
                "metric.coverage",
                metric_by_id["metric.coverage"].metric_version,
                coverage,
                ledger.eligible,
            ),
            MetricResult(
                "metric.abstention-rate",
                metric_by_id["metric.abstention-rate"].metric_version,
                abstention,
                ledger.eligible,
            ),
        ),
        uncertainty=(
            UncertaintyEstimate(
                primary.metric_id,
                uncertainty_method,
                lower,
                upper,
                Decimal(blocks),
                "Rows sharing event windows, dates, securities, or market shocks are not "
                "independent; complete non-overlapping 22-session date blocks are resampled.",
            ),
        ),
        stability=tuple(stability),
        baseline_comparisons=(
            BaselineComparison(baseline, primary.metric_id, expert_value, baseline_value),
        ),
        decision_change_counts=DecisionChangeCounts(ledger.settled, 0, 0, 0, 0),
        limitations=(
            "2020-2021 development engineering proof only; no effectiveness claim.",
            "No strategy admission, portfolio-value inference, action authority, or "
            "post-2021 data access.",
        ),
    )

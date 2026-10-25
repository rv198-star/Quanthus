"""Small shared P3 comparison primitives; no reader, fit, replay or admission.

One immutable context, governance decision and action policy are shared by both
assessment arms. Model outputs remain ordinary expert scores, subject to all
existing evidence, veto and constraint checks. Execution/accounting stays in P2.
This helper does not qualify labels or authorize a new research purpose.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from quanthus.aggregator import (
    GovernanceResult,
    ScoreActionPolicy,
    ScoreActionResult,
    aggregate_governance,
    aggregate_score_and_map_action,
)
from quanthus.expert_protocols import (
    ConstraintOutput,
    ExpertContext,
    ScoringOutput,
    VetoOutput,
)
from quanthus.expert_strategy.p3_history_review import require


@dataclass(frozen=True)
class EntryAssessmentComparison:
    governance: GovernanceResult
    baseline: ScoreActionResult
    candidate: ScoreActionResult


def compare_entry_assessments(
    *,
    context: ExpertContext,
    vetoes: Sequence[VetoOutput],
    constraints: Sequence[ConstraintOutput],
    baseline: Sequence[ScoringOutput],
    candidate: Sequence[ScoringOutput],
    policy: ScoreActionPolicy,
) -> EntryAssessmentComparison:
    """Reuse one factual governance result, changing only scoring assessments.

    This is an entry-decision comparison, not a held-position/exit simulator.
    Callers must separately qualify their exact model/reference/input closure.
    Unknown model support is represented by an abstained ScoringOutput, never
    recoded to bearish support. Missing factual inputs still require the normal
    system BLOCK_NEW veto; a model cannot turn that veto into a sell instruction.
    """
    governance = aggregate_governance(context, vetoes, constraints)
    return EntryAssessmentComparison(
        governance,
        aggregate_score_and_map_action(context, governance, baseline, policy),
        aggregate_score_and_map_action(context, governance, candidate, policy),
    )


def paired_intervals(left: list[float], right: list[float], seed: int) -> dict[str, Any]:
    """Existing descriptive 22-session/2,000-resample comparison, unchanged.

    Retain historical reason codes and rank endpoints so accepted BH reports
    remain reproducible. This is not a new confirmatory statistical policy.
    """
    import numpy as np

    require(len(left) == len(right) and len(left) >= 44, "BH_INTERVAL_PAIRING")
    count = len(left) // 22
    rng = random.Random(seed)
    indices = np.asarray([[rng.randrange(count) for _ in range(count)] for _ in range(2000)])
    paths = []
    for series in (left, right):
        raw = np.asarray(series, dtype=float)
        require(bool(np.isfinite(raw).all()) and bool((raw > -1).all()), "BH_INTERVAL_RETURNS")
        returns = raw[: count * 22].reshape(count, 22)[indices].reshape(2000, count * 22)
        wealth = np.concatenate([np.ones((2000, 1)), np.cumprod(1 + returns, axis=1)], axis=1)
        paths.append(
            {
                "net_total_return": wealth[:, -1] - 1,
                "maximum_drawdown": np.max(
                    1 - wealth / np.maximum.accumulate(wealth, axis=1), axis=1
                ),
            }
        )
    return {
        name: {"lower": str(ordered[49]), "upper": str(ordered[1949])}
        for name in paths[0]
        for ordered in [np.sort(paths[0][name] - paths[1][name])]
    }

"""Read-only descriptive analysis of the already admitted P3 four-way outputs.

This pure calculation module grants no source/result access or strict D admission.
It retains the frozen request's contrasts, materiality and paired block sampling.
"""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import Any

VARIANTS = ("B", "T", "R", "TR")
CONTRASTS = {
    "TR-B": {"TR": 1, "B": -1},
    "T-B": {"T": 1, "B": -1},
    "R-B": {"R": 1, "B": -1},
    "TR-R": {"TR": 1, "R": -1},
    "TR-T": {"TR": 1, "T": -1},
    "TR-T-R+B": {"TR": 1, "T": -1, "R": -1, "B": 1},
}
ZERO = Decimal(0)
ONE = Decimal(1)


def path_metrics(returns: Sequence[Decimal]) -> dict[str, Decimal]:
    """Compound returns and measure positive peak-to-trough loss, including start capital."""
    wealth = peak = ONE
    drawdown = ZERO
    for value in returns:
        if not value.is_finite() or value <= -ONE:
            raise ValueError("P3_ANALYSIS_INVALID_RETURN")
        wealth *= ONE + value
        peak = max(peak, wealth)
        drawdown = max(drawdown, (peak - wealth) / peak)
    return {"net_total_return": wealth - ONE, "maximum_drawdown": drawdown}


def classify_headline(
    delta: Mapping[str, Decimal], intervals: Mapping[str, Mapping[str, Decimal]]
) -> dict[str, bool | str]:
    """Apply frozen one-percentage-point criteria without selecting a diagnostic winner."""
    ret, dd = delta["net_total_return"], delta["maximum_drawdown"]
    materiality = Decimal("0.01")
    supported_return = (
        ret >= materiality and intervals["net_total_return"]["lower"] > ZERO and dd <= materiality
    )
    supported_drawdown = (
        dd <= -materiality and intervals["maximum_drawdown"]["upper"] < ZERO and ret >= -materiality
    )
    harm = (ret <= -materiality and intervals["net_total_return"]["upper"] < ZERO) or (
        dd >= materiality and intervals["maximum_drawdown"]["lower"] > ZERO
    )
    return {
        "return_increment_supported": supported_return,
        "drawdown_increment_supported": supported_drawdown,
        "material_harm_supported": harm,
        "disposition": (
            "MATERIAL_HARM"
            if harm
            else "SUPPORTED_INCREMENT"
            if supported_return or supported_drawdown
            else "INCONCLUSIVE_OR_ECONOMICALLY_NEGLIGIBLE"
        ),
        "claim_ceiling": "DESCRIPTIVE_RETROSPECTIVE_ONLY",
    }


def analyze_returns(
    request_id: str,
    series: Mapping[str, Sequence[Mapping[str, str]]],
    point_metrics: Mapping[str, Mapping[str, Decimal]],
) -> dict[str, Any]:
    """Use 2,000 paired nonoverlapping 22-session block resamples for each contrast.

    Exact full-window point estimates come from replay equity, not rounded daily returns.
    Bootstrap paths use the preserved eight-decimal daily-return evidence. The trailing
    incomplete block is explicitly excluded only from resampling, never from point estimates.
    """
    if set(series) != set(VARIANTS) or set(point_metrics) != set(VARIANTS):
        raise ValueError("P3_ANALYSIS_VARIANT_SET")
    dates = [row["market_date"] for row in series["B"]]
    if dates != sorted(set(dates)) or any(
        [row["market_date"] for row in series[v]] != dates for v in VARIANTS
    ):
        raise ValueError("P3_ANALYSIS_PAIRED_DATES")
    if any(not date(2025, 1, 1) <= date.fromisoformat(d) <= date(2026, 9, 18) for d in dates):
        raise ValueError("P3_ANALYSIS_WINDOW")
    values = {v: [Decimal(row["return"]) for row in series[v]] for v in VARIANTS}
    for v in VARIANTS:
        path_metrics(values[v])
        if any(not n.is_finite() for n in point_metrics[v].values()):
            raise ValueError("P3_ANALYSIS_INVALID_METRIC")
    block_count = len(dates) // 22
    if not block_count:
        raise ValueError("P3_ANALYSIS_NO_COMPLETE_BLOCK")
    blocks = {v: [values[v][i * 22 : (i + 1) * 22] for i in range(block_count)] for v in VARIANTS}
    contrasts: dict[str, Any] = {}
    for name, weights in CONTRASTS.items():
        seed = int(hashlib.sha256(f"{request_id}:{name}".encode()).hexdigest(), 16)
        generator = random.Random(seed)
        estimates: dict[str, list[Decimal]] = {"net_total_return": [], "maximum_drawdown": []}
        for _ in range(2000):
            indices = [generator.randrange(block_count) for _ in range(block_count)]
            sample = {v: path_metrics([r for i in indices for r in blocks[v][i]]) for v in weights}
            for metric in estimates:
                estimates[metric].append(
                    sum((sample[v][metric] * weight for v, weight in weights.items()), ZERO)
                )
        intervals = {}
        for metric, observations in estimates.items():
            observations.sort()
            intervals[metric] = {
                "lower": observations[math.floor(0.025 * 1999)],
                "upper": observations[math.floor(0.975 * 1999)],
            }
        delta = {
            metric: sum((point_metrics[v][metric] * w for v, w in weights.items()), ZERO)
            for metric in estimates
        }
        contrasts[name] = {
            "role": "headline" if name == "TR-B" else "diagnostic_only",
            "point_delta": {k: str(v) for k, v in delta.items()},
            "paired_block_95_interval": {
                k: {bound: str(n) for bound, n in limits.items()} for k, limits in intervals.items()
            },
            "seed": str(seed),
        }
        if name == "TR-B":
            contrasts[name]["decision"] = classify_headline(delta, intervals)
    return {
        "schema_version": "quanthus.p3-retrospective-analysis.v1",
        "request_id": request_id,
        "claim_ceiling": "DESCRIPTIVE_RETROSPECTIVE_ONLY",
        "original_B_accepted": False,
        "original_D_activated": False,
        "production_authorized": False,
        "method": "paired_nonoverlap_22_session_block_bootstrap",
        "block_sessions": 22,
        "complete_block_count": block_count,
        "resamples": 2000,
        "confidence_level": "0.95",
        "full_window_sessions": len(dates),
        "bootstrap_excluded_trailing_dates": dates[block_count * 22 :],
        "point_estimate_scope": "all dates; exact replay equity ratios",
        "bootstrap_precision": "preserved eight-decimal daily returns",
        "dependence_disclosure": (
            "Block resampling does not establish independent observations or historical PIT. "
            "Diagnostics cannot promote a different variant; no post-result tuning."
        ),
        "contrasts": contrasts,
    }

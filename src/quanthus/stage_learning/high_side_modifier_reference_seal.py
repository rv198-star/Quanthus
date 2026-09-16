"""Pure P026 Stage8 blind-reference resolution and cluster-aware sufficiency logic."""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any


class HighSideModifierReferenceSealError(ValueError):
    """Raised when P026 sealed-reference inputs violate the frozen contract."""


def resolve_row(short: Mapping[str, Any], fund: Mapping[str, Any]) -> tuple[str, str]:
    left_context = str(short["advancing_context"])
    right_context = str(fund["advancing_context"])
    if left_context == right_context == "TRUE":
        context = "TRUE"
    elif left_context == right_context == "FALSE":
        context = "FALSE"
    else:
        context = "AMBIGUOUS"
    if context == "FALSE":
        return context, "NOT_APPLICABLE"
    if context == "AMBIGUOUS":
        return context, "AMBIGUOUS"
    left_stage8 = str(short["upside_acceleration_extension"])
    right_stage8 = str(fund["upside_acceleration_extension"])
    if left_stage8 == right_stage8 == "TRUE":
        stage8 = "TRUE"
    elif left_stage8 == right_stage8 == "FALSE":
        stage8 = "FALSE"
    else:
        stage8 = "AMBIGUOUS"
    return context, stage8


def resolve_rows(
    short_opinions: Sequence[Mapping[str, Any]],
    fund_opinions: Sequence[Mapping[str, Any]],
    case_map_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if not short_opinions or len(short_opinions) != len(fund_opinions):
        raise HighSideModifierReferenceSealError("row_count")
    if len(case_map_rows) != len(short_opinions):
        raise HighSideModifierReferenceSealError("case_map_count")
    map_by_case = {str(row["case_id"]): row for row in case_map_rows}
    if len(map_by_case) != len(case_map_rows):
        raise HighSideModifierReferenceSealError("case_map_unique")
    output: list[dict[str, Any]] = []
    for short, fund in zip(short_opinions, fund_opinions, strict=True):
        case_id = str(short["case_id"])
        if case_id != str(fund["case_id"]) or case_id not in map_by_case:
            raise HighSideModifierReferenceSealError("case_alignment")
        mapping = map_by_case[case_id]
        context, stage8 = resolve_row(short, fund)
        output.append(
            {
                "case_id": case_id,
                "cluster_id": str(mapping["cluster_id"]),
                "cell": str(mapping["cell"]),
                "window_start": str(mapping["window_start"]),
                "window_end": str(mapping["window_end"]),
                "short_term": {
                    "advancing_context": str(short["advancing_context"]),
                    "upside_acceleration_extension": str(
                        short["upside_acceleration_extension"]
                    ),
                    "reason": str(short["reason"]),
                },
                "fund": {
                    "advancing_context": str(fund["advancing_context"]),
                    "upside_acceleration_extension": str(
                        fund["upside_acceleration_extension"]
                    ),
                    "reason": str(fund["reason"]),
                },
                "resolved": {
                    "advancing_context": context,
                    "upside_acceleration_extension": stage8,
                },
            }
        )
    return sorted(output, key=lambda row: str(row["case_id"]))


def _rate_within(numerator: int, denominator: int, ceiling: float) -> bool:
    if denominator <= 0:
        return False
    ceiling_hundred = round(ceiling * 100)
    return numerator * 100 <= ceiling_hundred * denominator


def summarize(
    rows: Sequence[Mapping[str, Any]],
    case_map_rows: Sequence[Mapping[str, Any]],
    gate: Mapping[str, Any],
) -> dict[str, Any]:
    all_clusters = {str(row["cluster_id"]) for row in case_map_rows}
    if not all_clusters:
        raise HighSideModifierReferenceSealError("cluster_denominator")
    context_positive: set[str] = set()
    context_negative: set[str] = set()
    stage8_applicable: set[str] = set()
    stage8_positive: set[str] = set()
    stage8_negative: set[str] = set()
    cell_context_definite = Counter()
    row_context = Counter()
    row_stage8 = Counter()
    per_cell = defaultdict(
        lambda: {
            "rows": 0,
            "context_definite_rows": 0,
            "context_true_rows": 0,
            "context_false_rows": 0,
        }
    )
    for row in rows:
        cluster = str(row["cluster_id"])
        cell = str(row["cell"])
        resolved = row["resolved"]
        context = str(resolved["advancing_context"])
        stage8 = str(resolved["upside_acceleration_extension"])
        row_context[context] += 1
        row_stage8[stage8] += 1
        per_cell[cell]["rows"] += 1
        if context == "TRUE":
            context_positive.add(cluster)
            stage8_applicable.add(cluster)
            cell_context_definite[cell] += 1
            per_cell[cell]["context_definite_rows"] += 1
            per_cell[cell]["context_true_rows"] += 1
            if stage8 == "TRUE":
                stage8_positive.add(cluster)
            elif stage8 == "FALSE":
                stage8_negative.add(cluster)
        elif context == "FALSE":
            context_negative.add(cluster)
            cell_context_definite[cell] += 1
            per_cell[cell]["context_definite_rows"] += 1
            per_cell[cell]["context_false_rows"] += 1
    context_definite = context_positive | context_negative
    context_unjudgeable = all_clusters - context_definite
    stage8_definite = stage8_positive | stage8_negative
    stage8_unjudgeable = stage8_applicable - stage8_definite
    cell_names = {str(row["cell"]) for row in case_map_rows}
    checks = {
        "minimum_context_definite_clusters": (
            len(context_definite) >= int(gate["minimum_context_definite_clusters"])
        ),
        "minimum_context_positive_clusters": (
            len(context_positive) >= int(gate["minimum_context_positive_clusters"])
        ),
        "minimum_context_negative_clusters": (
            len(context_negative) >= int(gate["minimum_context_negative_clusters"])
        ),
        "maximum_context_unjudgeable_cluster_rate": _rate_within(
            len(context_unjudgeable),
            len(all_clusters),
            float(gate["maximum_context_unavailable_or_unjudgeable_cluster_rate"]),
        ),
        "context_definite_row_in_all_eight_cells": (
            len(cell_names) == 8
            and all(cell_context_definite[cell] > 0 for cell in cell_names)
        ),
        "minimum_stage8_definite_applicable_clusters": (
            len(stage8_definite) >= int(gate["minimum_stage8_applicable_definite_clusters"])
        ),
        "minimum_stage8_positive_clusters": (
            len(stage8_positive) >= int(gate["minimum_stage8_positive_clusters"])
        ),
        "minimum_stage8_negative_clusters": (
            len(stage8_negative) >= int(gate["minimum_stage8_negative_clusters"])
        ),
        "maximum_stage8_unjudgeable_applicable_cluster_rate": _rate_within(
            len(stage8_unjudgeable),
            len(stage8_applicable),
            float(
                gate[
                    "maximum_stage8_unavailable_or_unjudgeable_applicable_cluster_rate"
                ]
            ),
        ),
    }
    passed = all(checks.values())
    return {
        "row_count": len(rows),
        "independent_cluster_count": len(all_clusters),
        "row_reference_counts": {
            "advancing_context": dict(sorted(row_context.items())),
            "stage8": dict(sorted(row_stage8.items())),
        },
        "advancing_context": {
            "definite_cluster_count": len(context_definite),
            "positive_cluster_count": len(context_positive),
            "negative_cluster_count": len(context_negative),
            "temporal_polarity_overlap_cluster_count": len(
                context_positive & context_negative
            ),
            "unjudgeable_cluster_count": len(context_unjudgeable),
            "unjudgeable_cluster_rate_fraction": (
                f"{len(context_unjudgeable)}/{len(all_clusters)}"
            ),
        },
        "stage8": {
            "applicable_cluster_count": len(stage8_applicable),
            "definite_applicable_cluster_count": len(stage8_definite),
            "positive_cluster_count": len(stage8_positive),
            "negative_cluster_count": len(stage8_negative),
            "temporal_polarity_overlap_cluster_count": len(
                stage8_positive & stage8_negative
            ),
            "unjudgeable_applicable_cluster_count": len(stage8_unjudgeable),
            "unjudgeable_applicable_cluster_rate_fraction": (
                f"{len(stage8_unjudgeable)}/{len(stage8_applicable)}"
            ),
        },
        "per_cell": dict(sorted(per_cell.items())),
        "gate_checks": checks,
        "passed": passed,
        "disposition": (
            gate["pass_disposition"] if passed else gate["fail_disposition"]
        ),
        "candidate_scoring_authorized": False,
        "p026_d_preregistration_authorized": passed,
        "resource_reopen_authorized": False,
        "stage9_authorized": False,
    }

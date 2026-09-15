"""Pure P020-C blind-reference row resolution and cluster-aware sufficiency logic."""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any


class DownsideLifecycleReferenceSealError(ValueError):
    """Raised when P020-C sealed-reference inputs violate the frozen contract."""


def resolve_row(short: Mapping[str, Any], fund: Mapping[str, Any]) -> tuple[str, str]:
    left12 = str(short["downside_context"])
    right12 = str(fund["downside_context"])
    if left12 == right12 == "TRUE":
        stage12 = "TRUE"
    elif left12 == right12 == "FALSE":
        stage12 = "FALSE"
    else:
        stage12 = "AMBIGUOUS"

    if stage12 == "FALSE":
        return stage12, "NOT_APPLICABLE"
    if stage12 == "AMBIGUOUS":
        return stage12, "AMBIGUOUS"

    left1 = str(short["deceleration_stabilization"])
    right1 = str(fund["deceleration_stabilization"])
    if left1 == right1 == "TRUE":
        stage1 = "TRUE"
    elif left1 == right1 == "FALSE":
        stage1 = "FALSE"
    else:
        stage1 = "AMBIGUOUS"
    return stage12, stage1


def resolve_rows(
    short_opinions: Sequence[Mapping[str, Any]],
    fund_opinions: Sequence[Mapping[str, Any]],
    case_map_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if not (len(short_opinions) == len(fund_opinions) == len(case_map_rows) == 187):
        raise DownsideLifecycleReferenceSealError("row_count")
    map_by_case = {str(row["case_id"]): row for row in case_map_rows}
    if len(map_by_case) != 187:
        raise DownsideLifecycleReferenceSealError("case_map_unique")
    output: list[dict[str, Any]] = []
    for short, fund in zip(short_opinions, fund_opinions, strict=True):
        case_id = str(short["case_id"])
        if case_id != str(fund["case_id"]) or case_id not in map_by_case:
            raise DownsideLifecycleReferenceSealError("case_alignment")
        mapping = map_by_case[case_id]
        stage12, stage1 = resolve_row(short, fund)
        output.append(
            {
                "case_id": case_id,
                "cluster_id": str(mapping["cluster_id"]),
                "cell": str(mapping["cell"]),
                "window_start": str(mapping["window_start"]),
                "window_end": str(mapping["window_end"]),
                "short_term": {
                    "downside_context": str(short["downside_context"]),
                    "deceleration_stabilization": str(
                        short["deceleration_stabilization"]
                    ),
                    "reason": str(short["reason"]),
                },
                "fund": {
                    "downside_context": str(fund["downside_context"]),
                    "deceleration_stabilization": str(
                        fund["deceleration_stabilization"]
                    ),
                    "reason": str(fund["reason"]),
                },
                "resolved": {
                    "downside_context": stage12,
                    "deceleration_stabilization": stage1,
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
    if len(all_clusters) != int(gate["independent_cluster_count"]):
        raise DownsideLifecycleReferenceSealError("cluster_denominator")

    stage12_positive: set[str] = set()
    stage12_negative: set[str] = set()
    stage1_applicable: set[str] = set()
    stage1_positive: set[str] = set()
    stage1_negative: set[str] = set()
    cell_stage12_definite = Counter()
    row_stage12 = Counter()
    row_stage1 = Counter()
    per_cell = defaultdict(
        lambda: {
            "rows": 0,
            "stage12_definite_rows": 0,
            "stage12_true_rows": 0,
            "stage12_false_rows": 0,
        }
    )

    for row in rows:
        cluster = str(row["cluster_id"])
        cell = str(row["cell"])
        resolved = row["resolved"]
        stage12 = str(resolved["downside_context"])
        stage1 = str(resolved["deceleration_stabilization"])
        row_stage12[stage12] += 1
        row_stage1[stage1] += 1
        per_cell[cell]["rows"] += 1
        if stage12 == "TRUE":
            stage12_positive.add(cluster)
            stage1_applicable.add(cluster)
            cell_stage12_definite[cell] += 1
            per_cell[cell]["stage12_definite_rows"] += 1
            per_cell[cell]["stage12_true_rows"] += 1
            if stage1 == "TRUE":
                stage1_positive.add(cluster)
            elif stage1 == "FALSE":
                stage1_negative.add(cluster)
        elif stage12 == "FALSE":
            stage12_negative.add(cluster)
            cell_stage12_definite[cell] += 1
            per_cell[cell]["stage12_definite_rows"] += 1
            per_cell[cell]["stage12_false_rows"] += 1

    stage12_definite = stage12_positive | stage12_negative
    stage12_unjudgeable = all_clusters - stage12_definite
    stage1_definite = stage1_positive | stage1_negative
    stage1_unjudgeable_applicable = stage1_applicable - stage1_definite
    cell_names = {str(row["cell"]) for row in case_map_rows}

    checks = {
        "minimum_stage12_definite_clusters": (
            len(stage12_definite) >= int(gate["minimum_stage12_definite_clusters"])
        ),
        "minimum_stage12_positive_clusters": (
            len(stage12_positive) >= int(gate["minimum_stage12_positive_clusters"])
        ),
        "minimum_stage12_negative_clusters": (
            len(stage12_negative) >= int(gate["minimum_stage12_negative_clusters"])
        ),
        "maximum_stage12_unjudgeable_cluster_rate": _rate_within(
            len(stage12_unjudgeable),
            len(all_clusters),
            float(gate["maximum_stage12_unjudgeable_cluster_rate"]),
        ),
        "stage12_definite_row_in_all_eight_cells": (
            len(cell_names) == 8
            and all(cell_stage12_definite[cell] > 0 for cell in cell_names)
        ),
        "minimum_stage1_definite_applicable_clusters": (
            len(stage1_definite)
            >= int(gate["minimum_stage1_definite_applicable_clusters"])
        ),
        "minimum_stage1_positive_clusters": (
            len(stage1_positive) >= int(gate["minimum_stage1_positive_clusters"])
        ),
        "minimum_stage1_negative_clusters": (
            len(stage1_negative) >= int(gate["minimum_stage1_negative_clusters"])
        ),
        "maximum_stage1_unjudgeable_applicable_cluster_rate": _rate_within(
            len(stage1_unjudgeable_applicable),
            len(stage1_applicable),
            float(gate["maximum_stage1_unjudgeable_applicable_cluster_rate"]),
        ),
    }
    passed = all(checks.values())
    return {
        "row_count": len(rows),
        "independent_cluster_count": len(all_clusters),
        "row_reference_counts": {
            "stage12": dict(sorted(row_stage12.items())),
            "stage1": dict(sorted(row_stage1.items())),
        },
        "stage12": {
            "definite_cluster_count": len(stage12_definite),
            "positive_cluster_count": len(stage12_positive),
            "negative_cluster_count": len(stage12_negative),
            "temporal_polarity_overlap_cluster_count": len(
                stage12_positive & stage12_negative
            ),
            "unjudgeable_cluster_count": len(stage12_unjudgeable),
            "unjudgeable_cluster_rate_fraction": (
                f"{len(stage12_unjudgeable)}/{len(all_clusters)}"
            ),
        },
        "stage1": {
            "applicable_cluster_count": len(stage1_applicable),
            "definite_applicable_cluster_count": len(stage1_definite),
            "positive_cluster_count": len(stage1_positive),
            "negative_cluster_count": len(stage1_negative),
            "temporal_polarity_overlap_cluster_count": len(
                stage1_positive & stage1_negative
            ),
            "unjudgeable_applicable_cluster_count": len(
                stage1_unjudgeable_applicable
            ),
            "unjudgeable_applicable_cluster_rate_fraction": (
                f"{len(stage1_unjudgeable_applicable)}/{len(stage1_applicable)}"
            ),
        },
        "per_cell": dict(sorted(per_cell.items())),
        "gate_checks": checks,
        "passed": passed,
        "disposition": (
            gate["pass_disposition"] if passed else gate["fail_disposition"]
        ),
        "candidate_scoring_authorized": False,
        "p020_d_preregistration_authorized": passed,
        "resource_reopen_authorized": False,
    }

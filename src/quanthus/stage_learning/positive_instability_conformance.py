"""P3-STAGE-009-C diagnostic-only conformance over consumed evaluation evidence.

This module performs descriptive comparisons on already-consumed P3-STAGE-008 evidence. It does
not select thresholds, score a new candidate, read market data, submit Jobs, or make qualification
claims.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.stage_learning.directional_boundary_development import case_features
from quanthus.stage_learning.positive_instability import CONTRACT_DIGEST, load_contract
from quanthus.structure_v2_inputs import digest

CONFIG_PATH = "config/stage-learning/positive-instability-conformance.v1.json"
CONFIG_DIGEST = "sha256:a979781a61ddb4807aff01033f55b9bdc6513b8303e8e7d8680d0966d33e3ce7"


class PositiveInstabilityConformanceError(ValueError):
    """The frozen consumed-evidence diagnostic contract differs."""


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def load_config(root: Path | None = None) -> dict[str, Any]:
    root = root or find_project_root()
    try:
        value = json.loads((root / CONFIG_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PositiveInstabilityConformanceError("conformance_config_unreadable") from exc
    if digest(value) != CONFIG_DIGEST:
        raise PositiveInstabilityConformanceError("conformance_config_drift")
    contract = value["bindings"]["positive_instability_contract"]
    if contract["digest"] != CONTRACT_DIGEST:
        raise PositiveInstabilityConformanceError("positive_instability_contract_binding")
    load_contract(root)
    for key in ("evaluation_review", "root_cause_diagnostic"):
        binding = value["bindings"][key]
        path = root / binding["path"]
        if _sha(path) != binding["sha256"]:
            raise PositiveInstabilityConformanceError(f"{key}_file_binding")
    diagnostic = json.loads(
        (root / value["bindings"]["root_cause_diagnostic"]["path"]).read_text(encoding="utf-8")
    )
    expected_semantic = value["bindings"]["root_cause_diagnostic"]["semantic_digest"]
    if digest(diagnostic) != expected_semantic:
        raise PositiveInstabilityConformanceError("root_cause_semantic_binding")
    groups = value["fixed_groups"]
    observed = diagnostic.get("observed")
    if not isinstance(observed, Mapping):
        raise PositiveInstabilityConformanceError("root_cause_observed_binding")
    if groups["center_shift_only_true_stage0_indices"] != observed[
        "definite_stage_0_center_shift_only_indices"
    ]:
        raise PositiveInstabilityConformanceError("true_stage0_group_binding")
    if groups["center_shift_only_false_positive_indices"] != observed[
        "center_shift_only_false_positive_indices"
    ]:
        raise PositiveInstabilityConformanceError("false_positive_group_binding")
    return value


def _median(values: Sequence[Decimal]) -> Decimal:
    if not values:
        raise PositiveInstabilityConformanceError("median_requires_values")
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal(2)


def _features(case: Mapping[str, Any]) -> dict[str, Decimal]:
    value = case_features(case)
    counter = value["counter_direction_excursion_share20"]
    if counter is None:
        raise PositiveInstabilityConformanceError("fixed_group_counter_excursion_unavailable")
    return {
        "range_width20": value["range_width20"],
        "efficiency20_abs": value["efficiency20_abs"],
        "range_traversal20": value["range_traversal20"],
        "counter_direction_excursion_share20": counter,
        "width_change20": value["width_change20"],
        "center_shift20_abs": value["center_shift20_abs"],
    }


def _group_summary(cases: Sequence[Mapping[str, Any]], indices: Sequence[int]) -> dict[str, Any]:
    rows = [_features(cases[index]) for index in indices]
    dimensions = tuple(rows[0])
    medians = {key: _median([row[key] for row in rows]) for key in dimensions}
    return {
        "count": len(rows),
        "indices": list(indices),
        "medians": {key: str(value) for key, value in medians.items()},
        "rows": [
            {"index": index, **{key: str(value) for key, value in row.items()}}
            for index, row in zip(indices, rows, strict=True)
        ],
    }


def analyze_consumed_evidence(
    cases: Sequence[Mapping[str, Any]], references: Mapping[str, str]
) -> dict[str, Any]:
    config = load_config()
    d3 = config["bindings"]["d3_prepare"]
    if len(cases) != d3["case_count"]:
        raise PositiveInstabilityConformanceError("consumed_case_count")
    if len({str(case.get("case_id")) for case in cases}) != len(cases):
        raise PositiveInstabilityConformanceError("consumed_case_identity")
    if set(references) != {str(case["case_id"]) for case in cases}:
        raise PositiveInstabilityConformanceError("consumed_reference_binding")

    groups = config["fixed_groups"]
    true_indices = groups["center_shift_only_true_stage0_indices"]
    false_indices = groups["center_shift_only_false_positive_indices"]
    if any(references[str(cases[index]["case_id"])] != "STAGE_0" for index in true_indices):
        raise PositiveInstabilityConformanceError("true_stage0_reference_drift")
    if any(
        references[str(cases[index]["case_id"])] not in {"NEITHER", "STAGE_2"}
        for index in false_indices
    ):
        raise PositiveInstabilityConformanceError("false_positive_reference_drift")

    true_group = _group_summary(cases, true_indices)
    false_group = _group_summary(cases, false_indices)
    tm = {key: Decimal(value) for key, value in true_group["medians"].items()}
    fm = {key: Decimal(value) for key, value in false_group["medians"].items()}
    comparisons = {
        "true_stage0_group_has_broader_range_than_false_positive_group_median": (
            tm["range_width20"] > fm["range_width20"]
        ),
        "true_stage0_group_has_lower_directional_efficiency_than_false_positive_group_median": (
            tm["efficiency20_abs"] < fm["efficiency20_abs"]
        ),
        "true_stage0_group_has_lower_range_traversal_than_false_positive_group_median": (
            tm["range_traversal20"] < fm["range_traversal20"]
        ),
        (
            "true_stage0_group_has_higher_counter_direction_excursion_than_"
            "false_positive_group_median"
        ): (
            tm["counter_direction_excursion_share20"]
            > fm["counter_direction_excursion_share20"]
        ),
    }
    fp_medians = fm
    per_true_case_dominance: dict[str, bool] = {}
    for item in true_group["rows"]:
        row = {key: Decimal(value) for key, value in item.items() if key != "index"}
        per_true_case_dominance[str(item["index"])] = (
            row["range_width20"] > fp_medians["range_width20"]
            and row["efficiency20_abs"] < fp_medians["efficiency20_abs"]
            and row["range_traversal20"] < fp_medians["range_traversal20"]
            and row["counter_direction_excursion_share20"]
            > fp_medians["counter_direction_excursion_share20"]
        )
    comparisons[
        "both_true_stage0_cases_are_more_disorder_like_than_false_positive_group_median_on_all_four_dimensions"
    ] = all(per_true_case_dominance.values())
    comparisons["center_shift_itself_is_excluded_from_the_b_truth_function"] = (
        load_contract()["modifiers"]["range_center_shift_20_vs_prior20"]["truth_function_role"]
        == "none"
    )
    expected = config["diagnostic_questions"]
    failed = [
        key
        for key, expected_value in expected.items()
        if comparisons.get(key) != expected_value
    ]

    definite_stage0_indices = [
        index
        for index, case in enumerate(cases)
        if references[str(case["case_id"])] == "STAGE_0"
    ]
    stage0_width_change_signs = {
        "positive": 0,
        "zero": 0,
        "negative": 0,
    }
    negative_width_change_indices: list[int] = []
    for index in definite_stage0_indices:
        width_change = _features(cases[index])["width_change20"]
        if width_change > 0:
            stage0_width_change_signs["positive"] += 1
        elif width_change < 0:
            stage0_width_change_signs["negative"] += 1
            negative_width_change_indices.append(index)
        else:
            stage0_width_change_signs["zero"] += 1

    return {
        "schema_version": "quanthus.stage-positive-instability-conformance-result.v1",
        "status": (
            "PASS_DIAGNOSTIC_CONFORMANCE_ONLY"
            if not failed
            else "FAIL_DIAGNOSTIC_CONFORMANCE"
        ),
        "case_count": len(cases),
        "fixed_groups": {
            "center_shift_only_true_stage0": true_group,
            "center_shift_only_false_positive": false_group,
        },
        "comparisons": comparisons,
        "failed_comparisons": failed,
        "per_true_case_four_dimension_dominance": per_true_case_dominance,
        "definite_stage0_count": len(definite_stage0_indices),
        "definite_stage0_width_change_signs": stage0_width_change_signs,
        "definite_stage0_negative_width_change_indices": negative_width_change_indices,
        "negative_width_change_cases_are_center_shift_only_true_stage0_group": (
            negative_width_change_indices == list(true_indices)
        ),
        "numeric_thresholds_selected": False,
        "parameter_trials": 0,
        "new_market_data_read": False,
        "runtime_jobs": 0,
        "qualification_claim": False,
        "stage_2_changed": False,
        "relocation_guard_retuned": False,
    }

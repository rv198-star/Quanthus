"""Read-only P3-STAGE-008-C conformance over synthetic contracts and exposed Stage-007 evidence.

This module does not select thresholds, acquire market data, submit Jobs or qualify Stage 0. It only
reproduces the diagnostic values already frozen by P3-STAGE-008-A from the immutable Stage-007
review transport and frozen review ledger.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from pathlib import Path
from statistics import median
from typing import Any

from quanthus.policy import find_project_root
from quanthus.structure_directional_path_geometry import (
    counter_direction_excursion_share,
    range_traversal,
)
from quanthus.structure_v2_inputs import digest

CONFIG_PATH = "config/stage-learning/directional-boundary-conformance.v1.json"


class DirectionalBoundaryConformanceError(ValueError):
    """The frozen 008-C conformance contract or exposed evidence is inconsistent."""


@dataclass(frozen=True)
class _CloseOnly:
    close: Decimal


def _sha256(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def load_config(root: Path | None = None) -> dict[str, Any]:
    root = root or find_project_root()
    try:
        value = json.loads((root / CONFIG_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DirectionalBoundaryConformanceError("conformance_config_unreadable") from exc
    if value.get("schema_version") != "quanthus.stage-directional-boundary-conformance.v1":
        raise DirectionalBoundaryConformanceError("conformance_config_schema")
    return value


def load_frozen_ledger(root: Path | None = None) -> dict[str, tuple[str, str]]:
    root = root or find_project_root()
    config = load_config(root)
    binding = config["bindings"]["stage_007_review_ledger"]
    path = root / binding["path"]
    raw = path.read_bytes()
    if _sha256(raw) != binding["sha256"]:
        raise DirectionalBoundaryConformanceError("stage007_review_ledger_drift")
    lines = [line for line in raw.decode("utf-8").splitlines() if not line.startswith("#")]
    reader = csv.DictReader(io.StringIO("\n".join(lines)), delimiter="|")
    result: dict[str, tuple[str, str]] = {}
    for row in reader:
        case_id = row["case_id"]
        if case_id in result:
            raise DirectionalBoundaryConformanceError("duplicate_stage007_case_in_ledger")
        result[case_id] = row["short_primary"], row["fund_primary"]
    if len(result) != 160:
        raise DirectionalBoundaryConformanceError("stage007_ledger_case_count")
    return result


def _definite_reference(pair: tuple[str, str]) -> str:
    if pair == ("S0", "S0"):
        return "STAGE_0"
    if pair == ("N", "N"):
        return "NEITHER"
    if pair == ("S2", "S2"):
        return "STAGE_2"
    return "AMBIGUOUS"


def _measurement(primary: Mapping[str, Any], field: str) -> Decimal:
    item = primary.get(field)
    if not isinstance(item, Mapping) or item.get("reason") != "available":
        raise DirectionalBoundaryConformanceError(f"primary_measurement_unavailable:{field}")
    try:
        value = Decimal(str(item["value"]))
    except Exception as exc:
        raise DirectionalBoundaryConformanceError(f"primary_measurement_invalid:{field}") from exc
    if not value.is_finite():
        raise DirectionalBoundaryConformanceError(f"primary_measurement_nonfinite:{field}")
    return value


def _old_stage0_prediction(case: Mapping[str, Any]) -> bool:
    primary = case.get("primary")
    if not isinstance(primary, Mapping) or not case.get("primary_available"):
        return False
    efficiency = abs(_measurement(primary, "directional_efficiency"))
    width_change = _measurement(primary, "width_change")
    center_shift = abs(_measurement(primary, "center_shift"))
    return efficiency <= Decimal("0.40") and (
        width_change >= Decimal("0.20") or center_shift >= Decimal("0.07")
    )


def _history_closes(case: Mapping[str, Any]) -> list[Decimal]:
    history = case.get("history")
    if not isinstance(history, list) or len(history) < 21:
        raise DirectionalBoundaryConformanceError("stage007_history_too_short")
    closes: list[Decimal] = []
    for row in history[-21:]:
        if not isinstance(row, list) or len(row) < 5:
            raise DirectionalBoundaryConformanceError("stage007_history_row_shape")
        try:
            value = Decimal(str(row[4]))
        except Exception as exc:
            raise DirectionalBoundaryConformanceError("stage007_history_close_invalid") from exc
        if not value.is_finite() or value <= 0:
            raise DirectionalBoundaryConformanceError("stage007_history_close_invalid")
        closes.append(value)
    return closes


def _absolute_window_return(closes: Sequence[Decimal]) -> Decimal:
    with localcontext(Context(prec=28)):
        return abs(closes[-1] / closes[0] - Decimal(1))


def _round4(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN), "f")


def _group_metrics(cases: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    returns: list[Decimal] = []
    traversal: list[Decimal] = []
    counter: list[Decimal] = []
    for case in cases:
        closes = _history_closes(case)
        rows = tuple(_CloseOnly(value) for value in closes)
        returns.append(_absolute_window_return(closes))
        traversal.append(range_traversal(rows))  # type: ignore[arg-type]
        value = counter_direction_excursion_share(rows)  # type: ignore[arg-type]
        if value is None:
            raise DirectionalBoundaryConformanceError("diagnostic_group_zero_net_direction")
        counter.append(value)
    return {
        "absolute_window_return_20_median": _round4(median(returns)),
        "range_traversal_20_median": _round4(median(traversal)),
        "counter_direction_excursion_share_20_median": _round4(median(counter)),
    }


def analyze_exposed_pages(
    pages: Sequence[Mapping[str, Any]],
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    """Reproduce frozen Stage-007 diagnostics without choosing any new threshold."""
    root = root or find_project_root()
    config = load_config(root)
    binding = config["bindings"]["stage_007_prepare"]
    ledger = load_frozen_ledger(root)
    if len(pages) != binding["page_count"]:
        raise DirectionalBoundaryConformanceError("stage007_page_count")
    cases: list[Mapping[str, Any]] = []
    seen_pages: set[int] = set()
    for page in pages:
        if (
            page.get("parent_job_id") != binding["job_id"]
            or page.get("manifest_sha256") != binding["manifest_sha256"]
            or page.get("evidence_file_sha256") != binding["evidence_sha256"]
            or page.get("candidate_output_included") is not False
            or page.get("future_rows_included") is not False
        ):
            raise DirectionalBoundaryConformanceError("stage007_transport_binding")
        page_number = page.get("page")
        if type(page_number) is not int or page_number in seen_pages:
            raise DirectionalBoundaryConformanceError("stage007_transport_page_identity")
        seen_pages.add(page_number)
        current = page.get("cases")
        if not isinstance(current, list):
            raise DirectionalBoundaryConformanceError("stage007_transport_cases")
        cases.extend(current)
    if seen_pages != set(range(binding["page_count"])) or len(cases) != binding["case_count"]:
        raise DirectionalBoundaryConformanceError("stage007_transport_denominator")
    case_ids = [case.get("case_id") for case in cases]
    if len(set(case_ids)) != len(case_ids) or set(case_ids) != set(ledger):
        raise DirectionalBoundaryConformanceError("stage007_case_identity_mismatch")

    groups = {
        "old_stage_0_true_positive": [],
        "old_stage_0_false_positive_neither": [],
    }
    for case in cases:
        case_id = str(case["case_id"])
        if not _old_stage0_prediction(case):
            continue
        reference = _definite_reference(ledger[case_id])
        if reference == "STAGE_0":
            groups["old_stage_0_true_positive"].append(case)
        elif reference == "NEITHER":
            groups["old_stage_0_false_positive_neither"].append(case)

    counts = {key: len(value) for key, value in groups.items()}
    if counts != config["frozen_group_definition"]["expected_counts"]:
        raise DirectionalBoundaryConformanceError("stage007_frozen_group_count_drift")
    observed = {key: _group_metrics(value) for key, value in groups.items()}
    frozen = config["frozen_design_regression"]
    expected = {
        group: {
            "absolute_window_return_20_median": frozen["absolute_window_return_20_median"][group],
            "range_traversal_20_median": frozen["range_traversal_20_median"][group],
            "counter_direction_excursion_share_20_median": (
                frozen["counter_direction_excursion_share_20_median"][group]
            ),
        }
        for group in groups
    }
    if observed != expected:
        raise DirectionalBoundaryConformanceError(
            "stage007_frozen_design_regression_differs:" + json.dumps(observed, sort_keys=True)
        )
    return {
        "schema_version": "quanthus.stage-directional-boundary-conformance-result.v1",
        "status": "PASS_DIAGNOSTIC_CONFORMANCE_ONLY",
        "case_count": len(cases),
        "group_counts": counts,
        "diagnostic_medians": observed,
        "threshold_search_count": 0,
        "parameter_trials": 0,
        "new_market_data_reads": 0,
        "runtime_jobs": 0,
        "stage_007_threshold_learning": False,
        "stage_007_qualification": False,
        "candidate_output_seen": False,
        "future_rows_seen": False,
        "config_digest": digest(config),
    }

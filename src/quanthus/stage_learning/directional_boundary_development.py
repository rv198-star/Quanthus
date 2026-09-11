"""Pure P3-STAGE-008-D candidate-grid and evaluation logic.

The module accepts already prepared, T-only case views plus sealed primary references. It does not
read market data, submit Jobs, generate opinions, open future partitions or mutate Stage-2 rules.
"""
from __future__ import annotations

import itertools
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Context, Decimal, localcontext
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.structure_v2_inputs import digest

CONFIG_PATH = "config/stage-learning/directional-boundary-development-screen.v1.json"
CONFIG_DIGEST = "sha256:fff38395c6466db0305287125d66bc5ba2f5a4ea18d6eb7ee97dd13f0047f963"
DEFINITE_REFERENCES = {"STAGE_0", "STAGE_2", "NEITHER"}


class DirectionalBoundaryDevelopmentError(ValueError):
    """The frozen fresh-development screen contract was violated."""


def load_config(root: Path | None = None) -> dict[str, Any]:
    root = root or find_project_root()
    try:
        value = json.loads((root / CONFIG_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DirectionalBoundaryDevelopmentError("development_screen_config_unreadable") from exc
    if digest(value) != CONFIG_DIGEST:
        raise DirectionalBoundaryDevelopmentError("development_screen_config_drift")
    return value


@dataclass(frozen=True)
class RelocationCandidate:
    absolute_window_return20_min: Decimal
    range_traversal20_min: Decimal
    counter_direction_excursion_share20_max: Decimal

    @property
    def candidate_id(self) -> str:
        return digest({
            "absolute_window_return20_min": self.absolute_window_return20_min,
            "range_traversal20_min": self.range_traversal20_min,
            "counter_direction_excursion_share20_max": (
                self.counter_direction_excursion_share20_max
            ),
        })

    def to_dict(self) -> dict[str, str]:
        return {
            "candidate_id": self.candidate_id,
            "absolute_window_return20_min": str(self.absolute_window_return20_min),
            "range_traversal20_min": str(self.range_traversal20_min),
            "counter_direction_excursion_share20_max": str(
                self.counter_direction_excursion_share20_max
            ),
        }


def candidate_grid() -> tuple[RelocationCandidate, ...]:
    config = load_config()["relocation_guard_grid"]
    output = tuple(
        RelocationCandidate(Decimal(r), Decimal(t), Decimal(c))
        for r, t, c in itertools.product(
            config["absolute_window_return20_min"],
            config["range_traversal20_min"],
            config["counter_direction_excursion_share20_max"],
        )
    )
    ids = {item.candidate_id for item in output}
    if len(output) != config["candidate_count"] or len(ids) != len(output):
        raise DirectionalBoundaryDevelopmentError("candidate_grid_identity")
    return output


def _number(primary: Mapping[str, Any], field: str) -> Decimal:
    item = primary.get(field)
    if not isinstance(item, Mapping) or item.get("reason") != "available":
        raise DirectionalBoundaryDevelopmentError(f"primary_measurement_unavailable:{field}")
    try:
        value = Decimal(str(item["value"]))
    except Exception as exc:
        raise DirectionalBoundaryDevelopmentError(f"primary_measurement_invalid:{field}") from exc
    if not value.is_finite():
        raise DirectionalBoundaryDevelopmentError(f"primary_measurement_nonfinite:{field}")
    return value


def _closes(case: Mapping[str, Any]) -> list[Decimal]:
    history = case.get("history")
    if not isinstance(history, list) or len(history) < 21:
        raise DirectionalBoundaryDevelopmentError("case_history_requires_21_rows")
    values = []
    for row in history[-21:]:
        if not isinstance(row, list) or len(row) < 5:
            raise DirectionalBoundaryDevelopmentError("case_history_row_shape")
        value = Decimal(str(row[4]))
        if not value.is_finite() or value <= 0:
            raise DirectionalBoundaryDevelopmentError("case_history_close_invalid")
        values.append(value)
    return values


def case_features(case: Mapping[str, Any]) -> dict[str, Decimal | None]:
    primary = case.get("primary")
    if not isinstance(primary, Mapping) or case.get("primary_available") is not True:
        raise DirectionalBoundaryDevelopmentError("case_primary_unavailable")
    closes = _closes(case)
    observed_range = max(closes) - min(closes)
    net = closes[-1] - closes[0]
    with localcontext(Context(prec=28)):
        absolute_return = abs(closes[-1] / closes[0] - Decimal(1))
        traversal = Decimal(0) if observed_range == 0 else abs(net) / observed_range
    counter: Decimal | None = None
    if net != 0:
        adverse = Decimal(0)
        if net > 0:
            running = closes[0]
            for value in closes[1:]:
                running = max(running, value)
                adverse = max(adverse, running - value)
        else:
            running = closes[0]
            for value in closes[1:]:
                running = min(running, value)
                adverse = max(adverse, value - running)
        with localcontext(Context(prec=28)):
            counter = adverse / observed_range
    return {
        "efficiency20_abs": abs(_number(primary, "directional_efficiency")),
        "range_width20": _number(primary, "range_width"),
        "width_change20": _number(primary, "width_change"),
        "center_shift20_abs": abs(_number(primary, "center_shift")),
        "absolute_window_return20": absolute_return,
        "range_traversal20": traversal,
        "counter_direction_excursion_share20": counter,
    }


def old_stage0(case: Mapping[str, Any]) -> bool:
    value = case_features(case)
    return value["efficiency20_abs"] <= Decimal("0.40") and (
        value["width_change20"] >= Decimal("0.20")
        or value["center_shift20_abs"] >= Decimal("0.07")
    )


def unchanged_stage2(case: Mapping[str, Any]) -> bool:
    value = case_features(case)
    return (
        value["efficiency20_abs"] <= Decimal("0.33")
        and value["range_width20"] <= Decimal("0.18")
        and value["width_change20"] <= Decimal("-0.20")
        and value["center_shift20_abs"] <= Decimal("0.04")
    )


def relocation_guard(case: Mapping[str, Any], candidate: RelocationCandidate) -> bool:
    value = case_features(case)
    counter = value["counter_direction_excursion_share20"]
    if counter is None:
        return False
    return (
        value["absolute_window_return20"] >= candidate.absolute_window_return20_min
        and value["range_traversal20"] >= candidate.range_traversal20_min
        and counter <= candidate.counter_direction_excursion_share20_max
    )


def candidate_stage0(case: Mapping[str, Any], candidate: RelocationCandidate) -> bool:
    return old_stage0(case) and not relocation_guard(case, candidate)


def _ratio(numerator: int, denominator: int) -> Decimal | None:
    with localcontext(Context(prec=28)):
        return None if denominator == 0 else Decimal(numerator) / Decimal(denominator)


def stage0_metrics(
    cases: Sequence[Mapping[str, Any]],
    references: Mapping[str, str],
    *,
    candidate: RelocationCandidate | None,
) -> dict[str, Any]:
    tp = fp = fn = definite_positives = definite_negatives = predictions = ambiguous = 0
    for case in cases:
        case_id = str(case.get("case_id"))
        if case_id not in references:
            raise DirectionalBoundaryDevelopmentError("reference_missing_for_case")
        reference = references[case_id]
        prediction = old_stage0(case) if candidate is None else candidate_stage0(case, candidate)
        if reference == "AMBIGUOUS":
            ambiguous += 1
            continue
        if reference == "INPUT_UNAVAILABLE":
            continue
        if reference not in DEFINITE_REFERENCES:
            raise DirectionalBoundaryDevelopmentError("unsupported_primary_reference")
        if reference == "STAGE_0":
            definite_positives += 1
            if prediction:
                tp += 1
            else:
                fn += 1
        else:
            definite_negatives += 1
            if prediction:
                fp += 1
        predictions += int(prediction)
    return {
        "case_count": len(cases),
        "definite_positive_references": definite_positives,
        "definite_negative_references": definite_negatives,
        "ambiguous_references": ambiguous,
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "stage_0_predictions": predictions,
        "precision": _ratio(tp, tp + fp),
        "positive_reference_coverage": _ratio(tp, definite_positives),
        "definite_negative_false_positive_rate": _ratio(fp, definite_negatives),
    }


def _meets(metrics: Mapping[str, Any], gates: Mapping[str, Any]) -> bool:
    if (
        metrics["case_count"] < gates["minimum_complete_cases"]
        or metrics["definite_positive_references"]
        < gates["minimum_definite_stage_0_positive_references"]
        or metrics["definite_negative_references"]
        < gates["minimum_definite_stage_0_negative_references"]
        or metrics["stage_0_predictions"] < gates["minimum_stage_0_predictions"]
    ):
        return False
    values = (
        metrics["precision"],
        metrics["positive_reference_coverage"],
        metrics["definite_negative_false_positive_rate"],
    )
    if any(value is None for value in values):
        return False
    return (
        metrics["precision"] >= Decimal(gates["minimum_stage_0_precision"])
        and metrics["positive_reference_coverage"]
        >= Decimal(gates["minimum_stage_0_positive_reference_coverage"])
        and metrics["definite_negative_false_positive_rate"]
        <= Decimal(gates["maximum_stage_0_definite_negative_false_positive_rate"])
    )


def select_calibration_candidate(
    cases: Sequence[Mapping[str, Any]], references: Mapping[str, str]
) -> dict[str, Any]:
    config = load_config()
    gates = config["calibration_gates"]
    trials = []
    for candidate in candidate_grid():
        metrics = stage0_metrics(cases, references, candidate=candidate)
        trials.append({
            "candidate": candidate,
            "metrics": metrics,
            "eligible": _meets(metrics, gates),
        })
    eligible = [item for item in trials if item["eligible"]]
    if not eligible:
        return {
            "disposition": gates["no_eligible_candidate_disposition"],
            "candidate": None,
            "trial_count": len(trials),
            "eligible_count": 0,
            "trials": [
                {"candidate": item["candidate"].to_dict(), "metrics": item["metrics"],
                 "eligible": item["eligible"]}
                for item in trials
            ],
        }

    def order(item: Mapping[str, Any]) -> tuple[Any, ...]:
        metrics = item["metrics"]
        return (
            -metrics["precision"],
            metrics["definite_negative_false_positive_rate"],
            -metrics["positive_reference_coverage"],
            item["candidate"].candidate_id,
        )

    selected = min(eligible, key=order)
    return {
        "disposition": "CANDIDATE_FROZEN_FOR_EVALUATION",
        "candidate": selected["candidate"].to_dict(),
        "metrics": selected["metrics"],
        "trial_count": len(trials),
        "eligible_count": len(eligible),
        "trials": [
            {"candidate": item["candidate"].to_dict(), "metrics": item["metrics"],
             "eligible": item["eligible"]}
            for item in trials
        ],
    }


def evaluate_frozen_candidate(
    cases: Sequence[Mapping[str, Any]],
    references: Mapping[str, str],
    candidate: RelocationCandidate,
) -> dict[str, Any]:
    config = load_config()
    gates = config["evaluation_gates"]
    baseline = stage0_metrics(cases, references, candidate=None)
    observed = stage0_metrics(cases, references, candidate=candidate)
    reasons = []
    if not _meets(observed, gates):
        reasons.append("absolute_stage0_gate_fails")
    if (
        observed["precision"] is None
        or baseline["precision"] is None
        or observed["precision"] < baseline["precision"]
    ):
        reasons.append("precision_does_not_match_or_improve_old_r0")
    if (
        observed["definite_negative_false_positive_rate"] is None
        or baseline["definite_negative_false_positive_rate"] is None
        or observed["definite_negative_false_positive_rate"]
        > baseline["definite_negative_false_positive_rate"]
    ):
        reasons.append("fpr_worse_than_old_r0")
    if observed["positive_reference_coverage"] is None or baseline[
        "positive_reference_coverage"
    ] is None:
        reasons.append("coverage_unavailable")
    elif (
        baseline["positive_reference_coverage"] - observed["positive_reference_coverage"]
        > Decimal(gates["maximum_absolute_coverage_loss_vs_old_r0"])
    ):
        reasons.append("coverage_loss_vs_old_r0_too_large")
    if baseline["false_positive"] > 0 and (
        baseline["false_positive"] - observed["false_positive"]
        < gates["if_old_r0_has_false_positive_candidate_must_remove_at_least"]
    ):
        reasons.append("no_false_positive_removed_vs_old_r0")
    stage2_baseline = {str(case["case_id"]): unchanged_stage2(case) for case in cases}
    stage2_candidate = {str(case["case_id"]): unchanged_stage2(case) for case in cases}
    if stage2_candidate != stage2_baseline:
        reasons.append("stage2_prediction_changed")
    return {
        "disposition": (
            "PROMISING_EXPLORATORY_REPAIR" if not reasons else "REVISE_OR_PARK_EVALUATION_FAIL"
        ),
        "reasons": reasons,
        "candidate": candidate.to_dict(),
        "baseline_stage0": baseline,
        "candidate_stage0": observed,
        "stage2_predictions_unchanged": stage2_candidate == stage2_baseline,
        "independent_validation": False,
        "official_label_authorized": False,
        "strategy_or_trading_authorized": False,
    }

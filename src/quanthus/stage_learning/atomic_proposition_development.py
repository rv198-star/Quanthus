"""Pure P3-STAGE-012 atomic-first development scoring and freeze logic.

This module owns no market-data reader and no reviewer. It scores only a sealed atomic reference
ledger against the preregistered finite instability/order surfaces plus the frozen P008 relocation
guard. Instability and order are selected independently before the unchanged Stage0/Stage2 resolver
is tested; there is no joint pair search.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from quanthus.policy import find_project_root
from quanthus.stage_learning.directional_boundary_development import case_features, relocation_guard
from quanthus.stage_learning.order_reference_protocol_v2 import project_atomic_primary
from quanthus.stage_learning.orderly_consolidation_joint_development import (
    OrderlyConsolidationCandidate,
    stage2_candidate_grid,
)
from quanthus.stage_learning.orderly_consolidation_joint_development import (
    threshold_basis as joint_threshold_basis,
)
from quanthus.stage_learning.orderly_consolidation_projection import contraction_magnitude20
from quanthus.stage_learning.positive_instability_quantitative_development import (
    FROZEN_008_RELOCATION,
    QuantitativeInstabilityCandidate,
    candidate_instability,
)
from quanthus.stage_learning.positive_instability_quantitative_development import (
    candidate_grid as instability_candidate_grid,
)
from quanthus.structure_v2_inputs import digest

PREREG_PATH = "config/stage-learning/stage-atomic-proposition-development-screen.v1.json"
PREREG_DIGEST = "sha256:24aacc70ef7ffa3bbfd0e28e8aa33fd91ba51bbd65da6c13640926215987c54d"
EXECUTION_PATH = "config/stage-learning/stage-atomic-proposition-development-execution.v1.json"
EXECUTION_DIGEST = "sha256:5203b5c4a325b775722c90c7404fc149567e448cf63d39e731e22fcd6cebe098"

Truth = Literal["TRUE", "FALSE", "UNKNOWN"]
AtomicReference = Literal["TRUE", "FALSE", "AMBIGUOUS", "INPUT_UNAVAILABLE"]
PROPOSITIONS = (
    "structural_instability_20",
    "coherent_directional_relocation_20",
    "orderly_consolidation_20",
)


class AtomicPropositionDevelopmentError(ValueError):
    """The frozen P3-STAGE-012 atomic-first development contract was violated."""


def _load(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AtomicPropositionDevelopmentError(f"{label}_unreadable") from exc
    if not isinstance(value, dict):
        raise AtomicPropositionDevelopmentError(f"{label}_must_be_object")
    return value


def load_preregistration(root: Path | None = None) -> dict[str, Any]:
    root = root or find_project_root()
    value = _load(root / PREREG_PATH, "p012_preregistration")
    if digest(value) != PREREG_DIGEST:
        raise AtomicPropositionDevelopmentError("p012_preregistration_drift")
    return value


def load_execution(root: Path | None = None) -> dict[str, Any]:
    root = root or find_project_root()
    value = _load(root / EXECUTION_PATH, "p012_execution")
    if digest(value) != EXECUTION_DIGEST:
        raise AtomicPropositionDevelopmentError("p012_execution_drift")
    if value["preregistration"]["digest"] != PREREG_DIGEST:
        raise AtomicPropositionDevelopmentError("execution_preregistration_binding")
    return value


def threshold_basis(feature_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Reuse the exact P010/P011 unlabeled quantile construction without joint scoring."""
    basis = joint_threshold_basis(feature_rows)
    return {
        **basis,
        "schema_version": "quanthus.stage-atomic-proposition-threshold-basis.v1",
        "labels_used": False,
        "reference_opinions_used": False,
        "joint_pair_search": False,
    }


def _truth_or(left: Truth, right: Truth) -> Truth:
    if "TRUE" in {left, right}:
        return "TRUE"
    if left == right == "FALSE":
        return "FALSE"
    return "UNKNOWN"


def envelope_failed_resolution_truth(measurement: Mapping[str, Any]) -> Truth:
    prereg = load_preregistration()
    rule = prereg["instability_candidate_surface"]["fixed_additive_channel"]
    if measurement.get("status") != rule["trace_status_required"]:
        return "UNKNOWN"
    descriptors = measurement.get("descriptors")
    if not isinstance(descriptors, Mapping):
        raise AtomicPropositionDevelopmentError("envelope_descriptors_missing")
    try:
        resolved = int(descriptors["resolved_escape_count"])
        alternations = int(descriptors["resolved_escape_side_alternation_count"])
        both_sides = descriptors["resolved_escape_both_sides"] is True
    except (KeyError, TypeError, ValueError) as exc:
        raise AtomicPropositionDevelopmentError("envelope_descriptor_shape") from exc
    return "TRUE" if (
        resolved >= int(rule["minimum_resolved_escape_count"])
        and both_sides
        and alternations >= int(rule["minimum_resolved_escape_side_alternation_count"])
    ) else "FALSE"


def instability_truth(
    case: Mapping[str, Any],
    candidate: QuantitativeInstabilityCandidate,
    envelope_measurement: Mapping[str, Any],
) -> dict[str, Truth]:
    base = candidate_instability(case, candidate)
    if base not in {"TRUE", "FALSE", "UNKNOWN"}:
        raise AtomicPropositionDevelopmentError("instability_truth_value")
    envelope = envelope_failed_resolution_truth(envelope_measurement)
    return {"base": base, "envelope": envelope, "enhanced": _truth_or(base, envelope)}


def relocation_truth(case: Mapping[str, Any]) -> Truth:
    return "TRUE" if relocation_guard(case, FROZEN_008_RELOCATION) else "FALSE"


def order_truth(case: Mapping[str, Any], candidate: OrderlyConsolidationCandidate) -> Truth:
    features = case_features(case)
    efficiency = features["efficiency20_abs"]
    width = features["range_width20"]
    contraction = contraction_magnitude20(features["width_change20"])
    if efficiency is None or width is None or contraction is None:
        return "UNKNOWN"
    thresholds = candidate.thresholds
    return "TRUE" if (
        efficiency <= thresholds.efficiency20_abs_max
        and width <= thresholds.range_width20_max
        and contraction >= thresholds.contraction_magnitude20_min
    ) else "FALSE"


def _ratio(numerator: int, denominator: int) -> Decimal | None:
    return None if denominator == 0 else Decimal(numerator) / Decimal(denominator)


def _atomic_metrics(
    case_ids: Sequence[str],
    references: Mapping[str, Mapping[str, Any]],
    predictions: Mapping[str, Truth],
    proposition: str,
) -> dict[str, Any]:
    tp = fp = fn = positives = negatives = predicted = ambiguous = unavailable = unknown_pred = 0
    for case_id in case_ids:
        case_ref = references.get(case_id)
        if not isinstance(case_ref, Mapping):
            raise AtomicPropositionDevelopmentError("atomic_reference_missing")
        reference = str(case_ref.get(proposition) or "")
        prediction = predictions[case_id]
        if reference == "AMBIGUOUS":
            ambiguous += 1
            continue
        if reference == "INPUT_UNAVAILABLE":
            unavailable += 1
            continue
        if reference not in {"TRUE", "FALSE"}:
            raise AtomicPropositionDevelopmentError("atomic_reference_value")
        if prediction == "UNKNOWN":
            unknown_pred += 1
        is_predicted = prediction == "TRUE"
        if reference == "TRUE":
            positives += 1
            tp += int(is_predicted)
            fn += int(not is_predicted)
        else:
            negatives += 1
            fp += int(is_predicted)
        predicted += int(is_predicted)
    return {
        "case_count": len(case_ids),
        "complete_reference_count": len(case_ids) - unavailable,
        "definite_positive_references": positives,
        "definite_negative_references": negatives,
        "ambiguous_references": ambiguous,
        "input_unavailable_references": unavailable,
        "unknown_predictions": unknown_pred,
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "predictions": predicted,
        "precision": _ratio(tp, tp + fp),
        "positive_reference_coverage": _ratio(tp, positives),
        "definite_negative_false_positive_rate": _ratio(fp, negatives),
    }


def _passes_atomic_gate(
    metrics: Mapping[str, Any],
    *,
    proposition: str,
    quality: Mapping[str, Any],
    sufficiency: Mapping[str, Any],
) -> bool:
    precision = metrics["precision"]
    coverage = metrics["positive_reference_coverage"]
    fpr = metrics["definite_negative_false_positive_rate"]
    prefix = {
        "structural_instability_20": "instability",
        "coherent_directional_relocation_20": "relocation",
        "orderly_consolidation_20": "order",
    }[proposition]
    return bool(
        metrics["complete_reference_count"] >= sufficiency["minimum_complete_cases"]
        and metrics["definite_positive_references"]
        >= sufficiency[f"minimum_definite_{prefix}_positive_references"]
        and metrics["definite_negative_references"]
        >= sufficiency[f"minimum_definite_{prefix}_negative_references"]
        and metrics["predictions"] >= quality["minimum_predictions"]
        and precision is not None
        and precision >= Decimal(str(quality["minimum_precision"]))
        and coverage is not None
        and coverage >= Decimal(str(quality["minimum_positive_reference_coverage"]))
        and fpr is not None
        and fpr <= Decimal(str(quality["maximum_definite_negative_false_positive_rate"]))
    )


def _atomic_reference_sufficient(
    references: Mapping[str, Mapping[str, Any]], case_ids: Sequence[str]
) -> bool:
    prereg = load_preregistration()
    sufficiency = prereg["development_sufficiency"]
    dummy = {case_id: "FALSE" for case_id in case_ids}
    for proposition in PROPOSITIONS:
        metrics = _atomic_metrics(case_ids, references, dummy, proposition)
        prefix = {
            "structural_instability_20": "instability",
            "coherent_directional_relocation_20": "relocation",
            "orderly_consolidation_20": "order",
        }[proposition]
        if (
            metrics["complete_reference_count"] < sufficiency["minimum_complete_cases"]
            or metrics["definite_positive_references"]
            < sufficiency[f"minimum_definite_{prefix}_positive_references"]
            or metrics["definite_negative_references"]
            < sufficiency[f"minimum_definite_{prefix}_negative_references"]
        ):
            return False
    return True


def score_and_freeze(
    cases: Sequence[Mapping[str, Any]],
    references: Mapping[str, Mapping[str, Any]],
    basis: Mapping[str, Any],
    envelope_measurements: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    prereg = load_preregistration()
    case_ids = [str(case.get("case_id") or "") for case in cases]
    if not all(case_ids) or len(case_ids) != len(set(case_ids)):
        raise AtomicPropositionDevelopmentError("case_identity")
    if set(case_ids) != set(references) or set(case_ids) != set(envelope_measurements):
        raise AtomicPropositionDevelopmentError("case_reference_envelope_identity")
    if not _atomic_reference_sufficient(references, case_ids):
        return {
            "disposition": prereg["no_pass_disposition"]["atomic_sufficiency_fail"],
            "exact_freeze": None,
            "preserved_2021_read_authorized": False,
        }

    instability_trials = []
    for candidate in instability_candidate_grid(basis):
        observed = {
            str(case["case_id"]): instability_truth(
                case, candidate, envelope_measurements[str(case["case_id"])]
            )
            for case in cases
        }
        base_predictions = {case_id: value["base"] for case_id, value in observed.items()}
        enhanced_predictions = {case_id: value["enhanced"] for case_id, value in observed.items()}
        base_metrics = _atomic_metrics(
            case_ids, references, base_predictions, "structural_instability_20"
        )
        metrics = _atomic_metrics(
            case_ids, references, enhanced_predictions, "structural_instability_20"
        )
        incremental_tp = metrics["true_positive"] - base_metrics["true_positive"]
        incremental_fp = metrics["false_positive"] - base_metrics["false_positive"]
        quality = prereg["atomic_quality_gates"]["instability"]
        eligible = (
            _passes_atomic_gate(
                metrics,
                proposition="structural_instability_20",
                quality=quality,
                sufficiency=prereg["development_sufficiency"],
            )
            and incremental_tp
            >= int(quality["minimum_incremental_true_positives_vs_same_profile_base"])
            and (
                incremental_tp > incremental_fp
                if quality["incremental_true_positives_must_exceed_incremental_false_positives"]
                else True
            )
        )
        instability_trials.append({
            "candidate": candidate,
            "base_metrics": base_metrics,
            "metrics": metrics,
            "incremental_true_positive": incremental_tp,
            "incremental_false_positive": incremental_fp,
            "eligible": eligible,
            "predictions": enhanced_predictions,
        })
    eligible_instability = [item for item in instability_trials if item["eligible"]]
    if not eligible_instability:
        return {
            "disposition": prereg["no_pass_disposition"]["no_eligible_instability_candidate"],
            "instability_trial_count": len(instability_trials),
            "exact_freeze": None,
            "preserved_2021_read_authorized": False,
        }
    instability_selected = min(
        eligible_instability,
        key=lambda item: (
            -item["metrics"]["precision"],
            item["metrics"]["definite_negative_false_positive_rate"],
            -item["metrics"]["positive_reference_coverage"],
            -item["incremental_true_positive"],
            item["candidate"].candidate_id,
        ),
    )

    relocation_predictions = {
        str(case["case_id"]): relocation_truth(case) for case in cases
    }
    relocation_metrics = _atomic_metrics(
        case_ids,
        references,
        relocation_predictions,
        "coherent_directional_relocation_20",
    )
    if not _passes_atomic_gate(
        relocation_metrics,
        proposition="coherent_directional_relocation_20",
        quality=prereg["atomic_quality_gates"]["relocation"],
        sufficiency=prereg["development_sufficiency"],
    ):
        return {
            "disposition": prereg["no_pass_disposition"]["fixed_relocation_gate_fail"],
            "relocation_metrics": relocation_metrics,
            "exact_freeze": None,
            "preserved_2021_read_authorized": False,
        }

    order_trials = []
    for candidate in stage2_candidate_grid(basis):
        predictions = {
            str(case["case_id"]): order_truth(case, candidate) for case in cases
        }
        metrics = _atomic_metrics(
            case_ids, references, predictions, "orderly_consolidation_20"
        )
        eligible = _passes_atomic_gate(
            metrics,
            proposition="orderly_consolidation_20",
            quality=prereg["atomic_quality_gates"]["order"],
            sufficiency=prereg["development_sufficiency"],
        )
        order_trials.append({
            "candidate": candidate,
            "metrics": metrics,
            "eligible": eligible,
            "predictions": predictions,
        })
    eligible_order = [item for item in order_trials if item["eligible"]]
    if not eligible_order:
        return {
            "disposition": prereg["no_pass_disposition"]["no_eligible_order_profile"],
            "order_trial_count": len(order_trials),
            "exact_freeze": None,
            "preserved_2021_read_authorized": False,
        }
    order_selected = min(
        eligible_order,
        key=lambda item: (
            -item["metrics"]["precision"],
            item["metrics"]["definite_negative_false_positive_rate"],
            -item["metrics"]["positive_reference_coverage"],
            item["candidate"].order_profile_id,
        ),
    )

    predicted_projection: dict[str, str] = {}
    ambiguous_conflicts = 0
    for case_id in case_ids:
        atomics = {
            "structural_instability_20": instability_selected["predictions"][case_id],
            "coherent_directional_relocation_20": relocation_predictions[case_id],
            "orderly_consolidation_20": order_selected["predictions"][case_id],
        }
        projected = project_atomic_primary(atomics, available=True)
        predicted_projection[case_id] = projected["status"]
        ambiguous_conflicts += int(projected["status"] == "AMBIGUOUS_CONFLICT")

    stage0_metrics = _projection_class_metrics(
        case_ids, references, predicted_projection, "STAGE_0"
    )
    stage2_metrics = _projection_class_metrics(
        case_ids, references, predicted_projection, "STAGE_2"
    )
    projection_gate = prereg["projection_compatibility_gate"]
    conflict_rate = Decimal(ambiguous_conflicts) / Decimal(len(case_ids))
    projection_pass = (
        _passes_projection_gate(stage0_metrics, projection_gate, "stage0")
        and _passes_projection_gate(stage2_metrics, projection_gate, "stage2")
        and conflict_rate <= Decimal(projection_gate["maximum_prediction_ambiguous_conflict_rate"])
    )
    if not projection_pass:
        return {
            "disposition": prereg["no_pass_disposition"]["projection_compatibility_fail"],
            "instability_candidate": instability_selected["candidate"].to_dict(),
            "relocation_metrics": relocation_metrics,
            "order_candidate": order_selected["candidate"].to_dict(),
            "projection": {
                "stage0": stage0_metrics,
                "stage2": stage2_metrics,
                "ambiguous_conflict_rate": str(conflict_rate),
            },
            "exact_freeze": None,
            "preserved_2021_read_authorized": False,
        }

    freeze = {
        "schema_version": "quanthus.stage-atomic-proposition-development-freeze.v1",
        "instability_candidate": instability_selected["candidate"].to_dict(),
        "fixed_envelope_channel": prereg["instability_candidate_surface"]["fixed_additive_channel"],
        "relocation": {
            "source": prereg["relocation_surface"]["source"],
            "threshold_retune": False,
        },
        "order_candidate": order_selected["candidate"].to_dict(),
        "resolver": prereg["projection_compatibility_gate"]["resolver"],
    }
    return {
        "disposition": "EXACT_ATOMIC_AND_PROJECTION_FREEZE_FOR_PRESERVED_2021",
        "instability_metrics": instability_selected["metrics"],
        "instability_base_metrics": instability_selected["base_metrics"],
        "instability_incremental_true_positive": instability_selected["incremental_true_positive"],
        "instability_incremental_false_positive": instability_selected[
            "incremental_false_positive"
        ],
        "relocation_metrics": relocation_metrics,
        "order_metrics": order_selected["metrics"],
        "projection": {
            "stage0": stage0_metrics,
            "stage2": stage2_metrics,
            "ambiguous_conflict_rate": str(conflict_rate),
        },
        "instability_trial_count": len(instability_trials),
        "order_trial_count": len(order_trials),
        "joint_pair_search_count": 0,
        "exact_freeze": freeze,
        "exact_freeze_digest": digest(freeze),
        "preserved_2021_read_authorized": True,
    }


def _projection_class_metrics(
    case_ids: Sequence[str],
    references: Mapping[str, Mapping[str, Any]],
    predictions: Mapping[str, str],
    positive_reference: str,
) -> dict[str, Any]:
    tp = fp = fn = positives = negatives = predicted = ambiguous = unavailable = 0
    for case_id in case_ids:
        reference = str(references[case_id].get("projected_primary_reference") or "")
        prediction = predictions[case_id]
        if reference in {"AMBIGUOUS", "AMBIGUOUS_CONFLICT"}:
            ambiguous += 1
            continue
        if reference == "INPUT_UNAVAILABLE":
            unavailable += 1
            continue
        if reference not in {"STAGE_0", "STAGE_2", "NEITHER"}:
            raise AtomicPropositionDevelopmentError("projected_reference_value")
        is_predicted = prediction == positive_reference
        if reference == positive_reference:
            positives += 1
            tp += int(is_predicted)
            fn += int(not is_predicted)
        else:
            negatives += 1
            fp += int(is_predicted)
        predicted += int(is_predicted)
    return {
        "case_count": len(case_ids),
        "complete_reference_count": len(case_ids) - unavailable,
        "definite_positive_references": positives,
        "definite_negative_references": negatives,
        "ambiguous_references": ambiguous,
        "input_unavailable_references": unavailable,
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "predictions": predicted,
        "precision": _ratio(tp, tp + fp),
        "positive_reference_coverage": _ratio(tp, positives),
        "definite_negative_false_positive_rate": _ratio(fp, negatives),
    }


def _passes_projection_gate(
    metrics: Mapping[str, Any], gates: Mapping[str, Any], prefix: str
) -> bool:
    precision = metrics["precision"]
    coverage = metrics["positive_reference_coverage"]
    fpr = metrics["definite_negative_false_positive_rate"]
    return bool(
        metrics["definite_positive_references"]
        >= gates[f"minimum_definite_{prefix}_positive_references"]
        and metrics["definite_negative_references"]
        >= gates[f"minimum_{prefix}_definite_negative_references"]
        and metrics["predictions"] >= gates[f"{prefix}_minimum_predictions"]
        and precision is not None
        and precision >= Decimal(gates[f"{prefix}_minimum_precision"])
        and coverage is not None
        and coverage >= Decimal(gates[f"{prefix}_minimum_positive_reference_coverage"])
        and fpr is not None
        and fpr <= Decimal(gates[f"{prefix}_maximum_definite_negative_false_positive_rate"])
    )

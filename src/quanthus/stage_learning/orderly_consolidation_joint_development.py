"""Pure P3-STAGE-011-D joint threshold basis and frozen 9x3 pair identities."""
from __future__ import annotations

import itertools
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.stage_learning.directional_boundary_development import (
    case_features,
    relocation_guard,
)
from quanthus.stage_learning.orderly_consolidation_projection import (
    OrderlyConsolidationMeasurements,
    OrderlyConsolidationThresholds,
    contraction_magnitude20,
    project_orderly_consolidation,
)
from quanthus.stage_learning.positive_instability_quantitative_development import (
    FROZEN_008_RELOCATION,
    QuantitativeInstabilityCandidate,
    baseline_stage0,
    candidate_instability,
    nearest_rank,
)
from quanthus.stage_learning.positive_instability_quantitative_development import (
    candidate_grid as stage0_candidate_grid,
)
from quanthus.stage_learning.positive_instability_quantitative_development import (
    threshold_basis as stage0_threshold_basis,
)
from quanthus.structure_v2_inputs import digest

PREREG_PATH = "config/stage-learning/orderly-consolidation-joint-development-screen.v1.json"
PREREG_DIGEST = "sha256:c9b5eff892f3d731170afac052e3810543f7444f5b6025bdef24c1ebfa4c213e"
EXECUTION_PATH = "config/stage-learning/orderly-consolidation-joint-development-execution.v1.json"
EXECUTION_DIGEST = "sha256:3424f673f11af42d37a454429fb9e624978b18799d10fea21c365f81c0c0d680"


class OrderlyConsolidationJointDevelopmentError(ValueError):
    """The frozen P3-STAGE-011-D joint development contract was violated."""


def _load(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OrderlyConsolidationJointDevelopmentError(f"{label}_unreadable") from exc
    if not isinstance(value, dict):
        raise OrderlyConsolidationJointDevelopmentError(f"{label}_must_be_object")
    return value


def load_preregistration(root: Path | None = None) -> dict[str, Any]:
    root = root or find_project_root()
    value = _load(root / PREREG_PATH, "joint_preregistration")
    if digest(value) != PREREG_DIGEST:
        raise OrderlyConsolidationJointDevelopmentError("joint_preregistration_drift")
    return value


def load_execution(root: Path | None = None) -> dict[str, Any]:
    root = root or find_project_root()
    value = _load(root / EXECUTION_PATH, "joint_execution")
    if digest(value) != EXECUTION_DIGEST:
        raise OrderlyConsolidationJointDevelopmentError("joint_execution_drift")
    if value["preregistration"]["digest"] != PREREG_DIGEST:
        raise OrderlyConsolidationJointDevelopmentError("execution_preregistration_binding")
    return value


def _decimal(value: Any, label: str) -> Decimal:
    try:
        observed = Decimal(str(value))
    except Exception as exc:
        raise OrderlyConsolidationJointDevelopmentError(f"invalid_decimal:{label}") from exc
    if not observed.is_finite():
        raise OrderlyConsolidationJointDevelopmentError(f"nonfinite_decimal:{label}")
    return observed


def threshold_basis(feature_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Extend the unchanged P010 basis with positive contraction magnitude only."""
    stage0 = stage0_threshold_basis(feature_rows)
    contractions: list[Decimal] = []
    for row in feature_rows:
        change = _decimal(row["width_change20"], "width_change20")
        magnitude = contraction_magnitude20(change)
        if magnitude is not None and magnitude > 0:
            contractions.append(magnitude)
    if not contractions:
        raise OrderlyConsolidationJointDevelopmentError(
            "positive_contraction_subpopulation_empty"
        )
    prereg = load_preregistration()
    quantiles = {
        str(profile["positive_contraction_min_quantile"])
        for profile in prereg["joint_candidate_surface"]["stage2_profiles"]
    }
    contraction_basis = {
        q: str(nearest_rank(contractions, Decimal(q)))
        for q in sorted(quantiles, key=Decimal)
    }
    return {
        **stage0,
        "schema_version": "quanthus.stage-orderly-consolidation-joint-threshold-basis.v1",
        "positive_contraction_magnitude_observation_count": len(contractions),
        "positive_contraction_magnitude20": contraction_basis,
        "stage0_basis_topology": "P3-STAGE-010_EXACT",
        "stage2_basis_topology": "P3-STAGE-011_THREE_BUNDLED_PROFILES",
        "labels_used": False,
        "reference_opinions_used": False,
    }


@dataclass(frozen=True)
class OrderlyConsolidationCandidate:
    order_profile_id: str
    thresholds: OrderlyConsolidationThresholds

    @property
    def candidate_id(self) -> str:
        return digest(self.to_dict(include_id=False))

    def to_dict(self, *, include_id: bool = True) -> dict[str, str]:
        value = {
            "order_profile_id": self.order_profile_id,
            "efficiency20_abs_max": str(self.thresholds.efficiency20_abs_max),
            "range_width20_max": str(self.thresholds.range_width20_max),
            "contraction_magnitude20_min": str(
                self.thresholds.contraction_magnitude20_min
            ),
        }
        return {"candidate_id": self.candidate_id, **value} if include_id else value


def stage2_candidate_grid(
    basis: Mapping[str, Any],
) -> tuple[OrderlyConsolidationCandidate, ...]:
    profiles = load_preregistration()["joint_candidate_surface"]["stage2_profiles"]
    output = []
    for profile in profiles:
        output.append(
            OrderlyConsolidationCandidate(
                order_profile_id=str(profile["id"]),
                thresholds=OrderlyConsolidationThresholds(
                    efficiency20_abs_max=_decimal(
                        basis["efficiency20_abs"][profile["efficiency_max_quantile"]],
                        "efficiency20_abs_max",
                    ),
                    range_width20_max=_decimal(
                        basis["range_width20"][profile["range_width_max_quantile"]],
                        "range_width20_max",
                    ),
                    contraction_magnitude20_min=_decimal(
                        basis["positive_contraction_magnitude20"][
                            profile["positive_contraction_min_quantile"]
                        ],
                        "contraction_magnitude20_min",
                    ),
                ),
            )
        )
    candidates = tuple(output)
    if len(candidates) != 3 or len({item.candidate_id for item in candidates}) != 3:
        raise OrderlyConsolidationJointDevelopmentError("stage2_candidate_identity")
    return candidates


@dataclass(frozen=True)
class JointCandidatePair:
    stage0: QuantitativeInstabilityCandidate
    stage2: OrderlyConsolidationCandidate

    @property
    def pair_id(self) -> str:
        return digest(
            {
                "stage0_candidate_id": self.stage0.candidate_id,
                "stage2_candidate_id": self.stage2.candidate_id,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair_id": self.pair_id,
            "stage0_candidate": self.stage0.to_dict(),
            "stage2_candidate": self.stage2.to_dict(),
        }


def joint_candidate_grid(basis: Mapping[str, Any]) -> tuple[JointCandidatePair, ...]:
    stage0 = stage0_candidate_grid(basis)
    stage2 = stage2_candidate_grid(basis)
    pairs = tuple(
        JointCandidatePair(left, right)
        for left, right in itertools.product(stage0, stage2)
    )
    if len(pairs) != 27 or len({pair.pair_id for pair in pairs}) != 27:
        raise OrderlyConsolidationJointDevelopmentError("joint_pair_identity")
    return pairs


def _ratio(numerator: int, denominator: int) -> Decimal | None:
    return None if denominator == 0 else Decimal(numerator) / Decimal(denominator)


def _relocation_truth(case: Mapping[str, Any]) -> str:
    return "TRUE" if relocation_guard(case, FROZEN_008_RELOCATION) else "FALSE"


def _order_status(
    case: Mapping[str, Any], candidate: OrderlyConsolidationCandidate
) -> str:
    features = case_features(case)
    return project_orderly_consolidation(
        OrderlyConsolidationMeasurements(
            efficiency20_abs=features["efficiency20_abs"],
            range_width20=features["range_width20"],
            width_change20=features["width_change20"],
            coherent_directional_relocation20=_relocation_truth(case),  # type: ignore[arg-type]
        ),
        candidate.thresholds,
    ).status


def pair_predictions(case: Mapping[str, Any], pair: JointCandidatePair) -> dict[str, Any]:
    instability = candidate_instability(case, pair.stage0)
    relocation = _relocation_truth(case)
    order = _order_status(case, pair.stage2)
    proposition_overlap = (
        instability == "TRUE" and relocation == "FALSE" and order == "TRUE"
    )
    return {
        "instability": instability,
        "relocation": relocation,
        "order": order,
        "stage_0": instability == "TRUE" and relocation == "FALSE" and order == "FALSE",
        "stage_2": instability == "FALSE" and relocation == "FALSE" and order == "TRUE",
        "proposition_overlap": proposition_overlap,
    }


def _class_metrics(
    cases: Sequence[Mapping[str, Any]],
    references: Mapping[str, str],
    predictions: Mapping[str, bool],
    *,
    positive_reference: str,
) -> dict[str, Any]:
    tp = fp = fn = positives = negatives = predicted = ambiguous = unavailable = 0
    for case in cases:
        case_id = str(case.get("case_id") or "")
        reference = references.get(case_id)
        if reference is None:
            raise OrderlyConsolidationJointDevelopmentError("reference_missing_for_case")
        prediction = bool(predictions[case_id])
        if reference == "AMBIGUOUS":
            ambiguous += 1
            continue
        if reference == "INPUT_UNAVAILABLE":
            unavailable += 1
            continue
        if reference not in {"STAGE_0", "STAGE_2", "NEITHER"}:
            raise OrderlyConsolidationJointDevelopmentError("unsupported_primary_reference")
        if reference == positive_reference:
            positives += 1
            tp += int(prediction)
            fn += int(not prediction)
        else:
            negatives += 1
            fp += int(prediction)
        predicted += int(prediction)
    return {
        "case_count": len(cases),
        "complete_reference_count": len(cases) - unavailable,
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


def _stage0_baseline_metrics(
    cases: Sequence[Mapping[str, Any]], references: Mapping[str, str]
) -> dict[str, Any]:
    predictions = {
        str(case["case_id"]): baseline_stage0(case, FROZEN_008_RELOCATION)
        for case in cases
    }
    return _class_metrics(
        cases, references, predictions, positive_reference="STAGE_0"
    )


def pair_metrics(
    cases: Sequence[Mapping[str, Any]],
    references: Mapping[str, str],
    pair: JointCandidatePair,
) -> dict[str, Any]:
    observed = {str(case["case_id"]): pair_predictions(case, pair) for case in cases}
    stage0 = _class_metrics(
        cases,
        references,
        {case_id: value["stage_0"] for case_id, value in observed.items()},
        positive_reference="STAGE_0",
    )
    stage2 = _class_metrics(
        cases,
        references,
        {case_id: value["stage_2"] for case_id, value in observed.items()},
        positive_reference="STAGE_2",
    )
    return {
        "stage_0": stage0,
        "stage_2": stage2,
        "proposition_overlap_count": sum(
            int(value["proposition_overlap"]) for value in observed.values()
        ),
    }


def _absolute_class_gate(
    metrics: Mapping[str, Any], gates: Mapping[str, Any], *, prefix: str
) -> bool:
    precision = metrics["precision"]
    coverage = metrics["positive_reference_coverage"]
    fpr = metrics["definite_negative_false_positive_rate"]
    return bool(
        metrics["complete_reference_count"] >= gates["minimum_complete_cases"]
        and metrics["definite_positive_references"]
        >= gates[f"minimum_definite_{prefix}_positive_references"]
        and metrics["definite_negative_references"]
        >= gates[f"minimum_{prefix}_definite_negative_references"]
        and metrics["predictions"] >= gates[f"minimum_{prefix}_predictions"]
        and precision is not None
        and precision >= Decimal(gates[f"{prefix}_minimum_precision"])
        and coverage is not None
        and coverage >= Decimal(gates[f"{prefix}_minimum_positive_reference_coverage"])
        and fpr is not None
        and fpr <= Decimal(gates[f"{prefix}_maximum_definite_negative_false_positive_rate"])
    )


def pair_passes(
    observed: Mapping[str, Any],
    baseline_stage0_metrics: Mapping[str, Any],
    gates: Mapping[str, Any],
) -> bool:
    for key in (
        "precision",
        "positive_reference_coverage",
        "definite_negative_false_positive_rate",
    ):
        if baseline_stage0_metrics.get(key) is None:
            raise OrderlyConsolidationJointDevelopmentError(
                "stage0_baseline_metric_unavailable"
            )
    stage0 = observed["stage_0"]
    stage2 = observed["stage_2"]
    if not _absolute_class_gate(stage0, gates, prefix="stage_0"):
        return False
    if not _absolute_class_gate(stage2, gates, prefix="stage_2"):
        return False
    if observed["proposition_overlap_count"] != gates["pair_overlap_count_must_equal"]:
        return False
    if stage0["precision"] < baseline_stage0_metrics["precision"]:
        return False
    if (
        stage0["definite_negative_false_positive_rate"]
        > baseline_stage0_metrics["definite_negative_false_positive_rate"]
    ):
        return False
    if (
        baseline_stage0_metrics["positive_reference_coverage"]
        - stage0["positive_reference_coverage"]
        > Decimal(gates["stage_0_maximum_absolute_coverage_loss_vs_frozen_008_baseline"])
    ):
        return False
    return not (
        baseline_stage0_metrics["false_positive"] > 0
        and baseline_stage0_metrics["false_positive"] - stage0["false_positive"]
        < gates["if_stage_0_baseline_has_false_positive_candidate_must_remove_at_least"]
    )


def select_development_pair(
    cases: Sequence[Mapping[str, Any]],
    references: Mapping[str, str],
    basis: Mapping[str, Any],
) -> dict[str, Any]:
    gates = load_preregistration()["development_gates"]
    baseline = _stage0_baseline_metrics(cases, references)
    trials = []
    for pair in joint_candidate_grid(basis):
        observed = pair_metrics(cases, references, pair)
        trials.append(
            {
                "pair": pair,
                "metrics": observed,
                "eligible": pair_passes(observed, baseline, gates),
            }
        )
    eligible = [trial for trial in trials if trial["eligible"]]

    def serialized(trial: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "pair": trial["pair"].to_dict(),
            "metrics": trial["metrics"],
            "eligible": trial["eligible"],
        }

    if not eligible:
        return {
            "disposition": gates["no_eligible_pair_disposition"],
            "baseline_stage_0": baseline,
            "pair": None,
            "trial_count": len(trials),
            "eligible_count": 0,
            "trials": [serialized(trial) for trial in trials],
        }

    def order(trial: Mapping[str, Any]) -> tuple[Any, ...]:
        metrics = trial["metrics"]
        stage0 = metrics["stage_0"]
        stage2 = metrics["stage_2"]
        return (
            -min(stage0["precision"], stage2["precision"]),
            max(
                stage0["definite_negative_false_positive_rate"],
                stage2["definite_negative_false_positive_rate"],
            ),
            -min(
                stage0["positive_reference_coverage"],
                stage2["positive_reference_coverage"],
            ),
            trial["pair"].stage0.candidate_id,
            trial["pair"].stage2.candidate_id,
        )

    selected = min(eligible, key=order)
    return {
        "disposition": "PAIR_FROZEN_FOR_PRESERVED_EVALUATION",
        "baseline_stage_0": baseline,
        "pair": selected["pair"].to_dict(),
        "metrics": selected["metrics"],
        "trial_count": len(trials),
        "eligible_count": len(eligible),
        "trials": [serialized(trial) for trial in trials],
    }

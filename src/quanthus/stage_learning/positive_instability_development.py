"""Pure P3-STAGE-009-D threshold-basis, candidate-grid and scoring logic.

The module accepts already prepared T-only case views and sealed references. It performs no market
read, Worker submission, opinion generation, grid expansion, Stage-2 threshold change, or relocation
retuning.
"""
from __future__ import annotations

import itertools
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.stage_learning.directional_boundary_development import (
    RelocationCandidate,
    case_features,
    relocation_guard,
    unchanged_stage2,
)
from quanthus.stage_learning.directional_boundary_development import (
    candidate_stage0 as baseline_stage0,
)
from quanthus.stage_learning.positive_instability import (
    PrimitiveEvidence,
    assess_positive_instability,
)
from quanthus.structure_v2_inputs import digest

PREREG_PATH = "config/stage-learning/positive-instability-development-screen.v1.json"
PREREG_DIGEST = "sha256:2ad1afc0c0ac1f9ba9822f1b34dc9f9a995e6c8783b7509e8c5189e897f4110a"
EXECUTION_PATH = "config/stage-learning/positive-instability-development-execution.v1.json"
EXECUTION_DIGEST = "sha256:16d21d839ecfff9146114aaf73a634a55717ed48104fc986de5e791c7d41a8b4"
FROZEN_008_RELOCATION = RelocationCandidate(Decimal("0.10"), Decimal("0.50"), Decimal("0.60"))
DEFINITE_REFERENCES = {"STAGE_0", "STAGE_2", "NEITHER"}


class PositiveInstabilityDevelopmentError(ValueError):
    """The preregistered Stage-009-D development contract was violated."""


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PositiveInstabilityDevelopmentError(f"{label}_unreadable") from exc
    if not isinstance(value, dict):
        raise PositiveInstabilityDevelopmentError(f"{label}_must_be_object")
    return value


def load_preregistration(root: Path | None = None) -> dict[str, Any]:
    root = root or find_project_root()
    value = _load_json(root / PREREG_PATH, "positive_instability_preregistration")
    if digest(value) != PREREG_DIGEST:
        raise PositiveInstabilityDevelopmentError("positive_instability_preregistration_drift")
    return value


def load_execution(root: Path | None = None) -> dict[str, Any]:
    root = root or find_project_root()
    value = _load_json(root / EXECUTION_PATH, "positive_instability_execution")
    if digest(value) != EXECUTION_DIGEST:
        raise PositiveInstabilityDevelopmentError("positive_instability_execution_drift")
    if value["preregistration"]["digest"] != PREREG_DIGEST:
        raise PositiveInstabilityDevelopmentError("execution_preregistration_binding")
    return value


def nearest_rank(values: Sequence[Decimal], quantile: Decimal) -> Decimal:
    """Deterministic 1-based nearest-rank quantile without interpolation."""
    if not values:
        raise PositiveInstabilityDevelopmentError("quantile_requires_values")
    if not Decimal(0) < quantile <= Decimal(1):
        raise PositiveInstabilityDevelopmentError("quantile_out_of_range")
    ordered = sorted(values)
    scaled = quantile * Decimal(len(ordered))
    rank = max(1, int(scaled.to_integral_value(rounding=ROUND_CEILING)))
    return ordered[rank - 1]


def _decimal(value: Any, label: str) -> Decimal:
    try:
        observed = Decimal(str(value))
    except Exception as exc:
        raise PositiveInstabilityDevelopmentError(f"invalid_decimal:{label}") from exc
    if not observed.is_finite():
        raise PositiveInstabilityDevelopmentError(f"nonfinite_decimal:{label}")
    return observed


def threshold_basis(feature_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Build unlabeled quantile thresholds from primary-available development observations."""
    if not feature_rows:
        raise PositiveInstabilityDevelopmentError("threshold_basis_requires_rows")
    fields: dict[str, list[Decimal]] = {
        "range_width20": [],
        "width_change20_positive": [],
        "efficiency20_abs": [],
        "range_traversal20": [],
        "counter_direction_excursion_share20": [],
    }
    for row in feature_rows:
        width = _decimal(row["range_width20"], "range_width20")
        width_change = _decimal(row["width_change20"], "width_change20")
        efficiency = _decimal(row["efficiency20_abs"], "efficiency20_abs")
        traversal = _decimal(row["range_traversal20"], "range_traversal20")
        fields["range_width20"].append(width)
        fields["efficiency20_abs"].append(efficiency)
        fields["range_traversal20"].append(traversal)
        if width_change > 0:
            fields["width_change20_positive"].append(width_change)
        counter = row.get("counter_direction_excursion_share20")
        if counter is not None:
            fields["counter_direction_excursion_share20"].append(
                _decimal(counter, "counter_direction_excursion_share20")
            )
    if not fields["width_change20_positive"] or not fields[
        "counter_direction_excursion_share20"
    ]:
        raise PositiveInstabilityDevelopmentError("threshold_basis_required_subpopulation_empty")

    config = load_preregistration()["threshold_basis"]

    def project(name: str, quantiles: Sequence[str]) -> dict[str, str]:
        values = fields[name]
        return {
            quantile: str(nearest_rank(values, Decimal(quantile)))
            for quantile in quantiles
        }

    return {
        "schema_version": "quanthus.stage-positive-instability-threshold-basis.v1",
        "method": load_execution()["quantile_method"]["name"],
        "observation_count": len(feature_rows),
        "positive_width_change_observation_count": len(fields["width_change20_positive"]),
        "counter_excursion_available_observation_count": len(
            fields["counter_direction_excursion_share20"]
        ),
        "range_width20": project("range_width20", config["range_width_quantiles"]),
        "positive_width_change20": project(
            "width_change20_positive", config["positive_width_change_quantiles"]
        ),
        "efficiency20_abs": project(
            "efficiency20_abs", config["absolute_directional_efficiency_quantiles"]
        ),
        "range_traversal20": project(
            "range_traversal20", config["range_traversal_quantiles"]
        ),
        "counter_direction_excursion_share20": project(
            "counter_direction_excursion_share20",
            config["counter_direction_excursion_quantiles"],
        ),
        "labels_used": False,
    }


@dataclass(frozen=True)
class InstabilityCandidate:
    core_profile_id: str
    expansion_profile_id: str
    reversal_profile_id: str
    range_width_min: Decimal
    positive_width_change_min: Decimal
    absolute_directional_efficiency_max: Decimal
    range_traversal_max: Decimal
    counter_direction_excursion_min: Decimal

    @property
    def candidate_id(self) -> str:
        return digest(self.to_dict(include_id=False))

    def to_dict(self, *, include_id: bool = True) -> dict[str, str]:
        value = {
            "core_profile_id": self.core_profile_id,
            "expansion_profile_id": self.expansion_profile_id,
            "reversal_profile_id": self.reversal_profile_id,
            "range_width_min": str(self.range_width_min),
            "positive_width_change_min": str(self.positive_width_change_min),
            "absolute_directional_efficiency_max": str(
                self.absolute_directional_efficiency_max
            ),
            "range_traversal_max": str(self.range_traversal_max),
            "counter_direction_excursion_min": str(self.counter_direction_excursion_min),
        }
        return {"candidate_id": self.candidate_id, **value} if include_id else value


def candidate_grid(basis: Mapping[str, Any]) -> tuple[InstabilityCandidate, ...]:
    config = load_preregistration()["candidate_grid"]
    output = []
    for core, expansion, reversal in itertools.product(
        config["shared_core_profiles"],
        config["expansion_profiles"],
        config["reversal_profiles"],
    ):
        output.append(
            InstabilityCandidate(
                core_profile_id=core["id"],
                expansion_profile_id=expansion["id"],
                reversal_profile_id=reversal["id"],
                range_width_min=_decimal(
                    basis["range_width20"][core["range_width_quantile"]],
                    "candidate_range_width_min",
                ),
                positive_width_change_min=_decimal(
                    basis["positive_width_change20"][expansion["positive_width_change_quantile"]],
                    "candidate_positive_width_change_min",
                ),
                absolute_directional_efficiency_max=_decimal(
                    basis["efficiency20_abs"][core["low_efficiency_quantile"]],
                    "candidate_efficiency_max",
                ),
                range_traversal_max=_decimal(
                    basis["range_traversal20"][reversal["low_traversal_quantile"]],
                    "candidate_traversal_max",
                ),
                counter_direction_excursion_min=_decimal(
                    basis["counter_direction_excursion_share20"][
                        reversal["high_counter_excursion_quantile"]
                    ],
                    "candidate_counter_min",
                ),
            )
        )
    candidates = tuple(output)
    if len(candidates) != config["candidate_count"] or len(
        {candidate.candidate_id for candidate in candidates}
    ) != len(candidates):
        raise PositiveInstabilityDevelopmentError("candidate_grid_identity")
    return candidates


def primitive_evidence_for_case(
    case: Mapping[str, Any], candidate: InstabilityCandidate
) -> PrimitiveEvidence:
    features = case_features(case)
    counter = features["counter_direction_excursion_share20"]
    return PrimitiveEvidence(
        range_width_material_20=(
            "TRUE" if features["range_width20"] >= candidate.range_width_min else "FALSE"
        ),
        width_expansion_material_20=(
            "TRUE"
            if features["width_change20"] >= candidate.positive_width_change_min
            else "FALSE"
        ),
        low_directional_efficiency_20=(
            "TRUE"
            if features["efficiency20_abs"] <= candidate.absolute_directional_efficiency_max
            else "FALSE"
        ),
        low_range_traversal_20=(
            "TRUE" if features["range_traversal20"] <= candidate.range_traversal_max else "FALSE"
        ),
        high_counter_direction_excursion_20=(
            "UNKNOWN"
            if counter is None
            else (
                "TRUE"
                if counter >= candidate.counter_direction_excursion_min
                else "FALSE"
            )
        ),
    )


def candidate_instability(case: Mapping[str, Any], candidate: InstabilityCandidate) -> str:
    return assess_positive_instability(primitive_evidence_for_case(case, candidate)).status


def candidate_stage0(case: Mapping[str, Any], candidate: InstabilityCandidate) -> bool:
    return (
        candidate_instability(case, candidate) == "TRUE"
        and not relocation_guard(case, FROZEN_008_RELOCATION)
        and not unchanged_stage2(case)
    )


def _ratio(numerator: int, denominator: int) -> Decimal | None:
    return None if denominator == 0 else Decimal(numerator) / Decimal(denominator)


def stage0_metrics(
    cases: Sequence[Mapping[str, Any]],
    references: Mapping[str, str],
    *,
    candidate: InstabilityCandidate | None,
) -> dict[str, Any]:
    tp = fp = fn = positive = negative = ambiguous = predictions = stage2_overlap = 0
    for case in cases:
        case_id = str(case.get("case_id"))
        reference = references.get(case_id)
        if reference is None:
            raise PositiveInstabilityDevelopmentError("reference_missing_for_case")
        prediction = (
            baseline_stage0(case, FROZEN_008_RELOCATION)
            if candidate is None
            else candidate_stage0(case, candidate)
        )
        if candidate is not None and (
            candidate_instability(case, candidate) == "TRUE" and unchanged_stage2(case)
        ):
            stage2_overlap += 1
        if reference == "AMBIGUOUS":
            ambiguous += 1
            continue
        if reference == "INPUT_UNAVAILABLE":
            continue
        if reference not in DEFINITE_REFERENCES:
            raise PositiveInstabilityDevelopmentError("unsupported_primary_reference")
        if reference == "STAGE_0":
            positive += 1
            if prediction:
                tp += 1
            else:
                fn += 1
        else:
            negative += 1
            if prediction:
                fp += 1
        predictions += int(prediction)
    return {
        "case_count": len(cases),
        "definite_positive_references": positive,
        "definite_negative_references": negative,
        "ambiguous_references": ambiguous,
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "stage_0_predictions": predictions,
        "precision": _ratio(tp, tp + fp),
        "positive_reference_coverage": _ratio(tp, positive),
        "definite_negative_false_positive_rate": _ratio(fp, negative),
        "candidate_instability_stage2_overlap_count": stage2_overlap,
    }


def _meets_absolute_gates(metrics: Mapping[str, Any], gates: Mapping[str, Any]) -> bool:
    if (
        metrics["case_count"] < gates["minimum_complete_cases"]
        or metrics["definite_positive_references"]
        < gates["minimum_definite_stage_0_positive_references"]
        or metrics["definite_negative_references"]
        < gates["minimum_definite_stage_0_negative_references"]
        or metrics["stage_0_predictions"] < gates["minimum_stage_0_predictions"]
    ):
        return False
    precision = metrics["precision"]
    coverage = metrics["positive_reference_coverage"]
    fpr = metrics["definite_negative_false_positive_rate"]
    if precision is None or coverage is None or fpr is None:
        return False
    return (
        precision >= Decimal(gates["minimum_stage_0_precision"])
        and coverage >= Decimal(gates["minimum_stage_0_positive_reference_coverage"])
        and fpr <= Decimal(gates["maximum_stage_0_definite_negative_false_positive_rate"])
        and metrics["candidate_instability_stage2_overlap_count"] == 0
    )


def candidate_passes(
    observed: Mapping[str, Any], baseline: Mapping[str, Any], gates: Mapping[str, Any]
) -> bool:
    if not _meets_absolute_gates(observed, gates):
        return False
    if observed["precision"] < baseline["precision"]:
        return False
    if observed["definite_negative_false_positive_rate"] > baseline[
        "definite_negative_false_positive_rate"
    ]:
        return False
    if (
        baseline["positive_reference_coverage"] - observed["positive_reference_coverage"]
        > Decimal(gates["maximum_absolute_coverage_loss_vs_frozen_008_baseline"])
    ):
        return False
    return not (
        baseline["false_positive"] > 0
        and baseline["false_positive"] - observed["false_positive"]
        < gates["if_baseline_has_false_positive_candidate_must_remove_at_least"]
    )


def select_development_candidate(
    cases: Sequence[Mapping[str, Any]],
    references: Mapping[str, str],
    basis: Mapping[str, Any],
) -> dict[str, Any]:
    gates = load_preregistration()["development_gates"]
    baseline = stage0_metrics(cases, references, candidate=None)
    trials = []
    for candidate in candidate_grid(basis):
        observed = stage0_metrics(cases, references, candidate=candidate)
        trials.append(
            {
                "candidate": candidate,
                "metrics": observed,
                "eligible": candidate_passes(observed, baseline, gates),
            }
        )
    eligible = [trial for trial in trials if trial["eligible"]]
    if not eligible:
        return {
            "disposition": gates["no_eligible_candidate_disposition"],
            "baseline": baseline,
            "candidate": None,
            "trial_count": len(trials),
            "eligible_count": 0,
            "trials": [
                {
                    "candidate": trial["candidate"].to_dict(),
                    "metrics": trial["metrics"],
                    "eligible": trial["eligible"],
                }
                for trial in trials
            ],
        }

    def order(trial: Mapping[str, Any]) -> tuple[Any, ...]:
        metrics = trial["metrics"]
        return (
            -metrics["precision"],
            metrics["definite_negative_false_positive_rate"],
            -metrics["positive_reference_coverage"],
            trial["candidate"].candidate_id,
        )

    selected = min(eligible, key=order)
    return {
        "disposition": "CANDIDATE_FROZEN_FOR_TASK_DISJOINT_EVALUATION",
        "baseline": baseline,
        "candidate": selected["candidate"].to_dict(),
        "metrics": selected["metrics"],
        "trial_count": len(trials),
        "eligible_count": len(eligible),
        "trials": [
            {
                "candidate": trial["candidate"].to_dict(),
                "metrics": trial["metrics"],
                "eligible": trial["eligible"],
            }
            for trial in trials
        ],
    }

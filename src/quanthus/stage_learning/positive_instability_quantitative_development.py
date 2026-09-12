"""Pure P3-STAGE-010-D threshold-basis, nine-candidate grid and scoring logic."""
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
from quanthus.stage_learning.positive_instability_quantitative_projection import (
    ProjectionMeasurements,
    ProjectionThresholds,
    absolute_range_width_gain,
    project_positive_instability,
)
from quanthus.structure_v2_inputs import digest

PREREG_PATH = "config/stage-learning/positive-instability-quantitative-development-screen.v1.json"
PREREG_DIGEST = "sha256:f39abceeaec16307f6edd38bf80d6b14349d26aee7f8f9948faf950137085581"
EXECUTION_PATH = (
    "config/stage-learning/positive-instability-quantitative-development-execution.v1.json"
)
EXECUTION_DIGEST = "sha256:708c2efe94bf48feebd8652c92ca6750a8fe5882541453c2dda8ba17b062c127"
FROZEN_008_RELOCATION = RelocationCandidate(Decimal("0.10"), Decimal("0.50"), Decimal("0.60"))
DEFINITE_REFERENCES = {"STAGE_0", "STAGE_2", "NEITHER"}


class PositiveInstabilityQuantitativeDevelopmentError(ValueError):
    """The preregistered P3-STAGE-010-D development contract was violated."""


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PositiveInstabilityQuantitativeDevelopmentError(f"{label}_unreadable") from exc
    if not isinstance(value, dict):
        raise PositiveInstabilityQuantitativeDevelopmentError(f"{label}_must_be_object")
    return value


def load_preregistration(root: Path | None = None) -> dict[str, Any]:
    root = root or find_project_root()
    value = _load_json(root / PREREG_PATH, "quantitative_preregistration")
    if digest(value) != PREREG_DIGEST:
        raise PositiveInstabilityQuantitativeDevelopmentError("quantitative_preregistration_drift")
    return value


def load_execution(root: Path | None = None) -> dict[str, Any]:
    root = root or find_project_root()
    value = _load_json(root / EXECUTION_PATH, "quantitative_execution")
    if digest(value) != EXECUTION_DIGEST:
        raise PositiveInstabilityQuantitativeDevelopmentError("quantitative_execution_drift")
    if value["preregistration"]["digest"] != PREREG_DIGEST:
        raise PositiveInstabilityQuantitativeDevelopmentError("execution_preregistration_binding")
    return value


def nearest_rank(values: Sequence[Decimal], quantile: Decimal) -> Decimal:
    if not values:
        raise PositiveInstabilityQuantitativeDevelopmentError("quantile_requires_values")
    if not Decimal(0) < quantile <= Decimal(1):
        raise PositiveInstabilityQuantitativeDevelopmentError("quantile_out_of_range")
    ordered = sorted(values)
    scaled = quantile * Decimal(len(ordered))
    rank = max(1, int(scaled.to_integral_value(rounding=ROUND_CEILING)))
    return ordered[rank - 1]


def _decimal(value: Any, label: str) -> Decimal:
    try:
        observed = Decimal(str(value))
    except Exception as exc:
        raise PositiveInstabilityQuantitativeDevelopmentError(f"invalid_decimal:{label}") from exc
    if not observed.is_finite():
        raise PositiveInstabilityQuantitativeDevelopmentError(f"nonfinite_decimal:{label}")
    return observed


def threshold_basis(feature_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not feature_rows:
        raise PositiveInstabilityQuantitativeDevelopmentError("threshold_basis_requires_rows")
    fields: dict[str, list[Decimal]] = {
        "range_width20": [],
        "positive_width_change20": [],
        "positive_absolute_width_gain20": [],
        "efficiency20_abs": [],
        "range_traversal20": [],
        "counter_direction_excursion_share20": [],
    }
    for row in feature_rows:
        width = _decimal(row["range_width20"], "range_width20")
        change = _decimal(row["width_change20"], "width_change20")
        efficiency = _decimal(row["efficiency20_abs"], "efficiency20_abs")
        traversal = _decimal(row["range_traversal20"], "range_traversal20")
        fields["range_width20"].append(width)
        fields["efficiency20_abs"].append(efficiency)
        fields["range_traversal20"].append(traversal)
        if change > 0:
            fields["positive_width_change20"].append(change)
            gain = absolute_range_width_gain(width, change)
            if gain is not None and gain > 0:
                fields["positive_absolute_width_gain20"].append(gain)
        counter = row.get("counter_direction_excursion_share20")
        if counter is not None:
            fields["counter_direction_excursion_share20"].append(
                _decimal(counter, "counter_direction_excursion_share20")
            )
    for name in (
        "positive_width_change20",
        "positive_absolute_width_gain20",
        "counter_direction_excursion_share20",
    ):
        if not fields[name]:
            raise PositiveInstabilityQuantitativeDevelopmentError(
                "threshold_basis_required_subpopulation_empty:" + name
            )

    profiles = load_preregistration()["candidate_grid"]
    quantiles: dict[str, set[str]] = {
        "range_width20": set(),
        "positive_width_change20": set(),
        "positive_absolute_width_gain20": set(),
        "efficiency20_abs": set(),
        "range_traversal20": set(),
        "counter_direction_excursion_share20": set(),
    }
    for profile in profiles["expansion_profiles"]:
        quantiles["positive_width_change20"].add(profile["positive_width_change_quantile"])
        quantiles["range_width20"].add(profile["range_width_floor_quantile"])
        quantiles["positive_absolute_width_gain20"].add(
            profile["positive_absolute_width_gain_quantile"]
        )
        quantiles["efficiency20_abs"].add(profile["efficiency_max_quantile"])
        quantiles["range_traversal20"].add(profile["traversal_max_quantile"])
        quantiles["counter_direction_excursion_share20"].add(
            profile["counter_excursion_min_quantile"]
        )
    for profile in profiles["broad_profiles"]:
        quantiles["range_width20"].add(profile["range_width_quantile"])
        quantiles["efficiency20_abs"].add(profile["efficiency_max_quantile"])
        quantiles["range_traversal20"].add(profile["traversal_max_quantile"])
        quantiles["counter_direction_excursion_share20"].add(
            profile["counter_excursion_min_quantile"]
        )

    def project(name: str) -> dict[str, str]:
        return {
            q: str(nearest_rank(fields[name], Decimal(q)))
            for q in sorted(quantiles[name], key=Decimal)
        }

    return {
        "schema_version": "quanthus.stage-positive-instability-quantitative-threshold-basis.v1",
        "method": load_execution()["quantile_method"]["name"],
        "observation_count": len(feature_rows),
        "positive_width_change_observation_count": len(fields["positive_width_change20"]),
        "positive_absolute_width_gain_observation_count": len(
            fields["positive_absolute_width_gain20"]
        ),
        "counter_excursion_available_observation_count": len(
            fields["counter_direction_excursion_share20"]
        ),
        **{name: project(name) for name in quantiles},
        "labels_used": False,
    }


@dataclass(frozen=True)
class QuantitativeInstabilityCandidate:
    expansion_profile_id: str
    broad_profile_id: str
    thresholds: ProjectionThresholds

    @property
    def candidate_id(self) -> str:
        return digest(self.to_dict(include_id=False))

    def to_dict(self, *, include_id: bool = True) -> dict[str, str]:
        value = {
            "expansion_profile_id": self.expansion_profile_id,
            "broad_profile_id": self.broad_profile_id,
            **{
                field: str(getattr(self.thresholds, field))
                for field in self.thresholds.__dataclass_fields__
            },
        }
        return {"candidate_id": self.candidate_id, **value} if include_id else value


def candidate_grid(basis: Mapping[str, Any]) -> tuple[QuantitativeInstabilityCandidate, ...]:
    config = load_preregistration()["candidate_grid"]
    output = []
    for expansion, broad in itertools.product(
        config["expansion_profiles"], config["broad_profiles"]
    ):
        thresholds = ProjectionThresholds(
            expansion_width_change_min=_decimal(
                basis["positive_width_change20"][expansion["positive_width_change_quantile"]],
                "expansion_width_change_min",
            ),
            expansion_range_width_min=_decimal(
                basis["range_width20"][expansion["range_width_floor_quantile"]],
                "expansion_range_width_min",
            ),
            expansion_absolute_width_gain_min=_decimal(
                basis["positive_absolute_width_gain20"][
                    expansion["positive_absolute_width_gain_quantile"]
                ],
                "expansion_absolute_width_gain_min",
            ),
            expansion_efficiency_max=_decimal(
                basis["efficiency20_abs"][expansion["efficiency_max_quantile"]],
                "expansion_efficiency_max",
            ),
            expansion_traversal_max=_decimal(
                basis["range_traversal20"][expansion["traversal_max_quantile"]],
                "expansion_traversal_max",
            ),
            expansion_counter_excursion_min=_decimal(
                basis["counter_direction_excursion_share20"][
                    expansion["counter_excursion_min_quantile"]
                ],
                "expansion_counter_excursion_min",
            ),
            broad_range_width_min=_decimal(
                basis["range_width20"][broad["range_width_quantile"]],
                "broad_range_width_min",
            ),
            broad_efficiency_max=_decimal(
                basis["efficiency20_abs"][broad["efficiency_max_quantile"]],
                "broad_efficiency_max",
            ),
            broad_traversal_max=_decimal(
                basis["range_traversal20"][broad["traversal_max_quantile"]],
                "broad_traversal_max",
            ),
            broad_counter_excursion_min=_decimal(
                basis["counter_direction_excursion_share20"][
                    broad["counter_excursion_min_quantile"]
                ],
                "broad_counter_excursion_min",
            ),
        )
        output.append(
            QuantitativeInstabilityCandidate(
                expansion_profile_id=expansion["id"],
                broad_profile_id=broad["id"],
                thresholds=thresholds,
            )
        )
    candidates = tuple(output)
    if len(candidates) != config["candidate_count"] or len(
        {candidate.candidate_id for candidate in candidates}
    ) != len(candidates):
        raise PositiveInstabilityQuantitativeDevelopmentError("candidate_grid_identity")
    return candidates


def _measurements(case: Mapping[str, Any]) -> ProjectionMeasurements:
    features = case_features(case)
    return ProjectionMeasurements(
        range_width20=features["range_width20"],
        width_change20=features["width_change20"],
        efficiency20_abs=features["efficiency20_abs"],
        range_traversal20=features["range_traversal20"],
        counter_direction_excursion_share20=features[
            "counter_direction_excursion_share20"
        ],
    )


def candidate_instability(
    case: Mapping[str, Any], candidate: QuantitativeInstabilityCandidate
) -> str:
    return project_positive_instability(_measurements(case), candidate.thresholds).status


def candidate_stage0(case: Mapping[str, Any], candidate: QuantitativeInstabilityCandidate) -> bool:
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
    candidate: QuantitativeInstabilityCandidate | None,
) -> dict[str, Any]:
    tp = fp = fn = positive = negative = ambiguous = predictions = stage2_overlap = 0
    for case in cases:
        case_id = str(case.get("case_id"))
        reference = references.get(case_id)
        if reference is None:
            raise PositiveInstabilityQuantitativeDevelopmentError("reference_missing_for_case")
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
            raise PositiveInstabilityQuantitativeDevelopmentError(
                "unsupported_primary_reference"
            )
        if reference == "STAGE_0":
            positive += 1
            tp += int(prediction)
            fn += int(not prediction)
        else:
            negative += 1
            fp += int(prediction)
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
        "disposition": "CANDIDATE_FROZEN_FOR_PRESERVED_EVALUATION",
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

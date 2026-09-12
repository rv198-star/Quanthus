"""P3-STAGE-010-B deterministic quantitative projection contract.

This module projects already-governed continuous Structure Facts plus a separately versioned
threshold bundle into the approved P3-STAGE-009 positive-instability channels. It owns no
market-data reader, threshold search, Stage-2 logic, relocation tuning, or center-shift truth
authority. It also owns no runtime Job submission.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from quanthus.policy import find_project_root
from quanthus.structure_v2_inputs import digest

Truth = Literal["TRUE", "FALSE", "UNKNOWN"]
CONTRACT_PATH = "config/stage-learning/positive-instability-quantitative-projection.v1.json"
CONTRACT_DIGEST = "sha256:10dec4e9a63d1d1277dfd7e345d1f7b9c65cc27bee0bf05671a86c1277171cee"


class PositiveInstabilityProjectionError(ValueError):
    """The quantitative projection contract was violated."""


def load_contract(root: Path | None = None) -> dict[str, Any]:
    root = root or find_project_root()
    try:
        value = json.loads((root / CONTRACT_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PositiveInstabilityProjectionError("projection_contract_unreadable") from exc
    if digest(value) != CONTRACT_DIGEST:
        raise PositiveInstabilityProjectionError("projection_contract_drift")
    return value


def _decimal(value: Decimal | str | int | float, label: str) -> Decimal:
    try:
        observed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PositiveInstabilityProjectionError(f"invalid_decimal:{label}") from exc
    if not observed.is_finite():
        raise PositiveInstabilityProjectionError(f"nonfinite_decimal:{label}")
    return observed


def truth_and(*values: Truth) -> Truth:
    if not values:
        raise PositiveInstabilityProjectionError("and_requires_input")
    if "FALSE" in values:
        return "FALSE"
    return "TRUE" if all(value == "TRUE" for value in values) else "UNKNOWN"


def truth_or(*values: Truth) -> Truth:
    if not values:
        raise PositiveInstabilityProjectionError("or_requires_input")
    if "TRUE" in values:
        return "TRUE"
    return "FALSE" if all(value == "FALSE" for value in values) else "UNKNOWN"


def at_least_two_of_three(left: Truth, middle: Truth, right: Truth) -> Truth:
    values = (left, middle, right)
    true_count = sum(value == "TRUE" for value in values)
    unknown_count = sum(value == "UNKNOWN" for value in values)
    if true_count >= 2:
        return "TRUE"
    if true_count + unknown_count < 2:
        return "FALSE"
    return "UNKNOWN"


@dataclass(frozen=True)
class ProjectionMeasurements:
    range_width20: Decimal
    width_change20: Decimal
    efficiency20_abs: Decimal
    range_traversal20: Decimal
    counter_direction_excursion_share20: Decimal | None

    def __post_init__(self) -> None:
        for field in (
            "range_width20",
            "width_change20",
            "efficiency20_abs",
            "range_traversal20",
        ):
            object.__setattr__(self, field, _decimal(getattr(self, field), field))
        if self.counter_direction_excursion_share20 is not None:
            object.__setattr__(
                self,
                "counter_direction_excursion_share20",
                _decimal(
                    self.counter_direction_excursion_share20,
                    "counter_direction_excursion_share20",
                ),
            )
        if self.range_width20 < 0:
            raise PositiveInstabilityProjectionError("negative_range_width")
        if not Decimal("0") <= self.efficiency20_abs <= Decimal("1"):
            raise PositiveInstabilityProjectionError("efficiency_out_of_bounds")
        if not Decimal("0") <= self.range_traversal20 <= Decimal("1"):
            raise PositiveInstabilityProjectionError("traversal_out_of_bounds")
        counter = self.counter_direction_excursion_share20
        if counter is not None and not Decimal("0") <= counter <= Decimal("1"):
            raise PositiveInstabilityProjectionError("counter_excursion_out_of_bounds")


@dataclass(frozen=True)
class ProjectionThresholds:
    expansion_width_change_min: Decimal
    expansion_range_width_min: Decimal
    expansion_absolute_width_gain_min: Decimal
    expansion_efficiency_max: Decimal
    expansion_traversal_max: Decimal
    expansion_counter_excursion_min: Decimal
    broad_range_width_min: Decimal
    broad_efficiency_max: Decimal
    broad_traversal_max: Decimal
    broad_counter_excursion_min: Decimal

    def __post_init__(self) -> None:
        for field in self.__dataclass_fields__:
            object.__setattr__(self, field, _decimal(getattr(self, field), field))
        if self.expansion_width_change_min <= 0:
            raise PositiveInstabilityProjectionError("expansion_width_change_must_be_positive")
        for field in (
            "expansion_range_width_min",
            "expansion_absolute_width_gain_min",
            "broad_range_width_min",
        ):
            if getattr(self, field) < 0:
                raise PositiveInstabilityProjectionError(f"negative_threshold:{field}")
        for field in (
            "expansion_efficiency_max",
            "expansion_traversal_max",
            "expansion_counter_excursion_min",
            "broad_efficiency_max",
            "broad_traversal_max",
            "broad_counter_excursion_min",
        ):
            value = getattr(self, field)
            if not Decimal("0") <= value <= Decimal("1"):
                raise PositiveInstabilityProjectionError(f"bounded_threshold:{field}")


@dataclass(frozen=True)
class ProjectionAssessment:
    status: Truth
    expanding_disorder_20: Truth
    broad_reversal_disorder_20: Truth
    primitives: dict[str, Truth]
    derived: dict[str, str | None]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "quanthus.stage-positive-instability-quantitative-assessment.v1",
            "status": self.status,
            "channels": {
                "expanding_disorder_20": self.expanding_disorder_20,
                "broad_reversal_disorder_20": self.broad_reversal_disorder_20,
            },
            "primitives": dict(self.primitives),
            "derived": dict(self.derived),
            "center_shift_truth_authority": False,
            "opaque_score": None,
        }


def absolute_range_width_gain(range_width20: Decimal, width_change20: Decimal) -> Decimal | None:
    current = _decimal(range_width20, "range_width20")
    change = _decimal(width_change20, "width_change20")
    denominator = Decimal("1") + change
    if denominator <= 0:
        return None
    prior = current / denominator
    return current - prior


def _threshold_truth(
    value: Decimal | None,
    *,
    minimum: Decimal | None = None,
    maximum: Decimal | None = None,
) -> Truth:
    if value is None:
        return "UNKNOWN"
    if (minimum is None) == (maximum is None):
        raise PositiveInstabilityProjectionError("threshold_truth_requires_one_bound")
    if minimum is not None:
        return "TRUE" if value >= minimum else "FALSE"
    assert maximum is not None
    return "TRUE" if value <= maximum else "FALSE"


def project_positive_instability(
    measurements: ProjectionMeasurements,
    thresholds: ProjectionThresholds,
) -> ProjectionAssessment:
    gain = absolute_range_width_gain(measurements.range_width20, measurements.width_change20)
    counter = measurements.counter_direction_excursion_share20

    primitives: dict[str, Truth] = {
        "width_expansion_material": _threshold_truth(
            measurements.width_change20, minimum=thresholds.expansion_width_change_min
        ),
        "expansion_range_width_material": _threshold_truth(
            measurements.range_width20, minimum=thresholds.expansion_range_width_min
        ),
        "absolute_range_width_gain_material": _threshold_truth(
            gain, minimum=thresholds.expansion_absolute_width_gain_min
        ),
        "expansion_low_directional_efficiency": _threshold_truth(
            measurements.efficiency20_abs, maximum=thresholds.expansion_efficiency_max
        ),
        "expansion_low_range_traversal": _threshold_truth(
            measurements.range_traversal20, maximum=thresholds.expansion_traversal_max
        ),
        "expansion_high_counter_excursion": _threshold_truth(
            counter, minimum=thresholds.expansion_counter_excursion_min
        ),
        "broad_range_width_material": _threshold_truth(
            measurements.range_width20, minimum=thresholds.broad_range_width_min
        ),
        "broad_low_directional_efficiency": _threshold_truth(
            measurements.efficiency20_abs, maximum=thresholds.broad_efficiency_max
        ),
        "broad_low_range_traversal": _threshold_truth(
            measurements.range_traversal20, maximum=thresholds.broad_traversal_max
        ),
        "broad_high_counter_excursion": _threshold_truth(
            counter, minimum=thresholds.broad_counter_excursion_min
        ),
    }

    amplitude_or_growth = truth_or(
        primitives["expansion_range_width_material"],
        primitives["absolute_range_width_gain_material"],
    )
    any_path_disorder = truth_or(
        primitives["expansion_low_directional_efficiency"],
        primitives["expansion_low_range_traversal"],
        primitives["expansion_high_counter_excursion"],
    )
    expanding = truth_and(
        primitives["width_expansion_material"],
        amplitude_or_growth,
        any_path_disorder,
    )

    broad_path_disorder = at_least_two_of_three(
        primitives["broad_low_directional_efficiency"],
        primitives["broad_low_range_traversal"],
        primitives["broad_high_counter_excursion"],
    )
    broad = truth_and(primitives["broad_range_width_material"], broad_path_disorder)
    status = truth_or(expanding, broad)
    return ProjectionAssessment(
        status=status,
        expanding_disorder_20=expanding,
        broad_reversal_disorder_20=broad,
        primitives=primitives,
        derived={
            "absolute_range_width_gain": None if gain is None else str(gain),
            "amplitude_or_growth_material": amplitude_or_growth,
            "any_path_disorder": any_path_disorder,
            "broad_path_disorder_two_of_three": broad_path_disorder,
        },
    )

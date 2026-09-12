"""P3-STAGE-011-B deterministic orderly-consolidation projection.

The module projects already-governed primary-scale Structure measurements plus the separately
resolved P3-STAGE-008 relocation proposition into `orderly_consolidation_20`. It owns no market-data
reader, threshold search, Stage-0 candidate selection, relocation retuning, center-shift truth
authority, runtime Job submission, or preserved-evaluation access.
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
CONTRACT_PATH = "config/stage-learning/orderly-consolidation-projection.v1.json"
CONTRACT_DIGEST = "sha256:11569752a0a3ae7c5a6c3f2ecf07b51710442c0f2445a848800203df3cda146c"


class OrderlyConsolidationProjectionError(ValueError):
    """The P3-STAGE-011 orderly-consolidation projection contract was violated."""


def load_contract(root: Path | None = None) -> dict[str, Any]:
    root = root or find_project_root()
    try:
        value = json.loads((root / CONTRACT_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OrderlyConsolidationProjectionError("projection_contract_unreadable") from exc
    if digest(value) != CONTRACT_DIGEST:
        raise OrderlyConsolidationProjectionError("projection_contract_drift")
    return value


def _decimal(value: Decimal | str | int | float | None, label: str) -> Decimal | None:
    if value is None:
        return None
    try:
        observed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise OrderlyConsolidationProjectionError(f"invalid_decimal:{label}") from exc
    if not observed.is_finite():
        raise OrderlyConsolidationProjectionError(f"nonfinite_decimal:{label}")
    return observed


def _truth_and(*values: Truth) -> Truth:
    if not values:
        raise OrderlyConsolidationProjectionError("and_requires_input")
    if "FALSE" in values:
        return "FALSE"
    return "TRUE" if all(value == "TRUE" for value in values) else "UNKNOWN"


def _maximum_truth(value: Decimal | None, maximum: Decimal) -> Truth:
    if value is None:
        return "UNKNOWN"
    return "TRUE" if value <= maximum else "FALSE"


def _minimum_truth(value: Decimal | None, minimum: Decimal) -> Truth:
    if value is None:
        return "UNKNOWN"
    return "TRUE" if value >= minimum else "FALSE"


def contraction_magnitude20(width_change20: Decimal | None) -> Decimal | None:
    value = _decimal(width_change20, "width_change20")
    if value is None:
        return None
    return max(Decimal(0), -value)


@dataclass(frozen=True)
class OrderlyConsolidationMeasurements:
    efficiency20_abs: Decimal | None
    range_width20: Decimal | None
    width_change20: Decimal | None
    coherent_directional_relocation20: Truth

    def __post_init__(self) -> None:
        for field in ("efficiency20_abs", "range_width20", "width_change20"):
            object.__setattr__(self, field, _decimal(getattr(self, field), field))
        if self.efficiency20_abs is not None and not (
            Decimal(0) <= self.efficiency20_abs <= Decimal(1)
        ):
            raise OrderlyConsolidationProjectionError("efficiency_out_of_bounds")
        if self.range_width20 is not None and self.range_width20 < 0:
            raise OrderlyConsolidationProjectionError("negative_range_width")
        if self.coherent_directional_relocation20 not in {"TRUE", "FALSE", "UNKNOWN"}:
            raise OrderlyConsolidationProjectionError("relocation_truth_value")


@dataclass(frozen=True)
class OrderlyConsolidationThresholds:
    efficiency20_abs_max: Decimal
    range_width20_max: Decimal
    contraction_magnitude20_min: Decimal

    def __post_init__(self) -> None:
        for field in self.__dataclass_fields__:
            value = _decimal(getattr(self, field), field)
            assert value is not None
            object.__setattr__(self, field, value)
        if not Decimal(0) <= self.efficiency20_abs_max <= Decimal(1):
            raise OrderlyConsolidationProjectionError("efficiency_threshold_out_of_bounds")
        if self.range_width20_max < 0:
            raise OrderlyConsolidationProjectionError("negative_range_width_threshold")
        if self.contraction_magnitude20_min <= 0:
            raise OrderlyConsolidationProjectionError("contraction_threshold_must_be_positive")


@dataclass(frozen=True)
class OrderlyConsolidationAssessment:
    status: Truth
    primitives: dict[str, Truth]
    derived: dict[str, str | None]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "quanthus.stage-orderly-consolidation-assessment.v1",
            "status": self.status,
            "primitives": dict(self.primitives),
            "derived": dict(self.derived),
            "center_shift_truth_authority": False,
            "path_turnover_truth_authority": False,
            "relocation_guard_retuned": False,
            "opaque_score": None,
        }


def project_orderly_consolidation(
    measurements: OrderlyConsolidationMeasurements,
    thresholds: OrderlyConsolidationThresholds,
) -> OrderlyConsolidationAssessment:
    contraction = contraction_magnitude20(measurements.width_change20)
    relocation_negative: Truth = {
        "TRUE": "FALSE",
        "FALSE": "TRUE",
        "UNKNOWN": "UNKNOWN",
    }[measurements.coherent_directional_relocation20]
    primitives: dict[str, Truth] = {
        "low_directional_efficiency": _maximum_truth(
            measurements.efficiency20_abs, thresholds.efficiency20_abs_max
        ),
        "materially_narrow_current_range": _maximum_truth(
            measurements.range_width20, thresholds.range_width20_max
        ),
        "material_range_contraction": _minimum_truth(
            contraction, thresholds.contraction_magnitude20_min
        ),
        "negative_coherent_relocation": relocation_negative,
    }
    status = _truth_and(*primitives.values())
    return OrderlyConsolidationAssessment(
        status=status,
        primitives=primitives,
        derived={
            "contraction_magnitude20": None if contraction is None else str(contraction),
        },
    )

"""P3-STAGE-009-B qualitative positive-instability proposition contract.

This module intentionally contains no market-data provider, runtime Job submission, numerical
thresholds, parameter search, Stage-2 mutation, or relocation-guard tuning. Future versioned
threshold policies may project continuous Facts into the primitive TRUE/FALSE/UNKNOWN evidence
accepted here; this module only owns proposition composition and auditable reasons.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from quanthus.policy import find_project_root
from quanthus.structure_v2_inputs import digest

Truth = Literal["TRUE", "FALSE", "UNKNOWN"]
TRUTH_VALUES: tuple[Truth, ...] = ("TRUE", "FALSE", "UNKNOWN")
CONTRACT_PATH = "config/stage-learning/positive-instability-contract.v1.json"
CONTRACT_DIGEST = "sha256:2160cdb23ed43acb248bafd7bbdace20ff2dd936ab9c8ff5f3b3cda3d74cda7b"

PRIMITIVES = (
    "range_width_material_20",
    "width_expansion_material_20",
    "low_directional_efficiency_20",
    "low_range_traversal_20",
    "high_counter_direction_excursion_20",
)


class PositiveInstabilityError(ValueError):
    """The qualitative positive-instability contract was violated."""


def load_contract(root: Path | None = None) -> dict[str, Any]:
    root = root or find_project_root()
    try:
        value = json.loads((root / CONTRACT_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PositiveInstabilityError("positive_instability_contract_unreadable") from exc
    if digest(value) != CONTRACT_DIGEST:
        raise PositiveInstabilityError("positive_instability_contract_drift")
    return value


def _truth(value: object, field: str) -> Truth:
    if value not in TRUTH_VALUES:
        raise PositiveInstabilityError(f"invalid_truth:{field}")
    return value  # type: ignore[return-value]


def truth_and(*values: Truth) -> Truth:
    if not values:
        raise PositiveInstabilityError("and_requires_input")
    if "FALSE" in values:
        return "FALSE"
    if all(value == "TRUE" for value in values):
        return "TRUE"
    return "UNKNOWN"


def truth_or(*values: Truth) -> Truth:
    if not values:
        raise PositiveInstabilityError("or_requires_input")
    if "TRUE" in values:
        return "TRUE"
    if all(value == "FALSE" for value in values):
        return "FALSE"
    return "UNKNOWN"


@dataclass(frozen=True)
class PrimitiveEvidence:
    range_width_material_20: Truth
    width_expansion_material_20: Truth
    low_directional_efficiency_20: Truth
    low_range_traversal_20: Truth
    high_counter_direction_excursion_20: Truth

    def __post_init__(self) -> None:
        for field in PRIMITIVES:
            _truth(getattr(self, field), field)

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> PrimitiveEvidence:
        if set(value) != set(PRIMITIVES):
            raise PositiveInstabilityError("primitive_evidence_fields")
        return cls(**{field: _truth(value[field], field) for field in PRIMITIVES})

    def to_dict(self) -> dict[str, Truth]:
        return {field: getattr(self, field) for field in PRIMITIVES}


@dataclass(frozen=True)
class ChannelAssessment:
    name: str
    status: Truth
    inputs: Mapping[str, Truth]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "inputs": dict(self.inputs),
        }


@dataclass(frozen=True)
class PositiveInstabilityAssessment:
    status: Truth
    expanding_disorder_20: ChannelAssessment
    broad_reversal_disorder_20: ChannelAssessment
    primitive_evidence: PrimitiveEvidence

    @property
    def activated_positive_channels(self) -> tuple[str, ...]:
        return tuple(
            channel.name
            for channel in (self.expanding_disorder_20, self.broad_reversal_disorder_20)
            if channel.status == "TRUE"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "quanthus.stage-positive-instability-assessment.v1",
            "status": self.status,
            "channels": {
                self.expanding_disorder_20.name: self.expanding_disorder_20.to_dict(),
                self.broad_reversal_disorder_20.name: self.broad_reversal_disorder_20.to_dict(),
            },
            "primitive_evidence": self.primitive_evidence.to_dict(),
            "activated_positive_channels": list(self.activated_positive_channels),
            "opaque_score": None,
            "center_shift_truth_authority": False,
        }


def assess_positive_instability(evidence: PrimitiveEvidence) -> PositiveInstabilityAssessment:
    """Compose primitive qualitative evidence into the approved two-channel proposition."""
    expanding_inputs = {
        "range_width_material_20": evidence.range_width_material_20,
        "width_expansion_material_20": evidence.width_expansion_material_20,
        "low_directional_efficiency_20": evidence.low_directional_efficiency_20,
    }
    expanding = ChannelAssessment(
        "expanding_disorder_20",
        truth_and(*expanding_inputs.values()),
        expanding_inputs,
    )

    reversal_inputs = {
        "low_range_traversal_20": evidence.low_range_traversal_20,
        "high_counter_direction_excursion_20": evidence.high_counter_direction_excursion_20,
    }
    reversal_or_roundtrip = truth_or(*reversal_inputs.values())
    broad_inputs = {
        "range_width_material_20": evidence.range_width_material_20,
        "low_directional_efficiency_20": evidence.low_directional_efficiency_20,
        "reversal_or_roundtrip_20": reversal_or_roundtrip,
    }
    broad = ChannelAssessment(
        "broad_reversal_disorder_20",
        truth_and(*broad_inputs.values()),
        broad_inputs,
    )
    status = truth_or(expanding.status, broad.status)
    return PositiveInstabilityAssessment(status, expanding, broad, evidence)


def attach_modifiers(
    assessment: PositiveInstabilityAssessment,
    *,
    range_center_shift_20_vs_prior20: str | None,
) -> dict[str, Any]:
    """Attach descriptive context without allowing it to mutate proposition truth."""
    result = assessment.to_dict()
    result["modifiers"] = {
        "range_center_shift_20_vs_prior20": range_center_shift_20_vs_prior20,
        "truth_function_role": "none",
    }
    result["status_after_modifiers"] = assessment.status
    return result

"""Frozen role-specific prospective scorecard contracts for P6-SCORE-001."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from quanthus.forward_validation.contracts import ForwardValidationContractError, fail
from quanthus.forward_validation.serialization import digest_value

_SCHEMA = "quanthus.p6-role-scorecard-policy.v1"
_ENTRY_DIGEST = "sha256:3c9356e49dbdd6356754b42cfd34af11949289121454a05bdbf0c0ff2aebaa18"
_ROLES = (
    "aggregator",
    "complete_strategy",
    "paper_operation",
    "position_risk",
    "trend_assessment",
)


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _decimal(value: object, path: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ForwardValidationContractError("field_mismatch", path) from exc
    if not parsed.is_finite():
        fail("field_mismatch", f"{path} must be finite")
    return parsed


def _texts(value: object, path: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        fail("field_mismatch", f"{path} must be a non-empty array")
    output = tuple(str(item) for item in value)
    if any(not item or item.strip() != item for item in output) or len(set(output)) != len(output):
        fail("field_mismatch", f"{path} must contain unique canonical text")
    return output


@dataclass(frozen=True)
class RoleMetricPolicy:
    role: str
    source: str
    primary_metrics: tuple[str, ...]
    guardrails: tuple[str, ...]
    forbidden_substitutions: tuple[str, ...]


@dataclass(frozen=True)
class P6ScorecardPolicy:
    path: Path
    policy_sha256: str
    roles: Mapping[str, RoleMetricPolicy]
    common_slices: tuple[str, ...]
    material_loss_threshold_cny: Decimal
    material_loss_classes: tuple[str, ...]


@dataclass(frozen=True)
class ForwardRoleScorecard:
    role: str
    source_artifact_digest: str
    metric_values: Mapping[str, str]
    sample_count: int
    slices: Mapping[str, str]
    limitations: tuple[str, ...]

    @property
    def digest(self) -> str:
        return digest_value(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "quanthus.p6-forward-role-scorecard.v1",
            "role": self.role,
            "source_artifact_digest": self.source_artifact_digest,
            "metric_values": dict(sorted(self.metric_values.items())),
            "sample_count": self.sample_count,
            "slices": dict(sorted(self.slices.items())),
            "limitations": list(sorted(self.limitations)),
        }


def load_scorecard_policy(path: Path) -> P6ScorecardPolicy:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ForwardValidationContractError("invalid_json", str(path)) from exc
    if not isinstance(raw, dict):
        fail("field_mismatch", "scorecard policy must be an object")
    if (
        raw.get("schema_version") != _SCHEMA
        or raw.get("work_item") != "P6-SCORE-001"
        or raw.get("state") != "FROZEN_PRE_RESULT_NO_ACTIVATION"
        or raw.get("entry_proposal_digest") != _ENTRY_DIGEST
    ):
        fail("scorecard_policy_drift", "P6 scorecard policy identity differs")
    principles = raw.get("principles")
    if not isinstance(principles, dict) or principles != {
        "universal_expert_score_authorized": False,
        "profitability_primary_gate": False,
        "coverage_always_reported_with_quality": True,
        "dependence_aware_block_reporting_required": True,
        "material_loss_must_be_classified": True,
        "result_driven_metric_edit_authorized": False,
    }:
        fail("scorecard_policy_drift", "scorecard principles differ")
    material = raw.get("material_loss")
    if not isinstance(material, dict):
        fail("field_mismatch", "material_loss must be an object")
    unit = _decimal(material.get("fixed_unit_notional_cny"), "fixed_unit_notional_cny")
    fraction = _decimal(material.get("absolute_fraction_of_one_unit"), "absolute_fraction")
    threshold = _decimal(material.get("absolute_threshold_cny"), "absolute_threshold_cny")
    if unit != Decimal("100000") or fraction != Decimal("0.01") or threshold != unit * fraction:
        fail("material_loss_policy_drift", "material-loss threshold differs")
    if material.get("mechanism_integrity_breach_always_material") is not True:
        fail("material_loss_policy_drift", "mechanism integrity breach must always be material")
    classes = _texts(material.get("allowed_classes"), "material_loss.allowed_classes")
    if material.get("unclassified_material_loss_blocks_review") is not True:
        fail("material_loss_policy_drift", "unclassified material loss must block review")
    common_slices = _texts(raw.get("common_slices"), "common_slices")
    roles_raw = raw.get("roles")
    if not isinstance(roles_raw, dict) or tuple(sorted(roles_raw)) != _ROLES:
        fail("scorecard_role_drift", "P6 scorecard roles differ")
    roles: dict[str, RoleMetricPolicy] = {}
    for role in _ROLES:
        value = roles_raw[role]
        if not isinstance(value, dict):
            fail("field_mismatch", f"roles.{role} must be an object")
        roles[role] = RoleMetricPolicy(
            role=role,
            source=str(value.get("source", "")),
            primary_metrics=_texts(value.get("primary_metrics"), f"roles.{role}.primary_metrics"),
            guardrails=_texts(value.get("guardrails"), f"roles.{role}.guardrails"),
            forbidden_substitutions=_texts(
                value.get("forbidden_substitutions"), f"roles.{role}.forbidden_substitutions"
            ),
        )
        if not roles[role].source:
            fail("field_mismatch", f"roles.{role}.source is empty")
    authority = raw.get("authority")
    if not isinstance(authority, dict) or authority.get("p6_score_implementation_authorized") is not True:
        fail("authority_drift", "P6 scorecard implementation authority missing")
    if any(value is not False for key, value in authority.items() if key != "p6_score_implementation_authorized"):
        fail("authority_drift", "P6 scorecard policy opens downstream authority")
    return P6ScorecardPolicy(
        path=path,
        policy_sha256=_sha(path),
        roles=MappingProxyType(roles),
        common_slices=common_slices,
        material_loss_threshold_cny=threshold,
        material_loss_classes=classes,
    )


def build_role_scorecard(
    *,
    policy: P6ScorecardPolicy,
    role: str,
    source_artifact_digest: str,
    metric_values: Mapping[str, Decimal | int | str],
    sample_count: int,
    slices: Mapping[str, str],
    limitations: tuple[str, ...],
) -> ForwardRoleScorecard:
    if role not in policy.roles:
        fail("scorecard_role_unknown", f"unsupported role {role}")
    if not source_artifact_digest.startswith("sha256:") or len(source_artifact_digest) != 71:
        fail("invalid_digest", "source_artifact_digest must be sha256")
    if type(sample_count) is not int or sample_count < 0:
        fail("field_mismatch", "sample_count must be non-negative integer")
    role_policy = policy.roles[role]
    allowed = set(role_policy.primary_metrics) | set(role_policy.guardrails)
    supplied = set(metric_values)
    if not set(role_policy.primary_metrics).issubset(supplied):
        fail("scorecard_primary_metric_missing", f"{role} primary metrics are incomplete")
    if not supplied.issubset(allowed):
        fail("scorecard_metric_not_registered", f"{role} contains unregistered metrics")
    if not set(slices).issubset(policy.common_slices):
        fail("scorecard_slice_not_registered", f"{role} contains unregistered slices")
    if any(not item or item.strip() != item for item in limitations):
        fail("field_mismatch", "limitations must be canonical text")
    normalized: dict[str, str] = {}
    for name, value in metric_values.items():
        if isinstance(value, Decimal):
            if not value.is_finite():
                fail("field_mismatch", f"metric {name} is not finite")
            normalized[name] = format(value, "f")
        elif isinstance(value, int) and not isinstance(value, bool):
            normalized[name] = str(value)
        elif isinstance(value, str) and value.strip() == value and value:
            normalized[name] = value
        else:
            fail("field_mismatch", f"metric {name} has unsupported value")
    return ForwardRoleScorecard(
        role=role,
        source_artifact_digest=source_artifact_digest,
        metric_values=MappingProxyType(normalized),
        sample_count=sample_count,
        slices=MappingProxyType(dict(slices)),
        limitations=tuple(limitations),
    )

"""P3 entry concentration checks bound to the frozen P0 risk policy."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any

from quanthus.policy import P0Policy
from quanthus.position_lifecycle import PositionStage

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_POSITIONED_STAGES = {
    PositionStage.TRIAL,
    PositionStage.DYNAMIC,
    PositionStage.EXITING,
}


class ConcentrationError(ValueError):
    """Raised when concentration evidence cannot produce a safe decision."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}")


class IndustryMappingUsage(str, Enum):
    STATIC_CONCENTRATION_ONLY = "static_non_pit_concentration_only"


def _require_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ConcentrationError("invalid_identity", f"{field_name} must not be empty")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


@dataclass(frozen=True)
class StaticIndustryMembership:
    security_code: str
    level1_industry_id: str
    mapping_version: str
    source_revision: str
    lineage_ref: str
    usage: IndustryMappingUsage = IndustryMappingUsage.STATIC_CONCENTRATION_ONLY

    def __post_init__(self) -> None:
        for field_name in (
            "security_code",
            "level1_industry_id",
            "mapping_version",
            "source_revision",
            "lineage_ref",
        ):
            _require_text(getattr(self, field_name), field_name)
        if self.usage is not IndustryMappingUsage.STATIC_CONCENTRATION_ONLY:
            raise ConcentrationError(
                "invalid_industry_usage",
                "static industry membership is allowed only for concentration",
            )

    @property
    def key(self) -> tuple[str, str, str, str, str, str]:
        return (
            self.security_code,
            self.level1_industry_id,
            self.mapping_version,
            self.source_revision,
            self.lineage_ref,
            self.usage.value,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "security_code": self.security_code,
            "level1_industry_id": self.level1_industry_id,
            "mapping_version": self.mapping_version,
            "source_revision": self.source_revision,
            "lineage_ref": self.lineage_ref,
            "usage": self.usage.value,
        }


@dataclass(frozen=True)
class ActiveUnitExposure:
    unit_id: str
    opportunity_id: str
    lifecycle_stage: PositionStage
    lifecycle_digest: str
    membership: StaticIndustryMembership

    def __post_init__(self) -> None:
        _require_text(self.unit_id, "unit_id")
        _require_text(self.opportunity_id, "opportunity_id")
        if self.lifecycle_stage not in _POSITIONED_STAGES:
            raise ConcentrationError(
                "invalid_active_exposure", "active exposure requires a positioned lifecycle stage"
            )
        if not _SHA256.fullmatch(self.lifecycle_digest):
            raise ConcentrationError("invalid_active_exposure", "lifecycle_digest must use sha256")

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.unit_id, self.opportunity_id, self.membership.security_code)


@dataclass(frozen=True)
class DailyEntryReservation:
    reservation_id: str
    decision_id: str
    market_date: date
    unit_id: str
    opportunity_id: str
    security_code: str
    level1_industry_id: str
    industry_mapping_version: str

    def __post_init__(self) -> None:
        for field_name in (
            "reservation_id",
            "decision_id",
            "unit_id",
            "opportunity_id",
            "security_code",
            "level1_industry_id",
            "industry_mapping_version",
        ):
            _require_text(getattr(self, field_name), field_name)

    @property
    def key(self) -> tuple[str, str, str, str, str, str]:
        return (
            self.reservation_id,
            self.unit_id,
            self.opportunity_id,
            self.security_code,
            self.level1_industry_id,
            self.industry_mapping_version,
        )


@dataclass(frozen=True)
class EntryCapacityCandidate:
    unit_id: str
    opportunity_id: str
    membership: StaticIndustryMembership

    def __post_init__(self) -> None:
        _require_text(self.unit_id, "unit_id")
        _require_text(self.opportunity_id, "opportunity_id")


@dataclass(frozen=True)
class EntryCapacityDecision:
    policy_version: str
    policy_digest: str
    market_date: date
    unit_id: str
    opportunity_id: str
    security_code: str
    level1_industry_id: str
    industry_mapping_version: str
    max_units_per_level1_industry: int
    max_new_units_per_day: int
    active_industry_units_before: int
    reserved_industry_units_before: int
    new_unit_reservations_before: int
    considered_active_unit_ids: tuple[str, ...]
    considered_reservation_ids: tuple[str, ...]
    allowed: bool
    reason_codes: tuple[str, ...]

    @property
    def execution_authorized(self) -> bool:
        return False

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "policy_digest": self.policy_digest,
            "market_date": self.market_date.isoformat(),
            "unit_id": self.unit_id,
            "opportunity_id": self.opportunity_id,
            "security_code": self.security_code,
            "level1_industry_id": self.level1_industry_id,
            "industry_mapping_version": self.industry_mapping_version,
            "max_units_per_level1_industry": self.max_units_per_level1_industry,
            "max_new_units_per_day": self.max_new_units_per_day,
            "active_industry_units_before": self.active_industry_units_before,
            "reserved_industry_units_before": self.reserved_industry_units_before,
            "new_unit_reservations_before": self.new_unit_reservations_before,
            "considered_active_unit_ids": list(self.considered_active_unit_ids),
            "considered_reservation_ids": list(self.considered_reservation_ids),
            "allowed": self.allowed,
            "reason_codes": list(self.reason_codes),
            "execution_authorized": self.execution_authorized,
        }


def _policy_limit(policy: P0Policy, name: str) -> int:
    value = policy.at("risk", name)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConcentrationError("invalid_risk_policy", f"risk.{name} must be a positive integer")
    return value


def _validate_policy(policy: P0Policy) -> tuple[int, int]:
    if policy.at("data", "level1_industry_mapping") != "static_for_concentration_only":
        raise ConcentrationError(
            "invalid_industry_policy",
            "P0 policy must restrict static level-one industry mapping to concentration",
        )
    industry_limit = _policy_limit(policy, "max_units_per_level1_industry")
    daily_limit = _policy_limit(policy, "max_new_units_per_day")
    return industry_limit, daily_limit


def _require_unique(values: Sequence[str], kind: str) -> None:
    if len(values) != len(set(values)):
        raise ConcentrationError("duplicate_capacity_evidence", f"duplicate {kind} identity")


def evaluate_entry_capacity(
    policy: P0Policy,
    candidate: EntryCapacityCandidate,
    *,
    market_date: date,
    active_exposures: Sequence[ActiveUnitExposure],
    accepted_entries: Sequence[DailyEntryReservation],
) -> EntryCapacityDecision:
    """Apply frozen industry/day caps without reserving cash or creating execution artifacts."""

    industry_limit, daily_limit = _validate_policy(policy)
    ordered_exposures = tuple(sorted(active_exposures, key=lambda item: item.key))
    ordered_entries = tuple(sorted(accepted_entries, key=lambda item: item.key))
    _require_unique([item.unit_id for item in ordered_exposures], "active unit")
    _require_unique([item.reservation_id for item in ordered_entries], "reservation")
    _require_unique([item.unit_id for item in ordered_entries], "reserved unit")
    if {item.unit_id for item in ordered_exposures}.intersection(
        item.unit_id for item in ordered_entries
    ):
        raise ConcentrationError(
            "unit_evidence_conflict", "one unit cannot be both active and entry-reserved"
        )
    if candidate.unit_id in {item.unit_id for item in ordered_exposures}:
        raise ConcentrationError("unit_not_empty", "candidate unit already has an active position")
    if candidate.unit_id in {item.unit_id for item in ordered_entries}:
        raise ConcentrationError(
            "unit_already_reserved", "candidate unit already has an accepted entry reservation"
        )
    if any(item.market_date != market_date for item in ordered_entries):
        raise ConcentrationError(
            "market_date_mismatch", "accepted entry evidence must belong to the evaluated date"
        )
    mapping_versions = {
        candidate.membership.mapping_version,
        *(item.membership.mapping_version for item in ordered_exposures),
        *(item.industry_mapping_version for item in ordered_entries),
    }
    if len(mapping_versions) != 1:
        raise ConcentrationError(
            "industry_mapping_mismatch", "one capacity decision cannot mix industry map versions"
        )

    active_industry_units = sum(
        item.membership.level1_industry_id == candidate.membership.level1_industry_id
        for item in ordered_exposures
    )
    reserved_industry_units = sum(
        item.level1_industry_id == candidate.membership.level1_industry_id
        for item in ordered_entries
    )
    reasons: list[str] = []
    if active_industry_units + reserved_industry_units >= industry_limit:
        reasons.append("industry_cap")
    if len(ordered_entries) >= daily_limit:
        reasons.append("daily_open_cap")
    allowed = not reasons
    return EntryCapacityDecision(
        policy_version=policy.version,
        policy_digest=f"sha256:{policy.digest}",
        market_date=market_date,
        unit_id=candidate.unit_id,
        opportunity_id=candidate.opportunity_id,
        security_code=candidate.membership.security_code,
        level1_industry_id=candidate.membership.level1_industry_id,
        industry_mapping_version=candidate.membership.mapping_version,
        max_units_per_level1_industry=industry_limit,
        max_new_units_per_day=daily_limit,
        active_industry_units_before=active_industry_units,
        reserved_industry_units_before=reserved_industry_units,
        new_unit_reservations_before=len(ordered_entries),
        considered_active_unit_ids=tuple(item.unit_id for item in ordered_exposures),
        considered_reservation_ids=tuple(item.reservation_id for item in ordered_entries),
        allowed=allowed,
        reason_codes=("entry_capacity_available",) if allowed else tuple(reasons),
    )

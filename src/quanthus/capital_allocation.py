"""Deterministic P3 candidate competition for explicitly available capital units."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any

from quanthus.backtest.contracts import UnitState, decimal_text
from quanthus.concentration import (
    ActiveUnitExposure,
    DailyEntryReservation,
    EntryCapacityCandidate,
    EntryCapacityDecision,
    StaticIndustryMembership,
    evaluate_entry_capacity,
)
from quanthus.policy import P0Policy
from quanthus.position_lifecycle import PositionLifecycleState, PositionStage


class CapitalAllocationError(ValueError):
    """Raised when candidate or unit evidence cannot be allocated safely."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}")


class AllocationDisposition(str, Enum):
    RESERVED = "reserved"
    REJECTED = "rejected"


def _require_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise CapitalAllocationError("invalid_identity", f"{field_name} must not be empty")


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
class AvailableCapitalUnit:
    lifecycle: PositionLifecycleState
    ledger: UnitState

    def __post_init__(self) -> None:
        if self.lifecycle.unit_id != self.ledger.unit_id:
            raise CapitalAllocationError(
                "unit_identity_mismatch", "lifecycle and ledger unit ids must match"
            )
        if self.lifecycle.stage is not PositionStage.EMPTY:
            raise CapitalAllocationError(
                "unit_not_available", "only an empty lifecycle may compete for a new entry"
            )
        if self.ledger.settled_quantity != 0 or self.ledger.security_code is not None:
            raise CapitalAllocationError(
                "unit_not_available", "an available unit must have an empty P2 ledger"
            )

    @property
    def unit_id(self) -> str:
        return self.lifecycle.unit_id

    @property
    def key(self) -> tuple[str, str]:
        return (self.unit_id, self.lifecycle.digest)


@dataclass(frozen=True)
class EntryAllocationCandidate:
    decision_id: str
    opportunity_id: str
    aggregate_score: Decimal
    membership: StaticIndustryMembership

    def __post_init__(self) -> None:
        _require_text(self.decision_id, "decision_id")
        _require_text(self.opportunity_id, "opportunity_id")
        if not self.aggregate_score.is_finite():
            raise CapitalAllocationError(
                "invalid_candidate_score", "aggregate_score must be finite"
            )

    @property
    def priority_key(self) -> tuple[Decimal, str, str, str]:
        return (
            -self.aggregate_score,
            self.membership.security_code,
            self.opportunity_id,
            self.decision_id,
        )


@dataclass(frozen=True)
class EntryAllocationDecision:
    decision_id: str
    opportunity_id: str
    security_code: str
    aggregate_score: Decimal
    priority_rank: int
    considered_unit_id: str | None
    disposition: AllocationDisposition
    reason_codes: tuple[str, ...]
    capacity: EntryCapacityDecision | None
    reservation_id: str | None

    @property
    def execution_authorized(self) -> bool:
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "opportunity_id": self.opportunity_id,
            "security_code": self.security_code,
            "aggregate_score": decimal_text(self.aggregate_score),
            "priority_rank": self.priority_rank,
            "considered_unit_id": self.considered_unit_id,
            "disposition": self.disposition.value,
            "reason_codes": list(self.reason_codes),
            "capacity": None if self.capacity is None else self.capacity.to_dict(),
            "reservation_id": self.reservation_id,
            "execution_authorized": self.execution_authorized,
        }


@dataclass(frozen=True)
class EntryAllocationBatch:
    market_date: date
    policy_version: str
    policy_digest: str
    candidate_order: tuple[str, ...]
    decisions: tuple[EntryAllocationDecision, ...]
    new_reservations: tuple[DailyEntryReservation, ...]

    @property
    def execution_authorized(self) -> bool:
        return False

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_date": self.market_date.isoformat(),
            "policy_version": self.policy_version,
            "policy_digest": self.policy_digest,
            "candidate_order": list(self.candidate_order),
            "decisions": [decision.to_dict() for decision in self.decisions],
            "new_reservations": [
                {
                    "reservation_id": reservation.reservation_id,
                    "decision_id": reservation.decision_id,
                    "market_date": reservation.market_date.isoformat(),
                    "unit_id": reservation.unit_id,
                    "opportunity_id": reservation.opportunity_id,
                    "security_code": reservation.security_code,
                    "level1_industry_id": reservation.level1_industry_id,
                    "industry_mapping_version": reservation.industry_mapping_version,
                }
                for reservation in self.new_reservations
            ],
            "execution_authorized": self.execution_authorized,
        }


def _require_unique(values: Sequence[str], kind: str) -> None:
    if len(values) != len(set(values)):
        raise CapitalAllocationError("duplicate_allocation_evidence", f"duplicate {kind}")


def allocate_entry_reservations(
    policy: P0Policy,
    *,
    market_date: date,
    candidates: Sequence[EntryAllocationCandidate],
    available_units: Sequence[AvailableCapitalUnit],
    active_exposures: Sequence[ActiveUnitExposure],
    accepted_entries: Sequence[DailyEntryReservation],
) -> EntryAllocationBatch:
    """Reserve empty units under frozen ordering and concentration limits."""

    ordered_candidates = tuple(sorted(candidates, key=lambda item: item.priority_key))
    ordered_units = list(sorted(available_units, key=lambda item: item.key))
    ordered_exposures = tuple(sorted(active_exposures, key=lambda item: item.key))
    reservations = list(sorted(accepted_entries, key=lambda item: item.key))

    _require_unique([item.decision_id for item in ordered_candidates], "candidate decision id")
    _require_unique([item.opportunity_id for item in ordered_candidates], "candidate opportunity")
    _require_unique(
        [item.membership.security_code for item in ordered_candidates],
        "candidate security",
    )
    _require_unique([item.unit_id for item in ordered_units], "available unit")
    _require_unique([item.unit_id for item in ordered_exposures], "active unit")
    _require_unique([item.reservation_id for item in reservations], "reservation id")
    _require_unique([item.unit_id for item in reservations], "reserved unit")
    active_unit_ids = {item.unit_id for item in ordered_exposures}
    reserved_unit_ids = {item.unit_id for item in reservations}
    if active_unit_ids.intersection(reserved_unit_ids):
        raise CapitalAllocationError(
            "unit_evidence_conflict", "one unit cannot be both active and entry-reserved"
        )
    if any(item.market_date != market_date for item in reservations):
        raise CapitalAllocationError(
            "market_date_mismatch", "accepted entries must match the allocation market date"
        )

    unavailable_ids = active_unit_ids | reserved_unit_ids
    if unavailable_ids.intersection(item.unit_id for item in ordered_units):
        raise CapitalAllocationError(
            "unit_not_available", "available units cannot also be active or reserved"
        )

    mapping_versions = {
        *(item.membership.mapping_version for item in ordered_candidates),
        *(item.membership.mapping_version for item in ordered_exposures),
        *(item.industry_mapping_version for item in reservations),
    }
    if len(mapping_versions) > 1:
        raise CapitalAllocationError(
            "industry_mapping_mismatch", "one allocation batch cannot mix mapping versions"
        )

    decisions: list[EntryAllocationDecision] = []
    new_reservations: list[DailyEntryReservation] = []
    for priority_rank, candidate in enumerate(ordered_candidates, start=1):
        if not ordered_units:
            decisions.append(
                EntryAllocationDecision(
                    decision_id=candidate.decision_id,
                    opportunity_id=candidate.opportunity_id,
                    security_code=candidate.membership.security_code,
                    aggregate_score=candidate.aggregate_score,
                    priority_rank=priority_rank,
                    considered_unit_id=None,
                    disposition=AllocationDisposition.REJECTED,
                    reason_codes=("no_empty_unit",),
                    capacity=None,
                    reservation_id=None,
                )
            )
            continue

        unit = ordered_units[0]
        capacity = evaluate_entry_capacity(
            policy,
            EntryCapacityCandidate(
                unit_id=unit.unit_id,
                opportunity_id=candidate.opportunity_id,
                membership=candidate.membership,
            ),
            market_date=market_date,
            active_exposures=ordered_exposures,
            accepted_entries=reservations,
        )
        if not capacity.allowed:
            decisions.append(
                EntryAllocationDecision(
                    decision_id=candidate.decision_id,
                    opportunity_id=candidate.opportunity_id,
                    security_code=candidate.membership.security_code,
                    aggregate_score=candidate.aggregate_score,
                    priority_rank=priority_rank,
                    considered_unit_id=unit.unit_id,
                    disposition=AllocationDisposition.REJECTED,
                    reason_codes=capacity.reason_codes,
                    capacity=capacity,
                    reservation_id=None,
                )
            )
            continue

        reservation_id = (
            "reservation_"
            + _digest([market_date.isoformat(), candidate.decision_id, unit.unit_id]).removeprefix(
                "sha256:"
            )[:24]
        )
        reservation = DailyEntryReservation(
            reservation_id=reservation_id,
            decision_id=candidate.decision_id,
            market_date=market_date,
            unit_id=unit.unit_id,
            opportunity_id=candidate.opportunity_id,
            security_code=candidate.membership.security_code,
            level1_industry_id=candidate.membership.level1_industry_id,
            industry_mapping_version=candidate.membership.mapping_version,
        )
        reservations.append(reservation)
        reservations.sort(key=lambda item: item.key)
        new_reservations.append(reservation)
        ordered_units.pop(0)
        decisions.append(
            EntryAllocationDecision(
                decision_id=candidate.decision_id,
                opportunity_id=candidate.opportunity_id,
                security_code=candidate.membership.security_code,
                aggregate_score=candidate.aggregate_score,
                priority_rank=priority_rank,
                considered_unit_id=unit.unit_id,
                disposition=AllocationDisposition.RESERVED,
                reason_codes=("entry_unit_reserved",),
                capacity=capacity,
                reservation_id=reservation_id,
            )
        )

    return EntryAllocationBatch(
        market_date=market_date,
        policy_version=policy.version,
        policy_digest=f"sha256:{policy.digest}",
        candidate_order=tuple(item.decision_id for item in ordered_candidates),
        decisions=tuple(decisions),
        new_reservations=tuple(new_reservations),
    )

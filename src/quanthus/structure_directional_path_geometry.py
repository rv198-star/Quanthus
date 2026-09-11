"""Threshold-free Directional Path Geometry Facts for bounded Structure research.

This family measures endpoint traversal and the largest excursion against the endpoint direction.
It emits no Stage proposition, chooses no threshold and owns no market-data acquisition route.
"""
from __future__ import annotations

import json
from collections import defaultdict, deque
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Context, Decimal, localcontext
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.structure_v2_inputs import Admission, DailyInput, digest
from quanthus.temporal_contracts import BusinessTime, ObservationRevision

RANGE_TRAVERSAL = "structure.geometry.range-traversal"
COUNTER_DIRECTION_EXCURSION_SHARE = "structure.geometry.counter-direction-excursion-share"
CONCEPTS = (RANGE_TRAVERSAL, COUNTER_DIRECTION_EXCURSION_SHARE)
WINDOW_SESSIONS = 20
REQUIRED_ROWS = WINDOW_SESSIONS + 1
CONTRACT_PATH = "config/structure-directional-path-geometry.v1.json"
CONTRACT_DIGEST = "sha256:d944a1c503b0d6bb71dfae247c94378361f1a4100174a6da93c944cc2cc1030c"


class DirectionalPathGeometryError(ValueError):
    """Raised when Directional Path Geometry construction violates its causal contract."""


def load_contract(root: Path | None = None) -> dict[str, Any]:
    root = root or find_project_root()
    try:
        value = json.loads((root / CONTRACT_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DirectionalPathGeometryError("directional path contract unreadable") from exc
    if digest(value) != CONTRACT_DIGEST:
        raise DirectionalPathGeometryError("directional path contract drift")
    return value


def _closes(rows: Sequence[DailyInput]) -> list[Decimal]:
    values = [item.close for item in rows]
    if len(values) != REQUIRED_ROWS or any(value is None for value in values):
        raise DirectionalPathGeometryError("directional path geometry requires 21 admitted closes")
    return [value for value in values if value is not None]


def range_traversal(rows: Sequence[DailyInput]) -> Decimal:
    """Return endpoint displacement divided by the observed close range, in [0, 1]."""
    closes = _closes(rows)
    observed_range = max(closes) - min(closes)
    if observed_range == 0:
        return Decimal(0)
    with localcontext(Context(prec=28)):
        value = abs(closes[-1] - closes[0]) / observed_range
    if not Decimal(0) <= value <= Decimal(1):
        raise DirectionalPathGeometryError("range traversal escaped closed unit interval")
    return value


def counter_direction_excursion_share(rows: Sequence[DailyInput]) -> Decimal | None:
    """Return the largest path excursion against endpoint direction as close-range share."""
    closes = _closes(rows)
    net = closes[-1] - closes[0]
    if net == 0:
        return None
    observed_range = max(closes) - min(closes)
    if observed_range <= 0:
        raise DirectionalPathGeometryError("nonzero endpoint displacement requires positive range")
    adverse = Decimal(0)
    if net > 0:
        running = closes[0]
        for value in closes[1:]:
            running = max(running, value)
            adverse = max(adverse, running - value)
    else:
        running = closes[0]
        for value in closes[1:]:
            running = min(running, value)
            adverse = max(adverse, value - running)
    with localcontext(Context(prec=28)):
        result = adverse / observed_range
    if not Decimal(0) <= result <= Decimal(1):
        raise DirectionalPathGeometryError(
            "counter-direction excursion escaped closed unit interval"
        )
    return result


def _spec(concept_id: str) -> dict[str, Any]:
    formulas = {
        RANGE_TRAVERSAL: (
            "abs(close_t-close_t_minus_20)/"
            "(max_close_t_minus_20_through_t-min_close_t_minus_20_through_t)"
        ),
        COUNTER_DIRECTION_EXCURSION_SHARE: (
            "positive_net:max(running_max_close_to_s-close_s)/close_range_20;"
            "negative_net:max(close_s-running_min_close_to_s)/close_range_20"
        ),
    }
    if concept_id not in formulas:
        raise DirectionalPathGeometryError("unregistered Directional Path Geometry concept")
    return {
        "schema_version": "quanthus.directional-path-geometry-fact-spec.v1",
        "concept_id": concept_id,
        "concept_version": "1.0.0",
        "formula": formulas[concept_id],
        "window_sessions": WINDOW_SESSIONS,
        "required_rows": REQUIRED_ROWS,
        "current_window_includes_t": True,
        "coordinate_policy": "same-listing-same-coordinate-contiguous-final-daily",
        "numeric_policy": "decimal-context28",
        "stage_thresholds_in_fact_identity": False,
    }


@dataclass(frozen=True)
class DirectionalPathMeasurement:
    security_code: str
    security_id: str
    listing_id: str
    market_date: date
    concept_id: str
    window_sessions: int
    coordinate_ref: str
    value: Decimal | None
    availability_reason: str
    spec_digest: str
    source_closure_digest: str
    business_time: BusinessTime

    @property
    def identity(self) -> ObservationRevision:
        subject = digest(
            {
                "security_id": self.security_id,
                "listing_id": self.listing_id,
                "concept_id": self.concept_id,
                "window_sessions": self.window_sessions,
            }
        )
        return ObservationRevision(
            subject,
            self.listing_id,
            self.concept_id,
            "1.0.0",
            self.spec_digest,
            self.coordinate_ref,
            self.business_time,
            self.source_closure_digest,
            digest({"value": self.value, "availability_reason": self.availability_reason}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_key": self.identity.observation_key,
            "revision_id": self.identity.revision_id,
            **asdict(self),
        }


@dataclass(frozen=True)
class DirectionalPathAvailability:
    security_code: str
    market_date: date
    available: bool
    reason: str
    measurement_revision_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DirectionalPathGeometryFacts:
    measurements: tuple[DirectionalPathMeasurement, ...]
    availability: tuple[DirectionalPathAvailability, ...]
    reset_counts: dict[str, int]

    def semantic(self) -> dict[str, Any]:
        return {
            "measurements": [item.to_dict() for item in self.measurements],
            "availability": [item.to_dict() for item in self.availability],
            "reset_counts": dict(sorted(self.reset_counts.items())),
        }

    @property
    def digest(self) -> str:
        return digest(self.semantic())


def _measurement(
    row: DailyInput,
    source_rows: Sequence[DailyInput],
    concept_id: str,
    value: Decimal | None,
    *,
    availability_reason: str = "available",
) -> DirectionalPathMeasurement:
    return DirectionalPathMeasurement(
        row.security_code,
        row.security_id,
        row.listing_id,
        row.market_date,
        concept_id,
        WINDOW_SESSIONS,
        row.coordinate_ref,
        value,
        availability_reason,
        digest(_spec(concept_id)),
        digest([item.member_digest for item in source_rows]),
        BusinessTime("measurement", row.result_as_of),
    )


def _current_measurements(rows: Sequence[DailyInput]) -> tuple[DirectionalPathMeasurement, ...]:
    if len(rows) < REQUIRED_ROWS:
        raise DirectionalPathGeometryError("Directional Path Geometry requires 21 contiguous rows")
    rows = rows[-REQUIRED_ROWS:]
    current = rows[-1]
    counter = counter_direction_excursion_share(rows)
    return (
        _measurement(current, rows, RANGE_TRAVERSAL, range_traversal(rows)),
        _measurement(
            current,
            rows,
            COUNTER_DIRECTION_EXCURSION_SHARE,
            counter,
            availability_reason="available" if counter is not None else "zero_net_direction",
        ),
    )


def build_directional_path_geometry_facts(
    admissions: Sequence[Admission],
    *,
    report_start: date,
    report_end: date,
) -> DirectionalPathGeometryFacts:
    """Build path geometry from exact contiguous admitted final-daily observations."""
    if report_end < report_start:
        raise DirectionalPathGeometryError("invalid Directional Path Geometry report window")
    segments: dict[str, deque[DailyInput]] = defaultdict(lambda: deque(maxlen=REQUIRED_ROWS))
    measurements: list[DirectionalPathMeasurement] = []
    availability: list[DirectionalPathAvailability] = []
    resets = {
        "nonadmitted": 0,
        "listing_or_coordinate_change": 0,
        "hidden_session_gap": 0,
        "available": 0,
        "insufficient_history": 0,
    }
    ordered = sorted(admissions, key=lambda item: (item.row.market_date, item.row.security_code))
    for attempt in ordered:
        row = attempt.row
        segment = segments[row.security_code]
        previous = segment[-1] if segment else None
        in_report = report_start <= row.market_date <= report_end
        if not attempt.admitted or row.close is None:
            if segment:
                resets["nonadmitted"] += 1
            segment.clear()
            if in_report:
                availability.append(
                    DirectionalPathAvailability(
                        row.security_code,
                        row.market_date,
                        False,
                        attempt.reason,
                        (),
                    )
                )
            continue
        if previous is not None and (
            previous.listing_id != row.listing_id or previous.coordinate_ref != row.coordinate_ref
        ):
            resets["listing_or_coordinate_change"] += 1
            segment.clear()
            previous = None
        if previous is not None and row.session_index != previous.session_index + 1:
            resets["hidden_session_gap"] += 1
            segment.clear()
        segment.append(row)
        if not in_report:
            continue
        if len(segment) < REQUIRED_ROWS:
            resets["insufficient_history"] += 1
            availability.append(
                DirectionalPathAvailability(
                    row.security_code,
                    row.market_date,
                    False,
                    "insufficient_contiguous_history",
                    (),
                )
            )
            continue
        current = _current_measurements(tuple(segment))
        measurements.extend(current)
        availability.append(
            DirectionalPathAvailability(
                row.security_code,
                row.market_date,
                True,
                "available",
                tuple(item.identity.revision_id for item in current),
            )
        )
        resets["available"] += 1
    return DirectionalPathGeometryFacts(
        tuple(
            sorted(
                measurements,
                key=lambda item: (
                    item.security_code,
                    item.market_date,
                    item.concept_id,
                    item.window_sessions,
                ),
            )
        ),
        tuple(sorted(availability, key=lambda item: (item.security_code, item.market_date))),
        resets,
    )


def directional_path_geometry_counts(facts: DirectionalPathGeometryFacts) -> dict[str, Any]:
    by_concept: dict[str, int] = defaultdict(int)
    unavailable = 0
    for item in facts.measurements:
        by_concept[item.concept_id] += 1
        unavailable += item.value is None
    return {
        "measurement_count": len(facts.measurements),
        "available_observation_count": sum(item.available for item in facts.availability),
        "unavailable_observation_count": sum(not item.available for item in facts.availability),
        "unavailable_measurement_count": unavailable,
        "concept_counts": dict(sorted(by_concept.items())),
        "reset_counts": dict(sorted(facts.reset_counts.items())),
    }

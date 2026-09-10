"""Formal continuous Structure Order Facts for bounded retrospective research.

The family describes rolling range width, width evolution and range-center movement. It emits no
Stage 0/2 proposition, no order/disorder classification and no parameter threshold.
"""
from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Context, Decimal, localcontext
from typing import Any

from quanthus.structure_v2_inputs import Admission, DailyInput, digest
from quanthus.temporal_contracts import BusinessTime, ObservationRevision

RANGE_WIDTH = "structure.geometry.range-width"
WIDTH_CHANGE = "structure.geometry.width-change"
RANGE_CENTER_SHIFT = "structure.geometry.range-center-shift"
CONCEPTS = (RANGE_WIDTH, WIDTH_CHANGE, RANGE_CENTER_SHIFT)


class StructureOrderError(ValueError):
    """Raised when Structure Order Fact construction violates the frozen contract."""


def _ratio(current: Decimal, prior: Decimal) -> Decimal:
    if prior <= 0:
        raise StructureOrderError("structure-order ratio denominator must be positive")
    with localcontext(Context(prec=28)):
        return current / prior - Decimal(1)


def _range(rows: Sequence[DailyInput]) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    highs = [item.high for item in rows]
    lows = [item.low for item in rows]
    if any(item is None for item in highs) or any(item is None for item in lows):
        raise StructureOrderError("structure-order range requires admitted OHLC")
    high = max(item for item in highs if item is not None)
    low = min(item for item in lows if item is not None)
    midpoint = (high + low) / Decimal(2)
    if midpoint <= 0:
        raise StructureOrderError("structure-order range midpoint must be positive")
    with localcontext(Context(prec=28)):
        width = (high - low) / midpoint
    return high, low, midpoint, width


def _spec(
    concept_id: str,
    *,
    window_sessions: int,
    comparison_window_sessions: int | None,
) -> dict[str, Any]:
    formulas = {
        RANGE_WIDTH: "(window_high-window_low)/((window_high+window_low)/2)",
        WIDTH_CHANGE: "current_range_width/prior_nonoverlap_range_width-1",
        RANGE_CENTER_SHIFT: "current_range_midpoint/prior_nonoverlap_range_midpoint-1",
    }
    if concept_id not in formulas:
        raise StructureOrderError("unregistered Structure Order concept")
    return {
        "schema_version": "quanthus.structure-order-fact-spec.v1",
        "concept_id": concept_id,
        "concept_version": "1.0.0",
        "formula": formulas[concept_id],
        "window_sessions": window_sessions,
        "comparison_window_sessions": comparison_window_sessions,
        "current_window_includes_t": True,
        "comparison_window_is_immediately_prior_nonoverlap": comparison_window_sessions is not None,
        "coordinate_policy": "same-listing-same-coordinate-contiguous-final-daily",
        "numeric_policy": "decimal-context28",
        "stage_thresholds_in_fact_identity": False,
    }


@dataclass(frozen=True)
class StructureOrderMeasurement:
    security_code: str
    security_id: str
    listing_id: str
    market_date: date
    concept_id: str
    window_sessions: int
    comparison_window_sessions: int | None
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
                "comparison_window_sessions": self.comparison_window_sessions,
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
class StructureOrderAvailability:
    security_code: str
    market_date: date
    available: bool
    reason: str
    measurement_revision_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StructureOrderFacts:
    measurements: tuple[StructureOrderMeasurement, ...]
    availability: tuple[StructureOrderAvailability, ...]
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
    window_sessions: int,
    comparison_window_sessions: int | None = None,
    availability_reason: str = "available",
) -> StructureOrderMeasurement:
    return StructureOrderMeasurement(
        row.security_code,
        row.security_id,
        row.listing_id,
        row.market_date,
        concept_id,
        window_sessions,
        comparison_window_sessions,
        row.coordinate_ref,
        value,
        availability_reason,
        digest(
            _spec(
                concept_id,
                window_sessions=window_sessions,
                comparison_window_sessions=comparison_window_sessions,
            )
        ),
        digest([item.member_digest for item in source_rows]),
        BusinessTime("measurement", row.result_as_of),
    )


def _current_measurements(rows: Sequence[DailyInput]) -> tuple[StructureOrderMeasurement, ...]:
    if len(rows) < 60:
        raise StructureOrderError("Structure Order Facts require 60 contiguous admitted sessions")
    rows = rows[-60:]
    current = rows[-1]
    current_ranges = {window: _range(rows[-window:]) for window in (10, 20, 60)}
    prior10 = _range(rows[-20:-10])
    prior20 = _range(rows[-40:-20])
    output: list[StructureOrderMeasurement] = []

    for window in (10, 20, 60):
        output.append(
            _measurement(
                current,
                rows[-window:],
                RANGE_WIDTH,
                current_ranges[window][3],
                window_sessions=window,
            )
        )

    for window, prior in ((10, prior10), (20, prior20)):
        current_midpoint = current_ranges[window][2]
        current_width = current_ranges[window][3]
        prior_midpoint = prior[2]
        prior_width = prior[3]
        width_change = None if prior_width == 0 else _ratio(current_width, prior_width)
        reason = "prior_zero_range_width" if prior_width == 0 else "available"
        source = rows[-2 * window :]
        output.extend(
            [
                _measurement(
                    current,
                    source,
                    WIDTH_CHANGE,
                    width_change,
                    window_sessions=window,
                    comparison_window_sessions=window,
                    availability_reason=reason,
                ),
                _measurement(
                    current,
                    source,
                    RANGE_CENTER_SHIFT,
                    _ratio(current_midpoint, prior_midpoint),
                    window_sessions=window,
                    comparison_window_sessions=window,
                ),
            ]
        )
    return tuple(output)


def build_structure_order_facts(
    admissions: Sequence[Admission],
    *,
    report_start: date,
    report_end: date,
) -> StructureOrderFacts:
    """Build threshold-free Structure Order Facts from contiguous admitted daily observations."""
    if report_end < report_start:
        raise StructureOrderError("invalid Structure Order report window")
    segments: dict[str, deque[DailyInput]] = defaultdict(lambda: deque(maxlen=60))
    measurements: list[StructureOrderMeasurement] = []
    availability: list[StructureOrderAvailability] = []
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
        if not attempt.admitted or row.close is None or row.high is None or row.low is None:
            if segment:
                resets["nonadmitted"] += 1
            segment.clear()
            if in_report:
                availability.append(
                    StructureOrderAvailability(
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
        if len(segment) < 60:
            resets["insufficient_history"] += 1
            availability.append(
                StructureOrderAvailability(
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
            StructureOrderAvailability(
                row.security_code,
                row.market_date,
                True,
                "available",
                tuple(item.identity.revision_id for item in current),
            )
        )
        resets["available"] += 1
    return StructureOrderFacts(
        tuple(
            sorted(
                measurements,
                key=lambda item: (
                    item.security_code,
                    item.market_date,
                    item.concept_id,
                    item.window_sessions,
                    item.comparison_window_sessions or 0,
                ),
            )
        ),
        tuple(sorted(availability, key=lambda item: (item.security_code, item.market_date))),
        resets,
    )


def structure_order_counts(facts: StructureOrderFacts) -> dict[str, Any]:
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

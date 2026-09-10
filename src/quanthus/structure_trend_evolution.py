"""Formal continuous Trend Evolution Facts for bounded Structure research.

These facts describe direction, efficiency, velocity and rolling-range position. They do not emit
Stage 1/8/9/12 labels, choose thresholds, infer a future bottom/top, or imply trading authority.
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

WINDOW_RETURN = "structure.geometry.window-return"
DIRECTIONAL_EFFICIENCY = "structure.geometry.directional-efficiency"
RETURN_VELOCITY = "structure.geometry.return-velocity"
VELOCITY_DELTA = "structure.geometry.velocity-delta"
RANGE_POSITION = "structure.geometry.range-position"
DRAWDOWN_FROM_HIGH = "structure.geometry.drawdown-from-window-high"
REBOUND_FROM_LOW = "structure.geometry.rebound-from-window-low"

CONCEPTS = (
    WINDOW_RETURN,
    DIRECTIONAL_EFFICIENCY,
    RETURN_VELOCITY,
    VELOCITY_DELTA,
    RANGE_POSITION,
    DRAWDOWN_FROM_HIGH,
    REBOUND_FROM_LOW,
)


class TrendEvolutionError(ValueError):
    """Raised when Trend Evolution Fact construction violates its causal contract."""


def _ratio(current: Decimal, prior: Decimal) -> Decimal:
    if prior <= 0:
        raise TrendEvolutionError("trend ratio denominator must be positive")
    with localcontext(Context(prec=28)):
        return current / prior - Decimal(1)


def _return(rows: Sequence[DailyInput], sessions: int) -> Decimal:
    current = rows[-1].close
    prior = rows[-sessions - 1].close
    if current is None or prior is None:
        raise TrendEvolutionError("trend return requires admitted closes")
    return _ratio(current, prior)


def _efficiency(rows: Sequence[DailyInput], sessions: int) -> Decimal:
    closes = [item.close for item in rows[-sessions - 1 :]]
    if any(item is None for item in closes):
        raise TrendEvolutionError("directional efficiency requires admitted closes")
    exact = [item for item in closes if item is not None]
    path = sum(abs(right - left) for left, right in zip(exact, exact[1:], strict=False))
    if path == 0:
        return Decimal(0)
    with localcontext(Context(prec=28)):
        return (exact[-1] - exact[0]) / path


def _range(rows: Sequence[DailyInput]) -> tuple[Decimal, Decimal]:
    highs = [item.high for item in rows]
    lows = [item.low for item in rows]
    if any(item is None for item in highs + lows):
        raise TrendEvolutionError("rolling range requires admitted OHLC")
    return (
        max(item for item in highs if item is not None),
        min(item for item in lows if item is not None),
    )


def _spec(
    concept_id: str,
    *,
    window_sessions: int | None,
    comparison_window_sessions: int | None = None,
) -> dict[str, Any]:
    formulas = {
        WINDOW_RETURN: "close_t/close_t_minus_n-1",
        DIRECTIONAL_EFFICIENCY: "(close_t-close_t_minus_n)/sum_abs_adjacent_close_changes",
        RETURN_VELOCITY: "window_return/window_sessions",
        VELOCITY_DELTA: "return_velocity_5-return_velocity_20",
        RANGE_POSITION: "(close_t-window_low)/(window_high-window_low)",
        DRAWDOWN_FROM_HIGH: "close_t/window_high-1",
        REBOUND_FROM_LOW: "close_t/window_low-1",
    }
    if concept_id not in formulas:
        raise TrendEvolutionError("unregistered Trend Evolution concept")
    return {
        "schema_version": "quanthus.trend-evolution-fact-spec.v1",
        "concept_id": concept_id,
        "concept_version": "1.0.0",
        "formula": formulas[concept_id],
        "window_sessions": window_sessions,
        "comparison_window_sessions": comparison_window_sessions,
        "current_window_includes_t": True,
        "return_baseline_is_exact_prior_admitted_session": True,
        "coordinate_policy": "same-listing-same-coordinate-contiguous-final-daily",
        "numeric_policy": "decimal-context28",
        "stage_thresholds_in_fact_identity": False,
    }


@dataclass(frozen=True)
class TrendMeasurement:
    security_code: str
    security_id: str
    listing_id: str
    market_date: date
    concept_id: str
    window_sessions: int | None
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
class TrendFactAvailability:
    security_code: str
    market_date: date
    available: bool
    reason: str
    measurement_revision_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TrendEvolutionFacts:
    measurements: tuple[TrendMeasurement, ...]
    availability: tuple[TrendFactAvailability, ...]
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
    window_sessions: int | None,
    comparison_window_sessions: int | None = None,
    availability_reason: str = "available",
) -> TrendMeasurement:
    specification = _spec(
        concept_id,
        window_sessions=window_sessions,
        comparison_window_sessions=comparison_window_sessions,
    )
    return TrendMeasurement(
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
        digest(specification),
        digest([item.member_digest for item in source_rows]),
        BusinessTime("measurement", row.result_as_of),
    )


def _current_measurements(rows: Sequence[DailyInput]) -> tuple[TrendMeasurement, ...]:
    if len(rows) < 61:
        raise TrendEvolutionError("Trend Evolution Facts require 61 contiguous admitted sessions")
    rows = rows[-61:]
    current = rows[-1]
    if current.close is None:
        raise TrendEvolutionError("Trend Evolution current close is missing")

    returns = {sessions: _return(rows, sessions) for sessions in (5, 20, 60)}
    efficiency = {sessions: _efficiency(rows, sessions) for sessions in (5, 20)}
    velocity = {
        sessions: returns[sessions] / Decimal(sessions) for sessions in (5, 20)
    }
    high60, low60 = _range(rows[-60:])
    position = None
    position_reason = "available"
    if high60 == low60:
        position_reason = "zero_window_range"
    else:
        with localcontext(Context(prec=28)):
            position = (current.close - low60) / (high60 - low60)

    output: list[TrendMeasurement] = []
    for sessions in (5, 20, 60):
        output.append(
            _measurement(
                current,
                rows[-sessions - 1 :],
                WINDOW_RETURN,
                returns[sessions],
                window_sessions=sessions,
            )
        )
    for sessions in (5, 20):
        output.append(
            _measurement(
                current,
                rows[-sessions - 1 :],
                DIRECTIONAL_EFFICIENCY,
                efficiency[sessions],
                window_sessions=sessions,
            )
        )
        output.append(
            _measurement(
                current,
                rows[-sessions - 1 :],
                RETURN_VELOCITY,
                velocity[sessions],
                window_sessions=sessions,
            )
        )
    output.append(
        _measurement(
            current,
            rows[-21:],
            VELOCITY_DELTA,
            velocity[5] - velocity[20],
            window_sessions=5,
            comparison_window_sessions=20,
        )
    )
    output.extend(
        [
            _measurement(
                current,
                rows[-60:],
                RANGE_POSITION,
                position,
                window_sessions=60,
                availability_reason=position_reason,
            ),
            _measurement(
                current,
                rows[-60:],
                DRAWDOWN_FROM_HIGH,
                _ratio(current.close, high60),
                window_sessions=60,
            ),
            _measurement(
                current,
                rows[-60:],
                REBOUND_FROM_LOW,
                _ratio(current.close, low60),
                window_sessions=60,
            ),
        ]
    )
    return tuple(output)


def build_trend_evolution_facts(
    admissions: Sequence[Admission],
    *,
    report_start: date,
    report_end: date,
) -> TrendEvolutionFacts:
    """Build threshold-free Trend Evolution Facts from exact contiguous admitted daily rows."""
    if report_end < report_start:
        raise TrendEvolutionError("invalid Trend Evolution report window")
    segments: dict[str, deque[DailyInput]] = defaultdict(lambda: deque(maxlen=61))
    measurements: list[TrendMeasurement] = []
    availability: list[TrendFactAvailability] = []
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
                    TrendFactAvailability(
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
        if len(segment) < 61:
            resets["insufficient_history"] += 1
            availability.append(
                TrendFactAvailability(
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
        refs = tuple(item.identity.revision_id for item in current)
        availability.append(
            TrendFactAvailability(row.security_code, row.market_date, True, "available", refs)
        )
        resets["available"] += 1
    return TrendEvolutionFacts(
        tuple(
            sorted(
                measurements,
                key=lambda item: (
                    item.security_code,
                    item.market_date,
                    item.concept_id,
                    item.window_sessions or 0,
                    item.comparison_window_sessions or 0,
                ),
            )
        ),
        tuple(sorted(availability, key=lambda item: (item.security_code, item.market_date))),
        resets,
    )


def trend_fact_counts(facts: TrendEvolutionFacts) -> dict[str, Any]:
    by_concept: dict[str, int] = defaultdict(int)
    unavailable_measurements = 0
    for item in facts.measurements:
        by_concept[item.concept_id] += 1
        unavailable_measurements += item.value is None
    return {
        "measurement_count": len(facts.measurements),
        "available_observation_count": sum(item.available for item in facts.availability),
        "unavailable_observation_count": sum(not item.available for item in facts.availability),
        "unavailable_measurement_count": unavailable_measurements,
        "concept_counts": dict(sorted(by_concept.items())),
        "reset_counts": dict(sorted(facts.reset_counts.items())),
    }

"""Exact 20+20 retrospective Head2 facts, using existing measurement kernels.

The generic Order/Trend bundles require 60/61 rows for their other concepts.
This entry point emits only the approved six facts from exactly forty admitted
sessions. It neither widens that window nor qualifies its caller's sources.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from quanthus import structure_directional_path_geometry as path_geometry
from quanthus import structure_order_facts as order
from quanthus import structure_trend_evolution as trend
from quanthus.structure_v2_inputs import Admission, digest

VERSION = "head2-exact40-facts-v1"
PrimaryMeasurement = order.StructureOrderMeasurement | trend.TrendMeasurement


@dataclass(frozen=True)
class Head2WindowFacts:
    anchor: date
    available: bool
    reason: str
    source_closure_digest: str
    primary: tuple[PrimaryMeasurement, ...]
    path: tuple[path_geometry.DirectionalPathMeasurement, ...]

    def semantic(self) -> dict[str, Any]:
        return {
            "version": VERSION, "anchor": self.anchor,
            "available": self.available, "reason": self.reason,
            "source_closure_digest": self.source_closure_digest,
            "primary": [m.to_dict() for m in self.primary],
            "path": [m.to_dict() for m in self.path],
        }

    @property
    def digest(self) -> str:
        return digest(self.semantic())

    def reference_facts(self) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        names = {
            trend.DIRECTIONAL_EFFICIENCY: "directional_efficiency",
            order.RANGE_WIDTH: "range_width", order.WIDTH_CHANGE: "width_change",
            order.RANGE_CENTER_SHIFT: "center_shift",
        }

        def record(
            m: PrimaryMeasurement | path_geometry.DirectionalPathMeasurement,
        ) -> dict[str, Any]:
            return {
                "value": None if m.value is None else str(m.value),
                "reason": m.availability_reason, "revision_id": m.identity.revision_id,
            }

        return (
            {names[m.concept_id]: record(m) for m in self.primary},
            {m.concept_id: record(m) for m in self.path},
        )


def build_head2_window_facts(
    admissions: Sequence[Admission], *, anchor: date,
) -> Head2WindowFacts:
    """Compute only the frozen facts; action/missing resets stay unavailable.

    A malformed caller window fails rather than being sorted, sliced or padded.
    Qualified corporate actions inside either half invalidate the comparison;
    no inherited state or adjusted price is synthesized across that reset.
    """
    if not date(2012, 1, 1) <= anchor <= date(2021, 12, 31):
        raise ValueError("HEAD2_DATE_SCOPE")
    if len(admissions) != 40:
        raise ValueError("HEAD2_EXACT40_REQUIRED")
    rows = tuple(a.row for a in admissions)
    current = rows[-1]
    if current.market_date != anchor:
        raise ValueError("HEAD2_ANCHOR_MISMATCH")
    identity = (current.security_code, current.security_id, current.listing_id, current.exchange)
    if any((r.security_code, r.security_id, r.listing_id, r.exchange) != identity for r in rows):
        raise ValueError("HEAD2_IDENTITY_MISMATCH")
    if rows[0].market_date < date(2012, 1, 1) or any(
        b.market_date <= a.market_date or b.session_index != a.session_index + 1
        for a, b in zip(rows, rows[1:], strict=False)
    ):
        raise ValueError("HEAD2_SEQUENCE_MISMATCH")
    closure = digest([r.member_digest for r in rows])

    def unavailable(reason: str) -> Head2WindowFacts:
        return Head2WindowFacts(anchor, False, reason, closure, (), ())

    for attempt in admissions:
        if not attempt.admitted:
            return unavailable("nonadmitted:" + attempt.reason)
        if any(v is None for v in (attempt.row.high, attempt.row.low, attempt.row.close)):
            return unavailable("missing_required_price")
    if any(r.coordinate_ref != current.coordinate_ref for r in rows):
        return unavailable("coordinate_change_within_fixed40")
    current_range = order._range(rows[-20:])
    prior_range = order._range(rows[:20])
    prior_width = prior_range[3]
    primary: tuple[PrimaryMeasurement, ...] = (
        trend._measurement(current, rows[-21:], trend.DIRECTIONAL_EFFICIENCY,
                           trend._efficiency(rows, 20), window_sessions=20),
        order._measurement(current, rows[-20:], order.RANGE_WIDTH,
                           current_range[3], window_sessions=20),
        order._measurement(
            current, rows, order.WIDTH_CHANGE,
            None if prior_width == 0 else order._ratio(current_range[3], prior_width),
            window_sessions=20, comparison_window_sessions=20,
            availability_reason="prior_zero_range_width" if prior_width == 0 else "available",
        ),
        order._measurement(current, rows, order.RANGE_CENTER_SHIFT,
                           order._ratio(current_range[2], prior_range[2]),
                           window_sessions=20, comparison_window_sessions=20),
    )
    paths = path_geometry._current_measurements(rows)
    available = all(m.value is not None for m in primary)
    return Head2WindowFacts(anchor, available,
                           "available" if available else "required_fact_unavailable",
                           closure, primary, paths)

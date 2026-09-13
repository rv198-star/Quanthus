"""P3-STAGE-012-E2 synthetic-only close-envelope resolution prototype.

The prototype emits deterministic event/descriptive evidence only. It is not a registered Fact,
does not classify compact disorder, and owns no learned numeric threshold. Every re-entry is
resolved using observations already present in the supplied T-ending 21-session path.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from quanthus.structure_directional_path_envelope import (
    PRIOR_ENVELOPE_SESSIONS,
    REQUIRED_CLOSES,
    DirectionalPathEnvelopeError,
    detect_close_envelope_events,
)
from quanthus.structure_directional_path_envelope import (
    CloseEnvelopeEscapeEvent as EnvelopeEscapeEvent,
)
from quanthus.structure_v2_inputs import digest

PRIMARY_HISTORY_SESSIONS = REQUIRED_CLOSES
STATUS = Literal["AVAILABLE", "UNAVAILABLE", "INSUFFICIENT_HISTORY"]


class RangeEnvelopePrototypeError(ValueError):
    """The synthetic range-envelope prototype input or profile is invalid."""


@dataclass(frozen=True)
class RangeEnvelopeAssessment:
    status: STATUS
    events: tuple[EnvelopeEscapeEvent, ...]
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        events = [item.to_dict() for item in self.events]
        resolved = [item for item in self.events if item.resolved_by_t]
        unresolved = [item for item in self.events if not item.resolved_by_t]
        resolved_sides = [item.side for item in resolved]
        alternations = sum(
            1
            for left, right in zip(resolved_sides, resolved_sides[1:], strict=False)
            if left != right
        )
        unresolved_sides = sorted({item.side for item in unresolved})
        return {
            "schema_version": "quanthus.stage-range-envelope-prototype-assessment.v1",
            "status": self.status,
            "reason": self.reason,
            "profile": {
                "primary_history_sessions": PRIMARY_HISTORY_SESSIONS,
                "prior_close_envelope_sessions": PRIOR_ENVELOPE_SESSIONS,
                "strict_escape_equality_is_inside": True,
                "future_beyond_t_used": False,
            },
            "events": events,
            "descriptors": {
                "escape_count": len(self.events),
                "resolved_escape_count": len(resolved),
                "unresolved_escape_count": len(unresolved),
                "upper_escape_count": sum(item.side == "UPPER" for item in self.events),
                "lower_escape_count": sum(item.side == "LOWER" for item in self.events),
                "resolved_escape_side_sequence": resolved_sides,
                "resolved_escape_side_alternation_count": alternations,
                "resolved_escape_both_sides": set(resolved_sides) == {"UPPER", "LOWER"},
                "unresolved_escape_sides": unresolved_sides,
                "single_unresolved_side_only": len(unresolved_sides) == 1 and bool(unresolved),
                "event_trace_digest": digest(events),
            },
            "claims": {
                "compact_disorder_truth": None,
                "orderly_consolidation_truth": None,
                "relocation_truth": None,
                "numeric_threshold_selected": False,
                "registered_fact": False,
            },
        }


def assess_close_envelope(closes: Sequence[object | None]) -> RangeEnvelopeAssessment:
    """Return the frozen E2 view over the Structure-owned envelope detector."""
    try:
        status, reason, events = detect_close_envelope_events(closes)
    except DirectionalPathEnvelopeError as exc:
        raise RangeEnvelopePrototypeError(str(exc)) from exc
    return RangeEnvelopeAssessment(status, events, reason)

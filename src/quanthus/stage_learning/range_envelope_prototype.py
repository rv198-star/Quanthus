"""P3-STAGE-012-E2 synthetic-only close-envelope resolution prototype.

The prototype emits deterministic event/descriptive evidence only. It is not a registered Fact,
does not classify compact disorder, and owns no learned numeric threshold. Every re-entry is
resolved using observations already present in the supplied T-ending 21-session path.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from quanthus.structure_v2_inputs import digest

PRIMARY_HISTORY_SESSIONS = 21
PRIOR_ENVELOPE_SESSIONS = 10
STATUS = Literal["AVAILABLE", "UNAVAILABLE", "INSUFFICIENT_HISTORY"]
SIDE = Literal["UPPER", "LOWER"]


class RangeEnvelopePrototypeError(ValueError):
    """The synthetic range-envelope prototype input or profile is invalid."""


def _decimal(value: object, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise RangeEnvelopePrototypeError(f"{label}_invalid") from exc
    if not result.is_finite() or result <= 0:
        raise RangeEnvelopePrototypeError(f"{label}_invalid")
    return result


@dataclass(frozen=True)
class EnvelopeEscapeEvent:
    session_index: int
    side: SIDE
    close: Decimal
    frozen_lower: Decimal
    frozen_upper: Decimal
    reentry_session_index: int | None

    @property
    def resolved_by_t(self) -> bool:
        return self.reentry_session_index is not None

    @property
    def reentry_lag_sessions(self) -> int | None:
        if self.reentry_session_index is None:
            return None
        return self.reentry_session_index - self.session_index

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_index": self.session_index,
            "side": self.side,
            "close": str(self.close),
            "frozen_lower": str(self.frozen_lower),
            "frozen_upper": str(self.frozen_upper),
            "resolved_by_t": self.resolved_by_t,
            "reentry_session_index": self.reentry_session_index,
            "reentry_lag_sessions": self.reentry_lag_sessions,
        }


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
    """Return exact close-envelope escape/re-entry evidence for one T-ending synthetic path."""
    if len(closes) < PRIMARY_HISTORY_SESSIONS:
        return RangeEnvelopeAssessment(
            "INSUFFICIENT_HISTORY", (), "primary_history_requires_21_sessions"
        )
    if len(closes) != PRIMARY_HISTORY_SESSIONS:
        raise RangeEnvelopePrototypeError("primary_history_profile_requires_exact_21_sessions")
    if any(value is None for value in closes):
        return RangeEnvelopeAssessment("UNAVAILABLE", (), "close_input_unavailable")
    values = tuple(_decimal(value, "close") for value in closes)
    events: list[EnvelopeEscapeEvent] = []
    for index in range(PRIOR_ENVELOPE_SESSIONS, PRIMARY_HISTORY_SESSIONS):
        prior = values[index - PRIOR_ENVELOPE_SESSIONS:index]
        lower = min(prior)
        upper = max(prior)
        current = values[index]
        side: SIDE | None = None
        if current > upper:
            side = "UPPER"
        elif current < lower:
            side = "LOWER"
        if side is None:
            continue
        reentry = next(
            (
                later
                for later in range(index + 1, PRIMARY_HISTORY_SESSIONS)
                if lower <= values[later] <= upper
            ),
            None,
        )
        events.append(
            EnvelopeEscapeEvent(
                session_index=index,
                side=side,
                close=current,
                frozen_lower=lower,
                frozen_upper=upper,
                reentry_session_index=reentry,
            )
        )
    return RangeEnvelopeAssessment("AVAILABLE", tuple(events))

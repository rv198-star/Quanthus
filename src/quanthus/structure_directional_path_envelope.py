"""Structure-owned synthetic proposal for close-envelope path resolution geometry.

This module belongs to the proposed B6-PATH envelope extension. It emits neutral path evidence
only: no Stage proposition, no learned threshold, no market-data acquisition route, and no
registered Fact.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from quanthus.policy import find_project_root
from quanthus.structure_v2_inputs import DailyInput, digest

PRIMARY_PATH_SESSIONS = 20
REQUIRED_CLOSES = PRIMARY_PATH_SESSIONS + 1
PRIOR_ENVELOPE_SESSIONS = 10
PROPOSED_CONCEPT_ID = "structure.geometry.close-envelope-resolution-trace"
CONTRACT_PATH = "config/structure-directional-path-envelope-extension.v1.json"
CONTRACT_DIGEST = "sha256:22d1031e547dec1db5a5d620b12949cc4a856537569c03d495d04cc4e559a71a"

STATUS = Literal["AVAILABLE", "UNAVAILABLE", "INSUFFICIENT_HISTORY"]
SIDE = Literal["UPPER", "LOWER"]


class DirectionalPathEnvelopeError(ValueError):
    """The proposed B6-PATH envelope profile or input is invalid."""


def load_contract(root: Path | None = None) -> dict[str, Any]:
    root = root or find_project_root()
    try:
        value = json.loads((root / CONTRACT_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DirectionalPathEnvelopeError("directional path envelope contract unreadable") from exc
    if digest(value) != CONTRACT_DIGEST:
        raise DirectionalPathEnvelopeError("directional path envelope contract drift")
    return value


def _decimal(value: object, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise DirectionalPathEnvelopeError(f"{label}_invalid") from exc
    if not result.is_finite() or result <= 0:
        raise DirectionalPathEnvelopeError(f"{label}_invalid")
    return result


@dataclass(frozen=True)
class CloseEnvelopeEscapeEvent:
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


def detect_close_envelope_events(
    closes: Sequence[object | None],
) -> tuple[STATUS, str | None, tuple[CloseEnvelopeEscapeEvent, ...]]:
    """Return the exact threshold-free escape/re-entry event trace for one T-ending path."""
    if len(closes) < REQUIRED_CLOSES:
        return "INSUFFICIENT_HISTORY", "primary_history_requires_21_sessions", ()
    if len(closes) != REQUIRED_CLOSES:
        raise DirectionalPathEnvelopeError("primary_history_profile_requires_exact_21_sessions")
    if any(value is None for value in closes):
        return "UNAVAILABLE", "close_input_unavailable", ()
    values = tuple(_decimal(value, "close") for value in closes)
    events: list[CloseEnvelopeEscapeEvent] = []
    for index in range(PRIOR_ENVELOPE_SESSIONS, REQUIRED_CLOSES):
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
                for later in range(index + 1, REQUIRED_CLOSES)
                if lower <= values[later] <= upper
            ),
            None,
        )
        events.append(
            CloseEnvelopeEscapeEvent(
                session_index=index,
                side=side,
                close=current,
                frozen_lower=lower,
                frozen_upper=upper,
                reentry_session_index=reentry,
            )
        )
    return "AVAILABLE", None, tuple(events)


@dataclass(frozen=True)
class DirectionalPathEnvelopeTrace:
    status: STATUS
    events: tuple[CloseEnvelopeEscapeEvent, ...]
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
            "schema_version": "quanthus.directional-path-envelope-trace-proposal.v1",
            "concept_id": PROPOSED_CONCEPT_ID,
            "registration_status": "PROPOSED_NOT_REGISTERED",
            "status": self.status,
            "reason": self.reason,
            "profile": {
                "primary_path_sessions": PRIMARY_PATH_SESSIONS,
                "required_contiguous_closes": REQUIRED_CLOSES,
                "prior_close_envelope_sessions": PRIOR_ENVELOPE_SESSIONS,
                "strict_escape_equality_is_inside": True,
                "reentry_known_as_of": "T",
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
                "stage_projection": None,
                "compact_disorder_truth": None,
                "relocation_truth": None,
                "numeric_threshold_selected": False,
                "registered_fact": False,
            },
        }


def assess_close_path_envelope(closes: Sequence[object | None]) -> DirectionalPathEnvelopeTrace:
    """Assess one exact T-ending close path under the proposed Structure-owned profile."""
    status, reason, events = detect_close_envelope_events(closes)
    return DirectionalPathEnvelopeTrace(status, events, reason)


def assess_daily_segment(rows: Sequence[DailyInput]) -> DirectionalPathEnvelopeTrace:
    """Validate B6-PATH segment invariants before assessing the proposed envelope trace."""
    if len(rows) < REQUIRED_CLOSES:
        return DirectionalPathEnvelopeTrace(
            "INSUFFICIENT_HISTORY", (), "primary_history_requires_21_sessions"
        )
    if len(rows) != REQUIRED_CLOSES:
        raise DirectionalPathEnvelopeError("primary_history_profile_requires_exact_21_sessions")
    if any(row.close is None for row in rows):
        return DirectionalPathEnvelopeTrace("UNAVAILABLE", (), "close_input_unavailable")
    first = rows[0]
    if any(
        row.listing_id != first.listing_id or row.coordinate_ref != first.coordinate_ref
        for row in rows[1:]
    ):
        return DirectionalPathEnvelopeTrace(
            "INSUFFICIENT_HISTORY", (), "listing_or_coordinate_change_requires_rewarm"
        )
    if any(
        right.session_index != left.session_index + 1
        for left, right in zip(rows, rows[1:], strict=False)
    ):
        return DirectionalPathEnvelopeTrace(
            "INSUFFICIENT_HISTORY", (), "hidden_session_gap_requires_rewarm"
        )
    return assess_close_path_envelope([row.close for row in rows])

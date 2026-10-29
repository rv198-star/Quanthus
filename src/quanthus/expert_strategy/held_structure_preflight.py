"""Offline #545 denominator screening; never an expert or trading admission path.

Reuses accepted decision/event metadata. It selects no prices, returns, future
targets or candidate rule. Identity overlap is deliberately weaker than a matched
consumer state, and ticker/date overlap never grants source compatibility.
"""

from __future__ import annotations

import hashlib
import json
from bisect import bisect_left
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
SUPPORT_FIELDS = frozenset(
    {"unreclaimed_age_sessions", "signed_distance_to_support", "post_break_rebound"}
)


class PreflightError(ValueError):
    """An exact denominator or archive identity could not be established."""


def read_pinned_json(path: Path, sha256: str, *, maximum_bytes: int) -> Any:
    """Read selected bytes once; verify the external pin before interpreting them."""
    if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
        raise PreflightError("invalid_sha256_pin")
    with path.open("rb") as source:
        payload = source.read(maximum_bytes + 1)
    if len(payload) > maximum_bytes:
        raise PreflightError("input_size_exceeded")
    if hashlib.sha256(payload).hexdigest() != sha256:
        raise PreflightError("input_sha256_mismatch")
    return json.loads(payload)


def _day(value: object) -> date:
    if not isinstance(value, str):
        raise PreflightError("invalid_business_date")
    return date.fromisoformat(value)


def _text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise PreflightError("missing_identity")
    return value


@dataclass(frozen=True)
class HeldIdentity:
    market_date: date
    unit_id: str
    security_code: str
    entry_key: str
    entry_date: date
    quantity: int
    snapshot_event_id: str


@dataclass(frozen=True)
class Population:
    arm: str
    identities: tuple[HeldIdentity, ...]
    decision_count: int
    entry_count: int
    actions: Mapping[str, int]
    reasons: Mapping[str, int]

    def safe_summary(self) -> dict[str, Any]:
        observations = self.identities
        return {
            "arm": self.arm,
            "decision_count": self.decision_count,
            "entry_decisions": self.entry_count,
            "held_decisions": len(observations),
            "held_identity_resolved": len(observations),
            "distinct_issuers_by_archive_code": len({r.security_code for r in observations}),
            "distinct_entry_episodes": len({r.entry_key for r in observations}),
            "distinct_dates": len({r.market_date for r in observations}),
            "first_date": min((r.market_date for r in observations), default=None),
            "last_date": max((r.market_date for r in observations), default=None),
            "actions": dict(sorted(self.actions.items())),
            "reasons": dict(sorted(self.reasons.items())),
            "full_consumer_input_state_recovered": False,
            "independent_sample_count": None,
        }


def recover_population(
    arm: str,
    decisions: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    coordinates: Mapping[str, str],
    *,
    first_date: date,
    last_date: date,
    decision_time: time,
) -> Population:
    """Resolve every held identity, or reject the entire selected population.

    A same-day close snapshot identifies the position at the decision boundary.
    The latest *strictly earlier* filled entry binds its episode. Entry fills do
    not authorize joining different arms as if their portfolio paths were equal.
    """
    if first_date > last_date or decision_time.tzinfo is not None:
        raise PreflightError("invalid_decision_scope")
    entries: dict[str, list[tuple[date, str, str]]] = defaultdict(list)
    held: list[Mapping[str, Any]] = []
    seen: set[tuple[date, str, str]] = set()
    entry_count = 0
    for row in decisions:
        day = _day(row["market_date"])
        if not first_date <= day <= last_date:
            raise PreflightError("decision_outside_frozen_window")
        surface, key = _text(row["surface"]), _text(row["decision_key"])
        identity = day, surface, key
        if identity in seen:
            raise PreflightError("duplicate_decision_key")
        seen.add(identity)
        if surface == "held":
            if key != f"held:{_text(row['unit_id'])}":
                raise PreflightError("held_unit_key_mismatch")
            held.append(row)
        elif surface == "entry":
            entry_count += 1
            coordinate = coordinates.get(key)
            if coordinate is None:
                raise PreflightError("entry_coordinate_missing")
            code, separator, day_text = coordinate.rpartition("@")
            if not code or separator != "@" or _day(day_text) != day:
                raise PreflightError("entry_coordinate_mismatch")
            if type(row["filled"]) is not bool:
                raise PreflightError("invalid_fill_marker")
            if row["filled"]:
                entries[_text(row["unit_id"])].append((day, key, code))
        else:
            raise PreflightError("unknown_decision_surface")
    for values in entries.values():
        values.sort()
        if len({r[0] for r in values}) != len(values):
            raise PreflightError("ambiguous_filled_entry")

    snapshots: dict[tuple[str, date], Mapping[str, Any]] = {}
    for event in events:
        if event["event_type"] != "daily_snapshot":
            continue
        payload = event["payload"]
        day = _day(payload["session_date"])
        unit = _text(payload["unit_id"])
        snapshot_key = unit, day
        if snapshot_key in snapshots:
            raise PreflightError("ambiguous_same_day_snapshot")
        observed = datetime.fromisoformat(event["event_time"])
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise PreflightError("snapshot_time_unknown")
        observed = observed.astimezone(SHANGHAI)
        if observed.date() != day or observed > datetime.combine(day, decision_time, SHANGHAI):
            raise PreflightError("snapshot_after_decision_boundary")
        snapshots[snapshot_key] = event

    resolved = []
    for row in held:
        day, unit = _day(row["market_date"]), _text(row["unit_id"])
        snapshot = snapshots.get((unit, day))
        if snapshot is None:
            raise PreflightError("held_snapshot_missing")
        payload = snapshot["payload"]
        quantity = payload["position_quantity"]
        if type(quantity) is not int or quantity <= 0:
            raise PreflightError("held_snapshot_not_positive")
        prior = entries.get(unit, [])
        index = bisect_left([r[0] for r in prior], day) - 1
        if index < 0:
            raise PreflightError("held_prior_entry_missing")
        entry_day, entry_key, code = prior[index]
        if code != payload["security_code"]:
            raise PreflightError("held_entry_security_mismatch")
        resolved.append(
            HeldIdentity(day, unit, code, entry_key, entry_day, quantity, snapshot["event_id"])
        )
    return Population(
        arm,
        tuple(sorted(resolved, key=lambda r: (r.market_date, r.unit_id))),
        len(decisions),
        entry_count,
        Counter(_text(r["final_action"]) for r in held),
        Counter(code for r in held for code in r["reason_codes"]),
    )


def compare_identity_paths(left: Population, right: Population) -> dict[str, Any]:
    """Describe identity/path overlap, explicitly not causal or full-state pairs."""
    lhs = {(r.unit_id, r.market_date): r for r in left.identities}
    rhs = {(r.unit_id, r.market_date): r for r in right.identities}
    common = lhs.keys() & rhs.keys()
    same_security = {k for k in common if lhs[k].security_code == rhs[k].security_code}
    same_episode = {k for k in same_security if lhs[k].entry_key == rhs[k].entry_key}
    return {
        "arms": [left.arm, right.arm],
        "common_unit_dates": len(common),
        "different_security_at_common_unit_date": len(common - same_security),
        "same_security_different_entry_episode": len(same_security - same_episode),
        "same_entry_episode": len(same_episode),
        "same_episode_different_quantity": sum(
            lhs[k].quantity != rhs[k].quantity for k in same_episode
        ),
        "left_unmatched_unit_dates": len(lhs.keys() - rhs.keys()),
        "right_unmatched_unit_dates": len(rhs.keys() - lhs.keys()),
        "qualified_full_state_pairs": 0,
        "reason": "IDENTITY_IS_NOT_COMPLETE_HELD_INPUT_STATE_OR_COUNTERFACTUAL",
    }


def screen_candidate(
    population: Population, candidate_rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Necessary coarse overlap screen, never ticker/date-only admission."""
    keys: set[tuple[str, date]] = set()
    available_fields: set[str] | None = None
    for row in candidate_rows:
        key = _text(row["security_code"]), _day(row["market_date"])
        if key in keys:
            raise PreflightError("ambiguous_candidate_code_date")
        keys.add(key)
        fields = {field for field, value in row.items() if value is not None}
        available_fields = fields if available_fields is None else available_fields & fields
    overlap = [r for r in population.identities if (r.security_code, r.market_date) in keys]
    return {
        "arm": population.arm,
        "candidate_security_dates": len(keys),
        "coarse_code_date_overlap": len(overlap),
        "overlap_issuers": len({r.security_code for r in overlap}),
        "overlap_episodes": len({r.entry_key for r in overlap}),
        "overlap_dates": len({r.market_date for r in overlap}),
        "missing_support_fields": sorted(SUPPORT_FIELDS - (available_fields or set())),
        "qualified_decision_fact_pairs": 0,
        "admission": "NOT_GRANTED",
        "reason": "NO_COARSE_OVERLAP" if not overlap else "EXACT_SOURCE_PURPOSE_BRIDGE_REQUIRED",
    }

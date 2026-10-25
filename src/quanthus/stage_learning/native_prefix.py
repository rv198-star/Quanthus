"""Project a P10-qualified native prefix into the existing retrospective controls.

The caller verifies the immutable P10 package before supplying these rows. This
adapter never qualifies source documents, infers an acquisition time, adjusts a
price, or grants reference/training/production admission. Its control mapping is
explicit: ordinary ACTIVE iff the existing daily input admission passes; the
target's sixty-session availability remains a separate downstream result.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.structure_v2_inputs import Admission, DailyInput, InputPolicy, admit_daily, digest

VERSION = "p5-qualified-native-prefix-retrospective-v1"
SHANGHAI = ZoneInfo("Asia/Shanghai")
VENUES = {
    "SSE": "venue_bec59e31eb6bb28111a10d7e6d2c689f",
    "SZSE": "venue_8c0bd9088a0dc88f28e5e7fa8efdce87",
}
Row = Mapping[str, Any]


@dataclass(frozen=True)
class NativePrefixScope:
    security_code: str
    security_id: str
    listing_id: str
    exchange: str
    first_listing: date
    through: date
    qualified_source_digest: str
    action_coverage_digest: str
    qualified_actions_digest: str


@dataclass(frozen=True)
class NativeAction:
    """Canonical, source-qualified action, effective at the ex-date's business day."""

    action_id: str
    ex_date: date
    action_type: str
    source_revision: str
    evidence_digest: str

    def identity(self) -> dict[str, str]:
        return {
            "action_id": self.action_id, "ex_date": self.ex_date.isoformat(),
            "action_type": self.action_type, "source_revision": self.source_revision,
            "evidence_digest": self.evidence_digest,
        }


def _sha(value: object) -> bool:
    return isinstance(value, str) and len(value) == 71 and value.startswith("sha256:") and all(
        c in "0123456789abcdef" for c in value[7:]
    )


def _day(value: Any) -> date:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("NATIVE_PREFIX_BUSINESS_CLOCK")
    return value.astimezone(SHANGHAI).date()


def _unique(rows: Sequence[Row], field: str) -> dict[Any, Row]:
    result: dict[Any, Row] = {}
    for row in rows:
        key = row[field]
        if key in result:
            raise ValueError("NATIVE_PREFIX_DUPLICATE_KEY")
        result[key] = row
    return result


def _contains(value: Any, identities: set[str]) -> bool:
    if isinstance(value, dict):
        return any(_contains(v, identities) for v in value.values())
    if isinstance(value, list):
        return any(_contains(v, identities) for v in value)
    return isinstance(value, str) and value in identities


def project_native_prefix(
    scope: NativePrefixScope, policy: InputPolicy, *, listings: Sequence[Row],
    calendar: Sequence[Row], bars: Sequence[Row], states: Sequence[Row],
    gaps: Sequence[Row], exceptions: Sequence[Row], actions: Sequence[NativeAction],
) -> tuple[Admission, ...]:
    """Keep every configured open session, including rejected/missing attempts.

    Inputs must already be frozen to one knowledge revision by P10. The ordinary
    control does not incorporate ST UNKNOWN or a target-specific lookback filter.
    Known gaps and unresolved keyed quality exceptions do retain fail-closed
    semantics. Source clocks are carried unchanged, never compared with T as if
    this were an actual platform-as-known reconstruction.
    """
    if (
        scope.exchange not in VENUES or policy.security_codes != (scope.security_code,)
        or not scope.security_id or not scope.listing_id
        or not date(2012, 1, 1) <= scope.first_listing <= scope.through <= date(2021, 12, 31)
        or not _sha(scope.qualified_source_digest) or not _sha(scope.action_coverage_digest)
        or not _sha(scope.qualified_actions_digest)
    ):
        raise ValueError("NATIVE_PREFIX_SCOPE")
    if len(listings) != 1:
        raise ValueError("NATIVE_PREFIX_LISTING")
    listing = listings[0]
    venue = VENUES[scope.exchange]
    if any(listing[k] != v for k, v in {
        "listing_id": scope.listing_id, "security_id": scope.security_id,
        "listing_code": scope.security_code, "venue_id": venue, "listing_currency": "CNY",
    }.items()) or _day(listing["effective_from"]) != scope.first_listing:
        raise ValueError("NATIVE_PREFIX_LISTING")
    sessions = _unique(calendar, "calendar_session_id")
    days: dict[date, Row] = {}
    for cal in sessions.values():
        if cal["venue_id"] != venue:
            raise ValueError("NATIVE_PREFIX_CALENDAR_VENUE")
        day = cal["trade_date"]
        if type(day) is not date:
            raise ValueError("NATIVE_PREFIX_CALENDAR_DATE")
        if scope.first_listing <= day <= scope.through and cal["is_open"] is True and cal[
            "session_type"
        ] == "REGULAR_DAY":
            if day in days:
                raise ValueError("NATIVE_PREFIX_DUPLICATE_SESSION_DAY")
            days[day] = cal
    if not days or scope.through not in days:
        raise ValueError("NATIVE_PREFIX_TERMINAL_SESSION")

    def observations(rows: Sequence[Row], *, bar: bool) -> dict[date, Row]:
        result: dict[date, Row] = {}
        for row in rows:
            day = _day(row["observation_start"])
            if row["listing_id"] != scope.listing_id or not (
                scope.first_listing <= day <= scope.through
            ) or day not in days:
                raise ValueError("NATIVE_PREFIX_OBSERVATION_SCOPE")
            cal = sessions.get(row["calendar_session_id"])
            if (cal is None or cal["trade_date"] != day or cal["venue_id"] != venue
                or row["calendar_session_id"] != days[day]["calendar_session_id"]):
                raise ValueError("NATIVE_PREFIX_CALENDAR_CLOSURE")
            if bar and (row["interval_size"], row["price_basis"], row["currency"]) != (
                "1d", "UNADJUSTED", "CNY",
            ):
                raise ValueError("NATIVE_PREFIX_PRICE_COORDINATE")
            if day in result:
                raise ValueError("NATIVE_PREFIX_DUPLICATE_KEY")
            result[day] = row
        return result

    bar_map = observations(bars, bar=True)
    state_map = observations(states, bar=False)
    gap_days = set()
    for gap in gaps:
        if gap["listing_id"] != scope.listing_id or gap["interval_size"] != "1d":
            raise ValueError("NATIVE_PREFIX_GAP_SCOPE")
        gap_days.add(_day(gap["affected_effective_at"]))
    exception_days = set()
    ids = {scope.security_id, scope.listing_id, scope.security_code}
    for exception in exceptions:
        # Invalid business keys must never disappear via a substring heuristic.
        key = json.loads(exception["affected_business_key_json"])
        if _contains(key, ids) and exception["resolution_state"].upper() != "RESOLVED":
            exception_days.add(_day(exception["affected_effective_at"]))
    if len({a.action_id for a in actions}) != len(actions):
        raise ValueError("NATIVE_PREFIX_DUPLICATE_ACTION")
    for action in actions:
        if (not scope.first_listing <= action.ex_date <= scope.through
            or not action.action_id or not action.action_type or not action.source_revision
            or not _sha(action.evidence_digest)):
            raise ValueError("NATIVE_PREFIX_ACTION_CLOSURE")
    ordered_actions = sorted(actions, key=lambda a: (a.ex_date, a.action_id))
    if digest([a.identity() for a in ordered_actions]) != scope.qualified_actions_digest:
        raise ValueError("NATIVE_PREFIX_ACTION_INVENTORY")
    admissions = []
    previous_epoch = None
    for index, (day, cal) in enumerate(sorted(days.items())):
        bar = bar_map.get(day)
        state = state_map.get(day)
        prefix = [a.identity() for a in ordered_actions if a.ex_date <= day]
        epoch = digest({"first_listing": scope.first_listing, "listing": scope.listing_id,
                        "complete_action_prefix": prefix})
        reason = None
        if bar is None:
            reason = "missing_daily_bar"
        elif state is None:
            reason = "missing_tradability_state"
        elif state["is_tradable"] is not True:
            reason = "nontrading_state"
        elif day in gap_days:
            reason = "known_observation_gap"
        elif day in exception_days:
            reason = "unresolved_quality_exception"
        cutoff = datetime.combine(day, time(15), SHANGHAI)
        if listing["effective_to"] is not None and cutoff >= listing["effective_to"]:
            reason = "listing_not_effective"
        src: Row = bar or {}
        artifact = src.get("source_artifact_sha256", "")
        if isinstance(artifact, str) and len(artifact) == 64:
            artifact = "sha256:" + artifact
        row = DailyInput(
            scope.security_code, day, index, cutoff, scope.security_id, scope.listing_id,
            scope.exchange, src.get("high"), src.get("low"), src.get("close"), src.get("volume"),
            src.get("source_record_id", ""), src.get("source_revision", ""), artifact,
            str(src.get("knowledge_from", "")), epoch, "final", reason,
            "corporate_action_coordinate_change" if previous_epoch is not None and (
                epoch != previous_epoch
            ) else None,
            digest(cal), digest({"coverage": scope.action_coverage_digest, "prefix": prefix}),
        )
        admissions.append(admit_daily(row, policy))
        previous_epoch = epoch
    return tuple(admissions)


def ordinary_control_rows(admissions: Sequence[Admission]) -> tuple[dict[str, Any], ...]:
    """Publish the BASE denominator before any target facts or reference labels."""
    keys = [a.key for a in admissions]
    if len(set(keys)) != len(keys):
        raise ValueError("NATIVE_PREFIX_DUPLICATE_CONTROL")
    return tuple({
        "security_code": a.row.security_code, "security_id": a.row.security_id,
        "T": a.row.market_date.isoformat(), "control_state": "ACTIVE" if a.admitted else "X",
        "reason": a.reason, "source_member_digest": a.row.member_digest,
        "historical_public_as_known_claim": False,
    } for a in admissions)

'''The P10 forward-exchange calendar (issue #743) - P12's read port.

P12 consumes ONLY the CN02 pointer
/srv/agentdock/quanthus-ops/p10-forward-calendar-v1/CURRENT_FORWARD_CALENDAR.json
(or a local exact replica for replay/tests).  The pointer names one immutable
versioned descriptor; P12 re-hashes the descriptor bytes itself and compares
them against the pointer's declared descriptor_sha256 - the pointer's word is
never taken for content (same rule as the daily-core members, audit R4).

Adoption rule (frozen 2026-11-24): only a version whose pointer-declared
available_at is at or before the configured decision cutoff may drive the run;
a version that arrived after the cutoff was not visible at decision time and
the day blocks rather than consume it.

next_session comes from the venues.SSE and venues.SZSE next_session maps -
the two mainland venues must agree; a disagreement, a null mapping, or a
session outside the descriptor's coverage window is a whole-day block.  BSE
is genuinely different: it may read status=unavailable (its own named
reason), which never blocks the day - BSE-coded new candidates are dropped
single-security with the reason on the receipt, and BSE holdings keep
holding with the alert (same disposition as the volume regime boundary).

The retired paths are gone: manifest calendar.next_session and the
dwd_trading_calendar member are no longer consulted anywhere.
'''

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

EXCHANGE_TZ = timezone(timedelta(hours=8))

POINTER_SCHEMA = "quanthus.forward-calendar-pointer.v1"
DESCRIPTOR_SCHEMA = "quanthus.forward-calendar.v1"

# CN02 default; deployments may override via env/config and tests inject a
# local replica root.
DEFAULT_CALENDAR_ROOT = Path(
    "/srv/agentdock/quanthus-ops/p10-forward-calendar-v1"
)
POINTER_NAME = "CURRENT_FORWARD_CALENDAR.json"


@dataclass(frozen=True)
class CalendarBlock:
    '''A named reason the forward calendar cannot drive the day.'''

    detail: str
    missing_member: str


@dataclass(frozen=True)
class ForwardCalendar:
    '''The verified forward calendar: identity, clocks, and venue maps.'''

    version: str
    descriptor_sha256: str          # recomputed by P12, never copied
    published_at: datetime | None   # P10-declared, verbatim
    available_at: datetime | None   # P10-declared, verbatim
    coverage_start: date | None
    coverage_end: date | None
    sse_next: dict
    szse_next: dict
    bse_status: str                 # "unavailable" expected; other values kept
    bse_reason: str | None
    descriptor: dict


def _sha256_file(path: Path) -> str:
    d = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            d.update(chunk)
    return d.hexdigest()


def _parse_ts(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def is_bse(security_code: str) -> bool:
    '''Beijing Stock Exchange codes: 4xxxxx / 8xxxxx / 920xxx (the same code
    partition as the paper desk's +/-30% band).'''
    code = str(security_code)
    return code.startswith(("4", "8", "920"))


def next_session(
    cal: ForwardCalendar, today: date
) -> tuple:
    '''(date | None, CalendarBlock | None): the agreed next open session, or
    the typed reason it cannot be named.

    SSE and SZSE must agree verbatim; a disagreement, a null mapping, or a
    value outside the descriptor's coverage blocks the day - never a guessed
    date.'''

    key = today.isoformat()
    sse = cal.sse_next.get(key)
    szse = cal.szse_next.get(key)
    venues = cal.descriptor.get("venues") or {}
    reason = None
    for name in ("SSE", "SZSE"):
        reason = reason or ((venues.get(name) or {}).get(
            "next_session_unavailable", {}) or {}).get(key)
    if sse != szse:
        return None, CalendarBlock(
            detail=(
                "SSE and SZSE disagree on the next session after "
                + key + ": " + repr(sse) + " vs " + repr(szse)
                + " - no authoritative session"
            ),
            missing_member="forward_calendar",
        )
    if sse is None:
        return None, CalendarBlock(
            detail=(
                "forward calendar maps no next session after " + key
                + (" (" + str(reason) + ")" if reason else " - outside coverage")
            ),
            missing_member="forward_calendar",
        )
    try:
        nxt = date.fromisoformat(str(sse))
    except ValueError:
        return None, CalendarBlock(
            detail="forward calendar next_session " + repr(sse) + " is not a date",
            missing_member="forward_calendar",
        )
    if cal.coverage_end is not None and nxt > cal.coverage_end:
        return None, CalendarBlock(
            detail=(
                "forward calendar next_session " + nxt.isoformat()
                + " exceeds coverage end " + cal.coverage_end.isoformat()
            ),
            missing_member="forward_calendar",
        )
    return nxt, None


def load_forward_calendar(
    calendar_root: Path,
    *,
    decision_cutoff: datetime,
) -> tuple:
    '''(ForwardCalendar | None, CalendarBlock | None, receipt_fields).

    Recompute every digest P12 consumes.  On a block, calendar is None and
    receipt_fields still carries what was actually observed - the declared
    version/clocks plus P12's own receive/read/verify stamps, null with a
    note when the event never happened.'''

    receipt: dict[str, Any] = {
        "calendar_version": None,
        "calendar_sha256": None,
        "calendar_published_at": None,
        "calendar_available_at": None,
        "calendar_received_at": None,
        "calendar_first_read_at": None,
        "calendar_verified_at": None,
        "calendar_stamp_note": None,
    }

    root = Path(calendar_root)
    pointer_path = root / POINTER_NAME
    # received_at = the moment P12 opened the pointer (the receive event).
    receipt["calendar_received_at"] = datetime.now(EXCHANGE_TZ).isoformat()
    if not pointer_path.is_file():
        receipt["calendar_stamp_note"] = (
            "pointer absent; received_at is the open attempt, first_read and "
            "verified have no event to stamp"
        )
        return None, CalendarBlock(
            detail="forward calendar pointer missing: " + str(pointer_path),
            missing_member="forward_calendar",
        ), receipt
    try:
        pointer_raw = pointer_path.read_bytes()
    except OSError as exc:
        return None, CalendarBlock(
            detail="forward calendar pointer unreadable: " + str(exc),
            missing_member="forward_calendar",
        ), receipt
    # first_read_at = the moment the pointer bytes were actually read back.
    receipt["calendar_first_read_at"] = datetime.now(EXCHANGE_TZ).isoformat()
    try:
        pointer = json.loads(pointer_raw)
    except json.JSONDecodeError as exc:
        return None, CalendarBlock(
            detail="forward calendar pointer is not JSON: " + str(exc),
            missing_member="forward_calendar",
        ), receipt
    if not isinstance(pointer, dict) or pointer.get("schema_version") != POINTER_SCHEMA:
        return None, CalendarBlock(
            detail="forward calendar pointer schema_version differs from "
            + POINTER_SCHEMA,
            missing_member="forward_calendar",
        ), receipt

    receipt["calendar_version"] = pointer.get("version")
    receipt["calendar_published_at"] = pointer.get("published_at")
    receipt["calendar_available_at"] = pointer.get("available_at")

    rel = pointer.get("descriptor")
    declared_sha = pointer.get("descriptor_sha256")
    if not isinstance(rel, str) or not rel or not isinstance(declared_sha, str):
        return None, CalendarBlock(
            detail="pointer names no descriptor path or sha256",
            missing_member="forward_calendar",
        ), receipt

    desc_path = (root / rel).resolve()
    try:
        desc_path.relative_to(root.resolve())
    except ValueError:
        return None, CalendarBlock(
            detail="descriptor " + repr(rel) + " escapes the calendar root",
            missing_member="forward_calendar",
        ), receipt
    if not desc_path.is_file():
        return None, CalendarBlock(
            detail="descriptor missing: " + str(desc_path),
            missing_member="forward_calendar",
        ), receipt

    # P12 recomputes the descriptor digest itself; the declared sha is a
    # claim to verify, never a value to copy.
    recomputed = _sha256_file(desc_path)
    actual_sha = "sha256:" + recomputed
    receipt["calendar_verified_at"] = datetime.now(EXCHANGE_TZ).isoformat()
    receipt["calendar_sha256"] = actual_sha
    # The pointer declares a bare hex digest; compare the recomputed hex,
    # never the sha256:-prefixed display form.
    if recomputed != declared_sha:
        return None, CalendarBlock(
            detail=(
                "descriptor sha256 differs from the pointer's declared "
                "descriptor_sha256 (" + str(declared_sha)[:24] + "...)"
            ),
            missing_member="forward_calendar",
        ), receipt

    try:
        descriptor = json.loads(desc_path.read_bytes())
    except json.JSONDecodeError as exc:
        return None, CalendarBlock(
            detail="descriptor is not JSON: " + str(exc),
            missing_member="forward_calendar",
        ), receipt
    if not isinstance(descriptor, dict) or descriptor.get("schema_version") != DESCRIPTOR_SCHEMA:
        return None, CalendarBlock(
            detail="descriptor schema_version differs from " + DESCRIPTOR_SCHEMA,
            missing_member="forward_calendar",
        ), receipt

    published_at = _parse_ts(pointer.get("published_at"))
    available_at = _parse_ts(pointer.get("available_at"))

    # Adoption rule: only a version available at or before the configured
    # decision cutoff was visible at decision time; a later-arriving version
    # stays unconsumed (the day blocks, never a post-cutoff rewrite).
    if available_at is None:
        return None, CalendarBlock(
            detail="pointer declares no tz-aware available_at; adoption "
            "requires a version visible at the cutoff",
            missing_member="forward_calendar",
        ), receipt
    if available_at > decision_cutoff:
        return None, CalendarBlock(
            detail=(
                "calendar version " + repr(pointer.get("version"))
                + " became available " + available_at.isoformat()
                + ", after the decision cutoff " + decision_cutoff.isoformat()
                + "; not visible at decision time"
            ),
            missing_member="forward_calendar",
        ), receipt

    venues = descriptor.get("venues") or {}

    def _venue_map(name):
        v = venues.get(name) or {}
        nxt = v.get("next_session")
        if not isinstance(nxt, dict):
            return {}
        return {
            str(k): (None if val is None else str(val))
            for k, val in nxt.items()
        }

    sse_next = _venue_map("SSE")
    szse_next = _venue_map("SZSE")
    if not sse_next or not szse_next:
        return None, CalendarBlock(
            detail="descriptor carries no SSE/SZSE next_session maps",
            missing_member="forward_calendar",
        ), receipt

    coverage = descriptor.get("coverage") or {}
    cov_start = cov_end = None
    try:
        if coverage.get("start"):
            cov_start = date.fromisoformat(str(coverage["start"]))
        if coverage.get("end"):
            cov_end = date.fromisoformat(str(coverage["end"]))
    except ValueError:
        cov_start = cov_end = None

    bse = venues.get("BSE") or {}
    cal = ForwardCalendar(
        version=str(pointer.get("version") or ""),
        descriptor_sha256=receipt["calendar_sha256"],
        published_at=published_at,
        available_at=available_at,
        coverage_start=cov_start,
        coverage_end=cov_end,
        sse_next=sse_next,
        szse_next=szse_next,
        bse_status=str(bse.get("status") or ""),
        bse_reason=bse.get("reason"),
        descriptor=descriptor,
    )
    return cal, None, receipt


def forward_calendar_provider(calendar_root: Path):
    '''Build the workbench placeholder's next_session provider over the
    forward-calendar pointer.  Same adoption rules as the job: the SHA is
    recomputed and only a version visible at read time is used.  Never
    raises into the page; an unresolvable calendar yields None and the
    placeholder stays blocked (it never invents a date).'''

    def provide(today: date):
        try:
            cal, block, _receipt = load_forward_calendar(
                calendar_root,
                decision_cutoff=datetime.now(EXCHANGE_TZ),
            )
            if cal is None:
                return None
            nxt, err = next_session(cal, today)
            return nxt if err is None else None
        except Exception:  # noqa: BLE001 - a read failure is not a session
            return None

    return provide

"""P11 H1 raw-history-input adapter: p11.raw-history-input.v1 -> RawSecurityInput.

P12 reads the qualified source members (daily history, calendar, listing facts) and
hands E1 a raw_history envelope; this adapter validates the envelope strictly, derives
the per-security universe_membership from listing intervals + venue calendar (or passes
through an authoritative source_daily_universe member verbatim), projects securities[]
into RawSecurityInput, and emits the per-security history-gate receipt destined for
source_closure.

Domain ownership: P11 computes listing age via the venue calendar, never by counting
OHLC bars or business days.  The 22/61/120 thresholds themselves stay inside the
original event scan / feature panel / row_number - this adapter only builds the input
closure those algorithms already read.  A listing fact missing from the source cannot
be inferred from the earliest bar, the file length or a weekday guess.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, NoReturn
from zoneinfo import ZoneInfo

from quanthus.product_daily.contracts import ProductDailyError
from quanthus.product_daily.recommendation.contracts import NoQualifiedRawInputs
from quanthus.product_daily.recommendation.daily_runner import (
    PendingSecurityInput,
    RawSecurityInput,
)
from quanthus.product_daily.recommendation.feature_adapter import (
    CorporateAction,
    DailyBar,
    ExceptionSpan,
    IdentityFacts,
    LimitFacts,
)

if TYPE_CHECKING:
    from quanthus.product_daily.recommendation.rule_seam import RuleDayOutput

RAW_HISTORY_SCHEMA = "p11.raw-history-input.v1"
# The three history gates, named - the thresholds live in the original algorithms;
# this adapter only reports observed membership/window lengths against them.
GATE_EVENTS = 22
GATE_FEATURES = 61
GATE_LISTING = 120


def _fail(code: str, message: str) -> NoReturn:
    raise ProductDailyError(code, message)


def _note_sealed_member(
    observed_sink: dict[str, Any],
    member_id: str,
    *,
    knowledge_at: datetime,
    visible_at: datetime | None,
    date_to: Any,
) -> None:
    """Record a post-cutoff sealed member once, keeping BOTH original clocks."""
    sealed = observed_sink.setdefault("sealed_members", [])
    existing = next((x for x in sealed if x.get("member_id") == member_id), None)
    record = {
        "member_id": member_id,
        "knowledge_at": knowledge_at.isoformat(),
        "visible_at": visible_at.isoformat() if visible_at is not None else None,
        "date_to": date_to,
        "kind": "sealed_post_cutoff_archive",
    }
    if existing is None:
        sealed.append(record)
    else:
        for k in ("knowledge_at", "visible_at", "date_to"):
            if existing.get(k) is None:
                existing[k] = record[k]


def _obj(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail("invalid_raw_history", f"{label} must be an object")
    return dict(value)


def _seq(value: Any, label: str) -> list[Any]:
    if not isinstance(value, (list, tuple)):
        _fail("invalid_raw_history", f"{label} must be a list")
    return list(value)


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        _fail("invalid_raw_history", f"{label} must be a non-empty string")
    return str(value)


def _text_or_unknown(value: Any, placeholder: str) -> str:
    """Lenient identity field for the account-member preservation path only:
    the caller's own value when present, else a named unknown placeholder.
    Never fabricates a scan/entry identity -- scan stays ineligible."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return f"unknown:{placeholder}"


def _sha256(value: Any, label: str) -> str:
    text_value = _text(value, label)
    body = text_value[len("sha256:") :] if text_value.startswith("sha256:") else text_value
    if len(body) != 64 or any(c not in "0123456789abcdef" for c in body.lower()):
        _fail("invalid_raw_history", f"{label} is not a sha256 digest")
    return "sha256:" + body.lower()


def _isodate(value: Any, label: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            _fail("invalid_raw_history", f"{label} is not an ISO date")
    _fail("invalid_raw_history", f"{label} must be an ISO date string")


def _listing_instant(value: Any, label: str, venue_id: str) -> datetime:
    """Keep a P10 listing effective_* endpoint at full instant precision.

    The DWD effective interval is a timestamp (effective_from/to carry a UTC
    instant, not a date literal), so the half-open comparison
    effective_from <= observation_start < effective_to must be evaluated on the
    instant.  A bare ISO date is promoted to the venue's local midnight - the
    declared legacy contract for date-only synthetic inputs, kept deliberately
    conservative.  A timezone-aware timestamp keeps its own clock; 'Z' and an
    explicit offset are equivalent under Python 3.10 (naive and non-finite
    values stay rejected).
    """
    tz = _venue_tz(venue_id)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            _fail("invalid_raw_history", f"{label} must carry a timezone")
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), tzinfo=tz)
    if isinstance(value, str):
        try:
            day = date.fromisoformat(value)
        except ValueError:
            day = None
        if day is not None:
            return datetime.combine(day, datetime.min.time(), tzinfo=tz)
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            _fail(
                "invalid_raw_history",
                f"{label} is not an ISO date or datetime",
            )
        if parsed.tzinfo is None:
            _fail("invalid_raw_history", f"{label} must carry a timezone")
        return parsed
    _fail("invalid_raw_history", f"{label} must be an ISO date or timestamp")


def _venue_tz(venue_id: str) -> ZoneInfo:
    """The venue's session-day timezone, from the bound venue fact.

    All currently admitted mainland venues trade on Asia/Shanghai; the mapping
    is a named fact (not inferred from a bar clock) and is the only place a
    source timestamp is turned into a session date.
    """
    if venue_id in ("SSE", "SZSE", "BSE", "XSHG", "XSHE", "XBEI"):
        return ZoneInfo("Asia/Shanghai")
    _fail("invalid_raw_history", f"no admitted session timezone for venue {venue_id}")


def _isodatetime(value: Any, label: str) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            _fail("invalid_raw_history", f"{label} must carry a timezone")
        return value
    if isinstance(value, str):
        try:
            # Python 3.10 does not accept the ISO 'Z' designator; it is
            # equivalent to +00:00 and is normalised once, here.
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            _fail("invalid_raw_history", f"{label} is not an ISO datetime")
        if parsed.tzinfo is None:
            _fail("invalid_raw_history", f"{label} must carry a timezone")
        return parsed
    _fail("invalid_raw_history", f"{label} must be an ISO datetime string")


def _decimal(value: Any, label: str, *, allow_none: bool = False) -> Decimal | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool):
        _fail("invalid_raw_history", f"{label} is not a decimal")
    try:
        parsed = Decimal(str(value))
    except Exception:
        _fail("invalid_raw_history", f"{label} is not a decimal")
    if not parsed.is_finite():
        _fail("invalid_raw_history", f"{label} is not a finite decimal")
    return parsed


def _bool3(value: Any, label: str) -> bool | None:
    if value is None:
        return None
    if type(value) is bool:
        return value
    _fail("invalid_raw_history", f"{label} must be true/false/null")


def _bool_strict(value: Any, label: str) -> bool:
    """A required source boolean: 'true'/'false' strings are never coerced."""
    if type(value) is bool:
        return value
    _fail("invalid_raw_history", f"{label} must be a boolean")


def _int_or_none(value: Any, label: str) -> int | None:
    """An account quantity: int or explicit null; never coerced from another type."""
    if value is None:
        return None
    if type(value) is bool or type(value) is not int:
        _fail("invalid_raw_history", f"{label} must be an integer or null")
    return int(value)


# --- typed envelope -----------------------------------------------------------------


@dataclass(frozen=True)
class SourceMember:
    package_id: str
    version: str
    member_path: str
    sha256: str
    date_from: date | None
    date_to: date | None
    knowledge_at: datetime
    visible_at: datetime
    member_id: str


@dataclass(frozen=True)
class CalendarRow:
    venue_id: str
    session_date: date
    is_open: bool
    # Session observation anchor (dwd_trading_calendar.open_at).  The listing
    # half-open interval effective_from <= anchor < effective_to is compared
    # against this instant, never the session date or a fabricated close time.
    open_at: datetime | None
    close_at: datetime | None
    knowledge_from: datetime
    source_member_id: str


@dataclass(frozen=True)
class ListingRow:
    security_id: str
    listing_id: str
    venue_id: str
    # The source effective interval is an INSTANT (DWD effective_from/to are UTC
    # timestamps), kept at full precision so the half-open comparison against
    # the session observation anchor matches the baseline query.  A date-only
    # value is promoted to the venue's local midnight for the declared legacy
    # contract; timestamp inputs keep their source clock.
    effective_from: datetime
    effective_to: datetime | None
    knowledge_from: datetime
    source_member_id: str


@dataclass(frozen=True)
class RawHistory:
    market_date: date
    decision_cutoff: datetime
    source_members: tuple[SourceMember, ...]
    calendar_rows: tuple[CalendarRow, ...]
    listing_rows: tuple[ListingRow, ...]
    securities: tuple[dict[str, Any], ...]
    source_members_by_id: Mapping[str, SourceMember]
    # B-segment: per-order typed pending facts (sell orders keep direction +
    # quantity verbatim) and pool-external unresolved account records.  Both
    # default to empty for strict/daily envelopes that never carried them.
    pending_securities: tuple[dict[str, Any], ...] = ()
    account_unresolved: tuple[dict[str, Any], ...] = ()


# --- strict loader ------------------------------------------------------------------


def load_raw_history(
    raw: Mapping[str, Any],
    *,
    as_observed: bool = False,
    observed_sink: dict[str, Any] | None = None,
) -> RawHistory:
    """Validate a p11.raw-history-input.v1 envelope; raise on any contract breach."""
    envelope = _obj(raw, "raw_history")
    if envelope.get("schema_version") != RAW_HISTORY_SCHEMA:
        _fail(
            "invalid_raw_history",
            f"schema_version must be {RAW_HISTORY_SCHEMA}",
        )
    market_date = _isodate(envelope.get("market_date"), "raw_history.market_date")
    decision_cutoff = _isodatetime(envelope.get("decision_cutoff"), "raw_history.decision_cutoff")

    members: list[SourceMember] = []
    member_ids: set[str] = set()
    for index, m in enumerate(_seq(envelope.get("source_members"), "source_members")):
        mo = _obj(m, f"source_members[{index}]")
        member_id = _text(mo.get("member_id"), f"source_members[{index}].member_id")
        if member_id in member_ids:
            _fail("duplicate_member", f"duplicate source_member {member_id}")
        member_ids.add(member_id)
        sha = _sha256(mo.get("sha256"), f"source_members[{index}].sha256")
        knowledge_at = _isodatetime(mo.get("knowledge_at"), f"source_members[{index}].knowledge_at")
        visible_at = _isodatetime(mo.get("visible_at"), f"source_members[{index}].visible_at")
        # A member the consumer could not have seen before the decision cutoff is not
        # admissible knowledge for this batch.  Under the versioned as-observed
        # replay mode it is kept and NAMED on the sink (never a relabelled
        # October-known input); strict still fails it.
        if visible_at > decision_cutoff:
            if not as_observed:
                _fail(
                    "source_visibility_after_cutoff",
                    f"source_member {member_id} visible_at after decision_cutoff",
                )
            if observed_sink is not None:
                _note_sealed_member(
                    observed_sink,
                    member_id,
                    knowledge_at=knowledge_at,
                    visible_at=visible_at,
                    date_to=mo.get("date_to"),
                )
        members.append(
            SourceMember(
                package_id=_text(mo.get("package_id"), f"source_members[{index}].package_id"),
                version=_text(mo.get("version"), f"source_members[{index}].version"),
                member_path=_text(mo.get("member_path"), f"source_members[{index}].member_path"),
                sha256=sha,
                date_from=(_isodate(mo["date_from"], "date_from") if mo.get("date_from") else None),
                date_to=(_isodate(mo["date_to"], "date_to") if mo.get("date_to") else None),
                knowledge_at=knowledge_at,
                visible_at=visible_at,
                member_id=member_id,
            )
        )

    calendar_rows: list[CalendarRow] = []
    seen_sessions: dict[tuple[str, date], CalendarRow] = {}
    for index, c in enumerate(_seq(envelope.get("calendar_rows"), "calendar_rows")):
        co = _obj(c, f"calendar_rows[{index}]")
        smid = _text(co.get("source_member_id"), f"calendar_rows[{index}].source_member_id")
        if smid not in member_ids:
            _fail("unknown_source_member", f"calendar_rows[{index}] -> {smid}")
        open_at = _isodatetime(co["open_at"], "open_at") if co.get("open_at") else None
        close_at = _isodatetime(co["close_at"], "close_at") if co.get("close_at") else None
        knowledge_from = _isodatetime(co["knowledge_from"], "calendar_rows[].knowledge_from")
        row = CalendarRow(
            venue_id=_text(co.get("venue_id"), f"calendar_rows[{index}].venue_id"),
            session_date=_isodate(co.get("session_date"), f"calendar_rows[{index}].session_date"),
            is_open=_bool_strict(co.get("is_open"), "calendar_rows[].is_open"),
            open_at=open_at,
            close_at=close_at,
            knowledge_from=knowledge_from,
            source_member_id=smid,
        )
        key = (row.venue_id, row.session_date)
        prior = seen_sessions.get(key)
        if prior is not None:
            # A duplicate (venue, session_date) row is only admissible when it
            # is semantically identical on every clock-bearing field - the same
            # instant for open_at and close_at (equivalent-timezone instants
            # compare equal), the same open flag, source member and knowledge
            # clock.  Two anchors for the same session that differ in any of
            # these cannot both describe its observation start, so the day is a
            # named conflict rather than "first row wins".
            if (
                prior.is_open != row.is_open
                or prior.source_member_id != row.source_member_id
                or prior.knowledge_from != row.knowledge_from
                or prior.open_at != row.open_at
                or prior.close_at != row.close_at
            ):
                _fail(
                    "conflicting_calendar_row",
                    f"calendar_rows[{index}] conflicts with an earlier "
                    f"{row.venue_id} {row.session_date} row",
                )
            # Semantically identical duplicates are normalised to a single row.
            continue
        seen_sessions[key] = row
        calendar_rows.append(row)

    listing_rows: list[ListingRow] = []
    for index, lrow in enumerate(_seq(envelope.get("listing_rows"), "listing_rows")):
        lo = _obj(lrow, f"listing_rows[{index}]")
        smid = _text(lo.get("source_member_id"), f"listing_rows[{index}].source_member_id")
        if smid not in member_ids:
            _fail("unknown_source_member", f"listing_rows[{index}] -> {smid}")
        venue = _text(lo.get("venue_id"), f"listing_rows[{index}].venue_id")
        eff_from = _listing_instant(
            lo.get("effective_from"), f"listing_rows[{index}].effective_from", venue
        )
        eff_to = (
            _listing_instant(lo["effective_to"], f"listing_rows[{index}].effective_to", venue)
            if lo.get("effective_to")
            else None
        )
        if eff_to is not None and eff_to < eff_from:
            _fail(
                "invalid_listing_interval",
                f"listing_rows[{index}] effective_to < effective_from",
            )
        listing_rows.append(
            ListingRow(
                security_id=_text(lo.get("security_id"), f"listing_rows[{index}].security_id"),
                listing_id=_text(lo.get("listing_id"), f"listing_rows[{index}].listing_id"),
                venue_id=venue,
                effective_from=eff_from,
                effective_to=eff_to,
                knowledge_from=_isodatetime(lo["knowledge_from"], "listing_rows[].knowledge_from"),
                source_member_id=smid,
            )
        )

    securities = tuple(_seq(envelope.get("securities"), "securities"))
    pending_securities = tuple(
        _seq(envelope.get("pending_securities") or (), "pending_securities")
    )
    account_unresolved = tuple(
        _seq(envelope.get("account_unresolved") or (), "account_unresolved")
    )
    return RawHistory(
        market_date=market_date,
        decision_cutoff=decision_cutoff,
        source_members=tuple(members),
        calendar_rows=tuple(calendar_rows),
        listing_rows=tuple(listing_rows),
        securities=securities,
        source_members_by_id={m.member_id: m for m in members},
        pending_securities=pending_securities,
        account_unresolved=account_unresolved,
    )


# --- membership derivation ----------------------------------------------------------


def _equivalent_day_membership(
    *,
    session: date,
    intervals: Sequence[ListingRow],
    venue_id: str,
) -> bool | None:
    """Exact membership proof for an is_open session whose open_at is absent.

    The venue's regular open is on the session's local trading day
    (SSE 09:30 / SZSE 09:15 order-acceptance, Asia/Shanghai - a named frozen
    contract, so the open instant is inside [local_midnight, next_midnight)).
    That lets us prove membership WITHOUT a stored anchor:

    * the whole day bound is strictly inside one listing interval -> count 1
    * the whole day bound is outside every interval                  -> count 0
    * the day straddles an interval start/end, or a venue/interval has no
      day bound evidence                                           -> unresolved

    Unresolved is returned as None so the caller isolates it rather than
    fabricating an age.  A present open_at keeps the exact half-open check.
    """
    tz = _venue_tz(venue_id)
    day_start = datetime.combine(session, datetime.min.time(), tzinfo=tz)
    day_end = day_start + timedelta(days=1)

    wholly_inside = False
    straddles = False
    for iv in intervals:
        # effective interval in venue-local instants; open upper bound means
        # "no end" for the day-bound test as well.
        frm = iv.effective_from.astimezone(tz)
        to = iv.effective_to.astimezone(tz) if iv.effective_to is not None else None
        if to is not None and to <= frm:
            # an empty/inverted interval is a date/identity conflict, not a gap
            continue
        # Wholly inside this interval: from <= day_start AND day_end <= to
        if frm <= day_start and (to is None or day_end <= to):
            wholly_inside = True
            continue
        # Wholly outside this interval: day_end <= frm OR day_start >= to
        if day_end <= frm or (to is not None and day_start >= to):
            continue
        # Otherwise the day intersects the interval boundary -> unresolved.
        straddles = True
    if straddles:
        return None
    return wholly_inside


def derive_universe_membership(
    *,
    listing_rows: Sequence[ListingRow],
    calendar_rows: Sequence[CalendarRow],
    security_id: str,
    listing_id: str,
    venue_id: str,
    market_date: date,
    # "strict" (R3/R4 default): a session missing open_at is unresolved and is
    # isolated when it could intersect the listing.  "equivalent_day" adds a
    # versioned day-boundary proof for the is_open=True / open_at=null rows
    # actually present in the October/November Base - when every possible open
    # instant of the session's local day is either entirely inside one listing
    # interval (count 1) or entirely outside all of them (count 0), the age is
    # still exact; only a boundary/straddling day is unresolved.
    anchor_mode: str = "strict",
    # Optional sink: when supplied and anchor_mode == "equivalent_day", each
    # session resolved by the day-boundary proof is recorded as
    # session_iso -> "equivalent_day".  Lets the receipt name the proof kind.
    proof_sink: dict[str, str] | None = None,
) -> tuple[date, ...]:
    """Universe membership = open sessions of the venue covered by the listing.

    The listing interval must match the caller-asserted identity on all three
    keys (security_id AND listing_id AND venue_id) - a row for a different
    listing of the same security cannot prove this security's sessions.

    The calendar must cover every open session from the listing's earliest
    effective_from through market_date; a calendar starting after the listing start
    cannot prove the full age and fails calendar_coverage_incomplete (the fallback
    is the named source_daily_universe member passthrough, not a fabricated count).
    Membership = each open venue session where a listing interval is active, never a
    bar-count or weekday proxy and never a T+1 backfill.
    """
    intervals = [
        row
        for row in listing_rows
        if row.security_id == security_id
        and row.listing_id == listing_id
        and row.venue_id == venue_id
    ]
    if not intervals:
        _fail(
            "listing_fact_missing",
            f"no listing interval for {security_id}/{listing_id}@{venue_id}; "
            "age cannot be inferred",
        )
    earliest_start = min(row.effective_from for row in intervals)
    # Each open venue session is anchored at its observation_start
    # (dwd_trading_calendar.open_at, an instant).  A session missing its anchor
    # cannot be half-open-compared; that session is named, never guessed from
    # the session date or the close clock.
    venue_sessions: list[tuple[date, datetime | None]] = sorted(
        (row.session_date, row.open_at)
        for row in calendar_rows
        if row.venue_id == venue_id and row.is_open and row.session_date <= market_date
    )
    # Coverage is asserted on the session DAY the listing began: the first
    # delivered open session must be on or before the listing's start session.
    # (The instant comparison belongs to membership inclusion, not coverage.)
    earliest_start_day = earliest_start.astimezone(_venue_tz(venue_id)).date()
    if not venue_sessions or venue_sessions[0][0] > earliest_start_day:
        _fail(
            "calendar_coverage_incomplete",
            f"the {venue_id} calendar does not cover the listing start "
            f"{earliest_start} for {security_id}; the full membership cannot be "
            "proven from a truncated calendar (use the named source_daily_universe "
            "member passthrough instead of fabricating an age)",
        )
    membership: list[date] = []
    missing_anchor: list[date] = []
    for session, anchor in venue_sessions:
        if anchor is None:
            # An anchor is required ONLY when the session could fall inside one
            # of the listing intervals - before the listing, after delisting,
            # or between disjoint intervals it provably cannot change the age.
            # A session outside every interval's possible day range is skipped
            # without an anchor; one that might intersect is named, never
            # guessed from the date or close clock.
            intersects = any(
                interval.effective_from.astimezone(_venue_tz(venue_id)).date()
                <= session
                <= (
                    interval.effective_to.astimezone(_venue_tz(venue_id)).date()
                    if interval.effective_to is not None
                    else session
                )
                for interval in intervals
            )
            if not intersects:
                continue
            if anchor_mode == "equivalent_day":
                verdict = _equivalent_day_membership(
                    session=session, intervals=intervals, venue_id=venue_id
                )
                if verdict is True:
                    membership.append(session)
                    if proof_sink is not None:
                        proof_sink[session.isoformat()] = "equivalent_day"
                    continue
                if verdict is False:
                    continue
                # verdict is None -> the day straddles a boundary and stays
                # unresolved.
            missing_anchor.append(session)
            continue
        for interval in intervals:
            # baseline: effective_from <= observation_start < effective_to
            if interval.effective_from <= anchor and (
                interval.effective_to is None or anchor < interval.effective_to
            ):
                membership.append(session)
                break
    if missing_anchor:
        # Anchor-missing sessions that could intersect a listing interval are a
        # per-security isolation: the listing interval is unverifiable for
        # those sessions so the receipt names them without fabricating an age.
        _fail(
            "listing_anchor_unavailable",
            f"{security_id}/{listing_id}@{venue_id}: calendar rows lack an "
            f"observation anchor for sessions {missing_anchor[:4]}",
        )
    return tuple(membership)


def listing_active_at_market_day(
    *,
    listing_rows: Sequence[ListingRow],
    calendar_rows: Sequence[CalendarRow],
    security_id: str,
    listing_id: str,
    venue_id: str,
    market_date: date,
) -> bool | None:
    """Eligibility check BEFORE aging: is T inside this security's listing interval?

    The qualified listing interval is the caller-asserted triple (security_id,
    listing_id, venue_id).  T's local open anchor is the venue calendar row for
    market_date (open_at when present, else the local-day bound proof).  A
    security whose qualified interval does NOT cover T -- e.g. delisted before
    T -- is not an entry-scan candidate at all: it must be isolated per-security,
    never force the shared "calendar cannot cover listing start" failure that
    ages a listed security needs.  A still-listed security keeps the strict
    coverage gate (the calendar must prove its full membership from the
    listing start).

    Returns True when the T open anchor/day-bound is provably inside one
    qualified interval, False when it is provably outside every interval, and
    None when the T anchor is absent and the day straddles an interval
    boundary (unresolved -- the caller treats it as unverifiable, never
    assumed listed).
    """
    intervals = [
        row
        for row in listing_rows
        if row.security_id == security_id
        and row.listing_id == listing_id
        and row.venue_id == venue_id
    ]
    if not intervals:
        return None
    tz = _venue_tz(venue_id)
    anchor_row = next(
        (
            row
            for row in calendar_rows
            if row.venue_id == venue_id and row.session_date == market_date
        ),
        None,
    )
    if anchor_row is not None and anchor_row.open_at is not None:
        anchor = anchor_row.open_at
        for iv in intervals:
            if iv.effective_from <= anchor and (
                iv.effective_to is None or anchor < iv.effective_to
            ):
                return True
        return False
    # The T session carries no stored anchor.  Before falling back to the
    # local-day bound, discharge the two whole-interval positions: if EVERY
    # qualified interval ended on a session strictly before market_date the
    # security is provably not listed at T (False); if EVERY interval begins
    # on a session strictly after market_date it is not yet listed (False).
    # Both are resolved without the day-bound proof so a 42-day null-anchor
    # warmup does not leave a clear delisted/future listing undecided.
    if all(
        iv.effective_to is not None and iv.effective_to.astimezone(tz).date() < market_date
        for iv in intervals
    ):
        return False
    if all(iv.effective_from.astimezone(tz).date() > market_date for iv in intervals):
        return False
    # No stored anchor for T's session: prove by the local-day bound.  The
    # session's whole local day inside one interval -> active; wholly outside
    # every interval -> not listed at T; straddling a boundary stays
    # unresolved (returned None so the caller fails closed per-security).
    return _equivalent_day_membership(session=market_date, intervals=intervals, venue_id=venue_id)


# --- securities[] -> RawSecurityInput ------------------------------------------------


def _identity_from(sec: Mapping[str, Any], security_index: int) -> IdentityFacts:
    ident = _obj(sec.get("identity"), f"securities[{security_index}].identity")
    return IdentityFacts(
        security_id=_text(ident.get("security_id"), "identity.security_id"),
        stock_code=_text(ident.get("stock_code"), "identity.stock_code"),
        listing_code=_text(ident.get("listing_code"), "identity.listing_code"),
        security_name=_text(ident.get("security_name"), "identity.security_name"),
        industry_id=_text(ident.get("industry_id"), "identity.industry_id"),
        venue_id=_text(ident.get("venue_id"), "identity.venue_id"),
        instrument_type=_text(ident.get("instrument_type"), "identity.instrument_type"),
        listing_id=_text(ident.get("listing_id"), "identity.listing_id"),
        company_id=_text(ident.get("company_id"), "identity.company_id"),
        depends_on_member_ids=tuple(
            _text(x, "depends_on_member_ids[]")
            for x in _seq(ident.get("depends_on_member_ids", ()), "depends_on_member_ids")
        ),
        invalidations=tuple(
            _text(x, "invalidations[]")
            for x in _seq(ident.get("invalidations", ()), "invalidations")
        ),
        price_bound=_decimal(ident.get("price_bound"), "identity.price_bound", allow_none=True),
        source_evidence_digest=_text(
            ident.get("source_evidence_digest"), "identity.source_evidence_digest"
        ),
    )


def _bars_from(sec: Mapping[str, Any], security_index: int) -> tuple[DailyBar, ...]:
    out: list[DailyBar] = []
    for bi, b in enumerate(_seq(sec.get("bars"), f"securities[{security_index}].bars")):
        bo = _obj(b, f"bars[{bi}]")
        out.append(
            DailyBar(
                security_id=_text(bo.get("security_id"), "bars[].security_id"),
                session_date=_isodate(bo.get("session_date"), "bars[].session_date"),
                open_price=_decimal(bo.get("open_price"), "bars[].open_price"),
                high_price=_decimal(bo.get("high_price"), "bars[].high_price"),
                low_price=_decimal(bo.get("low_price"), "bars[].low_price"),
                close_price=_decimal(bo.get("close_price"), "bars[].close_price"),
                volume=_decimal(bo.get("volume"), "bars[].volume"),
                amount=_decimal(bo.get("amount"), "bars[].amount"),
                is_tradable=_bool3(bo.get("is_tradable"), "bars[].is_tradable"),
            )
        )
    return tuple(out)


def _actions_from(sec: Mapping[str, Any], security_index: int) -> tuple[CorporateAction, ...]:
    out: list[CorporateAction] = []
    for ai, a in enumerate(_seq(sec.get("actions", ()), f"securities[{security_index}].actions")):
        ao = _obj(a, f"actions[{ai}]")
        out.append(
            CorporateAction(
                security_code=_text(ao.get("security_code"), "actions[].security_code"),
                effective_date=_isodate(ao.get("effective_date"), "actions[].effective_date"),
                cash_dividend_per_share=_decimal(
                    ao.get("cash_dividend_per_share"), "actions[].cash_dividend_per_share"
                ),
                bonus_share_ratio=_decimal(
                    ao.get("bonus_share_ratio"), "actions[].bonus_share_ratio"
                ),
                known_at=_isodate(ao.get("known_at"), "actions[].known_at"),
            )
        )
    return tuple(out)


def _exceptions_from(
    sec: Mapping[str, Any], security_index: int, *, october_replay: bool = False
) -> tuple[ExceptionSpan, ...]:
    out: list[ExceptionSpan] = []
    for ei, e in enumerate(
        _seq(sec.get("exceptions", ()), f"securities[{security_index}].exceptions")
    ):
        eo = _obj(e, f"exceptions[{ei}]")
        if october_replay and eo.get("ledger") == "P1_factor_pit_exception":
            # This panel builds holder returns from raw prices and signed cash/share
            # terms. Vendor factor versions remain unavailable for any dependent
            # historical calculation before their own cutoff. Preserve the ledger
            # row in the signed consumption evidence, outside corporate-action SQL.
            ident = _obj(sec.get("identity"), "exception owner")
            if (
                eo.get("exception_type") != "factor_pit_visibility"
                or eo.get("stock_code") != ident.get("listing_code")
                or (ident.get("venue_id"), eo.get("exchange")) not in {
                    ("SSE", "SH"), ("SZSE", "SZ")
                }
                or eo.get("source_revision") != "adj_factors_pit_exception_ledger_v1|bs_adj_v1"
                or "blocked_from" in eo
                or "blocked_through" in eo
            ):
                _fail("invalid_raw_history", "p1_factor_exception_identity_or_contract_mismatch")
            _isodate(eo.get("effective_date"), "factor exception effective_date")
            _isodate(eo.get("knowledge_cutoff"), "factor exception knowledge_cutoff")
            _text(eo.get("reason_code"), "factor exception reason_code")
            continue
        out.append(
            ExceptionSpan(
                security_code=_text(eo.get("security_code"), "exceptions[].security_code"),
                blocked_from=_isodate(eo.get("blocked_from"), "exceptions[].blocked_from"),
                blocked_through=(
                    _isodate(eo["blocked_through"], "exceptions[].blocked_through")
                    if eo.get("blocked_through")
                    else None
                ),
            )
        )
    return tuple(out)


def _limits_from(sec: Mapping[str, Any], security_index: int) -> tuple[LimitFacts, ...]:
    out: list[LimitFacts] = []
    for li, lf in enumerate(_seq(sec.get("limits", ()), f"securities[{security_index}].limits")):
        lo = _obj(lf, f"limits[{li}]")
        out.append(
            LimitFacts(
                session_date=_isodate(lo.get("session_date"), "limits[].session_date"),
                entry_policy_allows=_bool3(
                    lo.get("entry_policy_allows"), "limits[].entry_policy_allows"
                ),
                is_executable=_bool3(lo.get("is_executable"), "limits[].is_executable"),
                upper_limit_pct=_decimal(
                    lo.get("upper_limit_pct"), "limits[].upper_limit_pct", allow_none=True
                ),
                lower_limit_pct=_decimal(
                    lo.get("lower_limit_pct"), "limits[].lower_limit_pct", allow_none=True
                ),
                upper_limit_price=_decimal(
                    lo.get("upper_limit_price"), "limits[].upper_limit_price", allow_none=True
                ),
                lower_limit_price=_decimal(
                    lo.get("lower_limit_price"), "limits[].lower_limit_price", allow_none=True
                ),
            )
        )
    return tuple(out)


def _date_map(raw_map: Any, label: str) -> dict[date, datetime | None]:
    """A per-session instant map: date key -> aware instant, or explicit null.

    A JSON null is a DECLARED absence (the bound calendar row kept the session
    date/is_open flag but left its native instant empty), which downstream
    clock policy may refuse or admit as a date-context row -- it is not a
    parse failure.  A non-null value stays strict ISO + timezone.  Only the
    close/cutoff maps use this shape; other date maps keep their own parsers.
    """
    if not isinstance(raw_map, dict):
        _fail("invalid_raw_history", f"{label} must be an object")
    out: dict[date, datetime | None] = {}
    for key, value in raw_map.items():
        day = _isodate(key, f"{label} key")
        out[day] = None if value is None else _isodatetime(value, f"{label}[{key}]")
    return out


def _listing_floor_proof(
    *,
    listing_rows: Sequence[ListingRow],
    security_id: str,
    venue_id: str,
    membership: Sequence[date],
) -> dict[str, Any]:
    """Bounded listing-age proof (BOUNDED-WINDOW-AND-UNIVERSE-v1).

    The full membership sequence is still derived (row_number semantics preserved);
    this records HOW the 120 floor was established so a truncated 120-session proof
    window is never passed off as the complete listing age.
      AT_LEAST_120   the oldest delivered listing interval covers the first session of
                     a >=120-session proof window (floor proven, age may exceed it)
      EXACT          the listing started inside the delivered window, so the observed
                     membership length IS the exact listing age
    """
    sessions_needed = GATE_LISTING
    window = membership[-sessions_needed:]
    earliest_anchor = window[0] if window else None
    # earliest_anchor is a session DATE; the covering listing interval must
    # contain that session's observation start.  Use the venue-local midnight
    # of the anchor day as the conservative lower bound for the proof window
    # (the proof only asserts a floor, not an instant).
    covering = [
        row
        for row in listing_rows
        if row.security_id == security_id
        and row.venue_id == venue_id
        and earliest_anchor is not None
        and row.effective_from
        <= datetime.combine(earliest_anchor, datetime.min.time(), _venue_tz(row.venue_id))
    ]
    at_least = len(membership) >= sessions_needed and bool(covering)
    return {
        "kind": "AT_LEAST_120" if at_least else "EXACT",
        "observed": len(membership),
        "required": GATE_LISTING,
        "proof_window_from": None if not window else window[0].isoformat(),
        "floor_proven": len(membership) >= GATE_LISTING,
    }


def history_receipt_for_security(
    *,
    security_code: str,
    security_id: str,
    venue_id: str,
    membership: Sequence[date],
    bars: Sequence[DailyBar],
    market_date: date,
    listing_rows: Sequence[ListingRow],
    # Sessions resolved by the versioned equivalent-day proof (anchor_mode
    # "equivalent_day"), session_iso -> proof_kind.  None/empty means every
    # counted session used the exact instant anchor.  The receipt names WHICH
    # proof carried the membership so an equivalent-day count is never passed
    # off as a stored-anchor instant proof.
    membership_proof: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Per-security history gate receipt (goes into source_closure).

    observed = what the input actually carried; required = the gate floors. The three
    thresholds are reported, not recomputed - the underlying scan/panel/row_number
    decides pass/fail; this records the evidence for each, including the explicit
    bounded listing-floor proof kind.
    """
    observed_membership = len(membership)
    observed_bars = len([b for b in bars if b.session_date <= market_date])
    window_start = membership[0].isoformat() if membership else None
    window_end = membership[-1].isoformat() if membership else None
    return {
        "security_code": security_code,
        "security_id": security_id,
        "venue_id": venue_id,
        "gates": {
            "events": {"required": GATE_EVENTS, "observed": observed_bars},
            "features": {"required": GATE_FEATURES, "observed": observed_bars},
            "listing": {"required": GATE_LISTING, "observed": observed_membership},
        },
        "listing_floor_proof": _listing_floor_proof(
            listing_rows=listing_rows,
            security_id=security_id,
            venue_id=venue_id,
            membership=membership,
        ),
        # How each counted session was proven: "exact_instant" when a stored
        # open_at anchor carried the half-open check; "equivalent_day" when the
        # local day bound was proven wholly inside one listing interval.
        "membership_proof": (
            {
                "kind": "mixed" if membership_proof else "exact_instant",
                "anchor_mode": "equivalent_day" if membership_proof else "strict",
                "equivalent_day_sessions": (sorted(membership_proof) if membership_proof else []),
                "source": "dwd_trading_calendar.open_at | local-day-bound",
            }
        ),
        "membership_count": observed_membership,
        "bar_count": observed_bars,
        "membership_window": {"from": window_start, "to": window_end},
        "status": "observed",
    }


def _position_facts_from(
    sec: Mapping[str, Any],
) -> tuple[Any, int | None, str | None]:
    """Project the caller-declared position facts through, unchanged.

    position_facts arrives already typed (the P12 RunnerRequest projection passes
    the real HeldPositionFacts object).  A versioned serializable dict is the
    B-segment wire shape: it is adapted into a typed HeldPositionFacts when it
    carries the required named fields (holding_sessions int > 0, optional
    marked_return, prior_strategy_reduction_count int >= 0, and an evidence
    unit_id); an unqualified dict stays a contract breach.  An already-typed
    object passes through unchanged.
    """
    unverifiable: str | None = None
    pf = sec.get("position_facts")
    if pf is not None and isinstance(pf, dict):
        pf, unverifiable = _adapt_position_facts_dict(pf, sec)
    pds = sec.get("prior_deterioration_sessions")
    if pds is not None and (type(pds) is not int or pds < 0):
        _fail(
            "invalid_raw_history",
            "securities[].prior_deterioration_sessions must be a non-negative int",
        )
    return pf, pds, unverifiable


def _position_facts_ref_from_evidence(
    evidence: Any,
    *,
    holding: int,
    marked_return: Decimal | None,
    reductions: int,
) -> tuple[Any | None, str | None]:
    """Verify a serializable position_facts.evidence block against the
    existing ExpertEvidenceRef contract.

    Returns (ref, None) on a fully bound same-read reference, else
    (None, named_reason) so the caller degrades the member to a hold_alert
    rather than a whole-day block.  The verification never fabricates a
    digest to wash a source clean:

    * kind must be the canonical snapshot kind;
    * evidence_id must be position-facts:{unit_id} (unit_id recovered from
      it when the wire carries only the four canonical fields);
    * version must be the position-facts contract version 1.0.0;
    * digest must be a sha256 that recomputes EXACTLY over the declared
      value payload {unit_id, holding_sessions, marked_return,
      prior_strategy_reduction_count}; a changed value with an unchanged or
      bogus digest fails;
    * the bound unit_id must name the scenario account/read the position
      facts came from (the same-read anchor); a bare unit_id without the
      canonical reference cannot be bound and is rejected.
    """
    from quanthus.expert_evaluation.serialization import digest_value
    from quanthus.expert_protocols import EvidenceKind
    from quanthus.expert_strategy.runner import position_facts_evidence

    if not isinstance(evidence, Mapping):
        return None, "position_facts.evidence is not a same-read reference"
    unit_id = evidence.get("unit_id")
    if isinstance(unit_id, str) and not unit_id.strip():
        unit_id = None
    kind = evidence.get("kind")
    evidence_id = evidence.get("evidence_id")
    version = evidence.get("version")
    digest = evidence.get("digest")
    has_canonical_ref = (
        kind is not None
        or evidence_id is not None
        or version is not None
        or digest is not None
    )
    if not has_canonical_ref:
        if unit_id:
            return None, (
                "position_facts.evidence.unit_id alone cannot bind the "
                "same-read account source (needs kind/evidence_id/version/digest)"
            )
        return None, "position_facts.evidence is missing the same-read reference"
    if kind != EvidenceKind.SNAPSHOT.value:
        return None, f"position_facts.evidence.kind must be snapshot, got {kind!r}"
    if not isinstance(evidence_id, str) or not evidence_id.startswith("position-facts:"):
        return None, (
            "position_facts.evidence.evidence_id must be position-facts:{unit_id}"
        )
    ref_unit = evidence_id[len("position-facts:") :]
    if unit_id is not None and unit_id != ref_unit:
        return None, (
            "position_facts.evidence.unit_id disagrees with evidence_id "
            "(same-read source must be one identity)"
        )
    unit_id = unit_id or ref_unit
    if version != "1.0.0":
        return None, f"position_facts.evidence.version must be 1.0.0, got {version!r}"
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        return None, "position_facts.evidence.digest must be a sha256 digest"
    expected = digest_value(
        {
            "unit_id": unit_id,
            "holding_sessions": holding,
            "marked_return": None if marked_return is None else str(marked_return),
            "prior_strategy_reduction_count": reductions,
        }
    )
    if digest != expected:
        return None, (
            "position_facts.evidence.digest does not recompute over the declared "
            "value payload (wrong digest or changed value)"
        )
    ref = position_facts_evidence(
        unit_id=unit_id,
        holding_sessions=holding,
        marked_return=marked_return,
        prior_strategy_reduction_count=reductions,
    )
    return ref, None


def _adapt_position_facts_dict(
    pf: Mapping[str, Any], sec: Mapping[str, Any]
) -> tuple[Any | None, str | None]:
    """Adapt a versioned serializable position_facts dict to HeldPositionFacts.

    Returns (HeldPositionFacts, None) when the declared evidence verifies as
    a same-read ExpertEvidenceRef; returns (None, named_reason) when the
    evidence cannot be bound so the member degrades to a hold_alert rather
    than fabricating an exit or aborting the day.  The three numeric fields
    keep their original strictness: a missing/invalid holding_sessions or
    prior_strategy_reduction_count is a contract breach, not a degrade.
    """
    from quanthus.expert_strategy.position_risk import HeldPositionFacts

    holding = pf.get("holding_sessions")
    if type(holding) is not int or holding <= 0:
        _fail(
            "invalid_raw_history",
            "securities[].position_facts.holding_sessions must be a positive int",
        )
    reductions = pf.get("prior_strategy_reduction_count")
    if type(reductions) is not int or reductions < 0:
        _fail(
            "invalid_raw_history",
            "securities[].position_facts.prior_strategy_reduction_count must be "
            "a non-negative int",
        )
    marked = pf.get("marked_return")
    marked_return = None
    if marked is not None:
        try:
            marked_return = Decimal(str(marked))
        except Exception:
            _fail(
                "invalid_raw_history",
                "securities[].position_facts.marked_return must be decimal-parseable",
            )
        if not marked_return.is_finite():
            _fail(
                "invalid_raw_history",
                "securities[].position_facts.marked_return must be finite",
            )
    ref, unverifiable = _position_facts_ref_from_evidence(
        pf.get("evidence"),
        holding=holding,
        marked_return=marked_return,
        reductions=reductions,
    )
    if ref is None:
        return None, unverifiable
    return HeldPositionFacts(
        holding_sessions=holding,
        marked_return=marked_return,
        prior_strategy_reduction_count=reductions,
        evidence=ref,
    ), None


def raw_security_inputs_from_history(
    history: RawHistory,
    *,
    anchor_mode: str = "strict",
    proof_sinks: dict[str, dict[str, str]] | None = None,
    as_observed: bool = False,
) -> tuple[list[RawSecurityInput], list[dict[str, Any]]]:
    """Project raw_history.securities[] into RawSecurityInput[] + receipts.

    Every securities[] item is parsed inside its own error boundary so a malformed
    neighbour is isolated while a valid one keeps its real entry path.  An account
    member (held_membership=True / quantity / typed pending) is preserved into a
    RawSecurityInput even when its market facts are missing: the downstream account
    normalisation converts that into a hold+alert, never silently dropped.  An
    all-fail input produces zero RawSecurityInput plus explicit reasons - never a
    manufactured qualified-empty pass.
    """
    inputs: list[RawSecurityInput] = []
    receipts: list[dict[str, Any]] = []
    # The source calendar's own knowledge_from keyed by (venue, session_date):
    # used to prove a per-bar knowledge_cutoff is the source calendar VERSION
    # clock under the calendar_source_knowledge_from role.  Only consulted
    # inside the versioned as-observed replay.
    cal_knowledge: dict[tuple[str, date], datetime] = {}
    if as_observed:
        for row in history.calendar_rows:
            cal_knowledge[(row.venue_id, row.session_date)] = row.knowledge_from
    # Duplicate guard keys on the LISTING id (a relisted entity reuses
    # security_id across listings); see the check below.
    seen_ids: set[str] = set()
    for si, sec in enumerate(history.securities):
        sec_o = _obj(sec, f"securities[{si}]")
        # Read row-level account markers BEFORE the strict identity gate: a
        # known held/pending member whose market identity fails to parse must
        # still surface its account review (hold_alert / pending isolation)
        # rather than collapse into a bare securities[i] exclusion.  Account
        # facts are the caller-declared truth for this read; a broken market
        # row never fabricates a legal identity or a scan entry.
        position_facts, prior_det, pf_unverifiable = _position_facts_from(sec_o)
        held_membership = _bool3(sec_o.get("held_membership"), "securities[].held_membership")
        pending_kind = (
            _text(sec_o["pending_kind"], "securities[].pending_kind")
            if sec_o.get("pending_kind") is not None
            else None
        )
        held_reason = (
            _text(sec_o["held_unavailable_reason"], "held_unavailable_reason")
            if sec_o.get("held_unavailable_reason") is not None
            else None
        )
        held_qty = _int_or_none(sec_o.get("held_quantity"), "held_quantity")
        pending_qty = _int_or_none(sec_o.get("pending_quantity"), "pending_quantity")
        is_account_member = (
            held_membership is True
            or held_qty is not None
            or position_facts is not None
            or pending_kind is not None
            or bool(_seq(sec_o.get("pending") or (), f"securities[{si}].pending"))
        )
        try:
            identity = _identity_from(sec_o, si)
        except ProductDailyError as exc:
            if not is_account_member:
                receipts.append(
                    {
                        "security_code": f"securities[{si}]",
                        "status": "excluded",
                        "reason": f"identity_parse: {exc}",
                        "scope": "per_security",
                    }
                )
                continue
            # Preserve the minimal real account record: keep the declared
            # held/pending facts under a non-scan identity (the original bad
            # digest is kept verbatim, never washed) so the account
            # projection still surfaces hold_alert / pending isolation.  No
            # market identity is invented: every missing field falls back to
            # a named unknown placeholder and scan_eligible stays False.
            raw_id = sec_o.get("identity") or {}
            identity = IdentityFacts(
                security_id=_text_or_unknown(raw_id.get("security_id"), f"securities[{si}]"),
                stock_code=_text_or_unknown(
                    raw_id.get("stock_code") or raw_id.get("listing_code"),
                    f"securities[{si}]",
                ),
                listing_code=_text_or_unknown(
                    raw_id.get("listing_code") or raw_id.get("stock_code"),
                    f"securities[{si}]",
                ),
                security_name=_text_or_unknown(raw_id.get("security_name"), f"securities[{si}]"),
                industry_id=_text_or_unknown(raw_id.get("industry_id"), f"securities[{si}]"),
                venue_id=_text_or_unknown(raw_id.get("venue_id"), f"securities[{si}]"),
                instrument_type=_text_or_unknown(
                    raw_id.get("instrument_type"), f"securities[{si}]"
                ),
                listing_id=_text_or_unknown(raw_id.get("listing_id"), f"securities[{si}]"),
                company_id=_text_or_unknown(raw_id.get("company_id"), f"securities[{si}]"),
                depends_on_member_ids=(),
                invalidations=(),
                price_bound=None,
                source_evidence_digest=(
                    raw_id.get("source_evidence_digest")
                    if isinstance(raw_id.get("source_evidence_digest"), str)
                    else ""
                ),
            )
            receipts.append(
                {
                    "security_code": identity.listing_code,
                    "status": "account_member_market_unavailable",
                    "reason": f"identity_parse: {exc}",
                    "scope": "per_security",
                }
            )
            inputs.append(
                RawSecurityInput(
                    identity=identity,
                    bars=(),
                    universe_membership=(),
                    actions=(),
                    exceptions=(),
                    limits=(),
                    close_at_by_date={},
                    knowledge_cutoff_by_date={},
                    position_facts=position_facts,
                    prior_deterioration_sessions=prior_det,
                    held_membership=held_membership,
                    pending_kind=pending_kind,
                    held_unavailable_reason=pf_unverifiable or held_reason,
                    held_quantity=held_qty,
                    pending_quantity=pending_qty,
                    scan_eligible=False,
                )
            )
            continue
        code = identity.listing_code
        # Duplicate guard keys on the LISTING id, not the bare security_id: one
        # corporate entity legitimately carries several listings over time (a
        # delisted + a relisted share, e.g. security_7ca1ec46 spanning 000043 /
        # 001914).  Rejecting on security_id alone would hard-block the whole
        # day on that relisted shape even though each listing is aged by its
        # own listing_id interval and isolated per-listing.  The same
        # listing_id twice stays a hard error (a true duplicate row).
        if identity.listing_id in seen_ids:
            _fail(
                "duplicate_security",
                f"duplicate listing_id {identity.listing_id} (security_id {identity.security_id})",
            )
        seen_ids.add(identity.listing_id)
        # A same-read evidence failure degrades the member to a named
        # hold_alert reason, never a fabricated exit or a whole-day block.
        if pf_unverifiable and not held_reason:
            held_reason = pf_unverifiable
        # Versioned #749 role.  "calendar_source_knowledge_from" declares the
        # row's decision coordinate is the source calendar knowledge_from; it
        # is bound verbatim into the input closure and admitted downstream only
        # inside the versioned as-observed replay context.  Read BEFORE the
        # market try so an unknown role is its own named exclusion, never a
        # mislabelled market_parse failure.
        knowledge_clock_role = sec_o.get("knowledge_clock_role")
        if knowledge_clock_role is not None and knowledge_clock_role != (
            "calendar_source_knowledge_from"
        ):
            receipts.append(
                {
                    "security_code": code,
                    "status": "excluded",
                    "reason": (
                        f"knowledge_clock_role: {knowledge_clock_role!r} is not a named clock role"
                    ),
                    "scope": "per_security",
                }
            )
            continue
        sec_proof: dict[str, str] = {}
        try:
            # Listing-on-T eligibility is checked BEFORE aging: a security
            # provably not listed on the signal date (delisted, not yet
            # listed, or inside a gap between two listing intervals) is a
            # normal universe-census result and is isolated per-security with
            # the stable not_listed_on_signal_date reason -- never a
            # downstream missing_signal_membership / raw_input_closure_invalid
            # and never a fabricated age.
            t_active = listing_active_at_market_day(
                listing_rows=history.listing_rows,
                calendar_rows=history.calendar_rows,
                security_id=identity.security_id,
                listing_id=identity.listing_id,
                venue_id=identity.venue_id,
                market_date=history.market_date,
            )
            if t_active is False:
                if is_account_member:
                    # A held/pending position in a T-not-listed security is a
                    # single named census receipt, not a scan input: skip the
                    # membership derivation AND the bar/action market-parse
                    # entirely so it is never re-derived into a contradictory
                    # membership or a downstream missing_signal_membership.  It
                    # still lands as an empty-fact RawSecurityInput so the held
                    # reminder (hold_alert) fires and the audit row is non-empty.
                    receipts.append(
                        {
                            "security_code": code,
                            "status": "account_member_market_unavailable",
                            "reason": "not_listed_on_signal_date",
                            "scope": "per_security",
                        }
                    )
                    inputs.append(
                        RawSecurityInput(
                            identity=identity,
                            bars=(),
                            universe_membership=(),
                            actions=(),
                            exceptions=(),
                            limits=(),
                            close_at_by_date={},
                            knowledge_cutoff_by_date={},
                            position_facts=position_facts,
                            prior_deterioration_sessions=prior_det,
                            held_membership=held_membership,
                            pending_kind=pending_kind,
                            held_unavailable_reason=held_reason,
                            held_quantity=held_qty,
                            pending_quantity=pending_qty,
                            scan_eligible=False,
                        )
                    )
                    continue
                else:
                    receipts.append(
                        {
                            "security_code": code,
                            "status": "excluded",
                            "reason": "not_listed_on_signal_date",
                            "scope": "per_security",
                        }
                    )
                    continue
            membership = derive_universe_membership(
                listing_rows=history.listing_rows,
                calendar_rows=history.calendar_rows,
                security_id=identity.security_id,
                listing_id=identity.listing_id,
                venue_id=identity.venue_id,
                market_date=history.market_date,
                anchor_mode=anchor_mode,
                proof_sink=sec_proof,
            )
            if proof_sinks is not None and sec_proof:
                proof_sinks[identity.listing_code] = sec_proof
        except ProductDailyError as exc:
            rc = getattr(exc, "reason_code", "")
            if rc == "calendar_coverage_incomplete":
                # A venue with NO open sessions at all is a shared whole-venue
                # gap: every security on it is unverifiable, so the failure is
                # shared, not per-security.  Re-raise it so the day fails
                # closed; a per-security truncated-calendar isolation stays in
                # the branch below.
                venue_has_sessions = any(
                    row.venue_id == identity.venue_id and row.is_open
                    for row in history.calendar_rows
                )
                if not venue_has_sessions:
                    raise
                if is_account_member:
                    membership = ()
                    receipts.append(
                        {
                            "security_code": code,
                            "status": "account_member_market_unavailable",
                            "reason": str(exc),
                            "scope": "per_security",
                        }
                    )
                else:
                    receipts.append(
                        {
                            "security_code": code,
                            "status": "excluded",
                            "reason": str(exc),
                            "scope": "per_security",
                        }
                    )
                    continue
            elif is_account_member:
                membership = ()
                receipts.append(
                    {
                        "security_code": code,
                        "status": "account_member_market_unavailable",
                        "reason": str(exc),
                        "scope": "per_security",
                    }
                )
            else:
                receipts.append(
                    {
                        "security_code": code,
                        "status": "excluded",
                        "reason": str(exc),
                        "scope": "per_security",
                    }
                )
                continue
        try:
            bars = _bars_from(sec_o, si)
            actions = _actions_from(sec_o, si)
            exceptions = _exceptions_from(sec_o, si, october_replay=as_observed)
            limits = _limits_from(sec_o, si)
            close_map = _date_map(sec_o.get("close_at_by_date", {}), "close_at_by_date")
            cutoff_map = _date_map(
                sec_o.get("knowledge_cutoff_by_date", {}), "knowledge_cutoff_by_date"
            )
        except ProductDailyError as exc:
            if is_account_member:
                bars, actions, exceptions, limits = (), (), (), ()
                close_map, cutoff_map = {}, {}
                receipts.append(
                    {
                        "security_code": code,
                        "status": "account_member_market_unavailable",
                        "reason": f"market_parse: {exc}",
                        "scope": "per_security",
                    }
                )
            else:
                receipts.append(
                    {
                        "security_code": code,
                        "status": "excluded",
                        "reason": f"market_parse: {exc}",
                        "scope": "per_security",
                    }
                )
                continue
        if membership:
            receipts.append(
                history_receipt_for_security(
                    security_code=code,
                    security_id=identity.security_id,
                    venue_id=identity.venue_id,
                    membership=membership,
                    bars=bars,
                    market_date=history.market_date,
                    listing_rows=history.listing_rows,
                    membership_proof=sec_proof,
                )
            )
        # Versioned as-observed: prove WHICH rows carry the source calendar
        # VERSION clock by matching each per-bar knowledge_cutoff against the
        # SAME venue+session_date calendar_row.knowledge_from.  Only a row
        # whose cutoff is that exact source stamp moves the PIT coordinate to
        # the business fact instant downstream; an ordinary/mismatched cutoff
        # keeps its original identity (and a claimed role with no matching
        # source row names a real exclusion, never a silently-adopted label).
        matched_dates: frozenset[date] | None = None
        if as_observed and knowledge_clock_role == "calendar_source_knowledge_from":
            unmatched = [
                d
                for d, v in cutoff_map.items()
                if v is not None and cal_knowledge.get((identity.venue_id, d)) != v
            ]
            if unmatched:
                receipts.append(
                    {
                        "security_code": code,
                        "status": "excluded",
                        "reason": (
                            "calendar_source_knowledge_from cutoff is not the "
                            f"source calendar knowledge_from for {unmatched[:3]}"
                        ),
                        "scope": "per_security",
                    }
                )
                continue
            matched_dates = frozenset(d for d, v in cutoff_map.items() if v is not None)
        inputs.append(
            RawSecurityInput(
                identity=identity,
                bars=bars,
                universe_membership=membership,
                actions=actions,
                exceptions=exceptions,
                limits=limits,
                close_at_by_date=close_map,
                knowledge_cutoff_by_date=cutoff_map,
                knowledge_clock_role=knowledge_clock_role,
                source_calendar_matched_dates=matched_dates,
                position_facts=position_facts,
                prior_deterioration_sessions=prior_det,
                held_membership=held_membership,
                pending_kind=pending_kind,
                held_unavailable_reason=held_reason,
                held_quantity=held_qty,
                pending_quantity=pending_qty,
                scan_eligible=sec_o.get("scan_eligible") is not False,
            )
        )
    return inputs, receipts


# --- envelope identity + facts/policy binding ---------------------------------------


def raw_history_digest(history: RawHistory) -> str:
    """Digest the complete accepted raw_history envelope identity.

    Every byte that can change the derived membership or the projected
    RawSecurityInput is bound: source members (id, sha256, knowledge_at, visible_at,
    coverage), calendar rows (venue/date/is_open/open/close/knowledge), listing rows
    (security/venue/interval/knowledge), market_date and decision_cutoff.  A change
    to any of these alters the job identity even when the projected features do not
    move - matching the original panel's source-closure requirement.
    """
    from quanthus.event_library.contracts import sha256_digest

    payload = {
        "schema_version": RAW_HISTORY_SCHEMA,
        "market_date": history.market_date.isoformat(),
        "decision_cutoff": history.decision_cutoff.isoformat(),
        "source_members": [
            {
                "member_id": m.member_id,
                "package_id": m.package_id,
                "version": m.version,
                "member_path": m.member_path,
                "sha256": m.sha256,
                "date_from": m.date_from.isoformat() if m.date_from else None,
                "date_to": m.date_to.isoformat() if m.date_to else None,
                "knowledge_at": m.knowledge_at.isoformat(),
                "visible_at": m.visible_at.isoformat(),
            }
            for m in sorted(history.source_members, key=lambda x: x.member_id)
        ],
        "calendar_rows": [
            {
                "venue_id": c.venue_id,
                "session_date": c.session_date.isoformat(),
                "is_open": c.is_open,
                "open_at": c.open_at.isoformat() if c.open_at else None,
                "close_at": c.close_at.isoformat() if c.close_at else None,
                "knowledge_from": c.knowledge_from.isoformat(),
                "source_member_id": c.source_member_id,
            }
            for c in sorted(history.calendar_rows, key=lambda x: (x.venue_id, x.session_date))
        ],
        "listing_rows": [
            {
                "security_id": lrow.security_id,
                "listing_id": lrow.listing_id,
                "venue_id": lrow.venue_id,
                "effective_from": lrow.effective_from.isoformat(),
                "effective_to": lrow.effective_to.isoformat() if lrow.effective_to else None,
                "knowledge_from": lrow.knowledge_from.isoformat(),
                "source_member_id": lrow.source_member_id,
            }
            for lrow in sorted(
                history.listing_rows, key=lambda x: (x.security_id, x.effective_from)
            )
        ],
    }
    return str(sha256_digest(payload))


def bind_raw_history_to_run(
    history: RawHistory,
    *,
    facts: Any,
    policy: Any,
    as_observed: bool = False,
    observed_sink: dict[str, Any] | None = None,
) -> None:
    """Assert the envelope is the declared batch for THIS run, fail closed.

    The envelope is not self-declared: its market_date must equal facts.market_date
    and its decision_cutoff must equal policy.decision_cutoff (the predeclared
    consumer deadline, never a max(visible_at) re-derivation).  Every source member
    must satisfy knowledge_at <= cutoff and visible_at <= cutoff, every
    required_member_id in facts must be carried by a named source_member, and every
    calendar/listing knowledge_from must be <= cutoff.  A mismatch is a shared
    whole-day failure, never a relabelled pass.
    """
    if history.market_date != facts.market_date:
        _fail(
            "envelope_market_date_mismatch",
            f"raw_history.market_date {history.market_date} != facts.market_date "
            f"{facts.market_date}",
        )
    if history.decision_cutoff != policy.decision_cutoff:
        _fail(
            "envelope_decision_cutoff_mismatch",
            f"raw_history.decision_cutoff {history.decision_cutoff} != "
            f"policy.decision_cutoff {policy.decision_cutoff}; the envelope may not "
            "declare its own deadline",
        )
    for m in history.source_members:
        if m.knowledge_at > policy.decision_cutoff:
            if not as_observed:
                _fail(
                    "source_knowledge_after_cutoff",
                    f"source_member {m.member_id} knowledge_at {m.knowledge_at} exceeds "
                    f"the run decision_cutoff {policy.decision_cutoff}",
                )
            if observed_sink is not None:
                _note_sealed_member(
                    observed_sink,
                    m.member_id,
                    knowledge_at=m.knowledge_at,
                    visible_at=m.visible_at,
                    date_to=(m.date_to.isoformat() if m.date_to else None),
                )
    required = set(getattr(facts, "required_member_ids", ()) or ())
    supplied = {m.member_id for m in history.source_members}
    missing = required - supplied
    if missing:
        _fail(
            "required_member_not_supplied",
            f"raw_history does not carry required member(s) {sorted(missing)}",
        )
    for row in history.calendar_rows:
        if row.knowledge_from > policy.decision_cutoff:
            if not as_observed:
                _fail(
                    "calendar_knowledge_after_cutoff",
                    f"calendar {row.venue_id} {row.session_date} knowledge_from "
                    f"{row.knowledge_from} exceeds the run decision_cutoff",
                )
            if observed_sink is not None:
                observed_sink.setdefault("sealed_calendar", []).append(
                    {
                        "venue_id": row.venue_id,
                        "session_date": row.session_date.isoformat(),
                        "knowledge_from": row.knowledge_from.isoformat(),
                        "kind": "sealed_post_cutoff_archive",
                    }
                )
    for lrow in history.listing_rows:
        if lrow.knowledge_from > policy.decision_cutoff:
            if not as_observed:
                _fail(
                    "listing_knowledge_after_cutoff",
                    f"listing {lrow.security_id}@{lrow.venue_id} knowledge_from "
                    f"{lrow.knowledge_from} exceeds the run decision_cutoff",
                )
            if observed_sink is not None:
                observed_sink.setdefault("sealed_listings", []).append(
                    {
                        "security_id": lrow.security_id,
                        "venue_id": lrow.venue_id,
                        "knowledge_from": lrow.knowledge_from.isoformat(),
                        "kind": "sealed_post_cutoff_archive",
                    }
                )


# --- runner-facing convenience -------------------------------------------------------


def run_daily_advice_from_history(
    raw_history: Mapping[str, Any],
    **kwargs: Any,
) -> RuleDayOutput:
    """Strict-load a p11.raw-history-input.v1 envelope and run the existing raw chain.

    The envelope is bound to THIS run before any evaluation: market_date must equal
    facts.market_date, decision_cutoff must equal policy.decision_cutoff, every
    source member's knowledge_at AND visible_at must be <= the cutoff, and every
    required_member_id must be supplied.  The complete raw_history identity is
    digested into source_closure so a source/clock/member change drifts the job
    identity rather than silently reusing a pass.  All other kwargs pass through.
    """
    from quanthus.product_daily.recommendation.daily_runner import (
        run_daily_advice_from_raw,
    )

    # Feature-panel dependency preflight (Owner freeze 2026-11-25): every
    # scan-eligible security's feature row computes through
    # feature_adapter's DuckDB query.  A missing dependency must surface as
    # ONE named whole-day block BEFORE the per-security loop - never as
    # thousands of buried raw_input_closure_invalid entries collapsing into
    # an uninformative missing_raw_inputs day_block.
    try:
        import duckdb as _duckdb_preflight  # noqa: F401
    except ModuleNotFoundError as exc:
        _fail(
            "feature_adapter_dependency",
            "duckdb is required for the feature panel; the runtime "
            "environment lacks it, so no security could be evaluated",
        )

    # Versioned as-observed replay mode (explicit consumer config, strict is
    # the default): members/calendar/listing sealed AFTER the logical decision
    # cutoff are admitted as named post-cutoff archives on observed_sink, never
    # relabelled October-known.  Their business content date is checked by the
    # caller before entry; this only keeps the archival clocks honest.
    as_observed = kwargs.pop("as_observed", False)
    observed_sink = kwargs.pop("observed_sink", None)
    history = load_raw_history(raw_history, as_observed=as_observed, observed_sink=observed_sink)
    facts = kwargs.get("facts")
    policy = kwargs.get("policy")
    if facts is None or policy is None:
        _fail(
            "invalid_raw_history",
            "run_daily_advice_from_history requires facts and policy",
        )
    bind_raw_history_to_run(
        history,
        facts=facts,
        policy=policy,
        as_observed=as_observed,
        observed_sink=observed_sink,
    )
    # Versioned anchor policy: "strict" keeps the R3/R4 stored-anchor gate;
    # "equivalent_day" additionally resolves an is_open session whose open_at
    # is absent when its whole local day is provably inside/outside the listing.
    # The mode is a consumer-side evaluation config, never a data rewrite, and
    # each resolved session is named on the receipt's membership_proof.
    anchor_mode = kwargs.pop("anchor_mode", "strict")
    proof_sinks: dict[str, dict[str, str]] = {}
    inputs, receipts = raw_security_inputs_from_history(
        history,
        anchor_mode=anchor_mode,
        proof_sinks=proof_sinks,
        as_observed=as_observed,
    )
    envelope_identity = raw_history_digest(history)
    bound_receipts = [{**receipt, "raw_history_digest": envelope_identity} for receipt in receipts]
    # Convert adapter-level exclusions into the runner's input_exclusions so a bad
    # neighbour is a named isolation inside source_closure, never silently dropped.
    # Adapter-level exclusions are named isolations INSIDE the issued closure,
    # not appended after receipt issuance (a post-hoc dict replace would leave
    # receipt.source_closure_digest pointing at the pre-exclusion bytes).
    def _adapter_exclusion(receipt: Mapping[str, Any]) -> dict[str, Any]:
        if receipt.get("status") == "excluded":
            return {
                "security_code": receipt["security_code"],
                "reason": "raw_history_security_invalid",
                "member_or_field": receipt.get("reason", "unparseable securities[] entry"),
                "scope": "per_security",
            }
        # A held/pending account member provably not listed on T is a normal
        # universe-census result, named once inside the issued closure.  The
        # held alert and the receipt still come through; only the market scan
        # is skipped (scan_eligible=False on its RawSecurityInput).
        return {
            "security_code": receipt["security_code"],
            "reason": "not_listed_on_signal_date",
            "member_or_field": "held_account_member: "
            + receipt.get("reason", "not_listed_on_signal_date"),
            "scope": "per_security",
        }

    adapter_exclusions = [
        _adapter_exclusion(receipt)
        for receipt in receipts
        if receipt.get("status") == "excluded"
        or (
            receipt.get("status") == "account_member_market_unavailable"
            and receipt.get("reason") == "not_listed_on_signal_date"
        )
    ]
    runner_exclusions = [*(adapter_exclusions), *(kwargs.pop("input_exclusions", None) or ())]
    # B-segment pool-external account records: a held role surfaces a hold
    # review (hold_alert with the raw key named), a pending role stays
    # pending-isolated; neither admits a new entry or an exit decision for
    # that key.  They are named per-record inside the issued source_closure
    # via the account_unresolved closure field, never silently dropped.
    account_unresolved_records = [
        dict(r) for r in history.account_unresolved
    ]
    if not inputs and not account_unresolved_records:
        raise NoQualifiedRawInputs(
            "raw_history produced no qualified security inputs; this is a shared "
            "block, never a fabricated qualified-empty day",
            runner_exclusions,
        )
    # The single-T numeric context is established only inside the versioned
    # as-observed replay entry; a strict/forward/H1/M2 caller supplying it is
    # a contract breach, never a silent widening.  Under as_observed the
    # context is fixed (not a caller-chosen string), so no anchor or boolean
    # substitute is accepted elsewhere.  The public raw entry admits the
    # private OctoberReplayContext token only - this entry mints it from the
    # verified as_observed projection, so a bare string can never reach the
    # admission gate.
    numeric_context = kwargs.pop("numeric_context", None)
    if numeric_context is not None and (
        not as_observed or numeric_context != "october_replay_as_observed"
    ):
        _fail(
            "invalid_raw_history",
            "numeric_context requires the versioned as-observed replay mode",
        )
    replay_token = None
    scan_trading_dates = None
    if numeric_context is not None:
        from quanthus.product_daily.recommendation.daily_runner import (
            _new_october_context,
        )
        from quanthus.product_daily.recommendation.paper_replay import (
            bar_context_window,
        )

        replay_token = _new_october_context(numeric_context)
        # Bounded scan calendar for the as-observed single-T replay: the scan
        # window runs over the NAMED venue open sessions of the current
        # bar_context_window (the last <=61 contiguous open sessions ending at
        # T) plus facts.next_session - never the full universe_membership union
        # (a 1991 listing must not drag pre-scope sessions into
        # require_scan_date) and never narrowed by which bars exist (gaps and
        # short windows stay visible).  Derived from the bound calendar_rows'
        # is_open sessions <= T, the same calendar the replay envelope sliced.
        open_sessions = tuple(
            sorted({row.session_date for row in history.calendar_rows if row.is_open})
        )
        window = bar_context_window(open_sessions, history.market_date)
        scan_trading_dates = tuple(sorted({*window, facts.next_session}))
    # The as-observed replay's actual-read instant is bound into the signed
    # closure so each row's source clock can be re-read against THIS run's
    # real read.  It is forwarded to the raw runner ONLY alongside the private
    # token; a strict/forward caller may legitimately pass the same replay
    # observed_at on the wire (the replay envelope always carries it), so it
    # is dropped here rather than rejected as a context breach.
    as_observed_at = kwargs.pop("as_observed_at", None)
    consumption = kwargs.pop("consumption_evidence", None)
    if as_observed:
        factor_restrictions = [
            {"security_code": (sec.get("identity") or {}).get("listing_code"), "source_row": row}
            for sec in history.securities
            if isinstance(sec.get("identity"), Mapping) and isinstance(sec.get("exceptions"), list)
            for row in sec["exceptions"]
            if isinstance(row, Mapping) and row.get("ledger") == "P1_factor_pit_exception"
        ]
        price_inputs = [
            sec["replay_p1_price_input"]
            for sec in history.securities
            if "replay_p1_price_input" in sec
        ]
        if factor_restrictions or price_inputs:
            consumption = {
                **(consumption or {}),
                "p1_factor_visibility": {
                    "policy_version": "p1-c1-conditional-20260725-v1",
                    "scope": "factor_dependent_paths",
                    "restrictions": factor_restrictions,
                    "price_inputs": price_inputs,
                },
            }
    # Per-order typed pending facts stay verbatim (never summed or last-one-
    # wins); the runner merges them into the same account-membership audit.
    # Per-order pending evidence (order_key/source_receipt/direction) binds
    # the same-read order identity into the issued closure.  A fractional,
    # bool or negative quantity is a named invalid_pending_quantity on the
    # typed boundary, never silently truncated.
    pending_descriptors = [
        PendingSecurityInput(
            security_code=_text(p.get("security_code"), "pending_securities[].security_code"),
            kind=_text(p.get("kind"), "pending_securities[].kind"),
            quantity=p.get("quantity"),
            order_key=(
                _text(p.get("order_key"), "pending_securities[].order_key")
                if p.get("order_key") is not None
                else None
            ),
            source_receipt=(
                _text(p.get("source_receipt"), "pending_securities[].source_receipt")
                if p.get("source_receipt") is not None
                else None
            ),
            direction=(
                _text(p.get("direction"), "pending_securities[].direction")
                if p.get("direction") is not None
                else None
            ),
        )
        for p in history.pending_securities
    ]
    output = run_daily_advice_from_raw(
        raw=inputs,
        history_receipts=bound_receipts,
        caller_input_exclusions=runner_exclusions,
        consumption_evidence=consumption,
        numeric_context=replay_token,
        scan_trading_dates=scan_trading_dates,
        as_observed_at=as_observed_at if replay_token is not None else None,
        pending_securities=[*pending_descriptors, *(kwargs.pop("pending_securities", None) or ())],
        account_unresolved=account_unresolved_records,
        **kwargs,
    )
    # The issued receipt's digest must match the closure actually carried on the
    # output.  run_daily_advice already folded every exclusion into
    # source_closure before computing its digest, so this is an invariant check,
    # not a rewrite.
    from quanthus.event_library.contracts import sha256_digest as _sha

    actual = _sha(output.source_closure)
    bound = output.receipt.source_closure_digest if output.receipt is not None else actual
    if actual != bound:
        _fail(
            "source_closure_receipt_mismatch",
            "the final source_closure drifted from the issued receipt digest",
        )
    return output

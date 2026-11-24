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
from quanthus.product_daily.recommendation.daily_runner import RawSecurityInput
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


# --- strict loader ------------------------------------------------------------------


def load_raw_history(raw: Mapping[str, Any]) -> RawHistory:
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
        # admissible knowledge for this batch.
        if visible_at > decision_cutoff:
            _fail(
                "source_visibility_after_cutoff",
                f"source_member {member_id} visible_at after decision_cutoff",
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
    return RawHistory(
        market_date=market_date,
        decision_cutoff=decision_cutoff,
        source_members=tuple(members),
        calendar_rows=tuple(calendar_rows),
        listing_rows=tuple(listing_rows),
        securities=securities,
        source_members_by_id={m.member_id: m for m in members},
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


def _exceptions_from(sec: Mapping[str, Any], security_index: int) -> tuple[ExceptionSpan, ...]:
    out: list[ExceptionSpan] = []
    for ei, e in enumerate(
        _seq(sec.get("exceptions", ()), f"securities[{security_index}].exceptions")
    ):
        eo = _obj(e, f"exceptions[{ei}]")
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


def _date_map(raw_map: Any, label: str) -> dict[date, datetime]:
    if not isinstance(raw_map, dict):
        _fail("invalid_raw_history", f"{label} must be an object")
    out: dict[date, datetime] = {}
    for key, value in raw_map.items():
        out[_isodate(key, f"{label} key")] = _isodatetime(value, f"{label}[{key}]")
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


def _position_facts_from(sec: Mapping[str, Any]) -> tuple[Any, int | None]:
    """Project the caller-declared position facts through, unchanged.

    position_facts arrives already typed (the P12 RunnerRequest projection passes
    the real HeldPositionFacts object); a bare dict would lose its bound evidence
    and is rejected.  prior_deterioration_sessions is a strict non-negative int.
    """
    pf = sec.get("position_facts")
    if pf is not None and isinstance(pf, dict):
        _fail(
            "invalid_raw_history",
            "securities[].position_facts must be a HeldPositionFacts object, "
            "not a dict (it would lose its bound evidence)",
        )
    pds = sec.get("prior_deterioration_sessions")
    if pds is not None and (type(pds) is not int or pds < 0):
        _fail(
            "invalid_raw_history",
            "securities[].prior_deterioration_sessions must be a non-negative int",
        )
    return pf, pds


def raw_security_inputs_from_history(
    history: RawHistory,
    *,
    anchor_mode: str = "strict",
    proof_sinks: dict[str, dict[str, str]] | None = None,
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
    seen_ids: set[str] = set()
    for si, sec in enumerate(history.securities):
        sec_o = _obj(sec, f"securities[{si}]")
        try:
            identity = _identity_from(sec_o, si)
        except ProductDailyError as exc:
            receipts.append(
                {
                    "security_code": f"securities[{si}]",
                    "status": "excluded",
                    "reason": f"identity_parse: {exc}",
                    "scope": "per_security",
                }
            )
            continue
        code = identity.listing_code
        if identity.security_id in seen_ids:
            _fail("duplicate_security", f"duplicate security_id {identity.security_id}")
        seen_ids.add(identity.security_id)
        position_facts, prior_det = _position_facts_from(sec_o)
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
        )
        sec_proof: dict[str, str] = {}
        try:
            # Always resolve equivalent-day proofs into a local sink so the
            # receipt can name them; mirror into proof_sinks only when the
            # caller asked for the raw map.  anchor_mode stays a consumer
            # config - the proof marks, not the mode flag, drive the receipt.
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
            if getattr(exc, "reason_code", "") == "calendar_coverage_incomplete":
                # A shared calendar that cannot cover the listing start is a
                # shared failure for every security on that venue, not a
                # per-security isolation.
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
        try:
            bars = _bars_from(sec_o, si)
            actions = _actions_from(sec_o, si)
            exceptions = _exceptions_from(sec_o, si)
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
                position_facts=position_facts,
                prior_deterioration_sessions=prior_det,
                held_membership=held_membership,
                pending_kind=pending_kind,
                held_unavailable_reason=held_reason,
                held_quantity=held_qty,
                pending_quantity=pending_qty,
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
            _fail(
                "source_knowledge_after_cutoff",
                f"source_member {m.member_id} knowledge_at {m.knowledge_at} exceeds "
                f"the run decision_cutoff {policy.decision_cutoff}",
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
            _fail(
                "calendar_knowledge_after_cutoff",
                f"calendar {row.venue_id} {row.session_date} knowledge_from "
                f"{row.knowledge_from} exceeds the run decision_cutoff",
            )
    for lrow in history.listing_rows:
        if lrow.knowledge_from > policy.decision_cutoff:
            _fail(
                "listing_knowledge_after_cutoff",
                f"listing {lrow.security_id}@{lrow.venue_id} knowledge_from "
                f"{lrow.knowledge_from} exceeds the run decision_cutoff",
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

    history = load_raw_history(raw_history)
    facts = kwargs.get("facts")
    policy = kwargs.get("policy")
    if facts is None or policy is None:
        _fail(
            "invalid_raw_history",
            "run_daily_advice_from_history requires facts and policy",
        )
    bind_raw_history_to_run(history, facts=facts, policy=policy)
    # Versioned anchor policy: "strict" keeps the R3/R4 stored-anchor gate;
    # "equivalent_day" additionally resolves an is_open session whose open_at
    # is absent when its whole local day is provably inside/outside the listing.
    # The mode is a consumer-side evaluation config, never a data rewrite, and
    # each resolved session is named on the receipt's membership_proof.
    anchor_mode = kwargs.pop("anchor_mode", "strict")
    proof_sinks: dict[str, dict[str, str]] = {}
    inputs, receipts = raw_security_inputs_from_history(
        history, anchor_mode=anchor_mode, proof_sinks=proof_sinks
    )
    if not inputs:
        _fail(
            "missing_raw_inputs",
            "raw_history produced no qualified security inputs; this is a shared "
            "block, never a fabricated qualified-empty day",
        )
    envelope_identity = raw_history_digest(history)
    bound_receipts = [{**receipt, "raw_history_digest": envelope_identity} for receipt in receipts]
    # Convert adapter-level exclusions into the runner's input_exclusions so a bad
    # neighbour is a named isolation inside source_closure, never silently dropped.
    # Adapter-level exclusions are named isolations INSIDE the issued closure,
    # not appended after receipt issuance (a post-hoc dict replace would leave
    # receipt.source_closure_digest pointing at the pre-exclusion bytes).
    adapter_exclusions = [
        {
            "security_code": receipt["security_code"],
            "reason": "raw_history_security_invalid",
            "member_or_field": receipt.get("reason", "unparseable securities[] entry"),
            "scope": "per_security",
        }
        for receipt in receipts
        if receipt.get("status") == "excluded"
    ]
    runner_exclusions = [*(adapter_exclusions), *(kwargs.pop("input_exclusions", None) or ())]
    output = run_daily_advice_from_raw(
        raw=inputs,
        history_receipts=bound_receipts,
        caller_input_exclusions=runner_exclusions,
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

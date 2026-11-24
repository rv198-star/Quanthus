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
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, NoReturn

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


def _isodatetime(value: Any, label: str) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            _fail("invalid_raw_history", f"{label} is not an ISO datetime")
        if parsed.tzinfo is None:
            _fail("invalid_raw_history", f"{label} must carry a timezone")
        return parsed
    _fail("invalid_raw_history", f"{label} must be an ISO datetime string")


def _decimal(value: Any, label: str, *, allow_none: bool = False) -> Decimal | None:
    if value is None and allow_none:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        _fail("invalid_raw_history", f"{label} is not a decimal")


def _bool3(value: Any, label: str) -> bool | None:
    if value is None:
        return None
    if type(value) is bool:
        return value
    _fail("invalid_raw_history", f"{label} must be true/false/null")


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
    close_at: datetime | None
    knowledge_from: date | None
    source_member_id: str


@dataclass(frozen=True)
class ListingRow:
    security_id: str
    listing_id: str
    venue_id: str
    effective_from: date
    effective_to: date | None
    knowledge_from: date | None
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
    for index, c in enumerate(_seq(envelope.get("calendar_rows"), "calendar_rows")):
        co = _obj(c, f"calendar_rows[{index}]")
        smid = _text(co.get("source_member_id"), f"calendar_rows[{index}].source_member_id")
        if smid not in member_ids:
            _fail("unknown_source_member", f"calendar_rows[{index}] -> {smid}")
        close_at = _isodatetime(co["close_at"], "close_at") if co.get("close_at") else None
        calendar_rows.append(
            CalendarRow(
                venue_id=_text(co.get("venue_id"), f"calendar_rows[{index}].venue_id"),
                session_date=_isodate(
                    co.get("session_date"), f"calendar_rows[{index}].session_date"
                ),
                is_open=bool(co.get("is_open")),
                close_at=close_at,
                knowledge_from=(
                    _isodate(co["knowledge_from"], "knowledge_from")
                    if co.get("knowledge_from")
                    else None
                ),
                source_member_id=smid,
            )
        )

    listing_rows: list[ListingRow] = []
    for index, lrow in enumerate(_seq(envelope.get("listing_rows"), "listing_rows")):
        lo = _obj(lrow, f"listing_rows[{index}]")
        smid = _text(lo.get("source_member_id"), f"listing_rows[{index}].source_member_id")
        if smid not in member_ids:
            _fail("unknown_source_member", f"listing_rows[{index}] -> {smid}")
        eff_from = _isodate(lo.get("effective_from"), f"listing_rows[{index}].effective_from")
        eff_to = (
            _isodate(lo["effective_to"], f"listing_rows[{index}].effective_to")
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
                venue_id=_text(lo.get("venue_id"), f"listing_rows[{index}].venue_id"),
                effective_from=eff_from,
                effective_to=eff_to,
                knowledge_from=(
                    _isodate(lo["knowledge_from"], "knowledge_from")
                    if lo.get("knowledge_from")
                    else None
                ),
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


def derive_universe_membership(
    *,
    listing_rows: Sequence[ListingRow],
    calendar_rows: Sequence[CalendarRow],
    security_id: str,
    venue_id: str,
    market_date: date,
) -> tuple[date, ...]:
    """Universe membership = open sessions of the venue covered by the listing.

    A membership date is a venue open session on which the security had an active
    listing interval, restricted to <= market_date (history never backfills the
    prospective T+1 window into the prior listing age).  Wrong-exchange calendars are
    excluded by keying on venue_id.  Returns the ordered membership sequence.
    """
    intervals = [
        row for row in listing_rows if row.security_id == security_id and row.venue_id == venue_id
    ]
    if not intervals:
        _fail(
            "listing_fact_missing",
            f"no listing interval for {security_id}@{venue_id}; age cannot be inferred",
        )
    open_sessions = sorted(
        row.session_date
        for row in calendar_rows
        if row.venue_id == venue_id and row.is_open and row.session_date <= market_date
    )
    membership: list[date] = []
    for session in open_sessions:
        for interval in intervals:
            if interval.effective_from <= session and (
                interval.effective_to is None or session <= interval.effective_to
            ):
                membership.append(session)
                break
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
    covering = [
        row
        for row in listing_rows
        if row.security_id == security_id
        and row.venue_id == venue_id
        and earliest_anchor is not None
        and row.effective_from <= earliest_anchor
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
        "membership_count": observed_membership,
        "bar_count": observed_bars,
        "membership_window": {"from": window_start, "to": window_end},
        "status": "observed",
    }


def raw_security_inputs_from_history(
    history: RawHistory,
) -> tuple[list[RawSecurityInput], list[dict[str, Any]]]:
    """Project raw_history.securities[] into RawSecurityInput[] + per-security receipts.

    A bad security is isolated (recorded in receipts as excluded) so a valid neighbour
    keeps its real entry path; an all-fail input produces zero RawSecurityInput but the
    receipts still carry the per-security reason - never a manufactured empty pass.
    """
    inputs: list[RawSecurityInput] = []
    receipts: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for si, sec in enumerate(history.securities):
        sec_o = _obj(sec, f"securities[{si}]")
        identity = _identity_from(sec_o, si)
        code = identity.listing_code
        if identity.security_id in seen_ids:
            _fail("duplicate_security", f"duplicate security_id {identity.security_id}")
        seen_ids.add(identity.security_id)
        try:
            membership = derive_universe_membership(
                listing_rows=history.listing_rows,
                calendar_rows=history.calendar_rows,
                security_id=identity.security_id,
                venue_id=identity.venue_id,
                market_date=history.market_date,
            )
        except ProductDailyError as exc:
            receipts.append(
                {
                    "security_code": code,
                    "status": "excluded",
                    "reason": str(exc),
                    "scope": "per_security",
                }
            )
            continue
        bars = _bars_from(sec_o, si)
        receipts.append(
            history_receipt_for_security(
                security_code=code,
                security_id=identity.security_id,
                venue_id=identity.venue_id,
                membership=membership,
                bars=bars,
                market_date=history.market_date,
                listing_rows=history.listing_rows,
            )
        )
        inputs.append(
            RawSecurityInput(
                identity=identity,
                bars=bars,
                universe_membership=membership,
                actions=_actions_from(sec_o, si),
                exceptions=_exceptions_from(sec_o, si),
                limits=_limits_from(sec_o, si),
                close_at_by_date=_date_map(sec_o.get("close_at_by_date", {}), "close_at_by_date"),
                knowledge_cutoff_by_date=_date_map(
                    sec_o.get("knowledge_cutoff_by_date", {}), "knowledge_cutoff_by_date"
                ),
                # held/pending facts the caller may already project onto the
                # envelope; None stays unknown, never a synthetic default.
                held_membership=_bool3(
                    sec_o.get("held_membership"), "securities[].held_membership"
                ),
                pending_kind=(
                    _text(sec_o["pending_kind"], "securities[].pending_kind")
                    if sec_o.get("pending_kind") is not None
                    else None
                ),
                held_unavailable_reason=(
                    _text(sec_o["held_unavailable_reason"], "held_unavailable_reason")
                    if sec_o.get("held_unavailable_reason") is not None
                    else None
                ),
                held_quantity=(
                    None if sec_o.get("held_quantity") is None else int(sec_o["held_quantity"])
                ),
                pending_quantity=(
                    None
                    if sec_o.get("pending_quantity") is None
                    else int(sec_o["pending_quantity"])
                ),
            )
        )
    return inputs, receipts


# --- runner-facing convenience -------------------------------------------------------


def run_daily_advice_from_history(
    raw_history: Mapping[str, Any],
    **kwargs: Any,
) -> RuleDayOutput:
    """Strict-load a p11.raw-history-input.v1 envelope and run the existing raw chain.

    This is the thin E1 seam: validate the envelope -> derive per-security
    universe_membership from listing intervals + the venue calendar (full sequence,
    never a 120-session truncation passed off as the complete age) -> project each
    qualified security into RawSecurityInput -> run_daily_advice_from_raw with the
    per-security history receipts bound into source_closure.  All runner/facts/policy
    kwargs pass through unchanged.
    """
    from quanthus.product_daily.recommendation.daily_runner import (
        run_daily_advice_from_raw,
    )

    history = load_raw_history(raw_history)
    inputs, receipts = raw_security_inputs_from_history(history)
    if not inputs:
        _fail(
            "missing_raw_inputs",
            "raw_history produced no qualified security inputs; this is a shared "
            "block, never a fabricated qualified-empty day",
        )
    output = run_daily_advice_from_raw(raw=inputs, history_receipts=receipts, **kwargs)
    # The receipts also travel on the output for the P12 run receipt to record.
    return output

"""Calendar + listing facts for the bounded history reader (issue #749).

Reads the venue trading calendar and the listing intervals from the
pinned Base members plus the daily-core tail days, and hands them over
as neutral rows.  The reader never counts listing age, never fills a
missing calendar day, and never infers a listing start from the earliest
bar - it delivers the raw rows the P11 adapter consumes.

Per the P11 E1 candidate contract (commit 8dfc6cfa, R2->R3 fix): the
listing effective_from / effective_to keep their ORIGINAL timezone and
precision (native UTC instants, never truncated to a session date), and
the corresponding observation_start on calendar / tradability rows is
kept at native precision.  The P11 adapter owns the instant -> session
projection; this reader only carries the source bytes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from quanthus.p12_history.clocks import parse_ts
from quanthus.p12_history.source_pins import SourceMap

LISTING_PROOF_SESSIONS = 120


@dataclass(frozen=True)
class CalendarRow:
    venue_id: str
    session_date: date
    is_open: bool
    # The session's native open instant - a distinct source column, never
    # the observation_start.  09-30 carries open_at=null (exchange gave no
    # native open), 10-08 carries the real 01:30Z open; the two are never
    # merged (协调: envelope must not write observation_start as open_at).
    open_at: datetime | None
    close_at: datetime | None
    observation_start: datetime | None
    knowledge_from: datetime
    source_member_id: str


@dataclass(frozen=True)
class ListingRow:
    security_id: str
    listing_id: str
    venue_id: str
    # Original source instants, timezone-aware, native precision - the
    # P11 adapter projects them onto the venue session date.  Never
    # truncated to a date here (that over-counts a session, the R2 bug).
    effective_from: datetime
    effective_to: datetime | None
    knowledge_from: datetime
    source_member_id: str


def _date(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _rows(path: Path, columns):
    import pyarrow.parquet as pq

    recs = pq.ParquetFile(str(path)).read(columns=columns).to_pylist()
    # The frozen reader wire carries only JSON-native values verbatim:
    # Decimal -> exact source-precision text (never float), native
    # datetime -> ISO-8601 preserving its own offset, date -> ISO date.
    # No field's business instant is shifted - only its representation is
    # normalized so the envelope/coverage canonical bytes serialize once
    # for the digest + disk + P11 wire without a global default=str.
    from quanthus.p12_history.clocks import iso as _iso

    def _norm(v):
        if isinstance(v, Decimal):
            return str(v)
        # datetime -> ISO-8601 preserving its own offset; the business
        # instant is unchanged, only the representation is normalized so
        # the wire is JSON-native.  A bare date stays a date object here -
        # internal joins key on date; the coverage/envelope boundary is
        # where dates become ISO strings.
        if isinstance(v, datetime):
            return _iso(v)
        if isinstance(v, date):
            return v.isoformat()
        return v

    out = []
    for rec in recs:
        out.append({k: _norm(v) for k, v in rec.items()})
    return out


def _venue_code_map(source_map: SourceMap):
    """hash venue_id -> admitted venue code (SSE/SZSE/...), via mic_code.

    The sources key the calendar/listing venue by an opaque venue_id hash;
    the P11 loader admits a venue only by its named code or mic.  Resolve
    the hash to the admitted code through dwd_trading_venue.mic_code so
    downstream rows carry the admitted venue key, never the raw hash.
    Mic variants are normalised (BJSE -> BSE); a venue whose mic is not in
    the admitted set maps to None and its rows are dropped from the
    envelope (the scan domain is SSE/SZSE common_equity).
    """
    # Mic variants normalised to the loader's named set (BJSE is the BSE
    # board; the loader admits the BSE code).  A venue whose mic has no
    # admitted code maps to None and its rows drop out of the envelope.
    mic_to_code = {
        "XSHG": "SSE", "XSHE": "SZSE", "XBEI": "BSE", "BJSE": "BSE",
    }
    out = {}
    for member in source_map.base_members("dwd_trading_venue"):
        try:
            for rec in _rows(member.abs_path, ["venue_id", "mic_code"]):
                mic = str(rec.get("mic_code") or "")
                code = mic_to_code.get(mic)
                # Admit only venues the loader names (SSE/SZSE/BSE); any
                # other source venue drops out of the envelope.
                out[str(rec.get("venue_id") or "")] = code
        except Exception:  # noqa: BLE001
            continue
    return out


def read_calendar_rows(source_map: SourceMap, market_date: date):
    """Venue calendar rows covering up to T (dedup on venue+date).

    observation_start is kept at native precision; close_at too.  Rows
    are deduplicated on (venue_id, session_date); a conflicting later
    row is a named defect on the receipt.
    """
    venue_code = _venue_code_map(source_map)
    reads = []
    conflicts = []
    rows = {}
    session_ids = {}
    sources = list(source_map.base_members("dwd_trading_calendar"))
    for day in source_map.daily_open_days():
        d = _date(day)
        if d is None or d > market_date:
            continue
        m = source_map.daily_member(day, "dwd_trading_calendar")
        if m is not None:
            sources.append(m)
    for member in sources:
        # P10 daily-channel ruling: a calendar-map daily partition that is
        # an alias of the formal daily-core calendar member (same sha256)
        # is the SAME Daily identity - skip the duplicate read so both
        # logical aliases are gated under the formal member's channel
        # admission, never double-counted as independent sources.
        if getattr(member, "alias_of", None):
            reads.append({
                "member_id": member.member_id,
                "member_key": f"{member.package_id}::{member.member_id}",
                "status": "ALIAS",
                "alias_of": member.alias_of,
                "sha256": member.sha256,
                "sha_match": True,
            })
            continue
        verify = source_map.verify_member(member)
        if not verify["matches"]:
            reads.append({
                "member_id": member.member_id, "member_key": f"{member.package_id}::{member.member_id}", "status": "SHA_MISMATCH",
                "declared_sha256": verify["declared_sha256"],
                "actual_sha256": verify["actual_sha256"], "verified_at": verify.get("verified_at"),
            })
            continue
        try:
            records = _rows(
                member.abs_path,
                ["venue_id", "trade_date", "is_open", "open_at", "close_at",
                 "observation_start", "knowledge_from", "calendar_session_id"],
            )
        except Exception as exc:  # noqa: BLE001
            reads.append({"member_id": member.member_id, "member_key": f"{member.package_id}::{member.member_id}", "status": "UNREADABLE", "error": str(exc)})
            continue
        n = 0
        for rec in records:
            session = _date(rec.get("trade_date"))
            knowledge_from = parse_ts(rec.get("knowledge_from"))
            if session is None or session > market_date or knowledge_from is None:
                continue
            venue_id = venue_code.get(str(rec.get("venue_id") or ""))
            if venue_id is None:
                continue  # not an admitted venue for this scan domain
            row = CalendarRow(
                venue_id=venue_id,
                session_date=session,
                is_open=bool(rec.get("is_open")),
                open_at=parse_ts(rec.get("open_at")),
                close_at=parse_ts(rec.get("close_at")),
                observation_start=parse_ts(rec.get("observation_start")),
                knowledge_from=knowledge_from,
                source_member_id=member.member_id,
            )
            key = (row.venue_id, row.session_date)
            prior = rows.get(key)
            if prior is None:
                rows[key] = row
                n += 1
            elif (prior.is_open, prior.source_member_id, prior.knowledge_from) != (
                row.is_open, row.source_member_id, row.knowledge_from
            ):
                conflicts.append(
                    {"venue_id": row.venue_id, "session_date": session.isoformat(), "member_id": member.member_id}
                )
            # Native session-id -> (venue, trade_date) map so listing-level
            # tradability_state (keyed by calendar_session_id hash) joins
            # back to the session the bar projects onto.
            csid = str(rec.get("calendar_session_id") or "")
            if csid and session is not None and venue_id is not None:
                # Keep the trade_date as a date for internal joins; the
                # coverage boundary (read_history) normalizes it to ISO.
                session_ids[csid] = (venue_id, session)
        reads.append({"member_id": member.member_id, "member_key": f"{member.package_id}::{member.member_id}", "status": "READ", "rows": len(records), "kept": n, "sha256": member.sha256, "sha_match": True, "verified_at": verify.get("verified_at")})
    out = sorted(rows.values(), key=lambda r: (r.venue_id, r.session_date))
    # A venue calendar is a SHARED hard dependency - every security's
    # session window derives from it.  If its sources all failed (sha or
    # unreadable), or it yielded zero usable rows, that is a whole-day
    # block, not a normal empty: flag it so read_history can emit a named
    # day_block receipt instead of silently returning READ.
    failed = [r for r in reads if r.get("status") != "READ"]
    shared_failure = bool(sources) and (
        (len(failed) == len(reads) and not out) or (not out)
    )
    return out, {
        "reads": reads,
        "conflicts": conflicts,
        "session_ids": session_ids,
        "shared_failure": shared_failure,
        "failed_members": [r.get("member_id") for r in failed],
    }


def read_listing_rows(source_map: SourceMap, market_date: date):
    """Listing intervals from Base dwd_listing (instants kept verbatim)."""
    venue_code = _venue_code_map(source_map)
    reads = []
    rows = []
    for member in source_map.base_members("dwd_listing"):
        verify = source_map.verify_member(member)
        if not verify["matches"]:
            reads.append({
                "member_id": member.member_id, "member_key": f"{member.package_id}::{member.member_id}", "status": "SHA_MISMATCH",
                "declared_sha256": verify["declared_sha256"],
                "actual_sha256": verify["actual_sha256"], "verified_at": verify.get("verified_at"),
            })
            continue
        try:
            records = _rows(
                member.abs_path,
                ["security_id", "listing_id", "venue_id", "effective_from",
                 "effective_to", "knowledge_from"],
            )
        except Exception as exc:  # noqa: BLE001
            reads.append({"member_id": member.member_id, "member_key": f"{member.package_id}::{member.member_id}", "status": "UNREADABLE", "error": str(exc)})
            continue
        n = 0
        for rec in records:
            eff_from = parse_ts(rec.get("effective_from"))
            knowledge_from = parse_ts(rec.get("knowledge_from"))
            if eff_from is None or knowledge_from is None:
                continue
            eff_to = parse_ts(rec.get("effective_to"))
            venue_id = venue_code.get(str(rec.get("venue_id") or ""))
            if venue_id is None:
                continue  # not an admitted venue for this scan domain
            rows.append(
                ListingRow(
                    security_id=str(rec.get("security_id") or ""),
                    listing_id=str(rec.get("listing_id") or ""),
                    venue_id=venue_id,
                    effective_from=eff_from,
                    effective_to=eff_to,
                    knowledge_from=knowledge_from,
                    source_member_id=member.member_id,
                )
            )
            n += 1
        reads.append({"member_id": member.member_id, "member_key": f"{member.package_id}::{member.member_id}", "status": "READ", "rows": len(records), "kept": n, "sha256": member.sha256, "sha_match": True, "verified_at": verify.get("verified_at")})
    out = sorted(rows, key=lambda r: (r.security_id, r.effective_from))
    return out, {"reads": reads}

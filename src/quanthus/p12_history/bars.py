"""Bounded 1d-bar projection for the history reader (issue #749).

Delivers, per listing, the last 61 venue open sessions' 1d bars through
T (price_basis=none/UNADJUSTED, currency=CNY, OHLC + volume shares +
turnover CNY kept as separate columns).  Sources: the pinned Base
``dwd_security_bar/interval_size=1d`` month partitions plus each OPEN
daily-core ``dwd_security_bar`` member for the November tail.

Bars are joined to a security through the row's own ``listing_id`` - the
same key the identity chain binds.  Each emitted bar carries the owning
``security_id`` so the P11 adapter's bars[].security_id matches the
identity verbatim.  A session with no row simply has no bar - the reader
never substitutes an earlier non-contiguous record, never fills, and
never shrinks the 61-session window.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal

from quanthus.p12_history.calendar_listing import _date, _rows
from quanthus.p12_history.source_pins import SourceMap

ADMITTED_PRICE_BASIS = {"UNADJUSTED", "none", "NONE"}
ADMITTED_CURRENCY = {"CNY"}
BAR_SESSIONS = 61

_COLUMNS = [
    "source_record_id", "observation_end", "listing_id", "calendar_session_id",
    "interval_size", "price_basis", "currency",
    "open", "high", "low", "close", "volume", "turnover",
]


def _session_day(value):
    if value is None:
        return None
    try:
        end = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return end.astimezone(timezone.utc).date()


def _project_member(member, sessions_set, needed_listings, security_by_listing, out, reads, source_map=None):
    """Admit qualified 1d rows from one member into out[listing_id][date]."""
    if source_map is not None:
        verify = source_map.verify_member(member)
        if not verify["matches"]:
            reads.append({
                "member_id": member.member_id, "status": "SHA_MISMATCH",
                "declared_sha256": verify["declared_sha256"],
                "actual_sha256": verify["actual_sha256"],
            })
            return
    try:
        recs = _rows(member.abs_path, _COLUMNS)
    except Exception as exc:  # noqa: BLE001
        reads.append({"member_id": member.member_id, "status": "UNREADABLE", "error": str(exc)})
        return
    scanned = admitted = 0
    for rec in recs:
        scanned += 1
        if str(rec.get("price_basis") or "") not in ADMITTED_PRICE_BASIS:
            continue
        if str(rec.get("currency") or "") not in ADMITTED_CURRENCY:
            continue
        if str(rec.get("interval_size") or "") != "1d":
            continue
        session = _session_day(rec.get("observation_end"))
        if session is None or session not in sessions_set:
            continue
        listing_id = str(rec.get("listing_id") or "")
        if listing_id not in needed_listings:
            continue
        security_id = security_by_listing.get(listing_id, listing_id)
        try:
            bar = {
                "security_id": security_id,
                "session_date": session.isoformat(),
                "open_price": str(rec.get("open")),
                "high_price": str(rec.get("high")),
                "low_price": str(rec.get("low")),
                "close_price": str(rec.get("close")),
                "volume": str(rec.get("volume")),
                "amount": str(rec.get("turnover")),
                "is_tradable": None,
                # Native per-bar business clock verbatim (P11 裁定): the
                # observation_end names the session close-side fact.  The
                # session's own close_at / member knowledge_from are joined
                # downstream per venue/date - never derived into the bar.
                "observation_end": rec.get("observation_end"),
            }
        except Exception:  # noqa: BLE001
            continue
        out.setdefault(listing_id, {})[session] = bar
        admitted += 1
    reads.append({"member_id": member.member_id, "status": "READ", "rows": scanned, "admitted": admitted, "sha256": member.sha256, "sha_match": True})


def read_bars(source_map: SourceMap, sessions, security_by_listing):
    """Project 1d bars for ``sessions`` onto {listing_id: {date: bar}}.

    ``security_by_listing`` maps listing_id -> security_id so each bar
    carries the owning security_id.  Returns (bars_by_listing, coverage).
    """
    sessions_set = set(sessions)
    needed_listings = set(security_by_listing.keys())
    out = {}
    reads = []

    months_needed = {(s.year, s.month) for s in sessions}
    for member in source_map.base_members("dwd_security_bar"):
        if "interval_size=1d" not in member.member_id:
            continue
        ym = re.search(r"observation_start_year=(\d{4})/observation_start_month=(\d+)", member.member_id)
        if ym and (int(ym.group(1)), int(ym.group(2))) not in months_needed:
            continue
        _project_member(member, sessions_set, needed_listings, security_by_listing, out, reads, source_map)

    for day in source_map.daily_open_days():
        d = _date(day)
        if d is None or d not in sessions_set:
            continue
        member = source_map.daily_member(day, "dwd_security_bar")
        if member is None:
            reads.append({"date": day, "status": "MISSING_MEMBER", "member": "dwd_security_bar"})
            continue
        _project_member(member, sessions_set, needed_listings, security_by_listing, out, reads, source_map)

    return out, {"reads": reads}

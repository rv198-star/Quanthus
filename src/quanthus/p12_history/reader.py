"""Top-level bounded history reader (issue #749).

``read_history(market_date, decision_cutoff, holdings, pending)`` reads
the pinned sources once per Job, derives the per-venue open-session
windows, and hands the P11 adapter a ``p11.raw-history-input.v1``
envelope plus a coverage receipt.  The reader computes no listing age,
no event/feature gate and no candidate - it reports what the sources
carried, with UNKNOWN where coverage is unproven.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from quanthus.p12_history import bars as bars_mod
from quanthus.p12_history import calendar_listing as cal_mod
from quanthus.p12_history import envelope as env_mod
from quanthus.p12_history import identity as id_mod
from quanthus.p12_history import supplements as sup_mod
from quanthus.p12_history.clocks import REPLAY_AS_OBSERVED, iso, parse_ts, utcnow
from quanthus.p12_history.source_pins import BASE_BUSINESS_THROUGH, SourceMap

BAR_SESSIONS = bars_mod.BAR_SESSIONS
LISTING_PROOF_SESSIONS = cal_mod.LISTING_PROOF_SESSIONS


@dataclass
class HistoryResult:
    """The reader's hand-off: a raw_history envelope + coverage receipt."""

    raw_history: dict
    coverage: dict
    read_at: str
    status: str = "READ"


def _window_sessions(calendar_rows, venue_id, market_date, count):
    sessions = sorted(
        r.session_date for r in calendar_rows
        if r.venue_id == venue_id and r.is_open and r.session_date <= market_date
    )
    return sessions[-count:]


def _date_iso(value):
    """bar session_date ISO string -> date, for the tradability join."""
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def read_history(
    market_date: date,
    decision_cutoff: datetime,
    *,
    holdings=(),
    pending=(),
    source_map: SourceMap | None = None,
    observed_at: datetime | None = None,
) -> HistoryResult:
    """Read the bounded history for one T and assemble the raw_history."""
    smap = source_map or SourceMap()
    observed_at = observed_at or utcnow()
    read_at = iso(observed_at)

    calendar_rows, cal_cov = cal_mod.read_calendar_rows(smap, market_date)
    listing_rows, list_cov = cal_mod.read_listing_rows(smap, market_date)
    identities, security_by_listing, uni_cov = id_mod.build_universe(
        smap, market_date, holdings, pending, {}
    )
    actions, act_cov = sup_mod.read_actions(smap, market_date)
    exceptions, exc_cov = sup_mod.read_exceptions(smap, market_date)
    limits, lim_cov = sup_mod.read_limits(smap, market_date)

    sessions_needed = set()
    for venue in {c.venue_id for c in calendar_rows}:
        for s in _window_sessions(calendar_rows, venue, market_date, BAR_SESSIONS):
            sessions_needed.add(s)
    sessions_sorted = sorted(sessions_needed)

    bars_by_listing, bar_cov = bars_mod.read_bars(
        smap, sessions_sorted, security_by_listing
    )

    # ---- assemble source_members (unique members actually opened) ----
    member_entries = {}
    def _reg(member, knowledge_at, date_from=None, date_to=None, visible_at=None):
        if member.member_id not in member_entries:
            member_entries[member.member_id] = env_mod.member_entry(
                member, knowledge_at,
                visible_at if visible_at is not None else knowledge_at,
                date_from, date_to,
            )
    # P11 裁定 (OCTOBER-REPLAY-TIME-v1): the original member clock is
    # carried verbatim - visible_at equals the member's own knowledge_at
    # and is NOT bounded by the decision_cutoff.  The logical replay
    # cutoff is declared separately on the envelope; consumers consume
    # through the versioned October-replay entry which reads the real
    # clock.  Members whose source clock post-dates the cutoff are still
    # named on the receipt (bounded_members, kept for comparison).
    base_knowledge = datetime.fromisoformat(BASE_BUSINESS_THROUGH + "T16:00:00+00:00")
    bounded_members = []
    for table in (
        "dwd_trading_calendar", "dwd_listing", "dwd_security",
        "dwd_security_identifier", "dwd_trading_venue", "dwd_listed_company",
        "dwd_company_industry_membership", "dwd_industry_node",
        "dwd_industry_scheme", "dwd_corporate_action",
        "dwd_corporate_action_term", "dwd_quality_exception",
        "dwd_observation_gap", "dwd_price_limit_regime",
        "dwd_tradability_state", "dwd_security_bar",
    ):
        for m in smap.base_members(table):
            if base_knowledge > decision_cutoff:
                bounded_members.append(m.member_id)
            _reg(m, base_knowledge, visible_at=base_knowledge)
    for day in smap.daily_open_days():
        if day > market_date.isoformat():
            continue
        pkg = smap.daily_core(day)
        if pkg is None:
            continue
        day_dt = datetime.fromisoformat(day + "T16:00:00+00:00")
        for m in pkg.members:
            if day_dt > decision_cutoff:
                bounded_members.append(m.member_id)
            _reg(m, day_dt, date_from=day, date_to=day, visible_at=day_dt)
    source_member_entries = list(member_entries.values())

    # ---- per-security entries ----
    securities = []
    # Native session-state joins (P11 裁定): close_at and the source
    # knowledge clock come from the venue calendar row for that session;
    # is_tradable comes from dwd_tradability_state joined on
    # calendar_session_id; price-limit regimes join on venue + listing.
    # A fact the sources do not carry stays null - the reader never
    # derives tradability from the presence of a bar.
    venue_close = {}
    venue_knowledge = {}
    for c in calendar_rows:
        if c.close_at is not None:
            venue_close.setdefault(c.venue_id, {})[c.session_date.isoformat()] = iso(c.close_at)
        venue_knowledge.setdefault(c.venue_id, {})[c.session_date.isoformat()] = iso(c.knowledge_from)
    session_ids = cal_cov.get("session_ids") or {}
    tradability_by_key = {}
    for t in limits.get("tradability_state") or []:
        csid = str(t.get("calendar_session_id") or "")
        vd = session_ids.get(csid)
        if vd is None:
            continue
        venue_t, sess_t = vd
        tradability_by_key[(str(t.get("listing_id") or ""), sess_t)] = {
            "is_tradable": t.get("is_tradable"),
            "can_buy": t.get("can_buy"),
            "can_sell": t.get("can_sell"),
            "reason_code": str(t.get("reason_code") or ""),
            "observation_start": iso(parse_ts(t.get("observation_start"))),
            "upper_price": t.get("upper_price"),
            "lower_price": t.get("lower_price"),
        }
    limit_by_listing = {}
    for rg in limits.get("price_limit_regimes") or []:
        for lid in (rg.get("listing_id"),):
            if lid:
                limit_by_listing.setdefault(str(lid), []).append(
                    {
                        "upper_limit_ratio": rg.get("upper_limit_ratio"),
                        "lower_limit_ratio": rg.get("lower_limit_ratio"),
                        "security_status": rg.get("security_status"),
                        "ipo_phase": rg.get("ipo_phase"),
                        "effective_from": iso(parse_ts(rg.get("effective_from"))),
                        "effective_to": iso(parse_ts(rg.get("effective_to"))),
                    }
                )

    for ident in identities:
        sec_bars = [
            bars_by_listing[ident.listing_id][s]
            for s in sessions_sorted
            if ident.listing_id in bars_by_listing and s in bars_by_listing[ident.listing_id]
        ]
        for b in sec_bars:
            sd = _date_iso(b.get("session_date"))
            t = tradability_by_key.get((ident.listing_id, sd))
            if t is not None:
                b["is_tradable"] = t.get("is_tradable")
                b["can_buy"] = t.get("can_buy")
                b["can_sell"] = t.get("can_sell")
                b["tradability_reason"] = t.get("reason_code") or None
        sec_actions = [
            a for a in actions.values() if a["security_id"] == ident.security_id
        ]
        bar_dates = {b.get("session_date") for b in sec_bars}
        all_close = venue_close.get(ident.venue_id) or {}
        all_cut = venue_knowledge.get(ident.venue_id) or {}
        close_map = {d: v for d, v in all_close.items() if d in bar_dates}
        cutoff_map = {d: v for d, v in all_cut.items() if d in bar_dates}
        sec_limits = limit_by_listing.get(ident.listing_id) or []
        securities.append(
            env_mod.security_entry(
                ident, sec_bars, sec_actions, [], sec_limits,
                close_map, cutoff_map,
            )
        )

    raw_history = env_mod.build_envelope(
        market_date=market_date,
        decision_cutoff=decision_cutoff,
        source_member_entries=source_member_entries,
        calendar_rows=calendar_rows,
        listing_rows=listing_rows,
        securities=securities,
    )

    # Actual consumption closure (P11 裁定): only members actually opened
    # this run count for formal use.  Each reads[] entry already carries
    # sha_match from verify_member; here we re-resolve the member_id to
    # pin its package_kind (base / daily-core), absolute path, version,
    # composite key and the rows this T used.  November daily partitions
    # registered in the envelope are named "not consumed for this T" -
    # their sealed October evidence and original clocks stay verbatim.
    consumed = {}

    def _collect(section, key):
        seg = section.get(key) if isinstance(section, dict) else section
        for rec in seg or []:
            mid = rec.get("member_id")
            if not mid or rec.get("status") != "READ":
                continue
            if mid in consumed:
                consumed[mid]["uses"].append(key)
                continue
            resolver = getattr(smap, "resolve", None)
            resolved = resolver(mid) if callable(resolver) else None
            consumed[mid] = {
                "member_id": mid,
                "package_id": (resolved.package_id if resolved else rec.get("package_id")),
                "package_key": (
                    f"{(resolved.package_id if resolved else rec.get('package_id'))}::{mid}"
                ),
                "package_kind": (resolved.package_kind if resolved else "unresolved"),
                "version": (resolved.version if resolved else None),
                "abs_path": (str(resolved.abs_path) if resolved else rec.get("path")),
                "sha256": rec.get("sha256"),
                "sha_match": bool(rec.get("sha_match")),
                "rows": rec.get("rows"),
                "kept": rec.get("kept"),
                "admitted": rec.get("admitted"),
                "status": rec.get("status"),
                "verify_at": read_at,
                "uses": [key],
            }

    _collect(cal_cov, "reads")
    _collect(list_cov, "reads")
    _collect(uni_cov, "reads")
    _collect(bar_cov, "reads")
    _collect(act_cov, "action_reads")
    _collect(act_cov, "term_reads")
    _collect(exc_cov, "reads")
    _collect(lim_cov, "reads")

    # A member is "relevant for this T" unless it is a November daily-core
    # partition - those post-date T and are registered for sealed-October
    # evidence only, never consumed as this T's fact.
    def _november(mid, pkg):
        return "market_date=2026-11" in mid or "dwd-daily-2026-11" in str(pkg or "")

    for mid, v in consumed.items():
        v["relevant_for_T"] = not _november(mid, v.get("package_id"))
    relevant = [v for v in consumed.values() if v["relevant_for_T"]]
    november_not_used = sorted(
        v["member_id"] for v in consumed.values() if not v["relevant_for_T"]
    )
    registered_ids = {m["member_id"] for m in source_member_entries}
    not_read = sorted(registered_ids - set(consumed))

    member_consumption = {
        "consumed_member_count": len(consumed),
        "consumed_for_T_count": len(relevant),
        "base_member_count": sum(1 for v in consumed.values() if v["package_kind"] == "base"),
        "daily_member_count": sum(
            1 for v in consumed.values() if v["package_kind"] == "daily-core"
        ),
        "sha_all_match": all(v["sha_match"] for v in consumed.values()),
        "members": sorted(consumed.values(), key=lambda v: v["member_id"]),
        "registered_member_count": len(source_member_entries),
        "registered_not_read": not_read,
        "november_partitions_not_used": november_not_used,
        "note": (
            "Members actually opened and sha-verified this run. Registered "
            "envelope source_members may exceed consumed members; formal use "
            "counts only the consumed set. November daily partitions are "
            "named not-used for this T; their sealed evidence and clocks "
            "stay verbatim in the envelope."
        ),
    }

    coverage = {
        "read_at": read_at,
        "visible_at_label": REPLAY_AS_OBSERVED,
        "market_date": market_date.isoformat(),
        "calendar": cal_cov,
        "listing": list_cov,
        "universe": uni_cov,
        "bars": bar_cov,
        "actions": act_cov,
        "exceptions": exc_cov,
        "limits": lim_cov,
        "security_count": len(securities),
        "bar_sessions": BAR_SESSIONS,
        "listing_proof_sessions": LISTING_PROOF_SESSIONS,
        "bounded_members": bounded_members,
        "member_consumption": member_consumption,
        "note": (
            "Corporate-action/exception completeness for the covered window is "
            "UNKNOWN (0 securities proven; 2026-11 deltas SOURCE_NOT_REFRESHED). "
            "Native rows present are delivered verbatim; the P11 rule decides "
            "which securities drop."
        ),
    }
    return HistoryResult(raw_history=raw_history, coverage=coverage, read_at=read_at)

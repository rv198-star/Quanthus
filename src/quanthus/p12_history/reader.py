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
from quanthus.p12_history.clocks import REPLAY_AS_OBSERVED, iso, utcnow
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
    base_knowledge = datetime.fromisoformat(BASE_BUSINESS_THROUGH + "T16:00:00+00:00")
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
            _reg(m, base_knowledge)
    for day in smap.daily_open_days():
        pkg = smap.daily_core(day)
        if pkg is None:
            continue
        day_dt = datetime.fromisoformat(day + "T16:00:00+00:00")
        for m in pkg.members:
            _reg(m, day_dt, date_from=day, date_to=day)
    source_member_entries = list(member_entries.values())

    # ---- per-security entries ----
    securities = []
    for ident in identities:
        sec_bars = [
            bars_by_listing[ident.listing_id][s]
            for s in sessions_sorted
            if ident.listing_id in bars_by_listing and s in bars_by_listing[ident.listing_id]
        ]
        sec_actions = [
            a for a in actions.values() if a["security_id"] == ident.security_id
        ]
        securities.append(
            env_mod.security_entry(ident, sec_bars, sec_actions, [], [], {}, {})
        )

    raw_history = env_mod.build_envelope(
        market_date=market_date,
        decision_cutoff=decision_cutoff,
        source_member_entries=source_member_entries,
        calendar_rows=calendar_rows,
        listing_rows=listing_rows,
        securities=securities,
    )

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
        "note": (
            "Corporate-action/exception completeness for the covered window is "
            "UNKNOWN (0 securities proven; 2026-11 deltas SOURCE_NOT_REFRESHED). "
            "Native rows present are delivered verbatim; the P11 rule decides "
            "which securities drop."
        ),
    }
    return HistoryResult(raw_history=raw_history, coverage=coverage, read_at=read_at)

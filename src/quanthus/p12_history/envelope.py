"""Assemble the p11.raw-history-input.v1 envelope (issue #749).

Builds the FLAT raw_history dict the P11 H1 strict loader accepts (the
schema fields sit at the top level of the object passed to
``load_raw_history``): source_members, calendar_rows, listing_rows and
per-security securities[] entries.  The reader emits no
CandidateDailyInput, no feature, no trend label and no receipt - those
are the P11 adapter's job.

Clock discipline: source_members carry the member's own knowledge_at
when proven and the qualified-index observed_at as visible_at under
ENGINEERING_REPLAY_AS_OBSERVED; listing effective_from/to keep native
source timezone + precision; calendar rows emit session_date plus the
native open_at/close_at; decision_cutoff is the caller's predeclared
deadline, never derived here.
"""

from __future__ import annotations

from quanthus.p12_history.clocks import iso

SCHEMA = "p11.raw-history-input.v1"


def _num(value):
    """Keep a numeric fact at source precision (Decimal -> str for JSON)."""
    if value is None:
        return None
    return str(value)


def member_entry(member, knowledge_at, visible_at, date_from=None, date_to=None):
    return {
        "member_id": member.member_id,
        "package_id": member.package_id,
        "version": member.version,
        "member_path": member.member_id,
        "sha256": member.sha256,
        "date_from": date_from,
        "date_to": date_to,
        "knowledge_at": iso(knowledge_at),
        "visible_at": iso(visible_at),
    }


def build_envelope(*, market_date, decision_cutoff, source_member_entries,
                   calendar_rows, listing_rows, securities):
    """Assemble the FLAT raw_history dict (JSON-serializable)."""
    return {
        "schema_version": SCHEMA,
        "market_date": market_date.isoformat(),
        "decision_cutoff": iso(decision_cutoff),
        "source_members": source_member_entries,
        "calendar_rows": [
            {
                "venue_id": c.venue_id,
                "session_date": c.session_date.isoformat(),
                "is_open": c.is_open,
                "open_at": iso(c.observation_start),
                "close_at": iso(c.close_at),
                "observation_start": iso(c.observation_start),
                "knowledge_from": iso(c.knowledge_from),
                "source_member_id": c.source_member_id,
            }
            for c in calendar_rows
        ],
        "listing_rows": [
            {
                "security_id": l.security_id,
                "listing_id": l.listing_id,
                "venue_id": l.venue_id,
                "effective_from": iso(l.effective_from),
                "effective_to": iso(l.effective_to),
                "knowledge_from": iso(l.knowledge_from),
                "source_member_id": l.source_member_id,
            }
            for l in listing_rows
        ],
        "securities": securities,
    }


def security_entry(identity, bars, actions, exceptions, limits,
                   close_map, cutoff_map, **account):
    """One securities[] entry for the raw_history envelope."""
    entry = {
        "identity": {
            "security_id": identity.security_id,
            "stock_code": identity.stock_code,
            "listing_code": identity.listing_code,
            "security_name": identity.security_name,
            "industry_id": identity.industry_id,
            "venue_id": identity.venue_id,
            "instrument_type": identity.instrument_type,
            "listing_id": identity.listing_id,
            "company_id": identity.company_id,
            "depends_on_member_ids": list(identity.depends_on_member_ids),
            "invalidations": list(identity.invalidations),
            "price_bound": _num(identity.price_bound),
            "source_evidence_digest": identity.source_evidence_digest,
        },
        "bars": bars,
        "actions": actions,
        "exceptions": exceptions,
        "limits": limits,
        "close_at_by_date": close_map,
        "knowledge_cutoff_by_date": cutoff_map,
        # Clock-role annotation (P11 裁定): knowledge_cutoff_by_date carries
        # the venue calendar row's own knowledge_from for that session - a
        # SOURCE publish clock, never the run's decision_cutoff.  The
        # versioned October-replay entry binds the per-day cutoff separately.
        "knowledge_clock_role": "calendar_source_knowledge_from",
    }
    entry.update(account)
    return entry

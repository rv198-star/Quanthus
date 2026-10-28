"""Metadata-only qualification for the fixed B/consolidation confirmation.

An unconsumed event is necessary, not sufficient, for an independent sample.
Company/alias exposure, model time and execution coordinates are separate gates.
This module reads no prices, references, candidate scores or economic outcomes.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from quanthus.stage_learning.entry_consolidation import EntryOpportunity, pre_entry_window


@dataclass(frozen=True)
class ListingScope:
    venue: str
    first_session: date
    last_session: date | None


@dataclass(frozen=True)
class EntryQualification:
    event_id: str
    security_code: str
    issuer_id: str | None
    signal_date: date
    anchor: date | None
    next_open_session: date | None
    diagnostic_disposition: str
    confirmation_disposition: str


def qualify_population(
    opportunities: Sequence[EntryOpportunity], *,
    model_last_observation: date,
    identities: Mapping[str, str],
    diagnostic_blocked_aliases: set[str],
    all_exposed_aliases: set[str],
    consumed: Sequence[EntryOpportunity],
    sessions: Mapping[str, Sequence[date]],
    listings: Mapping[str, ListingScope],
) -> tuple[EntryQualification, ...]:
    """Recount the entire exact population; never replace it with a favorable subset.

    `consumed` must be an exact subset, including original B disposition/digest.
    Even a different event on a consumed issuer is excluded from confirmation.
    `diagnostic_*` reproduces the earlier, deliberately development-only boundary;
    all exposures additionally excludes old development roles from a fresh claim.
    A returned INCLUDE is only metadata eligibility, not sample/reference acceptance.
    """
    events = {event.event_id: event for event in opportunities}
    if len(events) != len(opportunities) or not events:
        raise ValueError("ENTRY_QUALIFICATION_POPULATION")
    if len({(e.security_code, e.signal_date) for e in opportunities}) != len(events):
        raise ValueError("ENTRY_QUALIFICATION_EVENT_COORDINATE")
    used: set[str] = set()
    used_issuers: set[str] = set()
    for event in consumed:
        original = events.get(event.event_id)
        if (original is None or event.event_id in used
                or original.security_code != event.security_code
                or original.signal_date != event.signal_date
                or original.baseline_allowed != event.baseline_allowed
                or original.baseline_decision_digest != event.baseline_decision_digest
                or identities.get(event.security_code) != event.issuer_id):
            raise ValueError("ENTRY_QUALIFICATION_CONSUMED_IDENTITY")
        used.add(event.event_id)
        used_issuers.add(event.issuer_id)
    for calendar in sessions.values():
        if not calendar or list(calendar) != sorted(set(calendar)):
            raise ValueError("ENTRY_QUALIFICATION_CALENDAR")
    output = []
    for event in opportunities:
        code, signal = event.security_code, event.signal_date
        issuer = identities.get(code)
        anchor, execution = None, None
        if not date(2012, 1, 1) <= signal <= date(2021, 12, 30):
            raise ValueError("ENTRY_QUALIFICATION_PROTECTED_SCOPE")
        if signal <= model_last_observation:
            reason = "signal_not_after_training"
        elif code in diagnostic_blocked_aliases:
            reason = "sealed_evaluation_or_recent_issuer_alias"
        elif issuer is None:
            reason = "missing_or_ambiguous_issuer"
        elif code not in listings:
            reason = "missing_accepted_validity"
        else:
            listing = listings[code]
            if listing.venue not in sessions:
                raise ValueError("ENTRY_QUALIFICATION_VENUE")
            calendar = sessions[listing.venue]
            try:
                window = pre_entry_window(calendar, signal)
            except ValueError as error:
                if str(error) != "ENTRY_REQUIRED40_UNAVAILABLE":
                    raise
                window = ()
            if not window:
                reason = "insufficient_calendar_history"
            else:
                anchor = window[-1]
                index = calendar.index(signal)
                execution = calendar[index + 1] if index + 1 < len(calendar) else None
                if anchor <= model_last_observation:
                    reason = "anchor_not_after_training"
                elif window[0] < listing.first_session:
                    reason = "insufficient_listing_history"
                elif listing.last_session is not None and signal > listing.last_session:
                    reason = "signal_after_listing_end"
                else:
                    reason = "INCLUDE"
        confirmation = reason
        if reason == "INCLUDE":
            if event.event_id in used:
                confirmation = "consumed_development_event"
            elif issuer in used_issuers:
                confirmation = "consumed_development_issuer"
            elif code in all_exposed_aliases:
                confirmation = "prior_development_or_evaluation_issuer_alias"
            elif execution is None:
                confirmation = "next_open_calendar_unavailable"
            else:
                confirmation = "METADATA_ELIGIBLE_NOT_YET_QUALIFIED"
        output.append(EntryQualification(
            event.event_id, code, issuer, signal, anchor, execution, reason, confirmation,
        ))
    # A lost/changed old event is a provenance defect, not new sample capacity.
    diagnostic = {r.event_id for r in output if r.diagnostic_disposition == "INCLUDE"}
    if not used.issubset(diagnostic):
        raise ValueError("ENTRY_QUALIFICATION_PRIOR_SCOPE_CHANGED")
    return tuple(output)


def qualification_summary(rows: Sequence[EntryQualification]) -> dict[str, Any]:
    if not rows or len({r.event_id for r in rows}) != len(rows):
        raise ValueError("ENTRY_QUALIFICATION_SUMMARY_POPULATION")
    metadata = [r for r in rows
                if r.confirmation_disposition == "METADATA_ELIGIBLE_NOT_YET_QUALIFIED"]
    return {
        "population": len(rows),
        "diagnostic_recount": dict(sorted(Counter(
            r.diagnostic_disposition for r in rows).items())),
        "confirmation_recount": dict(sorted(Counter(
            r.confirmation_disposition for r in rows).items())),
        "metadata_eligible_events": len(metadata),
        "metadata_eligible_issuers": len({r.issuer_id for r in metadata}),
        "cohort_gate": "EMPTY_FIXED_OPPORTUNITY_POPULATION" if not metadata
        else "METADATA_ELIGIBLE_REFERENCE_AND_SUPPORT_NOT_CHECKED",
        "independent_reference": "NOT_CHECKED",
        "recognition_quality": "NOT_EVALUATED",
        "whole_capital_comparison": "NOT_RUN_QUALIFICATION_REQUIRED",
        "consumer_admission": "NOT_GRANTED",
        "new_reference_price_or_outcome_rows_read": 0,
        "claim_ceiling": "Exact retained B population only; not global historical sample absence",
    }


def qualification_records(rows: Sequence[EntryQualification]) -> list[dict[str, Any]]:
    """Private audit rows; serialize dates without projecting prices or scores."""
    return [asdict(row) for row in rows]

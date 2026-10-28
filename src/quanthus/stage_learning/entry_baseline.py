"""Recover the exact retained B entry identities without opening economic results."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

from quanthus.event_library.contracts import EventIdentity
from quanthus.event_library.rule_card import CandidateRuleCard
from quanthus.stage_learning.entry_consolidation import EntryOpportunity
from quanthus.structure_v2_inputs import digest, exact_sha


def recover_baseline(
    preparation: Mapping[str, Any], decisions: Sequence[Mapping[str, Any]],
    card: CandidateRuleCard, *, runtime_snapshot_id: str,
) -> tuple[EntryOpportunity, ...]:
    """Bijection to the original producer's event namespace; never infer a fill.

    The caller verifies the exact retained preparation, B decision bytes and source
    authority. This projection uses only original decision metadata, not allocation,
    filled quantities, held decisions, prices, labels or subsequent returns.
    """
    cutoff = datetime.fromisoformat(preparation["source_information_cutoff"])
    source = preparation["effective_source_manifest_sha256"]
    if cutoff.tzinfo is None or not exact_sha(source):
        raise ValueError("ENTRY_BASELINE_SOURCE_IDENTITY")
    entries = {}
    for row in decisions:
        if row["surface"] != "entry":
            continue
        key = row["decision_key"]
        if (not key.startswith("entry:r2-event:") or key in entries
                or row["final_action"] not in {"trial_entry", "no_action"}
                or not exact_sha(row["formal_decision_digest"])):
            raise ValueError("ENTRY_BASELINE_DECISION_IDENTITY")
        entries[key] = row
    output = []
    seen = set()
    for row in preparation["candidates"]:
        identity = EventIdentity(
            row["security_id"], date.fromisoformat(row["signal_date"]),
            card.rule_id, card.rule_version, runtime_snapshot_id,
        )
        event = {
            "purpose_id": "p3-historical-development-robustness-v1",
            **identity.to_dict(), "knowledge_cutoff": cutoff.isoformat(),
            "business_close": datetime.fromisoformat(row["business_close"]).isoformat(),
            "candidate_source_known_at": cutoff.isoformat(),
            "candidate_rule_digest": card.semantic_digest,
            "snapshot_manifest_digest": source,
            "eligibility_evidence_digest": row["eligibility_evidence_digest"],
            "feature_window_manifest_digest": row["feature_evidence_digest"],
            "historical_availability_claim": False,
        }
        event_id = "r2-event:" + digest(event).removeprefix("sha256:")
        key = "entry:" + event_id
        if key in seen or key not in entries:
            raise ValueError("ENTRY_BASELINE_EVENT_BIJECTION")
        decision = entries[key]
        if decision["market_date"] != row["signal_date"]:
            raise ValueError("ENTRY_BASELINE_EVENT_DATE")
        seen.add(key)
        output.append(EntryOpportunity(
            event_id, row["security_id"], "PENDING_ISSUER_QUALIFICATION",
            identity.signal_date, decision["final_action"] == "trial_entry",
            decision["formal_decision_digest"],
        ))
    if seen != set(entries):
        raise ValueError("ENTRY_BASELINE_EVENT_BIJECTION")
    return tuple(output)


def alias_exclusion(
    listing_rows: Sequence[Mapping[str, Any]], security_rows: Sequence[Mapping[str, Any]],
    excluded_ids: set[str], excluded_codes: set[str],
) -> tuple[dict[str, str], set[str]]:
    """Conservative company/alias closure across all retained identity revisions.

    Static identity metadata is used only for retrospective clustering/exclusion,
    never as a historical industry or information-availability assertion.
    """
    companies: dict[str, set[str]] = {}
    for row in security_rows:
        companies.setdefault(row["security_id"], set()).add(row["company_id"])
    by_code: dict[str, set[str]] = {}
    excluded_companies = set()
    for row in listing_rows:
        values = companies.get(row["security_id"], set())
        by_code.setdefault(row["listing_code"], set()).update(values)
        if row["security_id"] in excluded_ids or row["listing_code"] in excluded_codes:
            excluded_companies.update(values)
    # Missing or ambiguous identity cannot be a qualified clustering coordinate.
    unique = {code: next(iter(values)) for code, values in by_code.items() if len(values) == 1}
    blocked = {code for code, values in by_code.items()
               if code in excluded_codes or values & excluded_companies}
    return unique, blocked

"""Offline census of an already-consumed held path, never a new trading policy.

Join the complete frozen denominator before summarising decisions. Missing Facts
remain missing, and differences in an existing shadow are not evidence of value.
This module neither reads market sources nor fits, scores or replays a model.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

Row = Mapping[str, Any]
Key = tuple[str, str]
HELD_ACTIONS = frozenset({"no_action", "reduce", "full_exit"})
GUARDS = frozenset({"none", "block_new", "reduce_only", "force_liquidate"})


def _text(row: Row, name: str) -> str:
    value = row.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError("CENSUS_INVALID_FIELD:" + name)
    return value


def _flag(row: Row, name: str) -> bool:
    value = row.get(name)
    if type(value) is not bool:
        raise ValueError("CENSUS_INVALID_BOOLEAN:" + name)
    return value


def _key(row: Row) -> Key:
    day = _text(row, "market_date")
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError("CENSUS_INVALID_DATE")
    return day, _text(row, "unit_id")


def _index(rows: Iterable[Row], name: str) -> dict[Key, Row]:
    result = {}
    for row in rows:
        key = _key(row)
        if key in result:
            raise ValueError("CENSUS_DUPLICATE:" + name)
        result[key] = row
    return result


def census_held_decisions(
    held: Sequence[Row],
    trace: Iterable[Row],
    shadow: Iterable[Row],
    support: Iterable[Row],
    *,
    expected: Mapping[str, int],
    first_date: date,
    last_date: date,
) -> dict[str, Any]:
    """Count recorded needs and safety overlap without choosing an alternative.

    ``expected`` is frozen before inspection, not inferred from surviving rows.
    The caller must authenticate the input files and code closure separately.
    Entry selection, allocation and fill alternatives are NOT_OBSERVED here.
    """
    identities = _index(held, "held")
    traces = _index(trace, "trace")
    shadows = _index(shadow, "shadow")
    if set(identities) != set(traces) or set(identities) != set(shadows):
        raise ValueError("CENSUS_REVIEW_DENOMINATOR")
    observed = {
        "reviews": len(held),
        "episodes": len({_text(r, "entry_key") for r in held}),
        "issuers": len({_text(r, "security_code") for r in held}),
        "unique_security_dates": len({(_text(r, "security_code"), _key(r)[0]) for r in held}),
    }
    if not identities or any(expected.get(k) != v for k, v in observed.items()):
        raise ValueError("CENSUS_FROZEN_DENOMINATOR")
    supports = {}
    for row in support:
        key = _text(row, "security_code"), _text(row, "market_date")
        if key in supports:
            raise ValueError("CENSUS_DUPLICATE:support")
        supports[key] = row
    required_support = {(_text(r, "security_code"), _key(r)[0]) for r in held}
    if set(supports) != required_support:
        raise ValueError("CENSUS_SUPPORT_DENOMINATOR")

    actions: Counter[str] = Counter()
    guards: Counter[str] = Counter()
    base_reasons: Counter[str] = Counter()
    unavailable: Counter[str] = Counter()
    shadow_reasons: Counter[str] = Counter()
    cross: Counter[str] = Counter()
    counts: Counter[str] = Counter()
    episode_owner: dict[str, tuple[str, str, str]] = {}
    changed_episodes, changed_issuers, changed_dates = set(), set(), set()
    proposed_episodes, overlap_episodes = set(), set()
    for key, identity in identities.items():
        day, unit = key
        code, episode = _text(identity, "security_code"), _text(identity, "entry_key")
        entry_day = _text(identity, "entry_date")
        if (
            type(identity.get("quantity")) is not int
            or identity["quantity"] < 0
            or date.fromisoformat(entry_day).isoformat() != entry_day
        ):
            raise ValueError("CENSUS_HELD_IDENTITY")
        if not first_date <= date.fromisoformat(day) <= last_date:
            raise ValueError("CENSUS_OUTSIDE_FROZEN_WINDOW")
        if date.fromisoformat(entry_day) > date.fromisoformat(day):
            raise ValueError("CENSUS_ENTRY_AFTER_REVIEW")
        owner = unit, code, entry_day
        if episode in episode_owner and episode_owner[episode] != owner:
            raise ValueError("CENSUS_EPISODE_IDENTITY")
        episode_owner[episode] = owner
        raw, delta = traces[key], shadows[key]
        fact = supports[code, day]
        if (
            raw.get("security_code") != code
            or delta.get("security_code") != code
            or delta.get("entry_key") != episode
            or raw.get("quantity") != identity.get("quantity")
        ):
            raise ValueError("CENSUS_JOIN_IDENTITY")
        context = raw.get("context")
        if not isinstance(context, dict) or context.get("security_code") != code:
            raise ValueError("CENSUS_CONTEXT_IDENTITY")
        cutoff = datetime.fromisoformat(_text(context, "knowledge_cutoff"))
        if cutoff.utcoffset() is None:
            raise ValueError("CENSUS_CONTEXT_CUTOFF")
        cutoff_day = cutoff.astimezone(ZoneInfo("Asia/Shanghai")).date()
        review_day = date.fromisoformat(day)
        # The accepted source is a retrospective snapshot. Record the actual
        # cutoff relation; never rewrite it to the market day or promote it to PIT.
        relation = (
            "same_session"
            if cutoff_day == review_day
            else ("later_snapshot" if cutoff_day > review_day else "earlier_cutoff")
        )
        counts["recorded_cutoff_" + relation] += 1
        action = _text(raw, "baseline_action")
        guard = _text(raw, "base_required_action")
        candidate = _text(delta, "candidate_action")
        if action not in HELD_ACTIONS or candidate not in HELD_ACTIONS or guard not in GUARDS:
            raise ValueError("CENSUS_HELD_ACTION_CONTRACT")
        if delta.get("baseline_action") != action or _text(
            delta, "baseline_decision_digest"
        ) != _text(raw, "baseline_decision_digest"):
            raise ValueError("CENSUS_BASELINE_BINDING")
        if (guard == "force_liquidate" and action != "full_exit") or (
            guard == "reduce_only" and action not in {"reduce", "full_exit"}
        ):
            raise ValueError("CENSUS_BASELINE_GUARD_NOT_ENFORCED")
        if (guard == "force_liquidate" and candidate != "full_exit") or (
            guard == "reduce_only" and candidate not in {"reduce", "full_exit"}
        ):
            raise ValueError("CENSUS_CANDIDATE_BYPASSES_GUARD")
        available = _flag(fact, "available")
        if (
            _flag(delta, "support_available") != available
            or delta.get("support_reason") != _text(fact, "reason")
            or delta.get("support_lineage_digest") != _text(fact, "lineage_digest")
        ):
            raise ValueError("CENSUS_FACT_BINDING")
        proposed, changed = _flag(delta, "proposed"), _flag(delta, "final_action_changed")
        if changed != (action != candidate) or (changed and not proposed):
            raise ValueError("CENSUS_ACTION_DELTA")
        if not available and (changed or proposed or _flag(fact, "stage10_active")):
            raise ValueError("CENSUS_UNAVAILABLE_IS_NOT_DAMAGE")
        actions[action] += 1
        guards[guard] += 1
        base_reasons[_text(raw, "base_reason_code")] += 1
        shadow_reasons[_text(delta, "reason")] += 1
        cross["|".join((action, guard, "available" if available else "unavailable"))] += 1
        counts["available_structure" if available else "unavailable_structure"] += 1
        counts["stage10_confirmation_reviews"] += _flag(fact, "stage10_active")
        counts["stage11_explanation_reviews"] += _flag(fact, "stage11_active")
        counts["existing_proposal_reviews"] += proposed
        counts["existing_safety_overlap_reviews"] += proposed and not changed
        counts["existing_changed_reviews"] += changed
        if not available:
            unavailable[_text(fact, "reason")] += 1
        if proposed:
            proposed_episodes.add(episode)
        if proposed and not changed:
            overlap_episodes.add(episode)
        if changed:
            changed_episodes.add(episode)
            changed_issuers.add(code)
            changed_dates.add(day)
    if expected.get("unavailable_structure") != counts["unavailable_structure"]:
        raise ValueError("CENSUS_UNAVAILABLE_DENOMINATOR")
    return {
        "kind": "held-decision-development-census-v1",
        "population": observed,
        "counts": dict(sorted(counts.items())),
        "baseline_actions": dict(sorted(actions.items())),
        "baseline_required_guards": dict(sorted(guards.items())),
        "baseline_reason_codes": dict(sorted(base_reasons.items())),
        "availability_by_action_and_guard": dict(sorted(cross.items())),
        "unavailable_reasons": dict(sorted(unavailable.items())),
        "existing_shadow_reasons": dict(sorted(shadow_reasons.items())),
        "existing_mapping_concentration": {
            "proposed_episodes": len(proposed_episodes),
            "safety_overlap_episodes": len(overlap_episodes),
            "changed_episodes": len(changed_episodes),
            "changed_issuers": len(changed_issuers),
            "changed_dates": len(changed_dates),
        },
        "unobserved_needs": {
            name: "NOT_OBSERVED_IN_HELD_TRACE"
            for name in ("entry_selection", "entry_timing", "allocation", "fill_alternatives")
        },
        "claim_ceiling": "DEVELOPMENT_DIAGNOSIS_NOT_INDEPENDENT_VALUE_OR_PIT_QUALIFICATION",
        "new_candidate_authorized": False,
        "historical_PIT_qualified": False,
        "data_unavailable_implies_sale": False,
    }

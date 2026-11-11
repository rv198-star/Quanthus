"""One frozen, retrospective queue-use diagnostic; no return, labels or orders."""
from __future__ import annotations

import json
import random
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Context, Decimal, localcontext
from pathlib import Path
from typing import Any

from quanthus.stage_learning.factor_slice import (
    FactorSliceError,
    PanelIdentity,
    verify_panel,
)
from quanthus.structure_v2_inputs import Admission, digest

POLICY = "config/research/factor541-five-session-diagnostic.v1.json"
POLICY_DIGEST = "sha256:9b415ba4bb34326a3ea557e6485b29206300454ca73783bda2e0d0c0eca44aef"


def load_contract(repo: Path) -> dict[str, Any]:
    spec: dict[str, Any] = json.loads((repo / POLICY).read_text())
    if digest(spec) != POLICY_DIGEST:
        raise FactorSliceError("FACTOR_USE_FROZEN_CONTRACT_CHANGED")
    return spec


def _keys(queue: Mapping[str, Any]) -> set[tuple[str, str]]:
    return {(r["security_id"], r["market_date"])
            for day in queue["days"] for r in day["selected"]}


def _arm(keys: set[tuple[str, str]], outcomes: Mapping[Any, Mapping[str, Any]]) -> dict[str, Any]:
    counts = Counter(outcomes[key]["outcome"] for key in keys)
    n, failures, unknown = len(keys), counts["quick_failure"], counts["unknown"]
    return {"slots": n, "quick_failure": failures,
            "no_quick_failure": counts["no_quick_failure"], "unknown": unknown,
            "failure_rate_lower": failures / n if n else None,
            "failure_rate_upper": (failures + unknown) / n if n else None}


def _paired(control: set[tuple[str, str]], candidate: set[tuple[str, str]],
            outcomes: Mapping[Any, Mapping[str, Any]]) -> dict[str, Any]:
    if len(control) != len(candidate) or not control:
        raise FactorSliceError("FACTOR_USE_EQUAL_COST_REQUIRED")
    removed, added = control - candidate, candidate - control
    security: dict[str, int] = defaultdict(int)
    days: dict[str, int] = defaultdict(int)
    months: dict[str, int] = defaultdict(int)
    unknown_control = unknown_candidate = 0
    for sign, keys in ((1, removed), (-1, added)):
        for key in sorted(keys):
            row = outcomes[key]
            contribution = sign * int(row["outcome"] == "quick_failure")
            security[key[0]] += contribution
            days[key[1]] += contribution
            months[key[1][:7]] += contribution
            if row["outcome"] == "unknown":
                unknown_control += int(sign == 1)
                unknown_candidate += int(sign == -1)
    net, n = sum(security.values()), len(control)
    low, high = net - unknown_candidate, net + unknown_control
    bootstrap: dict[str, Any] | None = None
    if security and unknown_control + unknown_candidate == 0:
        weights = [value for _, value in sorted(security.items())]
        rng = random.Random(5412021)
        samples = sorted(sum(rng.choice(weights) for _ in weights) for _ in range(10000))
        bootstrap = {"unit": "affected_security_cluster", "clusters": len(weights),
                     "resamples": 10000, "seed": 5412021,
                     "net_reduction_lower": samples[249],
                     "net_reduction_upper": samples[9749],
                     "rate_delta_lower": samples[249] / n,
                     "rate_delta_upper": samples[9749] / n,
                     "claim": "exploratory_cluster_sensitivity_not_independent_market_validation"}
    leave_security = min((net - x for x in security.values()), default=0)
    leave_day = min((net - x for x in days.values()), default=0)
    limited_positive = (low > 0 and bootstrap is not None
                        and bootstrap["net_reduction_lower"] > 0
                        and leave_security > 0 and leave_day > 0)
    return {"slots_per_arm": n, "common_slots": len(control & candidate),
            "removed": len(removed), "added": len(added),
            "affected_securities": len(security), "affected_dates": len(days),
            "known_net_fewer_quick_failures": net,
            "changed_unknown_control": unknown_control,
            "changed_unknown_candidate": unknown_candidate,
            "worst_case_net_fewer_quick_failures": low,
            "best_case_net_fewer_quick_failures": high,
            "rate_delta_lower": low / n, "rate_delta_upper": high / n,
            "common_unknowns_cancel_exactly": True, "bootstrap": bootstrap,
            "minimum_net_after_removing_one_security": leave_security,
            "minimum_net_after_removing_one_date": leave_day,
            "net_by_month": dict(sorted(months.items())),
            "disposition": ("LIMITED_POSITIVE_DIAGNOSTIC_ONLY" if limited_positive
                            else "PARK_VOLUME_GATE_INCONCLUSIVE_OR_UNFAVOURABLE"),
            "default_use": "not_promoted", "human_minutes_saved": None,
            "economic_value": "NOT_MEASURED", "strategy_or_P6_admitted": False}


def evaluate(
    admissions: tuple[Admission, ...], facts: Sequence[Mapping[str, Any]],
    anchors: Mapping[tuple[str, date], Decimal], baseline: Mapping[str, Any],
    candidate: Mapping[str, Any], *, identity: PanelIdentity, spec: Mapping[str, Any],
) -> dict[str, Any]:
    """Use the same admitted history and first Facts build; future rows score only."""
    if digest(spec) != POLICY_DIGEST:
        raise FactorSliceError("FACTOR_USE_FROZEN_CONTRACT_CHANGED")
    verify_panel(facts, identity)
    by_security: dict[str, list[Admission]] = defaultdict(list)
    for a in sorted(admissions, key=lambda a: a.key):
        if a.row.market_date.year != 2021:
            raise FactorSliceError("FACTOR_USE_PROTECTED_SCOPE")
        by_security[a.row.security_id].append(a)
    indexed = {(sid, a.row.market_date.isoformat()): (rows, index)
               for sid, rows in by_security.items() for index, a in enumerate(rows)}
    if len(indexed) != len(admissions) or set(indexed) != {
        (f["security_id"], f["market_date"]) for f in facts
    }:
        raise FactorSliceError("FACTOR_USE_SOURCE_DENOMINATOR")
    outcomes = {}
    for f in facts:
        key = f["security_id"], f["market_date"]
        rows, index = indexed[key]
        current = rows[index]
        if (current.row.member_digest != f["source_member_digest"]
                or current.admitted != f["admitted"]):
            raise FactorSliceError("FACTOR_USE_SOURCE_BINDING")
        if not f["admitted"] or f["resistance_margin20"] is None or Decimal(
            f["resistance_margin20"]
        ) <= 0:
            continue
        level = anchors.get(current.key)
        if level is None or level <= 0 or not isinstance(current.row.close, Decimal):
            raise FactorSliceError("FACTOR_USE_ANCHOR_MISSING")
        with localcontext(Context(prec=28)):
            margin = current.row.close / level - 1
        if format(margin, "f") != f["resistance_margin20"]:
            raise FactorSliceError("FACTOR_USE_ANCHOR_BINDING")
        future = rows[index + 1:index + 6]
        reasons = []
        if len(future) != 5:
            reasons.append("scope_end_incomplete_five_sessions")
        for offset, a in enumerate(future, start=1):
            if a.row.session_index != current.row.session_index + offset:
                reasons.append("session_gap")
            if (a.row.coordinate_ref != current.row.coordinate_ref or a.row.reset_reason
                    or a.row.listing_id != current.row.listing_id):
                reasons.append("coordinate_or_listing_boundary")
            if not a.admitted:
                reasons.append("unavailable_future:" + a.reason)
        if reasons:
            outcome = "unknown"
        else:
            closes = [a.row.close for a in future]
            if not all(isinstance(c, Decimal) and c.is_finite() for c in closes):
                raise FactorSliceError("FACTOR_USE_INVALID_ADMITTED_CLOSE")
            outcome = ("quick_failure" if any(c < level for c in closes if isinstance(c, Decimal))
                       else "no_quick_failure")
        outcomes[key] = {"security_id": key[0], "market_date": key[1], "outcome": outcome,
                         "reasons": sorted(set(reasons)),
                         "anchor_fact_revision": f["price_fact_revision"],
                         "T_source_member_digest": current.row.member_digest,
                         "future_source_member_digests": [a.row.member_digest for a in future],
                         "future_rows": len(future), "historical_knowledge_cutoff": None}
    base_keys, candidate_keys = _keys(baseline), _keys(candidate)
    if not base_keys | candidate_keys <= outcomes.keys():
        raise FactorSliceError("FACTOR_USE_QUEUE_OUTCOME_CLOSURE")
    base_days = {d["market_date"]: d for d in baseline["days"]}
    if set(base_days) != {d["market_date"] for d in candidate["days"]}:
        raise FactorSliceError("FACTOR_USE_QUEUE_DATE_CLOSURE")
    matched: set[tuple[str, str]] = set()
    for day in candidate["days"]:
        n = day["used_slots"]
        if (len(day["selected"]) != n or n > len(base_days[day["market_date"]]["selected"])
                or type(n) is not int or n < 0):
            raise FactorSliceError("FACTOR_USE_QUOTA_CLOSURE")
        matched.update((r["security_id"], r["market_date"])
                       for r in base_days[day["market_date"]]["selected"][:n])
    safe = {"schema_version": "quanthus.factor541-use-result.v1",
            "purpose": spec["purpose"], "contract_digest": POLICY_DIGEST,
            "full_price_pool": len(outcomes),
            "pool_outcomes": dict(sorted(Counter(r["outcome"] for r in outcomes.values()).items())),
            "unknown_reasons": dict(sorted(Counter(
                reason for r in outcomes.values() for reason in r["reasons"]).items())),
            "original_baseline": _arm(base_keys, outcomes),
            "candidate": _arm(candidate_keys, outcomes),
            "matched_count_control": _arm(matched, outcomes),
            "primary_paired": _paired(matched, candidate_keys, outcomes),
            "private_outcome_digest": digest([outcomes[k] for k in sorted(outcomes)]),
            "previously_exposed_2021_development_only": True,
            "historical_PIT_or_investment_value_admitted": False}
    return {"safe": safe, "private_rows": [outcomes[k] for k in sorted(outcomes)]}

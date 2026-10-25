"""Descriptive attribution of existing exposed historical runs.

Accounting partitions explain realized differences. They do not impute profits
for unexecuted orders or identify causal alpha from different capital paths.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.event_library.rule_card import CandidateRuleCard
from quanthus.event_library.scan import CandidateScanShard, _retain_raw_candidates
from quanthus.expert_strategy.p3_history_review import (
    ArchiveContract,
    ReviewedStudy,
    check_output,
    number,
    position_cycles,
    read_json,
    require,
    review_admitted_pair,
)

VERSION = "p3-existing-history-attribution-v1"
Q = Decimal


def coverage(
    raw_census: dict[str, Any],
    preparation: dict[str, Any],
    trading_days: list[str],
    card: CandidateRuleCard,
) -> dict[str, Any]:
    """Reapply the unchanged frozen selector; classify exclusions, never fill gaps."""
    shard = CandidateScanShard.from_dict(raw_census)
    date_index = {date.fromisoformat(day): index for index, day in enumerate(trading_days)}
    reasons: Counter[str] = Counter()
    retained, raw_count, isolated_count, gaps = _retain_raw_candidates(
        shard.raw_candidates, reasons=reasons, date_index=date_index, card=card
    )
    expected = {(c["security_id"], c["signal_date"]) for c in preparation["candidates"]}
    actual = {(c.security_id, c.signal_date.isoformat()) for c in retained}
    require(len(retained) == len(actual) and actual == expected, "FROZEN_RETAINED_POPULATION")
    require(
        raw_count == preparation["raw_triggers"]
        and isolated_count == preparation["isolated"]
        and len(gaps) == preparation["industry_gap_identities"]
        and len(retained) == preparation["retained_candidates"],
        "CENSUS_COUNTS",
    )
    by_year: dict[str, Counter[str]] = defaultdict(Counter)
    by_identity: dict[tuple[str, str, str, str], Counter[str]] = defaultdict(Counter)
    last_accepted: dict[str, int] = {}
    separation = max(
        card.isolation.cooldown_sessions, card.isolation.same_security_minimum_separation_sessions
    )
    for row in sorted(shard.raw_candidates, key=lambda r: (r.signal_date, r.security_id)):
        year = str(row.signal_date.year)
        identity = (row.security_id, row.listing_id, row.listing_code, row.company_id)
        buckets = (by_year[year], by_identity[identity])
        stage = "raw"
        for bucket in buckets:
            bucket[stage] += 1
        index = date_index[row.signal_date]
        previous = last_accepted.get(row.security_id)
        if previous is not None and index - previous < separation:
            stage = "cooldown_excluded"
        else:
            last_accepted[row.security_id] = index
            for bucket in buckets:
                bucket["post_cooldown"] += 1
            if row.industry_id is None or not row.industry_id.strip():
                stage = "industry_excluded"
            elif (row.security_id, row.signal_date.isoformat()) in actual:
                stage = "retained"
            else:
                stage = "concentration_excluded"
        for bucket in buckets:
            bucket[stage] += 1
    fields = (
        "raw",
        "cooldown_excluded",
        "post_cooldown",
        "industry_excluded",
        "concentration_excluded",
        "retained",
    )

    def complete(counter: Counter[str]) -> dict[str, int]:
        result = {key: counter[key] for key in fields}
        require(
            result["raw"] == result["cooldown_excluded"] + result["post_cooldown"]
            and result["post_cooldown"]
            == result["industry_excluded"] + result["concentration_excluded"] + result["retained"],
            "CENSUS_PARTITION",
        )
        return result

    totals = complete(sum(by_year.values(), Counter()))
    require(
        totals["post_cooldown"] == isolated_count
        and totals["industry_excluded"] == sum(gaps.values()),
        "CENSUS_ISOLATION",
    )
    return {
        "totals": totals,
        "by_year": {year: complete(v) for year, v in sorted(by_year.items())},
        "identity_count": len(by_identity),
        "industry_missing_identities": len(gaps),
        "by_identity": [
            {
                "security_id": identity[0],
                "listing_id": identity[1],
                "listing_code": identity[2],
                "company_id": identity[3],
                **complete(counter),
                "historical_listing_lifecycle": "NOT_ESTABLISHED_BY_CANDIDATE_CENSUS",
            }
            for identity, counter in sorted(by_identity.items())
        ],
        "scope_note": (
            "Fractions describe candidate observations after cooldown, not a full-market "
            "security denominator. Listing identities are preserved; historical listing/"
            "delisting dates are not in this census and are not inferred from last observation. "
            "Static industry remains a late snapshot, not PIT."
        ),
    }


def _entries(data: dict[str, Any], coordinates: dict[str, str]) -> dict[str, Any]:
    result = {
        coordinates[d["decision_key"]]: d for d in data["decisions.json"] if d["surface"] == "entry"
    }
    require(len(result) == len(coordinates), "ENTRY_PARTITION")
    return result


def _sum_cycles(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {"cycles": len(rows)}
    for name in ("gross_and_terminal_mark", "dividends", "fees", "net_pnl"):
        result[name] = str(sum((number(row[name]) for row in rows), Q(0)))
    require(
        number(result["gross_and_terminal_mark"])
        + number(result["dividends"])
        - number(result["fees"])
        == number(result["net_pnl"]),
        "COHORT_ACCOUNTING",
    )
    result["closed"] = sum(row["closed"] for row in rows)
    result["terminal_open"] = len(rows) - result["closed"]
    result["holding_calendar_days"] = sum(row["holding_calendar_days"] for row in rows)
    result["profitable_cycles"] = sum(number(row["net_pnl"]) > 0 for row in rows)
    return result


def _periods(equity: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    periods: dict[str, tuple[int, int]] = {
        **{str(year): (year, year) for year in range(2012, 2022)},
        **{f"{year}-{year + 1}": (year, year + 1) for year in range(2012, 2022, 2)},
    }
    result = {}
    for label, (begin, end) in periods.items():
        selected = [
            index
            for index, row in enumerate(equity[1:], 1)
            if begin <= int(row["market_date"][:4]) <= end
        ]
        if not selected:
            continue
        first = number(equity[selected[0] - 1]["system_equity"])
        last = number(equity[selected[-1]]["system_equity"])
        result[label] = {
            "sessions": len(selected),
            "net_pnl": str(last - first),
            "net_total_return": str(last / first - 1),
        }
    annual = sum(
        (number(value["net_pnl"]) for key, value in result.items() if "-" not in key), Q(0)
    )
    require(
        annual == number(equity[-1]["system_equity"]) - number(equity[0]["system_equity"]),
        "ANNUAL_ACCOUNTING",
    )
    return result


def attribute(
    study: ReviewedStudy,
    contract: ArchiveContract,
    *,
    comparisons: tuple[tuple[str, str], ...] = (("T", "B"), ("TCH", "B"), ("TCH", "T")),
) -> dict[str, Any]:
    cycles = {
        arm: position_cycles(data, study.coordinates, contract)
        for arm, data in study.primary.items()
    }
    entries = {arm: _entries(data, study.coordinates) for arm, data in study.primary.items()}
    capital = number(contract.initial_unit_capital) * len(contract.unit_ids)
    variants = {}
    for arm, data in study.primary.items():
        rows = list(entries[arm].values())
        require(set(entries[arm]) == set(study.coordinates.values()), "COMMON_CANDIDATES")
        cohort = _sum_cycles(cycles[arm])
        require(cohort["net_pnl"] == study.summaries[arm]["net_pnl"], "ARM_ACCOUNTING")
        variants[arm] = {
            **study.summaries[arm],
            "cycle_summary": cohort,
            "entry_decisions": {
                "count": len(rows),
                "final_actions": dict(sorted(Counter(r["final_action"] for r in rows).items())),
                "allocated": sum(r["allocated"] for r in rows),
                "filled": sum(r["filled"] for r in rows),
                "score_above_zero": sum(number(r["aggregate_score"]) > 0 for r in rows),
                "reason_sets": dict(
                    sorted(Counter("|".join(r["reason_codes"]) for r in rows).items())
                ),
            },
            "actual_exit_signal_reasons": dict(
                sorted(
                    Counter(
                        event["payload"]["decision_reason_code"]
                        for event in data["engine-events.json"]
                        if event["event_type"] == "signal_accepted"
                        and event["payload"]["side"] == "sell"
                    ).items()
                )
            ),
            "calendar_periods": _periods(data["equity.json"]),
        }
    contrasts = {}
    for left, right in comparisons:
        if left not in cycles or right not in cycles:
            continue
        ll = {row["coordinate"]: row for row in cycles[left]}
        rr = {row["coordinate"]: row for row in cycles[right]}
        shared = set(ll) & set(rr)
        left_only, right_only = set(ll) - shared, set(rr) - shared
        shared_left = _sum_cycles([ll[k] for k in sorted(shared)])
        shared_right = _sum_cycles([rr[k] for k in sorted(shared)])
        only_left = _sum_cycles([ll[k] for k in sorted(left_only)])
        only_right = _sum_cycles([rr[k] for k in sorted(right_only)])
        pieces = {
            "shared_realized_cycle_delta": number(shared_left["net_pnl"])
            - number(shared_right["net_pnl"]),
            "left_only_realized_pnl": number(only_left["net_pnl"]),
            "minus_right_only_realized_pnl": -number(only_right["net_pnl"]),
        }
        expected = number(study.summaries[left]["net_pnl"]) - number(
            study.summaries[right]["net_pnl"]
        )
        require(sum(pieces.values(), Q(0)) == expected, "CONTRAST_ACCOUNTING")
        transitions: Counter[str] = Counter()
        outcome: Counter[str] = Counter()
        for key in entries[left]:
            lrow, rrow = entries[left][key], entries[right][key]
            transitions[lrow["final_action"] + " / " + rrow["final_action"]] += 1
            outcome[f"{int(lrow['filled'])}/{int(rrow['filled'])}"] += 1

        def unmatched_reasons(
            rows: dict[str, dict[str, Any]], keys: set[str], other: dict[str, Any]
        ) -> dict[str, dict[str, Any]]:
            groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for key in sorted(keys):
                decision = other[key]
                reason = decision["final_action"] + ":" + "|".join(decision["reason_codes"])
                groups[reason].append(rows[key])
            return {reason: _sum_cycles(values) for reason, values in sorted(groups.items())}

        financial_fields = (
            "entry_date",
            "end_date",
            "closed",
            "fills",
            "gross_and_terminal_mark",
            "dividends",
            "fees",
            "net_pnl",
        )
        contrasts[left + "-" + right] = {
            "left": left,
            "right": right,
            "net_pnl_delta": str(expected),
            "return_delta": str(expected / capital),
            "accounting_residual": "0",
            "components": {k: str(v) for k, v in pieces.items()},
            "component_return_deltas": {k: str(v / capital) for k, v in pieces.items()},
            "shared_cycle_count": len(shared),
            "shared_financially_identical_cycle_count": sum(
                all(ll[key][field] == rr[key][field] for field in financial_fields)
                for key in shared
            ),
            "left_only_cycle_count": len(left_only),
            "right_only_cycle_count": len(right_only),
            "shared_left": shared_left,
            "shared_right": shared_right,
            "left_only": only_left,
            "right_only": only_right,
            "left_only_by_other_entry_decision": unmatched_reasons(ll, left_only, entries[right]),
            "right_only_by_other_entry_decision": unmatched_reasons(rr, right_only, entries[left]),
            "entry_action_transitions_left_right": dict(sorted(transitions.items())),
            "entry_filled_left_right": dict(sorted(outcome.items())),
            "interpretation": (
                "An exact partition of actual outcomes, not a counterfactual attribution. "
                "Shared entry coordinates can still have different quantity, unit, exit and "
                "capital competition; arm-only profits were never available by assumption."
            ),
        }
    return {
        "version": VERSION,
        "scope": contract.scope,
        "variants": variants,
        "contrasts": contrasts,
        "new_replay": False,
        "threshold_search": False,
        "no_counterfactual_profit_imputed": True,
    }


def main() -> None:
    from quanthus.event_library.rule_card import load_candidate_rule_card

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--primary", type=Path, required=True)
    parser.add_argument("--cold", type=Path, required=True)
    parser.add_argument("--admission-evidence", type=Path, required=True)
    parser.add_argument("--input-bindings", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    check_output(args.output, (args.primary, args.cold))
    bindings, bindings_sha = read_json(args.input_bindings)
    require(set(bindings) == {"raw_census", "preparation", "rule_card"}, "ATTRIBUTION_INPUTS")
    inputs = {
        key: read_json(Path(record["path"]), record["sha256"])[0]
        for key, record in bindings.items()
    }
    card = load_candidate_rule_card(Path(bindings["rule_card"]["path"]))
    reviewed, contract = review_admitted_pair(
        args.contract, args.primary, args.cold, args.admission_evidence
    )
    require(
        inputs["preparation"]["effective_source_manifest_sha256"] == contract.source_manifest_sha256
        and inputs["preparation"]["raw_candidate_census_sha256"]
        == bindings["raw_census"]["sha256"],
        "ATTRIBUTION_SOURCE_BINDING",
    )
    census = coverage(
        inputs["raw_census"],
        inputs["preparation"],
        [row["market_date"] for row in reviewed.primary["B"]["equity.json"]],
        card,
    )
    result = {
        "version": VERSION,
        "input_bindings_sha256": bindings_sha,
        "validation": reviewed.proof,
        "coverage": census,
        "attribution": attribute(reviewed, contract),
    }
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


if __name__ == "__main__":
    main()

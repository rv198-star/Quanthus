"""T-only blind packets and a security/day research review-list consumer.

This module grants no reference, research-execution or production authority.
Callers freeze the full opportunity set before supplying references/predictions.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any


class ReviewListError(ValueError):
    """The packet or frozen opportunity set is inconsistent."""


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _number(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ReviewListError("NONFINITE_OBSERVATION") from exc
    if not result.is_finite():
        raise ReviewListError("NONFINITE_OBSERVATION")
    return result


def blind_packet(packet: Mapping[str, Any], case_id: str) -> dict[str, Any]:
    """Whitelist raw T-only observations; remove all candidate-derived state.

    Prices use T close as a display unit; volume uses the largest observed volume.
    Original exact observations/identity bindings stay in the caller's private pack.
    Ratios are display values, not a change to the underlying warehouse data.
    """
    cutoff = date.fromisoformat(packet["market_date"])
    scale = packet["anchor_scale"]
    rows = packet["history"]
    if cutoff.year != 2021 or scale not in (20, 60) or not rows or len(rows) > 61:
        raise ReviewListError("PILOT_SCOPE_OR_HISTORY")
    dates = [date.fromisoformat(row["market_date"]) for row in rows]
    if dates != sorted(set(dates)) or dates[-1] != cutoff or any(d > cutoff for d in dates):
        raise ReviewListError("FUTURE_DUPLICATE_OR_UNORDERED_HISTORY")
    unit = _number(rows[-1]["close"])
    if unit <= 0:
        raise ReviewListError("INVALID_DISPLAY_UNIT")
    volumes = [_number(r["volume"]) for r in rows if r.get("volume") is not None]
    volume_unit = max(volumes, default=Decimal(0))
    if any(v < 0 for v in volumes):
        raise ReviewListError("NEGATIVE_VOLUME")
    observations = []
    contiguous = 0
    previous_epoch = None
    for index, row in enumerate(rows):
        if type(row["admitted"]) is not bool:
            raise ReviewListError("INVALID_ADMISSION_FLAG")
        item: dict[str, Any] = {
            "offset": index - len(rows) + 1, "date": row["market_date"],
            "admitted": row["admitted"], "reason": row["reason"],
            "reset_reason": row["reset_reason"],
        }
        epoch = row.get("coordinate_epoch")
        coordinate_break = index > 0 and epoch != previous_epoch
        if not row["admitted"] or row["reset_reason"] or coordinate_break:
            contiguous = 0
        if row["admitted"]:
            contiguous += 1
        with localcontext() as ctx:
            ctx.prec = 40
            for name in ("high", "low", "close", "volume"):
                value = row.get(name)
                denominator = volume_unit if name == "volume" else unit
                item[name] = (None if value is None or denominator == 0
                              else format(_number(value) / denominator, ".10f"))
        observations.append(item)
        previous_epoch = epoch
    result = {
        "schema_version": "quanthus.stage34-blind-method-packet.v1",
        "case_id": case_id, "as_of": cutoff.isoformat(), "scale_sessions": scale,
        "observed_prior_sessions": len(rows) - 1,
        "contiguous_prior_sessions": max(0, contiguous - 1),
        "scale_history_sufficient": contiguous - 1 >= scale,
        "price_display_unit": "T_close=1", "volume_display_unit": "visible_max=1",
        "observations": observations,
        "evidence_grade": "RETROSPECTIVE_COORDINATE_METHOD_PILOT_ONLY",
    }
    return {**result, "packet_sha256": _hash(result)}


def validate_judgements(packets: Sequence[Mapping[str, Any]],
                        judgements: Sequence[Mapping[str, Any]]) -> None:
    """Check identities and citations, never manufacture semantic truth."""
    by_id = {p["case_id"]: p for p in packets}
    if len(by_id) != len(packets) or len(judgements) != len(packets):
        raise ReviewListError("INCOMPLETE_REVIEW")
    if any(p["packet_sha256"] != _hash({k: v for k, v in p.items()
                                       if k != "packet_sha256"}) for p in packets):
        raise ReviewListError("PACKET_CONTENT_IDENTITY")
    seen = set()
    for review in judgements:
        case_id = review["case_id"]
        if case_id not in by_id or case_id in seen:
            raise ReviewListError("DUPLICATE_OR_UNKNOWN_REVIEW")
        seen.add(case_id)
        packet = by_id[case_id]
        if review["packet_sha256"] != packet["packet_sha256"]:
            raise ReviewListError("REVIEW_INPUT_IDENTITY")
        kind, codes = review["kind"], review["stage_codes"]
        if (kind not in ("definite", "ambiguous_set", "unjudgeable")
                or not isinstance(codes, list) or codes != sorted(set(codes))
                or any(type(c) is not int or c not in (3, 4) for c in codes)
                or (kind == "definite" and len(codes) > 1)
                or (kind == "ambiguous_set" and len(codes) < 2)
                or (kind == "unjudgeable" and codes)):
            raise ReviewListError("REVIEW_MEANING")
        evidence = review.get("evidence", [])
        if kind == "definite" and len({e["offset"] for e in evidence}) < 2:
            raise ReviewListError("ASSERTION_WITHOUT_EVIDENCE")
        observations = {o["offset"]: o for o in packet["observations"]}
        for cited in evidence:
            row = observations.get(cited["offset"])
            if (type(cited["offset"]) is not int or row is None
                    or cited["field"] not in ("high", "low", "close", "volume")
                    or row[cited["field"]] != cited["value"]):
                raise ReviewListError("UNBOUND_CITATION")
        if (not review.get("reason") or not review.get("counterevidence")
                or review.get("review_worthiness") not in ("yes", "no", "unknown")):
            raise ReviewListError("MISSING_RATIONALE_OR_UNCERTAINTY")


def review_lists(opportunities: Sequence[Mapping[str, Any]],
                 predictions: Mapping[str, int | None], *,
                 frozen_context_ids: Sequence[str], seed: str, slots: int = 5
                 ) -> dict[str, Any]:
    """Rank a full frozen universe, spending one slot per security/date.

    Cross-scale assertions are retained inside that unit; they are not extra votes.
    An unknown prediction remains in the denominator and does not nominate a unit.
    """
    ids = [r["context_id"] for r in opportunities]
    if (len(set(ids)) != len(ids) or len(set(frozen_context_ids)) != len(frozen_context_ids)
            or set(ids) != set(frozen_context_ids) or set(predictions) != set(ids)):
        raise ReviewListError("OPPORTUNITY_UNIVERSE_MISMATCH")
    if type(slots) is not int or slots != 5 or not isinstance(seed, str) or not seed:
        raise ReviewListError("FROZEN_LIST_POLICY")
    units: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in opportunities:
        assertion = predictions[row["context_id"]]
        if assertion is not None and (type(assertion) is not int or assertion not in (3, 4)):
            raise ReviewListError("INVALID_ASSERTION")
        if type(row["scale"]) is not int or row["scale"] not in (20, 60):
            raise ReviewListError("INVALID_SCALE")
        if date.fromisoformat(row["market_date"]).year != 2021:
            raise ReviewListError("FROZEN_RESEARCH_YEAR")
        if any(not isinstance(row[k], str) or not row[k]
               for k in ("security_id", "context_id", "episode_id")):
            raise ReviewListError("MISSING_BUSINESS_IDENTITY")
        units[row["security_id"], row["market_date"]].append(
            {"context_id": row["context_id"], "scale": row["scale"],
             "episode_id": row["episode_id"], "assertion": assertion})
    days: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (security_id, day), assertions in sorted(units.items()):
        codes = [a["assertion"] for a in assertions if a["assertion"] is not None]
        days[day].append({"security_id": security_id, "market_date": day,
                          "assertions": sorted(assertions, key=lambda x: x["context_id"]),
                          "priority": max(codes) if codes else None})
    output = []
    for day, eligible in sorted(days.items()):
        nominated = [u for u in eligible if u["priority"] is not None]
        nominated.sort(key=lambda u: (-u["priority"],
                                     _hash([seed, day, u["security_id"]])))
        selected = nominated[:slots]
        output.append({"market_date": day, "eligible_units": len(eligible),
                       "eligible_contexts": sum(len(u["assertions"]) for u in eligible),
                       "unknown_contexts": sum(a["assertion"] is None for u in eligible
                                               for a in u["assertions"]),
                       "nominated_units": len(nominated), "used_slots": len(selected),
                       "unused_slots": slots - len(selected),
                       "selection_budget_binding": len(nominated) > slots,
                       "selected": selected})
    return {"schema_version": "quanthus.stage34-review-list.v2",
            "universe_digest": _hash(sorted(ids)), "days": output,
            "total_contexts": len(ids), "total_units": len(units),
            "actual_review_cost_units": sum(d["used_slots"] for d in output),
            "consumer_test_authorized": False, "production_or_P6_admitted": False}


def review_use(opportunities: Sequence[Mapping[str, Any]],
               predictions: Mapping[str, int | None],
               references: Mapping[str, Mapping[str, Any]], *,
               frozen_context_ids: Sequence[str], seed: str) -> dict[str, Any]:
    """Measure a frozen research list; reference quality is a separate prerequisite."""
    result = review_lists(opportunities, predictions, frozen_context_ids=frozen_context_ids,
                          seed=seed)
    if set(references) != set(frozen_context_ids):
        raise ReviewListError("REFERENCE_UNIVERSE_MISMATCH")
    rows = {r["context_id"]: r for r in opportunities}
    selected = {a["context_id"] for day in result["days"] for unit in day["selected"]
                for a in unit["assertions"]}
    positive, matched, false_alert, ambiguous, unjudgeable = (set() for _ in range(5))
    wrong_stage = negative_alert = ambiguous_assertions = 0
    worthiness: Counter[str] = Counter()
    for context_id, reference in references.items():
        kind, codes = reference["kind"], reference["stage_codes"]
        if (kind not in ("definite", "ambiguous_set", "unjudgeable")
                or not isinstance(codes, list) or codes != sorted(set(codes))
                or any(type(code) is not int or code not in (3, 4) for code in codes)
                or (kind == "definite" and len(codes) > 1)
                or (kind == "ambiguous_set" and codes != [3, 4])
                or (kind == "unjudgeable" and codes)):
            raise ReviewListError("REFERENCE_MEANING")
        row = rows[context_id]
        unit_key = (row["security_id"], row["market_date"])
        if kind == "definite" and codes:
            positive.add(unit_key)
        if context_id not in selected or predictions[context_id] is None:
            continue
        worth = reference.get("review_worthiness", "not_supplied")
        if worth not in ("yes", "no", "unknown", "not_supplied"):
            raise ReviewListError("REFERENCE_REVIEW_WORTHINESS")
        worthiness[worth] += 1
        prediction = predictions[context_id]
        if kind == "definite":
            if prediction in codes:
                matched.add(unit_key)
            else:
                false_alert.add(unit_key)
                if codes:
                    wrong_stage += 1
                else:
                    negative_alert += 1
        elif kind == "ambiguous_set":
            ambiguous.add(unit_key)
            ambiguous_assertions += 1
        else:
            unjudgeable.add(unit_key)
    return {**result, "selected_matched_positive_units": len(matched),
            "missed_positive_units": len(positive - matched),
            "false_alert_units": len(false_alert), "wrong_stage_assertions": wrong_stage,
            "negative_alert_assertions": negative_alert,
            "ambiguous_assertion_units": len(ambiguous),
            "ambiguous_unique_assertions": ambiguous_assertions,
            "selected_unjudgeable_assertion_units": len(unjudgeable),
            "total_definite_positive_units": len(positive),
            "selected_assertion_review_worthiness": {
                key: worthiness[key] for key in ("yes", "no", "unknown", "not_supplied")
            },
            "human_time_saved_or_economic_value_claim": False}


def compare_review_use(opportunities: Sequence[Mapping[str, Any]],
                       arms: Mapping[str, Mapping[str, int | None]],
                       references: Mapping[str, Mapping[str, Any]], *,
                       frozen_context_ids: Sequence[str], seed: str,
                       frozen_opportunity_grid: Sequence[tuple[str, str, int]]
                       ) -> dict[str, Any]:
    expected = set(frozen_opportunity_grid)
    actual = {(r["security_id"], r["market_date"], r["scale"]) for r in opportunities}
    scales: dict[tuple[str, str], set[int]] = defaultdict(set)
    for security, day, scale in expected:
        scales[security, day].add(scale)
    if (len(expected) != len(frozen_opportunity_grid) or actual != expected
            or any(value != {20, 60} for value in scales.values())):
        raise ReviewListError("DAILY_GRID_NOT_COMPLETE_BOTH_SCALES")
    if set(arms) != {"frozen_R1", "frozen_R0"}:
        raise ReviewListError("FROZEN_ARMS_REQUIRED")
    results = {name: review_use(opportunities, predictions, references,
                               frozen_context_ids=frozen_context_ids, seed=seed)
               for name, predictions in arms.items()}
    candidate, baseline = results["frozen_R1"], results["frozen_R0"]
    equal_cost = candidate["actual_review_cost_units"] == baseline["actual_review_cost_units"]
    positive_delta = (candidate["selected_matched_positive_units"]
                      - baseline["selected_matched_positive_units"])
    false_delta = candidate["false_alert_units"] - baseline["false_alert_units"]
    unresolved_no_worse = all(candidate[key] <= baseline[key] for key in (
        "ambiguous_assertion_units", "selected_unjudgeable_assertion_units"))
    benefit = (equal_cost and positive_delta >= 0 and false_delta <= 0
               and (positive_delta > 0 or false_delta < 0) and unresolved_no_worse)
    return {"arms": results, "same_actual_review_cost": equal_cost,
            "bounded_use_improvement": benefit,
            "disposition": "BOUNDED_LIST_DOMINANCE" if benefit else "NO_BOUNDED_BENEFIT_PROVEN",
            "reference_quality_or_consumer_admission_proven": False,
            "production_or_P6_admitted": False}

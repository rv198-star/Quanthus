"""Bounded development experiment for Stage-0/2 organization propositions.

The runner consumes accepted Trend Evolution and Structure Order facts over a frozen 512-security
2020-2021 development cohort. Synthetic references see T-only fact values before candidate search.
No future outcome, holdout, official LabelCard, primary-stage arbitration, strategy, or trading
authority is admitted.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import re
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.stage_learning.source import (
    DevelopmentReadScope,
    discover_development_security_population,
    read_development_admissions,
)
from quanthus.structure_order_facts import (
    RANGE_CENTER_SHIFT,
    RANGE_WIDTH,
    WIDTH_CHANGE,
    StructureOrderMeasurement,
    build_structure_order_facts,
)
from quanthus.structure_trend_evolution import (
    DIRECTIONAL_EFFICIENCY,
    TrendMeasurement,
    build_trend_evolution_facts,
)
from quanthus.structure_v2_inputs import Admission, canonical, digest

DEFAULT_CONFIG = "config/stage-learning/order-proposition-development.v1.json"
_REVISION = re.compile(r"^[0-9a-f]{40}$")
LABEL_STAGE_0 = "stage_0"
LABEL_STAGE_2 = "stage_2"
LABEL_AMBIGUOUS = "ambiguous"
LABEL_NEITHER = "neither"
LABELS = (LABEL_STAGE_0, LABEL_STAGE_2, LABEL_AMBIGUOUS, LABEL_NEITHER)


class StageOrderPropositionError(ValueError):
    """Raised when the Stage-0/2 development contract is violated."""


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StageOrderPropositionError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise StageOrderPropositionError(f"{label} must be a JSON object")
    return value


def _date(value: Mapping[str, Any], key: str) -> date:
    try:
        return date.fromisoformat(str(value[key]))
    except (KeyError, ValueError) as exc:
        raise StageOrderPropositionError(f"invalid development date: {key}") from exc


def _decimal(value: object, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except Exception as exc:
        raise StageOrderPropositionError(f"invalid decimal: {label}") from exc
    if not parsed.is_finite():
        raise StageOrderPropositionError(f"non-finite decimal: {label}")
    return parsed


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _progress(started: float, stage: str, **details: Any) -> None:
    print(
        json.dumps(
            {
                "event": "stage-order-proposition-development-progress",
                "stage": stage,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                **details,
            },
            ensure_ascii=True,
            sort_keys=True,
        ),
        flush=True,
    )


def load_order_proposition_config(path: Path) -> dict[str, Any]:
    raw = _object(path, "order proposition development config")
    required = {
        "schema_version",
        "development_id",
        "work_item",
        "purpose",
        "training_node_config",
        "trend_fact_contract",
        "trend_fact_contract_digest",
        "order_fact_contract",
        "order_fact_contract_digest",
        "synthetic_panel_config",
        "authority",
        "development_window",
        "population",
        "proposition_identity",
        "reference_sampling",
        "r0",
        "search_space",
        "execution_order",
        "hard_gates",
        "selection",
        "artifact",
        "claim_limits",
    }
    if (
        set(raw) != required
        or raw.get("schema_version") != "quanthus.stage-order-proposition-development.v1"
        or raw.get("work_item") != "P3-STAGE-004"
    ):
        raise StageOrderPropositionError("order proposition config identity differs")
    authority = raw.get("authority")
    required_true = {
        "development_only",
        "runtime_implementation_authorized",
        "development_data_read_authorized",
        "formal_fact_consumption_authorized",
        "synthetic_reference_runtime_authorized",
        "limited_parameter_search_runtime_authorized",
        "development_proposition_candidate_artifact_authorized",
    }
    if not isinstance(authority, Mapping):
        raise StageOrderPropositionError("order proposition authority missing")
    for key, value in authority.items():
        expected = key in required_true
        if value is not expected:
            raise StageOrderPropositionError("order proposition authority differs")
    identity = raw.get("proposition_identity")
    if not isinstance(identity, Mapping) or (
        identity.get("stage_0_code") != 0
        or identity.get("stage_2_code") != 2
        or identity.get("stage_0_is_not_residual") is not True
        or identity.get("stage_2_implies_future_breakout") is not False
        or identity.get("both_false_allowed") is not True
        or identity.get("both_true_allowed") is not False
        or identity.get("primary_stage_output") is not False
    ):
        raise StageOrderPropositionError("order proposition identity differs")
    window = raw.get("development_window")
    population = raw.get("population")
    sampling = raw.get("reference_sampling")
    if not all(isinstance(item, Mapping) for item in (window, population, sampling)):
        raise StageOrderPropositionError("order proposition sections missing")
    scan = _date(window, "cohort_scan_start")
    cohort = _date(window, "cohort_date")
    warmup = _date(window, "warmup_start")
    start = _date(window, "report_start")
    end = _date(window, "report_end")
    if not (date(2012, 1, 1) <= scan <= warmup <= cohort < start <= end <= date(2021, 12, 31)):
        raise StageOrderPropositionError("order proposition must stay in development dates")
    if (
        population.get("cohort_size") != 512
        or population.get("one_reference_context_per_security") is not True
        or sampling.get("target_contexts_per_bucket") != 40
        or sampling.get("minimum_realized_contexts") != 120
        or sampling.get("one_context_per_security") is not True
    ):
        raise StageOrderPropositionError("order proposition population/sampling differs")
    if raw.get("execution_order") != [
        "resolve_references",
        "search_stage_2",
        "freeze_stage_2_if_eligible",
        "search_stage_0_with_pair_conflict_gate",
    ]:
        raise StageOrderPropositionError("order proposition execution order differs")
    return raw


def load_order_proposition_panel(path: Path) -> dict[str, Any]:
    raw = _object(path, "order proposition synthetic panel")
    if (
        raw.get("schema_version") != "quanthus.stage-order-proposition-synthetic-panel.v1"
        or raw.get("work_item") != "P3-STAGE-004"
        or raw.get("evidence_tier") != "SYNTHETIC_EXPERT_CONSENSUS"
        or raw.get("human_expert_validated") is not False
    ):
        raise StageOrderPropositionError("order proposition panel identity differs")
    assignment = raw.get("assignment")
    policies = raw.get("policies")
    semantics = raw.get("reference_semantics")
    if not all(isinstance(item, Mapping) for item in (assignment, policies, semantics)):
        raise StageOrderPropositionError("order proposition panel sections missing")
    ids = [str(assignment.get(key) or "") for key in ("primary_a", "primary_b", "adjudicator")]
    if len(set(ids)) != 3 or not set(ids) <= set(policies):
        raise StageOrderPropositionError("order proposition reviewer identities differ")
    if (
        assignment.get("primary_reviews_are_mutually_hidden") is not True
        or semantics.get("stage_0_is_not_residual") is not True
        or semantics.get("stage_2_implies_future_breakout") is not False
        or semantics.get("both_false_allowed") is not True
        or semantics.get("both_true_allowed") is not False
        or semantics.get("reviewers_never_receive_candidate_parameters") is not True
        or semantics.get("reviewers_never_receive_future_outcomes") is not True
    ):
        raise StageOrderPropositionError("order proposition panel semantics differ")
    authority = raw.get("authority")
    if not isinstance(authority, Mapping) or authority != {
        "development_only": True,
        "semantic_reference_authorized": True,
        "future_outcome_access_authorized": False,
        "arbitration_access_authorized": False,
        "permanent_blind_access_authorized": False,
        "formal_qualification_authorized": False,
    }:
        raise StageOrderPropositionError("order proposition panel authority differs")
    return raw


@dataclass(frozen=True)
class OrderObservation:
    security_code: str
    market_date: date
    evidence_digest: str
    efficiency20_abs: Decimal
    range_width20: Decimal
    width_change10: Decimal
    width_change20: Decimal
    center_shift10_abs: Decimal
    center_shift20_abs: Decimal

    @property
    def context_id(self) -> str:
        return digest(
            {
                "security_code": self.security_code,
                "market_date": self.market_date,
                "evidence_digest": self.evidence_digest,
            }
        )

    def feature_dict(self) -> dict[str, str]:
        return {
            "efficiency20_abs": str(self.efficiency20_abs),
            "range_width20": str(self.range_width20),
            "width_change10": str(self.width_change10),
            "width_change20": str(self.width_change20),
            "center_shift10_abs": str(self.center_shift10_abs),
            "center_shift20_abs": str(self.center_shift20_abs),
        }


@dataclass(frozen=True)
class SampledContext:
    context_id: str
    bucket: str
    observation: OrderObservation
    evidence_digest: str


@dataclass(frozen=True)
class ResolvedReference:
    context_id: str
    label: str
    resolution: str
    evidence_digest: str


@dataclass(frozen=True)
class PropositionScore:
    stage: int
    trial_id: str
    definite_positive_count: int
    definite_negative_count: int
    ambiguous_count: int
    true_positive: int
    false_positive: int
    false_negative: int
    precision: float
    precision_lower: float | None
    recall: float
    f1: float
    negative_false_positive_rate: float
    ambiguous_assertion_rate: float | None


def _measurement_value(
    values: Mapping[tuple[str, int, int | None], Any],
    concept: str,
    window: int,
    comparison: int | None = None,
) -> tuple[Decimal, str]:
    item = values.get((concept, window, comparison))
    if item is None or item.value is None:
        raise StageOrderPropositionError("required formal fact is unavailable")
    return item.value, item.identity.revision_id


def _trend_map(
    measurements: Sequence[TrendMeasurement], market_date: date
) -> dict[tuple[str, int, int | None], TrendMeasurement]:
    return {
        (item.concept_id, int(item.window_sessions or 0), item.comparison_window_sessions): item
        for item in measurements
        if item.market_date == market_date
    }


def _order_map(
    measurements: Sequence[StructureOrderMeasurement], market_date: date
) -> dict[tuple[str, int, int | None], StructureOrderMeasurement]:
    return {
        (item.concept_id, item.window_sessions, item.comparison_window_sessions): item
        for item in measurements
        if item.market_date == market_date
    }


def build_order_observations(
    admissions: Sequence[Admission],
    *,
    report_start: date,
    report_end: date,
) -> tuple[tuple[OrderObservation, ...], dict[str, int]]:
    by_security: dict[str, list[Admission]] = defaultdict(list)
    for item in admissions:
        by_security[item.row.security_code].append(item)
    observations: list[OrderObservation] = []
    counts = {"securities": 0, "fact_intersection": 0, "fact_unavailable": 0}
    for security_code in sorted(by_security):
        series = tuple(sorted(by_security[security_code], key=lambda item: item.row.market_date))
        trend = build_trend_evolution_facts(
            series, report_start=report_start, report_end=report_end
        )
        order = build_structure_order_facts(
            series, report_start=report_start, report_end=report_end
        )
        counts["securities"] += 1
        trend_available = {item.market_date for item in trend.availability if item.available}
        order_available = {item.market_date for item in order.availability if item.available}
        dates = sorted(trend_available & order_available)
        for market_date in dates:
            try:
                tmap = _trend_map(trend.measurements, market_date)
                omap = _order_map(order.measurements, market_date)
                efficiency20, ref_eff = _measurement_value(
                    tmap, DIRECTIONAL_EFFICIENCY, 20
                )
                width20, ref_width = _measurement_value(omap, RANGE_WIDTH, 20)
                width_change10, ref_wc10 = _measurement_value(omap, WIDTH_CHANGE, 10, 10)
                width_change20, ref_wc20 = _measurement_value(omap, WIDTH_CHANGE, 20, 20)
                center10, ref_cs10 = _measurement_value(omap, RANGE_CENTER_SHIFT, 10, 10)
                center20, ref_cs20 = _measurement_value(omap, RANGE_CENTER_SHIFT, 20, 20)
            except StageOrderPropositionError:
                counts["fact_unavailable"] += 1
                continue
            evidence = digest(
                {
                    "trend_order_measurement_revisions": sorted(
                        (ref_eff, ref_width, ref_wc10, ref_wc20, ref_cs10, ref_cs20)
                    )
                }
            )
            observations.append(
                OrderObservation(
                    security_code,
                    market_date,
                    evidence,
                    abs(efficiency20),
                    width20,
                    width_change10,
                    width_change20,
                    abs(center10),
                    abs(center20),
                )
            )
            counts["fact_intersection"] += 1
    ordered = tuple(
        sorted(observations, key=lambda item: (item.security_code, item.market_date))
    )
    return ordered, counts


def _bucket(item: OrderObservation, bands: Mapping[str, Any]) -> str:
    low_eff = _decimal(bands["low_efficiency20_abs_max"], "low efficiency")
    if item.efficiency20_abs > low_eff:
        return "directional_control"
    order_like = (
        item.range_width20 <= _decimal(bands["order_range_width20_max"], "order width")
        and item.width_change20 <= _decimal(bands["order_width_change20_max"], "order change")
        and item.center_shift20_abs
        <= _decimal(bands["order_center_shift20_abs_max"], "order center")
    )
    if order_like:
        return "order_like"
    disorder_like = (
        item.width_change20
        >= _decimal(bands["disorder_width_change20_min"], "disorder expansion")
        or item.center_shift20_abs
        >= _decimal(bands["disorder_center_shift20_abs_min"], "disorder center")
    )
    return "disorder_like" if disorder_like else "mixed_low_efficiency"


def sample_contexts(
    observations: Sequence[OrderObservation],
    *,
    seed: str,
    bucket_order: Sequence[str],
    per_bucket: int,
    minimum_total: int,
    bands: Mapping[str, Any],
) -> tuple[tuple[SampledContext, ...], dict[str, int]]:
    buckets: dict[str, list[OrderObservation]] = defaultdict(list)
    for item in observations:
        buckets[_bucket(item, bands)].append(item)
    selected: list[SampledContext] = []
    used_securities: set[str] = set()
    realized: dict[str, int] = {}
    for bucket_name in bucket_order:
        candidates = sorted(
            buckets.get(bucket_name, []),
            key=lambda item: (
                hashlib.sha256(
                    f"{seed}|{bucket_name}|{item.context_id}".encode()
                ).hexdigest(),
                item.context_id,
            ),
        )
        count = 0
        for item in candidates:
            if item.security_code in used_securities:
                continue
            selected.append(
                SampledContext(item.context_id, bucket_name, item, item.evidence_digest)
            )
            used_securities.add(item.security_code)
            count += 1
            if count == per_bucket:
                break
        realized[bucket_name] = count
    if len(selected) < minimum_total:
        raise StageOrderPropositionError(
            "order proposition reference sample has only "
            f"{len(selected)} contexts; need {minimum_total}"
        )
    return tuple(selected), realized


def _policy(panel: Mapping[str, Any], reviewer_id: str) -> Mapping[str, Any]:
    policy = panel["policies"].get(reviewer_id)
    if not isinstance(policy, Mapping):
        raise StageOrderPropositionError("order proposition reviewer policy missing")
    return policy


def _review_one(item: OrderObservation, policy: Mapping[str, Any]) -> str:
    stage2 = (
        item.efficiency20_abs
        <= _decimal(policy["stage_2_efficiency20_abs_max"], "stage2 efficiency")
        and item.range_width20
        <= _decimal(policy["stage_2_range_width20_max"], "stage2 width")
        and item.width_change10
        <= _decimal(policy["stage_2_width_change10_max"], "stage2 width10")
        and item.width_change20
        <= _decimal(policy["stage_2_width_change20_max"], "stage2 width20")
        and item.center_shift10_abs
        <= _decimal(policy["stage_2_center_shift10_abs_max"], "stage2 center10")
        and item.center_shift20_abs
        <= _decimal(policy["stage_2_center_shift20_abs_max"], "stage2 center20")
    )
    stage0 = (
        item.efficiency20_abs
        <= _decimal(policy["stage_0_efficiency20_abs_max"], "stage0 efficiency")
        and (
            item.width_change10
            >= _decimal(policy["stage_0_width_change10_min"], "stage0 width10")
            or item.width_change20
            >= _decimal(policy["stage_0_width_change20_min"], "stage0 width20")
            or item.center_shift10_abs
            >= _decimal(policy["stage_0_center_shift10_abs_min"], "stage0 center10")
            or item.center_shift20_abs
            >= _decimal(policy["stage_0_center_shift20_abs_min"], "stage0 center20")
        )
    )
    if stage2 and stage0:
        return LABEL_AMBIGUOUS
    if stage2:
        return LABEL_STAGE_2
    if stage0:
        return LABEL_STAGE_0
    stage0_eff = _decimal(policy["stage_0_efficiency20_abs_max"], "stage0 efficiency")
    return (
        LABEL_AMBIGUOUS
        if item.efficiency20_abs <= stage0_eff + Decimal("0.10")
        else LABEL_NEITHER
    )


def resolve_references(
    contexts: Sequence[SampledContext], panel: Mapping[str, Any]
) -> tuple[tuple[ResolvedReference, ...], int]:
    assignment = panel["assignment"]
    primary_ids = (str(assignment["primary_a"]), str(assignment["primary_b"]))
    adjudicator = str(assignment["adjudicator"])
    output: list[ResolvedReference] = []
    disagreements = 0
    for context in contexts:
        primary = [
            _review_one(context.observation, _policy(panel, reviewer_id))
            for reviewer_id in primary_ids
        ]
        if primary[0] == primary[1]:
            label = primary[0]
            resolution = "primary_consensus"
        else:
            disagreements += 1
            label = _review_one(context.observation, _policy(panel, adjudicator))
            resolution = "adjudicated"
        if label not in LABELS:
            raise StageOrderPropositionError("order proposition resolved label differs")
        output.append(
            ResolvedReference(context.context_id, label, resolution, context.evidence_digest)
        )
    return tuple(output), disagreements


def _wilson_lower(successes: int, total: int) -> float | None:
    if total <= 0:
        return None
    z = 1.6448536269514722
    p = successes / total
    denominator = 1 + z * z / total
    centre = p + z * z / (2 * total)
    radius = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return max(0.0, (centre - radius) / denominator)


def score_proposition(
    stage: int,
    contexts: Sequence[SampledContext],
    references: Sequence[ResolvedReference],
    predictions: Mapping[str, bool],
    *,
    trial_id: str,
) -> PropositionScore:
    positive_label = LABEL_STAGE_0 if stage == 0 else LABEL_STAGE_2
    negative_labels = {LABEL_NEITHER, LABEL_STAGE_2 if stage == 0 else LABEL_STAGE_0}
    refs = {item.context_id: item for item in references}
    ids = {item.context_id for item in contexts}
    if set(refs) != ids or set(predictions) != ids:
        raise StageOrderPropositionError("order proposition score identities differ")
    positive = [item for item in references if item.label == positive_label]
    negative = [item for item in references if item.label in negative_labels]
    ambiguous = [item for item in references if item.label == LABEL_AMBIGUOUS]
    tp = sum(bool(predictions[item.context_id]) for item in positive)
    fp = sum(bool(predictions[item.context_id]) for item in negative)
    fn = len(positive) - tp
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / len(positive) if positive else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    ambiguous_assert = sum(bool(predictions[item.context_id]) for item in ambiguous)
    return PropositionScore(
        stage,
        trial_id,
        len(positive),
        len(negative),
        len(ambiguous),
        tp,
        fp,
        fn,
        precision,
        _wilson_lower(tp, tp + fp),
        recall,
        f1,
        fp / len(negative) if negative else 0.0,
        ambiguous_assert / len(ambiguous) if ambiguous else None,
    )


def _predict_stage2(item: OrderObservation, params: Mapping[str, Any]) -> bool:
    return (
        item.efficiency20_abs <= _decimal(params["efficiency20_abs_max"], "stage2 efficiency")
        and item.range_width20 <= _decimal(params["range_width20_max"], "stage2 width")
        and item.width_change20 <= _decimal(params["width_change20_max"], "stage2 change")
        and item.center_shift20_abs
        <= _decimal(params["center_shift20_abs_max"], "stage2 center")
    )


def _predict_stage0(item: OrderObservation, params: Mapping[str, Any]) -> bool:
    return (
        item.efficiency20_abs <= _decimal(params["efficiency20_abs_max"], "stage0 efficiency")
        and (
            item.width_change20 >= _decimal(params["width_change20_min"], "stage0 expansion")
            or item.center_shift20_abs
            >= _decimal(params["center_shift20_abs_min"], "stage0 center drift")
        )
    )


def _reference_counts(references: Sequence[ResolvedReference]) -> dict[str, int]:
    counts = {label: 0 for label in LABELS}
    for item in references:
        counts[item.label] += 1
    return counts


def _gate(
    score: PropositionScore,
    *,
    reference_counts: Mapping[str, int],
    r0_f1: float,
    gates: Mapping[str, Any],
    pair_overlap_count: int = 0,
) -> dict[str, Any]:
    reasons: list[str] = []
    if reference_counts[LABEL_STAGE_0] < int(gates["minimum_stage_0_definite_references"]):
        reasons.append("stage_0_reference_count_below_gate")
    if reference_counts[LABEL_STAGE_2] < int(gates["minimum_stage_2_definite_references"]):
        reasons.append("stage_2_reference_count_below_gate")
    if reference_counts[LABEL_AMBIGUOUS] < int(gates["minimum_ambiguous_references"]):
        reasons.append("ambiguous_reference_count_below_gate")
    if reference_counts[LABEL_NEITHER] < int(gates["minimum_neither_references"]):
        reasons.append("neither_reference_count_below_gate")
    if score.definite_positive_count < int(gates["minimum_definite_positive_references"]):
        reasons.append("definite_positive_count_below_gate")
    if score.definite_negative_count < int(gates["minimum_definite_negative_references"]):
        reasons.append("definite_negative_count_below_gate")
    if score.recall < float(gates["minimum_positive_reference_coverage"]):
        reasons.append("positive_coverage_below_gate")
    if score.negative_false_positive_rate > float(
        gates["maximum_definite_negative_false_positive_rate"]
    ):
        reasons.append("negative_false_positive_rate_above_gate")
    if (
        score.ambiguous_assertion_rate is not None
        and score.ambiguous_assertion_rate > float(gates["maximum_ambiguous_unique_assertion_rate"])
    ):
        reasons.append("ambiguous_unique_assertion_rate_above_gate")
    if score.precision_lower is None or score.precision_lower < float(
        gates["minimum_precision_one_sided_95_lower"]
    ):
        reasons.append("precision_lower_below_gate")
    if score.f1 < r0_f1 - float(gates["f1_noninferiority_tolerance_vs_r0"]):
        reasons.append("f1_inferior_to_r0_beyond_tolerance")
    if pair_overlap_count > int(gates["maximum_pair_overlap_count"]):
        reasons.append("stage_0_stage_2_pair_overlap_above_gate")
    return {"passed": not reasons, "reason_codes": reasons}


def _parameter_distance(params: Mapping[str, Any], r0: Mapping[str, Any]) -> float:
    return sum(abs(float(params[key]) - float(r0[key])) for key in params)


def _trial_id(stage: int, params: Mapping[str, Any]) -> str:
    return digest(
        {
            "family": "stage-order-proposition-development-v1",
            "stage": stage,
            "params": dict(params),
        }
    )


def _rank_trial(item: Mapping[str, Any]) -> tuple[Any, ...]:
    score = item["score"]
    ambiguous = score["ambiguous_assertion_rate"]
    return (
        -float(score["f1"]),
        -float(score["precision"]),
        1.0 if ambiguous is None else float(ambiguous),
        float(item["distance_from_r0"]),
        str(item["trial_id"]),
    )


def _search_stage2(
    observations: Sequence[OrderObservation],
    contexts: Sequence[SampledContext],
    references: Sequence[ResolvedReference],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    r0 = config["r0"]["stage_2"]
    space = config["search_space"]["stage_2"]
    gates = config["hard_gates"]
    ref_counts = _reference_counts(references)

    def score(params: Mapping[str, Any], trial: str) -> PropositionScore:
        return score_proposition(
            2,
            contexts,
            references,
            {item.context_id: _predict_stage2(item.observation, params) for item in contexts},
            trial_id=trial,
        )

    r0_score = score(r0, "R0:stage_2")
    trials: list[dict[str, Any]] = []
    keys = list(space)
    for values in itertools.product(*(space[key] for key in keys)):
        params = dict(zip(keys, values, strict=True))
        trial = _trial_id(2, params)
        observed = score(params, trial)
        gate = _gate(observed, reference_counts=ref_counts, r0_f1=r0_score.f1, gates=gates)
        trials.append(
            {
                "trial_id": trial,
                "parameters": params,
                "score": asdict(observed),
                "gate": gate,
                "population_assertion_count": sum(
                    _predict_stage2(item, params) for item in observations
                ),
                "distance_from_r0": _parameter_distance(params, r0),
            }
        )
    eligible = [item for item in trials if item["gate"]["passed"]]
    selected = min(eligible, key=_rank_trial) if eligible else None
    return {
        "r0_score": asdict(r0_score),
        "trial_count": len(trials),
        "eligible_trial_count": len(eligible),
        "selected": selected,
        "trials": trials,
    }


def _search_stage0(
    observations: Sequence[OrderObservation],
    contexts: Sequence[SampledContext],
    references: Sequence[ResolvedReference],
    config: Mapping[str, Any],
    stage2_selected: Mapping[str, Any] | None,
) -> dict[str, Any]:
    r0 = config["r0"]["stage_0"]
    space = config["search_space"]["stage_0"]
    gates = config["hard_gates"]
    ref_counts = _reference_counts(references)
    stage2_params = None if stage2_selected is None else stage2_selected["parameters"]

    def score(params: Mapping[str, Any], trial: str) -> PropositionScore:
        return score_proposition(
            0,
            contexts,
            references,
            {item.context_id: _predict_stage0(item.observation, params) for item in contexts},
            trial_id=trial,
        )

    r0_score = score(r0, "R0:stage_0")
    trials: list[dict[str, Any]] = []
    keys = list(space)
    for values in itertools.product(*(space[key] for key in keys)):
        params = dict(zip(keys, values, strict=True))
        trial = _trial_id(0, params)
        observed = score(params, trial)
        overlap = (
            0
            if stage2_params is None
            else sum(
                _predict_stage0(item, params) and _predict_stage2(item, stage2_params)
                for item in observations
            )
        )
        gate = _gate(
            observed,
            reference_counts=ref_counts,
            r0_f1=r0_score.f1,
            gates=gates,
            pair_overlap_count=overlap,
        )
        trials.append(
            {
                "trial_id": trial,
                "parameters": params,
                "score": asdict(observed),
                "gate": gate,
                "pair_overlap_count": overlap,
                "population_assertion_count": sum(
                    _predict_stage0(item, params) for item in observations
                ),
                "distance_from_r0": _parameter_distance(params, r0),
            }
        )
    eligible = [item for item in trials if item["gate"]["passed"]]
    selected = min(eligible, key=_rank_trial) if eligible else None
    return {
        "r0_score": asdict(r0_score),
        "trial_count": len(trials),
        "eligible_trial_count": len(eligible),
        "selected": selected,
        "trials": trials,
    }


def _selected_parameters(result: Mapping[str, Any]) -> dict[str, Any] | None:
    selected = result.get("selected")
    return None if not isinstance(selected, Mapping) else dict(selected["parameters"])


def run_order_proposition_development(
    *,
    config_path: Path,
    training_root: Path,
    output_root: Path,
    code_revision: str,
) -> dict[str, Any]:
    if not _REVISION.fullmatch(code_revision):
        raise StageOrderPropositionError("order proposition run requires exact Git revision")
    started = time.perf_counter()
    root = find_project_root()
    config = load_order_proposition_config(config_path)
    panel = load_order_proposition_panel(root / str(config["synthetic_panel_config"]))
    trend_contract = _object(root / str(config["trend_fact_contract"]), "Trend Evolution contract")
    order_contract = _object(root / str(config["order_fact_contract"]), "Structure Order contract")
    if digest(trend_contract) != config["trend_fact_contract_digest"]:
        raise StageOrderPropositionError("Trend Evolution contract digest differs")
    if digest(order_contract) != config["order_fact_contract_digest"]:
        raise StageOrderPropositionError("Structure Order contract digest differs")
    node_config = _object(root / str(config["training_node_config"]), "training node config")
    window = config["development_window"]
    ranked = discover_development_security_population(
        training_root=training_root,
        node_config=node_config,
        scan_start=_date(window, "cohort_scan_start"),
        cohort_date=_date(window, "cohort_date"),
        minimum_observations=int(window["minimum_pre_cohort_observations"]),
        seed=str(window["cohort_seed"]),
    )
    cohort_size = int(config["population"]["cohort_size"])
    if len(ranked) < cohort_size:
        raise StageOrderPropositionError("development population smaller than frozen cohort")
    cohort = tuple(sorted(ranked[:cohort_size]))
    _progress(started, "cohort-ready", eligible_population=len(ranked), cohort_size=cohort_size)
    spec_digest = digest(
        {
            "schema_version": "quanthus.stage-order-proposition-source-spec.v1",
            "development_id": config["development_id"],
            "trend_fact_contract_digest": config["trend_fact_contract_digest"],
            "order_fact_contract_digest": config["order_fact_contract_digest"],
            "official_stage_labels": False,
        }
    )
    report_start = _date(window, "report_start")
    report_end = _date(window, "report_end")
    admissions, source_manifest, _policy = read_development_admissions(
        training_root=training_root,
        node_config=node_config,
        scope=DevelopmentReadScope(_date(window, "warmup_start"), report_start, report_end),
        security_codes=cohort,
        spec_digest=spec_digest,
    )
    _progress(started, "source-ready", admissions=len(admissions))
    observations, build_counts = build_order_observations(
        admissions, report_start=report_start, report_end=report_end
    )
    _progress(started, "formal-facts-ready", observations=len(observations), **build_counts)
    sampling = config["reference_sampling"]
    contexts, bucket_counts = sample_contexts(
        observations,
        seed=str(window["cohort_seed"]),
        bucket_order=tuple(str(item) for item in sampling["bucket_order"]),
        per_bucket=int(sampling["target_contexts_per_bucket"]),
        minimum_total=int(sampling["minimum_realized_contexts"]),
        bands=sampling["sampling_bands"],
    )
    references, disagreement_count = resolve_references(contexts, panel)
    reference_counts = _reference_counts(references)
    _progress(
        started,
        "references-ready",
        contexts=len(contexts),
        bucket_counts=bucket_counts,
        reference_counts=reference_counts,
        disagreements=disagreement_count,
    )
    stage2 = _search_stage2(observations, contexts, references, config)
    _progress(
        started,
        "stage-2-search-complete",
        eligible_trials=stage2["eligible_trial_count"],
        selected=_selected_parameters(stage2),
    )
    stage0 = _search_stage0(observations, contexts, references, config, stage2.get("selected"))
    _progress(
        started,
        "stage-0-search-complete",
        eligible_trials=stage0["eligible_trial_count"],
        selected=_selected_parameters(stage0),
    )
    stage2_params = _selected_parameters(stage2)
    stage0_params = _selected_parameters(stage0)
    complete = stage2_params is not None and stage0_params is not None
    pair_overlap = (
        None
        if not complete
        else sum(
            _predict_stage0(item, stage0_params) and _predict_stage2(item, stage2_params)
            for item in observations
        )
    )
    if complete and pair_overlap != 0:
        raise StageOrderPropositionError("selected Stage-0/2 pair overlaps despite hard gate")
    disposition = (
        "DEVELOPMENT_PROPOSITION_CANDIDATES_FROZEN" if complete else "INCONCLUSIVE"
    )
    summary = {
        "schema_version": "quanthus.stage-order-proposition-development-summary.v1",
        "work_item": "P3-STAGE-004",
        "development_id": config["development_id"],
        "code_revision": code_revision,
        "development_only": True,
        "cohort_size": cohort_size,
        "cohort_digest": digest(cohort),
        "source_manifest_digest": digest(source_manifest),
        "trend_fact_contract_digest": config["trend_fact_contract_digest"],
        "order_fact_contract_digest": config["order_fact_contract_digest"],
        "observation_count": len(observations),
        "build_counts": build_counts,
        "reference_evidence_tier": "SYNTHETIC_EXPERT_CONSENSUS",
        "human_expert_validated": False,
        "reference_summary": {
            "context_count": len(contexts),
            "bucket_counts": bucket_counts,
            "label_counts": reference_counts,
            "primary_disagreement_count": disagreement_count,
        },
        "search": {"stage_2": stage2, "stage_0": stage0},
        "selected_parameters": {"stage_2": stage2_params, "stage_0": stage0_params},
        "selected_pair_overlap_count": pair_overlap,
        "disposition": disposition,
        "future_outcome_read": False,
        "arbitration_read": False,
        "permanent_blind_read": False,
        "holdout_population_selected": False,
        "official_stage_labels_emitted": False,
        "primary_stage_emitted": False,
        "strategy_result_read": False,
        "elapsed_seconds": time.perf_counter() - started,
        "claim_limits": config["claim_limits"],
    }
    candidate = {
        "schema_version": "quanthus.stage-order-proposition-development-candidate.v1",
        "work_item": "P3-STAGE-004",
        "disposition": disposition,
        "stage_0": {
            "code": 0,
            "working_name": "structural_disorder",
            "parameters": stage0_params,
            "development_only": True,
        },
        "stage_2": {
            "code": 2,
            "working_name": "orderly_consolidation",
            "parameters": stage2_params,
            "development_only": True,
        },
        "both_false_allowed": True,
        "both_true_allowed": False,
        "selected_pair_overlap_count": pair_overlap,
        "primary_stage_authorized": False,
        "official_label_card_authorized": False,
        "human_expert_validated": False,
    }
    run_id = "stage-order-proposition-development-" + datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    run_root = output_root.expanduser().resolve() / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    payloads = {"summary.json": canonical(summary), "candidate.json": canonical(candidate)}
    for name, payload in payloads.items():
        (run_root / name).write_bytes(payload)
    manifest = {
        "schema_version": "quanthus.stage-order-proposition-development-artifact.v1",
        "run_id": run_id,
        "code_revision": code_revision,
        "development_config_digest": digest(config),
        "panel_config_digest": digest(panel),
        "files": {name: _sha256(payload) for name, payload in payloads.items()},
        "development_only": True,
        "official_stage_labels_emitted": False,
        "primary_stage_emitted": False,
        "arbitration_read": False,
        "permanent_blind_read": False,
    }
    manifest_bytes = canonical(manifest)
    (run_root / "manifest.json").write_bytes(manifest_bytes)
    ready = _sha256(manifest_bytes)
    (run_root / "READY").write_text(ready + "\n", encoding="ascii")
    _progress(started, "published", disposition=disposition, run_id=run_id)
    return {**summary, "run_root": str(run_root), "manifest_sha256": ready}


def verify_order_proposition_artifact(
    path: Path, *, code_revision: str | None = None
) -> dict[str, Any]:
    root = path.expanduser().resolve()
    manifest = _object(root / "manifest.json", "order proposition manifest")
    if manifest.get("schema_version") != "quanthus.stage-order-proposition-development-artifact.v1":
        raise StageOrderPropositionError("order proposition artifact schema differs")
    manifest_bytes = canonical(manifest)
    try:
        ready = (root / "READY").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise StageOrderPropositionError("order proposition READY unreadable") from exc
    if ready != _sha256(manifest_bytes):
        raise StageOrderPropositionError("order proposition READY does not bind manifest")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise StageOrderPropositionError("order proposition code revision differs")
    files = manifest.get("files")
    if not isinstance(files, Mapping) or set(files) != {"summary.json", "candidate.json"}:
        raise StageOrderPropositionError("order proposition artifact inventory differs")
    for name, expected in files.items():
        if _sha256((root / name).read_bytes()) != expected:
            raise StageOrderPropositionError(f"order proposition digest differs: {name}")
    summary = _object(root / "summary.json", "order proposition summary")
    candidate = _object(root / "candidate.json", "order proposition candidate")
    if (
        summary.get("development_only") is not True
        or summary.get("future_outcome_read") is not False
        or summary.get("arbitration_read") is not False
        or summary.get("permanent_blind_read") is not False
        or summary.get("official_stage_labels_emitted") is not False
        or summary.get("primary_stage_emitted") is not False
        or candidate.get("both_false_allowed") is not True
        or candidate.get("both_true_allowed") is not False
        or candidate.get("primary_stage_authorized") is not False
        or candidate.get("official_label_card_authorized") is not False
    ):
        raise StageOrderPropositionError("order proposition artifact authority differs")
    return {
        "run_id": manifest["run_id"],
        "manifest_sha256": ready,
        "candidate_artifact_digest": files["candidate.json"],
        "file_count": 2,
        "verified": True,
        "disposition": summary["disposition"],
        "selected_parameters": summary["selected_parameters"],
        "selected_pair_overlap_count": summary["selected_pair_overlap_count"],
        "reference_summary": summary["reference_summary"],
        "observation_count": summary["observation_count"],
        "development_only": True,
        "arbitration_read": False,
        "permanent_blind_read": False,
        "official_stage_labels_emitted": False,
        "primary_stage_emitted": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--config", type=Path, default=find_project_root() / DEFAULT_CONFIG)
    run.add_argument("--training-root", type=Path, required=True)
    run.add_argument("--output-root", type=Path, required=True)
    run.add_argument("--code-revision", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--artifact", type=Path, required=True)
    verify.add_argument("--code-revision")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "run":
        result = run_order_proposition_development(
            config_path=args.config,
            training_root=args.training_root,
            output_root=args.output_root,
            code_revision=args.code_revision,
        )
    else:
        result = verify_order_proposition_artifact(
            args.artifact, code_revision=args.code_revision
        )
    print(canonical(result).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

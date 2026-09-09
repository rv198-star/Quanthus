"""Bounded development selection for distinct Stage 5/6/7/10/11 semantics.

One 512-security 2020-2021 development build is shared by all second-group searches. Synthetic
references see only T-only facts. Stage 10 is frozen before Stage 7/11. No future outcome,
arbitration, permanent-blind, official-label, strategy, or trading authority is admitted.
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
from decimal import Context, Decimal, localcontext
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.stage_learning.core import (
    PredicateState,
    StageCandidateObservation,
    build_activity_observations,
    build_stage34_decisions,
)
from quanthus.stage_learning.source import (
    DevelopmentReadScope,
    discover_development_security_population,
    read_development_admissions,
)
from quanthus.structure_v2 import StructureV2Result, build_structure_v2
from quanthus.structure_v2_inputs import Admission, canonical, digest

DEFAULT_CONFIG = "config/stage-learning/second-group-development.v1.json"
_REVISION = re.compile(r"^[0-9a-f]{40}$")
STAGES = (5, 6, 7, 10, 11)


class StageSecondGroupDevelopmentError(ValueError):
    """Raised when the bounded second-group development contract is violated."""


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StageSecondGroupDevelopmentError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise StageSecondGroupDevelopmentError(f"{label} must be a JSON object")
    return value


def _date(value: Mapping[str, Any], key: str) -> date:
    try:
        return date.fromisoformat(str(value[key]))
    except (KeyError, ValueError) as exc:
        raise StageSecondGroupDevelopmentError(f"invalid development date: {key}") from exc


def _decimal(value: object, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except Exception as exc:
        raise StageSecondGroupDevelopmentError(f"invalid decimal: {label}") from exc
    if not parsed.is_finite():
        raise StageSecondGroupDevelopmentError(f"non-finite decimal: {label}")
    return parsed


def _ratio(current: Decimal, prior: Decimal) -> Decimal:
    if prior <= 0:
        raise StageSecondGroupDevelopmentError("ratio denominator must be positive")
    with localcontext(Context(prec=28)):
        return current / prior - Decimal(1)


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _progress(started: float, stage: str, **details: Any) -> None:
    print(
        json.dumps(
            {
                "event": "stage-second-group-development-progress",
                "stage": stage,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                **details,
            },
            ensure_ascii=True,
            sort_keys=True,
        ),
        flush=True,
    )


def load_second_group_development_config(path: Path) -> dict[str, Any]:
    raw = _object(path, "second-group development config")
    expected = {
        "schema_version",
        "development_id",
        "work_item",
        "purpose",
        "training_node_config",
        "first_group_candidate_config",
        "stage_5_6_experiment_config",
        "stage_7_10_11_experiment_config",
        "synthetic_panel_config",
        "authority",
        "development_window",
        "population",
        "state_identity",
        "execution_order",
        "dependency_rules",
        "reference_sampling",
        "hard_gates",
        "selection",
        "artifact",
        "claim_limits",
    }
    if (
        set(raw) != expected
        or raw.get("schema_version") != "quanthus.stage-second-group-development.v1"
        or raw.get("work_item") != "P3-STAGE-001-H"
    ):
        raise StageSecondGroupDevelopmentError("second-group development config identity differs")
    authority = raw.get("authority")
    if not isinstance(authority, Mapping):
        raise StageSecondGroupDevelopmentError("second-group development authority is missing")
    required_true = {
        "development_only",
        "runtime_implementation_authorized",
        "development_data_read_authorized",
        "synthetic_reference_runtime_authorized",
        "limited_parameter_search_runtime_authorized",
        "development_candidate_artifact_authorized",
    }
    required_false = set(authority) - required_true
    if any(authority.get(key) is not True for key in required_true) or any(
        authority.get(key) is not False for key in required_false
    ):
        raise StageSecondGroupDevelopmentError("second-group development authority differs")
    identity = raw.get("state_identity")
    if not isinstance(identity, Mapping) or (
        identity.get("preserve_distinct_stage_codes") != [5, 6, 7, 10, 11]
        or identity.get("stage_5_6_merge_authorized") is not False
        or identity.get("stage_10_11_merge_authorized") is not False
    ):
        raise StageSecondGroupDevelopmentError("second-group stage identity differs")
    if raw.get("execution_order") != ["stage_5_6", "stage_10", "stage_7", "stage_11"]:
        raise StageSecondGroupDevelopmentError("second-group execution order differs")
    dependency = raw.get("dependency_rules")
    if not isinstance(dependency, Mapping) or (
        dependency.get("stage_10_candidate_must_freeze_before_stage_7_and_stage_11") is not True
        or dependency.get("full_cartesian_cross_stage_search_forbidden") is not True
    ):
        raise StageSecondGroupDevelopmentError("second-group sequential dependency differs")
    window = raw.get("development_window")
    population = raw.get("population")
    sampling = raw.get("reference_sampling")
    gates = raw.get("hard_gates")
    if not all(isinstance(item, Mapping) for item in (window, population, sampling, gates)):
        raise StageSecondGroupDevelopmentError("second-group development sections are missing")
    scan_start = _date(window, "cohort_scan_start")
    cohort_date = _date(window, "cohort_date")
    warmup_start = _date(window, "warmup_start")
    report_start = _date(window, "report_start")
    report_end = _date(window, "report_end")
    if not (
        date(2012, 1, 1)
        <= scan_start
        <= warmup_start
        <= cohort_date
        < report_start
        <= report_end
        <= date(2021, 12, 31)
    ):
        raise StageSecondGroupDevelopmentError("second-group development must stay in development")
    if (
        population.get("cohort_size") != 512
        or population.get("anchor_scales") != [20, 60]
        or population.get("maximum_path_sessions_including_first_stage_4") != 60
        or population.get("shared_structure_build_required") is not True
        or population.get("shared_activity_build_required") is not True
    ):
        raise StageSecondGroupDevelopmentError("second-group population contract differs")
    if (
        sampling.get("target_contexts_per_stage") != 120
        or sampling.get("target_contexts_per_bucket") != 30
        or sampling.get("exact_bucket_quota_required") is not False
        or sampling.get("minimum_realized_contexts_per_stage") != 80
        or sampling.get("one_context_per_episode_per_stage") is not True
        or sampling.get("reviewers_receive_t_only_features_only") is not True
        or sampling.get("algorithm_predictions_hidden_from_reviewers") is not True
        or sampling.get("future_outcomes_hidden_from_reviewers") is not True
    ):
        raise StageSecondGroupDevelopmentError("second-group reference sampling contract differs")
    return raw


def load_second_group_panel(path: Path) -> dict[str, Any]:
    raw = _object(path, "second-group synthetic panel")
    if (
        raw.get("schema_version") != "quanthus.stage-second-group-synthetic-panel.v1"
        or raw.get("work_item") != "P3-STAGE-001-H"
        or raw.get("evidence_tier") != "SYNTHETIC_EXPERT_CONSENSUS"
        or raw.get("human_expert_validated") is not False
    ):
        raise StageSecondGroupDevelopmentError("second-group synthetic panel identity differs")
    authority = raw.get("authority")
    if not isinstance(authority, Mapping) or (
        authority.get("development_only") is not True
        or authority.get("semantic_reference_authorized") is not True
        or authority.get("future_outcome_access_authorized") is not False
        or authority.get("arbitration_access_authorized") is not False
        or authority.get("permanent_blind_access_authorized") is not False
        or authority.get("formal_qualification_authorized") is not False
    ):
        raise StageSecondGroupDevelopmentError("second-group synthetic panel authority differs")
    assignment = raw.get("assignment")
    policies = raw.get("policies")
    if not isinstance(assignment, Mapping) or not isinstance(policies, Mapping):
        raise StageSecondGroupDevelopmentError("second-group panel assignment/policies are missing")
    ids = [str(assignment.get(key) or "") for key in ("primary_a", "primary_b", "adjudicator")]
    if len(set(ids)) != 3 or not set(ids) <= set(policies):
        raise StageSecondGroupDevelopmentError("second-group panel reviewer identities differ")
    if assignment.get("primary_reviews_are_mutually_hidden") is not True:
        raise StageSecondGroupDevelopmentError("second-group primaries must be mutually hidden")
    semantics = raw.get("reference_semantics")
    if not isinstance(semantics, Mapping) or (
        semantics.get("stage_5_6_merge_authorized") is not False
        or semantics.get("stage_10_11_merge_authorized") is not False
        or semantics.get("reviewers_never_receive_candidate_parameters") is not True
        or semantics.get("reviewers_never_receive_future_outcomes") is not True
    ):
        raise StageSecondGroupDevelopmentError("second-group panel semantic boundary differs")
    return raw


@dataclass(frozen=True)
class SecondGroupObservation:
    security_code: str
    market_date: date
    scale: int
    episode_id: str
    maximum_extension: Decimal
    current_drawdown_abs: Decimal
    damage_depth_abs: Decimal
    repair_fraction: Decimal | None
    support_break_seen: bool
    support_reclaimed: bool
    support_current_unreclaimed: bool
    support_unreclaimed_age: int
    support_depth_abs: Decimal
    rebound_from_post_break_low: Decimal

    def context_id(self, stage: int) -> str:
        return digest(
            {
                "stage": stage,
                "security_code": self.security_code,
                "market_date": self.market_date,
                "scale": self.scale,
                "episode_id": self.episode_id,
            }
        )

    def feature_dict(self) -> dict[str, Any]:
        return {
            "market_date": self.market_date,
            "scale": self.scale,
            "maximum_extension": format(self.maximum_extension, "f"),
            "current_drawdown_abs": format(self.current_drawdown_abs, "f"),
            "damage_depth_abs": format(self.damage_depth_abs, "f"),
            "repair_fraction": (
                None if self.repair_fraction is None else format(self.repair_fraction, "f")
            ),
            "support_break_seen": self.support_break_seen,
            "support_reclaimed": self.support_reclaimed,
            "support_current_unreclaimed": self.support_current_unreclaimed,
            "support_unreclaimed_age": self.support_unreclaimed_age,
            "support_depth_abs": format(self.support_depth_abs, "f"),
            "rebound_from_post_break_low": format(self.rebound_from_post_break_low, "f"),
        }


@dataclass(frozen=True)
class SampledContext:
    stage: int
    context_id: str
    bucket: str
    observation: SecondGroupObservation
    evidence_digest: str


@dataclass(frozen=True)
class ResolvedReference:
    stage: int
    context_id: str
    kind: str
    supported: bool
    resolution: str
    evidence_digest: str


@dataclass(frozen=True)
class StageScore:
    stage: int
    trial_id: str
    definite_positive_count: int
    definite_negative_count: int
    ambiguous_count: int
    unjudgeable_count: int
    tp: int
    fp: int
    fn: int
    precision: float
    precision_lower: float | None
    recall: float
    f1: float
    positive_reference_coverage: float
    negative_false_positive_rate: float
    ambiguous_unique_assertion_rate: float | None
    unjudgeable_reference_rate: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _first_confirmed_stage4_candidates(
    candidates: Sequence[StageCandidateObservation],
) -> tuple[StageCandidateObservation, ...]:
    selected: dict[tuple[str, int, str], StageCandidateObservation] = {}
    for candidate in sorted(
        candidates,
        key=lambda item: (item.security_code, item.market_date, item.anchor_scale, item.episode_id),
    ):
        if candidate.stage_code != 4 or candidate.candidate_state is not PredicateState.TRUE:
            continue
        key = (candidate.security_code, candidate.anchor_scale, candidate.episode_id)
        selected.setdefault(key, candidate)
    return tuple(selected[key] for key in sorted(selected))


def build_second_group_observations(
    candidates: Sequence[StageCandidateObservation],
    structure: StructureV2Result,
    admissions: Sequence[Admission],
    *,
    maximum_sessions: int,
) -> tuple[SecondGroupObservation, ...]:
    """Project causal T-only features once for all five second-group stages."""
    if maximum_sessions < 2:
        raise StageSecondGroupDevelopmentError("second-group path horizon must be at least two")
    starts = _first_confirmed_stage4_candidates(candidates)
    episode_by_key = {
        (item.security_code, item.scale, item.episode_id, item.market_date): item
        for item in structure.episodes
    }
    rows_by_security: dict[str, list[Admission]] = defaultdict(list)
    for item in sorted(admissions, key=lambda row: (row.row.security_code, row.row.market_date)):
        rows_by_security[item.row.security_code].append(item)
    output: list[SecondGroupObservation] = []
    for candidate in starts:
        episode = episode_by_key.get(
            (
                candidate.security_code,
                candidate.anchor_scale,
                candidate.episode_id,
                candidate.market_date,
            )
        )
        if episode is None or episode.close is None or episode.frozen_low <= 0:
            raise StageSecondGroupDevelopmentError(
                "Stage-4 candidate lacks causal support evidence"
            )
        series = rows_by_security[candidate.security_code]
        start_index = next(
            (
                index
                for index, item in enumerate(series)
                if item.row.market_date == candidate.market_date
            ),
            None,
        )
        if start_index is None:
            raise StageSecondGroupDevelopmentError("Stage-4 acceptance row is missing")
        acceptance = series[start_index]
        if not acceptance.admitted or acceptance.row.close is None:
            raise StageSecondGroupDevelopmentError("Stage-4 acceptance row is not admitted")
        acceptance_close = acceptance.row.close
        listing_id = acceptance.row.listing_id
        coordinate_ref = acceptance.row.coordinate_ref
        running_peak = acceptance_close
        cycle_peak = acceptance_close
        cycle_trough = acceptance_close
        support = episode.frozen_low
        break_seen = False
        reclaimed = False
        break_offset: int | None = None
        post_break_low: Decimal | None = None
        previous_close: Decimal | None = None
        for offset, item in enumerate(series[start_index : start_index + maximum_sessions]):
            row = item.row
            if (
                not item.admitted
                or row.close is None
                or row.listing_id != listing_id
                or row.coordinate_ref != coordinate_ref
            ):
                break
            close = row.close
            running_peak = max(running_peak, close)
            maximum_extension = max(Decimal(0), _ratio(running_peak, acceptance_close))
            current_drawdown_abs = max(Decimal(0), -_ratio(close, running_peak))

            if close >= cycle_peak:
                cycle_peak = close
                cycle_trough = close
                damage_depth_abs = Decimal(0)
                repair_fraction = None
            else:
                cycle_trough = min(cycle_trough, close)
                damage_depth_abs = max(Decimal(0), -_ratio(cycle_trough, cycle_peak))
                denominator = cycle_peak - cycle_trough
                repair_fraction = (
                    Decimal(0)
                    if denominator <= 0
                    else (close - cycle_trough) / denominator
                )
                repair_fraction = min(Decimal(1), max(Decimal(0), repair_fraction))

            if (
                not break_seen
                and previous_close is not None
                and previous_close >= support > close
            ):
                break_seen = True
                break_offset = offset
                post_break_low = close
            current_unreclaimed = break_seen and not reclaimed
            if (
                current_unreclaimed
                and previous_close is not None
                and previous_close <= support < close
            ):
                reclaimed = True
                current_unreclaimed = False
            if current_unreclaimed:
                post_break_low = close if post_break_low is None else min(post_break_low, close)
            unreclaimed_age = (
                offset - int(break_offset)
                if current_unreclaimed and break_offset is not None
                else 0
            )
            support_depth_abs = (
                max(Decimal(0), -_ratio(close, support))
                if close < support
                else Decimal(0)
            )
            rebound = (
                max(Decimal(0), _ratio(close, post_break_low))
                if current_unreclaimed and post_break_low is not None
                else Decimal(0)
            )
            output.append(
                SecondGroupObservation(
                    candidate.security_code,
                    row.market_date,
                    candidate.anchor_scale,
                    candidate.episode_id,
                    maximum_extension,
                    current_drawdown_abs,
                    damage_depth_abs,
                    repair_fraction,
                    break_seen,
                    reclaimed,
                    current_unreclaimed,
                    unreclaimed_age,
                    support_depth_abs,
                    rebound,
                )
            )
            previous_close = close
    if not output:
        raise StageSecondGroupDevelopmentError("second-group T-only observation panel is empty")
    return tuple(output)


def _bucket(stage: int, item: SecondGroupObservation) -> str:
    if stage == 5:
        if item.support_current_unreclaimed or item.maximum_extension < Decimal("0.05"):
            return "lt_5_or_failure"
        if item.maximum_extension < Decimal("0.10"):
            return "5_to_10"
        if item.maximum_extension < Decimal("0.15"):
            return "10_to_15"
        return "gte_15"
    if stage == 6:
        if item.support_current_unreclaimed or item.current_drawdown_abs >= Decimal("0.20"):
            return "gte_20_or_failure"
        if item.current_drawdown_abs < Decimal("0.05"):
            return "lt_5"
        if item.current_drawdown_abs < Decimal("0.10"):
            return "5_to_10"
        return "10_to_20"
    if stage == 10:
        if not item.support_current_unreclaimed:
            return "not_current_failure"
        if item.support_unreclaimed_age < 3:
            return "unreclaimed_0_2"
        if item.support_unreclaimed_age < 8:
            return "unreclaimed_3_7"
        return "unreclaimed_8_plus"
    if stage == 7:
        if item.damage_depth_abs < Decimal("0.12"):
            return "damage_lt_12"
        repair = item.repair_fraction or Decimal(0)
        if repair < Decimal("0.25"):
            return "deep_repair_lt_25"
        if repair < Decimal("0.50"):
            return "deep_repair_25_50"
        return "deep_repair_gte_50"
    if stage == 11:
        if not item.support_current_unreclaimed:
            return "no_current_failure"
        if item.rebound_from_post_break_low < Decimal("0.03"):
            return "rebound_lt_3"
        if item.rebound_from_post_break_low < Decimal("0.08"):
            return "rebound_3_8"
        return "rebound_gte_8"
    raise StageSecondGroupDevelopmentError(f"unsupported second-group stage: {stage}")


def sample_stage_contexts(
    observations: Sequence[SecondGroupObservation],
    *,
    stage: int,
    seed: str,
    per_bucket: int,
    minimum_total: int,
) -> tuple[SampledContext, ...]:
    buckets: dict[str, list[SecondGroupObservation]] = defaultdict(list)
    for item in observations:
        buckets[_bucket(stage, item)].append(item)
    selected: list[SampledContext] = []
    used_episodes: set[str] = set()
    for bucket_name in sorted(buckets):
        candidates = sorted(
            buckets[bucket_name],
            key=lambda item: (
                hashlib.sha256(
                    f"{seed}|{stage}|{bucket_name}|{item.context_id(stage)}".encode()
                ).hexdigest(),
                item.context_id(stage),
            ),
        )
        count = 0
        for item in candidates:
            if item.episode_id in used_episodes:
                continue
            context_id = item.context_id(stage)
            evidence = digest(
                {
                    "stage": stage,
                    "context_id": context_id,
                    "features": item.feature_dict(),
                }
            )
            selected.append(SampledContext(stage, context_id, bucket_name, item, evidence))
            used_episodes.add(item.episode_id)
            count += 1
            if count == per_bucket:
                break
    if len(selected) < minimum_total:
        raise StageSecondGroupDevelopmentError(
            f"Stage {stage} reference sample has only {len(selected)} contexts; "
            f"need {minimum_total}"
        )
    return tuple(sorted(selected, key=lambda item: (item.bucket, item.context_id)))


def _policy(panel: Mapping[str, Any], reviewer_id: str) -> dict[str, Any]:
    raw = panel["policies"].get(reviewer_id)
    if not isinstance(raw, Mapping):
        raise StageSecondGroupDevelopmentError("second-group reviewer policy is missing")
    return dict(raw)


def _review_one(
    stage: int,
    obs: SecondGroupObservation,
    policy: Mapping[str, Any],
) -> tuple[str, bool]:
    if stage == 5:
        threshold = _decimal(policy["stage_5_extension_positive_min"], "stage5 extension")
        drawdown_max = _decimal(policy["stage_5_drawdown_max"], "stage5 drawdown")
        if obs.support_current_unreclaimed:
            return "definite", False
        if obs.maximum_extension >= threshold and obs.current_drawdown_abs <= drawdown_max:
            return "definite", True
        if (
            obs.maximum_extension >= threshold * Decimal("0.75")
            and obs.current_drawdown_abs <= drawdown_max + Decimal("0.04")
        ):
            return "ambiguous_set", True
        return "definite", False
    if stage == 6:
        extension = _decimal(policy["stage_6_extension_min"], "stage6 extension")
        lower = _decimal(policy["stage_6_drawdown_min"], "stage6 drawdown")
        deep = _decimal(policy["stage_6_deep_damage_min"], "stage6 deep damage")
        if obs.support_current_unreclaimed:
            return "definite", False
        if obs.maximum_extension >= extension and lower <= obs.current_drawdown_abs < deep:
            return "definite", True
        if (
            obs.maximum_extension >= extension * Decimal("0.75")
            and lower - Decimal("0.02") <= obs.current_drawdown_abs < deep + Decimal("0.02")
        ):
            return "ambiguous_set", True
        return "definite", False
    if stage == 10:
        persistence = int(policy["stage_10_persistence_min"])
        depth = _decimal(policy["stage_10_depth_min"], "stage10 depth")
        if not obs.support_current_unreclaimed:
            return "definite", False
        if obs.support_unreclaimed_age >= persistence and obs.support_depth_abs >= depth:
            return "definite", True
        if (
            obs.support_unreclaimed_age >= max(1, persistence - 2)
            and obs.support_depth_abs >= depth * Decimal("0.50")
        ):
            return "ambiguous_set", True
        return "definite", False
    if stage == 7:
        persistence = int(policy["stage_10_persistence_min"])
        failure_depth = _decimal(policy["stage_10_depth_min"], "stage10 depth")
        if (
            obs.support_current_unreclaimed
            and obs.support_unreclaimed_age >= persistence
            and obs.support_depth_abs >= failure_depth
        ):
            return "definite", False
        damage = _decimal(policy["stage_7_damage_min"], "stage7 damage")
        repair = _decimal(policy["stage_7_repair_min"], "stage7 repair")
        observed_repair = obs.repair_fraction or Decimal(0)
        if obs.damage_depth_abs >= damage and observed_repair >= repair:
            return "definite", True
        if (
            obs.damage_depth_abs >= damage * Decimal("0.80")
            and observed_repair >= repair * Decimal("0.70")
        ):
            return "ambiguous_set", True
        return "definite", False
    if stage == 11:
        persistence = int(policy["stage_10_persistence_min"])
        failure_depth = _decimal(policy["stage_10_depth_min"], "stage10 depth")
        rebound = _decimal(policy["stage_11_rebound_min"], "stage11 rebound")
        failure_grade = (
            obs.support_current_unreclaimed
            and obs.support_unreclaimed_age >= persistence
            and obs.support_depth_abs >= failure_depth
        )
        if not failure_grade:
            return "definite", False
        if obs.rebound_from_post_break_low >= rebound:
            return "definite", True
        if obs.rebound_from_post_break_low >= rebound * Decimal("0.60"):
            return "ambiguous_set", True
        return "definite", False
    raise StageSecondGroupDevelopmentError(f"unsupported stage review: {stage}")


def resolve_stage_references(
    contexts: Sequence[SampledContext], panel: Mapping[str, Any]
) -> tuple[tuple[ResolvedReference, ...], int]:
    assignment = panel["assignment"]
    primary_ids = (str(assignment["primary_a"]), str(assignment["primary_b"]))
    adjudicator_id = str(assignment["adjudicator"])
    output: list[ResolvedReference] = []
    disagreements = 0
    for context in contexts:
        reviews = [
            _review_one(context.stage, context.observation, _policy(panel, reviewer_id))
            for reviewer_id in primary_ids
        ]
        if reviews[0] == reviews[1]:
            kind, supported = reviews[0]
            resolution = "primary_consensus"
        else:
            disagreements += 1
            kind, supported = _review_one(
                context.stage, context.observation, _policy(panel, adjudicator_id)
            )
            resolution = "adjudicated"
        output.append(
            ResolvedReference(
                context.stage,
                context.context_id,
                kind,
                supported,
                resolution,
                context.evidence_digest,
            )
        )
    return tuple(output), disagreements


def _wilson_lower(successes: int, total: int) -> float | None:
    if total <= 0:
        return None
    z = 1.6448536269514722
    p = successes / total
    denom = 1 + z * z / total
    centre = p + z * z / (2 * total)
    radius = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return max(0.0, (centre - radius) / denom)


def score_stage(
    stage: int,
    contexts: Sequence[SampledContext],
    references: Sequence[ResolvedReference],
    predictions: Mapping[str, bool],
    *,
    trial_id: str,
) -> StageScore:
    by_id = {item.context_id: item for item in references}
    context_ids = {item.context_id for item in contexts}
    if set(by_id) != context_ids or set(predictions) != context_ids:
        raise StageSecondGroupDevelopmentError("second-group score identities differ")
    definite_positive = [item for item in references if item.kind == "definite" and item.supported]
    definite_negative = [
        item for item in references if item.kind == "definite" and not item.supported
    ]
    ambiguous = [item for item in references if item.kind == "ambiguous_set"]
    unjudgeable = [item for item in references if item.kind == "unjudgeable"]
    tp = sum(predictions[item.context_id] for item in definite_positive)
    fn = len(definite_positive) - tp
    fp = sum(predictions[item.context_id] for item in definite_negative)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / len(definite_positive) if definite_positive else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    ambiguous_assert = sum(predictions[item.context_id] for item in ambiguous)
    return StageScore(
        stage,
        trial_id,
        len(definite_positive),
        len(definite_negative),
        len(ambiguous),
        len(unjudgeable),
        tp,
        fp,
        fn,
        precision,
        _wilson_lower(tp, tp + fp),
        recall,
        f1,
        recall,
        fp / len(definite_negative) if definite_negative else 0.0,
        ambiguous_assert / len(ambiguous) if ambiguous else None,
        len(unjudgeable) / len(references) if references else 1.0,
    )


def _gate(score: StageScore, *, r0_f1: float, gates: Mapping[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    if score.definite_positive_count < int(gates["minimum_definite_positive_references"]):
        reasons.append("definite_positive_count_below_gate")
    if score.definite_negative_count < int(gates["minimum_definite_negative_references"]):
        reasons.append("definite_negative_count_below_gate")
    if score.ambiguous_count < int(gates["minimum_ambiguous_references"]):
        reasons.append("ambiguous_count_below_gate")
    if score.unjudgeable_reference_rate > float(gates["maximum_unjudgeable_reference_rate"]):
        reasons.append("unjudgeable_rate_above_gate")
    if score.positive_reference_coverage < float(gates["minimum_positive_reference_coverage"]):
        reasons.append("positive_coverage_below_gate")
    if score.negative_false_positive_rate > float(
        gates["maximum_definite_negative_false_positive_rate"]
    ):
        reasons.append("negative_false_positive_rate_above_gate")
    if (
        score.ambiguous_unique_assertion_rate is not None
        and score.ambiguous_unique_assertion_rate
        > float(gates["maximum_ambiguous_unique_assertion_rate"])
    ):
        reasons.append("ambiguous_unique_assertion_rate_above_gate")
    if score.precision_lower is None or score.precision_lower < float(
        gates["minimum_precision_one_sided_95_lower"]
    ):
        reasons.append("precision_lower_below_gate")
    if score.f1 < r0_f1 - float(gates["f1_noninferiority_tolerance_vs_r0"]):
        reasons.append("f1_inferior_to_r0_beyond_tolerance")
    return {"passed": not reasons, "reason_codes": reasons}


def _trial_id(stage: str, params: Mapping[str, Any]) -> str:
    return digest({"family": "second-group-development-v1", "stage": stage, "params": dict(params)})


def _predict_5_6(obs: SecondGroupObservation, params: Mapping[str, Any]) -> tuple[bool, bool]:
    extension = _decimal(params["advance_extension_min"], "advance extension")
    pullback = _decimal(params["pullback_drawdown_abs_min"], "pullback")
    deep = _decimal(params["deep_damage_drawdown_abs_min"], "deep damage")
    stage5 = obs.maximum_extension >= extension and not obs.support_current_unreclaimed
    stage6 = stage5 and pullback <= obs.current_drawdown_abs < deep
    return stage5, stage6


def _predict_10(obs: SecondGroupObservation, params: Mapping[str, Any]) -> bool:
    return (
        obs.support_current_unreclaimed
        and obs.support_unreclaimed_age >= int(params["unreclaimed_persistence_sessions"])
        and obs.support_depth_abs >= _decimal(params["failure_depth_abs_min"], "failure depth")
    )


def _predict_7(
    obs: SecondGroupObservation,
    params: Mapping[str, Any],
    stage10_params: Mapping[str, Any],
) -> bool:
    return (
        not _predict_10(obs, stage10_params)
        and obs.damage_depth_abs >= _decimal(params["damage_depth_abs_min"], "damage depth")
        and (obs.repair_fraction or Decimal(0))
        >= _decimal(params["repair_fraction_min"], "repair fraction")
    )


def _predict_11(
    obs: SecondGroupObservation,
    params: Mapping[str, Any],
    stage10_params: Mapping[str, Any],
) -> bool:
    return _predict_10(obs, stage10_params) and obs.rebound_from_post_break_low >= _decimal(
        params["rebound_from_post_break_low_min"], "rebound"
    )


def _parameter_distance(params: Mapping[str, Any], r0: Mapping[str, Any]) -> float:
    distance = 0.0
    for key, value in params.items():
        if key not in r0:
            continue
        try:
            distance += abs(float(value) - float(r0[key]))
        except (TypeError, ValueError):
            distance += 0.0 if value == r0[key] else 1.0
    return distance


def _rank_stage_trial(item: Mapping[str, Any]) -> tuple[Any, ...]:
    score = item["score"]
    return (
        -float(score["f1"]),
        -float(score["precision_lower"] or 0.0),
        -float(score["positive_reference_coverage"]),
        float(item["parameter_distance_from_r0"]),
        json.dumps(item["params"], sort_keys=True),
    )


def _search_5_6(
    contexts: Mapping[int, Sequence[SampledContext]],
    references: Mapping[int, Sequence[ResolvedReference]],
    experiment: Mapping[str, Any],
    gates: Mapping[str, Any],
) -> dict[str, Any]:
    raw = experiment["r1_search_space"]
    r0 = experiment["r0_baseline"]
    params_list = [
        {
            "advance_extension_min": format(extension, "f"),
            "pullback_drawdown_abs_min": format(pullback, "f"),
            "deep_damage_drawdown_abs_min": format(deep, "f"),
        }
        for extension, pullback, deep in itertools.product(
            tuple(_decimal(v, "extension") for v in raw["advance_extension_min"]),
            tuple(_decimal(v, "pullback") for v in raw["pullback_drawdown_abs_min"]),
            tuple(_decimal(v, "deep") for v in raw["deep_damage_drawdown_abs_min"]),
        )
        if deep > pullback
    ]
    if len(params_list) != int(raw["candidate_count"]):
        raise StageSecondGroupDevelopmentError("Stage 5/6 trial count differs")
    r0_params = {
        "advance_extension_min": str(r0["advance_extension_min"]),
        "pullback_drawdown_abs_min": str(r0["pullback_drawdown_abs_min"]),
        "deep_damage_drawdown_abs_min": str(r0["deep_damage_drawdown_abs_min"]),
    }

    def score_params(params: Mapping[str, Any], trial_id: str) -> tuple[StageScore, StageScore]:
        p5: dict[str, bool] = {}
        p6: dict[str, bool] = {}
        for stage in (5, 6):
            for context in contexts[stage]:
                stage5, stage6 = _predict_5_6(context.observation, params)
                (p5 if stage == 5 else p6)[context.context_id] = stage5 if stage == 5 else stage6
        return (
            score_stage(5, contexts[5], references[5], p5, trial_id=trial_id + ":5"),
            score_stage(6, contexts[6], references[6], p6, trial_id=trial_id + ":6"),
        )

    r0_scores = score_params(r0_params, "R0:" + _trial_id("5_6", r0_params))
    trials: list[dict[str, Any]] = []
    for params in params_list:
        trial_id = _trial_id("5_6", params)
        s5, s6 = score_params(params, trial_id)
        g5 = _gate(s5, r0_f1=r0_scores[0].f1, gates=gates)
        g6 = _gate(s6, r0_f1=r0_scores[1].f1, gates=gates)
        trials.append(
            {
                "trial_id": trial_id,
                "params": params,
                "score": {
                    "f1": (s5.f1 + s6.f1) / 2,
                    "precision_lower": min(s5.precision_lower or 0.0, s6.precision_lower or 0.0),
                    "positive_reference_coverage": (
                        s5.positive_reference_coverage + s6.positive_reference_coverage
                    )
                    / 2,
                },
                "stage_5": s5.to_dict(),
                "stage_6": s6.to_dict(),
                "gate": {
                    "passed": g5["passed"] and g6["passed"],
                    "stage_5": g5,
                    "stage_6": g6,
                },
                "parameter_distance_from_r0": _parameter_distance(params, r0_params),
            }
        )
    eligible = [item for item in trials if item["gate"]["passed"]]
    selected = min(eligible, key=_rank_stage_trial) if eligible else None
    return {
        "r0": {
            "params": r0_params,
            "stage_5": r0_scores[0].to_dict(),
            "stage_6": r0_scores[1].to_dict(),
        },
        "trial_count": len(trials),
        "eligible_trial_count": len(eligible),
        "trials": trials,
        "selected": selected,
    }


def _search_single_stage(
    *,
    stage: int,
    params_list: Sequence[Mapping[str, Any]],
    r0_params: Mapping[str, Any],
    contexts: Sequence[SampledContext],
    references: Sequence[ResolvedReference],
    gates: Mapping[str, Any],
    stage10_params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    def predict(item: SecondGroupObservation, params: Mapping[str, Any]) -> bool:
        if stage == 10:
            return _predict_10(item, params)
        if stage == 7:
            if stage10_params is None:
                raise StageSecondGroupDevelopmentError("Stage 7 requires frozen Stage 10")
            return _predict_7(item, params, stage10_params)
        if stage == 11:
            if stage10_params is None:
                raise StageSecondGroupDevelopmentError("Stage 11 requires frozen Stage 10")
            return _predict_11(item, params, stage10_params)
        raise StageSecondGroupDevelopmentError("unsupported single-stage search")

    def score_params(params: Mapping[str, Any], trial_id: str) -> StageScore:
        predictions = {
            context.context_id: predict(context.observation, params)
            for context in contexts
        }
        return score_stage(stage, contexts, references, predictions, trial_id=trial_id)

    r0_score = score_params(r0_params, "R0:" + _trial_id(str(stage), r0_params))
    trials: list[dict[str, Any]] = []
    for params in params_list:
        trial_id = _trial_id(str(stage), params)
        score = score_params(params, trial_id)
        gate = _gate(score, r0_f1=r0_score.f1, gates=gates)
        trials.append(
            {
                "trial_id": trial_id,
                "params": dict(params),
                "score": score.to_dict(),
                "gate": gate,
                "parameter_distance_from_r0": _parameter_distance(params, r0_params),
            }
        )
    eligible = [item for item in trials if item["gate"]["passed"]]
    selected = (
        min(
            eligible,
            key=lambda item: (
                -float(item["score"]["f1"]),
                -float(item["score"]["precision_lower"] or 0.0),
                -float(item["score"]["positive_reference_coverage"]),
                float(item["parameter_distance_from_r0"]),
                json.dumps(item["params"], sort_keys=True),
            ),
        )
        if eligible
        else None
    )
    return {
        "r0": {"params": dict(r0_params), "score": r0_score.to_dict()},
        "trial_count": len(trials),
        "eligible_trial_count": len(eligible),
        "trials": trials,
        "selected": selected,
    }


def _candidate_params_first_group(
    candidate: Mapping[str, Any],
) -> tuple[Decimal, int, Decimal, Decimal]:
    try:
        raw = candidate["trial"]["parameters"]
        return (
            _decimal(raw["probe_volume_ratio20_min"], "probe"),
            int(raw["acceptance_hold_sessions"]),
            _decimal(raw["acceptance_signed_distance_min"], "acceptance distance"),
            _decimal(raw["acceptance_window_volume_ratio20_min"], "acceptance activity"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise StageSecondGroupDevelopmentError(
            "first-group candidate parameters are invalid"
        ) from exc


def _reference_summary(
    contexts: Mapping[int, Sequence[SampledContext]],
    references: Mapping[int, Sequence[ResolvedReference]],
    disagreements: Mapping[int, int],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for stage in STAGES:
        kinds: dict[str, int] = defaultdict(int)
        positives = 0
        negatives = 0
        buckets: dict[str, int] = defaultdict(int)
        for context in contexts[stage]:
            buckets[context.bucket] += 1
        for reference in references[stage]:
            kinds[reference.kind] += 1
            if reference.kind == "definite":
                positives += int(reference.supported)
                negatives += int(not reference.supported)
        result[str(stage)] = {
            "context_count": len(contexts[stage]),
            "bucket_counts": dict(sorted(buckets.items())),
            "reference_kind_counts": dict(sorted(kinds.items())),
            "definite_positive_count": positives,
            "definite_negative_count": negatives,
            "primary_disagreement_count": disagreements[stage],
            "audit_veto_count": 0,
        }
    return result


def run_second_group_development(
    *,
    config_path: Path,
    training_root: Path,
    output_root: Path,
    code_revision: str,
) -> dict[str, Any]:
    if not _REVISION.fullmatch(code_revision):
        raise StageSecondGroupDevelopmentError(
            "second-group development requires exact Git revision"
        )
    started = time.perf_counter()
    root = find_project_root()
    config = load_second_group_development_config(config_path)
    node_config = _object(root / str(config["training_node_config"]), "training node")
    first_candidate = _object(
        root / str(config["first_group_candidate_config"]), "first-group candidate"
    )
    stage56 = _object(root / str(config["stage_5_6_experiment_config"]), "Stage 5/6 experiment")
    repair = _object(
        root / str(config["stage_7_10_11_experiment_config"]), "Stage 7/10/11 experiment"
    )
    panel = load_second_group_panel(root / str(config["synthetic_panel_config"]))
    if first_candidate.get("status") != "frozen_for_external_evaluation":
        raise StageSecondGroupDevelopmentError("first-group candidate is not frozen")
    if stage56.get("authority", {}).get("runtime_search_authorized") is not False:
        raise StageSecondGroupDevelopmentError("Stage 5/6 design config runtime authority drifted")
    if repair.get("authority", {}).get("runtime_search_authorized") is not False:
        raise StageSecondGroupDevelopmentError(
            "Stage 7/10/11 design config runtime authority drifted"
        )
    if stage56.get("state_identity_policy", {}).get(
        "preserve_distinct_stage_codes"
    ) != [5, 6, 7, 10, 11]:
        raise StageSecondGroupDevelopmentError("Stage 5/6 identity binding differs")
    if repair.get("stage_identity", {}).get("preserve_distinct_stage_codes") != [5, 6, 7, 10, 11]:
        raise StageSecondGroupDevelopmentError("Stage 7/10/11 identity binding differs")

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
        raise StageSecondGroupDevelopmentError("development population is smaller than 512")
    cohort = tuple(sorted(ranked[:cohort_size]))
    _progress(started, "cohort-ready", eligible_population=len(ranked), cohort_size=cohort_size)

    spec_digest = digest(
        {
            "schema_version": "quanthus.stage-second-group-development-definition.v1",
            "development_id": config["development_id"],
            "first_group_candidate_digest": digest(first_candidate),
            "stage_5_6_experiment_digest": digest(stage56),
            "stage_7_10_11_experiment_digest": digest(repair),
            "synthetic_panel_digest": digest(panel),
            "official_stage_labels": False,
        }
    )
    report_start = _date(window, "report_start")
    report_end = _date(window, "report_end")
    admissions, source_manifest, policy = read_development_admissions(
        training_root=training_root,
        node_config=node_config,
        scope=DevelopmentReadScope(_date(window, "warmup_start"), report_start, report_end),
        security_codes=cohort,
        spec_digest=spec_digest,
    )
    _progress(started, "source-ready", admissions=len(admissions))
    structure = build_structure_v2(
        admissions,
        spec_digest=spec_digest,
        calendar_ref=policy.calendar_ref,
        lookbacks=tuple(int(item) for item in config["population"]["anchor_scales"]),
    )
    _progress(started, "structure-ready", episode_observations=len(structure.episodes))
    activity = build_activity_observations(admissions, window=20)
    probe, hold, signed, activity_min = _candidate_params_first_group(first_candidate)
    first_candidates, _decisions = build_stage34_decisions(
        structure,
        activity,
        probe_volume_ratio_min=probe,
        acceptance_hold_sessions=hold,
        acceptance_signed_distance_min=signed,
        acceptance_window_volume_ratio_min=activity_min,
    )
    observations = build_second_group_observations(
        first_candidates,
        structure,
        admissions,
        maximum_sessions=int(config["population"]["maximum_path_sessions_including_first_stage_4"]),
    )
    _progress(
        started,
        "second-group-panel-ready",
        accepted_stage4_episode_count=len(_first_confirmed_stage4_candidates(first_candidates)),
        observation_count=len(observations),
    )

    sampling = config["reference_sampling"]
    contexts: dict[int, tuple[SampledContext, ...]] = {}
    references: dict[int, tuple[ResolvedReference, ...]] = {}
    disagreements: dict[int, int] = {}
    for stage in STAGES:
        contexts[stage] = sample_stage_contexts(
            observations,
            stage=stage,
            seed=str(sampling["seed"]),
            per_bucket=int(sampling["target_contexts_per_bucket"]),
            minimum_total=int(sampling["minimum_realized_contexts_per_stage"]),
        )
        references[stage], disagreements[stage] = resolve_stage_references(contexts[stage], panel)
    reference_summary = _reference_summary(contexts, references, disagreements)
    _progress(
        started,
        "references-ready",
        reference_counts={str(stage): len(contexts[stage]) for stage in STAGES},
        disagreement_counts={str(stage): disagreements[stage] for stage in STAGES},
    )

    gates = config["hard_gates"]
    stage56_result = _search_5_6(contexts, references, stage56, gates)
    _progress(
        started,
        "stage-5-6-search-complete",
        eligible_trials=stage56_result["eligible_trial_count"],
    )

    stage10_raw = repair["stage_10"]
    stage10_params = [
        {
            "unreclaimed_persistence_sessions": int(persistence),
            "failure_depth_abs_min": str(depth),
        }
        for persistence, depth in itertools.product(
            stage10_raw["r1_search_space"]["unreclaimed_persistence_sessions"],
            stage10_raw["r1_search_space"]["failure_depth_abs_min"],
        )
    ]
    stage10_r0 = dict(stage10_raw["r0_baseline"])
    stage10_result = _search_single_stage(
        stage=10,
        params_list=stage10_params,
        r0_params=stage10_r0,
        contexts=contexts[10],
        references=references[10],
        gates=gates,
    )
    _progress(
        started,
        "stage-10-search-complete",
        eligible_trials=stage10_result["eligible_trial_count"],
    )

    stage7_result: dict[str, Any]
    stage11_result: dict[str, Any]
    frozen10 = None if stage10_result["selected"] is None else stage10_result["selected"]["params"]
    if frozen10 is None:
        stage7_result = {"blocked": True, "reason": "stage_10_inconclusive"}
        stage11_result = {"blocked": True, "reason": "stage_10_inconclusive"}
    else:
        stage7_raw = repair["stage_7"]
        stage7_params = [
            {
                "damage_depth_abs_min": str(damage),
                "repair_fraction_min": str(repair_fraction),
            }
            for damage, repair_fraction in itertools.product(
                stage7_raw["deep_damage_drawdown_abs_min_candidates"],
                stage7_raw["repair_fraction_min_candidates"],
            )
        ]
        stage7_r0 = {"damage_depth_abs_min": "0.16", "repair_fraction_min": "0.40"}
        stage7_result = _search_single_stage(
            stage=7,
            params_list=stage7_params,
            r0_params=stage7_r0,
            contexts=contexts[7],
            references=references[7],
            gates=gates,
            stage10_params=frozen10,
        )
        _progress(
            started,
            "stage-7-search-complete",
            eligible_trials=stage7_result["eligible_trial_count"],
        )
        stage11_raw = repair["stage_11"]
        stage11_params = [
            {"rebound_from_post_break_low_min": str(value)}
            for value in stage11_raw["rebound_from_post_break_low_min_candidates"]
        ]
        stage11_r0 = {"rebound_from_post_break_low_min": "0.05"}
        stage11_result = _search_single_stage(
            stage=11,
            params_list=stage11_params,
            r0_params=stage11_r0,
            contexts=contexts[11],
            references=references[11],
            gates=gates,
            stage10_params=frozen10,
        )
        _progress(
            started,
            "stage-11-search-complete",
            eligible_trials=stage11_result["eligible_trial_count"],
        )

    selected = {
        "stage_5_6": (
            None
            if stage56_result["selected"] is None
            else stage56_result["selected"]["params"]
        ),
        "stage_10": frozen10,
        "stage_7": (
            None if stage7_result.get("selected") is None else stage7_result["selected"]["params"]
        ),
        "stage_11": (
            None if stage11_result.get("selected") is None else stage11_result["selected"]["params"]
        ),
    }
    complete = all(value is not None for value in selected.values())
    disposition = "DEVELOPMENT_CANDIDATES_FROZEN" if complete else "INCONCLUSIVE"
    candidate = {
        "schema_version": "quanthus.stage-second-group-development-candidate-artifact.v1",
        "development_id": config["development_id"],
        "evidence_scope": "development_only_not_confirmatory",
        "reference_evidence_tier": "SYNTHETIC_EXPERT_CONSENSUS",
        "human_expert_validated": False,
        "preserve_distinct_stage_codes": [5, 6, 7, 10, 11],
        "stage_5_6_merged": False,
        "stage_10_11_merged": False,
        "selection_order": ["stage_5_6", "stage_10", "stage_7", "stage_11"],
        "selected_parameters": selected,
        "complete": complete,
        "disposition": disposition,
        "official_label_publication_authorized": False,
        "strategy_consumption_authorized": False,
        "trading_authorized": False,
    }
    summary = {
        "schema_version": "quanthus.stage-second-group-development-summary.v1",
        "work_item": "P3-STAGE-001-H",
        "code_revision": code_revision,
        "development_id": config["development_id"],
        "development_config_digest": digest(config),
        "first_group_candidate_digest": digest(first_candidate),
        "stage_5_6_experiment_digest": digest(stage56),
        "stage_7_10_11_experiment_digest": digest(repair),
        "synthetic_panel_digest": digest(panel),
        "source_manifest_digest": digest(source_manifest),
        "cohort_size": cohort_size,
        "cohort_digest": digest(cohort),
        "observation_count": len(observations),
        "reference_summary": reference_summary,
        "stage_5_6": stage56_result,
        "stage_10": stage10_result,
        "stage_7": stage7_result,
        "stage_11": stage11_result,
        "selected_parameters": selected,
        "disposition": disposition,
        "development_only": True,
        "parameter_search_performed": True,
        "future_outcome_read": False,
        "arbitration_read": False,
        "permanent_blind_read": False,
        "holdout_population_selected": False,
        "stage_labels_emitted": False,
        "qualified_label_version": False,
        "elapsed_seconds": time.perf_counter() - started,
        "claim_limits": list(config["claim_limits"]),
    }
    run_id = "stage-second-group-development-" + datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    run_root = output_root.expanduser().resolve() / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    summary_payload = canonical(summary)
    candidate_payload = canonical(candidate)
    (run_root / "summary.json").write_bytes(summary_payload)
    (run_root / "candidate.json").write_bytes(candidate_payload)
    manifest = {
        "schema_version": "quanthus.stage-second-group-development-artifact.v1",
        "run_id": run_id,
        "code_revision": code_revision,
        "development_config_digest": digest(config),
        "files": {
            "candidate.json": _sha256(candidate_payload),
            "summary.json": _sha256(summary_payload),
        },
        "development_only": True,
        "parameter_search_performed": True,
        "future_outcome_read": False,
        "arbitration_read": False,
        "permanent_blind_read": False,
        "holdout_population_selected": False,
        "stage_labels_emitted": False,
        "qualified_label_version": False,
        "disposition": disposition,
    }
    manifest_bytes = canonical(manifest)
    (run_root / "manifest.json").write_bytes(manifest_bytes)
    ready = _sha256(manifest_bytes)
    (run_root / "READY").write_text(ready + "\n", encoding="ascii")
    return {
        **summary,
        "run_id": run_id,
        "run_root": str(run_root),
        "manifest_sha256": ready,
        "candidate_artifact_digest": _sha256(candidate_payload),
    }


def verify_second_group_development_artifact(
    path: Path, *, code_revision: str | None = None
) -> dict[str, Any]:
    root = path.expanduser().resolve()
    manifest = _object(root / "manifest.json", "second-group development manifest")
    if manifest.get("schema_version") != "quanthus.stage-second-group-development-artifact.v1":
        raise StageSecondGroupDevelopmentError("second-group development artifact schema differs")
    manifest_bytes = canonical(manifest)
    try:
        ready = (root / "READY").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise StageSecondGroupDevelopmentError(
            "second-group development READY is unreadable"
        ) from exc
    if ready != _sha256(manifest_bytes):
        raise StageSecondGroupDevelopmentError(
            "second-group development READY does not bind manifest"
        )
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise StageSecondGroupDevelopmentError("second-group development code revision differs")
    files = manifest.get("files")
    if not isinstance(files, Mapping) or set(files) != {"summary.json", "candidate.json"}:
        raise StageSecondGroupDevelopmentError("second-group development file inventory differs")
    for name, expected in files.items():
        if _sha256((root / name).read_bytes()) != expected:
            raise StageSecondGroupDevelopmentError(
                f"second-group development digest differs: {name}"
            )
    summary = _object(root / "summary.json", "second-group development summary")
    candidate = _object(root / "candidate.json", "second-group development candidate")
    if (
        summary.get("development_only") is not True
        or summary.get("future_outcome_read") is not False
        or summary.get("arbitration_read") is not False
        or summary.get("permanent_blind_read") is not False
        or summary.get("stage_labels_emitted") is not False
        or summary.get("qualified_label_version") is not False
        or candidate.get("preserve_distinct_stage_codes") != [5, 6, 7, 10, 11]
        or candidate.get("stage_5_6_merged") is not False
        or candidate.get("stage_10_11_merged") is not False
    ):
        raise StageSecondGroupDevelopmentError("second-group development authority differs")
    return {
        "run_id": manifest["run_id"],
        "manifest_sha256": ready,
        "candidate_artifact_digest": files["candidate.json"],
        "file_count": 2,
        "verified": True,
        "disposition": summary["disposition"],
        "selected_parameters": summary["selected_parameters"],
        "reference_summary": summary["reference_summary"],
        "development_only": True,
        "arbitration_read": False,
        "permanent_blind_read": False,
        "stage_labels_emitted": False,
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
        result = run_second_group_development(
            config_path=args.config,
            training_root=args.training_root,
            output_root=args.output_root,
            code_revision=args.code_revision,
        )
    else:
        result = verify_second_group_development_artifact(
            args.artifact,
            code_revision=args.code_revision,
        )
    print(canonical(result).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Development-only Stage-7/10/11 repair/failure morphology profile.

This runner preserves Stage 5/6/7/10/11 as distinct stage identities. It emits no stage labels and
performs no parameter search. Starting from the frozen first-group Stage-4 acceptance point, it
measures causal damage/repair cycles and first-break/reclaim sequences for two predeclared support
roles inside 2020-2021 development data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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
from quanthus.structure_v2 import EpisodeObservation, StructureV2Result, build_structure_v2
from quanthus.structure_v2_inputs import Admission, canonical, digest

DEFAULT_CONFIG = "config/stage-learning/second-group-repair-profile.v1.json"
_REVISION = re.compile(r"^[0-9a-f]{40}$")


class StageSecondGroupRepairProfileError(ValueError):
    """Raised when the Stage-7/10/11 development profile contract is violated."""


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StageSecondGroupRepairProfileError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise StageSecondGroupRepairProfileError(f"{label} must be a JSON object")
    return value


def _date(value: Mapping[str, Any], key: str) -> date:
    try:
        return date.fromisoformat(str(value[key]))
    except (KeyError, ValueError) as exc:
        raise StageSecondGroupRepairProfileError(f"invalid repair-profile date: {key}") from exc


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _ratio(current: Decimal, prior: Decimal) -> Decimal:
    if prior <= 0:
        raise StageSecondGroupRepairProfileError("repair-profile denominator must be positive")
    with localcontext(Context(prec=28)):
        return current / prior - Decimal(1)


def _fraction(numerator: Decimal, denominator: Decimal) -> Decimal:
    if denominator <= 0:
        raise StageSecondGroupRepairProfileError("repair fraction denominator must be positive")
    with localcontext(Context(prec=28)):
        return numerator / denominator


def _quantiles(values: Sequence[Decimal], probabilities: Sequence[float]) -> dict[str, str] | None:
    if not values:
        return None
    ordered = sorted(values)
    result: dict[str, str] = {}
    for probability in probabilities:
        rank = max(1, math.ceil(float(probability) * len(ordered)))
        result[f"p{int(round(float(probability) * 100)):02d}"] = format(ordered[rank - 1], "f")
    return result


def _integer_quantiles(
    values: Sequence[int], probabilities: Sequence[float]
) -> dict[str, int] | None:
    if not values:
        return None
    ordered = sorted(values)
    result: dict[str, int] = {}
    for probability in probabilities:
        rank = max(1, math.ceil(float(probability) * len(ordered)))
        result[f"p{int(round(float(probability) * 100)):02d}"] = ordered[rank - 1]
    return result


def _progress(started: float, stage: str, **details: Any) -> None:
    print(
        json.dumps(
            {
                "event": "stage-second-group-repair-profile-progress",
                "stage": stage,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                **details,
            },
            ensure_ascii=True,
            sort_keys=True,
        ),
        flush=True,
    )


def load_second_group_repair_profile_config(path: Path) -> dict[str, Any]:
    raw = _object(path, "second-group repair profile config")
    expected = {
        "schema_version",
        "profile_id",
        "work_item",
        "purpose",
        "training_node_config",
        "candidate_config",
        "candidate_digest",
        "second_group_experiment_config",
        "authority",
        "development_window",
        "population",
        "entry_contract",
        "state_identity_contract",
        "path_contract",
        "repair_profile",
        "failure_profile",
        "rebound_profile",
        "measurements",
        "artifact",
    }
    if (
        set(raw) != expected
        or raw.get("schema_version") != "quanthus.stage-second-group-repair-profile.v1"
        or raw.get("work_item") != "P3-STAGE-001-H"
    ):
        raise StageSecondGroupRepairProfileError("repair profile config identity differs")
    authority = raw.get("authority")
    if not isinstance(authority, Mapping):
        raise StageSecondGroupRepairProfileError("repair profile authority is missing")
    required_true = {
        "development_only",
        "runtime_implementation_authorized",
        "development_data_read_authorized",
        "continuous_profile_output_authorized",
    }
    required_false = set(authority) - required_true
    if any(authority.get(key) is not True for key in required_true) or any(
        authority.get(key) is not False for key in required_false
    ):
        raise StageSecondGroupRepairProfileError("repair profile authority differs")

    identity = raw.get("state_identity_contract")
    if not isinstance(identity, Mapping) or (
        identity.get("preserve_distinct_stage_codes") != [5, 6, 7, 10, 11]
        or identity.get("stage_5_6_merge_authorized") is not False
        or identity.get("stage_10_11_merge_authorized") is not False
        or identity.get("later_repair_does_not_retroactively_delete_an_earlier_valid_failure")
        is not True
    ):
        raise StageSecondGroupRepairProfileError("stage identity contract differs")

    window = raw.get("development_window")
    population = raw.get("population")
    path_contract = raw.get("path_contract")
    repair = raw.get("repair_profile")
    failure = raw.get("failure_profile")
    measurements = raw.get("measurements")
    if not all(
        isinstance(item, Mapping)
        for item in (window, population, path_contract, repair, failure, measurements)
    ):
        raise StageSecondGroupRepairProfileError("repair profile sections are missing")
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
        raise StageSecondGroupRepairProfileError("repair profile must stay in development dates")
    if population.get("cohort_size") != 512 or population.get("anchor_scales") != [20, 60]:
        raise StageSecondGroupRepairProfileError("repair profile population differs")
    if path_contract.get("maximum_path_sessions_including_first_stage_4") != 60:
        raise StageSecondGroupRepairProfileError("repair profile horizon differs")
    roles = path_contract.get("support_roles")
    if not isinstance(roles, Mapping) or set(roles) != {
        "acceptance_anchor",
        "initial_low_support_candidate",
    }:
        raise StageSecondGroupRepairProfileError("repair profile support roles differ")
    if path_contract.get("support_role_selection_inside_job_authorized") is not False:
        raise StageSecondGroupRepairProfileError("repair profile cannot select a support role")

    root = find_project_root()
    experiment = _object(
        root / str(raw["second_group_experiment_config"]), "second-group experiment"
    )
    experiment_identity = experiment.get("state_identity_policy")
    if not isinstance(experiment_identity, Mapping) or (
        experiment_identity.get("preserve_distinct_stage_codes") != [5, 6, 7, 10, 11]
        or experiment_identity.get("stage_5_6_merge_authorized") is not False
        or experiment_identity.get("stage_10_11_merge_authorized") is not False
    ):
        raise StageSecondGroupRepairProfileError("second-group experiment identity differs")
    deep = repair.get("deep_damage_candidate_abs_thresholds")
    expected_deep = experiment.get("r1_search_space", {}).get("deep_damage_drawdown_abs_min")
    if deep != expected_deep or deep != ["0.12", "0.16", "0.20"]:
        raise StageSecondGroupRepairProfileError("repair damage candidates differ")
    if failure.get("persistence_candidate_sessions") != [3, 5, 8]:
        raise StageSecondGroupRepairProfileError("failure persistence candidates differ")
    quantiles = measurements.get("quantiles")
    if (
        not isinstance(quantiles, list)
        or not quantiles
        or quantiles != sorted(set(quantiles))
        or any(not isinstance(item, (int, float)) or not 0 < float(item) < 1 for item in quantiles)
        or measurements.get("quantile_method") != "nearest_rank"
        or measurements.get("no_stage_threshold_is_derived_inside_this_job") is not True
    ):
        raise StageSecondGroupRepairProfileError("repair profile quantile contract differs")
    return raw


def _candidate(root: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    candidate = _object(root / str(config["candidate_config"]), "development candidate")
    if (
        candidate.get("schema_version") != "quanthus.stage-development-candidate.v1"
        or digest(candidate) != config.get("candidate_digest")
        or candidate.get("status") != "frozen_for_external_evaluation"
    ):
        raise StageSecondGroupRepairProfileError("frozen Stage-4 candidate differs")
    return candidate


def _candidate_params(candidate: Mapping[str, Any]) -> tuple[Decimal, int, Decimal, Decimal]:
    try:
        raw = candidate["trial"]["parameters"]
        return (
            Decimal(str(raw["probe_volume_ratio20_min"])),
            int(raw["acceptance_hold_sessions"]),
            Decimal(str(raw["acceptance_signed_distance_min"])),
            Decimal(str(raw["acceptance_window_volume_ratio20_min"])),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise StageSecondGroupRepairProfileError(
            "Stage-4 candidate parameters are invalid"
        ) from exc


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


@dataclass(frozen=True)
class RepairObservation:
    scale: int
    damage_depth: Decimal
    repair_fraction: Decimal | None


@dataclass(frozen=True)
class RepairCycleProfile:
    scale: int
    maximum_damage_depth: Decimal
    maximum_repair_fraction: Decimal
    fully_repaired: bool
    sessions_from_trough_to_full_repair: int | None


@dataclass(frozen=True)
class SupportObservationProfile:
    scale: int
    support_role: str
    unreclaimed_age_sessions: int
    distance_to_support: Decimal
    rebound_from_post_break_low: Decimal


@dataclass(frozen=True)
class SupportEpisodeProfile:
    scale: int
    support_role: str
    sessions_to_break: int | None
    sessions_to_reclaim_after_break: int | None
    post_break_observed_age_sessions: int | None
    maximum_unreclaimed_age_sessions: int | None
    minimum_unreclaimed_distance_to_support: Decimal | None
    maximum_rebound_while_unreclaimed: Decimal | None


@dataclass
class _SupportState:
    role: str
    level: Decimal
    break_offset: int | None = None
    reclaim_after: int | None = None
    post_break_observed_age: int | None = None
    maximum_unreclaimed_age: int | None = None
    minimum_unreclaimed_distance: Decimal | None = None
    post_break_low: Decimal | None = None
    maximum_unreclaimed_rebound: Decimal | None = None


@dataclass
class _RepairCycleState:
    peak: Decimal
    trough: Decimal
    trough_offset: int
    maximum_repair_fraction: Decimal = Decimal(0)


def _support_levels(episode: EpisodeObservation) -> dict[str, Decimal]:
    if episode.frozen_level <= 0 or episode.frozen_low <= 0:
        raise StageSecondGroupRepairProfileError("Stage-4 episode support levels must be positive")
    return {
        "acceptance_anchor": episode.frozen_level,
        "initial_low_support_candidate": episode.frozen_low,
    }


def build_second_group_repair_profiles(
    candidates: Sequence[StageCandidateObservation],
    structure: StructureV2Result,
    admissions: Sequence[Admission],
    *,
    maximum_sessions: int,
) -> tuple[
    tuple[RepairObservation, ...],
    tuple[RepairCycleProfile, ...],
    tuple[SupportObservationProfile, ...],
    tuple[SupportEpisodeProfile, ...],
    dict[str, int],
]:
    """Build T-only repair/failure profiles without mapping any row to Stage 7/10/11."""
    if maximum_sessions < 2:
        raise StageSecondGroupRepairProfileError("repair profile horizon must be at least two")
    starts = _first_confirmed_stage4_candidates(candidates)
    episode_by_key = {
        (item.security_code, item.scale, item.episode_id, item.market_date): item
        for item in structure.episodes
    }
    rows_by_security: dict[str, list[Admission]] = defaultdict(list)
    for item in sorted(admissions, key=lambda row: (row.row.security_code, row.row.market_date)):
        rows_by_security[item.row.security_code].append(item)

    repairs: list[RepairObservation] = []
    repair_cycles: list[RepairCycleProfile] = []
    support_observations: list[SupportObservationProfile] = []
    support_episodes: list[SupportEpisodeProfile] = []
    stopped = {"nonadmitted": 0, "coordinate_or_listing_change": 0, "report_end_or_horizon": 0}

    for candidate in starts:
        episode = episode_by_key.get(
            (
                candidate.security_code,
                candidate.anchor_scale,
                candidate.episode_id,
                candidate.market_date,
            )
        )
        if episode is None or episode.close is None:
            raise StageSecondGroupRepairProfileError("Stage-4 candidate lacks bound episode")
        series = rows_by_security.get(candidate.security_code, [])
        start_index = next(
            (
                index
                for index, item in enumerate(series)
                if item.row.market_date == candidate.market_date
            ),
            None,
        )
        if start_index is None:
            raise StageSecondGroupRepairProfileError("Stage-4 row is missing from admissions")
        acceptance = series[start_index]
        if not acceptance.admitted or acceptance.row.close is None:
            raise StageSecondGroupRepairProfileError("Stage-4 acceptance row is not admitted")
        listing_id = acceptance.row.listing_id
        coordinate_ref = acceptance.row.coordinate_ref
        cycle = _RepairCycleState(acceptance.row.close, acceptance.row.close, 0)
        support_states = {
            role: _SupportState(role, level) for role, level in _support_levels(episode).items()
        }
        previous_close: Decimal | None = None
        stopped_reason = "report_end_or_horizon"

        for offset, item in enumerate(series[start_index : start_index + maximum_sessions]):
            row = item.row
            if not item.admitted or row.close is None:
                stopped_reason = "nonadmitted"
                break
            if row.listing_id != listing_id or row.coordinate_ref != coordinate_ref:
                stopped_reason = "coordinate_or_listing_change"
                break
            close = row.close

            if close >= cycle.peak:
                if cycle.trough < cycle.peak:
                    depth = _ratio(cycle.trough, cycle.peak)
                    repairs.append(RepairObservation(candidate.anchor_scale, depth, Decimal(1)))
                    repair_cycles.append(
                        RepairCycleProfile(
                            candidate.anchor_scale,
                            depth,
                            Decimal(1),
                            True,
                            offset - cycle.trough_offset,
                        )
                    )
                cycle = _RepairCycleState(close, close, offset)
                if not repairs or repairs[-1].scale != candidate.anchor_scale or offset == 0:
                    repairs.append(RepairObservation(candidate.anchor_scale, Decimal(0), None))
            else:
                if close < cycle.trough:
                    cycle.trough = close
                    cycle.trough_offset = offset
                    cycle.maximum_repair_fraction = Decimal(0)
                depth = _ratio(cycle.trough, cycle.peak)
                repair_fraction = _fraction(close - cycle.trough, cycle.peak - cycle.trough)
                cycle.maximum_repair_fraction = max(
                    cycle.maximum_repair_fraction, repair_fraction
                )
                repairs.append(
                    RepairObservation(candidate.anchor_scale, depth, repair_fraction)
                )

            for state in support_states.values():
                if (
                    state.break_offset is None
                    and previous_close is not None
                    and previous_close >= state.level > close
                ):
                    state.break_offset = offset
                    state.post_break_observed_age = 0
                    state.maximum_unreclaimed_age = 0
                    state.post_break_low = close
                    state.minimum_unreclaimed_distance = _ratio(close, state.level)
                    state.maximum_unreclaimed_rebound = Decimal(0)
                if state.break_offset is None or state.reclaim_after is not None:
                    continue
                if (
                    offset > state.break_offset
                    and previous_close is not None
                    and previous_close <= state.level < close
                ):
                    state.reclaim_after = offset - state.break_offset
                    state.post_break_observed_age = state.reclaim_after
                    continue
                age = offset - state.break_offset
                state.post_break_observed_age = age
                state.maximum_unreclaimed_age = max(state.maximum_unreclaimed_age or 0, age)
                distance = _ratio(close, state.level)
                state.minimum_unreclaimed_distance = (
                    distance
                    if state.minimum_unreclaimed_distance is None
                    else min(state.minimum_unreclaimed_distance, distance)
                )
                state.post_break_low = (
                    close if state.post_break_low is None else min(state.post_break_low, close)
                )
                rebound = _ratio(close, state.post_break_low)
                state.maximum_unreclaimed_rebound = (
                    rebound
                    if state.maximum_unreclaimed_rebound is None
                    else max(state.maximum_unreclaimed_rebound, rebound)
                )
                support_observations.append(
                    SupportObservationProfile(
                        candidate.anchor_scale,
                        state.role,
                        age,
                        distance,
                        rebound,
                    )
                )
            previous_close = close

        if cycle.trough < cycle.peak:
            repair_cycles.append(
                RepairCycleProfile(
                    candidate.anchor_scale,
                    _ratio(cycle.trough, cycle.peak),
                    cycle.maximum_repair_fraction,
                    False,
                    None,
                )
            )
        for state in support_states.values():
            support_episodes.append(
                SupportEpisodeProfile(
                    candidate.anchor_scale,
                    state.role,
                    state.break_offset,
                    state.reclaim_after,
                    state.post_break_observed_age,
                    state.maximum_unreclaimed_age,
                    state.minimum_unreclaimed_distance,
                    state.maximum_unreclaimed_rebound,
                )
            )
        stopped[stopped_reason] += 1

    return (
        tuple(repairs),
        tuple(repair_cycles),
        tuple(support_observations),
        tuple(support_episodes),
        stopped,
    )


def summarize_second_group_repair_profiles(
    repairs: Sequence[RepairObservation],
    cycles: Sequence[RepairCycleProfile],
    support_observations: Sequence[SupportObservationProfile],
    support_episodes: Sequence[SupportEpisodeProfile],
    *,
    deep_damage_thresholds: Sequence[Decimal],
    persistence_candidates: Sequence[int],
    quantiles: Sequence[float],
) -> dict[str, Any]:
    def decimals(values: Sequence[Decimal]) -> dict[str, Any]:
        return {"count": len(values), "quantiles": _quantiles(values, quantiles)}

    def integers(values: Sequence[int]) -> dict[str, Any]:
        return {"count": len(values), "quantiles": _integer_quantiles(values, quantiles)}

    result: dict[str, Any] = {}
    scales = sorted({item.scale for item in support_episodes} | {item.scale for item in cycles})
    for scale in scales:
        scale_repairs = [item for item in repairs if item.scale == scale]
        scale_cycles = [item for item in cycles if item.scale == scale]
        repair_by_depth: dict[str, Any] = {}
        for threshold in deep_damage_thresholds:
            relevant_observations = [
                item
                for item in scale_repairs
                if -item.damage_depth >= threshold and item.repair_fraction is not None
            ]
            relevant_cycles = [
                item for item in scale_cycles if -item.maximum_damage_depth >= threshold
            ]
            fully = [item for item in relevant_cycles if item.fully_repaired]
            repair_by_depth[format(threshold, "f")] = {
                "observation_count": len(relevant_observations),
                "repair_fraction": decimals(
                    [
                        item.repair_fraction
                        for item in relevant_observations
                        if item.repair_fraction is not None
                    ]
                ),
                "cycle_count": len(relevant_cycles),
                "fully_repaired_cycle_count": len(fully),
                "fully_repaired_cycle_rate": (
                    len(fully) / len(relevant_cycles) if relevant_cycles else None
                ),
                "sessions_from_trough_to_full_repair": integers(
                    [
                        item.sessions_from_trough_to_full_repair
                        for item in fully
                        if item.sessions_from_trough_to_full_repair is not None
                    ]
                ),
            }

        support_by_role: dict[str, Any] = {}
        for role in ("acceptance_anchor", "initial_low_support_candidate"):
            episodes = [
                item
                for item in support_episodes
                if item.scale == scale and item.support_role == role
            ]
            observations = [
                item
                for item in support_observations
                if item.scale == scale and item.support_role == role
            ]
            broken = [item for item in episodes if item.sessions_to_break is not None]
            reclaimed = [
                item for item in broken if item.sessions_to_reclaim_after_break is not None
            ]
            persistence: dict[str, Any] = {}
            for candidate in persistence_candidates:
                at_risk = [
                    item
                    for item in broken
                    if (
                        item.sessions_to_reclaim_after_break is not None
                        and item.sessions_to_reclaim_after_break <= candidate
                    )
                    or (
                        item.post_break_observed_age_sessions is not None
                        and item.post_break_observed_age_sessions >= candidate
                    )
                ]
                reclaimed_within = [
                    item
                    for item in at_risk
                    if item.sessions_to_reclaim_after_break is not None
                    and item.sessions_to_reclaim_after_break <= candidate
                ]
                unreclaimed_at_candidate = [
                    item
                    for item in observations
                    if item.unreclaimed_age_sessions == candidate
                ]
                persistence[str(candidate)] = {
                    "at_risk_episode_count": len(at_risk),
                    "reclaimed_within_candidate_count": len(reclaimed_within),
                    "reclaimed_within_candidate_rate": (
                        len(reclaimed_within) / len(at_risk) if at_risk else None
                    ),
                    "persistent_unreclaimed_episode_count": len(unreclaimed_at_candidate),
                    "distance_to_support_at_candidate": decimals(
                        [item.distance_to_support for item in unreclaimed_at_candidate]
                    ),
                    "rebound_from_post_break_low_at_candidate": decimals(
                        [item.rebound_from_post_break_low for item in unreclaimed_at_candidate]
                    ),
                }
            support_by_role[role] = {
                "path_count": len(episodes),
                "break_episode_count": len(broken),
                "reclaim_episode_count": len(reclaimed),
                "reclaim_rate_among_breaks": len(reclaimed) / len(broken) if broken else None,
                "sessions_to_break": integers(
                    [
                        item.sessions_to_break
                        for item in broken
                        if item.sessions_to_break is not None
                    ]
                ),
                "sessions_to_reclaim_after_break": integers(
                    [
                        item.sessions_to_reclaim_after_break
                        for item in reclaimed
                        if item.sessions_to_reclaim_after_break is not None
                    ]
                ),
                "maximum_unreclaimed_age_sessions": integers(
                    [
                        item.maximum_unreclaimed_age_sessions
                        for item in broken
                        if item.maximum_unreclaimed_age_sessions is not None
                    ]
                ),
                "minimum_unreclaimed_distance_to_support": decimals(
                    [
                        item.minimum_unreclaimed_distance_to_support
                        for item in broken
                        if item.minimum_unreclaimed_distance_to_support is not None
                    ]
                ),
                "maximum_rebound_while_unreclaimed": decimals(
                    [
                        item.maximum_rebound_while_unreclaimed
                        for item in broken
                        if item.maximum_rebound_while_unreclaimed is not None
                    ]
                ),
                "persistence_candidates": persistence,
            }
        result[str(scale)] = {
            "repair_profile_by_deep_damage_candidate": repair_by_depth,
            "support_role_profiles": support_by_role,
        }
    return result


def run_second_group_repair_profile(
    *,
    config_path: Path,
    training_root: Path,
    output_root: Path,
    code_revision: str,
) -> dict[str, Any]:
    if not _REVISION.fullmatch(code_revision):
        raise StageSecondGroupRepairProfileError("repair profile requires exact Git revision")
    started = time.perf_counter()
    root = find_project_root()
    config = load_second_group_repair_profile_config(config_path)
    candidate = _candidate(root, config)
    node_config = _object(root / str(config["training_node_config"]), "training node")
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
        raise StageSecondGroupRepairProfileError("development population is smaller than cohort")
    cohort = tuple(sorted(ranked[:cohort_size]))
    _progress(started, "cohort-ready", eligible_population=len(ranked), cohort_size=cohort_size)

    spec_digest = digest(
        {
            "schema_version": "quanthus.stage-second-group-repair-profile-definition.v1",
            "profile_id": config["profile_id"],
            "candidate_digest": digest(candidate),
            "state_identity_contract": config["state_identity_contract"],
            "path_contract": config["path_contract"],
            "repair_profile": config["repair_profile"],
            "failure_profile": config["failure_profile"],
            "rebound_profile": config["rebound_profile"],
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
    probe, hold, signed, activity_min = _candidate_params(candidate)
    candidates, _decisions = build_stage34_decisions(
        structure,
        activity,
        probe_volume_ratio_min=probe,
        acceptance_hold_sessions=hold,
        acceptance_signed_distance_min=signed,
        acceptance_window_volume_ratio_min=activity_min,
    )
    accepted = _first_confirmed_stage4_candidates(candidates)
    _progress(started, "stage4-entry-ready", accepted_episode_count=len(accepted))
    repairs, cycles, support_obs, support_episodes, stopped = build_second_group_repair_profiles(
        candidates,
        structure,
        admissions,
        maximum_sessions=int(
            config["path_contract"]["maximum_path_sessions_including_first_stage_4"]
        ),
    )
    deep = tuple(
        Decimal(str(item))
        for item in config["repair_profile"]["deep_damage_candidate_abs_thresholds"]
    )
    persistence = tuple(
        int(item) for item in config["failure_profile"]["persistence_candidate_sessions"]
    )
    quantiles = tuple(float(item) for item in config["measurements"]["quantiles"])
    scale_profiles = summarize_second_group_repair_profiles(
        repairs,
        cycles,
        support_obs,
        support_episodes,
        deep_damage_thresholds=deep,
        persistence_candidates=persistence,
        quantiles=quantiles,
    )

    run_id = "stage-second-group-repair-profile-" + datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    run_root = output_root.expanduser().resolve() / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    summary = {
        "schema_version": "quanthus.stage-second-group-repair-profile-summary.v1",
        "work_item": "P3-STAGE-001-H",
        "run_id": run_id,
        "code_revision": code_revision,
        "profile_id": config["profile_id"],
        "profile_config_digest": digest(config),
        "candidate_digest": digest(candidate),
        "development_only": True,
        "development_window": {"report_start": report_start, "report_end": report_end},
        "cohort_size": cohort_size,
        "cohort_digest": digest(cohort),
        "accepted_stage4_episode_count": len(accepted),
        "repair_observation_count": len(repairs),
        "repair_cycle_count": len(cycles),
        "support_observation_count": len(support_obs),
        "path_stop_counts": stopped,
        "scale_profiles": scale_profiles,
        "preserved_stage_codes": [5, 6, 7, 10, 11],
        "stage_5_6_merged": False,
        "stage_10_11_merged": False,
        "stage_labels_emitted": False,
        "parameter_search_performed": False,
        "synthetic_panel_performed": False,
        "future_settlement_read": False,
        "arbitration_read": False,
        "permanent_blind_read": False,
        "holdout_population_selected": False,
        "strategy_result_read": False,
        "source_manifest_digest": digest(source_manifest),
        "elapsed_seconds": time.perf_counter() - started,
        "claim_limits": [
            (
                "Stage 5/6 and Stage 10/11 remain distinct stage codes; "
                "this profile does not merge them"
            ),
            "damage depth alone is not Stage 7; repair evidence is measured separately",
            "one support crossing is not Stage 10; persistence and reclaim are measured separately",
            (
                "rebound alone is not Stage 11; only rebound while the same broken "
                "support remains unreclaimed is profiled"
            ),
            "neither support role is selected or claimed as proven key support inside this job",
            "2022-2024 arbitration is not reread and 2025 permanent-blind remains untouched",
        ],
    }
    payload = canonical(summary)
    (run_root / "summary.json").write_bytes(payload)
    manifest = {
        "schema_version": "quanthus.stage-second-group-repair-profile-artifact.v1",
        "run_id": run_id,
        "code_revision": code_revision,
        "profile_config_digest": digest(config),
        "candidate_digest": digest(candidate),
        "files": {"summary.json": _sha256(payload)},
        "development_only": True,
        "preserved_stage_codes": [5, 6, 7, 10, 11],
        "stage_5_6_merged": False,
        "stage_10_11_merged": False,
        "stage_labels_emitted": False,
        "parameter_search_performed": False,
        "arbitration_read": False,
        "permanent_blind_read": False,
        "holdout_population_selected": False,
    }
    manifest_bytes = canonical(manifest)
    (run_root / "manifest.json").write_bytes(manifest_bytes)
    ready = _sha256(manifest_bytes)
    (run_root / "READY").write_text(ready + "\n", encoding="ascii")
    return {**summary, "run_root": str(run_root), "manifest_sha256": ready}


def verify_second_group_repair_profile_artifact(
    path: Path, *, code_revision: str | None = None
) -> dict[str, Any]:
    root = path.expanduser().resolve()
    manifest = _object(root / "manifest.json", "repair profile manifest")
    if manifest.get("schema_version") != "quanthus.stage-second-group-repair-profile-artifact.v1":
        raise StageSecondGroupRepairProfileError("repair profile artifact schema differs")
    manifest_bytes = canonical(manifest)
    try:
        ready = (root / "READY").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise StageSecondGroupRepairProfileError("repair profile READY is unreadable") from exc
    if ready != _sha256(manifest_bytes):
        raise StageSecondGroupRepairProfileError("repair profile READY does not bind manifest")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise StageSecondGroupRepairProfileError("repair profile code revision differs")
    summary = _object(root / "summary.json", "repair profile summary")
    if _sha256((root / "summary.json").read_bytes()) != manifest.get("files", {}).get(
        "summary.json"
    ):
        raise StageSecondGroupRepairProfileError("repair profile summary digest differs")
    if (
        summary.get("development_only") is not True
        or summary.get("preserved_stage_codes") != [5, 6, 7, 10, 11]
        or summary.get("stage_5_6_merged") is not False
        or summary.get("stage_10_11_merged") is not False
        or summary.get("stage_labels_emitted") is not False
        or summary.get("parameter_search_performed") is not False
        or summary.get("arbitration_read") is not False
        or summary.get("permanent_blind_read") is not False
        or summary.get("holdout_population_selected") is not False
    ):
        raise StageSecondGroupRepairProfileError("repair profile authority differs")
    return {
        "run_id": manifest["run_id"],
        "manifest_sha256": ready,
        "file_count": 1,
        "verified": True,
        "development_only": True,
        "accepted_stage4_episode_count": summary["accepted_stage4_episode_count"],
        "repair_observation_count": summary["repair_observation_count"],
        "repair_cycle_count": summary["repair_cycle_count"],
        "support_observation_count": summary["support_observation_count"],
        "path_stop_counts": summary["path_stop_counts"],
        "scale_profiles": summary["scale_profiles"],
        "preserved_stage_codes": [5, 6, 7, 10, 11],
        "stage_5_6_merged": False,
        "stage_10_11_merged": False,
        "stage_labels_emitted": False,
        "arbitration_read": False,
        "permanent_blind_read": False,
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
        result = run_second_group_repair_profile(
            config_path=args.config,
            training_root=args.training_root,
            output_root=args.output_root,
            code_revision=args.code_revision,
        )
    else:
        result = verify_second_group_repair_profile_artifact(
            args.artifact,
            code_revision=args.code_revision,
        )
    print(canonical(result).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

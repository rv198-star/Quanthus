"""Development-only morphology profile for P3-STAGE-001-H second-group states.

The runner does not emit Stage 5/6/7/10/11 labels and performs no parameter search. It starts from
an already-frozen Stage-4 acceptance point, then measures causal path geometry and activity inside
2020-2021 development data so later threshold design can be based on observed distributions.
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
    ActivityObservation,
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

DEFAULT_CONFIG = "config/stage-learning/second-group-profile.v1.json"
_REVISION = re.compile(r"^[0-9a-f]{40}$")


class StageSecondGroupProfileError(ValueError):
    """Raised when the development-only second-group profile contract is violated."""


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StageSecondGroupProfileError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise StageSecondGroupProfileError(f"{label} must be a JSON object")
    return value


def _date(value: Mapping[str, Any], key: str) -> date:
    try:
        return date.fromisoformat(str(value[key]))
    except (KeyError, ValueError) as exc:
        raise StageSecondGroupProfileError(f"invalid profile date: {key}") from exc


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _progress(started: float, stage: str, **details: Any) -> None:
    print(
        json.dumps(
            {
                "event": "stage-second-group-profile-progress",
                "stage": stage,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                **details,
            },
            ensure_ascii=True,
            sort_keys=True,
        ),
        flush=True,
    )


def load_second_group_profile_config(path: Path) -> dict[str, Any]:
    raw = _object(path, "second-group profile config")
    expected = {
        "schema_version",
        "profile_id",
        "work_item",
        "purpose",
        "training_node_config",
        "experiment_config",
        "candidate_config",
        "candidate_digest",
        "authority",
        "development_window",
        "population",
        "entry_contract",
        "semantic_axes",
        "path_contract",
        "measurements",
        "artifact",
    }
    if (
        set(raw) != expected
        or raw.get("schema_version") != "quanthus.stage-second-group-profile.v1"
    ):
        raise StageSecondGroupProfileError("second-group profile config schema differs")
    if raw.get("work_item") != "P3-STAGE-001-H":
        raise StageSecondGroupProfileError("second-group profile work item differs")
    authority = raw.get("authority")
    if not isinstance(authority, Mapping):
        raise StageSecondGroupProfileError("second-group profile authority is missing")
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
        raise StageSecondGroupProfileError("second-group profile authority differs")

    window = raw.get("development_window")
    population = raw.get("population")
    path = raw.get("path_contract")
    measurements = raw.get("measurements")
    if not all(isinstance(item, Mapping) for item in (window, population, path, measurements)):
        raise StageSecondGroupProfileError("second-group profile sections are missing")
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
        raise StageSecondGroupProfileError("second-group profile must stay in development dates")
    if population.get("cohort_size") != 512 or population.get("anchor_scales") != [20, 60]:
        raise StageSecondGroupProfileError("second-group profile population differs")
    if path.get("maximum_path_sessions_including_first_stage_4") != 60:
        raise StageSecondGroupProfileError("second-group profile path horizon differs")
    quantiles = measurements.get("quantiles")
    if (
        not isinstance(quantiles, list)
        or not quantiles
        or any(not isinstance(item, (int, float)) or not 0 < float(item) < 1 for item in quantiles)
        or quantiles != sorted(set(quantiles))
        or measurements.get("quantile_method") != "nearest_rank"
        or measurements.get("no_stage_threshold_is_derived_inside_this_job") is not True
    ):
        raise StageSecondGroupProfileError("second-group profile quantile contract differs")
    return raw


def _candidate(root: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    candidate = _object(root / str(config["candidate_config"]), "development candidate")
    if candidate.get("schema_version") != "quanthus.stage-development-candidate.v1":
        raise StageSecondGroupProfileError("development candidate schema differs")
    if digest(candidate) != config.get("candidate_digest"):
        raise StageSecondGroupProfileError("development candidate digest differs")
    if candidate.get("status") != "frozen_for_external_evaluation":
        raise StageSecondGroupProfileError("development candidate is not frozen")
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
        raise StageSecondGroupProfileError("frozen candidate parameters are invalid") from exc


def _ratio(current: Decimal, prior: Decimal) -> Decimal:
    if prior <= 0:
        raise StageSecondGroupProfileError("profile ratio denominator must be positive")
    with localcontext(Context(prec=28)):
        return current / prior - Decimal(1)


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


@dataclass(frozen=True)
class EpisodeProfile:
    scale: int
    sessions_observed: int
    maximum_extension_from_acceptance: Decimal
    maximum_drawdown_from_running_peak: Decimal
    minimum_distance_to_support: Decimal
    sessions_to_support_break: int | None
    maximum_post_break_rebound: Decimal | None
    sessions_to_support_reclaim_after_break: int | None


@dataclass(frozen=True)
class ObservationProfile:
    scale: int
    return_from_acceptance: Decimal
    return_5: Decimal | None
    return_10: Decimal | None
    return_20: Decimal | None
    drawdown_from_running_peak: Decimal
    distance_to_breakout_anchor: Decimal
    distance_to_support: Decimal
    volume_ratio20: Decimal | None


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


def build_second_group_profiles(
    candidates: Sequence[StageCandidateObservation],
    structure: StructureV2Result,
    activity: Sequence[ActivityObservation],
    admissions: Sequence[Admission],
    *,
    maximum_sessions: int,
) -> tuple[tuple[EpisodeProfile, ...], tuple[ObservationProfile, ...], dict[str, int]]:
    """Measure paths from first confirmed Stage-4 observations without emitting stage labels."""
    if maximum_sessions < 2:
        raise StageSecondGroupProfileError("profile path horizon must be at least two sessions")
    starts = _first_confirmed_stage4_candidates(candidates)
    episode_by_key = {
        (item.security_code, item.scale, item.episode_id, item.market_date): item
        for item in structure.episodes
    }
    activity_by_key = {item.key: item for item in activity}
    rows_by_security: dict[str, list[Admission]] = defaultdict(list)
    for item in sorted(admissions, key=lambda row: (row.row.security_code, row.row.market_date)):
        rows_by_security[item.row.security_code].append(item)

    episode_profiles: list[EpisodeProfile] = []
    observation_profiles: list[ObservationProfile] = []
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
            raise StageSecondGroupProfileError("confirmed Stage-4 candidate lacks bound episode")
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
            raise StageSecondGroupProfileError("Stage-4 acceptance row is missing from admissions")
        acceptance = series[start_index]
        if not acceptance.admitted or acceptance.row.close is None:
            raise StageSecondGroupProfileError("Stage-4 acceptance row is not admitted")

        acceptance_close = acceptance.row.close
        listing_id = acceptance.row.listing_id
        coordinate_ref = acceptance.row.coordinate_ref
        running_peak = acceptance_close
        post_break_low: Decimal | None = None
        support_break_session: int | None = None
        support_reclaim_session: int | None = None
        maximum_extension = Decimal(0)
        maximum_drawdown = Decimal(0)
        minimum_support_distance = _ratio(acceptance_close, episode.frozen_low)
        maximum_post_break_rebound: Decimal | None = None
        valid_closes: list[Decimal] = []
        previous_close: Decimal | None = None
        sessions_observed = 0
        stopped_reason = "report_end_or_horizon"

        for item in series[start_index : start_index + maximum_sessions]:
            row = item.row
            if not item.admitted or row.close is None:
                stopped_reason = "nonadmitted"
                break
            if row.listing_id != listing_id or row.coordinate_ref != coordinate_ref:
                stopped_reason = "coordinate_or_listing_change"
                break
            close = row.close
            sessions_observed += 1
            running_peak = max(running_peak, close)
            maximum_extension = max(maximum_extension, _ratio(running_peak, acceptance_close))
            drawdown = _ratio(close, running_peak)
            maximum_drawdown = min(maximum_drawdown, drawdown)
            support_distance = _ratio(close, episode.frozen_low)
            minimum_support_distance = min(minimum_support_distance, support_distance)

            if (
                support_break_session is None
                and previous_close is not None
                and previous_close >= episode.frozen_low > close
            ):
                support_break_session = sessions_observed - 1
                post_break_low = close
            elif support_break_session is not None:
                post_break_low = close if post_break_low is None else min(post_break_low, close)
                rebound = _ratio(close, post_break_low)
                maximum_post_break_rebound = (
                    rebound
                    if maximum_post_break_rebound is None
                    else max(maximum_post_break_rebound, rebound)
                )
                if (
                    support_reclaim_session is None
                    and previous_close is not None
                    and previous_close <= episode.frozen_low < close
                ):
                    support_reclaim_session = sessions_observed - 1 - support_break_session

            valid_closes.append(close)
            return_5 = _ratio(close, valid_closes[-6]) if len(valid_closes) > 5 else None
            return_10 = _ratio(close, valid_closes[-11]) if len(valid_closes) > 10 else None
            return_20 = _ratio(close, valid_closes[-21]) if len(valid_closes) > 20 else None

            activity_item = activity_by_key.get((row.security_code, row.market_date))
            observation_profiles.append(
                ObservationProfile(
                    candidate.anchor_scale,
                    _ratio(close, acceptance_close),
                    return_5,
                    return_10,
                    return_20,
                    drawdown,
                    _ratio(close, episode.frozen_level),
                    support_distance,
                    (
                        activity_item.volume_ratio
                        if activity_item is not None
                        and activity_item.available
                        and activity_item.volume_ratio is not None
                        else None
                    ),
                )
            )
            previous_close = close

        stopped[stopped_reason] += 1
        episode_profiles.append(
            EpisodeProfile(
                candidate.anchor_scale,
                sessions_observed,
                maximum_extension,
                maximum_drawdown,
                minimum_support_distance,
                support_break_session,
                maximum_post_break_rebound,
                support_reclaim_session,
            )
        )

    return tuple(episode_profiles), tuple(observation_profiles), stopped


def _summarize_profiles(
    episodes: Sequence[EpisodeProfile],
    observations: Sequence[ObservationProfile],
    *,
    quantiles: Sequence[float],
) -> dict[str, Any]:
    def decimal_distribution(values: Sequence[Decimal]) -> dict[str, Any]:
        return {
            "count": len(values),
            "quantiles": _quantiles(values, quantiles),
        }

    def integer_distribution(values: Sequence[int]) -> dict[str, Any]:
        return {
            "count": len(values),
            "quantiles": _integer_quantiles(values, quantiles),
        }

    by_scale: dict[str, Any] = {}
    for scale in sorted({item.scale for item in episodes}):
        e = [item for item in episodes if item.scale == scale]
        o = [item for item in observations if item.scale == scale]
        support_breaks = [item for item in e if item.sessions_to_support_break is not None]
        support_reclaims = [
            item for item in e if item.sessions_to_support_reclaim_after_break is not None
        ]
        by_scale[str(scale)] = {
            "accepted_episode_count": len(e),
            "observation_count": len(o),
            "support_candidate_break_episode_count": len(support_breaks),
            "support_candidate_reclaim_episode_count": len(support_reclaims),
            "episode_metrics": {
                "maximum_extension_from_stage4_acceptance": decimal_distribution(
                    [item.maximum_extension_from_acceptance for item in e]
                ),
                "maximum_drawdown_from_running_peak": decimal_distribution(
                    [item.maximum_drawdown_from_running_peak for item in e]
                ),
                "minimum_distance_to_initial_low_support_candidate": decimal_distribution(
                    [item.minimum_distance_to_support for item in e]
                ),
                "sessions_to_first_support_candidate_break": integer_distribution(
                    [
                        item.sessions_to_support_break
                        for item in e
                        if item.sessions_to_support_break is not None
                    ]
                ),
                "maximum_post_break_rebound_from_running_low": decimal_distribution(
                    [
                        item.maximum_post_break_rebound
                        for item in e
                        if item.maximum_post_break_rebound is not None
                    ]
                ),
                "sessions_to_first_support_candidate_reclaim_after_break": integer_distribution(
                    [
                        item.sessions_to_support_reclaim_after_break
                        for item in e
                        if item.sessions_to_support_reclaim_after_break is not None
                    ]
                ),
                "sessions_observed": integer_distribution([item.sessions_observed for item in e]),
            },
            "observation_metrics": {
                "return_from_stage4_acceptance": decimal_distribution(
                    [item.return_from_acceptance for item in o]
                ),
                "return_5_sessions": decimal_distribution(
                    [item.return_5 for item in o if item.return_5 is not None]
                ),
                "return_10_sessions": decimal_distribution(
                    [item.return_10 for item in o if item.return_10 is not None]
                ),
                "return_20_sessions": decimal_distribution(
                    [item.return_20 for item in o if item.return_20 is not None]
                ),
                "drawdown_from_running_peak": decimal_distribution(
                    [item.drawdown_from_running_peak for item in o]
                ),
                "distance_to_breakout_anchor": decimal_distribution(
                    [item.distance_to_breakout_anchor for item in o]
                ),
                "distance_to_initial_low_support_candidate": decimal_distribution(
                    [item.distance_to_support for item in o]
                ),
                "volume_ratio20": decimal_distribution(
                    [item.volume_ratio20 for item in o if item.volume_ratio20 is not None]
                ),
            },
        }
    return by_scale


def run_second_group_profile(
    *,
    config_path: Path,
    training_root: Path,
    output_root: Path,
    code_revision: str,
) -> dict[str, Any]:
    if not _REVISION.fullmatch(code_revision):
        raise StageSecondGroupProfileError("second-group profile requires exact Git revision")
    started = time.perf_counter()
    root = find_project_root()
    config = load_second_group_profile_config(config_path)
    candidate = _candidate(root, config)
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
        raise StageSecondGroupProfileError("development population is smaller than frozen cohort")
    cohort = tuple(sorted(ranked[:cohort_size]))
    _progress(started, "cohort-ready", eligible_population=len(ranked), cohort_size=cohort_size)

    spec_digest = digest(
        {
            "schema_version": "quanthus.stage-second-group-profile-definition.v1",
            "profile_id": config["profile_id"],
            "candidate_digest": digest(candidate),
            "semantic_axes": config["semantic_axes"],
            "path_contract": config["path_contract"],
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
    episodes, observations, stopped = build_second_group_profiles(
        candidates,
        structure,
        activity,
        admissions,
        maximum_sessions=int(
            config["path_contract"]["maximum_path_sessions_including_first_stage_4"]
        ),
    )
    quantiles = tuple(float(item) for item in config["measurements"]["quantiles"])
    summary_by_scale = _summarize_profiles(episodes, observations, quantiles=quantiles)

    run_id = "stage-second-group-profile-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_root = output_root.expanduser().resolve() / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    summary = {
        "schema_version": "quanthus.stage-second-group-profile-summary.v1",
        "work_item": "P3-STAGE-001-H",
        "run_id": run_id,
        "code_revision": code_revision,
        "profile_id": config["profile_id"],
        "profile_config_digest": digest(config),
        "candidate_digest": digest(candidate),
        "development_only": True,
        "development_window": {
            "report_start": report_start,
            "report_end": report_end,
        },
        "eligible_population_count": len(ranked),
        "cohort_size": cohort_size,
        "cohort_digest": digest(cohort),
        "accepted_stage4_episode_count": len(episodes),
        "path_observation_count": len(observations),
        "path_stop_counts": stopped,
        "scale_profiles": summary_by_scale,
        "stage_labels_emitted": False,
        "parameter_search_performed": False,
        "synthetic_panel_performed": False,
        "future_settlement_read": False,
        "arbitration_read": False,
        "permanent_blind_read": False,
        "holdout_population_selected": False,
        "strategy_result_read": False,
        "elapsed_seconds": time.perf_counter() - started,
        "claim_limits": [
            "Stage 5/6/7/10/11 are not emitted by this profile",
            "the pre-T frozen low is a support candidate, not proven key support",
            "daily path rows are correlated observations, not independent semantic samples",
            (
                "the profile may inform a later finite development search but does not "
                "choose thresholds"
            ),
            "2022-2024 arbitration is not reread and 2025 permanent-blind remains untouched",
        ],
        "source_manifest_digest": digest(source_manifest),
    }
    payload = canonical(summary)
    (run_root / "summary.json").write_bytes(payload)
    manifest = {
        "schema_version": "quanthus.stage-second-group-profile-artifact.v1",
        "run_id": run_id,
        "code_revision": code_revision,
        "profile_config_digest": digest(config),
        "candidate_digest": digest(candidate),
        "files": {"summary.json": _sha256(payload)},
        "development_only": True,
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


def verify_second_group_profile_artifact(
    path: Path, *, code_revision: str | None = None
) -> dict[str, Any]:
    root = path.expanduser().resolve()
    manifest = _object(root / "manifest.json", "second-group profile manifest")
    if manifest.get("schema_version") != "quanthus.stage-second-group-profile-artifact.v1":
        raise StageSecondGroupProfileError("second-group profile artifact schema differs")
    manifest_bytes = canonical(manifest)
    try:
        ready = (root / "READY").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise StageSecondGroupProfileError("second-group profile READY is unreadable") from exc
    if ready != _sha256(manifest_bytes):
        raise StageSecondGroupProfileError("second-group profile READY does not bind manifest")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise StageSecondGroupProfileError("second-group profile code revision differs")
    summary = _object(root / "summary.json", "second-group profile summary")
    if _sha256((root / "summary.json").read_bytes()) != manifest.get("files", {}).get(
        "summary.json"
    ):
        raise StageSecondGroupProfileError("second-group profile summary digest differs")
    if (
        summary.get("development_only") is not True
        or summary.get("stage_labels_emitted") is not False
        or summary.get("parameter_search_performed") is not False
        or summary.get("arbitration_read") is not False
        or summary.get("permanent_blind_read") is not False
        or summary.get("holdout_population_selected") is not False
    ):
        raise StageSecondGroupProfileError("second-group profile authority differs")
    return {
        "run_id": manifest["run_id"],
        "manifest_sha256": ready,
        "file_count": 1,
        "verified": True,
        "development_only": True,
        "stage_labels_emitted": False,
        "arbitration_read": False,
        "permanent_blind_read": False,
        "accepted_stage4_episode_count": summary["accepted_stage4_episode_count"],
        "path_observation_count": summary["path_observation_count"],
        "path_stop_counts": summary["path_stop_counts"],
        "scale_profiles": summary["scale_profiles"],
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
        result = run_second_group_profile(
            config_path=args.config,
            training_root=args.training_root,
            output_root=args.output_root,
            code_revision=args.code_revision,
        )
    else:
        result = verify_second_group_profile_artifact(
            args.artifact,
            code_revision=args.code_revision,
        )
    print(canonical(result).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

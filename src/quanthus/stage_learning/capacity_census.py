"""Development-only structural sample-capacity census for successor holdout sizing.

This runner uses only 2019 warm-up / 2020-2021 development data. It never runs the
synthetic semantic panel, candidate/R0 prediction, future settlement, strategy results,
or any holdout read. One maximum deterministic cohort is built once; smaller nested
cohorts are projected from the same Structure/Activity result.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.stage_learning.core import ActivityObservation, build_activity_observations
from quanthus.stage_learning.harness import load_harness_config
from quanthus.stage_learning.source import (
    DevelopmentReadScope,
    discover_development_security_population,
    read_development_admissions,
)
from quanthus.structure_v2 import EpisodeObservation, build_structure_v2
from quanthus.structure_v2_inputs import canonical, digest

DEFAULT_CONFIG = "config/stage-learning/sample-capacity-census.v1.json"
_HARNESS_CONFIG = "config/stage-learning/harness.v1.json"
_REVISION = re.compile(r"^[0-9a-f]{40}$")


class StageCapacityCensusError(ValueError):
    """Raised when the development-only census contract cannot be satisfied."""


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StageCapacityCensusError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise StageCapacityCensusError(f"{label} must be a JSON object")
    return value


def _date(value: Mapping[str, Any], key: str) -> date:
    try:
        return date.fromisoformat(str(value[key]))
    except (KeyError, ValueError) as exc:
        raise StageCapacityCensusError(f"invalid census date: {key}") from exc


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _progress(started: float, stage: str, **details: Any) -> None:
    print(
        json.dumps(
            {
                "event": "stage-capacity-census-progress",
                "stage": stage,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                **details,
            },
            ensure_ascii=True,
            sort_keys=True,
        ),
        flush=True,
    )


def load_capacity_census_config(path: Path) -> dict[str, Any]:
    raw = _object(path, "stage sample capacity census config")
    expected = {
        "schema_version",
        "census_id",
        "work_item",
        "purpose",
        "training_node_config",
        "experiment_config",
        "qualification_protocol",
        "authority",
        "development_window",
        "nested_population_sizes",
        "maximum_runtime_population",
        "analysis",
        "artifact",
    }
    if (
        set(raw) != expected
        or raw.get("schema_version") != "quanthus.stage-sample-capacity-census.v1"
    ):
        raise StageCapacityCensusError("capacity census config schema differs")
    if raw.get("work_item") != "P3-STAGE-001-J":
        raise StageCapacityCensusError("capacity census work item differs")
    authority = raw.get("authority")
    if not isinstance(authority, Mapping):
        raise StageCapacityCensusError("capacity census authority is missing")
    required_true = {
        "development_only",
        "runtime_implementation_authorized",
        "development_data_read_authorized",
    }
    required_false = {
        "arbitration_access_authorized",
        "permanent_blind_access_authorized",
        "candidate_prediction_authorized",
        "semantic_panel_authorized",
        "future_settlement_access_authorized",
        "strategy_result_access_authorized",
        "parameter_search_authorized",
        "holdout_population_selection_authorized",
        "holdout_submit_authorized",
    }
    if any(authority.get(key) is not True for key in required_true) or any(
        authority.get(key) is not False for key in required_false
    ):
        raise StageCapacityCensusError("capacity census authority differs")
    sizes = raw.get("nested_population_sizes")
    maximum = raw.get("maximum_runtime_population")
    if (
        not isinstance(sizes, list)
        or not sizes
        or any(not isinstance(item, int) or isinstance(item, bool) or item < 1 for item in sizes)
        or sizes != sorted(set(sizes))
        or maximum != sizes[-1]
        or maximum > 512
    ):
        raise StageCapacityCensusError("capacity census population sizes differ")
    window = raw.get("development_window")
    if not isinstance(window, Mapping):
        raise StageCapacityCensusError("capacity census development window is missing")
    cohort_scan_start = _date(window, "cohort_scan_start")
    cohort_date = _date(window, "cohort_date")
    warmup_start = _date(window, "warmup_start")
    report_start = _date(window, "report_start")
    report_end = _date(window, "report_end")
    if not (
        date(2012, 1, 1)
        <= cohort_scan_start
        <= warmup_start
        <= cohort_date
        < report_start
        <= report_end
        <= date(2021, 12, 31)
    ):
        raise StageCapacityCensusError("capacity census must stay inside development dates")
    return raw


def _fact_spec(root: Path, experiment: Mapping[str, Any], config: Mapping[str, Any]) -> str:
    harness = load_harness_config(root / _HARNESS_CONFIG)
    return digest(
        {
            "schema_version": "quanthus.stage-capacity-census-definition.v1",
            "experiment_id": experiment.get("experiment_id"),
            "first_group": experiment.get("first_group"),
            "facts": harness.get("facts"),
            "development_window": config["development_window"],
            "candidate_prediction": False,
            "semantic_review": False,
            "official_label": False,
        }
    )


def _rank(seed: str, episode: EpisodeObservation) -> str:
    return hashlib.sha256(
        f"{seed}|{episode.episode_id}|{episode.market_date.isoformat()}|{episode.scale}".encode()
    ).hexdigest()


def _take_up_to_unique(
    rows: Sequence[EpisodeObservation],
    *,
    cap: int,
    seed: str,
    used: set[str],
    distance_first: bool = False,
) -> tuple[tuple[EpisodeObservation, ...], int]:
    raw_unique = len({item.episode_id for item in rows})

    def order(item: EpisodeObservation) -> tuple[Any, ...]:
        distance = Decimal(0)
        if distance_first:
            if item.close is None or item.frozen_level <= 0:
                distance = Decimal("999")
            else:
                distance = abs(item.close / item.frozen_level - Decimal(1))
        return (distance, _rank(seed, item), item.episode_id, item.market_date)

    selected: list[EpisodeObservation] = []
    local: set[str] = set()
    for item in sorted(rows, key=order):
        if item.episode_id in used or item.episode_id in local:
            continue
        selected.append(item)
        local.add(item.episode_id)
        if len(selected) == cap:
            break
    used.update(local)
    return tuple(selected), raw_unique


def _capacity_for_population(
    episodes: Sequence[EpisodeObservation],
    activity: Sequence[ActivityObservation],
    *,
    security_codes: set[str],
    report_start: date,
    report_end: date,
    sampling: Mapping[str, Any],
    seed: str,
) -> dict[str, Any]:
    eligible = [
        item
        for item in episodes
        if item.security_code in security_codes and report_start <= item.market_date <= report_end
    ]
    activity_by_key = {
        item.key: item for item in activity if item.security_code in security_codes
    }
    target_caps = sampling["target_caps"]
    started_caps = sampling["started_activity_target_caps"]
    held_caps = sampling["early_held_target_caps"]
    used: set[str] = set()

    started_buckets: dict[str, list[EpisodeObservation]] = {
        "missing_or_lt_1": [],
        "1_to_1_25": [],
        "1_25_to_1_5": [],
        "gte_1_5": [],
    }
    for episode in eligible:
        if episode.observation != "started":
            continue
        item = activity_by_key.get((episode.security_code, episode.market_date))
        ratio = item.volume_ratio if item and item.available else None
        if ratio is None or ratio < Decimal("1.0"):
            bucket = "missing_or_lt_1"
        elif ratio < Decimal("1.25"):
            bucket = "1_to_1_25"
        elif ratio < Decimal("1.5"):
            bucket = "1_25_to_1_5"
        else:
            bucket = "gte_1_5"
        started_buckets[bucket].append(episode)

    realized_started: dict[str, int] = {}
    raw_started: dict[str, int] = {}
    for bucket in ("missing_or_lt_1", "1_to_1_25", "1_25_to_1_5", "gte_1_5"):
        selected, raw = _take_up_to_unique(
            started_buckets[bucket],
            cap=int(started_caps[bucket]),
            seed=f"{seed}|started|{bucket}",
            used=used,
        )
        realized_started[bucket] = len(selected)
        raw_started[bucket] = raw

    realized_held: dict[str, int] = {}
    raw_held: dict[str, int] = {}
    for hold in ("1", "2", "3"):
        rows = [
            item
            for item in eligible
            if item.observation == "held"
            and item.phase == "active"
            and item.hold_count == int(hold)
        ]
        selected, raw = _take_up_to_unique(
            rows,
            cap=int(held_caps[hold]),
            seed=f"{seed}|held|{hold}",
            used=used,
        )
        realized_held[hold] = len(selected)
        raw_held[hold] = raw

    top_rows = {
        "near_anchor_loss_negative": [
            item for item in eligible if item.observation in {"lost", "below"}
        ],
        "reclaimed_ambiguity": [item for item in eligible if item.observation == "reclaimed"],
        "mature_held": [
            item
            for item in eligible
            if item.observation == "held" and item.phase == "active" and item.hold_count >= 40
        ],
    }
    realized_top: dict[str, int] = {
        "started_activity": sum(realized_started.values()),
        "early_held": sum(realized_held.values()),
    }
    raw_top: dict[str, int] = {
        "started_activity": len(
            {item.episode_id for rows in started_buckets.values() for item in rows}
        ),
        "early_held": len(
            {
                item.episode_id
                for item in eligible
                if item.observation == "held"
                and item.phase == "active"
                and item.hold_count in {1, 2, 3}
            }
        ),
    }
    for name in ("near_anchor_loss_negative", "reclaimed_ambiguity", "mature_held"):
        selected, raw = _take_up_to_unique(
            top_rows[name],
            cap=int(target_caps[name]),
            seed=f"{seed}|{name}",
            used=used,
            distance_first=name == "near_anchor_loss_negative",
        )
        realized_top[name] = len(selected)
        raw_top[name] = raw

    shortfalls = {
        name: int(target_caps[name]) - realized_top[name]
        for name in target_caps
        if realized_top[name] < int(target_caps[name])
    }
    sub_shortfalls = {
        **{
            f"started_activity.{name}": int(started_caps[name]) - realized_started[name]
            for name in started_caps
            if realized_started[name] < int(started_caps[name])
        },
        **{
            f"early_held.{name}": int(held_caps[name]) - realized_held[name]
            for name in held_caps
            if realized_held[name] < int(held_caps[name])
        },
    }
    return {
        "population_size": len(security_codes),
        "eligible_episode_observation_count": len(eligible),
        "raw_unique_episode_capacity": {
            **raw_top,
            "started_activity_buckets": raw_started,
            "early_held_buckets": raw_held,
        },
        "realized_after_protocol_order": {
            **realized_top,
            "started_activity_buckets": realized_started,
            "early_held_buckets": realized_held,
        },
        "target_cap_shortfalls": shortfalls,
        "sub_stratum_shortfalls": sub_shortfalls,
        "all_target_caps_met": not shortfalls and not sub_shortfalls,
        "selected_unique_episode_count": len(used),
    }


def run_capacity_census(
    *,
    config_path: Path,
    training_root: Path,
    output_root: Path,
    code_revision: str,
) -> dict[str, Any]:
    if not _REVISION.fullmatch(code_revision):
        raise StageCapacityCensusError("capacity census requires exact Git revision")
    started = time.perf_counter()
    root = find_project_root()
    config = load_capacity_census_config(config_path)
    node_config = _object(root / str(config["training_node_config"]), "training node")
    experiment = _object(root / str(config["experiment_config"]), "stage experiment")
    protocol = _object(root / str(config["qualification_protocol"]), "qualification protocol")
    if (
        protocol.get("schema_version") != "quanthus.stage-qualification-protocol.v2"
        or protocol.get("authority", {}).get("holdout_access_authorized") is not False
        or protocol.get("sampling", {}).get("exact_stratum_quota_required") is not False
    ):
        raise StageCapacityCensusError("capacity census protocol binding differs")

    window = config["development_window"]
    cohort_scan_start = _date(window, "cohort_scan_start")
    cohort_date = _date(window, "cohort_date")
    warmup_start = _date(window, "warmup_start")
    report_start = _date(window, "report_start")
    report_end = _date(window, "report_end")
    seed = str(window["cohort_seed"])
    ranked_population = discover_development_security_population(
        training_root=training_root,
        node_config=node_config,
        scan_start=cohort_scan_start,
        cohort_date=cohort_date,
        minimum_observations=int(window["minimum_pre_cohort_observations"]),
        seed=seed,
    )
    maximum = int(config["maximum_runtime_population"])
    if len(ranked_population) < maximum:
        raise StageCapacityCensusError(
            "development population has only "
            f"{len(ranked_population)} eligible securities; need {maximum}"
        )
    cohort = tuple(sorted(ranked_population[:maximum]))
    _progress(
        started,
        "cohort-ready",
        eligible_population=len(ranked_population),
        runtime_population=maximum,
    )

    spec_digest = _fact_spec(root, experiment, config)
    admissions, source_manifest, policy = read_development_admissions(
        training_root=training_root,
        node_config=node_config,
        scope=DevelopmentReadScope(warmup_start, report_start, report_end),
        security_codes=cohort,
        spec_digest=spec_digest,
    )
    _progress(started, "source-ready", admissions=len(admissions))
    structure = build_structure_v2(
        admissions,
        spec_digest=spec_digest,
        calendar_ref=policy.calendar_ref,
        lookbacks=(20, 60),
    )
    _progress(started, "structure-ready", episode_observations=len(structure.episodes))
    activity = build_activity_observations(admissions, window=20)
    _progress(started, "activity-ready", activity_observations=len(activity))

    curves: list[dict[str, Any]] = []
    sampling = protocol["sampling"]
    sampling_seed = digest({"census_id": config["census_id"], "protocol": protocol["protocol_id"]})
    for size in config["nested_population_sizes"]:
        codes = set(ranked_population[: int(size)])
        curves.append(
            _capacity_for_population(
                structure.episodes,
                activity,
                security_codes=codes,
                report_start=report_start,
                report_end=report_end,
                sampling=sampling,
                seed=sampling_seed,
            )
        )
    first = next((item["population_size"] for item in curves if item["all_target_caps_met"]), None)
    summary = {
        "schema_version": "quanthus.stage-sample-capacity-census-summary.v1",
        "census_id": config["census_id"],
        "work_item": "P3-STAGE-001-J",
        "code_revision": code_revision,
        "development_only": True,
        "development_window": {
            "cohort_date": cohort_date,
            "report_start": report_start,
            "report_end": report_end,
        },
        "eligible_population_count": len(ranked_population),
        "runtime_population_size": maximum,
        "runtime_population_digest": digest(cohort),
        "source_manifest_digest": digest(source_manifest),
        "protocol_id": protocol["protocol_id"],
        "protocol_digest": digest(protocol),
        "capacity_curve": curves,
        "first_population_meeting_all_target_caps": first,
        "requires_larger_census": first is None,
        "candidate_prediction_performed": False,
        "semantic_panel_performed": False,
        "future_settlement_read": False,
        "strategy_result_read": False,
        "arbitration_read": False,
        "permanent_blind_read": False,
        "holdout_population_selected": False,
        "elapsed_seconds": time.perf_counter() - started,
        "claim_limits": [
            "capacity counts are development-only design evidence, not qualification evidence",
            "2022-2024 arbitration is not reread",
            "2025 permanent-blind remains untouched and unselected",
            (
                "the census may size a future cohort but cannot authorize or choose "
                "a holdout partition"
            ),
        ],
    }
    run_id = "stage-capacity-census-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_root = output_root.expanduser().resolve() / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    payload = canonical(summary)
    (run_root / "summary.json").write_bytes(payload)
    manifest = {
        "schema_version": "quanthus.stage-sample-capacity-census-artifact.v1",
        "run_id": run_id,
        "code_revision": code_revision,
        "files": {"summary.json": _sha256(payload)},
        "development_only": True,
        "arbitration_read": False,
        "permanent_blind_read": False,
        "holdout_population_selected": False,
    }
    manifest_bytes = canonical(manifest)
    (run_root / "manifest.json").write_bytes(manifest_bytes)
    ready = _sha256(manifest_bytes)
    (run_root / "READY").write_text(ready + "\n", encoding="ascii")
    return {**summary, "run_id": run_id, "run_root": str(run_root), "manifest_sha256": ready}


def verify_capacity_census_artifact(
    path: Path, *, code_revision: str | None = None
) -> dict[str, Any]:
    root = path.expanduser().resolve()
    manifest = _object(root / "manifest.json", "capacity census manifest")
    if manifest.get("schema_version") != "quanthus.stage-sample-capacity-census-artifact.v1":
        raise StageCapacityCensusError("capacity census artifact schema differs")
    manifest_bytes = canonical(manifest)
    try:
        ready = (root / "READY").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise StageCapacityCensusError("capacity census READY is unreadable") from exc
    if ready != _sha256(manifest_bytes):
        raise StageCapacityCensusError("capacity census READY does not bind manifest")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise StageCapacityCensusError("capacity census code revision differs")
    summary = _object(root / "summary.json", "capacity census summary")
    if (
        summary.get("development_only") is not True
        or summary.get("arbitration_read") is not False
        or summary.get("permanent_blind_read") is not False
        or summary.get("candidate_prediction_performed") is not False
        or summary.get("semantic_panel_performed") is not False
        or summary.get("holdout_population_selected") is not False
    ):
        raise StageCapacityCensusError("capacity census authority differs")
    if _sha256((root / "summary.json").read_bytes()) != manifest.get("files", {}).get(
        "summary.json"
    ):
        raise StageCapacityCensusError("capacity census summary digest differs")
    return {
        "run_id": manifest["run_id"],
        "manifest_sha256": ready,
        "file_count": 1,
        "verified": True,
        "development_only": True,
        "arbitration_read": False,
        "permanent_blind_read": False,
        "eligible_population_count": summary["eligible_population_count"],
        "runtime_population_size": summary["runtime_population_size"],
        "capacity_curve": summary["capacity_curve"],
        "first_population_meeting_all_target_caps": summary[
            "first_population_meeting_all_target_caps"
        ],
        "requires_larger_census": summary["requires_larger_census"],
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
        result = run_capacity_census(
            config_path=args.config,
            training_root=args.training_root,
            output_root=args.output_root,
            code_revision=args.code_revision,
        )
    else:
        result = verify_capacity_census_artifact(args.artifact, code_revision=args.code_revision)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

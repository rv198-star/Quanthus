"""Result-aware development reference supplement for the second stock-stage group.

This runner is intentionally narrower than ``second_group_development``.  It verifies and binds the
prior immutable development artifact, preserves the selected Stage-10 candidate, preserves the
attributable Stage-7 INCONCLUSIVE result without widening its grid, and supplements only the Stage-6
and Stage-11 T-only reference samples before rerunning the already-frozen Stage-5/6 and Stage-11
searches.  It never reads a holdout or future outcome and cannot publish an official label.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.stage_learning.core import build_activity_observations, build_stage34_decisions
from quanthus.stage_learning.second_group_development import (
    SampledContext,
    SecondGroupObservation,
    _bucket,
    _candidate_params_first_group,
    _first_confirmed_stage4_candidates,
    _search_5_6,
    _search_single_stage,
    build_second_group_observations,
    load_second_group_development_config,
    load_second_group_panel,
    resolve_stage_references,
    sample_stage_contexts,
)
from quanthus.stage_learning.source import (
    DevelopmentReadScope,
    discover_development_security_population,
    read_development_admissions,
)
from quanthus.structure_v2 import build_structure_v2
from quanthus.structure_v2_inputs import canonical, digest

DEFAULT_CONFIG = "config/stage-learning/second-group-development-supplement.v1.json"
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_STAGE_JOB = re.compile(r"^stage_[0-9a-f]{32}$")
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")


class StageSecondGroupSupplementError(ValueError):
    """Raised when the bounded development supplement contract is violated."""


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StageSecondGroupSupplementError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise StageSecondGroupSupplementError(f"{label} must be a JSON object")
    return value


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _date(value: Mapping[str, Any], key: str) -> date:
    try:
        return date.fromisoformat(str(value[key]))
    except (KeyError, ValueError) as exc:
        raise StageSecondGroupSupplementError(f"invalid supplement date: {key}") from exc


def _progress(started: float, stage: str, **details: Any) -> None:
    print(
        json.dumps(
            {
                "event": "stage-second-group-development-supplement-progress",
                "stage": stage,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                **details,
            },
            ensure_ascii=True,
            sort_keys=True,
        ),
        flush=True,
    )


def load_second_group_supplement_config(path: Path) -> dict[str, Any]:
    raw = _object(path, "second-group development supplement config")
    expected = {
        "schema_version",
        "supplement_id",
        "work_item",
        "purpose",
        "training_node_config",
        "base_development_config",
        "stage_5_6_experiment_config",
        "stage_7_10_11_experiment_config",
        "synthetic_panel_config",
        "prior_run",
        "authority",
        "development_window",
        "population",
        "state_identity",
        "supplement_sampling",
        "search_contract",
        "milestone_decision",
        "artifact",
        "claim_limits",
    }
    if (
        set(raw) != expected
        or raw.get("schema_version")
        != "quanthus.stage-second-group-development-supplement.v1"
        or raw.get("work_item") != "P3-STAGE-001-H"
    ):
        raise StageSecondGroupSupplementError("supplement config identity differs")
    authority = raw.get("authority")
    if not isinstance(authority, Mapping):
        raise StageSecondGroupSupplementError("supplement authority is missing")
    required_true = {
        "development_only",
        "runtime_implementation_authorized",
        "development_data_read_authorized",
        "prior_development_artifact_read_authorized",
        "synthetic_reference_supplement_authorized",
        "limited_parameter_search_runtime_authorized",
        "development_candidate_artifact_authorized",
    }
    required_false = set(authority) - required_true
    if any(authority.get(key) is not True for key in required_true) or any(
        authority.get(key) is not False for key in required_false
    ):
        raise StageSecondGroupSupplementError("supplement authority differs")
    prior = raw.get("prior_run")
    if not isinstance(prior, Mapping) or (
        not _STAGE_JOB.fullmatch(str(prior.get("job_id") or ""))
        or not str(prior.get("run_id") or "").startswith("stage-second-group-development-")
        or not _SHA.fullmatch(str(prior.get("manifest_sha256") or ""))
        or not _SHA.fullmatch(str(prior.get("candidate_artifact_digest") or ""))
        or prior.get("required_disposition") != "INCONCLUSIVE"
        or prior.get("stage_7_required_state") != "INCONCLUSIVE"
        or prior.get("frozen_stage_10")
        != {"unreclaimed_persistence_sessions": 5, "failure_depth_abs_min": "0.02"}
    ):
        raise StageSecondGroupSupplementError("prior development binding differs")
    shortfalls = prior.get("observed_reference_shortfalls")
    if not isinstance(shortfalls, Mapping) or (
        shortfalls.get("stage_6_ambiguous_references") != 9
        or shortfalls.get("stage_11_ambiguous_references") != 7
        or shortfalls.get("hard_gate_minimum_ambiguous_references") != 10
    ):
        raise StageSecondGroupSupplementError("prior reference-shortfall attribution differs")
    identity = raw.get("state_identity")
    if not isinstance(identity, Mapping) or (
        identity.get("preserve_distinct_stage_codes") != [5, 6, 7, 10, 11]
        or identity.get("stage_5_6_merge_authorized") is not False
        or identity.get("stage_10_11_merge_authorized") is not False
    ):
        raise StageSecondGroupSupplementError("supplement stage identity differs")
    window = raw.get("development_window")
    population = raw.get("population")
    sampling = raw.get("supplement_sampling")
    search = raw.get("search_contract")
    decision = raw.get("milestone_decision")
    if not all(
        isinstance(item, Mapping)
        for item in (window, population, sampling, search, decision)
    ):
        raise StageSecondGroupSupplementError("supplement sections are missing")
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
        raise StageSecondGroupSupplementError("supplement must stay inside development dates")
    if (
        population.get("cohort_size") != 512
        or population.get("anchor_scales") != [20, 60]
        or population.get("maximum_path_sessions_including_first_stage_4") != 60
        or population.get("shared_structure_build_required") is not True
    ):
        raise StageSecondGroupSupplementError("supplement population contract differs")
    if (
        sampling.get("supplement_stages") != [6, 11]
        or sampling.get("additional_target_contexts_per_bucket") != 20
        or sampling.get("exact_additional_bucket_quota_required") is not False
        or sampling.get("exclude_base_sample_episode_ids") is not True
        or sampling.get("one_context_per_episode_per_stage") is not True
        or sampling.get("minimum_merged_ambiguous_references_per_stage") != 10
    ):
        raise StageSecondGroupSupplementError("supplement sampling contract differs")
    if (
        search.get("hard_gate_values_must_not_change") is not True
        or search.get("full_cartesian_cross_stage_search_forbidden") is not True
        or search.get("stage_10") != "reuse_prior_frozen_candidate_without_research"
        or search.get("stage_7")
        != "retain_prior_inconclusive_without_grid_expansion_or_rerun"
    ):
        raise StageSecondGroupSupplementError("supplement search contract differs")
    if (
        decision.get("stage_7_inconclusive_is_not_a_failure_to_execute") is not True
        or decision.get("expert_review_required_after_complete_disposition") is not True
    ):
        raise StageSecondGroupSupplementError("supplement milestone decision differs")
    return raw


def _verify_prior_artifact(
    training_root: Path, config: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    prior = config["prior_run"]
    root = (
        training_root.expanduser().resolve()
        / "runs"
        / "stage-worker"
        / str(prior["job_id"])
        / str(prior["run_id"])
    ).resolve()
    training = training_root.expanduser().resolve()
    try:
        root.relative_to(training)
    except ValueError as exc:
        raise StageSecondGroupSupplementError("prior artifact escaped training root") from exc
    manifest = _object(root / "manifest.json", "prior development manifest")
    ready = (root / "READY").read_text(encoding="ascii").strip()
    if ready != prior["manifest_sha256"] or ready != _sha256(canonical(manifest)):
        raise StageSecondGroupSupplementError("prior development manifest binding differs")
    files = manifest.get("files")
    if not isinstance(files, Mapping) or set(files) != {"candidate.json", "summary.json"}:
        raise StageSecondGroupSupplementError("prior development file inventory differs")
    for name, expected in files.items():
        if _sha256((root / name).read_bytes()) != expected:
            raise StageSecondGroupSupplementError(f"prior artifact digest differs: {name}")
    if files["candidate.json"] != prior["candidate_artifact_digest"]:
        raise StageSecondGroupSupplementError("prior candidate digest differs")
    summary = _object(root / "summary.json", "prior development summary")
    candidate = _object(root / "candidate.json", "prior development candidate")
    if (
        summary.get("disposition") != prior["required_disposition"]
        or summary.get("development_only") is not True
        or summary.get("future_outcome_read") is not False
        or summary.get("arbitration_read") is not False
        or summary.get("permanent_blind_read") is not False
        or candidate.get("selected_parameters", {}).get("stage_10")
        != prior["frozen_stage_10"]
        or candidate.get("selected_parameters", {}).get("stage_7") is not None
    ):
        raise StageSecondGroupSupplementError("prior development disposition differs")
    ref = summary.get("reference_summary", {})
    stage6_ambiguous = ref.get("6", {}).get(
        "ambiguous_count",
        ref.get("6", {}).get("reference_kind_counts", {}).get("ambiguous_set"),
    )
    stage11_ambiguous = ref.get("11", {}).get(
        "ambiguous_count",
        ref.get("11", {}).get("reference_kind_counts", {}).get("ambiguous_set"),
    )
    if (
        stage6_ambiguous
        != prior["observed_reference_shortfalls"]["stage_6_ambiguous_references"]
        or stage11_ambiguous
        != prior["observed_reference_shortfalls"]["stage_11_ambiguous_references"]
    ):
        raise StageSecondGroupSupplementError("prior reference-summary shortfall differs")
    return summary, candidate, root


def sample_stage_supplement_contexts(
    observations: Sequence[SecondGroupObservation],
    *,
    stage: int,
    base_contexts: Sequence[SampledContext],
    seed: str,
    per_bucket: int,
) -> tuple[SampledContext, ...]:
    """Select deterministic T-only supplements while excluding every base-sample episode."""
    if stage not in {6, 11} or per_bucket < 1:
        raise StageSecondGroupSupplementError("unsupported supplement sampling request")
    used_episodes = {item.observation.episode_id for item in base_contexts}
    buckets: dict[str, list[SecondGroupObservation]] = defaultdict(list)
    for item in observations:
        if item.episode_id not in used_episodes:
            buckets[_bucket(stage, item)].append(item)
    selected: list[SampledContext] = []
    local_episodes: set[str] = set()
    for bucket_name in sorted(buckets):
        ordered = sorted(
            buckets[bucket_name],
            key=lambda item: (
                hashlib.sha256(
                    f"{seed}|{stage}|{bucket_name}|{item.context_id(stage)}".encode()
                ).hexdigest(),
                item.context_id(stage),
            ),
        )
        count = 0
        for item in ordered:
            if item.episode_id in local_episodes:
                continue
            context_id = item.context_id(stage)
            evidence = digest(
                {"stage": stage, "context_id": context_id, "features": item.feature_dict()}
            )
            selected.append(SampledContext(stage, context_id, bucket_name, item, evidence))
            local_episodes.add(item.episode_id)
            count += 1
            if count == per_bucket:
                break
    if {item.context_id for item in base_contexts} & {item.context_id for item in selected}:
        raise StageSecondGroupSupplementError("supplement reused a base context")
    if used_episodes & {item.observation.episode_id for item in selected}:
        raise StageSecondGroupSupplementError("supplement reused a base episode")
    return tuple(sorted(selected, key=lambda item: (item.bucket, item.context_id)))


def _reference_summary(
    contexts: Sequence[SampledContext], references: Sequence[Any]
) -> dict[str, Any]:
    buckets = Counter(item.bucket for item in contexts)
    kinds = Counter(str(item.kind) for item in references)
    definite_positive = sum(item.kind == "definite" and item.supported for item in references)
    definite_negative = sum(item.kind == "definite" and not item.supported for item in references)
    return {
        "context_count": len(contexts),
        "bucket_counts": dict(sorted(buckets.items())),
        "reference_kind_counts": dict(sorted(kinds.items())),
        "definite_positive_count": definite_positive,
        "definite_negative_count": definite_negative,
        "ambiguous_count": kinds.get("ambiguous_set", 0),
        "unjudgeable_count": kinds.get("unjudgeable", 0),
    }


def _stage7_prior_analysis(prior_summary: Mapping[str, Any]) -> dict[str, Any]:
    raw = prior_summary.get("stage_7")
    if not isinstance(raw, Mapping) or raw.get("selected") is not None:
        raise StageSecondGroupSupplementError("prior Stage 7 is not attributable INCONCLUSIVE")
    trials = raw.get("trials")
    if not isinstance(trials, list) or not trials:
        raise StageSecondGroupSupplementError("prior Stage 7 trial ledger is missing")
    reasons: Counter[str] = Counter()
    ranked: list[dict[str, Any]] = []
    for item in trials:
        if not isinstance(item, Mapping):
            raise StageSecondGroupSupplementError("prior Stage 7 trial record is malformed")
        gate = item.get("gate")
        score = item.get("score")
        if not isinstance(gate, Mapping) or not isinstance(score, Mapping):
            raise StageSecondGroupSupplementError("prior Stage 7 trial gate/score is missing")
        for reason in gate.get("reason_codes", []):
            reasons[str(reason)] += 1
        ranked.append(
            {
                "trial_id": item.get("trial_id"),
                "params": item.get("params"),
                "f1": score.get("f1"),
                "precision_lower": score.get("precision_lower"),
                "positive_reference_coverage": score.get("positive_reference_coverage"),
                "gate": dict(gate),
            }
        )
    ranked.sort(
        key=lambda item: (
            -float(item["f1"] or 0.0),
            -float(item["precision_lower"] or 0.0),
            -float(item["positive_reference_coverage"] or 0.0),
            json.dumps(item["params"], sort_keys=True),
        )
    )
    return {
        "status": "INCONCLUSIVE",
        "trial_count": len(trials),
        "eligible_trial_count": int(raw.get("eligible_trial_count") or 0),
        "gate_reason_counts": dict(sorted(reasons.items())),
        "top_diagnostic_trials": ranked[:3],
        "grid_expanded": False,
        "rerun": False,
    }


def _milestone_disposition(
    *, stage56_selected: Mapping[str, Any] | None, stage11_selected: Mapping[str, Any] | None
) -> tuple[bool, str]:
    complete = stage56_selected is not None and stage11_selected is not None
    return (
        complete,
        (
            "DEVELOPMENT_MILESTONE_COMPLETE_WITH_STAGE_7_INCONCLUSIVE"
            if complete
            else "INCONCLUSIVE_AFTER_REFERENCE_SUPPLEMENT"
        ),
    )


def run_second_group_development_supplement(
    *, config_path: Path, training_root: Path, output_root: Path, code_revision: str
) -> dict[str, Any]:
    if not _REVISION.fullmatch(code_revision):
        raise StageSecondGroupSupplementError("supplement requires exact Git revision")
    started = time.perf_counter()
    root = find_project_root()
    config = load_second_group_supplement_config(config_path)
    base = load_second_group_development_config(root / str(config["base_development_config"]))
    panel = load_second_group_panel(root / str(config["synthetic_panel_config"]))
    stage56 = _object(root / str(config["stage_5_6_experiment_config"]), "Stage 5/6 experiment")
    repair = _object(
        root / str(config["stage_7_10_11_experiment_config"]), "Stage 7/10/11 experiment"
    )
    node_config = _object(root / str(config["training_node_config"]), "training node")
    first_candidate = _object(
        root / str(base["first_group_candidate_config"]), "first-group candidate"
    )
    prior_summary, prior_candidate, prior_root = _verify_prior_artifact(training_root, config)
    stage7_prior = _stage7_prior_analysis(prior_summary)
    frozen10 = dict(config["prior_run"]["frozen_stage_10"])
    if prior_candidate["selected_parameters"]["stage_10"] != frozen10:
        raise StageSecondGroupSupplementError("frozen Stage 10 candidate drifted")
    _progress(
        started,
        "prior-artifact-verified",
        prior_run_id=config["prior_run"]["run_id"],
        prior_artifact_root=str(prior_root),
        stage_7_status=stage7_prior["status"],
    )

    # Reuse the exact base-development semantic spec so Structure/episode identities reproduce.
    spec_digest = digest(
        {
            "schema_version": "quanthus.stage-second-group-development-definition.v1",
            "development_id": base["development_id"],
            "first_group_candidate_digest": digest(first_candidate),
            "stage_5_6_experiment_digest": digest(stage56),
            "stage_7_10_11_experiment_digest": digest(repair),
            "synthetic_panel_digest": digest(panel),
            "official_stage_labels": False,
        }
    )
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
        raise StageSecondGroupSupplementError("development population is smaller than 512")
    cohort = tuple(sorted(ranked[:cohort_size]))
    _progress(started, "cohort-ready", eligible_population=len(ranked), cohort_size=cohort_size)
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
    first_candidates, _ = build_stage34_decisions(
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

    base_sampling = base["reference_sampling"]
    supplement = config["supplement_sampling"]
    base_contexts: dict[int, tuple[SampledContext, ...]] = {}
    supplement_contexts: dict[int, tuple[SampledContext, ...]] = {}
    merged_contexts: dict[int, tuple[SampledContext, ...]] = {}
    merged_references: dict[int, tuple[Any, ...]] = {}
    for stage in (5, 6, 11):
        base_contexts[stage] = sample_stage_contexts(
            observations,
            stage=stage,
            seed=str(base_sampling["seed"]),
            per_bucket=int(base_sampling["target_contexts_per_bucket"]),
            minimum_total=int(base_sampling["minimum_realized_contexts_per_stage"]),
        )
        if stage in supplement["supplement_stages"]:
            supplement_contexts[stage] = sample_stage_supplement_contexts(
                observations,
                stage=stage,
                base_contexts=base_contexts[stage],
                seed=str(supplement["supplement_seed"]),
                per_bucket=int(supplement["additional_target_contexts_per_bucket"]),
            )
        else:
            supplement_contexts[stage] = ()
        merged_contexts[stage] = tuple(
            sorted(
                (*base_contexts[stage], *supplement_contexts[stage]),
                key=lambda item: (item.bucket, item.context_id),
            )
        )
        if len({item.context_id for item in merged_contexts[stage]}) != len(
            merged_contexts[stage]
        ):
            raise StageSecondGroupSupplementError("merged reference contexts contain duplicates")
        merged_references[stage], _ = resolve_stage_references(merged_contexts[stage], panel)

    reference_summary = {
        str(stage): {
            "base": _reference_summary(
                base_contexts[stage],
                resolve_stage_references(base_contexts[stage], panel)[0],
            ),
            "supplement": _reference_summary(
                supplement_contexts[stage],
                resolve_stage_references(supplement_contexts[stage], panel)[0],
            )
            if supplement_contexts[stage]
            else {
                "context_count": 0,
                "bucket_counts": {},
                "reference_kind_counts": {},
                "definite_positive_count": 0,
                "definite_negative_count": 0,
                "ambiguous_count": 0,
                "unjudgeable_count": 0,
            },
            "merged": _reference_summary(merged_contexts[stage], merged_references[stage]),
        }
        for stage in (5, 6, 11)
    }
    minimum_ambiguous = int(supplement["minimum_merged_ambiguous_references_per_stage"])
    sufficient = all(
        reference_summary[str(stage)]["merged"]["ambiguous_count"] >= minimum_ambiguous
        for stage in (6, 11)
    )
    _progress(
        started,
        "supplement-references-ready",
        reference_counts={
            str(stage): reference_summary[str(stage)]["merged"]["context_count"]
            for stage in (5, 6, 11)
        },
        ambiguous_counts={
            str(stage): reference_summary[str(stage)]["merged"]["ambiguous_count"]
            for stage in (6, 11)
        },
        sufficient=sufficient,
    )

    gates = base["hard_gates"]
    if not sufficient:
        stage56_result: dict[str, Any] = {
            "blocked": True,
            "reason": "merged_ambiguous_reference_count_below_unchanged_gate",
        }
        stage11_result: dict[str, Any] = {
            "blocked": True,
            "reason": "merged_ambiguous_reference_count_below_unchanged_gate",
        }
    else:
        stage56_result = _search_5_6(
            {5: merged_contexts[5], 6: merged_contexts[6]},
            {5: merged_references[5], 6: merged_references[6]},
            stage56,
            gates,
        )
        stage11_raw = repair["stage_11"]
        stage11_params = [
            {"rebound_from_post_break_low_min": str(value)}
            for value in stage11_raw["rebound_from_post_break_low_min_candidates"]
        ]
        stage11_result = _search_single_stage(
            stage=11,
            params_list=stage11_params,
            r0_params={"rebound_from_post_break_low_min": "0.05"},
            contexts=merged_contexts[11],
            references=merged_references[11],
            gates=gates,
            stage10_params=frozen10,
        )
    stage56_selected = (
        stage56_result.get("selected", {}).get("params")
        if isinstance(stage56_result.get("selected"), Mapping)
        else None
    )
    stage11_selected = (
        stage11_result.get("selected", {}).get("params")
        if isinstance(stage11_result.get("selected"), Mapping)
        else None
    )
    milestone_complete, disposition = _milestone_disposition(
        stage56_selected=stage56_selected,
        stage11_selected=stage11_selected,
    )
    selected_parameters = {
        "stage_5_6": stage56_selected,
        "stage_10": frozen10,
        "stage_7": None,
        "stage_11": stage11_selected,
    }
    candidate = {
        "schema_version": "quanthus.stage-second-group-development-supplement-candidate.v1",
        "supplement_id": config["supplement_id"],
        "prior_candidate_artifact_digest": config["prior_run"]["candidate_artifact_digest"],
        "evidence_scope": "development_only_not_confirmatory",
        "reference_evidence_tier": "SYNTHETIC_EXPERT_CONSENSUS",
        "human_expert_validated": False,
        "preserve_distinct_stage_codes": [5, 6, 7, 10, 11],
        "selected_parameters": selected_parameters,
        "stage_7_disposition": "INCONCLUSIVE",
        "milestone_complete": milestone_complete,
        "disposition": disposition,
        "official_label_publication_authorized": False,
        "strategy_consumption_authorized": False,
        "trading_authorized": False,
    }
    summary = {
        "schema_version": "quanthus.stage-second-group-development-supplement-summary.v1",
        "work_item": "P3-STAGE-001-H",
        "code_revision": code_revision,
        "supplement_id": config["supplement_id"],
        "supplement_config_digest": digest(config),
        "prior_run_id": config["prior_run"]["run_id"],
        "prior_manifest_sha256": config["prior_run"]["manifest_sha256"],
        "prior_candidate_artifact_digest": config["prior_run"]["candidate_artifact_digest"],
        "source_manifest_digest": digest(source_manifest),
        "cohort_size": cohort_size,
        "cohort_digest": digest(cohort),
        "observation_count": len(observations),
        "reference_summary": reference_summary,
        "stage_5_6": stage56_result,
        "stage_10": {"frozen_from_prior_run": True, "selected": frozen10},
        "stage_7": stage7_prior,
        "stage_11": stage11_result,
        "selected_parameters": selected_parameters,
        "milestone_complete": milestone_complete,
        "disposition": disposition,
        "development_only": True,
        "reference_supplement_performed": True,
        "parameter_search_performed": sufficient,
        "stage_10_researched": False,
        "stage_7_researched": False,
        "hard_gate_values_changed": False,
        "future_outcome_read": False,
        "arbitration_read": False,
        "permanent_blind_read": False,
        "holdout_population_selected": False,
        "stage_labels_emitted": False,
        "qualified_label_version": False,
        "elapsed_seconds": time.perf_counter() - started,
        "claim_limits": list(config["claim_limits"]),
    }
    run_id = "stage-second-group-development-supplement-" + datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    run_root = output_root.expanduser().resolve() / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    summary_payload = canonical(summary)
    candidate_payload = canonical(candidate)
    (run_root / "summary.json").write_bytes(summary_payload)
    (run_root / "candidate.json").write_bytes(candidate_payload)
    manifest = {
        "schema_version": "quanthus.stage-second-group-development-supplement-artifact.v1",
        "run_id": run_id,
        "code_revision": code_revision,
        "supplement_config_digest": digest(config),
        "files": {
            "candidate.json": _sha256(candidate_payload),
            "summary.json": _sha256(summary_payload),
        },
        "development_only": True,
        "hard_gate_values_changed": False,
        "future_outcome_read": False,
        "arbitration_read": False,
        "permanent_blind_read": False,
        "holdout_population_selected": False,
        "stage_labels_emitted": False,
        "qualified_label_version": False,
        "milestone_complete": milestone_complete,
        "disposition": disposition,
    }
    manifest_payload = canonical(manifest)
    (run_root / "manifest.json").write_bytes(manifest_payload)
    ready = _sha256(manifest_payload)
    (run_root / "READY").write_text(ready + "\n", encoding="ascii")
    return {
        **summary,
        "run_id": run_id,
        "run_root": str(run_root),
        "manifest_sha256": ready,
        "candidate_artifact_digest": _sha256(candidate_payload),
    }


def verify_second_group_development_supplement_artifact(
    path: Path, *, code_revision: str | None = None
) -> dict[str, Any]:
    root = path.expanduser().resolve()
    manifest = _object(root / "manifest.json", "supplement manifest")
    if manifest.get("schema_version") != (
        "quanthus.stage-second-group-development-supplement-artifact.v1"
    ):
        raise StageSecondGroupSupplementError("supplement artifact schema differs")
    ready = (root / "READY").read_text(encoding="ascii").strip()
    if ready != _sha256(canonical(manifest)):
        raise StageSecondGroupSupplementError("supplement READY does not bind manifest")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise StageSecondGroupSupplementError("supplement code revision differs")
    files = manifest.get("files")
    if not isinstance(files, Mapping) or set(files) != {"candidate.json", "summary.json"}:
        raise StageSecondGroupSupplementError("supplement file inventory differs")
    for name, expected in files.items():
        if _sha256((root / name).read_bytes()) != expected:
            raise StageSecondGroupSupplementError(f"supplement digest differs: {name}")
    summary = _object(root / "summary.json", "supplement summary")
    candidate = _object(root / "candidate.json", "supplement candidate")
    if (
        summary.get("development_only") is not True
        or summary.get("hard_gate_values_changed") is not False
        or summary.get("stage_10_researched") is not False
        or summary.get("stage_7_researched") is not False
        or summary.get("future_outcome_read") is not False
        or summary.get("arbitration_read") is not False
        or summary.get("permanent_blind_read") is not False
        or summary.get("stage_labels_emitted") is not False
        or summary.get("qualified_label_version") is not False
        or candidate.get("preserve_distinct_stage_codes") != [5, 6, 7, 10, 11]
        or candidate.get("stage_7_disposition") != "INCONCLUSIVE"
    ):
        raise StageSecondGroupSupplementError("supplement authority differs")
    return {
        "run_id": manifest["run_id"],
        "manifest_sha256": ready,
        "candidate_artifact_digest": files["candidate.json"],
        "file_count": 2,
        "verified": True,
        "disposition": summary["disposition"],
        "milestone_complete": summary["milestone_complete"],
        "selected_parameters": summary["selected_parameters"],
        "reference_summary": summary["reference_summary"],
        "stage_7_gate_reason_counts": summary["stage_7"]["gate_reason_counts"],
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
        result = run_second_group_development_supplement(
            config_path=args.config,
            training_root=args.training_root,
            output_root=args.output_root,
            code_revision=args.code_revision,
        )
    else:
        result = verify_second_group_development_supplement_artifact(
            args.artifact, code_revision=args.code_revision
        )
    print(canonical(result).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

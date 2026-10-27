"""One-time held-out external semantic evaluation for P3-STAGE-001-F.

The runner consumes one frozen development candidate and one preregistered 2022-2024
arbitration contract.  It performs no parameter search, reads no 2025 permanent-blind
rows, reads no future settlement, and can emit at most a bounded SHADOW semantic
disposition because the reference tier remains SYNTHETIC_EXPERT_CONSENSUS.
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
from quanthus.stage_learning.learning import (
    R1Parameters,
    build_learning_observations,
    predict_stage,
    r0_parameters,
    score_predictions,
)
from quanthus.stage_learning.qualification_source import (
    QualificationReadScope,
    read_qualification_admissions,
)
from quanthus.stage_learning.reference import (
    ReferenceContext,
    boundary_tags,
    build_reviewer_packet,
    context_identity,
)
from quanthus.stage_learning.review_ledger import resolve_complete_review
from quanthus.stage_learning.source import discover_development_security_codes
from quanthus.stage_learning.synthetic_panel import (
    assignment_manifest,
    evaluate_panel,
    load_synthetic_panel,
)
from quanthus.structure_v2 import EpisodeObservation, build_structure_v2
from quanthus.structure_v2_inputs import canonical, digest

DEFAULT_CONFIG = "config/stage-learning/qualification.v1.json"
_HARNESS_CONFIG = "config/stage-learning/harness.v1.json"
_REVISION = re.compile(r"^[0-9a-f]{40}$")


class StageQualificationError(ValueError):
    """Raised when the frozen F evaluation cannot execute without exceeding authority."""


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StageQualificationError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise StageQualificationError(f"{label} must be a JSON object")
    return value


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _jsonl(values: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(canonical(dict(value)) + b"\n" for value in values)


def _date(value: Mapping[str, Any], key: str) -> date:
    try:
        return date.fromisoformat(str(value[key]))
    except (KeyError, ValueError) as exc:
        raise StageQualificationError(f"invalid qualification date: {key}") from exc


def _int(value: Mapping[str, Any], key: str) -> int:
    raw = value.get(key)
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 1:
        raise StageQualificationError(f"invalid positive integer: {key}")
    return raw


def load_qualification_config(path: Path) -> dict[str, Any]:
    raw = _object(path, "stage qualification config")
    expected = {
        "schema_version",
        "qualification_id",
        "work_item",
        "candidate_config",
        "candidate_digest",
        "training_node_config",
        "experiment_config",
        "synthetic_panel_config",
        "authority",
        "partition",
        "population",
        "sampling",
        "evaluation",
        "hard_gates",
        "decision",
        "artifact",
    }
    if set(raw) != expected or raw.get("schema_version") != "quanthus.stage-qualification.v1":
        raise StageQualificationError("stage qualification config schema differs")
    if raw.get("work_item") != "P3-STAGE-001-F":
        raise StageQualificationError("stage qualification work item differs")
    authority = raw.get("authority")
    if not isinstance(authority, Mapping):
        raise StageQualificationError("stage qualification authority is missing")
    required_true = {
        "implementation_authorized",
        "one_time_arbitration_semantic_evaluation_authorized",
        "bounded_shadow_disposition_authorized",
    }
    required_false = {
        "parameter_search_authorized",
        "candidate_mutation_after_result_access_authorized",
        "permanent_blind_access_authorized",
        "future_settlement_access_authorized",
        "human_expert_qualification_claim_authorized",
        "qualified_label_publication_authorized",
        "strategy_consumption_authorized",
        "daily_schedule_authorized",
        "trading_authorized",
    }
    if any(authority.get(key) is not True for key in required_true) or any(
        authority.get(key) is not False for key in required_false
    ):
        raise StageQualificationError("stage qualification authority differs")
    if authority.get("arbitration_access_budget_runs") != 1:
        raise StageQualificationError("qualification requires exactly one arbitration run budget")
    partition = raw.get("partition")
    if not isinstance(partition, Mapping) or (
        partition.get("name") != "2022-2024-arbitration"
        or partition.get("permanent_blind_2025_untouched") is not True
        or partition.get("physical_blind_claim") is not False
    ):
        raise StageQualificationError("qualification partition contract differs")
    sampling = raw.get("sampling")
    if not isinstance(sampling, Mapping):
        raise StageQualificationError("qualification sampling contract is missing")
    strata = sampling.get("strata")
    if not isinstance(strata, Mapping) or sum(int(value) for value in strata.values()) != _int(
        sampling, "total_contexts"
    ):
        raise StageQualificationError("qualification sampling strata do not close")
    if sampling.get("one_context_per_episode") is not True:
        raise StageQualificationError("qualification requires unique episodes")
    if sampling.get("no_result_dependent_resampling") is not True:
        raise StageQualificationError("qualification resampling discipline differs")
    evaluation = raw.get("evaluation")
    if not isinstance(evaluation, Mapping) or (
        evaluation.get("evaluate_candidate_and_r0_only") is not True
        or evaluation.get("r1_search_trial_count") != 0
        or evaluation.get("r2_executed") is not False
        or evaluation.get("future_settlement_read") is not False
        or evaluation.get("strategy_result_read") is not False
        or evaluation.get("reference_evidence_tier") != "SYNTHETIC_EXPERT_CONSENSUS"
    ):
        raise StageQualificationError("qualification evaluation contract differs")
    decision = raw.get("decision")
    if not isinstance(decision, Mapping) or (
        decision.get("qualified_label_version_allowed") is not False
        or decision.get("no_parameter_change_after_result_access") is not True
        or decision.get("failure_consumes_the_single_arbitration_access_budget") is not True
    ):
        raise StageQualificationError("qualification decision contract differs")
    return raw


def _candidate(root: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    relative = str(config["candidate_config"])
    candidate = _object(root / relative, "development candidate")
    if candidate.get("schema_version") != "quanthus.stage-development-candidate.v1":
        raise StageQualificationError("development candidate schema differs")
    if digest(candidate) != config.get("candidate_digest"):
        raise StageQualificationError("development candidate digest differs")
    if candidate.get("status") != "frozen_for_external_evaluation":
        raise StageQualificationError("development candidate is not frozen")
    if candidate.get("reference_evidence_tier") != "SYNTHETIC_EXPERT_CONSENSUS":
        raise StageQualificationError("development candidate evidence tier differs")
    return candidate


def _fact_spec(root: Path, experiment: Mapping[str, Any], config: Mapping[str, Any]) -> str:
    harness = load_harness_config(root / _HARNESS_CONFIG)
    partition = config["partition"]
    return digest(
        {
            "schema_version": "quanthus.stage-3-4-external-semantic-definition.v1",
            "experiment_id": experiment.get("experiment_id"),
            "first_group": experiment.get("first_group"),
            "facts": harness.get("facts"),
            "r0_engineering_rules": harness.get("r0_engineering_rules"),
            "qualification_partition": {
                "name": partition.get("name"),
                "knowledge_view": partition.get("knowledge_view"),
                "permanent_blind_2025_untouched": True,
            },
            "official_label": False,
        }
    )


def _rank(seed: str, context_id: str) -> str:
    return hashlib.sha256(f"{seed}|{context_id}".encode()).hexdigest()


def _context(
    episode: EpisodeObservation,
    activity_by_key: Mapping[tuple[str, date], ActivityObservation],
    *,
    stratum: str,
) -> ReferenceContext:
    context_id = context_identity(episode)
    activity = activity_by_key.get((episode.security_code, episode.market_date))
    return ReferenceContext(
        context_id=context_id,
        security_code=episode.security_code,
        market_date=episode.market_date,
        anchor_scale=episode.scale,
        episode_id=episode.episode_id,
        anchor_key=episode.anchor_key,
        anchor_revision=episode.anchor_revision,
        sampling_stratum=stratum,
        boundary_tags=boundary_tags(episode, activity),
        evidence_digest=digest(
            {
                "episode_revision": episode.revision_id,
                "activity_revision": None if activity is None else activity.revision_id,
            }
        ),
    )


def _take_unique_episodes(
    rows: Sequence[EpisodeObservation],
    *,
    count: int,
    seed: str,
    used_episode_ids: set[str],
    distance_first: bool = False,
    require_exact: bool = True,
) -> tuple[EpisodeObservation, ...]:
    if count == 0:
        return ()
    eligible = [item for item in rows if item.episode_id not in used_episode_ids]
    if distance_first:

        def order(item: EpisodeObservation) -> tuple[Any, ...]:
            if item.close is None or item.frozen_level <= 0:
                distance = Decimal("999")
            else:
                distance = abs(item.close / item.frozen_level - Decimal(1))
            return (distance, _rank(seed, context_identity(item)), context_identity(item))
    else:

        def order(item: EpisodeObservation) -> tuple[Any, ...]:
            return (_rank(seed, context_identity(item)), context_identity(item))

    selected: list[EpisodeObservation] = []
    local_episodes: set[str] = set()
    for item in sorted(eligible, key=order):
        if item.episode_id in local_episodes:
            continue
        selected.append(item)
        local_episodes.add(item.episode_id)
        if len(selected) == count:
            break
    if require_exact and len(selected) != count:
        raise StageQualificationError(
            f"qualification stratum has {len(selected)} unique episodes; need {count}"
        )
    used_episode_ids.update(local_episodes)
    return tuple(selected)


def select_qualification_contexts(
    episodes: Sequence[EpisodeObservation],
    activity: Sequence[ActivityObservation],
    *,
    report_start: date,
    report_end: date,
    sampling: Mapping[str, Any],
    target_caps: bool = False,
) -> tuple[ReferenceContext, ...]:
    """Select without candidate/future evidence; legacy runtime keeps exact quotas.

    A separately authorized development successor may explicitly request v2 caps.
    This option neither opens a partition nor relaxes semantic sufficiency.
    """
    seed = str(sampling.get("sampling_seed") or "")
    if not seed:
        raise StageQualificationError("qualification sampling seed is missing")
    activity_by_key = {item.key: item for item in activity}
    if len(activity_by_key) != len(activity):
        raise StageQualificationError("qualification activity observations contain duplicates")
    eligible = [item for item in episodes if report_start <= item.market_date <= report_end]
    if not eligible:
        if target_caps:
            return ()
        raise StageQualificationError("qualification episode population is empty")
    strata = sampling["strata"]
    started_bucket_counts = sampling.get("started_activity_buckets")
    held_counts = sampling.get("early_held_per_hold_count")
    if not isinstance(started_bucket_counts, Mapping) or not isinstance(held_counts, Mapping):
        raise StageQualificationError("qualification sub-strata are missing")
    if sum(int(value) for value in started_bucket_counts.values()) != int(
        strata["started_activity"]
    ):
        raise StageQualificationError("started activity bucket counts do not close")
    if sum(int(value) for value in held_counts.values()) != int(strata["early_held"]):
        raise StageQualificationError("early-held bucket counts do not close")

    used_episode_ids: set[str] = set()
    groups: list[tuple[str, tuple[EpisodeObservation, ...]]] = []

    started = [item for item in eligible if item.observation == "started"]
    started_buckets: dict[str, list[EpisodeObservation]] = {
        "missing_or_lt_1": [],
        "1_to_1_25": [],
        "1_25_to_1_5": [],
        "gte_1_5": [],
    }
    for episode in started:
        item = activity_by_key.get((episode.security_code, episode.market_date))
        ratio = (
            item.volume_ratio
            if item is not None and item.available and item.volume_ratio is not None
            else None
        )
        if ratio is None or ratio < Decimal("1.0"):
            bucket = "missing_or_lt_1"
        elif ratio < Decimal("1.25"):
            bucket = "1_to_1_25"
        elif ratio < Decimal("1.5"):
            bucket = "1_25_to_1_5"
        else:
            bucket = "gte_1_5"
        started_buckets[bucket].append(episode)
    started_selected: list[EpisodeObservation] = []
    for bucket in ("missing_or_lt_1", "1_to_1_25", "1_25_to_1_5", "gte_1_5"):
        selected = _take_unique_episodes(
            started_buckets[bucket],
            count=int(started_bucket_counts[bucket]),
            seed=f"{seed}|started|{bucket}",
            used_episode_ids=used_episode_ids,
            require_exact=not target_caps,
        )
        started_selected.extend(selected)
    groups.append(("qualification_started_activity", tuple(started_selected)))

    held_selected: list[EpisodeObservation] = []
    for hold in (1, 2, 3):
        selected = _take_unique_episodes(
            [
                item
                for item in eligible
                if item.observation == "held" and item.phase == "active" and item.hold_count == hold
            ],
            count=int(held_counts[str(hold)]),
            seed=f"{seed}|held|{hold}",
            used_episode_ids=used_episode_ids,
            require_exact=not target_caps,
        )
        held_selected.extend(selected)
    groups.append(("qualification_early_held", tuple(held_selected)))

    groups.append(
        (
            "qualification_near_anchor_loss_negative",
            _take_unique_episodes(
                [item for item in eligible if item.observation in {"lost", "below"}],
                count=int(strata["near_anchor_loss_negative"]),
                seed=f"{seed}|loss",
                used_episode_ids=used_episode_ids,
                distance_first=True,
                require_exact=not target_caps,
            ),
        )
    )
    groups.append(
        (
            "qualification_reclaimed_ambiguity",
            _take_unique_episodes(
                [item for item in eligible if item.observation == "reclaimed"],
                count=int(strata["reclaimed_ambiguity"]),
                seed=f"{seed}|reclaimed",
                used_episode_ids=used_episode_ids,
                require_exact=not target_caps,
            ),
        )
    )
    groups.append(
        (
            "qualification_mature_held",
            _take_unique_episodes(
                [
                    item
                    for item in eligible
                    if item.observation == "held"
                    and item.phase == "active"
                    and item.hold_count >= 40
                ],
                count=int(strata["mature_held"]),
                seed=f"{seed}|mature-held",
                used_episode_ids=used_episode_ids,
                require_exact=not target_caps,
            ),
        )
    )

    contexts = tuple(
        sorted(
            (
                _context(episode, activity_by_key, stratum=stratum)
                for stratum, rows in groups
                for episode in rows
            ),
            key=lambda item: (item.sampling_stratum, item.context_id),
        )
    )
    if not target_caps and len(contexts) != int(sampling["total_contexts"]):
        raise StageQualificationError("qualification context count differs")
    if len({item.context_id for item in contexts}) != len(contexts):
        raise StageQualificationError("qualification context identities are duplicated")
    if len({item.episode_id for item in contexts}) != len(contexts):
        raise StageQualificationError("qualification sample reuses an episode")
    return contexts


def _trial(candidate: Mapping[str, Any]) -> R1Parameters:
    try:
        raw = candidate["trial"]["parameters"]
        params = R1Parameters(
            Decimal(str(raw["probe_volume_ratio20_min"])),
            int(raw["acceptance_hold_sessions"]),
            Decimal(str(raw["acceptance_signed_distance_min"])),
            Decimal(str(raw["acceptance_window_volume_ratio20_min"])),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise StageQualificationError("frozen candidate parameters are invalid") from exc
    if params.trial_id != candidate["trial"]["trial_id"]:
        raise StageQualificationError("frozen candidate trial identity differs")
    return params


def _hard_gate(
    candidate_score: Mapping[str, Any],
    *,
    r0_score: Mapping[str, Any],
    hard_gates: Mapping[str, Any],
    reference_count: int,
) -> dict[str, Any]:
    reasons: list[str] = []
    metrics = candidate_score.get("stage_metrics")
    if not isinstance(metrics, Mapping):
        raise StageQualificationError("qualification stage metrics are missing")
    for stage in ("3", "4"):
        metric = metrics.get(stage)
        if not isinstance(metric, Mapping):
            reasons.append(f"stage_{stage}_metrics_missing")
            continue
        if int(metric.get("truth_count") or 0) < int(
            hard_gates["minimum_definite_references_per_stage"]
        ):
            reasons.append(f"stage_{stage}_reference_count_below_gate")
        lower = metric.get("precision_wilson_one_sided_95_lower")
        if not isinstance(lower, (int, float)) or float(lower) < float(
            hard_gates["minimum_stage_precision_one_sided_95_lower"]
        ):
            reasons.append(f"stage_{stage}_precision_lower_below_gate")
    if int(candidate_score.get("ambiguous_count") or 0) < int(
        hard_gates["minimum_ambiguous_references_for_overassertion_audit"]
    ):
        reasons.append("ambiguous_reference_count_below_gate")
    ambiguous_rate = candidate_score.get("ambiguous_unique_assertion_rate")
    if not isinstance(ambiguous_rate, (int, float)) or float(ambiguous_rate) > float(
        hard_gates["maximum_ambiguous_unique_assertion_rate"]
    ):
        reasons.append("ambiguous_unique_assertion_rate_above_gate")
    if float(candidate_score.get("positive_reference_coverage") or 0.0) < float(
        hard_gates["minimum_positive_reference_coverage"]
    ):
        reasons.append("positive_reference_coverage_below_gate")
    if float(candidate_score.get("definite_negative_false_positive_rate") or 0.0) > float(
        hard_gates["maximum_definite_negative_false_positive_rate"]
    ):
        reasons.append("definite_negative_false_positive_rate_above_gate")
    tolerance = float(hard_gates["macro_f1_noninferiority_tolerance_vs_r0"])
    if float(candidate_score.get("macro_f1") or 0.0) < (
        float(r0_score.get("macro_f1") or 0.0) - tolerance
    ):
        reasons.append("macro_f1_inferior_to_r0_beyond_tolerance")
    unjudgeable_rate = float(candidate_score.get("unjudgeable_count") or 0) / reference_count
    if unjudgeable_rate > float(hard_gates["maximum_unjudgeable_reference_rate"]):
        reasons.append("unjudgeable_reference_rate_above_gate")
    return {
        "passed": not reasons,
        "reason_codes": reasons,
        "unjudgeable_reference_rate": unjudgeable_rate,
    }


def run_qualification(
    *,
    config_path: Path,
    training_root: Path,
    output_root: Path,
    code_revision: str,
) -> dict[str, Any]:
    if not _REVISION.fullmatch(code_revision):
        raise StageQualificationError("qualification requires exact Git revision")
    started = time.perf_counter()
    root = find_project_root()
    config = load_qualification_config(config_path)
    candidate = _candidate(root, config)
    experiment = _object(root / str(config["experiment_config"]), "stage experiment")
    node_config = _object(root / str(config["training_node_config"]), "training node config")
    panel = load_synthetic_panel(root / str(config["synthetic_panel_config"]))
    if panel.get("evidence_tier") != "SYNTHETIC_EXPERT_CONSENSUS":
        raise StageQualificationError("qualification panel evidence tier differs")
    if panel.get("development_use", {}).get("formal_qualification_allowed") is not False:
        raise StageQualificationError("synthetic panel unexpectedly claims formal qualification")

    partition = config["partition"]
    population = config["population"]
    sampling = config["sampling"]
    evaluation = config["evaluation"]
    hard_gates = config["hard_gates"]
    decision = config["decision"]
    if not all(
        isinstance(item, Mapping)
        for item in (partition, population, sampling, evaluation, hard_gates, decision)
    ):
        raise StageQualificationError("qualification config sections are malformed")

    warmup_start = _date(partition, "warmup_start")
    cohort_date = _date(partition, "cohort_date")
    report_start = _date(partition, "evaluation_start")
    report_end = _date(partition, "evaluation_end")
    if not warmup_start <= cohort_date < report_start <= report_end <= date(2024, 12, 31):
        raise StageQualificationError("qualification partition dates are not ordered")

    cohort = discover_development_security_codes(
        training_root=training_root,
        node_config=node_config,
        scan_start=warmup_start,
        cohort_date=cohort_date,
        minimum_observations=_int(population, "minimum_pre_cohort_observations"),
        cohort_size=_int(population, "cohort_size"),
        seed=str(population["cohort_seed"]),
    )
    spec_digest = _fact_spec(root, experiment, config)
    scope = QualificationReadScope(warmup_start, report_start, report_end)
    admissions, source_manifest, policy = read_qualification_admissions(
        training_root=training_root,
        node_config=node_config,
        scope=scope,
        security_codes=cohort,
        spec_digest=spec_digest,
    )
    structure = build_structure_v2(
        admissions,
        spec_digest=spec_digest,
        calendar_ref=policy.calendar_ref,
        lookbacks=(20, 60),
    )
    activity = build_activity_observations(admissions, window=20)
    contexts = select_qualification_contexts(
        structure.episodes,
        activity,
        report_start=report_start,
        report_end=report_end,
        sampling=sampling,
    )
    packets = build_reviewer_packet(
        contexts,
        structure.episodes,
        activity,
        admissions,
        history_sessions=_int(sampling, "review_history_sessions"),
    )
    evidence_by_context = {item.context_id: item.evidence_digest for item in contexts}
    evaluated = evaluate_panel(panel, packets, evidence_by_context)
    primaries = (
        tuple(evaluated["primary_a"]),
        tuple(evaluated["primary_b"]),
    )
    adjudicator = tuple(evaluated["adjudicator"])
    resolved = resolve_complete_review(contexts, primaries, adjudicator)
    observations = build_learning_observations(contexts, structure, activity)

    candidate_params = _trial(candidate)
    if candidate_params.trial_id != evaluation.get("candidate_trial_id"):
        raise StageQualificationError("qualification candidate trial binding differs")
    r0 = r0_parameters(experiment)
    if r0.trial_id != evaluation.get("r0_trial_id"):
        raise StageQualificationError("qualification R0 trial binding differs")
    candidate_predictions = {
        item.context_id: predict_stage(item, candidate_params) for item in observations
    }
    r0_predictions = {item.context_id: predict_stage(item, r0) for item in observations}
    candidate_score = score_predictions(
        resolved, candidate_predictions, trial_id=candidate_params.trial_id
    ).to_dict()
    r0_score = score_predictions(resolved, r0_predictions, trial_id="R0:" + r0.trial_id).to_dict()
    gate = _hard_gate(
        candidate_score,
        r0_score=r0_score,
        hard_gates=hard_gates,
        reference_count=len(resolved),
    )
    if hard_gates.get("require_zero_quantitative_audit_vetoes") is True and any(
        item.get("veto") for item in evaluated["audits"]
    ):
        raise StageQualificationError("quantitative audit veto escaped panel evaluation")
    if hard_gates.get("require_unique_episode_per_sampled_context") is True and len(
        {item.episode_id for item in contexts}
    ) != len(contexts):
        raise StageQualificationError("qualification sample episode uniqueness differs")

    disposition = str(
        decision["when_all_hard_gates_pass"]
        if gate["passed"]
        else decision["when_any_hard_gate_fails"]
    )
    kind_counts: dict[str, int] = {}
    for item in resolved:
        kind_counts[item.reference_kind.value] = kind_counts.get(item.reference_kind.value, 0) + 1
    sample_counts = {
        stratum: sum(item.sampling_stratum == stratum for item in contexts)
        for stratum in sorted({item.sampling_stratum for item in contexts})
    }

    run_id = "stage-qualification-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_root = output_root.expanduser().resolve() / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    review_rows = {
        "primary-a.jsonl": _jsonl(
            tuple(
                {
                    "schema_version": "quanthus.stage-reference-review.v1",
                    **item.to_dict(),
                }
                for item in primaries[0]
            )
        ),
        "primary-b.jsonl": _jsonl(
            tuple(
                {
                    "schema_version": "quanthus.stage-reference-review.v1",
                    **item.to_dict(),
                }
                for item in primaries[1]
            )
        ),
        "adjudicator.jsonl": _jsonl(
            tuple(
                {
                    "schema_version": "quanthus.stage-reference-review.v1",
                    **item.to_dict(),
                }
                for item in adjudicator
            )
        ),
        "audit.jsonl": _jsonl(tuple(dict(item) for item in evaluated["audits"])),
        "institutional-challenges.jsonl": _jsonl(
            tuple(dict(item) for item in evaluated["challenges"])
        ),
    }
    summary = {
        "schema_version": "quanthus.stage-qualification-summary.v1",
        "work_item": "P3-STAGE-001-F",
        "run_id": run_id,
        "code_revision": code_revision,
        "qualification_id": config["qualification_id"],
        "qualification_config_digest": digest(config),
        "candidate_id": candidate["candidate_id"],
        "candidate_digest": digest(candidate),
        "candidate_trial_id": candidate_params.trial_id,
        "r0_trial_id": r0.trial_id,
        "partition": "2022-2024-arbitration",
        "permanent_blind_read": False,
        "future_settlement_read": False,
        "strategy_result_read": False,
        "parameter_search_performed": False,
        "cohort_size": len(cohort),
        "cohort_digest": digest(cohort),
        "context_count": len(contexts),
        "unique_episode_count": len({item.episode_id for item in contexts}),
        "sample_counts": sample_counts,
        "reference_kind_counts": dict(sorted(kind_counts.items())),
        "reference_evidence_tier": "SYNTHETIC_EXPERT_CONSENSUS",
        "human_expert_validated": False,
        "primary_disagreement_count": len(evaluated["disagreement_context_ids"]),
        "quantitative_audit_veto_count": sum(bool(item["veto"]) for item in evaluated["audits"]),
        "candidate_score": candidate_score,
        "r0_score": r0_score,
        "hard_gate": gate,
        "disposition": disposition,
        "qualified_label_version": False,
        "bounded_shadow_semantic_evidence": gate["passed"],
        "access_budget_consumed": True,
        "elapsed_seconds": time.perf_counter() - started,
        "claim_limits": [
            (
                "this Run consumes the single preregistered 2022-2024 arbitration "
                "semantic-evaluation budget"
            ),
            "synthetic consensus is not HUMAN_EXPERT_REFERENCE",
            (
                "a passing disposition is bounded SHADOW external-semantic evidence, "
                "not a QUALIFIED label publication"
            ),
            "2025 permanent-blind remains untouched",
            (
                "no future settlement, strategy result, R1 search, R2 model, or parameter "
                "mutation is admitted"
            ),
        ],
    }
    sample_manifest = {
        "schema_version": "quanthus.stage-qualification-sample-manifest.v1",
        "qualification_id": config["qualification_id"],
        "cohort": cohort,
        "cohort_digest": digest(cohort),
        "contexts": [item.to_dict() for item in contexts],
        "sampling": dict(sampling),
        "report_start": report_start,
        "report_end": report_end,
    }
    access_ledger = {
        "schema_version": "quanthus.stage-qualification-access-ledger.v1",
        "qualification_id": config["qualification_id"],
        "candidate_digest": digest(candidate),
        "partition": "2022-2024-arbitration",
        "access_budget_runs": 1,
        "access_budget_consumed": 1,
        "result_bearing": True,
        "permanent_blind_2025_read": False,
        "future_settlement_read": False,
        "parameter_search_performed": False,
        "source_manifest_digest": digest(source_manifest),
    }
    payloads: dict[str, bytes] = {
        "source-manifest.json": canonical(source_manifest),
        "sample-manifest.json": canonical(sample_manifest),
        "review-packets.jsonl": _jsonl(tuple(dict(item) for item in packets)),
        "resolved-references.jsonl": _jsonl(
            tuple(
                {"schema_version": "quanthus.stage-reference-resolution.v1", **item.to_dict()}
                for item in resolved
            )
        ),
        "candidate-score.json": canonical(candidate_score),
        "r0-score.json": canonical(r0_score),
        "assignment.json": canonical(
            assignment_manifest(panel, [item.context_id for item in contexts])
        ),
        "access-ledger.json": canonical(access_ledger),
        "summary.json": canonical(summary),
        **review_rows,
    }
    for name, payload in payloads.items():
        (run_root / name).write_bytes(payload)
    manifest = {
        "schema_version": "quanthus.stage-qualification-artifact.v1",
        "run_id": run_id,
        "code_revision": code_revision,
        "qualification_config_digest": digest(config),
        "candidate_digest": digest(candidate),
        "partition": "2022-2024-arbitration",
        "files": {name: _sha256(payload) for name, payload in sorted(payloads.items())},
        "access_budget_consumed": True,
        "permanent_blind_read": False,
        "future_settlement_read": False,
        "parameter_search_performed": False,
        "qualified_label_version": False,
        "disposition": disposition,
    }
    manifest_bytes = canonical(manifest)
    (run_root / "manifest.json").write_bytes(manifest_bytes)
    ready = _sha256(manifest_bytes)
    (run_root / "READY").write_text(ready + "\n", encoding="ascii")
    return {**summary, "run_root": str(run_root), "manifest_sha256": ready}


def verify_qualification_artifact(
    path: Path, *, code_revision: str | None = None
) -> dict[str, Any]:
    root = path.expanduser().resolve()
    manifest = _object(root / "manifest.json", "qualification manifest")
    if manifest.get("schema_version") != "quanthus.stage-qualification-artifact.v1":
        raise StageQualificationError("qualification artifact schema differs")
    manifest_bytes = canonical(manifest)
    try:
        ready = (root / "READY").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise StageQualificationError("qualification READY is unreadable") from exc
    if ready != _sha256(manifest_bytes):
        raise StageQualificationError("qualification READY does not bind manifest")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise StageQualificationError("qualification code revision differs")
    files = manifest.get("files")
    if not isinstance(files, Mapping):
        raise StageQualificationError("qualification file inventory is missing")
    for name, expected in files.items():
        if not isinstance(name, str) or not isinstance(expected, str):
            raise StageQualificationError("qualification file inventory is invalid")
        observed = _sha256((root / name).read_bytes())
        if observed != expected:
            raise StageQualificationError(f"qualification file digest differs: {name}")
    summary = _object(root / "summary.json", "qualification summary")
    if (
        manifest.get("access_budget_consumed") is not True
        or manifest.get("permanent_blind_read") is not False
        or manifest.get("future_settlement_read") is not False
        or manifest.get("parameter_search_performed") is not False
        or manifest.get("qualified_label_version") is not False
        or summary.get("partition") != "2022-2024-arbitration"
        or summary.get("reference_evidence_tier") != "SYNTHETIC_EXPERT_CONSENSUS"
        or summary.get("human_expert_validated") is not False
        or summary.get("qualified_label_version") is not False
    ):
        raise StageQualificationError("qualification authority differs")
    return {
        "run_id": manifest["run_id"],
        "manifest_sha256": ready,
        "disposition": manifest["disposition"],
        "file_count": len(files),
        "verified": True,
        "qualified_label_version": False,
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
        result = run_qualification(
            config_path=args.config,
            training_root=args.training_root,
            output_root=args.output_root,
            code_revision=args.code_revision,
        )
    else:
        result = verify_qualification_artifact(
            args.artifact,
            code_revision=args.code_revision,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

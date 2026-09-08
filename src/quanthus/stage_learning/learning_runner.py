"""Governed P3-STAGE-001-E development R0/R1 runner.

The runner is intentionally unusable until the delivery plan explicitly authorizes development
semantic training and a complete D reference-resolution artifact is supplied.  It never reads
future settlements, arbitration, permanent-blind data, or strategy outcomes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.stage_learning.core import build_activity_observations
from quanthus.stage_learning.learning import (
    build_learning_observations,
    enumerate_r1_trials,
    load_resolved_references,
    predict_stage,
    r0_parameters,
    score_predictions,
)
from quanthus.stage_learning.reference_pilot import verify_reference_pilot_artifact
from quanthus.stage_learning.review_ledger import (
    load_reference_contexts,
    verify_resolution_artifact,
)
from quanthus.stage_learning.source import DevelopmentReadScope, read_development_admissions
from quanthus.structure_v2 import build_structure_v2
from quanthus.structure_v2_inputs import canonical, digest

_REVISION = re.compile(r"^[0-9a-f]{40}$")


class StageLearningRunError(ValueError):
    """Raised when an E Run would violate authority, evidence identity, or data boundaries."""


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StageLearningRunError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise StageLearningRunError(f"{label} must be a JSON object")
    return value


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _jsonl(values: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(canonical(dict(value)) + b"\n" for value in values)


def _date(value: object, label: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise StageLearningRunError(f"{label} is not a date") from exc


def _minimum_precision_lower(score: Mapping[str, Any]) -> float:
    values: list[float] = []
    stage_metrics = score.get("stage_metrics")
    if not isinstance(stage_metrics, Mapping):
        return 0.0
    for stage in ("3", "4"):
        metric = stage_metrics.get(stage)
        if isinstance(metric, Mapping):
            value = metric.get("precision_wilson_one_sided_95_lower")
            if isinstance(value, (int, float)):
                values.append(float(value))
    return min(values) if values else 0.0


def _rank_trial(row: Mapping[str, Any], r0: Mapping[str, Any]) -> tuple[Any, ...]:
    score = row["score"]
    params = row["parameters"]
    r0_params = r0["parameters"]
    complexity = sum(
        params[key] != r0_params[key]
        for key in (
            "probe_volume_ratio20_min",
            "acceptance_hold_sessions",
            "acceptance_signed_distance_min",
            "acceptance_window_volume_ratio20_min",
        )
    )
    return (
        -float(score["macro_f1"]),
        -_minimum_precision_lower(score),
        -float(score["coverage"]),
        complexity,
        str(row["trial_id"]),
    )


def _require_authority(root: Path) -> dict[str, Any]:
    plan = _object(root / "config/stock-stage-learning-delivery.v1.json", "stage delivery plan")
    authority = plan.get("authorization")
    if not isinstance(authority, Mapping):
        raise StageLearningRunError("stage delivery authority is missing")
    if authority.get("development_learning_implementation_authorized") is not True:
        raise StageLearningRunError("E implementation authority is not open")
    if authority.get("training_authorized") is not True:
        raise StageLearningRunError("development semantic training authority is not open")
    if any(
        authority.get(key) is not False
        for key in (
            "market_result_access_authorized",
            "sealed_partition_access_authorized",
            "daily_schedule_authorized",
            "cloud_purchase_authorized",
        )
    ):
        raise StageLearningRunError("E authority unexpectedly opens a forbidden downstream scope")
    return plan


def run_development_learning(
    *,
    pilot_artifact: Path,
    resolution_artifact: Path,
    training_root: Path,
    output_root: Path,
    code_revision: str,
) -> dict[str, Any]:
    if not _REVISION.fullmatch(code_revision):
        raise StageLearningRunError("learning Run requires exact 40-character Git revision")
    started = time.perf_counter()
    root = find_project_root()
    plan = _require_authority(root)
    pilot_root = pilot_artifact.expanduser().resolve()
    resolution_root = resolution_artifact.expanduser().resolve()
    pilot_verified = verify_reference_pilot_artifact(pilot_root)
    resolution_verified = verify_resolution_artifact(resolution_root)
    if pilot_verified.get("independent_reference_complete") is not False:
        raise StageLearningRunError("unexpected D pilot semantic authority")
    if resolution_verified.get("resolved_count") != 120:
        raise StageLearningRunError(
            "E requires all 120 D pilot contexts to be independently resolved"
        )

    pilot_manifest = _object(pilot_root / "manifest.json", "D pilot manifest")
    resolution_manifest = _object(resolution_root / "manifest.json", "D resolution manifest")
    sample_manifest = _object(pilot_root / "sample-manifest.json", "D sample manifest")
    if resolution_manifest.get("sample_manifest_digest") != digest(sample_manifest):
        raise StageLearningRunError("D resolution does not bind the supplied pilot sample")

    config = _object(root / "config/stage-learning/reference-pilot.v1.json", "D pilot config")
    if pilot_manifest.get("config_digest") != digest(config):
        raise StageLearningRunError("D pilot config drifted since artifact publication")
    experiment = _object(root / str(config["experiment_config"]), "stage experiment")
    node_config = _object(root / str(config["training_node_config"]), "training node config")
    population = config.get("population")
    if not isinstance(population, Mapping):
        raise StageLearningRunError("D population config is missing")
    scan_start = _date(population.get("scan_start"), "scan_start")
    report_start = _date(population.get("report_start"), "report_start")
    read_end = _date(population.get("read_end"), "read_end")
    if read_end > date(2021, 12, 31):
        raise StageLearningRunError("E refuses post-development read scope")

    contexts = load_reference_contexts(pilot_root / "sample-manifest.json")
    references = load_resolved_references(resolution_root)
    if {item.context_id for item in contexts} != {item.context_id for item in references}:
        raise StageLearningRunError("resolved reference identities differ from D sample contexts")
    cohort = sample_manifest.get("cohort")
    if not isinstance(cohort, list) or not cohort:
        raise StageLearningRunError("D sample cohort is missing")
    security_codes = tuple(str(item) for item in cohort)
    spec_digest = str(_object(pilot_root / "summary.json", "D summary")["spec_digest"])
    scope = DevelopmentReadScope(scan_start, report_start, read_end)
    admissions, source_manifest, policy = read_development_admissions(
        training_root=training_root,
        node_config=node_config,
        scope=scope,
        security_codes=security_codes,
        spec_digest=spec_digest,
    )
    structure = build_structure_v2(
        admissions,
        spec_digest=spec_digest,
        calendar_ref=policy.calendar_ref,
        lookbacks=(20, 60),
    )
    activity = build_activity_observations(admissions, window=20)
    observations = build_learning_observations(contexts, structure, activity)

    r0 = r0_parameters(experiment)
    r0_predictions = {item.context_id: predict_stage(item, r0) for item in observations}
    r0_score = score_predictions(references, r0_predictions, trial_id="R0:" + r0.trial_id)
    r0_row = {
        "family": "R0",
        "trial_id": "R0:" + r0.trial_id,
        "parameters": r0.to_dict(),
        "score": r0_score.to_dict(),
        "development_pilot_only": True,
    }

    rows: list[dict[str, Any]] = []
    for params in enumerate_r1_trials(experiment):
        predictions = {item.context_id: predict_stage(item, params) for item in observations}
        score = score_predictions(references, predictions, trial_id=params.trial_id)
        rows.append(
            {
                "family": "R1",
                "trial_id": params.trial_id,
                "parameters": params.to_dict(),
                "score": score.to_dict(),
                "development_pilot_only": True,
            }
        )
    ranked = sorted(rows, key=lambda item: _rank_trial(item, r0_row))
    for rank, row in enumerate(ranked, 1):
        row["development_rank"] = rank
    best = ranked[0]

    summary = {
        "schema_version": "quanthus.stage-learning-summary.v1",
        "work_item": "P3-STAGE-001-E",
        "code_revision": code_revision,
        "pilot_manifest_sha256": pilot_verified["manifest_sha256"],
        "resolution_manifest_sha256": resolution_verified["manifest_sha256"],
        "source_manifest_digest": digest(source_manifest),
        "reference_count": len(references),
        "observation_count": len(observations),
        "r0": r0_row,
        "r1_trial_count": len(rows),
        "development_rank_1_trial_id": best["trial_id"],
        "development_rank_1_score": best["score"],
        "r2_executed": False,
        "qualification_authorized": False,
        "confirmatory_test_authority": False,
        "future_settlement_read": False,
        "arbitration_read": False,
        "permanent_blind_read": False,
        "elapsed_seconds": time.perf_counter() - started,
        "claim_limits": [
            (
                "the D feasibility pilot may be used for development learning but never "
                "confirmatory qualification"
            ),
            "development_rank_1 is not a QUALIFIED label version",
            "future settlement is not read by the semantic learner",
            "R2 remains disabled until R1 has an attributable expression shortfall",
        ],
    }
    run_id = "stage-learning-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_root = output_root.expanduser().resolve() / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    payloads = {
        "r0.json": canonical(r0_row),
        "r1-trials.jsonl": _jsonl(tuple(rows)),
        "summary.json": canonical(summary),
    }
    for name, payload in payloads.items():
        (run_root / name).write_bytes(payload)
    manifest = {
        "schema_version": "quanthus.stage-learning-artifact.v1",
        "run_id": run_id,
        "code_revision": code_revision,
        "delivery_plan_digest": digest(plan),
        "experiment_digest": digest(experiment),
        "pilot_manifest_sha256": pilot_verified["manifest_sha256"],
        "resolution_manifest_sha256": resolution_verified["manifest_sha256"],
        "files": {name: _sha256(payload) for name, payload in sorted(payloads.items())},
        "qualification_authorized": False,
        "future_settlement_read": False,
        "sealed_partition_read": False,
    }
    manifest_bytes = canonical(manifest)
    (run_root / "manifest.json").write_bytes(manifest_bytes)
    ready = _sha256(manifest_bytes)
    (run_root / "READY").write_text(ready + "\n", encoding="ascii")
    return {**summary, "run_id": run_id, "run_root": str(run_root), "manifest_sha256": ready}


def verify_learning_artifact(path: Path, *, code_revision: str | None = None) -> dict[str, Any]:
    root = path.expanduser().resolve()
    manifest = _object(root / "manifest.json", "stage learning manifest")
    if manifest.get("schema_version") != "quanthus.stage-learning-artifact.v1":
        raise StageLearningRunError("stage learning artifact schema differs")
    manifest_bytes = canonical(manifest)
    try:
        ready = (root / "READY").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise StageLearningRunError("stage learning READY is unreadable") from exc
    if ready != _sha256(manifest_bytes):
        raise StageLearningRunError("stage learning READY does not bind manifest")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise StageLearningRunError("stage learning code revision differs")
    files = manifest.get("files")
    if not isinstance(files, Mapping):
        raise StageLearningRunError("stage learning file inventory is missing")
    for name, expected in files.items():
        if not isinstance(name, str) or not isinstance(expected, str):
            raise StageLearningRunError("stage learning file inventory is invalid")
        if _sha256((root / name).read_bytes()) != expected:
            raise StageLearningRunError(f"stage learning file digest differs: {name}")
    if (
        manifest.get("qualification_authorized") is not False
        or manifest.get("future_settlement_read") is not False
        or manifest.get("sealed_partition_read") is not False
    ):
        raise StageLearningRunError("stage learning artifact exceeds development E authority")
    return {
        "run_id": manifest["run_id"],
        "manifest_sha256": ready,
        "file_count": len(files),
        "verified": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--pilot-artifact", type=Path, required=True)
    run.add_argument("--resolution-artifact", type=Path, required=True)
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
        result = run_development_learning(
            pilot_artifact=args.pilot_artifact,
            resolution_artifact=args.resolution_artifact,
            training_root=args.training_root,
            output_root=args.output_root,
            code_revision=args.code_revision,
        )
    else:
        result = verify_learning_artifact(args.artifact, code_revision=args.code_revision)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

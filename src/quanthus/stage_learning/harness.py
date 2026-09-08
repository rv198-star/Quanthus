"""Bounded real-DWD P3-STAGE-001-C historical harness.

The harness builds engineering-only stage-3/4 candidate records from accepted StructureV2 facts
plus a causal volume-ratio20 fact. It never trains a classifier, reads future targets, publishes an
official LabelCard or emits a trading decision.
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
from quanthus.stage_learning.core import (
    ActivityEngine,
    Stage34DecisionEngine,
    build_activity_observations,
    build_stage34_decisions,
)
from quanthus.stage_learning.oracle import validate_stage34_records
from quanthus.stage_learning.source import DevelopmentReadScope, read_development_admissions
from quanthus.structure_v2 import build_structure_v2
from quanthus.structure_v2_inputs import canonical, digest

SCHEMA = "quanthus.stage-learning-harness.v1"
DEFAULT_CONFIG = "config/stage-learning/harness.v1.json"
_SHA = re.compile(r"^[0-9a-f]{40}$")


class StageHarnessError(ValueError):
    """Raised when a harness run crosses the frozen C contract."""


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StageHarnessError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise StageHarnessError(f"{label} must be an object")
    return value


def load_harness_config(path: Path) -> dict[str, Any]:
    value = _object(path, "stage harness config")
    if value.get("schema_version") != SCHEMA or value.get("work_item") != "P3-STAGE-001-C":
        raise StageHarnessError("stage harness config identity differs")
    authority = value.get("authority")
    if not isinstance(authority, Mapping):
        raise StageHarnessError("stage harness authority is missing")
    if authority.get("implementation_authorized") is not True or authority.get(
        "engineering_candidate_output_authorized"
    ) is not True:
        raise StageHarnessError("stage harness implementation authority is not open")
    denied = (
        "official_label_publication_authorized",
        "classifier_training_authorized",
        "future_target_access_authorized",
        "arbitration_access_authorized",
        "permanent_blind_access_authorized",
        "strategy_consumption_authorized",
        "daily_schedule_authorized",
    )
    if any(authority.get(key) is not False for key in denied):
        raise StageHarnessError("stage harness config grants forbidden downstream authority")
    guard = value.get("development_read_guard")
    if not isinstance(guard, Mapping) or guard.get("maximum_date") != "2021-12-31":
        raise StageHarnessError("stage harness development guard differs")
    return value


def _scope(config: Mapping[str, Any]) -> DevelopmentReadScope:
    acceptance = config.get("acceptance_slice")
    if not isinstance(acceptance, Mapping):
        raise StageHarnessError("acceptance slice is missing")
    try:
        return DevelopmentReadScope(
            date.fromisoformat(str(acceptance["scan_start"])),
            date.fromisoformat(str(acceptance["report_start"])),
            date.fromisoformat(str(acceptance["report_end"])),
        )
    except (KeyError, ValueError) as exc:
        raise StageHarnessError("acceptance slice dates are invalid") from exc


def _codes(config: Mapping[str, Any]) -> tuple[str, ...]:
    acceptance = config.get("acceptance_slice")
    if not isinstance(acceptance, Mapping) or not isinstance(
        acceptance.get("security_codes"), list
    ):
        raise StageHarnessError("acceptance security codes are missing")
    codes = tuple(acceptance["security_codes"])
    if not codes or len(codes) > 3 or any(not isinstance(code, str) for code in codes):
        raise StageHarnessError("bounded C acceptance requires 1..3 security codes")
    return codes


def _rules(config: Mapping[str, Any]) -> dict[str, Any]:
    raw = config.get("r0_engineering_rules")
    if not isinstance(raw, Mapping):
        raise StageHarnessError("R0 engineering rule block is missing")
    try:
        return {
            "probe_volume_ratio_min": Decimal(str(raw["probe_volume_ratio20_min"])),
            "acceptance_hold_sessions": int(raw["acceptance_hold_sessions"]),
            "acceptance_signed_distance_min": Decimal(
                str(raw["acceptance_signed_distance_min"])
            ),
            "acceptance_window_volume_ratio_min": Decimal(
                str(raw["acceptance_window_volume_ratio20_min"])
            ),
        }
    except (KeyError, ValueError) as exc:
        raise StageHarnessError("R0 engineering rules are invalid") from exc


def _fact_spec(config: Mapping[str, Any], experiment: Mapping[str, Any]) -> str:
    return digest(
        {
            "schema_version": "quanthus.stage-3-4-engineering-definition.v1",
            "experiment_id": experiment.get("experiment_id"),
            "first_group": experiment.get("first_group"),
            "facts": config.get("facts"),
            "r0_engineering_rules": config.get("r0_engineering_rules"),
            "development_read_guard": config.get("development_read_guard"),
            "official_label": False,
        }
    )


def _exact_dicts(values: Sequence[Any]) -> list[dict[str, Any]]:
    return [item.to_dict() for item in values]


def _assert_incremental_parity(
    admissions: tuple[Any, ...], structure: Any, activity: Any, rules: Any
) -> dict[str, Any]:
    ordered_admissions = tuple(
        sorted(admissions, key=lambda item: (item.row.security_code, item.row.market_date))
    )
    split = max(1, len(ordered_admissions) // 2)
    activity_engine = ActivityEngine(window=20)
    activity_incremental = (
        *activity_engine.consume(ordered_admissions[:split]),
        *activity_engine.consume(ordered_admissions[split:]),
    )
    if _exact_dicts(activity_incremental) != _exact_dicts(activity):
        raise StageHarnessError("activity batch/incremental output differs")

    activity_by_key = {item.key: item for item in activity}
    episode_order = tuple(
        sorted(
            structure.episodes,
            key=lambda item: (item.security_code, item.market_date, item.scale, item.episode_id),
        )
    )
    episode_split = max(1, len(episode_order) // 2) if episode_order else 0
    engine = Stage34DecisionEngine(activity_by_key, **rules)
    first_candidates, first_decisions = engine.consume(episode_order[:episode_split])
    second_candidates, second_decisions = engine.consume(episode_order[episode_split:])
    incremental_candidates = (*first_candidates, *second_candidates)
    incremental_decisions = (*first_decisions, *second_decisions)
    candidates, decisions = build_stage34_decisions(structure, activity, **rules)
    if _exact_dicts(incremental_candidates) != _exact_dicts(candidates):
        raise StageHarnessError("stage candidate batch/incremental output differs")
    if _exact_dicts(incremental_decisions) != _exact_dicts(decisions):
        raise StageHarnessError("stage decision batch/incremental output differs")
    return {
        "activity_batch_incremental_exact": True,
        "candidate_batch_incremental_exact": True,
        "decision_batch_incremental_exact": True,
    }


def _assert_future_suffix_non_rewrite(
    admissions: tuple[Any, ...], *, spec_digest: str, calendar_ref: str, rules: Any
) -> dict[str, Any]:
    days = sorted({item.row.market_date for item in admissions})
    if len(days) < 4:
        raise StageHarnessError("acceptance slice is too short for future-suffix probe")
    cutoff = days[len(days) // 2]
    prefix = tuple(item for item in admissions if item.row.market_date <= cutoff)
    full_structure = build_structure_v2(
        admissions, spec_digest=spec_digest, calendar_ref=calendar_ref, lookbacks=(20, 60)
    )
    prefix_structure = build_structure_v2(
        prefix, spec_digest=spec_digest, calendar_ref=calendar_ref, lookbacks=(20, 60)
    )
    full_activity = build_activity_observations(admissions)
    prefix_activity = build_activity_observations(prefix)
    full_candidates, full_decisions = build_stage34_decisions(
        full_structure, full_activity, **rules
    )
    prefix_candidates, prefix_decisions = build_stage34_decisions(
        prefix_structure, prefix_activity, **rules
    )
    before_candidates = [item.to_dict() for item in full_candidates if item.market_date <= cutoff]
    before_decisions = [item.to_dict() for item in full_decisions if item.market_date <= cutoff]
    if before_candidates != _exact_dicts(prefix_candidates):
        raise StageHarnessError("future suffix rewrote an earlier candidate")
    if before_decisions != _exact_dicts(prefix_decisions):
        raise StageHarnessError("future suffix rewrote an earlier decision")
    return {"cutoff": cutoff.isoformat(), "earlier_candidate_and_decision_records_exact": True}


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _write_jsonl(path: Path, rows: Sequence[Any]) -> bytes:
    payload = b"".join(canonical(item.to_dict()) + b"\n" for item in rows)
    path.write_bytes(payload)
    return payload


def run_harness(
    *,
    config_path: Path,
    training_root: Path,
    output_root: Path,
    code_revision: str,
) -> dict[str, Any]:
    if not _SHA.fullmatch(code_revision):
        raise StageHarnessError("exact 40-character code revision is required")
    root = find_project_root()
    config = load_harness_config(config_path)
    experiment_path = root / str(config["experiment_config"])
    node_path = root / str(config["training_node_config"])
    experiment = _object(experiment_path, "stage experiment")
    node_config = _object(node_path, "training node config")
    if experiment.get("delivery_authority", {}).get("classifier_training_authorized") is not False:
        raise StageHarnessError("experiment unexpectedly authorizes classifier training")
    scope = _scope(config)
    codes = _codes(config)
    rules = _rules(config)
    spec_digest = _fact_spec(config, experiment)
    started = time.perf_counter()
    admissions, source_manifest, policy = read_development_admissions(
        training_root=training_root,
        node_config=node_config,
        scope=scope,
        security_codes=codes,
        spec_digest=spec_digest,
    )
    structure = build_structure_v2(
        admissions,
        spec_digest=spec_digest,
        calendar_ref=policy.calendar_ref,
        lookbacks=(20, 60),
    )
    activity = build_activity_observations(admissions, window=20)
    candidates, decisions = build_stage34_decisions(structure, activity, **rules)
    parity = _assert_incremental_parity(admissions, structure, activity, rules)
    suffix = _assert_future_suffix_non_rewrite(
        admissions, spec_digest=spec_digest, calendar_ref=policy.calendar_ref, rules=rules
    )
    oracle = validate_stage34_records(candidates, decisions)
    report_candidates = tuple(
        item for item in candidates if scope.report_start <= item.market_date <= scope.report_end
    )
    report_decisions = tuple(
        item for item in decisions if scope.report_start <= item.market_date <= scope.report_end
    )
    report_activity = tuple(
        item for item in activity if scope.report_start <= item.market_date <= scope.report_end
    )
    if any(item.official_label for item in (*report_candidates, *report_decisions)):
        raise StageHarnessError("official label authority leaked into C artifact")
    summary = {
        "schema_version": "quanthus.stage-learning-harness-summary.v1",
        "work_item": "P3-STAGE-001-C",
        "code_revision": code_revision,
        "scope": scope.to_dict(),
        "security_codes": codes,
        "spec_digest": spec_digest,
        "knowledge_view": "retrospective_dwd_record_view",
        "engineering_only": True,
        "official_label": False,
        "classifier_training_performed": False,
        "future_target_read": False,
        "source_counts": {
            "attempted": source_manifest["attempted"],
            "admitted": source_manifest["admitted"],
            "rejected": source_manifest["rejected"],
        },
        "fact_counts": {
            "structure_measurements": len(structure.measurements),
            "structure_interactions": len(structure.interactions),
            "structure_episode_observations": len(structure.episodes),
            "activity_observations": len(report_activity),
        },
        "candidate_counts": {
            "total": len(report_candidates),
            "stage_3": sum(item.stage_code == 3 for item in report_candidates),
            "stage_4": sum(item.stage_code == 4 for item in report_candidates),
        },
        "decision_counts": {
            "total": len(report_decisions),
            "confirmed_3": sum(item.primary_stage == 3 for item in report_decisions),
            "confirmed_4": sum(item.primary_stage == 4 for item in report_decisions),
            "abstain": sum(item.inference_status.value == "ABSTAIN" for item in report_decisions),
            "rule_conflict": sum(
                item.inference_status.value == "RULE_CONFLICT" for item in report_decisions
            ),
        },
        "checks": {**parity, "future_suffix": suffix, "oracle": oracle},
        "elapsed_seconds": time.perf_counter() - started,
        "claim_limits": [
            "engineering candidates are not official market-stage labels",
            "no human semantic truth or future target is used in this C artifact",
            "no classifier/model fitting occurs",
            "the full Base is physically present on RX6800; holdout isolation is logical only",
            (
                "C validates mechanics and causal evidence closure, not semantic accuracy or "
                "profitability"
            ),
        ],
    }

    run_id = "stage-harness-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_root = output_root.expanduser().resolve() / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    files: dict[str, bytes] = {}
    files["source-manifest.json"] = canonical(source_manifest)
    files["summary.json"] = canonical(summary)
    for name, rows in (
        ("activity.jsonl", report_activity),
        ("candidates.jsonl", report_candidates),
        ("decisions.jsonl", report_decisions),
    ):
        files[name] = b"".join(canonical(item.to_dict()) + b"\n" for item in rows)
    files["runtime.json"] = canonical(
        {
            "schema_version": "quanthus.stage-learning-runtime.v1",
            "job_concurrency": 1,
            "logical_cpu_threads_max": 8,
            "raw_market_price_output": False,
            "source_read_guard": source_manifest["read_guard"],
            "business_output_bytes": sum(len(value) for value in files.values()),
        }
    )
    for name, payload in files.items():
        (run_root / name).write_bytes(payload)
    manifest = {
        "schema_version": "quanthus.stage-learning-artifact.v1",
        "run_id": run_id,
        "code_revision": code_revision,
        "config_digest": digest(config),
        "experiment_digest": digest(experiment),
        "node_config_digest": digest(node_config),
        "source_manifest_digest": digest(source_manifest),
        "summary_digest": digest(summary),
        "files": {name: _sha256_bytes(payload) for name, payload in sorted(files.items())},
        "official_label": False,
        "classifier_training_performed": False,
        "future_target_read": False,
    }
    manifest_bytes = canonical(manifest)
    (run_root / "manifest.json").write_bytes(manifest_bytes)
    (run_root / "READY").write_text(_sha256_bytes(manifest_bytes) + "\n", encoding="ascii")
    return {
        **summary,
        "run_id": run_id,
        "run_root": str(run_root),
        "manifest_sha256": _sha256_bytes(manifest_bytes),
    }


def verify_harness_artifact(path: Path, *, code_revision: str | None = None) -> dict[str, Any]:
    root = path.expanduser().resolve()
    manifest = _object(root / "manifest.json", "stage harness manifest")
    if manifest.get("schema_version") != "quanthus.stage-learning-artifact.v1":
        raise StageHarnessError("stage harness artifact schema differs")
    manifest_bytes = canonical(manifest)
    ready = (root / "READY").read_text(encoding="ascii").strip()
    if ready != _sha256_bytes(manifest_bytes):
        raise StageHarnessError("stage harness READY does not bind manifest")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise StageHarnessError("stage harness code revision differs")
    files = manifest.get("files")
    if not isinstance(files, Mapping):
        raise StageHarnessError("stage harness file inventory is missing")
    for name, expected in files.items():
        if not isinstance(name, str) or not isinstance(expected, str):
            raise StageHarnessError("stage harness file inventory is invalid")
        try:
            observed = _sha256_bytes((root / name).read_bytes())
        except OSError as exc:
            raise StageHarnessError(f"stage harness file is unreadable: {name}") from exc
        if observed != expected:
            raise StageHarnessError(f"stage harness file digest differs: {name}")
    if manifest.get("official_label") is not False or manifest.get(
        "classifier_training_performed"
    ) is not False or manifest.get("future_target_read") is not False:
        raise StageHarnessError("stage harness artifact exceeds C authority")
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
        result = run_harness(
            config_path=args.config,
            training_root=args.training_root,
            output_root=args.output_root,
            code_revision=args.code_revision,
        )
    else:
        result = verify_harness_artifact(args.artifact, code_revision=args.code_revision)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Governed development-only T-only reference pilot and future-settlement runner."""
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
from quanthus.stage_learning.core import build_activity_observations
from quanthus.stage_learning.harness import load_harness_config
from quanthus.stage_learning.reference import (
    ReferencePilotError,
    build_reviewer_packet,
    select_reference_contexts,
)
from quanthus.stage_learning.settlement import settle_reference_contexts, settlement_ledger_summary
from quanthus.stage_learning.source import (
    DevelopmentReadScope,
    discover_development_security_codes,
    read_development_admissions,
)
from quanthus.structure_v2 import build_structure_v2
from quanthus.structure_v2_inputs import canonical, digest

DEFAULT_CONFIG = "config/stage-learning/reference-pilot.v1.json"
_HARNESS_CONFIG = "config/stage-learning/harness.v1.json"
_REVISION = re.compile(r"^[0-9a-f]{40}$")


class ReferencePilotRunError(ValueError):
    """Raised when the governed D pilot cannot be executed or verified."""


def _progress(started: float, stage: str, **details: Any) -> None:
    print(
        json.dumps(
            {
                "event": "stage-reference-pilot-progress",
                "stage": stage,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                **details,
            },
            ensure_ascii=True,
            sort_keys=True,
        ),
        flush=True,
    )


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReferencePilotRunError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ReferencePilotRunError(f"{label} must be a JSON object")
    return value


def load_reference_pilot_config(path: Path) -> dict[str, Any]:
    raw = _object(path, "stage reference pilot config")
    expected = {
        "schema_version",
        "work_item",
        "experiment_config",
        "training_node_config",
        "authority",
        "population",
        "sampling",
        "review",
        "future_target",
        "artifact",
    }
    if set(raw) != expected or raw.get("schema_version") != "quanthus.stage-reference-pilot.v1":
        raise ReferencePilotRunError("stage reference pilot config schema differs")
    if raw.get("work_item") != "P3-STAGE-001-D":
        raise ReferencePilotRunError("stage reference pilot work item differs")
    authority = raw.get("authority")
    if not isinstance(authority, Mapping):
        raise ReferencePilotRunError("stage reference pilot authority is missing")
    required_true = {
        "implementation_authorized",
        "development_reference_pack_authorized",
        "development_future_settlement_authorized",
    }
    required_false = {
        "classifier_training_authorized",
        "arbitration_access_authorized",
        "permanent_blind_access_authorized",
        "qualification_authorized",
        "strategy_consumption_authorized",
    }
    if any(authority.get(key) is not True for key in required_true) or any(
        authority.get(key) is not False for key in required_false
    ):
        raise ReferencePilotRunError("stage reference pilot authority differs")
    return raw


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _fact_spec(root: Path, experiment: Mapping[str, Any]) -> str:
    harness = load_harness_config(root / _HARNESS_CONFIG)
    return digest(
        {
            "schema_version": "quanthus.stage-3-4-engineering-definition.v1",
            "experiment_id": experiment.get("experiment_id"),
            "first_group": experiment.get("first_group"),
            "facts": harness.get("facts"),
            "r0_engineering_rules": harness.get("r0_engineering_rules"),
            "development_read_guard": harness.get("development_read_guard"),
            "official_label": False,
        }
    )


def _date(value: Mapping[str, Any], key: str) -> date:
    try:
        return date.fromisoformat(str(value[key]))
    except (KeyError, ValueError) as exc:
        raise ReferencePilotRunError(f"invalid reference pilot date: {key}") from exc


def _int(value: Mapping[str, Any], key: str) -> int:
    raw = value.get(key)
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 1:
        raise ReferencePilotRunError(f"invalid positive integer: {key}")
    return raw


def _jsonl(values: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(canonical(dict(value)) + b"\n" for value in values)


def run_reference_pilot(
    *,
    config_path: Path,
    training_root: Path,
    output_root: Path,
    code_revision: str,
) -> dict[str, Any]:
    if not _REVISION.fullmatch(code_revision):
        raise ReferencePilotRunError("reference pilot requires exact Git revision")
    started = time.perf_counter()
    root = find_project_root()
    config = load_reference_pilot_config(config_path)
    experiment = _object(root / str(config["experiment_config"]), "stage experiment")
    node_config = _object(root / str(config["training_node_config"]), "training node config")
    population = config["population"]
    sampling = config["sampling"]
    review = config["review"]
    target = config["future_target"]
    if not all(isinstance(item, Mapping) for item in (population, sampling, review, target)):
        raise ReferencePilotRunError("reference pilot config sections are invalid")
    population = dict(population)
    sampling = dict(sampling)
    review = dict(review)
    target = dict(target)

    scan_start = _date(population, "scan_start")
    cohort_date = _date(population, "cohort_date")
    report_start = _date(population, "report_start")
    report_end = _date(population, "report_end")
    read_end = _date(population, "read_end")
    if not scan_start < cohort_date <= report_start <= report_end < read_end <= date(2021, 12, 31):
        raise ReferencePilotRunError("reference pilot development dates are not ordered")
    if _int(sampling, "random_count") + _int(sampling, "boundary_or_rare_count") != _int(
        sampling, "total_episode_contexts"
    ):
        raise ReferencePilotRunError("reference pilot sample counts do not close")
    if review.get("algorithm_output_visible_to_primary_reviewers") is not False or review.get(
        "future_settlement_visible_to_primary_reviewers"
    ) is not False:
        raise ReferencePilotRunError("primary reviewer blinding is not frozen")
    if review.get("primary_reviewers_per_context") != 2 or review.get(
        "third_party_adjudication_on_disagreement"
    ) is not True:
        raise ReferencePilotRunError("independent review contract differs")

    cohort = discover_development_security_codes(
        training_root=training_root,
        node_config=node_config,
        scan_start=scan_start,
        cohort_date=cohort_date,
        minimum_observations=_int(population, "minimum_pre_cohort_observations"),
        cohort_size=_int(population, "cohort_size"),
        seed=str(population["cohort_seed"]),
    )
    _progress(started, "cohort-ready", cohort_size=len(cohort))
    scope = DevelopmentReadScope(scan_start, report_start, read_end)
    spec_digest = _fact_spec(root, experiment)
    admissions, source_manifest, policy = read_development_admissions(
        training_root=training_root,
        node_config=node_config,
        scope=scope,
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
    _progress(started, "structure-ready", episodes=len(structure.episodes))
    activity = build_activity_observations(admissions, window=20)
    _progress(started, "activity-ready", activity=len(activity))
    contexts = select_reference_contexts(
        structure.episodes,
        activity,
        report_start=report_start,
        report_end=report_end,
        seed=str(sampling["sampling_seed"]),
        random_count=_int(sampling, "random_count"),
        boundary_count=_int(sampling, "boundary_or_rare_count"),
    )
    _progress(started, "contexts-ready", contexts=len(contexts))
    packets = build_reviewer_packet(
        contexts,
        structure.episodes,
        activity,
        admissions,
        history_sessions=_int(sampling, "review_history_sessions"),
    )
    settlements = settle_reference_contexts(
        contexts,
        structure.episodes,
        admissions,
        horizon_sessions=_int(target, "horizon_trading_sessions"),
        positive_threshold=Decimal(str(target["positive_threshold"])),
        adverse_threshold=Decimal(str(target["adverse_threshold"])),
        same_session_overlap_policy=str(target["same_session_overlap_policy"]),
    )
    _progress(started, "settlements-ready", settlements=len(settlements))
    if len(contexts) != _int(sampling, "total_episode_contexts") or len(packets) != len(contexts):
        raise ReferencePilotRunError("reference pilot sample publication count differs")
    if len(settlements) != len(contexts):
        raise ReferencePilotRunError("reference pilot settlement count differs")
    context_ids = {item.context_id for item in contexts}
    if {item.context_id for item in settlements} != context_ids:
        raise ReferencePilotRunError("reference and settlement context identities differ")
    visible_keys = [
        key
        for packet in packets
        for key in packet
        if key != "forbidden_fields"
    ]
    if any("future" in key.lower() for key in visible_keys):
        raise ReferencePilotRunError("review packet leaked a future field")
    if any("candidate" in key.lower() for key in visible_keys):
        raise ReferencePilotRunError("review packet leaked an engineering candidate field")

    summary = {
        "schema_version": "quanthus.stage-reference-pilot-summary.v1",
        "work_item": "P3-STAGE-001-D",
        "code_revision": code_revision,
        "spec_digest": spec_digest,
        "cohort_size": len(cohort),
        "cohort_digest": digest(cohort),
        "sample_counts": {
            "total": len(contexts),
            "random": sum(item.sampling_stratum == "random" for item in contexts),
            "boundary_or_rare": sum(
                item.sampling_stratum == "boundary_or_rare" for item in contexts
            ),
        },
        "review_status": {
            "resolved_contexts": 0,
            "required_contexts": len(contexts),
            "semantic_reference_status": "INCONCLUSIVE_PENDING_INDEPENDENT_REVIEW",
            "pilot_may_be_confirmatory_test": False,
        },
        "settlement_counts": settlement_ledger_summary(settlements),
        "source_counts": {
            "attempted": source_manifest["attempted"],
            "admitted": source_manifest["admitted"],
            "rejected": source_manifest["rejected"],
        },
        "read_guard": source_manifest["read_guard"],
        "future_target_is_semantic_truth": False,
        "classifier_training_performed": False,
        "arbitration_read": False,
        "permanent_blind_read": False,
        "elapsed_seconds": time.perf_counter() - started,
        "claim_limits": [
            "the 120-context pilot estimates reference feasibility only",
            "no independent human/third-party semantic judgement is fabricated by this runner",
            "future settlements are development-only and cannot define stage 3/4 semantics",
            "the pilot cohort is fixed at 2020-01-02 and is not the final qualification population",
        ],
    }

    run_id = "stage-reference-pilot-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_root = output_root.expanduser().resolve() / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    payloads: dict[str, bytes] = {
        "source-manifest.json": canonical(source_manifest),
        "sample-manifest.json": canonical(
            {
                "schema_version": "quanthus.stage-reference-sample-manifest.v1",
                "cohort": cohort,
                "cohort_digest": digest(cohort),
                "contexts": [item.to_dict() for item in contexts],
                "sampling": sampling,
                "report_start": report_start,
                "report_end": report_end,
            }
        ),
        "review-packets.jsonl": _jsonl(tuple(dict(item) for item in packets)),
        "review-ledger-template.jsonl": b"",
        "settlements.jsonl": _jsonl(tuple(item.to_dict() for item in settlements)),
        "summary.json": canonical(summary),
    }
    for name, payload in payloads.items():
        (run_root / name).write_bytes(payload)
    manifest = {
        "schema_version": "quanthus.stage-reference-pilot-artifact.v1",
        "run_id": run_id,
        "code_revision": code_revision,
        "config_digest": digest(config),
        "experiment_digest": digest(experiment),
        "node_config_digest": digest(node_config),
        "source_manifest_digest": digest(source_manifest),
        "summary_digest": digest(summary),
        "files": {name: _sha256_bytes(payload) for name, payload in sorted(payloads.items())},
        "review_packet_future_free": True,
        "review_packet_candidate_verdict_free": True,
        "independent_reference_complete": False,
        "qualification_authorized": False,
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


def verify_reference_pilot_artifact(
    path: Path,
    *,
    code_revision: str | None = None,
) -> dict[str, Any]:
    root = path.expanduser().resolve()
    manifest = _object(root / "manifest.json", "reference pilot manifest")
    if manifest.get("schema_version") != "quanthus.stage-reference-pilot-artifact.v1":
        raise ReferencePilotRunError("reference pilot artifact schema differs")
    manifest_bytes = canonical(manifest)
    ready = (root / "READY").read_text(encoding="ascii").strip()
    if ready != _sha256_bytes(manifest_bytes):
        raise ReferencePilotRunError("reference pilot READY does not bind manifest")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise ReferencePilotRunError("reference pilot code revision differs")
    files = manifest.get("files")
    if not isinstance(files, Mapping):
        raise ReferencePilotRunError("reference pilot file inventory is missing")
    for name, expected in files.items():
        if not isinstance(name, str) or not isinstance(expected, str):
            raise ReferencePilotRunError("reference pilot file inventory is invalid")
        observed = _sha256_bytes((root / name).read_bytes())
        if observed != expected:
            raise ReferencePilotRunError(f"reference pilot file digest differs: {name}")
    if manifest.get("review_packet_future_free") is not True or manifest.get(
        "review_packet_candidate_verdict_free"
    ) is not True:
        raise ReferencePilotRunError("reference pilot reviewer blinding claim differs")
    if manifest.get("independent_reference_complete") is not False or manifest.get(
        "qualification_authorized"
    ) is not False:
        raise ReferencePilotRunError("reference pilot exceeds D engineering authority")
    return {
        "run_id": manifest["run_id"],
        "manifest_sha256": ready,
        "file_count": len(files),
        "verified": True,
        "independent_reference_complete": False,
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
    try:
        if args.command == "run":
            result = run_reference_pilot(
                config_path=args.config,
                training_root=args.training_root,
                output_root=args.output_root,
                code_revision=args.code_revision,
            )
        else:
            result = verify_reference_pilot_artifact(
                args.artifact,
                code_revision=args.code_revision,
            )
    except ReferencePilotError as exc:
        raise ReferencePilotRunError(str(exc)) from exc
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

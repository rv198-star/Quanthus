"""P3-STAGE-011-D1 fresh 2018 joint Stage0/Stage2 development prepare."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.stage_learning.directional_boundary_calibration_prepare import (
    verify_artifact as verify_stage008_d1_artifact,
)
from quanthus.stage_learning.order_proposition_development import build_order_observations
from quanthus.stage_learning.order_reference_protocol import (
    MAX_BYTES,
    build_evidence,
    load_protocol_contract,
    validate_evidence,
)
from quanthus.stage_learning.order_rule_prepare import verify_prepare_artifact
from quanthus.stage_learning.orderly_consolidation_joint_development import (
    EXECUTION_DIGEST,
    PREREG_DIGEST,
    joint_candidate_grid,
    load_execution,
    load_preregistration,
    stage2_candidate_grid,
    threshold_basis,
)
from quanthus.stage_learning.positive_instability_development_prepare import (
    _combined_feature_rows,
    _fresh_pool,
    _path_map,
    _retained_rows,
    _selected_path_facts,
)
from quanthus.stage_learning.positive_instability_development_prepare import (
    verify_artifact as verify_stage009_d1_artifact,
)
from quanthus.stage_learning.positive_instability_quantitative_development import (
    candidate_grid as stage0_candidate_grid,
)
from quanthus.stage_learning.positive_instability_quantitative_development_prepare import (
    select_development_cases,
)
from quanthus.stage_learning.positive_instability_quantitative_development_prepare import (
    verify_artifact as verify_stage010_d1_artifact,
)
from quanthus.stage_learning.source import (
    DevelopmentReadScope,
    discover_development_security_population,
    read_development_admissions,
    read_development_identity_members,
)
from quanthus.structure_v2_inputs import InputPolicy, canonical, digest

DEFAULT_CONFIG = "config/stage-learning/orderly-consolidation-joint-development-screen.v1.json"
ARTIFACT_SCHEMA = "quanthus.stage-orderly-consolidation-joint-d1-artifact.v1"
SUMMARY_SCHEMA = "quanthus.stage-orderly-consolidation-joint-d1-summary.v1"
_REVISION = re.compile(r"^[0-9a-f]{40}$")
EVIDENCE_SCOPE_PATH = "config/stage-learning/order-reference-evidence-scope-2018.v1.json"
EVIDENCE_SCOPE_DIGEST = "sha256:2400ccd1c025098b0a3d050aebf39cc0ebc7241289d9db2e8425819f9daf76c9"


class OrderlyConsolidationJointPrepareError(ValueError):
    """The P3-STAGE-011-D1 source, isolation or publication contract was violated."""


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OrderlyConsolidationJointPrepareError(f"cannot_read_{label}") from exc
    if not isinstance(value, dict):
        raise OrderlyConsolidationJointPrepareError(f"{label}_must_be_object")
    return value


def _day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise OrderlyConsolidationJointPrepareError("invalid_date") from exc


def _artifact_root(training_root: Path, binding: Mapping[str, Any]) -> Path:
    return (training_root / str(binding["artifact_relative_path"])).resolve()


def _verify_upstream_and_isolation(
    *, root: Path, training_root: Path, config: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, str], set[str], dict[str, Any]]:
    stage007 = config["bindings"]["stage007_prepare"]
    stage007_root = _artifact_root(training_root, stage007)
    stage007_verified = verify_prepare_artifact(stage007_root)
    if stage007_verified["manifest_sha256"] != stage007["manifest_sha256"]:
        raise OrderlyConsolidationJointPrepareError("stage007_prepare_binding")
    stage007_binding = _object(stage007_root / "source-binding.json", "stage007_source_binding")

    stage008 = config["bindings"]["stage008_d1"]
    stage008_root = _artifact_root(training_root, stage008)
    stage008_verified = verify_stage008_d1_artifact(stage008_root)
    if stage008_verified["manifest_sha256"] != stage008["manifest_sha256"]:
        raise OrderlyConsolidationJointPrepareError("stage008_d1_binding")
    stage008_binding = _object(stage008_root / "source-binding.json", "stage008_source_binding")
    stage008_fresh = stage008_binding.get("fresh_pool")
    if not isinstance(stage008_fresh, Mapping) or len(stage008_fresh) != 512:
        raise OrderlyConsolidationJointPrepareError("stage008_fresh_pool_binding")

    stage009 = config["bindings"]["stage009_d1"]
    stage009_root = _artifact_root(training_root, stage009)
    stage009_verified = verify_stage009_d1_artifact(stage009_root)
    if (
        stage009_verified["manifest_sha256"] != stage009["manifest_sha256"]
        or stage009_verified["evaluation_identity_digest"]
        != stage009["evaluation_identity_digest"]
        or stage009_verified["evaluation_plan_digest"] != stage009["evaluation_plan_digest"]
    ):
        raise OrderlyConsolidationJointPrepareError("stage009_d1_binding")
    stage009_binding = _object(stage009_root / "source-binding.json", "stage009_source_binding")
    stage009_fresh = stage009_binding.get("fresh_pool")
    if not isinstance(stage009_fresh, Mapping) or len(stage009_fresh) != 512:
        raise OrderlyConsolidationJointPrepareError("stage009_fresh_pool_binding")
    preserved_plan = _object(stage009_root / "evaluation-plan.json", "stage009_evaluation_plan")
    if (
        digest(preserved_plan) != stage009["evaluation_plan_digest"]
        or preserved_plan.get("identity_digest") != stage009["evaluation_identity_digest"]
        or preserved_plan.get("identity_count") != stage009["evaluation_identity_count"]
        or preserved_plan.get("data_read") is not False
    ):
        raise OrderlyConsolidationJointPrepareError("preserved_evaluation_plan_binding")

    stage010 = config["bindings"]["stage010_d1"]
    stage010_root = _artifact_root(training_root, stage010)
    stage010_verified = verify_stage010_d1_artifact(
        stage010_root, code_revision=str(stage010["code_revision"])
    )
    if (
        stage010_verified["manifest_sha256"] != stage010["manifest_sha256"]
        or stage010_verified["fresh_pool_size"] != stage010["fresh_pool_size"]
    ):
        raise OrderlyConsolidationJointPrepareError("stage010_d1_binding")
    stage010_binding = _object(stage010_root / "source-binding.json", "stage010_source_binding")
    stage010_fresh = stage010_binding.get("fresh_pool")
    if not isinstance(stage010_fresh, Mapping) or len(stage010_fresh) != 512:
        raise OrderlyConsolidationJointPrepareError("stage010_fresh_pool_binding")

    excluded = (
        set(stage007_binding["excluded_economic_ids"])
        | set(stage007_binding["pool"])
        | set(stage008_fresh)
        | set(stage009_fresh)
        | set(stage010_fresh)
    )
    upstream = {
        "stage007_manifest": stage007_verified["manifest_sha256"],
        "stage008_manifest": stage008_verified["manifest_sha256"],
        "stage009_manifest": stage009_verified["manifest_sha256"],
        "stage010_manifest": stage010_verified["manifest_sha256"],
        "stage007_pool_digest": digest(stage007_binding["pool"]),
        "stage008_fresh_pool_digest": digest(dict(stage008_fresh)),
        "stage009_fresh_pool_digest": digest(dict(stage009_fresh)),
        "stage010_fresh_pool_digest": digest(dict(stage010_fresh)),
    }
    return upstream, dict(stage010_fresh), excluded, preserved_plan


def run_prepare(
    *, config_path: Path, training_root: Path, output_root: Path, code_revision: str
) -> dict[str, Any]:
    if not _REVISION.fullmatch(code_revision):
        raise OrderlyConsolidationJointPrepareError("exact_code_revision_required")
    root = find_project_root()
    config = load_preregistration(root)
    execution = load_execution(root)
    evidence_binding = execution["evidence_protocol"]
    if (
        evidence_binding["path"] != EVIDENCE_SCOPE_PATH
        or evidence_binding["digest"] != EVIDENCE_SCOPE_DIGEST
    ):
        raise OrderlyConsolidationJointPrepareError("evidence_scope_execution_binding")
    evidence_protocol = load_protocol_contract(
        EVIDENCE_SCOPE_PATH, EVIDENCE_SCOPE_DIGEST, root
    )
    if config_path.resolve() != (root / DEFAULT_CONFIG).resolve():
        supplied = _object(config_path, "joint_development_screen")
        if digest(supplied) != PREREG_DIGEST:
            raise OrderlyConsolidationJointPrepareError("development_screen_config_drift")

    training_root = training_root.expanduser().resolve()
    upstream, stage010_fresh, excluded, preserved_plan = _verify_upstream_and_isolation(
        root=root, training_root=training_root, config=config
    )
    preserved_ids = {str(item["security_id"]) for item in preserved_plan["identities"]}

    node = _object(root / execution["training_node_config"], "training_node_config")
    scope = config["source_isolation"]
    candidate_codes = discover_development_security_population(
        training_root=training_root,
        node_config=node,
        scan_start=_day(scope["candidate_scan_start"]),
        cohort_date=_day(scope["candidate_cohort_date"]),
        minimum_observations=int(scope["candidate_minimum_observations"]),
        seed=str(scope["candidate_seed"]),
    )
    members = read_development_identity_members(
        training_root=training_root,
        node_config=node,
        security_codes=tuple(candidate_codes),
        start=_day(scope["identity_start"]),
        end=_day(scope["identity_end"]),
        active_on=_day(scope["candidate_cohort_date"]),
    )
    pairs = [
        {"security_code": item["security_code"], "security_id": item["security_id"]}
        for item in members
    ]
    fresh = _fresh_pool(
        pairs,
        excluded_ids=excluded,
        seed=str(scope["fresh_pool_seed"]),
        size=int(scope["fresh_pool_size"]),
    )
    if set(fresh) & set(stage010_fresh):
        raise OrderlyConsolidationJointPrepareError("stage010_fresh_pool_overlap")
    development_pool = {
        identity: fresh[identity]
        for identity in list(fresh)[: int(scope["development_identity_capacity"])]
    }
    if set(development_pool) & preserved_ids:
        raise OrderlyConsolidationJointPrepareError("development_preserved_evaluation_overlap")

    development_scope = DevelopmentReadScope(
        _day(scope["development_history_start"]),
        _day(scope["development_report_start"]),
        _day(scope["development_report_end"]),
    )
    admissions, source_manifest, source_policy = read_development_admissions(
        training_root=training_root,
        node_config=node,
        scope=development_scope,
        security_codes=tuple(sorted(development_pool.values())),
        spec_digest=EVIDENCE_SCOPE_DIGEST,
    )
    report_start = _day(scope["development_report_start"])
    report_end = _day(scope["development_report_end"])
    observations, fact_build_counts = build_order_observations(
        admissions, report_start=report_start, report_end=report_end
    )
    path_values = _path_map(admissions, report_start=report_start, report_end=report_end)
    feature_rows = _combined_feature_rows(observations, path_values)
    basis = threshold_basis(feature_rows)
    stage0_candidates = [item.to_dict() for item in stage0_candidate_grid(basis)]
    stage2_candidates = [item.to_dict() for item in stage2_candidate_grid(basis)]
    candidate_pairs = [
        {
            "pair_id": pair.pair_id,
            "stage0_candidate_id": pair.stage0.candidate_id,
            "stage2_candidate_id": pair.stage2.candidate_id,
        }
        for pair in joint_candidate_grid(basis)
    ]
    candidate_payload = {
        "schema_version": "quanthus.stage-orderly-consolidation-joint-candidate-grid.v1",
        "threshold_basis_digest": digest(basis),
        "stage0_candidate_count": len(stage0_candidates),
        "stage2_candidate_count": len(stage2_candidates),
        "pair_count": len(candidate_pairs),
        "stage0_candidates": stage0_candidates,
        "stage2_candidates": stage2_candidates,
        "pairs": candidate_pairs,
        "parameter_search_performed": False,
        "grid_expanded_after_result": False,
    }

    selection = select_development_cases(development_pool, feature_rows, config)
    selection["schema_version"] = (
        "quanthus.stage-orderly-consolidation-joint-development-selection.v1"
    )
    retained = _retained_rows(admissions, selection["selected"])
    selected_codes = tuple(sorted(item["security_code"] for item in selection["selected"]))
    evidence = build_evidence(
        retained,
        [
            (item["security_code"], date.fromisoformat(item["market_date"]))
            for item in selection["selected"]
        ],
        InputPolicy(EVIDENCE_SCOPE_DIGEST, selected_codes, source_policy.calendar_ref),
        code_revision=code_revision,
        source_kind="retrospective_development",
        sampling_strata={
            (item["security_code"], date.fromisoformat(item["market_date"])): "representative"
            for item in selection["selected"]
        },
        protocol_override=evidence_protocol,
        protocol_digest=EVIDENCE_SCOPE_DIGEST,
    )
    validate_evidence(evidence, expected_protocol_digest=EVIDENCE_SCOPE_DIGEST)
    selected_paths = _selected_path_facts(evidence, path_values)

    source_binding = {
        "schema_version": "quanthus.stage-orderly-consolidation-joint-d1-source-binding.v1",
        **upstream,
        "excluded_prior_identity_count": len(excluded),
        "candidate_population_digest": digest(tuple(candidate_codes)),
        "fresh_pool": fresh,
        "fresh_pool_digest": digest(fresh),
        "development_pool": development_pool,
        "development_pool_digest": digest(development_pool),
        "preserved_evaluation_identity_digest": preserved_plan["identity_digest"],
        "preserved_evaluation_plan_digest": digest(preserved_plan),
        "development_preserved_evaluation_overlap": 0,
        "source_manifest_digest": digest(source_manifest),
        "development_read_scope": development_scope.to_dict(),
        "evidence_protocol_path": EVIDENCE_SCOPE_PATH,
        "evidence_protocol_digest": EVIDENCE_SCOPE_DIGEST,
        "2019_p010_development_read": False,
        "2020_p009_development_read": False,
        "preserved_2021_evaluation_market_data_read": False,
        "2022_2024_read": False,
        "2025_read": False,
    }
    summary = {
        "schema_version": SUMMARY_SCHEMA,
        "work_item": "P3-STAGE-011-D",
        "phase": "D1_PREPARE_FRESH_2018_JOINT_DEVELOPMENT_AND_BIND_PRESERVED_2021_PLAN",
        "code_revision": code_revision,
        "preregistration_digest": PREREG_DIGEST,
        "execution_digest": EXECUTION_DIGEST,
        "fresh_pool_size": len(fresh),
        "development_identity_capacity": len(development_pool),
        "development_selected_case_count": selection["selected_case_count"],
        "development_shortfall_count": selection["shortfall_count"],
        "development_selection_digest": digest(selection),
        "threshold_basis_digest": digest(basis),
        "candidate_grid_digest": digest(candidate_payload),
        "stage0_candidate_count": len(stage0_candidates),
        "stage2_candidate_count": len(stage2_candidates),
        "candidate_pair_count": len(candidate_pairs),
        "preserved_evaluation_identity_count": preserved_plan["identity_count"],
        "preserved_evaluation_identity_digest": preserved_plan["identity_digest"],
        "preserved_evaluation_plan_digest": digest(preserved_plan),
        "preserved_evaluation_data_read": False,
        "evidence_protocol_digest": EVIDENCE_SCOPE_DIGEST,
        "development_evidence_digest": digest(evidence),
        "path_facts_digest": digest(selected_paths),
        "source_feature_observation_count": len(feature_rows),
        "fact_build_counts": fact_build_counts,
        "labels_used_for_threshold_basis": False,
        "reference_opinions_used_for_threshold_basis": False,
        "parameter_search": False,
        "opinion_count": 0,
        "pair_frozen": False,
        "future_partitions_read": False,
    }
    run_id = (
        "stage-orderly-consolidation-joint-d1-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    run_root = output_root.expanduser().resolve() / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    payloads = {
        "source-binding.json": canonical(source_binding),
        "development-selection.json": canonical(selection),
        "preserved-evaluation-plan.json": canonical(preserved_plan),
        "threshold-basis.json": canonical(basis),
        "candidate-grid.json": canonical(candidate_payload),
        "evidence.json": canonical(evidence),
        "path-facts.json": canonical(selected_paths),
        "summary.json": canonical(summary),
    }
    if len(payloads["evidence.json"]) > MAX_BYTES:
        raise OrderlyConsolidationJointPrepareError("development_evidence_size_budget")
    for name, raw in payloads.items():
        (run_root / name).write_bytes(raw)
    manifest = {
        "schema_version": ARTIFACT_SCHEMA,
        "run_id": run_id,
        "code_revision": code_revision,
        "preregistration_digest": PREREG_DIGEST,
        "execution_digest": EXECUTION_DIGEST,
        "files": {name: _sha(raw) for name, raw in payloads.items()},
        "authority": {
            "preserved_2021_evaluation_market_data_read": False,
            "parameter_search": False,
            "pair_frozen": False,
            "2019_p010_development_read": False,
            "2020_p009_development_read": False,
            "2022_2024_read": False,
            "2025_read": False,
        },
    }
    manifest_raw = canonical(manifest)
    (run_root / "manifest.json").write_bytes(manifest_raw)
    ready = _sha(manifest_raw)
    (run_root / "READY").write_text(ready + "\n", encoding="ascii")
    return {**summary, "run_id": run_id, "run_root": str(run_root), "manifest_sha256": ready}


def verify_artifact(path: Path, *, code_revision: str | None = None) -> dict[str, Any]:
    root = path.expanduser().resolve()
    manifest = _object(root / "manifest.json", "joint_d1_manifest")
    manifest_raw = canonical(manifest)
    ready = (root / "READY").read_text(encoding="ascii").strip()
    if manifest.get("schema_version") != ARTIFACT_SCHEMA or ready != _sha(manifest_raw):
        raise OrderlyConsolidationJointPrepareError("d1_manifest_or_ready")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise OrderlyConsolidationJointPrepareError("d1_code_revision")
    if (
        manifest.get("preregistration_digest") != PREREG_DIGEST
        or manifest.get("execution_digest") != EXECUTION_DIGEST
    ):
        raise OrderlyConsolidationJointPrepareError("d1_contract_binding")
    expected = {
        "source-binding.json",
        "development-selection.json",
        "preserved-evaluation-plan.json",
        "threshold-basis.json",
        "candidate-grid.json",
        "evidence.json",
        "path-facts.json",
        "summary.json",
    }
    files = manifest.get("files")
    if not isinstance(files, Mapping) or set(files) != expected:
        raise OrderlyConsolidationJointPrepareError("d1_inventory")
    payloads: dict[str, Any] = {}
    for name in sorted(expected):
        raw = (root / name).read_bytes()
        if _sha(raw) != files[name]:
            raise OrderlyConsolidationJointPrepareError("d1_file_digest:" + name)
        payloads[name] = json.loads(raw)
    validate_evidence(payloads["evidence.json"], expected_protocol_digest=EVIDENCE_SCOPE_DIGEST)
    summary = payloads["summary.json"]
    source = payloads["source-binding.json"]
    plan = payloads["preserved-evaluation-plan.json"]
    grid = payloads["candidate-grid.json"]
    basis = payloads["threshold-basis.json"]
    if (
        summary.get("preserved_evaluation_data_read") is not False
        or summary.get("labels_used_for_threshold_basis") is not False
        or summary.get("reference_opinions_used_for_threshold_basis") is not False
        or summary.get("parameter_search") is not False
        or summary.get("opinion_count") != 0
        or summary.get("pair_frozen") is not False
        or summary.get("evidence_protocol_digest") != EVIDENCE_SCOPE_DIGEST
        or source.get("evidence_protocol_path") != EVIDENCE_SCOPE_PATH
        or source.get("evidence_protocol_digest") != EVIDENCE_SCOPE_DIGEST
        or source.get("development_preserved_evaluation_overlap") != 0
        or source.get("2019_p010_development_read") is not False
        or source.get("2020_p009_development_read") is not False
        or source.get("preserved_2021_evaluation_market_data_read") is not False
        or source.get("2022_2024_read") is not False
        or source.get("2025_read") is not False
        or plan.get("data_read") is not False
        or basis.get("labels_used") is not False
        or basis.get("reference_opinions_used") is not False
        or grid.get("parameter_search_performed") is not False
        or grid.get("grid_expanded_after_result") is not False
    ):
        raise OrderlyConsolidationJointPrepareError("d1_authority_claim")
    if (
        digest(payloads["development-selection.json"])
        != summary.get("development_selection_digest")
        or digest(plan) != summary.get("preserved_evaluation_plan_digest")
        or digest(basis) != summary.get("threshold_basis_digest")
        or digest(grid) != summary.get("candidate_grid_digest")
        or digest(payloads["evidence.json"]) != summary.get("development_evidence_digest")
        or digest(payloads["path-facts.json"]) != summary.get("path_facts_digest")
        or grid.get("stage0_candidate_count") != 9
        or grid.get("stage2_candidate_count") != 3
        or grid.get("pair_count") != 27
    ):
        raise OrderlyConsolidationJointPrepareError("d1_summary_payload_binding")
    return {
        "verified": True,
        "run_id": manifest["run_id"],
        "manifest_sha256": ready,
        "file_count": len(expected),
        "fresh_pool_size": summary["fresh_pool_size"],
        "development_identity_capacity": summary["development_identity_capacity"],
        "development_selected_case_count": summary["development_selected_case_count"],
        "development_shortfall_count": summary["development_shortfall_count"],
        "development_selection_digest": summary["development_selection_digest"],
        "development_evidence_digest": summary["development_evidence_digest"],
        "threshold_basis_digest": summary["threshold_basis_digest"],
        "candidate_grid_digest": summary["candidate_grid_digest"],
        "stage0_candidate_count": 9,
        "stage2_candidate_count": 3,
        "candidate_pair_count": 27,
        "path_facts_digest": summary["path_facts_digest"],
        "preserved_evaluation_identity_count": summary["preserved_evaluation_identity_count"],
        "preserved_evaluation_identity_digest": summary["preserved_evaluation_identity_digest"],
        "preserved_evaluation_plan_digest": summary["preserved_evaluation_plan_digest"],
        "preserved_evaluation_data_read": False,
        "parameter_search": False,
        "opinion_count": 0,
        "pair_frozen": False,
        "review_ready": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--config", type=Path, default=find_project_root() / DEFAULT_CONFIG)
    run.add_argument("--training-root", type=Path, required=True)
    run.add_argument("--output-root", type=Path, required=True)
    run.add_argument("--code-revision", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--artifact", type=Path, required=True)
    verify.add_argument("--code-revision")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "run":
        result = run_prepare(
            config_path=args.config,
            training_root=args.training_root,
            output_root=args.output_root,
            code_revision=args.code_revision,
        )
    else:
        result = verify_artifact(args.artifact, code_revision=args.code_revision)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

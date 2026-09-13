"""P3-STAGE-012-G1 fresh 2016 atomic-first development prepare.

The prepare reuses the proven P011 source/selection/evidence primitives, adds the P011 fresh pool to
the exclusion closure, and publishes the registered envelope measurement only in a scoring-only
artifact. Blind reviewer transport remains limited to the historical Primary evidence plus existing
path-geometry facts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.stage_learning.atomic_proposition_development import (
    EXECUTION_DIGEST,
    PREREG_DIGEST,
    instability_candidate_grid,
    load_execution,
    load_preregistration,
    stage2_candidate_grid,
    threshold_basis,
)
from quanthus.stage_learning.order_proposition_development import build_order_observations
from quanthus.stage_learning.order_reference_protocol import (
    MAX_BYTES,
    build_evidence,
    load_protocol_contract,
    validate_evidence,
)
from quanthus.stage_learning.orderly_consolidation_joint_development import (
    load_preregistration as load_p011_preregistration,
)
from quanthus.stage_learning.orderly_consolidation_joint_development_prepare import (
    _verify_upstream_and_isolation,
)
from quanthus.stage_learning.orderly_consolidation_joint_development_prepare import (
    verify_artifact as verify_p011_artifact,
)
from quanthus.stage_learning.positive_instability_development_prepare import (
    _combined_feature_rows,
    _fresh_pool,
    _path_map,
    _retained_rows,
    _selected_path_facts,
)
from quanthus.stage_learning.positive_instability_quantitative_development_prepare import (
    select_development_cases,
)
from quanthus.stage_learning.source import (
    DevelopmentReadScope,
    discover_development_security_population,
    read_development_admissions,
    read_development_identity_members,
)
from quanthus.structure_directional_path_envelope import assess_close_path_envelope
from quanthus.structure_v2_inputs import InputPolicy, canonical, digest

DEFAULT_CONFIG = "config/stage-learning/stage-atomic-proposition-development-screen.v1.json"
ARTIFACT_SCHEMA = "quanthus.stage-atomic-proposition-g1-artifact.v1"
SUMMARY_SCHEMA = "quanthus.stage-atomic-proposition-g1-summary.v1"
EVIDENCE_SCOPE_PATH = "config/stage-learning/order-reference-evidence-scope-2016.v1.json"
EVIDENCE_SCOPE_DIGEST = "sha256:6bbe9c56b850d6bb6ebe49766f2e5c9c2860def98fb06b54c5320ea672b6e32b"
_REVISION = re.compile(r"^[0-9a-f]{40}$")


class AtomicPropositionPrepareError(ValueError):
    """The P3-STAGE-012-G1 source, isolation or publication contract was violated."""


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AtomicPropositionPrepareError(f"cannot_read_{label}") from exc
    if not isinstance(value, dict):
        raise AtomicPropositionPrepareError(f"{label}_must_be_object")
    return value


def _day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise AtomicPropositionPrepareError("invalid_date") from exc


def _artifact_root(training_root: Path, binding: Mapping[str, Any]) -> Path:
    return (training_root / str(binding["artifact_relative_path"])).resolve()


def _verify_isolation(
    *, root: Path, training_root: Path, execution: Mapping[str, Any]
) -> tuple[dict[str, Any], set[str], dict[str, Any]]:
    p011_config = load_p011_preregistration(root)
    upstream, _stage010_fresh, excluded, preserved_plan = _verify_upstream_and_isolation(
        root=root,
        training_root=training_root,
        config=p011_config,
    )
    p011 = execution["p011_artifact_binding"]
    p011_root = _artifact_root(training_root, p011)
    verified = verify_p011_artifact(p011_root)
    if (
        verified["manifest_sha256"] != p011["manifest_sha256"]
        or verified["run_id"] != p011["run_id"]
        or verified["fresh_pool_size"] != p011["fresh_pool_size"]
    ):
        raise AtomicPropositionPrepareError("p011_artifact_binding")
    source_binding = _object(p011_root / "source-binding.json", "p011_source_binding")
    p011_fresh = source_binding.get("fresh_pool")
    if not isinstance(p011_fresh, Mapping) or len(p011_fresh) != 512:
        raise AtomicPropositionPrepareError("p011_fresh_pool_binding")
    excluded |= set(str(identity) for identity in p011_fresh)
    return {
        **upstream,
        "stage011_manifest": verified["manifest_sha256"],
        "stage011_fresh_pool_digest": digest(dict(p011_fresh)),
    }, excluded, preserved_plan


def _envelope_measurements(
    evidence: Mapping[str, Any], retained_rows: Sequence[Any]
) -> dict[str, Any]:
    by_code: dict[str, list[Any]] = defaultdict(list)
    for row in retained_rows:
        by_code[str(row.security_code)].append(row)
    output: dict[str, Any] = {}
    for case in evidence["cases"]:
        code = str(case["security_code"])
        day = date.fromisoformat(str(case["market_date"]))
        history = sorted(
            (row for row in by_code[code] if row.market_date <= day),
            key=lambda row: row.market_date,
        )[-21:]
        if len(history) != 21:
            raise AtomicPropositionPrepareError("selected_envelope_history_missing")
        assessment = assess_close_path_envelope([row.close for row in history]).to_dict()
        output[str(case["case_id"])] = {
            "status": assessment["status"],
            "reason": assessment["reason"],
            "profile": assessment["profile"],
            "events": assessment["events"],
            "descriptors": assessment["descriptors"],
            "value_digest": digest(
                {
                    "status": assessment["status"],
                    "reason": assessment["reason"],
                    "profile": assessment["profile"],
                    "events": assessment["events"],
                    "descriptors": assessment["descriptors"],
                }
            ),
        }
    return {
        "schema_version": "quanthus.stage-atomic-proposition-envelope-measurements.v1",
        "concept_id": "structure.geometry.close-envelope-resolution-trace",
        "result_kind": "measurement",
        "review_transport_visible": False,
        "cases": output,
    }


def run_prepare(
    *, config_path: Path, training_root: Path, output_root: Path, code_revision: str
) -> dict[str, Any]:
    if not _REVISION.fullmatch(code_revision):
        raise AtomicPropositionPrepareError("exact_code_revision_required")
    root = find_project_root()
    config = load_preregistration(root)
    execution = load_execution(root)
    if config_path.resolve() != (root / DEFAULT_CONFIG).resolve():
        supplied = _object(config_path, "atomic_development_screen")
        if digest(supplied) != PREREG_DIGEST:
            raise AtomicPropositionPrepareError("development_screen_config_drift")
    evidence_binding = execution["evidence_protocol"]
    if (
        evidence_binding["path"] != EVIDENCE_SCOPE_PATH
        or evidence_binding["digest"] != EVIDENCE_SCOPE_DIGEST
    ):
        raise AtomicPropositionPrepareError("evidence_scope_execution_binding")
    evidence_protocol = load_protocol_contract(
        EVIDENCE_SCOPE_PATH, EVIDENCE_SCOPE_DIGEST, root
    )
    training_root = training_root.expanduser().resolve()
    upstream, excluded, preserved_plan = _verify_isolation(
        root=root, training_root=training_root, execution=execution
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
    if set(fresh) & excluded:
        raise AtomicPropositionPrepareError("fresh_pool_prior_overlap")
    development_pool = {
        identity: fresh[identity]
        for identity in list(fresh)[: int(scope["development_identity_capacity"])]
    }
    if set(development_pool) & preserved_ids:
        raise AtomicPropositionPrepareError("development_preserved_evaluation_overlap")

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
    instability_candidates = [item.to_dict() for item in instability_candidate_grid(basis)]
    order_candidates = [item.to_dict() for item in stage2_candidate_grid(basis)]
    candidate_payload = {
        "schema_version": "quanthus.stage-atomic-proposition-candidate-grid.v1",
        "threshold_basis_digest": digest(basis),
        "instability_candidate_count": len(instability_candidates),
        "order_profile_count": len(order_candidates),
        "relocation_candidate_count": 1,
        "joint_pair_search_count": 0,
        "instability_candidates": instability_candidates,
        "fixed_envelope_channel": config["instability_candidate_surface"]["fixed_additive_channel"],
        "order_candidates": order_candidates,
        "parameter_search_performed": False,
        "grid_expanded_after_result": False,
    }

    selection = select_development_cases(development_pool, feature_rows, config)
    selection["schema_version"] = "quanthus.stage-atomic-proposition-development-selection.v1"
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
    envelopes = _envelope_measurements(evidence, retained)

    source_binding = {
        "schema_version": "quanthus.stage-atomic-proposition-g1-source-binding.v1",
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
        "2017_2021_result_bearing_market_access": False,
        "2022_2024_read": False,
        "2025_read": False,
    }
    summary = {
        "schema_version": SUMMARY_SCHEMA,
        "work_item": "P3-STAGE-012-G1",
        "phase": "G1_PREPARE_FRESH_2016_ATOMIC_DEVELOPMENT",
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
        "envelope_measurements_digest": digest(envelopes),
        "instability_candidate_count": len(instability_candidates),
        "order_profile_count": len(order_candidates),
        "relocation_candidate_count": 1,
        "joint_pair_search_count": 0,
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
        "envelope_measurement_visible_to_reviewer": False,
        "parameter_search": False,
        "opinion_count": 0,
        "atomic_freeze": False,
        "future_partitions_read": False,
    }
    run_id = "stage-atomic-proposition-g1-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
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
        "envelope-measurements.json": canonical(envelopes),
        "summary.json": canonical(summary),
    }
    if len(payloads["evidence.json"]) > MAX_BYTES:
        raise AtomicPropositionPrepareError("development_evidence_size_budget")
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
            "atomic_freeze": False,
            "2017_2021_result_bearing_market_access": False,
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
    manifest = _object(root / "manifest.json", "g1_manifest")
    manifest_raw = canonical(manifest)
    ready = (root / "READY").read_text(encoding="ascii").strip()
    if manifest.get("schema_version") != ARTIFACT_SCHEMA or ready != _sha(manifest_raw):
        raise AtomicPropositionPrepareError("g1_manifest_or_ready")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise AtomicPropositionPrepareError("g1_code_revision")
    if (
        manifest.get("preregistration_digest") != PREREG_DIGEST
        or manifest.get("execution_digest") != EXECUTION_DIGEST
    ):
        raise AtomicPropositionPrepareError("g1_contract_binding")
    expected = {
        "source-binding.json",
        "development-selection.json",
        "preserved-evaluation-plan.json",
        "threshold-basis.json",
        "candidate-grid.json",
        "evidence.json",
        "path-facts.json",
        "envelope-measurements.json",
        "summary.json",
    }
    files = manifest.get("files")
    if not isinstance(files, Mapping) or set(files) != expected:
        raise AtomicPropositionPrepareError("g1_inventory")
    payloads: dict[str, Any] = {}
    for name in sorted(expected):
        raw = (root / name).read_bytes()
        if _sha(raw) != files[name]:
            raise AtomicPropositionPrepareError("g1_file_digest:" + name)
        payloads[name] = json.loads(raw)
    validate_evidence(payloads["evidence.json"], expected_protocol_digest=EVIDENCE_SCOPE_DIGEST)
    summary = payloads["summary.json"]
    source = payloads["source-binding.json"]
    plan = payloads["preserved-evaluation-plan.json"]
    grid = payloads["candidate-grid.json"]
    basis = payloads["threshold-basis.json"]
    envelopes = payloads["envelope-measurements.json"]
    if (
        summary.get("preserved_evaluation_data_read") is not False
        or summary.get("labels_used_for_threshold_basis") is not False
        or summary.get("reference_opinions_used_for_threshold_basis") is not False
        or summary.get("envelope_measurement_visible_to_reviewer") is not False
        or summary.get("parameter_search") is not False
        or summary.get("opinion_count") != 0
        or summary.get("atomic_freeze") is not False
        or source.get("evidence_protocol_path") != EVIDENCE_SCOPE_PATH
        or source.get("evidence_protocol_digest") != EVIDENCE_SCOPE_DIGEST
        or source.get("development_preserved_evaluation_overlap") != 0
        or source.get("2017_2021_result_bearing_market_access") is not False
        or source.get("2022_2024_read") is not False
        or source.get("2025_read") is not False
        or plan.get("data_read") is not False
        or basis.get("labels_used") is not False
        or basis.get("reference_opinions_used") is not False
        or grid.get("parameter_search_performed") is not False
        or grid.get("grid_expanded_after_result") is not False
        or grid.get("joint_pair_search_count") != 0
        or envelopes.get("review_transport_visible") is not False
    ):
        raise AtomicPropositionPrepareError("g1_authority_claim")
    if (
        digest(payloads["development-selection.json"])
        != summary.get("development_selection_digest")
        or digest(plan) != summary.get("preserved_evaluation_plan_digest")
        or digest(basis) != summary.get("threshold_basis_digest")
        or digest(grid) != summary.get("candidate_grid_digest")
        or digest(envelopes) != summary.get("envelope_measurements_digest")
        or digest(payloads["evidence.json"]) != summary.get("development_evidence_digest")
        or digest(payloads["path-facts.json"]) != summary.get("path_facts_digest")
        or grid.get("instability_candidate_count") != 9
        or grid.get("order_profile_count") != 3
        or grid.get("relocation_candidate_count") != 1
    ):
        raise AtomicPropositionPrepareError("g1_summary_payload_binding")
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
        "envelope_measurements_digest": summary["envelope_measurements_digest"],
        "instability_candidate_count": 9,
        "order_profile_count": 3,
        "relocation_candidate_count": 1,
        "joint_pair_search_count": 0,
        "path_facts_digest": summary["path_facts_digest"],
        "preserved_evaluation_identity_count": summary["preserved_evaluation_identity_count"],
        "preserved_evaluation_identity_digest": summary["preserved_evaluation_identity_digest"],
        "preserved_evaluation_plan_digest": summary["preserved_evaluation_plan_digest"],
        "preserved_evaluation_data_read": False,
        "parameter_search": False,
        "opinion_count": 0,
        "atomic_freeze": False,
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

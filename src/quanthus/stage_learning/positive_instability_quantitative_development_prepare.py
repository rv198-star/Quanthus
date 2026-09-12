"""P3-STAGE-010-D1 fresh 2019 quantitative development prepare.

D1 selects a fourth economic-identity pool after excluding all Stage-007/008/009 identity surfaces,
reads only 2018 warm-up plus 2019 development data, constructs an unlabeled nine-candidate basis,
freezes morphology-blind 2019 review cases, and binds the exact still-unread Stage-009 2021
evaluation plan without opening its market rows.
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
from quanthus.stage_learning.positive_instability_development_prepare import (
    _combined_feature_rows,
    _fresh_pool,
    _path_map,
    _rank,
    _retained_rows,
    _selected_path_facts,
)
from quanthus.stage_learning.positive_instability_development_prepare import (
    verify_artifact as verify_stage009_d1_artifact,
)
from quanthus.stage_learning.positive_instability_quantitative_development import (
    EXECUTION_DIGEST,
    PREREG_DIGEST,
    candidate_grid,
    load_execution,
    load_preregistration,
    threshold_basis,
)
from quanthus.stage_learning.source import (
    DevelopmentReadScope,
    discover_development_security_population,
    read_development_admissions,
    read_development_identity_members,
)
from quanthus.structure_v2_inputs import InputPolicy, canonical, digest

DEFAULT_CONFIG = (
    "config/stage-learning/positive-instability-quantitative-development-screen.v1.json"
)
ARTIFACT_SCHEMA = "quanthus.stage-positive-instability-quantitative-d1-artifact.v1"
SUMMARY_SCHEMA = "quanthus.stage-positive-instability-quantitative-d1-summary.v1"
REVIEW_EXPORT_SCHEMA = "quanthus.stage-positive-instability-quantitative-d2-review-input.v1"
REVIEW_EXPORT_PAGE_SIZE = 8
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_JOB_ID = re.compile(r"^stage_[0-9a-f]{32}$")
EVIDENCE_SCOPE_PATH = "config/stage-learning/order-reference-evidence-scope-2019.v1.json"
EVIDENCE_SCOPE_DIGEST = "sha256:2d65ce22666a7709a0f67e3d708df085bb624505484bddeec62a7de9b6ecaa0d"


class PositiveInstabilityQuantitativePrepareError(ValueError):
    """The P3-STAGE-010-D1 source, identity, selection or artifact contract was violated."""


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PositiveInstabilityQuantitativePrepareError(f"cannot_read_{label}") from exc
    if not isinstance(value, dict):
        raise PositiveInstabilityQuantitativePrepareError(f"{label}_must_be_object")
    return value


def _day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise PositiveInstabilityQuantitativePrepareError("invalid_date") from exc


def select_development_cases(
    pool: Mapping[str, str], feature_rows: Sequence[Mapping[str, Any]], config: Mapping[str, Any]
) -> dict[str, Any]:
    plan = config["development_selection"]
    seed = str(plan["sampling_seed"])
    code_to_identity = {code: identity for identity, code in pool.items()}
    by_identity: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for item in feature_rows:
        identity = code_to_identity.get(str(item["security_code"]))
        market_date = item["market_date"]
        if (
            identity is not None
            and isinstance(market_date, date)
            and market_date.year == int(plan["year"])
        ):
            by_identity[identity].append(item)
    ranked = sorted(pool, key=lambda identity: (_rank(seed, "development-id", identity), identity))
    planned = int(plan["planned_identities"])
    identities = ranked[:planned]
    caps = {int(q): int(value) for q, value in plan["quarter_caps"].items()}
    if sum(caps.values()) != planned:
        raise PositiveInstabilityQuantitativePrepareError("development_quarter_caps")
    quarter_by_index = []
    for quarter in range(1, 5):
        quarter_by_index.extend([quarter] * caps[quarter])
    selected = []
    missing = []
    for index, identity in enumerate(identities):
        quarter = quarter_by_index[index]
        candidates = [
            item
            for item in by_identity.get(identity, [])
            if (item["market_date"].month - 1) // 3 + 1 == quarter
        ]
        if not candidates:
            missing.append({"security_id": identity, "quarter": quarter})
            continue
        chosen = min(
            candidates,
            key=lambda item: (
                _rank(seed, "development-date", identity, item["market_date"]),
                item["market_date"],
            ),
        )
        selected.append(
            {
                "security_id": identity,
                "security_code": pool[identity],
                "market_date": chosen["market_date"].isoformat(),
                "quarter": quarter,
            }
        )
    return {
        "schema_version": (
            "quanthus.stage-positive-instability-quantitative-development-selection.v1"
        ),
        "planned_identity_count": planned,
        "selected_case_count": len(selected),
        "shortfall_count": len(missing),
        "selected": selected,
        "shortfalls": missing,
        "selection_uses_morphology": False,
        "one_case_per_identity": True,
        "cross_quarter_backfill": False,
        "identity_backfill": False,
    }


def _artifact_root(training_root: Path, binding: Mapping[str, Any]) -> Path:
    return (training_root / str(binding["artifact_relative_path"])).resolve()


def run_prepare(
    *, config_path: Path, training_root: Path, output_root: Path, code_revision: str
) -> dict[str, Any]:
    if not _REVISION.fullmatch(code_revision):
        raise PositiveInstabilityQuantitativePrepareError("exact_code_revision_required")
    root = find_project_root()
    config = load_preregistration(root)
    load_execution(root)
    evidence_protocol = load_protocol_contract(
        EVIDENCE_SCOPE_PATH,
        EVIDENCE_SCOPE_DIGEST,
        root,
    )
    if config_path.resolve() != (root / DEFAULT_CONFIG).resolve():
        supplied = _object(config_path, "quantitative_development_screen")
        if digest(supplied) != PREREG_DIGEST:
            raise PositiveInstabilityQuantitativePrepareError("development_screen_config_drift")
    training_root = training_root.expanduser().resolve()

    stage007 = config["bindings"]["stage007_prepare"]
    stage007_root = _artifact_root(training_root, stage007)
    stage007_verified = verify_prepare_artifact(
        stage007_root, code_revision=stage007["code_revision"]
    )
    if stage007_verified["manifest_sha256"] != stage007["manifest_sha256"]:
        raise PositiveInstabilityQuantitativePrepareError("stage007_prepare_binding")
    stage007_binding = _object(stage007_root / "source-binding.json", "stage007_source_binding")

    stage008 = config["bindings"]["stage008_d1"]
    stage008_root = _artifact_root(training_root, stage008)
    stage008_verified = verify_stage008_d1_artifact(
        stage008_root, code_revision=stage008["code_revision"]
    )
    if stage008_verified["manifest_sha256"] != stage008["manifest_sha256"]:
        raise PositiveInstabilityQuantitativePrepareError("stage008_d1_binding")
    stage008_binding = _object(stage008_root / "source-binding.json", "stage008_source_binding")
    stage008_fresh = stage008_binding.get("fresh_pool")
    if not isinstance(stage008_fresh, Mapping) or len(stage008_fresh) != 512:
        raise PositiveInstabilityQuantitativePrepareError("stage008_fresh_pool_binding")

    stage009 = config["bindings"]["stage009_d1"]
    stage009_root = _artifact_root(training_root, stage009)
    stage009_verified = verify_stage009_d1_artifact(
        stage009_root, code_revision=stage009["code_revision"]
    )
    if (
        stage009_verified["manifest_sha256"] != stage009["manifest_sha256"]
        or stage009_verified["evaluation_identity_digest"]
        != stage009["evaluation_identity_digest"]
        or stage009_verified["evaluation_plan_digest"] != stage009["evaluation_plan_digest"]
    ):
        raise PositiveInstabilityQuantitativePrepareError("stage009_d1_binding")
    stage009_binding = _object(stage009_root / "source-binding.json", "stage009_source_binding")
    stage009_fresh = stage009_binding.get("fresh_pool")
    if not isinstance(stage009_fresh, Mapping) or len(stage009_fresh) != 512:
        raise PositiveInstabilityQuantitativePrepareError("stage009_fresh_pool_binding")
    preserved_plan = _object(stage009_root / "evaluation-plan.json", "stage009_evaluation_plan")
    if (
        digest(preserved_plan) != stage009["evaluation_plan_digest"]
        or preserved_plan.get("identity_digest") != stage009["evaluation_identity_digest"]
        or preserved_plan.get("identity_count") != stage009["evaluation_identity_count"]
        or preserved_plan.get("data_read") is not False
    ):
        raise PositiveInstabilityQuantitativePrepareError("preserved_evaluation_plan_binding")
    preserved_ids = {str(item["security_id"]) for item in preserved_plan["identities"]}

    node = _object(root / config["training_node_config"], "training_node_config")
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
    excluded = (
        set(stage007_binding["excluded_economic_ids"])
        | set(stage007_binding["pool"])
        | set(stage008_fresh)
        | set(stage009_fresh)
    )
    fresh = _fresh_pool(
        pairs,
        excluded_ids=excluded,
        seed=str(scope["fresh_pool_seed"]),
        size=int(scope["fresh_pool_size"]),
    )
    development_pool = {
        identity: fresh[identity]
        for identity in list(fresh)[: int(scope["development_identity_capacity"])]
    }
    if set(development_pool) & preserved_ids:
        raise PositiveInstabilityQuantitativePrepareError(
            "development_preserved_evaluation_overlap"
        )

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
    candidates = [candidate.to_dict() for candidate in candidate_grid(basis)]
    candidate_payload = {
        "schema_version": "quanthus.stage-positive-instability-quantitative-candidate-grid.v1",
        "threshold_basis_digest": digest(basis),
        "candidate_count": len(candidates),
        "candidates": candidates,
        "parameter_search_performed": False,
    }

    selection = select_development_cases(development_pool, feature_rows, config)
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
        "schema_version": "quanthus.stage-positive-instability-quantitative-d1-source-binding.v1",
        "stage007_prepare_manifest_sha256": stage007_verified["manifest_sha256"],
        "stage008_d1_manifest_sha256": stage008_verified["manifest_sha256"],
        "stage009_d1_manifest_sha256": stage009_verified["manifest_sha256"],
        "stage007_pool_digest": digest(stage007_binding["pool"]),
        "stage008_fresh_pool_digest": digest(dict(stage008_fresh)),
        "stage009_fresh_pool_digest": digest(dict(stage009_fresh)),
        "excluded_prior_identity_count": len(excluded),
        "candidate_population_digest": digest(tuple(candidate_codes)),
        "fresh_pool": fresh,
        "fresh_pool_digest": digest(fresh),
        "development_pool": development_pool,
        "development_pool_digest": digest(development_pool),
        "preserved_evaluation_identity_digest": stage009["evaluation_identity_digest"],
        "preserved_evaluation_plan_digest": stage009["evaluation_plan_digest"],
        "development_preserved_evaluation_overlap": 0,
        "source_manifest_digest": digest(source_manifest),
        "development_read_scope": development_scope.to_dict(),
        "evidence_protocol_path": EVIDENCE_SCOPE_PATH,
        "evidence_protocol_digest": EVIDENCE_SCOPE_DIGEST,
        "preserved_2021_evaluation_market_data_read": False,
        "2020_stage009_development_read": False,
        "2022_2024_read": False,
        "2025_read": False,
    }
    summary = {
        "schema_version": SUMMARY_SCHEMA,
        "work_item": "P3-STAGE-010-D",
        "phase": "D1_PREPARE_FRESH_2019_DEVELOPMENT_AND_BIND_PRESERVED_2021_PLAN",
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
        "candidate_count": len(candidates),
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
        "parameter_search": False,
        "opinion_count": 0,
        "candidate_frozen": False,
        "future_partitions_read": False,
    }
    run_id = (
        "stage-positive-instability-quantitative-d1-"
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
        raise PositiveInstabilityQuantitativePrepareError("development_evidence_size_budget")
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
            "candidate_frozen": False,
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
    manifest = _object(root / "manifest.json", "quantitative_d1_manifest")
    raw = canonical(manifest)
    ready = (root / "READY").read_text(encoding="ascii").strip()
    if manifest.get("schema_version") != ARTIFACT_SCHEMA or ready != _sha(raw):
        raise PositiveInstabilityQuantitativePrepareError("d1_manifest_or_ready")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise PositiveInstabilityQuantitativePrepareError("d1_code_revision")
    if (
        manifest.get("preregistration_digest") != PREREG_DIGEST
        or manifest.get("execution_digest") != EXECUTION_DIGEST
    ):
        raise PositiveInstabilityQuantitativePrepareError("d1_contract_binding")
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
        raise PositiveInstabilityQuantitativePrepareError("d1_inventory")
    payloads = {}
    for name in sorted(expected):
        file_raw = (root / name).read_bytes()
        if _sha(file_raw) != files[name]:
            raise PositiveInstabilityQuantitativePrepareError("d1_file_digest:" + name)
        payloads[name] = json.loads(file_raw)
    validate_evidence(
        payloads["evidence.json"], expected_protocol_digest=EVIDENCE_SCOPE_DIGEST
    )
    summary = payloads["summary.json"]
    source = payloads["source-binding.json"]
    plan = payloads["preserved-evaluation-plan.json"]
    if (
        summary.get("preserved_evaluation_data_read") is not False
        or summary.get("labels_used_for_threshold_basis") is not False
        or summary.get("parameter_search") is not False
        or summary.get("opinion_count") != 0
        or summary.get("candidate_frozen") is not False
        or summary.get("evidence_protocol_digest") != EVIDENCE_SCOPE_DIGEST
        or source.get("evidence_protocol_path") != EVIDENCE_SCOPE_PATH
        or source.get("evidence_protocol_digest") != EVIDENCE_SCOPE_DIGEST
        or source.get("preserved_2021_evaluation_market_data_read") is not False
        or source.get("development_preserved_evaluation_overlap") != 0
        or source.get("2020_stage009_development_read") is not False
        or source.get("2022_2024_read") is not False
        or source.get("2025_read") is not False
        or plan.get("data_read") is not False
        or payloads["threshold-basis.json"].get("labels_used") is not False
        or payloads["candidate-grid.json"].get("parameter_search_performed") is not False
    ):
        raise PositiveInstabilityQuantitativePrepareError("d1_authority_claim")
    if (
        digest(payloads["development-selection.json"])
        != summary.get("development_selection_digest")
        or digest(plan) != summary.get("preserved_evaluation_plan_digest")
        or digest(payloads["threshold-basis.json"]) != summary.get("threshold_basis_digest")
        or digest(payloads["candidate-grid.json"]) != summary.get("candidate_grid_digest")
        or digest(payloads["evidence.json"]) != summary.get("development_evidence_digest")
        or digest(payloads["path-facts.json"]) != summary.get("path_facts_digest")
        or payloads["candidate-grid.json"].get("candidate_count") != 9
    ):
        raise PositiveInstabilityQuantitativePrepareError("d1_summary_payload_binding")
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
        "candidate_count": summary["candidate_count"],
        "path_facts_digest": summary["path_facts_digest"],
        "preserved_evaluation_identity_count": summary["preserved_evaluation_identity_count"],
        "preserved_evaluation_identity_digest": summary["preserved_evaluation_identity_digest"],
        "preserved_evaluation_plan_digest": summary["preserved_evaluation_plan_digest"],
        "preserved_evaluation_data_read": False,
        "parameter_search": False,
        "opinion_count": 0,
        "candidate_frozen": False,
        "review_ready": True,
    }


def build_review_export(
    path: Path, *, job_id: str, code_revision: str
) -> dict[str, Any]:
    if not _JOB_ID.fullmatch(job_id):
        raise PositiveInstabilityQuantitativePrepareError("review_export_job_id")
    if not _REVISION.fullmatch(code_revision):
        raise PositiveInstabilityQuantitativePrepareError("review_export_code_revision")
    verified = verify_artifact(path, code_revision=code_revision)
    root = path.expanduser().resolve()
    evidence = _object(root / "evidence.json", "quantitative_d1_evidence")
    path_facts = _object(root / "path-facts.json", "quantitative_d1_path_facts")
    source_rows = evidence.get("source_rows")
    cases = evidence.get("cases")
    path_by_case = path_facts.get("cases")
    if not isinstance(source_rows, dict) or not isinstance(cases, list):
        raise PositiveInstabilityQuantitativePrepareError("review_export_evidence_shape")
    if not isinstance(path_by_case, dict):
        raise PositiveInstabilityQuantitativePrepareError("review_export_path_facts_shape")
    if len(cases) != verified["development_selected_case_count"]:
        raise PositiveInstabilityQuantitativePrepareError("review_export_case_count")

    compact_cases = []
    for case in cases:
        if not isinstance(case, dict):
            raise PositiveInstabilityQuantitativePrepareError("review_export_case_shape")
        case_id = str(case.get("case_id") or "")
        body = {key: value for key, value in case.items() if key != "case_id"}
        if digest(body) != case_id:
            raise PositiveInstabilityQuantitativePrepareError("review_export_case_digest")
        directional = path_by_case.get(case_id)
        if not isinstance(directional, dict) or len(directional) != 2:
            raise PositiveInstabilityQuantitativePrepareError("review_export_path_facts")
        refs = case.get("history_refs")
        if not isinstance(refs, list) or len(refs) < 21:
            raise PositiveInstabilityQuantitativePrepareError("review_export_history_too_short")
        history = []
        for ref in refs[-21:]:
            row = source_rows.get(ref)
            if not isinstance(row, dict) or digest(row) != ref:
                raise PositiveInstabilityQuantitativePrepareError("review_export_history_binding")
            if row.get("market_date") > case.get("market_date"):
                raise PositiveInstabilityQuantitativePrepareError("review_export_future_row")
            history.append(
                [
                    ref,
                    row.get("market_date"),
                    row.get("high"),
                    row.get("low"),
                    row.get("close"),
                    row.get("coordinate_epoch"),
                    row.get("reset_reason"),
                ]
            )
        compact_cases.append(
            {
                "case_id": case_id,
                "security_code": case.get("security_code"),
                "security_id": case.get("security_id"),
                "market_date": case.get("market_date"),
                "sampling_stratum": case.get("sampling_stratum"),
                "coordinate_ref": case.get("coordinate_ref"),
                "primary_available": case.get("primary_available"),
                "input_reason": case.get("input_reason"),
                "availability_reasons": case.get("availability_reasons"),
                "primary": case.get("primary"),
                "directional_path_facts": directional,
                "history": history,
            }
        )

    return {
        "schema_version": REVIEW_EXPORT_SCHEMA,
        "work_item": "P3-STAGE-010-D2",
        "source_job_id": job_id,
        "source_code_revision": code_revision,
        "source_run_id": verified["run_id"],
        "manifest_sha256": verified["manifest_sha256"],
        "development_evidence_digest": verified["development_evidence_digest"],
        "path_facts_digest": verified["path_facts_digest"],
        "case_count": len(compact_cases),
        "page_size": REVIEW_EXPORT_PAGE_SIZE,
        "page_count": (len(compact_cases) + REVIEW_EXPORT_PAGE_SIZE - 1)
        // REVIEW_EXPORT_PAGE_SIZE,
        "cases": compact_cases,
        "threshold_basis_included": False,
        "candidate_output_included": False,
        "explicit_recent_derived_facts_included": False,
        "future_rows_included": False,
        "preserved_2021_evaluation_included": False,
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
    review_export = commands.add_parser("review-export")
    review_export.add_argument("--artifact", type=Path, required=True)
    review_export.add_argument("--job-id", required=True)
    review_export.add_argument("--code-revision", required=True)
    review_export.add_argument("--output", type=Path, required=True)
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
    elif args.command == "verify":
        result = verify_artifact(args.artifact, code_revision=args.code_revision)
    else:
        export = build_review_export(
            args.artifact,
            job_id=args.job_id,
            code_revision=args.code_revision,
        )
        raw = canonical(export)
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            with output.open("xb") as stream:
                stream.write(raw)
        except FileExistsError as exc:
            raise PositiveInstabilityQuantitativePrepareError(
                "review_export_output_exists"
            ) from exc
        result = {
            "exported": True,
            "output": str(output),
            "sha256": _sha(raw),
            "case_count": export["case_count"],
            "page_count": export["page_count"],
            "threshold_basis_included": False,
            "candidate_output_included": False,
            "preserved_2021_evaluation_included": False,
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

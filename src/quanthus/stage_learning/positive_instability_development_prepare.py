"""P3-STAGE-009-D1 third-pool development prepare and evaluation-plan freeze.

D1 verifies the accepted Stage-007 and Stage-008 prepare artifacts only to inherit their exposed
identity boundaries. It selects a third 512-identity pool, reads only 2019-2020 data for the 256
D-development identities, freezes 128 morphology-blind 2020 cases, computes an unlabeled quantile
threshold basis and exact 27-candidate grid, and freezes 160 2021 evaluation identities/quarters
without reading their 2021 market rows.
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
    PROTOCOL_DIGEST,
    build_evidence,
    validate_evidence,
)
from quanthus.stage_learning.order_rule_prepare import verify_prepare_artifact
from quanthus.stage_learning.positive_instability_development import (
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
from quanthus.structure_directional_path_geometry import (
    CONTRACT_DIGEST as PATH_FACT_DIGEST,
)
from quanthus.structure_directional_path_geometry import (
    COUNTER_DIRECTION_EXCURSION_SHARE,
    RANGE_TRAVERSAL,
    build_directional_path_geometry_facts,
)
from quanthus.structure_v2_inputs import InputPolicy, canonical, digest

DEFAULT_CONFIG = "config/stage-learning/positive-instability-development-screen.v1.json"
ARTIFACT_SCHEMA = "quanthus.stage-positive-instability-d1-artifact.v1"
SUMMARY_SCHEMA = "quanthus.stage-positive-instability-d1-summary.v1"
_REVISION = re.compile(r"^[0-9a-f]{40}$")


class PositiveInstabilityPrepareError(ValueError):
    """The D1 identity, source, selection or artifact contract was violated."""


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PositiveInstabilityPrepareError(f"cannot_read_{label}") from exc
    if not isinstance(value, dict):
        raise PositiveInstabilityPrepareError(f"{label}_must_be_object")
    return value


def _day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise PositiveInstabilityPrepareError("invalid_date") from exc


def _rank(seed: str, purpose: str, identity: str, day: date | None = None) -> str:
    return digest(
        {"seed": seed, "purpose": purpose, "security_id": identity, "market_date": day}
    )


def _fresh_pool(
    members: Sequence[Mapping[str, str]],
    *,
    excluded_ids: set[str],
    seed: str,
    size: int,
) -> dict[str, str]:
    by_code: dict[str, set[str]] = defaultdict(set)
    by_identity: dict[str, set[str]] = defaultdict(set)
    for item in members:
        code, identity = str(item["security_code"]), str(item["security_id"])
        by_code[code].add(identity)
        by_identity[identity].add(code)
    eligible = {
        identity: sorted(codes)[0]
        for identity, codes in by_identity.items()
        if identity not in excluded_ids and all(len(by_code[code]) == 1 for code in codes)
    }
    ranked = sorted(eligible, key=lambda identity: (_rank(seed, "fresh-pool", identity), identity))
    if len(ranked) < size:
        raise PositiveInstabilityPrepareError("fresh_pool_capacity_insufficient")
    return {identity: eligible[identity] for identity in ranked[:size]}


def _evaluation_plan(pool: Mapping[str, str], config: Mapping[str, Any]) -> dict[str, Any]:
    plan = config["evaluation_selection"]
    seed = str(plan["sampling_seed"])
    ranked = sorted(pool, key=lambda identity: (_rank(seed, "evaluation-id", identity), identity))
    selected = ranked[: int(plan["planned_identities"])]
    per_quarter = int(plan["identities_per_quarter"])
    rows = [
        {
            "security_id": identity,
            "security_code": pool[identity],
            "quarter": index // per_quarter + 1,
        }
        for index, identity in enumerate(selected)
    ]
    return {
        "schema_version": "quanthus.stage-positive-instability-evaluation-plan.v1",
        "selection_uses_morphology": False,
        "identities": rows,
        "identity_count": len(rows),
        "identity_digest": digest(rows),
        "data_read": False,
    }


def _path_map(admissions: Sequence[Any], *, report_start: date, report_end: date) -> dict:
    facts = build_directional_path_geometry_facts(
        admissions, report_start=report_start, report_end=report_end
    )
    values: dict[tuple[str, date], dict[str, Any]] = defaultdict(dict)
    for item in facts.measurements:
        values[(item.security_code, item.market_date)][item.concept_id] = {
            "value": None if item.value is None else str(item.value),
            "reason": item.availability_reason,
            "revision_id": item.identity.revision_id,
        }
    return values


def _combined_feature_rows(
    observations: Sequence[Any], path_values: Mapping[tuple[str, date], Mapping[str, Any]]
) -> list[dict[str, Any]]:
    output = []
    for item in observations:
        paths = path_values.get((item.security_code, item.market_date))
        if not isinstance(paths, Mapping) or set(paths) != {
            RANGE_TRAVERSAL,
            COUNTER_DIRECTION_EXCURSION_SHARE,
        }:
            continue
        traversal = paths[RANGE_TRAVERSAL]
        counter = paths[COUNTER_DIRECTION_EXCURSION_SHARE]
        if traversal.get("reason") != "available" or traversal.get("value") is None:
            continue
        counter_reason = counter.get("reason")
        if counter_reason not in {"available", "zero_net_direction"}:
            continue
        output.append(
            {
                "security_code": item.security_code,
                "market_date": item.market_date,
                "range_width20": str(item.range_width20),
                "width_change20": str(item.width_change20),
                "center_shift20_abs": str(item.center_shift20_abs),
                "efficiency20_abs": str(item.efficiency20_abs),
                "range_traversal20": str(traversal["value"]),
                "counter_direction_excursion_share20": (
                    None if counter["value"] is None else str(counter["value"])
                ),
            }
        )
    return output


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
            and market_date.year == plan["year"]
        ):
            by_identity[identity].append(item)
    ranked = sorted(pool, key=lambda identity: (_rank(seed, "development-id", identity), identity))
    identities = ranked[: int(plan["planned_identities"])]
    per_quarter = int(plan["identities_per_quarter"])
    selected = []
    missing = []
    for index, identity in enumerate(identities):
        quarter = index // per_quarter + 1
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
                "stratum": "representative",
                "feature_digest": digest(
                    {
                        key: value
                        for key, value in chosen.items()
                        if key not in {"security_code", "market_date"}
                    }
                ),
            }
        )
    return {
        "schema_version": "quanthus.stage-positive-instability-development-selection.v1",
        "selection_uses_morphology": False,
        "planned_identity_count": len(identities),
        "selected": selected,
        "selected_case_count": len(selected),
        "shortfall_count": len(missing),
        "shortfalls": missing,
        "one_case_per_identity": len({item["security_id"] for item in selected}) == len(selected),
    }


def _retained_rows(admissions: Sequence[Any], selected: Sequence[Mapping[str, Any]]) -> list[Any]:
    by_code: dict[str, list[Any]] = defaultdict(list)
    for attempt in admissions:
        by_code[attempt.row.security_code].append(attempt.row)
    rows = []
    for item in selected:
        code = str(item["security_code"])
        day = date.fromisoformat(str(item["market_date"]))
        history = [row for row in by_code[code] if row.market_date <= day][-61:]
        if len(history) != 61:
            raise PositiveInstabilityPrepareError("development_case_missing_61_rows")
        rows.extend(history)
    return sorted(rows, key=lambda row: (row.security_code, row.market_date))


def _selected_path_facts(
    evidence: Mapping[str, Any],
    path_values: Mapping[tuple[str, date], Mapping[str, Any]],
) -> dict[str, Any]:
    output = {}
    for case in evidence["cases"]:
        key = (str(case["security_code"]), date.fromisoformat(str(case["market_date"])))
        values = path_values.get(key)
        if not isinstance(values, Mapping) or set(values) != {
            RANGE_TRAVERSAL,
            COUNTER_DIRECTION_EXCURSION_SHARE,
        }:
            raise PositiveInstabilityPrepareError("selected_path_facts_unavailable")
        output[str(case["case_id"])] = dict(values)
    return {
        "schema_version": "quanthus.stage-positive-instability-path-facts.v1",
        "fact_contract_digest": PATH_FACT_DIGEST,
        "cases": output,
    }


def run_prepare(
    *, config_path: Path, training_root: Path, output_root: Path, code_revision: str
) -> dict[str, Any]:
    if not _REVISION.fullmatch(code_revision):
        raise PositiveInstabilityPrepareError("exact_code_revision_required")
    root = find_project_root()
    config = load_preregistration(root)
    load_execution(root)
    if config_path.resolve() != (root / DEFAULT_CONFIG).resolve():
        supplied = _object(config_path, "development_screen")
        if digest(supplied) != PREREG_DIGEST:
            raise PositiveInstabilityPrepareError("development_screen_config_drift")
    training_root = training_root.expanduser().resolve()

    stage007 = config["bindings"]["stage_007_prepare"]
    stage007_root = (training_root / stage007["artifact_relative_path"]).resolve()
    stage007_verified = verify_prepare_artifact(
        stage007_root, code_revision=stage007["code_revision"]
    )
    if stage007_verified["manifest_sha256"] != stage007["manifest_sha256"]:
        raise PositiveInstabilityPrepareError("stage007_prepare_binding")
    stage007_binding = _object(stage007_root / "source-binding.json", "stage007_source_binding")
    if (
        len(stage007_binding.get("pool", {})) != stage007["pool_size"]
        or len(stage007_binding.get("excluded_economic_ids", []))
        != stage007["excluded_economic_identity_count"]
    ):
        raise PositiveInstabilityPrepareError("stage007_identity_boundary_drift")

    stage008 = config["bindings"]["stage_008_d1"]
    stage008_root = (training_root / stage008["artifact_relative_path"]).resolve()
    stage008_verified = verify_stage008_d1_artifact(
        stage008_root, code_revision=stage008["code_revision"]
    )
    if (
        stage008_verified["manifest_sha256"] != stage008["manifest_sha256"]
        or stage008_verified["fresh_pool_size"] != stage008["fresh_pool_size"]
        or stage008_verified["evaluation_plan_digest"] != stage008["evaluation_plan_digest"]
    ):
        raise PositiveInstabilityPrepareError("stage008_d1_binding")
    stage008_binding = _object(stage008_root / "source-binding.json", "stage008_source_binding")
    stage008_fresh = stage008_binding.get("fresh_pool")
    if (
        not isinstance(stage008_fresh, Mapping)
        or len(stage008_fresh) != stage008["fresh_pool_size"]
    ):
        raise PositiveInstabilityPrepareError("stage008_fresh_pool_binding")

    node = _object(root / config["training_node_config"], "training_node_config")
    scope = config["source_scope"]
    candidate_codes = discover_development_security_population(
        training_root=training_root,
        node_config=node,
        scan_start=_day(scope["candidate_scan_start"]),
        cohort_date=_day(scope["candidate_cohort_date"]),
        minimum_observations=int(scope["candidate_minimum_observations"]),
        seed=str(scope["candidate_seed"]),
    )
    if digest(tuple(candidate_codes)) != stage007_binding["candidate_population_digest"]:
        raise PositiveInstabilityPrepareError("candidate_population_differs_from_stage007")
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
    identity_plan = config["fresh_identity_plan"]
    excluded = (
        set(stage007_binding["excluded_economic_ids"])
        | set(stage007_binding["pool"])
        | set(stage008_fresh)
    )
    fresh = _fresh_pool(
        pairs,
        excluded_ids=excluded,
        seed=str(identity_plan["fresh_pool_seed"]),
        size=int(identity_plan["fresh_pool_size"]),
    )
    ranked_fresh = list(fresh)
    development_pool = {
        identity: fresh[identity]
        for identity in ranked_fresh[: int(identity_plan["development_identity_capacity"])]
    }
    evaluation_pool = {
        identity: fresh[identity]
        for identity in ranked_fresh[int(identity_plan["development_identity_capacity"]):]
    }
    if set(development_pool) & set(evaluation_pool):
        raise PositiveInstabilityPrepareError("panel_identity_overlap")

    development_scope = DevelopmentReadScope(
        _day(scope["history_start"]),
        _day(scope["development_report_start"]),
        _day(scope["development_report_end"]),
    )
    admissions, source_manifest, source_policy = read_development_admissions(
        training_root=training_root,
        node_config=node,
        scope=development_scope,
        security_codes=tuple(sorted(development_pool.values())),
        spec_digest=PROTOCOL_DIGEST,
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
        "schema_version": "quanthus.stage-positive-instability-candidate-grid.v1",
        "threshold_basis_digest": digest(basis),
        "candidate_count": len(candidates),
        "candidates": candidates,
        "parameter_search_performed": False,
    }

    selection = select_development_cases(development_pool, feature_rows, config)
    if selection["selected_case_count"] > config["limits"]["maximum_development_cases"]:
        raise PositiveInstabilityPrepareError("development_case_budget")
    retained = _retained_rows(admissions, selection["selected"])
    selected_codes = tuple(sorted(item["security_code"] for item in selection["selected"]))
    evidence = build_evidence(
        retained,
        [
            (item["security_code"], date.fromisoformat(item["market_date"]))
            for item in selection["selected"]
        ],
        InputPolicy(PROTOCOL_DIGEST, selected_codes, source_policy.calendar_ref),
        code_revision=code_revision,
        source_kind="retrospective_development",
        sampling_strata={
            (item["security_code"], date.fromisoformat(item["market_date"])): "representative"
            for item in selection["selected"]
        },
    )
    validate_evidence(evidence)
    selected_paths = _selected_path_facts(evidence, path_values)
    evaluation_plan = _evaluation_plan(evaluation_pool, config)
    if evaluation_plan["identity_count"] != config["evaluation_selection"]["planned_identities"]:
        raise PositiveInstabilityPrepareError("evaluation_identity_plan_shortfall")

    source_binding = {
        "schema_version": "quanthus.stage-positive-instability-d1-source-binding.v1",
        "stage007_prepare_manifest_sha256": stage007_verified["manifest_sha256"],
        "stage007_excluded_identity_digest": digest(stage007_binding["excluded_economic_ids"]),
        "stage007_pool_digest": digest(stage007_binding["pool"]),
        "stage008_d1_manifest_sha256": stage008_verified["manifest_sha256"],
        "stage008_fresh_pool_digest": digest(dict(stage008_fresh)),
        "candidate_population_digest": digest(tuple(candidate_codes)),
        "excluded_prior_exposed_identity_count": len(excluded),
        "fresh_pool": fresh,
        "fresh_pool_digest": digest(fresh),
        "development_pool": development_pool,
        "development_pool_digest": digest(development_pool),
        "evaluation_pool": evaluation_pool,
        "evaluation_pool_digest": digest(evaluation_pool),
        "source_manifest_digest": digest(source_manifest),
        "development_read_scope": development_scope.to_dict(),
        "evaluation_market_data_read": False,
        "2022_2024_read": False,
        "2025_read": False,
    }
    selection_digest = digest(selection)
    evaluation_plan_digest = digest(evaluation_plan)
    summary = {
        "schema_version": SUMMARY_SCHEMA,
        "work_item": "P3-STAGE-009-D",
        "phase": "D1_PREPARE_2020_DEVELOPMENT_AND_FREEZE_2021_EVALUATION_IDENTITY_PLAN",
        "code_revision": code_revision,
        "preregistration_digest": PREREG_DIGEST,
        "execution_digest": EXECUTION_DIGEST,
        "fresh_pool_size": len(fresh),
        "development_identity_capacity": len(development_pool),
        "evaluation_identity_capacity": len(evaluation_pool),
        "development_selected_case_count": selection["selected_case_count"],
        "development_shortfall_count": selection["shortfall_count"],
        "development_selection_digest": selection_digest,
        "threshold_basis_digest": digest(basis),
        "candidate_grid_digest": digest(candidate_payload),
        "candidate_count": len(candidates),
        "evaluation_planned_identity_count": evaluation_plan["identity_count"],
        "evaluation_identity_digest": evaluation_plan["identity_digest"],
        "evaluation_plan_digest": evaluation_plan_digest,
        "evaluation_data_read": False,
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
        "stage-positive-instability-d1-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    run_root = output_root.expanduser().resolve() / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    payloads = {
        "source-binding.json": canonical(source_binding),
        "development-selection.json": canonical(selection),
        "evaluation-plan.json": canonical(evaluation_plan),
        "threshold-basis.json": canonical(basis),
        "candidate-grid.json": canonical(candidate_payload),
        "evidence.json": canonical(evidence),
        "path-facts.json": canonical(selected_paths),
        "summary.json": canonical(summary),
    }
    if len(payloads["evidence.json"]) > MAX_BYTES:
        raise PositiveInstabilityPrepareError("development_evidence_size_budget")
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
            "evaluation_market_data_read": False,
            "parameter_search": False,
            "candidate_frozen": False,
            "future_partitions_read": False,
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
    manifest = _object(root / "manifest.json", "positive_instability_d1_manifest")
    manifest_raw = canonical(manifest)
    ready = (root / "READY").read_text(encoding="ascii").strip()
    if manifest.get("schema_version") != ARTIFACT_SCHEMA or ready != _sha(manifest_raw):
        raise PositiveInstabilityPrepareError("d1_manifest_or_ready")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise PositiveInstabilityPrepareError("d1_code_revision")
    if (
        manifest.get("preregistration_digest") != PREREG_DIGEST
        or manifest.get("execution_digest") != EXECUTION_DIGEST
    ):
        raise PositiveInstabilityPrepareError("d1_contract_binding")
    files = manifest.get("files")
    expected = {
        "source-binding.json",
        "development-selection.json",
        "evaluation-plan.json",
        "threshold-basis.json",
        "candidate-grid.json",
        "evidence.json",
        "path-facts.json",
        "summary.json",
    }
    if not isinstance(files, Mapping) or set(files) != expected:
        raise PositiveInstabilityPrepareError("d1_inventory")
    payloads = {}
    for name in sorted(expected):
        raw = (root / name).read_bytes()
        if _sha(raw) != files[name]:
            raise PositiveInstabilityPrepareError("d1_file_digest:" + name)
        payloads[name] = json.loads(raw)
    validate_evidence(payloads["evidence.json"])
    summary = payloads["summary.json"]
    source_binding = payloads["source-binding.json"]
    if (
        summary.get("evaluation_data_read") is not False
        or summary.get("labels_used_for_threshold_basis") is not False
        or summary.get("parameter_search") is not False
        or summary.get("opinion_count") != 0
        or summary.get("candidate_frozen") is not False
        or source_binding.get("evaluation_market_data_read") is not False
        or source_binding.get("2022_2024_read") is not False
        or source_binding.get("2025_read") is not False
        or payloads["evaluation-plan.json"].get("data_read") is not False
        or payloads["threshold-basis.json"].get("labels_used") is not False
        or payloads["candidate-grid.json"].get("parameter_search_performed") is not False
    ):
        raise PositiveInstabilityPrepareError("d1_authority_claim")
    if (
        digest(payloads["development-selection.json"])
        != summary.get("development_selection_digest")
        or digest(payloads["evaluation-plan.json"]) != summary.get("evaluation_plan_digest")
        or digest(payloads["threshold-basis.json"]) != summary.get("threshold_basis_digest")
        or digest(payloads["candidate-grid.json"]) != summary.get("candidate_grid_digest")
        or digest(payloads["evidence.json"]) != summary.get("development_evidence_digest")
        or digest(payloads["path-facts.json"]) != summary.get("path_facts_digest")
        or payloads["candidate-grid.json"].get("candidate_count") != 27
    ):
        raise PositiveInstabilityPrepareError("d1_summary_payload_binding")
    if (
        set(source_binding["development_pool"]) & set(source_binding["evaluation_pool"])
        or set(source_binding["development_pool"]) | set(source_binding["evaluation_pool"])
        != set(source_binding["fresh_pool"])
    ):
        raise PositiveInstabilityPrepareError("d1_panel_identity_partition")
    return {
        "verified": True,
        "run_id": manifest["run_id"],
        "manifest_sha256": ready,
        "file_count": len(expected),
        "fresh_pool_size": summary["fresh_pool_size"],
        "development_identity_capacity": summary["development_identity_capacity"],
        "evaluation_identity_capacity": summary["evaluation_identity_capacity"],
        "development_selected_case_count": summary["development_selected_case_count"],
        "development_shortfall_count": summary["development_shortfall_count"],
        "development_selection_digest": summary["development_selection_digest"],
        "development_evidence_digest": summary["development_evidence_digest"],
        "threshold_basis_digest": summary["threshold_basis_digest"],
        "candidate_grid_digest": summary["candidate_grid_digest"],
        "candidate_count": summary["candidate_count"],
        "path_facts_digest": summary["path_facts_digest"],
        "evaluation_planned_identity_count": summary["evaluation_planned_identity_count"],
        "evaluation_identity_digest": summary["evaluation_identity_digest"],
        "evaluation_plan_digest": summary["evaluation_plan_digest"],
        "evaluation_data_read": False,
        "parameter_search": False,
        "opinion_count": 0,
        "candidate_frozen": False,
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
    print(json.dumps(result, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""P3-STAGE-008-D1 fresh-identity calibration prepare and evaluation-plan freeze.

D1 verifies the accepted Stage-007 prepare only to inherit its exclusion boundary and reconstructs
the same candidate population. It excludes the entire Stage-007 pool, selects a new 512-identity
pool, reads only 2019-2020 data for calibration identities, and freezes a 2021 evaluation identity
and quarter plan without reading 2021 evaluation bars.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.stage_learning.directional_boundary_development import CONFIG_DIGEST, load_config
from quanthus.stage_learning.order_proposition_development import build_order_observations
from quanthus.stage_learning.order_reference_protocol import (
    MAX_BYTES,
    PROTOCOL_DIGEST,
    build_evidence,
    validate_evidence,
)
from quanthus.stage_learning.order_rule_prepare import verify_prepare_artifact
from quanthus.stage_learning.source import (
    DevelopmentReadScope,
    discover_development_security_population,
    read_development_admissions,
    read_development_identity_members,
)
from quanthus.structure_directional_path_geometry import (
    CONTRACT_DIGEST as PATH_FACT_DIGEST,
)
from quanthus.structure_directional_path_geometry import build_directional_path_geometry_facts
from quanthus.structure_v2_inputs import InputPolicy, canonical, digest

DEFAULT_CONFIG = "config/stage-learning/directional-boundary-development-screen.v1.json"
ARTIFACT_SCHEMA = "quanthus.stage-directional-boundary-calibration-prepare-artifact.v1"
SUMMARY_SCHEMA = "quanthus.stage-directional-boundary-calibration-prepare-summary.v1"
_REVISION = re.compile(r"^[0-9a-f]{40}$")


class DirectionalBoundaryCalibrationPrepareError(ValueError):
    """The D1 source-isolation, selection or evidence contract was violated."""


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DirectionalBoundaryCalibrationPrepareError(f"cannot_read_{label}") from exc
    if not isinstance(value, dict):
        raise DirectionalBoundaryCalibrationPrepareError(f"{label}_must_be_object")
    return value


def _day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DirectionalBoundaryCalibrationPrepareError("invalid_date") from exc


def _rank(seed: str, purpose: str, identity: str, day: date | None = None) -> str:
    return digest({"seed": seed, "purpose": purpose, "security_id": identity, "market_date": day})


def _fresh_pool(
    members: Sequence[Mapping[str, str]],
    *,
    excluded_ids: set[str],
    stage007_pool_ids: set[str],
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
        if identity not in excluded_ids
        and identity not in stage007_pool_ids
        and all(len(by_code[code]) == 1 for code in codes)
    }
    ranked = sorted(eligible, key=lambda identity: (_rank(seed, "fresh-pool", identity), identity))
    if len(ranked) < size:
        raise DirectionalBoundaryCalibrationPrepareError("fresh_pool_capacity_insufficient")
    selected = ranked[:size]
    return {identity: eligible[identity] for identity in selected}


def _old_instability(observation: Any) -> bool:
    return observation.efficiency20_abs <= Decimal("0.40") and (
        observation.width_change20 >= Decimal("0.20")
        or observation.center_shift20_abs >= Decimal("0.07")
    )


def _order_control(observation: Any) -> bool:
    return (
        observation.efficiency20_abs <= Decimal("0.33")
        and observation.range_width20 <= Decimal("0.18")
        and observation.width_change20 <= Decimal("-0.20")
        and observation.center_shift20_abs <= Decimal("0.04")
    )


def select_calibration_cases(
    pool: Mapping[str, str], observations: Sequence[Any], config: Mapping[str, Any]
) -> dict[str, Any]:
    plan = config["calibration_selection"]
    seed = str(plan["sampling_seed"])
    code_to_identity = {code: identity for identity, code in pool.items()}
    by_identity: dict[str, list[Any]] = defaultdict(list)
    for item in observations:
        identity = code_to_identity.get(item.security_code)
        if identity is not None and item.market_date.year == plan["year"]:
            by_identity[identity].append(item)
    selected: list[dict[str, Any]] = []
    used: set[str] = set()

    coverage_ids = sorted(
        pool, key=lambda identity: (_rank(seed, "coverage-id", identity), identity)
    )[: plan["coverage_target"]]
    per_quarter = int(plan["coverage_per_quarter"])
    for index, identity in enumerate(coverage_ids):
        quarter = index // per_quarter + 1
        candidates = [
            item for item in by_identity.get(identity, [])
            if (item.market_date.month - 1) // 3 + 1 == quarter
        ]
        if not candidates:
            continue
        chosen = min(
            candidates,
            key=lambda item: (
                _rank(seed, "coverage-date", identity, item.market_date),
                item.market_date,
            ),
        )
        selected.append({
            "security_id": identity,
            "security_code": pool[identity],
            "market_date": chosen.market_date.isoformat(),
            "stratum": "coverage",
            "feature_digest": digest(chosen.feature_dict()),
        })
        used.add(identity)

    def add_stratum(name: str, target: int, predicate: Any) -> None:
        options = []
        for identity in pool:
            if identity in used:
                continue
            matches = [item for item in by_identity.get(identity, []) if predicate(item)]
            if not matches:
                continue
            chosen = min(
                matches,
                key=lambda item: (
                    _rank(seed, name + "-date", identity, item.market_date),
                    item.market_date,
                ),
            )
            options.append((identity, chosen))
        options.sort(key=lambda pair: (_rank(seed, name + "-identity", pair[0]), pair[0]))
        for identity, chosen in options[:target]:
            selected.append({
                "security_id": identity,
                "security_code": pool[identity],
                "market_date": chosen.market_date.isoformat(),
                "stratum": name,
                "feature_digest": digest(chosen.feature_dict()),
            })
            used.add(identity)

    add_stratum("old_instability_seed", int(plan["old_instability_seed_target"]), _old_instability)
    add_stratum("order_control", int(plan["order_control_target"]), _order_control)
    selected.sort(key=lambda item: (item["stratum"], item["security_id"]))
    counts = {
        name: sum(item["stratum"] == name for item in selected)
        for name in ("coverage", "old_instability_seed", "order_control")
    }
    targets = {
        "coverage": int(plan["coverage_target"]),
        "old_instability_seed": int(plan["old_instability_seed_target"]),
        "order_control": int(plan["order_control_target"]),
    }
    return {
        "schema_version": "quanthus.stage-directional-boundary-calibration-selection.v1",
        "selected": selected,
        "counts": counts,
        "shortfalls": {name: targets[name] - counts[name] for name in targets},
        "one_case_per_identity": len({item["security_id"] for item in selected}) == len(selected),
    }


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
        "schema_version": "quanthus.stage-directional-boundary-evaluation-plan.v1",
        "selection_uses_morphology": False,
        "identities": rows,
        "identity_count": len(rows),
        "identity_digest": digest(rows),
        "data_read": False,
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
            raise DirectionalBoundaryCalibrationPrepareError("calibration_case_missing_61_rows")
        rows.extend(history)
    return sorted(rows, key=lambda row: (row.security_code, row.market_date))


def _path_facts_for_cases(admissions: Sequence[Any], evidence: Mapping[str, Any]) -> dict[str, Any]:
    cases = evidence["cases"]
    dates = [date.fromisoformat(case["market_date"]) for case in cases]
    facts = build_directional_path_geometry_facts(
        admissions, report_start=min(dates), report_end=max(dates)
    )
    by_key: dict[tuple[str, date], dict[str, Any]] = defaultdict(dict)
    for item in facts.measurements:
        by_key[(item.security_code, item.market_date)][item.concept_id] = {
            "value": None if item.value is None else str(item.value),
            "reason": item.availability_reason,
            "revision_id": item.identity.revision_id,
        }
    output = {}
    for case in cases:
        key = (case["security_code"], date.fromisoformat(case["market_date"]))
        values = by_key.get(key, {})
        if len(values) != 2:
            raise DirectionalBoundaryCalibrationPrepareError("calibration_path_facts_unavailable")
        output[case["case_id"]] = values
    return {
        "schema_version": "quanthus.stage-directional-boundary-path-facts.v1",
        "fact_contract_digest": PATH_FACT_DIGEST,
        "cases": output,
    }


def run_prepare(
    *, config_path: Path, training_root: Path, output_root: Path, code_revision: str,
) -> dict[str, Any]:
    if not _REVISION.fullmatch(code_revision):
        raise DirectionalBoundaryCalibrationPrepareError("exact_code_revision_required")
    root = find_project_root()
    config = load_config(root)
    if digest(config) != CONFIG_DIGEST:
        raise DirectionalBoundaryCalibrationPrepareError("development_config_drift")
    training_root = training_root.expanduser().resolve()
    stage007 = config["bindings"]["stage_007_prepare"]
    prior_root = (training_root / stage007["artifact_relative_path"]).resolve()
    verified = verify_prepare_artifact(prior_root, code_revision=stage007["code_revision"])
    if verified["manifest_sha256"] != stage007["manifest_sha256"]:
        raise DirectionalBoundaryCalibrationPrepareError("stage007_prepare_binding")
    prior_binding = _object(prior_root / "source-binding.json", "stage007_source_binding")
    if (
        len(prior_binding.get("pool", {})) != stage007["pool_size"]
        or len(prior_binding.get("excluded_economic_ids", []))
        != stage007["excluded_economic_identity_count"]
        or prior_binding.get("candidate_population_count") != stage007["candidate_population_count"]
    ):
        raise DirectionalBoundaryCalibrationPrepareError("stage007_exclusion_boundary_drift")

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
    if digest(tuple(candidate_codes)) != prior_binding["candidate_population_digest"]:
        raise DirectionalBoundaryCalibrationPrepareError(
            "candidate_population_differs_from_stage007"
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
    identity_plan = config["fresh_identity_plan"]
    fresh = _fresh_pool(
        pairs,
        excluded_ids=set(prior_binding["excluded_economic_ids"]),
        stage007_pool_ids=set(prior_binding["pool"]),
        seed=str(identity_plan["fresh_pool_seed"]),
        size=int(identity_plan["fresh_pool_size"]),
    )
    ranked_fresh = list(fresh)
    calibration_pool = {
        identity: fresh[identity]
        for identity in ranked_fresh[: int(identity_plan["calibration_identity_capacity"])]
    }
    evaluation_pool = {
        identity: fresh[identity]
        for identity in ranked_fresh[int(identity_plan["calibration_identity_capacity"]):]
    }
    if set(calibration_pool) & set(evaluation_pool):
        raise DirectionalBoundaryCalibrationPrepareError("panel_identity_overlap")

    calibration_scope = DevelopmentReadScope(
        _day(scope["history_start"]),
        _day(scope["calibration_report_start"]),
        _day(scope["calibration_report_end"]),
    )
    admissions, source_manifest, source_policy = read_development_admissions(
        training_root=training_root,
        node_config=node,
        scope=calibration_scope,
        security_codes=tuple(sorted(calibration_pool.values())),
        spec_digest=PROTOCOL_DIGEST,
    )
    observations, build_counts = build_order_observations(
        admissions,
        report_start=_day(scope["calibration_report_start"]),
        report_end=_day(scope["calibration_report_end"]),
    )
    selection = select_calibration_cases(calibration_pool, observations, config)
    selected = selection["selected"]
    if len(selected) > config["limits"]["maximum_calibration_cases"]:
        raise DirectionalBoundaryCalibrationPrepareError("calibration_case_budget")
    retained = _retained_rows(admissions, selected)
    selected_codes = tuple(sorted(item["security_code"] for item in selected))
    evidence = build_evidence(
        retained,
        [(item["security_code"], date.fromisoformat(item["market_date"])) for item in selected],
        InputPolicy(PROTOCOL_DIGEST, selected_codes, source_policy.calendar_ref),
        code_revision=code_revision,
        source_kind="retrospective_development",
        sampling_strata={
            (item["security_code"], date.fromisoformat(item["market_date"])): (
                "representative" if item["stratum"] == "coverage" else "enriched"
            )
            for item in selected
        },
    )
    validate_evidence(evidence)
    selected_admissions = tuple(
        attempt for attempt in admissions if attempt.row.security_code in set(selected_codes)
    )
    path_facts = _path_facts_for_cases(selected_admissions, evidence)
    evaluation_plan = _evaluation_plan(evaluation_pool, config)
    if evaluation_plan["identity_count"] != config["evaluation_selection"]["planned_identities"]:
        raise DirectionalBoundaryCalibrationPrepareError("evaluation_identity_plan_shortfall")
    selection_digest = digest(selection)
    evaluation_plan_digest = digest(evaluation_plan)

    source_binding = {
        "schema_version": "quanthus.stage-directional-boundary-d1-source-binding.v1",
        "stage007_prepare_manifest_sha256": verified["manifest_sha256"],
        "stage007_excluded_identity_digest": digest(prior_binding["excluded_economic_ids"]),
        "stage007_pool_digest": digest(prior_binding["pool"]),
        "candidate_population_digest": digest(tuple(candidate_codes)),
        "fresh_pool": fresh,
        "fresh_pool_digest": digest(fresh),
        "calibration_pool": calibration_pool,
        "calibration_pool_digest": digest(calibration_pool),
        "evaluation_pool": evaluation_pool,
        "evaluation_pool_digest": digest(evaluation_pool),
        "source_manifest_digest": digest(source_manifest),
        "development_read_scope": calibration_scope.to_dict(),
        "evaluation_market_data_read": False,
    }
    summary = {
        "schema_version": SUMMARY_SCHEMA,
        "work_item": "P3-STAGE-008-D",
        "phase": "D1_PREPARE_CALIBRATION_AND_FREEZE_EVALUATION_IDENTITY_PLAN",
        "code_revision": code_revision,
        "config_digest": CONFIG_DIGEST,
        "fresh_pool_size": len(fresh),
        "calibration_identity_capacity": len(calibration_pool),
        "evaluation_identity_capacity": len(evaluation_pool),
        "calibration_case_count": len(selected),
        "calibration_selection_counts": selection["counts"],
        "calibration_selection_shortfalls": selection["shortfalls"],
        "calibration_selection_digest": selection_digest,
        "evaluation_planned_identity_count": evaluation_plan["identity_count"],
        "evaluation_identity_digest": evaluation_plan["identity_digest"],
        "evaluation_plan_digest": evaluation_plan_digest,
        "evaluation_data_read": False,
        "fact_build_counts": build_counts,
        "calibration_evidence_digest": digest(evidence),
        "path_facts_digest": digest(path_facts),
        "parameter_search": False,
        "opinion_count": 0,
        "candidate_frozen": False,
        "future_partitions_read": False,
    }
    run_id = (
        "stage-directional-boundary-d1-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    run_root = output_root.expanduser().resolve() / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    payloads = {
        "source-binding.json": canonical(source_binding),
        "calibration-selection.json": canonical(selection),
        "evaluation-plan.json": canonical(evaluation_plan),
        "evidence.json": canonical(evidence),
        "path-facts.json": canonical(path_facts),
        "summary.json": canonical(summary),
    }
    if len(payloads["evidence.json"]) > MAX_BYTES:
        raise DirectionalBoundaryCalibrationPrepareError("calibration_evidence_size_budget")
    for name, raw in payloads.items():
        (run_root / name).write_bytes(raw)
    manifest = {
        "schema_version": ARTIFACT_SCHEMA,
        "run_id": run_id,
        "code_revision": code_revision,
        "config_digest": CONFIG_DIGEST,
        "files": {name: _sha(raw) for name, raw in payloads.items()},
        "authority": {
            "evaluation_market_data_read": False,
            "parameter_search": False,
            "candidate_frozen": False,
            "future_partitions_read": False,
        },
    }
    manifest_raw = canonical(manifest)
    (run_root / "manifest.json").write_bytes(manifest_raw)
    ready = _sha(manifest_raw)
    (run_root / "READY").write_text(ready + "\n", encoding="ascii")
    return {**summary, "run_id": run_id, "run_root": str(run_root), "manifest_sha256": ready}


def verify_artifact(path: Path, *, code_revision: str | None = None) -> dict[str, Any]:
    root = path.expanduser().resolve()
    manifest = _object(root / "manifest.json", "d1_manifest")
    manifest_raw = canonical(manifest)
    ready = (root / "READY").read_text(encoding="ascii").strip()
    if manifest.get("schema_version") != ARTIFACT_SCHEMA or ready != _sha(manifest_raw):
        raise DirectionalBoundaryCalibrationPrepareError("d1_manifest_or_ready")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise DirectionalBoundaryCalibrationPrepareError("d1_code_revision")
    files = manifest.get("files")
    expected = {
        "source-binding.json", "calibration-selection.json", "evaluation-plan.json",
        "evidence.json", "path-facts.json", "summary.json",
    }
    if not isinstance(files, Mapping) or set(files) != expected:
        raise DirectionalBoundaryCalibrationPrepareError("d1_inventory")
    payloads = {}
    for name in sorted(expected):
        raw = (root / name).read_bytes()
        if _sha(raw) != files[name]:
            raise DirectionalBoundaryCalibrationPrepareError("d1_file_digest:" + name)
        payloads[name] = json.loads(raw)
    validate_evidence(payloads["evidence.json"])
    summary = payloads["summary.json"]
    if (
        summary.get("evaluation_data_read") is not False
        or summary.get("parameter_search") is not False
        or summary.get("opinion_count") != 0
        or summary.get("candidate_frozen") is not False
        or payloads["evaluation-plan.json"].get("data_read") is not False
    ):
        raise DirectionalBoundaryCalibrationPrepareError("d1_authority_claim")
    if (
        digest(payloads["calibration-selection.json"])
        != summary.get("calibration_selection_digest")
        or digest(payloads["evaluation-plan.json"]) != summary.get("evaluation_plan_digest")
        or payloads["evaluation-plan.json"].get("identity_digest")
        != summary.get("evaluation_identity_digest")
        or digest(payloads["evidence.json"]) != summary.get("calibration_evidence_digest")
        or digest(payloads["path-facts.json"]) != summary.get("path_facts_digest")
    ):
        raise DirectionalBoundaryCalibrationPrepareError("d1_summary_payload_binding")
    return {
        "verified": True,
        "run_id": manifest["run_id"],
        "manifest_sha256": ready,
        "file_count": len(expected),
        "fresh_pool_size": summary["fresh_pool_size"],
        "calibration_identity_capacity": summary["calibration_identity_capacity"],
        "evaluation_identity_capacity": summary["evaluation_identity_capacity"],
        "calibration_case_count": summary["calibration_case_count"],
        "calibration_selection_counts": summary["calibration_selection_counts"],
        "calibration_selection_shortfalls": summary["calibration_selection_shortfalls"],
        "calibration_selection_digest": summary["calibration_selection_digest"],
        "calibration_evidence_digest": summary["calibration_evidence_digest"],
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

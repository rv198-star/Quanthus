"""P3-STAGE-008-D3 morphology-blind evaluation evidence prepare.

Reads only the exact D1-frozen evaluation identities after D2 candidate freeze. It selects one
primary-available 2021 date per assigned quarter by stable hash, publishes T-only evidence and path
Facts, and performs no opinion generation, candidate scoring, parameter search, or future access.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.stage_learning.directional_boundary_calibration_prepare import (
    verify_artifact as verify_d1,
)
from quanthus.stage_learning.order_proposition_development import build_order_observations
from quanthus.stage_learning.order_reference_protocol import (
    MAX_BYTES,
    PROTOCOL_DIGEST,
    build_evidence,
    validate_evidence,
)
from quanthus.stage_learning.source import DevelopmentReadScope, read_development_admissions
from quanthus.structure_directional_path_geometry import build_directional_path_geometry_facts
from quanthus.structure_v2_inputs import InputPolicy, canonical, digest

CONFIG_PATH = "config/stage-learning/directional-boundary-evaluation-prepare.v1.json"
CONFIG_DIGEST = "sha256:08a06449ef2f49ada7dcfd588fe655befa8779c8d4b45d0d297db51f1719fde2"
ARTIFACT_SCHEMA = "quanthus.stage-directional-boundary-evaluation-prepare-artifact.v1"
SUMMARY_SCHEMA = "quanthus.stage-directional-boundary-evaluation-prepare-summary.v1"


class DirectionalBoundaryEvaluationPrepareError(ValueError):
    """D3 evaluation prepare violates the frozen D1/D2 boundary."""


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DirectionalBoundaryEvaluationPrepareError(f"cannot_read_{label}") from exc
    if not isinstance(value, dict):
        raise DirectionalBoundaryEvaluationPrepareError(f"{label}_must_be_object")
    return value


def load_config(root: Path | None = None) -> dict[str, Any]:
    root = root or find_project_root()
    value = _object(root / CONFIG_PATH, "d3_config")
    if digest(value) != CONFIG_DIGEST:
        raise DirectionalBoundaryEvaluationPrepareError("d3_config_drift")
    candidate = _object(root / value["frozen_candidate"]["path"], "frozen_candidate")
    if (
        digest(candidate) != value["frozen_candidate"]["digest"]
        or candidate["frozen_candidate"]["candidate_id"]
        != value["frozen_candidate"]["candidate_id"]
        or candidate["d1_prepare"]["evaluation_data_read_at_candidate_freeze"] is not False
    ):
        raise DirectionalBoundaryEvaluationPrepareError("candidate_freeze_binding")
    screen = _object(root / value["development_screen"]["path"], "development_screen")
    if digest(screen) != value["development_screen"]["digest"]:
        raise DirectionalBoundaryEvaluationPrepareError("development_screen_binding")
    return value


def _rank(seed: str, identity: str, day: date) -> str:
    return digest({"seed": seed, "purpose": "evaluation-date", "security_id": identity,
                   "market_date": day})


def select_evaluation_cases(
    evaluation_plan: Mapping[str, Any], observations: Sequence[Any], config: Mapping[str, Any]
) -> dict[str, Any]:
    rows = evaluation_plan.get("identities")
    if (
        evaluation_plan.get("selection_uses_morphology") is not False
        or evaluation_plan.get("data_read") is not False
        or not isinstance(rows, list)
        or len(rows) != config["d1_prepare"]["evaluation_identity_count"]
    ):
        raise DirectionalBoundaryEvaluationPrepareError("evaluation_plan_shape")
    by_code: dict[str, list[Any]] = defaultdict(list)
    for obs in observations:
        by_code[obs.security_code].append(obs)
    seed = str(config["selection"]["sampling_seed"])
    selected = []
    shortfalls = []
    seen_ids = set()
    for plan in rows:
        identity = str(plan["security_id"])
        code = str(plan["security_code"])
        quarter = int(plan["quarter"])
        if identity in seen_ids or quarter not in {1, 2, 3, 4}:
            raise DirectionalBoundaryEvaluationPrepareError("evaluation_plan_identity")
        seen_ids.add(identity)
        eligible = [
            obs for obs in by_code.get(code, [])
            if ((obs.market_date.month - 1) // 3 + 1) == quarter
        ]
        if not eligible:
            shortfalls.append({"security_id": identity, "security_code": code, "quarter": quarter})
            continue
        chosen = min(
            eligible,
            key=lambda obs: (_rank(seed, identity, obs.market_date), obs.market_date),
        )
        selected.append({
            "security_id": identity,
            "security_code": code,
            "quarter": quarter,
            "market_date": chosen.market_date.isoformat(),
            "date_selection_hash": _rank(seed, identity, chosen.market_date),
        })
    return {
        "schema_version": "quanthus.stage-directional-boundary-evaluation-selection.v1",
        "selection_uses_morphology": False,
        "selected": selected,
        "selected_case_count": len(selected),
        "shortfalls": shortfalls,
        "shortfall_count": len(shortfalls),
        "cross_quarter_backfill": False,
        "identity_backfill": False,
        "result_dependent_replacement": False,
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
            raise DirectionalBoundaryEvaluationPrepareError("evaluation_case_missing_61_rows")
        if history[-1].security_id != item["security_id"]:
            raise DirectionalBoundaryEvaluationPrepareError("evaluation_economic_identity_changed")
        rows.extend(history)
    unique = {(row.security_code, row.market_date): row for row in rows}
    if len(unique) != len(rows):
        raise DirectionalBoundaryEvaluationPrepareError("evaluation_history_overlap")
    return [unique[key] for key in sorted(unique)]


def _path_facts(admissions: Sequence[Any], evidence: Mapping[str, Any]) -> dict[str, Any]:
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
        key = case["security_code"], date.fromisoformat(case["market_date"])
        values = by_key.get(key, {})
        if len(values) != 2:
            raise DirectionalBoundaryEvaluationPrepareError("evaluation_path_facts_unavailable")
        output[case["case_id"]] = values
    return {"schema_version": "quanthus.stage-directional-boundary-path-facts.v1", "cases": output}


def run_prepare(
    *, config_path: Path, training_root: Path, output_root: Path, code_revision: str
) -> dict[str, Any]:
    root = find_project_root()
    config = load_config(root)
    if config_path.resolve() != (root / CONFIG_PATH).resolve():
        raise DirectionalBoundaryEvaluationPrepareError("d3_config_path")
    training_root = training_root.expanduser().resolve()
    d1 = config["d1_prepare"]
    d1_root = (training_root / d1["artifact_relative_path"]).resolve()
    verified = verify_d1(d1_root, code_revision=d1["code_revision"])
    if verified["manifest_sha256"] != d1["manifest_sha256"]:
        raise DirectionalBoundaryEvaluationPrepareError("d1_manifest_binding")
    evaluation_plan = _object(d1_root / "evaluation-plan.json", "evaluation_plan")
    if (
        digest(evaluation_plan) != d1["evaluation_plan_digest"]
        or evaluation_plan.get("identity_digest") != d1["evaluation_identity_digest"]
        or evaluation_plan.get("identity_count") != d1["evaluation_identity_count"]
    ):
        raise DirectionalBoundaryEvaluationPrepareError("evaluation_plan_binding")

    node = _object(root / config["training_node_config"], "training_node_config")
    scope = config["source_scope"]
    codes = tuple(sorted({str(item["security_code"]) for item in evaluation_plan["identities"]}))
    read_scope = DevelopmentReadScope(
        date.fromisoformat(scope["history_start"]),
        date.fromisoformat(scope["report_start"]),
        date.fromisoformat(scope["report_end"]),
    )
    admissions, source_manifest, source_policy = read_development_admissions(
        training_root=training_root,
        node_config=node,
        scope=read_scope,
        security_codes=codes,
        spec_digest=PROTOCOL_DIGEST,
    )
    observations, build_counts = build_order_observations(
        admissions,
        report_start=date.fromisoformat(scope["report_start"]),
        report_end=date.fromisoformat(scope["report_end"]),
    )
    selection = select_evaluation_cases(evaluation_plan, observations, config)
    if selection["selected_case_count"] > config["limits"]["maximum_evaluation_cases"]:
        raise DirectionalBoundaryEvaluationPrepareError("evaluation_case_budget")
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
    selected_set = set(selected_codes)
    path_facts = _path_facts(
        tuple(
            attempt
            for attempt in admissions
            if attempt.row.security_code in selected_set
        ),
        evidence,
    )
    summary = {
        "schema_version": SUMMARY_SCHEMA,
        "work_item": "P3-STAGE-008-D",
        "phase": "D3_PREPARE_EVALUATION_AFTER_CANDIDATE_FREEZE",
        "code_revision": code_revision,
        "config_digest": CONFIG_DIGEST,
        "frozen_candidate_id": config["frozen_candidate"]["candidate_id"],
        "evaluation_identity_count": evaluation_plan["identity_count"],
        "evaluation_selected_case_count": selection["selected_case_count"],
        "evaluation_shortfall_count": selection["shortfall_count"],
        "evaluation_selection_digest": digest(selection),
        "evaluation_evidence_digest": digest(evidence),
        "path_facts_digest": digest(path_facts),
        "source_manifest_digest": digest(source_manifest),
        "fact_build_counts": build_counts,
        "opinion_count": 0,
        "candidate_scored": False,
        "parameter_search": False,
        "candidate_replacement": False,
        "2022_2024_read": False,
        "2025_read": False,
        "future_outcome_read": False,
        "review_ready": True,
    }
    run_id = (
        "stage-directional-boundary-d3-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    run_root = output_root.expanduser().resolve() / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    payloads = {
        "evaluation-selection.json": canonical(selection),
        "evidence.json": canonical(evidence),
        "path-facts.json": canonical(path_facts),
        "summary.json": canonical(summary),
    }
    if len(payloads["evidence.json"]) > MAX_BYTES:
        raise DirectionalBoundaryEvaluationPrepareError("evaluation_evidence_size_budget")
    for name, raw in payloads.items():
        (run_root / name).write_bytes(raw)
    manifest = {
        "schema_version": ARTIFACT_SCHEMA,
        "run_id": run_id,
        "code_revision": code_revision,
        "config_digest": CONFIG_DIGEST,
        "candidate_contract_digest": config["frozen_candidate"]["digest"],
        "files": {name: _sha(raw) for name, raw in payloads.items()},
        "authority": {"opinion_generation": False, "candidate_scoring": False,
                      "parameter_search": False, "future_outcome_access": False},
    }
    manifest_raw = canonical(manifest)
    (run_root / "manifest.json").write_bytes(manifest_raw)
    ready = _sha(manifest_raw)
    (run_root / "READY").write_text(ready + "\n", encoding="ascii")
    return {**summary, "run_id": run_id, "run_root": str(run_root), "manifest_sha256": ready}


def verify_artifact(path: Path, *, code_revision: str | None = None) -> dict[str, Any]:
    root = path.expanduser().resolve()
    manifest = _object(root / "manifest.json", "d3_manifest")
    raw_manifest = canonical(manifest)
    ready = (root / "READY").read_text(encoding="ascii").strip()
    if manifest.get("schema_version") != ARTIFACT_SCHEMA or ready != _sha(raw_manifest):
        raise DirectionalBoundaryEvaluationPrepareError("d3_manifest_or_ready")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise DirectionalBoundaryEvaluationPrepareError("d3_code_revision")
    expected = {"evaluation-selection.json", "evidence.json", "path-facts.json", "summary.json"}
    if set(manifest.get("files", {})) != expected:
        raise DirectionalBoundaryEvaluationPrepareError("d3_inventory")
    payloads = {}
    for name in expected:
        raw = (root / name).read_bytes()
        if _sha(raw) != manifest["files"][name]:
            raise DirectionalBoundaryEvaluationPrepareError("d3_file_digest:" + name)
        payloads[name] = json.loads(raw)
    validate_evidence(payloads["evidence.json"])
    summary = payloads["summary.json"]
    if (
        summary.get("opinion_count") != 0
        or summary.get("candidate_scored") is not False
        or summary.get("parameter_search") is not False
        or summary.get("candidate_replacement") is not False
        or summary.get("2022_2024_read") is not False
        or summary.get("2025_read") is not False
        or summary.get("future_outcome_read") is not False
        or summary.get("review_ready") is not True
    ):
        raise DirectionalBoundaryEvaluationPrepareError("d3_authority_claim")
    if (
        digest(payloads["evaluation-selection.json"]) != summary["evaluation_selection_digest"]
        or digest(payloads["evidence.json"]) != summary["evaluation_evidence_digest"]
        or digest(payloads["path-facts.json"]) != summary["path_facts_digest"]
    ):
        raise DirectionalBoundaryEvaluationPrepareError("d3_summary_binding")
    return {"verified": True, "run_id": manifest["run_id"], "manifest_sha256": ready,
            "file_count": len(expected), **summary}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--training-root", type=Path, required=True)
    run.add_argument("--output-root", type=Path, required=True)
    run.add_argument("--code-revision", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--artifact", type=Path, required=True)
    verify.add_argument("--code-revision")
    args = parser.parse_args(argv)
    if args.command == "run":
        result = run_prepare(
            config_path=args.config,
            training_root=args.training_root,
            output_root=args.output_root,
            code_revision=args.code_revision,
        )
        print(json.dumps(result, sort_keys=True, default=str))
    else:
        print(json.dumps(verify_artifact(args.artifact, code_revision=args.code_revision),
                         sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

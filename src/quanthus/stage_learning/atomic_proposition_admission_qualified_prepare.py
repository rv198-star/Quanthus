"""P3-STAGE-014-C admission-qualified atomic-first result-bearing prepare.

The runner consumes only the exact P014-B admission-qualified 61-session identity-windows. It
preserves the P012 atomic candidate surfaces, numeric gates and resolver, publishes only blind
primary evidence for review, and keeps preserved 2021 plus later surfaces unread.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.stage_learning.atomic_proposition_development import (
    instability_candidate_grid,
    stage2_candidate_grid,
    threshold_basis,
)
from quanthus.stage_learning.atomic_proposition_development import (
    load_execution as load_p012_execution,
)
from quanthus.stage_learning.atomic_proposition_development import (
    load_preregistration as load_p012_preregistration,
)
from quanthus.stage_learning.atomic_proposition_development_prepare import (
    _envelope_measurements,
    _verify_isolation,
)
from quanthus.stage_learning.atomic_proposition_successor_development_prepare import (
    _case_selection,
    _read_exact_plan_windows,
)
from quanthus.stage_learning.development_resource_qualified_plan import (
    verify_artifact as verify_qualified_plan_artifact,
)
from quanthus.stage_learning.order_proposition_development import build_order_observations
from quanthus.stage_learning.order_reference_protocol import (
    MAX_BYTES,
    build_evidence,
    load_protocol_contract,
    validate_evidence,
)
from quanthus.stage_learning.positive_instability_development_prepare import (
    _combined_feature_rows,
    _path_map,
    _retained_rows,
    _selected_path_facts,
)
from quanthus.structure_v2_inputs import InputPolicy, canonical, digest

DEFAULT_CONFIG = (
    "config/stage-learning/"
    "stage-atomic-proposition-admission-qualified-development-screen.v1.json"
)
EXECUTION_PATH = (
    "config/stage-learning/stage-atomic-proposition-admission-qualified-execution.v1.json"
)
PREREG_DIGEST = "sha256:ba0b2929beb5bc17e857acf17488fa8360cddf05ff91ec7c3d7fbbf99e10a728"
EXECUTION_DIGEST = "sha256:249416713349f1e17da6c4f18d424ebae8afd3572cc5a645d3e5e4896beb0108"
EVIDENCE_SCOPE_PATH = "config/stage-learning/order-reference-evidence-scope-p013.v1.json"
EVIDENCE_SCOPE_DIGEST = "sha256:54587544cbeebbceaa31b50a878180cec851743ab90e1e42aff06c87fd86de85"
ARTIFACT_SCHEMA = "quanthus.stage-atomic-proposition-admission-qualified-d1-artifact.v1"
SUMMARY_SCHEMA = "quanthus.stage-atomic-proposition-admission-qualified-d1-summary.v1"
_REVISION = re.compile(r"^[0-9a-f]{40}$")


class AtomicPropositionAdmissionQualifiedError(ValueError):
    """The frozen P014-C semantic prepare contract was violated."""


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AtomicPropositionAdmissionQualifiedError(f"cannot_read_{label}") from exc
    if not isinstance(value, dict):
        raise AtomicPropositionAdmissionQualifiedError(f"{label}_must_be_object")
    return value


def _load_bound(root: Path, path: str, expected_digest: str, label: str) -> dict[str, Any]:
    value = _object(root / path, label)
    if digest(value) != expected_digest:
        raise AtomicPropositionAdmissionQualifiedError(f"{label}_drift")
    return value


def load_preregistration(root: Path | None = None) -> dict[str, Any]:
    root = root or find_project_root()
    value = _load_bound(root, DEFAULT_CONFIG, PREREG_DIGEST, "p014_preregistration")
    if value.get("state") != "PREREGISTERED_QUALIFIED_PLAN_BEFORE_SEMANTIC_MARKET_READ":
        raise AtomicPropositionAdmissionQualifiedError("p014_preregistration_state")
    return value


def load_execution(root: Path | None = None) -> dict[str, Any]:
    root = root or find_project_root()
    value = _load_bound(root, EXECUTION_PATH, EXECUTION_DIGEST, "p014_execution")
    if (
        value["preregistration"]["canonical_digest"] != PREREG_DIGEST
        or value["evidence_protocol"]["canonical_digest"] != EVIDENCE_SCOPE_DIGEST
    ):
        raise AtomicPropositionAdmissionQualifiedError("p014_execution_binding")
    return value


def _verified_plan(
    training_root: Path,
    binding: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    artifact_root = (training_root / str(binding["artifact_relative_path"])).resolve()
    verified = verify_qualified_plan_artifact(
        artifact_root,
        code_revision=str(binding["code_revision"]),
    )
    if (
        verified.get("manifest_sha256") != binding["manifest_sha256"]
        or verified.get("plan_digest") != binding["plan_digest"]
        or verified.get("identity_digest") != binding["identity_digest"]
        or verified.get("row_count") != binding["row_count"]
        or verified.get("unique_identity_count") != binding["unique_identity_count"]
        or verified.get("primary_count") != binding["primary_count"]
        or verified.get("fallback_count") != binding["fallback_count"]
    ):
        raise AtomicPropositionAdmissionQualifiedError("qualified_plan_artifact_binding")
    manifest = json.loads((artifact_root / "manifest.json").read_bytes())
    raw = (artifact_root / "qualified-plan.json").read_bytes()
    if manifest.get("files", {}).get("qualified-plan.json") != _sha(raw):
        raise AtomicPropositionAdmissionQualifiedError("qualified_plan_file_binding")
    plan = json.loads(raw)
    if not isinstance(plan, dict) or digest(plan.get("rows")) != binding["plan_digest"]:
        raise AtomicPropositionAdmissionQualifiedError("qualified_plan_payload_binding")
    return plan, verified


def _runtime_plan_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for raw in rows:
        row = dict(raw)
        if row.get("role") not in {"primary", "fallback"}:
            raise AtomicPropositionAdmissionQualifiedError("qualified_plan_role")
        output.append(
            {
                **row,
                "cell_slot": int(row["plan_slot"]),
                "open_session_count": 61,
            }
        )
    if len(output) != 256 or len({row["security_id"] for row in output}) != 256:
        raise AtomicPropositionAdmissionQualifiedError("qualified_plan_identity_count")
    return output


def run_prepare(
    *, config_path: Path, training_root: Path, output_root: Path, code_revision: str
) -> dict[str, Any]:
    if not _REVISION.fullmatch(code_revision):
        raise AtomicPropositionAdmissionQualifiedError("exact_code_revision_required")
    root = find_project_root()
    prereg = load_preregistration(root)
    execution = load_execution(root)
    if config_path.resolve() != (root / DEFAULT_CONFIG).resolve():
        supplied = _object(config_path, "p014_development_screen")
        if digest(supplied) != PREREG_DIGEST:
            raise AtomicPropositionAdmissionQualifiedError("p014_screen_config_drift")

    p012_config = load_p012_preregistration(root)
    p012_execution = load_p012_execution(root)
    if (
        execution["p012_atomic_contract"]["preregistration_digest"]
        != digest(p012_config)
        or execution["p012_atomic_contract"]["execution_digest"] != digest(p012_execution)
    ):
        raise AtomicPropositionAdmissionQualifiedError("p012_atomic_contract_binding")
    if (
        execution["p012_atomic_contract"]["instability_candidate_count"] != 9
        or execution["p012_atomic_contract"]["relocation_candidate_count"] != 1
        or execution["p012_atomic_contract"]["order_profile_count"] != 3
        or execution["p012_atomic_contract"]["joint_pair_search_count"] != 0
    ):
        raise AtomicPropositionAdmissionQualifiedError("p012_atomic_surface_binding")

    protocol = load_protocol_contract(EVIDENCE_SCOPE_PATH, EVIDENCE_SCOPE_DIGEST, root)
    training_root = training_root.expanduser().resolve()
    plan, plan_verified = _verified_plan(training_root, execution["qualified_plan_artifact"])
    plan_rows_raw = plan.get("rows")
    if not isinstance(plan_rows_raw, list):
        raise AtomicPropositionAdmissionQualifiedError("qualified_plan_rows")
    plan_rows = _runtime_plan_rows(plan_rows_raw)

    _upstream, _excluded, preserved_plan = _verify_isolation(
        root=root,
        training_root=training_root,
        execution=p012_execution,
    )
    preserved_ids = {str(item["security_id"]) for item in preserved_plan["identities"]}
    if {str(item["security_id"]) for item in plan_rows} & preserved_ids:
        raise AtomicPropositionAdmissionQualifiedError("qualified_plan_preserved_overlap")

    node = _object(root / execution["training_node_config"], "training_node_config")
    admissions, read_binding, calendar_ref = _read_exact_plan_windows(
        training_root=training_root,
        node=node,
        rows=plan_rows,
    )
    report_start = min(date.fromisoformat(str(item["window_start"])) for item in plan_rows)
    report_end = max(date.fromisoformat(str(item["window_end"])) for item in plan_rows)
    observations, fact_build_counts = build_order_observations(
        admissions,
        report_start=report_start,
        report_end=report_end,
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
        "fixed_envelope_channel": p012_config["instability_candidate_surface"][
            "fixed_additive_channel"
        ],
        "order_candidates": order_candidates,
        "parameter_search_performed": False,
        "grid_expanded_after_result": False,
    }

    selection = _case_selection(plan_rows, feature_rows)
    retained = _retained_rows(admissions, selection["selected"])
    selected_codes = tuple(sorted({item["security_code"] for item in selection["selected"]}))
    evidence = build_evidence(
        retained,
        [
            (item["security_code"], date.fromisoformat(item["market_date"]))
            for item in selection["selected"]
        ],
        InputPolicy(EVIDENCE_SCOPE_DIGEST, selected_codes, calendar_ref),
        code_revision=code_revision,
        source_kind="retrospective_development",
        sampling_strata={
            (item["security_code"], date.fromisoformat(item["market_date"])): "representative"
            for item in selection["selected"]
        },
        protocol_override=protocol,
        protocol_digest=EVIDENCE_SCOPE_DIGEST,
    )
    validate_evidence(evidence, expected_protocol_digest=EVIDENCE_SCOPE_DIGEST)
    selected_paths = _selected_path_facts(evidence, path_values)
    envelopes = _envelope_measurements(evidence, retained)

    selected_by_key = {
        (item["security_code"], item["market_date"]): item for item in selection["selected"]
    }
    case_strata: dict[str, dict[str, str]] = {}
    for case in evidence["cases"]:
        key = (str(case["security_code"]), str(case["market_date"]))
        selected_case = selected_by_key[key]
        case_strata[str(case["case_id"])] = {
            "era": selected_case["era"],
            "board_family": selected_case["board_family"],
            "cell": selected_case["cell"],
            "plan_role": selected_case["plan_role"],
            "window_id": selected_case["window_id"],
        }
    cell_counts = Counter(item["cell"] for item in selection["selected"])
    if len(cell_counts) != 8 or any(value != 24 for value in cell_counts.values()):
        raise AtomicPropositionAdmissionQualifiedError("selected_cell_balance")

    source_binding = {
        "schema_version": (
            "quanthus.stage-atomic-proposition-admission-qualified-d1-source-binding.v1"
        ),
        "qualified_plan_job_id": execution["qualified_plan_artifact"]["job_id"],
        "qualified_plan_manifest_sha256": plan_verified["manifest_sha256"],
        "qualified_plan_digest": plan_verified["plan_digest"],
        "qualified_plan_identity_digest": plan_verified["identity_digest"],
        "qualified_plan_row_count": plan_verified["row_count"],
        "qualified_plan_unique_identity_count": plan_verified["unique_identity_count"],
        "input_admission_qualified_before_semantic_read": prereg["resource_plan"][
            "input_admission_qualified_before_plan_freeze"
        ],
        "market_read_binding": read_binding,
        "development_preserved_evaluation_overlap": 0,
        "preserved_evaluation_identity_digest": preserved_plan["identity_digest"],
        "preserved_evaluation_plan_digest": digest(preserved_plan),
        "evidence_protocol_path": EVIDENCE_SCOPE_PATH,
        "evidence_protocol_digest": EVIDENCE_SCOPE_DIGEST,
        "price_rows_outside_exact_window": False,
        "preserved_2021_evaluation_market_data_read": False,
        "2022_2024_read": False,
        "2025_read": False,
    }
    summary = {
        "schema_version": SUMMARY_SCHEMA,
        "work_item": "P3-STAGE-014-C1",
        "phase": "ADMISSION_QUALIFIED_ATOMIC_FIRST_DEVELOPMENT_PREPARE",
        "code_revision": code_revision,
        "preregistration_digest": PREREG_DIGEST,
        "execution_digest": EXECUTION_DIGEST,
        "resource_plan_admission_qualified": True,
        "resource_plan_size": 256,
        "development_identity_capacity": 256,
        "development_selected_case_count": selection["selected_case_count"],
        "development_shortfall_count": selection["shortfall_count"],
        "development_selection_digest": digest(selection),
        "threshold_basis_digest": digest(basis),
        "candidate_grid_digest": digest(candidate_payload),
        "envelope_measurements_digest": digest(envelopes),
        "case_strata_digest": digest(case_strata),
        "instability_candidate_count": len(instability_candidates),
        "order_profile_count": len(order_candidates),
        "relocation_candidate_count": 1,
        "joint_pair_search_count": 0,
        "selected_cell_counts": dict(sorted(cell_counts.items())),
        "fallback_used_count": selection["fallback_used_count"],
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
    run_id = "stage-atomic-proposition-admission-qualified-d1-" + datetime.now(
        timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")
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
        "case-strata.json": canonical(case_strata),
        "summary.json": canonical(summary),
    }
    if len(payloads["evidence.json"]) > MAX_BYTES:
        raise AtomicPropositionAdmissionQualifiedError("development_evidence_size_budget")
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
            "price_rows_outside_exact_window": False,
            "preserved_2021_evaluation_market_data_read": False,
            "parameter_search": False,
            "atomic_freeze": False,
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
    manifest = _object(root / "manifest.json", "p014_semantic_manifest")
    manifest_raw = canonical(manifest)
    ready = (root / "READY").read_text(encoding="ascii").strip()
    if manifest.get("schema_version") != ARTIFACT_SCHEMA or ready != _sha(manifest_raw):
        raise AtomicPropositionAdmissionQualifiedError("p014_manifest_or_ready")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise AtomicPropositionAdmissionQualifiedError("p014_code_revision")
    if (
        manifest.get("preregistration_digest") != PREREG_DIGEST
        or manifest.get("execution_digest") != EXECUTION_DIGEST
    ):
        raise AtomicPropositionAdmissionQualifiedError("p014_contract_binding")
    expected = {
        "source-binding.json",
        "development-selection.json",
        "preserved-evaluation-plan.json",
        "threshold-basis.json",
        "candidate-grid.json",
        "evidence.json",
        "path-facts.json",
        "envelope-measurements.json",
        "case-strata.json",
        "summary.json",
    }
    files = manifest.get("files")
    if not isinstance(files, Mapping) or set(files) != expected:
        raise AtomicPropositionAdmissionQualifiedError("p014_inventory")
    payloads: dict[str, Any] = {}
    for name in sorted(expected):
        raw = (root / name).read_bytes()
        if _sha(raw) != files[name]:
            raise AtomicPropositionAdmissionQualifiedError("p014_file_digest:" + name)
        payloads[name] = json.loads(raw)
    validate_evidence(payloads["evidence.json"], expected_protocol_digest=EVIDENCE_SCOPE_DIGEST)
    summary = payloads["summary.json"]
    source = payloads["source-binding.json"]
    selection = payloads["development-selection.json"]
    plan = payloads["preserved-evaluation-plan.json"]
    grid = payloads["candidate-grid.json"]
    basis = payloads["threshold-basis.json"]
    envelopes = payloads["envelope-measurements.json"]
    strata = payloads["case-strata.json"]
    if (
        summary.get("resource_plan_admission_qualified") is not True
        or summary.get("resource_plan_size") != 256
        or summary.get("development_selected_case_count") != 192
        or summary.get("development_shortfall_count") != 0
        or len(summary.get("selected_cell_counts", {})) != 8
        or any(value != 24 for value in summary.get("selected_cell_counts", {}).values())
        or source.get("input_admission_qualified_before_semantic_read") is not True
        or source.get("price_rows_outside_exact_window") is not False
        or source.get("development_preserved_evaluation_overlap") != 0
        or source.get("preserved_2021_evaluation_market_data_read") is not False
        or source.get("2022_2024_read") is not False
        or source.get("2025_read") is not False
        or plan.get("data_read") is not False
        or basis.get("labels_used") is not False
        or basis.get("reference_opinions_used") is not False
        or grid.get("instability_candidate_count") != 9
        or grid.get("order_profile_count") != 3
        or grid.get("relocation_candidate_count") != 1
        or grid.get("joint_pair_search_count") != 0
        or grid.get("parameter_search_performed") is not False
        or grid.get("grid_expanded_after_result") is not False
        or envelopes.get("review_transport_visible") is not False
        or selection.get("selection_uses_morphology_values") is not False
        or selection.get("selection_uses_labels_or_reference") is not False
        or selection.get("selection_uses_candidate_output") is not False
        or selection.get("case_t_is_exact_window_end") is not True
        or len(strata) != 192
    ):
        raise AtomicPropositionAdmissionQualifiedError("p014_authority_claim")
    if (
        digest(selection) != summary.get("development_selection_digest")
        or digest(plan) != summary.get("preserved_evaluation_plan_digest")
        or digest(basis) != summary.get("threshold_basis_digest")
        or digest(grid) != summary.get("candidate_grid_digest")
        or digest(envelopes) != summary.get("envelope_measurements_digest")
        or digest(strata) != summary.get("case_strata_digest")
        or digest(payloads["evidence.json"]) != summary.get("development_evidence_digest")
        or digest(payloads["path-facts.json"]) != summary.get("path_facts_digest")
    ):
        raise AtomicPropositionAdmissionQualifiedError("p014_summary_payload_binding")
    return {
        "verified": True,
        "run_id": manifest["run_id"],
        "manifest_sha256": ready,
        "file_count": len(expected),
        "resource_plan_size": 256,
        "development_identity_capacity": 256,
        "development_selected_case_count": 192,
        "development_shortfall_count": 0,
        "development_selection_digest": summary["development_selection_digest"],
        "development_evidence_digest": summary["development_evidence_digest"],
        "threshold_basis_digest": summary["threshold_basis_digest"],
        "candidate_grid_digest": summary["candidate_grid_digest"],
        "envelope_measurements_digest": summary["envelope_measurements_digest"],
        "case_strata_digest": summary["case_strata_digest"],
        "instability_candidate_count": 9,
        "order_profile_count": 3,
        "relocation_candidate_count": 1,
        "joint_pair_search_count": 0,
        "path_facts_digest": summary["path_facts_digest"],
        "preserved_evaluation_identity_count": summary["preserved_evaluation_identity_count"],
        "preserved_evaluation_identity_digest": summary["preserved_evaluation_identity_digest"],
        "preserved_evaluation_plan_digest": summary["preserved_evaluation_plan_digest"],
        "preserved_evaluation_data_read": False,
        "selected_cell_counts": summary["selected_cell_counts"],
        "fallback_used_count": summary["fallback_used_count"],
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
    print(json.dumps(result, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

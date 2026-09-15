"""P3-STAGE-018 artifact-only instability channel decomposition.

This diagnostic reuses the exact P017 cell-local profile grid and immutable P015 parent instability
references. It measures expanding-disorder and broad-reversal-disorder channels separately and in
combination. It does not select candidates, retune thresholds, create new semantic labels, read DWD,
or open preserved evaluation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.stage_learning.combined_atomic_scoring import (
    _artifact_payloads,
    _combined_references,
    _ledger,
    _macro_atomic_metrics,
    _plain,
    _reference_counts,
)
from quanthus.stage_learning.directional_boundary_development import case_features
from quanthus.stage_learning.positive_instability_quantitative_development import (
    candidate_grid as instability_candidate_grid,
)
from quanthus.stage_learning.positive_instability_quantitative_projection import (
    ProjectionMeasurements,
    project_positive_instability,
)
from quanthus.stage_learning.stratum_normalized_instability import (
    _bound_repo_json,
    _merge_cases,
)
from quanthus.structure_v2_inputs import canonical, digest

DEFAULT_CONFIG = "config/stage-learning/stage-instability-channel-decomposition.v1.json"
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")


class InstabilityChannelDecompositionError(ValueError):
    """The frozen P3-STAGE-018 diagnostic contract was violated."""


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _file_sha(path: Path) -> str:
    try:
        return _sha(path.read_bytes())
    except OSError as exc:
        raise InstabilityChannelDecompositionError(f"cannot_hash:{path}") from exc


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InstabilityChannelDecompositionError(f"cannot_read:{label}") from exc
    if not isinstance(value, dict):
        raise InstabilityChannelDecompositionError(f"object_required:{label}")
    return value


def load_config(path: Path | None = None) -> dict[str, Any]:
    root = find_project_root()
    path = path or root / DEFAULT_CONFIG
    value = _object(path, "p018_config")
    if (
        value.get("schema_version") != "quanthus.stage-instability-channel-decomposition.v1"
        or value.get("work_item") != "P3-STAGE-018-A"
        or value.get("state")
        != "PREREGISTERED_ARTIFACT_ONLY_CHANNEL_DECOMPOSITION_BEFORE_EXECUTION"
    ):
        raise InstabilityChannelDecompositionError("config_identity")
    surface = value.get("diagnostic_surface")
    metrics = value.get("metrics")
    runtime = value.get("runtime")
    authority = value.get("authority")
    if not all(isinstance(item, Mapping) for item in (surface, metrics, runtime, authority)):
        raise InstabilityChannelDecompositionError("config_sections")
    if (
        surface.get("parent_proposition") != "structural_instability_20"
        or surface.get("channels")
        != ["expanding_disorder_20", "broad_reversal_disorder_20"]
        or surface.get("reference_case_count") != 252
        or surface.get("cell_count") != 8
        or surface.get("profile_count") != 9
        or surface.get("new_profile_coordinates") is not False
        or surface.get("new_thresholds") is not False
        or surface.get("threshold_recompute") is not False
        or surface.get("reference_rewrite") is not False
        or surface.get("new_semantic_labels") is not False
        or metrics.get("candidate_selection") is not False
        or metrics.get("eligibility_gate") is not False
        or metrics.get("successor_qualification") is not False
        or runtime.get("maximum_jobs") != 1
        or runtime.get("automatic_retries") != 0
        or runtime.get("worker_wall_seconds") != 900
    ):
        raise InstabilityChannelDecompositionError("config_budget")
    required_true = ("artifact_only_diagnostic", "channel_decomposition")
    required_false = (
        "candidate_scoring_for_selection",
        "candidate_reselection",
        "threshold_change",
        "new_reference_generation",
        "reference_rewrite",
        "dwd_market_read",
        "fixed_relocation_scoring",
        "order_scoring",
        "projection_compatibility",
        "preserved_2021_read",
        "2022_2024_read",
        "2025_read",
        "daily_or_strategy",
        "trading",
    )
    if any(authority.get(key) is not True for key in required_true) or any(
        authority.get(key) is not False for key in required_false
    ):
        raise InstabilityChannelDecompositionError("authority_ceiling")
    return value


def _artifact_payloads_local(
    *, training_root: Path, binding: Mapping[str, Any], required: Sequence[str], label: str
) -> dict[str, Any]:
    try:
        _, _, payloads = _artifact_payloads(
            training_root=training_root,
            binding=binding,
            required=required,
            label=label,
        )
    except Exception as exc:
        raise InstabilityChannelDecompositionError(f"artifact_binding:{label}:{exc}") from exc
    return payloads


def _measurements(case: Mapping[str, Any]) -> ProjectionMeasurements:
    features = case_features(case)
    return ProjectionMeasurements(
        range_width20=features["range_width20"],
        width_change20=features["width_change20"],
        efficiency20_abs=features["efficiency20_abs"],
        range_traversal20=features["range_traversal20"],
        counter_direction_excursion_share20=features[
            "counter_direction_excursion_share20"
        ],
    )


def _candidate_map(
    *, bases: Mapping[str, Any], raw_profile: Mapping[str, Any]
) -> dict[str, Any]:
    expansion_id = str(raw_profile.get("expansion_profile_id") or "")
    broad_id = str(raw_profile.get("broad_profile_id") or "")
    raw_cells = raw_profile.get("cell_candidates")
    if not isinstance(raw_cells, Mapping):
        raise InstabilityChannelDecompositionError("profile_cell_candidates")
    output: dict[str, Any] = {}
    for cell, basis in sorted(bases.items()):
        if not isinstance(basis, Mapping):
            raise InstabilityChannelDecompositionError("basis_cell_shape")
        candidates = instability_candidate_grid(basis)
        matched = [
            item
            for item in candidates
            if item.expansion_profile_id == expansion_id
            and item.broad_profile_id == broad_id
        ]
        if len(matched) != 1:
            raise InstabilityChannelDecompositionError("profile_candidate_binding")
        candidate = matched[0]
        if candidate.to_dict() != raw_cells.get(cell):
            raise InstabilityChannelDecompositionError("profile_threshold_binding")
        output[str(cell)] = candidate
    return output


def _attribution_counts(
    *,
    case_ids: Sequence[str],
    references: Mapping[str, Mapping[str, Any]],
    expansion: Mapping[str, str],
    broad: Mapping[str, str],
) -> dict[str, int]:
    counts = Counter()
    for case_id in case_ids:
        exp_true = expansion[case_id] == "TRUE"
        broad_true = broad[case_id] == "TRUE"
        if exp_true and broad_true:
            category = "overlap"
        elif exp_true:
            category = "expansion_only"
        elif broad_true:
            category = "broad_only"
        else:
            category = "neither"
        counts[f"{category}_count"] += 1
        reference = str(references[case_id].get("structural_instability_20") or "")
        if reference == "TRUE":
            counts[f"{category}_true_positive"] += 1
        elif reference == "FALSE":
            counts[f"{category}_false_positive"] += 1
    return {
        "expansion_true_count": sum(expansion[case_id] == "TRUE" for case_id in case_ids),
        "broad_true_count": sum(broad[case_id] == "TRUE" for case_id in case_ids),
        "overlap_true_count": counts["overlap_count"],
        "expansion_only_count": counts["expansion_only_count"],
        "broad_only_count": counts["broad_only_count"],
        "neither_count": counts["neither_count"],
        "expansion_only_true_positive": counts["expansion_only_true_positive"],
        "expansion_only_false_positive": counts["expansion_only_false_positive"],
        "broad_only_true_positive": counts["broad_only_true_positive"],
        "broad_only_false_positive": counts["broad_only_false_positive"],
        "overlap_true_positive": counts["overlap_true_positive"],
        "overlap_false_positive": counts["overlap_false_positive"],
    }


def decompose_profiles(
    *,
    cases: Mapping[str, Mapping[str, Any]],
    strata: Mapping[str, Mapping[str, str]],
    references: Mapping[str, Mapping[str, Any]],
    bases: Mapping[str, Any],
    profile_grid: Mapping[str, Any],
) -> list[dict[str, Any]]:
    case_ids = sorted(cases)
    if set(case_ids) != set(strata) or set(case_ids) != set(references):
        raise InstabilityChannelDecompositionError("case_closure")
    profiles = profile_grid.get("profiles")
    if not isinstance(profiles, list) or len(profiles) != 9:
        raise InstabilityChannelDecompositionError("profile_count")
    output = []
    for raw_profile in profiles:
        if not isinstance(raw_profile, Mapping):
            raise InstabilityChannelDecompositionError("profile_shape")
        candidate_by_cell = _candidate_map(bases=bases, raw_profile=raw_profile)
        expansion: dict[str, str] = {}
        broad: dict[str, str] = {}
        parent_or: dict[str, str] = {}
        for case_id in case_ids:
            cell = str(strata[case_id]["cell"])
            assessment = project_positive_instability(
                _measurements(cases[case_id]), candidate_by_cell[cell].thresholds
            )
            expansion[case_id] = assessment.expanding_disorder_20
            broad[case_id] = assessment.broad_reversal_disorder_20
            parent_or[case_id] = assessment.status
        output.append(
            {
                "profile_id": str(raw_profile.get("profile_id") or ""),
                "expansion_profile_id": str(raw_profile.get("expansion_profile_id") or ""),
                "broad_profile_id": str(raw_profile.get("broad_profile_id") or ""),
                "expanding_disorder_20": _macro_atomic_metrics(
                    case_ids=case_ids,
                    references=references,
                    predictions=expansion,
                    proposition="structural_instability_20",
                    strata=strata,
                ),
                "broad_reversal_disorder_20": _macro_atomic_metrics(
                    case_ids=case_ids,
                    references=references,
                    predictions=broad,
                    proposition="structural_instability_20",
                    strata=strata,
                ),
                "parent_or": _macro_atomic_metrics(
                    case_ids=case_ids,
                    references=references,
                    predictions=parent_or,
                    proposition="structural_instability_20",
                    strata=strata,
                ),
                "attribution": _attribution_counts(
                    case_ids=case_ids,
                    references=references,
                    expansion=expansion,
                    broad=broad,
                ),
            }
        )
    return output


def run_diagnostic(
    *, config_path: Path, training_root: Path, output_root: Path, code_revision: str
) -> dict[str, Any]:
    if not _REVISION.fullmatch(code_revision):
        raise InstabilityChannelDecompositionError("exact_code_revision_required")
    root = find_project_root()
    config = load_config(config_path)
    p017_config = _bound_repo_json(
        root, config["bindings"]["p017_config"], label="p017_config"
    )
    p017_result = _bound_repo_json(
        root, config["bindings"]["p017_result"], label="p017_result"
    )
    if (
        p017_result.get("state") != "CLOSED_STRATUM_NORMALIZED_INSTABILITY_FAIL"
        or p017_result.get("successor", {}).get("work_item") != "P3-STAGE-018"
    ):
        raise InstabilityChannelDecompositionError("p017_result_disposition")

    training_root = training_root.expanduser().resolve()
    p017_payloads = _artifact_payloads_local(
        training_root=training_root,
        binding=config["bindings"]["p017_recovery_artifact"],
        required=("stratum-bases.json", "profile-grid.json", "summary.json"),
        label="p017_recovery",
    )
    basis_payload = p017_payloads["stratum-bases.json"]
    profile_grid = p017_payloads["profile-grid.json"]
    summary = p017_payloads["summary.json"]
    bases = basis_payload.get("cells")
    if (
        not isinstance(bases, Mapping)
        or basis_payload.get("basis_digest")
        != config["bindings"]["p017_recovery_artifact"]["basis_digest"]
        or profile_grid.get("profile_grid_digest")
        != config["bindings"]["p017_recovery_artifact"]["profile_grid_digest"]
        or summary.get("eligible_candidate_count") != 0
        or summary.get("candidate_count") != 9
    ):
        raise InstabilityChannelDecompositionError("p017_artifact_binding")

    p014_payloads = _artifact_payloads_local(
        training_root=training_root,
        binding=p017_config["bindings"]["p014_semantic_artifact"],
        required=("evidence.json", "case-strata.json"),
        label="p014_semantic",
    )
    p015_payloads = _artifact_payloads_local(
        training_root=training_root,
        binding=p017_config["bindings"]["p015_additional_review_artifact"],
        required=("evidence.json", "case-strata.json"),
        label="p015_additional",
    )
    cases, strata = _merge_cases(p014_payloads, p015_payloads)
    p014_ledger = _ledger(
        training_root,
        p017_config["bindings"]["p014_review_ledger"],
        expected_count=192,
        label="p014_review_ledger",
    )
    p015_ledger = _ledger(
        training_root,
        p017_config["bindings"]["p015_review_ledger"],
        expected_count=60,
        label="p015_review_ledger",
    )
    references = _combined_references(p014_ledger, p015_ledger)
    if set(references) != set(cases) or len(cases) != 252:
        raise InstabilityChannelDecompositionError("reference_case_closure")

    profiles = decompose_profiles(
        cases=cases,
        strata=strata,
        references=references,
        bases=bases,
        profile_grid=profile_grid,
    )
    diagnostic = {
        "schema_version": config["output"]["diagnostic_schema"],
        "work_item": "P3-STAGE-018-B",
        "disposition": "PASS_CHANNEL_DECOMPOSITION_DIAGNOSTIC_COMPLETE",
        "reference_case_count": 252,
        "reference_counts": _reference_counts(references),
        "cell_count": 8,
        "profile_count": 9,
        "profiles": _plain(profiles),
        "candidate_selection_performed": False,
        "eligibility_gate_applied": False,
        "new_semantic_labels_generated": False,
        "threshold_change": False,
        "dwd_market_read": False,
        "preserved_2021_read": False,
        "2022_2024_read": False,
        "2025_read": False,
    }
    result_summary = {
        "schema_version": config["output"]["summary_schema"],
        "work_item": "P3-STAGE-018-B",
        "phase": "INSTABILITY_CHANNEL_DECOMPOSITION_DIAGNOSTIC",
        "code_revision": code_revision,
        "config_digest": digest(config),
        "disposition": diagnostic["disposition"],
        "reference_case_count": 252,
        "cell_count": 8,
        "profile_count": 9,
        "candidate_selection_performed": False,
        "new_semantic_labels_generated": False,
        "dwd_market_read": False,
        "preserved_2021_read": False,
        "2022_2024_read": False,
        "2025_read": False,
    }
    run_id = "stage-instability-channel-decomposition-" + datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    run_root = output_root.expanduser().resolve() / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    payloads = {
        "channel-decomposition.json": canonical(diagnostic),
        "reference-binding.json": canonical(
            {
                "p014_review_ledger_sha256": p017_config["bindings"]["p014_review_ledger"][
                    "sha256"
                ],
                "p015_review_ledger_sha256": p017_config["bindings"]["p015_review_ledger"][
                    "sha256"
                ],
                "combined_case_count": 252,
                "reference_rewrite": False,
            }
        ),
        "summary.json": canonical(result_summary),
    }
    for name, raw in payloads.items():
        (run_root / name).write_bytes(raw)
    manifest = {
        "schema_version": config["output"]["artifact_schema"],
        "run_id": run_id,
        "code_revision": code_revision,
        "config_digest": digest(config),
        "files": {name: _sha(raw) for name, raw in payloads.items()},
        "authority": {
            "candidate_selection": False,
            "threshold_change": False,
            "new_semantic_labels": False,
            "dwd_market_read": False,
            "preserved_2021_read": False,
            "2022_2024_read": False,
            "2025_read": False,
        },
    }
    manifest_raw = canonical(manifest)
    (run_root / "manifest.json").write_bytes(manifest_raw)
    manifest_sha = _sha(manifest_raw)
    (run_root / "READY").write_text(manifest_sha + "\n", encoding="ascii")
    return {
        **result_summary,
        "run_id": run_id,
        "run_root": str(run_root),
        "manifest_sha256": manifest_sha,
    }


def verify_artifact(path: Path, *, code_revision: str | None = None) -> dict[str, Any]:
    path = path.expanduser().resolve()
    manifest_raw = (path / "manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    manifest_sha = _sha(manifest_raw)
    ready = (path / "READY").read_text(encoding="ascii").strip()
    if ready != manifest_sha:
        raise InstabilityChannelDecompositionError("artifact_ready_binding")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise InstabilityChannelDecompositionError("artifact_code_revision")
    files = manifest.get("files")
    if not isinstance(files, Mapping):
        raise InstabilityChannelDecompositionError("artifact_files")
    for name in ("channel-decomposition.json", "reference-binding.json", "summary.json"):
        raw = (path / name).read_bytes()
        if files.get(name) != _sha(raw):
            raise InstabilityChannelDecompositionError(f"artifact_file_binding:{name}")
    summary = _object(path / "summary.json", "summary")
    diagnostic = _object(path / "channel-decomposition.json", "diagnostic")
    if (
        summary.get("disposition") != "PASS_CHANNEL_DECOMPOSITION_DIAGNOSTIC_COMPLETE"
        or diagnostic.get("profile_count") != 9
        or diagnostic.get("reference_case_count") != 252
        or diagnostic.get("candidate_selection_performed") is not False
        or diagnostic.get("new_semantic_labels_generated") is not False
        or diagnostic.get("dwd_market_read") is not False
        or diagnostic.get("preserved_2021_read") is not False
        or diagnostic.get("2025_read") is not False
    ):
        raise InstabilityChannelDecompositionError("artifact_summary")
    return {
        "verified": True,
        "run_id": manifest.get("run_id"),
        "manifest_sha256": manifest_sha,
        "file_count": len(files),
        "disposition": summary.get("disposition"),
        "reference_case_count": 252,
        "cell_count": 8,
        "profile_count": 9,
        "candidate_selection_performed": False,
        "new_semantic_labels_generated": False,
        "dwd_market_read": False,
        "preserved_2021_read": False,
        "2022_2024_read": False,
        "2025_read": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--config", type=Path, default=Path(DEFAULT_CONFIG))
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
        result = run_diagnostic(
            config_path=args.config,
            training_root=args.training_root,
            output_root=args.output_root,
            code_revision=args.code_revision,
        )
    else:
        result = verify_artifact(args.artifact, code_revision=args.code_revision)
    print(json.dumps(_plain(result), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

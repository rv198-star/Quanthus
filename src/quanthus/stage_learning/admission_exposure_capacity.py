"""P3-STAGE-021-B metadata-only capacity recomputation after exposure-taxonomy freeze.

The runner compares two exact-window consumption rules over the same P013 Era x Board metadata
reservoir. It never reads market bars, Stage1/12 semantics, thresholds, reviewer opinions or
preserved partitions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.stage_learning.development_resource_admission_screen import (
    verify_artifact as verify_admission_artifact,
)
from quanthus.stage_learning.development_resource_plan import _reconstruct_tokens
from quanthus.stage_learning.development_resource_plan import (
    verify_artifact as verify_p013_plan_artifact,
)
from quanthus.stage_learning.development_resource_qualified_plan import (
    verify_artifact as verify_qualified_plan_artifact,
)
from quanthus.structure_v2_inputs import canonical, digest

DEFAULT_CONFIG = "config/stage-learning/stage-admission-exposure-capacity.v1.json"
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")


class AdmissionExposureCapacityError(ValueError):
    """The frozen P021 metadata-capacity contract was violated."""


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _file_sha(path: Path) -> str:
    try:
        return _sha(path.read_bytes())
    except OSError as exc:
        raise AdmissionExposureCapacityError(f"cannot_hash:{path}") from exc


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdmissionExposureCapacityError(f"cannot_read:{label}") from exc
    if not isinstance(value, dict):
        raise AdmissionExposureCapacityError(f"object_required:{label}")
    return value


def _bound_json(root: Path, binding: Mapping[str, Any], label: str) -> dict[str, Any]:
    path = root / str(binding.get("path") or "")
    expected = str(binding.get("sha256") or "")
    if not _SHA.fullmatch(expected) or _file_sha(path) != expected:
        raise AdmissionExposureCapacityError(f"repo_binding:{label}")
    return _object(path, label)


def _artifact_root(training_root: Path, binding: Mapping[str, Any]) -> Path:
    return (training_root / str(binding.get("artifact_relative_path") or "")).resolve()


def _rank(seed: str, cell: str, identity: str) -> str:
    return hashlib.sha256(f"{seed}|{cell}|{identity}".encode()).hexdigest()


def load_config(path: Path | None = None) -> dict[str, Any]:
    root = find_project_root()
    value = _object(path or root / DEFAULT_CONFIG, "p021_capacity_config")
    if (
        value.get("schema_version") != "quanthus.stage-admission-exposure-capacity.v1"
        or value.get("work_item") != "P3-STAGE-021-B"
        or value.get("state") != "PREREGISTERED_METADATA_ONLY_CAPACITY_AFTER_TAXONOMY_FREEZE"
    ):
        raise AdmissionExposureCapacityError("config_identity")
    comparison = value.get("comparison")
    capacity = value.get("capacity_contract")
    runtime = value.get("runtime")
    authority = value.get("authority")
    if not all(isinstance(item, Mapping) for item in (comparison, capacity, runtime, authority)):
        raise AdmissionExposureCapacityError("config_sections")
    if (
        comparison.get("admission_only_reusable_class")
        != "L2A_TECHNICAL_INPUT_ADMISSION_READ"
        or comparison.get("semantic_consumed_class") != "L2B_SEMANTIC_DEVELOPMENT_READ"
        or comparison.get("preserved_identity_global_exclusion") is not True
        or capacity.get("required_unique_identities_per_cell") != 32
        or capacity.get("global_identity_unique_reserve_rows") != 256
        or capacity.get("no_market_prevalence_claim") is not True
        or capacity.get("no_result_aware_selection") is not True
        or runtime.get("maximum_jobs") != 1
        or runtime.get("automatic_retries") != 0
    ):
        raise AdmissionExposureCapacityError("config_budget")
    for key in (
        "market_bar_read",
        "admission_market_read",
        "stage1_12_semantic_read",
        "threshold_or_candidate_read",
        "reference_opinion_read",
        "p020_reopen",
        "preserved_2021_read",
        "2022_2024_read",
        "2025_read",
        "daily_or_strategy",
        "trading",
    ):
        if authority.get(key) is not False:
            raise AdmissionExposureCapacityError(f"authority:{key}")
    if authority.get("listing_calendar_metadata_read") is not True:
        raise AdmissionExposureCapacityError("metadata_authority")
    if authority.get("prior_artifact_metadata_read") is not True:
        raise AdmissionExposureCapacityError("artifact_authority")
    return value


def _artifact_tokens(
    *, root: Path, training_root: Path, config: Mapping[str, Any]
) -> tuple[set[tuple[str, str]], set[tuple[str, str]], set[tuple[str, str]], dict[str, Any]]:
    p013_result = _bound_json(root, config["bindings"]["p013_exact_plan_result"], "p013_result")
    p014_admission_result = _bound_json(
        root, config["bindings"]["p014_admission_result"], "p014_admission_result"
    )
    p014_plan_result = _bound_json(
        root, config["bindings"]["p014_qualified_plan_result"], "p014_plan_result"
    )

    p013_binding = config["bindings"]["p013_exact_plan_artifact"]
    p013_root = _artifact_root(training_root, p013_binding)
    p013_verified = verify_p013_plan_artifact(
        p013_root, code_revision=str(p013_binding["code_revision"])
    )
    if (
        p013_verified.get("manifest_sha256") != p013_binding.get("manifest_sha256")
        or p013_verified.get("plan_digest") != p013_binding.get("plan_digest")
        or p013_result.get("artifact", {}).get("manifest_sha256")
        != p013_binding.get("manifest_sha256")
    ):
        raise AdmissionExposureCapacityError("p013_artifact_binding")
    p013_plan = _object(p013_root / "exact-plan.json", "p013_exact_plan")
    p013_rows = p013_plan.get("rows")
    if not isinstance(p013_rows, list) or len(p013_rows) != 256:
        raise AdmissionExposureCapacityError("p013_plan_rows")
    p013_tokens = {
        (str(row["security_id"]), str(row["window_id"]))
        for row in p013_rows
        if isinstance(row, Mapping)
    }
    if len(p013_tokens) != 256:
        raise AdmissionExposureCapacityError("p013_token_count")

    admission_binding = config["bindings"]["p014_admission_artifact"]
    admission_root = _artifact_root(training_root, admission_binding)
    admission_verified = verify_admission_artifact(
        admission_root, code_revision=str(admission_binding["code_revision"])
    )
    if (
        admission_verified.get("manifest_sha256") != admission_binding.get("manifest_sha256")
        or p014_admission_result.get("artifact", {}).get("manifest_sha256")
        != admission_binding.get("manifest_sha256")
    ):
        raise AdmissionExposureCapacityError("p014_admission_artifact_binding")
    admission_ledger = _object(
        admission_root / "candidate-ledger.json", "p014_admission_ledger"
    )
    admission_rows = admission_ledger.get("rows")
    if not isinstance(admission_rows, list) or not admission_rows:
        raise AdmissionExposureCapacityError("p014_admission_rows")
    admission_tokens = {
        (str(row["security_id"]), str(row["window_id"]))
        for row in admission_rows
        if isinstance(row, Mapping)
    }

    plan_binding = config["bindings"]["p014_qualified_plan_artifact"]
    plan_root = _artifact_root(training_root, plan_binding)
    plan_verified = verify_qualified_plan_artifact(
        plan_root, code_revision=str(plan_binding["code_revision"])
    )
    if (
        plan_verified.get("manifest_sha256") != plan_binding.get("manifest_sha256")
        or plan_verified.get("plan_digest") != plan_binding.get("plan_digest")
        or p014_plan_result.get("artifact", {}).get("manifest_sha256")
        != plan_binding.get("manifest_sha256")
    ):
        raise AdmissionExposureCapacityError("p014_plan_artifact_binding")
    plan = _object(plan_root / "qualified-plan.json", "p014_qualified_plan")
    plan_rows = plan.get("rows")
    if not isinstance(plan_rows, list) or len(plan_rows) != 256:
        raise AdmissionExposureCapacityError("p014_plan_rows")
    semantic_tokens = {
        (str(row["security_id"]), str(row["window_id"]))
        for row in plan_rows
        if isinstance(row, Mapping)
    }
    if len(semantic_tokens) != 256 or not semantic_tokens <= admission_tokens:
        raise AdmissionExposureCapacityError("p014_semantic_subset")
    admission_only_tokens = admission_tokens - semantic_tokens
    if p013_tokens & admission_tokens:
        raise AdmissionExposureCapacityError("p013_p014_overlap")
    receipt = {
        "p013_manifest_sha256": p013_verified["manifest_sha256"],
        "p013_exact_window_count": len(p013_tokens),
        "p014_admission_manifest_sha256": admission_verified["manifest_sha256"],
        "p014_admission_window_count": len(admission_tokens),
        "p014_qualified_plan_manifest_sha256": plan_verified["manifest_sha256"],
        "p014_semantic_consumed_window_count": len(semantic_tokens),
        "p014_admission_only_reusable_window_count": len(admission_only_tokens),
        "p014_admission_only_reusable_digest": digest(sorted(admission_only_tokens)),
    }
    return p013_tokens, admission_tokens, semantic_tokens, receipt


def _cell_capacity(
    tokens: Sequence[Mapping[str, Any]],
    *,
    consumed: set[tuple[str, str]],
    cell_order: Sequence[str],
) -> dict[str, dict[str, int]]:
    by_cell: dict[str, list[Mapping[str, Any]]] = {str(cell): [] for cell in cell_order}
    for row in tokens:
        key = (str(row["security_id"]), str(row["window_id"]))
        if key in consumed:
            continue
        cell = f"{row['era']}/{row['board_family']}"
        if cell in by_cell:
            by_cell[cell].append(row)
    result: dict[str, dict[str, int]] = {}
    for cell in cell_order:
        rows = by_cell[str(cell)]
        result[str(cell)] = {
            "identity_count": len({str(row["security_id"]) for row in rows}),
            "window_count": len(rows),
        }
    return result


def _global_unique_feasibility(
    tokens: Sequence[Mapping[str, Any]],
    *,
    consumed: set[tuple[str, str]],
    cell_order: Sequence[str],
    required: int,
    seed: str,
) -> dict[str, Any]:
    by_cell: dict[str, set[str]] = {str(cell): set() for cell in cell_order}
    for row in tokens:
        key = (str(row["security_id"]), str(row["window_id"]))
        if key in consumed:
            continue
        cell = f"{row['era']}/{row['board_family']}"
        if cell in by_cell:
            by_cell[cell].add(str(row["security_id"]))
    used: set[str] = set()
    cell_receipts: list[dict[str, Any]] = []
    all_pass = True
    chosen_all: list[str] = []
    for cell in cell_order:
        candidates = sorted(
            (identity for identity in by_cell[str(cell)] if identity not in used),
            key=lambda identity: (_rank(seed, str(cell), identity), identity),
        )
        chosen = candidates[:required]
        sufficient = len(chosen) == required
        all_pass &= sufficient
        used.update(chosen)
        chosen_all.extend(f"{cell}|{identity}" for identity in chosen)
        cell_receipts.append(
            {
                "cell": str(cell),
                "available_before_global_exclusion": len(by_cell[str(cell)]),
                "available_after_prior_cell_identity_exclusion": len(candidates),
                "selected_count": len(chosen),
                "required_count": required,
                "sufficient": sufficient,
                "selected_identity_digest": digest(chosen),
            }
        )
    return {
        "feasible": all_pass,
        "selected_row_count": len(chosen_all),
        "global_unique_identity_count": len(used),
        "selection_digest": digest(chosen_all),
        "cell_receipts": cell_receipts,
    }


def run_capacity(
    *, config_path: Path, training_root: Path, output_root: Path, code_revision: str
) -> dict[str, Any]:
    if not _REVISION.fullmatch(code_revision):
        raise AdmissionExposureCapacityError("exact_code_revision_required")
    root = find_project_root()
    config = load_config(config_path)
    taxonomy = _bound_json(root, config["bindings"]["taxonomy"], "taxonomy")
    p020 = _bound_json(root, config["bindings"]["p020_resource_contract"], "p020_resource")
    if (
        taxonomy.get("state")
        != "PREREGISTERED_ADMISSION_ONLY_EXPOSURE_TAXONOMY_BEFORE_CAPACITY_RECOMPUTE"
        or taxonomy.get("capacity_recompute_contract", {}).get("work_item") != "P3-STAGE-021-B"
    ):
        raise AdmissionExposureCapacityError("taxonomy_state")
    if p020.get("work_item") != "P3-STAGE-020-B":
        raise AdmissionExposureCapacityError("p020_binding")

    training_root = training_root.expanduser().resolve()
    p013_tokens, admission_tokens, semantic_tokens, prior_receipt = _artifact_tokens(
        root=root, training_root=training_root, config=config
    )
    tokens, _windows, source_binding, preserved_ids = _reconstruct_tokens(
        root=root, training_root=training_root, config=config
    )
    if {str(row["security_id"]) for row in tokens} & preserved_ids:
        raise AdmissionExposureCapacityError("preserved_identity_in_metadata_tokens")

    old_consumed = p013_tokens | admission_tokens
    repaired_consumed = p013_tokens | semantic_tokens
    admission_only = admission_tokens - semantic_tokens
    cell_order = tuple(str(cell) for cell in config["capacity_contract"]["cell_order"])
    old_capacity = _cell_capacity(tokens, consumed=old_consumed, cell_order=cell_order)
    repaired_capacity = _cell_capacity(tokens, consumed=repaired_consumed, cell_order=cell_order)
    restored_tokens = [
        row
        for row in tokens
        if (str(row["security_id"]), str(row["window_id"])) in admission_only
    ]
    restored_by_cell = _cell_capacity(restored_tokens, consumed=set(), cell_order=cell_order)
    required = int(config["capacity_contract"]["required_unique_identities_per_cell"])
    per_cell_ready = all(
        repaired_capacity[cell]["identity_count"] >= required for cell in cell_order
    )
    global_feasibility = _global_unique_feasibility(
        tokens,
        consumed=repaired_consumed,
        cell_order=cell_order,
        required=required,
        seed=str(config["capacity_contract"]["identity_seed"]),
    )
    comparison_rows = []
    for cell in cell_order:
        comparison_rows.append(
            {
                "cell": cell,
                "old_identity_count": old_capacity[cell]["identity_count"],
                "old_window_count": old_capacity[cell]["window_count"],
                "repaired_identity_count": repaired_capacity[cell]["identity_count"],
                "repaired_window_count": repaired_capacity[cell]["window_count"],
                "restored_identity_count": restored_by_cell[cell]["identity_count"],
                "restored_window_count": restored_by_cell[cell]["window_count"],
                "required_identity_count": required,
                "repaired_cell_capacity_pass": (
                    repaired_capacity[cell]["identity_count"] >= required
                ),
            }
        )
    comparison = {
        "schema_version": config["output"]["comparison_schema"],
        "taxonomy_frozen_before_capacity": True,
        "rows": comparison_rows,
        "cell_count": len(comparison_rows),
        "old_all_cells_capacity_pass": all(
            old_capacity[cell]["identity_count"] >= required for cell in cell_order
        ),
        "repaired_all_cells_capacity_pass": per_cell_ready,
        "global_unique_8x32_metadata_feasibility": global_feasibility,
        "market_bar_read": False,
        "admission_market_read": False,
        "semantic_read": False,
    }
    source = {
        "schema_version": config["output"]["source_binding_schema"],
        **prior_receipt,
        "base_manifest_sha256": source_binding["base_manifest_sha256"],
        "correction_manifest_sha256": source_binding["correction_manifest_sha256"],
        "metadata_token_count": len(tokens),
        "preserved_2021_identity_count": len(preserved_ids),
        "old_consumed_window_count": len(old_consumed),
        "repaired_consumed_window_count": len(repaired_consumed),
        "restored_admission_only_window_count_in_metadata_reservoir": len(restored_tokens),
        "market_bar_read": False,
        "preserved_2021_read": False,
        "2022_2024_read": False,
        "2025_read": False,
    }
    disposition = (
        "PASS_REPAIRED_TAXONOMY_RESTORES_METADATA_CAPACITY"
        if per_cell_ready and global_feasibility["feasible"]
        else "CLOSED_REPAIRED_TAXONOMY_STILL_CAPACITY_SHORT"
    )
    summary = {
        "schema_version": config["output"]["summary_schema"],
        "work_item": "P3-STAGE-021-B",
        "phase": "ADMISSION_EXPOSURE_TAXONOMY_METADATA_CAPACITY",
        "code_revision": code_revision,
        "config_digest": digest(config),
        "disposition": disposition,
        "p014_admission_window_count": len(admission_tokens),
        "p014_semantic_consumed_window_count": len(semantic_tokens),
        "p014_admission_only_reusable_window_count": len(admission_only),
        "restored_admission_only_window_count_in_metadata_reservoir": len(restored_tokens),
        "old_all_cells_capacity_pass": comparison["old_all_cells_capacity_pass"],
        "repaired_all_cells_capacity_pass": per_cell_ready,
        "global_unique_8x32_metadata_feasible": global_feasibility["feasible"],
        "global_unique_selected_row_count": global_feasibility["selected_row_count"],
        "comparison_digest": digest(comparison),
        "source_binding_digest": digest(source),
        "market_bar_read": False,
        "admission_market_read": False,
        "stage1_12_semantic_read": False,
        "threshold_or_candidate_read": False,
        "reference_opinion_read": False,
        "p020_reopen_authorized": False,
        "preserved_2021_read": False,
        "2022_2024_read": False,
        "2025_read": False,
    }

    run_id = "stage-admission-exposure-capacity-" + datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    run_root = output_root.expanduser().resolve() / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    payloads = {
        "capacity-comparison.json": canonical(comparison),
        "source-binding.json": canonical(source),
        "summary.json": canonical(summary),
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
            "market_bar_read": False,
            "admission_market_read": False,
            "stage1_12_semantic_read": False,
            "threshold_or_candidate_read": False,
            "reference_opinion_read": False,
            "p020_reopen": False,
            "preserved_2021_read": False,
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
    manifest_raw = (root / "manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    ready = (root / "READY").read_text(encoding="ascii").strip()
    if _sha(manifest_raw) != ready:
        raise AdmissionExposureCapacityError("artifact_ready_digest")
    if manifest.get("schema_version") != "quanthus.stage-admission-exposure-capacity-artifact.v1":
        raise AdmissionExposureCapacityError("artifact_schema")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise AdmissionExposureCapacityError("artifact_code_revision")
    files = manifest.get("files")
    expected = {"capacity-comparison.json", "source-binding.json", "summary.json"}
    if not isinstance(files, Mapping) or set(files) != expected:
        raise AdmissionExposureCapacityError("artifact_files")
    payloads: dict[str, dict[str, Any]] = {}
    for name in expected:
        raw = (root / name).read_bytes()
        if _sha(raw) != files[name]:
            raise AdmissionExposureCapacityError(f"artifact_file_digest:{name}")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise AdmissionExposureCapacityError(f"artifact_shape:{name}")
        payloads[name] = value
    comparison = payloads["capacity-comparison.json"]
    rows = comparison.get("rows")
    if not isinstance(rows, list) or len(rows) != 8:
        raise AdmissionExposureCapacityError("comparison_rows")
    if comparison.get("taxonomy_frozen_before_capacity") is not True:
        raise AdmissionExposureCapacityError("taxonomy_order")
    for key in ("market_bar_read", "admission_market_read", "semantic_read"):
        if comparison.get(key) is not False:
            raise AdmissionExposureCapacityError(f"comparison_authority:{key}")
    source = payloads["source-binding.json"]
    summary = payloads["summary.json"]
    if digest(comparison) != summary.get("comparison_digest"):
        raise AdmissionExposureCapacityError("summary_comparison_digest")
    if digest(source) != summary.get("source_binding_digest"):
        raise AdmissionExposureCapacityError("summary_source_digest")
    for key in (
        "market_bar_read",
        "admission_market_read",
        "stage1_12_semantic_read",
        "threshold_or_candidate_read",
        "reference_opinion_read",
        "p020_reopen_authorized",
        "preserved_2021_read",
        "2022_2024_read",
        "2025_read",
    ):
        if summary.get(key) is not False:
            raise AdmissionExposureCapacityError(f"summary_authority:{key}")
    return {
        "verified": True,
        "run_id": manifest.get("run_id"),
        "manifest_sha256": ready,
        "file_count": len(files),
        "disposition": summary.get("disposition"),
        "p014_admission_window_count": summary.get("p014_admission_window_count"),
        "p014_semantic_consumed_window_count": summary.get(
            "p014_semantic_consumed_window_count"
        ),
        "p014_admission_only_reusable_window_count": summary.get(
            "p014_admission_only_reusable_window_count"
        ),
        "restored_admission_only_window_count_in_metadata_reservoir": summary.get(
            "restored_admission_only_window_count_in_metadata_reservoir"
        ),
        "old_all_cells_capacity_pass": summary.get("old_all_cells_capacity_pass"),
        "repaired_all_cells_capacity_pass": summary.get("repaired_all_cells_capacity_pass"),
        "global_unique_8x32_metadata_feasible": summary.get(
            "global_unique_8x32_metadata_feasible"
        ),
        "global_unique_selected_row_count": summary.get("global_unique_selected_row_count"),
        "comparison_rows": rows,
        "market_bar_read": False,
        "stage1_12_semantic_read": False,
        "p020_reopen_authorized": False,
        "preserved_2021_read": False,
        "2022_2024_read": False,
        "2025_read": False,
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
        result = run_capacity(
            config_path=args.config,
            training_root=args.training_root,
            output_root=args.output_root,
            code_revision=args.code_revision,
        )
    else:
        result = verify_artifact(args.artifact, code_revision=args.code_revision)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""P3-STAGE-020-B exposure-aware admission and exact resource plan.

This runner reuses the accepted P013/P014 resource algorithms. It excludes exact windows already
consumed by the P013 plan or actually read by the P014 admission screen, then performs only
61-session input-readiness checks and freezes an 8 x 32 / 24+8 Era x Board development panel.
It never reads Stage1/12 references, thresholds, candidate outputs, future outcomes or preserved
partitions.
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
from quanthus.stage_learning.development_resource_admission_screen import (
    _cell_summary,
    _screen_candidates,
    freeze_candidate_surface,
)
from quanthus.stage_learning.development_resource_admission_screen import (
    verify_artifact as verify_admission_artifact,
)
from quanthus.stage_learning.development_resource_plan import _reconstruct_tokens
from quanthus.stage_learning.development_resource_plan import (
    verify_artifact as verify_p013_plan_artifact,
)
from quanthus.stage_learning.development_resource_qualified_plan import (
    DevelopmentResourceQualifiedPlanError,
    select_qualified_plan,
)
from quanthus.structure_v2_inputs import canonical, digest

DEFAULT_CONFIG = "config/stage-learning/stage-downside-lifecycle-resource-plan.v1.json"
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")


class DownsideLifecycleResourcePlanError(ValueError):
    """The frozen P020-B resource/admission contract was violated."""


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _file_sha(path: Path) -> str:
    try:
        return _sha(path.read_bytes())
    except OSError as exc:
        raise DownsideLifecycleResourcePlanError(f"cannot_hash:{path}") from exc


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DownsideLifecycleResourcePlanError(f"cannot_read:{label}") from exc
    if not isinstance(value, dict):
        raise DownsideLifecycleResourcePlanError(f"object_required:{label}")
    return value


def _bound_json(root: Path, binding: Mapping[str, Any], label: str) -> dict[str, Any]:
    path = root / str(binding.get("path") or "")
    expected = str(binding.get("sha256") or "")
    if not _SHA.fullmatch(expected) or _file_sha(path) != expected:
        raise DownsideLifecycleResourcePlanError(f"repo_binding:{label}")
    return _object(path, label)


def load_config(path: Path | None = None) -> dict[str, Any]:
    root = find_project_root()
    path = path or root / DEFAULT_CONFIG
    value = _object(path, "p020_resource_config")
    if (
        value.get("schema_version") != "quanthus.stage-downside-lifecycle-resource-plan.v1"
        or value.get("work_item") != "P3-STAGE-020-B"
        or value.get("state")
        != "PREREGISTERED_RESOURCE_ADMISSION_AND_EXACT_PLAN_BEFORE_SEMANTIC_REVIEW"
    ):
        raise DownsideLifecycleResourcePlanError("config_identity")
    consumed = value.get("consumption_ceiling")
    screen = value.get("candidate_screen")
    admission = value.get("admission_contract")
    selection = value.get("selection")
    runtime = value.get("runtime")
    authority = value.get("authority")
    if not all(
        isinstance(item, Mapping)
        for item in (consumed, screen, admission, selection, runtime, authority)
    ):
        raise DownsideLifecycleResourcePlanError("config_sections")
    if (
        consumed.get("exclude_p013_exact_plan_windows") is not True
        or consumed.get("exclude_all_p014_admission_read_windows") is not True
        or consumed.get("whole_identity_burn_down") is not False
        or consumed.get("same_identity_disjoint_window_reuse_allowed") is not True
        or consumed.get("preserved_2021_identity_global_exclusion") is not True
        or screen.get("maximum_candidate_identities_per_cell") != 256
        or screen.get("maximum_windows_per_identity") != 4
        or admission.get("required_sessions") != 61
        or selection.get("reserve_per_cell") != 32
        or selection.get("primary_per_cell") != 24
        or selection.get("fallback_per_cell") != 8
        or selection.get("total_rows") != 256
        or selection.get("global_identity_unique") is not True
        or selection.get("cross_cell_fill") is not False
        or selection.get("post_result_replacement") is not False
        or runtime.get("maximum_jobs") != 1
        or runtime.get("automatic_retries") != 0
    ):
        raise DownsideLifecycleResourcePlanError("config_budget")
    if tuple(screen.get("cell_allocation_order") or ()) != tuple(selection.get("cell_order") or ()):
        raise DownsideLifecycleResourcePlanError("cell_order_mismatch")
    if authority.get("input_readiness_market_read") is not True:
        raise DownsideLifecycleResourcePlanError("input_read_authority")
    for key in (
        "semantic_market_read",
        "structure_fact_build",
        "reference_opinion",
        "threshold_basis",
        "candidate_output",
        "candidate_scoring",
        "primary_stage_output",
        "preserved_2021_market_read",
        "2022_2024_read",
        "2025_read",
        "daily_or_strategy",
        "trading",
    ):
        if authority.get(key) is not False:
            raise DownsideLifecycleResourcePlanError(f"authority:{key}")
    return value


def _artifact_root(training_root: Path, binding: Mapping[str, Any]) -> Path:
    return (training_root / str(binding.get("artifact_relative_path") or "")).resolve()


def _consumed_windows(
    *, root: Path, training_root: Path, config: Mapping[str, Any]
) -> tuple[set[tuple[str, str]], dict[str, Any]]:
    p013_result = _bound_json(root, config["bindings"]["p013_exact_plan_result"], "p013_result")
    p014_result = _bound_json(root, config["bindings"]["p014_admission_result"], "p014_result")

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
        raise DownsideLifecycleResourcePlanError("p013_artifact_binding")
    p013_plan = _object(p013_root / "exact-plan.json", "p013_exact_plan")
    p013_rows = p013_plan.get("rows")
    if not isinstance(p013_rows, list) or len(p013_rows) != 256:
        raise DownsideLifecycleResourcePlanError("p013_plan_rows")
    p013_tokens = {
        (str(row["security_id"]), str(row["window_id"]))
        for row in p013_rows
        if isinstance(row, Mapping)
    }
    if len(p013_tokens) != 256:
        raise DownsideLifecycleResourcePlanError("p013_consumed_count")

    p014_binding = config["bindings"]["p014_admission_artifact"]
    p014_root = _artifact_root(training_root, p014_binding)
    p014_verified = verify_admission_artifact(
        p014_root, code_revision=str(p014_binding["code_revision"])
    )
    if (
        p014_verified.get("manifest_sha256") != p014_binding.get("manifest_sha256")
        or p014_result.get("artifact", {}).get("manifest_sha256")
        != p014_binding.get("manifest_sha256")
    ):
        raise DownsideLifecycleResourcePlanError("p014_artifact_binding")
    p014_ledger = _object(p014_root / "candidate-ledger.json", "p014_admission_ledger")
    p014_rows = p014_ledger.get("rows")
    if not isinstance(p014_rows, list) or not p014_rows:
        raise DownsideLifecycleResourcePlanError("p014_admission_rows")
    p014_tokens = {
        (str(row["security_id"]), str(row["window_id"]))
        for row in p014_rows
        if isinstance(row, Mapping)
    }
    if not p014_tokens:
        raise DownsideLifecycleResourcePlanError("p014_consumed_empty")
    if p013_tokens & p014_tokens:
        raise DownsideLifecycleResourcePlanError("consumed_windows_overlap_unexpected")
    consumed = p013_tokens | p014_tokens
    return consumed, {
        "p013_manifest_sha256": p013_verified["manifest_sha256"],
        "p013_plan_digest": p013_verified["plan_digest"],
        "p013_consumed_window_count": len(p013_tokens),
        "p014_manifest_sha256": p014_verified["manifest_sha256"],
        "p014_consumed_window_count": len(p014_tokens),
        "combined_consumed_window_count": len(consumed),
        "combined_consumed_digest": digest(sorted(consumed)),
    }


def _empty_plan(config: Mapping[str, Any], disposition: str) -> dict[str, Any]:
    return {
        "schema_version": config["output"]["plan_schema"],
        "available": False,
        "disposition": disposition,
        "rows": [],
        "row_count": 0,
        "unique_identity_count": 0,
        "primary_count": 0,
        "fallback_count": 0,
        "cell_counts": {},
        "identity_digest": digest([]),
        "plan_digest": digest([]),
        "market_data_read_for_selection": False,
    }


def run_plan(
    *, config_path: Path, training_root: Path, output_root: Path, code_revision: str
) -> dict[str, Any]:
    if not _REVISION.fullmatch(code_revision):
        raise DownsideLifecycleResourcePlanError("exact_code_revision_required")
    root = find_project_root()
    config = load_config(config_path)
    concept = _bound_json(root, config["bindings"]["p020_concept"], "p020_concept")
    model = _bound_json(root, config["bindings"]["resource_model"], "resource_model")
    node = _bound_json(root, config["bindings"]["training_node"], "training_node")
    if (
        concept.get("state")
        != "PREREGISTERED_DOWNSIDE_LIFECYCLE_SEMANTICS_BEFORE_REFERENCE_OR_THRESHOLD_SEARCH"
        or concept.get("next_resource_step", {}).get("work_item") != "P3-STAGE-020-B"
    ):
        raise DownsideLifecycleResourcePlanError("p020_concept_state")
    if model.get("selected_disposition") != (
        "PROCEED_TO_METADATA_ONLY_ERA_BOARD_EXPOSURE_CAPACITY_CENSUS"
    ):
        raise DownsideLifecycleResourcePlanError("resource_model_state")

    training_root = training_root.expanduser().resolve()
    consumed, prior = _consumed_windows(root=root, training_root=training_root, config=config)
    tokens, _windows, source_binding, preserved_ids = _reconstruct_tokens(
        root=root, training_root=training_root, config=config
    )
    screen_model = {"candidate_screen": config["candidate_screen"]}
    candidates = freeze_candidate_surface(tokens=tokens, consumed=consumed, model=screen_model)
    if {str(row["security_id"]) for row in candidates} & preserved_ids:
        raise DownsideLifecycleResourcePlanError("preserved_identity_in_candidates")

    config_digest = digest(config)
    ledger, receipts = _screen_candidates(
        candidates=candidates,
        training_root=training_root,
        node=node,
        spec_digest=config_digest,
    )
    cell_summary, capacity_pass = _cell_summary(
        ledger, required=int(config["selection"]["reserve_per_cell"])
    )

    disposition = "PASS_RESOURCE_ADMISSION_AND_EXACT_PLAN_FROZEN"
    selected: list[dict[str, Any]] = []
    if capacity_pass:
        try:
            selected = select_qualified_plan(ledger, config=config)
        except DevelopmentResourceQualifiedPlanError as exc:
            if str(exc).startswith("global_unique_qualified_shortfall:"):
                disposition = "CLOSED_GLOBAL_IDENTITY_CAPACITY_SHORTFALL"
            else:
                raise DownsideLifecycleResourcePlanError(
                    f"qualified_plan_selection:{exc}"
                ) from exc
    else:
        disposition = "CLOSED_RESOURCE_ADMISSION_CAPACITY_SHORTFALL"

    if selected:
        cell_counts = Counter(str(row["cell"]) for row in selected)
        role_counts = Counter(str(row["role"]) for row in selected)
        plan = {
            "schema_version": config["output"]["plan_schema"],
            "available": True,
            "disposition": disposition,
            "selection_revision": code_revision,
            "rows": selected,
            "row_count": len(selected),
            "unique_identity_count": len({str(row["security_id"]) for row in selected}),
            "primary_count": role_counts["primary"],
            "fallback_count": role_counts["fallback"],
            "cell_counts": dict(sorted(cell_counts.items())),
            "identity_digest": digest(sorted(str(row["security_id"]) for row in selected)),
            "plan_digest": digest(selected),
            "market_data_read_for_selection": False,
        }
    else:
        plan = _empty_plan(config, disposition)

    candidate_payload = {
        "schema_version": config["output"]["candidate_ledger_schema"],
        "candidate_order_frozen_before_input_read": True,
        "rows": ledger,
        "row_count": len(ledger),
        "raw_ohlc_retained": False,
        "structure_facts_built": False,
        "semantic_label_or_reference_read": False,
        "threshold_or_candidate_read": False,
    }
    source_payload = {
        "schema_version": config["output"]["source_binding_schema"],
        **prior,
        "base_manifest_sha256": source_binding["base_manifest_sha256"],
        "correction_manifest_sha256": source_binding["correction_manifest_sha256"],
        "window_receipts": receipts,
        "window_receipts_digest": digest(receipts),
        "preserved_2021_identity_count": len(preserved_ids),
        "preserved_2021_market_read": False,
        "2022_2024_read": False,
        "2025_read": False,
    }
    summary = {
        "schema_version": config["output"]["summary_schema"],
        "work_item": "P3-STAGE-020-B",
        "phase": "DOWNSIDE_LIFECYCLE_RESOURCE_ADMISSION_AND_PLAN",
        "code_revision": code_revision,
        "config_digest": config_digest,
        "disposition": disposition,
        "candidate_window_count": len(ledger),
        "candidate_unique_identity_count": len({str(row["security_id"]) for row in ledger}),
        "qualified_window_count": sum(row["qualified"] is True for row in ledger),
        "qualified_unique_identity_count": len(
            {str(row["security_id"]) for row in ledger if row["qualified"] is True}
        ),
        "cell_summary": cell_summary,
        "all_cells_have_required_qualified_capacity": capacity_pass,
        "exact_plan_available": bool(selected),
        "exact_plan_row_count": plan["row_count"],
        "exact_plan_primary_count": plan["primary_count"],
        "exact_plan_fallback_count": plan["fallback_count"],
        "exact_plan_digest": plan["plan_digest"],
        "candidate_ledger_digest": digest(candidate_payload),
        "source_binding_digest": digest(source_payload),
        "raw_ohlc_published": False,
        "raw_ohlc_retained": False,
        "structure_facts_built": False,
        "semantic_label_or_reference_read": False,
        "threshold_or_candidate_read": False,
        "candidate_scoring": False,
        "primary_stage_output": False,
        "preserved_2021_market_read": False,
        "2022_2024_read": False,
        "2025_read": False,
        "ready_for_separate_semantic_review_freeze": bool(selected),
        "input_readiness_market_read": True,
    }

    run_id = "stage-downside-lifecycle-resource-plan-" + datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    run_root = output_root.expanduser().resolve() / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    payloads = {
        "candidate-ledger.json": canonical(candidate_payload),
        "exact-plan.json": canonical(plan),
        "source-binding.json": canonical(source_payload),
        "summary.json": canonical(summary),
    }
    for name, raw in payloads.items():
        (run_root / name).write_bytes(raw)
    manifest = {
        "schema_version": config["output"]["artifact_schema"],
        "run_id": run_id,
        "code_revision": code_revision,
        "config_digest": config_digest,
        "files": {name: _sha(raw) for name, raw in payloads.items()},
        "authority": {
            "raw_ohlc_published": False,
            "structure_facts_built": False,
            "semantic_label_or_reference_read": False,
            "threshold_or_candidate_read": False,
            "candidate_scoring": False,
            "primary_stage_output": False,
            "preserved_2021_market_read": False,
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
        raise DownsideLifecycleResourcePlanError("artifact_ready_digest")
    if manifest.get("schema_version") != (
        "quanthus.stage-downside-lifecycle-resource-plan-artifact.v1"
    ):
        raise DownsideLifecycleResourcePlanError("artifact_schema")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise DownsideLifecycleResourcePlanError("artifact_code_revision")
    files = manifest.get("files")
    expected = {"candidate-ledger.json", "exact-plan.json", "source-binding.json", "summary.json"}
    if not isinstance(files, Mapping) or set(files) != expected:
        raise DownsideLifecycleResourcePlanError("artifact_files")
    payloads: dict[str, dict[str, Any]] = {}
    for name in expected:
        raw = (root / name).read_bytes()
        if _sha(raw) != files[name]:
            raise DownsideLifecycleResourcePlanError(f"artifact_file_digest:{name}")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise DownsideLifecycleResourcePlanError(f"artifact_shape:{name}")
        payloads[name] = value

    ledger = payloads["candidate-ledger.json"]
    rows = ledger.get("rows")
    if not isinstance(rows, list) or not rows:
        raise DownsideLifecycleResourcePlanError("ledger_rows")
    allowed = {
        "security_id",
        "security_code",
        "era",
        "board_family",
        "cell",
        "window_id",
        "window_start",
        "window_end",
        "identity_rank",
        "window_rank",
        "qualified",
        "reason",
        "source_binding_digest",
    }
    if any(not isinstance(row, Mapping) or set(row) != allowed for row in rows):
        raise DownsideLifecycleResourcePlanError("ledger_row_fields")
    if ledger.get("raw_ohlc_retained") is not False:
        raise DownsideLifecycleResourcePlanError("ledger_raw_ohlc")

    plan = payloads["exact-plan.json"]
    plan_rows = plan.get("rows")
    if not isinstance(plan_rows, list):
        raise DownsideLifecycleResourcePlanError("plan_rows")
    if plan.get("available") is True:
        if len(plan_rows) != 256 or len({str(row["security_id"]) for row in plan_rows}) != 256:
            raise DownsideLifecycleResourcePlanError("plan_identity_count")
        if Counter(str(row["cell"]) for row in plan_rows) != Counter(
            {cell: 32 for cell in sorted({str(row["cell"]) for row in plan_rows})}
        ):
            raise DownsideLifecycleResourcePlanError("plan_cell_counts")
        if Counter(str(row["role"]) for row in plan_rows) != {"primary": 192, "fallback": 64}:
            raise DownsideLifecycleResourcePlanError("plan_role_counts")
    elif plan_rows:
        raise DownsideLifecycleResourcePlanError("unavailable_plan_has_rows")
    if plan.get("plan_digest") != digest(plan_rows):
        raise DownsideLifecycleResourcePlanError("plan_digest")

    summary = payloads["summary.json"]
    if summary.get("exact_plan_digest") != plan.get("plan_digest"):
        raise DownsideLifecycleResourcePlanError("summary_plan_digest")
    if digest(ledger) != summary.get("candidate_ledger_digest"):
        raise DownsideLifecycleResourcePlanError("summary_ledger_digest")
    source = payloads["source-binding.json"]
    if digest(source) != summary.get("source_binding_digest"):
        raise DownsideLifecycleResourcePlanError("summary_source_digest")
    for key in (
        "raw_ohlc_published",
        "raw_ohlc_retained",
        "structure_facts_built",
        "semantic_label_or_reference_read",
        "threshold_or_candidate_read",
        "candidate_scoring",
        "primary_stage_output",
        "preserved_2021_market_read",
        "2022_2024_read",
        "2025_read",
    ):
        if summary.get(key) is not False:
            raise DownsideLifecycleResourcePlanError(f"summary_authority:{key}")
    return {
        "verified": True,
        "run_id": manifest.get("run_id"),
        "manifest_sha256": ready,
        "file_count": len(files),
        "disposition": summary.get("disposition"),
        "candidate_window_count": summary.get("candidate_window_count"),
        "qualified_window_count": summary.get("qualified_window_count"),
        "cell_summary": summary.get("cell_summary"),
        "all_cells_have_required_qualified_capacity": summary.get(
            "all_cells_have_required_qualified_capacity"
        ),
        "exact_plan_available": summary.get("exact_plan_available"),
        "exact_plan_row_count": summary.get("exact_plan_row_count"),
        "exact_plan_primary_count": summary.get("exact_plan_primary_count"),
        "exact_plan_fallback_count": summary.get("exact_plan_fallback_count"),
        "exact_plan_digest": summary.get("exact_plan_digest"),
        "ready_for_separate_semantic_review_freeze": summary.get(
            "ready_for_separate_semantic_review_freeze"
        ),
        "input_readiness_market_read": summary.get("input_readiness_market_read"),
        "preserved_2021_market_read": False,
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
        result = run_plan(
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

"""P3-STAGE-014-B exact plan freeze from the immutable admission ledger.

This stage performs no market read. It verifies the P014-A artifact and deterministically freezes
32 globally identity-unique qualified resources in each of the eight Era x Board cells, with the
first 24 as primary and the next eight as prefrozen same-cell fallbacks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.stage_learning.development_resource_admission_screen import (
    verify_artifact as verify_admission_artifact,
)
from quanthus.structure_v2_inputs import canonical, digest

DEFAULT_CONFIG = "config/stage-learning/stage-development-resource-qualified-plan.v1.json"
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")


class DevelopmentResourceQualifiedPlanError(ValueError):
    """Raised when the P014-B qualified plan contract is violated."""


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DevelopmentResourceQualifiedPlanError(f"cannot_read_{label}") from exc
    if not isinstance(value, dict):
        raise DevelopmentResourceQualifiedPlanError(f"{label}_must_be_object")
    return value


def _sha_bytes(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _sha_file(path: Path) -> str:
    try:
        return _sha_bytes(path.read_bytes())
    except OSError as exc:
        raise DevelopmentResourceQualifiedPlanError(f"cannot_hash:{path.name}") from exc


def _bound_json(root: Path, binding: Mapping[str, Any], label: str) -> dict[str, Any]:
    path = root / str(binding.get("path") or "")
    expected = str(binding.get("sha256") or "")
    if not _SHA.fullmatch(expected) or _sha_file(path) != expected:
        raise DevelopmentResourceQualifiedPlanError(f"binding:{label}")
    return _object(path, label)


def load_config(path: Path) -> dict[str, Any]:
    value = _object(path, "qualified_plan_config")
    if (
        value.get("schema_version") != "quanthus.stage-development-resource-qualified-plan.v1"
        or value.get("work_item") != "P3-STAGE-014-B"
        or value.get("state")
        != "PREREGISTERED_QUALIFIED_PLAN_FROM_IMMUTABLE_ADMISSION_LEDGER"
    ):
        raise DevelopmentResourceQualifiedPlanError("config_identity")
    selection = value.get("selection")
    runtime = value.get("runtime")
    authority = value.get("authority")
    if not all(isinstance(item, Mapping) for item in (selection, runtime, authority)):
        raise DevelopmentResourceQualifiedPlanError("config_sections")
    if (
        selection.get("reserve_per_cell") != 32
        or selection.get("primary_per_cell") != 24
        or selection.get("fallback_per_cell") != 8
        or selection.get("total_rows") != 256
        or selection.get("global_identity_unique") is not True
        or selection.get("cross_cell_fill") is not False
        or selection.get("post_result_replacement") is not False
        or runtime.get("maximum_jobs") != 1
        or runtime.get("automatic_retries") != 0
    ):
        raise DevelopmentResourceQualifiedPlanError("config_budget")
    for key in (
        "market_price_read",
        "structure_fact_build",
        "threshold_basis",
        "reference_or_label_read",
        "candidate_output",
        "candidate_scoring",
        "preserved_2021_market_read",
        "2022_2024_read",
        "2025_read",
        "semantic_job_authorized",
        "daily_or_strategy",
        "trading",
    ):
        if authority.get(key) is not False:
            raise DevelopmentResourceQualifiedPlanError(f"authority:{key}")
    return value


def select_qualified_plan(
    rows: Sequence[Mapping[str, Any]], *, config: Mapping[str, Any]
) -> list[dict[str, Any]]:
    selection = config["selection"]
    cell_order = tuple(str(value) for value in selection["cell_order"])
    reserve = int(selection["reserve_per_cell"])
    primary = int(selection["primary_per_cell"])
    by_cell: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("qualified") is True and str(row.get("cell")) in cell_order:
            by_cell[str(row["cell"])].append(row)
    used: set[str] = set()
    selected: list[dict[str, Any]] = []
    for cell_rank, cell in enumerate(cell_order, start=1):
        # One deterministic qualified window per identity, inherited from the preregistered
        # candidate identity/window ranks; no market value participates here.
        best_by_identity: dict[str, Mapping[str, Any]] = {}
        for row in sorted(
            by_cell[cell],
            key=lambda item: (
                int(item["identity_rank"]),
                int(item["window_rank"]),
                str(item["security_id"]),
                str(item["window_id"]),
            ),
        ):
            best_by_identity.setdefault(str(row["security_id"]), row)
        candidates = sorted(
            best_by_identity.values(),
            key=lambda item: (
                int(item["identity_rank"]),
                int(item["window_rank"]),
                str(item["security_id"]),
                str(item["window_id"]),
            ),
        )
        chosen: list[Mapping[str, Any]] = []
        for row in candidates:
            identity = str(row["security_id"])
            if identity in used:
                continue
            chosen.append(row)
            used.add(identity)
            if len(chosen) == reserve:
                break
        if len(chosen) != reserve:
            raise DevelopmentResourceQualifiedPlanError(
                f"global_unique_qualified_shortfall:{cell}:{len(chosen)}"
            )
        for slot, row in enumerate(chosen, start=1):
            selected.append(
                {
                    "security_id": str(row["security_id"]),
                    "security_code": str(row["security_code"]),
                    "era": str(row["era"]),
                    "board_family": str(row["board_family"]),
                    "cell": cell,
                    "window_id": str(row["window_id"]),
                    "window_start": str(row["window_start"]),
                    "window_end": str(row["window_end"]),
                    "admission_source_binding_digest": str(row["source_binding_digest"]),
                    "admission_identity_rank": int(row["identity_rank"]),
                    "admission_window_rank": int(row["window_rank"]),
                    "cell_rank": cell_rank,
                    "plan_slot": slot,
                    "role": "primary" if slot <= primary else "fallback",
                }
            )
    if len(selected) != 256 or len({row["security_id"] for row in selected}) != 256:
        raise DevelopmentResourceQualifiedPlanError("global_identity_uniqueness")
    return selected


def run_plan(
    *, config_path: Path, training_root: Path, output_root: Path, code_revision: str
) -> dict[str, Any]:
    if not _REVISION.fullmatch(code_revision):
        raise DevelopmentResourceQualifiedPlanError("exact_code_revision_required")
    root = find_project_root()
    config = load_config(config_path)
    model = _bound_json(root, config["bindings"]["admission_model"], "admission_model")
    result = _bound_json(root, config["bindings"]["admission_result"], "admission_result")
    _bound_json(root, config["bindings"]["admission_screen"], "admission_screen")
    if model.get("state") != "DESIGN_SELECTED_ADMISSION_SCREEN_REQUIRED_BEFORE_SEMANTIC_READ":
        raise DevelopmentResourceQualifiedPlanError("model_state")
    if result.get("state") != "PASS_ADMISSION_CAPACITY_READY_FOR_QUALIFIED_PLAN":
        raise DevelopmentResourceQualifiedPlanError("admission_result_state")
    if result.get("totals", {}).get("all_cells_have_required_qualified_capacity") is not True:
        raise DevelopmentResourceQualifiedPlanError("admission_capacity_not_ready")

    artifact_binding = config["bindings"]["admission_artifact"]
    artifact_root = (
        training_root.expanduser().resolve()
        / str(artifact_binding["artifact_relative_path"])
    ).resolve()
    verified = verify_admission_artifact(
        artifact_root,
        code_revision=str(artifact_binding["code_revision"]),
    )
    if (
        verified.get("manifest_sha256") != artifact_binding["manifest_sha256"]
        or result.get("artifact", {}).get("manifest_sha256")
        != artifact_binding["manifest_sha256"]
    ):
        raise DevelopmentResourceQualifiedPlanError("admission_artifact_binding")
    ledger = _object(artifact_root / "candidate-ledger.json", "admission_candidate_ledger")
    rows = ledger.get("rows")
    if not isinstance(rows, list) or not rows:
        raise DevelopmentResourceQualifiedPlanError("admission_ledger_rows")
    selected = select_qualified_plan(rows, config=config)
    cell_counts = Counter(row["cell"] for row in selected)
    role_counts = Counter(row["role"] for row in selected)
    if len(cell_counts) != 8 or any(value != 32 for value in cell_counts.values()):
        raise DevelopmentResourceQualifiedPlanError("cell_counts")
    if role_counts != {"primary": 192, "fallback": 64}:
        raise DevelopmentResourceQualifiedPlanError("role_counts")
    plan = {
        "schema_version": config["output"]["plan_schema"],
        "selection_revision": code_revision,
        "rows": selected,
        "row_count": len(selected),
        "unique_identity_count": len({row["security_id"] for row in selected}),
        "primary_count": role_counts["primary"],
        "fallback_count": role_counts["fallback"],
        "cell_counts": dict(sorted(cell_counts.items())),
        "identity_digest": digest(sorted(row["security_id"] for row in selected)),
        "plan_digest": digest(selected),
        "source": "immutable_p014_admission_ledger_only",
        "market_data_read": False,
    }
    source = {
        "schema_version": config["output"]["source_binding_schema"],
        "admission_job_id": artifact_binding["job_id"],
        "admission_manifest_sha256": verified["manifest_sha256"],
        "admission_candidate_window_count": verified["candidate_window_count"],
        "admission_qualified_window_count": verified["qualified_window_count"],
        "admission_all_cells_ready": verified["all_cells_have_required_qualified_capacity"],
        "admission_ledger_digest": digest(ledger),
        "market_data_read": False,
        "preserved_2021_market_read": False,
        "2022_2024_read": False,
        "2025_read": False,
    }
    summary = {
        "schema_version": config["output"]["summary_schema"],
        "work_item": "P3-STAGE-014-B",
        "phase": "FREEZE_ADMISSION_QUALIFIED_ERA_BOARD_PLAN",
        "code_revision": code_revision,
        "config_digest": digest(config),
        "row_count": plan["row_count"],
        "unique_identity_count": plan["unique_identity_count"],
        "primary_count": plan["primary_count"],
        "fallback_count": plan["fallback_count"],
        "cell_counts": plan["cell_counts"],
        "identity_digest": plan["identity_digest"],
        "plan_digest": plan["plan_digest"],
        "market_price_read": False,
        "structure_fact_build": False,
        "threshold_basis": False,
        "reference_or_label_read": False,
        "candidate_output": False,
        "candidate_scoring": False,
        "preserved_2021_market_read": False,
        "2022_2024_read": False,
        "2025_read": False,
        "semantic_job_authorized": False,
    }
    run_id = "stage-development-resource-qualified-plan-" + datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    run_root = output_root.expanduser().resolve() / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    payloads = {
        "qualified-plan.json": canonical(plan),
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
        "files": {name: _sha_bytes(raw) for name, raw in payloads.items()},
        "authority": {
            "market_price_read": False,
            "structure_fact_build": False,
            "threshold_basis": False,
            "reference_or_label_read": False,
            "candidate_output": False,
            "candidate_scoring": False,
            "preserved_2021_market_read": False,
            "2022_2024_read": False,
            "2025_read": False,
        },
    }
    manifest_raw = canonical(manifest)
    (run_root / "manifest.json").write_bytes(manifest_raw)
    ready = _sha_bytes(manifest_raw)
    (run_root / "READY").write_text(ready + "\n", encoding="ascii")
    return {**summary, "run_id": run_id, "run_root": str(run_root), "manifest_sha256": ready}


def verify_artifact(path: Path, *, code_revision: str | None = None) -> dict[str, Any]:
    root = path.expanduser().resolve()
    manifest_raw = (root / "manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    ready = (root / "READY").read_text(encoding="ascii").strip()
    if _sha_bytes(manifest_raw) != ready:
        raise DevelopmentResourceQualifiedPlanError("artifact_ready_digest")
    if (
        manifest.get("schema_version")
        != "quanthus.stage-development-resource-qualified-plan-artifact.v1"
    ):
        raise DevelopmentResourceQualifiedPlanError("artifact_schema")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise DevelopmentResourceQualifiedPlanError("artifact_code_revision")
    files = manifest.get("files")
    expected = {"qualified-plan.json", "source-binding.json", "summary.json"}
    if not isinstance(files, Mapping) or set(files) != expected:
        raise DevelopmentResourceQualifiedPlanError("artifact_files")
    payloads: dict[str, dict[str, Any]] = {}
    for name in expected:
        raw = (root / name).read_bytes()
        if _sha_bytes(raw) != files[name]:
            raise DevelopmentResourceQualifiedPlanError(f"artifact_file_digest:{name}")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise DevelopmentResourceQualifiedPlanError(f"artifact_shape:{name}")
        payloads[name] = value
    plan = payloads["qualified-plan.json"]
    rows = plan.get("rows")
    if not isinstance(rows, list) or len(rows) != 256:
        raise DevelopmentResourceQualifiedPlanError("plan_rows")
    identities = [str(row["security_id"]) for row in rows if isinstance(row, Mapping)]
    if len(identities) != 256 or len(set(identities)) != 256:
        raise DevelopmentResourceQualifiedPlanError("plan_identity_uniqueness")
    cells = Counter(str(row["cell"]) for row in rows)
    roles = Counter(str(row["role"]) for row in rows)
    if len(cells) != 8 or any(value != 32 for value in cells.values()):
        raise DevelopmentResourceQualifiedPlanError("plan_cell_counts")
    if roles != {"primary": 192, "fallback": 64}:
        raise DevelopmentResourceQualifiedPlanError("plan_role_counts")
    if plan.get("plan_digest") != digest(rows):
        raise DevelopmentResourceQualifiedPlanError("plan_digest")
    summary = payloads["summary.json"]
    if summary.get("plan_digest") != plan.get("plan_digest"):
        raise DevelopmentResourceQualifiedPlanError("summary_plan_digest")
    for key in (
        "market_price_read",
        "structure_fact_build",
        "threshold_basis",
        "reference_or_label_read",
        "candidate_output",
        "candidate_scoring",
        "preserved_2021_market_read",
        "2022_2024_read",
        "2025_read",
        "semantic_job_authorized",
    ):
        if summary.get(key) is not False:
            raise DevelopmentResourceQualifiedPlanError(f"summary_authority:{key}")
    return {
        "verified": True,
        "run_id": manifest.get("run_id"),
        "manifest_sha256": ready,
        "file_count": len(files),
        "row_count": 256,
        "unique_identity_count": 256,
        "primary_count": 192,
        "fallback_count": 64,
        "cell_counts": dict(sorted(cells.items())),
        "identity_digest": plan.get("identity_digest"),
        "plan_digest": plan.get("plan_digest"),
        "market_data_read": False,
        "semantic_job_authorized": False,
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
    print(json.dumps(result, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

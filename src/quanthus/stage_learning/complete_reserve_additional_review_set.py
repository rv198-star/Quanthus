"""Freeze the complete P015-B additional blind-review denominator.

This metadata-only stage consumes the immutable P015-A review-readiness ledger and the exact
P014-B qualified plan. It selects every row marked new_review_required=true, joins only frozen
resource metadata, and performs no market, semantic, reviewer, threshold, candidate, or scoring
read.
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
from quanthus.stage_learning.complete_reserve_review_readiness import (
    verify_artifact as verify_readiness_artifact,
)
from quanthus.stage_learning.development_resource_qualified_plan import (
    verify_artifact as verify_qualified_plan_artifact,
)
from quanthus.structure_v2_inputs import canonical, digest

DEFAULT_CONFIG = "config/stage-learning/stage-complete-reserve-additional-review-set.v1.json"
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")


class CompleteReserveAdditionalReviewSetError(ValueError):
    """The P015-B metadata freeze contract was violated."""


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CompleteReserveAdditionalReviewSetError(f"cannot_read_{label}") from exc
    if not isinstance(value, dict):
        raise CompleteReserveAdditionalReviewSetError(f"{label}_must_be_object")
    return value


def _sha_bytes(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _sha_file(path: Path) -> str:
    try:
        return _sha_bytes(path.read_bytes())
    except OSError as exc:
        raise CompleteReserveAdditionalReviewSetError(f"cannot_hash:{path.name}") from exc


def _bound_json(root: Path, binding: Mapping[str, Any], label: str) -> dict[str, Any]:
    path = root / str(binding.get("path") or "")
    expected = str(binding.get("sha256") or "")
    if not _SHA.fullmatch(expected) or _sha_file(path) != expected:
        raise CompleteReserveAdditionalReviewSetError(f"binding:{label}")
    return _object(path, label)


def load_config(path: Path) -> dict[str, Any]:
    value = _object(path, "additional_review_set_config")
    if (
        value.get("schema_version") != "quanthus.stage-complete-reserve-additional-review-set.v1"
        or value.get("work_item") != "P3-STAGE-015-B"
        or value.get("state") != "PREREGISTERED_FREEZE_ALL_READY_UNREVIEWED_ROWS"
    ):
        raise CompleteReserveAdditionalReviewSetError("config_identity")
    selection = value.get("selection")
    runtime = value.get("runtime")
    authority = value.get("authority")
    if not all(isinstance(item, Mapping) for item in (selection, runtime, authority)):
        raise CompleteReserveAdditionalReviewSetError("config_sections")
    if (
        selection.get("source_row_count") != 256
        or selection.get("ready_row_count") != 252
        or selection.get("already_reviewed_ready_count") != 192
        or selection.get("expected_additional_review_count") != 60
        or selection.get("include_every_matching_row") is not True
        or selection.get("positive_count_stop") is not False
        or selection.get("semantic_value_sort") is not False
        or selection.get("reference_value_sort") is not False
        or selection.get("random_sample") is not False
        or selection.get("post_result_replacement") is not False
        or selection.get("new_identity_discovery") is not False
        or runtime.get("maximum_jobs") != 1
        or runtime.get("automatic_retries") != 0
    ):
        raise CompleteReserveAdditionalReviewSetError("config_budget")
    for key in (
        "market_data_read",
        "structure_fact_build",
        "semantic_values_read",
        "reference_opinions_read",
        "candidate_output_read",
        "threshold_basis_read",
        "selection_transport_read",
        "candidate_scoring",
        "preserved_2021_market_read",
        "2025_read",
        "daily_or_strategy",
        "trading",
    ):
        if authority.get(key) is not False:
            raise CompleteReserveAdditionalReviewSetError(f"authority:{key}")
    return value


def _read_bound_artifact_json(
    artifact_root: Path,
    *,
    file_name: str,
    manifest_sha256: str,
) -> dict[str, Any]:
    manifest_raw = (artifact_root / "manifest.json").read_bytes()
    manifest_sha = _sha_bytes(manifest_raw)
    ready = (artifact_root / "READY").read_text(encoding="ascii").strip()
    if manifest_sha != manifest_sha256 or ready != manifest_sha:
        raise CompleteReserveAdditionalReviewSetError("artifact_manifest_binding")
    manifest = json.loads(manifest_raw)
    raw = (artifact_root / file_name).read_bytes()
    if manifest.get("files", {}).get(file_name) != _sha_bytes(raw):
        raise CompleteReserveAdditionalReviewSetError(f"artifact_file_binding:{file_name}")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise CompleteReserveAdditionalReviewSetError(f"artifact_shape:{file_name}")
    return value


def run_freeze(
    *, config_path: Path, training_root: Path, output_root: Path, code_revision: str
) -> dict[str, Any]:
    if not _REVISION.fullmatch(code_revision):
        raise CompleteReserveAdditionalReviewSetError("exact_code_revision_required")
    root = find_project_root()
    config = load_config(config_path)
    model = _bound_json(root, config["bindings"]["reference_model"], "reference_model")
    readiness_result = _bound_json(
        root, config["bindings"]["readiness_result"], "readiness_result"
    )
    plan_result = _bound_json(
        root, config["bindings"]["qualified_plan_result"], "qualified_plan_result"
    )
    closeout = _bound_json(root, config["bindings"]["p014_closeout"], "p014_closeout")
    if model.get("work_item") != "P3-STAGE-015":
        raise CompleteReserveAdditionalReviewSetError("model_identity")
    if readiness_result.get("state") != "PASS_COMPLETE_RESERVE_REVIEW_READINESS":
        raise CompleteReserveAdditionalReviewSetError("readiness_result_state")
    if plan_result.get("state") != "PASS_QUALIFIED_EXACT_PLAN_FROZEN":
        raise CompleteReserveAdditionalReviewSetError("qualified_plan_result_state")
    if closeout.get("review_ledger", {}).get("ledger_sha256") != (
        config["bindings"]["p014_closeout"]["review_ledger_sha256"]
    ):
        raise CompleteReserveAdditionalReviewSetError("p014_review_ledger_binding")

    training_root = training_root.expanduser().resolve()
    readiness_binding = config["bindings"]["readiness_artifact"]
    readiness_root = (training_root / str(readiness_binding["artifact_relative_path"])).resolve()
    readiness_verified = verify_readiness_artifact(
        readiness_root, code_revision=str(readiness_binding["code_revision"])
    )
    if (
        readiness_verified.get("manifest_sha256") != readiness_binding["manifest_sha256"]
        or readiness_verified.get("ledger_digest") != readiness_binding["ledger_digest"]
        or readiness_verified.get("row_count") != 256
        or readiness_verified.get("ready_count") != 252
        or readiness_verified.get("already_reviewed_ready_count") != 192
        or readiness_verified.get("new_review_required_count") != 60
        or readiness_verified.get("continuation_pass") is not True
    ):
        raise CompleteReserveAdditionalReviewSetError("readiness_artifact_binding")
    readiness = _read_bound_artifact_json(
        readiness_root,
        file_name="review-readiness.json",
        manifest_sha256=str(readiness_binding["manifest_sha256"]),
    )
    readiness_rows = readiness.get("rows")
    if (
        not isinstance(readiness_rows, list)
        or digest(readiness_rows) != readiness_binding["ledger_digest"]
    ):
        raise CompleteReserveAdditionalReviewSetError("readiness_rows_binding")

    plan_binding = config["bindings"]["qualified_plan_artifact"]
    plan_root = (training_root / str(plan_binding["artifact_relative_path"])).resolve()
    plan_verified = verify_qualified_plan_artifact(
        plan_root, code_revision=str(plan_binding["code_revision"])
    )
    if (
        plan_verified.get("manifest_sha256") != plan_binding["manifest_sha256"]
        or plan_verified.get("plan_digest") != plan_binding["plan_digest"]
        or plan_verified.get("identity_digest") != plan_binding["identity_digest"]
    ):
        raise CompleteReserveAdditionalReviewSetError("qualified_plan_artifact_binding")
    plan = _read_bound_artifact_json(
        plan_root,
        file_name="qualified-plan.json",
        manifest_sha256=str(plan_binding["manifest_sha256"]),
    )
    plan_rows = plan.get("rows")
    if not isinstance(plan_rows, list) or len(plan_rows) != 256:
        raise CompleteReserveAdditionalReviewSetError("qualified_plan_rows")
    by_resource = {
        (str(row["security_id"]), str(row["window_id"])): row
        for row in plan_rows
        if isinstance(row, Mapping)
    }
    if len(by_resource) != 256:
        raise CompleteReserveAdditionalReviewSetError("qualified_plan_resource_identity")

    selected_readiness = [
        row
        for row in readiness_rows
        if (
            isinstance(row, Mapping)
            and row.get("review_packet_ready") is True
            and row.get("already_reviewed_in_p014_ledger") is False
            and row.get("new_review_required") is True
        )
    ]
    if len(selected_readiness) != 60:
        raise CompleteReserveAdditionalReviewSetError("additional_review_count")
    rows: list[dict[str, Any]] = []
    for readiness_row in selected_readiness:
        key = (str(readiness_row["security_id"]), str(readiness_row["window_id"]))
        plan_row = by_resource.get(key)
        if not isinstance(plan_row, Mapping):
            raise CompleteReserveAdditionalReviewSetError("additional_review_plan_join")
        if (
            str(plan_row["era"]) != str(readiness_row["era"])
            or str(plan_row["board_family"]) != str(readiness_row["board_family"])
            or str(plan_row["cell"]) != str(readiness_row["cell"])
        ):
            raise CompleteReserveAdditionalReviewSetError("additional_review_stratum_join")
        rows.append(
            {
                "security_id": str(plan_row["security_id"]),
                "security_code": str(plan_row["security_code"]),
                "era": str(plan_row["era"]),
                "board_family": str(plan_row["board_family"]),
                "cell": str(plan_row["cell"]),
                "window_id": str(plan_row["window_id"]),
                "window_start": str(plan_row["window_start"]),
                "window_end": str(plan_row["window_end"]),
                "plan_slot": int(plan_row["plan_slot"]),
                "plan_role": str(plan_row["role"]),
                "readiness_source_member_closure_digest": str(
                    readiness_row["source_member_closure_digest"]
                ),
            }
        )
    if len({(row["security_id"], row["window_id"]) for row in rows}) != 60:
        raise CompleteReserveAdditionalReviewSetError("additional_review_uniqueness")
    cell_counts = Counter(row["cell"] for row in rows)
    frozen_set = {
        "schema_version": config["output"]["set_schema"],
        "rows": rows,
        "row_count": len(rows),
        "cell_counts": dict(sorted(cell_counts.items())),
        "identity_digest": digest(sorted(row["security_id"] for row in rows)),
        "set_digest": digest(rows),
        "selection_predicate": config["selection"]["predicate"],
        "all_matching_rows_included": True,
        "semantic_selection": False,
    }
    source = {
        "schema_version": config["output"]["source_binding_schema"],
        "readiness_job_id": readiness_binding["job_id"],
        "readiness_manifest_sha256": readiness_verified["manifest_sha256"],
        "readiness_ledger_digest": readiness_verified["ledger_digest"],
        "qualified_plan_job_id": plan_binding["job_id"],
        "qualified_plan_manifest_sha256": plan_verified["manifest_sha256"],
        "qualified_plan_digest": plan_verified["plan_digest"],
        "p014_review_ledger_sha256": config["bindings"]["p014_closeout"][
            "review_ledger_sha256"
        ],
        "market_data_read": False,
        "semantic_values_read": False,
        "reference_opinions_read": False,
        "candidate_or_threshold_output_read": False,
    }
    summary = {
        "schema_version": config["output"]["summary_schema"],
        "work_item": "P3-STAGE-015-B",
        "phase": "FREEZE_COMPLETE_ADDITIONAL_REVIEW_SET",
        "code_revision": code_revision,
        "config_digest": digest(config),
        "row_count": len(rows),
        "cell_counts": dict(sorted(cell_counts.items())),
        "identity_digest": frozen_set["identity_digest"],
        "set_digest": frozen_set["set_digest"],
        "all_matching_rows_included": True,
        "positive_count_stop": False,
        "semantic_values_read": False,
        "reference_opinions_read": False,
        "candidate_output_read": False,
        "threshold_basis_read": False,
        "selection_transport_read": False,
        "preserved_2021_market_read": False,
        "2025_read": False,
    }

    run_id = "stage-complete-reserve-additional-review-set-" + datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    run_root = output_root.expanduser().resolve() / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    payloads = {
        "additional-review-set.json": canonical(frozen_set),
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
            "market_data_read": False,
            "semantic_values_read": False,
            "reference_opinions_read": False,
            "candidate_output_read": False,
            "threshold_basis_read": False,
            "selection_transport_read": False,
            "candidate_scoring": False,
            "preserved_2021_market_read": False,
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
        raise CompleteReserveAdditionalReviewSetError("artifact_ready_digest")
    if (
        manifest.get("schema_version")
        != "quanthus.stage-complete-reserve-additional-review-set-artifact.v1"
    ):
        raise CompleteReserveAdditionalReviewSetError("artifact_schema")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise CompleteReserveAdditionalReviewSetError("artifact_code_revision")
    files = manifest.get("files")
    expected = {"additional-review-set.json", "source-binding.json", "summary.json"}
    if not isinstance(files, Mapping) or set(files) != expected:
        raise CompleteReserveAdditionalReviewSetError("artifact_files")
    payloads: dict[str, dict[str, Any]] = {}
    for name in expected:
        raw = (root / name).read_bytes()
        if _sha_bytes(raw) != files[name]:
            raise CompleteReserveAdditionalReviewSetError(f"artifact_file_digest:{name}")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise CompleteReserveAdditionalReviewSetError(f"artifact_shape:{name}")
        payloads[name] = value
    frozen_set = payloads["additional-review-set.json"]
    rows = frozen_set.get("rows")
    allowed = {
        "security_id",
        "security_code",
        "era",
        "board_family",
        "cell",
        "window_id",
        "window_start",
        "window_end",
        "plan_slot",
        "plan_role",
        "readiness_source_member_closure_digest",
    }
    if (
        not isinstance(rows, list)
        or len(rows) != 60
        or any(not isinstance(row, Mapping) or set(row) != allowed for row in rows)
        or len({(row["security_id"], row["window_id"]) for row in rows}) != 60
        or frozen_set.get("set_digest") != digest(rows)
        or frozen_set.get("all_matching_rows_included") is not True
        or frozen_set.get("semantic_selection") is not False
    ):
        raise CompleteReserveAdditionalReviewSetError("artifact_set_binding")
    summary = payloads["summary.json"]
    if (
        summary.get("row_count") != 60
        or summary.get("set_digest") != frozen_set.get("set_digest")
        or summary.get("identity_digest") != frozen_set.get("identity_digest")
        or summary.get("all_matching_rows_included") is not True
        or summary.get("positive_count_stop") is not False
    ):
        raise CompleteReserveAdditionalReviewSetError("artifact_summary_binding")
    for key in (
        "semantic_values_read",
        "reference_opinions_read",
        "candidate_output_read",
        "threshold_basis_read",
        "selection_transport_read",
        "preserved_2021_market_read",
        "2025_read",
    ):
        if summary.get(key) is not False:
            raise CompleteReserveAdditionalReviewSetError(f"artifact_authority:{key}")
    return {
        "verified": True,
        "run_id": manifest.get("run_id"),
        "manifest_sha256": ready,
        "file_count": len(files),
        "row_count": 60,
        "cell_counts": frozen_set.get("cell_counts"),
        "identity_digest": frozen_set.get("identity_digest"),
        "set_digest": frozen_set.get("set_digest"),
        "all_matching_rows_included": True,
        "positive_count_stop": False,
        "semantic_values_read": False,
        "reference_opinions_read": False,
        "candidate_output_read": False,
        "threshold_basis_read": False,
        "selection_transport_read": False,
        "preserved_2021_market_read": False,
        "2025_read": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--config", default=DEFAULT_CONFIG)
    run.add_argument("--training-root", required=True)
    run.add_argument("--output-root", required=True)
    run.add_argument("--code-revision", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--artifact", required=True)
    verify.add_argument("--code-revision")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "run":
        result = run_freeze(
            config_path=Path(args.config),
            training_root=Path(args.training_root),
            output_root=Path(args.output_root),
            code_revision=args.code_revision,
        )
    else:
        result = verify_artifact(Path(args.artifact), code_revision=args.code_revision)
    print(json.dumps(result, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""P3-STAGE-015-A review-packet readiness census over the complete P014-B reserve.

The census rereads only the 256 exact admission-qualified 61-session windows and persists only
review-packet readiness, bounded reason codes and immutable source/resource bindings. Structure
values may be computed transiently by the accepted fact builders only to determine whether the
frozen primary/path packet exists at T; no value, threshold, candidate, opinion or future outcome is
published by this stage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.stage_learning.atomic_proposition_admission_qualified_prepare import (
    _runtime_plan_rows,
)
from quanthus.stage_learning.atomic_proposition_admission_qualified_prepare import (
    verify_artifact as verify_p014_semantic_artifact,
)
from quanthus.stage_learning.atomic_proposition_successor_development_prepare import (
    _read_exact_plan_windows,
)
from quanthus.stage_learning.development_resource_qualified_plan import (
    verify_artifact as verify_qualified_plan_artifact,
)
from quanthus.stage_learning.order_proposition_development import build_order_observations
from quanthus.stage_learning.positive_instability_development_prepare import _path_map
from quanthus.structure_v2_inputs import canonical, digest

DEFAULT_CONFIG = "config/stage-learning/stage-complete-reserve-review-readiness.v1.json"
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")


class CompleteReserveReviewReadinessError(ValueError):
    """Raised when the P015-A frozen readiness contract is violated."""


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CompleteReserveReviewReadinessError(f"cannot_read_{label}") from exc
    if not isinstance(value, dict):
        raise CompleteReserveReviewReadinessError(f"{label}_must_be_object")
    return value


def _sha_bytes(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _sha_file(path: Path) -> str:
    try:
        return _sha_bytes(path.read_bytes())
    except OSError as exc:
        raise CompleteReserveReviewReadinessError(f"cannot_hash:{path.name}") from exc


def _bound_json(root: Path, binding: Mapping[str, Any], label: str) -> dict[str, Any]:
    path = root / str(binding.get("path") or "")
    expected = str(binding.get("sha256") or "")
    if not _SHA.fullmatch(expected) or _sha_file(path) != expected:
        raise CompleteReserveReviewReadinessError(f"binding:{label}")
    return _object(path, label)


def load_config(path: Path) -> dict[str, Any]:
    value = _object(path, "complete_reserve_review_readiness")
    if (
        value.get("schema_version") != "quanthus.stage-complete-reserve-review-readiness.v1"
        or value.get("work_item") != "P3-STAGE-015-A"
        or value.get("state") != "PREREGISTERED_COMPLETE_RESERVE_REVIEW_READINESS_CENSUS"
    ):
        raise CompleteReserveReviewReadinessError("config_identity")
    resource = value.get("resource_contract")
    continuation = value.get("continuation_gate")
    retention = value.get("retention")
    runtime = value.get("runtime")
    authority = value.get("authority")
    if not all(
        isinstance(item, Mapping)
        for item in (resource, continuation, retention, runtime, authority)
    ):
        raise CompleteReserveReviewReadinessError("config_sections")
    if (
        resource.get("exact_row_count") != 256
        or resource.get("exact_cell_count") != 8
        or resource.get("exact_rows_per_cell") != 32
        or resource.get("global_identity_unique") is not True
        or resource.get("new_identity_discovery") is not False
        or resource.get("morphology_enrichment") is not False
        or resource.get("result_count_targeted_top_up") is not False
        or continuation.get("minimum_total_ready") != 192
        or continuation.get("minimum_ready_per_cell") != 24
        or continuation.get("all_p014_reviewed_rows_must_remain_ready") is not True
        or runtime.get("maximum_jobs") != 1
        or runtime.get("automatic_retries") != 0
    ):
        raise CompleteReserveReviewReadinessError("config_budget")
    required_false = (
        "persist_price_or_morphology_values",
        "reference_opinion_read",
        "reference_opinion_write",
        "p014_review_ledger_rewrite",
        "selection_transport_read",
        "candidate_output_read",
        "candidate_scoring",
        "parameter_search",
        "numeric_gate_change",
        "preserved_2021_market_read",
        "2022_2024_read",
        "2025_read",
        "daily_or_strategy",
        "trading",
    )
    if authority.get("exact_window_market_read") is not True:
        raise CompleteReserveReviewReadinessError("market_read_authority")
    if authority.get("review_readiness_computation") is not True:
        raise CompleteReserveReviewReadinessError("readiness_authority")
    if any(authority.get(key) is not False for key in required_false):
        raise CompleteReserveReviewReadinessError("authority_ceiling")
    return value


def _qualified_plan(
    training_root: Path, binding: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
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
    ):
        raise CompleteReserveReviewReadinessError("qualified_plan_artifact_binding")
    plan = _object(artifact_root / "qualified-plan.json", "qualified_plan")
    rows = plan.get("rows")
    if not isinstance(rows, list):
        raise CompleteReserveReviewReadinessError("qualified_plan_rows")
    runtime_rows = _runtime_plan_rows(rows)
    return runtime_rows, verified


def _p014_reviewed_resources(
    training_root: Path, binding: Mapping[str, Any]
) -> set[tuple[str, str]]:
    artifact_root = (training_root / str(binding["artifact_relative_path"])).resolve()
    verified = verify_p014_semantic_artifact(
        artifact_root,
        code_revision=str(binding["code_revision"]),
    )
    if (
        verified.get("manifest_sha256") != binding["manifest_sha256"]
        or verified.get("development_selected_case_count") != binding["selected_case_count"]
    ):
        raise CompleteReserveReviewReadinessError("p014_semantic_artifact_binding")
    selection = _object(artifact_root / "development-selection.json", "p014_selection")
    selected = selection.get("selected")
    if not isinstance(selected, list) or len(selected) != 192:
        raise CompleteReserveReviewReadinessError("p014_selection_denominator")
    resources = {
        (str(row["security_id"]), str(row["window_id"]))
        for row in selected
        if isinstance(row, Mapping)
    }
    if len(resources) != 192:
        raise CompleteReserveReviewReadinessError("p014_selection_resource_identity")
    return resources


def _readiness_reason(
    *,
    security_code: str,
    t: date,
    observation_keys: set[tuple[str, date]],
    path_values: Mapping[tuple[str, date], Mapping[str, Any]],
) -> str:
    key = (security_code, t)
    if key not in observation_keys:
        return "PRIMARY_FACTS_UNAVAILABLE"
    paths = path_values.get(key)
    if not isinstance(paths, Mapping):
        return "DIRECTIONAL_PATH_FACTS_UNAVAILABLE"
    traversal = paths.get("structure.geometry.range-traversal")
    counter = paths.get("structure.geometry.counter-direction-excursion-share")
    if not isinstance(traversal, Mapping) or not isinstance(counter, Mapping):
        return "DIRECTIONAL_PATH_FACTS_UNAVAILABLE"
    if traversal.get("reason") != "available" or traversal.get("value") is None:
        return "RANGE_TRAVERSAL_UNAVAILABLE"
    if counter.get("reason") not in {"available", "zero_net_direction"}:
        return "COUNTER_EXCURSION_UNAVAILABLE"
    return "READY"


def run_census(
    *, config_path: Path, training_root: Path, output_root: Path, code_revision: str
) -> dict[str, Any]:
    if not _REVISION.fullmatch(code_revision):
        raise CompleteReserveReviewReadinessError("exact_code_revision_required")
    root = find_project_root()
    config = load_config(config_path)
    model = _bound_json(root, config["bindings"]["successor_model"], "successor_model")
    result = _bound_json(root, config["bindings"]["qualified_plan_result"], "qualified_plan_result")
    closeout = _bound_json(root, config["bindings"]["p014_closeout"], "p014_closeout")
    node = _bound_json(root, config["bindings"]["training_node"], "training_node")
    if model.get("selected_disposition") != "PROCEED_TO_COMPLETE_RESERVE_REVIEW_READINESS_CENSUS":
        raise CompleteReserveReviewReadinessError("successor_model_state")
    if result.get("state") != "PASS_QUALIFIED_EXACT_PLAN_FROZEN":
        raise CompleteReserveReviewReadinessError("qualified_plan_result_state")
    if closeout.get("state") != "CLOSED_ATOMIC_REFERENCE_SUFFICIENCY_FAIL":
        raise CompleteReserveReviewReadinessError("p014_closeout_state")
    expected_ledger = config["bindings"]["p014_closeout"]["ledger_sha256"]
    if closeout.get("review_ledger", {}).get("ledger_sha256") != expected_ledger:
        raise CompleteReserveReviewReadinessError("p014_review_ledger_binding")

    training_root = training_root.expanduser().resolve()
    rows, plan_verified = _qualified_plan(
        training_root,
        config["bindings"]["qualified_plan_artifact"],
    )
    reviewed = _p014_reviewed_resources(
        training_root,
        config["bindings"]["p014_semantic_artifact"],
    )
    plan_resources = {(str(row["security_id"]), str(row["window_id"])) for row in rows}
    if not reviewed <= plan_resources:
        raise CompleteReserveReviewReadinessError("p014_reviewed_resource_not_in_plan")

    admissions, read_binding, _calendar_ref = _read_exact_plan_windows(
        training_root=training_root,
        node=node,
        rows=rows,
    )
    report_start = min(date.fromisoformat(str(row["window_start"])) for row in rows)
    report_end = max(date.fromisoformat(str(row["window_end"])) for row in rows)
    observations, fact_build_counts = build_order_observations(
        admissions,
        report_start=report_start,
        report_end=report_end,
    )
    path_values = _path_map(admissions, report_start=report_start, report_end=report_end)
    observation_keys = {(item.security_code, item.market_date) for item in observations}
    by_code: dict[str, list[Any]] = defaultdict(list)
    for attempt in admissions:
        by_code[str(attempt.row.security_code)].append(attempt)

    ledger_rows: list[dict[str, Any]] = []
    for row in rows:
        code = str(row["security_code"])
        start = date.fromisoformat(str(row["window_start"]))
        end = date.fromisoformat(str(row["window_end"]))
        source_rows = [
            attempt
            for attempt in by_code.get(code, ())
            if start <= attempt.row.market_date <= end
        ]
        if len(source_rows) != 61:
            raise CompleteReserveReviewReadinessError("exact_window_source_closure")
        closure = digest([attempt.row.member_digest for attempt in source_rows])
        reason = _readiness_reason(
            security_code=code,
            t=end,
            observation_keys=observation_keys,
            path_values=path_values,
        )
        ready = reason == "READY"
        resource = (str(row["security_id"]), str(row["window_id"]))
        already_reviewed = resource in reviewed
        if already_reviewed and not ready:
            raise CompleteReserveReviewReadinessError("p014_reviewed_resource_not_ready")
        ledger_rows.append(
            {
                "security_id": str(row["security_id"]),
                "window_id": str(row["window_id"]),
                "era": str(row["era"]),
                "board_family": str(row["board_family"]),
                "cell": str(row["cell"]),
                "review_packet_ready": ready,
                "readiness_reason": reason,
                "source_member_closure_digest": closure,
                "already_reviewed_in_p014_ledger": already_reviewed,
                "new_review_required": ready and not already_reviewed,
            }
        )

    if len(ledger_rows) != 256 or len({row["security_id"] for row in ledger_rows}) != 256:
        raise CompleteReserveReviewReadinessError("readiness_ledger_identity_count")
    ready_rows = [row for row in ledger_rows if row["review_packet_ready"]]
    ready_by_cell = Counter(str(row["cell"]) for row in ready_rows)
    total_by_cell = Counter(str(row["cell"]) for row in ledger_rows)
    if len(total_by_cell) != 8 or any(value != 32 for value in total_by_cell.values()):
        raise CompleteReserveReviewReadinessError("readiness_cell_denominator")
    continuation = config["continuation_gate"]
    passed = bool(
        len(ready_rows) >= int(continuation["minimum_total_ready"])
        and len(ready_by_cell) == 8
        and all(
            ready_by_cell[cell] >= int(continuation["minimum_ready_per_cell"])
            for cell in total_by_cell
        )
    )
    already_reviewed_ready = sum(
        row["review_packet_ready"] and row["already_reviewed_in_p014_ledger"]
        for row in ledger_rows
    )
    if already_reviewed_ready != 192:
        raise CompleteReserveReviewReadinessError("p014_reviewed_ready_count")
    new_review_required = sum(row["new_review_required"] for row in ledger_rows)
    reason_counts = Counter(str(row["readiness_reason"]) for row in ledger_rows)

    ledger = {
        "schema_version": config["output"]["ledger_schema"],
        "rows": ledger_rows,
        "row_count": len(ledger_rows),
        "ready_count": len(ready_rows),
        "unready_count": len(ledger_rows) - len(ready_rows),
        "ready_by_cell": dict(sorted(ready_by_cell.items())),
        "reason_counts": dict(sorted(reason_counts.items())),
        "already_reviewed_ready_count": already_reviewed_ready,
        "new_review_required_count": new_review_required,
        "ledger_digest": digest(ledger_rows),
        "semantic_values_retained": False,
    }
    source = {
        "schema_version": config["output"]["source_binding_schema"],
        "qualified_plan_manifest_sha256": plan_verified["manifest_sha256"],
        "qualified_plan_digest": plan_verified["plan_digest"],
        "qualified_plan_identity_digest": plan_verified["identity_digest"],
        "p014_semantic_manifest_sha256": (
            config["bindings"]["p014_semantic_artifact"]["manifest_sha256"]
        ),
        "p014_review_ledger_sha256": config["bindings"]["p014_closeout"]["ledger_sha256"],
        "market_read_binding": read_binding,
        "fact_build_counts": fact_build_counts,
        "price_or_morphology_values_persisted": False,
        "reference_opinions_read": False,
        "selection_transport_read": False,
        "candidate_output_read": False,
        "preserved_2021_market_read": False,
        "2025_read": False,
    }
    summary = {
        "schema_version": config["output"]["summary_schema"],
        "work_item": "P3-STAGE-015-A",
        "phase": "COMPLETE_RESERVE_REVIEW_READINESS_CENSUS",
        "code_revision": code_revision,
        "config_digest": digest(config),
        "row_count": len(ledger_rows),
        "ready_count": len(ready_rows),
        "unready_count": len(ledger_rows) - len(ready_rows),
        "ready_by_cell": dict(sorted(ready_by_cell.items())),
        "reason_counts": dict(sorted(reason_counts.items())),
        "already_reviewed_ready_count": already_reviewed_ready,
        "new_review_required_count": new_review_required,
        "ledger_digest": ledger["ledger_digest"],
        "continuation_pass": passed,
        "all_ready_rows_in_scope_for_next_freeze": True,
        "semantic_values_retained": False,
        "reference_opinions_read": False,
        "selection_transport_read": False,
        "candidate_output_read": False,
        "preserved_2021_market_read": False,
        "2025_read": False,
    }

    run_id = "stage-complete-reserve-review-readiness-" + datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    run_root = output_root.expanduser().resolve() / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    payloads = {
        "review-readiness.json": canonical(ledger),
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
            "semantic_values_retained": False,
            "reference_opinions_read": False,
            "selection_transport_read": False,
            "candidate_output_read": False,
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
        raise CompleteReserveReviewReadinessError("artifact_ready_digest")
    if (
        manifest.get("schema_version")
        != "quanthus.stage-complete-reserve-review-readiness-artifact.v1"
    ):
        raise CompleteReserveReviewReadinessError("artifact_schema")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise CompleteReserveReviewReadinessError("artifact_code_revision")
    files = manifest.get("files")
    expected = {"review-readiness.json", "source-binding.json", "summary.json"}
    if not isinstance(files, Mapping) or set(files) != expected:
        raise CompleteReserveReviewReadinessError("artifact_files")
    payloads: dict[str, dict[str, Any]] = {}
    for name in expected:
        raw = (root / name).read_bytes()
        if _sha_bytes(raw) != files[name]:
            raise CompleteReserveReviewReadinessError(f"artifact_file_digest:{name}")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise CompleteReserveReviewReadinessError(f"artifact_shape:{name}")
        payloads[name] = value
    ledger = payloads["review-readiness.json"]
    rows = ledger.get("rows")
    if not isinstance(rows, list) or len(rows) != 256:
        raise CompleteReserveReviewReadinessError("artifact_row_count")
    allowed = {
        "security_id",
        "window_id",
        "era",
        "board_family",
        "cell",
        "review_packet_ready",
        "readiness_reason",
        "source_member_closure_digest",
        "already_reviewed_in_p014_ledger",
        "new_review_required",
    }
    if any(not isinstance(row, Mapping) or set(row) != allowed for row in rows):
        raise CompleteReserveReviewReadinessError("artifact_retention_fields")
    if ledger.get("semantic_values_retained") is not False:
        raise CompleteReserveReviewReadinessError("artifact_semantic_value_ceiling")
    if ledger.get("ledger_digest") != digest(rows):
        raise CompleteReserveReviewReadinessError("artifact_ledger_digest")
    summary = payloads["summary.json"]
    ready_count = sum(row["review_packet_ready"] is True for row in rows)
    new_count = sum(row["new_review_required"] is True for row in rows)
    if (
        summary.get("row_count") != 256
        or summary.get("ready_count") != ready_count
        or summary.get("new_review_required_count") != new_count
        or summary.get("already_reviewed_ready_count") != 192
        or summary.get("ledger_digest") != ledger.get("ledger_digest")
    ):
        raise CompleteReserveReviewReadinessError("artifact_summary_binding")
    for key in (
        "semantic_values_retained",
        "reference_opinions_read",
        "selection_transport_read",
        "candidate_output_read",
        "preserved_2021_market_read",
        "2025_read",
    ):
        if summary.get(key) is not False:
            raise CompleteReserveReviewReadinessError(f"artifact_authority:{key}")
    return {
        "verified": True,
        "run_id": manifest.get("run_id"),
        "manifest_sha256": ready,
        "file_count": len(files),
        "row_count": 256,
        "ready_count": ready_count,
        "unready_count": 256 - ready_count,
        "ready_by_cell": summary.get("ready_by_cell"),
        "reason_counts": summary.get("reason_counts"),
        "already_reviewed_ready_count": 192,
        "new_review_required_count": new_count,
        "ledger_digest": ledger.get("ledger_digest"),
        "continuation_pass": summary.get("continuation_pass"),
        "semantic_values_retained": False,
        "reference_opinions_read": False,
        "selection_transport_read": False,
        "candidate_output_read": False,
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
        result = run_census(
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

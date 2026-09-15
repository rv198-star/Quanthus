"""P3-STAGE-024 cross-era clustered-identity supplemental admission.

The runner binds the immutable P023 no-pass artifact and may read only fallback candidates that P023
skipped solely because the identity was already realized elsewhere. It fills only the early-STAR
technical deficit, preserves one identity per Era x Board cell, and requires non-overlapping exact
windows for any cross-cell repeated identity. Repeated identities are recorded as clusters, not as
independent evidence. No Stage1/12 semantics, reviewer opinions, thresholds, or protected partitions
are read.
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
from quanthus.stage_learning.development_resource_admission_screen import _screen_candidates
from quanthus.stage_learning.realized_identity_admission import (
    verify_artifact as verify_p023_artifact,
)
from quanthus.structure_v2_inputs import canonical, digest

DEFAULT_CONFIG = "config/stage-learning/stage-clustered-identity-admission.v1.json"
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")
_P023_CONFLICT = "SKIPPED_REALIZED_IDENTITY_CONFLICT"


class ClusteredIdentityAdmissionError(ValueError):
    """The frozen P024 clustered-development admission contract was violated."""


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _file_sha(path: Path) -> str:
    try:
        return _sha(path.read_bytes())
    except OSError as exc:
        raise ClusteredIdentityAdmissionError(f"cannot_hash:{path}") from exc


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClusteredIdentityAdmissionError(f"cannot_read:{label}") from exc
    if not isinstance(value, dict):
        raise ClusteredIdentityAdmissionError(f"object_required:{label}")
    return value


def _bound_json(root: Path, binding: Mapping[str, Any], label: str) -> dict[str, Any]:
    path = root / str(binding.get("path") or "")
    expected = str(binding.get("sha256") or "")
    if not _SHA.fullmatch(expected) or _file_sha(path) != expected:
        raise ClusteredIdentityAdmissionError(f"repo_binding:{label}")
    return _object(path, label)


def _day(value: object) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ClusteredIdentityAdmissionError("invalid_date") from exc


def _windows_non_overlapping(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_start = _day(left["window_start"])
    left_end = _day(left["window_end"])
    right_start = _day(right["window_start"])
    right_end = _day(right["window_end"])
    if left_end < left_start or right_end < right_start:
        raise ClusteredIdentityAdmissionError("invalid_window_order")
    return left_end < right_start or right_end < left_start


def load_config(path: Path | None = None) -> dict[str, Any]:
    root = find_project_root()
    value = _object(path or root / DEFAULT_CONFIG, "p024_config")
    if (
        value.get("schema_version") != "quanthus.stage-clustered-identity-admission.v1"
        or value.get("work_item") != "P3-STAGE-024"
        or value.get("state")
        != "PREREGISTERED_CROSS_ERA_CLUSTERED_IDENTITY_SUPPLEMENT_BEFORE_NEW_READINESS_READ"
    ):
        raise ClusteredIdentityAdmissionError("config_identity")
    start = value.get("starting_panel")
    supplement = value.get("supplement_contract")
    cluster = value.get("cluster_contract")
    gate = value.get("pass_gate")
    failure = value.get("failure_policy")
    runtime = value.get("runtime")
    output = value.get("output")
    authority = value.get("authority")
    if not all(
        isinstance(item, Mapping)
        for item in (start, supplement, cluster, gate, failure, runtime, output, authority)
    ):
        raise ClusteredIdentityAdmissionError("config_sections")
    if (
        start.get("row_count") != 179
        or start.get("unique_identity_count") != 179
        or start.get("global_identity_unique") is not True
        or supplement.get("deficit_cell") != "STAR_PRE_CHINEXT_20PCT/STAR"
        or supplement.get("starting_cell_count") != 11
        or supplement.get("target_cell_count") != 24
        or supplement.get("maximum_activated_supplements") != 13
        or supplement.get("eligible_source")
        != "P023_FALLBACK_ATTEMPT_SKIPPED_REALIZED_IDENTITY_CONFLICT_ONLY"
        or supplement.get("require_prior_market_read_false") is not True
        or supplement.get("completed_p023_readiness_must_not_rerun") is not True
        or supplement.get("same_cell_only") is not True
        or supplement.get("within_cell_identity_unique") is not True
        or supplement.get("cross_cell_same_identity_allowed") is not True
        or supplement.get("cross_cell_repeat_requires_non_overlapping_exact_windows") is not True
        or supplement.get("post_result_candidate_append") is not False
        or supplement.get("cross_cell_fill") is not False
        or cluster.get("target_total_rows") != 192
        or cluster.get("target_rows_per_cell") != 24
        or cluster.get("cell_count") != 8
        or cluster.get("unique_identity_within_each_cell") is not True
        or cluster.get("cross_cell_repeated_identity_is_clustered") is not True
        or cluster.get("cluster_windows_pairwise_non_overlapping") is not True
        or cluster.get("repeated_rows_must_not_inflate_independent_evidence") is not True
        or cluster.get("preserved_evaluation_identity_global_exclusion_unchanged") is not True
        or gate.get("required_rows_per_cell") != 24
        or gate.get("required_total_rows") != 192
        or gate.get("pass_allows_only") != "SEPARATE_P020_C_PREREGISTRATION"
        or failure.get("automatic_retry") is not False
        or failure.get("automatic_p025_resource_repair") is not False
        or failure.get("on_failure") != "FOUR_PARTY_PORTFOLIO_REVIEW_BEFORE_ANY_SUCCESSOR"
        or runtime.get("maximum_jobs") != 1
        or runtime.get("automatic_retries") != 0
    ):
        raise ClusteredIdentityAdmissionError("config_budget")
    if (
        authority.get("input_readiness_market_read_for_previously_conflict_skipped_candidates")
        is not True
    ):
        raise ClusteredIdentityAdmissionError("input_read_authority")
    for key in (
        "raw_ohlc_publish_or_retain",
        "structure_fact_build",
        "stage1_12_semantic_read",
        "threshold_or_candidate_read",
        "reference_opinion_read",
        "p020_c_semantic_review",
        "preserved_2021_read",
        "2022_2024_read",
        "2025_read",
        "daily_or_strategy",
        "trading",
    ):
        if authority.get(key) is not False:
            raise ClusteredIdentityAdmissionError(f"authority:{key}")
    return value


def _load_p023_artifact(
    *, training_root: Path, config: Mapping[str, Any], root: Path
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    result = _bound_json(root, config["bindings"]["p023_result"], "p023_result")
    binding = config["bindings"]["p023_artifact"]
    artifact_root = (training_root / str(binding["artifact_relative_path"])).resolve()
    verified = verify_p023_artifact(
        artifact_root,
        code_revision=str(binding["code_revision"]),
    )
    if (
        verified.get("manifest_sha256") != binding.get("manifest_sha256")
        or verified.get("realized_plan_digest") != binding.get("realized_plan_digest")
        or verified.get("fallback_reservoir_digest") != binding.get("fallback_reservoir_digest")
        or result.get("job", {}).get("manifest_sha256") != binding.get("manifest_sha256")
        or result.get("realized_plan", {}).get("plan_digest") != binding.get("realized_plan_digest")
        or result.get("fallback", {}).get("reservoir_digest")
        != binding.get("fallback_reservoir_digest")
    ):
        raise ClusteredIdentityAdmissionError("p023_artifact_binding")
    payloads = {
        name: _object(artifact_root / name, f"p023_{name}")
        for name in (
            "fallback-reservoir.json",
            "fallback-admission.json",
            "realized-plan.json",
            "source-binding.json",
        )
    }
    realized_rows = payloads["realized-plan.json"].get("rows")
    if not isinstance(realized_rows, list) or len(realized_rows) != 179:
        raise ClusteredIdentityAdmissionError("p023_realized_rows")
    if len({str(row["security_id"]) for row in realized_rows}) != 179:
        raise ClusteredIdentityAdmissionError("p023_realized_identity_count")
    return payloads, {
        "p023_job_id": binding["job_id"],
        "p023_manifest_sha256": verified["manifest_sha256"],
        "p023_realized_plan_digest": verified["realized_plan_digest"],
        "p023_fallback_reservoir_digest": verified["fallback_reservoir_digest"],
    }


def _eligible_candidates(
    *,
    reservoir_rows: Sequence[Mapping[str, Any]],
    fallback_attempts: Sequence[Mapping[str, Any]],
    deficit_cell: str,
) -> list[dict[str, Any]]:
    reservoir = {
        (str(row["cell"]), str(row["security_id"]), str(row["window_id"])): row
        for row in reservoir_rows
    }
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for attempt in fallback_attempts:
        if (
            str(attempt.get("cell")) != deficit_cell
            or attempt.get("reason") != _P023_CONFLICT
            or attempt.get("market_read") is not False
            or attempt.get("qualified") is not False
            or attempt.get("activated") is not False
            or attempt.get("source_binding_digest") is not None
        ):
            continue
        key = (deficit_cell, str(attempt["security_id"]), str(attempt["window_id"]))
        source = reservoir.get(key)
        if source is None:
            raise ClusteredIdentityAdmissionError("conflict_candidate_missing_from_reservoir")
        if key in seen:
            raise ClusteredIdentityAdmissionError("duplicate_conflict_candidate")
        seen.add(key)
        output.append(
            {
                **dict(source),
                "p023_reason": _P023_CONFLICT,
                "p023_market_read": False,
            }
        )
    return sorted(
        output,
        key=lambda row: (int(row["identity_rank"]), int(row["window_rank"])),
    )


def _realized_row(source: Mapping[str, Any], readiness: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "security_id": str(source["security_id"]),
        "security_code": str(source["security_code"]),
        "listing_id": str(source["listing_id"]),
        "era": str(source["era"]),
        "board_family": str(source["board_family"]),
        "board_code": str(source.get("board_code") or source["board_family"]),
        "venue_id": str(source.get("venue_id") or ""),
        "cell": str(source["cell"]),
        "window_id": str(source["window_id"]),
        "window_start": str(source["window_start"]),
        "window_end": str(source["window_end"]),
        "role": "clustered_supplemental_fallback",
        "cluster_id": str(source["security_id"]),
        "admission_reason": str(readiness["reason"]),
        "admission_source_binding_digest": str(readiness["source_binding_digest"]),
    }


def _cluster_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_identity: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_identity[str(row["security_id"])].append(row)
    clusters: list[dict[str, Any]] = []
    for identity in sorted(by_identity):
        members = sorted(
            by_identity[identity],
            key=lambda row: (str(row["window_start"]), str(row["cell"]), str(row["window_id"])),
        )
        for index, left in enumerate(members):
            for right in members[index + 1 :]:
                if not _windows_non_overlapping(left, right):
                    raise ClusteredIdentityAdmissionError("cluster_window_overlap")
        clusters.append(
            {
                "cluster_id": identity,
                "row_count": len(members),
                "cells": [str(row["cell"]) for row in members],
                "windows": [
                    {
                        "window_id": str(row["window_id"]),
                        "window_start": str(row["window_start"]),
                        "window_end": str(row["window_end"]),
                    }
                    for row in members
                ],
            }
        )
    repeated = [cluster for cluster in clusters if cluster["row_count"] > 1]
    return {
        "clusters": clusters,
        "unique_cluster_count": len(clusters),
        "repeated_identity_cluster_count": len(repeated),
        "repeated_identity_row_count": sum(int(cluster["row_count"]) for cluster in repeated),
        "maximum_cluster_size": max((int(cluster["row_count"]) for cluster in clusters), default=0),
        "independent_identity_evidence_count": len(clusters),
    }


def _supplement(
    *,
    candidates: Sequence[Mapping[str, Any]],
    realized_rows: Sequence[Mapping[str, Any]],
    target_cell: str,
    target_count: int,
    training_root: Path,
    node: Mapping[str, Any],
    spec_digest: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    realized = [dict(row) for row in realized_rows]
    cell_ids = {str(row["security_id"]) for row in realized if str(row["cell"]) == target_cell}
    cluster_rows: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in realized:
        cluster_rows[str(row["security_id"])].append(row)
    deficit = target_count - sum(str(row["cell"]) == target_cell for row in realized)
    if deficit < 0:
        raise ClusteredIdentityAdmissionError("negative_starting_deficit")
    attempts: list[dict[str, Any]] = []
    activated: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    for candidate in candidates:
        if deficit <= 0:
            break
        if str(candidate["cell"]) != target_cell:
            raise ClusteredIdentityAdmissionError("candidate_cross_cell")
        if (
            candidate.get("p023_reason") != _P023_CONFLICT
            or candidate.get("p023_market_read") is not False
        ):
            raise ClusteredIdentityAdmissionError("candidate_not_p023_unread_conflict")
        identity = str(candidate["security_id"])
        base_attempt = {
            "cell": target_cell,
            "security_id": identity,
            "window_id": str(candidate["window_id"]),
            "identity_rank": int(candidate["identity_rank"]),
            "window_rank": int(candidate["window_rank"]),
            "p023_reason": _P023_CONFLICT,
            "p023_market_read": False,
        }
        if identity in cell_ids:
            attempts.append(
                {
                    **base_attempt,
                    "market_read": False,
                    "qualified": False,
                    "activated": False,
                    "reason": "SKIPPED_SAME_CELL_IDENTITY",
                    "source_binding_digest": None,
                }
            )
            continue
        if any(not _windows_non_overlapping(candidate, row) for row in cluster_rows[identity]):
            attempts.append(
                {
                    **base_attempt,
                    "market_read": False,
                    "qualified": False,
                    "activated": False,
                    "reason": "SKIPPED_CLUSTER_WINDOW_OVERLAP",
                    "source_binding_digest": None,
                }
            )
            continue
        screened, read_receipts = _screen_candidates(
            candidates=[candidate],
            training_root=training_root,
            node=node,
            spec_digest=spec_digest,
        )
        receipts.extend(dict(row) for row in read_receipts)
        if len(screened) != 1:
            raise ClusteredIdentityAdmissionError("supplement_screen_shape")
        readiness = screened[0]
        attempt = {
            **base_attempt,
            "market_read": True,
            "qualified": readiness["qualified"] is True,
            "activated": False,
            "reason": str(readiness["reason"]),
            "source_binding_digest": str(readiness["source_binding_digest"]),
        }
        if readiness["qualified"] is True:
            row = _realized_row(candidate, readiness)
            realized.append(row)
            activated.append(row)
            cell_ids.add(identity)
            cluster_rows[identity].append(row)
            deficit -= 1
            attempt["activated"] = True
        attempts.append(attempt)
    return attempts, activated, receipts, realized


def run_admission(
    *, config_path: Path, training_root: Path, output_root: Path, code_revision: str
) -> dict[str, Any]:
    if not _REVISION.fullmatch(code_revision):
        raise ClusteredIdentityAdmissionError("exact_code_revision_required")
    root = find_project_root()
    config = load_config(config_path)
    model = _bound_json(root, config["bindings"]["resource_model"], "resource_model")
    node = _bound_json(root, config["bindings"]["training_node"], "training_node")
    resource_unit = model.get("selected_model", {}).get("resource_unit")
    if resource_unit != [
        "economic_identity",
        "non_overlapping_time_window",
        "listing_board_at_time",
        "evidence_role",
    ]:
        raise ClusteredIdentityAdmissionError("resource_model_unit")
    training_root = training_root.expanduser().resolve()
    payloads, parent_binding = _load_p023_artifact(
        training_root=training_root,
        config=config,
        root=root,
    )
    reservoir_rows = payloads["fallback-reservoir.json"].get("rows")
    fallback_attempts = payloads["fallback-admission.json"].get("attempts")
    realized_rows = payloads["realized-plan.json"].get("rows")
    if not all(
        isinstance(rows, list) for rows in (reservoir_rows, fallback_attempts, realized_rows)
    ):
        raise ClusteredIdentityAdmissionError("p023_artifact_rows")
    supplement = config["supplement_contract"]
    target_cell = str(supplement["deficit_cell"])
    starting_counts = Counter(str(row["cell"]) for row in realized_rows)
    if (
        len(realized_rows) != config["starting_panel"]["row_count"]
        or starting_counts.get(target_cell, 0) != supplement["starting_cell_count"]
        or any(
            starting_counts.get(cell, 0) != int(count)
            for cell, count in config["starting_panel"]["cell_counts"].items()
        )
    ):
        raise ClusteredIdentityAdmissionError("p023_starting_panel_shape")
    candidates = _eligible_candidates(
        reservoir_rows=reservoir_rows,
        fallback_attempts=fallback_attempts,
        deficit_cell=target_cell,
    )
    config_digest = digest(config)
    attempts, activated, receipts, final_rows = _supplement(
        candidates=candidates,
        realized_rows=realized_rows,
        target_cell=target_cell,
        target_count=int(supplement["target_cell_count"]),
        training_root=training_root,
        node=node,
        spec_digest=config_digest,
    )
    if len(activated) > int(supplement["maximum_activated_supplements"]):
        raise ClusteredIdentityAdmissionError("supplement_activation_budget")
    final_counts = Counter(str(row["cell"]) for row in final_rows)
    for cell in final_counts:
        ids = [str(row["security_id"]) for row in final_rows if str(row["cell"]) == cell]
        if len(ids) != len(set(ids)):
            raise ClusteredIdentityAdmissionError("within_cell_identity_duplicate")
    cluster = _cluster_summary(final_rows)
    pass_gate = config["pass_gate"]
    final_pass = (
        len(final_rows) == int(pass_gate["required_total_rows"])
        and len(final_counts) == int(config["cluster_contract"]["cell_count"])
        and all(
            value == int(pass_gate["required_rows_per_cell"]) for value in final_counts.values()
        )
    )
    disposition = (
        str(pass_gate["pass_disposition"])
        if final_pass
        else str(config["failure_policy"]["fail_disposition"])
    )
    final_rows_sorted = sorted(
        (dict(row) for row in final_rows),
        key=lambda row: (str(row["cell"]), str(row["security_id"]), str(row["window_id"])),
    )
    plan_payload = {
        "schema_version": config["output"]["clustered_plan_schema"],
        "rows": final_rows_sorted,
        "row_count": len(final_rows_sorted),
        "unique_cluster_count": cluster["unique_cluster_count"],
        "cell_counts": dict(sorted(final_counts.items())),
        "within_cell_identity_unique": True,
        "cluster_windows_pairwise_non_overlapping": True,
        "all_rows_admission_ready": True,
        "plan_digest": digest(final_rows_sorted),
    }
    supplement_payload = {
        "schema_version": config["output"]["supplemental_admission_schema"],
        "eligible_candidate_count": len(candidates),
        "attempts": attempts,
        "attempt_count": len(attempts),
        "market_read_attempt_count": sum(row["market_read"] is True for row in attempts),
        "qualified_read_count": sum(row["qualified"] is True for row in attempts),
        "activated_rows": activated,
        "activated_count": len(activated),
        "readiness_failure_count": sum(
            row["market_read"] is True and row["qualified"] is not True for row in attempts
        ),
        "same_cell_identity_skip_count": sum(
            row["reason"] == "SKIPPED_SAME_CELL_IDENTITY" for row in attempts
        ),
        "cluster_overlap_skip_count": sum(
            row["reason"] == "SKIPPED_CLUSTER_WINDOW_OVERLAP" for row in attempts
        ),
        "raw_ohlc_retained": False,
    }
    cluster_payload = {
        "schema_version": config["output"]["cluster_summary_schema"],
        **cluster,
        "row_count": len(final_rows_sorted),
        "repeated_rows_not_independent": True,
        "preserved_evaluation_identity_global_exclusion_unchanged": True,
    }
    parent_source = payloads["source-binding.json"]
    source_payload = {
        "schema_version": config["output"]["source_binding_schema"],
        **parent_binding,
        "p023_source_binding_digest": digest(parent_source),
        "p024_config_digest": config_digest,
        "eligible_candidate_digest": digest(candidates),
        "supplemental_read_receipts_digest": digest(receipts),
        "new_read_candidate_count": supplement_payload["market_read_attempt_count"],
        "completed_p023_readiness_rerun_count": 0,
        "stage1_12_semantic_read": False,
        "preserved_2021_read": False,
        "2022_2024_read": False,
        "2025_read": False,
    }
    summary = {
        "schema_version": config["output"]["summary_schema"],
        "work_item": "P3-STAGE-024",
        "phase": "CROSS_ERA_CLUSTERED_IDENTITY_SUPPLEMENTAL_ADMISSION",
        "code_revision": code_revision,
        "config_digest": config_digest,
        "disposition": disposition,
        "starting_row_count": len(realized_rows),
        "eligible_conflict_skipped_candidate_count": len(candidates),
        "supplemental_market_read_attempt_count": supplement_payload["market_read_attempt_count"],
        "supplemental_readiness_failure_count": supplement_payload["readiness_failure_count"],
        "activated_supplement_count": len(activated),
        "final_row_count": len(final_rows_sorted),
        "final_unique_cluster_count": cluster["unique_cluster_count"],
        "final_repeated_identity_cluster_count": cluster["repeated_identity_cluster_count"],
        "final_independent_identity_evidence_count": cluster["independent_identity_evidence_count"],
        "final_cell_counts": dict(sorted(final_counts.items())),
        "clustered_plan_digest": plan_payload["plan_digest"],
        "source_binding_digest": digest(source_payload),
        "ready_for_separate_p020c_preregistration": final_pass,
        "completed_p023_readiness_rerun_count": 0,
        "repeated_rows_not_independent": True,
        "raw_ohlc_published_or_retained": False,
        "structure_fact_build": False,
        "stage1_12_semantic_read": False,
        "threshold_or_candidate_read": False,
        "reference_opinion_read": False,
        "p020_c_semantic_review_authorized": False,
        "preserved_2021_read": False,
        "2022_2024_read": False,
        "2025_read": False,
    }
    run_id = "stage-clustered-identity-admission-" + datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    run_root = output_root.expanduser().resolve() / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    artifact_payloads = {
        "supplemental-admission.json": canonical(supplement_payload),
        "clustered-plan.json": canonical(plan_payload),
        "cluster-summary.json": canonical(cluster_payload),
        "source-binding.json": canonical(source_payload),
        "summary.json": canonical(summary),
    }
    for name, raw in artifact_payloads.items():
        (run_root / name).write_bytes(raw)
    manifest = {
        "schema_version": config["output"]["artifact_schema"],
        "run_id": run_id,
        "code_revision": code_revision,
        "config_digest": config_digest,
        "files": {name: _sha(raw) for name, raw in artifact_payloads.items()},
        "authority": {
            "raw_ohlc_published_or_retained": False,
            "structure_fact_build": False,
            "stage1_12_semantic_read": False,
            "threshold_or_candidate_read": False,
            "reference_opinion_read": False,
            "p020_c_semantic_review_authorized": False,
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
        raise ClusteredIdentityAdmissionError("artifact_ready_digest")
    if manifest.get("schema_version") != "quanthus.stage-clustered-identity-admission-artifact.v1":
        raise ClusteredIdentityAdmissionError("artifact_schema")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise ClusteredIdentityAdmissionError("artifact_code_revision")
    files = manifest.get("files")
    expected = {
        "supplemental-admission.json",
        "clustered-plan.json",
        "cluster-summary.json",
        "source-binding.json",
        "summary.json",
    }
    if not isinstance(files, Mapping) or set(files) != expected:
        raise ClusteredIdentityAdmissionError("artifact_files")
    payloads: dict[str, dict[str, Any]] = {}
    for name in expected:
        raw = (root / name).read_bytes()
        if _sha(raw) != files[name]:
            raise ClusteredIdentityAdmissionError(f"artifact_file_digest:{name}")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ClusteredIdentityAdmissionError(f"artifact_shape:{name}")
        payloads[name] = value
    supplement = payloads["supplemental-admission.json"]
    plan = payloads["clustered-plan.json"]
    clusters = payloads["cluster-summary.json"]
    source = payloads["source-binding.json"]
    summary = payloads["summary.json"]
    rows = plan.get("rows")
    attempts = supplement.get("attempts")
    cluster_rows = clusters.get("clusters")
    if (
        not isinstance(rows, list)
        or not isinstance(attempts, list)
        or not isinstance(cluster_rows, list)
    ):
        raise ClusteredIdentityAdmissionError("artifact_rows")
    if plan.get("plan_digest") != digest(rows):
        raise ClusteredIdentityAdmissionError("plan_digest")
    if summary.get("clustered_plan_digest") != plan.get("plan_digest"):
        raise ClusteredIdentityAdmissionError("summary_plan_digest")
    if summary.get("source_binding_digest") != digest(source):
        raise ClusteredIdentityAdmissionError("summary_source_digest")
    if source.get("completed_p023_readiness_rerun_count") != 0:
        raise ClusteredIdentityAdmissionError("p023_readiness_rerun")
    for attempt in attempts:
        if attempt.get("market_read") is True and (
            attempt.get("p023_reason") != _P023_CONFLICT
            or attempt.get("p023_market_read") is not False
        ):
            raise ClusteredIdentityAdmissionError("unauthorized_new_read")
    counts = Counter(str(row["cell"]) for row in rows)
    for cell in counts:
        ids = [str(row["security_id"]) for row in rows if str(row["cell"]) == cell]
        if len(ids) != len(set(ids)):
            raise ClusteredIdentityAdmissionError("artifact_within_cell_identity_duplicate")
    recomputed_cluster = _cluster_summary(rows)
    if (
        clusters.get("unique_cluster_count") != recomputed_cluster["unique_cluster_count"]
        or clusters.get("repeated_identity_cluster_count")
        != recomputed_cluster["repeated_identity_cluster_count"]
        or clusters.get("independent_identity_evidence_count")
        != recomputed_cluster["independent_identity_evidence_count"]
        or clusters.get("repeated_rows_not_independent") is not True
    ):
        raise ClusteredIdentityAdmissionError("cluster_summary_mismatch")
    disposition = summary.get("disposition")
    if disposition == "PASS_CLUSTERED_ADMISSION_READY_PLAN_FROZEN":
        if len(rows) != 192 or len(counts) != 8 or any(value != 24 for value in counts.values()):
            raise ClusteredIdentityAdmissionError("pass_plan_shape")
        if summary.get("ready_for_separate_p020c_preregistration") is not True:
            raise ClusteredIdentityAdmissionError("pass_next_gate")
    elif disposition == "CLOSED_CLUSTERED_ADMISSION_CAPACITY_SHORTFALL":
        if summary.get("ready_for_separate_p020c_preregistration") is not False:
            raise ClusteredIdentityAdmissionError("fail_next_gate")
    else:
        raise ClusteredIdentityAdmissionError("unknown_disposition")
    if summary.get("repeated_rows_not_independent") is not True:
        raise ClusteredIdentityAdmissionError("independence_claim")
    for key in (
        "raw_ohlc_published_or_retained",
        "structure_fact_build",
        "stage1_12_semantic_read",
        "threshold_or_candidate_read",
        "reference_opinion_read",
        "p020_c_semantic_review_authorized",
        "preserved_2021_read",
        "2022_2024_read",
        "2025_read",
    ):
        if summary.get(key) is not False:
            raise ClusteredIdentityAdmissionError(f"summary_authority:{key}")
    return {
        "verified": True,
        "run_id": manifest.get("run_id"),
        "manifest_sha256": ready,
        "file_count": len(files),
        "disposition": disposition,
        "starting_row_count": summary.get("starting_row_count"),
        "eligible_conflict_skipped_candidate_count": summary.get(
            "eligible_conflict_skipped_candidate_count"
        ),
        "supplemental_market_read_attempt_count": summary.get(
            "supplemental_market_read_attempt_count"
        ),
        "supplemental_readiness_failure_count": summary.get("supplemental_readiness_failure_count"),
        "activated_supplement_count": summary.get("activated_supplement_count"),
        "final_row_count": summary.get("final_row_count"),
        "final_unique_cluster_count": summary.get("final_unique_cluster_count"),
        "final_repeated_identity_cluster_count": summary.get(
            "final_repeated_identity_cluster_count"
        ),
        "final_independent_identity_evidence_count": summary.get(
            "final_independent_identity_evidence_count"
        ),
        "final_cell_counts": summary.get("final_cell_counts"),
        "clustered_plan_digest": summary.get("clustered_plan_digest"),
        "ready_for_separate_p020c_preregistration": summary.get(
            "ready_for_separate_p020c_preregistration"
        ),
        "completed_p023_readiness_rerun_count": 0,
        "repeated_rows_not_independent": True,
        "stage1_12_semantic_read": False,
        "p020_c_semantic_review_authorized": False,
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
        result = run_admission(
            config_path=args.config,
            training_root=args.training_root,
            output_root=args.output_root,
            code_revision=args.code_revision,
        )
    else:
        result = verify_artifact(args.artifact, code_revision=args.code_revision)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

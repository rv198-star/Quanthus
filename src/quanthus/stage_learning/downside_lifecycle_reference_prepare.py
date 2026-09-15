"""P3-STAGE-020-C candidate-blind T-only review-packet preparation.

This runner binds the exact immutable P024 clustered development panel and rereads only those exact
admission-ready windows to reconstruct the accepted Trend Evolution Fact Family at T. It publishes an
opaque reviewer surface plus a separate internal case map. It does not collect opinions, expose
security identities to reviewers, read candidate/threshold/future information, or open protected
partitions.
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
from quanthus.stage_learning.clustered_identity_admission import (
    verify_artifact as verify_p024_artifact,
)
from quanthus.stage_learning.source import DevelopmentReadScope, read_development_admissions
from quanthus.structure_trend_evolution import CONCEPTS, TrendEvolutionError, build_trend_evolution_facts
from quanthus.structure_v2_inputs import canonical, digest

DEFAULT_CONFIG = "config/stage-learning/stage-downside-lifecycle-blind-reference.v1.json"
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")


class DownsideLifecycleReferencePrepareError(ValueError):
    """Raised when P020-C packet preparation violates the frozen preregistration."""


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _file_sha(path: Path) -> str:
    try:
        return _sha(path.read_bytes())
    except OSError as exc:
        raise DownsideLifecycleReferencePrepareError(f"cannot_hash:{path}") from exc


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DownsideLifecycleReferencePrepareError(f"cannot_read:{label}") from exc
    if not isinstance(value, dict):
        raise DownsideLifecycleReferencePrepareError(f"object_required:{label}")
    return value


def _bound_json(root: Path, binding: Mapping[str, Any], label: str) -> dict[str, Any]:
    path = root / str(binding.get("path") or "")
    expected = str(binding.get("sha256") or "")
    if not _SHA.fullmatch(expected) or _file_sha(path) != expected:
        raise DownsideLifecycleReferencePrepareError(f"repo_binding:{label}")
    return _object(path, label)


def _day(value: object) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise DownsideLifecycleReferencePrepareError("invalid_date") from exc


def _case_id(plan_digest: str, row: Mapping[str, Any]) -> str:
    return "case_" + hashlib.sha256(
        "|".join(
            (
                plan_digest,
                str(row["security_id"]),
                str(row["listing_id"]),
                str(row["window_id"]),
                str(row["cell"]),
            )
        ).encode("utf-8")
    ).hexdigest()[:32]


def _cluster_id(security_id: str) -> str:
    return "cluster_" + hashlib.sha256(security_id.encode("utf-8")).hexdigest()[:32]


def load_config(path: Path | None = None) -> dict[str, Any]:
    root = find_project_root()
    value = _object(path or root / DEFAULT_CONFIG, "p020c_config")
    if (
        value.get("schema_version") != "quanthus.stage-downside-lifecycle-blind-reference.v1"
        or value.get("work_item") != "P3-STAGE-020-C"
        or value.get("state")
        != "PREREGISTERED_CAPACITY_LIMITED_BLIND_REFERENCE_BEFORE_SEMANTIC_READ"
    ):
        raise DownsideLifecycleReferencePrepareError("config_identity")
    panel = value.get("resource_panel")
    packet = value.get("packet_contract")
    review = value.get("review_protocol")
    resolution = value.get("resolution_contract")
    gate = value.get("reference_sufficiency_gate")
    runtime = value.get("runtime")
    authority = value.get("authority")
    if not all(
        isinstance(item, Mapping)
        for item in (panel, packet, review, resolution, gate, runtime, authority)
    ):
        raise DownsideLifecycleReferencePrepareError("config_sections")
    if (
        panel.get("row_count") != 187
        or panel.get("unique_identity_cluster_count") != 179
        or panel.get("repeated_identity_cluster_count") != 8
        or panel.get("independent_identity_evidence_count") != 179
        or panel.get("repeated_rows_not_independent") is not True
        or panel.get("replacement_or_resource_expansion") is not False
        or panel.get("additional_admission_read") is not False
        or packet.get("attempt_every_panel_row") is not True
        or packet.get("security_identity_visible_to_reviewer") is not False
        or packet.get("identity_cluster_visible_to_reviewer") is not False
        or packet.get("future_rows_visible") is not False
        or packet.get("candidate_output_visible") is not False
        or packet.get("threshold_values_visible") is not False
        or packet.get("packet_failure_disposition") != "UNAVAILABLE_NO_REPLACEMENT"
        or tuple(packet.get("allowed_fact_concepts") or ()) != tuple(CONCEPTS)
        or review.get("roles") != ["short_term", "fund"]
        or review.get("same_origin_simulation") is not True
        or review.get("roles_isolated_from_each_other") is not True
        or review.get("opinion_collection_before_packet_freeze") is not False
        or resolution.get("unknown_is_not_negative") is not True
        or gate.get("independence_unit") != "economic_identity_cluster"
        or gate.get("minimum_stage12_definite_clusters") != 80
        or gate.get("minimum_stage12_definite_positive_clusters") != 20
        or gate.get("minimum_stage12_definite_negative_clusters") != 20
        or gate.get("minimum_stage1_applicable_definite_clusters") != 40
        or gate.get("minimum_stage1_definite_positive_clusters") != 20
        or gate.get("minimum_stage1_definite_negative_clusters") != 20
        or runtime.get("maximum_packet_jobs") != 1
        or runtime.get("automatic_packet_retries") != 0
    ):
        raise DownsideLifecycleReferencePrepareError("config_budget")
    if authority.get("exact_panel_semantic_packet_market_read") is not True:
        raise DownsideLifecycleReferencePrepareError("packet_market_read_authority")
    for key in (
        "raw_ohlc_publish_or_retain",
        "review_opinion_collection_before_packet_freeze",
        "candidate_or_threshold_read",
        "candidate_scoring",
        "primary_stage_compression",
        "labelcard_registration",
        "preserved_2021_read",
        "2022_2024_read",
        "2025_read",
        "daily_or_strategy",
        "trading",
    ):
        if authority.get(key) is not False:
            raise DownsideLifecycleReferencePrepareError(f"authority:{key}")
    design_binding = value["bindings"].get("trend_fact_design")
    if not isinstance(design_binding, Mapping):
        raise DownsideLifecycleReferencePrepareError("trend_design_binding")
    design_path = root / str(design_binding.get("path") or "")
    design_sha = str(design_binding.get("sha256") or "")
    if not _SHA.fullmatch(design_sha) or _file_sha(design_path) != design_sha:
        raise DownsideLifecycleReferencePrepareError("repo_binding:trend_fact_design")
    return value


def _bound_p024_panel(
    *, root: Path, training_root: Path, config: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    result = _bound_json(root, config["bindings"]["p024_result"], "p024_result")
    if result.get("state") != "CLOSED_CLUSTERED_ADMISSION_CAPACITY_SHORTFALL_REVIEWED":
        raise DownsideLifecycleReferencePrepareError("p024_result_state")
    binding = config["bindings"]["p024_artifact"]
    artifact_root = (training_root / str(binding["artifact_relative_path"])).resolve()
    verified = verify_p024_artifact(
        artifact_root,
        code_revision=str(binding["code_revision"]),
    )
    if (
        verified.get("manifest_sha256") != binding.get("manifest_sha256")
        or verified.get("clustered_plan_digest") != binding.get("clustered_plan_digest")
        or result.get("job", {}).get("manifest_sha256") != binding.get("manifest_sha256")
        or result.get("final_panel", {}).get("clustered_plan_digest")
        != binding.get("clustered_plan_digest")
    ):
        raise DownsideLifecycleReferencePrepareError("p024_artifact_binding")
    plan = _object(artifact_root / "clustered-plan.json", "p024_clustered_plan")
    rows = plan.get("rows")
    if not isinstance(rows, list) or len(rows) != 187:
        raise DownsideLifecycleReferencePrepareError("p024_panel_rows")
    exact = [dict(row) for row in rows if isinstance(row, Mapping)]
    if len(exact) != 187 or digest(exact) != str(binding["clustered_plan_digest"]):
        raise DownsideLifecycleReferencePrepareError("p024_panel_digest")
    clusters = {str(row["security_id"]) for row in exact}
    if len(clusters) != 179:
        raise DownsideLifecycleReferencePrepareError("p024_panel_clusters")
    counts = Counter(str(row["cell"]) for row in exact)
    if dict(sorted(counts.items())) != dict(sorted(config["resource_panel"]["cell_counts"].items())):
        raise DownsideLifecycleReferencePrepareError("p024_panel_cells")
    return exact, {
        "p024_job_id": binding["job_id"],
        "p024_code_revision": binding["code_revision"],
        "p024_manifest_sha256": binding["manifest_sha256"],
        "p024_clustered_plan_digest": binding["clustered_plan_digest"],
        "p024_row_count": len(exact),
        "p024_unique_identity_cluster_count": len(clusters),
    }


def _measurement_payload(measurement: Any) -> dict[str, Any]:
    return {
        "concept_id": str(measurement.concept_id),
        "window_sessions": measurement.window_sessions,
        "comparison_window_sessions": measurement.comparison_window_sessions,
        "value": None if measurement.value is None else str(measurement.value),
        "availability_reason": str(measurement.availability_reason),
    }


def _build_packet(
    *,
    row: Mapping[str, Any],
    admissions: Sequence[Any],
    plan_digest: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    case_id = _case_id(plan_digest, row)
    cluster_id = _cluster_id(str(row["security_id"]))
    end = _day(row["window_end"])
    reason = "available"
    values: list[dict[str, Any]] = []
    available = False
    try:
        facts = build_trend_evolution_facts(admissions, report_start=end, report_end=end)
        end_availability = [
            item
            for item in facts.availability
            if item.security_code == str(row["security_code"]) and item.market_date == end
        ]
        end_measurements = [
            item
            for item in facts.measurements
            if item.security_code == str(row["security_code"]) and item.market_date == end
        ]
        if len(end_availability) != 1 or end_availability[0].available is not True:
            reason = (
                str(end_availability[0].reason)
                if end_availability
                else "trend_fact_availability_missing"
            )
        elif len(end_measurements) != 11:
            reason = "trend_fact_measurement_shape"
        else:
            values = [
                _measurement_payload(item)
                for item in sorted(
                    end_measurements,
                    key=lambda item: (
                        item.concept_id,
                        item.window_sessions or 0,
                        item.comparison_window_sessions or 0,
                    ),
                )
            ]
            available = {item["concept_id"] for item in values} == set(CONCEPTS)
            reason = "available" if available else "trend_fact_concept_shape"
    except TrendEvolutionError as exc:
        reason = f"trend_fact_build:{type(exc).__name__}"
    packet = {
        "schema_version": "quanthus.stage-downside-lifecycle-review-packet.v1",
        "case_id": case_id,
        "status": "AVAILABLE" if available else "UNAVAILABLE",
        "knowledge_cutoff": str(row["window_end"]),
        "era": str(row["era"]),
        "board_family": str(row["board_family"]),
        "trend_fact_values": values,
        "fact_availability_reasons": [reason] if reason != "available" else [],
    }
    mapping = {
        "case_id": case_id,
        "cluster_id": cluster_id,
        "security_id": str(row["security_id"]),
        "security_code": str(row["security_code"]),
        "listing_id": str(row["listing_id"]),
        "cell": str(row["cell"]),
        "window_id": str(row["window_id"]),
        "window_start": str(row["window_start"]),
        "window_end": str(row["window_end"]),
        "packet_status": packet["status"],
        "packet_reason": reason,
        "semantic_source_binding_digest": digest([attempt.to_dict() for attempt in admissions]),
    }
    return packet, mapping


def _prepare_packets(
    *,
    rows: Sequence[Mapping[str, Any]],
    training_root: Path,
    node: Mapping[str, Any],
    config_digest: str,
    plan_digest: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    by_window: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_window[(str(row["window_id"]), str(row["window_start"]), str(row["window_end"]))].append(row)
    packets: list[dict[str, Any]] = []
    mappings: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    for window_id, start_text, end_text in sorted(by_window):
        members = by_window[(window_id, start_text, end_text)]
        codes = tuple(sorted({str(row["security_code"]) for row in members}))
        start = _day(start_text)
        end = _day(end_text)
        scope = DevelopmentReadScope(start, start, end)
        admissions, manifest, policy = read_development_admissions(
            training_root=training_root,
            node_config=node,
            scope=scope,
            security_codes=codes,
            spec_digest=config_digest,
        )
        by_code: dict[str, list[Any]] = defaultdict(list)
        for attempt in admissions:
            by_code[attempt.row.security_code].append(attempt)
        for attempts in by_code.values():
            attempts.sort(key=lambda attempt: attempt.row.market_date)
        for row in sorted(members, key=lambda item: (str(item["security_id"]), str(item["window_id"]))):
            attempts = by_code.get(str(row["security_code"]), [])
            packet, mapping = _build_packet(row=row, admissions=attempts, plan_digest=plan_digest)
            packets.append(packet)
            mappings.append(mapping)
        receipts.append(
            {
                "window_id": window_id,
                "scope": scope.to_dict(),
                "security_count": len(codes),
                "attempted": int(manifest["attempted"]),
                "admitted": int(manifest["admitted"]),
                "rejected": int(manifest["rejected"]),
                "manifest_digest": digest(manifest),
                "calendar_ref": policy.calendar_ref,
                "base_manifest_sha256": manifest["base_manifest_sha256"],
                "correction_manifest_sha256": manifest["correction_manifest_sha256"],
            }
        )
    return (
        sorted(packets, key=lambda row: str(row["case_id"])),
        sorted(mappings, key=lambda row: str(row["case_id"])),
        receipts,
    )


def run_prepare(
    *, config_path: Path, training_root: Path, output_root: Path, code_revision: str
) -> dict[str, Any]:
    if not _REVISION.fullmatch(code_revision):
        raise DownsideLifecycleReferencePrepareError("exact_code_revision_required")
    root = find_project_root()
    config = load_config(config_path)
    p020 = _bound_json(root, config["bindings"]["p020_semantic_concept"], "p020_semantic_concept")
    model = _bound_json(root, config["bindings"]["resource_model"], "resource_model")
    trend_contract = _bound_json(root, config["bindings"]["trend_fact_contract"], "trend_fact_contract")
    node = _bound_json(root, config["bindings"]["training_node"], "training_node")
    if p020.get("state") != "PREREGISTERED_DOWNSIDE_LIFECYCLE_SEMANTICS_BEFORE_REFERENCE_OR_THRESHOLD_SEARCH":
        raise DownsideLifecycleReferencePrepareError("p020_state")
    if model.get("selected_model", {}).get("name") != "ERA_BOARD_EXPOSURE_AWARE_DEVELOPMENT_RESERVOIR":
        raise DownsideLifecycleReferencePrepareError("resource_model_state")
    if trend_contract.get("fact_family_id") != "structure-trend-evolution-v1":
        raise DownsideLifecycleReferencePrepareError("trend_contract_state")
    training_root = training_root.expanduser().resolve()
    rows, p024_binding = _bound_p024_panel(root=root, training_root=training_root, config=config)
    config_digest = digest(config)
    plan_digest = str(config["resource_panel"]["clustered_plan_digest"])
    packets, case_map, receipts = _prepare_packets(
        rows=rows,
        training_root=training_root,
        node=node,
        config_digest=config_digest,
        plan_digest=plan_digest,
    )
    if len(packets) != 187 or len(case_map) != 187:
        raise DownsideLifecycleReferencePrepareError("packet_count")
    available = [row for row in packets if row["status"] == "AVAILABLE"]
    unavailable = [row for row in packets if row["status"] != "AVAILABLE"]
    mapping_by_case = {str(row["case_id"]): row for row in case_map}
    available_clusters = {
        str(mapping_by_case[str(row["case_id"])]["cluster_id"]) for row in available
    }
    all_clusters = {str(row["cluster_id"]) for row in case_map}
    if len(all_clusters) != 179:
        raise DownsideLifecycleReferencePrepareError("case_map_cluster_count")
    cells = Counter(str(row["cell"]) for row in case_map)
    packet_lines = b"".join(canonical(row) + b"\n" for row in packets)
    case_map_payload = {
        "schema_version": "quanthus.stage-downside-lifecycle-review-case-map.v1",
        "rows": case_map,
        "row_count": len(case_map),
        "unique_cluster_count": len(all_clusters),
        "case_map_digest": digest(case_map),
    }
    packet_manifest = {
        "schema_version": config["output"]["packet_manifest_schema"],
        "packet_count": len(packets),
        "available_packet_count": len(available),
        "unavailable_packet_count": len(unavailable),
        "unique_cluster_count": len(all_clusters),
        "available_unique_cluster_count": len(available_clusters),
        "cell_counts": dict(sorted(cells.items())),
        "capacity_limited_stratum": str(config["resource_panel"]["capacity_limited_stratum"]),
        "reviewer_identity_fields_present": False,
        "future_fields_present": False,
        "candidate_or_threshold_fields_present": False,
        "packet_lines_sha256": _sha(packet_lines),
        "case_map_digest": case_map_payload["case_map_digest"],
    }
    source_payload = {
        "schema_version": config["output"]["source_binding_schema"],
        **p024_binding,
        "p020_semantic_concept_sha256": config["bindings"]["p020_semantic_concept"]["sha256"],
        "trend_fact_contract_sha256": config["bindings"]["trend_fact_contract"]["sha256"],
        "packet_config_digest": config_digest,
        "market_read_receipts_digest": digest(receipts),
        "market_read_window_count": len(receipts),
        "resource_replacement_count": 0,
        "additional_admission_read_count": 0,
        "review_opinion_count": 0,
        "candidate_or_threshold_read": False,
        "preserved_2021_read": False,
        "2022_2024_read": False,
        "2025_read": False,
    }
    summary = {
        "schema_version": "quanthus.stage-downside-lifecycle-review-packet-summary.v1",
        "work_item": "P3-STAGE-020-C",
        "phase": "CAPACITY_LIMITED_T_ONLY_REVIEW_PACKET_PREPARATION",
        "code_revision": code_revision,
        "config_digest": config_digest,
        "packet_count": len(packets),
        "available_packet_count": len(available),
        "unavailable_packet_count": len(unavailable),
        "unique_identity_cluster_count": len(all_clusters),
        "available_unique_identity_cluster_count": len(available_clusters),
        "cell_counts": dict(sorted(cells.items())),
        "early_star_row_count": cells.get("STAR_PRE_CHINEXT_20PCT/STAR", 0),
        "review_packet_sha256": _sha(packet_lines),
        "case_map_digest": case_map_payload["case_map_digest"],
        "source_binding_digest": digest(source_payload),
        "ready_for_blind_opinion_collection": True,
        "review_opinion_count": 0,
        "raw_ohlc_published_or_retained": False,
        "candidate_or_threshold_read": False,
        "future_outcome_read": False,
        "preserved_2021_read": False,
        "2022_2024_read": False,
        "2025_read": False,
        "daily_or_strategy": False,
        "trading": False,
    }
    run_id = "stage-downside-lifecycle-review-packets-" + datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    run_root = output_root.expanduser().resolve() / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    payloads = {
        "review-packets.jsonl": packet_lines,
        "case-map.json": canonical(case_map_payload),
        "packet-manifest.json": canonical(packet_manifest),
        "source-binding.json": canonical(source_payload),
        "summary.json": canonical(summary),
    }
    for name, raw in payloads.items():
        (run_root / name).write_bytes(raw)
    manifest = {
        "schema_version": config["output"]["packet_artifact_schema"],
        "run_id": run_id,
        "code_revision": code_revision,
        "config_digest": config_digest,
        "files": {name: _sha(raw) for name, raw in payloads.items()},
        "authority": {
            "review_opinion_count": 0,
            "raw_ohlc_published_or_retained": False,
            "candidate_or_threshold_read": False,
            "future_outcome_read": False,
            "preserved_2021_read": False,
            "2022_2024_read": False,
            "2025_read": False,
            "daily_or_strategy": False,
            "trading": False,
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
        raise DownsideLifecycleReferencePrepareError("artifact_ready_digest")
    if manifest.get("schema_version") != "quanthus.stage-downside-lifecycle-review-packet-artifact.v1":
        raise DownsideLifecycleReferencePrepareError("artifact_schema")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise DownsideLifecycleReferencePrepareError("artifact_code_revision")
    files = manifest.get("files")
    expected = {
        "review-packets.jsonl",
        "case-map.json",
        "packet-manifest.json",
        "source-binding.json",
        "summary.json",
    }
    if not isinstance(files, Mapping) or set(files) != expected:
        raise DownsideLifecycleReferencePrepareError("artifact_files")
    raw_payloads: dict[str, bytes] = {}
    for name in expected:
        raw = (root / name).read_bytes()
        if _sha(raw) != files[name]:
            raise DownsideLifecycleReferencePrepareError(f"artifact_file_digest:{name}")
        raw_payloads[name] = raw
    try:
        packets = [json.loads(line) for line in raw_payloads["review-packets.jsonl"].splitlines()]
        case_map = json.loads(raw_payloads["case-map.json"])
        packet_manifest = json.loads(raw_payloads["packet-manifest.json"])
        source = json.loads(raw_payloads["source-binding.json"])
        summary = json.loads(raw_payloads["summary.json"])
    except json.JSONDecodeError as exc:
        raise DownsideLifecycleReferencePrepareError("artifact_json") from exc
    if len(packets) != 187 or summary.get("packet_count") != 187:
        raise DownsideLifecycleReferencePrepareError("artifact_packet_count")
    rows = case_map.get("rows") if isinstance(case_map, Mapping) else None
    if not isinstance(rows, list) or len(rows) != 187:
        raise DownsideLifecycleReferencePrepareError("artifact_case_map")
    if case_map.get("unique_cluster_count") != 179 or summary.get("unique_identity_cluster_count") != 179:
        raise DownsideLifecycleReferencePrepareError("artifact_cluster_count")
    if case_map.get("case_map_digest") != digest(rows):
        raise DownsideLifecycleReferencePrepareError("artifact_case_map_digest")
    if packet_manifest.get("packet_lines_sha256") != _sha(raw_payloads["review-packets.jsonl"]):
        raise DownsideLifecycleReferencePrepareError("artifact_packet_digest")
    if summary.get("review_packet_sha256") != packet_manifest.get("packet_lines_sha256"):
        raise DownsideLifecycleReferencePrepareError("summary_packet_digest")
    if summary.get("case_map_digest") != case_map.get("case_map_digest"):
        raise DownsideLifecycleReferencePrepareError("summary_case_map_digest")
    if summary.get("source_binding_digest") != digest(source):
        raise DownsideLifecycleReferencePrepareError("summary_source_digest")
    forbidden_packet_keys = {
        "security_id",
        "security_code",
        "listing_id",
        "cluster_id",
        "window_id",
        "window_start",
        "candidate",
        "threshold",
        "future",
    }
    case_ids: set[str] = set()
    for packet in packets:
        if not isinstance(packet, Mapping):
            raise DownsideLifecycleReferencePrepareError("packet_shape")
        if forbidden_packet_keys & set(packet):
            raise DownsideLifecycleReferencePrepareError("reviewer_identity_or_future_leak")
        if set(packet) != {
            "schema_version",
            "case_id",
            "status",
            "knowledge_cutoff",
            "era",
            "board_family",
            "trend_fact_values",
            "fact_availability_reasons",
        }:
            raise DownsideLifecycleReferencePrepareError("review_packet_fields")
        case_id = str(packet.get("case_id") or "")
        if not case_id.startswith("case_") or case_id in case_ids:
            raise DownsideLifecycleReferencePrepareError("review_case_id")
        case_ids.add(case_id)
        if packet.get("status") not in {"AVAILABLE", "UNAVAILABLE"}:
            raise DownsideLifecycleReferencePrepareError("review_packet_status")
        facts = packet.get("trend_fact_values")
        if not isinstance(facts, list):
            raise DownsideLifecycleReferencePrepareError("review_packet_facts")
        if packet.get("status") == "AVAILABLE":
            if len(facts) != 11 or {str(item.get("concept_id")) for item in facts} != set(CONCEPTS):
                raise DownsideLifecycleReferencePrepareError("available_packet_fact_shape")
    map_case_ids = {str(row.get("case_id")) for row in rows if isinstance(row, Mapping)}
    if map_case_ids != case_ids:
        raise DownsideLifecycleReferencePrepareError("case_map_case_ids")
    if packet_manifest.get("reviewer_identity_fields_present") is not False:
        raise DownsideLifecycleReferencePrepareError("identity_visibility")
    if packet_manifest.get("future_fields_present") is not False:
        raise DownsideLifecycleReferencePrepareError("future_visibility")
    if packet_manifest.get("candidate_or_threshold_fields_present") is not False:
        raise DownsideLifecycleReferencePrepareError("candidate_visibility")
    if summary.get("ready_for_blind_opinion_collection") is not True:
        raise DownsideLifecycleReferencePrepareError("next_gate")
    if summary.get("review_opinion_count") != 0:
        raise DownsideLifecycleReferencePrepareError("opinion_precollection")
    for key in (
        "raw_ohlc_published_or_retained",
        "candidate_or_threshold_read",
        "future_outcome_read",
        "preserved_2021_read",
        "2022_2024_read",
        "2025_read",
        "daily_or_strategy",
        "trading",
    ):
        if summary.get(key) is not False:
            raise DownsideLifecycleReferencePrepareError(f"summary_authority:{key}")
    return {
        "verified": True,
        "run_id": manifest.get("run_id"),
        "manifest_sha256": ready,
        "file_count": len(files),
        "packet_count": summary.get("packet_count"),
        "available_packet_count": summary.get("available_packet_count"),
        "unavailable_packet_count": summary.get("unavailable_packet_count"),
        "unique_identity_cluster_count": summary.get("unique_identity_cluster_count"),
        "available_unique_identity_cluster_count": summary.get(
            "available_unique_identity_cluster_count"
        ),
        "cell_counts": summary.get("cell_counts"),
        "early_star_row_count": summary.get("early_star_row_count"),
        "review_packet_sha256": summary.get("review_packet_sha256"),
        "case_map_digest": summary.get("case_map_digest"),
        "ready_for_blind_opinion_collection": True,
        "review_opinion_count": 0,
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
        result = run_prepare(
            config_path=args.config,
            training_root=args.training_root,
            output_root=args.output_root,
            code_revision=args.code_revision,
        )
    else:
        result = verify_artifact(args.artifact, code_revision=args.code_revision)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

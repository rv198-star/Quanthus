"""P3-STAGE-026-C bounded Stage8 resource admission and blind packet preparation.

The runner freezes the complete candidate order before any new market read, excludes every exact
identity-window already consumed by P013/P014/P020 semantic work, performs each bounded technical
read at most once, selects a capacity-limited Era x Board panel using readiness only, and reuses the
same in-memory admission attempts to build candidate/future-blind Trend Evolution packets. Stage9,
protected partitions, candidates, thresholds and economic outcomes remain unavailable.
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
from quanthus.stage_learning import admission_exposure_capacity as p021
from quanthus.stage_learning.complete_reserve_additional_review_set import (
    verify_artifact as verify_p015_artifact,
)
from quanthus.stage_learning.development_resource_admission_screen import (
    _reason_for_attempts,
    _spread_tokens,
)
from quanthus.stage_learning.development_resource_plan import _reconstruct_tokens
from quanthus.stage_learning.downside_lifecycle_reference_prepare import (
    verify_artifact as verify_p020_artifact,
)
from quanthus.stage_learning.source import DevelopmentReadScope, read_development_admissions
from quanthus.structure_trend_evolution import (
    CONCEPTS,
    TrendEvolutionError,
    build_trend_evolution_facts,
)
from quanthus.structure_v2_inputs import canonical, digest

DEFAULT_CONFIG = "config/stage-learning/stage-high-side-modifier-blind-reference.v1.json"
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")


class HighSideModifierReferencePrepareError(ValueError):
    """Raised when P026-C preparation violates the frozen P026-B contract."""


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _file_sha(path: Path) -> str:
    try:
        return _sha(path.read_bytes())
    except OSError as exc:
        raise HighSideModifierReferencePrepareError(f"cannot_hash:{path}") from exc


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HighSideModifierReferencePrepareError(f"cannot_read:{label}") from exc
    if not isinstance(value, dict):
        raise HighSideModifierReferencePrepareError(f"object_required:{label}")
    return value


def _bound_json(root: Path, binding: Mapping[str, Any], label: str) -> dict[str, Any]:
    path = root / str(binding.get("path") or "")
    expected = str(binding.get("sha256") or "")
    if not _SHA.fullmatch(expected) or _file_sha(path) != expected:
        raise HighSideModifierReferencePrepareError(f"repo_binding:{label}")
    return _object(path, label)


def _day(value: object) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise HighSideModifierReferencePrepareError("invalid_date") from exc


def _rank(seed: str, *parts: object) -> str:
    return hashlib.sha256("|".join([seed, *(str(part) for part in parts)]).encode()).hexdigest()


def _case_id(plan_digest: str, row: Mapping[str, Any]) -> str:
    raw = "|".join(
        (
            plan_digest,
            str(row["security_id"]),
            str(row["listing_id"]),
            str(row["window_id"]),
            str(row["cell"]),
        )
    ).encode()
    return "case_" + hashlib.sha256(raw).hexdigest()[:32]


def _cluster_id(identity: str) -> str:
    return "cluster_" + hashlib.sha256(identity.encode()).hexdigest()[:32]


def _overlaps(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_start, left_end = _day(left["window_start"]), _day(left["window_end"])
    right_start, right_end = _day(right["window_start"]), _day(right["window_end"])
    return not (left_end < right_start or right_end < left_start)


def load_config(path: Path | None = None) -> dict[str, Any]:
    root = find_project_root()
    value = _object(path or root / DEFAULT_CONFIG, "p026b_config")
    if (
        value.get("schema_version") != "quanthus.stage-high-side-modifier-blind-reference.v1"
        or value.get("work_item") != "P3-STAGE-026-B"
        or value.get("state")
        != "PREREGISTERED_STAGE8_CAPACITY_LIMITED_BLIND_REFERENCE_BEFORE_NEW_MARKET_READ"
    ):
        raise HighSideModifierReferencePrepareError("config_identity")
    exposure = value.get("exposure_contract")
    selection = value.get("resource_selection")
    gate = value.get("resource_gate")
    packet = value.get("packet_contract")
    review = value.get("review_protocol")
    reference_gate = value.get("reference_sufficiency_gate")
    runtime = value.get("runtime")
    authority = value.get("authority")
    if not all(
        isinstance(item, Mapping)
        for item in (exposure, selection, gate, packet, review, reference_gate, runtime, authority)
    ):
        raise HighSideModifierReferencePrepareError("config_sections")
    if (
        selection.get("target_admission_ready_rows_per_cell") != 20
        or selection.get("minimum_admission_ready_rows_per_cell") != 16
        or selection.get("maximum_candidate_identities_per_cell") != 64
        or selection.get("maximum_precommitted_windows_per_identity") != 2
        or selection.get("maximum_selected_rows") != 160
        or selection.get("minimum_selected_rows") != 144
        or selection.get("minimum_unique_economic_identity_clusters") != 128
        or selection.get("one_selected_row_per_identity_within_cell") is not True
        or selection.get("cross_cell_repeat_identity_allowed_only_for_non_overlapping_windows")
        is not True
        or selection.get("technical_fallback_order_frozen_before_read") is not True
        or selection.get("no_post_result_replacement") is not True
        or gate.get("minimum_rows_per_cell") != 16
        or gate.get("minimum_total_rows") != 144
        or gate.get("minimum_unique_identity_clusters") != 128
        or gate.get("p013_p014_p020_exact_consumed_window_overlap_count") != 0
        or gate.get("same_identity_selected_window_overlap_count") != 0
        or runtime.get("maximum_resource_packet_jobs") != 1
        or runtime.get("automatic_retries") != 0
    ):
        raise HighSideModifierReferencePrepareError("config_budget")
    if (
        exposure.get("p014_admission_only_l2a_exact_window_reuse_allowed") is not True
        or exposure.get("l2b_l3_l4_same_exact_window_reuse") is not False
        or exposure.get("p020_exact_row_or_identity_window_reuse_as_fresh_stage8_evidence")
        is not False
        or packet.get("raw_ohlc_published_or_retained") is not False
        or packet.get("security_identity_visible_to_reviewer") is not False
        or packet.get("identity_cluster_visible_to_reviewer") is not False
        or packet.get("stage9_activity_or_opinion_visible") is not False
        or review.get("stage9_opinion_collection") is not False
        or review.get("roles_isolated_until_immutable_seal") is not True
        or reference_gate.get("gate_change_after_opinions") is not False
        or reference_gate.get("post_opinion_resource_replacement") is not False
    ):
        raise HighSideModifierReferencePrepareError("config_blind_boundary")
    if (
        authority.get("bounded_exact_candidate_admission_market_read") is not True
        or authority.get("exact_selected_T_only_trend_fact_packet_read") is not True
    ):
        raise HighSideModifierReferencePrepareError("market_read_authority")
    for key in (
        "candidate_or_threshold_read",
        "candidate_scoring",
        "stage9_activity_fact_implementation",
        "stage9_reference_opinion",
        "primary_stage_output",
        "labelcard_registration",
        "preserved_2021_read",
        "2022_2024_read",
        "2025_read",
        "daily_or_strategy",
        "trading",
    ):
        if authority.get(key) is not False:
            raise HighSideModifierReferencePrepareError(f"authority:{key}")
    return value


def _artifact_root(training_root: Path, binding: Mapping[str, Any]) -> Path:
    return (training_root / str(binding.get("artifact_relative_path") or "")).resolve()


def _consumed_exposure(
    *, root: Path, training_root: Path, config: Mapping[str, Any]
) -> tuple[set[tuple[str, str]], set[tuple[str, str]], dict[str, Any]]:
    p013_tokens, admission_tokens, semantic_tokens, p014_receipt = p021._artifact_tokens(
        root=root, training_root=training_root, config=config
    )
    l2a_tokens = admission_tokens - semantic_tokens

    p015_result = _bound_json(
        root, config["bindings"]["p015_additional_review_result"], "p015_result"
    )
    p015_binding = config["bindings"]["p015_additional_review_artifact"]
    p015_root = _artifact_root(training_root, p015_binding)
    p015_verified = verify_p015_artifact(
        p015_root, code_revision=str(p015_binding["code_revision"])
    )
    if (
        p015_verified.get("manifest_sha256") != p015_binding.get("manifest_sha256")
        or p015_verified.get("set_digest") != p015_binding.get("set_digest")
        or p015_result.get("artifact", {}).get("manifest_sha256")
        != p015_binding.get("manifest_sha256")
    ):
        raise HighSideModifierReferencePrepareError("p015_artifact_binding")
    p015_set = _object(p015_root / "additional-review-set.json", "p015_additional_set")
    p015_rows = p015_set.get("rows")
    if not isinstance(p015_rows, list) or len(p015_rows) != 60:
        raise HighSideModifierReferencePrepareError("p015_rows")
    p015_tokens = {
        (str(row["security_id"]), str(row["window_id"]))
        for row in p015_rows
        if isinstance(row, Mapping)
    }
    if len(p015_tokens) != 60 or not p015_tokens <= semantic_tokens:
        raise HighSideModifierReferencePrepareError("p015_not_subset_of_p014_semantic")

    p020_result = _bound_json(
        root, config["bindings"]["p020_reference_result"], "p020_reference_result"
    )
    p020_binding = config["bindings"]["p020_packet_artifact"]
    p020_root = _artifact_root(training_root, p020_binding)
    p020_verified = verify_p020_artifact(
        p020_root, code_revision=str(p020_binding["code_revision"])
    )
    if (
        p020_verified.get("manifest_sha256") != p020_binding.get("manifest_sha256")
        or p020_verified.get("case_map_digest") != p020_binding.get("case_map_digest")
        or p020_verified.get("review_packet_sha256") != p020_binding.get("review_packet_sha256")
        or p020_result.get("packet_job", {}).get("manifest_sha256")
        != p020_binding.get("manifest_sha256")
    ):
        raise HighSideModifierReferencePrepareError("p020_artifact_binding")
    p020_map = _object(p020_root / "case-map.json", "p020_case_map")
    p020_rows = p020_map.get("rows")
    if (
        not isinstance(p020_rows, list)
        or len(p020_rows) != 187
        or p020_map.get("case_map_digest") != digest(p020_rows)
    ):
        raise HighSideModifierReferencePrepareError("p020_case_map")
    p020_tokens = {
        (str(row["security_id"]), str(row["window_id"]))
        for row in p020_rows
        if isinstance(row, Mapping)
    }
    if len(p020_tokens) != 187:
        raise HighSideModifierReferencePrepareError("p020_token_count")

    consumed = p013_tokens | semantic_tokens | p020_tokens
    return consumed, l2a_tokens, {
        **p014_receipt,
        "p015_additional_review_manifest_sha256": p015_verified["manifest_sha256"],
        "p015_additional_review_set_digest": p015_verified["set_digest"],
        "p015_additional_review_window_count": len(p015_tokens),
        "p015_subset_of_p014_semantic_verified": True,
        "p020_manifest_sha256": p020_verified["manifest_sha256"],
        "p020_case_map_digest": p020_verified["case_map_digest"],
        "p020_consumed_window_count": len(p020_tokens),
        "semantic_consumed_union_count": len(consumed),
    }


def _freeze_candidate_reservoir(
    tokens: Sequence[Mapping[str, Any]],
    *,
    consumed: set[tuple[str, str]],
    l2a_tokens: set[tuple[str, str]],
    contract: Mapping[str, Any],
) -> list[dict[str, Any]]:
    cells = tuple(str(cell) for cell in contract["cell_order"])
    identity_cap = int(contract["maximum_candidate_identities_per_cell"])
    window_cap = int(contract["maximum_precommitted_windows_per_identity"])
    identity_seed = str(contract["identity_seed"])
    window_seed = str(contract["window_seed"])
    by_cell_identity: dict[str, dict[str, list[Mapping[str, Any]]]] = {
        cell: defaultdict(list) for cell in cells
    }
    for row in tokens:
        token = (str(row["security_id"]), str(row["window_id"]))
        if token in consumed:
            continue
        cell = f"{row['era']}/{row['board_family']}"
        if cell in by_cell_identity:
            by_cell_identity[cell][str(row["security_id"])].append(row)
    output: list[dict[str, Any]] = []
    for cell_rank, cell in enumerate(cells, start=1):
        identities = sorted(
            by_cell_identity[cell],
            key=lambda identity: (_rank(identity_seed, cell, identity), identity),
        )[:identity_cap]
        for identity_rank, identity in enumerate(identities, start=1):
            windows = _spread_tokens(
                by_cell_identity[cell][identity],
                count=window_cap,
                seed=window_seed,
                identity=identity,
            )
            for window_rank, row in enumerate(windows, start=1):
                token = (identity, str(row["window_id"]))
                output.append(
                    {
                        "security_id": identity,
                        "security_code": str(row["security_code"]),
                        "listing_id": str(row["listing_id"]),
                        "era": str(row["era"]),
                        "board_family": str(row["board_family"]),
                        "board_code": str(row["board_code"]),
                        "venue_id": str(row["venue_id"]),
                        "cell": cell,
                        "window_id": str(row["window_id"]),
                        "window_start": str(row["window_start"]),
                        "window_end": str(row["window_end"]),
                        "prior_exposure_class": (
                            "L2A_TECHNICAL_INPUT_ADMISSION_READ"
                            if token in l2a_tokens
                            else str(row["exposure_level"])
                        ),
                        "cell_rank": cell_rank,
                        "identity_rank": identity_rank,
                        "window_rank": window_rank,
                    }
                )
    if {str(row["cell"]) for row in output} != set(cells):
        raise HighSideModifierReferencePrepareError("candidate_cell_missing")
    if any(
        (str(row["security_id"]), str(row["window_id"])) in consumed for row in output
    ):
        raise HighSideModifierReferencePrepareError("candidate_consumed_overlap")
    return output


def _read_candidates_once(
    *,
    candidates: Sequence[Mapping[str, Any]],
    training_root: Path,
    node: Mapping[str, Any],
    spec_digest: str,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[tuple[str, str], tuple[Any, ...]],
]:
    by_window: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in candidates:
        by_window[str(row["window_id"])].append(row)
    ledger: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    attempts_by_token: dict[tuple[str, str], tuple[Any, ...]] = {}
    for window_id in sorted(by_window):
        members = by_window[window_id]
        starts = {_day(row["window_start"]) for row in members}
        ends = {_day(row["window_end"]) for row in members}
        if len(starts) != 1 or len(ends) != 1:
            raise HighSideModifierReferencePrepareError("candidate_window_shape")
        codes = tuple(sorted({str(row["security_code"]) for row in members}))
        scope = DevelopmentReadScope(next(iter(starts)), next(iter(starts)), next(iter(ends)))
        admissions, manifest, policy = read_development_admissions(
            training_root=training_root,
            node_config=node,
            scope=scope,
            security_codes=codes,
            spec_digest=spec_digest,
        )
        by_code: dict[str, list[Any]] = defaultdict(list)
        for attempt in admissions:
            by_code[attempt.row.security_code].append(attempt)
        for values in by_code.values():
            values.sort(key=lambda attempt: attempt.row.market_date)
        for row in sorted(
            members,
            key=lambda item: (
                int(item["cell_rank"]),
                int(item["identity_rank"]),
                int(item["window_rank"]),
            ),
        ):
            attempts = tuple(by_code.get(str(row["security_code"]), ()))
            qualified, reason = _reason_for_attempts(attempts, row)
            token = (str(row["security_id"]), str(row["window_id"]))
            if token in attempts_by_token:
                raise HighSideModifierReferencePrepareError("candidate_token_duplicate")
            attempts_by_token[token] = attempts
            ledger.append(
                {
                    "security_id": token[0],
                    "security_code": str(row["security_code"]),
                    "listing_id": str(row["listing_id"]),
                    "cell": str(row["cell"]),
                    "window_id": token[1],
                    "window_start": str(row["window_start"]),
                    "window_end": str(row["window_end"]),
                    "cell_rank": int(row["cell_rank"]),
                    "identity_rank": int(row["identity_rank"]),
                    "window_rank": int(row["window_rank"]),
                    "prior_exposure_class": str(row["prior_exposure_class"]),
                    "qualified": qualified,
                    "reason": reason,
                    "source_binding_digest": digest([attempt.to_dict() for attempt in attempts]),
                }
            )
        receipts.append(
            {
                "window_id": window_id,
                "scope": scope.to_dict(),
                "candidate_security_count": len(codes),
                "attempted": int(manifest["attempted"]),
                "admitted": int(manifest["admitted"]),
                "rejected": int(manifest["rejected"]),
                "manifest_digest": digest(manifest),
                "calendar_ref": policy.calendar_ref,
                "base_manifest_sha256": manifest["base_manifest_sha256"],
                "correction_manifest_sha256": manifest["correction_manifest_sha256"],
            }
        )
    return ledger, receipts, attempts_by_token


def _candidate_options(
    reservoir: Sequence[Mapping[str, Any]],
    readiness: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    ready = {
        (str(row["security_id"]), str(row["window_id"]))
        for row in readiness
        if row["qualified"] is True
    }
    output: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in reservoir:
        token = (str(row["security_id"]), str(row["window_id"]))
        if token in ready:
            output[str(row["cell"])][token[0]].append(dict(row))
    for identities in output.values():
        for rows in identities.values():
            rows.sort(key=lambda row: (int(row["window_rank"]), str(row["window_id"])))
    return output


def _select_resource_plan(
    *,
    reservoir: Sequence[Mapping[str, Any]],
    readiness: Sequence[Mapping[str, Any]],
    contract: Mapping[str, Any],
) -> list[dict[str, Any]]:
    cells = tuple(str(cell) for cell in contract["cell_order"])
    target = int(contract["target_admission_ready_rows_per_cell"])
    options = _candidate_options(reservoir, readiness)
    identity_order: dict[str, list[str]] = {}
    for cell in cells:
        ranks: dict[str, int] = {}
        for row in reservoir:
            if str(row["cell"]) == cell:
                ranks.setdefault(str(row["security_id"]), int(row["identity_rank"]))
        identity_order[cell] = sorted(ranks, key=lambda identity: (ranks[identity], identity))

    selected: list[dict[str, Any]] = []
    selected_in_cell: dict[str, set[str]] = {cell: set() for cell in cells}
    selected_by_identity: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def add(row: Mapping[str, Any], *, role: str) -> None:
        cell = str(row["cell"])
        identity = str(row["security_id"])
        selected.append(
            {
                **dict(row),
                "selection_role": role,
                "cluster_id": _cluster_id(identity),
            }
        )
        selected_in_cell[cell].add(identity)
        selected_by_identity[identity].append(dict(row))

    # Phase 1: allocate globally unused identities first in scarcity-first cell order.
    for cell in cells:
        for identity in identity_order[cell]:
            if len(selected_in_cell[cell]) >= target:
                break
            if identity in selected_by_identity or identity not in options[cell]:
                continue
            add(options[cell][identity][0], role="GLOBAL_UNIQUE_FIRST")

    # Phase 2: allow explicit cross-cell identity clusters only on non-overlapping windows.
    for cell in cells:
        if len(selected_in_cell[cell]) >= target:
            continue
        for identity in identity_order[cell]:
            if len(selected_in_cell[cell]) >= target:
                break
            if identity in selected_in_cell[cell] or identity not in options[cell]:
                continue
            if identity not in selected_by_identity:
                add(options[cell][identity][0], role="GLOBAL_UNIQUE_LATE")
                continue
            candidate = next(
                (
                    row
                    for row in options[cell][identity]
                    if all(not _overlaps(row, prior) for prior in selected_by_identity[identity])
                ),
                None,
            )
            if candidate is not None:
                add(candidate, role="CROSS_CELL_NONOVERLAP_CLUSTER_REPEAT")

    by_cell_slot = Counter()
    ordered: list[dict[str, Any]] = []
    for row in sorted(
        selected,
        key=lambda item: (
            int(item["cell_rank"]),
            int(item["identity_rank"]),
            int(item["window_rank"]),
            str(item["security_id"]),
        ),
    ):
        cell = str(row["cell"])
        by_cell_slot[cell] += 1
        ordered.append({**row, "cell_slot": by_cell_slot[cell]})
    return ordered


def _selected_overlap_count(rows: Sequence[Mapping[str, Any]]) -> int:
    by_identity: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_identity[str(row["security_id"])].append(row)
    count = 0
    for values in by_identity.values():
        for index, left in enumerate(values):
            count += sum(_overlaps(left, right) for right in values[index + 1 :])
    return count


def _resource_gate(
    *,
    selected: Sequence[Mapping[str, Any]],
    consumed: set[tuple[str, str]],
    config: Mapping[str, Any],
    p015_subset_verified: bool,
) -> dict[str, Any]:
    gate = config["resource_gate"]
    cells = tuple(str(cell) for cell in config["resource_selection"]["cell_order"])
    counts = Counter(str(row["cell"]) for row in selected)
    clusters = {str(row["cluster_id"]) for row in selected}
    selected_tokens = {
        (str(row["security_id"]), str(row["window_id"])) for row in selected
    }
    overlap = len(selected_tokens & consumed)
    temporal_overlap = _selected_overlap_count(selected)
    checks = {
        "all_cells_minimum": all(
            counts[cell] >= int(gate["minimum_rows_per_cell"]) for cell in cells
        ),
        "minimum_total_rows": len(selected) >= int(gate["minimum_total_rows"]),
        "minimum_unique_identity_clusters": (
            len(clusters) >= int(gate["minimum_unique_identity_clusters"])
        ),
        "consumed_exact_window_overlap_zero": overlap == 0,
        "p015_subset_verified": p015_subset_verified,
        "same_identity_selected_window_overlap_zero": temporal_overlap == 0,
    }
    passed = all(checks.values())
    return {
        "passed": passed,
        "checks": checks,
        "row_count": len(selected),
        "unique_identity_cluster_count": len(clusters),
        "cell_counts": dict(sorted(counts.items())),
        "consumed_exact_window_overlap_count": overlap,
        "same_identity_selected_window_overlap_count": temporal_overlap,
        "disposition": (
            "PASS_STAGE8_RESOURCE_READY_FOR_T_ONLY_PACKET_BUILD"
            if passed
            else str(gate["on_fail"])
        ),
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
    *, row: Mapping[str, Any], admissions: Sequence[Any], plan_digest: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    case_id = _case_id(plan_digest, row)
    end = _day(row["window_end"])
    reason = "available"
    values: list[dict[str, Any]] = []
    available = False
    try:
        facts = build_trend_evolution_facts(admissions, report_start=end, report_end=end)
        availability = [
            item
            for item in facts.availability
            if item.security_code == str(row["security_code"]) and item.market_date == end
        ]
        measurements = [
            item
            for item in facts.measurements
            if item.security_code == str(row["security_code"]) and item.market_date == end
        ]
        if len(availability) != 1 or availability[0].available is not True:
            reason = (
                str(availability[0].reason)
                if availability
                else "trend_fact_availability_missing"
            )
        elif len(measurements) != 11:
            reason = "trend_fact_measurement_shape"
        else:
            values = [
                _measurement_payload(item)
                for item in sorted(
                    measurements,
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
        "schema_version": "quanthus.stage-high-side-modifier-review-packet.v1",
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
        "cluster_id": str(row["cluster_id"]),
        "security_id": str(row["security_id"]),
        "security_code": str(row["security_code"]),
        "listing_id": str(row["listing_id"]),
        "cell": str(row["cell"]),
        "window_id": str(row["window_id"]),
        "window_start": str(row["window_start"]),
        "window_end": str(row["window_end"]),
        "prior_exposure_class": str(row["prior_exposure_class"]),
        "selection_role": str(row["selection_role"]),
        "packet_status": packet["status"],
        "packet_reason": reason,
        "semantic_source_binding_digest": digest([attempt.to_dict() for attempt in admissions]),
    }
    return packet, mapping


def run_prepare(
    *, config_path: Path, training_root: Path, output_root: Path, code_revision: str
) -> dict[str, Any]:
    if not _REVISION.fullmatch(code_revision):
        raise HighSideModifierReferencePrepareError("exact_code_revision_required")
    root = find_project_root()
    config = load_config(config_path)
    semantics = _bound_json(root, config["bindings"]["p026_semantics"], "p026_semantics")
    model = _bound_json(root, config["bindings"]["resource_model"], "resource_model")
    taxonomy = _bound_json(root, config["bindings"]["exposure_taxonomy"], "taxonomy")
    trend = _bound_json(root, config["bindings"]["trend_fact_contract"], "trend_contract")
    node = _bound_json(root, config["bindings"]["training_node"], "training_node")
    if (
        semantics.get("state") != "PREREGISTERED_HIGH_SIDE_MODIFIER_SEMANTICS_BEFORE_RESULT_READ"
        or semantics.get("stage9", {}).get("current_gate") != "BLOCKED_MISSING_ACTIVITY_RELATION"
    ):
        raise HighSideModifierReferencePrepareError("semantic_state")
    if (
        model.get("selected_model", {}).get("name")
        != "ERA_BOARD_EXPOSURE_AWARE_DEVELOPMENT_RESERVOIR"
    ):
        raise HighSideModifierReferencePrepareError("resource_model_state")
    if taxonomy.get("state") != (
        "PREREGISTERED_ADMISSION_ONLY_EXPOSURE_TAXONOMY_BEFORE_CAPACITY_RECOMPUTE"
    ):
        raise HighSideModifierReferencePrepareError("taxonomy_state")
    if trend.get("fact_family_id") != "structure-trend-evolution-v1":
        raise HighSideModifierReferencePrepareError("trend_contract_state")

    training_root = training_root.expanduser().resolve()
    consumed, l2a_tokens, exposure_receipt = _consumed_exposure(
        root=root, training_root=training_root, config=config
    )
    metadata_tokens, _windows, source_binding, preserved_ids = _reconstruct_tokens(
        root=root, training_root=training_root, config=config
    )
    if {str(row["security_id"]) for row in metadata_tokens} & preserved_ids:
        raise HighSideModifierReferencePrepareError("preserved_identity_in_metadata_surface")
    reservoir = _freeze_candidate_reservoir(
        metadata_tokens,
        consumed=consumed,
        l2a_tokens=l2a_tokens,
        contract=config["resource_selection"],
    )
    reservoir_digest = digest(reservoir)
    config_digest = digest(config)
    readiness, receipts, attempts_by_token = _read_candidates_once(
        candidates=reservoir,
        training_root=training_root,
        node=node,
        spec_digest=config_digest,
    )
    selected = _select_resource_plan(
        reservoir=reservoir,
        readiness=readiness,
        contract=config["resource_selection"],
    )
    resource_gate = _resource_gate(
        selected=selected,
        consumed=consumed,
        config=config,
        p015_subset_verified=bool(exposure_receipt["p015_subset_of_p014_semantic_verified"]),
    )
    resource_plan_digest = digest(selected)

    packets: list[dict[str, Any]] = []
    case_map: list[dict[str, Any]] = []
    if resource_gate["passed"]:
        for row in selected:
            token = (str(row["security_id"]), str(row["window_id"]))
            admissions = attempts_by_token.get(token)
            if admissions is None:
                raise HighSideModifierReferencePrepareError("selected_attempts_missing")
            packet, mapping = _build_packet(
                row=row,
                admissions=admissions,
                plan_digest=resource_plan_digest,
            )
            packets.append(packet)
            case_map.append(mapping)
        packets.sort(key=lambda row: str(row["case_id"]))
        case_map.sort(key=lambda row: str(row["case_id"]))

    packet_lines = b"".join(canonical(row) + b"\n" for row in packets)
    reservoir_payload = {
        "schema_version": "quanthus.stage-high-side-modifier-candidate-reservoir.v1",
        "rows": reservoir,
        "row_count": len(reservoir),
        "unique_identity_count": len({str(row["security_id"]) for row in reservoir}),
        "cell_identity_counts": dict(
            sorted(
                {
                    cell: len(
                        {str(row["security_id"]) for row in reservoir if row["cell"] == cell}
                    )
                    for cell in config["resource_selection"]["cell_order"]
                }.items()
            )
        ),
        "reservoir_digest": reservoir_digest,
        "frozen_before_market_read": True,
        "semantic_values_used_for_order": False,
    }
    readiness_payload = {
        "schema_version": "quanthus.stage-high-side-modifier-readiness-ledger.v1",
        "rows": readiness,
        "row_count": len(readiness),
        "qualified_count": sum(row["qualified"] is True for row in readiness),
        "failure_reason_counts": dict(
            sorted(
                Counter(
                    str(row["reason"])
                    for row in readiness
                    if row["qualified"] is not True
                ).items()
            )
        ),
        "raw_ohlc_retained": False,
    }
    resource_payload = {
        "schema_version": config["output"]["resource_plan_schema"],
        "rows": selected,
        "row_count": len(selected),
        "unique_identity_cluster_count": len({str(row["cluster_id"]) for row in selected}),
        "cell_counts": resource_gate["cell_counts"],
        "plan_digest": resource_plan_digest,
        "resource_gate": resource_gate,
        "all_rows_admission_ready": True,
    }
    case_map_payload = {
        "schema_version": config["output"]["case_map_schema"],
        "rows": case_map,
        "row_count": len(case_map),
        "unique_cluster_count": len({str(row["cluster_id"]) for row in case_map}),
        "case_map_digest": digest(case_map),
    }
    source_payload = {
        "schema_version": "quanthus.stage-high-side-modifier-reference-source-binding.v1",
        **exposure_receipt,
        "base_manifest_sha256": source_binding["base_manifest_sha256"],
        "correction_manifest_sha256": source_binding["correction_manifest_sha256"],
        "candidate_reservoir_digest": reservoir_digest,
        "resource_plan_digest": resource_plan_digest,
        "market_read_receipts_digest": digest(receipts),
        "market_read_window_count": len(receipts),
        "l2a_reused_selected_row_count": sum(
            row["prior_exposure_class"] == "L2A_TECHNICAL_INPUT_ADMISSION_READ" for row in selected
        ),
        "preserved_2021_identity_count": len(preserved_ids),
        "candidate_or_threshold_read": False,
        "stage9_activity_read": False,
        "preserved_2021_read": False,
        "2022_2024_read": False,
        "2025_read": False,
    }
    summary = {
        "schema_version": "quanthus.stage-high-side-modifier-reference-prepare-summary.v1",
        "work_item": "P3-STAGE-026-C",
        "phase": "BOUNDED_STAGE8_RESOURCE_AND_T_ONLY_PACKET_PREPARATION",
        "code_revision": code_revision,
        "config_digest": config_digest,
        "candidate_reservoir_row_count": len(reservoir),
        "candidate_reservoir_digest": reservoir_digest,
        "technical_readiness_observation_count": len(readiness),
        "resource_gate": resource_gate,
        "selected_row_count": len(selected),
        "selected_unique_identity_cluster_count": resource_gate["unique_identity_cluster_count"],
        "selected_cell_counts": resource_gate["cell_counts"],
        "resource_plan_digest": resource_plan_digest,
        "packet_count": len(packets),
        "available_packet_count": sum(row["status"] == "AVAILABLE" for row in packets),
        "unavailable_packet_count": sum(row["status"] != "AVAILABLE" for row in packets),
        "review_packet_sha256": _sha(packet_lines),
        "case_map_digest": case_map_payload["case_map_digest"],
        "ready_for_blind_review": bool(resource_gate["passed"]),
        "review_opinion_count": 0,
        "raw_ohlc_published_or_retained": False,
        "candidate_or_threshold_read": False,
        "stage9_activity_or_opinion_read": False,
        "future_outcome_read": False,
        "preserved_2021_read": False,
        "2022_2024_read": False,
        "2025_read": False,
        "daily_or_strategy": False,
        "trading": False,
    }

    run_id = "stage-high-side-modifier-reference-" + datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    run_root = output_root.expanduser().resolve() / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    payloads = {
        "candidate-reservoir.json": canonical(reservoir_payload),
        "readiness-ledger.json": canonical(readiness_payload),
        "resource-plan.json": canonical(resource_payload),
        "review-packets.jsonl": packet_lines,
        "case-map.json": canonical(case_map_payload),
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
            "review_opinion_count": 0,
            "raw_ohlc_published_or_retained": False,
            "candidate_or_threshold_read": False,
            "stage9_activity_or_opinion_read": False,
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
        raise HighSideModifierReferencePrepareError("artifact_ready_digest")
    if manifest.get("schema_version") != (
        "quanthus.stage-high-side-modifier-reference-prepare-artifact.v1"
    ):
        raise HighSideModifierReferencePrepareError("artifact_schema")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise HighSideModifierReferencePrepareError("artifact_code_revision")
    files = manifest.get("files")
    expected = {
        "candidate-reservoir.json",
        "readiness-ledger.json",
        "resource-plan.json",
        "review-packets.jsonl",
        "case-map.json",
        "source-binding.json",
        "summary.json",
    }
    if not isinstance(files, Mapping) or set(files) != expected:
        raise HighSideModifierReferencePrepareError("artifact_files")
    raw: dict[str, bytes] = {}
    for name in expected:
        value = (root / name).read_bytes()
        if _sha(value) != files[name]:
            raise HighSideModifierReferencePrepareError(f"artifact_file_digest:{name}")
        raw[name] = value
    try:
        reservoir = json.loads(raw["candidate-reservoir.json"])
        readiness = json.loads(raw["readiness-ledger.json"])
        resource = json.loads(raw["resource-plan.json"])
        packets = [json.loads(line) for line in raw["review-packets.jsonl"].splitlines()]
        case_map = json.loads(raw["case-map.json"])
        source = json.loads(raw["source-binding.json"])
        summary = json.loads(raw["summary.json"])
    except json.JSONDecodeError as exc:
        raise HighSideModifierReferencePrepareError("artifact_json") from exc
    if reservoir.get("reservoir_digest") != digest(reservoir.get("rows")):
        raise HighSideModifierReferencePrepareError("reservoir_digest")
    if readiness.get("row_count") != len(readiness.get("rows", [])):
        raise HighSideModifierReferencePrepareError("readiness_count")
    rows = resource.get("rows")
    if not isinstance(rows, list) or resource.get("plan_digest") != digest(rows):
        raise HighSideModifierReferencePrepareError("resource_plan_digest")
    gate = resource.get("resource_gate")
    if not isinstance(gate, Mapping) or bool(gate.get("passed")) != bool(
        summary.get("resource_gate", {}).get("passed")
    ):
        raise HighSideModifierReferencePrepareError("resource_gate_binding")
    case_rows = case_map.get("rows")
    if not isinstance(case_rows, list) or case_map.get("case_map_digest") != digest(case_rows):
        raise HighSideModifierReferencePrepareError("case_map_digest")
    if summary.get("case_map_digest") != case_map.get("case_map_digest"):
        raise HighSideModifierReferencePrepareError("summary_case_map_digest")
    if summary.get("review_packet_sha256") != _sha(raw["review-packets.jsonl"]):
        raise HighSideModifierReferencePrepareError("summary_packet_digest")
    if summary.get("resource_plan_digest") != resource.get("plan_digest"):
        raise HighSideModifierReferencePrepareError("summary_resource_digest")
    if summary.get("candidate_reservoir_digest") != reservoir.get("reservoir_digest"):
        raise HighSideModifierReferencePrepareError("summary_reservoir_digest")
    if source.get("candidate_reservoir_digest") != reservoir.get("reservoir_digest"):
        raise HighSideModifierReferencePrepareError("source_reservoir_digest")
    if source.get("resource_plan_digest") != resource.get("plan_digest"):
        raise HighSideModifierReferencePrepareError("source_resource_digest")

    resource_pass = bool(gate.get("passed"))
    if resource_pass:
        if len(packets) != len(rows) or len(case_rows) != len(rows):
            raise HighSideModifierReferencePrepareError("packet_denominator")
        if summary.get("ready_for_blind_review") is not True:
            raise HighSideModifierReferencePrepareError("review_ready")
    else:
        if packets or case_rows or summary.get("ready_for_blind_review") is not False:
            raise HighSideModifierReferencePrepareError("resource_fail_packet_leak")
    forbidden = {
        "security_id",
        "security_code",
        "listing_id",
        "cluster_id",
        "window_id",
        "window_start",
        "candidate",
        "threshold",
        "future",
        "activity",
        "stage9",
    }
    case_ids: set[str] = set()
    for packet in packets:
        if not isinstance(packet, Mapping) or forbidden & set(packet):
            raise HighSideModifierReferencePrepareError("reviewer_hidden_field_leak")
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
            raise HighSideModifierReferencePrepareError("packet_fields")
        case_id = str(packet.get("case_id") or "")
        if not case_id.startswith("case_") or case_id in case_ids:
            raise HighSideModifierReferencePrepareError("case_id")
        case_ids.add(case_id)
        facts = packet.get("trend_fact_values")
        if not isinstance(facts, list):
            raise HighSideModifierReferencePrepareError("packet_facts")
        if packet.get("status") == "AVAILABLE" and (
            len(facts) != 11
            or {str(item.get("concept_id")) for item in facts} != set(CONCEPTS)
        ):
            raise HighSideModifierReferencePrepareError("packet_fact_shape")
    if {str(row.get("case_id")) for row in case_rows} != case_ids:
        raise HighSideModifierReferencePrepareError("case_map_ids")
    for key in (
        "raw_ohlc_published_or_retained",
        "candidate_or_threshold_read",
        "stage9_activity_or_opinion_read",
        "future_outcome_read",
        "preserved_2021_read",
        "2022_2024_read",
        "2025_read",
        "daily_or_strategy",
        "trading",
    ):
        if summary.get(key) is not False:
            raise HighSideModifierReferencePrepareError(f"summary_authority:{key}")
    if summary.get("review_opinion_count") != 0:
        raise HighSideModifierReferencePrepareError("opinion_precollection")
    return {
        "verified": True,
        "run_id": manifest.get("run_id"),
        "manifest_sha256": ready,
        "file_count": len(files),
        "config_digest": manifest.get("config_digest"),
        "resource_gate_passed": resource_pass,
        "resource_disposition": gate.get("disposition"),
        "selected_row_count": resource.get("row_count"),
        "selected_unique_identity_cluster_count": resource.get("unique_identity_cluster_count"),
        "selected_cell_counts": resource.get("cell_counts"),
        "resource_plan_digest": resource.get("plan_digest"),
        "packet_count": len(packets),
        "available_packet_count": summary.get("available_packet_count"),
        "unavailable_packet_count": summary.get("unavailable_packet_count"),
        "review_packet_sha256": summary.get("review_packet_sha256"),
        "case_map_digest": summary.get("case_map_digest"),
        "ready_for_blind_review": summary.get("ready_for_blind_review"),
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

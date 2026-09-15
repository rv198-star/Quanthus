"""P3-STAGE-022-B metadata-only primary-unique / contingent-fallback feasibility.

The runner freezes 24 active primary identities per Era x Board cell with global identity
uniqueness, then freezes eight dormant same-cell fallback candidates per cell after excluding every
identity. Cross-cell overlap among dormant fallbacks is allowed; activation is a later separately
governed step. No market bars or Stage1/12 semantics are read.
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
from quanthus.stage_learning.development_resource_plan import _reconstruct_tokens
from quanthus.structure_v2_inputs import canonical, digest

DEFAULT_CONFIG = (
    "config/stage-learning/stage-primary-unique-contingent-fallback-feasibility.v1.json"
)
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")


class PrimaryUniqueContingentFallbackError(ValueError):
    """The frozen P022 metadata feasibility contract was violated."""


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _file_sha(path: Path) -> str:
    try:
        return _sha(path.read_bytes())
    except OSError as exc:
        raise PrimaryUniqueContingentFallbackError(f"cannot_hash:{path}") from exc


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PrimaryUniqueContingentFallbackError(f"cannot_read:{label}") from exc
    if not isinstance(value, dict):
        raise PrimaryUniqueContingentFallbackError(f"object_required:{label}")
    return value


def _bound_json(root: Path, binding: Mapping[str, Any], label: str) -> dict[str, Any]:
    path = root / str(binding.get("path") or "")
    expected = str(binding.get("sha256") or "")
    if not _SHA.fullmatch(expected) or _file_sha(path) != expected:
        raise PrimaryUniqueContingentFallbackError(f"repo_binding:{label}")
    return _object(path, label)


def _rank(seed: str, *parts: object) -> str:
    return hashlib.sha256("|".join([seed, *(str(part) for part in parts)]).encode()).hexdigest()


def _day(value: object) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise PrimaryUniqueContingentFallbackError("invalid_date") from exc


def load_config(path: Path | None = None) -> dict[str, Any]:
    root = find_project_root()
    value = _object(path or root / DEFAULT_CONFIG, "p022_feasibility_config")
    if (
        value.get("schema_version")
        != "quanthus.stage-primary-unique-contingent-fallback-feasibility.v1"
        or value.get("work_item") != "P3-STAGE-022-B"
        or value.get("state")
        != "PREREGISTERED_METADATA_ONLY_FEASIBILITY_AFTER_P022_CONTRACT_FREEZE"
    ):
        raise PrimaryUniqueContingentFallbackError("config_identity")
    runtime = value.get("runtime")
    authority = value.get("authority")
    output = value.get("output")
    if not all(isinstance(item, Mapping) for item in (runtime, authority, output)):
        raise PrimaryUniqueContingentFallbackError("config_sections")
    if runtime.get("maximum_jobs") != 1 or runtime.get("automatic_retries") != 0:
        raise PrimaryUniqueContingentFallbackError("runtime_budget")
    if authority.get("listing_calendar_metadata_read") is not True:
        raise PrimaryUniqueContingentFallbackError("metadata_authority")
    if authority.get("prior_artifact_metadata_read") is not True:
        raise PrimaryUniqueContingentFallbackError("artifact_authority")
    for key in (
        "market_bar_read",
        "admission_market_read",
        "stage1_12_semantic_read",
        "threshold_or_candidate_read",
        "reference_opinion_read",
        "fallback_activation",
        "p020_reopen",
        "preserved_2021_read",
        "2022_2024_read",
        "2025_read",
        "daily_or_strategy",
        "trading",
    ):
        if authority.get(key) is not False:
            raise PrimaryUniqueContingentFallbackError(f"authority:{key}")
    return value


def _cell_identity_tokens(
    tokens: Sequence[Mapping[str, Any]],
    *,
    consumed: set[tuple[str, str]],
    cell_order: Sequence[str],
) -> dict[str, dict[str, list[Mapping[str, Any]]]]:
    by_cell: dict[str, dict[str, list[Mapping[str, Any]]]] = {
        str(cell): defaultdict(list) for cell in cell_order
    }
    for row in tokens:
        key = (str(row["security_id"]), str(row["window_id"]))
        if key in consumed:
            continue
        cell = f"{row['era']}/{row['board_family']}"
        if cell in by_cell:
            by_cell[cell][str(row["security_id"])].append(row)
    return by_cell


def _slot_matching(
    candidates_by_cell: Mapping[str, Sequence[str]],
    *,
    cell_order: Sequence[str],
    per_cell: int,
) -> tuple[dict[tuple[str, int], str], bool]:
    slots = [(str(cell), slot) for cell in cell_order for slot in range(1, per_cell + 1)]
    identity_to_slot: dict[str, tuple[str, int]] = {}

    def assign(slot: tuple[str, int], seen: set[str]) -> bool:
        cell, _ = slot
        for identity in candidates_by_cell[cell]:
            if identity in seen:
                continue
            seen.add(identity)
            occupied = identity_to_slot.get(identity)
            if occupied is None or assign(occupied, seen):
                identity_to_slot[identity] = slot
                return True
        return False

    matched_all = True
    for slot in slots:
        if not assign(slot, set()):
            matched_all = False
    slot_to_identity = {slot: identity for identity, slot in identity_to_slot.items()}
    return slot_to_identity, matched_all and len(slot_to_identity) == len(slots)


def _choose_window(
    rows: Sequence[Mapping[str, Any]], *, seed: str, cell: str, identity: str
) -> Mapping[str, Any]:
    if not rows:
        raise PrimaryUniqueContingentFallbackError("identity_without_window")
    return min(
        rows,
        key=lambda row: (
            _rank(seed, cell, identity, row["window_id"]),
            _day(row["window_start"]),
            _day(row["window_end"]),
            str(row["window_id"]),
        ),
    )


def _resource_row(
    row: Mapping[str, Any],
    *,
    cell: str,
    cell_rank: int,
    slot: int,
    role: str,
) -> dict[str, Any]:
    return {
        "security_id": str(row["security_id"]),
        "security_code": str(row["security_code"]),
        "listing_id": str(row["listing_id"]),
        "era": str(row["era"]),
        "board_family": str(row["board_family"]),
        "board_code": str(row["board_code"]),
        "venue_id": str(row["venue_id"]),
        "cell": cell,
        "cell_rank": cell_rank,
        "slot": slot,
        "role": role,
        "window_id": str(row["window_id"]),
        "window_start": str(row["window_start"]),
        "window_end": str(row["window_end"]),
        "prior_exposure_level": str(row["exposure_level"]),
    }


def _freeze_primary(
    by_cell: Mapping[str, Mapping[str, Sequence[Mapping[str, Any]]]],
    *,
    cell_order: Sequence[str],
    per_cell: int,
    identity_seed: str,
    window_seed: str,
) -> tuple[list[dict[str, Any]], bool, list[dict[str, Any]]]:
    candidates_by_cell: dict[str, list[str]] = {}
    for cell in cell_order:
        candidates_by_cell[str(cell)] = sorted(
            by_cell[str(cell)],
            key=lambda identity: (_rank(identity_seed, cell, identity), identity),
        )
    slot_to_identity, feasible = _slot_matching(
        candidates_by_cell,
        cell_order=cell_order,
        per_cell=per_cell,
    )
    rows: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    for cell_rank, cell in enumerate(cell_order, start=1):
        selected = []
        for slot in range(1, per_cell + 1):
            identity = slot_to_identity.get((str(cell), slot))
            if identity is None:
                continue
            window = _choose_window(
                by_cell[str(cell)][identity],
                seed=window_seed,
                cell=str(cell),
                identity=identity,
            )
            rows.append(
                _resource_row(
                    window,
                    cell=str(cell),
                    cell_rank=cell_rank,
                    slot=slot,
                    role="primary",
                )
            )
            selected.append(identity)
        receipts.append(
            {
                "cell": str(cell),
                "available_identity_count": len(candidates_by_cell[str(cell)]),
                "selected_primary_count": len(selected),
                "required_primary_count": per_cell,
                "selected_identity_digest": digest(sorted(selected)),
                "sufficient": len(selected) == per_cell,
            }
        )
    return rows, feasible, receipts


def _freeze_fallback_pools(
    by_cell: Mapping[str, Mapping[str, Sequence[Mapping[str, Any]]]],
    *,
    cell_order: Sequence[str],
    primary_identities: set[str],
    per_cell: int,
    identity_seed: str,
    window_seed: str,
) -> tuple[list[dict[str, Any]], bool, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    all_pass = True
    for cell_rank, cell in enumerate(cell_order, start=1):
        candidates = sorted(
            (
                identity
                for identity in by_cell[str(cell)]
                if identity not in primary_identities
            ),
            key=lambda identity: (_rank(identity_seed, cell, identity), identity),
        )
        chosen = candidates[:per_cell]
        sufficient = len(chosen) == per_cell
        all_pass &= sufficient
        for slot, identity in enumerate(chosen, start=1):
            window = _choose_window(
                by_cell[str(cell)][identity],
                seed=window_seed,
                cell=str(cell),
                identity=identity,
            )
            rows.append(
                _resource_row(
                    window,
                    cell=str(cell),
                    cell_rank=cell_rank,
                    slot=slot,
                    role="dormant_fallback",
                )
            )
        receipts.append(
            {
                "cell": str(cell),
                "available_non_primary_identity_count": len(candidates),
                "frozen_fallback_candidate_count": len(chosen),
                "fallback_ceiling": per_cell,
                "sufficient": sufficient,
                "selected_identity_digest": digest(chosen),
            }
        )
    return rows, all_pass, receipts


def _max_simultaneous_fallback_activation(
    fallback_rows: Sequence[Mapping[str, Any]], *, cell_order: Sequence[str], per_cell: int
) -> dict[str, Any]:
    by_cell: dict[str, list[str]] = {str(cell): [] for cell in cell_order}
    for row in sorted(
        fallback_rows,
        key=lambda item: (int(item["cell_rank"]), int(item["slot"])),
    ):
        by_cell[str(row["cell"])].append(str(row["security_id"]))
    slot_to_identity, full = _slot_matching(
        by_cell,
        cell_order=cell_order,
        per_cell=per_cell,
    )
    counts = Counter(cell for cell, _slot in slot_to_identity)
    return {
        "diagnostic_only": True,
        "all_64_simultaneously_feasible": full,
        "max_simultaneously_activatable_count": len(slot_to_identity),
        "cell_activation_counts": {str(cell): counts[str(cell)] for cell in cell_order},
        "assignment_digest": digest(
            [
                f"{cell}|{slot}|{identity}"
                for (cell, slot), identity in sorted(slot_to_identity.items())
            ]
        ),
    }


def run_feasibility(
    *, config_path: Path, training_root: Path, output_root: Path, code_revision: str
) -> dict[str, Any]:
    if not _REVISION.fullmatch(code_revision):
        raise PrimaryUniqueContingentFallbackError("exact_code_revision_required")
    root = find_project_root()
    config = load_config(config_path)
    contract = _bound_json(root, config["bindings"]["p022_contract"], "p022_contract")
    p021_config = _bound_json(
        root, config["bindings"]["p021_capacity_contract"], "p021_capacity_contract"
    )
    p021_result = _bound_json(
        root, config["bindings"]["p021_capacity_result"], "p021_capacity_result"
    )
    if (
        contract.get("state")
        != "PREREGISTERED_PRIMARY_UNIQUE_CONTINGENT_FALLBACK_BEFORE_FEASIBILITY_RUN"
        or p021_result.get("state")
        != "CLOSED_REPAIRED_TAXONOMY_GLOBAL_IDENTITY_SHORTFALL"
    ):
        raise PrimaryUniqueContingentFallbackError("parent_state")
    primary_contract = contract.get("primary_contract")
    fallback_contract = contract.get("fallback_contract")
    gate = contract.get("feasibility_gate")
    if not all(isinstance(item, Mapping) for item in (primary_contract, fallback_contract, gate)):
        raise PrimaryUniqueContingentFallbackError("contract_sections")
    cell_order = tuple(str(cell) for cell in primary_contract["cell_order"])
    primary_per_cell = int(primary_contract["primary_per_cell"])
    fallback_per_cell = int(fallback_contract["fallback_ceiling_per_cell"])
    if (
        primary_per_cell != 24
        or int(primary_contract["total_primary"]) != 192
        or primary_contract.get("global_identity_unique") is not True
        or fallback_per_cell != 8
        or int(fallback_contract["total_fallback_ceiling"]) != 64
        or fallback_contract.get("same_cell_only") is not True
        or fallback_contract.get("exclude_all_primary_identities") is not True
        or gate.get("primary_global_unique_192_required") is not True
        or gate.get("per_cell_dormant_non_primary_fallback_candidates_required") != 8
        or gate.get("worst_case_64_fallback_simultaneous_activation_required") is not False
    ):
        raise PrimaryUniqueContingentFallbackError("contract_budget")

    training_root = training_root.expanduser().resolve()
    p013_tokens, _admission_tokens, semantic_tokens, prior_receipt = p021._artifact_tokens(
        root=root,
        training_root=training_root,
        config=p021_config,
    )
    tokens, _windows, source_binding, preserved_ids = _reconstruct_tokens(
        root=root,
        training_root=training_root,
        config=p021_config,
    )
    repaired_consumed = p013_tokens | semantic_tokens
    by_cell = _cell_identity_tokens(
        tokens,
        consumed=repaired_consumed,
        cell_order=cell_order,
    )
    primary_rows, primary_feasible, primary_receipts = _freeze_primary(
        by_cell,
        cell_order=cell_order,
        per_cell=primary_per_cell,
        identity_seed=str(primary_contract["identity_seed"]),
        window_seed=str(primary_contract["window_seed"]),
    )
    primary_ids = {str(row["security_id"]) for row in primary_rows}
    fallback_rows: list[dict[str, Any]] = []
    fallback_feasible = False
    fallback_receipts: list[dict[str, Any]] = []
    if primary_feasible and len(primary_ids) == 192:
        fallback_rows, fallback_feasible, fallback_receipts = _freeze_fallback_pools(
            by_cell,
            cell_order=cell_order,
            primary_identities=primary_ids,
            per_cell=fallback_per_cell,
            identity_seed=str(fallback_contract["fallback_identity_seed"]),
            window_seed=str(fallback_contract["fallback_window_seed"]),
        )
    worst_case = _max_simultaneous_fallback_activation(
        fallback_rows,
        cell_order=cell_order,
        per_cell=fallback_per_cell,
    ) if fallback_rows else {
        "diagnostic_only": True,
        "all_64_simultaneously_feasible": False,
        "max_simultaneously_activatable_count": 0,
        "cell_activation_counts": {str(cell): 0 for cell in cell_order},
        "assignment_digest": digest([]),
    }

    if not primary_feasible or len(primary_rows) != 192 or len(primary_ids) != 192:
        disposition = "CLOSED_PRIMARY_GLOBAL_IDENTITY_SHORTFALL"
    elif not fallback_feasible or len(fallback_rows) != 64:
        disposition = "CLOSED_DORMANT_FALLBACK_POOL_SHORTFALL"
    else:
        disposition = "PASS_PRIMARY_UNIQUE_CONTINGENT_FALLBACK_METADATA_FEASIBLE"

    primary_plan = {
        "schema_version": config["output"]["primary_plan_schema"],
        "rows": primary_rows,
        "row_count": len(primary_rows),
        "unique_identity_count": len(primary_ids),
        "cell_counts": dict(sorted(Counter(str(row["cell"]) for row in primary_rows).items())),
        "global_identity_unique": len(primary_ids) == len(primary_rows),
        "plan_digest": digest(primary_rows),
        "cell_receipts": primary_receipts,
        "market_bar_read": False,
    }
    fallback_pools = {
        "schema_version": config["output"]["fallback_pool_schema"],
        "rows": fallback_rows,
        "row_count": len(fallback_rows),
        "unique_identity_count": len({str(row["security_id"]) for row in fallback_rows}),
        "cell_counts": dict(sorted(Counter(str(row["cell"]) for row in fallback_rows).items())),
        "primary_identity_overlap_count": len(
            primary_ids & {str(row["security_id"]) for row in fallback_rows}
        ),
        "cross_cell_identity_overlap_allowed": True,
        "pool_digest": digest(fallback_rows),
        "cell_receipts": fallback_receipts,
        "worst_case_simultaneous_activation": worst_case,
        "fallback_activation_performed": False,
    }
    source = {
        "schema_version": config["output"]["source_binding_schema"],
        **prior_receipt,
        "base_manifest_sha256": source_binding["base_manifest_sha256"],
        "correction_manifest_sha256": source_binding["correction_manifest_sha256"],
        "metadata_token_count": len(tokens),
        "repaired_consumed_window_count": len(repaired_consumed),
        "preserved_2021_identity_count": len(preserved_ids),
        "market_bar_read": False,
        "preserved_2021_read": False,
        "2022_2024_read": False,
        "2025_read": False,
    }
    summary = {
        "schema_version": config["output"]["summary_schema"],
        "work_item": "P3-STAGE-022-B",
        "phase": "PRIMARY_UNIQUE_CONTINGENT_FALLBACK_METADATA_FEASIBILITY",
        "code_revision": code_revision,
        "config_digest": digest(config),
        "disposition": disposition,
        "primary_row_count": len(primary_rows),
        "primary_unique_identity_count": len(primary_ids),
        "primary_all_cells_24": all(
            count == 24 for count in Counter(str(row["cell"]) for row in primary_rows).values()
        ) and len({str(row["cell"]) for row in primary_rows}) == 8,
        "primary_global_identity_unique": len(primary_ids) == len(primary_rows) == 192,
        "fallback_row_count": len(fallback_rows),
        "fallback_all_cells_8": all(
            count == 8 for count in Counter(str(row["cell"]) for row in fallback_rows).values()
        ) and len({str(row["cell"]) for row in fallback_rows}) == 8,
        "fallback_primary_identity_overlap_count": fallback_pools[
            "primary_identity_overlap_count"
        ],
        "worst_case_simultaneous_fallback_activation_count": worst_case[
            "max_simultaneously_activatable_count"
        ],
        "worst_case_all_64_fallbacks_feasible": worst_case[
            "all_64_simultaneously_feasible"
        ],
        "primary_plan_digest": primary_plan["plan_digest"],
        "fallback_pool_digest": fallback_pools["pool_digest"],
        "source_binding_digest": digest(source),
        "market_bar_read": False,
        "admission_market_read": False,
        "stage1_12_semantic_read": False,
        "threshold_or_candidate_read": False,
        "reference_opinion_read": False,
        "fallback_activation": False,
        "p020_reopen_authorized": False,
        "preserved_2021_read": False,
        "2022_2024_read": False,
        "2025_read": False,
    }

    run_id = "stage-primary-unique-contingent-fallback-" + datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    run_root = output_root.expanduser().resolve() / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    payloads = {
        "primary-plan.json": canonical(primary_plan),
        "fallback-pools.json": canonical(fallback_pools),
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
            "fallback_activation": False,
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
        raise PrimaryUniqueContingentFallbackError("artifact_ready_digest")
    if manifest.get("schema_version") != (
        "quanthus.stage-primary-unique-contingent-fallback-artifact.v1"
    ):
        raise PrimaryUniqueContingentFallbackError("artifact_schema")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise PrimaryUniqueContingentFallbackError("artifact_code_revision")
    files = manifest.get("files")
    expected = {"primary-plan.json", "fallback-pools.json", "source-binding.json", "summary.json"}
    if not isinstance(files, Mapping) or set(files) != expected:
        raise PrimaryUniqueContingentFallbackError("artifact_files")
    payloads: dict[str, dict[str, Any]] = {}
    for name in expected:
        raw = (root / name).read_bytes()
        if _sha(raw) != files[name]:
            raise PrimaryUniqueContingentFallbackError(f"artifact_file_digest:{name}")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise PrimaryUniqueContingentFallbackError(f"artifact_shape:{name}")
        payloads[name] = value
    primary = payloads["primary-plan.json"]
    fallback = payloads["fallback-pools.json"]
    summary = payloads["summary.json"]
    primary_rows = primary.get("rows")
    fallback_rows = fallback.get("rows")
    if not isinstance(primary_rows, list) or not isinstance(fallback_rows, list):
        raise PrimaryUniqueContingentFallbackError("artifact_rows")
    if primary.get("plan_digest") != digest(primary_rows):
        raise PrimaryUniqueContingentFallbackError("primary_digest")
    if fallback.get("pool_digest") != digest(fallback_rows):
        raise PrimaryUniqueContingentFallbackError("fallback_digest")
    if summary.get("primary_plan_digest") != primary.get("plan_digest"):
        raise PrimaryUniqueContingentFallbackError("summary_primary_digest")
    if summary.get("fallback_pool_digest") != fallback.get("pool_digest"):
        raise PrimaryUniqueContingentFallbackError("summary_fallback_digest")
    source = payloads["source-binding.json"]
    if summary.get("source_binding_digest") != digest(source):
        raise PrimaryUniqueContingentFallbackError("summary_source_digest")
    primary_ids = {str(row["security_id"]) for row in primary_rows}
    fallback_ids = {str(row["security_id"]) for row in fallback_rows}
    if primary_ids & fallback_ids:
        raise PrimaryUniqueContingentFallbackError("primary_fallback_identity_overlap")
    for key in (
        "market_bar_read",
        "admission_market_read",
        "stage1_12_semantic_read",
        "threshold_or_candidate_read",
        "reference_opinion_read",
        "fallback_activation",
        "p020_reopen_authorized",
        "preserved_2021_read",
        "2022_2024_read",
        "2025_read",
    ):
        if summary.get(key) is not False:
            raise PrimaryUniqueContingentFallbackError(f"summary_authority:{key}")
    return {
        "verified": True,
        "run_id": manifest.get("run_id"),
        "manifest_sha256": ready,
        "file_count": len(files),
        "disposition": summary.get("disposition"),
        "primary_row_count": summary.get("primary_row_count"),
        "primary_unique_identity_count": summary.get("primary_unique_identity_count"),
        "primary_all_cells_24": summary.get("primary_all_cells_24"),
        "primary_global_identity_unique": summary.get("primary_global_identity_unique"),
        "fallback_row_count": summary.get("fallback_row_count"),
        "fallback_all_cells_8": summary.get("fallback_all_cells_8"),
        "fallback_primary_identity_overlap_count": summary.get(
            "fallback_primary_identity_overlap_count"
        ),
        "worst_case_simultaneous_fallback_activation_count": summary.get(
            "worst_case_simultaneous_fallback_activation_count"
        ),
        "worst_case_all_64_fallbacks_feasible": summary.get(
            "worst_case_all_64_fallbacks_feasible"
        ),
        "primary_plan_digest": summary.get("primary_plan_digest"),
        "fallback_pool_digest": summary.get("fallback_pool_digest"),
        "market_bar_read": False,
        "admission_market_read": False,
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
        result = run_feasibility(
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

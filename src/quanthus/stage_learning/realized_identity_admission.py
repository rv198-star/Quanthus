"""P3-STAGE-023 primary-first admission with realized-identity fallback replacement.

The runner binds the exact immutable P022 192-row primary plan, freezes a deterministic same-cell
fallback reservoir from the P021 repaired metadata surface before any readiness result, admits all
primaries first, then activates fallbacks only for technical readiness deficits while preserving
global identity uniqueness of the realized 8 x 24 panel. No Stage1/12 semantic facts, opinions,
thresholds or preserved partitions are read.
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
from quanthus.stage_learning.development_resource_admission_screen import (
    _screen_candidates,
    _spread_tokens,
)
from quanthus.stage_learning.development_resource_plan import _reconstruct_tokens
from quanthus.stage_learning.primary_unique_contingent_fallback import (
    verify_artifact as verify_p022_artifact,
)
from quanthus.structure_v2_inputs import canonical, digest

DEFAULT_CONFIG = "config/stage-learning/stage-realized-identity-admission.v1.json"
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")


class RealizedIdentityAdmissionError(ValueError):
    """The frozen P023 primary-first admission contract was violated."""


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _file_sha(path: Path) -> str:
    try:
        return _sha(path.read_bytes())
    except OSError as exc:
        raise RealizedIdentityAdmissionError(f"cannot_hash:{path}") from exc


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RealizedIdentityAdmissionError(f"cannot_read:{label}") from exc
    if not isinstance(value, dict):
        raise RealizedIdentityAdmissionError(f"object_required:{label}")
    return value


def _bound_json(root: Path, binding: Mapping[str, Any], label: str) -> dict[str, Any]:
    path = root / str(binding.get("path") or "")
    expected = str(binding.get("sha256") or "")
    if not _SHA.fullmatch(expected) or _file_sha(path) != expected:
        raise RealizedIdentityAdmissionError(f"repo_binding:{label}")
    return _object(path, label)


def _rank(seed: str, *parts: object) -> str:
    return hashlib.sha256("|".join([seed, *(str(part) for part in parts)]).encode()).hexdigest()


def _day(value: object) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise RealizedIdentityAdmissionError("invalid_date") from exc


def load_config(path: Path | None = None) -> dict[str, Any]:
    root = find_project_root()
    value = _object(path or root / DEFAULT_CONFIG, "p023_config")
    if (
        value.get("schema_version") != "quanthus.stage-realized-identity-admission.v1"
        or value.get("work_item") != "P3-STAGE-023"
        or value.get("state")
        != "PREREGISTERED_PRIMARY_FIRST_ADMISSION_AND_CONTINGENT_REPLACEMENT_BEFORE_INPUT_READ"
    ):
        raise RealizedIdentityAdmissionError("config_identity")
    primary = value.get("primary_contract")
    fallback = value.get("fallback_reservoir")
    admission = value.get("admission_contract")
    gate = value.get("realized_plan_gate")
    runtime = value.get("runtime")
    authority = value.get("authority")
    if not all(
        isinstance(item, Mapping)
        for item in (primary, fallback, admission, gate, runtime, authority)
    ):
        raise RealizedIdentityAdmissionError("config_sections")
    if (
        primary.get("row_count") != 192
        or primary.get("primary_per_cell") != 24
        or primary.get("cell_count") != 8
        or primary.get("global_identity_unique") is not True
        or primary.get("read_all_primaries_before_fallback_activation") is not True
        or fallback.get("maximum_candidate_identities_per_cell") != 256
        or fallback.get("maximum_windows_per_identity") != 4
        or fallback.get("membership_and_order_frozen_before_input_read") is not True
        or fallback.get("same_cell_only") is not True
        or fallback.get("exclude_exact_primary_identity_window") is not True
        or fallback.get("planned_primary_identity_may_appear_in_other_fallback_windows")
        is not True
        or fallback.get("realized_primary_identity_blocks_fallback_activation_globally")
        is not True
        or fallback.get("activated_fallback_identity_blocks_later_activation_globally")
        is not True
        or fallback.get("at_most_one_realized_case_per_identity") is not True
        or admission.get("required_sessions") != 61
        or admission.get("skip_fallback_without_market_read_if_identity_already_realized")
        is not True
        or gate.get("required_rows_per_cell") != 24
        or gate.get("required_total_rows") != 192
        or gate.get("global_identity_unique") is not True
        or runtime.get("maximum_jobs") != 1
        or runtime.get("automatic_retries") != 0
    ):
        raise RealizedIdentityAdmissionError("config_budget")
    if authority.get("input_readiness_market_read") is not True:
        raise RealizedIdentityAdmissionError("input_read_authority")
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
            raise RealizedIdentityAdmissionError(f"authority:{key}")
    return value


def _p022_primary_plan(
    *, training_root: Path, config: Mapping[str, Any], root: Path
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    result = _bound_json(root, config["bindings"]["p022_result"], "p022_result")
    binding = config["bindings"]["p022_artifact"]
    artifact_root = (training_root / str(binding["artifact_relative_path"])).resolve()
    verified = verify_p022_artifact(
        artifact_root,
        code_revision=str(binding["code_revision"]),
    )
    if (
        verified.get("manifest_sha256") != binding.get("manifest_sha256")
        or verified.get("primary_plan_digest") != binding.get("primary_plan_digest")
        or result.get("job", {}).get("manifest_sha256") != binding.get("manifest_sha256")
        or result.get("primary", {}).get("plan_digest") != binding.get("primary_plan_digest")
    ):
        raise RealizedIdentityAdmissionError("p022_artifact_binding")
    primary = _object(artifact_root / "primary-plan.json", "p022_primary_plan")
    rows = primary.get("rows")
    if not isinstance(rows, list) or len(rows) != 192:
        raise RealizedIdentityAdmissionError("p022_primary_rows")
    identities = {str(row["security_id"]) for row in rows if isinstance(row, Mapping)}
    if len(identities) != 192:
        raise RealizedIdentityAdmissionError("p022_primary_identity_count")
    counts = Counter(str(row["cell"]) for row in rows)
    if len(counts) != 8 or any(value != 24 for value in counts.values()):
        raise RealizedIdentityAdmissionError("p022_primary_cell_counts")
    return [dict(row) for row in rows], {
        "p022_job_id": binding["job_id"],
        "p022_manifest_sha256": verified["manifest_sha256"],
        "p022_primary_plan_digest": verified["primary_plan_digest"],
        "p022_primary_identity_count": len(identities),
    }


def _admission_candidate(
    row: Mapping[str, Any], *, identity_rank: int, window_rank: int
) -> dict[str, Any]:
    era = str(row["era"])
    board_family = str(row["board_family"])
    cell = str(row.get("cell") or f"{era}/{board_family}")
    exposure = row.get("prior_exposure_level") or row.get("exposure_level") or "UNKNOWN"
    return {
        "security_id": str(row["security_id"]),
        "security_code": str(row["security_code"]),
        "listing_id": str(row["listing_id"]),
        "era": era,
        "board_family": board_family,
        "board_code": str(row.get("board_code") or board_family),
        "venue_id": str(row.get("venue_id") or ""),
        "cell": cell,
        "window_id": str(row["window_id"]),
        "window_start": str(row["window_start"]),
        "window_end": str(row["window_end"]),
        "prior_exposure_level": str(exposure),
        "cell_order": int(row.get("cell_rank") or 0),
        "identity_rank": identity_rank,
        "window_rank": window_rank,
    }


def _freeze_fallback_reservoir(
    tokens: Sequence[Mapping[str, Any]],
    *,
    consumed: set[tuple[str, str]],
    primary_rows: Sequence[Mapping[str, Any]],
    contract: Mapping[str, Any],
) -> list[dict[str, Any]]:
    cell_order = tuple(str(cell) for cell in contract["cell_order"])
    identity_cap = int(contract["maximum_candidate_identities_per_cell"])
    window_cap = int(contract["maximum_windows_per_identity"])
    identity_seed = str(contract["identity_seed"])
    window_seed = str(contract["window_seed"])
    primary_tokens = {
        (str(row["security_id"]), str(row["window_id"])) for row in primary_rows
    }
    by_cell_identity: dict[str, dict[str, list[Mapping[str, Any]]]] = {
        cell: defaultdict(list) for cell in cell_order
    }
    for row in tokens:
        key = (str(row["security_id"]), str(row["window_id"]))
        if key in consumed or key in primary_tokens:
            continue
        cell = f"{row['era']}/{row['board_family']}"
        if cell in by_cell_identity:
            by_cell_identity[cell][str(row["security_id"])].append(row)
    output: list[dict[str, Any]] = []
    for cell_rank, cell in enumerate(cell_order, start=1):
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
                output.append(
                    {
                        **_admission_candidate(
                            row,
                            identity_rank=identity_rank,
                            window_rank=window_rank,
                        ),
                        "cell_order": cell_rank,
                    }
                )
    if {str(row["cell"]) for row in output} != set(cell_order):
        raise RealizedIdentityAdmissionError("fallback_reservoir_cell_missing")
    return output


def _primary_admission_rows(primary_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        primary_rows,
        key=lambda row: (int(row["cell_rank"]), int(row["slot"])),
    )
    return [
        _admission_candidate(row, identity_rank=index, window_rank=1)
        for index, row in enumerate(ordered, start=1)
    ]


def _realized_row(
    source: Mapping[str, Any], readiness: Mapping[str, Any], *, role: str
) -> dict[str, Any]:
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
        "role": role,
        "admission_reason": str(readiness["reason"]),
        "admission_source_binding_digest": str(readiness["source_binding_digest"]),
    }


def _readiness_by_token(
    rows: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str], Mapping[str, Any]]:
    return {
        (str(row["security_id"]), str(row["window_id"])): row
        for row in rows
    }


def _activate_fallbacks(
    *,
    reservoir: Sequence[Mapping[str, Any]],
    realized: list[dict[str, Any]],
    realized_ids: set[str],
    deficits: dict[str, int],
    training_root: Path,
    node: Mapping[str, Any],
    spec_digest: str,
    cell_order: Sequence[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    attempts: list[dict[str, Any]] = []
    activated: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    by_cell: dict[str, list[Mapping[str, Any]]] = {str(cell): [] for cell in cell_order}
    for row in reservoir:
        by_cell[str(row["cell"])].append(row)
    for cell in cell_order:
        rows = sorted(
            by_cell[str(cell)],
            key=lambda row: (int(row["identity_rank"]), int(row["window_rank"])),
        )
        for candidate in rows:
            if deficits[str(cell)] <= 0:
                break
            identity = str(candidate["security_id"])
            if identity in realized_ids:
                attempts.append(
                    {
                        "cell": str(cell),
                        "security_id": identity,
                        "window_id": str(candidate["window_id"]),
                        "identity_rank": int(candidate["identity_rank"]),
                        "window_rank": int(candidate["window_rank"]),
                        "market_read": False,
                        "qualified": False,
                        "activated": False,
                        "reason": "SKIPPED_REALIZED_IDENTITY_CONFLICT",
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
            receipts.extend(read_receipts)
            if len(screened) != 1:
                raise RealizedIdentityAdmissionError("fallback_screen_shape")
            readiness = screened[0]
            attempt = {
                "cell": str(cell),
                "security_id": identity,
                "window_id": str(candidate["window_id"]),
                "identity_rank": int(candidate["identity_rank"]),
                "window_rank": int(candidate["window_rank"]),
                "market_read": True,
                "qualified": readiness["qualified"] is True,
                "activated": False,
                "reason": str(readiness["reason"]),
                "source_binding_digest": str(readiness["source_binding_digest"]),
            }
            if readiness["qualified"] is True:
                realized_row = _realized_row(
                    candidate,
                    readiness,
                    role="activated_fallback",
                )
                realized.append(realized_row)
                activated.append(realized_row)
                realized_ids.add(identity)
                deficits[str(cell)] -= 1
                attempt["activated"] = True
            attempts.append(attempt)
    return attempts, activated, receipts


def run_admission(
    *, config_path: Path, training_root: Path, output_root: Path, code_revision: str
) -> dict[str, Any]:
    if not _REVISION.fullmatch(code_revision):
        raise RealizedIdentityAdmissionError("exact_code_revision_required")
    root = find_project_root()
    config = load_config(config_path)
    p021_config = _bound_json(
        root, config["bindings"]["p021_capacity_contract"], "p021_capacity_contract"
    )
    taxonomy = _bound_json(root, config["bindings"]["p021_taxonomy"], "p021_taxonomy")
    model = _bound_json(root, config["bindings"]["resource_model"], "resource_model")
    node = _bound_json(root, config["bindings"]["training_node"], "training_node")
    if taxonomy.get("state") != (
        "PREREGISTERED_ADMISSION_ONLY_EXPOSURE_TAXONOMY_BEFORE_CAPACITY_RECOMPUTE"
    ):
        raise RealizedIdentityAdmissionError("taxonomy_state")
    if model.get("selected_disposition") != (
        "PROCEED_TO_METADATA_ONLY_ERA_BOARD_EXPOSURE_CAPACITY_CENSUS"
    ):
        raise RealizedIdentityAdmissionError("resource_model_state")
    training_root = training_root.expanduser().resolve()
    primary_rows, primary_binding = _p022_primary_plan(
        training_root=training_root,
        config=config,
        root=root,
    )
    p013_tokens, _admission_tokens, semantic_tokens, prior_receipt = p021._artifact_tokens(
        root=root,
        training_root=training_root,
        config=p021_config,
    )
    metadata_tokens, _windows, source_binding, preserved_ids = _reconstruct_tokens(
        root=root,
        training_root=training_root,
        config=p021_config,
    )
    repaired_consumed = p013_tokens | semantic_tokens
    fallback_reservoir = _freeze_fallback_reservoir(
        metadata_tokens,
        consumed=repaired_consumed,
        primary_rows=primary_rows,
        contract=config["fallback_reservoir"],
    )
    if {str(row["security_id"]) for row in fallback_reservoir} & preserved_ids:
        raise RealizedIdentityAdmissionError("preserved_identity_in_fallback_reservoir")
    reservoir_digest = digest(fallback_reservoir)
    config_digest = digest(config)

    primary_candidates = _primary_admission_rows(primary_rows)
    primary_ledger, primary_receipts = _screen_candidates(
        candidates=primary_candidates,
        training_root=training_root,
        node=node,
        spec_digest=config_digest,
    )
    if len(primary_ledger) != 192:
        raise RealizedIdentityAdmissionError("primary_admission_count")
    readiness = _readiness_by_token(primary_ledger)
    realized: list[dict[str, Any]] = []
    realized_ids: set[str] = set()
    deficits = {str(cell): 24 for cell in config["fallback_reservoir"]["cell_order"]}
    primary_role_map = {
        (str(row["security_id"]), str(row["window_id"])): row for row in primary_rows
    }
    for candidate in primary_candidates:
        key = (str(candidate["security_id"]), str(candidate["window_id"]))
        observed = readiness.get(key)
        if observed is None:
            raise RealizedIdentityAdmissionError("primary_readiness_missing")
        if observed["qualified"] is not True:
            continue
        identity = str(candidate["security_id"])
        if identity in realized_ids:
            raise RealizedIdentityAdmissionError("primary_realized_identity_duplicate")
        source = primary_role_map[key]
        realized.append(_realized_row(source, observed, role="primary"))
        realized_ids.add(identity)
        deficits[str(candidate["cell"])] -= 1

    fallback_attempts, activated, fallback_receipts = _activate_fallbacks(
        reservoir=fallback_reservoir,
        realized=realized,
        realized_ids=realized_ids,
        deficits=deficits,
        training_root=training_root,
        node=node,
        spec_digest=config_digest,
        cell_order=tuple(str(cell) for cell in config["fallback_reservoir"]["cell_order"]),
    )
    realized_counts = Counter(str(row["cell"]) for row in realized)
    final_pass = (
        len(realized) == 192
        and len(realized_ids) == 192
        and len(realized_counts) == 8
        and all(value == 24 for value in realized_counts.values())
        and all(value == 0 for value in deficits.values())
    )
    disposition = (
        "PASS_REALIZED_ADMISSION_READY_PLAN_FROZEN"
        if final_pass
        else "CLOSED_REALIZED_ADMISSION_CAPACITY_SHORTFALL"
    )
    realized_sorted = sorted(
        realized,
        key=lambda row: (
            list(config["fallback_reservoir"]["cell_order"]).index(str(row["cell"])),
            str(row["security_id"]),
            str(row["window_id"]),
        ),
    )
    primary_payload = {
        "schema_version": config["output"]["primary_admission_schema"],
        "rows": primary_ledger,
        "row_count": len(primary_ledger),
        "qualified_count": sum(row["qualified"] is True for row in primary_ledger),
        "failure_reason_counts": dict(
            sorted(
                Counter(
                    str(row["reason"])
                    for row in primary_ledger
                    if row["qualified"] is not True
                ).items()
            )
        ),
        "raw_ohlc_retained": False,
    }
    reservoir_payload = {
        "schema_version": config["output"]["fallback_reservoir_schema"],
        "rows": fallback_reservoir,
        "row_count": len(fallback_reservoir),
        "identity_count": len({str(row["security_id"]) for row in fallback_reservoir}),
        "cell_identity_counts": dict(
            sorted(
                {
                    cell: len(
                        {
                            str(row["security_id"])
                            for row in fallback_reservoir
                            if row["cell"] == cell
                        }
                    )
                    for cell in config["fallback_reservoir"]["cell_order"]
                }.items()
            )
        ),
        "reservoir_digest": reservoir_digest,
        "frozen_before_input_read": True,
        "market_values_used_for_order": False,
    }
    fallback_payload = {
        "schema_version": config["output"]["fallback_admission_schema"],
        "attempts": fallback_attempts,
        "attempt_count": len(fallback_attempts),
        "market_read_attempt_count": sum(row["market_read"] is True for row in fallback_attempts),
        "identity_conflict_skip_count": sum(
            row["reason"] == "SKIPPED_REALIZED_IDENTITY_CONFLICT" for row in fallback_attempts
        ),
        "activated_rows": activated,
        "activated_count": len(activated),
        "raw_ohlc_retained": False,
    }
    realized_payload = {
        "schema_version": config["output"]["realized_plan_schema"],
        "rows": realized_sorted,
        "row_count": len(realized_sorted),
        "unique_identity_count": len(realized_ids),
        "cell_counts": dict(sorted(realized_counts.items())),
        "deficits": dict(deficits),
        "primary_realized_count": sum(row["role"] == "primary" for row in realized_sorted),
        "activated_fallback_count": sum(
            row["role"] == "activated_fallback" for row in realized_sorted
        ),
        "global_identity_unique": len(realized_ids) == len(realized_sorted),
        "all_rows_admission_ready": True,
        "plan_digest": digest(realized_sorted),
    }
    source_payload = {
        "schema_version": config["output"]["source_binding_schema"],
        **primary_binding,
        **prior_receipt,
        "base_manifest_sha256": source_binding["base_manifest_sha256"],
        "correction_manifest_sha256": source_binding["correction_manifest_sha256"],
        "repaired_consumed_window_count": len(repaired_consumed),
        "metadata_token_count": len(metadata_tokens),
        "fallback_reservoir_digest": reservoir_digest,
        "primary_window_receipts_digest": digest(primary_receipts),
        "fallback_window_receipts_digest": digest(fallback_receipts),
        "preserved_2021_identity_count": len(preserved_ids),
        "preserved_2021_read": False,
        "2022_2024_read": False,
        "2025_read": False,
    }
    summary = {
        "schema_version": config["output"]["summary_schema"],
        "work_item": "P3-STAGE-023",
        "phase": "PRIMARY_FIRST_ADMISSION_AND_REALIZED_IDENTITY_REPLACEMENT",
        "code_revision": code_revision,
        "config_digest": config_digest,
        "disposition": disposition,
        "primary_admission_count": len(primary_ledger),
        "primary_ready_count": primary_payload["qualified_count"],
        "primary_failure_count": 192 - primary_payload["qualified_count"],
        "fallback_reservoir_row_count": len(fallback_reservoir),
        "fallback_market_read_attempt_count": fallback_payload["market_read_attempt_count"],
        "fallback_identity_conflict_skip_count": fallback_payload["identity_conflict_skip_count"],
        "activated_fallback_count": len(activated),
        "realized_row_count": len(realized_sorted),
        "realized_unique_identity_count": len(realized_ids),
        "realized_cell_counts": dict(sorted(realized_counts.items())),
        "realized_deficits": dict(deficits),
        "realized_global_identity_unique": len(realized_ids) == len(realized_sorted),
        "realized_plan_digest": realized_payload["plan_digest"],
        "fallback_reservoir_digest": reservoir_digest,
        "source_binding_digest": digest(source_payload),
        "ready_for_separate_p020c_preregistration": final_pass,
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

    run_id = "stage-realized-identity-admission-" + datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    run_root = output_root.expanduser().resolve() / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    payloads = {
        "fallback-reservoir.json": canonical(reservoir_payload),
        "primary-admission.json": canonical(primary_payload),
        "fallback-admission.json": canonical(fallback_payload),
        "realized-plan.json": canonical(realized_payload),
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
        raise RealizedIdentityAdmissionError("artifact_ready_digest")
    if manifest.get("schema_version") != "quanthus.stage-realized-identity-admission-artifact.v1":
        raise RealizedIdentityAdmissionError("artifact_schema")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise RealizedIdentityAdmissionError("artifact_code_revision")
    files = manifest.get("files")
    expected = {
        "fallback-reservoir.json",
        "primary-admission.json",
        "fallback-admission.json",
        "realized-plan.json",
        "source-binding.json",
        "summary.json",
    }
    if not isinstance(files, Mapping) or set(files) != expected:
        raise RealizedIdentityAdmissionError("artifact_files")
    payloads: dict[str, dict[str, Any]] = {}
    for name in expected:
        raw = (root / name).read_bytes()
        if _sha(raw) != files[name]:
            raise RealizedIdentityAdmissionError(f"artifact_file_digest:{name}")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise RealizedIdentityAdmissionError(f"artifact_shape:{name}")
        payloads[name] = value
    reservoir = payloads["fallback-reservoir.json"]
    primary = payloads["primary-admission.json"]
    fallback = payloads["fallback-admission.json"]
    realized = payloads["realized-plan.json"]
    source = payloads["source-binding.json"]
    summary = payloads["summary.json"]
    reservoir_rows = reservoir.get("rows")
    primary_rows = primary.get("rows")
    fallback_attempts = fallback.get("attempts")
    realized_rows = realized.get("rows")
    if not all(
        isinstance(rows, list)
        for rows in (reservoir_rows, primary_rows, fallback_attempts, realized_rows)
    ):
        raise RealizedIdentityAdmissionError("artifact_rows")
    if reservoir.get("reservoir_digest") != digest(reservoir_rows):
        raise RealizedIdentityAdmissionError("reservoir_digest")
    if realized.get("plan_digest") != digest(realized_rows):
        raise RealizedIdentityAdmissionError("realized_plan_digest")
    if summary.get("realized_plan_digest") != realized.get("plan_digest"):
        raise RealizedIdentityAdmissionError("summary_realized_digest")
    if summary.get("fallback_reservoir_digest") != reservoir.get("reservoir_digest"):
        raise RealizedIdentityAdmissionError("summary_reservoir_digest")
    if summary.get("source_binding_digest") != digest(source):
        raise RealizedIdentityAdmissionError("summary_source_digest")
    if len(primary_rows) != 192:
        raise RealizedIdentityAdmissionError("primary_rows")
    primary_fields = {
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
    if any(not isinstance(row, Mapping) or set(row) != primary_fields for row in primary_rows):
        raise RealizedIdentityAdmissionError("primary_fields")
    forbidden_names = {"open", "high", "low", "close", "volume", "threshold", "label"}
    for payload_rows in (primary_rows, fallback_attempts, realized_rows):
        if any(
            isinstance(row, Mapping)
            and any(str(key).lower() in forbidden_names for key in row)
            for row in payload_rows
        ):
            raise RealizedIdentityAdmissionError("raw_or_semantic_field")
    realized_ids = {str(row["security_id"]) for row in realized_rows if isinstance(row, Mapping)}
    if len(realized_ids) != len(realized_rows):
        raise RealizedIdentityAdmissionError("realized_identity_uniqueness")
    if summary.get("disposition") == "PASS_REALIZED_ADMISSION_READY_PLAN_FROZEN":
        counts = Counter(str(row["cell"]) for row in realized_rows)
        if (
            len(realized_rows) != 192
            or len(counts) != 8
            or any(value != 24 for value in counts.values())
        ):
            raise RealizedIdentityAdmissionError("pass_realized_shape")
        if summary.get("ready_for_separate_p020c_preregistration") is not True:
            raise RealizedIdentityAdmissionError("pass_next_gate")
    else:
        if summary.get("ready_for_separate_p020c_preregistration") is not False:
            raise RealizedIdentityAdmissionError("fail_next_gate")
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
            raise RealizedIdentityAdmissionError(f"summary_authority:{key}")
    return {
        "verified": True,
        "run_id": manifest.get("run_id"),
        "manifest_sha256": ready,
        "file_count": len(files),
        "disposition": summary.get("disposition"),
        "primary_admission_count": summary.get("primary_admission_count"),
        "primary_ready_count": summary.get("primary_ready_count"),
        "primary_failure_count": summary.get("primary_failure_count"),
        "fallback_reservoir_row_count": summary.get("fallback_reservoir_row_count"),
        "fallback_market_read_attempt_count": summary.get("fallback_market_read_attempt_count"),
        "fallback_identity_conflict_skip_count": summary.get(
            "fallback_identity_conflict_skip_count"
        ),
        "activated_fallback_count": summary.get("activated_fallback_count"),
        "realized_row_count": summary.get("realized_row_count"),
        "realized_unique_identity_count": summary.get("realized_unique_identity_count"),
        "realized_cell_counts": summary.get("realized_cell_counts"),
        "realized_deficits": summary.get("realized_deficits"),
        "realized_global_identity_unique": summary.get("realized_global_identity_unique"),
        "realized_plan_digest": summary.get("realized_plan_digest"),
        "fallback_reservoir_digest": summary.get("fallback_reservoir_digest"),
        "ready_for_separate_p020c_preregistration": summary.get(
            "ready_for_separate_p020c_preregistration"
        ),
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
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

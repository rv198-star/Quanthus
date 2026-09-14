"""P3-STAGE-013-C metadata-only exact era-board identity-window plan.

This module intentionally reconstructs the frozen P013-A metadata capacity surface from governed
listing/calendar metadata and prior immutable exposure artifacts. It never reads security bars,
Structure morphology, labels/references, candidate outputs, preserved-2021 market rows or later
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
from quanthus.stage_learning import development_resource_capacity_census as census
from quanthus.structure_v2_inputs import canonical, digest

DEFAULT_CONFIG = "config/stage-learning/stage-development-resource-plan.v1.json"
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")


class DevelopmentResourcePlanError(ValueError):
    """Raised when the exact metadata resource-plan contract is violated."""


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DevelopmentResourcePlanError(f"cannot_read_{label}") from exc
    if not isinstance(value, dict):
        raise DevelopmentResourcePlanError(f"{label}_must_be_object")
    return value


def _sha_bytes(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _sha_file(path: Path) -> str:
    try:
        return _sha_bytes(path.read_bytes())
    except OSError as exc:
        raise DevelopmentResourcePlanError(f"cannot_hash:{path.name}") from exc


def _day(value: object) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise DevelopmentResourcePlanError("invalid_date") from exc


def _rank(seed: str, *parts: object) -> str:
    raw = "|".join([seed, *(str(part) for part in parts)]).encode()
    return hashlib.sha256(raw).hexdigest()


def load_config(path: Path) -> dict[str, Any]:
    raw = _object(path, "development_resource_plan_config")
    if (
        raw.get("schema_version") != "quanthus.stage-development-resource-plan.v1"
        or raw.get("work_item") != "P3-STAGE-013-C"
        or raw.get("state") != "PREREGISTERED_METADATA_PLAN_BEFORE_RESULT_BEARING_READ"
    ):
        raise DevelopmentResourcePlanError("plan_config_identity")
    selection = raw.get("selection")
    authority = raw.get("authority")
    runtime = raw.get("runtime")
    if not all(isinstance(value, Mapping) for value in (selection, authority, runtime)):
        raise DevelopmentResourcePlanError("plan_config_sections")
    if (
        selection.get("total_reserve_rows") != 256
        or selection.get("total_primary_rows") != 192
        or selection.get("total_fallback_rows") != 64
        or selection.get("reserve_per_cell") != 32
        or selection.get("primary_per_cell") != 24
        or selection.get("fallback_per_cell") != 8
        or selection.get("global_identity_unique") is not True
        or selection.get("same_identity_multiple_rows") is not False
    ):
        raise DevelopmentResourcePlanError("plan_selection_contract")
    required_false = (
        "market_price_read",
        "morphology_read",
        "label_or_reference_read",
        "candidate_output_read",
        "candidate_scoring",
        "preserved_2021_market_read",
        "2022_2024_read",
        "2025_read",
        "result_bearing_stage_authorized",
        "daily_or_strategy",
        "trading",
    )
    if any(authority.get(key) is not False for key in required_false):
        raise DevelopmentResourcePlanError("plan_authority")
    if runtime.get("maximum_jobs") != 1 or runtime.get("automatic_retries") != 0:
        raise DevelopmentResourcePlanError("plan_runtime_budget")
    return raw


def _bound_json(root: Path, binding: Mapping[str, Any], label: str) -> dict[str, Any]:
    path = root / str(binding.get("path"))
    expected = str(binding.get("sha256"))
    if not _SHA.fullmatch(expected) or _sha_file(path) != expected:
        raise DevelopmentResourcePlanError(f"binding:{label}")
    return _object(path, label)


def _window_slots(windows: Sequence[Mapping[str, Any]], count: int) -> list[Mapping[str, Any]]:
    ordered = sorted(
        windows,
        key=lambda row: (
            _day(row["start"]),
            _day(row["end"]),
            str(row["board_code"]),
            str(row["window_id"]),
        ),
    )
    if not ordered or count < 1:
        raise DevelopmentResourcePlanError("window_slot_input")
    if len(ordered) >= count:
        return [ordered[((2 * index + 1) * len(ordered)) // (2 * count)] for index in range(count)]
    return [ordered[index % len(ordered)] for index in range(count)]


def _reconstruct_tokens(
    *, root: Path, training_root: Path, config: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str], set[str]]:
    census_binding = config["bindings"]["capacity_census_contract"]
    census_config = _bound_json(root, census_binding, "capacity_census_contract")
    census_config = census.load_config(root / str(census_binding["path"]))
    node_binding = census_config["bindings"]["training_node"]
    node = census._verify_bound_file(root, node_binding, "training_node")
    stages, _legacy_burn, preserved_ids = census._load_exposures(training_root, census_config)
    listings, calendars, source_binding = census._load_listing_and_calendar(training_root, node)
    windows = census._windows(census_config, calendars)
    by_board: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for listing in listings:
        by_board[listing["board_code"]].append(listing)

    tokens: list[dict[str, Any]] = []
    for window in windows:
        candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for listing in by_board[window["board_code"]]:
            if census._active_for_window(listing, window):
                candidates[listing["security_id"]].append(listing)
        for identity, active in candidates.items():
            unique = {(row["listing_id"], row["listing_code"]) for row in active}
            if len(unique) != 1:
                continue
            level = census._window_level(identity, window, stages, preserved_ids)
            if census._LEVEL_RANK[level] > census._LEVEL_RANK["L1_POOL_RESERVED_NOT_READ"]:
                continue
            listing = active[0]
            tokens.append(
                {
                    "security_id": identity,
                    "listing_id": listing["listing_id"],
                    "security_code": listing["listing_code"],
                    "era": window["era"],
                    "board_family": window["board_family"],
                    "board_code": window["board_code"],
                    "venue_id": window["venue_id"],
                    "window_id": window["window_id"],
                    "window_start": window["start"],
                    "window_end": window["end"],
                    "open_session_count": window["open_session_count"],
                    "exposure_level": level,
                }
            )
    return tokens, windows, source_binding, preserved_ids


def select_exact_plan(
    *,
    tokens: Sequence[Mapping[str, Any]],
    windows: Sequence[Mapping[str, Any]],
    quota: Mapping[str, Any],
) -> list[dict[str, Any]]:
    policy = quota["selection_policy"]
    identity_seed = str(policy["identity_seed"])
    allocation_order = [
        str(value) for value in policy["scarcity_order_for_global_identity_allocation"]
    ]
    quotas = {
        f"{row['era']}/{row['board_family']}": row
        for row in quota["cell_quotas"]
    }
    if set(allocation_order) != set(quotas):
        raise DevelopmentResourcePlanError("allocation_order_cells")
    by_cell_window: dict[str, dict[str, list[Mapping[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    windows_by_cell: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in tokens:
        cell = f"{row['era']}/{row['board_family']}"
        by_cell_window[cell][str(row["window_id"])].append(row)
    for row in windows:
        cell = f"{row['era']}/{row['board_family']}"
        if cell in quotas:
            windows_by_cell[cell][str(row["window_id"])] = row

    used: set[str] = set()
    selected_by_cell: dict[str, list[dict[str, Any]]] = {}
    for allocation_rank, cell in enumerate(allocation_order, start=1):
        reserve = int(quotas[cell]["reserve"])
        cell_windows = list(windows_by_cell[cell].values())
        slots = _window_slots(cell_windows, reserve)
        rows: list[dict[str, Any]] = []
        for slot_index, window in enumerate(slots, start=1):
            candidates = [
                row
                for row in by_cell_window[cell].get(str(window["window_id"]), ())
                if str(row["security_id"]) not in used
            ]
            candidates.sort(
                key=lambda row: (
                    _rank(
                        identity_seed,
                        cell,
                        window["window_id"],
                        slot_index,
                        row["security_id"],
                    ),
                    str(row["security_id"]),
                )
            )
            if not candidates:
                raise DevelopmentResourcePlanError(f"cell_global_identity_shortfall:{cell}")
            chosen = candidates[0]
            identity = str(chosen["security_id"])
            used.add(identity)
            role = "fallback" if slot_index % 4 == 0 else "primary"
            rows.append(
                {
                    "security_id": identity,
                    "listing_id": str(chosen["listing_id"]),
                    "security_code": str(chosen["security_code"]),
                    "era": str(chosen["era"]),
                    "board_family": str(chosen["board_family"]),
                    "board_code": str(chosen["board_code"]),
                    "venue_id": str(chosen["venue_id"]),
                    "window_id": str(chosen["window_id"]),
                    "window_start": _day(chosen["window_start"]).isoformat(),
                    "window_end": _day(chosen["window_end"]).isoformat(),
                    "open_session_count": int(chosen["open_session_count"]),
                    "prior_exposure_level": str(chosen["exposure_level"]),
                    "role": role,
                    "cell_slot": slot_index,
                    "allocation_rank": allocation_rank,
                }
            )
        counts = Counter(row["role"] for row in rows)
        if counts != {"primary": 24, "fallback": 8}:
            raise DevelopmentResourcePlanError(f"cell_role_counts:{cell}")
        selected_by_cell[cell] = rows

    presentation_order = [
        f"{row['era']}/{row['board_family']}" for row in quota["cell_quotas"]
    ]
    output = [row for cell in presentation_order for row in selected_by_cell[cell]]
    if len(output) != 256 or len({row["security_id"] for row in output}) != 256:
        raise DevelopmentResourcePlanError("global_identity_uniqueness")
    return output


def run_plan(
    *, config_path: Path, training_root: Path, output_root: Path, code_revision: str
) -> dict[str, Any]:
    if not _REVISION.fullmatch(code_revision):
        raise DevelopmentResourcePlanError("exact_code_revision_required")
    root = find_project_root()
    config = load_config(config_path)
    result = _bound_json(root, config["bindings"]["capacity_census_result"], "census_result")
    quota = _bound_json(root, config["bindings"]["quota_freeze"], "quota_freeze")
    artifact_binding = config["bindings"]["census_artifact"]
    artifact_root = (
        training_root.expanduser().resolve()
        / str(artifact_binding["artifact_relative_path"])
    ).resolve()
    verified = census.verify_artifact(
        artifact_root,
        code_revision=str(artifact_binding["code_revision"]),
    )
    if verified.get("manifest_sha256") != artifact_binding["manifest_sha256"]:
        raise DevelopmentResourcePlanError("census_artifact_binding")
    if result.get("artifact", {}).get("manifest_sha256") != artifact_binding["manifest_sha256"]:
        raise DevelopmentResourcePlanError("census_result_binding")

    tokens, windows, source_binding, preserved_ids = _reconstruct_tokens(
        root=root,
        training_root=training_root.expanduser().resolve(),
        config=config,
    )
    matrix = result.get("capacity_matrix")
    if not isinstance(matrix, list):
        raise DevelopmentResourcePlanError("census_result_matrix")
    observed = {
        (str(row["era"]), str(row["board_family"])): int(row["available_unique_identity_count"])
        for row in matrix
        if isinstance(row, Mapping) and row.get("structurally_present") is True
    }
    reconstructed: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in tokens:
        reconstructed[(str(row["era"]), str(row["board_family"]))].add(str(row["security_id"]))
    if {key: len(value) for key, value in reconstructed.items()} != observed:
        raise DevelopmentResourcePlanError("reconstructed_capacity_differs")

    rows = select_exact_plan(tokens=tokens, windows=windows, quota=quota)
    if set(row["security_id"] for row in rows) & preserved_ids:
        raise DevelopmentResourcePlanError("preserved_identity_in_plan")
    by_cell = Counter(f"{row['era']}/{row['board_family']}" for row in rows)
    role_counts = Counter(row["role"] for row in rows)
    plan = {
        "schema_version": config["output"]["plan_schema"],
        "selection_revision": code_revision,
        "rows": rows,
        "row_count": len(rows),
        "primary_count": role_counts["primary"],
        "fallback_count": role_counts["fallback"],
        "unique_identity_count": len({row["security_id"] for row in rows}),
        "cell_counts": dict(sorted(by_cell.items())),
        "identity_digest": digest(sorted(row["security_id"] for row in rows)),
        "plan_digest": digest(rows),
        "market_data_read": False,
    }
    source = {
        "schema_version": config["output"]["source_binding_schema"],
        "census_job_id": artifact_binding["job_id"],
        "census_manifest_sha256": artifact_binding["manifest_sha256"],
        "census_result_digest": digest(result),
        "quota_freeze_digest": digest(quota),
        "base_manifest_sha256": source_binding["base_manifest_sha256"],
        "correction_manifest_sha256": source_binding["correction_manifest_sha256"],
        "preserved_2021_identity_count": len(preserved_ids),
        "preserved_2021_market_data_read": False,
        "2022_2024_read": False,
        "2025_read": False,
    }
    summary = {
        "schema_version": config["output"]["summary_schema"],
        "work_item": "P3-STAGE-013-C",
        "phase": "FREEZE_EXACT_METADATA_IDENTITY_WINDOW_PLAN",
        "code_revision": code_revision,
        "config_digest": digest(config),
        "row_count": plan["row_count"],
        "primary_count": plan["primary_count"],
        "fallback_count": plan["fallback_count"],
        "unique_identity_count": plan["unique_identity_count"],
        "cell_counts": plan["cell_counts"],
        "identity_digest": plan["identity_digest"],
        "plan_digest": plan["plan_digest"],
        "market_price_read": False,
        "morphology_read": False,
        "label_or_reference_read": False,
        "candidate_output_read": False,
        "candidate_scoring": False,
        "preserved_2021_market_read": False,
        "2022_2024_read": False,
        "2025_read": False,
        "result_bearing_stage_authorized": False,
    }
    run_id = "stage-development-resource-plan-" + datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    run_root = output_root.expanduser().resolve() / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    payloads = {
        "exact-plan.json": canonical(plan),
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
            "morphology_read": False,
            "label_or_reference_read": False,
            "candidate_output_read": False,
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
        raise DevelopmentResourcePlanError("artifact_ready_digest")
    if manifest.get("schema_version") != "quanthus.stage-development-resource-plan-artifact.v1":
        raise DevelopmentResourcePlanError("artifact_schema")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise DevelopmentResourcePlanError("artifact_code_revision")
    files = manifest.get("files")
    expected = {"exact-plan.json", "source-binding.json", "summary.json"}
    if not isinstance(files, Mapping) or set(files) != expected:
        raise DevelopmentResourcePlanError("artifact_files")
    payloads: dict[str, dict[str, Any]] = {}
    for name in expected:
        raw = (root / name).read_bytes()
        if _sha_bytes(raw) != files[name]:
            raise DevelopmentResourcePlanError(f"artifact_file_digest:{name}")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise DevelopmentResourcePlanError(f"artifact_file_shape:{name}")
        payloads[name] = value
    plan = payloads["exact-plan.json"]
    summary = payloads["summary.json"]
    rows = plan.get("rows")
    if not isinstance(rows, list) or len(rows) != 256:
        raise DevelopmentResourcePlanError("plan_row_count")
    identities = [str(row["security_id"]) for row in rows if isinstance(row, Mapping)]
    if len(identities) != 256 or len(set(identities)) != 256:
        raise DevelopmentResourcePlanError("plan_identity_uniqueness")
    cells = Counter(f"{row['era']}/{row['board_family']}" for row in rows)
    roles = Counter(str(row["role"]) for row in rows)
    if len(cells) != 8 or any(value != 32 for value in cells.values()):
        raise DevelopmentResourcePlanError("plan_cell_counts")
    if roles != {"primary": 192, "fallback": 64}:
        raise DevelopmentResourcePlanError("plan_role_counts")
    if any(row.get("prior_exposure_level") not in census._LEVELS[:2] for row in rows):
        raise DevelopmentResourcePlanError("plan_exposure_level")
    if plan.get("plan_digest") != digest(rows):
        raise DevelopmentResourcePlanError("plan_digest")
    if summary.get("plan_digest") != plan.get("plan_digest"):
        raise DevelopmentResourcePlanError("summary_plan_digest")
    for key in (
        "market_price_read",
        "morphology_read",
        "label_or_reference_read",
        "candidate_output_read",
        "candidate_scoring",
        "preserved_2021_market_read",
        "2022_2024_read",
        "2025_read",
        "result_bearing_stage_authorized",
    ):
        if summary.get(key) is not False:
            raise DevelopmentResourcePlanError(f"summary_authority:{key}")
    return {
        "verified": True,
        "run_id": manifest.get("run_id"),
        "manifest_sha256": ready,
        "file_count": len(files),
        "row_count": len(rows),
        "primary_count": roles["primary"],
        "fallback_count": roles["fallback"],
        "unique_identity_count": len(set(identities)),
        "cell_counts": dict(sorted(cells.items())),
        "identity_digest": plan.get("identity_digest"),
        "plan_digest": plan.get("plan_digest"),
        "market_data_read": False,
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
        result = run_plan(
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

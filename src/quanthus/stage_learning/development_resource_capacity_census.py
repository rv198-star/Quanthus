"""P3-STAGE-013-A metadata-only era/board/exposure capacity census.

The runner reads only DWD listing identity/board metadata, trading-calendar metadata and
previously published Stage exposure metadata.  It never opens security bars, Structure
facts, reference evidence, labels, candidate outputs, protected evaluation market rows or
future outcome surfaces.
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
from quanthus.stage_learning.source import _SSE, _SZSE, _path_list, _source_roots
from quanthus.structure_v2_inputs import canonical, digest

DEFAULT_CONFIG = "config/stage-learning/stage-development-resource-capacity-census.v1.json"
SCHEMA = "quanthus.stage-development-resource-capacity-census.v1"
ARTIFACT_SCHEMA = "quanthus.stage-development-resource-capacity-census-artifact.v1"
SUMMARY_SCHEMA = "quanthus.stage-development-resource-capacity-census-summary.v1"
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")
_LEVELS = (
    "L0_METADATA_DISCOVERED",
    "L1_POOL_RESERVED_NOT_READ",
    "L2_DEVELOPMENT_MARKET_READ",
    "L3_THRESHOLD_OR_MODEL_BASIS_CONSUMED",
    "L4_REVIEW_OR_SCORING_CONSUMED",
    "L5_PRESERVED_EVALUATION",
)
_LEVEL_RANK = {value: index for index, value in enumerate(_LEVELS)}
_BOARD_TO_FAMILY = {
    "SSE_main": "MAIN",
    "SZ_main": "MAIN",
    "ChiNext": "CHINEXT",
    "STAR": "STAR",
}
_BOARD_TO_VENUE = {
    "SSE_main": _SSE,
    "SZ_main": _SZSE,
    "ChiNext": _SZSE,
    "STAR": _SSE,
}


class DevelopmentResourceCensusError(ValueError):
    """Raised when the metadata-only capacity contract is violated."""


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DevelopmentResourceCensusError(f"cannot_read_{label}") from exc
    if not isinstance(value, dict):
        raise DevelopmentResourceCensusError(f"{label}_must_be_object")
    return value


def _sha_bytes(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _sha_file(path: Path) -> str:
    try:
        return _sha_bytes(path.read_bytes())
    except OSError as exc:
        raise DevelopmentResourceCensusError(f"cannot_hash:{path.name}") from exc


def _day(value: object) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise DevelopmentResourceCensusError("invalid_date") from exc


def _interval_overlap(start: date, end: date, other_start: date, other_end: date) -> bool:
    return start <= other_end and other_start <= end


def load_config(path: Path) -> dict[str, Any]:
    raw = _object(path, "capacity_census_config")
    if raw.get("schema_version") != SCHEMA or raw.get("work_item") != "P3-STAGE-013-A":
        raise DevelopmentResourceCensusError("capacity_census_config_identity")
    authority = raw.get("authority")
    if not isinstance(authority, Mapping):
        raise DevelopmentResourceCensusError("capacity_census_authority")
    for key in (
        "identity_metadata_read",
        "listing_board_metadata_read",
        "trading_calendar_metadata_read",
        "prior_exposure_artifact_metadata_read",
    ):
        if authority.get(key) is not True:
            raise DevelopmentResourceCensusError("capacity_census_positive_authority")
    for key in (
        "market_price_read",
        "morphology_read",
        "label_read",
        "candidate_output_read",
        "candidate_scoring",
        "threshold_or_gate_change",
        "preserved_2021_market_read",
        "2022_2024_read",
        "2025_read",
        "daily_or_strategy",
        "trading",
    ):
        if authority.get(key) is not False:
            raise DevelopmentResourceCensusError("capacity_census_forbidden_authority")
    window = raw.get("window_contract")
    if not isinstance(window, Mapping) or window.get("open_sessions_per_window") != 61:
        raise DevelopmentResourceCensusError("capacity_census_window_contract")
    if (
        window.get("capacity_proxy_only") is not True
        or window.get("bar_completeness_claimed") is not False
    ):
        raise DevelopmentResourceCensusError("capacity_census_claim_ceiling")
    if raw.get("board_families") != {
        "MAIN": ["SSE_main", "SZ_main"],
        "CHINEXT": ["ChiNext"],
        "STAR": ["STAR"],
    }:
        raise DevelopmentResourceCensusError("capacity_census_board_families")
    eras = raw.get("eras")
    era_ids = (
        [item.get("id") for item in eras if isinstance(item, Mapping)]
        if isinstance(eras, list)
        else []
    )
    if era_ids != [
        "PRE_STAR",
        "STAR_PRE_CHINEXT_20PCT",
        "STAR_AND_CHINEXT_20PCT",
    ]:
        raise DevelopmentResourceCensusError("capacity_census_eras")
    artifacts = raw.get("exposure_artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != 5:
        raise DevelopmentResourceCensusError("capacity_census_exposure_artifacts")
    if (
        raw.get("runtime", {}).get("maximum_jobs") != 1
        or raw.get("runtime", {}).get("automatic_retries") != 0
    ):
        raise DevelopmentResourceCensusError("capacity_census_runtime_budget")
    return raw


def _verify_bound_file(root: Path, binding: Mapping[str, Any], label: str) -> dict[str, Any]:
    path = root / str(binding["path"])
    expected = str(binding["sha256"])
    if not _SHA.fullmatch(expected) or _sha_file(path) != expected:
        raise DevelopmentResourceCensusError(f"{label}_binding")
    return _object(path, label)


def _artifact_file(artifact_root: Path, manifest: Mapping[str, Any], name: str) -> dict[str, Any]:
    files = manifest.get("files")
    if not isinstance(files, Mapping) or not isinstance(files.get(name), str):
        raise DevelopmentResourceCensusError(f"artifact_file_not_bound:{name}")
    path = artifact_root / name
    raw = path.read_bytes()
    if _sha_bytes(raw) != files[name]:
        raise DevelopmentResourceCensusError(f"artifact_file_digest:{name}")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise DevelopmentResourceCensusError(f"artifact_file_shape:{name}")
    return value


def _load_exposures(
    training_root: Path, config: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], set[str], set[str]]:
    stages: list[dict[str, Any]] = []
    legacy_burn: set[str] = set()
    preserved_ids: set[str] = set()
    for index, item in enumerate(config["exposure_artifacts"]):
        artifact_root = (training_root / str(item["artifact_relative_path"])).resolve()
        manifest_path = artifact_root / "manifest.json"
        if _sha_file(manifest_path) != item["manifest_sha256"]:
            raise DevelopmentResourceCensusError(f"exposure_manifest:{item['id']}")
        manifest = _object(manifest_path, f"manifest_{index}")
        source = _artifact_file(artifact_root, manifest, "source-binding.json")
        selection = _artifact_file(artifact_root, manifest, str(item["selection_file"]))
        fresh = source.get(item["fresh_field"])
        basis = source.get(item["basis_field"])
        if not isinstance(fresh, Mapping) or not isinstance(basis, Mapping):
            raise DevelopmentResourceCensusError(f"exposure_pool_shape:{item['id']}")
        fresh_ids = {str(value) for value in fresh}
        basis_ids = {str(value) for value in basis}
        if not basis_ids <= fresh_ids:
            raise DevelopmentResourceCensusError(f"exposure_basis_not_fresh:{item['id']}")
        selected_raw = selection.get("selected")
        if not isinstance(selected_raw, list):
            raise DevelopmentResourceCensusError(f"exposure_selection_shape:{item['id']}")
        selected: dict[str, list[date]] = defaultdict(list)
        for row in selected_raw:
            if not isinstance(row, Mapping):
                raise DevelopmentResourceCensusError("exposure_selection_row")
            selected[str(row["security_id"])].append(_day(row["market_date"]))
        if not set(selected) <= basis_ids:
            raise DevelopmentResourceCensusError(f"selected_not_basis:{item['id']}")
        if index == 0:
            excluded = source.get("excluded_economic_ids")
            if not isinstance(excluded, list):
                raise DevelopmentResourceCensusError("stage007_excluded_identity_shape")
            legacy_burn.update(str(value) for value in excluded)
        legacy_burn.update(fresh_ids)
        stage = {
            "id": str(item["id"]),
            "fresh_ids": fresh_ids,
            "basis_ids": basis_ids,
            "selected": dict(selected),
            "read_start": _day(item["read_start"]),
            "report_start": _day(item["report_start"]),
            "report_end": _day(item["report_end"]),
            "manifest_sha256": str(item["manifest_sha256"]),
        }
        stages.append(stage)
        plan_file = item.get("preserved_plan_file")
        if plan_file is not None:
            plan = _artifact_file(artifact_root, manifest, str(plan_file))
            if digest(plan) != item["preserved_plan_digest"]:
                raise DevelopmentResourceCensusError("preserved_plan_digest")
            identities = plan.get("identities")
            if not isinstance(identities, list):
                raise DevelopmentResourceCensusError("preserved_plan_identities")
            preserved_ids = {
                str(row["security_id"])
                for row in identities
                if isinstance(row, Mapping)
            }
            if len(preserved_ids) != item["preserved_identity_count"]:
                raise DevelopmentResourceCensusError("preserved_identity_count")
            if plan.get("identity_digest") != item["preserved_identity_digest"]:
                raise DevelopmentResourceCensusError("preserved_identity_digest")
            if plan.get("data_read") is not False:
                raise DevelopmentResourceCensusError("preserved_plan_must_remain_unread")
    legacy_burn.update(preserved_ids)
    return stages, legacy_burn, preserved_ids


def _load_listing_and_calendar(
    training_root: Path, node: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, list[date]], dict[str, str]]:
    base, _correction, source_binding = _source_roots(training_root, node)
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise DevelopmentResourceCensusError("metadata_census_requires_duckdb") from exc
    payload = base / "payload"
    listing_path = payload / "listed_company/dwd_listing.parquet"
    calendar_paths = tuple(
        str(
            payload
            / "tradability_state/dwd_trading_calendar"
            / f"trade_date_year={year}/*.parquet"
        )
        for year in range(2012, 2021)
    )
    connection = duckdb.connect(":memory:")
    try:
        listing_rows = connection.execute(
            """
            SELECT security_id, listing_id, listing_code, venue_id, board_code,
                   CAST(effective_from AS DATE) AS effective_from,
                   CAST(effective_to AS DATE) AS effective_to
            FROM read_parquet(?)
            WHERE knowledge_to IS NULL
              AND board_code IN ('SSE_main','SZ_main','ChiNext','STAR')
              AND CAST(effective_from AS DATE) <= DATE '2020-12-31'
              AND (effective_to IS NULL OR CAST(effective_to AS DATE) > DATE '2012-01-01')
            ORDER BY security_id, effective_from, listing_id
            """,
            [str(listing_path)],
        ).fetchall()
        calendar_rows = connection.execute(
            f"""
            SELECT DISTINCT venue_id, CAST(trade_date AS DATE) AS trade_date
            FROM read_parquet({_path_list(calendar_paths)}, hive_partitioning=true)
            WHERE knowledge_to IS NULL AND is_open
              AND venue_id IN ('{_SSE}','{_SZSE}')
              AND CAST(trade_date AS DATE) BETWEEN DATE '2012-01-01' AND DATE '2020-12-31'
            ORDER BY venue_id, trade_date
            """
        ).fetchall()
    finally:
        connection.close()
    listings = [
        {
            "security_id": str(row[0]),
            "listing_id": str(row[1]),
            "listing_code": str(row[2]),
            "venue_id": str(row[3]),
            "board_code": str(row[4]),
            "effective_from": row[5],
            "effective_to": row[6],
        }
        for row in listing_rows
        if str(row[4]) in _BOARD_TO_FAMILY and str(row[3]) in {_SSE, _SZSE}
    ]
    calendars: dict[str, list[date]] = defaultdict(list)
    for venue, trade_day in calendar_rows:
        calendars[str(venue)].append(trade_day)
    return listings, dict(calendars), {
        "base_manifest_sha256": str(source_binding["base"]["manifest_sha256"]),
        "correction_manifest_sha256": str(source_binding["correction"]["manifest_sha256"]),
    }


def _windows(
    config: Mapping[str, Any], calendars: Mapping[str, Sequence[date]]
) -> list[dict[str, Any]]:
    size = int(config["window_contract"]["open_sessions_per_window"])
    output: list[dict[str, Any]] = []
    for era in config["eras"]:
        era_start, era_end = _day(era["start"]), _day(era["end"])
        eligible = set(str(value) for value in era["eligible_board_families"])
        for board_code, family in _BOARD_TO_FAMILY.items():
            if family not in eligible:
                continue
            venue = _BOARD_TO_VENUE[board_code]
            dates = [value for value in calendars.get(venue, ()) if era_start <= value <= era_end]
            full = len(dates) // size
            for index in range(full):
                chunk = dates[index * size : (index + 1) * size]
                output.append(
                    {
                        "window_id": f"{era['id']}|{board_code}|{index + 1:02d}",
                        "era": str(era["id"]),
                        "board_family": family,
                        "board_code": board_code,
                        "venue_id": venue,
                        "window_index": index + 1,
                        "start": chunk[0],
                        "end": chunk[-1],
                        "open_session_count": len(chunk),
                    }
                )
    return output


def _active_for_window(listing: Mapping[str, Any], window: Mapping[str, Any]) -> bool:
    return (
        listing["board_code"] == window["board_code"]
        and listing["venue_id"] == window["venue_id"]
        and listing["effective_from"] <= window["start"]
        and (listing["effective_to"] is None or window["end"] < listing["effective_to"])
    )


def _window_level(
    identity: str,
    window: Mapping[str, Any],
    stages: Sequence[Mapping[str, Any]],
    preserved_ids: set[str],
) -> str:
    if identity in preserved_ids:
        return "L5_PRESERVED_EVALUATION"
    level = "L0_METADATA_DISCOVERED"
    start, end = window["start"], window["end"]
    for stage in stages:
        if identity in stage["fresh_ids"] and _LEVEL_RANK[level] < 1:
            level = "L1_POOL_RESERVED_NOT_READ"
        if identity not in stage["basis_ids"]:
            continue
        if _interval_overlap(start, end, stage["read_start"], stage["report_end"]):
            level = max(level, "L2_DEVELOPMENT_MARKET_READ", key=_LEVEL_RANK.__getitem__)
        if _interval_overlap(start, end, stage["report_start"], stage["report_end"]):
            level = max(level, "L3_THRESHOLD_OR_MODEL_BASIS_CONSUMED", key=_LEVEL_RANK.__getitem__)
        if any(start <= selected <= end for selected in stage["selected"].get(identity, ())):
            level = "L4_REVIEW_OR_SCORING_CONSUMED"
    return level


def _identity_highest_levels(
    all_ids: set[str], stages: Sequence[Mapping[str, Any]], preserved_ids: set[str]
) -> dict[str, str]:
    levels = {identity: "L0_METADATA_DISCOVERED" for identity in all_ids}
    for stage in stages:
        for identity in stage["fresh_ids"] & all_ids:
            levels[identity] = max(
                levels[identity],
                "L1_POOL_RESERVED_NOT_READ",
                key=_LEVEL_RANK.__getitem__,
            )
        for identity in stage["basis_ids"] & all_ids:
            levels[identity] = max(
                levels[identity],
                "L3_THRESHOLD_OR_MODEL_BASIS_CONSUMED",
                key=_LEVEL_RANK.__getitem__,
            )
        for identity in set(stage["selected"]) & all_ids:
            levels[identity] = "L4_REVIEW_OR_SCORING_CONSUMED"
    for identity in preserved_ids & all_ids:
        levels[identity] = "L5_PRESERVED_EVALUATION"
    return levels


def run_census(
    *, config_path: Path, training_root: Path, output_root: Path, code_revision: str
) -> dict[str, Any]:
    if not _REVISION.fullmatch(code_revision):
        raise DevelopmentResourceCensusError("exact_code_revision_required")
    root = find_project_root()
    config = load_config(config_path)
    model = _verify_bound_file(root, config["bindings"]["resource_model"], "resource_model")
    node = _verify_bound_file(root, config["bindings"]["training_node"], "training_node")
    if (
        model.get("selected_disposition")
        != "PROCEED_TO_METADATA_ONLY_ERA_BOARD_EXPOSURE_CAPACITY_CENSUS"
    ):
        raise DevelopmentResourceCensusError("resource_model_not_authorized")
    training_root = training_root.expanduser().resolve()
    stages, legacy_burn, preserved_ids = _load_exposures(training_root, config)
    listings, calendars, source_binding = _load_listing_and_calendar(training_root, node)
    windows = _windows(config, calendars)

    by_board: dict[str, list[dict[str, Any]]] = defaultdict(list)
    all_ids: set[str] = set()
    for listing in listings:
        by_board[listing["board_code"]].append(listing)
        all_ids.add(listing["security_id"])

    token_rows: list[dict[str, Any]] = []
    ambiguous_tokens = 0
    for window in windows:
        candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for listing in by_board[window["board_code"]]:
            if _active_for_window(listing, window):
                candidates[listing["security_id"]].append(listing)
        for identity, active in candidates.items():
            unique_listings = {(row["listing_id"], row["listing_code"]) for row in active}
            if len(unique_listings) != 1:
                ambiguous_tokens += 1
                continue
            level = _window_level(identity, window, stages, preserved_ids)
            old_available = identity not in legacy_burn
            new_available = _LEVEL_RANK[level] <= _LEVEL_RANK["L1_POOL_RESERVED_NOT_READ"]
            token_rows.append(
                {
                    "identity": identity,
                    "window_id": window["window_id"],
                    "era": window["era"],
                    "board_family": window["board_family"],
                    "board_code": window["board_code"],
                    "exposure_level": level,
                    "old_whole_identity_available": old_available,
                    "exposure_aware_available": new_available,
                    "released_vs_old": (not old_available) and new_available,
                }
            )

    era_defs = {str(item["id"]): item for item in config["eras"]}
    all_families = tuple(config["board_families"])
    matrix: list[dict[str, Any]] = []
    for era_id, era in era_defs.items():
        start, end = _day(era["start"]), _day(era["end"])
        eligible = set(str(value) for value in era["eligible_board_families"])
        for family in all_families:
            active_ids = {
                row["security_id"]
                for row in listings
                if _BOARD_TO_FAMILY[row["board_code"]] == family
                and row["effective_from"] <= end
                and (row["effective_to"] is None or start < row["effective_to"])
            }
            rows = [
                row for row in token_rows if row["era"] == era_id and row["board_family"] == family
            ]
            usable_ids = {row["identity"] for row in rows}
            counts = Counter(row["exposure_level"] for row in rows)
            available_ids = {row["identity"] for row in rows if row["exposure_aware_available"]}
            preserved_overlap = usable_ids & preserved_ids
            matrix.append(
                {
                    "era": era_id,
                    "board_family": family,
                    "structurally_present": family in eligible,
                    "active_identity_count": len(active_ids) if family in eligible else 0,
                    "usable_identity_count": len(usable_ids) if family in eligible else 0,
                    "usable_window_count": len(
                        {
                            row["window_id"]
                            for row in rows
                        }
                    ) if family in eligible else 0,
                    "identity_window_capacity": len(rows) if family in eligible else 0,
                    "exposure_counts": {level: counts[level] for level in _LEVELS},
                    "legacy_whole_identity_available_capacity": sum(
                        1 for row in rows if row["old_whole_identity_available"]
                    ),
                    "exposure_aware_available_capacity": sum(
                        1 for row in rows if row["exposure_aware_available"]
                    ),
                    "released_capacity_vs_whole_identity": sum(
                        1 for row in rows if row["released_vs_old"]
                    ),
                    "available_unique_identity_count": len(available_ids),
                    "preserved_2021_identity_overlap": len(preserved_overlap),
                    "capacity_loss_reasons": {
                        "board_absent_in_era": 1 if family not in eligible else 0,
                        "listing_history_insufficient_for_full_61_session_window": (
                            max(0, len(active_ids) - len(usable_ids)) if family in eligible else 0
                        ),
                        "protected_l5_identity_window": counts["L5_PRESERVED_EVALUATION"],
                        "previously_consumed_l2_l4_identity_window": sum(
                            counts[level] for level in _LEVELS[2:5]
                        ),
                    },
                }
            )

    identity_counts: dict[str, Counter[str]] = defaultdict(Counter)
    available_windows: Counter[str] = Counter()
    for row in token_rows:
        identity_counts[row["identity"]][row["exposure_level"]] += 1
        if row["exposure_aware_available"]:
            available_windows[row["identity"]] += 1
    identity_levels = _identity_highest_levels(all_ids, stages, preserved_ids)
    identity_summary = [
        {
            "security_id": identity,
            "highest_known_exposure": identity_levels[identity],
            "window_counts": {level: identity_counts[identity][level] for level in _LEVELS},
            "exposure_aware_available_window_count": available_windows[identity],
        }
        for identity in sorted(all_ids)
    ]
    level_identity_counts = Counter(identity_levels.values())
    stage_ledger = [
        {
            "work_item": stage["id"],
            "fresh_identity_count": len(stage["fresh_ids"]),
            "basis_identity_count": len(stage["basis_ids"]),
            "selected_identity_count": len(stage["selected"]),
            "read_start": stage["read_start"],
            "report_start": stage["report_start"],
            "report_end": stage["report_end"],
            "manifest_sha256": stage["manifest_sha256"],
        }
        for stage in stages
    ]
    window_payload = {
        "schema_version": "quanthus.stage-development-resource-windows.v1",
        "open_sessions_per_window": config["window_contract"]["open_sessions_per_window"],
        "windows": windows,
    }
    matrix_payload = {
        "schema_version": "quanthus.stage-development-resource-capacity-matrix.v1",
        "cells": matrix,
    }
    exposure_payload = {
        "schema_version": "quanthus.stage-development-resource-exposure-ledger.v1",
        "stages": stage_ledger,
        "legacy_whole_identity_burn_count": len(legacy_burn),
        "preserved_2021_identity_count": len(preserved_ids),
        "preserved_2021_market_data_read": False,
        "identity_highest_exposure_counts": {
            level: level_identity_counts[level] for level in _LEVELS
        },
        "window_exposure_counts": dict(Counter(row["exposure_level"] for row in token_rows)),
    }
    identity_payload = {
        "schema_version": "quanthus.stage-development-resource-identity-window-summary.v1",
        "identities": identity_summary,
    }
    total_old = sum(row["legacy_whole_identity_available_capacity"] for row in matrix)
    total_new = sum(row["exposure_aware_available_capacity"] for row in matrix)
    released = sum(row["released_capacity_vs_whole_identity"] for row in matrix)
    summary = {
        "schema_version": SUMMARY_SCHEMA,
        "work_item": "P3-STAGE-013-A",
        "phase": "METADATA_ONLY_ERA_BOARD_EXPOSURE_CAPACITY_CENSUS",
        "code_revision": code_revision,
        "config_digest": digest(config),
        "resource_model_digest": digest(model),
        "source_binding": source_binding,
        "economic_identity_count": len(all_ids),
        "window_count": len(windows),
        "identity_window_capacity": len(token_rows),
        "ambiguous_identity_window_count": ambiguous_tokens,
        "legacy_whole_identity_available_capacity": total_old,
        "exposure_aware_available_capacity": total_new,
        "released_capacity_vs_whole_identity": released,
        "released_unique_identity_count": len(
            {row["identity"] for row in token_rows if row["released_vs_old"]}
        ),
        "repeated_identity_with_multiple_available_windows_count": sum(
            1 for count in available_windows.values() if count > 1
        ),
        "preserved_2021_identity_count": len(preserved_ids),
        "capacity_matrix_digest": digest(matrix_payload),
        "exposure_ledger_digest": digest(exposure_payload),
        "windows_digest": digest(window_payload),
        "identity_window_summary_digest": digest(identity_payload),
        "market_price_read": False,
        "morphology_read": False,
        "label_read": False,
        "candidate_output_read": False,
        "candidate_scoring": False,
        "preserved_2021_market_read": False,
        "2022_2024_read": False,
        "2025_read": False,
        "quota_frozen": False,
        "result_bearing_stage_authorized": False,
    }
    run_id = "stage-development-resource-census-" + datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    run_root = output_root.expanduser().resolve() / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    payloads = {
        "windows.json": canonical(window_payload),
        "capacity-matrix.json": canonical(matrix_payload),
        "exposure-ledger.json": canonical(exposure_payload),
        "identity-window-summary.json": canonical(identity_payload),
        "summary.json": canonical(summary),
    }
    for name, raw in payloads.items():
        (run_root / name).write_bytes(raw)
    manifest = {
        "schema_version": ARTIFACT_SCHEMA,
        "run_id": run_id,
        "code_revision": code_revision,
        "config_digest": digest(config),
        "files": {name: _sha_bytes(raw) for name, raw in payloads.items()},
        "authority": {
            "market_price_read": False,
            "morphology_read": False,
            "label_read": False,
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
        raise DevelopmentResourceCensusError("artifact_ready_digest")
    if manifest.get("schema_version") != ARTIFACT_SCHEMA:
        raise DevelopmentResourceCensusError("artifact_schema")
    if code_revision is not None and manifest.get("code_revision") != code_revision:
        raise DevelopmentResourceCensusError("artifact_code_revision")
    expected = {
        "windows.json",
        "capacity-matrix.json",
        "exposure-ledger.json",
        "identity-window-summary.json",
        "summary.json",
    }
    files = manifest.get("files")
    if not isinstance(files, Mapping) or set(files) != expected:
        raise DevelopmentResourceCensusError("artifact_files")
    payloads: dict[str, dict[str, Any]] = {}
    for name in expected:
        raw = (root / name).read_bytes()
        if _sha_bytes(raw) != files[name]:
            raise DevelopmentResourceCensusError(f"artifact_file_digest:{name}")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise DevelopmentResourceCensusError(f"artifact_file_shape:{name}")
        payloads[name] = value
    summary = payloads["summary.json"]
    for key in (
        "market_price_read",
        "morphology_read",
        "label_read",
        "candidate_output_read",
        "candidate_scoring",
        "preserved_2021_market_read",
        "2022_2024_read",
        "2025_read",
        "quota_frozen",
        "result_bearing_stage_authorized",
    ):
        if summary.get(key) is not False:
            raise DevelopmentResourceCensusError(f"artifact_authority:{key}")
    if digest(payloads["capacity-matrix.json"]) != summary.get("capacity_matrix_digest"):
        raise DevelopmentResourceCensusError("capacity_matrix_digest")
    if digest(payloads["exposure-ledger.json"]) != summary.get("exposure_ledger_digest"):
        raise DevelopmentResourceCensusError("exposure_ledger_digest")
    if digest(payloads["windows.json"]) != summary.get("windows_digest"):
        raise DevelopmentResourceCensusError("windows_digest")
    if digest(payloads["identity-window-summary.json"]) != summary.get(
        "identity_window_summary_digest"
    ):
        raise DevelopmentResourceCensusError("identity_window_summary_digest")
    return {
        "verified": True,
        "run_id": manifest["run_id"],
        "manifest_sha256": ready,
        "file_count": len(expected),
        "economic_identity_count": summary["economic_identity_count"],
        "window_count": summary["window_count"],
        "identity_window_capacity": summary["identity_window_capacity"],
        "legacy_whole_identity_available_capacity": summary[
            "legacy_whole_identity_available_capacity"
        ],
        "exposure_aware_available_capacity": summary["exposure_aware_available_capacity"],
        "released_capacity_vs_whole_identity": summary["released_capacity_vs_whole_identity"],
        "released_unique_identity_count": summary["released_unique_identity_count"],
        "preserved_2021_identity_count": summary["preserved_2021_identity_count"],
        "capacity_matrix": payloads["capacity-matrix.json"]["cells"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--config", type=Path, default=Path(DEFAULT_CONFIG))
    run.add_argument("--training-root", type=Path, required=True)
    run.add_argument("--output-root", type=Path, required=True)
    run.add_argument("--code-revision", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--artifact", type=Path, required=True)
    verify.add_argument("--code-revision")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "run":
            result = run_census(
                config_path=args.config,
                training_root=args.training_root,
                output_root=args.output_root,
                code_revision=args.code_revision,
            )
        else:
            result = verify_artifact(args.artifact, code_revision=args.code_revision)
    except DevelopmentResourceCensusError as exc:
        print(f"stage-development-resource-census: {exc}", flush=True)
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

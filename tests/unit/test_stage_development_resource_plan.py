from __future__ import annotations

import json
from collections import Counter
from datetime import date, timedelta

import pytest

from quanthus.policy import find_project_root
from quanthus.stage_learning.development_resource_plan import (
    DevelopmentResourcePlanError,
    _window_slots,
    load_config,
    select_exact_plan,
)

ROOT = find_project_root()
CONFIG = ROOT / "config/stage-learning/stage-development-resource-plan.v1.json"
QUOTA = ROOT / "config/stage-learning/stage-development-resource-quota-freeze.v1.json"
REVISION_R1 = (
    ROOT / "config/stage-learning/stage-development-resource-quota-revision-r1.v1.json"
)


def _window(era: str, family: str, index: int) -> dict:
    start = date(2018, 1, 1) + timedelta(days=70 * index)
    return {
        "window_id": f"{era}|{family}|{index:02d}",
        "era": era,
        "board_family": family,
        "board_code": {"MAIN": "SSE_main", "CHINEXT": "ChiNext", "STAR": "STAR"}[family],
        "venue_id": "venue-test",
        "window_index": index,
        "start": start,
        "end": start + timedelta(days=60),
        "open_session_count": 61,
    }


def _synthetic_surface() -> tuple[list[dict], list[dict], dict]:
    quota = json.loads(QUOTA.read_text(encoding="utf-8"))
    windows: list[dict] = []
    tokens: list[dict] = []
    for cell_index, cell in enumerate(quota["cell_quotas"], start=1):
        era = cell["era"]
        family = cell["board_family"]
        cell_windows = [_window(era, family, index) for index in range(1, 5)]
        windows.extend(cell_windows)
        for identity_index in range(1, 49):
            identity = f"sec-{cell_index:02d}-{identity_index:03d}"
            window = cell_windows[(identity_index - 1) % len(cell_windows)]
            tokens.append(
                {
                    "security_id": identity,
                    "listing_id": f"listing-{identity}",
                    "security_code": f"{cell_index:02d}{identity_index:04d}"[-6:],
                    "era": era,
                    "board_family": family,
                    "board_code": window["board_code"],
                    "venue_id": window["venue_id"],
                    "window_id": window["window_id"],
                    "window_start": window["start"],
                    "window_end": window["end"],
                    "open_session_count": 61,
                    "exposure_level": "L0_METADATA_DISCOVERED",
                }
            )
    return tokens, windows, quota


def test_config_freezes_metadata_only_single_job() -> None:
    value = load_config(CONFIG)
    assert value["selection"]["total_reserve_rows"] == 256
    assert value["selection"]["total_primary_rows"] == 192
    assert value["selection"]["total_fallback_rows"] == 64
    assert value["selection"]["global_identity_unique"] is True
    assert value["runtime"] == {
        "maximum_jobs": 1,
        "automatic_retries": 0,
        "worker_wall_seconds": 600,
    }
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
        assert value["authority"][key] is False


def test_window_slots_spread_when_windows_exceed_reserve() -> None:
    windows = [_window("PRE_STAR", "MAIN", index) for index in range(1, 61)]
    selected = _window_slots(windows, 32)
    indices = [int(str(row["window_id"]).rsplit("|", 1)[1]) for row in selected]
    assert len(indices) == 32
    assert len(set(indices)) == 32
    assert min(indices) <= 2
    assert max(indices) >= 59


def test_exact_plan_is_balanced_and_globally_identity_unique() -> None:
    tokens, windows, quota = _synthetic_surface()
    rows = select_exact_plan(tokens=tokens, windows=windows, quota=quota)
    assert len(rows) == 256
    assert len({row["security_id"] for row in rows}) == 256
    cells = {}
    for row in rows:
        key = (row["era"], row["board_family"])
        cells.setdefault(key, []).append(row)
    assert len(cells) == 8
    for selected in cells.values():
        assert len(selected) == 32
        assert sum(row["role"] == "primary" for row in selected) == 24
        assert sum(row["role"] == "fallback" for row in selected) == 8
        assert all(row["prior_exposure_level"] == "L0_METADATA_DISCOVERED" for row in selected)


def test_r1_waterfill_handles_sparse_launch_window_without_changing_cell_quota() -> None:
    tokens, windows, quota = _synthetic_surface()
    revision = json.loads(REVISION_R1.read_text(encoding="utf-8"))
    scarce_cell = quota["selection_policy"]["scarcity_order_for_global_identity_allocation"][0]
    era, family = scarce_cell.split("/", 1)
    retained = [
        row
        for row in tokens
        if not (row["era"] == era and row["board_family"] == family)
    ]
    cell_windows = [
        row for row in windows if row["era"] == era and row["board_family"] == family
    ]
    sparse_counts = [2, 10, 18, 18]
    sparse: list[dict] = []
    identity_index = 0
    for window, count in zip(cell_windows, sparse_counts, strict=True):
        for _ in range(count):
            identity_index += 1
            identity = f"scarce-{identity_index:03d}"
            sparse.append(
                {
                    "security_id": identity,
                    "listing_id": f"listing-{identity}",
                    "security_code": f"9{identity_index:05d}"[-6:],
                    "era": era,
                    "board_family": family,
                    "board_code": window["board_code"],
                    "venue_id": window["venue_id"],
                    "window_id": window["window_id"],
                    "window_start": window["start"],
                    "window_end": window["end"],
                    "open_session_count": 61,
                    "exposure_level": "L0_METADATA_DISCOVERED",
                }
            )
    with pytest.raises(DevelopmentResourcePlanError, match="cell_global_identity_shortfall"):
        select_exact_plan(tokens=[*retained, *sparse], windows=windows, quota=quota)
    rows = select_exact_plan(
        tokens=[*retained, *sparse],
        windows=windows,
        quota=quota,
        temporal_revision=revision,
    )
    selected = [
        row for row in rows if row["era"] == era and row["board_family"] == family
    ]
    assert len(selected) == 32
    assert len({row["security_id"] for row in selected}) == 32
    by_window = Counter(row["window_id"] for row in selected)
    assert len(by_window) == 4
    assert min(by_window.values()) == 2
    assert sum(by_window.values()) == 32


def test_exact_plan_fails_closed_when_global_unique_capacity_is_missing() -> None:
    tokens, windows, quota = _synthetic_surface()
    scarce_cell = quota["selection_policy"]["scarcity_order_for_global_identity_allocation"][0]
    era, family = scarce_cell.split("/", 1)
    kept = [
        row
        for row in tokens
        if not (row["era"] == era and row["board_family"] == family)
    ]
    scarce_rows = [
        row for row in tokens if row["era"] == era and row["board_family"] == family
    ][:20]
    with pytest.raises(DevelopmentResourcePlanError, match="cell_global_identity_shortfall"):
        select_exact_plan(tokens=[*kept, *scarce_rows], windows=windows, quota=quota)

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from quanthus.policy import find_project_root
from quanthus.stage_learning import development_resource_capacity_census as census

ROOT = find_project_root()
CONFIG = ROOT / "config/stage-learning/stage-development-resource-capacity-census.v1.json"
REVISION = "a" * 40


def _dates(start: date, count: int = 61) -> list[date]:
    return [start + timedelta(days=index) for index in range(count)]


def test_capacity_census_contract_is_metadata_only_and_one_shot() -> None:
    value = census.load_config(CONFIG)
    assert value["state"] == "PREREGISTERED_METADATA_ONLY_BEFORE_CENSUS"
    assert value["window_contract"]["open_sessions_per_window"] == 61
    assert value["window_contract"]["capacity_proxy_only"] is True
    assert value["window_contract"]["bar_completeness_claimed"] is False
    assert value["runtime"] == {
        "maximum_jobs": 1,
        "automatic_retries": 0,
        "wall_seconds": 900,
    }
    authority = value["authority"]
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
        assert authority[key] is False


def test_window_builder_respects_era_and_board_structure() -> None:
    config = census.load_config(CONFIG)
    calendars = {
        census._SSE: (
            _dates(date(2012, 1, 1))
            + _dates(date(2019, 7, 22))
            + _dates(date(2020, 8, 24))
        ),
        census._SZSE: (
            _dates(date(2012, 1, 1))
            + _dates(date(2019, 7, 22))
            + _dates(date(2020, 8, 24))
        ),
    }
    windows = census._windows(config, calendars)
    assert all(item["open_session_count"] == 61 for item in windows)
    assert not any(
        item["era"] == "PRE_STAR" and item["board_family"] == "STAR"
        for item in windows
    )
    assert {
        (item["era"], item["board_family"])
        for item in windows
    } >= {
        ("PRE_STAR", "MAIN"),
        ("PRE_STAR", "CHINEXT"),
        ("STAR_PRE_CHINEXT_20PCT", "STAR"),
        ("STAR_AND_CHINEXT_20PCT", "STAR"),
    }


def test_window_exposure_is_local_but_l5_is_global() -> None:
    stage = {
        "fresh_ids": {"identity-a"},
        "basis_ids": {"identity-a"},
        "selected": {"identity-a": [date(2019, 9, 1)]},
        "read_start": date(2018, 9, 1),
        "report_start": date(2019, 1, 1),
        "report_end": date(2019, 12, 31),
    }
    old_window = {"start": date(2016, 1, 1), "end": date(2016, 3, 31)}
    report_window = {"start": date(2019, 8, 1), "end": date(2019, 10, 31)}
    assert census._window_level("identity-a", old_window, [stage], set()) == (
        "L1_POOL_RESERVED_NOT_READ"
    )
    assert census._window_level("identity-a", report_window, [stage], set()) == (
        "L4_REVIEW_OR_SCORING_CONSUMED"
    )
    assert census._window_level("identity-a", old_window, [stage], {"identity-a"}) == (
        "L5_PRESERVED_EVALUATION"
    )


def test_run_census_releases_disjoint_windows_without_opening_market_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage = {
        "id": "synthetic-stage",
        "fresh_ids": {"identity-main"},
        "basis_ids": {"identity-main"},
        "selected": {"identity-main": [date(2019, 8, 15)]},
        "read_start": date(2019, 1, 1),
        "report_start": date(2019, 1, 1),
        "report_end": date(2019, 12, 31),
        "manifest_sha256": "sha256:" + "1" * 64,
    }
    monkeypatch.setattr(
        census,
        "_load_exposures",
        lambda _root, _config: ([stage], {"identity-main", "identity-star"}, {"identity-star"}),
    )
    listings = [
        {
            "security_id": "identity-main",
            "listing_id": "listing-main",
            "listing_code": "600001",
            "venue_id": census._SSE,
            "board_code": "SSE_main",
            "effective_from": date(2010, 1, 1),
            "effective_to": None,
        },
        {
            "security_id": "identity-chi",
            "listing_id": "listing-chi",
            "listing_code": "300001",
            "venue_id": census._SZSE,
            "board_code": "ChiNext",
            "effective_from": date(2010, 1, 1),
            "effective_to": None,
        },
        {
            "security_id": "identity-star",
            "listing_id": "listing-star",
            "listing_code": "688001",
            "venue_id": census._SSE,
            "board_code": "STAR",
            "effective_from": date(2019, 7, 22),
            "effective_to": None,
        },
    ]
    calendars = {
        census._SSE: (
            _dates(date(2012, 1, 1))
            + _dates(date(2019, 7, 22))
            + _dates(date(2020, 8, 24))
        ),
        census._SZSE: (
            _dates(date(2012, 1, 1))
            + _dates(date(2019, 7, 22))
            + _dates(date(2020, 8, 24))
        ),
    }
    monkeypatch.setattr(
        census,
        "_load_listing_and_calendar",
        lambda _root, _node: (
            listings,
            calendars,
            {
                "base_manifest_sha256": "sha256:" + "2" * 64,
                "correction_manifest_sha256": "sha256:" + "3" * 64,
            },
        ),
    )
    output = tmp_path / "runs"
    output.mkdir()
    result = census.run_census(
        config_path=CONFIG,
        training_root=tmp_path / "training",
        output_root=output,
        code_revision=REVISION,
    )
    assert result["market_price_read"] is False
    assert result["preserved_2021_market_read"] is False
    assert result["released_capacity_vs_whole_identity"] > 0
    assert result["exposure_aware_available_capacity"] > (
        result["legacy_whole_identity_available_capacity"]
    )
    verified = census.verify_artifact(Path(result["run_root"]), code_revision=REVISION)
    assert verified["verified"] is True
    assert verified["preserved_2021_identity_count"] == 1
    summary = json.loads((Path(result["run_root"]) / "summary.json").read_text())
    assert summary["quota_frozen"] is False
    assert summary["result_bearing_stage_authorized"] is False

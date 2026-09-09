from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from quanthus.policy import find_project_root
from quanthus.stage_learning.capacity_census import (
    _capacity_for_population,
    load_capacity_census_config,
)
from quanthus.stage_learning.core import ActivityObservation
from quanthus.structure_v2 import EpisodeObservation

ROOT = find_project_root()


def _episode(
    episode_id: str,
    observation: str,
    day: int,
    *,
    hold: int = 0,
    phase: str = "active",
    close: str = "11",
) -> EpisodeObservation:
    return EpisodeObservation(
        episode_id=episode_id,
        security_code="000001",
        market_date=date(2020, 1, day),
        scale=20,
        started_at=date(2020, 1, 2),
        anchor_key=f"anchor-{episode_id}",
        anchor_revision=f"revision-{episode_id}",
        frozen_level=Decimal("10"),
        frozen_low=Decimal("8"),
        phase=phase,
        observation=observation,
        exchange_session_age=day,
        observed_bar_count=day,
        hold_count=hold,
        close=Decimal(close),
        source_member_digest=f"member-{episode_id}-{day}",
        interaction_ref=None,
        previous_loss_ref=None,
    )


def _activity(day: int, ratio: str | None) -> ActivityObservation:
    return ActivityObservation(
        security_code="000001",
        market_date=date(2020, 1, day),
        window=20,
        available=ratio is not None,
        volume_ratio=None if ratio is None else Decimal(ratio),
        reason="available" if ratio is not None else "missing",
        source_member_digest=f"activity-{day}",
    )


def _sampling() -> dict:
    return {
        "target_caps": {
            "started_activity": 4,
            "early_held": 3,
            "near_anchor_loss_negative": 1,
            "reclaimed_ambiguity": 1,
            "mature_held": 1,
        },
        "started_activity_target_caps": {
            "missing_or_lt_1": 1,
            "1_to_1_25": 1,
            "1_25_to_1_5": 1,
            "gte_1_5": 1,
        },
        "early_held_target_caps": {"1": 1, "2": 1, "3": 1},
    }


def test_capacity_census_config_is_development_only_and_bounded() -> None:
    config = load_capacity_census_config(
        ROOT / "config/stage-learning/sample-capacity-census.v1.json"
    )
    authority = config["authority"]
    assert authority["development_only"] is True
    assert authority["development_data_read_authorized"] is True
    assert authority["arbitration_access_authorized"] is False
    assert authority["permanent_blind_access_authorized"] is False
    assert authority["candidate_prediction_authorized"] is False
    assert authority["semantic_panel_authorized"] is False
    assert authority["holdout_population_selection_authorized"] is False
    assert config["nested_population_sizes"] == [64, 128, 256, 512]
    assert config["maximum_runtime_population"] == 512
    window = config["development_window"]
    assert window["report_start"].startswith("2020-")
    assert window["report_end"].startswith("2021-")


def test_capacity_protocol_order_accounts_for_episode_reuse() -> None:
    episodes = [
        _episode("s0", "started", 2),
        _episode("s1", "started", 3),
        _episode("s2", "started", 4),
        _episode("shared", "started", 5),
        _episode("h1", "held", 6, hold=1),
        _episode("h2", "held", 7, hold=2),
        _episode("h3", "held", 8, hold=3),
        _episode("loss", "lost", 9, phase="below", close="9.9"),
        _episode("reclaim", "reclaimed", 10, phase="reclaimed"),
        _episode("shared", "held", 11, hold=40),
    ]
    activity = [
        _activity(2, None),
        _activity(3, "1.10"),
        _activity(4, "1.30"),
        _activity(5, "1.60"),
    ]
    result = _capacity_for_population(
        episodes,
        activity,
        security_codes={"000001"},
        report_start=date(2020, 1, 2),
        report_end=date(2020, 1, 31),
        sampling=_sampling(),
        seed="test",
    )
    assert result["raw_unique_episode_capacity"]["mature_held"] == 1
    assert result["realized_after_protocol_order"]["mature_held"] == 0
    assert result["target_cap_shortfalls"] == {"mature_held": 1}
    assert result["all_target_caps_met"] is False


def test_capacity_protocol_meets_caps_when_distinct_episode_exists() -> None:
    episodes = [
        _episode("s0", "started", 2),
        _episode("s1", "started", 3),
        _episode("s2", "started", 4),
        _episode("s3", "started", 5),
        _episode("h1", "held", 6, hold=1),
        _episode("h2", "held", 7, hold=2),
        _episode("h3", "held", 8, hold=3),
        _episode("loss", "lost", 9, phase="below", close="9.9"),
        _episode("reclaim", "reclaimed", 10, phase="reclaimed"),
        _episode("mature", "held", 11, hold=40),
    ]
    activity = [
        _activity(2, None),
        _activity(3, "1.10"),
        _activity(4, "1.30"),
        _activity(5, "1.60"),
    ]
    result = _capacity_for_population(
        episodes,
        activity,
        security_codes={"000001"},
        report_start=date(2020, 1, 2),
        report_end=date(2020, 1, 31),
        sampling=_sampling(),
        seed="test",
    )
    assert result["target_cap_shortfalls"] == {}
    assert result["sub_stratum_shortfalls"] == {}
    assert result["all_target_caps_met"] is True
    assert result["selected_unique_episode_count"] == 10


def test_capacity_config_is_not_holdout_authority() -> None:
    raw = json.loads(
        (ROOT / "config/stage-learning/sample-capacity-census.v1.json").read_text()
    )
    assert "2022" not in json.dumps(raw["development_window"])
    assert raw["analysis"]["do_not_extrapolate_as_qualification_evidence"] is True
    assert raw["artifact"]["market_values_published"] is False
    assert raw["artifact"]["security_codes_published"] is False
    assert raw["artifact"]["episode_rows_published"] is False

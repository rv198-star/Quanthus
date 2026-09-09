from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from quanthus.policy import find_project_root
from quanthus.stage_learning.core import (
    ActivityObservation,
    PredicateState,
    StageCandidateObservation,
)
from quanthus.stage_learning.second_group_profile import (
    _summarize_profiles,
    build_second_group_profiles,
    load_second_group_profile_config,
)
from quanthus.structure_v2 import EpisodeObservation, StructureV2Result
from quanthus.structure_v2_inputs import Admission, DailyInput

ROOT = find_project_root()
SHA = "sha256:" + "a" * 64
TZ = timezone(timedelta(hours=8))


def _admission(
    offset: int,
    close: str,
    *,
    admitted: bool = True,
    reset_reason: str | None = None,
) -> Admission:
    day = date(2020, 1, 2) + timedelta(days=offset)
    value = Decimal(close)
    row = DailyInput(
        security_code="000001",
        market_date=day,
        session_index=offset,
        cutoff=datetime(day.year, day.month, day.day, 15, 30, tzinfo=TZ),
        security_id="security-A",
        listing_id="listing-A",
        exchange="SZSE",
        high=value + Decimal("1"),
        low=value - Decimal("1"),
        close=value,
        volume=Decimal("1000"),
        source_record_id=f"source-{offset}",
        source_revision="revision-v1",
        source_artifact_sha256=SHA,
        dwd_known_from="2026-07-01T00:00:00+00:00",
        coordinate_epoch="epoch-v1",
        finality="final",
        quality_reason=None if admitted else "missing_daily_bar",
        reset_reason=reset_reason,
        calendar_member_digest=SHA,
        action_member_digest=SHA,
    )
    return Admission(
        row,
        admitted,
        "admitted_retrospective_only" if admitted else "missing_daily_bar",
        False,
    )


def _episode(acceptance_day: date) -> EpisodeObservation:
    return EpisodeObservation(
        episode_id="episode-A",
        security_code="000001",
        market_date=acceptance_day,
        scale=20,
        started_at=acceptance_day - timedelta(days=2),
        anchor_key="anchor-A",
        anchor_revision="anchor-revision-A",
        frozen_level=Decimal("100"),
        frozen_low=Decimal("90"),
        phase="active",
        observation="held",
        exchange_session_age=2,
        observed_bar_count=3,
        hold_count=2,
        close=Decimal("105"),
        source_member_digest=SHA,
        interaction_ref=None,
        previous_loss_ref=None,
        censor_reason=None,
    )


def _candidate(acceptance_day: date) -> StageCandidateObservation:
    return StageCandidateObservation(
        security_code="000001",
        market_date=acceptance_day,
        anchor_scale=20,
        stage_code=4,
        episode_id="episode-A",
        anchor_key="anchor-A",
        anchor_revision="anchor-revision-A",
        reference_role="same_frozen_high_as_acceptance_support",
        predicate_evidence=(),
        candidate_state=PredicateState.TRUE,
    )


def test_profile_config_opens_only_development_continuous_profile() -> None:
    raw = load_second_group_profile_config(
        ROOT / "config/stage-learning/second-group-profile.v1.json"
    )
    assert raw["work_item"] == "P3-STAGE-001-H"
    assert raw["population"]["cohort_size"] == 512
    assert raw["semantic_axes"]["trend_background"]["candidate_stage_code"] == 5
    assert raw["semantic_axes"]["trend_background"]["exclusive_with_local_process"] is False
    assert raw["semantic_axes"]["failure_rebound_process"][
        "breakout_anchor_loss_is_not_automatically_stage_10"
    ] is True
    authority = raw["authority"]
    assert authority["development_only"] is True
    assert authority["continuous_profile_output_authorized"] is True
    assert authority["stage_label_output_authorized"] is False
    assert authority["parameter_search_authorized"] is False
    assert authority["arbitration_access_authorized"] is False
    assert authority["permanent_blind_access_authorized"] is False


def test_profile_measures_support_break_and_rebound_without_emitting_stage() -> None:
    rows = tuple(
        _admission(index, close)
        for index, close in enumerate(
            ("100", "102", "105", "110", "108", "95", "89", "85", "88", "92")
        )
    )
    acceptance_day = rows[2].row.market_date
    episode = _episode(acceptance_day)
    structure = StructureV2Result((), (), (), (episode,), ())
    activity = tuple(
        ActivityObservation(
            item.row.security_code,
            item.row.market_date,
            20,
            True,
            Decimal("1.0"),
            "available",
            SHA,
        )
        for item in rows
    )
    episode_profiles, observations, stopped = build_second_group_profiles(
        (_candidate(acceptance_day),),
        structure,
        activity,
        rows,
        maximum_sessions=60,
    )
    assert len(episode_profiles) == 1
    profile = episode_profiles[0]
    assert profile.sessions_observed == 8
    assert profile.sessions_to_support_break == 4
    assert profile.sessions_to_support_reclaim_after_break == 3
    assert profile.maximum_extension_from_acceptance == Decimal("110") / Decimal("105") - 1
    assert profile.maximum_drawdown_from_running_peak == Decimal("85") / Decimal("110") - 1
    assert profile.minimum_distance_to_support == Decimal("85") / Decimal("90") - 1
    assert profile.maximum_post_break_rebound == Decimal("92") / Decimal("85") - 1
    assert len(observations) == 8
    assert stopped == {
        "nonadmitted": 0,
        "coordinate_or_listing_change": 0,
        "report_end_or_horizon": 1,
    }

    summary = _summarize_profiles(episode_profiles, observations, quantiles=(0.5,))
    assert summary["20"]["accepted_episode_count"] == 1
    assert summary["20"]["support_candidate_break_episode_count"] == 1
    assert summary["20"]["support_candidate_reclaim_episode_count"] == 1


def test_breakout_anchor_loss_alone_does_not_create_support_break() -> None:
    rows = tuple(
        _admission(index, close)
        for index, close in enumerate(("98", "101", "105", "99", "97", "96"))
    )
    acceptance_day = rows[2].row.market_date
    episode = _episode(acceptance_day)
    structure = StructureV2Result((), (), (), (episode,), ())
    episode_profiles, _, _ = build_second_group_profiles(
        (_candidate(acceptance_day),),
        structure,
        (),
        rows,
        maximum_sessions=60,
    )
    assert episode_profiles[0].sessions_to_support_break is None
    assert episode_profiles[0].minimum_distance_to_support > 0


def test_nonadmitted_row_stops_path_instead_of_fabricating_failure() -> None:
    rows = (
        _admission(0, "100"),
        _admission(1, "102"),
        _admission(2, "105"),
        _admission(3, "104", admitted=False),
        _admission(4, "80"),
    )
    acceptance_day = rows[2].row.market_date
    episode_profiles, observations, stopped = build_second_group_profiles(
        (_candidate(acceptance_day),),
        StructureV2Result((), (), (), (_episode(acceptance_day),), ()),
        (),
        rows,
        maximum_sessions=60,
    )
    assert episode_profiles[0].sessions_observed == 1
    assert episode_profiles[0].sessions_to_support_break is None
    assert len(observations) == 1
    assert stopped["nonadmitted"] == 1


def test_profile_config_contains_no_post_2021_development_window() -> None:
    raw = json.loads(
        (ROOT / "config/stage-learning/second-group-profile.v1.json").read_text(encoding="utf-8")
    )
    assert raw["development_window"]["report_end"] == "2021-12-31"
    text = json.dumps(raw["development_window"], sort_keys=True)
    assert "2022" not in text and "2025" not in text

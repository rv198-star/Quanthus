from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from quanthus.policy import find_project_root
from quanthus.stage_learning.core import PredicateState, StageCandidateObservation
from quanthus.stage_learning.second_group_repair_profile import (
    build_second_group_repair_profiles,
    load_second_group_repair_profile_config,
    summarize_second_group_repair_profiles,
)
from quanthus.structure_v2 import EpisodeObservation, StructureV2Result
from quanthus.structure_v2_inputs import Admission, DailyInput

ROOT = find_project_root()
SHA = "sha256:" + "a" * 64
TZ = timezone(timedelta(hours=8))


def _admission(offset: int, close: str, *, admitted: bool = True) -> Admission:
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
        calendar_member_digest=SHA,
        action_member_digest=SHA,
    )
    return Admission(
        row,
        admitted,
        "admitted_retrospective_only" if admitted else "missing_daily_bar",
        False,
    )


def _episode(day: date) -> EpisodeObservation:
    return EpisodeObservation(
        episode_id="episode-A",
        security_code="000001",
        market_date=day,
        scale=20,
        started_at=day - timedelta(days=2),
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
    )


def _candidate(day: date) -> StageCandidateObservation:
    return StageCandidateObservation(
        security_code="000001",
        market_date=day,
        anchor_scale=20,
        stage_code=4,
        episode_id="episode-A",
        anchor_key="anchor-A",
        anchor_revision="anchor-revision-A",
        reference_role="same_frozen_high_as_acceptance_support",
        predicate_evidence=(),
        candidate_state=PredicateState.TRUE,
    )


def test_repair_profile_preserves_all_second_group_stage_identities() -> None:
    raw = load_second_group_repair_profile_config(
        ROOT / "config/stage-learning/second-group-repair-profile.v1.json"
    )
    identity = raw["state_identity_contract"]
    assert identity["preserve_distinct_stage_codes"] == [5, 6, 7, 10, 11]
    assert identity["stage_5_6_merge_authorized"] is False
    assert identity["stage_10_11_merge_authorized"] is False
    assert identity["later_repair_does_not_retroactively_delete_an_earlier_valid_failure"] is True
    assert raw["repair_profile"]["deep_damage_candidate_abs_thresholds"] == [
        "0.12",
        "0.16",
        "0.20",
    ]
    assert raw["failure_profile"]["persistence_candidate_sessions"] == [3, 5, 8]
    assert raw["path_contract"]["support_role_selection_inside_job_authorized"] is False
    authority = raw["authority"]
    assert authority["development_only"] is True
    assert authority["stage_label_output_authorized"] is False
    assert authority["parameter_search_authorized"] is False
    assert authority["arbitration_access_authorized"] is False
    assert authority["permanent_blind_access_authorized"] is False


def test_repair_profile_separates_damage_repair_failure_and_unreclaimed_rebound() -> None:
    rows = tuple(
        _admission(index, close)
        for index, close in enumerate(
            ("100", "102", "105", "110", "100", "88", "85", "87", "89", "91", "95", "101", "110")
        )
    )
    acceptance_day = rows[2].row.market_date
    repairs, cycles, support_obs, support_episodes, stopped = build_second_group_repair_profiles(
        (_candidate(acceptance_day),),
        StructureV2Result((), (), (), (_episode(acceptance_day),), ()),
        rows,
        maximum_sessions=60,
    )
    assert stopped == {
        "nonadmitted": 0,
        "coordinate_or_listing_change": 0,
        "report_end_or_horizon": 1,
    }
    assert any(item.maximum_damage_depth == Decimal("85") / Decimal("110") - 1 for item in cycles)
    completed = [item for item in cycles if item.fully_repaired]
    assert len(completed) == 1
    assert completed[0].maximum_repair_fraction == Decimal(1)
    assert completed[0].sessions_from_trough_to_full_repair == 6
    assert any(
        item.damage_depth <= Decimal("-0.20") and item.repair_fraction is not None
        for item in repairs
    )

    by_role = {item.support_role: item for item in support_episodes}
    acceptance = by_role["acceptance_anchor"]
    deeper = by_role["initial_low_support_candidate"]
    assert acceptance.sessions_to_break == 3
    assert acceptance.sessions_to_reclaim_after_break == 6
    assert deeper.sessions_to_break == 3
    assert deeper.sessions_to_reclaim_after_break == 4

    acceptance_age3 = [
        item
        for item in support_obs
        if item.support_role == "acceptance_anchor" and item.unreclaimed_age_sessions == 3
    ]
    assert len(acceptance_age3) == 1
    assert acceptance_age3[0].rebound_from_post_break_low == Decimal("89") / Decimal("85") - 1
    assert not any(
        item.support_role == "acceptance_anchor" and item.unreclaimed_age_sessions == 6
        for item in support_obs
    )

    summary = summarize_second_group_repair_profiles(
        repairs,
        cycles,
        support_obs,
        support_episodes,
        deep_damage_thresholds=(Decimal("0.12"), Decimal("0.16"), Decimal("0.20")),
        persistence_candidates=(3, 5, 8),
        quantiles=(0.5,),
    )
    scale = summary["20"]
    deep20 = scale["repair_profile_by_deep_damage_candidate"]["0.20"]
    assert deep20["cycle_count"] == 1
    assert deep20["fully_repaired_cycle_count"] == 1
    assert deep20["fully_repaired_cycle_rate"] == 1.0
    p3 = scale["support_role_profiles"]["acceptance_anchor"]["persistence_candidates"]["3"]
    assert p3["at_risk_episode_count"] == 1
    assert p3["reclaimed_within_candidate_count"] == 0
    assert p3["persistent_unreclaimed_episode_count"] == 1
    p8 = scale["support_role_profiles"]["acceptance_anchor"]["persistence_candidates"]["8"]
    assert p8["reclaimed_within_candidate_count"] == 1
    assert p8["persistent_unreclaimed_episode_count"] == 0


def test_nonadmitted_row_censors_failure_instead_of_confirming_stage10() -> None:
    rows = (
        _admission(0, "100"),
        _admission(1, "102"),
        _admission(2, "105"),
        _admission(3, "95"),
        _admission(4, "89"),
        _admission(5, "80", admitted=False),
        _admission(6, "70"),
    )
    acceptance_day = rows[2].row.market_date
    _, _, support_obs, support_episodes, stopped = build_second_group_repair_profiles(
        (_candidate(acceptance_day),),
        StructureV2Result((), (), (), (_episode(acceptance_day),), ()),
        rows,
        maximum_sessions=60,
    )
    assert stopped["nonadmitted"] == 1
    deeper = next(
        item for item in support_episodes if item.support_role == "initial_low_support_candidate"
    )
    assert deeper.sessions_to_break == 2
    assert deeper.sessions_to_reclaim_after_break is None
    assert deeper.post_break_observed_age_sessions == 0
    assert not any(
        item.unreclaimed_age_sessions > 0
        for item in support_obs
        if item.support_role == deeper.support_role
    )


def test_repair_profile_has_no_post_2021_development_access() -> None:
    raw = json.loads(
        (ROOT / "config/stage-learning/second-group-repair-profile.v1.json").read_text()
    )
    assert raw["development_window"]["report_end"] == "2021-12-31"
    text = json.dumps(raw["development_window"], sort_keys=True)
    assert "2022" not in text and "2025" not in text

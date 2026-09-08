from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from quanthus.policy import find_project_root
from quanthus.stage_learning.core import ActivityObservation
from quanthus.stage_learning.reference import (
    ReferenceKind,
    ReferencePilotError,
    ReviewerJudgement,
    build_reviewer_packet,
    resolve_reference_reviews,
    select_reference_contexts,
)
from quanthus.stage_learning.reference_pilot import load_reference_pilot_config
from quanthus.stage_learning.settlement import SettlementState, settle_reference_contexts
from quanthus.structure_v2 import EpisodeObservation
from quanthus.structure_v2_inputs import Admission, DailyInput

ROOT = find_project_root()
SHA = "sha256:" + "a" * 64
TZ = timezone(timedelta(hours=8))


def episode(index: int, *, observation: str = "held", phase: str = "active") -> EpisodeObservation:
    day = date(2020, 1, 2) + timedelta(days=index)
    return EpisodeObservation(
        episode_id=f"episode-{index}",
        security_code="000001",
        market_date=day,
        scale=20,
        started_at=day - timedelta(days=2),
        anchor_key=f"anchor-{index}",
        anchor_revision=f"anchor-revision-{index}",
        frozen_level=Decimal("100"),
        frozen_low=Decimal("90"),
        phase=phase,
        observation=observation,
        exchange_session_age=2,
        observed_bar_count=3,
        hold_count=2,
        close=Decimal("100.2"),
        source_member_digest=SHA,
        interaction_ref=f"interaction-{index}",
        previous_loss_ref=None,
        censor_reason=None,
    )


def activity(item: EpisodeObservation, ratio: str = "1.25") -> ActivityObservation:
    return ActivityObservation(
        item.security_code,
        item.market_date,
        20,
        True,
        Decimal(ratio),
        "available",
        SHA,
    )


def attempt(
    index: int,
    *,
    close: str = "100",
    high: str = "101",
    low: str = "99",
    admitted: bool = True,
    reason: str = "admitted_retrospective_only",
    reset_reason: str | None = None,
) -> Admission:
    day = date(2020, 1, 2) + timedelta(days=index)
    row = DailyInput(
        security_code="000001",
        market_date=day,
        session_index=index,
        cutoff=datetime(day.year, day.month, day.day, 15, 30, tzinfo=TZ),
        security_id="security-A",
        listing_id="listing-A",
        exchange="SZSE",
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("1000"),
        source_record_id=f"source-{index}",
        source_revision="revision-v1",
        source_artifact_sha256=SHA,
        dwd_known_from="2026-07-01T00:00:00+00:00",
        coordinate_epoch="epoch-v1",
        finality="final",
        reset_reason=reset_reason,
        calendar_member_digest=SHA,
        action_member_digest=SHA,
    )
    return Admission(row, admitted, reason, False)


def test_reference_sampling_is_deterministic_and_closes_strata() -> None:
    episodes = tuple(
        episode(index, observation="started" if index % 7 == 0 else "held")
        for index in range(30)
    )
    activities = tuple(activity(item) for item in episodes)
    first = select_reference_contexts(
        episodes,
        activities,
        report_start=episodes[0].market_date,
        report_end=episodes[-1].market_date,
        seed="frozen-seed",
        random_count=6,
        boundary_count=4,
    )
    second = select_reference_contexts(
        episodes,
        activities,
        report_start=episodes[0].market_date,
        report_end=episodes[-1].market_date,
        seed="frozen-seed",
        random_count=6,
        boundary_count=4,
    )
    assert first == second
    assert len(first) == 10
    assert sum(item.sampling_stratum == "random" for item in first) == 6
    assert sum(item.sampling_stratum == "boundary_or_rare" for item in first) == 4
    assert all(item.evidence_digest.startswith("sha256:") for item in first)


def test_reviewer_packet_is_t_only_and_contains_no_candidate_or_future_verdict() -> None:
    episodes = tuple(episode(index) for index in range(5))
    activities = tuple(activity(item) for item in episodes)
    contexts = select_reference_contexts(
        episodes,
        activities,
        report_start=episodes[0].market_date,
        report_end=episodes[-1].market_date,
        seed="seed",
        random_count=2,
        boundary_count=2,
    )
    admissions = tuple(attempt(index) for index in range(5))
    packets = build_reviewer_packet(
        contexts,
        episodes,
        activities,
        admissions,
        history_sessions=20,
    )
    assert len(packets) == 4
    for packet in packets:
        assert "future" not in {key.lower() for key in packet if key != "forbidden_fields"}
        assert not any("candidate" in key.lower() for key in packet if key != "forbidden_fields")
        assert all(row["market_date"] <= packet["market_date"] for row in packet["history"])
        assert packet["review_contract"]["primary_reviewers_required"] == 2


def judgement(context, reviewer: str, supported: tuple[int, ...], *, role: str = "primary"):
    return ReviewerJudgement(
        context.context_id,
        reviewer,
        role,
        ReferenceKind.DEFINITE,
        supported,
        (),
        ("t_only_semantic_review",),
        context.evidence_digest,
    )


def test_reference_resolution_requires_two_independent_reviews_and_third_on_disagreement() -> None:
    item = select_reference_contexts(
        (episode(0, observation="started"), episode(1)),
        (activity(episode(0, observation="started")), activity(episode(1))),
        report_start=date(2020, 1, 2),
        report_end=date(2020, 1, 3),
        seed="seed",
        random_count=1,
        boundary_count=1,
    )[0]
    agreed = resolve_reference_reviews(
        (item,),
        (judgement(item, "reviewer-a", (3,)), judgement(item, "reviewer-b", (3,))),
    )
    assert agreed[0].resolution == "primary_agreement"
    assert agreed[0].supported_stage_codes == (3,)

    with pytest.raises(ReferencePilotError, match="adjudicator"):
        resolve_reference_reviews(
            (item,),
            (judgement(item, "reviewer-a", (3,)), judgement(item, "reviewer-b", (4,))),
        )
    resolved = resolve_reference_reviews(
        (item,),
        (
            judgement(item, "reviewer-a", (3,)),
            judgement(item, "reviewer-b", (4,)),
            judgement(item, "reviewer-c", (3, 4), role="adjudicator"),
        ),
    )
    assert resolved[0].resolution == "third_party_adjudication"
    assert resolved[0].supported_stage_codes == (3, 4)


def _one_context():
    item = episode(0, observation="started")
    context = select_reference_contexts(
        (item, episode(1)),
        (activity(item), activity(episode(1))),
        report_start=item.market_date,
        report_end=episode(1).market_date,
        seed="seed",
        random_count=1,
        boundary_count=1,
    )[0]
    return item, context


def test_future_settlement_is_separate_and_fail_closed() -> None:
    item, context = _one_context()
    base = [attempt(0)]
    positive = base + [attempt(index, high="116", low="99") for index in range(1, 21)]
    settled = settle_reference_contexts(
        (context,),
        (item, episode(1)),
        positive,
        horizon_sessions=20,
        positive_threshold=Decimal("0.15"),
        adverse_threshold=Decimal("-0.07"),
        same_session_overlap_policy="failure_wins",
    )[0]
    assert settled.state is SettlementState.SETTLED and settled.outcome == "positive"

    overlap = base + [attempt(1, high="116", low="92")] + [attempt(index) for index in range(2, 21)]
    failed = settle_reference_contexts(
        (context,),
        (item, episode(1)),
        overlap,
        horizon_sessions=20,
        positive_threshold=Decimal("0.15"),
        adverse_threshold=Decimal("-0.07"),
        same_session_overlap_policy="failure_wins",
    )[0]
    assert failed.outcome == "failure"

    reset = base + [attempt(1, reset_reason="corporate_action_coordinate_change")] + [
        attempt(index) for index in range(2, 21)
    ]
    censored = settle_reference_contexts(
        (context,),
        (item, episode(1)),
        reset,
        horizon_sessions=20,
        positive_threshold=Decimal("0.15"),
        adverse_threshold=Decimal("-0.07"),
        same_session_overlap_policy="failure_wins",
    )[0]
    assert censored.state is SettlementState.CENSORED

    missing = base + [replace(attempt(1), admitted=False, reason="missing_daily_bar")] + [
        attempt(index) for index in range(2, 21)
    ]
    unavailable = settle_reference_contexts(
        (context,),
        (item, episode(1)),
        missing,
        horizon_sessions=20,
        positive_threshold=Decimal("0.15"),
        adverse_threshold=Decimal("-0.07"),
        same_session_overlap_policy="failure_wins",
    )[0]
    assert unavailable.state is SettlementState.UNAVAILABLE


def test_reference_pilot_config_freezes_120_and_does_not_open_qualification() -> None:
    config = load_reference_pilot_config(ROOT / "config/stage-learning/reference-pilot.v1.json")
    assert config["sampling"]["total_episode_contexts"] == 120
    assert config["sampling"]["random_count"] == 80
    assert config["sampling"]["boundary_or_rare_count"] == 40
    assert config["authority"]["development_future_settlement_authorized"] is True
    assert config["authority"]["classifier_training_authorized"] is False
    assert config["authority"]["arbitration_access_authorized"] is False
    assert config["authority"]["permanent_blind_access_authorized"] is False
    assert config["authority"]["qualification_authorized"] is False

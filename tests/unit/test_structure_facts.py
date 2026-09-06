from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from quanthus.aggregator import (
    ScoreActionPolicy,
    aggregate_governance,
    aggregate_score_and_map_action,
)
from quanthus.expert_protocols import (
    ConstraintOutput,
    ConstraintScope,
    EvidenceKind,
    ExpertApplicability,
    ExpertContext,
    RequiredAction,
    VetoAuthority,
    VetoOutput,
    run_scoring_expert,
)
from quanthus.structure_facts import (
    ContinuousStructureFactsCompatibilityExpert,
    build_continuous_structure_facts,
    continuous_structure_feature_facts,
    render_coarse_structure_state,
)
from quanthus.structure_intelligence import StructureMvpState, StructureSourceRow

TZ = timezone(timedelta(hours=8))


def _row(
    index: int,
    *,
    high: str = "100",
    low: str = "90",
    close: str = "95",
    volume: str | None = "1000",
    reset: bool = False,
) -> StructureSourceRow:
    day = date(2020, 1, 1) + timedelta(days=index)
    return StructureSourceRow(
        market_date=day,
        security_code="000001",
        dwd_security_id="dwd-security-1",
        snapshot_id=f"snapshot-{day.isoformat()}",
        knowledge_cutoff=datetime(day.year, day.month, day.day, 15, tzinfo=TZ),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=None if volume is None else Decimal(volume),
        source_revision="sha256:" + "1" * 64,
        source_artifact_sha256="sha256:" + "2" * 64,
        history_reset=reset,
    )


def _build(rows: list[StructureSourceRow]):
    return build_continuous_structure_facts(
        rows,
        upstream_identity="upstream-fixture",
        policy_digest="sha256:" + "3" * 64,
    )


def _warmup() -> list[StructureSourceRow]:
    return [_row(index) for index in range(20)]


def test_prior_twenty_anchors_exclude_t_and_publish_signed_margins() -> None:
    rows = _warmup()
    rows.append(_row(20, high="120", low="91", close="101", volume="500"))

    facts = _build(rows)
    observed = facts.rows[-1]

    assert observed.available is True
    assert observed.source_window_start == rows[0].market_date
    assert observed.source_window_end == rows[19].market_date
    assert observed.rolling_resistance20 == Decimal("100")
    assert observed.rolling_support20 == Decimal("90")
    assert observed.resistance_margin == Decimal("0.01")
    assert observed.support_margin == Decimal("101") / Decimal("90") - Decimal("1")
    assert observed.episode_id is not None
    assert observed.breakout_level == Decimal("100")


def test_price_crossing_creates_episode_even_without_volume_confirmation() -> None:
    rows = _warmup()
    rows.append(_row(20, high="102", low="94", close="101", volume="100"))

    observed = _build(rows).rows[-1]

    assert observed.volume_ratio20 == Decimal("0.1")
    assert observed.episode_status == "active"
    assert observed.attempt_volume_ratio20 == Decimal("0.1")
    assert observed.post_attempt_hold_count == 0
    assert observed.episode_age_sessions == 0


def test_episode_anchor_freezes_and_later_holds_do_not_rewrite_attempt_row() -> None:
    rows = _warmup()
    rows.extend(
        [
            _row(20, high="105", low="94", close="101"),
            _row(21, high="110", low="98", close="102"),
            _row(22, high="115", low="99", close="103"),
        ]
    )

    facts = _build(rows)
    attempt, hold1, hold2 = facts.rows[-3:]

    assert attempt.breakout_level == Decimal("100")
    assert attempt.post_attempt_hold_count == 0
    assert hold1.breakout_level == Decimal("100")
    assert hold1.post_attempt_hold_count == 1
    assert hold1.episode_age_sessions == 1
    assert hold2.breakout_level == Decimal("100")
    assert hold2.post_attempt_hold_count == 2
    assert hold2.episode_age_sessions == 2
    assert attempt.digest == facts.rows[-3].digest
    assert len({attempt.episode_id, hold1.episode_id, hold2.episode_id}) == 1


def test_failed_hold_ends_old_episode_and_future_cross_gets_new_identity() -> None:
    rows = _warmup()
    rows.extend(
        [
            _row(20, high="105", low="94", close="101"),
            _row(21, high="103", low="98", close="99"),
            _row(22, high="104", low="99", close="100"),
            _row(23, high="120", low="100", close="111"),
        ]
    )

    facts = _build(rows)
    attempt, ended, no_episode, next_attempt = facts.rows[-4:]

    assert ended.episode_id == attempt.episode_id
    assert ended.episode_status == "ended"
    assert ended.episode_termination_reason == "close_below_breakout_level"
    assert ended.breakout_level == Decimal("100")
    assert no_episode.episode_id is None
    assert next_attempt.episode_status == "active"
    assert next_attempt.episode_id != attempt.episode_id


def test_history_reset_clears_episode_and_requires_new_twenty_session_history() -> None:
    rows = _warmup()
    rows.extend(
        [
            _row(20, high="105", low="94", close="101"),
            _row(21, high="102", low="96", close="101", reset=True),
        ]
    )

    observed = _build(rows).rows[-1]

    assert observed.available is False
    assert observed.availability_reason == "insufficient_history_after_reset"
    assert observed.history_reset is True
    assert observed.episode_id is None
    assert observed.rolling_resistance20 is None


def test_row_local_episode_explanation_and_coarse_renderer_are_separate() -> None:
    rows = _warmup()
    rows.extend(
        [
            _row(20, high="105", low="94", close="101"),
            _row(21, high="106", low="99", close="102"),
            _row(22, high="107", low="100", close="103"),
        ]
    )

    facts = _build(rows)
    observed = facts.rows[-1]

    assert observed.episode_id is not None
    assert observed.attempt_at == rows[20].market_date
    assert observed.attempt_snapshot_id == rows[20].snapshot_id
    assert observed.breakout_level == Decimal("100")
    assert observed.episode_support_level == Decimal("90")
    assert observed.episode_anchor_window_start == rows[0].market_date
    assert observed.episode_anchor_window_end == rows[19].market_date
    assert render_coarse_structure_state(observed) is StructureMvpState.BREAKOUT_ACCEPTED
    assert "state" not in observed.to_dict()
    assert "coarse" not in observed.to_dict()


def test_continuous_fact_expert_consumes_direct_features_through_aggregator() -> None:
    rows = _warmup()
    rows.append(_row(20, high="105", low="94", close="101"))
    adm = _build(rows)
    row = adm.rows[-1]
    feature_facts = continuous_structure_feature_facts(adm, row)
    context = ExpertContext(
        security_code=row.security_code,
        snapshot_id=row.snapshot_id,
        knowledge_cutoff=row.knowledge_cutoff,
        position_state="empty",
        feature_facts=feature_facts,
        label_results=(),
    )
    score = run_scoring_expert(ContinuousStructureFactsCompatibilityExpert(), context)
    snapshot = next(ref for ref in context.evidence_refs if ref.kind is EvidenceKind.SNAPSHOT)
    veto = VetoOutput(
        expert_id="system.structure-facts-test-guardian",
        expert_version="1.0.0",
        applicability=ExpertApplicability.APPLICABLE,
        applicability_reason="test",
        reason_code="test_allowed",
        evidence=(snapshot,),
        authority=VetoAuthority.SYSTEM,
        allow=True,
        required_action=RequiredAction.NONE,
    )
    constraint = ConstraintOutput(
        expert_id="system.structure-facts-test-position",
        expert_version="1.0.0",
        applicability=ExpertApplicability.APPLICABLE,
        applicability_reason="test",
        reason_code="test_position",
        evidence=(snapshot,),
        minimum_position=Decimal("0"),
        maximum_position=Decimal("0.10"),
        stop_rule="test_stop",
        hold_rule="test_hold",
        scope=ConstraintScope.UNIT,
    )
    governance = aggregate_governance(context, (veto,), (constraint,))
    action = aggregate_score_and_map_action(
        context,
        governance,
        (score,),
        ScoreActionPolicy("structure-facts-compatibility@1.0.0", Decimal("0.50")),
    )

    assert score.applicability is ExpertApplicability.APPLICABLE
    assert score.signal == Decimal("0.01")
    assert {item.evidence_id for item in score.evidence} == {
        "structure.breakout-margin",
        "structure.episode-status",
        "structure.resistance-margin",
    }
    assert action.aggregate_score == Decimal("0.01")


def test_continuous_fact_build_is_deterministic_and_package_digest_excludes_renderer() -> None:
    rows = _warmup()
    rows.extend([_row(20, close="101", high="105"), _row(21, close="102", high="106")])

    left = _build(rows)
    right = _build(list(reversed(rows)))

    assert left.content_digest == right.content_digest
    assert left.manifest_digest == right.manifest_digest
    assert [row.digest for row in left.rows] == [row.digest for row in right.rows]
    assert [render_coarse_structure_state(row) for row in left.rows] == [
        render_coarse_structure_state(row) for row in right.rows
    ]

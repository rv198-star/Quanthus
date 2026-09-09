from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal

from quanthus.policy import find_project_root
from quanthus.stage_learning.second_group_development import (
    ResolvedReference,
    SampledContext,
    SecondGroupObservation,
    _gate,
    _predict_5_6,
    _predict_7,
    _predict_10,
    _predict_11,
    load_second_group_development_config,
    load_second_group_panel,
    resolve_stage_references,
    sample_stage_contexts,
    score_stage,
)

ROOT = find_project_root()
SHA = "sha256:" + "a" * 64


def observation(
    index: int,
    *,
    episode: str | None = None,
    extension: str = "0.12",
    drawdown: str = "0.08",
    damage: str = "0.16",
    repair: str | None = "0.40",
    current_failure: bool = False,
    failure_age: int = 0,
    failure_depth: str = "0",
    rebound: str = "0",
) -> SecondGroupObservation:
    return SecondGroupObservation(
        security_code=f"{index % 999999:06d}",
        market_date=date(2020, 1, 2) + timedelta(days=index),
        scale=20 if index % 2 == 0 else 60,
        episode_id=episode or f"episode-{index}",
        maximum_extension=Decimal(extension),
        current_drawdown_abs=Decimal(drawdown),
        damage_depth_abs=Decimal(damage),
        repair_fraction=None if repair is None else Decimal(repair),
        support_break_seen=current_failure,
        support_reclaimed=False,
        support_current_unreclaimed=current_failure,
        support_unreclaimed_age=failure_age,
        support_depth_abs=Decimal(failure_depth),
        rebound_from_post_break_low=Decimal(rebound),
    )


def test_development_contract_opens_only_bounded_development_runtime() -> None:
    raw = load_second_group_development_config(
        ROOT / "config/stage-learning/second-group-development.v1.json"
    )
    assert raw["state_identity"]["preserve_distinct_stage_codes"] == [5, 6, 7, 10, 11]
    assert raw["state_identity"]["stage_5_6_merge_authorized"] is False
    assert raw["state_identity"]["stage_10_11_merge_authorized"] is False
    assert raw["execution_order"] == ["stage_5_6", "stage_10", "stage_7", "stage_11"]
    authority = raw["authority"]
    assert authority["limited_parameter_search_runtime_authorized"] is True
    assert authority["development_data_read_authorized"] is True
    assert authority["future_outcome_access_authorized"] is False
    assert authority["arbitration_access_authorized"] is False
    assert authority["permanent_blind_access_authorized"] is False
    assert authority["official_label_publication_authorized"] is False


def test_synthetic_panel_is_development_only_and_not_human_reference() -> None:
    raw = load_second_group_panel(
        ROOT / "config/stage-learning/second-group-synthetic-panel.v1.json"
    )
    assert raw["evidence_tier"] == "SYNTHETIC_EXPERT_CONSENSUS"
    assert raw["human_expert_validated"] is False
    assert raw["reference_semantics"]["stage_5_6_merge_authorized"] is False
    assert raw["reference_semantics"]["stage_10_11_merge_authorized"] is False
    assert raw["reference_semantics"]["reviewers_never_receive_candidate_parameters"] is True
    assert raw["authority"]["formal_qualification_authorized"] is False


def test_stage_5_and_6_can_coexist_without_merging() -> None:
    item = observation(1, extension="0.15", drawdown="0.09", damage="0.09")
    params = {
        "advance_extension_min": "0.10",
        "pullback_drawdown_abs_min": "0.08",
        "deep_damage_drawdown_abs_min": "0.16",
    }
    stage5, stage6 = _predict_5_6(item, params)
    assert stage5 is True
    assert stage6 is True


def test_stage_10_requires_persistence_and_depth_not_one_crossing() -> None:
    params = {"unreclaimed_persistence_sessions": 5, "failure_depth_abs_min": "0.04"}
    one_cross = observation(
        2, current_failure=True, failure_age=0, failure_depth="0.08", rebound="0.01"
    )
    shallow_old = observation(
        3, current_failure=True, failure_age=8, failure_depth="0.01", rebound="0.03"
    )
    confirmed = observation(
        4, current_failure=True, failure_age=5, failure_depth="0.05", rebound="0.03"
    )
    assert _predict_10(one_cross, params) is False
    assert _predict_10(shallow_old, params) is False
    assert _predict_10(confirmed, params) is True


def test_stage_7_requires_repair_and_is_blocked_by_current_frozen_stage_10() -> None:
    stage10 = {"unreclaimed_persistence_sessions": 5, "failure_depth_abs_min": "0.04"}
    stage7 = {"damage_depth_abs_min": "0.16", "repair_fraction_min": "0.40"}
    repaired = observation(5, damage="0.18", repair="0.45")
    failed = observation(
        6,
        damage="0.22",
        repair="0.50",
        current_failure=True,
        failure_age=8,
        failure_depth="0.07",
    )
    assert _predict_7(repaired, stage7, stage10) is True
    assert _predict_7(failed, stage7, stage10) is False


def test_stage_11_requires_same_frozen_stage_10_and_unreclaimed_rebound() -> None:
    stage10 = {"unreclaimed_persistence_sessions": 5, "failure_depth_abs_min": "0.04"}
    stage11 = {"rebound_from_post_break_low_min": "0.05"}
    rebound_without_failure = observation(7, rebound="0.10")
    failed_small_rebound = observation(
        8,
        current_failure=True,
        failure_age=8,
        failure_depth="0.06",
        rebound="0.03",
    )
    failed_rebound = observation(
        9,
        current_failure=True,
        failure_age=8,
        failure_depth="0.06",
        rebound="0.06",
    )
    assert _predict_11(rebound_without_failure, stage11, stage10) is False
    assert _predict_11(failed_small_rebound, stage11, stage10) is False
    assert _predict_11(failed_rebound, stage11, stage10) is True


def test_sampling_is_target_cap_and_one_context_per_episode() -> None:
    rows = []
    for index in range(200):
        if index % 4 == 0:
            rows.append(observation(index, extension="0.02"))
        elif index % 4 == 1:
            rows.append(observation(index, extension="0.07"))
        elif index % 4 == 2:
            rows.append(observation(index, extension="0.12"))
        else:
            rows.append(observation(index, extension="0.20"))
    selected = sample_stage_contexts(
        tuple(rows), stage=5, seed=SHA, per_bucket=30, minimum_total=80
    )
    assert len(selected) == 120
    assert len({item.observation.episode_id for item in selected}) == len(selected)
    counts: dict[str, int] = {}
    for item in selected:
        counts[item.bucket] = counts.get(item.bucket, 0) + 1
    assert set(counts.values()) == {30}


def test_panel_resolution_does_not_receive_algorithm_predictions() -> None:
    panel = load_second_group_panel(
        ROOT / "config/stage-learning/second-group-synthetic-panel.v1.json"
    )
    item = observation(210, current_failure=True, failure_age=8, failure_depth="0.06")
    context = SampledContext(10, item.context_id(10), "unreclaimed_8_plus", item, SHA)
    resolved, disagreements = resolve_stage_references((context,), panel)
    assert len(resolved) == 1
    assert resolved[0].stage == 10
    assert resolved[0].kind in {"definite", "ambiguous_set", "unjudgeable"}
    assert disagreements in {0, 1}


def test_score_and_gate_keep_ambiguous_overassertion_separate() -> None:
    contexts = tuple(
        SampledContext(10, f"sha256:{index:064x}", "bucket", observation(index), SHA)
        for index in range(50)
    )
    references = []
    for index, context in enumerate(contexts):
        if index < 20:
            kind, supported = "definite", True
        elif index < 40:
            kind, supported = "definite", False
        else:
            kind, supported = "ambiguous_set", True
        references.append(ResolvedReference(10, context.context_id, kind, supported, "test", SHA))
    predictions = {context.context_id: index < 20 for index, context in enumerate(contexts)}
    score = score_stage(
        10, contexts, tuple(references), predictions, trial_id="sha256:" + "b" * 64
    )
    assert score.tp == 20
    assert score.fp == 0
    assert score.ambiguous_unique_assertion_rate == 0.0
    gates = json.loads(
        (ROOT / "config/stage-learning/second-group-development.v1.json").read_text()
    )["hard_gates"]
    result = _gate(score, r0_f1=score.f1, gates=gates)
    assert result["passed"] is True

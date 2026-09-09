from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from quanthus.policy import find_project_root
from quanthus.stage_learning.second_group_development import (
    SampledContext,
    SecondGroupObservation,
)
from quanthus.stage_learning.second_group_development_supplement import (
    _milestone_disposition,
    _stage7_prior_analysis,
    load_second_group_supplement_config,
    sample_stage_supplement_contexts,
)

ROOT = find_project_root()
SHA = "sha256:" + "a" * 64


def _observation(
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


def _context(stage: int, item: SecondGroupObservation, bucket: str = "base") -> SampledContext:
    return SampledContext(stage, item.context_id(stage), bucket, item, SHA)


def test_supplement_contract_only_repairs_stage_6_and_11_reference_sufficiency() -> None:
    raw = load_second_group_supplement_config(
        ROOT / "config/stage-learning/second-group-development-supplement.v1.json"
    )
    assert raw["supplement_sampling"]["supplement_stages"] == [6, 11]
    assert raw["supplement_sampling"]["additional_target_contexts_per_bucket"] == 20
    assert raw["search_contract"]["hard_gate_values_must_not_change"] is True
    assert raw["search_contract"]["stage_10"] == "reuse_prior_frozen_candidate_without_research"
    assert raw["search_contract"]["stage_7"] == (
        "retain_prior_inconclusive_without_grid_expansion_or_rerun"
    )
    assert raw["prior_run"]["frozen_stage_10"] == {
        "unreclaimed_persistence_sessions": 5,
        "failure_depth_abs_min": "0.02",
    }
    authority = raw["authority"]
    assert authority["stage_10_research_authorized"] is False
    assert authority["stage_7_grid_expansion_authorized"] is False
    assert authority["future_outcome_access_authorized"] is False
    assert authority["arbitration_access_authorized"] is False
    assert authority["permanent_blind_access_authorized"] is False


def test_supplement_sampling_excludes_base_episodes_and_caps_each_bucket() -> None:
    rows: list[SecondGroupObservation] = []
    for index in range(320):
        group = index % 4
        if group == 0:
            rows.append(_observation(index, current_failure=False, rebound="0"))
        elif group == 1:
            rows.append(
                _observation(
                    index,
                    current_failure=True,
                    failure_age=1,
                    failure_depth="0.02",
                    rebound="0.01",
                )
            )
        elif group == 2:
            rows.append(
                _observation(
                    index,
                    current_failure=True,
                    failure_age=5,
                    failure_depth="0.04",
                    rebound="0.04",
                )
            )
        else:
            rows.append(
                _observation(
                    index,
                    current_failure=True,
                    failure_age=9,
                    failure_depth="0.06",
                    rebound="0.10",
                )
            )
    base = tuple(_context(11, item) for item in rows[:40])
    supplement = sample_stage_supplement_contexts(
        tuple(rows),
        stage=11,
        base_contexts=base,
        seed=SHA,
        per_bucket=20,
    )
    assert supplement
    assert not ({item.observation.episode_id for item in base} & {
        item.observation.episode_id for item in supplement
    })
    counts: dict[str, int] = {}
    for item in supplement:
        counts[item.bucket] = counts.get(item.bucket, 0) + 1
    assert all(value <= 20 for value in counts.values())


def test_stage7_prior_analysis_preserves_inconclusive_and_counts_gate_reasons() -> None:
    prior = {
        "stage_7": {
            "selected": None,
            "eligible_trial_count": 0,
            "trials": [
                {
                    "trial_id": "sha256:" + "1" * 64,
                    "params": {"damage_depth_abs_min": "0.16", "repair_fraction_min": "0.40"},
                    "score": {
                        "f1": 0.70,
                        "precision_lower": 0.60,
                        "positive_reference_coverage": 0.65,
                    },
                    "gate": {
                        "passed": False,
                        "reason_codes": ["precision_lower_below_gate"],
                    },
                },
                {
                    "trial_id": "sha256:" + "2" * 64,
                    "params": {"damage_depth_abs_min": "0.12", "repair_fraction_min": "0.25"},
                    "score": {
                        "f1": 0.65,
                        "precision_lower": 0.55,
                        "positive_reference_coverage": 0.75,
                    },
                    "gate": {
                        "passed": False,
                        "reason_codes": [
                            "precision_lower_below_gate",
                            "negative_false_positive_rate_above_gate",
                        ],
                    },
                },
            ],
        }
    }
    result = _stage7_prior_analysis(prior)
    assert result["status"] == "INCONCLUSIVE"
    assert result["grid_expanded"] is False
    assert result["rerun"] is False
    assert result["gate_reason_counts"] == {
        "negative_false_positive_rate_above_gate": 1,
        "precision_lower_below_gate": 2,
    }
    assert len(result["top_diagnostic_trials"]) == 2


def test_milestone_can_complete_with_attributable_stage7_inconclusive() -> None:
    complete, disposition = _milestone_disposition(
        stage56_selected={"advance_extension_min": "0.10"},
        stage11_selected={"rebound_from_post_break_low_min": "0.05"},
    )
    assert complete is True
    assert disposition == "DEVELOPMENT_MILESTONE_COMPLETE_WITH_STAGE_7_INCONCLUSIVE"
    incomplete, disposition = _milestone_disposition(
        stage56_selected=None,
        stage11_selected={"rebound_from_post_break_low_min": "0.05"},
    )
    assert incomplete is False
    assert disposition == "INCONCLUSIVE_AFTER_REFERENCE_SUPPLEMENT"

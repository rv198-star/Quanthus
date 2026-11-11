from __future__ import annotations

import copy
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.policy import find_project_root
from quanthus.stage_learning.core import ActivityObservation
from quanthus.stage_learning.qualification import (
    StageQualificationError,
    _candidate,
    _hard_gate,
    _trial,
    load_qualification_config,
    select_qualification_contexts,
)
from quanthus.structure_v2 import EpisodeObservation
from quanthus.structure_v2_inputs import digest

ROOT = find_project_root()
CONFIG = ROOT / "config/stage-learning/qualification.v1.json"


def test_qualification_config_freezes_one_arbitration_run_and_keeps_2025_closed() -> None:
    config = load_qualification_config(CONFIG)
    authority = config["authority"]
    assert authority["arbitration_access_budget_runs"] == 1
    assert authority["parameter_search_authorized"] is False
    assert authority["candidate_mutation_after_result_access_authorized"] is False
    assert authority["permanent_blind_access_authorized"] is False
    assert authority["qualified_label_publication_authorized"] is False
    assert config["partition"]["name"] == "2022-2024-arbitration"
    assert config["partition"]["permanent_blind_2025_untouched"] is True
    assert config["decision"]["when_all_hard_gates_pass"] == "SHADOW_EXTERNAL_SEMANTIC_ACCEPTED"
    assert config["decision"]["qualified_label_version_allowed"] is False
    assert config["sampling"]["total_contexts"] == 640
    assert config["sampling"]["one_context_per_episode"] is True


def test_qualification_config_rejects_search_2025_or_qualified_claim(tmp_path: Path) -> None:
    raw = json.loads(CONFIG.read_text(encoding="utf-8"))
    for path, value in (
        (("authority", "parameter_search_authorized"), True),
        (("authority", "permanent_blind_access_authorized"), True),
        (("authority", "qualified_label_publication_authorized"), True),
        (("authority", "arbitration_access_budget_runs"), 2),
        (("partition", "permanent_blind_2025_untouched"), False),
        (("decision", "qualified_label_version_allowed"), True),
    ):
        changed = copy.deepcopy(raw)
        changed[path[0]][path[1]] = value
        candidate = tmp_path / f"{path[0]}-{path[1]}.json"
        candidate.write_text(json.dumps(changed), encoding="utf-8")
        with pytest.raises(StageQualificationError):
            load_qualification_config(candidate)


def test_frozen_candidate_digest_and_trial_identity_are_exact() -> None:
    config = load_qualification_config(CONFIG)
    candidate = _candidate(ROOT, config)
    assert digest(candidate) == config["candidate_digest"]
    params = _trial(candidate)
    assert params.trial_id == (
        "sha256:c21032608ab96d6abae81ba5709bf3e2c3f9eebb29ccdcdccf715dc2c4325bfc"
    )
    assert str(params.probe_volume_ratio20_min) == "1.0"
    assert params.acceptance_hold_sessions == 2
    assert str(params.acceptance_signed_distance_min) == "0.0"


def _score(*, macro_f1: float = 0.90, coverage: float = 0.80, ambiguity: float = 0.0) -> dict:
    return {
        "stage_metrics": {
            "3": {
                "truth_count": 120,
                "precision_wilson_one_sided_95_lower": 0.90,
            },
            "4": {
                "truth_count": 110,
                "precision_wilson_one_sided_95_lower": 0.88,
            },
        },
        "ambiguous_count": 60,
        "ambiguous_unique_assertion_rate": ambiguity,
        "positive_reference_coverage": coverage,
        "definite_negative_false_positive_rate": 0.0,
        "macro_f1": macro_f1,
        "unjudgeable_count": 5,
    }


def _episode(index: int, *, observation: str, hold: int = 0) -> EpisodeObservation:
    security_code = f"{100000 + index:06d}"
    phase = "active"
    close = Decimal("101")
    if observation in {"lost", "below"}:
        phase = "below"
        close = Decimal("99")
    elif observation == "reclaimed":
        phase = "reclaimed"
        close = Decimal("100.5")
    return EpisodeObservation(
        episode_id=f"episode-{index}",
        security_code=security_code,
        market_date=date(2022, 6, 1),
        scale=20,
        started_at=date(2022, 5, 1),
        anchor_key=f"anchor-{index}",
        anchor_revision=f"anchor-revision-{index}",
        frozen_level=Decimal("100"),
        frozen_low=Decimal("90"),
        phase=phase,
        observation=observation,
        exchange_session_age=hold,
        observed_bar_count=max(1, hold + 1),
        hold_count=hold,
        close=close,
        source_member_digest=f"member-{index}",
        interaction_ref=None,
        previous_loss_ref=None,
    )


def _qualification_population() -> tuple[list[EpisodeObservation], list[ActivityObservation]]:
    episodes: list[EpisodeObservation] = []
    activity: list[ActivityObservation] = []
    index = 0
    started_buckets = (
        (25, Decimal("0.9")),
        (60, Decimal("1.1")),
        (60, Decimal("1.3")),
        (55, Decimal("1.6")),
    )
    for count, ratio in started_buckets:
        for _ in range(count):
            episode = _episode(index, observation="started")
            episodes.append(episode)
            activity.append(
                ActivityObservation(
                    security_code=episode.security_code,
                    market_date=episode.market_date,
                    window=20,
                    available=True,
                    volume_ratio=ratio,
                    reason="available",
                    source_member_digest=f"activity-{index}",
                )
            )
            index += 1
    for hold in (1, 2, 3):
        for _ in range(60):
            episodes.append(_episode(index, observation="held", hold=hold))
            index += 1
    for _ in range(100):
        episodes.append(_episode(index, observation="lost", hold=2))
        index += 1
    for _ in range(80):
        episodes.append(_episode(index, observation="reclaimed", hold=2))
        index += 1
    for _ in range(80):
        episodes.append(_episode(index, observation="held", hold=40))
        index += 1
    assert index == 640
    return episodes, activity


def test_qualification_sampler_closes_exact_unique_episode_strata() -> None:
    config = load_qualification_config(CONFIG)
    episodes, activity = _qualification_population()
    contexts = select_qualification_contexts(
        episodes,
        activity,
        report_start=date(2022, 1, 4),
        report_end=date(2024, 12, 31),
        sampling=config["sampling"],
    )
    assert len(contexts) == 640
    assert len({item.context_id for item in contexts}) == 640
    assert len({item.episode_id for item in contexts}) == 640
    counts = {
        stratum: sum(item.sampling_stratum == stratum for item in contexts)
        for stratum in {item.sampling_stratum for item in contexts}
    }
    assert counts == {
        "qualification_started_activity": 200,
        "qualification_early_held": 180,
        "qualification_near_anchor_loss_negative": 100,
        "qualification_reclaimed_ambiguity": 80,
        "qualification_mature_held": 80,
    }


def test_qualification_sampler_does_not_backfill_a_missing_preregistered_stratum() -> None:
    config = load_qualification_config(CONFIG)
    episodes, activity = _qualification_population()
    missing = next(item for item in episodes if item.observation == "started")
    episodes.remove(missing)
    activity = [item for item in activity if item.security_code != missing.security_code]
    with pytest.raises(StageQualificationError, match="unique episodes; need"):
        select_qualification_contexts(
            episodes,
            activity,
            report_start=date(2022, 1, 4),
            report_end=date(2024, 12, 31),
            sampling=config["sampling"],
        )


def test_explicit_successor_caps_keep_shortfall_without_backfilling_or_changing_legacy() -> None:
    config = load_qualification_config(CONFIG)
    episodes, activity = _qualification_population()
    episodes = [e for e in episodes if e.observation != "reclaimed"]
    contexts = select_qualification_contexts(
        episodes, activity, report_start=date(2022, 1, 4), report_end=date(2024, 12, 31),
        sampling=config["sampling"], target_caps=True,
    )
    assert len(contexts) == 560
    assert len({c.episode_id for c in contexts}) == 560
    assert not any(c.sampling_stratum == "qualification_reclaimed_ambiguity" for c in contexts)
    with pytest.raises(StageQualificationError, match="unique episodes; need"):
        select_qualification_contexts(episodes, activity, report_start=date(2022, 1, 4),
                                      report_end=date(2024, 12, 31), sampling=config["sampling"])


def test_qualification_hard_gate_passes_only_complete_preregistered_evidence() -> None:
    config = load_qualification_config(CONFIG)
    gates = config["hard_gates"]
    r0 = _score(macro_f1=0.88)
    result = _hard_gate(_score(), r0_score=r0, hard_gates=gates, reference_count=640)
    assert result["passed"] is True
    assert result["reason_codes"] == []

    ambiguity = _hard_gate(
        _score(ambiguity=0.11),
        r0_score=r0,
        hard_gates=gates,
        reference_count=640,
    )
    assert ambiguity["passed"] is False
    assert "ambiguous_unique_assertion_rate_above_gate" in ambiguity["reason_codes"]

    inferior = _hard_gate(
        _score(macro_f1=0.84),
        r0_score=r0,
        hard_gates=gates,
        reference_count=640,
    )
    assert inferior["passed"] is False
    assert "macro_f1_inferior_to_r0_beyond_tolerance" in inferior["reason_codes"]

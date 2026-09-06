from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.structure_economic_experiment import (
    C1_KEY,
    C2_KEY,
    EXPECTED_SCORE_MAPPING,
    StructureEconomicP2ReplayRuntime,
    _entry_action_map,
    _lifecycle_decision,
    build_economic_structure_observations,
    load_structure_economic_request,
)
from quanthus.structure_intelligence import (
    StructureAdmRow,
    StructureMvpState,
    StructureResearchAdm,
)

TZ = timezone(timedelta(hours=8))


def _row(
    market_date: date,
    state: StructureMvpState,
    *,
    close: str,
    prior_high: str | None,
    prior_low: str | None,
    volume_ratio: str | None,
    reason: str,
) -> StructureAdmRow:
    return StructureAdmRow(
        market_date=market_date,
        security_code="000001",
        dwd_security_id="dwd-security-1",
        snapshot_id=f"snapshot-{market_date.isoformat()}",
        knowledge_cutoff=datetime.combine(market_date, datetime.min.time(), tzinfo=TZ).replace(
            hour=15
        ),
        state=state,
        close=Decimal(close),
        prior_high20=None if prior_high is None else Decimal(prior_high),
        prior_low20=None if prior_low is None else Decimal(prior_low),
        volume_ratio20=None if volume_ratio is None else Decimal(volume_ratio),
        source_revision="sha256:" + "1" * 64,
        source_artifact_sha256="sha256:" + "2" * 64,
        reason_code=reason,
    )


def test_preregistered_request_is_exact_and_score_mapping_is_frozen(tmp_path: Path) -> None:
    source = Path("config/structure-economic-run.development.v1.json")
    request = load_structure_economic_request(source)

    assert request.digest == (
        "sha256:9a8a14a4855ddb189da2d8303c558a460ce781e63425942cda845e45e3ef66de"
    )
    assert request.variants == (
        "variant.native-baseline",
        "variant.structure-continuous",
        "variant.structure-coarse",
    )
    assert request.primary_comparison == "C2-C1"

    changed = json.loads(source.read_text(encoding="utf-8"))
    changed["score_mapping"]["coarse"]["breakout_accepted"] = "0.90"
    path = tmp_path / "changed.json"
    path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="score mapping differs"):
        load_structure_economic_request(path)


def test_continuous_and_coarse_scores_share_the_same_episode_memory() -> None:
    adm = StructureResearchAdm(
        upstream_identity="upstream-1",
        policy_digest="sha256:" + "3" * 64,
        rows=(
            _row(
                date(2020, 1, 3),
                StructureMvpState.BREAKOUT_ATTEMPT,
                close="101",
                prior_high="100",
                prior_low="90",
                volume_ratio="1.5",
                reason="close_above_prior_high20_with_volume",
            ),
            _row(
                date(2020, 1, 6),
                StructureMvpState.BREAKOUT_ATTEMPT,
                close="101.5",
                prior_high="101",
                prior_low="90",
                volume_ratio="1.1",
                reason="breakout_confirmation_pending",
            ),
            _row(
                date(2020, 1, 7),
                StructureMvpState.BREAKOUT_ACCEPTED,
                close="102",
                prior_high="101.5",
                prior_low="90",
                volume_ratio="1.0",
                reason="two_post_attempt_closes_hold_breakout",
            ),
            _row(
                date(2020, 1, 8),
                StructureMvpState.RANGE_INTACT,
                close="105",
                prior_high="106",
                prior_low="100",
                volume_ratio="1.0",
                reason="no_mvp_structure_trigger",
            ),
            _row(
                date(2020, 1, 9),
                StructureMvpState.SUPPORT_THREATENED,
                close="101",
                prior_high="106",
                prior_low="100",
                volume_ratio="1.0",
                reason="close_near_prior_low20",
            ),
            _row(
                date(2020, 1, 10),
                StructureMvpState.SUPPORT_BREACH,
                close="99",
                prior_high="106",
                prior_low="100",
                volume_ratio="1.0",
                reason="close_below_prior_low20",
            ),
        ),
    )

    observed = build_economic_structure_observations(adm)

    assert observed[0].active_attempt_level == Decimal("100")
    assert observed[0].attempt_hold_count == 0
    assert observed[0].coarse_signal == Decimal("0.50")
    assert observed[0].continuous_signal == Decimal("0.625")

    assert observed[1].active_attempt_level == Decimal("100")
    assert observed[1].attempt_hold_count == 1
    assert observed[1].coarse_signal == Decimal("0.50")
    assert observed[1].continuous_signal == Decimal("0.9375")

    assert observed[2].active_attempt_level == Decimal("100")
    assert observed[2].attempt_hold_count == 2
    assert observed[2].coarse_signal == Decimal("1.00")
    assert observed[2].continuous_signal == Decimal("1.00")

    assert observed[3].coarse_signal == Decimal("0.00")
    assert observed[3].continuous_signal == Decimal("0")
    assert observed[4].coarse_signal == Decimal("-0.50")
    assert observed[4].continuous_signal == Decimal("-0.75")
    assert observed[5].coarse_signal == Decimal("-1.00")
    assert observed[5].continuous_signal == Decimal("-1")


def test_lifecycle_decision_prioritizes_primary_coarse_vs_continuous_question() -> None:
    no_increment = {
        "C1-C0": {
            "material": True,
            "return_delta": "0.02",
            "maximum_drawdown_delta": "-0.01",
        },
        "C2-C0": {
            "material": True,
            "return_delta": "0.021",
            "maximum_drawdown_delta": "-0.011",
        },
        "C2-C1": {
            "material": False,
            "return_delta": "0.001",
            "maximum_drawdown_delta": "-0.001",
        },
    }
    assert (
        _lifecycle_decision(no_increment)
        == "continuous_facts_sufficient_no_material_coarse_increment"
    )

    dominated = {
        **no_increment,
        "C2-C0": {
            "material": True,
            "return_delta": "0.01",
            "maximum_drawdown_delta": "0.01",
        },
        "C2-C1": {
            "material": True,
            "return_delta": "-0.01",
            "maximum_drawdown_delta": "0.02",
        },
    }
    assert _lifecycle_decision(dominated) == "prefer_continuous_structure_facts"

    primary_dominated_with_nonmaterial_secondaries = {
        "C1-C0": {
            "material": False,
            "return_delta": "-0.002",
            "maximum_drawdown_delta": "-0.009",
        },
        "C2-C0": {
            "material": False,
            "return_delta": "-0.009",
            "maximum_drawdown_delta": "0.003",
        },
        "C2-C1": {
            "material": True,
            "return_delta": "-0.007",
            "maximum_drawdown_delta": "0.012",
        },
    }
    assert (
        _lifecycle_decision(primary_dominated_with_nonmaterial_secondaries)
        == "prefer_continuous_structure_facts"
    )

    primary_material_not_dominated = {
        **primary_dominated_with_nonmaterial_secondaries,
        "C2-C0": {
            "material": False,
            "return_delta": "0.009",
            "maximum_drawdown_delta": "0.003",
        },
        "C2-C1": {
            "material": True,
            "return_delta": "0.011",
            "maximum_drawdown_delta": "0.012",
        },
    }
    assert (
        _lifecycle_decision(primary_material_not_dominated)
        == "coarse_state_increment_detected_requires_human_review"
    )

    none = {
        key: {
            "material": False,
            "return_delta": "0",
            "maximum_drawdown_delta": "0",
        }
        for key in ("C1-C0", "C2-C0", "C2-C1")
    }
    assert _lifecycle_decision(none) == "no_material_structure_signal"


def test_structure_runtime_keeps_held_path_native_and_accepts_only_three_variants() -> None:
    runtime = object.__new__(StructureEconomicP2ReplayRuntime)
    assert runtime._held_intervention_id(C1_KEY) is None
    assert runtime._held_intervention_id(C2_KEY) is None
    assert runtime._variant_id(None) == "variant.native-baseline"
    assert runtime._variant_id(C1_KEY) == "variant.structure-continuous"
    assert runtime._variant_id(C2_KEY) == "variant.structure-coarse"
    with pytest.raises(ValueError, match="not admitted"):
        runtime._validate_intervention_id("anything-else")


def test_entry_action_pairing_ignores_downstream_held_path_divergence() -> None:
    baseline = [
        {
            "market_date": "2020-01-02",
            "decision_key": "entry:event-a",
            "surface": "entry",
            "final_action": "trial_entry",
        },
        {
            "market_date": "2020-01-03",
            "decision_key": "held:capital-unit-01",
            "surface": "held",
            "final_action": "no_action",
        },
    ]
    candidate = [
        {
            "market_date": "2020-01-02",
            "decision_key": "entry:event-a",
            "surface": "entry",
            "final_action": "no_action",
        },
        {
            "market_date": "2020-01-03",
            "decision_key": "held:capital-unit-02",
            "surface": "held",
            "final_action": "reduce",
        },
    ]

    assert set(_entry_action_map(baseline)) == set(_entry_action_map(candidate))
    assert _entry_action_map(baseline) != _entry_action_map(candidate)


def test_entry_action_pairing_rejects_candidate_population_drift() -> None:
    with pytest.raises(ValueError, match="duplicates"):
        _entry_action_map(
            [
                {
                    "market_date": "2020-01-02",
                    "decision_key": "entry:event-a",
                    "surface": "entry",
                    "final_action": "trial_entry",
                },
                {
                    "market_date": "2020-01-02",
                    "decision_key": "entry:event-a",
                    "surface": "entry",
                    "final_action": "no_action",
                },
            ]
        )


def test_expected_score_mapping_matches_preregistered_surface() -> None:
    assert EXPECTED_SCORE_MAPPING["coarse"] == {
        "unavailable": None,
        "range_intact": "0.00",
        "breakout_attempt": "0.50",
        "breakout_accepted": "1.00",
        "support_threatened": "-0.50",
        "support_breach": "-1.00",
    }

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, cast

import pytest

from quanthus.expert_evaluation.serialization import ExpertEvaluationError, digest_value
from quanthus.expert_strategy import load_expert_strategy_card

ROOT = Path(__file__).resolve().parents[2]
CARD = ROOT / "config/expert-strategies/p4-trend-risk.v1.json"
SEMANTIC_EXCLUDES = {"lifecycle", "owner", "governance", "semantic_digest"}


def _raw() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(CARD.read_text(encoding="utf-8")))


def _semantic_digest(raw: dict[str, Any]) -> str:
    return digest_value({key: value for key, value in raw.items() if key not in SEMANTIC_EXCLUDES})


def _mapping_digest(mapping: dict[str, Any]) -> str:
    return digest_value({key: value for key, value in mapping.items() if key != "mapping_digest"})


def _position_digest(expert: dict[str, Any]) -> str:
    return digest_value(
        {key: value for key, value in expert.items() if key != "expert_semantic_digest"}
    )


def _reseal(raw: dict[str, Any]) -> None:
    raw["semantic_digest"] = _semantic_digest(raw)


def _write(tmp_path: Path, raw: dict[str, Any]) -> Path:
    path = tmp_path / "strategy.json"
    path.write_text(json.dumps(raw, ensure_ascii=True, sort_keys=True), encoding="utf-8")
    return path


def test_proposal_closes_exact_two_expert_topology_without_results() -> None:
    card = load_expert_strategy_card(CARD, ROOT)

    assert card.semantic_digest == (
        "sha256:b0a37af05f14bf9cd23e4bf3fe22b546d88646dbad5696f4700dcfd7204a0716"
    )
    assert tuple(item["expert_id"] for item in card.experts) == (
        "expert.trend-stability",
        "expert.position-risk-review",
    )
    assert tuple(item["intervention_id"] for item in card.interventions) == (
        "intervention.trend-stability-abstain",
        "intervention.position-risk-base-policy",
        "intervention.both-experts-fallback",
    )


def test_pending_human_approval_blocks_implementation_and_worker_access() -> None:
    card = load_expert_strategy_card(CARD, ROOT)

    with pytest.raises(ExpertEvaluationError) as error:
        card.require_implementation_authority()

    assert error.value.reason_code == "strategy_card_not_approved"


def test_human_approval_metadata_can_change_without_rewriting_semantics(tmp_path: Path) -> None:
    raw = _raw()
    raw["lifecycle"] = "approved"
    raw["governance"] = {
        "approval_state": "approved",
        "approval_authority": "human",
        "decision_reference": "https://github.com/rv198-star/Quanthus/issues/149#approved",
    }
    card = load_expert_strategy_card(_write(tmp_path, raw), ROOT)

    card.require_implementation_authority()
    assert card.semantic_digest == _raw()["semantic_digest"]


def test_assessment_population_must_match_the_accepted_target(tmp_path: Path) -> None:
    raw = _raw()
    raw["population"]["assessment_population"]["digest"] = f"sha256:{'1' * 64}"
    _reseal(raw)

    with pytest.raises(ExpertEvaluationError) as error:
        load_expert_strategy_card(_write(tmp_path, raw), ROOT)

    assert error.value.reason_code == "assessment_population_mismatch"


def test_arbitration_or_blind_scope_is_rejected_before_result_access(tmp_path: Path) -> None:
    raw = _raw()
    raw["population"]["applicability_end"] = "2025-12-31"
    _reseal(raw)

    with pytest.raises(ExpertEvaluationError) as error:
        load_expert_strategy_card(_write(tmp_path, raw), ROOT)

    assert error.value.reason_code == "sealed_partition_access"


def test_fixture_identity_cannot_become_formal_strategy_lineage(tmp_path: Path) -> None:
    raw = _raw()
    raw["strategy_id"] = "candidate.market-breadth-trend-breakout"
    raw["product_candidate"]["strategy_id"] = raw["strategy_id"]
    _reseal(raw)

    with pytest.raises(ExpertEvaluationError) as error:
        load_expert_strategy_card(_write(tmp_path, raw), ROOT)

    assert error.value.reason_code == "fixture_lineage_forbidden"


def test_missing_or_extra_active_expert_fails_closed(tmp_path: Path) -> None:
    raw = _raw()
    raw["experts"] = raw["experts"][:1]
    _reseal(raw)

    with pytest.raises(ExpertEvaluationError) as error:
        load_expert_strategy_card(_write(tmp_path, raw), ROOT)

    assert error.value.reason_code == "topology_mismatch"


def test_all_scoring_abstention_cannot_become_a_neutral_entry(tmp_path: Path) -> None:
    raw = _raw()
    raw["aggregation"]["all_scoring_abstained_action"] = "neutral_score"
    _reseal(raw)

    with pytest.raises(ExpertEvaluationError) as error:
        load_expert_strategy_card(_write(tmp_path, raw), ROOT)

    assert error.value.reason_code == "unsafe_fallback"


def test_system_guardians_cannot_be_removed_by_strategy_attribution(tmp_path: Path) -> None:
    raw = _raw()
    raw["position_and_execution"]["hard_guardians"].remove("funding")
    _reseal(raw)

    with pytest.raises(ExpertEvaluationError) as error:
        load_expert_strategy_card(_write(tmp_path, raw), ROOT)

    assert error.value.reason_code == "guardian_mismatch"


def test_joint_fallback_must_block_new_and_preserve_base_management(tmp_path: Path) -> None:
    raw = _raw()
    raw["interventions"][2]["fallback_id"] = "fallback.neutral-score"
    _reseal(raw)

    with pytest.raises(ExpertEvaluationError) as error:
        load_expert_strategy_card(_write(tmp_path, raw), ROOT)

    assert error.value.reason_code == "unsafe_fallback"


def test_mapping_and_position_expert_digests_are_independently_bound(tmp_path: Path) -> None:
    raw = _raw()
    changed = copy.deepcopy(raw)
    trend_mapping = changed["experts"][0]["policy_mapping"]
    trend_mapping["state_effects"]["stable"]["signal"] = "0.80"
    trend_mapping["mapping_digest"] = _mapping_digest(trend_mapping)
    changed["semantic_digest"] = _semantic_digest(changed)

    assert changed["semantic_digest"] != raw["semantic_digest"]
    card = load_expert_strategy_card(_write(tmp_path, changed), ROOT)
    assert card.semantic_digest == changed["semantic_digest"]

    tampered = copy.deepcopy(raw)
    position = tampered["experts"][1]
    position["entry_policy"]["maximum_rsi14"] = "76.00"
    _reseal(tampered)
    with pytest.raises(ExpertEvaluationError) as error:
        load_expert_strategy_card(_write(tmp_path, tampered), ROOT)
    assert error.value.reason_code == "digest_mismatch"
    assert position["expert_semantic_digest"] != _position_digest(position)


def test_card_contains_no_observed_strategy_result_fields() -> None:
    serialized = json.dumps(_raw(), sort_keys=True)

    for forbidden in (
        "observed_return",
        "observed_drawdown",
        "metric_result",
        "sample_count",
        "winning_mapping",
    ):
        assert forbidden not in serialized


def test_unknown_nested_strategy_parameter_is_rejected(tmp_path: Path) -> None:
    raw = _raw()
    raw["aggregation"]["runtime_weight_search"] = True
    _reseal(raw)

    with pytest.raises(ExpertEvaluationError) as error:
        load_expert_strategy_card(_write(tmp_path, raw), ROOT)

    assert error.value.reason_code == "field_mismatch"

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from quanthus.expert_evaluation.serialization import ExpertEvaluationError, digest_value
from quanthus.expert_evaluation.target_cards import load_target_decision_card

ROOT = Path(__file__).resolve().parents[2]
CARD = ROOT / "config/expert-evaluations/targets/p4-event-first-passage-reuse.v1.json"


def _raw_card() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(CARD.read_text(encoding="utf-8")))


def _semantic_digest(raw: dict[str, Any]) -> str:
    return digest_value(
        {
            key: value
            for key, value in raw.items()
            if key not in {"lifecycle", "owner", "governance", "semantic_digest"}
        }
    )


def _write_card(tmp_path: Path, raw: dict[str, Any]) -> Path:
    path = tmp_path / "target.json"
    path.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")
    return path


def test_first_target_reuses_approved_p4_cards_without_result_access() -> None:
    card = load_target_decision_card(CARD, ROOT)

    assert card.semantic_digest == (
        "sha256:a67accc8688b2a63ef01afff160666119b72b5c0ec4adc421d266104e76087f5"
    )
    assert card.eligible_population.predicate_digest == (
        "sha256:a66195a194933ca3b0e3850c7fb515a28b5125eff1b29241d13f953565c9961c"
    )
    card.require_result_access_authority()
    assert card.resolved_decision(ROOT) == {
        "subject_kind": "opportunity",
        "subject_identity_field": "event_id",
        "population_digest": card.eligible_population.predicate_digest,
        "evaluation_start": "2020-01-01",
        "evaluation_end": "2021-12-31",
        "observation_anchor": "candidate_t_close",
        "target_entry_anchor": "T_plus_1_actual_executable_open",
        "horizon_trading_sessions": 20,
        "outcome_values": ["failure", "neutral", "positive"],
        "not_executable_treatment": "sample_ledger_only",
        "structural_break_digest": (
            "sha256:1294e0550361bafe9ecfbcfddb32a7bb1ff378332c4fb173db662f95debad9fc"
        ),
        "primary_metric_id": "metric.mean-absolute-ordinal-error",
        "baseline_id": "baseline.always-neutral-ordinal",
        "claim_scope": "engineering_settleability_only",
    }


def test_draft_target_blocks_result_bearing_access(tmp_path: Path) -> None:
    raw = _raw_card()
    raw["lifecycle"] = "draft"
    raw["governance"] = {
        "approval_state": "pending_human_approval",
        "approval_authority": "human",
        "decision_reference": "https://github.com/rv198-star/Quanthus/issues/148",
    }
    card = load_target_decision_card(_write_card(tmp_path, raw), ROOT)

    with pytest.raises(ExpertEvaluationError) as error:
        card.require_result_access_authority()

    assert error.value.reason_code == "target_decision_not_approved"


def test_lifecycle_metadata_does_not_change_exact_semantics(tmp_path: Path) -> None:
    raw = _raw_card()
    raw["lifecycle"] = "draft"
    raw["governance"] = {
        "approval_state": "pending_human_approval",
        "approval_authority": "human",
        "decision_reference": "https://github.com/rv198-star/Quanthus/issues/148",
    }
    card = load_target_decision_card(_write_card(tmp_path, raw), ROOT)

    assert card.semantic_digest == _raw_card()["semantic_digest"]


def test_reused_p4_target_digest_drift_fails_closed(tmp_path: Path) -> None:
    raw = _raw_card()
    raw["outcome_card"]["semantic_digest"] = f"sha256:{'0' * 64}"
    raw["semantic_digest"] = _semantic_digest(raw)

    with pytest.raises(ExpertEvaluationError) as error:
        load_target_decision_card(_write_card(tmp_path, raw), ROOT)

    assert error.value.reason_code == "target_reference_mismatch"


def test_population_predicate_drift_fails_before_target_resolution(tmp_path: Path) -> None:
    raw = _raw_card()
    raw["eligible_population"]["evaluation_end"] = "2022-01-03"
    raw["semantic_digest"] = _semantic_digest(raw)

    with pytest.raises(ExpertEvaluationError) as error:
        load_target_decision_card(_write_card(tmp_path, raw), ROOT)

    assert error.value.reason_code == "sealed_partition_access"


def test_unknown_target_decision_field_is_rejected(tmp_path: Path) -> None:
    raw = _raw_card()
    raw["realized_base_rate"] = "forbidden"

    with pytest.raises(ExpertEvaluationError) as error:
        load_target_decision_card(_write_card(tmp_path, raw), ROOT)

    assert error.value.reason_code == "field_mismatch"


def test_proposal_contains_no_result_bearing_fields() -> None:
    raw = _raw_card()
    serialized = json.dumps(raw, sort_keys=True)

    assert "base_rate" not in serialized
    assert "realized_label" not in serialized
    assert "metric_result" not in serialized
    assert "sample_count" not in serialized

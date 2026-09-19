from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REQUEST = ROOT / "config/research/p3-strat-003/request.v1.json"


def _digest_without_declared(value: dict[str, object]) -> str:
    payload = {key: item for key, item in value.items() if key != "semantic_digest"}
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def test_p3_strat_003_request_is_pre_result_and_digest_bound() -> None:
    request = json.loads(REQUEST.read_text(encoding="utf-8"))

    assert request["schema_version"] == "quanthus.p3-strat-003-research-request.v1"
    assert request["work_item"] == "P3-STRAT-003-A"
    assert request["state"] == "FROZEN_PRE_RESULT"
    assert request["semantic_digest"] == _digest_without_declared(request)
    assert request["semantic_digest"] == (
        "sha256:9de79141514aa1941d276da854d734786bb2b5bb83310a1d3c7792d60c952bde"
    )
    assert request["authority"]["result_read_before_a_acceptance"] is False


def test_p3_strat_003_freezes_exact_recent_history_and_consumes_2025_once() -> None:
    request = json.loads(REQUEST.read_text(encoding="utf-8"))
    data = request["data_use"]

    assert data["validation_2025"] == {
        "market_date_start": "2025-01-01",
        "market_date_end": "2025-12-31",
        "prior_policy_role": "permanent_blind",
        "authorized_role_for_this_request": "one_time_bounded_major_strategy_validation",
        "consume_blind_status_at": "first_result_bearing_D_read",
        "post_consumption_status": "exposed_for_successor_research_not_claimable_as_untouched",
        "parameter_search_authorized": False,
    }
    assert data["validation_2026"]["market_date_start"] == "2026-01-01"
    assert data["validation_2026"]["market_date_end"] == "2026-09-18"
    assert data["validation_2026"]["roll_forward_authorized"] is False
    assert data["arbitration_2022_2024"]["general_result_access"] is False
    warmup = data["arbitration_2022_2024"]["warmup_exception"]
    assert warmup["authorized"] is True
    assert warmup["maximum_sessions"] == 60
    assert warmup["result_labels_or_strategy_outcomes_forbidden"] is True
    assert data["terminal_handling"]["wait_for_future_maturity_before_D_close"] is False


def test_p3_strat_003_has_one_nonempty_control_and_exact_factorial() -> None:
    request = json.loads(REQUEST.read_text(encoding="utf-8"))
    variants = {item["variant_id"]: item for item in request["variants"]}

    assert set(variants) == {"B", "T", "R", "TR"}
    baseline = variants["B"]["entry_scorer"]
    assert baseline == {
        "kind": "deterministic_research_control",
        "control_id": "control.p4-candidate-pass-through",
        "control_version": "1.0.0",
        "applicable_signal": "1.00",
        "confidence": "1.00",
        "abstain_when_shared_entry_evidence_missing": True,
    }
    assert variants["B"]["trend_policy_effect"] == "disabled"
    assert variants["B"]["position_risk_strategy_effect"] == "disabled"
    assert variants["T"]["trend_policy_effect"] == "enabled_exact_v1"
    assert variants["T"]["position_risk_strategy_effect"] == "disabled"
    assert variants["R"]["trend_policy_effect"] == "disabled"
    assert variants["R"]["position_risk_strategy_effect"] == "enabled_exact_v1"
    assert variants["TR"]["trend_policy_effect"] == "enabled_exact_v1"
    assert variants["TR"]["position_risk_strategy_effect"] == "enabled_exact_v1"
    assert all(item["system_guardians"] == "unchanged" for item in variants.values())
    assert request["common_input_rules"]["shared_entry_complete_case_mask"].startswith("union of")


def test_p3_strat_003_freezes_claims_uncertainty_and_budget() -> None:
    request = json.loads(REQUEST.read_text(encoding="utf-8"))

    assert request["comparisons"]["headline"] == "TR-B"
    assert request["comparisons"]["factorial_diagnostics"] == [
        "T-B",
        "R-B",
        "TR-R",
        "TR-T",
        "TR-T-R+B",
    ]
    assert request["decision_rules"]["materiality"] == {
        "net_total_return_delta": "0.01",
        "maximum_drawdown_delta": "0.01",
        "action_change_rate": "0.02",
    }
    assert request["uncertainty"]["block_sessions"] == 22
    assert request["uncertainty"]["resamples"] == 2000
    assert request["uncertainty"]["confidence_level"] == "0.95"
    assert request["multiple_comparison_policy"]["single_headline_claim"] == "TR-B combined window"
    assert request["multiple_comparison_policy"]["result_driven_selection_or_ranking"] is False

    budget = request["resource_budget"]
    assert budget["result_bearing_composite_batches"] == 2
    assert budget["batch_roles"] == ["primary", "independent_cold_reproduction"]
    assert budget["variants_per_batch"] == 4
    assert budget["worker_concurrency"] == 1
    assert budget["provider_builds_per_batch"] == 1
    assert budget["hard_container_gib"] == 16
    assert budget["cross_batch_cache_for_independent_reproduction"] is False
    assert "no post-result retry" in budget["technical_recovery"]


def test_p3_strat_003_preserves_p6_and_allows_negative_closeout() -> None:
    request = json.loads(REQUEST.read_text(encoding="utf-8"))

    assert request["authority"]["p6_is_predecessor"] is False
    assert request["authority"]["p6_mutation_authorized"] is False
    assert request["authority"]["broker_or_live_capital_authorized"] is False
    assert request["authority"]["new_expert_authorized"] is False
    assert request["authority"]["aggregator_search_authorized"] is False
    assert request["decision_rules"]["negative_or_inconclusive_result_closes_honestly"] is True
    assert "P6_worker_ledger_budget_or_pointer_mutation" in request["stop_conditions"]
    assert "not_supported_or_material_harm" in request["closeout_outcomes"]
    assert "inconclusive" in request["closeout_outcomes"]


def test_p3_strat_003_binds_exact_preexisting_authorities() -> None:
    request = json.loads(REQUEST.read_text(encoding="utf-8"))

    for binding in request["bindings"].values():
        if not isinstance(binding, dict) or "path" not in binding:
            continue
        path = ROOT / binding["path"]
        observed = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        assert observed == binding["sha256"], binding["path"]

from __future__ import annotations

from decimal import Decimal

from quanthus.stage_learning import directional_boundary_evaluation as api


def _history(values: list[Decimal]) -> list[list[str | None]]:
    return [
        [f"sha256:{index:064x}", f"2021-01-{index + 1:02d}", str(value), str(value),
         str(value), "epoch", None]
        for index, value in enumerate(values)
    ]


def _case(index: int, kind: str) -> dict:
    case_id = "sha256:" + f"{index + 1:064x}"
    if kind == "stage0":
        closes = [Decimal(100) + (Decimal(6) if i % 2 else Decimal(-6)) for i in range(20)]
        closes.append(Decimal(100))
        efficiency, width_change, center = "0.05", "0.30", "0.10"
    elif kind == "directional_negative":
        closes = [Decimal(100 + i) for i in range(21)]
        efficiency, width_change, center = "0.30", "0.30", "0.10"
    else:
        closes = [Decimal(100) for _ in range(21)]
        efficiency, width_change, center = "0.05", "0.00", "0.00"
    return {
        "case_id": case_id,
        "primary_available": True,
        "primary": {
            "directional_efficiency": {"reason": "available", "value": efficiency},
            "range_width": {"reason": "available", "value": "0.30"},
            "width_change": {"reason": "available", "value": width_change},
            "center_shift": {"reason": "available", "value": center},
        },
        "history": _history(closes),
    }


def _ledger(cases: list[dict], positive_count: int) -> dict:
    rows = []
    for index, _case in enumerate(cases):
        is_positive = index < positive_count
        primary = {
            "structural_instability_20": "TRUE" if is_positive else "FALSE",
            "coherent_directional_relocation_20": "FALSE",
            "orderly_consolidation_20": "FALSE",
        }
        rows.append({
            "index": index,
            "short_term": dict(primary),
            "fund": dict(primary),
            "short_reason": "synthetic primary judgement",
            "fund_reason": "synthetic primary judgement",
        })
    return {"sha256": "sha256:" + "f" * 64, "rows": rows}


def test_frozen_candidate_contract_is_exact_and_has_no_evaluation_search() -> None:
    raw, candidate = api.load_candidate()
    assert candidate.candidate_id == raw["frozen_candidate"]["candidate_id"]
    assert candidate.absolute_window_return20_min == Decimal("0.10")
    assert candidate.range_traversal20_min == Decimal("0.50")
    assert candidate.counter_direction_excursion_share20_max == Decimal("0.60")
    assert raw["evaluation_authority"]["evaluation_parameter_search"] is False
    assert raw["evaluation_authority"]["candidate_replacement_after_evaluation_read"] is False
    assert raw["stage_2"]["rule_changed"] is False


def test_frozen_candidate_passes_when_it_removes_directional_false_positives() -> None:
    cases = []
    cases.extend(_case(index, "stage0") for index in range(20))
    cases.extend(_case(index, "directional_negative") for index in range(20, 40))
    cases.extend(_case(index, "quiet_negative") for index in range(40, 95))
    result = api.evaluate(cases, _ledger(cases, 20))
    assert result["status"] == "PROMISING_EXPLORATORY_REPAIR"
    assert result["reasons"] == []
    assert result["baseline_stage0"]["false_positive"] == 20
    assert result["candidate_stage0"]["false_positive"] == 0
    assert result["candidate_stage0"]["true_positive"] == 20
    assert result["candidate_stage0"]["precision"] == Decimal(1)
    assert result["stage2_predictions_unchanged"] is True
    assert result["evaluation_parameter_search"] is False
    assert result["candidate_replacement_after_evaluation_read"] is False


def test_evaluation_fails_closed_when_positive_denominator_is_too_small() -> None:
    cases = []
    cases.extend(_case(index, "stage0") for index in range(8))
    cases.extend(_case(index, "directional_negative") for index in range(8, 28))
    cases.extend(_case(index, "quiet_negative") for index in range(28, 95))
    result = api.evaluate(cases, _ledger(cases, 8))
    assert result["status"] == "REVISE_OR_PARK_EVALUATION_FAIL"
    assert "absolute_stage0_gate_fails" in result["reasons"]
    assert result["official_label_authorized"] is False
    assert result["strategy_or_trading_authorized"] is False

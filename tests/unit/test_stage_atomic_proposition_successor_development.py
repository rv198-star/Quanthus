from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from quanthus.policy import find_project_root
from quanthus.stage_learning.atomic_proposition_successor_development_prepare import (
    AtomicPropositionSuccessorPrepareError,
    _case_selection,
    load_execution,
    load_preregistration,
    load_preregistration_revision,
)

ROOT = find_project_root()
PROTOCOL = ROOT / "config/stage-learning/order-reference-evidence-scope-p013.v1.json"


def _plan_surface() -> tuple[list[dict], list[dict]]:
    cells = (
        ("PRE_STAR", "MAIN"),
        ("PRE_STAR", "CHINEXT"),
        ("STAR_PRE_CHINEXT_20PCT", "MAIN"),
        ("STAR_PRE_CHINEXT_20PCT", "CHINEXT"),
        ("STAR_PRE_CHINEXT_20PCT", "STAR"),
        ("STAR_AND_CHINEXT_20PCT", "MAIN"),
        ("STAR_AND_CHINEXT_20PCT", "CHINEXT"),
        ("STAR_AND_CHINEXT_20PCT", "STAR"),
    )
    plan: list[dict] = []
    features: list[dict] = []
    base = date(2018, 1, 1)
    for cell_index, (era, board) in enumerate(cells, start=1):
        for slot in range(1, 33):
            end = base + timedelta(days=cell_index * 100 + slot)
            role = "fallback" if slot % 4 == 0 else "primary"
            code = f"{cell_index:02d}{slot:04d}"[-6:]
            row = {
                "security_id": f"sec-{cell_index:02d}-{slot:02d}",
                "security_code": code,
                "era": era,
                "board_family": board,
                "window_id": f"{era}|{board}|{slot:02d}",
                "window_start": (end - timedelta(days=60)).isoformat(),
                "window_end": end.isoformat(),
                "role": role,
                "cell_slot": slot,
            }
            plan.append(row)
            features.append(
                {
                    "security_code": code,
                    "market_date": end,
                    "range_width20": "0.10",
                    "width_change20": "0.02",
                    "center_shift20_abs": "0.01",
                    "efficiency20_abs": "0.20",
                    "range_traversal20": "0.30",
                    "counter_direction_excursion_share20": "0.20",
                }
            )
    return plan, features


def test_successor_contracts_are_bound_before_market_read() -> None:
    prereg = load_preregistration(ROOT)
    revision = load_preregistration_revision(ROOT)
    execution = load_execution(ROOT)
    assert prereg["state"] == "PREREGISTERED_EXACT_PLAN_BEFORE_RESULT_BEARING_MARKET_READ"
    assert revision["revision"]["case_t_selection_after"] == "exact_plan_window_end_only"
    assert revision["authority"]["market_data_read_before_revision_freeze"] is False
    assert execution["runtime"] == {
        "maximum_jobs": 1,
        "automatic_retries": 0,
        "worker_wall_seconds": 3600,
    }
    assert execution["market_read"]["read_only_exact_plan_window_dates"] is True
    assert execution["market_read"]["price_rows_outside_window"] is False
    assert execution["authority"]["preserved_2021_evaluation_read"] is False
    assert execution["authority"]["2025_access"] is False


def test_successor_evidence_protocol_matches_exact_window_history() -> None:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    assert protocol["profile"]["retained_history_sessions"] == 61
    assert protocol["profile"]["primary_window_sessions"] == 20
    assert protocol["sampling"]["case_t"] == "exact_plan_window_end_only"
    assert protocol["sampling"]["cell_count"] == 8
    assert protocol["sampling"]["complete_cases_per_cell"] == 24
    assert protocol["bounds"]["max_cases"] == 192
    assert protocol["bounds"]["max_source_rows"] == 11712
    assert protocol["authority"]["preserved_2021_access"] is False
    assert protocol["authority"]["permanent_blind_access"] is False


def test_case_selection_uses_exact_window_end_and_keeps_eight_by_twenty_four() -> None:
    plan, features = _plan_surface()
    selected = _case_selection(plan, features)
    assert selected["selected_case_count"] == 192
    assert selected["fallback_used_count"] == 0
    assert selected["case_t_is_exact_window_end"] is True
    assert selected["selection_uses_morphology_values"] is False
    assert selected["selection_uses_labels_or_reference"] is False
    assert selected["selection_uses_candidate_output"] is False
    cells: dict[str, int] = {}
    by_identity = {row["security_id"]: row for row in plan}
    for row in selected["selected"]:
        cells[row["cell"]] = cells.get(row["cell"], 0) + 1
        assert row["market_date"] == by_identity[row["security_id"]]["window_end"]
        assert row["plan_role"] == "primary"
    assert len(cells) == 8
    assert set(cells.values()) == {24}


def test_same_cell_prefrozen_fallback_is_used_only_for_unavailable_primary() -> None:
    plan, features = _plan_surface()
    target_cell = "STAR_PRE_CHINEXT_20PCT/STAR"
    primary = next(
        row
        for row in plan
        if f"{row['era']}/{row['board_family']}" == target_cell and row["role"] == "primary"
    )
    features = [row for row in features if row["security_code"] != primary["security_code"]]
    selected = _case_selection(plan, features)
    assert selected["selected_case_count"] == 192
    assert selected["fallback_used_count"] == 1
    assert selected["fallback_used_by_cell"] == {target_cell: 1}
    target_rows = [row for row in selected["selected"] if row["cell"] == target_cell]
    assert len(target_rows) == 24
    assert sum(row["plan_role"] == "fallback" for row in target_rows) == 1
    assert selected["cross_cell_fill"] is False


def test_cell_fails_without_supplement_when_more_than_eight_slots_are_unavailable() -> None:
    plan, features = _plan_surface()
    target_cell = "STAR_PRE_CHINEXT_20PCT/STAR"
    target_codes = [
        row["security_code"]
        for row in plan
        if f"{row['era']}/{row['board_family']}" == target_cell and row["role"] == "primary"
    ][:9]
    features = [row for row in features if row["security_code"] not in set(target_codes)]
    with pytest.raises(
        AtomicPropositionSuccessorPrepareError,
        match="cell_complete_case_shortfall:STAR_PRE_CHINEXT_20PCT/STAR",
    ):
        _case_selection(plan, features)

from __future__ import annotations

import json
from datetime import date, timedelta
from types import SimpleNamespace

from quanthus.policy import find_project_root
from quanthus.stage_learning.development_resource_admission_screen import (
    _cell_summary,
    _reason_for_attempts,
    freeze_candidate_surface,
    load_config,
)

ROOT = find_project_root()
CONFIG = ROOT / "config/stage-learning/stage-development-resource-admission-screen.v1.json"
MODEL = ROOT / "config/stage-learning/stage-development-resource-admission-qualified-model.v1.json"


def _token(cell: str, identity_index: int, window_index: int) -> dict:
    era, family = cell.split("/", 1)
    start = date(2018, 1, 1) + timedelta(days=70 * window_index)
    board = {"MAIN": "SSE_main", "CHINEXT": "ChiNext", "STAR": "STAR"}[family]
    return {
        "security_id": f"sec-{identity_index:04d}",
        "listing_id": f"listing-{identity_index:04d}",
        "security_code": f"{identity_index:06d}"[-6:],
        "era": era,
        "board_family": family,
        "board_code": board,
        "venue_id": "venue-test",
        "window_id": f"{cell}|{window_index:02d}",
        "window_start": start,
        "window_end": start + timedelta(days=60),
        "open_session_count": 61,
        "exposure_level": "L0_METADATA_DISCOVERED",
    }


def _attempts(*, admitted: bool = True, reset_at: int | None = None) -> list:
    start = date(2018, 1, 1)
    output = []
    for index in range(61):
        changed = reset_at is not None and index >= reset_at
        row = SimpleNamespace(
            session_index=index,
            market_date=start + timedelta(days=index),
            listing_id="listing-1",
            security_id="sec-1",
            coordinate_ref="coordinate-2" if changed else "coordinate-1",
            reset_reason=("corporate_action_coordinate_change" if changed else None),
        )
        reason = "admitted_retrospective_only" if admitted else "missing_daily_bar"
        output.append(SimpleNamespace(row=row, admitted=admitted, reason=reason))
    return output


def test_config_freezes_admission_only_authority() -> None:
    value = load_config(CONFIG)
    assert value["runtime"] == {
        "maximum_jobs": 1,
        "automatic_retries": 0,
        "worker_wall_seconds": 2700,
    }
    assert value["qualification_target"]["required_unique_qualified_identities_per_cell"] == 32
    assert value["candidate_order"]["maximum_candidate_identities_per_cell"] == 256
    assert value["candidate_order"]["maximum_windows_per_identity"] == 4
    assert value["authority"]["input_readiness_market_read"] is True
    for key in (
        "semantic_market_read",
        "structure_fact_build",
        "threshold_or_model_basis",
        "reference_opinion",
        "candidate_output",
        "candidate_scoring",
        "preserved_2021_market_read",
        "2022_2024_read",
        "2025_read",
    ):
        assert value["authority"][key] is False


def test_candidate_surface_is_deterministic_bounded_and_excludes_consumed_token() -> None:
    model = json.loads(MODEL.read_text(encoding="utf-8"))
    cell = model["candidate_screen"]["cell_allocation_order"][0]
    tokens = [
        _token(cell, identity_index, window_index)
        for identity_index in range(1, 300)
        for window_index in range(1, 7)
    ]
    for extra_index, extra_cell in enumerate(
        model["candidate_screen"]["cell_allocation_order"][1:], start=1
    ):
        tokens.append(_token(extra_cell, 1000 + extra_index, 1))
    consumed = {("sec-0001", f"{cell}|01")}
    first = freeze_candidate_surface(tokens=tokens, consumed=consumed, model=model)
    second = freeze_candidate_surface(tokens=tokens, consumed=consumed, model=model)
    assert first == second
    cell_rows = [row for row in first if row["cell"] == cell]
    assert len({row["security_id"] for row in cell_rows}) == 256
    assert max(
        sum(row["security_id"] == identity for row in cell_rows)
        for identity in {row["security_id"] for row in cell_rows}
    ) <= 4
    assert not any(
        row["security_id"] == "sec-0001" and row["window_id"] == f"{cell}|01"
        for row in cell_rows
    )


def test_exact_61_session_admission_is_qualified() -> None:
    expected = {
        "window_end": (date(2018, 1, 1) + timedelta(days=60)).isoformat(),
        "listing_id": "listing-1",
        "security_id": "sec-1",
    }
    assert _reason_for_attempts(_attempts(), expected) == (
        True,
        "61_SESSION_CONTIGUOUS_INPUT_READY",
    )


def test_nonadmitted_or_coordinate_reset_window_fails_closed() -> None:
    expected = {
        "window_end": (date(2018, 1, 1) + timedelta(days=60)).isoformat(),
        "listing_id": "listing-1",
        "security_id": "sec-1",
    }
    assert _reason_for_attempts(_attempts(admitted=False), expected)[1] == (
        "MISSING_OR_INVALID_DAILY_INPUT"
    )
    assert _reason_for_attempts(_attempts(reset_at=40), expected)[1] == "COORDINATE_RESET"


def test_cell_summary_requires_32_unique_qualified_identities() -> None:
    ledger = [
        {
            "cell": "PRE_STAR/MAIN",
            "security_id": f"sec-{index:03d}",
            "qualified": index < 32,
            "reason": (
                "61_SESSION_CONTIGUOUS_INPUT_READY"
                if index < 32
                else "MISSING_OR_INVALID_DAILY_INPUT"
            ),
        }
        for index in range(40)
    ]
    summary, passed = _cell_summary(ledger, required=32)
    assert passed is True
    assert summary[0]["qualified_unique_identity_count"] == 32
    summary, passed = _cell_summary(ledger[:31], required=32)
    assert passed is False
    assert summary[0]["sufficient_for_plan_freeze"] is False

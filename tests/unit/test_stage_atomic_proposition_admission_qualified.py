from __future__ import annotations

import json

import pytest

from quanthus.policy import find_project_root
from quanthus.stage_learning.atomic_proposition_admission_qualified_prepare import (
    AtomicPropositionAdmissionQualifiedError,
    _runtime_plan_rows,
    load_execution,
    load_preregistration,
)

ROOT = find_project_root()


def test_p014_c_preregistration_binds_qualified_plan_and_frozen_atomic_surface() -> None:
    prereg = load_preregistration(ROOT)
    execution = load_execution(ROOT)

    assert prereg["work_item"] == "P3-STAGE-014-C"
    assert prereg["state"] == "PREREGISTERED_QUALIFIED_PLAN_BEFORE_SEMANTIC_MARKET_READ"
    assert prereg["resource_plan"] == {
        "source": "exact_P014_admission_qualified_plan",
        "cell_count": 8,
        "reserve_per_cell": 32,
        "primary_per_cell": 24,
        "fallback_per_cell": 8,
        "total_reserve": 256,
        "total_primary": 192,
        "total_fallback": 64,
        "global_identity_unique": True,
        "input_admission_qualified_before_plan_freeze": True,
        "cross_cell_fallback": False,
        "post_result_identity_replacement": False,
        "fallback_order": "qualified_plan_slot_ascending",
        "fallback_exhaustion": "FAIL_CELL_WITHOUT_SUPPLEMENT",
    }
    atomic = prereg["atomic_contract"]
    assert atomic["instability_candidate_count"] == 9
    assert atomic["relocation_candidate_count"] == 1
    assert atomic["order_profile_count"] == 3
    assert atomic["joint_pair_search_count"] == 0
    assert atomic["numeric_atomic_quality_gates"] == "exact_P012"
    assert atomic["projection_compatibility_numeric_gates"] == "exact_P012"
    assert atomic["candidate_surface_change"] is False
    assert prereg["authority"]["result_bearing_market_read"] is False
    assert prereg["authority"]["runtime_job_submission"] is False
    assert prereg["authority"]["preserved_2021_read"] is False
    assert prereg["authority"]["2025_read"] is False

    assert execution["work_item"] == "P3-STAGE-014-C1"
    assert execution["runtime"] == {
        "maximum_jobs": 1,
        "automatic_retries": 0,
        "worker_wall_seconds": 3600,
    }
    assert execution["market_read"]["group_by_exact_window"] is True
    assert execution["market_read"]["price_rows_outside_window"] is False
    assert execution["selection"]["cross_cell_fill"] is False
    assert execution["selection"]["post_result_identity_replacement"] is False
    assert execution["authority"]["parameter_search"] is False
    assert execution["authority"]["candidate_surface_change"] is False
    assert execution["authority"]["numeric_gate_change"] is False
    assert execution["authority"]["joint_pair_search"] is False
    assert execution["authority"]["preserved_2021_evaluation_read"] is False
    assert execution["authority"]["2025_access"] is False


def _synthetic_plan_rows() -> list[dict]:
    rows = []
    for index in range(256):
        rows.append(
            {
                "security_id": f"security-{index:03d}",
                "security_code": f"{index:06d}",
                "era": "PRE_STAR",
                "board_family": "MAIN",
                "board_code": "SSE_main",
                "venue_id": "venue-test",
                "window_id": f"window-{index:03d}",
                "window_start": "2018-01-01",
                "window_end": "2018-04-01",
                "plan_slot": index % 32 + 1,
                "role": "primary" if index % 32 < 24 else "fallback",
            }
        )
    return rows


def test_runtime_plan_projection_preserves_exact_identity_and_roles() -> None:
    rows = _runtime_plan_rows(_synthetic_plan_rows())
    assert len(rows) == 256
    assert len({row["security_id"] for row in rows}) == 256
    assert sum(row["role"] == "primary" for row in rows) == 192
    assert sum(row["role"] == "fallback" for row in rows) == 64
    assert all(row["open_session_count"] == 61 for row in rows)
    assert all(row["cell_slot"] == row["plan_slot"] for row in rows)


def test_runtime_plan_projection_fails_closed_on_role_or_identity_drift() -> None:
    rows = _synthetic_plan_rows()
    rows[0]["role"] = "replacement"
    with pytest.raises(AtomicPropositionAdmissionQualifiedError, match="qualified_plan_role"):
        _runtime_plan_rows(rows)

    rows = _synthetic_plan_rows()
    rows[1]["security_id"] = rows[0]["security_id"]
    with pytest.raises(
        AtomicPropositionAdmissionQualifiedError,
        match="qualified_plan_identity_count",
    ):
        _runtime_plan_rows(rows)


def test_machine_contracts_remain_json_objects() -> None:
    for path in (
        "config/stage-learning/stage-atomic-proposition-admission-qualified-development-screen.v1.json",
        "config/stage-learning/stage-atomic-proposition-admission-qualified-execution.v1.json",
    ):
        value = json.loads((ROOT / path).read_text(encoding="utf-8"))
        assert isinstance(value, dict)

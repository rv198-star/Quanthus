from __future__ import annotations

from datetime import date

from quanthus.policy import find_project_root
from quanthus.stage_learning.complete_reserve_review_readiness import (
    _readiness_reason,
    load_config,
)

ROOT = find_project_root()
CONFIG = ROOT / "config/stage-learning/stage-complete-reserve-review-readiness.v1.json"


def test_p015_a_contract_is_complete_reserve_readiness_only() -> None:
    value = load_config(CONFIG)
    assert value["work_item"] == "P3-STAGE-015-A"
    resource = value["resource_contract"]
    assert resource["exact_row_count"] == 256
    assert resource["exact_cell_count"] == 8
    assert resource["exact_rows_per_cell"] == 32
    assert resource["global_identity_unique"] is True
    assert resource["new_identity_discovery"] is False
    assert resource["new_random_sampling"] is False
    assert resource["morphology_enrichment"] is False
    assert resource["result_count_targeted_top_up"] is False
    continuation = value["continuation_gate"]
    assert continuation["minimum_total_ready"] == 192
    assert continuation["minimum_ready_per_cell"] == 24
    assert continuation["all_p014_reviewed_rows_must_remain_ready"] is True
    assert value["runtime"] == {
        "maximum_jobs": 1,
        "automatic_retries": 0,
        "worker_wall_seconds": 3600,
    }
    authority = value["authority"]
    assert authority["exact_window_market_read"] is True
    assert authority["review_readiness_computation"] is True
    for key in (
        "persist_price_or_morphology_values",
        "reference_opinion_read",
        "reference_opinion_write",
        "p014_review_ledger_rewrite",
        "selection_transport_read",
        "candidate_output_read",
        "candidate_scoring",
        "parameter_search",
        "numeric_gate_change",
        "preserved_2021_market_read",
        "2022_2024_read",
        "2025_read",
    ):
        assert authority[key] is False
    assert set(value["retention"]["allowed_fields"]) == {
        "security_id",
        "window_id",
        "era",
        "board_family",
        "cell",
        "review_packet_ready",
        "readiness_reason",
        "source_member_closure_digest",
        "already_reviewed_in_p014_ledger",
        "new_review_required",
    }


def test_readiness_reason_uses_only_fact_availability() -> None:
    t = date(2020, 6, 30)
    key = ("000001", t)
    paths = {
        key: {
            "structure.geometry.range-traversal": {
                "value": "0.91",
                "reason": "available",
            },
            "structure.geometry.counter-direction-excursion-share": {
                "value": None,
                "reason": "zero_net_direction",
            },
        }
    }
    assert _readiness_reason(
        security_code="000001",
        t=t,
        observation_keys={key},
        path_values=paths,
    ) == "READY"
    assert _readiness_reason(
        security_code="000001",
        t=t,
        observation_keys=set(),
        path_values=paths,
    ) == "PRIMARY_FACTS_UNAVAILABLE"

    changed = {key: dict(paths[key])}
    changed[key]["structure.geometry.range-traversal"] = {
        "value": None,
        "reason": "insufficient_history",
    }
    assert _readiness_reason(
        security_code="000001",
        t=t,
        observation_keys={key},
        path_values=changed,
    ) == "RANGE_TRAVERSAL_UNAVAILABLE"

    changed = {key: dict(paths[key])}
    changed[key]["structure.geometry.counter-direction-excursion-share"] = {
        "value": None,
        "reason": "insufficient_history",
    }
    assert _readiness_reason(
        security_code="000001",
        t=t,
        observation_keys={key},
        path_values=changed,
    ) == "COUNTER_EXCURSION_UNAVAILABLE"

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from quanthus.stage_learning.directional_boundary_evaluation_prepare import (
    CONFIG_DIGEST,
    _rank,
    load_config,
    select_evaluation_cases,
)
from quanthus.stage_learning.order_proposition_development import OrderObservation
from quanthus.structure_v2_inputs import digest


def observation(code: str, day: date, *, width: str) -> OrderObservation:
    return OrderObservation(
        code,
        day,
        digest({"code": code, "day": day, "width": width}),
        Decimal("0.1"),
        Decimal(width),
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
    )


def evaluation_plan() -> dict:
    rows = []
    for index in range(96):
        rows.append({
            "security_id": f"security-{index:03d}",
            "security_code": f"{index + 1:06d}",
            "quarter": index // 24 + 1,
        })
    return {
        "schema_version": "quanthus.stage-directional-boundary-evaluation-plan.v1",
        "selection_uses_morphology": False,
        "identities": rows,
        "identity_count": 96,
        "identity_digest": digest(rows),
        "data_read": False,
    }


def quarter_start(quarter: int) -> date:
    return date(2021, (quarter - 1) * 3 + 1, 4)


def test_config_binds_frozen_candidate_before_any_evaluation_read() -> None:
    config = load_config()
    assert digest(config) == CONFIG_DIGEST
    assert config["frozen_candidate"]["candidate_id"] == (
        "sha256:d11c368d4111ebf991315a6c61b800a6737d56e5f81792cf153dabcda7cb52fd"
    )
    assert config["selection"]["selection_uses_morphology"] is False
    assert config["authority"]["parameter_search"] is False
    assert config["authority"]["candidate_replacement"] is False
    assert config["authority"]["2022_2024_access"] is False
    assert config["authority"]["2025_access"] is False


def test_evaluation_date_selection_is_hash_quarter_only_not_morphology() -> None:
    config = load_config()
    plan = evaluation_plan()
    observations = []
    expected = {}
    seed = config["selection"]["sampling_seed"]
    for item in plan["identities"]:
        start = quarter_start(item["quarter"])
        candidates = [start, start + timedelta(days=7)]
        observations.extend([
            observation(item["security_code"], candidates[0], width="0.01"),
            observation(item["security_code"], candidates[1], width="1.50"),
        ])
        expected[item["security_id"]] = min(
            candidates, key=lambda day: (_rank(seed, item["security_id"], day), day)
        )
    selected = select_evaluation_cases(plan, observations, config)
    assert selected["selected_case_count"] == 96
    assert selected["shortfall_count"] == 0
    assert selected["cross_quarter_backfill"] is False
    assert selected["identity_backfill"] is False
    assert selected["result_dependent_replacement"] is False
    for item in selected["selected"]:
        assert item["market_date"] == expected[item["security_id"]].isoformat()


def test_missing_identity_is_retained_as_shortfall_without_backfill() -> None:
    config = load_config()
    plan = evaluation_plan()
    missing = plan["identities"][17]
    observations = [
        observation(item["security_code"], quarter_start(item["quarter"]), width="0.2")
        for item in plan["identities"]
        if item["security_id"] != missing["security_id"]
    ]
    selected = select_evaluation_cases(plan, observations, config)
    assert selected["selected_case_count"] == 95
    assert selected["shortfall_count"] == 1
    assert selected["shortfalls"] == [missing]
    assert missing["security_id"] not in {item["security_id"] for item in selected["selected"]}

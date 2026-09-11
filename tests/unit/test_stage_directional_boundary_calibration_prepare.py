from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from quanthus.stage_learning.directional_boundary_calibration_prepare import (
    _evaluation_plan,
    _fresh_pool,
    select_calibration_cases,
)
from quanthus.stage_learning.directional_boundary_development import load_config


@dataclass(frozen=True)
class Observation:
    security_code: str
    market_date: date
    efficiency20_abs: Decimal
    range_width20: Decimal
    width_change20: Decimal
    center_shift20_abs: Decimal

    def feature_dict(self) -> dict[str, str]:
        return {
            "efficiency20_abs": str(self.efficiency20_abs),
            "range_width20": str(self.range_width20),
            "width_change20": str(self.width_change20),
            "center_shift20_abs": str(self.center_shift20_abs),
        }


def test_fresh_pool_excludes_old_excluded_and_entire_stage007_pool() -> None:
    members = [
        {"security_code": f"{index:06d}", "security_id": f"security-{index:04d}"}
        for index in range(700)
    ]
    excluded = {f"security-{index:04d}" for index in range(20)}
    stage007_pool = {f"security-{index:04d}" for index in range(20, 80)}
    first = _fresh_pool(
        members,
        excluded_ids=excluded,
        stage007_pool_ids=stage007_pool,
        seed="fresh-test-seed",
        size=512,
    )
    second = _fresh_pool(
        members,
        excluded_ids=excluded,
        stage007_pool_ids=stage007_pool,
        seed="fresh-test-seed",
        size=512,
    )
    assert first == second
    assert len(first) == 512
    assert not (set(first) & excluded)
    assert not (set(first) & stage007_pool)
    assert len(set(first.values())) == 512


def test_calibration_selection_hits_frozen_16_32_16_without_backfill() -> None:
    config = load_config()
    pool = {f"security-{index:03d}": f"{index:06d}" for index in range(100)}
    observations = []
    for _identity, code in pool.items():
        for month, day in ((2, 1), (5, 1), (8, 1), (11, 1)):
            observations.append(
                Observation(
                    code,
                    date(2020, month, day),
                    Decimal("0.60"),
                    Decimal("0.25"),
                    Decimal("0.00"),
                    Decimal("0.02"),
                )
            )
        observations.append(
            Observation(
                code,
                date(2020, 3, 15),
                Decimal("0.20"),
                Decimal("0.35"),
                Decimal("0.35"),
                Decimal("0.10"),
            )
        )
        observations.append(
            Observation(
                code,
                date(2020, 6, 15),
                Decimal("0.20"),
                Decimal("0.12"),
                Decimal("-0.35"),
                Decimal("0.01"),
            )
        )
    selected = select_calibration_cases(pool, observations, config)
    assert selected["counts"] == {
        "coverage": 16,
        "old_instability_seed": 32,
        "order_control": 16,
    }
    assert selected["shortfalls"] == {
        "coverage": 0,
        "old_instability_seed": 0,
        "order_control": 0,
    }
    assert len(selected["selected"]) == 64
    assert selected["one_case_per_identity"] is True
    assert len({item["security_id"] for item in selected["selected"]}) == 64


def test_evaluation_plan_is_morphology_blind_96_identities_and_24_per_quarter() -> None:
    config = load_config()
    pool = {f"security-{index:03d}": f"{index:06d}" for index in range(256)}
    plan = _evaluation_plan(pool, config)
    assert plan["identity_count"] == 96
    assert plan["selection_uses_morphology"] is False
    assert plan["data_read"] is False
    identities = plan["identities"]
    assert len({item["security_id"] for item in identities}) == 96
    assert {
        quarter: sum(item["quarter"] == quarter for item in identities)
        for quarter in range(1, 5)
    } == {1: 24, 2: 24, 3: 24, 4: 24}

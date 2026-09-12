from __future__ import annotations

from datetime import date

from quanthus.stage_learning import positive_instability_development_prepare as api
from quanthus.stage_learning.positive_instability_development import load_preregistration


def _members(count: int = 700) -> list[dict[str, str]]:
    return [
        {
            "security_code": f"{index:06d}",
            "security_id": f"security-{index:04d}",
        }
        for index in range(count)
    ]


def test_fresh_pool_excludes_prior_identity_surfaces_and_is_deterministic() -> None:
    members = _members()
    excluded = {"security-0001", "security-0002", "security-0003"}
    first = api._fresh_pool(
        members,
        excluded_ids=excluded,
        seed="test-seed",
        size=512,
    )
    second = api._fresh_pool(
        members,
        excluded_ids=excluded,
        seed="test-seed",
        size=512,
    )
    assert first == second
    assert len(first) == 512
    assert not set(first) & excluded
    assert len(set(first.values())) == 512


def test_evaluation_plan_freezes_identity_quarters_without_data_read() -> None:
    config = load_preregistration()
    pool = {f"security-{index:03d}": f"{index:06d}" for index in range(256)}
    plan = api._evaluation_plan(pool, config)
    assert plan["identity_count"] == 160
    assert plan["data_read"] is False
    assert plan["selection_uses_morphology"] is False
    assert [
        sum(item["quarter"] == quarter for item in plan["identities"])
        for quarter in range(1, 5)
    ] == [40, 40, 40, 40]
    assert len({item["security_id"] for item in plan["identities"]}) == 160


def test_development_selection_is_hash_quarter_only_and_does_not_backfill() -> None:
    config = load_preregistration()
    pool = {f"security-{index:03d}": f"{index:06d}" for index in range(256)}
    plan = config["development_selection"]
    seed = str(plan["sampling_seed"])
    ranked = sorted(
        pool,
        key=lambda identity: (api._rank(seed, "development-id", identity), identity),
    )[:128]
    feature_rows = []
    for index, identity in enumerate(ranked):
        quarter = index // 32 + 1
        # Deliberately leave one identity without an assigned-quarter observation.
        if index == 7:
            feature_rows.append(
                {
                    "security_code": pool[identity],
                    "market_date": date(2020, 12, 15),
                    "range_width20": "0.20",
                    "width_change20": "0.10",
                    "center_shift20_abs": "0.01",
                    "efficiency20_abs": "0.10",
                    "range_traversal20": "0.20",
                    "counter_direction_excursion_share20": "0.80",
                }
            )
            continue
        month = {1: 2, 2: 5, 3: 8, 4: 11}[quarter]
        feature_rows.extend(
            [
                {
                    "security_code": pool[identity],
                    "market_date": date(2020, month, 10),
                    "range_width20": "0.20",
                    "width_change20": "0.10",
                    "center_shift20_abs": "0.01",
                    "efficiency20_abs": "0.10",
                    "range_traversal20": "0.20",
                    "counter_direction_excursion_share20": "0.80",
                },
                {
                    "security_code": pool[identity],
                    "market_date": date(2020, month, 20),
                    "range_width20": "0.90",
                    "width_change20": "0.90",
                    "center_shift20_abs": "0.90",
                    "efficiency20_abs": "0.90",
                    "range_traversal20": "0.90",
                    "counter_direction_excursion_share20": "0.10",
                },
            ]
        )
    selection = api.select_development_cases(pool, feature_rows, config)
    assert selection["selection_uses_morphology"] is False
    assert selection["planned_identity_count"] == 128
    assert selection["selected_case_count"] == 127
    assert selection["shortfall_count"] == 1
    assert selection["shortfalls"] == [{"security_id": ranked[7], "quarter": 1}]
    assert selection["one_case_per_identity"] is True
    assert ranked[7] not in {item["security_id"] for item in selection["selected"]}


def test_development_selection_date_is_independent_of_feature_values() -> None:
    config = load_preregistration()
    seed = str(config["development_selection"]["sampling_seed"])
    identities = sorted(
        [f"security-{index:03d}" for index in range(256)],
        key=lambda identity: (api._rank(seed, "development-id", identity), identity),
    )
    pool = {identity: f"{index:06d}" for index, identity in enumerate(identities)}
    chosen_identity = identities[0]
    rows = [
        {
            "security_code": pool[chosen_identity],
            "market_date": date(2020, 2, 3),
            "range_width20": "0.01",
            "width_change20": "-0.90",
            "center_shift20_abs": "0.80",
            "efficiency20_abs": "0.01",
            "range_traversal20": "0.01",
            "counter_direction_excursion_share20": "1.00",
        },
        {
            "security_code": pool[chosen_identity],
            "market_date": date(2020, 3, 3),
            "range_width20": "0.99",
            "width_change20": "0.99",
            "center_shift20_abs": "0.01",
            "efficiency20_abs": "0.99",
            "range_traversal20": "0.99",
            "counter_direction_excursion_share20": "0.01",
        },
    ]
    selection = api.select_development_cases(pool, rows, config)
    selected = selection["selected"]
    assert len(selected) == 1
    expected = min(
        rows,
        key=lambda row: (
            api._rank(seed, "development-date", chosen_identity, row["market_date"]),
            row["market_date"],
        ),
    )
    assert selected[0]["market_date"] == expected["market_date"].isoformat()

from __future__ import annotations

from datetime import date
from pathlib import Path

from quanthus.stage_learning.positive_instability_quantitative_development import (
    load_preregistration,
)
from quanthus.stage_learning.positive_instability_quantitative_development_prepare import (
    _parser,
    _rank,
    select_development_cases,
)


def test_verify_cli_matches_stage_worker_contract() -> None:
    revision = "a" * 40
    args = _parser().parse_args(
        ["verify", "--artifact", "/tmp/existing-artifact", "--code-revision", revision]
    )
    assert args.command == "verify"
    assert args.artifact == Path("/tmp/existing-artifact")
    assert args.code_revision == revision


def test_development_selection_is_hash_quarter_only_and_no_backfill() -> None:
    config = load_preregistration()
    plan = config["development_selection"]
    pool = {f"security-{index:03d}": f"{index:06d}" for index in range(256)}
    seed = str(plan["sampling_seed"])
    ranked = sorted(
        pool,
        key=lambda identity: (_rank(seed, "development-id", identity), identity),
    )[:160]
    rows = []
    for index, identity in enumerate(ranked):
        quarter = index // 40 + 1
        month = (
            (12 if quarter != 4 else 1)
            if index == 9
            else {1: 2, 2: 5, 3: 8, 4: 11}[quarter]
        )
        rows.append(
            {
                "security_code": pool[identity],
                "market_date": date(2019, month, 15),
                "range_width20": "0.20",
                "width_change20": "0.10",
                "center_shift20_abs": "0.01",
                "efficiency20_abs": "0.10",
                "range_traversal20": "0.20",
                "counter_direction_excursion_share20": "0.80",
            }
        )
    selection = select_development_cases(pool, rows, config)
    assert selection["planned_identity_count"] == 160
    assert selection["selected_case_count"] == 159
    assert selection["shortfall_count"] == 1
    assert selection["shortfalls"] == [{"security_id": ranked[9], "quarter": 1}]
    assert selection["selection_uses_morphology"] is False
    assert selection["cross_quarter_backfill"] is False
    assert selection["identity_backfill"] is False


def test_date_selection_is_feature_value_blind() -> None:
    config = load_preregistration()
    plan = config["development_selection"]
    seed = str(plan["sampling_seed"])
    pool = {f"security-{index:03d}": f"{index:06d}" for index in range(256)}
    ranked = sorted(
        pool,
        key=lambda identity: (_rank(seed, "development-id", identity), identity),
    )
    identity = ranked[0]
    code = pool[identity]
    rows = [
        {
            "security_code": code,
            "market_date": date(2019, 2, 1),
            "range_width20": "0.01",
            "width_change20": "9.0",
            "center_shift20_abs": "0.9",
            "efficiency20_abs": "0.01",
            "range_traversal20": "0.01",
            "counter_direction_excursion_share20": "1.0",
        },
        {
            "security_code": code,
            "market_date": date(2019, 3, 1),
            "range_width20": "0.99",
            "width_change20": "-0.9",
            "center_shift20_abs": "0.0",
            "efficiency20_abs": "0.99",
            "range_traversal20": "0.99",
            "counter_direction_excursion_share20": "0.0",
        },
    ]
    selection = select_development_cases(pool, rows, config)
    selected = selection["selected"]
    assert len(selected) == 1
    expected = min(
        rows,
        key=lambda row: (
            _rank(seed, "development-date", identity, row["market_date"]),
            row["market_date"],
        ),
    )
    assert selected[0]["market_date"] == expected["market_date"].isoformat()

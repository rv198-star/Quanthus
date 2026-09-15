from __future__ import annotations

from quanthus.policy import find_project_root
from quanthus.stage_learning import primary_unique_contingent_fallback as subject

ROOT = find_project_root()


def test_p022_contract_keeps_primary_identity_independence_and_contingent_fallbacks() -> None:
    contract = subject._object(
        ROOT / "config/stage-learning/stage-primary-unique-contingent-fallback.v1.json",
        "contract",
    )
    primary = contract["primary_contract"]
    fallback = contract["fallback_contract"]
    gate = contract["feasibility_gate"]
    assert primary["primary_per_cell"] == 24
    assert primary["total_primary"] == 192
    assert primary["global_identity_unique"] is True
    assert primary["cross_cell_fill"] is False
    assert fallback["fallback_ceiling_per_cell"] == 8
    assert fallback["total_fallback_ceiling"] == 64
    assert fallback["fallback_pool_is_dormant_before_activation"] is True
    assert fallback["same_cell_only"] is True
    assert fallback["exclude_all_primary_identities"] is True
    assert fallback["cross_cell_fallback_identity_overlap_may_exist_before_activation"] is True
    assert (
        fallback[
            "activation_requires_global_identity_uniqueness_against_realized_primaries_and_previously_activated_fallbacks"
        ]
        is True
    )
    assert fallback["simultaneous_64_fallback_global_uniqueness_is_diagnostic_only"] is True
    assert gate["primary_global_unique_192_required"] is True
    assert gate["per_cell_dormant_non_primary_fallback_candidates_required"] == 8
    assert gate["worst_case_64_fallback_simultaneous_activation_required"] is False


def test_slot_matching_can_reassign_earlier_cell_instead_of_false_shortfall() -> None:
    candidates = {"A": ["shared", "a-only"], "B": ["shared"]}
    matched, feasible = subject._slot_matching(candidates, cell_order=("A", "B"), per_cell=1)
    assert feasible is True
    assert matched[("B", 1)] == "shared"
    assert matched[("A", 1)] == "a-only"


def _token(identity: str, cell: str, window: str) -> dict[str, object]:
    era, board = cell.split("/", 1)
    return {
        "security_id": identity,
        "security_code": identity,
        "listing_id": f"listing-{identity}",
        "era": era,
        "board_family": board,
        "board_code": board,
        "venue_id": "venue",
        "window_id": window,
        "window_start": "2020-01-01",
        "window_end": "2020-03-31",
        "exposure_level": "L1_POOL_RESERVED_NOT_READ",
    }


def test_fallback_pool_excludes_primaries_but_may_overlap_across_cells() -> None:
    cells = ("ERA/A", "ERA/B")
    by_cell = {
        "ERA/A": {
            "p-a": [_token("p-a", "ERA/A", "a-p")],
            "shared-fallback": [_token("shared-fallback", "ERA/A", "a-f")],
        },
        "ERA/B": {
            "p-b": [_token("p-b", "ERA/B", "b-p")],
            "shared-fallback": [_token("shared-fallback", "ERA/B", "b-f")],
        },
    }
    fallback_rows, feasible, receipts = subject._freeze_fallback_pools(
        by_cell,
        cell_order=cells,
        primary_identities={"p-a", "p-b"},
        per_cell=1,
        identity_seed="fallback",
        window_seed="window",
    )
    assert feasible is True
    assert len(fallback_rows) == 2
    assert {row["security_id"] for row in fallback_rows} == {"shared-fallback"}
    assert all(row["security_id"] not in {"p-a", "p-b"} for row in fallback_rows)
    assert all(receipt["sufficient"] is True for receipt in receipts)
    worst_case = subject._max_simultaneous_fallback_activation(
        fallback_rows,
        cell_order=cells,
        per_cell=1,
    )
    assert worst_case["all_64_simultaneously_feasible"] is False
    assert worst_case["max_simultaneously_activatable_count"] == 1

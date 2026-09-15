from __future__ import annotations

from quanthus.policy import find_project_root
from quanthus.stage_learning import admission_exposure_capacity as subject

ROOT = find_project_root()


def test_p021_capacity_config_is_metadata_only_and_keeps_quota() -> None:
    config = subject.load_config(ROOT / subject.DEFAULT_CONFIG)
    assert config["work_item"] == "P3-STAGE-021-B"
    assert config["state"] == "PREREGISTERED_METADATA_ONLY_CAPACITY_AFTER_TAXONOMY_FREEZE"
    comparison = config["comparison"]
    assert comparison["admission_only_reusable_class"] == "L2A_TECHNICAL_INPUT_ADMISSION_READ"
    assert comparison["semantic_consumed_class"] == "L2B_SEMANTIC_DEVELOPMENT_READ"
    capacity = config["capacity_contract"]
    assert capacity["required_unique_identities_per_cell"] == 32
    assert capacity["global_identity_unique_reserve_rows"] == 256
    authority = config["authority"]
    assert authority["listing_calendar_metadata_read"] is True
    assert authority["prior_artifact_metadata_read"] is True
    for key in (
        "market_bar_read",
        "admission_market_read",
        "stage1_12_semantic_read",
        "threshold_or_candidate_read",
        "reference_opinion_read",
        "p020_reopen",
        "preserved_2021_read",
        "2022_2024_read",
        "2025_read",
        "daily_or_strategy",
        "trading",
    ):
        assert authority[key] is False


def test_cell_capacity_restores_only_admission_only_exact_windows() -> None:
    cells = ("ERA/STAR", "ERA/MAIN")
    tokens = [
        {
            "security_id": "A",
            "window_id": "W1",
            "era": "ERA",
            "board_family": "STAR",
        },
        {
            "security_id": "A",
            "window_id": "W2",
            "era": "ERA",
            "board_family": "STAR",
        },
        {
            "security_id": "B",
            "window_id": "W3",
            "era": "ERA",
            "board_family": "STAR",
        },
        {
            "security_id": "C",
            "window_id": "W4",
            "era": "ERA",
            "board_family": "MAIN",
        },
    ]
    p013 = {("A", "W1")}
    admission = {("A", "W2"), ("B", "W3")}
    semantic = {("B", "W3")}
    old_capacity = subject._cell_capacity(
        tokens,
        consumed=p013 | admission,
        cell_order=cells,
    )
    repaired_capacity = subject._cell_capacity(
        tokens,
        consumed=p013 | semantic,
        cell_order=cells,
    )
    assert old_capacity["ERA/STAR"] == {"identity_count": 0, "window_count": 0}
    assert repaired_capacity["ERA/STAR"] == {"identity_count": 1, "window_count": 1}
    assert repaired_capacity["ERA/MAIN"] == {"identity_count": 1, "window_count": 1}


def test_global_unique_feasibility_obeys_fixed_cell_order() -> None:
    cells = ("ERA/STAR", "ERA/MAIN")
    tokens = []
    for identity in ("A", "B", "C"):
        tokens.append(
            {
                "security_id": identity,
                "window_id": f"S-{identity}",
                "era": "ERA",
                "board_family": "STAR",
            }
        )
    for identity in ("A", "D", "E"):
        tokens.append(
            {
                "security_id": identity,
                "window_id": f"M-{identity}",
                "era": "ERA",
                "board_family": "MAIN",
            }
        )
    result = subject._global_unique_feasibility(
        tokens,
        consumed=set(),
        cell_order=cells,
        required=2,
        seed="test-seed",
    )
    assert result["feasible"] is True
    assert result["selected_row_count"] == 4
    assert result["global_unique_identity_count"] == 4
    assert all(row["sufficient"] is True for row in result["cell_receipts"])


def test_global_unique_feasibility_fails_without_quota_relaxation() -> None:
    cells = ("ERA/STAR", "ERA/MAIN")
    tokens = [
        {
            "security_id": "A",
            "window_id": "STAR-A",
            "era": "ERA",
            "board_family": "STAR",
        },
        {
            "security_id": "B",
            "window_id": "STAR-B",
            "era": "ERA",
            "board_family": "STAR",
        },
        {
            "security_id": "A",
            "window_id": "MAIN-A",
            "era": "ERA",
            "board_family": "MAIN",
        },
    ]
    result = subject._global_unique_feasibility(
        tokens,
        consumed=set(),
        cell_order=cells,
        required=2,
        seed="test-seed",
    )
    assert result["feasible"] is False
    assert result["selected_row_count"] == 2
    assert result["cell_receipts"][1]["selected_count"] == 0
    assert result["cell_receipts"][1]["sufficient"] is False

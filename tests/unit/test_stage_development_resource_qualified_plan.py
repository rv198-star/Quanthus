from __future__ import annotations

import json

import pytest

from quanthus.policy import find_project_root
from quanthus.stage_learning.development_resource_qualified_plan import (
    DevelopmentResourceQualifiedPlanError,
    load_config,
    select_qualified_plan,
)

ROOT = find_project_root()
CONFIG = ROOT / "config/stage-learning/stage-development-resource-qualified-plan.v1.json"


def _ledger(config: dict, per_cell: int = 40) -> list[dict]:
    rows: list[dict] = []
    for cell_rank, cell in enumerate(config["selection"]["cell_order"], start=1):
        era, family = cell.split("/", 1)
        for identity_rank in range(1, per_cell + 1):
            identity = f"sec-{cell_rank:02d}-{identity_rank:03d}"
            rows.append(
                {
                    "security_id": identity,
                    "security_code": f"{cell_rank:02d}{identity_rank:04d}"[-6:],
                    "era": era,
                    "board_family": family,
                    "cell": cell,
                    "window_id": f"{cell}|window-{identity_rank:03d}",
                    "window_start": "2018-01-01",
                    "window_end": "2018-03-31",
                    "identity_rank": identity_rank,
                    "window_rank": 1,
                    "qualified": True,
                    "reason": "61_SESSION_CONTIGUOUS_INPUT_READY",
                    "source_binding_digest": (
                        "sha256:"
                        + f"{cell_rank:02x}{identity_rank:02x}".ljust(64, "0")
                    ),
                }
            )
    return rows


def test_config_freezes_ledger_only_plan_authority() -> None:
    value = load_config(CONFIG)
    assert value["runtime"] == {
        "maximum_jobs": 1,
        "automatic_retries": 0,
        "worker_wall_seconds": 600,
    }
    assert value["selection"]["reserve_per_cell"] == 32
    assert value["selection"]["primary_per_cell"] == 24
    assert value["selection"]["fallback_per_cell"] == 8
    assert value["selection"]["global_identity_unique"] is True
    assert value["authority"]["admission_ledger_read"] is True
    for key in (
        "market_price_read",
        "structure_fact_build",
        "threshold_basis",
        "reference_or_label_read",
        "candidate_output",
        "candidate_scoring",
        "preserved_2021_market_read",
        "2022_2024_read",
        "2025_read",
        "semantic_job_authorized",
    ):
        assert value["authority"][key] is False


def test_select_qualified_plan_freezes_8x32_and_24_8_roles() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    rows = select_qualified_plan(_ledger(config), config=config)
    assert len(rows) == 256
    assert len({row["security_id"] for row in rows}) == 256
    cells = {}
    for row in rows:
        cells.setdefault(row["cell"], []).append(row)
    assert set(cells) == set(config["selection"]["cell_order"])
    for selected in cells.values():
        assert len(selected) == 32
        assert sum(row["role"] == "primary" for row in selected) == 24
        assert sum(row["role"] == "fallback" for row in selected) == 8
        assert [row["plan_slot"] for row in selected] == list(range(1, 33))


def test_global_identity_overlap_is_skipped_deterministically() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    ledger = _ledger(config)
    first, second = config["selection"]["cell_order"][:2]
    first_ids = [row["security_id"] for row in ledger if row["cell"] == first][:8]
    second_rows = [row for row in ledger if row["cell"] == second]
    for index, shared in enumerate(first_ids):
        second_rows[index]["security_id"] = shared
    selected = select_qualified_plan(ledger, config=config)
    assert len({row["security_id"] for row in selected}) == 256
    second_selected = [row for row in selected if row["cell"] == second]
    assert not set(first_ids) & {row["security_id"] for row in second_selected}


def test_global_identity_shortfall_fails_before_semantic_plan() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    ledger = _ledger(config, per_cell=31)
    with pytest.raises(
        DevelopmentResourceQualifiedPlanError,
        match="global_unique_qualified_shortfall",
    ):
        select_qualified_plan(ledger, config=config)

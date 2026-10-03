import json
from pathlib import Path

from scripts.sync_github_planning import (
    MILESTONES,
    P3_STRAT_003_CHILD_IDS,
    P3_STRAT_003_DESIGN_PATH,
    P3_STRAT_003_IDS,
    P3_STRAT_003_IMPLEMENTATION_SPECS,
    P3_STRAT_003_MILESTONE,
    p3_strat_003_epic_body,
    task_body,
    work_items,
)

ROOT = Path(__file__).resolve().parents[2]


def test_p3_strat_003_is_one_epic_with_dependency_gated_children() -> None:
    items = {item.stable_id: item for item in work_items(ROOT)}

    assert P3_STRAT_003_MILESTONE in MILESTONES
    assert {
        "P3-STRAT-003",
        "P3-STRAT-003-A",
        "P3-STRAT-003-B",
        "P3-STRAT-003-B-R1",
        "P3-STRAT-003-C",
        "P3-STRAT-003-C-R1",
        "P3-STRAT-003-D",
        "P3-STRAT-003-E",
    } == P3_STRAT_003_IDS
    assert P3_STRAT_003_CHILD_IDS == (
        "P3-STRAT-003-A",
        "P3-STRAT-003-B",
        "P3-STRAT-003-B-R1",
        "P3-STRAT-003-C",
        "P3-STRAT-003-C-R1",
        "P3-STRAT-003-D",
        "P3-STRAT-003-E",
    )
    assert all(items[item_id].milestone == P3_STRAT_003_MILESTONE for item_id in P3_STRAT_003_IDS)
    assert items["P3-STRAT-003"].issue_type == "epic"
    assert items["P3-STRAT-003"].status == "blocked"
    assert items["P3-STRAT-003-A"].status == "closed"
    assert items["P3-STRAT-003-B"].status == "closed"
    assert items["P3-STRAT-003-B-R1"].status == "ready"
    assert "P3-STRAT-003-B" in items["P3-STRAT-003-B-R1"].dependencies
    assert items["P3-STRAT-003-C"].status == "closed"
    assert items["P3-STRAT-003-C-R1"].status == "closed"
    assert items["P3-STRAT-003-D"].status == "closed"
    assert items["P3-STRAT-003-E"].status == "closed"
    closeout = json.loads((ROOT / "config/research/p3-p5-closeout-20261002.v1.json").read_text())
    assert closeout["p3"]["B_accepted"] is False
    assert closeout["p3"]["input_manifest"] is None
    assert closeout["p3"]["candidate_manifest"] is None
    assert closeout["p3"]["not_run"]["D_all_four_variants"] == "NOT_RUN_INPUT_UNQUALIFIED"
    assert closeout["p3"]["strategy_result_rows_read"] == 0
    assert "P3-STRAT-003-A" in items["P3-STRAT-003-B"].dependencies
    assert "P3-STRAT-003-A" in items["P3-STRAT-003-C"].dependencies
    assert "P3-STRAT-003-C" in items["P3-STRAT-003-C-R1"].dependencies
    assert "P3-STRAT-003-B" in items["P3-STRAT-003-D"].dependencies
    assert "P3-STRAT-003-C-R1" in items["P3-STRAT-003-D"].dependencies
    assert "P3-STRAT-003-D" in items["P3-STRAT-003-E"].dependencies
    assert "P6" not in items["P3-STRAT-003"].dependencies


def test_p3_strat_003_specs_use_one_governing_work_item_design() -> None:
    items = {item.stable_id: item for item in work_items(ROOT)}

    assert set(P3_STRAT_003_IMPLEMENTATION_SPECS) == P3_STRAT_003_IDS
    assert (ROOT / P3_STRAT_003_DESIGN_PATH).is_file()
    for item_id, spec in P3_STRAT_003_IMPLEMENTATION_SPECS.items():
        assert items[item_id].design_path == spec.design_path == P3_STRAT_003_DESIGN_PATH


def test_p3_strat_003_generated_bodies_project_boundaries() -> None:
    items = {item.stable_id: item for item in work_items(ROOT)}

    for item_id in P3_STRAT_003_IDS:
        body = task_body(items[item_id], "rv198-star/Quanthus")
        spec = P3_STRAT_003_IMPLEMENTATION_SPECS[item_id]
        assert "## Implementation contract" in body
        assert spec.approach in body
        assert all(f"`{path}`" in body for path in spec.owned_paths)
        assert all(gate in body for gate in spec.decision_gates)
        assert all(check in body for check in spec.verification)

    assert "P6-v1 is parallel evidence and is not a predecessor or mutable input." in task_body(
        items["P3-STRAT-003"], "rv198-star/Quanthus"
    )


def test_p3_strat_003_epic_body_links_every_child() -> None:
    items = {item.stable_id: item for item in work_items(ROOT)}
    urls = {
        item_id: f"https://github.test/issues/{index}"
        for index, item_id in enumerate(P3_STRAT_003_CHILD_IDS, start=318)
    }

    body = p3_strat_003_epic_body(items["P3-STRAT-003"], "rv198-star/Quanthus", urls)

    assert "kind=epic" in body
    assert "## Blocking work items" in body
    assert all(f"[{item_id}]({urls[item_id]})" in body for item_id in P3_STRAT_003_CHILD_IDS)

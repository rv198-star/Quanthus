from pathlib import Path

from scripts.sync_github_planning import (
    EXPERT_STRATEGY_CHILD_IDS,
    EXPERT_STRATEGY_DESIGN_IDS,
    EXPERT_STRATEGY_DESIGN_PATH,
    EXPERT_STRATEGY_IDS,
    EXPERT_STRATEGY_MILESTONE,
    MILESTONES,
    expert_strategy_epic_body,
    task_body,
    work_items,
)

ROOT = Path(__file__).resolve().parents[2]


def test_expert_strategy_mvp_is_one_epic_with_seven_dependency_gated_children() -> None:
    items = {item.stable_id: item for item in work_items(ROOT)}

    assert EXPERT_STRATEGY_MILESTONE in MILESTONES
    assert {
        "P3-EXPERT-000",
        "P3-EXPERT-001",
        "P3-EXPERT-002",
        "P3-EXPERT-003",
        "P3-STRAT-002",
        "P3-ATTR-001",
        "P3-EXPERT-004",
        "P3-EXPERT-005",
    } == EXPERT_STRATEGY_IDS
    assert len(EXPERT_STRATEGY_IDS - {"P3-EXPERT-000"}) == 7
    assert all(
        items[item_id].milestone == EXPERT_STRATEGY_MILESTONE for item_id in EXPERT_STRATEGY_IDS
    )
    assert items["P3-EXPERT-000"].issue_type == "epic"
    assert items["P3-EXPERT-001"].status == "ready"
    assert items["P3-EXPERT-000"].status == "ready"
    assert all(
        items[item_id].status == "blocked"
        for item_id in EXPERT_STRATEGY_IDS - {"P3-EXPERT-000", "P3-EXPERT-001"}
    )
    assert items["P3-DASH-001"].milestone != EXPERT_STRATEGY_MILESTONE


def test_expert_strategy_issues_link_the_approved_child_design() -> None:
    items = {item.stable_id: item for item in work_items(ROOT)}

    assert all(
        items[item_id].design_path == EXPERT_STRATEGY_DESIGN_PATH
        for item_id in EXPERT_STRATEGY_DESIGN_IDS
    )
    assert items["P3-ATTR-001"].design_path == ("docs/design/lld/10-monitoring-attribution.md")

    design = (ROOT / EXPERT_STRATEGY_DESIGN_PATH).read_text(encoding="utf-8")
    assert "| Design status | approved |" in design
    assert "Existing P3 expert/action protocol v1 and accepted Run digests remain frozen" in design
    assert (
        "Every expert version must have a quantitative, falsifiable evaluation contract" in design
    )
    assert "This is not expert voting" in design
    assert "There is no cross-target expert leaderboard or universal composite score" in design
    assert "Development is the only historical partition admitted by the MVP" in design
    assert all(f"`{item_id}`" in design for item_id in EXPERT_STRATEGY_IDS)


def test_expert_strategy_epic_body_links_every_blocking_child() -> None:
    items = {item.stable_id: item for item in work_items(ROOT)}
    urls = {
        item_id: f"https://github.test/issues/{index}"
        for index, item_id in enumerate(EXPERT_STRATEGY_CHILD_IDS, start=1)
    }

    body = expert_strategy_epic_body(items["P3-EXPERT-000"], "rv198-star/Quanthus", urls)

    assert "kind=epic" in body
    assert "## Blocking work items" in body
    assert all(f"[{item_id}]({urls[item_id]})" in body for item_id in EXPERT_STRATEGY_CHILD_IDS)


def test_planning_body_keeps_existing_section_spacing() -> None:
    item = next(item for item in work_items(ROOT) if item.stable_id == "P3-EXP-001")

    body = task_body(item, "rv198-star/Quanthus")

    assert "docs/design/hld.md)\n\n## Phase and gate" in body
    assert "docs/design/hld.md)\n\n\n## Phase and gate" not in body

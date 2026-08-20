from pathlib import Path

from scripts.sync_github_planning import (
    EXPERT_STRATEGY_CHILD_IDS,
    EXPERT_STRATEGY_DESIGN_PATH,
    EXPERT_STRATEGY_IDS,
    EXPERT_STRATEGY_IMPLEMENTATION_SPECS,
    EXPERT_STRATEGY_MILESTONE,
    EXPERT_STRATEGY_REVIEW_PATH,
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
    assert items["P3-EXPERT-001"].status == "closed"
    assert items["P3-EXPERT-002"].status == "closed"
    assert items["P3-EXPERT-000"].status == "ready"
    assert all(
        items[item_id].status == "blocked"
        for item_id in EXPERT_STRATEGY_IDS
        - {"P3-EXPERT-000", "P3-EXPERT-001", "P3-EXPERT-002"}
    )
    assert items["P3-DASH-001"].milestone != EXPERT_STRATEGY_MILESTONE
    assert "P4-LABEL-001" in items["P3-EXPERT-003"].dependencies
    assert "P3-EXPERT-002" in items["P3-EXPERT-003"].dependencies
    assert items["P3-STRAT-002"].title == (
        "Freeze and implement the first formal two-expert strategy topology"
    )
    assert items["P3-EXPERT-005"].title == (
        "Accept the development MVP and resolve forward-Shadow disposition"
    )


def test_expert_strategy_issues_link_their_implementation_designs() -> None:
    items = {item.stable_id: item for item in work_items(ROOT)}

    assert set(EXPERT_STRATEGY_IMPLEMENTATION_SPECS) == EXPERT_STRATEGY_IDS
    for item_id, specification in EXPERT_STRATEGY_IMPLEMENTATION_SPECS.items():
        assert items[item_id].design_path == specification.design_path
        assert (ROOT / specification.design_path).is_file()

    design = (ROOT / EXPERT_STRATEGY_DESIGN_PATH).read_text(encoding="utf-8")
    assert "| Design status | approved |" in design
    assert "Existing P3 expert/action protocol v1 and accepted Run digests remain frozen" in design
    assert (
        "Every expert version must have a quantitative, falsifiable evaluation contract" in design
    )
    assert "This is not expert voting" in design
    assert "There is no cross-target expert leaderboard or universal composite score" in design
    assert "Development is the only historical partition admitted by the MVP" in design
    assert "exactly two active complementary strategy" in design
    assert "P4-LABEL-001" in design
    assert "explicit forward-Shadow" in design
    assert all(f"`{item_id}`" in design for item_id in EXPERT_STRATEGY_IDS)


def test_each_expert_strategy_child_design_is_construction_ready() -> None:
    required_sections = (
        "Outcome and boundary",
        "Inputs and owned paths",
        "Implementation route",
        "Failure semantics",
        "Verification and acceptance",
        "Rollback",
    )

    for item_id in EXPERT_STRATEGY_CHILD_IDS:
        path = ROOT / EXPERT_STRATEGY_IMPLEMENTATION_SPECS[item_id].design_path
        content = path.read_text(encoding="utf-8")
        assert f"| Work item | `{item_id}` |" in content
        assert "| Design status | approved |" in content
        for section_name in required_sections:
            assert section_name in content, f"{path} is missing {section_name}"

    review = (ROOT / EXPERT_STRATEGY_REVIEW_PATH).read_text(encoding="utf-8")
    assert "| Verdict | passed after the design corrections recorded below |" in review
    assert "## 2. Product and personal-platform review" in review
    assert "## 3. Engineering and runtime review" in review
    assert "## 4. Statistical and governance review" in review
    assert "canonical non-empty spec set" in review
    assert "negative lifecycle outcomes terminally executable" in review


def test_issue_designs_close_cross_issue_runtime_and_evidence_boundaries() -> None:
    worker = (
        ROOT
        / EXPERT_STRATEGY_IMPLEMENTATION_SPECS["P3-EXPERT-002"].design_path
    ).read_text(encoding="utf-8")
    target = (
        ROOT
        / EXPERT_STRATEGY_IMPLEMENTATION_SPECS["P3-EXPERT-003"].design_path
    ).read_text(encoding="utf-8")
    strategy = (
        ROOT
        / EXPERT_STRATEGY_IMPLEMENTATION_SPECS["P3-STRAT-002"].design_path
    ).read_text(encoding="utf-8")
    attribution = (
        ROOT
        / EXPERT_STRATEGY_IMPLEMENTATION_SPECS["P3-ATTR-001"].design_path
    ).read_text(encoding="utf-8")
    product = (
        ROOT
        / EXPERT_STRATEGY_IMPLEMENTATION_SPECS["P3-EXPERT-004"].design_path
    ).read_text(encoding="utf-8")
    lifecycle = (
        ROOT
        / EXPERT_STRATEGY_IMPLEMENTATION_SPECS["P3-EXPERT-005"].design_path
    ).read_text(encoding="utf-8")
    protocol = (
        ROOT
        / EXPERT_STRATEGY_IMPLEMENTATION_SPECS["P3-EXPERT-001"].design_path
    ).read_text(encoding="utf-8")

    assert "All seven artifacts" in protocol
    assert "`ExpertLifecycleDecision`" in protocol
    assert "`evaluation_specs`: one or more" in worker
    assert "Operational attempt metadata remains" in worker
    assert "no result-bearing DWD read or Issue acceptance may bypass it" in target
    assert "exact product-catalogue candidate id/version" in strategy
    assert "sealed input owned by `P3-STRAT-002`" in attribution
    assert "initial package with\n`lifecycle_decision = null`" in product
    assert "`keep` requires `enabled`" in lifecycle
    assert "For `disabled`, prove" in lifecycle


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


def test_expert_strategy_task_bodies_project_execution_boundaries() -> None:
    items = {item.stable_id: item for item in work_items(ROOT)}

    for item_id in EXPERT_STRATEGY_IDS:
        body = task_body(items[item_id], "rv198-star/Quanthus")
        specification = EXPERT_STRATEGY_IMPLEMENTATION_SPECS[item_id]
        assert "## Implementation contract" in body
        assert specification.approach in body
        assert "### Owned paths" in body
        assert all(f"`{path}`" in body for path in specification.owned_paths)
        assert "## Decision gates" in body
        assert all(gate in body for gate in specification.decision_gates)
        assert "## Verification" in body
        assert all(check in body for check in specification.verification)
        assert "\n\n\n## Acceptance evidence" not in body


def test_planning_body_keeps_existing_section_spacing() -> None:
    item = next(item for item in work_items(ROOT) if item.stable_id == "P3-EXP-001")

    body = task_body(item, "rv198-star/Quanthus")

    assert "docs/design/hld.md)\n\n## Phase and gate" in body
    assert "docs/design/hld.md)\n\n\n## Phase and gate" not in body

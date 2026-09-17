from pathlib import Path

from scripts.sync_github_planning import (
    CHAPTERS,
    IMPLEMENTATION_SPECS,
    P6_CHILD_IDS,
    P6_DESIGN_PATH,
    P6_IDS,
    P6_IMPLEMENTATION_SPECS,
    p6_epic_body,
    task_body,
    work_items,
)

ROOT = Path(__file__).resolve().parents[2]


def test_p6_is_one_execution_epic_with_eight_dependency_gated_children() -> None:
    items = {item.stable_id: item for item in work_items(ROOT)}

    assert P6_CHILD_IDS == (
        "P6-GOV-001",
        "P6-DATA-001",
        "P6-SCORE-001",
        "P6-GOV-002",
        "P6-PAPER-001",
        "P6-WEB-001",
        "P6-ACTIVATE-001",
        "P6-REVIEW-001",
    )
    assert {"P6-000", *P6_CHILD_IDS} == P6_IDS
    assert items["P6-000"].issue_type == "epic"
    assert items["P6-000"].status == "ready"
    assert items["P6-GOV-001"].status == "closed"
    assert items["P6-DATA-001"].status == "closed"
    assert items["P6-SCORE-001"].status == "closed"
    assert items["P6-GOV-002"].status == "closed"
    assert items["P6-PAPER-001"].status == "closed"
    assert items["P6-WEB-001"].status == "closed"
    assert items["P6-ACTIVATE-001"].status == "ready"
    assert all(items[item_id].status == "blocked" for item_id in P6_CHILD_IDS[7:])
    assert all(items[item_id].phase == "P6" for item_id in P6_IDS)
    assert next(chapter for chapter in CHAPTERS if chapter.number == "08").status == "ready"


def test_p6_reuses_old_stable_ids_and_encodes_the_exact_dependency_chain() -> None:
    items = {item.stable_id: item for item in work_items(ROOT)}

    assert {"P6-GOV-001", "P6-GOV-002", "P6-SCORE-001", "P6-DATA-001", "P6-REVIEW-001"} <= P6_IDS
    expected_predecessors = {
        "P6-GOV-001": "P6-000 entry authority",
        "P6-DATA-001": "P6-GOV-001 plus explicit post-entry owner value review",
        "P6-SCORE-001": "P6-DATA-001",
        "P6-GOV-002": "P6-SCORE-001",
        "P6-PAPER-001": "P6-GOV-002",
        "P6-WEB-001": "P6-PAPER-001",
        "P6-ACTIVATE-001": "P6-WEB-001",
        "P6-REVIEW-001": "P6-ACTIVATE-001",
    }
    for item_id, predecessor in expected_predecessors.items():
        assert predecessor in items[item_id].dependencies


def test_p6_design_and_issue_contracts_freeze_authority_and_runtime_boundaries() -> None:
    design = (ROOT / P6_DESIGN_PATH).read_text(encoding="utf-8")

    assert set(P6_IMPLEMENTATION_SPECS) == P6_IDS
    assert set(P6_IMPLEMENTATION_SPECS) <= set(IMPLEMENTATION_SPECS)
    assert all((ROOT / spec.design_path).is_file() for spec in P6_IMPLEMENTATION_SPECS.values())
    assert "`quanthus.p6-forward-cycle.v1`" in design
    assert "`queue=replay`" in design
    assert "evidence becomes prospective only after" in design
    assert "Forward Shadow and paper operation are sibling evidence lanes" in design
    assert "P5 deferred; not a P6 predecessor" in design
    assert "zero notification/broker/live-capital authority" in design
    assert "prospective mechanism MVP" in design
    assert "non-overlapping 22-trading-session market blocks" in design
    assert "`P6-DATA-001`" in design
    assert "remains blocked until an explicit post-entry owner review" in design
    assert all(
        f"P6-{suffix}" in design
        for suffix in (
            "GOV-001",
            "DATA-001",
            "SCORE-001",
            "GOV-002",
            "PAPER-001",
            "WEB-001",
            "ACTIVATE-001",
            "REVIEW-001",
        )
    )


def test_p6_issue_bodies_are_construction_ready_and_epic_links_all_children() -> None:
    items = {item.stable_id: item for item in work_items(ROOT)}
    urls = {
        item_id: f"https://github.test/issues/{index}"
        for index, item_id in enumerate(P6_CHILD_IDS, start=1)
    }

    epic = p6_epic_body(items["P6-000"], "rv198-star/Quanthus", urls)
    assert "## Blocking work items" in epic
    assert all(f"[{item_id}]({urls[item_id]})" in epic for item_id in P6_CHILD_IDS)

    for item_id in P6_IDS:
        specification = P6_IMPLEMENTATION_SPECS[item_id]
        body = task_body(items[item_id], "rv198-star/Quanthus")
        assert P6_DESIGN_PATH in body
        assert "## Implementation contract" in body
        assert specification.approach in body
        assert all(gate in body for gate in specification.decision_gates)
        assert all(check in body for check in specification.verification)
        assert "does not depend on P5" in body
        assert "broker connection" in body
        assert "live capital" in body


def test_p5_is_deferred_without_becoming_a_p6_predecessor() -> None:
    items = work_items(ROOT)
    p5_items = [item for item in items if item.phase == "P5"]
    p6_items = [item for item in items if item.phase == "P6"]

    assert p5_items and all(item.status == "blocked" for item in p5_items)
    assert all("P5-" not in item.dependencies for item in p6_items)


def test_multi_expert_successor_review_selects_only_the_p6_entry_gate() -> None:
    review_path = ROOT / "docs/reviews/quanthus-multi-expert-successor-route-review-20260917.md"
    review = review_path.read_text(encoding="utf-8")
    assert "SELECT_P6_GOV001_AS_NEXT_PHASE_NO_AUTOMATIC_DOWNSTREAM" in review
    assert "Immediate phase target | `P6-GOV-001` only" in review
    assert "stop and reassess value before opening `P6-DATA-001`" in review
    assert "single-assistant four-perspective simulation" in review
    assert "Automatic continuation | forbidden" in review

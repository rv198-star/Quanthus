from __future__ import annotations

import json
import subprocess
import sys

from quanthus.policy import find_project_root

ROOT = find_project_root()


def test_bounded_delivery_issue_graph_and_evidence():
    plan = json.loads((ROOT / "config/structure-v2-delivery.v1.json").read_text())
    rows = {row["id"]: row for row in plan["items"]}
    parent = "P3-STRUCT-004"
    assert len(rows) == 7 and rows[parent]["kind"] == "epic"
    assert all("P3-DICT-001" not in row["depends_on"] for row in rows.values())
    for row in rows.values():
        assert row["scope"] and row["acceptance"]
        if row["status"] == "closed":
            assert row["evidence"]
        assert all((ROOT / path).is_file() for path in row["evidence"])

    def visit(key, ancestors):
        assert key not in ancestors
        for dependency in rows[key]["depends_on"]:
            if dependency in rows:
                visit(dependency, ancestors | {key})
    for key in rows:
        visit(key, set())
    if rows[parent]["status"] == "closed":
        assert plan["runtime_accepted"] is True
        assert all(row["status"] == "closed" for row in rows.values())


def test_issue_projection_defaults_to_offline_preview():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/sync_structure_v2_issues.py")],
        capture_output=True, text=True, check=True,
    )
    output = json.loads(result.stdout)
    assert output["apply"] is False and len(output["items"]) == 7

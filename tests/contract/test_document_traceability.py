import json
import re
import unittest
from pathlib import Path

from quanthus.gates import load_gate_report
from quanthus.policy import find_project_root
from scripts.sync_github_planning import CHAPTERS

WORK_ITEM_ID_PATTERN = r"(?:META|P[1-7])-[A-Z0-9-]+-[0-9]{3}|[CS][0-9]+"
WORK_ITEM_ROW = re.compile(rf"^\|\s*({WORK_ITEM_ID_PATTERN})\s*\|")
WORK_ITEM_TOKEN = re.compile(rf"(?<![A-Z0-9-])({WORK_ITEM_ID_PATTERN})(?![A-Z0-9-])")


def _table_ids(path: Path) -> list[str]:
    ids: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = WORK_ITEM_ROW.match(line)
        if match:
            ids.append(match.group(1))
    return ids


def _cells(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    return [cell.strip() for cell in stripped[1:-1].split("|")]


def _header_fields(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if _cells(line) != ["Field", "Value"]:
            continue
        fields: dict[str, str] = {}
        for row in lines[index + 2 :]:
            cells = _cells(row)
            if cells is None or len(cells) != 2:
                break
            fields[cells[0]] = cells[1]
        return fields
    raise AssertionError(f"document has no Field/Value header: {path}")


def _indexed_dependencies(path: Path) -> set[str]:
    dependencies: set[str] = set()
    header: list[str] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = _cells(line)
        if cells is None:
            header = None
            continue
        if cells and cells[0] == "ID":
            header = cells
            continue
        if header is None or "Depends on" not in header or len(cells) != len(header):
            continue
        dependency_cell = cells[header.index("Depends on")]
        dependencies.update(WORK_ITEM_TOKEN.findall(dependency_cell))
    return dependencies


class DocumentTraceabilityTests(unittest.TestCase):
    def test_lld_work_items_are_unique_and_indexed(self) -> None:
        root = find_project_root()
        index_ids = _table_ids(root / "docs" / "work-items" / "index.md")
        self.assertEqual(len(index_ids), len(set(index_ids)), "work-item index has duplicate ids")

        lld_ids: list[str] = []
        for path in sorted((root / "docs" / "design" / "lld").rglob("*.md")):
            lld_ids.extend(_table_ids(path))

        missing = sorted(set(lld_ids).difference(index_ids))
        self.assertEqual(missing, [], f"LLD work items missing from index: {missing}")

    def test_indexed_work_item_dependencies_exist(self) -> None:
        root = find_project_root()
        index_path = root / "docs" / "work-items" / "index.md"
        index_ids = set(_table_ids(index_path))

        missing = sorted(_indexed_dependencies(index_path).difference(index_ids))

        self.assertEqual(missing, [], f"work-item dependencies missing from index: {missing}")

    def test_progressive_navigation_nodes_exist(self) -> None:
        root = find_project_root()
        required = (
            "docs/AGENTS.md",
            "docs/README.md",
            "docs/architecture/README.md",
            "docs/design/hld.md",
            "docs/design/lld/README.md",
            "docs/design/lld/01-data-foundation.md",
            "docs/design/lld/02-backtest-engine.md",
            "docs/design/lld/10-monitoring-attribution.md",
            "docs/design/source-precedence.md",
            "docs/work-items/index.md",
        )

        missing = [path for path in required if not (root / path).is_file()]

        self.assertEqual(missing, [], f"required documentation navigation nodes missing: {missing}")

    def test_current_design_delivery_fields_match_release_gate(self) -> None:
        root = find_project_root()
        gate = load_gate_report()
        gate_raw = json.loads(gate.path.read_text(encoding="utf-8"))
        docs_map = _header_fields(root / "docs" / "README.md")
        prd = _header_fields(root / "docs" / "product" / "prd.md")
        hld = _header_fields(root / "docs" / "design" / "hld.md")
        p1 = _header_fields(root / "docs" / "design" / "lld" / "01-data-foundation.md")
        p2 = _header_fields(root / "docs" / "design" / "lld" / "02-backtest-engine.md")

        self.assertEqual(prd["Design status"], "approved")
        self.assertEqual(hld["Design status"], "approved")
        expected_phase = "P2 implementation open" if gate.p2_can_start else "P2 blocked"
        self.assertEqual(docs_map["Current phase"], expected_phase)
        self.assertEqual(prd["Current phase"], expected_phase)
        self.assertEqual(hld["Current phase"], expected_phase)
        if not gate.p1_acceptance_passed:
            expected_p1 = "blocked"
        elif gate_raw.get("conditional_acceptance") is not None:
            expected_p1 = "accepted-conditional"
        else:
            expected_p1 = "accepted"
        self.assertEqual(
            p1["Delivery gate"],
            expected_p1,
        )
        self.assertEqual(p2["Delivery gate"], "open" if gate.p2_can_start else "blocked")
        for fields in (docs_map, prd, hld, p1, p2):
            gate_path = fields.get("Governing gate") or fields.get("Gate source")
            if gate_path is not None:
                self.assertTrue((root / gate_path.strip("`")).is_file(), gate_path)

    def test_stock_stage_draft_cannot_claim_delivery_authority(self) -> None:
        root = find_project_root()
        fields = _header_fields(root / "docs" / "design" / "stock-stage-state-machine.md")

        self.assertEqual(fields["Design status"], "draft")
        self.assertEqual(fields["Delivery gate"], "blocked")
        self.assertIn("Non-governing", fields["Authority"])

    def test_planning_epic_status_matches_document_delivery_gate(self) -> None:
        root = find_project_root()
        status_by_gate = {
            "accepted": "ready",
            "accepted-conditional": "ready",
            "open": "ready",
            "blocked": "blocked",
        }

        for chapter in CHAPTERS:
            if chapter.phase not in {"P1", "P2"}:
                continue
            fields = _header_fields(root / chapter.path)
            delivery_gate = fields.get("Delivery gate")
            if delivery_gate not in status_by_gate:
                continue
            self.assertEqual(
                chapter.status,
                status_by_gate[delivery_gate],
                f"LLD-{chapter.number} planning status disagrees with {delivery_gate}",
            )


if __name__ == "__main__":
    unittest.main()

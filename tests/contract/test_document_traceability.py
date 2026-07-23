import re
import unittest
from pathlib import Path

from quanthus.policy import find_project_root


WORK_ITEM_ROW = re.compile(
    r"^\|\s*((?:META|P[1-7])-[A-Z0-9-]+-[0-9]{3}|[CS][0-9]+)\s*\|"
)


def _table_ids(path: Path) -> list[str]:
    ids: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = WORK_ITEM_ROW.match(line)
        if match:
            ids.append(match.group(1))
    return ids


class DocumentTraceabilityTests(unittest.TestCase):
    def test_lld_work_items_are_unique_and_indexed(self) -> None:
        root = find_project_root()
        index_ids = _table_ids(root / "docs" / "work-items" / "index.md")
        self.assertEqual(len(index_ids), len(set(index_ids)), "work-item index has duplicate ids")

        lld_ids: list[str] = []
        for path in sorted((root / "docs" / "design" / "lld").glob("*.md")):
            lld_ids.extend(_table_ids(path))

        missing = sorted(set(lld_ids).difference(index_ids))
        self.assertEqual(missing, [], f"LLD work items missing from index: {missing}")


if __name__ == "__main__":
    unittest.main()

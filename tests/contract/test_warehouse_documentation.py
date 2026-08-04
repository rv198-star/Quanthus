from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]
DICTIONARY = ROOT / "docs" / "design" / "lld" / "01-data-foundation" / "ods-dwd-data-dictionary.md"
LINEAGE = ROOT / "docs" / "design" / "lld" / "01-data-foundation" / "ods-to-dwd-lineage.md"
MODEL = ROOT / "config" / "pipelines" / "dwd-domain-model.v1.json"
RELEASE = ROOT / "config" / "releases" / "v2-build202607.json"


class WarehouseDocumentationContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dictionary = DICTIONARY.read_text(encoding="utf-8")
        cls.lineage = LINEAGE.read_text(encoding="utf-8")
        cls.model = json.loads(MODEL.read_text(encoding="utf-8"))
        cls.release = json.loads(RELEASE.read_text(encoding="utf-8"))

    def test_documents_are_routed_from_the_documentation_indexes(self) -> None:
        routes = [
            ROOT / "docs" / "README.md",
            ROOT / "docs" / "design" / "lld" / "README.md",
            ROOT / "docs" / "design" / "lld" / "01-data-foundation.md",
        ]
        for route in routes:
            text = route.read_text(encoding="utf-8")
            with self.subTest(route=route):
                self.assertIn("ods-dwd-data-dictionary.md", text)
                self.assertIn("ods-to-dwd-lineage.md", text)

    def test_release_identity_and_historical_ods_name_bridge_are_explicit(self) -> None:
        for layer in ("ods", "dwd"):
            contract = self.release["layers"][layer]
            with self.subTest(layer=layer):
                self.assertIn(contract["package_id"], self.dictionary)
                self.assertIn(contract["manifest_sha256"], self.dictionary)
                self.assertIn(contract["content_digest"], self.dictionary)

        self.assertIn("ods-base-202607-v1.2", self.lineage)
        self.assertIn(
            "sha256:090d6a50aac7007337229588e9cd1586a50f09811332e6024d08f28ddc4b6e05",
            self.lineage,
        )
        self.assertIn(self.release["layers"]["ods"]["manifest_sha256"], self.lineage)
        self.assertIn(self.release["layers"]["ods"]["content_digest"], self.lineage)
        self.assertIn("名称迁移没有重写 DWD manifest", self.lineage)

    def test_ods_catalog_declares_112_unique_datasets(self) -> None:
        catalog = self.dictionary.split("### 3.3 完整 ODS dataset 目录", 1)[1].split(
            "## 4. DWD 字段约定", 1
        )[0]
        declared_total = 0
        dataset_ids: list[str] = []
        for line in catalog.splitlines():
            match = re.fullmatch(r"- `[^`]+` \((\d+)\): (.+)", line)
            if match is None:
                continue
            declared = int(match.group(1))
            members = re.findall(r"`([^`]+)`", match.group(2))
            self.assertEqual(len(members), declared, line)
            declared_total += declared
            dataset_ids.extend(members)

        self.assertEqual(declared_total, 112)
        self.assertEqual(len(dataset_ids), 112)
        self.assertEqual(len(set(dataset_ids)), 112)

    def test_dwd_dictionary_covers_the_machine_model_and_base_row_total(self) -> None:
        table_lines = re.findall(r"^\| `(dwd_[^`]+)` \|.*$", self.dictionary, re.MULTILINE)
        model_tables = {table["name"] for table in self.model["tables"]}
        self.assertEqual(len(table_lines), 33)
        self.assertEqual(set(table_lines), model_tables)

        documented_rows: dict[str, int] = {}
        for line in self.dictionary.splitlines():
            if not line.startswith("| `dwd_"):
                continue
            columns = [column.strip() for column in line.split("|")]
            table = columns[1].strip("`")
            documented_rows[table] = int(columns[6].replace(",", ""))
        self.assertEqual(set(documented_rows), model_tables)
        self.assertEqual(sum(documented_rows.values()), 468_268_180)

        for table in model_tables:
            with self.subTest(table=table):
                self.assertIn(f"`{table}`", self.lineage)

    def test_full_build_and_recurring_coverage_are_not_conflated(self) -> None:
        ownership = self.model["build_execution_contract"]["dwd_script_ownership"]
        self.assertEqual(len(ownership), 9)
        self.assertEqual(set(ownership), {"57", "58", "59", "60", "61", "62", "63", "64", "72"})
        self.assertIn("9 个物理 DWD builder", self.lineage)

        daily = self.lineage.split("## 6. 当前日更血缘（实际部署）", 1)[1].split(
            "## 7. 当前周更血缘", 1
        )[0]
        daily_tables = {
            "dwd_trading_calendar",
            "dwd_security_bar",
            "dwd_security_valuation",
            "dwd_index_bar",
            "dwd_tradability_state",
            "dwd_disclosure",
            "dwd_disclosure_subject",
        }
        for table in daily_tables:
            with self.subTest(daily_table=table):
                self.assertIn(f"`{table}`", daily)
        self.assertIn("当前实际输出只有 7 张表", daily)

        unsupported = {
            "adjustment_evidence_delta",
            "suspensions_delta",
            "corporate_actions_delta",
            "dividends_delta",
            "factor_evidence_delta",
            "financial_disclosures_delta",
        }
        daily_code = (
            ROOT / "src" / "quanthus" / "pipelines" / "securities_warehouse" / "daily_domain.py"
        ).read_text(encoding="utf-8")
        for artifact in unsupported:
            with self.subTest(unsupported_artifact=artifact):
                self.assertIn(f"`{artifact}`", daily)
                self.assertIn(f'"{artifact}"', daily_code)
        self.assertIn("非空时 builder 必须失败", daily)

        weekly = self.lineage.split("## 7. 当前周更血缘", 1)[1].split("## 8. 外键和回放主链", 1)[0]
        self.assertIn("DWD correction 发布尚未启用", weekly)


if __name__ == "__main__":
    unittest.main()

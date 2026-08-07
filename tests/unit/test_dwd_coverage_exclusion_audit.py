import json
import importlib.util
import tempfile
import unittest
from pathlib import Path

from quanthus.pipelines.dwd_coverage_exclusion_audit import (
    _canonical_digest,
    audit_dwd_coverage_exclusions,
)


class DwdCoverageExclusionAuditTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("pyarrow"), "pyarrow is required for coverage scan")
    def test_plan_and_inventory_are_reported_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = root / "base"
            plan = root / "plan"
            base.mkdir()
            plan.mkdir()
            (base / "manifest.json").write_text(
                json.dumps(
                    {
                        "package_id": "dwd-base-202607-v2",
                        "content_digest": "sha256:" + "1" * 64,
                        "through_date": "2026-07-31",
                        "table_count": 33,
                        "row_counts": {"dwd_security": 1},
                    }
                ),
                encoding="ascii",
            )
            for name, value in {
                "input-plan.json": {"run_id": "run", "plan_digest": "sha256:" + "2" * 64},
                "coverage-matrix.json": {"domains": [{"domain": "listed_company", "status": "complete", "missing_source_tables": [], "source_gaps": []}]},
                "source-disposition-ledger.json": {"records": [{"dataset_id": "excluded", "disposition": "deferred", "reason": "outside scope", "target_tables": [], "row_count": 1}]},
                "capacity-estimate.json": {"environment": {"capacity_sufficient": True}},
            }.items():
                (plan / name).write_text(json.dumps(value), encoding="ascii")
            model = root / "model.json"
            table_names = ["dwd_security"] + [f"dwd_table_{index:02d}" for index in range(32)]
            model.write_text(
                json.dumps(
                    {
                        "tables": [{"name": name, "time_profile": "effective_interval"} for name in table_names],
                        "time_profiles": {"effective_interval": {"effective_from": "timestamp"}},
                    }
                ),
                encoding="ascii",
            )
            schema_value = {
                "tables": {
                    name: {
                        "status": "verified",
                        "row_count": 1,
                        "expected_rows": 1,
                        "part_count": 0,
                        "classified_empty_part_count": 0,
                        "row_count_matches_manifest": True,
                        "parts": [],
                    }
                    for name in table_names
                }
            }
            schema_value["canonical_digest"] = _canonical_digest(schema_value)
            schema = root / "schema.json"
            schema.write_text(json.dumps(schema_value), encoding="ascii")
            report = audit_dwd_coverage_exclusions(
                root=base,
                plan_root=plan,
                model_path=model,
                schema_report=schema,
            )
            self.assertEqual(report["summary"]["status"], "verified")
            self.assertFalse(report["summary"]["mutation_performed"])
            self.assertEqual(report["exclusions"][0]["disposition"], "deferred")


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path

from quanthus.pipelines.dwd_semantic_dimension_audit import (
    DwdSemanticDimensionAuditError,
    _canonical_digest,
    audit_dwd_semantic_dimensions,
    write_semantic_dimension_reports,
)


class DwdSemanticDimensionAuditTests(unittest.TestCase):
    def _reports(self, root: Path) -> tuple[Path, Path]:
        base_release = {
            "package_id": "dwd-base-202607-v2",
            "manifest_sha256": "sha256:" + "1" * 64,
            "content_digest": "sha256:" + "2" * 64,
            "through_date": "2026-07-31",
            "table_count": 33,
            "row_count": 33,
        }
        tables = {}
        matrix = []
        for index in range(33):
            table = f"dwd_fixture_{index:02d}"
            tables[table] = {
                "status": "verified",
                "row_count": 1,
                "violation_count": 0,
                "row_key": ["id"],
                "business_key": ["id"],
                "foreign_keys": [],
                "intervals": {},
                "lineage": {},
                "numeric_invariants": {},
                "duplicate_row_keys": {"group_count": 0, "row_count": 0},
                "business_key_version_groups": {"group_count": 0, "row_count": 0},
            }
            matrix.append(
                {
                    "table": table,
                    "time_profile": "static",
                    "reference_field": "effective_at",
                    "disposition": "forward_operational",
                    "historical_pit_authority": "verified",
                    "knowledge_rule": "fixture",
                    "ods_sufficiency": "reconstructable_existing_ods",
                    "evidence": ["fixture"],
                    "remediation_note": "",
                }
            )
        semantic = {
            "schema_version": 1,
            "kind": "dwd_base_v2_semantic_contract_audit",
            "base_release": base_release,
            "tables": tables,
            "summary": {"status": "verified", "finding_tables": [], "mutation_performed": False},
        }
        semantic["canonical_digest"] = _canonical_digest(semantic)
        pit = {
            "schema_version": 1,
            "kind": "dwd_base_v2_pit_semantics_audit",
            "base_release": base_release,
            "semantic_matrix": matrix,
            "execution_gate": {"status": "complete", "reason_codes": []},
            "historical_pit_gate": {
                "status": "verified",
                "reason_codes": [],
                "affected_tables": [],
            },
        }
        pit["report_digest"] = _canonical_digest(pit)
        semantic_path = root / "semantic.json"
        pit_path = root / "pit.json"
        semantic_path.write_text(json.dumps(semantic), encoding="ascii")
        pit_path.write_text(json.dumps(pit), encoding="ascii")
        return semantic_path, pit_path

    def test_projects_all_five_named_dimensions_with_bound_digests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            semantic, pit = self._reports(root)
            report = audit_dwd_semantic_dimensions(
                semantic_report_path=semantic,
                pit_report_path=pit,
            )
            self.assertEqual(report["summary"]["dimension_count"], 5)
            self.assertEqual(report["summary"]["table_count"], 33)
            self.assertFalse(report["projection_contract"]["rescan_performed"])
            self.assertTrue(report["projection_contract"]["source_findings_remain_authoritative"])
            output = root / "audit"
            result = write_semantic_dimension_reports(
                output,
                report,
                sealed_roots=(semantic, pit),
            )
            self.assertEqual(len(result["output_reports"]), 5)
            index = json.loads((output / "semantic-dimension-audit.json").read_text())
            for binding in index["output_reports"].values():
                path = Path(binding["path"])
                payload = json.loads(path.read_text())
                digest = payload.pop("canonical_digest")
                self.assertEqual(digest, binding["digest"])
                self.assertEqual(digest, _canonical_digest(payload))

    def test_base_identity_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            semantic, pit = self._reports(root)
            value = json.loads(pit.read_text())
            value["base_release"]["content_digest"] = "sha256:" + "f" * 64
            unsigned = {
                key: raw for key, raw in value.items() if key != "report_digest"
            }
            value["report_digest"] = _canonical_digest(unsigned)
            pit.write_text(json.dumps(value), encoding="ascii")
            with self.assertRaisesRegex(
                DwdSemanticDimensionAuditError, "different Base identities"
            ):
                audit_dwd_semantic_dimensions(
                    semantic_report_path=semantic,
                    pit_report_path=pit,
                )


if __name__ == "__main__":
    unittest.main()

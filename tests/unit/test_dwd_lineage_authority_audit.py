import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from quanthus.pipelines.dwd_lineage_authority_audit import (
    _canonical_digest,
    _sha256,
    audit_dwd_lineage_authority,
    write_lineage_authority_report,
)

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "config/pipelines/dwd-domain-model.v1.json"


class DwdLineageAuthorityAuditTests(unittest.TestCase):
    def _write_json(self, path: Path, value: object) -> None:
        path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")

    def _fixture(self, root: Path) -> tuple[Path, Path, Path]:
        dwd = root / "dwd"
        ods = root / "ods"
        plan_root = root / "plan"
        dwd.mkdir()
        ods.mkdir()
        plan_root.mkdir()
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        source_identity = {
            "package_id": "ods-v1",
            "manifest_sha256": "sha256:" + "1" * 64,
            "content_digest": "sha256:" + "2" * 64,
        }
        ledger = {
            "schema_version": 1,
            "records": [
                {
                    "dataset_id": "bars",
                    "disposition": "modeled",
                    "target_tables": ["dwd_security_bar"],
                }
            ],
            "summary": {"record_count": 1},
        }
        coverage = {"schema_version": 1, "domains": {}}
        capacity = {
            "schema_version": 1,
            "requirement": {"required_free_bytes": 1},
            "requirement_digest": _canonical_digest({"required_free_bytes": 1}),
        }
        semantic_plan = {
            "code_revision": revision,
            "input": {
                "bundle_id": source_identity["package_id"],
                "manifest_sha256": source_identity["manifest_sha256"],
                "content_digest": source_identity["content_digest"],
            },
            "model": {"sha256": _sha256(MODEL)},
            "source_ledger_digest": _canonical_digest(ledger),
            "coverage_matrix_digest": _canonical_digest(coverage),
            "capacity_requirement_digest": capacity["requirement_digest"],
        }
        plan = {
            "schema_version": 1,
            "kind": "dwd_dws_input_plan",
            "run_id": "fixture-run",
            "plan_digest": _canonical_digest(semantic_plan),
            "plan": semantic_plan,
        }
        values = {
            "input-plan.json": plan,
            "source-disposition-ledger.json": ledger,
            "coverage-matrix.json": coverage,
            "capacity-estimate.json": capacity,
            "run-receipt.json": {"schema_version": 1},
        }
        for name, value in values.items():
            self._write_json(plan_root / name, value)
        manifest = {
            "schema_version": 1,
            "kind": "dwd_dws_input_plan_package",
            "plan_digest": plan["plan_digest"],
            "files": [
                {
                    "path": name,
                    "sha256": _sha256(plan_root / name),
                    "size_bytes": (plan_root / name).stat().st_size,
                }
                for name in sorted(values)
            ],
        }
        self._write_json(plan_root / "manifest.json", manifest)
        (plan_root / "READY").write_text(
            _sha256(plan_root / "manifest.json") + "\n", encoding="ascii"
        )
        self._write_json(
            dwd / "manifest.json",
            {
                "package_id": "dwd-v2",
                "run_id": plan["run_id"],
                "input_plan_digest": plan["plan_digest"],
                "ods_base": source_identity,
                "model_sha256": _sha256(MODEL),
                "code_revision": revision,
            },
        )
        self._write_json(
            ods / "manifest.json",
            {
                "bundle_id": "ods-v2",
                "content_digest": source_identity["content_digest"],
                "storage_reuse": {
                    "sources": [
                        {
                            "bundle_id": source_identity["package_id"],
                            "manifest_sha256": source_identity["manifest_sha256"],
                            "content_digest": source_identity["content_digest"],
                        }
                    ]
                },
            },
        )
        return dwd, ods, plan_root

    def test_audit_closes_content_preserving_ods_republish(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            dwd, ods, plan = self._fixture(Path(temporary))

            report = audit_dwd_lineage_authority(
                dwd_root=dwd,
                ods_root=ods,
                plan_root=plan,
                repository=ROOT,
                model_path=MODEL,
            )

            self.assertEqual(report["status"], "verified")
            self.assertEqual(
                report["ods_authority"]["status"],
                "content_preserving_identity_republish",
            )
            self.assertFalse(report["mutation_performed"])

    def test_plan_member_tampering_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            dwd, ods, plan = self._fixture(Path(temporary))
            (plan / "coverage-matrix.json").write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "size or digest"):
                audit_dwd_lineage_authority(
                    dwd_root=dwd,
                    ods_root=ods,
                    plan_root=plan,
                    repository=ROOT,
                    model_path=MODEL,
                )

    def test_report_cannot_be_written_inside_a_sealed_package(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(RuntimeError, "outside sealed package"):
                write_lineage_authority_report(
                    root / "audit.json", {"status": "fixture"}, sealed_roots=(root,)
                )


if __name__ == "__main__":
    unittest.main()

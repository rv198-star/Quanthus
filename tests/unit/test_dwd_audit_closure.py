import json
import tempfile
import unittest
from pathlib import Path

from quanthus.pipelines.dwd_audit_closure import (
    DwdAuditClosureError,
    _canonical_digest,
    build_audit_closure,
)


def _sealed(path: Path, value: dict) -> None:
    payload = dict(value)
    payload["canonical_digest"] = _canonical_digest(value)
    path.write_text(json.dumps(payload), encoding="ascii")


class DwdAuditClosureTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, ...]:
        base = root / "base"
        plan = root / "plan"
        daily = root / "daily" / "packages" / "2026-08-01" / "one"
        base.mkdir(parents=True)
        plan.mkdir()
        daily.mkdir(parents=True)
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
        plan_payloads = {
            "input-plan.json": {"plan_digest": "sha256:" + "2" * 64},
            "coverage-matrix.json": {"domains": []},
            "source-disposition-ledger.json": {"records": []},
            "capacity-estimate.json": {"environment": {"capacity_sufficient": True}, "requirement": {}},
            "run-receipt.json": {"resource_use": {}},
        }
        for name, value in plan_payloads.items():
            (plan / name).write_text(json.dumps(value), encoding="ascii")
        (daily / "manifest.json").write_text(
            json.dumps(
                {
                    "package_id": "daily-one",
                    "base": {
                        "package_id": "dwd-base-202607-v2",
                        "manifest_sha256": "sha256:" + "0" * 64,
                        "content_digest": "sha256:" + "1" * 64,
                    },
                }
            ),
            encoding="ascii",
        )
        schema = {
            "mutation_performed": False,
            "tables": {
                "dwd_security": {
                    "status": "finding",
                    "finding_part_count": 1,
                    "part_count": 1,
                    "schema_variant_count": 2,
                    "row_count": 1,
                    "parts": [{"reason_codes": ["field_order_mismatch"]}],
                }
            },
        }
        semantic = {
            "mutation_performed": False,
            "tables": {
                "dwd_security": {
                    "status": "finding",
                    "row_count": 1,
                    "violation_count": 1,
                    "foreign_keys": [{"parent_table": "dwd_company", "column": "company_id", "temporal_or_future_parent_rows": 1}],
                }
            },
        }
        lineage = {"mutation_performed": False, "dwd": {"package_id": "dwd-base-202607-v2"}}
        invariant = {"mutation_performed": False, "tables": {}}
        phase_a = {"mutation_performed": False, "findings": []}
        paths = []
        for name, value in (
            ("schema.json", schema),
            ("semantic.json", semantic),
            ("lineage.json", lineage),
            ("invariant.json", invariant),
            ("phase-a.json", phase_a),
        ):
            path = root / name
            _sealed(path, value)
            paths.append(path)
        return base, plan, daily.parent.parent.parent, *paths

    def test_working_closure_keeps_builder_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base, plan, daily, schema, semantic, lineage, invariant, phase_a = self._fixture(Path(temporary))
            result = build_audit_closure(
                base_root=base,
                plan_root=plan,
                schema_report=schema,
                semantic_report=semantic,
                lineage_report=lineage,
                invariant_report=invariant,
                phase_a_findings=phase_a,
                daily_root=daily,
                output_root=Path(temporary) / "out",
                code_revision="a" * 40,
            )
            self.assertEqual(result["builder_gate"], "blocked")
            self.assertGreaterEqual(result["finding_count"], 2)
            self.assertTrue((Path(temporary) / "out" / "findings-ledger.json").is_file())

    def test_digest_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base, plan, daily, schema, semantic, lineage, invariant, phase_a = self._fixture(Path(temporary))
            payload = json.loads(schema.read_text(encoding="ascii"))
            payload["canonical_digest"] = "sha256:" + "f" * 64
            schema.write_text(json.dumps(payload), encoding="ascii")
            with self.assertRaisesRegex(DwdAuditClosureError, "canonical digest mismatch"):
                build_audit_closure(
                    base_root=base,
                    plan_root=plan,
                    schema_report=schema,
                    semantic_report=semantic,
                    lineage_report=lineage,
                    invariant_report=invariant,
                    phase_a_findings=phase_a,
                    daily_root=daily,
                    output_root=Path(temporary) / "out",
                    code_revision="a" * 40,
                )


if __name__ == "__main__":
    unittest.main()

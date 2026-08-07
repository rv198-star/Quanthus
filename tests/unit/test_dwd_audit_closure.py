import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from quanthus.pipelines.dwd_audit_closure import (
    _SEMANTIC_DIMENSION_REPORTS,
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

    def _semantic_dimension_report(self, root: Path, base: Path) -> Path:
        manifest = json.loads((base / "manifest.json").read_text(encoding="ascii"))
        base_release = {
            "package_id": manifest["package_id"],
            "manifest_sha256": "sha256:"
            + hashlib.sha256((base / "manifest.json").read_bytes()).hexdigest(),
            "content_digest": manifest["content_digest"],
            "through_date": manifest["through_date"],
            "table_count": 33,
            "row_count": 1,
        }
        output = root / "semantic-dimensions"
        output.mkdir()
        bindings = {}
        for dimension, (filename, kind) in _SEMANTIC_DIMENSION_REPORTS.items():
            value = {
                "schema_version": 1,
                "kind": kind,
                "generated_at": "2026-08-07T00:00:00+00:00",
                "base_release": base_release,
                "execution": {"read_only": True, "projection_only": True},
                "source_reports": {
                    "semantic_contract": {"path": "/archive/semantic.json", "digest": "sha256:" + "b" * 64},
                    "pit_semantics": {"path": "/archive/pit.json", "digest": "sha256:" + "c" * 64},
                },
                "projection_contract": {
                    "rescan_performed": False,
                    "findings_reclassified": False,
                    "source_findings_remain_authoritative": True,
                    "required_outputs": [name for name, _ in _SEMANTIC_DIMENSION_REPORTS.values()],
                },
                "source_summary": {"status": "verified", "reason_codes": []},
                "dimension_id": dimension,
                "evidence": {},
                "summary": {
                    "status": "verified",
                    "dimension_count": 5,
                    "table_count": 33,
                    "mutation_performed": False,
                },
                "mutation_performed": False,
            }
            path = output / filename
            _sealed(path, value)
            payload = json.loads(path.read_text(encoding="ascii"))
            bindings[dimension] = {
                "path": str(path),
                "kind": kind,
                "digest": payload["canonical_digest"],
            }
        index = {
            "schema_version": 1,
            "kind": "dwd_base_v2_semantic_dimension_audit",
            "generated_at": "2026-08-07T00:00:00+00:00",
            "base_release": base_release,
            "execution": {"read_only": True, "projection_only": True},
            "source_reports": {
                "semantic_contract": {"path": "/archive/semantic.json", "digest": "sha256:" + "b" * 64},
                "pit_semantics": {"path": "/archive/pit.json", "digest": "sha256:" + "c" * 64},
            },
            "projection_contract": {
                "rescan_performed": False,
                "findings_reclassified": False,
                "source_findings_remain_authoritative": True,
                "required_outputs": [name for name, _ in _SEMANTIC_DIMENSION_REPORTS.values()],
            },
            "dimensions": {dimension: {} for dimension in _SEMANTIC_DIMENSION_REPORTS},
            "output_reports": bindings,
            "summary": {
                "status": "verified",
                "dimension_count": 5,
                "table_count": 33,
                "reason_codes": [],
                "mutation_performed": False,
            },
            "mutation_performed": False,
        }
        index_path = output / "semantic-dimension-audit.json"
        _sealed(index_path, index)
        return index_path

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

    def test_semantic_dimension_report_is_required_for_closure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base, plan, daily, schema, semantic, lineage, invariant, phase_a = self._fixture(root)
            result = build_audit_closure(
                base_root=base,
                plan_root=plan,
                schema_report=schema,
                semantic_report=semantic,
                lineage_report=lineage,
                invariant_report=invariant,
                phase_a_findings=phase_a,
                daily_root=daily,
                output_root=root / "out-with-dimensions",
                code_revision="a" * 40,
                semantic_dimension_report=self._semantic_dimension_report(root, base),
            )
            ledger = json.loads(
                (root / "out-with-dimensions" / "findings-ledger.json").read_text(
                    encoding="ascii"
                )
            )
            ids = {item["finding_id"] for item in ledger["findings"]}
            self.assertNotIn("DWDV2-SEMANTIC-DIMENSION-REPORT", ids)
            self.assertNotIn("semantic_dimension_split_reports", result["open_audit_scope"])
            self.assertEqual(
                ledger["semantic_dimension_audit"]["dimension_count"], 5
            )
            self.assertIsNotNone(ledger["source_digests"]["semantic_dimensions"])

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

    def test_replay_finding_is_added_when_report_has_no_executions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base, plan, daily, schema, semantic, lineage, invariant, phase_a = self._fixture(root)
            replay = root / "replay.json"
            manifest = json.loads((base / "manifest.json").read_text(encoding="ascii"))
            replay_value = {
                "kind": "dwd_base_v2_replay_determinism_audit",
                "base_release": {
                    "package_id": manifest["package_id"],
                    "manifest_sha256": "sha256:" + hashlib.sha256(
                        (base / "manifest.json").read_bytes()
                    ).hexdigest(),
                    "content_digest": manifest["content_digest"],
                    "through_date": manifest["through_date"],
                },
                "summary": {
                    "status": "finding",
                    "mutation_performed": False,
                    "reason_codes": ["base_replay_evidence_missing"],
                    "independent_execution_count": 0,
                },
                "evidence": [],
            }
            _sealed(replay, replay_value)
            result = build_audit_closure(
                base_root=base,
                plan_root=plan,
                schema_report=schema,
                semantic_report=semantic,
                lineage_report=lineage,
                invariant_report=invariant,
                phase_a_findings=phase_a,
                daily_root=daily,
                output_root=root / "out-replay",
                code_revision="a" * 40,
                replay_report=replay,
            )
            ledger = json.loads((root / "out-replay" / "findings-ledger.json").read_text(encoding="ascii"))
            ids = {item["finding_id"] for item in ledger["findings"]}
            self.assertIn("DWDV2-REPLAY-DETERMINISM", ids)
            self.assertNotIn(
                "representative_replay_determinism_for_base_derivations",
                result["open_audit_scope"],
            )

    def test_missing_readiness_report_is_classified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base, plan, daily, schema, semantic, lineage, invariant, phase_a = self._fixture(
                Path(temporary)
            )
            result = build_audit_closure(
                base_root=base,
                plan_root=plan,
                schema_report=schema,
                semantic_report=semantic,
                lineage_report=lineage,
                invariant_report=invariant,
                phase_a_findings=phase_a,
                daily_root=daily,
                output_root=Path(temporary) / "out-readiness",
                code_revision="a" * 40,
            )
            ledger = json.loads(
                (Path(temporary) / "out-readiness" / "findings-ledger.json").read_text(
                    encoding="ascii"
                )
            )
            ids = {item["finding_id"] for item in ledger["findings"]}
            self.assertIn("DWDV2-READINESS-REPORT", ids)
            self.assertIn(
                "rebuild_publication_and_rollback_readiness_acceptance",
                result["open_audit_scope"],
            )

    def test_missing_downstream_report_is_classified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base, plan, daily, schema, semantic, lineage, invariant, phase_a = self._fixture(
                Path(temporary)
            )
            result = build_audit_closure(
                base_root=base,
                plan_root=plan,
                schema_report=schema,
                semantic_report=semantic,
                lineage_report=lineage,
                invariant_report=invariant,
                phase_a_findings=phase_a,
                daily_root=daily,
                output_root=Path(temporary) / "out-downstream",
                code_revision="a" * 40,
            )
            ledger = json.loads(
                (Path(temporary) / "out-downstream" / "findings-ledger.json").read_text(
                    encoding="ascii"
                )
            )
            ids = {item["finding_id"] for item in ledger["findings"]}
            self.assertIn("DWDV2-DOWNSTREAM-REPORT", ids)
            self.assertIn(
                "downstream_increment_compatibility_review",
                result["open_audit_scope"],
            )


if __name__ == "__main__":
    unittest.main()

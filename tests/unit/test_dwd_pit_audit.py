import importlib.util
import tempfile
import unittest
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from quanthus.pipelines.dwd_pit_audit import (
    DwdPitAuditError,
    DwdPitTablePolicy,
    _manifest_payload_files,
    _scan_table,
    _validate_output_paths,
    audit_dwd_pit_semantics,
    load_dwd_pit_audit_policy,
)

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "config/audits/dwd-pit-semantics.v1.json"
MODEL = ROOT / "config/pipelines/dwd-domain-model.v1.json"
HAS_PARQUET_AUDIT = (
    importlib.util.find_spec("pyarrow") is not None
    and importlib.util.find_spec("duckdb") is not None
)


class DwdPitAuditTests(unittest.TestCase):
    def test_policy_covers_exact_model_with_bounded_classifications(self) -> None:
        policy, model = load_dwd_pit_audit_policy(POLICY, model_path=MODEL)

        self.assertEqual(len(policy.tables), 33)
        self.assertEqual({item.table for item in policy.tables}, set(model))
        self.assertEqual(
            Counter(item.disposition for item in policy.tables),
            {
                "build_timestamp_substituted": 17,
                "ambiguous_source_insufficient": 4,
                "source_derived_historical": 5,
                "forward_operational": 7,
            },
        )
        self.assertEqual(
            Counter(item.ods_sufficiency for item in policy.tables),
            {
                "reconstructable_existing_ods": 16,
                "partially_reconstructable_fail_closed_remainder": 8,
                "requires_additive_evidence_or_fail_closed": 2,
                "forward_only_not_historical": 7,
            },
        )
        self.assertEqual(policy.required_mirror_labels, ("hermes", "cn01", "oci"))

    def test_manifest_payload_files_map_flat_and_partitioned_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = {
                "files": [
                    {"path": "payload/listed_company/dwd_security.parquet"},
                    {
                        "path": (
                            "payload/security_market_observation/dwd_security_bar/"
                            "interval_size=1d/observation_start_year=2020/part-00000.parquet"
                        )
                    },
                    {"path": "evidence/domains/listed_company/manifest.json"},
                ]
            }

            mapped = _manifest_payload_files(
                root,
                manifest,
                table_names=("dwd_security", "dwd_security_bar"),
            )

        self.assertEqual(
            mapped["dwd_security"],
            [root / "payload/listed_company/dwd_security.parquet"],
        )
        self.assertEqual(
            mapped["dwd_security_bar"],
            [
                root
                / "payload/security_market_observation/dwd_security_bar/"
                "interval_size=1d/observation_start_year=2020/part-00000.parquet"
            ],
        )

    def test_output_inside_sealed_mirror_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            with self.assertRaisesRegex(DwdPitAuditError, "must not be written inside"):
                _validate_output_paths(
                    [root / "evidence/report.json"], mirrors={"oci": root}
                )

    def test_duplicate_mirror_roots_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(DwdPitAuditError, "distinct physical roots"):
                audit_dwd_pit_semantics(
                    mirrors={"hermes": root, "cn01": root},
                    policy_path=POLICY,
                    model_path=MODEL,
                    mode="manifest-only",
                )

    def test_inaccessible_mirror_produces_fail_closed_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing-base"
            report = audit_dwd_pit_semantics(
                mirrors={"oci": missing},
                policy_path=POLICY,
                model_path=MODEL,
                mode="manifest-only",
            )

        self.assertEqual(report["execution_gate"]["status"], "blocked")
        self.assertIn(
            "required_mirror_not_supplied", report["execution_gate"]["reason_codes"]
        )
        self.assertIn(
            "mirror_verification_failed", report["execution_gate"]["reason_codes"]
        )
        self.assertEqual(report["historical_pit_gate"]["status"], "blocked")
        self.assertFalse(report["accessed_artifacts"]["mutation_performed"])

    @unittest.skipUnless(HAS_PARQUET_AUDIT, "full audit dependencies are unavailable")
    def test_table_scan_exposes_historical_future_knowledge(self) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "part-00000.parquet"
            table = pa.table(
                {
                    "record_source": ["fixture", "fixture", "fixture"],
                    "source_record_id": ["late", "historical", "current"],
                    "knowledge_from": pa.array(
                        [
                            datetime(2026, 8, 1, tzinfo=timezone.utc),
                            datetime(2013, 1, 2, tzinfo=timezone.utc),
                            datetime(2026, 7, 31, tzinfo=timezone.utc),
                        ],
                        type=pa.timestamp("us", tz="UTC"),
                    ),
                    "observation_end": pa.array(
                        [
                            datetime(2012, 1, 3, tzinfo=timezone.utc),
                            datetime(2013, 1, 2, tzinfo=timezone.utc),
                            datetime(2026, 7, 31, tzinfo=timezone.utc),
                        ],
                        type=pa.timestamp("us", tz="UTC"),
                    ),
                }
            )
            pq.write_table(table, path)
            policy = DwdPitTablePolicy(
                table="dwd_security_bar",
                disposition="build_timestamp_substituted",
                historical_pit_authority="blocked",
                reference_field="observation_end",
                ods_sufficiency="reconstructable_existing_ods",
                builder_path="fixture.py",
                knowledge_rule="fixture",
                evidence=("fixture",),
                remediation_note="fixture",
            )

            summary = _scan_table(
                [path],
                policy=policy,
                expected_rows=3,
                development_start=datetime(2012, 1, 1).date(),
                development_end=datetime(2021, 12, 31).date(),
                through_date=datetime(2026, 7, 31).date(),
            )

        self.assertEqual(summary["total_rows"], 3)
        self.assertEqual(summary["development_rows"], 2)
        self.assertEqual(summary["development_future_knowledge_rows"], 1)
        self.assertEqual(summary["build_year_reference_rows"], 1)
        self.assertEqual(
            summary["development_knowledge_year_distribution"], {"2013": 1, "2026": 1}
        )
        self.assertEqual(
            summary["development_future_knowledge_samples"][0]["source_record_id"], "late"
        )
        self.assertTrue(summary["row_count_matches_manifest"])


if __name__ == "__main__":
    unittest.main()

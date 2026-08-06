import importlib.util
import json
import tempfile
import unittest
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from quanthus.pipelines.ods_pit_audit import (
    OdsPhysicalCheck,
    OdsPitAuditError,
    _scan_parquet,
    _validate_output_paths,
    audit_ods_pit_sufficiency,
    load_ods_pit_audit_policy,
)

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "config/audits/ods-pit-sufficiency.v1.json"
MODEL = ROOT / "config/pipelines/dwd-domain-model.v1.json"
HAS_PARQUET_AUDIT = (
    importlib.util.find_spec("pyarrow") is not None
    and importlib.util.find_spec("duckdb") is not None
)


class OdsPitSufficiencyPolicyTests(unittest.TestCase):
    def test_policy_covers_all_dwd_tables_and_freezes_successor_boundary(self) -> None:
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        loaded = load_ods_pit_audit_policy(POLICY)
        model = json.loads(MODEL.read_text(encoding="utf-8"))
        expected_tables = {item["name"] for item in model["tables"]}
        mapped_tables = {
            table
            for family in policy["source_families"]
            for table in family["mapped_dwd_tables"]
        }

        self.assertEqual(policy["kind"], "ods_pit_sufficiency_audit")
        self.assertEqual(len(loaded.checks), 21)
        self.assertEqual(policy["ods_base"]["package_id"], "ods-base-202607-v2")
        self.assertEqual(policy["ods_base"]["through_date"], "2026-07-31")
        self.assertEqual(policy["ods_base"]["physical_holders"], ["hermes", "cn01"])
        self.assertFalse(policy["ods_base"]["oci_ods_enabled"])
        self.assertEqual(
            policy["successor_dwd_boundary"]["package_id"],
            "dwd-base-202607-v2.1",
        )
        self.assertEqual(
            policy["successor_dwd_boundary"]["through_date"], "2026-07-31"
        )
        self.assertEqual(
            policy["successor_dwd_boundary"]["daily_disposition"],
            "excluded_from_v2_1_base_and_cleaned_rebuilt_rebased_separately",
        )
        self.assertEqual(mapped_tables, expected_tables)

    def test_cn01_nfs_rebuild_topology_is_single_writer_and_fail_closed(self) -> None:
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        topology = policy["cn01_rebuild_execution_topology"]
        local = topology["local_state"]
        nfs = topology["nfs"]
        publication = topology["publication"]

        self.assertEqual(policy["policy_version"], "1.3.0")
        self.assertEqual(topology["execution_node"], "CN01")
        self.assertTrue(topology["single_writer"])
        self.assertEqual(topology["execution_limits"]["max_parallel_domain_builders"], 1)
        self.assertEqual(nfs["mount_point"], "/personal")
        self.assertGreaterEqual(nfs["minimum_free_bytes"], 100290327430)
        self.assertGreater(nfs["observed_free_bytes"], nfs["minimum_free_bytes"])
        self.assertEqual(nfs["fallback_to_local_scratch"], "forbidden")
        self.assertEqual(nfs["cross_host_locking"], "forbidden")
        self.assertTrue(local["lock_path"].startswith("/srv/quanthus/"))
        self.assertFalse(local["lock_path"].startswith(nfs["mount_point"] + "/"))
        self.assertTrue(local["ingress_path"].startswith(local["base_root"] + "/."))
        self.assertEqual(
            publication["expected_current_manifest_sha256"],
            policy["successor_dwd_boundary"]["predecessor_manifest_sha256"],
        )
        self.assertEqual(
            publication["cross_filesystem_install"],
            "manifest_driven_copy_to_hidden_local_ingress",
        )
        self.assertIn(
            "place_current_pointer_or_lock_authority_on_nfs",
            topology["forbidden_actions"],
        )

    def test_policy_records_cn01_deep_verification_and_keeps_hermes_pending(self) -> None:
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        scope = policy["evidence_scope"]
        cn01 = policy["cn01_physical_verification"]

        self.assertEqual(
            policy["oci_source_mirror"]["authority"],
            "non_authoritative_read_only_source_mirror",
        )
        self.assertEqual(scope["cn01_exact_ods_base_bytes"], "deep_verified")
        self.assertEqual(scope["exact_hermes_ods_base_bytes"], "pending_read_only_access")
        self.assertEqual(cn01["deep_verify"], "passed")
        self.assertEqual(cn01["file_count"], 21396)
        self.assertFalse(cn01["mutation_performed"])

    def test_policy_distinguishes_timestamp_rule_and_missing_evidence(self) -> None:
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        by_name = {item["source_family"]: item for item in policy["source_families"]}
        dispositions = Counter(item["sufficiency"] for item in policy["source_families"])

        self.assertEqual(len(by_name), 18)
        self.assertEqual(
            by_name["security_company_listing_identity"]["sufficiency"],
            "business_identity_complete_historical_attribute_contract_required",
        )
        self.assertEqual(
            by_name["adjustment_factors"]["observed_evidence"][
                "fail_closed_exception_rows"
            ],
            4173,
        )
        self.assertEqual(
            by_name["shareholding_changes"]["observed_evidence"][
                "disclosure_date_null_rows"
            ],
            124674,
        )
        self.assertEqual(
            by_name["corporate_actions_and_terms"]["observed_evidence"][
                "corporate_action_announce_null_rows"
            ],
            0,
        )
        self.assertEqual(
            by_name["financial_reporting"]["observed_evidence"][
                "post_cutoff_publish_date_report_versions"
            ],
            86,
        )
        self.assertEqual(
            by_name["financial_reporting"]["sufficiency"],
            "forward_only_from_acquisition_without_revision_archive",
        )
        self.assertEqual(
            by_name["announcements"]["observed_evidence"][
                "midnight_placeholder_rows"
            ],
            5413404,
        )
        self.assertEqual(
            by_name["announcements"]["sufficiency"],
            "mixed_precision_timestamp_conservative_rule_required",
        )
        self.assertEqual(
            by_name["security_valuation"]["sufficiency"],
            "historical_pit_unavailable_forward_only_from_acquisition",
        )
        self.assertGreaterEqual(dispositions["source_timestamp_sufficient"], 2)
        checks = {item.check_id: item for item in load_ods_pit_audit_policy(POLICY).checks}
        self.assertEqual(
            checks["full_financial_statements"].path_suffix,
            "artifacts/financial_statements.parquet",
        )
        self.assertEqual(policy["july_daily_tail"]["daily_ohlcv_rows"], 24960)
        self.assertEqual(
            policy["july_daily_tail"]["verified_empty_zero_column_parts"][
                "corporate_actions_delta"
            ],
            5,
        )

    def test_output_inside_ods_mirror_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            with self.assertRaisesRegex(OdsPitAuditError, "must not be written inside"):
                _validate_output_paths(
                    [root / "evidence" / "ods-audit.json"], mirrors={"hermes": root}
                )

    def test_inaccessible_ods_mirror_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing-ods"
            report = audit_ods_pit_sufficiency(
                mirrors={"hermes": missing},
                policy_path=POLICY,
                mode="manifest-only",
            )

        self.assertEqual(report["execution_gate"]["status"], "blocked")
        self.assertIn(
            "required_mirror_not_supplied", report["execution_gate"]["reason_codes"]
        )
        self.assertIn(
            "mirror_verification_failed", report["execution_gate"]["reason_codes"]
        )
        self.assertFalse(report["accessed_artifacts"]["mutation_performed"])

    @unittest.skipUnless(HAS_PARQUET_AUDIT, "full audit dependencies are unavailable")
    def test_parquet_scan_records_nulls_and_ranges(self) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source.parquet"
            pq.write_table(
                pa.table(
                    {
                        "stock_code": ["000001", "000001"],
                        "date": [datetime(2020, 1, 2), datetime(2020, 1, 3)],
                        "knowledge_cutoff": pa.array(
                            [datetime(2020, 1, 2, tzinfo=timezone.utc), None],
                            type=pa.timestamp("us", tz="UTC"),
                        ),
                    }
                ),
                path,
            )
            check = OdsPhysicalCheck(
                check_id="fixture",
                dataset_id="fixture",
                file_format="parquet",
                member=None,
                path_suffix=None,
                required_columns=("stock_code", "date", "knowledge_cutoff"),
                business_time_fields=("date",),
                knowledge_fields=("knowledge_cutoff",),
            )
            result = _scan_parquet([path], check)

        self.assertEqual(result["row_count"], 2)
        self.assertEqual(result["fields"]["knowledge_cutoff"]["null_rows"], 1)
        self.assertEqual(
            result["fields"]["date"]["minimum"], "2020-01-02T00:00:00"
        )


if __name__ == "__main__":
    unittest.main()

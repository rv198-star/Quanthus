import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "config/audits/dwd-base-v2-pre-rebuild-audit.v1.json"
MODEL = ROOT / "config/pipelines/dwd-domain-model.v1.json"


class DwdBasePreRebuildAuditPolicyTests(unittest.TestCase):
    def test_policy_covers_exact_33_table_model(self) -> None:
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        model = json.loads(MODEL.read_text(encoding="utf-8"))
        expected_tables = [item["name"] for item in model["tables"]]

        self.assertEqual(policy["kind"], "dwd_base_pre_rebuild_audit_policy")
        self.assertEqual(policy["policy_version"], "1.2.0")
        self.assertEqual(policy["governing_issue"], 78)
        self.assertEqual(policy["base_release"]["expected_table_count"], 33)
        self.assertEqual(policy["table_scope"], expected_tables)
        self.assertEqual(len(set(policy["table_scope"])), 33)
        self.assertEqual(policy["required_mirror_labels"], ["cn01"])
        self.assertEqual(policy["audit_authority"]["all_13_dimensions_execute_on"], "cn01")
        self.assertFalse(
            policy["audit_authority"]["replica_pre_rebuild_semantic_audit_required"]
        )

    def test_v2_1_build_is_hard_blocked_until_full_audit_closure(self) -> None:
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        gate = policy["hard_gate"]
        closure = policy["audit_closure_gate"]

        self.assertFalse(gate["dwd_v2_1_builder_execution_authorized"])
        self.assertFalse(gate["dwd_v2_1_candidate_assembly_authorized"])
        self.assertFalse(gate["dwd_v2_1_pointer_cutover_authorized"])
        self.assertTrue(gate["no_partial_rebuild_escape"])
        self.assertTrue(closure["all_33_tables_profiled"])
        self.assertTrue(closure["all_dimensions_complete"])
        self.assertEqual(closure["unresolved_critical_findings_allowed"], 0)
        self.assertTrue(closure["dwd_v2_1_builder_gate_opens_only_after_closure"])

    def test_all_required_audit_dimensions_and_outputs_are_frozen(self) -> None:
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        dimension_ids = {
            item["dimension_id"] for item in policy["audit_dimensions"]
        }
        required_dimensions = {
            "canonical_package_integrity",
            "schema_and_partition_contract",
            "pit_and_field_availability",
            "identity_and_entity_graph",
            "referential_integrity",
            "business_keys_row_keys_and_versions",
            "temporal_interval_integrity",
            "completeness_and_coverage",
            "numeric_and_domain_invariants",
            "lineage_and_source_authority",
            "derived_replay_determinism",
            "downstream_and_increment_compatibility",
            "rebuild_publication_and_rollback_readiness",
        }
        artifacts = set(policy["required_outputs"]["artifacts"])

        self.assertEqual(dimension_ids, required_dimensions)
        self.assertEqual(len(policy["execution_phases"]), 6)
        self.assertEqual(
            policy["required_outputs"]["root"],
            "/personal/quanthus-cn01-build-scratch/dwd-base-202607-v2.1/pre-rebuild-audit",
        )
        self.assertIn("findings-ledger.json", artifacts)
        self.assertIn("canonical-package-inventory.json", artifacts)
        self.assertNotIn("mirror-and-package-inventory.json", artifacts)
        self.assertIn("v2.1-remediation-scope.json", artifacts)
        self.assertIn("v2.1-build-contract.json", artifacts)
        self.assertTrue(
            policy["required_outputs"]["all_json_outputs_require_canonical_digest"]
        )

    def test_findings_cannot_remain_unknown_at_scope_freeze(self) -> None:
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        finding_model = policy["finding_model"]
        required_fields = set(finding_model["required_fields"])

        self.assertEqual(
            finding_model["unknown_or_unclassified_disposition"],
            "forbidden_at_scope_freeze",
        )
        self.assertIn("affected_rows_or_partitions", required_fields)
        self.assertIn("builder_or_contract_change", required_fields)
        self.assertIn("v2_1_acceptance_check", required_fields)
        self.assertIn("fix_in_v2_1", finding_model["allowed_dispositions"])
        self.assertIn("fail_closed_exclusion", finding_model["allowed_dispositions"])
        self.assertIn("no_change_proven", finding_model["allowed_dispositions"])

    def test_issue_78_owns_audit_and_successor_acceptance(self) -> None:
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        issue_gate = policy["issue_closure_gate"]

        self.assertTrue(issue_gate["pre_rebuild_audit_accepted"])
        self.assertTrue(issue_gate["v2_1_complete_33_table_package_accepted"])
        self.assertTrue(issue_gate["cn01_local_package_installed_and_verified"])
        self.assertTrue(issue_gate["deployed_successor_replicas_checksum_identical"])
        self.assertTrue(issue_gate["consumer_cutover_and_rollback_accepted"])
        self.assertIn(
            "close_issue_78_after_scope_freeze_but_before_v2_1_acceptance",
            policy["forbidden_actions"],
        )


if __name__ == "__main__":
    unittest.main()

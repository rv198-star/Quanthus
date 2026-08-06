import unittest
from pathlib import Path

from quanthus.pipelines.dwd_semantic_contract_audit import (
    _checkpoint_value,
    _duplicate_key_metrics,
    _interval_metrics,
    _key_version_metrics,
    _load_checkpoint,
    _numeric_predicates,
    _partition_confines_business_key,
    _prepare_scratch,
    _validated_memory_limit,
    load_semantic_contracts,
    write_semantic_contract_report,
)

ROOT = Path(__file__).resolve().parents[2]


class DwdSemanticContractAuditTests(unittest.TestCase):
    def test_memory_limit_is_explicit_and_injection_safe(self) -> None:
        self.assertEqual(_validated_memory_limit("2048mb"), "2048MB")
        self.assertEqual(_validated_memory_limit("2GB"), "2GB")
        for invalid in ("0GB", "2.5GB", "2GiB", "2GB; DROP TABLE x"):
            with (
                self.subTest(invalid=invalid),
                self.assertRaisesRegex(RuntimeError, "memory limit"),
            ):
                _validated_memory_limit(invalid)

    def test_report_cannot_be_written_inside_sealed_package(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(RuntimeError, "outside sealed package"):
                write_semantic_contract_report(
                    root / "audit.json",
                    {"status": "fixture"},
                    sealed_roots=(root,),
                )

    def test_scratch_cannot_be_created_inside_sealed_package(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(RuntimeError, "outside sealed package"):
                _prepare_scratch(root, root / "scratch")

    def test_checkpoint_is_bound_to_identity_and_digest(self) -> None:
        import json
        import tempfile

        identity = {"package_id": "fixture", "auditor_sha256": "sha256:fixture"}
        tables = {"dwd_fixture": {"status": "verified"}}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "checkpoint.json"
            path.write_text(json.dumps(_checkpoint_value(identity, tables)))

            self.assertEqual(
                _load_checkpoint(
                    path,
                    identity=identity,
                    allowed_tables={"dwd_fixture"},
                ),
                tables,
            )
            with self.assertRaisesRegex(RuntimeError, "identity or digest differs"):
                _load_checkpoint(
                    path,
                    identity={"package_id": "other"},
                    allowed_tables={"dwd_fixture"},
                )

    def test_repository_model_resolves_all_keys_and_foreign_keys(self) -> None:
        contracts = load_semantic_contracts(ROOT / "config/pipelines/dwd-domain-model.v1.json")

        self.assertEqual(len(contracts), 33)
        self.assertEqual(
            contracts["dwd_security_bar"].row_key,
            (
                "listing_id",
                "observation_start",
                "interval_size",
                "price_basis",
                "knowledge_from",
            ),
        )
        self.assertEqual(
            [item.table for item in contracts["dwd_security_bar"].foreign_keys],
            ["dwd_listing", "dwd_trading_calendar"],
        )
        self.assertTrue(_partition_confines_business_key(contracts["dwd_security_bar"]))
        self.assertTrue(_partition_confines_business_key(contracts["dwd_financial_fact"]))
        self.assertFalse(_partition_confines_business_key(contracts["dwd_quality_exception"]))

    def test_duplicate_and_interval_metrics_detect_violations(self) -> None:
        import duckdb

        connection = duckdb.connect(":memory:")
        try:
            connection.execute(
                """
                CREATE TABLE fixture (
                  company_id VARCHAR,
                  effective_from TIMESTAMPTZ,
                  effective_to TIMESTAMPTZ,
                  knowledge_from TIMESTAMPTZ,
                  knowledge_to TIMESTAMPTZ
                );
                INSERT INTO fixture VALUES
                  ('a', '2020-01-01', '2020-03-01', '2021-01-01', '2021-02-01'),
                  ('a', '2020-01-01', '2020-03-01', '2021-01-15', NULL),
                  ('a', '2020-02-01', NULL, '2021-03-01', NULL),
                  ('b', '2020-01-01', '2019-01-01', '2021-02-01', '2021-01-01')
                """
            )
            contract = load_semantic_contracts(ROOT / "config/pipelines/dwd-domain-model.v1.json")[
                "dwd_listed_company"
            ]

            self.assertEqual(
                _duplicate_key_metrics(
                    connection,
                    "fixture",
                    ("company_id", "effective_from"),
                ),
                {"group_count": 1, "row_count": 2},
            )
            self.assertEqual(
                _key_version_metrics(connection, "fixture", contract),
                {
                    "duplicate_row_keys": {"group_count": 0, "row_count": 0},
                    "business_key_version_groups": {
                        "group_count": 1,
                        "row_count": 2,
                    },
                    "knowledge_interval_overlap_rows": 1,
                },
            )
            metrics = _interval_metrics(connection, "fixture", contract)
            self.assertEqual(metrics["invalid_knowledge_interval_rows"], 1)
            self.assertEqual(metrics["invalid_effective_from_effective_to_rows"], 1)
            self.assertEqual(metrics["knowledge_interval_overlap_rows"], 1)
            self.assertEqual(metrics["effective_interval_overlap_rows"], 1)
        finally:
            connection.close()

    def test_explicit_numeric_predicates_follow_model_constraints(self) -> None:
        contracts = load_semantic_contracts(ROOT / "config/pipelines/dwd-domain-model.v1.json")

        bar = _numeric_predicates(contracts["dwd_security_bar"])
        self.assertIn("ohlc_envelope_rows", bar)
        self.assertIn("negative_volume_rows", bar)
        self.assertIn("non_unadjusted_price_basis_rows", bar)
        self.assertEqual(
            _numeric_predicates(contracts["dwd_adjustment_factor"])["nonpositive_factor_rows"],
            "factor_value <= 0",
        )


if __name__ == "__main__":
    unittest.main()

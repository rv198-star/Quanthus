import unittest
from pathlib import Path
from unittest.mock import patch

from quanthus.pipelines.dwd_semantic_contract_audit import (
    _checkpoint_value,
    _duplicate_key_metrics,
    _interval_metrics,
    _key_version_metrics,
    _load_checkpoint,
    _numeric_metrics,
    _numeric_predicates,
    _partition_confines_business_key,
    _partitioned_key_version_metrics,
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

    def test_partitioned_key_metrics_shard_without_splitting_business_keys(self) -> None:
        import tempfile

        import duckdb

        contracts = load_semantic_contracts(ROOT / "config/pipelines/dwd-domain-model.v1.json")
        contract = contracts["dwd_security_bar"]
        connection = duckdb.connect(":memory:")
        try:
            connection.execute(
                """
                CREATE TABLE bars (
                  listing_id VARCHAR,
                  observation_start TIMESTAMPTZ,
                  interval_size VARCHAR,
                  price_basis VARCHAR,
                  knowledge_from TIMESTAMPTZ,
                  knowledge_to TIMESTAMPTZ
                );
                INSERT INTO bars VALUES
                  ('a', '2026-01-01', '1d', 'UNADJUSTED', '2026-01-02', '2026-01-03'),
                  ('a', '2026-01-01', '1d', 'UNADJUSTED', '2026-01-02', '2026-01-03'),
                  ('a', '2026-01-01', '1d', 'UNADJUSTED', '2026-01-03', NULL),
                  ('b', '2026-01-02', '1d', 'UNADJUSTED', '2026-01-03', NULL);
                """
            )
            expected = _key_version_metrics(connection, "bars", contract)
            with tempfile.TemporaryDirectory() as temporary:
                path = (
                    Path(temporary)
                    / "interval_size=1d"
                    / "observation_start_year=2026"
                    / "observation_start_month=01"
                    / "part.parquet"
                )
                path.parent.mkdir(parents=True)
                connection.execute(f"COPY bars TO '{path}' (FORMAT PARQUET)")
                with patch(
                    "quanthus.pipelines.dwd_semantic_contract_audit."
                    "_KEY_VERSION_TARGET_COMPRESSED_BYTES",
                    max(1, path.stat().st_size // 2),
                ):
                    actual = _partitioned_key_version_metrics(
                        connection,
                        [path],
                        contract,
                    )
            self.assertEqual(actual, expected)
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

    def test_ohlc_finding_requires_an_exact_governed_exception_match(self) -> None:
        import duckdb

        connection = duckdb.connect(":memory:")
        try:
            connection.execute(
                """
                CREATE TABLE bars AS SELECT
                  'listing-1'::VARCHAR AS listing_id,
                  TIMESTAMPTZ '2026-07-31 07:00:00+00:00' AS observation_start,
                  '1d'::VARCHAR AS interval_size,
                  'UNADJUSTED'::VARCHAR AS price_basis,
                  10.0::DOUBLE AS open,
                  9.0::DOUBLE AS high,
                  8.0::DOUBLE AS low,
                  8.5::DOUBLE AS close,
                  1.0::DOUBLE AS volume,
                  1.0::DOUBLE AS turnover,
                  1::BIGINT AS trade_count
                """
            )
            key = (
                '{"interval_size":"1d","listing_id":"listing-1",'
                '"observation_start":"2026-07-31T07:00:00+00:00",'
                '"price_basis":"UNADJUSTED"}'
            )
            connection.execute(
                """
                CREATE TABLE quality (
                  affected_table VARCHAR,
                  rule_id VARCHAR,
                  resolution_state VARCHAR,
                  disposition VARCHAR,
                  affected_effective_at TIMESTAMPTZ,
                  affected_business_key_json VARCHAR
                )
                """
            )
            connection.execute(
                """
                INSERT INTO quality VALUES (
                  'dwd_security_bar',
                  'DWD_SECURITY_BAR_OHLC_ENVELOPE_V1',
                  'OPEN_ACCEPTED',
                  'ACCEPTED_LIMITATION',
                  TIMESTAMPTZ '2026-07-31 07:00:00+00:00',
                  ?
                )
                """,
                [key],
            )
            contract = load_semantic_contracts(ROOT / "config/pipelines/dwd-domain-model.v1.json")[
                "dwd_security_bar"
            ]

            accepted = _numeric_metrics(connection, "bars", contract, quality_source="quality")
            self.assertEqual(accepted["ohlc_envelope_rows"], 0)

            connection.execute(
                "UPDATE quality SET affected_business_key_json = "
                "replace(affected_business_key_json, 'listing-1', 'listing-2')"
            )
            mismatched = _numeric_metrics(connection, "bars", contract, quality_source="quality")
            self.assertEqual(mismatched["ohlc_envelope_rows"], 1)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()

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
from quanthus.pipelines.securities_warehouse.financial_concept_semantics import (
    load_financial_concept_semantics_policy,
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
        contracts = load_semantic_contracts(ROOT / "config/pipelines/dwd-domain-model.v1.json")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "checkpoint.json"
            path.write_text(json.dumps(_checkpoint_value(identity, {})))

            self.assertEqual(
                _load_checkpoint(
                    path,
                    identity=identity,
                    contracts=contracts,
                    expected_rows={table: 0 for table in contracts},
                ),
                {},
            )
            with self.assertRaisesRegex(RuntimeError, "identity or digest differs"):
                _load_checkpoint(
                    path,
                    identity={"package_id": "other"},
                    contracts=contracts,
                    expected_rows={table: 0 for table in contracts},
                )
            path.write_text(
                json.dumps(
                    _checkpoint_value(
                        identity,
                        {"dwd_listed_company": {"status": "verified"}},
                    )
                )
            )
            with self.assertRaisesRegex(RuntimeError, "table report shape differs"):
                _load_checkpoint(
                    path,
                    identity=identity,
                    contracts=contracts,
                    expected_rows={table: 0 for table in contracts},
                )

    def test_repository_model_resolves_all_keys_and_foreign_keys(self) -> None:
        contracts = load_semantic_contracts(ROOT / "config/pipelines/dwd-domain-model.v1.json")
        governed_contracts = load_semantic_contracts(
            ROOT / "config/pipelines/dwd-domain-model.v1.json",
            semantics_policy_path=(ROOT / "config/audits/dwd-financial-concept-semantics.v1.json"),
        )

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
        self.assertEqual(
            contracts["dwd_financial_concept"].knowledge_interval_identity,
            contracts["dwd_financial_concept"].business_key,
        )
        self.assertEqual(
            governed_contracts["dwd_financial_concept"].knowledge_interval_identity,
            ("financial_concept_id", "taxonomy_version"),
        )
        self.assertFalse(contracts["dwd_financial_concept"].require_closed_knowledge_intervals)
        self.assertTrue(
            governed_contracts["dwd_financial_concept"].require_closed_knowledge_intervals
        )

    def test_financial_concept_policy_is_validated_before_contract_loading(self) -> None:
        import json
        import tempfile

        source = ROOT / "config/audits/dwd-financial-concept-semantics.v1.json"
        policy = json.loads(source.read_text(encoding="utf-8"))
        policy["base_model_sha256"] = "sha256:" + "0" * 64
        with tempfile.TemporaryDirectory() as temporary:
            invalid = Path(temporary) / "invalid-policy.json"
            invalid.write_text(json.dumps(policy), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "policy binding is invalid"):
                load_semantic_contracts(
                    ROOT / "config/pipelines/dwd-domain-model.v1.json",
                    semantics_policy_path=invalid,
                )

    def test_financial_concept_policy_parse_and_digest_share_one_byte_snapshot(self) -> None:
        import hashlib

        policy_path = ROOT / "config/audits/dwd-financial-concept-semantics.v1.json"
        model_path = ROOT / "config/pipelines/dwd-domain-model.v1.json"
        policy_bytes = policy_path.read_bytes()
        original_read_bytes = Path.read_bytes
        policy_reads = 0

        def read_bytes_once(path: Path) -> bytes:
            nonlocal policy_reads
            if path == policy_path:
                policy_reads += 1
                return policy_bytes if policy_reads == 1 else b"{}"
            return original_read_bytes(path)

        with patch.object(Path, "read_bytes", read_bytes_once):
            policy = load_financial_concept_semantics_policy(
                policy_path,
                model_path=model_path,
            )

        self.assertEqual(policy_reads, 1)
        self.assertEqual(
            policy.sha256,
            "sha256:" + hashlib.sha256(policy_bytes).hexdigest(),
        )

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

    def test_financial_concept_audit_treats_effective_start_as_revised_state(self) -> None:
        import duckdb

        connection = duckdb.connect(":memory:")
        try:
            connection.execute(
                """
                CREATE TABLE concepts (
                  financial_concept_id VARCHAR,
                  taxonomy_version VARCHAR,
                  effective_from TIMESTAMPTZ,
                  effective_to TIMESTAMPTZ,
                  knowledge_from TIMESTAMPTZ,
                  knowledge_to TIMESTAMPTZ
                );
                INSERT INTO concepts VALUES
                  ('concept-1', 'v1', '2005-12-30 16:00:00+00', NULL,
                    '2026-07-31 00:00:00+00', '2026-08-01 00:00:00+00'),
                  ('concept-1', 'v1', '1998-12-30 16:00:00+00', NULL,
                    '2026-08-01 00:00:00+00', '2026-08-02 00:00:00+00'),
                  ('concept-1', 'v1', '1990-12-30 16:00:00+00', NULL,
                    '2026-08-02 00:00:00+00', NULL)
                """
            )
            contract = load_semantic_contracts(
                ROOT / "config/pipelines/dwd-domain-model.v1.json",
                semantics_policy_path=(
                    ROOT / "config/audits/dwd-financial-concept-semantics.v1.json"
                ),
            )["dwd_financial_concept"]

            self.assertEqual(
                _key_version_metrics(connection, "concepts", contract),
                {
                    "duplicate_row_keys": {"group_count": 0, "row_count": 0},
                    "business_key_version_groups": {
                        "group_count": 0,
                        "row_count": 0,
                    },
                    "knowledge_interval_overlap_rows": 0,
                    "knowledge_interval_boundary_violation_rows": 0,
                },
            )
            self.assertEqual(
                _interval_metrics(connection, "concepts", contract),
                {
                    "invalid_knowledge_interval_rows": 0,
                    "invalid_effective_from_effective_to_rows": 0,
                    "knowledge_interval_overlap_rows": 0,
                    "knowledge_interval_boundary_violation_rows": 0,
                    "effective_interval_overlap_rows": 0,
                },
            )
            connection.execute(
                """
                INSERT INTO concepts VALUES
                  ('concept-1', 'v1', '1980-12-30 16:00:00+00', NULL,
                    '2026-08-02 00:00:00+00', '2026-08-03 00:00:00+00')
                """
            )
            self.assertGreater(
                int(
                    _key_version_metrics(connection, "concepts", contract)[
                        "knowledge_interval_overlap_rows"
                    ]
                ),
                0,
            )
            self.assertGreater(
                _interval_metrics(connection, "concepts", contract)[
                    "knowledge_interval_overlap_rows"
                ],
                0,
            )
        finally:
            connection.close()

    def test_financial_concept_audit_rejects_knowledge_interval_gaps(self) -> None:
        import duckdb

        connection = duckdb.connect(":memory:")
        try:
            connection.execute(
                """
                CREATE TABLE concepts (
                  financial_concept_id VARCHAR,
                  taxonomy_version VARCHAR,
                  effective_from TIMESTAMPTZ,
                  effective_to TIMESTAMPTZ,
                  knowledge_from TIMESTAMPTZ,
                  knowledge_to TIMESTAMPTZ
                );
                INSERT INTO concepts VALUES
                  ('concept-1', 'v1', '2005-12-30 16:00:00+00', NULL,
                    '2026-07-31 00:00:00+00', '2026-07-31 12:00:00+00'),
                  ('concept-1', 'v1', '1998-12-30 16:00:00+00', NULL,
                    '2026-08-01 00:00:00+00', NULL)
                """
            )
            contract = load_semantic_contracts(
                ROOT / "config/pipelines/dwd-domain-model.v1.json",
                semantics_policy_path=(
                    ROOT / "config/audits/dwd-financial-concept-semantics.v1.json"
                ),
            )["dwd_financial_concept"]

            key_metrics = _key_version_metrics(connection, "concepts", contract)
            interval_metrics = _interval_metrics(connection, "concepts", contract)
            self.assertEqual(key_metrics["knowledge_interval_overlap_rows"], 0)
            self.assertEqual(
                key_metrics["knowledge_interval_boundary_violation_rows"],
                1,
            )
            self.assertEqual(interval_metrics["knowledge_interval_overlap_rows"], 0)
            self.assertEqual(
                interval_metrics["knowledge_interval_boundary_violation_rows"],
                1,
            )
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

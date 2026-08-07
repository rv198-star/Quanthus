import json
import tempfile
import unittest
from pathlib import Path

from quanthus.pipelines.dwd_domain_invariant_audit import (
    _checkpoint_value,
    _load_checkpoint,
    _manifest_table_names,
    _validated_memory_limit,
    invariant_predicates,
    write_domain_invariant_report,
)


class DwdDomainInvariantAuditTests(unittest.TestCase):
    def test_manifest_table_names_normalize_flat_payload_stems(self) -> None:
        self.assertEqual(
            _manifest_table_names(
                {
                    "files": [
                        {
                            "path": "payload/tradability_state/"
                            "dwd_price_limit_regime.parquet"
                        },
                        {
                            "path": "payload/security_market_observation/"
                            "dwd_security_bar/interval_size=1d/part.parquet"
                        },
                    ]
                }
            ),
            ["dwd_price_limit_regime", "dwd_security_bar"],
        )

    def test_resource_contract_and_checkpoint_identity(self) -> None:
        self.assertEqual(_validated_memory_limit("2gb"), "2GB")
        with self.assertRaisesRegex(RuntimeError, "memory limit"):
            _validated_memory_limit("2GB; DROP TABLE x")
        identity = {"package_id": "fixture", "memory_limit": "2GB", "threads": 1}
        tables = {"dwd_financial_fact": {"status": "verified"}}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "checkpoint.json"
            path.write_text(json.dumps(_checkpoint_value(identity, tables)))
            self.assertEqual(
                _load_checkpoint(
                    path,
                    identity=identity,
                    allowed_tables={"dwd_financial_fact"},
                ),
                tables,
            )
            with self.assertRaisesRegex(RuntimeError, "identity"):
                _load_checkpoint(
                    path,
                    identity={**identity, "memory_limit": "1GB"},
                    allowed_tables={"dwd_financial_fact"},
                )

    def test_predicates_cover_cross_field_model_constraints(self) -> None:
        predicates = invariant_predicates()

        self.assertIn("invalid_subject_cardinality_rows", predicates["dwd_disclosure_subject"])
        self.assertIn("nil_with_value_rows", predicates["dwd_financial_fact"])
        self.assertIn("unknown_gap_reason_rows", predicates["dwd_observation_gap"])
        self.assertIn("permission_on_nontradable_rows", predicates["dwd_tradability_state"])
        self.assertIn("invalid_open_close_order_rows", predicates["dwd_trading_calendar"])
        self.assertEqual(len(predicates), 11)

    def test_predicates_detect_fixture_violations(self) -> None:
        import duckdb

        connection = duckdb.connect(":memory:")
        try:
            connection.execute(
                """
                CREATE TABLE facts (
                  is_nil BOOLEAN,
                  value_decimal DECIMAL(38, 10),
                  value_text VARCHAR,
                  dimensions_json VARCHAR
                );
                INSERT INTO facts VALUES
                  (true, 1, NULL, '{}'),
                  (false, NULL, NULL, '{}'),
                  (false, 1, 'one', '{bad');
                """
            )
            rules = invariant_predicates()["dwd_financial_fact"]
            values = connection.execute(
                "SELECT "
                + ", ".join(f"count(*) FILTER (WHERE {predicate})" for predicate in rules.values())
                + " FROM facts"
            ).fetchone()
            self.assertEqual(values, (1, 1, 1, 1))
        finally:
            connection.close()

    def test_report_cannot_be_written_inside_sealed_package(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(RuntimeError, "outside sealed package"):
                write_domain_invariant_report(
                    root / "audit.json", {"status": "fixture"}, sealed_roots=(root,)
                )


if __name__ == "__main__":
    unittest.main()

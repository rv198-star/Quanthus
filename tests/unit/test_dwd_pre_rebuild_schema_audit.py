import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from quanthus.pipelines.dwd_pre_rebuild_audit import (
    ExpectedField,
    TableContract,
    _part_schema_classification,
    _partition_mismatches,
    _partition_values,
    _schema_mismatches,
    load_table_contracts,
    write_phase_b_report,
)
from quanthus.pipelines.securities_warehouse.ownership_capital import (
    ownership_capital_schemas,
)
from quanthus.pipelines.securities_warehouse.quality_governance import (
    _QUALITY_PARTITION_COLUMN,
    quality_governance_schema,
)
from quanthus.pipelines.securities_warehouse.tradability_state import (
    _PARTITIONED_TABLES,
)
from quanthus.pipelines.securities_warehouse.tradability_state import (
    _schemas as tradability_schemas,
)

ROOT = Path(__file__).resolve().parents[2]


class DwdPreRebuildSchemaAuditTests(unittest.TestCase):
    def test_report_cannot_be_written_inside_sealed_package(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(RuntimeError, "outside sealed package"):
                write_phase_b_report(
                    root / "audit.json",
                    {"status": "fixture"},
                    sealed_roots=(root,),
                )

    def test_flat_and_partitioned_payload_paths_resolve(self) -> None:
        self.assertEqual(
            _partition_values("payload/listed_company/dwd_security.parquet", "dwd_security"),
            {},
        )
        self.assertEqual(
            _partition_values(
                "payload/market/dwd_security_bar/interval_size=1d/"
                "observation_start_year=2020/part-00000.parquet",
                "dwd_security_bar",
            ),
            {"interval_size": "1d", "observation_start_year": "2020"},
        )

    def test_repository_contract_resolves_all_33_extended_schemas(self) -> None:
        contracts = load_table_contracts(
            ROOT / "config/pipelines/dwd-domain-model.v1.json",
            ROOT / "config/pipelines/dwd-domain-model.v1.1-extension.json",
        )

        self.assertEqual(len(contracts), 33)
        self.assertIn(
            "parser_version",
            {field.name for field in contracts["dwd_shareholding_change"].fields},
        )
        self.assertEqual(
            contracts["dwd_security_bar"].partition_by,
            ("interval_size", "observation_start_year", "observation_start_month"),
        )

    def test_failed_candidate_writers_match_frozen_schema_and_partition_contracts(self) -> None:
        contracts = load_table_contracts(
            ROOT / "config/pipelines/dwd-domain-model.v1.json",
            ROOT / "config/pipelines/dwd-domain-model.v1.1-extension.json",
        )
        schemas = {
            **tradability_schemas(),
            **ownership_capital_schemas(),
            "dwd_quality_exception": quality_governance_schema(),
        }
        failed_tables = {
            "dwd_observation_gap",
            "dwd_quality_exception",
            "dwd_share_capital_observation",
            "dwd_shareholder_count_observation",
            "dwd_shareholding_change",
            "dwd_shareholding_observation",
            "dwd_tradability_state",
        }

        for table in failed_tables:
            self.assertEqual(
                schemas[table].names,
                [field.name for field in contracts[table].fields],
                table,
            )
        self.assertEqual(
            _PARTITIONED_TABLES["dwd_tradability_state"],
            contracts["dwd_tradability_state"].partition_by,
        )
        self.assertEqual(
            _PARTITIONED_TABLES["dwd_observation_gap"],
            contracts["dwd_observation_gap"].partition_by,
        )
        self.assertEqual(
            (_QUALITY_PARTITION_COLUMN,),
            contracts["dwd_quality_exception"].partition_by,
        )

    def test_schema_drift_reports_type_nullable_and_order(self) -> None:
        import pyarrow as pa

        contract = TableContract(
            name="fixture",
            partition_by=(),
            fields=(
                ExpectedField("id", "string", False),
                ExpectedField("value", "int64", True),
            ),
        )
        schema = pa.schema(
            [
                pa.field("value", pa.int32(), nullable=False),
                pa.field("id", pa.string(), nullable=False),
            ]
        )

        self.assertEqual(
            _schema_mismatches(schema, contract),
            [
                "field_order_mismatch",
                "type_mismatch:value:int64:int32",
                "nullable_mismatch:value:True:False",
            ],
        )

    def test_zero_row_zero_column_part_is_classified_not_schema_drift(self) -> None:
        import pyarrow as pa

        contract = TableContract(
            name="fixture",
            partition_by=(),
            fields=(ExpectedField("id", "string", False),),
        )

        self.assertEqual(
            _part_schema_classification(pa.schema([]), contract, row_count=0),
            ([], "zero_row_zero_column"),
        )
        self.assertEqual(
            _part_schema_classification(pa.schema([]), contract, row_count=1),
            (["missing_fields:id"], None),
        )

    def test_partition_values_are_checked_against_statistics(self) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        contract = TableContract(
            name="fixture",
            partition_by=("interval_size", "observed_at_year"),
            fields=(
                ExpectedField("interval_size", "string", False),
                ExpectedField("observed_at", "timestamp[us, tz=UTC]", False),
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "part.parquet"
            table = pa.table(
                {
                    "interval_size": ["1d"],
                    "observed_at": pa.array(
                        [datetime(2020, 12, 31, 16, 30, tzinfo=timezone.utc)],
                        type=pa.timestamp("us", tz="UTC"),
                    ),
                },
                schema=pa.schema(
                    [
                        pa.field("interval_size", pa.string(), nullable=False),
                        pa.field(
                            "observed_at",
                            pa.timestamp("us", tz="UTC"),
                            nullable=False,
                        ),
                    ]
                ),
            )
            pq.write_table(table, path)
            parquet = pq.ParquetFile(path)

            self.assertEqual(
                _partition_mismatches(
                    parquet,
                    contract,
                    {"interval_size": "1d", "observed_at_year": "2020"},
                    row_count=1,
                ),
                [],
            )
            self.assertEqual(
                _partition_mismatches(
                    parquet,
                    contract,
                    {"interval_size": "5m", "observed_at_year": "2021"},
                    row_count=1,
                ),
                [
                    "partition_value_mismatch:interval_size",
                    "partition_value_mismatch:observed_at_year",
                ],
            )


if __name__ == "__main__":
    unittest.main()

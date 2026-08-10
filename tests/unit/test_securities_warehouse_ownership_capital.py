from __future__ import annotations

import hashlib
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import pyarrow as pa
import pyarrow.parquet as pq

from quanthus.pipelines.securities_warehouse.ownership_capital import (
    OwnershipCapitalBuildError,
    _exclude_out_of_range_holdings,
    _file_source,
    _partition_for_row,
    _single_record,
    _write_ownership_rows,
    materialize_shareholder_count_row,
    ownership_capital_schemas,
    parse_f10_capital_structure,
    parse_f10_shareholding_tables,
)


class OwnershipCapitalTests(unittest.TestCase):
    def test_structured_holder_count_preserves_raw_values(self) -> None:
        row = materialize_shareholder_count_row(
            {
                "code": "000001",
                "end_date": "2026-03-31",
                "shareholder_count": 457610.0,
                "avg_hold_num": 42406.25,
                "knowledge_cutoff": "2026-04-24",
                "fetched_at": "2026-07-24T07:42:00Z",
                "source": "eastmoney",
            },
            company_id="company-1",
            security_id="security-1",
            source_sha256="sha256:holder-count",
        )

        self.assertEqual(row["holder_count"], 457610)
        self.assertEqual(row["raw_holder_count"], "457610")
        self.assertEqual(row["raw_unit"], "HOLDER_ACCOUNT;SHARE_PER_HOLDER")

    def test_parent_knowledge_controls_shareholder_count_visibility(self) -> None:
        parent_knowledge = datetime(2026, 8, 1, tzinfo=timezone.utc)
        row = materialize_shareholder_count_row(
            {
                "code": "000001",
                "end_date": "2026-03-31",
                "shareholder_count": 10,
                "knowledge_cutoff": "2026-04-24",
            },
            company_id="company-1",
            security_id="security-1",
            source_sha256="sha256:holder-count",
            parent_knowledge_from=parent_knowledge,
        )

        self.assertEqual(row["knowledge_from"], parent_knowledge)

    def test_out_of_range_holding_percentage_is_excluded_exactly(self) -> None:
        accepted, excluded = _exclude_out_of_range_holdings(
            [
                {"holder_rank": 1, "holding_percentage": 2.94},
                {"holder_rank": 2, "holding_percentage": 168.87},
                {"holder_rank": 3, "holding_percentage": None},
            ]
        )

        self.assertEqual([row["holder_rank"] for row in accepted], [1, 3])
        self.assertEqual(excluded, 1)

    def test_v11_schemas_cover_four_owned_objects(self) -> None:
        schemas = ownership_capital_schemas()

        self.assertEqual(
            set(schemas),
            {
                "dwd_share_capital_observation",
                "dwd_shareholder_count_observation",
                "dwd_shareholding_change",
                "dwd_shareholding_observation",
            },
        )
        self.assertIn("holder_match_key", schemas["dwd_shareholding_observation"].names)
        self.assertIn("parser_version", schemas["dwd_shareholding_change"].names)

    def test_partition_paths_match_the_frozen_model(self) -> None:
        self.assertEqual(
            _partition_for_row(
                "dwd_share_capital_observation",
                {"observation_start": datetime(2021, 7, 9, tzinfo=timezone.utc)},
            ),
            (("observation_start_year", "2021"),),
        )
        self.assertEqual(
            _partition_for_row(
                "dwd_shareholder_count_observation",
                {"period_end": date(2022, 3, 31)},
            ),
            (("period_end_year", "2022"),),
        )
        self.assertEqual(
            _partition_for_row(
                "dwd_shareholding_change",
                {"effective_at": datetime(2023, 4, 5, tzinfo=timezone.utc)},
            ),
            (("effective_at_year", "2023"), ("effective_at_month", "04")),
        )
        self.assertEqual(
            _partition_for_row(
                "dwd_shareholding_observation",
                {"observation_start": datetime(2024, 5, 6, tzinfo=timezone.utc)},
            ),
            (("observation_start_year", "2024"),),
        )

    def test_writer_materializes_model_partition_names_without_schema_columns(self) -> None:
        cases = (
            (
                "dwd_share_capital_observation",
                {"observation_start": datetime(2021, 7, 9, tzinfo=timezone.utc)},
                pa.schema([pa.field("observation_start", pa.timestamp("us", tz="UTC"))]),
                "observation_start_year=2021",
            ),
            (
                "dwd_shareholder_count_observation",
                {"period_end": date(2022, 3, 31)},
                pa.schema([pa.field("period_end", pa.date32())]),
                "period_end_year=2022",
            ),
            (
                "dwd_shareholding_change",
                {"effective_at": datetime(2023, 4, 5, tzinfo=timezone.utc)},
                pa.schema([pa.field("effective_at", pa.timestamp("us", tz="UTC"))]),
                "effective_at_year=2023/effective_at_month=04",
            ),
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for table, row, schema, partition in cases:
                relative = _write_ownership_rows(root, table, [row], schema, "audit")
                expected = f"payload/ownership_capital/{table}/{partition}/part-audit.parquet"
                self.assertEqual(relative, [expected])
                self.assertTrue(pq.ParquetFile(root / expected).schema_arrow.equals(schema))

    def test_capital_matrix_expands_dates_and_units(self) -> None:
        rows = parse_f10_capital_structure(
            """
【1.股本结构】
｜单位(股)｜2026-03-09｜2024-12-31｜
｜总股本｜194.0592亿｜31.7545万｜
【2.股本变化】
"""
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["share_quantity"], 19_405_920_000)
        self.assertEqual(rows[0]["raw_unit"], "亿股")
        self.assertEqual(rows[1]["share_quantity"], 317_545)

    def test_holder_parser_keeps_table_kind_rank_and_continuation(self) -> None:
        rows = parse_f10_shareholding_tables(
            """
截至日期：2026-03-31 十大流通股东情况 A股户数:45万
｜股东名称｜持股数(股)｜占流通股比(%)｜股东性质｜增减情况(股)｜
｜中国工商银行－华泰柏瑞沪深300交易型开放式｜7808.7233万｜0.40 占A股｜基金｜未变｜
｜指数证券投资基金｜｜｜｜｜
｜香港中央结算有限公司｜5.7077亿｜2.94 占A股｜其他｜未变｜
└────┘
截至日期：2026-03-31 十大股东情况 A股户数:45万
｜股东名称｜持股数(股)｜占总股本比(%)｜股份性质｜增减情况(股)｜
｜香港中央结算有限公司｜5.7077亿｜2.94｜无限售A股｜未变｜
└────┘
"""
        )

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["holder_rank"], 1)
        self.assertTrue(rows[0]["shareholder_source_name"].endswith("指数证券投资基金"))
        self.assertEqual(rows[0]["table_kind"], "TOP_FLOATING_SHAREHOLDERS")
        self.assertEqual(rows[0]["holder_category"], "基金")
        self.assertEqual(rows[2]["table_kind"], "TOP_SHAREHOLDERS")
        self.assertEqual(rows[2]["share_nature"], "无限售A股")

    def test_single_record_rejects_ambiguous_authority(self) -> None:
        records = [
            {
                "mapping_identity": "shareholder_count",
                "record_type": "file",
                "disposition": "modeled",
                "role": "source_evidence",
            },
            {
                "mapping_identity": "shareholder_count",
                "record_type": "file",
                "disposition": "modeled",
                "role": "source_evidence",
            },
        ]

        with self.assertRaisesRegex(OwnershipCapitalBuildError, "found 2"):
            _single_record(
                records,
                identity="shareholder_count",
                record_type="file",
                disposition="modeled",
                role="source_evidence",
            )

    def test_file_source_binds_size_and_digest(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "payload/canonical/ref_shareholder_count.parquet"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"fixture")
            digest = "sha256:" + hashlib.sha256(b"fixture").hexdigest()
            records = [
                {
                    "mapping_identity": "shareholder_count",
                    "record_type": "file",
                    "disposition": "modeled",
                    "role": "source_evidence",
                    "path": path.relative_to(root).as_posix(),
                    "sha256": digest,
                    "size_bytes": 7,
                    "row_count": 3,
                }
            ]

            source = _file_source(
                records,
                root,
                source_id="shareholder_count",
                identity="shareholder_count",
                role="source_evidence",
            )

            self.assertEqual(source.row_count, 3)
            self.assertEqual(source.sha256, digest)


if __name__ == "__main__":
    unittest.main()

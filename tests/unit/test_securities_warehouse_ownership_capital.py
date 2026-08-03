from __future__ import annotations

import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from quanthus.pipelines.securities_warehouse.ownership_capital import (
    OwnershipCapitalBuildError,
    _file_source,
    _single_record,
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

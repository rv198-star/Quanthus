from __future__ import annotations

import gzip
import hashlib
import json
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

import pyarrow as pa
import pyarrow.parquet as pq

from quanthus.pipelines.securities_warehouse.financial_reporting import (
    FinancialCompanyIdentity,
    FinancialCompanyResolver,
    FinancialReportingBuildError,
    FinancialStatementSource,
    audit_financial_statement_sources,
    classify_statement_rows,
    conservative_provider_timestamp,
    decode_financial_envelope,
    expand_financial_envelope,
    financial_reporting_schemas,
    iter_statement_reports,
    select_financial_statement_sources,
    write_financial_reporting_source_audit,
)


def _envelope(payload: dict[str, object]) -> dict[str, object]:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    packed = gzip.compress(raw, mtime=0)
    return {
        "raw_payload_gzip": repr(packed).encode("ascii"),
        "raw_payload_bytes": len(raw),
        "raw_payload_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
    }


class FinancialEnvelopeTests(unittest.TestCase):
    def test_decodes_byte_literal_gzip_and_hash_checks_payload(self) -> None:
        row = _envelope(
            {
                "result": {
                    "data": {
                        "report_list": {
                            "20251231": {"data": []},
                            "20260331": {"data": []},
                        }
                    }
                }
            }
        )
        value = decode_financial_envelope(row)
        self.assertEqual(list(value.report_list), ["20251231", "20260331"])
        self.assertGreater(len(value.raw_payload), 0)

    def test_rejects_tampered_payload_digest(self) -> None:
        row = _envelope({"result": {"data": {"report_list": {}}}})
        row["raw_payload_sha256"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(FinancialReportingBuildError, "SHA-256"):
            decode_financial_envelope(row)

    def test_classifies_every_provider_item(self) -> None:
        report = {
            "data": [
                {"item_field": "", "item_display_type": 1, "item_value": ""},
                {"item_field": "CASH", "item_display_type": 2, "item_value": "12.50"},
                {"item_field": "DEBT", "item_display_type": 2, "item_value": ""},
                "malformed",
            ]
        }
        self.assertEqual(classify_statement_rows(report).heading_rows, 1)
        self.assertEqual(classify_statement_rows(report).valid_facts, 1)
        self.assertEqual(classify_statement_rows(report).null_placeholders, 1)
        self.assertEqual(classify_statement_rows(report).invalid_rows, 1)

    def test_uses_conservative_end_of_day_for_date_only_availability(self) -> None:
        self.assertEqual(
            conservative_provider_timestamp("2026-08-02"),
            datetime(2026, 8, 2, 15, 59, 59, 999999, tzinfo=timezone.utc),
        )

    def test_iterates_reports_in_period_order(self) -> None:
        row = _envelope(
            {
                "result": {
                    "data": {
                        "report_list": {
                            "20260331": {"data": [{"item_field": "A", "item_value": "1"}]},
                            "20251231": {"data": [{"item_field": "B", "item_value": "2"}]},
                        }
                    }
                }
            }
        )
        reports = iter_statement_reports(decode_financial_envelope(row))
        periods = [period for period, _report, _accounting in reports]
        self.assertEqual(periods, ["20251231", "20260331"])

    def test_stream_audit_keeps_matching_parent_overlap_and_hotfix_fill(self) -> None:
        parent_envelope = _envelope(
            {
                "result": {
                    "data": {
                        "report_list": {
                            "20251231": {
                                "data": [
                                    {"item_display_type": 1, "item_field": "", "item_value": ""},
                                    {"item_field": "CASH", "item_value": "12.5"},
                                    {"item_field": "DEBT", "item_value": ""},
                                ]
                            }
                        }
                    }
                }
            }
        )
        fill_envelope = _envelope(
            {
                "result": {
                    "data": {
                        "report_list": {
                            "20251231": {
                                "data": [{"item_field": "PROFIT", "item_value": "4"}]
                            }
                        }
                    }
                }
            }
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = root / "parent.parquet"
            hotfix = root / "hotfix.parquet"
            parent_row = {
                **parent_envelope,
                "security_id": "000001",
                "stock_code": "000001",
                "statement_type": "balance_sheet",
            }
            pq.write_table(pa.Table.from_pylist([parent_row]), parent)
            pq.write_table(
                pa.Table.from_pylist(
                    [
                        parent_row,
                        {
                            **fill_envelope,
                            "security_id": "000002",
                            "stock_code": "000002",
                            "statement_type": "income_statement",
                        },
                    ]
                ),
                hotfix,
            )
            sources = (
                FinancialStatementSource(
                    "financial_statements",
                    parent,
                    "payload/full.parquet",
                    "sha256:parent",
                    "parent-r1",
                    "parent",
                ),
                FinancialStatementSource(
                    "fundamentals_financial_statements_parquet",
                    hotfix,
                    "payload/hotfix.parquet",
                    "sha256:hotfix",
                    "hotfix-r1",
                    "hotfix",
                ),
            )
            audit = audit_financial_statement_sources(sources, batch_size=1)
            self.assertEqual(audit.report["overlay"]["parent_overlap_rows"], 1)
            self.assertEqual(audit.report["overlay"]["hotfix_fill_rows"], 1)
            self.assertEqual(audit.report["overlay"]["selected_envelope_rows"], 2)
            self.assertEqual(
                audit.report["candidate_size_calibration"]["dwd_financial_fact_upper_bound"],
                3,
            )
            output = write_financial_reporting_source_audit(root / "audit.json", audit)
            self.assertEqual(output.read_text(encoding="ascii"), output.read_text(encoding="ascii"))

    def test_stream_audit_rejects_different_parent_hotfix_overlap(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = root / "parent.parquet"
            hotfix = root / "hotfix.parquet"
            base = {"security_id": "000001", "stock_code": "000001", "statement_type": "cash_flow"}
            pq.write_table(
                pa.Table.from_pylist(
                    [{**base, **_envelope({"result": {"data": {"report_list": {}}}})}]
                ),
                parent,
            )
            pq.write_table(
                pa.Table.from_pylist(
                    [
                        {
                            **base,
                            **_envelope(
                                {"result": {"data": {"report_list": {"20251231": {"data": []}}}}}
                            ),
                        }
                    ]
                ),
                hotfix,
            )
            sources = (
                FinancialStatementSource("parent", parent, "parent", "sha256:parent", "", "parent"),
                FinancialStatementSource("hotfix", hotfix, "hotfix", "sha256:hotfix", "", "hotfix"),
            )
            with self.assertRaisesRegex(FinancialReportingBuildError, "overlap has different"):
                audit_financial_statement_sources(sources)

    def test_selects_exact_parent_and_fill_only_hotfix_from_ledger(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            full = root / "payload" / "source-evidence" / "financial_statements.parquet"
            hotfix = root / "payload" / "fundamentals" / "financial_statements.parquet"
            full.parent.mkdir(parents=True)
            hotfix.parent.mkdir(parents=True)
            full.write_bytes(b"full")
            hotfix.write_bytes(b"hotfix")
            records = [
                {
                    "record_type": "file",
                    "disposition": "modeled",
                    "mapping_identity": "financial_statements",
                    "role": "retained_immutable_source_gap_fill",
                    "path": full.relative_to(root).as_posix(),
                    "sha256": "sha256:" + hashlib.sha256(b"full").hexdigest(),
                },
                {
                    "record_type": "file",
                    "disposition": "modeled",
                    "mapping_identity": "fundamentals_financial_statements_parquet",
                    "role": "provider_source_evidence",
                    "path": hotfix.relative_to(root).as_posix(),
                    "sha256": "sha256:" + hashlib.sha256(b"hotfix").hexdigest(),
                },
            ]
            parent, selected_hotfix = select_financial_statement_sources(records, root)
            self.assertEqual(parent.precedence, "parent")
            self.assertEqual(selected_hotfix.precedence, "hotfix")
            self.assertEqual(parent.path, full)
            self.assertEqual(selected_hotfix.path, hotfix)

    def test_expands_lossless_envelope_into_three_typed_dwd_row_sets(self) -> None:
        row = {
            **_envelope(
                {
                    "result": {
                        "data": {
                            "report_list": {
                                "20251231": {
                                    "rType": "consolidated",
                                    "rCurrency": "CNY",
                                    "data_source": "provider-native",
                                    "is_audit": "audited",
                                    "publish_date": "2026-04-30",
                                    "update_time": 123,
                                    "data": [
                                        {"item_display_type": 1, "item_field": ""},
                                        {
                                            "item_field": "CASH",
                                            "item_title": "Cash",
                                            "item_value": "12.500000",
                                            "item_precision": "f2",
                                            "item_group_no": 1,
                                        },
                                        {
                                            "item_field": "NOTE",
                                            "item_title": "Note",
                                            "item_value": "not-numeric",
                                        },
                                        {
                                            "item_field": "DEBT",
                                            "item_title": "Debt",
                                            "item_value": "",
                                        },
                                    ],
                                }
                            }
                        }
                    }
                }
            ),
            "security_id": "000001",
            "stock_code": "000001",
            "statement_type": "balance_sheet",
            "source": "sina.CompanyFinanceService.getFinanceReport2022",
            "source_revision": "provider-v1",
            "acquired_at": "2026-07-31T23:12:41+08:00",
        }
        source = FinancialStatementSource(
            "financial_statements",
            Path("/fixture/full.parquet"),
            "payload/full.parquet",
            "sha256:" + "a" * 64,
            "",
            "parent",
        )
        resolver = FinancialCompanyResolver(
            [
                FinancialCompanyIdentity(
                    stock_code="000001",
                    security_id="security-1",
                    company_id="company-1",
                    effective_from=datetime(1991, 1, 1, tzinfo=timezone.utc),
                    effective_to=None,
                )
            ]
        )
        expanded = expand_financial_envelope(row, source=source, company_resolver=resolver)
        self.assertEqual(len(expanded.reports), 1)
        self.assertEqual(len(expanded.concepts), 3)
        self.assertEqual(len(expanded.facts), 3)
        self.assertEqual(expanded.reports[0]["company_id"], "company-1")
        self.assertEqual(expanded.facts[0]["value_decimal"], Decimal("12.500000"))
        self.assertEqual(expanded.facts[0]["value_text"], "12.500000")
        self.assertIsNone(expanded.facts[1]["value_decimal"])
        self.assertTrue(expanded.facts[2]["is_nil"])
        schemas = financial_reporting_schemas()
        for table, rows in (
            ("dwd_financial_report", expanded.reports),
            ("dwd_financial_concept", expanded.concepts),
            ("dwd_financial_fact", expanded.facts),
        ):
            self.assertTrue(
                pa.Table.from_pylist(list(rows), schema=schemas[table])
                .schema.equals(schemas[table], check_metadata=True)
            )


if __name__ == "__main__":
    unittest.main()

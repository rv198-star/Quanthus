from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
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
    FinancialReportingSource,
    FinancialReportingSourceInventory,
    FinancialStatementSource,
    _audit_financial_candidate,
    _batch_rows,
    _finalize_concepts,
    _write_batch,
    audit_financial_native_sources,
    audit_financial_statement_sources,
    build_financial_reporting,
    classify_statement_rows,
    conservative_provider_timestamp,
    decode_financial_envelope,
    expand_financial_envelope,
    expand_financial_native_row,
    financial_company_resolver_from_listed_company_rows,
    financial_reporting_schemas,
    iter_statement_reports,
    select_financial_reporting_sources,
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
    def test_full_build_stays_disabled_until_native_sources_are_integrated(self) -> None:
        with self.assertRaisesRegex(FinancialReportingBuildError, "full build is disabled"):
            build_financial_reporting(
                ods_package=Path("/not-read/ods"),
                input_plan=Path("/not-read/plan"),
                model_path=Path("/not-read/model"),
                listed_company_candidate=Path("/not-read/company"),
                source_audit_path=Path("/not-read/audit"),
                output=Path("/not-written/candidate"),
                run_id="fixture",
            )

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
                {"item_field": "", "item_display_type": 2, "item_value": "12.50"},
                "malformed",
            ]
        }
        self.assertEqual(classify_statement_rows(report).heading_rows, 1)
        self.assertEqual(classify_statement_rows(report).valid_facts, 1)
        self.assertEqual(classify_statement_rows(report).null_placeholders, 1)
        self.assertEqual(classify_statement_rows(report).invalid_rows, 2)

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
                            "20251231": {"data": [{"item_field": "PROFIT", "item_value": "4"}]}
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

    def test_selects_all_non_empty_financial_sources_without_identity_collapse(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)

            def write(relative: str, payload: bytes) -> tuple[str, int]:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
                return "sha256:" + hashlib.sha256(payload).hexdigest(), len(payload)

            files = {
                "payload/full/financial_statements.parquet": b"statements-full",
                "payload/hotfix/financial_statements.parquet": b"statements-hotfix",
                "payload/canonical/financial_quarterly.parquet": b"quarterly-canonical",
                "payload/full/financial_quarterly.parquet": b"quarterly-full",
                "payload/hotfix/financial_quarterly.parquet": b"quarterly-hotfix",
                "payload/canonical/info_archive.db": b"archive",
                "payload/daily/financial_disclosures_delta.parquet": b"empty",
            }
            identities = {relative: write(relative, payload) for relative, payload in files.items()}

            def file_record(
                *,
                identity: str,
                relative: str,
                role: str | None,
                rows: int,
                targets: list[str] | None = None,
            ) -> dict[str, object]:
                sha256, size = identities[relative]
                record: dict[str, object] = {
                    "record_type": "file",
                    "disposition": "modeled",
                    "mapping_identity": identity,
                    "path": relative,
                    "sha256": sha256,
                    "size_bytes": size,
                    "row_count": rows,
                    "target_tables": targets
                    or [
                        "dwd_financial_report",
                        "dwd_financial_concept",
                        "dwd_financial_fact",
                    ],
                }
                if role is not None:
                    record["role"] = role
                return record

            records = [
                file_record(
                    identity="financial_statements",
                    relative="payload/full/financial_statements.parquet",
                    role="retained_immutable_source_gap_fill",
                    rows=10,
                ),
                file_record(
                    identity="fundamentals_financial_statements_parquet",
                    relative="payload/hotfix/financial_statements.parquet",
                    role="provider_source_evidence",
                    rows=2,
                ),
                file_record(
                    identity="financial_quarterly",
                    relative="payload/canonical/financial_quarterly.parquet",
                    role="source_evidence",
                    rows=8,
                ),
                file_record(
                    identity="financial_quarterly",
                    relative="payload/full/financial_quarterly.parquet",
                    role="retained_immutable_source_gap_fill",
                    rows=9,
                ),
                file_record(
                    identity="fundamentals_financial_quarterly_parquet",
                    relative="payload/hotfix/financial_quarterly.parquet",
                    role="provider_source_evidence",
                    rows=3,
                ),
                file_record(
                    identity="announcement_archive",
                    relative="payload/canonical/info_archive.db",
                    role=None,
                    rows=20,
                    targets=["dwd_disclosure"],
                ),
                {
                    "record_type": "container_member",
                    "disposition": "modeled",
                    "mapping_identity": "announcement_archive|sqlite_table|fin_abstract",
                    "container_source_id": "announcement_archive",
                    "container_path": "payload/canonical/info_archive.db",
                    "path": "payload/canonical/info_archive.db#table=fin_abstract",
                    "member": "fin_abstract",
                    "row_count": 20,
                    "target_tables": [
                        "dwd_financial_report",
                        "dwd_financial_concept",
                        "dwd_financial_fact",
                    ],
                },
                file_record(
                    identity="financial_disclosures_delta",
                    relative="payload/daily/financial_disclosures_delta.parquet",
                    role=None,
                    rows=0,
                ),
            ]

            inventory = select_financial_reporting_sources(records, root)

            self.assertEqual(
                [source.source_id for source in inventory.sources],
                [
                    "weekly_full_financial_statements",
                    "hotfix_financial_statements",
                    "canonical_financial_quarterly",
                    "weekly_full_financial_quarterly",
                    "hotfix_financial_quarterly",
                    "announcement_archive_fin_abstract",
                ],
            )
            self.assertEqual(inventory.sources[-1].member, "fin_abstract")
            self.assertEqual(
                inventory.empty_modeled_sources,
                ("payload/daily/financial_disclosures_delta.parquet",),
            )

            extra_path = "payload/unknown/non_empty.parquet"
            identities[extra_path] = write(extra_path, b"unknown")
            records.append(
                file_record(
                    identity="unknown_financial_source",
                    relative=extra_path,
                    role=None,
                    rows=1,
                )
            )
            with self.assertRaisesRegex(
                FinancialReportingBuildError, "unclassified non-empty financial source"
            ):
                select_financial_reporting_sources(records, root)

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
                pa.Table.from_pylist(list(rows), schema=schemas[table]).schema.equals(
                    schemas[table], check_metadata=True
                )
            )

    def test_rejects_non_heading_item_without_concept_code(self) -> None:
        row = {
            **_envelope(
                {
                    "result": {
                        "data": {
                            "report_list": {
                                "20251231": {
                                    "data": [
                                        {
                                            "item_display_type": 2,
                                            "item_field": "",
                                            "item_value": "12.5",
                                        }
                                    ]
                                }
                            }
                        }
                    }
                }
            ),
            "security_id": "000001",
            "stock_code": "000001",
            "statement_type": "balance_sheet",
            "source": "fixture.finance",
            "source_revision": "fixture-v1",
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
        with self.assertRaisesRegex(FinancialReportingBuildError, "no item_field"):
            expand_financial_envelope(row, source=source, company_resolver=resolver)

    def test_expands_source_isolated_quarterly_and_abstract_rows(self) -> None:
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
        cutoff = datetime(2026, 8, 1, tzinfo=timezone.utc)
        canonical_source = FinancialReportingSource(
            source_id="canonical_financial_quarterly",
            source_kind="quarterly_projection",
            ledger_identity="financial_quarterly",
            path=Path("/fixture/canonical.parquet"),
            relative_path="payload/canonical/financial_quarterly.parquet",
            source_sha256="sha256:" + "a" * 64,
            source_revision="ods-base-202607-v1.2",
            precedence="independent",
        )
        canonical = expand_financial_native_row(
            {
                "stock_code": "000001",
                "report_date": datetime(2025, 12, 31),
                "publish_deadline": datetime(2026, 4, 30),
                "roe": 8.5,
                "revenue": 100.0,
                "net_profit": 20.0,
                "revenue_growth_yoy": None,
                "profit_growth_yoy": 5.0,
            },
            source=canonical_source,
            company_resolver=resolver,
            knowledge_cutoff=cutoff,
        )
        self.assertEqual(len(canonical.reports), 1)
        self.assertEqual(len(canonical.concepts), 5)
        self.assertEqual(len(canonical.facts), 5)
        self.assertEqual(
            {row["taxonomy_namespace"] for row in canonical.concepts},
            {"legacy.ods.financial_quarterly"},
        )
        units = {
            concept["concept_code"]: fact["unit_code"]
            for concept, fact in zip(canonical.concepts, canonical.facts, strict=True)
        }
        self.assertEqual(units["revenue"], "CNY")
        self.assertEqual(units["roe"], "PERCENT")
        self.assertTrue(
            next(
                fact["is_nil"]
                for concept, fact in zip(canonical.concepts, canonical.facts, strict=True)
                if concept["concept_code"] == "revenue_growth_yoy"
            )
        )

        sina_source = FinancialReportingSource(
            source_id="weekly_full_financial_quarterly",
            source_kind="quarterly_projection",
            ledger_identity="financial_quarterly",
            path=Path("/fixture/full.parquet"),
            relative_path="payload/full/financial_quarterly.parquet",
            source_sha256="sha256:" + "b" * 64,
            source_revision="quanthus-fundamentals-provider-v1",
            precedence="parent",
        )
        sina = expand_financial_native_row(
            {
                "security_id": "000001",
                "stock_code": "000001",
                "report_date": "20251231",
                "publish_date": "20260430",
                "revenue": "100.000000",
                "net_profit": "21.000000",
                "parent_net_profit": "20.000000",
                "currency": "CNY",
                "is_audit": "yes",
                "source": "sina.full_income_statement_projection",
                "source_revision": "provider-v1",
                "acquired_at": "2026-07-31T23:00:00+08:00",
            },
            source=sina_source,
            company_resolver=resolver,
            knowledge_cutoff=cutoff,
        )
        self.assertEqual(len(sina.facts), 3)
        self.assertNotEqual(
            canonical.reports[0]["financial_report_id"],
            sina.reports[0]["financial_report_id"],
        )
        self.assertEqual(
            {row["taxonomy_namespace"] for row in sina.concepts},
            {"sina.full_income_statement_projection"},
        )

        abstract_source = FinancialReportingSource(
            source_id="announcement_archive_fin_abstract",
            source_kind="financial_abstract",
            ledger_identity="announcement_archive|sqlite_table|fin_abstract",
            path=Path("/fixture/archive.db"),
            relative_path="payload/canonical/info_archive.db",
            source_sha256="sha256:" + "c" * 64,
            source_revision="ods-base-202607-v1.2",
            precedence="independent",
            member="fin_abstract",
        )
        abstract = expand_financial_native_row(
            {
                "code": "000001",
                "report_date": "2025-12-31",
                "revenue": 100.0,
                "eps_basic": 1.2,
                "ar_days": 20.0,
            },
            source=abstract_source,
            company_resolver=resolver,
            knowledge_cutoff=cutoff,
        )
        self.assertEqual(len(abstract.facts), 28)
        abstract_units = {
            concept["concept_code"]: fact["unit_code"]
            for concept, fact in zip(abstract.concepts, abstract.facts, strict=True)
        }
        self.assertEqual(abstract_units["eps_basic"], "CNY_PER_SHARE")
        self.assertEqual(abstract_units["ar_days"], "DAY")

        before_listing = expand_financial_native_row(
            {"stock_code": "000001", "report_date": "1990-12-31"},
            source=canonical_source,
            company_resolver=resolver,
            knowledge_cutoff=cutoff,
        )
        self.assertEqual(len(before_listing.reports), 0)
        self.assertEqual(
            before_listing.quality_exceptions[0]["code"],
            "BEFORE_FIRST_LISTED_COMPANY_IDENTITY",
        )

    def test_audits_native_sources_and_calibrates_selected_output(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            canonical_path = root / "canonical.parquet"
            full_path = root / "full.parquet"
            hotfix_path = root / "hotfix.parquet"
            archive_path = root / "archive.db"
            pq.write_table(
                pa.Table.from_pylist(
                    [
                        {
                            "stock_code": "000001",
                            "report_date": datetime(2025, 12, 31),
                            "publish_deadline": datetime(2026, 4, 30),
                            "roe": 8.5,
                            "revenue": 100.0,
                            "net_profit": 20.0,
                            "revenue_growth_yoy": 5.0,
                            "profit_growth_yoy": 4.0,
                        },
                        {
                            "stock_code": "000001",
                            "report_date": datetime(1990, 12, 31),
                            "publish_deadline": datetime(1991, 4, 30),
                            "roe": 1.0,
                            "revenue": 1.0,
                            "net_profit": 1.0,
                            "revenue_growth_yoy": 1.0,
                            "profit_growth_yoy": 1.0,
                        },
                    ]
                ),
                canonical_path,
            )

            def sina_row(stock_code: str) -> dict[str, object]:
                return {
                    "security_id": stock_code,
                    "stock_code": stock_code,
                    "report_date": "20251231",
                    "publish_date": "20260430",
                    "update_time": "20260430",
                    "revenue": "100.000000",
                    "net_profit": "21.000000",
                    "parent_net_profit": "20.000000",
                    "currency": "CNY",
                    "is_audit": "yes",
                    "audit_opinion": "clean",
                    "source": "sina.full_income_statement_projection",
                    "source_revision": "provider-v1",
                    "acquired_at": "2026-07-31T23:00:00+08:00",
                }

            pq.write_table(pa.Table.from_pylist([sina_row("000001")]), full_path)
            pq.write_table(pa.Table.from_pylist([sina_row("000002")]), hotfix_path)
            abstract_columns = ["code TEXT", "report_date TEXT"] + [
                f"{field} REAL"
                for field in (
                    "net_profit_attr",
                    "revenue",
                    "op_cost",
                    "net_profit",
                    "net_profit_deducted",
                    "equity",
                    "goodwill",
                    "ocf",
                    "eps_basic",
                    "bps",
                    "cfps",
                    "roe",
                    "roa",
                    "gross_margin",
                    "net_margin",
                    "expense_ratio",
                    "debt_ratio",
                    "revenue_yoy",
                    "np_attr_yoy",
                    "ocf_to_np",
                    "current_ratio",
                    "quick_ratio",
                    "equity_multiplier",
                    "inventory_turnover",
                    "ar_days",
                    "asset_turnover",
                    "retained_eps",
                    "ocf_ps",
                )
            ]
            with sqlite3.connect(archive_path) as connection:
                connection.execute(
                    f"CREATE TABLE fin_abstract ({', '.join(abstract_columns)})"
                )
                connection.execute(
                    "INSERT INTO fin_abstract (code, report_date, revenue) VALUES (?, ?, ?)",
                    ("000001", "2025-12-31", 100.0),
                )

            sources = (
                FinancialReportingSource(
                    "canonical_financial_quarterly",
                    "quarterly_projection",
                    "financial_quarterly",
                    canonical_path,
                    "payload/canonical/financial_quarterly.parquet",
                    "sha256:canonical",
                    "ods-base-v1.2",
                    "independent",
                    2,
                ),
                FinancialReportingSource(
                    "weekly_full_financial_quarterly",
                    "quarterly_projection",
                    "financial_quarterly",
                    full_path,
                    "payload/full/financial_quarterly.parquet",
                    "sha256:full",
                    "provider-v1",
                    "parent",
                    1,
                ),
                FinancialReportingSource(
                    "hotfix_financial_quarterly",
                    "quarterly_projection",
                    "fundamentals_financial_quarterly_parquet",
                    hotfix_path,
                    "payload/hotfix/financial_quarterly.parquet",
                    "sha256:hotfix",
                    "provider-v1",
                    "hotfix",
                    1,
                ),
                FinancialReportingSource(
                    "announcement_archive_fin_abstract",
                    "financial_abstract",
                    "announcement_archive|sqlite_table|fin_abstract",
                    archive_path,
                    "payload/canonical/info_archive.db",
                    "sha256:archive",
                    "ods-base-v1.2",
                    "independent",
                    1,
                    "fin_abstract",
                ),
            )
            inventory = FinancialReportingSourceInventory(
                sources=sources, empty_modeled_sources=()
            )
            resolver = FinancialCompanyResolver(
                [
                    FinancialCompanyIdentity(
                        "000001",
                        "security-1",
                        "company-1",
                        datetime(1991, 1, 1, tzinfo=timezone.utc),
                        None,
                    ),
                    FinancialCompanyIdentity(
                        "000002",
                        "security-2",
                        "company-2",
                        datetime(1991, 1, 1, tzinfo=timezone.utc),
                        None,
                    ),
                ]
            )

            audit = audit_financial_native_sources(
                inventory,
                company_resolver=resolver,
                knowledge_cutoff=datetime(2026, 8, 1, tzinfo=timezone.utc),
                batch_size=1,
            )

            self.assertEqual(
                audit.report["candidate_size_calibration"],
                {
                    "dwd_financial_report_rows": 4,
                    "dwd_financial_fact_rows": 39,
                    "dwd_financial_concept_count": 36,
                    "quality_exception_rows": 1,
                },
            )
            self.assertEqual(
                audit.report["quality_exceptions"],
                {"BEFORE_FIRST_LISTED_COMPANY_IDENTITY": 1},
            )
            self.assertEqual(
                audit.report["quarterly_overlay"]["hotfix_fill_rows"], 1
            )

    def test_company_resolver_accepts_multiple_same_company_security_versions(self) -> None:
        resolver = financial_company_resolver_from_listed_company_rows(
            [
                {"security_id": "security-1", "company_id": "company-1"},
                {"security_id": "security-1", "company_id": "company-1"},
            ],
            [
                {
                    "security_id": "security-1",
                    "identifier_namespace": "CN_STOCK_CODE:SSE",
                    "identifier_value": "600000",
                    "effective_from": datetime(1999, 1, 1, tzinfo=timezone.utc),
                    "effective_to": None,
                }
            ],
        )
        resolved = resolver.resolve(
            "600000",
            period_end=datetime(2025, 12, 31, tzinfo=timezone.utc).date(),
            published_at=None,
        )
        self.assertEqual(resolved.company_id, "company-1")

    def test_company_resolver_bridges_a_later_code_to_the_same_historical_company(self) -> None:
        resolver = FinancialCompanyResolver(
            [
                FinancialCompanyIdentity(
                    stock_code="000022",
                    security_id="security-old",
                    company_id="company-1",
                    effective_from=datetime(1993, 5, 5, tzinfo=timezone.utc),
                    effective_to=datetime(2018, 12, 26, tzinfo=timezone.utc),
                ),
                FinancialCompanyIdentity(
                    stock_code="001872",
                    security_id="security-new",
                    company_id="company-1",
                    effective_from=datetime(2018, 12, 26, tzinfo=timezone.utc),
                    effective_to=None,
                ),
            ]
        )

        resolved, reason = resolver.resolve_for_report(
            "001872", period_end=datetime(2010, 12, 31, tzinfo=timezone.utc).date()
        )

        self.assertIsNone(reason)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.security_id, "security-old")
        outside, reason = resolver.resolve_for_report(
            "001872", period_end=datetime(1990, 12, 31, tzinfo=timezone.utc).date()
        )
        self.assertIsNone(outside)
        self.assertEqual(reason, "BEFORE_FIRST_LISTED_COMPANY_IDENTITY")

    def test_writes_partitioned_rows_and_runs_external_key_audit(self) -> None:
        row = {
            **_envelope(
                {
                    "result": {
                        "data": {
                            "report_list": {
                                "20251231": {
                                    "rType": "consolidated",
                                    "rCurrency": "CNY",
                                    "data": [
                                        {"item_field": "CASH", "item_value": "12.5"},
                                        {"item_field": "DEBT", "item_value": ""},
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
            "source": "test.finance",
            "source_revision": "test-r1",
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
        rows_by_year, concepts, counts = _batch_rows([row], source=source, resolver=resolver)
        schemas = financial_reporting_schemas()
        with TemporaryDirectory() as temporary:
            work = Path(temporary)
            _write_batch(
                work=work,
                batch_id="00-000000",
                rows_by_year=rows_by_year,
                concepts=concepts,
                schemas=schemas,
            )
            concept_count = _finalize_concepts(work, {"concept_finalized": False}, schemas)
            row_counts, audit = _audit_financial_candidate(
                work=work,
                schemas=schemas,
                expected_reports=counts["dwd_financial_report"],
                expected_facts=counts["dwd_financial_fact"],
            )
        self.assertEqual(concept_count, 2)
        self.assertEqual(
            row_counts,
            {
                "dwd_financial_report": 1,
                "dwd_financial_concept": 2,
                "dwd_financial_fact": 2,
            },
        )
        self.assertEqual(
            audit["duplicate_business_keys"],
            {
                "dwd_financial_report": 0,
                "dwd_financial_concept": 0,
                "dwd_financial_fact": 0,
            },
        )


if __name__ == "__main__":
    unittest.main()

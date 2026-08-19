from __future__ import annotations

import hashlib
import json
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from quanthus.pipelines.securities_warehouse.disclosure_event import (
    DisclosureEventBuildError,
    _load_vocabulary,
    _provider_timestamp,
    _select_sources,
    close_adjustment_factor_intervals,
    disclosure_event_schemas,
    materialize_adjustment_factor_rows,
    materialize_announcement_rows,
    materialize_corporate_action_rows,
    reconcile_supplemental_corporate_action,
)


class DisclosureEventTests(unittest.TestCase):
    def test_physical_schemas_cover_all_six_owned_tables(self) -> None:
        self.assertEqual(
            set(disclosure_event_schemas()),
            {
                "dwd_disclosure",
                "dwd_disclosure_subject",
                "dwd_corporate_action",
                "dwd_corporate_action_term",
                "dwd_adjustment_factor",
                "dwd_adjustment_factor_input",
            },
        )

    def test_date_only_availability_uses_shanghai_end_of_day(self) -> None:
        value = _provider_timestamp(date(2026, 7, 31), "published_at")

        self.assertEqual(value.tzinfo, timezone.utc)
        self.assertEqual(value.isoformat(), "2026-07-31T15:59:59.999999+00:00")

    def test_vocabulary_requires_governed_date_policy(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "vocabulary.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "kind": "quanthus_disclosure_event_vocabulary",
                        "mapping_version": "fixture-v1",
                        "date_only_availability": "wrong",
                        "action_types": {"cash_div": "CASH_DIVIDEND"},
                        "action_statuses": {"实施": "IMPLEMENTED"},
                        "term_types": {"cash_per_share": "CASH_AMOUNT_PER_SHARE"},
                        "factor_methods": {
                            "adjust_factor": {
                                "method": "PROVIDER",
                                "direction": "PROVIDER_NATIVE",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(DisclosureEventBuildError, "unsupported"):
                _load_vocabulary(path)

    def test_source_selection_binds_container_to_archive(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = {
                "payload/canonical/info_archive.db": b"archive",
                "payload/canonical/ref_corporate_actions.parquet": b"actions",
                "payload/canonical/dividend_history.parquet": b"dividends",
                "payload/canonical/ref_adjustment_factors.parquet": b"factors",
            }
            for relative, payload in paths.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)

            def record(identity: str, relative: str, rows: int | None):
                payload = paths[relative]
                return {
                    "mapping_identity": identity,
                    "record_type": "file",
                    "disposition": "modeled",
                    "role": "source_evidence",
                    "path": relative,
                    "sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
                    "size_bytes": len(payload),
                    "row_count": rows,
                }

            records = [
                record("announcement_archive", "payload/canonical/info_archive.db", None),
                record(
                    "corporate_actions",
                    "payload/canonical/ref_corporate_actions.parquet",
                    2,
                ),
                record(
                    "dividend_history",
                    "payload/canonical/dividend_history.parquet",
                    3,
                ),
                record(
                    "adjustment_factors_pit",
                    "payload/canonical/ref_adjustment_factors.parquet",
                    4,
                ),
                {
                    "mapping_identity": "announcement_archive|sqlite_table|announcements",
                    "record_type": "container_member",
                    "disposition": "modeled",
                    "container_source_id": "announcement_archive",
                    "container_path": "payload/canonical/info_archive.db",
                    "path": "payload/canonical/info_archive.db#table=announcements",
                    "member": "announcements",
                    "row_count": 5,
                },
            ]

            selected = _select_sources(records, root)

            self.assertEqual(selected["announcements"].row_count, 5)
            self.assertEqual(
                selected["announcements"].path,
                selected["announcement_archive"].path,
            )

    def test_announcement_has_security_and_report_subjects(self) -> None:
        parent_knowledge = datetime(2026, 8, 1, tzinfo=timezone.utc)
        report_knowledge = datetime(2026, 8, 2, tzinfo=timezone.utc)
        rows = materialize_announcement_rows(
            {
                "ann_id": "ann-1",
                "publish_time": "2026-07-31",
                "title": "2026年半年度报告",
                "url": "https://example.invalid/ann-1",
                "category": "定期报告",
            },
            company_id="company-1",
            security_id="security-1",
            source_sha256="sha256:archive",
            source_revision="ods-base-v1.2",
            financial_report_ids=["report-1"],
            parent_knowledge_from=parent_knowledge,
            financial_report_knowledge={"report-1": report_knowledge},
        )

        self.assertEqual(len(rows["dwd_disclosure"]), 1)
        subjects = rows["dwd_disclosure_subject"]
        self.assertEqual(
            {row["subject_type"] for row in subjects},
            {"SECURITY", "FINANCIAL_REPORT"},
        )
        self.assertTrue(
            all(
                sum(
                    value is not None
                    for value in (
                        row["security_id"],
                        row["corporate_action_id"],
                        row["financial_report_id"],
                    )
                )
                == 1
                for row in subjects
            )
        )
        self.assertEqual(rows["dwd_disclosure"][0]["knowledge_from"], parent_knowledge)
        report_subject = next(row for row in subjects if row["subject_type"] == "FINANCIAL_REPORT")
        self.assertEqual(report_subject["knowledge_from"], report_knowledge)

    def test_action_terms_preserve_cash_and_share_roles(self) -> None:
        vocabulary = {
            "action_types": {"mixed": "MIXED_DISTRIBUTION"},
            "action_statuses": {"实施": "IMPLEMENTED"},
            "term_types": {
                "cash_per_share": "CASH_AMOUNT_PER_SHARE",
                "bonus_shares": "BONUS_SHARES_PER_SHARE",
                "transfer_shares": "TRANSFER_SHARES_PER_SHARE",
            },
        }
        rows = materialize_corporate_action_rows(
            {
                "action_id": "action-1",
                "stock_code": "000001",
                "announce_date": "2026-06-01",
                "ex_date": "2026-06-12",
                "action_type": "mixed",
                "progress": "实施",
                "cash_per_share": 0.36,
                "bonus_shares": 0.1,
                "built_at": "2026-06-01T12:00:00Z",
            },
            source_id="corporate_actions",
            source_sha256="sha256:actions",
            source_revision="v1",
            company_id="company-1",
            security_id="security-1",
            vocabulary=vocabulary,
        )

        terms = rows["dwd_corporate_action_term"]
        self.assertEqual(len(terms), 2)
        self.assertEqual(
            {row["term_type"] for row in terms},
            {"CASH_AMOUNT_PER_SHARE", "BONUS_SHARES_PER_SHARE"},
        )

    def test_action_share_terms_normalize_per_ten_to_per_share(self) -> None:
        rows = materialize_corporate_action_rows(
            {
                "action_id": "action-per-ten",
                "stock_code": "600519",
                "announce_date": "2014-06-18",
                "ex_date": "2014-06-25",
                "action_type": "mixed",
                "progress": "实施",
                "cash_per_share": 4.374,
                "bonus_shares": 1,
            },
            source_id="corporate_actions",
            source_sha256="sha256:actions",
            source_revision="v1",
            company_id="company-1",
            security_id="security-1",
            vocabulary={
                "share_quantity_unit": "per_ten_shares",
                "action_types": {"mixed": "MIXED_DISTRIBUTION"},
                "action_statuses": {"实施": "IMPLEMENTED"},
                "term_types": {
                    "cash_per_share": "CASH_AMOUNT_PER_SHARE",
                    "bonus_shares": "BONUS_SHARES_PER_SHARE",
                    "transfer_shares": "TRANSFER_SHARES_PER_SHARE",
                },
            },
        )
        bonus = next(
            term
            for term in rows["dwd_corporate_action_term"]
            if term["term_type"] == "BONUS_SHARES_PER_SHARE"
        )

        self.assertEqual(bonus["ratio_numerator"], Decimal("0.1"))

    def test_action_terms_drop_zero_value_placeholders(self) -> None:
        rows = materialize_corporate_action_rows(
            {
                "action_id": "action-zero",
                "stock_code": "000001",
                "announce_date": "2026-06-01",
                "ex_date": "2026-06-12",
                "action_type": "cash_div",
                "progress": "实施",
                "cash_per_share": 0.36,
                "bonus_shares": 0,
                "transfer_shares": 0,
            },
            source_id="dividend_history",
            source_sha256="sha256:dividend",
            source_revision="v1",
            company_id="company-1",
            security_id="security-1",
            vocabulary={
                "action_types": {"cash_div": "CASH_DIVIDEND"},
                "action_statuses": {"实施": "IMPLEMENTED"},
                "term_types": {
                    "cash_per_share": "CASH_AMOUNT_PER_SHARE",
                    "bonus_shares": "BONUS_SHARES_PER_SHARE",
                    "transfer_shares": "TRANSFER_SHARES_PER_SHARE",
                },
            },
        )

        self.assertEqual(len(rows["dwd_corporate_action_term"]), 1)
        self.assertEqual(
            rows["dwd_corporate_action_term"][0]["term_type"],
            "CASH_AMOUNT_PER_SHARE",
        )

    def test_supplemental_action_keeps_only_terms_missing_from_authority(self) -> None:
        vocabulary = {
            "action_types": {"mixed": "MIXED_DISTRIBUTION"},
            "action_statuses": {"实施": "IMPLEMENTED"},
            "term_types": {
                "cash_per_share": "CASH_AMOUNT_PER_SHARE",
                "bonus_shares": "BONUS_SHARES_PER_SHARE",
                "transfer_shares": "TRANSFER_SHARES_PER_SHARE",
            },
        }
        rows = materialize_corporate_action_rows(
            {
                "action_id": "supplemental",
                "stock_code": "000001",
                "announce_date": "2026-06-01",
                "ex_date": "2026-06-12",
                "action_type": "mixed",
                "progress": "实施",
                "cash_per_share": 0.36,
                "bonus_shares": 0.1,
            },
            source_id="dividend_history",
            source_sha256="sha256:dividend",
            source_revision="v1",
            company_id="company-1",
            security_id="security-1",
            vocabulary=vocabulary,
        )
        authority = {
            (
                "security-1",
                date(2026, 6, 12),
                date(2026, 6, 1),
                "CASH_AMOUNT_PER_SHARE",
            ): rows["dwd_corporate_action_term"][0]["amount_per_share"]
        }

        reconciled = reconcile_supplemental_corporate_action(rows, authority_terms=authority)

        self.assertEqual(len(reconciled["dwd_corporate_action"]), 1)
        self.assertEqual(
            [term["term_type"] for term in reconciled["dwd_corporate_action_term"]],
            ["BONUS_SHARES_PER_SHARE"],
        )

    def test_supplemental_action_conflict_fails_closed(self) -> None:
        rows = materialize_corporate_action_rows(
            {
                "action_id": "conflict",
                "stock_code": "000001",
                "announce_date": "2026-06-01",
                "ex_date": "2026-06-12",
                "action_type": "cash_div",
                "progress": "实施",
                "cash_per_share": 0.36,
            },
            source_id="dividend_history",
            source_sha256="sha256:dividend",
            source_revision="v1",
            company_id="company-1",
            security_id="security-1",
            vocabulary={
                "action_types": {"cash_div": "CASH_DIVIDEND"},
                "action_statuses": {"实施": "IMPLEMENTED"},
                "term_types": {
                    "cash_per_share": "CASH_AMOUNT_PER_SHARE",
                    "bonus_shares": "BONUS_SHARES_PER_SHARE",
                    "transfer_shares": "TRANSFER_SHARES_PER_SHARE",
                },
            },
        )
        authority = {
            (
                "security-1",
                date(2026, 6, 12),
                date(2026, 6, 1),
                "CASH_AMOUNT_PER_SHARE",
            ): rows["dwd_corporate_action_term"][0]["amount_per_share"] + 1
        }

        with self.assertRaisesRegex(DisclosureEventBuildError, "conflicts"):
            reconcile_supplemental_corporate_action(rows, authority_terms=authority)

    def test_factor_has_one_explicit_evidence_input_per_method(self) -> None:
        rows = materialize_adjustment_factor_rows(
            {
                "stock_code": "000001",
                "effective_date": "2026-06-12",
                "fore_adjust_factor": 1.2,
                "back_adjust_factor": 0.8,
                "adjust_factor": 1.0,
                "adj_version": "v3",
                "knowledge_cutoff": "2026-07-31T00:00:00Z",
                "source_revision": "r3",
            },
            source_sha256="sha256:factors",
            company_security_id="security-1",
            vocabulary={
                "factor_methods": {
                    "adjust_factor": {"direction": "PROVIDER_NATIVE", "method": "PROVIDER"},
                    "back_adjust_factor": {"direction": "BACKWARD", "method": "BACK"},
                    "fore_adjust_factor": {"direction": "FORWARD", "method": "FORE"},
                }
            },
        )

        self.assertEqual(len(rows["dwd_adjustment_factor"]), 3)
        self.assertEqual(len(rows["dwd_adjustment_factor_input"]), 3)
        self.assertTrue(
            all(
                row["input_type"] == "SOURCE_EVIDENCE"
                for row in rows["dwd_adjustment_factor_input"]
            )
        )

    def test_factor_intervals_close_at_the_next_effective_date(self) -> None:
        first = {
            "adjustment_factor_id": "factor-1",
            "effective_from": _provider_timestamp("2026-06-01", "first"),
            "effective_to": None,
        }
        second = {
            "adjustment_factor_id": "factor-1",
            "effective_from": _provider_timestamp("2026-07-01", "second"),
            "effective_to": None,
        }
        inputs = [dict(first), dict(second)]

        close_adjustment_factor_intervals([second, first], inputs)

        self.assertEqual(first["effective_to"], second["effective_from"])
        self.assertIsNone(second["effective_to"])
        self.assertEqual(inputs[0]["effective_to"], second["effective_from"])


if __name__ == "__main__":
    unittest.main()

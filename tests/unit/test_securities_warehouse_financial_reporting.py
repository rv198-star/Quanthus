from __future__ import annotations

import gzip
import hashlib
import json
import unittest
from datetime import datetime, timezone

from quanthus.pipelines.securities_warehouse.financial_reporting import (
    FinancialReportingBuildError,
    classify_statement_rows,
    conservative_provider_timestamp,
    decode_financial_envelope,
    iter_statement_reports,
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


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import pyarrow.parquet as pq

from quanthus.pipelines.dwd_pre_rebuild_audit import load_table_contracts
from quanthus.pipelines.securities_warehouse.quality_governance import (
    QualityGovernanceBuildError,
    _write_quality_rows,
    quality_exception_rows,
    quality_governance_schema,
    validate_domain_accounting_receipt,
)

_ROOT = Path(__file__).resolve().parents[2]

_CONTRACT = {
    "contract_id": "quanthus-dwd-domain-source-accounting-receipt-v1",
    "exception_required_fields": [
        "source_identity",
        "rule_id",
        "rule_version",
        "affected_domain",
        "affected_table",
        "affected_business_key",
        "affected_effective_at",
        "detected_at",
        "classification",
        "severity",
        "authority",
        "source_record_id",
        "source_revision",
        "source_artifact_sha256",
        "evidence_digest",
        "disposition",
        "resolution_state",
    ],
    "blocking_dispositions": ["FAILED"],
    "retained_dispositions": ["ACCEPTED_LIMITATION"],
}


def _receipt() -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "dwd_domain_source_accounting_receipt",
        "contract_id": _CONTRACT["contract_id"],
        "run_id": "run-1",
        "input_plan_digest": "sha256:plan",
        "domain": "listed_company",
        "candidate_manifest_sha256": "sha256:candidate",
        "sources": [
            {
                "source_identity": "security_master",
                "eligible_rows": 2,
                "modeled": 1,
                "reconciliation_only": 0,
                "ods_only": 0,
                "deferred": 0,
                "quality_exception": 1,
            }
        ],
        "exceptions": [
            {
                "source_identity": "security_master",
                "rule_id": "BSE_COVERAGE",
                "rule_version": "1",
                "affected_domain": "listed_company",
                "affected_table": "dwd_security",
                "affected_business_key": {"stock_code": "920258"},
                "affected_effective_at": "2026-07-31T15:59:59Z",
                "detected_at": "2026-07-31T15:59:59Z",
                "classification": "BOUNDED_COVERAGE",
                "severity": "WARN",
                "authority": "ODS_BASE_V1_2",
                "source_record_id": "row-2",
                "source_revision": "v1.2",
                "source_artifact_sha256": "sha256:source",
                "evidence_digest": "sha256:evidence",
                "disposition": "ACCEPTED_LIMITATION",
                "resolution_state": "OPEN_ACCEPTED",
            }
        ],
    }


class QualityGovernanceTests(unittest.TestCase):
    def test_physical_schema_order_matches_the_frozen_extended_model(self) -> None:
        contract = load_table_contracts(
            _ROOT / "config/pipelines/dwd-domain-model.v1.json",
            _ROOT / "config/pipelines/dwd-domain-model.v1.1-extension.json",
        )["dwd_quality_exception"]

        self.assertEqual(
            quality_governance_schema().names,
            [field.name for field in contract.fields],
        )

    def test_writer_derives_partition_year_and_keeps_it_out_of_physical_schema(self) -> None:
        rows = quality_exception_rows(
            [_receipt()],
            contract=_CONTRACT,
            run_id="run-1",
            input_plan_digest="sha256:plan",
        )
        rows[0]["detected_at"] = datetime(2025, 12, 31, tzinfo=timezone.utc)
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            relative = _write_quality_rows(
                root,
                rows,
                default_detected_at="2026-07-31T15:59:59Z",
            )
            expected = (
                "payload/governance/dwd_quality_exception/"
                "detected_at_year=2025/part-00000.parquet"
            )
            self.assertEqual(relative, [expected])
            schema = pq.ParquetFile(root / expected).schema_arrow
            self.assertTrue(schema.equals(quality_governance_schema()))
            self.assertNotIn("detected_at_year", schema.names)

    def test_quality_rows_normalize_iso_hour_timezone_offset(self) -> None:
        receipt = _receipt()
        receipt["exceptions"][0]["affected_effective_at"] = (  # type: ignore[index]
            "2007-12-26 09:30:00+08"
        )

        rows = quality_exception_rows(
            [receipt],
            contract=_CONTRACT,
            run_id="run-1",
            input_plan_digest="sha256:plan",
        )

        self.assertEqual(rows[0]["affected_effective_at"].utcoffset().total_seconds(), 0)

    def test_receipt_requires_source_row_conservation(self) -> None:
        receipt = _receipt()
        receipt["sources"][0]["modeled"] = 2  # type: ignore[index]

        with self.assertRaisesRegex(QualityGovernanceBuildError, "conserve"):
            validate_domain_accounting_receipt(
                receipt,
                contract=_CONTRACT,
                run_id="run-1",
                input_plan_digest="sha256:plan",
            )

    def test_quality_rows_retain_new_v11_fields(self) -> None:
        rows = quality_exception_rows(
            [_receipt()],
            contract=_CONTRACT,
            run_id="run-1",
            input_plan_digest="sha256:plan",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rule_version"], "1")
        self.assertEqual(rows[0]["affected_domain"], "listed_company")
        self.assertEqual(rows[0]["disposition"], "ACCEPTED_LIMITATION")


if __name__ == "__main__":
    unittest.main()

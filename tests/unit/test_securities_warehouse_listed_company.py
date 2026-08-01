from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pyarrow as pa
import pyarrow.parquet as pq

from quanthus.pipelines.securities_warehouse.listed_company import (
    ListedCompanyBuildError,
    build_listed_company,
)

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "config" / "pipelines" / "dwd-domain-model.v1.json"
REFERENCE_SEED = ROOT / "config" / "pipelines" / "securities-warehouse-reference-seeds.v1.json"


def _sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


class ListedCompanyBuildTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.ods = self.root / "ods-base-v1.1"
        self.plan = self.root / "input-plan"
        self.output = self.root / "candidate"
        self.run_id = "listed-company-fixture-v1"
        self.validity = self.root / "security-code-validity.json"
        self._write_sources()
        self._write_validity()
        self._write_plan()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _write_sources(self) -> None:
        canonical = self.ods / "payload" / "canonical"
        canonical.mkdir(parents=True)
        master_rows = [
            {
                "security_id": "sz.000022",
                "stock_code": "000022",
                "exchange": "SZSE",
                "name": "OLD PORT",
                "list_date": "1993-05-05",
                "delist_date": "2018-12-26",
                "status": "delisted",
                "bs_code": "sz.000022",
                "source": "fixture",
                "as_of": "2026-07-31",
            },
            {
                "security_id": "sz.001872",
                "stock_code": "001872",
                "exchange": "SZSE",
                "name": "NEW PORT",
                "list_date": "2018-12-27",
                "delist_date": None,
                "status": "listed",
                "bs_code": "sz.001872",
                "source": "fixture",
                "as_of": "2026-07-31",
            },
            {
                "security_id": "sh.600000",
                "stock_code": "600000",
                "exchange": "SSE",
                "name": "SH COMPANY",
                "list_date": "1999-11-10",
                "delist_date": None,
                "status": "listed",
                "bs_code": "sh.600000",
                "source": "fixture",
                "as_of": "2026-07-31",
            },
            {
                "security_id": "920001",
                "stock_code": "920001",
                "exchange": "BSE",
                "name": "BJ COMPANY",
                "list_date": "2022-01-01",
                "delist_date": None,
                "status": "listed",
                "bs_code": None,
                "source": "fixture",
                "as_of": "2026-07-31",
            },
        ]
        pq.write_table(pa.Table.from_pylist(master_rows), canonical / "ref_security_master.parquet")
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {"stock_code": row["stock_code"]}
                    for row in master_rows
                    if row["status"] == "listed"
                ]
            ),
            canonical / "stock_list.parquet",
        )
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {"exchange": "SSE", "trade_date": "2026-07-31"},
                    {"exchange": "SZSE", "trade_date": "2026-07-31"},
                    {"exchange": "BSE", "trade_date": "2026-07-31"},
                ]
            ),
            canonical / "ref_trading_calendar.parquet",
        )
        self._write_json(
            canonical / "cninfo_orgid_map.json",
            {
                "000022": "ORG-PORT",
                "001872": "ORG-PORT",
                "600000": "ORG-SH",
                "920001": "ORG-BJ",
            },
        )

    def _write_validity(self) -> None:
        self._write_json(
            self.validity,
            {
                "schema_version": "1.0.0",
                "mapping_version": "fixture-code-validity-v1",
                "status": "frozen",
                "intervals": [
                    {
                        "security_id": "SZSE-ECONOMIC-PORT",
                        "stock_code": "000022",
                        "exchange": "SZSE",
                        "valid_from": "1993-05-05",
                        "valid_to": "2018-12-26",
                    },
                    {
                        "security_id": "SZSE-ECONOMIC-PORT",
                        "stock_code": "001872",
                        "exchange": "SZSE",
                        "valid_from": "2018-12-27",
                        "valid_to": None,
                    },
                ],
            },
        )

    def _write_plan(self) -> None:
        source_specs = [
            (
                "security_master",
                "payload/canonical/ref_security_master.parquet",
                [
                    "dwd_listed_company",
                    "dwd_security",
                    "dwd_security_identifier",
                    "dwd_listing",
                ],
            ),
            (
                "stock_list",
                "payload/canonical/stock_list.parquet",
                ["dwd_security"],
            ),
            (
                "trading_calendar",
                "payload/canonical/ref_trading_calendar.parquet",
                ["dwd_trading_venue"],
            ),
            (
                "announcement_orgid_map",
                "payload/canonical/cninfo_orgid_map.json",
                ["dwd_listed_company", "dwd_company_identifier"],
            ),
        ]
        records = []
        for dataset_id, relative, targets in source_specs:
            path = self.ods / relative
            superseded = dataset_id == "stock_list"
            records.append(
                {
                    "dataset_id": dataset_id,
                    "record_type": "file",
                    "disposition": "superseded" if superseded else "modeled",
                    "mapping_identity": dataset_id,
                    "path": relative,
                    "sha256": _sha256(path),
                    "size_bytes": path.stat().st_size,
                    "target_tables": targets,
                }
            )
        semantic_plan = {
            "mode": "base",
            "knowledge_cutoff": "2026-08-01T00:00:00+00:00",
            "model": {
                "contract_id": json.loads(MODEL.read_text(encoding="utf-8"))["contract_id"],
                "sha256": _sha256(MODEL),
            },
            "source_precedence": {"expected_sse_szse_listed_scope_after_overlay": 2},
        }
        plan_digest = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(semantic_plan, sort_keys=True, separators=(",", ":")).encode("ascii")
            ).hexdigest()
        )
        self._write_json(
            self.plan / "input-plan.json",
            {
                "schema_version": 1,
                "run_id": self.run_id,
                "plan_digest": plan_digest,
                "plan": semantic_plan,
            },
        )
        self._write_json(
            self.plan / "source-disposition-ledger.json",
            {"schema_version": 1, "records": records},
        )
        files = []
        for name in ("input-plan.json", "source-disposition-ledger.json"):
            path = self.plan / name
            files.append({"path": name, "sha256": _sha256(path), "size_bytes": path.stat().st_size})
        self._write_json(
            self.plan / "manifest.json",
            {"schema_version": 1, "plan_digest": plan_digest, "files": files},
        )
        (self.plan / "READY").write_text(
            f"{_sha256(self.plan / 'manifest.json')}\n", encoding="ascii"
        )

    def _build(self, output: Path | None = None):
        return build_listed_company(
            ods_package=self.ods,
            input_plan=self.plan,
            model_path=MODEL,
            reference_seed_path=REFERENCE_SEED,
            code_validity_path=self.validity,
            output=output or self.output,
            run_id=self.run_id,
        )

    @staticmethod
    def _rows(output: Path, table: str) -> list[dict[str, object]]:
        return pq.read_table(output / "payload" / "listed_company" / f"{table}.parquet").to_pylist()

    def test_builds_six_tables_with_stable_identity_and_bse_policy(self) -> None:
        result = self._build()

        self.assertEqual(
            result.row_counts,
            {
                "dwd_listed_company": 3,
                "dwd_company_identifier": 3,
                "dwd_security": 3,
                "dwd_security_identifier": 11,
                "dwd_listing": 4,
                "dwd_trading_venue": 3,
            },
        )
        listings = self._rows(result.root, "dwd_listing")
        identifiers = self._rows(result.root, "dwd_security_identifier")
        old_security = next(
            row["security_id"] for row in listings if row["listing_code"] == "000022"
        )
        new_security = next(
            row["security_id"] for row in listings if row["listing_code"] == "001872"
        )
        self.assertEqual(old_security, new_security)
        self.assertNotIn("000022", str(old_security))
        self.assertNotIn("001872", str(old_security))
        self.assertEqual(
            {row["identifier_value"] for row in identifiers if row["security_id"] == old_security},
            {"000022", "001872", "sz.000022", "sz.001872"},
        )

        venues = self._rows(result.root, "dwd_trading_venue")
        self.assertEqual({row["mic_code"] for row in venues}, {"XSHG", "XSHE", "BJSE"})
        companies = self._rows(result.root, "dwd_listed_company")
        self.assertEqual(
            sum(
                row["resolution_scope"] == "CNINFO_ORG_ID:BSE_PARTIAL_NONBLOCKING"
                for row in companies
            ),
            1,
        )
        receipt = json.loads(
            (result.root / "receipts" / "listed-company-build.json").read_text(encoding="utf-8")
        )
        self.assertEqual(receipt["coverage"]["primary_sse_szse_listed"], 2)
        self.assertEqual(receipt["coverage"]["bse_policy"], "BSE_PARTIAL_NONBLOCKING")
        self.assertEqual(receipt["quality_exceptions"], [])
        reconciliation = json.loads(
            (result.root / "reconciliation" / "source-row-reconciliation.json").read_text(
                encoding="utf-8"
            )
        )
        stock_list = next(
            source for source in reconciliation["sources"] if source["dataset_id"] == "stock_list"
        )
        self.assertEqual(stock_list["outcome"], "corroborated_collection_scope")

    def test_output_keys_foreign_keys_and_intervals_are_valid(self) -> None:
        result = self._build()
        tables = {
            table: self._rows(result.root, table)
            for table in (
                "dwd_listed_company",
                "dwd_company_identifier",
                "dwd_security",
                "dwd_security_identifier",
                "dwd_listing",
                "dwd_trading_venue",
            )
        }
        companies = {row["company_id"] for row in tables["dwd_listed_company"]}
        securities = {row["security_id"] for row in tables["dwd_security"]}
        venues = {row["venue_id"] for row in tables["dwd_trading_venue"]}
        self.assertTrue(
            all(row["company_id"] in companies for row in tables["dwd_company_identifier"])
        )
        self.assertTrue(all(row["company_id"] in companies for row in tables["dwd_security"]))
        self.assertTrue(
            all(row["security_id"] in securities for row in tables["dwd_security_identifier"])
        )
        self.assertTrue(all(row["security_id"] in securities for row in tables["dwd_listing"]))
        self.assertTrue(all(row["venue_id"] in venues for row in tables["dwd_listing"]))
        for rows in tables.values():
            for row in rows:
                self.assertIsNotNone(row["knowledge_from"])
                if row["effective_to"] is not None:
                    self.assertLess(row["effective_from"], row["effective_to"])

    def test_tampered_declared_source_fails_closed(self) -> None:
        source = self.ods / "payload" / "canonical" / "cninfo_orgid_map.json"
        source.write_text("{}\n", encoding="ascii")

        with self.assertRaisesRegex(ListedCompanyBuildError, "ODS source failed verification"):
            self._build()
        self.assertFalse(self.output.exists())

    def test_same_run_is_idempotent_and_existing_output_is_verified(self) -> None:
        first = self._build()
        manifest_mtime = (first.root / "manifest.json").stat().st_mtime_ns

        second = self._build()

        self.assertEqual(second.semantic_digest, first.semantic_digest)
        self.assertEqual((first.root / "manifest.json").stat().st_mtime_ns, manifest_mtime)
        payload = first.root / "payload" / "listed_company" / "dwd_listing.parquet"
        payload.write_bytes(b"tampered")
        with self.assertRaisesRegex(ListedCompanyBuildError, "failed verification"):
            self._build()

    def test_retry_resumes_a_verified_table_checkpoint(self) -> None:
        real_write = pq.write_table
        calls = 0

        def fail_second_write(*args: object, **kwargs: object) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("fixture interruption")
            real_write(*args, **kwargs)

        with (
            mock.patch("pyarrow.parquet.write_table", side_effect=fail_second_write),
            self.assertRaisesRegex(OSError, "fixture interruption"),
        ):
            self._build()
        work = self.output.parent / f".{self.output.name}.work"
        checkpoint = json.loads((work / "checkpoint.json").read_text(encoding="utf-8"))
        self.assertEqual(list(checkpoint["completed_tables"]), ["dwd_listed_company"])

        result = self._build()

        self.assertTrue((result.root / "READY").is_file())
        self.assertFalse(work.exists())


if __name__ == "__main__":
    unittest.main()

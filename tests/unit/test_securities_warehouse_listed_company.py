from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import pyarrow as pa
import pyarrow.parquet as pq

from quanthus.pipelines.securities_warehouse import listed_company as listed_company_module
from quanthus.pipelines.securities_warehouse.listed_company import (
    ListedCompanyBuildError,
    build_listed_company,
)

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "config" / "pipelines" / "dwd-domain-model.v1.json"
REFERENCE_SEED = ROOT / "config" / "pipelines" / "securities-warehouse-reference-seeds.v1.json"


def _sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(encoded.encode('ascii')).hexdigest()}"


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
                        "source": "fixture exchange notice",
                        "evidence_locator": "fixture://old-port",
                        "evidence_sha256": f"sha256:{'a' * 64}",
                        "source_revision": "fixture-old-port-v1",
                        "knowledge_cutoff": "1993-05-05T00:00:00+08:00",
                    },
                    {
                        "security_id": "SZSE-ECONOMIC-PORT",
                        "stock_code": "001872",
                        "exchange": "SZSE",
                        "valid_from": "2018-12-27",
                        "valid_to": None,
                        "source": "fixture exchange notice",
                        "evidence_locator": "fixture://new-port",
                        "evidence_sha256": f"sha256:{'b' * 64}",
                        "source_revision": "fixture-new-port-v1",
                        "knowledge_cutoff": "2018-12-26T23:59:59+08:00",
                    },
                ],
                "source_revision": "fixture-code-validity-source-v1",
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
        snapshot_relative = "payload/weekly/test/ref_security_master.parquet"
        if (self.ods / snapshot_relative).is_file():
            source_specs.append(
                (
                    "weekly_test_security_master",
                    snapshot_relative,
                    ["dwd_listed_company", "dwd_security", "dwd_listing"],
                )
            )
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
        ledger = {"schema_version": 1, "records": records}
        master_rows = pq.read_table(
            self.ods / "payload" / "canonical" / "ref_security_master.parquet"
        ).to_pylist()
        expected_primary = sum(
            row["exchange"] in {"SSE", "SZSE"} and row["status"] == "listed" for row in master_rows
        )
        semantic_plan = {
            "schema_version": 1,
            "mode": "base",
            "knowledge_cutoff": "2026-08-02T00:00:00+00:00",
            "model": {
                "contract_id": json.loads(MODEL.read_text(encoding="utf-8"))["contract_id"],
                "sha256": _sha256(MODEL),
            },
            "source_precedence": {"expected_sse_szse_listed_scope_after_overlay": expected_primary},
            "source_ledger_digest": _digest(ledger),
        }
        plan_digest = _digest(semantic_plan)
        self._write_json(
            self.plan / "input-plan.json",
            {
                "schema_version": 1,
                "kind": "dwd_dws_input_plan",
                "run_id": self.run_id,
                "plan_digest": plan_digest,
                "plan": semantic_plan,
            },
        )
        self._write_json(
            self.plan / "source-disposition-ledger.json",
            ledger,
        )
        self._write_json(self.plan / "capacity-estimate.json", {"schema_version": 1})
        self._write_json(self.plan / "coverage-matrix.json", {"schema_version": 1})
        self._write_json(
            self.plan / "run-receipt.json",
            {"schema_version": 1, "kind": "dwd_dws_input_plan_receipt"},
        )
        files = []
        for name in (
            "capacity-estimate.json",
            "coverage-matrix.json",
            "input-plan.json",
            "run-receipt.json",
            "source-disposition-ledger.json",
        ):
            path = self.plan / name
            files.append({"path": name, "sha256": _sha256(path), "size_bytes": path.stat().st_size})
        self._write_json(
            self.plan / "manifest.json",
            {
                "schema_version": 1,
                "kind": "dwd_dws_input_plan_package",
                "plan_digest": plan_digest,
                "files": files,
            },
        )
        (self.plan / "READY").write_text(
            f"{_sha256(self.plan / 'manifest.json')}\n", encoding="ascii"
        )

    def _build(
        self,
        output: Path | None = None,
        reference_seed: Path | None = None,
        code_validity: Path | None = None,
    ):
        return build_listed_company(
            ods_package=self.ods,
            input_plan=self.plan,
            model_path=MODEL,
            reference_seed_path=reference_seed or REFERENCE_SEED,
            code_validity_path=code_validity or self.validity,
            output=output or self.output,
            run_id=self.run_id,
        )

    def _work(self) -> Path:
        matches = sorted(self.output.parent.glob(f".{self.output.name}.*.work"))
        self.assertEqual(len(matches), 1)
        return matches[0]

    def _set_plan_run_id(self, run_id: str) -> None:
        plan_path = self.plan / "input-plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["run_id"] = run_id
        self._write_json(plan_path, plan)
        manifest_path = self.plan / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        plan_entry = next(item for item in manifest["files"] if item["path"] == plan_path.name)
        plan_entry["sha256"] = _sha256(plan_path)
        plan_entry["size_bytes"] = plan_path.stat().st_size
        self._write_json(manifest_path, manifest)
        (self.plan / "READY").write_text(f"{_sha256(manifest_path)}\n", encoding="ascii")
        self.run_id = run_id

    @staticmethod
    def _rows(output: Path, table: str) -> list[dict[str, object]]:
        return pq.read_table(output / "payload" / "listed_company" / f"{table}.parquet").to_pylist()

    def _resign_manifest(self, output: Path) -> None:
        manifest_path = output / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for item in manifest["files"]:
            path = output / item["path"]
            item["sha256"] = _sha256(path)
            item["size_bytes"] = path.stat().st_size
        self._write_json(manifest_path, manifest)
        (output / "READY").write_text(f"{_sha256(manifest_path)}\n", encoding="ascii")

    def test_builds_six_tables_with_stable_identity_and_bse_policy(self) -> None:
        result = self._build()

        self.assertEqual(
            result.row_counts,
            {
                "dwd_listed_company": 4,
                "dwd_company_identifier": 4,
                "dwd_security": 4,
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
        self.assertEqual(len(companies), 4)
        port_history = sorted(
            (row for row in companies if row["display_name"] in {"OLD PORT", "NEW PORT"}),
            key=lambda row: row["effective_from"],
        )
        self.assertEqual([row["display_name"] for row in port_history], ["OLD PORT", "NEW PORT"])
        self.assertEqual(port_history[0]["effective_to"], port_history[1]["effective_from"])
        self.assertEqual(
            {row["knowledge_from"].isoformat() for row in port_history},
            {"2026-08-02T00:00:00+00:00"},
        )
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

    def test_bse_missing_company_identity_is_recorded_without_blocking(self) -> None:
        org_path = self.ods / "payload" / "canonical" / "cninfo_orgid_map.json"
        org_map = json.loads(org_path.read_text(encoding="utf-8"))
        del org_map["920001"]
        self._write_json(org_path, org_map)
        self._write_plan()

        result = self._build()

        companies = self._rows(result.root, "dwd_listed_company")
        self.assertEqual(
            sum(
                row["resolution_scope"] == "SOURCE_SECURITY_ID:BSE_PARTIAL_NONBLOCKING"
                for row in companies
            ),
            1,
        )
        receipt = json.loads(
            (result.root / "receipts" / "listed-company-build.json").read_text(encoding="utf-8")
        )
        self.assertEqual(receipt["quality_exceptions"][0]["code"], "BSE_COMPANY_IDENTITY_PARTIAL")

    def test_empty_bse_business_coverage_retains_seeded_venue(self) -> None:
        canonical = self.ods / "payload" / "canonical"
        master_path = canonical / "ref_security_master.parquet"
        master_rows = [
            row for row in pq.read_table(master_path).to_pylist() if row["exchange"] != "BSE"
        ]
        pq.write_table(pa.Table.from_pylist(master_rows), master_path)
        stock_path = canonical / "stock_list.parquet"
        stock_rows = [
            row for row in pq.read_table(stock_path).to_pylist() if row["stock_code"] != "920001"
        ]
        pq.write_table(pa.Table.from_pylist(stock_rows), stock_path)
        org_path = canonical / "cninfo_orgid_map.json"
        org_map = json.loads(org_path.read_text(encoding="utf-8"))
        del org_map["920001"]
        self._write_json(org_path, org_map)
        self._write_plan()

        result = self._build()

        self.assertEqual(result.bse_listed_count, 0)
        self.assertEqual(
            {row["mic_code"] for row in self._rows(result.root, "dwd_trading_venue")},
            {"XSHG", "XSHE", "BJSE"},
        )
        receipt = json.loads(
            (result.root / "receipts" / "listed-company-build.json").read_text(encoding="utf-8")
        )
        self.assertEqual(receipt["quality_exceptions"][0]["code"], "BSE_BUSINESS_COVERAGE_EMPTY")

    def test_bse_snapshot_difference_is_a_nonblocking_quality_exception(self) -> None:
        canonical = self.ods / "payload" / "canonical" / "ref_security_master.parquet"
        snapshot = self.ods / "payload" / "weekly" / "test" / "ref_security_master.parquet"
        snapshot.parent.mkdir(parents=True)
        rows = pq.read_table(canonical).to_pylist()
        next(row for row in rows if row["exchange"] == "BSE")["name"] = "STALE BJ NAME"
        pq.write_table(pa.Table.from_pylist(rows), snapshot)
        self._write_plan()

        result = self._build()

        receipt = json.loads(
            (result.root / "receipts" / "listed-company-build.json").read_text(encoding="utf-8")
        )
        exception = next(
            item
            for item in receipt["quality_exceptions"]
            if item["code"] == "BSE_SECURITY_MASTER_SNAPSHOT_DIFFERENCE"
        )
        self.assertEqual(exception["affected_rows"], 1)
        reconciliation = json.loads(
            (result.root / "reconciliation" / "source-row-reconciliation.json").read_text(
                encoding="utf-8"
            )
        )
        weekly = next(
            item
            for item in reconciliation["sources"]
            if item["dataset_id"] == "weekly_test_security_master"
        )
        self.assertEqual(weekly["outcome"], "corroborated_snapshot_with_bse_exception")

    def test_evidenced_code_change_overrides_inherited_successor_list_date(self) -> None:
        canonical = self.ods / "payload" / "canonical"
        master_path = canonical / "ref_security_master.parquet"
        master_rows = pq.read_table(master_path).to_pylist()
        master_rows.extend(
            [
                {
                    "security_id": "sh.600849",
                    "stock_code": "600849",
                    "exchange": "SSE",
                    "name": "OLD PHARMA",
                    "list_date": "1994-03-24",
                    "delist_date": "2010-03-05",
                    "status": "delisted",
                    "bs_code": "sh.600849",
                    "source": "fixture",
                    "as_of": "2026-07-31",
                },
                {
                    "security_id": "sh.601607",
                    "stock_code": "601607",
                    "exchange": "SSE",
                    "name": "NEW PHARMA",
                    "list_date": "1994-03-24",
                    "delist_date": None,
                    "status": "listed",
                    "bs_code": "sh.601607",
                    "source": "fixture",
                    "as_of": "2026-07-31",
                },
            ]
        )
        pq.write_table(pa.Table.from_pylist(master_rows), master_path)
        stock_path = canonical / "stock_list.parquet"
        stock_rows = pq.read_table(stock_path).to_pylist()
        stock_rows.append({"stock_code": "601607"})
        pq.write_table(pa.Table.from_pylist(stock_rows), stock_path)
        org_path = canonical / "cninfo_orgid_map.json"
        org_map = json.loads(org_path.read_text(encoding="utf-8"))
        org_map.update({"600849": "ORG-PHARMA", "601607": "ORG-PHARMA"})
        self._write_json(org_path, org_map)
        validity = json.loads(self.validity.read_text(encoding="utf-8"))
        validity["intervals"].extend(
            [
                {
                    "security_id": "SSE-600849",
                    "stock_code": "600849",
                    "exchange": "SSE",
                    "valid_from": "1994-03-24",
                    "valid_to": "2010-03-04",
                    "source": "fixture exchange notice",
                    "evidence_locator": "fixture://cninfo-57644871",
                    "evidence_sha256": f"sha256:{'c' * 64}",
                    "source_revision": "cninfo-57644871",
                    "knowledge_cutoff": "2010-03-03T23:59:59+08:00",
                },
                {
                    "security_id": "SSE-600849",
                    "stock_code": "601607",
                    "exchange": "SSE",
                    "valid_from": "2010-03-05",
                    "valid_to": None,
                    "source": "fixture exchange notice",
                    "evidence_locator": "fixture://cninfo-57644871",
                    "evidence_sha256": f"sha256:{'c' * 64}",
                    "source_revision": "cninfo-57644871",
                    "knowledge_cutoff": "2010-03-03T23:59:59+08:00",
                },
            ]
        )
        self._write_json(self.validity, validity)
        self._write_plan()

        result = self._build()

        history = sorted(
            (
                row
                for row in self._rows(result.root, "dwd_listed_company")
                if row["display_name"] in {"OLD PHARMA", "NEW PHARMA"}
            ),
            key=lambda row: row["effective_from"],
        )
        self.assertEqual([row["display_name"] for row in history], ["OLD PHARMA", "NEW PHARMA"])
        self.assertEqual(history[0]["effective_to"], history[1]["effective_from"])
        listings = [
            row
            for row in self._rows(result.root, "dwd_listing")
            if row["listing_code"] in {"600849", "601607"}
        ]
        self.assertEqual(len(listings), 2)
        self.assertEqual(
            {row["security_id"] for row in listings},
            {listings[0]["security_id"]},
        )

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
        self.assertEqual(
            {
                row["knowledge_from"].isoformat()
                for table_rows in tables.values()
                for row in table_rows
            },
            {"2026-08-02T00:00:00+00:00"},
        )
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

    def test_plan_package_rejects_extra_entries_and_symbolic_links(self) -> None:
        (self.plan / "unexpected").mkdir()
        with self.assertRaisesRegex(ListedCompanyBuildError, "physical inventory is not exact"):
            self._build()
        (self.plan / "unexpected").rmdir()
        (self.plan / "alias.json").symlink_to(self.plan / "input-plan.json")
        with self.assertRaisesRegex(ListedCompanyBuildError, "physical inventory is not exact"):
            self._build()

    def test_reference_seed_must_not_exceed_plan_cutoff(self) -> None:
        seed = json.loads(REFERENCE_SEED.read_text(encoding="utf-8"))
        seed["source_snapshot"]["retrieved_at"] = "2026-08-03T00:00:00+00:00"
        late_seed = self.root / "late-seed.json"
        self._write_json(late_seed, seed)

        with self.assertRaisesRegex(ListedCompanyBuildError, "reference seed exceeds"):
            self._build(reference_seed=late_seed)

    def test_plan_digest_is_recomputed_after_manifest_verification(self) -> None:
        plan_path = self.plan / "input-plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["plan"]["knowledge_cutoff"] = "2026-07-31T23:59:59+00:00"
        self._write_json(plan_path, plan)
        manifest_path = self.plan / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for item in manifest["files"]:
            path = self.plan / item["path"]
            item["sha256"] = _sha256(path)
            item["size_bytes"] = path.stat().st_size
        self._write_json(manifest_path, manifest)
        (self.plan / "READY").write_text(f"{_sha256(manifest_path)}\n", encoding="ascii")

        with self.assertRaisesRegex(ListedCompanyBuildError, "plan digest differs"):
            self._build()

    def test_code_validity_requires_evidence_and_knowledge_contract(self) -> None:
        validity = json.loads(self.validity.read_text(encoding="utf-8"))
        del validity["intervals"][0]["evidence_sha256"]
        self._write_json(self.validity, validity)

        with self.assertRaisesRegex(
            ListedCompanyBuildError, "invalid security-code validity mapping"
        ):
            self._build()

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

    def test_existing_output_rejects_undeclared_physical_files(self) -> None:
        result = self._build()
        (result.root / "unexpected.tmp").write_text("stale\n", encoding="ascii")

        with self.assertRaisesRegex(ListedCompanyBuildError, "physical file inventory"):
            self._build()

    def test_clean_rebuild_has_identical_manifest_and_receipt_bytes(self) -> None:
        first = self._build(self.root / "candidate-one")
        second = self._build(self.root / "candidate-two")

        self.assertEqual(
            (first.root / "manifest.json").read_bytes(),
            (second.root / "manifest.json").read_bytes(),
        )
        self.assertEqual(
            (first.root / "receipts" / "listed-company-build.json").read_bytes(),
            (second.root / "receipts" / "listed-company-build.json").read_bytes(),
        )

    def test_temporal_fk_and_overlap_gates_fail_closed(self) -> None:
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
        tables["dwd_security_identifier"][0]["venue_id"] = "missing-venue"
        with self.assertRaisesRegex(ListedCompanyBuildError, "temporal foreign key"):
            listed_company_module._validate_rows(tables)

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
        duplicate = dict(tables["dwd_listed_company"][0])
        duplicate["knowledge_from"] = duplicate["knowledge_from"].replace(microsecond=1)
        tables["dwd_listed_company"].append(duplicate)
        with self.assertRaisesRegex(ListedCompanyBuildError, "bitemporal versions overlap"):
            listed_company_module._validate_rows(tables)

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
        work = self._work()
        checkpoint = json.loads((work / "checkpoint.json").read_text(encoding="utf-8"))
        self.assertEqual(list(checkpoint["completed_tables"]), ["dwd_listed_company"])
        (work / ".checkpoint.json.orphan").write_text("partial", encoding="ascii")
        partial = work / "payload" / "listed_company" / "dwd_security.parquet.partial"
        partial.write_text("partial", encoding="ascii")

        result = self._build()

        self.assertTrue((result.root / "READY").is_file())
        self.assertFalse(work.exists())

    def test_retry_recovers_after_ready_before_directory_publish(self) -> None:
        real_replace = listed_company_module.os.replace

        def interrupt_publish(source: object, destination: object) -> None:
            if Path(source).name.endswith(".work") and Path(destination) == self.output:
                raise OSError("fixture publish interruption")
            real_replace(source, destination)

        with (
            mock.patch.object(listed_company_module.os, "replace", side_effect=interrupt_publish),
            self.assertRaisesRegex(OSError, "fixture publish interruption"),
        ):
            self._build()
        work = self._work()
        self.assertTrue((work / "READY").is_file())

        result = self._build()

        self.assertTrue((result.root / "READY").is_file())
        self.assertFalse(work.exists())

    def test_retry_initializes_after_interruption_before_first_checkpoint(self) -> None:
        real_write = listed_company_module._write_json_atomic
        interrupted = False

        def interrupt_initial_checkpoint(path: Path, value: object) -> None:
            nonlocal interrupted
            if path.name == "checkpoint.json" and not interrupted:
                interrupted = True
                raise OSError("fixture initial checkpoint interruption")
            real_write(path, value)

        with (
            mock.patch.object(
                listed_company_module,
                "_write_json_atomic",
                side_effect=interrupt_initial_checkpoint,
            ),
            self.assertRaisesRegex(OSError, "fixture initial checkpoint interruption"),
        ):
            self._build()
        work = self._work()
        self.assertTrue(work.is_dir())
        self.assertFalse((work / "checkpoint.json").exists())
        (work / ".checkpoint.json.interrupted").write_text("partial", encoding="ascii")

        result = self._build()

        self.assertTrue((result.root / "READY").is_file())
        self.assertFalse(work.exists())

    def test_different_run_does_not_clean_existing_run_temporaries(self) -> None:
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
        first_work = self._work()
        orphan = first_work / ".checkpoint.json.orphan"
        orphan.write_text("partial", encoding="ascii")

        self._set_plan_run_id("listed-company-fixture-v2")
        result = self._build()

        self.assertTrue((result.root / "READY").is_file())
        self.assertTrue(first_work.is_dir())
        self.assertEqual(orphan.read_text(encoding="ascii"), "partial")

    def test_concurrent_same_semantic_builds_are_serialized_and_idempotent(self) -> None:
        real_build = listed_company_module._build_listed_company_locked
        guard = threading.Lock()
        active = 0
        max_active = 0

        def observed_build(**kwargs: object):
            nonlocal active, max_active
            with guard:
                active += 1
                max_active = max(max_active, active)
            try:
                time.sleep(0.05)
                return real_build(**kwargs)
            finally:
                with guard:
                    active -= 1

        with (
            mock.patch.object(
                listed_company_module,
                "_build_listed_company_locked",
                side_effect=observed_build,
            ),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            results = list(executor.map(lambda _index: self._build(), range(2)))

        self.assertEqual(max_active, 1)
        self.assertEqual(results[0].semantic_digest, results[1].semantic_digest)
        self.assertTrue((self.output / "READY").is_file())

    def test_concurrent_different_semantics_fail_before_mixing_payloads(self) -> None:
        alternative_validity = self.root / "security-code-validity-alternative.json"
        validity = json.loads(self.validity.read_text(encoding="utf-8"))
        validity["source_revision"] = "fixture-code-validity-source-v2"
        self._write_json(alternative_validity, validity)

        def build(validity_path: Path):
            try:
                return validity_path, self._build(code_validity=validity_path)
            except ListedCompanyBuildError as exc:
                return validity_path, exc

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(build, (self.validity, alternative_validity)))

        results = [value for value in outcomes if not isinstance(value[1], Exception)]
        errors = [value for value in outcomes if isinstance(value[1], Exception)]
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIn("another run or semantic build", str(errors[0][1]))
        winning_validity, winner = results[0]
        self.assertEqual(
            self._build(code_validity=winning_validity).semantic_digest,
            winner.semantic_digest,
        )

    def test_rehashed_manifest_cannot_hide_semantic_payload_tampering(self) -> None:
        result = self._build()
        table_path = result.root / "payload" / "listed_company" / "dwd_listed_company.parquet"
        table = pq.read_table(table_path)
        rows = table.to_pylist()
        rows[0]["display_name"] = "TAMPERED COMPANY"
        pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), table_path)
        checkpoint_path = result.root / "checkpoint.json"
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        checkpoint["completed_tables"]["dwd_listed_company"]["sha256"] = _sha256(table_path)
        self._write_json(checkpoint_path, checkpoint)
        self._resign_manifest(result.root)

        with self.assertRaisesRegex(ListedCompanyBuildError, "semantic verification failed"):
            self._build()

    def test_rehashed_manifest_cannot_hide_physical_schema_tampering(self) -> None:
        result = self._build()
        table_path = result.root / "payload" / "listed_company" / "dwd_listed_company.parquet"
        table = pq.read_table(table_path)
        field_index = table.schema.get_field_index("display_name")
        fields = list(table.schema)
        fields[field_index] = pa.field("display_name", pa.large_string(), nullable=False)
        arrays = list(table.columns)
        arrays[field_index] = arrays[field_index].cast(pa.large_string())
        pq.write_table(pa.Table.from_arrays(arrays, schema=pa.schema(fields)), table_path)
        checkpoint_path = result.root / "checkpoint.json"
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        checkpoint["completed_tables"]["dwd_listed_company"]["sha256"] = _sha256(table_path)
        self._write_json(checkpoint_path, checkpoint)
        self._resign_manifest(result.root)

        with self.assertRaisesRegex(ListedCompanyBuildError, "semantic verification failed"):
            self._build()

    def test_rehashed_manifest_cannot_hide_receipt_tampering(self) -> None:
        result = self._build()
        receipt_path = result.root / "receipts" / "listed-company-build.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["status"] = "failed"
        receipt["coverage"]["primary_sse_szse_listed"] = 999
        receipt["quality_exceptions"] = [{"reason_code": "INVENTED"}]
        self._write_json(receipt_path, receipt)
        self._resign_manifest(result.root)

        with self.assertRaisesRegex(ListedCompanyBuildError, "receipt identity is inconsistent"):
            self._build()

    def test_ready_binds_manifest_and_receipt_input_plan_identity(self) -> None:
        result = self._build()
        manifest_path = result.root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["input_plan_digest"] = f"sha256:{'f' * 64}"
        self._write_json(manifest_path, manifest)
        (result.root / "READY").write_text(f"{_sha256(manifest_path)}\n", encoding="ascii")

        with self.assertRaisesRegex(ListedCompanyBuildError, "receipt identity is inconsistent"):
            self._build()


if __name__ == "__main__":
    unittest.main()

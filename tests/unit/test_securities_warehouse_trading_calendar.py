from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pyarrow as pa
import pyarrow.parquet as pq

from quanthus.pipelines.securities_warehouse import listed_company as listed_company_module
from quanthus.pipelines.securities_warehouse.trading_calendar import (
    TradingCalendarBuildError,
    build_trading_calendar,
)

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "config/pipelines/dwd-domain-model.v1.json"
UPSTREAM_TABLES = (
    "dwd_listed_company",
    "dwd_company_identifier",
    "dwd_security",
    "dwd_security_identifier",
    "dwd_listing",
    "dwd_trading_venue",
)


def _sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _normalize(value: object) -> object:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in sorted(value.items())}
    if isinstance(value, list | tuple):
        return [_normalize(item) for item in value]
    return value


def _digest(value: object) -> str:
    encoded = json.dumps(
        _normalize(value), ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )
    return f"sha256:{hashlib.sha256(encoded.encode('ascii')).hexdigest()}"


class TradingCalendarBuildTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.ods = self.root / "ods-base-v1.1"
        self.plan = self.root / "input-plan"
        self.upstream = self.root / "listed-company-candidate"
        self.output = self.root / "calendar-candidate"
        self.model = self.root / "dwd-domain-model.v1.json"
        self.run_id = "warehouse-base-fixture-v1"
        self.cutoff = datetime(2026, 8, 2, tzinfo=timezone.utc)
        self.bse_effective_from = datetime(1990, 1, 1, tzinfo=timezone.utc)
        self.through_date = "2026-01-04"
        self.records = self._write_calendar_sources()
        self._write_ods_contract()
        self._write_plan()
        self._write_upstream()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _calendar_rows(
        self, through: date, built_at: datetime, *, conflict: bool = False
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        current = date(2026, 1, 1)
        while current <= through:
            for exchange in ("SSE", "SZSE", "BSE"):
                is_open = int(current.weekday() < 5)
                if conflict and exchange == "SSE" and current == date(2026, 1, 2):
                    is_open = 0
                rows.append(
                    {
                        "exchange": exchange,
                        "trade_date": current,
                        "is_open": is_open,
                        "session": "day",
                        "source": "fixture.trade_dates",
                        "built_at": built_at,
                    }
                )
            current += timedelta(days=1)
        return rows

    def _write_ods_contract(self) -> None:
        bundle_id = "ods-base-fixture-v1.1"
        content_digest = _digest(
            {"bundle_id": bundle_id, "through_date": self.through_date}
        )
        self._write_json(
            self.ods / "manifest.json",
            {
                "schema_version": 1,
                "bundle_id": bundle_id,
                "through_date": self.through_date,
                "content_digest": content_digest,
            },
        )
        manifest_sha = _sha256(self.ods / "manifest.json")
        model = json.loads(MODEL.read_text(encoding="utf-8"))
        model["build_input_contract"].update(
            {
                "bundle_id": bundle_id,
                "manifest_sha256": manifest_sha,
                "content_digest": content_digest,
                "through_date": self.through_date,
            }
        )
        self._write_json(self.model, model)
        self.ods_identity = {
            "bundle_id": bundle_id,
            "content_digest": content_digest,
            "file_count": len(self.records),
            "kind": "ods_base_rollup",
            "manifest_sha256": manifest_sha,
            "through_date": self.through_date,
            "verification_mode": "deep",
            "verified_bytes": sum(int(record["size_bytes"]) for record in self.records),
        }

    def _write_calendar_sources(self) -> list[dict[str, object]]:
        specifications = (
            (
                "trading_calendar",
                "payload/canonical/ref_trading_calendar.parquet",
                date(2026, 1, 3),
                datetime(2026, 1, 3, 8, tzinfo=timezone.utc),
                False,
            ),
            (
                "daily_intraday_1m_trading_calendar",
                "payload/daily/2026-01-04/intraday_1m/artifacts/"
                "ref_trading_calendar.parquet",
                date(2026, 1, 4),
                datetime(2026, 1, 4, 8, tzinfo=timezone.utc),
                False,
            ),
            (
                "daily_intraday_5m_trading_calendar",
                "payload/daily/2026-01-04/intraday_5m/artifacts/"
                "ref_trading_calendar.parquet",
                date(2026, 1, 4),
                datetime(2026, 1, 4, 9, tzinfo=timezone.utc),
                False,
            ),
            (
                "daily_market_trading_calendar",
                "payload/daily/2026-01-04/market/artifacts/"
                "ref_trading_calendar.parquet",
                date(2026, 1, 4),
                datetime(2026, 1, 4, 7, tzinfo=timezone.utc),
                False,
            ),
        )
        records: list[dict[str, object]] = []
        for dataset_id, relative, through, built_at, conflict in specifications:
            path = self.ods / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            rows = self._calendar_rows(through, built_at, conflict=conflict)
            pq.write_table(pa.Table.from_pylist(rows), path)
            records.append(
                {
                    "dataset_id": dataset_id,
                    "record_type": "file",
                    "disposition": "modeled",
                    "mapping_identity": "trading_calendar",
                    "path": relative,
                    "sha256": _sha256(path),
                    "size_bytes": path.stat().st_size,
                    "target_tables": [
                        "dwd_trading_venue",
                        "dwd_trading_calendar",
                        "dwd_tradability_state",
                    ],
                }
            )
        return records

    def _seal_package(
        self,
        root: Path,
        *,
        kind: str,
        files: list[str],
        extra_manifest: dict[str, object],
    ) -> None:
        manifest = {
            "schema_version": 1,
            "kind": kind,
            "files": [
                {
                    "path": relative,
                    "sha256": _sha256(root / relative),
                    "size_bytes": (root / relative).stat().st_size,
                }
                for relative in sorted(files)
            ],
            **extra_manifest,
        }
        self._write_json(root / "manifest.json", manifest)
        (root / "READY").write_text(
            f"{_sha256(root / 'manifest.json')}\n", encoding="ascii"
        )

    def _write_plan(self) -> None:
        ledger = {"schema_version": 1, "records": self.records}
        semantic_plan = {
            "schema_version": 1,
            "mode": "base",
            "knowledge_cutoff": self.cutoff.isoformat(),
            "input": self.ods_identity,
            "model": {
                "contract_id": json.loads(self.model.read_text(encoding="utf-8"))[
                    "contract_id"
                ],
                "sha256": _sha256(self.model),
            },
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
        self._write_json(self.plan / "source-disposition-ledger.json", ledger)
        for name in ("capacity-estimate.json", "coverage-matrix.json"):
            self._write_json(self.plan / name, {"schema_version": 1})
        self._write_json(
            self.plan / "run-receipt.json",
            {
                "schema_version": 1,
                "kind": "dwd_dws_input_plan_receipt",
                "status": "planned",
                "run_id": self.run_id,
                "semantic_digest": plan_digest,
                "schema_digest": _sha256(self.model),
                "input_manifest_sha256": self.ods_identity["manifest_sha256"],
            },
        )
        files = [
            "capacity-estimate.json",
            "coverage-matrix.json",
            "input-plan.json",
            "run-receipt.json",
            "source-disposition-ledger.json",
        ]
        self._seal_package(
            self.plan,
            kind="dwd_dws_input_plan_package",
            files=files,
            extra_manifest={"plan_digest": plan_digest},
        )

    def _write_upstream(self) -> None:
        payload = self.upstream / "payload/listed_company"
        payload.mkdir(parents=True, exist_ok=True)
        venue_rows = []
        for exchange, mic, venue_id in (
            ("SSE", "XSHG", "venue_sse"),
            ("SZSE", "XSHE", "venue_szse"),
            ("BSE", "BJSE", "venue_bse"),
        ):
            venue_rows.append(
                {
                    "record_source": "fixture.seed",
                    "source_record_id": f"fixture#exchange_code={exchange}",
                    "source_revision": "fixture-v1",
                    "source_artifact_sha256": f"sha256:{'a' * 64}",
                    "knowledge_from": self.cutoff,
                    "knowledge_to": None,
                    "effective_from": (
                        self.bse_effective_from
                        if exchange == "BSE"
                        else datetime(1990, 1, 1, tzinfo=timezone.utc)
                    ),
                    "effective_to": None,
                    "venue_id": venue_id,
                    "mic_code": mic,
                    "venue_name": exchange,
                    "country_code": "CN",
                    "timezone": "Asia/Shanghai",
                }
            )
        table_digests: dict[str, str] = {}
        row_counts: dict[str, int] = {}
        checkpoint_tables: dict[str, dict[str, object]] = {}
        schemas = listed_company_module._table_schemas()
        for table in UPSTREAM_TABLES:
            path = payload / f"{table}.parquet"
            if table == "dwd_trading_venue":
                value = pa.Table.from_pylist(venue_rows, schema=schemas[table])
            else:
                value = pa.Table.from_pylist([], schema=schemas[table])
            pq.write_table(value, path)
            table_digests[table] = _digest(value.to_pylist())
            row_counts[table] = value.num_rows
            checkpoint_tables[table] = {
                "path": f"payload/listed_company/{table}.parquet",
                "row_count": value.num_rows,
                "schema_digest": _digest(str(value.schema)),
                "semantic_digest": table_digests[table],
                "sha256": _sha256(path),
            }
        reference_seed_sha = f"sha256:{'b' * 64}"
        code_validity_sha = f"sha256:{'c' * 64}"
        plan_digest = json.loads((self.plan / "input-plan.json").read_text())["plan_digest"]
        semantic_digest = _digest(
            {
                "input_plan_digest": plan_digest,
                "model_sha256": _sha256(self.model),
                "reference_seed_sha256": reference_seed_sha,
                "code_validity_sha256": code_validity_sha,
                "table_digests": table_digests,
            }
        )
        receipt = {
            "schema_version": 1,
            "kind": "dwd_listed_company_build_receipt",
            "status": "built",
            "run_id": self.run_id,
            "input_plan_digest": plan_digest,
            "model_sha256": _sha256(self.model),
            "reference_seed_sha256": reference_seed_sha,
            "code_validity_sha256": code_validity_sha,
            "table_digests": table_digests,
            "row_counts": row_counts,
            "semantic_digest": semantic_digest,
        }
        self._write_json(self.upstream / "receipts/listed-company-build.json", receipt)
        self._write_json(
            self.upstream / "checkpoint.json",
            {
                "schema_version": 1,
                "run_id": self.run_id,
                "semantic_digest": semantic_digest,
                "completed_tables": checkpoint_tables,
            },
        )
        self._write_json(
            self.upstream / "reconciliation/source-row-reconciliation.json",
            {"schema_version": 1, "input_plan_digest": plan_digest},
        )
        files = [
            "checkpoint.json",
            "receipts/listed-company-build.json",
            "reconciliation/source-row-reconciliation.json",
            *[f"payload/listed_company/{table}.parquet" for table in UPSTREAM_TABLES],
        ]
        self._seal_package(
            self.upstream,
            kind="dwd_listed_company_candidate",
            files=files,
            extra_manifest={
                "run_id": self.run_id,
                "input_plan_digest": plan_digest,
                "owned_tables": list(UPSTREAM_TABLES),
                "row_counts": row_counts,
                "semantic_digest": semantic_digest,
            },
        )

    def _build(self, output: Path | None = None):
        return build_trading_calendar(
            ods_package=self.ods,
            input_plan=self.plan,
            model_path=self.model,
            upstream_candidate=self.upstream,
            output=output or self.output,
            run_id=self.run_id,
        )

    def test_builds_latest_market_snapshot_with_stable_session_coordinates(self) -> None:
        result = self._build()
        self.assertEqual(result.row_count, 12)
        self.assertEqual(result.year_count, 1)
        self.assertIn("/market/", result.selected_source)
        partition = (
            self.output
            / "payload/tradability_state/dwd_trading_calendar/"
            "trade_date_year=2026/part-00000.parquet"
        )
        rows = pq.ParquetFile(partition).read().to_pylist()
        self.assertEqual(
            {row["venue_id"] for row in rows},
            {"venue_sse", "venue_szse", "venue_bse"},
        )
        open_row = next(
            row
            for row in rows
            if row["venue_id"] == "venue_sse" and row["trade_date"] == date(2026, 1, 2)
        )
        self.assertTrue(open_row["is_open"])
        self.assertEqual(open_row["open_at"], datetime(2026, 1, 2, 1, 30, tzinfo=timezone.utc))
        self.assertEqual(open_row["close_at"], datetime(2026, 1, 2, 7, tzinfo=timezone.utc))
        self.assertEqual(open_row["knowledge_from"], self.cutoff)
        closed_row = next(
            row
            for row in rows
            if row["venue_id"] == "venue_sse" and row["trade_date"] == date(2026, 1, 4)
        )
        self.assertFalse(closed_row["is_open"])
        self.assertIsNone(closed_row["open_at"])
        self.assertEqual(closed_row["calendar_reason"], "SCHEDULED_CLOSED")

    def test_output_is_idempotent_and_clean_rebuild_is_byte_identical(self) -> None:
        first = self._build()
        first_files = {
            path.relative_to(self.output).as_posix(): path.read_bytes()
            for path in self.output.rglob("*")
            if path.is_file()
        }
        second = self._build()
        rebuilt_output = self.root / "calendar-candidate-rebuilt"
        rebuilt = self._build(rebuilt_output)
        rebuilt_files = {
            path.relative_to(rebuilt_output).as_posix(): path.read_bytes()
            for path in rebuilt_output.rglob("*")
            if path.is_file()
        }
        self.assertEqual(first.semantic_digest, second.semantic_digest)
        self.assertEqual(first.semantic_digest, rebuilt.semantic_digest)
        self.assertEqual(first_files, rebuilt_files)

    def test_rejects_conflicting_same_date_snapshot(self) -> None:
        record = next(
            item
            for item in self.records
            if item["dataset_id"] == "daily_intraday_5m_trading_calendar"
        )
        path = self.ods / str(record["path"])
        pq.write_table(
            pa.Table.from_pylist(
                self._calendar_rows(
                    date(2026, 1, 4),
                    datetime(2026, 1, 4, 9, tzinfo=timezone.utc),
                    conflict=True,
                )
            ),
            path,
        )
        record["sha256"] = _sha256(path)
        record["size_bytes"] = path.stat().st_size
        self._write_plan()
        self._write_upstream()
        with self.assertRaisesRegex(TradingCalendarBuildError, "snapshots conflict"):
            self._build()

    def test_lineage_source_difference_does_not_create_business_conflict(self) -> None:
        record = next(
            item
            for item in self.records
            if item["dataset_id"] == "daily_intraday_5m_trading_calendar"
        )
        path = self.ods / str(record["path"])
        rows = self._calendar_rows(
            date(2026, 1, 4),
            datetime(2026, 1, 4, 9, tzinfo=timezone.utc),
        )
        for row in rows:
            row["source"] = "fixture.corrobating_source"
        pq.write_table(pa.Table.from_pylist(rows), path)
        record["sha256"] = _sha256(path)
        record["size_bytes"] = path.stat().st_size
        self._write_plan()
        self._write_upstream()
        self.assertEqual(self._build().row_count, 12)

    def test_rejects_upstream_from_another_run_even_when_resealed(self) -> None:
        receipt_path = self.upstream / "receipts/listed-company-build.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["run_id"] = "another-run"
        self._write_json(receipt_path, receipt)
        manifest_path = self.upstream / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["run_id"] = "another-run"
        for item in manifest["files"]:
            if item["path"] == "receipts/listed-company-build.json":
                item["sha256"] = _sha256(receipt_path)
                item["size_bytes"] = receipt_path.stat().st_size
        self._write_json(manifest_path, manifest)
        (self.upstream / "READY").write_text(
            f"{_sha256(manifest_path)}\n", encoding="ascii"
        )
        with self.assertRaisesRegex(TradingCalendarBuildError, "deep verification"):
            self._build()

    def test_rejects_resealed_upstream_table_with_wrong_physical_schema(self) -> None:
        table_path = self.upstream / "payload/listed_company/dwd_security.parquet"
        invalid_table = pa.table({"placeholder": pa.array([], type=pa.string())})
        pq.write_table(invalid_table, table_path)
        checkpoint_path = self.upstream / "checkpoint.json"
        checkpoint = json.loads(checkpoint_path.read_text())
        checkpoint["completed_tables"]["dwd_security"].update(
            {
                "schema_digest": _digest(str(invalid_table.schema)),
                "sha256": _sha256(table_path),
            }
        )
        self._write_json(checkpoint_path, checkpoint)
        manifest_path = self.upstream / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        for item in manifest["files"]:
            path = self.upstream / item["path"]
            if item["path"] in {
                "checkpoint.json",
                "payload/listed_company/dwd_security.parquet",
            }:
                item["sha256"] = _sha256(path)
                item["size_bytes"] = path.stat().st_size
        self._write_json(manifest_path, manifest)
        (self.upstream / "READY").write_text(
            f"{_sha256(manifest_path)}\n", encoding="ascii"
        )
        with self.assertRaisesRegex(TradingCalendarBuildError, "semantic verification"):
            self._build()

    def test_rejects_ods_manifest_outside_the_sealed_plan(self) -> None:
        self._write_json(self.ods / "manifest.json", {"tampered": True})
        with self.assertRaisesRegex(TradingCalendarBuildError, "manifest differs"):
            self._build()

    def test_existing_candidate_semantics_are_reverified(self) -> None:
        self._build()
        partition = (
            self.output
            / "payload/tradability_state/dwd_trading_calendar/"
            "trade_date_year=2026/part-00000.parquet"
        )
        rows = pq.ParquetFile(partition).read().to_pylist()
        rows[0]["calendar_reason"] = "TAMPERED"
        pq.write_table(pa.Table.from_pylist(rows), partition)
        manifest_path = self.output / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        relative = partition.relative_to(self.output).as_posix()
        for item in manifest["files"]:
            if item["path"] == relative:
                item["sha256"] = _sha256(partition)
                item["size_bytes"] = partition.stat().st_size
        self._write_json(manifest_path, manifest)
        (self.output / "READY").write_text(
            f"{_sha256(manifest_path)}\n", encoding="ascii"
        )
        with self.assertRaises(TradingCalendarBuildError):
            self._build()

    def test_bse_pre_venue_rows_are_explicitly_excluded(self) -> None:
        self.bse_effective_from = datetime(2026, 1, 2, 16, tzinfo=timezone.utc)
        self._write_upstream()
        result = self._build()
        self.assertEqual(result.row_count, 10)
        receipt = json.loads(
            (self.output / "receipts/trading-calendar-build.json").read_text()
        )
        self.assertEqual(
            receipt["coverage_exceptions"],
            [
                {
                    "code": "BSE_CALENDAR_PRE_VENUE_EFFECTIVE_RANGE",
                    "policy": "BSE_PARTIAL_NONBLOCKING",
                    "affected_rows": 2,
                    "minimum_trade_date": "2026-01-01",
                    "maximum_trade_date": "2026-01-02",
                    "resolution": "excluded_noneligible_pre_venue_rows",
                }
            ],
        )

    def test_resumes_from_verified_year_checkpoint(self) -> None:
        for record in self.records:
            path = self.ods / str(record["path"])
            is_canonical = record["dataset_id"] == "trading_calendar"
            dates = [date(2025, 12, 31)]
            if not is_canonical:
                dates.append(date(2026, 1, 1))
            rows = [
                {
                    "exchange": exchange,
                    "trade_date": trade_date,
                    "is_open": 1,
                    "session": "day",
                    "source": "fixture.trade_dates",
                    "built_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
                }
                for trade_date in dates
                for exchange in ("SSE", "SZSE", "BSE")
            ]
            pq.write_table(pa.Table.from_pylist(rows), path)
            record["sha256"] = _sha256(path)
            record["size_bytes"] = path.stat().st_size
        self.through_date = "2026-01-01"
        self._write_ods_contract()
        self._write_plan()
        self._write_upstream()

        original_write_table = pq.write_table
        calls = 0

        def fail_second_partition(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected second-year failure")
            return original_write_table(*args, **kwargs)

        with (
            mock.patch.object(pq, "write_table", side_effect=fail_second_partition),
            self.assertRaisesRegex(OSError, "second-year"),
        ):
            self._build()
        self.assertFalse(self.output.exists())
        work = next(self.root.glob(".calendar-candidate.*.work"))
        (work / ".checkpoint.json.orphan").write_text("orphan", encoding="ascii")
        partial = (
            work
            / "payload/tradability_state/dwd_trading_calendar/"
            "trade_date_year=2026/part-00000.parquet.partial"
        )
        partial.write_bytes(b"orphan")
        result = self._build()
        self.assertEqual(result.year_count, 2)
        self.assertEqual(result.row_count, 6)

    def test_rejects_symbolic_link_lock(self) -> None:
        target = self.root / "unrelated-lock-target"
        target.write_bytes(b"")
        lock = self.output.parent / f".{self.output.name}.lock"
        lock.symlink_to(target)
        with self.assertRaisesRegex(TradingCalendarBuildError, "cannot open build lock"):
            self._build()


if __name__ == "__main__":
    unittest.main()

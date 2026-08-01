from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from quanthus.pipelines.securities_warehouse.planner import (
    WarehousePlanningError,
    _base_records,
    _capacity,
    _model_contract,
    _validate_base_coverage,
    build_input_plan,
)

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "config" / "pipelines" / "dwd-domain-model.v1.json"


def _sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


class SecuritiesWarehousePlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _release(
        self,
        name: str,
        *,
        schedule: str,
        artifacts: list[str],
    ) -> Path:
        release = self.root / name
        values = []
        for artifact in artifacts:
            target = release / "artifacts" / f"{artifact}.parquet"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(f"fixture:{artifact}\n".encode("ascii"))
            values.append(
                {
                    "knowledge_cutoff": "2026-08-01T00:00:00+00:00",
                    "name": artifact,
                    "relative_path": f"artifacts/{artifact}.parquet",
                    "sha256": _sha256(target),
                    "size_bytes": target.stat().st_size,
                    "source_revision": f"fixture-{artifact}-v1",
                }
            )
        manifest = {
            "schema_version": 1,
            "release_id": name,
            "schedule": schedule,
            "market_date": "2026-07-31",
            "created_at": "2026-08-01T00:01:00+00:00",
            "producer": "planner-fixture",
            "status": "ready",
            "artifacts": values,
        }
        self._write_json(release / "release.json", manifest)
        (release / "READY").write_text(f"{_sha256(release / 'release.json')}\n", encoding="ascii")
        return release

    def _build(self, release: Path, output_name: str, run_id: str, *, mode: str = "daily"):
        return build_input_plan(
            ods_package=release,
            model_path=MODEL,
            output=self.root / output_name,
            mode=mode,
            run_id=run_id,
            knowledge_cutoff="2026-08-01T00:00:00Z",
            code_revision="a" * 40,
            runtime_id="fixture-runtime-v1",
            market_date="2026-07-31",
        )

    def test_daily_plan_is_sealed_and_semantic_digest_ignores_run_id(self) -> None:
        release = self._release(
            "daily-release-v1",
            schedule="daily",
            artifacts=["daily_ohlcv_none_delta", "security_master", "lane_status"],
        )
        first = self._build(release, "plan-a", "run-a")
        second = self._build(release, "plan-b", "run-b")

        self.assertEqual(first.plan_digest, second.plan_digest)
        self.assertEqual(first.file_count, 3)
        self.assertEqual(first.source_record_count, 3)
        self.assertTrue((first.root / "READY").is_file())
        self.assertTrue((first.root / "run-receipt.json").is_file())
        first_plan = json.loads((first.root / "input-plan.json").read_text(encoding="utf-8"))
        second_plan = json.loads((second.root / "input-plan.json").read_text(encoding="utf-8"))
        self.assertEqual(first_plan["run_id"], "run-a")
        self.assertEqual(second_plan["run_id"], "run-b")
        self.assertEqual(first_plan["plan"], second_plan["plan"])
        self.assertEqual(
            first_plan["plan"]["domain_plan"]["affected_dwd_domains"],
            ["listed_company", "security_market_observation", "tradability_state"],
        )
        receipt = json.loads((first.root / "run-receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["semantic_digest"], first.plan_digest)
        self.assertEqual(receipt["row_counts"]["source_records"], 3)
        self.assertEqual(receipt["quality_exceptions"], [])

    def test_weekly_aliases_select_only_affected_domains(self) -> None:
        release = self._release(
            "weekly-release-v1",
            schedule="weekly",
            artifacts=[
                "financial_quarterly_revision",
                "shareholder_count_revision",
                "reconciliation_summary",
            ],
        )
        result = self._build(release, "weekly-plan", "weekly-run", mode="weekly")
        plan = json.loads((result.root / "input-plan.json").read_text(encoding="utf-8"))
        self.assertEqual(
            plan["plan"]["domain_plan"]["affected_dwd_domains"],
            ["financial_reporting", "ownership_capital"],
        )

    def test_unclassified_release_artifact_fails_closed(self) -> None:
        release = self._release(
            "unknown-release-v1",
            schedule="daily",
            artifacts=["unknown_business_payload"],
        )
        with self.assertRaisesRegex(WarehousePlanningError, "unclassified"):
            self._build(release, "unknown-plan", "unknown-run")
        self.assertFalse((self.root / "unknown-plan").exists())

    def test_tampered_release_artifact_fails_closed(self) -> None:
        release = self._release(
            "tampered-release-v1",
            schedule="daily",
            artifacts=["daily_ohlcv_none_delta"],
        )
        (release / "artifacts" / "daily_ohlcv_none_delta.parquet").write_bytes(b"tampered")
        with self.assertRaisesRegex(WarehousePlanningError, "size mismatch"):
            self._build(release, "tampered-plan", "tampered-run")

    def test_existing_output_is_immutable(self) -> None:
        release = self._release(
            "immutable-release-v1",
            schedule="daily",
            artifacts=["daily_ohlcv_none_delta"],
        )
        first = self._build(release, "immutable-plan", "first-run")
        manifest_mtime = (first.root / "manifest.json").stat().st_mtime_ns
        retry = self._build(release, "immutable-plan", "first-run")
        self.assertEqual(retry.plan_digest, first.plan_digest)
        self.assertEqual((first.root / "manifest.json").stat().st_mtime_ns, manifest_mtime)
        with self.assertRaisesRegex(WarehousePlanningError, "different run or plan"):
            self._build(release, "immutable-plan", "second-run")

    def test_non_base_mode_rejects_an_ods_base_before_verification(self) -> None:
        package = self.root / "base"
        self._write_json(package / "manifest.json", {})

        with self.assertRaisesRegex(WarehousePlanningError, "requires an ODS release"):
            build_input_plan(
                ods_package=package,
                model_path=MODEL,
                output=self.root / "invalid-plan",
                mode="correction",
                run_id="correction-run",
                knowledge_cutoff="2026-08-01T00:00:00Z",
                code_revision="a" * 40,
                runtime_id="fixture-runtime-v1",
            )

    def test_model_contract_enforces_object_role_counts(self) -> None:
        model = json.loads(MODEL.read_text(encoding="utf-8"))
        model["tables"][0]["object_role"] = "supporting_structure"
        invalid_model = self.root / "invalid-model.json"
        self._write_json(invalid_model, model)

        with self.assertRaisesRegex(WarehousePlanningError, "14 core objects"):
            _model_contract(invalid_model)

    def test_base_coverage_requires_every_domain_object_and_governance(self) -> None:
        coverage = {
            "domains": [
                {
                    "domain": "listed_company",
                    "missing_source_tables": ["dwd_listing"],
                }
            ]
        }
        domain_plan = {
            "affected_dwd_domains": ["listed_company"],
            "dwd_domain_order": ["listed_company", "security_market_observation"],
            "build_governance_sidecar": False,
        }

        with self.assertRaisesRegex(WarehousePlanningError, "does not cover"):
            _validate_base_coverage(coverage, domain_plan)

    def test_base_records_expand_sqlite_and_classify_embedded_release_members(self) -> None:
        model, _ = _model_contract(MODEL)
        sqlite_tables = [
            "announcements",
            "crawl_progress",
            "fin_abstract",
            "fin_progress",
            "holder_changes",
            "holder_progress",
            "info_meta",
            "shareholder_count",
            "shareholder_count_progress",
            "shareholder_count_revision",
        ]
        files = [
            {
                "dataset_id": "announcement_archive",
                "format": "sqlite",
                "path": "payload/canonical/info_archive.db",
                "role": "source_evidence",
                "sha256": "sha256:" + "1" * 64,
                "size_bytes": 100,
                "tables": [
                    {
                        "name": name,
                        "row_count": index,
                        "schema_sha256": "sha256:" + f"{index + 1:064x}",
                    }
                    for index, name in enumerate(sqlite_tables)
                ],
            },
            {
                "dataset_id": "source_evidence_weekly_full_fundamentals_20260731",
                "format": "parquet",
                "path": "payload/source-evidence/weekly/artifacts/financial_statements.parquet",
                "role": "retained_immutable_source_gap_fill",
                "sha256": "sha256:" + "2" * 64,
                "size_bytes": 200,
            },
            {
                "dataset_id": "daily_intraday_1m_after_hours_fixed_price_trades",
                "format": "parquet",
                "path": "payload/daily/after-hours-trades.parquet",
                "role": "source_evidence",
                "sha256": "sha256:" + "3" * 64,
                "size_bytes": 300,
            },
            {
                "dataset_id": "daily_release_metadata",
                "format": "json",
                "path": "payload/daily/release.json",
                "role": "source_evidence",
                "sha256": "sha256:" + "4" * 64,
                "size_bytes": 400,
            },
        ]
        records = _base_records({"content": {"files": files}}, model)

        members = [record for record in records if record["record_type"] == "container_member"]
        self.assertEqual(len(members), 10)
        self.assertEqual(
            next(record for record in members if record["member"] == "holder_changes")[
                "target_tables"
            ],
            ["dwd_shareholding_change"],
        )
        by_dataset = {
            record["dataset_id"]: record
            for record in records
            if record["record_type"] == "file"
        }
        self.assertEqual(
            by_dataset["source_evidence_weekly_full_fundamentals_20260731"][
                "mapping_identity"
            ],
            "financial_statements",
        )
        self.assertEqual(
            by_dataset["daily_intraday_1m_after_hours_fixed_price_trades"]["disposition"],
            "deferred",
        )
        self.assertEqual(by_dataset["daily_release_metadata"]["disposition"], "ods_only")

        files[0]["tables"] = files[0]["tables"][:-1]
        with self.assertRaisesRegex(WarehousePlanningError, "inventory mismatch"):
            _base_records({"content": {"files": files}}, model)

    def test_capacity_counts_one_modeled_file_once_across_multiple_targets(self) -> None:
        records = [
            {
                "dataset_id": "shared_source",
                "disposition": "modeled",
                "path": "payload/shared.parquet",
                "record_type": "file",
                "size_bytes": 100,
                "target_tables": ["dwd_security", "dwd_listing"],
            }
        ]

        capacity = _capacity(self.root, records, available_bytes=1_000)

        requirement = capacity["requirement"]
        self.assertEqual(requirement["modeled_input_bytes"], 100)
        self.assertEqual(requirement["estimated_final_dwd_bytes"], 100)
        self.assertEqual(requirement["estimated_final_dws_bytes"], 100)
        self.assertEqual(requirement["largest_stage_or_spill_bytes"], 100)
        self.assertEqual(requirement["required_free_bytes"], 360)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

try:
    import duckdb  # noqa: F401
    import pyarrow as pa
    import pyarrow.parquet as pq
except ModuleNotFoundError:  # pragma: no cover - exercised by the base dependency job
    pa = None
    pq = None

from quanthus.pipelines import ods_base
from quanthus.pipelines.ods_base import (
    OdsBaseError,
    audit_ods_base_release_gate,
    audit_ods_base_sources,
    build_ods_base_bundle,
    load_ods_base_current,
    load_ods_base_policy,
    promote_ods_base_bundle,
    verify_ods_base_bundle,
    write_ods_base_source_inventory,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


@unittest.skipIf(pa is None or pq is None, "P1 dependencies are not installed")
class OdsBaseTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.astock = self.root / "astock"
        self.data = self.astock / "data"
        self.minute = self.astock / "minute" / "data"
        self.data.mkdir(parents=True)
        self.minute.mkdir(parents=True)
        self._write_sources([date(2026, 7, 30)])
        self.policy_path = self.root / "policy.json"
        self._write_policy()
        self.policy = load_ods_base_policy(self.policy_path)

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _write_sources(self, open_dates: list[date]) -> None:
        assert pa is not None
        assert pq is not None
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {"exchange": exchange, "trade_date": market_date, "is_open": 1}
                    for market_date in open_dates
                    for exchange in ("SSE", "SZSE")
                ]
            ),
            self.data / "calendar.parquet",
        )
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {
                        "stock_code": "000001",
                        "date": date(2026, 7, 29),
                        "close": 10.0,
                    }
                ]
            ),
            self.data / "bars.parquet",
        )
        self._write_json(self.data / "meta.json", {"fixture": "source"})
        database = sqlite3.connect(self.data / "archive.db")
        database.execute(
            "CREATE TABLE IF NOT EXISTS announcements(code TEXT, ann_id TEXT, title TEXT, "
            "PRIMARY KEY(code, ann_id))"
        )
        database.execute(
            "INSERT OR IGNORE INTO announcements VALUES ('000001', 'a1', 'notice')"
        )
        database.commit()
        database.close()

        for frequency, directory in (("5m", "prod_5m"), ("1m", "research_1m")):
            target = self.minute / directory
            target.mkdir(parents=True, exist_ok=True)
            for index in range(2):
                pq.write_table(
                    pa.Table.from_pylist(
                        [
                            {
                                "stock_code": f"00000{index + 1}",
                                "ts": datetime(2026, 7, 30, 9, 30),
                                "close": 10.0 + index,
                            }
                        ]
                    ),
                    target / f"00000{index + 1}_{frequency}.parquet",
                )
        self._write_json(self.minute / "smoke.json", {"excluded": True})
        evidence = self.data / "revisions" / "accepted-evidence"
        self._write_json(evidence / "META.json", {"accepted": True})
        self._write_json(evidence / "raw" / "response.json", {"row": 1})
        self._write_json(self.data / "temp_work" / "status.json", {"state": "failed"})

        for market_date in open_dates:
            self._write_daily_release(market_date, "market-attempt", "market")
            self._write_daily_release(market_date, "event-attempt", "events")
            pointer = self._daily_root() / market_date.isoformat() / "CURRENT.json"
            market_release = pointer.parent / "market-attempt" / "release.json"
            self._write_json(
                pointer,
                {
                    "schema_version": 1,
                    "schedule": "daily",
                    "market_date": market_date.isoformat(),
                    "attempt_id": "market-attempt",
                    "manifest_sha256": _sha256(market_release),
                },
            )
            for frequency in ("1m", "5m"):
                self._write_intraday_release(market_date, frequency)

    def _daily_root(self) -> Path:
        return self.data / "shadow" / "hermes" / "releases" / "daily"

    def _artifact(self, root: Path, name: str, market_date: date) -> dict[str, object]:
        assert pa is not None
        assert pq is not None
        (root / "artifacts").mkdir(parents=True, exist_ok=True)
        if name in {"acquisition_log", "lane_status", "intraday_meta"}:
            path = root / "artifacts" / f"{name}.json"
            self._write_json(path, {"name": name, "date": market_date.isoformat()})
        else:
            path = root / "artifacts" / f"{name}.parquet"
            rows = (
                [
                    {"trade_date": value, "is_open": 1, "exchange": "SSE"}
                    for value in (
                        [date(2026, 7, 30)]
                        if market_date == date(2026, 7, 30)
                        else [date(2026, 7, 30), market_date]
                    )
                ]
                if name == "trading_calendar"
                else [{"market_date": market_date, "artifact": name}]
            )
            pq.write_table(
                pa.Table.from_pylist(rows),
                path,
            )
        return {
            "name": name,
            "relative_path": path.relative_to(root).as_posix(),
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
            "knowledge_cutoff": f"{market_date.isoformat()}T20:30:00+08:00",
            "source_revision": f"fixture-{name}-v1",
        }

    def _write_daily_release(self, market_date: date, attempt: str, role: str) -> None:
        root = self._daily_root() / market_date.isoformat() / attempt
        root.mkdir(parents=True, exist_ok=True)
        required = (
            ("bars", "status", "trading_calendar")
            if role == "market"
            else ("announcements", "actions")
        )
        artifacts = [
            self._artifact(root, name, market_date)
            for name in (*required, "acquisition_log", "lane_status")
        ]
        self._write_json(
            root / "release.json",
            {
                "schema_version": 1,
                "schedule": "daily",
                "status": "ready",
                "market_date": market_date.isoformat(),
                "release_id": f"fixture-{role}-{market_date.isoformat()}",
                "producer": "fixture-producer",
                "created_at": (
                    f"{market_date.isoformat()}T11:00:00+00:00"
                    if role == "market"
                    else f"{market_date.isoformat()}T12:00:00+00:00"
                ),
                "artifacts": artifacts,
            },
        )
        (root / "READY").write_text(f"{_sha256(root / 'release.json')}\n", encoding="ascii")

    def _write_intraday_release(self, market_date: date, frequency: str) -> None:
        root = (
            self._daily_root()
            / frequency
            / market_date.isoformat()
            / f"intraday-{frequency}-attempt"
        )
        root.mkdir(parents=True, exist_ok=True)
        artifacts = [
            self._artifact(root, name, market_date)
            for name in (
                "intraday_bars",
                "intraday_meta",
                "intraday_coverage",
                "trading_calendar",
                "acquisition_log",
                "lane_status",
            )
        ]
        self._write_json(
            root / "release.json",
            {
                "schema_version": 1,
                "schedule": "daily",
                "status": "ready",
                "market_date": market_date.isoformat(),
                "release_id": f"fixture-intraday-{frequency}-{market_date.isoformat()}",
                "producer": "fixture-producer",
                "created_at": f"{market_date.isoformat()}T13:00:00+00:00",
                "artifacts": artifacts,
            },
        )
        (root / "READY").write_text(f"{_sha256(root / 'release.json')}\n", encoding="ascii")

    def _write_policy(self) -> None:
        self._write_json(
            self.policy_path,
            {
                "schema_version": 1,
                "contract_id": "ods-base-test-v1",
                "authority": {
                    "source_node": "hermes",
                    "mirror_nodes": ["hermes", "cn01"],
                    "oci_ods_enabled": False,
                },
                "source": {
                    "files": [
                        {
                            "dataset_id": "announcement_archive",
                            "source": "data/archive.db",
                            "target": "payload/canonical/archive.db",
                            "role": "source_evidence",
                            "format": "sqlite",
                            "key_columns": [],
                            "date_columns": [],
                        },
                        {
                            "dataset_id": "bars",
                            "source": "data/bars.parquet",
                            "target": "payload/canonical/bars.parquet",
                            "role": "source_market_data",
                            "format": "parquet",
                            "key_columns": ["stock_code", "date"],
                            "date_columns": ["date"],
                        },
                        {
                            "dataset_id": "trading_calendar",
                            "source": "data/calendar.parquet",
                            "target": "payload/canonical/calendar.parquet",
                            "role": "source_reference",
                            "format": "parquet",
                            "key_columns": ["exchange", "trade_date"],
                            "date_columns": ["trade_date"],
                        },
                        {
                            "dataset_id": "metadata",
                            "source": "data/meta.json",
                            "target": "payload/canonical/meta.json",
                            "role": "source_lineage",
                            "format": "json",
                            "key_columns": [],
                            "date_columns": [],
                        },
                    ],
                    "trees": [
                        {
                            "dataset_id": "minute_1m",
                            "source": "minute/data/research_1m",
                            "target": "payload/minute/research_1m",
                            "role": "source_market_archive",
                            "pattern": "*_1m.parquet",
                            "minimum_parts": 2,
                            "key_columns": ["stock_code", "ts"],
                            "date_columns": ["ts"],
                        },
                        {
                            "dataset_id": "minute_5m",
                            "source": "minute/data/prod_5m",
                            "target": "payload/minute/prod_5m",
                            "role": "source_market_archive",
                            "pattern": "*_5m.parquet",
                            "minimum_parts": 2,
                            "key_columns": ["stock_code", "ts"],
                            "date_columns": ["ts"],
                        },
                    ],
                    "archive_trees": [
                        {
                            "dataset_id": "accepted_evidence",
                            "source": "data/revisions/accepted-evidence",
                            "target": "payload/source-evidence/accepted-evidence",
                            "role": "accepted_source_lineage",
                            "include_patterns": ["**"],
                            "minimum_files": 2,
                        }
                    ],
                    "scope_gaps": [
                        {
                            "dataset_family": "fixture_unacquired_source",
                            "classification": "not_formally_acquired",
                            "reason": "fixture source family was never collected",
                            "downstream_impact": "fixture consumers cannot use that family",
                        }
                    ],
                    "daily": {
                        "source": "data/shadow/hermes/releases/daily",
                        "increment_start": "2026-07-30",
                        "market_required_artifacts": [
                            "bars",
                            "status",
                            "trading_calendar",
                        ],
                        "event_required_artifacts": ["announcements", "actions"],
                    },
                    "intraday_releases": [
                        {
                            "frequency": frequency,
                            "source": f"data/shadow/hermes/releases/daily/{frequency}",
                            "increment_start": "2026-07-30",
                            "required_artifacts": [
                                "intraday_bars",
                                "intraday_meta",
                                "intraday_coverage",
                                "trading_calendar",
                            ],
                        }
                        for frequency in ("1m", "5m")
                    ],
                    "exclusions": [
                        {
                            "pattern": "data/revisions",
                            "classification": "excluded_superseded_revision",
                            "reason": "only accepted evidence descendants are included",
                        },
                        {
                            "pattern": "data/shadow",
                            "classification": "excluded_unselected_release_root",
                            "reason": "only selected releases are included",
                        },
                        {
                            "pattern": "data/temp_work",
                            "classification": "excluded_temporary",
                            "reason": "failed workspace",
                        },
                        {
                            "pattern": "minute/data/smoke.json",
                            "classification": "excluded_smoke_fixture",
                            "reason": "smoke metadata",
                        },
                    ],
                },
                "retention": {
                    "migration_mode": "copy_only",
                    "delete_enabled": False,
                    "minimum_identical_copies": 2,
                },
            },
        )

    def _inventory(self, through_date: date, name: str) -> Path:
        path = self.root / "inventory" / name
        write_ods_base_source_inventory(
            path,
            audit_ods_base_sources(
                astock_root=self.astock,
                through_date=through_date,
                policy=self.policy,
            ),
        )
        return path

    def _build(self, bundle_id: str = "ods-base-through-20260730-v1") -> Path:
        output = self.root / "build" / bundle_id
        inventory = self._inventory(date(2026, 7, 30), f"{bundle_id}.json")
        result = build_ods_base_bundle(
            astock_root=self.astock,
            output=output,
            bundle_id=bundle_id,
            through_date=date(2026, 7, 30),
            policy=self.policy,
            code_revision="a" * 40,
            source_inventory=inventory,
        )
        self.assertEqual(result.through_date, date(2026, 7, 30))
        return output

    def test_build_verify_and_exclude_operational_logs(self) -> None:
        output = self._build()
        first = verify_ods_base_bundle(output, policy=self.policy, deep=True)
        second = build_ods_base_bundle(
            astock_root=self.astock,
            output=output,
            bundle_id=output.name,
            through_date=date(2026, 7, 30),
            policy=self.policy,
            code_revision="a" * 40,
            source_inventory=(
                self.root / "inventory" / "ods-base-through-20260730-v1.json"
            ),
        )
        self.assertEqual(first.content_digest, second.content_digest)
        self.assertFalse(any(output.rglob("acquisition_log.json")))
        self.assertFalse(any(output.rglob("lane_status.json")))
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        lineages = manifest["content"]["daily_releases"]
        self.assertEqual(len(lineages), 4)
        self.assertEqual(
            manifest["content"]["scope_gaps"][0]["dataset_family"],
            "fixture_unacquired_source",
        )
        self.assertTrue(
            any(
                artifact["name"] == "acquisition_log"
                and artifact["included_in_base"] is False
                for release in lineages
                for artifact in release["artifacts"]
            )
        )

    def test_tampering_and_unclassified_source_fail_closed(self) -> None:
        output = self._build()
        corrupted = self.root / "corrupted" / output.name
        shutil.copytree(output, corrupted)
        (corrupted / "payload" / "canonical" / "meta.json").write_text(
            "tampered\n", encoding="utf-8"
        )
        with self.assertRaises(OdsBaseError):
            verify_ods_base_bundle(corrupted, policy=self.policy)
        self._write_json(self.data / "unknown.json", {"unknown": True})
        with self.assertRaisesRegex(OdsBaseError, "unclassified"):
            build_ods_base_bundle(
                astock_root=self.astock,
                output=self.root / "other" / "other-base",
                bundle_id="other-base",
                through_date=date(2026, 7, 30),
                policy=self.policy,
                code_revision="a" * 40,
            )

    def test_mixed_source_roots_are_classified_at_source_unit_depth(self) -> None:
        excluded = self.data / "revisions" / "superseded-revision"
        self._write_json(excluded / "META.json", {"superseded": True})
        inventory = audit_ods_base_sources(
            astock_root=self.astock,
            through_date=date(2026, 7, 30),
            policy=self.policy,
        )
        classifications = {
            item["path"]: item["classification"]
            for item in inventory["source_inventory"]
        }
        self.assertEqual(classifications["data/revisions"], "mixed")
        self.assertEqual(
            classifications["data/revisions/accepted-evidence"], "included"
        )
        self.assertEqual(
            classifications["data/revisions/superseded-revision"],
            "excluded_superseded_revision",
        )

    def test_production_policy_discloses_full_statement_scope_gap(self) -> None:
        policy = load_ods_base_policy(
            REPOSITORY_ROOT / "config" / "pipelines" / "ods-base.v1.json"
        )
        gaps = {item.dataset_family: item for item in policy.scope_gaps}
        self.assertIn("raw_f10_and_full_financial_statements", gaps)
        self.assertEqual(
            gaps["raw_f10_and_full_financial_statements"].classification,
            "not_formally_acquired",
        )

    def test_final_policy_includes_july_valuation_repair_and_evidence(self) -> None:
        policy = load_ods_base_policy(
            REPOSITORY_ROOT
            / "config"
            / "pipelines"
            / "ods-base-final-through-20260731.v2.json"
        )
        repair = (
            self.root
            / "final-policy-source"
            / "data"
            / "backfill"
            / "valuation"
            / "2026-07-20_2026-07-30"
        )
        (repair / "checkpoints").mkdir(parents=True)
        for name in (
            "valuation_history.parquet",
            "receipts.parquet",
            "valuation_history.parquet.meta.json",
        ):
            (repair / name).write_bytes(b"fixture")
        (repair / "checkpoints" / "part-00000.json").write_text(
            "{}\n", encoding="utf-8"
        )
        (self.root / "final-policy-source" / "minute" / "data").mkdir(parents=True)

        inventory = ods_base._classify_source_root(
            self.root / "final-policy-source", policy
        )
        classifications = {item["path"]: item["classification"] for item in inventory}
        self.assertEqual(classifications["data/backfill"], "mixed")
        self.assertEqual(
            classifications[
                "data/backfill/valuation/2026-07-20_2026-07-30/valuation_history.parquet"
            ],
            "included",
        )
        self.assertEqual(
            classifications[
                "data/backfill/valuation/2026-07-20_2026-07-30/checkpoints"
            ],
            "included",
        )
        datasets = {item.dataset_id for item in (*policy.files, *policy.archives)}
        self.assertIn("valuation_history_july_repair", datasets)
        self.assertIn(
            "source_evidence_valuation_history_july_repair_checkpoints", datasets
        )
        self.assertIn("valuation_daily_delta", policy.daily.market_required_artifacts)

    def test_archive_source_exception_is_bound_and_tamper_evident(self) -> None:
        release_root = self.data / "revisions" / "cn01-weekly-gap-fill"
        artifact = release_root / "artifacts" / "raw_f10.parquet"
        artifact.parent.mkdir(parents=True)
        assert pa is not None
        assert pq is not None
        pq.write_table(
            pa.Table.from_pylist([{"stock_code": "000001", "raw": "fixture"}]),
            artifact,
        )
        self._write_json(
            release_root / "release.json",
            {
                "schema_version": 1,
                "schedule": "weekly",
                "status": "ready",
                "artifacts": [
                    {
                        "name": "raw_f10",
                        "relative_path": "artifacts/raw_f10.parquet",
                        "sha256": _sha256(artifact),
                        "size_bytes": artifact.stat().st_size,
                    }
                ],
            },
        )
        release_digest = _sha256(release_root / "release.json")
        (release_root / "READY").write_text(f"{release_digest}\n", encoding="ascii")
        policy_value = json.loads(self.policy_path.read_text(encoding="utf-8"))
        policy_value["source"]["archive_trees"].append(
            {
                "dataset_id": "cn01_weekly_gap_fill",
                "source": "data/revisions/cn01-weekly-gap-fill",
                "target": "payload/source-evidence/cn01-weekly-gap-fill",
                "role": "retained_immutable_source_gap_fill",
                "include_patterns": ["READY", "release.json", "artifacts/**"],
                "minimum_files": 3,
                "source_exception": {
                    "source_node": "cn01",
                    "source_path": "data/shadow/cn01-ods-v1/releases/weekly/2026-07-31/attempt",
                    "reason": "Hermes did not retain the complete weekly release",
                    "manifest_sha256": release_digest,
                },
            }
        )
        self._write_json(self.policy_path, policy_value)
        policy = load_ods_base_policy(self.policy_path)

        inventory = audit_ods_base_sources(
            astock_root=self.astock,
            through_date=date(2026, 7, 30),
            policy=policy,
        )
        dataset = next(
            item
            for item in inventory["datasets"]
            if item["dataset_id"] == "cn01_weekly_gap_fill"
        )
        self.assertEqual(dataset["source_exception"]["source_node"], "cn01")
        self.assertEqual(
            dataset["source_exception"]["manifest_sha256"], release_digest
        )

        artifact.write_bytes(b"tampered weekly fixture")
        with self.assertRaisesRegex(OdsBaseError, "differs from manifest"):
            audit_ods_base_sources(
                astock_root=self.astock,
                through_date=date(2026, 7, 30),
                policy=policy,
            )

    def test_final_policy_closes_full_fundamentals_scope_gap(self) -> None:
        policy = load_ods_base_policy(
            REPOSITORY_ROOT
            / "config"
            / "pipelines"
            / "ods-base-final-through-20260731.v2.json"
        )
        self.assertNotIn(
            "raw_f10_and_full_financial_statements",
            {item.dataset_family for item in policy.scope_gaps},
        )
        weekly = next(
            item
            for item in policy.archives
            if item.dataset_id == "source_evidence_weekly_full_fundamentals_20260731"
        )
        self.assertIsNotNone(weekly.source_exception)
        assert weekly.source_exception is not None
        self.assertEqual(weekly.source_exception.source_node, "cn01")
        self.assertEqual(
            weekly.source_exception.manifest_sha256,
            "sha256:074afad64b6c80e2f83f7265f360f13e04da2da5f15114d67de49a2b56a21b7f",
        )

    def test_frozen_inventory_blocks_changed_sources(self) -> None:
        inventory = self._inventory(date(2026, 7, 30), "frozen.json")
        self._write_json(self.data / "meta.json", {"fixture": "changed"})
        with self.assertRaisesRegex(OdsBaseError, "changed after"):
            build_ods_base_bundle(
                astock_root=self.astock,
                output=self.root / "changed" / "changed-base",
                bundle_id="changed-base",
                through_date=date(2026, 7, 30),
                policy=self.policy,
                code_revision="a" * 40,
                source_inventory=inventory,
            )

    def test_latest_accepted_release_extends_a_stale_base_calendar(self) -> None:
        assert pa is not None
        assert pq is not None
        pq.write_table(
            pa.Table.from_pylist(
                [{"exchange": "SSE", "trade_date": date(2026, 7, 29), "is_open": 1}]
            ),
            self.data / "calendar.parquet",
        )
        inventory = audit_ods_base_sources(
            astock_root=self.astock,
            through_date=date(2026, 7, 30),
            policy=self.policy,
        )
        self.assertEqual(inventory["through_date"], "2026-07-30")
        self.assertEqual(len(inventory["daily_releases"]), 4)

    def test_daily_current_may_authorize_the_event_role(self) -> None:
        date_root = self._daily_root() / "2026-07-30"
        event_release = date_root / "event-attempt" / "release.json"
        self._write_json(
            date_root / "CURRENT.json",
            {
                "schema_version": 1,
                "schedule": "daily",
                "market_date": "2026-07-30",
                "attempt_id": "event-attempt",
                "manifest_sha256": _sha256(event_release),
            },
        )
        inventory = audit_ods_base_sources(
            astock_root=self.astock,
            through_date=date(2026, 7, 30),
            policy=self.policy,
        )
        roles = {
            item["release_role"]: item["attempt_id"]
            for item in inventory["daily_releases"]
            if item["release_role"] in {"market", "events"}
        }
        self.assertEqual(roles, {"market": "market-attempt", "events": "event-attempt"})

    def test_intraday_gap_fill_may_use_an_explicit_hermes_revision(self) -> None:
        policy_value = json.loads(self.policy_path.read_text(encoding="utf-8"))
        for frequency in ("1m", "5m"):
            source_attempt = (
                self._daily_root()
                / frequency
                / "2026-07-30"
                / f"intraday-{frequency}-attempt"
            )
            revision_root = self.data / "revisions" / "hermes-gap" / frequency
            revision_attempt = revision_root / source_attempt.name
            shutil.copytree(source_attempt, revision_attempt)
            release_digest = _sha256(revision_attempt / "release.json")
            item = next(
                candidate
                for candidate in policy_value["source"]["intraday_releases"]
                if candidate["frequency"] == frequency
            )
            item["source_exceptions"] = [
                {
                    "market_date": "2026-07-30",
                    "source_node": "hermes",
                    "source": f"data/revisions/hermes-gap/{frequency}",
                    "reason": "formal Hermes release was incomplete; retained revision is complete",
                    "manifest_sha256": release_digest,
                }
            ]
        self._write_json(self.policy_path, policy_value)
        policy = load_ods_base_policy(self.policy_path)

        inventory = audit_ods_base_sources(
            astock_root=self.astock,
            through_date=date(2026, 7, 30),
            policy=policy,
        )

        intraday = [
            item
            for item in inventory["daily_releases"]
            if item["release_role"].startswith("intraday_")
        ]
        self.assertEqual({item["source_node"] for item in intraday}, {"hermes"})
        self.assertTrue(all(item["gap_fill"] is not None for item in intraday))

    def test_production_legacy_manifest_is_not_a_canonical_base_member(self) -> None:
        policy = load_ods_base_policy(
            REPOSITORY_ROOT / "config" / "pipelines" / "ods-base.v1.json"
        )
        legacy = {
            item.dataset_id: item
            for item in policy.files
            if item.dataset_id.startswith("legacy_dataset_manifest")
        }

        self.assertEqual(
            set(legacy),
            {"legacy_dataset_manifest", "legacy_dataset_manifest_metadata"},
        )
        self.assertTrue(
            all(item.role == "legacy_source_lineage" for item in legacy.values())
        )
        self.assertTrue(
            all(
                item.target.startswith("payload/source-evidence/legacy-dataset-manifest/")
                for item in legacy.values()
            )
        )

    def test_production_dividend_identity_allows_a_missing_ex_date(self) -> None:
        policy = load_ods_base_policy(
            REPOSITORY_ROOT / "config" / "pipelines" / "ods-base.v1.json"
        )
        dividend = next(
            item for item in policy.files if item.dataset_id == "dividend_history"
        )

        self.assertEqual(dividend.key_columns, ("stock_code", "announce_date"))
        self.assertIn("ex_dividend_date", dividend.date_columns)

    def test_promote_and_load_current(self) -> None:
        output = self._build()
        base_root = self.root / "node" / "base"
        current = base_root / "BASE_CURRENT.json"
        promoted = promote_ods_base_bundle(
            ingress_bundle=output,
            base_root=base_root,
            current_pointer=current,
            policy=self.policy,
        )
        loaded = load_ods_base_current(current, policy=self.policy, deep=True)
        self.assertEqual(promoted.content_digest, loaded.content_digest)
        self.assertEqual(loaded.through_date, date(2026, 7, 30))

    def test_hidden_transfer_staging_requires_explicit_verification_mode(self) -> None:
        output = self._build()
        base_root = self.root / "node" / "base"
        base_root.mkdir(parents=True)
        staging = base_root / f".incoming-{output.name}"
        shutil.copytree(output, staging)

        with self.assertRaisesRegex(OdsBaseError, "directory and bundle identity"):
            verify_ods_base_bundle(staging, policy=self.policy, deep=True)
        staged = verify_ods_base_bundle(
            staging,
            policy=self.policy,
            deep=True,
            allow_staging_name=True,
        )
        self.assertEqual(staged.bundle_id, output.name)

        promoted = promote_ods_base_bundle(
            ingress_bundle=staging,
            base_root=base_root,
            current_pointer=base_root / "BASE_CURRENT.json",
            policy=self.policy,
            move_ingress=True,
        )
        self.assertEqual(promoted.bundle_id, output.name)
        self.assertFalse(staging.exists())

    def test_final_base_reuses_unchanged_parent_files(self) -> None:
        provisional = self._build()
        self._write_sources([date(2026, 7, 30), date(2026, 7, 31)])
        final = self.root / "build" / "ods-base-202607-v1"
        result = build_ods_base_bundle(
            astock_root=self.astock,
            output=final,
            bundle_id=final.name,
            through_date=date(2026, 7, 31),
            policy=self.policy,
            code_revision="b" * 40,
            parent_base=provisional,
            source_inventory=self._inventory(date(2026, 7, 31), "final.json"),
        )
        self.assertEqual(result.through_date, date(2026, 7, 31))
        self.assertEqual(
            (provisional / "payload" / "canonical" / "bars.parquet").stat().st_ino,
            (final / "payload" / "canonical" / "bars.parquet").stat().st_ino,
        )
        manifest = json.loads((final / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(
            manifest["content"]["parent_base"]["content_digest"],
            verify_ods_base_bundle(provisional, policy=self.policy).content_digest,
        )
        self.assertGreater(manifest["storage_reuse"]["file_count"], 0)

    def test_policy_revision_can_reuse_and_replace_a_prior_policy_base(self) -> None:
        provisional = self._build()
        old_policy = self.policy
        revised_value = json.loads(self.policy_path.read_text(encoding="utf-8"))
        revised_value["contract_id"] = "ods-base-test-final-v2"
        revised_path = self.root / "final-policy.json"
        self._write_json(revised_path, revised_value)
        revised_policy = load_ods_base_policy(revised_path)
        self._write_sources([date(2026, 7, 30), date(2026, 7, 31)])
        final = self.root / "build" / "ods-base-policy-transition-v2"
        final_inventory = self.root / "inventory" / "policy-transition.json"
        write_ods_base_source_inventory(
            final_inventory,
            audit_ods_base_sources(
                astock_root=self.astock,
                through_date=date(2026, 7, 31),
                policy=revised_policy,
            ),
        )
        built = build_ods_base_bundle(
            astock_root=self.astock,
            output=final,
            bundle_id=final.name,
            through_date=date(2026, 7, 31),
            policy=revised_policy,
            code_revision="b" * 40,
            parent_base=provisional,
            parent_policy=old_policy,
            source_inventory=final_inventory,
        )
        base_root = self.root / "policy-transition-node" / "base"
        current = base_root / "BASE_CURRENT.json"
        prior = promote_ods_base_bundle(
            ingress_bundle=provisional,
            base_root=base_root,
            current_pointer=current,
            policy=old_policy,
        )
        promoted = promote_ods_base_bundle(
            ingress_bundle=final,
            base_root=base_root,
            current_pointer=current,
            policy=revised_policy,
            current_policy=old_policy,
            expected_current_manifest_sha256=prior.manifest_sha256,
        )
        self.assertEqual(promoted.manifest_sha256, built.manifest_sha256)
        self.assertEqual(
            load_ods_base_current(current, policy=revised_policy).bundle_id,
            final.name,
        )

    def test_pointer_advance_requires_compare_and_swap_precondition(self) -> None:
        provisional = self._build()
        base_root = self.root / "node" / "base"
        current_pointer = base_root / "BASE_CURRENT.json"
        promoted = promote_ods_base_bundle(
            ingress_bundle=provisional,
            base_root=base_root,
            current_pointer=current_pointer,
            policy=self.policy,
        )
        self._write_sources([date(2026, 7, 30), date(2026, 7, 31)])
        final = self.root / "build" / "ods-base-202607-v1"
        build_ods_base_bundle(
            astock_root=self.astock,
            output=final,
            bundle_id=final.name,
            through_date=date(2026, 7, 31),
            policy=self.policy,
            code_revision="b" * 40,
            parent_base=provisional,
            source_inventory=self._inventory(date(2026, 7, 31), "cas-final.json"),
        )

        with self.assertRaisesRegex(OdsBaseError, "expected manifest"):
            promote_ods_base_bundle(
                ingress_bundle=final,
                base_root=base_root,
                current_pointer=current_pointer,
                policy=self.policy,
            )
        with self.assertRaisesRegex(OdsBaseError, "precondition failed"):
            promote_ods_base_bundle(
                ingress_bundle=final,
                base_root=base_root,
                current_pointer=current_pointer,
                policy=self.policy,
                expected_current_manifest_sha256="sha256:" + "0" * 64,
            )

        final_promoted = promote_ods_base_bundle(
            ingress_bundle=final,
            base_root=base_root,
            current_pointer=current_pointer,
            policy=self.policy,
            expected_current_manifest_sha256=promoted.manifest_sha256,
        )
        self.assertEqual(final_promoted.bundle_id, "ods-base-202607-v1")
        self.assertEqual(
            promote_ods_base_bundle(
                ingress_bundle=final,
                base_root=base_root,
                current_pointer=current_pointer,
                policy=self.policy,
                expected_current_manifest_sha256=promoted.manifest_sha256,
            ).manifest_sha256,
            final_promoted.manifest_sha256,
        )

    def test_close_release_gate_binds_parent_and_all_four_roles(self) -> None:
        provisional = self._build()
        base_root = self.root / "node" / "base"
        current = base_root / "BASE_CURRENT.json"
        parent = promote_ods_base_bundle(
            ingress_bundle=provisional,
            base_root=base_root,
            current_pointer=current,
            policy=self.policy,
        )
        self._write_sources([date(2026, 7, 30), date(2026, 7, 31)])

        result = audit_ods_base_release_gate(
            release_root=self._daily_root().parent,
            base_current=current,
            market_date=date(2026, 7, 31),
            policy=self.policy,
            expected_parent_bundle_id=parent.bundle_id,
            expected_parent_manifest_sha256=parent.manifest_sha256,
            expected_parent_through_date=date(2026, 7, 30),
            minimum_free_bytes=0,
        )

        self.assertEqual(result["status"], "ready")
        self.assertEqual(
            [item["role"] for item in result["releases"]],
            ["market", "events", "1m", "5m"],
        )
        self.assertTrue(result["authorizes_candidate_build"])
        self.assertFalse(result["authorizes_base_promotion"])

    def test_final_release_policy_requires_valuation_with_old_parent_policy(self) -> None:
        provisional = self._build()
        base_root = self.root / "node-final-policy" / "base"
        current = base_root / "BASE_CURRENT.json"
        parent = promote_ods_base_bundle(
            ingress_bundle=provisional,
            base_root=base_root,
            current_pointer=current,
            policy=self.policy,
        )
        revised_value = json.loads(self.policy_path.read_text(encoding="utf-8"))
        revised_value["contract_id"] = "ods-base-test-final-release-v2"
        revised_value["source"]["daily"]["market_required_artifacts"].append(
            "valuation_daily_delta"
        )
        revised_path = self.root / "final-release-policy.json"
        self._write_json(revised_path, revised_value)
        revised_policy = load_ods_base_policy(revised_path)
        self._write_sources([date(2026, 7, 30), date(2026, 7, 31)])

        with self.assertRaisesRegex(OdsBaseError, "required market release"):
            audit_ods_base_release_gate(
                release_root=self._daily_root().parent,
                base_current=current,
                market_date=date(2026, 7, 31),
                policy=revised_policy,
                parent_policy=self.policy,
                expected_parent_bundle_id=parent.bundle_id,
                expected_parent_manifest_sha256=parent.manifest_sha256,
                expected_parent_through_date=date(2026, 7, 30),
                minimum_free_bytes=0,
            )

    def test_close_release_gate_fails_when_one_role_is_not_ready(self) -> None:
        provisional = self._build()
        base_root = self.root / "node" / "base"
        current = base_root / "BASE_CURRENT.json"
        parent = promote_ods_base_bundle(
            ingress_bundle=provisional,
            base_root=base_root,
            current_pointer=current,
            policy=self.policy,
        )
        self._write_sources([date(2026, 7, 30), date(2026, 7, 31)])
        missing = (
            self._daily_root()
            / "5m"
            / "2026-07-31"
            / "intraday-5m-attempt"
            / "READY"
        )
        missing.unlink()

        with self.assertRaisesRegex(OdsBaseError, "no complete ODS 5m release"):
            audit_ods_base_release_gate(
                release_root=self._daily_root().parent,
                base_current=current,
                market_date=date(2026, 7, 31),
                policy=self.policy,
                expected_parent_bundle_id=parent.bundle_id,
                expected_parent_manifest_sha256=parent.manifest_sha256,
                expected_parent_through_date=date(2026, 7, 30),
                minimum_free_bytes=0,
            )

    def test_close_release_gate_rejects_an_ambiguous_daily_attempt(self) -> None:
        provisional = self._build()
        base_root = self.root / "node" / "base"
        current = base_root / "BASE_CURRENT.json"
        parent = promote_ods_base_bundle(
            ingress_bundle=provisional,
            base_root=base_root,
            current_pointer=current,
            policy=self.policy,
        )
        target_date = date(2026, 7, 31)
        self._write_sources([date(2026, 7, 30), target_date])
        event_root = self._daily_root() / target_date.isoformat() / "event-attempt"
        manifest_path = event_root / "release.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifacts"].extend(
            self._artifact(event_root, name, target_date)
            for name in ("bars", "status", "trading_calendar")
        )
        self._write_json(manifest_path, manifest)
        (event_root / "READY").write_text(
            f"{_sha256(manifest_path)}\n", encoding="ascii"
        )

        with self.assertRaisesRegex(OdsBaseError, "ambiguous market/events roles"):
            audit_ods_base_release_gate(
                release_root=self._daily_root().parent,
                base_current=current,
                market_date=target_date,
                policy=self.policy,
                expected_parent_bundle_id=parent.bundle_id,
                expected_parent_manifest_sha256=parent.manifest_sha256,
                expected_parent_through_date=date(2026, 7, 30),
                minimum_free_bytes=0,
            )


if __name__ == "__main__":
    unittest.main()

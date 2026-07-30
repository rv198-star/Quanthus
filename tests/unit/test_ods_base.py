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

from quanthus.pipelines.ods_base import (
    OdsBaseError,
    audit_ods_base_sources,
    build_ods_base_bundle,
    load_ods_base_current,
    load_ods_base_policy,
    promote_ods_base_bundle,
    verify_ods_base_bundle,
    write_ods_base_source_inventory,
)


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
                            "classification": "mixed",
                            "reason": "only accepted evidence descendants are included",
                        },
                        {
                            "pattern": "data/shadow",
                            "classification": "mixed",
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


if __name__ == "__main__":
    unittest.main()

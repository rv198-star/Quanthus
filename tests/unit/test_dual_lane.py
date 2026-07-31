from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ModuleNotFoundError:  # pragma: no cover - exercised by the P1 dependency job
    pa = None
    pq = None

from quanthus.pipelines import dual_lane
from quanthus.pipelines.contracts import publish_ods_release
from quanthus.pipelines.dual_lane import (
    DualLaneError,
    EpochInput,
    LaneCandidate,
    arbitrate_and_publish,
    build_lane_candidate,
    load_dual_lane_policy,
    publish_epoch_input,
    select_market_daily_ods_release,
    verify_epoch_input,
    verify_lane_candidate,
)


def _sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _acquire_lease_process(
    policy_path: str,
    root: str,
    owner: str,
    now: str,
    results,
) -> None:
    try:
        lease = dual_lane.acquire_fenced_lease(
            root=Path(root),
            policy=load_dual_lane_policy(Path(policy_path)),
            owner=owner,
            clock=lambda: datetime.fromisoformat(now),
        )
        results.put(("acquired", owner, lease.fencing_token))
    except Exception as exc:  # pragma: no cover - asserted in the parent process
        results.put(("rejected", owner, type(exc).__name__, str(exc)))


def _arbitrate_process(
    policy_path: str,
    epoch_path: str,
    primary_path: str,
    standby_path: str,
    root: str,
    market_date: str,
    now: str,
    results,
) -> None:
    try:
        policy = load_dual_lane_policy(Path(policy_path))
        epoch = verify_epoch_input(Path(epoch_path))
        result = arbitrate_and_publish(
            market_date=date.fromisoformat(market_date),
            primary_candidate_path=Path(primary_path),
            standby_candidate_path=Path(standby_path),
            root=Path(root),
            policy=policy,
            expected_epoch=epoch,
            clock=lambda: datetime.fromisoformat(now),
        )
        results.put(("published", result.release_set_id))
    except Exception as exc:  # pragma: no cover - asserted in the parent process
        results.put(("failed", type(exc).__name__, str(exc)))


@unittest.skipIf(pa is None or pq is None, "P1 dependencies are not installed")
class DualLaneTests(unittest.TestCase):
    CODE_REVISION = "c" * 40

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.policy_path = self.root / "dual-lane-policy.json"
        self._write_json(self.policy_path, self._policy_payload())
        self.policy = load_dual_lane_policy(self.policy_path)
        self.universe_path = self.root / "p1-universe.json"
        self._write_json(
            self.universe_path,
            {
                "schema_version": "1.0.0",
                "policy_version": "dual-lane-test-universe-v1",
                "status": "frozen",
                "allowed_exchanges": ["SSE", "SZSE"],
                "excluded_security_ids": {},
                "out_of_scope_code_prefixes": {},
                "invalid_bar_handling": "retain_with_data_valid_false",
            },
        )
        self.validity_path = self.root / "security-code-validity.json"
        self._write_json(
            self.validity_path,
            {
                "schema_version": "1.0.0",
                "mapping_version": "dual-lane-test-code-validity-v1",
                "status": "frozen",
                "source_revision": "dual-lane-test-evidence-v1",
                "intervals": [
                    {
                        "security_id": "000001",
                        "stock_code": "000001",
                        "exchange": "SZSE",
                        "valid_from": "1991-04-03",
                        "valid_to": None,
                        "source": "test",
                        "evidence_locator": "test://000001",
                        "evidence_sha256": f"sha256:{'a' * 64}",
                        "source_revision": "dual-lane-test-evidence-v1",
                        "knowledge_cutoff": "1991-04-03T00:00:00+08:00",
                    }
                ],
            },
        )
        assert pa is not None
        assert pq is not None
        self.security_master_path = self.root / "ref_security_master.parquet"
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {
                        "security_id": "000001",
                        "stock_code": "000001",
                        "exchange": "SZSE",
                        "name": "TEST",
                        "list_date": date(1991, 4, 3),
                        "delist_date": None,
                        "status": "listed",
                        "bs_code": "sz.000001",
                        "as_of": date(2026, 7, 24),
                        "source": "test",
                        "built_at": datetime(2026, 7, 24, tzinfo=timezone.utc),
                    }
                ]
            ),
            self.security_master_path,
        )
        base = self.root / "historical-base" / "p1-history-base-v1"
        base.mkdir(parents=True)
        self.base_metadata_path = base / "META.json"
        self._write_json(
            self.base_metadata_path,
            {"revision": base.name, "kind": "test-base"},
        )
        self.base_pointer = self.root / "historical-base-CURRENT.json"
        self._write_json(
            self.base_pointer,
            {
                "revision": base.name,
                "revision_path": str(base),
                "meta_sha256": _sha256(base / "META.json"),
            },
        )

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _policy_payload(*, maximum_absence: int = 1) -> dict[str, object]:
        return {
            "schema_version": 1,
            "contract_id": "dual-lane-dwd-dws-test-v1",
            "timezone": "Asia/Shanghai",
            "roles": {
                "cn01": "fixed_primary_candidate_producer",
                "hermes": "standby_candidate_producer",
                "oci": "epoch_arbitration_ads_release_authority",
            },
            "runtime": {
                "python_series": "3.10",
                "packages": {
                    "duckdb": "1.5.5",
                    "polars": "1.43.0",
                    "pyarrow": "23.0.1",
                    "quanthus": "0.1.0.dev0",
                },
            },
            "schedule": {
                "epoch_publish_time": "16:30:00",
                "candidate_build_time": "19:50:00",
                "arbitration_time": "20:00:00",
                "completion_deadline": "20:30:00",
            },
            "arbitration": {
                "primary_lane": "cn01",
                "standby_lane": "hermes",
                "maximum_consecutive_standby_absence": maximum_absence,
                "fallback_failure_reasons": ["missing_candidate", "invalid_candidate"],
                "eligible_attempt_source": "lane_ods_current",
                "attempt_selection": "exact_only",
                "cross_lane_divergence_policy": "withhold_both",
                "row_or_artifact_mixing": False,
            },
            "publication": {
                "consumer_pointer": "release-set_CURRENT.json",
                "operational_ads_required": True,
                "legacy_dataset_pointers_are_authoritative": False,
                "postgresql_projection_authoritative": False,
                "fenced_lease": {
                    "lease_name": "canonical-publisher",
                    "duration_seconds": 1860,
                    "pointer_cas": "sha256-precondition-v1",
                    "target_owner": "dual-lane-target",
                    "legacy_owner": "legacy-oci",
                },
                "cutover_gate": {
                    "artifact": "GO_FOR_CUTOVER.json",
                    "environment": "GO_FOR_CUTOVER",
                    "live_activation_default": False,
                    "required_evidence": [
                        "legacy_writer_fenced",
                        "retention_gate_passed",
                        "restore_drill_passed",
                    ],
                },
            },
            "retention": {
                "migration_mode": "copy_only",
                "delete_enabled": False,
                "retain_legacy_oci_ods": True,
                "retain_legacy_dwd_dws": True,
                "retain_all_migration_candidates": True,
                "accepted_ods_recovery": {
                    "minimum_failure_independent_copies": 2,
                    "minimum_correction_horizon_days": 90,
                    "restore_rpo_seconds": 0,
                    "restore_rto_seconds": 3600,
                    "purge_requires": [
                        "correction_horizon_expired",
                        "zero_descendant_references",
                        "successful_restore_drill",
                        "explicit_inventory_approval",
                    ],
                },
            },
            "correction": {
                "state_machine": "immutable-correction-v1",
                "approval_required": True,
                "late_equivalent_republishes": False,
                "revoked_generation_publishable": False,
                "rollback_requires_fenced_cas": True,
                "reason_code_required": True,
            },
            "qualification": {
                "calibration_open_sessions": 1,
                "qualification_open_sessions": 2,
                "closed_session_fixtures": 1,
                "threshold_retuning_during_qualification": False,
            },
            "ads": {
                "operational_profile": "operational_daily_v1",
                "portable_profiles": ["strategy_replay", "event_library", "ml_training"],
                "mandatory_year_partitioning": False,
                "artifact_discovery": "manifest_tables_parts",
            },
        }

    @staticmethod
    def _clock(market_date: date, hour: int, minute: int = 0):
        value = datetime.combine(
            market_date,
            time(hour, minute),
            tzinfo=ZoneInfo("Asia/Shanghai"),
        )
        return lambda: value

    def _epoch(self, market_date: date) -> EpochInput:
        input_digest = dual_lane._epoch_input_digest(
            _sha256(self.base_pointer),
            _sha256(self.base_metadata_path),
            self.policy.digest,
            _sha256(self.universe_path),
            _sha256(self.validity_path),
            _sha256(self.security_master_path),
        )
        return publish_epoch_input(
            output=self.root / "epochs" / market_date.isoformat(),
            epoch_id=dual_lane._derive_epoch_id(
                market_date=market_date,
                code_revision=self.CODE_REVISION,
                input_digest=input_digest,
            ),
            market_date=market_date,
            policy=self.policy,
            universe_policy_path=self.universe_path,
            code_validity_path=self.validity_path,
            security_master_path=self.security_master_path,
            code_revision=self.CODE_REVISION,
            base_pointer=self.base_pointer,
            clock=self._clock(market_date, 16, 30),
        )

    def _release(
        self,
        market_date: date,
        *,
        lane: str,
        close: float = 10.1,
        variant: str = "base",
        knowledge_cutoff: datetime | None = None,
        release_created_at: datetime | None = None,
    ) -> Path:
        assert pa is not None
        assert pq is not None
        root = self.root / "ods-inputs" / market_date.isoformat() / f"{lane}-{variant}"
        inputs = root / "inputs"
        inputs.mkdir(parents=True)
        built_at = knowledge_cutoff or datetime.combine(
            market_date, time(8), tzinfo=timezone.utc
        )
        bars = inputs / "daily_ohlcv_none_delta.parquet"
        statuses = inputs / "daily_status_observation_delta.parquet"
        master = inputs / "ref_security_master.parquet"
        calendar = inputs / "ref_trading_calendar.parquet"
        stock_list = inputs / "stock_list.parquet"
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {
                        "stock_code": "000001",
                        "date": market_date,
                        "open": 10.0,
                        "high": max(10.2, close),
                        "low": 9.9,
                        "close": close,
                        "volume": 1000.0,
                        "amount": close * 1000.0,
                        "turnover_rate": 0.2,
                        "pct_change": (close / 10.0 - 1.0) * 100.0,
                        "adj_mode": "none",
                        "adj_version": "none_v1",
                        "source": "test",
                        "built_at": built_at,
                    }
                ]
            ),
            bars,
        )
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {
                        "stock_code": "000001",
                        "date": market_date,
                        "tradestatus": "1",
                        "isST": "0",
                        "source": "test",
                        "built_at": built_at,
                    }
                ]
            ),
            statuses,
        )
        master_rows = [
            {
                "security_id": "000001",
                "stock_code": "000001",
                "exchange": "SZSE",
                "name": "TEST",
                "list_date": date(1991, 4, 3),
                "delist_date": None,
                "status": "listed",
                "bs_code": "sz.000001",
                "as_of": market_date,
                "source": "test",
                "built_at": built_at,
            }
        ]
        if variant == "local-reference-drift":
            master_rows.append(
                {
                    "security_id": "920238",
                    "stock_code": "920238",
                    "exchange": "BSE",
                    "name": "OUT OF SCOPE",
                    "list_date": market_date,
                    "delist_date": None,
                    "status": "listed",
                    "bs_code": None,
                    "as_of": market_date,
                    "source": "test",
                    "built_at": built_at,
                }
            )
        pq.write_table(pa.Table.from_pylist(master_rows), master)
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {"exchange": "SSE", "trade_date": market_date, "is_open": 1},
                    {"exchange": "SZSE", "trade_date": market_date, "is_open": 1},
                ]
            ),
            calendar,
        )
        pq.write_table(
            pa.Table.from_pylist(
                [{"stock_code": "000001", "stock_name": "TEST", "market": "deep-A"}]
            ),
            stock_list,
        )
        sources = {
            "daily_ohlcv_none_delta": bars,
            "daily_status_observation_delta": statuses,
            "security_master": master,
            "trading_calendar": calendar,
            "stock_list": stock_list,
        }
        spec = root / "spec.json"
        self._write_json(
            spec,
            {
                "schema_version": 1,
                "release_id": f"{lane}-daily-{market_date.strftime('%Y%m%d')}-{variant}",
                "schedule": "daily",
                "market_date": market_date.isoformat(),
                "producer": f"{lane}-test",
                "artifacts": [
                    {
                        "name": name,
                        "source_path": str(source),
                        "relative_path": f"artifacts/{source.name}",
                        "source_revision": f"{name}-test-v1",
                        "knowledge_cutoff": built_at.isoformat(),
                    }
                    for name, source in sources.items()
                ],
            },
        )
        attempt_id = f"attempt-{lane}-{market_date.strftime('%Y%m%d')}-{variant}"
        date_root = self.root / "ods" / lane / "daily" / market_date.isoformat()
        release = date_root / attempt_id
        sealed_at = release_created_at or datetime.combine(
            market_date, time(19, 45), tzinfo=ZoneInfo("Asia/Shanghai")
        )

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return sealed_at if tz is None else sealed_at.astimezone(tz)

        with mock.patch("quanthus.pipelines.contracts.datetime", FrozenDateTime):
            publish_ods_release(spec_path=spec, output=release)
        verified = dual_lane.verify_ods_release(release)
        self._write_json(
            date_root / "CURRENT.json",
            {
                "schema_version": 1,
                "schedule": "daily",
                "market_date": market_date.isoformat(),
                "attempt_id": attempt_id,
                "release_id": verified.release.release_id,
                "manifest_sha256": verified.manifest_sha256,
                "ready_at": verified.release.created_at.isoformat(),
            },
        )
        return release

    def _candidate(
        self,
        *,
        epoch: EpochInput,
        lane: str,
        close: float = 10.1,
        variant: str = "base",
    ) -> LaneCandidate:
        market_date = epoch.market_date
        return build_lane_candidate(
            output=self.root / "candidates" / market_date.isoformat() / f"{lane}-{variant}",
            candidate_id=f"candidate-{lane}-{market_date.strftime('%Y%m%d')}-{variant}",
            lane_id=lane,
            attempt_id=f"attempt-{lane}-{market_date.strftime('%Y%m%d')}-{variant}",
            market_date=market_date,
            ods_release_dir=self._release(
                market_date,
                lane=lane,
                close=close,
                variant=variant,
            ),
            epoch_root=epoch.root,
            policy=self.policy,
            code_revision=self.CODE_REVISION,
            clock=self._clock(market_date, 19, 55),
        )

    @staticmethod
    def _rewrite_ready_manifest(root: Path, manifest: dict[str, object]) -> None:
        manifest_path = root / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (root / "READY").write_text(f"{_sha256(manifest_path)}\n", encoding="ascii")

    def test_policy_rejects_enabling_deletion_during_copy_only_migration(self) -> None:
        payload = self._policy_payload()
        retention = payload["retention"]
        assert isinstance(retention, dict)
        retention["delete_enabled"] = True
        path = self.root / "unsafe-policy.json"
        self._write_json(path, payload)

        with self.assertRaisesRegex(DualLaneError, "deletion disabled"):
            load_dual_lane_policy(path)

    def test_policy_rejects_directory_order_attempt_selection(self) -> None:
        payload = self._policy_payload()
        arbitration = payload["arbitration"]
        assert isinstance(arbitration, dict)
        arbitration["attempt_selection"] = "latest_directory"
        path = self.root / "unsafe-attempt-policy.json"
        self._write_json(path, payload)

        with self.assertRaisesRegex(DualLaneError, "exact lane ODS CURRENT"):
            load_dual_lane_policy(path)

    def test_epoch_publication_requires_aware_trusted_clock_inside_window(self) -> None:
        market_date = date(2026, 7, 24)
        input_digest = dual_lane._epoch_input_digest(
            _sha256(self.base_pointer),
            _sha256(self.base_metadata_path),
            self.policy.digest,
            _sha256(self.universe_path),
            _sha256(self.validity_path),
            _sha256(self.security_master_path),
        )
        arguments = {
            "output": self.root / "clock-rejected-epoch",
            "epoch_id": dual_lane._derive_epoch_id(
                market_date=market_date,
                code_revision=self.CODE_REVISION,
                input_digest=input_digest,
            ),
            "market_date": market_date,
            "policy": self.policy,
            "universe_policy_path": self.universe_path,
            "code_validity_path": self.validity_path,
            "security_master_path": self.security_master_path,
            "code_revision": self.CODE_REVISION,
            "base_pointer": self.base_pointer,
        }
        with self.assertRaisesRegex(DualLaneError, "timezone-aware"):
            publish_epoch_input(**arguments, clock=lambda: datetime(2026, 7, 24, 8, 30))
        with self.assertRaisesRegex(DualLaneError, "publication window"):
            publish_epoch_input(**arguments, clock=self._clock(market_date, 16, 29))

    def test_epoch_is_portable_and_rejects_tamper_and_path_traversal(self) -> None:
        epoch = self._epoch(date(2026, 7, 24))
        portable = self.root / "portable" / "epoch"
        shutil.copytree(epoch.root, portable)

        relocated = verify_epoch_input(portable)
        self.assertEqual(relocated.epoch_id, epoch.epoch_id)
        self.assertTrue(relocated.universe_policy_path.is_relative_to(portable))
        self.assertTrue(relocated.code_validity_path.is_relative_to(portable))
        self.assertTrue(relocated.security_master_path.is_relative_to(portable))

        changed_master = self.root / "changed-ref_security_master.parquet"
        changed_master.write_bytes(self.security_master_path.read_bytes() + b"changed")
        with self.assertRaisesRegex(DualLaneError, "existing epoch does not match"):
            publish_epoch_input(
                output=epoch.root,
                epoch_id=epoch.epoch_id,
                market_date=epoch.market_date,
                policy=self.policy,
                universe_policy_path=self.universe_path,
                code_validity_path=self.validity_path,
                security_master_path=changed_master,
                code_revision=self.CODE_REVISION,
                base_pointer=self.base_pointer,
                clock=self._clock(epoch.market_date, 16, 30),
            )

        tampered = self.root / "tampered" / "epoch"
        shutil.copytree(epoch.root, tampered)
        (tampered / "references" / "p1-universe.json").write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(DualLaneError, "integrity failed"):
            verify_epoch_input(tampered)

        traversal = self.root / "traversal" / "epoch"
        shutil.copytree(epoch.root, traversal)
        manifest = json.loads((traversal / "manifest.json").read_text(encoding="utf-8"))
        manifest["shared_references"]["universe_policy_path"] = "../outside.json"
        self._rewrite_ready_manifest(traversal, manifest)
        with self.assertRaisesRegex(DualLaneError, "safe relative path"):
            verify_epoch_input(traversal)

        unlisted = self.root / "unlisted" / "epoch"
        shutil.copytree(epoch.root, unlisted)
        (unlisted / "unexpected.txt").write_text("not declared\n", encoding="utf-8")
        with self.assertRaisesRegex(DualLaneError, "file set differs"):
            verify_epoch_input(unlisted)

    def test_epoch_rejects_input_changed_after_id_derivation(self) -> None:
        market_date = date(2026, 7, 24)
        input_digest = dual_lane._epoch_input_digest(
            _sha256(self.base_pointer),
            _sha256(self.base_metadata_path),
            self.policy.digest,
            _sha256(self.universe_path),
            _sha256(self.validity_path),
            _sha256(self.security_master_path),
        )
        epoch_id = dual_lane._derive_epoch_id(
            market_date=market_date,
            code_revision=self.CODE_REVISION,
            input_digest=input_digest,
        )
        self.security_master_path.write_bytes(self.security_master_path.read_bytes() + b"changed")

        with self.assertRaisesRegex(DualLaneError, "sealed governed inputs"):
            publish_epoch_input(
                output=self.root / "changed-input-epoch",
                epoch_id=epoch_id,
                market_date=market_date,
                policy=self.policy,
                universe_policy_path=self.universe_path,
                code_validity_path=self.validity_path,
                security_master_path=self.security_master_path,
                code_revision=self.CODE_REVISION,
                base_pointer=self.base_pointer,
                clock=self._clock(market_date, 16, 30),
            )

    def test_epoch_rejects_parent_pointer_that_disagrees_with_dwd(self) -> None:
        market_date = date(2026, 7, 24)
        candidate = self._candidate(epoch=self._epoch(market_date), lane="cn01")
        parent_pointer = self.root / "invalid-parent-pointer.json"
        self._write_json(
            parent_pointer,
            {
                "revision": candidate.dwd_root.name,
                "revision_path": str(candidate.dwd_root),
                "market_date": market_date.isoformat(),
                "manifest_sha256": f"sha256:{'0' * 64}",
            },
        )

        with self.assertRaisesRegex(DualLaneError, "parent pointer"):
            publish_epoch_input(
                output=self.root / "invalid-parent-epoch",
                epoch_id="epoch-invalid-parent",
                market_date=date(2026, 7, 25),
                policy=self.policy,
                universe_policy_path=self.universe_path,
                code_validity_path=self.validity_path,
                security_master_path=self.security_master_path,
                code_revision=self.CODE_REVISION,
                parent_pointer=parent_pointer,
                clock=self._clock(date(2026, 7, 25), 16, 30),
            )

    def test_epoch_rejects_historical_base_revision_alias(self) -> None:
        pointer = self.root / "invalid-base-pointer.json"
        self._write_json(
            pointer,
            {
                "revision": "not-the-meta-revision",
                "revision_path": str(self.base_metadata_path.parent),
                "meta_sha256": _sha256(self.base_metadata_path),
            },
        )

        with self.assertRaisesRegex(DualLaneError, "does not match its metadata"):
            publish_epoch_input(
                output=self.root / "invalid-base-epoch",
                epoch_id="epoch-invalid-base",
                market_date=date(2026, 7, 24),
                policy=self.policy,
                universe_policy_path=self.universe_path,
                code_validity_path=self.validity_path,
                security_master_path=self.security_master_path,
                code_revision=self.CODE_REVISION,
                base_pointer=pointer,
                clock=self._clock(date(2026, 7, 24), 16, 30),
            )

    def test_legacy_epoch_and_candidate_remain_auditable_but_ineligible(self) -> None:
        market_date = date(2026, 7, 24)
        epoch = self._epoch(market_date)
        candidate = self._candidate(epoch=epoch, lane="cn01")

        legacy_epoch = self.root / "legacy-epoch"
        shutil.copytree(epoch.root, legacy_epoch)
        epoch_manifest = json.loads(
            (legacy_epoch / "manifest.json").read_text(encoding="utf-8")
        )
        references = epoch_manifest["shared_references"]
        security_relative = references.pop("security_master_path")
        references.pop("security_master_sha256")
        epoch_manifest.pop("input_digest")
        (legacy_epoch / security_relative).unlink()
        epoch_manifest["files"] = [
            item for item in epoch_manifest["files"] if item["path"] != security_relative
        ]
        self._rewrite_ready_manifest(legacy_epoch, epoch_manifest)
        verified_epoch = verify_epoch_input(legacy_epoch)
        self.assertIsNone(verified_epoch.security_master_path)

        legacy_candidate = self.root / "legacy-candidate"
        shutil.copytree(candidate.root, legacy_candidate)
        candidate_manifest_path = legacy_candidate / "manifest.json"
        candidate_manifest = json.loads(candidate_manifest_path.read_text(encoding="utf-8"))
        dwd_relative = candidate_manifest["artifacts"]["dwd"]["path"]
        dwd_manifest_path = legacy_candidate / dwd_relative / "manifest.json"
        dwd_manifest = json.loads(dwd_manifest_path.read_text(encoding="utf-8"))
        dwd_manifest["lineage"].pop("security_master_sha256")
        self._write_json(dwd_manifest_path, dwd_manifest)
        candidate_manifest.pop("security_master_sha256")
        candidate_manifest["artifacts"]["dwd"]["manifest_sha256"] = _sha256(
            dwd_manifest_path
        )
        dws_relative = candidate_manifest["artifacts"]["dws"]["path"]
        dws_manifest_path = legacy_candidate / dws_relative / "manifest.json"
        dws_manifest = json.loads(dws_manifest_path.read_text(encoding="utf-8"))
        dws_manifest["lineage"]["dwd_manifest_sha256"] = _sha256(dwd_manifest_path)
        self._write_json(dws_manifest_path, dws_manifest)
        candidate_manifest["artifacts"]["dws"]["manifest_sha256"] = _sha256(
            dws_manifest_path
        )
        dwd_manifest_relative = dwd_manifest_path.relative_to(legacy_candidate).as_posix()
        dws_manifest_relative = dws_manifest_path.relative_to(legacy_candidate).as_posix()
        for item in candidate_manifest["files"]:
            if item["path"] == dwd_manifest_relative:
                item["sha256"] = _sha256(dwd_manifest_path)
                item["size_bytes"] = dwd_manifest_path.stat().st_size
            elif item["path"] == dws_manifest_relative:
                item["sha256"] = _sha256(dws_manifest_path)
                item["size_bytes"] = dws_manifest_path.stat().st_size
        candidate_manifest["candidate_content_digest"] = dual_lane._canonical_digest(
            {
                "lane_id": candidate_manifest["lane_id"],
                "market_date": candidate_manifest["market_date"],
                "epoch_manifest_sha256": candidate_manifest["epoch_manifest_sha256"],
                "ods_manifest_sha256": candidate_manifest["ods_manifest_sha256"],
                "business_content_digest": candidate_manifest["business_content_digest"],
                "code_revision": candidate_manifest["code_revision"],
                "runtime": candidate_manifest["runtime"],
                "contract_id": candidate_manifest["contract_id"],
                "policy_sha256": candidate_manifest["policy_sha256"],
                "eligibility": candidate_manifest["eligibility"],
                "attempt_selection": dual_lane._EXACT_ATTEMPT_SELECTION,
                "source_inputs": candidate_manifest["source_inputs"],
                "knowledge_cutoff": candidate_manifest["knowledge_cutoff"],
            }
        )
        self._rewrite_ready_manifest(legacy_candidate, candidate_manifest)
        verified_candidate = verify_lane_candidate(legacy_candidate)
        self.assertIsNone(verified_candidate.security_master_sha256)

        with self.assertRaisesRegex(DualLaneError, "audit-only"):
            build_lane_candidate(
                output=self.root / "legacy-epoch-candidate",
                candidate_id="candidate-from-legacy-epoch",
                lane_id="cn01",
                attempt_id="attempt-from-legacy-epoch",
                market_date=market_date,
                ods_release_dir=(
                    self.root
                    / "ods"
                    / "cn01"
                    / "daily"
                    / market_date.isoformat()
                    / "attempt-cn01-20260724-base"
                ),
                epoch_root=legacy_epoch,
                policy=self.policy,
                code_revision=self.CODE_REVISION,
            )

    def test_market_release_selection_never_falls_back_from_exact_current(self) -> None:
        market_date = date(2026, 7, 24)
        date_root = self.root / "lane-releases" / "daily" / market_date.isoformat()
        market_attempt = date_root / "attempt-market"
        shutil.copytree(
            self._release(market_date, lane="cn01"),
            market_attempt,
        )
        event_input = self.root / "event-input.json"
        self._write_json(event_input, {"event": "test"})
        event_spec = self.root / "event-spec.json"
        cutoff = datetime.combine(market_date, time(11), tzinfo=timezone.utc)
        self._write_json(
            event_spec,
            {
                "schema_version": 1,
                "release_id": "cn01-calendar-events-test",
                "schedule": "daily",
                "market_date": market_date.isoformat(),
                "producer": "cn01-test",
                "artifacts": [
                    {
                        "name": "announcements_delta",
                        "source_path": str(event_input),
                        "relative_path": "artifacts/announcements.json",
                        "source_revision": "events-test-v1",
                        "knowledge_cutoff": cutoff.isoformat(),
                    }
                ],
            },
        )
        event_attempt = date_root / "attempt-event"
        publish_ods_release(spec_path=event_spec, output=event_attempt)
        event = dual_lane.verify_ods_release(event_attempt)
        self._write_json(
            date_root / "CURRENT.json",
            {
                "schema_version": 1,
                "schedule": "daily",
                "market_date": market_date.isoformat(),
                "attempt_id": event_attempt.name,
                "release_id": event.release.release_id,
                "manifest_sha256": event.manifest_sha256,
                "ready_at": event.release.created_at.isoformat(),
            },
        )

        with self.assertRaisesRegex(DualLaneError, "eligible market-daily"):
            select_market_daily_ods_release(
                date_root,
                market_date=market_date,
                preferred_attempt=event_attempt.name,
            )

    def test_candidate_requires_exact_current_descriptor(self) -> None:
        market_date = date(2026, 7, 24)
        epoch = self._epoch(market_date)
        release = self._release(market_date, lane="cn01")
        current = release.parent / "CURRENT.json"
        payload = json.loads(current.read_text(encoding="utf-8"))
        payload["manifest_sha256"] = f"sha256:{'0' * 64}"
        self._write_json(current, payload)

        with self.assertRaisesRegex(DualLaneError, "ODS CURRENT"):
            build_lane_candidate(
                output=self.root / "candidate-invalid-current",
                candidate_id="candidate-invalid-current",
                lane_id="cn01",
                attempt_id=release.name,
                market_date=market_date,
                ods_release_dir=release,
                epoch_root=epoch.root,
                policy=self.policy,
                code_revision=self.CODE_REVISION,
                clock=self._clock(market_date, 19, 55),
            )

    def test_candidate_preserves_and_enforces_per_input_pit_lineage(self) -> None:
        market_date = date(2026, 7, 24)
        epoch = self._epoch(market_date)
        candidate = self._candidate(epoch=epoch, lane="cn01")

        self.assertEqual(len(candidate.source_inputs), 5)
        self.assertEqual(
            {item["name"] for item in candidate.source_inputs},
            {
                "daily_ohlcv_none_delta",
                "daily_status_observation_delta",
                "security_master",
                "stock_list",
                "trading_calendar",
            },
        )
        shutil.rmtree(self.root / "ods" / "cn01")
        relocated = verify_lane_candidate(candidate.root)
        self.assertEqual(relocated.source_inputs, candidate.source_inputs)

        late_release = self._release(
            market_date,
            lane="hermes",
            variant="late-cutoff",
            knowledge_cutoff=datetime.combine(
                market_date,
                time(20, 1),
                tzinfo=ZoneInfo("Asia/Shanghai"),
            ),
            release_created_at=datetime.combine(
                market_date,
                time(20, 2),
                tzinfo=ZoneInfo("Asia/Shanghai"),
            ),
        )
        with self.assertRaisesRegex(DualLaneError, "PIT cutoff"):
            build_lane_candidate(
                output=self.root / "candidate-late-cutoff",
                candidate_id="candidate-late-cutoff",
                lane_id="hermes",
                attempt_id=late_release.name,
                market_date=market_date,
                ods_release_dir=late_release,
                epoch_root=epoch.root,
                policy=self.policy,
                code_revision=self.CODE_REVISION,
                clock=self._clock(market_date, 19, 55),
            )

    def test_trusted_clock_enforces_candidate_and_arbitration_windows(self) -> None:
        market_date = date(2026, 7, 24)
        epoch = self._epoch(market_date)
        release = self._release(market_date, lane="cn01")
        with self.assertRaisesRegex(DualLaneError, "candidate build window"):
            build_lane_candidate(
                output=self.root / "candidate-after-cutoff",
                candidate_id="candidate-after-cutoff",
                lane_id="cn01",
                attempt_id=release.name,
                market_date=market_date,
                ods_release_dir=release,
                epoch_root=epoch.root,
                policy=self.policy,
                code_revision=self.CODE_REVISION,
                clock=self._clock(market_date, 20, 1),
            )

        primary = self._candidate(epoch=epoch, lane="cn01", variant="on-time")
        with self.assertRaisesRegex(DualLaneError, "before the frozen arbitration"):
            arbitrate_and_publish(
                market_date=market_date,
                primary_candidate_path=primary.root,
                standby_candidate_path=None,
                root=self.root / "clock-publication",
                policy=self.policy,
                expected_epoch=epoch,
                clock=self._clock(market_date, 19, 59),
            )
        missed = arbitrate_and_publish(
            market_date=market_date,
            primary_candidate_path=primary.root,
            standby_candidate_path=None,
            root=self.root / "deadline-publication",
            policy=self.policy,
            expected_epoch=epoch,
            clock=self._clock(market_date, 20, 31),
        )
        self.assertEqual(missed.status, "withheld")
        self.assertEqual(missed.reason, "withheld_completion_deadline_missed")

    def test_terminal_automatic_epoch_cannot_reopen_with_late_inputs(self) -> None:
        market_date = date(2026, 7, 24)
        epoch = self._epoch(market_date)
        publication = self.root / "terminal-publication"
        terminal = arbitrate_and_publish(
            market_date=market_date,
            primary_candidate_path=None,
            standby_candidate_path=None,
            root=publication,
            policy=self.policy,
            expected_epoch=epoch,
            clock=self._clock(market_date, 20, 5),
        )
        self.assertEqual(terminal.status, "withheld")
        terminal_record = next(
            (publication / "state" / "terminal-decisions").rglob("*.json")
        )
        self.assertEqual(
            json.loads(terminal_record.read_text(encoding="utf-8"))["decided_at"],
            self._clock(market_date, 20, 5)().astimezone(timezone.utc).isoformat(),
        )

        primary = self._candidate(epoch=epoch, lane="cn01")
        standby = self._candidate(epoch=epoch, lane="hermes")
        with self.assertRaisesRegex(DualLaneError, "cannot reopen"):
            arbitrate_and_publish(
                market_date=market_date,
                primary_candidate_path=primary.root,
                standby_candidate_path=standby.root,
                root=publication,
                policy=self.policy,
                expected_epoch=epoch,
                clock=self._clock(market_date, 20, 6),
            )

    def test_terminal_decision_retry_repairs_arbitration_state_after_crash(self) -> None:
        first_date = date(2026, 7, 24)
        second_date = date(2026, 7, 25)
        first_epoch = self._epoch(first_date)
        second_epoch = self._epoch(second_date)
        first = self._candidate(epoch=first_epoch, lane="cn01")
        second = self._candidate(epoch=second_epoch, lane="cn01")
        publication = self.root / "terminal-recovery-publication"
        original_atomic_json = dual_lane._atomic_json
        injected = False

        def fail_before_state(path: Path, value: dict[str, object]) -> None:
            nonlocal injected
            if path.name == "arbitration-state.json" and not injected:
                injected = True
                raise RuntimeError("injected after terminal decision")
            original_atomic_json(path, value)

        with (
            mock.patch.object(dual_lane, "_atomic_json", side_effect=fail_before_state),
            self.assertRaisesRegex(RuntimeError, "after terminal decision"),
        ):
            arbitrate_and_publish(
                market_date=first_date,
                primary_candidate_path=first.root,
                standby_candidate_path=None,
                root=publication,
                policy=self.policy,
                expected_epoch=first_epoch,
                clock=self._clock(first_date, 20, 5),
            )

        recovered = arbitrate_and_publish(
            market_date=first_date,
            primary_candidate_path=first.root,
            standby_candidate_path=None,
            root=publication,
            policy=self.policy,
            expected_epoch=first_epoch,
            clock=self._clock(first_date, 20, 6),
        )
        following = arbitrate_and_publish(
            market_date=second_date,
            primary_candidate_path=second.root,
            standby_candidate_path=None,
            root=publication,
            policy=self.policy,
            expected_epoch=second_epoch,
            clock=self._clock(second_date, 20, 5),
        )
        self.assertEqual(recovered.status, "published")
        self.assertEqual(following.reason, "withheld_standby_absence_budget_exceeded")

    def test_same_business_content_selects_cn01_and_publishes_release_set_ads(self) -> None:
        market_date = date(2026, 7, 24)
        epoch = self._epoch(market_date)
        primary = self._candidate(epoch=epoch, lane="cn01")
        standby = self._candidate(epoch=epoch, lane="hermes")

        self.assertEqual(primary.business_content_digest, standby.business_content_digest)
        self.assertNotEqual(primary.candidate_content_digest, standby.candidate_content_digest)

        publication_root = self.root / "publication"
        result = arbitrate_and_publish(
            market_date=market_date,
            primary_candidate_path=primary.root,
            standby_candidate_path=standby.root,
            root=publication_root,
            policy=self.policy,
            expected_epoch=epoch,
            clock=self._clock(market_date, 20, 5),
        )

        self.assertEqual(result.status, "published")
        self.assertEqual(result.reason, "primary")
        self.assertEqual(result.selected_lane, "cn01")
        pointer = json.loads(
            (publication_root / "release-set_CURRENT.json").read_text(encoding="utf-8")
        )
        release_root = publication_root / pointer["release_set_path"]
        release = json.loads((release_root / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(release["release_set_id"], result.release_set_id)
        self.assertEqual(_sha256(release_root / "manifest.json"), pointer["manifest_sha256"])
        ads_root = publication_root / release["operational_ads_path"]
        ads = json.loads((ads_root / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(ads["profile"], "operational_daily_v1")
        self.assertEqual(ads["catalog_id"], Path(release["catalog_path"]).parent.name)
        self.assertEqual(ads["catalog_sha256"], release["catalog_sha256"])
        self.assertEqual(ads["generation_ids"], [release["generation_id"]])
        self.assertTrue(ads["research_only"])
        self.assertFalse(ads["execution_authorized"])
        self.assertEqual(
            {table["name"] for table in ads["tables"]},
            {"market_daily", "market_status", "market_summary"},
        )
        for table in ads["tables"]:
            self.assertEqual(table["role"], "decision_visible")
            self.assertEqual(len(table["parts"]), 1)
            part = table["parts"][0]
            self.assertNotIn("..", Path(part["path"]).parts)
            self.assertEqual(_sha256(ads_root / part["path"]), part["sha256"])
            self.assertRegex(part["schema_sha256"], r"^sha256:[0-9a-f]{64}$")
            self.assertEqual(part["min_market_date"], market_date.isoformat())
            self.assertEqual(part["max_market_date"], market_date.isoformat())

    def test_lane_local_security_master_drift_cannot_change_candidate_reference(self) -> None:
        market_date = date(2026, 7, 24)
        epoch = self._epoch(market_date)
        primary = self._candidate(epoch=epoch, lane="cn01")
        standby = self._candidate(
            epoch=epoch,
            lane="hermes",
            variant="local-reference-drift",
        )

        self.assertEqual(primary.business_content_digest, standby.business_content_digest)
        self.assertEqual(
            _sha256(epoch.security_master_path),
            json.loads((primary.dwd_root / "manifest.json").read_text(encoding="utf-8"))["lineage"][
                "security_master_sha256"
            ],
        )

    def test_candidate_retry_cannot_reuse_a_different_attempt(self) -> None:
        market_date = date(2026, 7, 24)
        epoch = self._epoch(market_date)
        candidate = self._candidate(epoch=epoch, lane="cn01")

        with self.assertRaisesRegex(DualLaneError, "existing candidate does not match"):
            build_lane_candidate(
                output=candidate.root,
                candidate_id=candidate.candidate_id,
                lane_id="cn01",
                attempt_id="attempt-cn01-20260724-retry",
                market_date=market_date,
                ods_release_dir=(
                    self.root
                    / "ods"
                    / "cn01"
                    / "daily"
                    / market_date.isoformat()
                    / "attempt-cn01-20260724-base"
                ),
                epoch_root=epoch.root,
                policy=self.policy,
                code_revision=self.CODE_REVISION,
                clock=self._clock(market_date, 19, 55),
            )

    def test_candidate_manifest_cannot_forge_epoch_security_master_lineage(self) -> None:
        market_date = date(2026, 7, 24)
        candidate = self._candidate(epoch=self._epoch(market_date), lane="cn01")
        forged = self.root / "forged-security-master"
        shutil.copytree(candidate.root, forged)
        manifest = json.loads((forged / "manifest.json").read_text(encoding="utf-8"))
        manifest["security_master_sha256"] = f"sha256:{'0' * 64}"
        self._rewrite_ready_manifest(forged, manifest)

        with self.assertRaisesRegex(DualLaneError, "security-master lineage mismatch"):
            verify_lane_candidate(forged)

    def test_material_cross_lane_divergence_withholds_publication(self) -> None:
        market_date = date(2026, 7, 24)
        epoch = self._epoch(market_date)
        primary = self._candidate(epoch=epoch, lane="cn01")
        standby = self._candidate(
            epoch=epoch,
            lane="hermes",
            close=10.8,
            variant="divergent",
        )
        self.assertNotEqual(primary.business_content_digest, standby.business_content_digest)

        publication_root = self.root / "publication"
        result = arbitrate_and_publish(
            market_date=market_date,
            primary_candidate_path=primary.root,
            standby_candidate_path=standby.root,
            root=publication_root,
            policy=self.policy,
            expected_epoch=epoch,
            clock=self._clock(market_date, 20, 5),
        )

        self.assertEqual(result.status, "withheld")
        self.assertEqual(result.reason, "withheld_unattributed_divergence")
        self.assertFalse((publication_root / "release-set_CURRENT.json").exists())

    def test_trusted_oci_epoch_blocks_a_self_consistent_forged_candidate(self) -> None:
        market_date = date(2026, 7, 24)
        epoch = self._epoch(market_date)
        primary = self._candidate(epoch=epoch, lane="cn01")
        standby = self._candidate(epoch=epoch, lane="hermes")
        forged = self.root / "forged-primary"
        shutil.copytree(primary.root, forged)
        manifest = json.loads((forged / "manifest.json").read_text(encoding="utf-8"))
        manifest["epoch_manifest_sha256"] = f"sha256:{'0' * 64}"
        manifest["candidate_content_digest"] = dual_lane._canonical_digest(
            {
                "lane_id": manifest["lane_id"],
                "market_date": manifest["market_date"],
                "epoch_manifest_sha256": manifest["epoch_manifest_sha256"],
                "ods_manifest_sha256": manifest["ods_manifest_sha256"],
                "business_content_digest": manifest["business_content_digest"],
                "code_revision": manifest["code_revision"],
                "runtime": manifest["runtime"],
                "contract_id": manifest["contract_id"],
                "policy_sha256": manifest["policy_sha256"],
                "security_master_sha256": manifest["security_master_sha256"],
                "eligibility": manifest["eligibility"],
                "attempt_selection": dual_lane._EXACT_ATTEMPT_SELECTION,
                "source_inputs": manifest["source_inputs"],
                "knowledge_cutoff": manifest["knowledge_cutoff"],
            }
        )
        self._rewrite_ready_manifest(forged, manifest)

        result = arbitrate_and_publish(
            market_date=market_date,
            primary_candidate_path=forged,
            standby_candidate_path=standby.root,
            root=self.root / "publication",
            policy=self.policy,
            expected_epoch=epoch,
            clock=self._clock(market_date, 20, 5),
        )

        self.assertEqual(result.status, "withheld")
        self.assertEqual(result.reason, "withheld_epoch_mismatch")
        self.assertFalse((self.root / "publication" / "release-set_CURRENT.json").exists())

    def test_independently_invalid_primary_allows_declared_fallback(self) -> None:
        market_date = date(2026, 7, 24)
        epoch = self._epoch(market_date)
        primary = self._candidate(epoch=epoch, lane="cn01")
        standby = self._candidate(epoch=epoch, lane="hermes")
        invalid_primary = self.root / "invalid-primary"
        shutil.copytree(primary.root, invalid_primary)
        revision_suffix = (
            f"cn01_{market_date.strftime('%Y%m%d')}_"
            f"attempt-cn01-{market_date.strftime('%Y%m%d')}-base"
        )
        primary_part = (
            invalid_primary
            / "artifacts"
            / f"daily_market_dwd_v2_{revision_suffix}"
            / f"market_date={market_date.isoformat()}"
            / "part-00000.parquet"
        )
        primary_part.write_bytes(primary_part.read_bytes() + b"tampered")

        result = arbitrate_and_publish(
            market_date=market_date,
            primary_candidate_path=invalid_primary,
            standby_candidate_path=standby.root,
            root=self.root / "publication",
            policy=self.policy,
            expected_epoch=epoch,
            clock=self._clock(market_date, 20, 5),
        )

        self.assertEqual(result.status, "published")
        self.assertEqual(result.reason, "primary_failed_over:invalid_candidate")
        self.assertEqual(result.selected_lane, "hermes")

    def test_standby_absence_budget_allows_one_session_then_withholds(self) -> None:
        first_date = date(2026, 7, 24)
        second_date = date(2026, 7, 25)
        first_epoch = self._epoch(first_date)
        second_epoch = self._epoch(second_date)
        first = self._candidate(epoch=first_epoch, lane="cn01")
        second = self._candidate(epoch=second_epoch, lane="cn01")
        publication_root = self.root / "publication"

        allowed = arbitrate_and_publish(
            market_date=first_date,
            primary_candidate_path=first.root,
            standby_candidate_path=None,
            root=publication_root,
            policy=self.policy,
            expected_epoch=first_epoch,
            clock=self._clock(first_date, 20, 5),
        )
        withheld = arbitrate_and_publish(
            market_date=second_date,
            primary_candidate_path=second.root,
            standby_candidate_path=None,
            root=publication_root,
            policy=self.policy,
            expected_epoch=second_epoch,
            clock=self._clock(second_date, 20, 5),
        )

        self.assertEqual(allowed.reason, "primary_without_comparison")
        self.assertEqual(allowed.status, "published")
        self.assertEqual(withheld.reason, "withheld_standby_absence_budget_exceeded")
        self.assertEqual(withheld.status, "withheld")
        pointer = json.loads(
            (publication_root / "release-set_CURRENT.json").read_text(encoding="utf-8")
        )
        release = json.loads(
            (publication_root / pointer["release_set_path"] / "manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(release["market_date"], first_date.isoformat())

    def test_identical_arbitration_retry_is_idempotent(self) -> None:
        market_date = date(2026, 7, 24)
        epoch = self._epoch(market_date)
        primary = self._candidate(epoch=epoch, lane="cn01")
        standby = self._candidate(epoch=epoch, lane="hermes")
        publication_root = self.root / "publication"

        first = arbitrate_and_publish(
            market_date=market_date,
            primary_candidate_path=primary.root,
            standby_candidate_path=standby.root,
            root=publication_root,
            policy=self.policy,
            expected_epoch=epoch,
            clock=self._clock(market_date, 20, 5),
        )
        second = arbitrate_and_publish(
            market_date=market_date,
            primary_candidate_path=primary.root,
            standby_candidate_path=standby.root,
            root=publication_root,
            policy=self.policy,
            expected_epoch=epoch,
            clock=self._clock(market_date, 20, 5),
        )

        self.assertEqual(second.status, "published")
        self.assertEqual(second.release_set_id, first.release_set_id)
        self.assertEqual(second.selected_lane, first.selected_lane)

    def test_retry_recovers_after_publication_event_before_current_crash(self) -> None:
        market_date = date(2026, 7, 24)
        epoch = self._epoch(market_date)
        primary = self._candidate(epoch=epoch, lane="cn01")
        standby = self._candidate(epoch=epoch, lane="hermes")
        publication_root = self.root / "publication"
        original_atomic_json = dual_lane._atomic_json
        injected = False

        def fail_before_current(path: Path, value: dict[str, object]) -> None:
            nonlocal injected
            if path.name == "release-set_CURRENT.json" and not injected:
                injected = True
                raise RuntimeError("injected before CURRENT")
            original_atomic_json(path, value)

        with (
            mock.patch.object(dual_lane, "_atomic_json", side_effect=fail_before_current),
            self.assertRaisesRegex(RuntimeError, "injected before CURRENT"),
        ):
            arbitrate_and_publish(
                market_date=market_date,
                primary_candidate_path=primary.root,
                standby_candidate_path=standby.root,
                root=publication_root,
                policy=self.policy,
                expected_epoch=epoch,
                clock=self._clock(market_date, 20, 5),
            )

        self.assertFalse((publication_root / "release-set_CURRENT.json").exists())
        events_before = sorted((publication_root / "publication-events").glob("*.json"))
        self.assertEqual(len(events_before), 1)

        recovered = arbitrate_and_publish(
            market_date=market_date,
            primary_candidate_path=primary.root,
            standby_candidate_path=standby.root,
            root=publication_root,
            policy=self.policy,
            expected_epoch=epoch,
            clock=self._clock(market_date, 20, 5),
        )

        self.assertEqual(recovered.status, "published")
        self.assertTrue((publication_root / "release-set_CURRENT.json").is_file())
        self.assertEqual(
            sorted((publication_root / "publication-events").glob("*.json")),
            events_before,
        )

    def test_candidate_dws_relative_lineage_survives_directory_copy(self) -> None:
        market_date = date(2026, 7, 24)
        candidate = self._candidate(epoch=self._epoch(market_date), lane="cn01")
        copied = self.root / "relocated" / "candidate"
        shutil.copytree(candidate.root, copied)
        shutil.rmtree(candidate.root)

        verified = verify_lane_candidate(copied)
        dws_manifest = json.loads((verified.dws_root / "manifest.json").read_text(encoding="utf-8"))
        lineage_path = Path(dws_manifest["lineage"]["dwd_revision_path"])
        self.assertFalse(lineage_path.is_absolute())
        self.assertEqual((verified.dws_root / lineage_path).resolve(), verified.dwd_root)

    def test_sealed_candidate_and_current_are_group_readable_under_restrictive_umask(self) -> None:
        market_date = date(2026, 7, 24)
        previous_umask = os.umask(0o077)
        try:
            candidate = self._candidate(epoch=self._epoch(market_date), lane="cn01")
            current = self.root / "candidate-current.json"
            dual_lane.write_candidate_current(candidate, current)
        finally:
            os.umask(previous_umask)

        self.assertEqual(candidate.root.stat().st_mode & 0o777, 0o750)
        for path in candidate.root.rglob("*"):
            expected = 0o750 if path.is_dir() else 0o640
            self.assertEqual(path.stat().st_mode & 0o777, expected, path)
        self.assertEqual(current.stat().st_mode & 0o777, 0o640)

    def test_fenced_lease_is_single_owner_across_processes(self) -> None:
        context = multiprocessing.get_context("fork")
        results = context.Queue()
        root = self.root / "multiprocess-publication"
        now = self._clock(date(2026, 7, 24), 20, 5)().isoformat()
        processes = [
            context.Process(
                target=_acquire_lease_process,
                args=(str(self.policy_path), str(root), owner, now, results),
            )
            for owner in ("writer-a", "writer-b")
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(10)
            self.assertEqual(process.exitcode, 0)

        outcomes = sorted(results.get(timeout=2) for _ in processes)
        self.assertEqual([item[0] for item in outcomes].count("acquired"), 1)
        self.assertEqual([item[0] for item in outcomes].count("rejected"), 1)
        lease_events = sorted(
            (root / "state" / "fenced-leases" / "canonical-publisher" / "events").glob(
                "*.json"
            )
        )
        self.assertEqual(len(lease_events), 1)

    def test_concurrent_arbiters_publish_one_acceptance_and_one_pointer_cas(self) -> None:
        market_date = date(2026, 7, 24)
        epoch = self._epoch(market_date)
        primary = self._candidate(epoch=epoch, lane="cn01")
        standby = self._candidate(epoch=epoch, lane="hermes")
        root = self.root / "multiprocess-arbitration"
        context = multiprocessing.get_context("fork")
        results = context.Queue()
        arguments = (
            str(self.policy_path),
            str(epoch.root),
            str(primary.root),
            str(standby.root),
            str(root),
            market_date.isoformat(),
            self._clock(market_date, 20, 5)().isoformat(),
            results,
        )
        processes = [
            context.Process(target=_arbitrate_process, args=arguments) for _ in range(2)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(20)
            self.assertEqual(process.exitcode, 0)

        outcomes = [results.get(timeout=2) for _ in processes]
        self.assertEqual({item[0] for item in outcomes}, {"published"})
        self.assertEqual(len({item[1] for item in outcomes}), 1)
        self.assertEqual(
            len(list((root / "canonical" / "acceptance").rglob("*.json"))), 1
        )
        self.assertEqual(len(list((root / "pointer-cas-events").glob("*.json"))), 1)
        self.assertEqual(len(list((root / "publication-events").glob("*.json"))), 1)

    def test_lease_crash_does_not_reuse_fencing_token(self) -> None:
        root = self.root / "lease-crash-publication"
        original_atomic_json = dual_lane._atomic_json

        def fail_after_lease_event(path: Path, value: dict[str, object]) -> None:
            if path.name == "CURRENT.json" and path.parent.name == "canonical-publisher":
                raise RuntimeError("injected after lease event")
            original_atomic_json(path, value)

        with (
            mock.patch.object(dual_lane, "_atomic_json", side_effect=fail_after_lease_event),
            self.assertRaisesRegex(RuntimeError, "after lease event"),
        ):
            dual_lane.acquire_fenced_lease(
                root=root,
                policy=self.policy,
                owner="writer-a",
                clock=self._clock(date(2026, 7, 24), 20, 5),
            )

        recovered = dual_lane.acquire_fenced_lease(
            root=root,
            policy=self.policy,
            owner="writer-b",
            clock=self._clock(date(2026, 7, 24), 20, 5),
        )
        self.assertEqual(recovered.fencing_token, 2)
        events = sorted(
            (root / "state" / "fenced-leases" / "canonical-publisher" / "events").glob(
                "*.json"
            )
        )
        self.assertEqual([json.loads(path.read_text())["fencing_token"] for path in events], [1, 2])

    def test_expired_lease_takeover_fences_stale_pointer_commit(self) -> None:
        market_date = date(2026, 7, 24)
        epoch = self._epoch(market_date)
        primary = self._candidate(epoch=epoch, lane="cn01")
        standby = self._candidate(epoch=epoch, lane="hermes")
        root = self.root / "fenced-publication"
        result = arbitrate_and_publish(
            market_date=market_date,
            primary_candidate_path=primary.root,
            standby_candidate_path=standby.root,
            root=root,
            policy=self.policy,
            expected_epoch=epoch,
            clock=self._clock(market_date, 20, 5),
        )
        stale = dual_lane.acquire_fenced_lease(
            root=root,
            policy=self.policy,
            owner=self.policy.target_publisher_owner,
            clock=self._clock(market_date, 20, 5),
        )
        takeover = dual_lane.acquire_fenced_lease(
            root=root,
            policy=self.policy,
            owner=self.policy.legacy_publisher_owner,
            clock=self._clock(market_date, 20, 36),
        )
        self.assertGreater(takeover.fencing_token, stale.fencing_token)
        pointer_path = root / "release-set_CURRENT.json"
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(DualLaneError, "fenced by a newer lease"):
            dual_lane.compare_and_swap_release_pointer(
                root=root,
                policy=self.policy,
                release_set_root=root / "release-sets" / str(result.release_set_id),
                publication_event_path=root / pointer["publication_event_path"],
                expected_pointer_sha256=_sha256(pointer_path),
                lease=stale,
                reason_code="stale_retry",
                clock=self._clock(market_date, 20, 36),
            )

    def test_automatic_arbitration_rechecks_lease_expiry_before_pointer_commit(self) -> None:
        market_date = date(2026, 7, 24)
        epoch = self._epoch(market_date)
        primary = self._candidate(epoch=epoch, lane="cn01")
        standby = self._candidate(epoch=epoch, lane="hermes")
        times = iter(
            (
                self._clock(market_date, 20, 0)(),
                self._clock(market_date, 20, 32)(),
            )
        )
        root = self.root / "expired-automatic-publication"

        with self.assertRaisesRegex(DualLaneError, "lease expired before commit"):
            arbitrate_and_publish(
                market_date=market_date,
                primary_candidate_path=primary.root,
                standby_candidate_path=standby.root,
                root=root,
                policy=self.policy,
                expected_epoch=epoch,
                clock=lambda: next(times),
            )

        self.assertFalse((root / "release-set_CURRENT.json").exists())

    def test_go_for_cutover_requires_environment_and_complete_evidence(self) -> None:
        self.assertFalse(dual_lane.live_activation_requested(self.policy, {}))
        self.assertTrue(
            dual_lane.live_activation_requested(self.policy, {"GO_FOR_CUTOVER": "1"})
        )
        with self.assertRaisesRegex(DualLaneError, "must be 0, 1, or unset"):
            dual_lane.live_activation_requested(self.policy, {"GO_FOR_CUTOVER": "yes"})

        market_date = date(2026, 7, 24)
        gate_path = self.root / "GO_FOR_CUTOVER.json"
        legacy_lease = dual_lane.acquire_fenced_lease(
            root=self.root,
            policy=self.policy,
            owner=self.policy.legacy_publisher_owner,
            clock=self._clock(market_date, 19, 30),
        )
        self._write_json(
            gate_path,
            {
                "schema_version": 1,
                "kind": "go_for_cutover",
                "contract_id": self.policy.contract_id,
                "policy_sha256": self.policy.digest,
                "approved": True,
                "live_writer": self.policy.target_publisher_owner,
                "legacy_fencing_token": legacy_lease.fencing_token,
                "reason_code": "go_for_cutover_approved",
                "approved_at": self._clock(market_date, 20, 0)().isoformat(),
                "expires_at": self._clock(market_date, 21, 0)().isoformat(),
                "evidence": {
                    "legacy_writer_fenced": True,
                    "retention_gate_passed": True,
                    "restore_drill_passed": True,
                },
            },
        )
        verified = dual_lane.verify_go_for_cutover(
            gate_path=gate_path,
            policy=self.policy,
            clock=self._clock(market_date, 20, 5),
        )
        self.assertTrue(verified["approved"])

    def test_late_candidate_correction_approval_is_append_only(self) -> None:
        market_date = date(2026, 7, 24)
        epoch = self._epoch(market_date)
        root = self.root / "correction-governance"
        standby = self._candidate(epoch=epoch, lane="hermes")
        published = arbitrate_and_publish(
            market_date=market_date,
            primary_candidate_path=None,
            standby_candidate_path=standby.root,
            root=root,
            policy=self.policy,
            expected_epoch=epoch,
            clock=self._clock(market_date, 20, 5),
        )
        self.assertEqual(published.selected_lane, "hermes")
        pointer_path = root / "release-set_CURRENT.json"
        pointer_before = _sha256(pointer_path)
        release = json.loads(
            (
                root
                / json.loads(pointer_path.read_text(encoding="utf-8"))["release_set_path"]
                / "manifest.json"
            ).read_text(encoding="utf-8")
        )
        active_generation_id = str(release["generation_id"])
        accepted_path = root / str(release["generation_path"])
        equivalent = self._candidate(epoch=epoch, lane="cn01")
        first = dual_lane.evaluate_late_candidate(
            root=root,
            policy=self.policy,
            active_generation_id=active_generation_id,
            accepted_candidate_path=accepted_path,
            late_candidate_path=equivalent.root,
            actor="oci-arbiter",
            reason_code="late_primary_equivalent",
            clock=self._clock(market_date, 20, 10),
        )
        self.assertEqual(first.action, "late_equivalent")
        self.assertEqual(_sha256(pointer_path), pointer_before)

        material = self._candidate(
            epoch=epoch,
            lane="cn01",
            close=11.0,
            variant="material-late",
        )
        proposal = dual_lane.evaluate_late_candidate(
            root=root,
            policy=self.policy,
            active_generation_id=active_generation_id,
            accepted_candidate_path=accepted_path,
            late_candidate_path=material.root,
            actor="oci-arbiter",
            reason_code="late_primary_material_difference",
            clock=self._clock(market_date, 20, 11),
        )
        self.assertEqual(proposal.action, "correction_proposed")
        approval = dual_lane.approve_correction(
            root=root,
            policy=self.policy,
            proposal_event_path=proposal.path,
            replacement_generation_id="generation-corrected",
            impact_digest="sha256:" + "a" * 64,
            authority="data-owner",
            reason_code="correction_diff_approved",
            clock=self._clock(market_date, 20, 12),
        )
        self.assertEqual(approval.action, "correction_approved")
        self.assertTrue(first.path.is_file())
        self.assertTrue(proposal.path.is_file())
        self.assertTrue(approval.path.is_file())
        self.assertEqual(_sha256(pointer_path), pointer_before)

    def test_revocation_then_fenced_rollback_selects_older_complete_release(self) -> None:
        first_date = date(2026, 7, 24)
        second_date = date(2026, 7, 25)
        root = self.root / "rollback-publication"
        releases: list[str] = []
        for market_date in (first_date, second_date):
            epoch = self._epoch(market_date)
            result = arbitrate_and_publish(
                market_date=market_date,
                primary_candidate_path=self._candidate(epoch=epoch, lane="cn01").root,
                standby_candidate_path=self._candidate(epoch=epoch, lane="hermes").root,
                root=root,
                policy=self.policy,
                expected_epoch=epoch,
                clock=self._clock(market_date, 20, 5),
            )
            assert result.release_set_id is not None
            releases.append(result.release_set_id)

        current = json.loads((root / "release-set_CURRENT.json").read_text(encoding="utf-8"))
        current_release = json.loads(
            (root / current["release_set_path"] / "manifest.json").read_text(encoding="utf-8")
        )
        revocation = dual_lane.record_generation_revocation(
            root=root,
            policy=self.policy,
            generation_path=root / current_release["generation_path"],
            authority="data-owner",
            reason_code="integrity_failure_confirmed",
            integrity_evidence_digest="sha256:" + "b" * 64,
            clock=self._clock(second_date, 20, 6),
        )
        self.assertEqual(revocation.action, "generation_revoked")

        original_append = dual_lane._append_governance_transition
        injected = False

        def fail_after_pointer(**kwargs):
            nonlocal injected
            if kwargs["action"] == "rollback_published" and not injected:
                injected = True
                raise RuntimeError("injected after rollback pointer CAS")
            return original_append(**kwargs)

        with (
            mock.patch.object(
                dual_lane,
                "_append_governance_transition",
                side_effect=fail_after_pointer,
            ),
            self.assertRaisesRegex(RuntimeError, "after rollback pointer CAS"),
        ):
            dual_lane.rollback_release_set(
                root=root,
                policy=self.policy,
                target_release_set_id=releases[0],
                authority="release-owner",
                reason_code="rollback_after_revocation",
                clock=self._clock(second_date, 20, 7),
            )
        self.assertEqual(
            json.loads((root / "release-set_CURRENT.json").read_text())["release_set_id"],
            releases[0],
        )
        rollback = dual_lane.rollback_release_set(
            root=root,
            policy=self.policy,
            target_release_set_id=releases[0],
            authority="release-owner",
            reason_code="rollback_after_revocation",
            clock=self._clock(second_date, 20, 8),
        )
        self.assertEqual(rollback.action, "rollback_published")
        pointer = json.loads((root / "release-set_CURRENT.json").read_text(encoding="utf-8"))
        self.assertEqual(pointer["release_set_id"], releases[0])
        self.assertEqual(pointer["reason_code"], "approved_rollback")
        self.assertTrue(revocation.path.is_file())

    def test_rollback_rechecks_lease_expiry_before_pointer_commit(self) -> None:
        first_date = date(2026, 7, 24)
        second_date = date(2026, 7, 25)
        root = self.root / "expired-rollback-publication"
        releases: list[str] = []
        for market_date in (first_date, second_date):
            epoch = self._epoch(market_date)
            result = arbitrate_and_publish(
                market_date=market_date,
                primary_candidate_path=self._candidate(epoch=epoch, lane="cn01").root,
                standby_candidate_path=self._candidate(epoch=epoch, lane="hermes").root,
                root=root,
                policy=self.policy,
                expected_epoch=epoch,
                clock=self._clock(market_date, 20, 5),
            )
            assert result.release_set_id is not None
            releases.append(result.release_set_id)
        pointer_path = root / "release-set_CURRENT.json"
        pointer_before = _sha256(pointer_path)
        times = iter(
            (
                self._clock(second_date, 20, 6)(),
                self._clock(second_date, 20, 7)(),
                self._clock(second_date, 20, 39)(),
            )
        )

        with self.assertRaisesRegex(DualLaneError, "lease expired before commit"):
            dual_lane.rollback_release_set(
                root=root,
                policy=self.policy,
                target_release_set_id=releases[0],
                authority="release-owner",
                reason_code="operator_requested_rollback",
                clock=lambda: next(times),
            )

        self.assertEqual(_sha256(pointer_path), pointer_before)

    def test_accepted_ods_recovery_requires_two_domains_and_proves_restore(self) -> None:
        market_date = date(2026, 7, 24)
        candidate = self._candidate(epoch=self._epoch(market_date), lane="cn01")
        source_release = (
            self.root
            / "ods"
            / "cn01"
            / "daily"
            / market_date.isoformat()
            / candidate.attempt_id
        )
        recovery_root = self.root / "accepted-ods-governance"
        retention_until = datetime.combine(
            market_date + timedelta(days=100),
            time(0, 0),
            tzinfo=ZoneInfo("Asia/Shanghai"),
        )
        dual_lane.record_accepted_ods_copy(
            root=recovery_root,
            policy=self.policy,
            accepted_candidate_path=candidate.root,
            ods_release_copy_path=source_release,
            copy_id="source-copy",
            location_class="source_lane",
            failure_domain="cn01-node",
            retention_until=retention_until,
            clock=self._clock(market_date, 20, 5),
        )
        insufficient = dual_lane.assess_accepted_ods_recovery(
            root=recovery_root,
            policy=self.policy,
            accepted_candidate_path=candidate.root,
            clock=self._clock(market_date, 20, 5),
        )
        self.assertFalse(insufficient.passed)

        independent = self.root / "independent-recovery" / source_release.name
        independent.parent.mkdir(parents=True)
        shutil.copytree(source_release, independent)
        dual_lane.record_accepted_ods_copy(
            root=recovery_root,
            policy=self.policy,
            accepted_candidate_path=candidate.root,
            ods_release_copy_path=independent,
            copy_id="recovery-copy",
            location_class="independent_recovery",
            failure_domain="recovery-store-a",
            retention_until=retention_until,
            clock=self._clock(market_date, 20, 6),
        )
        passed = dual_lane.assess_accepted_ods_recovery(
            root=recovery_root,
            policy=self.policy,
            accepted_candidate_path=candidate.root,
            clock=self._clock(market_date, 20, 6),
        )
        self.assertTrue(passed.passed)
        rebuilt = self.root / "clean-restore" / "candidate"
        rebuilt.parent.mkdir(parents=True)
        shutil.copytree(candidate.root, rebuilt)
        drill = dual_lane.record_accepted_ods_restore_drill(
            root=recovery_root,
            policy=self.policy,
            accepted_candidate_path=candidate.root,
            restored_ods_release_path=independent,
            rebuilt_candidate_path=rebuilt,
            started_at=self._clock(market_date, 20, 0)(),
            actor="recovery-operator",
            clock=self._clock(market_date, 20, 7),
        )
        self.assertTrue(drill.is_file())
        purge = dual_lane.record_accepted_ods_purge_gate(
            root=recovery_root,
            policy=self.policy,
            accepted_candidate_path=candidate.root,
            actor="retention-auditor",
            clock=self._clock(market_date, 20, 8),
        )
        self.assertFalse(json.loads(purge.read_text(encoding="utf-8"))["eligible"])

    def test_transfer_helper_is_compatible_with_rrsync_forced_commands(self) -> None:
        project_root = Path(__file__).resolve().parents[2]
        for name in (
            "sync-dual-lane-date-root",
            "sync-dual-lane-history-base",
            "sync-dwd-dws-base",
        ):
            with self.subTest(helper=name):
                helper = (project_root / "ops/bin" / name).read_text(encoding="utf-8")
                self.assertNotIn("--protect-args", helper)
                self.assertNotIn("--delete", helper)
                if name == "sync-dwd-dws-base":
                    self.assertNotIn("--ignore-existing", helper)
                    self.assertIn("--attempt-id", helper)
                    self.assertIn("dwd-dws-bases/attempts/${attempt_id}", helper)
                    self.assertIn('[[ "$remote_root" != "." ]]', helper)
                else:
                    self.assertIn("--ignore-existing", helper)

    def test_dwd_dws_transfer_rejects_host_absolute_rrsync_root(self) -> None:
        project_root = Path(__file__).resolve().parents[2]
        dwd_package = self.root / "dwd-package"
        dws_package = self.root / "dws-package"
        for package in (dwd_package, dws_package):
            package.mkdir()
            (package / "manifest.json").write_text("{}\n", encoding="utf-8")
            (package / "READY").write_text("ready\n", encoding="utf-8")

        result = subprocess.run(
            [
                project_root / "ops/bin/sync-dwd-dws-base",
                "--dwd-package",
                dwd_package,
                "--dws-package",
                dws_package,
                "--attempt-id",
                "test-attempt-v1",
                "--remote-root",
                "/root/work/astock/data",
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("forced command is jailed", result.stderr)

    def test_daily_history_catalog_refresh_is_success_triggered_and_independent(self) -> None:
        project_root = Path(__file__).resolve().parents[2]
        systemd_root = project_root / "ops" / "systemd"
        candidate_unit = (systemd_root / "quanthus-dual-lane-candidate.service").read_text(
            encoding="utf-8"
        )
        catalog_unit = (systemd_root / "quanthus-dual-lane-history-catalog.service").read_text(
            encoding="utf-8"
        )
        runner = (project_root / "ops" / "bin" / "run-dual-lane-history-catalog").read_text(
            encoding="utf-8"
        )
        doctor = (project_root / "ops" / "bin" / "dual-lane-doctor").read_text(encoding="utf-8")

        self.assertIn("OnSuccess=quanthus-dual-lane-history-catalog.service", candidate_unit)
        self.assertIn("OnFailure=quanthus-pipeline-failure@%n.service", catalog_unit)
        self.assertIn(
            "ExecStart=/root/work/Quanthus/ops/bin/run-dual-lane-history-catalog", catalog_unit
        )
        self.assertIn("build-dual-lane-history-catalog", runner)
        self.assertIn("verify-dual-lane-history", runner)
        self.assertIn("build-dwd-dws-base-catalog", runner)
        self.assertIn("verify-dwd-dws-base-catalog", runner)
        self.assertIn("use_monthly_base", runner)
        self.assertIn("flock -n", runner)
        self.assertNotIn("--delete", runner)
        self.assertIn("candidate service does not load the daily lane-history", doctor)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

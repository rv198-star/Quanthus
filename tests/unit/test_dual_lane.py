from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import unittest
from datetime import date, datetime, time, timezone
from pathlib import Path
from unittest import mock

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
        base = self.root / "historical-base" / "p1-history-base-v1"
        base.mkdir(parents=True)
        self._write_json(base / "META.json", {"revision": base.name, "kind": "test-base"})
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
                "cross_lane_divergence_policy": "withhold_both",
                "row_or_artifact_mixing": False,
            },
            "publication": {
                "consumer_pointer": "release-set_CURRENT.json",
                "operational_ads_required": True,
                "legacy_dataset_pointers_are_authoritative": False,
                "postgresql_projection_authoritative": False,
            },
            "retention": {
                "migration_mode": "copy_only",
                "delete_enabled": False,
                "retain_legacy_oci_ods": True,
                "retain_legacy_dwd_dws": True,
                "retain_all_migration_candidates": True,
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

    def _epoch(self, market_date: date) -> EpochInput:
        return publish_epoch_input(
            output=self.root / "epochs" / market_date.isoformat(),
            epoch_id=f"epoch-{market_date.strftime('%Y%m%d')}",
            market_date=market_date,
            policy=self.policy,
            universe_policy_path=self.universe_path,
            code_validity_path=self.validity_path,
            code_revision=self.CODE_REVISION,
            base_pointer=self.base_pointer,
        )

    def _release(
        self,
        market_date: date,
        *,
        lane: str,
        close: float = 10.1,
        variant: str = "base",
    ) -> Path:
        assert pa is not None
        assert pq is not None
        root = self.root / "ods" / market_date.isoformat() / f"{lane}-{variant}"
        inputs = root / "inputs"
        inputs.mkdir(parents=True)
        built_at = datetime.combine(market_date, time(8), tzinfo=timezone.utc)
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
                        "as_of": market_date,
                        "source": "test",
                        "built_at": built_at,
                    }
                ]
            ),
            master,
        )
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
        release = root / "release"
        publish_ods_release(spec_path=spec, output=release)
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

    def test_epoch_is_portable_and_rejects_tamper_and_path_traversal(self) -> None:
        epoch = self._epoch(date(2026, 7, 24))
        portable = self.root / "portable" / "epoch"
        shutil.copytree(epoch.root, portable)

        relocated = verify_epoch_input(portable)
        self.assertEqual(relocated.epoch_id, epoch.epoch_id)
        self.assertTrue(relocated.universe_policy_path.is_relative_to(portable))
        self.assertTrue(relocated.code_validity_path.is_relative_to(portable))

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

    def test_market_release_selection_does_not_trust_shared_current_attempt(self) -> None:
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

        selected = select_market_daily_ods_release(
            date_root,
            market_date=market_date,
            preferred_attempt=event_attempt.name,
        )

        self.assertEqual(selected, market_attempt.resolve())

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
        )
        withheld = arbitrate_and_publish(
            market_date=second_date,
            primary_candidate_path=second.root,
            standby_candidate_path=None,
            root=publication_root,
            policy=self.policy,
            expected_epoch=second_epoch,
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
        )
        second = arbitrate_and_publish(
            market_date=market_date,
            primary_candidate_path=primary.root,
            standby_candidate_path=standby.root,
            root=publication_root,
            policy=self.policy,
            expected_epoch=epoch,
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

    def test_transfer_helper_is_compatible_with_rrsync_forced_commands(self) -> None:
        project_root = Path(__file__).resolve().parents[2]
        helper = (project_root / "ops/bin/sync-dual-lane-date-root").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("--protect-args", helper)
        self.assertNotIn("--delete", helper)
        self.assertIn("--ignore-existing", helper)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ModuleNotFoundError:  # pragma: no cover - exercised by the P1 dependency job
    pa = None
    pq = None

from quanthus.pipelines.dual_lane import EpochInput, LaneCandidate
from quanthus.pipelines.dual_lane_history import (
    DualLaneHistoryError,
    build_history_base_bundle,
    build_lane_history_catalog,
    load_history_base_current,
    load_history_base_policy,
    load_lane_history_current,
    promote_history_base_bundle,
    verify_history_base_bundle,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"sha256:{digest}"


@unittest.skipIf(pa is None or pq is None, "P1 dependencies are not installed")
class DualLaneHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "history-dwd-v1"
        self.source.mkdir()
        self._write_source()
        self.policy_path = self.root / "history-policy.json"
        self._write_policy()
        self.policy = load_history_base_policy(self.policy_path)

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _write_source(self) -> None:
        assert pa is not None
        assert pq is not None
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {
                        "security_id": "000001",
                        "date": date(2024, 1, 2),
                        "pct_change": 1.0,
                        "volume": 10.0,
                        "amount": 100.0,
                    },
                    {
                        "security_id": "000001",
                        "date": date(2024, 1, 3),
                        "pct_change": -1.0,
                        "volume": 12.0,
                        "amount": 120.0,
                    },
                    {
                        "security_id": "000002",
                        "date": date(2024, 1, 3),
                        "pct_change": 0.0,
                        "volume": 8.0,
                        "amount": 80.0,
                    },
                ]
            ),
            self.source / "daily_ohlcv_none.parquet",
        )
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {
                        "security_id": security_id,
                        "date": market_date,
                        "isST": is_st,
                        "tradestatus": trade_status,
                    }
                    for market_date, values in (
                        (date(2024, 1, 2), (("000001", "0", "1"), ("000002", "0", "0"))),
                        (date(2024, 1, 3), (("000001", "0", "1"), ("000002", "1", "1"))),
                    )
                    for security_id, is_st, trade_status in values
                ]
            ),
            self.source / "daily_isST.parquet",
        )
        for name in (
            "daily_universe.parquet",
            "ref_security_code_validity.parquet",
            "st_status_pit.parquet",
        ):
            pq.write_table(pa.table({"fixture": [name]}), self.source / name)
        payloads = sorted(path.name for path in self.source.iterdir() if path.suffix == ".parquet")
        checksums = {name: _sha256(self.source / name) for name in payloads}
        (self.source / "CHECKSUMS.sha256").write_text(
            "".join(
                f"{digest.removeprefix('sha256:')}  {name}\n" for name, digest in checksums.items()
            ),
            encoding="ascii",
        )
        self._write_json(
            self.source / "META.json",
            {
                "revision": self.source.name,
                "kind": "test_history_base",
                "checksums": checksums,
            },
        )

    def _write_policy(self) -> None:
        payloads = sorted(path.name for path in self.source.iterdir() if path.suffix == ".parquet")
        self._write_json(
            self.policy_path,
            {
                "schema_version": 1,
                "contract_id": "dual-lane-history-test-v1",
                "source": {
                    "revision": self.source.name,
                    "meta_sha256": _sha256(self.source / "META.json"),
                    "checksums_sha256": _sha256(self.source / "CHECKSUMS.sha256"),
                    "required_payloads": payloads,
                    "dwd_date_start": "2024-01-02",
                    "dwd_date_end": "2024-01-03",
                },
                "dws": {
                    "contract": "historical-market-summary-v1",
                    "status_scope": "observed_daily_status_keys",
                    "bar_scope": "intersection_with_observed_daily_status_keys",
                    "date_start": "2024-01-02",
                    "date_end": "2024-01-03",
                    "metric_columns": [
                        "security_count",
                        "tradable_security_count",
                        "suspended_security_count",
                        "st_security_count",
                        "bar_security_count",
                        "advanced_security_count",
                        "declined_security_count",
                        "flat_security_count",
                        "missing_pct_change_security_count",
                        "total_volume",
                        "total_amount",
                    ],
                },
                "merge": {
                    "lanes": ["cn01", "hermes"],
                    "daily_candidate_kind": "dual_lane_dwd_dws_candidate",
                    "parent_chain": "exact_manifest_v1",
                    "physical_merge": False,
                },
                "retention": {
                    "migration_mode": "copy_only",
                    "delete_enabled": False,
                    "minimum_sealed_node_copies": 2,
                },
            },
        )

    def _build_bundle(self) -> Path:
        output = self.root / "build" / "history-bundle-v1"
        result = build_history_base_bundle(
            source=self.source,
            output=output,
            bundle_id=output.name,
            policy=self.policy,
            code_revision="a" * 40,
        )
        self.assertEqual(result.dws_row_count, 2)
        self.assertEqual(result.dwd_date_end, date(2024, 1, 3))
        return output

    def _candidate(
        self,
        *,
        storage_root: Path,
        base_meta: str,
        lane_id: str = "cn01",
        market_date: date = date(2024, 1, 4),
        parent: dict[str, str] | None = None,
    ) -> LaneCandidate:
        suffix = market_date.strftime("%Y%m%d")
        candidate_root = (
            storage_root / "candidates" / market_date.isoformat() / f"candidate-{lane_id}"
        )
        dwd_root = candidate_root / "artifacts" / f"daily-dwd-{lane_id}-{suffix}"
        dws_root = candidate_root / "artifacts" / f"daily-dws-{lane_id}-{suffix}"
        dwd_root.mkdir(parents=True)
        dws_root.mkdir(parents=True)
        self._write_json(
            dwd_root / "manifest.json",
            {
                "revision": dwd_root.name,
                "lineage": {
                    "parent": parent
                    or {
                        "kind": "historical_base",
                        "revision": self.source.name,
                        "manifest_sha256": base_meta,
                    }
                },
            },
        )
        self._write_json(dws_root / "manifest.json", {"revision": dws_root.name})
        epoch_id = f"epoch-{lane_id}-{suffix}"
        self._write_json(
            candidate_root / "manifest.json",
            {"epoch_id": epoch_id, "fixture": "candidate"},
        )
        self._write_json(candidate_root.parent / "CURRENT.json", {"fixture": "current"})
        return LaneCandidate(
            root=candidate_root,
            candidate_id=f"candidate-{lane_id}",
            attempt_id="attempt-1",
            lane_id=lane_id,
            market_date=market_date,
            manifest_sha256=_sha256(candidate_root / "manifest.json"),
            candidate_content_digest=f"sha256:{'1' * 64}",
            business_content_digest=f"sha256:{'2' * 64}",
            dwd_root=dwd_root,
            dws_root=dws_root,
            epoch_manifest_sha256=f"sha256:{'3' * 64}",
            ods_manifest_sha256=f"sha256:{'4' * 64}",
            contract_id="dual-lane-test",
            policy_sha256=f"sha256:{'5' * 64}",
            code_revision="a" * 40,
            runtime={},
            security_master_sha256=None,
            eligibility=None,
            source_inputs=(),
            candidate_ready_at=None,
        )

    def _epoch(
        self,
        *,
        candidate: LaneCandidate,
        source_kind: str,
        source_revision: str,
        source_manifest_sha256: str,
        parent_date: date | None = None,
    ) -> EpochInput:
        manifest = json.loads((candidate.root / "manifest.json").read_text(encoding="utf-8"))
        source_path = None
        if source_kind == "daily_partition":
            assert parent_date is not None
            source_path = self.root / "epoch-parents" / source_revision
            self._write_json(
                source_path / "manifest.json",
                {"market_date": parent_date.isoformat(), "revision": source_revision},
            )
        return EpochInput(
            root=self.root / "epochs" / str(manifest["epoch_id"]),
            epoch_id=str(manifest["epoch_id"]),
            market_date=candidate.market_date,
            manifest_sha256=candidate.epoch_manifest_sha256,
            contract_id=candidate.contract_id,
            policy_sha256=candidate.policy_sha256,
            code_revision=candidate.code_revision,
            source_kind=source_kind,
            source_revision=source_revision,
            source_manifest_sha256=source_manifest_sha256,
            source_path=source_path,
            universe_policy_path=self.policy_path,
            code_validity_path=self.policy_path,
            security_master_path=None,
            eligibility=None,
        )

    def test_build_verify_promote_and_catalog(self) -> None:
        bundle_path = self._build_bundle()
        bundle = verify_history_base_bundle(bundle_path, policy=self.policy)
        storage_root = self.root / "node"
        base_root = storage_root / "history-bases"
        current = base_root / "CURRENT.json"
        promoted = promote_history_base_bundle(
            ingress_bundle=bundle_path,
            base_root=base_root,
            current_pointer=current,
            policy=self.policy,
        )
        self.assertEqual(promoted.content_digest, bundle.content_digest)
        self.assertEqual(
            load_history_base_current(current, policy=self.policy).manifest_sha256,
            bundle.manifest_sha256,
        )
        candidate = self._candidate(
            storage_root=storage_root,
            base_meta=bundle.source_meta_sha256,
        )
        epoch = self._epoch(
            candidate=candidate,
            source_kind="historical_base",
            source_revision=bundle.source_revision,
            source_manifest_sha256=bundle.source_meta_sha256,
        )
        catalog_root = storage_root / "lane-history"
        with (
            mock.patch(
                "quanthus.pipelines.dual_lane_history.load_candidate_current",
                return_value=candidate,
            ),
            mock.patch(
                "quanthus.pipelines.dual_lane_history.load_candidate_current_from_catalog",
                return_value=candidate,
            ),
            mock.patch(
                "quanthus.pipelines.dual_lane_history.verify_epoch_input",
                return_value=epoch,
            ),
        ):
            catalog = build_lane_history_catalog(
                storage_root=storage_root,
                base_current=current,
                candidate_root=storage_root / "candidates",
                catalog_root=catalog_root,
                lane_id="cn01",
                policy=self.policy,
            )
            loaded = load_lane_history_current(
                catalog_root / "cn01" / "CURRENT.json",
                storage_root=storage_root,
                policy=self.policy,
            )
            manifest_bytes = (catalog.root / "manifest.json").read_bytes()
            current_bytes = (catalog_root / "cn01" / "CURRENT.json").read_bytes()
            repeated = build_lane_history_catalog(
                storage_root=storage_root,
                base_current=current,
                candidate_root=storage_root / "candidates",
                catalog_root=catalog_root,
                lane_id="cn01",
                policy=self.policy,
            )
        self.assertEqual(catalog.catalog_id, loaded.catalog_id)
        self.assertEqual(catalog.through_date, date(2024, 1, 4))
        self.assertEqual(len(catalog.candidates), 1)
        self.assertEqual(repeated.root, catalog.root)
        self.assertEqual((catalog.root / "manifest.json").read_bytes(), manifest_bytes)
        self.assertEqual((catalog_root / "cn01" / "CURRENT.json").read_bytes(), current_bytes)

    def test_bundle_tamper_is_rejected(self) -> None:
        bundle_path = self._build_bundle()
        summary = bundle_path / "dws" / "daily_market_summary.parquet"
        summary.chmod(0o640)
        summary.write_bytes(summary.read_bytes() + b"tamper")
        with self.assertRaisesRegex(DualLaneHistoryError, "integrity failed"):
            verify_history_base_bundle(bundle_path, policy=self.policy)

    def test_candidate_must_bind_exact_history_base(self) -> None:
        bundle_path = self._build_bundle()
        storage_root = self.root / "node"
        base_root = storage_root / "history-bases"
        current = base_root / "CURRENT.json"
        promote_history_base_bundle(
            ingress_bundle=bundle_path,
            base_root=base_root,
            current_pointer=current,
            policy=self.policy,
        )
        candidate = self._candidate(
            storage_root=storage_root,
            base_meta=f"sha256:{'f' * 64}",
        )
        epoch = self._epoch(
            candidate=candidate,
            source_kind="historical_base",
            source_revision=self.source.name,
            source_manifest_sha256=_sha256(self.source / "META.json"),
        )
        with (
            mock.patch(
                "quanthus.pipelines.dual_lane_history.load_candidate_current",
                return_value=candidate,
            ),
            mock.patch(
                "quanthus.pipelines.dual_lane_history.verify_epoch_input",
                return_value=epoch,
            ),
            self.assertRaisesRegex(DualLaneHistoryError, "epoch canonical parent"),
        ):
            build_lane_history_catalog(
                storage_root=storage_root,
                base_current=current,
                candidate_root=storage_root / "candidates",
                catalog_root=storage_root / "lane-history",
                lane_id="cn01",
                policy=self.policy,
            )

    def test_standby_chain_binds_each_sealed_canonical_parent(self) -> None:
        bundle_path = self._build_bundle()
        bundle = verify_history_base_bundle(bundle_path, policy=self.policy)
        storage_root = self.root / "node"
        current = storage_root / "history-bases" / "CURRENT.json"
        promote_history_base_bundle(
            ingress_bundle=bundle_path,
            base_root=current.parent,
            current_pointer=current,
            policy=self.policy,
        )
        first = self._candidate(
            storage_root=storage_root,
            base_meta=bundle.source_meta_sha256,
            lane_id="hermes",
        )
        canonical_revision = "daily-dwd-cn01-20240104"
        canonical_manifest = f"sha256:{'9' * 64}"
        second = self._candidate(
            storage_root=storage_root,
            base_meta=bundle.source_meta_sha256,
            lane_id="hermes",
            market_date=date(2024, 1, 5),
            parent={
                "kind": "daily_partition",
                "revision": canonical_revision,
                "manifest_sha256": canonical_manifest,
            },
        )
        epochs = {
            "epoch-hermes-20240104": self._epoch(
                candidate=first,
                source_kind="historical_base",
                source_revision=bundle.source_revision,
                source_manifest_sha256=bundle.source_meta_sha256,
            ),
            "epoch-hermes-20240105": self._epoch(
                candidate=second,
                source_kind="daily_partition",
                source_revision=canonical_revision,
                source_manifest_sha256=canonical_manifest,
                parent_date=first.market_date,
            ),
        }

        def candidate_from_catalog(*, storage_root: Path, path: str, manifest_sha256: str):
            del storage_root, manifest_sha256
            return second if path.endswith("candidate-hermes") and "2024-01-05" in path else first

        with (
            mock.patch(
                "quanthus.pipelines.dual_lane_history.load_candidate_current",
                side_effect=(first, second),
            ),
            mock.patch(
                "quanthus.pipelines.dual_lane_history.load_candidate_current_from_catalog",
                side_effect=candidate_from_catalog,
            ),
            mock.patch(
                "quanthus.pipelines.dual_lane_history.verify_epoch_input",
                side_effect=lambda root: epochs[root.name],
            ),
        ):
            catalog = build_lane_history_catalog(
                storage_root=storage_root,
                base_current=current,
                candidate_root=storage_root / "candidates",
                catalog_root=storage_root / "lane-history",
                lane_id="hermes",
                policy=self.policy,
            )
        self.assertEqual(len(catalog.candidates), 2)
        manifest = json.loads((catalog.root / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(
            manifest["daily_partitions"][1]["canonical_parent"]["revision"],
            canonical_revision,
        )


if __name__ == "__main__":
    unittest.main()

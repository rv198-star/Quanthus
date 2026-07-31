from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from quanthus.pipelines.dual_lane import EpochInput, LaneCandidate
from quanthus.pipelines.dual_lane_history import HistoryBaseBundle
from quanthus.pipelines.dwd_dws_base import (
    DwdDwsBaseError,
    DwdDwsBasePair,
    LayerBasePackage,
    advance_dwd_dws_base_current,
    build_dwd_dws_base_lane_catalog,
    freeze_base_input_ledger,
    load_base_input_ledger,
    load_dwd_dws_base_current,
    load_dwd_dws_base_lane_catalog_current,
    load_dwd_dws_base_policy,
)


def _sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


class DwdDwsBaseTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.publication = self.root / "publication"
        self.catalog_id = "catalog-test-v1"
        self.generation_id = "generation-test-v1"
        self.market_date = date(2024, 1, 3)
        self.catalog_path = (
            self.publication / "canonical" / "catalogs" / self.catalog_id / "catalog.json"
        )
        self._write_json(
            self.catalog_path,
            {
                "schema_version": 1,
                "kind": "canonical_catalog_snapshot",
                "catalog_id": self.catalog_id,
                "market_date_generations": {
                    self.market_date.isoformat(): self.generation_id,
                },
            },
        )
        self.policy_path = self.root / "base-policy.json"
        self._write_json(self.policy_path, self._policy_payload())
        self.policy = load_dwd_dws_base_policy(self.policy_path)

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _policy_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "contract_id": "dwd-dws-monthly-base-test-v1",
            "phase": "provisional",
            "through_date": self.market_date.isoformat(),
            "open_sessions": [self.market_date.isoformat()],
            "packages": {
                "dwd": "dwd-base-test-v1",
                "dws": "dws-base-test-v1",
                "layout": "layer-manifest-members-v1",
                "ready_written_last": True,
            },
            "historical_seed": {
                "bundle_id": "history-base-test-v1",
                "manifest_sha256": f"sha256:{'1' * 64}",
                "content_digest": f"sha256:{'2' * 64}",
            },
            "canonical_catalog": {
                "catalog_id": self.catalog_id,
                "sha256": _sha256(self.catalog_path),
            },
            "calendar": {
                "calendar_id": "calendar-test-v1",
                "sha256": f"sha256:{'3' * 64}",
            },
            "projection": {
                "contract": "dws-from-exact-dwd-members-v1",
                "source_equivalence": "exact_rows",
            },
            "upstream_ods_base": {
                "binding_state": "pending_issue_47",
                "issue": 47,
                "required_for_final": True,
            },
            "authority": {
                "historical_seed": "legacy_accepted_disclosed",
                "daily_generations": "canonical_acceptance_only",
                "row_or_lane_mixing": False,
            },
            "retention": {
                "migration_mode": "copy_only",
                "delete_enabled": False,
                "absorbed_sources_retained": True,
            },
        }

    def _freeze_fixture(self) -> tuple[HistoryBaseBundle, LaneCandidate, EpochInput]:
        history_root = self.root / "history-base-test-v1"
        history_dwd = history_root / "dwd" / "seed-v1"
        history_dws = history_root / "dws"
        history_dwd.mkdir(parents=True)
        history_dws.mkdir()
        self._write_json(
            history_root / "manifest.json",
            {"source": {"checksums_sha256": f"sha256:{'4' * 64}"}},
        )
        self._write_json(
            history_dwd / "META.json",
            {"revision": "seed-v1", "built_at": "2024-01-02T12:00:00+00:00"},
        )
        history = HistoryBaseBundle(
            root=history_root,
            bundle_id=self.policy.history_bundle_id,
            manifest_sha256=self.policy.history_manifest_sha256,
            content_digest=self.policy.history_content_digest,
            source_revision="seed-v1",
            source_meta_sha256=f"sha256:{'5' * 64}",
            dwd_root=history_dwd,
            dws_root=history_dws,
            dwd_date_start=date(2024, 1, 1),
            dwd_date_end=date(2024, 1, 2),
            dws_date_start=date(2024, 1, 1),
            dws_date_end=date(2024, 1, 2),
            dws_row_count=2,
        )
        generation_root = (
            self.publication
            / "canonical"
            / "generations"
            / self.market_date.isoformat()
            / self.generation_id
        )
        dwd_root = generation_root / "artifacts" / "daily-dwd-test-v1"
        dws_root = generation_root / "artifacts" / "daily-dws-test-v1"
        self._write_json(
            dwd_root / "manifest.json",
            {
                "lineage": {
                    "parent": {
                        "kind": "historical_base",
                        "revision": history.source_revision,
                        "manifest_sha256": history.source_meta_sha256,
                    }
                }
            },
        )
        self._write_json(dws_root / "manifest.json", {"fixture": "dws"})
        epoch_id = "epoch-test-v1"
        candidate_manifest = {
            "epoch_id": epoch_id,
            "knowledge_cutoff": "2024-01-03T12:00:00+00:00",
            "ods_release_id": "ods-test-v1",
            "hard_gates": {"dwd_integrity": "passed", "dws_integrity": "passed"},
        }
        self._write_json(generation_root / "manifest.json", candidate_manifest)
        candidate = LaneCandidate(
            root=generation_root,
            candidate_id="candidate-test-v1",
            attempt_id="attempt-test-v1",
            lane_id="cn01",
            market_date=self.market_date,
            manifest_sha256=f"sha256:{'6' * 64}",
            candidate_content_digest=f"sha256:{'7' * 64}",
            business_content_digest=f"sha256:{'8' * 64}",
            dwd_root=dwd_root,
            dws_root=dws_root,
            epoch_manifest_sha256=f"sha256:{'9' * 64}",
            ods_manifest_sha256=f"sha256:{'a' * 64}",
            contract_id="dual-lane-test-v1",
            policy_sha256=f"sha256:{'b' * 64}",
            code_revision="c" * 40,
            runtime={"python_series": "3.10", "packages": {}},
            security_master_sha256=f"sha256:{'d' * 64}",
            eligibility=None,
            source_inputs=(),
            candidate_ready_at=None,
        )
        acceptance = {
            "schema_version": 1,
            "kind": "canonical_acceptance_record",
            "generation_id": self.generation_id,
            "market_date": self.market_date.isoformat(),
            "accepted_at": "2024-01-03T12:05:00+00:00",
            "reason": "primary",
            "selected_lane": candidate.lane_id,
            "candidate_manifest_sha256": candidate.manifest_sha256,
            "candidate_content_digest": candidate.candidate_content_digest,
            "business_content_digest": candidate.business_content_digest,
            "epoch_manifest_sha256": candidate.epoch_manifest_sha256,
            "ods_manifest_sha256": candidate.ods_manifest_sha256,
        }
        self._write_json(
            self.publication
            / "canonical"
            / "acceptance"
            / self.market_date.isoformat()
            / f"{self.generation_id}.json",
            acceptance,
        )
        epoch_root = self.root / "dual-lane" / "epochs" / self.market_date.isoformat() / epoch_id
        self._write_json(
            epoch_root / "manifest.json",
            {
                "shared_references": {
                    "universe_policy_sha256": f"sha256:{'e' * 64}",
                    "code_validity_sha256": f"sha256:{'f' * 64}",
                    "security_master_sha256": f"sha256:{'d' * 64}",
                }
            },
        )
        epoch = EpochInput(
            root=epoch_root,
            epoch_id=epoch_id,
            market_date=self.market_date,
            manifest_sha256=candidate.epoch_manifest_sha256,
            contract_id=candidate.contract_id,
            policy_sha256=candidate.policy_sha256,
            code_revision=candidate.code_revision,
            source_kind="historical_base",
            source_revision=history.source_revision,
            source_manifest_sha256=history.source_meta_sha256,
            source_path=None,
            universe_policy_path=self.root / "universe.json",
            code_validity_path=self.root / "validity.json",
            security_master_path=None,
            eligibility=None,
        )
        return history, candidate, epoch

    def test_policy_rejects_lane_mixing(self) -> None:
        value = self._policy_payload()
        authority = value["authority"]
        assert isinstance(authority, dict)
        authority["row_or_lane_mixing"] = True
        path = self.root / "unsafe-policy.json"
        self._write_json(path, value)
        with self.assertRaisesRegex(DwdDwsBaseError, "policy invariants"):
            load_dwd_dws_base_policy(path)

    def test_freeze_binds_canonical_acceptance_and_rejects_tampering(self) -> None:
        history, candidate, epoch = self._freeze_fixture()
        ledger_path = self.root / "input-ledger.json"
        with (
            mock.patch(
                "quanthus.pipelines.dwd_dws_base.verify_history_base_bundle",
                return_value=history,
            ),
            mock.patch(
                "quanthus.pipelines.dwd_dws_base.verify_lane_candidate",
                return_value=candidate,
            ),
            mock.patch(
                "quanthus.pipelines.dwd_dws_base.verify_epoch_input",
                return_value=epoch,
            ),
        ):
            ledger = freeze_base_input_ledger(
                history_bundle=history.root,
                history_policy=mock.Mock(),
                dual_lane_root=self.root / "dual-lane",
                publication_root=self.publication,
                output=ledger_path,
                policy=self.policy,
            )
        generations = ledger.value["generations"]
        self.assertIsInstance(generations, list)
        self.assertEqual(generations[0]["authority_state"], "canonical_accepted")
        self.assertEqual(
            generations[0]["acceptance"]["generation_id"],
            self.generation_id,
        )
        value = json.loads(ledger_path.read_text(encoding="utf-8"))
        value["generations"][0]["acceptance"]["reason"] = "tampered"
        self._write_json(ledger_path, value)
        with self.assertRaisesRegex(DwdDwsBaseError, "content digest mismatch"):
            load_base_input_ledger(ledger_path, policy=self.policy)

    def _pair(self, dwd_root: Path, dws_root: Path) -> DwdDwsBasePair:
        digest = f"sha256:{'1' * 64}"
        dwd = LayerBasePackage(
            root=dwd_root,
            package_id=self.policy.dwd_package_id,
            layer="dwd",
            manifest_sha256=f"sha256:{'2' * 64}",
            content_digest=f"sha256:{'3' * 64}",
            through_date=self.policy.through_date,
            input_ledger_digest=digest,
            value={},
        )
        dws = LayerBasePackage(
            root=dws_root,
            package_id=self.policy.dws_package_id,
            layer="dws",
            manifest_sha256=f"sha256:{'4' * 64}",
            content_digest=f"sha256:{'5' * 64}",
            through_date=self.policy.through_date,
            input_ledger_digest=digest,
            value={},
        )
        return DwdDwsBasePair(dwd=dwd, dws=dws)

    def test_base_current_is_cas_guarded_and_pair_bound(self) -> None:
        dwd_base = self.root / "node" / "dwd" / "base"
        dws_base = self.root / "node" / "dws" / "base"
        dwd_root = dwd_base / self.policy.dwd_package_id
        dws_root = dws_base / self.policy.dws_package_id
        dwd_root.mkdir(parents=True)
        dws_root.mkdir(parents=True)
        pair = self._pair(dwd_root, dws_root)
        with mock.patch(
            "quanthus.pipelines.dwd_dws_base.verify_dwd_dws_base_pair",
            return_value=pair,
        ):
            with self.assertRaisesRegex(DwdDwsBaseError, "SHA precondition failed"):
                advance_dwd_dws_base_current(
                    dwd_base_root=dwd_base,
                    dws_base_root=dws_base,
                    policy=self.policy,
                    expected_dwd_current_sha256=f"sha256:{'a' * 64}",
                    expected_dws_current_sha256=None,
                )
            self.assertFalse((dwd_base / "BASE_CURRENT.json").exists())
            self.assertFalse((dws_base / "BASE_CURRENT.json").exists())
            advance_dwd_dws_base_current(
                dwd_base_root=dwd_base,
                dws_base_root=dws_base,
                policy=self.policy,
                expected_dwd_current_sha256=None,
                expected_dws_current_sha256=None,
            )
            loaded = load_dwd_dws_base_current(
                dwd_pointer=dwd_base / "BASE_CURRENT.json",
                dws_pointer=dws_base / "BASE_CURRENT.json",
                policy=self.policy,
            )
            self.assertEqual(loaded.dwd.manifest_sha256, pair.dwd.manifest_sha256)
            value = json.loads((dwd_base / "BASE_CURRENT.json").read_text(encoding="utf-8"))
            value["pair_digest"] = f"sha256:{'f' * 64}"
            self._write_json(dwd_base / "BASE_CURRENT.json", value)
            with self.assertRaisesRegex(DwdDwsBaseError, "pair binding"):
                load_dwd_dws_base_current(
                    dwd_pointer=dwd_base / "BASE_CURRENT.json",
                    dws_pointer=dws_base / "BASE_CURRENT.json",
                    policy=self.policy,
                )

    def test_base_lane_catalog_allows_no_post_base_candidate_and_is_idempotent(self) -> None:
        platform_root = self.root / "platform"
        dwd_root = platform_root / "dwd" / "base" / self.policy.dwd_package_id
        dws_root = platform_root / "dws" / "base" / self.policy.dws_package_id
        dwd_root.mkdir(parents=True)
        dws_root.mkdir(parents=True)
        candidate_root = platform_root / "dual-lane" / "candidates"
        candidate_root.mkdir(parents=True)
        catalog_root = platform_root / "dual-lane" / "lane-history"
        pair = self._pair(dwd_root, dws_root)
        dwd_current = dwd_root.parent / "BASE_CURRENT.json"
        dws_current = dws_root.parent / "BASE_CURRENT.json"
        with (
            mock.patch(
                "quanthus.pipelines.dwd_dws_base.load_dwd_dws_base_current",
                return_value=pair,
            ),
            mock.patch(
                "quanthus.pipelines.dwd_dws_base.verify_dwd_dws_base_pair",
                return_value=pair,
            ),
        ):
            catalog = build_dwd_dws_base_lane_catalog(
                platform_root=platform_root,
                dwd_pointer=dwd_current,
                dws_pointer=dws_current,
                candidate_root=candidate_root,
                catalog_root=catalog_root,
                lane_id="cn01",
                policy=self.policy,
            )
            manifest_bytes = (catalog.root / "manifest.json").read_bytes()
            current = catalog_root / "cn01" / "CURRENT.json"
            current_bytes = current.read_bytes()
            repeated = build_dwd_dws_base_lane_catalog(
                platform_root=platform_root,
                dwd_pointer=dwd_current,
                dws_pointer=dws_current,
                candidate_root=candidate_root,
                catalog_root=catalog_root,
                lane_id="cn01",
                policy=self.policy,
            )
            loaded = load_dwd_dws_base_lane_catalog_current(
                current,
                platform_root=platform_root,
                policy=self.policy,
                dwd_pointer=dwd_current,
                dws_pointer=dws_current,
            )
        self.assertEqual(catalog.through_date, self.policy.through_date)
        self.assertEqual(catalog.candidates, ())
        self.assertEqual(loaded.catalog_id, catalog.catalog_id)
        self.assertEqual(repeated.root, catalog.root)
        self.assertEqual((catalog.root / "manifest.json").read_bytes(), manifest_bytes)
        self.assertEqual(current.read_bytes(), current_bytes)
        current_value = json.loads(current.read_text(encoding="utf-8"))
        self.assertEqual(current_value["kind"], "dwd_dws_base_lane_history_current")


if __name__ == "__main__":
    unittest.main()

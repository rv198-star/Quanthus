from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from quanthus.pipelines.ods_base import (
    _canonical_digest,
    _parquet_metadata,
    _sha256,
    load_ods_base_policy,
)
from quanthus.pipelines.ods_base_rollup import (
    OVERLAY_BUSINESS_KEYS,
    OdsBaseRollupError,
    advance_ods_base_rollup_current,
    build_ods_base_rollup,
    verify_ods_base_rollup,
)
from quanthus.pipelines.ods_calendar_history_hotfix import (
    build_ods_calendar_history_hotfix,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
AUTHORITY = {
    "execution_authorized": False,
    "historical_source": "hermes",
    "oci_ods_enabled": False,
    "reconstruction_seed": True,
}


class OdsBaseRollupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.base_root = self.root / "base"
        self.parent = self.base_root / "ods-base-parent-v1"
        self.hotfix = self.root / "base-hotfixes" / "ods-base-parent-v1-hotfix-001"
        self.output = self.base_root / "ods-base-parent-v1.1"
        self.calendar_hotfix = (
            self.root / "base-hotfixes" / "ods-base-parent-v1.1-calendar-history-001"
        )
        self.chained_output = self.base_root / "ods-base-parent-v1.2"
        self.policy = load_ods_base_policy(REPOSITORY_ROOT / "config/pipelines/ods-base.v1.json")
        self.parent_manifest_sha, self.parent_content_digest = self._write_parent()
        self.hotfix_manifest_sha, self.hotfix_content_digest = self._write_hotfix()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _file_entry(root: Path, path: str, dataset_id: str, role: str) -> dict[str, object]:
        target = root / path
        entry: dict[str, object] = {
            "dataset_id": dataset_id,
            "role": role,
            "format": "parquet" if target.suffix == ".parquet" else "json",
            "path": path,
            "sha256": _sha256(target),
            "size_bytes": target.stat().st_size,
        }
        if target.suffix == ".parquet":
            entry.update(_parquet_metadata(target, ()))
        return entry

    def _seal(self, root: Path, manifest: dict[str, object]) -> tuple[str, str]:
        self._write_json(root / "manifest.json", manifest)
        manifest_sha = _sha256(root / "manifest.json")
        (root / "READY").write_text(f"{manifest_sha}\n", encoding="ascii")
        return manifest_sha, str(manifest["content_digest"])

    def _write_parent(self) -> tuple[str, str]:
        import pyarrow as pa
        import pyarrow.parquet as pq

        path = "payload/canonical/parent.json"
        self._write_json(self.parent / path, {"source": "parent"})
        entry = self._file_entry(self.parent, path, "parent_dataset", "source_evidence")
        calendar_path = "payload/canonical/ref_trading_calendar.parquet"
        calendar_target = self.parent / calendar_path
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {
                        "exchange": exchange,
                        "trade_date": "2026-07-31",
                        "is_open": 1,
                        "session": "day",
                        "source": "fixture",
                        "built_at": "2026-08-01T00:00:00+00:00",
                    }
                    for exchange in ("SSE", "SZSE", "BSE")
                ]
            ),
            calendar_target,
        )
        calendar_entry = self._file_entry(
            self.parent,
            calendar_path,
            "trading_calendar",
            "canonical_reference",
        )
        content: dict[str, object] = {
            "contract_id": self.policy.contract_id,
            "policy_sha256": self.policy.digest,
            "source_node": "hermes",
            "mirror_nodes": ["hermes", "cn01"],
            "through_date": "2026-07-31",
            "increment_start": "2026-07-27",
            "authority": AUTHORITY,
            "parent_base": None,
            "source_inventory": [],
            "scope_gaps": [],
            "daily_releases": [],
            "datasets": [
                {
                    "dataset_id": "parent_dataset",
                    "role": "source_evidence",
                    "part_count": 1,
                    "size_bytes": entry["size_bytes"],
                    "parts": [entry],
                },
                {
                    "dataset_id": "trading_calendar",
                    "role": "canonical_reference",
                    "part_count": 1,
                    "size_bytes": calendar_entry["size_bytes"],
                    "parts": [calendar_entry],
                },
            ],
            "files": sorted([entry, calendar_entry], key=lambda item: str(item["path"])),
            "source_inventory_selection_digest": "fixture-parent-selection",
        }
        manifest: dict[str, object] = {
            "schema_version": 1,
            "kind": "quanthus_ods_base_bundle",
            "bundle_id": self.parent.name,
            "built_at": "2026-08-01T00:00:00+00:00",
            "code_revision": "a" * 40,
            "content": content,
            "content_digest": _canonical_digest(content),
            "storage_reuse": {"file_count": 0, "size_bytes": 0},
        }
        return self._seal(self.parent, manifest)

    def _write_hotfix(self, *, overlap_mismatches: int = 0) -> tuple[str, str]:
        import pyarrow as pa
        import pyarrow.parquet as pq

        files: list[dict[str, object]] = []
        for index, dataset_id in enumerate(sorted(OVERLAY_BUSINESS_KEYS)):
            suffix = ".parquet" if index == 0 else ".json"
            relative = f"payload/hotfix/{dataset_id}{suffix}"
            target = self.hotfix / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if suffix == ".parquet":
                pq.write_table(
                    pa.Table.from_pylist([{"stock_code": "000001", "date": "2026-07-31"}]),
                    target,
                )
            else:
                self._write_json(target, {"dataset_id": dataset_id})
            entry = self._file_entry(self.hotfix, relative, dataset_id, "source_evidence")
            if suffix == ".parquet":
                entry = {
                    key: value
                    for key, value in entry.items()
                    if key not in {"schema", "schema_sha256", "null_counts", "date_bounds"}
                }
            files.append(entry)
        files.sort(key=lambda item: str(item["path"]))
        parent_identity = {
            "bundle_id": self.parent.name,
            "manifest_sha256": self.parent_manifest_sha,
            "content_digest": self.parent_content_digest,
            "through_date": "2026-07-31",
        }
        scope = {
            "reason": "fixture",
            "security_count": 1,
            "scope_digest": _canonical_digest(["000001"]),
            "through_date": "2026-07-31",
        }
        coverage = {"daily": {"overlap_mismatches": overlap_mismatches}}
        content_identity = {
            "parent": parent_identity,
            "scope_digest": scope["scope_digest"],
            "through_date": scope["through_date"],
            "files": files,
        }
        manifest: dict[str, object] = {
            "schema_version": 1,
            "kind": "ods_base_hotfix",
            "hotfix_id": self.hotfix.name,
            "built_at": "2026-08-01T01:00:00+00:00",
            "code_revision": "fixture",
            "parent": parent_identity,
            "scope": scope,
            "application": {
                "mode": "fill_absent_keys_only",
                "parent_mutation": False,
                "current_switch": False,
            },
            "content": {"files": files, "coverage": coverage},
            "content_digest": _canonical_digest(content_identity),
        }
        return self._seal(self.hotfix, manifest)

    def _build(self):
        return build_ods_base_rollup(
            parent_base=self.parent,
            hotfix_bundle=self.hotfix,
            output=self.output,
            bundle_id=self.output.name,
            policy=self.policy,
            code_revision="b" * 40,
            built_at="2026-08-01T02:00:00Z",
            expected_parent_manifest_sha256=self.parent_manifest_sha,
            expected_parent_content_digest=self.parent_content_digest,
            expected_hotfix_manifest_sha256=self.hotfix_manifest_sha,
            expected_hotfix_content_digest=self.hotfix_content_digest,
        )

    def _write_calendar_hotfix(
        self, *, parent_manifest_sha: str, parent_content_digest: str
    ) -> tuple[str, str]:
        del parent_manifest_sha, parent_content_digest
        rows: list[dict[str, str]] = [
            {
                "calendar_date": trade_date,
                "is_trading_day": "1",
            }
            for trade_date in ("1990-12-19", "1990-12-20")
        ]
        result = build_ods_calendar_history_hotfix(
            parent_base=self.output,
            output=self.calendar_hotfix,
            hotfix_id=self.calendar_hotfix.name,
            policy=self.policy,
            code_revision="c" * 40,
            built_at="2026-08-01T03:00:00+00:00",
            provider_rows=rows,
            provider_receipt={"provider": "baostock", "raw_row_count": 2},
            expected_minimum_date=date(1990, 12, 19),
            expected_maximum_date=date(1990, 12, 20),
            expected_date_count=2,
            expected_open_date_count=2,
        )
        return result.manifest_sha256, result.content_digest

    def test_build_is_self_contained_hard_linked_and_deep_verifiable(self) -> None:
        result = self._build()
        self.assertEqual(result.file_count, 2 + len(OVERLAY_BUSINESS_KEYS))
        self.assertEqual(
            (self.parent / "payload/canonical/parent.json").stat().st_ino,
            (self.output / "payload/canonical/parent.json").stat().st_ino,
        )
        manifest = json.loads((self.output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(
            manifest["content"]["rollup"]["precedence"]["business_keys"],
            OVERLAY_BUSINESS_KEYS,
        )
        verified = verify_ods_base_rollup(self.output, policy=self.policy, deep=True)
        self.assertEqual(verified.manifest_sha256, result.manifest_sha256)

        shutil.rmtree(self.parent)
        shutil.rmtree(self.hotfix)
        self.assertEqual(
            verify_ods_base_rollup(self.output, policy=self.policy, deep=True).content_digest,
            result.content_digest,
        )

    def test_build_rejects_failed_hotfix_overlap_gate(self) -> None:
        shutil.rmtree(self.hotfix)
        self.hotfix_manifest_sha, self.hotfix_content_digest = self._write_hotfix(
            overlap_mismatches=1
        )
        with self.assertRaisesRegex(OdsBaseRollupError, "overlap mismatch gate"):
            self._build()

    def test_chained_rollup_accepts_only_additive_calendar_history(self) -> None:
        parent = self._build()
        hotfix_manifest_sha, hotfix_content_digest = self._write_calendar_hotfix(
            parent_manifest_sha=parent.manifest_sha256,
            parent_content_digest=parent.content_digest,
        )
        result = build_ods_base_rollup(
            parent_base=self.output,
            hotfix_bundle=self.calendar_hotfix,
            output=self.chained_output,
            bundle_id=self.chained_output.name,
            policy=self.policy,
            code_revision="d" * 40,
            built_at="2026-08-01T04:00:00Z",
            expected_parent_manifest_sha256=parent.manifest_sha256,
            expected_parent_content_digest=parent.content_digest,
            expected_hotfix_manifest_sha256=hotfix_manifest_sha,
            expected_hotfix_content_digest=hotfix_content_digest,
        )
        manifest = json.loads(
            (self.chained_output / "manifest.json").read_text(encoding="utf-8")
        )
        rollup = manifest["content"]["rollup"]
        self.assertEqual(rollup["schema_version"], 2)
        self.assertEqual(rollup["mode"], "self_contained_chained_rollup")
        self.assertEqual(rollup["parent"]["rollup"]["schema_version"], 1)
        self.assertEqual(
            rollup["precedence"]["business_keys"],
            {"trading_calendar_history_hotfix": ["exchange", "trade_date", "session"]},
        )
        self.assertEqual(
            (self.output / "payload/canonical/parent.json").stat().st_ino,
            (self.chained_output / "payload/canonical/parent.json").stat().st_ino,
        )
        self.assertEqual(
            verify_ods_base_rollup(self.chained_output, policy=self.policy, deep=True),
            result,
        )

        shutil.rmtree(self.output)
        shutil.rmtree(self.calendar_hotfix)
        verified = verify_ods_base_rollup(
            self.chained_output, policy=self.policy, deep=True
        )
        self.assertEqual(
            verified.content_digest,
            result.content_digest,
        )

    def test_pointer_advance_uses_exact_old_pointer_sha(self) -> None:
        result = self._build()
        current = self.base_root / "BASE_CURRENT.json"
        self._write_json(
            current,
            {
                "schema_version": 1,
                "bundle_id": self.parent.name,
                "bundle_path": self.parent.name,
                "manifest_sha256": self.parent_manifest_sha,
                "content_digest": self.parent_content_digest,
                "through_date": "2026-07-31",
            },
        )
        old_pointer_sha = _sha256(current)
        with self.assertRaisesRegex(OdsBaseRollupError, "file SHA precondition"):
            advance_ods_base_rollup_current(
                bundle=self.output,
                current_pointer=current,
                policy=self.policy,
                expected_current_pointer_sha256="sha256:" + "0" * 64,
                expected_current_bundle_id=self.parent.name,
                expected_target_manifest_sha256=result.manifest_sha256,
                expected_target_content_digest=result.content_digest,
            )
        advanced = advance_ods_base_rollup_current(
            bundle=self.output,
            current_pointer=current,
            policy=self.policy,
            expected_current_pointer_sha256=old_pointer_sha,
            expected_current_bundle_id=self.parent.name,
            expected_target_manifest_sha256=result.manifest_sha256,
            expected_target_content_digest=result.content_digest,
        )
        self.assertEqual(advanced.bundle_id, self.output.name)
        self.assertEqual(
            json.loads(current.read_text(encoding="utf-8"))["bundle_id"],
            self.output.name,
        )
        self.assertEqual(
            advance_ods_base_rollup_current(
                bundle=self.output,
                current_pointer=current,
                policy=self.policy,
                expected_current_pointer_sha256=old_pointer_sha,
                expected_current_bundle_id=self.parent.name,
                expected_target_manifest_sha256=result.manifest_sha256,
                expected_target_content_digest=result.content_digest,
            ).manifest_sha256,
            result.manifest_sha256,
        )


if __name__ == "__main__":
    unittest.main()

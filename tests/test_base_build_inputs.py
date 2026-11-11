"""Physical/frozen input closure tests; no synthetic fixture claims data acceptance."""
from __future__ import annotations

import copy
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quanthus.pipelines.securities_warehouse import base_build_inputs as inputs


class CompleteInputInventoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "source"
        self.root.mkdir()
        (self.root / "payload").mkdir()
        (self.root / "payload/source.json.gz").write_bytes(b"unaltered-source-bytes")
        self.row = {"path": "payload/source.json.gz", "size_bytes": 22,
                    "sha256": inputs.file_sha(self.root / "payload/source.json.gz")}
        self.row["size_bytes"] = (self.root / self.row["path"]).stat().st_size
        self.document = {"kind": "cn02_base24_daily_1d_native5m_v1", "files": [self.row],
                         "archival_source_path": "/unavailable/old/node/source"}
        self.seal()

    def seal(self):
        (self.root / "manifest.json").write_bytes(inputs.canonical(self.document))
        self.sha = inputs.file_sha(self.root / "manifest.json")
        (self.root / "READY").write_text(self.sha + "\n")

    def test_complete_inventory_is_not_data_acceptance(self):
        result = inputs.inventory(self.root, self.sha)
        self.assertEqual(result["file_count"], 3)
        self.assertEqual(result["status"], "FULL_MEMBER_INTEGRITY_PASS_NOT_DATA_ACCEPTANCE")
        self.assertEqual(result["identity"], inputs.identity(result["content"]))

    def test_relocation_does_not_read_archival_paths(self):
        frozen = inputs.inventory(self.root, self.sha)
        relocated = Path(self.tmp.name) / "new-location"
        shutil.copytree(self.root, relocated)
        shutil.rmtree(self.root)
        self.assertEqual(inputs.verify_inventory(relocated, frozen), frozen)

    def test_file_mutation_fails_cold_relocation(self):
        frozen = inputs.inventory(self.root, self.sha)
        (self.root / self.row["path"]).write_bytes(b"different-source-bytes")
        with self.assertRaisesRegex(inputs.BuildInputError, "MEMBER_IDENTITY"):
            inputs.verify_inventory(self.root, frozen)

    def test_missing_member(self):
        (self.root / self.row["path"]).unlink()
        with self.assertRaisesRegex(inputs.BuildInputError, "MEMBER_SET"):
            inputs.inventory(self.root, self.sha)

    def test_extra_member(self):
        (self.root / "unregistered-cache.parquet").write_bytes(b"hidden")
        with self.assertRaisesRegex(inputs.BuildInputError, "MEMBER_SET"):
            inputs.inventory(self.root, self.sha)

    def test_hardlinks_are_rejected(self):
        os.link(self.root / self.row["path"], Path(self.tmp.name) / "alias")
        with self.assertRaisesRegex(inputs.BuildInputError, "INDEPENDENT_REGULAR"):
            inputs.inventory(self.root, self.sha)

    def test_symlink_member_is_rejected(self):
        (self.root / self.row["path"]).unlink()
        real = Path(self.tmp.name) / "actual"
        real.write_bytes(b"unaltered-source-bytes")
        (self.root / self.row["path"]).symlink_to(real)
        with self.assertRaisesRegex(inputs.BuildInputError, "SYMLINK"):
            inputs.inventory(self.root, self.sha)

    def test_symlink_root_is_rejected(self):
        alias = Path(self.tmp.name) / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(inputs.BuildInputError, "SYMLINK"):
            inputs.inventory(alias, self.sha)

    def test_traversal_declaration(self):
        self.document["files"] = [{**self.row, "path": "../outside"}]
        self.seal()
        with self.assertRaisesRegex(inputs.BuildInputError, "MEMBER_PATH"):
            inputs.inventory(self.root, self.sha)

    def test_duplicate_declaration(self):
        self.document["files"] = [self.row, dict(self.row)]
        self.seal()
        with self.assertRaisesRegex(inputs.BuildInputError, "DECLARATION_INVALID"):
            inputs.inventory(self.root, self.sha)

    def test_replacement_manifest_cannot_select_itself(self):
        old = self.sha
        self.document["another_revision"] = True
        self.seal()
        with self.assertRaisesRegex(inputs.BuildInputError, "MANIFEST_IDENTITY"):
            inputs.inventory(self.root, old)

    def test_manifest_changed_between_hash_and_object_read(self):
        original = inputs._object
        def changing_object(path):
            value = original(path)
            if path.name == "manifest.json":
                value["unselected_revision"] = True
                path.write_bytes(inputs.canonical(value))
            return value
        with (
            patch.object(inputs, "_object", side_effect=changing_object),
            self.assertRaisesRegex(inputs.BuildInputError, "MANIFEST_IDENTITY"),
        ):
            inputs.inventory(self.root, self.sha)

    def test_unbound_ready(self):
        (self.root / "READY").write_text("sha256:" + "0" * 64)
        with self.assertRaisesRegex(inputs.BuildInputError, "READY_BINDING"):
            inputs.inventory(self.root, self.sha)

    def test_frozen_inventory_tamper(self):
        frozen = copy.deepcopy(inputs.inventory(self.root, self.sha))
        frozen["content"]["files"].pop()
        with self.assertRaisesRegex(inputs.BuildInputError, "FROZEN_IDENTITY"):
            inputs.verify_inventory(self.root, frozen)

    def test_ods_content_inventory(self):
        self.document = {"kind": "quanthus_ods_base24_bundle", "content": {"files": [self.row]}}
        self.seal()
        self.assertEqual(inputs.inventory(self.root, self.sha)["file_count"], 3)

    def test_dwd_descriptor_and_evidence_are_bound(self):
        (self.root / "evidence.json").write_bytes(b"evidence")
        descriptor = {"layout": {"dwd_security_bar": {"files": [self.row]}},
                      "evidence": {"evidence.json": inputs.file_sha(self.root / "evidence.json")}}
        (self.root / "MATERIALIZATION.json").write_bytes(inputs.canonical(descriptor))
        self.document = {
            "kind": "dwd_base24_native_1d5m_purpose_qualified_candidate_v1",
            "materialization_sha256": inputs.file_sha(self.root / "MATERIALIZATION.json"),
        }
        self.seal()
        self.assertEqual(inputs.inventory(self.root, self.sha)["file_count"], 5)
        (self.root / "MATERIALIZATION.json").write_bytes(b"{}")
        with self.assertRaisesRegex(inputs.BuildInputError, "DESCRIPTOR_IDENTITY"):
            inputs.inventory(self.root, self.sha)

    def test_unknown_protocol_fails_closed(self):
        self.document["kind"] = "unknown"
        self.seal()
        with self.assertRaisesRegex(inputs.BuildInputError, "UNSUPPORTED_KIND"):
            inputs.inventory(self.root, self.sha)


if __name__ == "__main__":
    unittest.main()

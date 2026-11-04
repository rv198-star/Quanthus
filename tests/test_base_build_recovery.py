from __future__ import annotations

import hashlib
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from quanthus.pipelines.securities_warehouse import base_build_recovery as recovery
from quanthus.pipelines.securities_warehouse.base_build_inputs import BuildInputError


class SelectedRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "restored"
        self.data = b"original-sdk-source-bytes"
        self.sha = "sha256:" + hashlib.sha256(self.data).hexdigest()
        self.row = {"path": "release/artifacts/source.json.gz", "sha256": self.sha,
                    "size_bytes": len(self.data)}
        self.request = {"contract": recovery.REQUEST, "members": [self.row]}

    def archive(self, *, data=None, kind=None, duplicate=False):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            other = tarfile.TarInfo("../../unselected")
            other.size = 4
            archive.addfile(other, io.BytesIO(b"skip"))
            if data is not False:
                for _ in range(2 if duplicate else 1):
                    payload = self.data if data is None else data
                    info = tarfile.TarInfo("blobs/" + self.sha[7:])
                    if kind:
                        info.type = kind
                        info.linkname = "/unmounted/source"
                    else:
                        info.size = len(payload)
                    archive.addfile(info, None if kind else io.BytesIO(payload))
        buffer.seek(0)
        return buffer

    def test_selected_bytes_only_and_no_product_ready(self):
        result = recovery.recover_stream(self.archive(), self.request, self.root)
        self.assertFalse(result["READY"])
        self.assertEqual((self.root / self.row["path"]).read_bytes(), self.data)
        self.assertEqual(len(list(self.root.rglob("*.gz"))), 1)
        self.assertFalse((self.root.parent / "unselected").exists())

    def test_existing_correct_member_is_verified_not_overwritten(self):
        recovery.recover_stream(self.archive(), self.request, self.root)
        path = self.root / self.row["path"]
        before = path.stat().st_mtime_ns
        result = recovery.recover_stream(io.BytesIO(), self.request, self.root)
        self.assertEqual(result["disposition"][self.row["path"]], "VERIFIED_REUSED")
        self.assertEqual(path.stat().st_mtime_ns, before)

    def test_existing_conflict_rejected(self):
        path = self.root / self.row["path"]
        path.parent.mkdir(parents=True)
        path.write_bytes(b"conflicting-old-stage")
        with self.assertRaisesRegex(BuildInputError, "EXISTING_TARGET_CONFLICT"):
            recovery.recover_stream(self.archive(), self.request, self.root)
        self.assertEqual(path.read_bytes(), b"conflicting-old-stage")

    def test_missing_blob(self):
        with self.assertRaisesRegex(BuildInputError, "SELECTED_BLOB_MISSING"):
            recovery.recover_stream(self.archive(data=False), self.request, self.root)

    def test_corruption_removes_partial_member(self):
        wrong = b"X" * len(self.data)
        with self.assertRaisesRegex(BuildInputError, "MEMBER_IDENTITY"):
            recovery.recover_stream(self.archive(data=wrong), self.request, self.root)
        self.assertFalse((self.root / self.row["path"]).exists())
        self.assertFalse(list(self.root.rglob("*.recovery-tmp")))

    def test_link_blob_rejected(self):
        with self.assertRaisesRegex(BuildInputError, "SELECTED_BLOB_INVALID"):
            recovery.recover_stream(self.archive(kind=tarfile.SYMTYPE), self.request, self.root)

    def test_duplicate_blob_rejected(self):
        with self.assertRaisesRegex(BuildInputError, "SELECTED_BLOB_INVALID"):
            recovery.recover_stream(self.archive(duplicate=True), self.request, self.root)

    def test_two_targets_same_bytes_are_independent(self):
        second = {**self.row, "path": "second/source.gz"}
        request = {"contract": recovery.REQUEST, "members": [self.row, second]}
        recovery.recover_stream(self.archive(), request, self.root)
        a, b = self.root / self.row["path"], self.root / second["path"]
        self.assertNotEqual(a.stat().st_ino, b.stat().st_ino)
        self.assertEqual(a.stat().st_nlink, 1)
        self.assertEqual(b.stat().st_nlink, 1)

    def test_verified_kept_copy(self):
        source = Path(self.tmp.name) / "retained.bin"
        source.write_bytes(self.data)
        result = recovery.recover_stream(io.BytesIO(), self.request, self.root,
                                         kept={self.sha: str(source)})
        self.assertEqual(result["disposition"][self.row["path"]], "VERIFIED_RETAINED_COPY")
        self.assertEqual(source.read_bytes(), self.data)

    def test_changed_kept_copy_rejected(self):
        source = Path(self.tmp.name) / "retained.bin"
        source.write_bytes(b"broken")
        with self.assertRaisesRegex(BuildInputError, "KEPT_SOURCE_CHANGED"):
            recovery.recover_stream(io.BytesIO(), self.request, self.root,
                                    kept={self.sha: str(source)})

    def test_target_traversal_rejected(self):
        request = {"contract": recovery.REQUEST, "members": [{**self.row, "path": "../escape"}]}
        with self.assertRaisesRegex(BuildInputError, "MEMBER_PATH"):
            recovery.recover_stream(self.archive(), request, self.root)


if __name__ == "__main__":
    unittest.main()

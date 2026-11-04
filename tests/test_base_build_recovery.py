from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def archive(self, *, data=None, kind=None, duplicate=False, relative=False):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            other = tarfile.TarInfo("../../unselected")
            other.size = 4
            archive.addfile(other, io.BytesIO(b"skip"))
            if data is not False:
                for _ in range(2 if duplicate else 1):
                    payload = self.data if data is None else data
                    name = self.row["path"] if relative else "blobs/" + self.sha[7:]
                    info = tarfile.TarInfo(name)
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

    def cli_fixture(self, *, relative=False):
        archive = Path(self.tmp.name) / "selected.tar.zst"
        raw = self.archive(relative=relative).read()
        compressed = subprocess.check_output(
            ["zstd", "--long=28", "-c"], input=raw,
        )
        archive.write_bytes(compressed)
        request = Path(self.tmp.name) / "request.json"
        request.write_text(json.dumps(self.request))
        archive_sha = "sha256:" + hashlib.sha256(compressed).hexdigest()
        argv = ["recover", "--request", str(request), "--archive", str(archive),
                "--archive-sha256", archive_sha, "--output", str(self.root)]
        if relative:
            argv += ["--archive-format", "relative-path"]
        return argv

    def cli_run(self, argv):
        # This test qualifies decoding, not the host's disposable /tmp capacity.
        usage = shutil.disk_usage(Path(self.tmp.name))._replace(free=16 * 1024 ** 3)
        with (
            patch("sys.argv", argv),
            patch.object(recovery.shutil, "disk_usage", return_value=usage),
        ):
            recovery.main()

    @unittest.skipUnless(shutil.which("zstd"), "zstd CLI required")
    def test_cli_restores_existing_long_window_archive(self):
        self.cli_run(self.cli_fixture())
        receipt = json.loads((self.root / "RECOVERY.safe.json").read_bytes())
        self.assertEqual(receipt["members"], self.request["members"])
        self.assertEqual((self.root / self.row["path"]).read_bytes(), self.data)
        self.assertFalse(receipt["READY"])

    @unittest.skipUnless(shutil.which("zstd"), "zstd CLI required")
    def test_cli_retry_keeps_original_receipt_and_rechecks_payload(self):
        argv = self.cli_fixture()
        self.cli_run(argv)
        path = self.root / "RECOVERY.safe.json"
        original = path.read_bytes()
        inode = path.stat().st_ino
        self.cli_run(argv)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(path.stat().st_ino, inode)
        (self.root / self.row["path"]).write_bytes(b"X" * len(self.data))
        with self.assertRaisesRegex(BuildInputError, "EXISTING_TARGET_CONFLICT"):
            self.cli_run(argv)
        self.assertEqual(path.read_bytes(), original)

    @unittest.skipUnless(shutil.which("zstd"), "zstd CLI required")
    def test_cli_retry_rejects_receipt_for_another_request(self):
        argv = self.cli_fixture()
        self.cli_run(argv)
        path = self.root / "RECOVERY.safe.json"
        changed = json.loads(path.read_bytes())
        changed["request_identity"] = "sha256:" + "0" * 64
        path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(BuildInputError, "RECEIPT_CONFLICT"):
            self.cli_run(argv)

    def test_request_cannot_claim_recovery_receipt_as_source_data(self):
        request = {"contract": recovery.REQUEST,
                   "members": [{**self.row, "path": "RECOVERY.safe.json"}]}
        with self.assertRaisesRegex(BuildInputError, "RESERVED_TARGET"):
            recovery.recover_stream(self.archive(), request, self.root)

    @unittest.skipUnless(shutil.which("zstd"), "zstd CLI required")
    def test_relative_member_cli_restore_and_retry(self):
        argv = self.cli_fixture(relative=True)
        self.cli_run(argv)
        self.assertEqual((self.root / self.row["path"]).read_bytes(), self.data)
        self.assertFalse((self.root.parent / "unselected").exists())
        receipt = (self.root / "RECOVERY.safe.json").read_bytes()
        self.cli_run(argv)
        self.assertEqual((self.root / "RECOVERY.safe.json").read_bytes(), receipt)
        self.assertEqual(json.loads(receipt)["archive_format"], "relative-path-v1")

    def test_relative_member_archive_rejects_duplicate_even_on_reuse(self):
        recovery.recover_path_stream(self.archive(relative=True), self.request, self.root)
        with self.assertRaisesRegex(BuildInputError, "SELECTED_MEMBER_INVALID"):
            recovery.recover_path_stream(self.archive(relative=True, duplicate=True),
                                         self.request, self.root)

    def test_relative_member_archive_rejects_link_missing_and_corrupt_bytes(self):
        cases = [(dict(kind=tarfile.SYMTYPE), "SELECTED_MEMBER_INVALID"),
                 (dict(data=False), "SELECTED_MEMBER_MISSING"),
                 (dict(data=b"X" * len(self.data)), "MEMBER_IDENTITY")]
        for number, (kwargs, reason) in enumerate(cases):
            with self.subTest(reason=reason), self.assertRaisesRegex(BuildInputError, reason):
                recovery.recover_path_stream(self.archive(relative=True, **kwargs), self.request,
                                             self.root.with_name("failure-" + str(number)))

    @unittest.skipUnless(shutil.which("zstd"), "zstd CLI required")
    def test_failed_member_does_not_wait_on_a_blocked_decoder(self):
        argv = self.cli_fixture()
        archive = Path(self.tmp.name) / "selected.tar.zst"
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as tar:
            bad = tarfile.TarInfo("blobs/" + self.sha[7:])
            bad.size = len(self.data)
            tar.addfile(bad, io.BytesIO(b"X" * len(self.data)))
            tail = tarfile.TarInfo("unselected-large-tail")
            tail.size = 2 * 1024 ** 2
            tar.addfile(tail, io.BytesIO(b"F" * tail.size))
        compressed = subprocess.check_output(["zstd", "-c"], input=buffer.getvalue())
        archive.write_bytes(compressed)
        archive_sha = "sha256:" + hashlib.sha256(compressed).hexdigest()
        argv[argv.index("--archive-sha256") + 1] = archive_sha
        # A subprocess timeout detects an actual blocked decoder rather than
        # mocking away the producer/consumer pipe that caused the deadlock.
        script = (
            "import json,sys,shutil; from unittest.mock import patch; "
            "from quanthus.pipelines.securities_warehouse import base_build_recovery as r; "
            "sys.argv=json.loads(sys.argv[1]); "
            "usage=shutil.disk_usage('.')._replace(free=16*1024**3); "
            "p=patch.object(r.shutil,'disk_usage',return_value=usage); p.start(); r.main()"
        )
        result = subprocess.run([sys.executable, "-c", script, json.dumps(argv)],
                                capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"BUILD_RECOVERY_MEMBER_IDENTITY", result.stderr)
        self.assertFalse((self.root / "RECOVERY.safe.json").exists())

    def test_target_traversal_rejected(self):
        request = {"contract": recovery.REQUEST, "members": [{**self.row, "path": "../escape"}]}
        with self.assertRaisesRegex(BuildInputError, "MEMBER_PATH"):
            recovery.recover_stream(self.archive(), request, self.root)


if __name__ == "__main__":
    unittest.main()

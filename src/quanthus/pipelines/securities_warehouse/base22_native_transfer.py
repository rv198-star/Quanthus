"""Forward ownership of completed job-local physical-copy TMP, without hardlinks.

Only an unsealed, pinned successful materialization can supply exact matching
files. Original input roots, changed inputs, failed partitions and old receipts
are never moved or deleted. The caller freezes this transfer before execution.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .base22 import digest, file_sha
from .base22_bounded import BoundedError
from .base22_sources import _fingerprint, _member, disjoint_output, physical_path


class OwnedTransfer:
    def __init__(
        self,
        *,
        root: Path,
        materialization_sha256: str,
        success_receipt: Path,
        plan: dict[str, Any],
        output: Path,
        kind: str,
    ) -> None:
        sources = [physical_path(Path(v["root"])) for v in plan["sources"].values()]
        self.root = disjoint_output(root, sources)
        disjoint_output(output, [self.root, *sources])
        if self.root.parent not in {output.parent, output.parent.parent}:
            raise BoundedError("NATIVE_OWNED_TRANSFER_NOT_SAME_JOB")
        receipt = json.loads(physical_path(success_receipt).read_bytes())
        marker = self.root / "MATERIALIZATION.json"
        if (
            (self.root / "READY").exists()
            or (self.root / "manifest.json").exists()
            or file_sha(marker) != materialization_sha256
            or receipt.get("root") != str(self.root)
            or receipt.get("materialization_sha256") != materialization_sha256
            or receipt.get("READY") is not False
        ):
            raise BoundedError("NATIVE_OWNED_TRANSFER_NOT_SUCCESSFUL_UNSEALED_TMP")
        record = json.loads(marker.read_bytes())
        if (
            record.get("kind") != kind
            or record.get("READY") is not False
            or sum(i["rows"] for t in record["layout"].values() for i in t["files"])
            != receipt.get("selected_rows")
        ):
            raise BoundedError("NATIVE_OWNED_TRANSFER_RECEIPT_MISMATCH")
        self.files: dict[tuple[str, str], Path] = {}
        self.snapshots = {}
        old = {
            (i["source_id"], i["archive_source_path"]): i
            for t in record["layout"].values()
            for i in t["files"]
        }
        bindings = []
        for entry in plan["tables"].values():
            for item in entry["files"]:
                key = (item["source_id"], item["path"])
                previous = old.get(key)
                if (
                    previous is None
                    or record["source_descriptors"].get(item["source_id"])
                    != plan["sources"][item["source_id"]]["descriptor_sha256"]
                    or previous
                    != {**item, "archive_source_path": item["path"], "path": previous["path"]}
                ):
                    continue  # New/repartitioned input is copied, not transferred.
                path = _member(self.root, previous["path"])
                if (
                    not path.is_file()
                    or path.stat().st_nlink != 1
                    or file_sha(path) != item["sha256"]
                ):
                    raise BoundedError("NATIVE_OWNED_TRANSFER_BYTES_CHANGED")
                self.files[key] = path
                self.snapshots[key] = _fingerprint(path)
                bindings.append(
                    {
                        "source_id": key[0],
                        "original_member": key[1],
                        "owned_member": previous["path"],
                        "sha256": item["sha256"],
                    }
                )
        self.registration = {
            "kind": "job_local_successful_materialization_forward_ownership_v1",
            "root_archival": str(self.root),
            "materialization_sha256": materialization_sha256,
            "success_receipt_sha256": file_sha(success_receipt),
            "frozen_plan_digest": digest(plan),
            "files": bindings,
            "new_output": str(output),
            "original_input_roots_moved": False,
            "changed_or_failed_partition_bytes_retained": True,
            "hardlink_or_symlink_created": False,
        }
        self.registration["identity"] = digest(self.registration)
        self.moved: list[str] = []

    def move(self, item: dict[str, Any], target: Path) -> bool:
        key = (item["source_id"], item["path"])
        source = self.files.get(key)
        if source is None:
            return False
        if (
            target.exists()
            or target.parent.stat().st_dev != source.stat().st_dev
            or _fingerprint(source) != self.snapshots[key]
            or file_sha(source) != item["sha256"]
        ):
            raise BoundedError("NATIVE_OWNED_TRANSFER_CHANGED_OR_CROSS_DEVICE")
        before = source.stat()
        os.rename(source, target)
        after = target.stat()
        if (
            (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
            or after.st_nlink != 1
            or file_sha(target) != item["sha256"]
        ):
            raise BoundedError("NATIVE_OWNED_TRANSFER_INVARIANT_FAILED")
        self.moved.append(str(target))
        return True

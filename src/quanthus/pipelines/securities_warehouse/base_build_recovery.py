"""Bounded SHA-selected recovery of regular data members from retained content.

No archive pathname is extracted. Callers select exact bytes/relative targets;
links, traversal, duplicate selected blobs, missing members and corrupt bytes fail.
The receipt is recovery evidence, never data qualification or a product READY.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import Any, BinaryIO

from .base_build_inputs import BuildInputError, canonical, file_sha, identity, member

REQUEST = "p10-base-selected-regular-recovery-v1"


def _sha(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise BuildInputError("BUILD_RECOVERY_SHA_REQUIRED")
    return value


def validate_request(request: dict[str, Any]) -> None:
    if (set(request) != {"contract", "members"} or request["contract"] != REQUEST
            or not isinstance(request["members"], list) or not request["members"]):
        raise BuildInputError("BUILD_RECOVERY_REQUEST_SCHEMA")
    paths = set()
    for row in request["members"]:
        if (not isinstance(row, dict) or set(row) != {"path", "sha256", "size_bytes"}
                or type(row["size_bytes"]) is not int or row["size_bytes"] < 0):
            raise BuildInputError("BUILD_RECOVERY_MEMBER_SCHEMA")
        member(Path("/unmounted-recovery-root"), row["path"])
        _sha(row["sha256"])
        if row["path"] in paths:
            raise BuildInputError("BUILD_RECOVERY_DUPLICATE_TARGET")
        paths.add(row["path"])


def _put(root: Path, row: dict[str, Any], stream: BinaryIO) -> None:
    target = member(root, row["path"])
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".recovery-tmp")
    h = hashlib.sha256()
    size = 0
    try:
        with temporary.open("xb") as dst:
            while data := stream.read(1024 * 1024):
                size += len(data)
                if size > row["size_bytes"]:
                    raise BuildInputError("BUILD_RECOVERY_MEMBER_SIZE")
                h.update(data)
                dst.write(data)
            dst.flush()
            os.fsync(dst.fileno())
        if size != row["size_bytes"] or "sha256:" + h.hexdigest() != row["sha256"]:
            raise BuildInputError("BUILD_RECOVERY_MEMBER_IDENTITY")
        # Never overwrite a conflicting or unexpectedly appeared destination.
        if target.exists() or target.is_symlink():
            raise BuildInputError("BUILD_RECOVERY_TARGET_APPEARED")
        temporary.rename(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def recover_stream(stream: BinaryIO, request: dict[str, Any], output: Path, *,
                   kept: dict[str, str] | None = None) -> dict[str, Any]:
    """Read a tar stream of content-addressed blobs, writing only selected bytes.

    Verified retained files may supply identical bytes. Repeated invocations only
    reuse a complete matching target; half-written/conflicting targets fail closed.
    """
    validate_request(request)
    output = member(output.parent.absolute(), output.name)
    output.mkdir(exist_ok=True)
    kept = kept or {}
    wanted: dict[str, list[dict[str, Any]]] = {}
    disposition = {}
    for row in request["members"]:
        path = member(output, row["path"])
        if path.exists():
            if path.stat().st_size != row["size_bytes"] or file_sha(path) != row["sha256"]:
                raise BuildInputError("BUILD_RECOVERY_EXISTING_TARGET_CONFLICT")
            disposition[row["path"]] = "VERIFIED_REUSED"
        elif row["sha256"] in kept:
            source = Path(kept[row["sha256"]])
            if source.stat().st_size != row["size_bytes"] or file_sha(source) != row["sha256"]:
                raise BuildInputError("BUILD_RECOVERY_KEPT_SOURCE_CHANGED")
            with source.open("rb") as src:
                _put(output, row, src)
            disposition[row["path"]] = "VERIFIED_RETAINED_COPY"
        else:
            wanted.setdefault(row["sha256"], []).append(row)
    seen = set()
    if wanted:
        with tarfile.open(fileobj=stream, mode="r|") as archive:
            for record in archive:
                expected = "sha256:" + record.name.removeprefix("blobs/")
                if expected not in wanted:
                    continue
                if (record.name != "blobs/" + expected[7:] or not record.isfile()
                        or expected in seen):
                    raise BuildInputError("BUILD_RECOVERY_SELECTED_BLOB_INVALID")
                rows = wanted[expected]
                if any(row["size_bytes"] != record.size for row in rows):
                    raise BuildInputError("BUILD_RECOVERY_MEMBER_SIZE")
                src = archive.extractfile(record)
                if src is None:
                    raise BuildInputError("BUILD_RECOVERY_BLOB_UNREADABLE")
                with src:
                    _put(output, rows[0], src)
                disposition[rows[0]["path"]] = "VERIFIED_ARCHIVED_BLOB"
                for row in rows[1:]:
                    with member(output, rows[0]["path"]).open("rb") as source:
                        _put(output, row, source)
                    disposition[row["path"]] = "VERIFIED_ARCHIVED_BLOB_COPY"
                seen.add(expected)
    if set(wanted) != seen:
        raise BuildInputError("BUILD_RECOVERY_SELECTED_BLOB_MISSING")
    for row in request["members"]:
        path = member(output, row["path"])
        if path.stat().st_size != row["size_bytes"] or file_sha(path) != row["sha256"]:
            raise BuildInputError("BUILD_RECOVERY_FINAL_MEMBER_IDENTITY")
    return {"contract": REQUEST, "status": "SELECTED_BYTES_RECOVERED_NOT_DATA_ACCEPTANCE",
            "request_identity": identity(request), "members": request["members"],
            "disposition": disposition, "READY": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--kept", type=Path)
    args = parser.parse_args()
    request = json.loads(args.request.read_bytes())
    validate_request(request)
    if file_sha(args.archive) != _sha(args.archive_sha256):
        raise BuildInputError("BUILD_RECOVERY_ARCHIVE_IDENTITY")
    target = args.output.resolve()
    sources = [args.archive.resolve(), args.request.resolve()]
    if args.kept is not None:
        sources.append(args.kept.resolve())
    if any(source == target or target in source.parents for source in sources):
        raise BuildInputError("BUILD_RECOVERY_OUTPUT_OVERLAP")
    required = sum(row["size_bytes"] for row in request["members"])
    if shutil.disk_usage(args.output.parent).free < required + 8 * 1024 ** 3:
        raise BuildInputError("BUILD_RECOVERY_CAPACITY")
    kept = json.loads(args.kept.read_bytes()) if args.kept else {}
    with subprocess.Popen(["zstd", "-d", "-c", str(args.archive)],
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as decoder:
        if decoder.stdout is None:
            raise BuildInputError("BUILD_RECOVERY_DECODER")
        result = recover_stream(decoder.stdout, request, args.output, kept=kept)
        # Drain the stream so zstd can validate the container and finish normally.
        for _ in iter(lambda: decoder.stdout.read(1024 * 1024), b""):
            pass
        if decoder.wait() != 0:
            raise BuildInputError("BUILD_RECOVERY_ARCHIVE_DECODE")
    with (args.output / "RECOVERY.safe.json").open("x") as receipt:
        receipt.write(canonical(result).decode("ascii") + "\n")
    print(result["status"], len(result["members"]))


if __name__ == "__main__":
    main()

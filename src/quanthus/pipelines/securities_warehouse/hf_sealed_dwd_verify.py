"""Finish a native DWD upload by verifying one immutable Hugging Face revision.

This command never uploads or deletes data. Source metadata is part of the expected
set even when manifest.json and READY are outside manifest.content.files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def log(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False), flush=True)


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + "." + str(os.getpid()))
    with temporary.open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def hashes(path: Path) -> tuple[str, str]:
    size = path.stat().st_size
    sha, blob = hashlib.sha256(), hashlib.sha1()
    blob.update(f"blob {size}\0".encode())
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024**2):
            sha.update(block)
            blob.update(block)
    return "sha256:" + sha.hexdigest(), blob.hexdigest()


def inventory(root: Path, expected_manifest: str) -> dict[str, dict[str, Any]]:
    if root.is_symlink() or root.resolve() != root:
        raise ValueError("HF_PHYSICAL_SEALED_DWD_REQUIRED")
    manifest = root / "manifest.json"
    if hashes(manifest)[0] != expected_manifest:
        raise ValueError("HF_SEALED_MANIFEST_CHANGED")
    body = json.loads(manifest.read_bytes())
    if body.get("kind") != "quanthus_dwd_base_bundle_v1":
        raise ValueError("HF_SEALED_DWD_KIND")
    declared = {}
    for row in body["content"]["files"]:
        relative = Path(row["path"])
        if relative.is_absolute() or ".." in relative.parts or str(relative) in declared:
            raise ValueError("HF_INVALID_SEALED_MEMBER")
        declared[str(relative)] = row
    ready = (root / "READY").read_text().strip()
    if ready != expected_manifest:
        raise ValueError("HF_SEALED_READY_CHANGED")
    expected = set(declared) | {"manifest.json", "READY"}
    actual = set()
    for directory, dirs, names in os.walk(root, followlinks=False):
        for name in [*dirs, *names]:
            path = Path(directory) / name
            if path.is_symlink():
                raise ValueError("HF_SEALED_MEMBER_LINK")
        actual.update(str((Path(directory) / name).relative_to(root)) for name in names)
    if actual != expected:
        raise ValueError("HF_SOURCE_FILE_SET_DIFFERS")
    results = {}
    for count, relative in enumerate(sorted(actual), 1):
        path = root / relative
        size = path.stat().st_size
        sha, blob = hashes(path)
        row = declared.get(relative)
        if row and (size != row["size_bytes"] or sha != row["sha256"]):
            raise ValueError("HF_SOURCE_MEMBER_CHANGED:" + relative)
        results["DWD/" + relative] = {"size": size, "sha256": sha, "git_blob_sha1": blob}
        if count % 10000 == 0:
            log({"source_verified": count, "total": len(actual)})
    return results


def compare(expected: dict, remote: dict) -> dict[str, Any]:
    missing = sorted(set(expected) - set(remote))
    extra = sorted(set(remote) - set(expected))
    mismatched, lfs, blobs, verified = [], 0, 0, []
    for path in sorted(set(expected) & set(remote)):
        local, cloud = expected[path], remote[path]
        if cloud["size"] != local["size"]:
            mismatched.append(path)
        elif cloud["lfs_sha256"]:
            lfs += 1
            if cloud["lfs_sha256"] != local["sha256"].removeprefix("sha256:"):
                mismatched.append(path)
        else:
            blobs += 1
            if cloud["blob_id"] != local["git_blob_sha1"]:
                mismatched.append(path)
        if path not in mismatched:
            verified.append(path)
    return {
        "expected_files": len(expected),
        "expected_bytes": sum(r["size"] for r in expected.values()),
        "verified_files": len(verified),
        "verified_bytes": sum(expected[p]["size"] for p in verified),
        "missing": missing,
        "extra": extra,
        "mismatched": mismatched,
        "files": len(verified),
        "bytes": sum(expected[p]["size"] for p in verified),
        "lfs_sha256_checked": lfs,
        "git_blob_checked": blobs,
        "status": "VERIFIED" if not (missing or extra or mismatched) else "FAILED",
    }


def remote_inventory(api: Any, repo: str, revision: str) -> dict[str, Any]:
    from huggingface_hub import RepoFile

    for attempt in range(6):
        rows = {}
        try:
            for item in api.list_repo_tree(
                repo_id=repo,
                repo_type="dataset",
                path_in_repo="DWD",
                recursive=True,
                expand=False,
                revision=revision,
            ):
                if not isinstance(item, RepoFile):
                    continue
                rows[item.path] = {
                    "size": item.size,
                    "lfs_sha256": getattr(item.lfs, "sha256", None),
                    "blob_id": item.blob_id,
                }
                if len(rows) % 1000 == 0:
                    log({"remote_listed": len(rows), "revision": revision})
                    time.sleep(0.5)
            return rows
        except Exception as exc:
            if attempt == 5:
                raise
            delay = min(180, 10 * 2**attempt)
            log({"remote_retry": attempt + 1, "error": type(exc).__name__, "delay": delay})
            time.sleep(delay)
    raise RuntimeError("HF_UNREACHABLE")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dwd", type=Path, required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    from huggingface_hub import HfApi

    token = os.environ.get("HF_TOKEN")
    if not token:
        raise ValueError("HF_TOKEN_MISSING")
    expected = inventory(args.dwd, args.manifest_sha256)
    api = HfApi(token=token)
    info = api.repo_info(repo_id=args.repo, repo_type="dataset")
    if info.private is not True:
        raise ValueError("HF_PRIVATE_REPOSITORY_REQUIRED")
    revision = info.sha
    log({"remote_revision": revision, "source_files": len(expected)})
    remote = remote_inventory(api, args.repo, revision)
    result = compare(expected, remote)
    proof = args.receipt.with_name(args.receipt.stem + "-INVENTORY-v2.json")
    write(proof, {"revision": revision, "source": expected, "remote": remote})
    old = json.loads(args.receipt.read_bytes()) if args.receipt.exists() else {}
    old.update(
        kind="base251-hf-dwd-upload-v2",
        status="COMPLETE" if result["status"] == "VERIFIED" else "FAILED",
        repo=args.repo,
        revision=revision,
        manifest_sha256=args.manifest_sha256,
        verification=result,
        files=result["files"],
        bytes=result["bytes"],
        inventory_proof=str(proof),
        inventory_proof_sha256=hashes(proof)[0],
        verified_at=datetime.now(timezone.utc).isoformat(),
        source_hashes="SHA256",
        remote_hashes="LFS_SHA256_OR_GIT_BLOB_SHA1",
        consumer_admission="NOT_GRANTED",
        execution_authorized=False,
    )
    if result["status"] == "VERIFIED":
        old["groups"] = [g for g in old.get("groups", []) if g["label"] != "root"] + [
            {
                "label": "root",
                "files": 3,
                "status": "REMOTE_SIZE_AND_SHA256_VERIFIED",
                "bytes": sum(
                    expected["DWD/" + name]["size"]
                    for name in ("READY", "manifest.json", "MATERIALIZATION.json")
                ),
            }
        ]
    write(args.receipt, old)
    log({"done": True, **result, "revision": revision})
    if result["status"] != "VERIFIED":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

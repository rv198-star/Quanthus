"""Bounded immutable GDrive transport for frozen Base 2.4 Daily/Weekly selections.

This is a byte-backup stage, not a warehouse qualifier or a source scanner. Its
input is the exact upstream P10 selection, including retained qualification and
limitations. Cloud success never changes that qualification or permits deletion.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

from quanthus.p10_operational_mail import atomic
from quanthus.pipelines.securities_warehouse.container_code import digest, verify_bundle

POLICY = Path(__file__).resolve().parents[2] / "config/pipelines/base24-backup.v1.json"


def physical(path: Path, *, directory: bool = False) -> os.stat_result:
    info = path.lstat()
    if (
        path.resolve(strict=True) != path
        or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
        or (not directory and info.st_nlink != 1)
    ):
        raise ValueError("P10_BACKUP_PHYSICAL_PATH_REQUIRED")
    return info


def tree(root: Path) -> set[str]:
    physical(root, directory=True)

    def failed(error: OSError) -> None:
        raise error

    found = set()
    for base, dirs, files in os.walk(root, followlinks=False, onerror=failed):
        for name in dirs:
            physical(Path(base) / name, directory=True)
        for name in files:
            path = Path(base) / name
            physical(path)
            found.add(path.relative_to(root).as_posix())
    return found


def relative(name: str) -> str:
    path = PurePosixPath(name)
    if (
        not name
        or path.is_absolute()
        or path.as_posix() != name
        or any(p in {".", ".."} for p in path.parts)
        or any(c in name for c in "\n\r\x00\\")
    ):
        raise ValueError("P10_BACKUP_MEMBER_PATH_INVALID")
    return name


def selection(plan_path: Path, state: Path) -> tuple[dict[str, Any], str]:
    physical(plan_path)
    physical(state, directory=True)
    if plan_path.stat().st_size > 16 * 1024**2:
        raise ValueError("P10_BACKUP_SELECTION_TOO_LARGE")
    raw = plan_path.read_bytes()
    plan = json.loads(raw)
    policy = json.loads(POLICY.read_bytes())
    if (
        plan.get("kind") != "p10-base24-backup-selection-v1"
        or plan.get("policy_sha256") != digest(POLICY)
        or plan.get("base_version") != policy["base_version"]
        or plan.get("base_manifest_sha256") != policy["base_manifest_sha256"]
        or plan.get("intervals") != policy["intervals"]
        or plan.get("execution_authorized") is not False
        or plan.get("formal_product_deletion") != "forbidden"
        or not re.fullmatch(r"[0-9a-f]{40}", str(plan.get("code_revision")))
        or not isinstance(plan.get("packages"), list)
        or not 1 <= len(plan["packages"]) <= 32
        or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", str(plan.get("remote_name")))
        or set(plan.get("folder_ids", {})) != {"ODS", "DWD"}
        or len(set(plan["folder_ids"].values())) != 2
        or any(
            not re.fullmatch(r"[a-zA-Z0-9_-]{10,128}", str(v)) for v in plan["folder_ids"].values()
        )
    ):
        raise ValueError("P10_BACKUP_BASE24_SELECTION_REQUIRED")
    limits = plan["limits"]
    for key in ("max_source_bytes", "max_archive_bytes", "free_floor_bytes", "timeout_seconds"):
        if type(limits.get(key)) is not int or limits[key] <= 0:
            raise ValueError("P10_BACKUP_EXPLICIT_BUDGET_REQUIRED")
    if not 30 <= limits["timeout_seconds"] <= 14400:
        raise ValueError("P10_BACKUP_TIMEOUT_OUT_OF_BOUNDS")
    total, archives, ids = 0, 0, set()
    protected = [plan_path, POLICY]
    for package in plan["packages"]:
        identifier = package["id"]
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,79}", identifier) or identifier in ids:
            raise ValueError("P10_BACKUP_PACKAGE_ID_INVALID")
        ids.add(identifier)
        date.fromisoformat(package["business_date"])
        if (
            package["layer"] not in policy["layers"]
            or package["cadence"] not in policy["cadences"]
            or package.get("qualification") not in {"qualified_scope", "partial_scope"}
            or not isinstance(package.get("limitations"), list)
            or any(not isinstance(s, str) for s in package["limitations"])
            or (package["qualification"] == "partial_scope" and not package["limitations"])
        ):
            raise ValueError("P10_BACKUP_SCOPE_INVALID")
        root = Path(package["root"])
        physical(root, directory=True)
        protected.append(root)
        files = package["files"]
        if not isinstance(files, dict) or not files or tree(root) != set(files):
            raise ValueError("P10_BACKUP_EXACT_PACKAGE_SET_DIFFERS")
        for name, record in files.items():
            path = root / relative(name)
            if (
                type(record["bytes"]) is not int
                or record["bytes"] < 0
                or physical(path).st_size != record["bytes"]
                or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"])
            ):
                raise ValueError("P10_BACKUP_FROZEN_MEMBER_DIFFERS")
            total += record["bytes"]
        proof = package["qualification_receipt"]
        if relative(proof) not in files:
            raise ValueError("P10_BACKUP_QUALIFICATION_RECEIPT_MISSING")
        # Uncompressed deterministic tar has a conservative exact-format bound.
        archives += 10240 + sum(
            (((v["bytes"] + 511) // 512) + ((len(n.encode()) + 768) // 512) + 2) * 512
            for n, v in files.items()
        )
        # Includes the safe cloud inventory plus the persisted plan/control files.
        archives += 2 * len(json.dumps(package).encode()) + 8192
    for path in protected:
        if state.is_relative_to(path) or path.is_relative_to(state):
            raise ValueError("P10_BACKUP_STATE_SOURCE_OVERLAP")
    if total > limits["max_source_bytes"] or archives > limits["max_archive_bytes"]:
        raise ValueError("P10_BACKUP_WRITE_BUDGET_EXCEEDED")
    if shutil.disk_usage(state).free < archives + limits["free_floor_bytes"]:
        raise ValueError("P10_BACKUP_FREE_FLOOR")
    return plan, hashlib.sha256(raw).hexdigest()


class _Reader:
    def __init__(self, stream: Any, until: float):
        self.stream, self.until = stream, until
        self.sha = hashlib.sha256()

    def read(self, size: int = -1, /) -> bytes:
        if size < 0:
            raise ValueError("P10_BACKUP_BOUNDED_READ_REQUIRED")
        if time.monotonic() >= self.until:
            raise TimeoutError("P10_BACKUP_JOB_TIMEOUT")
        value: bytes = self.stream.read(size)
        self.sha.update(value)
        return value


def pack(package: dict[str, Any], output: Path, until: float) -> None:
    """Read each source once while archiving; never persist an unverified archive."""
    root = Path(package["root"])
    with output.open("xb") as target, tarfile.open(fileobj=target, mode="w|") as archive:
        for name, record in sorted(package["files"].items()):
            path = root / name
            before = physical(path)
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = before.st_size, 0o440, 0
            with path.open("rb") as stream:
                reader = _Reader(stream, until)
                archive.addfile(info, reader)
            after = physical(path)
            if (
                reader.sha.hexdigest() != record["sha256"]
                or any(
                    getattr(before, k) != getattr(after, k)
                    for k in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
                )
                or after.st_size != record["bytes"]
            ):
                raise ValueError("P10_BACKUP_SOURCE_CHANGED_DURING_PACK")
    if tree(root) != set(package["files"]):
        raise ValueError("P10_BACKUP_SOURCE_SET_CHANGED_DURING_PACK")


def cloud(args: list[str], plan: dict[str, Any], secret: Path, until: float) -> str:
    timeout = min(600, int(until - time.monotonic()))
    if timeout < 1:
        raise TimeoutError("P10_BACKUP_JOB_TIMEOUT")
    command = [
        "rclone",
        *args,
        "--config",
        str(secret),
        "--retries",
        "1",
        "--low-level-retries",
        "2",
        "--contimeout",
        "15s",
        "--timeout",
        "60s",
        "--tpslimit",
        "1",
        "--checkers",
        "1",
        "--transfers",
        "1",
    ]
    # Never inherit alternate remotes, credentials, TLS-disable or arbitrary flags.
    env = {"PATH": os.defpath, "HOME": "/tmp", "TMPDIR": os.environ.get("TMPDIR", "/tmp")}
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        result = subprocess.run(
            command, env=env, stdout=stdout, stderr=stderr, timeout=timeout, check=False
        )
        if result.returncode:
            raise RuntimeError("P10_BACKUP_RCLONE_FAILED")
        if stdout.tell() > 1024**2:
            raise ValueError("P10_BACKUP_REMOTE_REPLY_TOO_LARGE")
        stdout.seek(0)
        return stdout.read().decode("utf-8")


def backup(plan_path: Path, state: Path, secret: Path) -> dict[str, Any]:
    plan, key = selection(plan_path, state)  # Before mkdir/lock/archive writes.
    raw = plan_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != key:
        raise ValueError("P10_BACKUP_SELECTION_CHANGED")
    until = time.monotonic() + plan["limits"]["timeout_seconds"]
    physical(secret)
    run = state / key
    if run.is_symlink():
        raise ValueError("P10_BACKUP_STATE_LINK_FORBIDDEN")
    run.mkdir(mode=0o700, exist_ok=True)
    physical(run, directory=True)
    lock_path = run / "LOCK"
    if lock_path.exists() or lock_path.is_symlink():
        physical(lock_path)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        frozen = run / "selection.json"
        if frozen.exists():
            physical(frozen)
            if digest(frozen) != key:
                raise ValueError("P10_BACKUP_FROZEN_SELECTION_CHANGED")
        else:
            with frozen.open("xb") as stream:
                stream.write(raw)
        results = []
        for package in plan["packages"]:
            item = run / package["id"]
            item.mkdir(exist_ok=True)
            physical(item, directory=True)
            upload, packed = item / "upload", item / "PACKED.json"
            upload.mkdir(exist_ok=True)
            physical(upload, directory=True)
            archive = upload / "data.tar"
            if packed.exists():
                physical(packed)
                physical(archive)
                hashes = json.loads(packed.read_bytes())
                if digest(archive) != hashes["sha256"] or archive.stat().st_size != hashes["bytes"]:
                    raise ValueError("P10_BACKUP_RETRY_ARCHIVE_CHANGED")
            else:
                # An interrupted/corrupt pack is evidence, never an overwrite target.
                if time.monotonic() >= until:
                    raise TimeoutError("P10_BACKUP_JOB_TIMEOUT")
                if (
                    shutil.disk_usage(state).free
                    < plan["limits"]["free_floor_bytes"]
                    + sum(
                        r["bytes"] + 2048 + len(n.encode()) * 2 for n, r in package["files"].items()
                    )
                    + 32768
                ):
                    raise ValueError("P10_BACKUP_FREE_FLOOR")
                pack(package, archive, until)
                if archive.stat().st_size > plan["limits"]["max_archive_bytes"]:
                    raise ValueError("P10_BACKUP_ARCHIVE_BUDGET_EXCEEDED")
                md5 = hashlib.md5(usedforsecurity=False)
                sha = hashlib.sha256()
                with archive.open("rb") as stream:
                    while block := stream.read(1024**2):
                        if time.monotonic() >= until:
                            raise TimeoutError("P10_BACKUP_JOB_TIMEOUT")
                        md5.update(block)
                        sha.update(block)
                hashes = {
                    "sha256": sha.hexdigest(),
                    "md5": md5.hexdigest(),
                    "bytes": archive.stat().st_size,
                }
                atomic(packed, hashes)
            metadata = {
                "kind": "p10-base24-cloud-backup-v1",
                "selection_sha256": key,
                "base_manifest_sha256": plan["base_manifest_sha256"],
                "base_version": "2.4",
                "backup_code_revision": plan["code_revision"],
                "policy_sha256": plan["policy_sha256"],
                "intervals": ["1d", "5m"],
                "archive": hashes,
                "package": {k: v for k, v in package.items() if k != "root"},
                "local_deletion_authorized": False,
                "data_qualification_changed": False,
            }
            meta_path = upload / "backup.json"
            if meta_path.exists():
                physical(meta_path)
                if json.loads(meta_path.read_bytes()) != metadata:
                    raise ValueError("P10_BACKUP_RETRY_METADATA_CHANGED")
            else:
                atomic(meta_path, metadata)
            expected = {}
            for name in ("data.tar", "backup.json"):
                member = upload / name
                md5 = hashlib.md5(usedforsecurity=False)
                # Archive MD5 was computed once during the post-pack pass above.
                if name == "data.tar":
                    value = hashes["md5"]
                else:
                    md5.update(member.read_bytes())
                    value = md5.hexdigest()
                expected[name] = (member.stat().st_size, value)
            if tree(upload) != set(expected):
                raise ValueError("P10_BACKUP_UPLOAD_SET_DIFFERS")
            remote = (
                f"{plan['remote_name']}:{package['cadence']}/base-2.4/"
                f"{package['business_date']}/{key}/{package['id']}"
            )
            flags = ["--drive-root-folder-id", plan["folder_ids"][package["layer"]]]
            cloud(
                ["copy", str(upload), remote, "--immutable", "--checksum", *flags],
                plan,
                secret,
                until,
            )
            observed = json.loads(
                cloud(["lsjson", remote, "--files-only", "--hash", *flags], plan, secret, until)
            )
            names = [v.get("Name") for v in observed]
            if (
                len(names) != len(set(names))
                or set(names) != set(expected)
                or any(
                    (v.get("Size"), v.get("Hashes", {}).get("MD5")) != expected[v["Name"]]
                    for v in observed
                )
            ):
                raise ValueError("P10_BACKUP_CLOUD_EXACT_BYTES_DIFFER")
            results.append(
                {
                    "id": package["id"],
                    "layer": package["layer"],
                    "cadence": package["cadence"],
                    "hashes": hashes,
                    "qualification": package["qualification"],
                    "limitations": package["limitations"],
                }
            )
        done = {
            "status": "cloud_bytes_verified",
            "selection_sha256": key,
            "base_version": "2.4",
            "packages": results,
            "data_qualification_changed": False,
            "local_deletion_authorized": False,
        }
        atomic(run / "VERIFIED.json", done)
        return done


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    revision = json.loads((root / "SOURCE.json").read_bytes())["revision"]
    verify_bundle(root, revision)
    if json.loads(args.selection.read_bytes()).get("code_revision") != revision:
        raise ValueError("P10_BACKUP_IMAGE_SELECTION_REVISION_DIFFERS")
    result = backup(args.selection, Path("/run/backup"), Path("/run/secrets/gdrive/rclone.conf"))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()

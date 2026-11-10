"""Bounded, byte-verified Hist-1D source recovery (Issue 602)."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib import request

INDEX_SHA256 = "50f984a671aed2a5f04206b19fb77a8eea14d223f83d701d0fe782d88444c72c"
MANIFEST_SHA256 = "6991d0c9440d4117c3dfdd3375e2267d0e43174203fc834751c9476cd7598206"
RECOVERY_TABLES = (
    "dwd_security_bar",
    "dwd_adjustment_factor",
    "dwd_corporate_action",
    "dwd_listing",
    "dwd_listing_status_interval",
    "dwd_trading_calendar",
    "dwd_price_limit_regime",
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024**2), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bound_json(path: Path, expected_sha256: str) -> dict[str, Any]:
    if file_sha256(path) != expected_sha256:
        raise ValueError("SOURCE_METADATA_HASH_MISMATCH")
    value: dict[str, Any] = json.loads(path.read_bytes())
    return value


def safe_destination(root: Path, member: str) -> Path:
    parts = PurePosixPath(member)
    if parts.is_absolute() or ".." in parts.parts or parts.parts[:2] != ("DWD", "payload"):
        raise ValueError("UNSAFE_ARCHIVE_PATH")
    path = root.joinpath(*parts.parts)
    if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
        raise ValueError("UNSAFE_RECOVERY_DESTINATION")
    return path


def recovery_selection(index: dict[str, Any]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for archive in index["archive_parts"]:
        members = []
        for member in archive["members"]:
            name = str(member[0])
            table = next((t for t in RECOVERY_TABLES if name.startswith(f"DWD/payload/{t}/")), None)
            if table is None:
                continue
            safe_destination(Path("/recovery"), name)
            if table == "dwd_security_bar":
                year = re.search(r"/observation_start_year=(\d{4})/", name)
                if (
                    "/interval_size=1d/" not in name
                    or year is None
                    or not 1990 <= int(year[1]) <= 2021
                ):
                    continue
            if name in seen:
                raise ValueError("DUPLICATE_SOURCE_MEMBER")
            seen.add(name)
            members.append(member)
        if members:
            selected.append(
                {**{k: v for k, v in archive.items() if k != "members"}, "members": members}
            )
    if not selected:
        raise ValueError("EMPTY_RECOVERY_SELECTION")
    return selected


def extract_members(archive: tarfile.TarFile, members: list[list[Any]], root: Path) -> int:
    expected = {str(row[0]): row for row in members}
    seen: set[str] = set()
    for info in archive:
        if info.name not in expected:
            continue
        if info.name in seen or not info.isfile():
            raise ValueError("INVALID_OR_DUPLICATE_TAR_MEMBER")
        seen.add(info.name)
        _, size, digest = expected[info.name]
        path = safe_destination(root, info.name)
        if info.size != size:
            raise ValueError("ARCHIVE_MEMBER_SIZE_MISMATCH")
        if path.exists():
            if path.stat().st_size != size or file_sha256(path) != digest:
                raise ValueError("EXISTING_RECOVERY_MEMBER_DIFFERS")
            continue
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_name(path.name + ".partial")
        if temporary.is_symlink():
            raise ValueError("UNSAFE_RECOVERY_PARTIAL")
        if temporary.exists():
            if temporary.stat().st_size == size and file_sha256(temporary) == digest:
                os.replace(temporary, path)
                continue
            temporary.unlink()
        source = archive.extractfile(info)
        if source is None:
            raise ValueError("UNREADABLE_ARCHIVE_MEMBER")
        try:
            with source, temporary.open("xb") as output:
                shutil.copyfileobj(source, output, length=4 * 1024**2)
            if temporary.stat().st_size != size or file_sha256(temporary) != digest:
                raise ValueError("ARCHIVE_MEMBER_HASH_MISMATCH")
            temporary.chmod(0o600)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    if seen != set(expected):
        raise ValueError("ARCHIVE_MEMBERS_MISSING")
    return len(seen)


def _opener(proxy: str | None) -> request.OpenerDirector:
    return request.build_opener(
        request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})
    )


def drive_token(credentials: Path, proxy: str | None) -> str:
    settings = json.loads(credentials.read_bytes())
    req = request.Request(
        settings["oauth_url"],
        headers={
            "X-AuthID": settings["authid"],
            "User-Agent": "Duplicati/2.4.0",
        },
    )
    with _opener(proxy).open(req, timeout=30) as response:
        return str(json.load(response)["access_token"])


def download_archive(part: dict[str, Any], root: Path, token: str, proxy: str | None) -> Path:
    path = root / str(part["name"])
    if path.name != part["name"]:
        raise ValueError("INVALID_ARCHIVE_NAME")
    if path.is_symlink():
        raise ValueError("UNSAFE_ARCHIVE_DESTINATION")
    if path.exists():
        if path.stat().st_size != part["archive_bytes"] or file_sha256(path) != part["sha256"]:
            raise ValueError("EXISTING_SOURCE_ARCHIVE_DIFFERS")
        return path
    partial = path.with_suffix(path.suffix + ".partial")
    if partial.is_symlink():
        raise ValueError("UNSAFE_ARCHIVE_PARTIAL")
    if partial.exists():
        partial_size = partial.stat().st_size
        if partial_size > part["archive_bytes"]:
            raise ValueError("SOURCE_ARCHIVE_PARTIAL_TOO_LARGE")
        if partial_size == part["archive_bytes"]:
            if file_sha256(partial) != part["sha256"]:
                raise ValueError("SOURCE_ARCHIVE_HASH_MISMATCH")
            os.replace(partial, path)
            return path
        if partial_size == 0:
            partial.unlink()
    started = time.monotonic()
    for attempt in range(3):
        offset = partial.stat().st_size if partial.exists() else 0
        headers = {"Authorization": "Bearer " + token}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        req = request.Request(
            "https://www.googleapis.com/drive/v3/files/" + str(part["file_id"]) + "?alt=media",
            headers=headers,
        )
        try:
            with _opener(proxy).open(req, timeout=60) as response:
                if offset and response.status != 206:
                    raise ValueError("RESUMED_SOURCE_RANGE_NOT_HONORED")
                with partial.open("ab" if offset else "xb") as output:
                    for chunk in iter(lambda: response.read(4 * 1024**2), b""):
                        output.write(chunk)
                        if (
                            output.tell() > part["archive_bytes"]
                            or time.monotonic() - started > 1200
                        ):
                            raise ValueError("SOURCE_DOWNLOAD_BUDGET_EXCEEDED")
        except (OSError, TimeoutError):
            if attempt == 2:
                raise
            continue
        break
    if partial.stat().st_size != part["archive_bytes"] or file_sha256(partial) != part["sha256"]:
        raise ValueError("SOURCE_ARCHIVE_HASH_MISMATCH")
    os.replace(partial, path)
    print(
        json.dumps(
            {
                "archive": path.name,
                "bytes": path.stat().st_size,
                "elapsed_s": round(time.monotonic() - started, 3),
                "status": "HASH_VERIFIED",
            }
        ),
        flush=True,
    )
    return path


def recover(workspace: Path, credentials: Path, proxy: str | None) -> dict[str, Any]:
    metadata = workspace / "raw/base23/metadata"
    index = bound_json(metadata / "archive-index.json", INDEX_SHA256)
    bound_json(metadata / "manifest.json", MANIFEST_SHA256)
    parts = recovery_selection(index)
    compressed_bytes = sum(int(p["archive_bytes"]) for p in parts)
    extracted_bytes = sum(int(m[1]) for p in parts for m in p["members"])
    if compressed_bytes > 3_000_000_000 or compressed_bytes + extracted_bytes > 16_000_000_000:
        raise ValueError("RECOVERY_SIZE_BUDGET_EXCEEDED")
    if shutil.disk_usage(workspace).free < 2 * (compressed_bytes + extracted_bytes):
        raise ValueError("RECOVERY_FREE_SPACE_INSUFFICIENT")
    raw = workspace / "raw/base23/archives"
    raw.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = workspace / "raw/base23/extracted"
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    started = time.monotonic()
    token = drive_token(credentials, proxy)
    with ThreadPoolExecutor(max_workers=2) as pool:
        paths = list(pool.map(lambda part: download_archive(part, raw, token, proxy), parts))
    count = 0
    for part, path in zip(parts, paths, strict=True):
        with subprocess.Popen(["zstd", "-dc", str(path)], stdout=subprocess.PIPE) as process:
            assert process.stdout is not None
            with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
                count += extract_members(archive, part["members"], destination)
            if process.wait():
                raise ValueError("ARCHIVE_DECOMPRESSION_FAILED")
    receipt = {
        "status": "PASS_SOURCE_RECOVERY",
        "index_sha256": INDEX_SHA256,
        "manifest_sha256": MANIFEST_SHA256,
        "archive_count": len(parts),
        "archive_bytes": compressed_bytes,
        "member_count": count,
        "member_bytes": extracted_bytes,
        "elapsed_s": round(time.monotonic() - started, 3),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "members": [m for p in parts for m in p["members"]],
    }
    (workspace / "RECOVERY-RECEIPT.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return {k: v for k, v in receipt.items() if k != "members"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--credential-file", type=Path, required=True)
    parser.add_argument("--proxy")
    args = parser.parse_args()
    print(json.dumps(recover(args.workspace, args.credential_file, args.proxy)), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Copy one verified partial native capture into a new bounded recovery run.

No provider or price interpretation here: preserve exact source bytes and acquisition
receipts. The existing native qualifier still owns all row semantics and grades.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import stat
from datetime import datetime
from pathlib import Path
from typing import Any


def obj(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("NATIVE_RESUME_OBJECT_REQUIRED")
    return value


def inventory(root: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    total = 0

    def failed(error: OSError) -> None:
        raise error

    for directory, dirs, names in os.walk(root, onerror=failed, followlinks=False):
        for name in [*dirs, *names]:
            path = Path(directory) / name
            if path.is_symlink():
                raise ValueError("NATIVE_RESUME_LINK_FORBIDDEN")
            if name in dirs:
                continue
            before = path.stat()
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("NATIVE_RESUME_REGULAR_FILE_REQUIRED")
            total += before.st_size
            if total > 256 * 1024**2 or len(result) >= 100000:
                raise ValueError("NATIVE_RESUME_SEED_SIZE_LIMIT")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if any(
                getattr(path.stat(), key) != getattr(before, key)
                for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
            ):
                raise ValueError("NATIVE_RESUME_SOURCE_CHANGED")
            result[path.relative_to(root).as_posix()] = {"bytes": before.st_size, "sha256": digest}
    return result


def seed_capture(seed: Path, output: Path, ref: dict[str, Any]) -> dict[str, Any]:
    """Validate everything before mkdir; copy regular independent files only."""
    if seed.is_symlink() or seed.resolve(strict=True) != seed.absolute():
        raise ValueError("NATIVE_RESUME_PHYSICAL_SEED_REQUIRED")
    seed = seed.absolute()
    if output.is_symlink() or output.resolve() != output.absolute():
        raise ValueError("NATIVE_RESUME_PHYSICAL_OUTPUT_REQUIRED")
    output = output.absolute()
    if output.exists() or output.is_relative_to(seed) or seed.is_relative_to(output):
        raise ValueError("NATIVE_RESUME_NEW_DISJOINT_OUTPUT_REQUIRED")
    files = inventory(seed)
    codes = sorted(ref["securities"])
    if not codes or len(codes) != ref["population"]:
        raise ValueError("NATIVE_RESUME_POPULATION_DIFFERS")
    complete = []
    reused = 0
    for shard in sorted(seed.iterdir()):
        if not shard.is_dir() or not re.fullmatch(r"shard-[0-9]{3}", shard.name):
            raise ValueError("NATIVE_RESUME_UNKNOWN_SHARD")
        index = int(shard.name[-3:])
        selected = codes[index * 50 : (index + 1) * 50]
        expected = {
            "schema_version": 1,
            "kind": "p10_baostock_native_m5_raw_capture_shard",
            "market_date": ref["market_date"],
            "market_ods_manifest_sha256": ref["source_ods_manifest_sha256"],
            "shard_index": index,
            "selected_symbols": selected,
            "provider": "baostock-native-5m",
            "frequency": "5",
            "adjustflag": "3",
            "formal_warehouse_ready": False,
        }
        if not selected or obj(shard / "identity.json") != expected:
            raise ValueError("NATIVE_RESUME_SHARD_IDENTITY_DIFFERS")
        if set(p.name for p in shard.iterdir()) - {
            "identity.json",
            "summary.json",
            "raw",
            "receipts",
            "original-responses",
        }:
            raise ValueError("NATIVE_RESUME_UNKNOWN_MEMBER")
        receipts = {p.name.removesuffix(".json"): p for p in (shard / "receipts").iterdir()}
        raws = {p.name.removesuffix(".json.gz"): p for p in (shard / "raw").iterdir()}
        if set(receipts) != set(raws) or not set(receipts) <= set(selected):
            raise ValueError("NATIVE_RESUME_ORPHAN_OR_UNKNOWN_SOURCE")
        for code, path in receipts.items():
            receipt = obj(path)
            if (
                path.name != code + ".json"
                or raws[code].name != code + ".json.gz"
                or receipt.get("stock_code") != code
                or receipt.get("status") != "raw_saved"
                or receipt.get("market_date") != ref["market_date"]
                or receipt.get("provider") != "baostock-native-5m"
                or receipt.get("source_market_ods_sha256") != ref["source_ods_manifest_sha256"]
                or datetime.fromisoformat(receipt["captured_at"]).tzinfo is None
            ):
                raise ValueError("NATIVE_RESUME_RECEIPT_IDENTITY_DIFFERS")
            with gzip.open(raws[code], "rb") as stream:
                raw = stream.read(1024**2 + 1)
            if len(raw) > 1024**2 or hashlib.sha256(raw).hexdigest() != receipt["raw_sha256"]:
                raise ValueError("NATIVE_RESUME_RAW_HASH_DIFFERS")
        reused += len(receipts)
        if (shard / "summary.json").exists():
            summary = obj(shard / "summary.json")
            if summary != {
                **expected,
                "saved": len(selected),
                "errors": 0,
                "published": False,
            } or set(receipts) != set(selected):
                raise ValueError("NATIVE_RESUME_INCOMPLETE_OR_FAILED_SUMMARY")
            complete.append(index)
    if not files or not reused:
        raise ValueError("NATIVE_RESUME_USEFUL_SOURCE_REQUIRED")
    shutil.copytree(seed, output, copy_function=shutil.copyfile)
    if inventory(output) != files or inventory(seed) != files:
        raise ValueError("NATIVE_RESUME_COPY_OR_SOURCE_CHANGED")
    digest = hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "population": len(codes),
        "complete_shards": complete,
        "reused_securities": reused,
        "remaining_securities": len(codes) - reused,
        "seed_inventory_sha256": "sha256:" + digest,
        "copied_files": len(files),
        "copied_bytes": sum(x["bytes"] for x in files.values()),
        "source_changed": False,
        "independent_regular_files": True,
    }

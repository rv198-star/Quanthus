"""Offline runtime admission, including installed wheel payload verification."""
from __future__ import annotations

import base64
import csv
import importlib.metadata
import io
import json
import platform
import re
import subprocess
import sys
import zipfile
import zoneinfo
from pathlib import Path
from typing import Any

from .base22_bounded import BoundedError
from .base_build_inputs import file_sha, identity, member

LOCK_SHA = "sha256:54fc4729e4b4ca32116e33107a6eb8da66c4195db3358564f55c55753219b892"


def load_lock(path: Path) -> dict[str, Any]:
    if file_sha(path) != LOCK_SHA:
        raise BoundedError("BUILD_ENVIRONMENT_LOCK_UNSUPPORTED_OR_CHANGED")
    return json.loads(path.read_bytes())


def _version(value: str) -> tuple[int, ...]:
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", value):
        raise BoundedError("BUILD_ENVIRONMENT_VERSION_UNREADABLE")
    return tuple(int(n) for n in value.split("."))


def observations() -> dict[str, Any]:
    libc, libc_version = platform.libc_ver()
    return {"system": platform.system(), "machine": platform.machine(),
            "implementation": platform.python_implementation(),
            "python": platform.python_version(), "libc": libc, "libc_version": libc_version,
            "python_executable": sys.executable,
            "timezone_search_path": list(zoneinfo.TZPATH)}


def verify_installed_wheel(wheel: Path, row: dict[str, Any]) -> dict[str, Any]:
    """Compare every installed wheel payload with its hash-pinned RECORD.

    Pip's generated entry-point wrappers, pyc and installation metadata are not
    wheel payload. No module import, network, or trust in a version string alone.
    """
    if wheel.stat().st_size != row["bytes"] or file_sha(wheel) != "sha256:" + row["sha256"]:
        raise BoundedError("BUILD_ENVIRONMENT_WHEEL_BYTES_CHANGED")
    distribution = importlib.metadata.distribution(row["distribution"])
    if distribution.version != row["version"]:
        raise BoundedError("BUILD_ENVIRONMENT_PACKAGE_VERSION_CHANGED")
    files = total_bytes = 0
    with zipfile.ZipFile(wheel) as archive:
        names = [n for n in archive.namelist() if not n.endswith("/")]
        records = [n for n in names if n.endswith(".dist-info/RECORD")]
        if len(names) != len(set(names)) or len(records) != 1:
            raise BoundedError("BUILD_ENVIRONMENT_WHEEL_RECORD_INVALID")
        recorded = {}
        for path, checksum, size in csv.reader(io.StringIO(archive.read(records[0]).decode())):
            member(Path("/offline-wheel-validation"), path)
            if path in recorded or any(p.endswith(".data") for p in path.split("/")):
                raise BoundedError("BUILD_ENVIRONMENT_WHEEL_RECORD_INVALID")
            recorded[path] = checksum, size
        if set(recorded) != set(names):
            raise BoundedError("BUILD_ENVIRONMENT_WHEEL_RECORD_INVALID")
        for name in sorted(names):
            if name == records[0]:
                continue  # Pip rewrites RECORD to include its generated files.
            checksum, size = recorded[name]
            if not checksum.startswith("sha256=") or not size.isdigit():
                raise BoundedError("BUILD_ENVIRONMENT_WHEEL_RECORD_INVALID")
            expected = base64.urlsafe_b64decode(checksum[7:] + "==").hex()
            installed = Path(distribution.locate_file(name))
            if installed.stat().st_size != int(size) or file_sha(installed) != "sha256:" + expected:
                raise BoundedError("BUILD_ENVIRONMENT_INSTALLED_PAYLOAD_CHANGED")
            files += 1
            total_bytes += int(size)
    return {"distribution": row["distribution"], "version": row["version"],
            "wheel_sha256": "sha256:" + row["sha256"],
            "installed_files_verified": files, "installed_bytes_verified": total_bytes}


def probe_native_runtime() -> dict[str, Any]:
    """Load the native extensions in a fresh process, including mmap/ABI checks.

    An isolated child prevents modules cached by the caller from concealing an
    unusable installation. It reads no build data and performs no network access.
    """
    code = """
import json, zoneinfo
zoneinfo.reset_tzpath(())
from datetime import datetime
from decimal import Decimal
import duckdb, pyarrow as pa
value = Decimal('123456789.1234567890')
table = pa.table({'value': pa.array([value], type=pa.decimal128(38, 10))})
with duckdb.connect() as connection:
    connection.register('probe', table)
    assert connection.execute('SELECT value FROM probe').fetchone()[0] == value
local = datetime(2026, 10, 31, tzinfo=zoneinfo.ZoneInfo('Asia/Shanghai'))
assert local.utcoffset().total_seconds() == 28800
print(json.dumps({'duckdb': duckdb.__version__, 'pyarrow': pa.__version__,
                  'exact_decimal_roundtrip': 'PASS', 'shanghai_offset': 'PASS'}))
"""
    try:
        result = subprocess.run([sys.executable, "-I", "-c", code], check=True,
                                capture_output=True, text=True, timeout=30)
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise BoundedError("BUILD_ENVIRONMENT_NATIVE_IMPORT_OR_EXACT_TYPE_PROBE_FAILED") from exc


def check_environment(*, lock: Path, wheelhouse: Path, include_restore: bool = True
                      ) -> dict[str, Any]:
    """Validate the declared platform and exact wheel bytes before a data stage."""
    configuration = load_lock(lock)
    actual = observations()
    expected = configuration["platform"]
    if (any(actual[k] != expected[k] for k in ("system", "machine", "implementation"))
            or actual["libc"] != "glibc"
            or not actual["python"].startswith(expected["python_minor"] + ".")
            or _version(actual["python"]) < _version(expected["python_minimum"])
            or _version(actual["libc_version"]) < _version(expected["glibc_minimum"])):
        raise BoundedError("BUILD_ENVIRONMENT_PLATFORM_UNSUPPORTED")
    if actual["timezone_search_path"]:
        raise BoundedError("BUILD_ENVIRONMENT_PINNED_TZDATA_REQUIRED_SET_PYTHONTZPATH_EMPTY")
    packages = []
    for row in configuration["wheels"]:
        packages.append(verify_installed_wheel(member(wheelhouse, row["filename"]), row))
    native_probe = probe_native_runtime()
    if any(native_probe.get(name) != importlib.metadata.version(name)
           for name in ("duckdb", "pyarrow")):
        raise BoundedError("BUILD_ENVIRONMENT_CHILD_PACKAGE_IDENTITY_CHANGED")
    decoder = None
    if include_restore:
        result = subprocess.run(["zstd", "--version"], check=True, capture_output=True,
                                text=True, timeout=10)
        match = re.search(r"\bv([0-9]+\.[0-9]+\.[0-9]+)\b", result.stdout)
        if (match is None or _version(match[1])
                < _version(configuration["external_decoder"]["minimum_version"])):
            raise BoundedError("BUILD_ENVIRONMENT_RESTORE_DECODER_UNSUPPORTED")
        decoder = {"command": "zstd", "version": match[1],
                   "scope": "restore_only_verified_uncompressed_members"}
    result = {"kind": configuration["contract"], "status": "OFFLINE_ENVIRONMENT_VERIFIED",
              "lock_sha256": LOCK_SHA, "observed": actual,
              "installed_wheel_payloads": packages, "native_probe": native_probe,
              "restore_decoder": decoder,
              "data_acceptance": False}
    result["identity"] = identity(result)
    return result

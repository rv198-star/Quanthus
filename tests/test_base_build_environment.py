from __future__ import annotations

import base64
import csv
import hashlib
import io
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from quanthus.pipelines.securities_warehouse import base_build_environment as environment
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base_build_inputs import file_sha

LOCK = Path("ops/data/base-build-environment.v1.json")


def wheel_fixture(tmp_path, monkeypatch):
    payload = {"synthetic/__init__.py": b"value = 1\n",
               "synthetic-1.0.dist-info/METADATA": b"Name: synthetic\nVersion: 1.0\n"}
    installed = tmp_path / "installed"
    record = io.StringIO()
    writer = csv.writer(record)
    for name, data in payload.items():
        p = installed / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        checksum = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        writer.writerow([name, "sha256=" + checksum, len(data)])
    writer.writerow(["synthetic-1.0.dist-info/RECORD", "", ""])
    wheel = tmp_path / "synthetic.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, data in payload.items():
            archive.writestr(name, data)
        archive.writestr("synthetic-1.0.dist-info/RECORD", record.getvalue())
    distribution = SimpleNamespace(version="1.0", locate_file=lambda name: installed / name)
    monkeypatch.setattr(environment.importlib.metadata, "distribution", lambda name: distribution)
    row = {"distribution": "synthetic", "version": "1.0", "filename": wheel.name,
           "bytes": wheel.stat().st_size, "sha256": file_sha(wheel)[7:]}
    return wheel, row, installed, distribution


def test_every_installed_payload_is_checked_against_hash_pinned_wheel(tmp_path, monkeypatch):
    wheel, row, _, _ = wheel_fixture(tmp_path, monkeypatch)
    result = environment.verify_installed_wheel(wheel, row)
    assert result["installed_files_verified"] == 2


@pytest.mark.parametrize("damage", ["wheel", "installed-bytes", "version"])
def test_version_string_or_present_file_does_not_prove_locked_environment(tmp_path, monkeypatch,
                                                                        damage):
    wheel, row, installed, distribution = wheel_fixture(tmp_path, monkeypatch)
    if damage == "wheel":
        wheel.write_bytes(wheel.read_bytes() + b"changed")
    elif damage == "installed-bytes":
        (installed / "synthetic/__init__.py").write_bytes(b"value = 2\n")
    else:
        distribution.version = "1.1"
    with pytest.raises(BoundedError, match="BUILD_ENVIRONMENT_"):
        environment.verify_installed_wheel(wheel, row)


def test_changed_lock_is_rejected_before_platform_or_package_checks(tmp_path):
    bad = tmp_path / "lock.json"
    bad.write_text(LOCK.read_text().replace('"1.5.6"', '"1.5.5"'))
    with pytest.raises(BoundedError, match="LOCK_UNSUPPORTED_OR_CHANGED"):
        environment.load_lock(bad)


def baseline():
    return {"system": "Linux", "machine": "x86_64", "implementation": "CPython",
            "python": "3.10.12", "libc": "glibc", "libc_version": "2.35",
            "python_executable": "/synthetic/bin/python", "timezone_search_path": []}


@pytest.mark.parametrize("field,value", [("machine", "aarch64"), ("python", "3.13.5"),
                                       ("python", "3.10.11"), ("libc_version", "2.27"),
                                       ("system", "Darwin"), ("libc", "musl"),
                                       ("timezone_search_path", ["/usr/share/zoneinfo"])])
def test_unsupported_or_unpinned_runtime_fails_before_member_read(
    tmp_path, monkeypatch, field, value,
):
    actual = baseline()
    actual[field] = value
    monkeypatch.setattr(environment, "observations", lambda: actual)
    with pytest.raises(BoundedError, match="BUILD_ENVIRONMENT_"):
        environment.check_environment(lock=LOCK, wheelhouse=tmp_path)


def test_receipt_separates_environment_evidence_from_data_acceptance(tmp_path, monkeypatch):
    monkeypatch.setattr(environment, "observations", baseline)
    monkeypatch.setattr(environment, "verify_installed_wheel",
                        lambda wheel, row: {"distribution": row["distribution"]})
    monkeypatch.setattr(environment, "probe_native_runtime",
                        lambda: {"duckdb": "1.5.6", "pyarrow": "23.0.1"})
    result = environment.check_environment(lock=LOCK, wheelhouse=tmp_path, include_restore=False)
    assert result["data_acceptance"] is False
    assert result["status"] == "OFFLINE_ENVIRONMENT_VERIFIED"
    assert len(result["installed_wheel_payloads"]) == 3
    assert result["restore_decoder"] is None


def test_unloadable_native_extension_fails_preflight_with_stable_reason(monkeypatch):
    def noexec(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0], stderr="failed to map segment")

    monkeypatch.setattr(environment.subprocess, "run", noexec)
    with pytest.raises(BoundedError, match="NATIVE_IMPORT_OR_EXACT_TYPE_PROBE_FAILED"):
        environment.probe_native_runtime()


def test_isolated_child_must_load_the_same_package_versions(tmp_path, monkeypatch):
    monkeypatch.setattr(environment, "observations", baseline)
    monkeypatch.setattr(environment, "verify_installed_wheel", lambda wheel, row: {})
    monkeypatch.setattr(environment, "probe_native_runtime",
                        lambda: {"duckdb": "1.5.5", "pyarrow": "23.0.1"})
    with pytest.raises(BoundedError, match="CHILD_PACKAGE_IDENTITY_CHANGED"):
        environment.check_environment(lock=LOCK, wheelhouse=tmp_path, include_restore=False)

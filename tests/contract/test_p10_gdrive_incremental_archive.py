"""Synthetic archive deltas restore exact bytes without republishing consumers."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
from datetime import date
from pathlib import Path

import pytest

from quanthus.pipelines import p10_complete_daily as full
from quanthus.pipelines import p10_complete_daily_gdrive as archive
from quanthus.pipelines import p10_gdrive_incremental_archive as delta
from quanthus.pipelines.p10_delivery_common import (
    atomic,
    digest,
    encoded,
    file_digest,
    object_at,
    seal,
)
from tests.contract.test_p10_complete_daily_gdrive import complete as build_native_complete


@pytest.fixture
def native_complete(tmp_path: Path) -> Path:
    return build_native_complete.__wrapped__(tmp_path)


def source(root: Path, *, revision: str, changed: bytes = b"new daily") -> dict:
    root.mkdir()
    (root / "common-finance.txt").write_bytes(b"unchanged captured synthetic source" * 100)
    (root / "daily.txt").write_bytes(changed)
    seal(
        root,
        {
            "kind": archive.COMPLETE_KIND,
            "code_revision": revision,
            "base": {
                "manifest_sha256": archive.BASE23_MANIFEST,
                "content_digest": archive.BASE23_CONTENT,
            },
            "consumable_intervals": ["1d", "1m", "5m"],
        },
    )
    return object_at(root / "manifest.json")


def bundle(root: Path, package: Path, *, reference: dict | None = None) -> dict:
    if not shutil.which("zstd"):
        pytest.skip("zstd unavailable")
    root.mkdir()
    manifest = object_at(package / "manifest.json")
    inherited = (
        [x for x in manifest["files"] if x["path"] == "common-finance.txt"] if reference else []
    )
    omitted = {x["path"] for x in inherited}
    paths = [p for p in package.rglob("*") if p.is_file() and p.name not in omitted]
    tar = root / "archive.tar"
    with tarfile.open(tar, "w") as handle:
        if not reference:
            info = tarfile.TarInfo(".")
            info.type = tarfile.DIRTYPE
            handle.addfile(info)
        for p in paths:
            handle.add(p, arcname=p.relative_to(package).as_posix())
    compressed = root / "archive.tar.zst"
    subprocess.run(["zstd", "-q", "-3", str(tar), "-o", str(compressed)], check=True)
    tar.unlink()
    report = {
        "archive_name": compressed.name,
        "archive_sha256": file_digest(compressed),
        "archive_md5": archive.md5(compressed),
        "archive_bytes": compressed.stat().st_size,
        "archive_policy_sha256": "sha256:" + "a" * 64,
        "manifest_sha256": file_digest(package / "manifest.json"),
        "content_digest": manifest["content_digest"],
        "source_bytes": sum(p.stat().st_size for p in package.rglob("*") if p.is_file()),
        "packed_source_bytes": sum(p.stat().st_size for p in paths),
        "inherited_files": inherited,
        "archive_reference": reference,
    }
    atomic(root / "archive-manifest.json", report)
    atomic(root / "source-pointer.json", report)
    atomic(root / "source-verification.json", {**report, "base": manifest["base"]})
    return report


def test_full_then_delta_restores_exact_new_daily_without_repeating_common_source(tmp_path: Path):
    first, second = tmp_path / "first", tmp_path / "second"
    source(first, revision="a" * 40)
    source(second, revision="b" * 40, changed=b"new revision")
    b1, b2 = tmp_path / "bundle1", tmp_path / "bundle2"
    parent = bundle(b1, first)
    target = bundle(b2, second, reference=parent)
    result = delta.restore_bundles([b1, b2], tmp_path / "restored", target["manifest_sha256"])
    assert result["dependencies"] == 1 and result["consumer_publication"] is False
    assert target["packed_source_bytes"] < target["source_bytes"] / 2
    for p in second.rglob("*"):
        if p.is_file():
            assert p.read_bytes() == (tmp_path / "restored" / p.relative_to(second)).read_bytes()


@pytest.mark.parametrize(
    "fault", ["missing", "corrupt", "inherit", "evidence", "metadata", "budget"]
)
def test_invalid_dependency_or_payload_never_creates_output(tmp_path: Path, fault: str):
    first, second = tmp_path / "first", tmp_path / "second"
    source(first, revision="a" * 40)
    source(second, revision="b" * 40)
    b1, b2 = tmp_path / "bundle1", tmp_path / "bundle2"
    parent = bundle(b1, first)
    target = bundle(b2, second, reference=parent)
    bundles = [b1, b2]
    if fault == "missing":
        bundles = [b2]
    elif fault == "corrupt":
        (b1 / parent["archive_name"]).write_bytes(b"corrupt")
    elif fault == "inherit":
        target["inherited_files"][0]["sha256"] = "sha256:" + "0" * 64
    elif fault == "evidence":
        atomic(b2 / "source-verification.json", {"manifest_sha256": "wrong"})
    elif fault == "metadata":
        target["archive_reference"]["archive_md5"] = "0" * 32
    elif fault == "budget":
        target["packed_source_bytes"] = 1
    atomic(b2 / "archive-manifest.json", target)
    with pytest.raises(ValueError):
        delta.restore_bundles(bundles, tmp_path / "restored", target["manifest_sha256"])
    assert not (tmp_path / "restored").exists()
    assert not list(tmp_path.glob(".complete-archive-restore-*"))


def test_restore_cli_is_executable():
    completed = subprocess.run(
        [sys.executable, "-m", delta.__name__, "--help"], capture_output=True, text=True, check=True
    )
    assert "--expected-manifest-sha256" in completed.stdout


def test_older_failed_delta_reuses_frozen_reference_after_newer_day_succeeds(
    tmp_path: Path, monkeypatch
):
    state = tmp_path / "state"
    pointer = {"manifest_sha256": "sha256:" + "b" * 64, "content_digest": "content"}
    run = state / "runs" / ("b" * 64)
    upload = run / "upload"
    upload.mkdir(parents=True)
    physical = upload / "archive.tar.zst"
    physical.write_bytes(b"synthetic frozen physical archive")
    policy = object_at(archive.POLICY_PATH)
    saved = {
        **pointer,
        "market_date": "2026-10-14",
        "archive_policy_sha256": file_digest(archive.POLICY_PATH),
        "effective_archive_policy_sha256": digest(encoded(policy)),
        "archive_code_revision": "c" * 40,
        "archive_reference": {"remote": "gdrive:original-13th-reference"},
        "archive_name": physical.name,
        "archive_sha256": file_digest(physical),
    }
    atomic(run / "archive.json", saved)
    atomic(state / "LAST_SUCCESS.json", {"market_date": "2026-10-15"})
    monkeypatch.setattr(archive, "admitted", lambda *a: (tmp_path, pointer, {}))
    monkeypatch.setattr(
        archive, "delta_reference", lambda *a: pytest.fail("frozen reference reselected")
    )
    assert archive.freeze(tmp_path, date(2026, 10, 14), state, "c" * 40, policy) == (upload, saved)


def test_delta_requires_actual_confirmed_archive_and_bounds_chain(tmp_path: Path):
    root = tmp_path / "source"
    root.mkdir()
    package = root / "package"
    manifest = source(package, revision="a" * 40)
    state = tmp_path / "state"
    done = {
        "policy_id": "p10-complete-daily-gdrive-v3",
        "base": manifest["base"],
        "consumable_intervals": ["1d", "1m", "5m"],
        "market_date": "2026-10-12",
        "status": "cloud_archive_verified",
        "cloud_ready_confirmed_at": "2026-10-13T16:00:00Z",
        "source_package_path": str(package),
        "archive_depth": 27,
        "manifest_sha256": file_digest(package / "manifest.json"),
    }
    policy = {**object_at(archive.POLICY_PATH), "bootstrap_archive_state": str(tmp_path / "empty")}
    atomic(state / "LAST_SUCCESS.json", done)
    assert delta.delta_reference(root, state, policy, "2026-10-13") == (None, {})
    done.pop("cloud_ready_confirmed_at")
    atomic(state / "LAST_SUCCESS.json", done)
    with pytest.raises(ValueError, match="actual cloud confirmation"):
        delta.delta_reference(root, state, policy, "2026-10-13")


@pytest.mark.parametrize("fault", [None, "ancestor", "sidecar", "checksum"])
def test_cloud_confirmation_requires_entire_restorable_dependency_closure(
    tmp_path: Path, monkeypatch, fault: str | None
):
    first, second = tmp_path / "first", tmp_path / "second"
    source(first, revision="a" * 40)
    source(second, revision="b" * 40)
    b1, b2 = tmp_path / "bundle1", tmp_path / "bundle2"
    parent = bundle(b1, first)
    parent.update(
        {
            "remote": "gdrive:full",
            "archive_depth": 0,
            "base": object_at(first / "manifest.json")["base"],
            "consumable_intervals": ["1d", "1m", "5m"],
        }
    )
    child = bundle(b2, second, reference=parent)
    child.update(
        {
            "remote": "gdrive:delta",
            "archive_depth": 1,
            "base": parent["base"],
            "consumable_intervals": ["1d", "1m", "5m"],
        }
    )
    for directory, report in ((b1, parent), (b2, child)):
        atomic(directory / "archive-manifest.json", report)
        atomic(directory / "CLOUD_READY.json", {**report, "status": "cloud_archive_verified"})
        (directory / "SHA256SUMS").write_text(
            "".join(
                f"{file_digest(p)[7:]}  {p.name}\n"
                for p in sorted(directory.iterdir())
                if p.name not in {"SHA256SUMS", "CLOUD_READY.json"}
            )
        )
    cloud = {"gdrive:full": b1, "gdrive:delta": b2}
    if fault == "ancestor":
        cloud.pop("gdrive:full")
    elif fault == "sidecar":
        (b1 / "source-verification.json").unlink()
    elif fault == "checksum":
        (b1 / "source-pointer.json").write_text('{"changed":"evidence"}')
    visited = []

    def fake(args, **kwargs):
        if args[0] == "lsjson":
            remote = args[1]
            visited.append(remote)
            if remote not in cloud:
                raise archive.ArchiveError("cloud_dependency_missing")
            return json.dumps(
                [
                    {
                        "Name": p.name,
                        "Size": p.stat().st_size,
                        "Hashes": {
                            "MD5": hashlib.md5(p.read_bytes(), usedforsecurity=False).hexdigest()
                        },
                    }
                    for p in cloud[remote].iterdir()
                ]
            )
        if args[0] == "cat":
            remote, name = args[1].rsplit("/", 1)
            return (cloud[remote] / name).read_text()
        pytest.fail("unexpected cloud mutation during dependency verification")

    monkeypatch.setattr(archive, "rclone", fake)
    if fault:
        with pytest.raises(archive.ArchiveError, match="cloud_dependency"):
            archive.verify_cloud_dependencies(child, {})
    else:
        archive.verify_cloud_dependencies(child, {})
    assert visited == ["gdrive:delta", "gdrive:full"]


@pytest.mark.parametrize("fault", ["marker", "size", "md5"])
def test_unverified_cloud_dependency_never_uploads_new_delta(
    tmp_path: Path, monkeypatch, fault: str
):
    reference = {
        "remote": "gdrive:synthetic-parent",
        "archive_name": "archive.tar.zst",
        "manifest_sha256": "manifest",
        "content_digest": "content",
        "archive_sha256": "archive",
        "archive_policy_sha256": "policy",
        "archive_md5": "md5",
        "archive_bytes": 10,
    }
    calls = []

    def fake(args, **kwargs):
        calls.append(args[0])
        if args[0] == "cat":
            return json.dumps(
                {**reference, "content_digest": "wrong"} if fault == "marker" else reference
            )
        if args[0] == "lsjson":
            return json.dumps(
                [
                    {
                        "Name": reference["archive_name"],
                        "Size": 11 if fault == "size" else 10,
                        "Hashes": {"MD5": "wrong" if fault == "md5" else "md5"},
                    }
                ]
            )
        pytest.fail("unverified dependency reached copy")

    monkeypatch.setattr(archive, "rclone", fake)
    with pytest.raises(archive.ArchiveError, match="cloud_dependency"):
        archive.transfer(
            tmp_path / "upload",
            {"archive_reference": reference},
            tmp_path / "config",
            object_at(archive.POLICY_PATH),
            job_until=archive.time.monotonic() + 10,
        )
    assert "copy" not in calls and "copyto" not in calls


def test_freeze_packs_only_changed_files_and_restores_original_native_product(
    tmp_path: Path, monkeypatch
):
    if not shutil.which("zstd") or "GNU tar" not in subprocess.check_output(
        ["tar", "--version"], text=True
    ):
        pytest.skip("GNU tar integration runs on CN01")
    root = tmp_path / "source"
    root.mkdir()
    old, new = root / "old", root / "new"
    source(old, revision="a" * 40)
    manifest = source(new, revision="b" * 40, changed=b"updated daily")
    parent_bundle = tmp_path / "parent-bundle"
    parent = bundle(parent_bundle, old)
    parent.update({"remote": "gdrive:synthetic-parent", "archive_depth": 0})
    state = tmp_path / "state"
    state.mkdir()
    receipt = {
        "base": manifest["base"],
        "consumable_intervals": ["1d", "1m", "5m"],
        "minute_quality_grades": {"1m": {"R3": 0}, "5m": {"R3": 0}},
        "code_revision": "b" * 40,
        "manifest_sha256": file_digest(new / "manifest.json"),
        "content_digest": manifest["content_digest"],
    }
    monkeypatch.setattr(archive, "admitted", lambda *a: (new, receipt, receipt))
    monkeypatch.setattr(
        archive,
        "delta_reference",
        lambda *a: (parent, {x["path"]: x for x in object_at(old / "manifest.json")["files"]}),
    )
    upload, report = archive.freeze(
        root, date(2026, 10, 13), state, "b" * 40, object_at(archive.POLICY_PATH)
    )
    restored = tmp_path / "restored"
    delta.restore_bundles([parent_bundle, upload], restored, report["manifest_sha256"])
    assert report["archive_depth"] == 1
    assert [x["path"] for x in report["inherited_files"]] == ["common-finance.txt"]
    assert (restored / "daily.txt").read_bytes() == b"updated daily"


def test_incremental_native_complete_roundtrip_keeps_semantic_admission(
    native_complete: Path, tmp_path: Path
):
    if not shutil.which("zstd") or "GNU tar" not in subprocess.check_output(
        ["tar", "--version"], text=True
    ):
        pytest.skip("native GNU tar integration runs on CN01")
    state = tmp_path / "state"
    state.mkdir()
    policy = {**object_at(archive.POLICY_PATH), "bootstrap_archive_state": str(tmp_path / "empty")}
    parent_upload, parent_report = archive.freeze(
        native_complete, date(2026, 10, 12), state, "c" * 40, policy
    )
    old_package = Path(parent_report["source_package_path"])
    atomic(
        state / "LAST_SUCCESS.json",
        {
            **parent_report,
            "status": "cloud_archive_verified",
            "cloud_ready_confirmed_at": "2026-10-13T16:00:00Z",
        },
    )
    full.build(
        daily=old_package / "members/1d",
        m1=old_package / "members/1m",
        m5=old_package / "members/5m",
        output_root=native_complete,
        code_revision="b" * 40,
    )
    upload, report = archive.freeze(native_complete, date(2026, 10, 12), state, "b" * 40, policy)
    restored = tmp_path / "native-restored"
    delta.restore_bundles([parent_upload, upload], restored, report["manifest_sha256"])
    verification = full.verify(restored)
    assert verification["manifest_sha256"] == report["manifest_sha256"]
    assert verification["minute_quality_grades"] == report["minute_quality_grades"]
    assert report["packed_source_bytes"] < report["source_bytes"] / 2
    assert verification["consumable_intervals"] == ["1d", "1m", "5m"]

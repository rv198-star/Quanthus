"""Disposable TMP reclamation; never age-prune data roots or failed attempts."""

from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from quanthus import p10_container_control as host
from quanthus import p10_tmp as tmp
from tests.unit.test_p10_container_delivery import deployment

NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)


def old_success(root, name="daily-success"):
    with tmp.job(root, name, []) as lease:
        (lease.path / "sort-spill").mkdir()
        (lease.path / "sort-spill/part").write_bytes(b"disposable synthetic spill")
        lease.status = "succeeded"
    marker = lease.path / tmp.MARKER
    value = json.loads(marker.read_bytes())
    value.update(
        started_at=(NOW - timedelta(days=5)).isoformat(),
        finished_at=(NOW - timedelta(days=4)).isoformat(),
    )
    marker.write_text(json.dumps(value))
    return lease.path


def test_dry_run_then_expired_success_only_is_removed(tmp_path):
    root = tmp_path / "tmp"
    root.mkdir()
    source = tmp_path / "source"
    source.mkdir()
    (source / "payload").write_bytes(b"protected synthetic input")
    path = old_success(root)
    unknown = root / "not-ours"
    unknown.mkdir()
    (unknown / "valuable").write_text("keep")
    before = (source / "payload").read_bytes()
    result = tmp.clean(root, [source], now=NOW)
    assert result["eligible"] == [path.name] and result["removed"] == []
    assert path.exists()
    result = tmp.clean(root, [source], apply=True, now=NOW)
    assert result["removed"] == [path.name] and not path.exists()
    assert (unknown / "valuable").read_text() == "keep"
    assert (source / "payload").read_bytes() == before


@pytest.mark.parametrize("status", ["running", "failed", "held"])
def test_failed_interrupted_and_held_jobs_retained_indefinitely(tmp_path, status):
    path = old_success(tmp_path)
    marker = path / tmp.MARKER
    value = json.loads(marker.read_bytes())
    value["status"] = status
    marker.write_text(json.dumps(value))
    assert tmp.clean(tmp_path, [], apply=True, now=NOW)["removed"] == []
    assert (path / "sort-spill/part").is_file()


def test_success_retained_for_full_72_hours(tmp_path):
    path = old_success(tmp_path)
    marker = path / tmp.MARKER
    value = json.loads(marker.read_bytes())
    value["finished_at"] = (NOW - timedelta(hours=71, minutes=59)).isoformat()
    marker.write_text(json.dumps(value))
    assert tmp.clean(tmp_path, [], apply=True, now=NOW)["kept"][0]["reason"] == "retention"
    value["finished_at"] = (NOW - timedelta(hours=72)).isoformat()
    marker.write_text(json.dumps(value))
    assert tmp.clean(tmp_path, [], apply=True, now=NOW)["removed"] == [path.name]


def test_active_lease_overrides_even_expired_success_marker(tmp_path):
    path = old_success(tmp_path)
    with (path / ".lease").open("rb+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = tmp.clean(tmp_path, [], apply=True, now=NOW)
        assert result["removed"] == []
        assert result["kept"][0]["reason"] == "active_lease"
    assert tmp.clean(tmp_path, [], apply=True, now=NOW)["removed"] == [path.name]


@pytest.mark.parametrize("link", ["job", "member", "marker", "lock", "hardlink"])
def test_links_never_lead_cleaner_to_sources(tmp_path, link):
    root = tmp_path / "tmp"
    root.mkdir()
    source = tmp_path / "source"
    source.mkdir()
    member = source / "keep"
    member.write_bytes(b"untouched source")
    path = old_success(root)
    if link == "job":
        (root / "linked-source").symlink_to(source, target_is_directory=True)
    elif link == "member":
        (path / "source").symlink_to(source, target_is_directory=True)
    elif link == "hardlink":
        os.link(member, path / "shared")
    else:
        target = path / (tmp.MARKER if link == "marker" else ".lease")
        target.unlink()
        target.symlink_to(member)
    result = tmp.clean(root, [source], apply=True, now=NOW)
    assert member.read_bytes() == b"untouched source"
    assert list(source.iterdir()) == [member]
    if link != "job":
        assert result["removed"] == []


def test_scan_error_keeps_complete_job(tmp_path, monkeypatch):
    path = old_success(tmp_path)

    def broken_walk(*args, **kwargs):
        kwargs["onerror"](PermissionError("synthetic scan error"))
        return iter(())

    monkeypatch.setattr(tmp.os, "walk", broken_walk)
    assert tmp.clean(tmp_path, [], apply=True, now=NOW)["removed"] == []
    assert (path / "sort-spill/part").is_file()


def test_job_exception_and_repeated_id_preserve_failure_evidence(tmp_path):
    with pytest.raises(RuntimeError), tmp.job(tmp_path, "daily-failed", []) as lease:
        (lease.path / "evidence").write_text("retain")
        lease.status = "succeeded"
        raise RuntimeError("interrupted")
    assert json.loads((lease.path / tmp.MARKER).read_bytes())["status"] == "failed"
    with pytest.raises(FileExistsError), tmp.job(tmp_path, "daily-failed", []):
        pytest.fail("old attempt overwritten")
    assert (lease.path / "evidence").read_text() == "retain"


@pytest.mark.parametrize("overlap", ["source", "runtime", "parent", "state", "symlink"])
def test_tmp_preflight_rejects_overlap_before_outputs(tmp_path, overlap):
    config = deployment(tmp_path)
    env = config["compose_env"]
    if overlap == "source":
        env["QUANTHUS_TMP_ROOT"] = env["QUANTHUS_BASE_ROOT"]
    elif overlap == "runtime":
        env["QUANTHUS_TMP_ROOT"] = env["QUANTHUS_RUNTIME_ROOT"]
    elif overlap == "state":
        env["QUANTHUS_TMP_ROOT"] = config["state"]
    elif overlap == "parent":
        env["QUANTHUS_TMP_ROOT"] = str(tmp_path)
    else:
        link = tmp_path / "linked-tmp"
        link.symlink_to(env["QUANTHUS_TMP_ROOT"], target_is_directory=True)
        env["QUANTHUS_TMP_ROOT"] = str(link)
    path = tmp_path / "deployment.json"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="P10_TMP"):
        host.settings(path)
    assert not list(Path(config["state"]).iterdir())
    assert not list(Path(env["QUANTHUS_BASE_ROOT"]).iterdir())

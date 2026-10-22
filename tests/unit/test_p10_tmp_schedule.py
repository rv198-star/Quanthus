"""Container-owned housekeeping clock and TMP-only deployment boundary."""

from __future__ import annotations

import json
import os
import selectors
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from quanthus import p10_container_control as host
from quanthus import p10_tmp as tmp
from quanthus import p10_tmp_schedule as clock
from tests.unit.test_p10_container_delivery import deployment


@pytest.mark.parametrize(
    ("instant", "expected"),
    [
        ("2026-10-21T19:29:59+00:00", "2026-10-21T03:30:00+08:00"),
        ("2026-10-21T19:30:00+00:00", "2026-10-22T03:30:00+08:00"),
        ("2026-10-22T20:00:00+08:00", "2026-10-22T03:30:00+08:00"),
    ],
)
def test_shanghai_slot_boundary(instant, expected):
    assert clock.slot(datetime.fromisoformat(instant)).isoformat() == expected


def test_restart_catches_up_once_and_clock_rollback_does_not_repeat(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(clock, "clean", lambda *a, **k: calls.append(k) or {"removed": []})
    now = datetime(2026, 10, 22, 12, tzinfo=clock.SHANGHAI)
    assert clock.tick(tmp_path, now)["slot"] == "2026-10-22T03:30:00+08:00"
    assert clock.tick(tmp_path, now + timedelta(hours=1)) is None
    assert clock.tick(tmp_path, now - timedelta(days=1)) is None
    assert clock.tick(tmp_path, now + timedelta(days=1)) is not None
    assert len(calls) == 2 and all(call["apply"] for call in calls)


def test_failed_sweep_does_not_advance_slot(tmp_path, monkeypatch):
    def failure(*args, **kwargs):
        raise PermissionError("synthetic scan failure")

    monkeypatch.setattr(clock, "clean", failure)
    with pytest.raises(PermissionError):
        clock.tick(tmp_path, datetime.now(timezone.utc))
    assert not (tmp_path / clock.STATE).exists()


@pytest.mark.parametrize("member", [clock.STATE, clock.LOCK])
def test_cleaner_state_or_lock_link_never_mutates_other_files(tmp_path, member):
    root = tmp_path / "tmp"
    root.mkdir()
    source = tmp_path / "not-tmp"
    source.write_bytes(b"keep")
    (root / member).symlink_to(source)
    with pytest.raises((ValueError, OSError)):
        if member == clock.STATE:
            clock.tick(root, datetime.now(timezone.utc))
        else:
            clock.schedule(root)
    assert source.read_bytes() == b"keep"


@pytest.mark.parametrize("mode", ["once", "start", "stop"])
@pytest.mark.parametrize("access", ["direct", "sudo-noninteractive"])
def test_host_only_launches_cleanup_container(tmp_path, monkeypatch, mode, access):
    config = deployment(tmp_path)
    config["docker_access"] = access
    calls = []
    monkeypatch.setattr(host.p10_tmp, "clean", lambda *a, **k: pytest.fail("host deletion"))

    def run(command, **kwargs):
        calls.append(command)
        assert command[: len(host.docker_command(config))] == host.docker_command(config)
        assert kwargs["env"].get("DOCKER_HOST") is None
        assert kwargs["env"].get("COMPOSE_DISABLE_ENV_FILE") == "1"
        assert "cleanup" in command
        assert "daily" not in command and "weekly" not in command
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(host.subprocess, "run", run)
    assert host.cleanup_container(config, mode)["mode"] == mode
    assert len(calls) == 1
    if mode == "once":
        assert "--apply" not in calls[0]


def test_compose_cleaner_has_no_data_credentials_network_or_socket_mounts(tmp_path):
    config = deployment(tmp_path)
    env = {**config["compose_env"], "PATH": os.defpath, "COMPOSE_DISABLE_ENV_FILE": "1"}
    env.update(
        QUANTHUS_PIPELINE_IMAGE=config["image"],
        QUANTHUS_EVENT_ROOT=str(tmp_path / "events"),
        QUANTHUS_MAIL_ROOT=str(tmp_path / "mail"),
        QUANTHUS_JOB_TMP=str(tmp_path / "one-job"),
    )
    command = [
        "docker",
        "compose",
        "--profile",
        "*",
        "--file",
        config["compose"],
        "config",
        "--format",
        "json",
    ]
    value = json.loads(subprocess.check_output(command, env=env, text=True))
    assert set(value["services"]) == {"daily", "recovery", "weekly", "cleanup", "backup"}
    cleaner = value["services"]["cleanup"]
    assert cleaner["image"] == value["services"]["daily"]["image"]
    assert cleaner["network_mode"] == "none" and cleaner["read_only"]
    assert "QUANTHUS_RESEND_API_KEY" not in cleaner["environment"]
    # Compose config serializes dollar signs escaped for a reusable config.
    # Actual raw-env runtime injection is separately exercised in the container probe.
    for role in ("daily", "recovery", "weekly", "backup"):
        assert (
            value["services"][role]["environment"]["QUANTHUS_RESEND_API_KEY"]
            == "synthetic-$$literal-key"
        )
    assert cleaner["cap_drop"] == ["ALL"]
    assert cleaner["profiles"] == ["maintenance"]
    assert cleaner["entrypoint"] == ["python", "-m", "quanthus.p10_tmp_schedule"]
    assert cleaner["command"] == ["schedule", "--root", "/work/tmp", "--apply"]
    assert [(v["source"], v["target"]) for v in cleaner["volumes"]] == [
        (config["compose_env"]["QUANTHUS_TMP_ROOT"], "/work/tmp")
    ]
    assert len({v["image"] for v in value["services"].values()}) == 1
    backup = value["services"]["backup"]
    assert backup["profiles"] == ["delivery"]
    assert backup["network_mode"] == "host"
    assert backup["environment"]["HTTPS_PROXY"] == "http://127.0.0.1:10800"
    mounted = {v["target"]: v for v in backup["volumes"]}
    assert all(
        mounted[p]["read_only"]
        for p in (
            config["compose_env"]["QUANTHUS_RUNTIME_ROOT"],
            "/data/weekly-jobs",
            "/data/weekly-input",
            "/run/events",
            "/run/config/backup.json",
        )
    )
    assert "/data/base" not in mounted
    assert all("docker.sock" not in v["source"] for v in backup["volumes"])


def test_new_process_catchup_singleton_and_sigterm(tmp_path):
    root = tmp_path / "tmp"
    root.mkdir()
    with tmp.job(root, "daily-complete", []) as lease:
        (lease.path / "spill").write_bytes(b"synthetic disposable bytes")
        lease.status = "succeeded"
    marker = lease.path / tmp.MARKER
    value = json.loads(marker.read_bytes())
    now = datetime.now(timezone.utc)
    value.update(
        started_at=(now - timedelta(days=5)).isoformat(),
        finished_at=(now - timedelta(days=4)).isoformat(),
    )
    marker.write_text(json.dumps(value))
    source = tmp_path / "source"
    source.write_bytes(b"unchanged synthetic source")
    # Child uses production scheduling code, with all source networking denied.
    probe = """
import sys
def deny(event,args):
    if event in {'socket.connect','socket.connect_ex','socket.getaddrinfo'}:
        raise RuntimeError('SOURCE_NETWORK_FORBIDDEN')
sys.addaudithook(deny)
from quanthus.p10_tmp_schedule import main
main()
"""
    command = [sys.executable, "-c", probe, "schedule", "--root", str(root), "--apply"]
    child_env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[2] / "src")}
    first = subprocess.Popen(
        command, env=child_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        with selectors.DefaultSelector() as ready:
            ready.register(first.stdout, selectors.EVENT_READ)
            assert ready.select(timeout=10), "cleaner failed to complete first sweep"
        result = json.loads(first.stdout.readline())
        assert result["removed"] == ["daily-complete"]
        second = subprocess.run(
            command, env=child_env, capture_output=True, text=True, timeout=5, check=True
        )
        assert json.loads(second.stdout)["status"] == "cleaner_already_active"
        first.terminate()
        first.communicate(timeout=5)
        assert first.returncode == 0
        # Restart in the same slot neither deletes again nor rewrites the persisted receipt.
        before = (root / clock.STATE).read_bytes()
        assert clock.tick(root, datetime.now(timezone.utc)) is None
        assert (root / clock.STATE).read_bytes() == before
        assert source.read_bytes() == b"unchanged synthetic source"
    finally:
        if first.poll() is None:
            first.kill()
            first.communicate()

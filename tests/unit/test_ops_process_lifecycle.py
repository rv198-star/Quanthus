from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace

import pytest

from quanthus.pipelines import process_control

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(os.name != "posix", reason="ODS runtime requires POSIX")


@pytest.fixture
def orchestrator():
    name = "ods_orchestrator_process_tests"
    loader = importlib.machinery.SourceFileLoader(name, str(ROOT / "ops/bin/run-cn01-ods-daily"))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    yield module
    sys.modules.pop(name, None)


def _wait_for(path: Path, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.stat().st_size:
            return
        time.sleep(0.01)
    raise AssertionError(f"child did not become ready: {path}")


def _pipeline(root: Path, *, ignore_term: bool = False, background: bool = False) -> list[str]:
    root.mkdir(parents=True, exist_ok=True)
    child = root / "child.py"
    child.write_text(
        "import os, signal, sys, time\n"
        "from pathlib import Path\n"
        "root = Path(sys.argv[1])\n"
        + ("signal.signal(signal.SIGTERM, signal.SIG_IGN)\n" if ignore_term else "")
        + "(root / 'child.pid').write_text(str(os.getpid()))\n"
        "while not (root / 'release').exists(): time.sleep(0.01)\n"
        "(root / 'late-write').write_text('old attempt still running')\n",
        encoding="utf-8",
    )
    script = '"$1" "$2" "$3" | tee "$4"'
    if background:
        script += ' & while [ ! -s "$3/child.pid" ]; do sleep 0.01; done'
    return [
        "bash",
        "-c",
        script,
        "lane",
        sys.executable,
        str(child),
        str(root),
        str(root / "tee.log"),
    ]


def _release_and_assert_stopped(root: Path) -> None:
    (root / "release").touch()
    time.sleep(0.2)
    assert not (root / "late-write").exists(), "a descendant wrote after cleanup returned"


def _cleanup_test_child(root: Path) -> None:
    # Exact PID belongs to this fixture only; never signal the test runner's group.
    path = root / "child.pid"
    if path.exists() and not (root / "late-write").exists():
        with suppress(ProcessLookupError):
            os.kill(int(path.read_text()), signal.SIGKILL)


@pytest.mark.parametrize("ignore_term", [False, True])
def test_lane_termination_stops_pipeline_descendants(tmp_path, orchestrator, ignore_term):
    job = orchestrator.LaneJob(
        "lane", _pipeline(tmp_path, ignore_term=ignore_term), tmp_path / "logs"
    )
    job.termination_grace_seconds = 0.1
    try:
        job.start()
        _wait_for(tmp_path / "child.pid")
        job.terminate()
        _release_and_assert_stopped(tmp_path)
        assert job.process is None
        assert job.log_handle is None
    finally:
        _cleanup_test_child(tmp_path)
        job.terminate()


def test_lane_cleanup_handles_already_exited_parent(tmp_path, orchestrator):
    job = orchestrator.LaneJob("lane", _pipeline(tmp_path, background=True), tmp_path / "logs")
    job.termination_grace_seconds = 0.1
    try:
        job.start()
        _wait_for(tmp_path / "child.pid")
        job.process.wait(timeout=5)
        job.terminate()
        _release_and_assert_stopped(tmp_path)
    finally:
        _cleanup_test_child(tmp_path)
        job.terminate()


def test_lane_poll_cleans_descendants_before_returning_done(tmp_path, orchestrator):
    job = orchestrator.LaneJob("lane", _pipeline(tmp_path, background=True), tmp_path / "logs")
    job.termination_grace_seconds = 0.1
    try:
        job.start()
        _wait_for(tmp_path / "child.pid")
        job.process.wait(timeout=5)
        assert job.poll() == ("done", 0)
        _release_and_assert_stopped(tmp_path)
    finally:
        _cleanup_test_child(tmp_path)
        job.terminate()


def test_expired_group_never_starts_a_lane(tmp_path, orchestrator):
    job = orchestrator.LaneJob("lane", [sys.executable, "-c", "pass"], tmp_path)
    result = orchestrator._run_group([job], deadline=time.monotonic() - 1, poll_seconds=0.01)
    assert result["lane"]["status"] == "sla_exceeded"
    assert result["lane"]["attempts"] == 0
    assert result["lane"]["logs"] == []


def test_catalog_timeout_stops_pipeline_descendants(tmp_path, orchestrator):
    try:
        rc, _ = orchestrator._run_command(
            _pipeline(tmp_path), log_path=tmp_path / "catalog.log", timeout=1
        )
        assert rc == 124
        _wait_for(tmp_path / "child.pid")
        _release_and_assert_stopped(tmp_path)
    finally:
        _cleanup_test_child(tmp_path)


def test_start_failure_closes_its_log(tmp_path, orchestrator):
    job = orchestrator.LaneJob("lane", [str(tmp_path / "missing")], tmp_path / "logs")
    with pytest.raises(OSError):
        job.start()
    assert job.process is None
    assert job.log_handle is None


def test_group_start_failure_cleans_other_started_lanes(tmp_path, orchestrator):
    active_root = tmp_path / "active"
    active = orchestrator.LaneJob("active", _pipeline(active_root), tmp_path / "logs")
    missing = orchestrator.LaneJob("missing", [str(tmp_path / "missing")], tmp_path / "logs")
    original_start = active.start

    def start_and_wait():
        original_start()
        _wait_for(active_root / "child.pid")

    active.start = start_and_wait
    try:
        with pytest.raises(OSError):
            orchestrator._run_group(
                [active, missing], deadline=time.monotonic() + 5, poll_seconds=0.01
            )
        _release_and_assert_stopped(active_root)
    finally:
        _cleanup_test_child(active_root)
        active.terminate()


def test_unconfirmed_group_cleanup_is_bounded_and_fails_closed(monkeypatch):
    signals = []
    process = SimpleNamespace(pid=2147483647, poll=lambda: 0)
    monkeypatch.setattr(process_control, "_group_exists", lambda _pgid: True)
    monkeypatch.setattr(
        process_control, "_signal_group", lambda pid, sig: signals.append((pid, sig))
    )
    with pytest.raises(process_control.ProcessGroupCleanupError, match="remains after SIGKILL"):
        process_control.terminate_process_group(process, grace_seconds=0, kill_wait_seconds=0)
    assert signals == [(process.pid, signal.SIGTERM), (process.pid, signal.SIGKILL)]


def test_permission_error_is_not_proof_of_group_absence(monkeypatch):
    def denied(*_args):
        raise PermissionError("cannot inspect")

    monkeypatch.setattr(process_control.os, "killpg", denied)
    assert process_control._group_exists(2147483647) is True


def test_cleanup_refuses_the_callers_group():
    process = SimpleNamespace(pid=os.getpgrp())
    with pytest.raises(process_control.ProcessGroupCleanupError, match="non-owned"):
        process_control.terminate_process_group(process)


def test_group_cleanup_failure_does_not_skip_other_groups(monkeypatch, orchestrator):
    visited = []

    def first():
        visited.append("first")
        raise process_control.ProcessGroupCleanupError("unconfirmed first")

    def fail_work(*_args, **_kwargs):
        raise RuntimeError("work failed")

    monkeypatch.setattr(orchestrator, "_run_group_owned", fail_work)
    with pytest.raises(process_control.ProcessGroupCleanupError, match="unconfirmed first"):
        orchestrator._run_group(
            [
                SimpleNamespace(terminate=first),
                SimpleNamespace(terminate=lambda: visited.append("second")),
            ],
            deadline=time.monotonic() + 1,
            poll_seconds=0.01,
        )
    assert visited == ["first", "second"]


def test_command_cleanup_failure_has_nonretryable_status(tmp_path, monkeypatch, orchestrator):
    def unconfirmed(_process):
        raise process_control.ProcessGroupCleanupError("synthetic cleanup failure")

    monkeypatch.setattr(orchestrator, "terminate_process_group", unconfirmed)
    log = tmp_path / "command.log"
    rc, _ = orchestrator._run_command([sys.executable, "-c", "pass"], log_path=log, timeout=5)
    assert rc == 75
    assert "synthetic cleanup failure" in log.read_text()


def test_retry_wrapper_never_retries_unconfirmed_cleanup(tmp_path):
    calls = tmp_path / "calls"
    runner = tmp_path / "runner.py"
    runner.write_text(
        "from pathlib import Path\n"
        f"with Path({str(calls)!r}).open('a') as f: f.write('attempt\\n')\n"
        "raise SystemExit(75)\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [str(ROOT / "ops/bin/run-ods-daily-with-retry"), sys.executable, str(runner)],
        env={
            **os.environ,
            "QUANTHUS_ODS_DAILY_MAX_ATTEMPTS": "2",
            "QUANTHUS_ODS_DAILY_RETRY_DELAY_SECONDS": "0",
        },
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert completed.returncode == 75
    assert calls.read_text() == "attempt\n"
    assert "automatic retry prohibited" in completed.stderr


def test_cleanup_failure_preserves_failed_run_without_ready(
    tmp_path, monkeypatch, orchestrator, capsys
):
    def unconfirmed(*_args, **_kwargs):
        raise process_control.ProcessGroupCleanupError("synthetic group still present")

    shadow = tmp_path / "shadow"
    for key, value in {
        "HERMES_ODS_NODE_ID": "cn01",
        "HERMES_ODS_ENV_FILE": str(tmp_path / "absent.env"),
        "HERMES_ODS_SHADOW_ROOT": str(shadow),
        "HERMES_ODS_RELEASE_ROOT": str(shadow / "releases"),
        "ASTOCK_HOME": str(tmp_path / "home"),
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(sys, "argv", ["orchestrator", "--market-date", "2026-09-21"])
    monkeypatch.setattr(orchestrator, "_run_group", unconfirmed)
    assert orchestrator.main() == 75
    state = json.loads(capsys.readouterr().out)
    assert state["status"] == "failed"
    assert state["error_code"] == "process_cleanup_unconfirmed"
    assert state["dwd"]["status"] == "blocked_by_lane_failure"
    runs = list(shadow.glob("orchestrator/daily/2026-09-21/*/run.json"))
    assert len(runs) == 1
    assert not (runs[0].parent / "READY").exists()
    assert json.loads(runs[0].read_text())["status"] == "failed"
    context = json.loads((shadow / "orchestrator/daily/notification-context.json").read_text())
    assert context["error_code"] == "process_cleanup_unconfirmed"

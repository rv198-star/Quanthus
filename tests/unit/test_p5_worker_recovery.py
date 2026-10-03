from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time

import pytest

from quanthus.stage_learning.p5_worker_recovery import BUSY, run_once
from quanthus.stage_learning.worker import (
    P5_SYNTHETIC_JOB,
    StageJob,
    StageWorker,
    StageWorkerError,
    StageWorkerPaths,
    cancel_job,
    submit_job,
)


def fixture(tmp_path):
    paths = StageWorkerPaths(tmp_path / "training", tmp_path / "repo", tmp_path / "key")
    submission = submit_job(paths, code_revision="a" * 40, kind=P5_SYNTHETIC_JOB)
    queued = paths.queue / (submission["job_id"] + ".json")
    job = StageJob.from_dict(json.loads(queued.read_text()))
    return StageWorker(paths), job, queued


def orphan(worker, job, queued):
    root = worker.paths.control_root / "p5-synthetic-leases"
    root.mkdir(parents=True)
    lease = root / queued.name
    lease.write_text(json.dumps({"schema_version": "quanthus.p5-synthetic-lease.v1",
                                 "job": job.to_dict()}))
    queued.rename(worker.paths.claimed / queued.name)
    return lease


def test_legacy_claim_is_not_recovered(tmp_path, monkeypatch):
    worker, job, queued = fixture(tmp_path)
    queued.rename(worker.paths.claimed / queued.name)
    monkeypatch.setattr(worker, "execute", lambda *a, **k: pytest.fail("legacy replay"))
    assert worker.run_once() is None
    assert (worker.paths.claimed / queued.name).exists()


@pytest.mark.parametrize("stage", [None, "preparing"])
def test_never_started_claim_keeps_job_and_seed_identity(tmp_path, monkeypatch, stage):
    worker, job, queued = fixture(tmp_path)
    orphan(worker, job, queued)
    if stage:
        worker._write_status(job, state="running", stage=stage)
        (worker.paths.runs / job.job_id).mkdir()
    observed = []

    def execute(value, *, claim_lock_fd):
        observed.append(value.to_dict())
        assert os.fstat(claim_lock_fd).st_ino > 0
        return worker._write_status(value, state="succeeded", stage="terminal")

    monkeypatch.setattr(worker, "execute", execute)
    assert run_once(worker)["state"] == "succeeded"
    assert observed == [job.to_dict()]
    assert worker.run_once() is None
    assert submit_job(worker.paths, code_revision=job.code_revision,
                      kind=P5_SYNTHETIC_JOB)["formal_seed_budget_consumed"] == 0


@pytest.mark.parametrize("verified", [True, False])
def test_started_orphan_verifies_without_rerun_and_preserves_evidence(
    tmp_path, monkeypatch, verified,
):
    worker, job, queued = fixture(tmp_path)
    orphan(worker, job, queued)
    previous = worker._write_status(job, state="running", stage="executing")
    artifact = worker.paths.runs / job.job_id / "p5-synthetic-v1"
    artifact.mkdir(parents=True)
    payload = artifact / "summary.json"
    payload.write_text("retained evidence")
    monkeypatch.setattr(worker, "execute", lambda *a, **k: pytest.fail("replayed fit"))
    monkeypatch.setattr(worker, "_prepare_worktree", lambda job: tmp_path / "worktree")
    monkeypatch.setattr(worker, "_remove_worktree", lambda path: None)

    def verify(value, run_root, worktree, *, claim_lock_fd):
        assert value == job
        assert os.fstat(claim_lock_fd).st_ino > 0
        if not verified:
            raise ValueError("incomplete manifest")
        return {"manifest_sha256": "sha256:" + "b" * 64}

    monkeypatch.setattr(worker, "_verify_result", verify)
    result = run_once(worker)
    assert result["state"] == ("succeeded" if verified else "failed")
    assert result["exit_code"] is None
    assert payload.read_text() == "retained evidence"
    history = worker.paths.control_root / "p5-synthetic-recovery" / queued.name
    assert json.loads(history.read_text())["prior_status"] == previous
    assert run_once(worker) is None


def test_terminal_claim_and_pending_cancel_never_execute(tmp_path, monkeypatch):
    worker, job, queued = fixture(tmp_path)
    orphan(worker, job, queued)
    previous = worker._write_status(job, state="succeeded", stage="terminal")
    monkeypatch.setattr(worker, "execute", lambda *a, **k: pytest.fail("terminal replay"))
    assert run_once(worker) == previous


def test_orphan_cancel_has_priority_over_verification(tmp_path, monkeypatch):
    worker, job, queued = fixture(tmp_path)
    orphan(worker, job, queued)
    (worker.paths.cancel / job.job_id).touch()
    monkeypatch.setattr(worker, "_prepare_worktree", lambda *a: pytest.fail("canceled verify"))
    assert run_once(worker)["state"] == "canceled"


@pytest.mark.parametrize("corruption", ["lease", "status", "claim_filename", "{}", "[]",
                                       '{"kind":"other"}'])
def test_identity_drift_preserves_claim_and_fails_closed(tmp_path, corruption):
    worker, job, queued = fixture(tmp_path)
    lease = orphan(worker, job, queued)
    if corruption == "lease":
        value = json.loads(lease.read_text())
        value["job"]["code_revision"] = "b" * 40
        lease.write_text(json.dumps(value))
    elif corruption == "status":
        worker._write_status(job, state="running", stage="executing")
        status = worker.paths.status / queued.name
        value = json.loads(status.read_text())
        value["input_digest"] = "sha256:" + "0" * 64
        status.write_text(json.dumps(value))
    elif corruption == "claim_filename":
        (worker.paths.claimed / queued.name).rename(
            worker.paths.claimed / ("stage_" + "b" * 32 + ".json"))
    else:
        (worker.paths.claimed / queued.name).write_text(corruption)
    with pytest.raises(StageWorkerError, match="identity differs"):
        run_once(worker)
    assert len(list(worker.paths.claimed.glob("*.json"))) == 1


def test_child_inherited_lease_blocks_recovery_after_parent_closes(tmp_path, monkeypatch):
    worker, job, queued = fixture(tmp_path)
    orphan(worker, job, queued)
    guard = worker.paths.control_root / "p5-synthetic-leases" / "worker.lock"
    lock = guard.open("a+")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; print('ready',flush=True); sys.stdin.read()"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, pass_fds=(lock.fileno(),),
    )
    try:
        assert child.stdout.readline().strip() == "ready"
        lock.close()  # Exactly the fd lifetime after supervisor death; child still owns it.
        monkeypatch.setattr(worker, "execute", lambda *a, **k: pytest.fail("live child replay"))
        assert run_once(worker) is BUSY
        monkeypatch.setattr(worker, "_claim_next", lambda: pytest.fail("other job with live child"))
        assert worker.run_once() is None
        assert (worker.paths.claimed / queued.name).exists()
        child.communicate("", timeout=5)
        monkeypatch.setattr(worker, "execute", lambda value, **kw: {"state": "succeeded"})
        assert run_once(worker)["state"] == "succeeded"
    finally:
        lock.close()
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


def test_generic_claim_path_skips_busy_p5(tmp_path):
    worker, job, queued = fixture(tmp_path)
    assert worker._claim_next() is None
    assert queued.exists()


def test_queued_terminal_is_not_overwritten(tmp_path, monkeypatch):
    worker, job, queued = fixture(tmp_path)
    prior = worker._write_status(job, state="failed", stage="terminal", error="original failure")
    monkeypatch.setattr(worker, "execute", lambda *a, **k: pytest.fail("terminal replay"))
    assert run_once(worker) == prior
    assert not queued.exists()


def test_cancel_during_verification_wins_terminal_commit(tmp_path, monkeypatch):
    worker, job, queued = fixture(tmp_path)
    orphan(worker, job, queued)
    worker._write_status(job, state="running", stage="verifying")
    (worker.paths.runs / job.job_id).mkdir()
    monkeypatch.setattr(worker, "_prepare_worktree", lambda job: tmp_path)
    monkeypatch.setattr(worker, "_remove_worktree", lambda path: None)

    def verify(*args, **kwargs):
        assert cancel_job(worker.paths, job.job_id)["cancel_requested"] is True
        return {"verified": True}

    monkeypatch.setattr(worker, "_verify_result", verify)
    assert run_once(worker)["state"] == "canceled"
    with pytest.raises(StageWorkerError, match="terminal"):
        cancel_job(worker.paths, job.job_id)


def test_cancel_after_final_check_before_commit_still_wins(tmp_path):
    worker, job, queued = fixture(tmp_path)
    assert cancel_job(worker.paths, job.job_id)["cancel_requested"] is True
    result = worker._write_status(job, state="succeeded", stage="terminal",
                                  result_ref={"verified": True})
    assert result["state"] == "canceled"
    assert result["result_ref"] is None


@pytest.mark.parametrize("phase", ["execute", "git"])
def test_real_supervisor_kill_retains_claim_and_child_guard(tmp_path, monkeypatch, phase):
    worker, job, queued = fixture(tmp_path)
    release = tmp_path / "release-child"
    script = """
import subprocess, sys
from pathlib import Path
from quanthus.stage_learning.worker import StageWorker, StageWorkerPaths
worker = StageWorker(StageWorkerPaths(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])))
def execute(job, *, claim_lock_fd):
    if sys.argv[5] == 'git':
        from quanthus.stage_learning import worker as module
        real_run = module.subprocess.run
        def fake_git(command, **kwargs):
            code = ("import sys,time; from pathlib import Path; "
                    "print('child-ready',flush=True); deadline=time.monotonic()+10; "
                    "exec('while not Path(sys.argv[1]).exists() and "
                    "time.monotonic()<deadline: time.sleep(.01)')")
            kwargs.pop('capture_output', None)
            return real_run([sys.executable, '-c', code, sys.argv[4]], **kwargs)
        module.subprocess.run = fake_git
        worker._git('worktree', 'add', 'synthetic-only')
        return
    child = subprocess.Popen([sys.executable, '-c',
        "import sys,time; from pathlib import Path; print('ready',flush=True); "
        "deadline=time.monotonic()+10; "
        "exec('while not Path(sys.argv[1]).exists() and "
        "time.monotonic()<deadline: time.sleep(.01)')",
        sys.argv[4]], stdout=subprocess.PIPE, text=True, pass_fds=(claim_lock_fd,))
    assert child.stdout.readline().strip() == 'ready'
    print('child-ready', flush=True)
    child.wait()
worker.execute = execute
worker.run_once()
"""
    parent = subprocess.Popen(
        [sys.executable, "-c", script, str(worker.paths.training_root),
         str(worker.paths.control_repo), str(worker.paths.git_key), str(release), phase],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert parent.stdout.readline().strip() == "child-ready"
        parent.kill()
        parent.wait(timeout=5)
        monkeypatch.setattr(worker, "_claim_next", lambda: pytest.fail("new ordinary job"))
        monkeypatch.setattr(worker, "execute", lambda *a, **k: pytest.fail("new P5 job"))
        assert worker.run_once() is None
        assert (worker.paths.claimed / queued.name).exists()
        release.touch()
        monkeypatch.setattr(worker, "execute", lambda job, **kw: {"state": "succeeded"})
        deadline = time.monotonic() + 5
        result = run_once(worker)
        while result is BUSY and time.monotonic() < deadline:
            time.sleep(.01)
            result = run_once(worker)
        assert result == {"state": "succeeded"}
    finally:
        release.touch()
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=5)


def test_bad_legacy_claim_does_not_block_p5_and_bad_queue_keeps_old_disposition(
    tmp_path, monkeypatch,
):
    worker, job, queued = fixture(tmp_path)
    legacy_claim = worker.paths.claimed / ("stage_" + "0" * 32 + ".json")
    legacy_claim.write_text("{broken")
    monkeypatch.setattr(worker, "execute", lambda *a, **kw: {"state": "succeeded"})
    assert run_once(worker)["state"] == "succeeded"
    assert legacy_claim.exists()
    bad_queue = worker.paths.queue / ("stage_" + "0" * 32 + ".json")
    bad_queue.write_text("{broken")
    with pytest.raises(StageWorkerError, match="cannot read"):
        worker.run_once()
    assert not bad_queue.exists()


@pytest.mark.parametrize("exit_confirmed", [True, False])
def test_exception_with_live_executor_never_deletes_its_worktree(
    tmp_path, monkeypatch, exit_confirmed,
):
    from quanthus.stage_learning import worker as module

    worker, job, queued = fixture(tmp_path)
    stopped = []
    removed = []

    class Process:
        def poll(self):
            return -15 if stopped else None

    def terminate(process):
        if not exit_confirmed:
            raise RuntimeError("ownership could not be confirmed")
        stopped.append(True)

    monkeypatch.setattr(worker, "_prepare_worktree", lambda job: tmp_path / "worktree")
    monkeypatch.setattr(worker, "_remove_worktree", lambda path: removed.append(path))
    monkeypatch.setattr(worker, "_terminate", terminate)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: Process())
    monkeypatch.setattr(module.time, "sleep", lambda t: (_ for _ in ()).throw(ValueError("crash")))
    if exit_confirmed:
        assert run_once(worker)["state"] == "failed"
        assert stopped and removed
        assert not (worker.paths.claimed / queued.name).exists()
    else:
        with pytest.raises(StageWorkerError, match="ownership unverified"):
            run_once(worker)
        assert not stopped and not removed
        assert (worker.paths.claimed / queued.name).exists()
        assert json.loads((worker.paths.status / queued.name).read_text())["state"] == "running"


def test_failed_terminal_writes_preserve_cancel_until_committed(tmp_path, monkeypatch):
    from quanthus.stage_learning import worker as module

    worker, job, queued = fixture(tmp_path)
    monkeypatch.setattr(worker, "_prepare_worktree", lambda job: tmp_path)
    monkeypatch.setattr(worker, "_remove_worktree", lambda path: None)
    assert cancel_job(worker.paths, job.job_id)["cancel_requested"] is True
    original = module._atomic_json

    def fail_terminal(path, value):
        if path.parent == worker.paths.status and value.get("stage") == "terminal":
            raise OSError("simulated disk failure")
        original(path, value)

    monkeypatch.setattr(module, "_atomic_json", fail_terminal)
    with pytest.raises(OSError, match="disk failure"):
        run_once(worker)
    assert (worker.paths.cancel / job.job_id).exists()
    assert (worker.paths.claimed / queued.name).exists()
    monkeypatch.setattr(module, "_atomic_json", original)
    assert run_once(worker)["state"] == "canceled"
    assert not (worker.paths.cancel / job.job_id).exists()

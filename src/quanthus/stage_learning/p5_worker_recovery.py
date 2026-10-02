"""Recover only leased synthetic P5 claims; never retry an interrupted fit.

The persistent worker flock inode is inherited by the executor and verifier. Acquiring it
proves those cooperating processes have exited, without PID reuse or age guesses.
Legacy claims without this protocol are deliberately left for explicit diagnosis.
"""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from quanthus.stage_learning.worker import StageJob, StageWorker

BUSY = object()


@contextmanager
def terminal_lock(paths: Any, job_id: str) -> Any:
    root = paths.control_root / "p5-synthetic-decisions"
    root.mkdir(parents=True, exist_ok=True)
    with (root / f"{job_id}.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def commit_terminal(worker: StageWorker, job: StageJob, value: dict[str, Any]) -> dict[str, Any]:
    from quanthus.stage_learning.worker import _atomic_json

    # The final state commit and cancel admission have one linearization lock.
    with terminal_lock(worker.paths, job.job_id):
        if (worker.paths.cancel / job.job_id).exists() and value["state"] != "canceled":
            value.update(state="canceled", error="cancel_requested_before_p5_terminal_commit",
                         result_ref=None)
        _atomic_json(worker.paths.status / f"{job.job_id}.json", value)
    return value


def _recover(worker: StageWorker, job: StageJob, fd: int) -> dict[str, Any]:
    from quanthus.stage_learning.worker import StageWorkerError, _atomic_json, _utc_now

    paths = worker.paths
    status_path = paths.status / f"{job.job_id}.json"
    prior = json.loads(status_path.read_text()) if status_path.exists() else None
    if prior is not None:
        if any(prior.get(key) != value for key, value in {
            "job_id": job.job_id, "kind": job.kind, "code_revision": job.code_revision,
            "input_digest": job.input_digest,
        }.items()):
            raise StageWorkerError("P5 orphan status identity differs")
        if prior.get("state") in {"succeeded", "failed", "canceled"}:
            return prior
        if prior.get("state") != "running":
            raise StageWorkerError("P5 orphan status is not recoverable")
    # Preserve the pre-recovery state before a terminal update, including across
    # a second crash in recovery itself. This file never replaces the job identity.
    history = paths.control_root / "p5-synthetic-recovery"
    history.mkdir(parents=True, exist_ok=True)
    receipt = history / f"{job.job_id}.json"
    if not receipt.exists():
        _atomic_json(receipt, {
            "schema_version": "quanthus.p5-synthetic-orphan-recovery.v1",
            "job": job.to_dict(), "prior_status": prior, "observed_at": _utc_now(),
            "formal_seed_budget_consumed": 0, "market_rows_read": 0,
        })
    started_at = None if prior is None else prior.get("started_at")
    cancel = paths.cancel / job.job_id
    if cancel.exists() or worker.stopping:
        return worker._write_status(
            job, state="canceled", stage="terminal", started_at=started_at,
            completed_at=_utc_now(), error="cancel_requested_during_p5_orphan_recovery",
        )
    run_root = paths.runs / job.job_id
    log = paths.logs / f"{job.job_id}.log"
    if not run_root.exists() and not log.exists() and (
        prior is None or prior.get("stage") == "preparing"
    ):
        return worker.execute(job, claim_lock_fd=fd)
    # A process could not have started before both run directory and log creation.
    if (run_root.is_dir() and not any(run_root.iterdir()) and not log.exists()
            and prior is not None and prior.get("stage") == "preparing"):
        run_root.rmdir()
        return worker.execute(job, claim_lock_fd=fd)
    worktree: Path | None = None
    try:
        worktree = worker._prepare_worktree(job)
        result = worker._verify_result(job, run_root, worktree, claim_lock_fd=fd)
        if cancel.exists() or worker.stopping:
            return worker._write_status(
                job, state="canceled", stage="terminal", started_at=started_at,
                completed_at=_utc_now(), error="cancel_requested_during_p5_orphan_recovery",
            )
        result.update({"log_path": str(log), "orphan_recovery": "verified_existing_artifact"})
        return worker._write_status(
            job, state="succeeded", stage="terminal", started_at=started_at,
            completed_at=_utc_now(), result_ref=result,
        )  # No fabricated process exit code: only artifact verification was observed.
    except Exception as exc:
        return worker._write_status(
            job, state="failed", stage="terminal", started_at=started_at,
            completed_at=_utc_now(),
            error=f"p5_orphan_unverified_no_retry:{type(exc).__name__}:{str(exc)[:350]}",
        )
    finally:
        if worktree is not None:
            worker._remove_worktree(worktree)


def run_once(worker: StageWorker) -> Any:
    root = worker.paths.control_root / "p5-synthetic-leases"
    root.mkdir(parents=True, exist_ok=True)
    guard = root / "worker.lock"
    if guard.is_symlink():
        from quanthus.stage_learning.worker import StageWorkerError

        raise StageWorkerError("P5 worker lease must be a regular file")
    with guard.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return BUSY  # No other P5 or ordinary kind may start while a child owns it.
        previous_fd = getattr(worker, "_p5_guard_fd", None)
        worker._p5_guard_fd = lock.fileno()
        try:
            return _run_once(worker, lock.fileno())
        finally:
            worker._p5_guard_fd = previous_fd


def _run_once(worker: StageWorker, worker_fd: int) -> Any:
    from quanthus.stage_learning.worker import P5_SYNTHETIC_JOB, StageJob, StageWorkerError

    paths = worker.paths
    root = paths.control_root / "p5-synthetic-leases"
    candidates = sorted(
        set(paths.claimed.glob("stage_*.json")) | set(paths.queue.glob("stage_*.json"))
    )
    for candidate in candidates:
        try:
            value = json.loads(candidate.read_text())
        except FileNotFoundError:
            continue
        except json.JSONDecodeError as exc:
            if (root / candidate.name).exists():
                raise StageWorkerError("P5 leased claim is unreadable") from exc
            continue
        if not isinstance(value, dict) or value.get("kind") != P5_SYNTHETIC_JOB:
            if (root / candidate.name).exists():
                raise StageWorkerError("P5 leased claim identity differs")
            continue
        job = StageJob.from_dict(value)
        if candidate.name != f"{job.job_id}.json":
            raise StageWorkerError("P5 claim filename identity differs")
        lease = root / candidate.name
        recovering = candidate.parent == paths.claimed
        if recovering and not lease.exists():
            return BUSY  # Legacy ownership is unknown; do not start another job either.
        root.mkdir(parents=True, exist_ok=True)
        if lease.is_symlink() or candidate.is_symlink():
            raise StageWorkerError("P5 claim/lease must be regular files")
        # Never unlink or atomically replace this inode: that would split the lock.
        with lease.open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return BUSY
            if not candidate.exists():
                continue
            if json.loads(candidate.read_text()) != value:
                raise StageWorkerError("P5 claim changed while acquiring lease")
            expected = {"schema_version": "quanthus.p5-synthetic-lease.v1", "job": job.to_dict()}
            lock.seek(0)
            raw = lock.read()
            if raw:
                if json.loads(raw) != expected:
                    raise StageWorkerError("P5 lease identity differs")
            elif recovering:
                raise StageWorkerError("P5 claimed lease is incomplete")
            else:
                lock.write(json.dumps(expected, sort_keys=True))
                lock.flush()
                os.fsync(lock.fileno())
            claimed = paths.claimed / candidate.name
            if not recovering:
                if claimed.exists():
                    raise StageWorkerError("P5 duplicate queued/claimed identity")
                try:
                    os.replace(candidate, claimed)
                except FileNotFoundError:
                    continue  # A queued cancellation won the publication race.
            result = _recover(worker, job, worker_fd) if (
                recovering or (paths.status / candidate.name).exists()
            ) else (
                worker.execute(job, claim_lock_fd=worker_fd)
            )
            # Exceptions preserve the claim for a later bounded diagnosis/recovery.
            claimed.unlink(missing_ok=True)
            (paths.cancel / job.job_id).unlink(missing_ok=True)
            return result
    return None

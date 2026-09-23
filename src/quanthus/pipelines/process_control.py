"""Bounded cleanup of an owned POSIX session, including shell pipelines.

Callers must launch with ``start_new_session=True`` and retain ownership until
cleanup completes. Collectors must not daemonize or leave the owned process group.
Production systemd cgroup cleanup remains a separate, required outer boundary.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from contextlib import suppress

PROCESS_CLEANUP_FAILED = 75


class ProcessGroupCleanupError(RuntimeError):
    """Quiescence was not confirmed; a new attempt must not be started."""


def _group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # EPERM proves neither absence nor cleanup. Keep waiting within the
        # bound (including transient zombie teardown); never treat it as gone.
        return True
    return True


def _signal_group(pgid: int, sig: int) -> None:
    with suppress(ProcessLookupError):
        os.killpg(pgid, sig)


def _wait_for_group_exit(process: subprocess.Popen[bytes], timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        # Reap our direct child; its exit alone does not prove group completion.
        process.poll()
        if not _group_exists(process.pid):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.05, remaining))


def terminate_process_group(
    process: subprocess.Popen[bytes],
    *,
    grace_seconds: float = 10.0,
    kill_wait_seconds: float = 10.0,
) -> None:
    """Return only after the owned group disappears, otherwise fail closed.

    Even an already-exited shell can have living children. Inability to inspect or
    signal the group is not success. Unreaped orphan zombies can conservatively keep
    the group non-empty: report unconfirmed cleanup instead of permitting a retry.
    """
    if os.name != "posix" or process.pid <= 0 or process.pid == os.getpgrp():
        raise ProcessGroupCleanupError("refusing cleanup of a non-owned process group")
    if grace_seconds < 0 or kill_wait_seconds < 0:
        raise ValueError("process cleanup budgets must be non-negative")
    try:
        _signal_group(process.pid, signal.SIGTERM)
        if not _wait_for_group_exit(process, grace_seconds):
            _signal_group(process.pid, signal.SIGKILL)
            if not _wait_for_group_exit(process, kill_wait_seconds):
                raise ProcessGroupCleanupError(
                    f"process_cleanup_unconfirmed: group {process.pid} remains after SIGKILL"
                )
        process.wait(timeout=kill_wait_seconds)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProcessGroupCleanupError(
            f"process_cleanup_unconfirmed: group {process.pid}: {type(exc).__name__}"
        ) from exc

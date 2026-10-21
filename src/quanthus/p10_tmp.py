"""Owned P10 job scratch: explicit lifecycle, lease and conservative reclamation."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import shutil
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from quanthus.p10_operational_mail import atomic

POLICY = "p10-success-tmp-72h-v1"
RETENTION = timedelta(hours=72)
MARKER = ".p10-tmp.json"


def physical(path: Path) -> os.stat_result:
    info = path.lstat()
    if path.resolve(strict=True) != path or info.st_uid != os.getuid():
        raise ValueError("P10_TMP_PHYSICAL_OWNER_REQUIRED")
    return info


def check_root(root: Path, protected: list[Path]) -> None:
    if not root.is_absolute() or not stat.S_ISDIR(physical(root).st_mode):
        raise ValueError("P10_TMP_PHYSICAL_ROOT_REQUIRED")
    for path in protected:
        actual = path.resolve(strict=True)
        if root.is_relative_to(actual) or actual.is_relative_to(root):
            raise ValueError("P10_TMP_PROTECTED_OVERLAP")


@dataclass
class Lease:
    path: Path
    status: str = "failed"


@contextlib.contextmanager
def job(root: Path, run_id: str, protected: list[Path]) -> Iterator[Lease]:
    check_root(root, protected)
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,127}", run_id):
        raise ValueError("P10_TMP_RUN_ID_INVALID")
    path = root / run_id
    path.mkdir(mode=0o700)  # Unique run only; no reuse or overwrite of old attempts.
    with (path / ".lease").open("xb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        value = {
            "kind": "p10-owned-tmp-v1",
            "policy": POLICY,
            "run_id": run_id,
            "uid": os.getuid(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "status": "running",
        }
        atomic(path / MARKER, value)
        lease = Lease(path)
        try:
            yield lease
        except BaseException:
            lease.status = "failed"
            raise
        finally:
            value.update(status=lease.status, finished_at=datetime.now(timezone.utc).isoformat())
            atomic(path / MARKER, value)


def _tree(path: Path, device: int) -> None:
    """Preflight the entire disposable tree; never follow links or nested mounts."""

    def failed(error: OSError) -> None:
        raise error

    for base, dirs, files in os.walk(path, followlinks=False, onerror=failed):
        for name in [*dirs, *files]:
            item = Path(base) / name
            info = physical(item)
            if (
                info.st_dev != device
                or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
                or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)
            ):
                raise ValueError("P10_TMP_UNSAFE_MEMBER")


def clean(
    root: Path, protected: list[Path], *, apply: bool = False, now: datetime | None = None
) -> dict[str, Any]:
    check_root(root, protected)
    instant = now or datetime.now(timezone.utc)
    if instant.utcoffset() is None:
        raise ValueError("P10_TMP_AWARE_TIME_REQUIRED")
    result: dict[str, Any] = {
        "policy": POLICY,
        "apply": apply,
        "removed": [],
        "eligible": [],
        "kept": [],
    }
    # A root scan failure raises. An unreadable/unknown job is retained with a reason.
    for path in sorted(root.iterdir()):
        reason = "unrecognized_or_unsafe"
        try:
            info = physical(path)
            if not stat.S_ISDIR(info.st_mode) or info.st_dev != root.stat().st_dev:
                raise ValueError("P10_TMP_UNSAFE_JOB")
            marker = path / MARKER
            meta = physical(marker)
            if not stat.S_ISREG(meta.st_mode) or meta.st_size > 4096 or meta.st_nlink != 1:
                raise ValueError("P10_TMP_MARKER_INVALID")
            # O_NOFOLLOW also protects the final component at open time.
            fd = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd) as stream:
                value = json.load(stream)
            if (
                set(value)
                != {"kind", "policy", "run_id", "uid", "started_at", "finished_at", "status"}
                or value["kind"] != "p10-owned-tmp-v1"
                or value["policy"] != POLICY
                or value["run_id"] != path.name
                or value["uid"] != os.getuid()
            ):
                raise ValueError("P10_TMP_IDENTITY_INVALID")
            if value["status"] != "succeeded":
                reason = "not_successful"
                raise ValueError("P10_TMP_RETAINED")
            started = datetime.fromisoformat(value["started_at"])
            finished = datetime.fromisoformat(value["finished_at"])
            if (
                started.utcoffset() is None
                or finished.utcoffset() is None
                or not started <= finished <= instant
            ):
                raise ValueError("P10_TMP_TIME_INVALID")
            if instant - finished < RETENTION:
                reason = "retention"
                raise ValueError("P10_TMP_RETAINED")
            lock_path = path / ".lease"
            meta = physical(lock_path)
            if not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1:
                raise ValueError("P10_TMP_LOCK_INVALID")
            fd = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb+") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    reason = "active_lease"
                    raise ValueError("P10_TMP_RETAINED") from None
                _tree(path, info.st_dev)
                if path.stat().st_ino != info.st_ino:
                    raise ValueError("P10_TMP_DIRECTORY_CHANGED")
                result["eligible"].append(path.name)
                if apply:
                    if not shutil.rmtree.avoids_symlink_attacks:
                        raise ValueError("P10_TMP_SAFE_RMTREE_UNAVAILABLE")
                    shutil.rmtree(path)
                    result["removed"].append(path.name)
        except (OSError, ValueError, TypeError, KeyError):
            result["kept"].append({"run_id": path.name, "reason": reason})
    return result

"""Same-image, TMP-only housekeeping; its clock runs inside the container."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import stat
import threading
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.p10_operational_mail import atomic
from quanthus.p10_tmp import POLICY, check_root, clean, physical

SHANGHAI = ZoneInfo("Asia/Shanghai")
STATE = ".p10-cleaner.json"
LOCK = ".p10-cleaner.lock"


def slot(now: datetime) -> datetime:
    if now.utcoffset() is None:
        raise ValueError("P10_CLEANER_AWARE_CLOCK_REQUIRED")
    local = now.astimezone(SHANGHAI)
    due = datetime.combine(local.date(), time(3, 30), SHANGHAI)
    return due if local >= due else due - timedelta(days=1)


def tick(root: Path, now: datetime) -> dict[str, Any] | None:
    """At most one successful sweep per Shanghai slot, including restart catch-up."""
    check_root(root, [])
    due = slot(now)
    state = root / STATE
    if state.exists() or state.is_symlink():
        info = physical(state)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 4096 or info.st_nlink != 1:
            raise ValueError("P10_CLEANER_STATE_INVALID")
        fd = os.open(state, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as stream:
            value = json.load(stream)
        if (
            set(value) != {"kind", "policy", "last_slot"}
            or value["kind"] != "p10-tmp-cleaner-v1"
            or value["policy"] != POLICY
        ):
            raise ValueError("P10_CLEANER_STATE_INVALID")
        previous = datetime.fromisoformat(value["last_slot"])
        if previous.utcoffset() is None or slot(previous) != previous:
            raise ValueError("P10_CLEANER_SLOT_INVALID")
        if previous >= due:
            return None  # Includes clock rollback; never repeat the sealed slot.
    result = clean(root, [], apply=True, now=now)
    atomic(state, {"kind": "p10-tmp-cleaner-v1", "policy": POLICY, "last_slot": due.isoformat()})
    return {"slot": due.isoformat(), **result}


def schedule(root: Path) -> None:
    check_root(root, [])
    path = root / LOCK
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "rb+") as lock:
        info = physical(path)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("P10_CLEANER_LOCK_INVALID")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"status": "cleaner_already_active"}), flush=True)
            return
        stopped = threading.Event()

        def stop(signum: int, frame: Any) -> None:
            stopped.set()

        handlers = {s: signal.signal(s, stop) for s in (signal.SIGTERM, signal.SIGINT)}
        try:
            while not stopped.is_set():
                try:
                    result = tick(root, datetime.now(SHANGHAI))
                    if result is not None:
                        print(json.dumps(result, sort_keys=True), flush=True)
                    now = datetime.now(SHANGHAI)
                    wait = (slot(now) + timedelta(days=1) - now).total_seconds()
                except Exception:
                    # Retain failure state; do not advance the slot or expose paths/secrets.
                    print(
                        json.dumps({"status": "failed", "reason": "P10_TMP_CLEANUP_FAILED"}),
                        flush=True,
                    )
                    wait = 300
                stopped.wait(max(1, min(wait, 300)))
        finally:
            for signum, handler in handlers.items():
                signal.signal(signum, handler)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["once", "schedule"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.mode == "schedule":
        if not args.apply:
            parser.error("schedule requires explicit --apply; use once for preview")
        schedule(args.root)
    else:
        print(json.dumps(clean(args.root, [], apply=args.apply), sort_keys=True))


if __name__ == "__main__":
    main()

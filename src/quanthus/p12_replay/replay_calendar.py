"""Session-date provider for the compressed historical replay (issue #748).

The production forward-calendar product only covers 2026-11-24 onward, so a
historical replay cannot consume it.  This module writes the SAME wire
shape the job's calendar port verifies (pointer + versioned descriptor
readable by ``load_forward_calendar``): sessions come from an explicit
list - on CN02 the pinned P10 Base calendar rows - never a weekday guess.
A query outside the declared coverage still blocks the day, exactly like
production.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime
from pathlib import Path

POINTER_SCHEMA = "quanthus.forward-calendar-pointer.v1"
DESCRIPTOR_SCHEMA = "quanthus.forward-calendar.v1"
POINTER_NAME = "CURRENT_FORWARD_CALENDAR.json"


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid():d}.tmp")
    with tmp.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def write_replay_calendar(
    root: Path,
    *,
    sessions,
    version: str,
    available_at: datetime,
) -> Path:
    """Materialize one verified forward-calendar replica under ``root``.

    ``sessions`` is the authoritative open-session list (ascending).  The
    descriptor maps, for each listed session except the last, the strict
    next session for SSE and SZSE (they always agree - a disagreement in
    the real product blocks the day, so the replay keeps one shared list).
    ``available_at`` is stamped verbatim; the adoption rule
    (available_at <= decision cutoff) is enforced by the job's own loader.
    Returns the pointer path.
    """

    ordered = sorted(set(sessions))
    if len(ordered) < 2:
        raise ValueError("replay calendar needs at least two sessions")
    next_map = {
        ordered[i].isoformat(): ordered[i + 1].isoformat()
        for i in range(len(ordered) - 1)
    }
    session_list = [s.isoformat() for s in ordered]
    descriptor = {
        "schema_version": DESCRIPTOR_SCHEMA,
        "version": version,
        "purpose": (
            "P12 issue #748 compressed replay calendar: open sessions from "
            "the pinned P10 session source; replay use only - not the P10 "
            "forward-calendar product."
        ),
        "coverage": {"start": session_list[0], "end": session_list[-1]},
        "venues": {
            "SSE": {
                "status": "AVAILABLE",
                "next_session": dict(next_map),
                "next_session_unavailable": {},
                "sessions": list(session_list),
            },
            "SZSE": {
                "status": "AVAILABLE",
                "next_session": dict(next_map),
                "next_session_unavailable": {},
                "sessions": list(session_list),
            },
            "BSE": {
                "status": "unavailable",
                "reason": "replay session list covers SSE/SZSE only",
                "next_session": None,
                "sessions": None,
            },
        },
    }
    vdir = Path(root) / version
    desc_rel = f"{version}/descriptor.json"
    desc_bytes = (json.dumps(descriptor, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )
    _write_atomic(Path(root) / desc_rel, desc_bytes)
    pointer = {
        "schema_version": POINTER_SCHEMA,
        "product_id": "p12-replay-session-calendar-v1",
        "status": "REPLAY",
        "version": version,
        "descriptor": desc_rel,
        "descriptor_sha256": _sha256_bytes(desc_bytes),
        "published_at": available_at.isoformat(),
        "available_at": available_at.isoformat(),
    }
    pointer_path = Path(root) / POINTER_NAME
    _write_atomic(
        pointer_path,
        (json.dumps(pointer, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"),
    )
    return pointer_path

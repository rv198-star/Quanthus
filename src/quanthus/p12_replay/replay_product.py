"""Build one replay day's daily-core wire shape from the pinned Base
(issue #748).

The replay does not re-date or re-publish P10 data.  For each session it
materializes a LOCAL pointer + manifest whose members are the verified
facts available for that session: the session's real 1d bars (sliced from
the pinned Base partition) plus the fixed provenance fields - a replay
manifest always declares the parent Base manifest sha (re-hashed from the
real bytes at the pinned CN02 path or an injected fixture path), the
replay input contract's static member evidence, and the source's own
knowledge clock.

The manifest is honestly marked as replay input (kind
``cn02_replay_core_v1`` would fail the pin check, so the wire kind stays
``cn02_base24_core_v1`` while a ``replay`` provenance block names the
build); consumer_admission stays NOT_GRANTED.  Missing history/
corporate-action/volume members are declared with explicit unknown
evidence, never fabricated as satisfied.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

POINTER_NAME = "CURRENT_DAILY_CORE_BASE24.json"
POINTER_KIND = "cn02_base24_daily_cycle_v1"
CORE_KIND = "cn02_base24_core_v1"
SELECTED_SCOPE = "core"


def _sha256_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    d = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            d.update(chunk)
    return "sha256:" + d.hexdigest()


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid():d}.tmp")
    with tmp.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


@dataclass(frozen=True)
class ReplayInputSpec:
    """What the replay manifest declares beyond the bar member."""

    history: dict
    corporate_actions: dict
    volume: dict
    knowledge_cutoff: str | None
    completed_at: str | None
    # P1 preserved factor-visibility exceptions (security x window scope)
    p1_visibility_exceptions: dict | None = None


@dataclass(frozen=True)
class ReplayDayProduct:
    """Paths + digests of one materialized replay day."""

    market_date: date
    job_root: Path
    pointer_path: Path
    core_root: Path
    manifest_path: Path
    core_manifest_sha256: str
    index_sha256: str
    row_counts: dict


def write_replay_day(
    job_root: Path,
    *,
    market_date: date,
    bars_parquet: Path,
    row_counts: dict,
    spec: ReplayInputSpec,
    base_manifest_path: Path,
    base_manifest_sha256: str,
    package_id: str | None = None,
) -> ReplayDayProduct:
    """Materialize job_root so ``run_daily`` qualifies this replay day.

    Layout (same as the CN02 daily cycle, locally rooted):
      job_root/<date>-replay/core/manifest.json
      job_root/<date>-replay/core/base.json          (byte-copy of the
        pinned parent Base manifest - P12 re-hashes it, never trusts a
        description)
      job_root/<date>-replay/core/payload/1d.parquet (verified session bars)
      job_root/DATES.json        (index, one entry for this date)
      job_root/CURRENT_DAILY_CORE_BASE24.json
    """

    job_root = Path(job_root)
    core_root = job_root / f"{market_date.isoformat()}-replay" / "core"
    (core_root / "payload").mkdir(parents=True, exist_ok=True)

    base_copy = core_root / "base.json"
    base_bytes = Path(base_manifest_path).read_bytes()
    base_copy.write_bytes(base_bytes)
    actual_base_sha = _sha256_file(base_copy)
    if actual_base_sha != base_manifest_sha256:
        raise ValueError(
            "pinned base manifest bytes re-hash to "
            + actual_base_sha
            + " not the declared "
            + base_manifest_sha256
        )

    payload_dest = core_root / "payload" / "1d.parquet"
    payload_bytes = Path(bars_parquet).read_bytes()
    payload_dest.write_bytes(payload_bytes)
    payload_sha = _sha256_file(payload_dest)
    payload_size = payload_dest.stat().st_size

    manifest = {
        "kind": CORE_KIND,
        "package_id": package_id or f"replay-core-{market_date.isoformat()}",
        "market_date": market_date.isoformat(),
        "replay": {
            "purpose": "P12 issue #748 compressed replay input",
            "source": "pinned Base month partition, replay-sliced",
            "built_at": datetime.now(timezone.utc).isoformat(),
            # P11 口径 3: the source's own observed clock stays on the
            # member rows (knowledge_from per bar); the manifest records
            # the replay's build/read clock - the two are never conflated
            # and no historical as-of is back-filled (the replay adapts
            # with ENGINEERING_REPLAY_AS_OBSERVED instead).
            "as_observed": "ENGINEERING_REPLAY_AS_OBSERVED",
            "source_clock": "member-row knowledge_from (per bar, UTC)",
            "read_clock": datetime.now(timezone.utc).isoformat(),
        },
        "base": {
            "manifest_sha256": base_manifest_sha256,
            "content_digest": None,
            "path": "base.json",
        },
        "row_counts": dict(row_counts),
        "files": [
            {
                "path": "payload/1d.parquet",
                "sha256": payload_sha,
                "size_bytes": payload_size,
            }
        ],
        "content_digest": _sha256_bytes(payload_bytes),
        "completed_at": spec.completed_at,
        "knowledge_cutoff": spec.knowledge_cutoff,
        "consumer_admission": "NOT_GRANTED",
        "history": spec.history,
        "corporate_actions": spec.corporate_actions,
        "volume": spec.volume,
        "p1_visibility_exceptions": spec.p1_visibility_exceptions,
    }
    manifest_bytes = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode("utf-8")
    manifest_path = core_root / "manifest.json"
    _write_atomic(manifest_path, manifest_bytes)
    core_sha = _sha256_bytes(manifest_bytes)

    index = {
        "dates": {
            market_date.isoformat(): {
                "core": str(core_root),
                "core_manifest_sha256": core_sha,
                "market_date": market_date.isoformat(),
            }
        }
    }
    index_bytes = json.dumps(index, sort_keys=True).encode("utf-8")
    (job_root / "DATES.json").write_bytes(index_bytes)
    index_sha = _sha256_bytes(index_bytes)

    entry = {
        "kind": POINTER_KIND,
        "market_date": market_date.isoformat(),
        "core": str(core_root),
        "core_manifest_sha256": core_sha,
        "base_manifest_sha256": base_manifest_sha256,
        "core_row_counts": dict(row_counts),
        "completed_at": spec.completed_at,
        "consumer_admission": "NOT_GRANTED",
        "status": "QUALIFIED_1D_NATIVE5M",
        "row_counts": {"1d": int(row_counts.get("dwd_security_bar", 0)), "5m": 0},
    }
    pointer = {
        "kind": POINTER_KIND,
        "selected_scope": SELECTED_SCOPE,
        "market_date": market_date.isoformat(),
        "consumer_admission": "NOT_GRANTED",
        "index_sha256": index_sha,
        "entry": entry,
    }
    pointer_path = job_root / POINTER_NAME
    _write_atomic(
        pointer_path,
        (json.dumps(pointer, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"),
    )
    return ReplayDayProduct(
        market_date=market_date,
        job_root=job_root,
        pointer_path=pointer_path,
        core_root=core_root,
        manifest_path=manifest_path,
        core_manifest_sha256=core_sha,
        index_sha256=index_sha,
        row_counts=dict(row_counts),
    )

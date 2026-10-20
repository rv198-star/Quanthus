"""Publish one immutable input plan from exact sealed Daily source identities.

No acquisition, payload qualification, CURRENT, notification or timer activation.
Each domain verifier still reads its payload in the downstream Daily DAG. An
intake seal means controls are frozen, never that those payloads are qualified.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .base22 import canonical, file_sha
from .base22_sources import disjoint_output, physical_path
from .base24_image import KIND as BASE_KIND
from .daily_base24_job import CST, KIND, LANES, WINDOW, delivery_window, validate_plan_inputs
from .native5m_daily_source import KIND as NATIVE_KIND

PREFIXES = {
    "market_daily_core": "hermes-market-daily-core-",
    "calendar_daily_events": "hermes-calendar-daily-events-",
}


def _object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("DAILY_INTAKE_OBJECT_REQUIRED")
    return value


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _exclusive(path: Path, raw: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _seal(path: Path, digest: str) -> None:
    # The consumer must never observe a partly written seal. link() publishes
    # the synced job-local temporary atomically and refuses an existing target.
    fd, name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write((digest + "\n").encode("ascii"))
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)
    finally:
        temporary.unlink(missing_ok=True)


def _ready(plan: dict[str, Any], paths: dict[str, Path]) -> None:
    """Check source seals and scope without repeating full payload scans."""
    for lane in sorted(LANES):
        entry = plan["lanes"][lane]
        root = paths["source_root"] / entry["release_path"]
        ready = physical_path(root / "READY")
        manifest = _object(root / "release.json")
        if ready.read_text().strip() != entry["sha256"] or not str(
            manifest.get("release_id", "")
        ).startswith(PREFIXES[lane] + plan["market_date"].replace("-", "") + "-"):
            raise ValueError("DAILY_INTAKE_LANE_SEAL_OR_SCOPE_DIFFERS")
    for key, kind in (("native5m", NATIVE_KIND), ("base", BASE_KIND)):
        manifest = _object(paths[key] / "manifest.json")
        if (
            physical_path(paths[key] / "READY").read_text().strip() != plan["control_sha256"][key]
            or manifest.get("kind") != kind
        ):
            raise ValueError("DAILY_INTAKE_PRODUCT_SEAL_OR_SCOPE_DIFFERS")
        if key == "native5m" and (
            manifest.get("interval") != "5m"
            or manifest.get("execution_authorized") is not False
            or manifest.get("policy_id") != "p10-trading-day-complete-daily-v1"
        ):
            raise ValueError("DAILY_INTAKE_NATIVE_SCOPE_DIFFERS")


def publish(
    *,
    market_date: date,
    code_revision: str,
    source_root: Path,
    releases: dict[str, str],
    native5m: Path,
    base: Path,
    release_config: Path,
    prior_pointer: Path,
    plan_root: Path,
    mode: str,
) -> dict[str, Any]:
    """Freeze exact controls, write the plan exclusively, then write its seal last.

    Exact-identity retries reuse both files. A crash after the plan but before its
    seal can finish only that same unmodified plan with all inputs still matching.
    Invalid inputs/path overlap reject before mkdir, lock or publication.
    """
    if set(releases) != LANES:
        raise ValueError("DAILY_INTAKE_EXACT_TWO_LANES_REQUIRED")
    paths = {
        k: physical_path(p)
        for k, p in {
            "source_root": source_root,
            "native5m": native5m,
            "base": base,
            "release_config": release_config,
            "prior_pointer": prior_pointer,
        }.items()
    }
    lanes = {}
    for lane, relative in releases.items():
        p = Path(relative)
        if not p.parts or p.is_absolute() or ".." in p.parts or p.as_posix() != relative:
            raise ValueError("DAILY_INTAKE_RELEASE_PATH_INVALID")
        path = physical_path(paths["source_root"] / p / "release.json")
        lanes[lane] = {"release_path": relative, "sha256": file_sha(path)}
    plan = {
        "kind": KIND,
        "market_date": market_date.isoformat(),
        "code_revision": code_revision,
        **{key: str(value) for key, value in paths.items()},
        "lanes": lanes,
        "control_sha256": {
            key: file_sha(
                paths[key] / "manifest.json" if key in {"base", "native5m"} else paths[key]
            )
            for key in ("base", "native5m", "release_config", "prior_pointer")
        },
        "consumer_admission": "NOT_GRANTED",
        "execution_authorized": False,
    }
    validate_plan_inputs(plan)
    _ready(plan, paths)
    # The intake deadline reserves the final 15 minutes for build and replay.
    delivery_window(market_date, _now(), mode)
    stop = datetime.fromisoformat(f"{market_date}T{WINDOW['sealed_input_deadline']}").replace(
        tzinfo=CST
    )
    if mode == "t-day" and _now() >= stop:
        raise TimeoutError("DAILY_INTAKE_SEALED_INPUT_DEADLINE")
    plan_root = disjoint_output(plan_root, list(paths.values()))
    lock = disjoint_output(
        plan_root.with_name("." + plan_root.name + ".publish.lock"), list(paths.values())
    )
    if not plan_root.parent.is_dir() or (plan_root.exists() and not plan_root.is_dir()):
        raise ValueError("DAILY_INTAKE_EXISTING_PARENT_REQUIRED")
    target = physical_path(plan_root / f"{market_date}.json")
    seal = physical_path(plan_root / f"{market_date}.json.READY")
    raw = canonical(plan) + b"\n"
    import hashlib

    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a"):
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        validate_plan_inputs(plan)
        _ready(plan, paths)
        if (target.exists() and target.read_bytes() != raw) or (
            seal.exists() and (not target.exists() or seal.read_text().strip() != digest)
        ):
            raise ValueError("DAILY_INTAKE_EXISTING_IDENTITY_DIFFERS")
        reused = target.exists() and seal.exists()
        plan_root.mkdir(mode=0o700, exist_ok=True)
        if not target.exists():
            _exclusive(target, raw)
        # Recheck controls and cutoff after writing, before publishing readiness.
        validate_plan_inputs(plan)
        _ready(plan, paths)
        if mode == "t-day" and _now() >= stop:
            raise TimeoutError("DAILY_INTAKE_SEALED_INPUT_DEADLINE")
        if not seal.exists():
            _seal(seal, digest)
        directory = os.open(plan_root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    return {
        "status": "REUSED_FROZEN_INPUT_PLAN" if reused else "PUBLISHED_FROZEN_INPUT_PLAN",
        "plan": str(target),
        "plan_sha256": digest,
        "market_date": market_date.isoformat(),
        "mode": mode,
        "payload_qualification": "REQUIRED_IN_DOWNSTREAM_DAILY_DAG",
        "natural_T_day_acquisition_claim": False,
        "consumer_admission": "NOT_GRANTED",
        "execution_authorized": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-date", type=date.fromisoformat, required=True)
    parser.add_argument("--code-revision", required=True)
    for name in ("source-root", "native5m", "base", "release-config", "prior-pointer", "plan-root"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--market-release", required=True, help="exact relative release directory")
    parser.add_argument("--events-release", required=True, help="exact relative release directory")
    parser.add_argument("--mode", choices=("t-day", "replay"), required=True)
    args = vars(parser.parse_args())
    args["releases"] = {
        "market_daily_core": args.pop("market_release"),
        "calendar_daily_events": args.pop("events_release"),
    }
    print(json.dumps(publish(**args), sort_keys=True))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Run one registered maintenance receiver from a private machine manifest."""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--job", required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    job = next(row for row in manifest["jobs"] if row["id"] == args.job)
    root, state = Path(job["source_root"]).resolve(), Path(job["state_dir"]).resolve()
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    logger = logging.getLogger("maintenance-receiver")
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(state / "receiver.log", maxBytes=1_000_000, backupCount=3)
    logger.addHandler(handler)
    actual = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True, capture_output=True, check=True
    ).stdout.strip()
    if actual != job["source_sha"]:
        logger.error("source identity changed; revalidate deployment")
        return 2
    clean = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "diff",
            "--quiet",
            "HEAD",
            "--",
            "scripts/maintenance*.py",
            "config/maintenance*.json",
            "docs/maintenance/loop-contract.md",
            "AGENTS.md",
        ]
    )
    if clean.returncode:
        logger.error("receiver source/configuration differs from the pinned commit")
        return 2
    command = [
        job.get("python", sys.executable),
        str(root / "scripts/maintenance_loop.py"),
        "poll",
        "--root",
        str(root),
        "--workspace",
        job["workspace"],
        "--state-dir",
        str(state),
        "--environment-id",
        job["environment_id"],
    ]
    if job.get("sources_file"):
        command += ["--sources-file", job["sources_file"]]
    if job.get("executor"):
        command += ["--executor", job["executor"]]
    result = subprocess.run(command, text=True, capture_output=True, timeout=1900)
    # stdout is the bounded, redacted transport summary; stderr remains private.
    logger.info("job=%s exit=%s summary=%s", args.job, result.returncode, result.stdout[-16000:])
    if result.stderr:
        logger.warning("job=%s private_diagnostic=%s", args.job, result.stderr[-2000:])
    print(result.stdout, end="")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())

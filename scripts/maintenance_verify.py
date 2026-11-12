#!/usr/bin/env python3
"""Execute focused maintenance tests and retain source-bound verification evidence."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from maintenance_loop import RUNTIME_FILES, file_digest, fingerprint, git, load_config
from maintenance_store import digest, identity, now


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    config = load_config(root)
    test = config["verification_test_path"]
    command = [
        sys.executable,
        "-m",
        "unittest",
        "discover",
        "-s",
        str(Path(test).parent),
        "-p",
        Path(test).name,
        "-v",
    ]
    started = now()
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=180)
    output = result.stdout + result.stderr
    args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    log = args.output_dir / "focused-tests.log"
    log.write_text(output)
    match = re.search(r"Ran (\d+) tests?", output)
    count = int(match[1]) if match else 0
    value = {
        "schema": "maintenance.verification.v1",
        "verification_run_id": identity("verification"),
        "started_at": started,
        "completed_at": now(),
        "source_sha": git(root, "rev-parse", "HEAD"),
        "operational_fingerprint": fingerprint(root, config),
        "runtime": sys.version.split()[0],
        "command": [
            "python",
            "-m",
            "unittest",
            "discover",
            "-s",
            str(Path(test).parent),
            "-p",
            Path(test).name,
            "-v",
        ],
        "exit_code": result.returncode if count else 2,
        "test_count": count,
        "tested_files": {
            p: file_digest(root / p)
            for p in RUNTIME_FILES
            + [test, "config/maintenance-loop.v1.json"]
            + config["authority_files"]
        },
        "log_file": log.name,
        "log_digest": file_digest(log),
    }
    value["evidence_digest"] = digest(value)
    path = args.output_dir / "verification.json"
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "evidence_path": str(path),
                "test_count": count,
                "exit_code": value["exit_code"],
                "evidence_digest": value["evidence_digest"],
            }
        )
    )
    return value["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())

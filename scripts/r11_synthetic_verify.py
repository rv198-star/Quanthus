#!/usr/bin/env python3
"""Run the R11 synthetic suite and write machine-readable expected/actual evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args()

    root = Path(args.repo_root).resolve()
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    golden_output = output / "golden-actuals.json"
    story_output = output / "story-actuals.json"
    for path in (golden_output, story_output):
        path.unlink(missing_ok=True)

    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(root / "src")
    environment["R11_GOLDEN_ACTUALS"] = str(golden_output)
    environment["R11_STORY_ACTUALS"] = str(story_output)
    command = (
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "tests/unit/test_product_daily_r11.py",
        "tests/contract/test_product_daily_r11.py",
    )
    started = datetime.now(timezone.utc)
    process = subprocess.run(command, cwd=root, env=environment, capture_output=True)
    ended = datetime.now(timezone.utc)
    stdout_path = output / "pytest.stdout.log"
    stderr_path = output / "pytest.stderr.log"
    stdout_path.write_bytes(process.stdout)
    stderr_path.write_bytes(process.stderr)

    receipt: dict[str, object] = {
        "schema_version": "quanthus.r11.synthetic-verification.v1",
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "command": list(command),
        "returncode": process.returncode,
        "passed": process.returncode == 0,
        "stdout_log": str(stdout_path),
        "stdout_sha256": _sha(stdout_path),
        "stderr_log": str(stderr_path),
        "stderr_sha256": _sha(stderr_path),
        "authority": {
            "synthetic_only": True,
            "market_row_read": 0,
            "paid_reference": 0,
            "training": 0,
            "capital_replay": 0,
            "daily_serving": 0,
            "production": 0,
        },
    }
    if process.returncode == 0 and golden_output.is_file() and story_output.is_file():
        golden = json.loads(golden_output.read_text(encoding="utf-8"))
        stories = json.loads(story_output.read_text(encoding="utf-8"))
        receipt["golden_case_count"] = golden["case_count"]
        receipt["golden_all_passed"] = golden["all_passed"]
        receipt["story_count"] = stories["story_count"]
        receipt["stories_rebuild_identical"] = stories["rebuild_identical"]
        receipt["golden_actuals"] = {
            "path": str(golden_output),
            "sha256": _sha(golden_output),
        }
        receipt["story_actuals"] = {
            "path": str(story_output),
            "sha256": _sha(story_output),
        }
    receipt_path = output / "synthetic-verification.json"
    receipt_path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    sys.stdout.buffer.write(process.stdout)
    sys.stderr.buffer.write(process.stderr)
    return process.returncode


if __name__ == "__main__":
    raise SystemExit(main())

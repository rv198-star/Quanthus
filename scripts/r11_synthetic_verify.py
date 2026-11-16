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
from decimal import Decimal
from pathlib import Path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_expected(expected: object, actual: object, path: str, errors: list[str]) -> None:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            errors.append(f"{path}: expected object")
            return
        if set(expected) != set(actual):
            errors.append(f"{path}: key mismatch")
            return
        for key in expected:
            _verify_expected(expected[key], actual[key], f"{path}.{key}", errors)
        return
    if isinstance(expected, bool):
        if type(actual) is not bool or actual is not expected:
            errors.append(f"{path}: boolean mismatch")
        return
    if isinstance(expected, int):
        if type(actual) is not int or actual != expected:
            errors.append(f"{path}: integer mismatch")
        return
    if isinstance(expected, str):
        if not isinstance(actual, str):
            errors.append(f"{path}: string mismatch")
            return
        try:
            expected_decimal = Decimal(expected)
            actual_decimal = Decimal(actual)
        except (ArithmeticError, ValueError):
            if actual != expected:
                errors.append(f"{path}: string mismatch")
        else:
            if actual_decimal != expected_decimal:
                errors.append(f"{path}: decimal mismatch")
        return
    if actual != expected:
        errors.append(f"{path}: value mismatch")


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
    verification_errors: list[str] = []
    if process.returncode == 0 and golden_output.is_file() and story_output.is_file():
        golden = json.loads(golden_output.read_text(encoding="utf-8"))
        stories = json.loads(story_output.read_text(encoding="utf-8"))
        frozen = json.loads(
            (root / "config" / "research" / "r11" / "lead-golden-cases.json").read_text(
                encoding="utf-8"
            )
        )
        actuals = golden.get("actuals", {})
        case_checks: dict[str, bool] = {}
        for case in frozen["cases"]:
            case_id = str(case["id"])
            actual = actuals.get(case_id)
            before = len(verification_errors)
            _verify_expected(case["expect"], actual, case_id, verification_errors)
            case_checks[case_id] = len(verification_errors) == before
        if set(actuals) != {str(case["id"]) for case in frozen["cases"]}:
            verification_errors.append("golden actual case set mismatch")
        expected_story_digests = {
            key: "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
            for key, value in stories.get("traces", {}).items()
        }
        if stories.get("schema_version") != "quanthus.r11.story-actuals.v2":
            verification_errors.append("story schema mismatch")
        if stories.get("story_count") != 3 or len(stories.get("traces", {})) != 3:
            verification_errors.append("story count mismatch")
        if stories.get("stories") != expected_story_digests:
            verification_errors.append("story digest mismatch")
        if stories.get("rebuild_identical") is not True:
            verification_errors.append("story rebuild mismatch")
        receipt["golden_case_count"] = golden["case_count"]
        receipt["golden_all_passed"] = not verification_errors
        receipt["case_checks"] = case_checks
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
        receipt["verification_errors"] = verification_errors
    receipt_path = output / "synthetic-verification.json"
    receipt_path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    sys.stdout.buffer.write(process.stdout)
    sys.stderr.buffer.write(process.stderr)
    return 0 if process.returncode == 0 and not verification_errors else 1


if __name__ == "__main__":
    raise SystemExit(main())

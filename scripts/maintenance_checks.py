#!/usr/bin/env python3
"""Select existing business checks for the paths this development task changes."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from maintenance_audit import runtime_satisfies


def changed_paths(root, base, head=None):
    command = ["git", "-C", str(root), "diff", "--name-only", "-z", base]
    if head:
        command.append(head)
    raw = subprocess.check_output(command).decode()
    paths = set(raw.split("\0")) - {""}
    if not head:
        paths |= set(
            subprocess.check_output(
                [
                    "git",
                    "-C",
                    str(root),
                    "ls-files",
                    "--others",
                    "--exclude-standard",
                    "-z",
                ]
            )
            .decode()
            .split("\0")
        ) - {""}
    return sorted(paths)


def select_rules(config, paths):
    return [
        rule
        for rule in config["rules"]
        if rule["state"] == "active"
        and any(fnmatch.fnmatchcase(path, pattern) for path in paths for pattern in rule["paths"])
    ]


def execute(root, rules):
    results = []
    environment = dict(os.environ)
    sources = [str(path) for path in (root / "src", root / "EKRI/src") if path.is_dir()]
    environment["PYTHONPATH"] = os.pathsep.join(sources + [environment.get("PYTHONPATH", "")])
    for rule in rules:
        for command in rule["commands"]:
            argv = [sys.executable if arg == "{python}" else arg for arg in command]
            result = subprocess.run(
                argv, cwd=root, env=environment, timeout=rule.get("timeout_seconds", 180)
            )
            results.append(
                {
                    "lesson_id": rule["lesson_id"],
                    "command": command,
                    "exit_code": result.returncode,
                    "failure_owner": rule["failure_owner"],
                }
            )
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--base", required=True)
    parser.add_argument("--head")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.base) or (
        args.head and not re.fullmatch(r"[0-9a-f]{40}", args.head)
    ):
        parser.error("--base and --head must be exact 40-character commits")
    root = args.root.resolve()
    config = json.loads((root / "config/maintenance-checks.v1.json").read_text())
    if args.run and not runtime_satisfies(sys.version.split()[0], config["python"]):
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "reason": "project_python_mismatch",
                    "required": config["python"],
                }
            )
        )
        return 2
    paths = changed_paths(root, args.base, args.head)
    rules = select_rules(config, paths)
    results = execute(root, rules) if args.run else []
    print(
        json.dumps(
            {
                "schema": "maintenance.check-selection.v1",
                "base": args.base,
                "head": args.head,
                "paths": paths,
                "selected": rules,
                "executed": results,
                "effect_status": "observing",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if any(item["exit_code"] for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())

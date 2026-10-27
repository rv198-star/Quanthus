#!/usr/bin/env python3
"""Inspect, append and reconcile blueprint progress. GitHub access is read-only."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quanthus.blueprint_progress import (  # noqa: E402
    CATALOG,
    LEDGER,
    VIEW,
    check_append_only,
    check_change_impact,
    encoded,
    event_scope,
    github_snapshot,
    read_json,
    reconcile,
    render,
    require,
    validate_ledger,
    work_context,
)


def atomic_write(path: Path, data: bytes) -> None:
    require(not path.is_symlink(), "refuse symlink output")
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.chmod(0o644)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["check", "render", "context", "prepare", "record", "audit"]
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--issue", type=int)
    parser.add_argument("--module", help="Limit context to one mapped module")
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--base-ref")
    parser.add_argument("--issues", type=Path, help="Offline snapshot from an earlier audit")
    parser.add_argument("--output", type=Path, help="Audit receipt destination")
    parser.add_argument("--event", type=Path, help="GitHub event; body is data, never code")
    args = parser.parse_args()
    try:
        root = args.root.resolve()
        catalog, ledger = read_json(root / CATALOG), read_json(root / LEDGER)
        validate_ledger(catalog, ledger, root)
        if args.base_ref:
            base = subprocess.run(
                ["git", "rev-parse", "--verify", f"{args.base_ref}^{{commit}}"],
                cwd=root,
                capture_output=True,
                check=False,
            )
            require(base.returncode == 0, "unreadable base revision")
            revision = base.stdout.decode().strip()
            listing = subprocess.run(
                ["git", "ls-tree", "--name-only", revision, LEDGER],
                cwd=root,
                capture_output=True,
                check=False,
            )
            require(listing.returncode == 0, "cannot inspect prior ledger")
            if listing.stdout.strip():
                old = subprocess.check_output(["git", "show", f"{revision}:{LEDGER}"], cwd=root)
                prior_ledger = json.loads(old)
                check_append_only(prior_ledger, ledger)
                prior_catalog = json.loads(
                    subprocess.check_output(["git", "show", f"{revision}:{CATALOG}"], cwd=root)
                )
                changed = subprocess.check_output(
                    ["git", "diff", "--no-renames", "--name-only", revision, "--"],
                    cwd=root,
                    text=True,
                ).splitlines()
                check_change_impact(prior_catalog, prior_ledger, ledger, changed)
        if args.command == "record":
            require(args.checkpoint is not None, "--checkpoint required")
            record = read_json(args.checkpoint)
            lock = os.open(
                root / ".blueprint-progress.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
            )
            with os.fdopen(lock, "w") as stream:
                fcntl.flock(stream, fcntl.LOCK_EX)
                # Re-read under the lock: a second writer must not overwrite the first.
                catalog, ledger = read_json(root / CATALOG), read_json(root / LEDGER)
                validate_ledger(catalog, ledger, root)
                previous = next((r for r in ledger["records"] if r["id"] == record.get("id")), None)
                require(previous is None or previous == record, "checkpoint id already used")
                if previous is None:
                    candidate = {**ledger, "records": [*ledger["records"], record]}
                    validate_ledger(catalog, candidate, root)
                    # The ledger is authoritative. An interruption before rendering is detected
                    # by `check`; `render` repairs only its disposable projection.
                    atomic_write(root / LEDGER, encoded(candidate))
                    ledger = candidate
                atomic_write(root / VIEW, render(catalog, ledger).encode())
            print("CHECKPOINT_RECORDED; module truth remains scoped to its evidence")
        elif args.command == "render":
            atomic_write(root / VIEW, render(catalog, ledger).encode())
            print(VIEW)
        elif args.command == "context":
            require(args.issue is not None, "--issue required")
            print(encoded(work_context(catalog, ledger, args.issue, args.module)).decode(), end="")
        elif args.command == "prepare":
            require(args.issue is not None and args.output is not None, "--issue/--output required")
            context = work_context(catalog, ledger, args.issue)
            item = context["work_item"]
            live = (
                read_json(args.issues)["issues"]
                if args.issues
                else github_snapshot({**catalog, "work_items": [item]})
            )
            observed = [row for row in live if row["number"] == args.issue]
            require(len(observed) == 1, "missing/duplicate requested issue observation")
            now = datetime.now(timezone.utc)
            draft = {
                "id": f"CP-{args.issue}-{now.strftime('%Y%m%dT%H%M%S%fZ')}",
                "recorded_at": now.isoformat(),
                "actor": "",
                "work_item": item["id"],
                "outcome": "progress",
                "summary": "",
                "evidence": [],
                "updates": [],
                "no_change_reason": "",
                "issue_observations": observed,
            }
            # A draft deliberately cannot pass validation before its author supplies judgment.
            with args.output.open("xb") as stream:
                stream.write(encoded(draft))
            print(f"DRAFT_ONLY: {args.output}; fill evidence and module impact before record")
        elif args.command == "audit":
            scope = event_scope(catalog, read_json(args.event)) if args.event else "tracked"
            if scope != "tracked":
                receipt: dict[str, Any] = {
                    "kind": "blueprint-issue-reconciliation-v1",
                    "status": "UNMAPPED_WORK_ITEM" if scope == "unmapped" else "OUTSIDE_SCOPE",
                    "module_status_changed": False,
                }
                if args.output:
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    atomic_write(args.output, encoded(receipt))
                print(encoded(receipt).decode(), end="")
                return 2 if scope == "unmapped" else 0
            live = read_json(args.issues)["issues"] if args.issues else github_snapshot(catalog)
            drift = reconcile(catalog, ledger, live)
            receipt = {
                "kind": "blueprint-issue-reconciliation-v1",
                "issues": live,
                "drift": drift,
                "module_status_changed": False,
                "status": "NEEDS_CHECKPOINT" if drift else "RECONCILED",
            }
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(args.output, encoded(receipt))
            print(encoded(receipt).decode(), end="")
            return 2 if drift else 0
        else:
            require(
                (root / VIEW).read_text(encoding="utf-8") == render(catalog, ledger),
                "generated blueprint is stale; run render",
            )
            print("BLUEPRINT_CHECK_PASS (links/hashes/history/view; not scientific acceptance)")
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        if args.command == "audit" and args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(
                args.output,
                encoded(
                    {"status": "AUDIT_FAILED", "module_status_changed": False, "reason": str(exc)}
                ),
            )
        print(f"BLUEPRINT_CHECK_FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

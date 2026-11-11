#!/usr/bin/env python3
"""Read-only file/commit checks before a worktree is archived or removed."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(root), *args], text=True, capture_output=True)


def inspect_worktree(root: Path) -> dict[str, object]:
    root = root.resolve()
    top = git(root, "rev-parse", "--show-toplevel")
    if top.returncode or Path(top.stdout.strip()).resolve() != root:
        raise ValueError("--worktree must name an existing worktree root")

    def paths(*args: str) -> list[str]:
        result = git(root, *args)
        if result.returncode:
            raise ValueError(result.stderr.strip())
        return [value for value in result.stdout.split("\0") if value]

    tracked = sorted(
        set(paths("diff", "--name-only", "-z") + paths("diff", "--cached", "--name-only", "-z"))
    )
    untracked = paths("ls-files", "--others", "--exclude-standard", "--directory", "-z")
    ignored = paths("ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "-z")
    main = git(root, "merge-base", "--is-ancestor", "HEAD", "origin/main")
    if main.returncode not in (0, 1):
        raise ValueError("origin/main must be fetched before checking commit preservation")
    upstream = git(root, "rev-parse", "--verify", "@{upstream}")
    unpushed = None
    if upstream.returncode == 0:
        count = git(root, "rev-list", "--count", "@{upstream}..HEAD")
        if count.returncode:
            raise ValueError(count.stderr.strip())
        unpushed = int(count.stdout.strip())
    return {
        "worktree": str(root),
        "tracked_changes": tracked,
        "untracked": untracked,
        "ignored_review_required": ignored,
        "head_in_cached_origin_main": main.returncode == 0,
        "commits_ahead_of_upstream": unpushed,
        "file_and_commit_check_pass": not (tracked or untracked or ignored)
        and main.returncode == 0,
        "external_dependency_and_process_review_required": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = inspect_worktree(args.worktree)
    except ValueError as error:
        parser.exit(2, f"error: {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["file_and_commit_check_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

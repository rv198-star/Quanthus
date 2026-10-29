#!/usr/bin/env python3
"""Check exploration memory, inspect context and validate non-authorizing proposals."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quanthus.blueprint_progress import (  # noqa: E402
    CATALOG,
    encoded,
    read_json,
    require,
    validate_ledger,
)
from quanthus.blueprint_progress import (
    LEDGER as BLUEPRINT,
)
from quanthus.exploration_governance import (  # noqa: E402
    LEDGER,
    NEXT,
    VIEW,
    check_history,
    context,
    render,
    validate_memory,
    validate_request,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "context", "preflight", "render"])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--base-ref", help="Exact prior full Git SHA for append-only checks")
    parser.add_argument("--family", default="")
    parser.add_argument("--module", action="append", default=[])
    parser.add_argument("--request", type=Path)
    args = parser.parse_args()
    try:
        root = args.root.resolve()
        catalog, blueprint = read_json(root / CATALOG), read_json(root / BLUEPRINT)
        memory = read_json(root / LEDGER)
        validate_ledger(catalog, blueprint, root)
        validate_memory(memory, catalog, blueprint, root)
        if args.base_ref:
            require(
                len(args.base_ref) == 40 and all(c in "0123456789abcdef" for c in args.base_ref),
                "full base SHA required",
            )
            listing = subprocess.run(
                ["git", "ls-tree", "--name-only", args.base_ref, "--", LEDGER],
                cwd=root,
                capture_output=True,
                check=False,
            )
            require(listing.returncode == 0, "unreadable prior revision")
            if listing.stdout.strip():
                old = subprocess.check_output(
                    ["git", "show", f"{args.base_ref}:{LEDGER}"], cwd=root
                )
                check_history(json.loads(old), memory)
        if args.command == "render":
            print(render(memory), end="")
        elif args.command == "context":
            require(set(args.module) <= {m["id"] for m in catalog["modules"]}, "unknown module")
            print(encoded(context(memory, args.family, args.module)).decode(), end="")
        elif args.command == "preflight":
            require(args.request is not None, "--request required")
            result = validate_request(read_json(args.request), memory, catalog, root)
            print(encoded(result).decode(), end="")
        else:
            require((root / VIEW).read_text() == render(memory), "stale conclusion view")
            validate_request(read_json(root / NEXT), memory, catalog, root)
            print("EXPLORATION_RECORDS_CHECKED; no scientific or execution authority granted")
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(f"EXPLORATION_CHECK_FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

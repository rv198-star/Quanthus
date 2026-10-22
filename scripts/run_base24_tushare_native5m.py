#!/usr/bin/env python3
"""Run a frozen Base 2.4 exact-day Tushare-style 5m selection."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path
from typing import Any

import requests

from quanthus.pipelines.securities_warehouse.base24_native5m_acquire import (
    run_acquisition,
)
from quanthus.providers.tushare_native_5m import fetch_captured_day


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Credential-ephemeral, raw-preserving Base 2.4 native-5m acquisition"
    )
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--source-root", action="append", type=Path, default=[])
    parser.add_argument("--max-new-partitions", type=int)
    parser.add_argument("--max-consecutive-failures", type=int, default=3)
    parser.add_argument("--max-attempts-per-partition", type=int, default=1)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    plan = args.plan.read_bytes()
    selection = args.selection.read_bytes() if args.selection else None
    print("READY_FOR_KEY", flush=True)
    key = getpass.getpass("") if sys.stdin.isatty() else sys.stdin.readline().strip()
    if not key:
        raise ValueError("API_KEY_REQUIRED")
    session = requests.Session()
    try:
        result: dict[str, Any] = run_acquisition(
            plan,
            args.output,
            lambda staging, request: fetch_captured_day(
                staging,
                request,
                api_key=key,
                get=session.get,
            ),
            source_roots=args.source_root,
            max_new_partitions=args.max_new_partitions,
            max_failures=args.max_consecutive_failures,
            max_attempts_per_partition=args.max_attempts_per_partition,
            selection_bytes=selection,
        )
    finally:
        key = ""
        session.close()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "COMPLETE" else 3


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Render or verify the deterministic Quanthus Data & Indicator Catalog projection."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from quanthus.data_catalog import (
    BUILDER_VERSION,
    CATALOG_SCHEMA,
    SNAPSHOT_MANIFEST,
    SOURCE_MANIFEST,
    build_catalog,
    canonical_bytes,
    file_sha256,
)
from quanthus.policy import find_project_root

MANIFEST_SCHEMA = "quanthus.data-indicator-catalog-snapshot-manifest.v1"


def snapshot_manifest(snapshot: dict, source_path: Path) -> dict:
    return {
        "schema_version": MANIFEST_SCHEMA,
        "catalog_schema_version": CATALOG_SCHEMA,
        "builder_version": BUILDER_VERSION,
        "source_manifest_sha256": file_sha256(source_path),
        "snapshot_digest": snapshot["snapshot_digest"],
        "entry_count": snapshot["summary"]["entry_count"],
        "gap_count": snapshot["summary"]["gap_count"],
        "counts_by_kind": snapshot["summary"]["counts_by_kind"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", default=SOURCE_MANIFEST)
    parser.add_argument("--manifest", default=SNAPSHOT_MANIFEST)
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--full",
        action="store_true",
        help="print the full derived catalog instead of the small snapshot manifest",
    )
    args = parser.parse_args()
    root = find_project_root()
    source_path = (root / args.sources).resolve()
    snapshot = build_catalog(source_path)
    manifest = snapshot_manifest(snapshot, source_path)
    if args.check:
        path = (root / args.manifest).resolve()
        if not path.is_file():
            print(f"catalog snapshot manifest missing: {path}", file=sys.stderr)
            return 2
        try:
            observed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            print("catalog snapshot manifest is unreadable", file=sys.stderr)
            return 2
        if observed != manifest:
            print(
                "catalog snapshot drift detected; refresh the manifest from authoritative sources",
                file=sys.stderr,
            )
            return 3
        print(
            f"catalog snapshot exact: entries={manifest['entry_count']} "
            f"gaps={manifest['gap_count']} "
            f"digest={manifest['snapshot_digest']}"
        )
        return 0
    payload = snapshot if args.full else manifest
    sys.stdout.buffer.write(canonical_bytes(payload) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

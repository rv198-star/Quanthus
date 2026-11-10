"""Read-only inventory driver; output is evidence, never a product seal."""
from __future__ import annotations

import argparse
from pathlib import Path

from .base_build_inputs import BuildInputError, canonical, inventory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    source, destination = args.root.resolve(), args.output.resolve()
    if source == destination or source in destination.parents:
        raise BuildInputError("BUILD_RECEIPT_INSIDE_SOURCE")
    result = inventory(args.root, args.manifest_sha256, workers=args.workers)
    with args.output.open("xb") as stream:
        stream.write(canonical(result) + b"\n")
    print(result["status"], result["identity"], result["file_count"], result["bytes"])


if __name__ == "__main__":
    main()

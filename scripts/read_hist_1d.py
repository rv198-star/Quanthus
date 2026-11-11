"""Read one pinned Hist-1D daily partition anonymously."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--repo", default="rv192/quanthus-hist-1d")
    parser.add_argument("--file")
    args = parser.parse_args()
    hub = importlib.import_module("huggingface_hub")
    pq = importlib.import_module("pyarrow.parquet")
    filename = args.file
    if filename is None:
        paths = hub.HfApi(token=False).list_repo_files(
            args.repo, repo_type="dataset", revision=args.revision
        )
        filename = next(
            path
            for path in sorted(paths)
            if path.startswith(
                "payload/dwd_security_bar/interval_size=1d/observation_start_year=2005/"
            )
            and path.endswith(".parquet")
        )
    path = hub.hf_hub_download(
        args.repo, filename=filename, repo_type="dataset", revision=args.revision, token=False
    )
    from pathlib import Path

    parquet = pq.ParquetFile(path)
    print(
        json.dumps(
            {
                "revision": args.revision,
                "file": filename,
                "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                "rows": parquet.metadata.num_rows,
                "sample": parquet.read(columns=["listing_id", "observation_start", "close"])
                .slice(0, 3)
                .to_pylist(),
            },
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

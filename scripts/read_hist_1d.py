"""Read one pinned Hist-1D daily partition anonymously."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json


def choose_partition(paths: list[str], year: int, month: int | None = None) -> str:
    prefix = f"payload/dwd_security_bar/interval_size=1d/observation_start_year={year}/"
    if month is not None:
        prefix += f"observation_start_month={month}/"
    matches = sorted(
        path for path in paths if path.startswith(prefix) and path.endswith(".parquet")
    )
    if not matches:
        raise ValueError("DAILY_PARTITION_NOT_FOUND")
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", default="main")
    parser.add_argument("--repo", default="rv192/quanthus-hist-1d")
    parser.add_argument("--file")
    parser.add_argument("--year", default=2005, type=int)
    parser.add_argument("--month", type=int, choices=range(1, 13))
    args = parser.parse_args()
    hub = importlib.import_module("huggingface_hub")
    pq = importlib.import_module("pyarrow.parquet")
    revision = (
        hub.HfApi(token=False).repo_info(args.repo, repo_type="dataset", revision=args.revision).sha
    )
    manifest_path = hub.hf_hub_download(
        args.repo, "manifest.json", repo_type="dataset", revision=revision, token=False
    )
    from pathlib import Path

    manifest = json.loads(Path(manifest_path).read_bytes())
    members = {row["path"]: row for row in manifest["members"]}
    filename = args.file or choose_partition(list(members), args.year, args.month)
    if filename not in members or not filename.startswith("payload/dwd_security_bar/"):
        raise ValueError("DAILY_FILE_NOT_MANIFESTED")
    path = hub.hf_hub_download(
        args.repo, filename=filename, repo_type="dataset", revision=revision, token=False
    )
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if digest != members[filename]["sha256"]:
        raise ValueError("DAILY_FILE_SHA256_MISMATCH")
    parquet = pq.ParquetFile(path)
    print(
        json.dumps(
            {
                "revision": revision,
                "file": filename,
                "sha256": digest,
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

"""Resource benchmark for standard and annual-sharded Direct-DWD projection."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import date
from pathlib import Path
from typing import Any

from quanthus.backtest.direct_dwd_provider import (
    DATASETS,
    _materialize,
    materialize_sharded_direct_dwd,
)

BENCHMARK_SCHEMA = "quanthus.direct-dwd-shard-benchmark.v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _memory_value(name: str) -> int | None:
    try:
        value = Path("/sys/fs/cgroup", name).read_text(encoding="ascii").strip()
    except OSError:
        return None
    return int(value) if value.isdigit() else None


def run_benchmark(
    *,
    mode: str,
    output: Path,
    dwd_root: Path,
    correction_root: Path,
    history_start: date,
    execution_end: date,
    workers: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    milestones: dict[str, float] = {}
    milestone_details: dict[str, Any] = {}

    def progress(stage: str, details: Any) -> None:
        milestones.setdefault(stage, time.perf_counter() - started)
        milestone_details.setdefault(stage, details)

    if mode == "standard":
        output.mkdir(parents=True, exist_ok=False)
        counts = _materialize(
            output,
            dwd_root=dwd_root,
            correction_root=correction_root,
            history_start=history_start,
            execution_end=execution_end,
            progress=progress,
        )
    elif mode == "sharded":
        counts = materialize_sharded_direct_dwd(
            output,
            dwd_root=dwd_root,
            correction_root=correction_root,
            history_start=history_start,
            execution_end=execution_end,
            workers=workers,
            progress=progress,
        )
    else:
        raise ValueError("unsupported Direct-DWD benchmark mode")
    elapsed = time.perf_counter() - started
    files = {
        name: {
            "sha256": _sha256(output / "tables" / f"{name}.parquet"),
            "size_bytes": (output / "tables" / f"{name}.parquet").stat().st_size,
        }
        for name in DATASETS
    }
    return {
        "schema_version": BENCHMARK_SCHEMA,
        "mode": mode,
        "workers": workers,
        "history_start": history_start.isoformat(),
        "execution_end": execution_end.isoformat(),
        "elapsed_seconds": round(elapsed, 3),
        "milestones_seconds": {
            stage: round(value, 3) for stage, value in milestones.items()
        },
        "milestone_details": milestone_details,
        "counts": counts,
        "files": files,
        "memory": {
            "current_bytes": _memory_value("memory.current"),
            "peak_bytes": _memory_value("memory.peak"),
            "limit_bytes": _memory_value("memory.max"),
            "swap_current_bytes": _memory_value("memory.swap.current"),
            "swap_peak_bytes": _memory_value("memory.swap.peak"),
            "swap_limit_bytes": _memory_value("memory.swap.max"),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("standard", "sharded"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dwd-root", type=Path, required=True)
    parser.add_argument("--correction-root", type=Path, required=True)
    parser.add_argument("--history-start", type=date.fromisoformat, required=True)
    parser.add_argument("--execution-end", type=date.fromisoformat, required=True)
    parser.add_argument("--workers", type=int, choices=(1, 2, 3, 4), default=1)
    args = parser.parse_args()
    result = run_benchmark(
        mode=args.mode,
        output=args.output.resolve(),
        dwd_root=args.dwd_root.resolve(),
        correction_root=args.correction_root.resolve(),
        history_start=args.history_start,
        execution_end=args.execution_end,
        workers=args.workers,
    )
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

"""Bounded, generation-specific task implementations for the P4 Event batch."""

from __future__ import annotations

import hashlib
import json
import os
import resource
import shutil
import time
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

from quanthus.backtest.data_provider import load_replay_data_provider_config
from quanthus.backtest.direct_dwd_provider import (
    materialize_direct_dwd_date_partition,
    merge_direct_dwd_date_partitions,
    open_materialized_direct_dwd_session,
)
from quanthus.event_library.job_local import build_job_local_feature_panel
from quanthus.event_library.physical_scan import (
    iter_projected_candidate_series,
    load_p4_research_adm,
)
from quanthus.event_library.rule_card import load_candidate_rule_card
from quanthus.event_library.scan import scan_candidate_series_shard
from quanthus.event_library.source import load_event_scan_source_policy
from quanthus.event_library.splits import load_split_policy
from quanthus.feature_registry import load_feature_registry
from quanthus.policy import find_project_root

TASK_SCHEMA = "quanthus.p4-event-shard-task.v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _release_file_cache(paths: tuple[Path, ...], *, flush: bool) -> None:
    """Release cache charged to a long-lived 3 GiB Worker after one child task."""

    advice = getattr(os, "POSIX_FADV_DONTNEED", None)
    advise = getattr(os, "posix_fadvise", None)
    if advice is None or advise is None:
        raise RuntimeError("event shard Worker requires POSIX_FADV_DONTNEED")
    files: set[Path] = set()
    for path in paths:
        if path.is_file():
            files.add(path.resolve())
        elif path.is_dir():
            files.update(item.resolve() for item in path.rglob("*") if item.is_file())
    for path in sorted(files):
        descriptor = os.open(path, os.O_RDONLY)
        try:
            if flush:
                os.fsync(descriptor)
            advise(descriptor, 0, 0, advice)
        finally:
            os.close(descriptor)


def _project_path(value: object, project_root: Path) -> Path:
    path = Path(str(value))
    return path.resolve() if path.is_absolute() else (project_root / path).resolve()


def _date_partition(payload: Mapping[str, Any]) -> dict[str, Any]:
    partition_id = str(payload["partition_id"])
    root = Path(str(payload["output_root"]))
    observed_id, counts, elapsed = materialize_direct_dwd_date_partition(
        partition_id,
        str(root),
        str(payload["dwd_root"]),
        str(payload["correction_root"]),
        date.fromisoformat(str(payload["scan_start"])),
        date.fromisoformat(str(payload["output_start"])),
        date.fromisoformat(str(payload["output_end"])),
    )
    if observed_id != partition_id:
        raise RuntimeError("date partition identity differs")
    _release_file_cache((root,), flush=True)
    _release_file_cache(
        (Path(str(payload["dwd_root"])), Path(str(payload["correction_root"]))),
        flush=False,
    )
    return {
        "output_root": str(root.resolve()),
        "counts": counts,
        "elapsed_seconds": round(elapsed, 3),
    }


def _merge(payload: Mapping[str, Any]) -> dict[str, Any]:
    output = Path(str(payload["output"]))
    output.parent.mkdir(parents=True, exist_ok=False)
    dataset = merge_direct_dwd_date_partitions(
        str(payload["dataset"]),
        tuple(str(item) for item in payload["paths"]),
        str(output),
        str(payload["spill"]),
    )
    _release_file_cache((output,), flush=True)
    _release_file_cache(tuple(Path(str(item)) for item in payload["paths"]), flush=False)
    return {
        "dataset": dataset,
        "output": str(output.resolve()),
        "sha256": _sha256(output),
        "bytes": output.stat().st_size,
    }


def _scan(payload: Mapping[str, Any], project_root: Path) -> dict[str, Any]:
    projection = load_p4_research_adm(
        Path(str(payload["adm_root"])), project_root=project_root
    )
    source = load_event_scan_source_policy(
        _project_path(payload["source_policy"], project_root),
        release_path=project_root / "config/releases/v2-build202607.json",
        provider_path=_project_path(payload["provider_config"], project_root),
    )
    card = load_candidate_rule_card(_project_path(payload["rule_card"], project_root))
    split = load_split_policy(_project_path(payload["split_policy"], project_root))
    security_ids = payload["security_ids"]
    if not isinstance(security_ids, list) or any(
        not isinstance(item, str) for item in security_ids
    ):
        raise RuntimeError("candidate shard security ids are invalid")
    spill = Path(str(payload["spill"]))
    try:
        shard = scan_candidate_series_shard(
            iter_projected_candidate_series(
                projection,
                source_policy=source,
                industry_mapping=projection.industry_mapping,
                allowed_security_ids=tuple(security_ids),
                duckdb_memory_limit="1GB",
                duckdb_temp_directory=spill,
            ),
            trading_dates=projection.trading_dates,
            snapshot_id=projection.snapshot_id,
            snapshot_manifest_digest=projection.manifest_sha256,
            card=card,
            split_policy=split,
            source_policy=source,
        )
        if shard.security_ids != tuple(sorted(security_ids)):
            raise RuntimeError("candidate shard security ownership differs")
    finally:
        shutil.rmtree(spill, ignore_errors=True)
    output = Path(str(payload["output"]))
    _write_json(output, shard.to_dict())
    _release_file_cache((projection.candidate_path,), flush=False)
    return {
        "output": str(output.resolve()),
        "sha256": _sha256(output),
        "bytes": output.stat().st_size,
        "security_count": len(shard.security_ids),
        "decoded_rows": shard.decoded_rows,
        "raw_candidates": len(shard.raw_candidates),
    }


def _feature_panel(payload: Mapping[str, Any], project_root: Path) -> dict[str, Any]:
    provider_config = load_replay_data_provider_config(
        _project_path(payload["provider_config"], project_root)
    )
    registry = load_feature_registry(_project_path(payload["feature_registry"], project_root))
    counts = payload["counts"]
    if not isinstance(counts, Mapping):
        raise RuntimeError("feature panel DWD counts are invalid")
    scope_start = date.fromisoformat(str(payload["scope_start"]))
    scope_end = date.fromisoformat(str(payload["scope_end"]))
    output = Path(str(payload["output"]))
    output.parent.mkdir(parents=True, exist_ok=False)
    with open_materialized_direct_dwd_session(
        provider_config,
        root=Path(str(payload["projection_root"])),
        scope_start=provider_config.scope.decision_start,
        scope_end=provider_config.scope.decision_end,
        counts={str(name): int(count) for name, count in counts.items()},
    ) as session:
        panel = build_job_local_feature_panel(
            session=session,
            provider_config=provider_config,
            registry=registry,
            scope_start=scope_start,
            scope_end=scope_end,
            output=output,
            duckdb_memory_limit="1500MB",
            duckdb_threads=1,
            release_source_cache=True,
            output_order="security-date",
            spill_cache_release_interval_seconds=1.0,
        )
    receipt = Path(str(payload["receipt"]))
    _write_json(receipt, panel.to_receipt())
    panel_sha256 = _sha256(panel.path)
    _release_file_cache((panel.path,), flush=True)
    _release_file_cache((Path(str(payload["projection_root"])),), flush=False)
    return {
        "output": str(panel.path),
        "receipt": str(receipt.resolve()),
        "sha256": panel_sha256,
        "bytes": panel.parquet_bytes,
        "rows": panel.report.row_count,
    }


def execute_event_shard_task(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Execute one admitted shard task and return its bounded result reference."""

    if payload.get("schema_version") != TASK_SCHEMA:
        raise RuntimeError("event shard task schema differs")
    kind = payload.get("kind")
    project_root = find_project_root()
    started = time.perf_counter()
    if kind == "direct-dwd-date":
        result = _date_partition(payload)
    elif kind == "direct-dwd-merge":
        result = _merge(payload)
    elif kind == "candidate-scan":
        result = _scan(payload, project_root)
    elif kind == "feature-panel":
        result = _feature_panel(payload, project_root)
    else:
        raise RuntimeError("event shard task kind is not admitted")
    return {
        **result,
        "kind": kind,
        "wall_seconds": round(time.perf_counter() - started, 3),
        "process_peak_rss_bytes": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        * 1024,
    }

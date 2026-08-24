"""Bounded benchmark for deterministic parallel P4 candidate scanning."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from quanthus.event_library.physical_scan import (
    P4ResearchADM,
    iter_projected_candidate_series,
)
from quanthus.event_library.rule_card import load_candidate_rule_card
from quanthus.event_library.scan import (
    CandidateScanShard,
    ScannedCandidate,
    finalize_candidate_scan_shards,
    scan_candidate_series_shard,
)
from quanthus.event_library.source import load_event_scan_source_policy
from quanthus.event_library.splits import load_split_policy
from quanthus.industry_mapping import load_static_industry_mapping
from quanthus.policy import find_project_root
from quanthus.research_adm import load_p4_research_adm_policy

BENCHMARK_SCHEMA = "quanthus.p4-candidate-shard-benchmark.v1"


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


def _load_projection(event_root: Path, project_root: Path) -> P4ResearchADM:
    adm_root = event_root / "adm"
    manifest_path = adm_root / "adm.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("ADM manifest must be an object")
    projection_raw = raw.get("candidate_projection")
    industry_raw = raw.get("static_industry_projection")
    if not isinstance(projection_raw, dict) or not isinstance(industry_raw, dict):
        raise ValueError("ADM manifest projection bindings are missing")
    policy = load_p4_research_adm_policy(project_root / "config/p4-research-adm.v1.json")
    candidate_path = adm_root / str(projection_raw["path"])
    industry_mapping = load_static_industry_mapping(
        adm_root / str(industry_raw["path"]),
        expected_version=str(industry_raw["mapping_version"]),
    )
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise RuntimeError("candidate shard benchmark requires duckdb") from exc
    connection = duckdb.connect(":memory:")
    try:
        trading_dates = tuple(
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT session_date FROM read_parquet(?) ORDER BY session_date",
                [str(candidate_path)],
            ).fetchall()
        )
    finally:
        connection.close()
    content_digest = str(raw["content_digest"])
    return P4ResearchADM(
        root=adm_root.resolve(),
        policy=policy,
        candidate_path=candidate_path.resolve(),
        industry_mapping=industry_mapping,
        manifest_path=manifest_path.resolve(),
        manifest_sha256=_sha256(manifest_path),
        content_digest=content_digest,
        snapshot_id=(
            "p4-adm-development-" + content_digest.removeprefix("sha256:")[:24]
        ),
        trading_dates=trading_dates,
        row_count=int(projection_raw["row_count"]),
    )


def _balanced_buckets(candidate_path: Path, workers: int) -> tuple[tuple[str, ...], ...]:
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise RuntimeError("candidate shard benchmark requires duckdb") from exc
    connection = duckdb.connect(":memory:")
    try:
        weighted = tuple(
            (str(row[0]), int(row[1]))
            for row in connection.execute(
                """
                SELECT dwd_security_id, count(*) AS row_count
                FROM read_parquet(?)
                GROUP BY dwd_security_id
                ORDER BY row_count DESC, dwd_security_id
                """,
                [str(candidate_path)],
            ).fetchall()
        )
    finally:
        connection.close()
    if workers < 1 or workers > len(weighted):
        raise ValueError("workers must fit the projected security count")
    buckets: list[list[str]] = [[] for _ in range(workers)]
    weights = [0] * workers
    for security_id, row_count in weighted:
        selected = min(range(workers), key=lambda index: (weights[index], index))
        buckets[selected].append(security_id)
        weights[selected] += row_count
    return tuple(tuple(sorted(bucket)) for bucket in buckets)


def _load_scan_contract(project_root: Path) -> tuple[Any, Any, Any]:
    source = load_event_scan_source_policy(
        project_root / "config/p4-event-scan-source.research.v1.json",
        release_path=project_root / "config/releases/v2-build202607.json",
        provider_path=project_root / "config/replay-data-provider.development.v2.json",
    )
    card = load_candidate_rule_card(
        project_root / "config/p4-candidate-price-volume-transition.research.v1.json"
    )
    split = load_split_policy(
        project_root / "config/p4-event-split-policy.research.v1.json"
    )
    return source, card, split


def _scan_bucket(
    event_root: str,
    project_root: str,
    security_ids: tuple[str, ...] | None,
) -> CandidateScanShard:
    project = Path(project_root)
    projection = _load_projection(Path(event_root), project)
    source, card, split = _load_scan_contract(project)
    return scan_candidate_series_shard(
        iter_projected_candidate_series(
            projection,
            source_policy=source,
            industry_mapping=projection.industry_mapping,
            allowed_security_ids=security_ids,
        ),
        trading_dates=projection.trading_dates,
        snapshot_id=projection.snapshot_id,
        snapshot_manifest_digest=projection.manifest_sha256,
        card=card,
        split_policy=split,
        source_policy=source,
    )


def _candidate_payload(candidate: ScannedCandidate) -> dict[str, Any]:
    return {
        "event": candidate.record.to_dict(),
        "listing_id": candidate.listing_id,
        "listing_code": candidate.listing_code,
        "company_id": candidate.company_id,
        "static_level1_industry_id": candidate.static_level1_industry_id,
        "feature_start": candidate.feature_start.isoformat(),
        "entry_date": candidate.entry_date.isoformat(),
        "feature_evidence": candidate.feature_evidence.to_dict(),
    }


def _candidate_jsonl_digest(candidates: tuple[ScannedCandidate, ...]) -> str:
    digest = hashlib.sha256()
    for candidate in candidates:
        digest.update(
            json.dumps(
                _candidate_payload(candidate),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        digest.update(b"\n")
    return f"sha256:{digest.hexdigest()}"


def run_benchmark(event_root: Path, workers: int) -> dict[str, Any]:
    project_root = find_project_root()
    projection = _load_projection(event_root, project_root)
    source, card, split = _load_scan_contract(project_root)
    planning_started = time.perf_counter()
    buckets = _balanced_buckets(projection.candidate_path, workers)
    planning_elapsed = time.perf_counter() - planning_started
    started = time.perf_counter()
    shards: tuple[CandidateScanShard, ...]
    if workers == 1:
        shards = (_scan_bucket(str(event_root), str(project_root), None),)
    else:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = tuple(
                executor.submit(
                    _scan_bucket,
                    str(event_root),
                    str(project_root),
                    bucket,
                )
                for bucket in buckets
            )
            shards = tuple(future.result() for future in futures)
    scan_elapsed = time.perf_counter() - started
    finalize_started = time.perf_counter()
    result = finalize_candidate_scan_shards(
        shards,
        trading_dates=projection.trading_dates,
        snapshot_id=projection.snapshot_id,
        snapshot_manifest_digest=projection.manifest_sha256,
        card=card,
        split_policy=split,
        source_policy=source,
    )
    finalize_elapsed = time.perf_counter() - finalize_started
    baseline_events = event_root / "events.jsonl"
    observed_digest = _candidate_jsonl_digest(result.candidates)
    expected_digest = _sha256(baseline_events)
    return {
        "schema_version": BENCHMARK_SCHEMA,
        "workers": workers,
        "pid": os.getpid(),
        "planning_elapsed_seconds": round(planning_elapsed, 3),
        "scan_elapsed_seconds": round(scan_elapsed, 3),
        "finalization_elapsed_seconds": round(finalize_elapsed, 3),
        "total_elapsed_seconds": round(
            planning_elapsed + scan_elapsed + finalize_elapsed, 3
        ),
        "candidate_events": result.counters.candidate_events,
        "counters": result.counters.to_dict(),
        "candidate_jsonl_sha256": observed_digest,
        "baseline_candidate_jsonl_sha256": expected_digest,
        "byte_identical_candidate_jsonl": observed_digest == expected_digest,
        "buckets": [
            {
                "security_count": len(bucket),
                "decoded_rows": shard.decoded_rows,
                "series_count": shard.series_count,
                "raw_triggers": len(shard.raw_candidates),
                "row_decode_elapsed_seconds": round(
                    shard.row_decode_elapsed_seconds, 3
                ),
                "security_scan_elapsed_seconds": round(
                    shard.security_scan_elapsed_seconds, 3
                ),
            }
            for bucket, shard in zip(buckets, shards, strict=True)
        ],
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
    parser.add_argument("--event-root", type=Path, required=True)
    parser.add_argument("--workers", type=int, choices=(1, 2, 3, 4), required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run_benchmark(args.event_root.resolve(), args.workers)
    rendered = json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()

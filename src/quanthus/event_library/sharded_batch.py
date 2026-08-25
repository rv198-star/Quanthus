"""PostgreSQL-orchestrated low-RAM implementation of the governed P4 batch."""

from __future__ import annotations

import argparse
import json
import os
import resource
import shutil
import time
from collections.abc import Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.backtest.data_provider import load_replay_data_provider_config
from quanthus.backtest.direct_dwd_provider import (
    DATASETS,
    assemble_direct_dwd_projection,
    direct_dwd_partition_specs,
    open_materialized_direct_dwd_session,
)
from quanthus.event_library.comparison_report import build_physical_comparison_report
from quanthus.event_library.job_local import (
    JobLocalFeaturePanel,
    merge_job_local_feature_panel_parts,
    plan_feature_panel_security_buckets,
)
from quanthus.event_library.label_corpus import build_physical_label_corpus
from quanthus.event_library.physical_scan import (
    load_p4_research_adm,
    materialize_p4_research_adm,
    plan_candidate_security_buckets,
    publish_event_candidate_shards,
)
from quanthus.event_library.reported_batch import (
    REPORTED_BATCH_RUNTIME_SCHEMA,
    _memory_sample,
    _tree_bytes,
    _write_progress_receipt,
    _write_runtime_receipt,
)
from quanthus.event_library.rule_card import load_candidate_rule_card
from quanthus.event_library.scan import CandidateScanShard
from quanthus.event_library.shard_tasks import TASK_SCHEMA
from quanthus.event_library.source import load_event_scan_source_policy
from quanthus.event_library.splits import load_split_policy
from quanthus.feature_registry import load_feature_registry
from quanthus.platform.jobs import input_digest
from quanthus.platform.models import JobShardRecord
from quanthus.platform.persistence import PostgresPlatformStore
from quanthus.policy import find_project_root

_DEVELOPMENT_START = date(2012, 1, 4)
_DECISION_END = date(2021, 12, 30)
_EXECUTION_END = date(2021, 12, 31)
SHARDED_DWD_DAILY_DATASETS = (
    "daily_ohlcv_none",
    "daily_universe",
    "price_limit_execution_daily",
    "trading_calendar",
)
MINIMUM_FEATURE_PANEL_PARTS = 2


def feature_panel_partition_count(worker_target: int) -> int:
    """Keep numerical work units stable between one- and two-Worker runs."""

    if worker_target not in {1, 2, 3, 4}:
        raise ValueError("feature panel Worker target is invalid")
    return max(MINIMUM_FEATURE_PANEL_PARTS, worker_target)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the sharded P4 reported batch")
    for name in (
        "source-policy",
        "provider-config",
        "rule-card",
        "split-policy",
        "outcome-card",
        "structural-break-card",
        "feature-registry",
        "report-policy",
        "event-output",
        "label-output",
        "report-output",
        "runtime-receipt",
        "progress-receipt",
        "code-revision",
        "job-id",
    ):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--parent-generation", type=int, required=True)
    parser.add_argument("--shard-target", type=int, choices=(1, 2, 3, 4), required=True)
    return parser


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object at {path}")
    return value


def _stage_receipt(name: str, elapsed: float, **details: Any) -> dict[str, Any]:
    return {
        "stage": name,
        "elapsed_seconds": round(elapsed, 3),
        "memory": _memory_sample(),
        **details,
    }


class ShardedBatch:
    def __init__(
        self,
        *,
        store: PostgresPlatformStore,
        job_id: str,
        parent_generation: int,
        shard_target: int,
        run_root: Path,
        progress_path: Path,
    ) -> None:
        self.store = store
        self.job_id = job_id
        self.parent_generation = parent_generation
        self.shard_target = shard_target
        self.run_root = run_root.resolve()
        self.progress_path = progress_path.resolve()
        self.shard_root = self.run_root / ".shards" / job_id

    def progress(self, stage: str, percent: int, **detail: Any) -> None:
        _write_progress_receipt(
            self.progress_path,
            stage=stage,
            progress=percent,
            detail={"shard_target": self.shard_target, **detail},
        )

    def dispatch(
        self,
        stage: str,
        payloads: Sequence[dict[str, Any]],
        *,
        progress_start: int,
        progress_end: int,
    ) -> tuple[dict[str, Any], ...]:
        now = datetime.now(timezone.utc)
        records = tuple(
            JobShardRecord(
                parent_job_id=self.job_id,
                parent_claim_generation=self.parent_generation,
                shard_id=f"{stage}-{ordinal:03d}",
                stage=stage,
                ordinal=ordinal,
                payload=payload,
                input_digest=input_digest(payload),
                state="queued",
                attempt=0,
                result_ref=None,
                error_reason=None,
                created_at=now,
                updated_at=now,
                available_at=now,
            )
            for ordinal, payload in enumerate(payloads)
        )
        self.store.create_job_shards(self.job_id, self.parent_generation, records)
        last_complete = -1
        while True:
            parent = self.store.get_job(self.job_id)
            if (
                parent is None
                or parent.claim_generation != self.parent_generation
                or parent.state != "running"
            ):
                raise RuntimeError("stale_claim_generation")
            if parent.cancel_requested:
                self.store.cancel_job_shards(self.job_id, self.parent_generation)
                raise RuntimeError("event_job_canceled")
            observed = self.store.list_job_shards(self.job_id, stage)
            failed = next((item for item in observed if item.state == "failed"), None)
            if failed is not None:
                raise RuntimeError(
                    f"event_shard_failed:{failed.shard_id}:{failed.error_reason}"
                )
            completed = sum(item.state == "succeeded" for item in observed)
            if completed != last_complete:
                fraction = completed / len(records)
                percent = progress_start + int((progress_end - progress_start) * fraction)
                self.progress(
                    f"sharded-{stage}",
                    percent,
                    completed=completed,
                    total=len(records),
                )
                last_complete = completed
            if completed == len(records):
                results = tuple(item.result_ref for item in observed)
                if any(item is None for item in results):
                    raise RuntimeError("successful event shard has no result reference")
                return tuple(item for item in results if item is not None)
            time.sleep(1)


def run_sharded_batch(args: argparse.Namespace) -> None:
    project = find_project_root()
    dsn = os.environ.get("QUANTHUS_PLATFORM_DATABASE_URL", "").strip()
    run_root = Path(os.environ.get("QUANTHUS_EVENT_RUN_ROOT", "/data/event-runs"))
    if not dsn:
        raise RuntimeError("QUANTHUS_PLATFORM_DATABASE_URL is required")
    batch = ShardedBatch(
        store=PostgresPlatformStore(dsn),
        job_id=args.job_id,
        parent_generation=args.parent_generation,
        shard_target=args.shard_target,
        run_root=run_root,
        progress_path=Path(args.progress_receipt),
    )
    if batch.shard_root.exists():
        shutil.rmtree(batch.shard_root)
    batch.shard_root.mkdir(parents=True)
    stages: list[dict[str, Any]] = []
    started = time.perf_counter()
    provider_path = (project / args.provider_config).resolve()
    provider_config = load_replay_data_provider_config(provider_path)
    registry = load_feature_registry((project / args.feature_registry).resolve())
    if provider_config.correction_root is None or provider_config.history_start is None:
        raise RuntimeError("sharded Event batch requires a direct DWD correction")

    try:
        specs = direct_dwd_partition_specs(provider_config.history_start, _EXECUTION_END)
        date_started = time.perf_counter()
        date_payloads = tuple(
            {
                "schema_version": TASK_SCHEMA,
                "kind": "direct-dwd-date",
                "partition_id": spec.partition_id,
                "dwd_root": str(provider_config.dwd_root),
                "correction_root": str(provider_config.correction_root),
                "scan_start": spec.scan_start.isoformat(),
                "output_start": spec.output_start.isoformat(),
                "output_end": spec.output_end.isoformat(),
                "output_root": str(
                    batch.shard_root
                    / "dwd-date"
                    / spec.partition_id
                    / "generation-{claim_generation}"
                ),
            }
            for spec in sorted(specs, key=lambda item: item.output_end, reverse=True)
        )
        date_results = batch.dispatch(
            "dwd-date", date_payloads, progress_start=6, progress_end=34
        )
        by_partition = {
            str(payload["partition_id"]): result
            for payload, result in zip(date_payloads, date_results, strict=True)
        }
        stages.append(
            _stage_receipt(
                "provider.direct-dwd-date-shards",
                time.perf_counter() - date_started,
                partitions=len(specs),
                workers=args.shard_target,
            )
        )

        merge_started = time.perf_counter()
        merge_payloads = tuple(
            {
                "schema_version": TASK_SCHEMA,
                "kind": "direct-dwd-merge",
                "dataset": dataset,
                "paths": [
                    str(
                        Path(str(by_partition[spec.partition_id]["output_root"]))
                        / "tables"
                        / f"{dataset}.parquet"
                    )
                    for spec in specs
                ],
                "output": str(
                    batch.shard_root
                    / "dwd-merge"
                    / dataset
                    / "generation-{claim_generation}"
                    / f"{dataset}.parquet"
                ),
                "spill": str(
                    batch.shard_root
                    / "dwd-merge"
                    / dataset
                    / "generation-{claim_generation}"
                    / "spill"
                ),
            }
            for dataset in SHARDED_DWD_DAILY_DATASETS
        )
        merge_results = batch.dispatch(
            "dwd-merge", merge_payloads, progress_start=35, progress_end=43
        )
        merged_paths = {
            str(result["dataset"]): Path(str(result["output"]))
            for result in merge_results
        }
        stages.append(
            _stage_receipt(
                "provider.direct-dwd-merge-shards",
                time.perf_counter() - merge_started,
                datasets=len(merged_paths),
            )
        )

        projection_started = time.perf_counter()
        projection_root = batch.shard_root / "direct-dwd-projection"
        partition_roots = tuple(
            Path(str(by_partition[spec.partition_id]["output_root"])) for spec in specs
        )
        partition_counts = tuple(
            {
                str(name): int(value)
                for name, value in by_partition[spec.partition_id]["counts"].items()
            }
            for spec in specs
        )
        counts = assemble_direct_dwd_projection(
            projection_root,
            specs=specs,
            partition_roots=partition_roots,
            partition_counts=partition_counts,
            merged_paths=merged_paths,
        )
        stages.append(
            _stage_receipt(
                "provider.direct-dwd-projection",
                time.perf_counter() - projection_started,
                rows=sum(counts.values()),
                bytes=_tree_bytes(projection_root),
                disposition="built_once",
            )
        )
        batch.progress("sharded-dwd-ready", 45, datasets=len(DATASETS))

        with open_materialized_direct_dwd_session(
            provider_config,
            root=projection_root,
            scope_start=_DEVELOPMENT_START,
            scope_end=_DECISION_END,
            counts=counts,
        ) as session:
            source = load_event_scan_source_policy(
                (project / args.source_policy).resolve(),
                release_path=project / "config/releases/v2-build202607.json",
                provider_path=provider_path,
            )
            card = load_candidate_rule_card((project / args.rule_card).resolve())
            split = load_split_policy((project / args.split_policy).resolve())
            event_started = time.perf_counter()
            package_staging = batch.shard_root / "event-package"
            projection = materialize_p4_research_adm(
                reader=session.reader,
                dwd_root=provider_config.dwd_root,
                source_policy=source,
                provider_identity=session.identity,
                output_root=package_staging / "adm",
            )
            buckets = plan_candidate_security_buckets(projection, args.shard_target)
            scan_payloads = tuple(
                {
                    "schema_version": TASK_SCHEMA,
                    "kind": "candidate-scan",
                    "adm_root": str(projection.root),
                    "source_policy": args.source_policy,
                    "provider_config": args.provider_config,
                    "rule_card": args.rule_card,
                    "split_policy": args.split_policy,
                    "security_ids": list(bucket),
                    "output": str(
                        batch.shard_root
                        / "candidate-scan"
                        / f"bucket-{ordinal:03d}"
                        / "generation-{claim_generation}"
                        / "scan.json"
                    ),
                    "spill": str(
                        batch.shard_root
                        / "candidate-scan"
                        / f"bucket-{ordinal:03d}"
                        / "generation-{claim_generation}"
                        / "spill"
                    ),
                }
                for ordinal, bucket in enumerate(buckets)
            )
            scan_results = batch.dispatch(
                "candidate-scan", scan_payloads, progress_start=48, progress_end=78
            )
            scan_shards = tuple(
                CandidateScanShard.from_dict(_read_object(Path(str(result["output"]))))
                for result in scan_results
            )
            event = publish_event_candidate_shards(
                projection=load_p4_research_adm(projection.root, project_root=project),
                reader=session.reader,
                source_policy=source,
                card=card,
                split_policy=split,
                shards=scan_shards,
                staging=package_staging,
                output=Path(args.event_output),
                code_revision=args.code_revision,
            )
            stages.append(
                _stage_receipt(
                    "event-library",
                    time.perf_counter() - event_started,
                    rows=event.candidate_events,
                    bytes=_tree_bytes(event.root),
                    provider_disposition="reused",
                    candidate_shards=args.shard_target,
                )
            )

            panel_started = time.perf_counter()
            panel_partition_count = feature_panel_partition_count(args.shard_target)
            panel_buckets = plan_feature_panel_security_buckets(
                projection_root,
                panel_partition_count,
                scope_start=_DEVELOPMENT_START,
                scope_end=_EXECUTION_END,
            )
            panel_payloads = tuple(
                {
                    "schema_version": TASK_SCHEMA,
                    "kind": "feature-panel",
                    "provider_config": args.provider_config,
                    "feature_registry": args.feature_registry,
                    "projection_root": str(projection_root),
                    "counts": counts,
                    "scope_start": _DEVELOPMENT_START.isoformat(),
                    "scope_end": _EXECUTION_END.isoformat(),
                    "security_ids": list(bucket),
                    "output": str(
                        batch.shard_root
                        / "feature-panel"
                        / f"part-{ordinal:03d}"
                        / "generation-{claim_generation}"
                        / "panel.parquet"
                    ),
                    "receipt": str(
                        batch.shard_root
                        / "feature-panel"
                        / f"part-{ordinal:03d}"
                        / "generation-{claim_generation}"
                        / "receipt.json"
                    ),
                }
                for ordinal, bucket in enumerate(panel_buckets)
            )
            panel_results = batch.dispatch(
                "feature-panel", panel_payloads, progress_start=80, progress_end=86
            )
            panel_parts = tuple(
                JobLocalFeaturePanel.from_receipt(
                    _read_object(Path(str(result["receipt"])))
                )
                for result in panel_results
            )
            label_panel = merge_job_local_feature_panel_parts(
                panel_parts,
                panel_buckets,
                output_root=batch.shard_root / "feature-panel" / "accepted",
            )
            panel_elapsed = time.perf_counter() - panel_started
            stages.append(
                _stage_receipt(
                    "feature-panel.label-scope",
                    panel_elapsed,
                    rows=label_panel.report.row_count,
                    bytes=label_panel.parquet_bytes,
                    disposition="built_once_sharded_tmp",
                    parts=len(panel_parts),
                    logical_partitions=len(panel_buckets),
                    scope_end=_EXECUTION_END.isoformat(),
                )
            )
            report_panel_started = time.perf_counter()
            report_panel = label_panel.scoped(
                scope_start=_DEVELOPMENT_START,
                scope_end=_DECISION_END,
            )
            stages.append(
                _stage_receipt(
                    "feature-panel.report-scope",
                    time.perf_counter() - report_panel_started,
                    rows=report_panel.report.row_count,
                    bytes=report_panel.parquet_bytes,
                    disposition="derived_scope_view_tmp",
                    parts=len(panel_parts),
                    logical_partitions=len(panel_buckets),
                    scope_end=_DECISION_END.isoformat(),
                )
            )

            label_started = time.perf_counter()
            label = build_physical_label_corpus(
                event_root=event.root,
                provider_config_path=provider_path,
                outcome_card_path=(project / args.outcome_card).resolve(),
                structural_card_path=(project / args.structural_break_card).resolve(),
                feature_registry_path=(project / args.feature_registry).resolve(),
                output=Path(args.label_output),
                code_revision=args.code_revision,
                provider_session=session,
                feature_panel=label_panel,
            )
            stages.append(
                _stage_receipt(
                    "label-corpus",
                    time.perf_counter() - label_started,
                    rows=label.total_events,
                    bytes=_tree_bytes(label.root),
                )
            )
            batch.progress("sharded-label-ready", 87, rows=label.total_events)

            report_started = time.perf_counter()
            report = build_physical_comparison_report(
                event_root=event.root,
                label_root=label.root,
                provider_config_path=provider_path,
                feature_registry_path=(project / args.feature_registry).resolve(),
                report_policy_path=(project / args.report_policy).resolve(),
                output=Path(args.report_output),
                code_revision=args.code_revision,
                provider_session=session,
                feature_panel=report_panel,
            )
            stages.append(
                _stage_receipt(
                    "comparison-report",
                    time.perf_counter() - report_started,
                    rows=report.three_class_events,
                    bytes=_tree_bytes(report.root),
                )
            )
            batch.progress("sharded-report-ready", 89, rows=report.three_class_events)

        runtime = {
            "schema_version": REPORTED_BATCH_RUNTIME_SCHEMA,
            "total_elapsed_seconds": round(time.perf_counter() - started, 3),
            "max_rss_bytes": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            * 1024,
            "memory": _memory_sample(),
            "provider_config_digest": provider_config.digest,
            "feature_registry_digest": registry.digest,
            "scope": {
                "start": _DEVELOPMENT_START.isoformat(),
                "decision_end": _DECISION_END.isoformat(),
                "execution_end": _EXECUTION_END.isoformat(),
            },
            "reuse": {
                "provider_materializations": 1,
                "feature_panel_materializations": 1,
                "cross_job_cache": False,
                "adm_published": False,
            },
            "sharded_execution": {
                "worker_memory_gib": 3,
                "target": args.shard_target,
                "feature_panel_parts": len(panel_parts),
                "job_id": args.job_id,
            },
            "stages": stages,
        }
        _write_runtime_receipt(Path(args.runtime_receipt), runtime)
    finally:
        try:
            batch.store.cancel_job_shards(args.job_id, args.parent_generation)
        except RuntimeError as exc:
            if str(exc) != "stale_claim_generation":
                raise
        shutil.rmtree(batch.shard_root, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    run_sharded_batch(_parser().parse_args(argv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

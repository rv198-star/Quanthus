"""Composite P4 batch with one DWD projection and two scope-bound feature panels."""

from __future__ import annotations

import json
import os
import resource
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, TypeVar

from quanthus.backtest.data_provider import ReplayDataProvider, load_replay_data_provider_config
from quanthus.event_library.comparison_report import (
    ComparisonReportResult,
    build_physical_comparison_report,
)
from quanthus.event_library.job_local import build_job_local_feature_panel
from quanthus.event_library.label_corpus import LabelCorpusResult, build_physical_label_corpus
from quanthus.event_library.physical_scan import (
    PhysicalEventScanResult,
    build_physical_event_scan,
)
from quanthus.feature_registry import load_feature_registry

REPORTED_BATCH_RUNTIME_SCHEMA = "quanthus.p4-reported-batch-runtime.v1"
REPORTED_BATCH_PROGRESS_SCHEMA = "quanthus.p4-reported-batch-progress.v1"
_DEVELOPMENT_START = date(2012, 1, 4)
_DECISION_END = date(2021, 12, 30)
_EXECUTION_END = date(2021, 12, 31)
T = TypeVar("T")


@dataclass(frozen=True)
class ReportedBatchResult:
    event: PhysicalEventScanResult
    label: LabelCorpusResult
    report: ComparisonReportResult
    runtime: Mapping[str, Any]


def _tree_bytes(root: Path) -> int:
    if root.is_file():
        return root.stat().st_size
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _run_stage(
    stages: list[dict[str, Any]],
    stage: str,
    operation: Callable[[], T],
    details: Callable[[T], Mapping[str, Any]],
) -> T:
    started = time.perf_counter()
    result = operation()
    stages.append(
        {
            "stage": stage,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            **details(result),
        }
    )
    return result


def _write_runtime_receipt(path: Path, receipt: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite P4 runtime receipt: {path}")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(receipt, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_progress_receipt(
    path: Path,
    *,
    stage: str,
    progress: int,
    detail: Mapping[str, Any],
) -> None:
    if not 5 <= progress <= 89:
        raise ValueError("reported batch child progress must be between 5 and 89")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(
                {
                    "schema_version": REPORTED_BATCH_PROGRESS_SCHEMA,
                    "stage": stage,
                    "progress": progress,
                    "detail": dict(detail),
                },
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def build_physical_reported_event_batch(
    *,
    source_policy_path: Path,
    provider_config_path: Path,
    rule_card_path: Path,
    split_policy_path: Path,
    outcome_card_path: Path,
    structural_card_path: Path,
    feature_registry_path: Path,
    report_policy_path: Path,
    event_output: Path,
    label_output: Path,
    report_output: Path,
    code_revision: str,
    runtime_receipt_path: Path | None = None,
    progress_receipt_path: Path | None = None,
) -> ReportedBatchResult:
    """Build the complete P4 result with Job-scoped prerequisite reuse."""

    if not code_revision.strip():
        raise ValueError("code_revision must not be empty")
    provider_config = load_replay_data_provider_config(provider_config_path)
    if provider_config.mode != "direct-dwd-job-local":
        raise ValueError("P4 reported batch requires direct DWD job-local mode")
    registry = load_feature_registry(feature_registry_path)
    provider = ReplayDataProvider(provider_config)
    stages: list[dict[str, Any]] = []
    batch_started = time.perf_counter()
    provider_stage_started = batch_started

    def publish_progress(stage: str, progress: int, details: Mapping[str, Any]) -> None:
        if progress_receipt_path is not None:
            _write_progress_receipt(
                progress_receipt_path,
                stage=stage,
                progress=progress,
                detail=details,
            )

    publish_progress("validating-reported-batch-input", 5, {})

    def provider_progress(stage: str, details: Mapping[str, Any]) -> None:
        nonlocal provider_stage_started
        now = time.perf_counter()
        stages.append(
            {
                "stage": f"provider.{stage}",
                "elapsed_seconds": round(now - provider_stage_started, 3),
                "disposition": "built",
                **dict(details),
            }
        )
        provider_stage_started = now
        provider_percent = {
            "dwd-authority-validated": 6,
            "dwd-source-scan-ready": 7,
            "dwd-market-projection-ready": 9,
            "dwd-action-projection-ready": 10,
            "dwd-projection-validated": 11,
        }.get(stage, 6)
        publish_progress(f"provider-{stage}", provider_percent, details)

    def event_progress(stage: str, details: Mapping[str, Any]) -> None:
        observed = dict(details)
        terminal = observed.pop("terminal", None) is True
        if stage == "candidate-scan":
            rows = int(observed.get("rows_decoded", 0))
            total = int(observed.get("projection_rows", 0))
            fraction = 0.0 if total <= 0 else min(1.0, rows / total)
            publish_progress("event-candidate-scan", 15 + int(65 * fraction), observed)
        elif stage == "adm-materialization":
            publish_progress("event-adm-materialization", 14, observed)
        elif stage == "candidate-finalization":
            publish_progress("event-candidate-finalization", 81, observed)
        elif stage == "entry-partition":
            publish_progress("event-entry-partition", 83, observed)
        elif stage == "package-publication":
            publish_progress("event-package-publication", 84, observed)
        elif stage == "package-verification":
            publish_progress("event-package-verification", 85, observed)
        if not terminal:
            return
        if stage == "candidate-scan":
            decode_elapsed = observed.pop("row_decode_elapsed_seconds")
            scan_elapsed = observed.pop("security_scan_elapsed_seconds")
            stages.append(
                {
                    "stage": "event.candidate-row-decode",
                    "elapsed_seconds": decode_elapsed,
                    **observed,
                }
            )
            stages.append(
                {
                    "stage": "event.security-scan",
                    "elapsed_seconds": scan_elapsed,
                    **observed,
                }
            )
            return
        stages.append({"stage": f"event.{stage}", **observed})

    session_started = time.perf_counter()
    session = provider.open(
        scope_start=_DEVELOPMENT_START,
        scope_end=_DECISION_END,
        progress=provider_progress,
    )
    provider_elapsed = round(time.perf_counter() - session_started, 3)
    with session:
        materialized_rows = session.identity.get("materialized_rows", {})
        provider_rows = (
            sum(int(value) for value in materialized_rows.values())
            if isinstance(materialized_rows, dict)
            else 0
        )
        provider_bytes = sum(
            _tree_bytes(path)
            for dataset in session.reader.bundle.datasets
            for path in session.reader.bundle.partition_paths(dataset.dataset)
        )
        stages.append(
            {
                "stage": "provider.direct-dwd-projection",
                "elapsed_seconds": provider_elapsed,
                "rows": provider_rows,
                "bytes": provider_bytes,
                "disposition": "built_once",
            }
        )
        event = _run_stage(
            stages,
            "event-library",
            lambda: build_physical_event_scan(
                source_policy_path=source_policy_path,
                provider_config_path=provider_config_path,
                rule_card_path=rule_card_path,
                split_policy_path=split_policy_path,
                output=event_output,
                code_revision=code_revision,
                provider_session=session,
                progress=event_progress,
            ),
            lambda result: {
                "rows": result.candidate_events,
                "bytes": _tree_bytes(result.root),
                "provider_disposition": "reused",
            },
        )
        event_output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".p4-job-local-", dir=event_output.parent.resolve()
        ) as temporary:
            label_panel = _run_stage(
                stages,
                "feature-panel.label-scope",
                lambda: build_job_local_feature_panel(
                    session=session,
                    provider_config=provider_config,
                    registry=registry,
                    scope_start=_DEVELOPMENT_START,
                    scope_end=_EXECUTION_END,
                    output=Path(temporary) / "feature-panel.parquet",
                ),
                lambda result: {
                    "rows": result.report.row_count,
                    "bytes": result.parquet_bytes,
                    "disposition": "built_once_tmp",
                    "scope_end": _EXECUTION_END.isoformat(),
                },
            )
            label = _run_stage(
                stages,
                "label-corpus",
                lambda: build_physical_label_corpus(
                    event_root=event.root,
                    provider_config_path=provider_config_path,
                    outcome_card_path=outcome_card_path,
                    structural_card_path=structural_card_path,
                    feature_registry_path=feature_registry_path,
                    output=label_output,
                    code_revision=code_revision,
                    provider_session=session,
                    feature_panel=label_panel,
                ),
                lambda result: {
                    "rows": result.total_events,
                    "bytes": _tree_bytes(result.root),
                    "provider_disposition": "reused",
                    "feature_panel_disposition": "supplied_scope_bound_tmp",
                },
            )
            label_panel.path.unlink()
            report_panel = _run_stage(
                stages,
                "feature-panel.report-scope",
                lambda: build_job_local_feature_panel(
                    session=session,
                    provider_config=provider_config,
                    registry=registry,
                    scope_start=_DEVELOPMENT_START,
                    scope_end=_DECISION_END,
                    output=Path(temporary) / "report-feature-panel.parquet",
                ),
                lambda result: {
                    "rows": result.report.row_count,
                    "bytes": result.parquet_bytes,
                    "disposition": "built_once_tmp",
                    "scope_end": _DECISION_END.isoformat(),
                },
            )
            report = _run_stage(
                stages,
                "comparison-report",
                lambda: build_physical_comparison_report(
                    event_root=event.root,
                    label_root=label.root,
                    provider_config_path=provider_config_path,
                    feature_registry_path=feature_registry_path,
                    report_policy_path=report_policy_path,
                    output=report_output,
                    code_revision=code_revision,
                    provider_session=session,
                    feature_panel=report_panel,
                ),
                lambda result: {
                    "rows": result.three_class_events,
                    "bytes": _tree_bytes(result.root),
                    "provider_disposition": "reused",
                    "feature_panel_disposition": "supplied_scope_bound_tmp",
                },
            )

    runtime = {
        "schema_version": REPORTED_BATCH_RUNTIME_SCHEMA,
        "total_elapsed_seconds": round(time.perf_counter() - batch_started, 3),
        "max_rss_bytes": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024,
        "provider_config_digest": provider_config.digest,
        "feature_registry_digest": registry.digest,
        "scope": {
            "start": _DEVELOPMENT_START.isoformat(),
            "decision_end": _DECISION_END.isoformat(),
            "execution_end": _EXECUTION_END.isoformat(),
        },
        "reuse": {
            "provider_materializations": 1,
            "feature_panel_materializations": 2,
            "cross_job_cache": False,
            "adm_published": False,
        },
        "stages": stages,
    }
    if runtime_receipt_path is not None:
        _write_runtime_receipt(runtime_receipt_path, runtime)
    return ReportedBatchResult(event, label, report, runtime)

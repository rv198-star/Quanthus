"""Job-local P4 intermediates shared only within one composite batch."""

from __future__ import annotations

import glob
import hashlib
import json
import os
from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from itertools import pairwise
from pathlib import Path
from typing import Any

from quanthus.backtest.baseline_research import (
    FEATURE_EVALUATOR_VERSION,
    BaselineFeaturePanel,
    BaselineFeaturePanelReport,
)
from quanthus.backtest.data_provider import ReplayDataProviderConfig, ReplayDataSession
from quanthus.feature_registry import FeatureRegistry

JOB_LOCAL_FEATURE_PANEL_SCHEMA = "quanthus.p4-job-local-feature-panel.v1"


class JobLocalArtifactError(RuntimeError):
    """Raised when an in-Job intermediate does not match its consumer."""


def _artifact_files(path: Path) -> tuple[Path, ...]:
    raw = str(path)
    if glob.has_magic(raw):
        files = tuple(Path(item).resolve() for item in sorted(glob.glob(raw)))
    else:
        files = (path.resolve(),)
    if not files or any(item.is_symlink() or not item.is_file() for item in files):
        raise JobLocalArtifactError("job_local_feature_panel_missing")
    return files


def _selection_digest(security_ids: Sequence[str]) -> str:
    encoded = json.dumps(
        {"allowed_security_ids": list(security_ids)},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def plan_feature_panel_security_buckets(
    projection_root: Path,
    workers: int,
    *,
    scope_start: date,
    scope_end: date,
) -> tuple[tuple[str, ...], ...]:
    if workers not in {1, 2, 3, 4} or scope_start > scope_end:
        raise JobLocalArtifactError("job_local_feature_panel_shard_plan_invalid")
    source = projection_root / "tables" / "daily_ohlcv_none.parquet"
    if source.is_symlink() or not source.is_file():
        raise JobLocalArtifactError("job_local_feature_panel_shard_source_missing")
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise JobLocalArtifactError("job_local_feature_panel_shard_source_unreadable") from exc
    connection = duckdb.connect(":memory:")
    try:
        weighted = tuple(
            (str(row[0]), int(row[1]))
            for row in connection.execute(
                """
                SELECT security_id, count(*) AS row_count
                FROM read_parquet(?)
                WHERE date BETWEEN ? AND ?
                GROUP BY security_id
                ORDER BY security_id
                """,
                [str(source), scope_start, scope_end],
            ).fetchall()
        )
    except Exception as exc:
        raise JobLocalArtifactError("job_local_feature_panel_shard_source_unreadable") from exc
    finally:
        connection.close()
    if workers > len(weighted) or any(
        not security_id.strip() or row_count <= 0 for security_id, row_count in weighted
    ):
        raise JobLocalArtifactError("job_local_feature_panel_shard_plan_invalid")
    cumulative = [0]
    for _security_id, row_count in weighted:
        cumulative.append(cumulative[-1] + row_count)
    cuts = [0]
    for boundary in range(1, workers):
        desired = cumulative[-1] * boundary / workers
        minimum = cuts[-1] + 1
        maximum = len(weighted) - (workers - boundary)
        position = bisect_left(cumulative, desired, minimum, maximum + 1)
        choices = tuple(
            cut for cut in (position - 1, position) if minimum <= cut <= maximum
        )
        cuts.append(min(choices, key=lambda cut: (abs(cumulative[cut] - desired), cut)))
    cuts.append(len(weighted))
    return tuple(
        tuple(security_id for security_id, _row_count in weighted[start:end])
        for start, end in pairwise(cuts)
    )


@dataclass(frozen=True)
class JobLocalFeaturePanel:
    path: Path
    report: BaselineFeaturePanelReport
    materialized_report: BaselineFeaturePanelReport
    provider_config_digest: str
    parquet_bytes: int
    schema_version: str = JOB_LOCAL_FEATURE_PANEL_SCHEMA

    def to_receipt(self) -> dict[str, Any]:
        def report_value(report: BaselineFeaturePanelReport) -> dict[str, Any]:
            return {
                name: (
                    value.isoformat() if isinstance(value, date) else value
                )
                for name, value in report.__dict__.items()
            }

        return {
            "schema_version": self.schema_version,
            "path": str(self.path),
            "report": report_value(self.report),
            "materialized_report": report_value(self.materialized_report),
            "provider_config_digest": self.provider_config_digest,
            "parquet_bytes": self.parquet_bytes,
        }

    @classmethod
    def from_receipt(cls, value: Mapping[str, Any]) -> JobLocalFeaturePanel:
        expected = {
            "schema_version",
            "path",
            "report",
            "materialized_report",
            "provider_config_digest",
            "parquet_bytes",
        }
        if set(value) != expected or value.get("schema_version") != JOB_LOCAL_FEATURE_PANEL_SCHEMA:
            raise JobLocalArtifactError("job_local_feature_panel_receipt_invalid")

        def report_value(raw: object) -> BaselineFeaturePanelReport:
            if not isinstance(raw, Mapping):
                raise JobLocalArtifactError("job_local_feature_panel_receipt_invalid")
            fields = dict(raw)
            try:
                fields["scope_start"] = date.fromisoformat(str(fields["scope_start"]))
                fields["scope_end"] = date.fromisoformat(str(fields["scope_end"]))
                return BaselineFeaturePanelReport(**fields)
            except (KeyError, TypeError, ValueError) as exc:
                raise JobLocalArtifactError(
                    "job_local_feature_panel_receipt_invalid"
                ) from exc

        path = Path(str(value["path"])).resolve()
        parquet_bytes = value["parquet_bytes"]
        provider_digest = value["provider_config_digest"]
        if (
            not isinstance(parquet_bytes, int)
            or isinstance(parquet_bytes, bool)
            or parquet_bytes <= 0
            or not isinstance(provider_digest, str)
            or not provider_digest
        ):
            raise JobLocalArtifactError("job_local_feature_panel_receipt_invalid")
        return cls(
            path=path,
            report=report_value(value["report"]),
            materialized_report=report_value(value["materialized_report"]),
            provider_config_digest=provider_digest,
            parquet_bytes=parquet_bytes,
        )

    def require_compatible(
        self,
        *,
        session: ReplayDataSession,
        provider_config: ReplayDataProviderConfig,
        registry: FeatureRegistry,
        scope_start: date,
        scope_end: date,
    ) -> None:
        if self.schema_version != JOB_LOCAL_FEATURE_PANEL_SCHEMA:
            raise JobLocalArtifactError("job_local_feature_panel_schema_mismatch")
        files = _artifact_files(self.path)
        if sum(item.stat().st_size for item in files) != self.parquet_bytes:
            raise JobLocalArtifactError("job_local_feature_panel_size_mismatch")
        if (
            self.provider_config_digest != provider_config.digest
            or session.config.digest != provider_config.digest
        ):
            raise JobLocalArtifactError("job_local_provider_identity_mismatch")
        expected_common = {
            "evaluator_version": FEATURE_EVALUATOR_VERSION,
            "feature_registry_version": registry.registry_version,
            "feature_registry_digest": registry.digest,
            "bundle_snapshot_id": session.reader.bundle.snapshot.snapshot_id,
            "bundle_digest": session.reader.bundle.digest,
            "data_content_digest": session.reader.data_content_digest,
            "data_manifest_sha256": session.reader.data_manifest_sha256,
        }
        for field, expected in expected_common.items():
            if getattr(self.report, field) != expected or getattr(
                self.materialized_report, field
            ) != expected:
                raise JobLocalArtifactError(f"job_local_feature_panel_{field}_mismatch")
        if (
            self.report.scope_start != scope_start
            or self.report.scope_end != scope_end
            or self.materialized_report.scope_start > scope_start
            or self.materialized_report.scope_end < scope_end
        ):
            raise JobLocalArtifactError("job_local_feature_panel_scope_mismatch")
        if (
            self.report.security_selection_digest is not None
            or self.report.selected_security_count is not None
            or self.materialized_report.security_selection_digest is not None
            or self.materialized_report.selected_security_count is not None
        ):
            raise JobLocalArtifactError("job_local_feature_panel_selection_mismatch")

    def scoped(self, *, scope_start: date, scope_end: date) -> JobLocalFeaturePanel:
        if scope_start > scope_end:
            raise JobLocalArtifactError("job_local_feature_panel_scope_invalid")
        if self.report.scope_start == scope_start and self.report.scope_end == scope_end:
            return self
        if (
            scope_start < self.materialized_report.scope_start
            or scope_end > self.materialized_report.scope_end
        ):
            raise JobLocalArtifactError("job_local_feature_panel_scope_mismatch")
        _artifact_files(self.path)
        try:
            import duckdb
        except ModuleNotFoundError as exc:
            raise JobLocalArtifactError("job_local_feature_panel_scope_unreadable") from exc
        connection = duckdb.connect(":memory:")
        try:
            counts = connection.execute(
                """
                SELECT count(*) AS row_count,
                       count(*) FILTER (WHERE feature_ready) AS feature_ready_count,
                       count(*) FILTER (WHERE entry_eligible) AS entry_eligible_count,
                       count(*) FILTER (WHERE exception_active) AS exception_active_count,
                       count(*) FILTER (
                           WHERE unexplained_price_transition
                       ) AS unexplained_transition_count,
                       count(DISTINCT security_id) FILTER (
                           WHERE scope_continuity_excluded
                       ) AS continuity_excluded_security_count
                FROM read_parquet(?)
                WHERE market_date BETWEEN ? AND ?
                """,
                [str(self.path), scope_start, scope_end],
            ).fetchone()
        except Exception as exc:
            raise JobLocalArtifactError("job_local_feature_panel_scope_unreadable") from exc
        finally:
            connection.close()
        if counts is None or int(counts[0]) <= 0 or int(counts[1]) <= 0:
            raise JobLocalArtifactError("job_local_feature_panel_scope_empty")
        report = replace(
            self.materialized_report,
            scope_start=scope_start,
            scope_end=scope_end,
            row_count=int(counts[0]),
            feature_ready_count=int(counts[1]),
            entry_eligible_count=int(counts[2]),
            exception_active_count=int(counts[3]),
            unexplained_transition_count=int(counts[4]),
            continuity_excluded_security_count=int(counts[5]),
        )
        return replace(self, report=report)


def merge_job_local_feature_panel_parts(
    parts: Sequence[JobLocalFeaturePanel],
    security_buckets: Sequence[Sequence[str]],
    *,
    output_root: Path,
) -> JobLocalFeaturePanel:
    """Bind disjoint accepted shard files as one immutable job-local panel."""

    if not parts or len(parts) != len(security_buckets):
        raise JobLocalArtifactError("job_local_feature_panel_parts_invalid")
    flattened = tuple(item for bucket in security_buckets for item in bucket)
    if (
        not flattened
        or flattened != tuple(sorted(flattened))
        or len(flattened) != len(set(flattened))
        or any(not bucket for bucket in security_buckets)
    ):
        raise JobLocalArtifactError("job_local_feature_panel_selection_mismatch")
    first = parts[0]
    common_fields = (
        "evaluator_version",
        "feature_registry_version",
        "feature_registry_digest",
        "bundle_snapshot_id",
        "bundle_digest",
        "scope_start",
        "scope_end",
        "data_content_digest",
        "data_manifest_sha256",
    )
    output_root.mkdir(parents=True, exist_ok=False)
    total_bytes = 0
    for ordinal, (part, bucket) in enumerate(zip(parts, security_buckets, strict=True)):
        files = _artifact_files(part.path)
        if len(files) != 1 or part.materialized_report != part.report:
            raise JobLocalArtifactError("job_local_feature_panel_parts_invalid")
        if (
            part.provider_config_digest != first.provider_config_digest
            or any(
                getattr(part.report, field) != getattr(first.report, field)
                for field in common_fields
            )
            or part.report.selected_security_count != len(bucket)
            or part.report.security_selection_digest
            != _selection_digest(tuple(sorted(bucket)))
            or part.parquet_bytes != files[0].stat().st_size
        ):
            raise JobLocalArtifactError("job_local_feature_panel_selection_mismatch")
        destination = output_root / f"part-{ordinal:03d}.parquet"
        os.link(files[0], destination)
        total_bytes += destination.stat().st_size
    report = replace(
        first.report,
        row_count=sum(part.report.row_count for part in parts),
        feature_ready_count=sum(part.report.feature_ready_count for part in parts),
        entry_eligible_count=sum(part.report.entry_eligible_count for part in parts),
        exception_active_count=sum(part.report.exception_active_count for part in parts),
        unexplained_transition_count=sum(
            part.report.unexplained_transition_count for part in parts
        ),
        continuity_excluded_security_count=sum(
            part.report.continuity_excluded_security_count for part in parts
        ),
        security_selection_digest=None,
        selected_security_count=None,
    )
    return JobLocalFeaturePanel(
        path=(output_root / "part-*.parquet").resolve(),
        report=report,
        materialized_report=report,
        provider_config_digest=first.provider_config_digest,
        parquet_bytes=total_bytes,
    )


def require_job_local_session(
    session: ReplayDataSession,
    provider_config: ReplayDataProviderConfig,
    *,
    scope_start: date,
    scope_end: date,
) -> None:
    provider_config.scope.authorize(scope_start, scope_end)
    if session.config.digest != provider_config.digest:
        raise JobLocalArtifactError("job_local_provider_identity_mismatch")


def build_job_local_feature_panel(
    *,
    session: ReplayDataSession,
    provider_config: ReplayDataProviderConfig,
    registry: FeatureRegistry,
    scope_start: date,
    scope_end: date,
    output: Path,
    duckdb_memory_limit: str = "6GB",
    duckdb_threads: int = 2,
    release_source_cache: bool = False,
    output_order: str = "date-security",
    spill_cache_release_interval_seconds: float | None = None,
    allowed_security_ids: Sequence[str] | None = None,
    materialize_selected_sources: bool = True,
) -> JobLocalFeaturePanel:
    require_job_local_session(
        session,
        provider_config,
        scope_start=provider_config.scope.decision_start,
        scope_end=provider_config.scope.decision_end,
    )
    if scope_start < provider_config.scope.decision_start:
        raise JobLocalArtifactError("job_local_feature_panel_scope_mismatch")
    if scope_end > provider_config.scope.execution_end:
        raise JobLocalArtifactError("job_local_feature_panel_scope_mismatch")
    artifact = BaselineFeaturePanel.materialize(
        session.reader.bundle,
        registry,
        scope_start=scope_start,
        scope_end=scope_end,
        output=output,
        data_content_digest=session.reader.data_content_digest,
        data_manifest_sha256=session.reader.data_manifest_sha256,
        duckdb_memory_limit=duckdb_memory_limit,
        duckdb_threads=duckdb_threads,
        release_source_cache=release_source_cache,
        output_order=output_order,
        spill_cache_release_interval_seconds=spill_cache_release_interval_seconds,
        allowed_security_ids=allowed_security_ids,
        materialize_selected_sources=materialize_selected_sources,
    )
    return JobLocalFeaturePanel(
        path=artifact.path,
        report=artifact.report,
        materialized_report=artifact.report,
        provider_config_digest=provider_config.digest,
        parquet_bytes=artifact.path.stat().st_size,
    )

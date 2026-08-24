"""Job-local P4 intermediates shared only within one composite batch."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
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
        if self.path.is_symlink() or not self.path.is_file():
            raise JobLocalArtifactError("job_local_feature_panel_missing")
        if self.path.stat().st_size != self.parquet_bytes:
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
        if (
            scope_start != self.materialized_report.scope_start
            or scope_end != self.materialized_report.scope_end
        ):
            raise JobLocalArtifactError("job_local_feature_panel_scope_mismatch")
        return self


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
    )
    return JobLocalFeaturePanel(
        path=artifact.path,
        report=artifact.report,
        materialized_report=artifact.report,
        provider_config_digest=provider_config.digest,
        parquet_bytes=artifact.path.stat().st_size,
    )

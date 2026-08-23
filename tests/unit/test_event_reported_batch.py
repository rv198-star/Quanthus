from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.backtest.baseline_research import BaselineFeaturePanelReport
from quanthus.event_library.comparison_report import ComparisonReportResult
from quanthus.event_library.job_local import (
    JOB_LOCAL_FEATURE_PANEL_SCHEMA,
    JobLocalArtifactError,
    JobLocalFeaturePanel,
)
from quanthus.event_library.label_corpus import LabelCorpusResult
from quanthus.event_library.physical_scan import PhysicalEventScanResult
from quanthus.event_library.reported_batch import _memory_sample, build_physical_reported_event_batch


def _report(*, end: date, rows: int = 2) -> BaselineFeaturePanelReport:
    return BaselineFeaturePanelReport(
        evaluator_version="1.0.0",
        feature_registry_version="1.0.0",
        feature_registry_digest="sha256:" + "1" * 64,
        bundle_snapshot_id="snapshot-test",
        bundle_digest="sha256:" + "2" * 64,
        scope_start=date(2012, 1, 4),
        scope_end=end,
        row_count=rows,
        feature_ready_count=rows,
        entry_eligible_count=rows,
        exception_active_count=0,
        unexplained_transition_count=0,
        continuity_excluded_security_count=0,
        data_content_digest="sha256:" + "3" * 64,
        data_manifest_sha256="sha256:" + "4" * 64,
    )


def test_job_local_panel_rejects_derived_scope_reuse(tmp_path: Path) -> None:
    panel_path = tmp_path / "feature-panel.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "market_date": date(2021, 12, 30),
                    "security_id": "600001",
                    "feature_ready": True,
                    "entry_eligible": True,
                    "exception_active": False,
                    "unexplained_price_transition": False,
                    "scope_continuity_excluded": False,
                },
                {
                    "market_date": date(2021, 12, 31),
                    "security_id": "600001",
                    "feature_ready": True,
                    "entry_eligible": True,
                    "exception_active": False,
                    "unexplained_price_transition": False,
                    "scope_continuity_excluded": False,
                },
            ]
        ),
        panel_path,
    )
    materialized = _report(end=date(2021, 12, 31))
    panel = JobLocalFeaturePanel(
        path=panel_path,
        report=materialized,
        materialized_report=materialized,
        provider_config_digest="sha256:" + "5" * 64,
        parquet_bytes=panel_path.stat().st_size,
    )

    with pytest.raises(JobLocalArtifactError, match="scope_mismatch"):
        panel.scoped(scope_start=date(2012, 1, 4), scope_end=date(2021, 12, 30))
    assert (
        panel.scoped(scope_start=date(2012, 1, 4), scope_end=date(2021, 12, 31))
        is panel
    )


def test_memory_sample_reads_process_and_configured_cgroup_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = {
        "QUANTHUS_EVENT_MEMORY_CURRENT_PATH": 100,
        "QUANTHUS_EVENT_MEMORY_PEAK_PATH": 200,
        "QUANTHUS_EVENT_SWAP_CURRENT_PATH": 0,
        "QUANTHUS_EVENT_SWAP_PEAK_PATH": 10,
    }
    for name, value in values.items():
        path = tmp_path / name.lower()
        path.write_text(f"{value}\n", encoding="ascii")
        monkeypatch.setenv(name, str(path))

    sample = _memory_sample()

    assert sample["process_current_rss_bytes"] > 0
    assert sample["process_peak_rss_bytes"] > 0
    assert sample["cgroup_memory_current_bytes"] == 100
    assert sample["cgroup_memory_peak_bytes"] == 200
    assert sample["cgroup_swap_current_bytes"] == 0
    assert sample["cgroup_swap_peak_bytes"] == 10


def test_job_local_panel_rejects_partial_or_drifted_identity(tmp_path: Path) -> None:
    panel_path = tmp_path / "feature-panel.parquet"
    panel_path.write_bytes(b"panel")
    report = _report(end=date(2021, 12, 31))
    panel = JobLocalFeaturePanel(
        path=panel_path,
        report=report,
        materialized_report=report,
        provider_config_digest="sha256:" + "5" * 64,
        parquet_bytes=panel_path.stat().st_size,
        schema_version=JOB_LOCAL_FEATURE_PANEL_SCHEMA,
    )
    provider_config = SimpleNamespace(
        digest="sha256:" + "6" * 64,
    )
    session = SimpleNamespace(config=provider_config)
    registry = SimpleNamespace(
        registry_version="1.0.0",
        digest="sha256:" + "1" * 64,
    )

    with pytest.raises(JobLocalArtifactError, match="provider_identity_mismatch"):
        panel.require_compatible(
            session=session,
            provider_config=provider_config,
            registry=registry,
            scope_start=date(2012, 1, 4),
            scope_end=date(2021, 12, 31),
        )


def test_reported_batch_builds_each_scope_bound_prerequisite_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider_part = tmp_path / "provider.parquet"
    provider_part.write_bytes(b"provider")
    calls: dict[str, int] = {
        "provider_open": 0,
        "panel_build": 0,
        "event": 0,
        "label": 0,
        "report": 0,
    }

    class Scope:
        decision_start = date(2012, 1, 4)
        decision_end = date(2021, 12, 30)
        execution_end = date(2021, 12, 31)

    provider_config = SimpleNamespace(
        mode="direct-dwd-job-local",
        digest="sha256:" + "5" * 64,
        scope=Scope(),
    )
    registry = SimpleNamespace(
        registry_version="1.0.0",
        digest="sha256:" + "1" * 64,
    )
    dataset = SimpleNamespace(dataset="daily")
    bundle = SimpleNamespace(
        datasets=(dataset,),
        partition_paths=lambda _dataset: (provider_part,),
    )
    session = SimpleNamespace(
        identity={"materialized_rows": {"daily": 10}},
        reader=SimpleNamespace(bundle=bundle),
    )
    session.__enter__ = lambda: session
    session.__exit__ = lambda *_args: None

    class Session:
        identity = session.identity
        reader = session.reader

        def __enter__(self) -> Session:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    shared_session = Session()

    class Provider:
        def __init__(self, _config: Any) -> None:
            pass

        def open(self, **kwargs: Any) -> Session:
            calls["provider_open"] += 1
            progress = kwargs["progress"]
            progress("dwd-projection-validated", {"row_count": 10})
            return shared_session

    panel_scopes: list[date] = []

    class Panel:
        def __init__(self, path: Path, scope_end: date) -> None:
            self.path = path
            self.report = SimpleNamespace(row_count=20, scope_end=scope_end)
            self.parquet_bytes = path.stat().st_size

    panels: dict[date, Panel] = {}

    def materialize_panel(**kwargs: Any) -> Panel:
        calls["panel_build"] += 1
        assert kwargs["session"] is shared_session
        scope_end = kwargs["scope_end"]
        panel_scopes.append(scope_end)
        output = Path(kwargs["output"])
        output.write_bytes(b"panel")
        panel = Panel(output, scope_end)
        panels[scope_end] = panel
        return panel

    def output_root(name: str) -> Path:
        root = tmp_path / name
        root.mkdir()
        (root / "artifact").write_bytes(name.encode("ascii"))
        return root

    def build_event(**kwargs: Any) -> PhysicalEventScanResult:
        calls["event"] += 1
        assert kwargs["provider_session"] is shared_session
        progress = kwargs["progress"]
        progress(
            "adm-materialization",
            {"terminal": True, "elapsed_seconds": 1.0, "rows": 10, "bytes": 20},
        )
        progress(
            "candidate-scan",
            {
                "terminal": True,
                "series_completed": 2,
                "rows_decoded": 10,
                "evaluated_rows": 8,
                "raw_triggers": 3,
                "row_decode_elapsed_seconds": 2.0,
                "security_scan_elapsed_seconds": 3.0,
            },
        )
        progress(
            "candidate-finalization",
            {"terminal": True, "elapsed_seconds": 0.5, "candidate_events": 12},
        )
        return PhysicalEventScanResult(
            output_root("event"), "sha256:event", "sha256:event-content", 12, 10, 2
        )

    def build_label(**kwargs: Any) -> LabelCorpusResult:
        calls["label"] += 1
        assert kwargs["provider_session"] is shared_session
        assert kwargs["feature_panel"] is panels[date(2021, 12, 31)]
        return LabelCorpusResult(
            output_root("label"),
            "sha256:label",
            "sha256:label-content",
            12,
            4,
            3,
            3,
            2,
            0,
            0,
        )

    def build_report(**kwargs: Any) -> ComparisonReportResult:
        calls["report"] += 1
        assert kwargs["provider_session"] is shared_session
        assert kwargs["feature_panel"] is panels[date(2021, 12, 30)]
        return ComparisonReportResult(
            output_root("report"),
            "sha256:report",
            "sha256:report-content",
            12,
            10,
            ("a", "b", "c"),
            True,
        )

    monkeypatch.setattr(
        "quanthus.event_library.reported_batch.load_replay_data_provider_config",
        lambda _path: provider_config,
    )
    monkeypatch.setattr(
        "quanthus.event_library.reported_batch.load_feature_registry", lambda _path: registry
    )
    monkeypatch.setattr("quanthus.event_library.reported_batch.ReplayDataProvider", Provider)
    monkeypatch.setattr(
        "quanthus.event_library.reported_batch.build_job_local_feature_panel",
        materialize_panel,
    )
    monkeypatch.setattr(
        "quanthus.event_library.reported_batch.build_physical_event_scan", build_event
    )
    monkeypatch.setattr(
        "quanthus.event_library.reported_batch.build_physical_label_corpus", build_label
    )
    monkeypatch.setattr(
        "quanthus.event_library.reported_batch.build_physical_comparison_report", build_report
    )
    receipt = tmp_path / "runtime.json"
    progress_receipt = tmp_path / "progress.json"

    result = build_physical_reported_event_batch(
        source_policy_path=Path("source.json"),
        provider_config_path=Path("provider.json"),
        rule_card_path=Path("rule.json"),
        split_policy_path=Path("split.json"),
        outcome_card_path=Path("outcome.json"),
        structural_card_path=Path("structural.json"),
        feature_registry_path=Path("features.json"),
        report_policy_path=Path("report-policy.json"),
        event_output=tmp_path / "event-output",
        label_output=tmp_path / "label-output",
        report_output=tmp_path / "report-output",
        code_revision="a" * 40,
        runtime_receipt_path=receipt,
        progress_receipt_path=progress_receipt,
    )

    assert calls == {
        "provider_open": 1,
        "panel_build": 2,
        "event": 1,
        "label": 1,
        "report": 1,
    }
    assert panel_scopes == [date(2021, 12, 31), date(2021, 12, 30)]
    assert result.runtime["reuse"] == {
        "provider_materializations": 1,
        "feature_panel_materializations": 2,
        "cross_job_cache": False,
        "adm_published": False,
    }
    assert json.loads(receipt.read_text(encoding="utf-8"))["reuse"] == result.runtime["reuse"]
    stage_names = [stage["stage"] for stage in result.runtime["stages"]]
    assert "event.adm-materialization" in stage_names
    assert "event.candidate-row-decode" in stage_names
    assert "event.security-scan" in stage_names
    assert "event.candidate-finalization" in stage_names
    progress = json.loads(progress_receipt.read_text(encoding="utf-8"))
    assert progress["schema_version"] == "quanthus.p4-reported-batch-progress.v1"
    assert progress["stage"] == "event-candidate-finalization"
    assert progress["progress"] == 81

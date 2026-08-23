from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from quanthus.platform.event_worker import (
    ComparisonReportEnvelope,
    EventLibraryEnvelope,
    EventWorker,
    LabelCorpusEnvelope,
    _event_input,
    _load_progress_receipt,
    _load_runtime_receipt,
    _validate_run_directory,
)
from quanthus.platform.jobs import P4_EVENT_SCAN_JOB, input_digest
from quanthus.platform.models import JobRecord

GOVERNED_INPUT = {
    "code_revision": "a" * 40,
    "source_policy": "config/p4-event-scan-source.research.v1.json",
    "provider_config": "config/replay-data-provider.development.v2.json",
    "rule_card": "config/p4-candidate-price-volume-transition.research.v1.json",
    "split_policy": "config/p4-event-split-policy.research.v1.json",
    "outcome_card": "config/p4-event-outcome-label.research.v1.json",
    "structural_break_card": "config/p4-event-structural-break.research.v1.json",
    "feature_registry": "config/p3-baseline-features.research.v1.json",
    "report_policy": "config/p4-event-comparison-report.research.v1.json",
}


def _job(*, kind: str = P4_EVENT_SCAN_JOB) -> JobRecord:
    now = datetime.now(timezone.utc)
    return JobRecord(
        job_id="job_0123456789abcdef0123456789abcdef",
        kind=kind,
        actor_user_id="usr_test",
        idempotency_key="p4-event-test",
        input_ref=dict(GOVERNED_INPUT),
        input_digest=input_digest(GOVERNED_INPUT),
        queue="event",
        concurrency_key="p4-event-scan:development-v1",
        state="running",
        stage="starting",
        progress=1,
        cancel_requested=False,
        attempt=1,
        result_ref=None,
        error_reason=None,
        created_at=now,
        updated_at=now,
        claimed_by="event-test",
        claim_generation=2,
    )


def test_event_input_is_exact_and_rejects_path_substitution() -> None:
    assert _event_input(GOVERNED_INPUT) == GOVERNED_INPUT

    changed = dict(GOVERNED_INPUT)
    changed["provider_config"] = "/tmp/provider.json"
    with pytest.raises(RuntimeError, match="provider_config_not_governed"):
        _event_input(changed)

    changed = dict(GOVERNED_INPUT)
    changed["arbitrary_command"] = "bash"
    with pytest.raises(RuntimeError, match="input_contract_mismatch"):
        _event_input(changed)


def test_runtime_receipt_requires_exact_scope_bound_materialization_counts(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime-metrics.json"
    payload = {
        "schema_version": "quanthus.p4-reported-batch-runtime.v1",
        "total_elapsed_seconds": 12.5,
        "max_rss_bytes": 1024,
        "provider_config_digest": "sha256:" + "1" * 64,
        "feature_registry_digest": "sha256:" + "2" * 64,
        "scope": {
            "start": "2012-01-04",
            "decision_end": "2021-12-30",
            "execution_end": "2021-12-31",
        },
        "stages": [
            {
                "stage": stage,
                "elapsed_seconds": 1.0,
                **(
                    {"scope_end": "2021-12-31"}
                    if stage == "feature-panel.label-scope"
                    else {"scope_end": "2021-12-30"}
                    if stage == "feature-panel.report-scope"
                    else {}
                ),
            }
            for stage in (
                "provider.direct-dwd-projection",
                "event-library",
                "feature-panel.label-scope",
                "feature-panel.report-scope",
                "label-corpus",
                "comparison-report",
            )
        ],
        "reuse": {
            "provider_materializations": 1,
            "feature_panel_materializations": 2,
            "cross_job_cache": False,
            "adm_published": False,
        },
    }
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    assert _load_runtime_receipt(path, required=True) is not None

    payload["stages"][2]["scope_end"] = "2021-12-30"
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="runtime_receipt_invalid"):
        _load_runtime_receipt(path, required=True)
    payload["stages"][2]["scope_end"] = "2021-12-31"

    payload["reuse"] = {
        "provider_materializations": 3,
        "feature_panel_materializations": 2,
        "cross_job_cache": False,
        "adm_published": False,
    }
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="runtime_receipt_invalid"):
        _load_runtime_receipt(path, required=True)


def test_progress_receipt_is_bounded_and_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "progress.json"
    payload = {
        "schema_version": "quanthus.p4-reported-batch-progress.v1",
        "stage": "event-candidate-scan",
        "progress": 47,
        "detail": {"rows_decoded": 1200, "projection_rows": 2400},
    }
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    assert _load_progress_receipt(path) == payload

    payload["progress"] = 90
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    assert _load_progress_receipt(path) is None
    payload["progress"] = 47
    payload["extra"] = True
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    assert _load_progress_receipt(path) is None


def test_event_worker_polls_child_progress_and_removes_receipt_before_sealing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _job()
    run_id = f"run_{job.job_id.removeprefix('job_')}"
    event_result = EventLibraryEnvelope(
        root=tmp_path / "event-library",
        manifest_sha256="sha256:" + "1" * 64,
        content_digest="sha256:" + "2" * 64,
        candidate_events=12,
        executable_events=10,
        not_executable_events=2,
    )
    label_result = LabelCorpusEnvelope(
        root=tmp_path / "label-corpus",
        manifest_sha256="sha256:" + "3" * 64,
        content_digest="sha256:" + "4" * 64,
        total_events=12,
        positive_events=4,
        neutral_events=3,
        failure_events=3,
        not_executable_events=2,
        censored_events=0,
        unavailable_events=0,
    )
    report_result = ComparisonReportEnvelope(
        root=tmp_path / "comparison-report",
        manifest_sha256="sha256:" + "5" * 64,
        content_digest="sha256:" + "6" * 64,
        candidate_events=12,
        three_class_events=10,
        leading_features=(
            "price.momentum20",
            "price.rsi14",
            "liquidity.volume_ratio20",
        ),
        acceptance_pass=True,
    )
    runtime = {
        "total_elapsed_seconds": 12.5,
        "reuse": {
            "provider_materializations": 1,
            "feature_panel_materializations": 2,
            "cross_job_cache": False,
            "adm_published": False,
        },
    }

    class Store:
        def __init__(self) -> None:
            self.current = job
            self.updates: list[dict[str, Any]] = []
            self.finished: dict[str, Any] | None = None

        def get_job(self, job_id: str) -> JobRecord:
            assert job_id == job.job_id
            return self.current

        def update_claimed_job(self, job_id: str, generation: int, **kwargs: Any) -> JobRecord:
            assert (job_id, generation) == (job.job_id, job.claim_generation)
            self.updates.append(kwargs)
            self.current = replace(
                self.current,
                stage=str(kwargs["stage"]),
                progress=max(self.current.progress, int(kwargs["progress"])),
            )
            return self.current

        def renew_job_lease(self, *_args: Any) -> bool:
            return True

        def finish_claimed_job(self, job_id: str, generation: int, **kwargs: Any) -> JobRecord:
            assert (job_id, generation) == (job.job_id, job.claim_generation)
            self.finished = kwargs
            return self.current

    class Process:
        returncode: int | None = None

        def __init__(self, command: tuple[str, ...], **_kwargs: Any) -> None:
            arguments = {
                command[index]: Path(command[index + 1]) for index in range(len(command) - 1)
            }
            for option in ("--event-output", "--label-output", "--report-output"):
                arguments[option].mkdir(parents=True)
            arguments["--runtime-receipt"].write_text("{}\n", encoding="utf-8")
            arguments["--progress-receipt"].write_text(
                json.dumps(
                    {
                        "schema_version": "quanthus.p4-reported-batch-progress.v1",
                        "stage": "event-candidate-scan",
                        "progress": 47,
                        "detail": {"rows_decoded": 1200, "projection_rows": 2400},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self.polls = 0

        def poll(self) -> int | None:
            self.polls += 1
            if self.polls == 1:
                return None
            self.returncode = 0
            return self.returncode

    store = Store()
    monkeypatch.setattr("quanthus.platform.event_worker.subprocess.Popen", Process)
    monkeypatch.setattr("quanthus.platform.event_worker.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "quanthus.platform.event_worker._verify_event_library", lambda _path: event_result
    )
    monkeypatch.setattr(
        "quanthus.platform.event_worker._verify_label_corpus", lambda _path: label_result
    )
    monkeypatch.setattr(
        "quanthus.platform.event_worker._verify_comparison_report", lambda _path: report_result
    )
    monkeypatch.setattr(
        "quanthus.platform.event_worker._load_runtime_receipt",
        lambda _path, *, required: runtime if required else None,
    )
    monkeypatch.setattr(
        "quanthus.platform.event_worker._validate_run_directory",
        lambda *_args, **_kwargs: (
            event_result,
            label_result,
            report_result,
            {"run_id": run_id},
        ),
    )
    worker = EventWorker(
        store,  # type: ignore[arg-type]
        worker_id="event-test",
        run_root=tmp_path,
        lease_seconds=300,
        renew_seconds=30,
        terminate_seconds=1,
    )

    worker._execute(job.job_id, job.claim_generation, job.input_ref, job.input_digest)

    assert {
        "stage": "event-candidate-scan",
        "progress": 47,
        "progress_detail": {
            "rows_decoded": 1200,
            "projection_rows": 2400,
            "run_id": run_id,
        },
    } in store.updates
    assert not (tmp_path / "runs" / run_id / "progress.json").exists()
    assert store.finished == {
        "state": "succeeded",
        "result_ref": {"run_id": run_id},
        "error_reason": None,
    }


def test_event_worker_claims_only_event_queue_and_kind(tmp_path: Path) -> None:
    job = _job(kind="quanthus.candidate-replay.v1")

    class Store:
        finished: dict[str, Any] | None = None

        def claim_job(self, queue: str, worker_id: str, lease_seconds: int) -> JobRecord:
            assert (queue, worker_id, lease_seconds) == ("event", "event-test", 300)
            return job

        def finish_claimed_job(
            self,
            job_id: str,
            generation: int,
            **kwargs: Any,
        ) -> JobRecord:
            assert (job_id, generation) == (job.job_id, 2)
            self.finished = kwargs
            return job

    store = Store()
    worker = EventWorker(
        store,  # type: ignore[arg-type]
        worker_id="event-test",
        run_root=tmp_path,
        lease_seconds=300,
        renew_seconds=30,
        terminate_seconds=1,
    )

    assert worker.run_once() is True
    assert store.finished == {
        "state": "failed",
        "result_ref": None,
        "error_reason": "job_kind_not_admitted",
    }


def test_event_worker_recovers_sealed_run_without_rerunning_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _job()
    run_id = f"run_{job.job_id.removeprefix('job_')}"
    final = tmp_path / "runs" / run_id
    final.mkdir(parents=True)
    expected_result = EventLibraryEnvelope(
        root=final / "event-library",
        manifest_sha256="sha256:" + "1" * 64,
        content_digest="sha256:" + "2" * 64,
        candidate_events=12,
        executable_events=10,
        not_executable_events=2,
    )
    expected_label_result = LabelCorpusEnvelope(
        root=final / "label-corpus",
        manifest_sha256="sha256:" + "3" * 64,
        content_digest="sha256:" + "4" * 64,
        total_events=12,
        positive_events=4,
        neutral_events=3,
        failure_events=3,
        not_executable_events=2,
        censored_events=0,
        unavailable_events=0,
    )
    expected_report_result = ComparisonReportEnvelope(
        root=final / "comparison-report",
        manifest_sha256="sha256:" + "5" * 64,
        content_digest="sha256:" + "6" * 64,
        candidate_events=12,
        three_class_events=10,
        leading_features=("price.momentum20", "price.rsi14", "liquidity.volume_ratio20"),
        acceptance_pass=True,
    )
    expected_ref = {
        "run_id": run_id,
        "artifact_ref": str(final),
        "result_sha256": expected_result.manifest_sha256,
    }

    class Store:
        updates: list[dict[str, Any]]
        finished: dict[str, Any] | None

        def __init__(self) -> None:
            self.updates = []
            self.finished = None

        def get_job(self, job_id: str) -> JobRecord:
            assert job_id == job.job_id
            return job

        def update_claimed_job(self, _job_id: str, _generation: int, **kwargs: Any) -> JobRecord:
            self.updates.append(kwargs)
            return job

        def finish_claimed_job(self, _job_id: str, _generation: int, **kwargs: Any) -> JobRecord:
            self.finished = kwargs
            return job

    store = Store()
    monkeypatch.setattr(
        "quanthus.platform.event_worker._validate_run_directory",
        lambda *_args, **_kwargs: (
            expected_result,
            expected_label_result,
            expected_report_result,
            expected_ref,
        ),
    )
    worker = EventWorker(
        store,  # type: ignore[arg-type]
        worker_id="event-test",
        run_root=tmp_path,
        lease_seconds=300,
        renew_seconds=30,
        terminate_seconds=1,
    )

    worker._execute(job.job_id, 2, job.input_ref, job.input_digest)

    assert store.updates == [
        {
            "stage": "recovering-sealed-event-run",
            "progress": 98,
            "progress_detail": {
                "run_id": run_id,
                "candidate_events": 12,
                "three_class_events": 10,
                "leading_features": [
                    "price.momentum20",
                    "price.rsi14",
                    "liquidity.volume_ratio20",
                ],
            },
        }
    ]
    assert store.finished == {
        "state": "succeeded",
        "result_ref": expected_ref,
        "error_reason": None,
    }


def test_validate_event_run_rejects_manifest_identity_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = EventLibraryEnvelope(
        root=tmp_path / "event-library",
        manifest_sha256="sha256:" + "1" * 64,
        content_digest="sha256:" + "2" * 64,
        candidate_events=4,
        executable_events=3,
        not_executable_events=1,
    )
    label_result = LabelCorpusEnvelope(
        root=tmp_path / "label-corpus",
        manifest_sha256="sha256:" + "3" * 64,
        content_digest="sha256:" + "4" * 64,
        total_events=4,
        positive_events=1,
        neutral_events=1,
        failure_events=1,
        not_executable_events=1,
        censored_events=0,
        unavailable_events=0,
    )
    report_result = ComparisonReportEnvelope(
        root=tmp_path / "comparison-report",
        manifest_sha256="sha256:" + "5" * 64,
        content_digest="sha256:" + "6" * 64,
        candidate_events=4,
        three_class_events=3,
        leading_features=("price.momentum20", "price.rsi14", "liquidity.volume_ratio20"),
        acceptance_pass=True,
    )
    event_root = tmp_path / "event-library"
    event_root.mkdir()
    label_root = tmp_path / "label-corpus"
    label_root.mkdir()
    report_root = tmp_path / "comparison-report"
    report_root.mkdir()
    (event_root / "manifest.json").write_text(
        '{"code_revision":"' + "a" * 40 + '"}\n', encoding="utf-8"
    )
    (label_root / "manifest.json").write_text(
        '{"code_revision":"' + "a" * 40 + '"}\n', encoding="utf-8"
    )
    (report_root / "manifest.json").write_text(
        '{"code_revision":"' + "a" * 40 + '"}\n', encoding="utf-8"
    )
    (tmp_path / "run-manifest.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        "quanthus.platform.event_worker._verify_event_library",
        lambda _path: result,
    )
    monkeypatch.setattr(
        "quanthus.platform.event_worker._verify_label_corpus",
        lambda _path: label_result,
    )
    monkeypatch.setattr(
        "quanthus.platform.event_worker._verify_comparison_report",
        lambda _path: report_result,
    )

    with pytest.raises(RuntimeError, match="event_run_manifest_mismatch"):
        _validate_run_directory(
            tmp_path,
            run_id="run_test",
            job_id="job_test",
            input_digest="sha256:test",
            code_revision="a" * 40,
        )

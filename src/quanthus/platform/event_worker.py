"""Lean PostgreSQL Event Worker for governed DWD-backed research jobs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quanthus.platform.jobs import P4_EVENT_SCAN_JOB, STRUCTURE_MVP_JOB, DurableJobQueue
from quanthus.platform.persistence import PlatformStore, PostgresPlatformStore

EVENT_QUEUE = "event"
RUN_SCHEMA = "quanthus.p4-event-run.v3"
REPORTED_BATCH_RUNTIME_SCHEMA = "quanthus.p4-reported-batch-runtime.v2"
REPORTED_BATCH_PROGRESS_SCHEMA = "quanthus.p4-reported-batch-progress.v1"
_CODE_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_STRUCTURE_RUN_CONFIG = "config/structure-mvp-run.development.v1.json"
_STRUCTURE_QUALIFICATION_SEED_ENV = "QUANTHUS_STRUCTURE_MVP_QUALIFICATION_SEED"
_BUILD_REVISION_ENV = "QUANTHUS_CODE_REVISION"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_EXPECTED_INPUT = {
    "source_policy": "config/p4-event-scan-source.research.v1.json",
    "provider_config": "config/replay-data-provider.development.v2.json",
    "rule_card": "config/p4-candidate-price-volume-transition.research.v1.json",
    "split_policy": "config/p4-event-split-policy.research.v1.json",
    "outcome_card": "config/p4-event-outcome-label.research.v1.json",
    "structural_break_card": "config/p4-event-structural-break.research.v1.json",
    "feature_registry": "config/p3-baseline-features.research.v1.json",
    "report_policy": "config/p4-event-comparison-report.research.v1.json",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the lean Quanthus P4 Event Worker")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=3.0)
    parser.add_argument("--lease-seconds", type=int, default=300)
    parser.add_argument("--renew-seconds", type=float, default=30.0)
    parser.add_argument("--terminate-seconds", type=float, default=20.0)
    return parser


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _terminate(process: subprocess.Popen[bytes], deadline_seconds: float) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=deadline_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=deadline_seconds)


def _event_input(input_ref: Mapping[str, Any]) -> dict[str, str]:
    expected_keys = {"code_revision", *_EXPECTED_INPUT}
    if set(input_ref) != expected_keys:
        raise RuntimeError("event_scan_input_contract_mismatch")
    code_revision = str(input_ref.get("code_revision") or "")
    if _CODE_REVISION_RE.fullmatch(code_revision) is None:
        raise RuntimeError("event_scan_code_revision_invalid")
    normalized = {"code_revision": code_revision}
    for field, expected in _EXPECTED_INPUT.items():
        observed = input_ref.get(field)
        if observed != expected:
            raise RuntimeError(f"event_scan_{field}_not_governed")
        normalized[field] = expected
    return normalized


def _structure_input(input_ref: Mapping[str, Any]) -> dict[str, str]:
    expected_keys = {"code_revision", "run_config"}
    if set(input_ref) != expected_keys:
        raise RuntimeError("structure_mvp_input_contract_mismatch")
    code_revision = str(input_ref.get("code_revision") or "")
    if _CODE_REVISION_RE.fullmatch(code_revision) is None:
        raise RuntimeError("structure_mvp_code_revision_invalid")
    if input_ref.get("run_config") != _STRUCTURE_RUN_CONFIG:
        raise RuntimeError("structure_mvp_run_config_not_governed")
    return {
        "code_revision": code_revision,
        "run_config": _STRUCTURE_RUN_CONFIG,
    }


def _structure_qualification_actor_user_id(store: PlatformStore) -> str:
    eligible = tuple(
        sorted(
            (
                user
                for user in store.list_users()
                if user.active and user.role in {"operator", "admin"}
            ),
            key=lambda user: (
                0 if user.role == "operator" else 1,
                user.username.casefold(),
                user.user_id,
            ),
        )
    )
    if not eligible:
        raise RuntimeError("structure_mvp_qualification_actor_unavailable")
    return eligible[0].user_id


def _seed_structure_mvp_qualification_jobs(
    store: PlatformStore,
    *,
    enabled_value: str,
    code_revision: str,
) -> tuple[str, ...]:
    """Idempotently seed exactly two fixed qualification Jobs when explicitly enabled."""

    normalized = enabled_value.strip()
    if normalized not in {"", "0", "1"}:
        raise RuntimeError("structure_mvp_qualification_seed_invalid")
    if normalized != "1":
        return ()
    if _CODE_REVISION_RE.fullmatch(code_revision) is None:
        raise RuntimeError("structure_mvp_qualification_revision_invalid")
    actor_user_id = _structure_qualification_actor_user_id(store)
    queue = DurableJobQueue(store, {STRUCTURE_MVP_JOB: EVENT_QUEUE})
    input_ref = {
        "code_revision": code_revision,
        "run_config": _STRUCTURE_RUN_CONFIG,
    }
    jobs: list[str] = []
    for suffix in ("a", "b"):
        stable_job_id = "job_" + hashlib.sha256(
            f"{STRUCTURE_MVP_JOB}:{code_revision}:{suffix}".encode()
        ).hexdigest()[:32]
        job, _created = queue.submit(
            kind=STRUCTURE_MVP_JOB,
            actor_user_id=actor_user_id,
            idempotency_key=(
                f"structure-mvp-qualification-v2-{suffix}-{code_revision[:12]}"
            ),
            input_ref=input_ref,
            concurrency_key=(
                f"structure-mvp:qualification-v2-{suffix}:{code_revision[:12]}"
            ),
            requested_job_id=stable_job_id,
        )
        if job.kind != STRUCTURE_MVP_JOB or job.input_ref != input_ref:
            raise RuntimeError("structure_mvp_qualification_seed_mismatch")
        jobs.append(job.job_id)
    return tuple(jobs)


def _canonical_digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _structure_request_digest(run_config: str) -> str:
    path = (_REPOSITORY_ROOT / run_config).resolve()
    if _REPOSITORY_ROOT not in path.parents:
        raise RuntimeError("structure_mvp_run_config_outside_repository")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("structure_mvp_run_config_unreadable") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != "quanthus.structure-mvp-run.v1":
        raise RuntimeError("structure_mvp_run_config_invalid")
    return _canonical_digest(raw)


def _verify_structure_mvp_artifact(
    root: Path,
    *,
    code_revision: str,
    request_digest: str,
) -> dict[str, Any]:
    manifest_path = root / "manifest.json"
    ready_path = root / "READY"
    summary_path = root / "summary.json"
    rows_path = root / "structure-adm.jsonl"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        ready = json.loads(ready_path.read_text(encoding="utf-8"))
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("structure_mvp_artifact_unreadable") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != "quanthus.structure-mvp-artifact-manifest.v1"
        or manifest.get("research_only") is not True
        or manifest.get("execution_authorized") is not False
        or manifest.get("code_revision") != code_revision
        or manifest.get("request_digest") != request_digest
    ):
        raise RuntimeError("structure_mvp_manifest_invalid")
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != {"structure-adm.jsonl", "summary.json"}:
        raise RuntimeError("structure_mvp_manifest_files_invalid")
    for name, path in (("structure-adm.jsonl", rows_path), ("summary.json", summary_path)):
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise RuntimeError("structure_mvp_artifact_file_unreadable") from exc
        observed = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        if files.get(name) != observed:
            raise RuntimeError("structure_mvp_artifact_digest_mismatch")
    try:
        manifest_sha = f"sha256:{hashlib.sha256(manifest_path.read_bytes()).hexdigest()}"
    except OSError as exc:
        raise RuntimeError("structure_mvp_manifest_unreadable") from exc
    if (
        not isinstance(ready, dict)
        or ready.get("schema_version") != "quanthus.structure-mvp-ready.v1"
        or ready.get("manifest_sha256") != manifest_sha
        or ready.get("result_digest") != manifest.get("result_digest")
    ):
        raise RuntimeError("structure_mvp_ready_invalid")
    if (
        not isinstance(summary, dict)
        or summary.get("schema_version") != "quanthus.structure-mvp-result.v1"
        or summary.get("code_revision") != code_revision
        or summary.get("request_digest") != request_digest
        or summary.get("result_digest") != manifest.get("result_digest")
        or summary.get("research_only") is not True
        or summary.get("execution_authorized") is not False
        or summary.get("economic_acceptance_claim") is not False
    ):
        raise RuntimeError("structure_mvp_summary_invalid")
    semantic_summary = dict(summary)
    semantic_summary.pop("result_digest", None)
    semantic_summary.pop("code_revision", None)
    if _canonical_digest(semantic_summary) != manifest.get("result_digest"):
        raise RuntimeError("structure_mvp_semantic_result_digest_mismatch")
    adm_manifest = summary.get("adm_manifest")
    state_counts = summary.get("state_counts")
    trend_semantics = summary.get("trend_semantics")
    decision_counts = summary.get("decision_counts")
    decisions = summary.get("decisions")
    probes = summary.get("probes")
    if (
        not isinstance(adm_manifest, dict)
        or not isinstance(state_counts, dict)
        or not isinstance(trend_semantics, dict)
        or not isinstance(decision_counts, dict)
        or not isinstance(decisions, list)
        or not isinstance(probes, list)
    ):
        raise RuntimeError("structure_mvp_summary_shape_invalid")
    if _canonical_digest(adm_manifest) != manifest.get("adm_manifest_digest"):
        raise RuntimeError("structure_mvp_adm_manifest_digest_mismatch")
    try:
        row_values = [
            json.loads(line)
            for line in rows_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("structure_mvp_adm_rows_invalid") from exc
    if (
        len(row_values) != int(adm_manifest.get("row_count", -1))
        or _canonical_digest(row_values) != adm_manifest.get("content_digest")
    ):
        raise RuntimeError("structure_mvp_adm_content_digest_mismatch")
    return {
        "artifact_ref": str(root.resolve()),
        "manifest_sha256": manifest_sha,
        "result_digest": str(manifest["result_digest"]),
        "adm_manifest_digest": str(manifest["adm_manifest_digest"]),
        "row_count": int(adm_manifest["row_count"]),
        "state_counts": dict(state_counts),
        "p2_probe_count": len(probes),
        "p2_fill_count": sum(int(item["fill_count"]) for item in probes),
    }


@dataclass(frozen=True)
class EventLibraryEnvelope:
    root: Path
    manifest_sha256: str
    content_digest: str
    candidate_events: int
    executable_events: int
    not_executable_events: int


@dataclass(frozen=True)
class LabelCorpusEnvelope:
    root: Path
    manifest_sha256: str
    content_digest: str
    total_events: int
    positive_events: int
    neutral_events: int
    failure_events: int
    not_executable_events: int
    censored_events: int
    unavailable_events: int


@dataclass(frozen=True)
class ComparisonReportEnvelope:
    root: Path
    manifest_sha256: str
    content_digest: str
    candidate_events: int
    three_class_events: int
    leading_features: tuple[str, ...]
    acceptance_pass: bool


def _verify_event_library(root: Path) -> EventLibraryEnvelope:
    completed = subprocess.run(
        (
            sys.executable,
            "-m",
            "quanthus",
            "verify-p4-event-scan",
            "--root",
            str(root),
        ),
        check=False,
        capture_output=True,
        timeout=300,
    )
    if completed.returncode != 0:
        raise RuntimeError("event_library_verifier_failed")
    try:
        payload = json.loads(completed.stdout.decode("utf-8").strip().splitlines()[-1])
    except (UnicodeDecodeError, json.JSONDecodeError, IndexError) as exc:
        raise RuntimeError("event_library_verifier_response_invalid") from exc
    required = {
        "root",
        "manifest_sha256",
        "content_digest",
        "candidate_events",
        "executable_events",
        "not_executable_events",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise RuntimeError("event_library_verifier_response_invalid")
    return EventLibraryEnvelope(
        root=Path(str(payload["root"])).resolve(),
        manifest_sha256=str(payload["manifest_sha256"]),
        content_digest=str(payload["content_digest"]),
        candidate_events=int(payload["candidate_events"]),
        executable_events=int(payload["executable_events"]),
        not_executable_events=int(payload["not_executable_events"]),
    )


def _verify_label_corpus(root: Path) -> LabelCorpusEnvelope:
    completed = subprocess.run(
        (
            sys.executable,
            "-m",
            "quanthus",
            "verify-p4-label-corpus",
            "--root",
            str(root),
        ),
        check=False,
        capture_output=True,
        timeout=300,
    )
    if completed.returncode != 0:
        raise RuntimeError("label_corpus_verifier_failed")
    try:
        payload = json.loads(completed.stdout.decode("utf-8").strip().splitlines()[-1])
    except (UnicodeDecodeError, json.JSONDecodeError, IndexError) as exc:
        raise RuntimeError("label_corpus_verifier_response_invalid") from exc
    required = {
        "root",
        "manifest_sha256",
        "content_digest",
        "total_events",
        "positive_events",
        "neutral_events",
        "failure_events",
        "not_executable_events",
        "censored_events",
        "unavailable_events",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise RuntimeError("label_corpus_verifier_response_invalid")
    return LabelCorpusEnvelope(
        root=Path(str(payload["root"])).resolve(),
        manifest_sha256=str(payload["manifest_sha256"]),
        content_digest=str(payload["content_digest"]),
        total_events=int(payload["total_events"]),
        positive_events=int(payload["positive_events"]),
        neutral_events=int(payload["neutral_events"]),
        failure_events=int(payload["failure_events"]),
        not_executable_events=int(payload["not_executable_events"]),
        censored_events=int(payload["censored_events"]),
        unavailable_events=int(payload["unavailable_events"]),
    )


def _verify_comparison_report(root: Path) -> ComparisonReportEnvelope:
    completed = subprocess.run(
        (
            sys.executable,
            "-m",
            "quanthus",
            "verify-p4-comparison-report",
            "--root",
            str(root),
        ),
        check=False,
        capture_output=True,
        timeout=300,
    )
    if completed.returncode != 0:
        raise RuntimeError("comparison_report_verifier_failed")
    try:
        payload = json.loads(completed.stdout.decode("utf-8").strip().splitlines()[-1])
    except (UnicodeDecodeError, json.JSONDecodeError, IndexError) as exc:
        raise RuntimeError("comparison_report_verifier_response_invalid") from exc
    required = {
        "root",
        "manifest_sha256",
        "content_digest",
        "candidate_events",
        "three_class_events",
        "leading_features",
        "acceptance_pass",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise RuntimeError("comparison_report_verifier_response_invalid")
    leading = payload["leading_features"]
    if not isinstance(leading, list) or any(not isinstance(item, str) for item in leading):
        raise RuntimeError("comparison_report_verifier_response_invalid")
    return ComparisonReportEnvelope(
        root=Path(str(payload["root"])).resolve(),
        manifest_sha256=str(payload["manifest_sha256"]),
        content_digest=str(payload["content_digest"]),
        candidate_events=int(payload["candidate_events"]),
        three_class_events=int(payload["three_class_events"]),
        leading_features=tuple(leading),
        acceptance_pass=bool(payload["acceptance_pass"]),
    )


def _result_ref(
    *,
    run_id: str,
    artifact_ref: Path,
    event_result: EventLibraryEnvelope,
    label_result: LabelCorpusEnvelope,
    report_result: ComparisonReportEnvelope,
    runtime: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "run_id": run_id,
        "artifact_ref": str(artifact_ref),
        "result_sha256": event_result.manifest_sha256,
        "content_digest": event_result.content_digest,
        "candidate_events": event_result.candidate_events,
        "executable_events": event_result.executable_events,
        "not_executable_events": event_result.not_executable_events,
        "label_manifest_sha256": label_result.manifest_sha256,
        "label_content_digest": label_result.content_digest,
        "positive_events": label_result.positive_events,
        "neutral_events": label_result.neutral_events,
        "failure_events": label_result.failure_events,
        "censored_events": label_result.censored_events,
        "unavailable_events": label_result.unavailable_events,
        "report_manifest_sha256": report_result.manifest_sha256,
        "report_content_digest": report_result.content_digest,
        "leading_features": list(report_result.leading_features),
        "report_acceptance_pass": report_result.acceptance_pass,
    }
    if runtime is not None:
        result["runtime"] = dict(runtime)
    return result


def _load_runtime_receipt(path: Path, *, required: bool) -> dict[str, Any] | None:
    if not path.is_file():
        if required:
            raise RuntimeError("event_runtime_receipt_missing")
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("event_runtime_receipt_unreadable") from exc
    reuse = payload.get("reuse") if isinstance(payload, dict) else None
    scope = payload.get("scope") if isinstance(payload, dict) else None
    stages = payload.get("stages") if isinstance(payload, dict) else None
    total_elapsed = payload.get("total_elapsed_seconds") if isinstance(payload, dict) else None
    max_rss = payload.get("max_rss_bytes") if isinstance(payload, dict) else None
    provider_digest = payload.get("provider_config_digest") if isinstance(payload, dict) else None
    registry_digest = payload.get("feature_registry_digest") if isinstance(payload, dict) else None
    memory = payload.get("memory") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != REPORTED_BATCH_RUNTIME_SCHEMA
        or not isinstance(total_elapsed, (int, float))
        or isinstance(total_elapsed, bool)
        or not math.isfinite(total_elapsed)
        or total_elapsed < 0
        or not isinstance(max_rss, int)
        or isinstance(max_rss, bool)
        or max_rss < 0
        or not isinstance(provider_digest, str)
        or _SHA256_RE.fullmatch(provider_digest) is None
        or not isinstance(registry_digest, str)
        or _SHA256_RE.fullmatch(registry_digest) is None
        or not _valid_memory_sample(memory)
        or scope
        != {
            "start": "2012-01-04",
            "decision_end": "2021-12-30",
            "execution_end": "2021-12-31",
        }
        or reuse
        != {
            "provider_materializations": 1,
            "feature_panel_materializations": 2,
            "cross_job_cache": False,
            "adm_published": False,
        }
        or not isinstance(stages, list)
    ):
        raise RuntimeError("event_runtime_receipt_invalid")
    required_stages = {
        "provider.direct-dwd-projection",
        "event-library",
        "feature-panel.label-scope",
        "feature-panel.report-scope",
        "label-corpus",
        "comparison-report",
    }
    required_stage_scopes = {
        "feature-panel.label-scope": "2021-12-31",
        "feature-panel.report-scope": "2021-12-30",
    }
    observed_stages: set[str] = set()
    for stage in stages:
        if not isinstance(stage, dict):
            raise RuntimeError("event_runtime_receipt_invalid")
        name = stage.get("stage")
        elapsed = stage.get("elapsed_seconds")
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(elapsed, (int, float))
            or isinstance(elapsed, bool)
            or not math.isfinite(elapsed)
            or elapsed < 0
        ):
            raise RuntimeError("event_runtime_receipt_invalid")
        expected_scope_end = required_stage_scopes.get(name)
        if expected_scope_end is not None and stage.get("scope_end") != expected_scope_end:
            raise RuntimeError("event_runtime_receipt_invalid")
        if not _valid_memory_sample(stage.get("memory")):
            raise RuntimeError("event_runtime_receipt_invalid")
        observed_stages.add(name)
    if not required_stages.issubset(observed_stages):
        raise RuntimeError("event_runtime_receipt_invalid")
    return payload


def _valid_memory_sample(value: object) -> bool:
    required = {
        "process_current_rss_bytes",
        "process_peak_rss_bytes",
        "cgroup_memory_current_bytes",
        "cgroup_memory_peak_bytes",
        "cgroup_memory_limit_bytes",
        "cgroup_swap_current_bytes",
        "cgroup_swap_peak_bytes",
        "cgroup_swap_limit_bytes",
        "cgroup_oom_count",
        "cgroup_oom_kill_count",
    }
    if not isinstance(value, dict) or set(value) != required:
        return False
    for name, observed in value.items():
        if observed is None and name.startswith("cgroup_"):
            continue
        if not isinstance(observed, int) or isinstance(observed, bool) or observed < 0:
            return False
    return True


def _load_progress_receipt(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file() or path.stat().st_size > 64 * 1024:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    stage = payload.get("stage") if isinstance(payload, dict) else None
    progress = payload.get("progress") if isinstance(payload, dict) else None
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "stage", "progress", "detail"}
        or payload.get("schema_version") != REPORTED_BATCH_PROGRESS_SCHEMA
        or not isinstance(stage, str)
        or not stage
        or len(stage) > 100
        or not isinstance(progress, int)
        or isinstance(progress, bool)
        or not 5 <= progress <= 89
        or not isinstance(detail, dict)
    ):
        return None
    return payload


def _validate_run_directory(
    path: Path,
    *,
    run_id: str,
    job_id: str,
    input_digest: str,
    code_revision: str,
) -> tuple[
    EventLibraryEnvelope,
    LabelCorpusEnvelope,
    ComparisonReportEnvelope,
    dict[str, Any],
]:
    try:
        manifest = json.loads((path / "run-manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("event_run_manifest_unreadable") from exc
    if not isinstance(manifest, dict):
        raise RuntimeError("event_run_manifest_invalid")
    event_result = _verify_event_library(path / "event-library")
    label_result = _verify_label_corpus(path / "label-corpus")
    report_result = _verify_comparison_report(path / "comparison-report")
    if label_result.total_events != event_result.candidate_events:
        raise RuntimeError("label_corpus_event_count_mismatch")
    if label_result.not_executable_events != event_result.not_executable_events:
        raise RuntimeError("label_corpus_not_executable_count_mismatch")
    if report_result.candidate_events != event_result.candidate_events:
        raise RuntimeError("comparison_report_event_count_mismatch")
    three_class_events = (
        label_result.positive_events
        + label_result.neutral_events
        + label_result.failure_events
    )
    if report_result.three_class_events != three_class_events:
        raise RuntimeError("comparison_report_three_class_count_mismatch")
    try:
        event_manifest = json.loads(
            (path / "event-library" / "manifest.json").read_text(encoding="utf-8")
        )
        label_manifest = json.loads(
            (path / "label-corpus" / "manifest.json").read_text(encoding="utf-8")
        )
        report_manifest = json.loads(
            (path / "comparison-report" / "manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("event_label_or_report_manifest_unreadable") from exc
    expected = {
        "schema_version": RUN_SCHEMA,
        "run_id": run_id,
        "job_id": job_id,
        "input_digest": input_digest,
        "code_revision": code_revision,
        "event_manifest_sha256": event_result.manifest_sha256,
        "event_content_digest": event_result.content_digest,
        "label_manifest_sha256": label_result.manifest_sha256,
        "label_content_digest": label_result.content_digest,
        "candidate_events": event_result.candidate_events,
        "executable_events": event_result.executable_events,
        "not_executable_events": event_result.not_executable_events,
        "positive_events": label_result.positive_events,
        "neutral_events": label_result.neutral_events,
        "failure_events": label_result.failure_events,
        "censored_events": label_result.censored_events,
        "unavailable_events": label_result.unavailable_events,
        "report_manifest_sha256": report_result.manifest_sha256,
        "report_content_digest": report_result.content_digest,
        "leading_features": list(report_result.leading_features),
        "report_acceptance_pass": report_result.acceptance_pass,
    }
    if manifest != expected:
        raise RuntimeError("event_run_manifest_mismatch")
    if not isinstance(event_manifest, dict) or event_manifest.get("code_revision") != code_revision:
        raise RuntimeError("event_library_code_revision_mismatch")
    if not isinstance(label_manifest, dict) or label_manifest.get("code_revision") != code_revision:
        raise RuntimeError("label_corpus_code_revision_mismatch")
    if (
        not isinstance(report_manifest, dict)
        or report_manifest.get("code_revision") != code_revision
    ):
        raise RuntimeError("comparison_report_code_revision_mismatch")
    runtime = _load_runtime_receipt(path / "runtime-metrics.json", required=False)
    return (
        event_result,
        label_result,
        report_result,
        _result_ref(
            run_id=run_id,
            artifact_ref=path,
            event_result=event_result,
            label_result=label_result,
            report_result=report_result,
            runtime=runtime,
        ),
    )


class EventWorker:
    """One static read-only-DWD worker for exact admitted event and structure research Jobs."""

    def __init__(
        self,
        store: PostgresPlatformStore,
        *,
        worker_id: str,
        run_root: Path,
        lease_seconds: int,
        renew_seconds: float,
        terminate_seconds: float,
        shard_target: int = 0,
    ) -> None:
        self.store = store
        self.worker_id = worker_id
        self.run_root = run_root.resolve()
        self.lease_seconds = lease_seconds
        self.renew_seconds = renew_seconds
        self.terminate_seconds = terminate_seconds
        if shard_target not in {0, 1, 2, 3, 4}:
            raise ValueError("Event shard target must be between zero and four")
        self.shard_target = shard_target
        self.stopping = False

    def run_once(self) -> bool:
        job = self.store.claim_job(EVENT_QUEUE, self.worker_id, self.lease_seconds)
        if job is None:
            return False
        if job.kind not in {P4_EVENT_SCAN_JOB, STRUCTURE_MVP_JOB}:
            self.store.finish_claimed_job(
                job.job_id,
                job.claim_generation,
                state="failed",
                result_ref=None,
                error_reason="job_kind_not_admitted",
            )
            return True
        try:
            if job.kind == STRUCTURE_MVP_JOB:
                self._execute_structure(
                    job.job_id,
                    job.claim_generation,
                    job.input_ref,
                    job.input_digest,
                )
            else:
                self._execute(
                    job.job_id,
                    job.claim_generation,
                    job.input_ref,
                    job.input_digest,
                )
        except Exception as exc:
            reason = f"event_worker_error:{type(exc).__name__}:{exc}"[:500]
            if job.kind == P4_EVENT_SCAN_JOB and self.shard_target:
                try:
                    self.store.cancel_job_shards(job.job_id, job.claim_generation)
                except RuntimeError as stale:
                    if str(stale) != "stale_claim_generation":
                        raise
            try:
                self.store.finish_claimed_job(
                    job.job_id,
                    job.claim_generation,
                    state="failed",
                    result_ref=None,
                    error_reason=reason,
                )
            except RuntimeError as stale:
                if str(stale) != "stale_claim_generation":
                    raise
        return True

    def _execute_structure(
        self,
        job_id: str,
        generation: int,
        input_ref: Mapping[str, Any],
        input_digest: str,
    ) -> None:
        governed = _structure_input(input_ref)
        observed = self.store.get_job(job_id)
        if observed is None or observed.claim_generation != generation:
            return
        request_digest = _structure_request_digest(governed["run_config"])
        run_id = f"run_{job_id.removeprefix('job_')}"
        final = self.run_root / "structure-runs" / run_id
        if final.exists():
            result_ref = _verify_structure_mvp_artifact(
                final,
                code_revision=governed["code_revision"],
                request_digest=request_digest,
            )
            result_ref["input_digest"] = input_digest
            self.store.update_claimed_job(
                job_id,
                generation,
                stage="recovering-sealed-structure-mvp-run",
                progress=98,
                progress_detail={
                    "run_id": run_id,
                    "row_count": result_ref["row_count"],
                    "p2_probe_count": result_ref["p2_probe_count"],
                    "p2_fill_count": result_ref["p2_fill_count"],
                },
            )
            self.store.finish_claimed_job(
                job_id,
                generation,
                state="succeeded",
                result_ref=result_ref,
                error_reason=None,
            )
            return

        staging = self.run_root / ".staging" / job_id / str(generation)
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        stdout_path = staging / "stdout.log"
        stderr_path = staging / "stderr.log"
        artifact_root = staging / "structure-mvp"
        command = (
            sys.executable,
            "-m",
            "quanthus.structure_mvp_run",
            "--request",
            governed["run_config"],
            "--output",
            str(artifact_root),
            "--code-revision",
            governed["code_revision"],
        )
        self.store.update_claimed_job(
            job_id,
            generation,
            stage="running-structure-mvp",
            progress=5,
            progress_detail={"run_id": run_id},
        )
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            process = subprocess.Popen(command, stdout=stdout, stderr=stderr)  # noqa: S603
        last_renewal = time.monotonic()
        canceled = False
        lost_lease = False
        while process.poll() is None:
            time.sleep(1)
            observed = self.store.get_job(job_id)
            if observed is None or observed.claim_generation != generation:
                lost_lease = True
                _terminate(process, self.terminate_seconds)
                break
            if observed.cancel_requested or self.stopping:
                canceled = observed.cancel_requested
                _terminate(process, self.terminate_seconds)
                break
            if time.monotonic() - last_renewal >= self.renew_seconds:
                if not self.store.renew_job_lease(
                    job_id, generation, self.worker_id, self.lease_seconds
                ):
                    lost_lease = True
                    _terminate(process, self.terminate_seconds)
                    break
                last_renewal = time.monotonic()
        if lost_lease:
            return
        if canceled:
            self.store.finish_claimed_job(
                job_id,
                generation,
                state="canceled",
                result_ref=None,
                error_reason=None,
            )
            return
        if process.returncode != 0 or not artifact_root.is_dir():
            self.store.finish_claimed_job(
                job_id,
                generation,
                state="failed",
                result_ref=None,
                error_reason=f"structure_mvp_child_exit_{process.returncode}",
            )
            return

        self.store.update_claimed_job(
            job_id,
            generation,
            stage="validating-structure-mvp-run",
            progress=92,
            progress_detail={"run_id": run_id},
        )
        result_ref = _verify_structure_mvp_artifact(
            artifact_root,
            code_revision=governed["code_revision"],
            request_digest=request_digest,
        )
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():
            raise RuntimeError("immutable_structure_mvp_run_already_exists")
        os.replace(artifact_root, final)
        shutil.rmtree(staging, ignore_errors=True)
        result_ref = _verify_structure_mvp_artifact(
            final,
            code_revision=governed["code_revision"],
            request_digest=request_digest,
        )
        result_ref["input_digest"] = input_digest
        self.store.finish_claimed_job(
            job_id,
            generation,
            state="succeeded",
            result_ref=result_ref,
            error_reason=None,
        )

    def _execute(
        self,
        job_id: str,
        generation: int,
        input_ref: Mapping[str, Any],
        input_digest: str,
    ) -> None:
        governed = _event_input(input_ref)
        observed = self.store.get_job(job_id)
        if observed is None or observed.claim_generation != generation:
            return
        run_id = f"run_{job_id.removeprefix('job_')}"
        final = self.run_root / "runs" / run_id
        if final.exists():
            event_result, label_result, report_result, result_ref = _validate_run_directory(
                final,
                run_id=run_id,
                job_id=job_id,
                input_digest=input_digest,
                code_revision=governed["code_revision"],
            )
            self.store.update_claimed_job(
                job_id,
                generation,
                stage="recovering-sealed-event-run",
                progress=98,
                progress_detail={
                    "run_id": run_id,
                    "candidate_events": event_result.candidate_events,
                    "three_class_events": report_result.three_class_events,
                    "leading_features": list(report_result.leading_features),
                },
            )
            self.store.finish_claimed_job(
                job_id,
                generation,
                state="succeeded",
                result_ref=result_ref,
                error_reason=None,
            )
            return

        staging = self.run_root / ".staging" / job_id / str(generation)
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        stdout_path = staging / "stdout.log"
        stderr_path = staging / "stderr.log"
        event_root = staging / "event-library"
        label_root = staging / "label-corpus"
        report_root = staging / "comparison-report"
        runtime_path = staging / "runtime-metrics.json"
        progress_path = staging / "progress.json"
        command: tuple[str, ...] = (
            sys.executable,
            "-m",
            (
                "quanthus.event_library.sharded_batch"
                if self.shard_target
                else "quanthus"
            ),
            *(("run-p4-reported-event-scan",) if not self.shard_target else ()),
            "--source-policy",
            governed["source_policy"],
            "--provider-config",
            governed["provider_config"],
            "--rule-card",
            governed["rule_card"],
            "--split-policy",
            governed["split_policy"],
            "--outcome-card",
            governed["outcome_card"],
            "--structural-break-card",
            governed["structural_break_card"],
            "--feature-registry",
            governed["feature_registry"],
            "--report-policy",
            governed["report_policy"],
            "--event-output",
            str(event_root),
            "--label-output",
            str(label_root),
            "--report-output",
            str(report_root),
            "--runtime-receipt",
            str(runtime_path),
            "--progress-receipt",
            str(progress_path),
            "--code-revision",
            governed["code_revision"],
        )
        if self.shard_target:
            command += (
                "--job-id",
                job_id,
                "--parent-generation",
                str(generation),
                "--shard-target",
                str(self.shard_target),
            )
        self.store.update_claimed_job(
            job_id,
            generation,
            stage="running-event-scan",
            progress=5,
            progress_detail={"run_id": run_id},
        )
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            process = subprocess.Popen(command, stdout=stdout, stderr=stderr)  # noqa: S603
        last_renewal = time.monotonic()
        last_progress_mtime_ns: int | None = None
        canceled = False
        lost_lease = False
        while process.poll() is None:
            time.sleep(1)
            observed = self.store.get_job(job_id)
            if observed is None or observed.claim_generation != generation:
                lost_lease = True
                _terminate(process, self.terminate_seconds)
                break
            if observed.cancel_requested or self.stopping:
                canceled = observed.cancel_requested
                _terminate(process, self.terminate_seconds)
                break
            try:
                progress_mtime_ns = progress_path.stat().st_mtime_ns
            except OSError:
                progress_mtime_ns = None
            if (
                progress_mtime_ns is not None
                and progress_mtime_ns != last_progress_mtime_ns
            ):
                receipt = _load_progress_receipt(progress_path)
                last_progress_mtime_ns = progress_mtime_ns
                if receipt is not None:
                    detail = dict(receipt["detail"])
                    detail["run_id"] = run_id
                    try:
                        self.store.update_claimed_job(
                            job_id,
                            generation,
                            stage=str(receipt["stage"]),
                            progress=max(observed.progress, int(receipt["progress"])),
                            progress_detail=detail,
                        )
                    except RuntimeError as exc:
                        if str(exc) != "stale_claim_generation":
                            raise
                        lost_lease = True
                        _terminate(process, self.terminate_seconds)
                        break
            if time.monotonic() - last_renewal >= self.renew_seconds:
                if not self.store.renew_job_lease(
                    job_id, generation, self.worker_id, self.lease_seconds
                ):
                    lost_lease = True
                    _terminate(process, self.terminate_seconds)
                    break
                last_renewal = time.monotonic()
        if lost_lease:
            return
        if canceled:
            self.store.finish_claimed_job(
                job_id,
                generation,
                state="canceled",
                result_ref=None,
                error_reason=None,
            )
            return
        if (
            process.returncode != 0
            or not event_root.is_dir()
            or not label_root.is_dir()
            or not report_root.is_dir()
            or not runtime_path.is_file()
        ):
            self.store.finish_claimed_job(
                job_id,
                generation,
                state="failed",
                result_ref=None,
                error_reason=f"event_scan_child_exit_{process.returncode}",
            )
            return

        self.store.update_claimed_job(
            job_id,
            generation,
            stage="validating-reported-event-run",
            progress=90,
            progress_detail={"run_id": run_id},
        )
        event_result = _verify_event_library(event_root)
        label_result = _verify_label_corpus(label_root)
        report_result = _verify_comparison_report(report_root)
        runtime = _load_runtime_receipt(runtime_path, required=True)
        assert runtime is not None
        progress_path.unlink(missing_ok=True)
        if label_result.total_events != event_result.candidate_events:
            raise RuntimeError("label_corpus_event_count_mismatch")
        if label_result.not_executable_events != event_result.not_executable_events:
            raise RuntimeError("label_corpus_not_executable_count_mismatch")
        if report_result.candidate_events != event_result.candidate_events:
            raise RuntimeError("comparison_report_event_count_mismatch")
        three_class_events = (
            label_result.positive_events
            + label_result.neutral_events
            + label_result.failure_events
        )
        if report_result.three_class_events != three_class_events:
            raise RuntimeError("comparison_report_three_class_count_mismatch")
        run_manifest = {
            "schema_version": RUN_SCHEMA,
            "run_id": run_id,
            "job_id": job_id,
            "input_digest": input_digest,
            "code_revision": governed["code_revision"],
            "event_manifest_sha256": event_result.manifest_sha256,
            "event_content_digest": event_result.content_digest,
            "label_manifest_sha256": label_result.manifest_sha256,
            "label_content_digest": label_result.content_digest,
            "candidate_events": event_result.candidate_events,
            "executable_events": event_result.executable_events,
            "not_executable_events": event_result.not_executable_events,
            "positive_events": label_result.positive_events,
            "neutral_events": label_result.neutral_events,
            "failure_events": label_result.failure_events,
            "censored_events": label_result.censored_events,
            "unavailable_events": label_result.unavailable_events,
            "report_manifest_sha256": report_result.manifest_sha256,
            "report_content_digest": report_result.content_digest,
            "leading_features": list(report_result.leading_features),
            "report_acceptance_pass": report_result.acceptance_pass,
        }
        (staging / "run-manifest.json").write_text(
            json.dumps(run_manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.store.update_claimed_job(
            job_id,
            generation,
            stage="sealing-reported-event-run",
            progress=97,
            progress_detail={
                "run_id": run_id,
                "candidate_events": event_result.candidate_events,
                "three_class_events": report_result.three_class_events,
                "leading_features": list(report_result.leading_features),
                "total_elapsed_seconds": runtime["total_elapsed_seconds"],
                "reuse": runtime["reuse"],
            },
        )
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():
            raise RuntimeError("immutable_event_run_already_exists")
        os.replace(staging, final)
        _event_result, _label_result, _report_result, result_ref = _validate_run_directory(
            final,
            run_id=run_id,
            job_id=job_id,
            input_digest=input_digest,
            code_revision=governed["code_revision"],
        )
        self.store.finish_claimed_job(
            job_id,
            generation,
            state="succeeded",
            result_ref=result_ref,
            error_reason=None,
        )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    dsn = os.environ.get("QUANTHUS_PLATFORM_DATABASE_URL", "").strip()
    if not dsn:
        raise RuntimeError("QUANTHUS_PLATFORM_DATABASE_URL is required")
    worker_id = os.environ.get("QUANTHUS_WORKER_ID", "").strip() or socket.gethostname()
    run_root = Path(os.environ.get("QUANTHUS_EVENT_RUN_ROOT", "/data/event-runs"))
    store = PostgresPlatformStore(dsn)
    try:
        _seed_structure_mvp_qualification_jobs(
            store,
            enabled_value=os.environ.get(_STRUCTURE_QUALIFICATION_SEED_ENV, "0"),
            code_revision=os.environ.get(_BUILD_REVISION_ENV, "").strip(),
        )
    except RuntimeError as exc:
        print(f"structure qualification seed rejected: {exc}", file=sys.stderr)
    worker = EventWorker(
        store,
        worker_id=worker_id,
        run_root=run_root,
        lease_seconds=args.lease_seconds,
        renew_seconds=args.renew_seconds,
        terminate_seconds=args.terminate_seconds,
        shard_target=int(os.environ.get("QUANTHUS_EVENT_SHARD_TARGET", "0")),
    )

    def stop(_signum: int, _frame: object) -> None:
        worker.stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not worker.stopping:
        worked = worker.run_once()
        if args.once:
            return 0
        if not worked:
            time.sleep(args.poll_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

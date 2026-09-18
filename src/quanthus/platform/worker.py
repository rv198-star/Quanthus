"""Lean PostgreSQL Replay Worker Supervisor."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from quanthus.expert_evaluation.runner import ExpertEvaluationJobInput
from quanthus.forward_validation.activation import runtime_code_revision
from quanthus.forward_validation.controller import controller_tick, record_controller_error
from quanthus.forward_validation.official_cycle import OfficialCycleJobInput
from quanthus.forward_validation.runner import ForwardCycleJobInput
from quanthus.platform.jobs import CANDIDATE_REPLAY_JOB, EXPERT_EVALUATION_JOB, P6_FORWARD_CYCLE_JOB
from quanthus.platform.persistence import PostgresPlatformStore
from quanthus.platform.worker_handlers import (
    EXPERT_STAGE_PROGRESS,
    P6_FORWARD_STAGE_PROGRESS,
    ExpertChildResult,
    expert_child_command,
    find_expert_run_for_input,
    find_p6_forward_run_for_input,
    p6_forward_child_command,
    validate_expert_child_result,
    validate_p6_forward_child_result,
)

STAGE_PROGRESS = {
    "loading-contracts": 2,
    "contracts-loaded": 3,
    "opening-data-provider": 4,
    "dwd-authority-validated": 6,
    "dwd-source-scan-ready": 8,
    "dwd-market-projection-ready": 12,
    "dwd-action-projection-ready": 14,
    "dwd-projection-validated": 16,
    "data-provider-opened": 5,
    "building-shared-feature-panel": 18,
    "shared-feature-panel-ready": 30,
    "baseline-suite-complete": 60,
    "baseline-suite-failed": 60,
    "governed-candidate-complete": 80,
    "governed-candidate-failed": 80,
    "external-candidate-complete": 95,
    "external-candidate-failed": 95,
    "batch-complete": 99,
}
DEFAULT_MEMORY_CURRENT_PATH = Path("/sys/fs/cgroup/memory.current")
DEFAULT_MEMORY_STAT_PATH = Path("/sys/fs/cgroup/memory.stat")
DEFAULT_MEMORY_STOP_BYTES = 14 * 1024**3


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the lean Quanthus Replay Worker")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=3.0)
    parser.add_argument("--lease-seconds", type=int, default=300)
    parser.add_argument("--renew-seconds", type=float, default=30.0)
    parser.add_argument("--terminate-seconds", type=float, default=20.0)
    parser.add_argument(
        "--memory-current-path",
        type=Path,
        default=Path(
            os.environ.get(
                "QUANTHUS_REPLAY_MEMORY_CURRENT_PATH", str(DEFAULT_MEMORY_CURRENT_PATH)
            )
        ),
    )
    parser.add_argument(
        "--memory-stop-bytes",
        type=int,
        default=int(
            os.environ.get(
                "QUANTHUS_REPLAY_MEMORY_STOP_BYTES", str(DEFAULT_MEMORY_STOP_BYTES)
            )
        ),
    )
    parser.add_argument(
        "--memory-stat-path",
        type=Path,
        default=Path(
            os.environ.get("QUANTHUS_REPLAY_MEMORY_STAT_PATH", str(DEFAULT_MEMORY_STAT_PATH))
        ),
    )
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


def _candidate_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    evidence_index = payload.get("evidence_index")
    candidates = payload.get("candidates")
    if not isinstance(evidence_index, list) or len(evidence_index) != 5:
        raise RuntimeError("candidate_evidence_incomplete")
    if not isinstance(candidates, list) or len(candidates) != 5:
        raise RuntimeError("candidate_result_incomplete")
    evidence_by_identity = {
        f"{item['candidate_id']}@{item['candidate_version']}": item
        for item in evidence_index
        if isinstance(item, dict)
    }
    if len(evidence_by_identity) != 5:
        raise RuntimeError("candidate_evidence_index_invalid")
    try:
        return tuple(
            {
                **dict(item),
                **evidence_by_identity[
                    f"{item['candidate_id']}@{item['candidate_version']}"
                ],
            }
            for item in candidates
            if isinstance(item, dict)
        )
    except KeyError as exc:
        raise RuntimeError("candidate_evidence_identity_mismatch") from exc


def _validate_run_directory(
    path: Path,
    *,
    run_id: str,
    job_id: str,
    input_digest: str,
) -> tuple[dict[str, Any], str, tuple[dict[str, Any], ...]]:
    try:
        manifest = json.loads((path / "run.json").read_text(encoding="utf-8"))
        payload = json.loads((path / "result.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("sealed_run_unreadable") from exc
    digest = _sha256(path / "result.json")
    if (
        manifest.get("schema_version") != "quanthus.candidate-run.v1"
        or manifest.get("run_id") != run_id
        or manifest.get("job_id") != job_id
        or manifest.get("input_digest") != input_digest
        or manifest.get("result_sha256") != digest
        or payload.get("status") != "succeeded"
    ):
        raise RuntimeError("sealed_run_manifest_mismatch")
    candidates = _candidate_payload(payload)
    if manifest.get("candidate_count") != len(candidates):
        raise RuntimeError("sealed_run_candidate_count_mismatch")
    for item in candidates:
        relative = Path(str(item["artifact"]))
        evidence_path = (path / relative).resolve()
        if path.resolve() not in evidence_path.parents:
            raise RuntimeError("sealed_run_evidence_path_escape")
        if not evidence_path.is_file() or _sha256(evidence_path) != item.get(
            "artifact_sha256"
        ):
            raise RuntimeError("sealed_run_evidence_checksum_mismatch")
    return payload, digest, candidates


class ReplayWorker:
    def __init__(
        self,
        store: PostgresPlatformStore,
        *,
        worker_id: str,
        run_root: Path,
        lease_seconds: int,
        renew_seconds: float,
        terminate_seconds: float,
        expert_run_root: Path | None = None,
        expert_registry_root: Path | None = None,
        p6_forward_run_root: Path | None = None,
        p6_forward_config_path: Path | None = None,
        p6_prospective_runtime_path: Path | None = None,
        memory_current_path: Path | None = None,
        memory_stat_path: Path | None = None,
        memory_stop_bytes: int = DEFAULT_MEMORY_STOP_BYTES,
    ) -> None:
        if memory_stop_bytes < 1:
            raise ValueError("memory_stop_bytes must be positive")
        self.store = store
        self.worker_id = worker_id
        self.run_root = run_root.resolve()
        self.lease_seconds = lease_seconds
        self.renew_seconds = renew_seconds
        self.terminate_seconds = terminate_seconds
        self.expert_run_root = (expert_run_root or self.run_root / "expert-evaluations").resolve()
        self.expert_registry_root = (
            expert_registry_root or Path("config/expert-evaluations")
        ).resolve()
        self.p6_forward_run_root = (
            p6_forward_run_root or self.run_root / "p6-forward"
        ).resolve()
        self.p6_forward_config_path = (
            p6_forward_config_path
            or Path("config/forward-validation/runtime/p6-forward-worker.v1.json")
        ).resolve()
        self.p6_prospective_runtime_path = (
            p6_prospective_runtime_path
            or Path("config/forward-validation/activation/p6-prospective-runtime.v1.json")
        ).resolve()
        self.memory_current_path = memory_current_path
        self.memory_stat_path = memory_stat_path
        self.memory_stop_bytes = memory_stop_bytes
        self.stopping = False

    def _memory_guard_failure(self) -> tuple[str, dict[str, Any]] | None:
        if self.memory_current_path is None:
            return None
        if self.memory_stat_path is None:
            return (
                "replay_memory_monitor_unavailable",
                {"error_type": "memory_stat_path_missing"},
            )
        try:
            current_bytes = int(self.memory_current_path.read_text(encoding="ascii").strip())
            stat_values = {
                key: int(value)
                for key, value in (
                    line.split()
                    for line in self.memory_stat_path.read_text(encoding="ascii").splitlines()
                )
            }
            inactive_file_bytes = stat_values["inactive_file"]
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            return (
                "replay_memory_monitor_unavailable",
                {"error_type": type(exc).__name__},
            )
        except (KeyError, TypeError):
            return (
                "replay_memory_monitor_unavailable",
                {"error_type": "inactive_file_missing"},
            )
        if current_bytes < 0 or inactive_file_bytes < 0:
            return (
                "replay_memory_monitor_unavailable",
                {"error_type": "negative_memory_measurement"},
            )
        working_set_bytes = max(current_bytes - inactive_file_bytes, 0)
        if working_set_bytes >= self.memory_stop_bytes:
            return (
                "replay_memory_guard_exceeded",
                {
                    "memory_current_bytes": current_bytes,
                    "memory_inactive_file_bytes": inactive_file_bytes,
                    "memory_working_set_bytes": working_set_bytes,
                    "memory_stop_bytes": self.memory_stop_bytes,
                },
            )
        return None

    def _record_memory_failure(
        self,
        job_id: str,
        generation: int,
        failure: tuple[str, dict[str, Any]],
    ) -> bool:
        reason, detail = failure
        try:
            self.store.update_claimed_job(
                job_id,
                generation,
                stage="resource-limit-exceeded",
                progress=5,
                progress_detail={"reason": reason, **detail},
            )
        except RuntimeError as exc:
            if str(exc) == "stale_claim_generation":
                return False
            raise
        return True

    def _finish_memory_failure(
        self,
        job_id: str,
        generation: int,
        failure: tuple[str, dict[str, Any]],
    ) -> None:
        reason, detail = failure
        detail_text = ":".join(f"{key}={value}" for key, value in sorted(detail.items()))
        self.store.finish_claimed_job(
            job_id,
            generation,
            state="failed",
            result_ref=None,
            error_reason=f"{reason}:{detail_text}"[:500],
        )

    def run_once(self) -> bool:
        job = self.store.claim_job("replay", self.worker_id, self.lease_seconds)
        if job is None:
            return False
        handlers = {
            CANDIDATE_REPLAY_JOB: self._execute,
            EXPERT_EVALUATION_JOB: self._execute_expert,
            P6_FORWARD_CYCLE_JOB: self._execute_p6_forward,
        }
        handler = handlers.get(job.kind)
        if handler is None:
            self.store.finish_claimed_job(
                job.job_id,
                job.claim_generation,
                state="failed",
                result_ref=None,
                error_reason="job_kind_not_admitted",
            )
            return True
        try:
            handler(job.job_id, job.claim_generation, job.input_ref)
        except Exception as exc:
            reason = f"worker_error:{type(exc).__name__}:{exc}"[:500]
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

    def _p6_forward_result_ref(self, child: Any) -> dict[str, Any]:
        return {
            "run_id": child.run.run_id,
            "artifact_ref": str(child.run.root),
            "manifest_digest": child.run.manifest_digest,
            "input_digest": child.run.input_digest,
            "summary": child.summary,
        }

    def _execute_p6_forward(
        self, job_id: str, generation: int, input_ref: dict[str, Any]
    ) -> None:
        observed = self.store.get_job(job_id)
        if observed is None or observed.claim_generation != generation:
            return
        mode = input_ref.get("mode")
        if mode == "disabled_rehearsal":
            job_input = ForwardCycleJobInput.from_dict(input_ref)
        elif mode == "official_cycle":
            job_input = OfficialCycleJobInput.from_dict(input_ref)
        else:
            raise RuntimeError("p6_forward_mode_not_admitted")
        if job_input.input_digest != observed.input_digest:
            raise RuntimeError("p6_forward_input_digest_mismatch")
        run_root = self.p6_forward_run_root / "runs"
        recovered = find_p6_forward_run_for_input(run_root, observed.input_digest)
        if recovered is not None:
            self.store.update_claimed_job(
                job_id,
                generation,
                stage="recovering-sealed-run",
                progress=98,
                progress_detail={"run_id": recovered.run.run_id},
            )
            self.store.finish_claimed_job(
                job_id,
                generation,
                state="succeeded",
                result_ref=self._p6_forward_result_ref(recovered),
                error_reason=None,
            )
            return
        attempt = self.p6_forward_run_root / ".staging" / job_id / str(generation)
        if attempt.exists():
            shutil.rmtree(attempt)
        attempt.mkdir(parents=True)
        input_path = attempt / "input.json"
        input_path.write_bytes(
            json.dumps(job_input.to_dict(), sort_keys=True).encode("utf-8") + b"\n"
        )
        stdout_path = attempt / "result.json"
        stderr_path = attempt / "progress.log"
        command = p6_forward_child_command(
            input_path=input_path,
            config_path=self.p6_forward_config_path,
            runtime_path=self.p6_prospective_runtime_path,
            staging=attempt / "work",
            run_root=run_root,
        )
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            process = subprocess.Popen(command, stdout=stdout, stderr=stderr)  # noqa: S603
        last_renewal = time.monotonic()
        progress_offset = 0
        canceled = False
        lost_lease = False
        memory_failure: tuple[str, dict[str, Any]] | None = None
        while process.poll() is None:
            memory_failure = self._memory_guard_failure()
            if memory_failure is not None:
                if not self._record_memory_failure(job_id, generation, memory_failure):
                    lost_lease = True
                _terminate(process, self.terminate_seconds)
                break
            time.sleep(0.1)
            progress_offset = self._publish_p6_forward_progress(
                stderr_path, progress_offset, job_id, generation
            )
            current = self.store.get_job(job_id)
            if current is None or current.claim_generation != generation:
                lost_lease = True
                _terminate(process, self.terminate_seconds)
                break
            if current.cancel_requested:
                canceled = True
                _terminate(process, self.terminate_seconds)
                break
            if self.stopping:
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
        if memory_failure is not None:
            self._finish_memory_failure(job_id, generation, memory_failure)
            return
        self._publish_p6_forward_progress(stderr_path, progress_offset, job_id, generation)
        if canceled:
            self.store.finish_claimed_job(
                job_id, generation, state="canceled", result_ref=None, error_reason=None
            )
            return
        if process.returncode != 0 or not stdout_path.is_file():
            self.store.finish_claimed_job(
                job_id,
                generation,
                state="failed",
                result_ref=None,
                error_reason=f"p6_forward_child_exit_{process.returncode}",
            )
            return
        try:
            payload = json.loads(stdout_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("p6_forward_child_result_unreadable") from exc
        child = validate_p6_forward_child_result(payload, run_root)
        if child.run.input_digest != observed.input_digest:
            raise RuntimeError("p6_forward_run_input_mismatch")
        self.store.update_claimed_job(
            job_id,
            generation,
            stage="publishing-run",
            progress=99,
            progress_detail={"run_id": child.run.run_id},
        )
        self.store.finish_claimed_job(
            job_id,
            generation,
            state="succeeded",
            result_ref=self._p6_forward_result_ref(child),
            error_reason=None,
        )

    def _publish_p6_forward_progress(
        self, path: Path, offset: int, job_id: str, generation: int
    ) -> int:
        with path.open("rb") as stream:
            stream.seek(offset)
            lines = stream.readlines()
            next_offset = stream.tell()
        for raw_line in lines:
            try:
                event = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if event.get("event") != "p6-forward-progress":
                continue
            stage = str(event.get("stage", "running"))
            detail = event.get("detail")
            if not isinstance(detail, dict):
                detail = {}
            try:
                self.store.update_claimed_job(
                    job_id,
                    generation,
                    stage=stage,
                    progress=P6_FORWARD_STAGE_PROGRESS.get(stage, 5),
                    progress_detail=detail,
                )
            except RuntimeError as exc:
                if str(exc) == "stale_claim_generation":
                    return next_offset
                raise
        return next_offset

    def _execute_expert(
        self, job_id: str, generation: int, input_ref: dict[str, Any]
    ) -> None:
        observed = self.store.get_job(job_id)
        if observed is None or observed.claim_generation != generation:
            return
        job_input = ExpertEvaluationJobInput.from_dict(input_ref)
        if job_input.input_digest != observed.input_digest:
            raise RuntimeError("expert_input_digest_mismatch")
        run_root = self.expert_run_root / "runs"
        existing = self.store.expert_evaluation_run_for_input(observed.input_digest)
        recovered: ExpertChildResult | None
        if existing is not None:
            recovered = validate_expert_child_result(
                {
                    "run_id": existing.run_id,
                    "artifact_ref": existing.artifact_ref,
                    "manifest_digest": existing.manifest_digest,
                    "evaluation_identities": list(existing.evaluation_identities),
                    "strategy_identity": existing.strategy_identity,
                    "summary": existing.summary,
                },
                run_root,
            )
        else:
            recovered = find_expert_run_for_input(run_root, observed.input_digest)
        if recovered is not None:
            self.store.update_claimed_job(
                job_id,
                generation,
                stage="recovering-sealed-run",
                progress=98,
                progress_detail={"run_id": recovered.run.run_id},
            )
            self.store.publish_expert_evaluation_run(
                job_id,
                generation,
                run_id=recovered.run.run_id,
                artifact_ref=str(recovered.run.root),
                manifest_digest=recovered.run.manifest_digest,
                evaluation_identities=recovered.evaluation_identities,
                strategy_identity=recovered.strategy_identity,
                summary=recovered.summary,
            )
            return
        attempt = self.expert_run_root / ".staging" / job_id / str(generation)
        if attempt.exists():
            shutil.rmtree(attempt)
        attempt.mkdir(parents=True)
        input_path = attempt / "input.json"
        input_path.write_bytes(job_input.canonical_bytes)
        stdout_path = attempt / "result.json"
        stderr_path = attempt / "progress.log"
        command = expert_child_command(
            input_path=input_path,
            registry_root=self.expert_registry_root,
            staging=attempt / "work",
            run_root=run_root,
        )
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            process = subprocess.Popen(command, stdout=stdout, stderr=stderr)  # noqa: S603
        last_renewal = time.monotonic()
        progress_offset = 0
        canceled = False
        lost_lease = False
        memory_failure: tuple[str, dict[str, Any]] | None = None
        while process.poll() is None:
            memory_failure = self._memory_guard_failure()
            if memory_failure is not None:
                if not self._record_memory_failure(job_id, generation, memory_failure):
                    lost_lease = True
                _terminate(process, self.terminate_seconds)
                break
            time.sleep(1)
            progress_offset = self._publish_expert_progress(
                stderr_path, progress_offset, job_id, generation
            )
            current = self.store.get_job(job_id)
            if current is None or current.claim_generation != generation:
                lost_lease = True
                _terminate(process, self.terminate_seconds)
                break
            if current.cancel_requested:
                canceled = True
                _terminate(process, self.terminate_seconds)
                break
            if self.stopping:
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
        if memory_failure is not None:
            self._finish_memory_failure(job_id, generation, memory_failure)
            return
        self._publish_expert_progress(stderr_path, progress_offset, job_id, generation)
        if canceled:
            self.store.finish_claimed_job(
                job_id, generation, state="canceled", result_ref=None, error_reason=None
            )
            return
        if process.returncode != 0 or not stdout_path.is_file():
            self.store.finish_claimed_job(
                job_id,
                generation,
                state="failed",
                result_ref=None,
                error_reason=f"expert_child_exit_{process.returncode}",
            )
            return
        try:
            payload = json.loads(stdout_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("expert_child_result_unreadable") from exc
        child = validate_expert_child_result(payload, run_root)
        manifest_input = ExpertEvaluationJobInput.from_dict(child.run.manifest["job_input"])
        if manifest_input.input_digest != observed.input_digest:
            raise RuntimeError("expert_run_input_mismatch")
        self.store.update_claimed_job(
            job_id,
            generation,
            stage="publishing-run",
            progress=99,
            progress_detail={"run_id": child.run.run_id},
        )
        self.store.publish_expert_evaluation_run(
            job_id,
            generation,
            run_id=child.run.run_id,
            artifact_ref=str(child.run.root),
            manifest_digest=child.run.manifest_digest,
            evaluation_identities=child.evaluation_identities,
            strategy_identity=child.strategy_identity,
            summary=child.summary,
        )

    def _publish_expert_progress(
        self, path: Path, offset: int, job_id: str, generation: int
    ) -> int:
        with path.open("rb") as stream:
            stream.seek(offset)
            lines = stream.readlines()
            next_offset = stream.tell()
        for raw_line in lines:
            try:
                event = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if event.get("event") != "expert-evaluation-progress":
                continue
            stage = str(event.get("stage", "running"))
            detail = event.get("detail")
            if not isinstance(detail, dict):
                detail = {}
            try:
                self.store.update_claimed_job(
                    job_id,
                    generation,
                    stage=stage,
                    progress=EXPERT_STAGE_PROGRESS.get(stage, 5),
                    progress_detail=detail,
                )
            except RuntimeError as exc:
                if str(exc) == "stale_claim_generation":
                    return next_offset
                raise
        return next_offset

    def _execute(self, job_id: str, generation: int, input_ref: dict[str, Any]) -> None:
        observed = self.store.get_job(job_id)
        if observed is None or observed.claim_generation != generation:
            return
        input_digest = observed.input_digest
        run_id = f"run_{job_id.removeprefix('job_')}"
        final = self.run_root / "runs" / run_id
        if final.exists():
            _payload, digest, candidates = _validate_run_directory(
                final,
                run_id=run_id,
                job_id=job_id,
                input_digest=input_digest,
            )
            self.store.update_claimed_job(
                job_id,
                generation,
                stage="recovering-sealed-run",
                progress=98,
                progress_detail={"run_id": run_id},
            )
            self.store.publish_candidate_run(
                job_id,
                generation,
                run_id=run_id,
                artifact_ref=str(final),
                result_sha256=digest,
                candidates=candidates,
                expected_active_runs=dict(input_ref.get("expected_active_runs", {})),
            )
            return
        staging = self.run_root / ".staging" / job_id / str(generation)
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        result_path = staging / "result.json"
        stdout_path = staging / "stdout.log"
        stderr_path = staging / "progress.log"
        command = (
            sys.executable,
            "-m",
            "quanthus",
            "run-candidate-batch",
            "--provider-config",
            str(input_ref["provider_config"]),
            "--scope-start",
            str(input_ref["scope_start"]),
            "--scope-end",
            str(input_ref["scope_end"]),
            "--output",
            str(result_path),
            "--evidence-dir",
            str(staging / "evidence"),
        )
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            process = subprocess.Popen(command, stdout=stdout, stderr=stderr)  # noqa: S603
        last_renewal = time.monotonic()
        progress_offset = 0
        canceled = False
        lost_lease = False
        memory_failure: tuple[str, dict[str, Any]] | None = None
        while process.poll() is None:
            memory_failure = self._memory_guard_failure()
            if memory_failure is not None:
                if not self._record_memory_failure(job_id, generation, memory_failure):
                    lost_lease = True
                _terminate(process, self.terminate_seconds)
                break
            time.sleep(1)
            progress_offset = self._publish_progress(
                stderr_path, progress_offset, job_id, generation
            )
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
        if memory_failure is not None:
            self._finish_memory_failure(job_id, generation, memory_failure)
            return
        progress_offset = self._publish_progress(stderr_path, progress_offset, job_id, generation)
        del progress_offset
        if canceled:
            self.store.finish_claimed_job(
                job_id, generation, state="canceled", result_ref=None, error_reason=None
            )
            return
        if process.returncode != 0 or not result_path.is_file():
            self.store.finish_claimed_job(
                job_id,
                generation,
                state="failed",
                result_ref=None,
                error_reason=f"replay_child_exit_{process.returncode}",
            )
            return
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        if payload.get("status") != "succeeded":
            self.store.finish_claimed_job(
                job_id,
                generation,
                state="failed",
                result_ref=None,
                error_reason="candidate_batch_failed",
            )
            return
        evidence_index = payload.get("evidence_index")
        candidates = _candidate_payload(payload)
        assert isinstance(evidence_index, list)
        for item in evidence_index:
            assert isinstance(item, dict)
            evidence_path = staging / str(item["artifact"]).removeprefix("evidence/")
            evidence_path = staging / "evidence" / evidence_path.name
            if not evidence_path.is_file() or _sha256(evidence_path) != item.get(
                "artifact_sha256"
            ):
                raise RuntimeError("candidate evidence artifact checksum differs")
        digest = _sha256(result_path)
        manifest = {
            "schema_version": "quanthus.candidate-run.v1",
            "run_id": run_id,
            "job_id": job_id,
            "sealed_claim_generation": generation,
            "input_digest": input_digest,
            "result_sha256": digest,
            "candidate_count": payload.get("candidate_count"),
            "data_provider": payload.get("data_provider"),
        }
        (staging / "run.json").write_text(
            json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():
            raise RuntimeError("immutable Run already exists")
        os.replace(staging, final)
        _payload, digest, candidates = _validate_run_directory(
            final,
            run_id=run_id,
            job_id=job_id,
            input_digest=input_digest,
        )
        self.store.update_claimed_job(
            job_id,
            generation,
            stage="publishing-run",
            progress=99,
            progress_detail={"run_id": run_id, "candidate_count": len(candidates)},
        )
        self.store.publish_candidate_run(
            job_id,
            generation,
            run_id=run_id,
            artifact_ref=str(final),
            result_sha256=digest,
            candidates=candidates,
            expected_active_runs=dict(input_ref.get("expected_active_runs", {})),
        )

    def _publish_progress(
        self, path: Path, offset: int, job_id: str, generation: int
    ) -> int:
        with path.open("rb") as stream:
            stream.seek(offset)
            lines = stream.readlines()
            next_offset = stream.tell()
        for raw_line in lines:
            try:
                event = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if event.get("event") != "candidate-batch-progress":
                continue
            stage = str(event.get("stage", "running"))
            detail = {key: value for key, value in event.items() if key not in {"event", "stage"}}
            try:
                self.store.update_claimed_job(
                    job_id,
                    generation,
                    stage=stage,
                    progress=STAGE_PROGRESS.get(stage, 5),
                    progress_detail=detail,
                )
            except RuntimeError as exc:
                if str(exc) == "stale_claim_generation":
                    return next_offset
                raise
        return next_offset


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    dsn = os.environ.get("QUANTHUS_PLATFORM_DATABASE_URL", "").strip()
    if not dsn:
        raise RuntimeError("QUANTHUS_PLATFORM_DATABASE_URL is required")
    worker_id = os.environ.get("QUANTHUS_WORKER_ID", "").strip() or socket.gethostname()
    run_root = Path(os.environ.get("QUANTHUS_CANDIDATE_RUN_ROOT", "/data/candidate-runs"))
    expert_run_root = Path(
        os.environ.get("QUANTHUS_EXPERT_RUN_ROOT", "/data/expert-evaluation-runs")
    )
    expert_registry_root = Path(
        os.environ.get("QUANTHUS_EXPERT_REGISTRY_ROOT", "/app/config/expert-evaluations")
    )
    p6_forward_run_root = Path(
        os.environ.get("QUANTHUS_P6_FORWARD_RUN_ROOT", "/data/p6-forward-runs")
    )
    p6_forward_config_path = Path(
        os.environ.get(
            "QUANTHUS_P6_FORWARD_CONFIG",
            "/app/config/forward-validation/runtime/p6-forward-worker.v1.json",
        )
    )
    store = PostgresPlatformStore(dsn)
    p6_runtime_path = Path(
        os.environ.get(
            "QUANTHUS_P6_RUNTIME_CONFIG",
            "/app/config/forward-validation/activation/p6-prospective-runtime.v1.json",
        )
    )
    worker = ReplayWorker(
        store,
        worker_id=worker_id,
        run_root=run_root,
        lease_seconds=args.lease_seconds,
        renew_seconds=args.renew_seconds,
        terminate_seconds=args.terminate_seconds,
        expert_run_root=expert_run_root,
        expert_registry_root=expert_registry_root,
        p6_forward_run_root=p6_forward_run_root,
        p6_forward_config_path=p6_forward_config_path,
        p6_prospective_runtime_path=p6_runtime_path,
        memory_current_path=args.memory_current_path,
        memory_stat_path=args.memory_stat_path,
        memory_stop_bytes=args.memory_stop_bytes,
    )

    def stop(_signum: int, _frame: object) -> None:
        worker.stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    controller_enabled = os.environ.get("QUANTHUS_P6_CONTROLLER_ENABLED", "0").strip()
    if controller_enabled not in {"0", "1"}:
        raise RuntimeError("QUANTHUS_P6_CONTROLLER_ENABLED must be 0 or 1")
    mandate_path = Path(
        os.environ.get(
            "QUANTHUS_P6_ACTIVATION_MANDATE",
            "/app/config/forward-validation/activation/p6-owner-activation-mandate.v1.json",
        )
    )
    code_revision = runtime_code_revision() if controller_enabled == "1" else None
    while not worker.stopping:
        if controller_enabled == "1":
            try:
                if code_revision is None:
                    raise RuntimeError("P6 controller requires an exact runtime code revision")
                controller_state = controller_tick(
                    store,
                    repository_root=Path("/app"),
                    p6_forward_run_root=p6_forward_run_root,
                    runtime_path=p6_runtime_path,
                    mandate_path=mandate_path,
                    code_revision=code_revision,
                )
                print(
                    json.dumps(
                        {"event": "p6-controller", **controller_state},
                        sort_keys=True,
                    ),
                    file=sys.stderr,
                    flush=True,
                )
            except Exception as exc:
                diagnostic_job_id = record_controller_error(
                    store,
                    code_revision=code_revision,
                    error=exc,
                )
                print(
                    json.dumps(
                        {
                            "event": "p6-controller-error",
                            "error_type": type(exc).__name__,
                            "reason": str(exc)[:500],
                            "diagnostic_job_id": diagnostic_job_id,
                        },
                        sort_keys=True,
                    ),
                    file=sys.stderr,
                    flush=True,
                )
        worked = worker.run_once()
        if args.once:
            return 0
        if not worked:
            time.sleep(args.poll_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

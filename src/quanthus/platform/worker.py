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

from quanthus.platform.jobs import CANDIDATE_REPLAY_JOB
from quanthus.platform.persistence import PostgresPlatformStore

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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the lean Quanthus Replay Worker")
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
    ) -> None:
        self.store = store
        self.worker_id = worker_id
        self.run_root = run_root.resolve()
        self.lease_seconds = lease_seconds
        self.renew_seconds = renew_seconds
        self.terminate_seconds = terminate_seconds
        self.stopping = False

    def run_once(self) -> bool:
        job = self.store.claim_job("replay", self.worker_id, self.lease_seconds)
        if job is None:
            return False
        if job.kind != CANDIDATE_REPLAY_JOB:
            self.store.finish_claimed_job(
                job.job_id,
                job.claim_generation,
                state="failed",
                result_ref=None,
                error_reason="job_kind_not_admitted",
            )
            return True
        try:
            self._execute(job.job_id, job.claim_generation, job.input_ref)
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
        while process.poll() is None:
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
        progress_offset = self._publish_progress(stderr_path, progress_offset, job_id, generation)
        del progress_offset
        if lost_lease:
            return
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
    worker = ReplayWorker(
        PostgresPlatformStore(dsn),
        worker_id=worker_id,
        run_root=run_root,
        lease_seconds=args.lease_seconds,
        renew_seconds=args.renew_seconds,
        terminate_seconds=args.terminate_seconds,
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

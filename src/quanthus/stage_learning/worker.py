"""Node-local governed worker for RX6800 stage-learning jobs.

The worker owns no production database credentials.  It accepts a tiny file-backed
control contract intended to be invoked through a dedicated authenticated SSH/MCP
adapter.  Each job executes an exact commit in an isolated Git worktree while the
control checkout remains unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

JOB_SCHEMA = "quanthus.stage-worker-job.v1"
STATUS_SCHEMA = "quanthus.stage-worker-status.v1"
STAGE_HARNESS_JOB = "quanthus.stage-harness.v1"
STAGE_REFERENCE_PILOT_JOB = "quanthus.stage-reference-pilot.v1"
STAGE_QUALIFICATION_JOB = "quanthus.stage-qualification.v1"
STAGE_CAPACITY_CENSUS_JOB = "quanthus.stage-capacity-census.v1"
STAGE_SECOND_GROUP_PROFILE_JOB = "quanthus.stage-second-group-profile.v1"
STAGE_SECOND_GROUP_REPAIR_PROFILE_JOB = "quanthus.stage-second-group-repair-profile.v1"
STAGE_SECOND_GROUP_DEVELOPMENT_JOB = "quanthus.stage-second-group-development.v1"
STAGE_SECOND_GROUP_DEVELOPMENT_SUPPLEMENT_JOB = (
    "quanthus.stage-second-group-development-supplement.v1"
)
STAGE_THIRD_GROUP_PROFILE_JOB = "quanthus.stage-third-group-profile.v1"
STAGE_ORDER_PROPOSITION_DEVELOPMENT_JOB = "quanthus.stage-order-proposition-development.v1"
STAGE_ORDER_RULE_PREPARE_JOB = "quanthus.stage-order-rule-prepare.v1"
STAGE_QUALIFICATION_ID = "stock-stage-first-group-3-4-arbitration-shadow-v1"
QUALIFICATION_BUDGET_SCHEMA = "quanthus.stage-qualification-budget.v1"
ORDER_RULE_PREPARE_BUDGET_SCHEMA = "quanthus.stage-order-rule-prepare-budget.v1"
JOB_KIND = STAGE_HARNESS_JOB  # compatibility alias for the first admitted kind
GITHUB_REPOSITORY = "git@github.com:rv198-star/Quanthus.git"
MAIN_REF = "refs/remotes/stage-control/main"
_JOB_ID = re.compile(r"^stage_[0-9a-f]{32}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_JOB_SPECS: dict[str, dict[str, Any]] = {
    STAGE_HARNESS_JOB: {
        "config": "config/stage-learning/harness.v1.json",
        "module": "quanthus.stage_learning.harness",
        "artifact_glob": "stage-harness-*",
        "process_error": "stage_harness_process_failed",
    },
    STAGE_REFERENCE_PILOT_JOB: {
        "config": "config/stage-learning/reference-pilot.v1.json",
        "module": "quanthus.stage_learning.reference_pilot",
        "artifact_glob": "stage-reference-pilot-*",
        "process_error": "stage_reference_pilot_process_failed",
    },
    STAGE_QUALIFICATION_JOB: {
        "config": "config/stage-learning/qualification.v1.json",
        "module": "quanthus.stage_learning.qualification",
        "artifact_glob": "stage-qualification-*",
        "process_error": "stage_qualification_process_failed",
    },
    STAGE_CAPACITY_CENSUS_JOB: {
        "config": "config/stage-learning/sample-capacity-census.v1.json",
        "module": "quanthus.stage_learning.capacity_census",
        "artifact_glob": "stage-capacity-census-*",
        "process_error": "stage_capacity_census_process_failed",
        "wall_seconds": 1800,
    },
    STAGE_SECOND_GROUP_PROFILE_JOB: {
        "config": "config/stage-learning/second-group-profile.v1.json",
        "module": "quanthus.stage_learning.second_group_profile",
        "artifact_glob": "stage-second-group-profile-*",
        "process_error": "stage_second_group_profile_process_failed",
        "wall_seconds": 1800,
    },
    STAGE_SECOND_GROUP_REPAIR_PROFILE_JOB: {
        "config": "config/stage-learning/second-group-repair-profile.v1.json",
        "module": "quanthus.stage_learning.second_group_repair_profile",
        "artifact_glob": "stage-second-group-repair-profile-*",
        "process_error": "stage_second_group_repair_profile_process_failed",
        "wall_seconds": 1800,
    },
    STAGE_SECOND_GROUP_DEVELOPMENT_JOB: {
        "config": "config/stage-learning/second-group-development.v1.json",
        "module": "quanthus.stage_learning.second_group_development",
        "artifact_glob": "stage-second-group-development-*",
        "process_error": "stage_second_group_development_process_failed",
        "wall_seconds": 2700,
    },
    STAGE_SECOND_GROUP_DEVELOPMENT_SUPPLEMENT_JOB: {
        "config": "config/stage-learning/second-group-development-supplement.v1.json",
        "module": "quanthus.stage_learning.second_group_development_supplement",
        "artifact_glob": "stage-second-group-development-supplement-*",
        "process_error": "stage_second_group_development_supplement_process_failed",
        "wall_seconds": 2700,
    },
    STAGE_THIRD_GROUP_PROFILE_JOB: {
        "config": "config/stage-learning/third-group-profile.v1.json",
        "module": "quanthus.stage_learning.third_group_profile",
        "artifact_glob": "stage-third-group-profile-*",
        "process_error": "stage_third_group_profile_process_failed",
        "wall_seconds": 1800,
    },
    STAGE_ORDER_PROPOSITION_DEVELOPMENT_JOB: {
        "config": "config/stage-learning/order-proposition-development.v1.json",
        "module": "quanthus.stage_learning.order_proposition_development",
        "artifact_glob": "stage-order-proposition-development-*",
        "process_error": "stage_order_proposition_development_process_failed",
        "wall_seconds": 2700,
    },
    STAGE_ORDER_RULE_PREPARE_JOB: {
        "config": "config/stage-learning/order-rule-prepare.v1.json",
        "module": "quanthus.stage_learning.order_rule_prepare",
        "artifact_glob": "stage-order-rule-prepare-*",
        "process_error": "stage_order_rule_prepare_process_failed",
        "wall_seconds": 2700,
    },
}


class StageWorkerError(RuntimeError):
    """Raised when the node-local stage worker contract cannot be satisfied."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StageWorkerError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise StageWorkerError(f"{label} must be an object")
    return value


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    with temporary.open("x", encoding="utf-8") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _exclusive_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise StageWorkerError(
            "qualification access budget is already reserved or consumed"
        ) from exc


@dataclass(frozen=True)
class StageJob:
    job_id: str
    kind: str
    code_revision: str
    config: str
    submitted_at: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> StageJob:
        expected = {"schema_version", "job_id", "kind", "code_revision", "config", "submitted_at"}
        if set(value) != expected or value.get("schema_version") != JOB_SCHEMA:
            raise StageWorkerError("stage job fields or schema differ")
        job = cls(
            job_id=str(value["job_id"]),
            kind=str(value["kind"]),
            code_revision=str(value["code_revision"]),
            config=str(value["config"]),
            submitted_at=str(value["submitted_at"]),
        )
        if not _JOB_ID.fullmatch(job.job_id):
            raise StageWorkerError("stage job id is invalid")
        spec = _JOB_SPECS.get(job.kind)
        if spec is None:
            raise StageWorkerError("stage job kind is not admitted")
        if not _REVISION.fullmatch(job.code_revision):
            raise StageWorkerError("stage job requires an exact Git revision")
        if job.config != spec["config"]:
            raise StageWorkerError("stage job config is not admitted")
        try:
            parsed = datetime.fromisoformat(job.submitted_at)
        except ValueError as exc:
            raise StageWorkerError("stage job submitted_at is invalid") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise StageWorkerError("stage job submitted_at must be timezone-aware")
        return job

    def to_dict(self) -> dict[str, str]:
        return {
            "schema_version": JOB_SCHEMA,
            "job_id": self.job_id,
            "kind": self.kind,
            "code_revision": self.code_revision,
            "config": self.config,
            "submitted_at": self.submitted_at,
        }

    @property
    def input_digest(self) -> str:
        return _digest(self.to_dict())


@dataclass(frozen=True)
class StageWorkerPaths:
    training_root: Path
    control_repo: Path
    git_key: Path

    @property
    def control_root(self) -> Path:
        return self.training_root / "control" / "stage-worker"

    @property
    def queue(self) -> Path:
        return self.control_root / "queue"

    @property
    def claimed(self) -> Path:
        return self.control_root / "claimed"

    @property
    def status(self) -> Path:
        return self.control_root / "status"

    @property
    def cancel(self) -> Path:
        return self.control_root / "cancel"

    @property
    def worktrees(self) -> Path:
        return self.control_root / "worktrees"

    @property
    def runtime_identity(self) -> Path:
        return self.control_root / "worker-runtime.json"

    @property
    def qualification_budget(self) -> Path:
        return self.control_root / "qualification-budget.json"

    @property
    def order_rule_prepare_budget(self) -> Path:
        return self.control_root / "order-rule-prepare-budget.json"

    @property
    def logs(self) -> Path:
        return self.training_root / "logs" / "stage-worker"

    @property
    def runs(self) -> Path:
        return self.training_root / "runs" / "stage-worker"

    @property
    def python(self) -> Path:
        return self.control_repo / ".venv-stage-training" / "bin" / "python"

    def ensure(self) -> None:
        for path in (
            self.queue,
            self.claimed,
            self.status,
            self.cancel,
            self.worktrees,
            self.logs,
            self.runs,
        ):
            path.mkdir(parents=True, exist_ok=True)


def _order_rule_prepare_budget_value(job: StageJob) -> dict[str, Any]:
    return {
        "schema_version": ORDER_RULE_PREPARE_BUDGET_SCHEMA,
        "work_item": "P3-STAGE-007-B",
        "job": job.to_dict(),
        "input_digest": job.input_digest,
    }


def _existing_order_rule_prepare_submission(
    paths: StageWorkerPaths, *, code_revision: str,
) -> dict[str, Any] | None:
    marker_path = paths.order_rule_prepare_budget
    if not marker_path.exists():
        return None
    marker = _object(marker_path, "order-rule prepare budget")
    if (
        marker.get("schema_version") != ORDER_RULE_PREPARE_BUDGET_SCHEMA
        or marker.get("work_item") != "P3-STAGE-007-B"
        or not isinstance(marker.get("job"), Mapping)
    ):
        raise StageWorkerError("order-rule prepare budget marker differs")
    job = StageJob.from_dict(marker["job"])
    if (
        job.kind != STAGE_ORDER_RULE_PREPARE_JOB
        or marker.get("input_digest") != job.input_digest
        or job.code_revision != code_revision
    ):
        raise StageWorkerError("order-rule prepare is already bound to another immutable input")
    observed = read_status(paths, job.job_id)
    return {
        **job.to_dict(),
        "input_digest": job.input_digest,
        "state": observed["state"],
        "idempotent_reuse": True,
    }


def _qualification_budget_value(job: StageJob, *, state: str, reserved_at: str) -> dict[str, Any]:
    if state not in {"reserved", "consumed"}:
        raise StageWorkerError("qualification access budget state is invalid")
    return {
        "schema_version": QUALIFICATION_BUDGET_SCHEMA,
        "qualification_id": STAGE_QUALIFICATION_ID,
        "job_id": job.job_id,
        "kind": job.kind,
        "code_revision": job.code_revision,
        "config": job.config,
        "state": state,
        "reserved_at": reserved_at,
        "consumed_at": _utc_now() if state == "consumed" else None,
    }


def _qualification_budget(paths: StageWorkerPaths) -> dict[str, Any]:
    value = _object(paths.qualification_budget, "qualification access budget")
    expected = {
        "schema_version",
        "qualification_id",
        "job_id",
        "kind",
        "code_revision",
        "config",
        "state",
        "reserved_at",
        "consumed_at",
    }
    if (
        set(value) != expected
        or value.get("schema_version") != QUALIFICATION_BUDGET_SCHEMA
        or value.get("qualification_id") != STAGE_QUALIFICATION_ID
        or value.get("kind") != STAGE_QUALIFICATION_JOB
        or value.get("state") not in {"reserved", "consumed"}
    ):
        raise StageWorkerError("qualification access budget marker differs")
    return value


def _reserve_qualification_budget(paths: StageWorkerPaths, job: StageJob) -> None:
    if job.kind != STAGE_QUALIFICATION_JOB:
        return
    reserved_at = _utc_now()
    _exclusive_json(
        paths.qualification_budget,
        _qualification_budget_value(job, state="reserved", reserved_at=reserved_at),
    )


def _consume_qualification_budget(paths: StageWorkerPaths, job: StageJob) -> None:
    if job.kind != STAGE_QUALIFICATION_JOB:
        return
    value = _qualification_budget(paths)
    if any(
        value.get(key) != expected
        for key, expected in (
            ("job_id", job.job_id),
            ("code_revision", job.code_revision),
            ("config", job.config),
        )
    ):
        raise StageWorkerError("qualification access budget identity differs")
    if value.get("state") != "reserved" or value.get("consumed_at") is not None:
        raise StageWorkerError("qualification access budget is already consumed")
    _atomic_json(
        paths.qualification_budget,
        _qualification_budget_value(
            job,
            state="consumed",
            reserved_at=str(value["reserved_at"]),
        ),
    )


def _release_qualification_reservation(paths: StageWorkerPaths, job: StageJob) -> None:
    if job.kind != STAGE_QUALIFICATION_JOB or not paths.qualification_budget.exists():
        return
    value = _qualification_budget(paths)
    if value.get("job_id") != job.job_id:
        raise StageWorkerError("qualification access budget identity differs")
    if value.get("state") == "reserved" and value.get("consumed_at") is None:
        paths.qualification_budget.unlink()


class StageWorker:
    """Single-concurrency node-local stage worker."""

    def __init__(
        self,
        paths: StageWorkerPaths,
        *,
        poll_seconds: float = 2.0,
        wall_seconds: int = 600,
        terminate_seconds: float = 20.0,
    ) -> None:
        if poll_seconds <= 0 or wall_seconds < 1 or terminate_seconds <= 0:
            raise ValueError("worker timing values must be positive")
        self.paths = paths
        self.poll_seconds = poll_seconds
        self.wall_seconds = wall_seconds
        self.terminate_seconds = terminate_seconds
        self.stopping = False
        self.paths.ensure()

    def _git_env(self) -> dict[str, str]:
        env = dict(os.environ)
        known_hosts = self.paths.git_key.parent / "known_hosts"
        env["GIT_SSH_COMMAND"] = (
            f"ssh -F none -i {self.paths.git_key} -o IdentitiesOnly=yes "
            "-o BatchMode=yes -o StrictHostKeyChecking=yes "
            f"-o UserKnownHostsFile={known_hosts} -o GlobalKnownHostsFile=/dev/null"
        )
        return env

    def _git(self, *arguments: str, timeout: float = 120) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", "-C", str(self.paths.control_repo), *arguments],
                check=True,
                text=True,
                capture_output=True,
                timeout=timeout,
                env=self._git_env(),
            )
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip().splitlines()
            tail = detail[-1][:400] if detail else f"exit {exc.returncode}"
            action = " ".join(arguments[:2]) or "command"
            raise StageWorkerError(f"git {action} failed: {tail}") from exc

    def _revision_admitted_locally(self, revision: str) -> bool:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(self.paths.control_repo),
                "merge-base",
                "--is-ancestor",
                revision,
                MAIN_REF,
            ],
            text=True,
            capture_output=True,
            env=self._git_env(),
            timeout=30,
        )
        return result.returncode == 0

    def _authorize_revision(self, revision: str) -> None:
        # The installer already resolves and pins GitHub main before starting the
        # service.  Reuse that admitted local authority first so the bootstrap
        # job does not immediately race a second fetch against the installer.
        if self._revision_admitted_locally(revision):
            return
        last_error: StageWorkerError | None = None
        for attempt in range(3):
            try:
                self._git(
                    "fetch",
                    "--no-tags",
                    GITHUB_REPOSITORY,
                    f"+refs/heads/main:{MAIN_REF}",
                    timeout=180,
                )
                last_error = None
                break
            except StageWorkerError as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(2**attempt)
        if last_error is not None:
            raise last_error
        if not self._revision_admitted_locally(revision):
            raise StageWorkerError("requested revision is not admitted by GitHub main")

    def _status_path(self, job_id: str) -> Path:
        return self.paths.status / f"{job_id}.json"

    def _write_status(
        self,
        job: StageJob,
        *,
        state: str,
        stage: str,
        started_at: str | None = None,
        completed_at: str | None = None,
        result_ref: Mapping[str, Any] | None = None,
        error: str | None = None,
        exit_code: int | None = None,
    ) -> dict[str, Any]:
        value: dict[str, Any] = {
            "schema_version": STATUS_SCHEMA,
            "job_id": job.job_id,
            "kind": job.kind,
            "code_revision": job.code_revision,
            "input_digest": job.input_digest,
            "state": state,
            "stage": stage,
            "submitted_at": job.submitted_at,
            "started_at": started_at,
            "completed_at": completed_at,
            "result_ref": None if result_ref is None else dict(result_ref),
            "error": error,
            "exit_code": exit_code,
            "updated_at": _utc_now(),
        }
        _atomic_json(self._status_path(job.job_id), value)
        return value

    def _claim_next(self) -> tuple[StageJob, Path] | None:
        for queued in sorted(self.paths.queue.glob("stage_*.json")):
            claimed = self.paths.claimed / queued.name
            try:
                os.replace(queued, claimed)
            except FileNotFoundError:
                continue
            try:
                job = StageJob.from_dict(_object(claimed, "claimed stage job"))
            except Exception:
                claimed.unlink(missing_ok=True)
                raise
            if self._status_path(job.job_id).exists():
                claimed.unlink(missing_ok=True)
                continue
            return job, claimed
        return None

    def _terminate(self, process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=self.terminate_seconds)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return
            process.wait(timeout=self.terminate_seconds)

    def _prepare_worktree(self, job: StageJob) -> Path:
        worktree = self.paths.worktrees / job.job_id
        if worktree.exists():
            shutil.rmtree(worktree)
        self._authorize_revision(job.code_revision)
        self._git("worktree", "prune")
        self._git("worktree", "add", "--detach", str(worktree), job.code_revision, timeout=120)
        observed = subprocess.check_output(
            ["git", "-C", str(worktree), "rev-parse", "HEAD"],
            text=True,
            timeout=10,
        ).strip()
        if observed != job.code_revision:
            raise StageWorkerError("execution worktree revision differs")
        return worktree

    def _remove_worktree(self, worktree: Path) -> None:
        if not worktree.exists():
            return
        try:
            self._git("worktree", "remove", "--force", str(worktree), timeout=60)
        except (subprocess.SubprocessError, OSError):
            shutil.rmtree(worktree, ignore_errors=True)
            with suppress(subprocess.SubprocessError, OSError):
                self._git("worktree", "prune", timeout=30)

    def _verify_result(self, job: StageJob, job_run_root: Path, worktree: Path) -> dict[str, Any]:
        spec = _JOB_SPECS[job.kind]
        artifacts = sorted(
            path for path in job_run_root.glob(spec["artifact_glob"]) if path.is_dir()
        )
        if len(artifacts) != 1:
            raise StageWorkerError("stage job did not publish exactly one governed artifact")
        env = dict(os.environ)
        env["PYTHONPATH"] = str(worktree / "src")
        completed = subprocess.run(
            [
                str(self.paths.python),
                "-m",
                spec["module"],
                "verify",
                "--artifact",
                str(artifacts[0]),
                "--code-revision",
                job.code_revision,
            ],
            check=True,
            text=True,
            capture_output=True,
            timeout=60,
            cwd=worktree,
            env=env,
        )
        try:
            verified = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise StageWorkerError("stage job verification output is invalid") from exc
        if not isinstance(verified, dict) or verified.get("verified") is not True:
            raise StageWorkerError("stage job artifact verification did not pass")
        result = {
            "artifact_root": str(artifacts[0]),
            "run_id": verified.get("run_id"),
            "manifest_sha256": verified.get("manifest_sha256"),
            "file_count": verified.get("file_count"),
        }
        if job.kind == STAGE_CAPACITY_CENSUS_JOB:
            result["capacity_census"] = {
                "eligible_population_count": verified.get("eligible_population_count"),
                "runtime_population_size": verified.get("runtime_population_size"),
                "capacity_curve": verified.get("capacity_curve"),
                "first_population_meeting_all_target_caps": verified.get(
                    "first_population_meeting_all_target_caps"
                ),
                "requires_larger_census": verified.get("requires_larger_census"),
            }
        if job.kind == STAGE_SECOND_GROUP_PROFILE_JOB:
            result["second_group_profile"] = {
                "accepted_stage4_episode_count": verified.get("accepted_stage4_episode_count"),
                "path_observation_count": verified.get("path_observation_count"),
                "path_stop_counts": verified.get("path_stop_counts"),
                "scale_profiles": verified.get("scale_profiles"),
            }
        if job.kind == STAGE_SECOND_GROUP_REPAIR_PROFILE_JOB:
            result["second_group_repair_profile"] = {
                "accepted_stage4_episode_count": verified.get("accepted_stage4_episode_count"),
                "repair_observation_count": verified.get("repair_observation_count"),
                "repair_cycle_count": verified.get("repair_cycle_count"),
                "support_observation_count": verified.get("support_observation_count"),
                "path_stop_counts": verified.get("path_stop_counts"),
                "scale_profiles": verified.get("scale_profiles"),
                "preserved_stage_codes": verified.get("preserved_stage_codes"),
                "stage_5_6_merged": verified.get("stage_5_6_merged"),
                "stage_10_11_merged": verified.get("stage_10_11_merged"),
            }
        if job.kind == STAGE_SECOND_GROUP_DEVELOPMENT_JOB:
            result["second_group_development"] = {
                "disposition": verified.get("disposition"),
                "selected_parameters": verified.get("selected_parameters"),
                "reference_summary": verified.get("reference_summary"),
                "candidate_artifact_digest": verified.get("candidate_artifact_digest"),
            }
        if job.kind == STAGE_SECOND_GROUP_DEVELOPMENT_SUPPLEMENT_JOB:
            result["second_group_development_supplement"] = {
                "disposition": verified.get("disposition"),
                "milestone_complete": verified.get("milestone_complete"),
                "selected_parameters": verified.get("selected_parameters"),
                "reference_summary": verified.get("reference_summary"),
                "stage_7_gate_reason_counts": verified.get("stage_7_gate_reason_counts"),
                "candidate_artifact_digest": verified.get("candidate_artifact_digest"),
            }
        if job.kind == STAGE_THIRD_GROUP_PROFILE_JOB:
            result["third_group_profile"] = {
                "profile_observation_count": verified.get("profile_observation_count"),
                "reset_counts": verified.get("reset_counts"),
                "metric_profiles": verified.get("metric_profiles"),
                "joint_diagnostic_counts": verified.get("joint_diagnostic_counts"),
                "diagnostic_slice_counts": verified.get("diagnostic_slice_counts"),
            }
        if job.kind == STAGE_ORDER_PROPOSITION_DEVELOPMENT_JOB:
            result["order_proposition_development"] = {
                "disposition": verified.get("disposition"),
                "selected_parameters": verified.get("selected_parameters"),
                "selected_pair_overlap_count": verified.get("selected_pair_overlap_count"),
                "reference_summary": verified.get("reference_summary"),
                "observation_count": verified.get("observation_count"),
                "candidate_artifact_digest": verified.get("candidate_artifact_digest"),
            }
        if job.kind == STAGE_ORDER_RULE_PREPARE_JOB:
            result["order_rule_prepare"] = {
                "source_isolation_verified": verified.get("source_isolation_verified"),
                "old_cohort_size": verified.get("old_cohort_size"),
                "excluded_economic_identity_count": verified.get(
                    "excluded_economic_identity_count"
                ),
                "candidate_population_count": verified.get("candidate_population_count"),
                "pool_size": verified.get("pool_size"),
                "selected_case_count": verified.get("selected_case_count"),
                "selection_digest": verified.get("selection_digest"),
                "selection_shortfalls": verified.get("selection_shortfalls"),
                "evidence_digest": verified.get("evidence_digest"),
                "evidence_source_row_count": verified.get("evidence_source_row_count"),
                "review_ready": verified.get("review_ready"),
            }
        return result

    def execute(self, job: StageJob) -> dict[str, Any]:
        started_at = _utc_now()
        self._write_status(job, state="running", stage="preparing", started_at=started_at)
        worktree: Path | None = None
        log_path = self.paths.logs / f"{job.job_id}.log"
        run_root = self.paths.runs / job.job_id
        run_root.mkdir(parents=True, exist_ok=False)
        cancel_path = self.paths.cancel / job.job_id
        qualification_access_started = False
        try:
            worktree = self._prepare_worktree(job)
            if cancel_path.exists() or self.stopping:
                return self._write_status(
                    job,
                    state="canceled",
                    stage="terminal",
                    started_at=started_at,
                    completed_at=_utc_now(),
                    error="cancel_requested_before_execution",
                )
            if job.kind == STAGE_QUALIFICATION_JOB:
                _consume_qualification_budget(self.paths, job)
                qualification_access_started = True
            self._write_status(job, state="running", stage="executing", started_at=started_at)
            env = dict(os.environ)
            env.update(
                {
                    "PYTHONPATH": str(worktree / "src"),
                    "OMP_NUM_THREADS": "8",
                    "OPENBLAS_NUM_THREADS": "8",
                    "MKL_NUM_THREADS": "8",
                    "NUMEXPR_NUM_THREADS": "8",
                }
            )
            spec = _JOB_SPECS[job.kind]
            command = [
                str(self.paths.python),
                "-m",
                spec["module"],
                "run",
                "--config",
                str(worktree / job.config),
                "--training-root",
                str(self.paths.training_root),
                "--output-root",
                str(run_root),
                "--code-revision",
                job.code_revision,
            ]
            with log_path.open("xb") as log:
                process = subprocess.Popen(
                    command,
                    cwd=worktree,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                job_wall_seconds = int(spec.get("wall_seconds", self.wall_seconds))
                deadline = time.monotonic() + job_wall_seconds
                canceled = False
                timed_out = False
                while process.poll() is None:
                    if cancel_path.exists() or self.stopping:
                        canceled = True
                        self._terminate(process)
                        break
                    if time.monotonic() >= deadline:
                        timed_out = True
                        self._terminate(process)
                        break
                    time.sleep(min(self.poll_seconds, 1.0))
                exit_code = process.wait()
            if canceled:
                return self._write_status(
                    job,
                    state="canceled",
                    stage="terminal",
                    started_at=started_at,
                    completed_at=_utc_now(),
                    error="cancel_requested",
                    exit_code=exit_code,
                )
            if timed_out:
                return self._write_status(
                    job,
                    state="failed",
                    stage="terminal",
                    started_at=started_at,
                    completed_at=_utc_now(),
                    error="stage_worker_wall_budget_exceeded",
                    exit_code=exit_code,
                )
            if exit_code != 0:
                return self._write_status(
                    job,
                    state="failed",
                    stage="terminal",
                    started_at=started_at,
                    completed_at=_utc_now(),
                    error=spec["process_error"],
                    exit_code=exit_code,
                )
            self._write_status(job, state="running", stage="verifying", started_at=started_at)
            result_ref = self._verify_result(job, run_root, worktree)
            result_ref["log_path"] = str(log_path)
            return self._write_status(
                job,
                state="succeeded",
                stage="terminal",
                started_at=started_at,
                completed_at=_utc_now(),
                result_ref=result_ref,
                exit_code=0,
            )
        except Exception as exc:
            return self._write_status(
                job,
                state="failed",
                stage="terminal",
                started_at=started_at,
                completed_at=_utc_now(),
                error=f"{type(exc).__name__}:{str(exc)[:500]}",
            )
        finally:
            cancel_path.unlink(missing_ok=True)
            if job.kind == STAGE_QUALIFICATION_JOB and not qualification_access_started:
                with suppress(StageWorkerError):
                    _release_qualification_reservation(self.paths, job)
            if worktree is not None:
                self._remove_worktree(worktree)

    def run_once(self) -> dict[str, Any] | None:
        claimed = self._claim_next()
        if claimed is None:
            return None
        job, claimed_path = claimed
        try:
            return self.execute(job)
        finally:
            claimed_path.unlink(missing_ok=True)

    def serve(self) -> None:
        revision = os.environ.get("QUANTHUS_STAGE_WORKER_CODE_REVISION", "")
        if not _REVISION.fullmatch(revision):
            raise StageWorkerError("worker runtime revision is missing or invalid")
        _atomic_json(
            self.paths.runtime_identity,
            {
                "schema_version": "quanthus.stage-worker-runtime.v1",
                "code_revision": revision,
                "pid": os.getpid(),
                "started_at": _utc_now(),
                "job_kinds": sorted(_JOB_SPECS),
                "concurrency": 1,
            },
        )
        while not self.stopping:
            try:
                result = self.run_once()
            except Exception as exc:
                print(f"stage-worker loop error: {type(exc).__name__}: {exc}", file=sys.stderr)
                result = None
            if result is None:
                time.sleep(self.poll_seconds)


def _paths(args: argparse.Namespace) -> StageWorkerPaths:
    return StageWorkerPaths(
        training_root=Path(args.training_root).expanduser().resolve(),
        control_repo=Path(args.control_repo).expanduser().resolve(),
        git_key=Path(args.git_key).expanduser().resolve(),
    )


def submit_job(
    paths: StageWorkerPaths,
    *,
    code_revision: str,
    kind: str = STAGE_HARNESS_JOB,
) -> dict[str, Any]:
    paths.ensure()
    if not _REVISION.fullmatch(code_revision):
        raise StageWorkerError("submit requires exact 40-character Git revision")
    spec = _JOB_SPECS.get(kind)
    if spec is None:
        raise StageWorkerError("submit kind is not admitted")
    if kind == STAGE_ORDER_RULE_PREPARE_JOB:
        existing = _existing_order_rule_prepare_submission(paths, code_revision=code_revision)
        if existing is not None:
            return existing
    job = StageJob(
        job_id="stage_" + uuid.uuid4().hex,
        kind=kind,
        code_revision=code_revision,
        config=spec["config"],
        submitted_at=_utc_now(),
    )
    path = paths.queue / f"{job.job_id}.json"
    qualification_reserved = job.kind == STAGE_QUALIFICATION_JOB
    prepare_reserved = job.kind == STAGE_ORDER_RULE_PREPARE_JOB
    if qualification_reserved:
        _reserve_qualification_budget(paths, job)
    if prepare_reserved:
        _exclusive_json(paths.order_rule_prepare_budget, _order_rule_prepare_budget_value(job))
    try:
        with path.open("xb") as stream:
            payload = json.dumps(job.to_dict(), sort_keys=True, indent=2).encode("utf-8") + b"\n"
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        if qualification_reserved:
            with suppress(StageWorkerError):
                _release_qualification_reservation(paths, job)
        if prepare_reserved:
            paths.order_rule_prepare_budget.unlink(missing_ok=True)
        raise StageWorkerError("generated duplicate stage job id") from exc
    except Exception:
        if qualification_reserved:
            with suppress(StageWorkerError):
                _release_qualification_reservation(paths, job)
        if prepare_reserved:
            paths.order_rule_prepare_budget.unlink(missing_ok=True)
        raise
    return {**job.to_dict(), "input_digest": job.input_digest, "state": "queued"}


def read_status(paths: StageWorkerPaths, job_id: str) -> dict[str, Any]:
    if not _JOB_ID.fullmatch(job_id):
        raise StageWorkerError("status requires a valid stage job id")
    status = paths.status / f"{job_id}.json"
    if status.is_file():
        return _object(status, "stage job status")
    for root, state in ((paths.queue, "queued"), (paths.claimed, "claimed")):
        candidate = root / f"{job_id}.json"
        if candidate.is_file():
            job = StageJob.from_dict(_object(candidate, "stage job"))
            return {
                "schema_version": STATUS_SCHEMA,
                "job_id": job.job_id,
                "kind": job.kind,
                "code_revision": job.code_revision,
                "input_digest": job.input_digest,
                "state": state,
                "stage": state,
                "submitted_at": job.submitted_at,
            }
    raise StageWorkerError("stage job is not known on this node")


def cancel_job(paths: StageWorkerPaths, job_id: str) -> dict[str, Any]:
    observed = read_status(paths, job_id)
    if observed.get("state") in {"succeeded", "failed", "canceled"}:
        raise StageWorkerError("terminal stage job cannot be canceled")
    paths.cancel.mkdir(parents=True, exist_ok=True)
    marker = paths.cancel / job_id
    marker.touch(exist_ok=True)
    if observed.get("state") == "queued":
        queued = paths.queue / f"{job_id}.json"
        if queued.exists():
            job = StageJob.from_dict(_object(queued, "queued stage job"))
            _release_qualification_reservation(paths, job)
            queued.unlink()
            worker = StageWorker(paths)
            worker._write_status(
                job,
                state="canceled",
                stage="terminal",
                completed_at=_utc_now(),
                error="cancel_requested_before_claim",
            )
            marker.unlink(missing_ok=True)
            return read_status(paths, job_id)
    return {**observed, "cancel_requested": True}


def doctor(paths: StageWorkerPaths) -> dict[str, Any]:
    paths.ensure()
    problems: list[str] = []
    if not (paths.control_repo / ".git").exists():
        problems.append("control_repo_not_git_checkout")
    if not paths.python.is_file() or not os.access(paths.python, os.X_OK):
        problems.append("stage_training_python_missing")
    if not paths.git_key.is_file():
        problems.append("github_deploy_key_missing")
    source = paths.training_root / "sources" / "dwd-v2.1-full-20260908"
    if not source.is_dir():
        problems.append("training_source_missing")
    return {
        "schema_version": "quanthus.stage-worker-doctor.v1",
        "ready": not problems,
        "problems": problems,
        "control_repo": str(paths.control_repo),
        "training_root": str(paths.training_root),
        "python": str(paths.python),
        "job_kinds": tuple(sorted(_JOB_SPECS)),
        "concurrency": 1,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--training-root",
        default=os.environ.get("QUANTHUS_STAGE_TRAINING_ROOT", "~/quanthus-data/training"),
    )
    parser.add_argument(
        "--control-repo",
        default=os.environ.get("QUANTHUS_STAGE_CONTROL_REPO", "~/quanthus-code/Quanthus"),
    )
    parser.add_argument(
        "--git-key",
        default=os.environ.get("QUANTHUS_STAGE_GIT_KEY", "~/.ssh/quanthus-github-ro"),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--once", action="store_true")
    serve.add_argument("--poll-seconds", type=float, default=2.0)
    serve.add_argument("--wall-seconds", type=int, default=600)
    submit = sub.add_parser("submit")
    submit.add_argument("--code-revision", required=True)
    submit.add_argument("--kind", choices=tuple(sorted(_JOB_SPECS)), default=STAGE_HARNESS_JOB)
    status = sub.add_parser("status")
    status.add_argument("--job-id", required=True)
    cancel = sub.add_parser("cancel")
    cancel.add_argument("--job-id", required=True)
    sub.add_parser("doctor")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    paths = _paths(args)
    try:
        if args.command == "serve":
            worker = StageWorker(
                paths,
                poll_seconds=args.poll_seconds,
                wall_seconds=args.wall_seconds,
            )
            if args.once:
                result = worker.run_once()
                payload = {"processed": result is not None, "result": result}
                print(json.dumps(payload, sort_keys=True))
            else:
                def stop(_signum: int, _frame: object) -> None:
                    worker.stopping = True

                signal.signal(signal.SIGTERM, stop)
                signal.signal(signal.SIGINT, stop)
                worker.serve()
            return 0
        if args.command == "submit":
            result = submit_job(paths, code_revision=args.code_revision, kind=args.kind)
        elif args.command == "status":
            result = read_status(paths, args.job_id)
        elif args.command == "cancel":
            result = cancel_job(paths, args.job_id)
        else:
            result = doctor(paths)
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0 if result.get("ready", True) is not False else 2
    except StageWorkerError as exc:
        print(f"stage-worker: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

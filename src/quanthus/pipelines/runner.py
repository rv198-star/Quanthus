"""Fail-closed OCI orchestration for versioned data-pipeline stages."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from datetime import time as wall_time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from quanthus.pipelines.contracts import OdsReleaseError, verify_ods_release
from quanthus.pipelines.daily_warehouse import market_session_is_open


class PipelineConfigError(RuntimeError):
    """Raised when a versioned pipeline config is unsafe or incomplete."""


class PipelineRunError(RuntimeError):
    """Raised when a pipeline cannot complete all configured stages."""


_PHASE_ORDER = {"acquire": 0, "verify": 1, "build": 2, "gate": 3, "publish": 4}


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PipelineConfigError(f"{key} must be a non-empty string")
    return value


def _integer(
    payload: Mapping[str, Any], key: str, *, default: int, minimum: int, maximum: int
) -> int:
    value = payload.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise PipelineConfigError(f"{key} must be an integer from {minimum} to {maximum}")
    return value


@dataclass(frozen=True)
class PipelineVariable:
    name: str
    environment: str
    default: str | None

    def resolve(self) -> str:
        value = os.environ.get(self.environment, self.default)
        if value is None or not value.strip():
            raise PipelineConfigError(
                f"pipeline variable {self.name} requires environment {self.environment}"
            )
        return value


@dataclass(frozen=True)
class PipelineStage:
    stage_id: str
    phase: str
    kind: str
    argv: tuple[str, ...]
    release_dir: str | None
    calendar_path: str | None
    retries: int
    retry_delay_seconds: int
    timeout_seconds: int


@dataclass(frozen=True)
class PipelineConfig:
    source_path: Path
    source_sha256: str
    pipeline_id: str
    timezone_name: str
    close_time: wall_time
    completion_sla_minutes: int
    run_root: str
    lock_path: str
    variables: tuple[PipelineVariable, ...]
    stages: tuple[PipelineStage, ...]


@dataclass(frozen=True)
class PipelineRunResult:
    pipeline_id: str
    run_id: str
    market_date: date
    status: str
    run_dir: Path
    started_at: datetime
    finished_at: datetime
    deadline_at: datetime
    sla_met: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "pipeline_id": self.pipeline_id,
            "run_id": self.run_id,
            "market_date": self.market_date.isoformat(),
            "status": self.status,
            "run_dir": str(self.run_dir),
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "deadline_at": self.deadline_at.isoformat(),
            "sla_met": self.sla_met,
        }


def _parse_time(value: str) -> wall_time:
    try:
        parsed = wall_time.fromisoformat(value)
    except ValueError as exc:
        raise PipelineConfigError("close_time must use HH:MM or HH:MM:SS") from exc
    if parsed.tzinfo is not None:
        raise PipelineConfigError("close_time must be a local wall time without timezone")
    return parsed


def load_pipeline_config(path: Path) -> PipelineConfig:
    path = path.resolve()
    try:
        encoded = path.read_bytes()
        payload = json.loads(encoded.decode("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineConfigError(f"cannot load pipeline config: {path}") from exc
    if not isinstance(payload, Mapping):
        raise PipelineConfigError("pipeline config must be an object")
    if payload.get("schema_version") != 1:
        raise PipelineConfigError("unsupported pipeline schema_version")
    timezone_name = _required_string(payload, "timezone")
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise PipelineConfigError(f"unknown timezone: {timezone_name}") from exc

    variable_values = payload.get("variables", {})
    if not isinstance(variable_values, Mapping):
        raise PipelineConfigError("variables must be an object")
    variables: list[PipelineVariable] = []
    for name, value in variable_values.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(value, Mapping):
            raise PipelineConfigError("each pipeline variable must be a named object")
        environment = _required_string(value, "environment")
        default_value = value.get("default")
        if default_value is not None and not isinstance(default_value, str):
            raise PipelineConfigError(f"variable {name} default must be a string")
        variables.append(PipelineVariable(name, environment, default_value))

    stage_values = payload.get("stages")
    if not isinstance(stage_values, list) or not stage_values:
        raise PipelineConfigError("stages must be a non-empty list")
    stages: list[PipelineStage] = []
    previous_phase = -1
    for value in stage_values:
        if not isinstance(value, Mapping):
            raise PipelineConfigError("each pipeline stage must be an object")
        stage_id = _required_string(value, "id")
        phase = _required_string(value, "phase")
        if phase not in _PHASE_ORDER:
            raise PipelineConfigError(f"stage {stage_id} has unsupported phase {phase}")
        if _PHASE_ORDER[phase] < previous_phase:
            raise PipelineConfigError("pipeline phases must be ordered acquire through publish")
        previous_phase = _PHASE_ORDER[phase]
        kind = _required_string(value, "kind")
        if kind not in {"command", "verify_ods_release", "market_session_check"}:
            raise PipelineConfigError(f"stage {stage_id} has unsupported kind {kind}")
        argv_value = value.get("argv", [])
        if not isinstance(argv_value, list) or any(
            not isinstance(argument, str) or not argument for argument in argv_value
        ):
            raise PipelineConfigError(f"stage {stage_id} argv must be a list of strings")
        release_dir = value.get("release_dir")
        if release_dir is not None and not isinstance(release_dir, str):
            raise PipelineConfigError(f"stage {stage_id} release_dir must be a string")
        if kind == "command" and not argv_value:
            raise PipelineConfigError(f"command stage {stage_id} requires argv")
        if kind == "verify_ods_release" and not release_dir:
            raise PipelineConfigError(f"verification stage {stage_id} requires release_dir")
        calendar_path = value.get("calendar_path")
        if calendar_path is not None and not isinstance(calendar_path, str):
            raise PipelineConfigError(f"stage {stage_id} calendar_path must be a string")
        if kind == "market_session_check" and not calendar_path:
            raise PipelineConfigError(f"market-session stage {stage_id} requires calendar_path")
        stages.append(
            PipelineStage(
                stage_id=stage_id,
                phase=phase,
                kind=kind,
                argv=tuple(argv_value),
                release_dir=release_dir,
                calendar_path=calendar_path,
                retries=_integer(value, "retries", default=0, minimum=0, maximum=100),
                retry_delay_seconds=_integer(
                    value,
                    "retry_delay_seconds",
                    default=0,
                    minimum=0,
                    maximum=3600,
                ),
                timeout_seconds=_integer(
                    value,
                    "timeout_seconds",
                    default=3600,
                    minimum=1,
                    maximum=86400,
                ),
            )
        )
    stage_ids = [stage.stage_id for stage in stages]
    if len(stage_ids) != len(set(stage_ids)):
        raise PipelineConfigError("pipeline stage ids must be unique")
    publish_indexes = [index for index, stage in enumerate(stages) if stage.phase == "publish"]
    if publish_indexes and publish_indexes != list(range(publish_indexes[0], len(stages))):
        raise PipelineConfigError("publish stages must be the final contiguous stages")
    if publish_indexes and not any(stage.phase == "gate" for stage in stages):
        raise PipelineConfigError("a publishing pipeline requires at least one gate stage")

    return PipelineConfig(
        source_path=path,
        source_sha256=f"sha256:{hashlib.sha256(encoded).hexdigest()}",
        pipeline_id=_required_string(payload, "pipeline_id"),
        timezone_name=timezone_name,
        close_time=_parse_time(_required_string(payload, "close_time")),
        completion_sla_minutes=_integer(
            payload,
            "completion_sla_minutes",
            default=300,
            minimum=1,
            maximum=1440,
        ),
        run_root=_required_string(payload, "run_root"),
        lock_path=_required_string(payload, "lock_path"),
        variables=tuple(variables),
        stages=tuple(stages),
    )


def _render(value: str, context: Mapping[str, str]) -> str:
    try:
        return value.format_map(context)
    except KeyError as exc:
        raise PipelineConfigError(f"unknown pipeline template variable: {exc.args[0]}") from exc
    except ValueError as exc:
        raise PipelineConfigError(f"invalid pipeline template: {value}") from exc


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=True, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def _ledger(
    *,
    config: PipelineConfig,
    run_id: str,
    market_date: date,
    started_at: datetime,
    deadline_at: datetime,
    status: str,
    stages: list[dict[str, Any]],
    finished_at: datetime | None = None,
    failure: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "pipeline_id": config.pipeline_id,
        "pipeline_config": str(config.source_path),
        "pipeline_config_sha256": config.source_sha256,
        "run_id": run_id,
        "market_date": market_date.isoformat(),
        "started_at": started_at.isoformat(),
        "deadline_at": deadline_at.isoformat(),
        "status": status,
        "stages": stages,
    }
    code_revision = os.environ.get("QUANTHUS_CODE_REVISION")
    if code_revision:
        payload["code_revision"] = code_revision
    if finished_at is not None:
        payload["finished_at"] = finished_at.isoformat()
        payload["sla_met"] = finished_at <= deadline_at
    if failure is not None:
        payload["failure"] = failure
    return payload


def _run_command(stage: PipelineStage, argv: list[str], log_path: Path) -> None:
    with log_path.open("a", encoding="utf-8") as log:
        completed = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=stage.timeout_seconds,
            check=False,
        )
    if completed.returncode:
        raise PipelineRunError(
            f"stage {stage.stage_id} command failed with exit code {completed.returncode}"
        )


def _run_stage(
    stage: PipelineStage,
    *,
    context: Mapping[str, str],
    log_path: Path,
) -> bool:
    if stage.kind == "command":
        _run_command(stage, [_render(argument, context) for argument in stage.argv], log_path)
        return False
    if stage.kind == "verify_ods_release":
        if stage.release_dir is None:
            raise AssertionError("verified pipeline config lost release_dir")
        result = verify_ods_release(Path(_render(stage.release_dir, context)))
        if result.release.market_date.isoformat() != context["market_date"]:
            raise OdsReleaseError(
                "ODS release market_date does not match the requested pipeline market date"
            )
        with log_path.open("a", encoding="utf-8") as log:
            json.dump(result.to_dict(), log, ensure_ascii=True, sort_keys=True)
            log.write("\n")
        return False
    if stage.calendar_path is None:
        raise AssertionError("verified pipeline config lost calendar_path")
    market_date = date.fromisoformat(context["market_date"])
    is_open = market_session_is_open(
        calendar=Path(_render(stage.calendar_path, context)),
        market_date=market_date,
    )
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"market_date={market_date.isoformat()} open={str(is_open).lower()}\n")
    return not is_open


def run_pipeline(config: PipelineConfig, *, market_date: date) -> PipelineRunResult:
    """Run one market-date pipeline under an exclusive lock and durable JSON ledger."""

    local_zone = ZoneInfo(config.timezone_name)
    started_at = datetime.now(timezone.utc)
    local_close = datetime.combine(market_date, config.close_time, tzinfo=local_zone)
    deadline_at = (local_close + timedelta(minutes=config.completion_sla_minutes)).astimezone(
        timezone.utc
    )
    run_id = f"{market_date.strftime('%Y%m%d')}-{started_at.strftime('%Y%m%dT%H%M%S%fZ')}"
    resolved_variables = {variable.name: variable.resolve() for variable in config.variables}
    initial_context = {
        **resolved_variables,
        "market_date": market_date.isoformat(),
        "market_date_compact": market_date.strftime("%Y%m%d"),
        "run_id": run_id,
    }
    run_root = Path(_render(config.run_root, initial_context)).resolve()
    run_dir = run_root / config.pipeline_id / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    context = {**initial_context, "run_dir": str(run_dir)}
    lock_path = Path(_render(config.lock_path, context)).resolve()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    stage_records: list[dict[str, Any]] = []
    ledger_path = run_dir / "run.json"
    _atomic_json(
        ledger_path,
        _ledger(
            config=config,
            run_id=run_id,
            market_date=market_date,
            started_at=started_at,
            deadline_at=deadline_at,
            status="running",
            stages=stage_records,
        ),
    )

    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            finished = datetime.now(timezone.utc)
            message = f"pipeline lock is already held: {lock_path}"
            _atomic_json(
                ledger_path,
                _ledger(
                    config=config,
                    run_id=run_id,
                    market_date=market_date,
                    started_at=started_at,
                    deadline_at=deadline_at,
                    finished_at=finished,
                    status="blocked",
                    stages=stage_records,
                    failure=message,
                ),
            )
            raise PipelineRunError(message) from exc

        closed_session = False
        try:
            for stage_index, stage in enumerate(config.stages):
                record: dict[str, Any] = {
                    "id": stage.stage_id,
                    "phase": stage.phase,
                    "kind": stage.kind,
                    "status": "running",
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "attempts": 0,
                    "log": f"{stage.stage_id}.log",
                }
                stage_records.append(record)
                _atomic_json(
                    ledger_path,
                    _ledger(
                        config=config,
                        run_id=run_id,
                        market_date=market_date,
                        started_at=started_at,
                        deadline_at=deadline_at,
                        status="running",
                        stages=stage_records,
                    ),
                )
                log_path = run_dir / f"{stage.stage_id}.log"
                for attempt in range(1, stage.retries + 2):
                    record["attempts"] = attempt
                    with log_path.open("a", encoding="utf-8") as log:
                        log.write(
                            f"[{datetime.now(timezone.utc).isoformat()}] attempt={attempt}\n"
                        )
                    try:
                        closed_session = _run_stage(
                            stage,
                            context=context,
                            log_path=log_path,
                        )
                    except (OdsReleaseError, PipelineRunError, subprocess.TimeoutExpired) as exc:
                        record["last_error"] = str(exc)
                        if attempt > stage.retries:
                            raise PipelineRunError(
                                f"stage {stage.stage_id} failed after {attempt} attempt(s): {exc}"
                            ) from exc
                        time.sleep(stage.retry_delay_seconds)
                    else:
                        record.pop("last_error", None)
                        record["status"] = "succeeded"
                        record["finished_at"] = datetime.now(timezone.utc).isoformat()
                        if closed_session:
                            record["outcome"] = "closed_market_session"
                        break
                _atomic_json(
                    ledger_path,
                    _ledger(
                        config=config,
                        run_id=run_id,
                        market_date=market_date,
                        started_at=started_at,
                        deadline_at=deadline_at,
                        status="running",
                        stages=stage_records,
                    ),
                )
                if closed_session:
                    skipped_at = datetime.now(timezone.utc).isoformat()
                    for remaining in config.stages[stage_index + 1 :]:
                        stage_records.append(
                            {
                                "id": remaining.stage_id,
                                "phase": remaining.phase,
                                "kind": remaining.kind,
                                "status": "skipped",
                                "reason": "closed_market_session",
                                "finished_at": skipped_at,
                                "attempts": 0,
                            }
                        )
                    break
        except BaseException as exc:
            finished = datetime.now(timezone.utc)
            if stage_records and stage_records[-1]["status"] == "running":
                stage_records[-1]["status"] = "failed"
                stage_records[-1]["finished_at"] = finished.isoformat()
            _atomic_json(
                ledger_path,
                _ledger(
                    config=config,
                    run_id=run_id,
                    market_date=market_date,
                    started_at=started_at,
                    deadline_at=deadline_at,
                    finished_at=finished,
                    status="failed",
                    stages=stage_records,
                    failure=str(exc),
                ),
            )
            if isinstance(exc, PipelineRunError):
                raise
            raise PipelineRunError(f"pipeline failed: {exc}") from exc

    finished_at = datetime.now(timezone.utc)
    sla_met = finished_at <= deadline_at
    if closed_session:
        status = "skipped_closed_session"
    else:
        status = "succeeded" if sla_met else "succeeded_late"
    final_payload = _ledger(
        config=config,
        run_id=run_id,
        market_date=market_date,
        started_at=started_at,
        deadline_at=deadline_at,
        finished_at=finished_at,
        status=status,
        stages=stage_records,
    )
    _atomic_json(ledger_path, final_payload)
    if not closed_session:
        _atomic_json(run_root / config.pipeline_id / "LAST_SUCCESS.json", final_payload)
    return PipelineRunResult(
        pipeline_id=config.pipeline_id,
        run_id=run_id,
        market_date=market_date,
        status=status,
        run_dir=run_dir,
        started_at=started_at,
        finished_at=finished_at,
        deadline_at=deadline_at,
        sla_met=sla_met,
    )

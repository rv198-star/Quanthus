"""Fail-closed OCI orchestration for versioned data-pipeline stages."""

from __future__ import annotations

import fcntl
import hashlib
import html
import json
import math
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
from urllib import request as urlrequest
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


def _positive_number(
    payload: Mapping[str, Any], key: str, *, maximum: float
) -> float | None:
    value = payload.get(key)
    if value is None:
        return None
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or not 0 < float(value) <= maximum
    ):
        raise PipelineConfigError(f"{key} must be a number from 0 (exclusive) to {maximum}")
    return float(value)


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
    observed_max_duration_seconds: float | None
    sla_seconds: int | None
    dynamic_sla_receipt_path: str | None


@dataclass(frozen=True)
class PipelineEmailNotification:
    api_url: str
    api_key_environment: str
    sender: str
    recipients: tuple[str, ...]
    timeout_seconds: int
    retries: int


@dataclass(frozen=True)
class PipelineConfig:
    source_path: Path
    source_sha256: str
    pipeline_id: str
    timezone_name: str
    close_time: wall_time
    completion_sla_minutes: int
    stage_sla_margin_percent: int
    run_root: str
    lock_path: str
    variables: tuple[PipelineVariable, ...]
    stages: tuple[PipelineStage, ...]
    email_notification: PipelineEmailNotification | None


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


def _load_email_notification(payload: Mapping[str, Any]) -> PipelineEmailNotification | None:
    notifications = payload.get("notifications")
    if notifications is None:
        return None
    if not isinstance(notifications, Mapping):
        raise PipelineConfigError("notifications must be an object")
    email = notifications.get("email")
    if not isinstance(email, Mapping):
        raise PipelineConfigError("notifications.email must be an object")
    if email.get("provider") != "resend":
        raise PipelineConfigError("notifications.email provider must be resend")
    api_url = _required_string(email, "api_url")
    if api_url != "https://api.resend.com/emails":
        raise PipelineConfigError("notifications.email api_url must be the Resend email endpoint")
    recipients_value = email.get("to")
    if (
        not isinstance(recipients_value, list)
        or not recipients_value
        or any(
            not isinstance(recipient, str) or not recipient.strip()
            for recipient in recipients_value
        )
    ):
        raise PipelineConfigError("notifications.email to must be a non-empty list of addresses")
    return PipelineEmailNotification(
        api_url=api_url,
        api_key_environment=_required_string(email, "api_key_environment"),
        sender=_required_string(email, "from"),
        recipients=tuple(recipients_value),
        timeout_seconds=_integer(
            email,
            "timeout_seconds",
            default=15,
            minimum=1,
            maximum=60,
        ),
        retries=_integer(email, "retries", default=2, minimum=0, maximum=5),
    )


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
    stage_sla_margin_percent = _integer(
        payload,
        "stage_sla_margin_percent",
        default=0,
        minimum=0,
        maximum=500,
    )
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
        observed_max_duration_seconds = _positive_number(
            value,
            "observed_max_duration_seconds",
            maximum=86400.0,
        )
        dynamic_sla_receipt_path = value.get("dynamic_sla_receipt_path")
        if dynamic_sla_receipt_path is not None and not isinstance(dynamic_sla_receipt_path, str):
            raise PipelineConfigError(
                f"stage {stage_id} dynamic_sla_receipt_path must be a string"
            )
        if observed_max_duration_seconds is not None and dynamic_sla_receipt_path is not None:
            raise PipelineConfigError(
                f"stage {stage_id} cannot use static and dynamic SLA observations together"
            )
        sla_seconds = (
            math.ceil(
                observed_max_duration_seconds * (100 + stage_sla_margin_percent) / 100
            )
            if observed_max_duration_seconds is not None
            else None
        )
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
                observed_max_duration_seconds=observed_max_duration_seconds,
                sla_seconds=sla_seconds,
                dynamic_sla_receipt_path=dynamic_sla_receipt_path,
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
        stage_sla_margin_percent=stage_sla_margin_percent,
        run_root=_required_string(payload, "run_root"),
        lock_path=_required_string(payload, "lock_path"),
        variables=tuple(variables),
        stages=tuple(stages),
        email_notification=_load_email_notification(payload),
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
        stage_sla_met = all(
            record.get("sla_met", True)
            for record in stages
            if record.get("status") == "succeeded"
        )
        sla_qualified = all(
            record.get("sla_qualified", True)
            for record in stages
            if record.get("status") == "succeeded"
        )
        payload["stage_sla_met"] = stage_sla_met
        payload["sla_qualified"] = sla_qualified
        payload["sla_met"] = finished_at <= deadline_at and stage_sla_met and sla_qualified
    if failure is not None:
        payload["failure"] = failure
    return payload


def _short_text(value: object | None) -> str:
    if value is None:
        return "-"
    compact = " ".join(str(value).split())
    return compact[:1000] if compact else "-"


def _format_seconds(value: object | None) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return f"{value:.3f}s"
    return "-"


def _html_summary_table(rows: list[tuple[str, str]]) -> str:
    return (
        '<table border="1" cellpadding="6" cellspacing="0">'
        + "".join(
            f'<tr><th align="left">{html.escape(label)}</th><td>{html.escape(value)}</td></tr>'
            for label, value in rows
        )
        + "</table>"
    )


def _handoff_details(stages: list[dict[str, Any]]) -> tuple[str, str]:
    for record in stages:
        source_id = record.get("handoff_source_id")
        if isinstance(source_id, str) and source_id:
            attempt_id = record.get("handoff_attempt_id")
            return source_id, attempt_id if isinstance(attempt_id, str) and attempt_id else "-"
    return "-", "-"


def _failed_stage_details(stages: list[dict[str, Any]]) -> tuple[str, str]:
    for record in reversed(stages):
        if record.get("status") == "failed":
            stage_id = record.get("id")
            return (
                stage_id if isinstance(stage_id, str) else "-",
                _short_text(record.get("last_error")),
            )
    return "-", "-"


def _pipeline_email_html(*, event: str, ledger: Mapping[str, Any], run_dir: Path) -> str:
    stages_value = ledger.get("stages")
    stages = (
        [stage for stage in stages_value if isinstance(stage, dict)]
        if isinstance(stages_value, list)
        else []
    )
    source_id, attempt_id = _handoff_details(stages)
    sla_met = ledger.get("sla_met")
    sla_status = "达标" if sla_met is True else "未达标" if sla_met is False else "未计算"
    heading = "DWD/DWS 清洗完成" if event == "completed" else "DWD/DWS 清洗失败"
    summary = _html_summary_table(
        [
            ("交易日", _short_text(ledger.get("market_date"))),
            ("运行 ID", _short_text(ledger.get("run_id"))),
            ("流水线状态", _short_text(ledger.get("status"))),
            ("截止时间", _short_text(ledger.get("deadline_at"))),
            ("SLA", sla_status),
            ("ODS 来源", source_id),
            ("ODS attempt", attempt_id),
            ("运行账本", str(run_dir / "run.json")),
        ]
    )
    stage_rows: list[str] = []
    for record in stages:
        if record.get("phase") not in {"build", "gate", "publish"}:
            continue
        sla_value = record.get("sla_met")
        stage_sla = "达标" if sla_value is True else "未达标" if sla_value is False else "-"
        stage_rows.append(
            "<tr>"
            f"<td>{html.escape(_short_text(record.get('id')))}</td>"
            f"<td>{html.escape(_short_text(record.get('phase')))}</td>"
            f"<td>{html.escape(_short_text(record.get('status')))}</td>"
            f"<td>{html.escape(_format_seconds(record.get('elapsed_seconds')))}</td>"
            f"<td>{html.escape(_format_seconds(record.get('sla_seconds')))}</td>"
            f"<td>{stage_sla}</td>"
            "</tr>"
        )
    stages_html = (
        '<table border="1" cellpadding="6" cellspacing="0">'
        "<tr><th>阶段</th><th>类型</th><th>状态</th><th>耗时</th><th>SLA</th><th>SLA 结果</th></tr>"
        + "".join(stage_rows)
        + "</table>"
        if stage_rows
        else "<p>尚未执行 DWD/DWS 清洗阶段。</p>"
    )
    failure_html = ""
    if event == "failed":
        failed_stage, stage_error = _failed_stage_details(stages)
        failure_html = "<h3>失败详情</h3>" + _html_summary_table(
            [
                ("失败阶段", failed_stage),
                ("阶段错误", stage_error),
                ("最终错误", _short_text(ledger.get("failure"))),
            ]
        )
    return (
        "<!doctype html><html><body>"
        f"<h2>{heading}</h2>"
        f"{summary}"
        "<h3>DWD/DWS 阶段</h3>"
        f"{stages_html}"
        f"{failure_html}"
        "</body></html>"
    )


def _send_resend_email(
    notification: PipelineEmailNotification,
    *,
    event: str,
    ledger: Mapping[str, Any],
    run_dir: Path,
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc)
    attempts = 0
    try:
        api_key = os.environ.get(notification.api_key_environment)
        if not api_key or not api_key.strip():
            raise RuntimeError(
                f"required Resend API key environment {notification.api_key_environment} is not set"
            )
        market_date = _short_text(ledger.get("market_date"))
        pipeline_id = _short_text(ledger.get("pipeline_id"))
        subject = (
            f"[Quanthus][{pipeline_id}] {market_date} DWD/DWS 清洗完成"
            if event == "completed"
            else f"[Quanthus][{pipeline_id}] {market_date} DWD/DWS 清洗失败"
        )
        request_body = json.dumps(
            {
                "from": notification.sender,
                "to": list(notification.recipients),
                "subject": subject,
                "html": _pipeline_email_html(event=event, ledger=ledger, run_dir=run_dir),
            }
        ).encode("utf-8")
        for attempts in range(1, notification.retries + 2):
            try:
                request = urlrequest.Request(
                    notification.api_url,
                    data=request_body,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "User-Agent": "Quanthus-Pipeline-Notifier/1.0",
                    },
                    method="POST",
                )
                response = urlrequest.urlopen(request, timeout=notification.timeout_seconds)
                try:
                    status_code = response.getcode()
                finally:
                    response.close()
                if not isinstance(status_code, int) or not 200 <= status_code < 300:
                    raise RuntimeError(f"Resend returned HTTP status {status_code}")
            except Exception:
                if attempts > notification.retries:
                    raise
                time.sleep(min(float(2 ** (attempts - 1)), 5.0))
            else:
                return {
                    "provider": "resend",
                    "event": event,
                    "status": "sent",
                    "attempts": attempts,
                    "started_at": started_at.isoformat(),
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
    except Exception as exc:
        return {
            "provider": "resend",
            "event": event,
            "status": "failed",
            "attempts": attempts,
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "error": _short_text(exc),
        }
    raise AssertionError("unreachable notification state")


def _notify_terminal_pipeline(
    *,
    config: PipelineConfig,
    ledger: dict[str, Any],
    ledger_path: Path,
    run_dir: Path,
) -> None:
    if config.email_notification is None:
        return
    status = ledger.get("status")
    if status in {"succeeded", "succeeded_late", "succeeded_unqualified"}:
        event = "completed"
    elif status in {"failed", "blocked"}:
        event = "failed"
    else:
        return
    ledger["notifications"] = {
        "email": _send_resend_email(
            config.email_notification,
            event=event,
            ledger=ledger,
            run_dir=run_dir,
        )
    }
    try:
        _atomic_json(ledger_path, ledger)
    except OSError:
        # A notification-record write must not replace the pipeline's terminal outcome.
        return


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


def _finish_stage_record(record: dict[str, Any], *, finished_at: datetime) -> None:
    started_at = datetime.fromisoformat(record["started_at"])
    elapsed_seconds = max(0.0, (finished_at - started_at).total_seconds())
    record["finished_at"] = finished_at.isoformat()
    record["elapsed_seconds"] = round(elapsed_seconds, 3)
    sla_seconds = record.get("sla_seconds")
    if isinstance(sla_seconds, int):
        record["sla_met"] = elapsed_seconds <= sla_seconds


def _apply_dynamic_sla(
    stage: PipelineStage,
    *,
    context: Mapping[str, str],
    record: dict[str, Any],
) -> None:
    if stage.dynamic_sla_receipt_path is None:
        return
    receipt_path = Path(_render(stage.dynamic_sla_receipt_path, context))
    try:
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineRunError(
            f"stage {stage.stage_id} cannot load dynamic SLA receipt: {receipt_path}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise PipelineRunError(f"stage {stage.stage_id} dynamic SLA receipt must be an object")
    source_id = payload.get("source_id")
    transfer = payload.get("transfer")
    if not isinstance(source_id, str) or not source_id or not isinstance(transfer, Mapping):
        raise PipelineRunError(f"stage {stage.stage_id} dynamic SLA receipt is incomplete")
    record["handoff_source_id"] = source_id
    attempt_id = payload.get("attempt_id")
    if isinstance(attempt_id, str) and attempt_id:
        record["handoff_attempt_id"] = attempt_id
    record["source_transfer_elapsed_seconds"] = transfer.get("elapsed_seconds")
    qualified = transfer.get("sla_qualified")
    if not isinstance(qualified, bool):
        raise PipelineRunError(f"stage {stage.stage_id} dynamic SLA qualification is invalid")
    record["sla_qualified"] = qualified
    if not qualified:
        return
    observed = transfer.get("observed_max_duration_seconds")
    sla_seconds = transfer.get("sla_seconds")
    if (
        not isinstance(observed, (int, float))
        or isinstance(observed, bool)
        or not math.isfinite(observed)
        or observed <= 0
        or not isinstance(sla_seconds, int)
        or isinstance(sla_seconds, bool)
        or sla_seconds < 1
    ):
        raise PipelineRunError(f"stage {stage.stage_id} dynamic SLA values are invalid")
    record["observed_max_duration_seconds"] = float(observed)
    record["sla_seconds"] = sla_seconds


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
            terminal_payload = _ledger(
                config=config,
                run_id=run_id,
                market_date=market_date,
                started_at=started_at,
                deadline_at=deadline_at,
                finished_at=finished,
                status="blocked",
                stages=stage_records,
                failure=message,
            )
            _atomic_json(ledger_path, terminal_payload)
            _notify_terminal_pipeline(
                config=config,
                ledger=terminal_payload,
                ledger_path=ledger_path,
                run_dir=run_dir,
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
                if stage.observed_max_duration_seconds is not None:
                    record["observed_max_duration_seconds"] = stage.observed_max_duration_seconds
                    record["sla_seconds"] = stage.sla_seconds
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
                        _apply_dynamic_sla(
                            stage,
                            context=context,
                            record=record,
                        )
                        record["status"] = "succeeded"
                        _finish_stage_record(
                            record,
                            finished_at=datetime.now(timezone.utc),
                        )
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
                _finish_stage_record(stage_records[-1], finished_at=finished)
            terminal_payload = _ledger(
                config=config,
                run_id=run_id,
                market_date=market_date,
                started_at=started_at,
                deadline_at=deadline_at,
                finished_at=finished,
                status="failed",
                stages=stage_records,
                failure=str(exc),
            )
            _atomic_json(ledger_path, terminal_payload)
            _notify_terminal_pipeline(
                config=config,
                ledger=terminal_payload,
                ledger_path=ledger_path,
                run_dir=run_dir,
            )
            if isinstance(exc, PipelineRunError):
                raise
            raise PipelineRunError(f"pipeline failed: {exc}") from exc

    finished_at = datetime.now(timezone.utc)
    stage_sla_met = all(
        record.get("sla_met", True)
        for record in stage_records
        if record.get("status") == "succeeded"
    )
    sla_qualified = all(
        record.get("sla_qualified", True)
        for record in stage_records
        if record.get("status") == "succeeded"
    )
    sla_met = finished_at <= deadline_at and stage_sla_met and sla_qualified
    if closed_session:
        status = "skipped_closed_session"
    elif not sla_qualified:
        status = "succeeded_unqualified"
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
    _notify_terminal_pipeline(
        config=config,
        ledger=final_payload,
        ledger_path=ledger_path,
        run_dir=run_dir,
    )
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

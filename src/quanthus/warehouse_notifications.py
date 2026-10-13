"""Idempotent operational email notifications for the DWD daily handoff."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import html
import json
import os
import re
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from urllib import error as urllib_error
from urllib import request
from zoneinfo import ZoneInfo

from quanthus.pipelines.securities_warehouse import (
    DailyDomainBuildError,
    DailyDomainResult,
    verify_daily_domain_increment,
)


class WarehouseNotificationError(RuntimeError):
    """Raised when a warehouse completion notification cannot be delivered."""

    def __init__(
        self, message: str, *, code: str = "notification_failed", attempts: int = 0
    ) -> None:
        super().__init__(message)
        self.code = code
        self.attempts = attempts


EVENTS = {
    "p10_cn01_daily_domain_failed": {
        "node": "CN01",
        "stage": "日频领域接续",
        "heading": "CN01 日频领域接续失败",
        "failure": True,
    },
    "p10_cn01_natural_day_ods_failed": {
        "node": "CN01",
        "stage": "自然日公告采集",
        "heading": "CN01 自然日公告采集失败",
        "failure": True,
    },
    "p10_cn01_weekly_source_failed": {
        "node": "CN01",
        "stage": "周频来源",
        "heading": "CN01 周频来源失败",
        "failure": True,
    },
    "p10_cn01_weekly_continuation_failed": {
        "node": "CN01",
        "stage": "周频 DWD 接续",
        "heading": "CN01周频 DWD 接续失败",
        "failure": True,
    },
    "p10_cn01_acquisition_quality_failed": {
        "node": "CN01",
        "stage": "采集端来源检查",
        "heading": "CN01采集端来源缺口",
        "failure": True,
    },
    "p10_cn01_complete_daily_gdrive_failed": {
        "node": "CN01",
        "stage": "完整 DWD GDrive归档",
        "heading": "CN01完整 DWD GDrive归档失败",
        "failure": True,
    },
    "p10_cn01_daily_cycle_failed": {
        "node": "CN01",
        "stage": "日频ODS/DWD任务",
        "heading": "CN01日频ODS/DWD任务失败",
        "failure": True,
    },
    "p10_cn01_daily_ods_failed": {
        "node": "CN01",
        "stage": "T日 ODS日频",
        "heading": "CN01 T日 ODS日频采集失败",
        "failure": True,
    },
    "p10_cn01_native_minutes_failed": {
        "node": "CN01",
        "stage": "T+1交易日 ODS分钟",
        "heading": "CN01 T+1交易日 1m/5m采集或质量未达标",
        "failure": True,
    },
    "p10_cn01_complete_daily_failed": {
        "node": "CN01",
        "stage": "T+1完整 DWD",
        "heading": "CN01 T+1完整 DWD构建未完成",
        "failure": True,
    },
    "p10_oci_complete_daily_failed": {
        "node": "OCI",
        "stage": "T+1完整 DWD同步",
        "heading": "OCI T+1完整 DWD同步或消费者接入未完成",
        "failure": True,
    },
    "cn01_dwd_completed": {
        "node": "CN01",
        "stage": "DWD构建",
        "heading": "CN01 DWD 构建完成",
        "failure": False,
    },
    "cn01_dwd_failed": {
        "node": "CN01",
        "stage": "DWD构建",
        "heading": "CN01 DWD 构建失败",
        "failure": True,
    },
    "hermes_dwd_completed": {
        "node": "Hermes",
        "stage": "DWD构建",
        "heading": "Hermes DWD 构建完成",
        "failure": False,
    },
    "hermes_dwd_failed": {
        "node": "Hermes",
        "stage": "DWD构建",
        "heading": "Hermes DWD 构建失败",
        "failure": True,
    },
    "oci_dwd_sync_completed": {
        "node": "OCI",
        "stage": "DWD同步",
        "heading": "OCI DWD 增量同步完成",
        "failure": False,
    },
    "oci_dwd_sync_failed": {
        "node": "OCI",
        "stage": "DWD同步",
        "heading": "OCI DWD 增量同步失败",
        "failure": True,
    },
    "p10_cn01_daily_core_failed": {
        "node": "CN01",
        "stage": "P10日级核心",
        "heading": "CN01 日级核心交付未达标",
        "failure": True,
    },
    "p10_hermes_daily_core_failed": {
        "node": "Hermes",
        "stage": "P10日级核心",
        "heading": "Hermes 日级核心交付未达标",
        "failure": True,
    },
    "p10_oci_daily_core_mirror_failed": {
        "node": "OCI",
        "stage": "P10日级核心镜像",
        "heading": "OCI 日级核心同步未达标",
        "failure": True,
    },
    "p10_cn01_minute_t_failed": {
        "node": "CN01",
        "stage": "P10 1m T采集",
        "heading": "CN01 当日分钟采集未完成",
        "failure": True,
    },
    "p10_cn01_minute_tplus1_failed": {
        "node": "CN01",
        "stage": "P10 1m T+1",
        "heading": "CN01 下一自然日分钟修订未完成",
        "failure": True,
    },
    "p10_cn01_minute_quality_degraded": {
        "node": "CN01",
        "stage": "P10 1m质量",
        "heading": "CN01 分钟研究级质量降级提示",
        "failure": True,
        "status_label": "质量降级",
    },
    "p10_cn01_minute_delivery_pass": {
        "node": "CN01",
        "stage": "P10 1m",
        "heading": "CN01 分钟数据交付 PASS",
        "failure": False,
        "status_label": "PASS · 数据交付通过",
    },
    "p10_cn01_minute_delivery_fail": {
        "node": "CN01",
        "stage": "P10 1m",
        "heading": "CN01 分钟数据交付 FAIL",
        "failure": True,
        "status_label": "FAIL · 数据交付失败",
    },
}


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
        "ascii"
    )


def _atomic_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o640)
            stream.write(_canonical_bytes(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WarehouseNotificationError(f"cannot read JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise WarehouseNotificationError(f"JSON root is not an object: {path}")
    return value


def _required_string(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise WarehouseNotificationError(f"notification response field {key!r} is invalid")
    return item


def _bounded_error(value: object) -> str:
    """Keep provider and system errors useful without persisting unbounded text or secrets."""

    if value is None:
        return "unspecified failure"
    text = " ".join(str(value).split())
    api_key = os.environ.get("QUANTHUS_RESEND_API_KEY", "").strip()
    if api_key:
        text = text.replace(api_key, "[redacted]")
    return text[:2000] or "unspecified failure"


def _failure_digest(value: object) -> str:
    # Systemd paths and timestamps often vary between retries; do not make every retry a new
    # logical email just because those volatile fragments changed.
    normalized = re.sub(r"\b\d{4}-\d{2}-\d{2}T[^\s]+", "<timestamp>", _bounded_error(value))
    normalized = re.sub(r"\b[0-9a-f]{12,}\b", "<hex>", normalized, flags=re.IGNORECASE)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _package_from_current(
    *, daily_root: Path, market_date: date
) -> tuple[dict[str, object], DailyDomainResult]:
    pointer = _object(daily_root / "CURRENT.json")
    if pointer.get("kind") != "cn01_daily_dwd_current":
        raise WarehouseNotificationError("DWD CURRENT kind is invalid")
    if pointer.get("market_date") != market_date.isoformat():
        raise WarehouseNotificationError("DWD CURRENT does not match the requested market date")
    raw_manifest = pointer.get("manifest_path")
    if not isinstance(raw_manifest, str):
        raise WarehouseNotificationError("DWD CURRENT manifest_path is invalid")
    manifest_path = Path(raw_manifest)
    if (
        manifest_path.is_absolute()
        or ".." in manifest_path.parts
        or len(manifest_path.parts) != 4
        or manifest_path.parts[0] != "packages"
        or manifest_path.parts[1] != market_date.isoformat()
        or manifest_path.parts[-1] != "manifest.json"
    ):
        raise WarehouseNotificationError("DWD CURRENT manifest_path is outside the daily package")
    package = (daily_root / manifest_path).resolve()
    if not package.is_relative_to(daily_root.resolve()):
        raise WarehouseNotificationError("DWD CURRENT manifest_path escapes the daily root")
    try:
        result = verify_daily_domain_increment(package.parent)
    except (DailyDomainBuildError, OSError, ValueError) as exc:
        raise WarehouseNotificationError(f"DWD package failed verification: {exc}") from exc
    if pointer.get("package_id") != result.package_id:
        raise WarehouseNotificationError("DWD CURRENT package_id differs from the manifest")
    if pointer.get("manifest_sha256") != result.manifest_sha256:
        raise WarehouseNotificationError("DWD CURRENT manifest_sha256 differs from the manifest")
    if pointer.get("content_digest") != result.content_digest:
        raise WarehouseNotificationError("DWD CURRENT content_digest differs from the manifest")
    return pointer, result


def _market_date_from_current(*, daily_root: Path) -> date:
    pointer = _object(daily_root / "CURRENT.json")
    raw_date = pointer.get("market_date")
    if not isinstance(raw_date, str):
        raise WarehouseNotificationError("DWD CURRENT market_date is invalid")
    try:
        return date.fromisoformat(raw_date)
    except ValueError as exc:
        raise WarehouseNotificationError("DWD CURRENT market_date is invalid") from exc


def _recipients() -> tuple[str, ...]:
    raw = os.environ.get("QUANTHUS_RESEND_TO", "61718@qq.com")
    values = tuple(item.strip() for item in raw.split(",") if item.strip())
    if not values:
        raise WarehouseNotificationError("QUANTHUS_RESEND_TO must contain at least one address")
    return values


def _email_html(
    *,
    event: str,
    market_date: date,
    pointer: Mapping[str, object] | None,
    result: DailyDomainResult | None,
    error: str | None,
    context: Mapping[str, object],
) -> str:
    node = str(context.get("node") or EVENTS[event]["node"])
    stage = str(context.get("stage") or EVENTS[event]["stage"])
    details: list[tuple[str, str]] = [
        ("节点", node),
        ("阶段", stage),
        (str(context.get("market_date_label", "交易日")), market_date.isoformat()),
        (
            "状态",
            str(
                EVENTS[event].get(
                    "status_label",
                    "失败" if EVENTS[event]["failure"] else "成功",
                )
            ),
        ),
    ]
    if result is not None:
        details.extend(
            [
                ("包 ID", str(result.package_id)),
                ("manifest SHA-256", str(result.manifest_sha256)),
                ("content digest", str(result.content_digest)),
                ("载荷大小", f"{result.payload_bytes} bytes"),
                ("行数", json.dumps(dict(result.row_counts), ensure_ascii=False, sort_keys=True)),
                ("CURRENT", str(pointer.get("manifest_path", "-")) if pointer else "-"),
            ]
        )
    if error:
        details.append(("错误", error))
    context_labels = {
        "unit": "systemd unit",
        "run_path": "运行路径",
        "error_code": "错误分类",
        "executed_phase": "采集阶段",
        "collection_status": "采集执行",
        "publication_status": "分级数据发布",
        "quality_distribution": "证券质量分布",
        "quality_reasons": "R3主要原因",
        "research_qualification": "最终研究资格",
        "tplus1_state": "补数终态",
        "delivery_verdict": "交付判定",
        "delivery_reason": "判定原因",
        "source_revised_count": "源数据修订证券数",
        "grade_manifest_sha256": "质量包 manifest SHA-256",
        "required_intervals": "所需数据频率",
        "deadline": "交付截止时间",
        "target_next_trade_day": "T+1交易日",
        "policy_id": "运行规则版本",
    }
    for key, label in context_labels.items():
        value = context.get(key)
        if value is not None and str(value).strip():
            details.append((label, str(value)))
    rows = "".join(
        f'<tr><th align="left">{html.escape(label)}</th><td>{html.escape(str(value))}</td></tr>'
        for label, value in details
    )
    return (
        "<!doctype html><html><body>"
        f"<h2>{html.escape(str(EVENTS[event]['heading']))}</h2>"
        f'<table border="1" cellpadding="6" cellspacing="0">{rows}</table>'
        "</body></html>"
    )


def _send_resend(
    *,
    event: str,
    market_date: date,
    pointer: Mapping[str, object] | None,
    result: DailyDomainResult | None,
    key: str,
    error: str | None = None,
    context: Mapping[str, object] | None = None,
) -> dict[str, object]:
    api_key = os.environ.get("QUANTHUS_RESEND_API_KEY", "").strip()
    if not api_key:
        raise WarehouseNotificationError("QUANTHUS_RESEND_API_KEY is not set")
    api_url = os.environ.get("QUANTHUS_RESEND_API_URL", "https://api.resend.com/emails")
    sender = os.environ.get("QUANTHUS_RESEND_FROM", "report@mindthus.com").strip()
    if not sender:
        raise WarehouseNotificationError("QUANTHUS_RESEND_FROM is empty")
    retries_raw = os.environ.get("QUANTHUS_RESEND_RETRIES", "2")
    try:
        retries = min(max(int(retries_raw), 0), 5)
    except ValueError as exc:
        raise WarehouseNotificationError("QUANTHUS_RESEND_RETRIES must be an integer") from exc
    try:
        timeout = float(os.environ.get("QUANTHUS_RESEND_TIMEOUT_SECONDS", "15"))
    except ValueError as exc:
        raise WarehouseNotificationError("QUANTHUS_RESEND_TIMEOUT_SECONDS must be numeric") from exc
    outcome_text = (
        str(EVENTS[event]["status_label"])
        if "status_label" in EVENTS[event]
        else ("失败" if EVENTS[event]["failure"] else "成功")
    )
    mail_context = context or {}
    if event == "p10_cn01_minute_quality_degraded":
        phase = str(mail_context.get("executed_phase") or mail_context.get("stage") or "1m")
        qualification = str(mail_context.get("research_qualification") or "未验证")
        outcome_text = f"{phase} {qualification} 质量降级"
    subject_stage = EVENTS[event]["stage"]
    if event in {"p10_cn01_minute_delivery_pass", "p10_cn01_minute_delivery_fail"}:
        phase = str(mail_context.get("executed_phase") or "")
        subject_stage = f"1m {phase}"
        outcome_text = "PASS" if event.endswith("_pass") else "FAIL"
    body = _canonical_bytes(
        {
            "from": sender,
            "to": list(_recipients()),
            "subject": (
                f"[Quanthus][{EVENTS[event]['node']}][{subject_stage}] "
                f"{market_date.isoformat()} "
                f"{outcome_text}"
            ),
            "html": _email_html(
                event=event,
                market_date=market_date,
                pointer=pointer,
                result=result,
                error=error,
                context=context or {},
            ),
        }
    )
    attempts = 0
    for attempts in range(1, retries + 2):
        try:
            http_request = request.Request(
                api_url,
                data=body,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Idempotency-Key": key,
                    "User-Agent": "Quanthus-Warehouse-Notifier/1.0",
                },
                method="POST",
            )
            try:
                with request.urlopen(http_request, timeout=timeout) as response:
                    status = response.getcode()
                    response_bytes = response.read()
            except urllib_error.HTTPError as exc:
                response_bytes = exc.read(1024)
                detail = response_bytes.decode("utf-8", errors="replace").strip()
                if detail:
                    detail = f": {_bounded_error(detail)}"
                raise WarehouseNotificationError(
                    f"Resend returned HTTP status {exc.code}{detail}",
                    code="resend_http_error",
                ) from exc
            if not isinstance(status, int) or not 200 <= status < 300:
                raise WarehouseNotificationError(
                    f"Resend returned HTTP status {status}", code="resend_http_error"
                )
            response_value: object = {}
            if response_bytes:
                try:
                    response_value = json.loads(response_bytes.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    response_value = {}
            message_id = response_value.get("id") if isinstance(response_value, Mapping) else None
            return {
                "provider": "resend",
                "status": "sent",
                "attempts": attempts,
                "message_id": message_id if isinstance(message_id, str) else None,
            }
        except Exception as exc:
            if attempts > retries:
                message = _bounded_error(exc)
                if isinstance(exc, WarehouseNotificationError):
                    raise WarehouseNotificationError(
                        message, code=exc.code, attempts=attempts
                    ) from exc
                raise WarehouseNotificationError(
                    f"Resend request failed: {message}",
                    code="resend_request_failed",
                    attempts=attempts,
                ) from exc
            time.sleep(min(float(2 ** (attempts - 1)), 5.0))
    raise AssertionError("unreachable notification state")


def _failure_context(
    *, failure_receipt: Path | None, error: str | None, context: Mapping[str, object] | None
) -> tuple[str, dict[str, object]]:
    values = dict(context or {})
    if failure_receipt is not None:
        receipt = _object(failure_receipt)
        for key in ("error", "error_code", "stage", "node", "unit", "run_path", "market_date"):
            if key in receipt and key not in values:
                values[key] = receipt[key]
    raw_error = error if error is not None else values.get("error")
    bounded = _bounded_error(raw_error)
    values["error"] = bounded
    return bounded, values


def send_daily_event(
    *,
    event: str,
    daily_root: Path,
    market_date: date | None,
    state_root: Path,
    error: str | None = None,
    context: Mapping[str, object] | None = None,
    failure_receipt: Path | None = None,
) -> dict[str, object]:
    if event not in EVENTS:
        raise WarehouseNotificationError(f"unsupported warehouse event: {event}")
    failure = bool(EVENTS[event]["failure"])
    event_context: dict[str, object] = dict(context or {})
    if failure:
        failure_error, event_context = _failure_context(
            failure_receipt=failure_receipt, error=error, context=event_context
        )
        if market_date is None:
            raw_date = event_context.get("market_date")
            if isinstance(raw_date, str):
                try:
                    market_date = date.fromisoformat(raw_date)
                except ValueError as exc:
                    raise WarehouseNotificationError(
                        "failure receipt market_date is invalid"
                    ) from exc
        if market_date is None:
            market_date = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        pointer = None
        result = None
        key_material = (
            f"quanthus-warehouse-v1\x1f{event}\x1f{market_date}\x1f"
            f"{event_context.get('stage', '')}\x1f{event_context.get('run_path', '')}\x1f"
            f"{_failure_digest(failure_error)}"
        )
    else:
        if market_date is None:
            market_date = _market_date_from_current(daily_root=daily_root)
        pointer, result = _package_from_current(daily_root=daily_root, market_date=market_date)
        failure_error = None
        key_material = (
            f"quanthus-warehouse-v1\x1f{event}\x1f{market_date}\x1f{result.manifest_sha256}"
        )
    key = hashlib.sha256(key_material.encode("ascii")).hexdigest()
    state_root.mkdir(parents=True, exist_ok=True)
    lock_path = state_root / f"{key}.lock"
    state_path = state_root / f"{key}.json"
    with lock_path.open("a+", encoding="ascii") as lock_stream:
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
        if state_path.exists():
            value = _object(state_path)
            value["status"] = "skipped"
            value["idempotency_key"] = key
            return value
        try:
            sent = _send_resend(
                event=event,
                market_date=market_date,
                pointer=pointer,
                result=result,
                key=key,
                error=failure_error,
                context=event_context,
            )
        except Exception as exc:
            notification_error = (
                exc
                if isinstance(exc, WarehouseNotificationError)
                else WarehouseNotificationError(
                    f"notification delivery failed: {_bounded_error(exc)}"
                )
            )
            failure_record: dict[str, object] = {
                "schema_version": 1,
                "kind": "quanthus_warehouse_notification_failure",
                "event": event,
                "market_date": market_date.isoformat(),
                "idempotency_key": key,
                "delivery_status": "failed",
                "error_code": notification_error.code,
                "error": _bounded_error(notification_error),
                "attempts": notification_error.attempts,
                "failed_at": datetime.now(timezone.utc).isoformat(),
            }
            _atomic_json(state_root / "failures" / f"{key}.json", failure_record)
            raise notification_error from exc
        record: dict[str, object] = {
            "schema_version": 1,
            "kind": "quanthus_warehouse_notification",
            "event": event,
            "market_date": market_date.isoformat(),
            "idempotency_key": key,
            "sent_at": datetime.now(timezone.utc).isoformat(),
            **sent,
        }
        if result is not None:
            record.update(
                {
                    "package_id": result.package_id,
                    "manifest_sha256": result.manifest_sha256,
                    "content_digest": result.content_digest,
                }
            )
        if event_context:
            record["context"] = event_context
        if failure_error is not None:
            record.update(
                {
                    "delivery_status": "sent",
                    "error": failure_error,
                }
            )
        _atomic_json(state_path, record)
        return record


def send_minute_delivery_outcome(
    *,
    market_date: date,
    phase: str,
    passed: bool,
    state_root: Path,
    context: Mapping[str, object],
    reason: str | None = None,
) -> dict[str, object]:
    """Exactly one final PASS or FAIL email per date+phase, not per verdict.

    Minute quality is independently SHA-verified upstream. Do not enter the
    daily-DWD CURRENT path that send_daily_event uses for ordinary successes.
    Lock identity excludes the verdict so duplicate 12:10/22:10 checkpoints
    can never mail a contradictory PASS after an earlier FAIL (or vice versa).
    """
    if phase not in {"minute_T", "minute_Tplus1"}:
        raise WarehouseNotificationError("invalid CN01 minute email phase")
    if context.get("delivery_verdict") != ("PASS" if passed else "FAIL"):
        raise WarehouseNotificationError("minute email verdict/context differ")
    event = "p10_cn01_minute_delivery_pass" if passed else "p10_cn01_minute_delivery_fail"
    key_material = f"quanthus-p10-minute-outcome-v1\x1fCN01\x1f{phase}\x1f{market_date}"
    key = hashlib.sha256(key_material.encode("ascii")).hexdigest()
    record_root = state_root / "minute-delivery-v1"
    record_root.mkdir(parents=True, exist_ok=True)
    state_path = record_root / f"{key}.json"
    with (record_root / f"{key}.lock").open("a+", encoding="ascii") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        if state_path.is_file():
            recorded = _object(state_path)
            if recorded.get("market_date") != market_date.isoformat():
                raise WarehouseNotificationError("minute email receipt date mismatch")
            return {**recorded, "status": "skipped"}
        try:
            sent = _send_resend(
                event=event,
                market_date=market_date,
                key=key,
                pointer=None,
                result=None,
                error=reason if not passed else None,
                context={"node": "CN01", "stage": phase, **context},
            )
        except Exception as exc:
            error_code = (
                exc.code
                if isinstance(exc, WarehouseNotificationError)
                else "minute_notification_failed"
            )
            _atomic_json(
                record_root / "failures" / f"{key}.json",
                {
                    "kind": "p10_minute_delivery_notice_failure_v1",
                    "phase": phase,
                    "market_date": market_date.isoformat(),
                    "error_code": error_code,
                    "error": _bounded_error(exc),
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            raise
        receipt: dict[str, object] = {
            "kind": "p10_minute_delivery_notice_v1",
            "phase": phase,
            "market_date": market_date.isoformat(),
            "event": event,
            "verdict": "PASS" if passed else "FAIL",
            "status": "sent",
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "idempotency_key": key,
            "delivery_reason": reason,
            "grade_manifest_sha256": context.get("grade_manifest_sha256"),
            **sent,
        }
        _atomic_json(state_path, receipt)
        return receipt


def send_failure_events(
    *, failure_receipt: Path, daily_root: Path, state_root: Path
) -> dict[str, object]:
    """Send the OCI synchronization failure alert."""

    try:
        receipt = _object(failure_receipt)
    except WarehouseNotificationError as exc:
        # A full sync failure can include a disk error before its receipt is written.  Keep a
        # generic OCI alert possible in that case; the journal still contains the detailed cause.
        record = send_daily_event(
            event="oci_dwd_sync_failed",
            daily_root=daily_root,
            market_date=None,
            state_root=state_root,
            error=f"failure receipt unavailable: {exc}",
            context={
                "stage": "oci_dwd_sync",
                "node": os.environ.get("QUANTHUS_NODE", "OCI"),
                "unit": os.environ.get("QUANTHUS_SYSTEMD_UNIT", "oci-dwd-daily-sync.service"),
            },
        )
        return {"status": "sent", "events": [record]}
    failed_at = receipt.get("failed_at")
    if isinstance(failed_at, str):
        try:
            failure_time = datetime.fromisoformat(failed_at)
            if failure_time.tzinfo is None:
                raise ValueError("missing timezone")
        except ValueError as exc:
            raise WarehouseNotificationError("failure receipt failed_at is invalid") from exc
        max_age = int(os.environ.get("QUANTHUS_FAILURE_RECEIPT_MAX_AGE_SECONDS", "600"))
        age = (datetime.now(timezone.utc) - failure_time.astimezone(timezone.utc)).total_seconds()
        if age > max_age:
            return {"status": "skipped_stale", "events": [], "receipt_age_seconds": int(age)}
    record = send_daily_event(
        event="oci_dwd_sync_failed",
        daily_root=daily_root,
        market_date=None,
        state_root=state_root,
        failure_receipt=failure_receipt,
    )
    return {"status": "sent", "events": [record]}


def _parse_market_date(value: str | None) -> date:
    if value:
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise WarehouseNotificationError("market date must use YYYY-MM-DD") from exc
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", choices=sorted(EVENTS), required=True)
    parser.add_argument("--daily-root", type=Path, required=True)
    parser.add_argument("--market-date")
    parser.add_argument("--error")
    parser.add_argument("--stage")
    parser.add_argument("--node")
    parser.add_argument("--unit")
    parser.add_argument("--run-path")
    parser.add_argument("--failure-receipt", type=Path)
    parser.add_argument(
        "--state-root",
        type=Path,
        default=Path(
            os.environ.get("QUANTHUS_NOTIFICATION_STATE_ROOT", "/var/lib/quanthus/notifications")
        ),
    )
    args = parser.parse_args(argv)
    context = {
        key: value
        for key, value in {
            "stage": args.stage,
            "node": args.node,
            "unit": args.unit,
            "run_path": args.run_path,
        }.items()
        if value
    }
    try:
        result = send_daily_event(
            event=args.event,
            daily_root=args.daily_root,
            market_date=_parse_market_date(args.market_date) if args.market_date else None,
            state_root=args.state_root,
            error=args.error,
            context=context,
            failure_receipt=args.failure_receipt,
        )
    except WarehouseNotificationError as exc:
        print(f"warehouse notification failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

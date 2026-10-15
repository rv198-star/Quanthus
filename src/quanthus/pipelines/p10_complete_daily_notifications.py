"""One failure email per target/stage, including retries of transport failure."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import time
from contextlib import suppress
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus import warehouse_notifications as mail
from quanthus.pipelines.p10_complete_daily_schedule import POLICY_ID
from quanthus.pipelines.p10_delivery_common import atomic, digest, encoded, object_at

EVENTS = {
    "domain_daily": "p10_cn01_daily_domain_failed",
    "natural_day": "p10_cn01_natural_day_ods_failed",
    "weekly_source": "p10_cn01_weekly_source_failed",
    "weekly": "p10_cn01_weekly_continuation_failed",
    "acquisition_quality": "p10_cn01_acquisition_quality_failed",
    "gdrive": "p10_cn01_complete_daily_gdrive_failed",
    "daily_ods": "p10_cn01_daily_ods_failed",
    "daily_cycle": "p10_cn01_daily_cycle_failed",
    "minutes": "p10_cn01_native_minutes_failed",
    "complete": "p10_cn01_complete_daily_failed",
    "mirror": "p10_oci_complete_daily_failed",
}


def _notification_key(day: date, stage: str, context: dict[str, Any]) -> str:
    identity = [POLICY_ID, day.isoformat(), stage]
    if str(context.get("market_date_label", "")).startswith("运行日期"):
        identity.append("execution_date_unresolved")
    if context.get("isolated_failure_drill"):
        identity.append(str(context["isolated_failure_drill"]))
    return digest(encoded(identity))[7:]


def failure(
    *, day: date, stage: str, state_root: Path, reason: str, context: dict[str, Any] | None = None
) -> dict[str, Any]:
    event = EVENTS[stage]
    state_root.mkdir(parents=True, exist_ok=True)
    key = _notification_key(day, stage, context or {})
    path = state_root / f"{key}.json"
    details = {"policy_id": POLICY_ID, "required_intervals": ["1d", "1m", "5m"], **(context or {})}
    with (state_root / f"{key}.lock").open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        pending = state_root / "pending" / f"{key}.json"
        if path.exists():
            pending.unlink(missing_ok=True)
            return {**object_at(path), "status": "already_sent"}
        if not pending.exists():
            atomic(
                pending,
                {
                    "policy_id": POLICY_ID,
                    "market_date": day.isoformat(),
                    "stage": stage,
                    "reason": mail._bounded_error(reason),
                    "context": details,
                    "queued_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        payload = object_at(pending)
        if (
            payload.get("policy_id") != POLICY_ID
            or payload.get("market_date") != day.isoformat()
            or payload.get("stage") != stage
            or not isinstance(payload.get("reason"), str)
            or not isinstance(payload.get("context"), dict)
            or _notification_key(day, stage, payload["context"]) != key
        ):
            raise ValueError("pending notification identity differs")
        try:
            sent = mail._send_resend(
                event=event,
                market_date=day,
                pointer=None,
                result=None,
                key=key,
                error=payload["reason"],
                context=payload["context"],
            )
        except Exception as exc:
            atomic(
                state_root / "failures" / f"{key}.json",
                {
                    "market_date": day.isoformat(),
                    "stage": stage,
                    "delivery_status": "failed",
                    "error_class": type(exc).__name__,
                    "policy_id": POLICY_ID,
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            raise
        record = {
            "market_date": day.isoformat(),
            "stage": stage,
            "delivery_status": "sent",
            "policy_id": POLICY_ID,
            "idempotency_key": key,
            "sent_at": datetime.now(timezone.utc).isoformat(),
            **sent,
        }
        atomic(path, record)
        pending.unlink(missing_ok=True)
        return record


def retry_pending(state_root: Path, *, limit: int = 4) -> dict[str, Any]:
    """Replay bounded immutable mail requests without re-running any data stage."""
    if not 1 <= limit <= 4:
        raise ValueError("notification replay limit must be between one and four")
    outcomes = []
    # Bytes are immutable; mtime is only the mail queue's round-robin priority.
    pending = sorted(
        (state_root / "pending").glob("*.json"), key=lambda p: (p.stat().st_mtime_ns, p.name)
    )
    for path in pending[:limit]:
        try:
            payload = object_at(path)
            day = date.fromisoformat(payload["market_date"])
            stage = payload["stage"]
            if (
                stage not in EVENTS
                or path.stem != _notification_key(day, stage, payload["context"])
                or payload.get("policy_id") != POLICY_ID
            ):
                raise ValueError("pending notification identity differs")
            record = failure(
                day=day,
                stage=stage,
                state_root=state_root,
                reason=payload["reason"],
                context=payload["context"],
            )
            outcomes.append({"key": path.stem, "status": record.get("status", "sent")})
        except Exception as exc:
            with suppress(FileNotFoundError):
                os.utime(path, None)
            outcomes.append(
                {"key": path.stem, "status": "retry_failed", "error_class": type(exc).__name__}
            )
    return {
        "status": "notification_retry_completed",
        "outcomes": outcomes,
        "remaining": len(list((state_root / "pending").glob("*.json"))),
    }


def _service_started_at(unit: str) -> float | None:
    try:
        started = (
            int(
                subprocess.check_output(
                    [
                        "systemctl",
                        "show",
                        unit,
                        "--property=ExecMainStartTimestampMonotonic",
                        "--value",
                    ],
                    text=True,
                    timeout=5,
                    stderr=subprocess.DEVNULL,
                ).strip()
            )
            / 1_000_000
        )
        return time.time() - (time.monotonic() - started) if started > 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _daily_service_started_at() -> float | None:
    return _service_started_at("quanthus-daily-core@cn01.service")


def _service_invocation_id(unit: str) -> str | None:
    """Only the originating failure identity, never a newer current invocation."""
    value = os.environ.get("MONITOR_INVOCATION_ID", "")
    if os.environ.get("MONITOR_UNIT") != unit or not re.fullmatch(r"[0-9a-f]{32}", value):
        return None
    return value


def _complete_cycle_failure_target(
    now: datetime, state: Path, *, originating_invocation_id: str | None = None
) -> tuple[date, str, str]:
    """Resolve only the exact originating controller invocation's date and phase."""
    fallback = (now.date(), "minutes", "运行日期（目标交易日未确定）")
    unit = "quanthus-complete-daily.service"
    service_start = _service_started_at(unit)
    invocation = (
        originating_invocation_id
        if originating_invocation_id is not None
        else _service_invocation_id(unit)
    )
    try:
        if (
            service_start is None
            or invocation is None
            or not re.fullmatch(r"[0…10884 tokens truncated…tage),
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
        "source_complete_status": "上游补数与构建",
        "source_minute_quality": "上游分钟质量声明",
        "mirror_stage_status": "OCI交付阶段",
        "consumer_admission_status": "OCI消费准入",
        "source_manifest_sha256": "来源完整包manifest",
        "source_content_digest": "来源完整包content",
        "service_result": "服务停止原因",
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

"""Task-local operational API mail; no data imports or notification service."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib import request
from zoneinfo import ZoneInfo

EVENT_KEYS = {
    "kind",
    "run_id",
    "role",
    "market_date",
    "status",
    "reason",
    "observed_at",
    "pipeline_revision",
    "manifests",
    "execution_authorized",
}
STATUSES = {"complete", "partial", "closed", "busy", "failed"}


def atomic(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def validate(value: dict[str, Any]) -> None:
    if (
        set(value) != EVENT_KEYS
        or value["kind"] != "p10-container-job-event-v1"
        or value["role"] not in {"daily", "recovery", "weekly"}
        or value["status"] not in STATUSES
        or value["execution_authorized"] is not False
        or not re.fullmatch(r"[a-z0-9-]{1,100}", str(value["run_id"]))
        or not re.fullmatch(r"[A-Z0-9_]{1,100}", str(value["reason"]))
        or not re.fullmatch(r"[0-9a-f]{40}", str(value["pipeline_revision"]))
        or not isinstance(value["manifests"], list)
        or any(not re.fullmatch(r"sha256:[0-9a-f]{64}", str(h)) for h in value["manifests"])
    ):
        raise ValueError("P10_OPERATIONAL_EVENT_INVALID")
    date.fromisoformat(value["market_date"])
    if datetime.fromisoformat(value["observed_at"]).tzinfo is None:
        raise ValueError("P10_EVENT_CLOCK_REQUIRES_ZONE")
    if value["status"] == "complete" and not value["manifests"]:
        raise ValueError("P10_COMPLETE_EVENT_REQUIRES_VERIFIED_MANIFEST")


def render_event(value: dict[str, Any]) -> tuple[str, str]:
    """Present only the validated terminal event; never infer data or backup gates."""
    validate(value)
    role = {"daily": "每日数据", "recovery": "数据补建", "weekly": "每周数据"}[value["role"]]
    status = {
        "complete": "完成",
        "partial": "部分完成",
        "closed": "休市处理完成",
        "busy": "已有任务运行",
        "failed": "失败",
    }[value["status"]]
    late = value["reason"].startswith("LATE_RECOVERY_")
    reason = value["reason"].removeprefix("LATE_RECOVERY_")
    descriptions = {
        ("complete", "QUALIFIED_1D_NATIVE5M"): "日频和 5 分钟 ODS/DWD 已构建完成，并通过验证。",
        ("partial", "CORE_READY_5M_HELD_SOURCE_QUALITY"): (
            "日频核心数据已就绪；5 分钟来源未通过质量检查，完整 Daily 尚未完成。"
        ),
        ("partial", "CORE_READY_5M_PENDING"): (
            "日频核心数据已就绪；5 分钟数据仍待完成，完整 Daily 尚未完成。"
        ),
        ("partial", "WEEKLY_FINANCIAL_VERIFIED_OTHER_DOMAINS_HELD"): (
            "每周财务数据已通过验证；其他领域仍暂缓，完整 Weekly 尚未完成。"
        ),
        ("closed", "AUTHORITATIVE_CLOSED_EVENT_SOURCE_READY"): (
            "交易日历确认当日休市，事件来源已就绪；此结果不代表交易日行情构建成功。"
        ),
        ("busy", "DATE_WRITER_ALREADY_ACTIVE"): (
            "同一日期已有构建任务运行，本次未重复启动。请等待在途任务的最终结果。"
        ),
        ("failed", "CONTAINER_RUN_FAILED"): (
            "采集或构建任务未完成。本通知没有具体故障详情，需要查看对应运行日志。"
        ),
    }
    fallback = {
        "complete": "任务已完成，并取得验收清单；具体通过范围以对应运行记录为准。",
        "partial": "任务仅部分完成，尚不能视为完整交付；具体范围请查看运行记录。",
        "closed": "任务报告休市处理完成；具体处理范围请查看运行记录。",
        "busy": "已有任务占用运行资源；最终结果请查看运行记录。",
        "failed": "任务失败，尚未确认完整交付；具体原因请查看运行日志。",
    }
    test = value["reason"].startswith("ACCEPTANCE_")
    label = status + ("·延迟" if late else "") + ("·测试" if test else "")
    subject = f"【{label}】Quanthus {value['market_date']} {role}"
    if test:
        subject += "（通道验收测试）"
    observed = datetime.fromisoformat(value["observed_at"]).astimezone(ZoneInfo("Asia/Shanghai"))
    lines = [
        f"数据日期：{value['market_date']}",
        f"处理结果：{role}{status}",
        descriptions.get((value["status"], reason), fallback[value["status"]]),
        f"记录时间：{observed:%Y-%m-%d %H:%M:%S}（北京时间）",
    ]
    if test:
        lines.insert(0, "这是邮件通道验收测试，不代表真实数据任务结果。")
    if late:
        lines.append("时效说明：本次属于延迟补建，未达到原定按时交付目标。")
    lines.extend(
        [
            "",
            "GDrive 备份由独立同步任务确认，本通知仅说明数据任务结果。",
            "",
            "排查信息（需要时使用）：",
            f"任务编号：{value['run_id']}",
            f"代码版本：{value['pipeline_revision'][:12]}",
            f"状态代码：{value['reason']}",
        ]
    )
    return subject, "\n".join(lines)


def deliver(event: Path, state: Path, secret: Path, *, attempts: int = 2) -> str:
    """API idempotency plus a durable local receipt, independent of Docker."""
    if event.is_symlink() or event.stat().st_size > 8192:
        raise ValueError("P10_MAIL_SMALL_PHYSICAL_EVENT_REQUIRED")
    value = json.loads(event.read_bytes())
    validate(value)
    if attempts not in {1, 2}:
        raise ValueError("P10_MAIL_BOUNDED_ATTEMPTS_REQUIRED")
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    key = hashlib.sha256(encoded).hexdigest()
    if state.resolve() != state.absolute():
        raise ValueError("P10_MAIL_PHYSICAL_STATE_REQUIRED")
    state.mkdir(parents=True, exist_ok=True)
    with (state / (key + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        receipt = state / (key + ".json")
        if receipt.exists() and json.loads(receipt.read_bytes()).get("status") == "sent":
            return "already_sent"
        # Runtime injection only: never bake a key into image ENV/ARG/layers.
        # Legacy file input remains readable for earlier independent send receipts.
        if "QUANTHUS_RESEND_API_KEY" in os.environ:
            settings = {
                "api_key": os.environ["QUANTHUS_RESEND_API_KEY"],
                "sender": os.environ.get("QUANTHUS_RESEND_FROM", "report@mindthus.com"),
                "recipients": [
                    v.strip()
                    for v in os.environ.get("QUANTHUS_RESEND_TO", "61718@qq.com").split(",")
                ],
            }
        else:
            settings = json.loads(secret.read_bytes())
        timeout = int(os.environ.get("QUANTHUS_RESEND_TIMEOUT_SECONDS", "15"))
        attempts = int(os.environ.get("QUANTHUS_RESEND_RETRIES", str(attempts - 1))) + 1
        if not 1 <= timeout <= 30 or attempts not in {1, 2}:
            raise ValueError("P10_MAIL_NOTIFICATION_BUDGET_INVALID")
        if (
            set(settings) != {"api_key", "sender", "recipients"}
            or not isinstance(settings["api_key"], str)
            or not settings["api_key"]
            or not isinstance(settings["sender"], str)
            or not settings["sender"]
            or not isinstance(settings["recipients"], list)
            or not 1 <= len(settings["recipients"]) <= 10
            or any(not isinstance(x, str) or "@" not in x for x in settings["recipients"])
        ):
            raise ValueError("P10_MAIL_PRIVATE_SETTINGS_INVALID")
        subject, body = render_event(value)
        payload = {
            "from": settings["sender"],
            "to": settings["recipients"],
            "subject": subject,
            "text": body,
        }
        for attempt in range(attempts):
            try:
                req = request.Request(
                    "https://api.resend.com/emails",
                    data=json.dumps(payload).encode(),
                    headers={
                        "Authorization": "Bearer " + settings["api_key"],
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "User-Agent": "Quanthus-Warehouse-Notifier/1.0",
                        "Idempotency-Key": key,
                    },
                    method="POST",
                )
                with request.urlopen(req, timeout=timeout) as response:
                    reply = json.loads(response.read(8192))
                if not isinstance(reply.get("id"), str) or not reply["id"]:
                    raise ValueError("P10_MAIL_PROVIDER_RECEIPT_MISSING")
                atomic(receipt, {"status": "sent", "event_sha256": key, "attempts": attempt + 1})
                return "sent"
            except Exception:
                # Never retain API responses, exception strings or credential contents.
                atomic(receipt, {"status": "failed", "event_sha256": key, "attempts": attempt + 1})
                if attempt + 1 < attempts:
                    time.sleep(1)
        raise RuntimeError("P10_OPERATIONAL_MAIL_FAILED_EVENT_RETAINED")


def notify(event: Path, state: Path, secret: Path) -> str:
    """Notification failure never changes the sealed data job's disposition."""
    try:
        return deliver(event, state, secret)
    except Exception:
        return "failed_event_retained"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", type=Path, required=True)
    args = parser.parse_args()
    if args.event.parent.resolve() != Path("/run/events"):
        raise ValueError("P10_MAIL_EVENT_DESTINATION_INVALID")
    result = notify(args.event, Path("/run/mail"), Path("/run/secrets/mail.json"))
    print(json.dumps({"mail": result}))
    if result == "failed_event_retained":
        raise SystemExit(2)


if __name__ == "__main__":
    main()

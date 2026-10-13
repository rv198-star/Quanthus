"""One failure email per target/stage, including retries of transport failure."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
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


def _archive_failure_target(now: datetime, state: Path) -> tuple[date, str]:
    fallback = (now.date(), "运行日期（目标交易日未确定）")
    started = _service_started_at("quanthus-gdrive-complete-daily.service")
    if started is None:
        return fallback
    paths = sorted(
        (state / "failures").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    for path in paths[:28]:
        if not started - 1 <= path.stat().st_mtime <= now.timestamp():
            continue
        try:
            payload = object_at(path)
            return date.fromisoformat(payload["market_date"]), "交易日"
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return fallback


def _daily_failure_target(now: datetime, path: Path) -> tuple[date, str]:
    fallback = (now.date(), "运行日期（目标交易日未确定）")
    service_start = _daily_service_started_at()
    if service_start is None:
        return fallback
    try:
        state = object_at(path)
        started = datetime.fromisoformat(state["started_at"])
        if (
            started.tzinfo is None
            or not service_start - 1 <= started.timestamp() <= now.timestamp()
        ):
            return fallback
        failures = [item for item in state.get("attempts", []) if item.get("returncode")]
        candidate = failures[-1]["market_date"] if failures else state.get("active_market_date")
        return (date.fromisoformat(candidate), "交易日") if candidate else fallback
    except (OSError, ValueError, TypeError, KeyError):
        return fallback


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--daily-cycle-failure", action="store_true")
    mode.add_argument("--archive-service-failure", action="store_true")
    mode.add_argument("--retry-state", action="append", type=Path)
    args = parser.parse_args()
    if args.retry_state:
        results = [retry_pending(root) for root in args.retry_state]
        print(json.dumps({"results": results}, sort_keys=True))
        return (
            2
            if any(
                item["status"] == "retry_failed"
                for result in results
                for item in result["outcomes"]
            )
            else 0
        )
    from quanthus.pipelines.p10_complete_daily_cycle import DAILY, JOURNAL
    from quanthus.pipelines.p10_complete_daily_schedule import CST

    if args.archive_service_failure:
        state = Path("/var/lib/quanthus/gdrive-complete-v2")
        target, label = _archive_failure_target(datetime.now(CST), state)
        record = failure(
            day=target,
            stage="gdrive",
            state_root=state / "notifications",
            reason="complete DWD GDrive service failed or timed out; "
            "inspect its bounded archive log",
            context={
                "market_date_label": label,
                "unit": "quanthus-gdrive-complete-daily.service",
                "error_code": "gdrive_service_failed",
            },
        )
        print(json.dumps(record, sort_keys=True))
        return 0
    target, label = _daily_failure_target(datetime.now(CST), DAILY / "operations/LATEST.json")
    record = failure(
        day=target,
        stage="daily_cycle",
        state_root=JOURNAL / "notifications",
        reason="independent daily ODS/DWD service failed; inspect its stage record; "
        "this alert does not imply both acquisition and build failed",
        context={"market_date_label": label, "error_code": "daily_cycle_service_failed"},
    )
    print(json.dumps(record, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

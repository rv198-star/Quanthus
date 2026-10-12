"""One failure email per target/stage, including retries of transport failure."""

from __future__ import annotations

import fcntl
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus import warehouse_notifications as mail
from quanthus.pipelines.p10_complete_daily_schedule import POLICY_ID
from quanthus.pipelines.p10_delivery_common import atomic, digest, encoded, object_at

EVENTS = {
    "daily_ods": "p10_cn01_daily_ods_failed",
    "minutes": "p10_cn01_native_minutes_failed",
    "complete": "p10_cn01_complete_daily_failed",
    "mirror": "p10_oci_complete_daily_failed",
}


def failure(
    *, day: date, stage: str, state_root: Path, reason: str, context: dict[str, Any] | None = None
) -> dict[str, Any]:
    event = EVENTS[stage]
    state_root.mkdir(parents=True, exist_ok=True)
    key = digest(encoded([POLICY_ID, day.isoformat(), stage]))[7:]
    path = state_root / f"{key}.json"
    details = {"policy_id": POLICY_ID, "required_intervals": ["1d", "1m", "5m"], **(context or {})}
    with (state_root / f"{key}.lock").open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        if path.exists():
            return {**object_at(path), "status": "already_sent"}
        try:
            sent = mail._send_resend(
                event=event,
                market_date=day,
                pointer=None,
                result=None,
                key=key,
                error=mail._bounded_error(reason),
                context=details,
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
        return record

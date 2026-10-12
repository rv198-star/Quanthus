"""One failure email per target/stage, including retries of transport failure."""

from __future__ import annotations

import argparse
import fcntl
import json
import subprocess
import time
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


def _daily_service_started_at() -> float | None:
    try:
        started = (
            int(
                subprocess.check_output(
                    [
                        "systemctl",
                        "show",
                        "quanthus-daily-core@cn01.service",
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
    parser.add_argument("--daily-cycle-failure", action="store_true", required=True)
    args = parser.parse_args()
    del args
    from quanthus.pipelines.p10_complete_daily_cycle import DAILY, JOURNAL
    from quanthus.pipelines.p10_complete_daily_schedule import CST

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

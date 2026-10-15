"""Failure watchdogs inspect target artifacts, rather than process exit codes."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from quanthus.pipelines.p10_complete_daily import accepted
from quanthus.pipelines.p10_complete_daily_cycle import (
    COMPLETE,
    JOURNAL,
    PRODUCT,
    RELEASES,
    _package,
    calendar_rows,
    sealed_daily_sources,
)
from quanthus.pipelines.p10_complete_daily_notifications import failure
from quanthus.pipelines.p10_complete_daily_schedule import CST, checkpoint, policy, target_for
from quanthus.pipelines.p10_native_minute_delivery import verify as verify_minute
from quanthus.platform.complete_daily_source import CompleteDailySource

OCI_ROOT = Path("/home/ubuntu/data/quanthus/dwd-complete-daily")


def evaluate(
    *,
    stage: str,
    now: datetime,
    rows: list[dict[str, Any]],
    releases: Path = RELEASES,
    product: Path = PRODUCT,
    complete: Path = COMPLETE,
    mirror_root: Path = OCI_ROOT,
) -> dict[str, Any]:
    target = target_for(now, rows, daily=stage == "daily_ods")
    if target is None or target < date.fromisoformat(policy()["baseline_market_date"]):
        return {"status": "no_eligible_trading_day"}
    deadline = checkpoint(
        target,
        {
            "daily_ods": "daily_ods_deadline",
            "minutes": "minute_deadline",
            "complete": "complete_deadline",
            "mirror": "mirror_deadline",
        }[stage],
        rows,
    )
    if now < deadline:
        return {"status": "not_yet_deadline", "market_date": str(target)}
    try:
        if stage == "daily_ods":
            sealed_daily_sources(releases, target)
        elif stage == "minutes":
            for interval in ("1m", "5m"):
                package = _package(product / interval, target)
                if package is None or verify_minute(package)["research_ready"] is not True:
                    raise ValueError("required native minute product unbuilt or R3 nonzero")
                manifest = json.loads((package / "manifest.json").read_text())
                pointer = json.loads(
                    (product / interval / "date-pointers" / f"{target}.json").read_text()
                )
                if (
                    max(
                        datetime.fromisoformat(manifest["sealed_at"]),
                        datetime.fromisoformat(pointer["first_verified_published_at"]),
                    )
                    > deadline
                ):
                    raise ValueError("required native minute product sealed after deadline")
        elif stage == "complete":
            receipt = accepted(complete, target)
            if datetime.fromisoformat(receipt["verified_at"]) > deadline:
                raise ValueError("complete Daily was verified after its deadline")
        else:
            receipt = CompleteDailySource(mirror_root).admission()
            if (
                receipt["market_date"] != str(target)
                or datetime.fromisoformat(receipt["first_verified_visible_at"]) > deadline
            ):
                raise ValueError("exact complete OCI mirror was not visible by its deadline")
    except (ValueError, OSError, KeyError):
        return {
            "status": "failed",
            "market_date": str(target),
            "stage": stage,
            "deadline": deadline.isoformat(),
            "reason": "required sealed artifact absent, wrong identity, or quality below contract",
        }
    return {"status": "passed", "market_date": str(target), "stage": stage}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage", choices=["daily_ods", "minutes", "complete", "mirror"], required=True
    )
    parser.add_argument("--notification-state", type=Path)
    parser.add_argument("--calendar-file", type=Path)
    args = parser.parse_args()
    notification_state = args.notification_state or (
        OCI_ROOT / "notifications" if args.stage == "mirror" else JOURNAL / "notifications"
    )
    now = datetime.now(CST)
    try:
        rows = (
            json.loads(args.calendar_file.read_text())
            if args.calendar_file
            else calendar_rows(
                now.date(), (OCI_ROOT if args.stage == "mirror" else JOURNAL) / "calendars"
            )
        )
        result = evaluate(stage=args.stage, now=now, rows=rows)
    except Exception as exc:
        failure(
            day=now.date(),
            stage=args.stage,
            state_root=notification_state,
            reason=f"authoritative calendar/artifact watchdog failed: {type(exc).__name__}",
            context={"market_date_label": "运行日期（目标交易日未确定）"},
        )
        raise
    if result["status"] == "failed":
        context: dict[str, Any] = {"deadline": result["deadline"]}
        if args.stage == "mirror":
            from quanthus.pipelines import p10_notification_incident as incident
            from quanthus.pipelines.p10_complete_daily_sync import CACHE

            context["upstream_incident"] = incident.recent_remote(
                OCI_ROOT / CACHE / "upstream-notifications",
                date.fromisoformat(result["market_date"]),
            )
        result["notification"] = failure(
            day=date.fromisoformat(result["market_date"]),
            stage=args.stage,
            state_root=notification_state,
            reason=result["reason"],
            context=context,
        )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] != "failed" else 2


if __name__ == "__main__":
    raise SystemExit(main())

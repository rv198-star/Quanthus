"""P10 market-date schedules and completion-notification gates (no side effects).

All timestamps are Asia/Shanghai. Eligibility depends on an authoritative
trading-calendar *list*, not weekdays, weekday-like holidays or local UTC.
This does not send messages, enable timers or make partial minute data READY.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

CONFIG_PATH = (
    Path(__file__).resolve().parents[3]
    / "config/pipelines/p10-four-node-daily-alert-schedule.v1.json"
)
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        value.get("schema_version") != 1
        or value.get("policy_id") != "p10-four-node-daily-calendar-and-alerts-v1"
        or value.get("timezone") != "Asia/Shanghai"
    ):
        raise ValueError("unexpected P10 node schedule version or timezone")
    return value


def _market_dates(rows: list[dict[str, Any]]) -> dict[date, bool]:
    result: dict[date, bool] = {}
    for row in rows:
        key = date.fromisoformat(str(row["trade_date"]))
        raw = row["is_open"]
        if str(raw) not in {"0", "1"} or key in result:
            raise ValueError("duplicate or invalid calendar market date")
        result[key] = str(raw) == "1"
    return result


def next_trade_day(target: date, rows: list[dict[str, Any]]) -> date:
    """Reject absent dates between T and T+1; never infer Monday after Friday."""
    dates = _market_dates(rows)
    for days in range(1, 16):
        possible = target + timedelta(days=days)
        if possible not in dates:
            raise ValueError("missing authoritative intermediate trading-calendar date")
        if dates[possible]:
            return possible
    raise ValueError("no verified next trading session within 15 calendar days")


def _date_time(day: date, hhmm: str) -> datetime:
    hour, minute = (int(n) for n in hhmm.split(":"))
    return datetime.combine(day, time(hour, minute), tzinfo=SHANGHAI)


def start_at(
    target: date,
    phase: str,
    rows: list[dict[str, Any]],
    *,
    policy_path: Path = CONFIG_PATH,
) -> datetime:
    policy = _config(policy_path)
    known = _market_dates(rows)
    if known.get(target) is not True:
        raise ValueError("target must be a verified exchange trading day")
    if phase == "daily_core":
        return _date_time(target, policy["schedule"]["daily_core"]["producer_first_live_cycle"])
    if phase == "minute_T":
        return _date_time(target, policy["schedule"]["minute_T"]["start_time"])
    if phase == "minute_Tplus1":
        following = next_trade_day(target, rows)
        return _date_time(following, policy["schedule"]["minute_Tplus1"]["start_time"])
    raise ValueError("unknown P10 operating phase")


def alert_checkpoint(
    target: date,
    kind: str,
    rows: list[dict[str, Any]],
    *,
    policy_path: Path = CONFIG_PATH,
) -> datetime:
    policy = _config(policy_path)
    if _market_dates(rows).get(target) is not True:
        raise ValueError("target market date not open")
    if kind == "daily_core":
        return _date_time(target, policy["schedule"]["daily_core"]["failure_email_at"])
    if kind == "minute_T":
        return _date_time(target, policy["schedule"]["minute_T"]["failure_email_at"])
    if kind == "minute_Tplus1":
        next_day = next_trade_day(target, rows)
        return _date_time(next_day, policy["schedule"]["minute_Tplus1"]["failure_email_at"])
    if kind == "oci_mirror":
        next_calendar_day = target + timedelta(days=1)
        return _date_time(
            next_calendar_day,
            policy["schedule"]["OCI_daily_core_mirror"][
                "failure_email_next_calendar_day_at"
            ],
        )
    raise ValueError("unknown P10 alert scope")


def classify_alert(
    *,
    kind: str,
    checked_at: datetime,
    checkpoint: datetime,
    receipt_status: str | None,
    processing_inflight: bool = False,
    exact_cn01_mirror: bool | None = None,
) -> str:
    """One outcome: not_due, defer, success, quality_warning or failure.

    Only a delivery transport error / missing receipt is a failure. An honest
    research NOT_READY is ONE per-day quality warning, never an R3 per-stock
    flood or a false daily-core failure.
    """
    if checked_at.tzinfo is None:
        raise ValueError("checked_at must include timezone")
    if checked_at.astimezone(SHANGHAI) < checkpoint:
        return "not_due"
    if processing_inflight:
        return "defer"
    if kind == "oci_mirror":
        return "success" if exact_cn01_mirror is True else "failure"
    if kind == "daily_core":
        return "success" if receipt_status == "READY_VERIFIED" else "failure"
    if kind in {"minute_T", "minute_Tplus1"}:
        if receipt_status in {
            "RESEARCH_READY_LOSS_AWARE",
            "GRADE_RECORDED",
            "SOURCE_UNCHANGED_STOPPED",
            "R2_RESTRICTED_RECORDED",
            "CUTOFF_WITH_HONEST_QUALITY_RECEIPT",
        }:
            return "success"
        if receipt_status in {"PARTIAL_COVERAGE_NO_RESEARCH_READY", "QUALITY_NOT_READY"}:
            return "quality_warning"
        return "failure"
    raise ValueError("unsupported P10 alert kind")

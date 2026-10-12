"""Versioned trading-day dependencies for the joint daily/1m/5m delivery."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.pipelines.p10_operating_schedule import _market_dates, next_trade_day
from quanthus.policy import find_project_root

CST = ZoneInfo("Asia/Shanghai")
POLICY_ID = "p10-trading-day-complete-daily-v1"
CONFIG = find_project_root() / "config/pipelines/p10-complete-daily.v1.json"


def policy() -> dict[str, Any]:
    value = json.loads(CONFIG.read_text())
    if (
        not isinstance(value, dict)
        or value.get("policy_id") != POLICY_ID
        or value.get("timezone") != "Asia/Shanghai"
        or value.get("required_intervals") != ["1d", "1m", "5m"]
        or value.get("minimum_minute_grade") != "R2"
        or value.get("maximum_R3") != 0
        or value.get("execution_authorized") is not False
        or value.get("operation_budget_seconds_per_interval") != 3600
        or value.get("operation_timeout_seconds") != 600
    ):
        raise ValueError("complete Daily policy identity or frozen gate differs")
    return value


def target_for(now: datetime, rows: list[dict[str, Any]], *, daily: bool = False) -> date | None:
    if now.tzinfo is None:
        raise ValueError("trading-day schedule requires timezone")
    today = now.astimezone(CST).date()
    dates = _market_dates(rows)
    if today not in dates:
        raise ValueError("today absent from authoritative trading calendar")
    if not dates[today]:
        return None
    if daily:
        return today
    for days in range(1, 16):
        candidate = today - timedelta(days=days)
        if candidate not in dates:
            raise ValueError("preceding calendar date absent; no inferred T+1")
        if dates[candidate]:
            if next_trade_day(candidate, rows) != today:
                raise ValueError("inconsistent next trading day")
            return candidate
    raise ValueError("no preceding trading day within bounded calendar")


def checkpoint(target: date, stage: str, rows: list[dict[str, Any]]) -> datetime:
    cfg = policy()
    if _market_dates(rows).get(target) is not True:
        raise ValueError("target must be an authoritative trading day")
    if stage not in {
        "minute_start",
        "minute_deadline",
        "complete_deadline",
        "mirror_deadline",
        "daily_ods_deadline",
    }:
        raise ValueError("unknown complete Daily checkpoint")
    following = target if stage == "daily_ods_deadline" else next_trade_day(target, rows)
    return datetime.combine(following, time.fromisoformat(cfg[stage]), tzinfo=CST)

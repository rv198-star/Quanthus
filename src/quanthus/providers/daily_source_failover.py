"""Bounded source recovery; saved evidence never becomes a fake refresh."""

from __future__ import annotations

import fcntl
import os
import time
import urllib.error
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypeVar
from zoneinfo import ZoneInfo

from quanthus.pipelines.p10_delivery_common import atomic, digest, encoded, object_at
from quanthus.policy import find_project_root

POLICY = "p10-daily-source-failover-v1"
CONFIG = find_project_root() / "config/pipelines/p10-daily-source-failover.v1.json"
T = TypeVar("T")


class SourceUnavailable(RuntimeError):
    """Expected availability failure, not a data qualification result."""


class BudgetExhausted(RuntimeError):
    """The shared Job budget expired; this is not a provider outage."""


def enabled() -> bool:
    value = os.environ.get("QUANTHUS_DAILY_SOURCE_FAILOVER", "")
    if value and value != POLICY:
        raise ValueError("daily source failover policy identity differs")
    return value == POLICY


def policy() -> dict[str, Any]:
    cfg = object_at(CONFIG)
    if cfg.get("policy_id") != POLICY or cfg.get("execution_authorized") is not False:
        raise ValueError("daily source failover policy identity differs")
    return cfg


def recoverable(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in {401, 403, 408, 429} or 500 <= exc.code <= 599
    if isinstance(exc, (SourceUnavailable, TimeoutError, ConnectionError, urllib.error.URLError)):
        return True
    return any(
        token in str(exc).lower()
        for token in (
            "baostock_access_denied",
            "10001011",
            "黑名单",
            "baostock login failed",
            "baostock query_trade_dates failed",
            "baostock provider readiness deadline",
            "baostock_provider_failure_circuit",
            "timed out",
            "connection reset",
        )
    )


def invoke(
    provider: str,
    scope: str,
    root: Path,
    operation: Callable[[], T],
    *,
    attempts: int = 1,
    now: datetime | None = None,
) -> T:
    """Serialize source probes; a failed provider gets a durable cooldown."""
    if provider not in {"baostock", "tencent"} or scope not in {"calendar", "daily"}:
        raise ValueError("unrecognized provider circuit identity")
    cfg = policy()
    if not 1 <= attempts <= cfg["http_attempts"]:
        raise ValueError("source retry attempts outside budget")
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{provider}-{scope}.json"
    with (root / f"{provider}-{scope}.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        stamp = now or datetime.now(timezone.utc)
        denied_path = root / f"{provider}-access-denied.json"
        if denied_path.exists():
            denied = object_at(denied_path)
            if denied.get("policy_id") != POLICY or denied.get("provider") != provider:
                raise ValueError("source denial receipt identity differs")
            if stamp < datetime.fromisoformat(denied["retry_after"]):
                raise SourceUnavailable(f"{provider.upper()}_ACCESS_CIRCUIT_OPEN")
        if path.exists():
            old = object_at(path)
            if old.get("policy_id") != POLICY or old.get("provider") != provider:
                raise ValueError("source circuit receipt identity differs")
            if old.get("retry_after") and stamp < datetime.fromisoformat(old["retry_after"]):
                raise SourceUnavailable(f"{provider.upper()}_CIRCUIT_OPEN:{scope}")
        for attempt in range(1, attempts + 1):
            try:
                value = operation()
            except Exception as exc:
                if not recoverable(exc):
                    raise
                denial = any(s in str(exc).lower() for s in ["10001011", "黑名单", "access_denied"])
                if attempt < attempts and not denial:
                    time.sleep(cfg["retry_delay_seconds"])
                    continue
                failed_at = now or datetime.now(timezone.utc)
                if denial:
                    atomic(
                        denied_path,
                        {
                            "policy_id": POLICY,
                            "provider": provider,
                            "reason": "explicit_access_denial",
                            "opened_at": failed_at.isoformat(),
                            "retry_after": (
                                failed_at + timedelta(seconds=cfg["circuit_seconds"])
                            ).isoformat(),
                            "cooldown_is_not_vendor_release_time": True,
                        },
                    )
                atomic(
                    path,
                    {
                        "policy_id": POLICY,
                        "provider": provider,
                        "scope": scope,
                        "opened_at": failed_at.isoformat(),
                        "attempts": attempt,
                        "reason": type(exc).__name__ + ":" + str(exc)[:300],
                        "retry_after": (
                            failed_at + timedelta(seconds=cfg["circuit_seconds"])
                        ).isoformat(),
                        "cooldown_is_not_vendor_release_time": True,
                    },
                )
                raise SourceUnavailable(f"{provider.upper()}_UNAVAILABLE:{scope}:{exc}") from exc
            atomic(
                path,
                {
                    "policy_id": POLICY,
                    "provider": provider,
                    "scope": scope,
                    "closed_at": (now or datetime.now(timezone.utc)).isoformat(),
                    "attempts": attempt,
                    "retry_after": None,
                },
            )
            return value
    raise AssertionError("bounded source invocation did not terminate")


def saved_calendar(
    root: Path,
    start: date,
    end: date,
    *,
    observed_day: date | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Reuse same-day authority with its original clock and exact coverage."""
    observed_day = observed_day or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    source = root / f"{observed_day}.json"
    if source.is_symlink():
        raise ValueError("saved calendar may not be a symlink")
    obj = object_at(source)
    fetched = datetime.fromisoformat(obj["fetched_at"])
    rows = obj["rows"]
    if (
        obj.get("provider") != "baostock-trading-calendar"
        or obj.get("market_date") != str(observed_day)
        or obj.get("rows_sha256") != digest(encoded(rows))
        or fetched.tzinfo is None
        or fetched > datetime.now(timezone.utc)
        or fetched.astimezone(ZoneInfo("Asia/Shanghai")).date() != observed_day
    ):
        raise ValueError("saved calendar authority/clock/digest differs")
    flags = {}
    for row in rows:
        day = date.fromisoformat(row["trade_date"])
        if day in flags or str(row["is_open"]) not in {"0", "1"}:
            raise ValueError("saved calendar duplicate or invalid state")
        flags[day] = str(row["is_open"])
    day = start
    selected = []
    while day <= end:
        if day not in flags:
            raise SourceUnavailable("SAVED_CALENDAR_TARGET_COVERAGE_MISSING")
        selected.append({"trade_date": str(day), "is_open": flags[day]})
        day += timedelta(days=1)
    return selected, {
        "source": "verified_same_day_authoritative_calendar_reuse",
        "authority": obj["provider"],
        "original_fetched_at": obj["fetched_at"],
        "source_path": str(source),
        "source_sha256": digest(source.read_bytes()),
        "new_source_requests": 0,
        "is_new_refresh": False,
    }


def calendar_rows(
    start: date,
    end: date,
    *,
    fetch: Callable[[date, date], list[dict[str, Any]]],
    calendar_root: Path,
    circuit_root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        rows = invoke("baostock", "calendar", circuit_root, lambda: fetch(start, end))
        return rows, {"source": "baostock.query_trade_dates", "is_new_refresh": True}
    except SourceUnavailable:
        try:
            return saved_calendar(calendar_root, start, end)
        except (FileNotFoundError, SourceUnavailable):
            # A sealed observation of the now-closed target day remains evidence
            # for that day. It never establishes today's or a future session.
            today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
            if end >= today:
                raise SourceUnavailable("SAVED_CALENDAR_TARGET_AUTHORITY_MISSING") from None
            return saved_calendar(calendar_root, start, end, observed_day=end)

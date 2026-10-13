"""Automatic capture-end reconciliation and independent weekly DWD continuation."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.pipelines.contracts import verify_ods_release
from quanthus.pipelines.p10_complete_daily_notifications import failure
from quanthus.pipelines.p10_delivery_common import (
    atomic,
    file_digest,
    object_at,
    require_revision,
    safe_path,
)
from quanthus.pipelines.p10_domain_continuation import build

STATE = Path("/var/lib/quanthus/acquisition-continuation-v1")
CST = ZoneInfo("Asia/Shanghai")


def latest_event(releases: Path, day: date) -> Path:
    candidates = []
    for path in sorted((releases / "daily" / day.isoformat()).glob("calendar-daily-events-*")):
        try:
            item = verify_ods_release(path)
        except (OSError, ValueError, RuntimeError):
            continue
        candidates.append((item.release.created_at, path))
    if not candidates:
        raise ValueError("QUALIFIED_EVENT_SOURCE_MISSING")
    return max(candidates)[1]


def latest_financial_attempt(shadow: Path, day: date) -> Path:
    for path in sorted(
        (shadow / "attempts/weekly-corrections" / day.isoformat()).glob("*/landing"), reverse=True
    ):
        if all(
            (path / name).is_file()
            for name in (
                "financial_statements.parquet",
                "financial_statements.parquet.receipts.parquet",
            )
        ):
            datasets = object_at(path.parent / "datasets.json")["datasets"]
            states = {item["name"]: item["status"] for item in datasets}
            if states.get("financial_statements") == "ready":
                return path
    raise ValueError("CURRENT_WEEK_FINANCIAL_SOURCE_MISSING")


def run(*, mode: str, day: date, revision: str, state: Path = STATE) -> dict[str, Any]:
    require_revision(revision)
    home = Path(os.environ.get("ASTOCK_HOME", "/root/work/astock/data"))
    shadow = Path(os.environ.get("HERMES_ODS_SHADOW_ROOT", str(home / "shadow/cn01-ods-v1")))
    core = Path(
        os.environ.get(
            "QUANTHUS_DWD_DAILY_CORE_ROOT", "/srv/quanthus/dwd-dws-v2/daily/products/daily-core"
        )
    )
    products = Path(
        os.environ.get(
            "QUANTHUS_DOMAIN_CONTINUATION_ROOT",
            "/srv/quanthus/dwd-dws-v2/daily/products/domain-continuation-v1",
        )
    )
    base = Path(os.environ["QUANTHUS_DWD_BASE"])
    config = Path(os.environ["QUANTHUS_V2_RELEASE_CONFIG"])
    state.mkdir(parents=True, exist_ok=True)
    with (state / (mode + ".lock")).open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        result: dict[str, Any] = {
            "mode": mode,
            "run_date": day.isoformat(),
            "code_revision": revision,
            "domains": {},
        }
        try:
            if mode == "natural-day":
                # This job supplies closed-date announcements.
                import pyarrow.parquet as pq

                calendar = (
                    pq.ParquetFile(Path(os.environ["HERMES_ODS_CALENDAR"]))
                    .read(columns=["exchange", "trade_date", "is_open"])
                    .to_pylist()
                )
                flags = [
                    row
                    for row in calendar
                    if row["exchange"] in {"SSE", "SZSE"}
                    and str(row["trade_date"])[:10] == day.isoformat()
                ]
                if len(flags) != 2 or len({int(row["is_open"]) for row in flags}) != 1:
                    raise ValueError("NATURAL_DAY_CALENDAR_AUTHORITY_MISSING")
                if int(flags[0]["is_open"]) == 1:
                    return {**result, "status": "handled_by_trading_day_daily_core"}
                source_days = [
                    str(row["trade_date"])[:10]
                    for row in calendar
                    if row["exchange"] == "SSE"
                    and int(row["is_open"]) == 1
                    and str(row["trade_date"])[:10] < day.isoformat()
                ]
                if not source_days:
                    raise ValueError("NATURAL_DAY_UNIVERSE_SOURCE_DATE_MISSING")
                env = {**os.environ, "HERMES_ODS_UNIVERSE_SOURCE_DATE": max(source_days)}
                root = Path(__file__).resolve().parents[3]
                command = [
                    str(root / "ops/bin/run-hermes-ods-lane"),
                    "--lane",
                    "calendar-daily-events",
                    "--market-date",
                    day.isoformat(),
                ]
                subprocess.run(
                    command, env=env, check=True, timeout=3600, stdout=subprocess.DEVNULL
                )
            if mode in {"daily", "natural-day"}:
                if mode == "daily":
                    pointer = object_at(core / "CURRENT_DAILY_CORE.json")
                    day = date.fromisoformat(pointer["market_date"])
                    if (
                        file_digest(safe_path(core, pointer["manifest_path"]))
                        != pointer["manifest_sha256"]
                    ):
                        raise ValueError("DAILY_POINTER_MANIFEST_DIFFERS")
                    cat = object_at(core / "catalogs" / day.isoformat() / "catalog.json")
                    event = next(
                        item for item in cat["lanes"] if item["lane"] == "calendar_daily_events"
                    )
                    release = safe_path(shadow / "releases", event["release_path"])
                    if (
                        verify_ods_release(release).manifest_sha256
                        != event["release_manifest_sha256"]
                    ):
                        raise ValueError("DAILY_EVENT_SOURCE_CHANGED")
                else:
                    release = latest_event(shadow / "releases", day)
                item = build(
                    domain="disclosure",
                    source=release / "artifacts/announcements_delta.parquet",
                    receipts=None,
                    core=core,
                    products=products,
                    base=base,
                    config=config,
                    day=day,
                    code_revision=revision,
                )
                result["domains"]["disclosure"] = {
                    key: item[key] for key in ("manifest_sha256", "content_digest", "audit")
                }
                quality_releases = [release]
                if mode == "daily":
                    for lane in cat["lanes"]:
                        candidate = safe_path(shadow / "releases", lane["release_path"])
                        if candidate != release:
                            if (
                                verify_ods_release(candidate).manifest_sha256
                                != lane["release_manifest_sha256"]
                            ):
                                raise ValueError("daily quality source changed")
                            quality_releases.append(candidate)
                unknown: list[dict[str, Any]] = []
                for quality_release in quality_releases:
                    if (quality_release / "artifacts/acquisition_quality.json").exists():
                        quality = object_at(quality_release / "artifacts/acquisition_quality.json")
                        unknown.extend(
                            entry
                            for entry in quality["datasets"]
                            if entry.get("current_refresh_proven") is False
                        )
                if unknown:
                    result["excluded_domains"] = sorted({entry["dataset"] for entry in unknown})
                    failure(
                        day=day,
                        stage="acquisition_quality",
                        state_root=state / "notifications",
                        reason=(
                            "SOURCE_NOT_REFRESHED: affected domains remain excluded; "
                            "qualified daily core continues"
                        ),
                        context={
                            "blocked_domains": result["excluded_domains"],
                            "daily_core_blocked": False,
                            "market_date_label": "自然日公告日期"
                            if mode == "natural-day"
                            else "交易日",
                        },
                    )
            if mode == "weekly":
                source = latest_financial_attempt(shadow, day)
                item = build(
                    domain="financial",
                    source=source / "financial_statements.parquet",
                    receipts=source / "financial_statements.parquet.receipts.parquet",
                    core=core,
                    products=products,
                    base=base,
                    config=config,
                    day=day,
                    code_revision=revision,
                )
                result["domains"]["financial"] = {
                    key: item[key] for key in ("manifest_sha256", "content_digest", "audit")
                }
                weekly_states = object_at(source.parent / "datasets.json")["datasets"]
                result["unavailable_domains"] = [
                    item["name"] for item in weekly_states if item["status"] != "ready"
                ]
                # Financial admission does not grant F10/shareholder coverage.
                result["consumer_excluded_domains"] = ["F10", "shareholder_complete_matrix"]
                if result["unavailable_domains"]:
                    failure(
                        day=day,
                        stage="weekly_source",
                        state_root=state / "notifications",
                        reason=(
                            "qualified financial DWD published; "
                            "some weekly sources remain unavailable; "
                            "affected uses held"
                        ),
                        context={
                            "market_date_label": "周频来源日期",
                            "financial_continuation_passed": True,
                            "unavailable_domains": result["unavailable_domains"],
                        },
                    )
            if os.environ.get("QUANTHUS_REVISE_ACCEPTED_COMPLETE") == "1":
                from quanthus.pipelines.p10_complete_daily import build as revise_complete

                complete = Path(
                    os.environ.get(
                        "QUANTHUS_COMPLETE_DAILY_ROOT",
                        "/srv/quanthus/dwd-dws-v2/daily/products/complete-daily",
                    )
                )
                complete_pointer = complete / "CURRENT_COMPLETE_DAILY.json"
                if complete_pointer.exists():
                    pointer = object_at(complete_pointer)
                    if pointer["market_date"] >= day.isoformat():
                        package = safe_path(complete, pointer["manifest_path"]).parent
                        result["complete_revision"] = revise_complete(
                            daily=package / "members/1d",
                            m1=package / "members/1m",
                            m5=package / "members/5m",
                            output_root=complete,
                            code_revision=revision,
                        )
            result["status"] = "qualified_domains_published_with_explicit_limitations"
        except Exception as exc:
            result["status"] = "failed"
            result["error_class"] = type(exc).__name__
            atomic(state / "runs" / mode / (day.isoformat() + ".json"), result)
            failure(
                day=day,
                stage="weekly"
                if mode == "weekly"
                else ("natural_day" if mode == "natural-day" else "domain_daily"),
                state_root=state / "notifications",
                reason="automatic continuation failed; inspect preserved stage evidence",
                context={
                    "error_class": type(exc).__name__,
                    "mode": mode,
                    "market_date_label": "业务日期",
                },
            )
            raise
        atomic(state / "runs" / mode / (day.isoformat() + ".json"), result)
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("daily", "weekly", "natural-day"), required=True)
    parser.add_argument("--day", type=date.fromisoformat)
    parser.add_argument("--code-revision", required=True)
    args = parser.parse_args()
    day = args.day or datetime.now(CST).date()
    if args.mode == "weekly" and args.day is None:
        day -= timedelta(days=(day.weekday() - 5) % 7)
    print(json.dumps(run(mode=args.mode, day=day, revision=args.code_revision), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

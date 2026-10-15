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
from quanthus.pipelines.p10_complete_daily_cycle import JOURNAL, calendar_rows
from quanthus.pipelines.p10_complete_daily_notifications import (
    SOURCE_GAP_REASON,
    failure,
    source_gap_context,
)
from quanthus.pipelines.p10_delivery_common import (
    atomic,
    file_digest,
    object_at,
    require_revision,
    safe_path,
)
from quanthus.pipelines.p10_domain_continuation import build
from quanthus.pipelines.p10_operating_schedule import _market_dates

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
        active = {
            "mode": mode,
            "market_date": day.isoformat(),
            "started_at": datetime.now(CST).isoformat(),
            "code_revision": revision,
            "invocation_id": os.environ.get("INVOCATION_ID"),
        }
        atomic(state / "active" / (mode + ".json"), active)
        try:
            if mode == "natural-day":
                # Operating eligibility needs the same authoritative current-date
                # calendar as the daily/T+1 controllers, not sealed Base rows.
                calendar = calendar_rows(day, JOURNAL / "calendars")
                saved = object_at(JOURNAL / "calendars" / f"{day}.json")
                acquired = datetime.fromisoformat(saved["fetched_at"])
                if (
                    saved.get("provider") != "baostock-trading-calendar"
                    or acquired.tzinfo is None
                    or acquired > datetime.now(CST)
                ):
                    raise ValueError("NATURAL_DAY_CALENDAR_AUTHORITY_MISSING")
                flags = _market_dates(calendar)
                if day not in flags:
                    raise ValueError("NATURAL_DAY_CALENDAR_AUTHORITY_MISSING")
                result["calendar_authority"] = {
                    "provider": saved["provider"],
                    "fetched_at": saved["fetched_at"],
                    "rows_sha256": saved["rows_sha256"],
                    "market_date": saved["market_date"],
                }
                if flags[day]:
                    result["status"] = "handled_by_trading_day_daily_core"
                    atomic(state / "runs" / mode / (day.isoformat() + ".json"), result)
                    return result
                source_day = None
                for offset in range(1, 16):
                    previous = day - timedelta(days=offset)
                    if previous not in flags:
                        raise ValueError("NATURAL_DAY_INTERMEDIATE_CALENDAR_DATE_MISSING")
                    if flags[previous]:
                        source_day = previous
                        break
                if source_day is None:
                    raise ValueError("NATURAL_DAY_UNIVERSE_SOURCE_DATE_MISSING")
                env = {**os.environ, "HERMES_ODS_UNIVERSE_SOURCE_DATE": str(source_day)}
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
                    atomic(
                        state / "active" / (mode + ".json"),
                        {
                            **active,
                            "market_date": day.isoformat(),
                        },
                    )
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
                        reason=SOURCE_GAP_REASON,
                        context={
                            **source_gap_context(unknown),
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

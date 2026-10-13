"""CN01 shared-reference Job: T+1 native minutes -> qualified complete DWD.

Each interval owns its persistent 60-minute budget and <=600s operations.
Timers may resume the Job; they cannot reset budget or overwrite originals.
"""

from __future__ import annotations

import argparse
import fcntl
import gzip
import json
import math
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast

from quanthus.pipelines.contracts import verify_ods_release
from quanthus.pipelines.p10_complete_daily import accepted
from quanthus.pipelines.p10_complete_daily_notifications import failure
from quanthus.pipelines.p10_complete_daily_schedule import (
    CST,
    POLICY_ID,
    checkpoint,
    policy,
    target_for,
)
from quanthus.pipelines.p10_delivery_common import (
    atomic,
    file_digest,
    object_at,
    safe_path,
)
from quanthus.pipelines.p10_minute_daily_cycle import calendar_for, find_market_ods
from quanthus.pipelines.p10_minute_operation_runtime import run_one
from quanthus.pipelines.p10_native_minute_delivery import (
    freeze_reference,
    native_m5,
    reference,
    source_index,
    verify,
)
from quanthus.pipelines.securities_warehouse.daily_core_domain import verify_daily_domain_increment
from quanthus.pipelines.securities_warehouse.daily_core_ods_catalog import _select_lane
from quanthus.pipelines.securities_warehouse.daily_domain import DailyDomainBuildError
from quanthus.providers.p10_native_m1_shard_capture import target_date_records

ROOT = Path("/srv/quanthus/dwd-dws-v2")
JOURNAL = ROOT / "intraday/operations/complete-daily-v1"
CAPTURE = ROOT / "intraday/quarantine/complete-daily-v1"
PRODUCT = ROOT / "intraday/products/native-minute-delivery-v1"
COMPLETE = ROOT / "daily/products/complete-daily"
DAILY = ROOT / "daily/products/daily-core"
RELEASES = Path("/root/work/astock/data/shadow/cn01-ods-v1/releases/daily")


def sealed_daily_sources(
    releases: Path, target: date, *, daily: Path = DAILY
) -> tuple[Path, str]:
    dated = daily / "date-pointers" / f"{target}.json"
    if dated.exists():
        pointer = object_at(dated)
        package = safe_path(daily, pointer["manifest_path"]).parent
        if (
            pointer.get("kind") != "cn01_daily_core_dwd_current"
            or pointer.get("market_date") != str(target)
            or file_digest(package / "manifest.json") != pointer.get("manifest_sha256")
        ):
            raise ValueError("dated daily source binding identity differs")
        try:
            checked_daily = verify_daily_domain_increment(package)
        except DailyDomainBuildError as exc:
            raise ValueError(f"dated daily binding invalid: {exc}") from exc
        manifest = object_at(package / "manifest.json")
        if (
            manifest.get("kind") != "cn01_daily_core_dwd_increment"
            or manifest["market_date"] != str(target)
            or checked_daily.package_id != pointer.get("package_id")
            or checked_daily.content_digest != pointer.get("content_digest")
        ):
            raise ValueError("dated daily source binding date or content identity differs")
        selected = manifest["selected_releases"]
        sources = {}
        for lane, prefix in (
            ("market_daily_core", "hermes-market-daily-core-"),
            ("calendar_daily_events", "hermes-calendar-daily-events-"),
        ):
            expected = selected[lane]
            matches = []
            for marker in sorted((releases / str(target)).glob("*/release.json")):
                if marker.is_symlink() or marker.parent.is_symlink():
                    continue
                if file_digest(marker) != expected["manifest_sha256"]:
                    continue
                checked = verify_ods_release(marker.parent)
                release = checked.release
                if (
                    checked.manifest_sha256 == expected["manifest_sha256"]
                    and release.release_id == expected["release_id"]
                    and release.release_id.startswith(prefix)
                    and release.market_date == target
                    and release.schedule == "daily"
                ):
                    matches.append(marker.parent)
            if len(matches) != 1:
                raise ValueError("exact daily-bound ODS source absent or ambiguous")
            sources[lane] = matches[0]
        return sources["market_daily_core"], selected["calendar_daily_events"]["manifest_sha256"]
    # Preserve legacy single-source admission when no dated DWD binding exists.
    market = find_market_ods(releases, target)
    events = _select_lane(releases.parent, target, "calendar_daily_events")
    return market, events.manifest_sha256


def calendar_rows(today: date, cache: Path) -> list[dict[str, Any]]:
    """Reuse a same-day authoritative calendar, rather than refetch per mirror poll."""
    from quanthus.pipelines.p10_delivery_common import digest, encoded

    path = cache / f"{today}.json"
    if path.exists():
        saved = object_at(path)
        if saved.get("market_date") != str(today) or saved.get("rows_sha256") != digest(
            encoded(saved["rows"])
        ):
            raise ValueError("same-day authoritative calendar cache identity differs")
        return cast(list[dict[str, Any]], saved["rows"])
    rows = calendar_for(today)
    atomic(
        path,
        {
            "market_date": str(today),
            "rows": rows,
            "rows_sha256": digest(encoded(rows)),
            "fetched_at": datetime.now(CST).isoformat(),
            "provider": "baostock-trading-calendar",
        },
    )
    return rows


def _operation(
    *, journal: Path, calendar: Path, target: date, operation: str, command: list[str]
) -> dict[str, Any]:
    if ("capture" in operation or "canary" in operation) and datetime.now(CST) >= checkpoint(
        target, "minute_deadline", json.loads(calendar.read_text())
    ):
        return {"status": "cutoff_reached", "reason": "new_source_operation_after_13:00"}
    return run_one(
        state_root=journal,
        calendar_file=calendar,
        market_date=target,
        phase="T_plus_1",
        operation_id=operation,
        argv=command,
        timeout_seconds=600,
        policy_id=POLICY_ID,
    )


def _budget(journal: Path, target: date) -> float:
    path = journal / f"{target}-T_plus_1.json"
    return float(object_at(path)["cumulative_processing_seconds"]) if path.exists() else 0.0


def _package(product: Path, target: date) -> Path | None:
    path = product / "date-pointers" / f"{target}.json"
    if not path.exists():
        return None
    pointer = object_at(path)
    package = Path(pointer["package_path"])
    if not package.resolve().is_relative_to(product.resolve()):
        raise ValueError("minute date pointer escapes its product root")
    actual = verify(package)
    if pointer["manifest_sha256"] != actual["manifest_sha256"]:
        raise ValueError("minute date pointer identity differs")
    return package


def run_interval(
    *, interval: str, target: date, job: Path, capture: Path, product: Path, revision: str
) -> dict[str, Any]:
    product = product / interval
    journal = job / interval
    journal.mkdir(parents=True, exist_ok=True)
    ref_file = job / "daily-reference.json"
    ref = reference(ref_file)
    calendar = job / "calendar.json"
    original = capture / str(target) / interval / "initial-v1"
    previous = _package(product, target)
    package = previous
    root = original
    codes = sorted(ref["securities"])
    attempt = "initial"
    cohort_file = None
    if previous is not None:
        result = verify(previous)
        if result["research_ready"]:
            return result
        if (journal / f"{target}-T_plus_1.json").exists():
            ledger = object_at(journal / f"{target}-T_plus_1.json")
            if any(op["operation_id"] == "repair-grade" for op in ledger["operations"]):
                return {**result, "repair_disposition": "one_bounded_repair_completed_stop"}
        # One bounded canary detects target-date repair; never poll the market again
        # merely because the rolling HTTP envelope changed outside T.
        quality = [
            json.loads(line)
            for line in (previous / "quality-status.jsonl").read_text().splitlines()
        ]
        codes = sorted(row["stock_code"] for row in quality if row["quality_grade"] == "R3")
        if not codes or _budget(journal, target) >= 3000:
            return result
        canary_codes = [
            codes[min(len(codes) - 1, i * len(codes) // min(24, len(codes)))]
            for i in range(min(24, len(codes)))
        ]
        slot = "1130" if datetime.now(CST).hour < 12 else "1230"
        canary_file = job / f"{interval}-canary-{slot}.json"
        atomic(canary_file, canary_codes)
        canary_root = capture / str(target) / interval / f"repair-canary-{slot}-v1"
        command = [
            sys.executable,
            "-m",
            "quanthus.pipelines.p10_native_minute_delivery",
            "capture",
            "--reference",
            str(ref_file),
            "--interval",
            interval,
            "--output",
            str(canary_root / "shard-000"),
            "--shard-index",
            "0",
            "--codes-file",
            str(canary_file),
        ]
        response = _operation(
            journal=journal,
            calendar=calendar,
            target=target,
            operation=f"repair-canary-{slot}",
            command=command,
        )
        if response.get("receipt", {}).get("exit_code") != 0:
            return result
        before = source_index(original, ref, interval)
        after = source_index(canary_root, ref, interval)
        changed = False
        for code, (path, receipt) in after.items():
            if not receipt:
                continue
            if code not in before or not before[code][1]:
                changed = True
                break
            first = gzip.decompress(before[code][0].read_bytes())
            second = gzip.decompress(path.read_bytes())
            if interval == "1m":
                changed = (
                    target_date_records(first, code, target)[1]
                    != target_date_records(second, code, target)[1]
                )
            else:
                changed = native_m5(first, code, str(target)) != native_m5(
                    second, code, str(target)
                )
            if changed:
                break
        if not changed:
            return {**result, "repair_disposition": "target_date_canary_unchanged_stop"}
        root = capture / str(target) / interval / "repair-v1"
        cohort_file = job / f"{interval}-repair-cohort.json"
        atomic(cohort_file, codes)
        attempt = "repair"
    attempts = []
    for index in range(math.ceil(len(codes) / 50)):
        if _budget(journal, target) >= 3000 or datetime.now(CST) >= datetime.combine(
            datetime.now(CST).date(),
            datetime.strptime(policy()["minute_deadline"], "%H:%M").time(),
            CST,
        ):
            break
        command = [
            sys.executable,
            "-m",
            "quanthus.pipelines.p10_native_minute_delivery",
            "capture",
            "--reference",
            str(ref_file),
            "--interval",
            interval,
            "--output",
            str(root / f"shard-{index:03}"),
            "--shard-index",
            str(index),
        ]
        if cohort_file:
            command += ["--codes-file", str(cohort_file)]
        response = _operation(
            journal=journal,
            calendar=calendar,
            target=target,
            operation=f"{attempt}-capture-{index:03}",
            command=command,
        )
        attempts.append(response["status"])
        if response["status"] == "cutoff_reached":
            break
    command = [
        sys.executable,
        "-m",
        "quanthus.pipelines.p10_native_minute_delivery",
        "build",
        "--reference",
        str(ref_file),
        "--interval",
        interval,
        "--capture-root",
        str(root),
        "--output",
        str(product),
        "--code-revision",
        revision,
    ]
    if attempt == "repair":
        command += ["--prior-capture-root", str(original)]
    response = _operation(
        journal=journal,
        calendar=calendar,
        target=target,
        operation=f"{attempt}-grade",
        command=command,
    )
    package = _package(product, target)
    if response.get("receipt", {}).get("exit_code") != 0 or package is None:
        return {
            "market_date": str(target),
            "interval": interval,
            "research_ready": False,
            "status": "native_capture_or_grade_failed",
            "attempts": attempts,
            "operation_status": response["status"],
        }
    return verify(package)


def daily_package(root: Path, target: date) -> Path:
    dated = root / "date-pointers" / f"{target}.json"
    pointer = object_at(dated if dated.exists() else root / "CURRENT_DAILY_CORE.json")
    if pointer.get("market_date") != str(target):
        raise ValueError("target daily DWD not yet available")
    return safe_path(root, pointer["manifest_path"]).parent


def run(
    *,
    now: datetime,
    rows: list[dict[str, Any]],
    revision: str,
    journal: Path = JOURNAL,
    capture: Path = CAPTURE,
    product: Path = PRODUCT,
    complete: Path = COMPLETE,
    daily: Path = DAILY,
    releases: Path = RELEASES,
    notifications: Path = JOURNAL / "notifications",
) -> dict[str, Any]:
    target = target_for(now, rows)
    if target is None or target < date.fromisoformat(policy()["baseline_market_date"]):
        return {"status": "no_eligible_trading_day"}
    if now < checkpoint(target, "minute_start", rows):
        return {"status": "not_yet_minute_start", "market_date": str(target)}
    job = journal / str(target)
    job.mkdir(parents=True, exist_ok=True)
    with (job / ".job.lock").open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (complete / "date-pointers" / f"{target}.json").exists():
            evidence = accepted(complete, target)
            if os.environ.get("HERMES_ODS_PREVENTION_POLICY") == "p10-acquisition-prevention-v1":
                from quanthus.pipelines.p10_complete_daily import build as revise_complete

                pointer = object_at(complete / "date-pointers" / f"{target}.json")
                package = safe_path(complete, pointer["manifest_path"]).parent
                evidence = revise_complete(
                    daily=package / "members/1d",
                    m1=package / "members/1m",
                    m5=package / "members/5m",
                    output_root=complete,
                    code_revision=revision,
                )
            elif evidence["code_revision"] != revision:
                raise ValueError("accepted complete Daily code differs; explicit revision required")
            return {
                "status": "complete_daily_published",
                "market_date": str(target),
                "complete": evidence,
                "disposition": "reuse_unchanged_verified_identity",
            }
        ref_file = job / "daily-reference.json"
        if not ref_file.exists():
            try:
                ods, events_sha = sealed_daily_sources(releases, target, daily=daily)
                freeze_reference(ods, str(target), ref_file, events_sha=events_sha)
                atomic(job / "calendar.json", rows)
                atomic(job / "code.json", {"code_revision": revision, "policy_id": POLICY_ID})
            except (ValueError, OSError, KeyError) as exc:
                result: dict[str, Any] = {
                    "status": "waiting_for_sealed_daily_ods",
                    "market_date": str(target),
                    "error_class": type(exc).__name__,
                }
                atomic(job / "status.json", result)
                failure(
                    day=target,
                    stage="daily_ods",
                    state_root=notifications,
                    reason="T daily ODS still not sealed at next trading-day minute start",
                )
                if now >= checkpoint(target, "minute_deadline", rows):
                    failure(
                        day=target,
                        stage="minutes",
                        state_root=notifications,
                        reason="sealed target daily ODS prerequisite absent at minute deadline",
                    )
                return result
        elif object_at(job / "code.json") != {"code_revision": revision, "policy_id": POLICY_ID}:
            raise ValueError(
                "persisted Job code identity changed; preserve budget and reconcile explicitly"
            )
        if shutil_free(journal) < 4 * 1024**3:
            raise ValueError("capture requires 4 GiB node disk reserve")
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {
                interval: pool.submit(
                    run_interval,
                    interval=interval,
                    target=target,
                    job=job,
                    capture=capture,
                    product=product,
                    revision=revision,
                )
                for interval in ("1m", "5m")
            }
            minutes = {}
            for interval, future in futures.items():
                try:
                    minutes[interval] = future.result()
                except Exception as exc:
                    minutes[interval] = {"research_ready": False, "error_class": type(exc).__name__}
        qualified = all(value.get("research_ready") is True for value in minutes.values())
        result = {
            "market_date": str(target),
            "policy_id": POLICY_ID,
            "minutes": minutes,
            "status": "minutes_qualified" if qualified else "minutes_not_qualified",
        }
        atomic(job / "status.json", result)
        if not qualified:
            failure(
                day=target,
                stage="minutes",
                state_root=notifications,
                reason="native 1m/5m did not both reach R2 minimum with R3=0; "
                "joint publication held",
                context={
                    "quality_distribution": {
                        key: value.get("quality_grades", "unbuilt")
                        for key, value in minutes.items()
                    }
                },
            )
            return result
        try:
            source = daily_package(daily, target)
            # One separately bounded composite build; minute histories are shared sealed ADM.
            command = [
                sys.executable,
                "-m",
                "quanthus.pipelines.p10_complete_daily",
                "--daily",
                str(source),
                "--m1",
                minutes["1m"]["package_path"],
                "--m5",
                minutes["5m"]["package_path"],
                "--output-root",
                str(complete),
                "--code-revision",
                revision,
            ]
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=600, check=False
            )
            # Keep file/line diagnostics; exception messages may contain source values.
            atomic(
                job / "complete-build-check.json",
                {
                    "code_revision": revision,
                    "exit_code": completed.returncode,
                    "stderr_frames": [
                        line.strip()
                        for line in completed.stderr.splitlines()
                        if line.lstrip().startswith('File "')
                    ][-20:],
                    "failure_class": (
                        completed.stderr.splitlines()[-1].split(":", 1)[0]
                        if completed.stderr.splitlines()
                        else None
                    ),
                },
            )
            if completed.returncode:
                raise ValueError("complete DWD projection failed; inspect bounded Job log")
            result["complete"] = json.loads(completed.stdout.splitlines()[-1])
            result["status"] = "complete_daily_published"
        except Exception as exc:
            result["status"] = "complete_daily_failed"
            result["error_class"] = type(exc).__name__
            failure(
                day=target,
                stage="complete",
                state_root=notifications,
                reason="native minutes qualified but complete DWD build "
                "or daily DWD dependency failed",
            )
        atomic(job / "status.json", result)
        return result


def shutil_free(path: Path) -> int:
    import shutil

    return shutil.disk_usage(path).free


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code-revision", required=True)
    parser.add_argument("--calendar-file", type=Path)
    args = parser.parse_args()
    now = datetime.now(CST)
    target = None
    try:
        rows = (
            json.loads(args.calendar_file.read_text())
            if args.calendar_file
            else calendar_rows(now.date(), JOURNAL / "calendars")
        )
        target = target_for(now, rows)
        result = run(now=now, rows=rows, revision=args.code_revision)
    except Exception as exc:
        failure(
            day=target or now.date(),
            stage="minutes",
            state_root=JOURNAL / "notifications",
            reason=f"complete Daily controller failed: {type(exc).__name__}",
            context={
                "error_code": "controller_or_calendar_failed",
                "market_date_label": "交易日" if target else "运行日期（目标交易日未确定）",
            },
        )
        raise
    print(json.dumps(result, sort_keys=True))
    return (
        0
        if result["status"]
        in {
            "complete_daily_published",
            "no_eligible_trading_day",
            "not_yet_minute_start",
            "waiting_for_sealed_daily_ods",
        }
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())

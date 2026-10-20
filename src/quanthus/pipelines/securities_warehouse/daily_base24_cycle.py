"""CN02 source acquisition and qualified Base 2.4 Daily publication.

Shared prerequisites: one source universe per date, one immutable daily reference,
one catalog/core, then one physical composite. Weekly and 1m are not predecessors.
Failed attempts are retained; retries use a new run. Date pointers only name fully
verified outputs. A held 5m scope never suppresses an independently qualified core.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import fcntl
import json
import math
import os
import signal
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import canonical, file_sha
from .base22_sources import disjoint_output, physical_path
from .daily_base24 import build, verify
from .daily_base24_job import WINDOW
from .daily_base24_provider import COLLECTOR
from .daily_core_domain import build_daily_domain_increment, verify_daily_domain_increment
from .daily_core_ods_catalog import DAILY_CORE_PRODUCT_SCOPE, build_daily_ods_catalog

CST = ZoneInfo("Asia/Shanghai")
KIND = "cn02_base24_daily_cycle_v1"


def object_at(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("DAILY_CYCLE_OBJECT_REQUIRED")
    return value


def write(path: Path, value: Any) -> None:
    with path.open("xb") as stream:
        stream.write(canonical(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def atomic(path: Path, value: Any) -> None:
    temporary = path.with_name("." + path.name + "." + str(os.getpid()))
    write(temporary, value)
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def time_gate(day: date, stage: str, recovery: bool, now: datetime | None = None) -> None:
    local = (now or datetime.now(timezone.utc)).astimezone(CST)
    if day > local.date():
        raise ValueError("DAILY_CYCLE_FUTURE_DATE")
    if day < local.date() or recovery:
        return
    clock = local.strftime("%H:%M:%S")
    stop = str(
        WINDOW["sealed_input_deadline"] if stage == "intake" else WINDOW["delivery_deadline"]
    )
    if clock < str(WINDOW["start_time"]) or clock >= stop:
        raise TimeoutError("DAILY_CYCLE_T_DAY_" + stage.upper() + "_WINDOW")


def alert(config: dict[str, Any], run: Path, day: date, reason: str) -> None:
    root = Path(config["root"]) / "alerts"
    root.mkdir(exist_ok=True)
    write(
        root / (run.name + ".json"),
        {
            "market_date": str(day),
            "reason": reason,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "run": str(run),
            "delivery_deadline": "21:00 Asia/Shanghai",
            "affected_scope": "Daily acquisition/build",
            "protected_values_exported": False,
            "execution_authorized": False,
        },
    )


def load(path: Path) -> dict[str, Any]:
    config = object_at(physical_path(path))
    if (
        config.get("kind") != KIND
        or config.get("node") != "cn02"
        or config.get("collector_revision") != COLLECTOR
        or config.get("intervals") != ["1d", "5m"]
        or config.get("execution_authorized") is not False
        or config.get("consumer_admission") != "NOT_GRANTED"
        or config.get("native_workers") not in {1, 2}
    ):
        raise ValueError("DAILY_CYCLE_CONFIGURATION_DIFFERS")
    import re

    if not re.fullmatch("[0-9a-f]{40}", str(config.get("pipeline_revision", ""))):
        raise ValueError("DAILY_CYCLE_FULL_REVISION_REQUIRED")
    sources = [physical_path(Path(config[k])) for k in ["base", "release_config", "collector"]]
    root = disjoint_output(Path(config["root"]), sources)
    if not root.is_absolute() or not root.is_dir():
        raise ValueError("DAILY_CYCLE_EXISTING_ROOT_REQUIRED")
    if file_sha(sources[0] / "manifest.json") != config.get("base_manifest_sha256"):
        raise ValueError("DAILY_CYCLE_BASE_IDENTITY_DIFFERS")
    if file_sha(sources[1]) != config.get("release_config_sha256"):
        raise ValueError("DAILY_CYCLE_POLICY_IDENTITY_DIFFERS")
    return config


def resources(root: Path, reserve: int = 1024**3) -> dict[str, Any]:
    stat = os.statvfs(root)
    result: dict[str, Any] = {
        "UTC": datetime.now(timezone.utc).isoformat(),
        "device": root.stat().st_dev,
        "available_bytes": stat.f_bavail * stat.f_frsize,
        "free_inodes": stat.f_favail,
        "batch_write_budget_bytes": reserve,
        "free_floor_bytes": 8 * 1024**3,
        "RSS_stop_bytes": 4 * 1024**3,
        "native_workers": 2,
        "lifecycle": "retain original sources, failures and immutable qualified products",
    }
    if result["available_bytes"] < result["free_floor_bytes"] + reserve or stat.f_favail < 20000:
        raise ValueError("DAILY_CYCLE_CAPACITY_GATE")
    return result


def group_rss(group: int) -> int:
    total = 0
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if os.getpgid(int(entry.name)) == group:
                fields = (entry / "statm").read_text().split()
                total += int(fields[1]) * os.sysconf("SC_PAGE_SIZE")
        except (ProcessLookupError, FileNotFoundError, PermissionError):
            continue
    return total


def provider(
    config: dict[str, Any], run: Path, label: str, action: str, *arguments: str, timeout: int = 3600
) -> dict[str, Any]:
    runtime = Path(config["collector"])
    command = [
        sys.executable,
        str(Path(__file__).with_name("daily_base24_provider.py")),
        action,
        "--runtime",
        str(runtime),
        *arguments,
    ]
    started = time.monotonic()
    env = {**os.environ, "PYTHONPATH": str(runtime / "src")}
    # Do not load CN01/Hermes service env or their credential-bearing shell files.
    for key in list(env):
        if key.startswith(("ASTOCK_", "HERMES_ODS_", "QUANTHUS_")):
            env.pop(key)
    peak = 0
    with (run / (label + ".log")).open("xb") as log:
        process = subprocess.Popen(
            command, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )
        try:
            while process.poll() is None:
                peak = max(peak, group_rss(process.pid))
                if time.monotonic() - started > timeout or peak > 4 * 1024**3:
                    raise TimeoutError("DAILY_CYCLE_PROVIDER_TIME_OR_RSS_BUDGET")
                resources(run, 0)
                time.sleep(1)
            if process.returncode:
                raise RuntimeError("DAILY_CYCLE_PROVIDER_FAILED_" + label)
        except BaseException:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
            raise
    value: dict[str, Any] = json.loads((run / (label + ".log")).read_text().splitlines()[-1])
    write(
        run / (label + ".receipt.json"),
        {
            "elapsed_seconds": time.monotonic() - started,
            "peak_group_RSS_bytes": peak,
            "result": value,
        },
    )
    return value


def native(
    config: dict[str, Any], run: Path, day: date, market: Path, events: Path
) -> dict[str, Any]:
    resources(run)
    reference = run / "daily-reference.json"
    value = provider(
        config,
        run,
        "reference",
        "reference",
        "--day",
        str(day),
        "--market",
        str(market),
        "--events",
        str(events),
        "--output",
        str(reference),
    )
    shards = math.ceil(value["population"] / 50)
    capture = run / "capture-5m"
    capture.mkdir()
    write(
        run / "native-write-budget.json",
        {
            **resources(run),
            "market_date": str(day),
            "reference_sha256": file_sha(reference),
            "market_manifest_sha256": file_sha(market / "release.json"),
            "events_manifest_sha256": file_sha(events / "release.json"),
            "expected_shards": shards,
            "expected_symbols": value["population"],
            "max_capture_logical_bytes": 256 * 1024**2,
            "max_seconds": 3600,
            "acquisition_clock": "actual CN02 observation; never backdated",
        },
    )

    def one(index: int) -> dict[str, Any]:
        return provider(
            config,
            run,
            f"capture-{index:03d}",
            "capture",
            "--day",
            str(day),
            "--reference",
            str(reference),
            "--output",
            str(capture / f"shard-{index:03d}"),
            "--shard-index",
            str(index),
            timeout=600,
        )

    began = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=config["native_workers"]) as pool:
        futures = [pool.submit(one, i) for i in range(shards)]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result["errors"] or time.monotonic() - began > 3600:
                for pending in futures:
                    pending.cancel()
                raise ValueError("DAILY_CYCLE_NATIVE_CAPTURE_INCOMPLETE")
    return provider(
        config,
        run,
        "native",
        "native",
        "--reference",
        str(reference),
        "--capture-root",
        str(capture),
        "--output",
        str(run / "native-products"),
        timeout=900,
    )


def prior(config: dict[str, Any], run: Path, day: date) -> Path | None:
    """Freeze one exact predecessor; never follow a mutable legacy CURRENT."""
    candidates = list(config.get("bootstrap_cores", []))
    index_path = Path(config["root"]) / "DATES.json"
    if index_path.exists():
        candidates.extend(object_at(index_path).get("dates", {}).values())
    candidates = [
        item for item in candidates if item.get("core") and item["market_date"] < str(day)
    ]
    if not candidates:
        return None
    item = max(candidates, key=lambda x: x["market_date"])
    path = physical_path(Path(item["core"]))
    if file_sha(path / "manifest.json") != item["core_manifest_sha256"]:
        raise ValueError("DAILY_CYCLE_PRIOR_CHANGED")
    verify_daily_domain_increment(path)
    anchor = physical_path(Path(config["prior_anchor"]))
    if not path.is_relative_to(anchor) or not run.is_relative_to(anchor):
        raise ValueError("DAILY_CYCLE_PRIOR_ANCHOR_DIFFERS")
    descriptor = anchor / ("cn02-daily-prior-" + run.name + ".json")
    write(
        descriptor,
        {
            "kind": "isolated_frozen_prior_reference_not_CURRENT",
            "manifest_path": (path / "manifest.json").relative_to(anchor).as_posix(),
            "manifest_sha256": item["core_manifest_sha256"],
        },
    )
    write(
        run / "prior-reference.json",
        {
            "descriptor": str(descriptor),
            "sha256": file_sha(descriptor),
            "market_date": item["market_date"],
        },
    )
    return descriptor


def publish(config: dict[str, Any], value: dict[str, Any]) -> None:
    """Serialize cross-date updates; replay never regresses the latest date."""
    root = Path(config["root"])
    with (root / "publication.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        index_path = root / "DATES.json"
        index: dict[str, Any] = (
            object_at(index_path)
            if index_path.exists()
            else {
                "kind": KIND,
                "dates": {},
                "execution_authorized": False,
                "consumer_admission": "NOT_GRANTED",
            }
        )
        if index.get("kind") != KIND or not isinstance(index.get("dates"), dict):
            raise ValueError("DAILY_CYCLE_EXISTING_INDEX_DIFFERS")
        day = value["market_date"]
        index["dates"][day] = value
        atomic(index_path, index)
        for field, filename in [
            ("core", "CURRENT_DAILY_CORE_BASE24.json"),
            ("product", "CURRENT_DAILY_1D5M_BASE24.json"),
        ]:
            eligible = [item for item in index["dates"].values() if item.get(field)]
            if eligible:
                latest = max(eligible, key=lambda x: x["market_date"])
                atomic(
                    root / filename,
                    {
                        "kind": KIND,
                        "selected_scope": field,
                        "market_date": latest["market_date"],
                        "entry": latest,
                        "execution_authorized": False,
                        "consumer_admission": "NOT_GRANTED",
                        "index_sha256": file_sha(index_path),
                        "date_coverage": {
                            k: v["status"] for k, v in sorted(index["dates"].items())
                        },
                    },
                )


def assemble(
    config: dict[str, Any],
    run: Path,
    day: date,
    market: Path,
    events: Path,
    native_root: Path | None = None,
) -> dict[str, Any]:
    """Materialize core once before optional 5m; retain useful independent work."""
    resources(run)
    market, events = physical_path(market), physical_path(events)
    if market.parent != events.parent or market.parent.name != str(day):
        raise ValueError("DAILY_CYCLE_EXACT_SAME_DATE_SOURCE_ROOT_REQUIRED")
    sources = market.parent.parent.parent
    expected = {
        "market_daily_core": file_sha(market / "release.json"),
        "calendar_daily_events": file_sha(events / "release.json"),
    }
    write(
        run / "assembly-budget.json",
        {
            **resources(run),
            "market_date": str(day),
            "sources": expected,
            "native_manifest_sha256": file_sha(native_root / "manifest.json")
            if native_root
            else None,
            "stages": ["catalog", "one immutable core", "native", "composite", "verify"],
            "no_1m_or_weekly_predecessor": True,
        },
    )
    started = time.monotonic()
    previous_policy = os.environ.get("HERMES_ODS_PREVENTION_POLICY")
    try:
        os.environ["HERMES_ODS_PREVENTION_POLICY"] = "p10-acquisition-prevention-v1"
        catalog = build_daily_ods_catalog(
            release_root=sources,
            market_date=day,
            output=run / "catalog",
            product_scope=DAILY_CORE_PRODUCT_SCOPE,
        )
    finally:
        if previous_policy is None:
            os.environ.pop("HERMES_ODS_PREVENTION_POLICY", None)
        else:
            os.environ["HERMES_ODS_PREVENTION_POLICY"] = previous_policy
    selected = object_at(catalog.root / "catalog.json")["lanes"]
    if {item["lane"]: item["release_manifest_sha256"] for item in selected} != expected:
        raise ValueError("DAILY_CYCLE_CATALOG_SELECTED_ANOTHER_REVISION")
    core = build_daily_domain_increment(
        catalog_root=catalog.root,
        release_root=sources,
        base_root=Path(config["base"]),
        release_config=Path(config["release_config"]),
        output=run / "core",
        code_revision=config["pipeline_revision"],
        prior_pointer=prior(config, run, day),
    )
    value = {
        "kind": KIND,
        "market_date": str(day),
        "status": "CORE_READY_5M_PENDING",
        "core": str(core.root),
        "core_manifest_sha256": core.manifest_sha256,
        "core_row_counts": core.row_counts,
        "pipeline_revision": config["pipeline_revision"],
        "collector_revision": COLLECTOR,
        "base_manifest_sha256": config["base_manifest_sha256"],
        "source_manifest_sha256": expected,
        "config_sha256": config["release_config_sha256"],
        "core_elapsed_seconds": time.monotonic() - started,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "consumer_admission": "NOT_GRANTED",
        "execution_authorized": False,
    }
    write(run / "core-receipt.json", value)
    publish(config, value)
    if native_root is None:
        result = native(config, run, day, market, events)
        native_root = Path(result["package_path"])
    native_manifest = object_at(native_root / "manifest.json")
    value["native_source"] = str(native_root)
    value["native_manifest_sha256"] = file_sha(native_root / "manifest.json")
    if native_manifest.get("research_ready") is not True:
        # Preserve the original strict R3 classification and core availability.
        value["status"] = "CORE_READY_5M_HELD_SOURCE_QUALITY"
        write(run / "receipt.json", value)
        publish(config, value)
        alert(config, run, day, "SOURCE_NATIVE5M_QUALITY_HELD_CORE_REMAINS_AVAILABLE")
        return value
    time_gate(day, "intake", bool(config.get("recovery_mode")))
    result = build(
        core=core.root,
        native5m=native_root,
        config=Path(config["release_config"]),
        output=run / "product",
        code_revision=config["pipeline_revision"],
    )
    checked = verify(run / "product")
    if checked["manifest_sha256"] != result["manifest_sha256"]:
        raise ValueError("DAILY_CYCLE_PRODUCT_CHANGED_BEFORE_PUBLICATION")
    time_gate(day, "delivery", bool(config.get("recovery_mode")))
    value.update(
        status="QUALIFIED_1D_NATIVE5M",
        product=str(run / "product"),
        product_manifest_sha256=checked["manifest_sha256"],
        row_counts=checked["row_counts"],
        completed_at=datetime.now(timezone.utc).isoformat(),
    )
    write(run / "receipt.json", value)
    publish(config, value)
    write(run / "READY", {"receipt_sha256": file_sha(run / "receipt.json")})
    return value


def collect(config: dict[str, Any], run: Path, day: date) -> dict[str, Any]:
    home = Path(config["root"]) / "collector-home"
    flags = provider(
        config,
        run,
        "calendar",
        "calendar",
        "--start",
        str(day - timedelta(days=45)),
        "--day",
        str(day),
        "--home",
        str(home),
        "--output",
        str(run / "calendar"),
    )
    if str(day) not in flags["dates"]:
        raise ValueError("DAILY_CYCLE_TARGET_CALENDAR_MISSING")
    if not flags["dates"][str(day)]:
        # A closed calendar date never starts daily/minute price requests.
        previous = [key for key, flag in flags["dates"].items() if flag and key < str(day)]
        if not previous:
            raise ValueError("DAILY_CYCLE_CLOSED_EVENT_UNIVERSE_DATE_MISSING")
        source_day = max(previous)
        provider(
            config,
            run,
            "universe",
            "universe",
            "--day",
            source_day,
            "--home",
            str(home),
            "--output",
            str(run / "universe-source.json"),
        )
        events = provider(
            config,
            run,
            "events",
            "events",
            "--day",
            str(day),
            "--universe-day",
            source_day,
            "--home",
            str(home),
            "--output",
            str(run / "events-source.json"),
            timeout=3700,
        )
        if events["status"] != "ready":
            raise ValueError("DAILY_CYCLE_CLOSED_EVENT_SOURCE_NOT_READY")
        result = {
            "kind": KIND,
            "market_date": str(day),
            "status": "AUTHORITATIVE_CLOSED_EVENT_SOURCE_READY",
            "calendar_sha256": file_sha(run / "calendar/calendar.json"),
            "price_provider_requests": 0,
            "event_source": events["release_dir"],
            "event_manifest_sha256": file_sha(Path(events["release_dir"]) / "release.json"),
            "consumer_admission": "NOT_GRANTED",
            "execution_authorized": False,
        }
        write(run / "receipt.json", result)
        publish(config, result)
        return result
    market = provider(
        config,
        run,
        "market",
        "market",
        "--day",
        str(day),
        "--home",
        str(home),
        "--output",
        str(run / "market-source.json"),
        "--budget",
        "7200",
        timeout=7300,
    )
    if market["status"] != "ready":
        raise ValueError("DAILY_CYCLE_MARKET_NOT_READY")
    events = provider(
        config,
        run,
        "events",
        "events",
        "--day",
        str(day),
        "--home",
        str(home),
        "--output",
        str(run / "events-source.json"),
        timeout=3700,
    )
    if events["status"] != "ready":
        raise ValueError("DAILY_CYCLE_EVENTS_NOT_READY")
    return assemble(config, run, day, Path(market["release_dir"]), Path(events["release_dir"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--day", type=date.fromisoformat)
    parser.add_argument("--native-only", action="store_true")
    parser.add_argument("--market", type=Path)
    parser.add_argument("--events", type=Path)
    parser.add_argument("--native", type=Path)
    parser.add_argument("--recovery", action="store_true")
    args = parser.parse_args()
    config = load(args.config)
    config["recovery_mode"] = args.recovery
    day = args.day or datetime.now(CST).date()
    if day > datetime.now(CST).date():
        raise ValueError("DAILY_CYCLE_FUTURE_DATE")
    root = Path(config["root"])
    run = root / (str(day) + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    lock_path = physical_path(root / (str(day) + ".lock"))
    exact_inputs = [physical_path(p) for p in [args.market, args.events, args.native] if p]
    # Supplied source roots must be protected before lock, mkdir or registration.
    disjoint_output(run, exact_inputs)
    disjoint_output(lock_path, exact_inputs)
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # A bounded source continuation holds the same date lock. A recovery
            # timer must leave it running rather than start a competing writer.
            print(json.dumps({"status": "DATE_WRITER_ALREADY_ACTIVE", "market_date": str(day)}))
            return 0
        index_path = root / "DATES.json"
        if index_path.exists() and not args.native_only:
            old = object_at(index_path).get("dates", {}).get(str(day), {})
            if old.get("status") == "QUALIFIED_1D_NATIVE5M":
                checked = verify(Path(old["product"]))
                if checked["manifest_sha256"] != old["product_manifest_sha256"]:
                    raise ValueError("DAILY_CYCLE_REUSE_PRODUCT_CHANGED")
                print(json.dumps({"status": "VERIFIED_UNCHANGED_REUSE", "entry": old}))
                return 0
            if old.get("status") == "AUTHORITATIVE_CLOSED_EVENT_SOURCE_READY":
                if (
                    file_sha(Path(old["event_source"]) / "release.json")
                    != old["event_manifest_sha256"]
                ):
                    raise ValueError("DAILY_CYCLE_CLOSED_SOURCE_CHANGED")
                print(json.dumps({"status": "VERIFIED_CLOSED_SOURCE_REUSE", "entry": old}))
                return 0
        if not args.native_only:
            time_gate(day, "delivery", args.recovery)
        previous_attempts = list(root.glob(str(day) + "-*/registration.json"))
        if not args.native_only and len(previous_attempts) >= 2:
            raise ValueError("DAILY_CYCLE_TWO_ATTEMPT_BUDGET_EXHAUSTED_REQUIRES_DISPOSITION")
        resources(root)
        run.mkdir(mode=0o700)
        write(
            run / "registration.json",
            {
                **resources(root),
                "kind": KIND,
                "market_date": str(day),
                "config_sha256": file_sha(args.config),
                "config": config,
            },
        )
        try:
            if args.native_only:
                result = native(
                    config, run, day, physical_path(args.market), physical_path(args.events)
                )
                write(
                    run / "receipt.json",
                    {
                        "stage": "source_only",
                        "result": result,
                        "complete_daily": False,
                        "consumer_admission": "NOT_GRANTED",
                    },
                )
                print(json.dumps({"run": str(run), "result": result}, sort_keys=True))
                return 0
            if bool(args.market) != bool(args.events):
                raise ValueError("DAILY_CYCLE_BOTH_EXACT_SOURCE_LANES_REQUIRED")
            result = (
                assemble(config, run, day, args.market, args.events, args.native)
                if args.market
                else collect(config, run, day)
            )
            print(json.dumps({"run": str(run), "result": result}, sort_keys=True))
            return 0
        except BaseException as exc:
            write(
                run / "failure.json",
                {
                    "UTC": datetime.now(timezone.utc).isoformat(),
                    "error_class": type(exc).__name__,
                    "reason": str(exc),
                    "no_complete_daily_pointer_published": True,
                },
            )
            alert(config, run, day, type(exc).__name__ + ": " + str(exc))
            if (run / "core-receipt.json").exists():
                partial = object_at(run / "core-receipt.json")
                partial["status"] = "CORE_READY_5M_FAILED"
                publish(config, partial)
            raise


if __name__ == "__main__":
    raise SystemExit(main())

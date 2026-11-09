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
import re
import shutil
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
            command,
            cwd=runtime,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
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
    config: dict[str, Any],
    run: Path,
    day: date,
    market: Path,
    events: Path,
    seed: Path | None = None,
) -> dict[str, Any]:
    resources(run)
    reference = run / "daily-reference.json"
    capture = run / "capture-5m"
    completed: set[int] = set()
    if seed is None:
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
        capture.mkdir()
    else:
        shutil.copyfile(seed.parent / "daily-reference.json", reference)
        value = provider(
            config,
            run,
            "seed",
            "seed",
            "--reference",
            str(reference),
            "--seed",
            str(seed),
            "--output",
            str(capture),
        )
        completed = set(value["complete_shards"])
    capture_seconds = config.get("native_capture_seconds", 3600)
    deadline = config.get("native_capture_deadline")
    shards = math.ceil(value["population"] / 50)
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
            "max_seconds": capture_seconds,
            "original_capture_deadline": deadline,
            "resume_seed": str(seed) if seed else None,
            "reused_securities": value.get("reused_securities", 0),
            "remaining_securities": value.get("remaining_securities", value["population"]),
            "seed_inventory_sha256": value.get("seed_inventory_sha256"),
            "acquisition_clock": "actual CN02 observation; never backdated",
        },
    )

    def one(index: int) -> dict[str, Any]:
        if deadline and datetime.now(timezone.utc) >= datetime.fromisoformat(deadline):
            raise ValueError("DAILY_CYCLE_NATIVE_CAPTURE_INCOMPLETE")
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
        futures = [pool.submit(one, i) for i in range(shards) if i not in completed]
        try:
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                expired = deadline and datetime.now(timezone.utc) >= datetime.fromisoformat(
                    deadline
                )
                if result["errors"] or time.monotonic() - began > capture_seconds or expired:
                    raise ValueError("DAILY_CYCLE_NATIVE_CAPTURE_INCOMPLETE")
        except BaseException:
            for pending in futures:
                pending.cancel()
            raise
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
    return finish_native(config, run, day, value, market, events, native_root)


def finish_native(
    config: dict[str, Any],
    run: Path,
    day: date,
    value: dict[str, Any],
    market: Path,
    events: Path,
    native_root: Path | None = None,
    seed: Path | None = None,
) -> dict[str, Any]:
    """One native tail for both first construction and a verified core continuation."""
    if native_root is None:
        result = (
            native(config, run, day, market, events, seed)
            if seed
            else native(config, run, day, market, events)
        )
        native_root = Path(result["package_path"])
    native_manifest = object_at(native_root / "manifest.json")
    value["native_source"] = str(native_root)
    value["native_manifest_sha256"] = file_sha(native_root / "manifest.json")
    if config.get("recovery_mode"):
        value["delivery_timing"] = "LATE_RECOVERY_NOT_ON_TIME_SUCCESS"
    if native_manifest.get("research_ready") is not True:
        recovered = None
        try:
            from .daily_auction_recovery import recover

            recovered = recover(
                run,
                value,
                revision=config["pipeline_revision"],
                recovery_mode=bool(config.get("recovery_mode")),
            )
            recovery_log = run / "closing-auction-recovery.json"
            recovery_report = object_at(recovery_log)
            value["auction_recovery"] = {
                "report": str(recovery_log),
                "report_sha256": file_sha(recovery_log),
                **recovery_report,
            }
            if recovered is not None and (
                recovery_report.get("status") != "RECOVERED"
                or recovery_report.get("candidate_verified") is not True
            ):
                raise ValueError("AUCTION_RECOVERY_NOT_COLD_VERIFIED")
        except Exception as exc:
            recovered = None
            value["auction_recovery"] = {
                "status": "HELD",
                "reason": type(exc).__name__ + ":" + str(exc)[:240],
            }
        if recovered is not None:
            native_root = recovered
            native_manifest = object_at(native_root / "manifest.json")
            value["native_source"] = str(native_root)
            value["native_manifest_sha256"] = file_sha(native_root / "manifest.json")
    if native_manifest.get("research_ready") is not True:
        # Preserve the original strict R3 classification and core availability.
        value["status"] = "CORE_READY_5M_HELD_SOURCE_QUALITY"
        write(run / "receipt.json", value)
        publish(config, value)
        reason = value.get("auction_recovery", {}).get("reason", "NOT_ELIGIBLE")
        alert(config, run, day, "SOURCE_NATIVE5M_QUALITY_HELD_CORE_REMAINS_AVAILABLE:" + reason)
        return value
    time_gate(day, "intake", bool(config.get("recovery_mode")))
    result = build(
        core=Path(value["core"]),
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
    from .standard_daily_live import finish as finish_standard_delivery

    finish_standard_delivery(config, run, day, value, market=market, events=events)
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
        from .standard_daily_live import finish as finish_standard_delivery

        finish_standard_delivery(config, run, day, result, events=Path(events["release_dir"]))
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


def reference_continuation(
    config: dict[str, Any],
    day: date,
    old: dict[str, Any],
) -> tuple[dict[str, Any], Path, Path]:
    """One bounded continuation only when reference failed before any 5m capture.

    Verify immutable core and both original ODS identities before creating a run.
    Never authorize another full daily acquisition or relax the two-attempt gate.
    The caller holds the existing date lock across validation and continuation.
    """
    root = physical_path(Path(config["root"]))
    core = physical_path(Path(old["core"]))
    previous = core.parent
    if (
        previous.parent != root
        or not previous.name.startswith(str(day) + "-")
        or core.name != "core"
        or old.get("market_date") != str(day)
        or old.get("status") != "CORE_READY_5M_FAILED"
        or old.get("base_manifest_sha256") != config["base_manifest_sha256"]
        or old.get("config_sha256") != config["release_config_sha256"]
        or object_at(previous / "failure.json").get("reason")
        != "DAILY_CYCLE_PROVIDER_FAILED_reference"
        or (previous / "daily-reference.json").exists()
        or (previous / "capture-5m").exists()
        or list(root.glob(str(day) + "-*/native-continuation.json"))
    ):
        raise ValueError("DAILY_CYCLE_REFERENCE_CONTINUATION_NOT_ELIGIBLE")
    receipt = object_at(previous / "core-receipt.json")
    if {k: v for k, v in receipt.items() if k != "status"} != {
        k: v for k, v in old.items() if k != "status"
    }:
        raise ValueError("DAILY_CYCLE_REUSE_CORE_RECEIPT_CHANGED")
    market, events = [
        physical_path(Path(object_at(previous / (name + "-source.json"))["release_dir"]))
        for name in ["market", "events"]
    ]
    if market.parent != events.parent or market.parent.name != str(day):
        raise ValueError("DAILY_CYCLE_EXACT_SAME_DATE_SOURCE_ROOT_REQUIRED")
    expected = {
        "market_daily_core": file_sha(market / "release.json"),
        "calendar_daily_events": file_sha(events / "release.json"),
    }
    if expected != old["source_manifest_sha256"]:
        raise ValueError("DAILY_CYCLE_REUSE_SOURCE_CHANGED")
    checked = verify_daily_domain_increment(core)
    if checked.manifest_sha256 != old["core_manifest_sha256"]:
        raise ValueError("DAILY_CYCLE_REUSE_CORE_CHANGED")
    return dict(old), market, events


def login_repair_plan(previous: Path, rules: dict[str, Any]) -> dict[str, Any]:
    """Repair only up to four pre-query login failures within the original budget."""
    if (
        not (previous / "native-capture-resume.json").is_file()
        or list(previous.parent.glob(previous.name[:10] + "-*/native-login-repair.json"))
        or not (previous / "failure.json").is_file()
        or not re.fullmatch(
            r"DAILY_CYCLE_PROVIDER_FAILED_capture-[0-9]{3}",
            object_at(previous / "failure.json").get("reason", ""),
        )
    ):
        raise ValueError("DAILY_CYCLE_LOGIN_REPAIR_NOT_ELIGIBLE")
    failed = []
    for log in sorted(previous.glob("capture-*.log")):
        if (previous / (log.stem + ".receipt.json")).exists():
            continue
        shard = previous / "capture-5m" / log.stem.replace("capture-", "shard-")
        if (
            not re.fullmatch(r"capture-[0-9]{3}", log.stem)
            or not log.read_text().endswith("ValueError: native 5m login failed\n")
            or not shard.is_dir()
            or (shard / "summary.json").exists()
            or (shard / "failures").exists()
            or not (shard / "raw").is_dir()
            or not (shard / "receipts").is_dir()
            or any((shard / "raw").iterdir())
            or any((shard / "receipts").iterdir())
        ):
            raise ValueError("DAILY_CYCLE_LOGIN_REPAIR_PREQUERY_ONLY")
        failed.append(int(log.stem[-3:]))
    budget = object_at(previous / "native-write-budget.json")
    deadline = datetime.fromisoformat(budget["UTC"]) + timedelta(seconds=budget["max_seconds"])
    remaining = int((deadline - datetime.now(timezone.utc)).total_seconds())
    failed_index = int(object_at(previous / "failure.json")["reason"][-3:])
    if (
        not 1 <= len(failed) <= rules["max_failed_shards"]
        or failed_index not in failed
        or remaining <= 0
    ):
        raise ValueError("DAILY_CYCLE_LOGIN_REPAIR_SCOPE_OR_DEADLINE")
    return {
        "failed_shards": failed,
        "max_seconds": min(rules["max_seconds"], remaining),
        "original_capture_deadline": deadline.isoformat(),
    }


def capture_continuation(
    config: dict[str, Any],
    day: date,
    old: dict[str, Any],
) -> tuple[dict[str, Any], Path, Path, Path]:
    """One additional bounded native batch, only in explicit late recovery mode."""
    root = physical_path(Path(config["root"]))
    policy = Path(__file__).resolve().parents[4] / "config/pipelines/base24-native-recovery.v1.json"
    rules = object_at(policy)
    if (
        not config.get("recovery_mode")
        or rules["max_resume_batches_per_date"] != 1
        or rules["max_seconds_per_batch"] != 3600
        or rules["native_workers_max"] != 2
        or old.get("status") != "CORE_READY_5M_FAILED"
        or old.get("market_date") != str(day)
        or old.get("base_manifest_sha256") != config["base_manifest_sha256"]
        or old.get("config_sha256") != config["release_config_sha256"]
    ):
        raise ValueError("DAILY_CYCLE_NATIVE_RESUME_NOT_ELIGIBLE")
    resumed = list(root.glob(str(day) + "-*/native-capture-resume.json"))
    repair = None
    if resumed:
        if len(resumed) != 1:
            raise ValueError("DAILY_CYCLE_LOGIN_REPAIR_EXACT_RESUME_REQUIRED")
        repair = login_repair_plan(resumed[0].parent, rules["login_only_repair"])
    matches = []
    for path in root.glob(str(day) + "-*/failure.json"):
        if repair is not None:
            if path.parent != resumed[0].parent:
                continue
        elif object_at(path).get("reason") != rules["failure_reason"]:
            continue
        receipt = object_at(path.parent / "core-receipt.json")
        if {k: v for k, v in receipt.items() if k != "status"} == {
            k: v for k, v in old.items() if k != "status"
        }:
            matches.append(path.parent)
    if len(matches) != 1:
        raise ValueError("DAILY_CYCLE_NATIVE_RESUME_EXACT_FAILURE_REQUIRED")
    previous = physical_path(matches[0])
    budget = object_at(previous / "native-write-budget.json")
    if budget["reference_sha256"] != file_sha(previous / "daily-reference.json") or (
        budget.get("resume_seed") and repair is None
    ):
        raise ValueError("DAILY_CYCLE_NATIVE_RESUME_REFERENCE_CHANGED")
    core = physical_path(Path(old["core"]))
    if core.parent.parent != root or not core.parent.name.startswith(str(day) + "-"):
        raise ValueError("DAILY_CYCLE_NATIVE_RESUME_CORE_ROOT_DIFFERS")
    market, events = [
        physical_path(Path(object_at(core.parent / (label + "-source.json"))["release_dir"]))
        for label in ["market", "events"]
    ]
    expected = {
        "market_daily_core": file_sha(market / "release.json"),
        "calendar_daily_events": file_sha(events / "release.json"),
    }
    if (
        market.parent != events.parent
        or market.parent.name != str(day)
        or expected != old["source_manifest_sha256"]
        or budget["market_manifest_sha256"] != expected["market_daily_core"]
        or budget["events_manifest_sha256"] != expected["calendar_daily_events"]
        or verify_daily_domain_increment(core).manifest_sha256 != old["core_manifest_sha256"]
    ):
        raise ValueError("DAILY_CYCLE_NATIVE_RESUME_INPUT_CHANGED")
    if repair is not None:
        config["native_login_repair"] = repair
        config["native_capture_seconds"] = repair["max_seconds"]
        config["native_capture_deadline"] = repair["original_capture_deadline"]
    return dict(old), market, events, physical_path(previous / "capture-5m")


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
        continuation = None
        capture_resume = None
        auction_resume = None
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
            if old.get("status") == "CORE_READY_5M_HELD_SOURCE_QUALITY":
                if any([args.market, args.events, args.native]):
                    raise ValueError("AUCTION_HELD_EXACT_INPUTS_ALREADY_FROZEN")
                from .daily_auction_recovery import held_continuation

                auction_resume = held_continuation(config, day, old)
            if old.get("status") == "CORE_READY_5M_FAILED":
                if args.recovery:
                    # A capture-budget failure can reuse source bytes. Reference
                    # failure retains its separately bounded pre-acquisition path.
                    failures = [
                        object_at(p).get("reason") for p in root.glob(str(day) + "-*/failure.json")
                    ]
                    if "DAILY_CYCLE_NATIVE_CAPTURE_INCOMPLETE" in failures:
                        capture_resume = capture_continuation(config, day, old)
                    else:
                        continuation = reference_continuation(config, day, old)
                else:
                    continuation = reference_continuation(config, day, old)
                if any([args.market, args.events, args.native]):
                    raise ValueError("DAILY_CYCLE_CONTINUATION_EXACT_INPUTS_ALREADY_FROZEN")
        if not args.native_only:
            time_gate(day, "delivery", args.recovery)
        previous_attempts = list(root.glob(str(day) + "-*/registration.json"))
        if (
            not args.native_only
            and continuation is None
            and capture_resume is None
            and len(previous_attempts) >= 2
        ):
            raise ValueError("DAILY_CYCLE_TWO_ATTEMPT_BUDGET_EXHAUSTED_REQUIRES_DISPOSITION")
        if capture_resume is not None:
            disjoint_output(
                run,
                [
                    capture_resume[1],
                    capture_resume[2],
                    capture_resume[3],
                    Path(capture_resume[0]["core"]),
                ],
            )
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
            if auction_resume is not None:
                value, market, events, source, captures = auction_resume
                disjoint_output(run, [market, events, source, captures, Path(value["core"])])
                write(
                    run / "auction-quality-continuation.json",
                    {
                        "market_date": str(day),
                        "original_core": value["core"],
                        "original_source": str(source),
                        "original_capture_root": str(captures),
                        "full_daily_reacquisition": False,
                    },
                )
                value.update(
                    status="CORE_READY_5M_PENDING",
                    pipeline_revision=config["pipeline_revision"],
                    auction_original_capture_root=str(captures),
                    delivery_timing="LATE_RECOVERY_NOT_ON_TIME_SUCCESS",
                )
                write(run / "core-receipt.json", value)
                result = finish_native(config, run, day, value, market, events, native_root=source)
                print(json.dumps({"run": str(run), "result": result}, sort_keys=True))
                return 0
            if capture_resume is not None:
                value, market, events, seed = capture_resume
                policy = (
                    Path(__file__).resolve().parents[4]
                    / "config/pipelines/base24-native-recovery.v1.json"
                )
                write(
                    run
                    / (
                        "native-login-repair.json"
                        if config.get("native_login_repair")
                        else "native-capture-resume.json"
                    ),
                    {
                        "policy_sha256": file_sha(policy),
                        "policy": object_at(policy),
                        "seed": str(seed),
                        "seed_reference_sha256": file_sha(seed.parent / "daily-reference.json"),
                        "core_manifest_sha256": value["core_manifest_sha256"],
                        "source_manifest_sha256": value["source_manifest_sha256"],
                        "original_pipeline_revision": value.get("pipeline_revision"),
                        "continuation_pipeline_revision": config["pipeline_revision"],
                        "market_date": str(day),
                        "budget": resources(root),
                        "login_repair": config.get("native_login_repair"),
                    },
                )
                value.update(
                    status="CORE_READY_5M_PENDING",
                    pipeline_revision=config["pipeline_revision"],
                    delivery_timing="LATE_RECOVERY_NOT_ON_TIME_SUCCESS",
                )
                write(run / "core-receipt.json", value)
                result = finish_native(config, run, day, value, market, events, seed=seed)
                print(json.dumps({"run": str(run), "result": result}, sort_keys=True))
                return 0
            if continuation is not None:
                value, market, events = continuation
                disjoint_output(run, [market, events, Path(value["core"])])
                write(
                    run / "native-continuation.json",
                    {
                        "kind": "p10-reference-failure-continuation-v1",
                        "original_core": value["core"],
                        "core_manifest_sha256": value["core_manifest_sha256"],
                        "source_manifest_sha256": value["source_manifest_sha256"],
                        "original_pipeline_revision": value.get("pipeline_revision"),
                        "continuation_pipeline_revision": config["pipeline_revision"],
                        "market_date": str(day),
                        "max_native_attempts": 1,
                        "full_daily_reacquisition": False,
                    },
                )
                value.update(
                    status="CORE_READY_5M_PENDING", pipeline_revision=config["pipeline_revision"]
                )
                write(run / "core-receipt.json", value)
                result = finish_native(config, run, day, value, market, events)
                print(json.dumps({"run": str(run), "result": result}, sort_keys=True))
                return 0
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
            # A quality continuation failure retains the original indexed HELD
            # disposition. Its private failure and consumed retry marker remain
            # available for explicit disposition; never downgrade the predecessor.
            if (run / "core-receipt.json").exists() and auction_resume is None:
                partial = object_at(run / "core-receipt.json")
                partial["status"] = "CORE_READY_5M_FAILED"
                publish(config, partial)
            raise


if __name__ == "__main__":
    raise SystemExit(main())

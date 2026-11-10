"""One bounded Daily recovery: observed closing auction, never a daily residual."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import signal
import threading
import time
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import canonical, file_sha

POLICY = (
    Path(__file__).resolve().parents[4] / "config/pipelines/base24-auction-auto-recovery.v1.json"
)
CONTRACT = "p10-daily-observed-auction-auto-recovery-v1"


def raw_write(path: Path, data: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def write(path: Path, value: Any) -> None:
    raw_write(path, canonical(value) + b"\n")


def fetch_response(url: str, timeout: float, maximum: int) -> bytes:
    # Public Chinese market-data endpoints; independent of mail/backup credentials.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, headers={"User-Agent": "Quanthus-P10/1.0"})
    with opener.open(request, timeout=timeout) as response:
        if response.geturl() != url or response.getcode() != 200:
            raise ValueError("AUCTION_HTTP_OR_REDIRECT_INVALID")
        body = response.read(maximum + 1)
    if len(body) > maximum:
        raise ValueError("AUCTION_RESPONSE_BUDGET")
    return body


def recover(
    run: Path,
    value: dict[str, Any],
    *,
    revision: str,
    recovery_mode: bool = False,
    fetch: Callable[[str, float, int], bytes] | None = None,
    now: datetime | None = None,
) -> Path | None:
    """Return only a cold-verified new source; failure leaves the original held."""
    began = time.monotonic()
    day = value["market_date"]
    stage = run / "auction-recovery"
    log = run / "closing-auction-recovery.json"
    if stage.exists() or log.exists():
        if not log.exists():
            if (stage / "source/READY").exists():
                (stage / "source/READY").rename(stage / "source/UNVERIFIED")
            write(
                log,
                {
                    "contract": CONTRACT,
                    "market_date": day,
                    "status": "HELD",
                    "reason": "AUCTION_ORPHAN_STAGE_NO_RETRY",
                    "candidate_verified": False,
                },
            )
        return None  # Never repeat a failed attempt or overwrite its evidence.
    report: dict[str, Any] = {
        "contract": CONTRACT,
        "market_date": day,
        "requests": 0,
        "response_bytes_total": 0,
        "candidate_verified": False,
        "source_mutated": False,
        "original_native_manifest_sha256": value["native_manifest_sha256"],
    }
    old_handler = None
    alarm_installed = False
    candidate = None
    try:
        policy_bytes = POLICY.read_bytes()
        report["policy_sha256"] = "sha256:" + hashlib.sha256(policy_bytes).hexdigest()
        policy = json.loads(policy_bytes)
        if (
            policy.get("contract") != CONTRACT
            or policy.get("enabled") is not True
            or policy.get("attempts_per_run") != 1
            or policy.get("full_daily_reacquisition") is not False
            or policy.get("max_quality_continuations_per_date") != 1
            or any(
                policy.get(k) is not False
                for k in [
                    "allow_daily_residual_inference",
                    "allow_gate_relaxation",
                    "allow_post_close_trades",
                ]
            )
        ):
            raise ValueError("AUCTION_POLICY_INVALID")
        source = Path(value["native_source"])
        manifest = json.loads((source / "manifest.json").read_bytes())
        if file_sha(source / "manifest.json") != value["native_manifest_sha256"]:
            raise ValueError("AUCTION_ORIGINAL_SOURCE_CHANGED")
        if manifest.get("processing_revision") is not None:
            raise ValueError("AUCTION_REBIND_PREDECESSOR_UNSUPPORTED")
        statuses = [
            json.loads(s) for s in (source / "quality-status.jsonl").read_text().splitlines()
        ]
        if manifest.get("market_date") != day or any(s.get("market_date") != day for s in statuses):
            raise ValueError("AUCTION_PREDECESSOR_DATE_DIFFERS")
        held = [s for s in statuses if s["quality_grade"] == "R3"]
        if (
            not 1 <= len(held) <= policy["max_symbols"]
            or any(s["quality_grade"] not in {"R0", "R3", "SUSPENDED"} for s in statuses)
            or any(
                not re.fullmatch(r"(?:60\d{4}|688\d{3})", s["stock_code"])
                or s.get("quality_reason") not in policy["allowed_reasons"]
                or s.get("missing_actual_minutes") != []
                for s in held
            )
        ):
            raise ValueError("AUCTION_NOT_ELIGIBLE")
        report["codes"] = sorted(s["stock_code"] for s in held)
        from .daily_base24_cycle import resources

        report["capacity"] = resources(
            run, reserve=max(1024**3, 2 * sum(f["bytes"] for f in manifest["files"]))
        )
        original_paths = {}
        capture_home = Path(value.get("auction_original_capture_root", run / "capture-5m"))
        report["original_capture_root"] = str(capture_home)
        for status in held:
            code = status["stock_code"]
            matches = list(capture_home.glob("shard-*/raw/" + code + ".json.gz"))
            if len(matches) != 1 or matches[0].is_symlink():
                raise ValueError("AUCTION_ORIGINAL_CAPTURE_UNAVAILABLE")
            if (
                "sha256:" + hashlib.sha256(gzip.decompress(matches[0].read_bytes())).hexdigest()
                != status["raw_sha256"]
            ):
                raise ValueError("AUCTION_ORIGINAL_CAPTURE_CHANGED")
            original_paths[code] = matches[0]
        clock = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("Asia/Shanghai"))
        seconds = float(policy["max_seconds"])
        input_remaining = None
        if day == clock.date().isoformat() and not recovery_mode:
            from .daily_base24_job import WINDOW

            deadline = datetime.fromisoformat(day + "T" + WINDOW["sealed_input_deadline"]).replace(
                tzinfo=clock.tzinfo
            )
            input_remaining = (deadline - clock).total_seconds()
            seconds = min(seconds, input_remaining)
        if seconds <= 0:
            raise TimeoutError("AUCTION_OUTSIDE_INTAKE_WINDOW")
        if (
            threading.current_thread() is not threading.main_thread()
            or signal.getitimer(signal.ITIMER_REAL)[0]
        ):
            raise ValueError("AUCTION_DEADLINE_CONTEXT_UNAVAILABLE")
        old_handler = signal.getsignal(signal.SIGALRM)

        def timeout_handler(signum, frame):
            raise TimeoutError("AUCTION_AUTO_RECOVERY_DEADLINE:" + report["phase"])

        report["phase"] = "network"

        signal.signal(signal.SIGALRM, timeout_handler)
        signal.setitimer(signal.ITIMER_REAL, seconds)
        alarm_installed = True
        stage.mkdir()
        inputs, captures = stage / "input", stage / "captures"
        (inputs / "capture-5m/shard-000/raw").mkdir(parents=True)
        captures.mkdir()
        frozen = dict(
            value,
            kind="p10-auction-recovery-input-snapshot-v1",
            status="CORE_READY_5M_HELD_SOURCE_QUALITY",
            original_run=str(run),
        )
        write(inputs / "receipt.json", frozen)
        for code, path in original_paths.items():
            shutil.copyfile(path, inputs / "capture-5m/shard-000/raw" / (code + ".json.gz"))
        index: dict[str, Any] = {"market_date": day, "files": {}}
        used_bytes = 0
        get = fetch or fetch_response

        def capture(name: str, url: str) -> bytes:
            nonlocal used_bytes
            if report["requests"] >= policy["max_requests"]:
                raise ValueError("AUCTION_REQUEST_BUDGET")
            remaining = seconds - (time.monotonic() - began)
            if remaining <= 0:
                raise TimeoutError("AUCTION_AUTO_RECOVERY_DEADLINE")
            started = datetime.now(timezone.utc).isoformat()
            report["requests"] += 1
            body = get(
                url, min(policy["request_timeout_seconds"], remaining), policy["max_response_bytes"]
            )
            if len(body) > policy["max_response_bytes"]:
                raise ValueError("AUCTION_RESPONSE_BUDGET")
            used_bytes += len(body)
            if used_bytes > policy["max_total_response_bytes"]:
                raise ValueError("AUCTION_TOTAL_RESPONSE_BUDGET")
            report["response_bytes_total"] = used_bytes
            raw_write(captures / name, body)
            index["files"][name] = {
                "url": url,
                "bytes": len(body),
                "sha256": "sha256:" + hashlib.sha256(body).hexdigest(),
                "started_at": started,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }
            write(captures / (name + ".receipt.json"), index["files"][name])
            return body

        for code in sorted(original_paths):
            prefix = "https://stock.gtimg.cn/data/index.php?appn=detail&c=sh" + code
            body = capture("tick-count-" + code + ".raw", prefix + "&action=datacount")
            from .closing_auction_source import js

            counts = js(body, "v_detail_time_sh" + code)
            if counts[0] != int(day.replace("-", "")):
                raise ValueError("AUCTION_LIVE_EVIDENCE_DATE_DIFFERS")
            page = len(counts[1].split("|")) - 1
            capture("tick-last-" + code + ".raw", prefix + "&action=data&p=" + str(page))
            if code.startswith("688"):
                capture(
                    "tencent-minute-" + code + ".json",
                    "https://ifzq.gtimg.cn/appstock/app/minute/query?code=sh" + code,
                )
        write(captures / "CAPTURE.json", index)
        from .closing_auction_repair import build

        report["phase"] = "local_build_and_cold_verify"
        report["network_elapsed_seconds"] = round(time.monotonic() - began, 3)
        local_seconds = float(policy["max_local_seconds"])
        if input_remaining is not None:
            local_seconds = min(local_seconds, input_remaining - (time.monotonic() - began))
        if local_seconds <= 0:
            raise TimeoutError("AUCTION_OUTSIDE_INTAKE_WINDOW")
        signal.setitimer(signal.ITIMER_REAL, local_seconds)
        candidate = stage / "source"
        result = build(inputs, captures, candidate, revision)
        from decimal import Decimal

        from .closing_auction_source import observed
        from .daily_base24 import mapping
        from .native5m_daily_source import verify_native5m_source

        amount_checks = {}
        for code in original_paths:
            raw = json.loads(
                gzip.decompress((candidate / "ods/raw" / (code + ".json.gz")).read_bytes())
            )
            event = observed(raw, code, day)
            difference = abs(event["amount"] - event["price"] * event["volume"])
            if difference > Decimal(policy["amount_max_abs_error_cny"]):
                raise ValueError("AUCTION_AMOUNT_NOTIONAL_DIFFERS:" + code)
            amount_checks[code] = str(difference)
        report["amount_notional_abs_error_cny"] = amount_checks
        core = Path(value["core"])
        daily, _ = mapping(core, day)
        core_manifest = json.loads((core / "manifest.json").read_bytes())
        checked = verify_native5m_source(
            candidate,
            result["manifest_sha256"],
            day=day,
            daily=daily,
            daily_sources={
                k: v["manifest_sha256"] for k, v in core_manifest["selected_releases"].items()
            },
        )
        report.update(
            status="RECOVERED",
            candidate_verified=True,
            source=str(candidate),
            manifest_sha256=result["manifest_sha256"],
            grades=checked["grades"],
        )
        return candidate
    except Exception as exc:
        if candidate is not None and (candidate / "READY").exists():
            (candidate / "READY").rename(candidate / "UNVERIFIED")
        report.update(status="HELD", reason=type(exc).__name__ + ":" + str(exc)[:240])
        return None
    finally:
        if alarm_installed:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_handler)
        report.update(
            elapsed_seconds=round(time.monotonic() - began, 3),
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        write(log, report)


def held_continuation(config: dict[str, Any], day, old: dict[str, Any]):
    """Reuse frozen core and original captures for one late quality recovery, never recollect."""
    from .daily_base24_cycle import object_at, physical_path, verify_daily_domain_increment

    rules = json.loads(POLICY.read_bytes())
    if (
        rules.get("enabled") is not True
        or rules.get("max_quality_continuations_per_date") != 1
        or rules.get("full_daily_reacquisition") is not False
    ):
        raise ValueError("AUCTION_HELD_CONTINUATION_POLICY_INVALID")
    root = physical_path(Path(config["root"]))
    core = physical_path(Path(old["core"]))
    previous = core.parent
    source = physical_path(Path(old["native_source"]))
    captures = previous / "capture-5m"
    if (
        not config.get("recovery_mode")
        or old.get("status") != "CORE_READY_5M_HELD_SOURCE_QUALITY"
        or old.get("market_date") != str(day)
        or old.get("base_manifest_sha256") != config["base_manifest_sha256"]
        or old.get("config_sha256") != config["release_config_sha256"]
        or previous.parent != root
        or not previous.name.startswith(str(day) + "-")
        or core.name != "core"
        or not captures.is_dir()
        or list(root.glob(str(day) + "-*/auction-quality-continuation.json"))
    ):
        raise ValueError("AUCTION_HELD_CONTINUATION_NOT_ELIGIBLE")
    if object_at(previous / "receipt.json") != old:
        raise ValueError("AUCTION_HELD_RECEIPT_CHANGED")
    if (
        file_sha(source / "manifest.json") != old["native_manifest_sha256"]
        or object_at(source / "manifest.json").get("research_ready") is not False
    ):
        raise ValueError("AUCTION_HELD_SOURCE_CHANGED")
    market, events = [
        physical_path(Path(object_at(previous / (label + "-source.json"))["release_dir"]))
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
        or verify_daily_domain_increment(core).manifest_sha256 != old["core_manifest_sha256"]
    ):
        raise ValueError("AUCTION_HELD_FROZEN_INPUT_CHANGED")
    return dict(old), market, events, source, captures

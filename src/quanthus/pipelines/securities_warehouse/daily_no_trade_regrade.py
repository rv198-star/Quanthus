"""One bounded regrade continuation for held no-trade 5m quality (#692).

Regrades the frozen original captures with the pinned collector's current
no-trade semantics (NO_TRADE_BAR_RULE). Never recollects and never edits the
original run, core, reference, captures or held evidence. Only R3 entries whose
sole defect is a source-observed all-zero no-trade bar are eligible; every other
held defect keeps its original disposition. One continuation per market date.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from .base22 import file_sha
from .base22_sources import physical_path

CONTRACT = "p10-cn02-held-no-trade-regrade-continuation-v1"
POLICY = (
    Path(__file__).resolve().parents[4]
    / "config/pipelines/base24-no-trade-regrade-continuation.v1.json"
)


def continuation(
    config: dict[str, Any], day: Any, old: dict[str, Any]
) -> tuple[dict[str, Any], Path, Path, Path, Path]:
    """Validate one frozen held state for a no-trade regrade; return its inputs."""
    from .daily_base24_cycle import object_at
    from .native5m_daily_source import is_no_trade_bar, m5_grid, native_m5

    rules = json.loads(POLICY.read_bytes())
    if (
        rules.get("contract") != CONTRACT
        or rules.get("enabled") is not True
        or rules.get("max_continuations_per_date") != 1
        or rules.get("full_daily_reacquisition") is not False
        or rules.get("allowed_held_reasons") != ["invalid_native_5m_source"]
        or rules.get("require_full_grid_after_regrade") is not True
    ):
        raise ValueError("NO_TRADE_REGRADE_POLICY_INVALID")
    root = physical_path(Path(config["root"]))
    core = physical_path(Path(old["core"]))
    previous = core.parent
    source = physical_path(Path(old["native_source"]))
    captures = previous / "capture-5m"
    reference = previous / "daily-reference.json"
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
        or not reference.is_file()
        or list(root.glob(str(day) + "-*/no-trade-regrade-continuation.json"))
    ):
        raise ValueError("NO_TRADE_REGRADE_CONTINUATION_NOT_ELIGIBLE")
    # The indexed entry is bound to the latest retained receipt for the date,
    # which may live in a prior quality-continuation run while the core,
    # reference and captures stay in the original run.
    receipt_runs = sorted(
        run for run in root.glob(str(day) + "-*") if (run / "receipt.json").is_file()
    )
    if not receipt_runs or object_at(receipt_runs[-1] / "receipt.json") != old:
        raise ValueError("NO_TRADE_REGRADE_RECEIPT_CHANGED")
    core_manifest = physical_path(core / "manifest.json")
    if not core_manifest.is_file() or file_sha(core_manifest) != old.get("core_manifest_sha256"):
        raise ValueError("NO_TRADE_REGRADE_CORE_CHANGED")
    if (
        file_sha(source / "manifest.json") != old["native_manifest_sha256"]
        or object_at(source / "manifest.json").get("research_ready") is not False
    ):
        raise ValueError("NO_TRADE_REGRADE_SOURCE_CHANGED")
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
    ):
        raise ValueError("NO_TRADE_REGRADE_SOURCE_CHANGED")
    ref = object_at(reference)
    if ref.get("market_date") != str(day) or file_sha(source / "daily-reference.json") != file_sha(
        reference
    ):
        raise ValueError("NO_TRADE_REGRADE_REFERENCE_DIFFERS")
    statuses = [
        json.loads(line) for line in (source / "quality-status.jsonl").read_text().splitlines()
    ]
    held = [s for s in statuses if s["quality_grade"] == "R3"]
    if (
        not held
        or any(s.get("quality_reason") not in rules["allowed_held_reasons"] for s in held)
        or any(s["quality_grade"] not in {"R0", "R2", "R3", "SUSPENDED"} for s in statuses)
    ):
        raise ValueError("NO_TRADE_REGRADE_HELD_POPULATION_INVALID")
    grid = m5_grid(str(day))
    for status in held:
        code = status["stock_code"]
        matches = list(captures.glob("shard-*/raw/" + code + ".json.gz"))
        if len(matches) != 1 or matches[0].is_symlink():
            raise ValueError("NO_TRADE_REGRADE_CAPTURE_UNAVAILABLE")
        raw = gzip.decompress(matches[0].read_bytes())
        if "sha256:" + hashlib.sha256(raw).hexdigest() != status["raw_sha256"]:
            raise ValueError("NO_TRADE_REGRADE_CAPTURE_CHANGED")
        daily = ref["securities"][code]
        bars = native_m5(raw, code, str(day))
        if len(bars) != len(grid) or any(stamp not in bars for stamp in grid):
            raise ValueError("NO_TRADE_REGRADE_GRID_INCOMPLETE")
        if not any(is_no_trade_bar(bar) for bar in bars.values()):
            raise ValueError("NO_TRADE_REGRADE_NO_TRADE_ABSENT")
        if bars[grid[-1]]["close"].quantize(Decimal(".01")) != Decimal(daily["close"]).quantize(
            Decimal(".01")
        ):
            raise ValueError("NO_TRADE_REGRADE_TERMINAL_CLOSE_DIFFERS")
        if sum((bar["volume_shares"] for bar in bars.values()), Decimal(0)) != Decimal(
            daily["volume"]
        ):
            raise ValueError("NO_TRADE_REGRADE_DAILY_VOLUME_DIFFERS")
    return dict(old), market, events, captures, reference

"""Fail-closed classification of Tencent 1m source-maturity evidence.

Only a source precision/close mismatch may defer an otherwise healthy daily
orchestration. Transport failures, incomplete scans and malformed receipts are
real failures, not a reason to extend the publication window.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

_QUALIFICATION_MARKER = "tencent source qualification failed"
_SOURCE_MISMATCHES = (
    "tencent/daily volume mismatch",
    "tencent/daily close mismatch",
)
_ATTEMPT_DIR_PATTERN = re.compile(r'"attempt_dir"\s*:\s*"([^"]+)"')


def _object(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _complete_source_mismatch(attempt_dir: Path, *, shadow_root: Path, market_date: date) -> bool:
    expected_root = (
        shadow_root / "attempts/intraday-archive/1m" / market_date.isoformat()
    ).resolve()
    if not attempt_dir.resolve().is_relative_to(expected_root):
        return False
    landing = attempt_dir / "landing"
    acquisition = _object(landing / "acquisition_log.json")
    checkpoint = _object(landing / "provider_raw/minute_1m/checkpoint.json")
    if acquisition is None or checkpoint is None:
        return False
    collector = acquisition.get("collector")
    symbols = checkpoint.get("symbols")
    if not isinstance(collector, dict) or not isinstance(symbols, dict):
        return False
    universe_size = collector.get("universe_size")
    if (
        collector.get("provider") != "quanthus.providers.minute_tdx"
        or collector.get("bar_size") != "1m"
        or collector.get("market_date") != market_date.isoformat()
        or collector.get("exit_code") != 1
        or not isinstance(universe_size, int)
        or isinstance(universe_size, bool)
        or universe_size <= 0
        or len(symbols) != universe_size
    ):
        return False
    failures = [
        entry
        for entry in symbols.values()
        if not isinstance(entry, dict) or entry.get("ok") is not True
    ]
    if not failures:
        return False
    return all(
        isinstance(entry, dict)
        and entry.get("status") == "error"
        and any(marker in str(entry.get("error", "")).lower() for marker in _SOURCE_MISMATCHES)
        for entry in failures
    )


def source_maturity_deferred(
    lanes: dict[str, dict[str, Any]],
    *,
    shadow_root: Path,
    market_date: date,
) -> bool:
    """Defer only a pure Tencent precision/close failure with three READY peers."""

    if any(
        lanes.get(lane, {}).get("status") != "ready"
        for lane in ("market-daily-core", "calendar-daily-events", "intraday-5m")
    ):
        return False
    minute = lanes.get("intraday-1m", {})
    if minute.get("status") != "failed":
        return False
    logs = minute.get("logs", [])
    if not isinstance(logs, list):
        return False
    if not logs:
        return False
    try:
        log_text = Path(logs[-1]).read_text(encoding="utf-8", errors="replace")
    except (OSError, TypeError):
        return False
    if _QUALIFICATION_MARKER in log_text.lower():
        return True
    for raw_attempt_dir in _ATTEMPT_DIR_PATTERN.findall(log_text):
        if _complete_source_mismatch(
            Path(raw_attempt_dir), shadow_root=shadow_root, market_date=market_date
        ):
            return True
    return False

"""Durable and bounded 1m T/T+1 runtime admission; no network or market data."""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from quanthus.pipelines.p10_minute_operation_runtime import (
    _load_state, _atomic, run_one,
)

CST = ZoneInfo("Asia/Shanghai")
TARGET = date(2026, 10, 8)


def calendar(tmp_path: Path) -> Path:
    p = tmp_path / "exchange-calendar.json"
    p.write_text(
        json.dumps(
            [
                {"trade_date": "2026-10-08", "is_open": "1"},
                {"trade_date": "2026-10-09", "is_open": "1"},
            ]
        )
    )
    return p


def run(
    tmp_path: Path, phase: str, operation_id: str, command: list[str],
    now: datetime, timeout: int = 3,
) -> dict:
    return run_one(
        state_root=tmp_path / "journal",
        calendar_file=calendar(tmp_path),
        market_date=TARGET,
        phase=phase, operation_id=operation_id,
        argv=command, timeout_seconds=timeout, now=now,
    )


def _set_consumed(tmp_path: Path, phase: str, seconds: float) -> None:
    root = tmp_path / "journal"
    root.mkdir(exist_ok=True)
    p = root / f"{TARGET.isoformat()}-{phase}.json"
    data = _load_state(p, TARGET, phase)
    data["cumulative_processing_seconds"] = seconds
    _atomic(p, data)


def test_59m59s_can_start_and_finish_after_60m(tmp_path: Path) -> None:
    _set_consumed(tmp_path, "T", 3599.98)
    result = run(
        tmp_path, "T", "last-shard",
        [sys.executable, "-c", "import time;time.sleep(0.06)"],
        datetime(2026, 10, 8, 21, 30, tzinfo=CST),
    )
    assert result["status"] == "success"
    assert result["receipt"]["post_cutoff_drain_seconds"] > 0
    assert result["receipt"]["completed_after_cutoff"] is True
    assert result["further_new_operations_admitted"] is False


def test_exact_cutoff_does_not_execute_any_new_command(tmp_path: Path) -> None:
    _set_consumed(tmp_path, "T", 3600)
    marker = tmp_path / "would-have-executed"
    cmd = [sys.executable, "-c", f"from pathlib import Path;Path({str(marker)!r}).touch()"]
    result = run(
        tmp_path, "T", "forbidden", cmd,
        datetime(2026, 10, 8, 21, 30, tzinfo=CST),
    )
    assert result["status"] == "cutoff_reached"
    assert not marker.exists()


def test_replay_exact_operation_id_is_idempotent(tmp_path: Path) -> None:
    cmd = [sys.executable, "-c", "pass"]
    when = datetime(2026, 10, 8, 20, 35, tzinfo=CST)
    first = run(tmp_path, "T", "same", cmd, when)
    second = run(tmp_path, "T", "same", cmd, when)
    assert first["status"] == "success"
    assert second["status"] == "already_recorded"
    state = json.loads(
        (tmp_path / "journal" / "2026-10-08-T.json").read_text()
    )
    assert len(state["operations"]) == 1


def test_reusing_id_with_different_command_is_forbidden(tmp_path: Path) -> None:
    when = datetime(2026, 10, 8, 20, 35, tzinfo=CST)
    run(tmp_path, "T", "same", [sys.executable, "-c", "pass"], when)
    with pytest.raises(ValueError, match="different command"):
        run(tmp_path, "T", "same", [sys.executable, "-c", "print('other')"], when)


def test_stale_inflight_state_requires_explicit_reconciliation(tmp_path: Path) -> None:
    root = tmp_path / "journal"
    root.mkdir(exist_ok=True)
    p = root / "2026-10-08-T.json"
    state = _load_state(p, TARGET, "T")
    state["inflight_operation"] = {"operation_id": "unresolved"}
    _atomic(p, state)
    with pytest.raises(ValueError, match="unreconciled"):
        run(
            tmp_path, "T", "next", [sys.executable, "-c", "pass"],
            datetime(2026, 10, 8, 20, 40, tzinfo=CST),
        )


def test_t_and_tplus1_cannot_share_or_reset_cumulative_clock(tmp_path: Path) -> None:
    _set_consumed(tmp_path, "T", 3600)
    assert run(
        tmp_path, "T", "t-late", [sys.executable, "-c", "pass"],
        datetime(2026, 10, 8, 21, 30, tzinfo=CST),
    )["status"] == "cutoff_reached"
    assert run(
        tmp_path, "T_plus_1", "source-canary", [sys.executable, "-c", "pass"],
        datetime(2026, 10, 9, 10, 35, tzinfo=CST),
    )["status"] == "success"


def test_wrong_trading_time_cannot_launch_operations(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside authorized"):
        run(
            tmp_path, "T", "early", [sys.executable, "-c", "pass"],
            datetime(2026, 10, 8, 19, 50, tzinfo=CST),
        )
    with pytest.raises(ValueError, match="outside authorized"):
        run(
            tmp_path, "T_plus_1", "yesterday", [sys.executable, "-c", "pass"],
            datetime(2026, 10, 8, 10, 35, tzinfo=CST),
        )


def test_individual_timeout_does_not_get_extended_by_phase_cutoff(
    tmp_path: Path,
) -> None:
    result = run(
        tmp_path, "T", "timed-out",
        [sys.executable, "-c", "import time;time.sleep(5)"],
        datetime(2026, 10, 8, 20, 35, tzinfo=CST), timeout=1,
    )
    assert result["status"] == "failed"
    assert result["receipt"]["timed_out_by_own_operation_limit"] is True
    assert result["receipt"]["exit_code"] == 124
    assert result["receipt"]["duration_seconds"] >= 1


def test_no_current_date_guessing_on_calendar_holidays(tmp_path: Path) -> None:
    invalid = tmp_path / "exchange-calendar.json"
    invalid.write_text(
        json.dumps([
            {"trade_date": "2026-09-30", "is_open": "1"},
            {"trade_date": "2026-10-01", "is_open": "0"},
        ])
    )
    with pytest.raises(ValueError, match="target must be a verified exchange"):
        run_one(
            state_root=tmp_path / "journal",
            calendar_file=invalid, market_date=date(2026, 10, 1),
            phase="T", operation_id="holiday",
            argv=[sys.executable, "-c", "pass"], timeout_seconds=1,
            now=datetime(2026, 10, 1, 20, 30, tzinfo=CST),
        )

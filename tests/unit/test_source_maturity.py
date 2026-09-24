from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from quanthus.pipelines.source_maturity import source_maturity_deferred

DAY = date(2026, 9, 23)


def _lanes(log: Path) -> dict[str, dict]:
    return {
        "market-daily-core": {"status": "ready"},
        "calendar-daily-events": {"status": "ready"},
        "intraday-5m": {"status": "ready"},
        "intraday-1m": {"status": "failed", "logs": [str(log)]},
    }


def _full_scan(tmp_path: Path, error: str, *, count: int = 4) -> tuple[Path, dict]:
    shadow = tmp_path / "shadow"
    attempt = shadow / "attempts/intraday-archive/1m/2026-09-23/run-1"
    landing = attempt / "landing"
    checkpoint = landing / "provider_raw/minute_1m/checkpoint.json"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    symbols = {f"00000{i}": {"ok": True, "status": "done"} for i in range(1, count)}
    symbols["000004"] = {"status": "error", "error": error}
    checkpoint.write_text(json.dumps({"symbols": symbols}), encoding="utf-8")
    (landing / "acquisition_log.json").write_text(
        json.dumps(
            {
                "collector": {
                    "provider": "quanthus.providers.minute_tdx",
                    "bar_size": "1m",
                    "market_date": DAY.isoformat(),
                    "exit_code": 1,
                    "universe_size": 4,
                }
            }
        ),
        encoding="utf-8",
    )
    log = tmp_path / "lane.log"
    log.write_text(json.dumps({"attempt_dir": str(attempt)}), encoding="utf-8")
    return log, _lanes(log)


def test_complete_scan_with_only_tencent_volume_or_close_mismatches_defers(
    tmp_path: Path,
) -> None:
    for error in (
        "Tencent m1 request/validation failed: Tencent/daily volume mismatch for 000004",
        "Tencent m1 request/validation failed: Tencent/daily close mismatch for 000004",
    ):
        log, lanes = _full_scan(tmp_path, error)
        assert source_maturity_deferred(lanes, shadow_root=tmp_path / "shadow", market_date=DAY)


def test_transport_and_incomplete_scan_remain_failures(tmp_path: Path) -> None:
    log, lanes = _full_scan(tmp_path, "TimeoutError: provider did not answer")
    assert not source_maturity_deferred(lanes, shadow_root=tmp_path / "shadow", market_date=DAY)
    _full_scan(tmp_path, "Tencent/daily close mismatch for 000004", count=3)
    assert not source_maturity_deferred(lanes, shadow_root=tmp_path / "shadow", market_date=DAY)
    lanes["intraday-5m"]["status"] = "failed"
    assert not source_maturity_deferred(lanes, shadow_root=tmp_path / "shadow", market_date=DAY)


def test_preflight_qualification_remains_deferred_only_with_other_lanes_ready(
    tmp_path: Path,
) -> None:
    log = tmp_path / "lane.log"
    log.write_text("Tencent source qualification failed for 000001", encoding="utf-8")
    lanes = _lanes(log)
    assert source_maturity_deferred(lanes, shadow_root=tmp_path / "shadow", market_date=DAY)
    lanes["market-daily-core"]["status"] = "failed"
    assert not source_maturity_deferred(lanes, shadow_root=tmp_path / "shadow", market_date=DAY)


def test_checkpoint_outside_node_shadow_cannot_defer(tmp_path: Path) -> None:
    _, lanes = _full_scan(tmp_path, "Tencent/daily close mismatch for 000004")
    assert not source_maturity_deferred(
        lanes, shadow_root=tmp_path / "different-shadow", market_date=DAY
    )


def test_latest_lane_attempt_governs_deferral(tmp_path: Path) -> None:
    first_log, lanes = _full_scan(tmp_path, "Tencent/daily close mismatch for 000004")
    latest_log = tmp_path / "latest.log"
    latest_log.write_text("TimeoutError: provider did not answer", encoding="utf-8")
    lanes["intraday-1m"]["logs"] = [str(first_log), str(latest_log)]
    assert not source_maturity_deferred(lanes, shadow_root=tmp_path / "shadow", market_date=DAY)


def test_mixed_source_and_transport_failures_do_not_defer(tmp_path: Path) -> None:
    _, lanes = _full_scan(tmp_path, "Tencent/daily close mismatch for 000004")
    checkpoint = (
        tmp_path
        / "shadow/attempts/intraday-archive/1m/2026-09-23/run-1"
        / "landing/provider_raw/minute_1m/checkpoint.json"
    )
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    payload["symbols"]["000003"] = {"status": "error", "error": "HTTP 503 upstream unavailable"}
    checkpoint.write_text(json.dumps(payload), encoding="utf-8")
    assert not source_maturity_deferred(lanes, shadow_root=tmp_path / "shadow", market_date=DAY)

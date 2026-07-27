"""Tests for repository-owned ODS provider entrypoints."""

from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines import lane_runner
from quanthus.providers import ods_snapshot, trading_calendar


def _write_parquet(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)


def _paths(home: Path, tmp_path: Path) -> lane_runner.LanePaths:
    return lane_runner.LanePaths(
        home=home,
        shadow_root=tmp_path / "shadow",
        calendar=home / "ref_trading_calendar.parquet",
        lock_root=tmp_path / "locks",
    )


def test_ods_snapshot_windows_local_source_and_writes_only_output(tmp_path: Path) -> None:
    home = tmp_path / "home"
    source = home / "ref_corporate_actions.parquet"
    _write_parquet(
        source,
        [
            {"stock_code": "000001", "announce_date": "2026-07-20"},
            {"stock_code": "000002", "announce_date": "2026-07-24"},
        ],
    )
    output = tmp_path / "attempt" / "corporate-actions.parquet"

    result = ods_snapshot.collect(
        "corporate-actions",
        date(2026, 7, 23),
        date(2026, 7, 24),
        output,
        home,
    )

    rows = pq.read_table(output).to_pylist()
    assert result["selected_row_count"] == 1
    assert [row["stock_code"] for row in rows] == ["000002"]
    assert output.with_suffix(".parquet.meta.json").is_file()
    assert sorted(path.name for path in home.iterdir()) == [source.name]


def test_ods_snapshot_fails_closed_when_source_is_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="source missing"):
        ods_snapshot.collect(
            "dividends",
            date(2026, 7, 1),
            date(2026, 7, 24),
            tmp_path / "attempt" / "dividends.parquet",
            tmp_path / "home",
        )


def test_calendar_refresh_requires_fresh_target_and_preserves_history(
    tmp_path: Path,
) -> None:
    source = tmp_path / "base" / "ref_trading_calendar.parquet"
    _write_parquet(
        source,
        [
            {"exchange": exchange, "trade_date": "2026-07-25", "is_open": 0}
            for exchange in ("SSE", "SZSE", "BSE")
        ],
    )
    output = tmp_path / "attempt" / "ref_trading_calendar.parquet"

    result = trading_calendar.refresh_calendar(
        source=source,
        output=output,
        target_date=date(2026, 7, 27),
        fetcher=lambda _start, _end: [
            {"trade_date": "2026-07-26", "is_open": 0},
            {"trade_date": "2026-07-27", "is_open": 1},
        ],
    )

    rows = pq.read_table(output).to_pylist()
    target = [row for row in rows if str(row["trade_date"]) == "2026-07-27"]
    assert result.target_state == "both_open"
    assert result.fetched_base_rows == 2
    assert result.merged_rows == 9
    assert {row["exchange"] for row in target} == {"SSE", "SZSE", "BSE"}
    assert {row["is_open"] for row in target} == {1}
    assert result.metadata_path.is_file()
    assert output.stat().st_mode & 0o777 == 0o644


def test_calendar_refresh_rejects_response_without_target(tmp_path: Path) -> None:
    source = tmp_path / "base" / "ref_trading_calendar.parquet"
    _write_parquet(
        source,
        [
            {"exchange": exchange, "trade_date": "2026-07-25", "is_open": 0}
            for exchange in ("SSE", "SZSE")
        ],
    )

    with pytest.raises(trading_calendar.CalendarRefreshError, match="does not contain target"):
        trading_calendar.refresh_calendar(
            source=source,
            output=tmp_path / "attempt" / "ref_trading_calendar.parquet",
            target_date=date(2026, 7, 27),
            fetcher=lambda _start, _end: [
                {"trade_date": "2026-07-26", "is_open": 0}
            ],
        )


def test_provider_spec_supports_repository_python_entrypoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _write_parquet(
        home / "ref_corporate_actions.parquet",
        [{"stock_code": "000001", "announce_date": "2026-07-24"}],
    )
    spec = tmp_path / "providers.json"
    spec.write_text(
        json.dumps(
            {
                "lanes": {
                    "calendar-daily-events": {
                        "corporate_actions_delta": {
                            "argv": [
                                "{python}",
                                "-m",
                                "quanthus.providers.ods_snapshot",
                                "corporate-actions",
                                "--start",
                                "{window_start}",
                                "--end",
                                "{market_date}",
                                "--output",
                                "{output}",
                                "--home",
                                "{home}",
                            ]
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_ODS_PROVIDER_SPEC", str(spec))
    paths = _paths(home, tmp_path)

    rows, meta = lane_runner._run_configured_provider_collector(
        lane_id="calendar-daily-events",
        dataset="corporate_actions_delta",
        output=tmp_path / "attempt" / "corporate-actions.parquet",
        paths=paths,
        market_date=date(2026, 7, 24),
        window_start=date(2026, 7, 24),
    )

    assert len(rows) == 1
    assert meta["exit_code"] == 0
    assert meta["argv"][0]
    assert "quanthus.providers.ods_snapshot" in meta["argv"]


def test_minute_collector_defaults_to_repository_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "data"
    home.mkdir()
    paths = _paths(home, tmp_path)
    observed: list[str] = []
    observed_timeout: list[int] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        observed.extend(command)
        observed_timeout.append(int(_kwargs["timeout"]))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.delenv("HERMES_ODS_MINUTE_COLLECTOR", raising=False)
    monkeypatch.delenv("HERMES_ODS_MINUTE_TIMEOUT_SEC", raising=False)
    monkeypatch.setattr(subprocess, "run", fake_run)
    result = lane_runner._run_minute_collector(
        paths=paths,
        market_date=date(2026, 7, 24),
        bar_size="5m",
        symbols=["000001"],
        raw_home=tmp_path / "attempt" / "minute_5m",
    )

    assert observed[1:3] == ["-m", "quanthus.providers.minute_tdx"]
    assert result["provider"] == "quanthus.providers.minute_tdx"
    assert observed_timeout == [1800]
    assert result["timeout_seconds"] == 1800


@pytest.mark.parametrize(
    ("configured", "message"),
    [
        ("not-a-number", "must be an integer"),
        ("59", "must be at least 60"),
        ("14401", "exceeds the 4-hour service timeout"),
    ],
)
def test_minute_collector_rejects_invalid_bounded_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured: str, message: str
) -> None:
    home = tmp_path / "data"
    home.mkdir()
    monkeypatch.setenv("HERMES_ODS_MINUTE_TIMEOUT_SEC", configured)

    with pytest.raises(lane_runner.LaneRunError, match=message):
        lane_runner._run_minute_collector(
            paths=_paths(home, tmp_path),
            market_date=date(2026, 7, 24),
            bar_size="5m",
            symbols=["000001"],
            raw_home=tmp_path / "attempt" / "minute_5m",
        )


def test_minute_collector_override_must_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "data"
    home.mkdir()
    monkeypatch.setenv("HERMES_ODS_MINUTE_COLLECTOR", str(tmp_path / "missing.py"))
    with pytest.raises(lane_runner.LaneRunError, match="override missing"):
        lane_runner._run_minute_collector(
            paths=_paths(home, tmp_path),
            market_date=date(2026, 7, 24),
            bar_size="5m",
            symbols=["000001"],
            raw_home=tmp_path / "attempt" / "minute_5m",
        )

"""Faults are synthetic and attempt-local; no provider or production I/O."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from quanthus.providers import minute_tdx


@pytest.fixture
def fallback(monkeypatch: pytest.MonkeyPatch) -> Mock:
    session = Mock()
    monkeypatch.setattr(requests, "Session", lambda: session)
    monkeypatch.setattr(minute_tdx, "tdx_client", Mock(side_effect=RuntimeError("offline")))
    monkeypatch.setattr(minute_tdx.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        minute_tdx,
        "_merge_write",
        lambda home, code, freq, _frame: (0, home / f"{code}_{freq}.parquet"),
    )
    return session


def collect(home: Path, count: int) -> int:
    return minute_tdx.collect(
        home=home,
        layer="research",
        frequency="1m",
        mode="incr",
        symbols=[f"{i:06d}" for i in range(1, count + 1)],
        max_pages=1,
        sleep_seconds=0,
        market_date="2026-09-21",
    )


def checkpoint(home: Path) -> dict:
    return json.loads((home / "checkpoint.json").read_text())["symbols"]


def status(home: Path) -> dict:
    return json.loads((home / "status.json").read_text())


def test_circuit_flushes_all_twenty_failures_before_terminal_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fallback: Mock,
) -> None:
    fetch = Mock(side_effect=RuntimeError("synthetic provider failure"))
    monkeypatch.setattr(minute_tdx, "_eastmoney_bars", fetch)
    writer = minute_tdx._atomic_json
    observed = []

    def verify_terminal_order(path: Path, payload: dict) -> None:
        if path.name == "status.json" and payload["state"] == "failed":
            observed.append(len(checkpoint(tmp_path)))
        writer(path, payload)

    monkeypatch.setattr(minute_tdx, "_atomic_json", verify_terminal_order)
    with pytest.raises(RuntimeError, match="circuit opened after 20"):
        collect(tmp_path, 25)
    assert observed == [20]
    assert len(checkpoint(tmp_path)) == status(tmp_path)["error"] == 20
    assert fetch.call_count == 20
    fallback.close.assert_called_once()


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
def test_interruption_preserves_completed_results_without_inventing_remaining_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fallback: Mock,
    interrupt: type,
) -> None:
    fetch = Mock(side_effect=[(None, {"ok": True})] * 2 + [interrupt("cancelled")])
    monkeypatch.setattr(minute_tdx, "_eastmoney_bars", fetch)
    with pytest.raises(interrupt, match="cancelled"):
        collect(tmp_path, 6)
    assert set(checkpoint(tmp_path)) == {"000001", "000002"}
    assert status(tmp_path)["state"] == "failed"
    assert status(tmp_path)["done"] == 2
    assert status(tmp_path)["current"] == "000003"
    fallback.close.assert_called_once()


def test_periodic_checkpoint_disk_failure_closes_session_and_never_reports_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fallback: Mock,
) -> None:
    monkeypatch.setattr(minute_tdx, "_eastmoney_bars", Mock(return_value=(None, {"ok": True})))
    writer = minute_tdx._atomic_json

    def disk_failure(path: Path, payload: dict) -> None:
        if path.name == "checkpoint.json":
            raise OSError("synthetic disk full")
        writer(path, payload)

    monkeypatch.setattr(minute_tdx, "_atomic_json", disk_failure)
    with pytest.raises(OSError, match="synthetic disk full"):
        collect(tmp_path, 6)
    assert status(tmp_path)["state"] != "done"
    fallback.close.assert_called_once()


def test_circuit_primary_failure_survives_terminal_checkpoint_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fallback: Mock,
    capsys: pytest.CaptureFixture,
) -> None:
    monkeypatch.setattr(minute_tdx, "_eastmoney_bars", Mock(side_effect=RuntimeError("offline")))
    writer = minute_tdx._atomic_json

    def fail_latest(path: Path, payload: dict) -> None:
        if path.name == "checkpoint.json" and len(payload["symbols"]) == 20:
            raise OSError("terminal disk full")
        writer(path, payload)

    monkeypatch.setattr(minute_tdx, "_atomic_json", fail_latest)
    with pytest.raises(RuntimeError, match="circuit opened after 20"):
        collect(tmp_path, 25)
    assert len(checkpoint(tmp_path)) == 15
    assert status(tmp_path)["state"] == "running"  # No false durable terminal receipt.
    assert "terminal disk full" in capsys.readouterr().err
    fallback.close.assert_called_once()


@pytest.mark.parametrize("one_error", [False, True])
def test_normal_terminal_counts_and_exit_codes_are_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fallback: Mock,
    one_error: bool,
) -> None:
    results = [(None, {"ok": True})] * 7
    if one_error:
        results[2] = RuntimeError("one symbol failure")
    monkeypatch.setattr(minute_tdx, "_eastmoney_bars", Mock(side_effect=results))
    assert collect(tmp_path, 7) == int(one_error)
    assert len(checkpoint(tmp_path)) == 7
    assert status(tmp_path)["done"] == 7 - int(one_error)
    assert status(tmp_path)["error"] == int(one_error)
    assert status(tmp_path)["state"] == ("done_with_errors" if one_error else "done")
    fallback.close.assert_called_once()


def test_initial_status_write_failure_never_opens_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fallback: Mock,
) -> None:
    monkeypatch.setattr(minute_tdx, "_atomic_json", Mock(side_effect=OSError("status unwritable")))
    with pytest.raises(OSError, match="status unwritable"):
        collect(tmp_path, 1)
    minute_tdx.tdx_client.assert_not_called()
    fallback.close.assert_not_called()


def test_tdx_interruption_also_flushes_completed_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(minute_tdx, "tdx_client", lambda: (object(), "synthetic-tdx"))
    monkeypatch.setattr(
        minute_tdx,
        "page_bars",
        Mock(
            side_effect=[
                (None, {"ok": True}),
                KeyboardInterrupt("tdx cancelled"),
            ]
        ),
    )
    monkeypatch.setattr(minute_tdx, "_merge_write", lambda *_: (0, tmp_path / "empty.parquet"))
    with pytest.raises(KeyboardInterrupt, match="tdx cancelled"):
        collect(tmp_path, 3)
    assert set(checkpoint(tmp_path)) == {"000001"}
    assert status(tmp_path)["state"] == "failed"
    assert status(tmp_path)["done"] == 1


def test_terminal_status_write_failure_keeps_checkpoint_and_exits_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fallback: Mock,
) -> None:
    monkeypatch.setattr(minute_tdx, "_eastmoney_bars", Mock(return_value=(None, {"ok": True})))
    writer = minute_tdx._atomic_json

    def fail_terminal_status(path: Path, payload: dict) -> None:
        if path.name == "status.json" and payload["state"] != "running":
            raise OSError("terminal status unavailable")
        writer(path, payload)

    monkeypatch.setattr(minute_tdx, "_atomic_json", fail_terminal_status)
    with pytest.raises(OSError, match="terminal status unavailable"):
        collect(tmp_path, 2)
    assert len(checkpoint(tmp_path)) == 2
    assert status(tmp_path)["state"] == "running"
    fallback.close.assert_called_once()


def test_session_close_error_does_not_mask_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fallback: Mock,
    capsys: pytest.CaptureFixture,
) -> None:
    monkeypatch.setattr(
        minute_tdx, "_eastmoney_bars", Mock(side_effect=KeyboardInterrupt("cancelled"))
    )
    fallback.close.side_effect = OSError("session close failed")
    with pytest.raises(KeyboardInterrupt, match="cancelled"):
        collect(tmp_path, 2)
    assert "session close failed" in capsys.readouterr().err
    assert checkpoint(tmp_path) == {}
    assert status(tmp_path)["state"] == "failed"


def test_session_close_error_after_success_is_not_swallowed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fallback: Mock,
) -> None:
    monkeypatch.setattr(minute_tdx, "_eastmoney_bars", Mock(return_value=(None, {"ok": True})))
    fallback.close.side_effect = OSError("session close failed")
    with pytest.raises(OSError, match="session close failed"):
        collect(tmp_path, 1)
    assert len(checkpoint(tmp_path)) == 1

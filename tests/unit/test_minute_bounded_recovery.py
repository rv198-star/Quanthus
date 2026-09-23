"""Deterministic opt-in recovery faults; no real provider or production access."""

import json
import subprocess
import sys
from collections import Counter
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from quanthus.pipelines import lane_runner
from quanthus.providers import minute_recovery as recovery
from quanthus.providers import minute_tdx as minute

POLICY = Path(__file__).resolve().parents[2] / "config/pipelines/minute-attempt-recovery.v1.json"


@pytest.fixture
def clock(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(recovery.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(
        recovery.time, "sleep", lambda seconds: setattr(clock, "now", clock.now + seconds)
    )
    return clock


@pytest.fixture
def setup(monkeypatch, clock):
    session = Mock()
    monkeypatch.setattr(requests, "Session", lambda: session)
    monkeypatch.setattr(minute, "tdx_client", Mock(side_effect=RuntimeError("offline")))
    monkeypatch.setattr(minute, "_merge_write", lambda home, code, freq, frame: (0, home / code))
    return session


def run(home, count=2, policy=None, **kwargs):
    return minute.collect(
        home=home,
        layer="research",
        frequency="1m",
        mode="incr",
        symbols=[f"{i:06d}" for i in range(count)],
        max_pages=1,
        sleep_seconds=0,
        recovery_policy=policy,
        **kwargs,
    )


def read(home, filename="status.json"):
    return json.loads((home / filename).read_text())


def test_4992_only_two_residuals_are_retried(tmp_path, monkeypatch, setup, clock):
    calls = Counter()

    def fetch(code, *args, **kwargs):
        calls[code] += 1
        if code in {"000002", "004991"} and calls[code] == 1:
            raise RuntimeError("transient")
        return None, {"ok": True}

    monkeypatch.setattr(minute, "_eastmoney_bars", fetch)
    # Avoid quadratic disk traffic in the scale test; retain the exact payload.
    receipts = {}
    monkeypatch.setattr(
        minute, "_atomic_json", lambda path, payload: receipts.update({path.name: payload})
    )
    policy = recovery.load_minute_recovery_policy(POLICY)
    assert run(tmp_path, 4992, policy) == 0
    assert sum(calls.values()) == 4994
    assert sum(value == 1 for value in calls.values()) == 4990
    status = receipts["status.json"]
    assert (status["state"], status["done"], status["error"]) == ("done", 4992, 0)
    assert status["symbol_attempts"] == 4994
    assert status["recovery_policy_sha256"] == policy.sha256
    entry = receipts["checkpoint.json"]["symbols"]["004991"]
    assert [a["status"] for a in entry["attempts"]] == ["error", "done"]
    assert clock.now < policy.budget_seconds
    setup.close.assert_called_once()


@pytest.mark.parametrize("enabled,expected", [(False, 2), (True, 4)])
def test_persistent_failure_is_bounded_and_not_double_counted(
    tmp_path, monkeypatch, setup, enabled, expected
):
    fetch = Mock(side_effect=RuntimeError("persistent"))
    monkeypatch.setattr(minute, "_eastmoney_bars", fetch)
    assert (
        run(tmp_path, policy=recovery.load_minute_recovery_policy(POLICY) if enabled else None) == 1
    )
    assert fetch.call_count == expected
    assert read(tmp_path)["error"] == 2
    assert read(tmp_path)["done"] == 0


def test_circuit_does_not_start_another_round(tmp_path, monkeypatch, setup):
    fetch = Mock(side_effect=RuntimeError("persistent"))
    monkeypatch.setattr(minute, "_eastmoney_bars", fetch)
    with pytest.raises(RuntimeError, match="circuit opened after 20"):
        run(tmp_path, 25, recovery.load_minute_recovery_policy(POLICY))
    assert fetch.call_count == 20
    assert len(read(tmp_path, "checkpoint.json")["symbols"]) == 20


@pytest.mark.parametrize("phase", ["endpoint", "response", "pacing", "round_delay"])
def test_one_budget_no_late_request_or_late_response_write(
    tmp_path, monkeypatch, setup, clock, phase
):
    policy = replace(recovery.load_minute_recovery_policy(POLICY), budget_seconds=0.1)

    def fetch(*args, **kwargs):
        if phase == "response":
            clock.now += 1
        if phase == "round_delay":
            raise RuntimeError("transient")
        return None, {"ok": True}

    fetcher = Mock(side_effect=fetch)
    monkeypatch.setattr(minute, "_eastmoney_bars", fetcher)
    if phase == "endpoint":

        def endpoint(**kwargs):
            clock.now += 1
            raise RuntimeError("offline")

        monkeypatch.setattr(minute, "tdx_client", endpoint)
    with pytest.raises(recovery.MinuteBudgetExceeded):
        run(tmp_path, 1 if phase == "round_delay" else 2, policy)
    assert fetcher.call_count == (0 if phase == "endpoint" else 1)
    assert read(tmp_path)["terminal_reason"] == "budget_exhausted"
    assert read(tmp_path)["state"] == "failed"
    if phase in {"endpoint", "response"}:
        assert read(tmp_path, "checkpoint.json")["symbols"] == {}


def test_round_does_not_reset_budget(tmp_path, monkeypatch, setup, clock):
    policy = replace(
        recovery.load_minute_recovery_policy(POLICY), budget_seconds=1.0, retry_delay_seconds=0.3
    )
    calls = []

    def fetch(*args, **kwargs):
        calls.append(kwargs["budget"].expires_at)
        clock.now += 0.6
        if len(calls) == 1:
            raise RuntimeError("transient")
        return None, {"ok": True}

    monkeypatch.setattr(minute, "_eastmoney_bars", fetch)
    with pytest.raises(recovery.MinuteBudgetExceeded):
        run(tmp_path, 1, policy)
    assert calls == [1.0, 1.0]
    assert read(tmp_path)["done"] == 0


def test_disk_failure_does_not_retry_provider(tmp_path, monkeypatch, setup):
    fetch = Mock(return_value=(None, {"ok": True}))
    monkeypatch.setattr(minute, "_eastmoney_bars", fetch)
    monkeypatch.setattr(minute, "_merge_write", Mock(side_effect=OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        run(tmp_path, policy=recovery.load_minute_recovery_policy(POLICY))
    assert fetch.call_count == 1
    assert read(tmp_path)["state"] == "failed"
    assert read(tmp_path)["error"] == 1


@pytest.mark.parametrize("late", [False, True])
def test_http_timeout_and_backoff_share_deadline(clock, late):
    session = Mock()

    def get(*args, **kwargs):
        assert kwargs["timeout"] == 0.25
        if late:
            clock.now += 1
            return Mock(json=lambda: {"data": {"code": "000001", "trends": []}})
        raise OSError("offline")

    session.get.side_effect = get
    with pytest.raises(recovery.MinuteBudgetExceeded):
        minute._eastmoney_payload(
            "000001", session=session, budget=recovery.MinuteBudget.start(0.25)
        )
    assert session.get.call_count == 1


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
def test_opt_in_cancellation_keeps_completed_evidence(tmp_path, monkeypatch, setup, interrupt):
    fetch = Mock(side_effect=[(None, {"ok": True}), interrupt("cancelled")])
    monkeypatch.setattr(minute, "_eastmoney_bars", fetch)
    with pytest.raises(interrupt):
        run(tmp_path, policy=recovery.load_minute_recovery_policy(POLICY))
    assert list(read(tmp_path, "checkpoint.json")["symbols"]) == ["000000"]
    assert read(tmp_path)["state"] == "failed"
    setup.close.assert_called_once()


def test_tdx_probe_stops_before_factory_after_deadline(monkeypatch, clock):
    factory = Mock()
    monkeypatch.setitem(
        sys.modules, "mootdx.quotes", SimpleNamespace(Quotes=SimpleNamespace(factory=factory))
    )

    def probe(*args, **kwargs):
        assert kwargs["timeout"] == 0.25
        clock.now += 1
        return True

    monkeypatch.setattr(minute, "_probe", probe)
    with pytest.raises(recovery.MinuteBudgetExceeded):
        minute.tdx_client(budget=recovery.MinuteBudget.start(0.25))
    factory.assert_not_called()


def test_tdx_page_rejects_late_response(clock):
    client = Mock()

    def bars(**kwargs):
        clock.now += 1
        return None

    client.bars.side_effect = bars
    with pytest.raises(recovery.MinuteBudgetExceeded):
        minute.page_bars(client, "000001", 8, 2, "incr", budget=recovery.MinuteBudget.start(0.25))
    assert client.bars.call_count == 1


def test_tdx_reconnect_cannot_swallow_budget_exhaustion(tmp_path, monkeypatch, setup):
    client = Mock()
    factory = Mock(side_effect=[(client, "synthetic"), recovery.MinuteBudgetExceeded("expired")])
    monkeypatch.setattr(minute, "tdx_client", factory)
    fetch = Mock(return_value=(None, {"ok": False, "error": "disconnected"}))
    monkeypatch.setattr(minute, "page_bars", fetch)
    with pytest.raises(recovery.MinuteBudgetExceeded):
        run(tmp_path, policy=recovery.load_minute_recovery_policy(POLICY))
    assert fetch.call_count == 1
    assert read(tmp_path)["terminal_reason"] == "budget_exhausted"
    assert read(tmp_path, "checkpoint.json")["symbols"]["000000"]["status"] == "error"


def test_expired_before_first_symbol_has_no_provider_request(tmp_path, monkeypatch, setup, clock):
    client = Mock()
    monkeypatch.setattr(minute, "tdx_client", lambda **kwargs: (client, "synthetic"))
    policy = replace(recovery.load_minute_recovery_policy(POLICY), budget_seconds=0.1)
    writer = minute._atomic_json

    def slow_initial_write(path, payload):
        writer(path, payload)
        if path.name == "status.json" and payload["state"] == "running":
            clock.now += 1

    monkeypatch.setattr(minute, "_atomic_json", slow_initial_write)
    with pytest.raises(recovery.MinuteBudgetExceeded):
        run(tmp_path, policy=policy)
    client.bars.assert_not_called()
    assert read(tmp_path)["symbol_attempts"] == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", True),
        ("retry_rounds", True),
        ("retry_rounds", 4),
        ("retry_rounds", -1),
        ("retry_delay_seconds", -1),
        ("retry_delay_seconds", float("nan")),
        ("budget_seconds", 0),
        ("budget_seconds", float("inf")),
        ("budget_seconds", "10"),
        ("frequency", "5m"),
        ("mode", "backfill"),
        ("activation", "automatic"),
        ("unknown", 1),
    ],
)
def test_invalid_policy_rejected(tmp_path, field, value):
    data = json.loads(POLICY.read_text())
    data[field] = value
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        recovery.load_minute_recovery_policy(path)


@pytest.mark.parametrize(
    "change",
    [{"layer": "prod", "frequency": "5m"}, {"mode": "backfill"}, {"symbols": ["000001", "000001"]}],
)
def test_unsupported_scope_rejected_before_io(tmp_path, monkeypatch, change):
    client = Mock()
    monkeypatch.setattr(minute, "tdx_client", client)
    args = dict(
        home=tmp_path / "new",
        layer="research",
        frequency="1m",
        mode="incr",
        symbols=["000001"],
        max_pages=1,
        sleep_seconds=0,
        recovery_policy=recovery.load_minute_recovery_policy(POLICY),
    )
    args.update(change)
    with pytest.raises(ValueError):
        minute.collect(**args)
    client.assert_not_called()
    assert not args["home"].exists()


@pytest.mark.parametrize("bar_size,enabled", [("1m", True), ("1m", False), ("5m", True)])
def test_lane_explicit_activation_only_for_1m(tmp_path, monkeypatch, bar_size, enabled):
    monkeypatch.setattr(lane_runner, "_minute_collector_fetch_mode", lambda **kwargs: ("incr", 1))
    monkeypatch.setenv("HERMES_ODS_MINUTE_1M_RECOVERY_POLICY", str(POLICY) if enabled else "")
    for key in (
        "HERMES_ODS_MINUTE_SEED_HOME",
        "HERMES_ODS_MINUTE_COLLECTOR",
        "HERMES_ODS_MINUTE_TIMEOUT_SEC",
    ):
        monkeypatch.delenv(key, raising=False)
    proc = Mock(return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""))
    monkeypatch.setattr(subprocess, "run", proc)
    result = lane_runner._run_minute_collector(
        paths=SimpleNamespace(home=tmp_path / "home"),
        market_date=date(2026, 9, 21),
        bar_size=bar_size,
        symbols=["000001"],
        raw_home=tmp_path / "raw",
    )
    assert ("--recovery-policy" in result["cmd"]) == (enabled and bar_size == "1m")
    assert ("recovery_policy_sha256" in result) == (enabled and bar_size == "1m")


@pytest.mark.parametrize(
    "key,value",
    [
        ("HERMES_ODS_MINUTE_SEED_HOME", "/unused"),
        ("HERMES_ODS_MINUTE_COLLECTOR", "/unused"),
        ("HERMES_ODS_MINUTE_TIMEOUT_SEC", "1740"),
    ],
)
def test_lane_conflicts_rejected_before_attempt_creation(tmp_path, monkeypatch, key, value):
    monkeypatch.setattr(lane_runner, "_minute_collector_fetch_mode", lambda **kwargs: ("incr", 1))
    monkeypatch.setenv("HERMES_ODS_MINUTE_1M_RECOVERY_POLICY", str(POLICY))
    monkeypatch.setenv(key, value)
    raw = tmp_path / "raw"
    with pytest.raises(lane_runner.LaneRunError):
        lane_runner._run_minute_collector(
            paths=SimpleNamespace(home=tmp_path / "home"),
            market_date=date(2026, 9, 21),
            bar_size="1m",
            symbols=["000001"],
            raw_home=raw,
        )
    assert not raw.exists()


def test_cli_invalid_policy_returns_failure_before_collection(tmp_path, monkeypatch):
    collector = Mock()
    monkeypatch.setattr(minute, "collect", collector)
    assert (
        minute.main(
            [
                "--layer",
                "research",
                "--freq",
                "1m",
                "--mode",
                "incr",
                "--symbols",
                "000001",
                "--home",
                str(tmp_path),
                "--recovery-policy",
                str(tmp_path / "absent"),
            ]
        )
        == 2
    )
    collector.assert_not_called()

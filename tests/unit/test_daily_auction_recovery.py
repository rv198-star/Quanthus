"""Bounded automatic recovery through the actual Daily hook; all HTTP is captured fixtures."""

import json
import signal
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse import daily_auction_recovery as auto
from quanthus.pipelines.securities_warehouse import daily_base24
from quanthus.pipelines.securities_warehouse import daily_base24_cycle as cycle
from quanthus.pipelines.securities_warehouse.base22 import file_sha
from tests.unit.test_closing_auction_source import held_auction_fixture


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    attempt, evidence, source, receipt = held_auction_fixture(tmp_path)
    monkeypatch.setattr(cycle, "resources", lambda *a, **k: {"available_bytes": 100 * 1024**3})
    core = tmp_path / "core"
    core.mkdir()
    (core / "manifest.json").write_text(
        json.dumps(
            {
                "selected_releases": {
                    "market_daily_core": {"manifest_sha256": "sha256:" + "a" * 64},
                    "calendar_daily_events": {"manifest_sha256": "sha256:" + "b" * 64},
                }
            }
        )
    )
    receipt["core"] = str(core)
    monkeypatch.setattr(
        daily_base24,
        "mapping",
        lambda *a: (
            {"600061": {"is_tradable": True, "close": Decimal(11), "volume": Decimal(248)}},
            None,
        ),
    )
    index = json.loads((evidence / "CAPTURE.json").read_text())
    responses = {v["url"]: (evidence / name).read_bytes() for name, v in index["files"].items()}
    return attempt, source, receipt, responses


def test_auto_source_build_and_cold_replay(scenario):
    run, source, value, responses = scenario
    old_hash = file_sha(source / "manifest.json")
    old_handler = signal.getsignal(signal.SIGALRM)
    calls = []

    def fetch(url, timeout, maximum):
        calls.append(url)
        assert 0 < timeout <= 6
        return responses[url]

    recovered = auto.recover(run, value, revision="c" * 40, recovery_mode=True, fetch=fetch)
    assert recovered is not None
    assert json.loads((recovered / "manifest.json").read_text())["grades"] == {"R0": 1}
    report = json.loads((run / "closing-auction-recovery.json").read_text())
    assert report["status"] == "RECOVERED"
    assert report["requests"] == len(calls) == 2
    assert file_sha(source / "manifest.json") == old_hash
    assert signal.getsignal(signal.SIGALRM) == old_handler
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0


@pytest.mark.parametrize("failure", ["network", "stale_date", "post_close", "oversized", "amount"])
def test_unavailable_or_invalid_evidence_stays_held(scenario, failure):
    run, source, value, responses = scenario

    def fetch(url, timeout, maximum):
        if failure == "network":
            raise TimeoutError("network unavailable")
        if failure == "oversized":
            return b"x" * (maximum + 1)
        raw = responses[url]
        if failure == "stale_date" and "datacount" in url:
            raw = raw.replace(b"20261103", b"20261102")
        if failure == "post_close" and "action=data&" in url:
            raw = raw.replace(b"15:00:02", b"15:05:02")
        if failure == "amount" and "action=data&" in url:
            raw = raw.replace(b"/2200/", b"/22000000/")
        return raw

    assert auto.recover(run, value, revision="c" * 40, recovery_mode=True, fetch=fetch) is None
    assert json.loads((run / "closing-auction-recovery.json").read_text())["status"] == "HELD"
    assert json.loads((source / "manifest.json").read_text())["research_ready"] is False
    assert not (run / "product").exists()
    # No repeated network call or overwritten receipt in the same attempt.
    assert (
        auto.recover(
            run,
            value,
            revision="c" * 40,
            recovery_mode=True,
            fetch=lambda *a: pytest.fail("repeat fetch"),
        )
        is None
    )


def test_outside_tday_intake_window_does_not_fetch(scenario):
    run, source, value, responses = scenario
    assert (
        auto.recover(
            run,
            value,
            revision="c" * 40,
            fetch=lambda *a: pytest.fail("late fetch"),
            now=datetime.fromisoformat("2026-11-03T20:45:01+08:00"),
        )
        is None
    )
    assert (
        "OUTSIDE_INTAKE_WINDOW"
        in json.loads((run / "closing-auction-recovery.json").read_text())["reason"]
    )


def test_unsupported_quality_does_not_fetch(scenario):
    run, source, value, responses = scenario
    path = source / "quality-status.jsonl"
    status = json.loads(path.read_text())
    status["missing_actual_minutes"] = ["202611030935"]
    path.write_text(json.dumps(status) + "\n")
    assert (
        auto.recover(
            run, value, revision="c" * 40, fetch=lambda *a: pytest.fail("unsupported fetch")
        )
        is None
    )
    assert (
        "NOT_ELIGIBLE" in json.loads((run / "closing-auction-recovery.json").read_text())["reason"]
    )


def test_daily_hook_uses_verified_recovered_source(tmp_path, monkeypatch):
    run = tmp_path / "run"
    run.mkdir()
    original = tmp_path / "old"
    repaired = tmp_path / "new"
    original.mkdir()
    repaired.mkdir()
    (original / "manifest.json").write_text('{"research_ready":false}')
    (repaired / "manifest.json").write_text('{"research_ready":true}')
    calls = []
    monkeypatch.setattr(auto, "recover", lambda *a, **k: repaired)
    (run / "closing-auction-recovery.json").write_text(
        json.dumps({"status": "RECOVERED", "candidate_verified": True})
    )
    monkeypatch.setattr(cycle, "time_gate", lambda *a: None)
    monkeypatch.setattr(
        cycle, "build", lambda **k: calls.append(k) or {"manifest_sha256": "sha256:test"}
    )
    monkeypatch.setattr(
        cycle,
        "verify",
        lambda *a: {"manifest_sha256": "sha256:test", "row_counts": {"1d": 1, "5m": 48}},
    )
    monkeypatch.setattr(cycle, "publish", lambda *a: None)
    value = {"core": str(tmp_path / "core"), "market_date": "2026-11-03"}
    config = {
        "pipeline_revision": "c" * 40,
        "release_config": str(tmp_path / "config"),
        "recovery_mode": True,
    }
    result = cycle.finish_native(
        config,
        run,
        datetime.fromisoformat("2026-11-03").date(),
        value,
        tmp_path / "market",
        tmp_path / "events",
        native_root=original,
    )
    assert calls[0]["native5m"] == repaired
    assert result["status"] == "QUALIFIED_1D_NATIVE5M"
    assert result["native_source"] == str(repaired)
    assert result["delivery_timing"] == "LATE_RECOVERY_NOT_ON_TIME_SUCCESS"
    assert result["auction_recovery"]["report_sha256"] == file_sha(
        run / "closing-auction-recovery.json"
    )


def test_hard_wall_clock_timeout_stays_held(scenario, monkeypatch, tmp_path):
    import time

    run, source, value, responses = scenario
    policy = json.loads(auto.POLICY.read_text())
    policy["max_seconds"] = 0.02
    path = tmp_path / "short-policy.json"
    path.write_text(json.dumps(policy))
    monkeypatch.setattr(auto, "POLICY", path)
    old_handler = signal.getsignal(signal.SIGALRM)

    def fetch(*args):
        time.sleep(0.5)
        raise AssertionError("alarm did not interrupt fetch")

    assert auto.recover(run, value, revision="c" * 40, recovery_mode=True, fetch=fetch) is None
    report = json.loads((run / "closing-auction-recovery.json").read_text())
    assert "AUTO_RECOVERY_DEADLINE" in report["reason"]
    assert report["elapsed_seconds"] < 0.3
    assert signal.getsignal(signal.SIGALRM) == old_handler
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0


def test_cold_verification_failure_quarantines_candidate(scenario, monkeypatch):
    run, source, value, responses = scenario
    path = Path(value["core"]) / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["selected_releases"]["market_daily_core"]["manifest_sha256"] = "sha256:" + "f" * 64
    path.write_text(json.dumps(manifest))
    assert (
        auto.recover(
            run, value, revision="c" * 40, recovery_mode=True, fetch=lambda url, *a: responses[url]
        )
        is None
    )
    assert not (run / "auction-recovery/source/READY").exists()
    assert (run / "auction-recovery/source/UNVERIFIED").exists()
    assert (
        json.loads((run / "closing-auction-recovery.json").read_text())["candidate_verified"]
        is False
    )


def test_resource_gate_precedes_network(scenario, monkeypatch):
    run, source, value, responses = scenario

    def full(*a, **k):
        raise ValueError("DAILY_CYCLE_CAPACITY_GATE")

    monkeypatch.setattr(cycle, "resources", full)
    assert (
        auto.recover(
            run,
            value,
            revision="c" * 40,
            recovery_mode=True,
            fetch=lambda *a: pytest.fail("capacity fetch"),
        )
        is None
    )
    assert (
        "CAPACITY_GATE" in json.loads((run / "closing-auction-recovery.json").read_text())["reason"]
    )


def test_orphan_stage_is_reported_and_quarantined_without_retry(scenario):
    run, source, value, responses = scenario
    candidate = run / "auction-recovery/source"
    candidate.mkdir(parents=True)
    (candidate / "READY").write_text("not verified")
    assert (
        auto.recover(run, value, revision="c" * 40, fetch=lambda *a: pytest.fail("orphan fetch"))
        is None
    )
    assert (candidate / "UNVERIFIED").exists()
    assert (
        json.loads((run / "closing-auction-recovery.json").read_text())["reason"]
        == "AUCTION_ORPHAN_STAGE_NO_RETRY"
    )


def test_unexpected_recovery_error_keeps_quality_held(tmp_path, monkeypatch):
    run = tmp_path / "run"
    source = tmp_path / "native"
    run.mkdir()
    source.mkdir()
    (source / "manifest.json").write_text('{"research_ready":false}')

    def broken(*a, **k):
        raise RuntimeError("unexpected hook error")

    monkeypatch.setattr(auto, "recover", broken)
    monkeypatch.setattr(cycle, "publish", lambda *a: None)
    alerts = []
    monkeypatch.setattr(cycle, "alert", lambda *a: alerts.append(a[-1]))
    value = {"core": str(tmp_path / "core"), "market_date": "2026-11-03"}
    result = cycle.finish_native(
        {"pipeline_revision": "c" * 40},
        run,
        datetime.fromisoformat("2026-11-03").date(),
        value,
        tmp_path / "market",
        tmp_path / "events",
        native_root=source,
    )
    assert result["status"] == "CORE_READY_5M_HELD_SOURCE_QUALITY"
    assert "unexpected hook error" in alerts[0]
    assert result["native_source"] == str(source)


@pytest.mark.parametrize("fault", ["none", "redirect", "http_status", "too_large"])
def test_network_transport_origin_status_and_byte_limits(monkeypatch, fault):
    from contextlib import nullcontext

    url = "https://stock.gtimg.cn/test"

    class Response:
        def geturl(self):
            return url if fault != "redirect" else "https://other.example/test"

        def getcode(self):
            return 200 if fault != "http_status" else 206

        def read(self, n):
            assert n == 9
            return b"123456789" if fault == "too_large" else b"ok"

    class Opener:
        def open(self, request, timeout):
            assert timeout == 1
            return nullcontext(Response())

    monkeypatch.setattr(auto.urllib.request, "build_opener", lambda *a: Opener())
    if fault == "none":
        assert auto.fetch_response(url, 1, 8) == b"ok"
    else:
        with pytest.raises(ValueError):
            auto.fetch_response(url, 1, 8)


def test_held_continuation_validates_frozen_core_and_sources(tmp_path, monkeypatch):
    from types import SimpleNamespace

    root = tmp_path / "runtime"
    previous = root / "2026-11-03-original"
    core = previous / "core"
    core.mkdir(parents=True)
    (previous / "capture-5m").mkdir()
    native = previous / "native"
    native.mkdir()
    (native / "manifest.json").write_text('{"research_ready":false}')
    release = tmp_path / "sources/2026-11-03"
    market, events = release / "market", release / "events"
    for path, label in [(market, "market"), (events, "events")]:
        path.mkdir(parents=True)
        (path / "release.json").write_text('{"kind":"' + label + '"}')
        (previous / (label + "-source.json")).write_text(json.dumps({"release_dir": str(path)}))
    old = {
        "status": "CORE_READY_5M_HELD_SOURCE_QUALITY",
        "market_date": "2026-11-03",
        "core": str(core),
        "native_source": str(native),
        "native_manifest_sha256": file_sha(native / "manifest.json"),
        "base_manifest_sha256": "sha256:base",
        "config_sha256": "sha256:config",
        "core_manifest_sha256": "sha256:core",
        "source_manifest_sha256": {
            "market_daily_core": file_sha(market / "release.json"),
            "calendar_daily_events": file_sha(events / "release.json"),
        },
    }
    (previous / "receipt.json").write_text(json.dumps(old))
    config = {
        "root": str(root),
        "recovery_mode": True,
        "base_manifest_sha256": "sha256:base",
        "release_config_sha256": "sha256:config",
    }
    monkeypatch.setattr(
        cycle,
        "verify_daily_domain_increment",
        lambda *a: SimpleNamespace(manifest_sha256="sha256:core"),
    )
    day = datetime.fromisoformat("2026-11-03").date()
    result = auto.held_continuation(config, day, old)
    assert result[0] == old
    assert result[3] == native
    assert result[4] == previous / "capture-5m"
    (market / "release.json").write_text("changed")
    with pytest.raises(ValueError, match="FROZEN_INPUT_CHANGED"):
        auto.held_continuation(config, day, old)


@pytest.mark.parametrize("fail_tail", [False, True])
def test_daily_quality_resume_never_recollects_full_day(tmp_path, monkeypatch, fail_tail):
    import sys

    root = tmp_path / "runtime"
    previous = root / "2026-11-03-original"
    core, source, captures = previous / "core", previous / "native", previous / "capture-5m"
    market, events = tmp_path / "sources/2026-11-03/market", tmp_path / "sources/2026-11-03/events"
    for path in [core, source, captures, market, events]:
        path.mkdir(parents=True)
    (previous / "registration.json").write_text("{}")
    old = {
        "status": "CORE_READY_5M_HELD_SOURCE_QUALITY",
        "market_date": "2026-11-03",
        "core": str(core),
    }
    (root / "DATES.json").write_text(json.dumps({"dates": {"2026-11-03": old}}))
    path = tmp_path / "config.json"
    path.write_text("{}")
    config = {"root": str(root), "pipeline_revision": "c" * 40}
    monkeypatch.setattr(cycle, "load", lambda *a: config)
    monkeypatch.setattr(cycle, "resources", lambda *a, **k: {"available_bytes": 100 * 1024**3})
    monkeypatch.setattr(cycle, "time_gate", lambda *a: None)
    monkeypatch.setattr(
        auto, "held_continuation", lambda *a: (dict(old), market, events, source, captures)
    )
    for name in ["collect", "assemble", "native"]:
        monkeypatch.setattr(cycle, name, lambda *a, **k: pytest.fail("full day recollection"))
    calls = []

    def finish(config, run, day, value, market, events, *, native_root):
        calls.append((run, dict(value), native_root))
        if fail_tail:
            raise OSError("tail copy failed")
        return {"status": "CORE_READY_5M_HELD_SOURCE_QUALITY"}

    monkeypatch.setattr(cycle, "finish_native", finish)
    monkeypatch.setattr(
        sys, "argv", ["cycle", "--config", str(path), "--day", "2026-11-03", "--recovery"]
    )
    monkeypatch.setattr(cycle, "alert", lambda *a: None)
    monkeypatch.setattr(
        cycle, "publish", lambda *a: pytest.fail("must preserve indexed predecessor")
    )
    if fail_tail:
        with pytest.raises(OSError, match="tail copy failed"):
            cycle.main()
        assert json.loads((root / "DATES.json").read_text())["dates"]["2026-11-03"] == old
    else:
        assert cycle.main() == 0
    assert len(calls) == 1
    run, value, native_root = calls[0]
    assert native_root == source
    assert value["auction_original_capture_root"] == str(captures)
    assert value["delivery_timing"] == "LATE_RECOVERY_NOT_ON_TIME_SUCCESS"
    assert (
        json.loads((run / "auction-quality-continuation.json").read_text())[
            "full_daily_reacquisition"
        ]
        is False
    )
    assert (run / "core-receipt.json").exists()

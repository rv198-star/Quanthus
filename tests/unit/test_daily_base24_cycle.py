from __future__ import annotations

import json
import os
import sys
import threading
import time
import types
from datetime import date, datetime

import pytest

from quanthus.pipelines.securities_warehouse import daily_base24_cycle as cycle
from quanthus.pipelines.securities_warehouse import daily_base24_provider as worker
from quanthus.pipelines.securities_warehouse.base22_sources import SourceAssessmentError


def test_atomic_and_exclusive_files(tmp_path):
    path = tmp_path / "pointer.json"
    cycle.write(path, {"date": "2026-10-08"})
    with pytest.raises(FileExistsError):
        cycle.write(path, {})
    cycle.atomic(path, {"date": "2026-10-09"})
    assert cycle.object_at(path) == {"date": "2026-10-09"}


def test_configuration_rejects_one_minute_and_changed_base(tmp_path):
    root, base, runtime = [tmp_path / name for name in ["root", "base", "runtime"]]
    for path in [root, base, runtime]:
        path.mkdir()
    cycle.write(base / "manifest.json", {})
    policy = tmp_path / "policy.json"
    cycle.write(policy, {})
    value = {
        "kind": cycle.KIND,
        "node": "cn02",
        "collector_revision": worker.COLLECTOR,
        "pipeline_revision": "a" * 40,
        "intervals": ["1d", "5m"],
        "execution_authorized": False,
        "consumer_admission": "NOT_GRANTED",
        "native_workers": 2,
        "root": str(root),
        "base": str(base),
        "collector": str(runtime),
        "release_config": str(policy),
        "base_manifest_sha256": cycle.file_sha(base / "manifest.json"),
        "release_config_sha256": cycle.file_sha(policy),
    }
    config = tmp_path / "config.json"
    config.write_text(json.dumps(value))
    assert cycle.load(config) == value
    for key, change in [
        ("intervals", ["1d", "1m", "5m"]),
        ("execution_authorized", True),
        ("native_workers", 10),
        ("collector_revision", "b" * 40),
        ("base_manifest_sha256", "sha256:" + "f" * 64),
        ("root", str(base / "nested")),
    ]:
        config.write_text(json.dumps({**value, key: change}))
        with pytest.raises((ValueError, SourceAssessmentError)):
            cycle.load(config)


def calendar_client(monkeypatch, rows, error="0"):
    fields = ["calendar_date", "is_trading_day"]
    iterator = iter(rows)
    response = types.SimpleNamespace(fields=fields, error_code=error)
    response.next = lambda: (
        setattr(response, "row", next(iterator, None)) or response.row is not None
    )
    response.get_row_data = lambda: response.row
    bs = types.ModuleType("baostock")
    bs.login = lambda: types.SimpleNamespace(error_code="0")
    bs.logout = lambda: None
    bs.query_trade_dates = lambda **kw: response
    monkeypatch.setitem(sys.modules, "baostock", bs)
    evidence = types.ModuleType("quanthus.providers.acquisition_evidence")
    evidence.clock = lambda: "2026-10-20T11:00:00+00:00"
    evidence.capture = lambda **kw: (kw["root"] / "original.json").write_bytes(kw["raw"])
    monkeypatch.setitem(sys.modules, evidence.__name__, evidence)
    delivery = types.ModuleType("quanthus.pipelines.p10_delivery_common")
    delivery.atomic = lambda p, v: p.write_text(json.dumps(v))
    delivery.encoded = lambda v: json.dumps(v).encode()
    delivery.file_digest = cycle.file_sha
    monkeypatch.setitem(sys.modules, delivery.__name__, delivery)
    transport = types.ModuleType("quanthus.providers.baostock_transport")
    transport.set_socket_timeout = lambda seconds: True
    monkeypatch.setitem(sys.modules, transport.__name__, transport)


def test_calendar_retains_raw_and_closed_dates_without_weekday_guess(monkeypatch, tmp_path):
    calendar_client(monkeypatch, [["2026-10-08", "1"], ["2026-10-09", "0"]])
    result = worker.calendar(
        date(2026, 10, 8), date(2026, 10, 9), tmp_path / "out", tmp_path / "home"
    )
    assert result["dates"] == {"2026-10-08": True, "2026-10-09": False}
    assert json.loads((tmp_path / "out/original.json").read_bytes())["rows"][1][1] == "0"
    assert (tmp_path / "home/calendar-baseline.parquet").is_file()
    with pytest.raises(ValueError):
        worker.calendar(date(2026, 10, 8), date(2026, 10, 9), tmp_path / "out")


@pytest.mark.parametrize(
    "rows,error",
    [
        ([], "0"),
        ([["2026-10-08", "1"]], "0"),
        ([["2026-10-08", "1"], ["2026-10-08", "0"]], "0"),
        ([["2026-10-08", "x"], ["2026-10-09", "1"]], "0"),
        ([], "provider_error"),
    ],
)
def test_calendar_unknown_duplicate_missing_or_error_never_seals(
    monkeypatch, tmp_path, rows, error
):
    calendar_client(monkeypatch, rows, error)
    with pytest.raises(ValueError):
        worker.calendar(date(2026, 10, 8), date(2026, 10, 9), tmp_path / "out")
    assert not (tmp_path / "out/calendar.json").exists()


def test_native_shared_reference_exact_bounded_shards(monkeypatch, tmp_path):
    calls, current, peak = [], 0, 0
    lock = threading.Lock()
    monkeypatch.setattr(cycle, "resources", lambda *a, **kw: {})
    market, events = tmp_path / "market", tmp_path / "events"
    for root in [market, events]:
        root.mkdir()
        cycle.write(root / "release.json", {})

    def provider(config, run, label, action, *args, **kw):
        nonlocal current, peak
        calls.append((action, args))
        if action == "reference":
            cycle.write(run / "daily-reference.json", {"population": 101})
            return {"population": 101}
        if action == "capture":
            with lock:
                current += 1
                peak = max(peak, current)
            time.sleep(0.01)
            with lock:
                current -= 1
            return {"errors": 0}
        return {"package_path": "sealed-source", "quality_grades": {"R0": 101}}

    monkeypatch.setattr(cycle, "provider", provider)
    result = cycle.native({"native_workers": 2}, tmp_path, date(2026, 10, 20), market, events)
    assert result["package_path"] == "sealed-source"
    assert [a for a, _ in calls].count("reference") == 1
    assert [a for a, _ in calls].count("capture") == 3
    assert [a for a, _ in calls].count("native") == 1
    assert peak <= 2
    assert all("1m" not in args for _, args in calls)


def test_native_source_error_does_not_build_product(monkeypatch, tmp_path):
    monkeypatch.setattr(cycle, "resources", lambda *a, **kw: {})
    for name in ["market", "events"]:
        (tmp_path / name).mkdir()
        cycle.write(tmp_path / name / "release.json", {})
    calls = []

    def provider(config, run, label, action, *a, **kw):
        calls.append(action)
        if action == "reference":
            cycle.write(run / "daily-reference.json", {})
            return {"population": 1}
        return {"errors": 1}

    monkeypatch.setattr(cycle, "provider", provider)
    with pytest.raises(ValueError, match="INCOMPLETE"):
        cycle.native(
            {"native_workers": 2},
            tmp_path,
            date(2026, 10, 20),
            tmp_path / "market",
            tmp_path / "events",
        )
    assert "native" not in calls


@pytest.mark.parametrize(
    "stage,clock,success",
    [
        ("intake", "17:29:59", False),
        ("intake", "17:30:00", True),
        ("intake", "20:44:59", True),
        ("intake", "20:45:00", False),
        ("delivery", "20:59:59", True),
        ("delivery", "21:00:00", False),
    ],
)
def test_window_uses_shanghai_and_explicit_recovery(stage, clock, success):
    day = date(2026, 10, 20)
    now = datetime.fromisoformat(f"2026-10-20T{clock}+08:00")
    if success:
        cycle.time_gate(day, stage, False, now)
    else:
        with pytest.raises(TimeoutError):
            cycle.time_gate(day, stage, False, now)
    cycle.time_gate(day, stage, True, now)
    cycle.time_gate(date(2026, 10, 8), stage, False, now)
    with pytest.raises(ValueError, match="FUTURE"):
        cycle.time_gate(date(2026, 10, 21), stage, True, now)


def test_replay_publication_preserves_latest_and_disclosed_gaps(tmp_path):
    config = {"root": str(tmp_path)}
    today = {
        "market_date": "2026-10-20",
        "status": "QUALIFIED_1D_NATIVE5M",
        "core": "today-core",
        "product": "today-product",
    }
    older = {
        "market_date": "2026-10-15",
        "status": "CORE_READY_5M_HELD_SOURCE_QUALITY",
        "core": "older-core",
    }
    cycle.publish(config, today)
    cycle.publish(config, older)
    for filename in ["CURRENT_DAILY_CORE_BASE24.json", "CURRENT_DAILY_1D5M_BASE24.json"]:
        current = cycle.object_at(tmp_path / filename)
        assert current["market_date"] == "2026-10-20"
        assert current["date_coverage"]["2026-10-15"] == older["status"]
        assert current["execution_authorized"] is False
        assert current["consumer_admission"] == "NOT_GRANTED"


def test_closed_session_acquires_events_without_price_requests(monkeypatch, tmp_path):
    config = {"root": str(tmp_path)}
    run = tmp_path / "run"
    run.mkdir()
    source = tmp_path / "event-source"
    source.mkdir()
    cycle.write(source / "release.json", {})
    calls = []

    def provider(config, run, label, action, *a, **kw):
        calls.append((action, a))
        if action == "calendar":
            (run / "calendar").mkdir()
            cycle.write(run / "calendar/calendar.json", {})
            return {"dates": {"2026-10-09": True, "2026-10-10": False}}
        if action == "events":
            return {"status": "ready", "release_dir": str(source)}
        assert action == "universe"
        return {}

    monkeypatch.setattr(cycle, "provider", provider)
    result = cycle.collect(config, run, date(2026, 10, 10))
    assert result["status"] == "AUTHORITATIVE_CLOSED_EVENT_SOURCE_READY"
    assert result["price_provider_requests"] == 0
    assert [a for a, _ in calls] == ["calendar", "universe", "events"]
    assert "2026-10-09" in calls[2][1]


@pytest.mark.parametrize("previous_policy", [None, "previous-frozen-policy"])
def test_catalog_must_match_both_exact_frozen_sources_before_core(
    monkeypatch, tmp_path, previous_policy
):
    if previous_policy is None:
        monkeypatch.delenv("HERMES_ODS_PREVENTION_POLICY", raising=False)
    else:
        monkeypatch.setenv("HERMES_ODS_PREVENTION_POLICY", previous_policy)
    day = date(2026, 10, 20)
    market, events = [tmp_path / "sources/daily" / str(day) / x for x in ["market", "events"]]
    for root in [market, events]:
        root.mkdir(parents=True)
        cycle.write(root / "release.json", {})
    run = tmp_path / "run"
    run.mkdir()
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    cycle.write(
        catalog / "catalog.json",
        {"lanes": [{"lane": "market_daily_core", "release_manifest_sha256": "another-revision"}]},
    )
    monkeypatch.setattr(cycle, "resources", lambda *a, **kw: {})
    monkeypatch.setattr(
        cycle, "build_daily_ods_catalog", lambda **kw: types.SimpleNamespace(root=catalog)
    )
    monkeypatch.setattr(
        cycle, "build_daily_domain_increment", lambda **kw: pytest.fail("core must not run")
    )
    with pytest.raises(ValueError, match="ANOTHER_REVISION"):
        cycle.assemble({}, run, day, market, events)
    assert os.environ.get("HERMES_ODS_PREVENTION_POLICY") == previous_policy
    assert not (run / "core-receipt.json").exists()
    assert not (tmp_path / "DATES.json").exists()


def test_r3_keeps_core_published_without_composite_or_full_ready(monkeypatch, tmp_path):
    monkeypatch.delenv("HERMES_ODS_PREVENTION_POLICY", raising=False)
    day = date(2026, 10, 20)
    market, events = [tmp_path / "sources/daily" / str(day) / x for x in ["market", "events"]]
    for root in [market, events]:
        root.mkdir(parents=True)
        cycle.write(root / "release.json", {})
    run, catalog, core, native = [tmp_path / x for x in ["run", "catalog", "core", "native"]]
    for root in [run, catalog, core, native]:
        root.mkdir()
    cycle.write(
        catalog / "catalog.json",
        {
            "lanes": [
                {"lane": lane, "release_manifest_sha256": cycle.file_sha(source / "release.json")}
                for lane, source in [
                    ("market_daily_core", market),
                    ("calendar_daily_events", events),
                ]
            ]
        },
    )
    cycle.write(native / "manifest.json", {"research_ready": False, "grades": {"R3": 1}})
    config = {
        "root": str(tmp_path),
        "base": str(tmp_path / "base"),
        "release_config": str(tmp_path / "policy"),
        "pipeline_revision": "a" * 40,
        "base_manifest_sha256": "base",
        "release_config_sha256": "policy",
    }
    monkeypatch.setattr(cycle, "resources", lambda *a, **kw: {})
    monkeypatch.setattr(cycle, "prior", lambda *a: None)
    monkeypatch.setattr(
        cycle, "build_daily_ods_catalog", lambda **kw: types.SimpleNamespace(root=catalog)
    )
    monkeypatch.setattr(
        cycle,
        "build_daily_domain_increment",
        lambda **kw: types.SimpleNamespace(
            root=core, manifest_sha256="core", row_counts={"table": 1}
        ),
    )
    monkeypatch.setattr(cycle, "build", lambda **kw: pytest.fail("R3 must not enter composite"))
    result = cycle.assemble(config, run, day, market, events, native)
    assert "HERMES_ODS_PREVENTION_POLICY" not in os.environ
    assert result["status"] == "CORE_READY_5M_HELD_SOURCE_QUALITY"
    assert (tmp_path / "CURRENT_DAILY_CORE_BASE24.json").is_file()
    assert not (tmp_path / "CURRENT_DAILY_1D5M_BASE24.json").exists()
    assert not (run / "READY").exists()
    assert len(list((tmp_path / "alerts").iterdir())) == 1


def test_source_overlap_is_rejected_before_any_lock_or_run(monkeypatch, tmp_path):
    marker = tmp_path / "original.bin"
    marker.write_bytes(b"original-source")
    monkeypatch.setattr(cycle, "load", lambda p: {"root": str(tmp_path)})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cycle",
            "--config",
            str(tmp_path / "config"),
            "--day",
            "2026-10-08",
            "--market",
            str(tmp_path),
            "--events",
            str(tmp_path),
        ],
    )
    with pytest.raises(SourceAssessmentError, match="OVERLAP"):
        cycle.main()
    assert list(tmp_path.iterdir()) == [marker]
    assert marker.read_bytes() == b"original-source"

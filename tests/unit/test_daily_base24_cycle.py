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


@pytest.mark.parametrize("action", ["reference", "capture"])
def test_provider_native_boundary_preserves_each_pinned_date_type(monkeypatch, tmp_path, action):
    """The pinned reference uses ISO strings; its capture API uses date objects."""
    # main is a subprocess entry point. Isolate its process-wide policy and SDK
    # import here so this synthetic boundary test cannot affect later tests.
    monkeypatch.setenv("HERMES_ODS_PREVENTION_POLICY", "synthetic-boundary-only")
    monkeypatch.setitem(sys.modules, "baostock", types.ModuleType("baostock"))
    common = types.ModuleType("quanthus.pipelines.p10_delivery_common")
    common.file_digest = lambda path: "sha256:" + "a" * 64
    delivery = types.ModuleType("quanthus.pipelines.p10_native_minute_delivery")

    def freeze(release, day, output, *, events_sha):
        assert type(day) is str and day == "2026-10-20"
        return {"reference_digest": "sha256:" + "b" * 64, "market_date": day, "population": 1}

    delivery.freeze_reference = freeze
    delivery.reference = lambda path: {
        "securities": {"600000": {}},
        "source_ods_manifest_sha256": "sha256:" + "a" * 64,
    }
    delivery.build = lambda **kwargs: pytest.fail("unexpected native build")
    capture = types.ModuleType("quanthus.providers.p10_native_m5_shard_capture")

    def shard(**kwargs):
        assert type(kwargs["market_date"]) is date
        assert kwargs["market_date"] == date(2026, 10, 20)
        return {"errors": 0}

    capture.capture_shard = shard
    for module in [common, delivery, capture]:
        monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(worker, "runtime", lambda path: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "worker",
            action,
            "--runtime",
            str(tmp_path),
            "--day",
            "2026-10-20",
            "--market",
            str(tmp_path / "market"),
            "--events",
            str(tmp_path / "events"),
            "--reference",
            str(tmp_path / "reference"),
            "--output",
            str(tmp_path / "output"),
            "--shard-index",
            "0",
        ],
    )
    assert worker.main() == 0


@pytest.mark.parametrize(
    "error,rows", [("0", [["exact", "12.34000000000"]]), ("provider_error", [])]
)
def test_native_client_paces_and_retains_actual_success_or_error(
    monkeypatch, tmp_path, error, rows
):
    now = [0.0]
    sleeps, captures, queries = [], [], []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    def query(*args, **kwargs):
        queries.append((args, kwargs))
        iterator = iter(rows)
        response = types.SimpleNamespace(fields=["f1", "f2"], error_code=error, error_msg="message")
        response.next = lambda: (
            setattr(response, "row", next(iterator, None)) or response.row is not None
        )
        response.get_row_data = lambda: response.row
        return response

    evidence = types.ModuleType("quanthus.providers.acquisition_evidence")
    evidence.clock = lambda: "2026-10-20T13:30:00+00:00"
    evidence.capture = lambda **kw: captures.append(kw)
    monkeypatch.setitem(sys.modules, evidence.__name__, evidence)
    monkeypatch.setattr(worker.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(worker.time, "sleep", sleep)
    client = worker.NativeCaptureClient(
        types.SimpleNamespace(query_history_k_data_plus=query), tmp_path
    )
    for _ in range(2):
        response = client.query_history_k_data_plus("sz.000001", "f1,f2", frequency="5")
        observed = []
        while str(response.error_code) == "0" and response.next():
            observed.append(response.get_row_data())
        assert observed == rows
    assert sleeps == [1.0] and len(queries) == 2  # No hidden retries.
    for item in captures:
        assert json.loads(item["raw"])["rows"] == rows
        assert json.loads(item["raw"])["error_code"] == error
        assert item["started_at"] == item["completed_at"] == evidence.clock()
        assert item["root"] == tmp_path


@pytest.mark.parametrize("second_error", ["0", "10001001"])
def test_native_expired_session_retains_failure_and_retries_only_once(
    monkeypatch, tmp_path, second_error
):
    from collections import deque

    responses = deque(["10001001", second_error])
    observations, logins = [], []

    def query(*args, **kwargs):
        error = responses.popleft()
        return types.SimpleNamespace(
            fields=[], error_code=error, error_msg="original SDK state", next=lambda: False
        )

    client = types.SimpleNamespace(
        query_history_k_data_plus=query,
        login=lambda: logins.append(True) or types.SimpleNamespace(error_code="0"),
    )
    evidence = types.ModuleType("quanthus.providers.acquisition_evidence")
    evidence.clock = lambda: "2026-10-20T13:30:00+00:00"
    evidence.capture = lambda **kw: observations.append(json.loads(kw["raw"])["error_code"])
    monkeypatch.setitem(sys.modules, evidence.__name__, evidence)
    timeouts = []
    transport = types.ModuleType("quanthus.providers.baostock_transport")
    transport.set_socket_timeout = lambda seconds: timeouts.append(seconds) or True
    monkeypatch.setitem(sys.modules, transport.__name__, transport)
    monkeypatch.setattr(worker.time, "sleep", lambda seconds: None)
    response = worker.NativeCaptureClient(client, tmp_path).query_history_k_data_plus(
        "sz.000001", "date,volume", frequency="5"
    )
    assert response.error_code == second_error
    assert observations == ["10001001", second_error]
    assert len(logins) == 1 and not responses
    assert timeouts == [12]  # login creates a new socket; restore its read deadline.


@pytest.mark.parametrize("first_error", ["10002007", "0"])
@pytest.mark.parametrize("second_valid", [True, False])
def test_native_network_or_wrong_security_reconnects_once(
    monkeypatch, tmp_path, first_error, second_valid
):
    from collections import deque

    responses = deque(
        [(first_error, "sh.600002"), ("0", "sh.600001" if second_valid else "sh.600003")]
    )
    captured, logins, timeouts = [], [], []

    def query(*args, **kwargs):
        error, code = responses.popleft()
        iterator = iter([["2026-10-19", code]])
        response = types.SimpleNamespace(fields=["date", "code"], error_code=error, error_msg="SDK")
        response.next = lambda: (
            setattr(response, "row", next(iterator, None)) or response.row is not None
        )
        response.get_row_data = lambda: response.row
        return response

    client = types.SimpleNamespace(
        query_history_k_data_plus=query,
        login=lambda: logins.append(True) or types.SimpleNamespace(error_code="0"),
    )
    evidence = types.ModuleType("quanthus.providers.acquisition_evidence")
    evidence.clock = lambda: "2026-10-20T14:00:00+00:00"
    evidence.capture = lambda **kw: captured.append(kw)
    monkeypatch.setitem(sys.modules, evidence.__name__, evidence)
    transport = types.ModuleType("quanthus.providers.baostock_transport")
    transport.set_socket_timeout = lambda seconds: timeouts.append(seconds) or True
    monkeypatch.setitem(sys.modules, transport.__name__, transport)
    monkeypatch.setattr(worker.time, "sleep", lambda seconds: None)
    response = worker.NativeCaptureClient(client, tmp_path).query_history_k_data_plus(
        "sh.600001", "date,code", frequency="d"
    )
    assert not responses and logins == [True] and timeouts == [12]
    assert response.error_code == ("0" if second_valid else "DAILY_NATIVE_SECURITY_MISMATCH")
    assert len(captured) == 2
    # The SDK said success for a wrong identity; preserve that exact observation.
    assert json.loads(captured[0]["raw"])["error_code"] == first_error
    assert captured[0]["status"] == first_error
    if first_error == "0":
        assert json.loads(captured[0]["raw"])["rows"] == [["2026-10-19", "sh.600002"]]


def test_native_exhausted_network_retry_reconnects_before_next_security(monkeypatch, tmp_path):
    from collections import deque

    states = deque(["10002007", "10002007", "0"])
    events = []

    def query(*args, **kwargs):
        code = states.popleft()
        events.append("query:" + code)
        return types.SimpleNamespace(
            fields=[], error_code=code, error_msg="SDK", next=lambda: False
        )

    client = types.SimpleNamespace(
        query_history_k_data_plus=query,
        login=lambda: events.append("login") or types.SimpleNamespace(error_code="0"),
    )
    evidence = types.ModuleType("quanthus.providers.acquisition_evidence")
    evidence.clock = lambda: "2026-10-20T14:00:00+00:00"
    evidence.capture = lambda **kw: None
    monkeypatch.setitem(sys.modules, evidence.__name__, evidence)
    transport = types.ModuleType("quanthus.providers.baostock_transport")
    transport.set_socket_timeout = lambda seconds: seconds == 12
    monkeypatch.setitem(sys.modules, transport.__name__, transport)
    monkeypatch.setattr(worker.time, "sleep", lambda seconds: None)
    observed = worker.NativeCaptureClient(client, tmp_path)
    assert observed.query_history_k_data_plus("sh.600001", "date,code").error_code == "10002007"
    assert observed.query_history_k_data_plus("sh.600002", "date,code").error_code == "0"
    assert events == ["query:10002007", "login", "query:10002007", "login", "query:0"]


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


def test_market_budget_matches_measured_full_universe_and_remains_bounded(monkeypatch, tmp_path):
    calls = []

    def provider(config, run, label, action, *args, **kwargs):
        calls.append((action, args, kwargs))
        if action == "calendar":
            return {"dates": {"2026-10-20": True}}
        return {"status": "ready", "release_dir": str(tmp_path / action)}

    monkeypatch.setattr(cycle, "provider", provider)
    monkeypatch.setattr(cycle, "assemble", lambda *args: {"completed": True})
    assert cycle.collect({"root": str(tmp_path)}, tmp_path, date(2026, 10, 20)) == {
        "completed": True
    }
    market = next(call for call in calls if call[0] == "market")
    assert market[1][-2:] == ("--budget", "7200")
    assert market[2]["timeout"] == 7300


def test_busy_date_timer_keeps_active_writer_without_attempt_or_pointer_change(
    monkeypatch, tmp_path, capsys
):
    import fcntl

    marker = tmp_path / "DATES.json"
    marker.write_text("retained-state")
    day = "2026-10-08"
    lock_path = tmp_path / (day + ".lock")
    monkeypatch.setattr(cycle, "load", lambda p: {"root": str(tmp_path)})
    monkeypatch.setattr(sys, "argv", ["cycle", "--config", "config.json", "--day", day])
    monkeypatch.setattr(
        cycle, "resources", lambda *a, **kw: pytest.fail("busy date must not start")
    )
    with lock_path.open("a+") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert cycle.main() == 0
    assert json.loads(capsys.readouterr().out) == {
        "status": "DATE_WRITER_ALREADY_ACTIVE",
        "market_date": day,
    }
    assert sorted(p.name for p in tmp_path.iterdir()) == [day + ".lock", "DATES.json"]
    assert marker.read_text() == "retained-state"


@pytest.mark.parametrize(
    "mutation",
    [None, "failure", "capture", "reference", "base", "policy", "source", "core", "date", "repeat"],
)
def test_reference_failure_resumes_only_verified_core_before_any_new_write(
    monkeypatch, tmp_path, mutation
):
    day = date(2026, 10, 20)
    previous = tmp_path / (str(day) + "-previous")
    previous.mkdir()
    core = previous / "core"
    core.mkdir()
    cycle.write(core / "manifest.json", {})
    market, events = [tmp_path / "sources/daily" / str(day) / x for x in ["market", "events"]]
    for path in [market, events]:
        path.mkdir(parents=True)
        cycle.write(path / "release.json", {})
    config = {
        "root": str(tmp_path),
        "base_manifest_sha256": "base",
        "release_config_sha256": "policy",
        "pipeline_revision": "b" * 40,
    }
    old = {
        "market_date": str(day),
        "status": "CORE_READY_5M_FAILED",
        "core": str(core),
        "core_manifest_sha256": cycle.file_sha(core / "manifest.json"),
        "base_manifest_sha256": "base",
        "config_sha256": "policy",
        "source_manifest_sha256": {
            "market_daily_core": cycle.file_sha(market / "release.json"),
            "calendar_daily_events": cycle.file_sha(events / "release.json"),
        },
    }
    failure = {"reason": "DAILY_CYCLE_PROVIDER_FAILED_reference"}
    if mutation == "failure":
        failure["reason"] = "different"
    if mutation == "capture":
        (previous / "capture-5m").mkdir()
    if mutation == "reference":
        cycle.write(previous / "daily-reference.json", {})
    if mutation == "base":
        old["base_manifest_sha256"] = "changed"
    if mutation == "policy":
        old["config_sha256"] = "changed"
    if mutation == "date":
        old["market_date"] = "2026-10-19"
    if mutation == "source":
        (market / "release.json").write_text('{"changed": true}')
    if mutation == "repeat":
        attempted = tmp_path / (str(day) + "-attempted")
        attempted.mkdir()
        cycle.write(attempted / "native-continuation.json", {})
    cycle.write(previous / "core-receipt.json", old)
    cycle.write(previous / "failure.json", failure)
    cycle.write(previous / "market-source.json", {"release_dir": str(market)})
    cycle.write(previous / "events-source.json", {"release_dir": str(events)})
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    def verify(path):
        assert path == core
        if mutation == "core":
            raise ValueError("changed core")
        return types.SimpleNamespace(manifest_sha256=old["core_manifest_sha256"])

    monkeypatch.setattr(cycle, "verify_daily_domain_increment", verify)
    if mutation:
        with pytest.raises(ValueError):
            cycle.reference_continuation(config, day, old)
    else:
        result = cycle.reference_continuation(config, day, old)
        assert result == (old, market, events)
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_provider_uses_its_own_project_root(monkeypatch, tmp_path):
    collector = tmp_path / "collector"
    collector.mkdir()

    def popen(command, **kwargs):
        assert kwargs["cwd"] == collector
        assert kwargs["env"]["PYTHONPATH"] == str(collector / "src")
        kwargs["stdout"].write(b'{"ok": true}\n')
        return types.SimpleNamespace(poll=lambda: 0, returncode=0)

    monkeypatch.setattr(cycle.subprocess, "Popen", popen)
    assert cycle.provider({"collector": str(collector)}, tmp_path, "probe", "reference") == {
        "ok": True
    }


@pytest.mark.parametrize("eligible", [True, False])
def test_main_keeps_two_full_attempt_gate_but_resumes_reference_once(
    monkeypatch, tmp_path, eligible, capsys
):
    day = date(2026, 10, 20)
    old = {
        "status": "CORE_READY_5M_FAILED" if eligible else "failed",
        "core": str(tmp_path / "old/core"),
    }
    cycle.write(tmp_path / "DATES.json", {"dates": {str(day): old}})
    for index in range(2):
        previous = tmp_path / (str(day) + f"-old-{index}")
        previous.mkdir()
        cycle.write(previous / "registration.json", {})
    config = {"root": str(tmp_path), "pipeline_revision": "b" * 40}
    monkeypatch.setattr(cycle, "load", lambda p: config)
    monkeypatch.setattr(cycle, "resources", lambda *a, **kw: {})
    monkeypatch.setattr(cycle, "time_gate", lambda *a: None)
    monkeypatch.setattr(cycle, "collect", lambda *a: pytest.fail("no full recollection"))
    monkeypatch.setattr(
        cycle, "build_daily_domain_increment", lambda **kw: pytest.fail("no core rebuild")
    )
    value = {
        "core": old["core"],
        "core_manifest_sha256": "core",
        "source_manifest_sha256": {"market": "market"},
    }
    monkeypatch.setattr(
        cycle,
        "reference_continuation",
        lambda *a: (dict(value), tmp_path / "market", tmp_path / "events"),
    )
    monkeypatch.setattr(cycle, "finish_native", lambda *a: {"status": "continued"})
    monkeypatch.setattr(
        sys, "argv", ["cycle", "--config", str(tmp_path / "DATES.json"), "--day", str(day)]
    )
    if eligible:
        assert cycle.main() == 0
        assert json.loads(capsys.readouterr().out)["result"] == {"status": "continued"}
        assert len(list(tmp_path.glob(str(day) + "-*/native-continuation.json"))) == 1
    else:
        with pytest.raises(ValueError, match="TWO_ATTEMPT_BUDGET"):
            cycle.main()
        assert not list(tmp_path.glob(str(day) + "-*/native-continuation.json"))

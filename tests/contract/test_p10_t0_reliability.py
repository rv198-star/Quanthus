import runpy
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from quanthus.pipelines import p10_acquisition_continuation as continuation
from quanthus.pipelines import p10_complete_daily_notifications as notices
from quanthus.pipelines.p10_delivery_common import atomic, digest, encoded

DAY = date(2026, 10, 14)
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def operating(tmp_path, monkeypatch):
    monkeypatch.setenv("INVOCATION_ID", "c" * 32)
    monkeypatch.setenv("QUANTHUS_DWD_BASE", str(tmp_path / "base"))
    monkeypatch.setenv("QUANTHUS_V2_RELEASE_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("HERMES_ODS_CALENDAR", str(tmp_path / "absent_sealed_calendar"))
    monkeypatch.setattr(continuation, "JOURNAL", tmp_path / "journal")
    calls = []
    monkeypatch.setattr(continuation, "failure", lambda **kw: calls.append(kw) or {})
    return tmp_path / "state", calls


def cache(day, rows, **changes):
    value = {
        "market_date": str(day),
        "provider": "baostock-trading-calendar",
        "rows": rows,
        "rows_sha256": digest(encoded(rows)),
        "fetched_at": datetime.now(continuation.CST).isoformat(),
    }
    value.update(changes)
    atomic(continuation.JOURNAL / "calendars" / f"{day}.json", value)


def test_trading_day_uses_saved_operating_calendar_not_sealed_base(operating, monkeypatch):
    state, calls = operating
    cache(DAY, [{"trade_date": str(DAY), "is_open": "1"}])
    monkeypatch.setattr(
        continuation.subprocess,
        "run",
        lambda *a, **kw: pytest.fail("no acquisition on trading date"),
    )
    result = continuation.run(mode="natural-day", day=DAY, revision="a" * 40, state=state)
    assert result["status"] == "handled_by_trading_day_daily_core"
    assert result["calendar_authority"]["provider"] == "baostock-trading-calendar"
    assert (state / "active/natural-day.json").exists()
    assert (state / "runs/natural-day/2026-10-14.json").exists()
    assert not calls


@pytest.mark.parametrize("fault", ["missing", "duplicate", "digest", "provider", "future"])
def test_operating_calendar_fails_closed(operating, fault):
    state, calls = operating
    rows = [{"trade_date": str(DAY), "is_open": "1"}]
    changes = {}
    if fault == "missing":
        rows[0]["trade_date"] = "2026-10-13"
    if fault == "duplicate":
        rows += rows
    if fault == "digest":
        changes["rows_sha256"] = "sha256:" + "0" * 64
    if fault == "provider":
        changes["provider"] = "unknown"
    if fault == "future":
        changes["fetched_at"] = (datetime.now(continuation.CST) + timedelta(hours=1)).isoformat()
    cache(DAY, rows, **changes)
    with pytest.raises(ValueError):
        continuation.run(mode="natural-day", day=DAY, revision="a" * 40, state=state)
    assert len(calls) == 1 and calls[0]["day"] == DAY


def test_nontrading_date_last_source_comes_from_calendar_not_weekday(operating, monkeypatch):
    state, calls = operating
    day = date(2026, 10, 17)
    cache(
        day,
        [
            {"trade_date": "2026-10-14", "is_open": "1"},
            {"trade_date": "2026-10-15", "is_open": "0"},
            {"trade_date": "2026-10-16", "is_open": "0"},
            {"trade_date": str(day), "is_open": "0"},
        ],
    )

    def intercept(command, **kw):
        assert kw["env"]["HERMES_ODS_UNIVERSE_SOURCE_DATE"] == "2026-10-14"
        assert command[-1] == "2026-10-17"
        raise RuntimeError("synthetic stop before any supplier request")

    monkeypatch.setattr(continuation.subprocess, "run", intercept)
    with pytest.raises(RuntimeError):
        continuation.run(mode="natural-day", day=day, revision="a" * 40, state=state)
    assert len(calls) == 1


@pytest.mark.parametrize("mode", ["daily", "weekly", "natural-day"])
def test_current_invocation_failure_target_is_exact_and_same_mail_key(tmp_path, monkeypatch, mode):
    now = datetime.now(continuation.CST)
    monkeypatch.setattr(notices, "_service_started_at", lambda unit: now.timestamp() - 2)
    monkeypatch.setattr(notices, "_service_invocation_id", lambda unit: "c" * 32)
    atomic(
        tmp_path / "active" / f"{mode}.json",
        {
            "mode": mode,
            "market_date": "2026-10-13",
            "started_at": (now - timedelta(seconds=1)).isoformat(),
            "invocation_id": "c" * 32,
        },
    )
    day, label = notices._continuation_failure_target(now, mode, tmp_path)
    assert day == date(2026, 10, 13) and label == "业务日期"
    assert notices._notification_key(
        day, "natural_day", {"market_date_label": label}
    ) == notices._notification_key(day, "natural_day", {"market_date_label": "业务日期"})


def test_stale_active_target_does_not_merge_unresolved_failure(tmp_path, monkeypatch):
    now = datetime.now(continuation.CST)
    monkeypatch.setattr(notices, "_service_started_at", lambda unit: now.timestamp() - 2)
    monkeypatch.setattr(notices, "_service_invocation_id", lambda unit: "c" * 32)
    atomic(
        tmp_path / "active/natural-day.json",
        {
            "mode": "natural-day",
            "market_date": "2026-10-13",
            "started_at": (now - timedelta(days=1)).isoformat(),
            "invocation_id": "c" * 32,
        },
    )
    day, label = notices._continuation_failure_target(now, "natural-day", tmp_path)
    assert day == now.date() and label.startswith("运行日期")
    assert notices._notification_key(
        day, "natural_day", {"market_date_label": label}
    ) != notices._notification_key(day, "natural_day", {})


def test_internal_and_failure_hook_send_only_once(operating, monkeypatch):
    state, calls = operating
    cache(DAY, [{"trade_date": "2026-10-13", "is_open": "1"}])
    with pytest.raises(ValueError):
        continuation.run(mode="natural-day", day=DAY, revision="a" * 40, state=state)
    now = datetime.now(continuation.CST)
    monkeypatch.setattr(notices, "_service_started_at", lambda unit: now.timestamp() - 5)
    monkeypatch.setattr(notices, "_service_invocation_id", lambda unit: "c" * 32)
    day, label = notices._continuation_failure_target(now, "natural-day", state)
    sent = []
    monkeypatch.setattr(
        notices.mail, "_send_resend", lambda **kw: sent.append(kw) or {"message_id": "synthetic"}
    )
    notices.failure(
        day=DAY,
        stage="natural_day",
        state_root=state / "notifications",
        reason="internal",
        context=calls[0]["context"],
    )
    notices.failure(
        day=day,
        stage="natural_day",
        state_root=state / "notifications",
        reason="hook",
        context={"market_date_label": label},
    )
    assert len(sent) == 1


def retry_fixture(tmp_path, monkeypatch):
    ns = runpy.run_path(str(ROOT / "ops/bin/run-cn01-ods-daily"), run_name="test_module")
    helper = ns["_core_event_dependency_for_retry"]
    monkeypatch.setenv("HERMES_ODS_PREVENTION_POLICY", "p10-acquisition-prevention-v1")
    monkeypatch.delenv("HERMES_ODS_UNIVERSE_SOURCE_DATE", raising=False)
    root = tmp_path / "source-universe-v1/2026-10-14"
    root.mkdir(parents=True)
    (root / "universe.json").write_text("{}\n")
    log = tmp_path / "event.log"
    log.write_text("ValueError: UNIVERSE_PROVIDER_QUERY_FAILED\n")
    state = {
        "lanes": {
            "market-daily-core": {"status": "ready"},
            "calendar-daily-events": {
                "status": "failed",
                "attempts": 1,
                "returncode": 1,
                "logs": [str(log)],
            },
        }
    }
    from quanthus.providers import security_universe

    checked = []
    monkeypatch.setattr(
        security_universe, "verify", lambda path, day: checked.append((path, day)) or {}
    )
    return helper, state, root, log, checked


def test_core_retry_waits_for_verified_current_day_dependency(tmp_path, monkeypatch):
    helper, state, root, log, checked = retry_fixture(tmp_path, monkeypatch)
    result = helper(
        state, is_core=True, home=tmp_path, market_date=DAY, deadline=time.monotonic() + 60
    )
    assert result["source_day"] == str(DAY) and result["budget_reset"] is False
    assert checked == [(root, DAY)] and result["initial_attempt"]["returncode"] == 1
    state["lanes"]["calendar-daily-events"]["logs"].append("next.log")
    assert result["initial_attempt"]["logs"] == [str(log)]


@pytest.mark.parametrize(
    "fault",
    [
        "deadline",
        "not_core",
        "market_failed",
        "cleanup",
        "already_retried",
        "other_error",
        "wrong_source_date",
        "missing_dependency",
    ],
)
def test_core_retry_rejects_unsafe_or_unrelated_restarts(tmp_path, monkeypatch, fault):
    helper, state, root, log, checked = retry_fixture(tmp_path, monkeypatch)
    deadline = time.monotonic() + 60
    core = True
    if fault == "deadline":
        deadline = time.monotonic() - 1
    if fault == "not_core":
        core = False
    if fault == "market_failed":
        state["lanes"]["market-daily-core"]["status"] = "failed"
    if fault == "cleanup":
        state["error_code"] = "process_cleanup_unconfirmed"
    if fault == "already_retried":
        state["lanes"]["calendar-daily-events"]["attempts"] = 2
    if fault == "other_error":
        log.write_text("ValueError: wrong source identity\n")
    if fault == "wrong_source_date":
        monkeypatch.setenv("HERMES_ODS_UNIVERSE_SOURCE_DATE", "2026-10-13")
    if fault == "missing_dependency":
        (root / "universe.json").unlink()
    assert helper(state, is_core=core, home=tmp_path, market_date=DAY, deadline=deadline) is None
    assert not checked


def test_corrupt_dependency_blocks_retry(tmp_path, monkeypatch):
    helper, state, root, log, checked = retry_fixture(tmp_path, monkeypatch)
    from quanthus.providers import security_universe

    def reject(*args):
        raise ValueError("synthetic SHA failure")

    monkeypatch.setattr(security_universe, "verify", reject)
    with pytest.raises(ValueError):
        helper(state, is_core=True, home=tmp_path, market_date=DAY, deadline=time.monotonic() + 60)


def test_missing_intermediate_nontrading_date_is_rejected(operating):
    state, calls = operating
    day = date(2026, 10, 17)
    cache(
        day,
        [
            {"trade_date": "2026-10-14", "is_open": "1"},
            {"trade_date": "2026-10-16", "is_open": "0"},
            {"trade_date": str(day), "is_open": "0"},
        ],
    )
    with pytest.raises(ValueError, match="INTERMEDIATE_CALENDAR"):
        continuation.run(mode="natural-day", day=day, revision="a" * 40, state=state)


@pytest.mark.parametrize(
    "marker_id,service_id", [(None, "c" * 32), ("b" * 32, "c" * 32), ("c" * 32, None)]
)
def test_other_or_missing_invocation_cannot_supply_target(
    tmp_path, monkeypatch, marker_id, service_id
):
    now = datetime.now(continuation.CST)
    monkeypatch.setattr(notices, "_service_started_at", lambda unit: now.timestamp() - 2)
    monkeypatch.setattr(notices, "_service_invocation_id", lambda unit: service_id)
    atomic(
        tmp_path / "active/natural-day.json",
        {
            "mode": "natural-day",
            "market_date": "2026-10-13",
            "started_at": now.isoformat(),
            "invocation_id": marker_id,
        },
    )
    day, label = notices._continuation_failure_target(now, "natural-day", tmp_path)
    assert day == now.date() and label.startswith("运行日期")


@pytest.mark.parametrize(
    "outcome", ["success", "start_error", "cleanup_error", "verify_error", "deadline_after_verify"]
)
def test_recovery_keeps_terminal_evidence_and_original_deadline(tmp_path, monkeypatch, outcome):
    from types import SimpleNamespace

    ns = runpy.run_path(str(ROOT / "ops/bin/run-cn01-ods-daily"), run_name="synthetic_ops")
    recover = ns["_recover_core_event"]
    g = recover.__globals__
    state = {
        "lanes": {
            "market-daily-core": {"status": "ready"},
            "calendar-daily-events": {
                "status": "failed",
                "returncode": 1,
                "attempts": 1,
                "logs": ["first.log"],
            },
        }
    }
    job = ns["LaneJob"](
        lane="calendar-daily-events", command=[], log_root=tmp_path, attempt=1, logs=["first.log"]
    )
    clock = [100.0]
    deadline = 200.0
    ran = []
    monkeypatch.setitem(g, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def dependency(*a, **kw):
        assert kw["deadline"] == deadline
        if outcome == "verify_error":
            raise ValueError("synthetic dependency SHA failure")
        if outcome == "deadline_after_verify":
            clock[0] = 201.0
        return {
            "status": "one_retry_with_current_day_dependency",
            "initial_attempt": {**state["lanes"]["calendar-daily-events"], "logs": ["first.log"]},
            "budget_reset": False,
        }

    monkeypatch.setitem(g, "_core_event_dependency_for_retry", dependency)

    def run_group(jobs, **kw):
        assert jobs == [job] and kw["deadline"] == deadline
        ran.append(True)
        job.attempt = 2
        job.logs.append("second.log")
        if outcome == "start_error":
            raise OSError("synthetic start failure")
        if outcome == "cleanup_error":
            raise ns["ProcessGroupCleanupError"]("synthetic cleanup failure")
        return {job.lane: {"status": "ready", "returncode": 0, "attempts": 2, "logs": job.logs}}

    monkeypatch.setitem(g, "_run_group", run_group)
    recover(
        state,
        is_core=True,
        home=tmp_path,
        market_date=DAY,
        deadline=deadline,
        daily_group=[job],
        poll_seconds=0.1,
    )
    assert state["lanes"]["market-daily-core"] == {"status": "ready"}
    if outcome == "success":
        assert state["lanes"][job.lane]["status"] == "ready"
    elif outcome == "cleanup_error":
        assert state["error_code"] == "process_cleanup_unconfirmed"
        assert state["lanes"][job.lane]["returncode"] == ns["PROCESS_CLEANUP_FAILED"]
    elif outcome == "start_error":
        assert (
            state["lanes"][job.lane]["status"] == "failed"
            and state["lanes"][job.lane]["attempts"] == 2
        )
    elif outcome == "verify_error":
        assert (
            not ran
            and state["core_event_dependency_recovery"]["status"]
            == "dependency_reverification_failed"
        )
    else:
        assert (
            not ran
            and state["core_event_dependency_recovery"]["status"]
            == "deadline_passed_after_dependency_verification"
        )
    if outcome not in ["verify_error", "deadline_after_verify"]:
        assert state["core_event_dependency_recovery"]["initial_attempt"]["logs"] == ["first.log"]

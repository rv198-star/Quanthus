import json
import sys
from datetime import date, datetime, timedelta

import pytest

from quanthus.pipelines import p10_complete_daily_cycle as cycle
from quanthus.pipelines import p10_complete_daily_notifications as notices
from quanthus.pipelines.p10_delivery_common import atomic

REVISION = "a" * 40
INVOCATION = "b" * 32


@pytest.fixture
def activity(tmp_path, monkeypatch):
    now = datetime.now(cycle.CST)
    monkeypatch.setenv("INVOCATION_ID", INVOCATION)
    monkeypatch.setattr(notices, "_service_started_at", lambda unit: now.timestamp() - 2)
    monkeypatch.setattr(notices.subprocess, "check_output", lambda *args, **kw: INVOCATION)
    value = {
        "kind": "p10_complete_daily_cycle_activity_v1",
        "policy_id": cycle.POLICY_ID,
        "invocation_id": INVOCATION,
        "started_at": (now - timedelta(seconds=1)).isoformat(),
        "market_date": "2026-10-13",
        "code_revision": REVISION,
        "stage": "minutes",
    }
    atomic(tmp_path / "active-run.json", value)
    return tmp_path, now, value


@pytest.mark.parametrize("stage", ["minutes", "complete"])
def test_hook_resolves_exact_current_target_and_stage(activity, stage):
    root, now, value = activity
    atomic(root / "active-run.json", {**value, "stage": stage})
    assert notices._complete_cycle_failure_target(now, root) == (
        date(2026, 10, 13),
        stage,
        "交易日",
    )


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "wrong_invocation",
        "stale",
        "future",
        "naive",
        "kind",
        "policy",
        "code",
        "stage",
        "target_missing",
        "target_invalid",
    ],
)
def test_hook_never_guesses_a_target_or_phase(activity, fault):
    root, now, value = activity
    if fault == "missing":
        (root / "active-run.json").unlink()
    else:
        if fault == "wrong_invocation":
            value["invocation_id"] = "c" * 32
        if fault == "stale":
            value["started_at"] = (now - timedelta(days=1)).isoformat()
        if fault == "future":
            value["started_at"] = (now + timedelta(hours=1)).isoformat()
        if fault == "naive":
            value["started_at"] = now.replace(tzinfo=None).isoformat()
        for key in ["kind", "policy", "code", "stage"]:
            if fault == key:
                value[{"policy": "policy_id", "code": "code_revision"}.get(key, key)] = "wrong"
        if fault == "target_missing":
            value["market_date"] = None
        if fault == "target_invalid":
            value["market_date"] = "not-a-date"
        atomic(root / "active-run.json", value)
    day, stage, label = notices._complete_cycle_failure_target(now, root)
    assert day == now.date() and stage == "minutes" and label.startswith("运行日期")


def test_stage_transition_preserves_invocation_start(activity):
    root, _, value = activity
    result = cycle.cycle_activity(
        root, target=date(2026, 10, 13), revision=REVISION, stage="complete"
    )
    assert result["started_at"] == value["started_at"]
    assert result["stage"] == "complete" and result["invocation_id"] == INVOCATION


def test_new_invocation_replaces_stale_stage(activity, monkeypatch):
    root, _, value = activity
    monkeypatch.setenv("INVOCATION_ID", "d" * 32)
    result = cycle.cycle_activity(root, target=None, revision=REVISION, stage="minutes")
    assert result["market_date"] is None and result["stage"] == "minutes"
    assert result["invocation_id"] == "d" * 32 and result["started_at"] != value["started_at"]


@pytest.mark.parametrize("stage", ["minutes", "complete"])
def test_internal_error_and_outer_hook_use_one_failure_mail(activity, monkeypatch, stage):
    root, now, value = activity
    atomic(root / "active-run.json", {**value, "stage": stage})
    sent = []
    monkeypatch.setattr(
        notices.mail, "_send_resend", lambda **kw: sent.append(kw) or {"message_id": "synthetic"}
    )
    notices.failure(
        day=date(2026, 10, 13),
        stage=stage,
        state_root=root / "notifications",
        reason="internal",
        context={"market_date_label": "交易日"},
    )
    target, actual_stage, label = notices._complete_cycle_failure_target(now, root)
    notices.failure(
        day=target,
        stage=actual_stage,
        state_root=root / "notifications",
        reason="timeout hook",
        context={"market_date_label": label},
    )
    assert len(sent) == 1


def test_cli_failure_hook_routes_exact_stage_without_source_actions(activity, monkeypatch):
    root, _, value = activity
    atomic(root / "active-run.json", {**value, "stage": "complete"})
    monkeypatch.setattr(cycle, "JOURNAL", root)
    monkeypatch.setattr(sys, "argv", ["notices", "--complete-cycle-service-failure"])
    calls = []
    monkeypatch.setattr(notices, "failure", lambda **kw: calls.append(kw) or {})
    assert notices.main() == 0
    assert calls[0]["stage"] == "complete" and calls[0]["day"] == date(2026, 10, 13)


def test_main_general_exception_keeps_complete_stage(activity, monkeypatch):
    root, _, _ = activity
    monkeypatch.setattr(cycle, "JOURNAL", root)
    monkeypatch.setattr(sys, "argv", ["cycle", "--code-revision", REVISION])
    monkeypatch.setattr(cycle, "calendar_rows", lambda *args: [])
    monkeypatch.setattr(cycle, "target_for", lambda *args: date(2026, 10, 13))

    def stopped(**kw):
        cycle.cycle_activity(root, target=date(2026, 10, 13), revision=REVISION, stage="complete")
        raise ValueError("synthetic complete artifact failure")

    monkeypatch.setattr(cycle, "run", stopped)
    calls = []
    monkeypatch.setattr(cycle, "failure", lambda **kw: calls.append(kw) or {})
    with pytest.raises(ValueError):
        cycle.main()
    assert calls[0]["stage"] == "complete" and calls[0]["day"] == date(2026, 10, 13)


def test_calendar_error_remains_unresolved_minutes(activity, monkeypatch):
    root, _, _ = activity
    monkeypatch.setattr(cycle, "JOURNAL", root)
    monkeypatch.setattr(sys, "argv", ["cycle", "--code-revision", REVISION])

    def stopped(*args):
        raise ValueError("synthetic missing calendar")

    monkeypatch.setattr(cycle, "calendar_rows", stopped)
    calls = []
    monkeypatch.setattr(cycle, "failure", lambda **kw: calls.append(kw) or {})
    with pytest.raises(ValueError):
        cycle.main()
    assert calls[0]["stage"] == "minutes"
    assert calls[0]["context"]["market_date_label"].startswith("运行日期")
    assert json.loads((root / "active-run.json").read_text())["market_date"] is None

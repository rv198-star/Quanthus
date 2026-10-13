"""Mail outage recovery is independent of suppliers, publishing and transfer budgets."""

import os
from datetime import date

import pytest

from quanthus.pipelines import p10_complete_daily_notifications as notices


def test_lost_response_replays_identical_request_and_ack_deduplicates(tmp_path, monkeypatch):
    calls = []

    def send(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise TimeoutError("response lost after provider may have accepted mail")
        return {"message_id": "synthetic", "status": "sent"}

    monkeypatch.setattr(notices.mail, "_send_resend", send)
    args = dict(
        day=date(2026, 10, 13),
        stage="complete",
        state_root=tmp_path,
        reason="first failure",
        context={"error_code": "timeout"},
    )
    with pytest.raises(TimeoutError):
        notices.failure(**args)
    pending = next((tmp_path / "pending").glob("*.json"))
    before = pending.read_bytes()
    result = notices.retry_pending(tmp_path)
    assert result["remaining"] == 0
    assert calls[0] == calls[1]
    assert b"first failure" in before
    assert notices.failure(**{**args, "reason": "changed diagnosis"})["status"] == "already_sent"
    assert notices.retry_pending(tmp_path)["outcomes"] == []
    assert len(calls) == 2


@pytest.mark.parametrize("stage", list(notices.EVENTS))
def test_each_stage_queues_before_send_and_preserves_original_body(tmp_path, monkeypatch, stage):
    calls = []

    def unavailable(**kwargs):
        calls.append(kwargs)
        assert len(list((tmp_path / "pending").glob("*.json"))) == 1
        raise ConnectionError("synthetic")

    monkeypatch.setattr(notices.mail, "_send_resend", unavailable)
    args = dict(day=date(2026, 10, 13), stage=stage, state_root=tmp_path, reason="original")
    with pytest.raises(ConnectionError):
        notices.failure(**args)
    with pytest.raises(ConnectionError):
        notices.failure(**{**args, "reason": "updated"})
    assert calls[0] == calls[1]


def test_replay_is_bounded_and_bad_identity_never_sends(tmp_path, monkeypatch):
    from quanthus.pipelines.p10_delivery_common import atomic

    monkeypatch.setattr(notices.mail, "_send_resend", lambda **_: pytest.fail("bad request sent"))
    atomic(
        tmp_path / "pending" / ("0" * 64 + ".json"),
        {
            "market_date": "2026-10-13",
            "stage": "complete",
            "policy_id": notices.POLICY_ID,
            "reason": "synthetic",
            "context": {},
        },
    )
    result = notices.retry_pending(tmp_path, limit=1)
    assert result["outcomes"][0]["status"] == "retry_failed"
    assert result["remaining"] == 1
    for limit in (0, 5):
        with pytest.raises(ValueError):
            notices.retry_pending(tmp_path, limit=limit)


def test_runtime_retry_units_are_independent_and_cover_all_outboxes():
    from quanthus.policy import find_project_root

    units = find_project_root() / "ops/systemd"
    for name in ("quanthus", "oci"):
        timer = (units / f"{name}-complete-daily-mail-retry.timer").read_text()
        service = (units / f"{name}-complete-daily-mail-retry.service").read_text()
        assert "OnCalendar=*-*-* *:00/5:00" in timer
        assert "Persistent=true" in timer
        assert "--retry-state" in service
        assert "run-complete-daily-cycle" not in service
    cn01 = (units / "quanthus-complete-daily-mail-retry.service").read_text()
    assert "gdrive-complete-v1/notifications" in cn01
    assert "complete-daily-v1/notifications" in cn01


def test_four_poison_requests_cannot_starve_new_valid_alert(tmp_path, monkeypatch):
    from quanthus.pipelines.p10_delivery_common import atomic, digest, encoded

    for n in range(4):
        p = tmp_path / "pending" / (str(n) * 64 + ".json")
        atomic(p, {})
        os.utime(p, (n + 1, n + 1))
    day = date(2026, 10, 13)
    stage = "mirror"
    key = digest(encoded([notices.POLICY_ID, str(day), stage]))[7:]
    valid = tmp_path / "pending" / f"{key}.json"
    atomic(
        valid,
        {
            "market_date": str(day),
            "stage": stage,
            "policy_id": notices.POLICY_ID,
            "reason": "synthetic",
            "context": {},
        },
    )
    os.utime(valid, (10, 10))
    calls = []
    monkeypatch.setattr(
        notices.mail, "_send_resend", lambda **kw: calls.append(kw) or {"status": "sent"}
    )
    assert len(notices.retry_pending(tmp_path)["outcomes"]) == 4
    assert calls == []
    notices.retry_pending(tmp_path)
    assert len(calls) == 1 and calls[0]["key"] == key
    assert not valid.exists()


def test_archive_timeout_target_is_fresh_or_explicitly_unresolved(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from quanthus.pipelines.p10_delivery_common import atomic

    now = datetime(2026, 10, 14, 5, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(notices, "_service_started_at", lambda _: now.timestamp() - 60)
    path = tmp_path / "failures" / "source.json"
    atomic(path, {"market_date": "2026-10-13"})
    os.utime(path, (now.timestamp() - 20, now.timestamp() - 20))
    assert notices._archive_failure_target(now, tmp_path) == (date(2026, 10, 13), "交易日")
    os.utime(path, (now.timestamp() - 120, now.timestamp() - 120))
    assert notices._archive_failure_target(now, tmp_path) == (
        now.date(),
        "运行日期（目标交易日未确定）",
    )


@pytest.mark.parametrize("stage", ["gdrive", "daily_cycle", "minutes", "mirror"])
def test_unresolved_execution_date_never_suppresses_real_trade_date(tmp_path, monkeypatch, stage):
    calls = []
    monkeypatch.setattr(
        notices.mail, "_send_resend", lambda **kw: calls.append(kw) or {"status": "sent"}
    )
    args = dict(day=date(2026, 10, 14), stage=stage, state_root=tmp_path, reason="synthetic")
    unknown = {**args, "context": {"market_date_label": "运行日期（目标交易日未确定）"}}
    known = {**args, "context": {"market_date_label": "交易日"}}
    notices.failure(**unknown)
    notices.failure(**known)
    assert len(calls) == 2 and calls[0]["key"] != calls[1]["key"]
    assert notices.failure(**unknown)["status"] == "already_sent"
    assert notices.failure(**known)["status"] == "already_sent"


def test_unresolved_date_pending_replays_the_same_namespace(tmp_path, monkeypatch):
    calls = []

    def send(**kw):
        calls.append(kw)
        if len(calls) == 1:
            raise TimeoutError("synthetic")
        return {"status": "sent"}

    monkeypatch.setattr(notices.mail, "_send_resend", send)
    with pytest.raises(TimeoutError):
        notices.failure(
            day=date(2026, 10, 14),
            stage="gdrive",
            state_root=tmp_path,
            reason="synthetic",
            context={"market_date_label": "运行日期（目标交易日未确定）"},
        )
    assert notices.retry_pending(tmp_path)["remaining"] == 0
    assert calls[0] == calls[1]


def test_watchdog_calendar_failure_marks_unresolved_execution_date(monkeypatch):
    import sys

    from quanthus.pipelines import p10_complete_daily_deadline as watchdog

    calls = []

    def unavailable(*_):
        raise ValueError("synthetic calendar failure")

    monkeypatch.setattr(watchdog, "calendar_rows", unavailable)
    monkeypatch.setattr(watchdog, "failure", lambda **kw: calls.append(kw) or {})
    monkeypatch.setattr(sys, "argv", ["watchdog", "--stage", "complete"])
    with pytest.raises(ValueError):
        watchdog.main()
    call = calls[0]
    assert call["context"]["market_date_label"] == "运行日期（目标交易日未确定）"
    assert notices._notification_key(
        call["day"], call["stage"], call["context"]
    ) != notices._notification_key(call["day"], call["stage"], {})

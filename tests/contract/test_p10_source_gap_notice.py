"""Source debt notices cannot become whole-product pass or suppress faults."""
from copy import deepcopy
from datetime import date, datetime

import pytest

from quanthus import warehouse_notifications as mail
from quanthus.pipelines import p10_complete_daily_notifications as notices
from quanthus.pipelines import p10_notification_trial as trial
from quanthus.pipelines.p10_delivery_common import atomic, object_at


def entries():
    return [{"dataset": name, "state": "unknown", "current_refresh_proven": False,
             "daily_core_blocked": False, "reason_code": "SOURCE_NOT_REFRESHED",
             "blocked_uses": [name]} for name in notices.SOURCE_GAP_NAMES]


def test_known_debt_has_exact_scoped_warning():
    context = notices.source_gap_context(entries())
    event = notices._event_for("acquisition_quality", context)
    assert event == "p10_cn01_acquisition_quality_limited"
    assert context["required_intervals"] == ["1d"]
    assert context["daily_core_blocked"] is False
    text = mail._email_html(event=event, market_date=date(2026, 10, 15), pointer=None,
                            result=None, error="SOURCE_NOT_REFRESHED", context=context)
    assert "来源受限（不阻断日频）" in text and "缺口说明" in text
    assert "停复牌专门来源" in text and "是否阻断日频" in text
    assert "['1m'" not in text and "失败</td>" not in text


@pytest.mark.parametrize("field,value", [
    ("dataset", "daily_ohlcv_none_delta"), ("daily_core_blocked", True),
    ("current_refresh_proven", True), ("state", "failed"),
    ("reason_code", "RAW_CLOSURE_MISMATCH"), ("blocked_uses", []),
])
def test_critical_or_unknown_evidence_cannot_be_downgraded(field, value):
    values = entries()
    values[0][field] = value
    with pytest.raises(ValueError):
        notices.source_gap_context(values)


def test_warning_identity_does_not_suppress_real_same_day_failure(tmp_path, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 10, 16, 1, 0, tzinfo=trial.CST)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)

    monkeypatch.setattr(trial, "datetime", Clock)
    sent = []
    monkeypatch.setattr(
        notices.mail, "_send_resend",
        lambda **kw: sent.append(kw) or {"message_id": "synthetic"},
    )
    day = date(2026, 10, 15)
    context = notices.source_gap_context(entries())
    kwargs = dict(day=day, stage="acquisition_quality", state_root=tmp_path,
                  reason="SOURCE_NOT_REFRESHED")
    assert notices.failure(**kwargs, context=context)["delivery_status"] == "sent"
    assert notices.failure(**kwargs, context=context)["status"] == "already_sent"
    actual_fault = notices.failure(**kwargs, context={"daily_core_blocked": True})
    assert actual_fault["delivery_status"] == "sent"
    assert [v["event"] for v in sent] == ["p10_cn01_acquisition_quality_limited",
                                          "p10_cn01_acquisition_quality_failed"]
    assert len(list(tmp_path.glob('*.json'))) == 2


def test_frozen_warning_rejects_tampered_scope_or_other_stage():
    context = notices.source_gap_context(entries())
    altered = deepcopy(context)
    altered["required_intervals"] = ["1m"]
    with pytest.raises(ValueError):
        notices._event_for("acquisition_quality", altered)
    with pytest.raises(ValueError):
        notices._event_for("minutes", context)


@pytest.mark.parametrize("reason,extra", [
    ("PermissionError: permission denied", {}),
    ("SOURCE_NOT_REFRESHED", {"error_code": "RAW_CLOSURE_MISMATCH"}),
    ("SOURCE_NOT_REFRESHED", {"error": "PermissionError"}),
    ("SOURCE_NOT_REFRESHED", {"daily_core_blocked": True}),
])
def test_real_fault_with_debt_context_stays_failure(tmp_path, monkeypatch, reason, extra):
    sent = []
    monkeypatch.setattr(trial, "failure_decision", lambda **kw: {"action": "immediate_fault"})
    monkeypatch.setattr(
        notices.mail, "_send_resend",
        lambda **kw: sent.append(kw) or {"message_id": "synthetic"},
    )
    context = {**notices.source_gap_context(entries()), **extra}
    result = notices.failure(day=date(2026, 10, 15), stage="acquisition_quality",
                             state_root=tmp_path, reason=reason, context=context)
    assert result["delivery_status"] == "sent"
    assert sent[0]["event"] == "p10_cn01_acquisition_quality_failed"
    assert sent[0]["context"]["required_intervals"] == ["1d"]
    assert result["idempotency_key"] != notices._notification_key(
        date(2026, 10, 15), "acquisition_quality", notices.source_gap_context(entries())
    )


def test_bad_pending_debt_context_sends_real_fault_and_preserves_original(tmp_path, monkeypatch):
    monkeypatch.setattr(trial, "failure_decision", lambda **kw: {"action": "immediate_fault"})
    sent = []
    monkeypatch.setattr(
        notices.mail, "_send_resend",
        lambda **kw: sent.append(kw) or {"message_id": "synthetic"},
    )
    day = date(2026, 10, 15)
    context = notices.source_gap_context(entries())
    key = notices._notification_key(day, "acquisition_quality", context)
    payload = {"policy_id": notices.POLICY_ID, "market_date": str(day),
               "stage": "acquisition_quality", "reason": "PermissionError", "context": context}
    atomic(tmp_path / "pending" / (key + ".json"), payload)
    result = notices.retry_pending(tmp_path)
    assert result["remaining"] == 0
    assert len(sent) == 1 and sent[0]["event"] == "p10_cn01_acquisition_quality_failed"
    assert object_at(tmp_path / "reclassified-source-notices" / (key + ".json")) == payload


def test_old_delivered_source_notice_is_not_replayed_by_format_change(tmp_path, monkeypatch):
    monkeypatch.setattr(trial, "failure_decision", lambda **kw: {"action": "immediate_fault"})
    sent = []
    monkeypatch.setattr(
        notices.mail, "_send_resend",
        lambda **kw: sent.append(kw) or {"message_id": "synthetic"},
    )
    kwargs = dict(day=date(2026, 10, 15), stage="acquisition_quality",
                  state_root=tmp_path, reason=notices.SOURCE_GAP_REASON)
    old = notices.failure(**kwargs)
    assert old["delivery_status"] == "sent"
    assert notices.failure(**kwargs, context=notices.source_gap_context(entries()))[
        "status"
    ] == "already_sent"
    assert len(sent) == 1

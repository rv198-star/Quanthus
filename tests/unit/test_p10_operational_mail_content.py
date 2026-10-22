"""Human mail preserves the terminal event's scope and durable delivery identity."""

from __future__ import annotations

import hashlib
import io
import json
from copy import deepcopy

import pytest

from quanthus import p10_operational_mail as mail
from tests.unit.test_p10_container_delivery import private_mail, safe_event


@pytest.mark.parametrize(
    "reason,subject,body",
    [
        ("QUALIFIED_1D_NATIVE5M", "【完成】", "日频和 5 分钟 ODS/DWD 已构建完成"),
        ("CONTAINER_RUN_FAILED", "【失败】", "本通知没有具体故障详情"),
        ("CORE_READY_5M_HELD_SOURCE_QUALITY", "【部分完成】", "5 分钟来源未通过质量检查"),
        ("CORE_READY_5M_PENDING", "【部分完成】", "5 分钟数据仍待完成"),
        ("WEEKLY_FINANCIAL_VERIFIED_OTHER_DOMAINS_HELD", "【部分完成】", "其他领域仍暂缓"),
        ("AUTHORITATIVE_CLOSED_EVENT_SOURCE_READY", "【休市处理完成】", "不代表交易日行情构建成功"),
        ("DATE_WRITER_ALREADY_ACTIVE", "【已有任务运行】", "本次未重复启动"),
    ],
)
def test_readable_subject_and_scope_without_machine_json(reason, subject, body):
    event = safe_event(reason)
    if reason.startswith("WEEKLY_"):
        event["role"] = "weekly"
    before = deepcopy(event)
    title, text = mail.render_event(event)
    assert "2026-10-21" in title and title.startswith(subject)
    assert body in text
    assert "GDrive 备份由独立同步任务确认" in text
    assert "execution_authorized" not in text and "sha256:" not in text
    assert not text.startswith("{") and event == before
    if event["status"] != "complete":
        assert "日频和 5 分钟 ODS/DWD 已构建完成" not in text


def test_late_recovery_uses_shanghai_time_and_keeps_deadline_miss():
    event = safe_event()
    event.update(
        role="recovery",
        reason="LATE_RECOVERY_QUALIFIED_1D_NATIVE5M",
        observed_at="2026-10-22T13:37:40.925361+00:00",
    )
    subject, text = mail.render_event(event)
    assert subject.startswith("【完成·延迟】") and "数据补建" in subject
    assert "2026-10-22 21:37:40（北京时间）" in text
    assert "未达到原定按时交付目标" in text
    assert "日频和 5 分钟 ODS/DWD 已构建完成" in text


def test_recovery_role_does_not_imply_late_and_utc_day_is_not_market_date():
    event = safe_event()
    event.update(role="recovery", observed_at="2026-10-21T16:01:00+00:00")
    subject, text = mail.render_event(event)
    assert "2026-10-21" in subject
    assert "2026-10-22 00:01:00（北京时间）" in text
    assert "延迟" not in subject and "时效说明" not in text


@pytest.mark.parametrize("status", sorted(mail.STATUSES))
def test_unknown_reason_never_invents_scope_or_failure_cause(status):
    event = safe_event()
    event.update(status=status, reason="FUTURE_REASON")
    _, text = mail.render_event(event)
    assert "运行记录" in text or "运行日志" in text
    assert "日频和 5 分钟 ODS/DWD 已构建完成" not in text
    assert "登录失败" not in text and "自动修复" not in text


@pytest.mark.parametrize("status", ["complete", "failed"])
def test_acceptance_mail_is_conspicuously_labelled(status):
    event = safe_event()
    event.update(status=status, reason="ACCEPTANCE_SYNTHETIC")
    title, text = mail.render_event(event)
    label = "完成" if status == "complete" else "失败"
    assert title.startswith(f"【{label}·测试】") and "通道验收测试" in title
    assert text.startswith("这是邮件通道验收测试，不代表真实数据任务结果。")


def test_new_body_keeps_event_bytes_and_existing_idempotency_key(tmp_path, monkeypatch):
    event = safe_event()
    raw = json.dumps(event, indent=2).encode()
    source = tmp_path / "event.json"
    source.write_bytes(raw)
    key = hashlib.sha256(
        json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    calls = []

    def api(req, timeout):
        assert req.get_header("Idempotency-key") == key
        payload = json.loads(req.data)
        assert payload["subject"].startswith("【完成】")
        assert "日频和 5 分钟" in payload["text"]
        calls.append(payload)
        return io.BytesIO(b'{"id":"synthetic-mail-id"}')

    monkeypatch.setattr(mail.request, "urlopen", api)
    state = tmp_path / "mail"
    secret = private_mail(tmp_path)
    assert mail.deliver(source, state, secret) == "sent"
    assert mail.deliver(source, state, secret) == "already_sent"
    assert len(calls) == 1 and source.read_bytes() == raw
    assert json.loads((state / (key + ".json")).read_bytes())["event_sha256"] == key


def test_preexisting_json_mail_receipt_is_not_resent_after_format_change(tmp_path, monkeypatch):
    event = safe_event()
    key = hashlib.sha256(
        json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    source = tmp_path / "event.json"
    source.write_text(json.dumps(event))
    state = tmp_path / "mail"
    state.mkdir()
    (state / (key + ".json")).write_text(json.dumps({"status": "sent", "event_sha256": key}))
    monkeypatch.setattr(mail.request, "urlopen", lambda *a, **k: pytest.fail("do not resend"))
    assert mail.deliver(source, state, tmp_path / "absent-secret") == "already_sent"

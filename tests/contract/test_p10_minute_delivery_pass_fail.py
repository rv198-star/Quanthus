"""CN01 P10 minute mail decisions are real evidence-bound PASS/FAIL."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from quanthus import warehouse_notifications as mail
from quanthus.pipelines import p10_minute_delivery_mail as delivery

CST = ZoneInfo("Asia/Shanghai")
DAY = date(2026, 10, 9)
CAL = [{"trade_date": "2026-10-09", "is_open": "1"}]
GRADE_ZERO_R3 = {
    "R0": 4900, "R1": 0, "R2": 82, "R3": 0, "SUSPENDED": 10,
}
GRADE_HAS_R3 = {
    "R0": 1536, "R1": 0, "R2": 30, "R3": 3416, "SUSPENDED": 10,
}


def _fixture(tmp_path: Path, monkeypatch, *, grades=None, phase="minute_T"):
    grades = dict(grades or GRADE_ZERO_R3)
    root = tmp_path / "minute"
    pkg = root / "packages" / str(DAY) / "fixed"
    pkg.mkdir(parents=True)
    manifest = pkg / "manifest.json"
    manifest.write_text('{"date_slice_revised_securities":["000001"]}')
    digest = "sha256:" + hashlib.sha256(manifest.read_bytes()).hexdigest()
    (pkg / "SEALED").write_text(digest + "\n")
    status = root / "date-status" / str(DAY)
    status.mkdir(parents=True)
    (status / "fixed.json").write_text(json.dumps({
        "market_date": str(DAY),
        "quality_grades": grades,
        "research_ready": False,
        "manifest_sha256": digest,
    }))

    def real_verify_called(p: Path):
        assert p == pkg, "must open the exact dated grade package"
        assert (p / "SEALED").read_text().strip() == digest
        return {
            "manifest_sha256": digest, "market_date": str(DAY),
            "quality_grades": grades, "research_ready": False,
            "admitted_raw": grades["R0"] + grades["R1"] + grades["R2"],
        }

    monkeypatch.setattr(delivery, "verify", real_verify_called)
    phase_root = tmp_path / "ledger"
    phase_root.mkdir()
    phase_code = "T" if phase == "minute_T" else "T_plus_1"
    terminal = (
        "source_grade_warehouse_published" if phase == "minute_T"
        else "grade_qualified"
    )
    operation = (
        "warehouse-build" if phase == "minute_T"
        else "warehouse-immutable-correction"
    )
    ledger = phase_root / f"{DAY}-{phase_code}.json"
    ledger.write_text(json.dumps({
        "market_date": str(DAY), "phase": phase_code,
        "terminal_disposition": terminal,
        "operations": [{"operation_id": operation, "outcome": "success"}],
        "inflight_operation": None,
    }))
    return root, phase_root, ledger, status / "fixed.json"


def _assess(root, phase_root, *, phase="minute_T", at=None):
    return delivery.assess_minute_delivery(
        target=DAY, phase=phase,
        at=at or datetime(2026, 10, 9, 21, 50, tzinfo=CST),
        calendar=CAL, minute_root=root, phase_root=phase_root,
    )


def test_r2_honestly_stored_and_no_r3_is_delivery_pass_even_if_research_not_ready(
    monkeypatch, tmp_path,
):
    root, phases, _, _ = _fixture(tmp_path, monkeypatch)
    result = _assess(root, phases)
    assert result["status"] == "pass"
    assert result["mail_event"] == "p10_cn01_minute_delivery_pass"
    assert result["grade"]["grades"]["R2"] == 82
    assert result["grade"]["research_ready"] is False


def test_unqualified_r3_always_fails_even_when_warehouse_published(
    monkeypatch, tmp_path,
):
    root, phases, _, _ = _fixture(tmp_path, monkeypatch, grades=GRADE_HAS_R3)
    result = _assess(root, phases)
    assert result["status"] == "fail"
    assert result["reason"] == "R3_unqualified_securities:3416"
    assert result["mail_event"] == "p10_cn01_minute_delivery_fail"


@pytest.mark.parametrize("issue", ["no_ledger", "not_terminal", "failed_publish"])
def test_missing_or_unverified_phase_never_passes(monkeypatch, tmp_path, issue):
    root, phases, state, _ = _fixture(tmp_path, monkeypatch)
    if issue == "no_ledger":
        state.unlink()
    elif issue == "not_terminal":
        current = json.loads(state.read_text())
        current.pop("terminal_disposition")
        state.write_text(json.dumps(current))
    else:
        current = json.loads(state.read_text())
        current["operations"][0]["outcome"] = "failed"
        state.write_text(json.dumps(current))
    assert _assess(root, phases)["status"] == "fail"


@pytest.mark.parametrize("issue", ["tampered_receipt", "physical_verify_failed"])
def test_bare_seal_cannot_prove_valid_data(monkeypatch, tmp_path, issue):
    root, phases, _, receipt = _fixture(tmp_path, monkeypatch)
    if issue == "tampered_receipt":
        content = json.loads(receipt.read_text())
        content["manifest_sha256"] = "sha256:" + "f" * 64
        receipt.write_text(json.dumps(content))
    else:
        monkeypatch.setattr(
            delivery, "verify",
            lambda _pkg: (_ for _ in ()).throw(ValueError("no R2 source")),
        )
    result = _assess(root, phases)
    assert result["status"] == "fail"
    assert "minute_verified_product_unavailable" in result["reason"]


def test_tplus1_unchanged_r3_is_fail_not_false_pass(monkeypatch, tmp_path):
    root, phases, ledger, _ = _fixture(
        tmp_path, monkeypatch, phase="minute_Tplus1", grades=GRADE_HAS_R3
    )
    state = json.loads(ledger.read_text())
    state["terminal_disposition"] = "source_unchanged_stopped"
    state["operations"] = [{
        "operation_id": "native-source-maturity-canary", "outcome": "success"
    }]
    ledger.write_text(json.dumps(state))
    result = _assess(
        root, phases, phase="minute_Tplus1",
        at=datetime(2026, 10, 10, 11, 50, tzinfo=CST),
    )
    assert result["status"] == "fail"
    assert result["reason"] == "R3_unqualified_securities:3416"


def test_tplus1_unchanged_without_r3_is_pass(monkeypatch, tmp_path):
    root, phases, ledger, _ = _fixture(tmp_path, monkeypatch, phase="minute_Tplus1")
    state = json.loads(ledger.read_text())
    state["terminal_disposition"] = "source_unchanged_stopped"
    state["operations"] = [{
        "operation_id": "native-source-maturity-canary", "outcome": "success"
    }]
    ledger.write_text(json.dumps(state))
    result = _assess(
        root, phases, phase="minute_Tplus1",
        at=datetime(2026, 10, 10, 11, 50, tzinfo=CST),
    )
    assert result["status"] == "pass"


def test_inflight_defers_no_emails_and_stale_operation_fails(monkeypatch, tmp_path):
    root, phases, ledger, _ = _fixture(tmp_path, monkeypatch, phase="minute_Tplus1")
    state = json.loads(ledger.read_text())
    state["inflight_operation"] = {
        "started_at": "2026-10-10T11:45:00+08:00", "timeout_seconds": 600
    }
    ledger.write_text(json.dumps(state))
    before = _assess(
        root, phases, phase="minute_Tplus1",
        at=datetime(2026, 10, 10, 11, 50, tzinfo=CST),
    )
    after = _assess(
        root, phases, phase="minute_Tplus1",
        at=datetime(2026, 10, 10, 12, 10, tzinfo=CST),
    )
    assert before["status"] == "defer" and before["mail_event"] is None
    assert after["status"] == "fail"
    assert after["reason"] == "minute_phase_operation_stale_or_dead"


def test_email_sent_once_even_if_later_checkpoint_flips_to_other_verdict(
    monkeypatch, tmp_path,
):
    sent = []

    def fake_sender(**kwargs):
        sent.append(kwargs)
        return {"status": "sent", "provider": "fake", "attempts": 1}

    monkeypatch.setattr(mail, "_send_resend", fake_sender)
    common = dict(
        market_date=DAY, phase="minute_Tplus1",
        state_root=tmp_path / "mail",
    )
    first = mail.send_minute_delivery_outcome(
        **common, passed=True, context={
            "delivery_verdict": "PASS", "executed_phase": "T+1",
            "research_qualification": "NOT READY",
        },
    )
    second = mail.send_minute_delivery_outcome(
        **common, passed=False, context={
            "delivery_verdict": "FAIL", "executed_phase": "T+1"
        }, reason="late_r3",
    )
    assert first["status"] == "sent"
    assert second["status"] == "skipped"
    assert second["verdict"] == "PASS"
    assert len(sent) == 1
    assert sent[0]["event"] == "p10_cn01_minute_delivery_pass"
    assert sent[0]["result"] is None


def test_mail_subject_contains_tplus1_and_explicit_pass_fail(monkeypatch):
    import json as json_module

    payloads = []
    class FakeResponse:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def getcode(self):
            return 200
        def read(self):
            return b'{"id":"fake"}'

    def fake_post(request, timeout):
        payloads.append(json_module.loads(request.data))
        return FakeResponse()

    monkeypatch.setattr(mail.request, "urlopen", fake_post)
    monkeypatch.setenv("QUANTHUS_RESEND_API_KEY", "fake-secret")
    monkeypatch.setenv("QUANTHUS_RESEND_TO", "test@example.com")
    for verdict in ("pass", "fail"):
        mail._send_resend(
            event=f"p10_cn01_minute_delivery_{verdict}",
            market_date=DAY, key="test-key", pointer=None, result=None,
            context={"executed_phase": "T+1", "delivery_verdict": verdict.upper(),
                     "research_qualification": "NOT READY"},
            error="R3_remaining" if verdict == "fail" else None,
        )
    assert "1m T+1" in payloads[0]["subject"]
    assert payloads[0]["subject"].endswith("PASS")
    assert payloads[1]["subject"].endswith("FAIL")
    assert "Research READY" not in payloads[0]["subject"]
    assert "NOT READY" in payloads[0]["html"]


@pytest.mark.parametrize("passed", [True, False])
def test_deadline_main_routes_both_minute_outcomes_to_true_email_sender(
    monkeypatch, tmp_path, capsys, passed,
):
    from quanthus.pipelines import p10_deadline_cycle as cycle

    event = (
        "p10_cn01_minute_delivery_pass" if passed
        else "p10_cn01_minute_delivery_fail"
    )
    calls = []
    grade = {
        "grades": GRADE_ZERO_R3 if passed else GRADE_HAS_R3,
        "research_ready": False,
        "manifest_sha256": "sha256:" + "a" * 64,
        "revised_count": 3200,
    }
    monkeypatch.setattr(cycle, "calendar_for", lambda _date: CAL)
    monkeypatch.setattr(cycle, "target_for", lambda *_args: DAY)
    monkeypatch.setattr(
        cycle, "_roots",
        lambda _node: (tmp_path / "daily", tmp_path / "minute", tmp_path / "phase"),
    )
    monkeypatch.setattr(
        cycle, "assess_minute_delivery",
        lambda **_kwargs: {
            "status": "pass" if passed else "fail",
            "reason": None if passed else "R3_unqualified_securities:3416",
            "mail_event": event,
            "grade": grade,
        },
    )
    monkeypatch.setattr(cycle, "minute_mail_snapshot", lambda **_kwargs: {})
    monkeypatch.setattr(
        cycle, "send_minute_delivery_outcome",
        lambda **kwargs: (calls.append(kwargs) or {"status": "sent"}),
    )
    monkeypatch.setattr(
        cycle, "send_daily_event",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("legacy sender used")),
    )
    monkeypatch.setattr(
        "sys.argv",
        ["p10-deadline-cycle", "--node", "CN01", "--phase", "minute_Tplus1", "--send",
         "--notification-state-root", str(tmp_path / "mail")],
    )
    assert cycle.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["delivery"]["status"] == "sent"
    assert len(calls) == 1
    assert calls[0]["phase"] == "minute_Tplus1"
    assert calls[0]["passed"] is passed
    assert calls[0]["context"]["research_qualification"] == "NOT READY"
    assert calls[0]["context"]["quality_distribution"].startswith("R0=")
    assert calls[0]["context"]["delivery_verdict"] == ("PASS" if passed else "FAIL")

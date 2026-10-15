"""Synthetic notification acceptance; never contact sources or the mail provider."""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from quanthus.pipelines import p10_complete_daily_notifications as notices
from quanthus.pipelines import p10_notification_incident as incident
from quanthus.pipelines import p10_notification_trial as trial
from quanthus.pipelines.p10_delivery_common import atomic, digest, encoded, file_digest, object_at

DAY = date(2026, 10, 15)


@pytest.fixture
def frozen(monkeypatch):
    clock = [datetime(2026, 10, 15, 22, 0, tzinfo=trial.CST)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)

    monkeypatch.setattr(trial, "datetime", Clock)
    return clock


@pytest.fixture
def sender(monkeypatch):
    sent = []
    monkeypatch.setattr(
        notices.mail, "_send_resend", lambda **kw: sent.append(kw) or {"message_id": "synthetic"}
    )
    return sent


def test_daily_attempts_wait_until_2345_and_send_once(tmp_path, frozen, sender):
    args = dict(day=DAY, stage="daily_cycle", state_root=tmp_path, reason="source not ready")
    assert notices.failure(**args)["status"] == "deferred_until_deadline"
    assert sender == [] and not (tmp_path / "pending").exists()
    frozen[0] = frozen[0].replace(hour=23, minute=45)
    assert notices.failure(**args)["delivery_status"] == "sent"
    assert notices.failure(**{**args, "stage": "daily_ods"})["status"] == "already_sent"
    assert len(sender) == 1 and sender[0]["context"]["required_intervals"] == ["1d"]


def test_blacklist_is_immediate_and_deadline_does_not_duplicate(tmp_path, frozen, sender):
    args = dict(
        day=DAY, stage="daily_cycle", state_root=tmp_path, reason="BAOSTOCK_ACCESS_DENIED: 10001011"
    )
    assert notices.failure(**args)["delivery_status"] == "sent"
    frozen[0] = frozen[0].replace(hour=23, minute=50)
    assert notices.failure(**{**args, "stage": "daily_ods"})["status"] == "already_sent"
    assert len(sender) == 1


def test_turning_off_success_trial_keeps_new_failure_policy(tmp_path, frozen, monkeypatch):
    value = object_at(trial.CONFIG)
    value["success_notifications_enabled"] = False
    atomic(tmp_path / "trial.json", value)
    monkeypatch.setattr(trial, "CONFIG", tmp_path / "trial.json")
    assert not trial.success_enabled(DAY)
    assert (
        trial.failure_decision(
            day=DAY, stage="daily_cycle", reason="not ready", context={}, state_root=tmp_path
        )["action"]
        == "defer"
    )


def test_minute_deadline_uses_saved_calendar_across_closed_dates(tmp_path, frozen):
    target = date(2026, 10, 16)
    frozen[0] = datetime(2026, 10, 19, 12, 59, tzinfo=trial.CST)
    rows = [
        {"trade_date": str(target + timedelta(days=i)), "is_open": int(i in {0, 3})}
        for i in range(4)
    ]
    atomic(
        tmp_path / "calendars/2026-10-19.json",
        {
            "market_date": "2026-10-19",
            "provider": "baostock-trading-calendar",
            "rows": rows,
            "rows_sha256": digest(encoded(rows)),
            "fetched_at": frozen[0].isoformat(),
        },
    )
    decision = trial.failure_decision(
        day=target,
        stage="minutes",
        reason="R3 remains",
        context={},
        state_root=tmp_path / "notifications",
    )
    assert decision["action"] == "defer" and decision["deadline"].startswith("2026-10-19T13:00")
    frozen[0] += timedelta(minutes=1)
    assert (
        trial.failure_decision(
            day=target,
            stage="minutes",
            reason="R3 remains",
            context={},
            state_root=tmp_path / "notifications",
        )["action"]
        == "deadline_failure"
    )


def minute_receipts(tmp_path):
    results = {}
    for interval in ("1m", "5m"):
        root = tmp_path / interval
        manifest = {
            "market_date": str(DAY),
            "interval": interval,
            "grades": {"R0": 1, "R2": 1, "SUSPENDED": 1},
            "research_ready": True,
            "content_digest": "sha256:" + "a" * 64,
            "reference_digest": "sha256:" + "b" * 64,
        }
        atomic(root / "manifest.json", manifest)
        sha = file_digest(root / "manifest.json")
        (root / "READY").write_text(sha + "\n")
        results[interval] = {
            **manifest,
            "quality_grades": manifest["grades"],
            "manifest_sha256": sha,
            "package_path": str(root),
        }
    return results


def test_native_counter_without_r3_key_is_qualified(tmp_path):
    proof = trial.minute_evidence(minute_receipts(tmp_path), DAY)
    assert proof["market_date"] == str(DAY) and "最低R2" in proof["minute_status"]
    assert "尚待" in proof["oci_status"] and "尚待" in proof["gdrive_status"]


@pytest.mark.parametrize("fault", ["r3", "negative", "wrong_grade", "wrong_date", "ref", "seal"])
def test_invalid_minute_evidence_never_sends_success(tmp_path, fault):
    receipts = minute_receipts(tmp_path)
    item = receipts["1m"]
    if fault in {"r3", "negative", "wrong_grade"}:
        item["quality_grades"] = (
            {"R3": 1} if fault == "r3" else ({"R0": -1} if fault == "negative" else {"UNKNOWN": 3})
        )
    if fault == "wrong_date":
        item["market_date"] = "2026-10-14"
    if fault == "ref":
        item["reference_digest"] = "sha256:" + "c" * 64
    if fault == "seal":
        (Path(item["package_path"]) / "READY").write_text("wrong")
    with pytest.raises(ValueError):
        trial.minute_evidence(receipts, DAY)


def test_success_queue_retries_identical_body_and_deduplicates_versions(tmp_path, monkeypatch):
    proof = trial.minute_evidence(minute_receipts(tmp_path / "sources"), DAY)
    notes = tmp_path / "notes"
    kwargs = dict(
        day=DAY, stage="minutes", state_root=notes, evidence=proof, code_revision="a" * 40
    )
    assert notices.success(**kwargs)["status"] == "success_notification_queued"
    pending = next((notes / "pending").glob("*.json"))
    before = pending.read_bytes()
    assert notices.success(**{**kwargs, "code_revision": "b" * 40})["status"].endswith("queued")
    assert pending.read_bytes() == before
    sent = []

    def send(**kw):
        sent.append(kw)
        if len(sent) == 1:
            raise TimeoutError("lost transport acknowledgement")
        return {"message_id": "synthetic"}

    monkeypatch.setattr(notices.mail, "_send_resend", send)
    assert notices.retry_pending(notes)["remaining"] == 1
    assert pending.read_bytes() == before
    assert notices.retry_pending(notes)["remaining"] == 0
    assert sent[0] == sent[1] and sent[0]["error"] is None
    assert sent[0]["event"] == "p10_cn01_tplus1_minutes_completed"
    assert notices.success(**kwargs)["status"] == "already_sent"
    assert notices.retry_pending(notes)["outcomes"] == []


def test_old_success_is_not_backfilled_and_no_evidence_is_not_success(tmp_path):
    assert notices.success(
        day=date(2026, 10, 14),
        stage="daily_ods",
        state_root=tmp_path,
        evidence={},
        code_revision="a" * 40,
    )["status"].endswith("not_enabled")
    with pytest.raises(ValueError):
        notices.success(
            day=DAY, stage="daily_ods", state_root=tmp_path, evidence={}, code_revision="a" * 40
        )


def test_daily_completion_checks_catalog_and_both_physical_sources(tmp_path, monkeypatch):
    from quanthus.pipelines import contracts
    from quanthus.pipelines.securities_warehouse import daily_core_ods_catalog as catalog

    atomic(
        tmp_path / "catalog/catalog.json",
        {
            "lanes": [
                {
                    "lane": name,
                    "release_path": name,
                    "release_manifest_sha256": "sha256:" + "a" * 64,
                }
                for name in ("market_daily_core", "calendar_daily_events")
            ]
        },
    )
    monkeypatch.setattr(
        catalog,
        "verify_daily_ods_catalog",
        lambda root: SimpleNamespace(
            market_date=DAY, product_scope="daily-core-v1", catalog_digest="sha256:" + "b" * 64
        ),
    )
    seen = []
    monkeypatch.setattr(
        contracts,
        "verify_ods_release",
        lambda root: (
            seen.append(root)
            or SimpleNamespace(
                manifest_sha256="sha256:" + "a" * 64,
                release=SimpleNamespace(market_date=DAY, status="ready", artifacts=[]),
            )
        ),
    )
    proof = trial.daily_evidence(tmp_path / "catalog", tmp_path, DAY)
    assert len(seen) == 2 and proof["required_intervals"] == ["1d"]
    assert proof["complete_dwd_status"] == "未由本通知验收"


def ack(day=DAY, stage="minutes"):
    return incident.seal(
        {
            "kind": incident.KIND,
            "market_date": str(day),
            "stage": stage,
            "resolved": False,
            "delivery_status": "sent",
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "idempotency_key": "a" * 64,
        }
    )


def test_known_upstream_merges_wait_but_never_hides_new_engineering_fault(tmp_path, frozen, sender):
    frozen[0] = datetime(2026, 10, 16, 14, 0, tzinfo=trial.CST)
    kwargs = dict(
        day=DAY,
        stage="mirror",
        state_root=tmp_path,
        reason="target absent",
        context={"deadline": "2026-10-16T14:00:00+08:00", "upstream_incident": ack()},
    )
    assert notices.failure(**kwargs)["status"] == "blocked_upstream_already_notified"
    assert sender == []
    assert (
        notices.failure(**{**kwargs, "reason": "No space left on device"})["delivery_status"]
        == "sent"
    )
    assert len(sender) == 1


@pytest.mark.parametrize("fault", ["resolved", "wrong_day", "digest", "not_sent"])
def test_invalid_upstream_proof_does_not_suppress_deadline(tmp_path, frozen, sender, fault):
    frozen[0] = datetime(2026, 10, 16, 14, 0, tzinfo=trial.CST)
    proof = ack()
    if fault == "resolved":
        proof["resolved"] = True
    if fault == "wrong_day":
        proof["market_date"] = "2026-10-14"
    if fault == "digest":
        proof["receipt_digest"] = "wrong"
    if fault == "not_sent":
        proof["delivery_status"] = "pending"
    notices.failure(
        day=DAY,
        stage="mirror",
        state_root=tmp_path,
        reason="target absent",
        context={"deadline": "2026-10-16T14:00:00+08:00", "upstream_incident": proof},
    )
    assert len(sender) == 1


def test_resolved_root_is_not_reopened_by_late_mail_replay(tmp_path, monkeypatch):
    monkeypatch.setattr(incident, "CN01_NOTES", tmp_path / "notes")
    monkeypatch.setattr(incident, "EXPORT", tmp_path / "export")
    receipt = {
        "delivery_status": "sent",
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "idempotency_key": "a" * 64,
    }
    incident.acknowledge(tmp_path / "notes", DAY, "minutes", receipt)
    assert incident.upstream(tmp_path / "export", DAY, "mirror")
    incident.resolve(tmp_path / "notes", DAY, "minutes")
    incident.acknowledge(tmp_path / "notes", DAY, "minutes", receipt)
    assert incident.upstream(tmp_path / "export", DAY, "mirror") is None


def test_remote_incident_requires_a_recent_authenticated_poll(tmp_path):
    atomic(
        tmp_path / "fresh-receipt.json",
        {
            "market_date": str(DAY),
            "incident": ack(),
            "fetched_at": (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat(),
        },
    )
    assert incident.recent_remote(tmp_path, DAY) is None
    atomic(
        tmp_path / "fresh-receipt.json",
        {
            "market_date": str(DAY),
            "incident": ack(),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert incident.recent_remote(tmp_path, DAY)


def test_notification_change_preserves_frozen_data_policy_bytes():
    import subprocess

    root = trial.CONFIG.parents[2]
    frozen = subprocess.check_output(
        [
            "git",
            "show",
            "ccd2817e434645622579cd8c7073c8efcd3285cd:config/pipelines/p10-complete-daily.v1.json",
        ],
        cwd=root,
    )
    assert (root / "config/pipelines/p10-complete-daily.v1.json").read_bytes() == frozen


def test_grouped_pending_replays_original_stage_and_body(tmp_path, frozen, monkeypatch):
    frozen[0] = frozen[0].replace(hour=23, minute=50)
    sent = []

    def send(**kw):
        sent.append(kw)
        if len(sent) == 1:
            raise TimeoutError("synthetic")
        return {"message_id": "synthetic"}

    monkeypatch.setattr(notices.mail, "_send_resend", send)
    with pytest.raises(TimeoutError):
        notices.failure(day=DAY, stage="daily_cycle", state_root=tmp_path, reason="original")
    queued = next((tmp_path / "pending").glob("*.json")).read_bytes()
    notices.failure(day=DAY, stage="daily_ods", state_root=tmp_path, reason="watchdog")
    assert (
        sent[0] == sent[1] and b"original" in queued and sent[1]["event"].endswith("cycle_failed")
    )
    assert not list((tmp_path / "pending").glob("*.json"))


@pytest.mark.parametrize("same_invocation", [True, False])
def test_outer_hook_only_defers_current_retryable_stage(tmp_path, monkeypatch, same_invocation):
    import sys

    from quanthus.pipelines import p10_complete_daily_cycle as cycle

    monkeypatch.setenv("SERVICE_RESULT", "exit-code")
    monkeypatch.setenv("INVOCATION_ID", "a" * 32)
    monkeypatch.setattr(sys, "argv", ["notes", "--complete-cycle-service-stop"])
    monkeypatch.setattr(cycle, "JOURNAL", tmp_path)
    monkeypatch.setattr(
        notices, "_complete_cycle_failure_target", lambda *a, **kw: (DAY, "minutes", "交易日")
    )
    atomic(
        tmp_path / str(DAY) / "status.json",
        {
            "status": "minutes_not_qualified",
            "invocation_id": ("a" if same_invocation else "b") * 32,
        },
    )
    seen = []
    monkeypatch.setattr(notices, "failure", lambda **kw: seen.append(kw) or {})
    assert notices.main() == 0
    assert seen[0]["context"]["error_code"] == (
        "retryable_stage_incomplete" if same_invocation else "controller_runtime_failed"
    )


def test_remote_poll_invalidates_old_receipt_when_source_proof_disappears(tmp_path, monkeypatch):
    from quanthus.pipelines import p10_complete_daily_sync as sync
    from quanthus.warehouse_sync import WarehouseSyncError

    def missing(**kw):
        raise WarehouseSyncError("No such file or directory")

    monkeypatch.setattr(sync, "_run_rsync", missing)
    cache = tmp_path / sync.CACHE / "upstream-notifications"
    atomic(
        cache / "fresh-receipt.json",
        {
            "market_date": str(DAY),
            "incident": ack(),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert sync.upstream_alert(tmp_path, DAY, source="synthetic", ssh="synthetic") is None
    assert incident.recent_remote(cache, DAY) is None


def test_missed_success_enqueue_is_recovered_without_supplier_calls(
    tmp_path, frozen, monkeypatch, sender
):
    root = tmp_path / "journal/notifications"
    shadow = tmp_path / "shadow"
    monkeypatch.setattr(incident, "CN01_NOTES", root)
    monkeypatch.setattr(incident, "EXPORT", tmp_path / "export")
    monkeypatch.setattr(notices, "datetime", trial.datetime)
    monkeypatch.setenv("HERMES_ODS_SHADOW_ROOT", str(shadow))
    rows = [{"trade_date": "2026-10-14", "is_open": 1}, {"trade_date": "2026-10-15", "is_open": 1}]
    atomic(
        root.parent / "calendars/2026-10-15.json",
        {
            "market_date": str(DAY),
            "provider": "baostock-trading-calendar",
            "rows": rows,
            "rows_sha256": digest(encoded(rows)),
            "fetched_at": frozen[0].isoformat(),
        },
    )
    atomic(
        shadow / f"orchestrator/daily-core/{DAY}/attempt/run.json",
        {
            "catalog": {"status": "ready", "root": str(tmp_path / "verified-catalog")},
        },
    )
    atomic(
        root.parent / str(DAY) / "status.json",
        {
            "minutes": minute_receipts(tmp_path / "current-T-minutes"),
        },
    )
    checked = []
    monkeypatch.setattr(
        trial,
        "daily_evidence",
        lambda *a: (
            checked.append(a)
            or {
                "market_date": str(DAY),
                "verification": "synthetic_verified_catalog",
                "catalog_digest": "sha256:" + "c" * 64,
                "required_intervals": ["1d"],
            }
        ),
    )
    result = notices.retry_pending(root)
    assert result["remaining"] == 0 and len(sender) == len(checked) == 1
    assert sender[0]["event"] == "p10_cn01_daily_ods_completed"
    notices.retry_pending(root)
    assert len(sender) == len(checked) == 1


def test_distinct_producers_never_reuse_resend_key_across_outboxes(tmp_path, frozen, sender):
    core = tmp_path / "complete-daily-v1/notifications"
    continuation = tmp_path / "acquisition-continuation-v1/notifications"
    for stage, root in [
        ("daily_cycle", core),
        ("acquisition_quality", continuation),
        ("domain_daily", continuation),
    ]:
        notices.failure(
            day=DAY,
            stage=stage,
            state_root=root,
            reason="PermissionError: independent producer fault",
        )
    assert len(sender) == 3 and len({request["key"] for request in sender}) == 3
    assert sender[0]["event"] != sender[1]["event"] != sender[2]["event"]


def test_optional_mail_context_does_not_break_existing_notifications(monkeypatch):
    from datetime import date

    from quanthus import warehouse_notifications as mail

    calls = []
    monkeypatch.setenv("QUANTHUS_RESEND_API_KEY", "synthetic-not-a-real-key")
    monkeypatch.setenv("QUANTHUS_RESEND_RETRIES", "0")

    class Response:
        status = 200

        def getcode(self):
            return self.status

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"id":"offline-synthetic"}'

    def send(request, **kwargs):
        calls.append(request)
        return Response()

    monkeypatch.setattr(mail.request, "urlopen", send)
    result = mail._send_resend(
        event="p10_cn01_daily_ods_completed",
        market_date=date(2026, 10, 15),
        pointer=None,
        result=None,
        key="offline-optional-context",
        context=None,
    )
    assert result["message_id"] == "offline-synthetic" and len(calls) == 1

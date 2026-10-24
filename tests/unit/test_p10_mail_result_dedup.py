"""Daily and recovery notify one unchanged result, without hiding real transitions."""

from __future__ import annotations

import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from quanthus import p10_operational_mail as mail
from tests.unit.test_p10_container_delivery import private_mail, safe_event


def event_file(root, *, role="daily", hour=10, reason="QUALIFIED_1D_NATIVE5M", **updates):
    value = safe_event() if reason.startswith("ACCEPTANCE_") else safe_event(reason)
    value.update(
        role=role, reason=reason, observed_at=f"2026-10-21T{hour:02}:00:00+00:00", **updates
    )
    value["run_id"] = f"{role}-{value['market_date']}-{hour}"
    root.mkdir(exist_ok=True)
    path = root / (value["run_id"] + ".json")
    path.write_text(json.dumps(value, indent=2))
    return path


def key(path):
    return hashlib.sha256(
        json.dumps(json.loads(path.read_bytes()), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@pytest.fixture
def sender(tmp_path, monkeypatch):
    calls = []

    def api(req, timeout):
        calls.append(req)
        return io.BytesIO(b'{"id":"synthetic-id"}')

    monkeypatch.setattr(mail.request, "urlopen", api)
    return tmp_path / "events", tmp_path / "mail", private_mail(tmp_path), calls


@pytest.mark.parametrize(
    "reason", ["QUALIFIED_1D_NATIVE5M", "AUTHORITATIVE_CLOSED_EVENT_SOURCE_READY"]
)
def test_daily_then_unchanged_recovery_sends_once_and_retains_events(sender, reason):
    events, state, secret, calls = sender
    daily = event_file(events, reason=reason)
    recovery = event_file(events, role="recovery", hour=13, reason=reason)
    before = {p: p.read_bytes() for p in (daily, recovery)}
    assert mail.deliver(daily, state, secret) == "sent"
    assert mail.deliver(recovery, state, secret) == "unchanged_result_already_sent"
    assert mail.deliver(recovery, state, secret) == "unchanged_result_already_sent"
    assert len(calls) == 1
    assert before == {p: p.read_bytes() for p in before}
    receipt = json.loads((state / (key(recovery) + ".json")).read_bytes())
    assert receipt["status"] == "suppressed_unchanged"
    assert receipt["duplicate_of_event_sha256"] == key(daily)


def test_legacy_sent_receipt_suppresses_recovery_without_secret_or_resend(sender):
    events, state, _, calls = sender
    daily = event_file(events)
    recovery = event_file(events, role="recovery", hour=13)
    state.mkdir()
    receipt = state / (key(daily) + ".json")
    original = json.dumps({"status": "sent", "event_sha256": key(daily), "attempts": 1})
    receipt.write_text(original)
    assert mail.deliver(recovery, state, state / "absent-secret") == "unchanged_result_already_sent"
    assert receipt.read_text() == original and not calls


@pytest.mark.parametrize("reason", ["CONTAINER_RUN_FAILED", "CORE_READY_5M_PENDING"])
def test_failed_or_partial_then_success_is_not_suppressed(sender, reason):
    events, state, secret, calls = sender
    first = event_file(events, reason=reason)
    recovery = event_file(events, role="recovery", hour=13)
    assert mail.deliver(first, state, secret) == "sent"
    assert mail.deliver(recovery, state, secret) == "sent"
    assert len(calls) == 2


def test_success_then_failure_then_real_recovery_notifies_all_three(sender):
    events, state, secret, calls = sender
    for p in (
        event_file(events),
        event_file(events, role="recovery", hour=12, reason="CONTAINER_RUN_FAILED"),
        event_file(events, role="recovery", hour=13),
    ):
        assert mail.deliver(p, state, secret) == "sent"
    assert len(calls) == 3


@pytest.mark.parametrize(
    "updates",
    [
        {"manifests": ["sha256:" + "c" * 64]},
        {"market_date": "2026-10-22"},
        {"role": "weekly"},
        {"reason": "ACCEPTANCE_SYNTHETIC"},
    ],
)
def test_changed_result_date_weekly_and_acceptance_are_independent(sender, updates):
    events, state, secret, calls = sender
    daily = event_file(events)
    recovery = event_file(events, hour=13, **({"role": "recovery"} | updates))
    assert mail.deliver(daily, state, secret) == "sent"
    assert mail.deliver(recovery, state, secret) == "sent"
    assert len(calls) == 2


def test_unsent_daily_does_not_hide_recovery(sender):
    events, state, secret, calls = sender
    event_file(events)
    recovery = event_file(events, role="recovery", hour=13)
    assert mail.deliver(recovery, state, secret) == "sent"
    assert len(calls) == 1


def test_failed_send_receipt_does_not_hide_recovery(sender):
    events, state, secret, calls = sender
    daily = event_file(events)
    recovery = event_file(events, role="recovery", hour=13)
    state.mkdir()
    (state / (key(daily) + ".json")).write_text(
        json.dumps({"status": "failed", "event_sha256": key(daily), "attempts": 2})
    )
    assert mail.deliver(recovery, state, secret) == "sent"
    assert len(calls) == 1


def test_reused_late_result_is_still_the_same_result(sender):
    events, state, secret, calls = sender
    daily = event_file(events)
    recovery = event_file(events, role="recovery", hour=13)
    value = json.loads(recovery.read_bytes())
    value["reason"] = "LATE_RECOVERY_" + value["reason"]
    recovery.write_text(json.dumps(value))
    assert mail.deliver(daily, state, secret) == "sent"
    assert mail.deliver(recovery, state, secret) == "unchanged_result_already_sent"
    assert len(calls) == 1


def test_concurrent_daily_and_recovery_share_delivery_lock(sender):
    events, state, secret, calls = sender
    paths = [event_file(events), event_file(events, role="recovery", hour=13)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda p: mail.deliver(p, state, secret), paths))
    assert sorted(results) == ["sent", "unchanged_result_already_sent"]
    assert len(calls) == 1


@pytest.mark.parametrize("mutation", ["symlink", "invalid_json", "receipt_identity"])
def test_unreadable_or_inconsistent_peer_cannot_establish_duplicate(sender, mutation):
    events, state, secret, calls = sender
    daily = event_file(events)
    recovery = event_file(events, role="recovery", hour=13)
    assert mail.deliver(daily, state, secret) == "sent"
    if mutation == "symlink":
        archived = daily.with_suffix(".old")
        daily.rename(archived)
        daily.symlink_to(archived)
    elif mutation == "invalid_json":
        daily.write_text("{")
    else:
        receipt = state / (key(daily) + ".json")
        receipt.write_text(json.dumps({"status": "sent", "event_sha256": "incorrect"}))
    assert mail.notify(recovery, state, secret) == "failed_event_retained"
    assert len(calls) == 1

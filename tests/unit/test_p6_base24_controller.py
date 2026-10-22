from __future__ import annotations

import json
import shutil
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from quanthus.forward_validation import base24_controller as control
from quanthus.forward_validation.contracts import ForwardValidationContractError
from quanthus.forward_validation.serialization import digest_value
from quanthus.platform.jobs import P6_FORWARD_CYCLE_JOB
from quanthus.platform.persistence import InMemoryPlatformStore
from tests.unit.test_p6_base24_cycle import CONFIG, ROOT, activation, runtime
from tests.unit.test_p6_base24_cycle import release as release
from tests.unit.test_p6_base24_runtime import runtime_source as runtime_source
from tests.unit.test_p6_base24_runtime import view as view


@pytest.fixture
def setup(release, tmp_path, monkeypatch):
    root, m, receipt = release
    r = replace(runtime(), daily_root=tmp_path / "catalog")
    package = r.daily_root / "packages" / m["market_date"] / m["package_id"]
    package.parent.mkdir(parents=True)
    shutil.move(root, package)
    run = tmp_path / "runtime"
    (run / "activation").mkdir(parents=True)
    a = activation()
    raw = {**a.semantic_payload(), "decision_digest": a.decision_digest}
    (run / "activation/base24-enabled.json").write_text(json.dumps(raw))
    pointer = {
        "schema_version": "quanthus.p6-base24-current.v1",
        **{k: m[k] for k in ("market_date", "package_id", "content_digest")},
        "manifest_sha256": receipt.manifest_sha256,
    }
    (r.daily_root / "CURRENT.json").write_text(json.dumps(pointer))
    receipt_path = r.daily_root / "receipts" / m["market_date"] / f"{m['package_id']}.json"
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text(
        json.dumps(
            {
                **receipt.semantic_payload,
                "receipt_digest": receipt.receipt_digest,
            }
        )
    )
    monkeypatch.setattr(control.cycle, "load_runtime", lambda *args, **kwargs: r)
    monkeypatch.setattr(control, "_mount_is_read_only", lambda p: True)
    monkeypatch.setattr(
        control, "_disabled_rehearsal_job", lambda *a, **kw: SimpleNamespace(job_id="rehearsal")
    )
    monkeypatch.setattr(control, "_verified_rehearsal_manifest", lambda *a: a_digest)
    monkeypatch.setattr(control, "_actor_user_id", lambda s: "usr_test")
    a_digest = a.disabled_rehearsal_manifest_digest
    store = InMemoryPlatformStore()
    args = dict(
        repository_root=ROOT, run_root=run, runtime_path=CONFIG, code_revision=a.deployment_revision
    )
    now = datetime.fromisoformat("2026-10-01T08:01:00+08:00")
    return store, args, now, r, raw


def test_no_successor_activation_never_inherits_old_oci_mandate(tmp_path):
    assert control.tick(
        InMemoryPlatformStore(),
        repository_root=ROOT,
        run_root=tmp_path,
        runtime_path=CONFIG,
        code_revision="a" * 40,
    ) == {"state": "waiting_new_base24_activation"}


def test_intake_freezes_one_real_clock_for_all_retries(setup):
    store, args, now, _, _ = setup
    first = control.tick(store, **args, now=now)
    before = store.get_job(first["job_id"])
    again = control.tick(store, **args, now=now + timedelta(seconds=30))
    assert again == first
    assert store.get_job(first["job_id"]) == before
    assert before.input_ref["consumed_at"] == now.astimezone(timezone.utc).isoformat()
    assert len(store.list_jobs(P6_FORWARD_CYCLE_JOB)) == 1


def test_changed_same_date_source_is_rejected_without_second_job(setup):
    store, args, now, r, _ = setup
    control.tick(store, **args, now=now)
    path = r.daily_root / "CURRENT.json"
    pointer = json.loads(path.read_text())
    pointer["content_digest"] = "sha256:" + "9" * 64
    path.write_text(json.dumps(pointer))
    with pytest.raises(ForwardValidationContractError, match="intake_conflict"):
        control.tick(store, **args, now=now + timedelta(seconds=30))
    assert len(store.list_jobs(P6_FORWARD_CYCLE_JOB)) == 1


@pytest.mark.parametrize("mode", ["paused", "writable", "revision", "late", "prior_day"])
def test_gates_do_not_create_official_jobs(setup, monkeypatch, mode):
    store, args, now, r, raw = setup
    if mode == "paused":
        raw.update(state="paused", official_prospective_cycle_authorized=False)
        raw["decision_digest"] = digest_value(
            {k: v for k, v in raw.items() if k != "decision_digest"}
        )
        (args["run_root"] / "activation/base24-enabled.json").write_text(json.dumps(raw))
        assert control.tick(store, **args, now=now)["state"] == "paused"
    elif mode == "writable":
        monkeypatch.setattr(control, "_mount_is_read_only", lambda p: False)
        assert control.tick(store, **args, now=now)["state"].startswith("blocked_")
    elif mode == "revision":
        args["code_revision"] = "b" * 40
        with pytest.raises(ForwardValidationContractError, match="activation_binding"):
            control.tick(store, **args, now=now)
    elif mode == "late":
        with pytest.raises(ForwardValidationContractError, match="missed_next_open"):
            control.tick(store, **args, now=now + timedelta(hours=2))
    else:
        path = r.daily_root / "CURRENT.json"
        pointer = json.loads(path.read_text())
        pointer["market_date"] = "2026-09-29"
        path.write_text(json.dumps(pointer))
        assert control.tick(store, **args, now=now)["state"] == "waiting_post_activation_session"
    assert store.list_jobs(P6_FORWARD_CYCLE_JOB) == ()


def test_failed_cycle_is_preserved_and_never_mechanically_retried(setup):
    store, args, now, _, _ = setup
    first = control.tick(store, **args, now=now)
    job = store.get_job(first["job_id"])
    failed = replace(job, state="failed", error_reason="synthetic-unchanged-failure")
    store.update_job(failed)
    assert (
        control.tick(store, **args, now=now + timedelta(minutes=2))["state"]
        == "blocked_failed_cycle"
    )
    assert store.get_job(job.job_id) == failed
    assert len(store.list_jobs(P6_FORWARD_CYCLE_JOB)) == 1


def test_old_activation_failure_is_not_inherited_by_successor(setup):
    store, args, now, _, _ = setup
    first = control.tick(store, **args, now=now)
    job = store.get_job(first["job_id"])
    legacy_input = {
        **job.input_ref,
        "activation_decision": {"decision_digest": "sha256:" + "0" * 64},
    }
    legacy = replace(
        job,
        job_id="job_" + "0" * 32,
        idempotency_key="legacy-terminal-record",
        input_ref=legacy_input,
        state="failed",
        error_reason="preserved-legacy-failure",
        concurrency_key=None,
    )
    store.create_job(legacy)
    assert control.tick(store, **args, now=now + timedelta(seconds=30)) == first
    assert store.get_job(legacy.job_id) == legacy


def test_first_success_requires_independent_reproduction_before_next_admission(setup, monkeypatch):
    store, args, now, _, _ = setup
    first = control.tick(store, **args, now=now)
    job = store.get_job(first["job_id"])
    store.update_job(replace(job, state="succeeded", result_ref={"run_id": "synthetic"}))
    monkeypatch.setattr(
        control, "_first_official_reproduction_job", lambda *a: SimpleNamespace(job_id="repro")
    )
    monkeypatch.setattr(control, "_first_official_reproduction_complete", lambda *a: False)
    assert control.tick(store, **args, now=now + timedelta(seconds=30)) == {
        "state": "waiting_first_official_reproduction",
        "job_id": "repro",
    }
    assert len(store.list_jobs(P6_FORWARD_CYCLE_JOB)) == 1

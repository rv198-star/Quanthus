# ruff: noqa: E501
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import quanthus.forward_validation.controller as controller
from quanthus.forward_validation.activation import (
    load_owner_activation_mandate,
    load_prospective_runtime_config,
    make_activation_decision_payload,
)
from quanthus.forward_validation.official_cycle import OfficialCycleJobInput
from quanthus.forward_validation.serialization import digest_value
from quanthus.platform.jobs import P6_FORWARD_CYCLE_JOB
from quanthus.platform.models import JobRecord, UserRecord
from quanthus.platform.persistence import InMemoryPlatformStore

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "config/forward-validation/activation/p6-prospective-runtime.v1.json"
MANDATE = ROOT / "config/forward-validation/activation/p6-owner-activation-mandate.v1.json"
UTC = timezone.utc


def _official_input(runtime, activation: dict[str, object]) -> OfficialCycleJobInput:
    semantic = {
        "schema_version": "quanthus.p6-dwd-visibility-receipt.v1",
        "package_id": "dwd-daily-20260921-test",
        "market_date": "2026-09-21",
        "manifest_sha256": "sha256:" + "4" * 64,
        "content_digest": "sha256:" + "5" * 64,
        "ready_observed_at": "2026-09-21T12:00:00+00:00",
        "consumer_visible_at": "2026-09-21T12:01:00+00:00",
        "source_node": "test",
    }
    visibility = {**semantic, "receipt_digest": digest_value(semantic)}
    return OfficialCycleJobInput(
        cycle_id="p6-official-2026-09-21",
        runtime_sha256=runtime.config_sha256,
        activation_decision=activation,
        market_date=datetime.fromisoformat("2026-09-21T00:00:00+08:00").date(),
        package_id="dwd-daily-20260921-test",
        manifest_sha256="sha256:" + "4" * 64,
        content_digest="sha256:" + "5" * 64,
        visibility_receipt=visibility,
        consumed_at=datetime(2026, 9, 21, 12, 2, tzinfo=UTC),
    )


def _store_succeeded_first_official(
    store: InMemoryPlatformStore,
    value: OfficialCycleJobInput,
) -> JobRecord:
    now = datetime(2026, 9, 21, 12, 3, tzinfo=UTC)
    job = JobRecord(
        job_id="job_" + "9" * 32,
        kind=P6_FORWARD_CYCLE_JOB,
        actor_user_id="usr_test",
        idempotency_key=value.idempotency_key,
        input_ref=value.to_dict(),
        state="succeeded",
        progress=100,
        cancel_requested=False,
        attempt=1,
        result_ref={
            "run_id": "p6_run_" + value.input_digest[7:31],
            "manifest_digest": "sha256:" + "6" * 64,
            "summary": {
                "mode": "official_cycle",
                "official_evidence": True,
                "market_date": "2026-09-21",
                "collection_session_count": 1,
                "tail_session_count": 0,
            },
        },
        error_reason=None,
        created_at=now,
        updated_at=now,
        completed_at=now,
        queue="replay",
        input_digest=value.input_digest,
        concurrency_key=value.concurrency_key,
        available_at=now,
        stage="succeeded",
        started_at=now,
        claimed_by="worker-test",
        claim_generation=1,
    )
    store.create_job(job)
    return job


def test_production_compose_arms_conditional_controller_and_mounts_p6_dwd_read_only() -> None:
    compose = (ROOT / "ops/platform/compose.yaml").read_text(encoding="utf-8")
    assert 'QUANTHUS_P6_CONTROLLER_ENABLED: "1"' in compose
    assert "/home/ubuntu/data/quanthus/dwd-daily:/data/dwd-daily:ro" in compose
    assert "/srv/quanthus/dwd-dws-v2.1/base/dwd-base-202607-v2.1:/srv/quanthus/dwd-dws-v2.1/base/dwd-base-202607-v2.1:ro" in compose
    assert "/srv/quanthus/dwd-dws-v2.1/corrections/dwd-base-202607-v2.1-correction-20260813-001:/srv/quanthus/dwd-dws-v2.1/corrections/dwd-base-202607-v2.1-correction-20260813-001:ro" in compose


def test_owner_mandate_is_digest_bound_to_runtime_and_expected_predecessor() -> None:
    runtime = load_prospective_runtime_config(RUNTIME, root=ROOT)
    mandate = load_owner_activation_mandate(MANDATE, runtime=runtime)
    assert mandate.runtime_sha256 == runtime.config_sha256
    assert mandate.expected_previous_production_revision == (
        "594845fa4882c9ed5107be9859ceabfc52356c56"
    )
    assert "disabled_rehearsal_succeeds_through_real_replay_worker" in (
        mandate.activation_conditions
    )


def test_fixed_mcp_service_identity_can_attribute_jobs_when_interactive_login_is_inactive() -> None:
    store = InMemoryPlatformStore()
    store.create_user(
        UserRecord(
            user_id=controller._EXTERNAL_MCP_ACTOR_USER_ID,
            username="mcp-service",
            password_hash="disabled",
            role="viewer",
            active=False,
            mfa_secret_ciphertext=None,
            mfa_enabled=False,
            created_at=datetime(2026, 9, 18, 1, tzinfo=UTC),
        )
    )
    assert controller._actor_user_id(store) == controller._EXTERNAL_MCP_ACTOR_USER_ID


def test_controller_error_is_persisted_as_non_executable_diagnostic_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryPlatformStore()
    monkeypatch.setattr(controller, "_actor_user_id", lambda _store: "usr_test")
    job_id = controller.record_controller_error(
        store,
        code_revision="a" * 40,
        error=RuntimeError("synthetic-controller-failure"),
    )
    assert job_id is not None
    job = store.get_job(job_id)
    assert job is not None
    assert job.kind == controller.P6_CONTROLLER_DIAGNOSTIC_JOB
    assert job.state == "failed"
    assert job.queue == "controller"
    assert job.stage == "controller-error"
    assert job.error_reason == "RuntimeError:synthetic-controller-failure"
    assert job.result_ref == {"error_type": "RuntimeError"}


def test_controller_seeds_real_disabled_rehearsal_before_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = InMemoryPlatformStore()
    monkeypatch.setattr(controller, "_actor_user_id", lambda _store: "usr_test")
    result = controller.controller_tick(
        store,
        repository_root=ROOT,
        p6_forward_run_root=tmp_path / "p6-forward",
        runtime_path=RUNTIME,
        mandate_path=MANDATE,
        code_revision="a" * 40,
        now=datetime(2026, 9, 18, 5, tzinfo=UTC),
    )
    assert result["state"] == "waiting_disabled_rehearsal"
    jobs = store.list_jobs(P6_FORWARD_CYCLE_JOB, 10)
    assert len(jobs) == 1
    assert jobs[0].input_ref["mode"] == "disabled_rehearsal"
    assert not (tmp_path / "p6-forward/activation/CURRENT.json").exists()


def test_controller_refuses_activation_until_dwd_mounts_are_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = InMemoryPlatformStore()
    fake_job = SimpleNamespace(job_id="job_" + "1" * 32, state="succeeded")
    monkeypatch.setattr(controller, "_disabled_rehearsal_job", lambda *_a, **_k: fake_job)
    monkeypatch.setattr(
        controller,
        "_verified_rehearsal_manifest",
        lambda *_a, **_k: "sha256:" + "2" * 64,
    )
    monkeypatch.setattr(controller, "_platform_green", lambda: True)
    monkeypatch.setattr(controller, "_dwd_mounts_read_only", lambda _runtime: False)
    result = controller.controller_tick(
        store,
        repository_root=ROOT,
        p6_forward_run_root=tmp_path / "p6-forward",
        runtime_path=RUNTIME,
        mandate_path=MANDATE,
        code_revision="a" * 40,
        now=datetime(2026, 9, 18, 5, tzinfo=UTC),
    )
    assert result["state"] == "blocked_dwd_mount_not_read_only"
    assert not (tmp_path / "p6-forward/activation/CURRENT.json").exists()


def test_controller_publishes_immutable_enabled_decision_only_after_all_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = InMemoryPlatformStore()
    fake_job = SimpleNamespace(job_id="job_" + "1" * 32, state="succeeded")
    monkeypatch.setattr(controller, "_disabled_rehearsal_job", lambda *_a, **_k: fake_job)
    monkeypatch.setattr(
        controller,
        "_verified_rehearsal_manifest",
        lambda *_a, **_k: "sha256:" + "2" * 64,
    )
    monkeypatch.setattr(controller, "_platform_green", lambda: True)
    monkeypatch.setattr(controller, "_dwd_mounts_read_only", lambda _runtime: True)
    monkeypatch.setattr(controller, "_submit_current_official", lambda *_a, **_k: None)
    result = controller.controller_tick(
        store,
        repository_root=ROOT,
        p6_forward_run_root=tmp_path / "p6-forward",
        runtime_path=RUNTIME,
        mandate_path=MANDATE,
        code_revision="a" * 40,
        now=datetime(2026, 9, 18, 5, tzinfo=UTC),
    )
    assert result["state"] == "enabled"
    pointer = json.loads(
        (tmp_path / "p6-forward/activation/CURRENT.json").read_text(encoding="utf-8")
    )
    decision = json.loads(
        (
            tmp_path
            / "p6-forward/activation"
            / str(pointer["decision_path"])
        ).read_text(encoding="utf-8")
    )
    assert decision["deployment_revision"] == "a" * 40
    assert decision["previous_production_revision"] == (
        "594845fa4882c9ed5107be9859ceabfc52356c56"
    )
    assert decision["official_prospective_cycle_authorized"] is True
    assert decision["broker_authorized"] is False
    assert decision["live_capital_authorized"] is False


def test_controller_rebinds_activation_for_maintenance_revision_only_before_official_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = load_prospective_runtime_config(RUNTIME, root=ROOT)
    mandate = load_owner_activation_mandate(MANDATE, runtime=runtime)
    activation_root = tmp_path / "p6-forward/activation"
    controller._publish_enabled_decision(
        activation_root=activation_root,
        runtime=runtime,
        mandate=mandate,
        code_revision="a" * 40,
        rehearsal_manifest_digest="sha256:" + "2" * 64,
        rollback_drill_digest="sha256:" + "3" * 64,
        now=datetime(2026, 9, 19, 1, tzinfo=UTC),
    )
    fake_rehearsal = SimpleNamespace(job_id="job_" + "1" * 32, state="succeeded")
    monkeypatch.setattr(controller, "_disabled_rehearsal_job", lambda *_a, **_k: fake_rehearsal)
    monkeypatch.setattr(
        controller,
        "_verified_rehearsal_manifest",
        lambda *_a, **_k: "sha256:" + "4" * 64,
    )
    monkeypatch.setattr(controller, "_platform_green", lambda: True)
    monkeypatch.setattr(controller, "_dwd_mounts_read_only", lambda _runtime: True)
    monkeypatch.setattr(controller, "_submit_current_official", lambda *_a, **_k: None)

    result = controller.controller_tick(
        InMemoryPlatformStore(),
        repository_root=ROOT,
        p6_forward_run_root=tmp_path / "p6-forward",
        runtime_path=RUNTIME,
        mandate_path=MANDATE,
        code_revision="b" * 40,
        now=datetime(2026, 9, 19, 2, tzinfo=UTC),
    )

    assert result["state"] == "enabled"
    pointer = json.loads(
        (activation_root / "CURRENT.json").read_text(encoding="utf-8")
    )
    rebound = json.loads(
        (activation_root / str(pointer["decision_path"])).read_text(encoding="utf-8")
    )
    assert rebound["deployment_revision"] == "b" * 40
    assert (
        activation_root / f"decisions/p6-activation-20260919-{'a' * 12}.json"
    ).is_file()
    assert (
        activation_root / f"decisions/p6-activation-20260919-{'b' * 12}.json"
    ).is_file()


def test_controller_refuses_maintenance_rebind_after_any_official_job(
    tmp_path: Path,
) -> None:
    runtime = load_prospective_runtime_config(RUNTIME, root=ROOT)
    mandate = load_owner_activation_mandate(MANDATE, runtime=runtime)
    activation_root = tmp_path / "p6-forward/activation"
    activation, _digest = controller._publish_enabled_decision(
        activation_root=activation_root,
        runtime=runtime,
        mandate=mandate,
        code_revision="a" * 40,
        rehearsal_manifest_digest="sha256:" + "2" * 64,
        rollback_drill_digest="sha256:" + "3" * 64,
        now=datetime(2026, 9, 19, 1, tzinfo=UTC),
    )
    store = InMemoryPlatformStore()
    _store_succeeded_first_official(store, _official_input(runtime, activation))

    with pytest.raises(RuntimeError, match="p6_post_evidence_revision_drift"):
        controller.controller_tick(
            store,
            repository_root=ROOT,
            p6_forward_run_root=tmp_path / "p6-forward",
            runtime_path=RUNTIME,
            mandate_path=MANDATE,
            code_revision="b" * 40,
            now=datetime(2026, 9, 22, 2, tzinfo=UTC),
        )


def test_controller_blocks_second_official_until_first_reproduction_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = load_prospective_runtime_config(RUNTIME, root=ROOT)
    mandate = load_owner_activation_mandate(MANDATE, runtime=runtime)
    activation = make_activation_decision_payload(
        runtime=runtime,
        mandate=mandate,
        decision_id="p6-activation-test",
        state="enabled",
        decision_at=datetime.fromisoformat("2026-09-19T09:00:00+08:00"),
        deployment_revision="a" * 40,
        disabled_rehearsal_manifest_digest="sha256:" + "2" * 64,
        rollback_drill_digest="sha256:" + "3" * 64,
    )
    store = InMemoryPlatformStore()
    primary = _store_succeeded_first_official(store, _official_input(runtime, activation))
    monkeypatch.setattr(controller, "_actor_user_id", lambda _store: "usr_test")
    monkeypatch.setattr(
        controller,
        "_activation_current",
        lambda *_a, **_k: (activation, str(activation["decision_digest"])),
    )
    monkeypatch.setattr(
        controller,
        "_submit_current_official",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("second official must wait for reproduction")
        ),
    )

    result = controller.controller_tick(
        store,
        repository_root=ROOT,
        p6_forward_run_root=tmp_path / "p6-forward",
        runtime_path=RUNTIME,
        mandate_path=MANDATE,
        code_revision="a" * 40,
        now=datetime(2026, 9, 21, 12, 4, tzinfo=UTC),
    )

    assert result["state"] == "waiting_first_official_reproduction"
    assert result["job_id"] == primary.job_id
    reproduction_jobs = [
        job
        for job in store.list_jobs(P6_FORWARD_CYCLE_JOB, 10)
        if job.input_ref.get("mode") == "official_reproduction"
    ]
    assert len(reproduction_jobs) == 1
    assert reproduction_jobs[0].state == "queued"


def test_controller_allows_collection_after_first_reproduction_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = load_prospective_runtime_config(RUNTIME, root=ROOT)
    mandate = load_owner_activation_mandate(MANDATE, runtime=runtime)
    activation = make_activation_decision_payload(
        runtime=runtime,
        mandate=mandate,
        decision_id="p6-activation-test",
        state="enabled",
        decision_at=datetime.fromisoformat("2026-09-19T09:00:00+08:00"),
        deployment_revision="a" * 40,
        disabled_rehearsal_manifest_digest="sha256:" + "2" * 64,
        rollback_drill_digest="sha256:" + "3" * 64,
    )
    store = InMemoryPlatformStore()
    primary = _store_succeeded_first_official(store, _official_input(runtime, activation))
    primary_result = primary.result_ref
    assert isinstance(primary_result, dict)
    reproduction_job = SimpleNamespace(
        job_id="job_" + "7" * 32,
        state="succeeded",
        result_ref={
            "summary": {
                "mode": "official_reproduction",
                "official_evidence": False,
                "semantic_match": True,
                "primary_run_id": primary_result["run_id"],
                "primary_manifest_digest": primary_result["manifest_digest"],
                "reproduced_manifest_digest": primary_result["manifest_digest"],
            }
        },
    )
    monkeypatch.setattr(
        controller,
        "_activation_current",
        lambda *_a, **_k: (activation, str(activation["decision_digest"])),
    )
    monkeypatch.setattr(
        controller,
        "_first_official_reproduction_job",
        lambda *_a, **_k: reproduction_job,
    )
    monkeypatch.setattr(controller, "_submit_current_official", lambda *_a, **_k: None)

    result = controller.controller_tick(
        store,
        repository_root=ROOT,
        p6_forward_run_root=tmp_path / "p6-forward",
        runtime_path=RUNTIME,
        mandate_path=MANDATE,
        code_revision="a" * 40,
        now=datetime(2026, 9, 21, 12, 4, tzinfo=UTC),
    )

    assert result["state"] == "enabled"
    assert result["first_official_reproduction_complete"] is True
    assert result["reproduction_job_id"] == reproduction_job.job_id


def test_controller_submits_current_ready_package_only_after_enabled_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = load_prospective_runtime_config(RUNTIME, root=ROOT)
    mandate = load_owner_activation_mandate(MANDATE, runtime=runtime)
    runtime = replace(runtime, daily_root=tmp_path / "daily")
    runtime.daily_root.mkdir()
    activation = make_activation_decision_payload(
        runtime=runtime,
        mandate=mandate,
        decision_id="p6-activation-test",
        state="enabled",
        decision_at=datetime.fromisoformat("2026-09-18T09:00:00+08:00"),
        deployment_revision="a" * 40,
        disabled_rehearsal_manifest_digest="sha256:" + "2" * 64,
        rollback_drill_digest="sha256:" + "3" * 64,
    )
    manifest_sha = "sha256:" + "4" * 64
    content_digest = "sha256:" + "5" * 64
    pointer = {
        "schema_version": 1,
        "kind": "cn01_daily_dwd_current",
        "market_date": "2026-09-21",
        "package_id": "dwd-daily-20260921-test",
        "manifest_path": (
            "packages/2026-09-21/dwd-daily-20260921-test/manifest.json"
        ),
        "manifest_sha256": manifest_sha,
        "content_digest": content_digest,
    }
    (runtime.daily_root / "CURRENT.json").write_text(
        json.dumps(pointer), encoding="utf-8"
    )
    receipt_dir = runtime.daily_root / "receipts/2026-09-21"
    receipt_dir.mkdir(parents=True)
    (receipt_dir / "dwd-daily-20260921-test.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "oci_cn01_dwd_daily_sync",
                "market_date": "2026-09-21",
                "package_id": "dwd-daily-20260921-test",
                "manifest_sha256": manifest_sha,
                "content_digest": content_digest,
                "payload_bytes": 123,
                "changed": True,
                "synced_at": "2026-09-21T12:01:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    store = InMemoryPlatformStore()
    monkeypatch.setattr(controller, "_actor_user_id", lambda _store: "usr_test")
    job = controller._submit_current_official(
        store,
        runtime=runtime,
        activation_payload=activation,
        now=datetime(2026, 9, 21, 12, 2, tzinfo=UTC),
    )
    assert job is not None
    assert job.input_ref["mode"] == "official_cycle"
    assert job.input_ref["activation_decision"]["decision_digest"] == (
        activation["decision_digest"]
    )
    assert job.input_ref["prior_run_id"] is None

def test_failed_official_job_blocks_resubmission_and_later_dates(tmp_path: Path) -> None:
    runtime = load_prospective_runtime_config(RUNTIME, root=ROOT)
    mandate = load_owner_activation_mandate(MANDATE, runtime=runtime)
    activation = make_activation_decision_payload(
        runtime=runtime,
        mandate=mandate,
        decision_id="p6-test-existing-failed",
        state="enabled",
        decision_at=datetime(2026, 9, 18, 9, tzinfo=UTC),
        deployment_revision="a" * 40,
        disabled_rehearsal_manifest_digest="sha256:" + "2" * 64,
        rollback_drill_digest="sha256:" + "3" * 64,
    )
    upstream = InMemoryPlatformStore()
    succeeded = _store_succeeded_first_official(upstream, _official_input(runtime, activation))
    failed = replace(
        succeeded, state="failed", result_ref=None, error_reason="p6_forward_child_exit_1"
    )
    store = InMemoryPlatformStore()
    store.create_job(failed)
    unavailable_root = replace(runtime, daily_root=tmp_path / "not-accessed")
    for moment in (datetime(2026, 9, 23, 12, tzinfo=UTC), datetime(2026, 9, 24, 12, tzinfo=UTC)):
        returned = controller._submit_current_official(
            store,
            runtime=unavailable_root,
            activation_payload=activation,
            now=moment,
        )
        assert returned is not None
        assert returned.job_id == failed.job_id
        assert returned.state == "failed"
        assert len(store.list_jobs(P6_FORWARD_CYCLE_JOB, 100)) == 1


def test_daily_core_controller_binds_schema2_sync_receipt_without_legacy_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib

    from quanthus.forward_validation.package_locator import resolve_daily_package

    root = tmp_path / "core"
    day = "2026-10-12"
    package_id = "dwd-daily-20261012-aaaaaaaaaaaa-bbbbbbb"
    entry = root / "packages" / day / "aaaaaaaaaaaa-bbbbbbb"
    entry.mkdir(parents=True)
    manifest = json.dumps({"package_id": package_id, "market_date": day}).encode()
    (entry / "manifest.json").write_bytes(manifest)
    manifest_sha = "sha256:" + hashlib.sha256(manifest).hexdigest()
    pointer = {
        "kind": "cn01_daily_core_dwd_current",
        "schema_version": 2,
        "product_scope": "daily-core-v1",
        "market_date": day,
        "package_id": package_id,
        "manifest_path": f"packages/{day}/aaaaaaaaaaaa-bbbbbbb/manifest.json",
        "manifest_sha256": manifest_sha,
        "content_digest": "sha256:" + "3" * 64,
    }
    (root / "CURRENT_DAILY_CORE.json").write_text(json.dumps(pointer))
    receipt_dir = root / "receipts" / day
    receipt_dir.mkdir(parents=True)
    receipt = {
        "kind": "oci_cn01_daily_core_dwd_sync",
        "schema_version": 2,
        "product_scope": "daily-core-v1",
        "market_date": day,
        "package_id": package_id,
        "manifest_sha256": manifest_sha,
        "content_digest": pointer["content_digest"],
        "synced_at": "2026-10-12T11:30:00+00:00",
    }
    (receipt_dir / (package_id + ".json")).write_text(json.dumps(receipt))
    value = controller._visibility_from_sync_receipt(root, pointer, product_scope="daily-core-v1")
    assert value["source_node"] == "oci-cn01-daily-core-sync"
    assert value["package_id"] == package_id
    assert (
        resolve_daily_package(
            root,
            datetime.fromisoformat(day).date(),
            package_id,
            manifest_sha256=manifest_sha,
            manifest_path=pointer["manifest_path"],
        )
        == entry
    )
    with pytest.raises(RuntimeError, match="sync_receipt_binding_mismatch"):
        controller._visibility_from_sync_receipt(root, pointer, product_scope="legacy-four-lane")

    runtime = load_prospective_runtime_config(
        ROOT / "config/forward-validation/activation/p6-prospective-runtime-r1.v1.json",
        root=ROOT,
    )
    runtime = replace(runtime, daily_root=root)
    mandate = load_owner_activation_mandate(
        MANDATE, runtime=load_prospective_runtime_config(RUNTIME, root=ROOT)
    )
    original = load_prospective_runtime_config(RUNTIME, root=ROOT)
    # The payload must be R1-runtime bound, but an old legacy Job can never
    # participate in R1's idempotency, progress or budget counters.
    r1_mandate = replace(mandate, runtime_sha256=runtime.config_sha256)
    activation = make_activation_decision_payload(
        runtime=runtime, mandate=r1_mandate, decision_id="r1-test",
        state="enabled", decision_at=datetime(2026, 10, 9, 6, tzinfo=UTC),
        deployment_revision="a" * 40,
        disabled_rehearsal_manifest_digest="sha256:" + "2" * 64,
        rollback_drill_digest="sha256:" + "3" * 64,
    )
    store = InMemoryPlatformStore()
    monkeypatch.setattr(controller, "_actor_user_id", lambda _store: "usr_test")
    monkeypatched_original = _official_input(
        original,
        make_activation_decision_payload(
            runtime=original,
            mandate=mandate,
            decision_id="legacy-test-2",
            state="enabled",
            decision_at=datetime(2026, 9, 19, 6, tzinfo=UTC),
            deployment_revision="a" * 40,
            disabled_rehearsal_manifest_digest="sha256:" + "2" * 64,
            rollback_drill_digest="sha256:" + "3" * 64,
        ),
    )
    old_store = InMemoryPlatformStore()
    old_job = _store_succeeded_first_official(old_store, monkeypatched_original)
    store.create_job(replace(old_job, state="failed", result_ref=None))
    job = controller._submit_current_official(
        store,
        runtime=runtime,
        activation_payload=activation,
        now=datetime(2026, 10, 12, 11, 35, tzinfo=UTC),
    )
    assert job is not None and job.state == "queued"
    assert job.input_ref["runtime_sha256"] == runtime.config_sha256
    assert len(store.list_jobs(P6_FORWARD_CYCLE_JOB, 100)) == 2

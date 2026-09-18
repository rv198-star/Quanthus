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
from quanthus.platform.jobs import P6_FORWARD_CYCLE_JOB
from quanthus.platform.persistence import InMemoryPlatformStore

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "config/forward-validation/activation/p6-prospective-runtime.v1.json"
MANDATE = ROOT / "config/forward-validation/activation/p6-owner-activation-mandate.v1.json"
UTC = timezone.utc


def test_production_compose_arms_conditional_controller_and_mounts_p6_dwd_read_only() -> None:
    compose = (ROOT / "ops/platform/compose.yaml").read_text(encoding="utf-8")
    assert 'QUANTHUS_P6_CONTROLLER_ENABLED: "${QUANTHUS_P6_CONTROLLER_ENABLED:-1}"' in compose
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

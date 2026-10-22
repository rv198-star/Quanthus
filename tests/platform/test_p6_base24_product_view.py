from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from quanthus.forward_validation.product_view import (
    ForwardProductViewError,
    ForwardValidationProductView,
)
from quanthus.forward_validation.serialization import digest_value
from quanthus.platform.jobs import P6_FORWARD_CYCLE_JOB, input_digest
from quanthus.platform.models import JobRecord
from quanthus.platform.persistence import InMemoryPlatformStore
from tests.platform.test_forward_validation_product_view import _write_enabled_activation
from tests.unit.test_p6_base24_cycle import CONFIG, ROOT, activation


def _decision(root: Path, *, state: str = "enabled") -> dict:
    a = activation()
    raw = {
        **a.semantic_payload(),
        "state": state,
        "official_prospective_cycle_authorized": state == "enabled",
    }
    raw["decision_digest"] = digest_value(raw)
    p = root / "activation/base24-enabled.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(raw))
    return raw


def _view(root, store, **changes):
    return ForwardValidationProductView(
        ROOT,
        store,
        runtime_path=CONFIG,
        forward_run_root=root,
        worker_revision=changes.pop("worker_revision", activation().deployment_revision),
        **changes,
    )


def _job(raw: dict, *, name: str, count: int, runtime_sha: str | None = None) -> JobRecord:
    now = datetime.now(timezone.utc)
    payload = {
        "mode": "official_cycle",
        "activation_decision": raw,
        "runtime_sha256": runtime_sha or raw["runtime_sha256"],
        "market_date": "2026-09-30",
    }
    return JobRecord(
        job_id="job_" + name,
        kind=P6_FORWARD_CYCLE_JOB,
        actor_user_id="synthetic",
        idempotency_key=name,
        input_ref=payload,
        state="succeeded",
        progress=100,
        cancel_requested=False,
        attempt=1,
        result_ref={
            "summary": {
                "mode": "official_cycle",
                "official_evidence": True,
                "market_date": "2026-09-30",
                "collection_session_count": count,
                "tail_session_count": 0,
            }
        },
        error_reason=None,
        created_at=now,
        updated_at=now,
        completed_at=now,
        input_digest=input_digest(payload),
        queue="replay",
    )


def test_base24_view_ignores_legacy_activation_and_old_jobs(tmp_path):
    old = _write_enabled_activation(tmp_path / "old-candidates")
    store = InMemoryPlatformStore()
    store.create_job(_job(old, name="legacy", count=55))
    view = _view(tmp_path / "new-runtime", store, candidate_run_root=tmp_path / "old-candidates")
    observed = view.candidate("candidate.p4-trend-risk", "1.0.1")
    assert observed["admission"]["state"] == "disabled"
    assert observed["evidence"]["official_collection_sessions_completed"] == 0
    assert observed["latest_forward_job"] is None


@pytest.mark.parametrize("state", ["enabled", "paused", "disabled"])
def test_base24_view_only_counts_this_activation_and_preserves_old_jobs(tmp_path, state):
    old_enabled = _decision(tmp_path)
    raw = _decision(tmp_path, state=state)
    store = InMemoryPlatformStore()
    store.create_job(_job(old_enabled, name="current", count=1))
    other = {**raw, "decision_digest": "sha256:" + "f" * 64}
    store.create_job(_job(other, name="other-activation", count=60))
    store.create_job(_job(raw, name="wrong-runtime", count=66, runtime_sha="sha256:" + "e" * 64))
    view = _view(tmp_path, store)
    observed = view.candidate("candidate.p4-trend-risk", "1.0.1")
    assert observed["admission"]["state"] == state
    assert observed["admission"]["official_prospective_cycle_authorized"] is (state == "enabled")
    assert observed["evidence"]["official_collection_sessions_completed"] == (
        1 if state == "enabled" else 0
    )
    if state == "enabled":
        assert observed["latest_forward_job"]["job_id"] == "job_current"
    else:
        assert observed["latest_forward_job"] is None
    assert len(store.list_jobs(P6_FORWARD_CYCLE_JOB)) == 3
    assert observed["source_digests"]["runtime"] == raw["runtime_sha256"]


@pytest.mark.parametrize("corruption", ["revision", "digest", "future", "symlink"])
def test_base24_view_rejects_wrong_identity_or_path(tmp_path, corruption):
    raw = _decision(tmp_path)
    p = tmp_path / "activation/base24-enabled.json"
    if corruption == "revision":
        view = _view(tmp_path, InMemoryPlatformStore(), worker_revision="b" * 40)
    else:
        view = _view(tmp_path, InMemoryPlatformStore())
        if corruption == "digest":
            raw["runtime_sha256"] = "sha256:" + "b" * 64
            raw["decision_digest"] = digest_value(
                {k: v for k, v in raw.items() if k != "decision_digest"}
            )
            p.write_text(json.dumps(raw))
        elif corruption == "future":
            future = datetime.now(timezone.utc) + timedelta(days=2)
            raw["decision_at"] = future.isoformat()
            from zoneinfo import ZoneInfo

            raw["activation_market_date"] = (
                future.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
            )
            raw["decision_digest"] = digest_value(
                {k: v for k, v in raw.items() if k != "decision_digest"}
            )
            p.write_text(json.dumps(raw))
        else:
            other = tmp_path / "external.json"
            p.rename(other)
            p.symlink_to(other)
    with pytest.raises(ForwardProductViewError, match="activation_projection_drift|path_escape"):
        view.health_summary()


def test_base24_view_does_not_silently_truncate_job_census(tmp_path):
    raw = _decision(tmp_path)
    store = InMemoryPlatformStore()
    for i in range(200):
        store.create_job(_job(raw, name=f"{i:03}", count=1))
    with pytest.raises(ForwardProductViewError, match="census"):
        _view(tmp_path, store).health_summary()


def test_base24_view_configuration_requires_all_explicit_bindings(tmp_path):
    with pytest.raises(ForwardProductViewError, match="configuration"):
        ForwardValidationProductView(ROOT, InMemoryPlatformStore(), runtime_path=CONFIG)


def test_base24_contract_import_stays_lean():
    code = """
import importlib.abc, sys
class RejectHeavy(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  blocked=('duckdb','pyarrow','quanthus.backtest','quanthus.pipelines',
           'quanthus.forward_validation.base24_runtime')
  if fullname.startswith(blocked):
   raise AssertionError('heavy runtime imported: '+fullname)
sys.meta_path.insert(0, RejectHeavy())
from pathlib import Path
from quanthus.forward_validation.base24_contract import load_runtime
root=Path.cwd()
config=root/'config/forward-validation/activation/p6-base24-cycle-runtime.v1.json'
assert load_runtime(config,root=root)
from quanthus.forward_validation.product_view import ForwardValidationProductView
"""
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True, capture_output=True)


def test_platform_environment_wires_explicit_base24_projection(monkeypatch, tmp_path):
    from quanthus.platform import composition

    monkeypatch.setenv("QUANTHUS_PLATFORM_DATABASE_URL", "inert-synthetic-dsn")
    monkeypatch.setenv("QUANTHUS_PLATFORM_MFA_KEY", "inert-synthetic-test-key")
    monkeypatch.setenv("QUANTHUS_P6_RUNTIME_CONFIG", str(CONFIG))
    monkeypatch.setenv("QUANTHUS_P6_FORWARD_RUN_ROOT", str(tmp_path))
    monkeypatch.setenv("QUANTHUS_P6_EXPECTED_WORKER_REVISION", "a" * 40)
    monkeypatch.setattr(composition, "create_platform_kernel", lambda **kwargs: kwargs)
    observed = composition.platform_kernel_from_env()
    assert observed["p6_runtime_path"] == CONFIG
    assert observed["p6_forward_run_root"] == tmp_path
    assert observed["p6_worker_revision"] == "a" * 40

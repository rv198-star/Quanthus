from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

import quanthus.platform.worker as worker_module
from quanthus.forward_validation.contracts import ForwardValidationContractError
from quanthus.forward_validation.runner import (
    ForwardCycleJobInput,
    load_forward_worker_config,
    make_disabled_rehearsal_input,
    run_disabled_rehearsal,
    verify_forward_run,
)
from quanthus.platform.jobs import (
    P6_FORWARD_CYCLE_JOB,
    input_digest,
    p6_forward_rehearsal_submission,
)
from quanthus.platform.models import JobRecord
from quanthus.platform.worker import ReplayWorker

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config/forward-validation/runtime/p6-forward-worker.v1.json"


def _input(rehearsal_id: str = "p6-gov002-disabled-rehearsal") -> ForwardCycleJobInput:
    config = load_forward_worker_config(CONFIG, root=ROOT)
    return make_disabled_rehearsal_input(rehearsal_id=rehearsal_id, config=config)


def _job(value: ForwardCycleJobInput, *, generation: int = 1, cancel: bool = False) -> JobRecord:
    now = datetime.now(timezone.utc)
    payload = value.to_dict()
    return JobRecord(
        job_id="job_" + "6" * 32,
        kind=P6_FORWARD_CYCLE_JOB,
        actor_user_id="usr_p6_test",
        idempotency_key=value.idempotency_key,
        input_ref=payload,
        state="running",
        progress=1,
        cancel_requested=cancel,
        attempt=1,
        result_ref=None,
        error_reason=None,
        created_at=now,
        updated_at=now,
        queue="replay",
        input_digest=input_digest(payload),
        concurrency_key=value.concurrency_key,
        available_at=now,
        stage="starting",
        claimed_by="worker-test",
        claim_generation=generation,
    )


class _Store:
    def __init__(self, job: JobRecord) -> None:
        self.job = job
        self.updates: list[tuple[str, int, str, int, dict[str, object]]] = []
        self.terminal: tuple[tuple[object, ...], dict[str, object]] | None = None
        self.renew = True

    def claim_job(self, queue: str, worker_id: str, lease_seconds: int) -> JobRecord:
        assert (queue, worker_id, lease_seconds) == ("replay", "worker-test", 300)
        return self.job

    def get_job(self, job_id: str) -> JobRecord:
        assert job_id == self.job.job_id
        return self.job

    def update_claimed_job(
        self,
        job_id: str,
        generation: int,
        *,
        stage: str,
        progress: int,
        progress_detail: dict[str, object],
    ) -> JobRecord:
        assert job_id == self.job.job_id
        self.updates.append((job_id, generation, stage, progress, progress_detail))
        return self.job

    def renew_job_lease(self, *_args: object) -> bool:
        return self.renew

    def finish_claimed_job(self, *args: object, **kwargs: object) -> JobRecord:
        self.terminal = (args, kwargs)
        return self.job


def _worker(tmp_path: Path, store: _Store, **changes: object) -> ReplayWorker:
    kwargs: dict[str, object] = {
        "worker_id": "worker-test",
        "run_root": tmp_path / "candidate",
        "lease_seconds": 300,
        "renew_seconds": 30.0,
        "terminate_seconds": 0.5,
        "p6_forward_run_root": tmp_path / "p6",
        "p6_forward_config_path": CONFIG,
    }
    kwargs.update(changes)
    return ReplayWorker(store, **kwargs)  # type: ignore[arg-type]


def test_disabled_rehearsal_contract_has_zero_runtime_side_effects(tmp_path: Path) -> None:
    config = load_forward_worker_config(CONFIG, root=ROOT)
    value = _input()
    run = run_disabled_rehearsal(
        job_input=value,
        config=config,
        staging=tmp_path / "staging",
        run_root=tmp_path / "runs",
    )

    summary = dict(run.summary)
    assert summary["official_evidence"] is False
    assert summary["dwd_read_count"] == 0
    assert summary["shared_projection_build_count"] == 0
    assert summary["shadow_write_count"] == 0
    assert summary["paper_write_count"] == 0
    assert summary["settlement_write_count"] == 0
    assert summary["scorecard_write_count"] == 0
    assert summary["notification_count"] == 0
    assert summary["broker_action_count"] == 0
    assert summary["live_capital_action_count"] == 0
    assert verify_forward_run(run.root, expected_input_digest=value.input_digest).manifest_digest == run.manifest_digest


def test_pre_activation_submission_rejects_official_shape() -> None:
    raw = _input().to_dict()
    raw["mode"] = "official"
    raw["activation_decision"] = {"state": "enabled"}

    with pytest.raises(ForwardValidationContractError, match="official_cycle_not_authorized"):
        p6_forward_rehearsal_submission(raw)


def test_rehearsal_submission_derives_stable_queue_keys() -> None:
    canonical, idempotency, concurrency = p6_forward_rehearsal_submission(_input().to_dict())
    assert canonical == _input().to_dict()
    assert idempotency == _input().idempotency_key
    assert concurrency == "p6-forward-cycle:disabled-rehearsal"


def test_replay_worker_executes_and_seals_disabled_rehearsal(tmp_path: Path) -> None:
    value = _input()
    store = _Store(_job(value))
    worker = _worker(tmp_path, store)

    assert worker.run_once() is True
    assert store.terminal is not None
    args, kwargs = store.terminal
    assert args[:2] == (store.job.job_id, 1)
    assert kwargs["state"] == "succeeded"
    result_ref = kwargs["result_ref"]
    assert isinstance(result_ref, dict)
    assert result_ref["input_digest"] == value.input_digest
    summary = result_ref["summary"]
    assert summary["official_evidence"] is False
    assert summary["dwd_read_count"] == 0
    assert any(update[2] == "publishing-run" for update in store.updates)


def test_second_claim_recovers_same_sealed_run_without_child_reexecution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = _input()
    first_store = _Store(_job(value))
    _worker(tmp_path, first_store).run_once()
    second_store = _Store(replace(_job(value), job_id="job_" + "7" * 32, claim_generation=2))
    worker = _worker(tmp_path, second_store)
    monkeypatch.setattr(
        worker_module,
        "p6_forward_child_command",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("child must not rerun")),
    )

    assert worker.run_once() is True
    assert second_store.terminal is not None
    _args, kwargs = second_store.terminal
    assert kwargs["state"] == "succeeded"
    assert any(update[2] == "recovering-sealed-run" for update in second_store.updates)


def test_partial_or_tampered_final_run_fails_closed(tmp_path: Path) -> None:
    value = _input()
    store = _Store(_job(value))
    worker = _worker(tmp_path, store)
    worker.run_once()
    result_ref = store.terminal[1]["result_ref"]  # type: ignore[index]
    artifact = Path(result_ref["artifact_ref"])  # type: ignore[index]
    (artifact / "rehearsal.json").write_text("{}\n", encoding="utf-8")
    next_store = _Store(replace(_job(value), job_id="job_" + "8" * 32, claim_generation=2))

    assert _worker(tmp_path, next_store).run_once() is True
    assert next_store.terminal is not None
    _args, kwargs = next_store.terminal
    assert kwargs["state"] == "failed"
    assert "forward_run_rehearsal" in str(kwargs["error_reason"])


def test_stale_claim_generation_publishes_nothing(tmp_path: Path) -> None:
    value = _input()
    store = _Store(_job(value, generation=1))
    store.job = replace(store.job, claim_generation=2)
    worker = _worker(tmp_path, store)

    worker._execute_p6_forward(store.job.job_id, 1, value.to_dict())
    assert store.terminal is None
    assert store.updates == []


def test_cancel_and_lease_loss_do_not_publish_forward_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeper = (sys.executable, "-c", "import time; time.sleep(10)")
    monkeypatch.setattr(worker_module, "p6_forward_child_command", lambda **_kwargs: sleeper)

    cancel_store = _Store(_job(_input("cancel"), cancel=True))
    cancel_worker = _worker(tmp_path / "cancel", cancel_store, renew_seconds=0.0)
    assert cancel_worker.run_once() is True
    assert cancel_store.terminal is not None
    assert cancel_store.terminal[1]["state"] == "canceled"

    lease_store = _Store(_job(_input("lease")))
    lease_store.renew = False
    lease_worker = _worker(tmp_path / "lease", lease_store, renew_seconds=0.0)
    assert lease_worker.run_once() is True
    assert lease_store.terminal is None


def test_memory_guard_is_reused_by_p6_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sleeper = (sys.executable, "-c", "import time; time.sleep(10)")
    monkeypatch.setattr(worker_module, "p6_forward_child_command", lambda **_kwargs: sleeper)
    memory_current = tmp_path / "memory.current"
    memory_stat = tmp_path / "memory.stat"
    memory_current.write_text("200\n", encoding="ascii")
    memory_stat.write_text("inactive_file 0\n", encoding="ascii")
    store = _Store(_job(_input("memory")))
    worker = _worker(
        tmp_path,
        store,
        memory_current_path=memory_current,
        memory_stat_path=memory_stat,
        memory_stop_bytes=100,
    )

    assert worker.run_once() is True
    assert store.terminal is not None
    _args, kwargs = store.terminal
    assert kwargs["state"] == "failed"
    assert str(kwargs["error_reason"]).startswith("replay_memory_guard_exceeded")

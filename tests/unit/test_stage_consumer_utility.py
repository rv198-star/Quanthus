from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from quanthus.platform import event_worker as worker_module
from quanthus.platform.event_worker import EventWorker, _structure_input, _structure_request_digest
from quanthus.platform.jobs import STRUCTURE_MVP_JOB, DurableJobQueue
from quanthus.platform.persistence import InMemoryPlatformStore
from quanthus.policy import find_project_root
from quanthus.stage_consumer_utility import (
    build_consumer_packet_set_from_replay,
    load_consumer_utility_request,
)
from quanthus.structure_v2_inputs import digest

from .test_structure_mvp_worker import _qualification_user

CODE = "c" * 40
REQUEST_PATH = "config/stage-learning/stage-consumer-utility-prereg.v1.json"


class DispatchStore(InMemoryPlatformStore):
    def claim_job(self, queue, worker_id, lease_seconds):
        for job in self.list_jobs():
            if job.queue == queue and job.state == "queued":
                claimed = replace(
                    job,
                    state="running",
                    claim_generation=1,
                    claimed_by=worker_id,
                    attempt=1,
                )
                self.update_job(claimed)
                return claimed
        return None

    def update_claimed_job(self, job_id, generation, **changes):
        job = self.get_job(job_id)
        assert job is not None and job.claim_generation == generation
        self.update_job(replace(job, **changes))

    def finish_claimed_job(self, job_id, generation, **changes):
        job = self.get_job(job_id)
        assert job is not None and job.claim_generation == generation
        self.finish_job(replace(job, **changes))


def _row(index: int, *, nested: bool) -> dict:
    return {
        "observation_identity": {
            "process_id": f"process-{index:02d}",
            "market_date": f"2020-02-{index + 1:02d}",
            "security_id": f"security-{index % 3}",
            "security_code": f"00000{index % 3 + 1}",
            "scale": 20,
        },
        "facts_only": {
            "process_id": f"process-{index:02d}",
            "phase": "unreclaimed",
            "unreclaimed_age_sessions": 5 + index,
            "support_distance_value": "-0.03",
            "post_break_rebound_value": "0.06" if nested else "0.03",
        },
        "development_shadow": {
            "context_assertions": [
                {"historical_stage_code": 10, "verdict": "TRUE"}
            ],
            "condition_assertions": (
                [{"historical_stage_code": 11, "verdict": "TRUE"}] if nested else []
            ),
            "modifier_assertions": [],
            "primary_stage": None,
        },
    }


def _replay() -> list[dict]:
    return [_row(index, nested=index >= 4) for index in range(10)]


def _queued_worker(tmp_path):
    store = DispatchStore()
    user = _qualification_user()
    store.create_user(user)
    queue = DurableJobQueue(store, {STRUCTURE_MVP_JOB: "event"})
    job, created = queue.submit(
        kind=STRUCTURE_MVP_JOB,
        actor_user_id=user.user_id,
        idempotency_key="p033-consumer-utility-packet-fixed-test",
        input_ref={"code_revision": CODE, "run_config": REQUEST_PATH},
    )
    assert created
    worker = EventWorker(
        store,
        worker_id="p033-consumer-utility-test",
        run_root=tmp_path,
        lease_seconds=300,
        renew_seconds=30,
        terminate_seconds=1,
    )
    return store, worker, job


def test_p033_prereg_freezes_consumer_utility_without_economic_or_pit_claim() -> None:
    request = load_consumer_utility_request(find_project_root() / REQUEST_PATH)
    assert request["work_item"] == "P3-STAGE-033"
    assert request["population"]["observation_count"] == 10
    assert request["population"]["new_market_or_dwd_read"] is False
    assert request["frozen_semantics"]["primary_stage"] is None
    assert request["evaluator_contract"]["arm_label_hidden_from_evaluator"] is True
    assert request["resource_budget"]["total_evaluations"] == 40
    assert "future_return" in request["metrics"]["not_metrics"]
    assert "strict PIT" in request["claim_ceiling"]


def test_p033_packets_are_balanced_blinded_and_deterministic() -> None:
    request = load_consumer_utility_request(find_project_root() / REQUEST_PATH)
    first = build_consumer_packet_set_from_replay(request, _replay())
    second = build_consumer_packet_set_from_replay(request, list(reversed(_replay())))
    assert first == second
    packets, assignments, answers, summary = first
    assert len(packets) == 40
    assert len(assignments) == 40
    assert len(answers) == 40
    assert summary["facts_only_packet_count"] == 20
    assert summary["facts_plus_layered_packet_count"] == 20
    assignment_by_id = {item["packet_id"]: item for item in assignments}
    for packet in packets:
        assignment = assignment_by_id[packet["packet_id"]]
        assert "arm" not in packet
        assert "strict_pit_claim" not in packet
        assert packet["knowledge_boundary"]["strict_pit_claim"] is False
        if assignment["arm"] == "facts_only":
            assert "layered_overlay" not in packet
        else:
            assert packet["layered_overlay"]["visible_historical_stage_codes"] in (
                [10],
                [10, 11],
            )
            assert packet["layered_overlay"]["primary_stage"] is None

    for role in request["consumer_roles"]:
        role_rows = [item for item in assignments if item["consumer_role"] == role]
        assert len(role_rows) == 10
        assert sum(item["arm"] == "facts_only" for item in role_rows) == 5
    for observation_index in range(10):
        observation_rows = [
            item for item in assignments if item["observation_index"] == observation_index
        ]
        assert sum(item["arm"] == "facts_only" for item in observation_rows) == 2
        assert sum(item["arm"] == "facts_plus_layered" for item in observation_rows) == 2


def test_p033_answer_key_preserves_parent_and_nested_truth() -> None:
    request = load_consumer_utility_request(find_project_root() / REQUEST_PATH)
    packets, _, answers, _ = build_consumer_packet_set_from_replay(request, _replay())
    packet_by_id = {item["packet_id"]: item for item in packets}
    for packet_id, answer in answers.items():
        identity = packet_by_id[packet_id]["observation_identity"]
        nested = int(identity["market_date"].split("-")[-1]) >= 5
        assert answer["Q1_PARENT_ACTIVE"] is True
        assert answer["Q2_NESTED_REBOUND_ACTIVE"] is nested
        assert answer["Q3_PARENT_RESOLVED_BY_REBOUND"] is False
        assert answer["Q4_CANONICAL_INTERPRETATION"] == (
            "structural_failure_with_nested_rebound" if nested else "structural_failure_only"
        )


def test_p033_fixed_request_is_governed_without_arbitrary_config() -> None:
    inputs = {"code_revision": CODE, "run_config": REQUEST_PATH}
    assert _structure_input(inputs) == inputs
    request = load_consumer_utility_request(find_project_root() / REQUEST_PATH)
    assert _structure_request_digest(REQUEST_PATH) == digest(request)
    with pytest.raises(RuntimeError):
        _structure_input({**inputs, "run_config": "/tmp/p033.json"})


def test_event_worker_dispatches_only_fixed_p033_packet_preparation(tmp_path, monkeypatch) -> None:
    store, worker, job = _queued_worker(tmp_path)
    request = load_consumer_utility_request(find_project_root() / REQUEST_PATH)
    monkeypatch.setattr(worker_module, "_structure_request_digest", lambda _: digest(request))
    commands = []

    def fake_verify(root, *, code_revision, request_digest):
        assert code_revision == CODE and request_digest == digest(request)
        return {
            "artifact_ref": str(root),
            "manifest_sha256": "sha256:" + "1" * 64,
            "request_digest": request_digest,
            "packet_count": 40,
            "assignment_count": 40,
            "answer_key_count": 40,
            "facts_only_packet_count": 20,
            "facts_plus_layered_packet_count": 20,
            "knowledge_view": "retrospective",
            "strict_pit_claim": False,
            "market_data_reads": 0,
            "dwd_provider_opens": 0,
            "experiment_result_available": False,
            "review_packets": [],
            "assignments": [],
            "scoring_key": {},
            "execution_authorized": False,
        }

    class Child:
        returncode = 0

        def __init__(self, command, **kwargs):
            commands.append(command)
            assert kwargs["stdout"] and kwargs["stderr"]
            assert command[1:3] == ("-m", "quanthus.stage_consumer_utility")
            assert command[command.index("--request") + 1] == REQUEST_PATH
            Path(command[command.index("--output") + 1]).mkdir(parents=True)

        def poll(self):
            return 0

    monkeypatch.setattr(worker_module, "verify_consumer_packet_artifact", fake_verify)
    monkeypatch.setattr(worker_module.subprocess, "Popen", Child)
    assert worker.run_once()
    completed = store.get_job(job.job_id)
    assert completed is not None and completed.state == "succeeded", completed.error_reason
    assert completed.result_ref["packet_count"] == 40
    assert completed.result_ref["facts_only_packet_count"] == 20
    assert completed.result_ref["facts_plus_layered_packet_count"] == 20
    assert completed.result_ref["strict_pit_claim"] is False
    assert completed.result_ref["experiment_result_available"] is False
    assert len(commands) == 1 and not worker.run_once()

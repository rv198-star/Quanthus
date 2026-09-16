from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from quanthus.expert_strategy.stage_semantic_consumption import (
    build_role_packets_from_replay,
    load_stage_expert_consumption_request,
)
from quanthus.platform import event_worker as worker_module
from quanthus.platform.event_worker import EventWorker, _structure_input, _structure_request_digest
from quanthus.platform.jobs import STRUCTURE_MVP_JOB, DurableJobQueue
from quanthus.platform.persistence import InMemoryPlatformStore
from quanthus.policy import find_project_root
from quanthus.structure_v2_inputs import digest

from .test_structure_mvp_worker import _qualification_user

CODE = "d" * 40
REQUEST_PATH = "config/expert-strategies/stage10-11-consumption-discovery.v1.json"


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


def _assertion(code: int, kind: str) -> dict:
    return {
        "proposition_id": f"stock-stage-{code}",
        "historical_stage_code": code,
        "proposition_kind": kind,
        "verdict": "TRUE",
        "evidence_grade": "accepted_development_projection",
        "fact_refs": [f"fact:fixture:{code}"],
        "provenance": {"source": "fixture"},
    }


def _row(index: int, *, nested: bool) -> dict:
    return {
        "observation_identity": {
            "process_id": f"process-{index:02d}",
            "market_date": f"2020-02-{index + 1:02d}",
            "security_id": "security-e5edcb299d289a18f54fbb1fac61de10",
            "security_code": "000001",
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
            "schema_version": "quanthus.stage-proposition-consumer-view.v1",
            "surface": "development_shadow",
            "control_state": "market_observation",
            "epistemic_status": "asserted",
            "status_reasons": [],
            "context_assertions": [_assertion(10, "context_or_structural_state")],
            "condition_assertions": (
                [_assertion(11, "event_or_nested_condition")] if nested else []
            ),
            "modifier_assertions": [],
            "fact_refs": ["fact:fixture:10"] + (["fact:fixture:11"] if nested else []),
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
        idempotency_key="p3-expert-006-stage-consumption-fixed-test",
        input_ref={"code_revision": CODE, "run_config": REQUEST_PATH},
    )
    assert created
    worker = EventWorker(
        store,
        worker_id="p3-expert-006-test",
        run_root=tmp_path,
        lease_seconds=300,
        renew_seconds=30,
        terminate_seconds=1,
    )
    return store, worker, job


def test_p3_expert_006_prereg_preserves_stage_and_action_boundaries() -> None:
    request = load_stage_expert_consumption_request(find_project_root() / REQUEST_PATH)
    assert request["work_item"] == "P3-EXPERT-006"
    assert request["population"]["observation_count"] == 10
    assert request["population"]["new_market_or_dwd_read"] is False
    assert request["population"]["permanent_blind_2025_access"] is False
    assert request["semantic_surface"]["facts_authority"] == "authoritative_substrate"
    assert request["semantic_surface"]["primary_stage"] is None
    assert request["semantic_surface"]["action_authority"] is False
    assert request["resource_budget"]["role_sessions"] == 4
    assert request["automatic_successor"] is None


def test_p3_expert_006_role_packets_are_deterministic_and_preserve_full_semantic_provenance(
) -> None:
    request = load_stage_expert_consumption_request(find_project_root() / REQUEST_PATH)
    first = build_role_packets_from_replay(request, _replay())
    second = build_role_packets_from_replay(request, list(reversed(_replay())))
    assert first == second
    packets, semantic_key, summary = first
    assert len(packets) == 4
    assert len(semantic_key) == 10
    assert summary["role_observation_evaluation_count"] == 40
    assert summary["stage_10_only_count"] == 4
    assert summary["stage_10_and_11_count"] == 6
    assert summary["market_data_reads"] == 0
    assert summary["dwd_provider_opens"] == 0
    assert summary["automatic_successor"] is None
    assert {packet["consumer_role"] for packet in packets} == set(request["consumer_roles"])
    for packet in packets:
        assert len(packet["observations"]) == 10
        advisory = packet["advisory_contract"]
        assert advisory["facts_authority"] == "authoritative_substrate"
        assert advisory["semantic_surface_authority"] == "development_advisory_only"
        assert advisory["action_authority"] is False
        assert advisory["trading_authority"] is False
        for row in packet["observations"]:
            surface = row["stage_semantic_surface"]
            assert surface["primary_stage"] is None
            assert surface["context_assertions"][0]["historical_stage_code"] == 10
            assert surface["context_assertions"][0]["evidence_grade"] == (
                "accepted_development_projection"
            )
            assert surface["context_assertions"][0]["fact_refs"]
            assert row["knowledge_boundary"]["strict_pit_claim"] is False


def test_p3_expert_006_semantic_key_tracks_parent_and_nested_truth() -> None:
    request = load_stage_expert_consumption_request(find_project_root() / REQUEST_PATH)
    _, semantic_key, _ = build_role_packets_from_replay(request, _replay())
    for index in range(10):
        key = semantic_key[str(index)]
        assert key["parent_context_active"] is True
        assert key["nested_rebound_active"] is (index >= 4)


def test_p3_expert_006_fixed_request_is_admitted_without_arbitrary_config() -> None:
    inputs = {"code_revision": CODE, "run_config": REQUEST_PATH}
    assert _structure_input(inputs) == inputs
    request = load_stage_expert_consumption_request(find_project_root() / REQUEST_PATH)
    assert _structure_request_digest(REQUEST_PATH) == digest(request)
    with pytest.raises(RuntimeError):
        _structure_input({**inputs, "run_config": "/tmp/p3-expert-006.json"})


def test_event_worker_dispatches_only_fixed_p3_expert_006_packet_preparation(
    tmp_path, monkeypatch
) -> None:
    store, worker, job = _queued_worker(tmp_path)
    request = load_stage_expert_consumption_request(find_project_root() / REQUEST_PATH)
    monkeypatch.setattr(worker_module, "_structure_request_digest", lambda _: digest(request))
    commands = []

    def fake_verify(root, *, code_revision, request_digest):
        assert code_revision == CODE and request_digest == digest(request)
        return {
            "artifact_ref": str(root),
            "manifest_sha256": "sha256:" + "1" * 64,
            "request_digest": request_digest,
            "role_packet_count": 4,
            "observation_count": 10,
            "role_observation_evaluation_count": 40,
            "knowledge_view": "retrospective",
            "strict_pit_claim": False,
            "market_data_reads": 0,
            "dwd_provider_opens": 0,
            "future_outcome_reads": 0,
            "execution_authorized": False,
            "automatic_successor": None,
            "review_packets": [],
        }

    class Child:
        returncode = 0

        def __init__(self, command, **kwargs):
            commands.append(command)
            assert kwargs["stdout"] and kwargs["stderr"]
            assert command[1:3] == (
                "-m",
                "quanthus.expert_strategy.stage_semantic_consumption",
            )
            assert command[command.index("--request") + 1] == REQUEST_PATH
            Path(command[command.index("--output") + 1]).mkdir(parents=True)

        def poll(self):
            return 0

    monkeypatch.setattr(worker_module, "verify_stage_expert_consumption_artifact", fake_verify)
    monkeypatch.setattr(worker_module.subprocess, "Popen", Child)
    assert worker.run_once()
    finished = store.get_job(job.job_id)
    assert finished is not None and finished.state == "succeeded", finished.error_reason
    assert len(commands) == 1 and not worker.run_once()
    assert finished.result_ref["role_packet_count"] == 4
    assert finished.result_ref["role_observation_evaluation_count"] == 40

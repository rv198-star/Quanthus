from __future__ import annotations

from pathlib import Path

import pytest

import quanthus.stage_learning.downside_lifecycle_reference_seal as resolution
import quanthus.stage_learning.worker as worker

JOB_ID = "stage_" + "a" * 32
MANIFEST_SHA = "sha256:" + "1" * 64
PACKET_SHA = "sha256:" + "2" * 64
CASE_MAP_DIGEST = "sha256:" + "3" * 64


def _paths(tmp_path: Path) -> worker.StageWorkerPaths:
    training = tmp_path / "training"
    repo = tmp_path / "repo"
    key = tmp_path / "key"
    (repo / ".git").mkdir(parents=True)
    key.write_text("test", encoding="utf-8")
    return worker.StageWorkerPaths(training, repo, key)


def _evidence() -> tuple[list[dict], list[dict]]:
    packets: list[dict] = []
    mapping: list[dict] = []
    for index in range(187):
        case_id = f"case_{index:04d}"
        cluster_index = index if index < 179 else index - 179
        cell = f"ERA{index % 2}/BOARD{index % 8}"
        packets.append({"case_id": case_id})
        mapping.append(
            {
                "case_id": case_id,
                "cluster_id": f"cluster_{cluster_index:04d}",
                "cell": cell,
                "window_start": "2020-01-01",
                "window_end": "2020-03-31",
            }
        )
    return packets, mapping


def _contract() -> dict:
    return {
        "bindings": {
            "packet_job": {
                "manifest_sha256": MANIFEST_SHA,
                "review_packet_sha256": PACKET_SHA,
                "case_map_digest": CASE_MAP_DIGEST,
            }
        },
        "frozen_gate": {
            "independent_cluster_count": 179,
            "minimum_stage12_definite_clusters": 80,
            "minimum_stage12_positive_clusters": 20,
            "minimum_stage12_negative_clusters": 20,
            "maximum_stage12_unjudgeable_cluster_rate": 0.15,
            "minimum_stage1_definite_applicable_clusters": 40,
            "minimum_stage1_positive_clusters": 20,
            "minimum_stage1_negative_clusters": 20,
            "maximum_stage1_unjudgeable_applicable_cluster_rate": 0.15,
            "pass_disposition": "PASS_CAPACITY_LIMITED_BLIND_REFERENCE_SUFFICIENCY",
            "fail_disposition": "CLOSED_BLIND_REFERENCE_INSUFFICIENT_NO_RESOURCE_REOPEN",
        },
    }


def _submission(role: str, packets: list[dict], *, disagree: bool = False) -> dict:
    opinions = []
    for index, packet in enumerate(packets):
        if index < 50:
            stage12 = "TRUE"
            stage1 = "TRUE" if index < 25 else "FALSE"
        else:
            stage12 = "FALSE"
            stage1 = "NOT_APPLICABLE"
        if disagree and index < 10:
            stage12 = "UNKNOWN"
            stage1 = "NOT_APPLICABLE"
        opinions.append(
            {
                "index": index,
                "case_id": packet["case_id"],
                "downside_context": stage12,
                "deceleration_stabilization": stage1,
                "reason": f"bounded {role} semantic judgement {index}",
            }
        )
    return {
        "schema_version": "quanthus.stage-downside-lifecycle-review-role-submission.v1",
        "work_item": "P3-STAGE-020-C",
        "parent_job_id": JOB_ID,
        "role": role,
        "source_manifest_sha256": MANIFEST_SHA,
        "review_packet_sha256": PACKET_SHA,
        "provenance": {
            "kind": "self_simulated",
            "origin_id": f"origin-{role}",
            "session_id": f"session-{role}",
            "model_id": "test-model",
        },
        "candidate_seen": False,
        "threshold_seen": False,
        "future_seen": False,
        "security_identity_seen": False,
        "cluster_identity_seen": False,
        "protected_partition_seen": False,
        "opinions": opinions,
    }


def test_row_resolution_preserves_context_and_nested_semantics() -> None:
    assert resolution.resolve_row(
        {"downside_context": "TRUE", "deceleration_stabilization": "TRUE"},
        {"downside_context": "TRUE", "deceleration_stabilization": "TRUE"},
    ) == ("TRUE", "TRUE")
    assert resolution.resolve_row(
        {"downside_context": "FALSE", "deceleration_stabilization": "NOT_APPLICABLE"},
        {"downside_context": "FALSE", "deceleration_stabilization": "NOT_APPLICABLE"},
    ) == ("FALSE", "NOT_APPLICABLE")
    assert resolution.resolve_row(
        {"downside_context": "TRUE", "deceleration_stabilization": "TRUE"},
        {"downside_context": "FALSE", "deceleration_stabilization": "NOT_APPLICABLE"},
    ) == ("AMBIGUOUS", "AMBIGUOUS")


def test_cluster_sufficiency_counts_identity_sets_not_rows() -> None:
    packets, case_map = _evidence()
    short = _submission("short_term", packets)["opinions"]
    fund = _submission("fund", packets)["opinions"]
    rows = resolution.resolve_rows(short, fund, case_map)
    summary = resolution.summarize(rows, case_map, _contract()["frozen_gate"])
    assert summary["row_count"] == 187
    assert summary["independent_cluster_count"] == 179
    assert summary["stage12"]["positive_cluster_count"] >= 20
    assert summary["stage12"]["negative_cluster_count"] >= 20
    assert summary["stage1"]["positive_cluster_count"] >= 20
    assert summary["stage1"]["negative_cluster_count"] >= 20
    assert summary["passed"] is True
    assert summary["p020_d_preregistration_authorized"] is True


def test_role_seals_are_independent_and_finalization_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    paths.ensure()
    packets, case_map = _evidence()
    contract = _contract()
    observed = {"job_id": JOB_ID}
    monkeypatch.setattr(worker, "_p020c_seal_contract", lambda paths: contract)
    monkeypatch.setattr(
        worker,
        "_p020c_packet_evidence",
        lambda paths, contract: (observed, tmp_path / "artifact", packets, case_map),
    )

    short_receipt = worker.seal_p020c_review_role(
        paths, JOB_ID, "short_term", _submission("short_term", packets)
    )
    assert short_receipt["sealed"] is True
    assert short_receipt["finalization_ready"] is False
    with pytest.raises(worker.StageWorkerError, match="both isolated role ledgers"):
        worker.finalize_p020c_review(paths, JOB_ID)

    fund_receipt = worker.seal_p020c_review_role(
        paths, JOB_ID, "fund", _submission("fund", packets)
    )
    assert fund_receipt["finalization_ready"] is True
    result = worker.finalize_p020c_review(paths, JOB_ID)
    assert result["finalized"] is True
    assert result["disposition"] == "PASS_CAPACITY_LIMITED_BLIND_REFERENCE_SUFFICIENCY"
    assert result["p020_d_preregistration_authorized"] is True
    assert result["candidate_scoring_authorized"] is False
    assert result["resource_reopen_authorized"] is False
    assert worker.finalize_p020c_review(paths, JOB_ID) == result


def test_role_submission_rejects_stage1_without_role_stage12_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    paths.ensure()
    packets, case_map = _evidence()
    contract = _contract()
    observed = {"job_id": JOB_ID}
    monkeypatch.setattr(worker, "_p020c_seal_contract", lambda paths: contract)
    monkeypatch.setattr(
        worker,
        "_p020c_packet_evidence",
        lambda paths, contract: (observed, tmp_path / "artifact", packets, case_map),
    )
    bad = _submission("short_term", packets)
    bad["opinions"][0]["downside_context"] = "FALSE"
    bad["opinions"][0]["deceleration_stabilization"] = "TRUE"
    with pytest.raises(worker.StageWorkerError, match="NOT_APPLICABLE"):
        worker.seal_p020c_review_role(paths, JOB_ID, "short_term", bad)

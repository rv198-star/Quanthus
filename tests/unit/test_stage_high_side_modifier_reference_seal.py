from __future__ import annotations

from pathlib import Path

import pytest

import quanthus.stage_learning.worker as worker
from quanthus.stage_learning import high_side_modifier_reference_seal as subject

JOB_ID = "stage_" + "b" * 32
MANIFEST_SHA = "sha256:" + "1" * 64
PACKET_SHA = "sha256:" + "2" * 64
PLAN_DIGEST = "sha256:" + "3" * 64
CASE_MAP_DIGEST = "sha256:" + "4" * 64


def _opinion(case_id: str, context: str, stage8: str) -> dict[str, str]:
    return {
        "case_id": case_id,
        "advancing_context": context,
        "upside_acceleration_extension": stage8,
        "reason": "bounded reason",
    }


def test_row_resolution_requires_role_agreement_and_context() -> None:
    assert subject.resolve_row(
        _opinion("a", "TRUE", "TRUE"), _opinion("a", "TRUE", "TRUE")
    ) == ("TRUE", "TRUE")
    assert subject.resolve_row(
        _opinion("a", "TRUE", "FALSE"), _opinion("a", "TRUE", "FALSE")
    ) == ("TRUE", "FALSE")
    assert subject.resolve_row(
        _opinion("a", "FALSE", "NOT_APPLICABLE"),
        _opinion("a", "FALSE", "NOT_APPLICABLE"),
    ) == ("FALSE", "NOT_APPLICABLE")
    assert subject.resolve_row(
        _opinion("a", "TRUE", "TRUE"),
        _opinion("a", "UNKNOWN", "NOT_APPLICABLE"),
    ) == ("AMBIGUOUS", "AMBIGUOUS")


def test_cluster_gate_counts_repeated_rows_once_but_keeps_time_specific_truth() -> None:
    case_map = [
        {
            "case_id": "case_a1",
            "cluster_id": "cluster_a",
            "cell": "E1/MAIN",
            "window_start": "2019-01-01",
            "window_end": "2019-03-31",
        },
        {
            "case_id": "case_a2",
            "cluster_id": "cluster_a",
            "cell": "E2/MAIN",
            "window_start": "2020-01-01",
            "window_end": "2020-03-31",
        },
        {
            "case_id": "case_b",
            "cluster_id": "cluster_b",
            "cell": "E3/MAIN",
            "window_start": "2020-04-01",
            "window_end": "2020-06-30",
        },
    ]
    short = [
        _opinion("case_a1", "TRUE", "TRUE"),
        _opinion("case_a2", "FALSE", "NOT_APPLICABLE"),
        _opinion("case_b", "TRUE", "FALSE"),
    ]
    fund = [dict(row) for row in short]
    rows = subject.resolve_rows(short, fund, case_map)
    summary = subject.summarize(
        rows,
        case_map,
        {
            "minimum_context_definite_clusters": 2,
            "minimum_context_positive_clusters": 1,
            "minimum_context_negative_clusters": 1,
            "maximum_context_unavailable_or_unjudgeable_cluster_rate": 0.5,
            "minimum_stage8_applicable_definite_clusters": 2,
            "minimum_stage8_positive_clusters": 1,
            "minimum_stage8_negative_clusters": 1,
            "maximum_stage8_unavailable_or_unjudgeable_applicable_cluster_rate": 0.5,
            "pass_disposition": "PASS",
            "fail_disposition": "FAIL",
        },
    )
    assert summary["independent_cluster_count"] == 2
    assert summary["advancing_context"]["positive_cluster_count"] == 2
    assert summary["advancing_context"]["negative_cluster_count"] == 1
    assert summary["advancing_context"]["temporal_polarity_overlap_cluster_count"] == 1
    assert summary["stage8"]["positive_cluster_count"] == 1
    assert summary["stage8"]["negative_cluster_count"] == 1
    # This three-cell fixture intentionally cannot satisfy the real eight-cell coverage gate.
    assert summary["gate_checks"]["context_definite_row_in_all_eight_cells"] is False
    assert summary["passed"] is False


def test_reference_gate_fails_closed_on_excess_unjudgeable_clusters() -> None:
    case_map = []
    short = []
    fund = []
    for index in range(8):
        case_id = f"case_{index}"
        case_map.append(
            {
                "case_id": case_id,
                "cluster_id": f"cluster_{index}",
                "cell": f"E{index}/B{index}",
                "window_start": "2020-01-01",
                "window_end": "2020-03-31",
            }
        )
        context = "UNKNOWN" if index in {0, 1} else ("TRUE" if index < 5 else "FALSE")
        stage8 = "TRUE" if context == "TRUE" else "NOT_APPLICABLE"
        short.append(_opinion(case_id, context, stage8))
        fund.append(dict(short[-1]))
    rows = subject.resolve_rows(short, fund, case_map)
    summary = subject.summarize(
        rows,
        case_map,
        {
            "minimum_context_definite_clusters": 4,
            "minimum_context_positive_clusters": 2,
            "minimum_context_negative_clusters": 2,
            "maximum_context_unavailable_or_unjudgeable_cluster_rate": 0.15,
            "minimum_stage8_applicable_definite_clusters": 2,
            "minimum_stage8_positive_clusters": 2,
            "minimum_stage8_negative_clusters": 0,
            "maximum_stage8_unavailable_or_unjudgeable_applicable_cluster_rate": 0.15,
            "pass_disposition": "PASS",
            "fail_disposition": "FAIL",
        },
    )
    assert summary["advancing_context"]["unjudgeable_cluster_count"] == 2
    assert summary["gate_checks"]["maximum_context_unjudgeable_cluster_rate"] is False
    assert summary["disposition"] == "FAIL"
    assert summary["p026_d_preregistration_authorized"] is False
    assert summary["stage9_authorized"] is False


def _worker_paths(tmp_path: Path) -> worker.StageWorkerPaths:
    training = tmp_path / "training"
    repo = tmp_path / "repo"
    key = tmp_path / "key"
    (repo / ".git").mkdir(parents=True)
    key.write_text("test", encoding="utf-8")
    return worker.StageWorkerPaths(training, repo, key)


def _worker_evidence() -> tuple[list[dict], list[dict]]:
    packets: list[dict] = []
    case_map: list[dict] = []
    for index in range(144):
        case_id = f"case_{index:04d}"
        cell = f"ERA{index // 18}/BOARD{index // 18}"
        packets.append({"case_id": case_id, "status": "AVAILABLE"})
        case_map.append(
            {
                "case_id": case_id,
                "cluster_id": f"cluster_{index:04d}",
                "cell": cell,
                "window_start": "2020-01-01",
                "window_end": "2020-03-31",
            }
        )
    return packets, case_map


def _worker_contract() -> dict:
    return {
        "output": {
            "role_ledger_schema": "quanthus.stage-high-side-modifier-review-role-ledger.v1",
            "reference_result_schema": "quanthus.stage-high-side-modifier-reference-result.v1",
        },
        "reference_sufficiency_gate": {
            "minimum_context_definite_clusters": 80,
            "minimum_context_positive_clusters": 20,
            "minimum_context_negative_clusters": 20,
            "maximum_context_unavailable_or_unjudgeable_cluster_rate": 0.15,
            "minimum_stage8_applicable_definite_clusters": 40,
            "minimum_stage8_positive_clusters": 20,
            "minimum_stage8_negative_clusters": 20,
            "maximum_stage8_unavailable_or_unjudgeable_applicable_cluster_rate": 0.15,
            "pass_disposition": "PASS_STAGE8_CAPACITY_LIMITED_BLIND_REFERENCE_SUFFICIENCY",
            "fail_disposition": "CLOSED_STAGE8_BLIND_REFERENCE_INSUFFICIENT_NO_REPAIR",
        },
    }


def _worker_observed() -> dict:
    return {
        "job_id": JOB_ID,
        "result_ref": {
            "manifest_sha256": MANIFEST_SHA,
            "high_side_modifier_reference_prepare": {
                "review_packet_sha256": PACKET_SHA,
                "resource_plan_digest": PLAN_DIGEST,
                "case_map_digest": CASE_MAP_DIGEST,
            },
        },
    }


def _worker_submission(role: str, packets: list[dict]) -> dict:
    opinions = []
    for index, packet in enumerate(packets):
        if index < 72:
            context = "TRUE"
            stage8 = "TRUE" if index < 36 else "FALSE"
        else:
            context = "FALSE"
            stage8 = "NOT_APPLICABLE"
        opinions.append(
            {
                "index": index,
                "case_id": packet["case_id"],
                "advancing_context": context,
                "upside_acceleration_extension": stage8,
                "reason": f"bounded {role} judgement {index}",
            }
        )
    return {
        "schema_version": "quanthus.stage-high-side-modifier-review-role-submission.v1",
        "work_item": "P3-STAGE-026",
        "parent_job_id": JOB_ID,
        "role": role,
        "source_manifest_sha256": MANIFEST_SHA,
        "review_packet_sha256": PACKET_SHA,
        "resource_plan_digest": PLAN_DIGEST,
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
        "stage9_seen": False,
        "protected_partition_seen": False,
        "opinions": opinions,
    }


def test_p026_role_seals_are_independent_and_finalize_idempotently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _worker_paths(tmp_path)
    paths.ensure()
    packets, case_map = _worker_evidence()
    contract = _worker_contract()
    observed = _worker_observed()
    monkeypatch.setattr(
        worker,
        "_p026_packet_evidence",
        lambda paths, job_id: (
            observed,
            tmp_path / "artifact",
            contract,
            packets,
            case_map,
        ),
    )
    short = worker.seal_p026_review_role(
        paths, JOB_ID, "short_term", _worker_submission("short_term", packets)
    )
    assert short["sealed"] is True
    assert short["finalization_ready"] is False
    with pytest.raises(worker.StageWorkerError, match="both isolated role ledgers"):
        worker.finalize_p026_review(paths, JOB_ID)
    fund = worker.seal_p026_review_role(
        paths, JOB_ID, "fund", _worker_submission("fund", packets)
    )
    assert fund["finalization_ready"] is True
    result = worker.finalize_p026_review(paths, JOB_ID)
    assert result["finalized"] is True
    assert result["disposition"] == "PASS_STAGE8_CAPACITY_LIMITED_BLIND_REFERENCE_SUFFICIENCY"
    assert result["p026_d_preregistration_authorized"] is True
    assert result["candidate_scoring_authorized"] is False
    assert result["stage9_authorized"] is False
    assert result["resource_reopen_authorized"] is False
    assert worker.finalize_p026_review(paths, JOB_ID) == result


def test_p026_submission_rejects_stage8_without_advancing_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _worker_paths(tmp_path)
    paths.ensure()
    packets, case_map = _worker_evidence()
    contract = _worker_contract()
    observed = _worker_observed()
    monkeypatch.setattr(
        worker,
        "_p026_packet_evidence",
        lambda paths, job_id: (
            observed,
            tmp_path / "artifact",
            contract,
            packets,
            case_map,
        ),
    )
    bad = _worker_submission("short_term", packets)
    bad["opinions"][0]["advancing_context"] = "FALSE"
    bad["opinions"][0]["upside_acceleration_extension"] = "TRUE"
    with pytest.raises(worker.StageWorkerError, match="NOT_APPLICABLE"):
        worker.seal_p026_review_role(paths, JOB_ID, "short_term", bad)

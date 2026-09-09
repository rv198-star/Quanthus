from __future__ import annotations

import json
from pathlib import Path

import pytest

from quanthus import stage_projection_artifact as artifact_module
from quanthus.stage_projection_artifact import (
    build_projection_qualification,
    load_projection_qualification_request,
    verify_stage_projection_artifact,
)
from quanthus.stage_projection_qualification import write_projection_qualification
from quanthus.structure_f9_run import write_result
from quanthus.structure_v2_inputs import digest

from .test_structure_f9_run import build as build_f9

ROOT = Path(__file__).resolve().parents[2]
CODE = "a" * 40
RUN_A = "run_" + "1" * 32
RUN_B = "run_" + "2" * 32


def _publish_source(root: Path):
    raw, _policy, admissions, readiness, structure, facts, projection, summary, stages = build_f9()
    runtime = {
        "schema_version": "quanthus.structure-f9-runtime.v1",
        "provider_builds": 1,
        "cross_job_cache_hits": 0,
        "provider_lifecycle": "job_local_TMP",
        "stages": stages,
    }
    write_result(
        root,
        raw,
        admissions,
        readiness,
        structure,
        facts,
        projection,
        summary,
        runtime,
    )
    return raw, facts, projection, summary


def _request(tmp_path: Path):
    event_root = tmp_path / "event-runs"
    source_root = event_root / "structure-runs"
    source_root.mkdir(parents=True)
    raw, facts, projection, summary = _publish_source(source_root / RUN_A)
    _publish_source(source_root / RUN_B)
    request = json.loads(
        (ROOT / "config/stage-10-11-projection-qualification.v1.json").read_text(
            encoding="utf-8"
        )
    )
    request["source_f9"].update(
        {
            "code_revision": CODE,
            "request_digest": digest(raw),
            "support_facts_digest": facts.digest,
            "legacy_projection_digest": digest([item.to_dict() for item in projection]),
            "run_ids": [RUN_A, RUN_B],
            "expected_counts": {
                "support_process_observation_count": len(facts.observations),
                "stage_10_confirmation_count": summary["report_counts"][
                    "stage_10_confirmation_count"
                ],
                "stage_10_context_count": summary["report_counts"][
                    "stage_10_active_observation_count"
                ],
                "stage_11_rebound_condition_count": summary["report_counts"][
                    "stage_11_active_observation_count"
                ],
            },
        }
    )
    return event_root, request


def test_fixed_qualification_request_rejects_authority_or_count_drift(tmp_path):
    raw = json.loads(
        (ROOT / "config/stage-10-11-projection-qualification.v1.json").read_text(
            encoding="utf-8"
        )
    )
    path = tmp_path / "request.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert load_projection_qualification_request(path)["authority"][
        "market_data_read_authorized"
    ] is False
    raw["authority"]["market_data_read_authorized"] = True
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="authority"):
        load_projection_qualification_request(path)


def test_qualification_replays_two_immutable_f9_artifacts_without_market_data(tmp_path):
    event_root, request = _request(tmp_path)
    payloads, summary = build_projection_qualification(request, event_root=event_root)
    assert payloads["projection-a.json"] == payloads["projection-b.json"]
    assert summary["decision"] == "PASS_FORMAL_PROJECTION_CONTRACT"
    assert summary["market_data_reads"] == 0
    assert summary["dwd_provider_opens"] == 0
    assert len(summary["source_results"]) == 2
    assert all(
        item["legacy_stage_10_predicate_mismatch_count"] == 0
        and item["legacy_stage_11_predicate_mismatch_count"] == 0
        for item in summary["source_results"]
    )
    counts = summary["source_results"][0]["counts"]
    assert counts["nested_10_11_candidate_count"] == counts[
        "stage_11_rebound_condition_count"
    ]
    assert counts["primary_stage_non_null_count"] == 0


def test_deep_verifier_reconstructs_formal_projection_from_source_artifacts(
    tmp_path, monkeypatch
):
    event_root, request = _request(tmp_path)
    project_root = tmp_path / "project"
    (project_root / "config").mkdir(parents=True)
    (project_root / "config/stage-10-11-projection.v1.json").write_bytes(
        (ROOT / "config/stage-10-11-projection.v1.json").read_bytes()
    )
    (project_root / "config/stage-10-11-projection-qualification.v1.json").write_text(
        json.dumps(request), encoding="utf-8"
    )
    monkeypatch.setattr(artifact_module, "find_project_root", lambda: project_root)
    monkeypatch.setattr(
        artifact_module,
        "load_projection_qualification_request",
        lambda _path: request,
    )
    monkeypatch.setenv("QUANTHUS_EVENT_RUN_ROOT", str(event_root))
    output = tmp_path / "projection-artifact"
    write_projection_qualification(output, request, code_revision=CODE)
    verified = verify_stage_projection_artifact(
        output,
        code_revision=CODE,
        request_digest=digest(request),
    )
    assert verified["legacy_predicate_compatibility"] is True
    assert verified["primary_stage_non_null_count"] == 0
    assert verified["p2_probe_count"] == verified["p2_fill_count"] == 0
    assert verified["execution_authorized"] is False

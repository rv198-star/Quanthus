from __future__ import annotations

import json
from pathlib import Path

import pytest

from quanthus.structure_f9_artifact import verify_structure_f9_artifact
from quanthus.structure_f9_run import load_request, run_from_admissions, write_result
from quanthus.structure_v2_inputs import InputPolicy, admit_daily, calculation_spec, digest

from .test_structure_support_facts import sequence
from .test_structure_v2_inputs import provider


def request() -> dict:
    return {
        "schema_version": "quanthus.structure-f9-run.v1",
        "provider_config": "config/replay-data-provider.structure-v2.development.v1.json",
        "scan_start": "2020-01-02",
        "report_start": "2020-01-02",
        "report_end": sequence()[-1].market_date.isoformat(),
        "security_codes": ["000001"],
        "lookbacks": [20, 60],
        "support_horizon_sessions": 60,
        "support_role": "breakout-start-rolling-low-candidate",
        "knowledge_view": "retrospective",
        "coordinate_policy": "raw-cny-action-epoch-reset-v1",
        "projection": {
            "stage_10_persistence_sessions": 5,
            "stage_10_failure_depth_abs_min": "0.02",
            "stage_11_rebound_min": "0.05",
            "official_label": False,
            "strategy_consumption_authorized": False,
        },
        "review_sample_limit": 12,
        "maximum_wall_seconds": 1200,
        "cold_result_job_budget": 2,
        "research_only": True,
        "execution_authorized": False,
        "economic_claim": False,
        "sealed_partition_access_authorized": False,
        "permanent_blind_access_authorized": False,
    }


def build():
    raw = request()
    policy = InputPolicy(digest(calculation_spec(raw)), ("000001",), "calendar-v1")
    admissions = tuple(admit_daily(item, policy) for item in sequence())
    readiness, structure, facts, projection, summary, stages = run_from_admissions(
        raw,
        admissions,
        policy,
        provider(),
        code_revision="a" * 40,
    )
    return raw, policy, admissions, readiness, structure, facts, projection, summary, stages


def test_fixed_request_rejects_projection_or_scope_drift(tmp_path):
    source = json.loads(
        Path("config/structure-f9-run.development.v1.json").read_text(encoding="utf-8")
    )
    path = tmp_path / "request.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    assert load_request(path)["projection"]["stage_11_rebound_min"] == "0.05"
    source["projection"]["stage_11_rebound_min"] = "0.04"
    path.write_text(json.dumps(source), encoding="utf-8")
    with pytest.raises(ValueError, match="projection"):
        load_request(path)


def test_run_builds_formal_f9_facts_and_prior_ref_projection():
    _raw, _policy, _admissions, _readiness, structure, facts, projection, summary, stages = build()
    assert structure.episodes
    assert facts.bindings and facts.observations
    assert summary["report_counts"]["support_break_count"] >= 1
    assert summary["report_counts"]["support_reclaim_count"] >= 1
    assert summary["report_counts"]["stage_10_confirmation_count"] >= 1
    assert summary["report_counts"]["stage_11_active_observation_count"] >= 1
    assert any(item.stage_11_active for item in projection)
    assert [item["stage"] for item in stages] == [
        "B2-admission",
        "B3-B5-base-structure",
        "B5-F9-support-process",
        "research-projection-10-11",
    ]
    assert summary["research_only"] is True
    assert summary["execution_authorized"] is False
    assert summary["economic_claim"] is False


def test_written_artifact_deep_reconstructs_exactly(tmp_path):
    raw, _policy, admissions, readiness, structure, facts, projection, summary, stages = build()
    runtime = {
        "schema_version": "quanthus.structure-f9-runtime.v1",
        "provider_builds": 1,
        "cross_job_cache_hits": 0,
        "provider_lifecycle": "job_local_TMP",
        "stages": stages,
    }
    artifact = tmp_path / "f9"
    write_result(
        artifact,
        raw,
        admissions,
        readiness,
        structure,
        facts,
        projection,
        summary,
        runtime,
    )
    verified = verify_structure_f9_artifact(
        artifact, code_revision="a" * 40, request_digest=digest(raw)
    )
    assert verified["support_facts_digest"] == facts.digest
    assert verified["fact_counts"] == summary["report_counts"]
    assert verified["official_label"] is False
    assert verified["execution_authorized"] is False


def test_deep_verifier_rejects_projection_tamper(tmp_path):
    raw, _policy, admissions, readiness, structure, facts, projection, summary, stages = build()
    runtime = {
        "schema_version": "quanthus.structure-f9-runtime.v1",
        "provider_builds": 1,
        "cross_job_cache_hits": 0,
        "provider_lifecycle": "job_local_TMP",
        "stages": stages,
    }
    artifact = tmp_path / "f9"
    write_result(
        artifact,
        raw,
        admissions,
        readiness,
        structure,
        facts,
        projection,
        summary,
        runtime,
    )
    projection_path = artifact / "projection.json"
    data = json.loads(projection_path.read_text(encoding="utf-8"))
    data[0]["stage_11_active"] = not data[0]["stage_11_active"]
    projection_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="file hash"):
        verify_structure_f9_artifact(
            artifact, code_revision="a" * 40, request_digest=digest(raw)
        )

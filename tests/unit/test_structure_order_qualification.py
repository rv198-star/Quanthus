from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from quanthus import structure_order_artifact as artifact_module
from quanthus.structure_f9_artifact import verify_structure_f9_artifact
from quanthus.structure_f9_run import write_result
from quanthus.structure_order_artifact import (
    build_structure_order_qualification,
    load_structure_order_contract,
    load_structure_order_qualification,
    verify_structure_order_artifact,
)
from quanthus.structure_order_qualification import write_structure_order_qualification
from quanthus.structure_v2_inputs import digest

from .test_structure_f9_run import build as build_f9

ROOT = Path(__file__).resolve().parents[2]
CODE = "a" * 40
RUN_A = "run_" + "3" * 32
RUN_B = "run_" + "4" * 32


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
    verified = verify_structure_f9_artifact(root, code_revision=CODE, request_digest=digest(raw))
    source_bytes = (root / "source-inputs.jsonl").read_bytes()
    return raw, verified, source_bytes


def _request(tmp_path: Path):
    event_root = tmp_path / "event-runs"
    source_root = event_root / "structure-runs"
    source_root.mkdir(parents=True)
    raw, verified_a, source_a = _publish_source(source_root / RUN_A)
    _raw_b, verified_b, source_b = _publish_source(source_root / RUN_B)
    assert source_a == source_b
    request = json.loads(
        (ROOT / "config/structure-order-qualification.v1.json").read_text(encoding="utf-8")
    )
    request["source_f9"] = {
        "code_revision": CODE,
        "request_digest": digest(raw),
        "source_inputs_digest": "sha256:" + hashlib.sha256(source_a).hexdigest(),
        "run_ids": [RUN_A, RUN_B],
        "manifest_sha256": [verified_a["manifest_sha256"], verified_b["manifest_sha256"]],
    }
    request["scope"].update(
        {
            "report_start": raw["report_start"],
            "report_end": raw["report_end"],
            "security_codes": raw["security_codes"],
        }
    )
    return event_root, request


def test_structure_order_contract_is_threshold_free_and_reuses_efficiency(tmp_path):
    contract = load_structure_order_contract(ROOT / "config/structure-order-facts.v1.json")
    assert contract["display_name_zh"] == "结构秩序事实族"
    assert contract["display_name_en"] == "Structure Order Fact Family"
    assert contract["semantics"]["directional_efficiency_is_reused_not_redefined"] is True
    assert contract["semantics"]["order_or_disorder_label_forbidden"] is True
    assert contract["authority"]["stage_projection_authorized"] is False
    changed = json.loads(json.dumps(contract))
    changed["authority"]["stage_projection_authorized"] = True
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="authority"):
        load_structure_order_contract(path)


def test_fixed_structure_order_request_rejects_market_data_authority(tmp_path):
    raw = json.loads(
        (ROOT / "config/structure-order-qualification.v1.json").read_text(encoding="utf-8")
    )
    path = tmp_path / "request.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert (
        load_structure_order_qualification(path)["authority"]["market_data_read_authorized"]
        is False
    )
    raw["authority"]["market_data_read_authorized"] = True
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="authority"):
        load_structure_order_qualification(path)


def test_structure_order_qualification_reuses_two_accepted_sources_without_market_data(
    tmp_path, monkeypatch
):
    event_root, request = _request(tmp_path)
    project_root = tmp_path / "project"
    (project_root / "config").mkdir(parents=True)
    (project_root / "config/structure-order-facts.v1.json").write_bytes(
        (ROOT / "config/structure-order-facts.v1.json").read_bytes()
    )
    monkeypatch.setattr(artifact_module, "find_project_root", lambda: project_root)
    payloads, summary = build_structure_order_qualification(request, event_root=event_root)
    assert payloads["structure-order-a.json"] == payloads["structure-order-b.json"]
    assert summary["decision"] == "PASS_BOUNDED_STRUCTURE_ORDER_FACT_FAMILY"
    assert summary["market_data_reads"] == summary["dwd_provider_opens"] == 0
    assert summary["source_run_count"] == 2
    counts = summary["source_results"][0]["counts"]
    assert counts["available_observation_count"] > 0
    assert counts["measurement_count"] == counts["available_observation_count"] * 7


def test_structure_order_deep_verifier_reconstructs_from_immutable_sources(
    tmp_path, monkeypatch
):
    event_root, request = _request(tmp_path)
    project_root = tmp_path / "project"
    (project_root / "config").mkdir(parents=True)
    (project_root / "config/structure-order-facts.v1.json").write_bytes(
        (ROOT / "config/structure-order-facts.v1.json").read_bytes()
    )
    (project_root / "config/structure-order-qualification.v1.json").write_text(
        json.dumps(request), encoding="utf-8"
    )
    monkeypatch.setattr(artifact_module, "find_project_root", lambda: project_root)
    monkeypatch.setattr(
        artifact_module,
        "load_structure_order_qualification",
        lambda _path: request,
    )
    monkeypatch.setenv("QUANTHUS_EVENT_RUN_ROOT", str(event_root))
    output = tmp_path / "order-artifact"
    write_structure_order_qualification(output, request, code_revision=CODE)
    verified = verify_structure_order_artifact(
        output,
        code_revision=CODE,
        request_digest=digest(request),
    )
    assert verified["source_run_count"] == 2
    assert verified["market_data_reads"] == verified["dwd_provider_opens"] == 0
    assert verified["stage_projection_authorized"] is False
    assert verified["official_label_card_authorized"] is False
    assert verified["execution_authorized"] is False

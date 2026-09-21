"""Synthetic boundaries for P3-STRAT-003-B-R1 industry inspection."""
from __future__ import annotations

import hashlib, json
from pathlib import Path
from types import SimpleNamespace

import pytest

from quanthus.expert_evaluation.serialization import ExpertEvaluationError, digest_value
from quanthus.expert_strategy import p3_strat_003_industry_inspection as inspection
from quanthus.platform.event_worker import _structure_input, _structure_request_digest

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config/research/p3-strat-003/industry-inspection.v1.json"


def test_frozen_industry_inspection_config_is_admitted() -> None:
    raw=json.loads(CONFIG.read_text())
    assert digest_value(raw)==inspection.REQUEST_DIGEST
    job={"code_revision":"a"*40,"run_config":str(CONFIG.relative_to(ROOT))}
    assert _structure_input(job)==job
    assert _structure_request_digest(job["run_config"])==inspection.REQUEST_DIGEST
    with pytest.raises(RuntimeError,match="run_config_not_governed"):
        _structure_input({**job,"run_config":"config/research/p3-strat-003/not-governed.json"})


def test_coverage_never_converts_failed_event_occurrences_to_security_count() -> None:
    ids=[
        {"security_id":"sec-a","company_id":"co-a","listing_code":"600001","venue_id":"SSE","first":"2024-10-01","last":"2026-09-18","decision":True},
        {"security_id":"sec-b","company_id":"co-b","listing_code":"000001","venue_id":"SZSE","first":"2024-10-01","last":"2026-09-18","decision":True},
        {"security_id":"sec-c","company_id":"co-c","listing_code":"600003","venue_id":"SSE","first":"2024-10-01","last":"2024-12-31","decision":False},
    ]
    observed=inspection._coverage(
        ids,
        old={"600001":"legacy"},
        dwd={"sec-a":"industry-1"},
        reasons={"sec-a":"covered","sec-b":"no_company_industry_membership","sec-c":"no_company_industry_membership"},
        p4={"sec-b":("industry-2","000001","SZSE")},
        by_code={("000001","SZSE"):["sec-b"],("600003","SSE"):["older-sec-c"]},
    )
    assert observed["all_security_status_counts"]=={"dwd_native":1,"no_company_industry_membership":1,"p4_sealed_reference":1}
    assert observed["decision_security_status_counts"]=={"dwd_native":1,"p4_sealed_reference":1}
    assert observed["remaining_unresolved_security_count"]==1
    assert observed["code_venue_only_reference_match_count"]==1
    assert "candidate_event_count" not in observed


@pytest.fixture
def missing_candidate(tmp_path:Path,monkeypatch:pytest.MonkeyPatch)->SimpleNamespace:
    project=tmp_path/"project"; event=tmp_path/"event-runs"
    request=project/CONFIG.relative_to(ROOT); request.parent.mkdir(parents=True)
    raw=json.loads(CONFIG.read_text()); qualification=json.loads((ROOT/raw["qualification"]["path"]).read_text())
    qp=project/raw["qualification"]["path"]; qp.parent.mkdir(parents=True,exist_ok=True)
    qdata=json.dumps(qualification,sort_keys=True).encode(); qp.write_bytes(qdata)
    raw["qualification"]["file_sha256"]="sha256:"+hashlib.sha256(qdata).hexdigest(); request.write_text(json.dumps(raw))
    monkeypatch.setattr(inspection,"REQUEST_DIGEST",digest_value(raw))
    monkeypatch.setattr(inspection,"find_project_root",lambda _p:project)
    monkeypatch.setattr(inspection,"_request",lambda _p:qualification)
    monkeypatch.setattr(inspection,"_run_root",lambda:event)
    base=tmp_path/"base"; base.mkdir()
    monkeypatch.setattr(inspection,"load_prospective_runtime_config",lambda *_a,**_k:SimpleNamespace(historical_base_root=base,static_industry_mapping=tmp_path/"legacy.json",static_industry_mapping_sha256="sha256:"+"0"*64))
    return SimpleNamespace(request=request,output=event/".staging/job_new/1/p3-strat-003-industry-inspection")


def test_missing_failed_projection_seals_unavailable_without_touching_industry_assets(missing_candidate:SimpleNamespace,monkeypatch:pytest.MonkeyPatch)->None:
    def forbidden(*_a:object,**_k:object)->None: pytest.fail("industry source should not be opened")
    monkeypatch.setattr(inspection,"_p4",forbidden); monkeypatch.setattr(inspection,"_dwd",forbidden); monkeypatch.setattr(inspection,"_old",forbidden)
    result=inspection.build_industry_inspection(request_path=missing_candidate.request,output=missing_candidate.output,code_revision="c"*40)
    assert result["disposition"]==inspection.UNAVAILABLE and result["b_acceptance"] is False
    manifest=json.loads((missing_candidate.output/"manifest.json").read_text())
    assert manifest["inspection"]["candidate_projection"]["status"]=="missing_or_unreadable"
    assert manifest["inspection"]["strategy_or_economic_results_read"] is False
    assert manifest["inspection"]["target_outcome_labels_read"] is False


def test_sealed_industry_inspection_detects_summary_tamper(missing_candidate:SimpleNamespace)->None:
    result=inspection.build_industry_inspection(request_path=missing_candidate.request,output=missing_candidate.output,code_revision="c"*40)
    assert inspection.verify_industry_inspection_artifact(missing_candidate.output,code_revision="c"*40,request_digest=inspection.REQUEST_DIGEST)==result
    (missing_candidate.output/"summary.json").write_text("{}\n")
    with pytest.raises(ExpertEvaluationError,match="artifact boundary"):
        inspection.verify_industry_inspection_artifact(missing_candidate.output,code_revision="c"*40,request_digest=inspection.REQUEST_DIGEST)

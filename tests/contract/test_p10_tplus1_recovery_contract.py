"""Regression for a genuine canary revision expanding to R2/R3 cohorts."""

import json
import sys
from datetime import date

from quanthus.pipelines import p10_minute_daily_cycle as cycle
from quanthus.pipelines.p10_historical_tplus1_recovery import (
    _run_operation,
)


def test_maturity_revision_recovers_all_imperfect_cohort_not_only_canaries(
    monkeypatch,tmp_path,
):
    target=date(2026,10,9)
    canary=["000001"]
    grades={}
    for i in range(111):
        code=f"{i:06d}"
        grades[code]={"quality_grade":"R3" if i<105 else "R0"}
    grades["000099"]={"quality_grade":"R2"}
    seen=[]
    def fake_run(**kwargs):
        seen.append(kwargs)
        return {"status":"success"}
    monkeypatch.setattr(cycle,"_run",fake_run)
    record=cycle._capture_revision_cohort(
        target=target,grades=grades,canaries=canary,
        stage=tmp_path/"stage",verified=tmp_path/"release",
        journal=tmp_path/"journal",calendar_file=tmp_path/"calendar",
    )
    selected=json.loads((tmp_path/"stage"/"selected-revisions.json").read_text())
    assert len(selected)==104  # 105 R3 + one R2 (already R3) - one canary
    assert "000001" not in selected
    assert all(code not in selected for code in [f"{i:06d}" for i in range(105,111)])
    assert record["shards_attempted"]==3
    assert [x["operation_id"] for x in seen]==[
        "revision-000","revision-001","revision-002"
    ]


def test_historical_recovery_never_reopens_spent_budget(tmp_path):
    state={
        "cumulative_processing_seconds":3600,
        "operations":[],"inflight_operation":None,
    }
    marker=tmp_path/"must-not-run"
    receipt=_run_operation(
        state_path=tmp_path/"state.json",state=state,
        operation_id="blocked-after-60m",
        command=[sys.executable,"-c",f"open({str(marker)!r},'w').close()"],
    )
    assert receipt["outcome"]=="cutoff_reached"
    assert not marker.exists()
    assert state["cumulative_processing_seconds"]==3600

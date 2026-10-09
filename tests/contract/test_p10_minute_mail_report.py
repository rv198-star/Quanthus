"""Minute degradation notification must say what really happened."""

import hashlib
import json
from datetime import date

from quanthus.pipelines.p10_deadline_cycle import minute_mail_snapshot
from quanthus.warehouse_notifications import _email_html


def test_minute_email_contains_actual_grade_and_distinct_ready(tmp_path):
    target=date(2026,10,9)
    root=tmp_path/"minute"
    package=root/"packages"/str(target)/"version1"
    package.mkdir(parents=True)
    quality=package/"quality-status.jsonl"
    quality.write_text(
        '{"stock_code":"000001","quality_grade":"R3","quality_reason":"minute_daily_volume_mismatch"}\n'
        '{"stock_code":"000006","quality_grade":"R0","quality_reason":"complete"}\n'
    )
    manifest=package/"manifest.json"
    manifest.write_text('{"kind":"synthetic-verifiable-email-test"}')
    sha="sha256:"+hashlib.sha256(manifest.read_bytes()).hexdigest()
    (package/"SEALED").write_text(sha+"\n")
    status=root/"date-status"/str(target)
    status.mkdir(parents=True)
    (status/"version1.json").write_text(json.dumps({
        "market_date":str(target),"manifest_sha256":sha,
        "quality_grades":{"R0":1,"R1":0,"R2":0,"R3":1,"SUSPENDED":0},
        "research_ready":False,
    }))
    journal=tmp_path/"ops"
    journal.mkdir()
    (journal/f"{target}-T.json").write_text(json.dumps({
        "operations":[{"operation_id":"capture-000","outcome":"success"},
                      {"operation_id":"capture-001","outcome":"failed"}],
        "terminal_disposition":"source_grade_warehouse_published",
    }))
    detail=minute_mail_snapshot(
        target=target,minute_root=root,phase_root=journal,phase="minute_T",
    )
    assert detail["collection_status"]=="任务执行：1成功/1失败"
    assert detail["research_qualification"]=="NOT READY"
    assert detail["quality_distribution"]=="R0=1 / R1=0 / R2=0 / R3=1 / SUSPENDED=0"
    html=_email_html(
        event="p10_cn01_minute_quality_degraded",market_date=target,
        pointer=None,result=None,error="source_native_quality_not_READY",
        context={"node":"CN01","stage":"minute_T",**detail},
    )
    assert "NOT READY" in html
    assert "R3=1" in html
    assert "minute_daily_volume_mismatch: 1" in html
    assert "任务执行：1成功/1失败" in html


def test_missing_sealed_source_must_not_claim_ready(tmp_path):
    result=minute_mail_snapshot(
        target=date(2026,10,9),minute_root=tmp_path/"absent",
        phase_root=tmp_path/"absent",phase="minute_Tplus1",
    )
    assert "未发现经过验证" in result["research_qualification"]
    assert "quality_distribution" not in result

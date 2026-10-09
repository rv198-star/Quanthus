"""P10 error-mail gate: source quality warning != transport failure."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from quanthus.pipelines.p10_deadline_notifications import evaluate

DAY = date(2026, 10, 8)
CST = ZoneInfo("Asia/Shanghai")
CAL = [
    {"trade_date": "2026-10-08", "is_open": "1"},
    {"trade_date": "2026-10-09", "is_open": "1"},
]


def _daily(root: Path, data: str = "CN01") -> str:
    package = root / "packages" / DAY.isoformat() / "id"
    package.mkdir(parents=True, exist_ok=True)
    manifest = package / "manifest.json"
    manifest.write_text(data)
    digest = "sha256:" + hashlib.sha256(manifest.read_bytes()).hexdigest()
    (root / "CURRENT_DAILY_CORE.json").write_text(json.dumps({
        "kind": "cn01_daily_core_dwd_current",
        "market_date": DAY.isoformat(),
        "manifest_path": f"packages/{DAY.isoformat()}/id/manifest.json",
        "manifest_sha256": digest,
    }))
    return digest


def _run(tmp_path: Path, kind: str, *, node: str = "CN01",
         at: datetime, cn01_sha: str | None = None) -> dict:
    return evaluate(
        node=node, phase=kind, target=DAY, calendar=CAL,
        daily_root=tmp_path / "daily",
        minute_root=tmp_path / "minute",
        phase_root=tmp_path / "ops",
        at=at, cn01_sha=cn01_sha,
    )


def _minute(tmp_path: Path, *, research_ready: bool) -> None:
    d = tmp_path / "minute" / "date-status"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{DAY.isoformat()}.json").write_text(json.dumps({
        "market_date": DAY.isoformat(),
        "research_ready": research_ready,
    }))


def test_no_false_failure_mail_when_core_is_verified(tmp_path: Path) -> None:
    _daily(tmp_path / "daily")
    result = _run(
        tmp_path, "daily_core",
        at=datetime(2026, 10, 8, 23, 45, tzinfo=CST),
    )
    assert result["status"] == "success"
    assert result["mail_event"] is None


def test_missing_required_core_triggers_one_email_event_after_checkpoint(
    tmp_path: Path,
) -> None:
    before = _run(
        tmp_path, "daily_core",
        at=datetime(2026, 10, 8, 23, 44, tzinfo=CST),
    )
    after = _run(
        tmp_path, "daily_core",
        at=datetime(2026, 10, 8, 23, 45, tzinfo=CST),
    )
    assert before["status"] == "not_due"
    assert after["status"] == "failure"
    assert after["mail_event"] == "p10_cn01_daily_core_failed"


def test_minute_r3_quality_warning_not_transport_crash(tmp_path: Path) -> None:
    _minute(tmp_path, research_ready=False)
    status = _run(
        tmp_path, "minute_T",
        at=datetime(2026, 10, 8, 21, 50, tzinfo=CST),
    )
    assert status["status"] == "quality_warning"
    assert status["mail_event"] == "p10_cn01_minute_quality_degraded"


def test_tplus1_probe_without_terminal_quality_disposition_is_failure(
    tmp_path: Path,
) -> None:
    _minute(tmp_path, research_ready=False)
    d = tmp_path / "ops"
    d.mkdir()
    (d / "2026-10-08-T_plus_1.json").write_text(json.dumps({
        "operations": [{"outcome": "success"}],
        "inflight_operation": None,
    }))
    record = _run(
        tmp_path, "minute_Tplus1",
        at=datetime(2026, 10, 9, 11, 50, tzinfo=CST),
    )
    assert record["status"] == "failure"
    assert record["mail_event"] == "p10_cn01_minute_tplus1_failed"


def test_tplus1_unchanged_source_does_not_hide_r3_quality_warning(
    tmp_path: Path,
) -> None:
    _minute(tmp_path, research_ready=False)
    d = tmp_path / "ops"
    d.mkdir()
    (d / "2026-10-08-T_plus_1.json").write_text(json.dumps({
        "terminal_disposition": "source_unchanged_stopped",
        "inflight_operation": None,
    }))
    record = _run(
        tmp_path, "minute_Tplus1",
        at=datetime(2026, 10, 9, 11, 50, tzinfo=CST),
    )
    assert record["status"] == "quality_warning"
    assert record["mail_event"] == "p10_cn01_minute_quality_degraded"


def test_tplus1_unchanged_source_is_silent_only_for_ready_product(
    tmp_path: Path,
) -> None:
    _minute(tmp_path, research_ready=True)
    ops=tmp_path/"ops"
    ops.mkdir()
    (ops/"2026-10-08-T_plus_1.json").write_text(json.dumps({
        "terminal_disposition":"source_unchanged_stopped",
        "inflight_operation":None,
    }))
    record=_run(
        tmp_path,"minute_Tplus1",
        at=datetime(2026,10,9,11,50,tzinfo=CST),
    )
    assert record["status"]=="success"
    assert record["mail_event"] is None


def test_tplus1_active_bounded_operation_deferred(tmp_path: Path) -> None:
    d = tmp_path / "ops"
    d.mkdir()
    (d / "2026-10-08-T_plus_1.json").write_text(json.dumps({
        "inflight_operation": {
            "id": "bounded",
            "started_at": "2026-10-09T11:45:00+08:00",
            "timeout_seconds": 600,
        },
    }))
    record = _run(
        tmp_path, "minute_Tplus1",
        at=datetime(2026, 10, 9, 11, 50, tzinfo=CST),
    )
    assert record["status"] == "defer"
    assert record["mail_event"] is None


def test_oci_email_requires_exact_cn01_manifest(tmp_path: Path) -> None:
    peer = _daily(tmp_path / "daily", data="OCI copy")
    now = datetime(2026, 10, 9, 0, 35, tzinfo=CST)
    good = _run(tmp_path, "oci_mirror", node="OCI", at=now, cn01_sha=peer)
    assert good["status"] == "success"
    fail = _run(tmp_path, "oci_mirror", node="OCI", at=now, cn01_sha="sha256:" + "0" * 64)
    assert fail["status"] == "failure"
    assert fail["mail_event"] == "p10_oci_daily_core_mirror_failed"


def test_trading_day_status_is_required_no_undated_emails(tmp_path: Path) -> None:
    import pytest
    with pytest.raises(ValueError, match="calendar"):
        evaluate(
            node="CN01", phase="minute_T", target=DAY,
            calendar=[{"trade_date": "2026-10-09", "is_open": "1"}],
            daily_root=tmp_path / "daily", minute_root=tmp_path / "minute",
            phase_root=tmp_path / "ops",
            at=datetime(2026, 10, 8, 21, 50, tzinfo=CST),
        )


def test_crashed_stale_inflight_cannot_suppress_email_forever(tmp_path: Path) -> None:
    d = tmp_path / "ops"
    d.mkdir()
    (d / "2026-10-08-T_plus_1.json").write_text(json.dumps({
        "inflight_operation": {
            "id": "dead",
            "started_at": "2026-10-09T10:30:00+08:00",
            "timeout_seconds": 600,
        },
    }))
    result = _run(
        tmp_path, "minute_Tplus1",
        at=datetime(2026, 10, 9, 11, 50, tzinfo=CST),
    )
    assert result["status"] == "failure"
    assert result["mail_event"] == "p10_cn01_minute_tplus1_failed"

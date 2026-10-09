"""No-op calendar and alert schedule gates; no email/network actions."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from quanthus.pipelines.p10_operating_schedule import (
    CONFIG_PATH,
    alert_checkpoint,
    classify_alert,
    next_trade_day,
    start_at,
)

CST = ZoneInfo("Asia/Shanghai")


def _golden_week() -> list[dict[str, str]]:
    origin = date(2026, 9, 30)
    end = date(2026, 10, 9)
    current = origin
    out: list[dict[str, str]] = []
    while current <= end:
        open_day = current in {date(2026, 9, 30), date(2026, 10, 8), end}
        out.append(
            {"trade_date": current.isoformat(), "is_open": "1" if open_day else "0"}
        )
        current += timedelta(days=1)
    return out


def test_calendar_week_holiday_skips_to_first_verified_open_day() -> None:
    assert next_trade_day(date(2026, 9, 30), _golden_week()) == date(2026, 10, 8)
    assert next_trade_day(date(2026, 10, 8), _golden_week()) == date(2026, 10, 9)


def test_missing_calendar_evidence_must_not_guess_weekday() -> None:
    records = [r for r in _golden_week() if r["trade_date"] != "2026-10-03"]
    with pytest.raises(ValueError, match="intermediate"):
        next_trade_day(date(2026, 9, 30), records)


def test_reject_nontrading_day_and_duplicate_market_calendar() -> None:
    with pytest.raises(ValueError, match="verified exchange trading"):
        start_at(date(2026, 10, 1), "minute_T", _golden_week())
    with pytest.raises(ValueError, match="duplicate"):
        next_trade_day(date(2026, 9, 30), [*_golden_week(), _golden_week()[0]])


def test_expected_start_and_email_checkpoints_in_china_time() -> None:
    calendar = _golden_week()
    date0 = date(2026, 10, 8)
    assert start_at(date0, "daily_core", calendar) == datetime(
        2026, 10, 8, 18, 30, tzinfo=CST
    )
    assert start_at(date0, "minute_T", calendar) == datetime(
        2026, 10, 8, 20, 30, tzinfo=CST
    )
    assert start_at(date0, "minute_Tplus1", calendar) == datetime(
        2026, 10, 9, 10, 30, tzinfo=CST
    )
    assert alert_checkpoint(date0, "daily_core", calendar) == datetime(
        2026, 10, 8, 23, 45, tzinfo=CST
    )
    assert alert_checkpoint(date0, "minute_T", calendar) == datetime(
        2026, 10, 8, 21, 50, tzinfo=CST
    )
    assert alert_checkpoint(date0, "minute_Tplus1", calendar) == datetime(
        2026, 10, 9, 11, 50, tzinfo=CST
    )
    assert alert_checkpoint(date0, "oci_mirror", calendar) == datetime(
        2026, 10, 9, 0, 35, tzinfo=CST
    )


def test_calendar_day_tplus1_timer_units_include_saturday() -> None:
    root = CONFIG_PATH.resolve().parents[2]
    timer=(root / "ops/systemd/quanthus-p10-minute-Tplus1.timer").read_text()
    alert=(root / "ops/systemd/quanthus-p10-alert-cn01-minute-Tplus1.timer").read_text()
    assert "OnCalendar=*-*-* 10:30:00 Asia/Shanghai" in timer
    assert "OnCalendar=*-*-* 11:50:00 Asia/Shanghai" in alert
    assert "OnCalendar=*-*-* 12:10:00 Asia/Shanghai" in alert
    assert "Mon..Fri" not in timer+alert


def test_friday_minute_maturity_executes_on_saturday() -> None:
    calendar = _golden_week()
    target=date(2026,10,9)
    assert start_at(target,"minute_Tplus1",calendar)==datetime(
        2026,10,10,10,30,tzinfo=CST
    )
    assert alert_checkpoint(target,"minute_Tplus1",calendar)==datetime(
        2026,10,10,11,50,tzinfo=CST
    )


def test_r2_and_source_unchanged_do_not_trigger_failure_emails() -> None:
    due = datetime(2026, 10, 9, 11, 50, tzinfo=CST)
    for status in (
        "SOURCE_UNCHANGED_STOPPED", "R2_RESTRICTED_RECORDED",
        "CUTOFF_WITH_HONEST_QUALITY_RECEIPT",
    ):
        assert classify_alert(
            kind="minute_Tplus1",
            checked_at=due,
            checkpoint=due,
            receipt_status=status,
        ) == "success"


def test_r3_quality_not_ready_one_warning_not_transmission_crash() -> None:
    due = datetime(2026, 10, 8, 21, 50, tzinfo=CST)
    assert classify_alert(
        kind="minute_T", checked_at=due, checkpoint=due,
        receipt_status="PARTIAL_COVERAGE_NO_RESEARCH_READY",
    ) == "quality_warning"


def test_not_yet_due_and_live_bounded_tail_are_not_false_failures() -> None:
    due = datetime(2026, 10, 8, 21, 50, tzinfo=CST)
    assert classify_alert(
        kind="minute_T", checked_at=due - timedelta(seconds=1),
        checkpoint=due, receipt_status=None,
    ) == "not_due"
    assert classify_alert(
        kind="minute_T", checked_at=due, checkpoint=due,
        receipt_status=None, processing_inflight=True,
    ) == "defer"


def test_daily_core_and_oci_mirror_fail_only_on_required_data() -> None:
    due = datetime(2026, 10, 9, 0, 35, tzinfo=CST)
    assert classify_alert(
        kind="daily_core", checked_at=due, checkpoint=due,
        receipt_status="READY_VERIFIED",
    ) == "success"
    assert classify_alert(
        kind="daily_core", checked_at=due, checkpoint=due, receipt_status=None,
    ) == "failure"
    assert classify_alert(
        kind="oci_mirror", checked_at=due, checkpoint=due,
        receipt_status=None, exact_cn01_mirror=True,
    ) == "success"
    assert classify_alert(
        kind="oci_mirror", checked_at=due, checkpoint=due,
        receipt_status=None, exact_cn01_mirror=False,
    ) == "failure"


def test_policy_keeps_cn02_isolated_until_real_inventory_and_acceptance() -> None:
    p = json.loads(CONFIG_PATH.read_text())
    assert p["node_roles"]["CN02"]["production_mirror_assumed"] is False
    assert p["node_roles"]["CN02"]["mirror_write_forbidden_without_separate_contract"]
    assert p["node_roles"]["OCI"]["minute_mirror_enabled"] is False
    assert p["email"]["no_credentials_in_config"] is True
    assert p["schedule"]["minute_T"]["new_operation_cutoff_cumulative_seconds"] == 3600
    assert p["schedule"]["minute_Tplus1"]["new_operation_cutoff_cumulative_seconds"] == 3600

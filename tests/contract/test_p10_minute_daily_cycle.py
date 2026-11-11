"""P10 T/T+1 supervisor calendar, canary and immutable-array tests."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from quanthus.pipelines.p10_minute_daily_cycle import (
    _atomic_array,
    _balanced_canaries,
    resolve_target,
)

CST = ZoneInfo("Asia/Shanghai")


def _days(start: date, end: date, open_dates: set[date]) -> list[dict]:
    out=[]
    day=start
    while day<=end:
        out.append({
            "trade_date":day.isoformat(),
            "is_open":"1" if day in open_dates else "0",
        })
        day+=timedelta(days=1)
    return out


def test_Tplus1_requires_yesterday_market_T_not_next_open_session() -> None:
    rows=_days(
        date(2026,9,30), date(2026,10,8),
        {date(2026,9,30),date(2026,10,8)},
    )
    assert resolve_target(datetime(2026,10,8,10,31,tzinfo=CST),"T_plus_1",rows) is None
    assert resolve_target(datetime(2026,10,1,10,31,tzinfo=CST),"T_plus_1",rows)==date(2026,9,30)
    assert resolve_target(datetime(2026,10,8,20,31,tzinfo=CST),"T",rows)==date(2026,10,8)


def test_current_next_trade_session_10_09_replays_october_8() -> None:
    rows=_days(
        date(2026,10,8), date(2026,10,9),
        {date(2026,10,8),date(2026,10,9)},
    )
    assert resolve_target(datetime(2026,10,9,10,30,tzinfo=CST),"T_plus_1",rows)==date(2026,10,8)


def test_non_trading_day_skips_without_unqualified_source_use() -> None:
    rows=_days(
        date(2026,10,8), date(2026,10,11),
        {date(2026,10,8),date(2026,10,9)},
    )
    assert resolve_target(datetime(2026,10,10,20,30,tzinfo=CST),"T",rows) is None
    assert resolve_target(datetime(2026,10,10,10,30,tzinfo=CST),"T_plus_1",rows)==date(2026,10,9)
    assert resolve_target(datetime(2026,10,11,10,30,tzinfo=CST),"T_plus_1",rows) is None


def test_missing_calendar_holiday_row_not_guessed() -> None:
    rows=_days(
        date(2026,9,30),date(2026,10,8),
        {date(2026,9,30),date(2026,10,8)},
    )
    rows=[r for r in rows if r["trade_date"]!="2026-10-07"]
    with pytest.raises(ValueError,match="missing"):
        resolve_target(datetime(2026,10,8,10,31,tzinfo=CST),"T_plus_1",rows)


def test_reject_unsupported_operation_phase() -> None:
    with pytest.raises(ValueError,match="invalid"):
        resolve_target(
            datetime(2026,10,9,20,30,tzinfo=CST),"T_plus_2",
            _days(date(2026,10,8),date(2026,10,9),{date(2026,10,9)}),
        )


def test_canary_cohort_picks_source_defects_cross_board() -> None:
    codes=["000001","000002","300750","300751","600000","600010","688001","688002"]
    grades={code:{"quality_grade":"R3"} for code in codes}
    selected=_balanced_canaries(codes,grades)
    assert set(codes)<=set(selected)
    assert len(selected)>=12 or len(codes)<12


def test_immutable_calendar_array_never_rewritten(tmp_path: Path) -> None:
    rows=[{"trade_date":"2026-10-09","is_open":"1"}]
    out=tmp_path/"calendar.json"
    _atomic_array(out,rows)
    before=out.read_bytes()
    _atomic_array(out,rows)
    assert out.read_bytes()==before
    with pytest.raises(ValueError,match="changed"):
        _atomic_array(out,[{"trade_date":"2026-10-09","is_open":"0"}])
    assert json.loads(out.read_text())==rows


def test_t_reserves_final_warehouse_operation_before_sixty_minute_cutoff(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from quanthus.pipelines import p10_minute_daily_cycle as cycle

    target = date(2026, 10, 9)
    journal = tmp_path / "journal"
    capture = tmp_path / "capture"
    codes = [f"{i:06}" for i in range(4992)]
    rows = [
        {"trade_date": target.isoformat(), "is_open": "1"},
    ]
    monkeypatch.setattr(cycle, "find_market_ods", lambda releases, day: tmp_path)
    monkeypatch.setattr(
        cycle, "verified_daily_universe",
        lambda path, day: (codes, "sha256:" + "a" * 64),
    )
    operations = []

    def fake_run(**kwargs):
        operations.append(kwargs["operation_id"])
        if kwargs["operation_id"] == "capture-000":
            receipts = capture / str(target) / "T-v1" / "shard-000" / "receipts"
            receipts.mkdir(parents=True)
            (receipts / "000001.json").write_text("{}")
            (journal / f"{target}-T.json").write_text(json.dumps({
                "cumulative_processing_seconds": 3001,
                "operations": [],
                "policy_id": "p10-1m-daily-and-tplus1-bounded-ops-v2",
            }))
        return {"status": "success"}

    monkeypatch.setattr(cycle, "_run", fake_run)
    result = cycle.run_t(
        target=target, rows=rows, journal=journal,
        releases=tmp_path, capture=capture, product=tmp_path / "product",
    )
    assert operations == ["capture-000", "warehouse-build"]
    assert result["status"] == "success"
    ledger = json.loads((journal / f"{target}-T.json").read_text())
    assert ledger["terminal_disposition"] == "source_grade_warehouse_published"

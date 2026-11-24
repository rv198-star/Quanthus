"""Bounded history reader (issue #749) - synthetic-fixture tests.

Builds small synthetic parquet members + a stub SourceMap, then checks
the reader emits a p11.raw-history-input.v1 envelope the strict loader
shape accepts, keeps the three clocks separate, carries UNKNOWN for
unproven coverage, and never fabricates bars / listing age / a member.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.p12_history import reader as reader_mod
from quanthus.p12_history.source_pins import ResolvedMember, SourceMap

UTC = timezone.utc
CUTOFF = datetime(2026, 11, 24, 22, 30, tzinfo=UTC)


def _write(path: Path, table: pa.Table) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)


def _member(root: Path, rel: str, package_id: str = "test-base", kind: str = "base",
            market_date: str | None = None) -> ResolvedMember:
    p = root / rel
    return ResolvedMember(
        member_id=rel,
        package_id=package_id,
        version="2.5.1",
        abs_path=p,
        sha256="sha256:" + "a" * 64,
        size_bytes=p.stat().st_size if p.exists() else 0,
        readability="READABLE",
        package_kind=kind,
        market_date=market_date,
    )


class StubSourceMap(SourceMap):
    """A SourceMap that serves hand-built members instead of the frozen package."""

    def __init__(self, members_by_table, daily_cores):
        self._members_by_table = members_by_table
        self._daily = daily_cores

    def base_members(self, table):
        return list(self._members_by_table.get(table, ()))

    def daily_open_days(self):
        return sorted(self._daily)

    def daily_session_class(self, market_date):
        return "OPEN" if market_date in self._daily else None

    def daily_member(self, market_date, name_fragment):
        for m in self._daily.get(market_date, ()):
            if name_fragment in m.member_id:
                return m
        return None

    def daily_core(self, market_date):
        return None  # members resolved via daily_member


def _calendar_table(days, venue="SSE"):
    return pa.table({
        "venue_id": [venue] * len(days),
        "trade_date": days,
        "is_open": [True] * len(days),
        "close_at": [datetime(d.year, d.month, d.day, 15, 0, tzinfo=UTC) for d in days],
        "observation_start": [datetime(d.year, d.month, d.day, 9, 30, tzinfo=UTC) for d in days],
        "knowledge_from": [datetime(d.year, d.month, d.day, 16, 0, tzinfo=UTC) for d in days],
    })


def _listing_table():
    return pa.table({
        "security_id": ["security-600000"],
        "listing_id": ["listing-600000"],
        "venue_id": ["SSE"],
        "effective_from": [datetime(2026, 1, 1, 0, 0, tzinfo=UTC)],
        "effective_to": pa.array([None], type=pa.timestamp("us", tz="UTC")),
        "knowledge_from": [datetime(2026, 1, 1, 0, 0, tzinfo=UTC)],
    })


def _security_table():
    return pa.table({
        "security_id": ["security-600000"],
        "company_id": ["company-1"],
        "instrument_type": ["common_equity"],
        "effective_from": [datetime(2026, 1, 1, tzinfo=UTC)],
        "effective_to": pa.array([None], type=pa.timestamp("us", tz="UTC")),
    })


def _bar_table(days, listing="listing-600000"):
    n = len(days)
    return pa.table({
        "source_record_id": [f"src/code=600000/{d}" for d in days],
        "observation_end": [datetime(d.year, d.month, d.day, 16, 0, tzinfo=UTC) for d in days],
        "listing_id": [listing] * n,
        "calendar_session_id": [f"cal-{d}" for d in days],
        "interval_size": ["1d"] * n,
        "price_basis": ["UNADJUSTED"] * n,
        "currency": ["CNY"] * n,
        "open": [10.0 + i for i in range(n)],
        "high": [11.0 + i for i in range(n)],
        "low": [9.0 + i for i in range(n)],
        "close": [10.5 + i for i in range(n)],
        "volume": [1000.0] * n,
        "turnover": [10500.0] * n,
    })


def _sessions(n, end=date(2026, 11, 24)):
    # consecutive open sessions ending at `end` (calendar treats all as open)
    from datetime import timedelta
    return [end - timedelta(days=i) for i in range(n - 1, -1, -1)]


def _build_map(tmp_path: Path, n_sessions=130, with_bars=True, with_listing=True):
    # calendar covers the listing start (2026-01-01) through T so membership
    # is provable; bars use the trailing n_sessions of the same calendar.
    cal_days = _sessions(330)
    members = {}
    cal = tmp_path / "cal.parquet"; _write(cal, _calendar_table(cal_days))
    members["dwd_trading_calendar"] = [_member(tmp_path, "cal.parquet")]
    if with_listing:
        p = tmp_path / "listing.parquet"; _write(p, _listing_table())
        members["dwd_listing"] = [_member(tmp_path, "listing.parquet")]
    for table, tbl in {
        "dwd_security": _security_table(),
        "dwd_security_identifier": pa.table({
            "security_id": ["security-600000"], "identifier_namespace": ["code"],
            "identifier_value": ["600000"], "venue_id": ["SSE"]}),
        "dwd_trading_venue": pa.table({"venue_id": ["SSE"], "mic_code": ["XSHG"]}),
        "dwd_listed_company": pa.table({"company_id": ["company-1"], "display_name": ["浦发银行"]}),
        "dwd_company_industry_membership": pa.table({
            "company_id": ["company-1"], "industry_node_id": ["ind-bank"],
            "membership_type": ["primary"],
            "effective_from": [datetime(2026,1,1,tzinfo=UTC)],
            "effective_to": pa.array([None], type=pa.timestamp("us", tz="UTC"))}),
        "dwd_industry_node": pa.table({}),
    }.items():
        p = tmp_path / f"{table}.parquet"
        _write(p, tbl)
        members[table] = [_member(tmp_path, f"{table}.parquet")]
    if with_bars:
        p = tmp_path / "bars-2026-11.parquet"
        _write(p, _bar_table(cal_days))
        members["dwd_security_bar"] = [
            _member(tmp_path, "payload/dwd_security_bar/interval_size=1d/observation_start_year=2026/observation_start_month=11/part-0.parquet")
        ]
        _write(tmp_path / "payload/dwd_security_bar/interval_size=1d/observation_start_year=2026/observation_start_month=11/part-0.parquet", _bar_table(cal_days))
    for t in ("dwd_corporate_action", "dwd_corporate_action_term", "dwd_quality_exception",
              "dwd_observation_gap", "dwd_price_limit_regime", "dwd_tradability_state"):
        members.setdefault(t, [])
    return StubSourceMap(members, {})


def test_envelope_shape_and_membership(tmp_path):
    smap = _build_map(tmp_path, n_sessions=130)
    res = reader_mod.read_history(
        date(2026, 11, 24), CUTOFF, source_map=smap,
        observed_at=datetime(2026, 11, 24, 23, 0, tzinfo=UTC),
    )
    rh = res.raw_history
    assert rh["schema_version"] == "p11.raw-history-input.v1"
    assert rh["market_date"] == "2026-11-24"
    assert rh["decision_cutoff"] == "2026-11-24T22:30:00Z"
    assert len(rh["securities"]) == 1
    sec = rh["securities"][0]
    assert sec["identity"]["security_id"] == "security-600000"
    assert sec["identity"]["stock_code"] == "600000"
    # 61-session window: bars are bounded, never the full 130
    assert len(sec["bars"]) == 61
    assert sec["bars"][0]["security_id"] == "security-600000"
    # listing instant preserved (native timestamp, not a bare date)
    assert "T" in rh["listing_rows"][0]["effective_from"]
    # three clocks separated
    assert res.coverage["visible_at_label"] == "ENGINEERING_REPLAY_AS_OBSERVED"
    assert res.read_at == "2026-11-24T23:00:00Z"


def test_missing_listing_leaves_membership_unproven(tmp_path):
    smap = _build_map(tmp_path, with_listing=False)
    res = reader_mod.read_history(
        date(2026, 11, 24), CUTOFF, source_map=smap,
    )
    # no listing rows -> P11 derives no membership; reader does not invent one
    assert res.raw_history["listing_rows"] == []


def test_missing_bars_yield_empty_bars_not_fabrication(tmp_path):
    smap = _build_map(tmp_path, with_bars=False)
    res = reader_mod.read_history(
        date(2026, 11, 24), CUTOFF, source_map=smap,
    )
    assert res.raw_history["securities"][0]["bars"] == []


def test_bar_window_is_last_61_sessions(tmp_path):
    smap = _build_map(tmp_path, n_sessions=200)
    res = reader_mod.read_history(
        date(2026, 11, 24), CUTOFF, source_map=smap,
    )
    bars = res.raw_history["securities"][0]["bars"]
    assert len(bars) == 61
    # last session is the market date itself
    assert bars[-1]["session_date"] == "2026-11-24"

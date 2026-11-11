"""End-to-end synthetic fixture, separate from all real-data attempt budgets."""

from __future__ import annotations

import importlib.util
import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")
ROOT = Path(__file__).resolve().parents[2]


def synthetic_source(tmp_path):
    db = duckdb.connect()
    db.execute("SET TimeZone='Asia/Shanghai'")
    common = "knowledge_from TIMESTAMPTZ,knowledge_to TIMESTAMPTZ"
    db.execute(
        "CREATE TABLE dwd_trading_calendar(calendar_session_id VARCHAR,venue_id VARCHAR,"
        "trade_date DATE,is_open BOOLEAN,open_at TIMESTAMPTZ,close_at TIMESTAMPTZ," + common + ")"
    )
    db.execute(
        "CREATE TABLE dwd_listing(listing_id VARCHAR,security_id VARCHAR,venue_id VARCHAR,"
        "listing_code VARCHAR,listing_currency VARCHAR,lot_size INTEGER,effective_from DATE,"
        "effective_to DATE," + common + ")"
    )
    db.execute(
        "CREATE TABLE dwd_security(security_id VARCHAR,instrument_type VARCHAR,"
        "effective_from DATE,effective_to DATE," + common + ")"
    )
    db.execute(
        "CREATE TABLE dwd_security_bar(listing_id VARCHAR,calendar_session_id VARCHAR,"
        "observation_start TIMESTAMPTZ,open DECIMAL(20,2),close DECIMAL(20,2),turnover DOUBLE,"
        "interval_size VARCHAR,price_basis VARCHAR," + common + ")"
    )
    db.execute(
        "CREATE TABLE dwd_tradability_state(listing_id VARCHAR,calendar_session_id VARCHAR,"
        "observation_start TIMESTAMPTZ,upper_price DECIMAL(20,2),lower_price DECIMAL(20,2),"
        "is_tradable BOOLEAN,can_buy BOOLEAN,can_sell BOOLEAN,reason_code VARCHAR," + common + ")"
    )
    db.execute(
        "CREATE TABLE dwd_corporate_action(corporate_action_id VARCHAR,security_id VARCHAR,"
        "action_type VARCHAR,action_status VARCHAR,effective_at TIMESTAMPTZ,ex_date DATE,"
        "announced_at TIMESTAMPTZ," + common + ")"
    )
    db.execute(
        "CREATE TABLE dwd_corporate_action_term(corporate_action_term_id VARCHAR,"
        "corporate_action_id VARCHAR,effective_at TIMESTAMPTZ,term_type VARCHAR,currency VARCHAR,"
        "amount_per_share DECIMAL(20,8),ratio_numerator DECIMAL(20,8),"
        "ratio_denominator DECIMAL(20,8),option_id VARCHAR," + common + ")"
    )
    db.execute(
        "CREATE TABLE dwd_quality_exception(quality_exception_id VARCHAR,affected_table VARCHAR,"
        "affected_business_key_json VARCHAR,affected_effective_at TIMESTAMPTZ,"
        "resolution_state VARCHAR," + common + ")"
    )
    from quanthus.hf_dwd_research_sql import SSE, SZSE

    known = "2026-10-31T00:00:00+08:00"
    days = []
    day = date(2019, 1, 1)
    while day <= date(2021, 12, 31):
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    db.executemany(
        "INSERT INTO dwd_trading_calendar VALUES(?,?,?,?,?,?,?,NULL)",
        [
            (
                venue + str(d),
                venue,
                d,
                True,
                str(d) + "T09:30:00+08:00",
                str(d) + "T15:00:00+08:00",
                known,
            )
            for venue in (SSE, SZSE)
            for d in days
        ],
    )
    for listing, security, venue, code in [
        ("L1", "S1", SSE, "000001"),
        ("L2", "S2", SZSE, "000002"),
    ]:
        db.execute(
            "INSERT INTO dwd_listing VALUES(?,?,?,?, 'CNY',100,'2010-01-01',NULL,?,NULL)",
            [listing, security, venue, code, known],
        )
        db.execute(
            "INSERT INTO dwd_security VALUES(?,'common_equity','2010-01-01',NULL,?,NULL)",
            [security, known],
        )
        market = [d for d in days if d >= date(2020, 1, 2)]
        bars = []
        states = []
        for i, d in enumerate(market):
            price = Decimal(str(round(10 * (1.003 if listing == "L1" else 1.008) ** i, 2)))
            sid = venue + str(d)
            at = str(d) + "T09:30:00+08:00"
            bars.append((listing, sid, at, price, price, 2e8, known))
            states.append(
                (listing, sid, at, price * Decimal("1.10"), price * Decimal(".90"), known)
            )
        db.executemany(
            "INSERT INTO dwd_security_bar VALUES(?,?,?,?,?,?,'1d','UNADJUSTED',?,NULL)", bars
        )
        db.executemany(
            "INSERT INTO dwd_tradability_state VALUES(?,?,?,?,?,true,true,true,"
            "'TRADABLE_OBSERVED',?,NULL)",
            states,
        )
    db.execute(
        "INSERT INTO dwd_corporate_action VALUES('A','S1','CASH_DIVIDEND','IMPLEMENTED',"
        "'2020-03-04T23:59:59+08:00','2020-03-04',NULL,?,NULL)",
        [known],
    )
    db.execute(
        "INSERT INTO dwd_corporate_action_term VALUES('TERM','A',"
        "'2020-03-04T23:59:59+08:00','CASH_AMOUNT_PER_SHARE','CNY',.1,NULL,NULL,NULL,?,NULL)",
        [known],
    )
    db.execute(
        "INSERT INTO dwd_quality_exception VALUES('Q','dwd_shareholding_change',"
        "'{\"stock_code\":\"999999\"}','2020-03-04T23:59:59+08:00','UNRESOLVED',?,NULL)",
        [known],
    )
    members = []
    for (name,) in db.execute("SHOW TABLES").fetchall():
        relative = "payload/" + name + "/all/part.parquet"
        path = tmp_path / "source/DWD" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        db.execute("COPY " + name + " TO '" + str(path) + "' (FORMAT PARQUET)")
        members.append({"path": relative, "size_bytes": path.stat().st_size})
    db.close()
    return members


def module():
    spec = importlib.util.spec_from_file_location(
        "capital_runner", ROOT / "scripts/research_capital_replay.py"
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_full_native_synthetic_path_uses_P2_and_one_shared_build(tmp_path):
    members = synthetic_source(tmp_path)
    q = json.loads((ROOT / "config/research/research-capital/request.v1.json").read_text())
    module().formal(q, members, {"git": "synthetic-only"}, tmp_path)
    result = json.loads((tmp_path / "result.json").read_text())
    assert all(a["status"] == "completed" for a in result["arms"].values())
    assert (
        result["stages"]["prerequisite_disposition"]
        == "build once per Job, shared by candidate/control"
    )
    for arm in result["arms"].values():
        assert len(arm["result"]["trades"]) > 100
        assert all(t["holding_sessions"] >= 2 for t in arm["result"]["trades"])
        assert arm["p2_step_receipts"]
    assert not result["historical_pit_claim"]


def test_independent_decimal_referee_matches_full_synthetic_P2(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    from research_capital_independent import IndependentFacts, independent_decision, referee

    members = synthetic_source(tmp_path)
    q = json.loads((ROOT / "config/research/research-capital/request.v1.json").read_text())
    module().formal(q, members, {"git": "synthetic-only"}, tmp_path)
    result = json.loads((tmp_path / "result.json").read_text())
    facts = IndependentFacts(tmp_path / "source/DWD", members, result["nominal_choices"], q)
    costs = json.loads((ROOT / q["policy"]["path"]).read_text())
    references = {}
    for name in ("candidate", "control"):
        other = referee(facts, result["nominal_choices"], name, costs)
        references[name] = other
        assert other["status"] == "completed"
        assert other["nominal_attempts"] == result["arms"][name]["nominal_attempts"]
        assert other["p2_step_receipts"] == result["arms"][name]["p2_step_receipts"]
        assert Decimal(other["ending_wealth"]) == Decimal(
            result["arms"][name]["result"]["performance"]["ending_equity"]
        )
    branch = independent_decision(references)
    assert branch["branch"] == result["decision"]["branch"]
    assert Decimal(branch["primary_increment"]) == Decimal(result["decision"]["primary_increment"])
    for name in ("candidate", "control"):
        actual = result["arms"][name]["result"]
        assert references[name]["trades"] == [
            {k: t[k] for k in ("entry_date", "exit_date", "pnl")} for t in actual["trades"]
        ]
        assert {k: Decimal(v) for k, v in references[name]["equity_curve"].items()} == {
            r["market_date"]: Decimal(r["system_equity"]) for r in actual["equity_curve"]
        }
        assert Decimal(branch["maximum_drawdown"][name]) == Decimal(
            actual["performance"]["maximum_drawdown"]
        )
    facts.db.close()

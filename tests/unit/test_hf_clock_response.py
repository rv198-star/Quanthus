"""Synthetic two-clock and common-population counterexamples for the R5 diagnostic."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from quanthus.hf_clock_response import clock_markout
from quanthus.hf_dwd_research import GuardError, validate_request
from quanthus.hf_dwd_research_sql import SSE, SZSE

duckdb = pytest.importorskip("duckdb")
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def clock_request():
    return json.loads((ROOT / "config/research/research-r5/request.v1.json").read_text())


@pytest.fixture
def clocks(clock_request):
    db = duckdb.connect()
    db.execute("""
      CREATE TABLE calendar(calendar_session_id VARCHAR,venue_id VARCHAR,next_session VARCHAR);
      CREATE TABLE panel(listing_id VARCHAR,calendar_session_id VARCHAR,venue_id VARCHAR,
                         security_ok BOOLEAN,op DOUBLE,trade_date DATE);
      CREATE TABLE labelled(trade_date DATE,venue_id VARCHAR,listing_id VARCHAR,
        next_session VARCHAR,universe BOOLEAN,base_selected BOOLEAN,selected BOOLEAN,
        next_open_reason VARCHAR,next_open DOUBLE,next_close DOUBLE);
    """)
    for venue in (SSE, SZSE):
        db.executemany(
            "INSERT INTO calendar VALUES(?,?,?)",
            [
                (venue + "27", venue, venue + "28"),
                (venue + "28", venue, venue + "29"),
                (venue + "31", venue, None),
            ],
        )
        for selected in (True, False):
            listing = venue + ("candidate" if selected else "control")
            db.execute(
                "INSERT INTO labelled VALUES('2021-12-24',?,?,?,true,true,?,'pass',100,?)",
                [venue, listing, venue + "27", selected, 110 if selected else 100],
            )
            db.execute(
                "INSERT INTO panel VALUES(?,?,?,true,?,'2021-12-28')",
                [listing, venue + "28", venue, 99 if selected else 100],
            )
            db.execute(
                "INSERT INTO panel VALUES(?,?,?,true,200,'2021-12-29')",
                [listing, venue + "29", venue],
            )
    yield db, clock_request["analysis"]
    db.close()


def test_two_scheduled_opens_cross_weekend_and_keep_multiplicative_term(clocks):
    db, analysis = clocks
    result = clock_markout(db, analysis)
    assert result["paired_days"] == 1
    assert result["mean_increment"] == pytest.approx(-0.01)
    assert result["components"]["intraday"]["mean_increment"] == pytest.approx(0.1)
    assert result["components"]["overnight"]["mean_increment"] == pytest.approx(-0.1)
    assert result["components"]["interaction"]["mean_increment"] == pytest.approx(-0.01)
    assert result["decomposition"]["max_opportunity_residual"] < 1e-10
    assert abs(result["decomposition"]["mean_residual"]) < 1e-10


@pytest.mark.parametrize(
    "fault,reason",
    [
        (
            "UPDATE panel SET op=NULL WHERE calendar_session_id LIKE '%28'",
            "missing_or_invalid_second_open",
        ),
        (
            "UPDATE panel SET security_ok=false WHERE calendar_session_id LIKE '%28'",
            "missing_second_identity",
        ),
        ("DELETE FROM panel WHERE calendar_session_id LIKE '%28'", "missing_second_identity"),
        ("UPDATE labelled SET next_close=0", "missing_or_invalid_next_close"),
        ("UPDATE labelled SET next_open_reason='cannot_buy'", "cannot_buy"),
        ("UPDATE labelled SET next_open=1e-300,next_close=1e300", "nonfinite_response"),
    ],
)
def test_common_population_does_not_skip_missing_session_or_identity(clocks, fault, reason):
    db, analysis = clocks
    db.execute(fault)
    result = clock_markout(db, analysis)
    assert result["paired_days"] == 0
    assert sum(x["count"] for x in result["label_ledger"] if x["reason"] == reason) == 4
    assert sum(x["count"] for x in result["label_ledger_by_year"]) == 4
    assert all(c["paired_daily"] == [] for c in result["components"].values())


def test_right_censor_keeps_group_year_denominators_without_2022_extension(clocks):
    db, analysis = clocks
    db.execute("""INSERT INTO labelled SELECT DATE '2021-12-30',venue_id,listing_id,
      venue_id||'31',universe,base_selected,selected,'pass',100,120 FROM labelled""")
    result = clock_markout(db, analysis)
    assert result["paired_days"] == 1
    censored = [
        x for x in result["label_ledger_by_year"] if x["reason"] == "right_censored_second_session"
    ]
    assert {x["group"] for x in censored} == {"candidate", "control"}
    assert sum(x["count"] for x in censored) == 4
    assert {x["year"] for x in censored} == {2021}
    assert result["mean_increment"] == pytest.approx(-0.01)


def test_one_missing_opportunity_uses_same_cohort_for_every_component(clocks):
    db, analysis = clocks
    db.execute(
        "UPDATE panel SET op=NULL WHERE listing_id=? AND trade_date='2021-12-28'",
        [SSE + "candidate"],
    )
    result = clock_markout(db, analysis)
    assert result["paired_days"] == 0
    assert result["valid_unpaired_opportunities"] == 3
    assert all(c["paired_daily"] == [] for c in result["components"].values())


def test_request_freezes_primary_boundary_and_censor_audit(clock_request):
    validate_request(clock_request)
    for key, field, value in [
        ("analysis", "primary_response", "overnight"),
        ("analysis", "audit_right_censored_decision", "2022-01-03"),
        ("scope", "decision_end", "2021-12-30"),
    ]:
        wrong = copy.deepcopy(clock_request)
        wrong[key][field] = value
        with pytest.raises(GuardError):
            validate_request(wrong)

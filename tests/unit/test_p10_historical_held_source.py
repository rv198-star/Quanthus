"""Source triage must preserve guarded unknowns and the exact input-key census."""

from datetime import date

import pytest

from quanthus.pipelines.securities_warehouse import p10_historical_held_source as p


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({"active_exception": True}, "EXISTING_EXCEPTION_REMAINS_BLOCKED"),
        ({"action_present": True}, "NORMALIZED_ACTION_RESIDUAL_CONFLICT"),
        ({"missing_reference": True}, "REFERENCE_EVIDENCE_UNAVAILABLE"),
        ({"observed_trading": False}, "NONTRADING_MARK_REFERENCE_CONFLICT"),
        (
            {"reported_change_matches_previous_bar": False},
            "SOURCE_REFERENCE_RESET_WITHOUT_NORMALIZED_ACTION",
        ),
        (
            {
                "reported_change_matches_previous_bar": False,
                "reported_change_within_regime": False,
            },
            "SOURCE_RETURN_REFERENCE_DISAGREEMENT",
        ),
        ({"ipo_window": True}, "IPO_RULE_APPLICABILITY_UNQUALIFIED"),
        ({}, "OBSERVED_PRICE_OUTSIDE_DECLARED_REGIME"),
        *[
            ({key: None}, "REFERENCE_EVIDENCE_UNAVAILABLE")
            for key in (
                "active_exception",
                "action_present",
                "missing_reference",
                "observed_trading",
                "reported_change_matches_previous_bar",
                "reported_change_within_regime",
                "ipo_window",
            )
        ],
    ],
)
def test_classification_is_diagnosis_and_never_repairs_or_accepts(changes, expected):
    facts = dict.fromkeys(
        ("active_exception", "action_present", "missing_reference", "ipo_window"), False
    )
    facts.update(
        observed_trading=True,
        reported_change_matches_previous_bar=True,
        reported_change_within_regime=True,
    )
    facts.update(changes)
    before = facts.copy()
    assert p.classify_facts(facts) == expected
    assert facts == before


def test_exception_precedes_action_and_missing_reference():
    facts = dict.fromkeys(
        (
            "active_exception",
            "action_present",
            "missing_reference",
            "observed_trading",
            "reported_change_matches_previous_bar",
            "reported_change_within_regime",
            "ipo_window",
        ),
        True,
    )
    assert p.classify_facts(facts) == "EXISTING_EXCEPTION_REMAINS_BLOCKED"
    facts["observed_trading"] = 1
    with pytest.raises(ValueError, match="HELD_SOURCE_FACTS_INVALID"):
        p.classify_facts(facts)


@pytest.fixture
def source():
    import duckdb

    with duckdb.connect() as c:
        c.execute("SET TimeZone='UTC'")
        c.execute("""CREATE TABLE replay_bars AS SELECT 'S' AS security_id,
          DATE '2014-10-23' AS date,100.0::DOUBLE AS close,0.0::DOUBLE AS pct_change
          UNION ALL SELECT 'S',DATE '2014-10-24',85.0,-5.0""")
        c.execute("""CREATE TABLE replay_limits AS SELECT 'S' AS security_id,
          DATE '2014-10-24' AS execution_date,'1' AS observed_tradestatus,
          10.0 AS lower_limit_pct,10.0 AS upper_limit_pct,90.0 AS lower_limit_price,
          110.0 AS upper_limit_price,'REGIME' AS reason_code,
          'prior_trading_close' AS price_reference_kind,false AS in_ipo_window,
          100.0 AS price_reference""")
        c.execute("""CREATE TABLE replay_unexplained_price_transitions AS
          SELECT 'S' AS security_id,DATE '2014-10-24' AS market_date""")
        c.execute("CREATE TABLE replay_actions_mapped (security_id VARCHAR,effective_date DATE)")
        c.execute("""CREATE TABLE replay_action_exceptions_mapped
          (security_id VARCHAR,blocked_from DATE,blocked_through DATE)""")
        yield c


def test_census_reads_preceding_date_without_emitting_prices_or_inferring_an_action(source):
    rows = p.guarded_source_census(source, [("S", date(2014, 10, 23))])
    assert len(rows) == 1
    row = rows[0]
    assert row["classification"] == "SOURCE_REFERENCE_RESET_WITHOUT_NORMALIZED_ACTION"
    assert row["possible_post_candidate"] is True
    assert row["previous_bar_date"] == date(2014, 10, 23)
    assert row["action_present"] is False
    assert row["below_declared_lower_limit"] is True
    assert len(row["diagnostic_bar_sha256"]) == len(row["limit_row_sha256"]) == 64
    assert not {"close", "previous_close", "pct_change", "consumer_admission"} & row.keys()
    assert not any(t[0].startswith("p10_held_") for t in source.execute("SHOW TABLES").fetchall())
    assert (
        p.guarded_source_census(source, [("S", date(2014, 10, 24))])[0]["possible_post_candidate"]
        is False
    )
    assert source.execute("SELECT count(*) FROM replay_bars").fetchone() == (2,)
    assert source.execute("SELECT count(*) FROM replay_actions_mapped").fetchone() == (0,)


@pytest.mark.parametrize("table", ["replay_bars", "replay_limits"])
def test_missing_joined_row_is_not_a_successfully_complete_census(source, table):
    source.execute(f"DELETE FROM {table}")
    with pytest.raises(ValueError, match="HELD_SOURCE_CENSUS_KEY_CLOSURE"):
        p.guarded_source_census(source, [])


def test_duplicate_join_and_preexisting_tmp_do_not_overwrite_evidence(source):
    source.execute("INSERT INTO replay_limits SELECT * FROM replay_limits")
    with pytest.raises(ValueError, match="HELD_SOURCE_CENSUS_KEY_CLOSURE"):
        p.guarded_source_census(source, [])
    source.execute("CREATE TABLE p10_held_bars AS SELECT 'evidence' AS retained")
    with pytest.raises(ValueError, match="HELD_SOURCE_TMP_EXISTS"):
        p.guarded_source_census(source, [])
    assert source.execute("SELECT * FROM p10_held_bars").fetchall() == [("evidence",)]


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE replay_limits SET observed_tradestatus='unavailable'",
        "UPDATE replay_limits SET lower_limit_pct=NULL",
        "UPDATE replay_bars SET close='NaN'::DOUBLE WHERE date=DATE '2014-10-24'",
        "UPDATE replay_bars SET pct_change=NULL WHERE date=DATE '2014-10-24'",
    ],
)
def test_unknown_or_nonfinite_reference_remains_unavailable(source, sql):
    source.execute(sql)
    rows = p.guarded_source_census(source, [])
    assert rows[0]["classification"] == "REFERENCE_EVIDENCE_UNAVAILABLE"


@pytest.mark.parametrize(
    "candidates",
    [
        [("S", date(2022, 1, 4))],
        [("S", date(2011, 12, 30))],
        [("S", "2014-10-23")],
        [("", date(2014, 10, 23))],
        [("S", date(2014, 10, 23)), ("S", date(2014, 10, 23))],
    ],
)
def test_invalid_population_does_not_touch_source(source, candidates):
    with pytest.raises(ValueError, match="HELD_SOURCE_CANDIDATE_"):
        p.guarded_source_census(source, candidates)
    assert source.execute("SELECT count(*) FROM replay_bars").fetchone() == (2,)

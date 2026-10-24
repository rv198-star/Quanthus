"""Late source is explicit retrospective evidence, never a general PIT waiver."""

from dataclasses import replace
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from quanthus.backtest.contracts import MARKET_TIMEZONE, CorporateAction
from quanthus.backtest.engine import BacktestInputError
from quanthus.expert_strategy import p3_historical_action_successor as p


@pytest.fixture
def bound():
    info = datetime(2026, 10, 24, 16, tzinfo=timezone.utc)
    action = CorporateAction(
        "exact-official-action",
        "synthetic",
        date(2012, 5, 31),
        info,
        Decimal("0.25"),
        Decimal("0.5"),
        "official-source|evidence=sha256:" + "a" * 64,
        "new-source",
    )
    engine = p.SupplementedEngine(None, info, "effective-view", {action.action_id: action})
    run = SimpleNamespace(
        official=False, data_snapshot_id="new-source", code_version=p.h.VERSION + ":test"
    )
    applied = datetime.combine(action.effective_date, time(9), tzinfo=MARKET_TIMEZONE)
    return engine, run, action, applied


def test_exact_late_observation_allowed_only_in_bound_retrospective_engine(bound):
    engine, run, action, applied = bound
    engine._validate_action_clock(run, action, applied)
    assert action.known_at > applied
    assert action.known_at.year == 2026


@pytest.mark.parametrize(
    "mutation",
    [
        "snapshot",
        "cash",
        "ratio",
        "date",
        "security",
        "clock",
        "revision",
        "run",
        "official",
        "applied",
        "code",
    ],
)
def test_same_action_id_cannot_hide_changed_terms_clock_or_production(bound, mutation):
    engine, run, action, applied = bound
    if mutation == "snapshot":
        action = replace(action, snapshot_id="old")
    if mutation == "cash":
        action = replace(action, cash_dividend_per_share=Decimal("0.26"))
    if mutation == "ratio":
        action = replace(action, bonus_share_ratio=Decimal("1.5"))
    if mutation == "date":
        action = replace(action, effective_date=date(2012, 6, 1))
    if mutation == "security":
        action = replace(action, security_code="other")
    if mutation == "clock":
        action = replace(action, known_at=datetime(2012, 5, 30, tzinfo=timezone.utc))
    if mutation == "revision":
        action = replace(action, source_revision="forged")
    if mutation == "run":
        run.data_snapshot_id = "foreign"
    if mutation == "official":
        run.official = True
    if mutation == "applied":
        applied = applied.replace(hour=10)
    if mutation == "code":
        run.code_version = "other"
    with pytest.raises(BacktestInputError, match="EXACT_ACTION_BINDING"):
        engine._validate_action_clock(run, action, applied)


def test_unknown_action_still_uses_original_binding_gate(bound):
    engine, run, action, applied = bound
    with pytest.raises(BacktestInputError, match="HISTORY_ACTION_BINDING"):
        engine._validate_action_clock(run, replace(action, action_id="unknown"), applied)


def test_reader_rejects_cutoff_before_real_acquisition_before_opening_any_source(bound):
    engine, _, _, _ = bound
    from quanthus.pipelines.securities_warehouse.p10_historical_action_source import ActionFacts

    fact = ActionFacts(
        "synthetic",
        date(2012, 5, 30),
        date(2012, 5, 31),
        None,
        None,
        None,
        Decimal("0.25"),
        Decimal("0.5"),
        engine.cutoff,
        "revision",
        "a" * 64,
        "b" * 64,
    )
    with pytest.raises(ValueError, match="SOURCE_CLOCK"):
        p.SupplementedReader(object(), (fact,), datetime(2012, 5, 30, tzinfo=timezone.utc))


def test_original_modules_and_requests_remain_unchanged_and_new_request_bound():
    repository = Path(__file__).resolve().parents[2]
    original = p.prior.load_request(repository)
    request = p.load_request(repository)
    assert request["scope"] == original["scope"]
    assert request["arms"] == original["arms"]
    assert request["analysis"] == original["analysis"]
    assert request["optional_rights_policy"] == original["optional_rights_policy"]
    assert request["previous_failures_costs_and_deadlines_retained"]
    assert request["maximum_additional_wall_seconds"] == 7200
    assert not request["historical_PIT_claim"]
    assert not request["source_guards_waived"]


def test_real_streamed_action_boundary_rebuilds_whole_raw_population(tmp_path):
    """A newly evidenced action changes candidates; use the full raw universe."""
    import json
    from datetime import timedelta

    import duckdb
    import pyarrow as pa

    from quanthus.event_library.scan import CandidateScanShard

    days = []
    day = date(2011, 1, 4)
    while len(days) < 320:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    action_day = days[285]
    bars, universe, limits = [], [], []
    for code in ("000001", "000002"):
        for i, day in enumerate(days):
            spread = 0.5 if i == 284 else 2.0 if i == 285 else 1.0
            amount = 50.0 if i == 284 else 200.0 if i == 285 else 100.0
            bars.append(
                dict(
                    security_id=code,
                    date=day,
                    open=100.0,
                    high=100.0 + spread,
                    low=100.0,
                    close=100.0,
                    volume=100.0,
                    amount=amount,
                )
            )
            universe.append(dict(security_id=code, market_date=day))
            limits.append(dict(security_id=code, execution_date=day, observed_tradestatus="1"))
    c = duckdb.connect(config={"threads": 1})
    try:
        for name, rows in (
            ("source_daily_ohlcv_none", bars),
            ("source_daily_universe", universe),
            ("source_price_limit_execution_daily", limits),
        ):
            c.register("input_arrow", pa.Table.from_pylist(rows))
            c.execute(f"CREATE TABLE {name} AS SELECT * FROM input_arrow")
            c.unregister("input_arrow")
        c.execute(
            "CREATE TABLE source_security_code_validity AS SELECT security_id,"
            "security_id AS stock_code,'SSE' AS exchange,DATE '2011-01-04' AS valid_from,"
            "NULL::DATE AS valid_to FROM source_daily_universe GROUP BY security_id"
        )
        c.execute(
            "CREATE TABLE selected_universe_sessions AS SELECT *,row_number() OVER("
            "PARTITION BY security_id ORDER BY market_date) AS listing_sessions "
            "FROM source_daily_universe"
        )
        c.execute(
            "CREATE TABLE source_corporate_action_exceptions_mapped ("
            "security_id VARCHAR,blocked_from DATE,blocked_through DATE)"
        )
        c.execute(
            "CREATE TABLE source_corporate_actions_normalized ("
            "security_id VARCHAR,effective_date DATE)"
        )
        c.execute(
            "CREATE TABLE new_actions AS SELECT '000001' AS security_id,?::DATE AS effective_date",
            [action_day],
        )

        class StreamingOnly:
            def execute(self, *args, **kwargs):
                c.execute(*args, **kwargs)
                return self

            @property
            def description(self):
                return c.description

            def fetchmany(self, count):
                return c.fetchmany(min(count, 17))

            def fetchall(self):
                raise AssertionError("full-history fetchall is forbidden")

        mapping = SimpleNamespace(
            by_security_id={
                code: SimpleNamespace(level1_industry_id="industry-" + code)
                for code in ("000001", "000002")
            }
        )
        repository = Path(__file__).resolve().parents[2]
        args = (StreamingOnly(), mapping, "2026-10-24T16:49:27+00:00", repository)
        before = p._raw_candidates(*args, dates=tuple(days), added=False)
        after = p._raw_candidates(*args, dates=tuple(days), added=True)
        assert {r.security_id for r in before} == {"000001", "000002"}
        assert {r.security_id for r in after} == {"000002"}
        assert before != after
        one = p._scan_raw_candidates(*args, dates=tuple(days), added=True, security_ids=("000001",))
        two = p._scan_raw_candidates(*args, dates=tuple(days), added=True, security_ids=("000002",))
        assert one[0] + two[0] == after
        assert one[4] + two[4] == 640
        assert one[3] + two[3] == 2
        result = p._rebuild_population(*args, dates=tuple(days), output=tmp_path)
        assert {r["security_id"] for r in result["candidates"]} == {"000002"}
        census = CandidateScanShard.from_dict(
            json.loads((tmp_path / "raw-candidate-census.json").read_text())
        )
        assert census.security_ids == ("000001", "000002")
        assert census.decoded_rows == 640 and census.series_count == 2
        assert {r.security_id for r in census.raw_candidates} == {"000002"}
        assert result["raw_candidate_census_sha256"] == p.prior.native._sha(
            tmp_path / "raw-candidate-census.json"
        )
    finally:
        c.close()

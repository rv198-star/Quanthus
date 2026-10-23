import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse import p10_execution_support as p


def market_row():
    return dict(
        listing_id="l",
        security_id="s",
        listing_code="000001",
        market_date=date(2026, 9, 30),
        open=Decimal("10"),
        high=Decimal("10"),
        low=Decimal("10"),
        close=Decimal("10"),
        volume=Decimal("1"),
        amount=Decimal("10"),
        is_tradable=True,
        can_buy=True,
        can_sell=True,
        upper_price=Decimal("11"),
        lower_price=Decimal("9"),
        board="SZ_main",
        listing_count=1,
        security_count=1,
        bar_count=1,
        calendar_count=1,
        venue_count=1,
        state_versions=1,
        bar_known=datetime(2026, 10, 1, tzinfo=timezone.utc),
        state_known=datetime(2026, 10, 1, tzinfo=timezone.utc),
        dated_status=[
            dict(
                value="NORMAL",
                reason="PROVIDER_TRADING",
                known=datetime(2026, 10, 1, tzinfo=timezone.utc),
            )
        ],
        statuses=[
            dict(
                kind="PROVIDER_DAILY_STATE",
                value="NORMAL",
                versions=1,
                known=datetime(2026, 10, 1, tzinfo=timezone.utc),
            )
        ],
        lifecycle=[
            dict(
                kind="LISTING_LIFECYCLE",
                value="LISTED",
                versions=1,
                known=datetime(2026, 10, 1, tzinfo=timezone.utc),
            )
        ],
        regimes=[
            dict(
                id="base",
                priority=10,
                kind="PERCENT_LIMIT",
                status=None,
                ipo=None,
                known=datetime(2026, 10, 1, tzinfo=timezone.utc),
                upper_ratio=Decimal("0.1"),
                lower_ratio=Decimal("0.1"),
                versions=1,
            ),
            dict(
                id="ipo",
                priority=1,
                kind="NO_LIMIT",
                status=None,
                ipo="INITIAL_LISTING_WINDOW",
                known=datetime(2026, 10, 1, tzinfo=timezone.utc),
                upper_ratio=None,
                lower_ratio=None,
                versions=1,
                raw_evidence="sha256:" + "a" * 64,
                rule="ipo_first_5_trading_days_no_limit",
                duration=5,
            ),
        ],
    )


def state(row, proof=None):
    row = dict(row, qualification_cutoff=datetime(2026, 10, 23, tzinfo=timezone.utc))
    return p._state_row(
        row,
        ordinal=120,
        complete=False,
        prior=Decimal("10"),
        policy=json.loads(Path("config/price-limit.v2.json").read_text()),
        raw_sha="sha256:" + "a" * 64,
        window_proof=proof,
    )


def test_native_bounds_and_true_clock_are_retained():
    row = market_row()
    result = state(row)
    assert result["execution_source_qualified"] is True
    assert result["qualified_upper_price"] == Decimal("11")
    row["upper_price"] = None
    row["lower_price"] = None
    result = state(row)
    assert result["execution_source_qualified"] and row["upper_price"] is None


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"dated_status": []}, "DATED_STATUS_UNAVAILABLE_OR_AMBIGUOUS"),
        (
            {"bar_known": datetime(2026, 10, 24, tzinfo=timezone.utc)},
            "CONTEXT_KNOWLEDGE_AFTER_CUTOFF",
        ),
        ({"upper_price": Decimal("11.5")}, "NATIVE_PRICE_BOUND_DISAGREEMENT"),
        ({"lifecycle": []}, "LISTING_LIFECYCLE_UNKNOWN"),
    ],
)
def test_unknown_does_not_become_normal_or_executable(change, reason):
    row = market_row()
    row.update(change)
    result = state(row)
    assert not result["execution_source_qualified"] and result["reason_code"] == reason
    assert row["is_tradable"] is True


def test_current_rule_requires_six_actual_sessions_and_exact_rule():
    row = market_row()
    row["regimes"] = [x for x in row["regimes"] if x["ipo"] is None]
    proof = {
        "rule_sha256": p.RULES["XSHE"]["sha256"],
        "listing_id": row["listing_id"],
        "market_date": row["market_date"],
        "observed_trading_sessions": 6,
        "rule_known_at": datetime(2026, 10, 22, tzinfo=timezone.utc),
    }
    assert state(row, proof)["execution_source_qualified"]
    proof["observed_trading_sessions"] = 5
    assert state(row, proof)["reason_code"] == "CURRENT_RULE_WINDOW_UNQUALIFIED"


def test_calendar_counts_use_visible_revision_once_and_reject_live_conflict():
    import duckdb

    with duckdb.connect(":memory:") as c:
        c.execute("""CREATE TABLE full_calendar AS SELECT 'v' venue_id,
            'session' calendar_session_id, DATE '2026-09-01' trade_date, true is_open,
            TIMESTAMPTZ '2026-09-02 00:00:00+00' knowledge_from,
            TIMESTAMPTZ '2026-10-09 00:00:00+00' knowledge_to UNION ALL SELECT
            'v','session', DATE '2026-09-01',true,
            TIMESTAMPTZ '2026-10-09 00:00:00+00',NULL""")
        c.execute("""CREATE TABLE listings AS SELECT 'l' listing_id,'v' venue_id,
            TIMESTAMPTZ '2020-01-01 00:00:00+00' effective_from""")
        result = p.prepare_calendar_counts(c, datetime(2026, 10, 20, tzinfo=timezone.utc))
        assert result == {"raw_rows": 2, "visible_rows": 1}
        assert c.execute("SELECT ordinal FROM calendar_rank").fetchall() == [(1,)]
        c.execute(
            "DROP TABLE qualified_calendar; DROP TABLE calendar_rank; DROP TABLE listing_floor"
        )
        c.execute("UPDATE full_calendar SET knowledge_to=NULL")
        with pytest.raises(p.PurposeError, match="VISIBLE_VERSION_AMBIGUOUS"):
            p.prepare_calendar_counts(c, datetime(2026, 10, 20, tzinfo=timezone.utc))

"""Feature adapter tests: canonical in-memory rows -> original panel SQL.

The adapter registers the rows as the same source_* relations the production pipeline
builds and runs the ORIGINAL _feature_panel_query, so the oracle IS the production
query run over an identical second in-memory copy.  Both must produce the same
BaselineFeatureRow fields; a divergence is a defect, not a tolerated approximation.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from quanthus.product_daily.contracts import ProductDailyError
from quanthus.product_daily.recommendation.feature_adapter import (
    CorporateAction,
    DailyBar,
    ExceptionSpan,
    IdentityFacts,
    LimitFacts,
    compute_security_feature_facts,
    daily_input_from_bar,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
CUTOFF = datetime(2026, 11, 24, 22, 30, tzinfo=SHANGHAI)
SIGNAL = date(2026, 11, 24)


def _days(n, end=SIGNAL):
    out = []
    cur = end
    while len(out) < n:
        if cur.weekday() < 5:
            out.append(cur)
        cur -= timedelta(days=1)
    return tuple(reversed(out))


def _bar(day: date, close: Decimal, volume=Decimal("1000")) -> DailyBar:
    return DailyBar(
        security_id="sec-1",
        session_date=day,
        open_price=close,
        high_price=close * Decimal("1.01"),
        low_price=close * Decimal("0.99"),
        close_price=close,
        volume=volume,
        amount=close * volume,  # CNY amount
    )


def _identity() -> IdentityFacts:
    return IdentityFacts(
        security_id="sec-1",
        stock_code="sec-1",
        listing_code="600000",
        security_name="name-600000",
        industry_id="industry-01",
        venue_id="SSE",
        instrument_type="common_equity",
        listing_id="listing-1",
        company_id="company-1",
        depends_on_member_ids=("core-daily-1d",),
        invalidations=("cancel if open above price_bound",),
        price_bound=Decimal("101.00"),
        source_evidence_digest="sha256:" + "a" * 64,
    )


def _limit(day: date, close: Decimal) -> LimitFacts:
    return LimitFacts(
        session_date=day,
        entry_policy_allows=True,
        is_executable=True,
        upper_limit_pct=Decimal("10"),
        lower_limit_pct=Decimal("10"),
        upper_limit_price=close * Decimal("1.10"),
        lower_limit_price=close * Decimal("0.90"),
    )


def _independent_oracle(
    days,
    bars,
    actions=(),
    exceptions=(),
    limits=(),
    security_id="sec-1",
):
    """Run the ORIGINAL _feature_panel_query over a second in-memory DuckDB copy.

    This is the production ground truth built from the same canonical rows; it shares
    no code with the adapter beyond the query generator itself, so a mismatch is a
    real regression, not a self-agreement.
    """

    import duckdb

    from quanthus.backtest.baseline_research import _feature_panel_query

    conn = duckdb.connect(":memory:")
    conn.execute("CREATE TABLE source_daily_universe(security_id VARCHAR, market_date DATE)")
    if days:
        conn.executemany(
            "INSERT INTO source_daily_universe VALUES (?,?)",
            [(security_id, d) for d in days],
        )
    conn.execute(
        "CREATE TABLE source_daily_ohlcv_none(security_id VARCHAR, date DATE, "
        "open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE, amount DOUBLE)"
    )
    if bars:
        conn.executemany(
            "INSERT INTO source_daily_ohlcv_none VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    b.security_id,
                    b.session_date,
                    float(b.open_price),
                    float(b.high_price),
                    float(b.low_price),
                    float(b.close_price),
                    float(b.volume),
                    float(b.amount),
                )
                for b in bars
            ],
        )
    conn.execute(
        "CREATE TABLE source_price_limit_execution_daily(security_id VARCHAR, "
        "execution_date DATE, entry_policy_allows BOOLEAN, is_executable BOOLEAN, "
        "upper_limit_pct DOUBLE, lower_limit_pct DOUBLE, "
        "upper_limit_price DOUBLE, lower_limit_price DOUBLE)"
    )
    if limits:
        conn.executemany(
            "INSERT INTO source_price_limit_execution_daily VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    security_id,
                    lm.session_date,
                    lm.entry_policy_allows,
                    lm.is_executable,
                    None if lm.upper_limit_pct is None else float(lm.upper_limit_pct),
                    None if lm.lower_limit_pct is None else float(lm.lower_limit_pct),
                    None if lm.upper_limit_price is None else float(lm.upper_limit_price),
                    None if lm.lower_limit_price is None else float(lm.lower_limit_price),
                )
                for lm in limits
            ],
        )
    conn.execute(
        "CREATE TABLE source_corporate_actions_normalized_mapped(security_code VARCHAR, "
        "effective_date DATE, known_at DATE, cash_dividend_per_share DOUBLE, "
        "bonus_share_ratio DOUBLE, security_id VARCHAR)"
    )
    if actions:
        conn.executemany(
            "INSERT INTO source_corporate_actions_normalized_mapped VALUES (?,?,?,?,?,?)",
            [
                (
                    a.security_code,
                    a.effective_date,
                    a.known_at,
                    float(a.cash_dividend_per_share),
                    float(a.bonus_share_ratio),
                    a.security_code,  # security_id resolved via identity map (same code here)
                )
                for a in actions
            ],
        )
    conn.execute(
        "CREATE TABLE source_corporate_action_exceptions_mapped(security_code VARCHAR, "
        "blocked_from DATE, blocked_through DATE, security_id VARCHAR)"
    )
    if exceptions:
        conn.executemany(
            "INSERT INTO source_corporate_action_exceptions_mapped VALUES (?,?,?,?)",
            [
                (e.security_code, e.blocked_from, e.blocked_through, e.security_code)
                for e in exceptions
            ],
        )
    sql = _feature_panel_query(days[0], days[-1], output_order="date-security")
    cur = conn.execute(sql)
    cols = [c[0] for c in cur.description]
    result = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    conn.close()
    return result


def _close_enough(a, b, tol=1e-9):
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= tol


def test_feature_facts_match_independent_panel_oracle():
    """Every BaselineFeatureRow field equals the original SQL on the same input."""

    days = _days(80)
    closes = [Decimal("95") + Decimal(i) * Decimal("0.42") for i in range(80)]
    volumes = [Decimal("900") + Decimal(i * 7 % 300) for i in range(80)]
    bars = [_bar(d, c, v) for d, c, v in zip(days, closes, volumes, strict=False)]
    limits = [_limit(d, c) for d, c in zip(days, closes, strict=False)]

    facts = compute_security_feature_facts(
        security_id="sec-1",
        bars=bars,
        actions=(),
        exceptions=(),
        limits=limits,
        trading_dates=days,
        identity=_identity(),
        signal_date=SIGNAL,
        signal_close=datetime.combine(SIGNAL, time(15, 0), SHANGHAI),
    )

    oracle = _independent_oracle(days, bars, limits=limits)
    oracle_row = [r for r in oracle if r["market_date"] == SIGNAL][0]

    assert _close_enough(facts.mark_price, oracle_row["close_price"])
    assert _close_enough(facts.total_return_index, oracle_row["total_return_index"])
    assert _close_enough(facts.sma20, oracle_row["sma20"])
    assert _close_enough(facts.sma60, oracle_row["sma60"])
    assert _close_enough(facts.momentum20, oracle_row["momentum20"])
    assert _close_enough(facts.prior_high20, oracle_row["prior_high20"])
    assert _close_enough(facts.rsi14, oracle_row["rsi14"])
    assert _close_enough(facts.realized_volatility20, oracle_row["realized_volatility20"])
    assert _close_enough(facts.volume_ratio20, oracle_row["volume_ratio20"])
    assert _close_enough(facts.average_amount20, oracle_row["average_amount20"])
    assert _close_enough(facts.daily_total_return, oracle_row["daily_total_return"])
    assert facts.listing_sessions == int(oracle_row["listing_sessions"]) == 80
    assert facts.feature_ready == oracle_row["feature_ready"]
    assert facts.entry_eligible == oracle_row["entry_eligible"]
    assert facts.exception_active == oracle_row["exception_active"]
    assert facts.unexplained_price_transition == oracle_row["unexplained_price_transition"]
    assert facts.scope_continuity_excluded == oracle_row["scope_continuity_excluded"]
    assert facts.security_name == "name-600000"
    assert facts.industry_id == "industry-01"
    assert facts.price_bound == Decimal("101.00")


def test_dividend_and_exception_match_oracle():
    """A known dividend + an exception span produce identical flags/returns.

    The action must be applied (known_at < effective_date); the late-known action is
    filtered by the panel's own mapped_actions gate, and the exception span flips the
    same flags in both implementations.
    """

    days = _days(70)
    closes = [Decimal("100") + Decimal(i) * Decimal("0.3") for i in range(70)]
    bars = [_bar(d, c) for d, c in zip(days, closes, strict=False)]
    limits = [_limit(d, c) for d, c in zip(days, closes, strict=False)]
    actions = (
        CorporateAction(
            security_code="sec-1",
            effective_date=days[55],
            known_at=days[50],  # known before it applies -> used
            cash_dividend_per_share=Decimal("0.5"),
            bonus_share_ratio=Decimal("0"),
        ),
    )
    exceptions = (
        ExceptionSpan(security_code="sec-1", blocked_from=days[-2], blocked_through=days[-1]),
    )

    facts = compute_security_feature_facts(
        security_id="sec-1",
        bars=bars,
        actions=actions,
        exceptions=exceptions,
        limits=limits,
        trading_dates=days,
        identity=_identity(),
        signal_date=SIGNAL,
        signal_close=datetime.combine(SIGNAL, time(15, 0), SHANGHAI),
    )
    oracle = _independent_oracle(days, bars, actions=actions, exceptions=exceptions, limits=limits)
    oracle_row = [r for r in oracle if r["market_date"] == SIGNAL][0]

    assert _close_enough(facts.daily_total_return, oracle_row["daily_total_return"])
    assert facts.exception_active == oracle_row["exception_active"] is True
    assert facts.entry_eligible == oracle_row["entry_eligible"] is False
    assert facts.feature_ready == oracle_row["feature_ready"] is False
    assert facts.scope_continuity_excluded == oracle_row["scope_continuity_excluded"]


def test_late_known_action_fails_closed_at_source_audit():
    """known_at >= effective_date is invalid input; the shared audit rejects it.

    The production source audit treats a not-yet-known action as invalid input
    (fail closed), matching the PIT rule that a corporate action may only apply when
    it was knowable before its effective date.  The adapter surfaces that same
    rejection rather than silently dropping or applying the row.
    """

    days = _days(70)
    closes = [Decimal("100") + Decimal(i) * Decimal("0.3") for i in range(70)]
    bars = [_bar(d, c) for d, c in zip(days, closes, strict=False)]
    limits = [_limit(d, c) for d, c in zip(days, closes, strict=False)]
    late = CorporateAction(
        security_code="sec-1",
        effective_date=days[55],
        known_at=days[55],  # NOT knowable before it applies -> invalid input
        cash_dividend_per_share=Decimal("9.9"),
        bonus_share_ratio=Decimal("0"),
    )

    with pytest.raises(ProductDailyError) as excinfo:
        compute_security_feature_facts(
            security_id="sec-1",
            bars=bars,
            actions=(late,),
            exceptions=(),
            limits=limits,
            trading_dates=days,
            identity=_identity(),
            signal_date=SIGNAL,
            signal_close=datetime.combine(SIGNAL, time(15, 0), SHANGHAI),
        )
    assert "invalid" in str(excinfo.value).lower()


def test_missing_limit_member_keeps_entry_not_eligible():
    """No LimitFacts -> the limit member is unbound -> entry_eligible is False."""

    days = _days(70)
    closes = [Decimal("100") + Decimal(i) * Decimal("0.3") for i in range(70)]
    bars = [_bar(d, c) for d, c in zip(days, closes, strict=False)]
    facts = compute_security_feature_facts(
        security_id="sec-1",
        bars=bars,
        actions=(),
        exceptions=(),
        limits=(),
        trading_dates=days,
        identity=_identity(),
        signal_date=SIGNAL,
        signal_close=datetime.combine(SIGNAL, time(15, 0), SHANGHAI),
    )
    oracle = _independent_oracle(days, bars, limits=())
    oracle_row = [r for r in oracle if r["market_date"] == SIGNAL][0]
    assert facts.entry_eligible == oracle_row["entry_eligible"] is False
    assert facts.feature_ready == oracle_row["feature_ready"] is True


def test_missing_signal_membership_fails_closed():
    days = _days(30)
    bars = [_bar(d, Decimal("100")) for d in days]
    with pytest.raises(ProductDailyError):
        compute_security_feature_facts(
            security_id="sec-1",
            bars=bars,
            actions=(),
            exceptions=(),
            limits=(),
            trading_dates=days[:-1],  # membership does not reach the signal date
            identity=_identity(),
            signal_date=SIGNAL,
            signal_close=datetime.combine(SIGNAL, time(15, 0), SHANGHAI),
        )


def test_no_bars_fails_closed():
    with pytest.raises(ProductDailyError):
        compute_security_feature_facts(
            security_id="sec-1",
            bars=(),
            actions=(),
            exceptions=(),
            limits=(),
            trading_dates=(SIGNAL,),
            identity=_identity(),
            signal_date=SIGNAL,
            signal_close=datetime.combine(SIGNAL, time(15, 0), SHANGHAI),
        )


def test_short_history_feature_not_ready_never_shrunk():
    """30 sessions < the readiness floor -> feature_ready False, not a shrunk window."""

    days = _days(30)
    closes = [Decimal("100") + Decimal(i) * Decimal("0.5") for i in range(30)]
    bars = [_bar(d, c) for d, c in zip(days, closes, strict=False)]
    facts = compute_security_feature_facts(
        security_id="sec-1",
        bars=bars,
        actions=(),
        exceptions=(),
        limits=[_limit(d, c) for d, c in zip(days, closes, strict=False)],
        trading_dates=days,
        identity=_identity(),
        signal_date=SIGNAL,
        signal_close=datetime.combine(SIGNAL, time(15, 0), SHANGHAI),
    )
    oracle = _independent_oracle(
        days, bars, limits=[_limit(d, c) for d, c in zip(days, closes, strict=False)]
    )
    oracle_row = [r for r in oracle if r["market_date"] == SIGNAL][0]
    assert facts.feature_ready == oracle_row["feature_ready"] is False
    assert facts.listing_sessions == int(oracle_row["listing_sessions"]) == 30


def test_daily_input_from_bar_projects_amount_not_volume():
    """turnover carries the CNY amount, never the share volume."""

    day = SIGNAL
    bar = _bar(day, Decimal("100"), volume=Decimal("1234"))
    identity = _identity()
    close_at = datetime.combine(day, time(15, 0), SHANGHAI)
    row = daily_input_from_bar(
        bar=bar,
        identity=identity,
        listing_sessions=200,
        close_at=close_at,
        knowledge_cutoff=CUTOFF,
        source_evidence_digest="sha256:" + "a" * 64,
    )
    assert row.turnover == bar.amount == Decimal("100") * Decimal("1234")
    assert row.turnover != bar.volume
    assert row.listing_code == "600000"
    assert row.static_level1_industry_id == "industry-01"
    assert row.close_price == Decimal("100")
    assert row.knowledge_cutoff == CUTOFF

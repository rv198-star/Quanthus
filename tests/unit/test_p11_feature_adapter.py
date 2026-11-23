"""Synthetic adapter tests: in-memory bars/actions -> real feature values, fail-closed."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from math import exp, log
from zoneinfo import ZoneInfo

import pytest

from quanthus.product_daily.contracts import ProductDailyError
from quanthus.product_daily.recommendation.feature_adapter import (
    CalendarView,
    DailyBar,
    IdentityFacts,
    compute_security_feature_facts,
    daily_input_from_bar,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
CUTOFF = datetime(2026, 11, 24, 22, 30, tzinfo=SHANGHAI)
SIGNAL = date(2026, 11, 24)


def _bar(day: date, close: Decimal, volume=Decimal("1000")) -> DailyBar:
    return DailyBar(
        security_id="sec-1",
        session_date=day,
        open_price=close,
        high_price=close * Decimal("1.01"),
        low_price=close * Decimal("0.99"),
        close_price=close,
        volume=volume,
        amount=close * volume,
    )


def _identity() -> IdentityFacts:
    return IdentityFacts(
        security_id="sec-1",
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


def _calendar(days) -> CalendarView:
    return CalendarView(trading_dates=tuple(days), listing_sessions=len(days))


def _days(n, end=SIGNAL):
    from datetime import timedelta

    out = []
    cur = end
    while len(out) < n:
        if cur.weekday() < 5:
            out.append(cur)
        cur -= timedelta(days=1)
    return tuple(reversed(out))


def test_feature_values_match_panel_formula():
    """Computed rsi14/vol20/total_return/sma/momentum equal the reference formula exactly."""

    days = _days(60)
    closes = [Decimal("100") + Decimal(i) * Decimal("0.5") for i in range(60)]
    bars = [_bar(d, c) for d, c in zip(days, closes, strict=False)]
    identity = _identity()
    cal = _calendar(days)
    facts = compute_security_feature_facts(
        security_id="sec-1",
        bars=bars,
        actions=(),
        exceptions=(),
        calendar=cal,
        identity=identity,
        signal_date=SIGNAL,
        signal_close=datetime.combine(SIGNAL, time(15, 0), SHANGHAI),
    )
    # Reference formula: total_return_index = 100*exp(sum(log returns))
    # returns for bars[1..59] (first bar has no prior close -> None, treated as 0)
    returns = [log(float(closes[i] / closes[i - 1])) for i in range(1, 60)]
    tri = 100.0 * exp(sum(returns))
    assert abs(float(facts.total_return_index) - tri) < 1e-6
    # index per row: 100*exp(cumulative log returns); row0=100 (no prior)
    tris = [100.0]
    cum = 0.0
    for r in returns:
        cum += r
        tris.append(100.0 * exp(cum))
    assert len(tris) == 60
    assert abs(float(facts.sma60) - sum(tris) / 60) < 1e-6
    # rsi14 = 100*avg_gain/(avg_gain+avg_loss) over last 14 daily_total_return
    last14 = returns[-14:]
    gains = [r for r in last14 if r > 0]
    losses = [-r for r in last14 if r < 0]
    ag = sum(gains) / 14
    al = sum(losses) / 14
    assert abs(float(facts.rsi14) - 100 * ag / (ag + al)) < 1e-6
    assert facts.security_name == "name-600000"
    assert facts.industry_id == "industry-01"
    assert facts.mark_price == closes[-1]
    assert facts.price_bound == Decimal("101.00")


def test_missing_signal_bar_fails_closed():
    days = _days(60)
    bars = [_bar(d, Decimal("100")) for d in days[:-1]]  # no signal-date bar
    with pytest.raises(ProductDailyError):
        compute_security_feature_facts(
            security_id="sec-1",
            bars=bars,
            actions=(),
            exceptions=(),
            calendar=_calendar(days),
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
            calendar=_calendar(()),
            identity=_identity(),
            signal_date=SIGNAL,
            signal_close=datetime.combine(SIGNAL, time(15, 0), SHANGHAI),
        )


def test_short_history_yields_none_not_default():
    """A short history yields None for the windowed features, never a shrunk window."""

    days = _days(30)
    bars = [_bar(d, Decimal("100")) for d in days]
    facts = compute_security_feature_facts(
        security_id="sec-1",
        bars=bars,
        actions=(),
        exceptions=(),
        calendar=_calendar(days),
        identity=_identity(),
        signal_date=SIGNAL,
        signal_close=datetime.combine(SIGNAL, time(15, 0), SHANGHAI),
    )
    # 30 sessions < 60 -> sma60/vol20/momentum unavailable, not defaulted
    assert facts.sma60 is None
    assert facts.momentum20 is not None  # 20-day momentum computable on 30 sessions
    assert facts.rsi14 is not None


def test_daily_input_from_bar_projects_real_fields():
    day = SIGNAL
    bar = _bar(day, Decimal("100"))
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
    assert row.listing_code == "600000"
    assert row.static_level1_industry_id == "industry-01"
    assert row.close_price == Decimal("100")
    assert row.session_date == day
    assert row.close_at == close_at
    assert row.knowledge_cutoff == CUTOFF
